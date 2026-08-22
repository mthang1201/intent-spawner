"""Shared input and output contracts for spawn recommendation backends.

The P2 contracts in this module are deliberately data-only.  In particular,
``EnvironmentCandidate`` carries administrator-owned identifiers, never an
image reference, Kubernetes object, or directly applicable resource values.
Catalog resolution and :class:`recommender.policy.PolicyValidator` therefore
remain mandatory before a recommendation can affect KubeSpawner.

Semantic text normalization is uniform: Unicode NFKC, surrounding-whitespace
removal, internal-whitespace collapse, and Unicode case-folding.  Semantic
collections are then deduplicated and sorted.  A required feature takes
precedence over the same preferred feature, while required/forbidden overlap
is rejected as contradictory.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
import json
import math
import re
import unicodedata
from typing import Any, ClassVar, TypeVar


POLICY_VERSION = "resource-image-policy-v1"
SCHEMA_VERSION = "spawn-recommendation-v1"

STRUCTURED_INTENT_SCHEMA_VERSION = "structured-intent-v1"
EXTRACTION_PROVENANCE_SCHEMA_VERSION = "structured-intent-provenance-v1"
RESOURCE_CONSTRAINTS_SCHEMA_VERSION = "resource-constraints-v1"
ENVIRONMENT_CANDIDATE_SCHEMA_VERSION = "environment-candidate-v1"
RETRIEVAL_HIT_SCHEMA_VERSION = "retrieval-hit-v1"
SOFT_PREFERENCE_COMPONENT_SCHEMA_VERSION = "soft-preference-component-v1"
CONSTRAINT_EVALUATION_SCHEMA_VERSION = "constraint-evaluation-v2"
RANKED_CANDIDATE_SCHEMA_VERSION = "ranked-candidate-v1"
RECOMMENDATION_TRACE_SCHEMA_VERSION = "recommendation-trace-v1"

_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_ContractT = TypeVar("_ContractT", bound="_VersionedContract")


class ContractValidationError(ValueError):
    """A P2 contract failed strict schema or semantic validation."""


class TaskType(str, Enum):
    """Versioned semantic workload taxonomy used by ``StructuredIntent``."""

    DATA_ANALYSIS = "data_analysis"
    DATA_PROCESSING = "data_processing"
    VISUALIZATION = "visualization"
    MACHINE_LEARNING = "machine_learning"
    DEEP_LEARNING = "deep_learning"
    MODEL_TRAINING = "model_training"
    MODEL_INFERENCE = "model_inference"
    SCIENTIFIC_COMPUTING = "scientific_computing"
    SOFTWARE_DEVELOPMENT = "software_development"
    EDUCATION = "education"
    OTHER = "other"
    UNSPECIFIED = "unspecified"


class GPURequirement(str, Enum):
    """Whether a GPU is a hard constraint, preference, or irrelevant."""

    REQUIRED = "required"
    PREFERRED = "preferred"
    FORBIDDEN = "forbidden"
    NOT_NEEDED = "not_needed"
    UNSPECIFIED = "unspecified"


class ExtractionMode(str, Enum):
    """Whether semantic fields came from the primary extractor or safe degradation."""

    PRIMARY = "primary"
    DETERMINISTIC_DEGRADED = "deterministic_degraded"


class RetrievalSource(str, Enum):
    """The retrieval channel that produced a candidate hit."""

    SPARSE = "sparse"
    DENSE = "dense"
    FUSED = "fused"


def _normalized_text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(f"{label} must be a string")
    normalized = _WHITESPACE_PATTERN.sub(
        " ", unicodedata.normalize("NFKC", value).strip()
    ).casefold()
    if not normalized and not allow_empty:
        raise ContractValidationError(f"{label} must not be blank")
    return normalized


def _normalized_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ContractValidationError(
            f"{label} must be a lowercase administrator/catalog identifier"
        )
    return value


def _version(value: object, label: str) -> str:
    if not isinstance(value, str) or not _VERSION_PATTERN.fullmatch(value):
        raise ContractValidationError(f"{label} must be a machine-readable version")
    return value


def _provenance_label(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(f"{label} must be a string")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized or len(normalized) > 256 or any(
        unicodedata.category(character).startswith("C") for character in normalized
    ):
        raise ContractValidationError(f"{label} must be a bounded printable value")
    return normalized


def _schema_version(value: object, expected: str) -> str:
    if value != expected:
        raise ContractValidationError(
            f"unsupported schema_version {value!r}; expected {expected!r}"
        )
    return expected


def _finite_number(
    value: object,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractValidationError(f"{label} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ContractValidationError(f"{label} must be finite")
    if minimum is not None and normalized < minimum:
        raise ContractValidationError(f"{label} must be >= {minimum:g}")
    if maximum is not None and normalized > maximum:
        raise ContractValidationError(f"{label} must be <= {maximum:g}")
    # Canonicalize negative zero so semantically equal inputs serialize equally.
    return 0.0 if normalized == 0 else normalized


def _optional_non_negative_number(value: object, label: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, label, minimum=0.0)


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractValidationError(f"{label} must be a positive integer")
    return value


def _normalized_strings(value: object, label: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Collection):
        raise ContractValidationError(f"{label} must be a collection of strings")
    normalized = {_normalized_text(item, f"{label} item") for item in value}
    return tuple(sorted(normalized))


def _enum_value(value: object, enum_type: type[Enum], label: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        normalized = _normalized_text(value, label)
        try:
            return enum_type(normalized)
        except ValueError:
            pass
    supported = ", ".join(item.value for item in enum_type)
    raise ContractValidationError(f"{label} must be one of: {supported}")


def _enum_values(
    value: object,
    enum_type: type[Enum],
    label: str,
) -> tuple[Enum, ...]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Collection):
        raise ContractValidationError(f"{label} must be a collection")
    normalized = {_enum_value(item, enum_type, f"{label} item") for item in value}
    return tuple(sorted(normalized, key=lambda item: item.value))


def _serialize(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _serialize(item) for key, item in sorted(value.items())}
    if is_dataclass(value):
        return {
            item.name: _serialize(getattr(value, item.name))
            for item in fields(value)
        }
    return value


def _strict_json_loads(payload: str) -> object:
    if not isinstance(payload, str):
        raise ContractValidationError("contract JSON must be a string")

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ContractValidationError(f"duplicate JSON field {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(payload, object_pairs_hook=object_pairs)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ContractValidationError("contract JSON is invalid") from exc


class _VersionedContract:
    """Small dependency-free validation/serialization surface for P2 schemas."""

    __slots__ = ()
    SUPPORTED_SCHEMA_VERSION: ClassVar[str]

    def to_dict(self) -> dict[str, object]:
        """Return normalized fields in deterministic declaration order."""

        serialized = _serialize(self)
        assert isinstance(serialized, dict)
        return serialized

    def to_json(self) -> str:
        """Return canonical compact JSON for hashing and reproducible traces."""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_dict(cls: type[_ContractT], payload: object) -> _ContractT:
        """Strictly validate an untrusted mapping with no unknown fields."""

        if not isinstance(payload, Mapping):
            raise ContractValidationError(f"{cls.__name__} payload must be a mapping")
        if not all(isinstance(key, str) for key in payload):
            raise ContractValidationError(
                f"{cls.__name__} field names must be strings"
            )
        allowed = {item.name for item in fields(cls)}
        unknown = set(payload) - allowed
        if unknown:
            raise ContractValidationError(
                f"{cls.__name__} contains unknown fields: {', '.join(sorted(unknown))}"
            )
        try:
            return cls(**dict(payload))
        except TypeError as exc:
            raise ContractValidationError(f"{cls.__name__} fields are invalid") from exc

    @classmethod
    def from_json(cls: type[_ContractT], payload: str) -> _ContractT:
        """Strictly validate JSON, including rejection of duplicate keys."""

        return cls.from_dict(_strict_json_loads(payload))


@dataclass(frozen=True, slots=True)
class ResourceConstraints(_VersionedContract):
    """Semantic lower bounds; values are not directly applicable resources."""

    gpu_requirement: GPURequirement = GPURequirement.UNSPECIFIED
    minimum_cpu_cores: float | None = None
    minimum_memory_gb: float | None = None
    dataset_size_gb: float | None = None
    schema_version: str = RESOURCE_CONSTRAINTS_SCHEMA_VERSION

    SUPPORTED_SCHEMA_VERSION: ClassVar[str] = RESOURCE_CONSTRAINTS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "gpu_requirement",
            _enum_value(self.gpu_requirement, GPURequirement, "gpu_requirement"),
        )
        object.__setattr__(
            self,
            "minimum_cpu_cores",
            _optional_non_negative_number(
                self.minimum_cpu_cores, "minimum_cpu_cores"
            ),
        )
        object.__setattr__(
            self,
            "minimum_memory_gb",
            _optional_non_negative_number(
                self.minimum_memory_gb, "minimum_memory_gb"
            ),
        )
        object.__setattr__(
            self,
            "dataset_size_gb",
            _optional_non_negative_number(self.dataset_size_gb, "dataset_size_gb"),
        )
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, self.SUPPORTED_SCHEMA_VERSION),
        )


@dataclass(frozen=True, slots=True)
class ExtractionProvenance(_VersionedContract):
    """Trusted provenance attached by extractor code, never supplied by a model."""

    extractor_name: str = "unattributed"
    extractor_version: str = "unattributed-v1"
    prompt_version: str | None = None
    prompt_sha256: str | None = None
    model_id: str | None = None
    mode: ExtractionMode = ExtractionMode.DETERMINISTIC_DEGRADED
    degraded_reason: str | None = "unattributed"
    conflicts: tuple[str, ...] = ()
    schema_version: str = EXTRACTION_PROVENANCE_SCHEMA_VERSION

    SUPPORTED_SCHEMA_VERSION: ClassVar[str] = EXTRACTION_PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "extractor_name", _normalized_identifier(self.extractor_name, "extractor_name")
        )
        object.__setattr__(
            self,
            "extractor_version",
            _version(self.extractor_version, "extractor_version"),
        )
        if self.prompt_version is not None:
            object.__setattr__(
                self,
                "prompt_version",
                _version(self.prompt_version, "prompt_version"),
            )
        if self.model_id is not None:
            object.__setattr__(
                self, "model_id", _provenance_label(self.model_id, "model_id")
            )
        if self.prompt_sha256 is not None:
            if not isinstance(self.prompt_sha256, str) or not re.fullmatch(
                r"[0-9a-f]{64}", self.prompt_sha256
            ):
                raise ContractValidationError(
                    "prompt_sha256 must be a lowercase SHA-256 digest"
                )
        object.__setattr__(
            self, "mode", _enum_value(self.mode, ExtractionMode, "mode")
        )
        if self.degraded_reason is not None:
            object.__setattr__(
                self,
                "degraded_reason",
                _normalized_identifier(self.degraded_reason, "degraded_reason"),
            )
        if self.mode is ExtractionMode.PRIMARY and self.degraded_reason is not None:
            raise ContractValidationError(
                "primary extraction cannot contain a degraded_reason"
            )
        if (
            self.mode is ExtractionMode.DETERMINISTIC_DEGRADED
            and self.degraded_reason is None
        ):
            raise ContractValidationError(
                "deterministic degraded extraction requires a degraded_reason"
            )
        object.__setattr__(
            self, "conflicts", _normalized_strings(self.conflicts, "conflicts")
        )
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, self.SUPPORTED_SCHEMA_VERSION),
        )


@dataclass(frozen=True, slots=True)
class StructuredIntent(_VersionedContract):
    """Normalized semantic facts extracted from a recommendation request.

    Profile IDs, image IDs/references, and Kubernetes resources are
    intentionally absent from this schema.
    """

    task_types: tuple[TaskType, ...] = ()
    required_features: tuple[str, ...] = ()
    preferred_features: tuple[str, ...] = ()
    forbidden_features: tuple[str, ...] = ()
    required_frameworks: tuple[str, ...] = ()
    preferred_frameworks: tuple[str, ...] = ()
    required_libraries: tuple[str, ...] = ()
    preferred_libraries: tuple[str, ...] = ()
    resource_constraints: ResourceConstraints = field(default_factory=ResourceConstraints)
    ambiguities: tuple[str, ...] = ()
    normalized_query: str = ""
    extraction_confidence: float = 0.0
    extraction_provenance: ExtractionProvenance = field(
        default_factory=ExtractionProvenance
    )
    schema_version: str = STRUCTURED_INTENT_SCHEMA_VERSION

    SUPPORTED_SCHEMA_VERSION: ClassVar[str] = STRUCTURED_INTENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "task_types",
            _enum_values(self.task_types, TaskType, "task_types"),
        )
        for name in (
            "required_features",
            "preferred_features",
            "forbidden_features",
            "required_frameworks",
            "preferred_frameworks",
            "required_libraries",
            "preferred_libraries",
            "ambiguities",
        ):
            object.__setattr__(
                self, name, _normalized_strings(getattr(self, name), name)
            )

        required_features = set(self.required_features)
        forbidden_features = set(self.forbidden_features)
        conflict = required_features & forbidden_features
        if conflict:
            raise ContractValidationError(
                "features cannot be both required and forbidden: "
                + ", ".join(sorted(conflict))
            )
        object.__setattr__(
            self,
            "preferred_features",
            tuple(
                item
                for item in self.preferred_features
                if item not in required_features and item not in forbidden_features
            ),
        )
        object.__setattr__(
            self,
            "preferred_frameworks",
            tuple(
                item
                for item in self.preferred_frameworks
                if item not in set(self.required_frameworks)
            ),
        )
        object.__setattr__(
            self,
            "preferred_libraries",
            tuple(
                item
                for item in self.preferred_libraries
                if item not in set(self.required_libraries)
            ),
        )
        if not isinstance(self.resource_constraints, ResourceConstraints):
            raise ContractValidationError(
                "resource_constraints must be a ResourceConstraints object"
            )
        object.__setattr__(
            self,
            "normalized_query",
            _normalized_text(
                self.normalized_query, "normalized_query", allow_empty=True
            ),
        )
        object.__setattr__(
            self,
            "extraction_confidence",
            _finite_number(
                self.extraction_confidence,
                "extraction_confidence",
                minimum=0.0,
                maximum=1.0,
            ),
        )
        if not isinstance(self.extraction_provenance, ExtractionProvenance):
            raise ContractValidationError(
                "extraction_provenance must be an ExtractionProvenance object"
            )
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, self.SUPPORTED_SCHEMA_VERSION),
        )

    @classmethod
    def from_dict(cls, payload: object) -> "StructuredIntent":
        if not isinstance(payload, Mapping):
            raise ContractValidationError("StructuredIntent payload must be a mapping")
        values = dict(payload)
        constraints = values.get("resource_constraints")
        if isinstance(constraints, Mapping):
            values["resource_constraints"] = ResourceConstraints.from_dict(constraints)
        provenance = values.get("extraction_provenance")
        if isinstance(provenance, Mapping):
            values["extraction_provenance"] = ExtractionProvenance.from_dict(provenance)
        return super().from_dict(values)


@dataclass(frozen=True, slots=True)
class EnvironmentCandidate(_VersionedContract):
    """Trusted catalog references selected after deterministic ranking.

    The identifiers still require resolution against administrator-owned
    catalogs and conversion to ``SpawnRecommendation`` followed by
    ``PolicyValidator``.  This object cannot carry an image reference or
    Kubernetes resource assignment.
    """

    candidate_id: str
    profile_id: str
    image_id: str
    catalog_version: str
    policy_version: str
    schema_version: str = ENVIRONMENT_CANDIDATE_SCHEMA_VERSION

    SUPPORTED_SCHEMA_VERSION: ClassVar[str] = ENVIRONMENT_CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("candidate_id", "profile_id", "image_id"):
            object.__setattr__(
                self, name, _normalized_identifier(getattr(self, name), name)
            )
        object.__setattr__(
            self, "catalog_version", _version(self.catalog_version, "catalog_version")
        )
        object.__setattr__(
            self, "policy_version", _version(self.policy_version, "policy_version")
        )
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, self.SUPPORTED_SCHEMA_VERSION),
        )


@dataclass(frozen=True, slots=True)
class RetrievalHit(_VersionedContract):
    """One candidate identifier returned by a versioned retrieval channel."""

    candidate_id: str
    source: RetrievalSource
    rank: int
    score: float
    retriever_version: str
    index_version: str
    schema_version: str = RETRIEVAL_HIT_SCHEMA_VERSION

    SUPPORTED_SCHEMA_VERSION: ClassVar[str] = RETRIEVAL_HIT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _normalized_identifier(self.candidate_id, "candidate_id")
        )
        object.__setattr__(
            self, "source", _enum_value(self.source, RetrievalSource, "source")
        )
        object.__setattr__(self, "rank", _positive_integer(self.rank, "rank"))
        object.__setattr__(
            self, "score", _finite_number(self.score, "score", minimum=0.0)
        )
        object.__setattr__(
            self,
            "retriever_version",
            _version(self.retriever_version, "retriever_version"),
        )
        object.__setattr__(
            self, "index_version", _version(self.index_version, "index_version")
        )
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, self.SUPPORTED_SCHEMA_VERSION),
        )


@dataclass(frozen=True, slots=True)
class SoftPreferenceComponent(_VersionedContract):
    """One reproducible component of a candidate's normalized soft score."""

    preference: str
    matched: bool
    weight: float
    score: float
    explanation_code: str
    schema_version: str = SOFT_PREFERENCE_COMPONENT_SCHEMA_VERSION

    SUPPORTED_SCHEMA_VERSION: ClassVar[str] = SOFT_PREFERENCE_COMPONENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "preference", _normalized_text(self.preference, "preference")
        )
        if not isinstance(self.matched, bool):
            raise ContractValidationError("matched must be a boolean")
        object.__setattr__(
            self, "weight", _finite_number(self.weight, "weight", minimum=0.0)
        )
        if self.weight <= 0.0:
            raise ContractValidationError("weight must be positive")
        object.__setattr__(
            self,
            "score",
            _finite_number(self.score, "score", minimum=0.0, maximum=self.weight),
        )
        expected_score = self.weight if self.matched else 0.0
        if not math.isclose(self.score, expected_score, rel_tol=0.0, abs_tol=1e-12):
            raise ContractValidationError(
                "soft preference score must equal weight when matched and zero otherwise"
            )
        object.__setattr__(
            self,
            "explanation_code",
            _normalized_identifier(self.explanation_code, "explanation_code"),
        )
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, self.SUPPORTED_SCHEMA_VERSION),
        )


