"""Comprehensive adversarial test suite for Protocol-v5 E2 natural-language robustness tooling."""

from __future__ import annotations

from copy import deepcopy
import csv
import json
from pathlib import Path
import random

import pytest
import yaml

from evaluation_v5.robustness import (
    EquivalenceReviewRow,
    EquivalenceStatus,
    HumanReviewStatus,
    InvalidReviewDecisionError,
    PerturbationClass,
    RobustnessDataset,
    RobustnessFamily,
    RobustnessValidationError,
    RobustnessVariant,
    StaleReviewError,
    TransitionMatrixSummary,
    VariantMetadata,
    VariantSource,
    apply_review_decisions,
    compute_robustness_metrics,
    compute_text_sha256,
    evaluate_robustness_pairs,
    export_equivalence_review,
    export_equivalence_review_csv,
    export_equivalence_review_json,
    export_equivalence_review_markdown,
    extract_review_rows,
    generate_ambiguity_variant,
    generate_code_context_variant,
    generate_draft_variant,
    generate_family_drafts,
    generate_informal_colloquial,
    generate_irrelevant_context,
    generate_paraphrase_no_keywords,
    inject_typo_noise,
    load_robustness_dataset,
    load_robustness_families_from_gold,
    load_robustness_families_from_split,
    main,
    normalize_perturbation_class,
    to_gold_variant_class,
    validate_robustness_dataset,
    validate_robustness_family,
)
from evaluation_v5.gold_dataset import (
    CONFIRMATORY_ELIGIBLE_CLASSIFICATIONS,
    EvidenceClassification,
    GoldDatasetValidationError,
    compile_gold_dataset,
    current_catalog_identity,
    validate_gold_dataset,
)



ROOT = Path(__file__).resolve().parents[1]
DEV_SPLIT_PATH = ROOT / "benchmarks_v5" / "v5-development.yaml"


def _sample_variant(
    variant_id: str,
    *,
    family_id: str = "data-cleaning-family",
    variant_type: PerturbationClass | None = None,
    language: str = "en",
    intent: str = "Clean a medium dataframe with pandas.",
    equivalence_status: EquivalenceStatus | None = None,
    human_review_status: HumanReviewStatus = HumanReviewStatus.APPROVED,
    source: VariantSource = VariantSource.HUMAN_AUTHORED,
    code_context: list[str] | None = None,
    expected_differences: str | None = None,
) -> RobustnessVariant:
    if equivalence_status is None:
        if "canonical" in variant_id or "can" in variant_id:
            equivalence_status = EquivalenceStatus.CANONICAL_REFERENCE
        else:
            equivalence_status = EquivalenceStatus.REVIEWED_EQUIVALENT
    if variant_type is None:
        if equivalence_status == EquivalenceStatus.CANONICAL_REFERENCE or "canonical" in variant_id or "can" in variant_id:
            variant_type = PerturbationClass.CANONICAL
        else:
            variant_type = PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS

    meta = VariantMetadata(
        variant_type=variant_type,
        language=language,
        source=source,
        human_review_status=human_review_status,
        equivalence_status=equivalence_status,
        expected_semantic_differences=expected_differences,
        notes=(),
    )
    return RobustnessVariant(
        variant_id=variant_id,
        family_id=family_id,
        intent=intent,
        code_context=tuple(code_context or []),
        metadata=meta,
        dataset_size_gb=1.5,
    )


def _sample_family(
    family_id: str = "data-cleaning-family",
    *,
    variants: list[RobustnessVariant] | None = None,
    preferred_candidate: str = "medium-scipy-data-science",
    acceptable_candidates: list[str] | None = None,
    role: str = "development",
    evidence_classification: str | None = None,
) -> RobustnessFamily:
    acc = acceptable_candidates or ["medium-scipy-data-science", "large-scipy-data-science"]
    if evidence_classification is None:
        evidence_classification = (
            "human_reviewed_confirmatory"
            if role == "confirmatory"
            else "development_only"
        )
    vars_list = variants or [
        _sample_variant(f"{family_id}-canonical", family_id=family_id),
        _sample_variant(
            f"{family_id}-para",
            family_id=family_id,
            variant_type=PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS,
            intent="Transform and clean tabular dataset records.",
            equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT,
        ),
        _sample_variant(
            f"{family_id}-vi",
            family_id=family_id,
            variant_type=PerturbationClass.VIETNAMESE,
            language="vi",
            intent="Làm sạch dữ liệu bảng với pandas.",
            equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT,
        ),
        _sample_variant(
            f"{family_id}-noise",
            family_id=family_id,
            variant_type=PerturbationClass.TYPO_NOISE,
            intent="cln tabulr dtaframe with pndas",
            equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT,
        ),
        _sample_variant(
            f"{family_id}-ambig",
            family_id=family_id,
            variant_type=PerturbationClass.AMBIGUOUS_OR_CONFLICTING_SIGNAL,
            intent="Need small CPU but also maybe 500GB cluster GPU.",
            equivalence_status=EquivalenceStatus.CONTROLLED_AMBIGUITY,
        ),
    ]

    return RobustnessFamily(
        family_id=family_id,
        title="Data cleaning family",
        workload_stratum="data_processing",
        difficulty="medium",
        executable_workload_id="data_pandas_read_transform",
        gold_structured_intent={
            "task_types": ["data_processing"],
            "required_features": ["pandas"],
            "preferred_features": [],
            "forbidden_features": [],
            "required_frameworks": [],
            "preferred_frameworks": [],
            "gpu_semantics": "forbidden",
            "minimum_cpu_cores": 1.0,
            "minimum_memory_gb": 2.0,
            "dataset_size_gb": 1.5,
            "ambiguities": [],
        },
        candidate_gold={
            "preferred_candidate_ids": [preferred_candidate],
            "acceptable_candidate_ids": acc,
        },
        profile_gold={
            "preferred_profile_ids": ["medium"],
            "acceptable_profile_ids": ["medium", "large"],
        },
        image_gold={
            "preferred_image_ids": ["scipy-data-science"],
            "acceptable_image_ids": ["scipy-data-science"],
            "required_capabilities": ["pandas"],
        },
        policy_gold={
            "required_constraints": ["gpu_allowed=false"],
            "explicitly_unsupported_requirements": [],
            "expected_feasibility": "feasible",
        },
        variants=tuple(vars_list),
        label_review={"status": "approved", "reviewed_by": "evaluator-lead"},
        source_provenance={
            "source_split": role,
            "evidence_classification": evidence_classification,
        },
        role=role,
        evidence_classification=evidence_classification,
    )


# ---------------------------------------------------------------------------
# 1. Canonical Reference Contract (SRR / WCFR Exclusion)
# ---------------------------------------------------------------------------


def test_canonical_reference_excluded_from_srr_and_wcfr():
    """Prove canonical reference is baseline only and strictly excluded from SRR & WCFR."""
    # Family A: canonical + 2 reviewed equivalent variants (both pass)
    can_a = _sample_variant("fam-a-canonical", family_id="fam-a")
    v1_a = _sample_variant("fam-a-v1", family_id="fam-a", equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT)
    v2_a = _sample_variant("fam-a-v2", family_id="fam-a", equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT)
    fam_a = _sample_family("fam-a", variants=[can_a, v1_a, v2_a])

    # Family B: canonical only (0 reviewed equivalent variants)
    can_b = _sample_variant("fam-b-canonical", family_id="fam-b")
    fam_b = _sample_family("fam-b", variants=[can_b])

    dataset = RobustnessDataset(dataset_id="test-can-exclusion", families=(fam_a, fam_b))

    preds = {
        "fam-a-canonical": {"variant_id": "fam-a-canonical", "ranked_candidate_ids": ["medium-scipy-data-science"]},
        "fam-a-v1": {"variant_id": "fam-a-v1", "ranked_candidate_ids": ["medium-scipy-data-science"]},
        "fam-a-v2": {"variant_id": "fam-a-v2", "ranked_candidate_ids": ["medium-scipy-data-science"]},
        "fam-b-canonical": {"variant_id": "fam-b-canonical", "ranked_candidate_ids": ["unacceptable"]},
    }

    metrics = compute_robustness_metrics(dataset, preds, system_name="p2")

    # Only 2 reviewed-equivalent perturbations exist across the dataset
    assert metrics.semantic_robustness_denominator == 2
    assert metrics.semantic_robustness_numerator == 2
    assert metrics.semantic_robustness_rate_micro == 1.0
    assert metrics.semantic_robustness_rate_macro == 1.0

    # Family B has 0 perturbation exposure; WCFR is evaluated only over Family A
    assert metrics.worst_case_family_total_count == 1
    assert metrics.worst_case_family_pass_count == 1
    assert metrics.worst_case_family_robustness == 1.0

    # Canonical baseline is tracked separately
    assert metrics.canonical_evaluated_count == 2
    assert metrics.canonical_acceptable_count == 1
    assert metrics.canonical_accuracy == 0.5


def test_only_reviewed_equivalent_perturbations_enter_srr_wcfr():
    """Prove that pending, ambiguous, and non-equivalent variants do NOT enter SRR/WCFR."""
    can = _sample_variant("can", equivalence_status=EquivalenceStatus.CANONICAL_REFERENCE)
    eq = _sample_variant("eq", equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT)
    pending = _sample_variant("pending", equivalence_status=EquivalenceStatus.PENDING_REVIEW, human_review_status=HumanReviewStatus.PENDING)
    ambig = _sample_variant("ambig", equivalence_status=EquivalenceStatus.CONTROLLED_AMBIGUITY)
    non_eq = _sample_variant("non_eq", equivalence_status=EquivalenceStatus.NON_EQUIVALENT)

    fam = _sample_family("fam-mixed", variants=[can, eq, pending, ambig, non_eq])
    dataset = RobustnessDataset(dataset_id="test-mixed", families=(fam,))

    preds = {
        "can": {"variant_id": "can", "ranked_candidate_ids": ["medium-scipy-data-science"]},
        "eq": {"variant_id": "eq", "ranked_candidate_ids": ["medium-scipy-data-science"]},
        "pending": {"variant_id": "pending", "ranked_candidate_ids": ["unacceptable"]},
        "ambig": {"variant_id": "ambig", "ranked_candidate_ids": ["unacceptable"], "detected_ambiguous": True},
        "non_eq": {"variant_id": "non_eq", "ranked_candidate_ids": ["large-pytorch-deep-learning"]},
    }

    metrics = compute_robustness_metrics(dataset, preds, system_name="p2")
    # Denominator is strictly 1 (the 1 reviewed_equivalent variant)
    assert metrics.semantic_robustness_denominator == 1
    assert metrics.semantic_robustness_numerator == 1
    assert metrics.semantic_robustness_rate_micro == 1.0


