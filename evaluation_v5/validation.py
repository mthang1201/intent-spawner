"""Fail-closed validation and checksum verification for Protocol-v5."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping

from evaluation_v4.dataset import file_sha256

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


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
_PLACEHOLDERS = frozenset(
    {"unknown", "unavailable", "none", "null", "n/a", "na", "tbd"}
)


class ManifestValidationError(ValueError):
    """A Protocol-v5 manifest violates its versioned contract."""


class ChecksumMismatchError(ManifestValidationError):
    """A current input does not match its recorded SHA-256 identity."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestValidationError(message)


def _nonblank(value: object, label: str, *, required: bool) -> None:
    if value is None and not required:
        return
    _require(isinstance(value, str) and bool(value.strip()), f"{label} must be non-blank")
    if required:
        _require(value.strip().lower() not in _PLACEHOLDERS, f"{label} cannot be a placeholder")


def _sha256(value: object, label: str, *, required: bool) -> None:
    if value is None and not required:
        return
    _require(isinstance(value, str) and bool(_SHA256.fullmatch(value)), f"{label} must be a lowercase SHA-256 digest")


def _json_mapping(
    value: Mapping[str, Any],
    label: str,
    *,
    required: bool,
) -> None:
    _require(isinstance(value, Mapping), f"{label} must be an object")
    _require(all(isinstance(key, str) and key for key in value), f"{label} keys must be non-blank strings")
    if required:
        _require(bool(value), f"OBSERVED manifest requires {label}")
        _require_complete_json(value, label)
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ManifestValidationError(f"{label} must be finite JSON data") from exc


def _require_complete_json(value: object, label: str) -> None:
    if value is None:
        raise ManifestValidationError(f"OBSERVED manifest has missing {label}")
    if isinstance(value, str):
        if not value.strip() or value.strip().lower() in _PLACEHOLDERS:
            raise ManifestValidationError(f"OBSERVED manifest has placeholder {label}")
        return
    if isinstance(value, Mapping):
        if not value:
            raise ManifestValidationError(f"OBSERVED manifest has empty {label}")
        for key, item in value.items():
            _require_complete_json(item, f"{label}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _require_complete_json(item, f"{label}[{index}]")


def _validate_timestamp(value: object) -> None:
    _require(
        isinstance(value, str) and value.endswith("Z"),
        "execution_timestamp_utc must be an ISO-8601 UTC timestamp ending in Z",
    )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestValidationError(
            "execution_timestamp_utc must be an ISO-8601 UTC timestamp"
        ) from exc
    _require(
        parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed),
        "execution_timestamp_utc must use UTC",
    )


