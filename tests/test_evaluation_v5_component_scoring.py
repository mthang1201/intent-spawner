from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

import evaluation_v5.analysis.component_scoring as component_scoring_module

from evaluation_v5.analysis.component_scoring import (
    COMPONENT_ANALYSIS_SCHEMA_VERSION,
    ComponentAnalysisError,
    GoldCase,
    GoldSource,
    PRIMARY_CATEGORIES,
    _validate_gold_evidence_join,
    _validate_primary_attribution,
    analyze_component_evidence,
    load_component_evidence,
    load_component_gold,
    load_validated_evidence,
    main,
    p3_headroom_report,
    score_component_records,
    score_recommendation,
    validate_analysis_package,
    write_analysis_package,
    write_not_executed,
)
from evaluation_v5.gold_dataset import (
    GOLD_DATASET_SCHEMA_VERSION,
    compile_gold_dataset,
    current_catalog_identity,
    validate_gold_dataset,
    write_document_exclusive,
)
from evaluation_v5.offline.runner import run_offline_recommendations
from evaluation_v5.offline.validate_evidence import validate_offline_evidence
from evaluation_v5.split_dataset import SplitRole, _read_split_bundle
from recommender.candidate_corpus import load_candidate_corpus
from recommender.models import GPURequirement, ResourceConstraints, StructuredIntent


CORPUS = load_candidate_corpus()


def _gold_structured(**changes):
    value = {
        "task_types": [],
        "required_features": ["pandas"],
        "preferred_features": [],
        "forbidden_features": [],
        "required_frameworks": [],
        "preferred_frameworks": [],
        "gpu_semantics": "unspecified",
        "minimum_cpu_cores": None,
        "minimum_memory_gb": None,
        "dataset_size_gb": 0.5,
        "ambiguities": [],
    }
    value.update(changes)
    return value


def _case(case_id: str = "case", family_id: str = "family") -> GoldCase:
    return GoldCase(
        case_id=case_id,
        family_id=family_id,
        variant_id=case_id,
        language="en",
        prompt="Analyze data with pandas.",
        dataset_size_gb=0.5,
        code_context_hints=("import pandas as pd",),
        gold_structured_intent=_gold_structured(),
        candidate_gold={
            "preferred_candidate_ids": ["medium-scipy-data-science"],
            "acceptable_candidate_ids": [
                "medium-scipy-data-science",
                "large-scipy-data-science",
            ],
        },
        image_gold={
            "preferred_image_ids": ["scipy-data-science"],
            "acceptable_image_ids": ["scipy-data-science"],
            "required_capabilities": ["pandas"],
        },
        policy_gold={
            "required_constraints": [],
            "explicitly_unsupported_requirements": [],
            "expected_feasibility": "feasible",
        },
    )


def _intent(
    *,
    required_libraries=("pandas",),
    required_features=(),
    required_frameworks=(),
    preferred_features=(),
    preferred_libraries=(),
    preferred_frameworks=(),
    forbidden_features=(),
    gpu=GPURequirement.UNSPECIFIED,
    cpu=None,
    memory=None,
    ambiguities=(),
):
    return StructuredIntent(
        required_libraries=required_libraries,
        required_features=required_features,
        required_frameworks=required_frameworks,
        preferred_features=preferred_features,
        preferred_libraries=preferred_libraries,
        preferred_frameworks=preferred_frameworks,
        forbidden_features=forbidden_features,
        resource_constraints=ResourceConstraints(
            gpu_requirement=gpu,
            minimum_cpu_cores=cpu,
            minimum_memory_gb=memory,
            dataset_size_gb=0.5,
        ),
        ambiguities=ambiguities,
        normalized_query="Analyze data with pandas.",
        extraction_confidence=1.0,
    ).to_dict()


def _ranked(candidate_ids):
    return [
        {"candidate_id": candidate_id, "rank": index, "score": 1.0 / index}
        for index, candidate_id in enumerate(candidate_ids, start=1)
    ]


def _record(
    case: GoldCase,
    *,
    system="P2",
    intent=None,
    retrieved=("small-scipy-data-science", "medium-scipy-data-science"),
    evaluations=None,
    final=("medium-scipy-data-science", "small-scipy-data-science"),
    predicted="medium-scipy-data-science",
    no_feasible=False,
    unsupported=(),
    fallback_category=None,
    status="completed",
    errors=None,
    backend_provenance=None,
    repeat_index=0,
):
    if intent is None:
        intent = _intent()
    if evaluations is None:
        evaluations = {candidate_id: True for candidate_id in retrieved}
    return {
        "record_id": f"{case.case_id}-{system}-{repeat_index}",
        "run_id": "synthetic-component-run",
        "case_id": case.case_id,
        "family_id": case.family_id,
        "variant_id": case.variant_id,
        "system_id": system,
        "repeat_index": repeat_index,
        "seed": repeat_index + 1,
        "status": status,
        "structured_intent": intent,
        "candidate_top_k": _ranked(retrieved),
        "constraint_evaluations": [
            {"candidate_id": candidate_id, "feasible": feasible}
            for candidate_id, feasible in evaluations.items()
        ],
        "constraint_summary": {
            "no_feasible_candidate": no_feasible,
            "unsupported_constraints": list(unsupported),
        },
        "final_ranking": _ranked(final),
        "predicted_candidate_id": predicted,
        "fallback": {
            "used": fallback_category is not None,
            "category": fallback_category,
        },
        "errors": errors,
        "backend_provenance": backend_provenance,
    }


