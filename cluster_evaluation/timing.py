"""Versioned analysis rules for quantized Kubernetes timestamps."""

from __future__ import annotations

from dataclasses import dataclass
import statistics
from typing import Iterable


TIMING_ANALYSIS_RULE_VERSION = "2.0.0"
KUBERNETES_TIMESTAMP_RESOLUTION_SECONDS = 1.0
TIME_IMPROVEMENT_THRESHOLD = 0.20


@dataclass(frozen=True)
class CensoredDuration:
    observed_seconds: float
    lower_seconds: float
    upper_seconds: float
    resolution_seconds: float


def interval_censored_duration(
    value: float | int | None,
    *,
    resolution_seconds: float = KUBERNETES_TIMESTAMP_RESOLUTION_SECONDS,
) -> CensoredDuration | None:
    """Interpret a duration derived from two timestamps at a known resolution.

    A zero duration is valid. No offset or continuity correction is added. The
    upper bound is exclusive because each source timestamp may have lost less
    than one resolution unit.
    """

    if value is None:
        return None
    observed = float(value)
    if observed < 0:
        raise ValueError(f"duration must be non-negative, got {observed}")
    if resolution_seconds <= 0:
        raise ValueError("timestamp resolution must be positive")
    return CensoredDuration(
        observed_seconds=observed,
        lower_seconds=max(0.0, observed - resolution_seconds),
        upper_seconds=observed + resolution_seconds,
        resolution_seconds=resolution_seconds,
    )


def median_censored_duration(
    values: Iterable[float | int | None],
    *,
    resolution_seconds: float = KUBERNETES_TIMESTAMP_RESOLUTION_SECONDS,
) -> CensoredDuration | None:
    observations = [
        item
        for value in values
        if (item := interval_censored_duration(value, resolution_seconds=resolution_seconds))
        is not None
    ]
    if not observations:
        return None
    return CensoredDuration(
        observed_seconds=statistics.median(item.observed_seconds for item in observations),
        lower_seconds=statistics.median(item.lower_seconds for item in observations),
        upper_seconds=statistics.median(item.upper_seconds for item in observations),
        resolution_seconds=resolution_seconds,
    )


def improvement_is_distinguishable(
    baseline: CensoredDuration | None,
    candidate: CensoredDuration | None,
    *,
    improvement_threshold: float = TIME_IMPROVEMENT_THRESHOLD,
) -> bool:
    """Return true only when every compatible duration clears the threshold."""

    if baseline is None or candidate is None or baseline.lower_seconds <= 0:
        return False
    return candidate.upper_seconds <= (1.0 - improvement_threshold) * baseline.lower_seconds
