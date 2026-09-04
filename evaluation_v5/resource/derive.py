"""Derive conservative, interval-censored safe envelopes from raw E4 trials."""

from __future__ import annotations

from collections import defaultdict
import math
import statistics
from typing import Any, Iterable, Mapping, Sequence

from .manifest import (
    CPU_LATTICE_M, MEMORY_LATTICE_MIB, SAFE_RULE, validate_resource_manifest,
    workload_fingerprint,
)
from .models import SafeEnvelope, TrialObservation


DERIVATION_SCHEMA_VERSION = "protocol-v5-safe-resource-envelopes-v1.2.0"
NON_MONOTONIC = "NON_MONOTONIC_BOUNDARY_REQUIRES_REVIEW"
REFERENCE_UNSTABLE = "REFERENCE_RUNTIME_UNSTABLE_REQUIRES_REVIEW"


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    """Descriptive repeat-stability interval; never a workload-population inference."""
    if total == 0:
        return (None, None)
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def reference_relative_spread(runtimes: Sequence[float]) -> float:
    if len(runtimes) != SAFE_RULE["reference_repeats"] or any(value <= 0 for value in runtimes):
        raise ValueError("reference stability requires three positive runtimes")
    median = statistics.median(runtimes)
    return (max(runtimes) - min(runtimes)) / median


def reference_is_stable(runtimes: Sequence[float]) -> tuple[bool, float]:
    statistic = reference_relative_spread(runtimes)
    return statistic <= SAFE_RULE["reference_max_relative_spread"] + 1e-12, statistic


def _valid(rows: Iterable[TrialObservation]) -> list[TrialObservation]:
    return [row for row in rows if not row.infrastructure_invalid]


def _memory_events_are_oom_free(metrics: Mapping[str, Any]) -> bool:
    events = metrics.get("memory_events_delta")
    if not isinstance(events, Mapping):
        return False
    for key in SAFE_RULE["required_zero_memory_events"]:
        value = events.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value != 0:
            return False
    for key in SAFE_RULE["optional_zero_memory_events"]:
        if key in events:
            value = events[key]
            if not isinstance(value, int) or isinstance(value, bool) or value != 0:
                return False
    return True


def trial_basic_success(row: TrialObservation) -> bool:
    metrics = row.cgroup_metrics
    cpu_parts = str(metrics.get("cpu_max", "")).split()
    try:
        observed_cpu_m = float(cpu_parts[0]) / float(cpu_parts[1]) * 1000 if len(cpu_parts) == 2 and cpu_parts[0] != "max" else None
        observed_memory_bytes = int(str(metrics.get("memory_max", "")))
    except (ValueError, ZeroDivisionError):
        observed_cpu_m = None
        observed_memory_bytes = None
    return (
        row.workload_success
        and 1 <= row.workload_timeout_seconds <= 120
        and row.runtime_seconds is not None
        and math.isfinite(row.runtime_seconds)
        and 0 < row.runtime_seconds <= row.workload_timeout_seconds
        and row.cgroup_version == "v2"
        and metrics.get("source") == "cgroup_v2_in_container"
        and set(metrics.get("controllers") or []) >= {"cpu", "memory", "pids"}
        and isinstance(metrics.get("memory_peak_mib"), (int, float))
        and isinstance(metrics.get("cpu_full_window_average_m"), (int, float))
        and isinstance(metrics.get("cpu_usage_usec_delta"), int)
        and isinstance(metrics.get("cpu_max"), str)
        and isinstance(metrics.get("memory_max"), str)
        and _memory_events_are_oom_free(metrics)
        and observed_cpu_m is not None
        and abs(observed_cpu_m - row.cpu_m) <= 1
        and observed_memory_bytes == row.memory_mib * 1024 * 1024
    )


def cell_acceptable(rows: Sequence[TrialObservation], reference_median: float, required: int) -> bool:
    valid = _valid(rows)
    if len(valid) != required or not all(trial_basic_success(row) for row in valid):
        return False
    runtimes = [float(row.runtime_seconds) for row in valid if row.runtime_seconds is not None]
    return (
        len(runtimes) == required
        and statistics.median(runtimes) <= reference_median * SAFE_RULE["median_runtime_ratio_max"]
        and max(runtimes) <= reference_median * SAFE_RULE["individual_runtime_ratio_max"]
    )


