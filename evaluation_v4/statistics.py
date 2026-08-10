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


def wilcoxon_signed_rank(
    first: Sequence[float | int | None],
    second: Sequence[float | int | None],
) -> dict[str, Any]:
    """Paired Wilcoxon signed-rank test on common non-null observations."""

    if len(first) != len(second):
        raise ValueError("paired sequences must have equal length")

    pairs: list[tuple[float, float]] = []
    for a, b in zip(first, second):
        if a is not None and b is not None and math.isfinite(float(a)) and math.isfinite(float(b)):
            pairs.append((float(a), float(b)))

    diffs = [a - b for a, b in pairs if abs(a - b) > 1e-12]
    n = len(diffs)
    if n == 0:
        return {
            "pairs": len(pairs),
            "non_zero_pairs": 0,
            "w_positive": 0.0,
            "w_negative": 0.0,
            "statistic": 0.0,
            "z_score": 0.0,
            "p_value_raw": 1.0,
        }

    abs_diffs = [abs(d) for d in diffs]
    sorted_indices = sorted(range(n), key=lambda i: abs_diffs[i])

    # Assign fractional ranks for ties
    ranks = [0.0] * n
    i = 0
    tie_counts: list[int] = []
    while i < n:
        j = i
        while j < n and abs(abs_diffs[sorted_indices[j]] - abs_diffs[sorted_indices[i]]) < 1e-12:
            j += 1
        tie_len = j - i
        tie_counts.append(tie_len)
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[sorted_indices[k]] = avg_rank
        i = j

    w_pos = sum(ranks[idx] for idx, d in enumerate(diffs) if d > 0)
    w_neg = sum(ranks[idx] for idx, d in enumerate(diffs) if d < 0)
    t_stat = min(w_pos, w_neg)

    # For small n (<= 15), calculate exact two-sided p-value by sign permutation
    if n <= 15:
        all_ranks = [ranks[idx] for idx in range(n)]
        target = t_stat
        total_perms = 2**n
        tail_count = 0
        for mask in range(total_perms):
            perm_w_pos = sum(all_ranks[bit] for bit in range(n) if (mask >> bit) & 1)
            perm_w_neg = sum(all_ranks) - perm_w_pos
            if min(perm_w_pos, perm_w_neg) <= target + 1e-9:
                tail_count += 1
        p_val = min(1.0, tail_count / total_perms)
        z_val = 0.0
    else:
        # Asymptotic approximation with continuity and tie correction
        mean_w = n * (n + 1) / 4.0
        var_w = (n * (n + 1) * (2 * n + 1)) / 24.0
        tie_correction = sum(t**3 - t for t in tie_counts) / 48.0
        var_w = max(1e-12, var_w - tie_correction)
        std_w = math.sqrt(var_w)
        diff_from_mean = abs(w_pos - mean_w)
        # Apply continuity correction
        z_val = max(0.0, diff_from_mean - 0.5) / std_w
        # Two-sided standard normal p-value
        p_val = min(1.0, math.erfc(z_val / math.sqrt(2)))

    return {
        "pairs": len(pairs),
        "non_zero_pairs": n,
        "w_positive": round(w_pos, 4),
        "w_negative": round(w_neg, 4),
        "statistic": round(t_stat, 4),
        "z_score": round(z_val, 6),
        "p_value_raw": float(f"{p_val:.12g}"),
    }


def confusion_matrix(
    actual: Sequence[str],
    predicted: Sequence[str | None],
    labels: Sequence[str] = ("small", "medium", "large"),
) -> dict[str, Any]:
    """Compute a multi-class confusion matrix with precision and recall."""

    label_list = list(labels)
    label_indices = {label: idx for idx, label in enumerate(label_list)}
    matrix = [[0 for _ in label_list] for _ in label_list]
    unmapped_count = 0

    for act, pred in zip(actual, predicted):
        if act not in label_indices:
            continue
        row = label_indices[act]
        if pred in label_indices:
            col = label_indices[pred]
            matrix[row][col] += 1
        else:
            unmapped_count += 1

    row_totals = [sum(matrix[r]) for r in range(len(label_list))]
    col_totals = [sum(matrix[r][c] for r in range(len(label_list))) for c in range(len(label_list))]
    total_evaluated = sum(row_totals)
    correct = sum(matrix[i][i] for i in range(len(label_list)))
    accuracy = correct / total_evaluated if total_evaluated > 0 else 0.0

    per_class: dict[str, dict[str, float | int | None]] = {}
    for idx, label in enumerate(label_list):
        rec = matrix[idx][idx] / row_totals[idx] if row_totals[idx] > 0 else None
        prec = matrix[idx][idx] / col_totals[idx] if col_totals[idx] > 0 else None
        per_class[label] = {
            "total_actual": row_totals[idx],
            "total_predicted": col_totals[idx],
            "correct": matrix[idx][idx],
            "recall": round(rec, 6) if rec is not None else None,
            "precision": round(prec, 6) if prec is not None else None,
        }

    return {
        "labels": label_list,
        "matrix": matrix,
        "row_totals": row_totals,
        "col_totals": col_totals,
        "total_evaluated": total_evaluated,
        "unmapped_or_null": unmapped_count,
        "accuracy": round(accuracy, 6),
        "per_class": per_class,
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
    "confusion_matrix",
    "exact_mcnemar",
    "holm_adjust",
    "mean",
    "quantile",
    "wilcoxon_signed_rank",
    "wilson_interval",
]
