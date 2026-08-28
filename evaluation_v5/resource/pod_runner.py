"""In-container entry point for one bounded E4 workload trial."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import threading
import time
from typing import Any

from .manifest import DEFAULT_MANIFEST, load_resource_manifest, workloads_by_id
from .workloads import execute_workload


CGROUP_ROOT = Path("/sys/fs/cgroup")
POD_SCHEMA_VERSION = "protocol-v5-resource-pod-result-v1.0.0"


def _read_text(name: str) -> str | None:
    try:
        return (CGROUP_ROOT / name).read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError):
        return None


def _read_int(name: str) -> int | None:
    value = _read_text(name)
    try:
        return None if value in {None, "max"} else int(value)
    except ValueError:
        return None


def _key_values(name: str) -> dict[str, int]:
    value = _read_text(name)
    if value is None:
        return {}
    parsed: dict[str, int] = {}
    try:
        for line in value.splitlines():
            key, raw = line.split(maxsplit=1)
            parsed[key] = int(raw)
    except ValueError:
        return {}
    return parsed


def _delta(after: dict[str, int], before: dict[str, int], key: str) -> int | None:
    if key not in before or key not in after:
        return None
    return after[key] - before[key]


class CgroupSampler:
    def __init__(self, interval_seconds: float = 0.1) -> None:
        if interval_seconds < 0.05:
            raise ValueError("cgroup sample interval must be at least 50ms")
        self.interval_seconds = interval_seconds
        self.sample_count = 0
        self.memory_sample_max = _read_int("memory.current")
        self.cpu_sample_max_m: float | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._cpu_before = _key_values("cpu.stat")
        self._memory_events_before = _key_values("memory.events")
        self._started = time.monotonic()

    def start(self) -> None:
        self._thread.start()

    def _loop(self) -> None:
        previous_at = time.monotonic()
        previous_cpu = _key_values("cpu.stat").get("usage_usec")
        while not self._stop.wait(self.interval_seconds):
            now = time.monotonic()
            current_cpu = _key_values("cpu.stat").get("usage_usec")
            current_memory = _read_int("memory.current")
            if current_memory is not None:
                self.memory_sample_max = max(self.memory_sample_max or 0, current_memory)
            if current_cpu is not None and previous_cpu is not None and now > previous_at:
                cpu_m = (current_cpu - previous_cpu) / 1_000_000 / (now - previous_at) * 1000
                self.cpu_sample_max_m = max(self.cpu_sample_max_m or 0.0, cpu_m)
            previous_at, previous_cpu = now, current_cpu
            self.sample_count += 1

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self._thread.join(timeout=1)
        stopped = time.monotonic()
        cpu_after = _key_values("cpu.stat")
        events_after = _key_values("memory.events")
        peak = _read_int("memory.peak")
        usage = _delta(cpu_after, self._cpu_before, "usage_usec")
        window = stopped - self._started
        return {
            "source": "cgroup_v2_in_container",
            "cgroup_version": "v2" if _read_text("cgroup.controllers") is not None else None,
            "controllers": (_read_text("cgroup.controllers") or "").split(),
            "cpu_max": _read_text("cpu.max"),
            "memory_max": _read_text("memory.max"),
            "memory_high": _read_text("memory.high"),
            "sample_interval_seconds": self.interval_seconds,
            "sample_count": self.sample_count,
            "memory_peak_mib": None if peak is None else round(peak / 1024 / 1024, 6),
            "memory_current_sample_max_mib": None if self.memory_sample_max is None else round(self.memory_sample_max / 1024 / 1024, 6),
            "cpu_full_window_average_m": None if usage is None or window <= 0 else round(usage / 1_000_000 / window * 1000, 6),
            "cpu_interval_sample_max_m": None if self.cpu_sample_max_m is None else round(self.cpu_sample_max_m, 6),
            "cpu_usage_usec_delta": usage,
            "cpu_nr_periods_delta": _delta(cpu_after, self._cpu_before, "nr_periods"),
            "cpu_nr_throttled_delta": _delta(cpu_after, self._cpu_before, "nr_throttled"),
            "cpu_throttled_usec_delta": _delta(cpu_after, self._cpu_before, "throttled_usec"),
            "memory_events_delta": {
                key: _delta(events_after, self._memory_events_before, key)
                for key in sorted(set(events_after) | set(self._memory_events_before))
            },
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--family-id", required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--sample-interval", type=float, default=0.1)
    args = parser.parse_args(argv)
    manifest = load_resource_manifest(args.manifest)
    workload = workloads_by_id(manifest).get(args.family_id)
    if workload is None:
        parser.error(f"unknown family_id {args.family_id!r}")
    sampler = CgroupSampler(args.sample_interval)
    started = time.monotonic()
    sampler.start()
    result = execute_workload(workload)
    metrics = sampler.stop()
    elapsed = time.monotonic() - started
    marker_ok = result.marker_sha256 == workload["expected_marker_sha256"]
    correctness_ok = marker_ok and result.correctness_invariants_ok
    print(json.dumps({
        "schema_version": POD_SCHEMA_VERSION,
        "family_id": workload["family_id"],
        "runtime_seconds": round(elapsed, 6),
        "expected_marker_sha256": workload["expected_marker_sha256"],
        "observed_marker_sha256": result.marker_sha256,
        "correctness_marker_ok": marker_ok,
        "correctness_invariants_ok": result.correctness_invariants_ok,
        "correctness_details": result.correctness_details,
        "workload": result.to_dict(),
        "cgroup_metrics": metrics,
    }, sort_keys=True), flush=True)
    return 0 if correctness_ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
