"""E2 Natural-Language Robustness Experiment Dataset Tooling (Protocol-v5)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from evaluation_v5.gold_dataset import write_document_exclusive


def _write_text_exclusive(path: Path, content: str) -> Path:
    target = Path(path)
    if not target.parent.is_dir():
        raise FileNotFoundError(f"Directory not found: {target.parent}")
    with open(target, "x", encoding="utf-8") as handle:
        handle.write(content)
    return target

from .evaluator import (
    PAIR_LEVEL_SCHEMA_VERSION,
    PairComparisonRecord,
    PairLevelOutput,
    evaluate_robustness_pairs,
)
from .generator import (
    ParaphraseGeneratorError,
    generate_ambiguity_variant,
    generate_code_context_variant,
    generate_draft_variant,
    generate_family_drafts,
    generate_informal_colloquial,
    generate_irrelevant_context,
    generate_paraphrase_no_keywords,
    inject_typo_noise,
)
from .loader import (
    RobustnessLoaderError,
    load_robustness_dataset,
    load_robustness_families_from_gold,
    load_robustness_families_from_split,
)
from .metrics import (
    FamilyRobustnessSummary,
    METRICS_SCHEMA_VERSION,
    RobustnessMetricsResult,
    TransitionMatrixSummary,
    VariantEvaluationRecord,
    compute_robustness_metrics,
)
from .models import (
    RobustnessDataset,
    RobustnessFamily,
    RobustnessValidationError,
    RobustnessVariant,
    compute_dataset_canonical_sha256,
    validate_robustness_dataset,
    validate_robustness_family,
)
from .review import (
    EquivalenceReviewRow,
    InvalidReviewDecisionError,
    StaleReviewError,
    apply_review_decisions,
    export_equivalence_review,
    export_equivalence_review_csv,
    export_equivalence_review_json,
    export_equivalence_review_markdown,
    extract_review_rows,
)
from .taxonomy import (
    EquivalenceStatus,
    HumanReviewStatus,
    PerturbationClass,
    VariantMetadata,
    VariantSource,
    compute_text_sha256,
    normalize_perturbation_class,
    to_gold_variant_class,
)


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Protocol-v5 Natural-Language Robustness Dataset and Evaluation Tooling"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. Review Export
    review_parser = subparsers.add_parser(
        "review", help="Export human-review equivalence artifact (Markdown, CSV, or JSON)"
    )
    review_parser.add_argument("dataset", type=Path, help="Path to gold dataset or split bundle")
    review_parser.add_argument(
        "--format",
        choices=("markdown", "csv", "json"),
        default="markdown",
        help="Export format (default: markdown)",
    )
    review_parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write output file (defaults to stdout)",
    )

    # 2. Summary
    summary_parser = subparsers.add_parser(
        "summary", help="Print summary of robustness families and perturbation classes"
    )
    summary_parser.add_argument("dataset", type=Path, help="Path to gold dataset or split bundle")

    # 3. Draft Generation (Development Only)
    draft_parser = subparsers.add_parser(
        "draft", help="Generate draft perturbation variants for development authoring"
    )
    draft_parser.add_argument("dataset", type=Path, help="Path to development gold dataset")
    draft_parser.add_argument(
        "--seed", type=int, default=42, help="Deterministic random seed"
    )
    draft_parser.add_argument(
        "--output", type=Path, help="Optional output path for updated dataset or review artifact"
    )
    draft_parser.add_argument(
        "--format",
        choices=("yaml", "json", "markdown", "csv"),
        default=None,
        help="Export format for output file",
    )
    draft_parser.add_argument(
        "--review-output",
        type=Path,
        help="Optional path to write review artifact (markdown, csv, or json)",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _cli_parser()
    args = parser.parse_args(argv)

    if args.command == "review":
        dataset = load_robustness_dataset(args.dataset)
        fmt = args.format.lower().strip()
        if args.output is not None:
            if args.output.exists():
                raise FileExistsError(f"Output path already exists: {args.output}")
            suffix = args.output.suffix.lower()
            if fmt == "markdown" and suffix not in {".md", ".markdown"}:
                raise ValueError(
                    f"--format 'markdown' conflicts with output extension '{args.output.suffix}'"
                )
            if fmt == "csv" and suffix != ".csv":
                raise ValueError(
                    f"--format 'csv' conflicts with output extension '{args.output.suffix}'"
                )
            if fmt == "json" and suffix != ".json":
                raise ValueError(
                    f"--format 'json' conflicts with output extension '{args.output.suffix}'"
                )
            if suffix not in {".md", ".markdown", ".csv", ".json"}:
                raise ValueError(
                    f"Unsupported review output file extension: '{args.output.suffix}'. Must be .md, .markdown, .csv, or .json"
                )
        content = export_equivalence_review(dataset, format=fmt)
        if args.output is not None:
            _write_text_exclusive(args.output, content)
        else:
            print(content)
        return 0

    if args.command == "summary":
        dataset = load_robustness_dataset(args.dataset)
        print(f"Dataset ID: {dataset.dataset_id}")
        print(f"Role: {dataset.role}")
        print(f"Families: {len(dataset.families)}")
        print(f"Total variants: {dataset.total_variants}")
        print(f"Valid reviewed-equivalent variants: {dataset.total_reviewed_equivalent_variants}")
        return 0

    if args.command == "draft":
        if args.output is not None and args.output.exists():
            raise FileExistsError(f"Output path already exists: {args.output}")
        if args.review_output is not None and args.review_output.exists():
            raise FileExistsError(f"Review output path already exists: {args.review_output}")

        dataset = load_robustness_dataset(args.dataset)
        if dataset.role == "confirmatory":
            raise PermissionError("Paraphrase generation is strictly prohibited on confirmatory datasets.")
        for family in dataset.families:
            if family.is_confirmatory or family.role == "confirmatory":
                raise PermissionError(
                    f"Paraphrase generation is strictly prohibited on confirmatory family {family.family_id!r}."
                )

        updated_families: list[RobustnessFamily] = []
        total_drafts_generated = 0

        for fam_idx, family in enumerate(dataset.families):
            family_seed = args.seed + fam_idx * 100
            drafts = generate_family_drafts(family, seed=family_seed)
            total_drafts_generated += len(drafts)

            existing_ids = {v.variant_id for v in family.variants}
            combined_variants = list(family.variants)
            for d in drafts:
                if d.variant_id not in existing_ids:
                    combined_variants.append(d)
                    existing_ids.add(d.variant_id)

            updated_fam = RobustnessFamily(
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
                variants=tuple(combined_variants),
                label_review=family.label_review,
                source_provenance=family.source_provenance,
                role="development",
                evidence_classification="generated_draft",
            )
            updated_families.append(updated_fam)

        source_evidence_classification = (
            dataset.families[0].evidence_classification
            if dataset.families
            else ("human_reviewed_confirmatory" if dataset.role == "confirmatory" else "development_only")
        )
        if dataset.metadata and "evidence_classification" in dataset.metadata:
            source_evidence_classification = str(dataset.metadata["evidence_classification"])

        dataset_metadata = {
            **(dict(dataset.metadata) if dataset.metadata else {}),
            "generator_id": "protocol-v5-robustness-draft-generator-v1.0.0",
            "generator_version": "1.0.0",
            "generator_seed": args.seed,
            "source_dataset_id": dataset.dataset_id,
            "source_canonical_sha256": dataset.canonical_sha256,
            "source_role": dataset.role,
            "source_evidence_classification": source_evidence_classification,
            "output_role": "development",
            "output_evidence_classification": "generated_draft",
            "evidence_classification": "generated_draft",
            "generated_drafts_count": total_drafts_generated,
        }

        dataset_id = (
            dataset.dataset_id
            if dataset.dataset_id.endswith("-drafts")
            else f"{dataset.dataset_id}-drafts"
        )
        updated_dataset = RobustnessDataset(
            dataset_id=dataset_id,
            families=tuple(updated_families),
            protocol_version=dataset.protocol_version,
            role="development",
            metadata=dataset_metadata,
        )

        if args.output is not None:
            suffix = args.output.suffix.lower()
            if args.format is not None:
                fmt = args.format.lower().strip()
                if fmt in {"yaml", "yml"}:
                    if suffix not in {".yaml", ".yml"}:
                        raise ValueError(
                            f"--format '{args.format}' conflicts with output extension '{args.output.suffix}'"
                        )
                    fmt = "yaml"
                elif fmt == "json":
                    if suffix != ".json":
                        raise ValueError(
                            f"--format 'json' conflicts with output extension '{args.output.suffix}'"
                        )
                elif fmt in {"markdown", "md"}:
                    if suffix not in {".md", ".markdown"}:
                        raise ValueError(
                            f"--format '{args.format}' conflicts with output extension '{args.output.suffix}'"
                        )
                    fmt = "markdown"
                elif fmt == "csv":
                    if suffix != ".csv":
                        raise ValueError(
                            f"--format 'csv' conflicts with output extension '{args.output.suffix}'"
                        )
                else:
                    raise ValueError(f"Unsupported format: '{args.format}'")
            else:
                if suffix in {".yaml", ".yml"}:
                    fmt = "yaml"
                elif suffix == ".json":
                    fmt = "json"
                elif suffix in {".md", ".markdown"}:
                    fmt = "markdown"
                elif suffix == ".csv":
                    fmt = "csv"
                else:
                    raise ValueError(
                        f"Unsupported output file extension: '{args.output.suffix}'. Must be .yaml, .yml, .json, .md, or .csv"
                    )

            if fmt in {"markdown", "csv"}:
                content = export_equivalence_review(updated_dataset, format=fmt)
                _write_text_exclusive(args.output, content)
            elif fmt in {"yaml", "json"}:
                write_document_exclusive(args.output, updated_dataset.to_dict())

        if args.review_output is not None:
            rev_suffix = args.review_output.suffix.lower()
            if rev_suffix == ".csv":
                rev_fmt = "csv"
            elif rev_suffix == ".json":
                rev_fmt = "json"
            elif rev_suffix in {".md", ".markdown"}:
                rev_fmt = "markdown"
            else:
                raise ValueError(
                    f"Unsupported review output file extension: '{args.review_output.suffix}'. Must be .md, .markdown, .csv, or .json"
                )
            rev_content = export_equivalence_review(updated_dataset, format=rev_fmt)
            _write_text_exclusive(args.review_output, rev_content)

        report = {
            "status": "DRAFTS_GENERATED",
            "source_dataset_id": dataset.dataset_id,
            "updated_dataset_id": updated_dataset.dataset_id,
            "seed": args.seed,
            "family_count": len(updated_dataset.families),
            "total_variants": updated_dataset.total_variants,
            "generated_drafts_count": total_drafts_generated,
            "canonical_sha256": updated_dataset.canonical_sha256,
            "output_path": str(args.output) if args.output else None,
            "review_output_path": (
                str(args.review_output) if args.review_output else None
            ),
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "EquivalenceReviewRow",
    "EquivalenceStatus",
    "FamilyRobustnessSummary",
    "HumanReviewStatus",
    "InvalidReviewDecisionError",
    "METRICS_SCHEMA_VERSION",
    "PAIR_LEVEL_SCHEMA_VERSION",
    "PairComparisonRecord",
    "PairLevelOutput",
    "ParaphraseGeneratorError",
    "PerturbationClass",
    "RobustnessDataset",
    "RobustnessFamily",
    "RobustnessLoaderError",
    "RobustnessMetricsResult",
    "RobustnessValidationError",
    "RobustnessVariant",
    "StaleReviewError",
    "TransitionMatrixSummary",
    "VariantEvaluationRecord",
    "VariantMetadata",
    "VariantSource",
    "apply_review_decisions",
    "compute_dataset_canonical_sha256",
    "compute_robustness_metrics",
    "compute_text_sha256",
    "evaluate_robustness_pairs",
    "export_equivalence_review",
    "export_equivalence_review_csv",
    "export_equivalence_review_json",
    "export_equivalence_review_markdown",
    "extract_review_rows",
    "generate_ambiguity_variant",
    "generate_code_context_variant",
    "generate_draft_variant",
    "generate_family_drafts",
    "generate_informal_colloquial",
    "generate_irrelevant_context",
    "generate_paraphrase_no_keywords",
    "inject_typo_noise",
    "load_robustness_dataset",
    "load_robustness_families_from_gold",
    "load_robustness_families_from_split",
    "main",
    "normalize_perturbation_class",
    "to_gold_variant_class",
    "validate_robustness_dataset",
    "validate_robustness_family",
]
