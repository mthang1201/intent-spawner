"""Contracts, data structures, and validation for Protocol-v5 E5 image storage scalability."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .contracts import parse_image_digest, file_sha256

STORAGE_SCHEMA_VERSION = "protocol-v5-image-storage-evidence-v1.0.0"
PROTOCOL_VERSION = "5.0.0"
EXPERIMENT_ID = "E5"


class StorageExecutionStatus(str, Enum):
    """Execution status of storage scalability observation."""

    OBSERVED = "OBSERVED"
    NOT_EXECUTED = "NOT_EXECUTED"


class SplitStage(str, Enum):
    """Split stage under evaluation."""

    DEVELOPMENT = "development"
    CONFIRMATORY = "confirmatory"


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
    """Inspected layer manifest and uncompressed/content size for one catalog image."""

    image_id: str
    image_reference: str
    image_digest: str
    platform: dict[str, str]
    layers: tuple[LayerInspection, ...]
    total_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "image_reference": self.image_reference,
            "image_digest": self.image_digest,
            "platform": dict(self.platform),
            "layers": [layer.to_dict() for layer in self.layers],
            "total_bytes": self.total_bytes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ImageLayerMetadata:
        return cls(
            image_id=str(data["image_id"]),
            image_reference=str(data["image_reference"]),
            image_digest=str(data["image_digest"]),
            platform={str(k): str(v) for k, v in data.get("platform", {}).items()},
            layers=tuple(LayerInspection.from_dict(l) for l in data.get("layers", ())),
            total_bytes=int(data["total_bytes"]),
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
