"""Robustness family and variant loader based on Protocol-v5 gold and split schemas."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from evaluation_v5.gold_dataset import (
    GOLD_DATASET_SCHEMA_VERSION,
    GoldDataset,
    LoadedGoldDataset,
    load_gold_dataset,
    validate_gold_dataset,
)
from evaluation_v5.split_dataset import (
    SPLIT_BUNDLE_SCHEMA_VERSION,
    SPLIT_BUNDLE_SCHEMA_VERSION_V2,
    LoadedSplit,
    SplitBundle,
    load_development_split,
    validate_split_bundle,
)

from .models import RobustnessDataset, RobustnessFamily, RobustnessVariant
from .taxonomy import (
    EquivalenceStatus,
    HumanReviewStatus,
    PerturbationClass,
    VariantMetadata,
    VariantSource,
    normalize_perturbation_class,
)


class RobustnessLoaderError(ValueError):
    """An error occurred loading or validating robustness families."""


def _variant_source_from_provenance(
    provenance: Mapping[str, Any] | None,
) -> VariantSource:
    if provenance is None:
        return VariantSource.HUMAN_AUTHORED
    classification = provenance.get("evidence_classification", "")
    if classification == "generated_draft":
        return VariantSource.GENERATED_DRAFT
    if classification == "synthetic_test_fixture":
        return VariantSource.SYNTHETIC_TEST
    source_dataset_id = provenance.get("source_dataset_id", "")
    if "intent-gold-v4" in source_dataset_id or "v4" in source_dataset_id:
        return VariantSource.IMPORTED_V4
    return VariantSource.HUMAN_AUTHORED


def load_robustness_families_from_gold(
    gold_data: GoldDataset | LoadedGoldDataset | Mapping[str, Any] | Path,
    *,
    workload_manifests: Sequence[Path] = (),
) -> RobustnessDataset:
    """Load robustness families from a Protocol-v5 family gold authoring dataset."""
    if isinstance(gold_data, (str, Path)):
        loaded_gold = load_gold_dataset(
            Path(gold_data), workload_manifests=workload_manifests
        )
        dataset = loaded_gold.dataset
    elif isinstance(gold_data, LoadedGoldDataset):
        dataset = gold_data.dataset
    elif isinstance(gold_data, GoldDataset):
        dataset = gold_data
    elif isinstance(gold_data, Mapping):
        dataset = validate_gold_dataset(
            gold_data, workload_manifests=workload_manifests
        )
    else:
        raise RobustnessLoaderError(f"Unsupported gold dataset type: {type(gold_data)}")

    meta = dataset.dataset_metadata
    dataset_id = str(meta["dataset_id"])
    role = str(meta["role"])
    protocol_version = str(meta.get("protocol_version", "5.0.0"))

    families: list[RobustnessFamily] = []
    seen_variant_ids: set[str] = set()

    for family in dataset.families:
        variants: list[RobustnessVariant] = []
        family_source = _variant_source_from_provenance(family.source_provenance)

        for variant in family.variants:
            if variant.variant_id in seen_variant_ids:
                raise RobustnessLoaderError(
                    f"Duplicate variant ID {variant.variant_id!r} in dataset {dataset_id}"
                )
            seen_variant_ids.add(variant.variant_id)

            perturbation_class = normalize_perturbation_class(variant.variant_class)
            equiv_val = variant.equivalence_status
            try:
                equiv_status = EquivalenceStatus(equiv_val)
            except (TypeError, ValueError):
                equiv_status = EquivalenceStatus.PENDING_REVIEW

            review_status_val = family.label_review.get("status", "pending")
            if equiv_status == EquivalenceStatus.PENDING_REVIEW:
                review_status = HumanReviewStatus.PENDING
            elif review_status_val == "approved":
                review_status = HumanReviewStatus.APPROVED
            else:
                review_status = HumanReviewStatus.PENDING

            variant_meta = VariantMetadata(
                variant_type=perturbation_class,
                language=variant.language,
                source=family_source,
                human_review_status=review_status,
                equivalence_status=equiv_status,
                expected_semantic_differences=None,
                notes=tuple(family.label_review.get("notes", ())),
            )

            dataset_size = family.gold_structured_intent.get("dataset_size_gb")
            variants.append(
                RobustnessVariant(
                    variant_id=variant.variant_id,
                    family_id=family.family_id,
                    intent=variant.intent,
                    code_context=variant.code_context,
                    metadata=variant_meta,
                    dataset_size_gb=dataset_size,
                )
            )

        families.append(
            RobustnessFamily(
                family_id=family.family_id,
                title=family.title,
                workload_stratum=family.workload_stratum,
                difficulty=family.difficulty,
                executable_workload_id=family.executable_workload_id,
                gold_structured_intent=dict(family.gold_structured_intent),
                candidate_gold=dict(family.candidate_gold),
                profile_gold=dict(family.profile_gold),
                image_gold=dict(family.image_gold),
                policy_gold=dict(family.policy_gold),
                variants=tuple(variants),
                label_review=dict(family.label_review),
                source_provenance=family.source_provenance,
            )
        )

    return RobustnessDataset(
        dataset_id=dataset_id,
        families=tuple(families),
        protocol_version=protocol_version,
        role=role,
        metadata=dict(meta),
    )


def load_robustness_families_from_split(
    split_data: SplitBundle | LoadedSplit | Mapping[str, Any] | Path,
    *,
    workload_manifests: Sequence[Path] = (),
) -> RobustnessDataset:
    """Load robustness families from a compiled Protocol-v5 split bundle (v1 or v2)."""
    if isinstance(split_data, (str, Path)):
        path = Path(split_data)
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        bundle = validate_split_bundle(
            document, workload_manifests=workload_manifests
        )
    elif isinstance(split_data, LoadedSplit):
        bundle = split_data.bundle
    elif isinstance(split_data, SplitBundle):
        bundle = split_data
    elif isinstance(split_data, Mapping):
        bundle = validate_split_bundle(
            split_data, workload_manifests=workload_manifests
        )
    else:
        raise RobustnessLoaderError(f"Unsupported split bundle type: {type(split_data)}")

    manifest = bundle.split_manifest
    dataset_id = manifest.dataset_id
    role = manifest.role.value
    schema_version = bundle.schema_version

    grouped_cases: dict[str, list[Any]] = defaultdict(list)
    for case in bundle.cases:
        grouped_cases[case.family_id].append(case)

    families: list[RobustnessFamily] = []
    seen_variant_ids: set[str] = set()

    for family_id in manifest.family_ids:
        cases = grouped_cases.get(family_id, [])
        if not cases:
            continue

        first_case = cases[0]
        variants: list[RobustnessVariant] = []

        # Extract family-level gold
        if schema_version == SPLIT_BUNDLE_SCHEMA_VERSION_V2:
            family_meta = first_case.family_metadata or {}
            title = str(family_meta.get("title", family_id))
            stratum = str(family_meta.get("workload_stratum", "general"))
            difficulty = str(family_meta.get("difficulty", "medium"))
            executable = family_meta.get("executable_workload_id")
            gold = first_case.gold
            gold_structured = dict(gold.get("gold_structured_intent", {}))
            candidate_gold = dict(gold.get("candidate_gold", {}))
            profile_gold = dict(gold.get("profile_gold", {}))
            image_gold = dict(gold.get("image_gold", {}))
            policy_gold = dict(gold.get("policy_gold", {}))
            source_prov = first_case.source_provenance.get(
                "original_source_provenance"
            )
            label_review = first_case.source_provenance.get(
                "label_review", {"status": "approved"}
            )
        else:
            # v1 legacy split bundle projection
            title = family_id.replace("-", " ").title()
            stratum = "general"
            difficulty = "medium"
            executable = None
            gold = first_case.gold
            pref_cand = gold.get("preferred_candidate_id")
            acc_cands = gold.get("acceptable_candidate_ids", [])
            allowed_profiles = gold.get("allowed_profiles", [])
            req_caps = gold.get("required_image_capabilities", [])
            gpu_allowed = gold.get("gpu_allowed", True)
            feasible = gold.get("request_feasible", True)

            # Reconstruct profile/image/candidate gold
            preferred_profiles = [pref_cand.split("-")[0]] if pref_cand else []
            acceptable_profiles = sorted(
                {c.split("-")[0] for c in acc_cands if "-" in c}
            )
            preferred_images = (
                ["-".join(pref_cand.split("-")[1:])] if pref_cand else []
            )
            acceptable_images = sorted(
                {"-".join(c.split("-")[1:]) for c in acc_cands if "-" in c}
            )

            candidate_gold = {
                "preferred_candidate_ids": [pref_cand] if pref_cand else [],
                "acceptable_candidate_ids": list(acc_cands),
            }
            profile_gold = {
                "preferred_profile_ids": preferred_profiles,
                "acceptable_profile_ids": acceptable_profiles or allowed_profiles,
            }
            image_gold = {
                "preferred_image_ids": preferred_images,
                "acceptable_image_ids": acceptable_images,
                "required_capabilities": req_caps,
            }
            policy_gold = {
                "required_constraints": [
                    f"allowed_profiles={','.join(allowed_profiles)}",
                    f"gpu_allowed={str(gpu_allowed).lower()}",
                ],
                "explicitly_unsupported_requirements": [] if feasible else ["Infeasible"],
                "expected_feasibility": "feasible" if feasible else "infeasible",
            }
            gold_structured = {
                "task_types": [],
                "required_features": req_caps,
                "preferred_features": [],
                "forbidden_features": [],
                "required_frameworks": [],
                "preferred_frameworks": [],
                "gpu_semantics": "unspecified",
                "minimum_cpu_cores": None,
                "minimum_memory_gb": None,
                "dataset_size_gb": first_case.inputs.get("dataset_size_gb"),
                "ambiguities": [],
            }
            source_prov = first_case.source_provenance
            label_review = {"status": "approved"}

        family_source = _variant_source_from_provenance(source_prov)

        for case in cases:
            unique_variant_id = case.case_id or case.variant_id
            if unique_variant_id in seen_variant_ids:
                raise RobustnessLoaderError(
                    f"Duplicate variant ID {unique_variant_id!r} in split {dataset_id}"
                )
            seen_variant_ids.add(unique_variant_id)

            if schema_version == SPLIT_BUNDLE_SCHEMA_VERSION_V2 and case.variant_metadata:
                variant_class_str = case.variant_metadata.get(
                    "variant_class", "canonical_en"
                )
                equiv_status_str = case.variant_metadata.get(
                    "equivalence_status", "reviewed_equivalent"
                )
            else:
                # Infer from case_id or variant_id naming
                vid = f"{case.case_id} {case.variant_id}".lower()
                if "canonical" in vid:
                    variant_class_str = "canonical_en"
                    equiv_status_str = "canonical_reference"
                elif "paraphrase" in vid:
                    variant_class_str = "paraphrase_en"
                    equiv_status_str = "reviewed_equivalent"
                elif "vietnamese" in vid or "-vi" in vid or case.language == "vi":
                    variant_class_str = "vietnamese"
                    equiv_status_str = "reviewed_equivalent"
                elif "noisy" in vid or "noise" in vid or "informal" in vid:
                    variant_class_str = "informal_or_noisy"
                    equiv_status_str = "reviewed_equivalent"
                elif "code" in vid:
                    variant_class_str = "optional_code_context"
                    equiv_status_str = "reviewed_equivalent"
                elif "ambig" in vid:
                    variant_class_str = "optional_ambiguity_variant"
                    equiv_status_str = "controlled_ambiguity"
                else:
                    variant_class_str = "canonical_en"
                    equiv_status_str = "canonical_reference"

            perturbation_class = normalize_perturbation_class(variant_class_str)
            try:
                equiv_status = EquivalenceStatus(equiv_status_str)
            except (TypeError, ValueError):
                equiv_status = EquivalenceStatus.REVIEWED_EQUIVALENT

            variant_meta = VariantMetadata(
                variant_type=perturbation_class,
                language=case.language,
                source=family_source,
                human_review_status=HumanReviewStatus.APPROVED,
                equivalence_status=equiv_status,
                expected_semantic_differences=None,
                notes=(),
            )

            code_hints = case.inputs.get("code_context_hints", [])
            variants.append(
                RobustnessVariant(
                    variant_id=unique_variant_id,
                    family_id=case.family_id,
                    intent=case.prompt,
                    code_context=tuple(code_hints),
                    metadata=variant_meta,
                    dataset_size_gb=case.inputs.get("dataset_size_gb"),
                )
            )

        families.append(
            RobustnessFamily(
                family_id=family_id,
                title=title,
                workload_stratum=stratum,
                difficulty=difficulty,
                executable_workload_id=executable,
                gold_structured_intent=gold_structured,
                candidate_gold=candidate_gold,
                profile_gold=profile_gold,
                image_gold=image_gold,
                policy_gold=policy_gold,
                variants=tuple(variants),
                label_review=label_review,
                source_provenance=source_prov,
            )
        )

    return RobustnessDataset(
        dataset_id=dataset_id,
        families=tuple(families),
        protocol_version="5.0.0",
        role=role,
        metadata={"split_id": manifest.split_id},
    )


def load_robustness_dataset(
    source: GoldDataset | LoadedGoldDataset | SplitBundle | LoadedSplit | Mapping[str, Any] | Path | str | None = None,
    *,
    workload_manifests: Sequence[Path] = (),
) -> RobustnessDataset:
    """Polymorphic loader for robustness datasets from any supported source or default development bundle."""
    if source is None:
        return load_robustness_families_from_split(
            load_development_split(), workload_manifests=workload_manifests
        )

    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"Robustness source file not found: {path}")
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RobustnessLoaderError(f"Failed to parse source file {path}: {exc}") from exc
        if not isinstance(document, Mapping):
            raise RobustnessLoaderError(f"File {path} must contain a mapping")
        schema = document.get("schema_version")
        if schema == GOLD_DATASET_SCHEMA_VERSION:
            return load_robustness_families_from_gold(
                path, workload_manifests=workload_manifests
            )
        if schema in {SPLIT_BUNDLE_SCHEMA_VERSION, SPLIT_BUNDLE_SCHEMA_VERSION_V2}:
            return load_robustness_families_from_split(
                path, workload_manifests=workload_manifests
            )
        raise RobustnessLoaderError(f"Unrecognized schema_version {schema!r} in {path}")

    if isinstance(source, (GoldDataset, LoadedGoldDataset)):
        return load_robustness_families_from_gold(
            source, workload_manifests=workload_manifests
        )

    if isinstance(source, (SplitBundle, LoadedSplit)):
        return load_robustness_families_from_split(
            source, workload_manifests=workload_manifests
        )

    if isinstance(source, Mapping):
        schema = source.get("schema_version")
        if schema == GOLD_DATASET_SCHEMA_VERSION:
            return load_robustness_families_from_gold(
                source, workload_manifests=workload_manifests
            )
        if schema in {SPLIT_BUNDLE_SCHEMA_VERSION, SPLIT_BUNDLE_SCHEMA_VERSION_V2}:
            return load_robustness_families_from_split(
                source, workload_manifests=workload_manifests
            )
        # Check if direct RobustnessDataset dict
        if "families" in source:
            return RobustnessDataset.from_dict(source)
        raise RobustnessLoaderError(f"Unrecognized mapping schema_version {schema!r}")

    raise RobustnessLoaderError(f"Unsupported robustness source: {type(source)}")


__all__ = [
    "RobustnessLoaderError",
    "load_robustness_dataset",
    "load_robustness_families_from_gold",
    "load_robustness_families_from_split",
]
