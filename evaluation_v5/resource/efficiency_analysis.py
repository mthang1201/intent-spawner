"""Failure-aware derivation, family-first inference, oracle error, and Pareto checks."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

from evaluation_v5.analysis.statistics import derive_bootstrap_seed, holm_adjust, paired_effect_sizes, paired_family_bootstrap_ci, paired_test

from .comparison import classify_axis
from .efficiency_contracts import CONTRASTS, FAMILY_COUNT, PARETO_OBJECTIVES, REPETITIONS
from .efficiency_models import ResourceAllocation, primary_outcome


PRIMARY_ENDPOINTS = ("success_rate", "oom_rate", "cpu_cost_per_success", "memory_cost_per_success")
SECONDARY_ENDPOINTS = (
    "timeout_rate", "pending_or_admission_rate", "runtime_error_rate", "correctness_rate",
    "correct_completion_rate", "incorrect_rate", "runtime_seconds",
    "cpu_request_ratio", "memory_request_ratio", "cpu_request_error_signed",
    "memory_request_error_signed", "cpu_limit_error_signed", "memory_limit_error_signed",
)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return float(value)


def _error(allocation: int, selected: int | None, interval: Mapping[str, Any] | None) -> dict[str, Any]:
    if selected is None or not interval:
        return {
            "signed": None, "absolute": None, "percentage": None,
            "over": None, "under": None,
            "classification": "NO_REFERENCE_AVAILABLE",
            "definition": "allocation_minus_oracle_selected",
            "percentage_denominator": "oracle_selected",
        }
    signed = allocation - selected
    return {
        "signed": signed, "absolute": abs(signed),
        "percentage": None if selected == 0 else signed / selected * 100,
        "over": max(signed, 0), "under": max(-signed, 0),
        "classification": classify_axis(allocation, interval, selected),
        "definition": "allocation_minus_oracle_selected",
        "percentage_denominator": "oracle_selected",
    }


def canonical_allocation_identity(allocation: Mapping[str, Any]) -> tuple[int, int, int, int, int, str | None]:
    """Return the sole identity used for final P2_DYNAMIC uniqueness counts.

    ``ResourceAllocation`` accepts only canonical millicores, MiB, and integer
    GPU counts, so Kubernetes spellings such as ``1`` CPU and ``1000m`` are
    normalized before this function is called and cannot create false uniques.
    """

    value = ResourceAllocation.from_dict(allocation)
    return (
        value.cpu_request_m, value.cpu_limit_m,
        value.memory_request_mib, value.memory_limit_mib,
        value.gpu_count, value.gpu_resource,
    )


def _dynamic_telemetry(
    row: Mapping[str, Any], decision: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if row.get("condition") != "P2_DYNAMIC":
        return {
            "applicable": False, "status": "NOT_APPLICABLE",
            "raw_generated_targets": None, "floor_adjusted_targets": None,
            "profile_floors": None, "profile_floor_applied": None,
            "quantized_allocation": None, "quantization_deltas": None,
            "quantization_policy": None,
            "policy_clipping_applied": None, "fallback_to_catalog": None,
            "clipping_semantics": None, "fallback_reason": None, "final_allocation": None,
            "canonical_final_allocation_identity": None,
        }
    if decision is None:
        return {
            "applicable": True, "status": "EVIDENCE_MISSING",
            "raw_generated_targets": None, "floor_adjusted_targets": None,
            "profile_floors": None, "profile_floor_applied": None,
            "quantized_allocation": None, "quantization_deltas": None,
            "quantization_policy": None,
            "policy_clipping_applied": None, "fallback_to_catalog": None,
            "clipping_semantics": None, "fallback_reason": "SEALED_FAMILY_DECISION_MISSING",
            "final_allocation": None, "canonical_final_allocation_identity": None,
        }
    if decision.get("family_id") != row.get("family_id"):
        raise ValueError("dynamic decision does not belong to the trial family")
    planned = dict(row["planned_resources"])
    final = (decision.get("allocations") or {}).get("P2_DYNAMIC")
    if final != planned:
        raise ValueError("dynamic decision lineage does not end at the trial allocation")
    trace = decision.get("dynamic_trace") or {}
    dynamic = decision.get("dynamic_decision") or {}
    fallback = trace.get("fallback_to_catalog")
    if dynamic.get("applied_mode") == "dynamic":
        quantized = trace.get("quantized_resources")
        expected = {
            "cpu_request_m": (quantized or {}).get("cpu_request_millicores"),
            "cpu_limit_m": (quantized or {}).get("cpu_limit_millicores"),
            "memory_request_mib": (quantized or {}).get("memory_request_mib"),
            "memory_limit_mib": (quantized or {}).get("memory_limit_mib"),
            "gpu_count": (quantized or {}).get("gpu_count"),
            "gpu_resource": (quantized or {}).get("gpu_resource"),
        }
        if fallback is not False or expected != planned:
            raise ValueError("quantized dynamic allocation lineage is inconsistent")
    elif dynamic.get("applied_mode") == "catalog":
        if fallback is not True or final != (decision.get("allocations") or {}).get("P2_CATALOG"):
            raise ValueError("dynamic fallback lineage is inconsistent")
    else:
        raise ValueError("dynamic decision lineage has an unsupported applied mode")
    return {
        "applicable": True, "status": "AVAILABLE",
        "raw_generated_targets": trace.get("formula_targets"),
        "profile_floors": trace.get("profile_floors"),
        "profile_floor_applied": trace.get("profile_floor_applied"),
        "floor_adjusted_targets": trace.get("floor_adjusted_targets"),
        "quantized_allocation": trace.get("quantized_resources"),
        "quantization_deltas": trace.get("quantization_deltas"),
        "quantization_policy": trace.get("quantization_policy"),
        "policy_clipping_applied": trace.get("policy_clipping_applied"),
        "clipping_semantics": trace.get("clipping_semantics"),
        "fallback_to_catalog": fallback,
        "fallback_reason": trace.get("fallback_reason"),
        "final_allocation": final,
        "canonical_final_allocation_identity": list(canonical_allocation_identity(final)),
    }


def summarize_dynamic_allocations(decisions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize final generated allocations under one frozen canonical identity."""

    generated = [
        row for row in decisions
        if (row.get("dynamic_decision") or {}).get("applied_mode") == "dynamic"
    ]
    allocations = {
        canonical_allocation_identity((row.get("allocations") or {})["P2_DYNAMIC"])
        for row in generated
    }
    return {
        "schema_version": "protocol-v5-resource-efficiency-dynamic-summary-v1.0.0",
        "generated_family_count": len(generated),
        "catalog_fallback_count": len(decisions) - len(generated),
        "unique_generated_allocation_count": len(allocations),
        "unique_generated_allocations": [list(item) for item in sorted(allocations, key=repr)],
        "unique_allocation_definition": {
            "scope": "global_across_generated_P2_DYNAMIC_families",
            "stage": "after_quantization_and_policy_validation_final_allocation",
            "identity_fields": ["cpu_request_m", "cpu_limit_m", "memory_request_mib", "memory_limit_mib", "gpu_count", "gpu_resource"],
            "canonical_units": {"cpu": "millicores", "memory": "MiB", "gpu": "integer_extended_resource_count"},
            "catalog_fallbacks_included": False,
        },
        "traces": [{"family_id": row["family_id"], **dict(row.get("dynamic_trace") or {})} for row in decisions],
    }