# ---------------------------------------------------------------------------
# 2. Protocol-v5 v1 Robustness-Family Grouping (Zero Semantic Guessing)
# ---------------------------------------------------------------------------


def test_v1_grouping_prompt_text_alteration_invariance():
    """Changing prompt text while preserving explicit family_id does NOT alter family grouping."""
    from evaluation_v5.split_dataset import split_bundle_checksum
    raw_doc = yaml.safe_load(DEV_SPLIT_PATH.read_text(encoding="utf-8"))
    raw_doc["cases"][0]["prompt"] = "Completely altered arbitrary prompt text without keywords"
    raw_doc["split_manifest"]["checksum"] = split_bundle_checksum(raw_doc)

    dataset = load_robustness_families_from_split(raw_doc)
    assert len(dataset.families) == 10
    assert dataset.total_variants == 18


def test_v1_grouping_semantic_similarity_isolation():
    """Two semantically identical prompts with distinct family_ids are NEVER grouped together."""
    from evaluation_v5.split_dataset import split_bundle_checksum
    raw_doc = yaml.safe_load(DEV_SPLIT_PATH.read_text(encoding="utf-8"))
    new_case = dict(raw_doc["cases"][0])
    new_case["case_id"] = "new-isolated-case"
    new_case["family_id"] = "brand-new-family"
    raw_doc["cases"].append(new_case)
    raw_doc["split_manifest"]["family_ids"] = sorted(
        raw_doc["split_manifest"]["family_ids"] + ["brand-new-family"]
    )
    raw_doc["split_manifest"]["case_count"] = len(raw_doc["cases"])
    raw_doc["split_manifest"]["family_count"] = len(raw_doc["split_manifest"]["family_ids"])
    raw_doc["split_manifest"]["checksum"] = split_bundle_checksum(raw_doc)

    dataset = load_robustness_families_from_split(raw_doc)
    # Must now have exactly 11 distinct families
    assert len(dataset.families) == 11
    family_ids = [f.family_id for f in dataset.families]
    assert "brand-new-family" in family_ids


def test_v1_grouping_recommender_prediction_independence():
    """Recommender predictions or model inferences have zero role in dataset family loading."""
    dataset = load_robustness_dataset(DEV_SPLIT_PATH)
    # Grouping is completely static and deterministic
    assert len(dataset.families) == 10
    for fam in dataset.families:
        assert isinstance(fam.family_id, str)
        assert len(fam.variants) >= 1


# ---------------------------------------------------------------------------
# 3. Review Decision Trust Boundary & Dataset Revision Binding (Tests A-E)
# ---------------------------------------------------------------------------


def test_review_trust_boundary_a_changed_text():
    """Test A: Modifying variant text MUST cause stale review rejection."""
    fam = _sample_family("fam-tb-a")
    draft = generate_draft_variant(fam, PerturbationClass.TYPO_NOISE, seed=11)
    fam_with_draft = RobustnessFamily(
        family_id=fam.family_id,
        title=fam.title,
        workload_stratum=fam.workload_stratum,
        difficulty=fam.difficulty,
        executable_workload_id=fam.executable_workload_id,
        gold_structured_intent=fam.gold_structured_intent,
        candidate_gold=fam.candidate_gold,
        profile_gold=fam.profile_gold,
        image_gold=fam.image_gold,
        policy_gold=fam.policy_gold,
        variants=fam.variants + (draft,),
        label_review=fam.label_review,
    )
    dataset = RobustnessDataset(dataset_id="test-tb-a", families=(fam_with_draft,))
    rows = extract_review_rows(dataset)
    target_row = next(r for r in rows if r.variant_id == draft.variant_id)

    # Modify variant text
    modified_draft = RobustnessVariant(
        variant_id=draft.variant_id,
        family_id=draft.family_id,
        intent="Altered intent text",
        code_context=draft.code_context,
        metadata=draft.metadata,
        dataset_size_gb=draft.dataset_size_gb,
    )
    mod_fam = RobustnessFamily(
        family_id=fam.family_id,
        title=fam.title,
        workload_stratum=fam.workload_stratum,
        difficulty=fam.difficulty,
        executable_workload_id=fam.executable_workload_id,
        gold_structured_intent=fam.gold_structured_intent,
        candidate_gold=fam.candidate_gold,
        profile_gold=fam.profile_gold,
        image_gold=fam.image_gold,
        policy_gold=fam.policy_gold,
        variants=fam.variants + (modified_draft,),
        label_review=fam.label_review,
    )
    mod_dataset = RobustnessDataset(dataset_id="test-tb-a", families=(mod_fam,))

    with pytest.raises(StaleReviewError, match="text hash mismatch"):
        apply_review_decisions(
            mod_dataset,
            [
                {
                    "variant_id": draft.variant_id,
                    "family_id": fam.family_id,
                    "variant_text_sha256": target_row.variant_text_sha256,
                    "human_review_status": "approved",
                    "equivalence_status": "reviewed_equivalent",
                    "reviewed_by": "annotator",
                    "notes": "Approved",
                }
            ],
        )


def test_review_trust_boundary_b_changed_gold():
    """Test B: Modifying gold candidates causes dataset canonical checksum mismatch."""
    fam = _sample_family("fam-tb-b")
    dataset = RobustnessDataset(dataset_id="test-tb-b", families=(fam,))
    rows = extract_review_rows(dataset)
    target_row = rows[1]

    # Modify candidate gold
    mod_fam = RobustnessFamily(
        family_id=fam.family_id,
        title=fam.title,
        workload_stratum=fam.workload_stratum,
        difficulty=fam.difficulty,
        executable_workload_id=fam.executable_workload_id,
        gold_structured_intent=fam.gold_structured_intent,
        candidate_gold={"preferred_candidate_ids": ["different-gold-candidate"], "acceptable_candidate_ids": ["different-gold-candidate"]},
        profile_gold=fam.profile_gold,
        image_gold=fam.image_gold,
        policy_gold=fam.policy_gold,
        variants=fam.variants,
        label_review=fam.label_review,
    )
    mod_dataset = RobustnessDataset(dataset_id="test-tb-b", families=(mod_fam,))

    with pytest.raises(StaleReviewError, match="dataset canonical checksum mismatch"):
        apply_review_decisions(
            mod_dataset,
            [
                {
                    "variant_id": target_row.variant_id,
                    "family_id": fam.family_id,
                    "variant_text_sha256": target_row.variant_text_sha256,
                    "dataset_canonical_sha256": target_row.dataset_canonical_sha256,
                    "human_review_status": "approved",
                    "equivalence_status": "reviewed_equivalent",
                    "reviewed_by": "annotator",
                    "notes": "Approved",
                }
            ],
        )


def test_review_trust_boundary_c_changed_canonical():
    """Test C: Modifying canonical baseline causes dataset canonical checksum mismatch."""
    fam = _sample_family("fam-tb-c")
    dataset = RobustnessDataset(dataset_id="test-tb-c", families=(fam,))
    rows = extract_review_rows(dataset)
    target_row = rows[1]

    # Modify canonical variant intent
    new_can = RobustnessVariant(
        variant_id=fam.canonical_variant.variant_id,
        family_id=fam.family_id,
        intent="Modified canonical baseline meaning",
        code_context=(),
        metadata=fam.canonical_variant.metadata,
    )
    mod_fam = RobustnessFamily(
        family_id=fam.family_id,
        title=fam.title,
        workload_stratum=fam.workload_stratum,
        difficulty=fam.difficulty,
        executable_workload_id=fam.executable_workload_id,
        gold_structured_intent=fam.gold_structured_intent,
        candidate_gold=fam.candidate_gold,
        profile_gold=fam.profile_gold,
        image_gold=fam.image_gold,
        policy_gold=fam.policy_gold,
        variants=(new_can,) + fam.variants[1:],
        label_review=fam.label_review,
    )
    mod_dataset = RobustnessDataset(dataset_id="test-tb-c", families=(mod_fam,))

    with pytest.raises(StaleReviewError, match="dataset canonical checksum mismatch"):
        apply_review_decisions(
            mod_dataset,
            [
                {
                    "variant_id": target_row.variant_id,
                    "family_id": fam.family_id,
                    "variant_text_sha256": target_row.variant_text_sha256,
                    "dataset_canonical_sha256": target_row.dataset_canonical_sha256,
                    "human_review_status": "approved",
                    "equivalence_status": "reviewed_equivalent",
                    "reviewed_by": "annotator",
                    "notes": "Approved",
                }
            ],
        )


def test_review_trust_boundary_d_changed_perturbation_class():
    """Test D: Modifying perturbation class causes dataset checksum mismatch."""
    fam = _sample_family("fam-tb-d")
    dataset = RobustnessDataset(dataset_id="test-tb-d", families=(fam,))
    rows = extract_review_rows(dataset)
    target_row = rows[1]

    # Modify perturbation class of variant 1
    new_meta = VariantMetadata(
        variant_type=PerturbationClass.INFORMAL_COLLOQUIAL,
        language=fam.variants[1].metadata.language,
        source=fam.variants[1].metadata.source,
        human_review_status=fam.variants[1].metadata.human_review_status,
        equivalence_status=fam.variants[1].metadata.equivalence_status,
        expected_semantic_differences=fam.variants[1].metadata.expected_semantic_differences,
        notes=fam.variants[1].metadata.notes,
    )
    mod_var = RobustnessVariant(
        variant_id=fam.variants[1].variant_id,
        family_id=fam.variants[1].family_id,
        intent=fam.variants[1].intent,
        code_context=fam.variants[1].code_context,
        metadata=new_meta,
    )
    mod_fam = RobustnessFamily(
        family_id=fam.family_id,
        title=fam.title,
        workload_stratum=fam.workload_stratum,
        difficulty=fam.difficulty,
        executable_workload_id=fam.executable_workload_id,
        gold_structured_intent=fam.gold_structured_intent,
        candidate_gold=fam.candidate_gold,
        profile_gold=fam.profile_gold,
        image_gold=fam.image_gold,
        policy_gold=fam.policy_gold,
        variants=(fam.variants[0], mod_var) + fam.variants[2:],
        label_review=fam.label_review,
    )
    mod_dataset = RobustnessDataset(dataset_id="test-tb-d", families=(mod_fam,))

    with pytest.raises(StaleReviewError, match="dataset canonical checksum mismatch"):
        apply_review_decisions(
            mod_dataset,
            [
                {
                    "variant_id": target_row.variant_id,
                    "family_id": fam.family_id,
                    "variant_text_sha256": target_row.variant_text_sha256,
                    "dataset_canonical_sha256": target_row.dataset_canonical_sha256,
                    "human_review_status": "approved",
                    "equivalence_status": "reviewed_equivalent",
                    "reviewed_by": "annotator",
                    "notes": "Approved",
                }
            ],
        )


