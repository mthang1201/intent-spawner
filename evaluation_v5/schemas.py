"""Versioned, serializable contracts for Protocol-v5 experiment manifests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


MANIFEST_SCHEMA_VERSION = "protocol-v5-manifest-v1.0.0"
PROTOCOL_VERSION = "5.0.0"


class EvidenceStatus(str, Enum):
    """Lifecycle state of one Protocol-v5 evidence package."""

    PLANNED = "PLANNED"
    DRY_RUN = "DRY_RUN"
    OBSERVED = "OBSERVED"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"
    NOT_EXECUTED = "NOT_EXECUTED"


class ExperimentId(str, Enum):
    """Closed Protocol-v5 experiment registry."""

    E1 = "E1"
    E2 = "E2"
    E3 = "E3"
    E4 = "E4"
    E5 = "E5"
    E6 = "E6"


class SplitStage(str, Enum):
    """Whether a split may be used for development or confirmation."""

    DEVELOPMENT = "development"
    CONFIRMATORY = "confirmatory"


def _exact_mapping(
    value: object,
    expected: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    keys = set(value)
    missing = sorted(expected - keys)
    extra = sorted(keys - expected)
    if missing:
        raise ValueError(f"{label} missing fields: {', '.join(missing)}")
    if extra:
        raise ValueError(f"{label} unexpected fields: {', '.join(extra)}")
    return dict(value)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return dict(value)


@dataclass(frozen=True, slots=True)
class DatasetIdentity:
    dataset_id: str | None
    dataset_sha256: str | None

    _FIELDS = frozenset({"dataset_id", "dataset_sha256"})

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_sha256": self.dataset_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> "DatasetIdentity":
        payload = _exact_mapping(value, cls._FIELDS, "dataset_identity")
        return cls(
            dataset_id=payload["dataset_id"],
            dataset_sha256=payload["dataset_sha256"],
        )


@dataclass(frozen=True, slots=True)
class SplitIdentity:
    split_id: str
    stage: SplitStage

    _FIELDS = frozenset({"split_id", "stage"})

    def to_dict(self) -> dict[str, Any]:
        return {"split_id": self.split_id, "stage": self.stage.value}

    @classmethod
    def from_dict(cls, value: object) -> "SplitIdentity":
        payload = _exact_mapping(value, cls._FIELDS, "split_identity")
        try:
            stage = SplitStage(payload["stage"])
        except (TypeError, ValueError) as exc:
            raise ValueError("split_identity.stage is unsupported") from exc
        return cls(split_id=payload["split_id"], stage=stage)


@dataclass(frozen=True, slots=True)
class CandidateCatalogIdentity:
    catalog_version: str | None
    catalog_sha256: str | None
    corpus_version: str | None
    corpus_sha256: str | None

    _FIELDS = frozenset(
        {"catalog_version", "catalog_sha256", "corpus_version", "corpus_sha256"}
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_version": self.catalog_version,
            "catalog_sha256": self.catalog_sha256,
            "corpus_version": self.corpus_version,
            "corpus_sha256": self.corpus_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> "CandidateCatalogIdentity":
        payload = _exact_mapping(value, cls._FIELDS, "candidate_catalog")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class ExtractorIdentity:
    extractor_name: str | None
    extractor_version: str | None
    extractor_model_id: str | None
    extractor_prompt_version: str | None
    extractor_prompt_sha256: str | None

    _FIELDS = frozenset(
        {
            "extractor_name",
            "extractor_version",
            "extractor_model_id",
            "extractor_prompt_version",
            "extractor_prompt_sha256",
        }
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "extractor_model_id": self.extractor_model_id,
            "extractor_prompt_version": self.extractor_prompt_version,
            "extractor_prompt_sha256": self.extractor_prompt_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ExtractorIdentity":
        payload = _exact_mapping(value, cls._FIELDS, "extractor")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class EmbeddingIndexIdentity:
    embedding_model_id: str | None
    embedding_model_revision: str | None
    dense_index_version: str | None
    dense_index_sha256: str | None
    sparse_index_version: str | None
    sparse_index_sha256: str | None
    hybrid_index_version: str | None
    hybrid_index_sha256: str | None

    _FIELDS = frozenset(
        {
            "embedding_model_id",
            "embedding_model_revision",
            "dense_index_version",
            "dense_index_sha256",
            "sparse_index_version",
            "sparse_index_sha256",
            "hybrid_index_version",
            "hybrid_index_sha256",
        }
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "embedding_model_id": self.embedding_model_id,
            "embedding_model_revision": self.embedding_model_revision,
            "dense_index_version": self.dense_index_version,
            "dense_index_sha256": self.dense_index_sha256,
            "sparse_index_version": self.sparse_index_version,
            "sparse_index_sha256": self.sparse_index_sha256,
            "hybrid_index_version": self.hybrid_index_version,
            "hybrid_index_sha256": self.hybrid_index_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> "EmbeddingIndexIdentity":
        payload = _exact_mapping(value, cls._FIELDS, "embedding_indexes")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class ProtocolV5Manifest:
    """One manifest shared by all Protocol-v5 experiment families."""

    experiment_id: ExperimentId
    run_id: str
    git_revision: str | None
    execution_timestamp_utc: str
    dataset_identity: DatasetIdentity
    split_identity: SplitIdentity
    backend_system_versions: Mapping[str, Any]
    candidate_catalog: CandidateCatalogIdentity
    structured_intent_schema_version: str | None
    extractor: ExtractorIdentity
    embedding_indexes: EmbeddingIndexIdentity
    retrieval_configuration: Mapping[str, Any]
    constraint_ranking_configuration: Mapping[str, Any]
    p3_reranker_version: str | None
    environment_identity: Mapping[str, Any]
    random_seeds: tuple[int, ...]
    execution_status: EvidenceStatus
    schema_version: str = MANIFEST_SCHEMA_VERSION
    protocol_version: str = PROTOCOL_VERSION

    _FIELDS = frozenset(
        {
            "schema_version",
            "protocol_version",
            "experiment_id",
            "run_id",
            "git_revision",
            "execution_timestamp_utc",
            "dataset_identity",
            "split_identity",
            "backend_system_versions",
            "candidate_catalog",
            "structured_intent_schema_version",
            "extractor",
            "embedding_indexes",
            "retrieval_configuration",
            "constraint_ranking_configuration",
            "p3_reranker_version",
            "environment_identity",
            "random_seeds",
            "execution_status",
        }
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "experiment_id": self.experiment_id.value,
            "run_id": self.run_id,
            "git_revision": self.git_revision,
            "execution_timestamp_utc": self.execution_timestamp_utc,
            "dataset_identity": self.dataset_identity.to_dict(),
            "split_identity": self.split_identity.to_dict(),
            "backend_system_versions": dict(self.backend_system_versions),
            "candidate_catalog": self.candidate_catalog.to_dict(),
            "structured_intent_schema_version": self.structured_intent_schema_version,
            "extractor": self.extractor.to_dict(),
            "embedding_indexes": self.embedding_indexes.to_dict(),
            "retrieval_configuration": dict(self.retrieval_configuration),
            "constraint_ranking_configuration": dict(
                self.constraint_ranking_configuration
            ),
            "p3_reranker_version": self.p3_reranker_version,
            "environment_identity": dict(self.environment_identity),
            "random_seeds": list(self.random_seeds),
            "execution_status": self.execution_status.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ProtocolV5Manifest":
        payload = _exact_mapping(value, cls._FIELDS, "manifest")
        try:
            experiment_id = ExperimentId(payload["experiment_id"])
        except (TypeError, ValueError) as exc:
            raise ValueError("manifest.experiment_id is unsupported") from exc
        try:
            execution_status = EvidenceStatus(payload["execution_status"])
        except (TypeError, ValueError) as exc:
            raise ValueError("manifest.execution_status is unsupported") from exc
        seeds = payload["random_seeds"]
        if isinstance(seeds, (str, bytes, Mapping)) or not isinstance(
            seeds, (list, tuple)
        ):
            raise ValueError("manifest.random_seeds must be a list")
        manifest = cls(
            schema_version=payload["schema_version"],
            protocol_version=payload["protocol_version"],
            experiment_id=experiment_id,
            run_id=payload["run_id"],
            git_revision=payload["git_revision"],
            execution_timestamp_utc=payload["execution_timestamp_utc"],
            dataset_identity=DatasetIdentity.from_dict(payload["dataset_identity"]),
            split_identity=SplitIdentity.from_dict(payload["split_identity"]),
            backend_system_versions=_mapping(
                payload["backend_system_versions"], "backend_system_versions"
            ),
            candidate_catalog=CandidateCatalogIdentity.from_dict(
                payload["candidate_catalog"]
            ),
            structured_intent_schema_version=payload[
                "structured_intent_schema_version"
            ],
            extractor=ExtractorIdentity.from_dict(payload["extractor"]),
            embedding_indexes=EmbeddingIndexIdentity.from_dict(
                payload["embedding_indexes"]
            ),
            retrieval_configuration=_mapping(
                payload["retrieval_configuration"], "retrieval_configuration"
            ),
            constraint_ranking_configuration=_mapping(
                payload["constraint_ranking_configuration"],
                "constraint_ranking_configuration",
            ),
            p3_reranker_version=payload["p3_reranker_version"],
            environment_identity=_mapping(
                payload["environment_identity"], "environment_identity"
            ),
            random_seeds=tuple(seeds),
            execution_status=execution_status,
        )
        from .validation import validate_manifest

        return validate_manifest(manifest)


__all__ = [
    "CandidateCatalogIdentity",
    "DatasetIdentity",
    "EmbeddingIndexIdentity",
    "EvidenceStatus",
    "ExperimentId",
    "ExtractorIdentity",
    "MANIFEST_SCHEMA_VERSION",
    "PROTOCOL_VERSION",
    "ProtocolV5Manifest",
    "SplitIdentity",
    "SplitStage",
]
