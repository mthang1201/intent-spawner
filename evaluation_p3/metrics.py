"""Paired quality, correctness, latency, usage, and transition metrics."""

from __future__ import annotations

from collections import Counter
import math
import statistics
from typing import Any, Mapping, Sequence


METRICS_SCHEMA_VERSION = "p2-p3-aggregate-metrics-v1.0.0"
PAIRED_CHANGES_SCHEMA_VERSION = "p2-p3-paired-changes-v1.1.0"
TRANSITIONS_SCHEMA_VERSION = "p2-p3-error-transitions-v1.1.0"
SYSTEMS = ("p2", "p3")
TRANSITIONS = (
    "p2_wrong_to_p3_correct",
    "p2_correct_to_p3_correct",
    "p2_correct_to_p3_wrong",
    "p2_wrong_to_p3_wrong",
)


def _rate(numerator: int | float, denominator: int) -> float | None:
    return float(numerator) / denominator if denominator else None


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _reciprocal_rank(ranked: Sequence[str], acceptable: set[str]) -> float:
    for rank, candidate_id in enumerate(ranked, start=1):
        if candidate_id in acceptable:
            return 1.0 / rank
    return 0.0


def _ndcg(ranked: Sequence[str], acceptable: set[str], k: int = 5) -> float:
    if not acceptable:
        return 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, candidate_id in enumerate(ranked[:k], start=1)
        if candidate_id in acceptable
    )
    ideal_count = min(len(acceptable), k)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return dcg / ideal if ideal else 0.0


def _rank(ranked: Sequence[str], candidates: set[str]) -> int | None:
    return next(
        (rank for rank, candidate_id in enumerate(ranked, start=1) if candidate_id in candidates),
        None,
    )