def test_review_trust_boundary_e_unchanged_semantic_dataset():
    """Test E: Exact same dataset revision accepted without error."""
    fam = _sample_family("fam-tb-e")
    dataset = RobustnessDataset(dataset_id="test-tb-e", families=(fam,))
    rows = extract_review_rows(dataset)
    target_row = rows[1]

    updated = apply_review_decisions(
        dataset,
        [
            {
                "variant_id": target_row.variant_id,
                "family_id": fam.family_id,
                "variant_text_sha256": target_row.variant_text_sha256,
                "dataset_canonical_sha256": target_row.dataset_canonical_sha256,
                "human_review_status": "approved",
                "equivalence_status": "reviewed_equivalent",
                "reviewed_by": "annotator-lead",
                "notes": "Verified identical revision",
            }
        ],
    )
    target_var = next(v for v in updated.families[0].variants if v.variant_id == target_row.variant_id)
    assert target_var.metadata.human_review_status == "approved"
    assert "reviewed_by: annotator-lead" in target_var.metadata.notes[-1]


# ---------------------------------------------------------------------------
# 4. Generator Provenance Complete Verification
# ---------------------------------------------------------------------------


def test_generator_provenance_complete():
    fam = _sample_family("fam-gen")
    draft = generate_draft_variant(fam, PerturbationClass.TYPO_NOISE, seed=777)

    # Check provenance attributes
    assert draft.metadata.source == VariantSource.GENERATED_DRAFT
    assert draft.metadata.human_review_status == HumanReviewStatus.PENDING
    assert draft.metadata.equivalence_status == EquivalenceStatus.PENDING_REVIEW

    notes_str = " ".join(draft.metadata.notes)
    assert "generator_id: protocol-v5-robustness-draft-generator-v1.0.0" in notes_str
    assert "template_version: v1.0.0" in notes_str
    assert "seed: 777" in notes_str
    assert f"source_canonical_id: {fam.canonical_variant.variant_id}" in notes_str


def test_generator_refuses_confirmatory_datasets():
    conf_fam = _sample_family("fam-conf", role="confirmatory")
    with pytest.raises(PermissionError, match="strictly prohibited on confirmatory"):
        generate_draft_variant(conf_fam, PerturbationClass.TYPO_NOISE)


# ---------------------------------------------------------------------------
# 5. Explicit Negative-Control Verification
# ---------------------------------------------------------------------------


def test_negative_control_non_equivalent_metrics():
    """Verify non_equivalent meaning-changing perturbations are excluded from SRR/WCFR and tracked separately."""
    can_var = _sample_variant("cpu-can", intent="Train a small CPU-only scikit-learn model.")
    neg_var = _sample_variant(
        "gpu-neg",
        intent="Train a CUDA PyTorch workload that requires a GPU.",
        equivalence_status=EquivalenceStatus.NON_EQUIVALENT,
    )
    fam = _sample_family("neg-fam", variants=[can_var, neg_var])
    dataset = RobustnessDataset(dataset_id="test-neg", families=(fam,))

    preds = {
        "cpu-can": {"variant_id": "cpu-can", "ranked_candidate_ids": ["medium-scipy-data-science"]},
        # Model appropriately changed recommendation for GPU demand
        "gpu-neg": {"variant_id": "gpu-neg", "ranked_candidate_ids": ["large-pytorch-deep-learning"]},
    }

    metrics = compute_robustness_metrics(dataset, preds, system_name="p2")
    # 0 reviewed_equivalent variants -> SRR denominator is 0
    assert metrics.semantic_robustness_denominator == 0
    assert metrics.semantic_robustness_rate_micro is None
    assert metrics.worst_case_family_total_count == 0

    # Negative control tracked separately
    assert metrics.non_equivalent_count == 1
    assert metrics.non_equivalent_changed_count == 1
    assert metrics.non_equivalent_sensitivity_rate == 1.0


# ---------------------------------------------------------------------------
# 6. Explicit Controlled-Ambiguity Verification
# ---------------------------------------------------------------------------


def test_controlled_ambiguity_metrics_and_separation():
    can_var = _sample_variant("amb-can", intent="Standard pandas workflow.")
    amb_var = _sample_variant(
        "amb-var",
        variant_type=PerturbationClass.AMBIGUOUS_OR_CONFLICTING_SIGNAL,
        intent="Need 1 CPU core but also 8 A100 GPUs simultaneously.",
        equivalence_status=EquivalenceStatus.CONTROLLED_AMBIGUITY,
    )
    fam = _sample_family("amb-fam", variants=[can_var, amb_var])
    dataset = RobustnessDataset(dataset_id="test-amb", families=(fam,))

    preds = {
        "amb-can": {"variant_id": "amb-can", "ranked_candidate_ids": ["medium-scipy-data-science"]},
        "amb-var": {"variant_id": "amb-var", "ranked_candidate_ids": ["medium-scipy-data-science"], "detected_ambiguous": True},
    }

    metrics = compute_robustness_metrics(dataset, preds, system_name="p2")
    # Ambiguity variant excluded from SRR
    assert metrics.semantic_robustness_denominator == 0
    assert metrics.ambiguity_total_count == 1
    assert metrics.ambiguity_detection_count == 1
    assert metrics.ambiguity_detection_rate == 1.0


# ---------------------------------------------------------------------------
# 7. Four-Quadrant Transition Matrix & Pair Records
# ---------------------------------------------------------------------------


def test_four_quadrant_transition_matrix():
    fam_ok = _sample_family(
        "fam-ok",
        variants=[
            _sample_variant("fam-ok-can", family_id="fam-ok"),
            _sample_variant("fam-ok-v1", family_id="fam-ok", equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT),
            _sample_variant("fam-ok-v2", family_id="fam-ok", equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT),
        ],
    )
    fam_fail = _sample_family(
        "fam-fail",
        variants=[
            _sample_variant("fam-fail-can", family_id="fam-fail"),
            _sample_variant("fam-fail-v1", family_id="fam-fail", equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT),
            _sample_variant("fam-fail-v2", family_id="fam-fail", equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT),
        ],
    )
    dataset = RobustnessDataset(dataset_id="test-trans", families=(fam_ok, fam_fail))

    preds = {
        "fam-ok-can": {"variant_id": "fam-ok-can", "ranked_candidate_ids": ["medium-scipy-data-science"]},
        "fam-ok-v1": {"variant_id": "fam-ok-v1", "ranked_candidate_ids": ["medium-scipy-data-science"]},
        "fam-ok-v2": {"variant_id": "fam-ok-v2", "ranked_candidate_ids": ["unacceptable"]},
        "fam-fail-can": {"variant_id": "fam-fail-can", "ranked_candidate_ids": ["unacceptable"]},
        "fam-fail-v1": {"variant_id": "fam-fail-v1", "ranked_candidate_ids": ["medium-scipy-data-science"]},
        "fam-fail-v2": {"variant_id": "fam-fail-v2", "ranked_candidate_ids": ["unacceptable"]},
    }

    metrics = compute_robustness_metrics(dataset, preds, system_name="p2")
    tm = metrics.transition_matrix

    assert tm.stable_correct_count == 1
    assert tm.degradation_count == 1
    assert tm.improvement_count == 1
    assert tm.stable_incorrect_count == 1
    assert tm.total_evaluated == 4
    assert tm.conditioned_degradation_rate == 0.5
    assert tm.conditioned_improvement_rate == 0.5


# ---------------------------------------------------------------------------
# 8. CSV Formula Injection Defense and Privacy Redaction Tests
# ---------------------------------------------------------------------------


def test_csv_formula_injection_defense_closure():
    hostile_vars = [
        _sample_variant("h1", intent="=HYPERLINK('http://malicious.example')"),
        _sample_variant("h2", intent="+SUM(A1:A10)"),
        _sample_variant("h3", intent="-1+1"),
        _sample_variant("h4", intent="@SUM(1,2)"),
    ]
    fam = _sample_family("hostile-fam", variants=[_sample_variant("can")] + hostile_vars)
    dataset = RobustnessDataset(dataset_id="test-hostile", families=(fam,))

    csv_text = export_equivalence_review(dataset, format="csv")
    for pattern in ("'=HYPERLINK", "'+SUM", "'-1+1", "'@SUM"):
        assert pattern in csv_text


def test_redaction_safety_closure():
    """Verify TOP_SECRET_E2_CLOSURE_SENTINEL_53B9 is never leaked in error messages."""
    sentinel = "TOP_SECRET_E2_CLOSURE_SENTINEL_53B9"
    var = _sample_variant("secret-var", intent=f"Run code with {sentinel}", equivalence_status=EquivalenceStatus.NON_EQUIVALENT)
    fam = _sample_family("secret-fam", variants=[var])

    try:
        validate_robustness_family(fam)
    except RobustnessValidationError as exc:
        assert sentinel not in str(exc)


# ---------------------------------------------------------------------------
# 9. Complete Synthetic E2 Development Lifecycle Integration Test
# ---------------------------------------------------------------------------


