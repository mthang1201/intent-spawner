"""Frozen regression contract for the existing rule-based P1 system."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from recommender.deployment import DeploymentMetadata
from recommender.jupyterhub_integration import (
    PREVIEW_VERSION,
    PROFILE_RESOURCES,
    RecommendationPreviewRuntime,
)
from recommender.models import RecommendationRequest
from recommender.policy import PolicyValidator
from recommender.recommender import recommend_profile
from recommender.rule_based import (
    DATA_TERMS,
    GPU_TERMS,
    PROFILES,
    TRAINING_TERMS,
    RuleBasedRecommender,
    load_image_catalog,
)


CATALOG_VERSION = "2026-08-06.1"
POLICY_VERSION = "resource-image-policy-v1"
SCHEMA_VERSION = "spawn-recommendation-v1"
BACKEND_NAME = "rule_based"
BACKEND_VERSION = "rule-based-v1"

MINIMAL_REFERENCE = (
    "quay.io/jupyter/minimal-notebook@sha256:"
    "a153ceb6b41db4f86b7d7dc20c7b63d08e75e2038d5e8758b954fda50ed2e18d"
)
SCIPY_REFERENCE = (
    "quay.io/jupyter/scipy-notebook@sha256:"
    "1a91a693c8cb086f3607f2ed38a2743ecd53dd1dac2d3e84e6cd647b33fd2bba"
)
TENSORFLOW_REFERENCE = (
    "quay.io/jupyter/tensorflow-notebook@sha256:"
    "25ddc4f73bea5a252335775b59c0e1d4969bbf70ee78d3c6d1d9da02ce58fb68"
)
PYTORCH_REFERENCE = (
    "quay.io/jupyter/pytorch-notebook@sha256:"
    "69c72823a4e0dbee17114bbe44d0377bc9a39504a76f6314995f7d5bfaa98d60"
)

UNIFIED_KEYS = [
    "profile",
    "reasons",
    "score",
    "image_id",
    "image_reference",
    "image_reasons",
    "catalog_version",
    "policy_version",
    "schema_version",
    "backend_name",
    "backend_version",
]


def _expected(
    *,
    profile: str,
    reasons: list[str],
    score: int,
    image_id: str = "minimal-python",
    image_reference: str = MINIMAL_REFERENCE,
    image_reasons: list[str] | None = None,
) -> dict[str, object]:
    return {
        "profile": profile,
        "reasons": reasons,
        "score": score,
        "image_id": image_id,
        "image_reference": image_reference,
        "image_reasons": image_reasons
        if image_reasons is not None
        else ["no specialized image signal detected", "selected catalog default"],
        "catalog_version": CATALOG_VERSION,
        "policy_version": POLICY_VERSION,
        "schema_version": SCHEMA_VERSION,
        "backend_name": BACKEND_NAME,
        "backend_version": BACKEND_VERSION,
    }


P1_CASES = [
    pytest.param(
        RecommendationRequest(),
        _expected(
            profile="small",
            reasons=["basic/light workload context"],
            score=0,
        ),
        id="empty-default",
    ),
    pytest.param(
        RecommendationRequest(intent="basic", dataset_size_gb=0.499),
        _expected(
            profile="small",
            reasons=["basic/light workload context"],
            score=0,
        ),
        id="below-half-gb",
    ),
    pytest.param(
        RecommendationRequest(intent="basic", dataset_size_gb=0.5),
        _expected(
            profile="medium",
            reasons=["dataset size >= 0.5GB"],
            score=1,
        ),
        id="at-half-gb",
    ),
    pytest.param(
        RecommendationRequest(intent="basic", dataset_size_gb=1.999),
        _expected(
            profile="medium",
            reasons=["dataset size >= 0.5GB"],
            score=1,
        ),
        id="below-two-gb",
    ),
    pytest.param(
        RecommendationRequest(intent="basic", dataset_size_gb=2.0),
        _expected(
            profile="large",
            reasons=["dataset size >= 2GB"],
            score=3,
        ),
        id="at-two-gb",
    ),
    pytest.param(
        RecommendationRequest(intent="train CSV model"),
        _expected(
            profile="large",
            reasons=[
                "data-processing context detected: csv",
                "training/modeling context detected: train, model",
            ],
            score=3,
            image_id="scipy-data-science",
            image_reference=SCIPY_REFERENCE,
            image_reasons=[
                "image capability match: csv, train, model",
                "selected from administrator catalog only",
            ],
        ),
        id="additive-data-training",
    ),
    pytest.param(
        RecommendationRequest(
            intent="TRAIN a DEEP LEARNING classifier",
            dataset_size_gb=3,
            code_context="import TensorFlow as tf\nimport TORCH",
        ),
        _expected(
            profile="gpu_or_large",
            reasons=[
                "GPU/deep-learning context detected: torch, tensorflow, deep learning",
                "Demo environment has no real GPU, so this maps to Large resources.",
            ],
            score=99,
            image_id="tensorflow-deep-learning",
            image_reference=TENSORFLOW_REFERENCE,
            image_reasons=[
                "image capability match: tensorflow",
                "selected from administrator catalog only",
            ],
        ),
        id="gpu-short-circuit-and-image-priority",
    ),
    pytest.param(
        RecommendationRequest(intent="deep learning with torch"),
        _expected(
            profile="gpu_or_large",
            reasons=[
                "GPU/deep-learning context detected: torch, deep learning",
                "Demo environment has no real GPU, so this maps to Large resources.",
            ],
            score=99,
            image_id="pytorch-deep-learning",
            image_reference=PYTORCH_REFERENCE,
            image_reasons=[
                "image capability match: torch, deep learning",
                "selected from administrator catalog only",
            ],
        ),
        id="pytorch-catalog-output",
    ),
    pytest.param(
        RecommendationRequest(intent="run method", code_context="estimator.fit(X, y)"),
        _expected(
            profile="medium",
            reasons=["training/modeling context detected: fit, .fit("],
            score=2,
            image_id="scipy-data-science",
            image_reference=SCIPY_REFERENCE,
            image_reasons=[
                "image capability match: fit, .fit(",
                "selected from administrator catalog only",
            ],
        ),
        id="dotted-fit-order",
    ),
    pytest.param(
        RecommendationRequest(intent="use modeler and trainer"),
        _expected(
            profile="small",
            reasons=["basic/light workload context"],
            score=0,
        ),
        id="whole-token-boundaries",
    ),
]


@pytest.mark.parametrize(("recommendation_request", "expected"), P1_CASES)
def test_deployed_p1_outputs_are_frozen(recommendation_request, expected):
    payload = RuleBasedRecommender().recommend(recommendation_request).to_unified_dict()

    assert list(payload) == UNIFIED_KEYS
    assert payload == expected


def test_p1_profile_domain_and_rule_vocabularies_are_frozen():
    assert PROFILES == ("small", "medium", "large", "gpu_or_large")
    assert GPU_TERMS == (
        "torch",
        "tensorflow",
        "cuda",
        "gpu",
        "deep learning",
        "resnet",
        "bert",
    )
    assert TRAINING_TERMS == (
        "train",
        "training",
        "fit",
        ".fit(",
        "sklearn",
        "scikit-learn",
        "xgboost",
        "model",
    )
    assert DATA_TERMS == (
        "pandas",
        "read_csv",
        "dataframe",
        "csv",
        "parquet",
    )


@pytest.mark.parametrize(
    "dataset_size_gb",
    [None, "", "not-a-number", -1, float("inf"), float("nan")],
)
def test_deployed_p1_invalid_dataset_hints_preserve_unknown_size_fallback(
    dataset_size_gb,
):
    recommendation = RuleBasedRecommender().recommend(
        RecommendationRequest(intent="basic", dataset_size_gb=dataset_size_gb)
    )

    assert recommendation.to_unified_dict() == _expected(
        profile="small",
        reasons=["basic/light workload context"],
        score=0,
    )
    assert recommend_profile(
        "basic",
        dataset_size_gb,
        "",
    ).to_dict() == recommendation.to_dict()


@pytest.mark.parametrize(("recommendation_request", "_expected_payload"), P1_CASES)
def test_legacy_cli_and_evaluation_function_remains_in_exact_p1_parity(
    recommendation_request,
    _expected_payload,
):
    deployed = RuleBasedRecommender().recommend(recommendation_request)
    legacy = recommend_profile(
        recommendation_request.intent,
        recommendation_request.dataset_size_gb,
        recommendation_request.code_context,
    )

    assert legacy.to_dict() == deployed.to_dict()


def test_p1_image_tie_breaking_is_priority_then_image_id():
    catalog = {
        "catalog_version": "tie-break-v1",
        "default_image": "alpha",
        "images": {
            "zeta": {
                "display_name": "Zeta",
                "reference": "example.invalid/zeta@sha256:" + "b" * 64,
                "description": "Zeta test image.",
                "capabilities": ["special"],
                "match_terms": ["special"],
                "priority": 10,
            },
            "alpha": {
                "display_name": "Alpha",
                "reference": "example.invalid/alpha@sha256:" + "a" * 64,
                "description": "Alpha test image.",
                "capabilities": ["special"],
                "match_terms": ["special"],
                "priority": 10,
            },
            "higher": {
                "display_name": "Higher",
                "reference": "example.invalid/higher@sha256:" + "c" * 64,
                "description": "Higher-priority test image.",
                "capabilities": ["other"],
                "match_terms": ["other"],
                "priority": 20,
            },
        },
    }
    backend = RuleBasedRecommender(catalog=catalog)

    assert backend.recommend_image("special")[0] == "alpha"
    assert backend.recommend_image("special other")[0] == "higher"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"profile": "arbitrary"}, "profile is not recognized"),
        ({"image_id": "arbitrary"}, "image is not allowlisted"),
        (
            {"image_reference": "example.invalid/forged@sha256:" + "f" * 64},
            "reference does not match",
        ),
        ({"schema_version": "spawn-recommendation-v2"}, "schema version"),
        ({"policy_version": "resource-image-policy-v2"}, "policy version"),
        ({"catalog_version": "stale-catalog"}, "stale image catalog"),
    ],
)
def test_policy_validator_remains_the_final_recommender_trust_boundary(
    changes,
    message,
):
    catalog = load_image_catalog()
    validator = PolicyValidator.from_catalog(profiles=PROFILES, catalog=catalog)
    trusted = RuleBasedRecommender(catalog=catalog).recommend(RecommendationRequest())

    with pytest.raises(ValueError, match=message):
        validator.validate(replace(trusted, **changes))


class _CountingP1Backend:
    backend_name = BACKEND_NAME

    def __init__(self, catalog):
        self.delegate = RuleBasedRecommender(catalog=catalog)
        self.calls = 0

    def recommend(self, request):
        self.calls += 1
        return self.delegate.recommend(request)


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


def test_preview_confirmation_and_pre_spawn_use_one_stored_p1_decision():
    catalog = load_image_catalog()
    backend = _CountingP1Backend(catalog)
    runtime = RecommendationPreviewRuntime(
        deployment=DeploymentMetadata(
            backend=BACKEND_NAME,
            backend_version=BACKEND_VERSION,
            package_version="intent-spawner-recommender-v2",
            package_checksum="a" * 64,
        ),
        catalog=catalog,
        backend=backend,
    )
    try:
        preview = asyncio.run(
            runtime.issue(
                "alice",
                {
                    "intent": "private deep learning job",
                    "dataset_size_gb": 0.2,
                    "code_context": "import torch",
                },
            )
        )
        assert backend.calls == 1
        assert preview["recommendation"]["profile"] == "gpu_or_large"
        assert preview["applied_profile"] == "large"

        preview_id = preview["recommendation_preview_id"]
        options = runtime.options_from_form(
            _spawner(),
            {
                "preview_version": [PREVIEW_VERSION],
                "decision_action": ["accept"],
                "recommendation_preview_id": [preview_id],
            },
        )
        record = runtime.previews[preview_id]
        assert record["confirmation"] == {
            "decision_action": "accept",
            "applied_profile": "large",
            "applied_image_id": "pytorch-deep-learning",
        }
        assert "private deep learning job" not in json.dumps(record)

        # Inputs added after confirmation are not recommendation inputs. The hook
        # must apply the stored, bound decision without calling P1 again.
        options.update(
            {
                "intent": "replace with a basic notebook",
                "dataset_size_gb": 0,
                "code_context": "print('forged')",
            }
        )
        target = _spawner(options=options)
        asyncio.run(runtime.pre_spawn(target))

        assert backend.calls == 1
        assert target.cpu_limit == PROFILE_RESOURCES["large"]["cpu_limit"]
        assert target.image == catalog["images"]["pytorch-deep-learning"]["reference"]
        assert preview_id not in runtime.previews
        with pytest.raises(ValueError, match="already used"):
            asyncio.run(runtime.pre_spawn(_spawner(options=options)))
        assert backend.calls == 1
    finally:
        runtime.executor.shutdown()
