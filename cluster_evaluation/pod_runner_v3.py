"""Container entry point for one v3 resource-envelope workload."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import threading
import time
from typing import Any

from benchmarks.resource_envelope_runner import MANIFEST, run_workload, workloads_by_id


CGROUP_ROOT = Path("/sys/fs/cgroup")


def _read_int(name: str) -> int | None:
    try:
        value = (CGROUP_ROOT / name).read_text(encoding="utf-8").strip()
        return None if value == "max" else int(value)
    except (FileNotFoundError, PermissionError, ValueError):
        return None


def _cpu_stat() -> dict[str, int]:
    try:
        return {
            key: int(value)
            for key, value in (
                line.split(maxsplit=1)
                for line in (CGROUP_ROOT / "cpu.stat").read_text(encoding="utf-8").splitlines()
            )
        }
    except (FileNotFoundError, PermissionError, ValueError):
        return {}


def _delta(after: dict[str, int], before: dict[str, int], key: str) -> int | None:
    if key not in before or key not in after:
        return None
    return after[key] - before[key]


class CgroupSampler:
    def __init__(self, interval_seconds: float) -> None:
        if interval_seconds < 0.05:
            raise ValueError("v3 sampling interval must be at least 50ms")
        self.interval_seconds = interval_seconds
        self.sample_count = 0
        self.cpu_interval_sample_max_m: float | None = None
        self.memory_current_sample_max_bytes: int | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._started_at: float | None = None
        self._cpu_started: dict[str, int] = {}

    def start(self) -> None:
        self._started_at = time.monotonic()
        self._cpu_started = _cpu_stat()
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_seconds * 4))
        stopped = time.monotonic()
        cpu_stopped = _cpu_stat()
        window = stopped - (self._started_at or stopped)
        usage_delta = _delta(cpu_stopped, self._cpu_started, "usage_usec")
        average_m = (
            None
            if usage_delta is None or window <= 0
            else usage_delta / 1_000_000 / window * 1000
        )
        memory_peak = _read_int("memory.peak")
        return {
            "source": "cgroup_v2_in_container",
            "sample_interval_seconds": self.interval_seconds,
            "sample_count": self.sample_count,
            "measurement_window_seconds": round(window, 6),
            "cpu_interval_sample_max_m": (
                None
                if self.cpu_interval_sample_max_m is None
                else round(self.cpu_interval_sample_max_m, 3)
            ),
            "cpu_full_window_average_m": None if average_m is None else round(average_m, 3),
            "cpu_usage_usec_delta": usage_delta,
            "cpu_nr_periods_delta": _delta(cpu_stopped, self._cpu_started, "nr_periods"),
            "cpu_nr_throttled_delta": _delta(cpu_stopped, self._cpu_started, "nr_throttled"),
            "cpu_throttled_usec_delta": _delta(
                cpu_stopped, self._cpu_started, "throttled_usec"
            ),
            "memory_current_sample_max_mib": (
                None
                if self.memory_current_sample_max_bytes is None
                else round(self.memory_current_sample_max_bytes / 1024 / 1024, 3)
            ),
            "peak_memory_mib": (
                None if memory_peak is None else round(memory_peak / 1024 / 1024, 3)
            ),
            "memory_peak_file_available": memory_peak is not None,
        }

    def _loop(self) -> None:
        previous_time = time.monotonic()
        previous_cpu = _cpu_stat().get("usage_usec")
        while not self._stop.wait(self.interval_seconds):
            now = time.monotonic()
            current_cpu = _cpu_stat().get("usage_usec")
            current_memory = _read_int("memory.current")
            if current_memory is not None:
                self.memory_current_sample_max_bytes = max(
                    self.memory_current_sample_max_bytes or 0, current_memory
                )
            if current_cpu is not None and previous_cpu is not None and now > previous_time:
                cpu_m = (current_cpu - previous_cpu) / 1_000_000 / (now - previous_time) * 1000
                self.cpu_interval_sample_max_m = max(
                    self.cpu_interval_sample_max_m or 0.0, cpu_m
                )
            previous_cpu = current_cpu
            previous_time = now
            self.sample_count += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workload-id", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--sample-interval", type=float, default=0.1)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args(argv)

    workload = workloads_by_id(args.manifest).get(args.workload_id)
    if workload is None:
        parser.error(f"unknown workload_id {args.workload_id!r}")
    sampler = CgroupSampler(args.sample_interval)
    sampler.start()
    started = time.monotonic()
    workload_result = run_workload(workload, args.seed)
    cgroup = sampler.stop()
    print(
        json.dumps(
            {
                "pod_runner_schema_version": "3.0.0",
                "workload": workload_result,
                "cgroup_metrics": cgroup,
                "container_elapsed_seconds": round(time.monotonic() - started, 6),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
