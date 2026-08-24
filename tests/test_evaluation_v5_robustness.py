"""Comprehensive adversarial test suite for Protocol-v5 E2 natural-language robustness tooling."""

from __future__ import annotations

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
) -> RobustnessFamily:
    acc = acceptable_candidates or ["medium-scipy-data-science", "large-scipy-data-science"]
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
        source_provenance={"source_split": role},
    )


# ---------------------------------------------------------------------------
# 1. Perturbation Taxonomy and Metadata Tests
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
    assert (
        normalize_perturbation_class("typo_noise")
        == PerturbationClass.TYPO_NOISE
    )
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


# ---------------------------------------------------------------------------
# 2. Family Structural Invariants and Validation
# ---------------------------------------------------------------------------


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

    # Duplicate family ID across dataset
    with pytest.raises(RobustnessValidationError, match="Duplicate family ID"):
        dup_fam_ds = RobustnessDataset(dataset_id="dup-ds", families=(fam1, fam1))
        validate_robustness_dataset(dup_fam_ds)

    # Duplicate global variant ID across families
    dup_glob_var = _sample_variant("glob-var-1", family_id="fam-b")
    fam_b = _sample_family("fam-b", variants=[_sample_variant("fam-1-canonical", family_id="fam-b")])
    with pytest.raises(RobustnessValidationError, match="Duplicate global variant ID"):
        dup_var_ds = RobustnessDataset(dataset_id="dup-var-ds", families=(fam1, fam_b))
        validate_robustness_dataset(dup_var_ds)


# ---------------------------------------------------------------------------
# 3. Loader Tests
# ---------------------------------------------------------------------------


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


def test_load_robustness_dataset_default_argument():
    dataset = load_robustness_dataset()
    assert len(dataset.families) == 10


# ---------------------------------------------------------------------------
# 4. Development-Only Draft Generator Tests & Safety Guardrails
# ---------------------------------------------------------------------------


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
    assert "data-cleaning-family-draft-informal-colloquial-123" in draft.variant_id


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

    for d in drafts:
        assert d.metadata.source == VariantSource.GENERATED_DRAFT
        assert d.metadata.human_review_status == HumanReviewStatus.PENDING


def test_confirmatory_family_draft_generation_prohibited():
    conf_fam = _sample_family("conf-fam", role="confirmatory")
    with pytest.raises(PermissionError, match="strictly prohibited on confirmatory"):
        generate_draft_variant(conf_fam, PerturbationClass.TYPO_NOISE)


# ---------------------------------------------------------------------------
# 5. Equivalence Review Export & Approval Application Tests
# ---------------------------------------------------------------------------


def test_export_equivalence_review_formats():
    fam = _sample_family()
    dataset = RobustnessDataset(
        dataset_id="test-dataset",
        families=(fam,),
    )

    # 1. CSV
    csv_text = export_equivalence_review(dataset, format="csv")
    reader = csv.DictReader(csv_text.splitlines())
    rows = list(reader)
    assert len(rows) == 5
    assert rows[0]["family_id"] == "data-cleaning-family"
    assert rows[0]["variant_type"] == "canonical"
    assert rows[0]["proposed_equivalence_decision"] == "canonical_reference"

    # 2. JSON
    json_text = export_equivalence_review(dataset, format="json")
    parsed = json.loads(json_text)
    assert len(parsed) == 5
    assert parsed[0]["variant_id"] == "data-cleaning-family-canonical"

    # 3. Markdown
    md_text = export_equivalence_review(dataset, format="markdown")
    assert "# Workload Variant Semantic Equivalence Review" in md_text
    assert "## Family: Data cleaning family (`data-cleaning-family`)" in md_text
    assert "`data-cleaning-family-canonical`" in md_text


