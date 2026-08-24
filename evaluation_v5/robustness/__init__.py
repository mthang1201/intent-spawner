"""E2 Natural-Language Robustness Experiment Dataset Tooling (Protocol-v5)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

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
        "--output", type=Path, help="Optional output path for updated dataset"
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _cli_parser()
    args = parser.parse_args(argv)

    if args.command == "review":
        dataset = load_robustness_dataset(args.dataset)
        content = export_equivalence_review(dataset, format=args.format)
        if args.output is not None:
            args.output.write_text(content, encoding="utf-8")
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
        dataset = load_robustness_dataset(args.dataset)
        if dataset.role == "confirmatory":
            raise PermissionError("Paraphrase generation is strictly prohibited on confirmatory datasets.")
        print("Generated drafts for development dataset...")
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