def test_complete_synthetic_e2_development_lifecycle(tmp_path: Path):
    """End-to-end synthetic validation of the complete E2 robustness lifecycle."""
    # 1. Canonical family authoring
    can_var = _sample_variant(
        "e2-fam-canonical",
        family_id="e2-fam",
        intent="Analyze tabular CSV data using pandas on CPU.",
        code_context=["import pandas as pd", "df = pd.read_csv('input.csv')"],
        equivalence_status=EquivalenceStatus.CANONICAL_REFERENCE,
    )
    fam = _sample_family("e2-fam", variants=[can_var])

    # 2. Draft generation (development only)
    drafts = generate_family_drafts(fam, seed=101)
    assert len(drafts) == 7
    for d in drafts:
        assert d.metadata.source == VariantSource.GENERATED_DRAFT
        assert d.metadata.human_review_status == HumanReviewStatus.PENDING

    # Add negative control (meaning-changing)
    neg_control = _sample_variant(
        "e2-fam-neg-control",
        family_id="e2-fam",
        intent="Train a 70B parameter LLM on 8x NVIDIA H100 GPUs.",
        equivalence_status=EquivalenceStatus.NON_EQUIVALENT,
    )

    all_variants = (can_var,) + drafts + (neg_control,)
    fam_with_all = RobustnessFamily(
        family_id=fam.family_id,
        title=fam.title,
        workload_stratum=fam.workload_stratum,
        difficulty=fam.difficulty,
        executable_workload_id=fam.executable_workload_id,
        gold_structured_intent=fam.gold_structured_intent,
        candidate_gold=fam.candidate_gold,
        profile_gold=fam.profile_gold,
        image_gold=fam.image_gold,
        policy_gold=fam.policy_gold,
        variants=all_variants,
        label_review=fam.label_review,
    )
    dataset = RobustnessDataset(dataset_id="e2-lifecycle-dev", families=(fam_with_all,))
    validate_robustness_dataset(dataset)

    # 3. Export review artifact
    md_review = export_equivalence_review(dataset, format="markdown")
    csv_review = export_equivalence_review(dataset, format="csv")
    json_review = export_equivalence_review(dataset, format="json")
    assert "e2-fam-canonical" in md_review
    assert "e2-fam-neg-control" in csv_review
    assert len(json.loads(json_review)) == 9

    # 4. Human review approval & Stale rejection test
    with pytest.raises(StaleReviewError):
        apply_review_decisions(
            dataset,
            [
                {
                    "variant_id": drafts[0].variant_id,
                    "family_id": "e2-fam",
                    "variant_text_sha256": "bad-stale-hash-12345",
                    "human_review_status": "approved",
                    "reviewed_by": "human-reviewer",
                    "notes": "Reviewed",
                }
            ],
        )

    # Apply valid approvals
    decisions = []
    for d in drafts:
        eq_status = (
            EquivalenceStatus.CONTROLLED_AMBIGUITY.value
            if d.metadata.variant_type == PerturbationClass.AMBIGUOUS_OR_CONFLICTING_SIGNAL
            else EquivalenceStatus.REVIEWED_EQUIVALENT.value
        )
        decisions.append(
            {
                "variant_id": d.variant_id,
                "family_id": "e2-fam",
                "variant_text_sha256": d.text_sha256,
                "dataset_canonical_sha256": dataset.canonical_sha256,
                "human_review_status": "approved",
                "equivalence_status": eq_status,
                "reviewed_by": "lead-adjudicator",
                "notes": "Approved in synthetic verification pass.",
            }
        )
    reviewed_dataset = apply_review_decisions(dataset, decisions)

    # 5. Synthetic model execution & pair evaluation
    preds = {
        "e2-fam-canonical": {
            "variant_id": "e2-fam-canonical",
            "ranked_candidate_ids": ["medium-scipy-data-science"],
            "selected_profile": "medium",
            "selected_image": "scipy-data-science",
            "extracted_capabilities": ["pandas"],
            "detected_infeasible": False,
            "latency_seconds": 0.05,
        },
        "e2-fam-neg-control": {
            "variant_id": "e2-fam-neg-control",
            "ranked_candidate_ids": ["large-pytorch-deep-learning"],
            "selected_profile": "large",
            "selected_image": "pytorch-deep-learning",
            "detected_infeasible": False,
            "latency_seconds": 0.04,
        },
    }
    for idx, d in enumerate(drafts):
        if d.metadata.variant_type == PerturbationClass.AMBIGUOUS_OR_CONFLICTING_SIGNAL:
            preds[d.variant_id] = {
                "variant_id": d.variant_id,
                "ranked_candidate_ids": ["medium-scipy-data-science"],
                "detected_ambiguous": True,
                "latency_seconds": 0.06,
            }
        elif idx == 0:
            # 1 failure -> degradation
            preds[d.variant_id] = {
                "variant_id": d.variant_id,
                "ranked_candidate_ids": ["small-minimal-python"],
                "latency_seconds": 0.05,
            }
        else:
            # Success -> stable_correct
            preds[d.variant_id] = {
                "variant_id": d.variant_id,
                "ranked_candidate_ids": ["medium-scipy-data-science"],
                "selected_profile": "medium",
                "selected_image": "scipy-data-science",
                "extracted_capabilities": ["pandas"],
                "latency_seconds": 0.05,
            }

    # 6. Evaluate pairs and compute metrics
    pairs = evaluate_robustness_pairs(reviewed_dataset, preds, system_name="p2")
    assert pairs.total_pairs == 9

    metrics = compute_robustness_metrics(reviewed_dataset, preds, system_name="p2")
    # 6 reviewed equivalent perturbations (canonical, ambiguity, and neg-control excluded)
    assert metrics.semantic_robustness_denominator == 6
    # 5 passed (1 draft degraded)
    assert metrics.semantic_robustness_numerator == 5
    assert metrics.semantic_robustness_rate_micro == pytest.approx(5.0 / 6.0)
    assert metrics.semantic_robustness_rate_macro == pytest.approx(5.0 / 6.0)

    # WCFR fails due to 1 degradation
    assert metrics.worst_case_family_robustness == 0.0
    assert metrics.transition_matrix.stable_correct_count == 5
    assert metrics.transition_matrix.degradation_count == 1

    # Ambiguity and Negative controls
    assert metrics.ambiguity_detection_rate == 1.0
    assert metrics.non_equivalent_count == 1
    assert metrics.non_equivalent_sensitivity_rate == 1.0


# ---------------------------------------------------------------------------
# 10. CLI Tests
# ---------------------------------------------------------------------------


def test_cli_summary(capsys: pytest.CaptureFixture[str]):
    exit_code = main(["summary", str(DEV_SPLIT_PATH)])
    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Dataset ID: protocol-v5-development-2026-08-22" in captured
    assert "Families: 10" in captured
    assert "Total variants: 18" in captured
    assert "Valid reviewed-equivalent variants: 8" in captured


def test_cli_review_markdown(capsys: pytest.CaptureFixture[str]):
    exit_code = main(["review", str(DEV_SPLIT_PATH), "--format", "markdown"])
    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "# Workload Variant Semantic Equivalence Review" in captured


# ---------------------------------------------------------------------------
# 11. Taxonomy, Structural, and Invariant Tests
# ---------------------------------------------------------------------------


def test_perturbation_class_normalization():
    assert normalize_perturbation_class("canonical") == PerturbationClass.CANONICAL
    assert normalize_perturbation_class("canonical_en") == PerturbationClass.CANONICAL
    assert (
        normalize_perturbation_class("paraphrase_without_obvious_keywords")
        == PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS
    )
    assert (
        normalize_perturbation_class("paraphrase_en")
        == PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS
    )
    assert normalize_perturbation_class("vietnamese") == PerturbationClass.VIETNAMESE
    assert (
        normalize_perturbation_class("informal_or_noisy")
        == PerturbationClass.INFORMAL_COLLOQUIAL
    )
    assert normalize_perturbation_class("typo_noise") == PerturbationClass.TYPO_NOISE
    assert (
        normalize_perturbation_class("irrelevant_extra_context")
        == PerturbationClass.IRRELEVANT_EXTRA_CONTEXT
    )
    assert (
        normalize_perturbation_class("optional_code_context")
        == PerturbationClass.REQUIREMENT_EXPRESSED_IN_CODE_CONTEXT
    )
    assert (
        normalize_perturbation_class("optional_ambiguity_variant")
        == PerturbationClass.AMBIGUOUS_OR_CONFLICTING_SIGNAL
    )

    with pytest.raises(ValueError, match="Unsupported perturbation class"):
        normalize_perturbation_class("completely_invalid_class")


def test_to_gold_variant_class_mapping():
    assert to_gold_variant_class(PerturbationClass.CANONICAL) == "canonical_en"
    assert (
        to_gold_variant_class(PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS)
        == "paraphrase_en"
    )
    assert to_gold_variant_class(PerturbationClass.VIETNAMESE) == "vietnamese"
    assert (
        to_gold_variant_class(PerturbationClass.INFORMAL_COLLOQUIAL)
        == "informal_or_noisy"
    )
    assert (
        to_gold_variant_class(PerturbationClass.REQUIREMENT_EXPRESSED_IN_CODE_CONTEXT)
        == "optional_code_context"
    )
    assert (
        to_gold_variant_class(PerturbationClass.AMBIGUOUS_OR_CONFLICTING_SIGNAL)
        == "optional_ambiguity_variant"
    )


def test_variant_metadata_roundtrip():
    meta = VariantMetadata(
        variant_type=PerturbationClass.VIETNAMESE,
        language="vi",
        source=VariantSource.HUMAN_AUTHORED,
        human_review_status=HumanReviewStatus.APPROVED,
        equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT,
        expected_semantic_differences="Translation to Vietnamese",
        notes=("Checked against reference",),
    )
    d = meta.to_dict()
    assert d["variant_type"] == "vietnamese"
    assert d["language"] == "vi"
    assert d["source"] == "human_authored"
    assert d["human_review_status"] == "approved"
    assert d["equivalence_status"] == "reviewed_equivalent"

    rebuilt = VariantMetadata.from_dict(d)
    assert rebuilt == meta