@dataclass(frozen=True, slots=True)
class ConstraintEvaluation(_VersionedContract):
    """Versioned deterministic constraint result for one catalog candidate."""

    candidate_id: str
    feasible: bool
    matched_hard_constraints: tuple[str, ...]
    violated_hard_constraints: tuple[str, ...]
    unsupported_constraints: tuple[str, ...]
    soft_preference_score: float
    soft_preference_components: tuple[SoftPreferenceComponent, ...]
    explanation_codes: tuple[str, ...]
    evaluator_version: str
    constraint_policy_version: str
    schema_version: str = CONSTRAINT_EVALUATION_SCHEMA_VERSION

    SUPPORTED_SCHEMA_VERSION: ClassVar[str] = CONSTRAINT_EVALUATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _normalized_identifier(self.candidate_id, "candidate_id")
        )
        if not isinstance(self.feasible, bool):
            raise ContractValidationError("feasible must be a boolean")
        for name in (
            "matched_hard_constraints",
            "violated_hard_constraints",
            "unsupported_constraints",
            "explanation_codes",
        ):
            object.__setattr__(
                self, name, _normalized_strings(getattr(self, name), name)
            )
        overlap = set(self.matched_hard_constraints) & set(
            self.violated_hard_constraints
        )
        if overlap:
            raise ContractValidationError(
                "constraints cannot be both satisfied and violated: "
                + ", ".join(sorted(overlap))
            )
        if self.feasible and self.violated_hard_constraints:
            raise ContractValidationError(
                "a feasible candidate cannot contain violated constraints"
            )
        if not self.feasible and not self.violated_hard_constraints:
            raise ContractValidationError(
                "an infeasible candidate requires at least one violated constraint"
            )
        components = tuple(self.soft_preference_components)
        if not all(isinstance(item, SoftPreferenceComponent) for item in components):
            raise ContractValidationError(
                "soft_preference_components must contain only SoftPreferenceComponent objects"
            )
        component_names = [item.preference for item in components]
        if len(set(component_names)) != len(component_names):
            raise ContractValidationError("soft preference components must be unique")
        components = tuple(sorted(components, key=lambda item: item.preference))
        object.__setattr__(self, "soft_preference_components", components)
        expected_soft_score = (
            sum(item.score for item in components)
            / sum(item.weight for item in components)
            if components
            else 0.0
        )
        object.__setattr__(
            self,
            "soft_preference_score",
            _finite_number(
                self.soft_preference_score,
                "soft_preference_score",
                minimum=0.0,
                maximum=1.0,
            ),
        )
        if not math.isclose(
            self.soft_preference_score,
            expected_soft_score,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ContractValidationError(
                "soft_preference_score must equal the normalized component score"
            )
        object.__setattr__(
            self,
            "evaluator_version",
            _version(self.evaluator_version, "evaluator_version"),
        )
        object.__setattr__(
            self,
            "constraint_policy_version",
            _version(self.constraint_policy_version, "constraint_policy_version"),
        )
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, self.SUPPORTED_SCHEMA_VERSION),
        )

    @classmethod
    def from_dict(cls, payload: object) -> "ConstraintEvaluation":
        if not isinstance(payload, Mapping):
            raise ContractValidationError("ConstraintEvaluation payload must be a mapping")
        values = dict(payload)
        components = values.get("soft_preference_components")
        if isinstance(components, list):
            values["soft_preference_components"] = tuple(
                SoftPreferenceComponent.from_dict(item)
                if isinstance(item, Mapping)
                else item
                for item in components
            )
        return super().from_dict(values)


