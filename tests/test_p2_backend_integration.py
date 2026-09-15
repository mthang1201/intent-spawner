from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from recommender.deployment import DeploymentMetadata
from recommender.hybrid_retrieval import HybridCandidateHit, HybridRetrievalResult
from recommender.jupyterhub_integration import (
    PREVIEW_VERSION,
    RecommendationPreviewRuntime,
    options_form,
)
from recommender.local_structured_intent import LocalStructuredIntentExtractor
from recommender.models import GPURequirement, RecommendationRequest, RetrievalSource
from recommender.p2_backend import P2Recommender
from recommender.policy import PolicyValidator
from recommender.registry import DEFAULT_REGISTRY, create_recommender
from recommender.rule_based import PROFILES, load_image_catalog


def _spawner(*, options=None, username="alice"):
    logs = []
    return SimpleNamespace(
        user=SimpleNamespace(name=username),
        user_options=options or {},
        extra_annotations={},
        extra_resource_guarantees={},
        extra_resource_limits={},
        log=SimpleNamespace(info=lambda *args: logs.append(args)),
        logs=logs,
    )


def _runtime(backend: P2Recommender) -> RecommendationPreviewRuntime:
    return RecommendationPreviewRuntime(
        deployment=DeploymentMetadata(
            backend="p2",
            backend_version=backend.backend_version,
            package_version="intent-spawner-recommender-v3",
            package_checksum="a" * 64,
        ),
        catalog=backend.catalog,
        backend=backend,
    )


def test_local_extractor_records_explicit_constraints_without_selecting_catalog_values():
    intent = LocalStructuredIntentExtractor().extract(
        RecommendationRequest(
            intent="This job requires a GPU and at least 6 CPU cores with minimum 12 GB memory.",
            dataset_size_gb=2.5,
            code_context="import pandas as pd\nfrom sklearn.model_selection import train_test_split",
        )
    )
    assert intent.resource_constraints.gpu_requirement is GPURequirement.REQUIRED
    assert intent.resource_constraints.minimum_cpu_cores == 6.0
    assert intent.resource_constraints.minimum_memory_gb == 12.0
    assert intent.resource_constraints.dataset_size_gb == 2.5
    assert intent.required_libraries == ("pandas", "scikit-learn")
    serialized = intent.to_dict()
    assert not {"candidate_id", "profile_id", "image_id", "image_reference"} & set(serialized)


def test_local_extractor_does_not_turn_general_need_phrases_into_packages():
    intent = LocalStructuredIntentExtractor().extract(
        RecommendationRequest(
            intent="This checksum needs deterministic CPU work.",
            code_context="import multiprocessing",
        )
    )
    assert intent.required_libraries == ()


def test_p2_is_new_registry_backend_and_resolves_final_values_from_trusted_corpus():
    assert "p2" in DEFAULT_REGISTRY.names
    backend = create_recommender("p2")
    assert isinstance(backend, P2Recommender)
    detailed = backend.recommend_detailed(
        RecommendationRequest(
            intent="Analyze a CSV with pandas",
            dataset_size_gb=0.8,
            code_context="import pandas as pd",
        )
    )
    document = backend.corpus.get(detailed.final_candidate_id)
    assert document is not None
    assert detailed.recommendation.profile == document.profile_id
    assert detailed.recommendation.image_id == document.image_id
    assert detailed.recommendation.image_reference == document.image_reference
    assert detailed.trace is not None
    assert detailed.trace.selected_candidate == document.to_environment_candidate()
    validator = PolicyValidator.from_catalog(profiles=PROFILES, catalog=backend.catalog)
    assert validator.validate(detailed.recommendation) is detailed.recommendation


def test_unknown_retrieved_candidate_cannot_cross_trusted_resolution_boundary(monkeypatch):
    backend = P2Recommender()
    malicious = HybridRetrievalResult(
        fused_hits=(
            HybridCandidateHit(
                candidate_id="attacker-candidate",
                rank=1,
                score=1.0,
                sparse_rank=1,
                sparse_score=1.0,
                retrieval_legs=(RetrievalSource.SPARSE,),
                index_version=backend.retriever.metadata.index_version,
            ),
        ),
        sparse_hits=(),
        dense_hits=(),
        metadata=backend.retriever.metadata,
    )
    monkeypatch.setattr(backend.retriever, "retrieve_detailed", lambda *args, **kwargs: malicious)
    detailed = backend.recommend_detailed(RecommendationRequest(intent="basic Python"))
    assert detailed.fallback_category == "pipeline_validation_failure"
    assert detailed.final_candidate_id != "attacker-candidate"
    trusted = backend.corpus.get(detailed.final_candidate_id)
    assert trusted is not None
    assert detailed.recommendation.image_reference == trusted.image_reference


