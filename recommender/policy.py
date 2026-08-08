"""Backend-neutral validation at the recommender-to-policy boundary."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from .models import POLICY_VERSION, SCHEMA_VERSION, SpawnRecommendation


@dataclass(frozen=True)
class PolicyValidator:
    """Validate a recommendation against deployment-owned profile/image policy."""

    profiles: frozenset[str]
    image_references: Mapping[str, str]
    catalog_version: str
    policy_version: str = POLICY_VERSION
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_catalog(
        cls,
        *,
        profiles: Collection[str],
        catalog: Mapping[str, Any],
        policy_version: str = POLICY_VERSION,
    ) -> "PolicyValidator":
        """Build a validator from the administrator-owned image catalog."""

        images = catalog.get("images")
        catalog_version = catalog.get("catalog_version")
        if not isinstance(images, Mapping) or not images:
            raise ValueError("policy validator requires a non-empty image catalog")
        if not isinstance(catalog_version, str) or not catalog_version:
            raise ValueError("policy validator requires a catalog version")
        image_references = {
            image_id: item.get("reference")
            for image_id, item in images.items()
            if isinstance(image_id, str) and isinstance(item, Mapping)
        }
        if len(image_references) != len(images) or not all(
            isinstance(reference, str) and reference
            for reference in image_references.values()
        ):
            raise ValueError("policy validator requires image references for every catalog entry")
        return cls(
            profiles=frozenset(profiles),
            image_references=image_references,
            catalog_version=catalog_version,
            policy_version=policy_version,
        )

    def validate(self, recommendation: SpawnRecommendation) -> SpawnRecommendation:
        """Return an accepted recommendation or raise on a policy/schema mismatch."""

        if not isinstance(recommendation, SpawnRecommendation):
            raise TypeError("configured backend returned an invalid recommendation type")
        if recommendation.schema_version != self.schema_version:
            raise ValueError("recommendation uses an unsupported schema version")
        if recommendation.profile not in self.profiles:
            raise ValueError("recommended profile is not recognized by deployment policy")
        if recommendation.image_id not in self.image_references:
            raise ValueError("recommended image is not allowlisted")
        if recommendation.image_reference != self.image_references[recommendation.image_id]:
            raise ValueError("recommended image reference does not match the allowlist")
        if recommendation.policy_version != self.policy_version:
            raise ValueError("recommendation uses an unsupported policy version")
        if recommendation.catalog_version != self.catalog_version:
            raise ValueError("recommendation uses a stale image catalog")
        return recommendation


__all__ = ["PolicyValidator"]
