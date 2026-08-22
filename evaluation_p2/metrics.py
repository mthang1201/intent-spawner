"""Frozen metric and P2 error-analysis definitions for offline evaluation."""

from __future__ import annotations

from collections import Counter
import math
import statistics
from typing import Any, Mapping, Sequence


METRICS_SCHEMA_VERSION = "p1-p2-aggregate-metrics-v1.0.0"
P2_ERROR_SCHEMA_VERSION = "p2-error-categorization-v1.0.0"
P3_DECISION_SCHEMA_VERSION = "p3-headroom-decision-v1.0.0"
PRIMARY_SYSTEMS = ("p1", "p2")
ERROR_CATEGORIES = (
    "extraction_error",
    "retrieval_miss",
    "constraint_error",
    "ranking_error",
    "unsupported_catalog",
    "infrastructure_provider_failure",
)


def _prediction_index(predictions: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    result = {(item["system"], item["sample_id"]): item for item in predictions}
    if len(result) != len(predictions):
        raise ValueError("prediction system/sample pairs must be unique")
    return result


def _reciprocal_rank(ranked: Sequence[str], acceptable: set[str]) -> float:
    for rank, candidate_id in enumerate(ranked, start=1):
        if candidate_id in acceptable:
            return 1.0 / rank
    return 0.0


def _ndcg(ranked: Sequence[str], acceptable: set[str], k: int) -> float:
    if not acceptable:
        return 0.0
    dcg = sum(
        (1.0 / math.log2(rank + 1))
        for rank, candidate_id in enumerate(ranked[:k], start=1)
        if candidate_id in acceptable
    )
    ideal_count = min(len(acceptable), k)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return dcg / ideal if ideal else 0.0


def _safe_rate(numerator: int | float, denominator: int) -> float | None:
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


def aggregate_metrics(
    dataset: Mapping[str, Any],
    predictions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    index = _prediction_index(predictions)
    aggregates: dict[str, Any] = {}
    for system in PRIMARY_SYSTEMS:
        top1_numerator = 0
        top1_denominator = 0
        hit_numerators = {1: 0, 3: 0, 5: 0}
        ranking_denominator = 0
        reciprocal_ranks: list[float] = []
        ndcg_values: list[float] = []
        violation_numerator = 0
        violation_denominator = 0
        latencies: list[float] = []
        fallback_count = 0
        policy_count = 0
        tp = fp = tn = fn = 0

        for item in dataset["items"]:
            prediction = index[(system, item["sample_id"])]
            gold = item["gold"]
            ranked = list(prediction["ranked_candidate_ids"])
            acceptable = set(gold["acceptable_candidate_ids"])
            preferred = gold["preferred_candidate_id"]
            if preferred is not None:
                top1_denominator += 1
                top1_numerator += int(bool(ranked) and ranked[0] == preferred)
            if acceptable:
                ranking_denominator += 1
                for k in hit_numerators:
                    hit_numerators[k] += int(bool(acceptable & set(ranked[:k])))
                reciprocal_ranks.append(_reciprocal_rank(ranked, acceptable))
                ndcg_values.append(_ndcg(ranked, acceptable, 5))

            if gold["request_feasible"]:
                violation_denominator += 1
                violation_numerator += int(bool(prediction["constraint_violated"]))

            detected = bool(prediction["detected_infeasible"])
            actual = not bool(gold["request_feasible"])
            if actual and detected:
                tp += 1
            elif actual and not detected:
                fn += 1
            elif not actual and detected:
                fp += 1
            else:
                tn += 1

            latency = prediction.get("latency_seconds")
            if isinstance(latency, (int, float)) and math.isfinite(float(latency)):
                latencies.append(float(latency))
            fallback_count += int(bool(prediction["fallback_used"]))
            policy_count += int(bool(prediction["policy_compliant"]))

        sample_count = len(dataset["items"])
        precision = _safe_rate(tp, tp + fp)
        recall = _safe_rate(tp, tp + fn)
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall
            else None
        )
        aggregates[system] = {
            "sample_count": sample_count,
            "top1_accuracy": {
                "value": _safe_rate(top1_numerator, top1_denominator),
                "numerator": top1_numerator,
                "denominator": top1_denominator,
            },
            "acceptable_candidate_hit_at_k": {
                str(k): {
                    "value": _safe_rate(value, ranking_denominator),
                    "numerator": value,
                    "denominator": ranking_denominator,
                }
                for k, value in hit_numerators.items()
            },
            "mrr": {
                "value": statistics.fmean(reciprocal_ranks) if reciprocal_ranks else None,
                "denominator": ranking_denominator,
            },
            "ndcg_at_5": {
                "value": statistics.fmean(ndcg_values) if ndcg_values else None,
                "denominator": ranking_denominator,
            },
            "constraint_violation_rate": {
                "value": _safe_rate(violation_numerator, violation_denominator),
                "numerator": violation_numerator,
                "denominator": violation_denominator,
            },
            "infeasible_request_detection": {
                "true_positive": tp,
                "false_positive": fp,
                "true_negative": tn,
                "false_negative": fn,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "accuracy": _safe_rate(tp + tn, tp + fp + tn + fn),
            },
            "latency_seconds": {
                "count": len(latencies),
                "mean": statistics.fmean(latencies) if latencies else None,
                "median": statistics.median(latencies) if latencies else None,
                "p95": _percentile(latencies, 0.95),
                "minimum": min(latencies) if latencies else None,
                "maximum": max(latencies) if latencies else None,
            },
            "fallback_rate": {
                "value": _safe_rate(fallback_count, sample_count),
                "numerator": fallback_count,
                "denominator": sample_count,
            },
            "policy_compliance_rate": {
                "value": _safe_rate(policy_count, sample_count),
                "numerator": policy_count,
                "denominator": sample_count,
            },
        }
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "primary_systems": list(PRIMARY_SYSTEMS),
        "metric_definition_notes": {
            "p1_ranked_list": "P1 emits one decision, so its ranked list contains only that candidate.",
            "constraint_violation_denominator": "Requests labeled feasible only; infeasible detection is reported separately.",
            "relevance": "Binary relevance over the gold acceptable-candidate set.",
        },
        "systems": aggregates,
    }


