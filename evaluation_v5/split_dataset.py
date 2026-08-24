"""Strict, separately loadable Protocol-v5 dataset split bundles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence

import yaml

from evaluation_v4.dataset import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEVELOPMENT_DATASET = ROOT / "benchmarks_v5" / "v5-development.yaml"
SPLIT_BUNDLE_SCHEMA_VERSION = "protocol-v5-split-bundle-v1.0.0"
SPLIT_BUNDLE_SCHEMA_VERSION_V2 = "protocol-v5-split-bundle-v2.0.0"
SUPPORTED_SPLIT_BUNDLE_SCHEMA_VERSIONS = frozenset(
    {SPLIT_BUNDLE_SCHEMA_VERSION, SPLIT_BUNDLE_SCHEMA_VERSION_V2}
)
DEFAULT_DEVELOPMENT_SPLIT_ID = "v5-development"
DEFAULT_CONFIRMATORY_SPLIT_ID = "v5-confirmatory"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"
)
_KNOWN_PROFILES = frozenset({"small", "medium", "large"})
_GPU_REQUIREMENTS = frozenset(
    {"unspecified", "required", "preferred", "forbidden"}
)
_DIR_FD_OPEN_SUPPORTED = os.open in os.supports_dir_fd


class SplitBundleValidationError(ValueError):
    """A Protocol-v5 split bundle violates its schema or checksum."""


class SplitRole(str, Enum):
    DEVELOPMENT = "development"
    CONFIRMATORY = "confirmatory"


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SplitBundleValidationError(f"{label} must be an object")
    return dict(value)


def _exact_mapping(
    value: object,
    fields: frozenset[str],
    label: str,
) -> dict[str, Any]:
    payload = _mapping(value, label)
    missing = sorted(fields - set(payload))
    # Do not sort attacker-controlled YAML keys: mixed key types are not valid
    # JSON and sorting them can raise an unsanitized TypeError before validation.
    extra = set(payload) - fields
    if missing:
        raise SplitBundleValidationError(
            f"{label} missing fields: {', '.join(missing)}"
        )
    if extra:
        raise SplitBundleValidationError(
            f"{label} contains {len(extra)} unexpected field(s)"
        )
    return payload


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SplitBundleValidationError(f"{label} must be a non-blank string")
    return value


def _safe_id(value: object, label: str) -> str:
    selected = _nonblank(value, label)
    if not _SAFE_ID.fullmatch(selected):
        raise SplitBundleValidationError(
            f"{label} must be a bounded filesystem-safe identifier"
        )
    return selected


def _timestamp(value: object, label: str) -> str:
    selected = _nonblank(value, label)
    if not _UTC_TIMESTAMP.fullmatch(selected):
        raise SplitBundleValidationError(
            f"{label} must be an ISO-8601 UTC timestamp ending in Z"
        )
    try:
        parsed = datetime.fromisoformat(selected.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SplitBundleValidationError(
            f"{label} must be an ISO-8601 UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise SplitBundleValidationError(f"{label} must use UTC")
    return selected


def _string_list(
    value: object,
    label: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise SplitBundleValidationError(f"{label} must be {qualifier}")
    selected = tuple(_nonblank(item, label) for item in value)
    if len(selected) != len(set(selected)):
        raise SplitBundleValidationError(f"{label} must not contain duplicates")
    return selected


def _finite_json_mapping(value: object, label: str) -> dict[str, Any]:
    payload = _mapping(value, label)
    active_containers: set[int] = set()

    def validate_json_value(selected: object, selected_label: str) -> None:
        if isinstance(selected, Mapping):
            identity = id(selected)
            if identity in active_containers:
                raise SplitBundleValidationError(
                    f"{selected_label} must not contain recursive data"
                )
            if not all(isinstance(key, str) for key in selected):
                raise SplitBundleValidationError(
                    f"{selected_label} keys must be strings"
                )
            active_containers.add(identity)
            try:
                for item in selected.values():
                    validate_json_value(item, f"{selected_label}.value")
            finally:
                active_containers.remove(identity)
            return
        if isinstance(selected, list):
            identity = id(selected)
            if identity in active_containers:
                raise SplitBundleValidationError(
                    f"{selected_label} must not contain recursive data"
                )
            active_containers.add(identity)
            try:
                for index, item in enumerate(selected):
                    validate_json_value(item, f"{selected_label}[{index}]")
            finally:
                active_containers.remove(identity)
            return
        if selected is None or isinstance(selected, (bool, int, str)):
            return
        if isinstance(selected, float) and math.isfinite(selected):
            return
        raise SplitBundleValidationError(
            f"{selected_label} must contain finite JSON data"
        )

    validate_json_value(payload, label)
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SplitBundleValidationError(
            f"{label} must contain finite JSON data"
        ) from exc
    return payload


def _nonnegative_number_or_none(value: object, label: str) -> int | float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise SplitBundleValidationError(
            f"{label} must be null or a finite non-negative number"
        )
    return value


@dataclass(frozen=True, slots=True)
class CreationMetadata:
    created_at_utc: str
    created_by: str

    _FIELDS = frozenset({"created_at_utc", "created_by"})

    @classmethod
    def from_dict(cls, value: object) -> "CreationMetadata":
        payload = _exact_mapping(value, cls._FIELDS, "creation_metadata")
        return cls(
            created_at_utc=_timestamp(
                payload["created_at_utc"], "creation_metadata.created_at_utc"
            ),
            created_by=_nonblank(
                payload["created_by"], "creation_metadata.created_by"
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "created_at_utc": self.created_at_utc,
            "created_by": self.created_by,
        }


@dataclass(frozen=True, slots=True)
class FreezeMetadata:
    frozen_at_utc: str
    frozen_by: str

    _FIELDS = frozenset({"frozen_at_utc", "frozen_by"})

    @classmethod
    def from_dict(cls, value: object) -> "FreezeMetadata":
        payload = _exact_mapping(value, cls._FIELDS, "freeze_metadata")
        return cls(
            frozen_at_utc=_timestamp(
                payload["frozen_at_utc"], "freeze_metadata.frozen_at_utc"
            ),
            frozen_by=_nonblank(payload["frozen_by"], "freeze_metadata.frozen_by"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "frozen_at_utc": self.frozen_at_utc,
            "frozen_by": self.frozen_by,
        }


@dataclass(frozen=True, slots=True)
class SplitManifest:
    dataset_id: str
    split_id: str
    role: SplitRole
    family_ids: tuple[str, ...]
    case_count: int
    family_count: int
    checksum: str
    creation_metadata: CreationMetadata
    freeze_metadata: FreezeMetadata

    _FIELDS = frozenset(
        {
            "dataset_id",
            "split_id",
            "role",
            "family_ids",
            "case_count",
            "family_count",
            "checksum",
            "creation_metadata",
            "freeze_metadata",
        }
    )

    @classmethod
    def from_dict(cls, value: object) -> "SplitManifest":
        payload = _exact_mapping(value, cls._FIELDS, "split_manifest")
        try:
            role = SplitRole(payload["role"])
        except (TypeError, ValueError) as exc:
            raise SplitBundleValidationError(
                "split_manifest.role must be development or confirmatory"
            ) from exc
        family_ids = _string_list(
            payload["family_ids"],
            "split_manifest.family_ids",
            allow_empty=False,
        )
        if tuple(sorted(family_ids)) != family_ids:
            raise SplitBundleValidationError(
                "split_manifest.family_ids must be sorted"
            )
        for family_id in family_ids:
            _safe_id(family_id, "split_manifest.family_ids")
        for field in ("case_count", "family_count"):
            count = payload[field]
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise SplitBundleValidationError(
                    f"split_manifest.{field} must be a positive integer"
                )
        checksum = payload["checksum"]
        if not isinstance(checksum, str) or not _SHA256.fullmatch(checksum):
            raise SplitBundleValidationError(
                "split_manifest.checksum must be a lowercase SHA-256 digest"
            )
        creation = CreationMetadata.from_dict(payload["creation_metadata"])
        freeze = FreezeMetadata.from_dict(payload["freeze_metadata"])
        created = datetime.fromisoformat(
            creation.created_at_utc.replace("Z", "+00:00")
        )
        frozen = datetime.fromisoformat(freeze.frozen_at_utc.replace("Z", "+00:00"))
        if frozen < created:
            raise SplitBundleValidationError(
                "freeze_metadata.frozen_at_utc cannot precede creation"
            )
        return cls(
            dataset_id=_safe_id(payload["dataset_id"], "split_manifest.dataset_id"),
            split_id=_safe_id(payload["split_id"], "split_manifest.split_id"),
            role=role,
            family_ids=family_ids,
            case_count=payload["case_count"],
            family_count=payload["family_count"],
            checksum=checksum,
            creation_metadata=creation,
            freeze_metadata=freeze,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "split_id": self.split_id,
            "role": self.role.value,
            "family_ids": list(self.family_ids),
            "case_count": self.case_count,
            "family_count": self.family_count,
            "checksum": self.checksum,
            "creation_metadata": self.creation_metadata.to_dict(),
            "freeze_metadata": self.freeze_metadata.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class SplitCase:
    case_id: str
    family_id: str
    variant_id: str
    language: str
    prompt: str
    inputs: Mapping[str, Any]
    gold: Mapping[str, Any]
    source_provenance: Mapping[str, Any]
    family_metadata: Mapping[str, Any] | None = None
    variant_metadata: Mapping[str, Any] | None = None

    _FIELDS = frozenset(
        {
            "case_id",
            "family_id",
            "variant_id",
            "language",
            "prompt",
            "inputs",
            "gold",
            "source_provenance",
        }
    )
    _INPUT_FIELDS = frozenset({"dataset_size_gb", "code_context_hints"})
    _GOLD_FIELDS = frozenset(
        {
            "request_feasible",
            "preferred_candidate_id",
            "acceptable_candidate_ids",
            "required_image_capabilities",
            "allowed_profiles",
            "gpu_allowed",
            "expected_extraction",
        }
    )
    _PROVENANCE_REQUIRED = frozenset(
        {
            "source_dataset_id",
            "source_schema_version",
            "source_case_id",
            "source_split",
            "evidence_classification",
        }
    )

    @classmethod
    def from_dict(cls, value: object, *, index: int) -> "SplitCase":
        label = f"cases[{index}]"
        payload = _exact_mapping(value, cls._FIELDS, label)
        inputs = _exact_mapping(payload["inputs"], cls._INPUT_FIELDS, f"{label}.inputs")
        size = inputs["dataset_size_gb"]
        if (
            isinstance(size, bool)
            or not isinstance(size, (int, float))
            or not math.isfinite(float(size))
            or size < 0
        ):
            raise SplitBundleValidationError(
                f"{label}.inputs.dataset_size_gb must be finite and non-negative"
            )
        hints = _string_list(
            inputs["code_context_hints"], f"{label}.inputs.code_context_hints"
        )

        gold = _exact_mapping(payload["gold"], cls._GOLD_FIELDS, f"{label}.gold")
        feasible = gold["request_feasible"]
        if not isinstance(feasible, bool):
            raise SplitBundleValidationError(
                f"{label}.gold.request_feasible must be boolean"
            )
        acceptable = _string_list(
            gold["acceptable_candidate_ids"],
            f"{label}.gold.acceptable_candidate_ids",
        )
        preferred = gold["preferred_candidate_id"]
        if preferred is not None:
            preferred = _safe_id(
                preferred, f"{label}.gold.preferred_candidate_id"
            )
        for candidate_id in acceptable:
            _safe_id(candidate_id, f"{label}.gold.acceptable_candidate_ids")
        if feasible and (not acceptable or preferred not in acceptable):
            raise SplitBundleValidationError(
                f"{label}.gold feasible case requires a preferred acceptable candidate"
            )
        if not feasible and (acceptable or preferred is not None):
            raise SplitBundleValidationError(
                f"{label}.gold infeasible case cannot define candidates"
            )
        capabilities = _string_list(
            gold["required_image_capabilities"],
            f"{label}.gold.required_image_capabilities",
        )
        profiles = _string_list(
            gold["allowed_profiles"],
            f"{label}.gold.allowed_profiles",
            allow_empty=False,
        )
        if not set(profiles).issubset(_KNOWN_PROFILES):
            raise SplitBundleValidationError(
                f"{label}.gold.allowed_profiles contains an unknown profile"
            )
        if not isinstance(gold["gpu_allowed"], bool):
            raise SplitBundleValidationError(
                f"{label}.gold.gpu_allowed must be boolean"
            )
        expected = gold["expected_extraction"]
        if expected is not None:
            expected_label = f"{label}.gold.expected_extraction"
            expected = _exact_mapping(
                expected,
                frozenset(
                    {
                        "gpu_requirement",
                        "minimum_cpu_cores",
                        "minimum_memory_gb",
                        "required_libraries",
                    }
                ),
                expected_label,
            )
            gpu_requirement = expected["gpu_requirement"]
            if gpu_requirement not in _GPU_REQUIREMENTS:
                raise SplitBundleValidationError(
                    f"{expected_label}.gpu_requirement is unsupported"
                )
            minimum_cpu_cores = _nonnegative_number_or_none(
                expected["minimum_cpu_cores"],
                f"{expected_label}.minimum_cpu_cores",
            )
            minimum_memory_gb = _nonnegative_number_or_none(
                expected["minimum_memory_gb"],
                f"{expected_label}.minimum_memory_gb",
            )
            required_libraries = _string_list(
                expected["required_libraries"],
                f"{expected_label}.required_libraries",
            )
            expected = {
                "gpu_requirement": gpu_requirement,
                "minimum_cpu_cores": minimum_cpu_cores,
                "minimum_memory_gb": minimum_memory_gb,
                "required_libraries": list(required_libraries),
            }

        provenance = _finite_json_mapping(
            payload["source_provenance"], f"{label}.source_provenance"
        )
        missing_provenance = sorted(cls._PROVENANCE_REQUIRED - set(provenance))
        if missing_provenance:
            raise SplitBundleValidationError(
                f"{label}.source_provenance missing fields: "
                + ", ".join(missing_provenance)
            )
        for field in cls._PROVENANCE_REQUIRED - {"evidence_classification"}:
            _safe_id(provenance[field], f"{label}.source_provenance.{field}")
        _nonblank(
            provenance["evidence_classification"],
            f"{label}.source_provenance.evidence_classification",
        )
        if "original_provenance" in provenance:
            provenance["original_provenance"] = _finite_json_mapping(
                provenance["original_provenance"],
                f"{label}.source_provenance.original_provenance",
            )

        language = _nonblank(payload["language"], f"{label}.language")
        if not _LANGUAGE.fullmatch(language):
            raise SplitBundleValidationError(
                f"{label}.language must be a valid language tag"
            )

        return cls(
            case_id=_safe_id(payload["case_id"], f"{label}.case_id"),
            family_id=_safe_id(payload["family_id"], f"{label}.family_id"),
            variant_id=_safe_id(payload["variant_id"], f"{label}.variant_id"),
            language=language,
            prompt=_nonblank(payload["prompt"], f"{label}.prompt"),
            inputs={
                "dataset_size_gb": size,
                "code_context_hints": list(hints),
            },
            gold={
                "request_feasible": feasible,
                "preferred_candidate_id": preferred,
                "acceptable_candidate_ids": list(acceptable),
                "required_image_capabilities": list(capabilities),
                "allowed_profiles": list(profiles),
                "gpu_allowed": gold["gpu_allowed"],
                "expected_extraction": expected,
            },
            source_provenance=provenance,
        )

    def to_dict(
        self,
        *,
        schema_version: str = SPLIT_BUNDLE_SCHEMA_VERSION,
    ) -> dict[str, Any]:
        payload = {
            "case_id": self.case_id,
            "family_id": self.family_id,
            "variant_id": self.variant_id,
            "language": self.language,
            "prompt": self.prompt,
            "inputs": dict(self.inputs),
            "gold": dict(self.gold),
            "source_provenance": dict(self.source_provenance),
        }
        if schema_version == SPLIT_BUNDLE_SCHEMA_VERSION_V2:
            if self.family_metadata is None or self.variant_metadata is None:
                raise SplitBundleValidationError(
                    "v2 split case requires family_metadata and variant_metadata"
                )
            payload["family_metadata"] = dict(self.family_metadata)
            payload["variant_metadata"] = dict(self.variant_metadata)
            # Preserve the v2 schema's declaration order for human-readable
            # YAML while checksum canonicalization remains key-order agnostic.
            payload = {
                "case_id": payload["case_id"],
                "family_id": payload["family_id"],
                "variant_id": payload["variant_id"],
                "language": payload["language"],
                "prompt": payload["prompt"],
                "inputs": payload["inputs"],
                "family_metadata": payload["family_metadata"],
                "variant_metadata": payload["variant_metadata"],
                "gold": payload["gold"],
                "source_provenance": payload["source_provenance"],
            }
        return payload


@dataclass(frozen=True, slots=True)
class SplitBundle:
    split_manifest: SplitManifest
    cases: tuple[SplitCase, ...]
    schema_version: str = SPLIT_BUNDLE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "split_manifest": self.split_manifest.to_dict(),
            "cases": [
                case.to_dict(schema_version=self.schema_version)
                for case in self.cases
            ],
        }


@dataclass(frozen=True, slots=True)
class LoadedSplit:
    bundle: SplitBundle
    source_file_sha256: str

    @property
    def manifest(self) -> SplitManifest:
        return self.bundle.split_manifest


def split_bundle_checksum(document: Mapping[str, Any] | SplitBundle) -> str:
    """Hash canonical parsed content while excluding only the checksum itself."""

    payload = document.to_dict() if isinstance(document, SplitBundle) else dict(document)
    root = _exact_mapping(
        payload,
        frozenset({"schema_version", "split_manifest", "cases"}),
        "split bundle",
    )
    manifest = _mapping(root["split_manifest"], "split_manifest")
    if "checksum" not in manifest:
        raise SplitBundleValidationError("split_manifest missing fields: checksum")
    manifest_without_checksum = {
        key: value for key, value in manifest.items() if key != "checksum"
    }
    canonical = {
        "schema_version": root["schema_version"],
        "split_manifest": manifest_without_checksum,
        "cases": root["cases"],
    }
    try:
        return canonical_sha256(_finite_json_mapping(canonical, "split bundle"))
    except (TypeError, ValueError) as exc:
        raise SplitBundleValidationError(
            "split bundle must contain finite JSON-compatible values"
        ) from exc


def validate_split_bundle(
    document: object,
    *,
    expected_role: SplitRole | None = None,
    expected_split_id: str | None = None,
    workload_manifests: Sequence[Path] = (),
) -> SplitBundle:
    """Validate a complete split bundle and its embedded canonical checksum."""

    root = _exact_mapping(
        document,
        frozenset({"schema_version", "split_manifest", "cases"}),
        "split bundle",
    )
    schema_version = root["schema_version"]
    if schema_version not in SUPPORTED_SPLIT_BUNDLE_SCHEMA_VERSIONS:
        raise SplitBundleValidationError("split bundle schema_version is unsupported")
    manifest = SplitManifest.from_dict(root["split_manifest"])
    if expected_role is not None and manifest.role is not expected_role:
        raise SplitBundleValidationError(
            f"split role mismatch: expected {expected_role.value}"
        )
    if expected_split_id is not None:
        _safe_id(expected_split_id, "expected_split_id")
        if manifest.split_id != expected_split_id:
            raise SplitBundleValidationError(
                f"split ID mismatch: expected {expected_split_id}"
            )
    raw_cases = root["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise SplitBundleValidationError("cases must be a non-empty list")
    if schema_version == SPLIT_BUNDLE_SCHEMA_VERSION:
        cases = tuple(
            SplitCase.from_dict(value, index=index)
            for index, value in enumerate(raw_cases)
        )
    else:
        from .gold_dataset import (
            GoldDatasetValidationError,
            validate_compiled_case,
        )

        normalized_cases: list[SplitCase] = []
        for index, value in enumerate(raw_cases):
            try:
                normalized = validate_compiled_case(
                    value,
                    index=index,
                    workload_manifests=workload_manifests,
                )
            except GoldDatasetValidationError as exc:
                raise SplitBundleValidationError(str(exc)) from exc
            normalized_cases.append(
                SplitCase(
                    case_id=normalized["case_id"],
                    family_id=normalized["family_id"],
                    variant_id=normalized["variant_id"],
                    language=normalized["language"],
                    prompt=normalized["prompt"],
                    inputs=normalized["inputs"],
                    gold=normalized["gold"],
                    source_provenance=normalized["source_provenance"],
                    family_metadata=normalized["family_metadata"],
                    variant_metadata=normalized["variant_metadata"],
                )
            )
        cases = tuple(normalized_cases)
        family_signatures: dict[str, str] = {}
        family_canonical_references: dict[str, int] = {}
        authoring_checksums: set[str] = set()
        for index, case in enumerate(cases):
            provenance = case.source_provenance
            if provenance["source_dataset_id"] != manifest.dataset_id:
                raise SplitBundleValidationError(
                    f"cases[{index}].source_provenance.source_dataset_id "
                    "does not match split_manifest.dataset_id"
                )
            if provenance["source_split"] != manifest.role.value:
                raise SplitBundleValidationError(
                    f"cases[{index}].source_provenance.source_split "
                    "does not match split_manifest.role"
                )
            authoring_checksums.add(str(provenance["authoring_canonical_sha256"]))
            family_signature = canonical_sha256(
                {
                    "family_metadata": case.family_metadata,
                    "gold": case.gold,
                    "source_provenance": {
                        key: nested
                        for key, nested in provenance.items()
                        if key != "source_case_id"
                    },
                }
            )
            existing = family_signatures.setdefault(case.family_id, family_signature)
            if existing != family_signature:
                raise SplitBundleValidationError(
                    f"cases[{index}] disagrees with another case in family "
                    f"{case.family_id!r}"
                )
            if case.variant_metadata["equivalence_status"] == "canonical_reference":
                family_canonical_references[case.family_id] = (
                    family_canonical_references.get(case.family_id, 0) + 1
                )
                if family_canonical_references[case.family_id] > 1:
                    raise SplitBundleValidationError(
                        f"family {case.family_id!r} has multiple canonical references"
                    )
        if len(authoring_checksums) != 1:
            raise SplitBundleValidationError(
                "v2 cases must share one authoring_canonical_sha256"
            )
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise SplitBundleValidationError("case IDs must be unique within a split")
    derived_families = tuple(sorted({case.family_id for case in cases}))
    if manifest.family_ids != derived_families:
        raise SplitBundleValidationError(
            "split_manifest.family_ids does not match case family IDs"
        )
    if manifest.case_count != len(cases):
        raise SplitBundleValidationError(
            "split_manifest.case_count does not match cases"
        )
    if manifest.family_count != len(derived_families):
        raise SplitBundleValidationError(
            "split_manifest.family_count does not match family_ids"
        )
    actual_checksum = split_bundle_checksum(root)
    if manifest.checksum != actual_checksum:
        raise SplitBundleValidationError(
            "split_manifest.checksum does not match canonical split content"
        )
    return SplitBundle(
        split_manifest=manifest,
        cases=cases,
        schema_version=schema_version,
    )


def _open_readonly_regular(path: Path, *, no_follow: bool) -> int:
    """Open a regular-file candidate without following path components."""

    read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if not no_follow:
        return os.open(path, read_flags)

    if (
        not path.is_absolute()
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
        or not _DIR_FD_OPEN_SUPPORTED
    ):
        raise SplitBundleValidationError(
            "split dataset cannot be opened with required path isolation"
        )

    anchor = Path(path.anchor)
    components = path.relative_to(anchor).parts
    if not components or any(component in {"", ".", ".."} for component in components):
        raise SplitBundleValidationError(
            "split dataset path is not a canonical absolute file path"
        )

    no_follow_flag = os.O_NOFOLLOW
    directory_flags = read_flags | os.O_DIRECTORY | no_follow_flag
    directory_fd = os.open(anchor, directory_flags)
    try:
        for component in components[:-1]:
            next_fd = os.open(
                component,
                directory_flags,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(
            components[-1],
            read_flags | no_follow_flag | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
    finally:
        os.close(directory_fd)


def _read_split_bundle(
    path: Path,
    *,
    expected_role: SplitRole,
    expected_split_id: str,
    no_follow: bool = False,
    workload_manifests: Sequence[Path] = (),
) -> LoadedSplit:
    """Read, hash, and parse one immutable byte snapshot from one descriptor."""

    try:
        descriptor = _open_readonly_regular(path, no_follow=no_follow)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise SplitBundleValidationError(
                    "split dataset must be a regular file"
                )
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                raw = handle.read()
        finally:
            os.close(descriptor)
        document = yaml.safe_load(raw.decode("utf-8"))
    except SplitBundleValidationError:
        raise
    except (OSError, RuntimeError, UnicodeDecodeError) as exc:
        raise SplitBundleValidationError(
            "split dataset could not be read safely"
        ) from exc
    except yaml.YAMLError as exc:
        raise SplitBundleValidationError("split dataset YAML is invalid") from exc
    bundle = validate_split_bundle(
        document,
        expected_role=expected_role,
        expected_split_id=expected_split_id,
        workload_manifests=workload_manifests,
    )
    return LoadedSplit(
        bundle=bundle,
        source_file_sha256=hashlib.sha256(raw).hexdigest(),
    )


def load_development_split(
    *,
    expected_split_id: str = DEFAULT_DEVELOPMENT_SPLIT_ID,
) -> LoadedSplit:
    """Load only the tracked Protocol-v5 development bundle."""

    if expected_split_id != DEFAULT_DEVELOPMENT_SPLIT_ID:
        raise SplitBundleValidationError(
            "no tracked development bundle is registered for that split ID"
        )
    return _read_split_bundle(
        DEFAULT_DEVELOPMENT_DATASET,
        expected_role=SplitRole.DEVELOPMENT,
        expected_split_id=expected_split_id,
    )


__all__ = [
    "DEFAULT_CONFIRMATORY_SPLIT_ID",
    "DEFAULT_DEVELOPMENT_DATASET",
    "DEFAULT_DEVELOPMENT_SPLIT_ID",
    "LoadedSplit",
    "SPLIT_BUNDLE_SCHEMA_VERSION",
    "SPLIT_BUNDLE_SCHEMA_VERSION_V2",
    "SUPPORTED_SPLIT_BUNDLE_SCHEMA_VERSIONS",
    "SplitBundle",
    "SplitBundleValidationError",
    "SplitCase",
    "SplitManifest",
    "SplitRole",
    "load_development_split",
    "split_bundle_checksum",
    "validate_split_bundle",
]