def _score(record, case=None):
    return score_recommendation(
        record,
        case or _case(),
        corpus=CORPUS,
        retrieval_ks=(1, 3, 5),
    )


@pytest.mark.parametrize(
    ("category", "case", "record"),
    [
        (
            "EXTRACTION_ERROR",
            _case("extraction", "extraction"),
            lambda case: _record(
                case,
                intent=_intent(required_libraries=()),
                final=("small-scipy-data-science", "medium-scipy-data-science"),
                predicted="small-scipy-data-science",
            ),
        ),
        (
            "RETRIEVAL_MISS",
            _case("retrieval", "retrieval"),
            lambda case: _record(
                case,
                retrieved=("small-minimal-python",),
                evaluations={"small-minimal-python": True},
                final=("small-minimal-python",),
                predicted="small-minimal-python",
            ),
        ),
        (
            "CONSTRAINT_ERROR",
            _case("constraint", "constraint"),
            lambda case: _record(
                case,
                evaluations={
                    "small-scipy-data-science": True,
                    "medium-scipy-data-science": False,
                },
                final=("small-scipy-data-science",),
                predicted="small-scipy-data-science",
            ),
        ),
        (
            "RANKING_ERROR",
            _case("ranking", "ranking"),
            lambda case: _record(
                case,
                final=("small-scipy-data-science", "medium-scipy-data-science"),
                predicted="small-scipy-data-science",
            ),
        ),
        (
            "PROVIDER_FAILURE",
            _case("provider", "provider"),
            lambda case: _record(
                case,
                intent=None,
                retrieved=(),
                evaluations={},
                final=(),
                predicted="small-minimal-python",
                status="error",
                errors={"category": "TimeoutError", "code": "adapter_execution_error"},
            ) | {"structured_intent": None},
        ),
    ],
)
def test_primary_error_categories_for_feasible_requests(category, case, record):
    scored = _score(record(case), case)
    assert scored["end_to_end"]["primary_category"] == category


def test_unsupported_catalog_category_for_correct_infeasible_detection():
    case = replace(
        _case("unsupported", "unsupported"),
        gold_structured_intent=_gold_structured(
            required_features=["pytorch"],
            gpu_semantics="required",
        ),
        candidate_gold={"preferred_candidate_ids": [], "acceptable_candidate_ids": []},
        image_gold={
            "preferred_image_ids": [],
            "acceptable_image_ids": ["pytorch-deep-learning"],
            "required_capabilities": ["pytorch"],
        },
        policy_gold={
            "required_constraints": ["GPU device required"],
            "explicitly_unsupported_requirements": ["No profile grants a GPU"],
            "expected_feasibility": "infeasible",
        },
    )
    record = _record(
        case,
        intent=_intent(
            required_libraries=(),
            required_features=("pytorch",),
            gpu=GPURequirement.REQUIRED,
        ),
        retrieved=("small-pytorch-deep-learning",),
        evaluations={"small-pytorch-deep-learning": False},
        final=(),
        predicted="medium-minimal-python",
        no_feasible=True,
        unsupported=("gpu:required",),
        fallback_category="unsupported_catalog",
    )
    scored = _score(record, case)
    assert scored["end_to_end"]["primary_category"] == "UNSUPPORTED_CATALOG"
    assert scored["end_to_end"]["query_correct"] is True


def test_other_category_for_unresolved_ambiguous_request():
    case = replace(
        _case("ambiguous", "ambiguous"),
        gold_structured_intent=_gold_structured(
            ambiguities=["Needs clarification"]
        ),
        candidate_gold={"preferred_candidate_ids": [], "acceptable_candidate_ids": []},
        policy_gold={
            "required_constraints": [],
            "explicitly_unsupported_requirements": [],
            "expected_feasibility": "ambiguous",
        },
    )
    record = _record(
        case,
        intent=_intent(ambiguities=("Needs clarification",)),
    )
    scored = _score(record, case)
    assert scored["end_to_end"]["primary_category"] == "OTHER"


def test_success_has_no_primary_category_but_keeps_secondary_diagnostics():
    case = _case("success", "success")
    scored = _score(_record(case), case)
    assert scored["end_to_end"]["recommendation_failed"] is False
    assert scored["end_to_end"]["primary_category"] is None


def test_extraction_precedes_retrieval_and_hard_soft_metrics_remain_separate():
    case = _case("mixed", "mixed")
    record = _record(
        case,
        intent=_intent(required_libraries=()),
        retrieved=("small-minimal-python",),
        evaluations={"small-minimal-python": True},
        final=("small-minimal-python",),
        predicted="small-minimal-python",
    )
    scored = _score(record, case)
    assert scored["end_to_end"]["primary_category"] == "EXTRACTION_ERROR"
    assert "NO_ACCEPTABLE_IN_RETRIEVED_TOP_K" in scored["end_to_end"]["secondary_tags"]
    assert scored["extraction"]["required_features"]["recall"] == 0.0
    assert scored["extraction"]["preferred_features"]["recall"] is None


