"""Preregistered analysis for protocol-v3 calibration and hold-out records."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import median
from typing import Any, Callable, Iterable

from benchmarks.resource_envelope_runner import load_manifest
from cluster_evaluation.policies import PROFILE_RESOURCES
from cluster_evaluation.result_schema_v3 import validate_record


METHODS = ("static_default", "intent_only", "context_aware")
PROFILES = ("small", "medium", "large")
BOOTSTRAP_SEED = 20260723
BOOTSTRAP_SAMPLES = 10_000
ROOT = Path(__file__).resolve().parents[1]


def _load_records(directory: Path) -> list[dict[str, Any]]:
    path = directory / "results.jsonl"
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for record in records:
        validate_record(record)
    return records


def _valid(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if not record["infrastructure_invalid"]]


def _median(values: Iterable[float | int | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return None if not present else median(present)


def _iqr(values: Iterable[float | int | None]) -> list[float | None]:
    present = sorted(float(value) for value in values if value is not None)
    if not present:
        return [None, None]

    def percentile(probability: float) -> float:
        position = (len(present) - 1) * probability
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return present[lower]
        return present[lower] + (present[upper] - present[lower]) * (position - lower)

    return [percentile(0.25), percentile(0.75)]


def _waste(record: dict[str, Any]) -> float | None:
    peak = record["actual_cgroup_peak_mib"]
    if peak in (None, 0):
        return None
    request = float(record["memory_request_mi"])
    return max(0.0, (request - float(peak)) / request)


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float | None]:
    if total <= 0:
        return [None, None]
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def validate_calibration(records: list[dict[str, Any]]) -> dict[str, Any]:
    valid = _valid(records)
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in valid:
        by_key.setdefault((record["workload_id"], record["applied_profile"]), []).append(record)

    checks: list[dict[str, Any]] = []

    def add(
        workload_id: str,
        profile: str,
        expected: str,
        predicate: Callable[[list[dict[str, Any]]], bool],
    ) -> None:
        selected = by_key.get((workload_id, profile), [])
        checks.append(
            {
                "workload_id": workload_id,
                "profile": profile,
                "expected": expected,
                "records": len(selected),
                "passed": len(selected) == 3 and predicate(selected),
                "run_ids": [record["run_id"] for record in selected],
            }
        )

    def success_band(lower: float, upper: float) -> Callable[[list[dict[str, Any]]], bool]:
        return lambda rows: all(
            row["success"]
            and row["actual_cgroup_peak_mib"] is not None
            and lower <= row["actual_cgroup_peak_mib"] <= upper
            for row in rows
        )

    add("cal_small_envelope", "small", "success_3_of_3_peak_315_335", success_band(315, 335))
    add(
        "cal_small_medium_boundary",
        "small",
        "oom_3_of_3",
        lambda rows: all(row["oom_killed"] for row in rows),
    )
    add(
        "cal_small_medium_boundary",
        "medium",
        "success_3_of_3_peak_820_880",
        success_band(820, 880),
    )
    add(
        "cal_medium_large_boundary",
        "medium",
        "oom_3_of_3",
        lambda rows: all(row["oom_killed"] for row in rows),
    )
    add(
        "cal_medium_large_boundary",
        "large",
        "success_3_of_3_peak_1600_1700",
        success_band(1600, 1700),
    )
    for profile in PROFILES:
        add(
            "cal_cpu_units",
            profile,
            "success_3_of_3_and_at_least_200_samples",
            lambda rows: all(
                row["success"] and (row["cgroup_sample_count"] or 0) >= 200 for row in rows
            ),
        )
    medium_cpu = by_key.get(("cal_cpu_units", "medium"), [])
    medium_runtime = _median(row["benchmark_runtime_seconds"] for row in medium_cpu)
    checks.append(
        {
            "workload_id": "cal_cpu_units",
            "profile": "medium",
            "expected": "median_runtime_30_45_seconds",
            "records": len(medium_cpu),
            "passed": medium_runtime is not None and 30 <= medium_runtime <= 45,
            "observed_median": medium_runtime,
            "run_ids": [row["run_id"] for row in medium_cpu],
        }
    )
    return {
        "protocol_version": "3.0.0",
        "status": "pass" if all(check["passed"] for check in checks) else "fail",
        "checks": checks,
        "infrastructure_invalid_records": sum(
            record["infrastructure_invalid"] for record in records
        ),
    }


def derive_ground_truth(records: list[dict[str, Any]]) -> dict[str, Any]:
    valid = _valid(records)
    manifest_ids = [
        item["workload_id"]
        for item in load_manifest()["workloads"]
        if item["evaluation_set"].startswith("holdout_")
    ]
    workloads: dict[str, Any] = {}
    for workload_id in manifest_ids:
        profiles: dict[str, Any] = {}
        for profile in PROFILES:
            selected = [
                row
                for row in valid
                if row["workload_id"] == workload_id and row["applied_profile"] == profile
            ]
            if len(selected) != 5:
                raise ValueError(
                    f"incomplete ground-truth cell {workload_id}/{profile}: "
                    f"expected 5 valid records, found {len(selected)}"
                )
            reliable = len(selected) == 5 and all(row["success"] for row in selected)
            profiles[profile] = {
                "records": len(selected),
                "reliable": reliable,
                "successes": sum(row["success"] for row in selected),
                "ooms": sum(row["oom_killed"] for row in selected),
                "median_time_to_outcome_seconds": _median(
                    row["time_to_outcome_seconds"] for row in selected if row["success"]
                ),
                "median_benchmark_runtime_seconds": _median(
                    row["benchmark_runtime_seconds"] for row in selected if row["success"]
                ),
                "median_peak_memory_mib": _median(
                    row["actual_cgroup_peak_mib"] for row in selected if row["success"]
                ),
                "median_memory_waste": _median(_waste(row) for row in selected if row["success"]),
                "run_ids": [row["run_id"] for row in selected],
            }
        reliable_profiles = [
            profile for profile in PROFILES if profiles[profile]["reliable"]
        ]
        smallest = reliable_profiles[0] if reliable_profiles else None
        acceptable: list[str] = []
        if smallest:
            acceptable.append(smallest)
            baseline_time = profiles[smallest]["median_time_to_outcome_seconds"]
            for profile in reliable_profiles[1:]:
                candidate_time = profiles[profile]["median_time_to_outcome_seconds"]
                waste = profiles[profile]["median_memory_waste"]
                faster = (
                    baseline_time not in (None, 0)
                    and candidate_time is not None
                    and candidate_time <= 0.8 * baseline_time
                )
                if faster or (waste is not None and waste < 0.5):
                    acceptable.append(profile)
        workloads[workload_id] = {
            "smallest_reliable_profile": smallest,
            "utility_acceptable_profiles": acceptable,
            "profiles": profiles,
        }
    return {
        "schema_version": "3.0.0",
        "derivation": "five-of-five reliable; preregistered 20% time or <50% waste utility rule",
        "workloads": workloads,
    }


def _cluster_bootstrap_difference(
    records: list[dict[str, Any]],
    baseline: str,
    field: str,
) -> dict[str, float | None]:
    workload_ids = sorted({row["workload_id"] for row in records})
    if not workload_ids:
        return {"estimate": None, "lower_95": None, "upper_95": None}

    def rate(workload_id: str, method: str) -> float:
        rows = [
            row
            for row in records
            if row["workload_id"] == workload_id and row["method"] == method
        ]
        return sum(bool(row[field]) for row in rows) / len(rows)

    differences = {
        workload_id: rate(workload_id, "context_aware") - rate(workload_id, baseline)
        for workload_id in workload_ids
    }
    estimate = sum(differences.values()) / len(differences)
    rng = random.Random(f"{BOOTSTRAP_SEED}|{baseline}|{field}")
    samples = []
    for _ in range(BOOTSTRAP_SAMPLES):
        chosen = [rng.choice(workload_ids) for _ in workload_ids]
        samples.append(sum(differences[item] for item in chosen) / len(chosen))
    samples.sort()
    return {
        "estimate": estimate,
        "lower_95": samples[int(0.025 * BOOTSTRAP_SAMPLES)],
        "upper_95": samples[int(0.975 * BOOTSTRAP_SAMPLES) - 1],
    }


def _mcnemar_exact(
    records: list[dict[str, Any]], baseline: str, field: str
) -> dict[str, int | float | None]:
    keyed = {
        (row["workload_id"], row["repeat_index"], row["method"]): bool(row[field])
        for row in records
    }
    pairs = sorted({(row["workload_id"], row["repeat_index"]) for row in records})
    context_only = 0
    baseline_only = 0
    for workload_id, repeat in pairs:
        context = keyed.get((workload_id, repeat, "context_aware"))
        base = keyed.get((workload_id, repeat, baseline))
        if context is None or base is None or context == base:
            continue
        if context:
            context_only += 1
        else:
            baseline_only += 1
    discordant = context_only + baseline_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(
            math.comb(discordant, index)
            for index in range(min(context_only, baseline_only) + 1)
        ) / (2**discordant)
        p_value = min(1.0, 2 * tail)
    return {
        "context_only": context_only,
        "baseline_only": baseline_only,
        "discordant_pairs": discordant,
        "two_sided_exact_p": p_value,
        "holm_adjusted_p": None,
    }


def _holm_adjust(tests: list[dict[str, Any]]) -> None:
    ordered = sorted(
        enumerate(tests), key=lambda item: item[1]["test"]["two_sided_exact_p"]
    )
    running = 0.0
    total = len(ordered)
    for rank, (_, item) in enumerate(ordered):
        adjusted = min(
            1.0,
            (total - rank) * float(item["test"]["two_sided_exact_p"]),
        )
        running = max(running, adjusted)
        item["test"]["holm_adjusted_p"] = running


def analyze_comparative(
    records: list[dict[str, Any]], ground_truth: dict[str, Any]
) -> dict[str, Any]:
    valid = _valid(records)
    manifest = {item["workload_id"]: item for item in load_manifest()["workloads"]}
    core = [row for row in valid if row["evaluation_set"] == "holdout_core"]
    robustness = [
        row for row in valid if row["evaluation_set"] == "holdout_robustness"
    ]
    for workload_id in sorted({row["workload_id"] for row in valid}):
        for method in METHODS:
            count = sum(
                row["workload_id"] == workload_id and row["method"] == method
                for row in valid
            )
            if count != 5:
                raise ValueError(
                    f"incomplete comparative cell {workload_id}/{method}: "
                    f"expected 5 valid records, found {count}"
                )

    checksums: dict[tuple[str, int], set[str]] = {}
    for row in valid:
        if row["checksum"]:
            checksums.setdefault((row["workload_id"], row["repeat_index"]), set()).add(
                row["checksum"]
            )
    mismatches = [
        {"workload_id": key[0], "repeat_index": key[1], "checksums": sorted(values)}
        for key, values in checksums.items()
        if len(values) > 1
    ]
    if mismatches:
        raise ValueError(f"checksum mismatch invalidates comparative matrix: {mismatches}")

    summaries = []
    confusion_matrices: list[dict[str, Any]] = []
    for method in METHODS:
        selected = [row for row in core if row["method"] == method]
        successes = sum(row["success"] for row in selected)
        ooms = sum(row["oom_killed"] for row in selected)
        under_steps: list[int] = []
        over_steps: list[int] = []
        exact = 0
        for row in selected:
            minimum = ground_truth["workloads"][row["workload_id"]][
                "smallest_reliable_profile"
            ]
            if minimum is None:
                continue
            delta = PROFILES.index(row["applied_profile"]) - PROFILES.index(minimum)
            under_steps.append(max(0, -delta))
            over_steps.append(max(0, delta))
            exact += delta == 0
        summaries.append(
            {
                "method": method,
                "records": len(selected),
                "successes": successes,
                "success_rate": successes / len(selected) if selected else None,
                "success_wilson_95": wilson_interval(successes, len(selected)),
                "ooms": ooms,
                "oom_rate": ooms / len(selected) if selected else None,
                "oom_wilson_95": wilson_interval(ooms, len(selected)),
                "mean_underprovision_steps": (
                    sum(under_steps) / len(under_steps) if under_steps else None
                ),
                "mean_overprovision_steps": (
                    sum(over_steps) / len(over_steps) if over_steps else None
                ),
                "exact_minimum_profile_rate": (
                    exact / len(under_steps) if under_steps else None
                ),
                "median_memory_waste": _median(_waste(row) for row in selected),
                "memory_waste_iqr": _iqr(_waste(row) for row in selected),
                "benchmark_runtime_iqr_seconds": _iqr(
                    row["benchmark_runtime_seconds"] for row in selected
                ),
                "wilson_unit": "trial_level_descriptive_with_clustered_repeats",
            }
        )
        for operational_minimum in PROFILES:
            for applied_profile in PROFILES:
                confusion_matrices.append(
                    {
                        "method": method,
                        "operational_minimum_profile": operational_minimum,
                        "applied_profile": applied_profile,
                        "records": sum(
                            row["applied_profile"] == applied_profile
                            and ground_truth["workloads"][row["workload_id"]][
                                "smallest_reliable_profile"
                            ]
                            == operational_minimum
                            for row in selected
                        ),
                    }
                )

    robustness_cases = []
    for workload_id in ("h07_noisy_overstated", "h08_hidden_large"):
        for method in METHODS:
            selected = [
                row
                for row in robustness
                if row["workload_id"] == workload_id and row["method"] == method
            ]
            robustness_cases.append(
                {
                    "workload_id": workload_id,
                    "signal_path": manifest[workload_id]["expected_signal_path"],
                    "method": method,
                    "applied_profile": selected[0]["applied_profile"] if selected else None,
                    "successes": sum(row["success"] for row in selected),
                    "ooms": sum(row["oom_killed"] for row in selected),
                    "records": len(selected),
                }
            )

    contrasts = []
    exact_tests = []
    for baseline in ("static_default", "intent_only"):
        contrasts.append(
            {
                "baseline": baseline,
                "success_rate_difference_context_minus_baseline": _cluster_bootstrap_difference(
                    core, baseline, "success"
                ),
                "oom_rate_difference_context_minus_baseline": _cluster_bootstrap_difference(
                    core, baseline, "oom_killed"
                ),
            }
        )
        exact_tests.extend(
            [
                {
                    "baseline": baseline,
                    "outcome": outcome,
                    "test": _mcnemar_exact(core, baseline, field),
                }
                for outcome, field in (
                    ("success", "success"),
                    ("oom", "oom_killed"),
                )
            ]
        )
    _holm_adjust(exact_tests)
    failure_accounting = []
    for stratum, rows in (
        ("confirmatory", core),
        ("robustness", robustness),
        ("all_including_infrastructure_invalid", records),
    ):
        for category in sorted({row["failure_category"] for row in rows}):
            failure_accounting.append(
                {
                    "stratum": stratum,
                    "failure_category": category,
                    "records": sum(row["failure_category"] == category for row in rows),
                }
            )
    exclusions = [
        {
            "run_id": row["run_id"],
            "workload_id": row["workload_id"],
            "method": row["method"],
            "reason": row["exclusion_reason"],
            "replacement_run_id": row["replacement_run_id"],
        }
        for row in records
        if row["infrastructure_invalid"]
    ]
    return {
        "protocol_version": "3.0.0",
        "primary_stratum": "h01-h06",
        "summaries": summaries,
        "cluster_bootstrap_samples": BOOTSTRAP_SAMPLES,
        "contrasts": contrasts,
        "supplementary_exact_mcnemar": exact_tests,
        "confusion_matrices": confusion_matrices,
        "robustness_cases": robustness_cases,
        "failure_accounting": failure_accounting,
        "exclusions": exclusions,
        "checksum_mismatches": mismatches,
        "infrastructure_invalid_records": sum(
            row["infrastructure_invalid"] for row in records
        ),
        "power_boundary": {
            "workload_clusters": 6,
            "repeats_per_cell": 5,
            "effective_n_at_icc_0_5": 30 / (1 + 4 * 0.5),
            "interpretation": "estimation study; powered only for approximately 45-55 percentage-point effects",
        },
    }


def analyze_end_to_end(
    records: list[dict[str, Any]], direct_records: list[dict[str, Any]]
) -> dict[str, Any]:
    valid = _valid(records)
    direct = _valid(direct_records)
    expected_workloads = {
        "h01_small_stream",
        "h02_medium_size_signal",
        "h04_large_honest",
        "h05_large_context_recovery",
        "h06_cpu_parallel",
    }
    actual_workloads = {row["workload_id"] for row in valid}
    if actual_workloads != expected_workloads:
        raise ValueError(
            "end-to-end sentinel set differs from the preregistration: "
            f"{sorted(actual_workloads)}"
        )
    rows = []
    for workload_id in sorted({row["workload_id"] for row in valid}):
        for method in METHODS:
            end_rows = [
                row
                for row in valid
                if row["workload_id"] == workload_id and row["method"] == method
            ]
            direct_rows = [
                row
                for row in direct
                if row["workload_id"] == workload_id and row["method"] == method
            ]
            if not end_rows:
                continue
            if len(end_rows) != 3:
                raise ValueError(
                    f"incomplete end-to-end cell {workload_id}/{method}: "
                    f"expected 3 valid records, found {len(end_rows)}"
                )
            direct_profile = direct_rows[0]["applied_profile"] if direct_rows else None
            if len(direct_rows) != 5 or len(
                {row["applied_profile"] for row in direct_rows}
            ) != 1:
                raise ValueError(
                    f"incomplete or inconsistent direct comparison cell "
                    f"{workload_id}/{method}"
                )
            end_profiles = {row["applied_profile"] for row in end_rows}
            rows.append(
                {
                    "workload_id": workload_id,
                    "method": method,
                    "end_to_end_records": len(end_rows),
                    "direct_records": len(direct_rows),
                    "applied_profile_concordant": (
                        len(end_profiles) == 1 and direct_profile in end_profiles
                    ),
                    "end_to_end_success_direction": (
                        sum(row["success"] for row in end_rows) / len(end_rows) >= 0.5
                    ),
                    "direct_success_direction": (
                        None
                        if not direct_rows
                        else sum(row["success"] for row in direct_rows)
                        / len(direct_rows)
                        >= 0.5
                    ),
                    "end_to_end_oom_direction": (
                        sum(row["oom_killed"] for row in end_rows) / len(end_rows) >= 0.5
                    ),
                    "direct_oom_direction": (
                        None
                        if not direct_rows
                        else sum(row["oom_killed"] for row in direct_rows)
                        / len(direct_rows)
                        >= 0.5
                    ),
                    "median_spawn_latency_seconds": _median(
                        row["spawn_latency_seconds"] for row in end_rows
                    ),
                }
            )
    return {
        "protocol_version": "3.0.0",
        "role": "fidelity_replication_not_powered_efficacy",
        "rows": rows,
        "profile_concordance_rate": (
            sum(row["applied_profile_concordant"] for row in rows) / len(rows)
            if rows
            else None
        ),
        "success_direction_concordance_rate": (
            sum(
                row["end_to_end_success_direction"]
                == row["direct_success_direction"]
                for row in rows
            )
            / len(rows)
            if rows
            else None
        ),
        "oom_direction_concordance_rate": (
            sum(
                row["end_to_end_oom_direction"] == row["direct_oom_direction"]
                for row in rows
            )
            / len(rows)
            if rows
            else None
        ),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_integrity_manifest(directory: Path) -> None:
    manifest = directory / "SHA256SUMS"
    lines = [
        f"{_sha256(path)}  {path.relative_to(directory)}\n"
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path != manifest
    ]
    with manifest.open("x", encoding="utf-8") as handle:
        handle.writelines(lines)


def _write_success_figure(path: Path, summaries: list[dict[str, Any]]) -> None:
    width = 720
    height = 420
    left = 90
    bottom = 340
    chart_height = 280
    bar_width = 110
    gap = 90
    colors = {
        "static_default": "#4c78a8",
        "intent_only": "#f58518",
        "context_aware": "#54a24b",
    }
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="360" y="28" text-anchor="middle" font-family="sans-serif" '
        'font-size="18">Confirmatory hold-out success rate (descriptive)</text>',
        f'<line x1="{left}" y1="60" x2="{left}" y2="{bottom}" stroke="black"/>',
        f'<line x1="{left}" y1="{bottom}" x2="680" y2="{bottom}" stroke="black"/>',
    ]
    for tick in range(0, 6):
        rate = tick / 5
        y = bottom - rate * chart_height
        lines.append(
            f'<line x1="{left - 5}" y1="{y:.1f}" x2="{left}" y2="{y:.1f}" stroke="black"/>'
        )
        lines.append(
            f'<text x="{left - 10}" y="{y + 5:.1f}" text-anchor="end" '
            f'font-family="sans-serif" font-size="12">{rate:.1f}</text>'
        )
    for index, row in enumerate(summaries):
        rate = float(row["success_rate"] or 0.0)
        x = left + 70 + index * (bar_width + gap)
        bar_height = rate * chart_height
        y = bottom - bar_height
        method = row["method"]
        lines.extend(
            [
                f'<rect x="{x}" y="{y:.1f}" width="{bar_width}" '
                f'height="{bar_height:.1f}" fill="{colors[method]}"/>',
                f'<text x="{x + bar_width / 2:.1f}" y="{y - 8:.1f}" '
                f'text-anchor="middle" font-family="sans-serif" font-size="13">{rate:.3f}</text>',
                f'<text x="{x + bar_width / 2:.1f}" y="{bottom + 24}" '
                f'text-anchor="middle" font-family="sans-serif" font-size="12">{method}</text>',
            ]
        )
    lines.append(
        '<text x="18" y="200" transform="rotate(-90 18 200)" '
        'text-anchor="middle" font-family="sans-serif" font-size="13">Success rate</text>'
    )
    lines.append("</svg>")
    with path.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _write_calibration_report(path: Path, calibration: dict[str, Any]) -> None:
    lines = [
        "# Protocol-v3 Calibration Gate",
        "",
        f"Status: **{calibration['status']}**.",
        "",
        "| Workload | Profile | Expected | Records | Passed |",
        "| --- | --- | --- | ---: | --- |",
    ]
    lines.extend(
        f"| {row['workload_id']} | {row['profile']} | {row['expected']} | "
        f"{row['records']} | {row['passed']} |"
        for row in calibration["checks"]
    )
    lines.extend(
        [
            "",
            "Calibration records are excluded from method comparisons. A failed gate",
            "forbids ground-truth, comparative, and JupyterHub execution.",
            "",
        ]
    )
    with path.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def _write_report(
    path: Path,
    calibration: dict[str, Any],
    comparison: dict[str, Any],
    end_to_end: dict[str, Any] | None,
) -> None:
    lines = [
        "# Protocol-v3 Observed Results",
        "",
        "> Generated only from supplied v3 raw records. These results apply to the",
        "> frozen synthetic suite and pinned disposable environment, not production.",
        "",
        "## Calibration",
        "",
        f"Calibration gate: **{calibration['status']}**.",
        "",
        "## Confirmatory hold-out",
        "",
        "| Method | Success | OOM | Mean under steps | Mean over steps | Exact minimum |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in comparison["summaries"]:
        lines.append(
            "| {method} | {successes}/{records} | {ooms}/{records} | "
            "{under:.3f} | {over:.3f} | {exact:.3f} |".format(
                method=row["method"],
                successes=row["successes"],
                ooms=row["ooms"],
                records=row["records"],
                under=row["mean_underprovision_steps"] or 0.0,
                over=row["mean_overprovision_steps"] or 0.0,
                exact=row["exact_minimum_profile_rate"] or 0.0,
            )
        )
    lines.extend(
        [
            "",
            "The two robustness workloads are reported only in",
            "`robustness-cases.csv`; they are not included in this headline table.",
            "",
            "## End-to-end fidelity",
            "",
        ]
    )
    if end_to_end is None:
        lines.append("No JupyterHub end-to-end directory was supplied; no fidelity claim is available.")
    else:
        lines.extend(
            [
                f"Applied-profile concordance: {end_to_end['profile_concordance_rate']:.3f}.",
                f"Success-direction concordance: {end_to_end['success_direction_concordance_rate']:.3f}.",
                f"OOM-direction concordance: {end_to_end['oom_direction_concordance_rate']:.3f}.",
            ]
        )
    lines.extend(
        [
            "",
            "## Claim boundary",
            "",
            "The outputs may support controlled synthetic resource-envelope, OOM-boundary,",
            "fixed-suite method, CPU-throttling, and resource-application statements.",
            "They do not establish production effectiveness, real-user behavior,",
            "multi-user density, autoscaling, GPU/history-aware behavior, security,",
            "continuous CPU peaks, or SLA reliability. Pressure padding is not a real",
            "dataset size.",
            "",
        ]
    )
    with path.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze protocol-v3 evidence.")
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path)
    parser.add_argument("--comparative", type=Path)
    parser.add_argument("--end-to-end", type=Path)
    parser.add_argument("--calibration-only", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite analysis directory {args.out}")
    from cluster_evaluation.evidence_v3 import validate_experiment

    evidence_reports = {
        "calibration": validate_experiment(args.calibration, "calibration")
    }
    calibration = validate_calibration(_load_records(args.calibration))
    if calibration["status"] != "pass":
        args.out.mkdir(parents=True)
        _write_json(args.out / "analysis-inputs.json", {
            "protocol_version": "3.0.0",
            "analysis_sha256": _sha256(Path(__file__)),
            "manifest_sha256": _sha256(ROOT / "benchmarks" / "workloads-v3.yaml"),
            "evidence": evidence_reports,
        })
        _write_json(args.out / "calibration.json", calibration)
        _write_calibration_report(args.out / "CALIBRATION_REPORT.md", calibration)
        _write_integrity_manifest(args.out)
        raise RuntimeError("calibration gate failed; hold-out analysis is not permitted")
    if args.calibration_only:
        args.out.mkdir(parents=True)
        _write_json(args.out / "analysis-inputs.json", {
            "protocol_version": "3.0.0",
            "analysis_sha256": _sha256(Path(__file__)),
            "manifest_sha256": _sha256(ROOT / "benchmarks" / "workloads-v3.yaml"),
            "evidence": evidence_reports,
        })
        _write_json(args.out / "calibration.json", calibration)
        _write_calibration_report(args.out / "CALIBRATION_REPORT.md", calibration)
        _write_integrity_manifest(args.out)
        return 0
    if args.ground_truth is None or args.comparative is None:
        raise ValueError(
            "--ground-truth and --comparative are required unless --calibration-only is used"
        )
    evidence_reports["ground_truth"] = validate_experiment(
        args.ground_truth, "ground-truth"
    )
    evidence_reports["comparative"] = validate_experiment(
        args.comparative, "comparative"
    )
    direct_commits = {
        report["git_commit"]
        for report in evidence_reports.values()
    }
    direct_images = {
        report["container_image"]
        for report in evidence_reports.values()
    }
    if len(direct_commits) != 1 or len(direct_images) != 1:
        raise ValueError("direct-pod phases use inconsistent commits or image digests")
    if args.end_to_end is not None:
        evidence_reports["jupyterhub"] = validate_experiment(
            args.end_to_end, "jupyterhub"
        )
    ground = derive_ground_truth(_load_records(args.ground_truth))
    comparative_records = _load_records(args.comparative)
    comparison = analyze_comparative(comparative_records, ground)
    end_to_end = (
        analyze_end_to_end(_load_records(args.end_to_end), comparative_records)
        if args.end_to_end is not None
        else None
    )
    args.out.mkdir(parents=True)
    _write_json(args.out / "analysis-inputs.json", {
        "protocol_version": "3.0.0",
        "analysis_sha256": _sha256(Path(__file__)),
        "manifest_sha256": _sha256(ROOT / "benchmarks" / "workloads-v3.yaml"),
        "evidence": evidence_reports,
    })
    _write_json(args.out / "calibration.json", calibration)
    _write_json(args.out / "ground-truth.json", ground)
    _write_json(args.out / "comparative.json", comparison)
    _write_csv(args.out / "method-summary.csv", comparison["summaries"])
    _write_csv(args.out / "robustness-cases.csv", comparison["robustness_cases"])
    _write_csv(args.out / "confusion-matrices.csv", comparison["confusion_matrices"])
    _write_csv(args.out / "failure-accounting.csv", comparison["failure_accounting"])
    _write_csv(args.out / "mcnemar-holm.csv", [
        {
            "baseline": row["baseline"],
            "outcome": row["outcome"],
            **row["test"],
        }
        for row in comparison["supplementary_exact_mcnemar"]
    ])
    _write_success_figure(
        args.out / "confirmatory-success-rate.svg", comparison["summaries"]
    )
    if end_to_end is not None:
        _write_json(args.out / "end-to-end-fidelity.json", end_to_end)
        _write_csv(args.out / "end-to-end-fidelity.csv", end_to_end["rows"])
    _write_report(args.out / "REPORT.md", calibration, comparison, end_to_end)
    _write_integrity_manifest(args.out)
    return 0


def _write_json(path: Path, payload: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
