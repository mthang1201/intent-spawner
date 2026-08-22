"""Administrator-owned EnvironmentCandidate retrieval corpus for P2.

This module builds deterministic retrieval representations (``CandidateDocument``)
exclusively from valid combinations in the administrator-owned image catalog
and profile configuration.

The candidates are retrieval representations.  When a candidate is selected by
the P2 ranking pipeline, it converts to a trusted :class:`recommender.models.EnvironmentCandidate`
and :class:`recommender.models.SpawnRecommendation` before undergoing strict validation
by :class:`recommender.policy.PolicyValidator`.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
import argparse
import hashlib
import json
import math
import re
from typing import Any, ClassVar, Sequence

from .models import (
    ENVIRONMENT_CANDIDATE_SCHEMA_VERSION,
    POLICY_VERSION,
    ContractValidationError,
    EnvironmentCandidate,
    GPURequirement,
    SpawnRecommendation,
    TaskType,
    _normalized_identifier,
    _normalized_strings,
    _normalized_text,
    _schema_version,
    _version,
)
from .rule_based import DEFAULT_CATALOG_PATH, load_image_catalog, validate_image_catalog


CANDIDATE_DOCUMENT_SCHEMA_VERSION = "candidate-document-v1"
CANDIDATE_CORPUS_SCHEMA_VERSION = "candidate-corpus-v1"
DEFAULT_CORPUS_VERSION = "environment-candidate-corpus-v1"

_BYTES_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)?\s*$")
_UNIT_MULTIPLIERS = {
    "b": 1,
    "k": 1024,
    "kb": 1000,
    "kib": 1024,
    "m": 1024 * 1024,
    "mb": 1000 * 1000,
    "mib": 1024 * 1024,
    "g": 1024 * 1024 * 1024,
    "gb": 1000 * 1000 * 1000,
    "gib": 1024 * 1024 * 1024,
    "t": 1024 * 1024 * 1024 * 1024,
    "tb": 1000 * 1000 * 1000 * 1000,
    "tib": 1024 * 1024 * 1024 * 1024,
}

DEFAULT_PROFILE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "small": {
        "slug": "small",
        "display_name": "Small: low CPU/RAM",
        "description": "For basic Python and light notebooks. Intentionally too small for the OOM demo.",
        "cpu_guarantee": 0.1,
        "cpu_limit": 0.5,
        "mem_guarantee": "256M",
        "mem_limit": "384M",
        "gpu_count": 0,
        "gpu_resource": None,
        "suitability_tags": [
            "basic_python",
            "education",
            "light_workload",
            "low_cpu",
            "low_memory",
            "small_dataset",
        ],
    },
    "medium": {
        "slug": "medium",
        "display_name": "Medium: moderate CPU/RAM",
        "description": "For medium data exploration and pandas workloads.",
        "cpu_guarantee": 0.5,
        "cpu_limit": 1.0,
        "mem_guarantee": "768M",
        "mem_limit": "1G",
        "gpu_count": 0,
        "gpu_resource": None,
        "suitability_tags": [
            "data_exploration",
            "data_processing",
            "medium_dataset",
            "medium_workload",
            "moderate_cpu",
            "moderate_memory",
            "pandas",
        ],
    },
    "large": {
        "slug": "large",
        "display_name": "Large: high CPU/RAM",
        "description": "For training-like workloads. Users may choose this defensively even when idle.",
        "cpu_guarantee": 1.5,
        "cpu_limit": 2.0,
        "mem_guarantee": "1536M",
        "mem_limit": "2G",
        "gpu_count": 0,
        "gpu_resource": None,
        "suitability_tags": [
            "deep_learning",
            "heavy_workload",
            "high_cpu",
            "high_memory",
            "large_dataset",
            "model_training",
            "training",
        ],
    },
}

IMAGE_METADATA_EXTENSIONS: dict[str, dict[str, Any]] = {
    "minimal-python": {
        "task_types": [
            TaskType.SOFTWARE_DEVELOPMENT,
            TaskType.EDUCATION,
            TaskType.OTHER,
        ],
        "frameworks": [],
        "libraries": ["python", "jupyterlab"],
        "suitability_tags": [
            "education",
            "general_python",
            "light_scripts",
            "minimal_environment",
        ],
        "preference_tags": [
            "fast_startup",
            "lightweight",
            "minimal",
        ],
    },
    "scipy-data-science": {
        "task_types": [
            TaskType.DATA_ANALYSIS,
            TaskType.DATA_PROCESSING,
            TaskType.VISUALIZATION,
            TaskType.MACHINE_LEARNING,
            TaskType.SCIENTIFIC_COMPUTING,
        ],
        "frameworks": ["scikit-learn", "xgboost"],
        "libraries": [
            "matplotlib",
            "numpy",
            "pandas",
            "scikit-learn",
            "scipy",
            "seaborn",
            "xgboost",
        ],
        "suitability_tags": [
            "data_analysis",
            "data_science",
            "machine_learning",
            "tabular_data",
            "visualization",
        ],
        "preference_tags": [
            "pandas",
            "scikit_learn",
            "scipy",
            "tabular",
            "visualization",
        ],
    },
    "pytorch-deep-learning": {
        "task_types": [
            TaskType.DEEP_LEARNING,
            TaskType.MODEL_TRAINING,
            TaskType.MODEL_INFERENCE,
            TaskType.MACHINE_LEARNING,
            TaskType.DATA_ANALYSIS,
        ],
        "frameworks": ["pytorch", "torch"],
        "libraries": [
            "cuda-userspace",
            "data-science",
            "pytorch",
            "torch",
            "torchaudio",
            "torchvision",
        ],
        "suitability_tags": [
            "cuda_userspace",
            "deep_learning",
            "model_training",
            "neural_networks",
            "pytorch_models",
        ],
        "preference_tags": [
            "cuda_support",
            "deep_learning",
            "neural_networks",
            "pytorch",
            "torch",
        ],
    },
    "tensorflow-deep-learning": {
        "task_types": [
            TaskType.DEEP_LEARNING,
            TaskType.MODEL_TRAINING,
            TaskType.MODEL_INFERENCE,
            TaskType.MACHINE_LEARNING,
            TaskType.DATA_ANALYSIS,
        ],
        "frameworks": ["tensorflow", "keras"],
        "libraries": [
            "cuda-userspace",
            "data-science",
            "keras",
            "tensorflow",
        ],
        "suitability_tags": [
            "cuda_userspace",
            "deep_learning",
            "keras",
            "model_training",
            "neural_networks",
            "tensorflow_models",
        ],
        "preference_tags": [
            "cuda_support",
            "deep_learning",
            "keras",
            "neural_networks",
            "tensorflow",
        ],
    },
}


def parse_memory_to_bytes(value: str | int | float) -> int:
    """Parse integer bytes or memory strings with units into integer byte count."""
    if isinstance(value, bool):
        raise ContractValidationError("memory value must not be a boolean")
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value < 0:
            raise ContractValidationError("memory value must be finite and non-negative")
        return int(value)
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError("memory string must not be blank")

    match = _BYTES_PATTERN.match(value.strip())
    if not match:
        raise ContractValidationError(f"invalid memory specification: {value!r}")

    number_str, unit_str = match.groups()
    try:
        number = float(number_str)
    except ValueError as exc:
        raise ContractValidationError(f"invalid memory specification: {value!r}") from exc

    if not math.isfinite(number) or number < 0:
        raise ContractValidationError("memory amount must be finite and non-negative")

    unit = (unit_str or "b").lower()
    multiplier = _UNIT_MULTIPLIERS.get(unit)
    if multiplier is None:
        raise ContractValidationError(f"unknown memory unit: {unit_str!r}")

    return int(number * multiplier)


def canonical_json_checksum(payload: Any) -> str:
    """Return the SHA-256 checksum of canonical compact JSON representation."""
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CandidateResourceMetadata:
    """Normalized CPU, memory, and GPU resource capabilities of a candidate."""

    cpu_guarantee_cores: float
    cpu_limit_cores: float
    memory_guarantee_gb: float
    memory_limit_gb: float
    memory_guarantee_bytes: int
    memory_limit_bytes: int
    gpu_count: int = 0
    gpu_resource: str | None = None

    def __post_init__(self) -> None:
        for name in ("cpu_guarantee_cores", "cpu_limit_cores", "memory_guarantee_gb", "memory_limit_gb"):
            val = getattr(self, name)
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or val < 0:
                raise ContractValidationError(f"{name} must be a finite non-negative number")
            object.__setattr__(self, name, float(val))

        for name in ("memory_guarantee_bytes", "memory_limit_bytes", "gpu_count"):
            val = getattr(self, name)
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                raise ContractValidationError(f"{name} must be a non-negative integer")

        if self.cpu_guarantee_cores > self.cpu_limit_cores:
            raise ContractValidationError("cpu_guarantee_cores must not exceed cpu_limit_cores")
        if self.memory_guarantee_bytes > self.memory_limit_bytes:
            raise ContractValidationError("memory_guarantee_bytes must not exceed memory_limit_bytes")
        if self.memory_guarantee_gb > self.memory_limit_gb:
            raise ContractValidationError("memory_guarantee_gb must not exceed memory_limit_gb")

    @classmethod
    def from_profile_dict(cls, profile: Mapping[str, Any]) -> "CandidateResourceMetadata":
        """Construct validated resource metadata from raw profile configuration."""
        cpu_guarantee = float(profile.get("cpu_guarantee", 0.0))
        cpu_limit = float(profile.get("cpu_limit", 0.0))
        mem_guarantee_raw = profile.get("mem_guarantee", 0)
        mem_limit_raw = profile.get("mem_limit", 0)
        gpu_count = int(profile.get("gpu_count", 0))
        gpu_resource = profile.get("gpu_resource")
        if gpu_resource is not None:
            gpu_resource = str(gpu_resource).strip() or None

        mem_guarantee_bytes = parse_memory_to_bytes(mem_guarantee_raw)
        mem_limit_bytes = parse_memory_to_bytes(mem_limit_raw)
        mem_guarantee_gb = round(mem_guarantee_bytes / (1024**3), 4)
        mem_limit_gb = round(mem_limit_bytes / (1024**3), 4)

        return cls(
            cpu_guarantee_cores=cpu_guarantee,
            cpu_limit_cores=cpu_limit,
            memory_guarantee_gb=mem_guarantee_gb,
            memory_limit_gb=mem_limit_gb,
            memory_guarantee_bytes=mem_guarantee_bytes,
            memory_limit_bytes=mem_limit_bytes,
            gpu_count=gpu_count,
            gpu_resource=gpu_resource,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_guarantee_cores": self.cpu_guarantee_cores,
            "cpu_limit_cores": self.cpu_limit_cores,
            "memory_guarantee_gb": self.memory_guarantee_gb,
            "memory_limit_gb": self.memory_limit_gb,
            "memory_guarantee_bytes": self.memory_guarantee_bytes,
            "memory_limit_bytes": self.memory_limit_bytes,
            "gpu_count": self.gpu_count,
            "gpu_resource": self.gpu_resource,
        }


def generate_candidate_retrieval_text(
    *,
    candidate_id: str,
    profile_id: str,
    profile_display_name: str,
    image_id: str,
    image_display_name: str,
    image_reference: str,
    description: str,
    task_types: Sequence[TaskType | str],
    frameworks: Sequence[str],
    libraries: Sequence[str],
    capabilities: Sequence[str],
    match_terms: Sequence[str],
    suitability_tags: Sequence[str],
    preference_tags: Sequence[str],
    resource_metadata: CandidateResourceMetadata,
) -> str:
    """Deterministically generate canonical retrieval text from structured candidate metadata."""
    sorted_tasks = ", ".join(
        sorted({t.value if isinstance(t, TaskType) else str(t) for t in task_types})
    )
    sorted_frameworks = ", ".join(sorted({_normalized_text(f, "framework") for f in frameworks}))
    sorted_libraries = ", ".join(sorted({_normalized_text(l, "library") for l in libraries}))
    sorted_capabilities = ", ".join(sorted({_normalized_text(c, "capability") for c in capabilities}))
    sorted_match_terms = ", ".join(sorted({_normalized_text(m, "match_term") for m in match_terms}))
    sorted_suitability = ", ".join(sorted({_normalized_text(s, "suitability") for s in suitability_tags}))
    sorted_preference = ", ".join(sorted({_normalized_text(p, "preference") for p in preference_tags}))

    gpu_desc = (
        f"{resource_metadata.gpu_count} GPU ({resource_metadata.gpu_resource})"
        if resource_metadata.gpu_count and resource_metadata.gpu_resource
        else f"{resource_metadata.gpu_count} GPU"
    )

    lines = [
        f"Candidate ID: {candidate_id}",
        f"Profile: {profile_display_name} (ID: {profile_id}) | CPU Guarantee: {resource_metadata.cpu_guarantee_cores:g} cores | CPU Limit: {resource_metadata.cpu_limit_cores:g} cores | Memory Guarantee: {resource_metadata.memory_guarantee_gb:g} GB | Memory Limit: {resource_metadata.memory_limit_gb:g} GB | GPU: {gpu_desc}",
        f"Image: {image_display_name} (ID: {image_id}) | Reference: {image_reference}",
        f"Description: {description}",
        f"Workloads / Tasks: {sorted_tasks or 'none'}",
        f"Frameworks: {sorted_frameworks or 'none'}",
        f"Libraries: {sorted_libraries or 'none'}",
        f"Capabilities: {sorted_capabilities or 'none'}",
        f"Keywords / Match Terms: {sorted_match_terms or 'none'}",
        f"Suitability Tags: {sorted_suitability or 'none'}",
        f"Preference Tags: {sorted_preference or 'none'}",
    ]
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class CandidateDocument:
    """Rich retrieval representation of an administrator-authorized environment candidate."""

    candidate_id: str
    profile_id: str
    image_id: str
    image_reference: str
    display_name: str
    description: str
    task_types: tuple[TaskType, ...]
    capabilities: tuple[str, ...]
    frameworks: tuple[str, ...]
    libraries: tuple[str, ...]
    resource_metadata: CandidateResourceMetadata
    gpu_capability: GPURequirement
    suitability_tags: tuple[str, ...]
    preference_tags: tuple[str, ...]
    match_terms: tuple[str, ...]
    retrieval_text: str
    catalog_version: str
    policy_version: str = POLICY_VERSION
    schema_version: str = CANDIDATE_DOCUMENT_SCHEMA_VERSION

    SUPPORTED_SCHEMA_VERSION: ClassVar[str] = CANDIDATE_DOCUMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _normalized_identifier(self.candidate_id, "candidate_id")
        )
        object.__setattr__(
            self, "profile_id", _normalized_identifier(self.profile_id, "profile_id")
        )
        object.__setattr__(
            self, "image_id", _normalized_identifier(self.image_id, "image_id")
        )
        if not isinstance(self.image_reference, str) or not self.image_reference.strip():
            raise ContractValidationError("image_reference must be a non-empty string")
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ContractValidationError("display_name must be a non-empty string")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ContractValidationError("description must be a non-empty string")
        if not isinstance(self.resource_metadata, CandidateResourceMetadata):
            raise ContractValidationError("resource_metadata must be a CandidateResourceMetadata")

        for name in ("capabilities", "frameworks", "libraries", "suitability_tags", "preference_tags", "match_terms"):
            object.__setattr__(self, name, _normalized_strings(getattr(self, name), name))

        if not isinstance(self.gpu_capability, GPURequirement):
            if isinstance(self.gpu_capability, str):
                object.__setattr__(self, "gpu_capability", GPURequirement(self.gpu_capability))
            else:
                raise ContractValidationError("gpu_capability must be a GPURequirement enum")

        object.__setattr__(self, "catalog_version", _version(self.catalog_version, "catalog_version"))
        object.__setattr__(self, "policy_version", _version(self.policy_version, "policy_version"))
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, self.SUPPORTED_SCHEMA_VERSION),
        )
        if not isinstance(self.retrieval_text, str) or not self.retrieval_text.strip():
            raise ContractValidationError("retrieval_text must be non-empty")

    def to_environment_candidate(self) -> EnvironmentCandidate:
        """Resolve to the trusted data contract carrying only identifiers and provenance."""
        return EnvironmentCandidate(
            candidate_id=self.candidate_id,
            profile_id=self.profile_id,
            image_id=self.image_id,
            catalog_version=self.catalog_version,
            policy_version=self.policy_version,
        )

    def to_spawn_recommendation(
        self,
        *,
        reasons: Sequence[str] | None = None,
        image_reasons: Sequence[str] | None = None,
        score: float | int | None = 100,
        backend_name: str = "p2_recommender",
        backend_version: str = "p2-v1",
    ) -> SpawnRecommendation:
        """Convert candidate to SpawnRecommendation for PolicyValidator verification."""
        rec_reasons = list(reasons) if reasons is not None else [
            f"Selected candidate {self.candidate_id} ({self.display_name})",
            f"Profile: {self.profile_id} ({self.resource_metadata.cpu_limit_cores:g} CPU limit, {self.resource_metadata.memory_limit_gb:g}GB memory limit)",
        ]
        img_reasons = list(image_reasons) if image_reasons is not None else [
            f"Image: {self.image_id} ({self.display_name})",
            "Selected from administrator catalog only",
        ]
        return SpawnRecommendation(
            profile=self.profile_id,
            reasons=rec_reasons,
            score=score,
            image_id=self.image_id,
            image_reference=self.image_reference,
            image_reasons=img_reasons,
            catalog_version=self.catalog_version,
            policy_version=self.policy_version,
            backend_name=backend_name,
            backend_version=backend_version,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize candidate document to standard dictionary representation."""
        return {
            "candidate_id": self.candidate_id,
            "profile_id": self.profile_id,
            "image_id": self.image_id,
            "image_reference": self.image_reference,
            "display_name": self.display_name,
            "description": self.description,
            "task_types": [t.value for t in self.task_types],
            "capabilities": list(self.capabilities),
            "frameworks": list(self.frameworks),
            "libraries": list(self.libraries),
            "resource_metadata": self.resource_metadata.to_dict(),
            "gpu_capability": self.gpu_capability.value,
            "suitability_tags": list(self.suitability_tags),
            "preference_tags": list(self.preference_tags),
            "match_terms": list(self.match_terms),
            "retrieval_text": self.retrieval_text,
            "catalog_version": self.catalog_version,
            "policy_version": self.policy_version,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CandidateDocument":
        """Strictly deserialize mapping into CandidateDocument."""
        if not isinstance(payload, Mapping):
            raise ContractValidationError("CandidateDocument payload must be a mapping")

        raw = dict(payload)
        res_raw = raw.get("resource_metadata")
        if isinstance(res_raw, Mapping):
            raw["resource_metadata"] = CandidateResourceMetadata(
                cpu_guarantee_cores=float(res_raw["cpu_guarantee_cores"]),
                cpu_limit_cores=float(res_raw["cpu_limit_cores"]),
                memory_guarantee_gb=float(res_raw["memory_guarantee_gb"]),
                memory_limit_gb=float(res_raw["memory_limit_gb"]),
                memory_guarantee_bytes=int(res_raw["memory_guarantee_bytes"]),
                memory_limit_bytes=int(res_raw["memory_limit_bytes"]),
                gpu_count=int(res_raw.get("gpu_count", 0)),
                gpu_resource=res_raw.get("gpu_resource"),
            )

        task_types_raw = raw.get("task_types", ())
        if isinstance(task_types_raw, (list, tuple)):
            raw["task_types"] = tuple(
                TaskType(t) if not isinstance(t, TaskType) else t
                for t in task_types_raw
            )

        return cls(**raw)


@dataclass(frozen=True, slots=True)
class CandidateCorpus:
    """Administrator-owned catalog corpus containing valid candidate representations."""

    candidates: tuple[CandidateDocument, ...]
    corpus_version: str
    source_image_catalog_version: str
    source_image_catalog_checksum: str
    source_profile_catalog_checksum: str
    corpus_checksum: str
    policy_version: str = POLICY_VERSION
    schema_version: str = CANDIDATE_CORPUS_SCHEMA_VERSION

    SUPPORTED_SCHEMA_VERSION: ClassVar[str] = CANDIDATE_CORPUS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "corpus_version", _version(self.corpus_version, "corpus_version")
        )
        object.__setattr__(
            self,
            "source_image_catalog_version",
            _version(self.source_image_catalog_version, "source_image_catalog_version"),
        )
        object.__setattr__(
            self,
            "source_image_catalog_checksum",
            _normalized_text(self.source_image_catalog_checksum, "source_image_catalog_checksum"),
        )
        object.__setattr__(
            self,
            "source_profile_catalog_checksum",
            _normalized_text(self.source_profile_catalog_checksum, "source_profile_catalog_checksum"),
        )
        object.__setattr__(
            self,
            "corpus_checksum",
            _normalized_text(self.corpus_checksum, "corpus_checksum"),
        )
        object.__setattr__(
            self, "policy_version", _version(self.policy_version, "policy_version")
        )
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, self.SUPPORTED_SCHEMA_VERSION),
        )

        candidate_ids = [c.candidate_id for c in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ContractValidationError("corpus candidates must have unique candidate IDs")

    def __len__(self) -> int:
        return len(self.candidates)

    def __iter__(self):
        return iter(self.candidates)

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        """Return tuple of sorted candidate IDs."""
        return tuple(c.candidate_id for c in self.candidates)

    def get(self, candidate_id: str) -> CandidateDocument | None:
        """Lookup candidate document by candidate ID."""
        for candidate in self.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        return None

    def find_by_profile(self, profile_id: str) -> tuple[CandidateDocument, ...]:
        """Return candidates matching the specified profile ID."""
        return tuple(c for c in self.candidates if c.profile_id == profile_id)

    def find_by_image(self, image_id: str) -> tuple[CandidateDocument, ...]:
        """Return candidates matching the specified image ID."""
        return tuple(c for c in self.candidates if c.image_id == image_id)

    def enumerate_candidates(self) -> list[dict[str, Any]]:
        """Diagnostic helper: summarize all candidates in the corpus."""
        return [
            {
                "candidate_id": c.candidate_id,
                "profile_id": c.profile_id,
                "image_id": c.image_id,
                "display_name": c.display_name,
                "cpu_limit_cores": c.resource_metadata.cpu_limit_cores,
                "memory_limit_gb": c.resource_metadata.memory_limit_gb,
                "gpu_count": c.resource_metadata.gpu_count,
                "task_types": [t.value for t in c.task_types],
                "frameworks": list(c.frameworks),
                "retrieval_text_length": len(c.retrieval_text),
            }
            for c in self.candidates
        ]

    def to_diagnostic_dict(self) -> dict[str, Any]:
        """Return comprehensive diagnostic view of the corpus."""
        return {
            "corpus_version": self.corpus_version,
            "source_image_catalog_version": self.source_image_catalog_version,
            "source_image_catalog_checksum": self.source_image_catalog_checksum,
            "source_profile_catalog_checksum": self.source_profile_catalog_checksum,
            "corpus_checksum": self.corpus_checksum,
            "policy_version": self.policy_version,
            "schema_version": self.schema_version,
            "candidate_count": len(self.candidates),
            "candidates": [c.to_dict() for c in self.candidates],
        }

    def to_json(self) -> str:
        """Return compact canonical JSON representation."""
        return json.dumps(
            self.to_diagnostic_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def build_candidate_corpus(
    *,
    image_catalog: Mapping[str, Any] | None = None,
    profile_catalog: Mapping[str, Any] | None = None,
    corpus_version: str = DEFAULT_CORPUS_VERSION,
    policy_version: str = POLICY_VERSION,
    allowed_combinations: Collection[tuple[str, str]] | None = None,
) -> CandidateCorpus:
    """Build deterministic CandidateCorpus from authoritative administrator catalogs.

    Parameters:
        image_catalog: validated image catalog mapping (defaults to loaded image-catalog.yaml).
        profile_catalog: profile definitions mapping (defaults to DEFAULT_PROFILE_DEFINITIONS).
        corpus_version: version identifier for the candidate corpus.
        policy_version: policy version identifier.
        allowed_combinations: optional collection of (profile_id, image_id) tuples restricting combinations.
    """
    valid_image_catalog = (
        validate_image_catalog(image_catalog)
        if image_catalog is not None
        else load_image_catalog()
    )

    valid_profile_catalog = (
        dict(profile_catalog)
        if profile_catalog is not None
        else DEFAULT_PROFILE_DEFINITIONS
    )

    image_catalog_checksum = canonical_json_checksum(valid_image_catalog)
    profile_catalog_checksum = canonical_json_checksum(valid_profile_catalog)

    images = valid_image_catalog["images"]
    catalog_version = valid_image_catalog["catalog_version"]

    candidates_list: list[CandidateDocument] = []

    for profile_id in sorted(valid_profile_catalog):
        profile_data = valid_profile_catalog[profile_id]
        if not isinstance(profile_data, Mapping):
            raise ContractValidationError(f"profile {profile_id!r} configuration must be a mapping")

        profile_display = str(profile_data.get("display_name", profile_id))
        profile_desc = str(profile_data.get("description", ""))
        resource_meta = CandidateResourceMetadata.from_profile_dict(profile_data)
        profile_suitability = list(profile_data.get("suitability_tags", []))

        for image_id in sorted(images):
            if allowed_combinations is not None and (profile_id, image_id) not in allowed_combinations:
                continue

            image_data = images[image_id]
            image_display = str(image_data.get("display_name", image_id))
            image_ref = str(image_data.get("reference", ""))
            image_desc = str(image_data.get("description", ""))
            image_caps = list(image_data.get("capabilities", []))
            image_terms = list(image_data.get("match_terms", []))

            # Retrieve rich metadata extensions
            ext = IMAGE_METADATA_EXTENSIONS.get(image_id, {})
            task_types = tuple(ext.get("task_types", [TaskType.OTHER]))
            frameworks = tuple(ext.get("frameworks", []))
            libraries = tuple(ext.get("libraries", image_caps))
            image_suitability = ext.get("suitability_tags", [])
            preference_tags = tuple(ext.get("preference_tags", []))

            candidate_id = f"{profile_id}-{image_id}"
            combined_display = f"{profile_display.split(':')[0].strip()} / {image_display}"
            combined_description = (
                f"{profile_display} ({resource_meta.cpu_limit_cores:g} CPU limit, "
                f"{resource_meta.memory_limit_gb:g}GB memory limit) with {image_display} "
                f"({image_desc})"
            )

            all_suitability = tuple(sorted(set(profile_suitability + image_suitability)))

            gpu_capability = (
                GPURequirement.REQUIRED
                if resource_meta.gpu_count > 0
                else GPURequirement.NOT_NEEDED
            )

            retrieval_text = generate_candidate_retrieval_text(
                candidate_id=candidate_id,
                profile_id=profile_id,
                profile_display_name=profile_display,
                image_id=image_id,
                image_display_name=image_display,
                image_reference=image_ref,
                description=combined_description,
                task_types=task_types,
                frameworks=frameworks,
                libraries=libraries,
                capabilities=tuple(image_caps),
                match_terms=tuple(image_terms),
                suitability_tags=all_suitability,
                preference_tags=preference_tags,
                resource_metadata=resource_meta,
            )

            doc = CandidateDocument(
                candidate_id=candidate_id,
                profile_id=profile_id,
                image_id=image_id,
                image_reference=image_ref,
                display_name=combined_display,
                description=combined_description,
                task_types=task_types,
                capabilities=tuple(image_caps),
                frameworks=frameworks,
                libraries=libraries,
                resource_metadata=resource_meta,
                gpu_capability=gpu_capability,
                suitability_tags=all_suitability,
                preference_tags=preference_tags,
                match_terms=tuple(image_terms),
                retrieval_text=retrieval_text,
                catalog_version=catalog_version,
                policy_version=policy_version,
            )
            candidates_list.append(doc)

    sorted_candidates = tuple(sorted(candidates_list, key=lambda c: c.candidate_id))

    # Compute corpus checksum from canonical candidates dict
    candidates_dict = [c.to_dict() for c in sorted_candidates]
    corpus_checksum = canonical_json_checksum(candidates_dict)

    corpus = CandidateCorpus(
        candidates=sorted_candidates,
        corpus_version=corpus_version,
        source_image_catalog_version=catalog_version,
        source_image_catalog_checksum=image_catalog_checksum,
        source_profile_catalog_checksum=profile_catalog_checksum,
        corpus_checksum=corpus_checksum,
        policy_version=policy_version,
    )
    validate_candidate_corpus(corpus, image_catalog=valid_image_catalog, profile_catalog=valid_profile_catalog)
    return corpus


def validate_candidate_corpus(
    corpus: CandidateCorpus,
    *,
    image_catalog: Mapping[str, Any] | None = None,
    profile_catalog: Mapping[str, Any] | None = None,
) -> CandidateCorpus:
    """Validate a CandidateCorpus against administrator catalogs and security rules."""
    if not isinstance(corpus, CandidateCorpus):
        raise ContractValidationError("corpus must be an instance of CandidateCorpus")

    if not corpus.candidates:
        raise ContractValidationError("candidate corpus must not be empty")

    # 1. Validate unique candidate IDs
    candidate_ids = [c.candidate_id for c in corpus.candidates]
    if len(set(candidate_ids)) != len(candidate_ids):
        duplicates = sorted({cid for cid in candidate_ids if candidate_ids.count(cid) > 1})
        raise ContractValidationError(f"duplicate candidate IDs detected: {', '.join(duplicates)}")

    # 2. Check reference integrity against image catalog
    if image_catalog is not None:
        valid_images = image_catalog.get("images", {})
        if not isinstance(valid_images, Mapping):
            raise ContractValidationError("image_catalog missing valid 'images' mapping")
        for c in corpus.candidates:
            if c.image_id not in valid_images:
                raise ContractValidationError(
                    f"candidate {c.candidate_id!r} references nonexistent image {c.image_id!r}"
                )
            expected_ref = valid_images[c.image_id].get("reference")
            if c.image_reference != expected_ref:
                raise ContractValidationError(
                    f"candidate {c.candidate_id!r} image_reference {c.image_reference!r} "
                    f"does not match catalog reference {expected_ref!r}"
                )
            if c.catalog_version != image_catalog.get("catalog_version"):
                raise ContractValidationError(
                    f"candidate {c.candidate_id!r} catalog_version {c.catalog_version!r} "
                    f"does not match image catalog version {image_catalog.get('catalog_version')!r}"
                )

    # 3. Check reference integrity against profile catalog
    if profile_catalog is not None:
        for c in corpus.candidates:
            if c.profile_id not in profile_catalog:
                raise ContractValidationError(
                    f"candidate {c.candidate_id!r} references nonexistent profile {c.profile_id!r}"
                )

    # 4. Verify candidate ordering and internal validity
    expected_sorted = tuple(sorted(corpus.candidates, key=lambda c: c.candidate_id))
    if corpus.candidates != expected_sorted:
        raise ContractValidationError("candidates in corpus must be sorted by candidate_id")

    return corpus


def load_candidate_corpus(
    *,
    catalog_path: str = str(DEFAULT_CATALOG_PATH),
    corpus_version: str = DEFAULT_CORPUS_VERSION,
) -> CandidateCorpus:
    """Convenience loader for default administrator candidate corpus."""
    image_catalog = load_image_catalog(catalog_path)
    return build_candidate_corpus(
        image_catalog=image_catalog,
        corpus_version=corpus_version,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enumerate and validate EnvironmentCandidate corpus.")
    parser.add_argument("--summary", action="store_true", help="Print human-readable candidate summary.")
    parser.add_argument("--json", action="store_true", help="Output canonical corpus JSON.")
    parser.add_argument("--validate", action="store_true", help="Perform strict validation.")
    parser.add_argument("--dump", action="store_true", help="Dump full candidate retrieval texts.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    corpus = load_candidate_corpus()

    if args.validate:
        validate_candidate_corpus(corpus)
        print(f"Validation successful: {len(corpus)} valid candidates.")

    if args.summary:
        print(f"Candidate Corpus Version: {corpus.corpus_version}")
        print(f"Image Catalog Version: {corpus.source_image_catalog_version}")
        print(f"Corpus Checksum: {corpus.corpus_checksum}")
        print(f"Total Candidates: {len(corpus)}\n")
        for item in corpus.enumerate_candidates():
            print(
                f" - {item['candidate_id']:<35} Profile: {item['profile_id']:<8} "
                f"Image: {item['image_id']:<26} CPU limit: {item['cpu_limit_cores']} "
                f"Mem limit: {item['memory_limit_gb']}GB"
            )

    elif args.dump:
        for c in corpus:
            print("=" * 70)
            print(c.retrieval_text)
            print()

    elif args.json:
        print(corpus.to_json())

    else:
        # Default output
        print(f"Corpus loaded: {len(corpus)} candidates, checksum={corpus.corpus_checksum[:16]}...")


if __name__ == "__main__":
    main()
