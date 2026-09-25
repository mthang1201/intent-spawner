"""Progress reporting and heartbeat logging utilities for Protocol-v5 E5."""

from __future__ import annotations

from datetime import datetime, timezone
import sys
import threading
import time
from typing import Any


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_bytes(num_bytes: int | float) -> str:
    """Format byte counts into human-readable strings (KB, MB, GB)."""
    val = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(val) < 1024.0 or unit == "TB":
            return f"{val:.2f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.2f} B"


def format_progress_bar(current: int, total: int, width: int = 20) -> str:
    """Render a text progress bar: [=========>          ] 5/10 (50%)."""
    if total <= 0:
        return f"[{current}]"
    ratio = min(max(current / total, 0.0), 1.0)
    filled = int(round(ratio * width))
    bar = "=" * max(0, filled - 1) + (">" if filled > 0 else "")
    bar = bar.ljust(width, " ")
    percent = int(ratio * 100)
    return f"[{bar}] {current}/{total} ({percent}%)"


def progress_log(message: str, *, prefix: str = "E5", file: Any = None) -> None:
    """Output an immediately flushed timestamped log message."""
    target = file if file is not None else sys.stdout
    print(f"[{_timestamp()}] [{prefix}] {message}", file=target, flush=True)


class ProgressHeartbeat:
    """Context manager that periodically logs a heartbeat during long operations.

    Guarantees that long-running operations (such as container image pulls or
    multi-megabyte registry manifest inspections) will not leave the terminal
    silent for more than `interval_seconds`.
    """

    def __init__(
        self,
        task_description: str,
        interval_seconds: float = 20.0,
        prefix: str = "E5:Heartbeat",
    ) -> None:
        self.task_description = task_description
        self.interval_seconds = interval_seconds
        self.prefix = prefix
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_time = 0.0

    def __enter__(self) -> ProgressHeartbeat:
        self._start_time = time.monotonic()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        return False

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            elapsed = time.monotonic() - self._start_time
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            elapsed_str = f"{mins}m{secs:02d}s" if mins > 0 else f"{secs}s"
            progress_log(
                f"Still in progress: {self.task_description} (elapsed: {elapsed_str})...",
                prefix=self.prefix,
            )
