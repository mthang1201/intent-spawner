#!/usr/bin/env python3
"""Independent stdlib-only recomputation for the Protocol-v5 E4 provenance audit.

This verifier intentionally imports no E4 analysis, statistics, capacity, oracle,
or package-validation implementation from the repository.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
import random
from pathlib import Path
from statistics import mean
from typing import Any


CONDITIONS = ("STATIC_LARGE", "P1_CATALOG", "P2_CATALOG", "P2_DYNAMIC")
CONTRASTS = (
    ("P2_CATALOG", "STATIC_LARGE"),
    ("P2_DYNAMIC", "STATIC_LARGE"),
    ("P2_DYNAMIC", "P2_CATALOG"),
    ("P2_CATALOG", "P1_CATALOG"),
)
PRIMARY = ("success_rate", "oom_rate", "cpu_cost_per_success", "memory_cost_per_success")
SECONDARY = (
    "timeout_rate", "pending_or_admission_rate", "runtime_error_rate", "correctness_rate",
    "correct_completion_rate", "incorrect_rate", "runtime_seconds", "cpu_request_ratio",
    "memory_request_ratio", "cpu_request_error_signed", "memory_request_error_signed",
    "cpu_limit_error_signed", "memory_limit_error_signed",
)
PARETO = {
    "maximize": ["success_rate", "correct_completion_rate"],
    "minimize": [
        "cpu_cost_per_success", "memory_cost_per_success", "oom_rate", "timeout_rate",
        "pending_or_admission_rate", "runtime_error_rate", "incorrect_rate",
    ],
    "undefined_cost": "INDETERMINATE",
    "lower_cost_with_worse_reliability": "EFFICIENCY_RELIABILITY_TRADEOFF",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def nullable_mean(values: list[Any]) -> float | None:
    selected = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return mean(selected) if selected else None


def quantile(values: list[float], probability: float) -> float:
    selected = sorted(float(value) for value in values)
    position = (len(selected) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return selected[lower]
    fraction = position - lower
    return selected[lower] * (1 - fraction) + selected[upper] * fraction


def derive_seed(base_seed: int, *components: str) -> int:
    payload = {
        "algorithm": "sha256-canonical-json-first-64-bits-v1",
        "base_seed": base_seed,
        "components": list(components),
    }
    encoded = json.dumps(payload, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big")


def bootstrap_ci(rows: list[dict[str, Any]], endpoint: str, candidate: str, reference: str) -> list[float]:
    generator = random.Random(derive_seed(20260904, endpoint, candidate, reference))
    estimates = []
    for _ in range(2000):
        sampled = [rows[generator.randrange(len(rows))] for _ in rows]
        candidate_mean = sum(float(row[candidate]) for row in sampled) / len(sampled)
        reference_mean = sum(float(row[reference]) for row in sampled) / len(sampled)
        estimates.append(candidate_mean - reference_mean)
    return [quantile(estimates, 0.025), quantile(estimates, 0.975)]


def ranks(differences: list[float]) -> tuple[list[float], list[float]]:
    nonzero = [value for value in differences if abs(value) > 1e-12]
    absolute = [abs(value) for value in nonzero]
    order = sorted(range(len(absolute)), key=absolute.__getitem__)
    result = [0.0] * len(absolute)
    index = 0
    while index < len(order):
        end = index + 1
        while end < len(order) and abs(absolute[order[end]] - absolute[order[index]]) < 1e-12:
            end += 1
        average = (index + 1 + end) / 2.0
        for position in range(index, end):
            result[order[position]] = average
        index = end
    return nonzero, result


def effect_sizes(first: list[float], second: list[float]) -> dict[str, Any]:
    differences = [right - left for left, right in zip(first, second)]
    average = sum(differences) / len(differences)
    binary = all(value in (0.0, 1.0) for value in first + second)
    nonzero, assigned = ranks(differences)
    if not nonzero:
        biserial = 0.0
    else:
        positive = sum(rank for rank, value in zip(assigned, nonzero) if value > 0)
        negative = sum(rank for rank, value in zip(assigned, nonzero) if value < 0)
        biserial = (positive - negative) / (positive + negative)
    variance = sum((value - average) ** 2 for value in differences) / (len(differences) - 1)
    deviation = math.sqrt(variance)
    return {
        "effect_direction": "second_minus_first",
        "pairs": len(differences),
        "mean_difference": average,
        "risk_difference": average if binary else None,
        "median_paired_difference": quantile(differences, 0.5),
        "matched_pairs_rank_biserial": biserial,
        "cohens_dz": average / deviation if deviation > 1e-12 else None,
    }


def wilcoxon(first: list[float], second: list[float]) -> dict[str, Any]:
    differences, assigned = ranks([right - left for left, right in zip(first, second)])
    positive = sum(rank for rank, value in zip(assigned, differences) if value > 0)
    negative = sum(rank for rank, value in zip(assigned, differences) if value < 0)
    statistic = min(positive, negative)
    count = len(differences)
    if count == 0:
        positive = negative = statistic = z_score = 0.0
        p_value = 1.0
    elif count <= 15:
        tail = 0
        total = 2**count
        rank_sum = sum(assigned)
        for mask in range(total):
            perm_positive = sum(assigned[bit] for bit in range(count) if (mask >> bit) & 1)
            if min(perm_positive, rank_sum - perm_positive) <= statistic + 1e-9:
                tail += 1
        p_value, z_score = min(1.0, tail / total), 0.0
    else:
        tie_counts: list[int] = []
        absolute = sorted(abs(value) for value in differences)
        index = 0
        while index < count:
            end = index + 1
            while end < count and abs(absolute[end] - absolute[index]) < 1e-12:
                end += 1
            tie_counts.append(end - index)
            index = end
        expected = count * (count + 1) / 4.0
        variance = count * (count + 1) * (2 * count + 1) / 24.0
        variance -= sum(value**3 - value for value in tie_counts) / 48.0
        z_score = max(0.0, abs(positive - expected) - 0.5) / math.sqrt(max(1e-12, variance))
        p_value = min(1.0, math.erfc(z_score / math.sqrt(2)))
    return {
        "effect_direction": "second_minus_first",
        "effective_family_n": len(first),
        "pairs": len(first),
        "binary_outcome": False,
        "binary_outcome_detected": all(value in (0.0, 1.0) for value in first + second),
        "inference_status": "ELIGIBLE" if len(first) >= 10 else "WITHHELD_SMALL_N",
        "warning_codes": ["SMALL_EFFECTIVE_FAMILY_N"] if len(first) < 20 else [],
        "test_method": "wilcoxon_signed_rank",
        "alternative": "two_sided",
        "non_zero_pairs": count,
        "w_positive": round(positive, 4),
        "w_negative": round(negative, 4),
        "statistic": round(statistic, 4),
        "z_score": round(z_score, 6),
        "p_value_raw": float(f"{p_value:.12g}"),
    }


def holm(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    adjusted = [1.0] * len(values)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (len(values) - rank) * values[index]))
        adjusted[index] = running
    return adjusted


def primary_outcome(row: dict[str, Any]) -> str:
    if row["infrastructure_invalid"]:
        return "INFRASTRUCTURE_INVALID"
    for field, label in (
        ("pending_or_admission_failure", "PENDING_OR_ADMISSION_FAILURE"),
        ("oom", "OOM"), ("timeout", "TIMEOUT"), ("runtime_error", "RUNTIME_ERROR"),
    ):
        if row[field]:
            return label
    if row["success"]:
        return "SUCCESS"
    return "UNKNOWN_FAILURE"


def derived_rows(raw_rows: list[dict[str, Any]], envelopes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in raw_rows:
        planned = row["planned_resources"]
        runtime = row.get("workload_runtime_seconds")
        if runtime is None and row["scheduled"] and (row["oom"] or row["timeout"] or row["runtime_error"]):
            runtime = row.get("container_runtime_seconds")
        if not row["scheduled"]:
            runtime = 0.0
        metrics = row.get("cgroup_metrics") or {}
        cpu = metrics.get("mean_cpu_m", metrics.get("cpu_full_window_average_m"))
        peak = metrics.get("peak_memory_mib", metrics.get("memory_peak_mib"))
        envelope = envelopes.get(row["family_id"])
        values = {
            "primary_outcome": primary_outcome(row),
            "accounting_runtime_seconds": runtime,
            "observed_runtime_seconds": runtime if row["scheduled"] else None,
            "mean_cpu_m": cpu,
            "peak_memory_mib": peak,
            "cpu_request_ratio": None if cpu is None else cpu / planned["cpu_request_m"],
            "memory_request_ratio": None if peak is None else peak / planned["memory_request_mib"],
            "cpu_request_time_cpu_seconds": None if runtime is None else planned["cpu_request_m"] * runtime / 1000,
            "memory_request_time_mib_seconds": None if runtime is None else planned["memory_request_mib"] * runtime,
        }
        for axis, request_key, selected_key in (
            ("cpu", "cpu_request_m", "cpu_selected_m"),
            ("memory", "memory_request_mib", "memory_selected_mib"),
        ):
            selected = None if envelope is None else envelope.get(selected_key)
            values[f"{axis}_request_error_signed"] = None if selected is None else planned[request_key] - selected
            values[f"{axis}_limit_error_signed"] = None if selected is None else planned[request_key.replace("request", "limit")] - selected
        result.append({**row, **values})
    return result


def family_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["family_id"], row["condition"])].append(row)
    output = []
    for (family, condition), cell in sorted(grouped.items()):
        cell.sort(key=lambda row: row["repetition"])
        if len(cell) != 10 or [row["repetition"] for row in cell] != list(range(1, 11)):
            raise ValueError(f"incomplete cell {family}/{condition}")
        successes = sum(row["primary_outcome"] == "SUCCESS" for row in cell)
        output.append({
            "family_id": family,
            "condition": condition,
            "successful_tasks": successes,
            "family_analysis_complete": True,
            "success_rate": successes / len(cell),
            "correct_completion_rate": successes / len(cell),
            "oom_rate": sum(row["oom"] for row in cell) / len(cell),
            "timeout_rate": sum(row["timeout"] for row in cell) / len(cell),
            "pending_or_admission_rate": sum(row["pending_or_admission_failure"] for row in cell) / len(cell),
            "runtime_error_rate": sum(row["runtime_error"] for row in cell) / len(cell),
            "incorrect_rate": sum(row["correctness"] is False for row in cell) / len(cell),
            "correctness_rate": nullable_mean([None if row["correctness"] is None else int(row["correctness"]) for row in cell]),
            "runtime_seconds": nullable_mean([row["observed_runtime_seconds"] for row in cell]),
            "cpu_request_ratio": nullable_mean([row["cpu_request_ratio"] for row in cell]),
            "memory_request_ratio": nullable_mean([row["memory_request_ratio"] for row in cell]),
            "cpu_request_error_signed": nullable_mean([row["cpu_request_error_signed"] for row in cell]),
            "memory_request_error_signed": nullable_mean([row["memory_request_error_signed"] for row in cell]),
            "cpu_limit_error_signed": nullable_mean([row["cpu_limit_error_signed"] for row in cell]),
            "memory_limit_error_signed": nullable_mean([row["memory_limit_error_signed"] for row in cell]),
            "cpu_request_time_numerator_cpu_seconds": sum(row["cpu_request_time_cpu_seconds"] for row in cell),
            "memory_request_time_numerator_mib_seconds": sum(row["memory_request_time_mib_seconds"] for row in cell),
            "cpu_cost_per_success": sum(row["cpu_request_time_cpu_seconds"] for row in cell) / successes,
            "memory_cost_per_success": sum(row["memory_request_time_mib_seconds"] for row in cell) / successes,
        })
    return output


def condition_rows(families: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in families:
        grouped[row["condition"]].append(row)
    output = []
    for condition, rows in sorted(grouped.items()):
        successes = sum(row["successful_tasks"] for row in rows)
        summary = {
            "condition": condition,
            "family_rows": len(rows),
            "effective_family_n": len(rows),
            "successful_tasks": successes,
            "cpu_cost_per_success": sum(row["cpu_request_time_numerator_cpu_seconds"] for row in rows) / successes,
            "memory_cost_per_success": sum(row["memory_request_time_numerator_mib_seconds"] for row in rows) / successes,
            "cost_unavailable_reason": None,
            "failure_request_time_included": True,
            "cpu_cost_per_success_definition": "sum(cpu_request_m / 1000 * accounting_runtime_seconds for all valid attempts) / successful_tasks",
            "memory_cost_per_success_definition": "sum(memory_request_mib * accounting_runtime_seconds for all valid attempts) / successful_tasks",
        }
        for endpoint in (*PRIMARY[:2], *SECONDARY):
            summary[endpoint] = nullable_mean([row[endpoint] for row in rows])
        output.append(summary)
    return output


def statistics_rows(families: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index = {(row["family_id"], row["condition"]): row for row in families}
    family_ids = sorted({row["family_id"] for row in families})
    output = []
    for endpoint in (*PRIMARY, *SECONDARY):
        endpoint_rows = []
        for candidate, reference in CONTRASTS:
            paired = [
                {"family_id": family, reference: index[(family, reference)][endpoint], candidate: index[(family, candidate)][endpoint]}
                for family in family_ids
                if index[(family, reference)][endpoint] is not None and index[(family, candidate)][endpoint] is not None
            ]
            first = [row[reference] for row in paired]
            second = [row[candidate] for row in paired]
            endpoint_rows.append({
                "endpoint": endpoint,
                "endpoint_role": "primary" if endpoint in PRIMARY else "secondary",
                "candidate_condition": candidate,
                "reference_condition": reference,
                "effect_direction": "candidate_minus_reference",
                "effective_family_n": len(paired),
                "effect": effect_sizes(first, second),
                "ci_95_candidate_minus_reference": bootstrap_ci(paired, endpoint, candidate, reference),
                "test": wilcoxon(first, second),
            })
        adjusted = holm([row["test"]["p_value_raw"] for row in endpoint_rows])
        for row, value in zip(endpoint_rows, adjusted):
            row["test"]["p_value_holm_within_endpoint"] = value
        output.extend(endpoint_rows)
    return output


def dominant(bounds: dict[str, float]) -> str:
    finite = {key: value for key, value in bounds.items() if math.isfinite(value)}
    minimum = min(finite.values())
    tied = sorted(key for key, value in finite.items() if value == minimum)
    return "TIED:" + "+".join(tied) if len(tied) > 1 else tied[0]


def capacity_result(raw_rows: list[dict[str, Any]], capacity: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], set[tuple[Any, ...]]] = defaultdict(set)
    for row in raw_rows:
        resource = row["observed_resources"]
        grouped[(row["family_id"], row["condition"])].add((
            resource["cpu_request_m"], resource["cpu_limit_m"], resource["memory_request_mib"],
            resource["memory_limit_mib"], resource.get("gpu_count", 0), resource.get("gpu_resource"),
        ))
    if any(len(values) != 1 for values in grouped.values()):
        raise ValueError("observed request disagreement")
    resources = {
        key: {
            "cpu_request_m": next(iter(values))[0], "cpu_limit_m": next(iter(values))[1],
            "memory_request_mib": next(iter(values))[2], "memory_limit_mib": next(iter(values))[3],
            "gpu_count": next(iter(values))[4], "gpu_resource": next(iter(values))[5],
        }
        for key, values in grouped.items()
    }
    families = sorted({key[0] for key in resources})
    homogeneous = []
    for family in families:
        reference_resource = resources[(family, "STATIC_LARGE")]
        reference_bounds = {
            "CPU": capacity["cpu_m"] // reference_resource["cpu_request_m"],
            "MEMORY": capacity["memory_mib"] // reference_resource["memory_request_mib"],
            "GPU": math.inf,
        }
        reference = int(min(reference_bounds.values()))
        for condition in CONDITIONS:
            resource = resources[(family, condition)]
            bounds = {
                "CPU": capacity["cpu_m"] // resource["cpu_request_m"],
                "MEMORY": capacity["memory_mib"] // resource["memory_request_mib"],
                "GPU": math.inf,
            }
            density = int(min(bounds.values()))
            homogeneous.append({
                "family_id": family, "condition": condition, "schedulable_sessions": density,
                "capacity_gain_sessions_vs_static_large": density - reference,
                "capacity_gain_fraction_vs_static_large": density / reference - 1,
                "dominant_constraint": dominant(bounds),
            })
    balanced = []
    for condition in CONDITIONS:
        def pressure(family: str) -> tuple[float, float, str]:
            resource = resources[(family, condition)]
            parts = [resource["cpu_request_m"] / capacity["cpu_m"], resource["memory_request_mib"] / capacity["memory_mib"]]
            return -max(parts), -sum(parts), family
        bins: list[dict[str, Any]] = []
        for family in sorted(families, key=pressure):
            resource = resources[(family, condition)]
            for slot in bins:
                if slot["used_cpu_m"] + resource["cpu_request_m"] <= capacity["cpu_m"] and slot["used_memory_mib"] + resource["memory_request_mib"] <= capacity["memory_mib"]:
                    slot["families"].append(family)
                    slot["used_cpu_m"] += resource["cpu_request_m"]
                    slot["used_memory_mib"] += resource["memory_request_mib"]
                    break
            else:
                bins.append({"families": [family], "used_cpu_m": resource["cpu_request_m"], "used_memory_mib": resource["memory_request_mib"], "used_gpu_count": 0})
        for slot in bins:
            slot["dominant_constraint"] = dominant({
                "CPU": (capacity["cpu_m"] - slot["used_cpu_m"]) / capacity["cpu_m"],
                "MEMORY": (capacity["memory_mib"] - slot["used_memory_mib"]) / capacity["memory_mib"],
                "GPU": math.inf,
            })
        balanced.append({"condition": condition, "nodes_required": len(bins), "sessions": len(families), "mean_sessions_per_node": len(families) / len(bins), "bins": bins})
    reference = balanced[0]
    for row in balanced:
        row["node_reduction_vs_static_large"] = reference["nodes_required"] - row["nodes_required"]
        row["density_gain_vs_static_large"] = row["mean_sessions_per_node"] / reference["mean_sessions_per_node"] - 1
    return {
        "schema_version": "protocol-v5-resource-efficiency-capacity-result-v1.0.0",
        "evidence_label": "SIMULATED_DETERMINISTIC_REQUEST_PACKING",
        "evidence_type": "SIMULATED_CAPACITY",
        "capacity_source": "KUBERNETES_NODE_STATUS_ALLOCATABLE",
        "scheduler_input": "OBSERVED_POD_RESOURCE_REQUESTS",
        "concurrent_cluster_evidence": False,
        "status": "SIMULATED",
        "warning": "Request-packing simulation only; it is not real concurrent-cluster performance evidence.",
        "workload_mix": "ONE_SESSION_PER_EACH_OF_16_FROZEN_FAMILIES",
        "packing_algorithm": "multidimensional-first-fit-decreasing-v1",
        "tie_breaking": ["maximum_normalized_pressure_descending", "total_normalized_pressure_descending", "family_id_ascending"],
        "capacity": capacity,
        "homogeneous_family_density": homogeneous,
        "balanced_family_mix": balanced,
    }


def pareto_classification(candidate: dict[str, Any], reference: dict[str, Any]) -> str:
    minimize, maximize = PARETO["minimize"], PARETO["maximize"]
    no_worse = all(candidate[key] <= reference[key] for key in minimize) and all(candidate[key] >= reference[key] for key in maximize)
    any_better = any(candidate[key] < reference[key] for key in minimize) or any(candidate[key] > reference[key] for key in maximize)
    if no_worse and any_better:
        return "STRICT_FRONTIER_IMPROVEMENT"
    costs_better = any(candidate[key] < reference[key] for key in ("cpu_cost_per_success", "memory_cost_per_success"))
    reliability_worse = any(candidate[key] > reference[key] for key in minimize[2:]) or any(candidate[key] < reference[key] for key in maximize)
    if costs_better and reliability_worse:
        return "EFFICIENCY_RELIABILITY_TRADEOFF"
    if all(candidate[key] == reference[key] for key in minimize + maximize):
        return "EQUIVALENT"
    no_better = all(candidate[key] >= reference[key] for key in minimize) and all(candidate[key] <= reference[key] for key in maximize)
    any_worse = any(candidate[key] > reference[key] for key in minimize) or any(candidate[key] < reference[key] for key in maximize)
    if no_better and any_worse:
        return "DOMINATED"
    return "INDETERMINATE"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    raw = read_jsonl(args.raw / "raw" / "trials.jsonl")
    oracle_rows = read_json(args.oracle / "derived" / "safe-envelopes.json")["envelopes"]
    envelopes = {row["family_id"]: row for row in oracle_rows if row.get("cpu_selected_m") is not None and row.get("memory_selected_mib") is not None}
    derived = derived_rows(raw, envelopes)
    families = family_rows(derived)
    conditions = condition_rows(families)
    statistics = statistics_rows(families)
    capacity = read_json(args.freeze)["capacity_input"]["allocatable"]
    packing = capacity_result(raw, capacity)
    by_condition = {row["condition"]: row for row in conditions}
    pareto = [
        {"condition": condition, "reference": "STATIC_LARGE", "classification": pareto_classification(by_condition[condition], by_condition["STATIC_LARGE"])}
        for condition in ("P1_CATALOG", "P2_CATALOG", "P2_DYNAMIC")
    ]

    reported_derived = read_jsonl(args.analysis / "derived" / "trials.jsonl")
    metric_keys = (
        "primary_outcome", "accounting_runtime_seconds", "observed_runtime_seconds", "mean_cpu_m", "peak_memory_mib",
        "cpu_request_ratio", "memory_request_ratio", "cpu_request_time_cpu_seconds", "memory_request_time_mib_seconds",
    )
    error_key_map = {
        "cpu_request_error_signed": "cpu_request_allocation_error_signed",
        "memory_request_error_signed": "memory_request_allocation_error_signed",
        "cpu_limit_error_signed": "cpu_limit_allocation_error_signed",
        "memory_limit_error_signed": "memory_limit_allocation_error_signed",
    }
    derived_mismatches = []
    for expected, actual in zip(derived, reported_derived):
        mismatched = [key for key in metric_keys if expected[key] != actual[key]]
        mismatched.extend(
            expected_key for expected_key, actual_key in error_key_map.items()
            if expected[expected_key] != actual[actual_key]
        )
        if mismatched:
            derived_mismatches.append({"trial_id": expected["trial_id"], "fields": mismatched})

    reported_conditions = read_json(args.analysis / "derived" / "condition-summaries.json")["rows"]
    reported_statistics = read_json(args.analysis / "statistics" / "results.json")["rows"]
    reported_pareto = read_json(args.analysis / "report" / "pareto.json")["rows"]
    reported_packing = read_json(args.analysis / "capacity" / "simulation.json")
    nodes = [row["nodes_required"] for row in packing["balanced_family_mix"]]
    minimum_nodes = {
        condition: {
            "cpu_lower_bound": math.ceil(sum(next(iter({
                (row["observed_resources"]["cpu_request_m"], row["observed_resources"]["memory_request_mib"])
                for row in raw if row["family_id"] == family and row["condition"] == condition
            }))[0] for family in sorted({row["family_id"] for row in raw})) / capacity["cpu_m"]),
            "memory_lower_bound": math.ceil(sum(next(iter({
                (row["observed_resources"]["cpu_request_m"], row["observed_resources"]["memory_request_mib"])
                for row in raw if row["family_id"] == family and row["condition"] == condition
            }))[1] for family in sorted({row["family_id"] for row in raw})) / capacity["memory_mib"]),
        }
        for condition in CONDITIONS
    }
    exact = {
        "derived_metrics": len(derived) == len(reported_derived) == 640 and not derived_mismatches,
        "condition_summaries": conditions == reported_conditions,
        "statistics": statistics == reported_statistics,
        "pareto": pareto == reported_pareto,
        "packing": packing == reported_packing,
    }
    output = {
        "schema_version": "protocol-v5-e4-independent-recomputation-v1.0.0",
        "implementation_independence": "STDLIB_ONLY_NO_PROJECT_ANALYSIS_IMPORTS",
        "status": "PASS" if all(exact.values()) else "FAIL",
        "exact_matches": exact,
        "raw_trial_rows": len(raw),
        "derived_metric_rows_checked": len(derived),
        "derived_metric_mismatches": derived_mismatches,
        "statistical_rows_checked": len(statistics),
        "oracle_attempt_rows_claimed": read_json(args.oracle / "manifest.json").get("trial_count"),
        "oracle_safe_envelope_count": len(envelopes),
        "conditions": conditions,
        "p2_dynamic_reduction_vs_static_large": {
            "cpu_cost_per_success_fraction": 1 - by_condition["P2_DYNAMIC"]["cpu_cost_per_success"] / by_condition["STATIC_LARGE"]["cpu_cost_per_success"],
            "memory_cost_per_success_fraction": 1 - by_condition["P2_DYNAMIC"]["memory_cost_per_success"] / by_condition["STATIC_LARGE"]["memory_cost_per_success"],
        },
        "pareto": pareto,
        "packing_nodes_by_condition": dict(zip(CONDITIONS, nodes)),
        "packing_lower_bounds": minimum_nodes,
        "requested_plan_packing_target": dict(zip(CONDITIONS, [4, 1, 2, 2])),
        "requested_plan_packing_target_matches_raw": nodes == [4, 1, 2, 2],
        "requested_plan_packing_target_feasible": all(
            max(bounds.values()) <= target
            for bounds, target in zip(minimum_nodes.values(), [4, 1, 2, 2])
        ),
    }
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": output["status"], "exact_matches": exact,
        "packing_nodes_by_condition": output["packing_nodes_by_condition"],
        "requested_target_matches": output["requested_plan_packing_target_matches_raw"],
        "requested_target_feasible": output["requested_plan_packing_target_feasible"],
    }, sort_keys=True))
    return 0 if all(exact.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
