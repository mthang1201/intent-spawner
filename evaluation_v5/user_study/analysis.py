"""Participant-aware analysis and aggregate reporting for Protocol-v5 E3."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import csv
import hashlib
from importlib import metadata
import io
import json
import math
import platform
from pathlib import Path
import re
import statistics
import sys
from typing import Any
import warnings

from .questionnaires import (
    ANALYSIS_PLAN,
    ANALYSIS_PLAN_SHA256,
    ANALYSIS_PLAN_VERSION,
    CUSTOM_ITEM_IDS,
    FINAL_PREFERENCE_ID,
    FINAL_PREFERENCES,
    SEQ_ITEM_ID,
)


ANALYSIS_SCHEMA_VERSION = "protocol-v5-user-study-analysis-v1.1.0"
REPORTING_VERSION = "protocol-v5-user-study-reporting-v1.1.0"
BOOTSTRAP_REPLICATES = int(ANALYSIS_PLAN["bootstrap_replicates"])
BOOTSTRAP_SEED = int(ANALYSIS_PLAN["bootstrap_seed"])
ALPHA = float(ANALYSIS_PLAN["family_alpha"])
BOOTSTRAP_MIN_SUCCESS_FRACTION = float(
    ANALYSIS_PLAN["bootstrap_minimum_success_fraction"]
)
SUPPORTED_PYTHON = ">=3.11,<3.15"
PINNED_ANALYSIS_DEPENDENCIES = {
    "numpy": "2.5.1",
    "scipy": "1.18.1",
    "pandas": "3.0.5",
    "patsy": "1.0.2",
    "statsmodels": "0.14.6",
    "matplotlib": "3.11.1",
}


class UserStudyAnalysisError(RuntimeError):
    """Validated inputs cannot produce the declared analysis artifacts."""


class _ModelContractError(RuntimeError):
    """A predeclared technical criterion requires an endpoint fallback."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _technical_reason(exc: Exception) -> str:
    return getattr(exc, "reason_code", f"fit_exception_{type(exc).__name__}")


def analysis_dependency_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in PINNED_ANALYSIS_DEPENDENCIES:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def validate_analysis_dependencies() -> dict[str, str | None]:
    if not ((3, 11) <= sys.version_info[:2] < (3, 15)):
        raise UserStudyAnalysisError(
            f"unsupported Python {sys.version.split()[0]}; requires {SUPPORTED_PYTHON}"
        )
    versions = analysis_dependency_versions()
    drift = {
        package: {"expected": expected, "observed": versions[package]}
        for package, expected in PINNED_ANALYSIS_DEPENDENCIES.items()
        if versions[package] != expected
    }
    if drift:
        raise UserStudyAnalysisError(
            "analysis dependency drift; install requirements-analysis.txt: "
            + json.dumps(drift, sort_keys=True)
        )
    return versions


def _finite(value: object) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    selected = float(value)
    return selected if math.isfinite(selected) else None


def _percentile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _ci(values: Sequence[float]) -> list[float | None]:
    return [_percentile(values, ALPHA / 2), _percentile(values, 1 - ALPHA / 2)]


def _bootstrap_contract(
    *, seed_offset: int, successful: int, requested: int, ci_available: bool
) -> dict[str, Any]:
    return {
        "resampling_unit": "participant_all_rows_together",
        "rng_algorithm": "numpy.random.PCG64",
        "base_seed": BOOTSTRAP_SEED,
        "seed_offset": seed_offset,
        "effective_seed": BOOTSTRAP_SEED + seed_offset,
        "requested_replicates": requested,
        "successful_replicates": successful,
        "successful_fraction": successful / requested if requested else 0.0,
        "minimum_success_fraction": BOOTSTRAP_MIN_SUCCESS_FRACTION,
        "ci_construction": "percentile_equal_tailed_95",
        "failed_replicates_discarded": requested - successful,
        "ci_available": ci_available,
    }


def _participant_bootstrap(
    values: Mapping[str, float], *, seed_offset: int = 0
) -> tuple[list[float | None], dict[str, Any]]:
    if len(values) < 2:
        return [None, None], _bootstrap_contract(
            seed_offset=seed_offset,
            successful=0,
            requested=BOOTSTRAP_REPLICATES,
            ci_available=False,
        )
    import numpy as np

    keys = sorted(values)
    rng = np.random.default_rng(BOOTSTRAP_SEED + seed_offset)
    samples = [
        statistics.fmean(values[keys[index]] for index in rng.integers(0, len(keys), len(keys)))
        for _ in range(BOOTSTRAP_REPLICATES)
    ]
    interval = _ci(samples)
    return interval, _bootstrap_contract(
        seed_offset=seed_offset,
        successful=len(samples),
        requested=BOOTSTRAP_REPLICATES,
        ci_available=all(bound is not None for bound in interval),
    )


def _cluster_condition_ci(
    rows: Sequence[Mapping[str, Any]], condition: str, field: str, *, seed_offset: int
) -> tuple[list[float | None], dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.get("condition") != condition:
            continue
        value = row.get(field)
        selected = float(value) if isinstance(value, bool) else _finite(value)
        if selected is not None:
            grouped[str(row["participant_id"])].append(selected)
    participant_values = {
        participant: statistics.fmean(values)
        for participant, values in grouped.items()
        if values
    }
    return _participant_bootstrap(participant_values, seed_offset=seed_offset)


def _paired_effect(
    rows: Sequence[Mapping[str, Any]], field: str, *, seed_offset: int
) -> dict[str, Any]:
    grouped: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"B0": [], "P2": []}
    )
    for row in rows:
        condition = row.get("condition")
        raw_value = row.get(field)
        value = float(raw_value) if isinstance(raw_value, bool) else _finite(raw_value)
        if condition in {"B0", "P2"} and value is not None:
            grouped[str(row["participant_id"])][str(condition)].append(value)
    differences: dict[str, float] = {}
    for participant, conditions in grouped.items():
        if conditions["B0"] and conditions["P2"]:
            differences[participant] = statistics.fmean(
                conditions["P2"]
            ) - statistics.fmean(conditions["B0"])
    values = list(differences.values())
    mean_difference = statistics.fmean(values) if values else None
    standard_deviation = statistics.stdev(values) if len(values) >= 2 else None
    interval, bootstrap = _participant_bootstrap(
        differences, seed_offset=seed_offset
    )
    return {
        "effect_direction": "P2_minus_B0",
        "complete_participant_count": len(values),
        "mean_difference": mean_difference,
        "median_difference": statistics.median(values) if values else None,
        "cohens_dz": (
            mean_difference / standard_deviation
            if mean_difference is not None
            and standard_deviation is not None
            and standard_deviation > 0
            else None
        ),
        "confidence_interval_95": interval,
        "ci_method": "participant_percentile_bootstrap",
        "standardized_effect_definition": "cohens_dz=mean_participant_difference/sd_participant_difference",
        "bootstrap": bootstrap,
    }


def _design_formula(outcome: str) -> str:
    return (
        f"{outcome} ~ condition_p2 + C(pair_id) + variant_slot + period + "
        "condition_order_p2_first"
    )


def _model_frame(rows: Sequence[Mapping[str, Any]], outcome: str) -> Any:
    import pandas as pd

    selected: list[dict[str, Any]] = []
    for row in rows:
        value = row.get(outcome)
        numeric = float(value) if isinstance(value, bool) else _finite(value)
        if numeric is None:
            continue
        if any(row.get(field) is None for field in ("pair_id", "variant_slot", "period", "condition_order")):
            continue
        selected.append(
            {
                "participant_id": str(row["participant_id"]),
                "pair_id": str(row["pair_id"]),
                "variant_slot": int(row["variant_slot"]),
                "period": int(row["period"]),
                "condition_p2": int(row["condition"] == "P2"),
                "condition_order_p2_first": int(
                    str(row["condition_order"]).startswith("P2")
                ),
                outcome: numeric,
            }
        )
    return pd.DataFrame(selected)


