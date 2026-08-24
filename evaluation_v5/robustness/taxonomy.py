"""Perturbation classes, equivalence states, and metadata contracts for E2 robustness."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import re
from typing import Any, Mapping, Sequence


_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")


def compute_text_sha256(intent: str, code_context: Sequence[str] = ()) -> str:
    """Compute deterministic SHA-256 fingerprint for variant intent and code context."""
    normalized_lines = [intent.strip()] + [line.strip() for line in code_context]
    payload = "\n".join(normalized_lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class PerturbationClass(str, Enum):
    """Supported perturbation classes for natural-language robustness."""

    CANONICAL = "canonical"
    PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS = "paraphrase_without_obvious_keywords"
    VIETNAMESE = "vietnamese"
    INFORMAL_COLLOQUIAL = "informal_colloquial"
    TYPO_NOISE = "typo_noise"
    IRRELEVANT_EXTRA_CONTEXT = "irrelevant_extra_context"
    REQUIREMENT_EXPRESSED_IN_CODE_CONTEXT = "requirement_expressed_in_code_context"
    AMBIGUOUS_OR_CONFLICTING_SIGNAL = "ambiguous_or_conflicting_signal"


class EquivalenceStatus(str, Enum):
    """Semantic equivalence state of a variant with respect to its family."""

    CANONICAL_REFERENCE = "canonical_reference"
    REVIEWED_EQUIVALENT = "reviewed_equivalent"
    PENDING_REVIEW = "pending_review"
    CONTROLLED_AMBIGUITY = "controlled_ambiguity"
    NON_EQUIVALENT = "non_equivalent"


class HumanReviewStatus(str, Enum):
    """Human review decision state for a variant."""

    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class VariantSource(str, Enum):
    """Provenance source of the variant text."""

    HUMAN_AUTHORED = "human_authored"
    IMPORTED_V4 = "imported_v4"
    SYNTHETIC_TEST = "synthetic_test"
    GENERATED_DRAFT = "GENERATED_DRAFT"


_GOLD_VARIANT_CLASS_MAPPINGS: dict[str, PerturbationClass] = {
    "canonical_en": PerturbationClass.CANONICAL,
    "canonical": PerturbationClass.CANONICAL,
    "paraphrase_en": PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS,
    "paraphrase": PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS,
    "paraphrase_without_obvious_keywords": PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS,
    "vietnamese": PerturbationClass.VIETNAMESE,
    "informal_or_noisy": PerturbationClass.INFORMAL_COLLOQUIAL,
    "informal_colloquial": PerturbationClass.INFORMAL_COLLOQUIAL,
    "typo_noise": PerturbationClass.TYPO_NOISE,
    "typo_or_noise": PerturbationClass.TYPO_NOISE,
    "irrelevant_extra_context": PerturbationClass.IRRELEVANT_EXTRA_CONTEXT,
    "optional_code_context": PerturbationClass.REQUIREMENT_EXPRESSED_IN_CODE_CONTEXT,
    "requirement_expressed_in_code_context": PerturbationClass.REQUIREMENT_EXPRESSED_IN_CODE_CONTEXT,
    "optional_ambiguity_variant": PerturbationClass.AMBIGUOUS_OR_CONFLICTING_SIGNAL,
    "ambiguous_or_conflicting_signal": PerturbationClass.AMBIGUOUS_OR_CONFLICTING_SIGNAL,
    "other": PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS,
}


def normalize_perturbation_class(value: str) -> PerturbationClass:
    """Normalize any supported gold variant_class or perturbation string into PerturbationClass."""
    cleaned = value.strip().lower()
    if cleaned in _GOLD_VARIANT_CLASS_MAPPINGS:
        return _GOLD_VARIANT_CLASS_MAPPINGS[cleaned]
    try:
        return PerturbationClass(cleaned)
    except ValueError as exc:
        supported = ", ".join(item.value for item in PerturbationClass)
        raise ValueError(
            f"Unsupported perturbation class: {value!r}. Must be one of: {supported}"
        ) from exc


def to_gold_variant_class(perturbation: PerturbationClass) -> str:
    """Map a PerturbationClass to the standard protocol-v5 gold authoring variant_class."""
    mapping: dict[PerturbationClass, str] = {
        PerturbationClass.CANONICAL: "canonical_en",
        PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS: "paraphrase_en",
        PerturbationClass.VIETNAMESE: "vietnamese",
        PerturbationClass.INFORMAL_COLLOQUIAL: "informal_or_noisy",
        PerturbationClass.TYPO_NOISE: "informal_or_noisy",
        PerturbationClass.IRRELEVANT_EXTRA_CONTEXT: "informal_or_noisy",
        PerturbationClass.REQUIREMENT_EXPRESSED_IN_CODE_CONTEXT: "optional_code_context",
        PerturbationClass.AMBIGUOUS_OR_CONFLICTING_SIGNAL: "optional_ambiguity_variant",
    }
    return mapping.get(perturbation, "other")


@dataclass(frozen=True, slots=True)
class VariantMetadata:
    """Rich metadata describing a robustness test variant."""

    variant_type: PerturbationClass
    language: str
    source: VariantSource | str
    human_review_status: HumanReviewStatus | str
    equivalence_status: EquivalenceStatus | str
    expected_semantic_differences: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_type": (
                self.variant_type.value
                if isinstance(self.variant_type, PerturbationClass)
                else str(self.variant_type)
            ),
            "language": self.language,
            "source": (
                self.source.value
                if isinstance(self.source, VariantSource)
                else str(self.source)
            ),
            "human_review_status": (
                self.human_review_status.value
                if isinstance(self.human_review_status, HumanReviewStatus)
                else str(self.human_review_status)
            ),
            "equivalence_status": (
                self.equivalence_status.value
                if isinstance(self.equivalence_status, EquivalenceStatus)
                else str(self.equivalence_status)
            ),
            "expected_semantic_differences": self.expected_semantic_differences,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, value: object) -> "VariantMetadata":
        if not isinstance(value, Mapping):
            raise ValueError("VariantMetadata must be a mapping")
        payload = dict(value)

        variant_type_val = payload.get("variant_type") or payload.get("variant_class")
        if not isinstance(variant_type_val, str) or not variant_type_val.strip():
            raise ValueError("VariantMetadata missing variant_type")
        variant_type = normalize_perturbation_class(variant_type_val)

        language = payload.get("language", "en")
        if not isinstance(language, str) or not _LANGUAGE.fullmatch(language):
            raise ValueError(f"Invalid language tag: {language!r}")

        source_val = payload.get("source", VariantSource.HUMAN_AUTHORED.value)
        try:
            source = VariantSource(source_val)
        except (TypeError, ValueError):
            source = str(source_val)

        review_val = payload.get("human_review_status", HumanReviewStatus.PENDING.value)
        try:
            review_status = HumanReviewStatus(review_val)
        except (TypeError, ValueError):
            review_status = str(review_val)

        equiv_val = payload.get("equivalence_status", EquivalenceStatus.PENDING_REVIEW.value)
        try:
            equiv_status = EquivalenceStatus(equiv_val)
        except (TypeError, ValueError):
            equiv_status = str(equiv_val)

        differences = payload.get("expected_semantic_differences")
        if differences is not None and not isinstance(differences, str):
            differences = str(differences)

        notes_raw = payload.get("notes", [])
        if isinstance(notes_raw, Sequence) and not isinstance(notes_raw, (str, bytes)):
            notes = tuple(str(item) for item in notes_raw)
        else:
            notes = ()

        return cls(
            variant_type=variant_type,
            language=language,
            source=source,
            human_review_status=review_status,
            equivalence_status=equiv_status,
            expected_semantic_differences=differences,
            notes=notes,
        )


__all__ = [
    "EquivalenceStatus",
    "HumanReviewStatus",
    "PerturbationClass",
    "VariantMetadata",
    "VariantSource",
    "compute_text_sha256",
    "normalize_perturbation_class",
    "to_gold_variant_class",
]
