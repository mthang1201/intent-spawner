"""Comprehensive tests for statistical analysis corrections, LLM schema hardening, and Stage C planning."""

import json
from pathlib import Path
import pytest

from evaluation_v4.dataset import load_dataset
from evaluation_v4.plan_system import build_system_plan
from evaluation_v4.recommenders import create_backend, evaluate_item
from evaluation_v4.statistics import (
    calculate_effect_sizes,
    cluster_bootstrap_ci,
    exact_mcnemar,
    holm_adjust,
    paired_difference_cluster_bootstrap_ci,
    wilcoxon_signed_rank,
)
from recommender.external_llm import (
    DEFAULT_PROMPT_VERSION,
    ExternalLLMConfig,
    ExternalLLMRecommender,
    LLMCompletionRequest,
    LLMCompletionResponse,
    LLMOutputValidationError,
    PROMPT_VERSION_V4_0,
    PROMPT_VERSION_V4_1,
)
from recommender.models import RecommendationRequest, SpawnRecommendation


class MockClient:
    def __init__(self, response_content: str):
        self.response_content = response_content
        self.recorded_requests: list[LLMCompletionRequest] = []

    def complete(self, request: LLMCompletionRequest, *, timeout: float) -> str:
        self.recorded_requests.append(request)
        return self.response_content


def test_prompt_v4_1_instruction_content():
    """Verify prompt-v4.1.0 includes explicit 5-field instruction text."""
    client = MockClient(
        json.dumps({
            "profile": "medium",
            "reasons": ["Appropriate for pandas"],
            "image_id": "minimal-python",
            "image_reasons": ["Standard Python"],
        })
    )
    config = ExternalLLMConfig(
        endpoint="https://api.example.com/v1/chat/completions",
        model="test-model",
        prompt_version=PROMPT_VERSION_V4_1,
        max_retries=0,
    )
    recommender = ExternalLLMRecommender(config=config, client=client)
    req = RecommendationRequest(intent="Test intent", dataset_size_gb=1.0)
    recommender.recommend(req)

    assert len(client.recorded_requests) == 1
    system_msg = client.recorded_requests[0].messages[0].content
    assert '"profile": string' in system_msg
    assert '"score": number' in system_msg
    assert '"image_id": string' in system_msg


def test_prompt_v4_0_legacy_content():
    """Verify prompt-v4.0.0 uses legacy concise instruction."""
    client = MockClient(
        json.dumps({
            "profile": "medium",
            "reasons": ["Appropriate for pandas"],
            "score": 90.0,
            "image_id": "minimal-python",
            "image_reasons": ["Standard Python"],
        })
    )
    config = ExternalLLMConfig(
        endpoint="https://api.example.com/v1/chat/completions",
        model="test-model",
        prompt_version=PROMPT_VERSION_V4_0,
        max_retries=0,
    )
    recommender = ExternalLLMRecommender(config=config, client=client)
    req = RecommendationRequest(intent="Test intent", dataset_size_gb=1.0)
    recommender.recommend(req)

    assert len(client.recorded_requests) == 1
    system_msg = client.recorded_requests[0].messages[0].content
    assert "You recommend one JupyterHub resource profile" in system_msg


def test_llm_parser_rejects_response_without_required_score():
    """The repaired prompt/schema still treats an omitted score as invalid."""
    content = json.dumps({
        "profile": "small",
        "reasons": ["Lightweight compute"],
        "image_id": "minimal-python",
        "image_reasons": ["Basic Python"],
    })
    client = MockClient(content)
    config = ExternalLLMConfig(
        endpoint="https://api.example.com/v1/chat/completions",
        model="test-model",
        max_retries=0,
    )
    recommender = ExternalLLMRecommender(config=config, client=client)
    result = recommender.recommend_with_metadata(RecommendationRequest(intent="Quick script"))
    assert result.metadata.fallback_used is True
    assert result.metadata.validation_error == "LLMOutputValidationError"


def test_llm_parser_accepts_valid_response_with_score():
    """Verify that providing score within [0, 100] is parsed properly."""
    content = json.dumps({
        "profile": "large",
        "reasons": ["High memory workload"],
        "score": 95.5,
        "image_id": "scipy-data-science",
        "image_reasons": ["SciPy tools"],
    })
    client = MockClient(content)
    config = ExternalLLMConfig(
        endpoint="https://api.example.com/v1/chat/completions",
        model="test-model",
        max_retries=0,
    )
    recommender = ExternalLLMRecommender(config=config, client=client)
    rec = recommender.recommend(RecommendationRequest(intent="Large matrix computation"))
    assert rec.profile == "large"
    assert rec.score == 95.5
    assert rec.image_id == "scipy-data-science"


