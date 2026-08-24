"""Synthetic, non-evidentiary tests for Protocol-v5 family statistics."""

from __future__ import annotations

import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation_v5.analysis.component_scoring import GoldSource
from evaluation_v5.analysis.statistical_analysis import (
    StatisticalAnalysisError,
    analyze_statistical_evidence,
    analyze_statistical_records,
    validate_statistical_package,
    write_analysis_package,
    write_not_executed,
)
from evaluation_v5.analysis.statistics import (
    ELIGIBLE,
    INSUFFICIENT_EFFECTIVE_FAMILY_N,
    SMALL_EFFECTIVE_FAMILY_N,
    WITHHELD_SMALL_N,
    derive_bootstrap_seed,
    family_bootstrap_ci,
    family_n_warnings,
    holm_adjust,
    inference_eligibility,
    paired_effect_sizes,
    paired_family_bootstrap_ci,
    paired_test,
    statistical_decision,
)
from evaluation_v5.offline.runner import (
    COMPLETION_FILENAME,
    PROVENANCE_FILENAME,
    RAW_DIRECTORY_NAME,
    RECORDS_FILENAME,
    REPORT_DIRECTORY_NAME,
)
from evaluation_v5.split_dataset import (
    SPLIT_BUNDLE_SCHEMA_VERSION,
    SPLIT_BUNDLE_SCHEMA_VERSION_V2,
)
from recommender.candidate_corpus import load_candidate_corpus


CORPUS = load_candidate_corpus()
GOOD_CANDIDATE = "medium-scipy-data-science"
OTHER_ACCEPTABLE_CANDIDATE = "large-scipy-data-science"
CONSTRAINT_VIOLATING_CANDIDATE = "small-minimal-python"


def _case(
    family_id: str,
    variant_id: str,
    *,
    variant_class: str = "canonical_en",
    workload_stratum: str = "data_processing",
    equivalence_status: str | None = None,
):
    if equivalence_status is None:
        equivalence_status = (
            "canonical_reference"
            if variant_class == "canonical_en"
            else "reviewed_equivalent"
        )
    language = "vi" if variant_class == "vietnamese" else "en"
    return SimpleNamespace(
        case_id=f"{family_id}-{variant_id}",
        family_id=family_id,
        variant_id=variant_id,
        language=language,
        family_metadata={"workload_stratum": workload_stratum},
        variant_metadata={
            "variant_class": variant_class,
            "equivalence_status": equivalence_status,
        },
        gold={
            "gold_structured_intent": {
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
            },
            "candidate_gold": {
                "preferred_candidate_ids": [GOOD_CANDIDATE],
                "acceptable_candidate_ids": [
                    GOOD_CANDIDATE,
                    OTHER_ACCEPTABLE_CANDIDATE,
                ],
            },
            "profile_gold": {
                "preferred_profile_ids": ["medium"],
                "acceptable_profile_ids": ["medium", "large"],
            },
            "image_gold": {
                "preferred_image_ids": ["scipy-data-science"],
                "acceptable_image_ids": ["scipy-data-science"],
                "required_capabilities": ["pandas"],
            },
            "policy_gold": {
                "required_constraints": [],
                "explicitly_unsupported_requirements": [],
                "expected_feasibility": "feasible",
            },
        },
    )


def _gold(cases, *, schema_version: str = SPLIT_BUNDLE_SCHEMA_VERSION_V2):
    split = SimpleNamespace(
        bundle=SimpleNamespace(
            schema_version=schema_version,
            cases=tuple(cases),
        ),
        manifest=SimpleNamespace(
            split_id="synthetic-development",
            checksum="4" * 64,
        ),
    )
    return GoldSource(
        role="development",
        dataset_id="synthetic-statistics-fixture",
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
        cases=(),
        split=split,
        freeze_identity={"synthetic_fixture": True},
    )


def _ranked(candidate_ids):
    return [
        {"candidate_id": candidate_id, "rank": rank, "score": 1.0 / rank}
        for rank, candidate_id in enumerate(candidate_ids, start=1)
    ]


