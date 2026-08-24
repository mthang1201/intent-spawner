"""Human-authoring, review, migration, and compilation tools for Protocol-v5 gold data.

This module never creates workload text or declares semantic equivalence.  It
validates human-authored family records and can project an approved, manually
frozen authoring dataset into the flat split-bundle consumed by evaluators.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
import unicodedata

import yaml

from evaluation_v4.dataset import canonical_sha256
from recommender.candidate_corpus import (
    DEFAULT_PROFILE_DEFINITIONS,
    CandidateCorpus,
    CandidateDocument,
    load_candidate_corpus,
)
from recommender.models import GPURequirement, TaskType


ROOT = Path(__file__).resolve().parents[1]
GOLD_DATASET_SCHEMA_VERSION = "protocol-v5-gold-family-v1.0.0"
COMPILED_SPLIT_SCHEMA_VERSION = "protocol-v5-split-bundle-v2.0.0"
GOLD_REVIEW_SCHEMA_VERSION = "protocol-v5-gold-review-v1.0.0"
GOLD_SUMMARY_SCHEMA_VERSION = "protocol-v5-gold-summary-v1.0.0"
PROTOCOL_VERSION = "5.0.0"

DEFAULT_WORKLOAD_MANIFESTS = (
    ROOT / "benchmarks" / "workloads.yaml",
    ROOT / "benchmarks" / "workloads-v3.yaml",
)
DEFAULT_IMPORT_REVIEW_STRATA = (
    "boundary",
    "data_processing",
    "deep_learning",
    "light",
    "machine_learning",
    "numerical_computing",
    "policy",
)

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"
)

_ROLES = frozenset({"development", "confirmatory"})
_LIFECYCLES = frozenset({"draft", "reviewed", "frozen"})
_DIFFICULTIES = frozenset({"unassessed", "easy", "medium", "hard"})
_FEASIBILITIES = frozenset({"feasible", "infeasible", "ambiguous"})
_VARIANT_CLASSES = frozenset(
    {
        "canonical_en",
        "paraphrase_en",
        "vietnamese",
        "informal_or_noisy",
        "optional_code_context",
        "optional_ambiguity_variant",
        "other",
    }
)
_EQUIVALENCE_STATES = frozenset(
    {
        "canonical_reference",
        "reviewed_equivalent",
        "pending_review",
        "controlled_ambiguity",
    }
)
_REVIEW_STATES = frozenset({"pending", "approved"})
_GPU_SEMANTICS = frozenset(item.value for item in GPURequirement)
_TASK_TYPES = frozenset(item.value for item in TaskType)


class GoldDatasetValidationError(ValueError):
    """A family-oriented Protocol-v5 gold dataset is invalid."""


class GoldDatasetReviewError(GoldDatasetValidationError):
    """A dataset has unresolved human-review findings and cannot compile."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate and merge keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge" or key_node.value == "<<":
            raise GoldDatasetValidationError("YAML merge keys are not permitted")
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise GoldDatasetValidationError("YAML mapping keys must be scalar") from exc
        if duplicate:
            raise GoldDatasetValidationError(f"duplicate YAML field {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GoldDatasetValidationError(f"{label} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise GoldDatasetValidationError(f"{label} keys must be strings")
    return dict(value)


def _exact_mapping(
    value: object,
    fields: Iterable[str],
    label: str,
    *,
    optional_fields: Iterable[str] = (),
) -> dict[str, Any]:
    payload = _mapping(value, label)
    required = frozenset(fields)
    expected = required | frozenset(optional_fields)
    missing = sorted(required - set(payload))
    extra = sorted(set(payload) - expected)
    if missing:
        raise GoldDatasetValidationError(
            f"{label} missing fields: {', '.join(missing)}"
        )
    if extra:
        raise GoldDatasetValidationError(
            f"{label} unexpected fields: {', '.join(extra)}"
        )
    return payload


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GoldDatasetValidationError(f"{label} must be a non-blank string")
    return value


def _safe_id(value: object, label: str) -> str:
    selected = _nonblank(value, label)
    if not _SAFE_ID.fullmatch(selected):
        raise GoldDatasetValidationError(
            f"{label} must be a lowercase bounded identifier"
        )
    return selected


def _timestamp(value: object, label: str) -> str:
    selected = _nonblank(value, label)
    if not _UTC_TIMESTAMP.fullmatch(selected):
        raise GoldDatasetValidationError(
            f"{label} must be an ISO-8601 UTC timestamp ending in Z"
        )
    try:
        parsed = datetime.fromisoformat(selected.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GoldDatasetValidationError(f"{label} is not a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise GoldDatasetValidationError(f"{label} must use UTC")
    return selected


def _sha256(value: object, label: str) -> str:
    selected = _nonblank(value, label)
    if not _SHA256.fullmatch(selected):
        raise GoldDatasetValidationError(
            f"{label} must be a lowercase SHA-256 digest"
        )
    return selected


def _string_list(
    value: object,
    label: str,
    *,
    allow_empty: bool = True,
    identifiers: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise GoldDatasetValidationError(f"{label} must be {qualifier}")
    selected = tuple(
        (
            _safe_id(item, f"{label}[{index}]")
            if identifiers
            else _nonblank(item, f"{label}[{index}]")
        )
        for index, item in enumerate(value)
    )
    if len(selected) != len(set(selected)):
        raise GoldDatasetValidationError(f"{label} must not contain duplicates")
    return selected


def _optional_resource(value: object, label: str) -> int | float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise GoldDatasetValidationError(
            f"{label} must be null or a finite non-negative number"
        )
    # Preserve integer-versus-float representation because Protocol-v5
    # canonical checksums intentionally distinguish 1 from 1.0.
    return value


def _finite_json(value: object, label: str = "document") -> None:
    active: set[int] = set()

    def visit(item: object, path: str) -> None:
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in active:
                raise GoldDatasetValidationError(f"{path} contains recursive data")
            if not all(isinstance(key, str) for key in item):
                raise GoldDatasetValidationError(f"{path} keys must be strings")
            active.add(identity)
            try:
                for key, nested in item.items():
                    visit(nested, f"{path}.{key}")
            finally:
                active.remove(identity)
            return
        if isinstance(item, list):
            identity = id(item)
            if identity in active:
                raise GoldDatasetValidationError(f"{path} contains recursive data")
            active.add(identity)
            try:
                for index, nested in enumerate(item):
                    visit(nested, f"{path}[{index}]")
            finally:
                active.remove(identity)
            return
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float) and math.isfinite(item):
            return
        raise GoldDatasetValidationError(f"{path} must contain finite JSON data")

    visit(value, label)
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise GoldDatasetValidationError(
            f"{label} must contain finite JSON data"
        ) from exc


def _strict_json_loads(text: str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise GoldDatasetValidationError(f"duplicate JSON field {key!r}")
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise GoldDatasetValidationError(f"non-finite JSON number {value!r}")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except GoldDatasetValidationError:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        raise GoldDatasetValidationError("gold dataset JSON is invalid") from exc


def _strict_yaml_loads(text: str) -> object:
    try:
        # yaml.load delegates to get_single_data(), which rejects a second
        # document while still using the duplicate-key-safe constructor above.
        return yaml.load(text, Loader=_UniqueKeySafeLoader)
    except GoldDatasetValidationError:
        raise
    except (yaml.YAMLError, RuntimeError, RecursionError) as exc:
        raise GoldDatasetValidationError("gold dataset YAML is invalid") from exc


@dataclass(frozen=True, slots=True)
class Variant:
    variant_id: str
    variant_class: str
    language: str
    intent: str
    code_context: tuple[str, ...]
    equivalence_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "variant_class": self.variant_class,
            "language": self.language,
            "intent": self.intent,
            "code_context": list(self.code_context),
            "equivalence_status": self.equivalence_status,
        }


@dataclass(frozen=True, slots=True)
class WorkloadFamily:
    family_id: str
    title: str
    workload_stratum: str
    difficulty: str
    executable_workload_id: str | None
    gold_structured_intent: Mapping[str, Any]
    candidate_gold: Mapping[str, Any]
    profile_gold: Mapping[str, Any]
    image_gold: Mapping[str, Any]
    policy_gold: Mapping[str, Any]
    variants: tuple[Variant, ...]
    label_review: Mapping[str, Any]
    source_provenance: Mapping[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "title": self.title,
            "workload_stratum": self.workload_stratum,
            "difficulty": self.difficulty,
            "executable_workload_id": self.executable_workload_id,
            "gold_structured_intent": dict(self.gold_structured_intent),
            "candidate_gold": dict(self.candidate_gold),
            "profile_gold": dict(self.profile_gold),
            "image_gold": dict(self.image_gold),
            "policy_gold": dict(self.policy_gold),
            "variants": [variant.to_dict() for variant in self.variants],
            "label_review": dict(self.label_review),
            "source_provenance": (
                dict(self.source_provenance)
                if self.source_provenance is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class GoldDataset:
    dataset_metadata: Mapping[str, Any]
    catalog_identity: Mapping[str, Any]
    review_policy: Mapping[str, Any]
    families: tuple[WorkloadFamily, ...]
    schema_version: str = GOLD_DATASET_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dataset_metadata": dict(self.dataset_metadata),
            "catalog_identity": dict(self.catalog_identity),
            "review_policy": dict(self.review_policy),
            "families": [family.to_dict() for family in self.families],
        }


@dataclass(frozen=True, slots=True)
class LoadedGoldDataset:
    dataset: GoldDataset
    source_path: Path
    source_file_sha256: str
    source_canonical_sha256: str


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    severity: str
    code: str
    message: str
    family_ids: tuple[str, ...] = ()
    variant_ids: tuple[str, ...] = ()
    fingerprints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "family_ids": list(self.family_ids),
            "variant_ids": list(self.variant_ids),
            "fingerprints": list(self.fingerprints),
        }


@dataclass(frozen=True, slots=True)
class ReviewReport:
    dataset_id: str
    canonical_sha256: str
    findings: tuple[ReviewFinding, ...]
    schema_version: str = GOLD_REVIEW_SCHEMA_VERSION

    @property
    def blocking_findings(self) -> tuple[ReviewFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "blocking")

    @property
    def advisory_findings(self) -> tuple[ReviewFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "advisory")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "canonical_sha256": self.canonical_sha256,
            "blocking_count": len(self.blocking_findings),
            "advisory_count": len(self.advisory_findings),
            "findings": [item.to_dict() for item in self.findings],
        }


@dataclass(frozen=True, slots=True)
class _CatalogContext:
    corpus: CandidateCorpus
    candidates: Mapping[str, CandidateDocument]
    profiles: frozenset[str]
    images: Mapping[str, Mapping[str, Any]]
    workload_ids: frozenset[str]


def current_catalog_identity(corpus: CandidateCorpus | None = None) -> dict[str, str]:
    selected = load_candidate_corpus() if corpus is None else corpus
    return {
        "candidate_corpus_version": selected.corpus_version,
        "candidate_corpus_sha256": selected.corpus_checksum,
        "image_catalog_version": selected.source_image_catalog_version,
        "image_catalog_sha256": selected.source_image_catalog_checksum,
        "profile_catalog_sha256": selected.source_profile_catalog_checksum,
    }


def _load_workload_ids(paths: Sequence[Path]) -> frozenset[str]:
    identifiers: set[str] = set()
    for path in paths:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise GoldDatasetValidationError(
                f"workload manifest could not be loaded: {path}"
            ) from exc
        root = _mapping(document, f"workload manifest {path}")
        workloads = root.get("workloads")
        if not isinstance(workloads, list):
            raise GoldDatasetValidationError(
                f"workload manifest {path} must contain a workloads list"
            )
        for index, item in enumerate(workloads):
            payload = _mapping(item, f"{path}.workloads[{index}]")
            identifier = _safe_id(
                payload.get("workload_id"), f"{path}.workloads[{index}].workload_id"
            )
            if identifier in identifiers:
                raise GoldDatasetValidationError(
                    f"duplicate executable workload ID {identifier!r}"
                )
            identifiers.add(identifier)
    return frozenset(identifiers)


def _catalog_context(
    *,
    corpus: CandidateCorpus | None = None,
    workload_manifests: Sequence[Path] = (),
) -> _CatalogContext:
    selected = load_candidate_corpus() if corpus is None else corpus
    from recommender.rule_based import load_image_catalog

    image_catalog = load_image_catalog()
    paths = tuple(DEFAULT_WORKLOAD_MANIFESTS) + tuple(workload_manifests)
    return _CatalogContext(
        corpus=selected,
        candidates={candidate.candidate_id: candidate for candidate in selected},
        profiles=frozenset(DEFAULT_PROFILE_DEFINITIONS),
        images=dict(image_catalog["images"]),
        workload_ids=_load_workload_ids(paths),
    )


def _validate_catalog_identity(
    value: object,
    context: _CatalogContext,
    label: str = "catalog_identity",
) -> dict[str, str]:
    fields = (
        "candidate_corpus_version",
        "candidate_corpus_sha256",
        "image_catalog_version",
        "image_catalog_sha256",
        "profile_catalog_sha256",
    )
    payload = _exact_mapping(value, fields, label)
    selected = {
        "candidate_corpus_version": _nonblank(
            payload["candidate_corpus_version"],
            f"{label}.candidate_corpus_version",
        ),
        "candidate_corpus_sha256": _sha256(
            payload["candidate_corpus_sha256"],
            f"{label}.candidate_corpus_sha256",
        ),
        "image_catalog_version": _nonblank(
            payload["image_catalog_version"], f"{label}.image_catalog_version"
        ),
        "image_catalog_sha256": _sha256(
            payload["image_catalog_sha256"], f"{label}.image_catalog_sha256"
        ),
        "profile_catalog_sha256": _sha256(
            payload["profile_catalog_sha256"], f"{label}.profile_catalog_sha256"
        ),
    }
    expected = current_catalog_identity(context.corpus)
    for field in fields:
        if selected[field] != expected[field]:
            raise GoldDatasetValidationError(
                f"{label}.{field} does not match administrator-owned configuration"
            )
    return selected


def _validate_source_dataset(value: object, label: str) -> dict[str, str]:
    payload = _exact_mapping(
        value,
        {"dataset_id", "schema_version", "source_file_sha256", "source_split"},
        label,
    )
    return {
        "dataset_id": _safe_id(payload["dataset_id"], f"{label}.dataset_id"),
        "schema_version": _safe_id(
            payload["schema_version"], f"{label}.schema_version"
        ),
        "source_file_sha256": _sha256(
            payload["source_file_sha256"], f"{label}.source_file_sha256"
        ),
        "source_split": _safe_id(
            payload["source_split"], f"{label}.source_split"
        ),
    }


def _validate_metadata(value: object) -> dict[str, Any]:
    payload = _exact_mapping(
        value,
        {
            "dataset_id",
            "protocol_version",
            "role",
            "lifecycle",
            "created_at_utc",
            "created_by",
            "git_revision",
            "evidence_classification",
            "freeze_metadata",
            "source_datasets",
        },
        "dataset_metadata",
    )
    role = payload["role"]
    if role not in _ROLES:
        raise GoldDatasetValidationError(
            "dataset_metadata.role must be development or confirmatory"
        )
    if payload["protocol_version"] != PROTOCOL_VERSION:
        raise GoldDatasetValidationError(
            "dataset_metadata.protocol_version is unsupported"
        )
    lifecycle = payload["lifecycle"]
    if lifecycle not in _LIFECYCLES:
        raise GoldDatasetValidationError(
            "dataset_metadata.lifecycle must be draft, reviewed, or frozen"
        )
    git_revision = payload["git_revision"]
    if not isinstance(git_revision, str) or not _GIT_REVISION.fullmatch(git_revision):
        raise GoldDatasetValidationError(
            "dataset_metadata.git_revision must be a full lowercase Git revision"
        )
    raw_sources = payload["source_datasets"]
    if not isinstance(raw_sources, list):
        raise GoldDatasetValidationError("dataset_metadata.source_datasets must be a list")
    sources = [
        _validate_source_dataset(item, f"dataset_metadata.source_datasets[{index}]")
        for index, item in enumerate(raw_sources)
    ]
    source_keys = [(item["dataset_id"], item["source_split"]) for item in sources]
    if len(source_keys) != len(set(source_keys)):
        raise GoldDatasetValidationError(
            "dataset_metadata.source_datasets must not contain duplicates"
        )
    freeze = payload["freeze_metadata"]
    if freeze is None:
        if lifecycle == "frozen":
            raise GoldDatasetValidationError(
                "frozen dataset requires dataset_metadata.freeze_metadata"
            )
        normalized_freeze = None
    else:
        freeze_payload = _exact_mapping(
            freeze, {"frozen_at_utc", "frozen_by"}, "dataset_metadata.freeze_metadata"
        )
        normalized_freeze = {
            "frozen_at_utc": _timestamp(
                freeze_payload["frozen_at_utc"],
                "dataset_metadata.freeze_metadata.frozen_at_utc",
            ),
            "frozen_by": _nonblank(
                freeze_payload["frozen_by"],
                "dataset_metadata.freeze_metadata.frozen_by",
            ),
        }
        if lifecycle != "frozen":
            raise GoldDatasetValidationError(
                "freeze_metadata is only valid for a frozen dataset"
            )
    created = _timestamp(payload["created_at_utc"], "dataset_metadata.created_at_utc")
    if normalized_freeze is not None:
        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        frozen_dt = datetime.fromisoformat(
            normalized_freeze["frozen_at_utc"].replace("Z", "+00:00")
        )
        if frozen_dt < created_dt:
            raise GoldDatasetValidationError("dataset freeze cannot precede creation")
    return {
        "dataset_id": _safe_id(payload["dataset_id"], "dataset_metadata.dataset_id"),
        "protocol_version": PROTOCOL_VERSION,
        "role": role,
        "lifecycle": lifecycle,
        "created_at_utc": created,
        "created_by": _nonblank(payload["created_by"], "dataset_metadata.created_by"),
        "git_revision": git_revision,
        "evidence_classification": _safe_id(
            payload["evidence_classification"],
            "dataset_metadata.evidence_classification",
        ),
        "freeze_metadata": normalized_freeze,
        "source_datasets": sources,
    }


def _validate_review_policy(value: object) -> dict[str, Any]:
    payload = _exact_mapping(
        value,
        {
            "required_workload_strata",
            "max_preferred_profile_share",
            "max_preferred_image_share",
        },
        "review_policy",
    )
    strata = _string_list(
        payload["required_workload_strata"],
        "review_policy.required_workload_strata",
        identifiers=True,
    )
    normalized: dict[str, Any] = {"required_workload_strata": list(strata)}
    for field in ("max_preferred_profile_share", "max_preferred_image_share"):
        selected = payload[field]
        if (
            isinstance(selected, bool)
            or not isinstance(selected, (int, float))
            or not math.isfinite(float(selected))
            or not 0 < float(selected) <= 1
        ):
            raise GoldDatasetValidationError(
                f"review_policy.{field} must be finite and in (0, 1]"
            )
        normalized[field] = float(selected)
    return normalized


def _validate_structured_intent(value: object, label: str) -> dict[str, Any]:
    fields = {
        "task_types",
        "required_features",
        "preferred_features",
        "forbidden_features",
        "required_frameworks",
        "preferred_frameworks",
        "gpu_semantics",
        "minimum_cpu_cores",
        "minimum_memory_gb",
        "dataset_size_gb",
        "ambiguities",
    }
    payload = _exact_mapping(value, fields, label)
    task_types = _string_list(payload["task_types"], f"{label}.task_types", identifiers=True)
    unknown_tasks = sorted(set(task_types) - _TASK_TYPES)
    if unknown_tasks:
        raise GoldDatasetValidationError(
            f"{label}.task_types contains unsupported values: {', '.join(unknown_tasks)}"
        )
    identifier_fields = (
        "required_features",
        "preferred_features",
        "forbidden_features",
        "required_frameworks",
        "preferred_frameworks",
    )
    normalized: dict[str, Any] = {"task_types": list(task_types)}
    for field in identifier_fields:
        normalized[field] = list(
            _string_list(payload[field], f"{label}.{field}", identifiers=True)
        )
    required = set(normalized["required_features"])
    preferred = set(normalized["preferred_features"])
    forbidden = set(normalized["forbidden_features"])
    if required & forbidden:
        raise GoldDatasetValidationError(
            f"{label} features cannot be both required and forbidden"
        )
    if preferred & (required | forbidden):
        raise GoldDatasetValidationError(
            f"{label}.preferred_features overlaps required or forbidden features"
        )
    if set(normalized["preferred_frameworks"]) & set(normalized["required_frameworks"]):
        raise GoldDatasetValidationError(
            f"{label}.preferred_frameworks overlaps required_frameworks"
        )
    gpu = payload["gpu_semantics"]
    if gpu not in _GPU_SEMANTICS:
        raise GoldDatasetValidationError(f"{label}.gpu_semantics is unsupported")
    normalized.update(
        {
            "gpu_semantics": gpu,
            "minimum_cpu_cores": _optional_resource(
                payload["minimum_cpu_cores"], f"{label}.minimum_cpu_cores"
            ),
            "minimum_memory_gb": _optional_resource(
                payload["minimum_memory_gb"], f"{label}.minimum_memory_gb"
            ),
            "dataset_size_gb": _optional_resource(
                payload["dataset_size_gb"], f"{label}.dataset_size_gb"
            ),
            "ambiguities": list(
                _string_list(payload["ambiguities"], f"{label}.ambiguities")
            ),
        }
    )
    return normalized


def _validate_id_gold(
    value: object,
    label: str,
    preferred_field: str,
    acceptable_field: str,
) -> dict[str, Any]:
    payload = _exact_mapping(value, {preferred_field, acceptable_field}, label)
    preferred = _string_list(
        payload[preferred_field], f"{label}.{preferred_field}", identifiers=True
    )
    acceptable = _string_list(
        payload[acceptable_field], f"{label}.{acceptable_field}", identifiers=True
    )
    if not set(preferred).issubset(acceptable):
        raise GoldDatasetValidationError(
            f"{label}.{preferred_field} must be a subset of {acceptable_field}"
        )
    return {preferred_field: list(preferred), acceptable_field: list(acceptable)}


def _validate_image_gold(value: object, label: str) -> dict[str, Any]:
    payload = _exact_mapping(
        value,
        {"preferred_image_ids", "acceptable_image_ids", "required_capabilities"},
        label,
    )
    normalized = _validate_id_gold(
        {
            "preferred_image_ids": payload["preferred_image_ids"],
            "acceptable_image_ids": payload["acceptable_image_ids"],
        },
        label,
        "preferred_image_ids",
        "acceptable_image_ids",
    )
    normalized["required_capabilities"] = list(
        _string_list(
            payload["required_capabilities"],
            f"{label}.required_capabilities",
            identifiers=True,
        )
    )
    return normalized


def _validate_policy_gold(value: object, label: str) -> dict[str, Any]:
    payload = _exact_mapping(
        value,
        {
            "required_constraints",
            "explicitly_unsupported_requirements",
            "expected_feasibility",
        },
        label,
    )
    feasibility = payload["expected_feasibility"]
    if feasibility not in _FEASIBILITIES:
        raise GoldDatasetValidationError(f"{label}.expected_feasibility is unsupported")
    return {
        "required_constraints": list(
            _string_list(
                payload["required_constraints"], f"{label}.required_constraints"
            )
        ),
        "explicitly_unsupported_requirements": list(
            _string_list(
                payload["explicitly_unsupported_requirements"],
                f"{label}.explicitly_unsupported_requirements",
            )
        ),
        "expected_feasibility": feasibility,
    }


def _validate_variant(value: object, label: str) -> Variant:
    payload = _exact_mapping(
        value,
        {
            "variant_id",
            "variant_class",
            "language",
            "intent",
            "equivalence_status",
        },
        label,
        optional_fields={"code_context"},
    )
    variant_class = payload["variant_class"]
    if variant_class not in _VARIANT_CLASSES:
        raise GoldDatasetValidationError(f"{label}.variant_class is unsupported")
    language = _nonblank(payload["language"], f"{label}.language")
    if not _LANGUAGE.fullmatch(language):
        raise GoldDatasetValidationError(f"{label}.language is not a valid language tag")
    if variant_class in {"canonical_en", "paraphrase_en"} and language.lower() != "en":
        raise GoldDatasetValidationError(
            f"{label}.{variant_class} must use language en"
        )
    if variant_class == "vietnamese" and language.lower() != "vi":
        raise GoldDatasetValidationError(f"{label}.vietnamese must use language vi")
    equivalence = payload["equivalence_status"]
    if equivalence not in _EQUIVALENCE_STATES:
        raise GoldDatasetValidationError(f"{label}.equivalence_status is unsupported")
    return Variant(
        variant_id=_safe_id(payload["variant_id"], f"{label}.variant_id"),
        variant_class=variant_class,
        language=language,
        intent=_nonblank(payload["intent"], f"{label}.intent"),
        code_context=_string_list(
            payload.get("code_context", []), f"{label}.code_context"
        ),
        equivalence_status=equivalence,
    )


def _validate_label_review(value: object, label: str) -> dict[str, Any]:
    payload = _exact_mapping(
        value, {"status", "reviewed_by", "reviewed_at_utc", "notes"}, label
    )
    status = payload["status"]
    if status not in _REVIEW_STATES:
        raise GoldDatasetValidationError(f"{label}.status must be pending or approved")
    notes = _string_list(
        payload["notes"], f"{label}.notes", allow_empty=status == "pending"
    )
    if status == "pending":
        if payload["reviewed_by"] is not None or payload["reviewed_at_utc"] is not None:
            raise GoldDatasetValidationError(
                f"{label} pending review cannot identify a completed reviewer"
            )
        reviewer = None
        reviewed_at = None
    else:
        reviewer = _nonblank(payload["reviewed_by"], f"{label}.reviewed_by")
        reviewed_at = _timestamp(
            payload["reviewed_at_utc"], f"{label}.reviewed_at_utc"
        )
    return {
        "status": status,
        "reviewed_by": reviewer,
        "reviewed_at_utc": reviewed_at,
        "notes": list(notes),
    }


def _validate_source_provenance(value: object, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    payload = _exact_mapping(
        value,
        {
            "source_dataset_id",
            "source_schema_version",
            "source_family_id",
            "source_case_ids",
            "source_split",
            "source_file_sha256",
            "evidence_classification",
            "original_label_sha256",
        },
        label,
    )
    return {
        "source_dataset_id": _safe_id(
            payload["source_dataset_id"], f"{label}.source_dataset_id"
        ),
        "source_schema_version": _safe_id(
            payload["source_schema_version"], f"{label}.source_schema_version"
        ),
        "source_family_id": _safe_id(
            payload["source_family_id"], f"{label}.source_family_id"
        ),
        "source_case_ids": list(
            _string_list(
                payload["source_case_ids"],
                f"{label}.source_case_ids",
                allow_empty=False,
                identifiers=True,
            )
        ),
        "source_split": _safe_id(payload["source_split"], f"{label}.source_split"),
        "source_file_sha256": _sha256(
            payload["source_file_sha256"], f"{label}.source_file_sha256"
        ),
        "evidence_classification": _safe_id(
            payload["evidence_classification"],
            f"{label}.evidence_classification",
        ),
        "original_label_sha256": _sha256(
            payload["original_label_sha256"], f"{label}.original_label_sha256"
        ),
    }


def _candidate_features(candidate: CandidateDocument) -> set[str]:
    return (
        set(candidate.capabilities)
        | set(candidate.frameworks)
        | set(candidate.libraries)
        | set(candidate.suitability_tags)
        | set(candidate.preference_tags)
    )


def _candidate_satisfies(
    candidate: CandidateDocument,
    structured: Mapping[str, Any],
    image_gold: Mapping[str, Any],
) -> bool:
    features = _candidate_features(candidate)
    if not set(structured["required_features"]).issubset(features):
        return False
    if set(structured["forbidden_features"]) & features:
        return False
    framework_space = (
        set(candidate.frameworks)
        | set(candidate.libraries)
        | set(candidate.capabilities)
    )
    if not set(structured["required_frameworks"]).issubset(framework_space):
        return False
    if not set(image_gold["required_capabilities"]).issubset(candidate.capabilities):
        return False
    cpu = structured["minimum_cpu_cores"]
    if cpu is not None and candidate.resource_metadata.cpu_limit_cores < cpu:
        return False
    memory = structured["minimum_memory_gb"]
    if memory is not None and candidate.resource_metadata.memory_limit_gb < memory:
        return False
    gpu = structured["gpu_semantics"]
    if gpu == GPURequirement.REQUIRED.value and candidate.resource_metadata.gpu_count < 1:
        return False
    if gpu == GPURequirement.FORBIDDEN.value and candidate.resource_metadata.gpu_count != 0:
        return False
    return True


def _validate_family(
    value: object,
    *,
    index: int,
    context: _CatalogContext,
) -> WorkloadFamily:
    label = f"families[{index}]"
    payload = _exact_mapping(
        value,
        {
            "family_id",
            "title",
            "workload_stratum",
            "difficulty",
            "gold_structured_intent",
            "candidate_gold",
            "profile_gold",
            "image_gold",
            "policy_gold",
            "variants",
            "label_review",
        },
        label,
        optional_fields={"executable_workload_id", "source_provenance"},
    )
    family_id = _safe_id(payload["family_id"], f"{label}.family_id")
    difficulty = payload["difficulty"]
    if difficulty not in _DIFFICULTIES:
        raise GoldDatasetValidationError(f"{label}.difficulty is unsupported")
    executable = payload.get("executable_workload_id")
    if executable is not None:
        executable = _safe_id(executable, f"{label}.executable_workload_id")
        if executable not in context.workload_ids:
            raise GoldDatasetValidationError(
                f"{label}.executable_workload_id does not resolve"
            )
    structured = _validate_structured_intent(
        payload["gold_structured_intent"], f"{label}.gold_structured_intent"
    )
    candidate_gold = _validate_id_gold(
        payload["candidate_gold"],
        f"{label}.candidate_gold",
        "preferred_candidate_ids",
        "acceptable_candidate_ids",
    )
    profile_gold = _validate_id_gold(
        payload["profile_gold"],
        f"{label}.profile_gold",
        "preferred_profile_ids",
        "acceptable_profile_ids",
    )
    image_gold = _validate_image_gold(payload["image_gold"], f"{label}.image_gold")
    policy_gold = _validate_policy_gold(payload["policy_gold"], f"{label}.policy_gold")

    candidate_ids = set(candidate_gold["acceptable_candidate_ids"])
    preferred_candidate_ids = set(candidate_gold["preferred_candidate_ids"])
    unknown_candidates = sorted(candidate_ids - set(context.candidates))
    if unknown_candidates:
        raise GoldDatasetValidationError(
            f"{label}.candidate_gold references unknown candidates: "
            + ", ".join(unknown_candidates)
        )
    profile_ids = set(profile_gold["acceptable_profile_ids"])
    preferred_profile_ids = set(profile_gold["preferred_profile_ids"])
    unknown_profiles = sorted(profile_ids - context.profiles)
    if unknown_profiles:
        raise GoldDatasetValidationError(
            f"{label}.profile_gold references unknown profiles: {', '.join(unknown_profiles)}"
        )
    image_ids = set(image_gold["acceptable_image_ids"])
    preferred_image_ids = set(image_gold["preferred_image_ids"])
    unknown_images = sorted(image_ids - set(context.images))
    if unknown_images:
        raise GoldDatasetValidationError(
            f"{label}.image_gold references unknown images: {', '.join(unknown_images)}"
        )

    for candidate_id in sorted(candidate_ids):
        candidate = context.candidates[candidate_id]
        if candidate.profile_id not in profile_ids or candidate.image_id not in image_ids:
            raise GoldDatasetValidationError(
                f"{label} acceptable candidate {candidate_id!r} disagrees with profile/image gold"
            )
        if not _candidate_satisfies(candidate, structured, image_gold):
            raise GoldDatasetValidationError(
                f"{label} acceptable candidate {candidate_id!r} violates hard gold constraints"
            )
    for candidate_id in sorted(preferred_candidate_ids):
        candidate = context.candidates[candidate_id]
        if (
            candidate.profile_id not in preferred_profile_ids
            or candidate.image_id not in preferred_image_ids
        ):
            raise GoldDatasetValidationError(
                f"{label} preferred candidate {candidate_id!r} disagrees with "
                "preferred profile/image gold"
            )

    if candidate_ids:
        candidate_profiles = {context.candidates[item].profile_id for item in candidate_ids}
        candidate_images = {context.candidates[item].image_id for item in candidate_ids}
        preferred_profiles = {
            context.candidates[item].profile_id for item in preferred_candidate_ids
        }
        preferred_images = {
            context.candidates[item].image_id for item in preferred_candidate_ids
        }
        if candidate_profiles != profile_ids or candidate_images != image_ids:
            raise GoldDatasetValidationError(
                f"{label} acceptable profile/image gold must equal candidate projections"
            )
        if preferred_profiles != preferred_profile_ids or preferred_images != preferred_image_ids:
            raise GoldDatasetValidationError(
                f"{label} preferred profile/image gold must equal candidate projections"
            )

    feasibility = policy_gold["expected_feasibility"]
    unsupported = policy_gold["explicitly_unsupported_requirements"]
    satisfiable = any(
        _candidate_satisfies(candidate, structured, image_gold)
        for candidate in context.corpus
    )
    if feasibility == "feasible":
        if not candidate_ids or not preferred_candidate_ids:
            raise GoldDatasetValidationError(
                f"{label} feasible gold requires acceptable and preferred candidates"
            )
        if unsupported:
            raise GoldDatasetValidationError(
                f"{label} feasible gold cannot mark unsupported requirements"
            )
        if not satisfiable:
            raise GoldDatasetValidationError(
                f"{label} hard constraints are impossible in the frozen catalog"
            )
    elif feasibility == "infeasible":
        if candidate_ids or preferred_candidate_ids:
            raise GoldDatasetValidationError(
                f"{label} infeasible gold cannot define candidates"
            )
        if preferred_profile_ids or preferred_image_ids:
            raise GoldDatasetValidationError(
                f"{label} infeasible gold cannot define preferred profiles/images"
            )
        if not unsupported:
            raise GoldDatasetValidationError(
                f"{label} infeasible gold must explicitly mark unsupported requirements"
            )
    else:
        if not structured["ambiguities"]:
            raise GoldDatasetValidationError(
                f"{label} ambiguous feasibility requires structured ambiguities"
            )

    raw_variants = payload["variants"]
    if not isinstance(raw_variants, list) or not raw_variants:
        raise GoldDatasetValidationError(f"{label}.variants must be a non-empty list")
    variants = tuple(
        _validate_variant(item, f"{label}.variants[{variant_index}]")
        for variant_index, item in enumerate(raw_variants)
    )
    local_ids = [item.variant_id for item in variants]
    if len(local_ids) != len(set(local_ids)):
        raise GoldDatasetValidationError(f"{label}.variant IDs must be unique")
    if sum(item.equivalence_status == "canonical_reference" for item in variants) > 1:
        raise GoldDatasetValidationError(
            f"{label} cannot contain more than one canonical reference"
        )

    return WorkloadFamily(
        family_id=family_id,
        title=_nonblank(payload["title"], f"{label}.title"),
        workload_stratum=_safe_id(
            payload["workload_stratum"], f"{label}.workload_stratum"
        ),
        difficulty=difficulty,
        executable_workload_id=executable,
        gold_structured_intent=structured,
        candidate_gold=candidate_gold,
        profile_gold=profile_gold,
        image_gold=image_gold,
        policy_gold=policy_gold,
        variants=variants,
        label_review=_validate_label_review(
            payload["label_review"], f"{label}.label_review"
        ),
        source_provenance=_validate_source_provenance(
            payload.get("source_provenance"), f"{label}.source_provenance"
        ),
    )


def validate_gold_dataset(
    document: object,
    *,
    workload_manifests: Sequence[Path] = (),
) -> GoldDataset:
    """Validate a complete family-oriented dataset against administrator data."""

    _finite_json(document, "gold dataset")
    root = _exact_mapping(
        document,
        {"schema_version", "dataset_metadata", "catalog_identity", "review_policy", "families"},
        "gold dataset",
    )
    if root["schema_version"] != GOLD_DATASET_SCHEMA_VERSION:
        raise GoldDatasetValidationError("gold dataset schema_version is unsupported")
    context = _catalog_context(workload_manifests=workload_manifests)
    metadata = _validate_metadata(root["dataset_metadata"])
    catalog_identity = _validate_catalog_identity(root["catalog_identity"], context)
    review_policy = _validate_review_policy(root["review_policy"])
    raw_families = root["families"]
    if not isinstance(raw_families, list) or not raw_families:
        raise GoldDatasetValidationError("gold dataset families must be a non-empty list")
    families = tuple(
        _validate_family(item, index=index, context=context)
        for index, item in enumerate(raw_families)
    )
    family_ids = [item.family_id for item in families]
    if len(family_ids) != len(set(family_ids)):
        raise GoldDatasetValidationError("family IDs must be globally unique")
    variant_ids = [variant.variant_id for family in families for variant in family.variants]
    if len(variant_ids) != len(set(variant_ids)):
        raise GoldDatasetValidationError("variant IDs must be globally unique")
    return GoldDataset(
        dataset_metadata=metadata,
        catalog_identity=catalog_identity,
        review_policy=review_policy,
        families=families,
    )


def load_gold_dataset(
    path: Path,
    *,
    workload_manifests: Sequence[Path] = (),
) -> LoadedGoldDataset:
    """Strictly read and validate one YAML or JSON authoring dataset."""

    selected = Path(path)
    if selected.suffix.lower() not in {".yaml", ".yml", ".json"}:
        raise GoldDatasetValidationError("gold dataset must use .yaml, .yml, or .json")
    try:
        if not selected.is_file():
            raise FileNotFoundError(selected)
        raw = selected.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GoldDatasetValidationError("gold dataset could not be read as UTF-8") from exc
    document = (
        _strict_json_loads(text)
        if selected.suffix.lower() == ".json"
        else _strict_yaml_loads(text)
    )
    _finite_json(document, "gold dataset")
    dataset = validate_gold_dataset(
        document, workload_manifests=workload_manifests
    )
    return LoadedGoldDataset(
        dataset=dataset,
        # Preserve the caller's lexical path so confirmatory compilation can
        # require an explicitly absolute path and reject repository symlinks.
        source_path=selected,
        source_file_sha256=hashlib.sha256(raw).hexdigest(),
        source_canonical_sha256=canonical_sha256(dataset.to_dict()),
    )


def _dataset(value: GoldDataset | LoadedGoldDataset) -> GoldDataset:
    return value.dataset if isinstance(value, LoadedGoldDataset) else value


def summarize_gold_dataset(
    value: GoldDataset | LoadedGoldDataset,
) -> dict[str, Any]:
    """Return a deterministic family-aware coverage summary."""

    dataset = _dataset(value)
    strata_families: Counter[str] = Counter()
    strata_cases: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    difficulties: Counter[str] = Counter()
    feasibilities: Counter[str] = Counter()
    capabilities: Counter[str] = Counter()
    preferred_profiles: Counter[str] = Counter()
    acceptable_profiles: Counter[str] = Counter()
    preferred_images: Counter[str] = Counter()
    acceptable_images: Counter[str] = Counter()
    perturbation_cases: Counter[str] = Counter()
    perturbation_families: dict[str, set[str]] = defaultdict(set)
    for family in dataset.families:
        case_count = len(family.variants)
        strata_families[family.workload_stratum] += 1
        strata_cases[family.workload_stratum] += case_count
        difficulties[family.difficulty] += 1
        feasibilities[str(family.policy_gold["expected_feasibility"])] += 1
        for capability in family.image_gold["required_capabilities"]:
            capabilities[str(capability)] += 1
        for profile_id in family.profile_gold["preferred_profile_ids"]:
            preferred_profiles[str(profile_id)] += 1
        for profile_id in family.profile_gold["acceptable_profile_ids"]:
            acceptable_profiles[str(profile_id)] += 1
        for image_id in family.image_gold["preferred_image_ids"]:
            preferred_images[str(image_id)] += 1
        for image_id in family.image_gold["acceptable_image_ids"]:
            acceptable_images[str(image_id)] += 1
        for variant in family.variants:
            languages[variant.language] += 1
            perturbation_cases[variant.variant_class] += 1
            perturbation_families[variant.variant_class].add(family.family_id)
    strata = {
        key: {
            "family_count": strata_families[key],
            "case_count": strata_cases[key],
        }
        for key in sorted(strata_families)
    }
    perturbations = {
        key: {
            "case_count": perturbation_cases[key],
            "family_count": len(perturbation_families[key]),
        }
        for key in sorted(perturbation_cases)
    }
    return {
        "schema_version": GOLD_SUMMARY_SCHEMA_VERSION,
        "dataset_id": dataset.dataset_metadata["dataset_id"],
        "canonical_sha256": canonical_sha256(dataset.to_dict()),
        "family_count": len(dataset.families),
        "case_count": sum(len(family.variants) for family in dataset.families),
        "language_counts": dict(sorted(languages.items())),
        "workload_strata": strata,
        "difficulty_distribution": dict(sorted(difficulties.items())),
        "feasibility_distribution": dict(sorted(feasibilities.items())),
        "capability_coverage": dict(sorted(capabilities.items())),
        "profile_coverage": {
            "preferred": dict(sorted(preferred_profiles.items())),
            "acceptable": dict(sorted(acceptable_profiles.items())),
        },
        "image_coverage": {
            "preferred": dict(sorted(preferred_images.items())),
            "acceptable": dict(sorted(acceptable_images.items())),
        },
        "perturbation_coverage": perturbations,
    }


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    characters = (
        " " if unicodedata.category(character).startswith("P") else character
        for character in normalized
    )
    return " ".join("".join(characters).split())


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _imbalance_findings(
    counter: Counter[str],
    *,
    threshold: float,
    code: str,
    noun: str,
) -> list[ReviewFinding]:
    total = sum(counter.values())
    if not total:
        return []
    findings: list[ReviewFinding] = []
    for identifier, count in sorted(counter.items()):
        share = count / total
        if share > threshold:
            findings.append(
                ReviewFinding(
                    severity="advisory",
                    code=code,
                    message=(
                        f"Preferred {noun} {identifier!r} has share {share:.3f}, "
                        f"above configured maximum {threshold:.3f}."
                    ),
                )
            )
    return findings


def review_gold_dataset(value: GoldDataset | LoadedGoldDataset) -> ReviewReport:
    """Return redaction-safe blocking and advisory human-review findings."""

    dataset = _dataset(value)
    findings: list[ReviewFinding] = []
    strata: Counter[str] = Counter(family.workload_stratum for family in dataset.families)
    preferred_profiles: Counter[str] = Counter()
    preferred_images: Counter[str] = Counter()
    variants: list[tuple[WorkloadFamily, Variant]] = []
    for family in dataset.families:
        if family.difficulty == "unassessed":
            findings.append(
                ReviewFinding(
                    "blocking",
                    "unassessed_difficulty",
                    "Family difficulty has not been assessed.",
                    family_ids=(family.family_id,),
                )
            )
        if family.label_review["status"] != "approved":
            findings.append(
                ReviewFinding(
                    "blocking",
                    "unresolved_gold_review",
                    "Family gold labels have not received human approval.",
                    family_ids=(family.family_id,),
                )
            )
        ambiguity = (
            family.policy_gold["expected_feasibility"] == "ambiguous"
            or bool(family.gold_structured_intent["ambiguities"])
            or len(family.candidate_gold["preferred_candidate_ids"]) > 1
            or len(family.profile_gold["preferred_profile_ids"]) > 1
            or len(family.image_gold["preferred_image_ids"]) > 1
            or any(
                variant.equivalence_status == "controlled_ambiguity"
                for variant in family.variants
            )
        )
        if ambiguity:
            if family.label_review["status"] == "approved":
                findings.append(
                    ReviewFinding(
                        "advisory",
                        "documented_gold_ambiguity",
                        "Family contains documented ambiguity approved by a human reviewer.",
                        family_ids=(family.family_id,),
                    )
                )
            else:
                findings.append(
                    ReviewFinding(
                        "blocking",
                        "unresolved_gold_ambiguity",
                        "Family ambiguity has not received human approval.",
                        family_ids=(family.family_id,),
                    )
                )
        for profile_id in family.profile_gold["preferred_profile_ids"]:
            preferred_profiles[str(profile_id)] += 1
        for image_id in family.image_gold["preferred_image_ids"]:
            preferred_images[str(image_id)] += 1
        for variant in family.variants:
            variants.append((family, variant))
            if variant.equivalence_status == "pending_review":
                findings.append(
                    ReviewFinding(
                        "blocking",
                        "pending_semantic_equivalence",
                        "Variant semantic equivalence requires human review.",
                        family_ids=(family.family_id,),
                        variant_ids=(variant.variant_id,),
                    )
                )

    for stratum, count in sorted(strata.items()):
        if count == 1:
            family_id = next(
                family.family_id
                for family in dataset.families
                if family.workload_stratum == stratum
            )
            findings.append(
                ReviewFinding(
                    "advisory",
                    "singleton_stratum",
                    f"Workload stratum {stratum!r} contains one family.",
                    family_ids=(family_id,),
                )
            )
    missing = sorted(
        set(dataset.review_policy["required_workload_strata"]) - set(strata)
    )
    for stratum in missing:
        findings.append(
            ReviewFinding(
                "advisory",
                "missing_workload_category",
                f"Review policy requires missing workload stratum {stratum!r}.",
            )
        )
    findings.extend(
        _imbalance_findings(
            preferred_profiles,
            threshold=float(dataset.review_policy["max_preferred_profile_share"]),
            code="unbalanced_preferred_profile",
            noun="profile",
        )
    )
    findings.extend(
        _imbalance_findings(
            preferred_images,
            threshold=float(dataset.review_policy["max_preferred_image_share"]),
            code="unbalanced_preferred_image",
            noun="image",
        )
    )

    for left_index, (left_family, left_variant) in enumerate(variants):
        for right_family, right_variant in variants[left_index + 1 :]:
            if left_variant.intent == right_variant.intent:
                findings.append(
                    ReviewFinding(
                        "advisory",
                        "identical_variant",
                        "Two variants contain identical text.",
                        family_ids=tuple(
                            sorted({left_family.family_id, right_family.family_id})
                        ),
                        variant_ids=tuple(
                            sorted((left_variant.variant_id, right_variant.variant_id))
                        ),
                        fingerprints=(_text_sha256(left_variant.intent),),
                    )
                )
                continue
            left_normalized = _normalize_text(left_variant.intent)
            right_normalized = _normalize_text(right_variant.intent)
            if left_normalized == right_normalized:
                findings.append(
                    ReviewFinding(
                        "advisory",
                        "normalized_identical_variant",
                        "Two variants are identical after review normalization.",
                        family_ids=tuple(
                            sorted({left_family.family_id, right_family.family_id})
                        ),
                        variant_ids=tuple(
                            sorted((left_variant.variant_id, right_variant.variant_id))
                        ),
                        fingerprints=(_text_sha256(left_normalized),),
                    )
                )

    ordered = tuple(
        sorted(
            findings,
            key=lambda item: (
                0 if item.severity == "blocking" else 1,
                item.code,
                item.family_ids,
                item.variant_ids,
                item.message,
            ),
        )
    )
    return ReviewReport(
        dataset_id=str(dataset.dataset_metadata["dataset_id"]),
        canonical_sha256=canonical_sha256(dataset.to_dict()),
        findings=ordered,
    )


def _git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    revision = result.stdout.strip()
    if not _GIT_REVISION.fullmatch(revision):
        raise GoldDatasetValidationError("current Git revision is unavailable")
    return revision


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _title_from_id(identifier: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-_]", identifier))


def _variant_class_from_v4(value: str, language: str) -> str:
    if value == "canonical" and language == "en":
        return "canonical_en"
    if value == "paraphrase" and language == "en":
        return "paraphrase_en"
    if language == "vi" or value == "vietnamese":
        return "vietnamese"
    return "other"


def _v4_candidate_ids(
    profiles: Sequence[str],
    images: Sequence[str],
    context: _CatalogContext,
    *,
    label: str,
) -> list[str]:
    selected: list[str] = []
    for profile_id in profiles:
        for image_id in images:
            candidate_id = f"{profile_id}-{image_id}"
            if candidate_id not in context.candidates:
                raise GoldDatasetValidationError(
                    f"{label} maps to unknown candidate {candidate_id!r}"
                )
            selected.append(candidate_id)
    return sorted(selected)


def import_v4_dataset(
    source: Path,
    *,
    source_split: str = "development",
    sample_ids: Sequence[str] = (),
    workload_manifests: Sequence[Path] = (),
) -> GoldDataset:
    """Import selected Protocol-v4 records into a development-only draft."""

    if source_split not in {"development", "test", "all"}:
        raise GoldDatasetValidationError("source_split must be development, test, or all")
    path = Path(source)
    try:
        raw = path.read_bytes()
        document = _strict_yaml_loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise GoldDatasetValidationError("Protocol-v4 source could not be read") from exc
    root = _mapping(document, "Protocol-v4 dataset")
    if root.get("schema_version") != "intent-gold-v4.0.0":
        raise GoldDatasetValidationError("source is not intent-gold-v4.0.0")
    dataset_id = _safe_id(root.get("dataset_id"), "Protocol-v4 dataset_id")
    items = root.get("items")
    if not isinstance(items, list):
        raise GoldDatasetValidationError("Protocol-v4 source items must be a list")
    requested = set(sample_ids)
    selected_items: list[Mapping[str, Any]] = []
    for index, item in enumerate(items):
        payload = _mapping(item, f"Protocol-v4 items[{index}]")
        item_split = payload.get("split")
        sample_id = str(payload.get("sample_id", ""))
        if source_split != "all" and item_split != source_split:
            continue
        if requested and sample_id not in requested:
            continue
        selected_items.append(payload)
    found = {str(item.get("sample_id")) for item in selected_items}
    missing = sorted(requested - found)
    if missing:
        raise GoldDatasetValidationError(
            "requested Protocol-v4 sample IDs were not selected: " + ", ".join(missing)
        )
    if not selected_items:
        raise GoldDatasetValidationError("Protocol-v4 import selected no cases")

    context = _catalog_context(workload_manifests=workload_manifests)
    source_digest = hashlib.sha256(raw).hexdigest()
    mappings = {
        str(item["workload_family"]): str(item["workload_id"])
        for item in root.get("system_workload_mapping", [])
        if isinstance(item, Mapping)
        and "workload_family" in item
        and "workload_id" in item
    }
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in selected_items:
        grouped[str(item["workload_family"])].append(item)
    families: list[dict[str, Any]] = []
    source_splits = sorted({str(item["split"]) for item in selected_items})
    for family_id in sorted(grouped):
        items_in_family = grouped[family_id]
        reference = items_in_family[0]
        family_source_splits = {str(item["split"]) for item in items_in_family}
        if len(family_source_splits) != 1:
            raise GoldDatasetValidationError(
                f"Protocol-v4 family {family_id!r} crosses source splits"
            )
        family_source_split = next(iter(family_source_splits))
        for item in items_in_family[1:]:
            for field in ("gold", "policy_constraints", "stratum"):
                if canonical_sha256(item[field]) != canonical_sha256(reference[field]):
                    raise GoldDatasetValidationError(
                        f"Protocol-v4 family {family_id!r} has non-uniform {field}"
                    )
        gold = _mapping(reference["gold"], f"Protocol-v4 {family_id}.gold")
        policy = _mapping(
            reference["policy_constraints"],
            f"Protocol-v4 {family_id}.policy_constraints",
        )
        preferred_profiles = [str(gold["preferred_profile"])]
        acceptable_profiles = [str(item) for item in gold["acceptable_profiles"]]
        preferred_images = [str(gold["preferred_image_id"])]
        acceptable_images = [str(item) for item in gold["acceptable_image_ids"]]
        preferred_candidates = _v4_candidate_ids(
            preferred_profiles,
            preferred_images,
            context,
            label=f"Protocol-v4 family {family_id}",
        )
        acceptable_candidates = _v4_candidate_ids(
            acceptable_profiles,
            acceptable_images,
            context,
            label=f"Protocol-v4 family {family_id}",
        )
        sizes = {
            float(_mapping(item["inputs"], "Protocol-v4 inputs")["dataset_size_gb"])
            for item in items_in_family
        }
        dataset_size = next(iter(sizes)) if len(sizes) == 1 else None
        source_case_ids = sorted(str(item["sample_id"]) for item in items_in_family)
        variants = []
        for item in sorted(items_in_family, key=lambda selected: str(selected["sample_id"])):
            inputs = _mapping(item["inputs"], "Protocol-v4 inputs")
            language = str(item["language"])
            variant_class = _variant_class_from_v4(str(item["variant"]), language)
            variants.append(
                {
                    "variant_id": str(item["sample_id"]),
                    "variant_class": variant_class,
                    "language": language,
                    "intent": str(inputs["intent"]),
                    "code_context": list(inputs["code_context_hints"]),
                    "equivalence_status": (
                        "canonical_reference"
                        if variant_class == "canonical_en"
                        else "pending_review"
                    ),
                }
            )
        families.append(
            {
                "family_id": family_id,
                "title": _title_from_id(family_id),
                "workload_stratum": str(reference["stratum"]),
                "difficulty": "unassessed",
                "executable_workload_id": mappings.get(family_id),
                "gold_structured_intent": {
                    "task_types": [],
                    "required_features": list(gold["required_image_capabilities"]),
                    "preferred_features": [],
                    "forbidden_features": [],
                    "required_frameworks": [],
                    "preferred_frameworks": [],
                    "gpu_semantics": "unspecified",
                    "minimum_cpu_cores": None,
                    "minimum_memory_gb": None,
                    "dataset_size_gb": dataset_size,
                    "ambiguities": (
                        []
                        if dataset_size is not None
                        else ["Imported variants have different dataset-size labels."]
                    ),
                },
                "candidate_gold": {
                    "preferred_candidate_ids": preferred_candidates,
                    "acceptable_candidate_ids": acceptable_candidates,
                },
                "profile_gold": {
                    "preferred_profile_ids": preferred_profiles,
                    "acceptable_profile_ids": acceptable_profiles,
                },
                "image_gold": {
                    "preferred_image_ids": preferred_images,
                    "acceptable_image_ids": acceptable_images,
                    "required_capabilities": list(gold["required_image_capabilities"]),
                },
                "policy_gold": {
                    "required_constraints": [
                        "allowed_profiles="
                        + ",".join(str(item) for item in policy["allowed_profiles"]),
                        "gpu_allowed=" + str(bool(policy["gpu_allowed"])).lower(),
                    ],
                    "explicitly_unsupported_requirements": [],
                    "expected_feasibility": "feasible",
                },
                "variants": variants,
                "label_review": {
                    "status": "pending",
                    "reviewed_by": None,
                    "reviewed_at_utc": None,
                    "notes": [
                        "Imported Protocol-v4 labels require Protocol-v5 human review."
                    ],
                },
                "source_provenance": {
                    "source_dataset_id": dataset_id,
                    "source_schema_version": str(root["schema_version"]),
                    "source_family_id": family_id,
                    "source_case_ids": source_case_ids,
                    "source_split": family_source_split,
                    "source_file_sha256": source_digest,
                    "evidence_classification": "historical_formative_development_only",
                    "original_label_sha256": canonical_sha256(
                        {
                            "gold": gold,
                            "policy_constraints": policy,
                            "provenance": reference.get("provenance"),
                        }
                    ),
                },
            }
        )
    now = _utc_now()
    import_identity = canonical_sha256(
        {
            "source_file_sha256": source_digest,
            "source_split": source_split,
            "selected_sample_ids": sorted(found),
        }
    )
    document_out = {
        "schema_version": GOLD_DATASET_SCHEMA_VERSION,
        "dataset_metadata": {
            "dataset_id": f"v5-v4-import-{import_identity[:12]}",
            "protocol_version": PROTOCOL_VERSION,
            "role": "development",
            "lifecycle": "draft",
            "created_at_utc": now,
            "created_by": "protocol-v5-v4-importer",
            "git_revision": _git_revision(),
            "evidence_classification": "historical_formative_development_only",
            "freeze_metadata": None,
            "source_datasets": [
                {
                    "dataset_id": dataset_id,
                    "schema_version": str(root["schema_version"]),
                    "source_file_sha256": source_digest,
                    "source_split": selected_split,
                }
                for selected_split in source_splits
            ],
        },
        "catalog_identity": current_catalog_identity(context.corpus),
        "review_policy": {
            "required_workload_strata": list(DEFAULT_IMPORT_REVIEW_STRATA),
            "max_preferred_profile_share": 0.5,
            "max_preferred_image_share": 0.5,
        },
        "families": families,
    }
    return validate_gold_dataset(
        document_out, workload_manifests=workload_manifests
    )


def validate_compiled_case(
    value: object,
    *,
    index: int,
    workload_manifests: Sequence[Path] = (),
) -> dict[str, Any]:
    """Validate and normalize one v2 flat case for split-bundle loading."""

    label = f"cases[{index}]"
    payload = _exact_mapping(
        value,
        {
            "case_id",
            "family_id",
            "variant_id",
            "language",
            "prompt",
            "inputs",
            "family_metadata",
            "variant_metadata",
            "gold",
            "source_provenance",
        },
        label,
    )
    case_id = _safe_id(payload["case_id"], f"{label}.case_id")
    variant_id = _safe_id(payload["variant_id"], f"{label}.variant_id")
    if case_id != variant_id:
        raise GoldDatasetValidationError(f"{label}.case_id must equal variant_id")
    family_id = _safe_id(payload["family_id"], f"{label}.family_id")
    family_meta = _exact_mapping(
        payload["family_metadata"],
        {"title", "workload_stratum", "difficulty", "executable_workload_id"},
        f"{label}.family_metadata",
    )
    inputs = _exact_mapping(
        payload["inputs"], {"dataset_size_gb", "code_context_hints"}, f"{label}.inputs"
    )
    variant_meta = _exact_mapping(
        payload["variant_metadata"],
        {"variant_class", "equivalence_status"},
        f"{label}.variant_metadata",
    )
    source = _exact_mapping(
        payload["source_provenance"],
        {
            "source_dataset_id",
            "source_schema_version",
            "source_case_id",
            "source_split",
            "evidence_classification",
            "authoring_canonical_sha256",
            "catalog_identity",
            "label_review",
            "original_source_provenance",
        },
        f"{label}.source_provenance",
    )
    context = _catalog_context(workload_manifests=workload_manifests)
    _validate_catalog_identity(
        source["catalog_identity"], context, f"{label}.source_provenance.catalog_identity"
    )
    gold = _exact_mapping(
        payload["gold"],
        {"gold_structured_intent", "candidate_gold", "profile_gold", "image_gold", "policy_gold"},
        f"{label}.gold",
    )
    variant = {
        "variant_id": variant_id,
        "variant_class": variant_meta["variant_class"],
        "language": payload["language"],
        "intent": payload["prompt"],
        "code_context": inputs["code_context_hints"],
        "equivalence_status": variant_meta["equivalence_status"],
    }
    family_document = {
        "family_id": family_id,
        "title": family_meta["title"],
        "workload_stratum": family_meta["workload_stratum"],
        "difficulty": family_meta["difficulty"],
        "executable_workload_id": family_meta["executable_workload_id"],
        "gold_structured_intent": gold["gold_structured_intent"],
        "candidate_gold": gold["candidate_gold"],
        "profile_gold": gold["profile_gold"],
        "image_gold": gold["image_gold"],
        "policy_gold": gold["policy_gold"],
        "variants": [variant],
        "label_review": source["label_review"],
        "source_provenance": source["original_source_provenance"],
    }
    normalized_family = _validate_family(family_document, index=index, context=context)
    normalized_variant = normalized_family.variants[0]
    if normalized_family.difficulty == "unassessed":
        raise GoldDatasetValidationError(
            f"{label}.family_metadata.difficulty cannot be unassessed in a compiled split"
        )
    if normalized_family.label_review["status"] != "approved":
        raise GoldDatasetValidationError(
            f"{label}.source_provenance.label_review must be approved"
        )
    if normalized_variant.equivalence_status == "pending_review":
        raise GoldDatasetValidationError(
            f"{label}.variant_metadata.equivalence_status cannot be pending_review"
        )
    if (
        inputs["dataset_size_gb"]
        != normalized_family.gold_structured_intent["dataset_size_gb"]
    ):
        raise GoldDatasetValidationError(
            f"{label}.inputs.dataset_size_gb must match gold_structured_intent"
        )
    if source["source_case_id"] != variant_id:
        raise GoldDatasetValidationError(
            f"{label}.source_provenance.source_case_id must equal variant_id"
        )
    if source["source_schema_version"] != GOLD_DATASET_SCHEMA_VERSION:
        raise GoldDatasetValidationError(
            f"{label}.source_provenance.source_schema_version is unsupported"
        )
    return {
        "case_id": case_id,
        "family_id": family_id,
        "variant_id": variant_id,
        "language": normalized_variant.language,
        "prompt": normalized_variant.intent,
        "inputs": {
            "dataset_size_gb": _optional_resource(
                inputs["dataset_size_gb"], f"{label}.inputs.dataset_size_gb"
            ),
            "code_context_hints": list(normalized_variant.code_context),
        },
        "family_metadata": {
            "title": normalized_family.title,
            "workload_stratum": normalized_family.workload_stratum,
            "difficulty": normalized_family.difficulty,
            "executable_workload_id": normalized_family.executable_workload_id,
        },
        "variant_metadata": {
            "variant_class": normalized_variant.variant_class,
            "equivalence_status": normalized_variant.equivalence_status,
        },
        "gold": {
            "gold_structured_intent": dict(normalized_family.gold_structured_intent),
            "candidate_gold": dict(normalized_family.candidate_gold),
            "profile_gold": dict(normalized_family.profile_gold),
            "image_gold": dict(normalized_family.image_gold),
            "policy_gold": dict(normalized_family.policy_gold),
        },
        "source_provenance": {
            "source_dataset_id": _safe_id(
                source["source_dataset_id"], f"{label}.source_provenance.source_dataset_id"
            ),
            "source_schema_version": _safe_id(
                source["source_schema_version"], f"{label}.source_provenance.source_schema_version"
            ),
            "source_case_id": _safe_id(
                source["source_case_id"], f"{label}.source_provenance.source_case_id"
            ),
            "source_split": _safe_id(
                source["source_split"], f"{label}.source_provenance.source_split"
            ),
            "evidence_classification": _safe_id(
                source["evidence_classification"],
                f"{label}.source_provenance.evidence_classification",
            ),
            "authoring_canonical_sha256": _sha256(
                source["authoring_canonical_sha256"],
                f"{label}.source_provenance.authoring_canonical_sha256",
            ),
            "catalog_identity": dict(source["catalog_identity"]),
            "label_review": dict(normalized_family.label_review),
            "original_source_provenance": (
                dict(normalized_family.source_provenance)
                if normalized_family.source_provenance is not None
                else None
            ),
        },
    }


def _outside_repository(path: Path) -> bool:
    repository = ROOT.resolve()
    lexical = Path(os.path.abspath(path))
    resolved = path.resolve()
    for candidate in (lexical, resolved):
        try:
            candidate.relative_to(repository)
        except ValueError:
            continue
        return False
    return True


def compile_gold_dataset(
    value: GoldDataset | LoadedGoldDataset,
    *,
    source_path: Path | None = None,
    output_path: Path | None = None,
    workload_manifests: Sequence[Path] = (),
):
    """Compile approved human-authored families into a v2 flat split bundle."""

    dataset = _dataset(value)
    if dataset.dataset_metadata["lifecycle"] != "frozen":
        raise GoldDatasetReviewError("compilation requires a manually frozen dataset")
    report = review_gold_dataset(dataset)
    if report.blocking_findings:
        codes = sorted({item.code for item in report.blocking_findings})
        raise GoldDatasetReviewError(
            "compilation blocked by unresolved labels: " + ", ".join(codes)
        )
    role = str(dataset.dataset_metadata["role"])
    effective_source = (
        value.source_path if isinstance(value, LoadedGoldDataset) else source_path
    )
    if role == "confirmatory":
        if effective_source is None or output_path is None:
            raise GoldDatasetValidationError(
                "confirmatory compilation requires explicit source and output paths"
            )
        if (
            not Path(effective_source).is_absolute()
            or not Path(output_path).is_absolute()
            or not _outside_repository(Path(effective_source))
            or not _outside_repository(Path(output_path))
        ):
            raise GoldDatasetValidationError(
                "confirmatory source and output must be absolute external paths"
            )
    canonical = canonical_sha256(dataset.to_dict())
    cases: list[dict[str, Any]] = []
    for family in dataset.families:
        for variant in family.variants:
            cases.append(
                {
                    "case_id": variant.variant_id,
                    "family_id": family.family_id,
                    "variant_id": variant.variant_id,
                    "language": variant.language,
                    "prompt": variant.intent,
                    "inputs": {
                        "dataset_size_gb": family.gold_structured_intent["dataset_size_gb"],
                        "code_context_hints": list(variant.code_context),
                    },
                    "family_metadata": {
                        "title": family.title,
                        "workload_stratum": family.workload_stratum,
                        "difficulty": family.difficulty,
                        "executable_workload_id": family.executable_workload_id,
                    },
                    "variant_metadata": {
                        "variant_class": variant.variant_class,
                        "equivalence_status": variant.equivalence_status,
                    },
                    "gold": {
                        "gold_structured_intent": dict(family.gold_structured_intent),
                        "candidate_gold": dict(family.candidate_gold),
                        "profile_gold": dict(family.profile_gold),
                        "image_gold": dict(family.image_gold),
                        "policy_gold": dict(family.policy_gold),
                    },
                    "source_provenance": {
                        "source_dataset_id": dataset.dataset_metadata["dataset_id"],
                        "source_schema_version": dataset.schema_version,
                        "source_case_id": variant.variant_id,
                        "source_split": role,
                        "evidence_classification": dataset.dataset_metadata[
                            "evidence_classification"
                        ],
                        "authoring_canonical_sha256": canonical,
                        "catalog_identity": dict(dataset.catalog_identity),
                        "label_review": dict(family.label_review),
                        "original_source_provenance": (
                            dict(family.source_provenance)
                            if family.source_provenance is not None
                            else None
                        ),
                    },
                }
            )
    family_ids = sorted(family.family_id for family in dataset.families)
    freeze = dataset.dataset_metadata["freeze_metadata"]
    assert isinstance(freeze, Mapping)
    document: dict[str, Any] = {
        "schema_version": COMPILED_SPLIT_SCHEMA_VERSION,
        "split_manifest": {
            "dataset_id": dataset.dataset_metadata["dataset_id"],
            "split_id": (
                "v5-development" if role == "development" else "v5-confirmatory"
            ),
            "role": role,
            "family_ids": family_ids,
            "case_count": len(cases),
            "family_count": len(family_ids),
            "checksum": "0" * 64,
            "creation_metadata": {
                "created_at_utc": dataset.dataset_metadata["created_at_utc"],
                "created_by": dataset.dataset_metadata["created_by"],
            },
            "freeze_metadata": dict(freeze),
        },
        "cases": cases,
    }
    from .split_dataset import split_bundle_checksum, validate_split_bundle

    document["split_manifest"]["checksum"] = split_bundle_checksum(document)
    return validate_split_bundle(
        document, workload_manifests=workload_manifests
    )


def _serialize_document(path: Path, payload: Mapping[str, Any]) -> bytes:
    suffix = path.suffix.lower()
    if suffix == ".json":
        text = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    elif suffix in {".yaml", ".yml"}:
        text = yaml.safe_dump(
            dict(payload), sort_keys=False, allow_unicode=True, default_flow_style=False
        ).rstrip()
    else:
        raise GoldDatasetValidationError("output must use .yaml, .yml, or .json")
    return (text + "\n").encode("utf-8")


def write_document_exclusive(path: Path, payload: Mapping[str, Any]) -> Path:
    """Atomically create YAML/JSON without replacing an existing destination."""

    target = Path(path)
    if not target.parent.is_dir():
        raise FileNotFoundError(target.parent)
    serialized = _serialize_document(target, payload)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    metadata = payload.get("dataset_metadata")
    split_manifest = payload.get("split_manifest")
    confirmatory = (
        isinstance(metadata, Mapping) and metadata.get("role") == "confirmatory"
    ) or (
        isinstance(split_manifest, Mapping)
        and split_manifest.get("role") == "confirmatory"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600 if confirmatory else 0o644)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)
        temporary.unlink()
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(target.parent, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return target
    finally:
        if temporary.exists():
            temporary.unlink()


def _render_markdown_summary(summary: Mapping[str, Any]) -> str:
    lines = [
        f"# Dataset summary: {summary['dataset_id']}",
        "",
        f"- Families: {summary['family_count']}",
        f"- Cases: {summary['case_count']}",
        f"- Canonical SHA-256: `{summary['canonical_sha256']}`",
        "",
    ]
    for heading, field in (
        ("Languages", "language_counts"),
        ("Difficulty", "difficulty_distribution"),
        ("Feasibility", "feasibility_distribution"),
        ("Capabilities", "capability_coverage"),
    ):
        lines.extend([f"## {heading}", ""])
        values = summary[field]
        if values:
            lines.extend(f"- {key}: {values[key]}" for key in sorted(values))
        else:
            lines.append("- None")
        lines.append("")
    lines.extend(["## Workload strata", ""])
    for identifier, counts in summary["workload_strata"].items():
        lines.append(
            f"- {identifier}: {counts['family_count']} family/families, "
            f"{counts['case_count']} case(s)"
        )
    lines.append("")
    for heading, field in (
        ("Profile coverage", "profile_coverage"),
        ("Image coverage", "image_coverage"),
    ):
        lines.extend([f"## {heading}", ""])
        for label in ("preferred", "acceptable"):
            values = summary[field][label]
            if values:
                lines.extend(
                    f"- {label} {identifier}: {values[identifier]}"
                    for identifier in sorted(values)
                )
            else:
                lines.append(f"- {label}: none")
        lines.append("")
    lines.extend(["## Perturbation coverage", ""])
    for identifier, counts in summary["perturbation_coverage"].items():
        lines.append(
            f"- {identifier}: {counts['family_count']} family/families, "
            f"{counts['case_count']} case(s)"
        )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_markdown_review(report: ReviewReport) -> str:
    lines = [
        f"# Human review: {report.dataset_id}",
        "",
        f"- Blocking findings: {len(report.blocking_findings)}",
        f"- Advisory findings: {len(report.advisory_findings)}",
        f"- Canonical SHA-256: `{report.canonical_sha256}`",
        "",
    ]
    if not report.findings:
        lines.append("No review findings.\n")
        return "\n".join(lines)
    for severity in ("blocking", "advisory"):
        selected = [item for item in report.findings if item.severity == severity]
        if not selected:
            continue
        lines.extend([f"## {severity.title()}", ""])
        for item in selected:
            identifiers = ", ".join(item.family_ids + item.variant_ids)
            suffix = f" ({identifiers})" if identifiers else ""
            lines.append(f"- `{item.code}`{suffix}: {item.message}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "summary", "review"):
        child = subparsers.add_parser(command)
        child.add_argument("dataset", type=Path)
        child.add_argument(
            "--workload-manifest", action="append", default=[], type=Path
        )
        if command in {"summary", "review"}:
            child.add_argument(
                "--format",
                choices=("json", "markdown"),
                default="json" if command == "summary" else "markdown",
            )
    importer = subparsers.add_parser("import-v4")
    importer.add_argument("source", type=Path)
    importer.add_argument("--output", required=True, type=Path)
    importer.add_argument(
        "--source-split", choices=("development", "test", "all"), default="development"
    )
    importer.add_argument("--sample-id", action="append", default=[])
    importer.add_argument("--workload-manifest", action="append", default=[], type=Path)
    compiler = subparsers.add_parser("compile")
    compiler.add_argument("source", type=Path)
    compiler.add_argument("--output", required=True, type=Path)
    compiler.add_argument("--workload-manifest", action="append", default=[], type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "import-v4":
            dataset = import_v4_dataset(
                args.source,
                source_split=args.source_split,
                sample_ids=args.sample_id,
                workload_manifests=args.workload_manifest,
            )
            write_document_exclusive(args.output, dataset.to_dict())
            print(
                json.dumps(
                    {
                        "status": "DEVELOPMENT_DRAFT",
                        "output": str(args.output),
                        "family_count": len(dataset.families),
                        "case_count": sum(len(item.variants) for item in dataset.families),
                        "evidence_classification": "historical_formative_development_only",
                    },
                    sort_keys=True,
                )
            )
            return 0
        loaded = load_gold_dataset(
            args.dataset if hasattr(args, "dataset") else args.source,
            workload_manifests=args.workload_manifest,
        )
        if args.command == "validate":
            print(
                json.dumps(
                    {
                        "status": "VALID",
                        "dataset_id": loaded.dataset.dataset_metadata["dataset_id"],
                        "source_file_sha256": loaded.source_file_sha256,
                        "canonical_sha256": loaded.source_canonical_sha256,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "summary":
            summary = summarize_gold_dataset(loaded)
            if args.format == "json":
                print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(_render_markdown_summary(summary), end="")
            return 0
        if args.command == "review":
            report = review_gold_dataset(loaded)
            if args.format == "json":
                print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(_render_markdown_review(report), end="")
            return 1 if report.blocking_findings else 0
        bundle = compile_gold_dataset(
            loaded,
            source_path=loaded.source_path,
            output_path=args.output,
            workload_manifests=args.workload_manifest,
        )
        write_document_exclusive(args.output, bundle.to_dict())
        print(
            json.dumps(
                {
                    "status": "COMPILED",
                    "output": str(args.output),
                    "schema_version": bundle.schema_version,
                    "checksum": bundle.split_manifest.checksum,
                },
                sort_keys=True,
            )
        )
        return 0
    except (GoldDatasetValidationError, OSError, subprocess.SubprocessError) as exc:
        print(
            json.dumps(
                {"status": "ERROR", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "COMPILED_SPLIT_SCHEMA_VERSION",
    "GOLD_DATASET_SCHEMA_VERSION",
    "GOLD_REVIEW_SCHEMA_VERSION",
    "GOLD_SUMMARY_SCHEMA_VERSION",
    "GoldDataset",
    "GoldDatasetReviewError",
    "GoldDatasetValidationError",
    "LoadedGoldDataset",
    "ReviewFinding",
    "ReviewReport",
    "Variant",
    "WorkloadFamily",
    "compile_gold_dataset",
    "current_catalog_identity",
    "import_v4_dataset",
    "load_gold_dataset",
    "review_gold_dataset",
    "summarize_gold_dataset",
    "validate_compiled_case",
    "validate_gold_dataset",
    "write_document_exclusive",
]
