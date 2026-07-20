"""Generate deterministic evaluation summaries from raw experiment records."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable

import yaml

from experiments.jsonl_io import read_jsonl
from experiments.result_schema import CSV_FIELDS


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_DIR = ROOT / "results"
DEFAULT_MANIFEST = ROOT / "benchmarks" / "workloads.yaml"
DEFAULT_RESULTS_MD = ROOT / "docs" / "evaluation" / "RESULTS.md"
METHOD_ORDER = ("static_manual", "intent_only", "context_aware")
PROFILE_ORDER = {"small": 0, "medium": 1, "large": 2, "gpu_or_large": 3}
BOUNDARY_CATEGORIES = {"boundary", "conflicting_signal", "policy"}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze immutable experiment raw JSONL.")
    parser.add_argument("--experiment-dir", type=Path, help="Directory containing results.jsonl and optional matrix/environment files.")
    parser.add_argument("--raw-jsonl", type=Path, help="Explicit raw results JSONL path.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--results-md", type=Path, default=DEFAULT_RESULTS_MD)
    parser.add_argument("--environment-report", type=Path, help="Optional capability-check JSON to summarize in RESULTS.md.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def _load_manifest(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _read_json(path: Path) -> Any | None:
    if not path or not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _read_matrix(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing analysis output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({field for row in rows for field in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in fieldnames})


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return value


def _profile_delta(applied: str | None, acceptable: list[str]) -> int | None:
    if not applied or not acceptable:
        return None
    applied_rank = PROFILE_ORDER[applied]
    acceptable_ranks = [PROFILE_ORDER[profile] for profile in acceptable if profile in PROFILE_ORDER]
    if not acceptable_ranks:
        return None
    if applied_rank < min(acceptable_ranks):
        return applied_rank - min(acceptable_ranks)
    if applied_rank > max(acceptable_ranks):
        return applied_rank - max(acceptable_ranks)
    return 0


def _recommendation_outcome(applied: str | None, acceptable: list[str]) -> str:
    delta = _profile_delta(applied, acceptable)
    if delta is None:
        return "missing"
    if delta < 0:
        return "under"
    if delta > 0:
        return "over"
    return "acceptable"


def _summary_stats(values: Iterable[float | int | None]) -> dict[str, Any]:
    materialized = list(values)
    clean = sorted(float(value) for value in materialized if value is not None)
    missing = sum(1 for value in materialized if value is None)
    if not clean:
        return {
            "n": 0,
            "missing": missing,
            "median": None,
            "iqr": None,
            "mean": None,
            "stddev": None,
        }
    q1, q3 = _quartiles(clean)
    return {
        "n": len(clean),
        "missing": missing,
        "median": round(statistics.median(clean), 6),
        "iqr": round(q3 - q1, 6),
        "mean": round(statistics.fmean(clean), 6),
        "stddev": round(statistics.stdev(clean), 6) if len(clean) > 1 else 0.0,
    }


def _quartiles(values: list[float]) -> tuple[float, float]:
    if len(values) == 1:
        return values[0], values[0]
    midpoint = len(values) // 2
    if len(values) % 2:
        lower = values[:midpoint]
        upper = values[midpoint + 1 :]
    else:
        lower = values[:midpoint]
        upper = values[midpoint:]
    return statistics.median(lower), statistics.median(upper)


def _rate(count: int, total: int) -> float | None:
    return round(count / total, 6) if total else None


def _group(records: list[dict[str, Any]], *keys: str) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for record in records:
        key = tuple(_hashable_key(record.get(item)) for item in keys)
        grouped.setdefault(key, []).append(record)
    return dict(sorted(grouped.items(), key=lambda item: tuple(str(value) for value in item[0])))


def _hashable_key(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(value)
    if isinstance(value, dict):
        return tuple(sorted(value.items()))
    return value


def _method_sort_key(method: str) -> int:
    return METHOD_ORDER.index(method) if method in METHOD_ORDER else len(METHOD_ORDER)


def _enrich_records(records: list[dict[str, Any]], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    workloads = {workload["workload_id"]: workload for workload in manifest["workloads"]}
    enriched = []
    for record in records:
        workload = workloads[record["workload_id"]]
        acceptable = list(workload["expected_acceptable_profiles"])
        expected_recommender = list(workload["expected_recommender_profiles"])
        memory_waste_ratio = None
        if record["memory_request_mi"] is not None and record["peak_memory_mi"]:
            memory_waste_ratio = record["memory_request_mi"] / record["peak_memory_mi"]
        cpu_waste_ratio = None
        if record["cpu_request_m"] is not None and record["cpu_usage_m"]:
            cpu_waste_ratio = record["cpu_request_m"] / record["cpu_usage_m"]
        row = dict(record)
        row.update(
            {
                "category": workload["category"],
                "expected_acceptable_profiles": acceptable,
                "expected_recommender_profiles": expected_recommender,
                "expected_pressure_type": workload["expected_pressure_type"],
                "recommendation_outcome": _recommendation_outcome(record["applied_profile"], acceptable),
                "profile_delta": _profile_delta(record["applied_profile"], acceptable),
                "missing_cpu_usage": record["cpu_usage_m"] is None,
                "missing_peak_memory": record["peak_memory_mi"] is None,
                "missing_pending_time": record["pod_pending_duration_seconds"] is None,
                "missing_restart_count": record["restart_or_respawn_count"] is None,
                "memory_request_to_peak_ratio": round(memory_waste_ratio, 6) if memory_waste_ratio is not None else None,
                "cpu_request_to_observed_ratio": round(cpu_waste_ratio, 6) if cpu_waste_ratio is not None else None,
                "excluded_from_comparative_summary": False,
                "exclusion_reason": None,
            }
        )
        enriched.append(row)
    return sorted(enriched, key=lambda item: (item["workload_id"], _method_sort_key(item["method"]), item["repeat_index"]))


def _summary_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{field: record.get(field) for field in list(CSV_FIELDS) + [
        "category",
        "expected_acceptable_profiles",
        "expected_recommender_profiles",
        "expected_pressure_type",
        "recommendation_outcome",
        "profile_delta",
        "memory_request_to_peak_ratio",
        "cpu_request_to_observed_ratio",
        "missing_cpu_usage",
        "missing_peak_memory",
        "missing_pending_time",
        "missing_restart_count",
        "excluded_from_comparative_summary",
        "exclusion_reason",
    ]} for record in records]


def _run_count_rows(records: list[dict[str, Any]], matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    planned_by_method = {method: 0 for method in METHOD_ORDER}
    for item in matrix:
        planned_by_method[item["method"]] = planned_by_method.get(item["method"], 0) + 1
    if not matrix:
        for method, items in _group(records, "method").items():
            planned_by_method[method[0]] = len(items)

    rows = []
    for method in sorted(planned_by_method, key=_method_sort_key):
        items = [record for record in records if record["method"] == method]
        rows.append(
            {
                "method": method,
                "planned_count": planned_by_method[method],
                "recorded_count": len(items),
                "successful_count": sum(1 for record in items if record["success"] is True),
                "failed_count": sum(1 for record in items if record["success"] is False),
                "timeout_count": sum(1 for record in items if record["timeout"] is True),
                "excluded_count": sum(1 for record in items if record["excluded_from_comparative_summary"]),
                "missing_cpu_usage_count": sum(1 for record in items if record["missing_cpu_usage"]),
                "missing_peak_memory_count": sum(1 for record in items if record["missing_peak_memory"]),
                "missing_pending_time_count": sum(1 for record in items if record["missing_pending_time"]),
                "missing_restart_count": sum(1 for record in items if record["missing_restart_count"]),
            }
        )
    total = {
        "method": "all",
        "planned_count": sum(row["planned_count"] for row in rows),
        "recorded_count": sum(row["recorded_count"] for row in rows),
        "successful_count": sum(row["successful_count"] for row in rows),
        "failed_count": sum(row["failed_count"] for row in rows),
        "timeout_count": sum(row["timeout_count"] for row in rows),
        "excluded_count": sum(row["excluded_count"] for row in rows),
        "missing_cpu_usage_count": sum(row["missing_cpu_usage_count"] for row in rows),
        "missing_peak_memory_count": sum(row["missing_peak_memory_count"] for row in rows),
        "missing_pending_time_count": sum(row["missing_pending_time_count"] for row in rows),
        "missing_restart_count": sum(row["missing_restart_count"] for row in rows),
    }
    return rows + [total]


def _rate_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for (method,), items in _group(records, "method").items():
        total = len(items)
        failure_count = sum(1 for record in items if record["success"] is False)
        oom_count = sum(1 for record in items if record["oom_killed"] is True)
        timeout_count = sum(1 for record in items if record["timeout"] is True)
        rows.append(
            {
                "method": method,
                "run_count": total,
                "failure_count": failure_count,
                "failure_rate": _rate(failure_count, total),
                "oom_killed_count": oom_count,
                "oom_killed_rate": _rate(oom_count, total),
                "timeout_count": timeout_count,
                "timeout_rate": _rate(timeout_count, total),
                "missing_oom_status_count": sum(1 for record in items if record["oom_killed"] is None),
            }
        )
    return sorted(rows, key=lambda row: _method_sort_key(row["method"]))


def _metric_summary_rows(records: list[dict[str, Any]], field: str, output_field_prefix: str) -> list[dict[str, Any]]:
    rows = []
    for (method,), items in _group(records, "method").items():
        stats = _summary_stats(record.get(field) for record in items)
        rows.append({"method": method, **{f"{output_field_prefix}_{key}": value for key, value in stats.items()}})
    return sorted(rows, key=lambda row: _method_sort_key(row["method"]))


def _waste_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for (method,), items in _group(records, "method").items():
        memory = _summary_stats(record["memory_request_to_peak_ratio"] for record in items)
        cpu = _summary_stats(record["cpu_request_to_observed_ratio"] for record in items)
        rows.append(
            {
                "method": method,
                **{f"memory_request_to_peak_{key}": value for key, value in memory.items()},
                **{f"cpu_request_to_observed_{key}": value for key, value in cpu.items()},
            }
        )
    return sorted(rows, key=lambda row: _method_sort_key(row["method"]))


def _scatter_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "run_id": record["run_id"],
            "method": record["method"],
            "workload_id": record["workload_id"],
            "repeat_index": record["repeat_index"],
            "cpu_request_m": record["cpu_request_m"],
            "cpu_usage_m": record["cpu_usage_m"],
            "cpu_measurement_statistic": record["cpu_measurement_statistic"],
            "cpu_sampling_interval_seconds": record["cpu_sampling_interval_seconds"],
            "cpu_measurement_window_seconds": record["cpu_measurement_window_seconds"],
            "cpu_measurement_source": record["cpu_measurement_source"],
            "memory_request_mi": record["memory_request_mi"],
            "peak_memory_mi": record["peak_memory_mi"],
            "cpu_request_to_observed_ratio": record["cpu_request_to_observed_ratio"],
            "memory_request_to_peak_ratio": record["memory_request_to_peak_ratio"],
            "resource_measurement_source": record["resource_measurement_source"],
        }
        for record in records
    ]


def _confusion_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for (method, expected_profiles, applied_profile), items in _group(
        records,
        "method",
        "expected_acceptable_profiles",
        "applied_profile",
    ).items():
        outcomes = {outcome: sum(1 for record in items if record["recommendation_outcome"] == outcome) for outcome in ("acceptable", "under", "over", "missing")}
        rows.append(
            {
                "method": method,
                "expected_acceptable_profiles": expected_profiles,
                "applied_profile": applied_profile,
                "count": len(items),
                **{f"{outcome}_count": count for outcome, count in outcomes.items()},
            }
        )
    return sorted(rows, key=lambda row: (_method_sort_key(row["method"]), row["expected_acceptable_profiles"], str(row["applied_profile"])))


def _ablation_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for (method,), items in _group(records, "method").items():
        total = len(items)
        acceptable = sum(1 for record in items if record["recommendation_outcome"] == "acceptable")
        under = sum(1 for record in items if record["recommendation_outcome"] == "under")
        over = sum(1 for record in items if record["recommendation_outcome"] == "over")
        policy_warnings = sum(1 for record in items if record["policy_warnings"])
        success = sum(1 for record in items if record["success"] is True)
        memory_waste = _summary_stats(record["memory_request_to_peak_ratio"] for record in items)
        time_to_success = _summary_stats(record["time_to_success_seconds"] for record in items)
        rows.append(
            {
                "method": method,
                "run_count": total,
                "success_count": success,
                "success_rate": _rate(success, total),
                "acceptable_profile_count": acceptable,
                "acceptable_profile_rate": _rate(acceptable, total),
                "under_profile_count": under,
                "under_profile_rate": _rate(under, total),
                "over_profile_count": over,
                "over_profile_rate": _rate(over, total),
                "policy_warning_count": policy_warnings,
                "policy_warning_rate": _rate(policy_warnings, total),
                "median_time_to_success_seconds": time_to_success["median"],
                "time_to_success_missing": time_to_success["missing"],
                "median_memory_request_to_peak_ratio": memory_waste["median"],
                "memory_waste_missing": memory_waste["missing"],
            }
        )
    return sorted(rows, key=lambda row: _method_sort_key(row["method"]))


def _per_workload_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for (workload_id, method), items in _group(records, "workload_id", "method").items():
        time_stats = _summary_stats(record["time_to_success_seconds"] for record in items)
        waste_stats = _summary_stats(record["memory_request_to_peak_ratio"] for record in items)
        rows.append(
            {
                "workload_id": workload_id,
                "method": method,
                "category": items[0]["category"],
                "run_count": len(items),
                "success_count": sum(1 for record in items if record["success"] is True),
                "failure_count": sum(1 for record in items if record["success"] is False),
                "timeout_count": sum(1 for record in items if record["timeout"] is True),
                "oom_killed_count": sum(1 for record in items if record["oom_killed"] is True),
                "applied_profiles": sorted({record["applied_profile"] for record in items if record["applied_profile"]}),
                "recommendation_outcomes": sorted({record["recommendation_outcome"] for record in items}),
                "median_time_to_success_seconds": time_stats["median"],
                "iqr_time_to_success_seconds": time_stats["iqr"],
                "median_memory_request_to_peak_ratio": waste_stats["median"],
                "memory_waste_missing": waste_stats["missing"],
            }
        )
    return sorted(rows, key=lambda row: (row["workload_id"], _method_sort_key(row["method"])))


def _robustness_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [record for record in records if record["category"] in BOUNDARY_CATEGORIES]
    rows = []
    for (category, method), items in _group(selected, "category", "method").items():
        total = len(items)
        acceptable = sum(1 for record in items if record["recommendation_outcome"] == "acceptable")
        warnings = sum(1 for record in items if record["policy_warnings"])
        rows.append(
            {
                "category": category,
                "method": method,
                "run_count": total,
                "success_count": sum(1 for record in items if record["success"] is True),
                "acceptable_profile_count": acceptable,
                "acceptable_profile_rate": _rate(acceptable, total),
                "under_profile_count": sum(1 for record in items if record["recommendation_outcome"] == "under"),
                "over_profile_count": sum(1 for record in items if record["recommendation_outcome"] == "over"),
                "policy_warning_count": warnings,
                "policy_warning_rate": _rate(warnings, total),
            }
        )
    return sorted(rows, key=lambda row: (row["category"], _method_sort_key(row["method"])))


def _write_svg_bar(path: Path, rows: list[dict[str, Any]], label_field: str, value_field: str, *, title: str, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing figure: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    width = 720
    height = 320
    margin = 54
    values = [float(row[value_field] or 0.0) for row in rows]
    max_value = max(values) if values else 1.0
    max_value = max(max_value, 1.0)
    bar_gap = 18
    bar_width = (width - 2 * margin - bar_gap * max(0, len(rows) - 1)) / max(1, len(rows))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{margin}" y="28" font-family="Arial, sans-serif" font-size="18" fill="#222">{html.escape(title)}</text>',
        f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="#333"/>',
        f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="#333"/>',
    ]
    palette = ["#2f6fbb", "#8a5a44", "#2d875c", "#555555"]
    for index, row in enumerate(rows):
        value = float(row[value_field] or 0.0)
        bar_height = (height - 2 * margin - 24) * value / max_value
        x = margin + index * (bar_width + bar_gap)
        y = height - margin - bar_height
        label = str(row[label_field])
        parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}" fill="{palette[index % len(palette)]}"/>')
        parts.append(f'<text x="{x + bar_width / 2:.2f}" y="{height - margin + 20}" text-anchor="middle" font-family="Arial, sans-serif" font-size="12" fill="#222">{html.escape(label)}</text>')
        parts.append(f'<text x="{x + bar_width / 2:.2f}" y="{max(y - 8, 44):.2f}" text-anchor="middle" font-family="Arial, sans-serif" font-size="12" fill="#222">{value:.3g}</text>')
    parts.append("</svg>\n")
    path.write_text("\n".join(parts), encoding="utf-8")


def _write_svg_scatter(path: Path, rows: list[dict[str, Any]], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing figure: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    points = [row for row in rows if row["memory_request_mi"] is not None and row["peak_memory_mi"] is not None]
    width = 720
    height = 420
    margin = 64
    max_x = max([float(row["memory_request_mi"]) for row in points] + [1.0])
    max_y = max([float(row["peak_memory_mi"]) for row in points] + [1.0])
    max_axis = max(max_x, max_y)
    palette = {"static_manual": "#2f6fbb", "intent_only": "#8a5a44", "context_aware": "#2d875c"}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="64" y="30" font-family="Arial, sans-serif" font-size="18" fill="#222">Requested vs Peak Memory</text>',
        f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="#333"/>',
        f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="#333"/>',
        f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{margin}" stroke="#999" stroke-dasharray="4 4"/>',
        f'<text x="{width/2}" y="{height-18}" text-anchor="middle" font-family="Arial, sans-serif" font-size="13" fill="#222">Memory request (MiB)</text>',
        f'<text x="18" y="{height/2}" text-anchor="middle" transform="rotate(-90 18 {height/2})" font-family="Arial, sans-serif" font-size="13" fill="#222">Observed peak memory (MiB)</text>',
    ]
    for row in points:
        x = margin + (width - 2 * margin) * float(row["memory_request_mi"]) / max_axis
        y = height - margin - (height - 2 * margin) * float(row["peak_memory_mi"]) / max_axis
        color = palette.get(row["method"], "#555555")
        title = html.escape(f"{row['method']} {row['workload_id']} r{row['repeat_index']}")
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4.5" fill="{color}" opacity="0.82"><title>{title}</title></circle>')
    legend_x = width - margin - 160
    for index, method in enumerate(METHOD_ORDER):
        y = margin + index * 20
        parts.append(f'<circle cx="{legend_x}" cy="{y}" r="5" fill="{palette[method]}"/>')
        parts.append(f'<text x="{legend_x + 12}" y="{y + 4}" font-family="Arial, sans-serif" font-size="12" fill="#222">{method}</text>')
    parts.append("</svg>\n")
    path.write_text("\n".join(parts), encoding="utf-8")


def _write_figures(results_dir: Path, tables: dict[str, list[dict[str, Any]]], *, overwrite: bool) -> list[Path]:
    figures_dir = results_dir / "figures"
    outputs = []
    failure_svg = figures_dir / "failure_rate.svg"
    _write_svg_bar(failure_svg, tables["oom_failure"], "method", "failure_rate", title="Failure Rate By Method", overwrite=overwrite)
    outputs.append(failure_svg)
    time_svg = figures_dir / "time_to_success_median.svg"
    _write_svg_bar(time_svg, tables["time_to_success"], "method", "time_to_success_median", title="Median Time To Success", overwrite=overwrite)
    outputs.append(time_svg)
    waste_svg = figures_dir / "memory_waste_ratio_median.svg"
    _write_svg_bar(waste_svg, tables["waste_ratio"], "method", "memory_request_to_peak_median", title="Median Memory Request To Peak Ratio", overwrite=overwrite)
    outputs.append(waste_svg)
    scatter_svg = figures_dir / "requested_vs_peak_memory.svg"
    _write_svg_scatter(scatter_svg, tables["requested_vs_peak_scatter"], overwrite=overwrite)
    outputs.append(scatter_svg)
    return outputs


def _markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(_format_md(row.get(column)) for column in columns) + " |")
    return "\n".join([header, separator, *body])


def _format_md(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (list, dict, tuple)):
        return "`" + html.escape(json.dumps(value, sort_keys=True, separators=(",", ":"))) + "`"
    return html.escape(str(value))


def _write_results_md(
    path: Path,
    *,
    experiment_dir: Path | None,
    raw_jsonl: Path,
    records: list[dict[str, Any]],
    environment: dict[str, Any] | None,
    capability_report: dict[str, Any] | None,
    capability_report_path: Path | None,
    tables: dict[str, list[dict[str, Any]]],
    outputs: list[Path],
    overwrite: bool,
) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing report: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    total = len(records)
    successes = sum(1 for record in records if record["success"] is True)
    failures = sum(1 for record in records if record["success"] is False)
    time_stats = _summary_stats(record["time_to_success_seconds"] for record in records)
    memory_stats = _summary_stats(record["memory_request_to_peak_ratio"] for record in records)
    pending_missing = sum(1 for record in records if record["pod_pending_duration_seconds"] is None)
    cpu_missing = sum(1 for record in records if record["cpu_usage_m"] is None)
    metric_blocker = capability_report and capability_report.get("metrics_api_available") is False
    lines = [
        "# Evaluation Results",
        "",
        "## Scope",
        "",
        "These results are derived from immutable local synthetic benchmark records. They are not live JupyterHub pod experiments, and they do not support cluster-wide efficiency claims.",
        "",
        f"- Raw input: `{raw_jsonl}`",
        f"- Experiment directory: `{experiment_dir}`" if experiment_dir else "- Experiment directory: not provided",
        f"- Records analyzed: {total}",
    ]
    if environment:
        lines.extend(
            [
                f"- Recorded git commit: `{environment.get('git_commit')}`",
                f"- Recorded git branch: `{environment.get('git_branch')}`",
                f"- Environment ID: `{environment.get('environment_id')}`",
                f"- Planned runs: {environment.get('planned_run_count')}",
                f"- Python: `{environment.get('python_version')}`",
                f"- Kubernetes context: `{environment.get('kubectl_context')}`",
                f"- Helm: `{environment.get('helm_version')}`",
            ]
        )
    if capability_report:
        lines.extend(
            [
                "",
                "## Environment Capability",
                "",
                f"- Container runtime: {capability_report.get('container_runtime') or 'not detected'}",
                f"- Kubernetes context: {capability_report.get('kubectl_context') or 'not available'}",
                f"- Helm available: {capability_report.get('helm_available')}",
                f"- CPU count: {capability_report.get('cpu_count')}",
                f"- Memory bytes: {capability_report.get('memory_bytes')}",
                f"- Metrics API available: {capability_report.get('metrics_api_available')}",
            ]
        )
        if metric_blocker:
            lines.extend(
                [
                    "",
                    "**Blocker:** Kubernetes resource metrics are unavailable in this environment. `kubectl top nodes` failed, so Kubernetes CPU samples, memory peaks, Pending-time, OOMKilled, and restart/respawn comparisons cannot be claimed from live cluster evidence.",
                    "",
                    "Exact preflight command that must succeed in a suitable cluster-backed environment:",
                    "",
                    "```bash",
                    "kubectl top nodes && kubectl top pods -A --containers",
                    "```",
                ]
            )
    lines.extend(
        [
            "",
            "## Directly Observed Findings",
            "",
            f"- Local synthetic records completed: {successes}/{total}; failures: {failures}/{total}.",
            f"- Median time to success was {time_stats['median']} seconds with IQR {time_stats['iqr']} across non-missing local timings.",
            f"- Median memory request-to-peak ratio was {memory_stats['median']} with IQR {memory_stats['iqr']} using Python `resource.getrusage` peak RSS.",
            f"- Missing CPU usage measurements: {cpu_missing}/{total}. Missing Kubernetes Pending-time measurements: {pending_missing}/{total}.",
            "",
            "Run counts and exclusions:",
            "",
            _markdown_table(
                tables["run_counts"],
                [
                    "method",
                    "planned_count",
                    "recorded_count",
                    "successful_count",
                    "failed_count",
                    "timeout_count",
                    "excluded_count",
                    "missing_cpu_usage_count",
                    "missing_pending_time_count",
                ],
            ),
            "",
            "Ablation summary:",
            "",
            _markdown_table(
                tables["ablation"],
                [
                    "method",
                    "run_count",
                    "success_rate",
                    "acceptable_profile_rate",
                    "under_profile_rate",
                    "over_profile_rate",
                    "policy_warning_rate",
                    "median_memory_request_to_peak_ratio",
                ],
            ),
            "",
            "## Interpretation",
            "",
            "Within this controlled local benchmark, all methods completed the synthetic workloads. Differences in requested resources are driven by the deterministic profile-selection policies and the fixed manifest signals, not by adaptive tuning after observing results.",
            "",
            "The memory waste-ratio table is useful for comparing profile conservatism in this local process model. It should not be interpreted as Kubernetes pod utilization because the resource source is Python peak RSS, not metrics-server or Prometheus.",
            "",
            "## Failed Or Inconclusive Cases",
            "",
            "- Live cluster resource-metric evidence is inconclusive because the Metrics API is unavailable.",
            "- OOMKilled, restart/respawn, and Pending-time comparisons are reported with missing-data counts rather than inferred values.",
            "- No run was excluded from the local comparative summary; missing cluster-only measurements remain visible in the tables.",
            "",
            "## Unsupported Claims",
            "",
            "- These results do not show that the approach is generally effective for all JupyterHub deployments.",
            "- These results do not show improved real cluster density or scheduler behavior.",
            "- These results do not validate history-aware provisioning or GPU execution.",
            "",
            "## Limitations",
            "",
            "- The benchmark uses generated synthetic data and local Python processes.",
            "- Dataset size values are declared hints, not measured data sizes.",
            "- Peak memory is process-level RSS; CPU usage is unavailable here.",
            "- The full live-cluster experiment remains blocked until a working resource-metric source is present and Kubernetes pod evidence is collected.",
            "",
            "## Generated Outputs",
            "",
        ]
    )
    if capability_report_path:
        lines.append(f"- `{capability_report_path}`")
    for output in outputs:
        lines.append(f"- `{output}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def analyze(args: argparse.Namespace) -> list[Path]:
    experiment_dir = args.experiment_dir
    raw_jsonl = args.raw_jsonl or ((experiment_dir / "results.jsonl") if experiment_dir else None)
    if raw_jsonl is None:
        raise ValueError("provide --experiment-dir or --raw-jsonl")
    manifest = _load_manifest(args.manifest)
    records = _enrich_records(read_jsonl(raw_jsonl), manifest)
    matrix = _read_matrix(experiment_dir / "matrix.jsonl") if experiment_dir else []
    environment = _read_json(experiment_dir / "environment.json") if experiment_dir else None
    capability_report = _read_json(args.environment_report) if args.environment_report else None

    tables = {
        "summary": _summary_rows(records),
        "run_counts": _run_count_rows(records, matrix),
        "oom_failure": _rate_rows(records),
        "restart_respawn": _metric_summary_rows(records, "restart_or_respawn_count", "restart_or_respawn"),
        "time_to_success": _metric_summary_rows(records, "time_to_success_seconds", "time_to_success"),
        "pending_time": _metric_summary_rows(records, "pod_pending_duration_seconds", "pending_time"),
        "requested_vs_peak_scatter": _scatter_rows(records),
        "waste_ratio": _waste_rows(records),
        "recommendation_confusion": _confusion_rows(records),
        "ablation": _ablation_rows(records),
        "per_workload": _per_workload_rows(records),
        "robustness_boundary": _robustness_rows(records),
    }

    outputs: list[Path] = []
    names = {
        "summary": "summary.csv",
        "run_counts": "run_counts_and_exclusions.csv",
        "oom_failure": "oom_failure_rates.csv",
        "restart_respawn": "restart_respawn_comparison.csv",
        "time_to_success": "time_to_success_comparison.csv",
        "pending_time": "pending_time_comparison.csv",
        "requested_vs_peak_scatter": "requested_vs_peak_scatter.csv",
        "waste_ratio": "waste_ratio_comparison.csv",
        "recommendation_confusion": "recommendation_confusion.csv",
        "ablation": "ablation.csv",
        "per_workload": "per_workload_results.csv",
        "robustness_boundary": "robustness_boundary_summary.csv",
    }
    for key, filename in names.items():
        path = args.results_dir / filename
        _write_csv(path, tables[key], overwrite=args.overwrite)
        outputs.append(path)
    outputs.extend(_write_figures(args.results_dir, tables, overwrite=args.overwrite))
    _write_results_md(
        args.results_md,
        experiment_dir=experiment_dir,
        raw_jsonl=raw_jsonl,
        records=records,
        environment=environment,
        capability_report=capability_report,
        capability_report_path=args.environment_report,
        tables=tables,
        outputs=outputs,
        overwrite=args.overwrite,
    )
    outputs.append(args.results_md)
    return outputs


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        outputs = analyze(args)
    except (OSError, ValueError) as exc:
        print(f"analysis failure: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"generated": [str(output) for output in outputs]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