def test_feature_library_normalization_frameworks_and_numeric_diagnostics():
    case = replace(
        _case("extract", "extract"),
        gold_structured_intent=_gold_structured(
            required_frameworks=["scikit-learn"],
            preferred_features=["fast-startup"],
            minimum_cpu_cores=2,
            minimum_memory_gb=None,
        ),
    )
    scored = _score(
        _record(
            case,
            intent=_intent(
                required_libraries=("pandas",),
                required_frameworks=("scikit-learn",),
                preferred_libraries=("fast-startup",),
                cpu=2.0,
                memory=1.0,
            ),
        ),
        case,
    )
    extraction = scored["extraction"]
    assert extraction["required_features"]["exact_match"] is True
    assert extraction["preferred_features"]["exact_match"] is True
    assert extraction["required_frameworks"]["exact_match"] is True
    assert extraction["minimum_cpu_cores"]["outcome"] == "correct_value"
    assert extraction["minimum_memory_gb"]["outcome"] == "spurious"


def test_invalid_structured_intent_schema_is_an_extraction_error():
    case = _case("invalid-schema", "invalid-schema")
    record = _record(
        case,
        intent={"schema_version": "structured-intent-v1"},
        final=("small-scipy-data-science", "medium-scipy-data-science"),
        predicted="small-scipy-data-science",
    )
    scored = _score(record, case)
    assert scored["extraction"]["schema_valid"] is False
    assert scored["end_to_end"]["primary_category"] == "EXTRACTION_ERROR"


def test_gpu_and_ambiguity_detection_are_exact_but_text_is_not_compared():
    case = replace(
        _case("gpu-ambiguity", "gpu-ambiguity"),
        gold_structured_intent=_gold_structured(
            gpu_semantics="forbidden",
            ambiguities=["Human-authored ambiguity note"],
        ),
    )
    scored = _score(
        _record(
            case,
            intent=_intent(
                gpu=GPURequirement.FORBIDDEN,
                ambiguities=("Different diagnostic wording",),
            ),
        ),
        case,
    )
    assert scored["extraction"]["gpu_semantics"]["exact"] is True
    assert scored["extraction"]["ambiguity_detection"]["correct"] is True


def test_retrieval_recall_hit_mrr_and_ndcg_use_unfiltered_top_k():
    case = _case("retrieval-metrics", "retrieval-metrics")
    record = _record(
        case,
        retrieved=(
            "small-scipy-data-science",
            "medium-scipy-data-science",
            "large-scipy-data-science",
        ),
        final=("medium-scipy-data-science",),
    )
    scored = _score(record, case)
    assert scored["retrieval"]["metrics_at_k"]["1"]["recall"] == 0.0
    assert scored["retrieval"]["metrics_at_k"]["1"]["hit"] is False
    assert scored["retrieval"]["metrics_at_k"]["3"]["recall"] == 1.0
    assert scored["retrieval"]["metrics_at_k"]["3"]["hit"] is True
    assert scored["retrieval"]["mrr"] == 0.5
    assert scored["retrieval"]["metrics_at_k"]["3"]["ndcg"] is not None


def test_constraint_false_rejection_and_infeasible_survival_are_separate():
    case = _case("constraint-metrics", "constraint-metrics")
    record = _record(
        case,
        retrieved=("small-minimal-python", "medium-scipy-data-science"),
        evaluations={
            "small-minimal-python": True,
            "medium-scipy-data-science": False,
        },
        final=("small-minimal-python",),
        predicted="small-minimal-python",
    )
    scored = _score(record, case)
    assert scored["constraints"]["acceptable_false_rejection_rate"] == 1.0
    assert scored["constraints"]["infeasible_candidate_survival_rate"] == 1.0
    assert scored["constraints"]["selected_hard_constraint_violation"] is True


def _p3_provider_provenance():
    return {
        "p3_provenance": {
            "provider_failure": True,
            "reranker_degraded_reason": "reranker_provider_error",
        }
    }


def test_p3_provider_failure_is_primary_when_reranking_degradation_leaves_bad_fallback():
    case = _case("p3-provider", "p3-provider")
    record = _record(
        case,
        system="P3",
        final=("small-scipy-data-science", "medium-scipy-data-science"),
        predicted="small-scipy-data-science",
        backend_provenance=_p3_provider_provenance(),
    )
    scored = _score(record, case)
    assert scored["end_to_end"]["primary_category"] == "PROVIDER_FAILURE"
    assert "PROVIDER_DEGRADED" in scored["end_to_end"]["secondary_tags"]


def test_upstream_retrieval_miss_precedes_later_p3_provider_failure():
    case = _case("p3-upstream", "p3-upstream")
    record = _record(
        case,
        system="P3",
        retrieved=("small-minimal-python",),
        evaluations={"small-minimal-python": True},
        final=("small-minimal-python",),
        predicted="small-minimal-python",
        backend_provenance=_p3_provider_provenance(),
    )
    scored = _score(record, case)
    assert scored["end_to_end"]["primary_category"] == "RETRIEVAL_MISS"
    assert "PROVIDER_DEGRADED" in scored["end_to_end"]["secondary_tags"]