def test_family_structural_invariants():
    fam = _sample_family("valid-fam")
    validate_robustness_family(fam)

    # 1. Zero variants
    with pytest.raises(RobustnessValidationError, match="zero variants"):
        empty_fam = RobustnessFamily(
            family_id="empty-fam",
            title="Empty",
            workload_stratum="data_processing",
            difficulty="medium",
            executable_workload_id=None,
            gold_structured_intent={},
            candidate_gold={},
            profile_gold={},
            image_gold={},
            policy_gold={},
            variants=(),
            label_review={},
        )
        validate_robustness_family(empty_fam)

    # 2. Duplicate variant IDs
    dup_var1 = _sample_variant("dup-id")
    dup_var2 = _sample_variant("dup-id")
    with pytest.raises(RobustnessValidationError, match="Duplicate variant ID"):
        dup_fam = RobustnessFamily(
            family_id="dup-fam",
            title="Dup",
            workload_stratum="data_processing",
            difficulty="medium",
            executable_workload_id=None,
            gold_structured_intent={},
            candidate_gold={},
            profile_gold={},
            image_gold={},
            policy_gold={},
            variants=(dup_var1, dup_var2),
            label_review={},
        )
        validate_robustness_family(dup_fam)

    # 3. Multiple canonical references
    can_var1 = _sample_variant("can-1", equivalence_status=EquivalenceStatus.CANONICAL_REFERENCE)
    can_var2 = _sample_variant("can-2", equivalence_status=EquivalenceStatus.CANONICAL_REFERENCE)
    with pytest.raises(RobustnessValidationError, match="multiple .* canonical references"):
        multi_can_fam = RobustnessFamily(
            family_id="multi-can-fam",
            title="Multi-can",
            workload_stratum="data_processing",
            difficulty="medium",
            executable_workload_id=None,
            gold_structured_intent={},
            candidate_gold={},
            profile_gold={},
            image_gold={},
            policy_gold={},
            variants=(can_var1, can_var2),
            label_review={},
        )
        validate_robustness_family(multi_can_fam)


def test_dataset_structural_invariants():
    fam1 = _sample_family("fam-1")
    fam2 = _sample_family("fam-2")
    dataset = RobustnessDataset(dataset_id="valid-ds", families=(fam1, fam2))
    validate_robustness_dataset(dataset)

    with pytest.raises(RobustnessValidationError, match="Duplicate family ID"):
        dup_fam_ds = RobustnessDataset(dataset_id="dup-ds", families=(fam1, fam1))
        validate_robustness_dataset(dup_fam_ds)

    dup_glob_var = _sample_variant("glob-var-1", family_id="fam-b")
    fam_b = _sample_family("fam-b", variants=[_sample_variant("fam-1-canonical", family_id="fam-b")])
    with pytest.raises(RobustnessValidationError, match="Duplicate global variant ID"):
        dup_var_ds = RobustnessDataset(dataset_id="dup-var-ds", families=(fam1, fam_b))
        validate_robustness_dataset(dup_var_ds)


def test_load_robustness_dataset_from_dev_split():
    dataset = load_robustness_dataset(DEV_SPLIT_PATH)
    assert dataset.dataset_id == "protocol-v5-development-2026-08-22"
    assert dataset.role == "development"
    assert len(dataset.families) == 10
    assert dataset.total_variants == 18

    for family in dataset.families:
        assert family.canonical_variant is not None
        assert len(family.variants) >= 1
        assert family.family_id != ""


def test_draft_generator_stamps_generated_draft():
    fam = _sample_family()
    draft = generate_draft_variant(
        fam,
        PerturbationClass.INFORMAL_COLLOQUIAL,
        seed=123,
    )
    assert draft.metadata.source == VariantSource.GENERATED_DRAFT
    assert draft.metadata.human_review_status == HumanReviewStatus.PENDING
    assert draft.metadata.equivalence_status == EquivalenceStatus.PENDING_REVIEW
    assert "generator_id: protocol-v5-robustness-draft-generator-v1.0.0" in draft.metadata.notes[1]
    assert draft.family_id == fam.family_id


def test_generate_family_drafts_covers_classes():
    fam = _sample_family()
    drafts = generate_family_drafts(fam, seed=42)
    assert len(drafts) == 7
    classes = {d.metadata.variant_type for d in drafts}
    assert PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS in classes
    assert PerturbationClass.VIETNAMESE in classes
    assert PerturbationClass.INFORMAL_COLLOQUIAL in classes
    assert PerturbationClass.TYPO_NOISE in classes
    assert PerturbationClass.IRRELEVANT_EXTRA_CONTEXT in classes
    assert PerturbationClass.REQUIREMENT_EXPRESSED_IN_CODE_CONTEXT in classes
    assert PerturbationClass.AMBIGUOUS_OR_CONFLICTING_SIGNAL in classes


def test_export_equivalence_review_formats():
    fam = _sample_family()
    dataset = RobustnessDataset(
        dataset_id="test-dataset",
        families=(fam,),
    )

    csv_text = export_equivalence_review(dataset, format="csv")
    reader = csv.DictReader(csv_text.splitlines())
    rows = list(reader)
    assert len(rows) == 5
    assert rows[0]["family_id"] == "data-cleaning-family"
    assert rows[0]["variant_type"] == "canonical"

    json_text = export_equivalence_review(dataset, format="json")
    parsed = json.loads(json_text)
    assert len(parsed) == 5
    assert parsed[0]["variant_id"] == "data-cleaning-family-canonical"

    md_text = export_equivalence_review(dataset, format="markdown")
    assert "# Workload Variant Semantic Equivalence Review" in md_text


def test_invalid_review_decisions_rejection():
    fam = _sample_family()
    dataset = RobustnessDataset(dataset_id="test-invalid-decisions", families=(fam,))

    with pytest.raises(InvalidReviewDecisionError, match="unknown variant"):
        apply_review_decisions(dataset, [{"variant_id": "nonexistent-var"}])

    can_id = fam.canonical_variant.variant_id
    with pytest.raises(InvalidReviewDecisionError, match="does not match variant family"):
        apply_review_decisions(
            dataset,
            [{"variant_id": can_id, "family_id": "wrong-family-id"}],
        )

    with pytest.raises(InvalidReviewDecisionError, match="Duplicate review decision"):
        apply_review_decisions(
            dataset,
            [
                {"variant_id": can_id, "notes": "Decision 1"},
                {"variant_id": can_id, "notes": "Decision 2"},
            ],
        )

    with pytest.raises(InvalidReviewDecisionError, match="requires non-empty reviewer identity"):
        apply_review_decisions(
            dataset,
            [
                {
                    "variant_id": can_id,
                    "human_review_status": "approved",
                    "notes": "Valid note",
                }
            ],
        )


def test_srr_micro_vs_macro_weighting_fixture():
    # Family A: 20 reviewed equivalent variants, 10 pass -> 50% family SRR
    fam_a_vars = [_sample_variant("fam-a-can", family_id="fam-a")]
    for i in range(1, 21):
        fam_a_vars.append(
            _sample_variant(
                f"fam-a-v{i}",
                family_id="fam-a",
                variant_type=PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS,
                equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT,
            )
        )
    fam_a = _sample_family("fam-a", variants=fam_a_vars)

    # Family B: 1 reviewed equivalent variant, 1 passes -> 100% family SRR
    fam_b_vars = [
        _sample_variant("fam-b-can", family_id="fam-b"),
        _sample_variant("fam-b-v1", family_id="fam-b", variant_type=PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS, equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT),
    ]
    fam_b = _sample_family("fam-b", variants=fam_b_vars)

    dataset = RobustnessDataset(dataset_id="test-micro-macro", families=(fam_a, fam_b))

    preds = {}
    preds["fam-a-can"] = {"variant_id": "fam-a-can", "ranked_candidate_ids": ["medium-scipy-data-science"]}
    preds["fam-b-can"] = {"variant_id": "fam-b-can", "ranked_candidate_ids": ["medium-scipy-data-science"]}

    # Fam A: exactly 10 pass, 10 fail
    for idx, v in enumerate(fam_a.variants[1:]):
        if idx < 10:
            preds[v.variant_id] = {"variant_id": v.variant_id, "ranked_candidate_ids": ["medium-scipy-data-science"]}
        else:
            preds[v.variant_id] = {"variant_id": v.variant_id, "ranked_candidate_ids": ["unacceptable-candidate"]}

    # Fam B: 1 passes
    preds["fam-b-v1"] = {"variant_id": "fam-b-v1", "ranked_candidate_ids": ["medium-scipy-data-science"]}

    metrics = compute_robustness_metrics(dataset, preds, system_name="p2")

    # Variant-micro SRR = 11/21
    assert metrics.semantic_robustness_denominator == 21
    assert metrics.semantic_robustness_numerator == 11
    assert metrics.semantic_robustness_rate_micro == pytest.approx(11.0 / 21.0)

    # Family-macro SRR = (10/20 + 1/1) / 2 = (0.5 + 1.0) / 2 = 0.75
    assert metrics.semantic_robustness_rate_macro == pytest.approx(0.75)


def test_evaluate_robustness_pairs_preservation():
    fam = _sample_family("fam-p")
    dataset = RobustnessDataset(dataset_id="test-pairs", families=(fam,))

    preds = {
        "fam-p-canonical": {
            "variant_id": "fam-p-canonical",
            "ranked_candidate_ids": ["medium-scipy-data-science"],
            "selected_profile": "medium",
            "selected_image": "scipy-data-science",
            "extracted_capabilities": ["pandas"],
            "detected_infeasible": False,
            "latency_seconds": 0.04,
        },
        "fam-p-para": {
            "variant_id": "fam-p-para",
            "ranked_candidate_ids": ["medium-scipy-data-science"],
            "selected_profile": "medium",
            "selected_image": "scipy-data-science",
            "extracted_capabilities": ["pandas"],
            "detected_infeasible": False,
            "latency_seconds": 0.06,
        },
        "fam-p-vi": {
            "variant_id": "fam-p-vi",
            "ranked_candidate_ids": ["large-scipy-data-science"],
            "selected_profile": "large",
            "selected_image": "scipy-data-science",
            "extracted_capabilities": ["pandas"],
            "detected_infeasible": False,
            "latency_seconds": 0.05,
        },
        "fam-p-noise": {
            "variant_id": "fam-p-noise",
            "ranked_candidate_ids": ["small-minimal-python"],
            "selected_profile": "small",
            "selected_image": "minimal-python",
            "extracted_capabilities": [],
            "detected_infeasible": False,
            "latency_seconds": 0.03,
        },
        "fam-p-ambig": {
            "variant_id": "fam-p-ambig",
            "ranked_candidate_ids": ["medium-scipy-data-science"],
            "detected_infeasible": False,
            "detected_ambiguous": True,
            "latency_seconds": 0.08,
        },
    }

    pairs_output = evaluate_robustness_pairs(dataset, preds, system_name="p2")
    assert pairs_output.total_pairs == 5
    assert len(pairs_output.records) == 5

    para_rec = next(r for r in pairs_output.records if r.variant_id == "fam-p-para")
    assert para_rec.matches_canonical_candidate is True
    assert para_rec.transition_category == "stable_correct"

    jsonl = pairs_output.to_jsonl()
    assert len([line for line in jsonl.splitlines() if line.strip()]) == 5


