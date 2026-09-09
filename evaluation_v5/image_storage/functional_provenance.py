"""Source-bound recommendation provenance for Protocol-v5 E5 functionality."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from evaluation_v5.offline.recommenders import candidate_catalog_snapshot
from evaluation_v5.offline.runner import RAW_DIRECTORY_NAME, RECORDS_FILENAME
from evaluation_v5.offline.source_run import (
    SOURCE_RUN_PROVENANCE_SCHEMA_VERSION,
    SourceRunProvenanceError,
    VerifiedRecommendationRunProvenance,
    reverify_recommendation_run_provenance,
)
from recommender.candidate_corpus import build_candidate_corpus

from .contracts import ImageProbeResult, SecurityVerificationError, parse_image_digest


SOURCE_RECOMMENDATION_PROVENANCE_FILENAME = "source-recommendation-run.json"
SOURCE_RECOMMENDATIONS_FILENAME = "source-recommendations.jsonl"


def canonical_identity_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class BoundRecommendationSource:
    """Verified source capability plus an exact, checksum-bound record snapshot."""

    provenance: Mapping[str, Any]
    records: tuple[Mapping[str, Any], ...]
    records_bytes: bytes

    @property
    def recommendation_run_sha256(self) -> str:
        return str(self.provenance["recommendation_run_sha256"])

    def system_identity(self, system_id: str) -> Mapping[str, Any]:
        identities = self.provenance["system_identities"]
        if not isinstance(identities, Mapping) or system_id not in identities:
            raise SourceRunProvenanceError(
                f"source run lacks frozen identity for system {system_id!r}"
            )
        identity = identities[system_id]
        if not isinstance(identity, Mapping):
            raise SourceRunProvenanceError(
                f"source system identity {system_id!r} is malformed"
            )
        return identity

    def p2_identity(self) -> Mapping[str, Any]:
        identities = self.provenance["system_identities"]
        if isinstance(identities, Mapping) and isinstance(identities.get("P2"), Mapping):
            return identities["P2"]
        if isinstance(identities, Mapping) and isinstance(identities.get("P3"), Mapping):
            frozen = identities["P3"].get("frozen_p2")
            if isinstance(frozen, Mapping):
                return frozen
        raise SourceRunProvenanceError(
            "E5 functional provenance requires source-bound P2 configuration identity"
        )


def _strict_json(raw: bytes, *, label: str) -> Mapping[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise SourceRunProvenanceError(
                    f"{label} contains duplicate JSON field {key!r}"
                )
            value[key] = item
        return value

    def constant(value: str) -> Any:
        raise SourceRunProvenanceError(
            f"{label} contains non-finite number {value}"
        )

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=constant,
        )
    except SourceRunProvenanceError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SourceRunProvenanceError(f"{label} is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise SourceRunProvenanceError(f"{label} must be an object")
    return dict(value)


def _strict_records(raw: bytes) -> tuple[Mapping[str, Any], ...]:
    if not raw or not raw.endswith(b"\n"):
        raise SourceRunProvenanceError(
            "source recommendation JSONL must be non-empty and newline terminated"
        )
    records: list[Mapping[str, Any]] = []
    for index, line in enumerate(raw.splitlines(), start=1):
        records.append(
            _strict_json(line, label=f"source recommendation row {index}")
        )
    return tuple(records)


def verify_copied_recommendation_source(
    *,
    provenance_bytes: bytes,
    records_bytes: bytes,
) -> BoundRecommendationSource:
    """Verify the self-contained recommendation snapshot copied into E5.

    Unlike ``bind_recommendation_source``, this read-only adapter does not
    reopen an upstream recommendation directory. It is for downstream
    consumers of an immutable E5 package and accepts only the exact copied
    provenance document and JSONL bytes that the package seal protects.
    """
    provenance = _strict_json(
        provenance_bytes,
        label="source recommendation provenance",
    )
    if provenance.get("schema_version") != SOURCE_RUN_PROVENANCE_SCHEMA_VERSION:
        raise SourceRunProvenanceError(
            "current E5 requires Prompt 3 source provenance "
            f"{SOURCE_RUN_PROVENANCE_SCHEMA_VERSION}"
        )

    records_sha256 = hashlib.sha256(records_bytes).hexdigest()
    source_artifacts = provenance.get("source_artifacts")
    if (
        provenance.get("recommendation_run_sha256") != records_sha256
        or not isinstance(source_artifacts, Mapping)
        or source_artifacts.get("recommendations_sha256") != records_sha256
        or provenance.get("claims_permitted") is not False
    ):
        raise SourceRunProvenanceError(
            "copied recommendation JSONL is not checksum-bound to source provenance"
        )

    records = _strict_records(records_bytes)
    record_ids = tuple(record.get("record_id") for record in records)
    provenance_ids = provenance.get("record_ids")
    if (
        any(not isinstance(record_id, str) or not record_id for record_id in record_ids)
        or len(set(record_ids)) != len(record_ids)
        or not isinstance(provenance_ids, list)
        or any(not isinstance(record_id, str) or not record_id for record_id in provenance_ids)
        or len(set(provenance_ids)) != len(provenance_ids)
        or sorted(record_ids) != sorted(provenance_ids)
    ):
        raise SourceRunProvenanceError(
            "copied recommendation record IDs do not match source provenance"
        )

    catalog_identity = provenance.get("catalog_identity")
    raw_candidates = (
        catalog_identity.get("candidates")
        if isinstance(catalog_identity, Mapping)
        else None
    )
    if not isinstance(raw_candidates, list):
        raise SourceRunProvenanceError(
            "source recommendation catalog identity is malformed"
        )
    candidates: dict[str, Mapping[str, Any]] = {}
    for candidate in raw_candidates:
        if not isinstance(candidate, Mapping):
            raise SourceRunProvenanceError(
                "source recommendation catalog candidate is malformed"
            )
        candidate_id = candidate.get("candidate_id")
        image_id = candidate.get("image_id")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id
            or candidate_id in candidates
            or not isinstance(image_id, str)
            or not image_id
        ):
            raise SourceRunProvenanceError(
                "source recommendation catalog candidate identity is invalid"
            )
        candidates[candidate_id] = candidate

    systems = provenance.get("systems")
    identities = provenance.get("system_identities")
    if (
        not isinstance(systems, list)
        or any(not isinstance(system, str) or not system for system in systems)
        or len(set(systems)) != len(systems)
        or not isinstance(identities, Mapping)
        or set(identities) != set(systems)
    ):
        raise SourceRunProvenanceError(
            "source recommendation system identities are malformed"
        )

    for record in records:
        if (
            record.get("run_id") != provenance.get("run_id")
            or record.get("provenance_fingerprint")
            != provenance.get("provenance_fingerprint")
            or record.get("system_id") not in systems
        ):
            raise SourceRunProvenanceError(
                "copied recommendation record identity mismatches source provenance"
            )
        candidate_id = record.get("predicted_candidate_id")
        image_id = record.get("predicted_image_id")
        if candidate_id is None:
            if image_id is not None:
                raise SourceRunProvenanceError(
                    "source recommendation image exists without a selected candidate"
                )
        else:
            candidate = candidates.get(candidate_id)
            if not isinstance(candidate, Mapping) or candidate.get("image_id") != image_id:
                raise SourceRunProvenanceError(
                    "copied recommendation record/image mismatches its sealed catalog candidate"
                )

    source = BoundRecommendationSource(
        provenance=provenance,
        records=records,
        records_bytes=records_bytes,
    )
    # This establishes the complete Prompt 3 fields required by E5, including
    # indexes, retrieval, constraint/ranking, split, catalog, and P2 identity.
    try:
        source_manifest_identities(source)
    except (KeyError, TypeError) as exc:
        raise SourceRunProvenanceError(
            "source recommendation provenance is incomplete or malformed"
        ) from exc
    return source


def bind_recommendation_source(
    capability: VerifiedRecommendationRunProvenance,
    *,
    catalog: Mapping[str, Any],
) -> BoundRecommendationSource:
    """Reverify Prompt 3's capability and bind its exact rows to this catalog."""
    if type(capability) is not VerifiedRecommendationRunProvenance:
        raise TypeError(
            "E5 requires VerifiedRecommendationRunProvenance from "
            "verify_recommendation_run_provenance(); caller-supplied provenance is rejected"
        )
    verified = reverify_recommendation_run_provenance(capability)
    provenance = verified.to_dict()

    actual_catalog = candidate_catalog_snapshot(
        build_candidate_corpus(image_catalog=catalog)
    )
    if provenance.get("catalog_identity") != actual_catalog:
        raise SecurityVerificationError(
            "E5 catalog does not match the originating recommendation run catalog identity"
        )

    records_path = verified.evidence_dir / RAW_DIRECTORY_NAME / RECORDS_FILENAME
    try:
        raw = records_path.read_bytes()
    except OSError as exc:
        raise SourceRunProvenanceError(
            "source recommendation records could not be read at the E5 boundary"
        ) from exc
    if hashlib.sha256(raw).hexdigest() != verified.recommendation_run_sha256:
        raise SourceRunProvenanceError(
            "source recommendation records do not match the verified source-run checksum"
        )
    records = _strict_records(raw)
    record_ids = tuple(record.get("record_id") for record in records)
    if (
        any(not isinstance(record_id, str) or not record_id for record_id in record_ids)
        or len(set(record_ids)) != len(record_ids)
        or sorted(record_ids) != sorted(verified.record_ids)
    ):
        raise SourceRunProvenanceError(
            "source recommendation record IDs do not match verified provenance"
        )

    candidates = {
        item["candidate_id"]: item
        for item in actual_catalog["candidates"]
        if isinstance(item, Mapping)
    }
    systems = set(provenance["systems"])
    for record in records:
        if record.get("run_id") != provenance["run_id"]:
            raise SourceRunProvenanceError(
                "source recommendation record run identity mismatch"
            )
        if record.get("provenance_fingerprint") != provenance["provenance_fingerprint"]:
            raise SourceRunProvenanceError(
                "source recommendation record provenance fingerprint mismatch"
            )
        system_id = record.get("system_id")
        if system_id not in systems:
            raise SourceRunProvenanceError(
                "source recommendation record refers to an unbound system"
            )
        candidate_id = record.get("predicted_candidate_id")
        image_id = record.get("predicted_image_id")
        if candidate_id is None:
            if image_id is not None:
                raise SourceRunProvenanceError(
                    "source recommendation image exists without a selected candidate"
                )
            continue
        candidate = candidates.get(candidate_id)
        if not isinstance(candidate, Mapping) or candidate.get("image_id") != image_id:
            raise SourceRunProvenanceError(
                "source recommendation record/image does not match the sealed catalog candidate"
            )

    # Reopen the upstream package once more after reading the exact bytes. This
    # closes the time-of-check/time-of-use window at the execution boundary.
    current = reverify_recommendation_run_provenance(verified)
    if current.to_dict() != provenance:
        raise SourceRunProvenanceError(
            "source recommendation provenance changed while E5 bound its records"
        )
    return BoundRecommendationSource(
        provenance=provenance,
        records=records,
        records_bytes=raw,
    )