def _prediction_index(
    predictions: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    index = {(item["system"], item["sample_id"]): item for item in predictions}
    if len(index) != len(predictions):
        raise ValueError("prediction system/sample pairs must be unique")
    return index


def _system_metrics(
    system: str,
    dataset: Mapping[str, Any],
    index: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    preferred_correct = 0
    preferred_denominator = 0
    acceptable_correct = 0
    acceptable_denominator = 0
    reciprocal_ranks: list[float] = []
    ndcg_values: list[float] = []
    violation_count = 0
    violation_denominator = 0
    latencies: list[float] = []

    for item in dataset["items"]:
        prediction = index[(system, item["sample_id"])]
        ranked = list(prediction["ranked_candidate_ids"])
        gold = item["gold"]
        acceptable = set(gold["acceptable_candidate_ids"])
        preferred = gold["preferred_candidate_id"]
        if preferred is not None:
            preferred_denominator += 1
            preferred_correct += int(bool(ranked) and ranked[0] == preferred)
        if acceptable:
            acceptable_denominator += 1
            acceptable_correct += int(bool(ranked) and ranked[0] in acceptable)
            reciprocal_ranks.append(_reciprocal_rank(ranked, acceptable))
            ndcg_values.append(_ndcg(ranked, acceptable))
        if gold["request_feasible"]:
            violation_denominator += 1
            violation_count += int(bool(prediction["constraint_violated"]))
        latency = prediction.get("latency_seconds")
        if isinstance(latency, (int, float)) and math.isfinite(float(latency)):
            latencies.append(float(latency))

    return {
        "sample_count": len(dataset["items"]),
        "top1_accuracy": {
            "value": _rate(preferred_correct, preferred_denominator),
            "numerator": preferred_correct,
            "denominator": preferred_denominator,
            "definition": "rank-1 candidate equals the single preferred candidate",
        },
        "acceptable_candidate_accuracy": {
            "value": _rate(acceptable_correct, acceptable_denominator),
            "numerator": acceptable_correct,
            "denominator": acceptable_denominator,
            "definition": "rank-1 candidate belongs to the gold acceptable set",
        },
        "mrr": {
            "value": statistics.fmean(reciprocal_ranks) if reciprocal_ranks else None,
            "denominator": acceptable_denominator,
        },
        "ndcg_at_5": {
            "value": statistics.fmean(ndcg_values) if ndcg_values else None,
            "denominator": acceptable_denominator,
            "relevance": "binary over the gold acceptable-candidate set",
        },
        "hard_constraint_violation_rate": {
            "value": _rate(violation_count, violation_denominator),
            "numerator": violation_count,
            "denominator": violation_denominator,
        },
        "latency_seconds": {
            "count": len(latencies),
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "mean": statistics.fmean(latencies) if latencies else None,
            "minimum": min(latencies) if latencies else None,
            "maximum": max(latencies) if latencies else None,
        },
    }


def _p3_operational_metrics(
    dataset: Mapping[str, Any],
    index: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    predictions = [index[("p3", item["sample_id"])] for item in dataset["items"]]
    invoked = [item for item in predictions if item.get("reranker_invoked")]
    invalid = [item for item in invoked if item.get("invalid_reranker_output")]
    provider_failures = [item for item in invoked if item.get("provider_failure")]
    degraded = [item for item in invoked if item.get("reranker_degraded")]
    outside = [item for item in invoked if item.get("selected_outside_p2_feasible")]
    reasons = Counter(
        str(item.get("reranker_degraded_reason"))
        for item in invoked
        if item.get("reranker_degraded_reason") is not None
    )

    prompt_tokens = [
        int(item["prompt_tokens"])
        for item in invoked
        if isinstance(item.get("prompt_tokens"), int)
        and not isinstance(item.get("prompt_tokens"), bool)
    ]
    completion_tokens = [
        int(item["completion_tokens"])
        for item in invoked
        if isinstance(item.get("completion_tokens"), int)
        and not isinstance(item.get("completion_tokens"), bool)
    ]
    total_tokens = [
        int(item["total_tokens"])
        for item in invoked
        if isinstance(item.get("total_tokens"), int)
        and not isinstance(item.get("total_tokens"), bool)
    ]
    costs = [
        float(item["estimated_cost_usd"])
        for item in invoked
        if isinstance(item.get("estimated_cost_usd"), (int, float))
        and not isinstance(item.get("estimated_cost_usd"), bool)
    ]
    pricing_ids = sorted(
        {str(item["pricing_id"]) for item in invoked if item.get("pricing_id")}
    )

    def usage(values: Sequence[int]) -> dict[str, Any]:
        return {
            "coverage_count": len(values),
            "sum": sum(values) if values else None,
            "p50": _percentile([float(value) for value in values], 0.50),
            "p95": _percentile([float(value) for value in values], 0.95),
        }

    return {
        "reranker_invocations": len(invoked),
        "reranker_not_invoked": len(predictions) - len(invoked),
        "invalid_reranker_output_rate": {
            "value": _rate(len(invalid), len(invoked)),
            "numerator": len(invalid),
            "denominator": len(invoked),
        },
        "provider_failure_rate": {
            "value": _rate(len(provider_failures), len(invoked)),
            "numerator": len(provider_failures),
            "denominator": len(invoked),
        },
        "provider_failure_or_fallback_rate": {
            "value": _rate(len(degraded), len(invoked)),
            "numerator": len(degraded),
            "denominator": len(invoked),
        },
        "degraded_reason_counts": dict(sorted(reasons.items())),
        "selected_outside_p2_feasible_rate": {
            "value": _rate(len(outside), len(invoked)),
            "numerator": len(outside),
            "denominator": len(invoked),
        },
        "token_usage": {
            "prompt_tokens": usage(prompt_tokens),
            "completion_tokens": usage(completion_tokens),
            "total_tokens": usage(total_tokens),
        },
        "external_cost_usd": {
            "value": sum(costs) if costs else None,
            "coverage_count": len(costs),
            "invocation_count": len(invoked),
            "pricing_ids": pricing_ids,
            "unavailable_reason": (
                None
                if costs
                else "no applicable external pricing provenance was configured"
            ),
        },
    }


def paired_analysis(
    dataset: Mapping[str, Any],
    predictions: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index = _prediction_index(predictions)
    changes: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    samples: dict[str, list[str]] = {name: [] for name in TRANSITIONS}

    for item in dataset["items"]:
        sample_id = item["sample_id"]
        p2 = index[("p2", sample_id)]
        p3 = index[("p3", sample_id)]
        p2_ranked = list(p2["ranked_candidate_ids"])
        p3_ranked = list(p3["ranked_candidate_ids"])
        acceptable = set(item["gold"]["acceptable_candidate_ids"])
        preferred = item["gold"]["preferred_candidate_id"]
        p2_acceptable = bool(p2_ranked and p2_ranked[0] in acceptable)
        p3_acceptable = bool(p3_ranked and p3_ranked[0] in acceptable)
        if item["gold"]["request_feasible"]:
            p2_correct = p2_acceptable
            p3_correct = p3_acceptable
        else:
            p2_correct = bool(p2.get("detected_infeasible"))
            p3_correct = bool(
                p3.get(
                    "detected_infeasible",
                    p2_correct
                    and not p3.get("reranker_invoked")
                    and not p3_ranked,
                )
            )
        if not p2_correct and p3_correct:
            transition = "p2_wrong_to_p3_correct"
        elif p2_correct and p3_correct:
            transition = "p2_correct_to_p3_correct"
        elif p2_correct and not p3_correct:
            transition = "p2_correct_to_p3_wrong"
        else:
            transition = "p2_wrong_to_p3_wrong"
        counts[transition] += 1
        samples[transition].append(sample_id)
        changes.append(
            {
                "sample_id": sample_id,
                "workload_family": item["workload_family"],
                "request_feasible": item["gold"]["request_feasible"],
                "preferred_candidate_id": preferred,
                "acceptable_candidate_ids": sorted(acceptable),
                "p2_top1_candidate_id": p2_ranked[0] if p2_ranked else None,
                "p3_top1_candidate_id": p3_ranked[0] if p3_ranked else None,
                "p2_preferred_correct": bool(
                    preferred is not None and p2_ranked and p2_ranked[0] == preferred
                ),
                "p3_preferred_correct": bool(
                    preferred is not None and p3_ranked and p3_ranked[0] == preferred
                ),
                "p2_acceptable_correct": p2_acceptable,
                "p3_acceptable_correct": p3_acceptable,
                "p2_query_correct": p2_correct,
                "p3_query_correct": p3_correct,
                "p2_first_acceptable_rank": _rank(p2_ranked, acceptable),
                "p3_first_acceptable_rank": _rank(p3_ranked, acceptable),
                "top1_changed": (p2_ranked[:1] != p3_ranked[:1]),
                "ranking_changed": p2_ranked != p3_ranked,
                "transition": transition,
                "latency_delta_seconds": float(p3["latency_seconds"])
                - float(p2["latency_seconds"]),
                "reranker_degraded": bool(p3.get("reranker_degraded")),
                "reranker_degraded_reason": p3.get("reranker_degraded_reason"),
                "prompt_tokens": p3.get("prompt_tokens"),
                "completion_tokens": p3.get("completion_tokens"),
                "estimated_cost_usd": p3.get("estimated_cost_usd"),
            }
        )

    transitions = {
        "schema_version": TRANSITIONS_SCHEMA_VERSION,
        "correctness_definition": (
            "for feasible queries, correct means rank 1 belongs to the gold "
            "acceptable set; for infeasible queries, correct means the request "
            "is detected as infeasible without invoking reranking"
        ),
        "counts": {name: counts[name] for name in TRANSITIONS},
        "sample_ids": samples,
        "net_corrections": (
            counts["p2_wrong_to_p3_correct"] - counts["p2_correct_to_p3_wrong"]
        ),
    }
    return changes, transitions


def aggregate_metrics(
    dataset: Mapping[str, Any],
    predictions: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    index = _prediction_index(predictions)
    expected = {
        (system, item["sample_id"])
        for system in SYSTEMS
        for item in dataset["items"]
    }
    if set(index) != expected:
        raise ValueError("predictions must contain exactly one P2 and P3 record per query")
    systems = {
        system: _system_metrics(system, dataset, index) for system in SYSTEMS
    }
    changes, transitions = paired_analysis(dataset, predictions)

    def delta(path: str) -> float | None:
        p2 = systems["p2"][path]["value"]
        p3 = systems["p3"][path]["value"]
        return float(p3) - float(p2) if p2 is not None and p3 is not None else None

    metrics = {
        "schema_version": METRICS_SCHEMA_VERSION,
        "primary_systems": list(SYSTEMS),
        "systems": systems,
        "incremental_delta_p3_minus_p2": {
            "top1_accuracy": delta("top1_accuracy"),
            "acceptable_candidate_accuracy": delta("acceptable_candidate_accuracy"),
            "mrr": delta("mrr"),
            "ndcg_at_5": delta("ndcg_at_5"),
            "hard_constraint_violation_rate": delta(
                "hard_constraint_violation_rate"
            ),
            "latency_p50_seconds": (
                systems["p3"]["latency_seconds"]["p50"]
                - systems["p2"]["latency_seconds"]["p50"]
            ),
            "latency_p95_seconds": (
                systems["p3"]["latency_seconds"]["p95"]
                - systems["p2"]["latency_seconds"]["p95"]
            ),
        },
        "p3_correctness_cost_complexity": _p3_operational_metrics(dataset, index),
    }
    paired = {
        "schema_version": PAIRED_CHANGES_SCHEMA_VERSION,
        "correctness_definition": transitions["correctness_definition"],
        "queries": changes,
    }
    return metrics, paired, transitions


__all__ = [
    "METRICS_SCHEMA_VERSION",
    "PAIRED_CHANGES_SCHEMA_VERSION",
    "TRANSITIONS_SCHEMA_VERSION",
    "aggregate_metrics",
    "paired_analysis",
]
