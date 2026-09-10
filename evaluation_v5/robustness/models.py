"""Domain models for natural-language robustness workload families and variants."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from evaluation_v5.gold_dataset import CONFIRMATORY_ELIGIBLE_CLASSIFICATIONS
from .taxonomy import (
    EquivalenceStatus,
    HumanReviewStatus,
    PerturbationClass,
    VariantMetadata,
    VariantSource,
    compute_text_sha256,
    to_gold_variant_class,
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
    role: str = "development"
    evidence_classification: str = "development_only"

    @property
    def is_confirmatory(self) -> bool:
        """Return True if this family belongs to the sealed confirmatory split."""
        if str(self.role).strip().lower() == "confirmatory":
            return True
        if self.evidence_classification in CONFIRMATORY_ELIGIBLE_CLASSIFICATIONS:
            return True
        if self.source_provenance is not None:
            source_split = self.source_provenance.get("source_split") or self.source_provenance.get("role")
            if source_split is not None and str(source_split).strip().lower() == "confirmatory":
                return True
            src_class = self.source_provenance.get("evidence_classification")
            if src_class is not None and src_class in CONFIRMATORY_ELIGIBLE_CLASSIFICATIONS:
                return True
        return False

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
            "role": self.role,
            "evidence_classification": self.evidence_classification,
        }

    def to_gold_authoring_dict(
        self,
        *,
        include_source_provenance: bool = True,
    ) -> dict[str, Any]:
        """Convert robustness family into a compliant Protocol-v5 gold authoring family dict."""
        lr = dict(self.label_review) if self.label_review else {"status": "approved"}
        status = lr.get("status", "approved")
        if status == "approved":
            lr.setdefault("status", "approved")
            lr.setdefault("reviewed_by", "protocol-v5-robustness-reviewer")
            lr.setdefault("reviewed_at_utc", "2026-08-22T00:00:00Z")
            if not lr.get("notes"):
                lr["notes"] = ["Approved robustness family label review"]
        else:
            lr.setdefault("status", "pending")
            lr.setdefault("reviewed_by", None)
            lr.setdefault("reviewed_at_utc", None)
            lr.setdefault("notes", [])

        source_prov = (
            dict(self.source_provenance)
            if include_source_provenance and self.source_provenance is not None
            else None
        )

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
            "variants": [
                {
                    "variant_id": v.variant_id,
                    "variant_class": (
                        to_gold_variant_class(v.metadata.variant_type)
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
                }
                for v in self.variants
            ],
            "label_review": lr,
            "source_provenance": source_prov,
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        dataset_role: str | None = None,
    ) -> "RobustnessFamily":
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

        prov = payload.get("source_provenance")
        prov_split = (
            prov.get("source_split") or prov.get("role")
            if isinstance(prov, Mapping)
            else None
        )
        prov_class = (
            prov.get("evidence_classification")
            if isinstance(prov, Mapping)
            else None
        )
        role = str(payload.get("role") or prov_split or dataset_role or "development")
        classification = str(
            payload.get("evidence_classification")
            or prov_class
            or ("human_reviewed_confirmatory" if role == "confirmatory" else "development_only")
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
            role=role,
            evidence_classification=classification,
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
        role = str(payload.get("role", "development"))
        raw_families = payload.get("families", [])
        if not isinstance(raw_families, list):
            raise ValueError("RobustnessDataset families must be a list")
        families = tuple(
            RobustnessFamily.from_dict(item, dataset_role=role)
            for item in raw_families
        )
        return cls(
            dataset_id=str(payload.get("dataset_id", "robustness-dataset")),
            families=families,
            protocol_version=str(payload.get("protocol_version", "5.0.0")),
            role=role,
            metadata=payload.get("metadata"),
        )

    def to_gold_authoring_dict(
        self,
        *,
        catalog_identity: Mapping[str, Any],
        review_policy: Mapping[str, Any],
        lifecycle: str = "draft",
        created_at_utc: str | None = None,
        created_by: str = "robustness-author",
        git_revision: str = "0" * 40,
        evidence_classification: str | None = None,
        freeze_metadata: Mapping[str, Any] | None = None,
        source_datasets: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Convert robustness dataset into a compliant Protocol-v5 gold authoring document."""
        classification = evidence_classification
        if classification is None:
            if self.metadata and "evidence_classification" in self.metadata:
                classification = str(self.metadata["evidence_classification"])
            elif self.role == "confirmatory":
                classification = "human_reviewed_confirmatory"
            else:
                classification = "development_only"

        timestamp = created_at_utc or "2026-08-25T00:00:00Z"
        sources = [dict(s) for s in source_datasets] if source_datasets is not None else []

        return {
            "schema_version": "protocol-v5-gold-family-v1.0.0",
            "dataset_metadata": {
                "dataset_id": self.dataset_id,
                "protocol_version": self.protocol_version,
                "role": self.role,
                "lifecycle": lifecycle,
                "created_at_utc": timestamp,
                "created_by": created_by,
                "git_revision": git_revision,
                "evidence_classification": classification,
                "freeze_metadata": dict(freeze_metadata) if freeze_metadata is not None else None,
                "source_datasets": sources,
            },
            "catalog_identity": dict(catalog_identity),
            "review_policy": dict(review_policy),
            "families": [
                f.to_gold_authoring_dict(include_source_provenance=bool(sources))
                for f in self.families
            ],
        }


