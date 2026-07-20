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

from cluster_evaluation.validate_artifacts import validate


PROFILES = ("small", "medium", "large")
METHODS = ("static_default", "intent_only", "context_aware")
KUBERNETES_TIMESTAMP_RESOLUTION_SECONDS = 1.0
# Creation and termination timestamps are independently quantized. A difference
# must exceed two one-second bins before it can support the preregistered speed
# branch of the acceptable-envelope rule.
MIN_RESOLVABLE_TIME_DELTA_SECONDS = 2.0


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (path / "results.jsonl").read_text(encoding="utf-8").splitlines()]


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
    return (ended - started).total_seconds()


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
        stats[smallest]["time_improvement_fraction"] = 0.0
        stats[smallest]["time_acceptance_measurement_adequate"] = False
        stats[smallest]["acceptance_basis"] = "smallest_reliable_profile"
        for profile in PROFILES[PROFILES.index(smallest) + 1 :]:
            candidate_tts = stats[profile]["tts"]
            improvement = (
                (base_tts - candidate_tts) / base_tts
                if base_tts not in (None, 0) and candidate_tts is not None
                else 0.0
            )
            time_delta = (
                base_tts - candidate_tts
                if base_tts is not None and candidate_tts is not None
                else None
            )
            measurement_adequate = (
                time_delta is not None and time_delta > MIN_RESOLVABLE_TIME_DELTA_SECONDS
            )
            time_accepted = improvement >= 0.2 and measurement_adequate
            waste_accepted = stats[profile]["waste"] is not None and stats[profile]["waste"] < 0.5
            accepted = stats[profile]["reliable"] and (time_accepted or waste_accepted)
            if accepted:
                acceptable.append(profile)
            stats[profile]["time_improvement_fraction"] = round(improvement, 6)
            stats[profile]["time_acceptance_measurement_adequate"] = measurement_adequate
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
                "Kubernetes creation/termination timestamps have one-second resolution; "
                "the audit requires an observed delta greater than two seconds before the "
                "preregistered 20% speed branch can accept a larger profile"
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
                    "median_benchmark_runtime_seconds": stats[profile]["benchmark_runtime"],
                    "median_memory_waste_ratio": stats[profile]["waste"],
                    "time_improvement_fraction": stats[profile]["time_improvement_fraction"],
                    "time_acceptance_measurement_adequate": stats[profile][
                        "time_acceptance_measurement_adequate"
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
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--envelopes", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    integrity = validate(args.ground.resolve(), args.comparative.resolve(), args.capacity.resolve())
    args.out.mkdir(parents=True, exist_ok=True)
    ground = read_rows(args.ground)
    comparative = read_rows(args.comparative)
    capacity = read_rows(args.capacity)
    commit = integrity["evaluated_git_commit"]

    envelopes, ground_table = derive_envelopes(ground)
    args.envelopes.parent.mkdir(parents=True, exist_ok=True)
    args.envelopes.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.1.0",
                "evaluated_git_commit": commit,
                "derivation": (
                    "CLUSTER_EXPERIMENT_PROTOCOL.md preregistered reliability/time/waste rule "
                    "with the final-audit timestamp-resolution guard"
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

    per_workload: list[dict[str, Any]] = []
    for workload_id in sorted(acceptable):
        for method in METHODS:
            records = [
                record
                for record in comparative
                if record["workload_id"] == workload_id and record["method"] == method
            ]
            per_workload.append(
                {
                    "workload_id": workload_id,
                    "method": method,
                    "applied_profile": records[0]["applied_profile"],
                    "successes": sum(record["success"] for record in records),
                    "median_time_to_success_seconds": median(
                        record["time_to_success_seconds"] for record in records
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
    write_csv(args.out / "capacity_density.csv", capacity_table)

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
    svg_bars(
        args.out / "time_to_success.svg",
        "Median time to success",
        list(METHODS),
        [
            next(
                row["median_time_to_success_seconds"]
                for row in summaries
                if row["method"] == method
            )
            for method in METHODS
        ],
        "Seconds",
    )
    svg_bars(
        args.out / "capacity_concurrency.svg",
        "Median maximum concurrent Running pods",
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
        args.out / "pending_time.svg",
        "Median Pending time among FailedScheduling pods",
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
    svg_scatter(
        args.out / "requested_vs_peak.svg",
        [
            (record["memory_request_mi"], record["peak_memory_mi"], record["applied_profile"])
            for record in comparative
        ],
    )

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
        f"| Capacity | 108 pods / 9 batches | {sum(row['completed'] for row in capacity)} pods / {len(capacity)} batches | {sum(row['failed'] for row in capacity)} | 0 | 0 |",
        "",
        "## Ground truth",
        "",
        "All 12 workloads completed reliably under Small. The manifest expectations were excluded from derivation. The preregistered 20% time-improvement branch was not measurement-valid for differences of two seconds or less because Kubernetes creation and termination timestamps have one-second resolution. The final audit therefore added a disclosed measurement-adequacy guard; this is a correction to analysis validity, not a newly optimized effect threshold.",
        "",
        "## Comparative outcome",
        "",
        "| Method | Acceptable / 60 | Median waste | Median time-to-success (s) | OOM |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for summary in summaries:
        lines.append(
            f"| {summary['method']} | {summary['acceptable_profile_runs']} | "
            f"{summary['median_memory_waste_ratio']:.3f} | "
            f"{summary['median_time_to_success_seconds']:.3f} | {summary['oom_killed']} |"
        )
    lines.extend(
        [
            "",
            "An earlier 108-run ground-truth pilot is excluded from every table and figure because its environment file retained unnecessary machine identifiers. Its ignored raw directory remains local and no pilot value was copied into the sanitized matrix.",
            "",
            "All methods completed every run without OOM. Success alone therefore does not establish recommendation quality. The workload implementations are much smaller than their declared dataset-size hints, so these acceptable-profile rates diagnose behavior on this synthetic suite rather than predictive accuracy for real notebooks.",
            "",
            "## Capacity pressure",
            "",
            "The retained records show median maximum concurrency of 9 pods for intent-only and 7 for static-default and context-aware across three counterbalanced repeats. Fifteen static-default pods, nine intent-only pods, and fifteen context-aware pods retained FailedScheduling evidence, with median queued Pending time of 22 seconds for each method. These are request-reservation observations under the fixed 20-second hold.",
            "",
            "The exact capacity batch generator is not present in evaluated commit `39b6973`; only its plan, nine immutable batch records, per-pod outcomes, and environment record are retained. The observation is therefore descriptive operational evidence, not a fully reproducible density result, and must not be generalized to production cluster density.",
            "",
            "## Measurement limits",
            "",
            "The standard-library workloads are short and small relative to their declared dataset hints. Results apply only to this benchmark, image, profile table, and local single-node cluster. No history-aware or GPU evaluation was performed. Metrics Server availability was verified by a documented probe, but it captured zero per-job snapshots for the 288 short ground-truth/comparative pods. Memory peaks come from cgroup-v2 `memory.peak`. Only 86 jobs had at least one 10ms CPU sample. For the other 202, evaluated code stored the full-job CPU average in the historical `peak_cpu_m` field; those values must not be cited as CPU peaks. Current code preserves the average separately and leaves an unsampled peak missing.",
            "",
            "Raw inputs: `results/cluster/raw/`. `python -m cluster_evaluation.validate_artifacts` reconciles every retained plan, record, sidecar, resource mapping, and supporting path. Every derived CSV row contains supporting run IDs.",
        ]
    )
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