def _boundary_interval(
    lattice: Sequence[int],
    *,
    selected: int | None,
    tested_accepted: set[int],
    tested_rejected: set[int],
) -> dict[str, Any]:
    lower_neighbor = None if selected is None or selected == lattice[0] else lattice[lattice.index(selected) - 1]
    neighbor_tested = lower_neighbor is None or lower_neighbor in tested_accepted or lower_neighbor in tested_rejected
    lower_rejected = lower_neighbor if lower_neighbor in tested_rejected else None
    ordinary_supported = selected is not None and selected in tested_accepted and neighbor_tested and (
        lower_neighbor is None or lower_rejected is not None
    )
    return {
        "largest_tested_rejected": max((value for value in tested_rejected if selected is None or value < selected), default=None),
        "smallest_tested_accepted": min(tested_accepted) if tested_accepted else None,
        "selected_verified_safe": selected,
        "interval_notation": (
            f"({lower_rejected}, {selected}]" if ordinary_supported and lower_rejected is not None
            else f"(-infinity, {selected}] within tested lattice" if ordinary_supported and lower_neighbor is None
            else None
        ),
        "lower_exclusive": lower_rejected,
        "upper_inclusive": selected if ordinary_supported else None,
        "one_sided": ordinary_supported and lower_neighbor is None,
        "selected_point_tested": selected in tested_accepted if selected is not None else False,
        "immediate_lower_neighbor": lower_neighbor,
        "immediate_lower_neighbor_tested": neighbor_tested,
        "ordinary_interval_supported": ordinary_supported,
        "exact_minimum_claimed": False,
        "lattice_minimum": lattice[0],
        "lattice_maximum": lattice[-1],
        "tested_accepted": sorted(tested_accepted),
        "tested_rejected": sorted(tested_rejected),
    }


def _empty_envelope(workload: Mapping[str, Any], status: str, reasons: set[str], rows: Sequence[TrialObservation], *,
                    reference_median: float | None = None, reference_spread: float | None = None) -> SafeEnvelope:
    return SafeEnvelope(
        family_id=str(workload["family_id"]),
        workload_instance_id=str(workload["workload_instance_id"]),
        workload_fingerprint=workload_fingerprint(workload),
        status=status,
        cpu_selected_m=None,
        memory_selected_mib=None,
        cpu_minimum_interval=_boundary_interval(CPU_LATTICE_M, selected=None, tested_accepted=set(), tested_rejected=set()),
        memory_minimum_interval=_boundary_interval(MEMORY_LATTICE_MIB, selected=None, tested_accepted=set(), tested_rejected=set()),
        reference_median_runtime_seconds=reference_median,
        reference_runtime_relative_spread=reference_spread,
        reference_stability_threshold=SAFE_RULE["reference_max_relative_spread"],
        reference_stability_rule_version=SAFE_RULE["reference_stability_rule_version"],
        joint_successes=0,
        joint_trials=0,
        joint_success_wilson_95=wilson_interval(0, 0),
        manual_review_status="REQUIRED",
        eligible_for_comparison=False,
        reason_codes=tuple(sorted(reasons)),
        source_run_ids=tuple(row.run_id for row in rows),
    )


def _probe_cells(rows: Sequence[TrialObservation], *, phase: str, axis: str,
                 reference_median: float, memory_selected: int | None = None) -> tuple[set[int], set[int], set[str]]:
    grouped: dict[int, list[TrialObservation]] = defaultdict(list)
    for row in rows:
        if row.phase != phase or (memory_selected is not None and row.memory_mib != memory_selected):
            continue
        grouped[row.memory_mib if axis == "memory" else row.cpu_m].append(row)
    accepted: set[int] = set()
    rejected: set[int] = set()
    reasons: set[str] = set()
    for value, cell in grouped.items():
        valid = _valid(cell)
        basic = [trial_basic_success(item) for item in valid]
        if basic and any(basic) and not all(basic):
            reasons.add(f"MIXED_{axis.upper()}_PROBE_OUTCOME")
        if cell_acceptable(cell, reference_median, SAFE_RULE["probe_repeats"]):
            accepted.add(value)
        else:
            rejected.add(value)
    return accepted, rejected, reasons


