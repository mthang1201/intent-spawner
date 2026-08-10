"""Comparable recommender adapters for protocol-v4 offline evaluation."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Mapping

from recommender.models import RecommendationRequest
from recommender.registry import create_recommender
from recommender.reliability import recommend_with_metadata
from recommender.rule_based import RuleBasedRecommender

from .dataset import PROFILE_ORDER, normalize_profile


RECOMMENDERS = (
    "static_profile_baseline",
    "rule_based_mapping",
    "external_llm",
    "self_hosted_local_ollama_llm",
    "static_small",
    "static_large",
    "rule_based_intent_only",
    "rule_based_context",
    "self_hosted_llm",
    "static_default",
)
DEFAULT_RECOMMENDERS = (
    "static_profile_baseline",
    "rule_based_mapping",
    "external_llm",
    "self_hosted_local_ollama_llm",
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
    raw_response: str | None = None
    parsed_profile: str | None = None
    parsed_image_id: str | None = None
    validation_error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
    inference_latency_seconds: float | None = None


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
    normalized = normalize_profile(raw_profile)
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
    if method in {"static_profile_baseline", "static_default", "static_small", "static_large"}:
        return None
    if method in {"rule_based_intent_only", "rule_based_context", "rule_based_mapping"}:
        return RuleBasedRecommender()
    if method == "self_hosted_local_ollama_llm":
        return create_recommender("self_hosted_local_ollama_llm")
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
    if method in {"static_profile_baseline", "static_default", "static_small", "static_large"}:
        if method in {"static_profile_baseline", "static_default"}:
            # Authoritative single operational baseline (frozen to medium per repository policy)
            raw_profile = "medium"
        elif method == "static_small":
            raw_profile = "small"
        else:
            raw_profile = "large"

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
            raw_response=None,
            parsed_profile=raw_profile,
            parsed_image_id=image_id,
            validation_error=None,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            estimated_cost_usd=None,
            inference_latency_seconds=None,
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
        raw_profile=metadata.parsed_profile if metadata.fallback_used else recommendation.profile,
        predicted_profile=predicted,
        applied_profile=applied,
        predicted_image_id=recommendation.image_id,
        requested_backend=metadata.requested_backend,
        effective_backend=metadata.effective_backend,
        backend_version=recommendation.backend_version,
        model_id=(
            str(backend.config.model)
            if method in {"external_llm", "self_hosted_llm", "self_hosted_local_ollama_llm"}
            and hasattr(backend, "config")
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
            if method in {"external_llm", "self_hosted_llm", "self_hosted_local_ollama_llm"}
            else "deterministic_local"
        ),
        raw_response=metadata.raw_response,
        parsed_profile=metadata.parsed_profile if metadata.fallback_used else (metadata.parsed_profile or recommendation.profile),
        parsed_image_id=metadata.parsed_image_id if metadata.fallback_used else (metadata.parsed_image_id or recommendation.image_id),
        validation_error=metadata.validation_error,
        prompt_tokens=metadata.prompt_tokens,
        completion_tokens=metadata.completion_tokens,
        total_tokens=metadata.total_tokens,
        estimated_cost_usd=metadata.estimated_cost_usd,
        inference_latency_seconds=metadata.inference_latency_seconds,
    )


def error_decision(
    method: str,
    error: Exception,
    elapsed: float,
    *,
    model_id: str | None = None,
    error_category: str | None = None,
) -> EvaluationDecision:
    """Represent a failed call without persisting free-form error text or secrets."""

    category = error_category or (
        str(error)
        if isinstance(error, RuntimeError) and not str(error).startswith("<")
        else type(error).__name__
    )
    return EvaluationDecision(
        raw_profile=None,
        predicted_profile=None,
        applied_profile=None,
        predicted_image_id=None,
        requested_backend=method,
        effective_backend="unavailable",
        backend_version="unavailable",
        model_id=model_id,
        policy_compliant=False,
        fallback_used=False,
        fallback_error_category=None,
        attempt_count=0,
        latency_seconds=max(0.0, elapsed),
        error_category=category,
        execution_mode=(
            "live_backend"
            if method in {"external_llm", "self_hosted_llm", "self_hosted_local_ollama_llm"}
            else "deterministic_local"
        ),
        raw_response=None,
        parsed_profile=None,
        parsed_image_id=None,
        validation_error=str(category),
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        estimated_cost_usd=None,
        inference_latency_seconds=None,
    )


__all__ = [
    "DEFAULT_RECOMMENDERS",
    "EvaluationDecision",
    "RECOMMENDERS",
    "create_backend",
    "error_decision",
    "evaluate_item",
]
