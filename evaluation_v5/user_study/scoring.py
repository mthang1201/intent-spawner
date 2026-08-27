"""Condition-blind final-selection scoring for Protocol-v5 E3.

This scorer intentionally knows nothing about B0, P2, previews, or rankings.
It evaluates only the final confirmed profile/image pair against the frozen
researcher-only gold attached to a matched task pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schemas import PairGold, UserStudyValidationError


FINAL_SELECTION_SCORING_VERSION = (
    "protocol-v5-user-study-final-selection-scoring-v1.0.0"
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise UserStudyValidationError(message)


@dataclass(frozen=True, slots=True)
class FinalSelectionScore:
    """Frozen labels for one final confirmed environment selection."""

    scoring_version: str
    scoring_status: str
    candidate_id: str | None
    selection_correct: bool
    selection_acceptable: bool
    profile_acceptable: bool
    image_acceptable: bool
    hard_constraints_satisfied: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "selection_scoring_version": self.scoring_version,
            "selection_scoring_status": self.scoring_status,
            "candidate_id": self.candidate_id,
            "selection_correct": self.selection_correct,
            "selection_acceptable": self.selection_acceptable,
            "profile_acceptable": self.profile_acceptable,
            "image_acceptable": self.image_acceptable,
            "hard_constraints_satisfied": self.hard_constraints_satisfied,
        }


def score_final_selection(
    gold: PairGold,
    *,
    profile_id: str | None,
    image_id: str | None,
) -> FinalSelectionScore:
    """Score a final selection identically for every experimental condition.

    ``selection_correct`` means the frozen preferred candidate was selected.
    ``selection_acceptable`` means the final candidate is in the broader
    frozen acceptable set.  The acceptable set is authoritatively validated
    against the candidate corpus and therefore represents candidates satisfying
    the pair's hard requirements and policy constraints.
    """

    _require(isinstance(gold, PairGold), "final-selection scoring requires PairGold")
    _require(bool(gold.acceptable_profile_ids), "gold has no acceptable profiles")
    _require(bool(gold.acceptable_image_ids), "gold has no acceptable images")
    _require(bool(gold.acceptable_candidate_ids), "gold has no acceptable candidates")
    _require(
        gold.preferred_candidate_id in gold.acceptable_candidate_ids,
        "gold preferred candidate is not acceptable",
    )
    if profile_id is None or image_id is None:
        _require(
            profile_id is None and image_id is None,
            "final selection must contain both profile and image or neither",
        )
        return FinalSelectionScore(
            scoring_version=FINAL_SELECTION_SCORING_VERSION,
            scoring_status="unavailable_no_confirmation",
            candidate_id=None,
            selection_correct=False,
            selection_acceptable=False,
            profile_acceptable=False,
            image_acceptable=False,
            hard_constraints_satisfied=False,
        )

    candidate_id = f"{profile_id}-{image_id}"
    profile_acceptable = profile_id in gold.acceptable_profile_ids
    image_acceptable = image_id in gold.acceptable_image_ids
    acceptable = candidate_id in gold.acceptable_candidate_ids
    return FinalSelectionScore(
        scoring_version=FINAL_SELECTION_SCORING_VERSION,
        scoring_status="scored",
        candidate_id=candidate_id,
        selection_correct=candidate_id == gold.preferred_candidate_id,
        selection_acceptable=acceptable,
        profile_acceptable=profile_acceptable,
        image_acceptable=image_acceptable,
        hard_constraints_satisfied=acceptable,
    )


__all__ = [
    "FINAL_SELECTION_SCORING_VERSION",
    "FinalSelectionScore",
    "score_final_selection",
]
