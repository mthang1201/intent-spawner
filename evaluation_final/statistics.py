"""Dependency-light descriptive and paired statistics for final evaluation."""

from __future__ import annotations

from collections import defaultdict
import math
import random
import statistics
from typing import Any, Callable, Mapping, Sequence


STATISTICS_VERSION = "final-evaluation-statistics-v1.0.0"


def safe_rate(numerator: int | float, denominator: int) -> float | None:
    return float(numerator) / denominator if denominator else None


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not 0 <= fraction <= 1:
        raise ValueError("percentile fraction must be between zero and one")
    selected = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not selected:
        return None
    if len(selected) == 1:
        return selected[0]
    position = (len(selected) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return selected[lower]
    weight = position - lower
    return selected[lower] * (1.0 - weight) + selected[upper] * weight


def distribution(values: Sequence[float | int | None]) -> dict[str, Any]:
    selected = [
        float(value)
        for value in values
        if value is not None and math.isfinite(float(value))
    ]
    return {
        "count": len(selected),
        "mean": statistics.fmean(selected) if selected else None,
        "median": statistics.median(selected) if selected else None,
        "p50": percentile(selected, 0.50),
        "p95": percentile(selected, 0.95),
        "minimum": min(selected) if selected else None,
        "maximum": max(selected) if selected else None,
    }


def exact_mcnemar(first: Sequence[bool], second: Sequence[bool]) -> dict[str, Any]:
    if len(first) != len(second):
        raise ValueError("paired outcomes must have equal length")
    first_only = sum(a and not b for a, b in zip(first, second))
    second_only = sum(b and not a for a, b in zip(first, second))
    discordant = first_only + second_only
    if not discordant:
        p_value = 1.0
    else:
        tail = sum(
            math.comb(discordant, k) * (0.5**discordant)
            for k in range(min(first_only, second_only) + 1)
        )
        p_value = min(1.0, 2.0 * tail)
    return {
        "first_only_success": first_only,
        "second_only_success": second_only,
        "discordant_pairs": discordant,
        "p_value_two_sided_exact": p_value,
    }


def cluster_bootstrap_mean_ci(
    rows: Sequence[Mapping[str, Any]],
    value: Callable[[Mapping[str, Any]], float | int | None],
    *,
    cluster_field: str,
    replicates: int = 2000,
    seed: int = 20260822,
) -> tuple[float | None, float | None]:
    """Percentile CI resampling the declared independent cluster unit."""

    if replicates < 1:
        raise ValueError("bootstrap replicates must be positive")
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if cluster_field not in row:
            raise ValueError(f"bootstrap row is missing cluster field {cluster_field!r}")
        grouped[str(row[cluster_field])].append(row)
    clusters = sorted(grouped)
    if len(clusters) < 2:
        return None, None
    generator = random.Random(seed)
    estimates: list[float] = []
    for _ in range(replicates):
        sampled: list[Mapping[str, Any]] = []
        for _index in clusters:
            sampled.extend(grouped[generator.choice(clusters)])
        values = [
            float(observed)
            for row in sampled
            if (observed := value(row)) is not None
            and math.isfinite(float(observed))
        ]
        if values:
            estimates.append(statistics.fmean(values))
    return percentile(estimates, 0.025), percentile(estimates, 0.975)


__all__ = [
    "STATISTICS_VERSION",
    "cluster_bootstrap_mean_ci",
    "distribution",
    "exact_mcnemar",
    "percentile",
    "safe_rate",
]
