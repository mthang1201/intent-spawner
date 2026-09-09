"""Immutable, source-verified recommendation-run provenance for downstream work.

The capability in this module is built from complete evidence, never from
caller-supplied identity fields. Confirmatory evidence still requires the
sealed split capability; development evidence is reopened from its canonical
repository source.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping as MappingABC
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from evaluation_v4.dataset import file_sha256
from evaluation_v5.isolation import VerifiedConfirmatorySplit
from evaluation_v5.split_dataset import load_development_split

from .runner import (
    COMPLETION_FILENAME,
    PROVENANCE_FILENAME,
    RAW_DIRECTORY_NAME,
    RECORDS_FILENAME,
    REPORT_DIRECTORY_NAME,
)
from .validate_evidence import (
    OfflineEvidenceValidationError,
    validate_offline_evidence,
)


SOURCE_RUN_PROVENANCE_SCHEMA_VERSION = (
    "protocol-v5-source-run-provenance-v1.1.0"
)


class SourceRunProvenanceError(RuntimeError):
    """A recommendation run could not be converted into verified provenance."""


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise SourceRunProvenanceError(
            "source-run provenance must contain finite JSON values"
        ) from exc


_SOURCE_RUN_CONSTRUCTION_KEY = object()


class VerifiedRecommendationRunProvenance(MappingABC[str, Any]):
    """Immutable provenance capability derived from a fully validated run."""

    __slots__ = ("_json", "_evidence_dir", "_split_capability")

    def __init__(
        self,
        *,
        document: Mapping[str, Any],
        evidence_dir: Path,
        split_capability: VerifiedConfirmatorySplit | None,
        _construction_key: object,
    ) -> None:
        if _construction_key is not _SOURCE_RUN_CONSTRUCTION_KEY:
            raise TypeError(
                "VerifiedRecommendationRunProvenance is produced only by "
                "verify_recommendation_run_provenance()"
            )
        object.__setattr__(self, "_json", _canonical_json(document))
        object.__setattr__(self, "_evidence_dir", evidence_dir)
        object.__setattr__(self, "_split_capability", split_capability)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("VerifiedRecommendationRunProvenance is immutable")

    @property
    def evidence_dir(self) -> Path:
        return self._evidence_dir

    @property
    def recommendation_run_sha256(self) -> str:
        return str(self["recommendation_run_sha256"])

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(self["record_ids"])

    def to_dict(self) -> dict[str, Any]:
        value = json.loads(self._json)
        assert isinstance(value, dict)
        return value

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise SourceRunProvenanceError(
                    f"{label} contains duplicate JSON field {key!r}"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                SourceRunProvenanceError(
                    f"{label} contains non-finite number {constant}"
                )
            ),
        )
    except SourceRunProvenanceError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SourceRunProvenanceError(f"{label} is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise SourceRunProvenanceError(f"{label} must contain an object")
    return dict(value)


def _read(path: Path, *, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SourceRunProvenanceError(f"{label} could not be read") from exc


def _p2_identity(provenance: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "structured_intent_schema_version": provenance[
            "structured_intent_schema_version"
        ],
        "extractor_prompt": {
            "extractor_name": provenance["extractor_name"],
            "extractor_version": provenance["extractor_version"],
            "model_id": provenance["extractor_model_id"],
            "prompt_version": provenance["extractor_prompt_version"],
            "prompt_sha256": provenance["extractor_prompt_sha256"],
        },
        "indexes": {
            "embedding_model_id": provenance["embedding_model_id"],
            "embedding_model_revision": provenance[
                "embedding_model_revision"
            ],
            "dense_index_version": provenance["dense_index_version"],
            "dense_index_sha256": provenance["dense_index_sha256"],
            "sparse_index_version": provenance["sparse_index_version"],
            "sparse_index_sha256": provenance["sparse_index_sha256"],
            "hybrid_index_version": provenance["hybrid_index_version"],
            "hybrid_index_sha256": provenance["hybrid_index_sha256"],
        },
        "retrieval_configuration": dict(
            provenance["retrieval_configuration"]
        ),
        "constraint_ranking_configuration": dict(
            provenance["constraint_ranking_configuration"]
        ),
    }


def _system_identities(provenance: Mapping[str, Any]) -> dict[str, Any]:
    raw = provenance["system_frozen_provenance"]
    assert isinstance(raw, Mapping)
    result: dict[str, Any] = {}
    for system_id in provenance["systems"]:
        selected = raw[system_id]
        assert isinstance(selected, Mapping)
        identity: dict[str, Any] = {
            "adapter_version": selected["adapter_version"],
            "backend_name": selected["backend_name"],
            "backend_version": selected["backend_version"],
        }
        if system_id == "P2":
            identity.update(_p2_identity(selected))
        elif system_id == "P3":
            frozen_p2 = selected["frozen_p2_provenance"]
            assert isinstance(frozen_p2, Mapping)
            identity.update(
                {
                    "pipeline_version": selected["pipeline_version"],
                    "reranker": {
                        "name": selected["reranker_name"],
                        "version": selected["reranker_version"],
                        "model_id": selected.get("reranker_model_id"),
                        "prompt_version": selected.get(
                            "reranker_prompt_version"
                        ),
                        "prompt_sha256": selected.get(
                            "reranker_prompt_sha256"
                        ),
                    },
                    "frozen_p2": _p2_identity(frozen_p2),
                }
            )
        result[system_id] = identity
    return result


def verify_recommendation_run_provenance(
    evidence_dir: Path,
    *,
    confirmatory_split: VerifiedConfirmatorySplit | None = None,
) -> VerifiedRecommendationRunProvenance:
    """Validate a complete run and return its source-bound provenance."""

    root = evidence_dir.resolve()
    if (
        confirmatory_split is not None
        and type(confirmatory_split) is not VerifiedConfirmatorySplit
    ):
        raise TypeError(
            "confirmatory source-run provenance requires a "
            "VerifiedConfirmatorySplit"
        )
    split = confirmatory_split or load_development_split()
    raw_dir = root / RAW_DIRECTORY_NAME
    report_dir = root / REPORT_DIRECTORY_NAME
    provenance_path = raw_dir / PROVENANCE_FILENAME
    records_path = raw_dir / RECORDS_FILENAME
    completion_path = report_dir / COMPLETION_FILENAME
    try:
        initial_digests = {
            path: file_sha256(path)
            for path in (provenance_path, records_path, completion_path)
        }
    except OSError as exc:
        raise SourceRunProvenanceError(
            "recommendation evidence package is incomplete or unreadable"
        ) from exc
    try:
        validation = validate_offline_evidence(root, split=split)
    except OfflineEvidenceValidationError as exc:
        raise SourceRunProvenanceError(
            "recommendation evidence failed authoritative validation"
        ) from exc
    if validation.get("status") != "PASS":
        raise SourceRunProvenanceError(
            "recommendation evidence did not pass authoritative validation"
        )
    provenance_raw = _read(provenance_path, label="offline provenance")
    records_raw = _read(records_path, label="recommendation records")
    completion_raw = _read(completion_path, label="offline completion")
    current_bytes = {
        provenance_path: provenance_raw,
        records_path: records_raw,
        completion_path: completion_raw,
    }
    if any(
        hashlib.sha256(raw).hexdigest() != initial_digests[path]
        for path, raw in current_bytes.items()
    ):
        raise SourceRunProvenanceError(
            "recommendation evidence changed during source verification"
        )
    provenance = _strict_json(provenance_raw, label="offline provenance")
    completion = _strict_json(completion_raw, label="offline completion")
    if not records_raw.endswith(b"\n"):
        raise SourceRunProvenanceError(
            "recommendation records must be newline terminated"
        )
    records = [
        _strict_json(line, label=f"recommendation record {index}")
        for index, line in enumerate(records_raw.splitlines(), start=1)
    ]
    recommendation_sha256 = file_sha256(records_path)
    if completion.get("recommendations_jsonl_sha256") != recommendation_sha256:
        raise SourceRunProvenanceError(
            "completion does not bind the exact recommendation run"
        )
    record_ids = [record.get("record_id") for record in records]
    if not all(isinstance(item, str) and item for item in record_ids):
        raise SourceRunProvenanceError(
            "validated recommendation run contains an invalid record ID"
        )
    document = {
        "schema_version": SOURCE_RUN_PROVENANCE_SCHEMA_VERSION,
        "protocol_version": provenance["protocol_version"],
        "experiment_id": provenance["experiment_id"],
        "run_id": provenance["run_id"],
        "provenance_fingerprint": provenance["provenance_fingerprint"],
        "recommendation_run_sha256": recommendation_sha256,
        "record_ids": sorted(record_ids),
        "source_artifacts": {
            "provenance_sha256": file_sha256(provenance_path),
            "recommendations_sha256": recommendation_sha256,
            "completion_sha256": file_sha256(completion_path),
        },
        "split": dict(provenance["split"]),
        "freeze_identity": dict(provenance["freeze_identity"]),
        "git_revision": provenance["git_revision"],
        "systems": list(provenance["systems"]),
        "system_identities": _system_identities(provenance),
        "catalog_identity": dict(provenance["candidate_catalog"]),
        "claims_permitted": False,
    }
    return VerifiedRecommendationRunProvenance(
        document=document,
        evidence_dir=root,
        split_capability=confirmatory_split,
        _construction_key=_SOURCE_RUN_CONSTRUCTION_KEY,
    )


def reverify_recommendation_run_provenance(
    provenance: VerifiedRecommendationRunProvenance,
) -> VerifiedRecommendationRunProvenance:
    """Reopen all sources and reject a stale or tampered capability."""

    if type(provenance) is not VerifiedRecommendationRunProvenance:
        raise TypeError(
            "a VerifiedRecommendationRunProvenance from "
            "verify_recommendation_run_provenance() is required"
        )
    current = verify_recommendation_run_provenance(
        provenance.evidence_dir,
        confirmatory_split=provenance._split_capability,
    )
    if current.to_dict() != provenance.to_dict():
        raise SourceRunProvenanceError(
            "recommendation-run provenance capability no longer matches its sources"
        )
    return current


# Loading is verification; there is intentionally no unauthenticated parser.
load_recommendation_run_provenance = verify_recommendation_run_provenance


__all__ = [
    "SOURCE_RUN_PROVENANCE_SCHEMA_VERSION",
    "SourceRunProvenanceError",
    "VerifiedRecommendationRunProvenance",
    "load_recommendation_run_provenance",
    "reverify_recommendation_run_provenance",
    "verify_recommendation_run_provenance",
]
