"""Protocol-v5 derived metrics, component analysis, family statistics, and reporting."""

from __future__ import annotations

from .component_scoring import (
    AnalysisResult as ComponentAnalysisResult,
    score_component_records,
)
from .reporting import (
    REPORTING_SCHEMA_VERSION,
    ReportingError,
    generate_offline_report,
    write_not_executed_report,
)
from .statistical_analysis import (
    StatisticalAnalysisResult,
    analyze_statistical_records,
)

__all__ = [
    "ComponentAnalysisResult",
    "REPORTING_SCHEMA_VERSION",
    "ReportingError",
    "StatisticalAnalysisResult",
    "analyze_statistical_records",
    "generate_offline_report",
    "score_component_records",
    "write_not_executed_report",
]