def _selection_fit(frame: Any) -> tuple[Any, float]:
    import statsmodels.api as sm
    import statsmodels.formula.api as smf

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fit = smf.gee(
            _design_formula("selection_success"),
            groups="participant_id",
            data=frame,
            family=sm.families.Binomial(),
            cov_struct=sm.cov_struct.Exchangeable(),
        ).fit(maxiter=200)
    if any(issubclass(item.category, RuntimeWarning) for item in caught):
        raise _ModelContractError("numerical_warning")
    if not bool(getattr(fit, "converged", True)):
        raise _ModelContractError("nonconvergence")
    b0 = frame.copy()
    p2 = frame.copy()
    b0["condition_p2"] = 0
    p2["condition_p2"] = 1
    risk_difference = float(fit.predict(p2).mean() - fit.predict(b0).mean())
    if not math.isfinite(risk_difference):
        raise _ModelContractError("nonfinite_marginal_standardization")
    return fit, risk_difference


def _fit_with_numerical_warnings_as_errors(callback: Any) -> Any:
    """Run a model fit and make its predeclared fallback deterministic.

    Statsmodels can return a result object after singular-matrix, overflow, or
    convergence warnings.  Those fits are not accepted merely because they
    happen to contain a coefficient; the caller moves to its declared robust
    fallback instead.
    """

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fit = callback()
    rejected = {
        "ConvergenceWarning",
        "HessianInversionWarning",
        "RuntimeWarning",
    }
    if any(item.category.__name__ in rejected for item in caught):
        raise _ModelContractError("numerical_or_convergence_warning")
    return fit


def _require_finite_inference(*values: float) -> None:
    if not all(math.isfinite(value) for value in values):
        raise _ModelContractError("nonfinite_inference")


def _require_identifiable_random_intercept(fit: Any) -> None:
    import numpy as np

    covariance = np.asarray(fit.cov_re, dtype=float)
    if (
        covariance.size == 0
        or not np.isfinite(covariance).all()
        or np.linalg.matrix_rank(covariance) < covariance.shape[0]
        or float(np.linalg.eigvalsh(covariance).min()) <= 0
    ):
        raise _ModelContractError("singular_random_intercept_covariance")


def _coefficient_inference(
    fit: Any, name: str, *, include_p: bool = True
) -> tuple[float, float, float, float | None]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        beta = float(fit.params[name])
        interval = fit.conf_int().loc[name]
        lower = float(interval.iloc[0])
        upper = float(interval.iloc[1])
        p_value = float(fit.pvalues[name]) if include_p else None
    if any(
        item.category.__name__
        in {"ConvergenceWarning", "HessianInversionWarning", "RuntimeWarning"}
        for item in caught
    ):
        raise _ModelContractError("numerical_warning_during_inference")
    _require_finite_inference(
        beta, lower, upper, *(() if p_value is None else (p_value,))
    )
    return beta, lower, upper, p_value


def _selection_bootstrap(frame: Any) -> tuple[list[float | None], dict[str, Any]]:
    if frame["participant_id"].nunique() < 4:
        return [None, None], _bootstrap_contract(
            seed_offset=101,
            successful=0,
            requested=BOOTSTRAP_REPLICATES,
            ci_available=False,
        )
    import numpy as np
    import pandas as pd

    participants = sorted(frame["participant_id"].unique())
    rng = np.random.default_rng(BOOTSTRAP_SEED + 101)
    estimates: list[float] = []
    for _ in range(BOOTSTRAP_REPLICATES):
        pieces = []
        for draw_index, participant in enumerate(
            rng.choice(participants, size=len(participants), replace=True)
        ):
            piece = frame[frame["participant_id"] == participant].copy()
            piece["participant_id"] = f"bootstrap-{draw_index}"
            pieces.append(piece)
        try:
            _, estimate = _selection_fit(pd.concat(pieces, ignore_index=True))
            if math.isfinite(estimate):
                estimates.append(estimate)
        except Exception:
            continue
    threshold = math.ceil(
        BOOTSTRAP_REPLICATES * BOOTSTRAP_MIN_SUCCESS_FRACTION
    )
    available = len(estimates) >= threshold
    interval = _ci(estimates) if available else [None, None]
    return interval, _bootstrap_contract(
        seed_offset=101,
        successful=len(estimates),
        requested=BOOTSTRAP_REPLICATES,
        ci_available=available,
    )


def _method_contract(
    *,
    requested_method: str,
    actual_method: str,
    fallback_reason: str | None,
    eligible_row_count: int,
    participant_count: int,
    converged: bool | None,
    eligible_pair_count: int | None = None,
) -> dict[str, Any]:
    return {
        "requested_method": requested_method,
        "actual_method": actual_method,
        "fallback_reason": fallback_reason,
        "converged": converged,
        "eligibility": {
            "eligible_row_count": eligible_row_count,
            "effective_participant_count": participant_count,
            "complete_matched_pair_count": eligible_pair_count,
        },
    }


def _paired_p_value(rows: Sequence[Mapping[str, Any]], field: str) -> float | None:
    differences = _paired_differences(rows, field)
    if len(differences) < 2:
        return None
    values = list(differences.values())
    if not any(value != 0 for value in values):
        return 1.0
    from scipy.stats import wilcoxon

    try:
        p_value = float(wilcoxon(values, alternative="two-sided").pvalue)
        return p_value if math.isfinite(p_value) else None
    except ValueError:
        return 1.0


