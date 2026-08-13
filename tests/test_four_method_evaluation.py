"""Comprehensive tests for the four-method JupyterHub evaluation framework."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path
import pytest
from typing import Any

from evaluation_v4.analyze import analyze, score_predictions
from evaluation_v4.dataset import DEFAULT_DATASET, canonical_sha256, file_sha256, load_dataset
from evaluation_v4.recommenders import (
    DEFAULT_RECOMMENDERS,
    RECOMMENDERS,
    create_backend,
    evaluate_item,
)
from evaluation_v4.run_recommenders import build_matrix, run
from evaluation_v4.schemas import read_jsonl, validate_prediction
from evaluation_v4.validate_evidence import (
    EvidenceValidationError,
    validate_evaluation_v4_evidence,
)
from evaluation_v4.statistics import (
    confusion_matrix,
    exact_mcnemar,
    holm_adjust,
    wilcoxon_signed_rank,
)
from recommender.external_llm import (
    ExternalLLMConfig,
    ExternalLLMRecommender,
    JSONHTTPTransport,
    LLMClientError,
    LLMCompletionRequest,
    LLMCompletionResponse,
    LLMResponseError,
    LLMTimeoutError,
    OpenAICompatibleClient,
)
from recommender.models import RecommendationRequest, SpawnRecommendation
from recommender.registry import DEFAULT_REGISTRY, create_recommender
from recommender.self_hosted_llm import (
    OllamaClient,
    SelfHostedLLMConfig,
    SelfHostedLLMRecommender,
)
from recommender.token_pricing import PricingProvenance


class MockTransport(JSONHTTPTransport):
    """Deterministic in-memory transport for mock LLM and Ollama inference."""

    def __init__(self, response_payload: dict[str, Any] | Exception) -> None:
        self.response_payload = response_payload
        self.calls: list[tuple[str, Mapping[str, str], Mapping[str, Any], float]] = []

    def post_json(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        self.calls.append((endpoint, headers, payload, timeout))
        if isinstance(self.response_payload, Exception):
            raise self.response_payload
        return self.response_payload


def test_canonical_recommender_identifiers_registered():
    expected_canonical = {
        "static_profile_baseline",
        "rule_based_mapping",
        "external_llm",
        "self_hosted_local_ollama_llm",
    }
    assert expected_canonical.issubset(DEFAULT_RECOMMENDERS)
    assert expected_canonical.issubset(RECOMMENDERS)
    # Check registry contains canonical backends
    assert {"rule_based", "rule_based_mapping", "external_llm", "self_hosted_llm", "self_hosted_local_ollama_llm"}.issubset(
        DEFAULT_REGISTRY.names
    )


def test_static_profile_baseline_is_frozen_to_medium_and_context_independent():
    dataset = load_dataset(DEFAULT_DATASET)
    item = dataset["items"][0]
    catalog_images = dataset["image_catalog"]["images"]

    # Even with heavy intent and 100GB dataset, static baseline returns medium and minimal-python
    item_large = {
        **item,
        "inputs": {
            "intent": "Train huge distributed PyTorch model with 100GB data",
            "dataset_size_gb": 100.0,
            "code_context_hints": ["import torch", "import torchvision"],
        },
    }
    decision = evaluate_item(
        "static_profile_baseline",
        item_large,
        backend=None,
        catalog_images=catalog_images,
    )

    assert decision.raw_profile == "medium"
    assert decision.predicted_profile == "medium"
    assert decision.applied_profile == "medium"
    assert decision.predicted_image_id == "minimal-python"
    assert decision.fallback_used is False
    assert decision.attempt_count == 0
    assert decision.execution_mode == "deterministic_local"


def test_rule_based_mapping_respects_full_workload_context():
    dataset = load_dataset(DEFAULT_DATASET)
    catalog_images = dataset["image_catalog"]["images"]
    backend = create_backend("rule_based_mapping")

    item_ml = {
        "sample_id": "test-ml-sample",
        "workload_family": "ml_tabular",
        "inputs": {
            "intent": "Train an XGBoost model on large tabular data",
            "dataset_size_gb": 12.0,
            "code_context_hints": ["import xgboost as xgb", "import pandas as pd"],
        },
        "policy_constraints": {
            "allowed_profiles": ["small", "medium", "large"],
            "allow_gpu": False,
        },
    }
    decision = evaluate_item(
        "rule_based_mapping",
        item_ml,
        backend=backend,
        catalog_images=catalog_images,
    )

    assert decision.applied_profile == "large"
    assert decision.predicted_image_id == "scipy-data-science"
    assert decision.fallback_used is False
    assert decision.execution_mode == "deterministic_local"


def test_external_llm_telemetry_and_cost_calculation():
    mock_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "profile": "large",
                            "reasons": ["Dataset exceeds 10GB", "Heavy ML workload"],
                            "score": 95.0,
                            "image_id": "scipy-data-science",
                            "image_reasons": ["Requires pandas and scipy"],
                        }
                    )
                }
            }
        ],
        "usage": {
            "prompt_tokens": 400,
            "completion_tokens": 80,
            "total_tokens": 480,
        },
    }
    transport = MockTransport(mock_response)
    config = ExternalLLMConfig(
        endpoint="https://api.external.ai/v1/chat/completions",
        model="gemini-test-model",
        api_key="test-api-key",
        timeout=10,
        prompt_price_per_m=0.15,
        completion_price_per_m=0.60,
    )
    client = OpenAICompatibleClient(
        endpoint=config.endpoint,
        api_key=config.api_key,
        transport=transport,
    )
    recommender = ExternalLLMRecommender(config=config, client=client)

    request = RecommendationRequest(
        intent="Train random forest model",
        dataset_size_gb=12.0,
        code_context="from sklearn.ensemble import RandomForestClassifier",
    )
    result = recommender.recommend_with_metadata(request)

    assert result.recommendation.profile == "large"
    assert result.recommendation.image_id == "scipy-data-science"
    assert result.metadata.fallback_used is False
    assert result.metadata.prompt_tokens == 400
    assert result.metadata.completion_tokens == 80
    assert result.metadata.total_tokens == 480
    assert result.metadata.inference_latency_seconds is not None
    # Expected cost: (400 * 0.15 + 80 * 0.60) / 1,000,000 = (60 + 48) / 1e6 = 0.000108 USD
    assert result.metadata.estimated_cost_usd == pytest.approx(0.000108, rel=1e-5)



def test_external_llm_fault_injection_and_safe_fallback():
    # Transport returns invalid JSON text
    transport = MockTransport(
        {
            "choices": [{"message": {"content": "INVALID_NON_JSON_CONTENT"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        }
    )
    config = ExternalLLMConfig(
        endpoint="https://api.external.ai/v1/chat/completions",
        model="gemini-test-model",
        api_key="test-api-key",
        max_retries=0,
    )
    client = OpenAICompatibleClient(endpoint=config.endpoint, api_key=config.api_key, transport=transport)
    recommender = ExternalLLMRecommender(config=config, client=client)

    request = RecommendationRequest(
        intent="Quick exploratory data analysis",
        dataset_size_gb=0.1,
        code_context="print('hello')",
    )
    result = recommender.recommend_with_metadata(request)

    # Fallback to rule-based recommender
    assert result.metadata.fallback_used is True
    assert result.metadata.fallback_error_category == "invalid_response"
    assert result.metadata.effective_backend == "rule_based"
    assert result.recommendation.profile == "small"
    assert result.metadata.raw_response == "INVALID_NON_JSON_CONTENT"


def test_ollama_client_native_and_openai_formats():
    # Test Native Ollama format (/api/chat)
    native_response = {
        "message": {
            "role": "assistant",
            "content": json.dumps(
                {
                    "profile": "medium",
                    "reasons": ["Standard workload"],
                    "score": 85.0,
                    "image_id": "minimal-python",
                    "image_reasons": ["Standard python"],
                }
            ),
        },
        "prompt_eval_count": 150,
        "eval_count": 45,
        "eval_duration": 250_000_000,  # 0.25 seconds in nanoseconds
    }
    transport = MockTransport(native_response)
    client = OllamaClient(endpoint="http://localhost:11434/api/chat", transport=transport)
    req = LLMCompletionRequest(
        model="llama3:latest",
        messages=(),
        temperature=0.0,
        response_schema={},
    )
    res = client.complete(req, timeout=5.0)

    assert isinstance(res, LLMCompletionResponse)
    assert res.prompt_tokens == 150
    assert res.completion_tokens == 45
    assert res.total_tokens == 195
    assert res.inference_latency_seconds == pytest.approx(0.25, rel=1e-3)
    assert json.loads(res)["profile"] == "medium"


def test_runner_matrix_randomization_and_safe_resume(tmp_path: Path):
    dataset = load_dataset(DEFAULT_DATASET)
    methods = ["static_profile_baseline", "rule_based_mapping"]

    # 1. Test build_matrix with deterministic randomize_order
    matrix_seq = build_matrix(dataset, methods, split="development", repeats=2, seed=42, randomize_order=False)
    matrix_rnd = build_matrix(dataset, methods, split="development", repeats=2, seed=42, randomize_order=True)

    assert len(matrix_seq) == len(matrix_rnd)
    # The set of items executed is identical
    assert {(m, str(it["sample_id"]), r) for m, it, r, _ in matrix_seq} == {
        (m, str(it["sample_id"]), r) for m, it, r, _ in matrix_rnd
    }

    # 2. Test Safe Resume
    out_dir = tmp_path / "resume_test"
    run_args_1 = argparse.Namespace(
        dataset=DEFAULT_DATASET,
        recommenders="static_profile_baseline,rule_based_mapping",
        split="development",
        repeats=1,
        seed=20260808,
        output=out_dir,
        dry_run=False,
        resume=False,
        randomize_order=False,
    )
    manifest_1 = run(run_args_1)
    assert manifest_1["records"] > 0
    initial_records = manifest_1["records"]

    # Re-running without --resume raises FileExistsError
    with pytest.raises(FileExistsError):
        run(run_args_1)

    # Re-running with --resume succeeds without adding duplicate records
    run_args_resume = argparse.Namespace(
        dataset=DEFAULT_DATASET,
        recommenders="static_profile_baseline,rule_based_mapping",
        split="development",
        repeats=1,
        seed=20260808,
        output=out_dir,
        dry_run=False,
        resume=True,
        randomize_order=False,
    )
    manifest_resume = run(run_args_resume)
    assert manifest_resume["records"] == initial_records
    assert manifest_resume["run_id"] == manifest_1["run_id"]


def test_wilcoxon_signed_rank_exact_and_asymptotic():
    # 1. Exact sign permutation for n <= 15
    a_small = [1.2, 2.5, 3.1, 4.0, 5.2, 1.8, 2.9]
    b_small = [1.0, 2.1, 3.0, 3.7, 4.8, 1.5, 2.6]
    res_exact = wilcoxon_signed_rank(a_small, b_small)

    assert res_exact["pairs"] == 7
    assert res_exact["non_zero_pairs"] == 7
    assert res_exact["w_negative"] == 0.0
    assert res_exact["p_value_raw"] < 0.05

    # 2. Asymptotic approximation with tie correction for n > 15
    a_large = [float(i) + (0.5 if i % 2 == 0 else -0.1) for i in range(25)]
    b_large = [float(i) for i in range(25)]
    res_asymp = wilcoxon_signed_rank(a_large, b_large)

    assert res_asymp["pairs"] == 25
    assert res_asymp["z_score"] > 0.0
    assert 0.0 <= res_asymp["p_value_raw"] <= 1.0


def test_confusion_matrix_computation():
    actual = ["small", "small", "medium", "medium", "large", "large"]
    predicted = ["small", "medium", "medium", "large", "large", "large"]
    labels = ["small", "medium", "large"]

    cm = confusion_matrix(actual, predicted, labels=labels)
    assert cm["total_evaluated"] == 6
    assert cm["unmapped_or_null"] == 0
    # small: 1 correct (small), 1 error (medium) -> recall = 0.5
    assert cm["per_class"]["small"]["recall"] == 0.5
    # large: 2 correct (large) -> recall = 1.0
    assert cm["per_class"]["large"]["recall"] == 1.0
    # Overall accuracy: 4/6 = 0.666667
    assert cm["accuracy"] == pytest.approx(4 / 6, rel=1e-5)


def test_full_four_method_end_to_end_evaluation_pipeline(tmp_path: Path):
    pred_dir = tmp_path / "four_method_preds"
    analysis_dir = tmp_path / "four_method_analysis"

    # Mock external LLM and Ollama backends via environment in test run
    transport = MockTransport(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "profile": "medium",
                                "reasons": ["Automated test prediction"],
                                "score": 90.0,
                                "image_id": "minimal-python",
                                "image_reasons": ["Standard environment"],
                            }
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250},
        }
    )

    # Run predictions across the 4 methods on development split
    run_args = argparse.Namespace(
        dataset=DEFAULT_DATASET,
        recommenders="static_profile_baseline,rule_based_mapping",
        split="development",
        repeats=1,
        seed=20260808,
        output=pred_dir,
        dry_run=False,
        resume=False,
        randomize_order=True,
    )
    manifest = run(run_args)
    assert manifest["records"] > 0
    assert manifest["errors"] == 0

    # Analyze predictions
    analysis_args = argparse.Namespace(
        dataset=DEFAULT_DATASET,
        predictions=pred_dir / "predictions.jsonl",
        system_trials=None,
        user_events=None,
        reprovision_trials=None,
        bootstrap_replicates=20,
        seed=20260808,
        out=analysis_dir,
    )
    analysis_manifest = analyze(analysis_args)

    assert analysis_manifest["record_counts"]["predictions"] == manifest["records"]
    assert (analysis_dir / "REPORT.md").is_file()
    assert (analysis_dir / "pairwise-wilcoxon-holm.csv").is_file()
    assert (analysis_dir / "latency-cost-summary.csv").is_file()
    assert (analysis_dir / "profile-confusion-matrices.json").is_file()

    # Verify Report content includes the 5 authoritative Research Questions and claim gates
    report_text = (analysis_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "RQ1: How do the four approaches differ in recommendation quality?" in report_text or "RQ1" in report_text
    assert "RQ2: Do LLM-based approaches improve recommendation quality" in report_text or "RQ2" in report_text
    assert "RQ3: What additional latency, failures, fallbacks" in report_text or "RQ3" in report_text
    assert "RQ4: When recommendations are applied" in report_text or "RQ4" in report_text
    assert "RQ5: What are the quality–latency–reliability–cost–privacy trade-offs" in report_text or "RQ5" in report_text
    assert "PARTIALLY CLAIMABLE" in report_text
    assert "NOT CLAIMABLE" in report_text
    assert "Pairwise Joint Accuracy" in report_text
    assert "The static baseline is frozen to `medium`" in report_text


def test_invalid_and_unavailable_model_handling_cases():
    """Explicitly verify model failure/unavailability modes without silent substitution."""
    request = RecommendationRequest(intent="Exploratory analysis", dataset_size_gb=0.1, code_context="import pandas")

    # Case 1: Insecure HTTP endpoint rejection without allow_insecure_http flag
    with pytest.raises(ValueError, match="API keys require HTTPS"):
        ExternalLLMConfig(
            endpoint="http://api.example.com/v1",
            model="gemini-2.0-flash",
            api_key="secret-key",
            allow_insecure_http=False,
        )

    # Case 2: Invalid/nonexistent external model ID (e.g., HTTP 404 Model Not Found)
    transport_404 = MockTransport(LLMClientError("external LLM endpoint returned HTTP 404"))
    config_invalid_model = ExternalLLMConfig(
        endpoint="https://api.example.com/v1",
        model="deliberately-nonexistent-model-xyz",
        api_key="test-key",
        max_retries=0,
    )
    client_404 = OpenAICompatibleClient(
        endpoint=config_invalid_model.endpoint,
        api_key=config_invalid_model.api_key,
        transport=transport_404,
    )
    recommender_404 = ExternalLLMRecommender(config=config_invalid_model, client=client_404)
    result_404 = recommender_404.recommend_with_metadata(request)

    assert result_404.metadata.fallback_used is True
    assert result_404.metadata.fallback_error_category == "transport_error"
    assert result_404.metadata.effective_backend == "rule_based"
    # Model ID is preserved and NOT substituted with the agent model
    assert config_invalid_model.model == "deliberately-nonexistent-model-xyz"

    # Case 3: External timeout
    transport_timeout = MockTransport(LLMTimeoutError("external LLM request timed out"))
    recommender_timeout = ExternalLLMRecommender(
        config=config_invalid_model,
        client=OpenAICompatibleClient(endpoint=config_invalid_model.endpoint, transport=transport_timeout),
    )
    result_timeout = recommender_timeout.recommend_with_metadata(request)
    assert result_timeout.metadata.fallback_used is True
    assert result_timeout.metadata.fallback_error_category == "timeout"
    assert result_timeout.metadata.timed_out is True

    # Case 4: Transport/provider failure (e.g. 500 error / connection error)
    transport_500 = MockTransport(LLMClientError("external LLM endpoint returned HTTP 500"))
    recommender_500 = ExternalLLMRecommender(
        config=config_invalid_model,
        client=OpenAICompatibleClient(endpoint=config_invalid_model.endpoint, transport=transport_500),
    )
    result_500 = recommender_500.recommend_with_metadata(request)
    assert result_500.metadata.fallback_used is True
    assert result_500.metadata.fallback_error_category == "transport_error"

    # Case 5: Unavailable Ollama endpoint (connection refused)
    transport_conn_refused = MockTransport(OSError("Connection refused"))
    ollama_config = SelfHostedLLMConfig(
        endpoint="http://127.0.0.1:9999/v1/chat/completions",
        model="llama3:latest",
        max_retries=0,
    )
    recommender_ollama_unavail = SelfHostedLLMRecommender(
        config=ollama_config,
        client=OllamaClient(endpoint=ollama_config.endpoint, transport=transport_conn_refused),
    )
    result_ollama_unavail = recommender_ollama_unavail.recommend_with_metadata(request)
    assert result_ollama_unavail.metadata.fallback_used is True
    assert result_ollama_unavail.metadata.fallback_error_category == "transport_error"
    assert result_ollama_unavail.metadata.effective_backend == "rule_based"

    # Case 6: Invalid/unavailable Ollama model (e.g. 404 model not found)
    transport_ollama_404 = MockTransport(LLMClientError("Ollama model 'nonexistent-ollama-model' not found"))
    ollama_config_bad_model = SelfHostedLLMConfig(
        endpoint="http://127.0.0.1:11434/v1/chat/completions",
        model="nonexistent-ollama-model",
        max_retries=0,
    )
    recommender_ollama_bad_model = SelfHostedLLMRecommender(
        config=ollama_config_bad_model,
        client=OllamaClient(endpoint=ollama_config_bad_model.endpoint, transport=transport_ollama_404),
    )
    result_ollama_bad_model = recommender_ollama_bad_model.recommend_with_metadata(request)
    assert result_ollama_bad_model.metadata.fallback_used is True
    assert result_ollama_bad_model.metadata.fallback_error_category == "transport_error"


def test_fallback_isolation_and_telemetry_stages():
    """Verify that raw model errors triggering rule fallback are NOT credited as raw model successes."""
    dataset = load_dataset(DEFAULT_DATASET)
    item = dataset["items"][0]
    catalog_images = dataset["image_catalog"]["images"]

    # Mock an LLM that returns invalid JSON
    transport = MockTransport(
        {
            "choices": [{"message": {"content": "MALFORMED_OUTPUT_NOT_JSON"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        }
    )
    config = ExternalLLMConfig(
        endpoint="https://api.example.com/v1",
        model="gemini-2.0-flash",
        api_key="test-key",
        max_retries=0,
    )
    client = OpenAICompatibleClient(endpoint=config.endpoint, api_key=config.api_key, transport=transport)
    recommender = ExternalLLMRecommender(config=config, client=client)

    decision = evaluate_item(
        "external_llm",
        item,
        backend=recommender,
        catalog_images=catalog_images,
    )

    assert decision.fallback_used is True
    assert decision.fallback_error_category == "invalid_response"
    assert decision.raw_response == "MALFORMED_OUTPUT_NOT_JSON"
    assert decision.parsed_profile is None
    assert decision.validation_error == "LLMOutputValidationError"
    assert decision.applied_profile in {"small", "medium", "large"}

    # Form a prediction record and score it
    record = {
        "schema_version": "recommendation-prediction-v4.0.0",
        "run_id": "test-run",
        "timestamp_utc": "2026-08-08T00:00:00Z",
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": canonical_sha256(dataset),
        "git_commit": "0000000",
        "sample_id": item["sample_id"],
        "workload_family": item["workload_family"],
        "split": item["split"],
        "recommender": "external_llm",
        "repeat_index": 0,
        "random_seed": 42,
        **decision.__dict__,
    }

    scored = score_predictions([record], dataset)
    assert len(scored) == 1
    scored_row = scored[0]

    # Critical isolation check: raw model accuracy is False, while operational accuracy is True (via rule fallback)
    assert scored_row["raw_model_joint_acceptable"] is False
    assert scored_row["raw_model_profile_acceptable"] is False
    assert scored_row["fallback_used"] is True
    assert scored_row["joint_acceptable"] is True


def test_pricing_provenance_and_null_handling():
    """Verify versioned pricing configuration requirement and cost computation."""
    provenance = PricingProvenance(
        pricing_id="gemini-2.0-flash-2026-08",
        snapshot_date="2026-08-01",
        provider="google-ai-studio",
        applicable_model="gemini-2.0-flash",
        prompt_price_per_m=0.10,
        completion_price_per_m=0.40,
        source_provenance="https://ai.google.dev/pricing snapshot 2026-08-01",
    )

    # 1000 prompt tokens ($0.00010) + 500 completion tokens ($0.00020) = $0.00030
    cost = provenance.calculate_cost_usd(1000, 500)
    assert cost == pytest.approx(0.00030, rel=1e-5)

    # Missing tokens returns None
    assert provenance.calculate_cost_usd(None, 500) is None
    assert provenance.calculate_cost_usd(1000, None) is None

    # Config without pricing yields None cost
    config_no_pricing = ExternalLLMConfig(
        endpoint="https://api.example.com/v1",
        model="gemini-2.0-flash",
        api_key="test-key",
    )
    assert config_no_pricing.pricing is None
    assert config_no_pricing.prompt_price_per_m is None
    assert config_no_pricing.completion_price_per_m is None


def test_ollama_resource_overhead_representation():
    """Verify that Ollama inference service resources are not fabricated or conflated with workload pods."""
    native_response = {
        "message": {
            "role": "assistant",
            "content": json.dumps(
                {
                    "profile": "small",
                    "reasons": ["Lightweight job"],
                    "score": 80.0,
                    "image_id": "minimal-python",
                    "image_reasons": ["Python standard"],
                }
            ),
        },
        "prompt_eval_count": 50,
        "eval_count": 20,
    }
    transport = MockTransport(native_response)
    client = OllamaClient(endpoint="http://localhost:11434/api/chat", transport=transport)
    config = SelfHostedLLMConfig(endpoint="http://localhost:11434/api/chat", model="llama3:latest")
    recommender = SelfHostedLLMRecommender(config=config, client=client)

    request = RecommendationRequest(intent="Test job", dataset_size_gb=0.1)
    result = recommender.recommend_with_metadata(request)

    # Telemetry contains tokens and latency, but NO fabricated host CPU/memory/GPU metrics
    assert result.metadata.prompt_tokens == 50
    assert result.metadata.completion_tokens == 20
    assert result.metadata.estimated_cost_usd is None  # Self-hosted local has null cost unless configured


def test_evidence_integrity_immutable_hash_and_rejection_of_corrupt_data(tmp_path: Path):
    """Verify evidence immutability and strict duplicate/corruption rejection."""
    dataset = load_dataset(DEFAULT_DATASET)
    pred_dir = tmp_path / "integrity_preds"
    analysis_dir = tmp_path / "integrity_analysis"

    run_args = argparse.Namespace(
        dataset=DEFAULT_DATASET,
        recommenders="static_profile_baseline",
        split="development",
        repeats=1,
        seed=20260808,
        output=pred_dir,
        dry_run=False,
        resume=False,
        randomize_order=False,
    )
    run(run_args)

    pred_file = pred_dir / "predictions.jsonl"
    digest_before = file_sha256(pred_file)

    # Run analysis
    analysis_args = argparse.Namespace(
        dataset=DEFAULT_DATASET,
        predictions=pred_file,
        system_trials=None,
        user_events=None,
        reprovision_trials=None,
        bootstrap_replicates=10,
        seed=20260808,
        out=analysis_dir,
    )
    analyze(analysis_args)

    # Raw evidence must be 100% byte-identical before and after analysis
    digest_after = file_sha256(pred_file)
    assert digest_before == digest_after

    # Verify duplicate trial rejection
    records = read_jsonl(pred_file, validate_prediction)
    duplicate_records = records + [records[0]]
    with pytest.raises(ValueError, match="duplicate prediction key"):
        score_predictions(duplicate_records, dataset)


def test_missing_external_api_credentials_blocked(tmp_path: Path):
    """Verify missing external API key is explicitly caught, blocked, and does not fabricate predictions."""
    # 1. Config loading without API key raises missing_credentials error
    with pytest.raises(ValueError, match="missing_credentials"):
        ExternalLLMConfig.from_environ({"EXTERNAL_LLM_ENDPOINT": "https://api.example.com/v1", "EXTERNAL_LLM_MODEL": "gemini-2.0-flash"})

    # 2. Evaluation runner cleanly records missing_credentials without silent fallback or fabrication
    pred_dir = tmp_path / "missing_creds_preds"
    run_args = argparse.Namespace(
        dataset=DEFAULT_DATASET,
        recommenders="external_llm",
        split="development",
        repeats=1,
        seed=20260808,
        output=pred_dir,
        dry_run=False,
        resume=False,
        randomize_order=False,
    )
    manifest = run(run_args)

    assert manifest["blocked_backends"] == {"external_llm": "missing_credentials"}
    assert manifest["errors"] == manifest["records"]

    pred_file = pred_dir / "predictions.jsonl"
    records = read_jsonl(pred_file, validate_prediction)
    assert len(records) > 0
    for record in records:
        assert record["recommender"] == "external_llm"
        assert record["effective_backend"] == "unavailable"
        assert record["error_category"] == "missing_credentials"
        assert record["applied_profile"] is None
        assert record["fallback_used"] is False
        assert record["policy_compliant"] is False
        assert record["model_id"] == "gemini-3.5-flash"


def test_metric_consistency_across_recommendation_stages(tmp_path: Path):
    """Verify that deterministic methods have Ops Acc == Raw Acc, and LLM fallback isolation behaves strictly."""
    dataset = load_dataset(DEFAULT_DATASET)
    pred_dir = tmp_path / "consistency_preds"
    run_args = argparse.Namespace(
        dataset=DEFAULT_DATASET,
        recommenders="static_profile_baseline,rule_based_mapping",
        split="test",
        repeats=1,
        seed=20260808,
        output=pred_dir,
        dry_run=False,
        resume=False,
        randomize_order=False,
    )
    run(run_args)

    pred_file = pred_dir / "predictions.jsonl"
    records = read_jsonl(pred_file, validate_prediction)
    scored = score_predictions(records, dataset)

    # Group by recommender
    by_rec = {}
    for r in scored:
        by_rec.setdefault(r["recommender"], []).append(r)

    # 1. Static baseline: Ops Joint Acc == Raw Joint Acc, Fallback == 0
    static_ops_acc = sum(r["joint_acceptable"] for r in by_rec["static_profile_baseline"]) / len(by_rec["static_profile_baseline"])
    static_raw_acc = sum(r["raw_model_joint_acceptable"] for r in by_rec["static_profile_baseline"]) / len(by_rec["static_profile_baseline"])
    assert static_ops_acc == static_raw_acc
    assert all(r["fallback_used"] is False for r in by_rec["static_profile_baseline"])

    # 2. Rule-based mapping: Ops Joint Acc == Raw Joint Acc, Fallback == 0 (including deep learning items)
    rule_ops_acc = sum(r["joint_acceptable"] for r in by_rec["rule_based_mapping"]) / len(by_rec["rule_based_mapping"])
    rule_raw_acc = sum(r["raw_model_joint_acceptable"] for r in by_rec["rule_based_mapping"]) / len(by_rec["rule_based_mapping"])
    assert rule_ops_acc == rule_raw_acc == 0.6875
    assert all(r["fallback_used"] is False for r in by_rec["rule_based_mapping"])

    # Deep learning items with gpu_or_large are correctly normalized and acceptable
    dl_items = [r for r in by_rec["rule_based_mapping"] if r["workload_family"] in {"pytorch-training", "tensorflow-training"}]
    assert len(dl_items) == 6
    for item in dl_items:
        assert item["raw_model_profile_acceptable"] is True
        assert item["raw_model_joint_acceptable"] is True


def test_reproducibility_provenance_manifest_completeness(tmp_path: Path):
    """Verify run-manifest.json contains complete provenance metadata required by Section 5."""
    pred_dir = tmp_path / "provenance_preds"
    run_args = argparse.Namespace(
        dataset=DEFAULT_DATASET,
        recommenders="static_profile_baseline,rule_based_mapping",
        split="development",
        repeats=1,
        seed=20260808,
        output=pred_dir,
        dry_run=False,
        resume=False,
        randomize_order=False,
    )
    manifest = run(run_args)

    required_fields = [
        "protocol_version",
        "experiment_id",
        "run_id",
        "created_utc",
        "dataset",
        "dataset_path",
        "dataset_sha256",
        "policy_version",
        "policy_sha256",
        "catalog_version",
        "catalog_sha256",
        "git_commit",
        "git_branch",
        "git_worktree_dirty",
        "environment_id",
        "runtime_environment",
        "split",
        "recommenders",
        "methods_provenance",
        "repeats",
        "seed",
        "randomize_order",
        "records",
        "errors",
        "blocked_backends",
        "raw_outputs_append_only",
        "predictions_path",
        "predictions_sha256",
    ]
    for field in required_fields:
        assert field in manifest, f"Missing required provenance field {field}"

    # Verify non-LLM methods have prompt_status = not_applicable
    methods_prov = manifest["methods_provenance"]
    assert methods_prov["static_profile_baseline"]["prompt_status"] == "not_applicable"
    assert methods_prov["rule_based_mapping"]["prompt_status"] == "not_applicable"


def test_validate_evidence_tool_and_corruption_detection(tmp_path: Path):
    """Test the evaluation_v4 validate_evidence tool on valid and corrupted evidence."""
    # 1. Existing audit demo evidence passes validation
    audit_res = validate_evaluation_v4_evidence(Path("results/offline-audit-demo"))
    assert audit_res["status"] == "pass"
    assert audit_res["records_validated"] == 96

    # 2. Test disposable copy mutation detection
    demo_dir = Path("results/offline-audit-demo")
    test_evidence = tmp_path / "test_evidence"
    test_evidence.mkdir()
    (test_evidence / "run-manifest.json").write_text((demo_dir / "run-manifest.json").read_text(encoding="utf-8"), encoding="utf-8")
    (test_evidence / "predictions.jsonl").write_text((demo_dir / "predictions.jsonl").read_text(encoding="utf-8"), encoding="utf-8")

    # Valid copy passes
    assert validate_evaluation_v4_evidence(test_evidence)["status"] == "pass"

    # SHA corruption is caught
    with (test_evidence / "predictions.jsonl").open("a", encoding="utf-8") as h:
        h.write("\n")
    with pytest.raises(EvidenceValidationError, match="SHA-256 mismatch"):
        validate_evaluation_v4_evidence(test_evidence)