def test_apply_review_decisions_happy_path():
    fam = _sample_family()
    draft = generate_draft_variant(fam, PerturbationClass.TYPO_NOISE, seed=99)
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
    dataset = RobustnessDataset(
        dataset_id="test-dataset",
        families=(fam_with_draft,),
    )

    # Initially draft is pending
    assert fam_with_draft.variants[-1].metadata.human_review_status == HumanReviewStatus.PENDING

    # Apply valid review decision
    updated = apply_review_decisions(
        dataset,
        [
            {
                "variant_id": draft.variant_id,
                "family_id": fam.family_id,
                "variant_text_sha256": draft.text_sha256,
                "human_review_status": "approved",
                "equivalence_status": "reviewed_equivalent",
                "reviewed_by": "lead-evaluator",
                "notes": "Reviewed and approved by human annotator.",
            }
        ],
    )
    target = updated.families[0].variants[-1]
    assert target.metadata.human_review_status == "approved"
    assert target.metadata.equivalence_status == "reviewed_equivalent"
    assert "Reviewed and approved" in target.metadata.notes[0]
    assert "reviewed_by: lead-evaluator" in target.metadata.notes[1]


def test_stale_review_rejection_trust_boundary():
    """Modifying underlying variant text MUST cause review decision to be rejected as stale."""
    fam = _sample_family()
    draft = generate_draft_variant(fam, PerturbationClass.TYPO_NOISE, seed=99)
    old_hash = draft.text_sha256

    # Create modified variant with altered text
    modified_draft = RobustnessVariant(
        variant_id=draft.variant_id,
        family_id=draft.family_id,
        intent="Completely different modified prompt intent",
        code_context=draft.code_context,
        metadata=draft.metadata,
        dataset_size_gb=draft.dataset_size_gb,
    )
    fam_with_modified = RobustnessFamily(
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
    dataset = RobustnessDataset(dataset_id="test-stale", families=(fam_with_modified,))

    # Attempting to apply review decision created for old_hash MUST raise StaleReviewError
    with pytest.raises(StaleReviewError, match="Stale review decision for variant"):
        apply_review_decisions(
            dataset,
            [
                {
                    "variant_id": draft.variant_id,
                    "family_id": fam.family_id,
                    "variant_text_sha256": old_hash,
                    "human_review_status": "approved",
                    "equivalence_status": "reviewed_equivalent",
                    "reviewed_by": "annotator-1",
                    "notes": "Looks equivalent",
                }
            ],
        )


def test_invalid_review_decisions_rejection():
    fam = _sample_family()
    dataset = RobustnessDataset(dataset_id="test-invalid-decisions", families=(fam,))

    # 1. Unknown variant
    with pytest.raises(InvalidReviewDecisionError, match="unknown variant"):
        apply_review_decisions(dataset, [{"variant_id": "nonexistent-var"}])

    # 2. Wrong family
    can_id = fam.canonical_variant.variant_id
    with pytest.raises(InvalidReviewDecisionError, match="does not match variant family"):
        apply_review_decisions(
            dataset,
            [{"variant_id": can_id, "family_id": "wrong-family-id"}],
        )

    # 3. Duplicate decisions for same variant
    with pytest.raises(InvalidReviewDecisionError, match="Duplicate review decision"):
        apply_review_decisions(
            dataset,
            [
                {"variant_id": can_id, "notes": "Decision 1"},
                {"variant_id": can_id, "notes": "Decision 2"},
            ],
        )

    # 4. Approved without reviewer identity
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

    # 5. Approved without notes
    with pytest.raises(InvalidReviewDecisionError, match="requires non-empty review notes"):
        apply_review_decisions(
            dataset,
            [
                {
                    "variant_id": can_id,
                    "human_review_status": "approved",
                    "reviewed_by": "annotator-1",
                    "notes": "",
                }
            ],
        )


def test_csv_formula_injection_defense():
    """Verify that intent text beginning with formula prefixes is sanitized in CSV exports."""
    hostile_var = _sample_variant(
        "hostile-var",
        intent="=cmd|' /C calc'!A0",
        equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT,
    )
    fam = _sample_family("hostile-fam", variants=[_sample_variant("can"), hostile_var])
    dataset = RobustnessDataset(dataset_id="test-formula", families=(fam,))

    csv_text = export_equivalence_review(dataset, format="csv")
    assert "'=cmd|' /C calc'!A0" in csv_text

    # The in-memory object's intent MUST remain unmutated
    assert hostile_var.intent == "=cmd|' /C calc'!A0"


# ---------------------------------------------------------------------------
# 6. Micro SRR vs Macro SRR Mathematical Weighting Fixture
# ---------------------------------------------------------------------------


def test_srr_micro_vs_macro_weighting_fixture():
    """Prove that Micro SRR and Macro SRR properly expose weighting differences:

    Family A: 20 variants, 10 pass -> 50% family SRR
    Family B: 1 variant, 1 passes -> 100% family SRR
    Total variants: 21, total passing: 11
    Micro SRR = 11/21 ~= 52.38%
    Macro SRR = (50% + 100%) / 2 = 75.00%
    """
    # Family A: canonical + 19 equivalent variants (20 total)
    fam_a_vars = [_sample_variant("fam-a-can", family_id="fam-a")]
    for i in range(1, 20):
        fam_a_vars.append(
            _sample_variant(
                f"fam-a-v{i}",
                family_id="fam-a",
                variant_type=PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS,
                equivalence_status=EquivalenceStatus.REVIEWED_EQUIVALENT,
            )
        )
    fam_a = _sample_family("fam-a", variants=fam_a_vars)

    # Family B: canonical (1 total equivalent variant)
    fam_b_vars = [_sample_variant("fam-b-can", family_id="fam-b")]
    fam_b = _sample_family("fam-b", variants=fam_b_vars)

    dataset = RobustnessDataset(dataset_id="test-micro-macro", families=(fam_a, fam_b))

    preds = {}
    # Fam A: exactly 10 pass, 10 fail
    for idx, v in enumerate(fam_a.variants):
        if idx < 10:
            preds[v.variant_id] = {
                "variant_id": v.variant_id,
                "ranked_candidate_ids": ["medium-scipy-data-science"],
            }
        else:
            preds[v.variant_id] = {
                "variant_id": v.variant_id,
                "ranked_candidate_ids": ["unacceptable-candidate"],
            }

    # Fam B: 1 passes
    preds["fam-b-can"] = {
        "variant_id": "fam-b-can",
        "ranked_candidate_ids": ["medium-scipy-data-science"],
    }

    metrics = compute_robustness_metrics(dataset, preds, system_name="p2")

    # Variant-micro SRR = 11/21
    expected_micro = 11.0 / 21.0
    assert metrics.semantic_robustness_rate_micro == pytest.approx(expected_micro)
    assert metrics.semantic_robustness_numerator == 11
    assert metrics.semantic_robustness_denominator == 21

    # Family-macro SRR = (10/20 + 1/1) / 2 = (0.5 + 1.0) / 2 = 0.75
    expected_macro = (0.5 + 1.0) / 2.0
    assert metrics.semantic_robustness_rate_macro == pytest.approx(expected_macro)

    # Exposure stats
    assert metrics.min_variants_per_family == 1
    assert metrics.max_variants_per_family == 20
    assert metrics.mean_variants_per_family == pytest.approx(10.5)


# ---------------------------------------------------------------------------
# 7. Four-Quadrant Transition Matrix Tests
# ---------------------------------------------------------------------------


def test_four_quadrant_transition_matrix():
    """Verify classification of the 4 canonical->variant transitions:

    1. stable_correct (can=ok, var=ok)
    2. degradation (can=ok, var=fail)
    3. improvement (can=fail, var=ok)
    4. stable_incorrect (can=fail, var=fail)
    """
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
        # fam-ok canonical succeeds
        "fam-ok-can": {"variant_id": "fam-ok-can", "ranked_candidate_ids": ["medium-scipy-data-science"]},
        # v1 succeeds -> stable_correct
        "fam-ok-v1": {"variant_id": "fam-ok-v1", "ranked_candidate_ids": ["medium-scipy-data-science"]},
        # v2 fails -> degradation
        "fam-ok-v2": {"variant_id": "fam-ok-v2", "ranked_candidate_ids": ["unacceptable"]},
        # fam-fail canonical fails
        "fam-fail-can": {"variant_id": "fam-fail-can", "ranked_candidate_ids": ["unacceptable"]},
        # v1 succeeds -> improvement
        "fam-fail-v1": {"variant_id": "fam-fail-v1", "ranked_candidate_ids": ["medium-scipy-data-science"]},
        # v2 fails -> stable_incorrect
        "fam-fail-v2": {"variant_id": "fam-fail-v2", "ranked_candidate_ids": ["unacceptable"]},
    }

    metrics = compute_robustness_metrics(dataset, preds, system_name="p2")
    tm = metrics.transition_matrix

    assert tm.stable_correct_count == 1
    assert tm.degradation_count == 1
    assert tm.improvement_count == 1
    assert tm.stable_incorrect_count == 1
    assert tm.total_evaluated == 4

    # Conditioned rates:
    # Conditioned degradation = degradation / (stable_correct + degradation) = 1 / 2 = 50%
    assert tm.conditioned_degradation_rate == 0.5
    # Conditioned improvement = improvement / (stable_incorrect + improvement) = 1 / 2 = 50%
    assert tm.conditioned_improvement_rate == 0.5


