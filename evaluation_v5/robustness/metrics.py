"""Robustness metric definitions, transition matrices, and aggregations for E2 evaluation."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import math
import statistics
from typing import Any

from .models import RobustnessDataset, RobustnessFamily, RobustnessVariant
from .taxonomy import EquivalenceStatus, PerturbationClass


METRICS_SCHEMA_VERSION = "protocol-v5-robustness-metrics-v1.0.0"


def _safe_rate(numerator: int | float, denominator: int) -> float | None:
    return float(numerator) / denominator if denominator > 0 else None


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 1.0
    return len(set_a & set_b) / len(union)


@dataclass(frozen=True, slots=True)
class TransitionMatrixSummary:
    """Four-quadrant canonical-to-variant transition tracking."""

    stable_correct_count: int
    degradation_count: int
    improvement_count: int
    stable_incorrect_count: int
    total_evaluated: int
    conditioned_degradation_rate: float | None
    conditioned_improvement_rate: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stable_correct_count": self.stable_correct_count,
            "degradation_count": self.degradation_count,
            "improvement_count": self.improvement_count,
            "stable_incorrect_count": self.stable_incorrect_count,
            "total_evaluated": self.total_evaluated,
            "conditioned_degradation_rate": self.conditioned_degradation_rate,
            "conditioned_improvement_rate": self.conditioned_improvement_rate,
        }


@dataclass(frozen=True, slots=True)
class VariantEvaluationRecord:
    """Evaluation outcome for a single variant."""

    variant_id: str
    family_id: str
    variant_type: PerturbationClass
    language: str
    is_canonical: bool
    is_equivalent: bool
    is_controlled_ambiguity: bool
    is_non_equivalent: bool
    is_acceptable: bool
    is_preferred: bool
    constraints_satisfied: bool
    detected_infeasible: bool
    detected_ambiguous: bool
    top_candidate_id: str | None
    selected_profile: str | None
    selected_image: str | None
    extracted_capabilities: tuple[str, ...]
    latency_seconds: float | None = None
    fallback_used: bool = False
    transition_category: str = "canonical_baseline"


@dataclass(frozen=True, slots=True)
class FamilyRobustnessSummary:
    """Family-level robustness aggregation."""

    family_id: str
    family_title: str
    total_variants: int
    equivalent_variants_count: int
    acceptable_equivalent_count: int
    family_robustness_rate: float | None
    all_equivalent_succeeded: bool  # Worst-case family pass/fail
    canonical_succeeded: bool
    canonical_top_candidate: str | None
    canonical_profile: str | None
    canonical_image: str | None
    canonical_capabilities: tuple[str, ...]
    candidate_consistency_count: int
    profile_consistency_count: int
    image_consistency_count: int
    acceptable_consistency_count: int
    capability_jaccard_scores: tuple[float, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "family_title": self.family_title,
            "total_variants": self.total_variants,
            "equivalent_variants_count": self.equivalent_variants_count,
            "acceptable_equivalent_count": self.acceptable_equivalent_count,
            "family_robustness_rate": self.family_robustness_rate,
            "all_equivalent_succeeded": self.all_equivalent_succeeded,
            "canonical_succeeded": self.canonical_succeeded,
            "canonical_top_candidate": self.canonical_top_candidate,
            "canonical_profile": self.canonical_profile,
            "canonical_image": self.canonical_image,
            "candidate_consistency_rate": _safe_rate(
                self.candidate_consistency_count, self.equivalent_variants_count
            ),
            "profile_consistency_rate": _safe_rate(
                self.profile_consistency_count, self.equivalent_variants_count
            ),
            "image_consistency_rate": _safe_rate(
                self.image_consistency_count, self.equivalent_variants_count
            ),
            "acceptable_consistency_rate": _safe_rate(
                self.acceptable_consistency_count, self.equivalent_variants_count
            ),
            "mean_capability_jaccard": (
                statistics.fmean(self.capability_jaccard_scores)
                if self.capability_jaccard_scores
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class RobustnessMetricsResult:
    """Comprehensive metrics result for natural-language robustness (E2)."""

    schema_version: str
    system_name: str
    dataset_id: str
    total_families: int
    total_variants: int

    # Primary robustness metrics (Micro and Macro SRR)
    semantic_robustness_rate_micro: float | None
    semantic_robustness_rate_macro: float | None
    semantic_robustness_rate: float | None  # Primary (micro) alias
    semantic_robustness_numerator: int
    semantic_robustness_denominator: int

    # Worst-Case Family Robustness (WCFR)
    worst_case_family_robustness: float | None
    worst_case_family_pass_count: int
    worst_case_family_total_count: int
    mean_variants_per_family: float | None
    min_variants_per_family: int
    max_variants_per_family: int

    # Accuracy breakdowns
    canonical_accuracy: float | None
    canonical_top1_accuracy: float | None
    english_paraphrase_accuracy: float | None
    vietnamese_accuracy: float | None
    noisy_text_accuracy: float | None
    code_context_accuracy: float | None

    # Degradation and transitions
    overall_degradation_from_canonical: float | None
    transition_matrix: TransitionMatrixSummary
    degradation_by_class: Mapping[str, float | None]
    accuracy_by_class: Mapping[str, dict[str, Any]]

    # Consistency across equivalent variants
    candidate_consistency: float | None
    profile_consistency: float | None
    image_consistency: float | None
    capability_consistency: float | None
    hard_constraint_consistency: float | None
    acceptable_consistency: float | None

    # Ambiguity detection
    ambiguity_detection_rate: float | None
    ambiguity_detection_count: int
    ambiguity_total_count: int

    # Negative controls / Semantic sensitivity
    non_equivalent_count: int
    non_equivalent_changed_count: int
    non_equivalent_sensitivity_rate: float | None

    # Family-level details
    family_summaries: tuple[FamilyRobustnessSummary, ...] = field(
        default_factory=tuple
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "system_name": self.system_name,
            "dataset_id": self.dataset_id,
            "total_families": self.total_families,
            "total_variants": self.total_variants,
            "semantic_robustness_rate": {
                "micro": self.semantic_robustness_rate_micro,
                "macro": self.semantic_robustness_rate_macro,
                "primary": self.semantic_robustness_rate,
                "successful_equivalent_variants": self.semantic_robustness_numerator,
                "total_equivalent_variants": self.semantic_robustness_denominator,
            },
            "worst_case_family_robustness": {
                "value": self.worst_case_family_robustness,
                "passing_families": self.worst_case_family_pass_count,
                "total_families": self.worst_case_family_total_count,
                "exposure": {
                    "mean_variants_per_family": self.mean_variants_per_family,
                    "min_variants_per_family": self.min_variants_per_family,
                    "max_variants_per_family": self.max_variants_per_family,
                },
            },
            "accuracies": {
                "canonical_acceptable": self.canonical_accuracy,
                "canonical_top1": self.canonical_top1_accuracy,
                "english_paraphrase": self.english_paraphrase_accuracy,
                "vietnamese": self.vietnamese_accuracy,
                "noisy_text": self.noisy_text_accuracy,
                "code_context": self.code_context_accuracy,
            },
            "transition_matrix": self.transition_matrix.to_dict(),
            "degradation_from_canonical": {
                "overall_unconditioned": self.overall_degradation_from_canonical,
                "conditioned_degradation_rate": self.transition_matrix.conditioned_degradation_rate,
                "conditioned_improvement_rate": self.transition_matrix.conditioned_improvement_rate,
                "by_perturbation_class": dict(self.degradation_by_class),
            },
            "accuracy_by_class": dict(self.accuracy_by_class),
            "within_family_consistency": {
                "candidate_consistency": self.candidate_consistency,
                "profile_consistency": self.profile_consistency,
                "image_consistency": self.image_consistency,
                "capability_consistency": self.capability_consistency,
                "hard_constraint_consistency": self.hard_constraint_consistency,
                "acceptable_consistency": self.acceptable_consistency,
            },
            "ambiguity_handling": {
                "detection_rate": self.ambiguity_detection_rate,
                "detected_count": self.ambiguity_detection_count,
                "total_ambiguity_variants": self.ambiguity_total_count,
            },
            "negative_controls": {
                "non_equivalent_count": self.non_equivalent_count,
                "changed_prediction_count": self.non_equivalent_changed_count,
                "sensitivity_rate": self.non_equivalent_sensitivity_rate,
            },
            "family_summaries": [item.to_dict() for item in self.family_summaries],
        }


def _is_prediction_acceptable(
    family: RobustnessFamily,
    pred: Mapping[str, Any],
) -> tuple[bool, bool]:
    """Return (is_acceptable, is_preferred) for a prediction against family gold."""
    ranked = list(pred.get("ranked_candidate_ids", []))
    top_candidate = ranked[0] if ranked else pred.get("selected_candidate_id")

    policy_gold = family.policy_gold
    feasibility = policy_gold.get("expected_feasibility", "feasible")
    detected_infeasible = bool(pred.get("detected_infeasible", False))

    if feasibility == "infeasible":
        # For infeasible workload, success is correctly detecting infeasibility
        return (detected_infeasible, detected_infeasible)

    if detected_infeasible:
        # Feasible workload falsely marked infeasible
        return (False, False)

    acceptable = set(family.candidate_gold.get("acceptable_candidate_ids", []))
    preferred = set(family.candidate_gold.get("preferred_candidate_ids", []))

    if not acceptable:
        # Fallback to profile and image checking
        profile = pred.get("selected_profile")
        image = pred.get("selected_image")
        acc_profiles = set(family.profile_gold.get("acceptable_profile_ids", []))
        acc_images = set(family.image_gold.get("acceptable_image_ids", []))
        pref_profiles = set(family.profile_gold.get("preferred_profile_ids", []))
        pref_images = set(family.image_gold.get("preferred_image_ids", []))

        is_acc = bool(profile in acc_profiles and image in acc_images)
        is_pref = bool(profile in pref_profiles and image in pref_images)
        return (is_acc, is_pref)

    is_acc = bool(top_candidate and top_candidate in acceptable)
    is_pref = bool(top_candidate and top_candidate in preferred)
    return (is_acc, is_pref)


def compute_robustness_metrics(
    dataset: RobustnessDataset | Sequence[RobustnessFamily],
    predictions: Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]],
    *,
    system_name: str = "p2",
) -> RobustnessMetricsResult:
    """Compute comprehensive E2 natural-language robustness metrics."""
    raw_families = dataset.families if isinstance(dataset, RobustnessDataset) else dataset
    # Sort families deterministically by family_id for evaluation invariance
    families = sorted(raw_families, key=lambda f: f.family_id)
    dataset_id = (
        dataset.dataset_id
        if isinstance(dataset, RobustnessDataset)
        else "robustness-evaluation"
    )

    # Index predictions by variant_id
    if isinstance(predictions, Mapping):
        pred_map = dict(predictions)
    else:
        pred_map = {
            str(item["variant_id"]): item
            for item in predictions
            if "variant_id" in item
        }

    # Evaluate each variant
    records: list[VariantEvaluationRecord] = []
    records_by_family: dict[str, list[VariantEvaluationRecord]] = defaultdict(list)

    # First evaluate canonical variants to determine baseline correctness per family
    canonical_correctness: dict[str, bool] = {}
    canonical_top_cands: dict[str, str | None] = {}

    for family in families:
        can_variant = family.canonical_variant
        can_pred = pred_map.get(can_variant.variant_id)
        if can_pred is None:
            can_acc = False
            can_top = None
        else:
            can_acc, _ = _is_prediction_acceptable(family, can_pred)
            can_ranked = list(can_pred.get("ranked_candidate_ids", []))
            can_top = can_ranked[0] if can_ranked else can_pred.get("selected_candidate_id")
        canonical_correctness[family.family_id] = can_acc
        canonical_top_cands[family.family_id] = can_top

    for family in families:
        can_acc = canonical_correctness[family.family_id]
        for variant in family.variants:
            pred = pred_map.get(variant.variant_id)
            if pred is None:
                # Missing prediction treated as failure
                pred = {
                    "ranked_candidate_ids": [],
                    "selected_candidate_id": None,
                    "selected_profile": None,
                    "selected_image": None,
                    "extracted_capabilities": [],
                    "detected_infeasible": False,
                    "detected_ambiguous": False,
                    "constraint_violated": True,
                    "latency_seconds": None,
                    "fallback_used": True,
                }

            is_acc, is_pref = _is_prediction_acceptable(family, pred)
            ranked = list(pred.get("ranked_candidate_ids", []))
            top_cand = ranked[0] if ranked else pred.get("selected_candidate_id")

            # Extract profile and image from candidate or explicit fields
            profile = pred.get("selected_profile")
            image = pred.get("selected_image")
            if not profile and top_cand and "-" in top_cand:
                profile = top_cand.split("-")[0]
            if not image and top_cand and "-" in top_cand:
                image = "-".join(top_cand.split("-")[1:])

            caps = tuple(str(c) for c in pred.get("extracted_capabilities", []))
            constraints_sat = not bool(pred.get("constraint_violated", False))

            # Classify 4-quadrant transition for equivalent non-canonical variants
            if variant.is_canonical:
                trans_cat = "canonical_baseline"
            elif can_acc and is_acc:
                trans_cat = "stable_correct"
            elif can_acc and not is_acc:
                trans_cat = "degradation"
            elif not can_acc and is_acc:
                trans_cat = "improvement"
            else:
                trans_cat = "stable_incorrect"

            rec = VariantEvaluationRecord(
                variant_id=variant.variant_id,
                family_id=family.family_id,
                variant_type=variant.metadata.variant_type,
                language=variant.metadata.language,
                is_canonical=variant.is_canonical,
                is_equivalent=variant.is_equivalent,
                is_controlled_ambiguity=variant.is_controlled_ambiguity,
                is_non_equivalent=variant.is_non_equivalent,
                is_acceptable=is_acc,
                is_preferred=is_pref,
                constraints_satisfied=constraints_sat,
                detected_infeasible=bool(pred.get("detected_infeasible", False)),
                detected_ambiguous=bool(
                    pred.get("detected_ambiguous", False)
                    or pred.get("ambiguity_detected", False)
                ),
                top_candidate_id=top_cand,
                selected_profile=profile,
                selected_image=image,
                extracted_capabilities=caps,
                latency_seconds=pred.get("latency_seconds"),
                fallback_used=bool(pred.get("fallback_used", False)),
                transition_category=trans_cat,
            )
            records.append(rec)
            records_by_family[family.family_id].append(rec)

    # 1. Semantic Robustness Rate (SRR): Micro and Macro
    equivalent_records = [r for r in records if r.is_equivalent]
    srr_num = sum(1 for r in equivalent_records if r.is_acceptable)
    srr_den = len(equivalent_records)
    srr_micro = _safe_rate(srr_num, srr_den)

    # 2. Worst-Case Family Robustness (WCFR) and Family-Macro SRR
    family_summaries: list[FamilyRobustnessSummary] = []
    wcfr_pass_count = 0
    wcfr_total_families = 0
    family_rates: list[float] = []
    family_variant_counts: list[int] = []

    candidate_consistencies: list[float] = []
    profile_consistencies: list[float] = []
    image_consistencies: list[float] = []
    acceptable_consistencies: list[float] = []
    capability_jaccards: list[float] = []
    constraint_consistencies: list[float] = []

    for family in families:
        fam_records = records_by_family[family.family_id]
        if not fam_records:
            continue

        equiv_fam_records = [r for r in fam_records if r.is_equivalent]
        if not equiv_fam_records:
            continue

        wcfr_total_families += 1
        family_variant_counts.append(len(equiv_fam_records))

        # Identify canonical record
        canonical_rec = next(
            (r for r in fam_records if r.is_canonical), fam_records[0]
        )
        canonical_succeeded = canonical_rec.is_acceptable
        all_equiv_ok = all(r.is_acceptable for r in equiv_fam_records)

        if all_equiv_ok:
            wcfr_pass_count += 1

        acc_equiv_count = sum(1 for r in equiv_fam_records if r.is_acceptable)
        fam_srr = acc_equiv_count / len(equiv_fam_records)
        family_rates.append(fam_srr)

        cand_match = sum(
            1
            for r in equiv_fam_records
            if r.top_candidate_id == canonical_rec.top_candidate_id
        )
        prof_match = sum(
            1
            for r in equiv_fam_records
            if r.selected_profile == canonical_rec.selected_profile
        )
        img_match = sum(
            1
            for r in equiv_fam_records
            if r.selected_image == canonical_rec.selected_image
        )
        acc_match = sum(
            1
            for r in equiv_fam_records
            if r.is_acceptable == canonical_rec.is_acceptable
        )
        constraint_match = sum(
            1
            for r in equiv_fam_records
            if r.constraints_satisfied == canonical_rec.constraints_satisfied
        )

        can_caps_set = set(canonical_rec.extracted_capabilities)
        jaccard_scores = tuple(
            _jaccard_similarity(set(r.extracted_capabilities), can_caps_set)
            for r in equiv_fam_records
        )

        candidate_consistencies.append(cand_match / len(equiv_fam_records))
        profile_consistencies.append(prof_match / len(equiv_fam_records))
        image_consistencies.append(img_match / len(equiv_fam_records))
        acceptable_consistencies.append(acc_match / len(equiv_fam_records))
        constraint_consistencies.append(
            constraint_match / len(equiv_fam_records)
        )
        capability_jaccards.extend(jaccard_scores)

        summary = FamilyRobustnessSummary(
            family_id=family.family_id,
            family_title=family.title,
            total_variants=len(fam_records),
            equivalent_variants_count=len(equiv_fam_records),
            acceptable_equivalent_count=acc_equiv_count,
            family_robustness_rate=fam_srr,
            all_equivalent_succeeded=all_equiv_ok,
            canonical_succeeded=canonical_succeeded,
            canonical_top_candidate=canonical_rec.top_candidate_id,
            canonical_profile=canonical_rec.selected_profile,
            canonical_image=canonical_rec.selected_image,
            canonical_capabilities=canonical_rec.extracted_capabilities,
            candidate_consistency_count=cand_match,
            profile_consistency_count=prof_match,
            image_consistency_count=img_match,
            acceptable_consistency_count=acc_match,
            capability_jaccard_scores=jaccard_scores,
        )
        family_summaries.append(summary)

    srr_macro = statistics.fmean(family_rates) if family_rates else None
    wcfr = _safe_rate(wcfr_pass_count, wcfr_total_families)

    exposure_mean = (
        statistics.fmean(family_variant_counts)
        if family_variant_counts
        else None
    )
    exposure_min = min(family_variant_counts) if family_variant_counts else 0
    exposure_max = max(family_variant_counts) if family_variant_counts else 0

    # 3. Four-Quadrant Transition Matrix (for non-canonical equivalent variants)
    non_canonical_equiv = [r for r in equivalent_records if not r.is_canonical]
    stable_corr = sum(1 for r in non_canonical_equiv if r.transition_category == "stable_correct")
    degrad = sum(1 for r in non_canonical_equiv if r.transition_category == "degradation")
    improv = sum(1 for r in non_canonical_equiv if r.transition_category == "improvement")
    stable_inc = sum(1 for r in non_canonical_equiv if r.transition_category == "stable_incorrect")

    cond_degrad_rate = _safe_rate(degrad, stable_corr + degrad)
    cond_improv_rate = _safe_rate(improv, stable_inc + improv)

    trans_matrix = TransitionMatrixSummary(
        stable_correct_count=stable_corr,
        degradation_count=degrad,
        improvement_count=improv,
        stable_incorrect_count=stable_inc,
        total_evaluated=len(non_canonical_equiv),
        conditioned_degradation_rate=cond_degrad_rate,
        conditioned_improvement_rate=cond_improv_rate,
    )

    # 4. Accuracies by class & subset
    canonical_records = [r for r in records if r.is_canonical]
    canonical_acc = _safe_rate(
        sum(1 for r in canonical_records if r.is_acceptable),
        len(canonical_records),
    )
    canonical_top1 = _safe_rate(
        sum(1 for r in canonical_records if r.is_preferred),
        len(canonical_records),
    )

    en_para_records = [
        r
        for r in equivalent_records
        if r.variant_type == PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS
        and r.language == "en"
    ]
    en_para_acc = _safe_rate(
        sum(1 for r in en_para_records if r.is_acceptable), len(en_para_records)
    )

    vi_records = [
        r
        for r in equivalent_records
        if r.variant_type == PerturbationClass.VIETNAMESE or r.language == "vi"
    ]
    vi_acc = _safe_rate(
        sum(1 for r in vi_records if r.is_acceptable), len(vi_records)
    )

    noisy_records = [
        r
        for r in equivalent_records
        if r.variant_type
        in {
            PerturbationClass.INFORMAL_COLLOQUIAL,
            PerturbationClass.TYPO_NOISE,
            PerturbationClass.IRRELEVANT_EXTRA_CONTEXT,
        }
    ]
    noisy_acc = _safe_rate(
        sum(1 for r in noisy_records if r.is_acceptable), len(noisy_records)
    )

    code_records = [
        r
        for r in equivalent_records
        if r.variant_type
        == PerturbationClass.REQUIREMENT_EXPRESSED_IN_CODE_CONTEXT
    ]
    code_acc = _safe_rate(
        sum(1 for r in code_records if r.is_acceptable), len(code_records)
    )

    # Granular breakdown by PerturbationClass
    accuracy_by_class: dict[str, dict[str, Any]] = {}
    degradation_by_class: dict[str, float | None] = {}

    for pclass in PerturbationClass:
        cls_records = [r for r in equivalent_records if r.variant_type == pclass]
        count = len(cls_records)
        cls_fams = len({r.family_id for r in cls_records})
        acc_count = sum(1 for r in cls_records if r.is_acceptable)
        pref_count = sum(1 for r in cls_records if r.is_preferred)
        rate = _safe_rate(acc_count, count)
        pref_rate = _safe_rate(pref_count, count)

        # Transition counts for this class
        c_stable_corr = sum(1 for r in cls_records if r.transition_category == "stable_correct")
        c_degrad = sum(1 for r in cls_records if r.transition_category == "degradation")
        c_improv = sum(1 for r in cls_records if r.transition_category == "improvement")
        c_stable_inc = sum(1 for r in cls_records if r.transition_category == "stable_incorrect")

        accuracy_by_class[pclass.value] = {
            "family_count": cls_fams,
            "variant_count": count,
            "acceptable_count": acc_count,
            "preferred_count": pref_count,
            "accuracy": rate,
            "top1_accuracy": pref_rate,
            "transition_counts": {
                "stable_correct": c_stable_corr,
                "degradation": c_degrad,
                "improvement": c_improv,
                "stable_incorrect": c_stable_inc,
            },
        }
        if canonical_acc is not None and rate is not None:
            degradation_by_class[pclass.value] = canonical_acc - rate
        else:
            degradation_by_class[pclass.value] = None

    # Overall unconditioned degradation
    non_canonical_acc = _safe_rate(
        sum(1 for r in non_canonical_equiv if r.is_acceptable),
        len(non_canonical_equiv),
    )
    if canonical_acc is not None and non_canonical_acc is not None:
        overall_degradation = canonical_acc - non_canonical_acc
    else:
        overall_degradation = None

    # 5. Consistency aggregations
    candidate_consistency_val = (
        statistics.fmean(candidate_consistencies)
        if candidate_consistencies
        else None
    )
    profile_consistency_val = (
        statistics.fmean(profile_consistencies)
        if profile_consistencies
        else None
    )
    image_consistency_val = (
        statistics.fmean(image_consistencies) if image_consistencies else None
    )
    acceptable_consistency_val = (
        statistics.fmean(acceptable_consistencies)
        if acceptable_consistencies
        else None
    )
    capability_consistency_val = (
        statistics.fmean(capability_jaccards) if capability_jaccards else None
    )
    hard_constraint_consistency_val = (
        statistics.fmean(constraint_consistencies)
        if constraint_consistencies
        else None
    )

    # 6. Ambiguity handling
    ambiguous_records = [r for r in records if r.is_controlled_ambiguity]
    ambig_det_count = sum(
        1
        for r in ambiguous_records
        if r.detected_ambiguous or r.detected_infeasible
    )
    ambig_det_rate = _safe_rate(ambig_det_count, len(ambiguous_records))

    # 7. Negative controls / Semantic sensitivity
    non_equiv_records = [r for r in records if r.is_non_equivalent]
    non_equiv_count = len(non_equiv_records)
    non_equiv_changed = 0
    for r in non_equiv_records:
        can_top = canonical_top_cands.get(r.family_id)
        if r.top_candidate_id != can_top:
            non_equiv_changed += 1
    non_equiv_sensitivity = _safe_rate(non_equiv_changed, non_equiv_count)

    return RobustnessMetricsResult(
        schema_version=METRICS_SCHEMA_VERSION,
        system_name=system_name,
        dataset_id=dataset_id,
        total_families=len(families),
        total_variants=len(records),
        semantic_robustness_rate_micro=srr_micro,
        semantic_robustness_rate_macro=srr_macro,
        semantic_robustness_rate=srr_micro,  # Default alias
        semantic_robustness_numerator=srr_num,
        semantic_robustness_denominator=srr_den,
        worst_case_family_robustness=wcfr,
        worst_case_family_pass_count=wcfr_pass_count,
        worst_case_family_total_count=wcfr_total_families,
        mean_variants_per_family=exposure_mean,
        min_variants_per_family=exposure_min,
        max_variants_per_family=exposure_max,
        canonical_accuracy=canonical_acc,
        canonical_top1_accuracy=canonical_top1,
        english_paraphrase_accuracy=en_para_acc,
        vietnamese_accuracy=vi_acc,
        noisy_text_accuracy=noisy_acc,
        code_context_accuracy=code_acc,
        overall_degradation_from_canonical=overall_degradation,
        transition_matrix=trans_matrix,
        degradation_by_class=degradation_by_class,
        accuracy_by_class=accuracy_by_class,
        candidate_consistency=candidate_consistency_val,
        profile_consistency=profile_consistency_val,
        image_consistency=image_consistency_val,
        capability_consistency=capability_consistency_val,
        hard_constraint_consistency=hard_constraint_consistency_val,
        acceptable_consistency=acceptable_consistency_val,
        ambiguity_detection_rate=ambig_det_rate,
        ambiguity_detection_count=ambig_det_count,
        ambiguity_total_count=len(ambiguous_records),
        non_equivalent_count=non_equiv_count,
        non_equivalent_changed_count=non_equiv_changed,
        non_equivalent_sensitivity_rate=non_equiv_sensitivity,
        family_summaries=tuple(family_summaries),
    )


__all__ = [
    "FamilyRobustnessSummary",
    "METRICS_SCHEMA_VERSION",
    "RobustnessMetricsResult",
    "TransitionMatrixSummary",
    "VariantEvaluationRecord",
    "compute_robustness_metrics",
]
