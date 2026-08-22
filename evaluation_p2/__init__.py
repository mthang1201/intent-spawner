"""Versioned offline P1-versus-P2 recommendation evaluation."""

from .dataset import (
    EVALUATION_DATASET_SCHEMA_VERSION,
    SUPPLEMENT_SCHEMA_VERSION,
    load_evaluation_dataset,
)
from .metrics import aggregate_metrics, categorize_p2_errors, p3_decision_report

__all__ = [
    "EVALUATION_DATASET_SCHEMA_VERSION",
    "SUPPLEMENT_SCHEMA_VERSION",
    "aggregate_metrics",
    "categorize_p2_errors",
    "load_evaluation_dataset",
    "p3_decision_report",
]
