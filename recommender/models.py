"""Shared input and output contracts for spawn recommendation backends."""

from __future__ import annotations

from dataclasses import dataclass


POLICY_VERSION = "resource-image-policy-v1"
SCHEMA_VERSION = "spawn-recommendation-v1"


@dataclass(frozen=True)
class RecommendationRequest:
    """Permitted pre-spawn context supplied to a recommender."""

    intent: str = ""
    dataset_size_gb: float | int | str | None = 0.0
    code_context: str = ""


@dataclass(frozen=True)
class SpawnRecommendation:
    """Backend-neutral recommendation consumed by UI and policy layers."""

    profile: str
    reasons: list[str]
    score: int | float | None
    image_id: str
    image_reference: str
    image_reasons: list[str]
    catalog_version: str
    policy_version: str = POLICY_VERSION
    schema_version: str = SCHEMA_VERSION
    backend_name: str = "rule_based"
    backend_version: str = "rule-based-v1"

    def to_dict(self) -> dict[str, object]:
        """Return the exact legacy serialization used by existing callers."""

        return {
            "profile": self.profile,
            "reasons": self.reasons,
            "score": self.score,
            "image_id": self.image_id,
            "image_reference": self.image_reference,
            "image_reasons": self.image_reasons,
            "catalog_version": self.catalog_version,
            "policy_version": self.policy_version,
        }

    def to_unified_dict(self) -> dict[str, object]:
        """Return the versioned schema shared by every backend."""

        payload = self.to_dict()
        payload.update(
            {
                "schema_version": self.schema_version,
                "backend_name": self.backend_name,
                "backend_version": self.backend_version,
            }
        )
        return payload


# Backward-compatible public name.
Recommendation = SpawnRecommendation
