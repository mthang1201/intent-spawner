"""Load, write, and adapt Protocol-v5 manifest provenance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .paths import ResultPaths
from .provenance import write_manifest_atomic
from .schemas import (
    CandidateCatalogIdentity,
    EmbeddingIndexIdentity,
    ExtractorIdentity,
    ProtocolV5Manifest,
)
from .validation import validate_manifest, verify_manifest_checksums


def _require_keys(
    value: Mapping[str, Any],
    keys: tuple[str, ...],
    label: str,
) -> None:
    missing = [key for key in keys if key not in value]
    if missing:
        raise ValueError(f"{label} missing fields: {', '.join(missing)}")


def _verify_frozen_p2(
    p2: Mapping[str, Any],
    frozen: Mapping[str, Any],
) -> None:
    for field in (
        "backend_version",
        "pipeline_version",
        "corpus_checksum",
        "hybrid_index_checksum",
    ):
        if frozen.get(field) != p2.get(field):
            raise ValueError(
                f"P3 frozen_p2_provenance does not match P2 for {field}"
            )


def adapt_operational_provenance(
    p2_provenance: Mapping[str, Any],
    *,
    candidate_catalog_sha256: str,
    p3_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map supplied P2/P3 snapshots without importing or constructing a backend."""

    if not isinstance(p2_provenance, Mapping):
        raise ValueError("p2_provenance must be an object")
    _require_keys(
        p2_provenance,
        (
            "backend_version",
            "pipeline_version",
            "structured_intent_schema_version",
            "extractor_name",
            "extractor_version",
            "extractor_model_id",
            "extractor_prompt_version",
            "extractor_prompt_sha256",
            "dense_embedding_model_id",
            "dense_embedding_model_revision",
            "dense_index_version",
            "dense_index_checksum",
            "sparse_index_version",
            "sparse_index_checksum",
            "hybrid_index_version",
            "hybrid_index_checksum",
            "hybrid_retriever_version",
            "hybrid_rrf",
            "corpus_version",
            "corpus_checksum",
            "catalog_version",
            "constraint_evaluator_version",
            "constraint_policy_version",
            "ranker_version",
        ),
        "p2_provenance",
    )
    rrf = p2_provenance["hybrid_rrf"]
    if not isinstance(rrf, Mapping):
        raise ValueError("p2_provenance.hybrid_rrf must be an object")

    systems: dict[str, Any] = {
        "P2": {
            "backend_version": p2_provenance["backend_version"],
            "pipeline_version": p2_provenance["pipeline_version"],
        }
    }
    p3_reranker_version: str | None = None
    if p3_provenance is not None:
        if not isinstance(p3_provenance, Mapping):
            raise ValueError("p3_provenance must be an object")
        _require_keys(
            p3_provenance,
            (
                "backend_version",
                "pipeline_version",
                "reranker_version",
                "frozen_p2_provenance",
            ),
            "p3_provenance",
        )
        frozen = p3_provenance["frozen_p2_provenance"]
        if not isinstance(frozen, Mapping):
            raise ValueError("p3_provenance.frozen_p2_provenance must be an object")
        _verify_frozen_p2(p2_provenance, frozen)
        systems["P3"] = {
            "backend_version": p3_provenance["backend_version"],
            "pipeline_version": p3_provenance["pipeline_version"],
        }
        p3_reranker_version = p3_provenance["reranker_version"]

    return {
        "backend_system_versions": systems,
        "candidate_catalog": CandidateCatalogIdentity(
            catalog_version=p2_provenance["catalog_version"],
            catalog_sha256=candidate_catalog_sha256,
            corpus_version=p2_provenance["corpus_version"],
            corpus_sha256=p2_provenance["corpus_checksum"],
        ),
        "structured_intent_schema_version": p2_provenance[
            "structured_intent_schema_version"
        ],
        "extractor": ExtractorIdentity(
            extractor_name=p2_provenance["extractor_name"],
            extractor_version=p2_provenance["extractor_version"],
            extractor_model_id=p2_provenance["extractor_model_id"],
            extractor_prompt_version=p2_provenance["extractor_prompt_version"],
            extractor_prompt_sha256=p2_provenance["extractor_prompt_sha256"],
        ),
        "embedding_indexes": EmbeddingIndexIdentity(
            embedding_model_id=p2_provenance["dense_embedding_model_id"],
            embedding_model_revision=p2_provenance[
                "dense_embedding_model_revision"
            ],
            dense_index_version=p2_provenance["dense_index_version"],
            dense_index_sha256=p2_provenance["dense_index_checksum"],
            sparse_index_version=p2_provenance["sparse_index_version"],
            sparse_index_sha256=p2_provenance["sparse_index_checksum"],
            hybrid_index_version=p2_provenance["hybrid_index_version"],
            hybrid_index_sha256=p2_provenance["hybrid_index_checksum"],
        ),
        "retrieval_configuration": {
            "retriever_version": p2_provenance["hybrid_retriever_version"],
            **dict(rrf),
        },
        "constraint_ranking_configuration": {
            "constraint_evaluator_version": p2_provenance[
                "constraint_evaluator_version"
            ],
            "constraint_policy_version": p2_provenance[
                "constraint_policy_version"
            ],
            "ranker_version": p2_provenance["ranker_version"],
        },
        "p3_reranker_version": p3_reranker_version,
    }


def load_manifest(
    path: Path,
    *,
    dataset_path: Path | None = None,
    catalog_path: Path | None = None,
) -> ProtocolV5Manifest:
    """Read one strict manifest and optionally verify current input files."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON") from exc
    manifest = ProtocolV5Manifest.from_dict(value)
    verify_manifest_checksums(
        manifest,
        dataset_path=dataset_path,
        catalog_path=catalog_path,
    )
    return manifest


def write_manifest(
    paths: ResultPaths,
    manifest: ProtocolV5Manifest,
    *,
    dataset_path: Path | None = None,
    catalog_path: Path | None = None,
    development_override: bool = False,
) -> Path:
    """Validate identities before atomically publishing a run manifest."""

    validate_manifest(manifest)
    verify_manifest_checksums(
        manifest,
        dataset_path=dataset_path,
        catalog_path=catalog_path,
    )
    return write_manifest_atomic(
        paths,
        manifest,
        development_override=development_override,
    )


__all__ = [
    "adapt_operational_provenance",
    "load_manifest",
    "write_manifest",
]