def test_p3_provider_failure_with_successful_p2_fallback_has_no_primary():
    case = _case("p3-success", "p3-success")
    scored = _score(
        _record(
            case,
            system="P3",
            backend_provenance=_p3_provider_provenance(),
            fallback_category="reranking_reranker_provider_error",
        ),
        case,
    )
    assert scored["end_to_end"]["recommendation_failed"] is False
    assert scored["end_to_end"]["primary_category"] is None
    assert "PROVIDER_DEGRADED" in scored["end_to_end"]["secondary_tags"]


def test_primary_attribution_invariant_covers_both_p2_and_p3():
    failed_case = _case("both-failed", "both-failed")
    failed_records = [
        _record(
            failed_case,
            system=system,
            final=("small-scipy-data-science", "medium-scipy-data-science"),
            predicted="small-scipy-data-science",
        )
        for system in ("P2", "P3")
    ]
    failed_result = score_component_records(
        _source([failed_case]), failed_records, corpus=CORPUS
    )
    assert {
        row["system_id"]: row["end_to_end"]["primary_category"]
        for row in failed_result.recommendations
    } == {"P2": "RANKING_ERROR", "P3": "RANKING_ERROR"}

    success_case = _case("both-success", "both-success")
    success_result = score_component_records(
        _source([success_case]),
        [_record(success_case, system=system) for system in ("P2", "P3")],
        corpus=CORPUS,
    )
    assert all(
        row["end_to_end"]["primary_category"] is None
        for row in success_result.recommendations
    )


def test_primary_attribution_rejects_unset_multiple_or_duplicated_categories():
    base = {
        "system_id": "P3",
        "end_to_end": {
            "recommendation_failed": True,
            "primary_category": None,
            "secondary_tags": [],
        },
    }
    with pytest.raises(ComponentAnalysisError, match="exactly one"):
        _validate_primary_attribution(base)

    multiple = deepcopy(base)
    multiple["end_to_end"]["primary_category"] = [
        "RETRIEVAL_MISS",
        "PROVIDER_FAILURE",
    ]
    with pytest.raises(ComponentAnalysisError, match="exactly one"):
        _validate_primary_attribution(multiple)

    duplicated = deepcopy(base)
    duplicated["end_to_end"]["primary_category"] = "PROVIDER_FAILURE"
    duplicated["end_to_end"]["secondary_tags"] = ["PROVIDER_FAILURE"]
    with pytest.raises(ComponentAnalysisError, match="duplicates"):
        _validate_primary_attribution(duplicated)


def test_provider_failure_is_attributed_to_the_observed_constraint_stage():
    case = _case("constraint-provider", "constraint-provider")
    record = _record(
        case,
        evaluations={},
        final=(),
        predicted="small-scipy-data-science",
        status="error",
        errors={"category": "TimeoutError", "code": "adapter_execution_error"},
    )
    scored = _score(record, case)
    assert scored["provider"]["stage_index"] == 3
    assert scored["end_to_end"]["primary_category"] == "PROVIDER_FAILURE"
    assert scored["end_to_end"]["primary_stage"] == "constraint_provider"


def _source(cases, role="development"):
    return GoldSource(
        role=role,
        dataset_id="synthetic-component-gold",
        schema_version="protocol-v5-gold-family-v1.0.0",
        source_file_sha256="1" * 64,
        canonical_sha256="2" * 64,
        catalog_identity={
            "candidate_corpus_version": CORPUS.corpus_version,
            "candidate_corpus_sha256": CORPUS.corpus_checksum,
            "image_catalog_version": CORPUS.source_image_catalog_version,
            "image_catalog_sha256": CORPUS.source_image_catalog_checksum,
            "profile_catalog_sha256": CORPUS.source_profile_catalog_checksum,
        },
        cases=tuple(cases),
    )


def _compiled_v2_document(*, include_second_variant=False):
    case = _case("component-cli-case", "component-cli-family")
    variants = [
        {
            "variant_id": case.case_id,
            "variant_class": "canonical_en",
            "language": case.language,
            "intent": case.prompt,
            "code_context": list(case.code_context_hints),
            "equivalence_status": "canonical_reference",
        }
    ]
    if include_second_variant:
        variants.append(
            {
                "variant_id": "component-cli-paraphrase",
                "variant_class": "paraphrase_en",
                "language": "en",
                "intent": "Use pandas to inspect and transform this table.",
                "code_context": ["import pandas as pd"],
                "equivalence_status": "reviewed_equivalent",
            }
        )
    return {
        "schema_version": GOLD_DATASET_SCHEMA_VERSION,
        "dataset_metadata": {
            "dataset_id": "component-cli-gold",
            "protocol_version": "5.0.0",
            "role": "development",
            "lifecycle": "frozen",
            "created_at_utc": "2026-08-24T00:00:00Z",
            "created_by": "component-scoring-test",
            "git_revision": "0" * 40,
            "evidence_classification": "synthetic_test_fixture",
            "freeze_metadata": {
                "frozen_at_utc": "2026-08-24T01:00:00Z",
                "frozen_by": "component-scoring-test",
            },
            "source_datasets": [],
        },
        "catalog_identity": current_catalog_identity(),
        "review_policy": {
            "required_workload_strata": ["data_processing"],
            "max_preferred_profile_share": 1.0,
            "max_preferred_image_share": 1.0,
        },
        "families": [
            {
                "family_id": case.family_id,
                "title": "Component CLI family",
                "workload_stratum": "data_processing",
                "difficulty": "medium",
                "executable_workload_id": None,
                "gold_structured_intent": case.gold_structured_intent,
                "candidate_gold": case.candidate_gold,
                "profile_gold": {
                    "preferred_profile_ids": ["medium"],
                    "acceptable_profile_ids": ["medium", "large"],
                },
                "image_gold": case.image_gold,
                "policy_gold": case.policy_gold,
                "variants": variants,
                "label_review": {
                    "status": "approved",
                    "reviewed_by": "component-scoring-test",
                    "reviewed_at_utc": "2026-08-24T01:00:00Z",
                    "notes": ["Synthetic fixture labels checked against the catalog."],
                },
                "source_provenance": None,
            }
        ],
    }


