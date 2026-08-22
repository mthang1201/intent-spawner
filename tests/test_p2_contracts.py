"""Focused schema and trust-boundary tests for the internal P2 contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import json

import pytest

from recommender.models import (
    CONSTRAINT_EVALUATION_SCHEMA_VERSION,
    ENVIRONMENT_CANDIDATE_SCHEMA_VERSION,
    POLICY_VERSION,
    RANKED_CANDIDATE_SCHEMA_VERSION,
    RECOMMENDATION_TRACE_SCHEMA_VERSION,
    RESOURCE_CONSTRAINTS_SCHEMA_VERSION,
    RETRIEVAL_HIT_SCHEMA_VERSION,
    STRUCTURED_INTENT_SCHEMA_VERSION,
    ConstraintEvaluation,
    ContractValidationError,
    EnvironmentCandidate,
    GPURequirement,
    RankedCandidate,
    RecommendationTrace,
    ResourceConstraints,
    RetrievalHit,
    RetrievalSource,
    StructuredIntent,
    TaskType,
)


def _intent() -> StructuredIntent:
    return StructuredIntent(
        task_types=["model_training", TaskType.DATA_ANALYSIS, "data_analysis"],
        required_features=["  CUDA  ", "jupyterlab", "cuda"],
        preferred_features=["CUDA", " Visualization "],
        forbidden_features=["root access", "ROOT   ACCESS"],
        required_frameworks=["PyTorch"],
        preferred_frameworks=[" pytorch ", "TensorFlow"],
        required_libraries=["Pandas"],
        preferred_libraries=["NumPy", "pandas"],
        resource_constraints=ResourceConstraints(
            gpu_requirement="required",
            minimum_cpu_cores=2,
            minimum_memory_gb=8.0,
            dataset_size_gb=12,
        ),
        ambiguities=[" Dataset may be compressed ", "dataset  may be compressed"],
        normalized_query="  TRAIN   a PyTorch model  ",
        extraction_confidence=0.875,
    )


def _hit(candidate_id: str, source: str, rank: int, score: float) -> RetrievalHit:
    return RetrievalHit(
        candidate_id=candidate_id,
        source=source,
        rank=rank,
        score=score,
        retriever_version=f"{source}-retriever-v1",
        index_version="environment-index-v1",
    )


def _evaluation(candidate_id: str, feasible: bool = True) -> ConstraintEvaluation:
    return ConstraintEvaluation(
        candidate_id=candidate_id,
        feasible=feasible,
        matched_hard_constraints=("gpu", "memory") if feasible else ("memory",),
        violated_hard_constraints=() if feasible else ("gpu",),
        unsupported_constraints=(),
        soft_preference_score=0.0,
        soft_preference_components=(),
        explanation_codes=("candidate_feasible",) if feasible else ("candidate_infeasible",),
        evaluator_version="constraint-evaluator-v1",
        constraint_policy_version="constraint-policy-v1",
    )


def _ranked(candidate_id: str, rank: int, score: float) -> RankedCandidate:
    return RankedCandidate(
        candidate_id=candidate_id,
        rank=rank,
        score=score,
        ranking_reasons=("preferred framework", "feature match"),
        ranker_version="deterministic-ranker-v1",
    )


def _candidate(candidate_id: str = "large-pytorch") -> EnvironmentCandidate:
    return EnvironmentCandidate(
        candidate_id=candidate_id,
        profile_id="large",
        image_id="pytorch-deep-learning",
        catalog_version="2026-08-06.1",
        policy_version=POLICY_VERSION,
    )


def test_structured_intent_normalizes_deduplicates_and_applies_precedence():
    intent = _intent()

    assert intent.task_types == (TaskType.DATA_ANALYSIS, TaskType.MODEL_TRAINING)
    assert intent.required_features == ("cuda", "jupyterlab")
    assert intent.preferred_features == ("visualization",)
    assert intent.forbidden_features == ("root access",)
    assert intent.required_frameworks == ("pytorch",)
    assert intent.preferred_frameworks == ("tensorflow",)
    assert intent.required_libraries == ("pandas",)
    assert intent.preferred_libraries == ("numpy",)
    assert intent.ambiguities == ("dataset may be compressed",)
    assert intent.normalized_query == "train a pytorch model"
    assert intent.resource_constraints.gpu_requirement is GPURequirement.REQUIRED


def test_structured_intent_contains_semantics_but_no_selection_fields():
    names = {item.name for item in fields(StructuredIntent)}

    assert "profile" not in names
    assert "profile_id" not in names
    assert "image_id" not in names
    assert "image_reference" not in names
    assert "resources" not in names


@pytest.mark.parametrize(
    "field_name",
    ["minimum_cpu_cores", "minimum_memory_gb", "dataset_size_gb"],
)
@pytest.mark.parametrize("bad_value", [-1, float("nan"), float("inf"), float("-inf"), True, "1"])
def test_resource_constraints_reject_invalid_resource_values(field_name, bad_value):
    with pytest.raises(ContractValidationError):
        ResourceConstraints(**{field_name: bad_value})


@pytest.mark.parametrize("gpu", ["must_have", "optional", 1, None])
def test_resource_constraints_reject_unsupported_gpu_semantics(gpu):
    with pytest.raises(ContractValidationError, match="gpu_requirement"):
        ResourceConstraints(gpu_requirement=gpu)


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan"), float("inf"), True])
def test_structured_intent_rejects_invalid_confidence(confidence):
    with pytest.raises(ContractValidationError, match="extraction_confidence"):
        StructuredIntent(extraction_confidence=confidence)


def test_structured_intent_rejects_unsupported_task_type_and_feature_conflicts():
    with pytest.raises(ContractValidationError, match="task_types"):
        StructuredIntent(task_types=["profile_selection"])
    with pytest.raises(ContractValidationError, match="both required and forbidden"):
        StructuredIntent(
            required_features=["gpu"],
            forbidden_features=["GPU"],
        )


def test_environment_candidate_has_only_catalog_ids_and_provenance():
    candidate = _candidate()

    assert candidate.to_dict() == {
        "candidate_id": "large-pytorch",
        "profile_id": "large",
        "image_id": "pytorch-deep-learning",
        "catalog_version": "2026-08-06.1",
        "policy_version": POLICY_VERSION,
        "schema_version": ENVIRONMENT_CANDIDATE_SCHEMA_VERSION,
    }
    with pytest.raises(ContractValidationError, match="unknown fields"):
        EnvironmentCandidate.from_dict(
            {
                **candidate.to_dict(),
                "image_reference": "attacker.invalid/notebook:latest",
            }
        )
    with pytest.raises(ContractValidationError, match="unknown fields"):
        EnvironmentCandidate.from_dict(
            {**candidate.to_dict(), "resources": {"cpu": "999"}}
        )


@pytest.mark.parametrize(
    ("contract", "changes"),
    [
        (_hit("large-pytorch", "sparse", 1, 4.2), {"source": "keyword"}),
        (_hit("large-pytorch", "dense", 1, 0.9), {"rank": 0}),
        (_hit("large-pytorch", "fused", 1, 0.03), {"score": float("nan")}),
        (_ranked("large-pytorch", 1, 0.8), {"score": -0.1}),
    ],
)
def test_stage_contracts_reject_bad_enums_ranks_and_scores(contract, changes):
    payload = {**contract.to_dict(), **changes}

    with pytest.raises(ContractValidationError):
        type(contract).from_dict(payload)


def test_constraint_evaluation_is_internally_consistent():
    with pytest.raises(ContractValidationError, match="feasible candidate"):
        ConstraintEvaluation(
            candidate_id="candidate",
            feasible=True,
            matched_hard_constraints=(),
            violated_hard_constraints=("gpu",),
            unsupported_constraints=(),
            soft_preference_score=0.0,
            soft_preference_components=(),
            explanation_codes=("candidate_infeasible",),
            evaluator_version="constraints-v1",
            constraint_policy_version="constraint-policy-v1",
        )
    with pytest.raises(ContractValidationError, match="requires at least one"):
        ConstraintEvaluation(
            candidate_id="candidate",
            feasible=False,
            matched_hard_constraints=(),
            violated_hard_constraints=(),
            unsupported_constraints=(),
            soft_preference_score=0.0,
            soft_preference_components=(),
            explanation_codes=("candidate_infeasible",),
            evaluator_version="constraints-v1",
            constraint_policy_version="constraint-policy-v1",
        )


def test_contract_serialization_is_canonical_and_round_trips():
    first = _intent()
    second = StructuredIntent(
        task_types=["data_analysis", "model_training"],
        required_features=["jupyterlab", "cuda"],
        preferred_features=["visualization", "cuda"],
        forbidden_features=["root access"],
        required_frameworks=["pytorch"],
        preferred_frameworks=["tensorflow", "pytorch"],
        required_libraries=["pandas"],
        preferred_libraries=["pandas", "numpy"],
        resource_constraints=ResourceConstraints(
            gpu_requirement=GPURequirement.REQUIRED,
            minimum_cpu_cores=2.0,
            minimum_memory_gb=8,
            dataset_size_gb=12.0,
        ),
        ambiguities=["dataset may be compressed"],
        normalized_query="train a pytorch model",
        extraction_confidence=0.875,
    )

    assert first.to_json() == second.to_json()
    assert StructuredIntent.from_json(first.to_json()) == first
    assert json.loads(first.to_json())["schema_version"] == STRUCTURED_INTENT_SCHEMA_VERSION
    assert "NaN" not in first.to_json()


def test_contract_json_rejects_duplicate_and_unknown_fields():
    with pytest.raises(ContractValidationError, match="duplicate JSON field"):
        ResourceConstraints.from_json(
            '{"gpu_requirement":"required","gpu_requirement":"preferred"}'
        )
    with pytest.raises(ContractValidationError, match="unknown fields"):
        ResourceConstraints.from_dict({"minimum_cpu": 2})


def test_recommendation_trace_sorts_stage_outputs_and_round_trips():
    selected = _candidate()
    trace = RecommendationTrace(
        pipeline_version="p2-pipeline-v1",
        catalog_version="2026-08-06.1",
        index_version="environment-index-v1",
        structured_intent=_intent(),
        retrieval_hits=(
            _hit("medium-scipy", "sparse", 2, 3.0),
            _hit("large-pytorch", "dense", 1, 0.9),
            _hit("large-pytorch", "sparse", 1, 4.0),
        ),
        constraint_evaluations=(
            _evaluation("medium-scipy", feasible=False),
            _evaluation("large-pytorch"),
        ),
        ranked_candidates=(_ranked("large-pytorch", 1, 0.95),),
        selected_candidate=selected,
    )

    assert [(item.source.value, item.rank) for item in trace.retrieval_hits] == [
        ("dense", 1),
        ("sparse", 1),
        ("sparse", 2),
    ]
    assert [item.candidate_id for item in trace.constraint_evaluations] == [
        "large-pytorch",
        "medium-scipy",
    ]
    assert RecommendationTrace.from_json(trace.to_json()) == trace
    assert trace.to_dict()["schema_version"] == RECOMMENDATION_TRACE_SCHEMA_VERSION


def test_recommendation_trace_rejects_inconsistent_provenance_and_selection():
    wrong_index_hit = RetrievalHit(
        candidate_id="large-pytorch",
        source="dense",
        rank=1,
        score=0.9,
        retriever_version="dense-v1",
        index_version="other-index-v1",
    )
    with pytest.raises(ContractValidationError, match="index_version"):
        RecommendationTrace(
            pipeline_version="p2-v1",
            catalog_version="2026-08-06.1",
            index_version="environment-index-v1",
            structured_intent=_intent(),
            retrieval_hits=(wrong_index_hit,),
        )
    with pytest.raises(ContractValidationError, match="rank-1"):
        RecommendationTrace(
            pipeline_version="p2-v1",
            catalog_version="2026-08-06.1",
            index_version="environment-index-v1",
            structured_intent=_intent(),
            ranked_candidates=(_ranked("medium-scipy", 1, 0.8),),
            selected_candidate=_candidate("large-pytorch"),
        )


def test_all_contracts_are_immutable_and_reject_unsupported_schema_versions():
    contracts = [
        ResourceConstraints(),
        StructuredIntent(),
        _candidate(),
        _hit("candidate", "sparse", 1, 1),
        _evaluation("candidate"),
        _ranked("candidate", 1, 1),
        RecommendationTrace(
            pipeline_version="p2-v1",
            catalog_version="catalog-v1",
            index_version="index-v1",
            structured_intent=StructuredIntent(),
        ),
    ]
    expected_versions = [
        RESOURCE_CONSTRAINTS_SCHEMA_VERSION,
        STRUCTURED_INTENT_SCHEMA_VERSION,
        ENVIRONMENT_CANDIDATE_SCHEMA_VERSION,
        RETRIEVAL_HIT_SCHEMA_VERSION,
        CONSTRAINT_EVALUATION_SCHEMA_VERSION,
        RANKED_CANDIDATE_SCHEMA_VERSION,
        RECOMMENDATION_TRACE_SCHEMA_VERSION,
    ]

    assert [item.schema_version for item in contracts] == expected_versions
    for contract in contracts:
        with pytest.raises(FrozenInstanceError):
            contract.schema_version = "changed"
        with pytest.raises(ContractValidationError, match="unsupported schema_version"):
            type(contract).from_dict(
                {**contract.to_dict(), "schema_version": "unsupported-v99"}
            )
