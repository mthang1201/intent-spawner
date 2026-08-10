"""Analyze protocol-v4 recommendation and system-effectiveness evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .dataset import (
    DEFAULT_DATASET,
    PROFILE_ORDER,
    canonical_sha256,
    dataset_index,
    dataset_summary,
    file_sha256,
    load_dataset,
    normalize_profile,
)
from .schemas import (
    read_jsonl,
    validate_prediction,
    validate_reprovision_trial,
    validate_system_trial,
    validate_user_event,
)
from .statistics import (
    cluster_bootstrap_ci,
    confusion_matrix,
    exact_mcnemar,
    holm_adjust,
    mean,
    quantile,
    wilcoxon_signed_rank,
)


ROOT = Path(__file__).resolve().parents[1]


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _round(value: Any, digits: int = 6) -> Any:
    return round(value, digits) if isinstance(value, float) else value


def _round_p(value: Any) -> Any:
    """Keep small non-zero p-values from being rendered as statistical zero."""

    return float(f"{value:.12g}") if isinstance(value, float) else value


def _metric_ci(
    rows: Sequence[Mapping[str, Any]],
    field: str,
    *,
    replicates: int,
    seed: int,
) -> tuple[float | None, float | None]:
    return cluster_bootstrap_ci(
        rows,
        lambda sample: mean(row.get(field) for row in sample),
        replicates=replicates,
        seed=seed,
    )


def _validate_joined_identity(record: Mapping[str, Any], item: Mapping[str, Any]) -> None:
    if record["workload_family"] != item["workload_family"]:
        raise ValueError(
            f"record family mismatch for sample {record['sample_id']!r}"
        )


def _classify_size_band(dataset_size_gb: float | int | None) -> str:
    if dataset_size_gb is None or dataset_size_gb < 0.5:
        return "< 0.5 GB"
    if dataset_size_gb <= 2.0:
        return "0.5 - 2.0 GB"
    return "> 2.0 GB"


def _classify_resource_category(stratum: str) -> str:
    s = stratum.lower()
    if "cpu" in s or "cpu-heavy" in s:
        return "cpu_heavy"
    if "deep-learning" in s or "deep_learning" in s or "gpu" in s:
        return "deep_learning"
    if "machine-learning" in s or "machine_learning" in s:
        return "machine_learning"
    if "tabular" in s or "memory" in s or "large" in s:
        return "memory_heavy"
    return "light_or_standard"


def _classify_ambiguity(stratum: str) -> str:
    s = stratum.lower()
    if "ambiguous" in s or "conflicting" in s or "noisy" in s or "hidden" in s:
        return "ambiguous"
    return "unambiguous"


def score_predictions(
    records: Sequence[Mapping[str, Any]],
    dataset: Mapping[str, Any],
) -> list[dict[str, Any]]:
    items = dataset_index(dataset)
    expected_hash = canonical_sha256(dataset)
    scored: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, int]] = set()
    catalog_images = dataset["image_catalog"]["images"]
    for record in records:
        if record["dataset_id"] != dataset["dataset_id"] or record["dataset_sha256"] != expected_hash:
            raise ValueError("prediction dataset identity does not match the supplied gold set")
        sample_id = str(record["sample_id"])
        if sample_id not in items:
            raise ValueError(f"unknown prediction sample_id {sample_id!r}")
        key = (str(record["recommender"]), sample_id, int(record["repeat_index"]))
        if key in seen_keys:
            raise ValueError(f"duplicate prediction key {key!r}")
        seen_keys.add(key)
        item = items[sample_id]
        _validate_joined_identity(record, item)
        gold = item["gold"]
        predicted_profile = record["applied_profile"]
        predicted_image = record["predicted_image_id"]
        raw_model_profile = record.get("parsed_profile") or record.get("raw_profile")
        raw_model_image = record.get("parsed_image_id") or record.get("predicted_image_id")
        fallback_used = bool(record["fallback_used"])

        profile_available = predicted_profile in PROFILE_ORDER
        image_available = predicted_image in catalog_images
        acceptable_profiles = list(gold["acceptable_profiles"])
        acceptable_images = list(gold["acceptable_image_ids"])
        minimum_profile = min(acceptable_profiles, key=PROFILE_ORDER.__getitem__)
        maximum_profile = max(acceptable_profiles, key=PROFILE_ORDER.__getitem__)
        profile_steps = (
            abs(PROFILE_ORDER[predicted_profile] - PROFILE_ORDER[gold["preferred_profile"]])
            if profile_available
            else None
        )
        required_capabilities = set(gold["required_image_capabilities"])
        available_capabilities = (
            set(catalog_images[predicted_image]["capabilities"])
            if image_available
            else set()
        )
        profile_acceptable = profile_available and predicted_profile in acceptable_profiles
        image_acceptable = image_available and predicted_image in acceptable_images

        # Raw model quality (without fallback credit)
        raw_model_profile_normalized = normalize_profile(raw_model_profile)
        raw_model_profile_acceptable = bool(
            not fallback_used
            and raw_model_profile_normalized in acceptable_profiles
        )
        raw_model_image_acceptable = bool(
            not fallback_used
            and raw_model_image in acceptable_images
        )
        raw_model_joint_acceptable = bool(
            not fallback_used
            and raw_model_profile_acceptable
            and raw_model_image_acceptable
            and record["policy_compliant"]
            and record["error_category"] is None
        )

        dataset_size_gb = item["inputs"].get("dataset_size_gb")
        stratum = str(item["stratum"])

        scored.append(
            {
                **dict(record),
                "gold_preferred_profile": gold["preferred_profile"],
                "gold_preferred_image_id": gold["preferred_image_id"],
                "stratum": stratum,
                "language": item["language"],
                "variant": item["variant"],
                "size_band": _classify_size_band(dataset_size_gb),
                "resource_category": _classify_resource_category(stratum),
                "ambiguity": _classify_ambiguity(stratum),
                "profile_available": profile_available,
                "image_available": image_available,
                "profile_exact": profile_available and predicted_profile == gold["preferred_profile"],
                "profile_acceptable": profile_acceptable,
                "raw_model_profile_acceptable": raw_model_profile_acceptable,
                "raw_model_image_acceptable": raw_model_image_acceptable,
                "raw_model_joint_acceptable": raw_model_joint_acceptable,
                "profile_ordinal_error": profile_steps,
                "underprovisioned": profile_available and PROFILE_ORDER[predicted_profile] < PROFILE_ORDER[minimum_profile],
                "overprovisioned": profile_available and PROFILE_ORDER[predicted_profile] > PROFILE_ORDER[maximum_profile],
                "image_exact": image_available and predicted_image == gold["preferred_image_id"],
                "image_acceptable": image_acceptable,
                "image_capability_coverage": image_available and required_capabilities.issubset(available_capabilities),
                "joint_acceptable": bool(
                    profile_acceptable
                    and image_acceptable
                    and record["policy_compliant"]
                    and record["error_category"] is None
                ),
                "policy_violation": not bool(record["policy_compliant"]),
                "error": record["error_category"] is not None,
            }
        )
    return scored


RECOMMENDATION_METRICS = (
    "profile_exact",
    "profile_acceptable",
    "raw_model_profile_acceptable",
    "raw_model_image_acceptable",
    "raw_model_joint_acceptable",
    "underprovisioned",
    "overprovisioned",
    "image_exact",
    "image_acceptable",
    "image_capability_coverage",
    "joint_acceptable",
    "policy_violation",
    "fallback_used",
    "error",
)


def _prediction_summary_row(
    method: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    replicates: int,
    seed: int,
) -> dict[str, Any]:
    prompt_tokens_list = [row["prompt_tokens"] for row in rows if row.get("prompt_tokens") is not None]
    completion_tokens_list = [row["completion_tokens"] for row in rows if row.get("completion_tokens") is not None]
    total_tokens_list = [row["total_tokens"] for row in rows if row.get("total_tokens") is not None]
    cost_list = [row["estimated_cost_usd"] for row in rows if row.get("estimated_cost_usd") is not None]
    inf_latency_list = [row["inference_latency_seconds"] for row in rows if row.get("inference_latency_seconds") is not None]
    latency_list = [item["latency_seconds"] for item in rows if item["latency_seconds"] is not None]

    row: dict[str, Any] = {
        "recommender": method,
        "records": len(rows),
        "samples": len({item["sample_id"] for item in rows}),
        "families": len({item["workload_family"] for item in rows}),
        "coverage": mean(item["profile_available"] and item["image_available"] for item in rows),
        "profile_ordinal_mae_available": mean(item["profile_ordinal_error"] for item in rows),
        "latency_mean_seconds": mean(latency_list),
        "latency_median_seconds": quantile(latency_list, 0.5),
        "latency_p95_seconds": quantile(latency_list, 0.95),
        "inference_latency_median_seconds": quantile(inf_latency_list, 0.5) if inf_latency_list else None,
        "prompt_tokens_mean": mean(prompt_tokens_list) if prompt_tokens_list else None,
        "completion_tokens_mean": mean(completion_tokens_list) if completion_tokens_list else None,
        "total_tokens_mean": mean(total_tokens_list) if total_tokens_list else None,
        "estimated_cost_usd_per_1k": (mean(cost_list) * 1000.0) if cost_list else None,
    }
    for metric_index, metric in enumerate(RECOMMENDATION_METRICS):
        value = mean(item[metric] for item in rows)
        low, high = _metric_ci(
            rows,
            metric,
            replicates=replicates,
            seed=seed + metric_index,
        )
        row[metric + "_rate"] = value
        row[metric + "_ci_low"] = low
        row[metric + "_ci_high"] = high
    return {key: _round(value) for key, value in row.items()}


def analyze_recommendations(
    records: Sequence[Mapping[str, Any]],
    dataset: Mapping[str, Any],
    *,
    replicates: int,
    seed: int,
) -> dict[str, Any]:
    scored = score_predictions(records, dataset)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scored:
        grouped[row["recommender"]].append(row)
    summaries = [
        _prediction_summary_row(method, grouped[method], replicates=replicates, seed=seed + index * 100)
        for index, method in enumerate(sorted(grouped))
    ]

    breakdowns: list[dict[str, Any]] = []
    for dimension in ("split", "language", "stratum", "size_band", "resource_category", "ambiguity"):
        cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in scored:
            cells[(row["recommender"], str(row[dimension]))].append(row)
        for (method, value), cell_rows in sorted(cells.items()):
            breakdowns.append(
                {
                    "dimension": dimension,
                    "value": value,
                    "recommender": method,
                    "records": len(cell_rows),
                    "families": len({item["workload_family"] for item in cell_rows}),
                    "profile_acceptable_rate": _round(mean(item["profile_acceptable"] for item in cell_rows)),
                    "image_acceptable_rate": _round(mean(item["image_acceptable"] for item in cell_rows)),
                    "joint_acceptable_rate": _round(mean(item["joint_acceptable"] for item in cell_rows)),
                    "raw_model_joint_acceptable_rate": _round(mean(item["raw_model_joint_acceptable"] for item in cell_rows)),
                }
            )

    paired: list[dict[str, Any]] = []
    methods = sorted(grouped)
    keyed_operational = {
        method: {
            (row["sample_id"], row["repeat_index"]): bool(row["joint_acceptable"])
            for row in method_rows
        }
        for method, method_rows in grouped.items()
    }
    for first_index, first in enumerate(methods):
        for second in methods[first_index + 1 :]:
            common = sorted(set(keyed_operational[first]) & set(keyed_operational[second]))
            if not common:
                continue
            result = exact_mcnemar(
                [keyed_operational[first][key] for key in common],
                [keyed_operational[second][key] for key in common],
            )
            paired.append(
                {
                    "comparison_type": "joint_acceptable_operational",
                    "first": first,
                    "second": second,
                    "pairs": len(common),
                    **result,
                }
            )
    adjusted = holm_adjust([float(row["p_value_raw"]) for row in paired])
    for row, value in zip(paired, adjusted):
        row["p_value_holm"] = _round_p(value)
        row["p_value_raw"] = _round_p(row["p_value_raw"])

    # Continuous paired latency comparisons via Wilcoxon signed-rank test
    paired_wilcoxon: list[dict[str, Any]] = []
    keyed_latencies = {
        method: {
            (row["sample_id"], row["repeat_index"]): row["latency_seconds"]
            for row in method_rows
            if row["latency_seconds"] is not None
        }
        for method, method_rows in grouped.items()
    }
    for first_index, first in enumerate(methods):
        for second in methods[first_index + 1 :]:
            common_keys = sorted(set(keyed_latencies[first]) & set(keyed_latencies[second]))
            if not common_keys:
                continue
            first_vals = [keyed_latencies[first][k] for k in common_keys]
            second_vals = [keyed_latencies[second][k] for k in common_keys]
            w_res = wilcoxon_signed_rank(first_vals, second_vals)
            paired_wilcoxon.append(
                {
                    "metric": "latency_seconds",
                    "first": first,
                    "second": second,
                    "pairs": len(common_keys),
                    "mean_diff_first_minus_second": _round(mean(a - b for a, b in zip(first_vals, second_vals))),
                    **w_res,
                }
            )
    if paired_wilcoxon:
        wilcoxon_adj = holm_adjust([float(row["p_value_raw"]) for row in paired_wilcoxon])
        for row, value in zip(paired_wilcoxon, wilcoxon_adj):
            row["p_value_holm"] = _round_p(value)
            row["p_value_raw"] = _round_p(row["p_value_raw"])

    # Profile confusion matrices
    confusion_matrices: dict[str, Any] = {}
    for method, method_rows in grouped.items():
        golds = [str(r["gold_preferred_profile"]) for r in method_rows]
        applied = [r["applied_profile"] for r in method_rows]
        confusion_matrices[method] = confusion_matrix(golds, applied)

    consistency: list[dict[str, Any]] = []
    family_cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in scored:
        family_cells[(row["recommender"], row["workload_family"])].append(row)
    for (method, family), family_rows in sorted(family_cells.items()):
        outputs = [
            (row["applied_profile"], row["predicted_image_id"])
            for row in family_rows
            if row["error_category"] is None
        ]
        dominant = Counter(outputs).most_common(1)[0][1] / len(outputs) if outputs else 0.0
        consistency.append(
            {
                "recommender": method,
                "workload_family": family,
                "records": len(family_rows),
                "dominant_output_rate": _round(dominant),
                "all_variants_joint_acceptable": all(row["joint_acceptable"] for row in family_rows),
            }
        )
    return {
        "summaries": summaries,
        "breakdowns": breakdowns,
        "paired_mcnemar_holm": paired,
        "paired_wilcoxon_holm": paired_wilcoxon,
        "confusion_matrices": confusion_matrices,
        "family_robustness": consistency,
        "scored_records": scored,
    }



def _group_by_evidence_and_method(records: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(str(record["evidence_class"]), str(record["recommender"]))].append(record)
    return grouped


def _enrich_system_trials(
    records: Sequence[Mapping[str, Any]],
    dataset: Mapping[str, Any],
) -> list[dict[str, Any]]:
    items = dataset_index(dataset)
    enriched: list[dict[str, Any]] = []
    for record in records:
        if record["sample_id"] not in items:
            raise ValueError(f"unknown system-trial sample {record['sample_id']!r}")
        item = items[record["sample_id"]]
        _validate_joined_identity(record, item)
        cpu_util = (
            record["cpu_usage_mean_m"] / record["cpu_request_m"]
            if record["cpu_usage_mean_m"] is not None
            else None
        )
        memory_util = (
            record["memory_usage_mean_mib"] / record["memory_request_mib"]
            if record["memory_usage_mean_mib"] is not None
            else None
        )
        peak_memory_to_request = (
            record["memory_usage_peak_mib"] / record["memory_request_mib"]
            if record["memory_usage_peak_mib"] is not None
            else None
        )
        enriched.append(
            {
                **dict(record),
                "cpu_request_utilization": cpu_util,
                "memory_request_utilization": memory_util,
                "peak_memory_to_request": peak_memory_to_request,
                "applied_profile_acceptable": record["applied_profile"] in item["gold"]["acceptable_profiles"],
                "applied_image_acceptable": record["applied_image_id"] in item["gold"]["acceptable_image_ids"],
                "cleanup_failure": record["cleanup_status"] != "completed",
            }
        )
    return enriched


def analyze_system_trials(
    records: Sequence[Mapping[str, Any]],
    dataset: Mapping[str, Any],
    *,
    replicates: int,
    seed: int,
) -> list[dict[str, Any]]:
    enriched = _enrich_system_trials(records, dataset)
    summaries: list[dict[str, Any]] = []
    binary_fields = (
        "pod_ready",
        "pending_failure",
        "oom_killed",
        "image_pull_failure",
        "workload_success",
        "applied_profile_acceptable",
        "applied_image_acceptable",
        "cleanup_failure",
    )
    continuous_fields = (
        "cpu_request_utilization",
        "memory_request_utilization",
        "peak_memory_to_request",
        "pending_duration_seconds",
        "time_to_ready_seconds",
        "workload_duration_seconds",
    )
    for group_index, ((evidence_class, method), rows) in enumerate(sorted(_group_by_evidence_and_method(enriched).items())):
        result: dict[str, Any] = {
            "evidence_class": evidence_class,
            "recommender": method,
            "trials": len(rows),
            "families": len({row["workload_family"] for row in rows}),
            "cpu_usage_availability": _round(mean(row["cpu_usage_mean_m"] is not None for row in rows)),
            "memory_usage_availability": _round(mean(row["memory_usage_mean_mib"] is not None for row in rows)),
        }
        for field_index, field in enumerate(binary_fields + continuous_fields):
            value = mean(row[field] for row in rows)
            low, high = _metric_ci(
                rows,
                field,
                replicates=replicates,
                seed=seed + group_index * 100 + field_index,
            )
            result[field + ("_rate" if field in binary_fields else "_mean")] = _round(value)
            result[field + "_ci_low"] = _round(low)
            result[field + "_ci_high"] = _round(high)
        result["time_to_ready_p95_seconds"] = _round(
            quantile([row["time_to_ready_seconds"] for row in rows if row["time_to_ready_seconds"] is not None], 0.95)
        )
        summaries.append(result)
    return summaries


def compare_system_trials(
    records: Sequence[Mapping[str, Any]],
    dataset: Mapping[str, Any],
    *,
    replicates: int,
    seed: int,
) -> dict[str, list[dict[str, Any]]]:
    """Paired system comparisons within evidence class and workload/repeat block."""

    enriched = _enrich_system_trials(records, dataset)
    by_class_method: dict[tuple[str, str], dict[tuple[str, int], dict[str, Any]]] = {}
    for evidence_class, method in sorted(
        {(row["evidence_class"], row["recommender"]) for row in enriched}
    ):
        keyed: dict[tuple[str, int], dict[str, Any]] = {}
        for row in enriched:
            if (row["evidence_class"], row["recommender"]) != (evidence_class, method):
                continue
            key = (row["workload_family"], row["repeat_index"])
            if key in keyed:
                raise ValueError(
                    "system comparisons require one trial per evidence/method/family/repeat"
                )
            keyed[key] = row
        by_class_method[(evidence_class, method)] = keyed

    binary_rows: list[dict[str, Any]] = []
    continuous_rows: list[dict[str, Any]] = []
    binary_endpoints = ("oom_killed", "pending_failure", "workload_success")
    continuous_endpoints = (
        "cpu_request_utilization",
        "memory_request_utilization",
        "peak_memory_to_request",
        "pending_duration_seconds",
        "time_to_ready_seconds",
    )
    evidence_classes = sorted({key[0] for key in by_class_method})
    for class_index, evidence_class in enumerate(evidence_classes):
        methods = sorted(method for klass, method in by_class_method if klass == evidence_class)
        for first_index, first in enumerate(methods):
            for second in methods[first_index + 1 :]:
                first_rows = by_class_method[(evidence_class, first)]
                second_rows = by_class_method[(evidence_class, second)]
                common = sorted(set(first_rows) & set(second_rows))
                for endpoint in binary_endpoints:
                    result = exact_mcnemar(
                        [bool(first_rows[key][endpoint]) for key in common],
                        [bool(second_rows[key][endpoint]) for key in common],
                    )
                    binary_rows.append(
                        {
                            "evidence_class": evidence_class,
                            "endpoint": endpoint,
                            "first": first,
                            "second": second,
                            "pairs": len(common),
                            **result,
                        }
                    )
                for endpoint_index, endpoint in enumerate(continuous_endpoints):
                    differences = [
                        {
                            "workload_family": key[0],
                            "difference": second_rows[key][endpoint] - first_rows[key][endpoint],
                        }
                        for key in common
                        if first_rows[key][endpoint] is not None
                        and second_rows[key][endpoint] is not None
                    ]
                    estimate = mean(row["difference"] for row in differences)
                    low, high = cluster_bootstrap_ci(
                        differences,
                        lambda sample: mean(row["difference"] for row in sample),
                        replicates=replicates,
                        seed=seed + class_index * 1000 + first_index * 100 + endpoint_index,
                    ) if differences else (None, None)
                    continuous_rows.append(
                        {
                            "evidence_class": evidence_class,
                            "endpoint": endpoint,
                            "first": first,
                            "second": second,
                            "paired_trials": len(differences),
                            "mean_difference_second_minus_first": _round(estimate),
                            "ci_low": _round(low),
                            "ci_high": _round(high),
                        }
                    )
    for evidence_class in evidence_classes:
        selected = [row for row in binary_rows if row["evidence_class"] == evidence_class]
        adjusted = holm_adjust([float(row["p_value_raw"]) for row in selected])
        for row, value in zip(selected, adjusted):
            row["p_value_raw"] = _round_p(row["p_value_raw"])
            row["p_value_holm"] = _round_p(value)
    return {"binary": binary_rows, "continuous": continuous_rows}


def analyze_user_events(
    records: Sequence[Mapping[str, Any]],
    dataset: Mapping[str, Any],
    *,
    replicates: int = 2000,
    seed: int = 20260808,
) -> list[dict[str, Any]]:
    items = dataset_index(dataset)
    for record in records:
        if record["sample_id"] not in items:
            raise ValueError(f"unknown user-event sample {record['sample_id']!r}")
        _validate_joined_identity(record, items[record["sample_id"]])
    summaries: list[dict[str, Any]] = []
    for group_index, ((evidence_class, method), rows) in enumerate(sorted(_group_by_evidence_and_method(records).items())):
        decisions = [row for row in rows if row["action"] in {"accept", "override"}]
        accepted = sum(row["action"] == "accept" for row in decisions)
        low, high = cluster_bootstrap_ci(
            rows,
            lambda sample: (
                sum(row["action"] == "accept" for row in sample)
                / sum(row["action"] in {"accept", "override"} for row in sample)
                if any(row["action"] in {"accept", "override"} for row in sample)
                else None
            ),
            cluster_field="participant_block_id",
            replicates=replicates,
            seed=seed + group_index,
        )
        summaries.append(
            {
                "evidence_class": evidence_class,
                "recommender": method,
                "exposures": len(rows),
                "participant_blocks": len({row["participant_block_id"] for row in rows}),
                "decided": len(decisions),
                "acceptance_rate_decided": _round(accepted / len(decisions) if decisions else None),
                "acceptance_ci_low": _round(low),
                "acceptance_ci_high": _round(high),
                "acceptance_rate_all_exposures": _round(sum(row["action"] == "accept" for row in rows) / len(rows)),
                "override_rate": _round(sum(row["action"] == "override" for row in rows) / len(rows)),
                "cancel_rate": _round(sum(row["action"] == "cancel" for row in rows) / len(rows)),
                "task_success_rate_available": _round(mean(row["task_success"] for row in rows)),
                "decision_time_median_seconds": _round(quantile([row["decision_time_seconds"] for row in rows if row["decision_time_seconds"] is not None], 0.5)),
            }
        )
    return summaries


def compare_user_acceptance(
    records: Sequence[Mapping[str, Any]],
    dataset: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Exact paired acceptance comparisons for repeated participant/task blocks."""

    items = dataset_index(dataset)
    decided = [record for record in records if record["action"] in {"accept", "override"}]
    keyed: dict[tuple[str, str], dict[tuple[str, str], Mapping[str, Any]]] = defaultdict(dict)
    for record in decided:
        if record["sample_id"] not in items:
            raise ValueError(f"unknown user-event sample {record['sample_id']!r}")
        _validate_joined_identity(record, items[record["sample_id"]])
        group = (str(record["evidence_class"]), str(record["recommender"]))
        pair_key = (str(record["participant_block_id"]), str(record["sample_id"]))
        if pair_key in keyed[group]:
            raise ValueError(
                "paired user analysis requires one decided exposure per method/participant/sample"
            )
        keyed[group][pair_key] = record
    comparisons: list[dict[str, Any]] = []
    evidence_classes = sorted({key[0] for key in keyed})
    for evidence_class in evidence_classes:
        methods = sorted(method for klass, method in keyed if klass == evidence_class)
        for first_index, first in enumerate(methods):
            for second in methods[first_index + 1 :]:
                first_rows = keyed[(evidence_class, first)]
                second_rows = keyed[(evidence_class, second)]
                common = sorted(set(first_rows) & set(second_rows))
                if not common:
                    continue
                result = exact_mcnemar(
                    [first_rows[key]["action"] == "accept" for key in common],
                    [second_rows[key]["action"] == "accept" for key in common],
                )
                comparisons.append(
                    {
                        "evidence_class": evidence_class,
                        "first": first,
                        "second": second,
                        "participant_task_pairs": len(common),
                        **result,
                    }
                )
    for evidence_class in evidence_classes:
        selected = [row for row in comparisons if row["evidence_class"] == evidence_class]
        adjusted = holm_adjust([float(row["p_value_raw"]) for row in selected])
        for row, value in zip(selected, adjusted):
            row["p_value_raw"] = _round_p(row["p_value_raw"])
            row["p_value_holm"] = _round_p(value)
    return comparisons