def _write_cli_inputs(
    tmp_path: Path,
    *,
    include_second_variant=False,
    system_ids=("P2",),
):
    authoring_path = tmp_path / "family-gold.json"
    document = _compiled_v2_document(
        include_second_variant=include_second_variant
    )
    write_document_exclusive(authoring_path, document)
    bundle = compile_gold_dataset(validate_gold_dataset(document))
    compiled_path = tmp_path / "compiled-gold.json"
    write_document_exclusive(compiled_path, bundle.to_dict())
    split = _read_split_bundle(
        compiled_path,
        expected_role=SplitRole.DEVELOPMENT,
        expected_split_id="v5-development",
    )
    evidence_dir = tmp_path / "prompt5-evidence"
    run_offline_recommendations(
        split,
        result_dir=evidence_dir,
        system_ids=system_ids,
        repeats=1,
        seed=8128,
        frozen_configuration={"snapshot": "component-scoring-cli-test-v1"},
    )
    return authoring_path, compiled_path, evidence_dir, split, document


def test_all_failed_p2_recommendations_are_categorized_and_aggregated_by_family():
    cases = [_case("a", "one"), _case("b", "one"), _case("c", "two")]
    records = [
        _record(
            cases[0],
            final=("small-scipy-data-science", "medium-scipy-data-science"),
            predicted="small-scipy-data-science",
        ),
        _record(cases[1]),
        _record(cases[2]),
    ]
    result = score_component_records(_source(cases), records, corpus=CORPUS)
    failed = [
        row for row in result.recommendations if row["end_to_end"]["recommendation_failed"]
    ]
    assert failed
    assert all(row["end_to_end"]["primary_category"] in PRIMARY_CATEGORIES for row in failed)
    assert result.aggregates["systems"]["P2"]["family_count"] == 2
    extraction = result.aggregates["systems"]["P2"]["extraction"]
    assert extraction["overall_f1"] is None
    assert extraction["overall_f1_prohibited"] is True
    assert extraction["gpu_semantics_confusion"]["counts"] == {
        "unspecified->unspecified": 2.0
    }
    assert extraction["minimum_cpu_cores_outcomes"]["counts"] == {
        "correct_absent": 2.0
    }


def test_per_family_summary_collapses_repeats_before_variants():
    cases = [_case("repeat-a", "repeat-family"), _case("repeat-b", "repeat-family")]
    records = [
        _record(
            cases[0],
            repeat_index=0,
            final=("small-scipy-data-science", "medium-scipy-data-science"),
            predicted="small-scipy-data-science",
        ),
        _record(cases[0], repeat_index=1),
        _record(cases[1], repeat_index=0),
        _record(cases[1], repeat_index=1),
    ]
    result = score_component_records(_source(cases), records, corpus=CORPUS)
    family = result.families[0]
    assert family["query_correctness"] == 0.75
    assert family["family_weighted_diagnostics"]["recommendation_failure_rate"] == 0.25
    assert family["variant_count"] == 2
    assert family["variant_summaries"][0]["repeat_count"] == 2
    assert family["variant_summaries"][0]["repeat_indices"] == [0, 1]


def test_p3_headroom_gate_uses_family_counts_and_is_configurable():
    families = [
        {"system_id": "P2", "family_id": "r1", "primary_category": "RANKING_ERROR", "acceptable_feasible_in_top_k": True},
        {"system_id": "P2", "family_id": "r2", "primary_category": "RANKING_ERROR", "acceptable_feasible_in_top_k": True},
        {"system_id": "P2", "family_id": "e1", "primary_category": "EXTRACTION_ERROR", "acceptable_feasible_in_top_k": True},
        {"system_id": "P2", "family_id": "ok", "primary_category": None, "acceptable_feasible_in_top_k": True},
    ]
    report = p3_headroom_report(
        families,
        role="development",
        minimum_count=1,
        minimum_fraction=0.25,
    )
    assert report["p2_error_family_count"] == 3
    assert report["ranking_error_family_count"] == 2
    assert report["ranking_error_fraction_of_p2_errors"] == pytest.approx(2 / 3)
    assert report["eligible_family_count"] == 4
    assert report["criterion_met"] is True
    assert report["backend_changed"] is False


