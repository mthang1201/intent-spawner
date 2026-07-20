"""Run one benchmark inside a pod while sampling its cgroup-v2 usage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import threading
import time
from typing import Any

from benchmarks.workload_runner import run_workload


CGROUP_ROOT = Path("/sys/fs/cgroup")


def _read_int(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
        return None if value == "max" else int(value)
    except (FileNotFoundError, PermissionError, ValueError):
        return None


def _cpu_usage_usec() -> int | None:
    try:
        fields = {
            key: int(value)
            for key, value in (
                line.split(maxsplit=1)
                for line in (CGROUP_ROOT / "cpu.stat").read_text(encoding="utf-8").splitlines()
            )
        }
    except (FileNotFoundError, PermissionError, ValueError):
        return None
    return fields.get("usage_usec")


class CgroupSampler:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self.peak_cpu_m: float | None = None
        self.peak_memory_bytes: int | None = None
        self.sample_count = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._started_at: float | None = None
        self._started_cpu_usec: int | None = None

    def start(self) -> None:
        self._started_at = time.monotonic()
        self._started_cpu_usec = _cpu_usage_usec()
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_seconds * 4))
        stopped_at = time.monotonic()
        stopped_cpu_usec = _cpu_usage_usec()
        full_window_average_cpu_m = None
        if (
            self._started_at is not None
            and self._started_cpu_usec is not None
            and stopped_cpu_usec is not None
            and stopped_at > self._started_at
        ):
            full_window_average_cpu_m = (
                ((stopped_cpu_usec - self._started_cpu_usec) / 1_000_000)
                / (stopped_at - self._started_at)
                * 1000
            )
        memory_peak = _read_int(CGROUP_ROOT / "memory.peak")
        if memory_peak is not None:
            self.peak_memory_bytes = max(self.peak_memory_bytes or 0, memory_peak)
        return {
            "source": "cgroup_v2_in_container",
            "sample_interval_seconds": self.interval_seconds,
            "sample_count": self.sample_count,
            "peak_cpu_m": None if self.peak_cpu_m is None else round(self.peak_cpu_m, 3),
            "full_window_average_cpu_m": (
                None
                if full_window_average_cpu_m is None
                else round(full_window_average_cpu_m, 3)
            ),
            "peak_memory_mi": (
                None
                if self.peak_memory_bytes is None
                else round(self.peak_memory_bytes / 1024 / 1024, 3)
            ),
            "memory_peak_file_available": memory_peak is not None,
        }

    def _sample_loop(self) -> None:
        previous_time = time.monotonic()
        previous_cpu = _cpu_usage_usec()
        while not self._stop.wait(self.interval_seconds):
            now = time.monotonic()
            cpu = _cpu_usage_usec()
            memory = _read_int(CGROUP_ROOT / "memory.current")
            if memory is not None:
                self.peak_memory_bytes = max(self.peak_memory_bytes or 0, memory)
            if cpu is not None and previous_cpu is not None and now > previous_time:
                cpu_m = ((cpu - previous_cpu) / 1_000_000) / (now - previous_time) * 1000
                self.peak_cpu_m = max(self.peak_cpu_m or 0.0, cpu_m)
            if cpu is not None:
                previous_cpu = cpu
            previous_time = now
            self.sample_count += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workload-id", required=True)
    parser.add_argument("--scale", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--sample-interval", type=float, default=0.01)
    parser.add_argument("--hold-seconds", type=float, default=0.0)
    args = parser.parse_args()

    sampler = CgroupSampler(args.sample_interval)
    started = time.monotonic()
    sampler.start()
    workload = run_workload(args.workload_id, args.scale, args.seed)
    workload_finished = time.monotonic()
    if args.hold_seconds > 0:
        time.sleep(args.hold_seconds)
    cgroup = sampler.stop()
    payload = {
        "pod_runner_schema_version": "1.0.0",
        "workload": workload,
        "cgroup_metrics": cgroup,
        "workload_elapsed_seconds": round(workload_finished - started, 6),
        "container_elapsed_seconds": round(time.monotonic() - started, 6),
        "hold_seconds": args.hold_seconds,
    }
    print(json.dumps(payload, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