def _selection_analysis(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    frame = _model_frame(rows, "selection_success")
    participant_count = int(frame["participant_id"].nunique()) if not frame.empty else 0
    requested = "participant_clustered_binomial_gee_exchangeable"
    if participant_count < 2 or frame["selection_success"].nunique() < 2:
        paired = _paired_effect(rows, "selection_success", seed_offset=102)
        return {
            "status": "FALLBACK",
            "method": "participant_paired_risk_difference",
            **_method_contract(
                requested_method=requested,
                actual_method="participant_paired_risk_difference",
                fallback_reason="insufficient_clusters_or_outcome_variation",
                eligible_row_count=len(frame),
                participant_count=participant_count,
                converged=None,
            ),
            "risk_difference": paired["mean_difference"],
            "risk_difference_ci_95": paired["confidence_interval_95"],
            "risk_difference_estimand": "mean_participant_P2_minus_B0_success_probability",
            "odds_ratio": None,
            "odds_ratio_ci_95": [None, None],
            "p_value_raw": _paired_p_value(rows, "selection_success"),
            "p_value_sidedness": "two_sided",
            "effective_participant_count": participant_count,
            "bootstrap": paired["bootstrap"],
        }
    try:
        fit, risk_difference = _selection_fit(frame)
        beta, lower, upper, p_value = _coefficient_inference(
            fit, "condition_p2"
        )
        assert p_value is not None
        bootstrap_ci, bootstrap = _selection_bootstrap(frame)
        return {
            "status": "MODELED",
            "method": "participant_clustered_binomial_gee_exchangeable",
            **_method_contract(
                requested_method=requested,
                actual_method=requested,
                fallback_reason=None,
                eligible_row_count=len(frame),
                participant_count=participant_count,
                converged=True,
            ),
            "response_variable": "selection_success",
            "cluster_unit": "participant_id",
            "working_correlation": "exchangeable",
            "fixed_effects": [
                "condition_p2",
                "C(pair_id)",
                "variant_slot",
                "period",
                "condition_order_p2_first",
            ],
            "counterbalance_cell_handling": "coverage_diagnostic_only_redundant_with_frozen_design_terms",
            "risk_difference": risk_difference,
            "risk_difference_ci_95": bootstrap_ci,
            "risk_difference_estimand": "average_over_observed_frozen_design_rows_of_predicted_P2_probability_minus_predicted_B0_probability",
            "risk_difference_ci_method": "participant_cluster_refit_percentile_bootstrap",
            "bootstrap": bootstrap,
            "odds_ratio": math.exp(beta),
            "odds_ratio_ci_95": [math.exp(lower), math.exp(upper)],
            "p_value_raw": p_value,
            "p_value_sidedness": "two_sided",
            "effective_participant_count": participant_count,
        }
    except Exception as exc:
        paired = _paired_effect(rows, "selection_success", seed_offset=103)
        return {
            "status": "FALLBACK",
            "method": "participant_paired_risk_difference",
            **_method_contract(
                requested_method=requested,
                actual_method="participant_paired_risk_difference",
                fallback_reason=f"gee_nonconvergence_or_nonidentifiability:{_technical_reason(exc)}",
                eligible_row_count=len(frame),
                participant_count=participant_count,
                converged=False,
            ),
            "risk_difference": paired["mean_difference"],
            "risk_difference_ci_95": paired["confidence_interval_95"],
            "risk_difference_estimand": "mean_participant_P2_minus_B0_success_probability",
            "odds_ratio": None,
            "odds_ratio_ci_95": [None, None],
            "p_value_raw": _paired_p_value(rows, "selection_success"),
            "p_value_sidedness": "two_sided",
            "effective_participant_count": participant_count,
            "bootstrap": paired["bootstrap"],
        }


def _log_mixed_analysis(
    rows: Sequence[Mapping[str, Any]], field: str, *, seed_offset: int
) -> dict[str, Any]:
    matched = _complete_matched_rows(rows, field)
    frame = _model_frame(matched, field)
    paired = _paired_effect(matched, field, seed_offset=seed_offset)
    participant_count = int(frame["participant_id"].nunique()) if not frame.empty else 0
    eligible_pair_count = len(frame) // 2
    requested = "log_time_participant_random_intercept_mixedlm"

    def robust(reason: str) -> dict[str, Any]:
        return {
            "status": "FALLBACK",
            "method": "participant_paired_robust_raw_scale",
            **_method_contract(
                requested_method=requested,
                actual_method="participant_paired_robust_raw_scale",
                fallback_reason=reason,
                eligible_row_count=len(frame),
                participant_count=participant_count,
                converged=None if reason.startswith("insufficient") or reason == "nonpositive_time" else False,
                eligible_pair_count=eligible_pair_count,
            ),
            "response_variable": field,
            "clock_definition": _clock_definition(field),
            "geometric_mean_ratio": None,
            "geometric_mean_ratio_ci_95": [None, None],
            "percent_change": None,
            "p_value_raw": _paired_p_value(matched, field),
            "p_value_sidedness": "two_sided",
            "raw_paired_effect": paired,
            "effective_participant_count": participant_count,
        }

    if frame.empty or (frame[field] <= 0).any() or participant_count < 3:
        reason = "nonpositive_time" if not frame.empty and (frame[field] <= 0).any() else "insufficient_participants"
        return robust(reason)
    frame = frame.copy()
    frame["log_outcome"] = frame[field].map(math.log)
    try:
        import statsmodels.formula.api as smf

        fit = _fit_with_numerical_warnings_as_errors(
            lambda: smf.mixedlm(
                _design_formula("log_outcome"),
                frame,
                groups=frame["participant_id"],
            ).fit(reml=True, method="lbfgs", maxiter=500, disp=False)
        )
        if not bool(fit.converged):
            raise _ModelContractError("nonconvergence")
        _require_identifiable_random_intercept(fit)
        beta, lower, upper, p_value = _coefficient_inference(
            fit, "condition_p2"
        )
        assert p_value is not None
        ratio = math.exp(beta)
        return {
            "status": "MODELED",
            "method": requested,
            **_method_contract(
                requested_method=requested,
                actual_method=requested,
                fallback_reason=None,
                eligible_row_count=len(frame),
                participant_count=participant_count,
                converged=True,
                eligible_pair_count=eligible_pair_count,
            ),
            "response_variable": field,
            "clock_definition": _clock_definition(field),
            "random_intercept_unit": "participant_id",
            "fixed_effects": [
                "condition_p2",
                "C(pair_id)",
                "variant_slot",
                "period",
                "condition_order_p2_first",
            ],
            "counterbalance_cell_handling": "coverage_diagnostic_only_redundant_with_frozen_design_terms",
            "estimand": "conditional_P2_to_B0_geometric_mean_ratio_among_confirmed_complete_matched_tasks",
            "geometric_mean_ratio": ratio,
            "geometric_mean_ratio_ci_95": [
                math.exp(lower),
                math.exp(upper),
            ],
            "percent_change": (ratio - 1) * 100,
            "p_value_raw": p_value,
            "p_value_sidedness": "two_sided",
            "raw_paired_effect": paired,
            "effective_participant_count": participant_count,
        }
    except Exception as exc:
        return robust(
            f"mixed_model_nonconvergence_or_nonidentifiability:{_technical_reason(exc)}"
        )


def _clock_definition(field: str) -> str | None:
    return {
        "decision_time_seconds": "confirmed_environment_timestamp_minus_task_shown_timestamp",
        "notebook_ready_time_seconds": "notebook_ready_timestamp_minus_confirmed_environment_timestamp",
        "end_to_end_seconds": "notebook_ready_timestamp_minus_task_shown_timestamp",
    }.get(field)


def _paired_differences(
    rows: Sequence[Mapping[str, Any]], field: str
) -> dict[str, float]:
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"B0": [], "P2": []})
    for row in rows:
        raw_value = row.get(field)
        value = float(raw_value) if isinstance(raw_value, bool) else _finite(raw_value)
        condition = row.get("condition")
        if value is not None and condition in {"B0", "P2"}:
            grouped[str(row["participant_id"])][str(condition)].append(value)
    return {
        participant: statistics.fmean(values["P2"]) - statistics.fmean(values["B0"])
        for participant, values in grouped.items()
        if values["B0"] and values["P2"]
    }


