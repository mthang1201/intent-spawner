"""Protocol-v5 experiment architecture; no experiment is executed on import."""

from .manifest import adapt_operational_provenance, load_manifest, write_manifest
from .paths import (
    DEFAULT_RESULTS_ROOT,
    PROTOCOL_DIRECTORY,
    ResultPaths,
    create_result_directory,
    result_paths,
)
from .provenance import write_provenance_json
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

__all__ = [
    "CandidateCatalogIdentity",
    "ChecksumMismatchError",
    "DEFAULT_RESULTS_ROOT",
    "DatasetIdentity",
    "EmbeddingIndexIdentity",
    "EvidenceStatus",
    "ExperimentId",
    "ExtractorIdentity",
    "MANIFEST_SCHEMA_VERSION",
    "ManifestValidationError",
    "PROTOCOL_DIRECTORY",
    "PROTOCOL_VERSION",
    "ProtocolV5Manifest",
    "ResultPaths",
    "SplitIdentity",
    "SplitStage",
    "adapt_operational_provenance",
    "create_result_directory",
    "load_manifest",
    "result_paths",
    "validate_manifest",
    "verify_file_checksum",
    "verify_manifest_checksums",
    "write_manifest",
    "write_provenance_json",
]