def analyze_reprovision_trials(
    records: Sequence[Mapping[str, Any]],
    dataset: Mapping[str, Any],
    *,
    replicates: int = 2000,
    seed: int = 20260808,
) -> list[dict[str, Any]]:
    items = dataset_index(dataset)
    enriched: list[dict[str, Any]] = []
    for record in records:
        if record["sample_id"] not in items:
            raise ValueError(f"unknown re-provision sample {record['sample_id']!r}")
        _validate_joined_identity(record, items[record["sample_id"]])
        success = bool(
            record["outcome"] == "completed"
            and record["replacement_ready"]
            and record["pvc_continuity_verified"]
            and record["workload_resume_verified"]
            and not record["pending_failure"]
            and not record["oom_killed"]
        )
        enriched.append({**dict(record), "reprovision_success": success})
    summaries: list[dict[str, Any]] = []
    for group_index, ((evidence_class, method), rows) in enumerate(sorted(_group_by_evidence_and_method(enriched).items())):
        successes = sum(row["reprovision_success"] for row in rows)
        low, high = cluster_bootstrap_ci(
            rows,
            lambda sample: mean(row["reprovision_success"] for row in sample),
            replicates=replicates,
            seed=seed + group_index,
        )
        rollback_attempts = [row for row in rows if row["rollback_attempted"]]
        summaries.append(
            {
                "evidence_class": evidence_class,
                "recommender": method,
                "trials": len(rows),
                "success_rate": _round(successes / len(rows)),
                "success_ci_low": _round(low),
                "success_ci_high": _round(high),
                "replacement_ready_rate": _round(mean(row["replacement_ready"] for row in rows)),
                "pvc_continuity_rate": _round(mean(row["pvc_continuity_verified"] for row in rows)),
                "workload_resume_rate": _round(mean(row["workload_resume_verified"] for row in rows)),
                "rollback_attempt_rate": _round(len(rollback_attempts) / len(rows)),
                "rollback_success_rate_when_attempted": _round(mean(row["rollback_successful"] for row in rollback_attempts)),
                "cleanup_completed_rate": _round(mean(row["cleanup_status"] == "completed" for row in rows)),
                "downtime_median_seconds": _round(quantile([row["downtime_seconds"] for row in rows if row["downtime_seconds"] is not None], 0.5)),
                "downtime_p95_seconds": _round(quantile([row["downtime_seconds"] for row in rows if row["downtime_seconds"] is not None], 0.95)),
            }
        )
    return summaries


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for field in row:
            if field not in fieldnames:
                fieldnames.append(field)
    with path.open("x", encoding="utf-8", newline="") as handle:
        if not fieldnames:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _observed_available(rows: Sequence[Mapping[str, Any]]) -> bool:
    return any(row.get("evidence_class") == "observed" for row in rows)


