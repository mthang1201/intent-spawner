"""In-container measurement wrapper for protocol-v4 system trials.

The bounded workload remains the frozen protocol-v3 implementation.  This
wrapper adds the time-window CPU and memory statistics required by protocol v4
without changing the workload's demand, seed, or safety caps.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import threading
import time
from typing import Any

from benchmarks.resource_envelope_runner import MANIFEST, run_workload, workloads_by_id


CGROUP_ROOT = Path("/sys/fs/cgroup")


def _read_int(root: Path, name: str) -> int | None:
    try:
        value = (root / name).read_text(encoding="utf-8").strip()
        return None if value == "max" else int(value)
    except (FileNotFoundError, PermissionError, ValueError):
        return None


def _cpu_stat(root: Path) -> dict[str, int]:
    try:
        return {
            key: int(value)
            for key, value in (
                line.split(maxsplit=1)
                for line in (root / "cpu.stat").read_text(encoding="utf-8").splitlines()
            )
        }
    except (FileNotFoundError, PermissionError, ValueError):
        return {}


def _delta(after: dict[str, int], before: dict[str, int], key: str) -> int | None:
    if key not in before or key not in after:
        return None
    return after[key] - before[key]


class CgroupWindowSampler:
    """Collect pure full-window means plus diagnostic sample maxima."""

    def __init__(self, interval_seconds: float, cgroup_root: Path = CGROUP_ROOT) -> None:
        if interval_seconds < 0.05:
            raise ValueError("v4 sampling interval must be at least 50ms")
        self.interval_seconds = interval_seconds
        self.cgroup_root = cgroup_root
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._started_at: float | None = None
        self._cpu_started: dict[str, int] = {}
        self._memory_samples: list[int] = []
        self.cpu_interval_sample_max_m: float | None = None

    def _sample_memory(self) -> None:
        current = _read_int(self.cgroup_root, "memory.current")
        if current is not None:
            self._memory_samples.append(current)

    def start(self) -> None:
        self._started_at = time.monotonic()
        self._cpu_started = _cpu_stat(self.cgroup_root)
        self._sample_memory()
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_seconds * 4))
        self._sample_memory()
        stopped = time.monotonic()
        cpu_stopped = _cpu_stat(self.cgroup_root)
        window = stopped - (self._started_at or stopped)
        usage_delta = _delta(cpu_stopped, self._cpu_started, "usage_usec")
        cpu_mean_m = (
            None
            if usage_delta is None or window <= 0
            else usage_delta / 1_000_000 / window * 1000
        )
        memory_peak = _read_int(self.cgroup_root, "memory.peak")
        memory_mean = (
            sum(self._memory_samples) / len(self._memory_samples)
            if self._memory_samples
            else None
        )
        return {
            "source": "cgroup_v2_in_container_window",
            "sample_interval_seconds": self.interval_seconds,
            "sample_count": len(self._memory_samples),
            "measurement_window_seconds": round(window, 6),
            "cpu_usage_mean_m": None if cpu_mean_m is None else round(cpu_mean_m, 3),
            "cpu_interval_sample_max_m": (
                None
                if self.cpu_interval_sample_max_m is None
                else round(self.cpu_interval_sample_max_m, 3)
            ),
            "cpu_usage_usec_delta": usage_delta,
            "cpu_nr_periods_delta": _delta(cpu_stopped, self._cpu_started, "nr_periods"),
            "cpu_nr_throttled_delta": _delta(
                cpu_stopped, self._cpu_started, "nr_throttled"
            ),
            "cpu_throttled_usec_delta": _delta(
                cpu_stopped, self._cpu_started, "throttled_usec"
            ),
            "memory_usage_mean_mib": (
                None if memory_mean is None else round(memory_mean / 2**20, 3)
            ),
            "memory_usage_sample_max_mib": (
                None
                if not self._memory_samples
                else round(max(self._memory_samples) / 2**20, 3)
            ),
            "memory_usage_peak_mib": (
                None if memory_peak is None else round(memory_peak / 2**20, 3)
            ),
            "memory_peak_file_available": memory_peak is not None,
        }

    def _loop(self) -> None:
        previous_time = time.monotonic()
        previous_cpu = _cpu_stat(self.cgroup_root).get("usage_usec")
        while not self._stop.wait(self.interval_seconds):
            now = time.monotonic()
            current_cpu = _cpu_stat(self.cgroup_root).get("usage_usec")
            self._sample_memory()
            if current_cpu is not None and previous_cpu is not None and now > previous_time:
                cpu_m = (
                    (current_cpu - previous_cpu)
                    / 1_000_000
                    / (now - previous_time)
                    * 1000
                )
                self.cpu_interval_sample_max_m = max(
                    self.cpu_interval_sample_max_m or 0.0, cpu_m
                )
            previous_cpu = current_cpu
            previous_time = now


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded v4 system workload.")
    parser.add_argument("--workload-id", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--sample-interval", type=float, default=0.1)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args(argv)

    workload = workloads_by_id(args.manifest).get(args.workload_id)
    if workload is None:
        parser.error(f"unknown workload_id {args.workload_id!r}")
    sampler = CgroupWindowSampler(args.sample_interval)
    sampler.start()
    started = time.monotonic()
    workload_result = run_workload(workload, args.seed)
    metrics = sampler.stop()
    print(
        json.dumps(
            {
                "pod_runner_schema_version": "4.0.0",
                "workload": workload_result,
                "cgroup_metrics": metrics,
                "container_elapsed_seconds": round(time.monotonic() - started, 6),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