def _record(
    case,
    system: str,
    *,
    success: bool = True,
    repeat_index: int = 0,
    latency: float = 1.0,
    candidate_id: str | None = None,
    status: str = "completed",
):
    if candidate_id is None and status == "completed":
        candidate_id = GOOD_CANDIDATE if success else CONSTRAINT_VIOLATING_CANDIDATE
    profile_id = "medium" if success else "small"
    image_id = "scipy-data-science" if success else "minimal-python"
    ranked_ids = (
        [GOOD_CANDIDATE, OTHER_ACCEPTABLE_CANDIDATE]
        if success
        else [CONSTRAINT_VIOLATING_CANDIDATE, GOOD_CANDIDATE]
    )
    return {
        "record_id": f"{case.case_id}-{system}-{repeat_index}",
        "run_id": "synthetic-statistics-run",
        "case_id": case.case_id,
        "family_id": case.family_id,
        "variant_id": case.variant_id,
        "system_id": system,
        "repeat_index": repeat_index,
        "seed": repeat_index + 100,
        "status": status,
        "predicted_candidate_id": candidate_id,
        "predicted_profile_id": profile_id if candidate_id is not None else None,
        "predicted_image_id": image_id if candidate_id is not None else None,
        "candidate_top_k": _ranked(ranked_ids),
        "constraint_summary": {
            "no_feasible_candidate": False,
            "unsupported_constraints": [],
        },
        "metric_inputs": {"infeasible_request_signal": False},
        "structured_intent": {"ambiguities": []},
        "latency_components": {"total_elapsed_seconds": latency},
    }


def _analyze(cases, records, *, seed=20260824, replicates=80):
    return analyze_statistical_records(
        _gold(cases),
        records,
        bootstrap_seed=seed,
        bootstrap_replicates=replicates,
        corpus=CORPUS,
    )


def _system_row(result, system: str, endpoint: str):
    return next(
        row
        for row in result.system_estimates
        if row["system_id"] == system and row["endpoint"] == endpoint
    )


def _comparison_row(result, comparison_id: str, endpoint: str):
    return next(
        row
        for row in result.paired_comparisons
        if row["comparison_id"] == comparison_id and row["endpoint"] == endpoint
    )


def _stratum_row(result, system: str, endpoint: str, dimension: str, value: str):
    return next(
        row
        for row in result.stratified_estimates
        if row["system_id"] == system
        and row["endpoint"] == endpoint
        and row["dimension"] == dimension
        and row["value"] == value
    )


def test_known_paired_methods_holm_effects_and_constant_bootstrap_cis():
    first_binary = [0] * 10
    second_binary = [1] * 10
    mcnemar = paired_test(first_binary, second_binary, binary_outcome=True)
    assert mcnemar["test_method"] == "exact_mcnemar"
    assert mcnemar["first_only_successes"] == 0
    assert mcnemar["second_only_successes"] == 10
    assert mcnemar["p_value_raw"] == pytest.approx(0.001953125)

    wilcoxon = paired_test(
        [0.0, 0.0, 0.0, 0.0],
        [0.1, 0.2, 0.3, 0.4],
        binary_outcome=False,
    )
    assert wilcoxon["test_method"] == "wilcoxon_signed_rank"
    assert wilcoxon["w_positive"] == 10.0
    assert wilcoxon["w_negative"] == 0.0
    assert wilcoxon["p_value_raw"] == pytest.approx(0.125)

    effects = paired_effect_sizes([0, 0, 0, 0], [1, 0, 1, 0])
    assert effects["mean_difference"] == pytest.approx(0.5)
    assert effects["risk_difference"] == pytest.approx(0.5)
    assert effects["median_paired_difference"] == pytest.approx(0.5)
    assert effects["matched_pairs_rank_biserial"] == pytest.approx(1.0)
    assert effects["cohens_dz"] == pytest.approx(3**0.5 / 2)

    assert holm_adjust([0.01, 0.04, 0.2]) == pytest.approx([0.03, 0.08, 0.2])
    constant = [{"family_id": str(index), "value": 0.5} for index in range(5)]
    assert family_bootstrap_ci(constant, "value", replicates=40, seed=7) == (0.5, 0.5)
    paired = [
        {"family_id": str(index), "first": index / 10, "second": index / 10 + 0.25}
        for index in range(5)
    ]
    assert paired_family_bootstrap_ci(
        paired, "first", "second", replicates=40, seed=7
    ) == pytest.approx((0.25, 0.25))