# ---------------------------------------------------------------------------
# 8. Pair-Level Output & CSV / JSONL Preservation Tests
# ---------------------------------------------------------------------------


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

    # Check canonical comparisons
    para_rec = next(r for r in pairs_output.records if r.variant_id == "fam-p-para")
    assert para_rec.matches_canonical_candidate is True
    assert para_rec.matches_canonical_profile is True
    assert para_rec.matches_canonical_image is True
    assert para_rec.transition_category == "stable_correct"
    assert para_rec.capability_jaccard_with_canonical == 1.0

    vi_rec = next(r for r in pairs_output.records if r.variant_id == "fam-p-vi")
    assert vi_rec.is_acceptable is True
    assert vi_rec.matches_canonical_candidate is False
    assert vi_rec.transition_category == "stable_correct"

    noise_rec = next(r for r in pairs_output.records if r.variant_id == "fam-p-noise")
    assert noise_rec.is_acceptable is False
    assert noise_rec.transition_category == "degradation"

    # Check JSONL & CSV export
    jsonl = pairs_output.to_jsonl()
    lines = [line for line in jsonl.splitlines() if line.strip()]
    assert len(lines) == 5

    csv_text = pairs_output.to_csv()
    csv_rows = list(csv.DictReader(csv_text.splitlines()))
    assert len(csv_rows) == 5
    assert csv_rows[0]["transition_category"] == "canonical_baseline"