def validate_manifest(manifest: ProtocolV5Manifest) -> ProtocolV5Manifest:
    """Validate one manifest without reading or executing any experiment."""

    _require(isinstance(manifest, ProtocolV5Manifest), "manifest has the wrong type")
    _require(
        manifest.schema_version == MANIFEST_SCHEMA_VERSION,
        "manifest schema_version is unsupported",
    )
    _require(
        manifest.protocol_version == PROTOCOL_VERSION,
        "manifest protocol_version is unsupported",
    )
    _require(
        isinstance(manifest.experiment_id, ExperimentId),
        "manifest experiment_id is unsupported",
    )
    _require(
        isinstance(manifest.dataset_identity, DatasetIdentity),
        "dataset_identity has the wrong type",
    )
    _require(
        isinstance(manifest.split_identity, SplitIdentity),
        "split_identity has the wrong type",
    )
    _require(
        isinstance(manifest.candidate_catalog, CandidateCatalogIdentity),
        "candidate_catalog has the wrong type",
    )
    _require(
        isinstance(manifest.extractor, ExtractorIdentity),
        "extractor has the wrong type",
    )
    _require(
        isinstance(manifest.embedding_indexes, EmbeddingIndexIdentity),
        "embedding_indexes has the wrong type",
    )
    _require(
        isinstance(manifest.run_id, str) and bool(_SAFE_ID.fullmatch(manifest.run_id)),
        "run_id must be a bounded filesystem-safe identifier",
    )
    _require(
        isinstance(manifest.split_identity.split_id, str)
        and bool(_SAFE_ID.fullmatch(manifest.split_identity.split_id)),
        "split_identity.split_id must be a bounded filesystem-safe identifier",
    )
    _require(
        isinstance(manifest.split_identity.stage, SplitStage),
        "split_identity.stage is unsupported",
    )
    _require(
        isinstance(manifest.execution_status, EvidenceStatus),
        "execution_status is unsupported",
    )
    _validate_timestamp(manifest.execution_timestamp_utc)

    observed = manifest.execution_status is EvidenceStatus.OBSERVED
    if manifest.git_revision is None:
        _require(not observed, "OBSERVED manifest requires git_revision")
    else:
        _require(
            isinstance(manifest.git_revision, str)
            and bool(_GIT_REVISION.fullmatch(manifest.git_revision)),
            "git_revision must be a full lowercase Git revision",
        )

    _nonblank(
        manifest.dataset_identity.dataset_id,
        "dataset_identity.dataset_id",
        required=observed,
    )
    _sha256(
        manifest.dataset_identity.dataset_sha256,
        "dataset_identity.dataset_sha256",
        required=observed,
    )
    catalog = manifest.candidate_catalog
    for label, value in (
        ("candidate_catalog.catalog_version", catalog.catalog_version),
        ("candidate_catalog.corpus_version", catalog.corpus_version),
    ):
        _nonblank(value, label, required=observed)
    for label, value in (
        ("candidate_catalog.catalog_sha256", catalog.catalog_sha256),
        ("candidate_catalog.corpus_sha256", catalog.corpus_sha256),
    ):
        _sha256(value, label, required=observed)

    _nonblank(
        manifest.structured_intent_schema_version,
        "structured_intent_schema_version",
        required=observed,
    )
    extractor = manifest.extractor
    for label, value in (
        ("extractor.extractor_name", extractor.extractor_name),
        ("extractor.extractor_version", extractor.extractor_version),
        ("extractor.extractor_model_id", extractor.extractor_model_id),
        ("extractor.extractor_prompt_version", extractor.extractor_prompt_version),
    ):
        _nonblank(value, label, required=observed)
    _sha256(
        extractor.extractor_prompt_sha256,
        "extractor.extractor_prompt_sha256",
        required=observed,
    )

    indexes = manifest.embedding_indexes
    for label, value in (
        ("embedding_indexes.embedding_model_id", indexes.embedding_model_id),
        (
            "embedding_indexes.embedding_model_revision",
            indexes.embedding_model_revision,
        ),
        ("embedding_indexes.dense_index_version", indexes.dense_index_version),
        ("embedding_indexes.sparse_index_version", indexes.sparse_index_version),
        ("embedding_indexes.hybrid_index_version", indexes.hybrid_index_version),
    ):
        _nonblank(value, label, required=observed)
    for label, value in (
        ("embedding_indexes.dense_index_sha256", indexes.dense_index_sha256),
        ("embedding_indexes.sparse_index_sha256", indexes.sparse_index_sha256),
        ("embedding_indexes.hybrid_index_sha256", indexes.hybrid_index_sha256),
    ):
        _sha256(value, label, required=observed)

    _json_mapping(
        manifest.backend_system_versions,
        "backend_system_versions",
        required=observed,
    )
    _json_mapping(
        manifest.retrieval_configuration,
        "retrieval_configuration",
        required=observed,
    )
    _json_mapping(
        manifest.constraint_ranking_configuration,
        "constraint_ranking_configuration",
        required=observed,
    )
    _json_mapping(
        manifest.environment_identity,
        "environment_identity",
        required=observed,
    )
    if observed:
        _nonblank(
            manifest.environment_identity.get("environment_id"),
            "environment_identity.environment_id",
            required=True,
        )

    _require(
        isinstance(manifest.random_seeds, tuple)
        and all(
            isinstance(seed, int) and not isinstance(seed, bool)
            for seed in manifest.random_seeds
        ),
        "random_seeds must contain only integers",
    )
    p3_present = any(key.upper() == "P3" for key in manifest.backend_system_versions)
    if p3_present:
        _nonblank(
            manifest.p3_reranker_version,
            "p3_reranker_version",
            required=True,
        )
    else:
        _require(
            manifest.p3_reranker_version is None,
            "p3_reranker_version is only valid when P3 participates",
        )
    return manifest


def verify_file_checksum(path: Path, expected_sha256: str, *, label: str) -> str:
    """Verify one file using the mature Protocol-v4 streaming SHA-256 helper."""

    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    _sha256(expected_sha256, f"{label} expected checksum", required=True)
    actual = file_sha256(path)
    if actual != expected_sha256:
        raise ChecksumMismatchError(
            f"{label} checksum mismatch: expected {expected_sha256}, found {actual}"
        )
    return actual


def verify_manifest_checksums(
    manifest: ProtocolV5Manifest,
    *,
    dataset_path: Path | None = None,
    catalog_path: Path | None = None,
) -> dict[str, str]:
    """Verify supplied current inputs against identities recorded in a manifest."""

    validate_manifest(manifest)
    verified: dict[str, str] = {}
    if dataset_path is not None:
        expected = manifest.dataset_identity.dataset_sha256
        if expected is None:
            raise ManifestValidationError(
                "dataset_path was supplied but dataset_identity.dataset_sha256 is missing"
            )
        verified["dataset"] = verify_file_checksum(
            dataset_path, expected, label="dataset"
        )
    if catalog_path is not None:
        expected = manifest.candidate_catalog.catalog_sha256
        if expected is None:
            raise ManifestValidationError(
                "catalog_path was supplied but candidate_catalog.catalog_sha256 is missing"
            )
        verified["candidate_catalog"] = verify_file_checksum(
            catalog_path, expected, label="candidate catalog"
        )
    return verified


__all__ = [
    "ChecksumMismatchError",
    "ManifestValidationError",
    "validate_manifest",
    "verify_file_checksum",
    "verify_manifest_checksums",
]
