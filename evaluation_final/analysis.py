"""RQ-specific analysis with B0, P1, P2, and gated-P3 semantics."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
import statistics
from typing import Any, Mapping, Sequence

from .schemas import validate_rq1_events, validate_rq1_task_set
from .statistics import (
    STATISTICS_VERSION,
    cluster_bootstrap_mean_ci,
    distribution,
    exact_mcnemar,
    safe_rate,
)
from .systems import P2_ABLATION_IDS, active_primary_system_ids, validate_p2_ablation_id


RQ1_ANALYSIS_SCHEMA_VERSION = "final-rq1-derived-metrics-v1.0.0"
RQ2_ANALYSIS_SCHEMA_VERSION = "final-rq2-derived-metrics-v1.0.0"
RQ3_ANALYSIS_SCHEMA_VERSION = "final-rq3-derived-metrics-v1.0.0"

_INTERNAL_SYSTEM_IDS = {
    "b0": "B0",
    "p1": "P1",
    "p2": "P2",
    "p3": "P3",
    "B0": "B0",
    "P1": "P1",
    "P2": "P2",
    "P3": "P3",
}


def _rate(values: Sequence[bool | None]) -> dict[str, Any]:
    selected = [value for value in values if value is not None]
    numerator = sum(bool(value) for value in selected)
    return {
        "value": safe_rate(numerator, len(selected)),
        "numerator": numerator,
        "denominator": len(selected),
    }


def _mean(values: Sequence[float | None]) -> dict[str, Any]:
    selected = [float(value) for value in values if value is not None]
    return {
        "value": statistics.fmean(selected) if selected else None,
        "denominator": len(selected),
    }


def _ndcg(ranked: Sequence[str], acceptable: set[str], k: int = 5) -> float:
    if not acceptable:
        return 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, candidate_id in enumerate(ranked[:k], start=1)
        if candidate_id in acceptable
    )
    ideal_count = min(k, len(acceptable))
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return dcg / ideal if ideal else 0.0


def _reciprocal_rank(ranked: Sequence[str], acceptable: set[str]) -> float:
    for rank, candidate_id in enumerate(ranked, start=1):
        if candidate_id in acceptable:
            return 1.0 / rank
    return 0.0


def _prediction_index(
    dataset: Mapping[str, Any],
    predictions: Sequence[Mapping[str, Any]],
    *,
    expected_systems: Sequence[str],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    sample_ids = {str(item["sample_id"]) for item in dataset["items"]}
    index: dict[tuple[str, str], Mapping[str, Any]] = {}
    for record in predictions:
        raw_system = record.get("system")
        if raw_system not in _INTERNAL_SYSTEM_IDS:
            raise ValueError(f"prediction uses a non-primary system label: {raw_system!r}")
        system = _INTERNAL_SYSTEM_IDS[str(raw_system)]
        if system not in expected_systems:
            raise ValueError(f"unexpected {system} prediction in this analysis")
        sample_id = str(record.get("sample_id"))
        if sample_id not in sample_ids:
            raise ValueError(f"prediction references unknown sample {sample_id!r}")
        key = (system, sample_id)
        if key in index:
            raise ValueError(f"duplicate prediction for {system}/{sample_id}")
        if "dataset_sha256" in record and record["dataset_sha256"] != dataset.get(
            "dataset_sha256"
        ):
            raise ValueError("prediction dataset checksum does not match frozen dataset")
        index[key] = record
    expected = {
        (system, str(item["sample_id"]))
        for system in expected_systems
        for item in dataset["items"]
    }
    if set(index) != expected:
        missing = sorted(expected - set(index))
        extra = sorted(set(index) - expected)
        raise ValueError(f"prediction coverage mismatch; missing={missing}, extra={extra}")
    return index


def _score(
    item: Mapping[str, Any], system: str, prediction: Mapping[str, Any]
) -> dict[str, Any]:
    ranked = list(prediction.get("ranked_candidate_ids", []))
    if not all(isinstance(value, str) and value for value in ranked):
        raise ValueError("ranked_candidate_ids must contain non-blank strings")
    gold = item["gold"]
    acceptable = set(gold["acceptable_candidate_ids"])
    preferred = gold["preferred_candidate_id"]
    feasible = bool(gold["request_feasible"])
    top = ranked[0] if ranked else None
    detected = bool(prediction.get("detected_infeasible"))
    latency = prediction.get("latency_seconds")
    if latency is not None and (
        isinstance(latency, bool)
        or not isinstance(latency, (int, float))
        or not math.isfinite(float(latency))
        or float(latency) < 0
    ):
        raise ValueError("latency_seconds must be finite and non-negative")
    constraint_violated = bool(prediction.get("constraint_violated"))
    acceptable_top1 = (top in acceptable) if acceptable else None
    preferred_top1 = (top == preferred) if preferred is not None else None
    return {
        "sample_id": item["sample_id"],
        "workload_family": item["workload_family"],
        "system_id": system,
        "request_feasible": feasible,
        "preferred_top1": preferred_top1,
        "acceptable_top1": acceptable_top1,
        "acceptable_hit_at_1": (bool(acceptable & set(ranked[:1])) if acceptable else None),
        "acceptable_hit_at_3": (bool(acceptable & set(ranked[:3])) if acceptable else None),
        "acceptable_hit_at_5": (bool(acceptable & set(ranked[:5])) if acceptable else None),
        "reciprocal_rank": _reciprocal_rank(ranked, acceptable) if acceptable else None,
        "ndcg_at_5": _ndcg(ranked, acceptable) if acceptable else None,
        "constraint_violated": constraint_violated if feasible else None,
        "constraint_safe": (not constraint_violated) if feasible else None,
        "actual_unsupported": not feasible,
        "detected_unsupported": detected,
        "query_correct": bool(acceptable_top1) if feasible else detected,
        "latency_seconds": float(latency) if latency is not None else None,
        "fallback_used": bool(prediction.get("fallback_used")),
        "fallback_category": prediction.get("fallback_category"),
        "policy_compliant": bool(prediction.get("policy_compliant")),
        "ranked_candidate_ids": ranked,
        "retrieved_candidate_ids": list(prediction.get("retrieved_candidate_ids", [])),
        "feasible_candidate_ids": list(prediction.get("feasible_candidate_ids", [])),
    }


def _unsupported_detection(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tp = sum(row["actual_unsupported"] and row["detected_unsupported"] for row in rows)
    fn = sum(row["actual_unsupported"] and not row["detected_unsupported"] for row in rows)
    fp = sum(not row["actual_unsupported"] and row["detected_unsupported"] for row in rows)
    tn = sum(not row["actual_unsupported"] and not row["detected_unsupported"] for row in rows)
    precision = safe_rate(tp, tp + fp)
    recall = safe_rate(tp, tp + fn)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": safe_rate(tp + tn, len(rows)),
    }


def _recommendation_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fallbacks = Counter(
        str(row["fallback_category"] or "unspecified")
        for row in rows
        if row["fallback_used"]
    )
    return {
        "sample_count": len(rows),
        "top1_accuracy": _rate([row["preferred_top1"] for row in rows]),
        "acceptable_candidate": {
            "top1_accuracy": _rate([row["acceptable_top1"] for row in rows]),
            "hit_at_1": _rate([row["acceptable_hit_at_1"] for row in rows]),
            "hit_at_3": _rate([row["acceptable_hit_at_3"] for row in rows]),
            "hit_at_5": _rate([row["acceptable_hit_at_5"] for row in rows]),
        },
        "mrr": _mean([row["reciprocal_rank"] for row in rows]),
        "ndcg_at_5": _mean([row["ndcg_at_5"] for row in rows]),
        "constraint_violation_rate": _rate(
            [row["constraint_violated"] for row in rows]
        ),
        "unsupported_request_detection": _unsupported_detection(rows),
        "query_correctness": _rate([row["query_correct"] for row in rows]),
        "latency_seconds": distribution([row["latency_seconds"] for row in rows]),
        "fallback_behavior": {
            "rate": _rate([row["fallback_used"] for row in rows]),
            "category_counts": dict(sorted(fallbacks.items())),
        },
        "policy_compliance_rate": _rate([row["policy_compliant"] for row in rows]),
    }


def _paired_inference(
    items: Sequence[Mapping[str, Any]],
    index: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    first_system: str,
    second_system: str,
    replicates: int,
    seed: int,
) -> dict[str, Any]:
    scored: list[dict[str, Any]] = []
    for item in items:
        sample_id = str(item["sample_id"])
        first = _score(item, first_system, index[(first_system, sample_id)])
        second = _score(item, second_system, index[(second_system, sample_id)])
        row: dict[str, Any] = {
            "sample_id": sample_id,
            "workload_family": item["workload_family"],
        }
        for field in (
            "preferred_top1",
            "acceptable_top1",
            "reciprocal_rank",
            "ndcg_at_5",
            "constraint_safe",
            "query_correct",
            "latency_seconds",
        ):
            row[f"first_{field}"] = first[field]
            row[f"second_{field}"] = second[field]
            row[f"delta_{field}"] = (
                float(second[field]) - float(first[field])
                if first[field] is not None and second[field] is not None
                else None
            )
        scored.append(row)

    results: dict[str, Any] = {}
    binary_fields = {
        "preferred_top1",
        "acceptable_top1",
        "constraint_safe",
        "query_correct",
    }
    for offset, field in enumerate(
        (
            "preferred_top1",
            "acceptable_top1",
            "reciprocal_rank",
            "ndcg_at_5",
            "constraint_safe",
            "query_correct",
            "latency_seconds",
        )
    ):
        paired = [row for row in scored if row[f"delta_{field}"] is not None]
        differences = [float(row[f"delta_{field}"]) for row in paired]
        lower, upper = cluster_bootstrap_mean_ci(
            paired,
            lambda row, selected=field: row[f"delta_{selected}"],
            cluster_field="workload_family",
            replicates=replicates,
            seed=seed + offset,
        )
        result: dict[str, Any] = {
            "difference": f"{second_system}_minus_{first_system}",
            "mean_difference": statistics.fmean(differences) if differences else None,
            "cluster_bootstrap_95_ci": [lower, upper],
            "paired_sample_count": len(paired),
            "direction": "lower_is_better" if field == "latency_seconds" else "higher_is_better",
        }
        if field in binary_fields:
            result["exact_mcnemar"] = exact_mcnemar(
                [bool(row[f"first_{field}"]) for row in paired],
                [bool(row[f"second_{field}"]) for row in paired],
            )
        results[field] = result
    return {
        "first_system": first_system,
        "second_system": second_system,
        "independent_bootstrap_cluster": "workload_family",
        "bootstrap_replicates": replicates,
        "bootstrap_seed": seed,
        "metrics": results,
    }


def _p2_failure_category(item: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    fallback = row["fallback_category"]
    acceptable = set(item["gold"]["acceptable_candidate_ids"])
    retrieved = acceptable & set(row["retrieved_candidate_ids"])
    feasible = acceptable & set(row["feasible_candidate_ids"])
    if fallback in {"infrastructure_provider_failure", "pipeline_validation_failure"}:
        return "infrastructure_or_provider_failure"
    if isinstance(fallback, str) and fallback.startswith("extraction_"):
        return "extraction_error"
    if not item["gold"]["request_feasible"]:
        return "unsupported_catalog" if row["detected_unsupported"] else "unsupported_detection_miss"
    if not retrieved:
        return "retrieval_miss"
    if retrieved and not feasible:
        return "constraint_filtering_error"
    if feasible and not row["acceptable_top1"]:
        return "ranking_error"
    return "no_error"


def _p2_ablation_metrics(
    dataset: Mapping[str, Any],
    predictions: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    if not predictions:
        return {
            "status": "not_run",
            "classification": "secondary_P2_ablations_not_primary_systems",
            "allowed_ablation_ids": list(P2_ABLATION_IDS),
        }
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in predictions:
        if _INTERNAL_SYSTEM_IDS.get(record.get("system")) != "P2":
            raise ValueError("ablation observations must retain primary system_id P2")
        ablation_id = validate_p2_ablation_id(record.get("ablation_id"))
        grouped[ablation_id].append(record)
    variants: dict[str, Any] = {}
    for ablation_id in P2_ABLATION_IDS:
        if ablation_id not in grouped:
            continue
        index = _prediction_index(dataset, grouped[ablation_id], expected_systems=("P2",))
        rows = [
            _score(item, "P2", index[("P2", str(item["sample_id"]))])
            for item in dataset["items"]
        ]
        variants[ablation_id] = _recommendation_metrics(rows)
    return {
        "status": "available",
        "classification": "secondary_P2_ablations_not_primary_systems",
        "variants": variants,
    }


def analyze_rq2(
    dataset: Mapping[str, Any],
    predictions: Sequence[Mapping[str, Any]],
    *,
    ablation_predictions: Sequence[Mapping[str, Any]] | None = None,
    bootstrap_replicates: int = 2000,
    bootstrap_seed: int = 20260822,
) -> dict[str, Any]:
    """Compare P1 and P2 on paired recommendation observations."""

    index = _prediction_index(dataset, predictions, expected_systems=("P1", "P2"))
    system_rows: dict[str, list[dict[str, Any]]] = {"P1": [], "P2": []}
    failure_counts: Counter[str] = Counter()
    failure_samples: dict[str, list[str]] = defaultdict(list)
    for item in dataset["items"]:
        sample_id = str(item["sample_id"])
        for system in ("P1", "P2"):
            row = _score(item, system, index[(system, sample_id)])
            system_rows[system].append(row)
            if system == "P2":
                category = _p2_failure_category(item, row)
                failure_counts[category] += 1
                failure_samples[category].append(sample_id)
    return {
        "schema_version": RQ2_ANALYSIS_SCHEMA_VERSION,
        "statistics_version": STATISTICS_VERSION,
        "research_question": "RQ2",
        "primary_systems": ["P1", "P2"],
        "systems": {
            system: _recommendation_metrics(system_rows[system])
            for system in ("P1", "P2")
        },
        "paired_P2_versus_P1": _paired_inference(
            dataset["items"],
            index,
            first_system="P1",
            second_system="P2",
            replicates=bootstrap_replicates,
            seed=bootstrap_seed,
        ),
        "P2_failure_categories": {
            "definition_version": "final-p2-failure-categories-v1.0.0",
            "counts": dict(sorted(failure_counts.items())),
            "sample_ids": dict(sorted(failure_samples.items())),
        },
        "P2_ablations": _p2_ablation_metrics(dataset, ablation_predictions),
        "metric_notes": {
            "top1_accuracy": "rank-1 equals the single preferred candidate",
            "acceptable_candidate": "binary relevance over the frozen acceptable-candidate set",
            "constraint_violations": "feasible requests only; unsupported detection is separate",
            "P1_ranking_scope": "P1 emits one decision, so its observed list has length one",
        },
    }


def _rq1_sessions(
    task_set: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    task_index = {item["task_id"]: item for item in task_set["tasks"]}
    grouped: dict[tuple[str, str, str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        if event["task_id"] not in task_index:
            raise ValueError(f"RQ1 event references unknown task {event['task_id']!r}")
        grouped[
            (
                event["study_id"],
                event["participant_id"],
                event["session_id"],
                event["task_id"],
                event["system_id"],
            )
        ].append(event)
    rows: list[dict[str, Any]] = []
    candidate_events = {
        "candidate_selected",
        "recommendation_accepted",
        "manual_correction",
        "task_completed",
    }
    interaction_events = {
        "candidate_selected",
        "recommendation_previewed",
        "recommendation_accepted",
        "recommendation_rejected",
        "manual_correction",
    }
    for key, raw in grouped.items():
        ordered = sorted(raw, key=lambda item: item["event_index"])
        indexes = [item["event_index"] for item in ordered]
        if indexes != list(range(len(ordered))):
            raise ValueError(f"RQ1 session {key} requires contiguous event indexes from zero")
        elapsed = [float(item["elapsed_seconds"]) for item in ordered]
        if elapsed != sorted(elapsed):
            raise ValueError(f"RQ1 session {key} elapsed time must be monotonic")
        if ordered[0]["event_type"] != "study_started":
            raise ValueError(f"RQ1 session {key} must begin with study_started")
        terminal = [
            item for item in ordered if item["event_type"] in {"task_completed", "task_abandoned"}
        ]
        if len(terminal) != 1 or terminal[0] is not ordered[-1]:
            raise ValueError(f"RQ1 session {key} requires one final terminal event")
        task = task_index[key[3]]
        acceptable = set(task["acceptable_candidate_ids"])
        completed = terminal[0]["event_type"] == "task_completed"
        final_candidate = terminal[0]["candidate_id"] if completed else None
        appropriate_times = [
            float(item["elapsed_seconds"])
            for item in ordered
            if item["event_type"] in candidate_events
            and item["candidate_id"] in acceptable
        ]
        rows.append(
            {
                "study_id": key[0],
                "participant_id": key[1],
                "session_id": key[2],
                "task_id": key[3],
                "system_id": key[4],
                "workload_family": task["workload_family"],
                "correct_environment_selection": bool(
                    completed and final_candidate in acceptable
                ),
                "time_to_appropriate_selection_seconds": (
                    min(appropriate_times) if appropriate_times else None
                ),
                "interaction_action_count": sum(
                    item["event_type"] in interaction_events for item in ordered
                ),
                "selection_action_count": sum(
                    item["event_type"] in candidate_events - {"task_completed"}
                    for item in ordered
                ),
                "manual_correction_count": sum(
                    item["event_type"] == "manual_correction" for item in ordered
                ),
                "task_completed": completed,
                "task_completion_seconds": elapsed[-1] if completed else None,
            }
        )
    return rows


def _rq1_system_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "session_count": len(rows),
        "correct_environment_selection": _rate(
            [row["correct_environment_selection"] for row in rows]
        ),
        "time_to_appropriate_selection_seconds": distribution(
            [row["time_to_appropriate_selection_seconds"] for row in rows]
        ),
        "interaction_action_count": distribution(
            [row["interaction_action_count"] for row in rows]
        ),
        "selection_action_count": distribution(
            [row["selection_action_count"] for row in rows]
        ),
        "manual_correction_count": distribution(
            [row["manual_correction_count"] for row in rows]
        ),
        "task_completion": _rate([row["task_completed"] for row in rows]),
        "task_completion_seconds": distribution(
            [row["task_completion_seconds"] for row in rows]
        ),
    }


def _rq1_paired_comparison(
    baseline: Sequence[Mapping[str, Any]],
    treatment: Sequence[Mapping[str, Any]],
    *,
    treatment_id: str,
    replicates: int,
    seed: int,
) -> dict[str, Any]:
    key = lambda row: (row["study_id"], row["participant_id"], row["task_id"])
    b_index = {key(row): row for row in baseline}
    t_index = {key(row): row for row in treatment}
    if len(b_index) != len(baseline) or len(t_index) != len(treatment):
        raise ValueError("RQ1 paired keys must be unique within each system")
    common = sorted(set(b_index) & set(t_index))
    rows: list[dict[str, Any]] = []
    fields = (
        "correct_environment_selection",
        "time_to_appropriate_selection_seconds",
        "interaction_action_count",
        "selection_action_count",
        "manual_correction_count",
        "task_completed",
        "task_completion_seconds",
    )
    for pair_key in common:
        baseline_row = b_index[pair_key]
        treatment_row = t_index[pair_key]
        row: dict[str, Any] = {
            "participant_id": pair_key[1],
            "task_id": pair_key[2],
        }
        for field in fields:
            left = baseline_row[field]
            right = treatment_row[field]
            row[f"baseline_{field}"] = left
            row[f"treatment_{field}"] = right
            row[f"delta_{field}"] = (
                float(right) - float(left)
                if left is not None and right is not None
                else None
            )
        rows.append(row)
    metrics: dict[str, Any] = {}
    for offset, field in enumerate(fields):
        paired = [row for row in rows if row[f"delta_{field}"] is not None]
        diffs = [float(row[f"delta_{field}"]) for row in paired]
        lower, upper = cluster_bootstrap_mean_ci(
            paired,
            lambda row, selected=field: row[f"delta_{selected}"],
            cluster_field="participant_id",
            replicates=replicates,
            seed=seed + offset,
        )
        metrics[field] = {
            "difference": f"{treatment_id}_minus_B0",
            "mean_difference": statistics.fmean(diffs) if diffs else None,
            "participant_cluster_bootstrap_95_ci": [lower, upper],
            "paired_session_count": len(paired),
            "direction": (
                "higher_is_better"
                if field in {"correct_environment_selection", "task_completed"}
                else "lower_is_better"
            ),
        }
        if field in {"correct_environment_selection", "task_completed"}:
            metrics[field]["exact_mcnemar"] = exact_mcnemar(
                [bool(row[f"baseline_{field}"]) for row in paired],
                [bool(row[f"treatment_{field}"]) for row in paired],
            )
    return {
        "baseline_system": "B0",
        "treatment_system": treatment_id,
        "common_participant_task_sessions": len(common),
        "metrics": metrics,
    }


def analyze_rq1(
    task_set_document: object,
    event_records: Sequence[object],
    *,
    p3_gate_status: str,
    bootstrap_replicates: int = 2000,
    bootstrap_seed: int = 20260822,
) -> dict[str, Any]:
    """Analyze user-facing selection outcomes; never compute ranking metrics for B0."""

    task_set = validate_rq1_task_set(task_set_document)
    events = validate_rq1_events(event_records, p3_gate_status=p3_gate_status)
    rows = _rq1_sessions(task_set, events)
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["system_id"]].append(row)
    active = active_primary_system_ids(p3_gate_status)
    comparisons = {
        system: _rq1_paired_comparison(
            grouped.get("B0", []),
            grouped.get(system, []),
            treatment_id=system,
            replicates=bootstrap_replicates,
            seed=bootstrap_seed + position * 100,
        )
        for position, system in enumerate(active)
        if system != "B0" and grouped.get("B0") and grouped.get(system)
    }
    result = {
        "schema_version": RQ1_ANALYSIS_SCHEMA_VERSION,
        "statistics_version": STATISTICS_VERSION,
        "research_question": "RQ1",
        "active_primary_systems": list(active),
        "study_id_values": sorted({row["study_id"] for row in rows}),
        "task_set_id": task_set["task_set_id"],
        "systems": {
            system: _rq1_system_metrics(grouped[system])
            for system in active
            if system in grouped
        },
        "paired_comparisons_against_B0": comparisons,
        "analysis_rule": (
            "B0 emits no recommendation or ranking; only selection, interaction, "
            "correction, time, and completion outcomes are analyzed in RQ1."
        ),
    }
    forbidden = ("top1", "mrr", "ndcg", "reciprocal_rank")
    serialized = json.dumps(result, sort_keys=True).lower()
    if any(label in serialized for label in forbidden):
        raise RuntimeError("RQ1 output accidentally contains a recommendation-ranking metric")
    return result


def analyze_rq3(
    dataset: Mapping[str, Any],
    predictions: Sequence[Mapping[str, Any]] | None,
    *,
    p3_gate_status: str,
    gate_evidence: Mapping[str, Any],
    bootstrap_replicates: int = 2000,
    bootstrap_seed: int = 20260822,
) -> dict[str, Any]:
    """Run paired P2/P3 analysis only when the pre-recorded gate retained P3."""

    if p3_gate_status != "retained":
        if predictions:
            raise ValueError("P3 predictions cannot enter final analysis after a non-retained gate")
        return {
            "schema_version": RQ3_ANALYSIS_SCHEMA_VERSION,
            "research_question": "RQ3",
            "status": "not_applicable_after_gate",
            "metrics_generated": False,
            "gate_evidence": dict(gate_evidence),
            "reason": "P3 was not retained for final confirmatory evaluation.",
        }
    if not predictions:
        return {
            "schema_version": RQ3_ANALYSIS_SCHEMA_VERSION,
            "research_question": "RQ3",
            "status": "awaiting_paired_observations",
            "metrics_generated": False,
            "gate_evidence": dict(gate_evidence),
        }
    index = _prediction_index(dataset, predictions, expected_systems=("P2", "P3"))
    normalized = [
        {**dict(record), "system": _INTERNAL_SYSTEM_IDS[str(record["system"])].lower()}
        for record in predictions
    ]
    from evaluation_p3.metrics import aggregate_metrics as aggregate_p3_metrics

    metrics, paired, transitions = aggregate_p3_metrics(dataset, normalized)
    metrics = {
        **metrics,
        "primary_systems": ["P2", "P3"],
        "systems": {"P2": metrics["systems"]["p2"], "P3": metrics["systems"]["p3"]},
    }
    return {
        "schema_version": RQ3_ANALYSIS_SCHEMA_VERSION,
        "statistics_version": STATISTICS_VERSION,
        "research_question": "RQ3",
        "status": "completed",
        "metrics_generated": True,
        "gate_evidence": dict(gate_evidence),
        "primary_systems": ["P2", "P3"],
        "paired_metrics": metrics,
        "paired_changes": paired,
        "error_transitions": transitions,
        "paired_inference": _paired_inference(
            dataset["items"],
            index,
            first_system="P2",
            second_system="P3",
            replicates=bootstrap_replicates,
            seed=bootstrap_seed,
        ),
    }


__all__ = [
    "RQ1_ANALYSIS_SCHEMA_VERSION",
    "RQ2_ANALYSIS_SCHEMA_VERSION",
    "RQ3_ANALYSIS_SCHEMA_VERSION",
    "analyze_rq1",
    "analyze_rq2",
    "analyze_rq3",
]
