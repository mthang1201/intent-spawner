"""Integration tests for the P3 recommendation backend."""

from __future__ import annotations

import asyncio
from dataclasses import fields
import json
from types import SimpleNamespace
import pytest

from recommender.candidate_corpus import build_candidate_corpus
from recommender.deployment import DeploymentMetadata
from recommender.external_llm import (
    ExternalLLMConfig,
    LLMCompletionResponse,
    LLMTimeoutError,
)
from recommender.jupyterhub_integration import PREVIEW_VERSION, RecommendationPreviewRuntime
from recommender.models import (
    RecommendationRequest,
    SpawnRecommendation,
)
from recommender.p3_backend import P3Config, P3Recommender
from recommender.p3_reranker import P3Reranker
from recommender.policy import PolicyValidator
from recommender.registry import DEFAULT_REGISTRY, create_recommender
from recommender.rule_based import PROFILES, load_image_catalog


class StaticClient:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = []

    def complete(self, request, *, timeout):
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        return self.response


class DynamicRerankerClient:
    """Mock client that dynamically ranks the supplied feasible candidates."""

    def __init__(self, preferred_first: str = "medium-scipy-data-science", score: float = 0.92):
        self.preferred_first = preferred_first
        self.score = score
        self.calls = []

    def complete(self, request, *, timeout):
        self.calls.append((request, timeout))
        user_content = json.loads(request.messages[1].content)
        feasible_candidates = user_content["feasible_candidates"]
        candidate_ids = [c["candidate_id"] for c in feasible_candidates]

        ordered = []
        if self.preferred_first in candidate_ids:
            ordered.append(self.preferred_first)
        for cid in candidate_ids:
            if cid not in ordered:
                ordered.append(cid)

        ranking = [
            {
                "candidate_id": cid,
                "score": self.score if index == 0 else round(0.5 / (index + 1), 2),
                "explanation": f"Rank {index + 1} explanation for {cid}",
            }
            for index, cid in enumerate(ordered)
        ]
        payload = json.dumps({"ranking": ranking})
        return LLMCompletionResponse(payload, prompt_tokens=150, completion_tokens=80)


def _spawner(*, options=None):
    logs = []
    return SimpleNamespace(
        user=SimpleNamespace(name="alice"),
        user_options=options or {},
        extra_annotations={},
        extra_resource_guarantees={},
        extra_resource_limits={},
        log=SimpleNamespace(info=lambda *args: logs.append(args)),
        logs=logs,
    )


def _runtime(backend: P3Recommender) -> RecommendationPreviewRuntime:
    return RecommendationPreviewRuntime(
        deployment=DeploymentMetadata(
            backend="p3",
            backend_version=backend.backend_version,
            package_version="intent-spawner-recommender-v3",
            package_checksum="a" * 64,
        ),
        catalog=backend.catalog,
        backend=backend,
    )


def test_p3_is_registered_and_creates_p3_recommender():
    assert "p3" in DEFAULT_REGISTRY.names
    backend = create_recommender("p3")
    assert isinstance(backend, P3Recommender)
    assert backend.backend_name == "p3"
    assert backend.backend_version == "p3-reranker-v1.0.0"


def test_p3_config_cannot_override_frozen_p2_algorithm_parameters():
    names = {field.name for field in fields(P3Config)}
    assert names == {
        "reranker_mode",
        "total_timeout",
        "max_concurrent_recommendations",
        "config_version",
    }
    assert not names.intersection(
        {
            "extractor_mode",
            "top_k",
            "sparse_top_k",
            "dense_top_k",
            "rrf_k",
            "sparse_weight",
            "dense_weight",
        }
    )


def test_p3_recommend_detailed_successful_reranking():
    catalog = load_image_catalog()
    corpus = build_candidate_corpus(image_catalog=catalog)

    client = DynamicRerankerClient(preferred_first="medium-scipy-data-science", score=0.92)
    reranker = P3Reranker(
        config=ExternalLLMConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="mock-reranker",
            api_key="key",
        ),
        client=client,
    )

    p3 = P3Recommender(
        catalog=catalog,
        corpus=corpus,
        reranker=reranker,
        config=P3Config(),
    )

    request = RecommendationRequest(
        intent="Analyze tabular data with pandas",
        dataset_size_gb=0.8,
        code_context="import pandas as pd\ndf = pd.read_csv('data.csv')",
    )

    detailed = p3.recommend_detailed(request)

    assert isinstance(detailed.recommendation, SpawnRecommendation)
    assert detailed.recommendation.backend_name == "p3"
    assert detailed.recommendation.profile == "medium"
    assert detailed.recommendation.image_id == "scipy-data-science"
    assert detailed.recommendation.score == 92.0
    assert detailed.fallback_category == "none"

    # Verify PolicyValidator approves the recommendation
    validator = PolicyValidator.from_catalog(profiles=PROFILES, catalog=catalog)
    validated = validator.validate(detailed.recommendation)
    assert validated is detailed.recommendation

    # Verify trace integrity
    assert detailed.trace is not None
    assert detailed.trace.selected_candidate is not None
    assert detailed.trace.selected_candidate.candidate_id == "medium-scipy-data-science"
    assert detailed.trace.ranked_candidates[0].candidate_id == "medium-scipy-data-science"


