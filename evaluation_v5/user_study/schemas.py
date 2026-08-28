"""Strict, versioned contracts for the Protocol-v5 E3 user study.

The authoritative task-set contract deliberately keeps the gold requirements at
the matched-pair level.  :func:`browser_safe_task_set` creates a different,
gold-free projection for presentation in JupyterHub.  Event records contain no
free-form participant input and use one fixed field set so staging JSONL can be
validated without interpreting UI-specific payloads.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
import re
import uuid
from typing import Any, ClassVar


TASK_SET_SCHEMA_VERSION = "protocol-v5-user-study-task-set-v1.0.0"
BROWSER_TASK_SET_SCHEMA_VERSION = (
    "protocol-v5-user-study-browser-task-set-v1.0.0"
)
ASSIGNMENT_SCHEMA_VERSION = "protocol-v5-user-study-assignment-v1.1.0"
EVENT_SCHEMA_VERSION = "protocol-v5-user-study-event-v1.0.0"
STUDY_TIMING_CONTRACT_VERSION = "protocol-v5-user-study-timing-contract-v1.0.0"
STUDY_TIMING_CONTRACT = {
    "version": STUDY_TIMING_CONTRACT_VERSION,
    "decision_time_nonconfirmation_bound_seconds": 600.0,
    "decision_time_nonconfirmation_bound_semantics": (
        "server_enforced_task_decision_limit_and_secondary_analysis_bound_only"
    ),
}
STUDY_TIMING_CONTRACT_SHA256 = hashlib.sha256(
    json.dumps(
        STUDY_TIMING_CONTRACT,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
# Compatibility name used by the Hub and event validator. The only numeric
# declaration is in the frozen timing contract above.
DECISION_LIMIT_SECONDS = float(
    STUDY_TIMING_CONTRACT["decision_time_nonconfirmation_bound_seconds"]
)
READINESS_LIMIT_SECONDS = 180.0
# Only absorbs binary floating-point serialization at an exact boundary; it is
# not an operational grace period.
_TIMING_EPSILON_SECONDS = 1e-6

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PARTICIPANT_ID = re.compile(r"^P-[0-9a-f]{12}$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRESCRIBED_WORDING = re.compile(
    r"\b(?:type|enter|say|paste)\s+(?:this|exactly|the following)\b",
    re.IGNORECASE,
)
_FORBIDDEN_TASK_FIELDS = frozenset(
    {
        "exact_input",
        "exact_sentence",
        "intent",
        "intent_text",
        "prescribed_intent",
        "prompt",
        "suggested_input",
        "suggested_intent",
        "suggested_wording",
    }
)


class UserStudyValidationError(ValueError):
    """A user-study artifact violates its versioned, fail-closed contract."""


class Condition(str, Enum):
    """The two frozen study conditions."""

    B0 = "B0"
    P2 = "P2"


class TaskPhase(str, Enum):
    """Whether a task is practice-only or contributes to outcomes."""

    WARM_UP = "warm_up"
    MEASURED = "measured"


class TaskSetStage(str, Enum):
    """Permitted use of a task set."""

    DEVELOPMENT = "development"
    CONFIRMATORY = "confirmatory"


class TaskSetStatus(str, Enum):
    """Authoring lifecycle of the task set."""

    DRAFT = "draft"
    FROZEN = "frozen"


class ReviewStatus(str, Enum):
    """Independent matched-pair equivalence review state."""

    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


class Difficulty(str, Enum):
    """Coarse authoring stratum used to match task variants."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class EventType(str, Enum):
    """Closed event vocabulary for content-free study instrumentation."""

    TASK_SHOWN = "task_shown"
    INTENT_FOCUS = "intent_focus"
    INTENT_EDIT = "intent_edit"
    PREVIEW_REQUESTED = "preview_requested"
    PREVIEW_RECEIVED = "preview_received"
    PROFILE_CHANGED = "profile_changed"
    IMAGE_CHANGED = "image_changed"
    OVERRIDE = "override"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    NOTEBOOK_READY = "notebook_ready"


class PreviewStatus(str, Enum):
    """Outcome of a P2 preview request."""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


class CancelReason(str, Enum):
    """Content-free reasons why a trial did not reach confirmation."""

    PARTICIPANT_CANCELLED = "participant_cancelled"
    DECISION_TIMEOUT = "decision_timeout"
    CONSENT_WITHDRAWAL = "consent_withdrawal"
    INSTRUMENTATION_FAILURE = "instrumentation_failure"
    HUB_RESTART = "hub_restart"
    SESSION_TERMINATED = "session_terminated"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise UserStudyValidationError(message)