def selected_image_identity(
    *,
    image_id: str | None,
    catalog: Mapping[str, Any],
    probe_results: Sequence[ImageProbeResult],
) -> tuple[str | None, str | None]:
    """Derive the selected immutable digest and observed execution platform."""
    if image_id is None:
        return None, None
    entry = catalog.get("images", {}).get(image_id)
    if not isinstance(entry, Mapping):
        raise SecurityVerificationError(
            f"source recommendation selected image {image_id!r} outside the E5 catalog"
        )
    selected_digest = parse_image_digest(str(entry.get("reference", "")))
    matching = [result for result in probe_results if result.image_id == image_id]
    if any(
        result.image_digest != selected_digest
        or (
            result.resolved_image_digest is not None
            and result.resolved_image_digest != selected_digest
        )
        for result in matching
    ):
        raise SecurityVerificationError(
            f"image {image_id!r} probe identity disagrees with the selected catalog digest"
        )
    platforms = {
        result.resolved_image_platform
        for result in matching
        if result.resolved_image_platform
    }
    if len(platforms) > 1:
        raise SecurityVerificationError(
            f"image {image_id!r} probes disagree on the execution platform"
        )
    return selected_digest, next(iter(platforms), None)


def source_manifest_identities(
    source: BoundRecommendationSource | Mapping[str, Any],
) -> dict[str, Any]:
    """Derive cross-experiment manifest fields solely from source provenance."""
    provenance = source.provenance if isinstance(source, BoundRecommendationSource) else source
    if isinstance(source, BoundRecommendationSource):
        p2 = source.p2_identity()
    else:
        identities = provenance.get("system_identities")
        p2 = identities.get("P2") if isinstance(identities, Mapping) else None
        if not isinstance(p2, Mapping) and isinstance(identities, Mapping):
            p3 = identities.get("P3")
            p2 = p3.get("frozen_p2") if isinstance(p3, Mapping) else None
        if not isinstance(p2, Mapping):
            raise SourceRunProvenanceError(
                "E5 source provenance lacks source-bound P2 configuration identity"
            )
    extractor = p2["extractor_prompt"]
    indexes = p2["indexes"]
    catalog = provenance["catalog_identity"]
    split = provenance["split"]
    system_identities = provenance["system_identities"]
    backend_versions = {
        system_id: {
            key: identity[key]
            for key in ("adapter_version", "backend_name", "backend_version")
        }
        for system_id, identity in system_identities.items()
    }
    p3 = system_identities.get("P3") if isinstance(system_identities, Mapping) else None
    reranker = p3.get("reranker") if isinstance(p3, Mapping) else None
    return {
        "dataset_identity": {
            "dataset_id": split["dataset_id"],
            "dataset_sha256": split["dataset_sha256"],
        },
        "split_identity": {
            "split_id": split["split_id"],
            "stage": split["role"],
        },
        "backend_system_versions": backend_versions,
        "candidate_catalog": {
            key: catalog[key]
            for key in (
                "catalog_version",
                "catalog_sha256",
                "corpus_version",
                "corpus_sha256",
            )
        },
        "structured_intent_schema_version": p2[
            "structured_intent_schema_version"
        ],
        "extractor": {
            "extractor_name": extractor["extractor_name"],
            "extractor_version": extractor["extractor_version"],
            "extractor_model_id": extractor["model_id"],
            "extractor_prompt_version": extractor["prompt_version"],
            "extractor_prompt_sha256": extractor["prompt_sha256"],
        },
        "embedding_indexes": dict(indexes),
        "retrieval_configuration": dict(p2["retrieval_configuration"]),
        "constraint_ranking_configuration": dict(
            p2["constraint_ranking_configuration"]
        ),
        "p3_reranker_version": (
            reranker.get("version") if isinstance(reranker, Mapping) else None
        ),
    }


__all__ = [
    "BoundRecommendationSource",
    "SOURCE_RECOMMENDATIONS_FILENAME",
    "SOURCE_RECOMMENDATION_PROVENANCE_FILENAME",
    "bind_recommendation_source",
    "canonical_identity_sha256",
    "selected_image_identity",
    "source_manifest_identities",
    "verify_copied_recommendation_source",
]