def test_p3_reranks_the_complete_frozen_p2_feasible_order():
    catalog = load_image_catalog()
    corpus = build_candidate_corpus(image_catalog=catalog)
    client = DynamicRerankerClient(preferred_first="medium-scipy-data-science")
    reranker = P3Reranker(
        config=ExternalLLMConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="mock-reranker",
            api_key="key",
        ),
        client=client,
    )
    p3 = P3Recommender(catalog=catalog, corpus=corpus, reranker=reranker)
    request = RecommendationRequest(
        intent="Analyze a dataframe with pandas",
        dataset_size_gb=0.5,
        code_context="import pandas as pd",
    )

    detailed = p3.recommend_detailed(request)
    p2_ranked = [
        item.candidate_id
        for item in detailed.p2_result.ranking_result.ranked_candidates
    ]
    submitted = json.loads(client.calls[0][0].messages[1].content)
    submitted_ids = [
        item["candidate_id"] for item in submitted["feasible_candidates"]
    ]

    assert submitted_ids == p2_ranked
    assert {
        item.candidate_id for item in detailed.reranking_result.reranked_candidates
    } == set(p2_ranked)
    assert detailed.metadata.p2_provenance == detailed.p2_result.metadata.p2_provenance
    assert (
        detailed.metadata.p3_provenance["frozen_p2_provenance"]
        == detailed.metadata.p2_provenance
    )


def test_p3_degrades_to_p2_ranking_when_reranker_fails():
    catalog = load_image_catalog()
    corpus = build_candidate_corpus(image_catalog=catalog)

    client = StaticClient(error=LLMTimeoutError("Reranker timeout"))
    reranker = P3Reranker(
        config=ExternalLLMConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="mock-reranker",
            api_key="key",
            timeout=1.0,
            max_retries=0,
        ),
        client=client,
    )

    p3 = P3Recommender(
        catalog=catalog,
        corpus=corpus,
        reranker=reranker,
    )

    request = RecommendationRequest(
        intent="Basic python scripts",
        dataset_size_gb=0.1,
    )

    detailed = p3.recommend_detailed(request)

    # Failed reranking returns the exact frozen P2 recommendation.
    assert detailed.recommendation == detailed.p2_result.recommendation
    assert detailed.recommendation.backend_name == "p2"
    assert detailed.fallback_category == "reranking_reranker_timeout"
    assert detailed.metadata.fallback_used is True
    assert detailed.reranking_result is not None
    assert detailed.reranking_result.degraded is True
    assert detailed.reranking_result.degraded_reason == "reranker_timeout"

    # Validation still succeeds with trusted catalog resolution
    validator = PolicyValidator.from_catalog(profiles=PROFILES, catalog=catalog)
    assert validator.validate(detailed.recommendation) is detailed.recommendation


def test_p3_operational_provenance_contains_no_raw_context():
    catalog = load_image_catalog()
    corpus = build_candidate_corpus(image_catalog=catalog)

    client = DynamicRerankerClient()
    reranker = P3Reranker(
        config=ExternalLLMConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="mock-reranker",
            api_key="key",
        ),
        client=client,
    )

    p3 = P3Recommender(
        catalog=catalog,
        corpus=corpus,
        reranker=reranker,
        config=P3Config(),
    )

    secret_intent = "SUPER_SECRET_UNANNOUNCED_RESEARCH_INTENT"
    secret_code = "SECRET_PROPRIETARY_ALGORITHM = 9999"

    detailed = p3.recommend_detailed(
        RecommendationRequest(
            intent=secret_intent,
            dataset_size_gb=0.1,
            code_context=secret_code,
        )
    )

    provenance = detailed.metadata.to_operational_dict()["p3_provenance"]
    rendered = json.dumps(provenance)

    assert secret_intent not in rendered
    assert secret_code not in rendered
    assert "reranker_name" in provenance
    assert "reranker_version" in provenance
    assert "frozen_p2_provenance" in provenance
    assert "dense_index_version" in provenance["frozen_p2_provenance"]
    assert "sparse_index_version" in provenance["frozen_p2_provenance"]


def test_infeasible_request_triggers_p3_manual_override():
    catalog = load_image_catalog()
    corpus = build_candidate_corpus(image_catalog=catalog)

    # Infeasible request: requiring GPU in catalog without GPU
    p3 = P3Recommender(catalog=catalog, corpus=corpus)
    runtime = _runtime(p3)

    try:
        preview = asyncio.run(
            runtime.issue(
                "alice",
                {
                    "intent": "This deep learning job requires GPU hardware.",
                    "dataset_size_gb": 1.0,
                    "code_context": "import torch",
                },
            )
        )
        assert preview["requires_manual_override"] is True

        options = runtime.options_from_form(
            _spawner(),
            {
                "preview_version": [PREVIEW_VERSION],
                "decision_action": ["override"],
                "recommendation_preview_id": [preview["recommendation_preview_id"]],
                "override_profile": ["large"],
                "override_image_id": ["pytorch-deep-learning"],
            },
        )
        target = _spawner(options=options)
        asyncio.run(runtime.pre_spawn(target))
        assert target.image == catalog["images"]["pytorch-deep-learning"]["reference"]
    finally:
        runtime.executor.shutdown()