@dataclass(frozen=True, slots=True)
class RankedCandidate(_VersionedContract):
    """Deterministically ranked feasible candidate identifier."""

    candidate_id: str
    rank: int
    score: float
    ranking_reasons: tuple[str, ...]
    ranker_version: str
    schema_version: str = RANKED_CANDIDATE_SCHEMA_VERSION

    SUPPORTED_SCHEMA_VERSION: ClassVar[str] = RANKED_CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _normalized_identifier(self.candidate_id, "candidate_id")
        )
        object.__setattr__(self, "rank", _positive_integer(self.rank, "rank"))
        object.__setattr__(
            self, "score", _finite_number(self.score, "score", minimum=0.0)
        )
        object.__setattr__(
            self,
            "ranking_reasons",
            _normalized_strings(self.ranking_reasons, "ranking_reasons"),
        )
        object.__setattr__(
            self, "ranker_version", _version(self.ranker_version, "ranker_version")
        )
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, self.SUPPORTED_SCHEMA_VERSION),
        )


@dataclass(frozen=True, slots=True)
class RecommendationTrace(_VersionedContract):
    """Versioned P2 stage outputs without raw retrieved documents.

    A trace contains normalized semantic intent and is therefore research/debug
    data, not an operational log or pod-metadata payload.
    """

    pipeline_version: str
    catalog_version: str
    index_version: str
    structured_intent: StructuredIntent
    retrieval_hits: tuple[RetrievalHit, ...] = ()
    constraint_evaluations: tuple[ConstraintEvaluation, ...] = ()
    ranked_candidates: tuple[RankedCandidate, ...] = ()
    selected_candidate: EnvironmentCandidate | None = None
    schema_version: str = RECOMMENDATION_TRACE_SCHEMA_VERSION

    SUPPORTED_SCHEMA_VERSION: ClassVar[str] = RECOMMENDATION_TRACE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "pipeline_version", _version(self.pipeline_version, "pipeline_version")
        )
        object.__setattr__(
            self, "catalog_version", _version(self.catalog_version, "catalog_version")
        )
        object.__setattr__(
            self, "index_version", _version(self.index_version, "index_version")
        )
        if not isinstance(self.structured_intent, StructuredIntent):
            raise ContractValidationError(
                "structured_intent must be a StructuredIntent object"
            )
        hits = self._objects(self.retrieval_hits, RetrievalHit, "retrieval_hits")
        evaluations = self._objects(
            self.constraint_evaluations,
            ConstraintEvaluation,
            "constraint_evaluations",
        )
        ranked = self._objects(
            self.ranked_candidates, RankedCandidate, "ranked_candidates"
        )
        hits = tuple(sorted(hits, key=lambda item: (item.source.value, item.rank, item.candidate_id)))
        evaluations = tuple(sorted(evaluations, key=lambda item: item.candidate_id))
        ranked = tuple(sorted(ranked, key=lambda item: (item.rank, item.candidate_id)))
        object.__setattr__(self, "retrieval_hits", hits)
        object.__setattr__(self, "constraint_evaluations", evaluations)
        object.__setattr__(self, "ranked_candidates", ranked)

        if any(item.index_version != self.index_version for item in hits):
            raise ContractValidationError(
                "all retrieval hits must use the trace index_version"
            )
        self._unique(
            ((item.source.value, item.candidate_id) for item in hits),
            "retrieval source/candidate pairs",
        )
        self._unique(
            ((item.source.value, item.rank) for item in hits),
            "retrieval source/ranks",
        )
        self._unique(
            (item.candidate_id for item in evaluations),
            "constraint candidate IDs",
        )
        self._unique(
            (item.candidate_id for item in ranked), "ranked candidate IDs"
        )
        self._unique((item.rank for item in ranked), "candidate ranks")
        if ranked and [item.rank for item in ranked] != list(range(1, len(ranked) + 1)):
            raise ContractValidationError("ranked candidate ranks must be contiguous from 1")

        if self.selected_candidate is not None:
            if not isinstance(self.selected_candidate, EnvironmentCandidate):
                raise ContractValidationError(
                    "selected_candidate must be an EnvironmentCandidate or None"
                )
            if self.selected_candidate.catalog_version != self.catalog_version:
                raise ContractValidationError(
                    "selected candidate must use the trace catalog_version"
                )
            if not ranked or self.selected_candidate.candidate_id != ranked[0].candidate_id:
                raise ContractValidationError(
                    "selected candidate must be the rank-1 candidate"
                )
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, self.SUPPORTED_SCHEMA_VERSION),
        )

    @staticmethod
    def _objects(value: object, expected_type: type, label: str) -> tuple:
        if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Collection):
            raise ContractValidationError(f"{label} must be a collection")
        items = tuple(value)
        if not all(isinstance(item, expected_type) for item in items):
            raise ContractValidationError(
                f"{label} must contain only {expected_type.__name__} objects"
            )
        return items

    @staticmethod
    def _unique(values: Any, label: str) -> None:
        items = list(values)
        if len(set(items)) != len(items):
            raise ContractValidationError(f"{label} must be unique")

    @classmethod
    def from_dict(cls, payload: object) -> "RecommendationTrace":
        if not isinstance(payload, Mapping):
            raise ContractValidationError("RecommendationTrace payload must be a mapping")
        values = dict(payload)
        nested = values.get("structured_intent")
        if isinstance(nested, Mapping):
            values["structured_intent"] = StructuredIntent.from_dict(nested)
        converters = {
            "retrieval_hits": RetrievalHit,
            "constraint_evaluations": ConstraintEvaluation,
            "ranked_candidates": RankedCandidate,
        }
        for name, contract in converters.items():
            items = values.get(name)
            if isinstance(items, list):
                values[name] = tuple(
                    contract.from_dict(item) if isinstance(item, Mapping) else item
                    for item in items
                )
        selected = values.get("selected_candidate")
        if isinstance(selected, Mapping):
            values["selected_candidate"] = EnvironmentCandidate.from_dict(selected)
        return super().from_dict(values)


