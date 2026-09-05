"""Deterministic request-only capacity simulations for E4 comparison."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .efficiency_contracts import CONDITIONS, FAMILY_COUNT, REPETITIONS


LABEL = "SIMULATED_DETERMINISTIC_REQUEST_PACKING"
EVIDENCE_TYPE = "SIMULATED_CAPACITY"
CAPACITY_SOURCE = "KUBERNETES_NODE_STATUS_ALLOCATABLE"
SCHEDULER_INPUT = "OBSERVED_POD_RESOURCE_REQUESTS"


def _dominant(bounds: Mapping[str, float]) -> str:
    finite = {key: value for key, value in bounds.items() if math.isfinite(value)}
    minimum = min(finite.values())
    tied = sorted(key for key, value in finite.items() if value == minimum)
    return "TIED:" + "+".join(tied) if len(tied) > 1 else tied[0]


def _density(resource: Mapping[str, int], capacity: Mapping[str, int]) -> tuple[int, str]:
    bounds = {
        "CPU": capacity["cpu_m"] // resource["cpu_request_m"],
        "MEMORY": capacity["memory_mib"] // resource["memory_request_mib"],
        "GPU": math.inf if resource.get("gpu_count", 0) == 0 else capacity["gpu_count"] // resource["gpu_count"],
    }
    return int(min(bounds.values())), _dominant(bounds)


def verify_observed_requests(trials: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[int, tuple[int, int, int, int, int, str | None]]] = {}
    for row in trials:
        if row.get("infrastructure_invalid"):
            continue
        observed = row.get("observed_resources")
        if not isinstance(observed, Mapping):
            raise ValueError("capacity simulation requires resources read back from every observed pod specification")
        key = (str(row["family_id"]), str(row["condition"]))
        repetition = row.get("repetition")
        values = (
            observed.get("cpu_request_m"), observed.get("cpu_limit_m"),
            observed.get("memory_request_mib"), observed.get("memory_limit_mib"),
            observed.get("gpu_count", 0), observed.get("gpu_resource"),
        )
        if (
            isinstance(repetition, bool) or not isinstance(repetition, int)
            or any(isinstance(value, bool) or not isinstance(value, int) for value in values[:5])
            or (values[5] is not None and not isinstance(values[5], str))
        ):
            raise ValueError("observed pod resources must be exact integer quantities")
        repetitions = grouped.setdefault(key, {})
        if repetition in repetitions:
            raise ValueError(f"duplicate valid observed request for repetition {repetition} in {key}")
        repetitions[repetition] = values
    resources: dict[tuple[str, str], dict[str, Any]] = {}
    for key, repetition_values in grouped.items():
        if set(repetition_values) != set(range(1, REPETITIONS + 1)):
            raise ValueError(f"observed requests are incomplete across repetitions for {key}")
        values = set(repetition_values.values())
        if len(values) != 1:
            raise ValueError(f"observed requests disagree across repetitions for {key}")
        cpu_request, cpu_limit, memory_request, memory_limit, gpu, gpu_resource = next(iter(values))
        resources[key] = {
            "cpu_request_m": cpu_request, "cpu_limit_m": cpu_limit,
            "memory_request_mib": memory_request, "memory_limit_mib": memory_limit,
            "gpu_count": gpu, "gpu_resource": gpu_resource,
        }
    families = {key[0] for key in resources}
    if len(families) != FAMILY_COUNT or set(resources) != {(family, condition) for family in families for condition in CONDITIONS}:
        raise ValueError(f"capacity simulation requires all {FAMILY_COUNT} family-condition cells")
    return resources


def _pack_condition(
    condition: str, families: Sequence[str], resources: Mapping[tuple[str, str], Mapping[str, int]], capacity: Mapping[str, int],
) -> dict[str, Any]:
    def pressures(family: str) -> tuple[float, float, str]:
        resource = resources[(family, condition)]
        parts = [resource["cpu_request_m"] / capacity["cpu_m"], resource["memory_request_mib"] / capacity["memory_mib"]]
        if resource.get("gpu_count", 0):
            if capacity["gpu_count"] <= 0:
                parts.append(math.inf)
            else:
                parts.append(resource["gpu_count"] / capacity["gpu_count"])
        return (-max(parts), -sum(parts), family)

    bins: list[dict[str, Any]] = []
    for family in sorted(families, key=pressures):
        resource = resources[(family, condition)]
        placed = False
        for slot in bins:
            if (
                slot["used_cpu_m"] + resource["cpu_request_m"] <= capacity["cpu_m"]
                and slot["used_memory_mib"] + resource["memory_request_mib"] <= capacity["memory_mib"]
                and slot["used_gpu_count"] + resource.get("gpu_count", 0) <= capacity["gpu_count"]
            ):
                slot["families"].append(family)
                slot["used_cpu_m"] += resource["cpu_request_m"]
                slot["used_memory_mib"] += resource["memory_request_mib"]
                slot["used_gpu_count"] += resource.get("gpu_count", 0)
                placed = True
                break
        if not placed:
            if resource["cpu_request_m"] > capacity["cpu_m"] or resource["memory_request_mib"] > capacity["memory_mib"] or resource.get("gpu_count", 0) > capacity["gpu_count"]:
                raise ValueError(f"{family}/{condition} does not fit on the frozen node")
            bins.append({"families": [family], "used_cpu_m": resource["cpu_request_m"], "used_memory_mib": resource["memory_request_mib"], "used_gpu_count": resource.get("gpu_count", 0)})
    for slot in bins:
        remaining_bounds = {
            "CPU": (capacity["cpu_m"] - slot["used_cpu_m"]) / capacity["cpu_m"],
            "MEMORY": (capacity["memory_mib"] - slot["used_memory_mib"]) / capacity["memory_mib"],
            "GPU": math.inf if slot["used_gpu_count"] == 0 else (capacity["gpu_count"] - slot["used_gpu_count"]) / capacity["gpu_count"],
        }
        slot["dominant_constraint"] = _dominant(remaining_bounds)
    return {"condition": condition, "nodes_required": len(bins), "sessions": len(families), "mean_sessions_per_node": len(families) / len(bins), "bins": bins}


def simulate_capacity(trials: Sequence[Mapping[str, Any]], capacity_contract: Mapping[str, Any]) -> dict[str, Any]:
    common = {
        "schema_version": "protocol-v5-resource-efficiency-capacity-result-v1.0.0",
        "evidence_label": LABEL,
        "evidence_type": EVIDENCE_TYPE,
        "capacity_source": CAPACITY_SOURCE,
        "scheduler_input": SCHEDULER_INPUT,
        "concurrent_cluster_evidence": False,
    }
    if (
        capacity_contract.get("evidence_type") != EVIDENCE_TYPE
        or capacity_contract.get("capacity_source") != CAPACITY_SOURCE
        or capacity_contract.get("physical_capacity_permitted") is not False
    ):
        return {**common, "status": "NOT_EXECUTED", "reason": "CAPACITY_CONTRACT_IS_NOT_ALLOCATABLE_ONLY"}
    if capacity_contract.get("freeze_status") != "FROZEN":
        return {**common, "status": "NOT_EXECUTED", "reason": "NODE_CAPACITY_NOT_FROZEN"}
    capacity = dict(capacity_contract["allocatable"])
    try:
        resources = verify_observed_requests(trials)
    except ValueError as exc:
        return {**common, "status": "NOT_EXECUTED", "reason": "OBSERVED_REQUESTS_INCOMPLETE_OR_INCONSISTENT", "detail": str(exc)}
    families = sorted({key[0] for key in resources})
    homogeneous: list[dict[str, Any]] = []
    for family in families:
        reference, _ = _density(resources[(family, "STATIC_LARGE")], capacity)
        for condition in CONDITIONS:
            density, dominant = _density(resources[(family, condition)], capacity)
            homogeneous.append({
                "family_id": family, "condition": condition, "schedulable_sessions": density,
                "capacity_gain_sessions_vs_static_large": density - reference,
                "capacity_gain_fraction_vs_static_large": None if reference == 0 else density / reference - 1,
                "dominant_constraint": dominant,
            })
    balanced = [_pack_condition(condition, families, resources, capacity) for condition in CONDITIONS]
    reference = next(row for row in balanced if row["condition"] == "STATIC_LARGE")
    for row in balanced:
        row["node_reduction_vs_static_large"] = reference["nodes_required"] - row["nodes_required"]
        row["density_gain_vs_static_large"] = row["mean_sessions_per_node"] / reference["mean_sessions_per_node"] - 1
    return {
        **common, "status": "SIMULATED",
        "warning": "Request-packing simulation only; it is not real concurrent-cluster performance evidence.",
        "workload_mix": "ONE_SESSION_PER_EACH_OF_16_FROZEN_FAMILIES",
        "packing_algorithm": "multidimensional-first-fit-decreasing-v1",
        "tie_breaking": ["maximum_normalized_pressure_descending", "total_normalized_pressure_descending", "family_id_ascending"],
        "capacity": capacity, "homogeneous_family_density": homogeneous, "balanced_family_mix": balanced,
    }


__all__ = ["CAPACITY_SOURCE", "EVIDENCE_TYPE", "LABEL", "SCHEDULER_INPUT", "simulate_capacity", "verify_observed_requests"]