def test_deterministic_evaluation_reordering_invariance():
    fam1 = _sample_family("fam-1")
    fam2 = _sample_family("fam-2")

    dataset_a = RobustnessDataset(dataset_id="test-order-a", families=(fam1, fam2))
    dataset_b = RobustnessDataset(dataset_id="test-order-b", families=(fam2, fam1))

    preds = {}
    for fam in (fam1, fam2):
        for v in fam.variants:
            preds[v.variant_id] = {
                "variant_id": v.variant_id,
                "ranked_candidate_ids": ["medium-scipy-data-science"],
            }

    metrics_a = compute_robustness_metrics(dataset_a, preds, system_name="p2")
    metrics_b = compute_robustness_metrics(dataset_b, preds, system_name="p2")

    assert metrics_a.semantic_robustness_rate_micro == metrics_b.semantic_robustness_rate_micro
    assert metrics_a.semantic_robustness_rate_macro == metrics_b.semantic_robustness_rate_macro
    assert metrics_a.worst_case_family_robustness == metrics_b.worst_case_family_robustness


def test_robustness_family_role_and_classification_integrity():
    dev_fam = _sample_family("fam-dev", role="development")
    assert dev_fam.role == "development"
    assert dev_fam.evidence_classification == "development_only"
    assert dev_fam.is_confirmatory is False

    conf_fam = _sample_family("fam-conf", role="confirmatory")
    assert conf_fam.role == "confirmatory"
    assert conf_fam.evidence_classification == "human_reviewed_confirmatory"
    assert conf_fam.is_confirmatory is True

    # Check is_confirmatory triggered by classification alone
    leak_class_fam = _sample_family(
        "fam-leak",
        role="development",
        evidence_classification="human_reviewed_confirmatory",
    )
    assert leak_class_fam.is_confirmatory is True
    with pytest.raises(RobustnessValidationError, match="invalid role"):
        validate_robustness_family(leak_class_fam)

    # Check is_confirmatory triggered by source_provenance
    leak_prov_fam = _sample_family("fam-leak2", role="development")
    leak_prov_fam = RobustnessFamily(
        family_id=leak_prov_fam.family_id,
        title=leak_prov_fam.title,
        workload_stratum=leak_prov_fam.workload_stratum,
        difficulty=leak_prov_fam.difficulty,
        executable_workload_id=leak_prov_fam.executable_workload_id,
        gold_structured_intent=leak_prov_fam.gold_structured_intent,
        candidate_gold=leak_prov_fam.candidate_gold,
        profile_gold=leak_prov_fam.profile_gold,
        image_gold=leak_prov_fam.image_gold,
        policy_gold=leak_prov_fam.policy_gold,
        variants=leak_prov_fam.variants,
        label_review=leak_prov_fam.label_review,
        source_provenance={"source_split": "confirmatory"},
        role="development",
        evidence_classification="development_only",
    )
    assert leak_prov_fam.is_confirmatory is True
    with pytest.raises(RobustnessValidationError, match="invalid role"):
        validate_robustness_family(leak_prov_fam)


def test_validate_robustness_dataset_role_isolation():
    dev_fam = _sample_family("fam-dev", role="development")
    conf_fam = _sample_family("fam-conf", role="confirmatory")

    # Confirmatory family inside development dataset
    bad_dev_ds = RobustnessDataset(
        dataset_id="ds-dev-poisoned",
        families=(dev_fam, conf_fam),
        role="development",
    )
    with pytest.raises(
        RobustnessValidationError, match="contains confirmatory family"
    ):
        validate_robustness_dataset(bad_dev_ds)

    # Development family inside confirmatory dataset
    bad_conf_ds = RobustnessDataset(
        dataset_id="ds-conf-poisoned",
        families=(conf_fam, dev_fam),
        role="confirmatory",
    )
    with pytest.raises(
        RobustnessValidationError, match="contains non-confirmatory family"
    ):
        validate_robustness_dataset(bad_conf_ds)


def test_loader_propagates_role_and_classification():
    dataset = load_robustness_dataset(DEV_SPLIT_PATH)
    assert dataset.role == "development"
    for fam in dataset.families:
        assert fam.role == "development"
        assert fam.evidence_classification == "historical_formative_development_only"
        assert fam.is_confirmatory is False


def test_generator_unconditionally_rejects_confirmatory_families():
    conf_fam = _sample_family("fam-conf", role="confirmatory")
    with pytest.raises(
        PermissionError, match="strictly prohibited on confirmatory family"
    ):
        generate_draft_variant(conf_fam, PerturbationClass.TYPO_NOISE)

    with pytest.raises(
        PermissionError, match="strictly prohibited on confirmatory family"
    ):
        generate_family_drafts(conf_fam)


def test_cli_draft_refuses_confirmatory_input(tmp_path: Path):
    conf_fam = _sample_family("fam-conf", role="confirmatory")
    conf_dataset = RobustnessDataset(
        dataset_id="conf-dataset",
        families=(conf_fam,),
        role="confirmatory",
    )
    conf_file = tmp_path / "conf-input.yaml"
    conf_file.write_text(yaml.safe_dump(conf_dataset.to_dict()), encoding="utf-8")

    with pytest.raises(
        PermissionError,
        match="strictly prohibited on confirmatory",
    ):
        main(["draft", str(conf_file)])