def test_p2_operational_provenance_is_complete_and_contains_no_raw_context():
    backend = P2Recommender()
    secret_intent = "private-customer-intent-should-not-appear"
    secret_code = "SECRET_SOURCE_CODE = 42"
    detailed = backend.recommend_detailed(
        RecommendationRequest(
            intent=secret_intent,
            dataset_size_gb=0.2,
            code_context=secret_code,
        )
    )
    provenance = detailed.metadata.to_operational_dict()["p2_provenance"]
    required = {
        "backend_version",
        "structured_intent_schema_version",
        "extractor_model_id",
        "extractor_prompt_version",
        "dense_embedding_model_revision",
        "dense_index_version",
        "sparse_index_version",
        "hybrid_rrf",
        "candidate_count",
        "retrieved_candidate_count",
        "feasible_candidate_count",
        "final_candidate_id",
        "fallback_category",
    }
    assert required.issubset(provenance)
    rendered = json.dumps(provenance)
    assert secret_intent not in rendered
    assert secret_code not in rendered


def test_infeasible_p2_preview_requires_existing_manual_override_path():
    backend = P2Recommender()
    runtime = _runtime(backend)
    try:
        preview = asyncio.run(
            runtime.issue(
                "alice",
                {
                    "intent": "This training job requires a GPU device.",
                    "dataset_size_gb": 0.5,
                    "code_context": "import torch",
                },
            )
        )
        assert preview["requires_manual_override"] is True
        with pytest.raises(ValueError, match="manual override is required"):
            runtime.options_from_form(
                _spawner(),
                {
                    "preview_version": [PREVIEW_VERSION],
                    "decision_action": ["accept"],
                    "recommendation_preview_id": [preview["recommendation_preview_id"]],
                },
            )
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
        assert target.image == backend.catalog["images"]["pytorch-deep-learning"]["reference"]
        audit = json.loads(target.logs[-1][1])
        assert audit["p2_provenance"]["fallback_category"] == "unsupported_catalog"
        assert "requires a GPU" not in json.dumps(audit)
        # Verify infeasibility metadata is exposed
        assert "infeasibility" in preview
        assert preview["infeasibility"]["infeasible"] is True
        assert preview["infeasibility"]["message"] == "No approved profile fully satisfies this workload."
    finally:
        runtime.executor.shutdown()


def test_infeasible_workload_triggers_server_side_confirmation_block():
    backend = P2Recommender()
    runtime = _runtime(backend)
    try:
        preview = asyncio.run(
            runtime.issue(
                "alice",
                {
                    "intent": "Requires GPU acceleration with cuda 12 and 64 GB RAM.",
                    "code_context": "import torch",
                },
            )
        )
        assert preview["requires_manual_override"] is True
        inf = preview["infeasibility"]
        assert inf["infeasible"] is True
        assert inf["message"] == "No approved profile fully satisfies this workload."
        assert "action_guidance" in inf

        # Server-side confirmation rejection: accept action is strictly blocked
        with pytest.raises(ValueError, match="manual override is required"):
            runtime.options_from_form(
                _spawner(username="alice"),
                {
                    "preview_version": [PREVIEW_VERSION],
                    "decision_action": ["accept"],
                    "recommendation_preview_id": [preview["recommendation_preview_id"]],
                },
            )

        # Manual override succeeds
        options = runtime.options_from_form(
            _spawner(username="alice"),
            {
                "preview_version": [PREVIEW_VERSION],
                "decision_action": ["override"],
                "recommendation_preview_id": [preview["recommendation_preview_id"]],
                "override_profile": ["large"],
                "override_image_id": ["scipy-data-science"],
            },
        )
        assert options["applied_profile"] == "large"
        assert options["applied_image_id"] == "scipy-data-science"
    finally:
        runtime.executor.shutdown()


