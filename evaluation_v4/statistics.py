"""Small, dependency-free statistical helpers for protocol-v4 analysis."""

from __future__ import annotations

from collections import defaultdict
import math
import random
from typing import Any, Callable, Iterable, Mapping, Sequence


def mean(values: Iterable[float | int | bool | None]) -> float | None:
    selected = [float(value) for value in values if value is not None]
    return sum(selected) / len(selected) if selected else None


def quantile(values: Iterable[float | int], probability: float) -> float | None:
    selected = sorted(float(value) for value in values)
    if not selected:
        return None
    if not 0 <= probability <= 1:
        raise ValueError("probability must be between zero and one")
    position = (len(selected) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return selected[lower]
    fraction = position - lower
    return selected[lower] * (1 - fraction) + selected[upper] * fraction


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half_width = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return max(0.0, center - half_width), min(1.0, center + half_width)


def cluster_bootstrap_ci(
    rows: Sequence[Mapping[str, Any]],
    metric: Callable[[Sequence[Mapping[str, Any]]], float | None],
    *,
    cluster_field: str = "workload_family",
    replicates: int = 2000,
    seed: int = 20260808,
) -> tuple[float | None, float | None]:
    """Percentile CI resampling workload families, not correlated variants."""

    if replicates < 1:
        raise ValueError("bootstrap replicates must be >= 1")
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[cluster_field])].append(row)
    clusters = sorted(grouped)
    if len(clusters) < 2:
        return None, None
    generator = random.Random(seed)
    estimates: list[float] = []
    for _ in range(replicates):
        sampled_rows: list[Mapping[str, Any]] = []
        for _index in range(len(clusters)):
            sampled_rows.extend(grouped[generator.choice(clusters)])
        estimate = metric(sampled_rows)
        if estimate is not None and math.isfinite(estimate):
            estimates.append(float(estimate))
    if not estimates:
        return None, None
    return quantile(estimates, 0.025), quantile(estimates, 0.975)


def exact_mcnemar(first: Sequence[bool], second: Sequence[bool]) -> dict[str, int | float]:
    if len(first) != len(second):
        raise ValueError("paired outcomes must have equal length")
    first_only = sum(a and not b for a, b in zip(first, second))
    second_only = sum(b and not a for a, b in zip(first, second))
    discordant = first_only + second_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(
            math.comb(discordant, k) * (0.5**discordant)
            for k in range(0, min(first_only, second_only) + 1)
        )
        p_value = min(1.0, 2 * tail)
    return {
        "first_only_correct": first_only,
        "second_only_correct": second_only,
        "discordant_pairs": discordant,
        "p_value_raw": p_value,
    }


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Return Holm step-down family-wise-error adjusted p-values."""

    count = len(p_values)
    order = sorted(range(count), key=lambda index: p_values[index])
    adjusted = [1.0] * count
    running = 0.0
    for rank, index in enumerate(order):
        candidate = min(1.0, (count - rank) * float(p_values[index]))
        running = max(running, candidate)
        adjusted[index] = running
    return adjusted


__all__ = [
    "cluster_bootstrap_ci",
    "exact_mcnemar",
    "holm_adjust",
    "mean",
    "quantile",
    "wilson_interval",
]
