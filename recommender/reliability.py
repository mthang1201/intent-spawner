"""Bounded async execution and low-cardinality recommender telemetry."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import math
import threading
import time

from .base import Recommender
from .models import RecommendationRequest, SpawnRecommendation


DEFAULT_MAX_CONCURRENT_NETWORK_RECOMMENDATIONS = 4
MAX_CONCURRENT_NETWORK_RECOMMENDATIONS = 64
MAX_FALLBACK_RESERVE_SECONDS = 0.05
FALLBACK_RESERVE_FRACTION = 0.1


def network_work_deadline(started: float, total_deadline: float) -> float:
    """Reserve a small part of the total budget for fallback and serialization."""

    available = max(0.0, total_deadline - started)
    reserve = min(MAX_FALLBACK_RESERVE_SECONDS, available * FALLBACK_RESERVE_FRACTION)
    return total_deadline - reserve


@dataclass(frozen=True)
class RecommendationMetadata:
    """Safe, bounded metadata for logs, previews, audits, and annotations."""

    requested_backend: str
    effective_backend: str
    fallback_used: bool
    fallback_error_category: str | None
    attempt_count: int
    total_elapsed_seconds: float
    timed_out: bool
    deadline_exhausted: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "requested_backend": self.requested_backend,
            "effective_backend": self.effective_backend,
            "fallback_used": self.fallback_used,
            "fallback_error_category": self.fallback_error_category,
            "attempt_count": self.attempt_count,
            "total_elapsed_seconds": round(self.total_elapsed_seconds, 6),
            "timed_out": self.timed_out,
            "deadline_exhausted": self.deadline_exhausted,
        }


@dataclass(frozen=True)
class RecommendationResult:
    """Internal result that keeps telemetry out of the public recommendation schema."""

    recommendation: SpawnRecommendation
    metadata: RecommendationMetadata


class RecommendationCallState:
    """Thread-safe progress snapshot used when an async caller reaches its deadline."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempt_count = 0

    def mark_attempt(self, attempt_count: int) -> None:
        with self._lock:
            self._attempt_count = attempt_count

    @property
    def attempt_count(self) -> int:
        with self._lock:
            return self._attempt_count


def _validate_max_concurrency(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("maximum concurrent network recommendations must be an integer")
    if not 1 <= value <= MAX_CONCURRENT_NETWORK_RECOMMENDATIONS:
        raise ValueError(
            "maximum concurrent network recommendations must be between 1 and "
            f"{MAX_CONCURRENT_NETWORK_RECOMMENDATIONS}"
        )
    return value


def recommend_with_metadata(
    recommender: Recommender,
    request: RecommendationRequest,
    *,
    deadline: float | None = None,
    state: RecommendationCallState | None = None,
) -> RecommendationResult:
    """Use an extended backend method when present, otherwise adapt the legacy API."""

    extended = getattr(recommender, "recommend_with_metadata", None)
    if callable(extended):
        return extended(request, deadline=deadline, state=state)

    started = time.monotonic()
    recommendation = recommender.recommend(request)
    if not isinstance(recommendation, SpawnRecommendation):
        raise TypeError("configured backend returned an invalid recommendation type")
    requested_backend = getattr(recommender, "backend_name", recommendation.backend_name)
    return RecommendationResult(
        recommendation=recommendation,
        metadata=RecommendationMetadata(
            requested_backend=requested_backend,
            effective_backend=recommendation.backend_name,
            fallback_used=requested_backend != recommendation.backend_name,
            fallback_error_category=None,
            attempt_count=0,
            total_elapsed_seconds=max(0.0, time.monotonic() - started),
            timed_out=False,
            deadline_exhausted=False,
        ),
    )


class AsyncRecommendationExecutor:
    """Run only network recommenders in a bounded worker pool.

    Admission happens before submission, so the executor never accumulates an
    unbounded queue. If the awaiting coroutine is cancelled, its permit remains
    held until the worker finishes; abandoned work therefore cannot exceed the
    configured concurrency bound.
    """

    def __init__(
        self,
        max_concurrent_network_recommendations: int = (
            DEFAULT_MAX_CONCURRENT_NETWORK_RECOMMENDATIONS
        ),
    ) -> None:
        maximum = _validate_max_concurrency(max_concurrent_network_recommendations)
        self.max_concurrent_network_recommendations = maximum
        self._semaphore = asyncio.Semaphore(maximum)
        self._executor = ThreadPoolExecutor(
            max_workers=maximum,
            thread_name_prefix="recommender-network",
        )

    @staticmethod
    def _total_timeout(recommender: Recommender) -> float:
        config = getattr(recommender, "config", None)
        value = getattr(config, "total_timeout", None)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("network recommender total timeout is not configured")
        timeout = float(value)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("network recommender total timeout must be positive")
        return timeout

    @staticmethod
    def _fallback_after_deadline(
        recommender: Recommender,
        request: RecommendationRequest,
        *,
        started: float,
        state: RecommendationCallState,
    ) -> RecommendationResult:
        fallback = getattr(recommender, "fallback_result", None)
        if not callable(fallback):
            raise TimeoutError("recommendation deadline exhausted")
        return fallback(
            request,
            error_category="deadline_exhausted",
            attempt_count=state.attempt_count,
            started=started,
            timed_out=True,
            deadline_exhausted=True,
        )

    async def recommend(
        self,
        recommender: Recommender,
        request: RecommendationRequest,
    ) -> RecommendationResult:
        if not getattr(recommender, "network_bound", False):
            return recommend_with_metadata(recommender, request)

        total_timeout = self._total_timeout(recommender)
        loop = asyncio.get_running_loop()
        started = time.monotonic()
        total_deadline = started + total_timeout
        deadline = network_work_deadline(started, total_deadline)
        state = RecommendationCallState()

        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=max(0.0, deadline - started)
            )
        except asyncio.TimeoutError:
            return self._fallback_after_deadline(
                recommender, request, started=started, state=state
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self._semaphore.release()
            return self._fallback_after_deadline(
                recommender, request, started=started, state=state
            )

        concurrent_future: Future[RecommendationResult] = self._executor.submit(
            recommend_with_metadata,
            recommender,
            request,
            deadline=total_deadline,
            state=state,
        )
        wrapped_future = asyncio.wrap_future(concurrent_future, loop=loop)
        released = False

        def release_when_done(_future: object) -> None:
            nonlocal released
            if released:
                return
            released = True
            self._semaphore.release()
            # Consume an exception after cancellation/timeout to avoid a noisy
            # "Future exception was never retrieved" warning.
            if wrapped_future.done() and not wrapped_future.cancelled():
                wrapped_future.exception()

        wrapped_future.add_done_callback(release_when_done)
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped_future), timeout=remaining)
        except asyncio.TimeoutError:
            return self._fallback_after_deadline(
                recommender, request, started=started, state=state
            )
        except asyncio.CancelledError:
            # shield() keeps the bounded worker alive. Its permit is released by
            # the callback only after the per-call deadline stops the work.
            raise

    def shutdown(self, *, wait: bool = True) -> None:
        """Release worker threads. Primarily useful for deterministic tests."""

        self._executor.shutdown(wait=wait, cancel_futures=True)


__all__ = [
    "AsyncRecommendationExecutor",
    "DEFAULT_MAX_CONCURRENT_NETWORK_RECOMMENDATIONS",
    "MAX_CONCURRENT_NETWORK_RECOMMENDATIONS",
    "RecommendationCallState",
    "RecommendationMetadata",
    "RecommendationResult",
    "network_work_deadline",
    "recommend_with_metadata",
]