# ---------------------------------------------------------------------------
# 9. Deterministic Reordering Invariance Tests
# ---------------------------------------------------------------------------


def test_deterministic_evaluation_reordering_invariance():
    """Reordering input families or variants MUST NOT change metric results or pair records."""
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

    pairs_a = evaluate_robustness_pairs(dataset_a, preds, system_name="p2")
    pairs_b = evaluate_robustness_pairs(dataset_b, preds, system_name="p2")

    assert [r.variant_id for r in pairs_a.records] == [r.variant_id for r in pairs_b.records]


# ---------------------------------------------------------------------------
# 10. Hostile Unicode and Privacy Redaction Tests
# ---------------------------------------------------------------------------


def test_hostile_unicode_and_content_resilience():
    """Ensure complex Unicode, emoji, RTL marks, and code context hints survive."""
    hostile_intent = "Tiếng Việt có dấu 🚀 \u200e\u200f 'quotes' & \"double\" <xml>"
    hostile_code = ["import torch", "df = pd.read_csv('test,with,commas.csv')"]
    var = _sample_variant("hostile-unicode", intent=hostile_intent, code_context=hostile_code)
    fam = _sample_family("unicode-fam", variants=[var])
    dataset = RobustnessDataset(dataset_id="test-unicode", families=(fam,))

    csv_text = export_equivalence_review(dataset, format="csv")
    assert "Tiếng Việt có dấu 🚀" in csv_text

    json_text = export_equivalence_review(dataset, format="json")
    parsed = json.loads(json_text)
    assert parsed[0]["variant_text"] == hostile_intent + " [Code hints: import torch; df = pd.read_csv('test,with,commas.csv')]"


