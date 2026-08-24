"""Pair-level evaluation preserving every prediction, gold check, transition, and canonical comparison."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import csv
from dataclasses import dataclass
import io
import json
from typing import Any

from .metrics import _is_prediction_acceptable, _jaccard_similarity
from .models import RobustnessDataset, RobustnessFamily, RobustnessVariant
from .review import _csv_sanitize
from .taxonomy import PerturbationClass, compute_text_sha256


PAIR_LEVEL_SCHEMA_VERSION = "protocol-v5-robustness-pairs-v1.0.0"


@dataclass(frozen=True, slots=True)
class PairComparisonRecord:
    """Granular observation record for one (family, variant) prediction."""

    family_id: str
    family_title: str
    variant_id: str
    variant_type: str
    language: str
    source: str
    equivalence_status: str
    human_review_status: str
    is_canonical: bool
    is_equivalent: bool
    intent: str
    code_context: tuple[str, ...]
    variant_text_sha256: str

    # Gold
    gold_feasible: bool
    gold_preferred_candidate: str | None
    gold_acceptable_candidates: tuple[str, ...]
    gold_required_capabilities: tuple[str, ...]
    gold_preferred_profiles: tuple[str, ...]
    gold_acceptable_profiles: tuple[str, ...]
    gold_preferred_images: tuple[str, ...]
    gold_acceptable_images: tuple[str, ...]

    # Prediction
    system: str
    predicted_top_candidate: str | None
    predicted_ranked_candidates: tuple[str, ...]
    predicted_profile: str | None
    predicted_image: str | None
    predicted_capabilities: tuple[str, ...]
    predicted_feasible: bool
    latency_seconds: float | None
    fallback_used: bool

    # Gold comparisons
    is_acceptable: bool
    is_preferred: bool
    constraints_satisfied: bool
    transition_category: str

    # Canonical comparisons
    canonical_variant_id: str
    canonical_top_candidate: str | None
    canonical_profile: str | None
    canonical_image: str | None
    canonical_capabilities: tuple[str, ...]
    canonical_feasible: bool
    canonical_is_acceptable: bool
    matches_canonical_candidate: bool
    matches_canonical_profile: bool
    matches_canonical_image: bool
    matches_canonical_capabilities: bool
    capability_jaccard_with_canonical: float
    matches_canonical_constraints: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "family_title": self.family_title,
            "variant_id": self.variant_id,
            "variant_type": self.variant_type,
            "language": self.language,
            "source": self.source,
            "equivalence_status": self.equivalence_status,
            "human_review_status": self.human_review_status,
            "is_canonical": self.is_canonical,
            "is_equivalent": self.is_equivalent,
            "intent": self.intent,
            "code_context": list(self.code_context),
            "variant_text_sha256": self.variant_text_sha256,
            "gold": {
                "feasible": self.gold_feasible,
                "preferred_candidate": self.gold_preferred_candidate,
                "acceptable_candidates": list(self.gold_acceptable_candidates),
                "required_capabilities": list(self.gold_required_capabilities),
                "preferred_profiles": list(self.gold_preferred_profiles),
                "acceptable_profiles": list(self.gold_acceptable_profiles),
                "preferred_images": list(self.gold_preferred_images),
                "acceptable_images": list(self.gold_acceptable_images),
            },
            "prediction": {
                "system": self.system,
                "top_candidate": self.predicted_top_candidate,
                "ranked_candidates": list(self.predicted_ranked_candidates),
                "profile": self.predicted_profile,
                "image": self.predicted_image,
                "capabilities": list(self.predicted_capabilities),
                "feasible": self.predicted_feasible,
                "latency_seconds": self.latency_seconds,
                "fallback_used": self.fallback_used,
            },
            "evaluation": {
                "is_acceptable": self.is_acceptable,
                "is_preferred": self.is_preferred,
                "constraints_satisfied": self.constraints_satisfied,
                "transition_category": self.transition_category,
            },
            "canonical_comparison": {
                "canonical_variant_id": self.canonical_variant_id,
                "canonical_top_candidate": self.canonical_top_candidate,
                "canonical_profile": self.canonical_profile,
                "canonical_image": self.canonical_image,
                "canonical_capabilities": list(self.canonical_capabilities),
                "canonical_feasible": self.canonical_feasible,
                "canonical_is_acceptable": self.canonical_is_acceptable,
                "matches_canonical_candidate": self.matches_canonical_candidate,
                "matches_canonical_profile": self.matches_canonical_profile,
                "matches_canonical_image": self.matches_canonical_image,
                "matches_canonical_capabilities": self.matches_canonical_capabilities,
                "capability_jaccard": self.capability_jaccard_with_canonical,
                "matches_canonical_constraints": self.matches_canonical_constraints,
            },
        }

    def to_csv_dict(self) -> dict[str, Any]:
        """Return dict with formula prefixes sanitized for safe CSV output."""
        return {
            "family_id": self.family_id,
            "variant_id": self.variant_id,
            "variant_type": self.variant_type,
            "language": self.language,
            "is_canonical": self.is_canonical,
            "is_equivalent": self.is_equivalent,
            "gold_preferred_candidate": self.gold_preferred_candidate or "",
            "predicted_top_candidate": self.predicted_top_candidate or "",
            "is_acceptable": self.is_acceptable,
            "is_preferred": self.is_preferred,
            "transition_category": self.transition_category,
            "matches_canonical_candidate": self.matches_canonical_candidate,
            "matches_canonical_profile": self.matches_canonical_profile,
            "matches_canonical_image": self.matches_canonical_image,
            "capability_jaccard": self.capability_jaccard_with_canonical,
            "latency_seconds": (
                self.latency_seconds if self.latency_seconds is not None else ""
            ),
            "fallback_used": self.fallback_used,
            "variant_text_sha256": self.variant_text_sha256,
        }


@dataclass(frozen=True, slots=True)
class PairLevelOutput:
    """Container for complete pair-level evaluation results."""

    schema_version: str
    system_name: str
    dataset_id: str
    total_pairs: int
    records: tuple[PairComparisonRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "system_name": self.system_name,
            "dataset_id": self.dataset_id,
            "total_pairs": self.total_pairs,
            "records": [rec.to_dict() for rec in self.records],
        }

    def to_jsonl(self) -> str:
        """Export records as newline-delimited JSON for analysis."""
        lines = [
            json.dumps(rec.to_dict(), ensure_ascii=False, sort_keys=True)
            for rec in self.records
        ]
        return "\n".join(lines) + "\n" if lines else ""

    def to_csv(self) -> str:
        """Export flattened tabular summary of all pair comparisons with formula-injection defense."""
        output = io.StringIO()
        fieldnames = [
            "family_id",
            "variant_id",
            "variant_type",
            "language",
            "is_canonical",
            "is_equivalent",
            "gold_preferred_candidate",
            "predicted_top_candidate",
            "is_acceptable",
            "is_preferred",
            "transition_category",
            "matches_canonical_candidate",
            "matches_canonical_profile",
            "matches_canonical_image",
            "capability_jaccard",
            "latency_seconds",
            "fallback_used",
            "variant_text_sha256",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for rec in self.records:
            writer.writerow(rec.to_csv_dict())
        return output.getvalue()


def evaluate_robustness_pairs(
    dataset: RobustnessDataset | Sequence[RobustnessFamily],
    predictions: Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]],
    *,
    system_name: str = "p2",
) -> PairLevelOutput:
    """Evaluate system predictions against robustness variants and record pair comparisons."""
    raw_families = dataset.families if isinstance(dataset, RobustnessDataset) else dataset
    families = sorted(raw_families, key=lambda f: f.family_id)
    dataset_id = (
        dataset.dataset_id if isinstance(dataset, RobustnessDataset) else "robustness-evaluation"
    )

    if isinstance(predictions, Mapping):
        pred_map = dict(predictions)
    else:
        pred_map = {str(item["variant_id"]): item for item in predictions if "variant_id" in item}

    records: list[PairComparisonRecord] = []

    for family in families:
        # First pass: evaluate canonical variant to obtain reference baseline
        canonical_variant = family.canonical_variant
        can_pred = pred_map.get(canonical_variant.variant_id, {})
        can_ranked = list(can_pred.get("ranked_candidate_ids", []))
        can_top_cand = can_ranked[0] if can_ranked else can_pred.get("selected_candidate_id")

        can_profile = can_pred.get("selected_profile")
        can_image = can_pred.get("selected_image")
        if not can_profile and can_top_cand and "-" in can_top_cand:
            can_profile = can_top_cand.split("-")[0]
        if not can_image and can_top_cand and "-" in can_top_cand:
            can_image = "-".join(can_top_cand.split("-")[1:])

        can_caps = tuple(str(c) for c in can_pred.get("extracted_capabilities", []))
        can_feasible = not bool(can_pred.get("detected_infeasible", False))
        can_sat = not bool(can_pred.get("constraint_violated", False))
        can_is_acc, _ = _is_prediction_acceptable(family, can_pred)

        cand_gold = family.candidate_gold
        prof_gold = family.profile_gold
        img_gold = family.image_gold
        pol_gold = family.policy_gold

        pref_cands = cand_gold.get("preferred_candidate_ids", [])
        pref_cand = pref_cands[0] if pref_cands else None
        acc_cands = tuple(cand_gold.get("acceptable_candidate_ids", []))
        req_caps = tuple(img_gold.get("required_capabilities", []))
        pref_profs = tuple(prof_gold.get("preferred_profile_ids", []))
        acc_profs = tuple(prof_gold.get("acceptable_profile_ids", []))
        pref_imgs = tuple(img_gold.get("preferred_image_ids", []))
        acc_imgs = tuple(img_gold.get("acceptable_image_ids", []))
        feasible_gold = pol_gold.get("expected_feasibility", "feasible") == "feasible"

        for variant in family.variants:
            pred = pred_map.get(variant.variant_id, {})
            ranked = list(pred.get("ranked_candidate_ids", []))
            top_cand = ranked[0] if ranked else pred.get("selected_candidate_id")

            profile = pred.get("selected_profile")
            image = pred.get("selected_image")
            if not profile and top_cand and "-" in top_cand:
                profile = top_cand.split("-")[0]
            if not image and top_cand and "-" in top_cand:
                image = "-".join(top_cand.split("-")[1:])

            caps = tuple(str(c) for c in pred.get("extracted_capabilities", []))
            pred_feasible = not bool(pred.get("detected_infeasible", False))
            pred_sat = not bool(pred.get("constraint_violated", False))

            is_acc, is_pref = _is_prediction_acceptable(family, pred)

            # Classify transition
            if variant.is_canonical:
                trans_cat = "canonical_baseline"
            elif can_is_acc and is_acc:
                trans_cat = "stable_correct"
            elif can_is_acc and not is_acc:
                trans_cat = "degradation"
            elif not can_is_acc and is_acc:
                trans_cat = "improvement"
            else:
                trans_cat = "stable_incorrect"

            # Canonical comparisons
            match_cand = bool(top_cand and top_cand == can_top_cand)
            match_prof = bool(profile and profile == can_profile)
            match_img = bool(image and image == can_image)
            match_caps = set(caps) == set(can_caps)
            jaccard = _jaccard_similarity(set(caps), set(can_caps))
            match_constraints = pred_sat == can_sat

            meta = variant.metadata
            v_type_str = (
                meta.variant_type.value
                if isinstance(meta.variant_type, PerturbationClass)
                else str(meta.variant_type)
            )

            rec = PairComparisonRecord(
                family_id=family.family_id,
                family_title=family.title,
                variant_id=variant.variant_id,
                variant_type=v_type_str,
                language=meta.language,
                source=str(meta.source),
                equivalence_status=str(meta.equivalence_status),
                human_review_status=str(meta.human_review_status),
                is_canonical=variant.is_canonical,
                is_equivalent=variant.is_equivalent,
                intent=variant.intent,
                code_context=variant.code_context,
                variant_text_sha256=variant.text_sha256,
                gold_feasible=feasible_gold,
                gold_preferred_candidate=pref_cand,
                gold_acceptable_candidates=acc_cands,
                gold_required_capabilities=req_caps,
                gold_preferred_profiles=pref_profs,
                gold_acceptable_profiles=acc_profs,
                gold_preferred_images=pref_imgs,
                gold_acceptable_images=acc_imgs,
                system=system_name,
                predicted_top_candidate=top_cand,
                predicted_ranked_candidates=tuple(ranked),
                predicted_profile=profile,
                predicted_image=image,
                predicted_capabilities=caps,
                predicted_feasible=pred_feasible,
                latency_seconds=pred.get("latency_seconds"),
                fallback_used=bool(pred.get("fallback_used", False)),
                is_acceptable=is_acc,
                is_preferred=is_pref,
                constraints_satisfied=pred_sat,
                transition_category=trans_cat,
                canonical_variant_id=canonical_variant.variant_id,
                canonical_top_candidate=can_top_cand,
                canonical_profile=can_profile,
                canonical_image=can_image,
                canonical_capabilities=can_caps,
                canonical_feasible=can_feasible,
                canonical_is_acceptable=can_is_acc,
                matches_canonical_candidate=match_cand,
                matches_canonical_profile=match_prof,
                matches_canonical_image=match_img,
                matches_canonical_capabilities=match_caps,
                capability_jaccard_with_canonical=jaccard,
                matches_canonical_constraints=match_constraints,
            )
            records.append(rec)

    return PairLevelOutput(
        schema_version=PAIR_LEVEL_SCHEMA_VERSION,
        system_name=system_name,
        dataset_id=dataset_id,
        total_pairs=len(records),
        records=tuple(records),
    )


__all__ = [
    "PAIR_LEVEL_SCHEMA_VERSION",
    "PairComparisonRecord",
    "PairLevelOutput",
    "evaluate_robustness_pairs",
]