def test_duplicate_repeats_do_not_change_family_level_p3_gate():
    cases = [
        _case(f"gate-{index}", f"gate-family-{index}")
        for index in range(4)
    ]

    def result_for_repeats(repeat_count):
        records = []
        for repeat_index in range(repeat_count):
            for index, case in enumerate(cases):
                if index == 0:
                    records.append(
                        _record(
                            case,
                            repeat_index=repeat_index,
                            final=(
                                "small-scipy-data-science",
                                "medium-scipy-data-science",
                            ),
                            predicted="small-scipy-data-science",
                        )
                    )
                else:
                    records.append(_record(case, repeat_index=repeat_index))
        return score_component_records(
            _source(cases),
            records,
            corpus=CORPUS,
            gate_minimum_count=1,
            gate_minimum_fraction=0.25,
        ).p3_headroom

    one_repeat = result_for_repeats(1)
    ten_repeats = result_for_repeats(10)
    for field in (
        "p2_error_family_count",
        "ranking_error_family_count",
        "eligible_family_count",
        "ranking_error_fraction_of_eligible_families",
        "criterion_met",
        "advisory_decision",
    ):
        assert ten_repeats[field] == one_repeat[field]
    assert one_repeat["eligible_family_count"] == 4
    assert one_repeat["ranking_error_family_count"] == 1
    assert one_repeat["ranking_error_fraction_of_eligible_families"] == 0.25


def test_p3_headroom_is_not_evaluated_on_confirmatory_data():
    report = p3_headroom_report([], role="confirmatory")
    assert report["status"] == "NOT_APPLICABLE_CONFIRMATORY"
    assert report["backend_changed"] is False


def test_p3_headroom_is_not_computed_from_zero_p2_families():
    report = p3_headroom_report([], role="development")
    assert report == {
        "schema_version": "protocol-v5-p3-headroom-gate-v1.0.0",
        "status": "NOT_EXECUTED",
        "reason_code": "NO_COMPLETE_P2_FAMILY_ROWS",
        "advisory_only": True,
        "backend_changed": False,
    }
    assert not _numeric_json_values(report)


def test_gold_raw_join_rejects_incomplete_coverage_and_catalog_drift():
    case = _case("joined", "joined")
    gold = _source([case])
    record = {
        "case_id": case.case_id,
        "family_id": case.family_id,
        "variant_id": case.variant_id,
        "input_identity": {"prompt_sha256": "0" * 64, "case_sha256": "0" * 64},
    }
    provenance = {
        "candidate_catalog": {
            "corpus_version": CORPUS.corpus_version,
            "corpus_sha256": CORPUS.corpus_checksum,
            "catalog_version": CORPUS.source_image_catalog_version,
            "catalog_sha256": CORPUS.source_image_catalog_checksum,
        }
    }
    with pytest.raises(ComponentAnalysisError, match="prompt"):
        _validate_gold_evidence_join(gold, provenance, [record])
    with pytest.raises(ComponentAnalysisError, match="coverage"):
        _validate_gold_evidence_join(gold, provenance, [])


