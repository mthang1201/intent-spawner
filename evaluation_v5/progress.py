"""Universal real-time progress tracker and heartbeat logger for evaluation suites."""

from __future__ import annotations

from datetime import datetime, timezone
import sys
import threading
import time
from typing import Any


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def format_duration(seconds: float) -> str:
    """Format duration into MM:SS or HH:MM:SS."""
    val = max(0, int(seconds))
    mins, secs = divmod(val, 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}" if hours > 0 else f"{mins:02d}:{secs:02d}"


class ProgressTracker:
    """Real-time progress tracker with progress bar, ETA, and automatic heartbeat."""

    def __init__(
        self,
        total: int,
        description: str = "Evaluating",
        width: int = 20,
        heartbeat_interval: float = 30.0,
    ) -> None:
        self.total = max(1, total)
        self.description = description
        self.width = width
        self.heartbeat_interval = heartbeat_interval
        self.current = 0
        self.start_time = time.monotonic()
        self.last_log_time = self.start_time
        self.is_tty = sys.stderr.isatty()
        self._current_item_desc = ""
        self._item_start_time = self.start_time

        # Background heartbeat thread
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()

    def update_item_start(self, item_desc: str) -> None:
        """Call right before starting work on an item."""
        self._current_item_desc = item_desc
        self._item_start_time = time.monotonic()

    def advance(self, step: int = 1, item_desc: str = "", extra_info: str = "") -> None:
        """Advance progress and print status."""
        self.current = min(self.total, self.current + step)
        now = time.monotonic()
        elapsed = now - self.start_time
        pct = (self.current / self.total) * 100.0

        # ETA
        avg_time = elapsed / self.current if self.current > 0 else 0.0
        remaining = self.total - self.current
        eta = remaining * avg_time

        ratio = min(max(self.current / self.total, 0.0), 1.0)
        filled = int(round(ratio * self.width))
        bar = "=" * max(0, filled - 1) + (">" if filled > 0 and filled < self.width else "")
        bar = bar.ljust(self.width, " ")
        if filled >= self.width:
            bar = "=" * self.width

        line = (
            f"[{bar}] {self.current:3d}/{self.total} ({pct:5.1f}%) | "
            f"Elapsed: {format_duration(elapsed)} | ETA: {format_duration(eta)}"
        )
        if item_desc:
            line += f" | {item_desc}"
        if extra_info:
            line += f" | {extra_info}"

        time_since_log = now - self.last_log_time
        should_log_non_tty = (
            self.current == 1
            or self.current == self.total
            or (self.current % 10 == 0)
            or (time_since_log >= 30.0)
        )

        if self.is_tty:
            sys.stderr.write(f"\r{line}")
            sys.stderr.flush()
            if self.current % max(1, self.total // 5) == 0 or self.current == self.total:
                sys.stderr.write("\n")
                sys.stderr.flush()
        elif should_log_non_tty:
            print(f"[{_timestamp()}] {line}", file=sys.stderr, flush=True)
            self.last_log_time = now

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.wait(self.heartbeat_interval):
            now = time.monotonic()
            item_elapsed = now - self._item_start_time
            if item_elapsed >= self.heartbeat_interval and self.current < self.total:
                desc = self._current_item_desc or self.description
                msg = f"\n  ⏳ [{_timestamp()}] Still processing: {desc} (current item: {format_duration(item_elapsed)}, overall: {format_duration(now - self.start_time)})..."
                sys.stderr.write(msg + "\n")
                sys.stderr.flush()

    def close(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=0.5)
        if self.is_tty:
            sys.stderr.write("\n")
            sys.stderr.flush()

    def __enter__(self) -> ProgressTracker:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self.close()
        return False