def _extraction_mismatch(expected: object, extracted: object) -> bool:
    if expected is None:
        return False
    if not isinstance(expected, Mapping) or not isinstance(extracted, Mapping):
        return True
    for field in ("gpu_requirement", "minimum_cpu_cores", "minimum_memory_gb"):
        if expected.get(field) != extracted.get(field):
            return True
    return set(expected.get("required_libraries", [])) != set(
        extracted.get("required_libraries", [])
    )


def categorize_p2_errors(
    dataset: Mapping[str, Any],
    predictions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    index = _prediction_index(predictions)
    cases: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for item in dataset["items"]:
        prediction = index[("p2", item["sample_id"])]
        gold = item["gold"]
        acceptable = set(gold["acceptable_candidate_ids"])
        retrieved_acceptable = acceptable & set(prediction["retrieved_candidate_ids"])
        feasible_acceptable = acceptable & set(prediction["feasible_candidate_ids"])
        ranked = list(prediction["ranked_candidate_ids"])
        top_acceptable = bool(ranked and ranked[0] in acceptable)
        extraction_mismatch = _extraction_mismatch(
            gold.get("expected_extraction"), prediction.get("extracted_constraints")
        )
        fallback_category = prediction.get("fallback_category")

        if prediction.get("infrastructure_failure") or fallback_category in {
            "infrastructure_provider_failure",
            "pipeline_validation_failure",
        }:
            category = "infrastructure_provider_failure"
        elif not gold["request_feasible"] and prediction["detected_infeasible"]:
            category = "unsupported_catalog"
        elif extraction_mismatch or (
            isinstance(fallback_category, str)
            and fallback_category.startswith("extraction_")
        ):
            category = "extraction_error"
        elif not gold["request_feasible"] and not prediction["detected_infeasible"]:
            category = "constraint_error"
        elif gold["request_feasible"] and not retrieved_acceptable:
            category = "retrieval_miss"
        elif gold["request_feasible"] and retrieved_acceptable and not feasible_acceptable:
            category = "constraint_error"
        elif gold["request_feasible"] and feasible_acceptable and not top_acceptable:
            category = "ranking_error"
        else:
            category = "no_error"
        counts[category] += 1
        cases.append(
            {
                "sample_id": item["sample_id"],
                "category": category,
                "request_feasible": gold["request_feasible"],
                "extraction_mismatch": extraction_mismatch,
                "acceptable_retrieved": bool(retrieved_acceptable),
                "acceptable_feasible": bool(feasible_acceptable),
                "top1_acceptable": top_acceptable,
                "fallback_category": fallback_category,
            }
        )
    return {
        "schema_version": P2_ERROR_SCHEMA_VERSION,
        "categories": {category: counts.get(category, 0) for category in ERROR_CATEGORIES},
        "no_error": counts.get("no_error", 0),
        "cases": cases,
    }


def p3_decision_report(
    dataset: Mapping[str, Any],
    predictions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    index = _prediction_index(predictions)
    eligible = 0
    headroom_ids: list[str] = []
    for item in dataset["items"]:
        if not item["gold"]["request_feasible"]:
            continue
        prediction = index[("p2", item["sample_id"])]
        acceptable = set(item["gold"]["acceptable_candidate_ids"])
        acceptable_feasible = acceptable & set(prediction["feasible_candidate_ids"])
        if not acceptable_feasible:
            continue
        eligible += 1
        ranked = list(prediction["ranked_candidate_ids"])
        if not ranked or ranked[0] not in acceptable:
            headroom_ids.append(item["sample_id"])
    rate = _safe_rate(len(headroom_ids), eligible)
    minimum_cases = max(3, math.ceil(0.05 * eligible)) if eligible else 3
    meaningful = bool(
        eligible and len(headroom_ids) >= minimum_cases and rate is not None and rate >= 0.05
    )
    return {
        "schema_version": P3_DECISION_SCHEMA_VERSION,
        "criterion": (
            "Meaningful headroom requires at least max(3, ceil(5% of eligible cases)) "
            "cases and a headroom rate of at least 5%."
        ),
        "eligible_cases": eligible,
        "headroom_case_count": len(headroom_ids),
        "headroom_rate": rate,
        "headroom_sample_ids": headroom_ids,
        "meaningful_reranking_headroom": meaningful,
        "decision": (
            "P2 leaves meaningful deterministic-ranking headroom for a future P3 study."
            if meaningful
            else "P2 does not leave meaningful reranking headroom under the frozen criterion."
        ),
        "p3_implemented": False,
    }


__all__ = [
    "ERROR_CATEGORIES",
    "METRICS_SCHEMA_VERSION",
    "P2_ERROR_SCHEMA_VERSION",
    "P3_DECISION_SCHEMA_VERSION",
    "PRIMARY_SYSTEMS",
    "aggregate_metrics",
    "categorize_p2_errors",
    "p3_decision_report",
]
