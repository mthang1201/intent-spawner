"""Recommendation functional success metrics and mismatch detection for Protocol-v5."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import json
from typing import Any, Mapping, Sequence

from .contracts import (
    DimensionCStatus,
    FUNCTIONAL_EVALUATION_SCHEMA_VERSION,
    FUNCTIONAL_METRICS_SCHEMA_VERSION,
    FunctionalEvaluationRecord,
    ImageProbeResult,
    ProbeExecutionStatus,
)


@dataclass(frozen=True, slots=True)
class SystemFunctionalSummary:
    """Aggregated operational metrics for one recommendation system with explicit denominators."""

    system_id: str
    total_recommendations: int
    recommendations_with_image_count: int
    no_image_recommendation_count: int
    gold_acceptable_count: int
    gold_preferred_count: int
    catalog_capability_satisfied_count: int
    catalog_unsatisfied_count: int
    functional_validation_eligible_count: int
    functional_executed_count: int
    functional_passed_count: int
    functional_failed_count: int
    functional_unavailable_count: int
    operationally_adequate_count: int
    gold_acceptable_rate: float
    gold_preferred_rate: float
    catalog_capability_coverage_rate: float
    functional_execution_coverage: float
    functional_success_rate_among_executed: float | None
    conservative_functional_success_rate: float | None
    operational_adequacy_rate: float
    joint_gold_and_functional_rate: float | None
    catalog_probe_mismatch_count: int
    label_pass_functional_fail_count: int
    label_fail_functional_pass_count: int
    capability_unsatisfied_count: int
    execution_unavailable_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "system_id": self.system_id,
            "total_recommendations": self.total_recommendations,
            "recommendations_with_image_count": self.recommendations_with_image_count,
            "no_image_recommendation_count": self.no_image_recommendation_count,
            "gold_acceptable_count": self.gold_acceptable_count,
            "gold_preferred_count": self.gold_preferred_count,
            "catalog_capability_satisfied_count": self.catalog_capability_satisfied_count,
            "catalog_unsatisfied_count": self.catalog_unsatisfied_count,
            "functional_validation_eligible_count": self.functional_validation_eligible_count,
            "functional_executed_count": self.functional_executed_count,
            "functional_passed_count": self.functional_passed_count,
            "functional_failed_count": self.functional_failed_count,
            "functional_unavailable_count": self.functional_unavailable_count,
            "operationally_adequate_count": self.operationally_adequate_count,
            "gold_acceptable_rate": round(self.gold_acceptable_rate, 4),
            "gold_preferred_rate": round(self.gold_preferred_rate, 4),
            "catalog_capability_coverage_rate": round(self.catalog_capability_coverage_rate, 4),
            "functional_execution_coverage": round(self.functional_execution_coverage, 4),
            "functional_success_rate_among_executed": (
                round(self.functional_success_rate_among_executed, 4)
                if self.functional_success_rate_among_executed is not None
                else None
            ),
            "conservative_functional_success_rate": (
                round(self.conservative_functional_success_rate, 4)
                if self.conservative_functional_success_rate is not None
                else None
            ),
            "operational_adequacy_rate": round(self.operational_adequacy_rate, 4),
            "joint_gold_and_functional_rate": (
                round(self.joint_gold_and_functional_rate, 4)
                if self.joint_gold_and_functional_rate is not None
                else None
            ),
            "catalog_probe_mismatch_count": self.catalog_probe_mismatch_count,
            "label_pass_functional_fail_count": self.label_pass_functional_fail_count,
            "label_fail_functional_pass_count": self.label_fail_functional_pass_count,
            "capability_unsatisfied_count": self.capability_unsatisfied_count,
            "execution_unavailable_count": self.execution_unavailable_count,
        }


@dataclass(frozen=True, slots=True)
class FunctionalMetricsReport:
    """Complete summary of functional validation across all systems and mismatch analysis."""

    schema_version: str
    catalog_version: str
    total_evaluations: int
    probe_summary: dict[str, int]
    systems: dict[str, SystemFunctionalSummary]
    catalog_probe_mismatches: list[dict[str, Any]]
    label_operational_discrepancies: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "catalog_version": self.catalog_version,
            "total_evaluations": self.total_evaluations,
            "probe_summary": dict(self.probe_summary),
            "systems": {k: v.to_dict() for k, v in self.systems.items()},
            "catalog_probe_mismatches": self.catalog_probe_mismatches,
            "label_operational_discrepancies": self.label_operational_discrepancies,
        }


def evaluate_recommendation_functional(
    *,
    case_id: str,
    family_id: str = "",
    variant_id: str = "",
    system_id: str,
    predicted_image_id: str | None,
    required_capabilities: Sequence[str],
    gold_preferred_image_id: str | None,
    gold_acceptable_image_ids: Sequence[str],
    catalog: Mapping[str, Any],
    probe_results: Mapping[tuple[str, str], ImageProbeResult],
    execution_status: str = "COMPLETED",
) -> FunctionalEvaluationRecord:
    """Evaluate one recommendation row across Dimensions A, B, and C with strict 3-state logic."""
    norm_required = tuple(sorted({c.strip().lower() for c in required_capabilities if c.strip()}))
    norm_acceptable = tuple(sorted({img.strip() for img in gold_acceptable_image_ids if img.strip()}))

    # Dimension A: Gold-label correctness (benchmark YAML label matching)
    dim_a_preferred = bool(predicted_image_id and predicted_image_id == gold_preferred_image_id)
    dim_a_acceptable = bool(predicted_image_id and predicted_image_id in norm_acceptable)

    # Dimension B: Catalog capability coverage (catalog claims)
    images_catalog = catalog.get("images", {})
    image_catalog_entry = images_catalog.get(predicted_image_id, {}) if predicted_image_id else {}
    catalog_caps = set(image_catalog_entry.get("capabilities", []))

    missing_catalog_caps = tuple(sorted([c for c in norm_required if c not in catalog_caps]))
    dim_b_satisfied = bool(predicted_image_id and len(missing_catalog_caps) == 0)

    # Dimension C & Mismatch detection
    mismatches: list[str] = []
    failed_probes_list: list[str] = []
    unavailable_probes_list: list[str] = []

    if not predicted_image_id:
        # Case 1: No image recommendation provided
        dim_c_status = DimensionCStatus.NOT_APPLICABLE.value
        dim_c_satisfied: bool | None = None
        dim_c_coverage = False
        mismatches.append("NO_IMAGE_RECOMMENDATION")
    elif not dim_b_satisfied:
        # Case 2: Selected image / catalog does NOT satisfy workload required capabilities
        # Dimension C CANNOT pass.
        dim_c_status = DimensionCStatus.NOT_EXECUTED.value
        dim_c_satisfied = None
        dim_c_coverage = False
        mismatches.append("CAPABILITY_UNSATISFIED")
    else:
        # Case 3: Eligible recommendation - evaluate workload required capabilities in container
        caps_to_check = set(norm_required)
        if not caps_to_check:
            caps_to_check.add("python")

        for cap in sorted(caps_to_check):
            key = (predicted_image_id, cap)
            result = probe_results.get(key)
            if result is None:
                unavailable_probes_list.append(f"probe:{predicted_image_id}:{cap}(missing)")
            elif not result.is_executed:
                unavailable_probes_list.append(f"probe:{predicted_image_id}:{cap}({result.execution_status})")
            elif not result.success:
                failed_probes_list.append(f"probe:{predicted_image_id}:{cap}({result.error_category or 'failed'})")

        if unavailable_probes_list:
            dim_c_status = DimensionCStatus.NOT_EXECUTED.value
            dim_c_satisfied = None
            dim_c_coverage = False
            mismatches.append("EXECUTION_UNAVAILABLE")
        elif failed_probes_list:
            dim_c_status = DimensionCStatus.FAIL.value
            dim_c_satisfied = False
            dim_c_coverage = True
        else:
            dim_c_status = DimensionCStatus.PASS.value
            dim_c_satisfied = True
            dim_c_coverage = True

    failed_probes = tuple(failed_probes_list)
    unavailable_probes = tuple(unavailable_probes_list)

    # Mismatch 1: Catalog claims capability exists, AND container was started & executed, but probe failed!
    if predicted_image_id and dim_b_satisfied:
        for cap in sorted(catalog_caps):
            key = (predicted_image_id, cap)
            result = probe_results.get(key)
            if result and result.is_genuine_probe_failure:
                mismatches.append("CATALOG_PROBE_MISMATCH")
                break

    # Mismatch 2: Label matches gold YAML, but genuine functional failure in executed container
    if dim_a_acceptable and dim_c_status == DimensionCStatus.FAIL.value:
        mismatches.append("LABEL_PASS_FUNCTIONAL_FAIL")

    # Mismatch 3: Label differs from gold YAML, but functionally passed and catalog satisfies requirements
    if not dim_a_acceptable and dim_c_status == DimensionCStatus.PASS.value and dim_b_satisfied:
        mismatches.append("LABEL_FAIL_FUNCTIONAL_PASS")

    return FunctionalEvaluationRecord(
        schema_version=FUNCTIONAL_EVALUATION_SCHEMA_VERSION,
        case_id=case_id,
        family_id=family_id,
        variant_id=variant_id,
        system_id=system_id,
        predicted_image_id=predicted_image_id,
        required_capabilities=norm_required,
        gold_preferred_image_id=gold_preferred_image_id,
        gold_acceptable_image_ids=norm_acceptable,
        dimension_a_gold_match=dim_a_acceptable,
        dimension_a_preferred_match=dim_a_preferred,
        dimension_b_catalog_satisfied=dim_b_satisfied,
        missing_catalog_capabilities=missing_catalog_caps,
        dimension_c_status=dim_c_status,
        dimension_c_functional_satisfied=dim_c_satisfied,
        dimension_c_execution_coverage=dim_c_coverage,
        failed_probes=failed_probes,
        unavailable_probes=unavailable_probes,
        mismatch_types=tuple(mismatches),
        execution_status=execution_status,
    )


def compute_functional_metrics(
    evaluations: Sequence[FunctionalEvaluationRecord],
    catalog: Mapping[str, Any],
    probe_results: Sequence[ImageProbeResult] | None = None,
) -> FunctionalMetricsReport:
    """Aggregate evaluations into system metrics, recording explicit counts and denominators."""
    by_system = defaultdict(list)
    for rec in evaluations:
        by_system[rec.system_id].append(rec)

    catalog_version = str(catalog.get("catalog_version", "unknown"))
    systems_summary: dict[str, SystemFunctionalSummary] = {}
    catalog_probe_mismatches: list[dict[str, Any]] = []
    label_operational_discrepancies: list[dict[str, Any]] = []

    # Probe-level counts
    probes_list = list(probe_results or ())
    probe_summary = {
        "total_probes_configured": len(probes_list),
        "probes_executed": sum(1 for p in probes_list if p.is_executed),
        "probes_passed": sum(1 for p in probes_list if p.is_executed and p.success),
        "probes_failed": sum(1 for p in probes_list if p.is_genuine_probe_failure),
        "probes_unavailable": sum(1 for p in probes_list if not p.is_executed),
    }

    for sys_id, records in sorted(by_system.items()):
        n = len(records)
        if n == 0:
            continue

        with_img_count = sum(1 for r in records if r.predicted_image_id is not None)
        no_img_count = n - with_img_count

        gold_acc_count = sum(1 for r in records if r.dimension_a_gold_match)
        gold_pref_count = sum(1 for r in records if r.dimension_a_preferred_match)
        catalog_count = sum(1 for r in records if r.dimension_b_catalog_satisfied)
        catalog_unsat_count = n - catalog_count

        # Functional eligibility: has image AND Dimension B is satisfied
        eligible_count = sum(
            1 for r in records if r.predicted_image_id is not None and r.dimension_b_catalog_satisfied
        )

        # Dimension C counts among eligible recommendations
        func_exec_count = sum(
            1 for r in records
            if r.predicted_image_id is not None and r.dimension_b_catalog_satisfied and r.dimension_c_execution_coverage
        )
        func_pass_count = sum(
            1 for r in records
            if r.predicted_image_id is not None and r.dimension_b_catalog_satisfied and r.dimension_c_status == DimensionCStatus.PASS.value
        )
        func_fail_count = sum(
            1 for r in records
            if r.predicted_image_id is not None and r.dimension_b_catalog_satisfied and r.dimension_c_status == DimensionCStatus.FAIL.value
        )
        func_unavail_count = sum(
            1 for r in records
            if r.predicted_image_id is not None and r.dimension_b_catalog_satisfied and r.dimension_c_status == DimensionCStatus.NOT_EXECUTED.value
        )

        # Operational adequacy: Image present AND Dim B satisfied AND Dim C PASS
        op_adequate_count = func_pass_count

        # Rates
        gold_acc_rate = gold_acc_count / n
        gold_pref_rate = gold_pref_count / n
        catalog_cov_rate = catalog_count / n
        exec_coverage = func_exec_count / eligible_count if eligible_count > 0 else 0.0
        success_rate_among_executed = (
            func_pass_count / func_exec_count if func_exec_count > 0 else None
        )
        conservative_success_rate = (
            func_pass_count / n if func_exec_count > 0 else None
        )
        op_adequacy_rate = op_adequate_count / n

        # Joint pass: Dimension A gold acceptable AND Dimension B satisfied AND Dimension C PASS
        joint_count = sum(
            1 for r in records
            if r.dimension_a_gold_match and r.dimension_b_catalog_satisfied and r.dimension_c_status == DimensionCStatus.PASS.value
        )
        joint_rate = (
            joint_count / func_exec_count if func_exec_count > 0 else None
        )

        cat_probe_mismatch = sum(1 for r in records if "CATALOG_PROBE_MISMATCH" in r.mismatch_types)
        label_pass_func_fail = sum(1 for r in records if "LABEL_PASS_FUNCTIONAL_FAIL" in r.mismatch_types)
        label_fail_func_pass = sum(1 for r in records if "LABEL_FAIL_FUNCTIONAL_PASS" in r.mismatch_types)
        cap_unsat = sum(1 for r in records if "CAPABILITY_UNSATISFIED" in r.mismatch_types)
        exec_unavail = sum(1 for r in records if "EXECUTION_UNAVAILABLE" in r.mismatch_types)

        summary = SystemFunctionalSummary(
            system_id=sys_id,
            total_recommendations=n,
            recommendations_with_image_count=with_img_count,
            no_image_recommendation_count=no_img_count,
            gold_acceptable_count=gold_acc_count,
            gold_preferred_count=gold_pref_count,
            catalog_capability_satisfied_count=catalog_count,
            catalog_unsatisfied_count=catalog_unsat_count,
            functional_validation_eligible_count=eligible_count,
            functional_executed_count=func_exec_count,
            functional_passed_count=func_pass_count,
            functional_failed_count=func_fail_count,
            functional_unavailable_count=func_unavail_count,
            operationally_adequate_count=op_adequate_count,
            gold_acceptable_rate=gold_acc_rate,
            gold_preferred_rate=gold_pref_rate,
            catalog_capability_coverage_rate=catalog_cov_rate,
            functional_execution_coverage=exec_coverage,
            functional_success_rate_among_executed=success_rate_among_executed,
            conservative_functional_success_rate=conservative_success_rate,
            operational_adequacy_rate=op_adequacy_rate,
            joint_gold_and_functional_rate=joint_rate,
            catalog_probe_mismatch_count=cat_probe_mismatch,
            label_pass_functional_fail_count=label_pass_func_fail,
            label_fail_functional_pass_count=label_fail_func_pass,
            capability_unsatisfied_count=cap_unsat,
            execution_unavailable_count=exec_unavail,
        )
        systems_summary[sys_id] = summary

        # Collect detailed mismatch rows
        for r in records:
            if "CATALOG_PROBE_MISMATCH" in r.mismatch_types:
                catalog_probe_mismatches.append(r.to_dict())
            if "LABEL_PASS_FUNCTIONAL_FAIL" in r.mismatch_types or "LABEL_FAIL_FUNCTIONAL_PASS" in r.mismatch_types:
                label_operational_discrepancies.append(r.to_dict())

    return FunctionalMetricsReport(
        schema_version=FUNCTIONAL_METRICS_SCHEMA_VERSION,
        catalog_version=catalog_version,
        total_evaluations=len(evaluations),
        probe_summary=probe_summary,
        systems=systems_summary,
        catalog_probe_mismatches=catalog_probe_mismatches,
        label_operational_discrepancies=label_operational_discrepancies,
    )