def test_bootstrap_enforces_one_family_row_and_is_order_independent():
    rows = [
        {"family_id": "c", "value": 1.0},
        {"family_id": "a", "value": 0.0},
        {"family_id": "b", "value": 0.5},
    ]
    assert family_bootstrap_ci(rows, "value", replicates=200, seed=91) == (
        family_bootstrap_ci(list(reversed(rows)), "value", replicates=200, seed=91)
    )
    with pytest.raises(ValueError, match="duplicate family"):
        family_bootstrap_ci(
            [{"family_id": "same", "value": 0}, {"family_id": "same", "value": 1}],
            "value",
        )


def test_seed_derivation_is_stable_and_namespaced():
    expected = derive_bootstrap_seed(20260824, "comparison", "P2_minus_P1")
    assert expected == 4720244408692817407
    assert derive_bootstrap_seed(20260824, "comparison", "P2_minus_P1") == expected
    assert derive_bootstrap_seed(20260824, "comparison", "P3_minus_P2") != expected


def test_equal_family_weighting_ignores_unequal_variant_exposure():
    cases = [
        _case("many", f"v{index}", variant_class=("canonical_en" if index == 0 else "paraphrase_en"))
        for index in range(4)
    ] + [_case("single", "v0")]
    records = []
    for system in ("P1", "P2"):
        for case in cases:
            records.append(_record(case, system, success=case.family_id == "many"))
    result = _analyze(cases, records)
    row = _system_row(result, "P2", "joint_accept_at_1")
    assert row["estimate"] == pytest.approx(0.5)
    assert row["effective_family_n"] == 2
    assert row["variant_count"] == 5
    families = {
        row["family_id"]: row
        for row in result.family_estimates
        if row["system_id"] == "P2"
    }
    assert families["many"]["values"]["joint_accept_at_1"] == 1.0
    assert families["single"]["values"]["joint_accept_at_1"] == 0.0


def test_duplicate_calls_change_runtime_diagnostics_not_accuracy_sample_size():
    cases = [_case("family-a", "canonical"), _case("family-b", "canonical")]
    once = [
        _record(case, system, success=case.family_id == "family-a", latency=1.0)
        for system in ("P1", "P2")
        for case in cases
    ]
    repeated = list(once)
    repeated.extend(
        _record(
            case,
            system,
            success=case.family_id == "family-a",
            repeat_index=1,
            latency=3.0,
        )
        for system in ("P1", "P2")
        for case in cases
    )
    one_result = _analyze(cases, once)
    repeated_result = _analyze(cases, repeated)
    for endpoint in ("joint_accept_at_1", "profile_acceptable_accuracy"):
        one = _system_row(one_result, "P2", endpoint)
        many = _system_row(repeated_result, "P2", endpoint)
        assert many["estimate"] == one["estimate"]
        assert many["effective_family_n"] == one["effective_family_n"] == 2
        assert many["execution_count"] == 4
        assert one["execution_count"] == 2
    repeated_family = next(
        row
        for row in repeated_result.family_estimates
        if row["system_id"] == "P2" and row["family_id"] == "family-a"
    )
    assert repeated_family["latency_execution_distribution"]["count"] == 2
    assert repeated_family["latency_execution_distribution"]["minimum"] == 1.0
    assert repeated_family["latency_execution_distribution"]["maximum"] == 3.0


def test_record_input_order_does_not_change_estimates_or_effective_seeds():
    cases = [_case(f"family-{index}", "canonical") for index in range(3)]
    records = [
        _record(case, system, success=(system == "P2" or index == 0), latency=index + 1)
        for index, case in enumerate(cases)
        for system in ("P1", "P2")
    ]
    shuffled = list(records)
    random.Random(991).shuffle(shuffled)
    first = _analyze(cases, records, seed=1234)
    second = _analyze(cases, shuffled, seed=1234)
    assert second == first