def compute_dataset_canonical_sha256(dataset: RobustnessDataset) -> str:
    """Compute deterministic cryptographic SHA-256 fingerprint binding trust and provenance state."""
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
            "role": fam.role,
            "evidence_classification": fam.evidence_classification,
            "source_provenance": (
                dict(fam.source_provenance) if fam.source_provenance is not None else None
            ),
            "label_review": dict(fam.label_review) if fam.label_review is not None else {},
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
                    "human_review_status": (
                        v.metadata.human_review_status.value
                        if isinstance(v.metadata.human_review_status, HumanReviewStatus)
                        else str(v.metadata.human_review_status)
                    ),
                    "source": (
                        v.metadata.source.value
                        if isinstance(v.metadata.source, VariantSource)
                        else str(v.metadata.source)
                    ),
                    "expected_semantic_differences": v.metadata.expected_semantic_differences,
                    "dataset_size_gb": v.dataset_size_gb,
                    "notes": list(v.metadata.notes),
                }
                for v in sorted(fam.variants, key=lambda x: x.variant_id)
            ],
        }
        items.append(fam_dict)

    metadata_payload = None
    if dataset.metadata is not None:
        metadata_payload = {
            k: v for k, v in sorted(dataset.metadata.items())
            if not k.startswith("_transient")
        }

    dataset_dict = {
        "dataset_id": dataset.dataset_id,
        "protocol_version": dataset.protocol_version,
        "role": dataset.role,
        "metadata": metadata_payload,
        "families": items,
    }

    payload = json.dumps(
        dataset_dict,
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

    # Role and evidence classification integrity
    if family.is_confirmatory:
        if family.role != "confirmatory":
            raise RobustnessValidationError(
                f"Confirmatory family {family.family_id!r} has invalid role {family.role!r}"
            )
        if family.evidence_classification not in CONFIRMATORY_ELIGIBLE_CLASSIFICATIONS:
            raise RobustnessValidationError(
                f"Confirmatory family {family.family_id!r} has invalid evidence_classification {family.evidence_classification!r}"
            )
        if family.source_provenance is not None:
            source_split = family.source_provenance.get("source_split") or family.source_provenance.get("role")
            if source_split is not None and str(source_split).lower() != "confirmatory":
                raise RobustnessValidationError(
                    f"Confirmatory family {family.family_id!r} has conflicting source_split {source_split!r}"
                )
            src_class = family.source_provenance.get("evidence_classification")
            if src_class is not None and src_class not in CONFIRMATORY_ELIGIBLE_CLASSIFICATIONS:
                raise RobustnessValidationError(
                    f"Confirmatory family {family.family_id!r} has non-confirmatory source evidence_classification {src_class!r}"
                )
    else:
        if family.evidence_classification in CONFIRMATORY_ELIGIBLE_CLASSIFICATIONS:
            raise RobustnessValidationError(
                f"Development family {family.family_id!r} cannot have confirmatory evidence_classification {family.evidence_classification!r}"
            )

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

        if dataset.role == "confirmatory":
            if not family.is_confirmatory or family.role != "confirmatory":
                raise RobustnessValidationError(
                    f"Confirmatory dataset {dataset.dataset_id!r} contains non-confirmatory family {family.family_id!r}"
                )
        elif family.is_confirmatory or family.role == "confirmatory":
            raise RobustnessValidationError(
                f"Development dataset {dataset.dataset_id!r} contains confirmatory family {family.family_id!r}"
            )

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
