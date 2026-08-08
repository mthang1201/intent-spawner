"""Comparable recommender adapters for protocol-v4 offline evaluation."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Mapping

from recommender.models import RecommendationRequest
from recommender.registry import create_recommender
from recommender.reliability import recommend_with_metadata
from recommender.rule_based import RuleBasedRecommender

from .dataset import PROFILE_ORDER


RECOMMENDERS = (
    "static_small",
    "static_large",
    "rule_based_intent_only",
    "rule_based_context",
    "external_llm",
    "self_hosted_llm",
)
DEFAULT_RECOMMENDERS = (
    "static_small",
    "static_large",
    "rule_based_intent_only",
    "rule_based_context",
)


@dataclass(frozen=True)
class EvaluationDecision:
    raw_profile: str | None
    predicted_profile: str | None
    applied_profile: str | None
    predicted_image_id: str | None
    requested_backend: str
    effective_backend: str
    backend_version: str
    model_id: str | None
    policy_compliant: bool
    fallback_used: bool
    fallback_error_category: str | None
    attempt_count: int
    latency_seconds: float | None
    error_category: str | None
    execution_mode: str


def _normalize_profile(profile: str | None) -> str | None:
    if profile == "gpu_or_large":
        return "large"
    return profile if profile in PROFILE_ORDER else None


def _nearest_allowed(profile: str, allowed: list[str]) -> str:
    target = PROFILE_ORDER[profile]
    return min(
        allowed,
        key=lambda candidate: (
            abs(PROFILE_ORDER[candidate] - target),
            PROFILE_ORDER[candidate],
        ),
    )


def _apply_policy(
    *,
    raw_profile: str | None,
    image_id: str | None,
    item: Mapping[str, Any],
    catalog_images: Mapping[str, Any],
) -> tuple[str | None, str | None, bool]:
    normalized = _normalize_profile(raw_profile)
    policy = item["policy_constraints"]
    allowed = list(policy["allowed_profiles"])
    profile_valid = normalized is not None and normalized in allowed
    image_valid = isinstance(image_id, str) and image_id in catalog_images
    applied = normalized if profile_valid else (
        _nearest_allowed(normalized, allowed) if normalized is not None and allowed else None
    )
    return normalized, applied, profile_valid and image_valid


def _request_for(method: str, item: Mapping[str, Any]) -> RecommendationRequest:
    inputs = item["inputs"]
    if method == "rule_based_intent_only":
        return RecommendationRequest(
            intent=str(inputs["intent"]),
            dataset_size_gb=0.0,
            code_context="",
        )
    return RecommendationRequest(
        intent=str(inputs["intent"]),
        dataset_size_gb=inputs["dataset_size_gb"],
        code_context="\n".join(inputs["code_context_hints"]),
    )


def create_backend(method: str) -> Any:
    if method not in RECOMMENDERS:
        raise ValueError(f"unsupported recommender {method!r}")
    if method in {"static_small", "static_large"}:
        return None
    if method in {"rule_based_intent_only", "rule_based_context"}:
        return RuleBasedRecommender()
    return create_recommender(method)


def evaluate_item(
    method: str,
    item: Mapping[str, Any],
    *,
    backend: Any,
    catalog_images: Mapping[str, Any],
) -> EvaluationDecision:
    """Execute one method without leaking gold labels into its inputs."""

    started = time.monotonic()
    if method in {"static_small", "static_large"}:
        raw_profile = "small" if method == "static_small" else "large"
        image_id = "minimal-python"
        predicted, applied, compliant = _apply_policy(
            raw_profile=raw_profile,
            image_id=image_id,
            item=item,
            catalog_images=catalog_images,
        )
        return EvaluationDecision(
            raw_profile=raw_profile,
            predicted_profile=predicted,
            applied_profile=applied,
            predicted_image_id=image_id,
            requested_backend=method,
            effective_backend=method,
            backend_version="evaluation-static-baseline-v1",
            model_id=None,
            policy_compliant=compliant,
            fallback_used=False,
            fallback_error_category=None,
            attempt_count=0,
            latency_seconds=max(0.0, time.monotonic() - started),
            error_category=None,
            execution_mode="deterministic_local",
        )

    request = _request_for(method, item)
    result = recommend_with_metadata(backend, request)
    recommendation = result.recommendation
    metadata = result.metadata
    predicted, applied, compliant = _apply_policy(
        raw_profile=recommendation.profile,
        image_id=recommendation.image_id,
        item=item,
        catalog_images=catalog_images,
    )
    return EvaluationDecision(
        raw_profile=recommendation.profile,
        predicted_profile=predicted,
        applied_profile=applied,
        predicted_image_id=recommendation.image_id,
        requested_backend=metadata.requested_backend,
        effective_backend=metadata.effective_backend,
        backend_version=recommendation.backend_version,
        model_id=(
            str(backend.config.model)
            if method in {"external_llm", "self_hosted_llm"}
            else None
        ),
        policy_compliant=compliant,
        fallback_used=metadata.fallback_used,
        fallback_error_category=metadata.fallback_error_category,
        attempt_count=metadata.attempt_count,
        latency_seconds=metadata.total_elapsed_seconds,
        error_category=None,
        execution_mode=(
            "live_backend"
            if method in {"external_llm", "self_hosted_llm"}
            else "deterministic_local"
        ),
    )


def error_decision(method: str, error: Exception, elapsed: float) -> EvaluationDecision:
    """Represent a failed call without persisting free-form error text or secrets."""

    category = type(error).__name__
    return EvaluationDecision(
        raw_profile=None,
        predicted_profile=None,
        applied_profile=None,
        predicted_image_id=None,
        requested_backend=method,
        effective_backend="unavailable",
        backend_version="unavailable",
        model_id=None,
        policy_compliant=False,
        fallback_used=False,
        fallback_error_category=None,
        attempt_count=0,
        latency_seconds=max(0.0, elapsed),
        error_category=category,
        execution_mode=(
            "live_backend"
            if method in {"external_llm", "self_hosted_llm"}
            else "deterministic_local"
        ),
    )


__all__ = [
    "DEFAULT_RECOMMENDERS",
    "EvaluationDecision",
    "RECOMMENDERS",
    "create_backend",
    "error_decision",
    "evaluate_item",
]