def test_primary_comparison_and_optional_p3_are_separate():
    cases = [_case(f"family-{index}", "canonical") for index in range(3)]
    p1_p2 = [
        _record(case, system, success=(system == "P2" or index == 0))
        for index, case in enumerate(cases)
        for system in ("P1", "P2")
    ]
    without_p3 = _analyze(cases, p1_p2)
    assert {row["comparison_id"] for row in without_p3.paired_comparisons} == {
        "P2_minus_P1"
    }
    with_p3 = _analyze(
        cases,
        p1_p2 + [_record(case, "P3", success=True) for case in cases],
    )
    assert {row["comparison_id"] for row in with_p3.paired_comparisons} == {
        "P2_minus_P1",
        "P3_minus_P2",
    }
    assert _comparison_row(
        with_p3, "P2_minus_P1", "joint_accept_at_1"
    )["multiplicity_family"] is None


def test_fractional_family_outcome_uses_wilcoxon_without_thresholding():
    cases = [
        _case("fractional-a", "canonical"),
        _case("fractional-a", "paraphrase", variant_class="paraphrase_en"),
        _case("fractional-b", "canonical"),
        _case("fractional-b", "paraphrase", variant_class="paraphrase_en"),
    ]
    records = []
    for case in cases:
        records.append(_record(case, "P1", success=case.variant_id == "canonical"))
        records.append(_record(case, "P2", success=True))
    result = _analyze(cases, records)
    comparison = _comparison_row(result, "P2_minus_P1", "joint_accept_at_1")
    assert comparison["test_method"] == "wilcoxon_signed_rank"
    assert comparison["effect_sizes"]["mean_difference"] == pytest.approx(0.5)
    assert comparison["effect_sizes"]["risk_difference"] is None


def test_unanimous_repeated_calls_remain_rates_and_use_wilcoxon():
    cases = [_case(f"repeated-{index}", "canonical") for index in range(3)]
    records = [
        _record(
            case,
            system,
            success=system == "P2",
            repeat_index=repeat_index,
        )
        for case in cases
        for system in ("P1", "P2")
        for repeat_index in (0, 1)
    ]
    result = _analyze(cases, records)
    comparison = _comparison_row(result, "P2_minus_P1", "joint_accept_at_1")
    assert comparison["test_method"] == "wilcoxon_signed_rank"
    assert comparison["test_details"]["binary_outcome"] is False
    assert comparison["effective_family_n"] == 3
    assert comparison["effect_sizes"]["risk_difference"] is None

    single_family_records = [
        record for record in records if record["family_id"] == cases[0].family_id
    ]
    single = _comparison_row(
        _analyze(cases[:1], single_family_records),
        "P2_minus_P1",
        "joint_accept_at_1",
    )
    assert single["effective_family_n"] == 1
    assert single["effect_sizes"]["risk_difference"] is None


def test_p1_retrieval_and_absent_strata_are_not_zero_filled():
    cases = [_case("family", "canonical")]
    records = [_record(cases[0], system) for system in ("P1", "P2")]
    result = _analyze(cases, records)
    retrieval = _system_row(result, "P1", "retrieval_hit_at_1")
    assert retrieval["applicability"] == "NOT_APPLICABLE"
    assert retrieval["estimate"] is None
    absent = _stratum_row(
        result, "P2", "joint_accept_at_1", "variant_stratum", "noisy"
    )
    assert absent["applicability"] == "NOT_AVAILABLE"
    assert absent["estimate"] is None
    p1_retrieval_stratum = _stratum_row(
        result, "P1", "retrieval_hit_at_1", "variant_stratum", "canonical"
    )
    assert p1_retrieval_stratum["applicability"] == "NOT_APPLICABLE"
    assert p1_retrieval_stratum["estimate"] is None


