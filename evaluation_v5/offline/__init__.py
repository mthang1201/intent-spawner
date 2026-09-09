"""Protocol-v5 offline evaluation helpers.

``run`` remains the data-isolation preflight module.  The recommendation
runner is intentionally a separate import so preflight never constructs or
executes a backend.
"""

from .recommenders import (
    OfflineAdapterResult,
    OfflineCaseInput,
    OfflineSystemAdapter,
    P1FrozenAdapter,
    P2FrozenAdapter,
    P3FrozenAdapter,
    default_adapters,
)

_RUNNER_EXPORTS = frozenset(
    {
        "DuplicateRecordError",
        "EvidenceRecordError",
        "OfflineRunResult",
        "OfflineRunnerError",
        "ProvenanceMismatchError",
        "build_execution_matrix",
        "run_offline_recommendations",
        "validate_raw_record",
    }
)
_VALIDATOR_EXPORTS = frozenset(
    {
        "OfflineEvidenceValidationError",
        "validate_offline_evidence",
    }
)
_SOURCE_RUN_EXPORTS = frozenset(
    {
        "SOURCE_RUN_PROVENANCE_SCHEMA_VERSION",
        "SourceRunProvenanceError",
        "VerifiedRecommendationRunProvenance",
        "load_recommendation_run_provenance",
        "reverify_recommendation_run_provenance",
        "verify_recommendation_run_provenance",
    }
)


def __getattr__(name: str):
    """Keep ``python -m evaluation_v5.offline.runner`` free of import warnings."""

    if name in _RUNNER_EXPORTS:
        from . import runner

        return getattr(runner, name)
    if name in _VALIDATOR_EXPORTS:
        from . import validate_evidence

        return getattr(validate_evidence, name)
    if name in _SOURCE_RUN_EXPORTS:
        from . import source_run

        return getattr(source_run, name)
    raise AttributeError(name)


__all__ = [
    "DuplicateRecordError",
    "EvidenceRecordError",
    "OfflineAdapterResult",
    "OfflineCaseInput",
    "OfflineEvidenceValidationError",
    "OfflineRunResult",
    "OfflineRunnerError",
    "OfflineSystemAdapter",
    "P1FrozenAdapter",
    "P2FrozenAdapter",
    "P3FrozenAdapter",
    "ProvenanceMismatchError",
    "SOURCE_RUN_PROVENANCE_SCHEMA_VERSION",
    "SourceRunProvenanceError",
    "VerifiedRecommendationRunProvenance",
    "build_execution_matrix",
    "default_adapters",
    "load_recommendation_run_provenance",
    "reverify_recommendation_run_provenance",
    "run_offline_recommendations",
    "validate_raw_record",
    "validate_offline_evidence",
    "verify_recommendation_run_provenance",
]
