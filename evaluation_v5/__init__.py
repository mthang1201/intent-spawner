"""Protocol-v5 experiment architecture; no experiment is executed on import."""

from .manifest import adapt_operational_provenance, load_manifest, write_manifest
from .paths import (
    DEFAULT_RESULTS_ROOT,
    PROTOCOL_DIRECTORY,
    ResultPaths,
    create_result_directory,
    result_paths,
)
from .isolation import (
    CONFIRMATORY_DATASET_ENV_VAR,
    FREEZE_ARTIFACT_ENV_VAR,
    ConfirmatoryLoadResult,
    ContaminationReport,
    SplitContaminationError,
    SplitIsolationError,
    VerifiedConfirmatorySplit,
    check_contamination,
    load_confirmatory_split,
    normalize_prompt,
    verify_confirmatory_split,
)
from .provenance import write_json_exclusive, write_provenance_json
from .evidence_trust import EvidenceImmutabilityError
from .schemas import (
    MANIFEST_SCHEMA_VERSION,
    PROTOCOL_VERSION,
    CandidateCatalogIdentity,
    DatasetIdentity,
    EmbeddingIndexIdentity,
    EvidenceStatus,
    ExperimentId,
    ExtractorIdentity,
    ProtocolV5Manifest,
    SplitIdentity,
    SplitStage,
)
from .validation import (
    ChecksumMismatchError,
    ManifestValidationError,
    validate_manifest,
    verify_file_checksum,
    verify_manifest_checksums,
)
from .split_dataset import (
    DEFAULT_CONFIRMATORY_SPLIT_ID,
    DEFAULT_DEVELOPMENT_DATASET,
    DEFAULT_DEVELOPMENT_SPLIT_ID,
    SPLIT_BUNDLE_SCHEMA_VERSION,
    SPLIT_BUNDLE_SCHEMA_VERSION_V2,
    SUPPORTED_SPLIT_BUNDLE_SCHEMA_VERSIONS,
    LoadedSplit,
    SplitBundle,
    SplitBundleValidationError,
    SplitCase,
    SplitManifest,
    SplitRole,
    load_development_split,
    split_bundle_checksum,
    validate_split_bundle,
)
_GOLD_EXPORTS = frozenset(
    {
        "COMPILED_SPLIT_SCHEMA_VERSION",
        "GOLD_DATASET_SCHEMA_VERSION",
        "GOLD_REVIEW_SCHEMA_VERSION",
        "GOLD_SUMMARY_SCHEMA_VERSION",
        "GoldDataset",
        "GoldDatasetReviewError",
        "GoldDatasetValidationError",
        "LoadedGoldDataset",
        "ReviewFinding",
        "ReviewReport",
        "Variant",
        "WorkloadFamily",
        "compile_gold_dataset",
        "current_catalog_identity",
        "import_v4_dataset",
        "load_gold_dataset",
        "review_gold_dataset",
        "summarize_gold_dataset",
        "validate_gold_dataset",
    }
)
_FREEZE_EXPORTS = frozenset(
    {
        "DesignSnapshot",
        "FreezeValidationError",
        "ProductionFreezeManifest",
        "VerifiedProductionFreeze",
        "load_design_snapshot",
        "parse_design_snapshot",
        "parse_production_freeze",
        "reverify_production_freeze",
        "verify_production_freeze",
    }
)


def __getattr__(name: str):
    """Lazily expose gold/freeze APIs without preloading their heavy modules."""

    if name in _GOLD_EXPORTS:
        from . import gold_dataset

        return getattr(gold_dataset, name)
    if name in _FREEZE_EXPORTS:
        from . import freeze

        return getattr(freeze, name)
    raise AttributeError(name)

__all__ = [
    "CandidateCatalogIdentity",
    "ChecksumMismatchError",
    "CONFIRMATORY_DATASET_ENV_VAR",
    "COMPILED_SPLIT_SCHEMA_VERSION",
    "ConfirmatoryLoadResult",
    "ContaminationReport",
    "DEFAULT_CONFIRMATORY_SPLIT_ID",
    "DEFAULT_DEVELOPMENT_DATASET",
    "DEFAULT_DEVELOPMENT_SPLIT_ID",
    "DEFAULT_RESULTS_ROOT",
    "DatasetIdentity",
    "DesignSnapshot",
    "EmbeddingIndexIdentity",
    "EvidenceImmutabilityError",
    "EvidenceStatus",
    "ExperimentId",
    "ExtractorIdentity",
    "FreezeValidationError",
    "FREEZE_ARTIFACT_ENV_VAR",
    "GOLD_DATASET_SCHEMA_VERSION",
    "GOLD_REVIEW_SCHEMA_VERSION",
    "GOLD_SUMMARY_SCHEMA_VERSION",
    "GoldDataset",
    "GoldDatasetReviewError",
    "GoldDatasetValidationError",
    "LoadedSplit",
    "LoadedGoldDataset",
    "MANIFEST_SCHEMA_VERSION",
    "ManifestValidationError",
    "PROTOCOL_DIRECTORY",
    "PROTOCOL_VERSION",
    "ProtocolV5Manifest",
    "ProductionFreezeManifest",
    "ResultPaths",
    "ReviewFinding",
    "ReviewReport",
    "SplitIdentity",
    "SplitBundle",
    "SplitBundleValidationError",
    "SplitCase",
    "SplitContaminationError",
    "SplitIsolationError",
    "VerifiedConfirmatorySplit",
    "SplitManifest",
    "SplitRole",
    "SplitStage",
    "SPLIT_BUNDLE_SCHEMA_VERSION",
    "SPLIT_BUNDLE_SCHEMA_VERSION_V2",
    "SUPPORTED_SPLIT_BUNDLE_SCHEMA_VERSIONS",
    "Variant",
    "VerifiedProductionFreeze",
    "WorkloadFamily",
    "adapt_operational_provenance",
    "check_contamination",
    "compile_gold_dataset",
    "create_result_directory",
    "current_catalog_identity",
    "import_v4_dataset",
    "load_gold_dataset",
    "load_design_snapshot",
    "load_manifest",
    "load_confirmatory_split",
    "load_development_split",
    "normalize_prompt",
    "parse_design_snapshot",
    "parse_production_freeze",
    "reverify_production_freeze",
    "result_paths",
    "review_gold_dataset",
    "summarize_gold_dataset",
    "validate_gold_dataset",
    "validate_manifest",
    "verify_file_checksum",
    "verify_confirmatory_split",
    "verify_manifest_checksums",
    "verify_production_freeze",
    "write_manifest",
    "write_json_exclusive",
    "write_provenance_json",
    "split_bundle_checksum",
    "validate_split_bundle",
]