@dataclass(frozen=True)
class RecommendationRequest:
    """Permitted pre-spawn context supplied to a recommender."""

    intent: str = ""
    dataset_size_gb: float | int | str | None = None
    code_context: str = ""


@dataclass(frozen=True)
class SpawnRecommendation:
    """Backend-neutral recommendation consumed by UI and policy layers."""

    profile: str
    reasons: list[str]
    score: int | float | None
    image_id: str
    image_reference: str
    image_reasons: list[str]
    catalog_version: str
    policy_version: str = POLICY_VERSION
    schema_version: str = SCHEMA_VERSION
    backend_name: str = "rule_based"
    backend_version: str = "rule-based-v1"

    def to_dict(self) -> dict[str, object]:
        """Return the exact legacy serialization used by existing callers."""

        return {
            "profile": self.profile,
            "reasons": self.reasons,
            "score": self.score,
            "image_id": self.image_id,
            "image_reference": self.image_reference,
            "image_reasons": self.image_reasons,
            "catalog_version": self.catalog_version,
            "policy_version": self.policy_version,
        }

    def to_unified_dict(self) -> dict[str, object]:
        """Return the versioned schema shared by every backend."""

        payload = self.to_dict()
        payload.update(
            {
                "schema_version": self.schema_version,
                "backend_name": self.backend_name,
                "backend_version": self.backend_version,
            }
        )
        return payload


# Backward-compatible public name.
Recommendation = SpawnRecommendation
