"""Family-level statistical helpers for Protocol-v5 analyses.

The independent unit in Protocol-v5 is the workload family.  Callers must
therefore collapse executions to variants and variants to families before
using this module.  The bootstrap helpers enforce one row per family so that
an accidental repeat- or variant-level analysis fails closed.

Where the Protocol-v4 implementation remains valid, this module delegates to
its dependency-free, tested helpers.  Protocol-v5 comparisons consistently
use the direction ``second - first`` (for example, ``P2 - P1``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from typing import Any

from evaluation_v4.statistics import (
    cluster_bootstrap_ci as _v4_cluster_bootstrap_ci,
    exact_mcnemar,
    holm_adjust,
    mean,
    paired_difference_cluster_bootstrap_ci as _v4_paired_difference_bootstrap_ci,
    quantile,
    wilcoxon_signed_rank,
)


STATISTICS_SCHEMA_VERSION = "protocol-v5-statistics-v1.0.0"
PROTOCOL_VERSION = "5.0.0"

DEFAULT_BOOTSTRAP_REPLICATES = 2_000
DEFAULT_BOOTSTRAP_SEED = 20_260_824
DEFAULT_CONFIDENCE_LEVEL = 0.95
DEFAULT_ALPHA = 0.05

MIN_BOOTSTRAP_FAMILY_N = 2
MIN_PAIRED_DECISION_FAMILY_N = 10
SMALL_EFFECTIVE_FAMILY_N_THRESHOLD = 20

SMALL_EFFECTIVE_FAMILY_N = "SMALL_EFFECTIVE_FAMILY_N"
INSUFFICIENT_EFFECTIVE_FAMILY_N = "INSUFFICIENT_EFFECTIVE_FAMILY_N"
WITHHELD_SMALL_N = "WITHHELD_SMALL_N"
NOT_COMPUTABLE = "NOT_COMPUTABLE"
ELIGIBLE = "ELIGIBLE"

EFFECT_DIRECTION = "second_minus_first"
CI_METHOD = "family_percentile_bootstrap"
SEED_DERIVATION_ALGORITHM = "sha256-canonical-json-first-64-bits-v1"

_ZERO_TOLERANCE = 1e-12


def derive_bootstrap_seed(base_seed: int, *components: object) -> int:
    """Derive a stable 64-bit seed from a base seed and labelled components.

    The canonical payload is a compact, key-sorted JSON object containing the
    algorithm identifier, integer base seed, and JSON-compatible components.
    The returned seed is the unsigned big-endian integer represented by the
    first eight bytes of its SHA-256 digest.  This definition is deliberately
    explicit so every effective seed can be reproduced from provenance.
    """

    if isinstance(base_seed, bool) or not isinstance(base_seed, int):
        raise TypeError("base_seed must be an integer")
    payload = {
        "algorithm": SEED_DERIVATION_ALGORITHM,
        "base_seed": base_seed,
        "components": list(components),
    }
    try:
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TypeError("bootstrap seed components must be JSON-compatible") from exc
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big", signed=False)


def _finite_number(value: object, *, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be numeric, boolean, or null")
    selected = float(value)
    return selected if math.isfinite(selected) else None


def _validated_family_rows(
    rows: Sequence[Mapping[str, Any]],
    value_fields: Sequence[str],
    *,
    family_field: str,
    complete_values: bool,
) -> list[dict[str, Any]]:
    """Validate the one-row-per-family contract and select finite values."""

    seen: set[str] = set()
    selected: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if family_field not in row:
            raise ValueError(f"row {index} is missing family field {family_field!r}")
        raw_family = row[family_field]
        if raw_family is None or str(raw_family).strip() == "":
            raise ValueError(f"row {index} has an empty family identifier")
        family_id = str(raw_family)
        if family_id in seen:
            raise ValueError(
                f"duplicate family {family_id!r}; aggregate executions and variants "
                "before family-level inference"
            )
        seen.add(family_id)

        normalized: dict[str, Any] = {"family_id": family_id}
        for field in value_fields:
            if field not in row:
                raise ValueError(f"family {family_id!r} is missing value field {field!r}")
            normalized[field] = _finite_number(
                row[field], label=f"family {family_id!r} field {field!r}"
            )
        if complete_values and any(normalized[field] is None for field in value_fields):
            continue
        selected.append(normalized)

    # Protocol-v4 already sorts cluster identifiers before sampling.  Sorting
    # here additionally keeps normalization and downstream diagnostics stable.
    return sorted(selected, key=lambda row: row["family_id"])


def family_bootstrap_ci(
    rows: Sequence[Mapping[str, Any]],
    value_field: str,
    *,
    family_field: str = "family_id",
    replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> tuple[float | None, float | None]:
    """Return a 95% percentile CI after equal-weight family resampling.

    ``rows`` must contain at most one row for each family.  Null and non-finite
    endpoint values are excluded; fewer than two usable families yield no CI.
    """

    normalized = _validated_family_rows(
        rows,
        (value_field,),
        family_field=family_field,
        complete_values=True,
    )
    if len(normalized) < MIN_BOOTSTRAP_FAMILY_N:
        if replicates < 1:
            raise ValueError("bootstrap replicates must be >= 1")
        return None, None
    return _v4_cluster_bootstrap_ci(
        normalized,
        lambda sample: mean(row[value_field] for row in sample),
        cluster_field="family_id",
        replicates=replicates,
        seed=seed,
    )


def paired_family_bootstrap_ci(
    rows: Sequence[Mapping[str, Any]],
    first_field: str,
    second_field: str,
    *,
    family_field: str = "family_id",
    replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> tuple[float | None, float | None]:
    """Return a family-bootstrap CI for the paired effect ``second - first``.

    Only complete finite pairs are used.  Both system values necessarily come
    from the same resampled family; marginally available observations are not
    combined into an invalid pseudo-pair.
    """

    normalized = _validated_family_rows(
        rows,
        (first_field, second_field),
        family_field=family_field,
        complete_values=True,
    )
    if len(normalized) < MIN_BOOTSTRAP_FAMILY_N:
        if replicates < 1:
            raise ValueError("bootstrap replicates must be >= 1")
        return None, None
    return _v4_paired_difference_bootstrap_ci(
        normalized,
        second_field,
        first_field,
        cluster_field="family_id",
        replicates=replicates,
        seed=seed,
    )


def _complete_pairs(
    first: Sequence[float | int | bool | None],
    second: Sequence[float | int | bool | None],
) -> list[tuple[float, float]]:
    if len(first) != len(second):
        raise ValueError("paired outcomes must have equal length")
    pairs: list[tuple[float, float]] = []
    for index, (first_value, second_value) in enumerate(zip(first, second)):
        normalized_first = _finite_number(first_value, label=f"first[{index}]")
        normalized_second = _finite_number(second_value, label=f"second[{index}]")
        if normalized_first is not None and normalized_second is not None:
            pairs.append((normalized_first, normalized_second))
    return pairs


def is_binary_outcomes(
    *outcomes: Sequence[float | int | bool | None],
) -> bool:
    """Return whether all observed values are exactly binary (zero or one).

    Null and non-finite values are ignored because paired analyses exclude
    them.  At least one usable value is required.  No thresholding is used.
    """

    observed = False
    for sequence_index, sequence in enumerate(outcomes):
        for value_index, value in enumerate(sequence):
            selected = _finite_number(
                value, label=f"outcomes[{sequence_index}][{value_index}]"
            )
            if selected is None:
                continue
            observed = True
            if selected != 0.0 and selected != 1.0:
                return False
    return observed


def _matched_pairs_rank_biserial(differences: Sequence[float]) -> float | None:
    if not differences:
        return None
    nonzero = [difference for difference in differences if abs(difference) > _ZERO_TOLERANCE]
    if not nonzero:
        return 0.0

    absolute = [abs(difference) for difference in nonzero]
    order = sorted(range(len(absolute)), key=absolute.__getitem__)
    ranks = [0.0] * len(absolute)
    index = 0
    while index < len(order):
        end = index + 1
        while (
            end < len(order)
            and abs(absolute[order[end]] - absolute[order[index]]) < _ZERO_TOLERANCE
        ):
            end += 1
        average_rank = (index + 1 + end) / 2.0
        for position in range(index, end):
            ranks[order[position]] = average_rank
        index = end

    positive = sum(rank for rank, difference in zip(ranks, nonzero) if difference > 0)
    negative = sum(rank for rank, difference in zip(ranks, nonzero) if difference < 0)
    return (positive - negative) / (positive + negative)


def paired_effect_sizes(
    first: Sequence[float | int | bool | None],
    second: Sequence[float | int | bool | None],
) -> dict[str, int | float | str | None]:
    """Calculate paired family effects in the direction ``second - first``.

    A risk difference is emitted only for genuinely binary paired outcomes.
    Paired Cohen's ``d_z`` is undefined for one pair or zero variance and is
    therefore ``None`` rather than the misleading zero used by some legacy
    implementations.
    """

    pairs = _complete_pairs(first, second)
    if not pairs:
        return {
            "effect_direction": EFFECT_DIRECTION,
            "pairs": 0,
            "mean_difference": None,
            "risk_difference": None,
            "median_paired_difference": None,
            "matched_pairs_rank_biserial": None,
            "cohens_dz": None,
        }

    differences = [second_value - first_value for first_value, second_value in pairs]
    mean_difference = sum(differences) / len(differences)
    median_difference = quantile(differences, 0.5)
    binary = is_binary_outcomes(
        [first_value for first_value, _ in pairs],
        [second_value for _, second_value in pairs],
    )

    cohens_dz: float | None = None
    if len(differences) > 1:
        variance = sum(
            (difference - mean_difference) ** 2 for difference in differences
        ) / (len(differences) - 1)
        standard_deviation = math.sqrt(variance)
        if standard_deviation > _ZERO_TOLERANCE:
            cohens_dz = mean_difference / standard_deviation

    return {
        "effect_direction": EFFECT_DIRECTION,
        "pairs": len(pairs),
        "mean_difference": mean_difference,
        "risk_difference": mean_difference if binary else None,
        "median_paired_difference": median_difference,
        "matched_pairs_rank_biserial": _matched_pairs_rank_biserial(differences),
        "cohens_dz": cohens_dz,
    }


def family_n_warnings(effective_family_n: int) -> list[str]:
    """Return stable warning codes for an effective family sample size."""

    if isinstance(effective_family_n, bool) or not isinstance(effective_family_n, int):
        raise TypeError("effective_family_n must be an integer")
    if effective_family_n < 0:
        raise ValueError("effective_family_n must be non-negative")
    warnings: list[str] = []
    if effective_family_n < MIN_BOOTSTRAP_FAMILY_N:
        warnings.append(INSUFFICIENT_EFFECTIVE_FAMILY_N)
    if effective_family_n < SMALL_EFFECTIVE_FAMILY_N_THRESHOLD:
        warnings.append(SMALL_EFFECTIVE_FAMILY_N)
    return warnings


def inference_eligibility(effective_family_n: int) -> dict[str, Any]:
    """Describe CI/test/decision eligibility under Protocol-v5 small-N rules."""

    warnings = family_n_warnings(effective_family_n)
    if effective_family_n < MIN_BOOTSTRAP_FAMILY_N:
        status = NOT_COMPUTABLE
    elif effective_family_n < MIN_PAIRED_DECISION_FAMILY_N:
        status = WITHHELD_SMALL_N
    else:
        status = ELIGIBLE
    return {
        "effective_family_n": effective_family_n,
        "ci_eligible": effective_family_n >= MIN_BOOTSTRAP_FAMILY_N,
        "paired_test_eligible": effective_family_n >= MIN_BOOTSTRAP_FAMILY_N,
        "statistical_decision_eligible": (
            effective_family_n >= MIN_PAIRED_DECISION_FAMILY_N
        ),
        "inference_status": status,
        "warning_codes": warnings,
    }


def paired_test(
    first: Sequence[float | int | bool | None],
    second: Sequence[float | int | bool | None],
    *,
    binary_outcome: bool | None = None,
) -> dict[str, Any]:
    """Route paired family outcomes to exact McNemar or signed-rank testing.

    When ``binary_outcome`` is omitted, exact observed zero/one values select
    McNemar.  Passing ``False`` is useful for a structurally fractional family
    rate whose realized values happen to be zero and one.  Passing ``True``
    fails unless every complete pair is exactly binary; values are never
    thresholded.  Tests are omitted below two complete families and decisions
    are withheld below ten, even though raw p-values remain available at N=2–9.
    """

    if binary_outcome is not None and not isinstance(binary_outcome, bool):
        raise TypeError("binary_outcome must be true, false, or null")
    pairs = _complete_pairs(first, second)
    first_values = [pair[0] for pair in pairs]
    second_values = [pair[1] for pair in pairs]
    detected_binary = is_binary_outcomes(first_values, second_values)
    if binary_outcome is True and pairs and not detected_binary:
        raise ValueError("McNemar requires exact binary paired outcomes")
    use_mcnemar = detected_binary if binary_outcome is None else binary_outcome

    eligibility = inference_eligibility(len(pairs))
    common: dict[str, Any] = {
        "effect_direction": EFFECT_DIRECTION,
        "effective_family_n": len(pairs),
        "pairs": len(pairs),
        "binary_outcome": bool(use_mcnemar),
        "binary_outcome_detected": detected_binary,
        "inference_status": eligibility["inference_status"],
        "warning_codes": eligibility["warning_codes"],
    }
    if len(pairs) < MIN_BOOTSTRAP_FAMILY_N:
        return {
            **common,
            "test_method": None,
            "p_value_raw": None,
            "statistic": None,
        }

    if use_mcnemar:
        result = exact_mcnemar(
            [value == 1.0 for value in first_values],
            [value == 1.0 for value in second_values],
        )
        return {
            **common,
            "test_method": "exact_mcnemar",
            "alternative": "two_sided",
            "first_only_successes": result["first_only_correct"],
            "second_only_successes": result["second_only_correct"],
            "discordant_pairs": result["discordant_pairs"],
            "statistic": result["discordant_pairs"],
            "p_value_raw": result["p_value_raw"],
        }

    # The trusted v4 function forms differences as its first argument minus
    # its second.  Reverse arguments to preserve Protocol-v5's second-first
    # direction in W+ and W-.
    result = wilcoxon_signed_rank(second_values, first_values)
    return {
        **common,
        "test_method": "wilcoxon_signed_rank",
        "alternative": "two_sided",
        "non_zero_pairs": result["non_zero_pairs"],
        "w_positive": result["w_positive"],
        "w_negative": result["w_negative"],
        "statistic": result["statistic"],
        "z_score": result["z_score"],
        "p_value_raw": result["p_value_raw"],
    }


def statistical_decision(
    p_value: float | None,
    effective_family_n: int,
    *,
    alpha: float = DEFAULT_ALPHA,
    assumptions_adequate: bool = True,
) -> str:
    """Return a guarded decision label for a raw or multiplicity-adjusted p-value."""

    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
        raise ValueError("alpha must be between zero and one")
    selected: float | None = None
    if p_value is not None:
        if isinstance(p_value, bool) or not isinstance(p_value, (int, float)):
            raise ValueError(
                "p_value must be null or a finite value between zero and one"
            )
        selected = float(p_value)
        if not math.isfinite(selected) or not 0 <= selected <= 1:
            raise ValueError(
                "p_value must be null or a finite value between zero and one"
            )
    eligibility = inference_eligibility(effective_family_n)
    if eligibility["inference_status"] == NOT_COMPUTABLE or selected is None:
        return NOT_COMPUTABLE
    if eligibility["inference_status"] == WITHHELD_SMALL_N:
        return WITHHELD_SMALL_N
    if not assumptions_adequate:
        return "WITHHELD_INADEQUATE_ASSUMPTIONS"
    return "REJECT_NULL" if selected <= alpha else "FAIL_TO_REJECT_NULL"


__all__ = [
    "CI_METHOD",
    "DEFAULT_ALPHA",
    "DEFAULT_BOOTSTRAP_REPLICATES",
    "DEFAULT_BOOTSTRAP_SEED",
    "DEFAULT_CONFIDENCE_LEVEL",
    "EFFECT_DIRECTION",
    "ELIGIBLE",
    "INSUFFICIENT_EFFECTIVE_FAMILY_N",
    "MIN_BOOTSTRAP_FAMILY_N",
    "MIN_PAIRED_DECISION_FAMILY_N",
    "NOT_COMPUTABLE",
    "PROTOCOL_VERSION",
    "SEED_DERIVATION_ALGORITHM",
    "SMALL_EFFECTIVE_FAMILY_N",
    "SMALL_EFFECTIVE_FAMILY_N_THRESHOLD",
    "STATISTICS_SCHEMA_VERSION",
    "WITHHELD_SMALL_N",
    "derive_bootstrap_seed",
    "exact_mcnemar",
    "family_bootstrap_ci",
    "family_n_warnings",
    "holm_adjust",
    "inference_eligibility",
    "is_binary_outcomes",
    "mean",
    "paired_effect_sizes",
    "paired_family_bootstrap_ci",
    "paired_test",
    "quantile",
    "statistical_decision",
    "wilcoxon_signed_rank",
]
