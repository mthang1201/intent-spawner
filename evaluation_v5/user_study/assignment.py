"""Deterministic counterbalanced assignments for Protocol-v5 E3.

The schedule uses twelve cells: condition first (2) x variant allocation (2)
x cyclic measured-pair order (3).  SHA-256 keyed ordering is used throughout
so results do not depend on Python's pseudo-random implementation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, ClassVar

from .schemas import (
    ASSIGNMENT_SCHEMA_VERSION,
    EVENT_SCHEMA_VERSION,
    Condition,
    TaskPhase,
    TaskSet,
    UserStudyValidationError,
    browser_safe_task_set,
    canonical_json_sha256,
    parse_task_set,
    validate_task_set,
)
from .fairness import validate_study_environment_identity
from .questionnaires import (
    ANALYSIS_PLAN_SHA256,
    ANALYSIS_PLAN_VERSION,
    QUESTIONNAIRE_INSTRUMENT_SHA256,
    QUESTIONNAIRE_INSTRUMENT_VERSION,
    QUESTIONNAIRE_SCHEMA_SHA256,
    QUESTIONNAIRE_SCHEMA_VERSION,
)
from .scoring import FINAL_SELECTION_SCORING_VERSION


PROTOCOL_VERSION = "5.0.0"
ASSIGNMENT_GENERATOR_VERSION = "protocol-v5-user-study-counterbalance-v1.2.0"
PARTICIPANT_TARGET = 36
COUNTERBALANCE_CELL_COUNT = 12

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PSEUDONYM = re.compile(r"^P-[0-9a-f]{12}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_REVISION = re.compile(r"^(?:[0-9a-f]{40}|unknown)$")
_EMAIL_LIKE = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"
)
_FORBIDDEN_PROVENANCE_KEYS = frozenset(
    {
        "api_key",
        "code",
        "contact",
        "credential",
        "email",
        "intent",
        "name",
        "notebook",
        "operator",
        "owner",
        "participant",
        "password",
        "person",
        "private_key",
        "raw_intent",
        "secret",
        "token",
        "user",
        "user_name",
        "username",
    }
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise UserStudyValidationError(message)


def _safe_id(value: object, label: str) -> str:
    _require(isinstance(value, str), f"{label} must be a string")
    result = value.strip()
    _require(bool(_SAFE_ID.fullmatch(result)), f"{label} must be a safe identifier")
    return result


def _exact(value: object, fields: frozenset[str], label: str) -> dict[str, Any]:
    _require(isinstance(value, Mapping), f"{label} must be an object")
    payload = dict(value)
    missing = sorted(fields - set(payload))
    extra = sorted(set(payload) - fields)
    _require(not missing, f"{label} missing fields: {', '.join(missing)}")
    _require(not extra, f"{label} unexpected fields: {', '.join(extra)}")
    return payload


def _utc(value: object, label: str) -> str:
    _require(isinstance(value, str) and value.endswith("Z"), f"{label} must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise UserStudyValidationError(f"{label} must be ISO-8601 UTC") from exc
    _require(parsed.utcoffset() == timezone.utc.utcoffset(parsed), f"{label} must be UTC")
    return value


def _content_free_provenance(value: object, label: str, *, depth: int = 0) -> Any:
    """Return JSON provenance after rejecting identity/content/secret channels."""

    _require(depth <= 4, f"{label} is nested too deeply")
    if isinstance(value, Mapping):
        _require(len(value) <= 32, f"{label} has too many fields")
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = _safe_id(raw_key, f"{label} key")
            folded = key.casefold()
            _require(
                folded not in _FORBIDDEN_PROVENANCE_KEYS
                and not any(
                    marker in folded
                    for marker in (
                        "password",
                        "secret",
                        "credential",
                        "private_key",
                        "api_key",
                        "token",
                        "access_token",
                        "refresh_token",
                    )
                ),
                f"{label}.{key} is forbidden in content-free provenance",
            )
            result[key] = _content_free_provenance(
                child, f"{label}.{key}", depth=depth + 1
            )
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        _require(len(value) <= 64, f"{label} has too many entries")
        return [
            _content_free_provenance(child, f"{label}[{index}]", depth=depth + 1)
            for index, child in enumerate(value)
        ]
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        _require(math.isfinite(value), f"{label} must be finite")
        return value
    _require(isinstance(value, str), f"{label} must contain only JSON scalar metadata")
    _require(bool(value.strip()) and len(value) <= 256, f"{label} must be a short non-blank string")
    _require("\x00" not in value, f"{label} contains a NUL character")
    _require(not _EMAIL_LIKE.search(value), f"{label} appears to contain an email address")
    return value


def _environment_identity(value: object) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "environment_identity must be an object")
    normalized = _content_free_provenance(value, "environment_identity")
    _require(isinstance(normalized, dict), "environment_identity must be an object")
    _require(
        "environment_id" in normalized,
        "environment_identity requires a stable environment_id",
    )
    _safe_id(normalized["environment_id"], "environment_identity.environment_id")
    return normalized


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _key(seed: int, *parts: object) -> str:
    return hashlib.sha256(_canonical([seed, *parts])).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


@dataclass(frozen=True, slots=True)
class AssignmentTask:
    trial_id: str
    task_id: str
    pair_id: str
    variant_id: str
    phase: TaskPhase
    condition: Condition
    period: int
    position_in_period: int
    sequence_index: int

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "trial_id",
            "task_id",
            "pair_id",
            "variant_id",
            "phase",
            "condition",
            "period",
            "position_in_period",
            "sequence_index",
        }
    )

    def __post_init__(self) -> None:
        for name in ("trial_id", "task_id", "pair_id", "variant_id"):
            object.__setattr__(self, name, _safe_id(getattr(self, name), f"task.{name}"))
        _require(isinstance(self.phase, TaskPhase), "task.phase is unsupported")
        _require(isinstance(self.condition, Condition), "task.condition is unsupported")
        _require(self.period in {1, 2}, "task.period must be 1 or 2")
        _require(
            isinstance(self.position_in_period, int)
            and not isinstance(self.position_in_period, bool)
            and 0 <= self.position_in_period <= 3,
            "task.position_in_period must be 0..3",
        )
        _require(
            isinstance(self.sequence_index, int)
            and not isinstance(self.sequence_index, bool)
            and 0 <= self.sequence_index <= 7,
            "task.sequence_index must be 0..7",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "task_id": self.task_id,
            "pair_id": self.pair_id,
            "variant_id": self.variant_id,
            "phase": self.phase.value,
            "condition": self.condition.value,
            "period": self.period,
            "position_in_period": self.position_in_period,
            "sequence_index": self.sequence_index,
        }

    @classmethod
    def from_dict(cls, value: object) -> "AssignmentTask":
        payload = _exact(value, cls._FIELDS, "assigned task")
        try:
            phase = TaskPhase(payload["phase"])
            condition = Condition(payload["condition"])
        except (TypeError, ValueError) as exc:
            raise UserStudyValidationError("assigned task enum is unsupported") from exc
        return cls(
            trial_id=payload["trial_id"],
            task_id=payload["task_id"],
            pair_id=payload["pair_id"],
            variant_id=payload["variant_id"],
            phase=phase,
            condition=condition,
            period=payload["period"],
            position_in_period=payload["position_in_period"],
            sequence_index=payload["sequence_index"],
        )


@dataclass(frozen=True, slots=True)
class ParticipantAssignment:
    participant_id: str
    session_id: str
    counterbalance_cell: str
    condition_order: tuple[Condition, Condition]
    b0_variant_slot: int
    order_row: int
    task_sequence: tuple[AssignmentTask, ...]

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "participant_id",
            "session_id",
            "counterbalance_cell",
            "condition_order",
            "b0_variant_slot",
            "order_row",
            "task_sequence",
        }
    )

    def __post_init__(self) -> None:
        _require(
            isinstance(self.participant_id, str)
            and bool(_PSEUDONYM.fullmatch(self.participant_id)),
            "participant_id must be P-<12 hex>",
        )
        object.__setattr__(self, "session_id", _safe_id(self.session_id, "session_id"))
        object.__setattr__(
            self,
            "counterbalance_cell",
            _safe_id(self.counterbalance_cell, "counterbalance_cell"),
        )
        _require(
            len(self.condition_order) == 2
            and set(self.condition_order) == {Condition.B0, Condition.P2},
            "condition_order must contain B0 and P2 once",
        )
        _require(self.b0_variant_slot in {0, 1}, "b0_variant_slot must be 0 or 1")
        _require(self.order_row in {0, 1, 2}, "order_row must be 0..2")
        _require(
            self.counterbalance_cell
            == f"C-{self.condition_order[0].value}-{self.b0_variant_slot}-{self.order_row}",
            "counterbalance_cell differs from its condition, variant, or order factors",
        )
        _require(len(self.task_sequence) == 8, "participant requires eight assigned tasks")
        _require(
            [item.sequence_index for item in self.task_sequence] == list(range(8)),
            "task sequence indexes must be contiguous from zero",
        )
        _require(
            len({item.trial_id for item in self.task_sequence}) == 8,
            "trial IDs must be unique within a participant",
        )
        _require(
            [item.period for item in self.task_sequence] == [1] * 4 + [2] * 4,
            "task sequence must contain period 1 followed by period 2",
        )
        for period, condition in enumerate(self.condition_order, start=1):
            selected = [item for item in self.task_sequence if item.period == period]
            _require(len(selected) == 4, "each period requires one warm-up and three tasks")
            _require(
                all(item.condition is condition for item in selected),
                "assigned task condition differs from period condition",
            )
            _require(
                [item.position_in_period for item in selected] == list(range(4)),
                "period positions must be contiguous from zero",
            )
            _require(selected[0].phase is TaskPhase.WARM_UP, "period must start with warm-up")
            _require(
                all(item.phase is TaskPhase.MEASURED for item in selected[1:]),
                "period must contain three measured tasks after warm-up",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "session_id": self.session_id,
            "counterbalance_cell": self.counterbalance_cell,
            "condition_order": [item.value for item in self.condition_order],
            "b0_variant_slot": self.b0_variant_slot,
            "order_row": self.order_row,
            "task_sequence": [item.to_dict() for item in self.task_sequence],
        }

    @classmethod
    def from_dict(cls, value: object) -> "ParticipantAssignment":
        payload = _exact(value, cls._FIELDS, "participant assignment")
        order = payload["condition_order"]
        _require(isinstance(order, Sequence) and not isinstance(order, (str, bytes)), "condition_order must be an array")
        sequence = payload["task_sequence"]
        _require(isinstance(sequence, Sequence) and not isinstance(sequence, (str, bytes)), "task_sequence must be an array")
        try:
            parsed_order = tuple(Condition(item) for item in order)
        except (TypeError, ValueError) as exc:
            raise UserStudyValidationError("condition_order is unsupported") from exc
        _require(len(parsed_order) == 2, "condition_order requires two entries")
        return cls(
            participant_id=payload["participant_id"],
            session_id=payload["session_id"],
            counterbalance_cell=payload["counterbalance_cell"],
            condition_order=(parsed_order[0], parsed_order[1]),
            b0_variant_slot=payload["b0_variant_slot"],
            order_row=payload["order_row"],
            task_sequence=tuple(AssignmentTask.from_dict(item) for item in sequence),
        )


@dataclass(frozen=True, slots=True)
class AssignmentManifest:
    assignment_id: str
    study_id: str
    task_set_id: str
    task_set_sha256: str
    browser_task_set_sha256: str
    consent_version: str
    generator_version: str
    seed: int
    participant_count: int
    generated_at_utc: str
    git_revision: str
    freeze_id: str
    config_identity: str
    catalog_version: str
    corpus_version: str
    policy_version: str
    event_schema_version: str
    selection_scoring_version: str
    questionnaire_schema_version: str
    questionnaire_schema_sha256: str
    questionnaire_instrument_version: str
    questionnaire_instrument_sha256: str
    analysis_plan_version: str
    analysis_plan_sha256: str
    environment_identity: Mapping[str, Any]
    assignments: tuple[ParticipantAssignment, ...]
    balance_audit: Mapping[str, Any]
    schema_version: str = ASSIGNMENT_SCHEMA_VERSION
    protocol_version: str = PROTOCOL_VERSION

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "protocol_version",
            "assignment_id",
            "study_id",
            "task_set_id",
            "task_set_sha256",
            "browser_task_set_sha256",
            "consent_version",
            "generator_version",
            "seed",
            "participant_count",
            "generated_at_utc",
            "git_revision",
            "freeze_id",
            "config_identity",
            "catalog_version",
            "corpus_version",
            "policy_version",
            "event_schema_version",
            "selection_scoring_version",
            "questionnaire_schema_version",
            "questionnaire_schema_sha256",
            "questionnaire_instrument_version",
            "questionnaire_instrument_sha256",
            "analysis_plan_version",
            "analysis_plan_sha256",
            "environment_identity",
            "assignments",
            "balance_audit",
        }
    )

    def __post_init__(self) -> None:
        _require(self.schema_version == ASSIGNMENT_SCHEMA_VERSION, "unsupported assignment schema")
        _require(self.protocol_version == PROTOCOL_VERSION, "unsupported protocol version")
        for name in (
            "assignment_id",
            "study_id",
            "task_set_id",
            "consent_version",
            "generator_version",
            "freeze_id",
            "config_identity",
            "catalog_version",
            "corpus_version",
            "policy_version",
            "event_schema_version",
            "selection_scoring_version",
            "questionnaire_schema_version",
            "questionnaire_instrument_version",
            "analysis_plan_version",
        ):
            object.__setattr__(self, name, _safe_id(getattr(self, name), name))
        _require(bool(_SHA256.fullmatch(self.task_set_sha256)), "task_set_sha256 must be SHA-256")
        _require(bool(_SHA256.fullmatch(self.browser_task_set_sha256)), "browser_task_set_sha256 must be SHA-256")
        _require(bool(_SHA256.fullmatch(self.questionnaire_schema_sha256)), "questionnaire_schema_sha256 must be SHA-256")
        _require(bool(_SHA256.fullmatch(self.questionnaire_instrument_sha256)), "questionnaire_instrument_sha256 must be SHA-256")
        _require(bool(_SHA256.fullmatch(self.analysis_plan_sha256)), "analysis_plan_sha256 must be SHA-256")
        _require(
            isinstance(self.seed, int) and not isinstance(self.seed, bool) and self.seed >= 0,
            "seed must be a non-negative integer",
        )
        _require(
            isinstance(self.participant_count, int)
            and not isinstance(self.participant_count, bool)
            and self.participant_count > 0,
            "participant_count must be positive",
        )
        _utc(self.generated_at_utc, "generated_at_utc")
        _require(isinstance(self.git_revision, str) and bool(_GIT_REVISION.fullmatch(self.git_revision)), "git_revision must be a full lowercase SHA-1 or unknown")
        object.__setattr__(
            self,
            "environment_identity",
            _environment_identity(self.environment_identity),
        )
        _require(
            self.event_schema_version == EVENT_SCHEMA_VERSION,
            "assignment event_schema_version is unsupported",
        )
        _require(
            self.selection_scoring_version == FINAL_SELECTION_SCORING_VERSION,
            "assignment selection_scoring_version is unsupported",
        )
        _require(self.questionnaire_schema_version == QUESTIONNAIRE_SCHEMA_VERSION, "assignment questionnaire_schema_version is unsupported")
        _require(self.questionnaire_schema_sha256 == QUESTIONNAIRE_SCHEMA_SHA256, "assignment questionnaire-schema checksum is unsupported")
        _require(self.questionnaire_instrument_version == QUESTIONNAIRE_INSTRUMENT_VERSION, "assignment questionnaire_instrument_version is unsupported")
        _require(self.questionnaire_instrument_sha256 == QUESTIONNAIRE_INSTRUMENT_SHA256, "assignment questionnaire-instrument checksum is unsupported")
        _require(self.analysis_plan_version == ANALYSIS_PLAN_VERSION, "assignment analysis_plan_version is unsupported")
        _require(self.analysis_plan_sha256 == ANALYSIS_PLAN_SHA256, "assignment analysis-plan checksum is unsupported")
        object.__setattr__(
            self,
            "environment_identity",
            validate_study_environment_identity(
                self.environment_identity,
                confirmatory=self.freeze_id != "development-unfrozen",
            ),
        )
        _require(isinstance(self.balance_audit, Mapping), "balance_audit must be an object")
        json.dumps(self.balance_audit, allow_nan=False, sort_keys=True)
        _require(len(self.assignments) == self.participant_count, "participant_count differs from assignments")
        _require(
            len({item.participant_id for item in self.assignments}) == len(self.assignments),
            "participant pseudonyms must be unique",
        )
        _require(
            len({item.session_id for item in self.assignments}) == len(self.assignments),
            "session IDs must be unique",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "assignment_id": self.assignment_id,
            "study_id": self.study_id,
            "task_set_id": self.task_set_id,
            "task_set_sha256": self.task_set_sha256,
            "browser_task_set_sha256": self.browser_task_set_sha256,
            "consent_version": self.consent_version,
            "generator_version": self.generator_version,
            "seed": self.seed,
            "participant_count": self.participant_count,
            "generated_at_utc": self.generated_at_utc,
            "git_revision": self.git_revision,
            "freeze_id": self.freeze_id,
            "config_identity": self.config_identity,
            "catalog_version": self.catalog_version,
            "corpus_version": self.corpus_version,
            "policy_version": self.policy_version,
            "event_schema_version": self.event_schema_version,
            "selection_scoring_version": self.selection_scoring_version,
            "questionnaire_schema_version": self.questionnaire_schema_version,
            "questionnaire_schema_sha256": self.questionnaire_schema_sha256,
            "questionnaire_instrument_version": self.questionnaire_instrument_version,
            "questionnaire_instrument_sha256": self.questionnaire_instrument_sha256,
            "analysis_plan_version": self.analysis_plan_version,
            "analysis_plan_sha256": self.analysis_plan_sha256,
            "environment_identity": dict(self.environment_identity),
            "assignments": [item.to_dict() for item in self.assignments],
            "balance_audit": dict(self.balance_audit),
        }

    @classmethod
    def from_dict(cls, value: object) -> "AssignmentManifest":
        if isinstance(value, Mapping):
            legacy_fields = cls._FIELDS - {
                "event_schema_version",
                "selection_scoring_version",
                "questionnaire_schema_version",
                "questionnaire_schema_sha256",
                "questionnaire_instrument_version",
                "questionnaire_instrument_sha256",
                "analysis_plan_version",
                "analysis_plan_sha256",
            }
            if set(value) == legacy_fields:
                _require(
                    value.get("freeze_id") == "development-unfrozen",
                    "legacy assignment contracts are accepted only for development",
                )
                value = {
                    **dict(value),
                    "event_schema_version": EVENT_SCHEMA_VERSION,
                    "selection_scoring_version": FINAL_SELECTION_SCORING_VERSION,
                    "questionnaire_schema_version": QUESTIONNAIRE_SCHEMA_VERSION,
                    "questionnaire_schema_sha256": QUESTIONNAIRE_SCHEMA_SHA256,
                    "questionnaire_instrument_version": QUESTIONNAIRE_INSTRUMENT_VERSION,
                    "questionnaire_instrument_sha256": QUESTIONNAIRE_INSTRUMENT_SHA256,
                    "analysis_plan_version": ANALYSIS_PLAN_VERSION,
                    "analysis_plan_sha256": ANALYSIS_PLAN_SHA256,
                    "schema_version": ASSIGNMENT_SCHEMA_VERSION,
                }
        payload = _exact(value, cls._FIELDS, "assignment manifest")
        assignments = payload["assignments"]
        _require(isinstance(assignments, Sequence) and not isinstance(assignments, (str, bytes)), "assignments must be an array")
        _require(
            isinstance(payload["environment_identity"], Mapping),
            "environment_identity must be an object",
        )
        _require(
            isinstance(payload["balance_audit"], Mapping),
            "balance_audit must be an object",
        )
        return cls(
            schema_version=payload["schema_version"],
            protocol_version=payload["protocol_version"],
            assignment_id=payload["assignment_id"],
            study_id=payload["study_id"],
            task_set_id=payload["task_set_id"],
            task_set_sha256=payload["task_set_sha256"],
            browser_task_set_sha256=payload["browser_task_set_sha256"],
            consent_version=payload["consent_version"],
            generator_version=payload["generator_version"],
            seed=payload["seed"],
            participant_count=payload["participant_count"],
            generated_at_utc=payload["generated_at_utc"],
            git_revision=payload["git_revision"],
            freeze_id=payload["freeze_id"],
            config_identity=payload["config_identity"],
            catalog_version=payload["catalog_version"],
            corpus_version=payload["corpus_version"],
            policy_version=payload["policy_version"],
            event_schema_version=payload["event_schema_version"],
            selection_scoring_version=payload["selection_scoring_version"],
            questionnaire_schema_version=payload["questionnaire_schema_version"],
            questionnaire_schema_sha256=payload["questionnaire_schema_sha256"],
            questionnaire_instrument_version=payload["questionnaire_instrument_version"],
            questionnaire_instrument_sha256=payload["questionnaire_instrument_sha256"],
            analysis_plan_version=payload["analysis_plan_version"],
            analysis_plan_sha256=payload["analysis_plan_sha256"],
            environment_identity=dict(payload["environment_identity"]),
            assignments=tuple(ParticipantAssignment.from_dict(item) for item in assignments),
            balance_audit=dict(payload["balance_audit"]),
        )

    @property
    def checksum(self) -> str:
        return canonical_json_sha256(self.to_dict())

    def participant(self, participant_id: str) -> ParticipantAssignment:
        for item in self.assignments:
            if item.participant_id == participant_id:
                return item
        raise KeyError(participant_id)


def _cells(seed: int) -> list[tuple[Condition, int, int]]:
    remaining = [
        (first, slot, row)
        for first in (Condition.B0, Condition.P2)
        for slot in (0, 1)
        for row in range(3)
    ]
    ordered: list[tuple[Condition, int, int]] = []
    condition_counts: Counter[str] = Counter()
    slot_counts: Counter[int] = Counter()
    row_counts: Counter[int] = Counter()
    joint_counts: Counter[tuple[str, int]] = Counter()

    def imbalance(counts: Mapping[object, int], levels: Sequence[object]) -> int:
        values = [counts.get(level, 0) for level in levels]
        return max(values) - min(values)

    while remaining:
        scored: list[tuple[tuple[int, int, int, int], str, tuple[Condition, int, int]]] = []
        for cell in remaining:
            first, slot, row = cell
            next_condition = condition_counts.copy()
            next_slot = slot_counts.copy()
            next_row = row_counts.copy()
            next_joint = joint_counts.copy()
            next_condition[first.value] += 1
            next_slot[slot] += 1
            next_row[row] += 1
            next_joint[(first.value, slot)] += 1
            score = (
                imbalance(next_condition, (Condition.B0.value, Condition.P2.value)),
                imbalance(next_slot, (0, 1)),
                imbalance(next_row, (0, 1, 2)),
                imbalance(
                    next_joint,
                    tuple(
                        (condition.value, variant_slot)
                        for condition in (Condition.B0, Condition.P2)
                        for variant_slot in (0, 1)
                    ),
                ),
            )
            scored.append(
                (
                    score,
                    _key(seed, "cell", len(ordered), first.value, slot, row),
                    cell,
                )
            )
        _, _, selected = min(scored)
        ordered.append(selected)
        remaining.remove(selected)
        condition_counts[selected[0].value] += 1
        slot_counts[selected[1]] += 1
        row_counts[selected[2]] += 1
        joint_counts[(selected[0].value, selected[1])] += 1
    return ordered


def _participant_id(seed: int, study_id: str, ordinal: int) -> str:
    return "P-" + _key(seed, "participant", study_id, ordinal)[:12]


def _trial_id(seed: int, participant_id: str, sequence_index: int) -> str:
    return "T-" + _key(seed, "trial", participant_id, sequence_index)[:16]


def _balance(assignments: Sequence[ParticipantAssignment], measured_pairs: Sequence[str]) -> dict[str, Any]:
    first = Counter(item.condition_order[0].value for item in assignments)
    cells = Counter(item.counterbalance_cell for item in assignments)
    variant_by_condition: Counter[str] = Counter()
    pair_positions: Counter[str] = Counter()
    for item in assignments:
        for task in item.task_sequence:
            if task.phase is not TaskPhase.MEASURED:
                continue
            variant_by_condition[f"{task.pair_id}:{task.variant_id}:{task.condition.value}"] += 1
            pair_positions[f"{task.pair_id}:{task.position_in_period}"] += 1
    return {
        "cell_count": COUNTERBALANCE_CELL_COUNT,
        "condition_first": dict(sorted(first.items())),
        "counterbalance_cells": dict(sorted(cells.items())),
        "variant_by_condition": dict(sorted(variant_by_condition.items())),
        "measured_pair_positions": dict(sorted(pair_positions.items())),
        "measured_pair_ids": list(measured_pairs),
        "exact_target_balance": len(assignments) == PARTICIPANT_TARGET,
    }


def generate_assignment_manifest(
    task_set: TaskSet | Mapping[str, Any],
    *,
    study_id: str,
    participant_count: int,
    seed: int,
    consent_version: str,
    git_revision: str = "unknown",
    freeze_id: str = "development-unfrozen",
    config_identity: str = "development-unfrozen",
    environment_identity: Mapping[str, Any] | None = None,
    generated_at_utc: str | None = None,
    confirmatory: bool = False,
) -> AssignmentManifest:
    """Generate a deterministic, balanced assignment manifest."""

    parsed = validate_task_set(
        parse_task_set(task_set),
        confirmatory=confirmatory,
        require_protocol_design=True,
    )
    study_id = _safe_id(study_id, "study_id")
    consent_version = _safe_id(consent_version, "consent_version")
    freeze_id = _safe_id(freeze_id, "freeze_id")
    config_identity = _safe_id(config_identity, "config_identity")
    _require(
        isinstance(git_revision, str) and bool(_GIT_REVISION.fullmatch(git_revision)),
        "git_revision must be a full lowercase SHA-1 or unknown",
    )
    normalized_environment = _environment_identity(
        environment_identity
        or {
            "environment_id": "not-recorded",
            "mode": "development_unfrozen",
        }
    )
    normalized_environment = validate_study_environment_identity(
        normalized_environment, confirmatory=confirmatory
    )
    _require(
        isinstance(participant_count, int)
        and not isinstance(participant_count, bool)
        and participant_count > 0,
        "participant_count must be positive",
    )
    _require(isinstance(seed, int) and not isinstance(seed, bool) and seed >= 0, "seed must be non-negative")

    warmup = next(pair for pair in parsed.pairs if pair.phase is TaskPhase.WARM_UP)
    measured = sorted(
        (pair for pair in parsed.pairs if pair.phase is TaskPhase.MEASURED),
        key=lambda pair: pair.pair_id,
    )
    cell_order = _cells(seed)
    participants: list[ParticipantAssignment] = []
    for ordinal in range(participant_count):
        first_condition, b0_slot, order_row = cell_order[ordinal % len(cell_order)]
        second_condition = Condition.P2 if first_condition is Condition.B0 else Condition.B0
        condition_order = (first_condition, second_condition)
        ordered_pairs = measured[order_row:] + measured[:order_row]
        participant_id = _participant_id(seed, study_id, ordinal)
        session_id = "S-" + _key(seed, "session", study_id, ordinal)[:16]
        cell = f"C-{first_condition.value}-{b0_slot}-{order_row}"
        sequence: list[AssignmentTask] = []
        for period_index, condition in enumerate(condition_order, start=1):
            period_pairs = [warmup, *ordered_pairs]
            for position, pair in enumerate(period_pairs):
                variants = sorted(pair.tasks, key=lambda task: task.variant_id)
                slot = b0_slot if condition is Condition.B0 else 1 - b0_slot
                task = variants[slot]
                sequence_index = len(sequence)
                sequence.append(
                    AssignmentTask(
                        trial_id=_trial_id(seed, participant_id, sequence_index),
                        task_id=task.task_id,
                        pair_id=task.pair_id,
                        variant_id=task.variant_id,
                        phase=task.phase,
                        condition=condition,
                        period=period_index,
                        position_in_period=position,
                        sequence_index=sequence_index,
                    )
                )
        participants.append(
            ParticipantAssignment(
                participant_id=participant_id,
                session_id=session_id,
                counterbalance_cell=cell,
                condition_order=condition_order,
                b0_variant_slot=b0_slot,
                order_row=order_row,
                task_sequence=tuple(sequence),
            )
        )

    public = browser_safe_task_set(parsed)
    plan_identity = {
        "study_id": study_id,
        "task_set_id": parsed.task_set_id,
        "task_set_sha256": parsed.checksum,
        "browser_task_set_sha256": canonical_json_sha256(public),
        "consent_version": consent_version,
        "generator_version": ASSIGNMENT_GENERATOR_VERSION,
        "seed": seed,
        "participant_count": participant_count,
        "git_revision": git_revision,
        "freeze_id": freeze_id,
        "config_identity": config_identity,
        "catalog_version": parsed.catalog_version,
        "corpus_version": parsed.corpus_version,
        "policy_version": parsed.policy_version,
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "selection_scoring_version": FINAL_SELECTION_SCORING_VERSION,
        "questionnaire_schema_version": QUESTIONNAIRE_SCHEMA_VERSION,
        "questionnaire_schema_sha256": QUESTIONNAIRE_SCHEMA_SHA256,
        "questionnaire_instrument_version": QUESTIONNAIRE_INSTRUMENT_VERSION,
        "questionnaire_instrument_sha256": QUESTIONNAIRE_INSTRUMENT_SHA256,
        "analysis_plan_version": ANALYSIS_PLAN_VERSION,
        "analysis_plan_sha256": ANALYSIS_PLAN_SHA256,
        "environment_identity": normalized_environment,
        "assignments": [item.to_dict() for item in participants],
    }
    assignment_id = "A-" + canonical_json_sha256(plan_identity)[:20]
    manifest = AssignmentManifest(
        assignment_id=assignment_id,
        study_id=study_id,
        task_set_id=parsed.task_set_id,
        task_set_sha256=parsed.checksum,
        browser_task_set_sha256=canonical_json_sha256(public),
        consent_version=consent_version,
        generator_version=ASSIGNMENT_GENERATOR_VERSION,
        seed=seed,
        participant_count=participant_count,
        generated_at_utc=generated_at_utc or _now(),
        git_revision=git_revision,
        freeze_id=freeze_id,
        config_identity=config_identity,
        catalog_version=parsed.catalog_version,
        corpus_version=parsed.corpus_version,
        policy_version=parsed.policy_version,
        event_schema_version=EVENT_SCHEMA_VERSION,
        selection_scoring_version=FINAL_SELECTION_SCORING_VERSION,
        questionnaire_schema_version=QUESTIONNAIRE_SCHEMA_VERSION,
        questionnaire_schema_sha256=QUESTIONNAIRE_SCHEMA_SHA256,
        questionnaire_instrument_version=QUESTIONNAIRE_INSTRUMENT_VERSION,
        questionnaire_instrument_sha256=QUESTIONNAIRE_INSTRUMENT_SHA256,
        analysis_plan_version=ANALYSIS_PLAN_VERSION,
        analysis_plan_sha256=ANALYSIS_PLAN_SHA256,
        environment_identity=normalized_environment,
        assignments=tuple(participants),
        balance_audit=_balance(participants, [pair.pair_id for pair in measured]),
    )
    return validate_assignment_manifest(manifest, task_set=parsed)


def validate_assignment_manifest(
    manifest: AssignmentManifest | Mapping[str, Any],
    *,
    task_set: TaskSet | Mapping[str, Any] | None = None,
) -> AssignmentManifest:
    """Validate checksums, assigned tasks, crossover coverage, and balance."""

    parsed = manifest if isinstance(manifest, AssignmentManifest) else AssignmentManifest.from_dict(manifest)
    _require(parsed.generator_version == ASSIGNMENT_GENERATOR_VERSION, "unsupported assignment generator")
    if task_set is not None:
        tasks = validate_task_set(parse_task_set(task_set), require_protocol_design=True)
        _require(parsed.task_set_id == tasks.task_set_id, "assignment task_set_id mismatch")
        _require(parsed.task_set_sha256 == tasks.checksum, "assignment task-set checksum mismatch")
        _require(
            parsed.browser_task_set_sha256 == canonical_json_sha256(browser_safe_task_set(tasks)),
            "assignment browser task-set checksum mismatch",
        )
        for participant in parsed.assignments:
            seen_pairs: Counter[str] = Counter()
            for assigned in participant.task_sequence:
                task = tasks.task_by_id(assigned.task_id)
                _require(task.pair_id == assigned.pair_id, "assigned task pair mismatch")
                _require(task.variant_id == assigned.variant_id, "assigned task variant mismatch")
                _require(task.phase is assigned.phase, "assigned task phase mismatch")
                seen_pairs[assigned.pair_id] += 1
            _require(
                all(count == 2 for count in seen_pairs.values())
                and set(seen_pairs) == {pair.pair_id for pair in tasks.pairs},
                "participant must see both distinct variants of every pair once",
            )
            for pair in tasks.pairs:
                variants = {
                    item.variant_id
                    for item in participant.task_sequence
                    if item.pair_id == pair.pair_id
                }
                _require(
                    variants == {task.variant_id for task in pair.tasks},
                    "participant repeated or omitted a matched variant",
                )
                ordered_variants = sorted(pair.tasks, key=lambda item: item.variant_id)
                for assigned in (
                    item
                    for item in participant.task_sequence
                    if item.pair_id == pair.pair_id
                ):
                    expected_slot = (
                        participant.b0_variant_slot
                        if assigned.condition is Condition.B0
                        else 1 - participant.b0_variant_slot
                    )
                    _require(
                        assigned.variant_id == ordered_variants[expected_slot].variant_id,
                        "assigned variant differs from the counterbalance allocation",
                    )

            warmup_pair = next(
                pair for pair in tasks.pairs if pair.phase is TaskPhase.WARM_UP
            )
            measured_pairs = sorted(
                (pair for pair in tasks.pairs if pair.phase is TaskPhase.MEASURED),
                key=lambda pair: pair.pair_id,
            )
            expected_order = measured_pairs[participant.order_row :] + measured_pairs[
                : participant.order_row
            ]
            expected_pair_ids = [pair.pair_id for pair in expected_order]
            for period in (1, 2):
                period_tasks = [
                    item for item in participant.task_sequence if item.period == period
                ]
                _require(
                    period_tasks[0].pair_id == warmup_pair.pair_id,
                    "period warm-up pair differs from the task-set warm-up",
                )
                _require(
                    [item.pair_id for item in period_tasks[1:]] == expected_pair_ids,
                    "measured pair order differs from the cyclic order row",
                )
    expected = _balance(
        list(parsed.assignments),
        list(parsed.balance_audit.get("measured_pair_ids", [])),
    )
    _require(dict(parsed.balance_audit) == expected, "assignment balance audit does not recompute")
    if parsed.participant_count == PARTICIPANT_TARGET:
        _require(
            parsed.balance_audit["condition_first"] == {"B0": 18, "P2": 18},
            "36-participant condition-first balance must be exact",
        )
        _require(
            set(parsed.balance_audit["counterbalance_cells"].values()) == {3},
            "36 participants must repeat every counterbalance cell three times",
        )
        _require(
            len(parsed.balance_audit["variant_by_condition"]) == 12
            and set(parsed.balance_audit["variant_by_condition"].values()) == {18},
            "36 participants must balance every measured variant across B0 and P2",
        )
        _require(
            len(parsed.balance_audit["measured_pair_positions"]) == 9
            and set(parsed.balance_audit["measured_pair_positions"].values()) == {24},
            "36 participants must balance every measured pair across task positions",
        )
    identity = {
        "study_id": parsed.study_id,
        "task_set_id": parsed.task_set_id,
        "task_set_sha256": parsed.task_set_sha256,
        "browser_task_set_sha256": parsed.browser_task_set_sha256,
        "consent_version": parsed.consent_version,
        "generator_version": parsed.generator_version,
        "seed": parsed.seed,
        "participant_count": parsed.participant_count,
        "git_revision": parsed.git_revision,
        "freeze_id": parsed.freeze_id,
        "config_identity": parsed.config_identity,
        "catalog_version": parsed.catalog_version,
        "corpus_version": parsed.corpus_version,
        "policy_version": parsed.policy_version,
        "event_schema_version": parsed.event_schema_version,
        "selection_scoring_version": parsed.selection_scoring_version,
        "questionnaire_schema_version": parsed.questionnaire_schema_version,
        "questionnaire_schema_sha256": parsed.questionnaire_schema_sha256,
        "questionnaire_instrument_version": parsed.questionnaire_instrument_version,
        "questionnaire_instrument_sha256": parsed.questionnaire_instrument_sha256,
        "analysis_plan_version": parsed.analysis_plan_version,
        "analysis_plan_sha256": parsed.analysis_plan_sha256,
        "environment_identity": dict(parsed.environment_identity),
        "assignments": [item.to_dict() for item in parsed.assignments],
    }
    expected_assignment_id = "A-" + canonical_json_sha256(identity)[:20]
    _require(
        parsed.assignment_id == expected_assignment_id,
        "assignment_id does not bind the assignment manifest identity",
    )
    return parsed


def load_assignment_manifest(path: str) -> AssignmentManifest:
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    return validate_assignment_manifest(value)


__all__ = [
    "ASSIGNMENT_GENERATOR_VERSION",
    "COUNTERBALANCE_CELL_COUNT",
    "PARTICIPANT_TARGET",
    "AssignmentManifest",
    "AssignmentTask",
    "ParticipantAssignment",
    "generate_assignment_manifest",
    "load_assignment_manifest",
    "validate_assignment_manifest",
]