def test_redaction_safety_and_privacy():
    """Verify that exception messages do NOT leak secret prompt text."""
    secret_sentinel = "TOP_SECRET_E2_SENTINEL_93A7"
    var = _sample_variant(
        "secret-var",
        intent=f"Analyze secret dataset {secret_sentinel}",
        equivalence_status=EquivalenceStatus.NON_EQUIVALENT,
    )
    fam = _sample_family("secret-fam", variants=[var])  # Canonical marked non_equivalent

    try:
        validate_robustness_family(fam)
    except RobustnessValidationError as exc:
        msg = str(exc)
        assert secret_sentinel not in msg
        assert "secret-var" in msg


# ---------------------------------------------------------------------------
# 11. CLI Tests
# ---------------------------------------------------------------------------


def test_cli_summary(capsys: pytest.CaptureFixture[str]):
    exit_code = main(["summary", str(DEV_SPLIT_PATH)])
    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Dataset ID: protocol-v5-development-2026-08-22" in captured
    assert "Families: 10" in captured
    assert "Total variants: 18" in captured


def test_cli_review_markdown(capsys: pytest.CaptureFixture[str]):
    exit_code = main(["review", str(DEV_SPLIT_PATH), "--format", "markdown"])
    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "# Workload Variant Semantic Equivalence Review" in captured


# ---------------------------------------------------------------------------
# 12. Synthetic Complete E2 Development Lifecycle Integration Test
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
    # Attempt stale review decision with bad hash
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
                "human_review_status": "approved",
                "equivalence_status": eq_status,
                "reviewed_by": "lead-adjudicator",
                "notes": "Approved in synthetic verification pass.",
            }
        )
    reviewed_dataset = apply_review_decisions(dataset, decisions)

    # 5. Synthetic model execution & pair evaluation
    # Create predictions for each variant
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
            "ranked_candidate_ids": ["large-pytorch-deep-learning"],  # Changed prediction!
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
                "ranked_candidate_ids": ["small-minimal-python"],  # Unacceptable
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
    jsonl_output = pairs.to_jsonl()
    assert len(jsonl_output.strip().splitlines()) == 9

    metrics = compute_robustness_metrics(reviewed_dataset, preds, system_name="p2")
    # 7 equivalent variants (canonical + 6 reviewed equivalents; ambiguity and neg-control excluded)
    assert metrics.semantic_robustness_denominator == 7
    # 6 passed (canonical + 5 drafts)
    assert metrics.semantic_robustness_numerator == 6
    assert metrics.semantic_robustness_rate_micro == pytest.approx(6.0 / 7.0)
    assert metrics.semantic_robustness_rate_macro == pytest.approx(6.0 / 7.0)

    # WCFR fails due to 1 degradation
    assert metrics.worst_case_family_robustness == 0.0
    assert metrics.transition_matrix.stable_correct_count == 5
    assert metrics.transition_matrix.degradation_count == 1

    # Ambiguity detected
    assert metrics.ambiguity_detection_rate == 1.0

    # Negative control sensitivity
    assert metrics.non_equivalent_count == 1
    assert metrics.non_equivalent_sensitivity_rate == 1.0