def _exact_mapping(
    value: object,
    expected: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise UserStudyValidationError(f"{label} must be an object")
    keys = set(value)
    forbidden = sorted(keys & _FORBIDDEN_TASK_FIELDS)
    if forbidden:
        raise UserStudyValidationError(
            f"{label} contains prescribed-intent fields: {', '.join(forbidden)}"
        )
    missing = sorted(expected - keys)
    extra = sorted(keys - expected)
    if missing:
        raise UserStudyValidationError(
            f"{label} missing fields: {', '.join(missing)}"
        )
    if extra:
        raise UserStudyValidationError(
            f"{label} unexpected fields: {', '.join(extra)}"
        )
    return dict(value)


def _nonblank(value: object, label: str, *, maximum: int = 4096) -> str:
    _require(isinstance(value, str), f"{label} must be a string")
    normalized = value.strip()
    _require(bool(normalized), f"{label} must be non-blank")
    _require(len(normalized) <= maximum, f"{label} is too long")
    _require("\x00" not in normalized, f"{label} contains a NUL character")
    return normalized


def _safe_id(value: object, label: str) -> str:
    normalized = _nonblank(value, label, maximum=128)
    _require(bool(_SAFE_ID.fullmatch(normalized)), f"{label} must be a safe identifier")
    return normalized


def _enum(enum_type: type[Enum], value: object, label: str) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise UserStudyValidationError(f"{label} is unsupported") from exc


def _string_tuple(
    value: object,
    label: str,
    *,
    safe_ids: bool,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    _require(
        isinstance(value, Sequence) and not isinstance(value, (str, bytes)),
        f"{label} must be an array",
    )
    values = tuple(
        _safe_id(item, f"{label}[{index}]")
        if safe_ids
        else _nonblank(item, f"{label}[{index}]", maximum=512)
        for index, item in enumerate(value)
    )
    _require(allow_empty or bool(values), f"{label} cannot be empty")
    _require(len(set(values)) == len(values), f"{label} must not contain duplicates")
    return values


def _as_finite_json(value: object, label: str) -> dict[str, Any]:
    _require(isinstance(value, Mapping), f"{label} must be an object")
    payload = dict(value)
    _require(
        all(isinstance(key, str) and key for key in payload),
        f"{label} keys must be non-blank strings",
    )
    try:
        json.dumps(payload, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise UserStudyValidationError(f"{label} must be finite JSON data") from exc
    return payload


def canonical_json_sha256(value: object) -> str:
    """Return the SHA-256 of UTF-8 canonical JSON for a serializable value."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise UserStudyValidationError("artifact must be finite JSON data") from exc
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class TaskVariant:
    """One scenario in a matched pair; it never prescribes P2 wording."""

    task_id: str
    pair_id: str
    variant_id: str
    phase: TaskPhase
    language: str
    scenario: str
    difficulty: Difficulty
    presentation_version: str

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "task_id",
            "pair_id",
            "variant_id",
            "phase",
            "language",
            "scenario",
            "difficulty",
            "presentation_version",
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _safe_id(self.task_id, "task.task_id"))
        object.__setattr__(self, "pair_id", _safe_id(self.pair_id, "task.pair_id"))
        object.__setattr__(self, "variant_id", _safe_id(self.variant_id, "task.variant_id"))
        _require(isinstance(self.phase, TaskPhase), "task.phase is unsupported")
        language = _nonblank(self.language, "task.language", maximum=32)
        _require(bool(_LANGUAGE.fullmatch(language)), "task.language is invalid")
        _require(language.lower().startswith("en"), "user-study tasks must be in English")
        object.__setattr__(self, "language", language)
        scenario = _nonblank(self.scenario, "task.scenario", maximum=2000)
        _require(
            not _PRESCRIBED_WORDING.search(scenario),
            "task.scenario must describe work, not prescribe exact participant wording",
        )
        object.__setattr__(self, "scenario", scenario)
        _require(isinstance(self.difficulty, Difficulty), "task.difficulty is unsupported")
        object.__setattr__(
            self,
            "presentation_version",
            _safe_id(self.presentation_version, "task.presentation_version"),
        )

    @classmethod
    def from_dict(cls, value: object) -> "TaskVariant":
        payload = _exact_mapping(value, cls._FIELDS, "task")
        return cls(
            task_id=payload["task_id"],
            pair_id=payload["pair_id"],
            variant_id=payload["variant_id"],
            phase=_enum(TaskPhase, payload["phase"], "task.phase"),
            language=payload["language"],
            scenario=payload["scenario"],
            difficulty=_enum(Difficulty, payload["difficulty"], "task.difficulty"),
            presentation_version=payload["presentation_version"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "pair_id": self.pair_id,
            "variant_id": self.variant_id,
            "phase": self.phase.value,
            "language": self.language,
            "scenario": self.scenario,
            "difficulty": self.difficulty.value,
            "presentation_version": self.presentation_version,
        }


@dataclass(frozen=True, slots=True)
class PairGold:
    """Researcher-only requirements shared by both task variants."""

    requirements: tuple[str, ...]
    acceptable_profile_ids: tuple[str, ...]
    acceptable_image_ids: tuple[str, ...]
    acceptable_candidate_ids: tuple[str, ...]
    preferred_candidate_id: str
    policy_constraints: tuple[str, ...]
    difficulty: Difficulty
    equivalence_review_status: ReviewStatus

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "requirements",
            "acceptable_profile_ids",
            "acceptable_image_ids",
            "acceptable_candidate_ids",
            "preferred_candidate_id",
            "policy_constraints",
            "difficulty",
            "equivalence_review_status",
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requirements",
            _string_tuple(self.requirements, "gold.requirements", safe_ids=True),
        )
        for field_name in (
            "acceptable_profile_ids",
            "acceptable_image_ids",
            "acceptable_candidate_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _string_tuple(
                    getattr(self, field_name), f"gold.{field_name}", safe_ids=True
                ),
            )
        object.__setattr__(
            self,
            "preferred_candidate_id",
            _safe_id(self.preferred_candidate_id, "gold.preferred_candidate_id"),
        )
        _require(
            self.preferred_candidate_id in self.acceptable_candidate_ids,
            "gold.preferred_candidate_id must be acceptable",
        )
        object.__setattr__(
            self,
            "policy_constraints",
            _string_tuple(
                self.policy_constraints,
                "gold.policy_constraints",
                safe_ids=True,
                allow_empty=True,
            ),
        )
        _require(isinstance(self.difficulty, Difficulty), "gold.difficulty is unsupported")
        _require(
            isinstance(self.equivalence_review_status, ReviewStatus),
            "gold.equivalence_review_status is unsupported",
        )

    @classmethod
    def from_dict(cls, value: object) -> "PairGold":
        payload = _exact_mapping(value, cls._FIELDS, "gold")
        return cls(
            requirements=_string_tuple(
                payload["requirements"], "gold.requirements", safe_ids=True
            ),
            acceptable_profile_ids=_string_tuple(
                payload["acceptable_profile_ids"],
                "gold.acceptable_profile_ids",
                safe_ids=True,
            ),
            acceptable_image_ids=_string_tuple(
                payload["acceptable_image_ids"],
                "gold.acceptable_image_ids",
                safe_ids=True,
            ),
            acceptable_candidate_ids=_string_tuple(
                payload["acceptable_candidate_ids"],
                "gold.acceptable_candidate_ids",
                safe_ids=True,
            ),
            preferred_candidate_id=payload["preferred_candidate_id"],
            policy_constraints=_string_tuple(
                payload["policy_constraints"],
                "gold.policy_constraints",
                safe_ids=True,
                allow_empty=True,
            ),
            difficulty=_enum(Difficulty, payload["difficulty"], "gold.difficulty"),
            equivalence_review_status=_enum(
                ReviewStatus,
                payload["equivalence_review_status"],
                "gold.equivalence_review_status",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirements": list(self.requirements),
            "acceptable_profile_ids": list(self.acceptable_profile_ids),
            "acceptable_image_ids": list(self.acceptable_image_ids),
            "acceptable_candidate_ids": list(self.acceptable_candidate_ids),
            "preferred_candidate_id": self.preferred_candidate_id,
            "policy_constraints": list(self.policy_constraints),
            "difficulty": self.difficulty.value,
            "equivalence_review_status": self.equivalence_review_status.value,
        }


@dataclass(frozen=True, slots=True)
class MatchedPair:
    """Exactly two distinct scenarios with one shared gold definition."""

    pair_id: str
    phase: TaskPhase
    tasks: tuple[TaskVariant, TaskVariant]
    gold: PairGold

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"pair_id", "phase", "tasks", "gold"}
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "pair_id", _safe_id(self.pair_id, "pair.pair_id"))
        _require(isinstance(self.phase, TaskPhase), "pair.phase is unsupported")
        _require(
            isinstance(self.tasks, tuple) and len(self.tasks) == 2,
            "pair.tasks must contain exactly two task variants",
        )
        _require(
            all(isinstance(task, TaskVariant) for task in self.tasks),
            "pair.tasks contains an invalid task",
        )
        _require(isinstance(self.gold, PairGold), "pair.gold has the wrong type")
        _require(
            len({task.task_id for task in self.tasks}) == 2,
            "pair task IDs must be distinct",
        )
        _require(
            len({task.variant_id for task in self.tasks}) == 2,
            "pair variant IDs must be distinct",
        )
        _require(
            {task.variant_id for task in self.tasks} == {"A", "B"},
            "pair variant IDs must be exactly A and B for counterbalancing",
        )
        _require(
            len({task.scenario for task in self.tasks}) == 2,
            "pair scenarios must be distinct matched variants",
        )
        _require(
            all(task.pair_id == self.pair_id for task in self.tasks),
            "pair task pair_id values must match their container",
        )
        _require(
            all(task.phase is self.phase for task in self.tasks),
            "pair task phases must match their container",
        )
        _require(
            all(task.difficulty is self.gold.difficulty for task in self.tasks),
            "both variants and shared gold must have equivalent difficulty",
        )

    @classmethod
    def from_dict(cls, value: object) -> "MatchedPair":
        payload = _exact_mapping(value, cls._FIELDS, "pair")
        tasks_raw = payload["tasks"]
        _require(
            isinstance(tasks_raw, Sequence) and not isinstance(tasks_raw, (str, bytes)),
            "pair.tasks must be an array",
        )
        tasks = tuple(TaskVariant.from_dict(item) for item in tasks_raw)
        _require(len(tasks) == 2, "pair.tasks must contain exactly two task variants")
        return cls(
            pair_id=payload["pair_id"],
            phase=_enum(TaskPhase, payload["phase"], "pair.phase"),
            tasks=(tasks[0], tasks[1]),
            gold=PairGold.from_dict(payload["gold"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "phase": self.phase.value,
            "tasks": [task.to_dict() for task in self.tasks],
            "gold": self.gold.to_dict(),
        }

    def variant(self, variant_id: str) -> TaskVariant:
        """Return one variant by ID, failing closed when it is absent."""

        for task in self.tasks:
            if task.variant_id == variant_id:
                return task
        raise KeyError(variant_id)


@dataclass(frozen=True, slots=True)
class TaskSet:
    """Authoritative matched-pair task bundle, including researcher-only gold."""

    task_set_id: str
    stage: TaskSetStage
    status: TaskSetStatus
    language: str
    presentation_version: str
    catalog_version: str
    corpus_version: str
    policy_version: str
    pairs: tuple[MatchedPair, ...]
    schema_version: str = TASK_SET_SCHEMA_VERSION

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "task_set_id",
            "stage",
            "status",
            "language",
            "presentation_version",
            "catalog_version",
            "corpus_version",
            "policy_version",
            "pairs",
        }
    )

    def __post_init__(self) -> None:
        _require(
            self.schema_version == TASK_SET_SCHEMA_VERSION,
            "task_set.schema_version is unsupported",
        )
        object.__setattr__(
            self, "task_set_id", _safe_id(self.task_set_id, "task_set.task_set_id")
        )
        _require(isinstance(self.stage, TaskSetStage), "task_set.stage is unsupported")
        _require(isinstance(self.status, TaskSetStatus), "task_set.status is unsupported")
        language = _nonblank(self.language, "task_set.language", maximum=32)
        _require(bool(_LANGUAGE.fullmatch(language)), "task_set.language is invalid")
        _require(language.lower().startswith("en"), "user-study task set must be English")
        object.__setattr__(self, "language", language)
        for field_name in (
            "presentation_version",
            "catalog_version",
            "corpus_version",
            "policy_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _safe_id(getattr(self, field_name), f"task_set.{field_name}"),
            )
        _require(bool(self.pairs), "task_set.pairs cannot be empty")
        _require(
            all(isinstance(pair, MatchedPair) for pair in self.pairs),
            "task_set.pairs contains an invalid pair",
        )
        pair_ids = [pair.pair_id for pair in self.pairs]
        task_ids = [task.task_id for pair in self.pairs for task in pair.tasks]
        _require(len(set(pair_ids)) == len(pair_ids), "task_set pair IDs must be unique")
        _require(len(set(task_ids)) == len(task_ids), "task_set task IDs must be unique")
        _require(
            all(task.language == self.language for pair in self.pairs for task in pair.tasks),
            "task language must match task_set.language",
        )
        _require(
            all(
                task.presentation_version == self.presentation_version
                for pair in self.pairs
                for task in pair.tasks
            ),
            "task presentation versions must match task_set.presentation_version",
        )

    @classmethod
    def from_dict(cls, value: object) -> "TaskSet":
        payload = _exact_mapping(value, cls._FIELDS, "task_set")
        pairs_raw = payload["pairs"]
        _require(
            isinstance(pairs_raw, Sequence) and not isinstance(pairs_raw, (str, bytes)),
            "task_set.pairs must be an array",
        )
        return cls(
            schema_version=payload["schema_version"],
            task_set_id=payload["task_set_id"],
            stage=_enum(TaskSetStage, payload["stage"], "task_set.stage"),
            status=_enum(TaskSetStatus, payload["status"], "task_set.status"),
            language=payload["language"],
            presentation_version=payload["presentation_version"],
            catalog_version=payload["catalog_version"],
            corpus_version=payload["corpus_version"],
            policy_version=payload["policy_version"],
            pairs=tuple(MatchedPair.from_dict(item) for item in pairs_raw),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_set_id": self.task_set_id,
            "stage": self.stage.value,
            "status": self.status.value,
            "language": self.language,
            "presentation_version": self.presentation_version,
            "catalog_version": self.catalog_version,
            "corpus_version": self.corpus_version,
            "policy_version": self.policy_version,
            "pairs": [pair.to_dict() for pair in self.pairs],
        }

    @property
    def checksum(self) -> str:
        """Canonical checksum bound into assignment manifests."""

        return canonical_json_sha256(self.to_dict())

    def pair_by_id(self, pair_id: str) -> MatchedPair:
        for pair in self.pairs:
            if pair.pair_id == pair_id:
                return pair
        raise KeyError(pair_id)

    def task_by_id(self, task_id: str) -> TaskVariant:
        for pair in self.pairs:
            for task in pair.tasks:
                if task.task_id == task_id:
                    return task
        raise KeyError(task_id)

    def browser_projection(self) -> dict[str, Any]:
        """Return the browser-safe projection with all pair gold removed."""

        return browser_safe_task_set(self)


def parse_task_set(value: object) -> TaskSet:
    """Parse a strict task-set mapping or return an already parsed instance."""

    if isinstance(value, TaskSet):
        return value
    return TaskSet.from_dict(value)


def load_task_set(path: str | Path) -> TaskSet:
    """Load JSON or YAML task-set input without changing the source artifact."""

    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UserStudyValidationError(f"invalid task-set JSON: {exc}") from exc
    else:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - repository runtime includes PyYAML
            raise UserStudyValidationError("PyYAML is required to load YAML task sets") from exc
        try:
            payload = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise UserStudyValidationError(f"invalid task-set YAML: {exc}") from exc
    return TaskSet.from_dict(payload)


def _catalog_ids(catalog: object | None) -> tuple[set[str] | None, str | None]:
    if catalog is None:
        return None, None
    if isinstance(catalog, Mapping):
        if "images" in catalog:
            images = catalog["images"]
            _require(isinstance(images, Mapping), "catalog.images must be an object")
            version = catalog.get("catalog_version")
            return set(images), str(version) if version is not None else None
        return set(str(key) for key in catalog), None
    if isinstance(catalog, Iterable) and not isinstance(catalog, (str, bytes)):
        return set(str(item) for item in catalog), None
    raise UserStudyValidationError("catalog must expose image IDs")


def _corpus_indexes(
    corpus: object | None,
) -> tuple[dict[str, tuple[str, str]] | None, str | None, str | None]:
    if corpus is None:
        return None, None, None
    candidates = getattr(corpus, "candidates", None)
    if candidates is None and isinstance(corpus, Mapping):
        candidates = corpus.get("candidates", corpus)
    index: dict[str, tuple[str, str]] = {}
    if isinstance(candidates, Mapping):
        iterable: Iterable[object] = candidates.values()
    elif isinstance(candidates, Iterable) and not isinstance(candidates, (str, bytes)):
        iterable = candidates
    else:
        raise UserStudyValidationError("corpus must expose candidate records")
    for item in iterable:
        if isinstance(item, Mapping):
            candidate_id = item.get("candidate_id")
            profile_id = item.get("profile_id")
            image_id = item.get("image_id")
        else:
            candidate_id = getattr(item, "candidate_id", None)
            profile_id = getattr(item, "profile_id", None)
            image_id = getattr(item, "image_id", None)
        cid = _safe_id(candidate_id, "corpus.candidate_id")
        _require(cid not in index, f"duplicate corpus candidate ID {cid!r}")
        index[cid] = (
            _safe_id(profile_id, f"corpus[{cid}].profile_id"),
            _safe_id(image_id, f"corpus[{cid}].image_id"),
        )
    corpus_version = getattr(corpus, "corpus_version", None)
    policy_version = getattr(corpus, "policy_version", None)
    if isinstance(corpus, Mapping):
        corpus_version = corpus.get("corpus_version", corpus_version)
        policy_version = corpus.get("policy_version", policy_version)
    return (
        index,
        str(corpus_version) if corpus_version is not None else None,
        str(policy_version) if policy_version is not None else None,
    )


def validate_task_set(
    task_set: TaskSet | Mapping[str, Any],
    *,
    catalog: object | None = None,
    corpus: object | None = None,
    confirmatory: bool = False,
    require_protocol_design: bool = False,
) -> TaskSet:
    """Validate task equivalence, review gates, and authoritative IDs.

    Confirmatory preparation fails closed for any development/draft task set or
    pair whose equivalence review is not approved.  ``require_protocol_design``
    additionally enforces the E3 allocation shape of one warm-up and three
    measured pairs.
    """

    parsed = parse_task_set(task_set)
    if confirmatory:
        _require(
            parsed.stage is TaskSetStage.CONFIRMATORY,
            "confirmatory preparation rejects a development task set",
        )
        _require(
            parsed.status is TaskSetStatus.FROZEN,
            "confirmatory preparation rejects a draft task set",
        )
    if parsed.stage is TaskSetStage.CONFIRMATORY:
        _require(
            parsed.status is TaskSetStatus.FROZEN,
            "confirmatory task sets must be frozen",
        )
    if parsed.status is TaskSetStatus.FROZEN:
        _require(
            all(
                pair.gold.equivalence_review_status is ReviewStatus.APPROVED
                for pair in parsed.pairs
            ),
            "frozen task sets require approved matched-pair equivalence reviews",
        )

    warmups = [pair for pair in parsed.pairs if pair.phase is TaskPhase.WARM_UP]
    measured = [pair for pair in parsed.pairs if pair.phase is TaskPhase.MEASURED]
    if require_protocol_design:
        _require(len(warmups) == 1, "E3 task set must contain exactly one warm-up pair")
        _require(len(measured) == 3, "E3 task set must contain exactly three measured pairs")

    image_ids, catalog_version = _catalog_ids(catalog)
    candidate_index, corpus_version, policy_version = _corpus_indexes(corpus)
    if catalog_version is not None:
        _require(
            parsed.catalog_version == catalog_version,
            "task-set catalog_version differs from the supplied catalog",
        )
    if corpus_version is not None:
        _require(
            parsed.corpus_version == corpus_version,
            "task-set corpus_version differs from the supplied corpus",
        )
    if policy_version is not None:
        _require(
            parsed.policy_version == policy_version,
            "task-set policy_version differs from the supplied corpus",
        )

    for pair in parsed.pairs:
        gold = pair.gold
        if confirmatory:
            _require(
                gold.equivalence_review_status is ReviewStatus.APPROVED,
                f"confirmatory preparation rejects unapproved pair {pair.pair_id!r}",
            )
        if image_ids is not None:
            unknown_images = sorted(set(gold.acceptable_image_ids) - image_ids)
            _require(
                not unknown_images,
                f"pair {pair.pair_id!r} references unknown images: {', '.join(unknown_images)}",
            )
        if candidate_index is not None:
            unknown_candidates = sorted(
                set(gold.acceptable_candidate_ids) - set(candidate_index)
            )
            _require(
                not unknown_candidates,
                f"pair {pair.pair_id!r} references unknown candidates: "
                + ", ".join(unknown_candidates),
            )
            for candidate_id in gold.acceptable_candidate_ids:
                profile_id, image_id = candidate_index[candidate_id]
                _require(
                    profile_id in gold.acceptable_profile_ids,
                    f"candidate {candidate_id!r} profile is not acceptable for pair {pair.pair_id!r}",
                )
                _require(
                    image_id in gold.acceptable_image_ids,
                    f"candidate {candidate_id!r} image is not acceptable for pair {pair.pair_id!r}",
                )
            projected_profiles = {
                candidate_index[candidate_id][0]
                for candidate_id in gold.acceptable_candidate_ids
            }
            projected_images = {
                candidate_index[candidate_id][1]
                for candidate_id in gold.acceptable_candidate_ids
            }
            _require(
                projected_profiles == set(gold.acceptable_profile_ids),
                f"pair {pair.pair_id!r} acceptable profiles do not exactly match its candidates",
            )
            _require(
                projected_images == set(gold.acceptable_image_ids),
                f"pair {pair.pair_id!r} acceptable images do not exactly match its candidates",
            )
            known_profiles = {profile for profile, _ in candidate_index.values()}
            unknown_profiles = sorted(set(gold.acceptable_profile_ids) - known_profiles)
            _require(
                not unknown_profiles,
                f"pair {pair.pair_id!r} references unknown profiles: "
                + ", ".join(unknown_profiles),
            )
    return parsed


def browser_safe_task_set(task_set: TaskSet | Mapping[str, Any]) -> dict[str, Any]:
    """Create the only task-set representation permitted in the browser.

    This is intentionally not a ``TaskSet`` serialization: the different schema
    version makes it impossible to accidentally pass the projection to scoring
    code as an authoritative gold bundle.
    """

    parsed = parse_task_set(task_set)
    projection = {
        "schema_version": BROWSER_TASK_SET_SCHEMA_VERSION,
        "source_task_set_id": parsed.task_set_id,
        "source_task_set_sha256": parsed.checksum,
        "language": parsed.language,
        "presentation_version": parsed.presentation_version,
        "pairs": [
            {
                "pair_id": pair.pair_id,
                "phase": pair.phase.value,
                "tasks": [task.to_dict() for task in pair.tasks],
            }
            for pair in parsed.pairs
        ],
    }
    serialized = json.dumps(projection, sort_keys=True)
    _require('"gold"' not in serialized, "browser projection leaked gold fields")
    return projection


@dataclass(frozen=True, slots=True)
class StudyEvent:
    """One content-free, append-only instrumentation event."""

    study_id: str
    assignment_id: str
    session_id: str
    participant_id: str
    trial_id: str
    task_id: str
    pair_id: str
    condition: Condition
    consent_version: str
    event_uuid: str
    event_index: int
    timestamp_utc: str
    monotonic_seconds: float
    event_type: EventType
    profile_id: str | None
    image_id: str | None
    old_profile_id: str | None
    new_profile_id: str | None
    old_image_id: str | None
    new_image_id: str | None
    preview_status: PreviewStatus | None
    cancel_reason: CancelReason | None
    schema_version: str = EVENT_SCHEMA_VERSION

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "study_id",
            "assignment_id",
            "session_id",
            "participant_id",
            "trial_id",
            "task_id",
            "pair_id",
            "condition",
            "consent_version",
            "event_uuid",
            "event_index",
            "timestamp_utc",
            "monotonic_seconds",
            "event_type",
            "profile_id",
            "image_id",
            "old_profile_id",
            "new_profile_id",
            "old_image_id",
            "new_image_id",
            "preview_status",
            "cancel_reason",
        }
    )

    def __post_init__(self) -> None:
        _require(
            self.schema_version == EVENT_SCHEMA_VERSION,
            "event.schema_version is unsupported",
        )
        for field_name in (
            "study_id",
            "assignment_id",
            "session_id",
            "trial_id",
            "task_id",
            "pair_id",
            "consent_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _safe_id(getattr(self, field_name), f"event.{field_name}"),
            )
        _require(
            isinstance(self.participant_id, str)
            and bool(_PARTICIPANT_ID.fullmatch(self.participant_id)),
            "event.participant_id must be a study pseudonym P-<12 hex>",
        )
        _require(isinstance(self.condition, Condition), "event.condition is unsupported")
        _require(isinstance(self.event_type, EventType), "event.event_type is unsupported")
        try:
            parsed_uuid = uuid.UUID(self.event_uuid)
        except (AttributeError, TypeError, ValueError) as exc:
            raise UserStudyValidationError("event.event_uuid must be a UUID") from exc
        _require(
            str(parsed_uuid) == self.event_uuid.lower(),
            "event.event_uuid must use canonical UUID notation",
        )
        _require(
            isinstance(self.event_index, int)
            and not isinstance(self.event_index, bool)
            and self.event_index >= 0,
            "event.event_index must be a non-negative integer",
        )
        _validate_utc_timestamp(self.timestamp_utc, "event.timestamp_utc")
        _require(
            isinstance(self.monotonic_seconds, (int, float))
            and not isinstance(self.monotonic_seconds, bool)
            and math.isfinite(float(self.monotonic_seconds))
            and float(self.monotonic_seconds) >= 0,
            "event.monotonic_seconds must be finite and non-negative",
        )
        object.__setattr__(self, "monotonic_seconds", float(self.monotonic_seconds))
        for field_name in (
            "profile_id",
            "image_id",
            "old_profile_id",
            "new_profile_id",
            "old_image_id",
            "new_image_id",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self, field_name, _safe_id(value, f"event.{field_name}")
                )
        _require(
            self.preview_status is None or isinstance(self.preview_status, PreviewStatus),
            "event.preview_status is unsupported",
        )
        _require(
            self.cancel_reason is None or isinstance(self.cancel_reason, CancelReason),
            "event.cancel_reason is unsupported",
        )

    @classmethod
    def from_dict(cls, value: object) -> "StudyEvent":
        payload = _exact_mapping(value, cls._FIELDS, "event")
        return cls(
            schema_version=payload["schema_version"],
            study_id=payload["study_id"],
            assignment_id=payload["assignment_id"],
            session_id=payload["session_id"],
            participant_id=payload["participant_id"],
            trial_id=payload["trial_id"],
            task_id=payload["task_id"],
            pair_id=payload["pair_id"],
            condition=_enum(Condition, payload["condition"], "event.condition"),
            consent_version=payload["consent_version"],
            event_uuid=payload["event_uuid"],
            event_index=payload["event_index"],
            timestamp_utc=payload["timestamp_utc"],
            monotonic_seconds=payload["monotonic_seconds"],
            event_type=_enum(EventType, payload["event_type"], "event.event_type"),
            profile_id=payload["profile_id"],
            image_id=payload["image_id"],
            old_profile_id=payload["old_profile_id"],
            new_profile_id=payload["new_profile_id"],
            old_image_id=payload["old_image_id"],
            new_image_id=payload["new_image_id"],
            preview_status=(
                None
                if payload["preview_status"] is None
                else _enum(
                    PreviewStatus,
                    payload["preview_status"],
                    "event.preview_status",
                )
            ),
            cancel_reason=(
                None
                if payload["cancel_reason"] is None
                else _enum(CancelReason, payload["cancel_reason"], "event.cancel_reason")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "study_id": self.study_id,
            "assignment_id": self.assignment_id,
            "session_id": self.session_id,
            "participant_id": self.participant_id,
            "trial_id": self.trial_id,
            "task_id": self.task_id,
            "pair_id": self.pair_id,
            "condition": self.condition.value,
            "consent_version": self.consent_version,
            "event_uuid": self.event_uuid,
            "event_index": self.event_index,
            "timestamp_utc": self.timestamp_utc,
            "monotonic_seconds": self.monotonic_seconds,
            "event_type": self.event_type.value,
            "profile_id": self.profile_id,
            "image_id": self.image_id,
            "old_profile_id": self.old_profile_id,
            "new_profile_id": self.new_profile_id,
            "old_image_id": self.old_image_id,
            "new_image_id": self.new_image_id,
            "preview_status": (
                self.preview_status.value if self.preview_status is not None else None
            ),
            "cancel_reason": (
                self.cancel_reason.value if self.cancel_reason is not None else None
            ),
        }


def _validate_utc_timestamp(value: object, label: str) -> datetime:
    _require(isinstance(value, str) and value.endswith("Z"), f"{label} must end in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise UserStudyValidationError(f"{label} must be ISO-8601 UTC") from exc
    _require(
        parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed),
        f"{label} must use UTC",
    )
    return parsed


_SELECTION_FIELDS = (
    "profile_id",
    "image_id",
    "old_profile_id",
    "new_profile_id",
    "old_image_id",
    "new_image_id",
)
_P2_ONLY_EVENTS = frozenset(
    {
        EventType.INTENT_FOCUS,
        EventType.INTENT_EDIT,
        EventType.PREVIEW_REQUESTED,
        EventType.PREVIEW_RECEIVED,
        EventType.OVERRIDE,
    }
)


def _all_null(event: StudyEvent, fields: Iterable[str]) -> bool:
    return all(getattr(event, field_name) is None for field_name in fields)


def validate_event(event: StudyEvent | Mapping[str, Any]) -> StudyEvent:
    """Validate one event's exact fields and event-specific payload."""

    parsed = event if isinstance(event, StudyEvent) else StudyEvent.from_dict(event)
    event_type = parsed.event_type
    _require(
        parsed.condition is Condition.P2 or event_type not in _P2_ONLY_EVENTS,
        f"{event_type.value} is not valid under B0",
    )

    if event_type in {
        EventType.TASK_SHOWN,
        EventType.INTENT_FOCUS,
        EventType.INTENT_EDIT,
        EventType.PREVIEW_REQUESTED,
    }:
        _require(_all_null(parsed, _SELECTION_FIELDS), f"{event_type.value} cannot carry selection IDs")
        _require(parsed.preview_status is None, f"{event_type.value} cannot carry preview_status")
        _require(parsed.cancel_reason is None, f"{event_type.value} cannot carry cancel_reason")
    elif event_type is EventType.PREVIEW_RECEIVED:
        _require(parsed.preview_status is not None, "preview_received requires preview_status")
        _require(
            _all_null(
                parsed,
                ("old_profile_id", "new_profile_id", "old_image_id", "new_image_id"),
            ),
            "preview_received cannot carry change fields",
        )
        if parsed.preview_status is PreviewStatus.SUCCESS:
            _require(
                parsed.profile_id is not None and parsed.image_id is not None,
                "successful preview_received requires profile_id and image_id",
            )
        else:
            _require(
                parsed.profile_id is None and parsed.image_id is None,
                "failed preview_received cannot carry selection IDs",
            )
        _require(parsed.cancel_reason is None, "preview_received cannot carry cancel_reason")
    elif event_type is EventType.PROFILE_CHANGED:
        _require(parsed.new_profile_id is not None, "profile_changed requires new_profile_id")
        _require(
            _all_null(parsed, ("profile_id", "image_id", "old_image_id", "new_image_id")),
            "profile_changed contains inapplicable selection fields",
        )
        _require(parsed.preview_status is None and parsed.cancel_reason is None, "profile_changed contains inapplicable status")
    elif event_type is EventType.IMAGE_CHANGED:
        _require(parsed.new_image_id is not None, "image_changed requires new_image_id")
        _require(
            _all_null(parsed, ("profile_id", "image_id", "old_profile_id", "new_profile_id")),
            "image_changed contains inapplicable selection fields",
        )
        _require(parsed.preview_status is None and parsed.cancel_reason is None, "image_changed contains inapplicable status")
    elif event_type in {EventType.OVERRIDE, EventType.CONFIRM}:
        _require(
            parsed.profile_id is not None and parsed.image_id is not None,
            f"{event_type.value} requires profile_id and image_id",
        )
        _require(
            _all_null(
                parsed,
                ("old_profile_id", "new_profile_id", "old_image_id", "new_image_id"),
            ),
            f"{event_type.value} cannot carry change fields",
        )
        _require(parsed.preview_status is None and parsed.cancel_reason is None, f"{event_type.value} contains inapplicable status")
    elif event_type is EventType.CANCEL:
        _require(parsed.cancel_reason is not None, "cancel requires cancel_reason")
        _require(_all_null(parsed, _SELECTION_FIELDS), "cancel cannot carry selection IDs")
        _require(parsed.preview_status is None, "cancel cannot carry preview_status")
    elif event_type is EventType.NOTEBOOK_READY:
        _require(
            (parsed.profile_id is None) == (parsed.image_id is None),
            "notebook_ready must carry both selection IDs or neither",
        )
        _require(
            _all_null(
                parsed,
                ("old_profile_id", "new_profile_id", "old_image_id", "new_image_id"),
            ),
            "notebook_ready cannot carry change fields",
        )
        _require(parsed.preview_status is None and parsed.cancel_reason is None, "notebook_ready contains inapplicable status")
    return parsed


def _assignment_event_index(
    assignment_manifest: object | None,
) -> dict[tuple[str, str], dict[str, str]] | None:
    if assignment_manifest is None:
        return None
    raw = (
        assignment_manifest.to_dict()
        if hasattr(assignment_manifest, "to_dict")
        else assignment_manifest
    )
    _require(isinstance(raw, Mapping), "assignment_manifest must be an object")
    study_id = raw.get("study_id")
    assignment_id = raw.get("assignment_id")
    consent_version = raw.get("consent_version")
    for field_name, value in (
        ("study_id", study_id),
        ("assignment_id", assignment_id),
        ("consent_version", consent_version),
    ):
        _safe_id(value, f"assignment_manifest.{field_name}")
    assignments = raw.get("assignments")
    _require(isinstance(assignments, Sequence), "assignment_manifest.assignments must be an array")
    index: dict[tuple[str, str], dict[str, str]] = {}
    for participant in assignments:
        _require(isinstance(participant, Mapping), "assignment entry must be an object")
        participant_id = participant.get("participant_id")
        session_id = participant.get("session_id")
        sequence = participant.get("task_sequence")
        _require(isinstance(sequence, Sequence), "assignment task_sequence must be an array")
        for task in sequence:
            _require(isinstance(task, Mapping), "assigned task must be an object")
            key = (str(participant_id), str(task.get("task_id")))
            _require(key not in index, "assignment repeats a participant/task identity")
            index[key] = {
                "study_id": str(study_id),
                "assignment_id": str(assignment_id),
                "session_id": str(session_id),
                "participant_id": str(participant_id),
                "trial_id": str(task.get("trial_id")),
                "task_id": str(task.get("task_id")),
                "pair_id": str(task.get("pair_id")),
                "condition": str(task.get("condition")),
                "consent_version": str(consent_version),
            }
    return index


def validate_event_stream(
    events: Iterable[StudyEvent | Mapping[str, Any]],
    *,
    assignment_manifest: object | None = None,
    task_set: TaskSet | Mapping[str, Any] | None = None,
    allowed_profile_ids: Iterable[str] | None = None,
    allowed_image_ids: Iterable[str] | None = None,
    allow_incomplete: bool = False,
) -> tuple[StudyEvent, ...]:
    """Validate complete trial state machines in an event stream.

    Trials may be interleaved in the input JSONL.  Ordering is enforced within
    each trial using its event index and timestamp; UUID uniqueness is global.
    ``allow_incomplete`` is reserved for validation of a live, durable prefix:
    it permits an empty stream and a final non-terminal trial state, but does
    not relax identity, transition, ordering, or privacy checks.  Finalization
    must use the default strict mode.
    """

    parsed_events = tuple(validate_event(event) for event in events)
    if not parsed_events:
        _require(allow_incomplete, "event stream cannot be empty")
        return ()
    uuids = [event.event_uuid for event in parsed_events]
    _require(len(set(uuids)) == len(uuids), "event stream contains duplicate event_uuid values")

    allowed_profiles = set(allowed_profile_ids) if allowed_profile_ids is not None else None
    allowed_images = set(allowed_image_ids) if allowed_image_ids is not None else None
    tasks = parse_task_set(task_set) if task_set is not None else None
    assigned = _assignment_event_index(assignment_manifest)

    trial_groups: dict[tuple[str, str], list[StudyEvent]] = defaultdict(list)
    for event in parsed_events:
        for field_name, allowed in (
            ("profile_id", allowed_profiles),
            ("old_profile_id", allowed_profiles),
            ("new_profile_id", allowed_profiles),
            ("image_id", allowed_images),
            ("old_image_id", allowed_images),
            ("new_image_id", allowed_images),
        ):
            value = getattr(event, field_name)
            if value is not None and allowed is not None:
                _require(value in allowed, f"event.{field_name} references an unknown ID")
        if tasks is not None:
            try:
                task = tasks.task_by_id(event.task_id)
            except KeyError as exc:
                raise UserStudyValidationError(
                    f"event references unknown task {event.task_id!r}"
                ) from exc
            _require(task.pair_id == event.pair_id, "event task/pair identity drift")
        if assigned is not None:
            expected = assigned.get((event.participant_id, event.task_id))
            _require(expected is not None, "event task is absent from participant assignment")
            observed = {
                "study_id": event.study_id,
                "assignment_id": event.assignment_id,
                "session_id": event.session_id,
                "participant_id": event.participant_id,
                "trial_id": event.trial_id,
                "task_id": event.task_id,
                "pair_id": event.pair_id,
                "condition": event.condition.value,
                "consent_version": event.consent_version,
            }
            for field_name, expected_value in expected.items():
                _require(
                    observed[field_name] == expected_value,
                    f"event.{field_name} differs from participant assignment",
                )
        trial_groups[(event.session_id, event.trial_id)].append(event)

    for trial_key, trial in trial_groups.items():
        trial.sort(key=lambda item: item.event_index)
        first = trial[0]
        _require(first.event_index == 0, f"trial {trial_key!r} must start at event_index 0")
        _require(first.event_type is EventType.TASK_SHOWN, f"trial {trial_key!r} must start with task_shown")
        identity = (
            first.study_id,
            first.assignment_id,
            first.session_id,
            first.participant_id,
            first.trial_id,
            first.task_id,
            first.pair_id,
            first.condition,
            first.consent_version,
        )
        current_profile: str | None = None
        current_image: str | None = None
        recommendation: tuple[str, str] | None = None
        preview_pending = False
        successful_preview = False
        intent_focused = False
        edit_recorded_for_focus = False
        override_selection: tuple[str, str] | None = None
        confirmed: tuple[str, str] | None = None
        confirmed_at: float | None = None
        terminal = False
        previous_time: float | None = None
        previous_timestamp: datetime | None = None

        for expected_index, event in enumerate(trial):
            _require(event.event_index == expected_index, f"trial {trial_key!r} event indexes must be contiguous")
            current_identity = (
                event.study_id,
                event.assignment_id,
                event.session_id,
                event.participant_id,
                event.trial_id,
                event.task_id,
                event.pair_id,
                event.condition,
                event.consent_version,
            )
            _require(current_identity == identity, f"trial {trial_key!r} identity drift")
            timestamp = _validate_utc_timestamp(event.timestamp_utc, "event.timestamp_utc")
            if previous_time is not None:
                _require(event.monotonic_seconds >= previous_time, f"trial {trial_key!r} monotonic time moved backwards")
                _require(timestamp >= previous_timestamp, f"trial {trial_key!r} UTC time moved backwards")
            previous_time = event.monotonic_seconds
            previous_timestamp = timestamp

            if expected_index == 0:
                continue
            _require(not terminal, f"trial {trial_key!r} contains an event after terminal state")
            if confirmed is not None:
                _require(
                    event.event_type is EventType.NOTEBOOK_READY,
                    f"trial {trial_key!r} permits only notebook_ready after confirm",
                )
            if event.event_type is EventType.TASK_SHOWN:
                raise UserStudyValidationError(f"trial {trial_key!r} contains repeated task_shown")
            if event.event_type is EventType.INTENT_FOCUS:
                intent_focused = True
                edit_recorded_for_focus = False
            elif event.event_type is EventType.INTENT_EDIT:
                _require(intent_focused, "intent_edit requires an intent_focus episode")
                _require(
                    not edit_recorded_for_focus,
                    "intent_edit must be coalesced once per focus episode",
                )
                edit_recorded_for_focus = True
            elif event.event_type is EventType.PREVIEW_REQUESTED:
                _require(not preview_pending, f"trial {trial_key!r} already has a pending preview")
                _require(
                    intent_focused and (edit_recorded_for_focus or successful_preview),
                    "first preview requires a content-free focused edit episode",
                )
                preview_pending = True
            elif event.event_type is EventType.PREVIEW_RECEIVED:
                _require(preview_pending, f"trial {trial_key!r} received preview without request")
                preview_pending = False
                if event.preview_status is PreviewStatus.SUCCESS:
                    successful_preview = True
                    current_profile = event.profile_id
                    current_image = event.image_id
                    recommendation = (event.profile_id, event.image_id)  # type: ignore[arg-type]
                    override_selection = None
            elif event.event_type is EventType.PROFILE_CHANGED:
                if first.condition is Condition.P2:
                    _require(successful_preview, "P2 selection cannot change before a successful preview")
                _require(event.old_profile_id == current_profile, "profile_changed old_profile_id does not match current selection")
                _require(event.new_profile_id != current_profile, "profile_changed must change the selection")
                current_profile = event.new_profile_id
            elif event.event_type is EventType.IMAGE_CHANGED:
                if first.condition is Condition.P2:
                    _require(successful_preview, "P2 selection cannot change before a successful preview")
                _require(event.old_image_id == current_image, "image_changed old_image_id does not match current selection")
                _require(event.new_image_id != current_image, "image_changed must change the selection")
                current_image = event.new_image_id
            elif event.event_type is EventType.OVERRIDE:
                _require(successful_preview and recommendation is not None, "override requires a successful preview")
                _require(
                    (event.profile_id, event.image_id) != recommendation,
                    "override must differ from the latest recommendation",
                )
                _require(
                    (event.profile_id, event.image_id) == (current_profile, current_image),
                    "override selection must match current selectors",
                )
                _require(
                    override_selection != (event.profile_id, event.image_id),
                    "override must not repeat the same selection",
                )
                override_selection = (event.profile_id, event.image_id)  # type: ignore[arg-type]
            elif event.event_type is EventType.CONFIRM:
                _require(not preview_pending, "confirm cannot occur while a preview is pending")
                _require(
                    event.monotonic_seconds - first.monotonic_seconds
                    <= DECISION_LIMIT_SECONDS + _TIMING_EPSILON_SECONDS,
                    f"trial {trial_key!r} confirm exceeds the "
                    f"{DECISION_LIMIT_SECONDS:g}-second decision limit",
                )
                _require(
                    (event.profile_id, event.image_id) == (current_profile, current_image),
                    "confirm selection must match current selectors",
                )
                if first.condition is Condition.P2:
                    _require(successful_preview and recommendation is not None, "P2 confirm requires a successful preview")
                    if (event.profile_id, event.image_id) != recommendation:
                        _require(
                            override_selection == (event.profile_id, event.image_id),
                            "a changed P2 selection requires a matching override event",
                        )
                confirmed = (event.profile_id, event.image_id)  # type: ignore[arg-type]
                confirmed_at = event.monotonic_seconds
            elif event.event_type is EventType.CANCEL:
                if event.cancel_reason is CancelReason.DECISION_TIMEOUT:
                    _require(
                        event.monotonic_seconds - first.monotonic_seconds
                        >= DECISION_LIMIT_SECONDS - _TIMING_EPSILON_SECONDS,
                        f"trial {trial_key!r} decision_timeout occurred before "
                        f"{DECISION_LIMIT_SECONDS:g} seconds",
                    )
                terminal = True
            elif event.event_type is EventType.NOTEBOOK_READY:
                _require(confirmed is not None, "notebook_ready cannot occur before confirm")
                _require(confirmed_at is not None, "notebook_ready is missing confirm timing")
                _require(
                    event.monotonic_seconds - confirmed_at
                    <= READINESS_LIMIT_SECONDS + _TIMING_EPSILON_SECONDS,
                    f"trial {trial_key!r} notebook_ready exceeds the 180-second readiness limit",
                )
                if event.profile_id is not None:
                    _require(
                        (event.profile_id, event.image_id) == confirmed,
                        "notebook_ready selection differs from confirm",
                    )
                terminal = True
        if not allow_incomplete:
            _require(
                confirmed is not None or terminal,
                f"trial {trial_key!r} is incomplete (neither confirm nor cancel)",
            )
    return parsed_events


__all__ = [
    "ASSIGNMENT_SCHEMA_VERSION",
    "BROWSER_TASK_SET_SCHEMA_VERSION",
    "DECISION_LIMIT_SECONDS",
    "EVENT_SCHEMA_VERSION",
    "READINESS_LIMIT_SECONDS",
    "STUDY_TIMING_CONTRACT",
    "STUDY_TIMING_CONTRACT_SHA256",
    "STUDY_TIMING_CONTRACT_VERSION",
    "TASK_SET_SCHEMA_VERSION",
    "CancelReason",
    "Condition",
    "Difficulty",
    "EventType",
    "MatchedPair",
    "PairGold",
    "PreviewStatus",
    "ReviewStatus",
    "StudyEvent",
    "TaskPhase",
    "TaskSet",
    "TaskSetStage",
    "TaskSetStatus",
    "TaskVariant",
    "UserStudyValidationError",
    "browser_safe_task_set",
    "canonical_json_sha256",
    "load_task_set",
    "parse_task_set",
    "validate_event",
    "validate_event_stream",
    "validate_task_set",
]