def test_llm_parser_rejects_missing_required_fields():
    """Missing 'profile' or 'image_id' must trigger fallback and record error."""
    content = json.dumps({
        "reasons": ["Missing profile"],
        "image_id": "minimal-python",
        "image_reasons": ["Basic Python"],
    })
    client = MockClient(content)
    config = ExternalLLMConfig(
        endpoint="https://api.example.com/v1/chat/completions",
        model="test-model",
        max_retries=0,
    )
    recommender = ExternalLLMRecommender(config=config, client=client)
    result = recommender.recommend_with_metadata(RecommendationRequest(intent="Test"))
    assert result.metadata.fallback_used is True
    assert result.recommendation.profile is not None  # Fallback provided valid profile


def test_llm_parser_rejects_unexpected_fields():
    """Unexpected field must trigger fallback."""
    content = json.dumps({
        "profile": "small",
        "reasons": ["Valid"],
        "image_id": "minimal-python",
        "image_reasons": ["Valid"],
        "hallucinated_field": 12345,
    })
    client = MockClient(content)
    config = ExternalLLMConfig(
        endpoint="https://api.example.com/v1/chat/completions",
        model="test-model",
        max_retries=0,
    )
    recommender = ExternalLLMRecommender(config=config, client=client)
    result = recommender.recommend_with_metadata(RecommendationRequest(intent="Test"))
    assert result.metadata.fallback_used is True


def test_llm_parser_rejects_invalid_score():
    """Negative score or score > 100 must be rejected and trigger fallback."""
    for bad_score in (-5.0, 105.0, "high", True):
        content = json.dumps({
            "profile": "small",
            "reasons": ["Valid"],
            "score": bad_score,
            "image_id": "minimal-python",
            "image_reasons": ["Valid"],
        })
        client = MockClient(content)
        config = ExternalLLMConfig(
            endpoint="https://api.example.com/v1/chat/completions",
            model="test-model",
            max_retries=0,
        )
        recommender = ExternalLLMRecommender(config=config, client=client)
        result = recommender.recommend_with_metadata(RecommendationRequest(intent="Test"))
        assert result.metadata.fallback_used is True


def test_exact_mcnemar_and_wilcoxon():
    """Verify exact McNemar and paired Wilcoxon statistical tests."""
    first = [True] * 40 + [False] * 8
    second = [True] * 30 + [False] * 18
    res_mcnemar = exact_mcnemar(first, second)
    assert res_mcnemar["first_only_correct"] >= 10
    assert 0.0 <= res_mcnemar["p_value_raw"] <= 1.0

    scores_a = [1.0] * 38 + [0.0] * 10
    scores_b = [1.0] * 30 + [0.0] * 18
    res_wilcoxon = wilcoxon_signed_rank(scores_a, scores_b)
    assert res_wilcoxon["pairs"] == 48
    assert res_wilcoxon["w_positive"] > res_wilcoxon["w_negative"]
    assert 0.0 <= res_wilcoxon["p_value_raw"] <= 1.0


def test_effect_sizes_and_cluster_difference_bootstrap():
    """Verify effect size calculations and paired difference cluster bootstrap CI."""
    vec_a = [1.0] * 40 + [0.0] * 8
    vec_b = [1.0] * 30 + [0.0] * 18
    effects = calculate_effect_sizes(vec_a, vec_b)
    assert round(effects["mean_difference"], 4) == round(10 / 48, 4)
    assert effects["cohens_d_paired"] is not None and effects["cohens_d_paired"] > 0
    assert effects["cliffs_delta"] > 0

    rows = [
        {"workload_family": f"fam-{i % 10}", "a": vec_a[i], "b": vec_b[i]}
        for i in range(48)
    ]
    ci_low, ci_high = paired_difference_cluster_bootstrap_ci(
        rows, "a", "b", replicates=500, seed=42
    )
    assert ci_low is not None and ci_high is not None
    assert ci_low <= effects["mean_difference"] <= ci_high


def test_stage_c_plan_generation():
    """Verify non-mutating Stage C system trial plan generation."""
    dataset = load_dataset()
    methods = [
        "static_profile_baseline",
        "rule_based_mapping",
        "external_llm",
        "self_hosted_local_ollama_llm",
    ]
    plan = build_system_plan(dataset, methods=methods, repeats=5, seed=20260808)
    assert len(plan) == 8 * 4 * 5  # 8 workload families * 4 methods * 5 repeats = 160 trials
    plan_methods = {t["recommender"] for t in plan}
    assert plan_methods == set(methods)
    for trial in plan:
        assert "workload_family" in trial
        assert "representative_sample_id" in trial
        assert "system_manifest_path" in trial
        assert "system_workload_id" in trial
        assert "recommender" in trial
        assert "paired_workload_seed" in trial
        assert "cache_condition" in trial