def test_cli_draft_fail_closed_existing_output(tmp_path: Path):
    dev_dataset = RobustnessDataset(
        dataset_id="dev-dataset",
        families=(_sample_family("fam-1"),),
        role="development",
    )
    input_file = tmp_path / "dev-input.yaml"
    input_file.write_text(yaml.safe_dump(dev_dataset.to_dict()), encoding="utf-8")

    existing_output = tmp_path / "already_exists.yaml"
    existing_output.write_text("existing content", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        main(["draft", str(input_file), "--output", str(existing_output)])

    existing_review = tmp_path / "already_exists.md"
    existing_review.write_text("existing review", encoding="utf-8")

    output_ok = tmp_path / "not_yet.yaml"
    with pytest.raises(FileExistsError, match="already exists"):
        main(
            [
                "draft",
                str(input_file),
                "--output",
                str(output_ok),
                "--review-output",
                str(existing_review),
            ]
        )
    assert not output_ok.exists()


def test_cli_draft_deterministic_and_provenance(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    dev_dataset = RobustnessDataset(
        dataset_id="dev-dataset",
        families=(_sample_family("fam-1"),),
        role="development",
    )
    input_file = tmp_path / "dev-input.yaml"
    input_file.write_text(yaml.safe_dump(dev_dataset.to_dict()), encoding="utf-8")

    output1 = tmp_path / "out1.yaml"
    review1 = tmp_path / "rev1.md"

    # Run draft CLI with seed 42
    exit_code = main(
        [
            "draft",
            str(input_file),
            "--seed",
            "42",
            "--output",
            str(output1),
            "--review-output",
            str(review1),
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["status"] == "DRAFTS_GENERATED"
    assert report["seed"] == 42
    assert report["generated_drafts_count"] == 7

    # Validate output artifact
    loaded1 = load_robustness_dataset(output1)
    assert loaded1.role == "development"
    assert loaded1.metadata["generator_seed"] == 42
    assert loaded1.metadata["evidence_classification"] == "generated_draft"
    assert (
        loaded1.metadata["generator_id"]
        == "protocol-v5-robustness-draft-generator-v1.0.0"
    )
    assert len(loaded1.families[0].variants) == 5 + 7

    # Validate review artifact
    assert review1.exists()
    rev_text = review1.read_text(encoding="utf-8")
    assert "fam-1" in rev_text
    assert "GENERATED_DRAFT" in rev_text or "generated_draft" in rev_text.lower()

    # Run again with same seed to test determinism
    output2 = tmp_path / "out2.yaml"
    main(
        [
            "draft",
            str(input_file),
            "--seed",
            "42",
            "--output",
            str(output2),
        ]
    )
    loaded2 = load_robustness_dataset(output2)
    assert loaded1.canonical_sha256 == loaded2.canonical_sha256

    # Run with different seed to test sensitivity
    output3 = tmp_path / "out3.yaml"
    main(
        [
            "draft",
            str(input_file),
            "--seed",
            "99",
            "--output",
            str(output3),
        ]
    )
    loaded3 = load_robustness_dataset(output3)
    assert loaded1.canonical_sha256 != loaded3.canonical_sha256


def test_canonical_checksum_binds_trust_and_provenance_state():
    base_ds = load_robustness_dataset(DEV_SPLIT_PATH)
    base_sha = base_ds.canonical_sha256

    # 1. Mutating ONLY dataset role changes checksum
    ds_role = RobustnessDataset(
        dataset_id=base_ds.dataset_id,
        families=base_ds.families,
        protocol_version=base_ds.protocol_version,
        role="development_staging",
        metadata=base_ds.metadata,
    )
    assert ds_role.canonical_sha256 != base_sha

    # 2. Mutating ONLY family role changes checksum
    fam0 = base_ds.families[0]
    fam0_role = RobustnessFamily(
        family_id=fam0.family_id,
        title=fam0.title,
        workload_stratum=fam0.workload_stratum,
        difficulty=fam0.difficulty,
        executable_workload_id=fam0.executable_workload_id,
        gold_structured_intent=fam0.gold_structured_intent,
        candidate_gold=fam0.candidate_gold,
        profile_gold=fam0.profile_gold,
        image_gold=fam0.image_gold,
        policy_gold=fam0.policy_gold,
        variants=fam0.variants,
        label_review=fam0.label_review,
        source_provenance=fam0.source_provenance,
        role="development_staging",
        evidence_classification=fam0.evidence_classification,
    )
    ds_fam_role = RobustnessDataset(
        dataset_id=base_ds.dataset_id,
        families=(fam0_role,) + base_ds.families[1:],
        protocol_version=base_ds.protocol_version,
        role=base_ds.role,
        metadata=base_ds.metadata,
    )
    assert ds_fam_role.canonical_sha256 != base_sha

    # 3. Mutating ONLY family evidence_classification changes checksum
    fam0_class = RobustnessFamily(
        family_id=fam0.family_id,
        title=fam0.title,
        workload_stratum=fam0.workload_stratum,
        difficulty=fam0.difficulty,
        executable_workload_id=fam0.executable_workload_id,
        gold_structured_intent=fam0.gold_structured_intent,
        candidate_gold=fam0.candidate_gold,
        profile_gold=fam0.profile_gold,
        image_gold=fam0.image_gold,
        policy_gold=fam0.policy_gold,
        variants=fam0.variants,
        label_review=fam0.label_review,
        source_provenance=fam0.source_provenance,
        role=fam0.role,
        evidence_classification="synthetic_test_fixture",
    )
    ds_fam_class = RobustnessDataset(
        dataset_id=base_ds.dataset_id,
        families=(fam0_class,) + base_ds.families[1:],
        protocol_version=base_ds.protocol_version,
        role=base_ds.role,
        metadata=base_ds.metadata,
    )
    assert ds_fam_class.canonical_sha256 != base_sha

    # 4. Mutating ONLY family source_provenance changes checksum
    fam0_prov = RobustnessFamily(
        family_id=fam0.family_id,
        title=fam0.title,
        workload_stratum=fam0.workload_stratum,
        difficulty=fam0.difficulty,
        executable_workload_id=fam0.executable_workload_id,
        gold_structured_intent=fam0.gold_structured_intent,
        candidate_gold=fam0.candidate_gold,
        profile_gold=fam0.profile_gold,
        image_gold=fam0.image_gold,
        policy_gold=fam0.policy_gold,
        variants=fam0.variants,
        label_review=fam0.label_review,
        source_provenance={"source_dataset_id": "mutated-source"},
        role=fam0.role,
        evidence_classification=fam0.evidence_classification,
    )
    ds_fam_prov = RobustnessDataset(
        dataset_id=base_ds.dataset_id,
        families=(fam0_prov,) + base_ds.families[1:],
        protocol_version=base_ds.protocol_version,
        role=base_ds.role,
        metadata=base_ds.metadata,
    )
    assert ds_fam_prov.canonical_sha256 != base_sha

    # 5. Mutating ONLY dataset metadata trust keys changes checksum
    ds_meta = RobustnessDataset(
        dataset_id=base_ds.dataset_id,
        families=base_ds.families,
        protocol_version=base_ds.protocol_version,
        role=base_ds.role,
        metadata={"generator_seed": 777},
    )
    assert ds_meta.canonical_sha256 != base_sha

    # 6. Mutating ONLY variant source changes checksum
    v0 = fam0.variants[0]
    v0_meta = VariantMetadata(
        variant_type=v0.metadata.variant_type,
        language=v0.metadata.language,
        source=VariantSource.GENERATED_DRAFT,
        human_review_status=v0.metadata.human_review_status,
        equivalence_status=v0.metadata.equivalence_status,
        expected_semantic_differences=v0.metadata.expected_semantic_differences,
        notes=v0.metadata.notes,
    )
    v0_mutated = RobustnessVariant(
        variant_id=v0.variant_id,
        family_id=v0.family_id,
        intent=v0.intent,
        code_context=v0.code_context,
        metadata=v0_meta,
        dataset_size_gb=v0.dataset_size_gb,
    )
    fam0_var_src = RobustnessFamily(
        family_id=fam0.family_id,
        title=fam0.title,
        workload_stratum=fam0.workload_stratum,
        difficulty=fam0.difficulty,
        executable_workload_id=fam0.executable_workload_id,
        gold_structured_intent=fam0.gold_structured_intent,
        candidate_gold=fam0.candidate_gold,
        profile_gold=fam0.profile_gold,
        image_gold=fam0.image_gold,
        policy_gold=fam0.policy_gold,
        variants=(v0_mutated,) + fam0.variants[1:],
        label_review=fam0.label_review,
        source_provenance=fam0.source_provenance,
        role=fam0.role,
        evidence_classification=fam0.evidence_classification,
    )
    ds_var_src = RobustnessDataset(
        dataset_id=base_ds.dataset_id,
        families=(fam0_var_src,) + base_ds.families[1:],
        protocol_version=base_ds.protocol_version,
        role=base_ds.role,
        metadata=base_ds.metadata,
    )
    assert ds_var_src.canonical_sha256 != base_sha

    # 7. Mutating ONLY variant human_review_status changes checksum
    v0_meta_rev = VariantMetadata(
        variant_type=v0.metadata.variant_type,
        language=v0.metadata.language,
        source=v0.metadata.source,
        human_review_status=HumanReviewStatus.REJECTED,
        equivalence_status=v0.metadata.equivalence_status,
        expected_semantic_differences=v0.metadata.expected_semantic_differences,
        notes=v0.metadata.notes,
    )
    v0_mutated_rev = RobustnessVariant(
        variant_id=v0.variant_id,
        family_id=v0.family_id,
        intent=v0.intent,
        code_context=v0.code_context,
        metadata=v0_meta_rev,
        dataset_size_gb=v0.dataset_size_gb,
    )
    fam0_var_rev = RobustnessFamily(
        family_id=fam0.family_id,
        title=fam0.title,
        workload_stratum=fam0.workload_stratum,
        difficulty=fam0.difficulty,
        executable_workload_id=fam0.executable_workload_id,
        gold_structured_intent=fam0.gold_structured_intent,
        candidate_gold=fam0.candidate_gold,
        profile_gold=fam0.profile_gold,
        image_gold=fam0.image_gold,
        policy_gold=fam0.policy_gold,
        variants=(v0_mutated_rev,) + fam0.variants[1:],
        label_review=fam0.label_review,
        source_provenance=fam0.source_provenance,
        role=fam0.role,
        evidence_classification=fam0.evidence_classification,
    )
    ds_var_rev = RobustnessDataset(
        dataset_id=base_ds.dataset_id,
        families=(fam0_var_rev,) + base_ds.families[1:],
        protocol_version=base_ds.protocol_version,
        role=base_ds.role,
        metadata=base_ds.metadata,
    )
    assert ds_var_rev.canonical_sha256 != base_sha

    # 8. Mutating ONLY variant equivalence_status changes checksum
    v0_meta_equiv = VariantMetadata(
        variant_type=v0.metadata.variant_type,
        language=v0.metadata.language,
        source=v0.metadata.source,
        human_review_status=v0.metadata.human_review_status,
        equivalence_status=EquivalenceStatus.NON_EQUIVALENT,
        expected_semantic_differences=v0.metadata.expected_semantic_differences,
        notes=v0.metadata.notes,
    )
    v0_mutated_equiv = RobustnessVariant(
        variant_id=v0.variant_id,
        family_id=v0.family_id,
        intent=v0.intent,
        code_context=v0.code_context,
        metadata=v0_meta_equiv,
        dataset_size_gb=v0.dataset_size_gb,
    )
    fam0_var_equiv = RobustnessFamily(
        family_id=fam0.family_id,
        title=fam0.title,
        workload_stratum=fam0.workload_stratum,
        difficulty=fam0.difficulty,
        executable_workload_id=fam0.executable_workload_id,
        gold_structured_intent=fam0.gold_structured_intent,
        candidate_gold=fam0.candidate_gold,
        profile_gold=fam0.profile_gold,
        image_gold=fam0.image_gold,
        policy_gold=fam0.policy_gold,
        variants=(v0_mutated_equiv,) + fam0.variants[1:],
        label_review=fam0.label_review,
        source_provenance=fam0.source_provenance,
        role=fam0.role,
        evidence_classification=fam0.evidence_classification,
    )
    ds_var_equiv = RobustnessDataset(
        dataset_id=base_ds.dataset_id,
        families=(fam0_var_equiv,) + base_ds.families[1:],
        protocol_version=base_ds.protocol_version,
        role=base_ds.role,
        metadata=base_ds.metadata,
    )
    assert ds_var_equiv.canonical_sha256 != base_sha


def test_stale_review_attack_fails_closed_on_trust_mutation():
    base_ds = load_robustness_dataset(DEV_SPLIT_PATH)
    rows = extract_review_rows(base_ds)
    target_row = rows[0]

    # Valid review decision matching current canonical sha256
    decision = {
        "variant_id": target_row.variant_id,
        "dataset_id": base_ds.dataset_id,
        "dataset_canonical_sha256": base_ds.canonical_sha256,
        "family_id": target_row.family_id,
        "variant_text_sha256": target_row.variant_text_sha256,
        "human_review_status": "approved",
        "equivalence_status": "reviewed_equivalent",
        "reviewed_by": "reviewer-alice",
        "notes": "Validly reviewed on original revision",
    }

    # Verify decision applies cleanly to original dataset
    approved_ds = apply_review_decisions(base_ds, [decision])
    assert approved_ds is not None

    # Mutate ONLY evidence_classification
    fam0 = base_ds.families[0]
    fam0_mutated = RobustnessFamily(
        family_id=fam0.family_id,
        title=fam0.title,
        workload_stratum=fam0.workload_stratum,
        difficulty=fam0.difficulty,
        executable_workload_id=fam0.executable_workload_id,
        gold_structured_intent=fam0.gold_structured_intent,
        candidate_gold=fam0.candidate_gold,
        profile_gold=fam0.profile_gold,
        image_gold=fam0.image_gold,
        policy_gold=fam0.policy_gold,
        variants=fam0.variants,
        label_review=fam0.label_review,
        source_provenance=fam0.source_provenance,
        role=fam0.role,
        evidence_classification="generated_draft",
    )
    mutated_ds = RobustnessDataset(
        dataset_id=base_ds.dataset_id,
        families=(fam0_mutated,) + base_ds.families[1:],
        protocol_version=base_ds.protocol_version,
        role=base_ds.role,
        metadata=base_ds.metadata,
    )
    with pytest.raises(StaleReviewError, match="dataset canonical checksum mismatch"):
        apply_review_decisions(mutated_ds, [decision])

    # Mutate ONLY source_provenance
    fam0_prov = RobustnessFamily(
        family_id=fam0.family_id,
        title=fam0.title,
        workload_stratum=fam0.workload_stratum,
        difficulty=fam0.difficulty,
        executable_workload_id=fam0.executable_workload_id,
        gold_structured_intent=fam0.gold_structured_intent,
        candidate_gold=fam0.candidate_gold,
        profile_gold=fam0.profile_gold,
        image_gold=fam0.image_gold,
        policy_gold=fam0.policy_gold,
        variants=fam0.variants,
        label_review=fam0.label_review,
        source_provenance={"source_dataset_id": "adversary_dataset"},
        role=fam0.role,
        evidence_classification=fam0.evidence_classification,
    )
    mutated_prov_ds = RobustnessDataset(
        dataset_id=base_ds.dataset_id,
        families=(fam0_prov,) + base_ds.families[1:],
        protocol_version=base_ds.protocol_version,
        role=base_ds.role,
        metadata=base_ds.metadata,
    )
    with pytest.raises(StaleReviewError, match="dataset canonical checksum mismatch"):
        apply_review_decisions(mutated_prov_ds, [decision])


def test_cli_format_mismatch_and_authoritative_enforcement(tmp_path: Path):
    ds = load_robustness_dataset(DEV_SPLIT_PATH)
    input_file = tmp_path / "dev_input.yaml"
    ds_dict = ds.to_dict()
    input_file.write_text(yaml.safe_dump(ds_dict), encoding="utf-8")

    # Mismatch --format json with .yaml output fails closed
    with pytest.raises(ValueError, match="conflicts with output extension"):
        main(
            [
                "draft",
                str(input_file),
                "--format",
                "json",
                "--output",
                str(tmp_path / "drafts.yaml"),
            ]
        )

    # Mismatch --format yaml with .json output fails closed
    with pytest.raises(ValueError, match="conflicts with output extension"):
        main(
            [
                "draft",
                str(input_file),
                "--format",
                "yaml",
                "--output",
                str(tmp_path / "drafts.json"),
            ]
        )

    # Unsupported output extension fails closed
    with pytest.raises(ValueError, match="Unsupported output file extension"):
        main(
            [
                "draft",
                str(input_file),
                "--output",
                str(tmp_path / "drafts.txt"),
            ]
        )

    # Unsupported review output extension fails closed
    with pytest.raises(ValueError, match="Unsupported review output file extension"):
        main(
            [
                "draft",
                str(input_file),
                "--output",
                str(tmp_path / "drafts.yaml"),
                "--review-output",
                str(tmp_path / "review.txt"),
            ]
        )

    # Review command format mismatch with output extension fails closed
    with pytest.raises(ValueError, match="conflicts with output extension"):
        main(
            [
                "review",
                str(input_file),
                "--format",
                "csv",
                "--output",
                str(tmp_path / "review.md"),
            ]
        )

    # Matching combinations succeed
    out_yaml = tmp_path / "valid.yaml"
    assert (
        main(
            [
                "draft",
                str(input_file),
                "--format",
                "yaml",
                "--output",
                str(out_yaml),
            ]
        )
        == 0
    )
    assert out_yaml.exists()

    out_json = tmp_path / "valid.json"
    assert (
        main(
            [
                "draft",
                str(input_file),
                "--format",
                "json",
                "--output",
                str(out_json),
            ]
        )
        == 0
    )
    assert out_json.exists()


def test_end_to_end_review_to_development_compile_path(tmp_path: Path):
    # 1. Begin with legitimate development authoring material
    base_split = load_robustness_families_from_split(DEV_SPLIT_PATH)
    fam = base_split.families[0]
    dev_dataset = RobustnessDataset(
        dataset_id="e2e-dev-lifecycle",
        families=(fam,),
        protocol_version="5.0.0",
        role="development",
        metadata={"creator": "test-author", "evidence_classification": "development_only"},
    )

    # 2. Generate deterministic robustness drafts
    drafts = generate_family_drafts(fam, seed=42)
    assert len(drafts) == 7
    # 3. Prove generated variants remain generated_draft, pending review, non-confirmatory
    for draft in drafts:
        assert draft.metadata.source == VariantSource.GENERATED_DRAFT
        assert draft.metadata.human_review_status in {
            HumanReviewStatus.DRAFT,
            HumanReviewStatus.PENDING,
        }
        assert draft.is_pending_review

    source_provenance = {
        "source_dataset_id": "e2e-dev-lifecycle",
        "source_schema_version": "protocol-v5-gold-family-v1.0.0",
        "source_family_id": fam.family_id,
        "source_case_ids": [fam.variants[0].variant_id],
        "source_split": "development",
        "source_file_sha256": "0" * 64,
        "evidence_classification": "development_only",
        "original_label_sha256": "0" * 64,
    }
    updated_fam = RobustnessFamily(
        family_id=fam.family_id,
        title=fam.title,
        workload_stratum=fam.workload_stratum,
        difficulty=fam.difficulty,
        executable_workload_id=fam.executable_workload_id,
        gold_structured_intent=fam.gold_structured_intent,
        candidate_gold=fam.candidate_gold,
        profile_gold=fam.profile_gold,
        image_gold=fam.image_gold,
        policy_gold=fam.policy_gold,
        variants=fam.variants + tuple(drafts),
        label_review={
            "status": "approved",
            "reviewed_by": "expert-reviewer-1",
            "reviewed_at_utc": "2026-08-25T00:00:00Z",
            "notes": ["Approved for development benchmark"],
        },
        source_provenance=source_provenance,
        role="development",
        evidence_classification="generated_draft",
    )
    draft_dataset = RobustnessDataset(
        dataset_id="e2e-dev-lifecycle-drafts",
        families=(updated_fam,),
        protocol_version="5.0.0",
        role="development",
        metadata={
            "generator_id": "protocol-v5-robustness-draft-generator-v1.0.0",
            "generator_version": "1.0.0",
            "generator_seed": 42,
            "evidence_classification": "generated_draft",
            "source_dataset_id": "e2e-dev-lifecycle",
            "source_canonical_sha256": dev_dataset.canonical_sha256,
        },
    )

    # 4. Export checksum-bound review material
    review_rows = extract_review_rows(draft_dataset)
    assert len(review_rows) == len(updated_fam.variants)
    assert all(r.dataset_canonical_sha256 == draft_dataset.canonical_sha256 for r in review_rows)

    # 5. Apply valid human review decisions
    decisions = []
    for d in drafts:
        decisions.append(
            {
                "variant_id": d.variant_id,
                "dataset_id": draft_dataset.dataset_id,
                "dataset_canonical_sha256": draft_dataset.canonical_sha256,
                "family_id": updated_fam.family_id,
                "variant_text_sha256": d.text_sha256,
                "human_review_status": "approved",
                "equivalence_status": "reviewed_equivalent",
                "reviewed_by": "human-expert-1",
                "notes": "Reviewed and confirmed semantically equivalent for development benchmarks",
            }
        )
    reviewed_dataset = apply_review_decisions(draft_dataset, decisions)

    # 6. Preserve generated origin/provenance
    for v in reviewed_dataset.families[0].variants:
        if v.variant_id in {d.variant_id for d in drafts}:
            assert v.metadata.source == VariantSource.GENERATED_DRAFT
            assert v.metadata.human_review_status == HumanReviewStatus.APPROVED
            assert v.is_reviewed_equivalent
            assert any("reviewed_by: human-expert-1" in n for n in v.metadata.notes)

    # 7. Convert using existing authoritative gold-authoring path
    catalog_identity = current_catalog_identity()
    review_policy = {
        "required_workload_strata": [fam.workload_stratum],
        "max_preferred_profile_share": 1.0,
        "max_preferred_image_share": 1.0,
    }
    source_datasets = [
        {
            "dataset_id": "e2e-dev-lifecycle",
            "schema_version": "protocol-v5-gold-family-v1.0.0",
            "source_file_sha256": "0" * 64,
            "source_split": "development",
            "evidence_classification": "development_only",
        }
    ]
    gold_doc = reviewed_dataset.to_gold_authoring_dict(
        catalog_identity=catalog_identity,
        review_policy=review_policy,
        lifecycle="draft",
        evidence_classification="development_only",
        source_datasets=source_datasets,
    )
    validated_gold = validate_gold_dataset(gold_doc)
    assert validated_gold.dataset_metadata["role"] == "development"

    # 8. Freeze only using existing legitimate lifecycle
    frozen_doc = deepcopy(gold_doc)
    frozen_doc["dataset_metadata"]["lifecycle"] = "frozen"
    frozen_doc["dataset_metadata"]["freeze_metadata"] = {
        "frozen_at_utc": "2026-08-25T14:00:00Z",
        "frozen_by": "custodian-1",
    }
    validated_frozen = validate_gold_dataset(frozen_doc)

    # 9. Compile as DEVELOPMENT
    compiled_split = compile_gold_dataset(validated_frozen)
    assert compiled_split.split_manifest.role == "development"
    # 10. Prove compiled result preserves enough lineage to identify its origin
    assert compiled_split.split_manifest.dataset_id == "e2e-dev-lifecycle-drafts"
    assert len(compiled_split.cases) == len(updated_fam.variants)
    assert any(
        c.case_id == drafts[0].variant_id for c in compiled_split.cases
    )

    # 11. Attempt to use the same generated lineage as CONFIRMATORY -> fails closed
    conf_doc = deepcopy(frozen_doc)
    conf_doc["dataset_metadata"]["role"] = "confirmatory"
    conf_doc["dataset_metadata"]["evidence_classification"] = "human_reviewed_confirmatory"
    # Should fail before confirmatory compilation
    with pytest.raises(GoldDatasetValidationError):
        validate_gold_dataset(conf_doc)