def compute_claim_gates(
    recommendation: Mapping[str, Any],
    system_records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Compute claim gates for the authoritative research questions RQ1-RQ5."""
    executed_methods = [str(row["recommender"]) for row in recommendation.get("summaries", [])]
    scored_records = recommendation.get("scored_records", [])

    has_real_external_llm = any(
        r["recommender"] == "external_llm"
        and r.get("execution_mode") == "live_backend"
        and r.get("error_category") is None
        and not r.get("fallback_used")
        for r in scored_records
    )
    has_real_ollama = any(
        r["recommender"] == "self_hosted_local_ollama_llm"
        and r.get("execution_mode") == "live_backend"
        and r.get("error_category") is None
        and not r.get("fallback_used")
        for r in scored_records
    )
    has_static = "static_profile_baseline" in executed_methods
    has_rule_based = "rule_based_mapping" in executed_methods

    real_methods: list[str] = []
    if has_static:
        real_methods.append("static_profile_baseline")
    if has_rule_based:
        real_methods.append("rule_based_mapping")
    if has_real_external_llm:
        real_methods.append("external_llm")
    if has_real_ollama:
        real_methods.append("self_hosted_local_ollama_llm")

    # RQ1: How do the four approaches differ in recommendation quality?
    if len(real_methods) == 4:
        rq1_status = "CLAIMABLE"
        rq1_reason = "Evaluated across all 4 canonical approaches with real predictions."
    elif len(real_methods) >= 2:
        rq1_status = "PARTIALLY CLAIMABLE"
        rq1_reason = (
            f"Only {len(real_methods)} of 4 methods have real recommendation-quality evidence "
            f"({', '.join(sorted(real_methods))}). Narrow comparison between static baseline "
            "and rule-based mapping is supported, but not a four-method conclusion."
        )
    else:
        rq1_status = "NOT CLAIMABLE"
        rq1_reason = "Insufficient methods evaluated to draw comparative recommendation quality conclusions."

    # RQ2: Do LLM-based approaches improve recommendation quality compared with the static baseline and rule-based mapping?
    if (has_static or has_rule_based) and has_real_external_llm and has_real_ollama:
        rq2_status = "CLAIMABLE"
        rq2_reason = "Recommendation quality comparison completed between LLM approaches and deterministic baselines."
    elif (has_static or has_rule_based) and (has_real_external_llm or has_real_ollama):
        rq2_status = "PARTIALLY CLAIMABLE"
        rq2_reason = "Partial comparison available for only one LLM backend against deterministic baselines."
    else:
        rq2_status = "NOT CLAIMABLE"
        rq2_reason = "Neither external LLM nor Ollama has been evaluated with real model inference."

    # RQ3: What additional latency, failures, fallbacks, monetary cost, resource consumption, and operational overhead do LLM approaches introduce?
    if has_real_external_llm and has_real_ollama:
        rq3_status = "CLAIMABLE"
        rq3_reason = "Inference latency, failures, fallbacks, token cost, and resource telemetry measured for both LLM approaches."
    elif has_real_external_llm or has_real_ollama:
        rq3_status = "PARTIALLY CLAIMABLE"
        rq3_reason = "Inference latency and telemetry available for only one LLM approach."
    else:
        rq3_status = "NOT CLAIMABLE"
        rq3_reason = "No real external/Ollama inference latency, failures, cost, or resource-overhead measurements exist yet."

    # RQ4: When recommendations are applied, how does each approach affect workload success, OOM events, Pending failures, runtime, and resource efficiency in Kubernetes and JupyterHub?
    observed_system = [r for r in system_records if r.get("evidence_class") == "observed"]
    observed_methods = {str(r["recommender"]) for r in observed_system}
    if len(observed_methods) >= 4:
        rq4_status = "CLAIMABLE"
        rq4_reason = "Observed Stage C Kubernetes/JupyterHub pod outcomes evaluated across all four approaches."
    elif len(observed_methods) > 0:
        rq4_status = "PARTIALLY CLAIMABLE"
        rq4_reason = f"Stage C cluster trials observed for {len(observed_methods)} of 4 methods ({', '.join(sorted(observed_methods))})."
    else:
        rq4_status = "NOT CLAIMABLE"
        rq4_reason = "No four-method applied Kubernetes/JupyterHub experiment exists yet (requires observed Stage C cluster telemetry)."

    # RQ5: What are the quality–latency–reliability–cost–privacy trade-offs between an external LLM and a locally hosted Ollama model?
    if has_real_external_llm and has_real_ollama:
        rq5_status = "CLAIMABLE"
        rq5_reason = "Empirical head-to-head evaluation completed between external LLM API and local Ollama model."
    else:
        rq5_status = "NOT CLAIMABLE"
        rq5_reason = "No empirical external-vs-Ollama comparison exists yet (requires live external LLM and local Ollama evaluations)."

    return [
        {
            "id": "RQ1",
            "question": "How do the four approaches differ in recommendation quality?",
            "status": rq1_status,
            "reason": rq1_reason,
        },
        {
            "id": "RQ2",
            "question": "Do LLM-based approaches improve recommendation quality compared with the static baseline and rule-based mapping?",
            "status": rq2_status,
            "reason": rq2_reason,
        },
        {
            "id": "RQ3",
            "question": "What additional latency, failures, fallbacks, monetary cost, resource consumption, and operational overhead do LLM approaches introduce?",
            "status": rq3_status,
            "reason": rq3_reason,
        },
        {
            "id": "RQ4",
            "question": "When recommendations are applied, how does each approach affect workload success, OOM events, Pending failures, runtime, and resource efficiency in Kubernetes and JupyterHub?",
            "status": rq4_status,
            "reason": rq4_reason,
        },
        {
            "id": "RQ5",
            "question": "What are the quality–latency–reliability–cost–privacy trade-offs between an external LLM and a locally hosted Ollama model?",
            "status": rq5_status,
            "reason": rq5_reason,
        },
    ]


def _write_report(
    path: Path,
    recommendation: Mapping[str, Any],
    system_records: Sequence[Mapping[str, Any]],
    user_records: Sequence[Mapping[str, Any]],
    reprovision_records: Sequence[Mapping[str, Any]],
) -> None:
    lines = [
        "# Protocol-v4 Recommender Evaluation Report",
        "",
        "> **Evaluation Status**: Framework implemented and validated for four evaluation methods.",
        "> Deterministic offline evaluation executed for methods present in the predictions stream.",
        "> Live external LLM API evaluations, local Ollama daemon executions, and Kubernetes Stage C cluster experiments require separate live environment telemetry and credentials.",
        "",
        "## 1. Recommendation Quality Across Methods",
        "",
        "| Recommender | N | Profile Acc | Image Acc | Joint Acc (Ops) | Raw Model Joint Acc | Underprov | Overprov | Fallback Rate | Median Latency (s) |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in recommendation["summaries"]:
        lines.append(
            "| **{recommender}** | {records} | {profile:.4f} | {image:.4f} | **{joint:.4f}** | {raw_joint} | {under:.4f} | {over:.4f} | {fallback} | {latency} |".format(
                recommender=row["recommender"],
                records=row["records"],
                profile=float(row.get("profile_acceptable_rate") or 0.0),
                image=float(row.get("image_acceptable_rate") or 0.0),
                joint=float(row.get("joint_acceptable_rate") or 0.0),
                raw_joint=f"{float(row['raw_model_joint_acceptable_rate']):.4f}" if row.get("raw_model_joint_acceptable_rate") is not None else "N/A",
                under=float(row.get("underprovisioned_rate") or 0.0),
                over=float(row.get("overprovisioned_rate") or 0.0),
                fallback=f"{float(row['fallback_used_rate']):.4f}" if row.get("fallback_used_rate") is not None else "0.0000",
                latency=f"{float(row['latency_median_seconds']):.3f}" if row.get("latency_median_seconds") is not None else "N/A",
            )
        )

    lines.extend([
        "",
        "## 2. Statistical Hypothesis Testing",
        "",
        "### Pairwise Joint Accuracy (Exact McNemar Test with Holm-Bonferroni Correction)",
        "",
        "| Method A | Method B | N Pairs | A-Only Correct | B-Only Correct | Raw p-value | Holm Adjusted p | Significance (α=0.05) |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- |",
    ])
    for row in recommendation.get("paired_mcnemar_holm", []):
        sig = "**Significant**" if float(row["p_value_holm"]) < 0.05 else "Not Significant"
        lines.append(
            f"| {row['first']} | {row['second']} | {row['pairs']} | {row['first_only_correct']} | {row['second_only_correct']} | {row['p_value_raw']} | {row['p_value_holm']} | {sig} |"
        )

    if recommendation.get("paired_wilcoxon_holm"):
        lines.extend([
            "",
            "### Pairwise Latency Comparison (Paired Wilcoxon Signed-Rank Test with Holm Correction)",
            "",
            "| Method A | Method B | N Pairs | Mean Diff (A - B s) | Statistic | z-score | Raw p-value | Holm Adjusted p | Significance (α=0.05) |",
            "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- |",
        ])
        for row in recommendation["paired_wilcoxon_holm"]:
            sig = "**Significant**" if float(row["p_value_holm"]) < 0.05 else "Not Significant"
            lines.append(
                f"| {row['first']} | {row['second']} | {row['pairs']} | {row['mean_diff_first_minus_second']} | {row['statistic']} | {row['z_score']} | {row['p_value_raw']} | {row['p_value_holm']} | {sig} |"
            )

    lines.extend([
        "",
        "## 3. Latency, Token Usage, and Estimated Cost Summary",
        "",
        "| Recommender | Median Latency (s) | P95 Latency (s) | Mean Prompt Tokens | Mean Completion Tokens | Mean Total Tokens | Est. Cost / 1k Reqs ($) |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for row in recommendation["summaries"]:
        lines.append(
            "| **{recommender}** | {med_lat} | {p95_lat} | {prompt_tok} | {comp_tok} | {tot_tok} | {cost} |".format(
                recommender=row["recommender"],
                med_lat=f"{float(row['latency_median_seconds']):.4f}" if row.get("latency_median_seconds") is not None else "N/A",
                p95_lat=f"{float(row['latency_p95_seconds']):.4f}" if row.get("latency_p95_seconds") is not None else "N/A",
                prompt_tok=f"{float(row['prompt_tokens_mean']):.1f}" if row.get("prompt_tokens_mean") is not None else "N/A",
                comp_tok=f"{float(row['completion_tokens_mean']):.1f}" if row.get("completion_tokens_mean") is not None else "N/A",
                tot_tok=f"{float(row['total_tokens_mean']):.1f}" if row.get("total_tokens_mean") is not None else "N/A",
                cost=f"${float(row['estimated_cost_usd_per_1k']):.4f}" if row.get("estimated_cost_usd_per_1k") is not None else "N/A",
            )
        )

    lines.extend(["", "## 4. Thesis Claim Gates & Evidence Status", ""])
    gates = compute_claim_gates(recommendation, system_records)
    for gate in gates:
        status_md = f"**{gate['status']}**"
        lines.append(f"- **{gate['id']}**: {gate['question']}")
        lines.append(f"  - **Status**: {status_md}")
        lines.append(f"  - **Reason**: {gate['reason']}")

    lines.extend([
        "",
        "## 5. Statistical Methodology",
        "",
        "- **Resampling Strategy**: Confidence intervals are estimated using cluster percentile bootstrap over `workload_family` (2,000 replicates), preserving paraphrase and linguistic correlations.",
        "- **Binary Paired Hypothesis Testing**: Exact two-tailed McNemar tests with step-down Holm-Bonferroni family-wise error rate adjustment (α = 0.05).",
        "- **Continuous Paired Hypothesis Testing**: Two-tailed Wilcoxon signed-rank tests with continuity and tie correction, paired across common sample IDs and repetitions.",
        "- **Fairness Rules**: The static baseline is frozen to `medium` profile (`minimal-python` image); invalid LLM predictions triggering rule fallbacks are tracked as fallback outcomes and never credited as raw LLM prediction successes.",
        "- **Pricing Provenance**: Estimated token cost is computed only when versioned pricing configuration is explicitly supplied; unconfigured methods report N/A.",
        "",
    ])
    with path.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite analysis directory {args.out}")
    dataset = load_dataset(args.dataset)
    predictions = read_jsonl(args.predictions, validate_prediction)
    system_records = read_jsonl(args.system_trials, validate_system_trial) if args.system_trials else []
    user_records = read_jsonl(args.user_events, validate_user_event) if args.user_events else []
    reprovision_records = (
        read_jsonl(args.reprovision_trials, validate_reprovision_trial)
        if args.reprovision_trials
        else []
    )
    recommendation = analyze_recommendations(
        predictions,
        dataset,
        replicates=args.bootstrap_replicates,
        seed=args.seed,
    )
    system_summary = analyze_system_trials(
        system_records,
        dataset,
        replicates=args.bootstrap_replicates,
        seed=args.seed,
    ) if system_records else []
    system_comparisons = compare_system_trials(
        system_records,
        dataset,
        replicates=args.bootstrap_replicates,
        seed=args.seed,
    ) if system_records else {"binary": [], "continuous": []}
    user_summary = analyze_user_events(
        user_records,
        dataset,
        replicates=args.bootstrap_replicates,
        seed=args.seed,
    ) if user_records else []
    user_comparisons = compare_user_acceptance(user_records, dataset) if user_records else []
    reprovision_summary = analyze_reprovision_trials(
        reprovision_records,
        dataset,
        replicates=args.bootstrap_replicates,
        seed=args.seed,
    ) if reprovision_records else []

    # Latency and cost summary rows
    latency_cost_rows = [
        {
            "recommender": row["recommender"],
            "records": row["records"],
            "latency_mean_seconds": row.get("latency_mean_seconds"),
            "latency_median_seconds": row.get("latency_median_seconds"),
            "latency_p95_seconds": row.get("latency_p95_seconds"),
            "inference_latency_median_seconds": row.get("inference_latency_median_seconds"),
            "prompt_tokens_mean": row.get("prompt_tokens_mean"),
            "completion_tokens_mean": row.get("completion_tokens_mean"),
            "total_tokens_mean": row.get("total_tokens_mean"),
            "estimated_cost_usd_per_1k": row.get("estimated_cost_usd_per_1k"),
        }
        for row in recommendation["summaries"]
    ]

    args.out.mkdir(parents=True)
    _write_csv(args.out / "recommendation-summary.csv", recommendation["summaries"])
    _write_csv(args.out / "recommendation-breakdowns.csv", recommendation["breakdowns"])
    _write_csv(args.out / "pairwise-mcnemar-holm.csv", recommendation["paired_mcnemar_holm"])
    _write_csv(args.out / "pairwise-wilcoxon-holm.csv", recommendation.get("paired_wilcoxon_holm", []))
    _write_csv(args.out / "latency-cost-summary.csv", latency_cost_rows)
    _write_csv(args.out / "family-robustness.csv", recommendation["family_robustness"])
    _write_csv(args.out / "system-effectiveness.csv", system_summary)
    _write_csv(args.out / "system-paired-binary.csv", system_comparisons["binary"])
    _write_csv(args.out / "system-paired-continuous.csv", system_comparisons["continuous"])
    _write_csv(args.out / "user-acceptance.csv", user_summary)
    _write_csv(args.out / "user-paired-acceptance.csv", user_comparisons)
    _write_csv(args.out / "reprovisioning-effectiveness.csv", reprovision_summary)

    _write_json(args.out / "profile-confusion-matrices.json", recommendation.get("confusion_matrices", {}))
    _write_json(
        args.out / "analysis.json",
        {
            "recommendation": {
                key: value for key, value in recommendation.items() if key != "scored_records"
            },
            "system_effectiveness": system_summary,
            "system_paired_comparisons": system_comparisons,
            "user_acceptance": user_summary,
            "user_paired_acceptance": user_comparisons,
            "reprovisioning": reprovision_summary,
        },
    )
    input_paths = [args.dataset, args.predictions]
    input_paths.extend(
        path
        for path in (args.system_trials, args.user_events, args.reprovision_trials)
        if path is not None
    )
    gates = compute_claim_gates(recommendation, system_records)
    claim_gates_map = {
        gate["id"]: {
            "question": gate["question"],
            "status": gate["status"],
            "reason": gate["reason"],
        }
        for gate in gates
    }
    manifest = {
        "protocol_version": "4.0.0",
        "created_utc": _now_utc(),
        "dataset": dataset_summary(dataset),
        "bootstrap": {
            "unit": "workload_family",
            "replicates": args.bootstrap_replicates,
            "seed": args.seed,
        },
        "input_sha256": {str(path): file_sha256(path) for path in input_paths},
        "record_counts": {
            "predictions": len(predictions),
            "system_trials": len(system_records),
            "user_events": len(user_records),
            "reprovision_trials": len(reprovision_records),
        },
        "claim_gates": claim_gates_map,
        "secondary_evidence_dimensions": {
            "system_effectiveness_observed": _observed_available(system_records),
            "user_acceptance_observed": _observed_available(user_records),
            "reprovisioning_observed": _observed_available(reprovision_records),
        },
    }
    _write_json(args.out / "analysis-manifest.json", manifest)
    _write_report(
        args.out / "REPORT.md",
        recommendation,
        system_records,
        user_records,
        reprovision_records,
    )
    return manifest


def main(argv: list[str] | None = None) -> int:

    parser = argparse.ArgumentParser(description="Analyze protocol-v4 evaluation records.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--system-trials", type=Path)
    parser.add_argument("--user-events", type=Path)
    parser.add_argument("--reprovision-trials", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "v4-analysis")
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260808)
    args = parser.parse_args(argv)
    result = analyze(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
