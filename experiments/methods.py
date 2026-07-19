"""Comparable resource-selection methods for evaluation runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from recommender.recommender import recommend_profile


METHODS = ("static_manual", "intent_only", "context_aware")
APPROVED_PROFILES = ("small", "medium", "large")
PROFILE_ORDER = {profile: index for index, profile in enumerate(APPROVED_PROFILES)}

CONTEXT_SIGNAL_TERMS = (
    "pandas",
    "read_csv",
    "dataframe",
    "csv",
    "parquet",
    "train",
    "fit",
    "sklearn",
    "model",
    "torch",
    "tensorflow",
    "cuda",
    "gpu",
    "deep learning",
)


@dataclass(frozen=True)
class MethodDecision:
    recommended_profile: str | None
    applied_profile: str | None
    recommendation_reasons: list[str]
    policy_warnings: list[str]
    context_signal_summary: dict[str, Any]


def summarize_context_signals(
    code_context_hints: list[str],
    *,
    raw_context_available: bool,
    dataset_size_signal_used: bool,
) -> dict[str, Any]:
    text = "\n".join(code_context_hints).lower()
    detected = [term for term in CONTEXT_SIGNAL_TERMS if term in text]
    return {
        "raw_context_stored": False,
        "raw_context_available": raw_context_available,
        "hint_count": len(code_context_hints) if raw_context_available else 0,
        "detected_terms": detected if raw_context_available else [],
        "dataset_size_signal_used": dataset_size_signal_used,
    }


def _apply_policy(recommended_profile: str | None, workload: dict[str, Any]) -> tuple[str | None, list[str]]:
    if recommended_profile is None:
        return None, []

    mapped_profile = "large" if recommended_profile == "gpu_or_large" else recommended_profile
    warnings: list[str] = []
    policy = workload.get("policy_constraints", {})
    allowed = policy.get("allowed_profiles")
    disallowed = set(policy.get("disallowed_profiles", []))

    if recommended_profile in disallowed or mapped_profile in disallowed:
        warnings.append(f"recommended profile {recommended_profile} is disallowed by workload policy")

    if allowed and mapped_profile not in allowed:
        for candidate in reversed(APPROVED_PROFILES):
            if candidate in allowed:
                warnings.append(f"applied profile changed from {mapped_profile} to {candidate} due to policy")
                return candidate, warnings
        warnings.append("no allowed profile was available to apply")
        return None, warnings

    return mapped_profile, warnings


def _static_manual_profile(workload: dict[str, Any], applied_profile: str | None) -> tuple[str | None, list[str]]:
    if applied_profile:
        selected, warnings = _apply_policy(applied_profile, workload)
        return selected, warnings

    acceptable = [
        profile
        for profile in workload.get("expected_acceptable_profiles", [])
        if profile in APPROVED_PROFILES
    ]
    if not acceptable:
        return "small", ["no approved acceptable profile was listed; static/manual policy defaulted to small"]

    selected = min(acceptable, key=lambda profile: PROFILE_ORDER[profile])
    selected, warnings = _apply_policy(selected, workload)
    return selected, warnings


def decide_method(
    method: str,
    workload: dict[str, Any],
    *,
    applied_profile: str | None = None,
) -> MethodDecision:
    """Return the method-specific recommendation and applied profile.

    static_manual is a deterministic stand-in for an operator or user choosing
    from the approved static profiles: it selects the smallest approved profile
    listed as acceptable by the benchmark manifest. That policy avoids an
    obviously wrong baseline while keeping resource use conservative.
    """

    if method not in METHODS:
        raise ValueError(f"unsupported method {method!r}")

    if method == "static_manual":
        selected, warnings = _static_manual_profile(workload, applied_profile)
        return MethodDecision(
            recommended_profile=None,
            applied_profile=selected,
            recommendation_reasons=[
                "static/manual deterministic policy selected the smallest approved acceptable profile"
            ],
            policy_warnings=warnings,
            context_signal_summary=summarize_context_signals(
                [],
                raw_context_available=False,
                dataset_size_signal_used=False,
            ),
        )

    if method == "intent_only":
        rec = recommend_profile(
            intent=workload["intent"],
            dataset_size_gb=0.0,
            code_context="",
        )
        selected, warnings = _apply_policy(rec.profile, workload)
        return MethodDecision(
            recommended_profile=rec.profile,
            applied_profile=selected,
            recommendation_reasons=rec.reasons,
            policy_warnings=warnings,
            context_signal_summary=summarize_context_signals(
                [],
                raw_context_available=False,
                dataset_size_signal_used=False,
            ),
        )

    code_context_hints = list(workload["code_context_hints"])
    rec = recommend_profile(
        intent=workload["intent"],
        dataset_size_gb=workload["dataset_size_hint_gb"],
        code_context="\n".join(code_context_hints),
    )
    selected, warnings = _apply_policy(rec.profile, workload)
    return MethodDecision(
        recommended_profile=rec.profile,
        applied_profile=selected,
        recommendation_reasons=rec.reasons,
        policy_warnings=warnings,
        context_signal_summary=summarize_context_signals(
            code_context_hints,
            raw_context_available=True,
            dataset_size_signal_used=True,
        ),
    )