def _derive_family(workload: Mapping[str, Any], rows: Sequence[TrialObservation]) -> SafeEnvelope:
    reasons: set[str] = set()
    if any(row.infrastructure_invalid and row.replacement_of is not None for row in rows):
        reasons.add("INFRASTRUCTURE_REPLACEMENT_EXHAUSTED")
    expected_fingerprint = workload_fingerprint(workload)
    if any(
        row.workload_instance_id != workload["workload_instance_id"]
        or row.workload_fingerprint != expected_fingerprint
        for row in rows
    ):
        reasons.add("WORKLOAD_INSTANCE_FINGERPRINT_MISMATCH")
        return _empty_envelope(workload, "WORKLOAD_INSTANCE_MISMATCH_REQUIRES_REVIEW", reasons, rows)

    reference = _valid(row for row in rows if row.phase == "reference")
    if len(reference) != SAFE_RULE["reference_repeats"] or not all(trial_basic_success(row) for row in reference):
        reasons.add("REFERENCE_NOT_SAFE_3_OF_3")
        return _empty_envelope(workload, "NO_SAFE_BOUND_WITHIN_SEARCH_SPACE", reasons, rows)
    reference_runtimes = [float(row.runtime_seconds) for row in reference if row.runtime_seconds is not None]
    reference_median = statistics.median(reference_runtimes)
    stable, reference_spread = reference_is_stable(reference_runtimes)
    if not stable:
        reasons.add(REFERENCE_UNSTABLE)
        return _empty_envelope(
            workload, REFERENCE_UNSTABLE, reasons, rows,
            reference_median=reference_median, reference_spread=reference_spread,
        )

    memory_safe, memory_rejected, memory_reasons = _probe_cells(
        rows, phase="memory_probe", axis="memory", reference_median=reference_median,
    )
    reasons.update(memory_reasons)
    memory_selected = min(memory_safe) if memory_safe else None
    memory_interval = _boundary_interval(
        MEMORY_LATTICE_MIB, selected=memory_selected,
        tested_accepted=memory_safe, tested_rejected=memory_rejected,
    )
    if any(accepted < rejected for accepted in memory_safe for rejected in memory_rejected):
        reasons.add(NON_MONOTONIC)
    if MEMORY_LATTICE_MIB[-1] in memory_rejected and not memory_safe:
        reasons.add("NO_SAFE_MEMORY_WITHIN_SEARCH_SPACE")
    if memory_selected is None or not memory_interval["ordinary_interval_supported"]:
        reasons.add("MEMORY_LOCAL_BOUNDARY_EVIDENCE_INCOMPLETE")

    cpu_safe: set[int] = set()
    cpu_rejected: set[int] = set()
    if memory_selected is not None:
        cpu_safe, cpu_rejected, cpu_reasons = _probe_cells(
            rows, phase="cpu_probe", axis="cpu", reference_median=reference_median,
            memory_selected=memory_selected,
        )
        reasons.update(cpu_reasons)
    cpu_selected = min(cpu_safe) if cpu_safe else None
    cpu_interval = _boundary_interval(
        CPU_LATTICE_M, selected=cpu_selected,
        tested_accepted=cpu_safe, tested_rejected=cpu_rejected,
    )
    if any(accepted < rejected for accepted in cpu_safe for rejected in cpu_rejected):
        reasons.add(NON_MONOTONIC)
    if CPU_LATTICE_M[-1] in cpu_rejected and not cpu_safe:
        reasons.add("NO_SAFE_CPU_WITHIN_SEARCH_SPACE")
    if cpu_selected is None or not cpu_interval["ordinary_interval_supported"]:
        reasons.add("CPU_LOCAL_BOUNDARY_EVIDENCE_INCOMPLETE")

    joint = _valid(
        row for row in rows
        if row.phase == "joint_verification"
        and row.cpu_m == cpu_selected and row.memory_mib == memory_selected
    )
    joint_success = sum(trial_basic_success(row) for row in joint)
    joint_safe = (
        cpu_selected is not None
        and memory_selected is not None
        and memory_interval["ordinary_interval_supported"]
        and cpu_interval["ordinary_interval_supported"]
        and cell_acceptable(joint, reference_median, SAFE_RULE["joint_verification_repeats"])
        and joint_success == SAFE_RULE["required_joint_successes"]
    )
    if not joint_safe:
        reasons.add("JOINT_VERIFICATION_NOT_SAFE_5_OF_5")
    blocking = bool(reasons)
    status = "CALIBRATED_PENDING_REVIEW" if joint_safe and not blocking else (
        NON_MONOTONIC if NON_MONOTONIC in reasons else "NO_SAFE_BOUND_WITHIN_SEARCH_SPACE"
    )
    return SafeEnvelope(
        family_id=str(workload["family_id"]),
        workload_instance_id=str(workload["workload_instance_id"]),
        workload_fingerprint=expected_fingerprint,
        status=status,
        cpu_selected_m=cpu_selected if joint_safe and not blocking else None,
        memory_selected_mib=memory_selected if joint_safe and not blocking else None,
        cpu_minimum_interval=cpu_interval,
        memory_minimum_interval=memory_interval,
        reference_median_runtime_seconds=reference_median,
        reference_runtime_relative_spread=reference_spread,
        reference_stability_threshold=SAFE_RULE["reference_max_relative_spread"],
        reference_stability_rule_version=SAFE_RULE["reference_stability_rule_version"],
        joint_successes=joint_success,
        joint_trials=len(joint),
        joint_success_wilson_95=wilson_interval(joint_success, len(joint)),
        manual_review_status="PENDING" if joint_safe and not blocking else "REQUIRED",
        eligible_for_comparison=False,
        reason_codes=tuple(sorted(reasons)),
        source_run_ids=tuple(row.run_id for row in rows),
    )


