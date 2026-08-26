"""Convenience alias for evaluation_v5.analysis.reporting."""

from __future__ import annotations

import sys

from evaluation_v5.analysis.reporting import (
    REPORTING_SCHEMA_VERSION,
    ReportingError,
    compute_confidence_intervals_data,
    compute_error_taxonomy_data,
    compute_limitations_block,
    compute_p3_development_decision,
    compute_paired_family_outcomes_data,
    compute_recommendation_quality_data,
    compute_retrieval_ablation_data,
    compute_robustness_table_data,
    format_limitations_md,
    format_p3_decision_md,
    format_recommendation_quality_md,
    format_robustness_md,
    generate_offline_report,
    generate_synthesis_report,
    main,
    render_confidence_intervals_svg,
    render_error_taxonomy_svg,
    render_paired_family_outcomes_svg,
    render_retrieval_recall_svg,
    write_not_executed_report,
)

if __name__ == "__main__":
    sys.exit(main())

__all__ = [
    "REPORTING_SCHEMA_VERSION",
    "ReportingError",
    "compute_confidence_intervals_data",
    "compute_error_taxonomy_data",
    "compute_limitations_block",
    "compute_p3_development_decision",
    "compute_paired_family_outcomes_data",
    "compute_recommendation_quality_data",
    "compute_retrieval_ablation_data",
    "compute_robustness_table_data",
    "format_limitations_md",
    "format_p3_decision_md",
    "format_recommendation_quality_md",
    "format_robustness_md",
    "generate_offline_report",
    "generate_synthesis_report",
    "main",
    "render_confidence_intervals_svg",
    "render_error_taxonomy_svg",
    "render_paired_family_outcomes_svg",
    "render_retrieval_recall_svg",
    "write_not_executed_report",
]
