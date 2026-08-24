"""Human review export and approval tooling for variant semantic equivalence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import csv
from dataclasses import dataclass
import io
import json
import re
from typing import Any

from .models import RobustnessDataset, RobustnessFamily, RobustnessVariant
from .taxonomy import (
    EquivalenceStatus,
    HumanReviewStatus,
    PerturbationClass,
    VariantMetadata,
    VariantSource,
)


_UTC_TIMESTAMP_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class StaleReviewError(ValueError):
    """Raised when a review decision is applied to modified or stale variant text."""

    pass


class InvalidReviewDecisionError(ValueError):
    """Raised when a review decision is malformed or violates trust boundaries."""

    pass


def _csv_sanitize(value: str) -> str:
    """Neutralize potential CSV formula injection while preserving text for viewer applications."""
    if value and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


@dataclass(frozen=True, slots=True)
class EquivalenceReviewRow:
    """One human-review record for verifying variant semantic equivalence."""

    family_id: str
    family_title: str
    canonical_meaning: str
    variant_id: str
    variant_type: str
    language: str
    variant_text: str
    expected_semantic_differences: str
    proposed_equivalence_decision: str
    human_review_status: str
    source: str
    notes: str
    variant_text_sha256: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "family_id": self.family_id,
            "family_title": self.family_title,
            "canonical_meaning": self.canonical_meaning,
            "variant_id": self.variant_id,
            "variant_type": self.variant_type,
            "language": self.language,
            "variant_text": self.variant_text,
            "expected_semantic_differences": self.expected_semantic_differences,
            "proposed_equivalence_decision": self.proposed_equivalence_decision,
            "human_review_status": self.human_review_status,
            "source": self.source,
            "notes": self.notes,
            "variant_text_sha256": self.variant_text_sha256,
        }

    def to_csv_dict(self) -> dict[str, str]:
        """Return dict with formula prefixes sanitized for safe CSV output."""
        return {
            "family_id": self.family_id,
            "family_title": _csv_sanitize(self.family_title),
            "canonical_meaning": _csv_sanitize(self.canonical_meaning),
            "variant_id": self.variant_id,
            "variant_type": self.variant_type,
            "language": self.language,
            "variant_text": _csv_sanitize(self.variant_text),
            "expected_semantic_differences": _csv_sanitize(
                self.expected_semantic_differences
            ),
            "proposed_equivalence_decision": self.proposed_equivalence_decision,
            "human_review_status": self.human_review_status,
            "source": self.source,
            "notes": _csv_sanitize(self.notes),
            "variant_text_sha256": self.variant_text_sha256,
        }


def _build_review_row(
    family: RobustnessFamily,
    variant: RobustnessVariant,
) -> EquivalenceReviewRow:
    canonical = family.canonical_variant
    canonical_meaning = canonical.intent
    if canonical.code_context:
        canonical_meaning += " [Code hints: " + "; ".join(canonical.code_context) + "]"

    variant_text = variant.intent
    if variant.code_context:
        variant_text += " [Code hints: " + "; ".join(variant.code_context) + "]"

    meta = variant.metadata
    differences = meta.expected_semantic_differences or (
        "None (Canonical reference)"
        if variant.is_canonical
        else "None (Semantically equivalent)"
    )

    return EquivalenceReviewRow(
        family_id=family.family_id,
        family_title=family.title,
        canonical_meaning=canonical_meaning,
        variant_id=variant.variant_id,
        variant_type=(
            meta.variant_type.value
            if isinstance(meta.variant_type, PerturbationClass)
            else str(meta.variant_type)
        ),
        language=meta.language,
        variant_text=variant_text,
        expected_semantic_differences=differences,
        proposed_equivalence_decision=(
            meta.equivalence_status.value
            if isinstance(meta.equivalence_status, EquivalenceStatus)
            else str(meta.equivalence_status)
        ),
        human_review_status=(
            meta.human_review_status.value
            if isinstance(meta.human_review_status, HumanReviewStatus)
            else str(meta.human_review_status)
        ),
        source=(
            meta.source.value
            if isinstance(meta.source, VariantSource)
            else str(meta.source)
        ),
        notes="; ".join(meta.notes),
        variant_text_sha256=variant.text_sha256,
    )


def extract_review_rows(
    source: RobustnessDataset | Sequence[RobustnessFamily],
) -> list[EquivalenceReviewRow]:
    """Extract ordered review rows for all variants in the dataset or families."""
    families = source.families if isinstance(source, RobustnessDataset) else source
    rows: list[EquivalenceReviewRow] = []
    for family in families:
        for variant in family.variants:
            rows.append(_build_review_row(family, variant))
    return rows


def export_equivalence_review_csv(
    rows: Sequence[EquivalenceReviewRow],
) -> str:
    """Format equivalence review records as RFC-4180 CSV with formula-injection defense."""
    output = io.StringIO()
    fieldnames = [
        "family_id",
        "family_title",
        "canonical_meaning",
        "variant_id",
        "variant_type",
        "language",
        "variant_text",
        "expected_semantic_differences",
        "proposed_equivalence_decision",
        "human_review_status",
        "source",
        "notes",
        "variant_text_sha256",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row.to_csv_dict())
    return output.getvalue()


def export_equivalence_review_json(
    rows: Sequence[EquivalenceReviewRow],
) -> str:
    """Format equivalence review records as pretty-printed JSON."""
    return (
        json.dumps(
            [row.to_dict() for row in rows],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def export_equivalence_review_markdown(
    rows: Sequence[EquivalenceReviewRow],
) -> str:
    """Format equivalence review records as human-readable Markdown tables."""
    lines = [
        "# Workload Variant Semantic Equivalence Review",
        "",
        "This artifact records human and candidate equivalence decisions for "
        "natural-language robustness variants.",
        "",
        f"**Total variants reviewed**: {len(rows)}",
        "",
    ]

    # Group by family
    grouped: dict[str, list[EquivalenceReviewRow]] = {}
    for row in rows:
        grouped.setdefault(row.family_id, []).append(row)

    for family_id, family_rows in grouped.items():
        title = family_rows[0].family_title
        lines.append(f"## Family: {title} (`{family_id}`)")
        lines.append("")
        lines.append(f"- **Canonical meaning**: {family_rows[0].canonical_meaning}")
        lines.append("")
        lines.append(
            "| Variant ID | Type | Lang | Source | Equivalence Decision | Review Status | Expected Differences |"
        )
        lines.append(
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
        )
        for row in family_rows:
            # Escape pipe characters for markdown table safety
            v_id = row.variant_id
            v_type = row.variant_type
            lang = row.language
            src = row.source
            decision = row.proposed_equivalence_decision
            status = row.human_review_status
            diffs = row.expected_semantic_differences.replace("|", "\\|")
            lines.append(
                f"| `{v_id}` | {v_type} | {lang} | `{src}` | **{decision}** | {status} | {diffs} |"
            )
        lines.append("")
        lines.append("### Variant Texts")
        lines.append("")
        for row in family_rows:
            lines.append(f"- **`{row.variant_id}`** ({row.variant_type}):")
            lines.append(f"  > {row.variant_text}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_equivalence_review(
    source: RobustnessDataset | Sequence[RobustnessFamily],
    *,
    format: str = "markdown",
) -> str:
    """Export equivalence review artifact in csv, json, or markdown format."""
    rows = extract_review_rows(source)
    fmt = format.lower().strip()
    if fmt == "csv":
        return export_equivalence_review_csv(rows)
    if fmt == "json":
        return export_equivalence_review_json(rows)
    if fmt in {"markdown", "md"}:
        return export_equivalence_review_markdown(rows)
    raise ValueError(
        f"Unsupported review export format: {format!r}. Use csv, json, or markdown."
    )


def apply_review_decisions(
    dataset: RobustnessDataset,
    decisions: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
) -> RobustnessDataset:
    """Apply approved reviewer decisions to variants in a RobustnessDataset with trust boundaries."""
    if isinstance(decisions, Sequence):
        decision_list = [dict(item) for item in decisions]
    else:
        decision_list = [{"variant_id": k, **dict(v)} for k, v in decisions.items()]

    # Validate decision batch for duplicates
    seen_decision_ids: set[str] = set()
    for item in decision_list:
        v_id = str(item.get("variant_id", ""))
        if not v_id:
            raise InvalidReviewDecisionError("Review decision missing variant_id")
        if v_id in seen_decision_ids:
            raise InvalidReviewDecisionError(
                f"Duplicate review decision for variant {v_id!r}"
            )
        seen_decision_ids.add(v_id)

    # Index existing variants across the dataset
    variant_lookup: dict[str, tuple[RobustnessFamily, RobustnessVariant]] = {}
    for family in dataset.families:
        for variant in family.variants:
            variant_lookup[variant.variant_id] = (family, variant)

    # Validate each decision against its target variant
    decision_map: dict[str, dict[str, Any]] = {}
    for item in decision_list:
        v_id = str(item["variant_id"])
        target = variant_lookup.get(v_id)
        if target is None:
            raise InvalidReviewDecisionError(
                f"Review decision references unknown variant {v_id!r}"
            )

        family, variant = target

        # Check family binding
        if "family_id" in item and item["family_id"] != family.family_id:
            raise InvalidReviewDecisionError(
                f"Review decision family_id {item['family_id']!r} does not match variant family {family.family_id!r}"
            )

        # Check stale review hash binding
        if "variant_text_sha256" in item:
            expected_hash = str(item["variant_text_sha256"]).strip()
            if expected_hash and expected_hash != variant.text_sha256:
                raise StaleReviewError(
                    f"Stale review decision for variant {v_id!r}: text hash mismatch (expected {expected_hash!r}, got {variant.text_sha256!r})"
                )

        # Check reviewer identity and notes requirements for approved decisions
        status_val = str(item.get("human_review_status", "")).lower().strip()
        if status_val == HumanReviewStatus.APPROVED.value:
            reviewer = (
                item.get("reviewed_by")
                or item.get("reviewer_id")
                or item.get("reviewer")
            )
            if not reviewer or not str(reviewer).strip():
                raise InvalidReviewDecisionError(
                    f"Approved review decision for variant {v_id!r} requires non-empty reviewer identity (reviewed_by)"
                )

            notes = item.get("notes")
            if notes is None or (isinstance(notes, str) and not notes.strip()) or (isinstance(notes, Sequence) and not notes):
                raise InvalidReviewDecisionError(
                    f"Approved review decision for variant {v_id!r} requires non-empty review notes"
                )

        # Validate timestamp format if provided
        timestamp = item.get("reviewed_at_utc")
        if timestamp is not None and not _UTC_TIMESTAMP_REGEX.match(str(timestamp)):
            raise InvalidReviewDecisionError(
                f"Invalid UTC timestamp format in review decision for {v_id!r}: {timestamp!r}"
            )

        decision_map[v_id] = item

    # Apply decisions to produce updated dataset
    updated_families: list[RobustnessFamily] = []
    for family in dataset.families:
        updated_variants: list[RobustnessVariant] = []
        for variant in family.variants:
            decision = decision_map.get(variant.variant_id)
            if decision is None:
                updated_variants.append(variant)
                continue

            current_meta = variant.metadata
            new_review_status = decision.get(
                "human_review_status", HumanReviewStatus.APPROVED
            )
            new_equiv_status = decision.get(
                "equivalence_status",
                decision.get(
                    "proposed_equivalence_decision", current_meta.equivalence_status
                ),
            )
            new_diffs = decision.get(
                "expected_semantic_differences",
                current_meta.expected_semantic_differences,
            )
            new_notes = decision.get("notes")
            if new_notes is not None:
                if isinstance(new_notes, str):
                    notes_tuple = tuple(new_notes.split("; "))
                else:
                    notes_tuple = tuple(str(x) for x in new_notes)
            else:
                notes_tuple = current_meta.notes

            # If reviewer identity was provided, append to notes
            reviewer = (
                decision.get("reviewed_by")
                or decision.get("reviewer_id")
                or decision.get("reviewer")
            )
            if reviewer and not any(f"reviewed_by: {reviewer}" in n for n in notes_tuple):
                notes_tuple = notes_tuple + (f"reviewed_by: {reviewer}",)

            meta = VariantMetadata(
                variant_type=current_meta.variant_type,
                language=current_meta.language,
                source=current_meta.source,
                human_review_status=new_review_status,
                equivalence_status=new_equiv_status,
                expected_semantic_differences=new_diffs,
                notes=notes_tuple,
            )
            updated_variants.append(
                RobustnessVariant(
                    variant_id=variant.variant_id,
                    family_id=variant.family_id,
                    intent=variant.intent,
                    code_context=variant.code_context,
                    metadata=meta,
                    dataset_size_gb=variant.dataset_size_gb,
                )
            )

        updated_families.append(
            RobustnessFamily(
                family_id=family.family_id,
                title=family.title,
                workload_stratum=family.workload_stratum,
                difficulty=family.difficulty,
                executable_workload_id=family.executable_workload_id,
                gold_structured_intent=family.gold_structured_intent,
                candidate_gold=family.candidate_gold,
                profile_gold=family.profile_gold,
                image_gold=family.image_gold,
                policy_gold=family.policy_gold,
                variants=tuple(updated_variants),
                label_review=family.label_review,
                source_provenance=family.source_provenance,
            )
        )

    return RobustnessDataset(
        dataset_id=dataset.dataset_id,
        families=tuple(updated_families),
        protocol_version=dataset.protocol_version,
        role=dataset.role,
        metadata=dataset.metadata,
    )


__all__ = [
    "EquivalenceReviewRow",
    "InvalidReviewDecisionError",
    "StaleReviewError",
    "apply_review_decisions",
    "export_equivalence_review",
    "export_equivalence_review_csv",
    "export_equivalence_review_json",
    "export_equivalence_review_markdown",
    "extract_review_rows",
]