def test_all_variant_and_workload_strata_receive_descriptive_rows():
    classes = (
        "canonical_en",
        "paraphrase_en",
        "vietnamese",
        "informal_or_noisy",
        "optional_code_context",
    )
    cases = [
        _case(
            f"family-{index}",
            f"variant-{index}",
            variant_class=variant_class,
            workload_stratum=("data_processing" if index < 3 else "deep_learning"),
        )
        for index, variant_class in enumerate(classes)
    ]
    records = [_record(case, system) for case in cases for system in ("P1", "P2")]
    result = _analyze(cases, records)
    for value in ("canonical", "paraphrase", "vietnamese", "noisy", "code_centric"):
        row = _stratum_row(
            result, "P2", "joint_accept_at_1", "variant_stratum", value
        )
        assert row["applicability"] == "AVAILABLE"
        assert row["estimate"] == 1.0
        assert row["hypothesis_tested"] is False
    for value, expected_n in (("data_processing", 3), ("deep_learning", 2)):
        row = _stratum_row(
            result, "P2", "joint_accept_at_1", "workload_stratum", value
        )
        assert row["applicability"] == "AVAILABLE"
        assert row["effective_family_n"] == expected_n


def test_frozen_catalog_oracle_scores_unknown_and_violating_candidates():
    cases = [_case("oracle", "canonical")]
    records = [
        _record(cases[0], "P1", success=False, candidate_id="unknown-candidate"),
        _record(
            cases[0],
            "P2",
            success=False,
            candidate_id=CONSTRAINT_VIOLATING_CANDIDATE,
        ),
        _record(cases[0], "P3", success=False, candidate_id=None, status="error"),
    ]
    result = _analyze(cases, records)
    family_rows = {row["system_id"]: row for row in result.family_estimates}
    assert family_rows["P1"]["values"]["hard_constraint_violation_rate"] == 1.0
    assert family_rows["P2"]["values"]["hard_constraint_violation_rate"] == 1.0
    assert family_rows["P3"]["values"]["hard_constraint_violation_rate"] is None
    assert family_rows["P3"]["values"]["selection_coverage"] == 0.0
    assert family_rows["P3"]["values"]["joint_accept_at_1"] == 0.0


def test_small_family_n_boundaries_warn_and_withhold_claims():
    assert family_n_warnings(1) == [
        INSUFFICIENT_EFFECTIVE_FAMILY_N,
        SMALL_EFFECTIVE_FAMILY_N,
    ]
    assert family_n_warnings(19) == [SMALL_EFFECTIVE_FAMILY_N]
    assert family_n_warnings(20) == []
    assert inference_eligibility(1)["ci_eligible"] is False
    assert inference_eligibility(9)["inference_status"] == WITHHELD_SMALL_N
    assert inference_eligibility(10)["inference_status"] == ELIGIBLE
    assert statistical_decision(0.001, 9) == WITHHELD_SMALL_N
    assert statistical_decision(0.001, 10) == "REJECT_NULL"
    assert family_bootstrap_ci(
        [{"family_id": "only", "value": 1.0}], "value", replicates=10
    ) == (None, None)


def test_small_n_analyzer_keeps_effects_and_p_values_but_withholds_decision():
    cases = [_case(f"family-{index}", "canonical") for index in range(3)]
    records = [
        _record(case, system, success=(system == "P2"))
        for case in cases
        for system in ("P1", "P2")
    ]
    result = _analyze(cases, records)
    row = _comparison_row(result, "P2_minus_P1", "joint_accept_at_1")
    assert row["effective_family_n"] == 3
    assert row["p_value_raw"] is not None
    assert row["effect_sizes"]["mean_difference"] == 1.0
    assert row["effect_ci_low"] == row["effect_ci_high"] == 1.0
    assert row["statistical_decision"] == WITHHELD_SMALL_N
    assert SMALL_EFFECTIVE_FAMILY_N in row["warning_codes"]


