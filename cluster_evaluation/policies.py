"""Operational method policies for the Kubernetes evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from experiments.methods import decide_method


METHODS = ("static_default", "intent_only", "context_aware")
PROFILES = ("small", "medium", "large")
PROFILE_RESOURCES: dict[str, dict[str, str | int]] = {
    "small": {
        "cpu_request": "100m",
        "cpu_limit": "500m",
        "memory_request": "256M",
        "memory_limit": "384M",
        "cpu_request_m": 100,
        "cpu_limit_m": 500,
        "memory_request_mi": 244,
        "memory_limit_mi": 366,
    },
    "medium": {
        "cpu_request": "500m",
        "cpu_limit": "1",
        "memory_request": "768M",
        "memory_limit": "1G",
        "cpu_request_m": 500,
        "cpu_limit_m": 1000,
        "memory_request_mi": 732,
        "memory_limit_mi": 953,
    },
    "large": {
        "cpu_request": "1500m",
        "cpu_limit": "2",
        "memory_request": "1536M",
        "memory_limit": "2G",
        "cpu_request_m": 1500,
        "cpu_limit_m": 2000,
        "memory_request_mi": 1464,
        "memory_limit_mi": 1907,
    },
}


@dataclass(frozen=True)
class Decision:
    recommended_profile: str | None
    applied_profile: str
    recommendation_reasons: list[str]
    policy_warnings: list[str]
    context_signal_summary: dict[str, Any]


def _apply_allowed_profile(profile: str, workload: dict[str, Any]) -> tuple[str, list[str]]:
    policy = workload.get("policy_constraints", {})
    allowed = list(policy.get("allowed_profiles", PROFILES))
    disallowed = set(policy.get("disallowed_profiles", []))
    usable = [candidate for candidate in PROFILES if candidate in allowed and candidate not in disallowed]
    if profile in usable:
        return profile, []
    if not usable:
        raise ValueError(f"{workload['workload_id']} has no usable CPU profile")
    fallback = min(usable, key=lambda candidate: abs(PROFILES.index(candidate) - PROFILES.index(profile)))
    return fallback, [f"fixed default {profile} changed to {fallback} by workload policy"]


def decide_cluster_method(method: str, workload: dict[str, Any]) -> Decision:
    """Decide a method without exposing operational ground truth.

    The fixed baseline receives only the global default and policy constraints.
    Intent-only and context-aware retain the input separation tested by the
    historical runner.
    """

    if method == "static_default":
        applied, warnings = _apply_allowed_profile("medium", workload)
        return Decision(
            recommended_profile="medium",
            applied_profile=applied,
            recommendation_reasons=["fixed deployment default is medium for every workload"],
            policy_warnings=warnings,
            context_signal_summary={
                "raw_context_stored": False,
                "raw_context_available": False,
                "hint_count": 0,
                "detected_terms": [],
                "dataset_size_signal_used": False,
            },
        )
    if method not in {"intent_only", "context_aware"}:
        raise ValueError(f"unsupported cluster method {method!r}")
    decision = decide_method(method, workload)
    if decision.applied_profile not in PROFILES:
        raise ValueError(f"{method} did not produce an applicable CPU profile")
    return Decision(
        recommended_profile=decision.recommended_profile,
        applied_profile=decision.applied_profile,
        recommendation_reasons=decision.recommendation_reasons,
        policy_warnings=decision.policy_warnings,
        context_signal_summary=decision.context_signal_summary,
    )

