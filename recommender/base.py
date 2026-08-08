"""Backend interface for spawn recommenders."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import RecommendationRequest, SpawnRecommendation


@runtime_checkable
class Recommender(Protocol):
    """Structural interface implemented by all recommendation backends."""

    def recommend(self, request: RecommendationRequest) -> SpawnRecommendation:
        """Return one unified recommendation for the supplied context."""

        ...
