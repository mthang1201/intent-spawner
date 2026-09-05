"""Contracts, data structures, and validation for Protocol-v5 E5 image storage scalability."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
from pathlib import Path
import re
from typing import Any

from .contracts import parse_image_digest, file_sha256

STORAGE_SCHEMA_VERSION = "protocol-v5-image-storage-evidence-v1.0.0"
PROTOCOL_VERSION = "5.0.0"
EXPERIMENT_ID = "E5"

# Explicit size domain contracts
SIZE_DOMAIN_COMPRESSED_OCI_BLOB = "compressed_oci_manifest_layer_bytes"
SIZE_DOMAIN_UNCOMPRESSED = "uncompressed_filesystem_layer_bytes"

DEFAULT_CATALOG_SCALES = (4, 8, 16)


class StorageExecutionStatus(str, Enum):
    """Execution status of storage scalability observation."""

    OBSERVED = "OBSERVED"
    NOT_EXECUTED = "NOT_EXECUTED"
    INCOMPLETE = "INCOMPLETE"


class SplitStage(str, Enum):
    """Split stage under evaluation."""

    DEVELOPMENT = "development"
    CONFIRMATORY = "confirmatory"


class SizeDomainMismatchError(ValueError):
    """Raised when sizes from incompatible domains (compressed vs uncompressed) are aggregated."""


def assert_size_domain_consistent(domain_a: str, domain_b: str) -> None:
    """Ensure two storage size domains are identical and valid."""
    if domain_a != domain_b:
        raise SizeDomainMismatchError(
            f"Cross-domain aggregation rejected: {domain_a!r} vs {domain_b!r}"
        )
    if domain_a not in (SIZE_DOMAIN_COMPRESSED_OCI_BLOB, SIZE_DOMAIN_UNCOMPRESSED):
        raise SizeDomainMismatchError(f"Unrecognized storage size domain: {domain_a!r}")


@dataclass(frozen=True, slots=True)
class LayerInspection:
    """Individual container image layer descriptor."""

    digest: str
    size: int
    media_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "digest": self.digest,
            "size": self.size,
        }
        if self.media_type:
            data["media_type"] = self.media_type
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LayerInspection:
        return cls(
            digest=str(data["digest"]),
            size=int(data["size"]),
            media_type=str(data.get("media_type", "")),
        )


@dataclass(frozen=True, slots=True)
class ImageLayerMetadata:
    """Inspected layer manifest and immutable provenance for one catalog image."""

    image_id: str
    image_reference: str
    image_digest: str
    platform: dict[str, str]
    layers: tuple[LayerInspection, ...]
    total_bytes: int
    is_digest_pinned: bool = True
    resolved_digest: str = ""
    manifest_digest: str = ""
    manifest_media_type: str = ""
    config_digest: str = ""
    ordered_layer_digests: tuple[str, ...] = ()
    layer_media_types: tuple[str, ...] = ()
    layer_sizes: tuple[int, ...] = ()
    size_domain: str = SIZE_DOMAIN_COMPRESSED_OCI_BLOB
    uncompressed_layer_bytes: int | None = None

    def __post_init__(self) -> None:
        if not self.resolved_digest and self.image_digest:
            object.__setattr__(self, "resolved_digest", self.image_digest)
        if not self.ordered_layer_digests and self.layers:
            object.__setattr__(
                self, "ordered_layer_digests", tuple(l.digest for l in self.layers)
            )
        if not self.layer_media_types and self.layers:
            object.__setattr__(
                self, "layer_media_types", tuple(l.media_type for l in self.layers)
            )
        if not self.layer_sizes and self.layers:
            object.__setattr__(
                self, "layer_sizes", tuple(l.size for l in self.layers)
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "image_reference": self.image_reference,
            "is_digest_pinned": self.is_digest_pinned,
            "image_digest": self.image_digest,
            "resolved_digest": self.resolved_digest,
            "platform": dict(self.platform),
            "manifest_digest": self.manifest_digest,
            "manifest_media_type": self.manifest_media_type,
            "config_digest": self.config_digest,
            "ordered_layer_digests": list(self.ordered_layer_digests),
            "layer_media_types": list(self.layer_media_types),
            "layer_sizes": list(self.layer_sizes),
            "size_domain": self.size_domain,
            "uncompressed_layer_bytes": self.uncompressed_layer_bytes,
            "layers": [layer.to_dict() for layer in self.layers],
            "total_bytes": self.total_bytes,
            "layer_count": len(self.layers),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ImageLayerMetadata:
        layers = tuple(LayerInspection.from_dict(l) for l in data.get("layers", ()))
        ref = str(data["image_reference"])
        pinned = bool(data.get("is_digest_pinned", "@sha256:" in ref))
        digest = str(data.get("image_digest", ""))
        resolved = str(data.get("resolved_digest", digest))
        return cls(
            image_id=str(data["image_id"]),
            image_reference=ref,
            image_digest=digest,
            platform={str(k): str(v) for k, v in data.get("platform", {}).items()},
            layers=layers,
            total_bytes=int(data["total_bytes"]),
            is_digest_pinned=pinned,
            resolved_digest=resolved,
            manifest_digest=str(data.get("manifest_digest", "")),
            manifest_media_type=str(data.get("manifest_media_type", "")),
            config_digest=str(data.get("config_digest", "")),
            ordered_layer_digests=tuple(
                str(d) for d in data.get("ordered_layer_digests", [l.digest for l in layers])
            ),
            layer_media_types=tuple(
                str(m) for m in data.get("layer_media_types", [l.media_type for l in layers])
            ),
            layer_sizes=tuple(
                int(s) for s in data.get("layer_sizes", [l.size for l in layers])
            ),
            size_domain=str(data.get("size_domain", SIZE_DOMAIN_COMPRESSED_OCI_BLOB)),
            uncompressed_layer_bytes=(
                int(data["uncompressed_layer_bytes"])
                if data.get("uncompressed_layer_bytes") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class PrefixStorageMeasurement:
    """Accumulated storage metrics for an ordered catalog prefix."""

    prefix_size: int
    image_digests: tuple[str, ...]
    naive_logical_bytes: int
    unique_layer_bytes: int
    savings_bytes: int = 0
    savings_ratio: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "prefix_size": self.prefix_size,
            "image_digests": list(self.image_digests),
            "naive_logical_bytes": self.naive_logical_bytes,
            "unique_layer_bytes": self.unique_layer_bytes,
        }

    def to_extended_dict(self) -> dict[str, Any]:
        return {
            "prefix_size": self.prefix_size,
            "image_digests": list(self.image_digests),
            "naive_logical_bytes": self.naive_logical_bytes,
            "unique_layer_bytes": self.unique_layer_bytes,
            "savings_bytes": self.savings_bytes,
            "savings_ratio": round(self.savings_ratio, 6),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PrefixStorageMeasurement:
        naive = int(data["naive_logical_bytes"])
        unique = int(data["unique_layer_bytes"])
        savings = naive - unique
        ratio = (savings / naive) if naive > 0 else 0.0
        return cls(
            prefix_size=int(data["prefix_size"]),
            image_digests=tuple(str(d) for d in data.get("image_digests", ())),
            naive_logical_bytes=naive,
            unique_layer_bytes=unique,
            savings_bytes=savings,
            savings_ratio=ratio,
        )


@dataclass(frozen=True, slots=True)
class MarginalStorageRecord:
    """First-class evidence of marginal unique bytes per newly introduced image (U_n - U_(n-1))."""

    introduction_index: int
    image_id: str
    image_reference: str
    resolved_digest: str
    previous_unique_bytes: int
    new_unique_bytes: int
    marginal_unique_bytes: int
    cumulative_logical_bytes: int
    cumulative_unique_bytes: int
    cumulative_savings_bytes: int
    cumulative_savings_ratio: float
    size_domain: str = SIZE_DOMAIN_COMPRESSED_OCI_BLOB

    def to_dict(self) -> dict[str, Any]:
        return {
            "introduction_index": self.introduction_index,
            "image_id": self.image_id,
            "image_reference": self.image_reference,
            "resolved_digest": self.resolved_digest,
            "previous_unique_bytes": self.previous_unique_bytes,
            "new_unique_bytes": self.new_unique_bytes,
            "marginal_unique_bytes": self.marginal_unique_bytes,
            "cumulative_logical_bytes": self.cumulative_logical_bytes,
            "cumulative_unique_bytes": self.cumulative_unique_bytes,
            "cumulative_savings_bytes": self.cumulative_savings_bytes,
            "cumulative_savings_ratio": round(self.cumulative_savings_ratio, 6),
            "size_domain": self.size_domain,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MarginalStorageRecord:
        return cls(
            introduction_index=int(data["introduction_index"]),
            image_id=str(data["image_id"]),
            image_reference=str(data["image_reference"]),
            resolved_digest=str(data["resolved_digest"]),
            previous_unique_bytes=int(data["previous_unique_bytes"]),
            new_unique_bytes=int(data["new_unique_bytes"]),
            marginal_unique_bytes=int(data["marginal_unique_bytes"]),
            cumulative_logical_bytes=int(data["cumulative_logical_bytes"]),
            cumulative_unique_bytes=int(data["cumulative_unique_bytes"]),
            cumulative_savings_bytes=int(data["cumulative_savings_bytes"]),
            cumulative_savings_ratio=float(data["cumulative_savings_ratio"]),
            size_domain=str(data.get("size_domain", SIZE_DOMAIN_COMPRESSED_OCI_BLOB)),
        )


def compute_marginal_storage(
    inspections: Sequence[ImageLayerMetadata],
) -> list[MarginalStorageRecord]:
    """Compute first-class marginal unique byte records (U_n - U_(n-1)) for ordered images."""
    records: list[MarginalStorageRecord] = []
    seen_unique_layers: dict[str, int] = {}
    prev_unique = 0
    cumulative_logical = 0

    for idx, meta in enumerate(inspections, start=1):
        cumulative_logical += meta.total_bytes
        for layer in meta.layers:
            seen_unique_layers[layer.digest] = layer.size

        current_unique = sum(seen_unique_layers.values())
        marginal_unique = current_unique - prev_unique

        # Invariant checks
        if current_unique > cumulative_logical:
            raise ValueError(
                f"Prefix {idx} violates non-expansion: unique={current_unique} > logical={cumulative_logical}"
            )
        if marginal_unique < 0:
            raise ValueError(
                f"Negative marginal unique bytes at index {idx}: {marginal_unique}"
            )
        if marginal_unique > meta.total_bytes:
            raise ValueError(
                f"Marginal unique bytes ({marginal_unique}) exceed image logical size ({meta.total_bytes}) at index {idx}"
            )

        savings = cumulative_logical - current_unique
        ratio = (savings / cumulative_logical) if cumulative_logical > 0 else 0.0

        records.append(
            MarginalStorageRecord(
                introduction_index=idx,
                image_id=meta.image_id,
                image_reference=meta.image_reference,
                resolved_digest=meta.resolved_digest or meta.image_digest,
                previous_unique_bytes=prev_unique,
                new_unique_bytes=current_unique,
                marginal_unique_bytes=marginal_unique,
                cumulative_logical_bytes=cumulative_logical,
                cumulative_unique_bytes=current_unique,
                cumulative_savings_bytes=savings,
                cumulative_savings_ratio=ratio,
                size_domain=meta.size_domain,
            )
        )
        prev_unique = current_unique

    return records


@dataclass(frozen=True, slots=True)
class PairwiseReuseRecord:
    """Long-form pairwise layer reuse observation between two catalog images."""

    image_a: str
    image_b: str
    image_a_digest: str
    image_b_digest: str
    shared_layer_count: int
    shared_layer_bytes: int
    jaccard_layer_count: float
    jaccard_layer_bytes: float
    size_domain: str = SIZE_DOMAIN_COMPRESSED_OCI_BLOB

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_a": self.image_a,
            "image_b": self.image_b,
            "image_a_digest": self.image_a_digest,
            "image_b_digest": self.image_b_digest,
            "shared_layer_count": self.shared_layer_count,
            "shared_layer_bytes": self.shared_layer_bytes,
            "jaccard_layer_count": round(self.jaccard_layer_count, 6),
            "jaccard_layer_bytes": round(self.jaccard_layer_bytes, 6),
            "size_domain": self.size_domain,
        }


@dataclass(frozen=True, slots=True)
class PairwiseReuseAnalysis:
    """Complete pairwise layer-reuse analysis with symmetric count and byte matrices."""

    image_ids: tuple[str, ...]
    image_digests: tuple[str, ...]
    shared_layer_count_matrix: tuple[tuple[int, ...], ...]
    shared_layer_byte_matrix: tuple[tuple[int, ...], ...]
    jaccard_byte_matrix: tuple[tuple[float, ...], ...]
    pairwise_records: tuple[PairwiseReuseRecord, ...]
    diagonal_semantics: str = "Self total layer count and self total logical layer bytes"
    symmetry_verified: bool = True
    size_domain: str = SIZE_DOMAIN_COMPRESSED_OCI_BLOB

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_ids": list(self.image_ids),
            "image_digests": list(self.image_digests),
            "diagonal_semantics": self.diagonal_semantics,
            "symmetry_verified": self.symmetry_verified,
            "size_domain": self.size_domain,
            "shared_layer_count_matrix": [list(row) for row in self.shared_layer_count_matrix],
            "shared_layer_byte_matrix": [list(row) for row in self.shared_layer_byte_matrix],
            "jaccard_byte_matrix": [
                [round(v, 6) for v in row] for row in self.jaccard_byte_matrix
            ],
            "pairwise_records": [r.to_dict() for r in self.pairwise_records],
        }


def compute_pairwise_layer_reuse(
    inspections: Sequence[ImageLayerMetadata],
) -> PairwiseReuseAnalysis:
    """Compute pairwise layer reuse matrices and long-form records using exact digest matching."""
    image_ids = tuple(meta.image_id for meta in inspections)
    image_digests = tuple(meta.resolved_digest or meta.image_digest for meta in inspections)
    n = len(inspections)

    layer_maps: list[dict[str, int]] = []
    for meta in inspections:
        img_map: dict[str, int] = {}
        for l in meta.layers:
            img_map[l.digest] = l.size
        layer_maps.append(img_map)

    count_matrix: list[list[int]] = [[0] * n for _ in range(n)]
    byte_matrix: list[list[int]] = [[0] * n for _ in range(n)]
    jaccard_matrix: list[list[float]] = [[0.0] * n for _ in range(n)]
    records: list[PairwiseReuseRecord] = []

    domain = inspections[0].size_domain if inspections else SIZE_DOMAIN_COMPRESSED_OCI_BLOB

    for i in range(n):
        for j in range(n):
            set_i = set(layer_maps[i].keys())
            set_j = set(layer_maps[j].keys())
            shared_digests = set_i & set_j
            union_digests = set_i | set_j

            shared_count = len(shared_digests)
            shared_bytes = sum(layer_maps[i][d] for d in shared_digests)
            union_bytes = (
                sum(layer_maps[i].values())
                + sum(layer_maps[j].values())
                - shared_bytes
            )

            jaccard_count = (shared_count / len(union_digests)) if union_digests else 1.0
            jaccard_bytes = (shared_bytes / union_bytes) if union_bytes > 0 else 1.0

            count_matrix[i][j] = shared_count
            byte_matrix[i][j] = shared_bytes
            jaccard_matrix[i][j] = jaccard_bytes

            if i <= j:
                records.append(
                    PairwiseReuseRecord(
                        image_a=image_ids[i],
                        image_b=image_ids[j],
                        image_a_digest=image_digests[i],
                        image_b_digest=image_digests[j],
                        shared_layer_count=shared_count,
                        shared_layer_bytes=shared_bytes,
                        jaccard_layer_count=jaccard_count,
                        jaccard_layer_bytes=jaccard_bytes,
                        size_domain=domain,
                    )
                )

    # Verify symmetry invariant: M[i, j] == M[j, i]
    for i in range(n):
        for j in range(n):
            if count_matrix[i][j] != count_matrix[j][i]:
                raise ValueError(
                    f"Count matrix asymmetry at ({i},{j}): {count_matrix[i][j]} != {count_matrix[j][i]}"
                )
            if byte_matrix[i][j] != byte_matrix[j][i]:
                raise ValueError(
                    f"Byte matrix asymmetry at ({i},{j}): {byte_matrix[i][j]} != {byte_matrix[j][i]}"
                )

    return PairwiseReuseAnalysis(
        image_ids=image_ids,
        image_digests=image_digests,
        shared_layer_count_matrix=tuple(tuple(r) for r in count_matrix),
        shared_layer_byte_matrix=tuple(tuple(r) for r in byte_matrix),
        jaccard_byte_matrix=tuple(tuple(r) for r in jaccard_matrix),
        pairwise_records=tuple(records),
        diagonal_semantics="Self total layer count and self total logical layer bytes",
        symmetry_verified=True,
        size_domain=domain,
    )


@dataclass(frozen=True, slots=True)
class CatalogImageEntry:
    """Administrator-approved immutable catalog image entry."""

    image_id: str
    reference: str
    is_digest_pinned: bool
    display_name: str = ""
    description: str = ""
    capabilities: tuple[str, ...] = ()
    match_terms: tuple[str, ...] = ()
    priority: int = 0
    resolved_digest: str = ""


@dataclass(frozen=True, slots=True)
class ExperimentalCatalogConfig:
    """Explicit frozen experimental catalog configuration representing approved images and scale membership."""

    catalog_version: str
    catalog_hash: str
    ordered_images: tuple[CatalogImageEntry, ...]
    catalog_scales: tuple[int, ...] = DEFAULT_CATALOG_SCALES

    def get_scale_images(self, scale: int) -> tuple[CatalogImageEntry, ...]:
        """Return approved images for the requested scale if sufficient, or available subset."""
        return self.ordered_images[:scale]

    def get_scale_status(self, scale: int) -> tuple[str, str]:
        """Return (status, reason) for a given scale based on approved image availability."""
        available = len(self.ordered_images)
        if available >= scale:
            return ("OBSERVED", "")
        return (
            "NOT_EXECUTED",
            f"insufficient_approved_images: catalog defines {available} approved image(s), {scale} required",
        )


def get_experimental_catalog_config(
    catalog: Mapping[str, Any],
    scales: Sequence[int] = DEFAULT_CATALOG_SCALES,
) -> ExperimentalCatalogConfig:
    """Construct an immutable experimental catalog configuration from loaded catalog data."""
    version = str(catalog.get("catalog_version", "unknown"))
    images_data = catalog.get("images", {})
    entries: list[CatalogImageEntry] = []

    for img_id, item in images_data.items():
        if not isinstance(item, Mapping):
            continue
        ref = str(item.get("reference", ""))
        pinned = "@sha256:" in ref
        digest = parse_image_digest(ref) if pinned else ""
        entries.append(
            CatalogImageEntry(
                image_id=str(img_id),
                reference=ref,
                is_digest_pinned=pinned,
                display_name=str(item.get("display_name", "")),
                description=str(item.get("description", "")),
                capabilities=tuple(str(c) for c in item.get("capabilities", ())),
                match_terms=tuple(str(t) for t in item.get("match_terms", ())),
                priority=int(item.get("priority", 100)),
                resolved_digest=digest,
            )
        )

    # Sort strictly by (priority, image_id)
    entries.sort(key=lambda e: (e.priority, e.image_id))

    # Compute deterministic catalog hash
    hasher = hashlib.sha256()
    hasher.update(version.encode("utf-8"))
    for e in entries:
        hasher.update(f"{e.image_id}:{e.reference}:{e.priority}".encode("utf-8"))
    cat_hash = hasher.hexdigest()

    return ExperimentalCatalogConfig(
        catalog_version=version,
        catalog_hash=cat_hash,
        ordered_images=tuple(entries),
        catalog_scales=tuple(sorted(set(scales))),
    )


@dataclass(frozen=True, slots=True)
class ScaleLevelEvaluationRecord:
    """Combined scale-level record capturing joint storage and P2 recommendation metrics."""

    catalog_size: int
    catalog_id: str
    catalog_hash: str
    ordered_immutable_image_references: tuple[str, ...]

    # Storage metrics
    storage_measurement_status: str
    logical_image_bytes: int | None
    unique_layer_bytes: int | None
    dedup_saving_bytes: int | None
    dedup_saving_ratio: float | None
    marginal_unique_bytes: int | None
    size_domain: str

    # Recommendation metrics
    p2_evaluation_status: str
    p2_image_acceptable_accuracy: float | None
    p2_image_preferred_accuracy: float | None
    p2_retrieval_recall_at_k: float | None
    recall_k: int
    p2_latency_mean_seconds: float | None
    p2_latency_median_seconds: float | None
    p2_latency_p95_seconds: float | None
    p2_latency_min_seconds: float | None
    p2_latency_max_seconds: float | None
    p2_latency_std_seconds: float | None

    # Provenance and dataset info
    evaluation_dataset_identity: str
    dataset_sha256: str
    evaluated_case_count: int
    feasible_case_count: int
    p2_config_version: str
    provenance: dict[str, Any]
    status_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_size": self.catalog_size,
            "catalog_id": self.catalog_id,
            "catalog_hash": self.catalog_hash,
            "ordered_immutable_image_references": list(
                self.ordered_immutable_image_references
            ),
            "storage_measurement_status": self.storage_measurement_status,
            "logical_image_bytes": self.logical_image_bytes,
            "unique_layer_bytes": self.unique_layer_bytes,
            "dedup_saving_bytes": self.dedup_saving_bytes,
            "dedup_saving_ratio": (
                round(self.dedup_saving_ratio, 6)
                if self.dedup_saving_ratio is not None
                else None
            ),
            "marginal_unique_bytes": self.marginal_unique_bytes,
            "size_domain": self.size_domain,
            "p2_evaluation_status": self.p2_evaluation_status,
            "p2_image_acceptable_accuracy": (
                round(self.p2_image_acceptable_accuracy, 6)
                if self.p2_image_acceptable_accuracy is not None
                else None
            ),
            "p2_image_preferred_accuracy": (
                round(self.p2_image_preferred_accuracy, 6)
                if self.p2_image_preferred_accuracy is not None
                else None
            ),
            "p2_retrieval_recall_at_k": (
                round(self.p2_retrieval_recall_at_k, 6)
                if self.p2_retrieval_recall_at_k is not None
                else None
            ),
            "recall_k": self.recall_k,
            "p2_latency_mean_seconds": (
                round(self.p2_latency_mean_seconds, 6)
                if self.p2_latency_mean_seconds is not None
                else None
            ),
            "p2_latency_median_seconds": (
                round(self.p2_latency_median_seconds, 6)
                if self.p2_latency_median_seconds is not None
                else None
            ),
            "p2_latency_p95_seconds": (
                round(self.p2_latency_p95_seconds, 6)
                if self.p2_latency_p95_seconds is not None
                else None
            ),
            "p2_latency_min_seconds": (
                round(self.p2_latency_min_seconds, 6)
                if self.p2_latency_min_seconds is not None
                else None
            ),
            "p2_latency_max_seconds": (
                round(self.p2_latency_max_seconds, 6)
                if self.p2_latency_max_seconds is not None
                else None
            ),
            "p2_latency_std_seconds": (
                round(self.p2_latency_std_seconds, 6)
                if self.p2_latency_std_seconds is not None
                else None
            ),
            "evaluation_dataset_identity": self.evaluation_dataset_identity,
            "dataset_sha256": self.dataset_sha256,
            "evaluated_case_count": self.evaluated_case_count,
            "feasible_case_count": self.feasible_case_count,
            "p2_config_version": self.p2_config_version,
            "provenance": dict(self.provenance),
            "status_reason": self.status_reason,
        }


@dataclass(frozen=True, slots=True)
class StorageEvidenceRecord:
    """Complete Protocol-v5 E5 image storage evidence artifact."""

    schema_version: str
    protocol_version: str
    experiment_id: str
    execution_status: str
    split_stage: str
    claims_permitted: bool
    measured_at_utc: str
    catalog: dict[str, Any]
    platform: dict[str, Any]
    measurement_method: str
    prefixes: tuple[PrefixStorageMeasurement, ...]
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "experiment_id": self.experiment_id,
            "execution_status": self.execution_status,
            "split_stage": self.split_stage,
            "claims_permitted": self.claims_permitted,
            "measured_at_utc": self.measured_at_utc,
            "catalog": dict(self.catalog),
            "platform": dict(self.platform),
            "measurement_method": self.measurement_method,
            "prefixes": [p.to_dict() for p in self.prefixes],
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StorageEvidenceRecord:
        return cls(
            schema_version=str(data.get("schema_version", STORAGE_SCHEMA_VERSION)),
            protocol_version=str(data.get("protocol_version", PROTOCOL_VERSION)),
            experiment_id=str(data.get("experiment_id", EXPERIMENT_ID)),
            execution_status=str(data["execution_status"]),
            split_stage=str(data["split_stage"]),
            claims_permitted=bool(data["claims_permitted"]),
            measured_at_utc=str(data["measured_at_utc"]),
            catalog=dict(data["catalog"]),
            platform=dict(data["platform"]),
            measurement_method=str(data["measurement_method"]),
            prefixes=tuple(
                PrefixStorageMeasurement.from_dict(p) for p in data.get("prefixes", ())
            ),
            provenance=dict(data["provenance"]),
        )


def get_ordered_catalog_images(
    catalog: Mapping[str, Any],
) -> list[tuple[str, str, str]]:
    """Resolve images in deterministic frozen catalog order (sorted by priority, then image_id).

    Returns a list of tuples: (image_id, image_reference, image_digest).
    """
    images_data = catalog.get("images", {})
    ordered_items = []
    for image_id, entry in images_data.items():
        if not isinstance(entry, Mapping):
            continue
        priority = int(entry.get("priority", 100))
        ref = str(entry.get("reference", ""))
        digest = parse_image_digest(ref)
        ordered_items.append((priority, image_id, ref, digest))

    ordered_items.sort(key=lambda x: (x[0], x[1]))
    return [(item[1], item[2], item[3]) for item in ordered_items]

