"""Bounded synthetic workloads for the preregistered v3 experiment.

The v3 runner is deliberately separate from ``workload_runner.py`` so the
published v1/v2 corpus remains reproducible. Memory workloads target the
container's total cgroup usage, page-touch every anonymous allocation, and keep
all blocks alive for a bounded hold. The CLI never downloads or reads data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
from pathlib import Path
import random
import sys
import time
from typing import Any, Callable

import yaml


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "workloads-v3.yaml"
CGROUP_ROOT = Path("/sys/fs/cgroup")
MIB = 1024 * 1024
MAX_TARGET_MIB = 1700
MAX_DEADLINE_SECONDS = 120
CHUNK_MIB = 8
OVERSHOOT_MIB = 16
PAGE_BYTES = 4096
CPU_WORKERS = 2
CPU_ITERATIONS_PER_UNIT = 100_000
EVALUATION_SETS = {"calibration", "holdout_core", "holdout_robustness"}
MEMORY_OPERATIONS = {
    "stream_aggregation",
    "table_transform",
    "table_join",
    "sort_group",
    "encoded_fit",
    "materialization",
}
WORKLOAD_REQUIRED_FIELDS = {
    "workload_id",
    "evaluation_set",
    "operation",
    "description",
    "intent",
    "dataset_size_hint_gb",
    "code_context_hints",
    "target_cgroup_mib",
    "target_band_mib",
    "hold_seconds",
    "max_allocation_mib",
    "work_units",
    "workload_deadline_seconds",
    "expected_signal_path",
    "expected_minimum_profile",
    "expected_method_profiles",
    "deterministic_seed",
    "data_source",
    "license",
}

EXIT_RUNTIME_FAILURE = 1
EXIT_USAGE = 2
EXIT_MEMORY_PRESSURE = 70
EXIT_DEADLINE = 124


def load_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        manifest = yaml.safe_load(handle)
    validate_manifest(manifest)
    return manifest


def workloads_by_id(path: Path = MANIFEST) -> dict[str, dict[str, Any]]:
    return {item["workload_id"]: item for item in load_manifest(path)["workloads"]}


def validate_manifest(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("v3 manifest must be an object")
    if manifest.get("schema_version") != "3.0.0":
        raise ValueError("v3 manifest must declare schema_version 3.0.0")
    if manifest.get("protocol_version") != "3.0.0":
        raise ValueError("v3 manifest must declare protocol_version 3.0.0")
    if manifest.get("master_seed") != 20260723:
        raise ValueError("v3 manifest master seed differs from the preregistration")
    if manifest.get("namespace") != "z2jh-context-demo":
        raise ValueError("v3 manifest namespace differs from the preregistration")
    limits = manifest.get("limits", {})
    if not isinstance(limits, dict):
        raise ValueError("v3 manifest limits must be an object")
    if limits.get("max_target_cgroup_mib") != MAX_TARGET_MIB:
        raise ValueError("manifest maximum cgroup target differs from the runner hard cap")
    if limits.get("max_deadline_seconds") != MAX_DEADLINE_SECONDS:
        raise ValueError("manifest maximum deadline differs from the runner hard cap")
    expected_limits = {
        "allocation_chunk_mib": CHUNK_MIB,
        "allocation_overshoot_mib": OVERSHOOT_MIB,
        "page_touch_bytes": PAGE_BYTES,
        "memory_hold_seconds": 8,
        "cpu_worker_count": CPU_WORKERS,
    }
    for name, expected in expected_limits.items():
        if limits.get(name) != expected:
            raise ValueError(f"manifest limit {name} differs from the runner hard cap")

    items = manifest.get("workloads")
    if not isinstance(items, list) or not items:
        raise ValueError("v3 manifest must contain workloads")
    ids: set[str] = set()
    for workload in items:
        workload_id = str(workload.get("workload_id", ""))
        if not workload_id or workload_id in ids:
            raise ValueError(f"missing or duplicate workload_id {workload_id!r}")
        ids.add(workload_id)
        validate_workload(workload)
        if (
            workload["operation"] in MEMORY_OPERATIONS
            and int(workload["target_cgroup_mib"]) <= 0
        ):
            raise ValueError(f"{workload_id}: manifest memory target must be positive")
    counts = {
        evaluation_set: sum(item["evaluation_set"] == evaluation_set for item in items)
        for evaluation_set in EVALUATION_SETS
    }
    if counts != {"calibration": 4, "holdout_core": 6, "holdout_robustness": 2}:
        raise ValueError(f"v3 workload strata differ from the preregistration: {counts}")
    sentinel_ids = {
        item["workload_id"] for item in items if item.get("sentinel_end_to_end") is True
    }
    if sentinel_ids != {
        "h01_small_stream",
        "h02_medium_size_signal",
        "h04_large_honest",
        "h05_large_context_recovery",
        "h06_cpu_parallel",
    }:
        raise ValueError("v3 JupyterHub sentinel set differs from the preregistration")


def validate_workload(workload: dict[str, Any]) -> None:
    if not isinstance(workload, dict):
        raise ValueError("each v3 workload must be an object")
    missing = WORKLOAD_REQUIRED_FIELDS - workload.keys()
    if missing:
        raise ValueError(f"{workload.get('workload_id')}: missing fields {sorted(missing)}")
    workload_id = workload["workload_id"]
    if not isinstance(workload_id, str) or not workload_id:
        raise ValueError("workload_id must be a non-empty string")
    if workload["evaluation_set"] not in EVALUATION_SETS:
        raise ValueError(f"{workload_id}: invalid evaluation_set")
    if workload["operation"] not in OPERATIONS:
        raise ValueError(f"{workload_id}: unsupported operation")
    if not isinstance(workload["intent"], str):
        raise ValueError(f"{workload_id}: intent must be a string")
    if not isinstance(workload["code_context_hints"], list) or not all(
        isinstance(item, str) for item in workload["code_context_hints"]
    ):
        raise ValueError(f"{workload_id}: code_context_hints must be a list of strings")
    if not isinstance(workload["dataset_size_hint_gb"], (int, float)) or isinstance(
        workload["dataset_size_hint_gb"], bool
    ):
        raise ValueError(f"{workload_id}: dataset_size_hint_gb must be numeric")
    if float(workload["dataset_size_hint_gb"]) < 0:
        raise ValueError(f"{workload_id}: dataset_size_hint_gb cannot be negative")
    if not isinstance(workload["target_band_mib"], list) or len(
        workload["target_band_mib"]
    ) != 2:
        raise ValueError(f"{workload_id}: target_band_mib must contain two values")
    target = int(workload["target_cgroup_mib"])
    deadline = int(workload["workload_deadline_seconds"])
    max_allocation = int(workload["max_allocation_mib"])
    lower, upper = [int(value) for value in workload["target_band_mib"]]
    if not 0 <= target <= MAX_TARGET_MIB:
        raise ValueError(f"{workload_id}: target exceeds {MAX_TARGET_MIB} MiB")
    if not 1 <= deadline <= MAX_DEADLINE_SECONDS:
        raise ValueError(f"{workload_id}: deadline exceeds hard cap")
    if lower < 0 or upper < lower or upper > MAX_TARGET_MIB:
        raise ValueError(f"{workload_id}: invalid target band")
    if target and not lower <= target <= upper:
        raise ValueError(f"{workload_id}: target is outside target band")
    if max_allocation < 0:
        raise ValueError(f"{workload_id}: max_allocation_mib cannot be negative")
    if max_allocation > target + OVERSHOOT_MIB and workload["operation"] != "cpu_parallel":
        raise ValueError(f"{workload_id}: allocation cap exceeds target + 16 MiB")
    if float(workload["hold_seconds"]) < 0 or float(workload["hold_seconds"]) > 8:
        raise ValueError(f"{workload_id}: hold is outside the preregistered bound")
    if not isinstance(workload["work_units"], int) or isinstance(
        workload["work_units"], bool
    ):
        raise ValueError(f"{workload_id}: work_units must be an integer")
    if workload["operation"] == "cpu_parallel":
        if workload["work_units"] <= 0 or target != 0:
            raise ValueError(f"{workload_id}: invalid CPU workload bounds")
    elif workload["work_units"] != 0:
        raise ValueError(f"{workload_id}: invalid memory workload bounds")
    if not isinstance(workload["data_source"], dict) or workload["data_source"].get(
        "type"
    ) != "synthetic":
        raise ValueError(f"{workload_id}: only synthetic data is permitted")
    if workload["expected_minimum_profile"] not in {"small", "medium", "large"}:
        raise ValueError(f"{workload_id}: invalid expected_minimum_profile")
    expected_methods = workload["expected_method_profiles"]
    if workload["evaluation_set"] == "calibration":
        profiles = workload.get("calibration_profiles")
        if (
            not isinstance(profiles, list)
            or not profiles
            or len(profiles) != len(set(profiles))
            or any(profile not in {"small", "medium", "large"} for profile in profiles)
        ):
            raise ValueError(f"{workload_id}: invalid calibration_profiles")
        if expected_methods != {}:
            raise ValueError(f"{workload_id}: calibration cannot define method expectations")
    else:
        if "calibration_profiles" in workload:
            raise ValueError(f"{workload_id}: hold-out cannot define calibration_profiles")
        if set(expected_methods) != {
            "static_default",
            "intent_only",
            "context_aware",
        } or any(
            profile not in {"small", "medium", "large"}
            for profile in expected_methods.values()
        ):
            raise ValueError(f"{workload_id}: invalid expected_method_profiles")
        if not isinstance(workload.get("sentinel_end_to_end"), bool):
            raise ValueError(f"{workload_id}: sentinel_end_to_end must be boolean")


def _read_cgroup_bytes(root: Path, name: str) -> int | None:
    try:
        text = (root / name).read_text(encoding="utf-8").strip()
        return None if text == "max" else int(text)
    except (FileNotFoundError, PermissionError, ValueError):
        return None


def _deep_size(value: Any) -> int:
    seen: set[int] = set()

    def visit(item: Any) -> int:
        identity = id(item)
        if identity in seen:
            return 0
        seen.add(identity)
        size = sys.getsizeof(item)
        if isinstance(item, dict):
            return size + sum(visit(key) + visit(child) for key, child in item.items())
        if isinstance(item, (list, tuple, set)):
            return size + sum(visit(child) for child in item)
        return size

    return visit(value)


def _stream_aggregation(seed: int) -> tuple[dict[str, Any], Any]:
    rng = random.Random(seed)
    buckets = [0.0] * 64
    checksum = 0.0
    for index in range(50_000):
        value = rng.random()
        buckets[index % len(buckets)] += value
        checksum += value * ((index % 17) + 1)
    result = {"rows": 50_000, "bucket_sum": round(sum(buckets), 8), "checksum": round(checksum, 8)}
    return result, buckets


def _table_transform(seed: int) -> tuple[dict[str, Any], Any]:
    rng = random.Random(seed)
    retained = []
    total = 0.0
    for index in range(45_000):
        row = (rng.random(), rng.random(), rng.random(), index % 97)
        if row[0] + row[1] > 0.9:
            transformed = (index, math.log1p(sum(row[:3])), row[3])
            retained.append(transformed)
            total += transformed[1]
    return {"rows": 45_000, "retained": len(retained), "total": round(total, 8)}, retained


def _table_join(seed: int) -> tuple[dict[str, Any], Any]:
    rng = random.Random(seed)
    right = {index: rng.random() for index in range(12_000)}
    joined = []
    total = 0.0
    for index in range(36_000):
        key = index % 12_000
        value = rng.random() * right[key]
        joined.append((key, value))
        total += value
    return {"left_rows": 36_000, "right_rows": 12_000, "total": round(total, 8)}, (right, joined)


def _sort_group(seed: int) -> tuple[dict[str, Any], Any]:
    rng = random.Random(seed)
    rows = [(index % 128, rng.random(), index) for index in range(60_000)]
    rows.sort(key=lambda row: (row[0], row[1]))
    grouped = [0.0] * 128
    for group, value, _ in rows:
        grouped[group] += value
    return {"rows": len(rows), "leader": max(range(128), key=grouped.__getitem__)}, (rows, grouped)


def _encoded_fit(seed: int) -> tuple[dict[str, Any], Any]:
    rng = random.Random(seed)
    weights = [0.0] * 32
    feature_sums = [0.0] * 32
    for iteration in range(4):
        for row in range(20_000):
            label = 1.0 if (row + seed) % 7 < 3 else 0.0
            for feature in range(32):
                value = ((row * (feature + 3)) % 101) / 101.0 + rng.random() * 0.001
                feature_sums[feature] += value
                weights[feature] += (label - 0.5) * value * 0.00001
    return {
        "rows": 20_000,
        "features": 32,
        "iterations": 4,
        "weight_checksum": round(sum(weights), 8),
    }, (weights, feature_sums)


def _materialization(seed: int) -> tuple[dict[str, Any], Any]:
    rng = random.Random(seed)
    values = [(index, rng.random(), (index * 17) % 257) for index in range(50_000)]
    checksum = round(sum(value * (category + 1) for _, value, category in values), 8)
    return {"rows": len(values), "checksum": checksum}, values


def _cpu_worker(seed: int, work_units: int, output: multiprocessing.Queue) -> None:
    mask = (1 << 64) - 1
    value = (seed | 1) & mask
    for unit in range(work_units):
        for index in range(CPU_ITERATIONS_PER_UNIT):
            value ^= (value << 13) & mask
            value ^= value >> 7
            value ^= (value << 17) & mask
            value = (value + index + unit) & mask
    output.put(value)


def _cpu_parallel(seed: int, work_units: int, deadline_seconds: float) -> tuple[dict[str, Any], Any]:
    if work_units <= 0:
        raise ValueError("cpu_parallel requires positive work_units")
    context = multiprocessing.get_context("spawn")
    output: multiprocessing.Queue = context.Queue()
    processes = [
        context.Process(target=_cpu_worker, args=(seed + index * 1_000_003, work_units, output))
        for index in range(CPU_WORKERS)
    ]
    started = time.monotonic()
    for process in processes:
        process.start()
    try:
        for process in processes:
            remaining = deadline_seconds - (time.monotonic() - started)
            process.join(max(0.0, remaining))
        if any(process.is_alive() for process in processes):
            raise TimeoutError("CPU workload exceeded its bounded deadline")
        if any(process.exitcode != 0 for process in processes):
            raise RuntimeError("CPU worker exited unsuccessfully")
        values = [output.get(timeout=2) for _ in processes]
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
            process.join(timeout=1)
        output.close()
    return {
        "workers": CPU_WORKERS,
        "work_units_per_worker": work_units,
        "iterations_per_unit": CPU_ITERATIONS_PER_UNIT,
        "worker_checksums": values,
    }, values


OPERATIONS: dict[str, Callable[..., tuple[dict[str, Any], Any]]] = {
    "stream_aggregation": _stream_aggregation,
    "table_transform": _table_transform,
    "table_join": _table_join,
    "sort_group": _sort_group,
    "encoded_fit": _encoded_fit,
    "materialization": _materialization,
    "cpu_parallel": _cpu_parallel,
}


def _allocate_to_cgroup_target(
    target_mib: int,
    max_allocation_mib: int,
    cgroup_root: Path,
) -> tuple[list[bytearray], int, int, int]:
    if target_mib <= 0:
        current = _read_cgroup_bytes(cgroup_root, "memory.current") or 0
        return [], 0, current, current
    current = _read_cgroup_bytes(cgroup_root, "memory.current")
    if current is None:
        raise RuntimeError("cgroup-v2 memory.current is required for v3 memory workloads")

    target_bytes = target_mib * MIB
    hard_total_bytes = (target_mib + OVERSHOOT_MIB) * MIB
    allocated_cap_bytes = max_allocation_mib * MIB
    blocks: list[bytearray] = []
    allocated = 0
    while current < target_bytes:
        remaining_to_cap = allocated_cap_bytes - allocated
        if remaining_to_cap <= 0:
            raise MemoryError("allocation cap reached before cgroup target")
        block_bytes = min(CHUNK_MIB * MIB, remaining_to_cap)
        block = bytearray(block_bytes)
        for offset in range(0, block_bytes, PAGE_BYTES):
            block[offset] = (len(blocks) + offset // PAGE_BYTES) & 0xFF
        blocks.append(block)
        allocated += block_bytes
        current = _read_cgroup_bytes(cgroup_root, "memory.current")
        if current is None:
            raise RuntimeError("cgroup-v2 memory.current disappeared during allocation")
        if current > hard_total_bytes:
            raise MemoryError("cgroup usage exceeded target + 16 MiB hard cap")
    return blocks, allocated, current, target_bytes


def deterministic_seed(workload_id: str, repeat_index: int, master_seed: int = 20260723) -> int:
    encoded = f"v3|{master_seed}|{workload_id}|{repeat_index}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(encoded).digest()[:4], "big")


def run_workload(
    workload: dict[str, Any],
    seed: int,
    *,
    cgroup_root: Path = CGROUP_ROOT,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    validate_workload(workload)
    started = time.monotonic()
    operation = workload["operation"]
    deadline = float(workload["workload_deadline_seconds"])
    if operation == "cpu_parallel":
        result, retained = _cpu_parallel(seed, int(workload["work_units"]), deadline)
        padding: list[bytearray] = []
        padding_bytes = 0
        cgroup_after = _read_cgroup_bytes(cgroup_root, "memory.current")
    else:
        result, retained = OPERATIONS[operation](seed)
        padding, padding_bytes, cgroup_after, _ = _allocate_to_cgroup_target(
            int(workload["target_cgroup_mib"]),
            int(workload["max_allocation_mib"]),
            cgroup_root,
        )
        if float(workload["hold_seconds"]):
            sleep(float(workload["hold_seconds"]))

    elapsed = time.monotonic() - started
    if elapsed > deadline:
        raise TimeoutError(f"workload exceeded deadline {deadline:g}s")
    useful_bytes = _deep_size(retained)
    deterministic_payload = {
        "schema_version": "3.0.0",
        "workload_id": workload["workload_id"],
        "operation": operation,
        "seed": seed,
        "result": result,
    }
    checksum = hashlib.sha256(
        json.dumps(deterministic_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    # Keep references live until after all accounting fields are captured.
    _ = (retained, padding)
    return {
        **deterministic_payload,
        "synthetic_data": True,
        "data_persisted": False,
        "checksum": checksum,
        "target_cgroup_mib": int(workload["target_cgroup_mib"]),
        "target_band_mib": list(workload["target_band_mib"]),
        "useful_allocation_bytes": useful_bytes,
        "pressure_padding_bytes": padding_bytes,
        "cgroup_memory_after_padding_bytes": cgroup_after,
        "hold_seconds": float(workload["hold_seconds"]),
        "work_units": int(workload["work_units"]),
        "elapsed_seconds": round(elapsed, 6),
        "result": result,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a bounded v3 resource-envelope workload.")
    parser.add_argument("--workload-id")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        manifest = load_manifest(args.manifest)
        if args.validate_only:
            print(
                json.dumps(
                    {
                        "schema_version": manifest["schema_version"],
                        "workloads": len(manifest["workloads"]),
                        "status": "valid",
                    },
                    sort_keys=True,
                )
            )
            return 0
        if not args.workload_id or args.seed is None:
            raise ValueError("--workload-id and --seed are required unless --validate-only is used")
        workload = next(
            (item for item in manifest["workloads"] if item["workload_id"] == args.workload_id),
            None,
        )
        if workload is None:
            raise ValueError(f"unknown workload_id {args.workload_id!r}")
        print(json.dumps(run_workload(workload, args.seed), sort_keys=True), flush=True)
        return 0
    except TimeoutError as exc:
        print(json.dumps({"status": "timeout", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return EXIT_DEADLINE
    except MemoryError as exc:
        print(json.dumps({"status": "memory_failure", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return EXIT_MEMORY_PRESSURE
    except ValueError as exc:
        print(f"v3 workload configuration error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"v3 workload runtime failure: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
