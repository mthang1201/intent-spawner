"""Tests for the single natural-language workload input workflow."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from recommender.deployment import DeploymentMetadata
from recommender.jupyterhub_integration import (
    PREVIEW_VERSION,
    PROFILE_RESOURCES,
    RecommendationPreviewRuntime,
    options_form,
    validate_preview_request,
)
from recommender.models import (
    EnvironmentCandidate,
    ExtractionMode,
    ExtractionProvenance,
    GPURequirement,
    RecommendationRequest,
    ResourceConstraints,
    StructuredIntent,
    TaskType,
)
from recommender.p2_backend import P2Config, P2Recommender
from recommender.rule_based import RuleBasedRecommender, load_image_catalog


def preview_runtime(*, backend=None) -> RecommendationPreviewRuntime:
    catalog = load_image_catalog()
    return RecommendationPreviewRuntime(
        deployment=DeploymentMetadata(
            backend="rule_based" if backend is None else backend.backend_name,
            backend_version="rule-based-v1" if backend is None else backend.backend_version,
            package_version="intent-spawner-recommender-v3",
            package_checksum="a" * 64,
        ),
        catalog=catalog,
        backend=backend or RuleBasedRecommender(catalog=catalog),
    )


def spawner(username: str = "alice", *, options=None):
    logs = []
    return SimpleNamespace(
        user=SimpleNamespace(name=username),
        current_user=username,
        user_options=options or {},
        environment={},
        extra_annotations={},
        extra_resource_guarantees={},
        extra_resource_limits={},
        cpu_guarantee=0.0,
        cpu_limit=0.0,
        mem_guarantee="",
        mem_limit="",
        image="",
        log=SimpleNamespace(info=lambda *args: logs.append(args)),
        logs=logs,
    )


# --- 1. UI Rendering Tests ---

def test_ui_renders_single_workload_textarea_and_no_legacy_fields():
    runtime = preview_runtime()
    html = options_form(runtime, "/hub/recommendation-preview")

    # Primary single textarea
    assert '<label for="intent"><strong>Describe your workload</strong></label>' in html
    assert '<textarea id="intent"' in html
    assert "Describe what you want to do, your framework or libraries" in html
    assert "placeholder=" in html

    # Separate visible workload fields must NOT be rendered
    assert 'id="dataset_size_gb"' not in html
    assert 'name="dataset_size_gb"' not in html
    assert 'id="code_context"' not in html
    assert 'name="code_context"' not in html
    assert "Estimated dataset size" not in html
    assert "Optional imports or code context" not in html

    # Production UI must NOT expose a P1/P2 backend selector
    assert 'name="backend"' not in html
    assert 'id="backend"' not in html
    assert 'name="recommender"' not in html
    assert 'id="recommender"' not in html
    assert "P1" not in html
    assert "P2" not in html

    runtime.executor.shutdown()


def test_ui_javascript_serializes_only_intent_and_validates_client_side():
    runtime = preview_runtime()
    html = options_form(runtime, "/hub/recommendation-preview")

    # JS lookup of intent only
    assert 'const intentField=document.getElementById("intent");' in html
    assert 'const values=()=>({intent:intentField.value});' in html
    # Obsolete DOM lookups removed
    assert 'document.getElementById("dataset_size_gb")' not in html
    assert 'document.getElementById("code_context")' not in html
    # Client-side non-empty check
    assert "Please enter a workload description before previewing." in html

    runtime.executor.shutdown()


# --- 2. Preview Request Validation Tests ---

def test_single_natural_language_preview_request_succeeds():
    runtime = preview_runtime()
    payload = asyncio.run(
        runtime.issue(
            "alice",
            {"intent": "I want to train a small PyTorch image classification model using about 8 GB of images."},
        )
    )
    assert payload["preview_version"] == PREVIEW_VERSION
    assert "recommendation_preview_id" in payload
    assert payload["applied_profile"] in PROFILE_RESOURCES
    assert payload["image_display_name"]
    runtime.executor.shutdown()


def test_empty_and_whitespace_only_workload_input_is_rejected():
    runtime = preview_runtime()

    with pytest.raises(ValueError, match="workload description must not be empty"):
        asyncio.run(runtime.issue("alice", {"intent": ""}))

    with pytest.raises(ValueError, match="workload description must not be empty"):
        asyncio.run(runtime.issue("alice", {"intent": "   \n\t  \r  "}))

    with pytest.raises(ValueError, match="workload description must not be empty"):
        validate_preview_request({})

    runtime.executor.shutdown()


def test_workload_alias_is_rejected_as_unsupported_field():
    # Only canonical 'intent' is accepted at the UI/API boundary
    with pytest.raises(ValueError, match="unsupported fields"):
        validate_preview_request({"workload": "train a model"})


def test_legacy_fields_backward_compatibility():
    # When omitted, dataset_size_gb is None
    req = validate_preview_request({"intent": "train a model"})
    assert req.intent == "train a model"
    assert req.dataset_size_gb is None
    assert req.code_context == ""

    # When provided by internal/legacy test callers, validated and parsed
    req_legacy = validate_preview_request(
        {"intent": "train a model", "dataset_size_gb": "3.5", "code_context": "import torch"}
    )
    assert req_legacy.intent == "train a model"
    assert req_legacy.dataset_size_gb == 3.5
    assert req_legacy.code_context == "import torch"


# --- 3. P1 Rule-Based Baseline Tests ---

def test_p1_missing_dataset_size_is_unknown():
    p1 = RuleBasedRecommender(catalog=load_image_catalog())

    # When no size is specified in text and dataset_size_gb is None
    req_no_size = RecommendationRequest(intent="basic Python calculations")
    assert req_no_size.dataset_size_gb is None
    rec_no_size = p1.recommend(req_no_size)

    # Size is unknown: no dataset size rules fire (e.g. no "dataset size >= ...")
    assert not any("dataset size" in reason for reason in rec_no_size.reasons)
    assert rec_no_size.profile == "small"
    assert "basic/light workload context" in rec_no_size.reasons


def test_p1_with_natural_language_and_backward_compatible_size():
    p1 = RuleBasedRecommender(catalog=load_image_catalog())

    # Natural language with dataset size in text but dataset_size_gb=None (unknown)
    req = RecommendationRequest(
        intent="I want to process a 5 GB CSV dataset using pandas for data analytics."
    )
    assert req.dataset_size_gb is None
    rec = p1.recommend(req)

    # Missing dataset_size_gb means size rules do not fire (unknown)
    assert not any("dataset size" in reason for reason in rec.reasons)
    # But keyword rules for data processing and image match still work
    assert rec.image_id == "scipy-data-science"
    assert rec.profile == "medium"

    # Backward-compatible request with explicit dataset_size_gb
    legacy_req = RecommendationRequest(
        intent="I want to process a CSV dataset using pandas for data analytics.",
        dataset_size_gb=5.0,
    )
    legacy_rec = p1.recommend(legacy_req)
    assert any("dataset size >= 2GB" in reason for reason in legacy_rec.reasons)
    assert legacy_rec.profile == "large"


def test_p1_keyword_gpu_short_circuit_from_raw_natural_language():
    p1 = RuleBasedRecommender(catalog=load_image_catalog())
    req = RecommendationRequest(
        intent="I need CUDA and PyTorch to train an image classification model."
    )
    rec = p1.recommend(req)
    assert rec.profile == "gpu_or_large"
    assert rec.image_id == "pytorch-deep-learning"
    assert any("GPU/deep-learning context detected" in reason for reason in rec.reasons)


# --- 4. P2 Uses StructuredIntent, Not RecommendationRequest.dataset_size_gb ---

def test_p2_downstream_ranking_uses_structured_intent_not_legacy_request_field():
    catalog = load_image_catalog()
    config = P2Config(extractor_mode="local")

    # Custom mock extractor that returns explicit StructuredIntent
    class CustomExtractor:
        network_bound = False
        extractor_name = "test-custom-extractor"
        extractor_version = "v1"

        def extract(self, request: RecommendationRequest) -> StructuredIntent:
            return StructuredIntent(
                task_types=(TaskType.DEEP_LEARNING, TaskType.MODEL_TRAINING),
                preferred_features=("cuda-userspace",),
                required_frameworks=("pytorch",),
                resource_constraints=ResourceConstraints(
                    gpu_requirement=GPURequirement.PREFERRED,
                    minimum_cpu_cores=1.0,
                    minimum_memory_gb=1.0,
                    dataset_size_gb=10.0,  # Intent extractor derived 10 GB
                ),
                normalized_query="train pytorch with cuda",
                extraction_confidence=0.95,
                extraction_provenance=ExtractionProvenance(
                    extractor_name="test-custom-extractor",
                    extractor_version="v1",
                    mode=ExtractionMode.PRIMARY,
                    degraded_reason=None,
                ),
            )

    p2 = P2Recommender(
        config=config,
        catalog=catalog,
        extractor=CustomExtractor(),
    )

    # Request has dataset_size_gb=None
    request = RecommendationRequest(
        intent="Fine-tune a PyTorch model on 10 GB dataset with CUDA",
        dataset_size_gb=None,
    )

    detailed = p2.recommend_detailed(request)
    assert detailed.fallback_category == "none"
    assert detailed.recommendation.image_id == "pytorch-deep-learning"

    # Verify that constraint evaluations in the trace operated on the StructuredIntent's constraints
    assert detailed.trace is not None
    assert detailed.trace.structured_intent.resource_constraints.dataset_size_gb == 10.0
    assert detailed.trace.structured_intent.resource_constraints.gpu_requirement is GPURequirement.PREFERRED
    assert detailed.trace.structured_intent.required_frameworks == ("pytorch",)


# --- 5. JupyterHub Full Options Form Submission & pre_spawn Path ---

def test_jupyterhub_full_options_form_submission_accept_flow():
    runtime = preview_runtime()
    target_spawner = spawner("alice")

    # 1. User submits single natural-language preview
    preview = asyncio.run(
        runtime.issue(
            "alice",
            {"intent": "Process a tabular dataset using pandas and scikit-learn"},
        )
    )
    preview_id = preview["recommendation_preview_id"]
    applied_profile = preview["applied_profile"]
    applied_image = preview["recommendation"]["image_id"]

    # 2. Form submission with "accept" decision
    formdata = {
        "preview_version": [PREVIEW_VERSION],
        "decision_action": ["accept"],
        "recommendation_preview_id": [preview_id],
    }
    options = runtime.options_from_form(target_spawner, formdata)
    assert options["decision_action"] == "accept"
    assert options["applied_profile"] == applied_profile
    assert options["applied_image_id"] == applied_image

    # Bind options to spawner
    target_spawner.user_options = options

    # 3. pre_spawn hook applies resources and digest-pinned image
    asyncio.run(runtime.pre_spawn(target_spawner))
    expected_resources = PROFILE_RESOURCES[applied_profile]
    assert target_spawner.cpu_limit == expected_resources["cpu_limit"]
    assert target_spawner.cpu_guarantee == expected_resources["cpu_guarantee"]
    assert target_spawner.image == runtime.images[applied_image]["reference"]
    assert "intent-spawner.local/profile" in target_spawner.extra_annotations
    assert target_spawner.extra_annotations["intent-spawner.local/profile"] == applied_profile

    # Verify audit log was written
    assert any("recommendation_audit=" in log_args[0] for log_args in target_spawner.logs)

    # 4. Preview token was consumed and cannot be reused
    with pytest.raises(ValueError, match="unknown, expired, restarted, or already used"):
        runtime.validate(preview_id, "alice", consume=False)

    runtime.executor.shutdown()


def test_jupyterhub_full_options_form_submission_override_flow():
    runtime = preview_runtime()
    target_spawner = spawner("alice")

    preview = asyncio.run(
        runtime.issue(
            "alice",
            {"intent": "Quick light exploratory script"},
        )
    )
    preview_id = preview["recommendation_preview_id"]

    # Form submission with "override" to medium profile and scipy-data-science image
    formdata = {
        "preview_version": [PREVIEW_VERSION],
        "decision_action": ["override"],
        "recommendation_preview_id": [preview_id],
        "override_profile": ["medium"],
        "override_image_id": ["scipy-data-science"],
    }
    options = runtime.options_from_form(target_spawner, formdata)
    assert options["decision_action"] == "override"
    assert options["applied_profile"] == "medium"
    assert options["applied_image_id"] == "scipy-data-science"

    target_spawner.user_options = options
    asyncio.run(runtime.pre_spawn(target_spawner))

    assert target_spawner.cpu_limit == PROFILE_RESOURCES["medium"]["cpu_limit"]
    assert target_spawner.image == runtime.images["scipy-data-science"]["reference"]

    runtime.executor.shutdown()


def test_jupyterhub_options_tamper_protection():
    runtime = preview_runtime()
    target_spawner = spawner("alice")

    preview = asyncio.run(
        runtime.issue("alice", {"intent": "light computation"})
    )
    preview_id = preview["recommendation_preview_id"]

    formdata = {
        "preview_version": [PREVIEW_VERSION],
        "decision_action": ["accept"],
        "recommendation_preview_id": [preview_id],
    }
    options = runtime.options_from_form(target_spawner, formdata)

    # Tamper with applied profile
    options["applied_profile"] = "large"
    target_spawner.user_options = options

    with pytest.raises(ValueError, match="spawn options changed after recommendation confirmation"):
        asyncio.run(runtime.pre_spawn(target_spawner))

    runtime.executor.shutdown()
