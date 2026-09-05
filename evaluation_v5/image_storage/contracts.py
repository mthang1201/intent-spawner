"""Contracts, data structures, and security validation for Protocol-v5 image probes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


IMAGE_PROBE_MANIFEST_SCHEMA_VERSION = "protocol-v5-image-probe-manifest-v1.1.0"
IMAGE_PROBE_RECORD_SCHEMA_VERSION = "protocol-v5-image-probe-record-v1.1.0"
FUNCTIONAL_EVALUATION_SCHEMA_VERSION = "protocol-v5-image-functional-evaluation-v1.3.0"
FUNCTIONAL_METRICS_SCHEMA_VERSION = "protocol-v5-image-functional-metrics-v1.3.0"
E5_RUN_SCHEMA_VERSION = "protocol-v5-image-validation-run-v1.3.0"

DIGEST_PATTERN = re.compile(r"@sha256:([a-f0-9]{64})$")


class SecurityVerificationError(ValueError):
    """Raised when an unapproved, unpinned, or mismatched image is provided."""


class ProbeExecutionError(RuntimeError):
    """Raised when a probe runner encounters an unrecoverable execution failure."""


class ProbeExecutionStatus(str, Enum):
    """Execution lifecycle status of one probe observation."""

    EXECUTED = "EXECUTED"
    IMAGE_NOT_PRESENT = "IMAGE_NOT_PRESENT"
    CONTAINER_UNAVAILABLE = "CONTAINER_UNAVAILABLE"
    NOT_EXECUTED_DRY_RUN = "NOT_EXECUTED_DRY_RUN"


class DimensionCStatus(str, Enum):
    """Functional execution result for a recommendation."""

    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EXECUTED = "NOT_EXECUTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class ProbeSpec:
    """Bounded functional probe specification for one capability."""

    probe_id: str
    capability: str
    description: str
    script: str
    timeout_seconds: float = 15.0
    cpu_limit: str = "1000m"
    memory_limit: str = "1Gi"
    expected_metadata_keys: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "capability": self.capability,
            "description": self.description,
            "script": self.script,
            "timeout_seconds": self.timeout_seconds,
            "cpu_limit": self.cpu_limit,
            "memory_limit": self.memory_limit,
            "expected_metadata_keys": list(self.expected_metadata_keys),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProbeSpec":
        return cls(
            probe_id=str(data["probe_id"]),
            capability=str(data["capability"]),
            description=str(data["description"]),
            script=str(data["script"]),
            timeout_seconds=float(data.get("timeout_seconds", 15.0)),
            cpu_limit=str(data.get("cpu_limit", "1000m")),
            memory_limit=str(data.get("memory_limit", "1Gi")),
            expected_metadata_keys=tuple(data.get("expected_metadata_keys", ())),
        )


@dataclass(frozen=True, slots=True)
class ImageProbeSpec:
    """Collection of functional probes configured for one approved catalog image."""

    image_id: str
    image_reference: str
    image_digest: str
    documented_capabilities: tuple[str, ...]
    probes: tuple[ProbeSpec, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "image_reference": self.image_reference,
            "image_digest": self.image_digest,
            "documented_capabilities": list(self.documented_capabilities),
            "probes": [probe.to_dict() for probe in self.probes],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ImageProbeSpec":
        return cls(
            image_id=str(data["image_id"]),
            image_reference=str(data["image_reference"]),
            image_digest=str(data["image_digest"]),
            documented_capabilities=tuple(data.get("documented_capabilities", ())),
            probes=tuple(ProbeSpec.from_dict(p) for p in data.get("probes", ())),
        )


@dataclass(frozen=True, slots=True)
class ImageProbeManifest:
    """Complete manifest of functional probes derived from the image catalog."""

    catalog_version: str
    catalog_sha256: str
    catalog_path: str
    images: tuple[ImageProbeSpec, ...]
    schema_version: str = IMAGE_PROBE_MANIFEST_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "catalog_version": self.catalog_version,
            "catalog_sha256": self.catalog_sha256,
            "catalog_path": self.catalog_path,
            "images": [img.to_dict() for img in self.images],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ImageProbeManifest":
        return cls(
            schema_version=str(data.get("schema_version", IMAGE_PROBE_MANIFEST_SCHEMA_VERSION)),
            catalog_version=str(data["catalog_version"]),
            catalog_sha256=str(data["catalog_sha256"]),
            catalog_path=str(data["catalog_path"]),
            images=tuple(ImageProbeSpec.from_dict(img) for img in data.get("images", ())),
        )


@dataclass(frozen=True, slots=True)
class ImageProbeResult:
    """Raw observation from running one functional probe inside a container."""

    probe_id: str
    image_id: str
    image_reference: str
    image_digest: str
    capability: str
    success: bool
    execution_status: str = ProbeExecutionStatus.NOT_EXECUTED_DRY_RUN.value
    resolved_image_digest: str | None = None
    import_version_metadata: dict[str, str] = field(default_factory=dict)
    runtime_seconds: float = 0.0
    error_category: str | None = None
    error_message: str | None = None
    stdout: str | None = None
    execution_mode: str = "dry_run"
    timestamp_utc: str = ""
    schema_version: str = IMAGE_PROBE_RECORD_SCHEMA_VERSION

    @property
    def is_executed(self) -> bool:
        """True if the probe actually executed in a started container."""
        return self.execution_status == ProbeExecutionStatus.EXECUTED.value

    @property
    def is_genuine_probe_failure(self) -> bool:
        """True only if the container ran and the functional probe failed."""
        return self.is_executed and not self.success

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "probe_id": self.probe_id,
            "image_id": self.image_id,
            "image_reference": self.image_reference,
            "image_digest": self.image_digest,
            "capability": self.capability,
            "success": self.success,
            "execution_status": self.execution_status,
            "resolved_image_digest": self.resolved_image_digest,
            "import_version_metadata": dict(self.import_version_metadata),
            "runtime_seconds": round(self.runtime_seconds, 6),
            "error_category": self.error_category,
            "error_message": self.error_message,
            "stdout": self.stdout,
            "execution_mode": self.execution_mode,
            "timestamp_utc": self.timestamp_utc,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ImageProbeResult":
        # Backward-compatible execution_status inference if loading v1.0
        status = data.get("execution_status")
        if status is None:
            err = data.get("error_category")
            if err == "NOT_EXECUTED_DRY_RUN":
                status = ProbeExecutionStatus.NOT_EXECUTED_DRY_RUN.value
            elif err in ("IMAGE_NOT_PRESENT", "CONTAINER_LAUNCH_FAILED"):
                status = ProbeExecutionStatus.IMAGE_NOT_PRESENT.value if err == "IMAGE_NOT_PRESENT" else ProbeExecutionStatus.CONTAINER_UNAVAILABLE.value
            else:
                status = ProbeExecutionStatus.EXECUTED.value

        return cls(
            schema_version=str(data.get("schema_version", IMAGE_PROBE_RECORD_SCHEMA_VERSION)),
            probe_id=str(data["probe_id"]),
            image_id=str(data["image_id"]),
            image_reference=str(data["image_reference"]),
            image_digest=str(data["image_digest"]),
            capability=str(data["capability"]),
            success=bool(data["success"]),
            execution_status=str(status),
            resolved_image_digest=data.get("resolved_image_digest"),
            import_version_metadata=dict(data.get("import_version_metadata", {})),
            runtime_seconds=float(data.get("runtime_seconds", 0.0)),
            error_category=data.get("error_category"),
            error_message=data.get("error_message"),
            stdout=data.get("stdout"),
            execution_mode=str(data.get("execution_mode", "dry_run")),
            timestamp_utc=str(data.get("timestamp_utc", "")),
        )


@dataclass(frozen=True, slots=True)
class FunctionalEvaluationRecord:
    """Evaluation of one recommendation row across Dimensions A, B, and C."""

    case_id: str
    family_id: str
    variant_id: str
    system_id: str
    predicted_image_id: str | None
    required_capabilities: tuple[str, ...]
    gold_preferred_image_id: str | None
    gold_acceptable_image_ids: tuple[str, ...]
    dimension_a_gold_match: bool
    dimension_a_preferred_match: bool
    dimension_b_catalog_satisfied: bool
    missing_catalog_capabilities: tuple[str, ...]
    dimension_c_status: str
    dimension_c_functional_satisfied: bool | None
    dimension_c_execution_coverage: bool
    dimension_c_eligible: bool = True
    failed_probes: tuple[str, ...] = ()
    unavailable_probes: tuple[str, ...] = ()
    undefined_probes: tuple[str, ...] = ()
    mismatch_types: tuple[str, ...] = ()
    execution_status: str = "COMPLETED"
    source_predicted_image_value: str | None = None
    schema_version: str = FUNCTIONAL_EVALUATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "family_id": self.family_id,
            "variant_id": self.variant_id,
            "system_id": self.system_id,
            "source_predicted_image_value": (
                self.source_predicted_image_value
                if self.source_predicted_image_value is not None
                else self.predicted_image_id
            ),
            "predicted_image_id": self.predicted_image_id,
            "required_capabilities": list(self.required_capabilities),
            "gold_preferred_image_id": self.gold_preferred_image_id,
            "gold_acceptable_image_ids": list(self.gold_acceptable_image_ids),
            "dimension_a_gold_match": self.dimension_a_gold_match,
            "dimension_a_preferred_match": self.dimension_a_preferred_match,
            "dimension_b_catalog_satisfied": self.dimension_b_catalog_satisfied,
            "missing_catalog_capabilities": list(self.missing_catalog_capabilities),
            "dimension_c_status": self.dimension_c_status,
            "dimension_c_functional_satisfied": self.dimension_c_functional_satisfied,
            "dimension_c_execution_coverage": self.dimension_c_execution_coverage,
            "dimension_c_eligible": self.dimension_c_eligible,
            "failed_probes": list(self.failed_probes),
            "unavailable_probes": list(self.unavailable_probes),
            "undefined_probes": list(self.undefined_probes),
            "mismatch_types": list(self.mismatch_types),
            "execution_status": self.execution_status,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FunctionalEvaluationRecord":
        status_c = data.get("dimension_c_status")
        satisfied_c = data.get("dimension_c_functional_satisfied")
        if status_c is None:
            # Infer from legacy boolean
            if satisfied_c is True:
                status_c = DimensionCStatus.PASS.value
            elif satisfied_c is False:
                # Check mismatches to see if it was actually not executed
                mismatches = tuple(data.get("mismatch_types", ()))
                if "EXECUTION_UNAVAILABLE" in mismatches:
                    status_c = DimensionCStatus.NOT_EXECUTED.value
                    satisfied_c = None
                else:
                    status_c = DimensionCStatus.FAIL.value
            else:
                status_c = DimensionCStatus.NOT_EXECUTED.value

        return cls(
            schema_version=str(data.get("schema_version", FUNCTIONAL_EVALUATION_SCHEMA_VERSION)),
            case_id=str(data["case_id"]),
            family_id=str(data.get("family_id", "")),
            variant_id=str(data.get("variant_id", "")),
            system_id=str(data["system_id"]),
            source_predicted_image_value=data.get("source_predicted_image_value", data.get("predicted_image_id")),
            predicted_image_id=data.get("predicted_image_id"),
            required_capabilities=tuple(data.get("required_capabilities", ())),
            gold_preferred_image_id=data.get("gold_preferred_image_id"),
            gold_acceptable_image_ids=tuple(data.get("gold_acceptable_image_ids", ())),
            dimension_a_gold_match=bool(data["dimension_a_gold_match"]),
            dimension_a_preferred_match=bool(data["dimension_a_preferred_match"]),
            dimension_b_catalog_satisfied=bool(data["dimension_b_catalog_satisfied"]),
            missing_catalog_capabilities=tuple(data.get("missing_catalog_capabilities", ())),
            dimension_c_status=str(status_c),
            dimension_c_functional_satisfied=satisfied_c,
            dimension_c_execution_coverage=bool(
                data.get("dimension_c_execution_coverage", status_c in (DimensionCStatus.PASS.value, DimensionCStatus.FAIL.value))
            ),
            dimension_c_eligible=bool(
                data.get("dimension_c_eligible", data.get("dimension_b_catalog_satisfied", True))
            ),
            failed_probes=tuple(data.get("failed_probes", ())),
            unavailable_probes=tuple(data.get("unavailable_probes", ())),
            undefined_probes=tuple(data.get("undefined_probes", ())),
            mismatch_types=tuple(data.get("mismatch_types", ())),
            execution_status=str(data.get("execution_status", "COMPLETED")),
        )


def parse_image_digest(image_reference: str) -> str:
    """Extract and validate the sha256 digest from an image reference."""
    match = DIGEST_PATTERN.search(image_reference)
    if not match:
        raise SecurityVerificationError(
            f"Image reference {image_reference!r} is not pinned with an immutable @sha256: digest."
        )
    return f"sha256:{match.group(1)}"


def validate_approved_image_reference(
    image_reference: str,
    catalog: Mapping[str, Any],
) -> str:
    """Verify that an image reference belongs to the approved catalog and is pinned by digest.

    Returns the extracted image digest if valid, or raises SecurityVerificationError.
    """
    digest = parse_image_digest(image_reference)
    images = catalog.get("images", {})
    approved_refs = {
        data.get("reference")
        for data in images.values()
        if isinstance(data, Mapping) and "reference" in data
    }
    if image_reference not in approved_refs:
        raise SecurityVerificationError(
            f"Image reference {image_reference!r} is not an administrator-approved image in the catalog."
        )
    return digest


def file_sha256(path: Path) -> str:
    """Compute the sha256 hex digest of a file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()
