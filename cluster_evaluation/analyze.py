"""Regenerate traceable Kubernetes evaluation tables, figures, and report."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import statistics
from typing import Any, Iterable

import yaml

from cluster_evaluation.result_compat import (
    CPU_RECONCILIATION_CATEGORIES,
    cpu_reconciliation,
    normalized_cluster_record,
)
from cluster_evaluation.timing import (
    KUBERNETES_TIMESTAMP_RESOLUTION_SECONDS,
    TIMING_ANALYSIS_RULE_VERSION,
    improvement_is_distinguishable,
    median_censored_duration,
)
from cluster_evaluation.validate_artifacts import validate


PROFILES = ("small", "medium", "large")
METHODS = ("static_default", "intent_only", "context_aware")
def read_rows(path: Path) -> list[dict[str, Any]]:
    return [
        normalized_cluster_record(json.loads(line), root=Path(__file__).resolve().parents[1])
        for line in (path / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (path / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def median(values: Iterable[float | int | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return round(statistics.median(present), 6) if present else None


def write_csv(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(data[0]) if data else ["empty"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(data)


def duration_seconds(start: str, end: str) -> float:
    started = datetime.fromisoformat(start.replace("Z", "+00:00"))
    ended = datetime.fromisoformat(end.replace("Z", "+00:00"))
    duration = (ended - started).total_seconds()
    if duration < 0:
        raise ValueError(f"inconsistent timestamps: {end!r} precedes {start!r}")
    return duration


def svg_bars(path: Path, title: str, labels: list[str], values: list[float], ylabel: str) -> None:
    width, height, margin = 760, 420, 70
    maximum = max(values) if values and max(values) > 0 else 1
    bars = []
    for index, (label, value) in enumerate(zip(labels, values)):
        x = margin + index * (width - 2 * margin) / len(values) + 18
        bar_width = (width - 2 * margin) / len(values) - 36
        bar_height = (height - 2 * margin) * value / maximum
        bars.append(
            f'<rect x="{x:.1f}" y="{height-margin-bar_height:.1f}" width="{bar_width:.1f}" '
            f'height="{bar_height:.1f}" fill="#2563eb"/>'
            f'<text x="{x+bar_width/2:.1f}" y="{height-margin+20}" text-anchor="middle" '
            f'font-size="12">{label}</text>'
            f'<text x="{x+bar_width/2:.1f}" y="{height-margin-bar_height-7:.1f}" '
            f'text-anchor="middle" font-size="12">{value:.3f}</text>'
        )
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
        '<rect width="100%" height="100%" fill="white"/>'
        f'<text x="{width/2}" y="28" text-anchor="middle" font-size="18">{title}</text>'
        f'<text transform="translate(18 {height/2}) rotate(-90)" text-anchor="middle" '
        f'font-size="12">{ylabel}</text>'
        f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="black"/>'
        f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="black"/>'
        f'{"".join(bars)}</svg>\n',
        encoding="utf-8",
    )


def svg_scatter(path: Path, data: list[tuple[float, float, str]]) -> None:
    width, height, margin = 760, 500, 70
    maximum = max(max(request, peak) for request, peak, _ in data) * 1.05
    colors = {"small": "#16a34a", "medium": "#2563eb", "large": "#dc2626"}
    points = []
    for request, peak, profile in data:
        x = margin + (width - 2 * margin) * request / maximum
        y = height - margin - (height - 2 * margin) * peak / maximum
        points.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{colors[profile]}" opacity=".6"/>'
        )
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
        '<rect width="100%" height="100%" fill="white"/>'
        f'<text x="{width/2}" y="28" text-anchor="middle" font-size="18">'
        'Requested versus observed peak memory</text>'
        f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{margin}" '
        'stroke="#777" stroke-dasharray="5 5"/>'
        f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="black"/>'
        f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="black"/>'
        f'{"".join(points)}<text x="{width/2}" y="{height-15}" text-anchor="middle">'
        'Memory request (MiB)</text>'
        f'<text transform="translate(18 {height/2}) rotate(-90)" text-anchor="middle">'
        'cgroup peak (MiB)</text></svg>\n',
        encoding="utf-8",
    )


def svg_intervals(
    path: Path,
    title: str,
    rows: list[dict[str, Any]],
    *,
    label_field: str,
    observed_field: str,
    lower_field: str,
    upper_field: str,
) -> None:
    width, height, margin = 760, 420, 90
    maximum = max(float(row[upper_field]) for row in rows) if rows else 1.0
    scale = (width - 2 * margin) / max(maximum, 1.0)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width/2}" y="28" text-anchor="middle" font-size="18">{title}</text>',
    ]
    for index, row in enumerate(rows):
        y = margin + index * 90
        lower = float(row[lower_field])
        upper = float(row[upper_field])
        observed = float(row[observed_field])
        x1, x2, point = margin + lower * scale, margin + upper * scale, margin + observed * scale
        parts.extend(
            [
                f'<text x="{margin-10}" y="{y+4}" text-anchor="end" font-size="12">{row[label_field]}</text>',
                f'<line x1="{x1:.1f}" y1="{y}" x2="{x2:.1f}" y2="{y}" stroke="#2563eb" stroke-width="4"/>',
                f'<circle cx="{point:.1f}" cy="{y}" r="6" fill="#111827"/>',
                f'<text x="{x2+8:.1f}" y="{y+4}" font-size="12">{observed:g}s [{lower:g},{upper:g})</text>',
            ]
        )
    parts.extend(
        [
            f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="black"/>',
            f'<text x="{width/2}" y="{height-25}" text-anchor="middle" font-size="12">Seconds (1 s timestamp resolution)</text>',
            '</svg>\n',
        ]
    )
    path.write_text("".join(parts), encoding="utf-8")


def has_failed_scheduling(pod: dict[str, Any]) -> bool:
    return any("FailedScheduling" in reason for reason in pod.get("pending_reasons", []))


def derive_envelopes(ground: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    envelopes: list[dict[str, Any]] = []
    table: list[dict[str, Any]] = []
    for workload_id in sorted({record["workload_id"] for record in ground}):
        workload_records = [record for record in ground if record["workload_id"] == workload_id]
        stats: dict[str, dict[str, Any]] = {}
        for profile in PROFILES:
            profile_records = [
                record for record in workload_records if record["applied_profile"] == profile
            ]
            time_interval = median_censored_duration(
                record["time_to_success_seconds"] for record in profile_records
            )
            stats[profile] = {
                "reliable": len(profile_records) == 3
                and all(
                    record["success"]
                    and not record["timeout"]
                    and not record["oom_killed"]
                    and record["cleanup_status"] == "completed"
                    for record in profile_records
                ),
                "tts": median(record["time_to_success_seconds"] for record in profile_records),
                "tts_lower": None if time_interval is None else time_interval.lower_seconds,
                "tts_upper": None if time_interval is None else time_interval.upper_seconds,
                "benchmark_runtime": median(
                    record["benchmark_runtime_seconds"] for record in profile_records
                ),
                "waste": median(
                    record["memory_reservation_waste_ratio"] for record in profile_records
                ),
                "run_ids": [record["run_id"] for record in profile_records],
            }

        smallest = next(profile for profile in PROFILES if stats[profile]["reliable"])
        acceptable = [smallest]
        base_tts = stats[smallest]["tts"]
        base_interval = median_censored_duration(
            record["time_to_success_seconds"]
            for record in workload_records
            if record["applied_profile"] == smallest
        )
        stats[smallest]["observed_time_improvement_fraction"] = 0.0
        stats[smallest]["time_improvement_distinguishable"] = False
        stats[smallest]["acceptance_basis"] = "smallest_reliable_profile"
        for profile in PROFILES[PROFILES.index(smallest) + 1 :]:
            candidate_tts = stats[profile]["tts"]
            improvement = (
                (base_tts - candidate_tts) / base_tts
                if base_tts not in (None, 0) and candidate_tts is not None
                else 0.0
            )
            candidate_interval = median_censored_duration(
                record["time_to_success_seconds"]
                for record in workload_records
                if record["applied_profile"] == profile
            )
            distinguishable = improvement_is_distinguishable(base_interval, candidate_interval)
            time_accepted = improvement >= 0.2 and distinguishable
            waste_accepted = stats[profile]["waste"] is not None and stats[profile]["waste"] < 0.5
            accepted = stats[profile]["reliable"] and (time_accepted or waste_accepted)
            if accepted:
                acceptable.append(profile)
            stats[profile]["observed_time_improvement_fraction"] = round(improvement, 2)
            stats[profile]["time_improvement_distinguishable"] = distinguishable
            stats[profile]["acceptance_basis"] = (
                "time_and_waste"
                if time_accepted and waste_accepted
                else "time"
                if time_accepted
                else "waste"
                if waste_accepted
                else "unreliable"
                if not stats[profile]["reliable"]
                else "over_reserved"
            )

        envelope = {
            "workload_id": workload_id,
            "smallest_reliable_profile": smallest,
            "acceptable_profiles": acceptable,
            "manifest_expectation_status": "not_operationally_grounded; excluded from derivation",
            "time_measurement_status": (
                f"timing rule {TIMING_ANALYSIS_RULE_VERSION}: Kubernetes creation and "
                "termination timestamps have one-second resolution; durations are "
                "interval-censored without offsets and the 20% branch is used only when "
                "the candidate upper bound clears the baseline lower bound"
            ),
            "profiles": stats,
        }
        envelopes.append(envelope)
        for profile in PROFILES:
            table.append(
                {
                    "workload_id": workload_id,
                    "profile": profile,
                    "reliable": stats[profile]["reliable"],
                    "median_time_to_success_seconds": stats[profile]["tts"],
                    "time_to_success_lower_seconds": stats[profile]["tts_lower"],
                    "time_to_success_upper_seconds_exclusive": stats[profile]["tts_upper"],
                    "timestamp_resolution_seconds": KUBERNETES_TIMESTAMP_RESOLUTION_SECONDS,
                    "timing_analysis_rule_version": TIMING_ANALYSIS_RULE_VERSION,
                    "median_benchmark_runtime_seconds": stats[profile]["benchmark_runtime"],
                    "median_memory_waste_ratio": stats[profile]["waste"],
                    "observed_time_improvement_fraction": stats[profile]["observed_time_improvement_fraction"],
                    "time_improvement_distinguishable": stats[profile][
                        "time_improvement_distinguishable"
                    ],
                    "outcome": "acceptable" if profile in acceptable else "over_reserved",
                    "acceptance_basis": stats[profile]["acceptance_basis"],
                    "run_ids": ";".join(stats[profile]["run_ids"]),
                }
            )
    return envelopes, table


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ground", type=Path, required=True)
    parser.add_argument("--comparative", type=Path, required=True)
    parser.add_argument("--capacity", type=Path, required=True)
    parser.add_argument("--historical-capacity", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--envelopes", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    integrity = validate(args.ground.resolve(), args.comparative.resolve(), args.capacity.resolve())
    if args.historical_capacity is not None:
        validate(
            args.ground.resolve(),
            args.comparative.resolve(),
            args.historical_capacity.resolve(),
        )
    args.out.mkdir(parents=True, exist_ok=True)
    ground = read_rows(args.ground)
    comparative = read_rows(args.comparative)
    capacity = read_jsonl(args.capacity)
    historical_capacity = (
        read_jsonl(args.historical_capacity) if args.historical_capacity is not None else []
    )
    capacity_is_principal = integrity["capacity_provenance"] == "reproducible_v2"
    obsolete = (
        (
            "historical_capacity_supplementary.csv",
            "historical_capacity_concurrency_supplementary.svg",
            "historical_pending_time_supplementary.svg",
        )
        if capacity_is_principal and not historical_capacity
        else (
            ("capacity_density.csv", "capacity_concurrency.svg", "pending_time.svg")
            if not capacity_is_principal
            else ()
        )
    )
    for filename in obsolete:
        (args.out / filename).unlink(missing_ok=True)
    commit = integrity["evaluated_git_commit"]

    envelopes, ground_table = derive_envelopes(ground)
    args.envelopes.parent.mkdir(parents=True, exist_ok=True)
    args.envelopes.write_text(
        yaml.safe_dump(
            {
                "schema_version": "2.0.0",
                "evaluated_git_commit": commit,
                "derivation": (
                    f"CLUSTER_EXPERIMENT_PROTOCOL.md reliability/time/waste rule with "
                    f"predeclared interval-censored timing rule {TIMING_ANALYSIS_RULE_VERSION}"
                ),
                "workloads": envelopes,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    write_csv(args.out / "ground_truth_profile_outcomes.csv", ground_table)

    acceptable = {
        envelope["workload_id"]: set(envelope["acceptable_profiles"])
        for envelope in envelopes
    }
    summaries: list[dict[str, Any]] = []
    for method in METHODS:
        method_records = [record for record in comparative if record["method"] == method]
        time_interval = median_censored_duration(
            record["time_to_success_seconds"] for record in method_records
        )
        summaries.append(
            {
                "method": method,
                "planned": 60,
                "completed": len(method_records),
                "failed": sum(not record["success"] for record in method_records),
                "timed_out": sum(record["timeout"] for record in method_records),
                "oom_killed": sum(bool(record["oom_killed"]) for record in method_records),
                "excluded": 0,
                "acceptable_profile_runs": sum(
                    record["applied_profile"] in acceptable[record["workload_id"]]
                    for record in method_records
                ),
                "median_time_to_success_seconds": median(
                    record["time_to_success_seconds"] for record in method_records
                ),
                "time_to_success_lower_seconds": (
                    None if time_interval is None else time_interval.lower_seconds
                ),
                "time_to_success_upper_seconds_exclusive": (
                    None if time_interval is None else time_interval.upper_seconds
                ),
                "timestamp_resolution_seconds": KUBERNETES_TIMESTAMP_RESOLUTION_SECONDS,
                "timing_analysis_rule_version": TIMING_ANALYSIS_RULE_VERSION,
                "method_timing_distinguishable": False,
                "median_pending_seconds": median(
                    record["pod_pending_duration_seconds"] for record in method_records
                ),
                "median_memory_waste_ratio": median(
                    record["memory_reservation_waste_ratio"] for record in method_records
                ),
                "run_ids": ";".join(record["run_id"] for record in method_records),
            }
        )
    write_csv(args.out / "method_summary.csv", summaries)

    cpu_rows = []
    for record in ground + comparative:
        cpu_rows.append(
            {
                "run_id": record["run_id"],
                "experiment_kind": record["experiment_kind"],
                "workload_id": record["workload_id"],
                "method": record["method"],
                "cpu_usage_m": record["cpu_usage_m"],
                "measurement_statistic": record["cpu_measurement_statistic"],
                "sampling_interval_seconds": record["cpu_sampling_interval_seconds"],
                "measurement_window_seconds": record["cpu_measurement_window_seconds"],
                "source": record["cpu_measurement_source"],
                "reconciliation_category": record["cpu_reconciliation_category"],
                "legacy_source_field": record["legacy_source_field"],
                "raw_schema_version": record["cluster_schema_version"],
            }
        )
    write_csv(args.out / "cpu_measurements.csv", cpu_rows)
    reconciliation = cpu_reconciliation(ground + comparative)
    reconciliation_rows = [
        {
            "category": category,
            "records": reconciliation[category],
            "total_records": reconciliation["total_records"],
        }
        for category in CPU_RECONCILIATION_CATEGORIES
    ]
    write_csv(args.out / "cpu_metric_reconciliation.csv", reconciliation_rows)

    per_workload: list[dict[str, Any]] = []
    for workload_id in sorted(acceptable):
        for method in METHODS:
            records = [
                record
                for record in comparative
                if record["workload_id"] == workload_id and record["method"] == method
            ]
            time_interval = median_censored_duration(
                record["time_to_success_seconds"] for record in records
            )
            per_workload.append(
                {
                    "workload_id": workload_id,
                    "method": method,
                    "applied_profile": records[0]["applied_profile"],
                    "successes": sum(record["success"] for record in records),
                    "median_time_to_success_seconds": median(
                        record["time_to_success_seconds"] for record in records
                    ),
                    "time_to_success_lower_seconds": (
                        None if time_interval is None else time_interval.lower_seconds
                    ),
                    "time_to_success_upper_seconds_exclusive": (
                        None if time_interval is None else time_interval.upper_seconds
                    ),
                    "median_peak_memory_mi": median(record["peak_memory_mi"] for record in records),
                    "median_memory_waste_ratio": median(
                        record["memory_reservation_waste_ratio"] for record in records
                    ),
                    "acceptable": records[0]["applied_profile"] in acceptable[workload_id],
                    "run_ids": ";".join(record["run_id"] for record in records),
                }
            )
    write_csv(args.out / "per_workload_method.csv", per_workload)
    write_csv(
        args.out / "method_ablation.csv",
        [row for row in per_workload if row["method"] in {"intent_only", "context_aware"}],
    )
    write_csv(
        args.out / "boundary_robustness.csv",
        [
            row
            for row in per_workload
            if row["workload_id"].startswith("boundary_")
            or row["workload_id"] == "policy_gpu_disallowed"
        ],
    )

    scatter = [
        {
            "run_id": record["run_id"],
            "method": record["method"],
            "workload_id": record["workload_id"],
            "profile": record["applied_profile"],
            "memory_request_mi": record["memory_request_mi"],
            "peak_memory_mi": record["peak_memory_mi"],
        }
        for record in comparative
    ]
    write_csv(args.out / "requested_vs_peak.csv", scatter)

    capacity_table = []
    for batch in capacity:
        capacity_table.append(
            {
                "batch_id": batch["batch_id"],
                "method": batch["method"],
                "repeat_index": batch["repeat_index"],
                "population": batch["population_size"],
                "completed": batch["completed"],
                "failed": batch["failed"],
                "max_concurrent_running": batch["max_concurrent_running"],
                "makespan_seconds": round(
                    duration_seconds(batch["started_at"], batch["recorded_at"]), 3
                ),
                "pods_with_failed_scheduling": sum(
                    has_failed_scheduling(pod) for pod in batch["pods"]
                ),
                "run_ids": ";".join(pod["run_id"] for pod in batch["pods"]),
            }
        )
    capacity_output = (
        "capacity_density.csv"
        if capacity_is_principal
        else "historical_capacity_supplementary.csv"
    )
    write_csv(args.out / capacity_output, capacity_table)
    if historical_capacity:
        historical_table = []
        for batch in historical_capacity:
            historical_table.append(
                {
                    "batch_id": batch["batch_id"],
                    "method": batch["method"],
                    "repeat_index": batch["repeat_index"],
                    "population": batch["population_size"],
                    "completed": batch["completed"],
                    "failed": batch["failed"],
                    "max_concurrent_running": batch["max_concurrent_running"],
                    "makespan_seconds": round(
                        duration_seconds(batch["started_at"], batch["recorded_at"]), 3
                    ),
                    "pods_with_failed_scheduling": sum(
                        has_failed_scheduling(pod) for pod in batch["pods"]
                    ),
                    "evidence_status": "supplementary_historical_runner_unavailable",
                    "run_ids": ";".join(pod["run_id"] for pod in batch["pods"]),
                }
            )
        write_csv(args.out / "historical_capacity_supplementary.csv", historical_table)

    svg_bars(
        args.out / "waste_comparison.svg",
        "Median memory reservation waste",
        list(METHODS),
        [
            next(row["median_memory_waste_ratio"] for row in summaries if row["method"] == method)
            for method in METHODS
        ],
        "Waste ratio",
    )
    svg_intervals(
        args.out / "time_to_success_intervals.svg",
        "Median time to success with quantization intervals",
        summaries,
        label_field="method",
        observed_field="median_time_to_success_seconds",
        lower_field="time_to_success_lower_seconds",
        upper_field="time_to_success_upper_seconds_exclusive",
    )
    svg_bars(
        args.out / "cpu_metric_reconciliation.svg",
        "CPU measurement reconciliation",
        [row["category"] for row in reconciliation_rows],
        [float(row["records"]) for row in reconciliation_rows],
        "Records",
    )
    svg_bars(
        args.out
        / (
            "capacity_concurrency.svg"
            if capacity_is_principal
            else "historical_capacity_concurrency_supplementary.svg"
        ),
        (
            "Median maximum concurrent Running pods"
            if capacity_is_principal
            else "Supplementary historical concurrency"
        ),
        list(METHODS),
        [
            median(
                batch["max_concurrent_running"]
                for batch in capacity
                if batch["method"] == method
            )
            or 0
            for method in METHODS
        ],
        "Pods",
    )
    svg_bars(
        args.out
        / (
            "pending_time.svg"
            if capacity_is_principal
            else "historical_pending_time_supplementary.svg"
        ),
        (
            "Median Pending time among FailedScheduling pods"
            if capacity_is_principal
            else "Supplementary historical Pending time"
        ),
        list(METHODS),
        [
            median(
                pod["pending_seconds"]
                for batch in capacity
                if batch["method"] == method
                for pod in batch["pods"]
                if has_failed_scheduling(pod)
            )
            or 0
            for method in METHODS
        ],
        "Seconds",
    )
    if historical_capacity:
        svg_bars(
            args.out / "historical_capacity_concurrency_supplementary.svg",
            "Supplementary historical concurrency",
            list(METHODS),
            [
                median(
                    batch["max_concurrent_running"]
                    for batch in historical_capacity
                    if batch["method"] == method
                )
                or 0
                for method in METHODS
            ],
            "Pods",
        )
        svg_bars(
            args.out / "historical_pending_time_supplementary.svg",
            "Supplementary historical Pending time",
            list(METHODS),
            [
                median(
                    pod["pending_seconds"]
                    for batch in historical_capacity
                    if batch["method"] == method
                    for pod in batch["pods"]
                    if has_failed_scheduling(pod)
                )
                or 0
                for method in METHODS
            ],
            "Seconds",
        )
    svg_scatter(
        args.out / "requested_vs_peak.svg",
        [
            (record["memory_request_mi"], record["peak_memory_mi"], record["applied_profile"])
            for record in comparative
        ],
    )

    capacity_concurrency_medians = {
        method: median(
            batch["max_concurrent_running"]
            for batch in capacity
            if batch["method"] == method
        )
        or 0
        for method in METHODS
    }
    capacity_failed_scheduling_counts = {
        method: sum(
            has_failed_scheduling(pod)
            for batch in capacity
            if batch["method"] == method
            for pod in batch["pods"]
        )
        for method in METHODS
    }
    capacity_pending_medians = {
        method: median(
            pod["pending_seconds"]
            for batch in capacity
            if batch["method"] == method
            for pod in batch["pods"]
            if has_failed_scheduling(pod) and pod["pending_seconds"] is not None
        )
        for method in METHODS
    }
    capacity_summary = "; ".join(
        f"{method.replace('_', '-')} median maximum Running "
        f"{capacity_concurrency_medians[method]:g}, "
        f"FailedScheduling pods {capacity_failed_scheduling_counts[method]}, "
        f"median affected Pending "
        f"{capacity_pending_medians[method]:g}s"
        if capacity_pending_medians[method] is not None
        else f"{method.replace('_', '-')} median maximum Running "
        f"{capacity_concurrency_medians[method]:g}, "
        f"FailedScheduling pods {capacity_failed_scheduling_counts[method]}, "
        "median affected Pending unavailable"
        for method in METHODS
    )
    reconciliation_counts = {
        row["category"]: int(row["records"]) for row in reconciliation_rows
    }

    lines = [
        "# Kubernetes Cluster Results",
        "",
        f"Evaluated workload commit: `{commit}`. Evidence scope: one disposable ARM64 Minikube v1.33.1 node with 6 CPUs and 6088560Ki allocatable memory.",
        "",
        "## Run accounting",
        "",
        "| Stage | Planned | Completed | Failed | Timed out | Excluded |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| Ground truth | 108 | {len(ground)} | {sum(not row['success'] for row in ground)} | {sum(row['timeout'] for row in ground)} | 0 |",
        f"| Comparative | 180 | {len(comparative)} | {sum(not row['success'] for row in comparative)} | {sum(row['timeout'] for row in comparative)} | 0 |",
        f"| {'Capacity v2' if capacity_is_principal else 'Historical capacity (supplementary)'} | 108 pods / 9 batches | {sum(row['completed'] for row in capacity)} pods / {len(capacity)} batches | {sum(row['failed'] for row in capacity)} | 0 | 0 |",
        "",
        "## Ground truth",
        "",
        f"All 12 workloads completed reliably under Small. Manifest expectations were excluded from derivation. Timing rule {TIMING_ANALYSIS_RULE_VERSION} treats one-second Kubernetes durations as interval-censored, accepts zero as valid, and adds no offset, smoothing, or continuity correction. The 20% threshold is unchanged; a larger profile clears the timing branch only when its upper bound is at least 20% below the baseline lower bound.",
        "",
        "## Comparative outcome",
        "",
        "| Method | Acceptable / 60 | Median waste | Median time-to-success interval (s) | OOM |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for summary in summaries:
        lines.append(
            f"| {summary['method']} | {summary['acceptable_profile_runs']} | "
            f"{summary['median_memory_waste_ratio']:.3f} | "
            f"{summary['median_time_to_success_seconds']:g} "
            f"[{summary['time_to_success_lower_seconds']:g},"
            f"{summary['time_to_success_upper_seconds_exclusive']:g}) | "
            f"{summary['oom_killed']} |"
        )
    lines.extend(
        [
            "",
            "An earlier 108-run ground-truth pilot is excluded from every table and figure because its environment file retained unnecessary machine identifiers. Its ignored raw directory remains local and no pilot value was copied into the sanitized matrix.",
            "",
            "All methods completed every run without OOM. Success alone therefore does not establish recommendation quality. The workload implementations are much smaller than their declared dataset-size hints, so these acceptable-profile rates diagnose behavior on this synthetic suite rather than predictive accuracy for real notebooks.",
            "",
            "The method medians are all 1 second with the same [0, 2) second interval. The available timestamps therefore cannot distinguish method-level time to success, and no timing advantage is claimed.",
            "",
            "## Capacity pressure" if capacity_is_principal else "## Supplementary historical capacity",
            "",
            f"Across the three counterbalanced repeats: {capacity_summary}. These values are computed directly from the selected capacity corpus and describe request-reservation pressure under the fixed 20-second hold.",
            "",
            (
                f"Capacity v2 was generated by committed protocol 2.0.0 at `{integrity['capacity_git_commit']}`. It is principal evidence only for this controlled disposable environment and does not demonstrate production density."
                if capacity_is_principal
                else "The exact capacity batch generator is not present in evaluated commit `39b6973`. The immutable plan and outcomes remain transparent supplementary evidence, but are excluded from principal claim support and cannot establish a density result."
            ),
            *(
                [
                    "",
                    "The earlier capacity corpus is retained in `historical_capacity_supplementary.csv` and supplementary figures. Its missing runner provenance is not backfilled, and it is excluded from the capacity-v2 claim above."
                ]
                if historical_capacity
                else []
            ),
            "",
            "## Measurement limits",
            "",
            "The standard-library workloads are short and small relative to their declared dataset hints. Results apply only to this benchmark, image, profile table, and local single-node cluster. No history-aware or GPU evaluation was performed. Metrics Server availability was verified by a documented probe, but it captured zero per-job snapshots for the 288 short ground-truth/comparative pods. Memory peaks come from cgroup-v2 `memory.peak`.",
            "",
            f"CPU reconciliation: {reconciliation_counts['genuine_cgroup_peak']} genuine cgroup CPU peaks, {reconciliation_counts['average']} full-window averages, {reconciliation_counts['sampled_instantaneous']} unambiguous interval sample maxima, {reconciliation_counts['legacy_hybrid_maximum']} legacy maxima combining the interval-sample maximum with the full-window average, and {reconciliation_counts['unavailable']} unavailable values. The immutable schema-1 implementation took the maximum of those two CPU statistics whenever periodic samples existed, so the 86 hybrid values cannot be narrowed to either statistic after the fact. No CPU-peak or CPU-waste claim is made from any historical CPU class.",
            "",
            "Raw input sets: `results/cluster/raw/ground-truth-39b6973-seed20260720`, `results/cluster/raw/comparative-39b6973-seed20260720`, `results/cluster/raw/capacity-v2-ca2e74b-seed20260721`, and the supplementary `results/cluster/raw/capacity-39b6973-seed20260721`. `python -m cluster_evaluation.validate_artifacts` reconciles every retained plan, record, sidecar, resource mapping, and supporting path.",
            "",
            "Derived-input map: `ground_truth_profile_outcomes.csv` and `benchmarks/observed_resource_envelopes.yaml` use ground truth; `method_summary.csv`, `per_workload_method.csv`, `method_ablation.csv`, `boundary_robustness.csv`, `requested_vs_peak.csv`, `time_to_success_intervals.svg`, `waste_comparison.svg`, and `requested_vs_peak.svg` use comparative records plus the ground-truth envelopes; `cpu_measurements.csv`, `cpu_metric_reconciliation.csv`, and `cpu_metric_reconciliation.svg` use ground-truth plus comparative records; `capacity_density.csv`, `capacity_concurrency.svg`, and `pending_time.svg` use capacity-v2; the three filenames containing `historical` use only the historical supplementary corpus. Row-level tables retain run IDs; the aggregate CPU reconciliation is traceable through `cpu_measurements.csv`.",
        ]
    )
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