def derive_safe_envelopes(manifest: Mapping[str, Any], observations: Sequence[TrialObservation]) -> dict[str, Any]:
    validated = validate_resource_manifest(manifest)
    by_family: dict[str, list[TrialObservation]] = defaultdict(list)
    for observation in observations:
        by_family[observation.family_id].append(observation)
    envelopes = [
        _derive_family(workload, by_family.get(workload["family_id"], []))
        for workload in validated["workloads"]
    ]
    return {
        "schema_version": DERIVATION_SCHEMA_VERSION,
        "protocol_version": "5.0.0",
        "experiment_id": "E4",
        "safe_rule_version": SAFE_RULE["version"],
        "reference_stability_rule": {
            "version": SAFE_RULE["reference_stability_rule_version"],
            "statistic": "(maximum runtime - minimum runtime) / median runtime",
            "maximum_inclusive": SAFE_RULE["reference_max_relative_spread"],
            "interpretation": "deterministic stability gate, not a hypothesis test",
        },
        "derivation": "tested-neighbor interval bounds plus strict joint 5-of-5 verification",
        "independent_unit": "frozen canonical executable workload instance, not the unlimited semantic family",
        "conditionality": {
            "memory": "estimated conditional on 2000m CPU",
            "cpu": "estimated conditional on the selected tested-safe memory bound",
            "joint": "the selected CPU-memory pair is then verified unchanged",
        },
        "repeat_interpretation": "identical-instance stability and runtime variability only; Wilson metadata is descriptive and not population inference",
        "primary_uncertainty": "interval-censored resource boundary",
        "family_count": len(envelopes),
        "eligible_family_count": 0,
        "manual_review_required_before_comparison": True,
        "envelopes": [envelope.to_dict() for envelope in envelopes],
    }


__all__ = [
    "DERIVATION_SCHEMA_VERSION", "NON_MONOTONIC", "REFERENCE_UNSTABLE",
    "cell_acceptable", "derive_safe_envelopes", "reference_is_stable",
    "reference_relative_spread", "trial_basic_success", "wilson_interval",
]