def _complete_matched_rows(
    rows: Sequence[Mapping[str, Any]], field: str
) -> list[Mapping[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        participant = row.get("participant_id")
        pair = row.get("pair_id")
        condition = row.get("condition")
        if participant is None or pair is None or condition not in {"B0", "P2"}:
            continue
        if _finite(row.get(field)) is None:
            continue
        grouped[(str(participant), str(pair))][str(condition)] = row
    return [
        conditions[condition]
        for conditions in grouped.values()
        if set(conditions) == {"B0", "P2"}
        for condition in ("B0", "P2")
    ]


def _scale_mixed_analysis(
    rows: Sequence[Mapping[str, Any]], field: str, *, seed_offset: int
) -> dict[str, Any]:
    matched = _complete_matched_rows(rows, field)
    paired = _paired_effect(matched, field, seed_offset=seed_offset)
    frame = _model_frame(matched, field)
    participant_count = int(frame["participant_id"].nunique()) if not frame.empty else 0
    eligible_pair_count = len(frame) // 2
    requested = "participant_random_intercept_mixedlm_pair_fixed"

    def fallback(reason: str, *, converged: bool | None) -> dict[str, Any]:
        return {
            "status": "FALLBACK",
            "method": "participant_paired_scale_difference",
            **_method_contract(
                requested_method=requested,
                actual_method="participant_paired_scale_difference",
                fallback_reason=reason,
                eligible_row_count=len(frame),
                participant_count=participant_count,
                converged=converged,
                eligible_pair_count=eligible_pair_count,
            ),
            "response_variable": field,
            "adjusted_mean_difference": paired["mean_difference"],
            "adjusted_mean_difference_ci_95": paired["confidence_interval_95"],
            "participant_cluster_bootstrap_ci_95": paired["confidence_interval_95"],
            "standardized_paired_effect": paired["cohens_dz"],
            "standardized_effect_definition": paired["standardized_effect_definition"],
            "p_value_raw": _paired_p_value(matched, field),
            "p_value_sidedness": "two_sided",
            "paired_effect": paired,
        }
    if frame.empty or frame["participant_id"].nunique() < 3:
        return fallback("insufficient_participants", converged=None)
    try:
        import statsmodels.formula.api as smf

        fit = _fit_with_numerical_warnings_as_errors(
            lambda: smf.mixedlm(
                _design_formula(field),
                frame,
                groups=frame["participant_id"],
            ).fit(reml=True, method="lbfgs", maxiter=500, disp=False)
        )
        if not bool(fit.converged):
            raise _ModelContractError("nonconvergence")
        _require_identifiable_random_intercept(fit)
        estimate, lower, upper, p_value = _coefficient_inference(
            fit, "condition_p2"
        )
        assert p_value is not None
        return {
            "status": "MODELED",
            "method": requested,
            **_method_contract(
                requested_method=requested,
                actual_method=requested,
                fallback_reason=None,
                eligible_row_count=len(frame),
                participant_count=participant_count,
                converged=True,
                eligible_pair_count=eligible_pair_count,
            ),
            "response_variable": field,
            "random_intercept_unit": "participant_id",
            "fixed_effects": [
                "condition_p2",
                "C(pair_id)",
                "variant_slot",
                "period",
                "condition_order_p2_first",
            ],
            "adjusted_mean_difference": estimate,
            "adjusted_mean_difference_ci_95": [lower, upper],
            "participant_cluster_bootstrap_ci_95": paired[
                "confidence_interval_95"
            ],
            "standardized_paired_effect": paired["cohens_dz"],
            "standardized_effect_definition": paired["standardized_effect_definition"],
            "p_value_raw": p_value,
            "p_value_sidedness": "two_sided",
            "paired_effect": paired,
        }
    except Exception as exc:
        return fallback(
            f"mixed_model_nonconvergence_or_nonidentifiability:{_technical_reason(exc)}",
            converged=False,
        )


def _count_analysis(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, Any]:
    frame = _model_frame(rows, field)
    participant_count = int(frame["participant_id"].nunique()) if not frame.empty else 0
    requested = "participant_clustered_quasipoisson_gee_exchangeable"

    def fallback(reason: str, *, converged: bool | None) -> dict[str, Any]:
        paired = _paired_effect(rows, field, seed_offset=302 + len(field))
        return {
            "status": "FALLBACK",
            "method": "participant_paired_count_difference",
            **_method_contract(
                requested_method=requested,
                actual_method="participant_paired_count_difference",
                fallback_reason=reason,
                eligible_row_count=len(frame),
                participant_count=participant_count,
                converged=converged,
            ),
            "response_variable": field,
            "incidence_rate_ratio": None,
            "confidence_interval_95": [None, None],
            "p_value_raw": _paired_p_value(rows, field),
            "p_value_sidedness": "two_sided",
            "dispersion_scale": None,
            "dispersion_method": "pearson_scale_estimated_from_fit",
            "paired_effect": paired,
        }

    if frame.empty or participant_count < 2:
        return fallback("insufficient_participants", converged=None)
    try:
        import statsmodels.api as sm
        import statsmodels.formula.api as smf

        fit = _fit_with_numerical_warnings_as_errors(
            lambda: smf.gee(
                _design_formula(field),
                groups="participant_id",
                data=frame,
                family=sm.families.Poisson(),
                cov_struct=sm.cov_struct.Exchangeable(),
            ).fit(maxiter=200, scale="X2")
        )
        if not bool(getattr(fit, "converged", True)):
            raise _ModelContractError("nonconvergence")
        beta, lower, upper, p_value = _coefficient_inference(
            fit, "condition_p2"
        )
        assert p_value is not None
        dispersion_scale = float(fit.scale)
        _require_finite_inference(dispersion_scale)
        return {
            "status": "MODELED",
            "method": requested,
            **_method_contract(
                requested_method=requested,
                actual_method=requested,
                fallback_reason=None,
                eligible_row_count=len(frame),
                participant_count=participant_count,
                converged=True,
            ),
            "response_variable": field,
            "cluster_unit": "participant_id",
            "working_correlation": "exchangeable",
            "family": "poisson_mean_with_pearson_scale_quasi_likelihood",
            "dispersion_method": "pearson_scale_estimated_from_fit",
            "dispersion_scale": dispersion_scale,
            "fixed_effects": [
                "condition_p2",
                "C(pair_id)",
                "variant_slot",
                "period",
                "condition_order_p2_first",
            ],
            "incidence_rate_ratio": math.exp(beta),
            "confidence_interval_95": [math.exp(lower), math.exp(upper)],
            "p_value_raw": p_value,
            "p_value_sidedness": "two_sided",
            "paired_effect": _paired_effect(rows, field, seed_offset=301 + len(field)),
        }
    except Exception as exc:
        return fallback(
            f"count_gee_nonconvergence_or_nonidentifiability:{_technical_reason(exc)}",
            converged=False,
        )


def _holm(p_values: Mapping[str, float | None]) -> dict[str, float | None]:
    usable = sorted(
        (
            (name, value)
            for name, value in p_values.items()
            if value is not None and math.isfinite(value)
        ),
        key=lambda item: item[1],
    )
    adjusted: dict[str, float | None] = {name: None for name in p_values}
    running = 0.0
    # Preserve the frozen family size even when one endpoint is not computable;
    # a missing co-primary test must never make the other test less stringent.
    total = len(p_values)
    for index, (name, value) in enumerate(usable):
        running = max(running, min(1.0, value * (total - index)))
        adjusted[name] = running
    return adjusted


def _preference_summary(questionnaires: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    answers = [
        row.get(FINAL_PREFERENCE_ID)
        for row in questionnaires
        if row.get("questionnaire_type") == "final_preference"
    ]
    observed = [value for value in answers if value in FINAL_PREFERENCES]
    n = len(observed)
    rows: list[dict[str, Any]] = []
    if n:
        from scipy.stats import norm

        z = float(norm.ppf(1 - ALPHA / (2 * len(FINAL_PREFERENCES))))
    else:
        z = 0.0
    for category in ("B0", "P2", "NO_PREFERENCE"):
        count = observed.count(category)
        proportion = count / n if n else None
        if proportion is None:
            interval = [None, None]
        else:
            denominator = 1 + z * z / n
            center = (proportion + z * z / (2 * n)) / denominator
            radius = z * math.sqrt(proportion * (1 - proportion) / n + z * z / (4 * n * n)) / denominator
            interval = [max(0.0, center - radius), min(1.0, center + radius)]
        rows.append(
            {
                "preference": category,
                "count": count,
                "answered_denominator": n,
                "percentage": proportion * 100 if proportion is not None else None,
                "simultaneous_confidence_interval_95": [
                    bound * 100 if bound is not None else None for bound in interval
                ],
                "interval_method": "bonferroni_adjusted_wilson",
                "missing_response_count": len(answers) - n,
            }
        )
    return rows


def _participant_paired_analysis(
    rows: Sequence[Mapping[str, Any]], field: str, *, seed_offset: int
) -> dict[str, Any]:
    paired = _paired_effect(rows, field, seed_offset=seed_offset)
    return {
        "status": "COMPUTED" if paired["complete_participant_count"] else "NOT_COMPUTABLE",
        "method": "participant_paired_difference",
        **_method_contract(
            requested_method="participant_paired_difference",
            actual_method="participant_paired_difference",
            fallback_reason=(
                None
                if paired["complete_participant_count"]
                else "no_complete_participant_pairs"
            ),
            eligible_row_count=sum(
                _finite(row.get(field)) is not None for row in rows
            ),
            participant_count=paired["complete_participant_count"],
            converged=None,
            eligible_pair_count=paired["complete_participant_count"],
        ),
        "response_variable": field,
        "participant_dependence": "one_P2_minus_B0_difference_per_participant",
        "task_pair_fixed_effect": False,
        "p_value_raw": _paired_p_value(rows, field),
        "p_value_sidedness": "two_sided",
        **paired,
    }


def _primary_inference_registry(
    selection: Mapping[str, Any] | None,
    decision: Mapping[str, Any] | None,
) -> dict[str, Any]:
    endpoints = (
        ("selection_success", selection),
        ("decision_time_seconds", decision),
    )
    hypotheses = []
    for endpoint, result in endpoints:
        raw = result.get("p_value_raw") if result is not None else None
        adjusted = result.get("p_value_holm") if result is not None else None
        hypotheses.append(
            {
                "endpoint": endpoint,
                "sidedness": "two_sided",
                "inference_status": "AVAILABLE" if raw is not None else "UNAVAILABLE",
                "raw_p_value": raw,
                "holm_adjusted_p_value": adjusted,
                "reject_at_family_alpha": (
                    adjusted <= ALPHA if adjusted is not None else None
                ),
            }
        )
    return {
        "method": "holm_step_down",
        "family_alpha": ALPHA,
        "family_size": 2,
        "unavailable_endpoint_policy": "retain_in_family_and_never_reduce_family_size",
        "hypotheses": hypotheses,
    }


def _condition_summary(
    tasks: Sequence[Mapping[str, Any]], questionnaires: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    task_questionnaires = [row for row in questionnaires if row.get("questionnaire_type") == "seq_task"]
    condition_questionnaires = [row for row in questionnaires if row.get("questionnaire_type") == "post_condition"]
    summaries: list[dict[str, Any]] = []
    for condition_index, condition in enumerate(("B0", "P2")):
        selected = [row for row in tasks if row.get("condition") == condition]
        confirmed = [row for row in selected if row.get("confirmed")]
        selection_ci, selection_bootstrap = _cluster_condition_ci(
            selected,
            condition,
            "selection_success",
            seed_offset=10 + condition_index,
        )
        if condition == "P2" and confirmed:
            override_ci, override_bootstrap = _cluster_condition_ci(
                confirmed, "P2", "final_override", seed_offset=40
            )
        else:
            override_ci = [None, None]
            override_bootstrap = None
        def mean(field: str, source: Sequence[Mapping[str, Any]] = selected) -> float | None:
            values = [
                float(row.get(field))
                if isinstance(row.get(field), bool)
                else _finite(row.get(field))
                for row in source
                if row.get("condition") == condition
            ]
            present = [value for value in values if value is not None]
            return statistics.fmean(present) if present else None
        summaries.append(
            {
                "condition": condition,
                "participant_count": len({row["participant_id"] for row in selected}),
                "measured_task_count": len(selected),
                "selection_success_rate": mean("selection_success"),
                "selection_success_ci_95": selection_ci,
                "selection_success_bootstrap": selection_bootstrap,
                "confirmed_count": len(confirmed),
                "nonconfirmation_count": len(selected) - len(confirmed),
                "nonconfirmation_reasons": dict(
                    sorted(
                        {
                            reason: sum(
                                row.get("decision_time_unavailability_reason") == reason
                                for row in selected
                            )
                            for reason in {
                                str(row.get("decision_time_unavailability_reason"))
                                for row in selected
                                if row.get("decision_time_unavailability_reason")
                                is not None
                            }
                        }.items()
                    )
                ),
                "confirmation_rate": len(confirmed) / len(selected) if selected else None,
                "decision_time_mean_seconds": mean("decision_time_seconds"),
                "decision_time_timeout_sensitivity_mean_seconds": mean(
                    "decision_time_timeout_sensitivity_seconds"
                ),
                "decision_time_median_seconds": statistics.median([row["decision_time_seconds"] for row in selected if row.get("decision_time_seconds") is not None]) if any(row.get("decision_time_seconds") is not None for row in selected) else None,
                "decision_time_missing_count": sum(row.get("decision_time_seconds") is None for row in selected),
                "total_action_mean": mean("total_action_count"),
                "interaction_count_mean": mean("interaction_count"),
                "correction_mean": mean("correction_count"),
                "notebook_ready_rate": mean("notebook_ready_observed"),
                "notebook_ready_time_mean_seconds": mean("notebook_ready_time_seconds"),
                "notebook_ready_time_missing_reasons": dict(
                    sorted(
                        {
                            reason: sum(
                                row.get("notebook_ready_time_status") == reason
                                for row in selected
                            )
                            for reason in {
                                str(row.get("notebook_ready_time_status"))
                                for row in selected
                                if row.get("notebook_ready_time_status")
                                not in {None, "available"}
                            }
                        }.items()
                    )
                ),
                "end_to_end_launch_time_mean_seconds": mean("end_to_end_seconds"),
                "end_to_end_launch_time_missing_reasons": dict(
                    sorted(
                        {
                            reason: sum(
                                row.get("end_to_end_status") == reason
                                for row in selected
                            )
                            for reason in {
                                str(row.get("end_to_end_status"))
                                for row in selected
                                if row.get("end_to_end_status")
                                not in {None, "available"}
                            }
                        }.items()
                    )
                ),
                "seq_mean": mean(SEQ_ITEM_ID, task_questionnaires),
                "sus_mean": mean("sus_score", condition_questionnaires),
                **{f"{item}_mean": mean(item, condition_questionnaires) for item in CUSTOM_ITEM_IDS},
                "final_override_rate_among_confirmed": (
                    sum(bool(row.get("final_override")) for row in confirmed) / len(confirmed)
                    if condition == "P2" and confirmed else None
                ),
                "final_override_rate_ci_95": override_ci,
                "final_override_rate_bootstrap": override_bootstrap,
            }
        )
    return summaries


def _design_diagnostics(tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def counts(field: str) -> dict[str, int]:
        values = {
            str(row[field])
            for row in tasks
            if row.get(field) is not None
        }
        return {
            value: sum(str(row.get(field)) == value for row in tasks)
            for value in sorted(values)
        }

    return {
        "task_pair_counts": counts("pair_id"),
        "variant_slot_counts": counts("variant_slot"),
        "period_counts": counts("period"),
        "condition_order_counts": counts("condition_order"),
        "position_counts": counts("position"),
        "counterbalance_cell_counts": counts("counterbalance_cell"),
        "counterbalance_cell_analysis_role": "coverage_diagnostic_only_redundant_with_frozen_fixed_effect_terms",
    }


def analyze_user_study(
    *,
    execution_status: str,
    task_rows: Sequence[Mapping[str, Any]],
    questionnaire_rows: Sequence[Mapping[str, Any]],
    sessions: Sequence[Mapping[str, Any]],
    exclusions: Sequence[Mapping[str, Any]],
    assignment_manifest: Any,
) -> dict[str, Any]:
    """Return aggregate-only analysis. NOT_EXECUTED never invents denominators."""

    participant_flow = [
        {"stage": "assignments_issued", "count": assignment_manifest.participant_count},
        {"stage": "session_records", "count": len(sessions)},
        {"stage": "consent_acknowledged", "count": sum(bool(row.get("consent_acknowledged")) for row in sessions)},
        {"stage": "completed_sessions", "count": sum(row.get("session_status") == "complete" for row in sessions)},
        {"stage": "excluded_sessions", "count": len(exclusions)},
        {"stage": "incomplete_sessions", "count": sum(row.get("session_status") == "incomplete" for row in sessions)},
        {"stage": "analyzable_participants", "count": len({row["participant_id"] for row in task_rows})},
    ]
    if execution_status == "NOT_EXECUTED":
        return {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "execution_status": execution_status,
            "claims_permitted": False,
            "observed_rows_present": False,
            "analysis_plan_version": ANALYSIS_PLAN_VERSION,
            "analysis_plan_sha256": ANALYSIS_PLAN_SHA256,
            "participant_flow": participant_flow,
            "condition_summary": [],
            "design_diagnostics": _design_diagnostics([]),
            "effects": {},
            "primary_inference_registry": _primary_inference_registry(None, None),
            "preference": [],
            "missingness": [],
        }

    seq_rows = [row for row in questionnaire_rows if row.get("questionnaire_type") == "seq_task"]
    task_design = {str(row["trial_id"]): row for row in task_rows}
    seq_with_design = [
        {**dict(row), **{key: task_design.get(str(row.get("trial_id")), {}).get(key) for key in ("variant_slot", "condition_order")}}
        for row in seq_rows
    ]
    condition_rows = [row for row in questionnaire_rows if row.get("questionnaire_type") == "post_condition"]
    selection = _selection_analysis(task_rows)
    decision = _log_mixed_analysis(task_rows, "decision_time_seconds", seed_offset=201)
    adjusted = _holm(
        {
            "selection_success": selection.get("p_value_raw"),
            "decision_time_seconds": decision.get("p_value_raw"),
        }
    )
    selection["p_value_holm"] = adjusted["selection_success"]
    decision["p_value_holm"] = adjusted["decision_time_seconds"]
    interaction = _count_analysis(task_rows, "interaction_count")
    effects = {
        "selection_success": selection,
        "decision_time_seconds": decision,
        "decision_time_timeout_sensitivity": {
            **_participant_paired_analysis(
                task_rows,
                "decision_time_timeout_sensitivity_seconds",
                seed_offset=205,
            ),
            "estimand": "timeout_bound_decision_completion_composite_P2_minus_B0",
            "timeout_bound_seconds": ANALYSIS_PLAN["decision_time_seconds"][
                "nonconfirmation_sensitivity"
            ]["value_for_unconfirmed_trial_seconds"],
            "primary_holm_family": False,
        },
        "interaction_count": interaction,
        "total_action_count": {
            **interaction,
            "compatibility_alias_for": "interaction_count",
        },
        "correction_count": _count_analysis(task_rows, "correction_count"),
        "notebook_ready_time": _log_mixed_analysis(
            task_rows, "notebook_ready_time_seconds", seed_offset=202
        ),
        "end_to_end_launch_time": _log_mixed_analysis(
            task_rows, "end_to_end_seconds", seed_offset=206
        ),
        "seq_ease": _scale_mixed_analysis(
            seq_with_design, SEQ_ITEM_ID, seed_offset=203
        ),
        "sus": _participant_paired_analysis(
            condition_rows, "sus_score", seed_offset=204
        ),
        "custom_items": {
            item: _participant_paired_analysis(
                condition_rows, item, seed_offset=210 + index
            )
            for index, item in enumerate(CUSTOM_ITEM_IDS)
        },
        "holm_family": {
            "family": ["selection_success", "decision_time_seconds"],
            "alpha": ALPHA,
            "method": "holm_two_sided",
        },
    }
    missingness = [
        {
            "outcome": field,
            "condition": condition,
            "missing_count": sum(row.get(field) is None for row in task_rows if row.get("condition") == condition),
            "denominator": sum(row.get("condition") == condition for row in task_rows),
        }
        for field in (
            "decision_time_seconds",
            "notebook_ready_time_seconds",
            "end_to_end_seconds",
        )
        for condition in ("B0", "P2")
    ]
    missingness.extend(
        {
            "outcome": "confirmation",
            "condition": condition,
            "missing_count": sum(
                not bool(row.get("confirmed"))
                for row in task_rows
                if row.get("condition") == condition
            ),
            "denominator": sum(
                row.get("condition") == condition for row in task_rows
            ),
        }
        for condition in ("B0", "P2")
    )
    for condition in ("B0", "P2"):
        selected = [
            row for row in task_rows if row.get("condition") == condition
        ]
        for outcome, reason_field, available_value in (
            (
                "decision_time_seconds",
                "decision_time_unavailability_reason",
                None,
            ),
            (
                "notebook_ready_time_seconds",
                "notebook_ready_time_status",
                "available",
            ),
            ("end_to_end_seconds", "end_to_end_status", "available"),
        ):
            reasons = {
                str(row.get(reason_field))
                for row in selected
                if row.get(reason_field) not in {None, available_value}
            }
            missingness.extend(
                {
                    "outcome": outcome,
                    "condition": condition,
                    "missing_reason": reason,
                    "missing_count": sum(
                        str(row.get(reason_field)) == reason for row in selected
                    ),
                    "denominator": len(selected),
                }
                for reason in sorted(reasons)
            )
    missingness.extend(
        {
            "outcome": "notebook_ready_after_confirmation",
            "condition": condition,
            "missing_count": sum(
                not bool(row.get("notebook_ready_observed"))
                for row in task_rows
                if row.get("condition") == condition and bool(row.get("confirmed"))
            ),
            "denominator": sum(
                row.get("condition") == condition and bool(row.get("confirmed"))
                for row in task_rows
            ),
        }
        for condition in ("B0", "P2")
    )
    missingness.extend(
        {
            "outcome": SEQ_ITEM_ID,
            "condition": condition,
            "missing_count": sum(
                row.get(SEQ_ITEM_ID) is None
                for row in seq_rows
                if row.get("condition") == condition
            ),
            "denominator": sum(
                row.get("condition") == condition for row in seq_rows
            ),
        }
        for condition in ("B0", "P2")
    )
    missingness.extend(
        {
            "outcome": field,
            "condition": condition,
            "missing_count": sum(row.get(field) is None for row in condition_rows if row.get("condition") == condition),
            "denominator": sum(row.get("condition") == condition for row in condition_rows),
        }
        for field in ("sus_score", *CUSTOM_ITEM_IDS)
        for condition in ("B0", "P2")
    )
    preference_rows = [
        row
        for row in questionnaire_rows
        if row.get("questionnaire_type") == "final_preference"
    ]
    missingness.append(
        {
            "outcome": FINAL_PREFERENCE_ID,
            "condition": "ALL",
            "missing_count": sum(
                row.get(FINAL_PREFERENCE_ID) is None for row in preference_rows
            ),
            "denominator": len(preference_rows),
        }
    )
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "execution_status": execution_status,
        "claims_permitted": execution_status == "OBSERVED",
        "observed_rows_present": execution_status in {"OBSERVED", "INCOMPLETE"},
        "synthetic_only": execution_status == "DRY_RUN",
        "analysis_plan_version": ANALYSIS_PLAN_VERSION,
        "analysis_plan_sha256": ANALYSIS_PLAN_SHA256,
        "participant_flow": participant_flow,
        "condition_summary": _condition_summary(task_rows, questionnaire_rows),
        "design_diagnostics": _design_diagnostics(task_rows),
        "effects": effects,
        "primary_inference_registry": _primary_inference_registry(
            selection, decision
        ),
        "preference": _preference_summary(questionnaire_rows),
        "missingness": missingness,
    }


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as handle:
        handle.write(content)
    return path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _csv_text(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return "status\nNOT_EXECUTED\n"
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                field: json.dumps(value, separators=(",", ":"))
                if isinstance(value, (list, dict))
                else value
                for field, value in row.items()
            }
        )
    return buffer.getvalue()


def _svg_placeholder(title: str, status: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="300" viewBox="0 0 900 300">'
        '<rect width="900" height="300" fill="#ffffff"/>'
        f'<text x="450" y="125" text-anchor="middle" font-family="sans-serif" font-size="24">{title}</text>'
        f'<text x="450" y="175" text-anchor="middle" font-family="sans-serif" font-size="18" fill="#64748b">{status}</text>'
        "</svg>\n"
    )


def _bar_svg(title: str, labels: Sequence[str], values: Sequence[float | None], *, maximum: float | None = None) -> str:
    observed = [value for value in values if value is not None and math.isfinite(value)]
    if not observed:
        return _svg_placeholder(title, "NOT_EXECUTED / unavailable")
    from matplotlib import rc_context
    from matplotlib.backends.backend_svg import FigureCanvasSVG
    from matplotlib.figure import Figure

    axis_max = maximum or max(observed) or 1.0
    with rc_context({"svg.hashsalt": "protocol-v5-e3", "font.family": "DejaVu Sans"}):
        figure = Figure(figsize=(9, max(3.0, 0.58 * len(labels) + 1.6)))
        canvas = FigureCanvasSVG(figure)
        axis = figure.subplots()
        positions = list(range(len(labels)))
        for position, value in zip(positions, values):
            if value is not None and math.isfinite(value):
                axis.barh(position, value, color="#2563eb")
                axis.text(
                    min(value + axis_max * 0.015, axis_max * 1.12),
                    position,
                    f"{value:.3f}",
                    va="center",
                    fontsize=9,
                )
            else:
                axis.text(axis_max * 0.015, position, "unavailable", va="center", fontsize=9)
        axis.set_yticks(positions, labels=labels)
        axis.set_xlim(0, axis_max * 1.18)
        axis.invert_yaxis()
        axis.set_title(title)
        axis.spines[["top", "right"]].set_visible(False)
        figure.tight_layout()
        buffer = io.StringIO()
        canvas.print_svg(buffer, metadata={"Date": None, "Creator": REPORTING_VERSION})
        return buffer.getvalue()


_PSEUDONYM_PATTERN = re.compile(r"\bP-[0-9a-f]{12}\b", re.IGNORECASE)
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IP_PATTERN = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
_DIRECT_KEY_PATTERN = re.compile(
    r'(?:"(?:full_name|email|username|account_id|participant_id|session_id|ip_address)"\s*:'
    r'|^(?:full_name|email|username|account_id|participant_id|session_id|ip_address)(?:,|$)'
    r'|,(?:full_name|email|username|account_id|participant_id|session_id|ip_address)(?:,|$))',
    re.IGNORECASE | re.MULTILINE,
)
_FREE_TEXT_KEY_PATTERN = re.compile(
    r"\b(?:comment|comments|free_text|participant_notes|response_text)\b",
    re.IGNORECASE,
)
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:^|[\s\"'=])(?:/(?:Users|home|tmp|private|Volumes)/[^\s\"']+|[A-Za-z]:\\[^\r\n\"']+)",
    re.MULTILINE,
)


def audit_report_privacy(paths: Sequence[Path]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for code, pattern in (
            ("PSEUDONYM", _PSEUDONYM_PATTERN),
            ("EMAIL", _EMAIL_PATTERN),
            ("IP_ADDRESS", _IP_PATTERN),
            ("DIRECT_IDENTIFIER_KEY", _DIRECT_KEY_PATTERN),
            ("FREE_TEXT_FIELD", _FREE_TEXT_KEY_PATTERN),
            ("ABSOLUTE_PATH", _ABSOLUTE_PATH_PATTERN),
        ):
            if pattern.search(text):
                violations.append({"file": path.name, "code": code})
    if violations:
        raise UserStudyAnalysisError(
            "aggregate report privacy audit failed: " + ", ".join(item["code"] for item in violations)
        )
    return {
        "schema_version": "protocol-v5-user-study-report-privacy-audit-v1.0.0",
        "status": "PASS",
        "files_scanned": len(paths),
        "direct_identifier_findings": 0,
        "checks": [
            "pseudonym",
            "email",
            "IP address",
            "direct identifier field",
            "free-text response field",
            "absolute local path",
        ],
    }


def _display(value: object, digits: int = 3) -> str:
    numeric = _finite(value)
    return "unavailable" if numeric is None else f"{numeric:.{digits}f}"


def _display_ci(value: object) -> str:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        return "unavailable"
    return f"[{_display(value[0])}, {_display(value[1])}]"


def _report_markdown(analysis: Mapping[str, Any]) -> str:
    status = str(analysis["execution_status"])
    guard = {
        "NOT_EXECUTED": "No real study exports were supplied. Every empirical result is NOT_EXECUTED; no zero-valued estimate or denominator was substituted.",
        "DRY_RUN": "This package contains clearly labeled SYNTHETIC DRY_RUN inputs and supports no empirical claim.",
        "INCOMPLETE": "This is an incomplete observed collection. Results are descriptive under the frozen plan and must not be represented as the completed confirmatory study.",
        "OBSERVED": "Inference is bounded to the frozen study population, tasks, catalog, and controlled environment.",
    }[status]
    lines = [
        "# Protocol-v5 E3 B0-versus-P2 user-study report",
        "",
        f"Evidence status: `{status}`.",
        "",
        guard,
        "",
        "## Frozen analysis contract",
        "",
        "SelectionSuccess and DecisionTime are the two co-primary outcomes. Their two-sided p-values alone form a fixed-size two-hypothesis Holm family at alpha 0.05, including when one endpoint is unavailable. Interaction effort, corrections, notebook readiness, questionnaires, preference, and the frozen timeout-bound non-confirmation analysis are secondary. The three CUSTOM Likert items are reported separately and are not SUS dimensions.",
        "",
        f"Analysis plan: `{analysis['analysis_plan_version']}` (`{analysis['analysis_plan_sha256']}`).",
        "",
        "Participant is the clustered/random sampling structure; the three frozen matched task pairs are fixed repeated factors only for task-level outcomes. Counterbalance cell is a coverage diagnostic because it is redundant with the frozen condition-order, variant, period, and position design.",
        "",
        "## Participant flow",
        "",
        "| Stage | Count |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {row['stage']} | {row['count']} |" for row in analysis["participant_flow"]
    )
    lines.extend(["", "## Condition summary", ""])
    if not analysis["condition_summary"]:
        lines.append("NOT_EXECUTED — no empirical condition estimates are available.")
    else:
        lines.extend(
            [
                "| Condition | Participants | Tasks | SelectionSuccess | Decision time, mean s | Actions, mean | Corrections, mean | SEQ | SUS |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in analysis["condition_summary"]:
            lines.append(
                "| {condition} | {participants} | {tasks} | {success} | {time} | {actions} | {corrections} | {seq} | {sus} |".format(
                    condition=row["condition"],
                    participants=row["participant_count"],
                    tasks=row["measured_task_count"],
                    success=_display(row["selection_success_rate"]),
                    time=_display(row["decision_time_mean_seconds"]),
                    actions=_display(row["total_action_mean"]),
                    corrections=_display(row["correction_mean"]),
                    seq=_display(row["seq_mean"]),
                    sus=_display(row["sus_mean"]),
                )
            )
    lines.extend(["", "## Effect estimates", ""])
    effects = analysis["effects"]
    if not effects:
        lines.append("NOT_EXECUTED — effect sizes, confidence intervals, and p-values are unavailable.")
    else:
        selection = effects["selection_success"]
        decision = effects["decision_time_seconds"]
        lines.extend(
            [
                f"- SelectionSuccess: P2-minus-B0 risk difference {_display(selection.get('risk_difference'))}, 95% CI {_display_ci(selection.get('risk_difference_ci_95'))}; odds ratio {_display(selection.get('odds_ratio'))}, 95% CI {_display_ci(selection.get('odds_ratio_ci_95'))}; Holm p {_display(selection.get('p_value_holm'))}. Method status: `{selection.get('status')}`.",
                f"- DecisionTime: geometric-mean ratio {_display(decision.get('geometric_mean_ratio'))}, 95% CI {_display_ci(decision.get('geometric_mean_ratio_ci_95'))}; raw paired P2-minus-B0 difference {_display(decision['raw_paired_effect'].get('mean_difference'))} seconds, 95% CI {_display_ci(decision['raw_paired_effect'].get('confidence_interval_95'))}; Holm p {_display(decision.get('p_value_holm'))}. Method status: `{decision.get('status')}`.",
            ]
        )
        sensitivity = effects["decision_time_timeout_sensitivity"]
        lines.append(
            f"- DecisionTime non-confirmation sensitivity: every measured trial is retained; an unconfirmed trial uses only the predeclared 600-second task-timeout bound. Paired P2-minus-B0 difference {_display(sensitivity.get('mean_difference'))} seconds, 95% CI {_display_ci(sensitivity.get('confidence_interval_95'))}. This sensitivity is not in the primary Holm family."
        )
        for key, label in (
            ("total_action_count", "Total actions"),
            ("correction_count", "Corrections"),
        ):
            result = effects[key]
            lines.append(
                f"- {label}: incidence-rate ratio {_display(result.get('incidence_rate_ratio'))}, 95% CI {_display_ci(result.get('confidence_interval_95'))}; paired P2-minus-B0 difference {_display(result.get('paired_effect', {}).get('mean_difference'))}, paired 95% CI {_display_ci(result.get('paired_effect', {}).get('confidence_interval_95'))}. Method status: `{result.get('status')}`."
            )
        readiness = effects["notebook_ready_time"]
        lines.append(
            f"- Notebook-ready time (`notebook_ready - confirmed_environment`): geometric-mean ratio {_display(readiness.get('geometric_mean_ratio'))}, 95% CI {_display_ci(readiness.get('geometric_mean_ratio_ci_95'))}; raw paired difference {_display(readiness['raw_paired_effect'].get('mean_difference'))} seconds, 95% CI {_display_ci(readiness['raw_paired_effect'].get('confidence_interval_95'))}. Method status: `{readiness.get('status')}`."
        )
        end_to_end = effects["end_to_end_launch_time"]
        lines.append(
            f"- End-to-end launch time (`notebook_ready - task_shown`, separately labeled): geometric-mean ratio {_display(end_to_end.get('geometric_mean_ratio'))}, 95% CI {_display_ci(end_to_end.get('geometric_mean_ratio_ci_95'))}; raw paired difference {_display(end_to_end['raw_paired_effect'].get('mean_difference'))} seconds, 95% CI {_display_ci(end_to_end['raw_paired_effect'].get('confidence_interval_95'))}. Method status: `{end_to_end.get('status')}`."
        )
        p2_summary = next(
            (
                row
                for row in analysis["condition_summary"]
                if row["condition"] == "P2"
            ),
            {},
        )
        lines.append(
            f"- P2 final override rate among confirmed P2 trials: {_display(p2_summary.get('final_override_rate_among_confirmed'))}, participant-bootstrap 95% CI {_display_ci(p2_summary.get('final_override_rate_ci_95'))}. B0 has no recommendation or override metric."
        )
        seq = effects["seq_ease"]
        seq_effect = seq.get("paired_effect", seq)
        lines.append(
            f"- SEQ ease: paired P2-minus-B0 difference {_display(seq_effect.get('mean_difference'))}, standardized paired effect {_display(seq_effect.get('cohens_dz'))}, 95% CI {_display_ci(seq_effect.get('confidence_interval_95'))}. Method status: `{seq.get('status')}`."
        )
        sus = effects["sus"]
        lines.append(
            f"- SUS: paired P2-minus-B0 difference {_display(sus.get('mean_difference'))}, standardized paired effect {_display(sus.get('cohens_dz'))}, 95% CI {_display_ci(sus.get('confidence_interval_95'))}."
        )
        for item, result in effects["custom_items"].items():
            lines.append(
                f"- CUSTOM `{item}`: paired P2-minus-B0 difference {_display(result.get('mean_difference'))}, standardized paired effect {_display(result.get('cohens_dz'))}, 95% CI {_display_ci(result.get('confidence_interval_95'))}."
            )
    lines.extend(["", "## Final preference", ""])
    if not analysis["preference"]:
        lines.append("NOT_EXECUTED — no preference denominator was invented.")
    else:
        lines.extend(
            [
                "| Preference | Count | Answered denominator | Percent | Simultaneous 95% CI | Missing |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in analysis["preference"]:
            lines.append(
                f"| {row['preference']} | {row['count']} | {row['answered_denominator']} | {_display(row['percentage'])} | {_display_ci(row['simultaneous_confidence_interval_95'])} | {row['missing_response_count']} |"
            )
    lines.extend(
        [
            "",
            "## Missing responses and limitations",
            "",
            "Missing responses are never imputed. A missing SUS item makes that condition's SUS score unavailable; an unanswered preference is not recoded as no preference. Unconfirmed trials remain in task/confirmation denominators and have a coded DecisionTime-unavailability reason. Detailed endpoint denominators are in `tables/missingness.csv`.",
            "",
            "Session exclusions are restricted to the frozen reason registry and are never selected from observed performance or significance. Generalization beyond the recruited population, frozen tasks, catalog, and controlled Hub is unsupported.",
            "",
        ]
    )
    return "\n".join(lines)


def write_analysis_artifacts(root: Path, analysis: Mapping[str, Any]) -> tuple[Path, ...]:
    """Write aggregate tables, figures, report, and privacy audit exclusively."""

    report_dir = root / "report"
    derived_dir = root / "derived"
    status = str(analysis["execution_status"])
    paths: list[Path] = []
    paths.append(_write(derived_dir / "analysis.json", _json_text(analysis)))
    paths.append(_write(derived_dir / "model-effects.json", _json_text(analysis["effects"])))
    table_specs = {
        "participant-flow.csv": analysis["participant_flow"],
        "condition-summary.csv": analysis["condition_summary"],
        "preference.csv": analysis["preference"],
        "missingness.csv": analysis["missingness"],
    }
    for filename, rows in table_specs.items():
        paths.append(_write(report_dir / "tables" / filename, _csv_text(rows)))
        paths.append(_write(report_dir / "tables" / filename.replace(".csv", ".json"), _json_text(rows)))
    summaries = {row["condition"]: row for row in analysis["condition_summary"]}
    b0, p2 = summaries.get("B0", {}), summaries.get("P2", {})
    figure_specs = {
        "accuracy.svg": ("SelectionSuccess", ["B0", "P2"], [b0.get("selection_success_rate"), p2.get("selection_success_rate")], 1.0),
        "decision-time.svg": ("Decision time (mean seconds)", ["B0", "P2"], [b0.get("decision_time_mean_seconds"), p2.get("decision_time_mean_seconds")], None),
        "interaction-effort.svg": ("Interaction effort", ["B0 actions", "P2 actions", "B0 corrections", "P2 corrections"], [b0.get("total_action_mean"), p2.get("total_action_mean"), b0.get("correction_mean"), p2.get("correction_mean")], None),
        "sus-seq-custom.svg": ("SUS, SEQ, and clearly labeled CUSTOM items (% scale maximum)", ["B0 SUS", "P2 SUS", "B0 SEQ", "P2 SEQ", "B0 CUSTOM confidence", "P2 CUSTOM confidence", "B0 CUSTOM natural expression", "P2 CUSTOM natural expression", "B0 CUSTOM convenience", "P2 CUSTOM convenience"], [b0.get("sus_mean"), p2.get("sus_mean"), (b0.get("seq_mean") / 7 * 100 if b0.get("seq_mean") is not None else None), (p2.get("seq_mean") / 7 * 100 if p2.get("seq_mean") is not None else None), *((value / 7 * 100 if value is not None else None) for value in (b0.get(f"{CUSTOM_ITEM_IDS[0]}_mean"), p2.get(f"{CUSTOM_ITEM_IDS[0]}_mean"), b0.get(f"{CUSTOM_ITEM_IDS[1]}_mean"), p2.get(f"{CUSTOM_ITEM_IDS[1]}_mean"), b0.get(f"{CUSTOM_ITEM_IDS[2]}_mean"), p2.get(f"{CUSTOM_ITEM_IDS[2]}_mean")))], 100.0),
        "preference.svg": ("Final preference (percent of answered)", [row["preference"] for row in analysis["preference"]], [row["percentage"] for row in analysis["preference"]], 100.0),
    }
    for filename, (title, labels, values, maximum) in figure_specs.items():
        content = (
            _svg_placeholder(
                title,
                "NOT_EXECUTED — no real consented participant data available",
            )
            if status == "NOT_EXECUTED"
            else _bar_svg(title, labels, values, maximum=maximum)
        )
        paths.append(_write(report_dir / "figures" / filename, content))
    paths.append(
        _write(
            report_dir / "USER_STUDY_REPORT.md",
            _report_markdown(analysis),
        )
    )
    manifest_path = report_dir / "analysis-manifest.json"
    audit_path = report_dir / "privacy-audit.json"
    manifest = {
        "schema_version": REPORTING_VERSION,
        "execution_status": status,
        "analysis_plan_version": ANALYSIS_PLAN_VERSION,
        "analysis_plan_sha256": ANALYSIS_PLAN_SHA256,
        "python_version": platform.python_version(),
        "supported_python": SUPPORTED_PYTHON,
        "analysis_dependencies": analysis_dependency_versions(),
        "pinned_analysis_dependencies": PINNED_ANALYSIS_DEPENDENCIES,
        "figure_renderer": "matplotlib_svg",
        "bootstrap_contract": {
            "resampling_unit": ANALYSIS_PLAN["bootstrap_resampling_unit"],
            "rng": ANALYSIS_PLAN["bootstrap_rng"],
            "seed": ANALYSIS_PLAN["bootstrap_seed"],
            "replicates": BOOTSTRAP_REPLICATES,
            "ci": ANALYSIS_PLAN["bootstrap_ci"],
            "minimum_success_fraction": ANALYSIS_PLAN[
                "bootstrap_minimum_success_fraction"
            ],
            "failure_policy": ANALYSIS_PLAN["bootstrap_failure_policy"],
        },
        "generated_files": sorted(
            str(path.relative_to(root))
            for path in (*paths, manifest_path, audit_path)
        ),
        "generated_file_sha256": {
            str(path.relative_to(root)): _file_sha256(path)
            for path in sorted(paths)
        },
    }
    paths.append(_write(manifest_path, _json_text(manifest)))
    report_paths = sorted(
        path
        for path in report_dir.rglob("*")
        if path.is_file() and path != audit_path
    )
    aggregate_paths = sorted(
        {
            *report_paths,
            derived_dir / "analysis.json",
            derived_dir / "model-effects.json",
        }
    )
    audit = audit_report_privacy(aggregate_paths)
    paths.append(_write(audit_path, _json_text(audit)))
    return tuple(paths)


__all__ = [
    "ANALYSIS_SCHEMA_VERSION",
    "PINNED_ANALYSIS_DEPENDENCIES",
    "UserStudyAnalysisError",
    "analyze_user_study",
    "analysis_dependency_versions",
    "audit_report_privacy",
    "validate_analysis_dependencies",
    "write_analysis_artifacts",
]
