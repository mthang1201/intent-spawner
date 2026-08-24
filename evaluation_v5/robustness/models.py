"""Domain models for natural-language robustness workload families and variants."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .taxonomy import (
    EquivalenceStatus,
    HumanReviewStatus,
    PerturbationClass,
    VariantMetadata,
    VariantSource,
    compute_text_sha256,
)


class RobustnessValidationError(ValueError):
    """Raised when a robustness family or dataset violates structural invariants."""

    pass


@dataclass(frozen=True, slots=True)
class RobustnessVariant:
    """One natural-language surface variant belonging to a workload family."""

    variant_id: str
    family_id: str
    intent: str
    code_context: tuple[str, ...]
    metadata: VariantMetadata
    dataset_size_gb: float | int | None = None

    @property
    def text_sha256(self) -> str:
        """Deterministic SHA-256 fingerprint of intent and code context."""
        return compute_text_sha256(self.intent, self.code_context)

    @property
    def is_canonical(self) -> bool:
        """Return True if this variant is the canonical reference for its family."""
        return (
            self.metadata.equivalence_status == EquivalenceStatus.CANONICAL_REFERENCE
            or self.metadata.variant_type == PerturbationClass.CANONICAL
        )

    @property
    def is_reviewed_equivalent(self) -> bool:
        """Return True if this variant is a human-approved equivalent perturbation (excludes canonical)."""
        return (
            self.metadata.equivalence_status == EquivalenceStatus.REVIEWED_EQUIVALENT
            and not self.is_canonical
        )

    @property
    def is_equivalent(self) -> bool:
        """Alias for is_reviewed_equivalent; strictly excludes canonical baseline."""
        return self.is_reviewed_equivalent

    @property
    def is_controlled_ambiguity(self) -> bool:
        """Return True if this variant is an intentional, documented ambiguity case."""
        return (
            self.metadata.equivalence_status == EquivalenceStatus.CONTROLLED_AMBIGUITY
            or self.metadata.variant_type
            == PerturbationClass.AMBIGUOUS_OR_CONFLICTING_SIGNAL
        )

    @property
    def is_non_equivalent(self) -> bool:
        """Return True if this variant is explicitly non-equivalent to the canonical workload."""
        return self.metadata.equivalence_status == EquivalenceStatus.NON_EQUIVALENT

    @property
    def is_pending_review(self) -> bool:
        """Return True if this variant is untrusted / awaiting human review."""
        return (
            self.metadata.equivalence_status == EquivalenceStatus.PENDING_REVIEW
            or self.metadata.human_review_status
            in {HumanReviewStatus.PENDING, HumanReviewStatus.DRAFT}
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "family_id": self.family_id,
            "intent": self.intent,
            "code_context": list(self.code_context),
            "metadata": self.metadata.to_dict(),
            "dataset_size_gb": self.dataset_size_gb,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RobustnessVariant":
        if not isinstance(value, Mapping):
            raise ValueError("RobustnessVariant must be a mapping")
        payload = dict(value)
        hints = payload.get("code_context", [])
        if isinstance(hints, Sequence) and not isinstance(hints, (str, bytes)):
            code_context = tuple(str(item) for item in hints)
        else:
            code_context = ()

        raw_meta = payload.get("metadata")
        if isinstance(raw_meta, Mapping):
            metadata = VariantMetadata.from_dict(raw_meta)
        else:
            metadata = VariantMetadata.from_dict(payload)

        return cls(
            variant_id=str(payload["variant_id"]),
            family_id=str(payload["family_id"]),
            intent=str(payload["intent"]),
            code_context=code_context,
            metadata=metadata,
            dataset_size_gb=payload.get("dataset_size_gb"),
        )


@dataclass(frozen=True, slots=True)
class RobustnessFamily:
    """One independent workload family containing all its semantic and surface variants."""

    family_id: str
    title: str
    workload_stratum: str
    difficulty: str
    executable_workload_id: str | None
    gold_structured_intent: Mapping[str, Any]
    candidate_gold: Mapping[str, Any]
    profile_gold: Mapping[str, Any]
    image_gold: Mapping[str, Any]
    policy_gold: Mapping[str, Any]
    variants: tuple[RobustnessVariant, ...]
    label_review: Mapping[str, Any]
    source_provenance: Mapping[str, Any] | None = None

    @property
    def canonical_variant(self) -> RobustnessVariant:
        """Find the canonical reference variant for this family."""
        for variant in self.variants:
            if (
                variant.metadata.equivalence_status
                == EquivalenceStatus.CANONICAL_REFERENCE
            ):
                return variant
        for variant in self.variants:
            if variant.metadata.variant_type == PerturbationClass.CANONICAL:
                return variant
        if self.variants:
            return self.variants[0]
        raise ValueError(f"Family {self.family_id!r} has no variants")

    @property
    def reviewed_equivalent_variants(self) -> tuple[RobustnessVariant, ...]:
        """All reviewed-equivalent perturbation variants (strictly excluding canonical)."""
        return tuple(
            variant for variant in self.variants if variant.is_reviewed_equivalent
        )

    @property
    def equivalent_variants(self) -> tuple[RobustnessVariant, ...]:
        """Alias for reviewed_equivalent_variants."""
        return self.reviewed_equivalent_variants

    @property
    def non_canonical_equivalent_variants(self) -> tuple[RobustnessVariant, ...]:
        """All equivalent variants excluding the canonical reference itself."""
        return self.reviewed_equivalent_variants

    @property
    def ambiguous_variants(self) -> tuple[RobustnessVariant, ...]:
        """All intentional ambiguity / conflicting variants."""
        return tuple(
            variant for variant in self.variants if variant.is_controlled_ambiguity
        )

    @property
    def non_equivalent_variants(self) -> tuple[RobustnessVariant, ...]:
        """All explicitly non-equivalent variants."""
        return tuple(
            variant for variant in self.variants if variant.is_non_equivalent
        )

    @property
    def pending_variants(self) -> tuple[RobustnessVariant, ...]:
        """All variants awaiting human review."""
        return tuple(
            variant for variant in self.variants if variant.is_pending_review
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "title": self.title,
            "workload_stratum": self.workload_stratum,
            "difficulty": self.difficulty,
            "executable_workload_id": self.executable_workload_id,
            "gold_structured_intent": dict(self.gold_structured_intent),
            "candidate_gold": dict(self.candidate_gold),
            "profile_gold": dict(self.profile_gold),
            "image_gold": dict(self.image_gold),
            "policy_gold": dict(self.policy_gold),
            "variants": [variant.to_dict() for variant in self.variants],
            "label_review": dict(self.label_review),
            "source_provenance": (
                dict(self.source_provenance)
                if self.source_provenance is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> "RobustnessFamily":
        if not isinstance(value, Mapping):
            raise ValueError("RobustnessFamily must be a mapping")
        payload = dict(value)
        raw_variants = payload.get("variants", [])
        if not isinstance(raw_variants, list) or not raw_variants:
            raise ValueError("RobustnessFamily variants must be a non-empty list")

        variants = tuple(
            RobustnessVariant.from_dict(
                {**dict(item), "family_id": payload["family_id"]}
            )
            for item in raw_variants
        )

        return cls(
            family_id=str(payload["family_id"]),
            title=str(payload.get("title", payload["family_id"])),
            workload_stratum=str(payload.get("workload_stratum", "general")),
            difficulty=str(payload.get("difficulty", "medium")),
            executable_workload_id=payload.get("executable_workload_id"),
            gold_structured_intent=dict(payload.get("gold_structured_intent", {})),
            candidate_gold=dict(payload.get("candidate_gold", {})),
            profile_gold=dict(payload.get("profile_gold", {})),
            image_gold=dict(payload.get("image_gold", {})),
            policy_gold=dict(payload.get("policy_gold", {})),
            variants=variants,
            label_review=dict(payload.get("label_review", {"status": "approved"})),
            source_provenance=payload.get("source_provenance"),
        )


@dataclass(frozen=True, slots=True)
class RobustnessDataset:
    """A collection of workload families for natural-language robustness evaluation."""

    dataset_id: str
    families: tuple[RobustnessFamily, ...]
    protocol_version: str = "5.0.0"
    role: str = "development"
    metadata: Mapping[str, Any] | None = None

    @property
    def total_variants(self) -> int:
        return sum(len(family.variants) for family in self.families)

    @property
    def total_reviewed_equivalent_variants(self) -> int:
        return sum(
            len(family.reviewed_equivalent_variants)
            for family in self.families
        )

    @property
    def total_equivalent_variants(self) -> int:
        """Alias for total_reviewed_equivalent_variants."""
        return self.total_reviewed_equivalent_variants

    @property
    def canonical_sha256(self) -> str:
        """Deterministic cryptographic SHA-256 fingerprint for complete dataset revision."""
        return compute_dataset_canonical_sha256(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "protocol_version": self.protocol_version,
            "role": self.role,
            "families": [family.to_dict() for family in self.families],
            "metadata": dict(self.metadata) if self.metadata is not None else None,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RobustnessDataset":
        if not isinstance(value, Mapping):
            raise ValueError("RobustnessDataset must be a mapping")
        payload = dict(value)
        raw_families = payload.get("families", [])
        if not isinstance(raw_families, list):
            raise ValueError("RobustnessDataset families must be a list")
        families = tuple(RobustnessFamily.from_dict(item) for item in raw_families)
        return cls(
            dataset_id=str(payload.get("dataset_id", "robustness-dataset")),
            families=families,
            protocol_version=str(payload.get("protocol_version", "5.0.0")),
            role=str(payload.get("role", "development")),
            metadata=payload.get("metadata"),
        )


def compute_dataset_canonical_sha256(dataset: RobustnessDataset) -> str:
    """Compute deterministic cryptographic SHA-256 fingerprint for complete dataset revision."""
    items = []
    for fam in sorted(dataset.families, key=lambda f: f.family_id):
        fam_dict = {
            "family_id": fam.family_id,
            "title": fam.title,
            "workload_stratum": fam.workload_stratum,
            "difficulty": fam.difficulty,
            "executable_workload_id": fam.executable_workload_id,
            "gold_structured_intent": fam.gold_structured_intent,
            "candidate_gold": fam.candidate_gold,
            "profile_gold": fam.profile_gold,
            "image_gold": fam.image_gold,
            "policy_gold": fam.policy_gold,
            "variants": [
                {
                    "variant_id": v.variant_id,
                    "variant_type": (
                        v.metadata.variant_type.value
                        if isinstance(v.metadata.variant_type, PerturbationClass)
                        else str(v.metadata.variant_type)
                    ),
                    "language": v.metadata.language,
                    "intent": v.intent,
                    "code_context": list(v.code_context),
                    "equivalence_status": (
                        v.metadata.equivalence_status.value
                        if isinstance(v.metadata.equivalence_status, EquivalenceStatus)
                        else str(v.metadata.equivalence_status)
                    ),
                    "expected_semantic_differences": v.metadata.expected_semantic_differences,
                    "dataset_size_gb": v.dataset_size_gb,
                }
                for v in sorted(fam.variants, key=lambda x: x.variant_id)
            ],
        }
        items.append(fam_dict)

    payload = json.dumps(
        {"dataset_id": dataset.dataset_id, "families": items},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_robustness_family(family: RobustnessFamily) -> None:
    """Validate family structural invariants deterministically without leaking secret prompt text."""
    if not family.variants:
        raise RobustnessValidationError(
            f"Family {family.family_id!r} has zero variants; at least 1 variant is required"
        )

    # Check for duplicate variant IDs within the family
    seen_ids: set[str] = set()
    for variant in family.variants:
        if variant.variant_id in seen_ids:
            raise RobustnessValidationError(
                f"Duplicate variant ID {variant.variant_id!r} within family {family.family_id!r}"
            )
        seen_ids.add(variant.variant_id)

    # Check canonical references
    canonical_refs = [
        v
        for v in family.variants
        if v.metadata.equivalence_status == EquivalenceStatus.CANONICAL_REFERENCE
    ]
    if len(canonical_refs) > 1:
        raise RobustnessValidationError(
            f"Family {family.family_id!r} has multiple ({len(canonical_refs)}) canonical references"
        )
    if len(canonical_refs) == 1:
        canonical = canonical_refs[0]
        if canonical.metadata.equivalence_status == EquivalenceStatus.NON_EQUIVALENT:
            raise RobustnessValidationError(
                f"Canonical variant {canonical.variant_id!r} in family {family.family_id!r} cannot be marked non_equivalent"
            )
    else:
        # Fallback check on variant_type CANONICAL
        canon_types = [
            v
            for v in family.variants
            if v.metadata.variant_type == PerturbationClass.CANONICAL
        ]
        if len(canon_types) > 1:
            raise RobustnessValidationError(
                f"Family {family.family_id!r} has multiple ({len(canon_types)}) variants with variant_type canonical"
            )


def validate_robustness_dataset(dataset: RobustnessDataset) -> None:
    """Validate global dataset structural invariants."""
    seen_families: set[str] = set()
    seen_variants: set[str] = set()

    for family in dataset.families:
        if family.family_id in seen_families:
            raise RobustnessValidationError(
                f"Duplicate family ID {family.family_id!r} in dataset {dataset.dataset_id!r}"
            )
        seen_families.add(family.family_id)

        validate_robustness_family(family)

        for variant in family.variants:
            if variant.variant_id in seen_variants:
                raise RobustnessValidationError(
                    f"Duplicate global variant ID {variant.variant_id!r} in dataset {dataset.dataset_id!r}"
                )
            seen_variants.add(variant.variant_id)


__all__ = [
    "RobustnessDataset",
    "RobustnessFamily",
    "RobustnessValidationError",
    "RobustnessVariant",
    "compute_dataset_canonical_sha256",
    "validate_robustness_dataset",
    "validate_robustness_family",
]