def test_infeasible_preview_forwards_detailed_backend_infeasibility_info():
    class InfeasibleDetailedBackend:
        def __init__(self):
            self.catalog = load_image_catalog()
            self.backend_name = "p2_mock"
            self.backend_version = "1.0.0"
            self.generation = {"backend": "mock-v1"}

        def recommend_with_metadata(self, request, *args, **kwargs):
            from recommender.models import SpawnRecommendation
            from recommender.reliability import RecommendationMetadata, RecommendationResult
            rec = SpawnRecommendation(
                profile="small",
                image_id="scipy-data-science",
                image_reference=self.catalog["images"]["scipy-data-science"]["reference"],
                catalog_version=self.catalog["catalog_version"],
                reasons=["Fallback selection"],
                image_reasons=["Default fallback"],
                score=1.0,
                backend_name="p2_mock",
                backend_version="1.0.0",
            )
            class EnrichedMetadata(RecommendationMetadata):
                def to_operational_dict(self):
                    d = super().to_operational_dict()
                    d["infeasibility_info"] = {
                        "infeasible": True,
                        "requested_resources": {"cpu_cores": 16.0, "memory_gb": 64.0, "gpu": "required"},
                        "catalog_limits": {"cpu_cores": 2.0, "memory_gb": 2.0, "gpu_count": 0},
                        "unmet_constraints": ["minimum_cpu_cores:16", "minimum_memory_gb:64", "gpu:required"],
                        "message": "No approved profile fully satisfies this workload.",
                        "action_guidance": "Please edit your workload intent or explicitly choose a manual override below.",
                    }
                    return d

            metadata = EnrichedMetadata(
                requested_backend="p2_mock",
                effective_backend="p2_mock",
                attempt_count=1,
                total_elapsed_seconds=0.01,
                timed_out=False,
                deadline_exhausted=False,
                fallback_used=True,
                fallback_error_category="no_feasible_candidate",
                p2_provenance={
                    "fallback_category": "no_feasible_candidate",
                    "feasible_candidate_count": 0,
                },
            )
            return RecommendationResult(rec, metadata)

    backend = InfeasibleDetailedBackend()
    runtime = _runtime(backend)
    try:
        preview = asyncio.run(
            runtime.issue(
                "bob",
                {"intent": "Large multi-gpu training workload with 64 GB RAM and 16 CPU cores."},
            )
        )
        assert preview["requires_manual_override"] is True
        inf = preview["infeasibility"]
        assert inf["infeasible"] is True
        assert inf["requested_resources"]["cpu_cores"] == 16.0
        assert inf["requested_resources"]["memory_gb"] == 64.0
        assert inf["requested_resources"]["gpu"] == "required"
        assert inf["catalog_limits"]["cpu_cores"] == 2.0
        assert inf["catalog_limits"]["memory_gb"] == 2.0
        assert inf["catalog_limits"]["gpu_count"] == 0
        assert "minimum_cpu_cores:16" in inf["unmet_constraints"]
    finally:
        runtime.executor.shutdown()


def test_feasible_workload_does_not_show_warning_or_infeasibility():
    backend = P2Recommender()
    runtime = _runtime(backend)
    try:
        preview = asyncio.run(
            runtime.issue(
                "dave",
                {
                    "intent": "Basic pandas data analysis and plotting with matplotlib.",
                    "dataset_size_gb": 0.1,
                    "code_context": "import pandas as pd",
                },
            )
        )
        assert preview.get("requires_manual_override") is not True
        assert "infeasibility" not in preview

        options = runtime.options_from_form(
            _spawner(username="dave"),
            {
                "preview_version": [PREVIEW_VERSION],
                "decision_action": ["accept"],
                "recommendation_preview_id": [preview["recommendation_preview_id"]],
            },
        )
        assert options["decision_action"] == "accept"
    finally:
        runtime.executor.shutdown()


def test_options_form_html_contains_infeasibility_warning_elements():
    backend = P2Recommender()
    runtime = _runtime(backend)
    html_output = options_form(runtime, "/hub/recommendation-preview")
    assert 'id="infeasibility-warning"' in html_output
    assert 'id="infeasibility-req-line"' in html_output
    assert 'id="infeasibility-avail-line"' in html_output
    assert "Catalog limits per resource:" in html_output
    assert "Workload exceeds available resources" in html_output