def test_not_executed_status_is_explicit_and_non_overwriting(tmp_path: Path):
    output = tmp_path / "not-executed"
    write_not_executed(output, reason="Real Prompt-3/Prompt-5 inputs unavailable")
    manifest = json.loads((output / "analysis-manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == COMPONENT_ANALYSIS_SCHEMA_VERSION
    assert manifest["status"] == "NOT_EXECUTED"
    assert manifest["claims_permitted"] is False
    assert manifest["outputs"] == {}
    assert manifest["p3_headroom_gate_status"] == "NOT_EXECUTED"
    assert validate_analysis_package(output)["analysis_status"] == "NOT_EXECUTED"
    assert {item.name for item in output.iterdir()} == {"analysis-manifest.json"}
    assert not _numeric_json_values(manifest)
    with pytest.raises(FileExistsError):
        write_not_executed(output, reason="must not overwrite")


def _numeric_json_values(value):
    if isinstance(value, dict):
        return [item for nested in value.values() for item in _numeric_json_values(nested)]
    if isinstance(value, list):
        return [item for nested in value for item in _numeric_json_values(nested)]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [value]
    return []


def _assert_not_executed_without_metrics(output: Path):
    validation = validate_analysis_package(output)
    assert validation["analysis_status"] == "NOT_EXECUTED"
    assert validation["outputs_validated"] == 0
    assert {item.name for item in output.iterdir()} == {"analysis-manifest.json"}
    manifest = json.loads(
        (output / "analysis-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["p3_headroom_gate_status"] == "NOT_EXECUTED"
    assert manifest["outputs"] == {}
    assert not _numeric_json_values(manifest)
    serialized = json.dumps(manifest, sort_keys=True).casefold()
    for empirical_key in (
        "recall_at_5",
        '"mrr"',
        "extraction_f1",
        "constraint_violation_rate",
        "ranking_error_fraction",
        "criterion_met",
        "advisory_decision",
    ):
        assert empirical_key not in serialized


def test_status_only_cli_has_no_partial_metrics_or_gate_decision(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    output = tmp_path / "status-only"
    assert main(["--status-only", "--output-dir", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_EXECUTED"
    _assert_not_executed_without_metrics(output)


def test_missing_raw_or_complete_gold_is_not_executed_at_api_and_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    authoring_path, _, evidence_dir, _, _ = _write_cli_inputs(inputs)

    missing_raw_output = tmp_path / "missing-raw"
    analyze_component_evidence(
        tmp_path / "absent-evidence",
        authoring_path,
        missing_raw_output,
    )
    _assert_not_executed_without_metrics(missing_raw_output)

    missing_gold_output = tmp_path / "missing-gold"
    assert main(
        [
            "--evidence-dir",
            str(evidence_dir),
            "--gold-dataset",
            str(tmp_path / "absent-gold.json"),
            "--output-dir",
            str(missing_gold_output),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_EXECUTED"
    _assert_not_executed_without_metrics(missing_gold_output)

    omitted_output = tmp_path / "omitted-inputs"
    assert main(["--output-dir", str(omitted_output)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_EXECUTED"
    _assert_not_executed_without_metrics(omitted_output)


def test_incomplete_raw_gold_or_mixed_partial_sources_cannot_bypass_fail_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    _, compiled_path, evidence_dir, _, _ = _write_cli_inputs(
        inputs, include_second_variant=True
    )

    incomplete_evidence = tmp_path / "incomplete-evidence"
    shutil.copytree(evidence_dir, incomplete_evidence)
    records_path = incomplete_evidence / "raw" / "recommendations.jsonl"
    records = records_path.read_text(encoding="utf-8").splitlines()
    records_path.write_text(records[0] + "\n", encoding="utf-8")
    incomplete_raw_output = tmp_path / "incomplete-raw-output"
    analyze_component_evidence(
        incomplete_evidence,
        compiled_path,
        incomplete_raw_output,
    )
    _assert_not_executed_without_metrics(incomplete_raw_output)

    partial_gold = tmp_path / "partial-family-gold.json"
    write_document_exclusive(partial_gold, _compiled_v2_document())
    incomplete_gold_output = tmp_path / "incomplete-gold-output"
    assert main(
        [
            "--evidence-dir",
            str(evidence_dir),
            "--gold-dataset",
            str(partial_gold),
            "--output-dir",
            str(incomplete_gold_output),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_EXECUTED"
    _assert_not_executed_without_metrics(incomplete_gold_output)

    mixed_partial_output = tmp_path / "mixed-partial-output"
    analyze_component_evidence(
        incomplete_evidence,
        partial_gold,
        mixed_partial_output,
    )
    _assert_not_executed_without_metrics(mixed_partial_output)


def test_empty_or_partial_direct_scoring_is_rejected_before_metrics():
    cases = [_case("coverage-a", "coverage"), _case("coverage-b", "coverage")]
    with pytest.raises(ComponentAnalysisError, match="complete raw evidence"):
        score_component_records(_source(cases), [], corpus=CORPUS)
    with pytest.raises(ComponentAnalysisError, match="exactly cover"):
        score_component_records(
            _source(cases), [_record(cases[0])], corpus=CORPUS
        )


def test_analysis_package_records_checksums_and_does_not_mutate_raw(tmp_path: Path):
    case = _case("package", "package")
    record = _record(case)
    gold = _source([case])
    result = score_component_records(gold, [record], corpus=CORPUS)
    evidence = tmp_path / "evidence"
    raw = evidence / "raw"
    raw.mkdir(parents=True)
    raw_path = raw / "recommendations.jsonl"
    raw_bytes = b'{"preserved":true}\n'
    raw_path.write_bytes(raw_bytes)
    output = tmp_path / "analysis"
    write_analysis_package(
        output,
        result=result,
        gold=gold,
        evidence_dir=evidence,
        provenance={
            "run_id": "synthetic",
            "provenance_fingerprint": "f" * 64,
            "systems": ["P2"],
            "system_frozen_provenance": {"P2": {"version": "fixture"}},
            "candidate_catalog": {"corpus_sha256": CORPUS.corpus_checksum},
        },
        retrieval_ks=(1, 3, 5),
        gate_minimum_count=3,
        gate_minimum_fraction=0.05,
    )
    manifest = json.loads((output / "analysis-manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "DERIVED_EVIDENCE_COMPLETE"
    assert set(manifest["outputs"]) == {
        "aggregates",
        "per_recommendation",
        "per_family",
        "p3_headroom_gate",
    }
    validation = validate_analysis_package(output)
    assert validation["status"] == "PASS"
    assert validation["outputs_validated"] == 4
    assert raw_path.read_bytes() == raw_bytes
    with pytest.raises(FileExistsError):
        write_analysis_package(
            output,
            result=result,
            gold=gold,
            evidence_dir=evidence,
            provenance={},
            retrieval_ks=(1, 3, 5),
            gate_minimum_count=3,
            gate_minimum_fraction=0.05,
        )


def test_analysis_package_validator_rejects_checksum_drift(tmp_path: Path):
    case = _case("drift", "drift")
    gold = _source([case])
    result = score_component_records(gold, [_record(case)], corpus=CORPUS)
    evidence = tmp_path / "evidence"
    (evidence / "raw").mkdir(parents=True)
    (evidence / "raw" / "recommendations.jsonl").write_text("{}\n", encoding="utf-8")
    output = tmp_path / "analysis"
    write_analysis_package(
        output,
        result=result,
        gold=gold,
        evidence_dir=evidence,
        provenance={"systems": ["P2"]},
        retrieval_ks=(1, 3, 5),
        gate_minimum_count=3,
        gate_minimum_fraction=0.05,
    )
    with (output / "aggregates.json").open("a", encoding="utf-8") as handle:
        handle.write(" ")
    with pytest.raises(ComponentAnalysisError, match="checksum"):
        validate_analysis_package(output)


def test_compiled_v2_prompt5_evidence_runs_through_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    authoring_path, gold_path, evidence_dir, split, _ = _write_cli_inputs(
        tmp_path
    )
    assert validate_offline_evidence(evidence_dir, split=split)["status"] == "PASS"

    output_dir = tmp_path / "component-analysis"
    assert main(
        [
            "--evidence-dir",
            str(evidence_dir),
            "--gold-dataset",
            str(gold_path),
            "--output-dir",
            str(output_dir),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == (
        "DERIVED_EVIDENCE_COMPLETE"
    )
    validation = validate_analysis_package(output_dir)
    assert validation == {
        "schema_version": COMPONENT_ANALYSIS_SCHEMA_VERSION,
        "status": "PASS",
        "analysis_status": "DERIVED_EVIDENCE_COMPLETE",
        "outputs_validated": 4,
        "recommendations_validated": 1,
        "families_validated": 1,
    }

    family_output_dir = tmp_path / "component-analysis-from-family"
    assert main(
        [
            "--evidence-dir",
            str(evidence_dir),
            "--gold-dataset",
            str(authoring_path),
            "--output-dir",
            str(family_output_dir),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == (
        "DERIVED_EVIDENCE_COMPLETE"
    )
    assert validate_analysis_package(family_output_dir)["status"] == "PASS"


def test_validated_evidence_loader_retains_requested_or_all_systems(
    tmp_path: Path,
):
    _, gold_path, evidence_dir, _, _ = _write_cli_inputs(
        tmp_path,
        system_ids=("P1", "P2"),
    )
    gold = load_component_gold(gold_path)

    provenance, all_records = load_validated_evidence(
        evidence_dir,
        gold,
        systems=None,
        require_systems=False,
    )
    assert provenance["systems"] == ["P1", "P2"]
    assert {record["system_id"] for record in all_records} == {"P1", "P2"}

    _, p1_records = load_validated_evidence(
        evidence_dir,
        gold,
        systems=("P1",),
    )
    assert {record["system_id"] for record in p1_records} == {"P1"}


    with pytest.raises(ComponentAnalysisError, match="missing requested system"):
        load_validated_evidence(evidence_dir, gold, systems=("P3",))

    _, component_records = load_component_evidence(evidence_dir, gold)
    assert {record["system_id"] for record in component_records} == {"P2"}

    from evaluation_v5.analysis.statistical_analysis import (
        analyze_statistical_evidence,
        validate_statistical_package,
    )

    statistical_output = tmp_path / "family-statistics"
    analyze_statistical_evidence(
        evidence_dir,
        gold_path,
        statistical_output,
        bootstrap_replicates=20,
    )
    statistical_manifest = json.loads(
        (statistical_output / "analysis-manifest.json").read_text(encoding="utf-8")
    )
    assert statistical_manifest["status"] == "DERIVED_EVIDENCE_COMPLETE"
    assert statistical_manifest["systems"] == ["P1", "P2"]
    assert statistical_manifest["source"]["gold_catalog_identity"] == dict(
        gold.catalog_identity
    )
    assert statistical_manifest["evidence_validation"][
        "raw_completion_verified_before_external_gold_load"
    ] is True
    assert validate_statistical_package(statistical_output)["status"] == "PASS"


def test_confirmatory_gold_carries_frozen_p3_gate_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    document = _compiled_v2_document()
    bundle = compile_gold_dataset(validate_gold_dataset(document))
    compiled = tmp_path / "synthetic-confirmatory.json"
    write_document_exclusive(compiled, bundle.to_dict())
    split = _read_split_bundle(
        compiled,
        expected_role=SplitRole.DEVELOPMENT,
        expected_split_id="v5-development",
    )
    freeze = tmp_path / "synthetic-freeze.json"
    freeze.write_text("{}\n", encoding="utf-8")
    fake_load = SimpleNamespace(
        split=split,
        freeze_manifest={
            "freeze_id": "synthetic-freeze",
            "created_at_utc": "2026-08-24T00:00:00Z",
            "configuration_snapshot": {
                "p3_gate": {
                    "status": "not_retained",
                    "p3_active": False,
                    "snapshot_version": "protocol-v5-p3-gate-snapshot-v1",
                    "evidence_sha256": "a" * 64,
                }
            },
        },
    )
    monkeypatch.setattr(
        component_scoring_module,
        "load_confirmatory_split",
        lambda *args, **kwargs: fake_load,
    )
    gold = load_component_gold(
        compiled,
        role="confirmatory",
        freeze_path=freeze,
        split_id="synthetic-confirmatory",
    )
    assert gold.p3_gate_identity == {
        "status": "not_retained",
        "p3_active": False,
        "snapshot_version": "protocol-v5-p3-gate-snapshot-v1",
        "evidence_sha256": "a" * 64,
        "source": "authoritative_protocol_v5_freeze",
    }
