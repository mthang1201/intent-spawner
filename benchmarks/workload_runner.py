"""Run deterministic synthetic benchmark workloads.

The benchmark suite uses generated data only. The runner intentionally avoids
heavy optional dependencies so the non-cluster validation path stays small and
repeatable. Workloads that represent pandas or scikit-learn scenarios emulate
the same data-shape and memory-pressure signals with the Python standard
library; the manifest carries the user-facing intent and code-context hints
that the recommender evaluates.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import resource
import statistics
import sys
import tempfile
import time
from typing import Callable


EXIT_USAGE = 2
EXIT_RUNTIME_FAILURE = 1
EXIT_MEMORY_PRESSURE = 70

SCALES = {
    "tiny": 0.2,
    "small": 1.0,
    "medium": 2.5,
    "large": 5.0,
    "boundary_below": 0.9,
    "boundary_above": 1.1,
    "memory_pressure": 4.0,
}


@dataclass(frozen=True)
class WorkloadPlan:
    operation: str
    base_rows: int
    features: int
    groups: int = 16
    memory_target_mib: int = 0
    iterations: int = 5


WORKLOAD_PLANS: dict[str, WorkloadPlan] = {
    "light_basic_python": WorkloadPlan("basic_python", base_rows=20_000, features=3),
    "light_small_csv_read": WorkloadPlan("csv_read", base_rows=4_000, features=4),
    "light_visual_aggregation": WorkloadPlan("visual_aggregation", base_rows=12_000, features=2),
    "data_pandas_read_transform": WorkloadPlan("read_transform", base_rows=18_000, features=6),
    "data_dataframe_join_medium": WorkloadPlan("join", base_rows=16_000, features=5),
    "data_large_aggregation": WorkloadPlan("aggregation", base_rows=30_000, features=8, groups=96),
    "ml_sklearn_fit_small": WorkloadPlan("ml_fit", base_rows=2_500, features=8, iterations=4),
    "ml_sklearn_fit_medium": WorkloadPlan("ml_fit", base_rows=7_500, features=16, iterations=5),
    "ml_sklearn_fit_memory_pressure": WorkloadPlan(
        "ml_fit",
        base_rows=14_000,
        features=32,
        memory_target_mib=192,
        iterations=6,
    ),
    "boundary_below_0_5_ambiguous": WorkloadPlan("basic_python", base_rows=18_000, features=3),
    "boundary_above_0_5_conflicting": WorkloadPlan("ml_fit", base_rows=4_000, features=10, iterations=4),
    "policy_gpu_disallowed": WorkloadPlan("policy_check", base_rows=1_000, features=2),
}


def _scaled_count(base_rows: int, scale: str) -> int:
    if scale not in SCALES:
        raise ValueError(f"unsupported scale {scale!r}; expected one of {sorted(SCALES)}")
    return max(1, int(base_rows * SCALES[scale]))


def _rng(seed: int) -> random.Random:
    return random.Random(seed)


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _usage_metadata() -> dict[str, object]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    max_rss_bytes = usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
    return {
        "user_cpu_seconds": round(usage.ru_utime, 6),
        "system_cpu_seconds": round(usage.ru_stime, 6),
        "max_rss_bytes": max_rss_bytes,
    }


def _basic_python(plan: WorkloadPlan, rows: int, seed: int) -> dict[str, object]:
    rng = _rng(seed)
    values = [rng.random() for _ in range(rows)]
    total = sum((index % 11 + 1) * value for index, value in enumerate(values))
    return {
        "rows": rows,
        "features": plan.features,
        "mean": round(statistics.fmean(values), 8),
        "weighted_total": round(total, 8),
    }


def _csv_read(plan: WorkloadPlan, rows: int, seed: int) -> dict[str, object]:
    rng = _rng(seed)
    sums = [0.0 for _ in range(plan.features)]
    with tempfile.TemporaryDirectory(prefix="intent-spawner-bench-") as tmpdir:
        csv_path = Path(tmpdir) / "synthetic.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([f"feature_{index}" for index in range(plan.features)])
            for row_index in range(rows):
                row = [round(rng.random() + row_index * 0.00001, 8) for _ in range(plan.features)]
                writer.writerow(row)

        with csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                for index in range(plan.features):
                    sums[index] += float(row[f"feature_{index}"])

    return {
        "rows": rows,
        "features": plan.features,
        "column_sums": [round(value, 6) for value in sums],
    }


def _visual_aggregation(plan: WorkloadPlan, rows: int, seed: int) -> dict[str, object]:
    rng = _rng(seed)
    bins = [0 for _ in range(12)]
    values: list[float] = []
    for _ in range(rows):
        value = min(0.999999, max(0.0, rng.gauss(0.5, 0.16)))
        values.append(value)
        bins[int(value * len(bins))] += 1
    return {
        "rows": rows,
        "histogram_bins": bins,
        "mean": round(statistics.fmean(values), 8),
    }


def _read_transform(plan: WorkloadPlan, rows: int, seed: int) -> dict[str, object]:
    rng = _rng(seed)
    transformed_total = 0.0
    kept_rows = 0
    for row_index in range(rows):
        row = [rng.random() * (feature + 1) for feature in range(plan.features)]
        if row[0] + row[1] > 0.65:
            kept_rows += 1
            transformed_total += math.log1p(sum(row)) + row_index % 7
    return {
        "rows": rows,
        "features": plan.features,
        "kept_rows": kept_rows,
        "transformed_total": round(transformed_total, 6),
    }


def _join(plan: WorkloadPlan, rows: int, seed: int) -> dict[str, object]:
    rng = _rng(seed)
    right_size = max(1, rows // 3)
    right = {index: rng.random() * 10 for index in range(right_size)}
    joined_rows = 0
    joined_total = 0.0
    for row_index in range(rows):
        key = row_index % right_size
        left_value = rng.random() * (row_index % 17 + 1)
        if key in right:
            joined_rows += 1
            joined_total += left_value * right[key]
    return {
        "rows": rows,
        "right_rows": right_size,
        "joined_rows": joined_rows,
        "joined_total": round(joined_total, 6),
    }


def _aggregation(plan: WorkloadPlan, rows: int, seed: int) -> dict[str, object]:
    rng = _rng(seed)
    grouped = {group: [0, 0.0] for group in range(plan.groups)}
    for row_index in range(rows):
        group = row_index % plan.groups
        value = rng.random() * (1 + group / plan.groups)
        grouped[group][0] += 1
        grouped[group][1] += value
    leaders = sorted(
        ((group, count, total) for group, (count, total) in grouped.items()),
        key=lambda item: item[2],
        reverse=True,
    )[:5]
    return {
        "rows": rows,
        "groups": plan.groups,
        "top_groups": [[group, count, round(total, 6)] for group, count, total in leaders],
    }


def _allocate_pressure(target_mib: int) -> list[bytearray]:
    if target_mib <= 0:
        return []
    block_mib = 8
    blocks: list[bytearray] = []
    allocated = 0
    while allocated < target_mib:
        blocks.append(bytearray(min(block_mib, target_mib - allocated) * 1024 * 1024))
        allocated += block_mib
    return blocks


def _ml_fit(plan: WorkloadPlan, rows: int, seed: int) -> dict[str, object]:
    rng = _rng(seed)
    pressure_blocks = _allocate_pressure(plan.memory_target_mib)
    weights = [0.0 for _ in range(plan.features)]
    labels_seen = 0

    for _ in range(plan.iterations):
        gradients = [0.0 for _ in range(plan.features)]
        labels_seen = 0
        for row_index in range(rows):
            features = [
                math.sin((row_index + 1) * (feature + 1) * 0.013) + rng.random() * 0.05
                for feature in range(plan.features)
            ]
            label = 1 if sum(features[: max(1, plan.features // 3)]) > 0 else 0
            prediction = 1.0 / (1.0 + math.exp(-sum(w * x for w, x in zip(weights, features))))
            error = prediction - label
            for feature_index, value in enumerate(features):
                gradients[feature_index] += error * value
            labels_seen += label
        for feature_index in range(plan.features):
            weights[feature_index] -= 0.2 * gradients[feature_index] / rows

    return {
        "rows": rows,
        "features": plan.features,
        "iterations": plan.iterations,
        "positive_labels_last_pass": labels_seen,
        "memory_pressure_mib": plan.memory_target_mib,
        "pressure_blocks": len(pressure_blocks),
        "weight_checksum": round(sum(weights), 8),
    }


def _policy_check(plan: WorkloadPlan, rows: int, seed: int) -> dict[str, object]:
    rng = _rng(seed)
    return {
        "rows": rows,
        "features": plan.features,
        "gpu_requested": True,
        "gpu_available": False,
        "policy_probe": round(sum(rng.random() for _ in range(rows)), 8),
    }


OPERATIONS: dict[str, Callable[[WorkloadPlan, int, int], dict[str, object]]] = {
    "basic_python": _basic_python,
    "csv_read": _csv_read,
    "visual_aggregation": _visual_aggregation,
    "read_transform": _read_transform,
    "join": _join,
    "aggregation": _aggregation,
    "ml_fit": _ml_fit,
    "policy_check": _policy_check,
}


def run_workload(workload_id: str, scale: str, seed: int) -> dict[str, object]:
    if workload_id not in WORKLOAD_PLANS:
        raise ValueError(f"unknown workload_id {workload_id!r}")

    plan = WORKLOAD_PLANS[workload_id]
    rows = _scaled_count(plan.base_rows, scale)
    operation = OPERATIONS[plan.operation]
    started = time.monotonic()
    result = operation(plan, rows, seed)
    deterministic_payload = {
        "workload_id": workload_id,
        "scale": scale,
        "seed": seed,
        "operation": plan.operation,
        "result": result,
    }

    return {
        "schema_version": "1.0",
        "workload_id": workload_id,
        "operation": plan.operation,
        "scale": scale,
        "seed": seed,
        "synthetic_data": True,
        "data_persisted": False,
        "deterministic_digest": _digest(deterministic_payload),
        "result": result,
        "runtime": {
            "elapsed_seconds": round(time.monotonic() - started, 6),
            **_usage_metadata(),
        },
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a synthetic benchmark workload.")
    parser.add_argument("--workload-id", required=True, choices=sorted(WORKLOAD_PLANS))
    parser.add_argument("--scale", required=True, choices=sorted(SCALES))
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument(
        "--metadata-out",
        help="Optional path for the JSON metadata. Metadata is always printed to stdout.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        metadata = run_workload(args.workload_id, args.scale, args.seed)
    except MemoryError:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "workload_id": args.workload_id,
                    "status": "failed",
                    "error": "memory allocation failed before the configured workload completed",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return EXIT_MEMORY_PRESSURE
    except ValueError as exc:
        print(f"workload configuration error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"workload runtime failure: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_FAILURE

    encoded = json.dumps(metadata, sort_keys=True)
    if args.metadata_out:
        output_path = Path(args.metadata_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with output_path.open("x", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
        except FileExistsError:
            print(f"refusing to overwrite existing metadata: {output_path}", file=sys.stderr)
            return EXIT_USAGE
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