def load_approved_oracle(path: Path, *, expected_sha256: str | None = None) -> dict[str, Any]:
    from .evidence import file_sha256, validate_evidence_package

    package = validate_evidence_package(path)
    if package["execution_status"] != "OBSERVED" or not package["eligible_for_comparison"]:
        raise ValueError("oracle must be a sealed manually approved independent calibration package")
    if expected_sha256 is not None and file_sha256(path / "SHA256SUMS") != expected_sha256:
        raise ValueError("oracle package checksum differs from the frozen contract")
    payload = json.loads((path / "derived" / "safe-envelopes.json").read_text(encoding="utf-8"))
    return {row["family_id"]: row for row in payload["envelopes"]}


def derive_trial(
    row: Mapping[str, Any],
    oracle: Mapping[str, Mapping[str, Any]] | None = None,
    decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(row)
    result["derived_schema_version"] = "protocol-v5-resource-efficiency-derived-trial-v1.0.0"
    result["primary_outcome"] = primary_outcome(row)
    planned = ResourceAllocation.from_dict(row["planned_resources"]).to_dict()
    infrastructure_invalid = bool(row.get("infrastructure_invalid"))
    scheduled = bool(row.get("scheduled"))
    workload_runtime = _finite(row.get("workload_runtime_seconds"))
    container_runtime = _finite(row.get("container_runtime_seconds"))
    if infrastructure_invalid:
        accounting_runtime, runtime_reason = None, "INFRASTRUCTURE_INVALID"
    elif workload_runtime is not None:
        accounting_runtime, runtime_reason = workload_runtime, "WORKLOAD_MEASUREMENT"
    elif scheduled and result["primary_outcome"] in {"OOM", "TIMEOUT", "RUNTIME_ERROR"} and container_runtime is not None:
        accounting_runtime, runtime_reason = container_runtime, "KUBERNETES_CONTAINER_DURATION_FALLBACK"
    elif not scheduled:
        accounting_runtime, runtime_reason = 0.0, "UNSCHEDULED_ZERO_REQUEST_TIME"
    else:
        accounting_runtime, runtime_reason = None, "SCHEDULED_DURATION_MISSING"
    metrics = {} if infrastructure_invalid else row.get("cgroup_metrics") or {}
    mean_cpu_m = _finite(metrics.get("mean_cpu_m"))
    if mean_cpu_m is None:
        mean_cpu_m = _finite(metrics.get("cpu_full_window_average_m"))
    if mean_cpu_m is None and accounting_runtime not in (None, 0):
        cpu_usage = _finite(metrics.get("cpu_usage_seconds"))
        if cpu_usage is None:
            usage_usec = _finite(metrics.get("cpu_usage_usec_delta"))
            cpu_usage = None if usage_usec is None else usage_usec / 1_000_000
        mean_cpu_m = None if cpu_usage is None else cpu_usage / accounting_runtime * 1000
    peak_memory = _finite(metrics.get("peak_memory_mib"))
    if peak_memory is None:
        peak_memory = _finite(metrics.get("memory_peak_mib"))
    result.update({
        "valid_attempt": not infrastructure_invalid,
        "mean_cpu_m": mean_cpu_m, "peak_memory_mib": peak_memory,
        "cpu_request_ratio": None if mean_cpu_m is None else mean_cpu_m / planned["cpu_request_m"],
        "memory_request_ratio": None if peak_memory is None else peak_memory / planned["memory_request_mib"],
        "accounting_runtime_seconds": accounting_runtime, "accounting_runtime_reason": runtime_reason,
        "observed_runtime_seconds": None if not scheduled or infrastructure_invalid else accounting_runtime,
        "observed_runtime_unavailable_reason": (
            "INFRASTRUCTURE_INVALID" if infrastructure_invalid
            else None if scheduled and accounting_runtime is not None
            else "UNSCHEDULED" if not scheduled else "SCHEDULED_DURATION_MISSING"
        ),
        "cpu_request_time_millicore_seconds": None if accounting_runtime is None else planned["cpu_request_m"] * accounting_runtime,
        "cpu_request_time_cpu_seconds": None if accounting_runtime is None else planned["cpu_request_m"] * accounting_runtime / 1000,
        "memory_request_time_mib_seconds": None if accounting_runtime is None else planned["memory_request_mib"] * accounting_runtime,
        "cpu_usage_unavailable_reason": None if mean_cpu_m is not None else "CGROUP_CPU_USAGE_MISSING",
        "peak_memory_unavailable_reason": None if peak_memory is not None else "CGROUP_MEMORY_PEAK_MISSING",
        "cpu_request_ratio_unavailable_reason": None if mean_cpu_m is not None else "CGROUP_CPU_USAGE_MISSING",
        "memory_request_ratio_unavailable_reason": None if peak_memory is not None else "CGROUP_MEMORY_PEAK_MISSING",
    })
    if infrastructure_invalid:
        result["cpu_usage_unavailable_reason"] = "INFRASTRUCTURE_INVALID"
        result["peak_memory_unavailable_reason"] = "INFRASTRUCTURE_INVALID"
        result["cpu_request_ratio_unavailable_reason"] = "INFRASTRUCTURE_INVALID"
        result["memory_request_ratio_unavailable_reason"] = "INFRASTRUCTURE_INVALID"
    envelope = None if oracle is None or infrastructure_invalid else oracle.get(str(row["family_id"]))
    for axis, key, interval_key, selected_key in (
        ("cpu", "cpu_request_m", "cpu_minimum_interval", "cpu_selected_m"),
        ("memory", "memory_request_mib", "memory_minimum_interval", "memory_selected_mib"),
    ):
        selected = None if envelope is None else envelope.get(selected_key)
        interval = None if envelope is None else envelope.get(interval_key)
        request_error = _error(planned[key], selected, interval)
        request_error["comparison_role"] = "capacity_request_comparison"
        result[f"{axis}_request_error"] = request_error
        limit_key = key.replace("request", "limit")
        limit_error = _error(planned[limit_key], selected, interval)
        limit_error["comparison_role"] = "oom_and_runtime_safety_limit_comparison"
        result[f"{axis}_limit_error"] = limit_error
        for allocation_kind, error in (("request", request_error), ("limit", limit_error)):
            result[f"{axis}_{allocation_kind}_allocation_error_signed"] = error["signed"]
            result[f"{axis}_{allocation_kind}_allocation_error_absolute"] = error["absolute"]
            result[f"{axis}_{allocation_kind}_allocation_error_percentage"] = error["percentage"]
            result[f"{axis}_{allocation_kind}_over_allocation"] = error["over"]
            result[f"{axis}_{allocation_kind}_under_allocation"] = error["under"]
    dynamic = _dynamic_telemetry(row, decision)
    result["telemetry"] = {
        "canonical_units": {
            "cpu_allocation": "millicores", "memory_allocation": "MiB",
            "gpu_allocation": "integer_extended_resource_count",
            "runtime": "seconds", "cpu_request_time": "CPU-seconds",
            "memory_request_time": "MiB-seconds",
        },
        "outcome": {
            "primary": result["primary_outcome"],
            "success": bool(row.get("success")), "oom": bool(row.get("oom")),
            "timeout": bool(row.get("timeout")),
            "pending_or_admission_failure": bool(row.get("pending_or_admission_failure")),
            "runtime_error": bool(row.get("runtime_error")),
            "infrastructure_invalid": infrastructure_invalid,
            "admission_or_scheduling_reason": row.get("admission_or_scheduling_reason"),
            "correctness": row.get("correctness"),
            "runtime_seconds": result["observed_runtime_seconds"],
            "runtime_unavailable_reason": result["observed_runtime_unavailable_reason"],
        },
        "request_and_limit": {
            **planned,
            "gpu_request_count": planned["gpu_count"],
            "gpu_limit_count": planned["gpu_count"],
        },
        "usage": {
            "mean_cpu_m": mean_cpu_m, "peak_memory_mib": peak_memory,
            "cpu_request_ratio": result["cpu_request_ratio"],
            "memory_request_ratio": result["memory_request_ratio"],
            "mean_cpu_unavailable_reason": result["cpu_usage_unavailable_reason"],
            "peak_memory_unavailable_reason": result["peak_memory_unavailable_reason"],
        },
        "oracle_comparison": {
            "status": "AVAILABLE" if envelope is not None else (
                "NOT_APPLICABLE_INFRASTRUCTURE_INVALID" if infrastructure_invalid else "NO_REFERENCE_AVAILABLE"
            ),
            "cpu_request": result["cpu_request_error"],
            "cpu_limit": result["cpu_limit_error"],
            "memory_request": result["memory_request_error"],
            "memory_limit": result["memory_limit_error"],
        },
        "dynamic_allocation": dynamic,
    }
    return result


def _nullable_mean(values: Sequence[Any]) -> float | None:
    selected = [value for value in (_finite(item) for item in values) if value is not None]
    return mean(selected) if selected else None


def _variability(values: Sequence[Any]) -> dict[str, float | int | None]:
    selected = [value for value in (_finite(item) for item in values) if value is not None]
    return {
        "n": len(selected),
        "mean": mean(selected) if selected else None,
        "standard_deviation": pstdev(selected) if selected else None,
        "minimum": min(selected) if selected else None,
        "maximum": max(selected) if selected else None,
    }


def summarize_repetitions(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Resolve infrastructure replacements to one nested repetition record."""

    grouped: dict[tuple[str, str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["family_id"]), str(row["condition"]), int(row["repetition"]))].append(row)
    summaries: list[dict[str, Any]] = []
    for (family, condition, repetition), attempts in sorted(grouped.items()):
        valid = [row for row in attempts if row.get("valid_attempt")]
        if len(valid) > 1:
            raise ValueError("a family-condition-repetition cell has multiple valid workload attempts")
        selected = dict(valid[0] if valid else attempts[-1])
        selected.update({
            "repetition_summary_schema_version": "protocol-v5-resource-efficiency-repetition-summary-v1.0.0",
            "family_id": family, "condition": condition, "repetition": repetition,
            "attempt_records": len(attempts),
            "infrastructure_invalid_attempts": sum(bool(row.get("infrastructure_invalid")) for row in attempts),
            "analysis_eligible": bool(valid),
            "analysis_unavailable_reason": None if valid else "NO_VALID_WORKLOAD_ATTEMPT",
        })
        summaries.append(selected)
    return summaries


def summarize_families(
    rows: Sequence[Mapping[str, Any]], *, expected_repetitions: int | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["family_id"]), str(row["condition"]))].append(row)
    summaries: list[dict[str, Any]] = []
    for (family, condition), all_rows in sorted(grouped.items()):
        cell = [row for row in all_rows if row.get("valid_attempt")]
        repetitions = {int(row["repetition"]) for row in all_rows if row.get("repetition") is not None}
        complete = bool(cell)
        if expected_repetitions is not None:
            complete = (
                len(cell) == expected_repetitions
                and repetitions == set(range(1, expected_repetitions + 1))
                and all(row.get("analysis_eligible", True) for row in all_rows)
            )
        success_count = sum(row["primary_outcome"] == "SUCCESS" for row in cell)
        missing_cost = any(row.get("accounting_runtime_seconds") is None for row in cell)
        cpu_numerator = None if missing_cost else sum(float(row["cpu_request_time_cpu_seconds"]) for row in cell)
        memory_numerator = None if missing_cost else sum(float(row["memory_request_time_mib_seconds"]) for row in cell)
        if not complete:
            cpu_cost = memory_cost = None
            cost_reason = "INCOMPLETE_REPETITION_EVIDENCE"
        elif success_count == 0:
            cpu_cost = memory_cost = None
            cost_reason = "ZERO_SUCCESS"
        elif missing_cost:
            cpu_cost = memory_cost = None
            cost_reason = "INCOMPLETE_OR_MISSING_DURATION_EVIDENCE"
        else:
            cpu_cost, memory_cost, cost_reason = cpu_numerator / success_count, memory_numerator / success_count, None
        denominator = len(cell)
        summary = {
            "family_id": family, "condition": condition,
            "repetition_count": len(repetitions),
            "raw_attempt_records": sum(int(row.get("attempt_records", 1)) for row in all_rows),
            "valid_attempts": denominator, "successful_tasks": success_count,
            "family_analysis_complete": complete,
            "success_rate": None if not complete else success_count / denominator,
            "correct_completion_rate": None if not complete else success_count / denominator,
            "oom_rate": None if not complete else sum(bool(row.get("oom")) for row in cell) / denominator,
            "timeout_rate": None if not complete else sum(bool(row.get("timeout")) for row in cell) / denominator,
            "pending_or_admission_rate": None if not complete else sum(bool(row.get("pending_or_admission_failure")) for row in cell) / denominator,
            "runtime_error_rate": None if not complete else sum(bool(row.get("runtime_error")) for row in cell) / denominator,
            "incorrect_rate": None if not complete else sum(row.get("correctness") is False for row in cell) / denominator,
            "correctness_observed_tasks": sum(row.get("correctness") is not None for row in cell),
            "correctness_rate": None if not complete else _nullable_mean([None if row.get("correctness") is None else int(row["correctness"]) for row in cell]),
            "runtime_seconds": None if not complete else _nullable_mean([row.get("observed_runtime_seconds") for row in cell]),
            "runtime_variability": _variability([row.get("observed_runtime_seconds") for row in cell]),
            "mean_cpu_m": None if not complete else _nullable_mean([row.get("mean_cpu_m") for row in cell]),
            "mean_cpu_variability": _variability([row.get("mean_cpu_m") for row in cell]),
            "peak_memory_mib": None if not complete else _nullable_mean([row.get("peak_memory_mib") for row in cell]),
            "peak_memory_variability": _variability([row.get("peak_memory_mib") for row in cell]),
            "cpu_request_ratio": None if not complete else _nullable_mean([row.get("cpu_request_ratio") for row in cell]),
            "memory_request_ratio": None if not complete else _nullable_mean([row.get("memory_request_ratio") for row in cell]),
            "cpu_request_error_signed": None if not complete else _nullable_mean([(row.get("cpu_request_error") or {}).get("signed") for row in cell]),
            "memory_request_error_signed": None if not complete else _nullable_mean([(row.get("memory_request_error") or {}).get("signed") for row in cell]),
            "cpu_limit_error_signed": None if not complete else _nullable_mean([(row.get("cpu_limit_error") or {}).get("signed") for row in cell]),
            "memory_limit_error_signed": None if not complete else _nullable_mean([(row.get("memory_limit_error") or {}).get("signed") for row in cell]),
            "cpu_request_time_numerator_cpu_seconds": cpu_numerator,
            "memory_request_time_numerator_mib_seconds": memory_numerator,
            "cpu_cost_per_success": cpu_cost, "memory_cost_per_success": memory_cost, "cost_unavailable_reason": cost_reason,
            "cost_numerator_attempts": denominator,
            "failure_request_time_included": True,
            "cost_denominator_definition": "count(primary_outcome == SUCCESS)",
            "outcome_counts_in_cost_numerator": dict(sorted(Counter(row["primary_outcome"] for row in cell).items())),
        }
        summaries.append(summary)
    return summaries


def summarize_conditions(families: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in families:
        grouped[str(row["condition"])].append(row)
    summaries: list[dict[str, Any]] = []
    for condition, rows in sorted(grouped.items()):
        success_count = sum(int(row["successful_tasks"]) for row in rows)
        missing_cost = any(
            not row.get("family_analysis_complete")
            or row.get("cpu_request_time_numerator_cpu_seconds") is None
            or row.get("memory_request_time_numerator_mib_seconds") is None
            for row in rows
        )
        if success_count == 0:
            cpu_cost = memory_cost = None
            cost_reason = "ZERO_SUCCESS"
        elif missing_cost:
            cpu_cost = memory_cost = None
            cost_reason = "INCOMPLETE_OR_MISSING_DURATION_EVIDENCE"
        else:
            cpu_cost = sum(row["cpu_request_time_numerator_cpu_seconds"] for row in rows) / success_count
            memory_cost = sum(row["memory_request_time_numerator_mib_seconds"] for row in rows) / success_count
            cost_reason = None
        summary = {
            "condition": condition,
            "family_rows": len(rows),
            "effective_family_n": sum(bool(row.get("family_analysis_complete")) for row in rows),
            "successful_tasks": success_count,
            "cpu_cost_per_success": cpu_cost,
            "memory_cost_per_success": memory_cost,
            "cost_unavailable_reason": cost_reason,
            "failure_request_time_included": True,
            "cpu_cost_per_success_definition": "sum(cpu_request_m / 1000 * accounting_runtime_seconds for all valid attempts) / successful_tasks",
            "memory_cost_per_success_definition": "sum(memory_request_mib * accounting_runtime_seconds for all valid attempts) / successful_tasks",
        }
        for endpoint in (*PRIMARY_ENDPOINTS[:2], *SECONDARY_ENDPOINTS):
            summary[endpoint] = _nullable_mean([row.get(endpoint) for row in rows])
        summaries.append(summary)
    return summaries


def statistical_results(families: Sequence[Mapping[str, Any]], *, bootstrap_replicates: int = 2000, seed: int = 20260904) -> list[dict[str, Any]]:
    index = {(str(row["family_id"]), str(row["condition"])): row for row in families}
    if len(index) != len(families):
        raise ValueError("statistical input must contain at most one summary per family-condition")
    family_ids = sorted({key[0] for key in index})
    if len(family_ids) > FAMILY_COUNT:
        raise ValueError("statistical input exceeds the frozen independent family count")
    results: list[dict[str, Any]] = []
    for endpoint in (*PRIMARY_ENDPOINTS, *SECONDARY_ENDPOINTS):
        endpoint_rows: list[dict[str, Any]] = []
        for candidate, reference in CONTRASTS:
            paired = [{"family_id": family, "first": index.get((family, reference), {}).get(endpoint), "second": index.get((family, candidate), {}).get(endpoint)} for family in family_ids]
            usable = [row for row in paired if row["first"] is not None and row["second"] is not None]
            first_values = [row["first"] for row in usable]
            second_values = [row["second"] for row in usable]
            test = paired_test(first_values, second_values, binary_outcome=False)
            ci = paired_family_bootstrap_ci(usable, "first", "second", replicates=bootstrap_replicates, seed=derive_bootstrap_seed(seed, endpoint, candidate, reference))
            endpoint_rows.append({"endpoint": endpoint, "endpoint_role": "primary" if endpoint in PRIMARY_ENDPOINTS else "secondary", "candidate_condition": candidate, "reference_condition": reference, "effect_direction": "candidate_minus_reference", "effective_family_n": len(usable), "effect": paired_effect_sizes(first_values, second_values), "ci_95_candidate_minus_reference": list(ci), "test": test})
        available = [row for row in endpoint_rows if row["test"]["p_value_raw"] is not None]
        adjusted = holm_adjust([row["test"]["p_value_raw"] for row in available])
        for row, value in zip(available, adjusted):
            row["test"]["p_value_holm_within_endpoint"] = value
        for row in endpoint_rows:
            row["test"].setdefault("p_value_holm_within_endpoint", None)
        results.extend(endpoint_rows)
    return results


def classify_pareto(candidate: Mapping[str, Any], reference: Mapping[str, Any]) -> str:
    minimize = tuple(PARETO_OBJECTIVES["minimize"])
    maximize = tuple(PARETO_OBJECTIVES["maximize"])
    keys = (*minimize, *maximize)
    if any(candidate.get(key) is None or reference.get(key) is None for key in keys):
        return "INDETERMINATE"
    no_worse = all(candidate[key] <= reference[key] for key in minimize) and all(
        candidate[key] >= reference[key] for key in maximize
    )
    any_better = any(candidate[key] < reference[key] for key in minimize) or any(
        candidate[key] > reference[key] for key in maximize
    )
    no_better = all(candidate[key] >= reference[key] for key in minimize) and all(
        candidate[key] <= reference[key] for key in maximize
    )
    any_worse = any(candidate[key] > reference[key] for key in minimize) or any(
        candidate[key] < reference[key] for key in maximize
    )
    costs_better = any(
        candidate[key] < reference[key]
        for key in ("cpu_cost_per_success", "memory_cost_per_success")
    )
    costs_worse = any(
        candidate[key] > reference[key]
        for key in ("cpu_cost_per_success", "memory_cost_per_success")
    )
    reliability_minimize = tuple(
        key for key in minimize
        if key not in {"cpu_cost_per_success", "memory_cost_per_success"}
    )
    reliability_worse = any(candidate[key] > reference[key] for key in reliability_minimize) or any(
        candidate[key] < reference[key] for key in maximize
    )
    reliability_better = any(candidate[key] < reference[key] for key in reliability_minimize) or any(
        candidate[key] > reference[key] for key in maximize
    )
    if no_worse and any_better:
        return "STRICT_FRONTIER_IMPROVEMENT"
    if costs_better and reliability_worse:
        return "EFFICIENCY_RELIABILITY_TRADEOFF"
    all_equal = all(candidate[key] == reference[key] for key in keys)
    if all_equal:
        return "EQUIVALENT"
    if no_better and any_worse:
        return "DOMINATED"
    if reliability_better and costs_worse:
        return "EFFICIENCY_RELIABILITY_TRADEOFF"
    return "INDETERMINATE"


def analyze_trials(
    trials: Sequence[Mapping[str, Any]], *,
    oracle: Mapping[str, Mapping[str, Any]] | None = None,
    decisions: Sequence[Mapping[str, Any]] | None = None,
    bootstrap_replicates: int = 2000,
) -> dict[str, Any]:
    decision_by_family = {str(row["family_id"]): row for row in decisions or []}
    derived = [derive_trial(row, oracle, decision_by_family.get(str(row["family_id"]))) for row in trials]
    repetitions = summarize_repetitions(derived)
    families = summarize_families(repetitions, expected_repetitions=REPETITIONS)
    conditions = summarize_conditions(families)
    by_condition = {row["condition"]: row for row in conditions}
    pareto = [{"condition": condition, "reference": "STATIC_LARGE", "classification": classify_pareto(by_condition[condition], by_condition["STATIC_LARGE"])} for condition in ("P1_CATALOG", "P2_CATALOG", "P2_DYNAMIC") if condition in by_condition and "STATIC_LARGE" in by_condition]
    design_counts = {
        "number_of_families": len({str(row["family_id"]) for row in repetitions}),
        "repetitions_per_family_condition": REPETITIONS,
        "raw_primary_trial_count": len({str(row["primary_trial_id"]) for row in trials}),
        "independent_semantic_n": len({str(row["family_id"]) for row in repetitions}),
    }
    return {
        "schema_version": "protocol-v5-resource-efficiency-analysis-v1.0.0",
        "analysis_hierarchy": [
            "raw_trial_attempt", "family_condition_repetition",
            "family_condition", "paired_cross_family_inference",
        ],
        "design_counts": design_counts,
        "raw_attempt_record_count": len(trials),
        "derived_trials": derived, "repetition_summaries": repetitions,
        "family_condition_summaries": families, "condition_summaries": conditions,
        "statistics": statistical_results(families, bootstrap_replicates=bootstrap_replicates),
        "pareto_objectives": PARETO_OBJECTIVES,
        "success_noninferiority_margin": None,
        "pareto": pareto,
    }


__all__ = [
    "analyze_trials", "canonical_allocation_identity", "classify_pareto",
    "derive_trial", "load_approved_oracle", "statistical_results",
    "summarize_conditions", "summarize_dynamic_allocations", "summarize_families",
    "summarize_repetitions",
]