def test_v1_metadata_fails_closed_and_invalid_inputs_emit_not_executed(tmp_path: Path):
    cases = [_case("family", "canonical")]
    records = [_record(cases[0], system) for system in ("P1", "P2")]
    with pytest.raises(StatisticalAnalysisError, match="split v2 metadata"):
        analyze_statistical_records(
            _gold(cases, schema_version=SPLIT_BUNDLE_SCHEMA_VERSION),
            records,
            bootstrap_replicates=10,
            corpus=CORPUS,
        )

    output = tmp_path / "invalid-evidence-status"
    analyze_statistical_evidence(
        tmp_path / "missing-evidence",
        tmp_path / "missing-gold.json",
        output,
        bootstrap_replicates=10,
    )
    manifest = json.loads((output / "analysis-manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "NOT_EXECUTED"
    assert manifest["claims_permitted"] is False
    assert manifest["reason_code"] == "INPUTS_UNAVAILABLE_OR_INVALID"
    assert {path.name for path in output.iterdir()} == {"analysis-manifest.json"}
    assert validate_statistical_package(output)["analysis_status"] == "NOT_EXECUTED"


def test_package_writes_are_exclusive_and_checksum_tampering_is_detected(tmp_path: Path):
    status_dir = tmp_path / "status"
    write_not_executed(status_dir, reason="Synthetic fixture only")
    with pytest.raises(FileExistsError):
        write_not_executed(status_dir, reason="Must not overwrite")

    cases = [_case("family-a", "canonical"), _case("family-b", "canonical")]
    records = [_record(case, system) for case in cases for system in ("P1", "P2")]
    gold = _gold(cases)
    result = analyze_statistical_records(
        gold,
        records,
        bootstrap_replicates=20,
        corpus=CORPUS,
    )
    evidence_dir = tmp_path / "synthetic-evidence"
    raw_dir = evidence_dir / RAW_DIRECTORY_NAME
    raw_dir.mkdir(parents=True)
    (raw_dir / RECORDS_FILENAME).write_text("{}\n", encoding="utf-8")
    (raw_dir / PROVENANCE_FILENAME).write_text("{}\n", encoding="utf-8")
    report_dir = evidence_dir / REPORT_DIRECTORY_NAME
    report_dir.mkdir()
    (report_dir / COMPLETION_FILENAME).write_text("{}\n", encoding="utf-8")
    package_dir = tmp_path / "statistics-package"
    write_analysis_package(
        package_dir,
        result=result,
        gold=gold,
        evidence_dir=evidence_dir,
        provenance={
            "run_id": "synthetic-run",
            "provenance_fingerprint": "3" * 64,
            "systems": ["P1", "P2"],
            "system_frozen_provenance": {"P1": {}, "P2": {}},
            "candidate_catalog": {"synthetic_fixture": True},
            "frozen_configuration": {"synthetic_fixture": True},
            "environment_identity": {"synthetic_fixture": True},
        },
        retrieval_ks=(1, 3, 5),
        bootstrap_replicates=20,
        bootstrap_seed=20260824,
    )
    assert validate_statistical_package(package_dir)["analysis_status"] == (
        "DERIVED_EVIDENCE_COMPLETE"
    )
    with pytest.raises(FileExistsError):
        write_analysis_package(
            package_dir,
            result=result,
            gold=gold,
            evidence_dir=evidence_dir,
            provenance={},
            retrieval_ks=(1, 3, 5),
            bootstrap_replicates=20,
            bootstrap_seed=20260824,
        )

    manifest_path = package_dir / "analysis-manifest.json"
    original_manifest = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(original_manifest)
    namespace = next(iter(manifest["bootstrap"]["derived_seeds"]))
    manifest["bootstrap"]["derived_seeds"][namespace] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(StatisticalAnalysisError, match="derived bootstrap seed"):
        validate_statistical_package(package_dir)
    manifest_path.write_text(original_manifest, encoding="utf-8")

    system_output = package_dir / "system-estimates.jsonl"
    system_output.write_text(
        system_output.read_text(encoding="utf-8") + "{}\n",
        encoding="utf-8",
    )
    with pytest.raises(StatisticalAnalysisError, match="checksum mismatch"):
        validate_statistical_package(package_dir)
