"""Opt-in JupyterHub adapter for the Protocol-v5 B0-versus-P2 study.

This module is deliberately separate from :mod:`recommender.jupyterhub_integration`.
It wraps one already-created P2 ``RecommendationPreviewRuntime`` and uses that
same runtime catalog and fixed profile table in both study conditions.  B0
never calls the recommendation backend.

Only the browser-safe task projection is accepted here.  Researcher gold is
neither loaded nor mounted into the Hub pod.  Participant intent is passed
transiently to the existing preview runtime and is never included in study
events, gate records, annotations, or logs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import html
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any
import uuid

from recommender.candidate_corpus import (
    DEFAULT_PROFILE_DEFINITIONS,
    build_candidate_corpus,
)
from recommender.jupyterhub_integration import (
    PREVIEW_VERSION,
    PROFILE_RESOURCES,
    RecommendationPreviewRuntime,
    safe_escape_truncate,
    safe_json_dumps,
)

from .assignment import (
    AssignmentManifest,
    AssignmentTask,
    ParticipantAssignment,
    load_assignment_manifest,
)
from .instrumentation import AppendOnlyEventStore
from .fairness import validate_study_environment_identity, verify_fairness_manifest
from .questionnaires import (
    CUSTOM_ITEMS,
    CUSTOM_ITEM_IDS,
    FINAL_PREFERENCE_ID,
    QUESTIONNAIRE_INSTRUMENT_VERSION,
    QUESTIONNAIRE_SCHEMA_VERSION,
    SEQ_ITEM_ID,
    SUS_ITEMS,
    SUS_ITEM_IDS,
    QuestionnaireType,
    QuestionnaireValidationError,
    expected_questionnaire_ids,
    validate_questionnaire_record,
)
from .schemas import (
    BROWSER_TASK_SET_SCHEMA_VERSION,
    CancelReason,
    Condition,
    DECISION_LIMIT_SECONDS,
    EventType,
    PreviewStatus,
    READINESS_LIMIT_SECONDS,
    StudyEvent,
    canonical_json_sha256,
)


STUDY_HUB_ADAPTER_VERSION = "protocol-v5-user-study-hub-adapter-v1.1.0"
STUDY_HUB_PACKAGE_CHECKSUM_ENV = "INTENT_SPAWNER_USER_STUDY_PACKAGE_CHECKSUM"
STUDY_HUB_PACKAGE_VERSION_ENV = "INTENT_SPAWNER_USER_STUDY_PACKAGE_VERSION"
STUDY_ASSIGNMENT_CHECKSUM_ENV = "INTENT_SPAWNER_USER_STUDY_ASSIGNMENT_CHECKSUM"
STUDY_CONFIG_IDENTITY_ENV = "INTENT_SPAWNER_USER_STUDY_CONFIG_IDENTITY"
STUDY_ENVIRONMENT_ID_ENV = "INTENT_SPAWNER_USER_STUDY_ENVIRONMENT_ID"
STUDY_HUB_RUNTIME_FILES = (
    "__init__.py",
    "assignment.py",
    "hub.py",
    "instrumentation.py",
    "fairness.py",
    "questionnaires.py",
    "scoring.py",
    "schemas.py",
    "spawn_pending.html",
)
STUDY_HUB_MAX_PACKAGE_BYTES = 900 * 1024
CONSENT_ACK_SCHEMA_VERSION = "protocol-v5-user-study-consent-ack-v1.0.0"
TRANSITION_SCHEMA_VERSION = "protocol-v5-user-study-transition-v1.0.0"
INCOMPLETE_SCHEMA_VERSION = "protocol-v5-user-study-incomplete-v1.0.0"
SESSION_SCHEMA_VERSION = "protocol-v5-user-study-session-v1.0.0"
EXCLUSION_SCHEMA_VERSION = "protocol-v5-user-study-exclusion-v1.0.0"
EXCLUSION_REASON_VERSION = "protocol-v5-user-study-exclusion-reasons-v1.0.0"
DECISION_TIMEOUT_SECONDS = DECISION_LIMIT_SECONDS
READINESS_TIMEOUT_SECONDS = READINESS_LIMIT_SECONDS
CLEANUP_TIMEOUT_SECONDS = 30.0

_PARTICIPANT_ID = re.compile(r"^P-[0-9a-f]{12}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BROWSER_TOP_FIELDS = frozenset(
    {
        "schema_version",
        "source_task_set_id",
        "source_task_set_sha256",
        "language",
        "presentation_version",
        "pairs",
    }
)
_BROWSER_PAIR_FIELDS = frozenset({"pair_id", "phase", "tasks"})
_BROWSER_TASK_FIELDS = frozenset(
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


class StudyHubError(ValueError):
    """The study adapter cannot safely continue the requested operation."""


class StudySessionIncompleteError(StudyHubError):
    """A Hub restart or instrumentation failure invalidated the session."""


class DecisionDeadlineExpired(StudyHubError):
    """The 10-minute decision deadline passed before confirmation."""


def compute_study_adapter_checksum(package_dir: str | Path) -> str:
    """Hash the exact study-only Hub runtime using a stable file framing."""

    root = Path(package_dir)
    digest = hashlib.sha256()
    for name in STUDY_HUB_RUNTIME_FILES:
        content = (root / name).read_bytes()
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


def validate_study_adapter_package(
    package_dir: str | Path,
    *,
    expected_checksum: str,
    expected_version: str,
) -> None:
    root = Path(package_dir)
    missing = [name for name in STUDY_HUB_RUNTIME_FILES if not (root / name).is_file()]
    if missing:
        raise StudyHubError("study adapter package is incomplete: " + ", ".join(missing))
    size = sum((root / name).stat().st_size for name in STUDY_HUB_RUNTIME_FILES)
    if size > STUDY_HUB_MAX_PACKAGE_BYTES:
        raise StudyHubError("study adapter package exceeds the ConfigMap size limit")
    if expected_version != STUDY_HUB_ADAPTER_VERSION:
        raise StudyHubError("study adapter package version differs from mounted code")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_checksum):
        raise StudyHubError("study adapter checksum must be SHA-256")
    if compute_study_adapter_checksum(root) != expected_checksum:
        raise StudyHubError("study adapter package checksum differs from mounted code")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise StudyHubError("study wall clock must return an aware UTC datetime")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _first(formdata: Mapping[str, Any], name: str, default: str = "") -> str:
    values = formdata.get(name, [default])
    value = values[0] if isinstance(values, (list, tuple)) else values
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _study_participant_id(subject: object) -> str:
    current = getattr(subject, "current_user", None)
    if isinstance(current, str):
        candidate = current
    elif isinstance(getattr(current, "name", None), str):
        candidate = current.name
    else:
        user = getattr(subject, "user", None)
        if isinstance(user, str):
            candidate = user
        else:
            candidate = getattr(user, "name", "")
    # JupyterHub authenticators are allowed to normalize usernames. In
    # particular, DummyAuthenticator lowercases them by default. Accept only a
    # case-normalized version of the issued pseudonym syntax and immediately
    # restore the canonical research ID before assignment lookup or logging.
    if isinstance(candidate, str) and re.fullmatch(r"^[Pp]-[0-9a-f]{12}$", candidate):
        candidate = "P-" + candidate[2:]
    if not isinstance(candidate, str) or not _PARTICIPANT_ID.fullmatch(candidate):
        raise StudyHubError(
            "sign in with the issued pseudonymous participant ID P-<12 hex>"
        )
    return candidate


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise StudyHubError(f"{label} must be a safe identifier")
    return value


def _exact_mapping(
    value: object, fields: frozenset[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise StudyHubError(f"{label} must be an object")
    payload = dict(value)
    missing = sorted(fields - set(payload))
    extra = sorted(set(payload) - fields)
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise StudyHubError(f"{label} fields are invalid ({'; '.join(details)})")
    return payload


def validate_browser_task_set(value: object) -> dict[str, Any]:
    """Validate the gold-free presentation artifact accepted by the Hub."""

    root = _exact_mapping(value, _BROWSER_TOP_FIELDS, "browser task set")
    if root["schema_version"] != BROWSER_TASK_SET_SCHEMA_VERSION:
        raise StudyHubError("unsupported browser task-set schema")
    for name in ("source_task_set_id", "presentation_version"):
        _safe_id(root[name], f"browser task set {name}")
    checksum = root["source_task_set_sha256"]
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise StudyHubError("browser task-set source checksum must be SHA-256")
    if root["language"] != "en":
        raise StudyHubError("the Protocol-v5 study presentation must be English")
    pairs = root["pairs"]
    if not isinstance(pairs, Sequence) or isinstance(pairs, (str, bytes)):
        raise StudyHubError("browser task-set pairs must be an array")
    if not pairs:
        raise StudyHubError("browser task set cannot be empty")
    task_ids: set[str] = set()
    pair_ids: set[str] = set()
    normalized_pairs: list[dict[str, Any]] = []
    for pair_index, raw_pair in enumerate(pairs):
        pair = _exact_mapping(
            raw_pair, _BROWSER_PAIR_FIELDS, f"browser pair {pair_index}"
        )
        pair_id = _safe_id(pair["pair_id"], f"browser pair {pair_index} pair_id")
        if pair_id in pair_ids:
            raise StudyHubError("browser task-set pair IDs must be unique")
        pair_ids.add(pair_id)
        if pair["phase"] not in {"warm_up", "measured"}:
            raise StudyHubError("browser pair phase is unsupported")
        tasks = pair["tasks"]
        if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes)):
            raise StudyHubError("browser pair tasks must be an array")
        if len(tasks) != 2:
            raise StudyHubError("each browser pair must contain exactly two tasks")
        normalized_tasks: list[dict[str, Any]] = []
        for task_index, raw_task in enumerate(tasks):
            task = _exact_mapping(
                raw_task,
                _BROWSER_TASK_FIELDS,
                f"browser task {pair_index}.{task_index}",
            )
            task_id = _safe_id(task["task_id"], "browser task task_id")
            if task_id in task_ids:
                raise StudyHubError("browser task IDs must be unique")
            task_ids.add(task_id)
            if task["pair_id"] != pair_id or task["phase"] != pair["phase"]:
                raise StudyHubError("browser task identity differs from its pair")
            _safe_id(task["variant_id"], "browser task variant_id")
            _safe_id(task["presentation_version"], "browser presentation version")
            if task["language"] != "en":
                raise StudyHubError("browser tasks must be English")
            if task["difficulty"] not in {"easy", "medium", "hard"}:
                raise StudyHubError("browser task difficulty is unsupported")
            scenario = task["scenario"]
            if not isinstance(scenario, str) or not scenario.strip():
                raise StudyHubError("browser task scenario must be non-blank")
            if len(scenario) > 2000 or "\x00" in scenario:
                raise StudyHubError("browser task scenario is invalid")
            normalized_tasks.append(dict(task))
        normalized_pairs.append(
            {"pair_id": pair_id, "phase": pair["phase"], "tasks": normalized_tasks}
        )
    normalized = dict(root)
    normalized["pairs"] = normalized_pairs
    # Defence in depth: no researcher-only field name can appear at any depth.
    serialized = json.dumps(normalized, sort_keys=True)
    forbidden = (
        '"gold"',
        '"requirements"',
        '"acceptable_candidate_ids"',
        '"preferred_candidate_id"',
        '"policy_constraints"',
    )
    if any(field in serialized for field in forbidden):
        raise StudyHubError("browser task set contains researcher-only gold")
    return normalized


def load_browser_task_set(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return validate_browser_task_set(json.load(handle))


def shared_option_snapshot(runtime: RecommendationPreviewRuntime) -> dict[str, Any]:
    """Return the single ordered profile/image panel used by B0 and P2."""

    profiles = []
    for profile_id, resources in PROFILE_RESOURCES.items():
        definition = DEFAULT_PROFILE_DEFINITIONS[profile_id]
        profiles.append(
            {
                "profile_id": profile_id,
                "display_name": definition["display_name"],
                "description": definition["description"],
                "resources": dict(resources),
            }
        )
    images = []
    for image_id, image in runtime.images.items():
        images.append(
            {
                "image_id": image_id,
                "display_name": image["display_name"],
                "description": image["description"],
                "reference": image["reference"],
            }
        )
    return {
        "profiles": profiles,
        "images": images,
        "policy_version": runtime.policy.policy_version,
        "catalog_version": runtime.catalog["catalog_version"],
    }


def shared_option_snapshot_sha256(runtime: RecommendationPreviewRuntime) -> str:
    return canonical_json_sha256(shared_option_snapshot(runtime))


class StudyGateStore:
    """Exclusive, content-free consent/transition/incomplete gate records."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, kind: str, session_id: str) -> Path:
        _safe_id(session_id, "session_id")
        return self.root / kind / f"{session_id}.json"

    @staticmethod
    def _write_all(fd: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("study gate write made no progress")
            view = view[written:]

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _write_exclusive(
        self, kind: str, session_id: str, record: Mapping[str, Any]
    ) -> Path:
        path = self._path(kind, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(
                dict(record),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            existing = path.read_bytes()
            if existing != encoded:
                raise StudyHubError(f"conflicting immutable {kind} marker")
            return path
        try:
            self._write_all(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)
        self._fsync_directory(path.parent)
        return path

    def _read_marker(self, kind: str, session_id: str) -> dict[str, Any]:
        path = self._path(kind, session_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudyHubError(f"{kind} marker is unavailable or corrupted") from exc
        if not isinstance(value, Mapping):
            raise StudyHubError(f"{kind} marker must be an object")
        return dict(value)

    def _append_record_once(
        self,
        filename: str,
        record: Mapping[str, Any],
        *,
        identity_field: str,
        uniqueness_fields: Sequence[str] = (),
        idempotent_ignored_fields: Sequence[str] = (),
    ) -> Path:
        """Durably append one canonical staging record with ID idempotency."""

        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(
                dict(record),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        fd = os.open(path, flags, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.lseek(fd, 0, os.SEEK_SET)
            raw = b""
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                raw += chunk
            if raw and not raw.endswith(b"\n"):
                raise StudyHubError(f"{filename} has an incomplete final record")
            for line in raw.splitlines():
                try:
                    existing = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise StudyHubError(f"{filename} contains invalid JSON") from exc
                if not isinstance(existing, Mapping):
                    raise StudyHubError(f"{filename} records must be objects")
                if existing.get(identity_field) != record.get(identity_field):
                    if uniqueness_fields and all(
                        existing.get(field) == record.get(field)
                        for field in uniqueness_fields
                    ):
                        raise StudyHubError(
                            f"{filename} scheduled record was already submitted"
                        )
                    continue
                existing_comparable = dict(existing)
                record_comparable = dict(record)
                for field in idempotent_ignored_fields:
                    existing_comparable.pop(field, None)
                    record_comparable.pop(field, None)
                if existing_comparable == record_comparable:
                    return path
                raise StudyHubError(
                    f"{filename} contains a conflicting {identity_field} record"
                )
            self._write_all(fd, encoded)
            os.fsync(fd)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        self._fsync_directory(path.parent)
        return path

    def has_consent(self, session_id: str, consent_version: str) -> bool:
        path = self._path("consent-acks", session_id)
        if not path.is_file():
            return False
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudyHubError("consent acknowledgement is corrupted") from exc
        return (
            isinstance(record, Mapping)
            and record.get("schema_version") == CONSENT_ACK_SCHEMA_VERSION
            and record.get("session_id") == session_id
            and record.get("consent_version") == consent_version
            and record.get("acknowledged") is True
        )

    def acknowledge_consent(
        self,
        manifest: AssignmentManifest,
        participant: ParticipantAssignment,
        *,
        acknowledged_at_utc: str,
    ) -> Path:
        record = {
            "schema_version": CONSENT_ACK_SCHEMA_VERSION,
            "study_id": manifest.study_id,
            "assignment_id": manifest.assignment_id,
            "session_id": participant.session_id,
            "participant_id": participant.participant_id,
            "consent_version": manifest.consent_version,
            "acknowledged": True,
            "acknowledged_at_utc": acknowledged_at_utc,
        }
        return self._write_exclusive("consent-acks", participant.session_id, record)

    def has_transition(self, session_id: str) -> bool:
        return self._path("transition-markers", session_id).is_file()

    def acknowledge_transition(
        self,
        manifest: AssignmentManifest,
        participant: ParticipantAssignment,
        *,
        acknowledged_at_utc: str,
    ) -> Path:
        record = {
            "schema_version": TRANSITION_SCHEMA_VERSION,
            "study_id": manifest.study_id,
            "assignment_id": manifest.assignment_id,
            "session_id": participant.session_id,
            "participant_id": participant.participant_id,
            "after_period": 1,
            "acknowledged_at_utc": acknowledged_at_utc,
        }
        return self._write_exclusive(
            "transition-markers", participant.session_id, record
        )

    def mark_incomplete(
        self,
        manifest: AssignmentManifest,
        participant: ParticipantAssignment,
        assigned: AssignmentTask,
        *,
        last_event: Mapping[str, Any],
        detected_at_utc: str,
    ) -> Path:
        record = {
            "schema_version": INCOMPLETE_SCHEMA_VERSION,
            "study_id": manifest.study_id,
            "assignment_id": manifest.assignment_id,
            "session_id": participant.session_id,
            "participant_id": participant.participant_id,
            "trial_id": assigned.trial_id,
            "task_id": assigned.task_id,
            "reason": "hub_restart",
            "detected_at_utc": detected_at_utc,
            "last_event_uuid": last_event["event_uuid"],
            "last_event_index": last_event["event_index"],
        }
        return self._write_exclusive(
            "incomplete-markers", participant.session_id, record
        )

    def is_incomplete(self, session_id: str) -> bool:
        return self._path("incomplete-markers", session_id).is_file()

    def record_completed_session(
        self,
        manifest: AssignmentManifest,
        participant: ParticipantAssignment,
        *,
        completed_at_utc: str,
    ) -> Path:
        consent = self._read_marker("consent-acks", participant.session_id)
        record = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "study_id": manifest.study_id,
            "assignment_id": manifest.assignment_id,
            "session_id": participant.session_id,
            "participant_id": participant.participant_id,
            "consent_version": manifest.consent_version,
            "consent_acknowledged": consent.get("acknowledged") is True,
            "session_status": "complete",
            "started_at_utc": consent.get("acknowledged_at_utc"),
            "ended_at_utc": completed_at_utc,
        }
        if not record["consent_acknowledged"]:
            raise StudyHubError("completed session lacks consent acknowledgement")
        return self._append_record_once(
            "sessions.jsonl", record, identity_field="session_id"
        )

    def record_incomplete_session(
        self,
        manifest: AssignmentManifest,
        participant: ParticipantAssignment,
        *,
        detected_at_utc: str,
    ) -> tuple[Path, Path]:
        consent = self._read_marker("consent-acks", participant.session_id)
        session = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "study_id": manifest.study_id,
            "assignment_id": manifest.assignment_id,
            "session_id": participant.session_id,
            "participant_id": participant.participant_id,
            "consent_version": manifest.consent_version,
            "consent_acknowledged": consent.get("acknowledged") is True,
            "session_status": "excluded",
            "started_at_utc": consent.get("acknowledged_at_utc"),
            "ended_at_utc": detected_at_utc,
        }
        exclusion = {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "reason_version": EXCLUSION_REASON_VERSION,
            "study_id": manifest.study_id,
            "assignment_id": manifest.assignment_id,
            "session_id": participant.session_id,
            "participant_id": participant.participant_id,
            "reason_code": "instrumentation_corruption",
            "recorded_at_utc": detected_at_utc,
        }
        if not session["consent_acknowledged"]:
            raise StudyHubError("incomplete session lacks consent acknowledgement")
        return (
            self._append_record_once(
                "sessions.jsonl", session, identity_field="session_id"
            ),
            self._append_record_once(
                "exclusions.jsonl", exclusion, identity_field="session_id"
            ),
        )

    def questionnaire_records(self) -> list[dict[str, Any]]:
        path = self.root / "questionnaires.jsonl"
        if not path.exists():
            return []
        try:
            fd = os.open(path, os.O_RDONLY)
        except FileNotFoundError:
            return []
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        if raw and not raw.endswith(b"\n"):
            raise StudyHubError("questionnaires.jsonl has an incomplete final record")
        records: list[dict[str, Any]] = []
        for line in raw.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise StudyHubError("questionnaires.jsonl contains invalid JSON") from exc
            records.append(validate_questionnaire_record(value).to_dict())
        return records

    def append_questionnaire(self, record: Mapping[str, Any]) -> Path:
        validated = validate_questionnaire_record(record).to_dict()
        return self._append_record_once(
            "questionnaires.jsonl",
            validated,
            identity_field="response_uuid",
            uniqueness_fields=("session_id", "questionnaire_id"),
            idempotent_ignored_fields=("submitted_at_utc",),
        )


@dataclass(frozen=True, slots=True)
class ActiveStudyTask:
    participant: ParticipantAssignment
    assigned: AssignmentTask
    scenario: str


class StudySessionRuntime:
    """Server-owned assignment, event, deadline, and readiness state."""

    def __init__(
        self,
        *,
        assignment_manifest: AssignmentManifest,
        browser_task_set: Mapping[str, Any],
        recommendation_runtime: RecommendationPreviewRuntime,
        event_store: AppendOnlyEventStore,
        gate_store: StudyGateStore,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = _utc_now,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
        mark_restart_incomplete: bool = True,
    ) -> None:
        self.manifest = assignment_manifest
        self.browser_task_set = validate_browser_task_set(browser_task_set)
        self.recommendation_runtime = recommendation_runtime
        self.event_store = event_store
        self.gate_store = gate_store
        self.monotonic = monotonic
        self.utc_now = utc_now
        self.uuid_factory = uuid_factory
        self._lock = threading.RLock()
        self._trial_starts: dict[tuple[str, str], float] = {}
        self._decision_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._readiness_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._readiness_missing: set[tuple[str, str]] = set()

        if (
            self.browser_task_set["source_task_set_id"] != self.manifest.task_set_id
            or self.browser_task_set["source_task_set_sha256"]
            != self.manifest.task_set_sha256
            or canonical_json_sha256(self.browser_task_set)
            != self.manifest.browser_task_set_sha256
        ):
            raise StudyHubError("assignment and browser task-set checksums differ")
        if recommendation_runtime.deployment.backend != "p2":
            raise StudyHubError("the Protocol-v5 P2 condition requires the frozen P2 backend")
        if recommendation_runtime.resource_enricher is not None:
            raise StudyHubError(
                "study fairness requires the same fixed profile envelopes in B0 and P2"
            )
        if recommendation_runtime.catalog["catalog_version"] != self.manifest.catalog_version:
            raise StudyHubError("assignment catalog version differs from the live Hub")
        if recommendation_runtime.policy.policy_version != self.manifest.policy_version:
            raise StudyHubError("assignment policy version differs from the live Hub")
        confirmatory = self.manifest.freeze_id != "development-unfrozen"
        environment_identity = validate_study_environment_identity(
            self.manifest.environment_identity, confirmatory=confirmatory
        )
        fairness_manifest = environment_identity.get("fairness_manifest")
        if fairness_manifest is not None:
            try:
                verify_fairness_manifest(
                    fairness_manifest,
                    catalog=recommendation_runtime.catalog,
                    corpus=build_candidate_corpus(
                        image_catalog=recommendation_runtime.catalog
                    ),
                    freeze_id=self.manifest.freeze_id,
                    config_identity=self.manifest.config_identity,
                    deployment_revision=self.manifest.git_revision,
                    kubernetes_environment_id=str(
                        fairness_manifest["kubernetes_environment_id"]
                    ),
                    confirmatory=confirmatory,
                )
            except ValueError as exc:
                raise StudyHubError(
                    "live Hub differs from the frozen B0/P2 fairness identity"
                ) from exc

        self._tasks = {
            task["task_id"]: dict(task)
            for pair in self.browser_task_set["pairs"]
            for task in pair["tasks"]
        }
        assigned_task_ids = {
            task.task_id
            for participant in self.manifest.assignments
            for task in participant.task_sequence
        }
        if assigned_task_ids != set(self._tasks):
            raise StudyHubError("browser task set differs from assigned scenarios")
        if mark_restart_incomplete:
            self._mark_orphaned_trials()

    def participant(self, participant_id: str) -> ParticipantAssignment:
        if not _PARTICIPANT_ID.fullmatch(participant_id):
            raise StudyHubError("participant ID is not an issued study pseudonym")
        try:
            return self.manifest.participant(participant_id)
        except KeyError as exc:
            raise StudyHubError("participant pseudonym is absent from the assignment") from exc

    def _events(self) -> list[dict[str, Any]]:
        return self.event_store.read_events()

    def _trial_events(self, session_id: str, trial_id: str) -> list[dict[str, Any]]:
        return [
            event
            for event in self._events()
            if event["session_id"] == session_id and event["trial_id"] == trial_id
        ]

    @staticmethod
    def _is_terminal(events: Sequence[Mapping[str, Any]]) -> bool:
        return any(event["event_type"] in {"confirm", "cancel"} for event in events)

    def _mark_orphaned_trials(self) -> None:
        try:
            events = self._events()
        except ValueError:
            # Corruption is intentionally not converted into restart state.
            raise
        by_trial: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for event in events:
            by_trial.setdefault((event["session_id"], event["trial_id"]), []).append(event)
        detected = _utc_text(self.utc_now())
        for participant in self.manifest.assignments:
            for assigned in participant.task_sequence:
                trial = by_trial.get((participant.session_id, assigned.trial_id), [])
                if trial and not self._is_terminal(trial):
                    if not self.gate_store.is_incomplete(participant.session_id):
                        self.gate_store.mark_incomplete(
                            self.manifest,
                            participant,
                            assigned,
                            last_event=trial[-1],
                            detected_at_utc=detected,
                        )
                    marker = self.gate_store._read_marker(
                        "incomplete-markers", participant.session_id
                    )
                    self.gate_store.record_incomplete_session(
                        self.manifest,
                        participant,
                        detected_at_utc=str(marker["detected_at_utc"]),
                    )

    def has_consent(self, participant_id: str) -> bool:
        participant = self.participant(participant_id)
        return self.gate_store.has_consent(
            participant.session_id, self.manifest.consent_version
        )

    def acknowledge_consent(self, participant_id: str) -> Path:
        participant = self.participant(participant_id)
        if self.gate_store.has_consent(
            participant.session_id, self.manifest.consent_version
        ):
            return self.gate_store._path("consent-acks", participant.session_id)
        return self.gate_store.acknowledge_consent(
            self.manifest,
            participant,
            acknowledged_at_utc=_utc_text(self.utc_now()),
        )

    def acknowledge_transition(self, participant_id: str) -> Path:
        participant = self.participant(participant_id)
        if self.gate_store.has_transition(participant.session_id):
            return self.gate_store._path("transition-markers", participant.session_id)
        return self.gate_store.acknowledge_transition(
            self.manifest,
            participant,
            acknowledged_at_utc=_utc_text(self.utc_now()),
        )

    def current_task(self, participant_id: str) -> ActiveStudyTask | None:
        participant = self.participant(participant_id)
        if self.gate_store.is_incomplete(participant.session_id):
            raise StudySessionIncompleteError(
                "the Hub restarted during an active task; this session is incomplete"
            )
        events = self._events()
        for assigned in participant.task_sequence:
            trial = [
                event
                for event in events
                if event["session_id"] == participant.session_id
                and event["trial_id"] == assigned.trial_id
            ]
            if not self._is_terminal(trial):
                return ActiveStudyTask(
                    participant=participant,
                    assigned=assigned,
                    scenario=self._tasks[assigned.task_id]["scenario"],
                )
        return None

    def pending_questionnaire(self, participant_id: str) -> dict[str, Any] | None:
        """Return the next scheduled form after its task/period is terminal."""

        participant = self.participant(participant_id)
        submitted = {
            row["questionnaire_id"]
            for row in self.gate_store.questionnaire_records()
            if row["session_id"] == participant.session_id
        }
        events = self._events()
        for assigned in participant.task_sequence:
            terminal = self._is_terminal(
                [
                    event
                    for event in events
                    if event["session_id"] == participant.session_id
                    and event["trial_id"] == assigned.trial_id
                ]
            )
            if not terminal:
                return None
            if assigned.phase.value == "measured":
                questionnaire_id = f"seq:{assigned.trial_id}"
                if questionnaire_id not in submitted:
                    return {
                        "questionnaire_type": QuestionnaireType.SEQ_TASK.value,
                        "questionnaire_id": questionnaire_id,
                        "condition": assigned.condition.value,
                        "period": assigned.period,
                        "trial_id": assigned.trial_id,
                        "task_id": assigned.task_id,
                        "pair_id": assigned.pair_id,
                    }
                if assigned.position_in_period == 3:
                    questionnaire_id = f"post_condition:{assigned.period}"
                    if questionnaire_id not in submitted:
                        return {
                            "questionnaire_type": QuestionnaireType.POST_CONDITION.value,
                            "questionnaire_id": questionnaire_id,
                            "condition": assigned.condition.value,
                            "period": assigned.period,
                            "trial_id": None,
                            "task_id": None,
                            "pair_id": None,
                        }
        if "final_preference" not in submitted:
            return {
                "questionnaire_type": QuestionnaireType.FINAL_PREFERENCE.value,
                "questionnaire_id": "final_preference",
                "condition": None,
                "period": None,
                "trial_id": None,
                "task_id": None,
                "pair_id": None,
            }
        return None

    def record_questionnaire(
        self,
        participant_id: str,
        questionnaire_id: str,
        response_uuid: str,
        responses: Mapping[str, Any],
    ) -> dict[str, Any]:
        participant = self.participant(participant_id)
        pending = self.pending_questionnaire(participant_id)
        if pending is None or pending["questionnaire_id"] != questionnaire_id:
            raise StudyHubError("questionnaire is not currently scheduled")
        record = validate_questionnaire_record(
            {
                "schema_version": QUESTIONNAIRE_SCHEMA_VERSION,
                "instrument_version": QUESTIONNAIRE_INSTRUMENT_VERSION,
                "study_id": self.manifest.study_id,
                "assignment_id": self.manifest.assignment_id,
                "session_id": participant.session_id,
                "participant_id": participant.participant_id,
                "response_uuid": response_uuid,
                **pending,
                "responses": dict(responses),
                "submitted_at_utc": _utc_text(self.utc_now()),
            }
        ).to_dict()
        self.gate_store.append_questionnaire(record)
        return record

    def questionnaire_complete(self, participant_id: str) -> bool:
        participant = self.participant(participant_id)
        observed = {
            row["questionnaire_id"]
            for row in self.gate_store.questionnaire_records()
            if row["session_id"] == participant.session_id
        }
        return observed == expected_questionnaire_ids(participant)

    def transition_required(self, active: ActiveStudyTask) -> bool:
        return (
            active.assigned.sequence_index == 4
            and not self.gate_store.has_transition(active.participant.session_id)
        )

    def ensure_task_shown(self, active: ActiveStudyTask) -> StudyEvent:
        if not self.has_consent(active.participant.participant_id):
            raise StudyHubError("consent-version acknowledgement is required")
        if self.transition_required(active):
            raise StudyHubError("the standardized transition must be acknowledged")
        key = (active.participant.session_id, active.assigned.trial_id)
        with self._lock:
            existing = self._trial_events(*key)
            if existing:
                first = StudyEvent.from_dict(existing[0])
                if first.event_type is not EventType.TASK_SHOWN:
                    raise StudyHubError("trial event stream does not start with task_shown")
                if key not in self._trial_starts:
                    # Existing nonterminal events at construction are marked incomplete.
                    raise StudySessionIncompleteError(
                        "cannot resume a task with an incompatible monotonic clock"
                    )
                return first
            self._trial_starts[key] = self.monotonic()
            event = self._build_event(active, EventType.TASK_SHOWN, event_index=0)
            self.event_store.append(event)
            return event

    def _build_event(
        self,
        active: ActiveStudyTask,
        event_type: EventType,
        *,
        event_index: int,
        profile_id: str | None = None,
        image_id: str | None = None,
        old_profile_id: str | None = None,
        new_profile_id: str | None = None,
        old_image_id: str | None = None,
        new_image_id: str | None = None,
        preview_status: PreviewStatus | None = None,
        cancel_reason: CancelReason | None = None,
        monotonic_seconds: float | None = None,
    ) -> StudyEvent:
        key = (active.participant.session_id, active.assigned.trial_id)
        if monotonic_seconds is None:
            start = self._trial_starts.get(key)
            if start is None:
                raise StudySessionIncompleteError("trial monotonic origin is unavailable")
            monotonic_seconds = max(0.0, self.monotonic() - start)
        return StudyEvent(
            study_id=self.manifest.study_id,
            assignment_id=self.manifest.assignment_id,
            session_id=active.participant.session_id,
            participant_id=active.participant.participant_id,
            trial_id=active.assigned.trial_id,
            task_id=active.assigned.task_id,
            pair_id=active.assigned.pair_id,
            condition=active.assigned.condition,
            consent_version=self.manifest.consent_version,
            event_uuid=str(self.uuid_factory()),
            event_index=event_index,
            timestamp_utc=_utc_text(self.utc_now()),
            monotonic_seconds=monotonic_seconds,
            event_type=event_type,
            profile_id=profile_id,
            image_id=image_id,
            old_profile_id=old_profile_id,
            new_profile_id=new_profile_id,
            old_image_id=old_image_id,
            new_image_id=new_image_id,
            preview_status=preview_status,
            cancel_reason=cancel_reason,
        )

    def _active_for_trial(self, participant_id: str, trial_id: str) -> ActiveStudyTask:
        current = self.current_task(participant_id)
        if current is None or current.assigned.trial_id != trial_id:
            raise StudyHubError("trial is not the participant's current assignment")
        return current

    def _elapsed(self, active: ActiveStudyTask) -> float:
        key = (active.participant.session_id, active.assigned.trial_id)
        start = self._trial_starts.get(key)
        if start is None:
            raise StudySessionIncompleteError("trial monotonic origin is unavailable")
        return max(0.0, self.monotonic() - start)

    def _cancel_timeout_once(self, active: ActiveStudyTask) -> StudyEvent | None:
        trial = self._trial_events(
            active.participant.session_id, active.assigned.trial_id
        )
        if self._is_terminal(trial):
            return None
        elapsed = self._elapsed(active)
        event = self._build_event(
            active,
            EventType.CANCEL,
            event_index=len(trial),
            cancel_reason=CancelReason.DECISION_TIMEOUT,
            monotonic_seconds=max(DECISION_TIMEOUT_SECONDS, elapsed),
        )
        self.event_store.append(event)
        self._cancel_decision_task(active)
        return event

    def _enforce_deadline(self, active: ActiveStudyTask) -> None:
        if self._elapsed(active) >= DECISION_TIMEOUT_SECONDS:
            self._cancel_timeout_once(active)
            raise DecisionDeadlineExpired("the 10-minute decision limit expired")

    def record(
        self,
        participant_id: str,
        trial_id: str,
        event_type: EventType | str,
        *,
        event_uuid: str | None = None,
        **fields: Any,
    ) -> StudyEvent:
        parsed_type = EventType(event_type)
        with self._lock:
            for field_name in ("profile_id", "old_profile_id", "new_profile_id"):
                value = fields.get(field_name)
                if value is not None and value not in PROFILE_RESOURCES:
                    raise StudyHubError(f"{field_name} is not an allowlisted profile")
            for field_name in ("image_id", "old_image_id", "new_image_id"):
                value = fields.get(field_name)
                if (
                    value is not None
                    and value not in self.recommendation_runtime.images
                ):
                    raise StudyHubError(f"{field_name} is not an allowlisted image")
            if event_uuid is not None:
                try:
                    parsed_uuid = uuid.UUID(event_uuid)
                except (AttributeError, TypeError, ValueError) as exc:
                    raise StudyHubError("event_uuid must be a canonical UUID") from exc
                if str(parsed_uuid) != event_uuid.lower():
                    raise StudyHubError("event_uuid must be a canonical UUID")
                for durable in self._events():
                    if durable["event_uuid"] != event_uuid:
                        continue
                    requested = {
                        key: (value.value if hasattr(value, "value") else value)
                        for key, value in fields.items()
                    }
                    if (
                        durable["participant_id"] == participant_id
                        and durable["trial_id"] == trial_id
                        and durable["event_type"] == parsed_type.value
                        and all(durable.get(key) == value for key, value in requested.items())
                    ):
                        return StudyEvent.from_dict(durable)
                    raise StudyHubError("event_uuid was reused for a different event")
            active = self._active_for_trial(participant_id, trial_id)
            if (
                parsed_type is EventType.CANCEL
                and fields.get("cancel_reason") is CancelReason.DECISION_TIMEOUT
            ):
                if self._elapsed(active) < DECISION_TIMEOUT_SECONDS:
                    raise StudyHubError("the server decision deadline has not expired")
                self._cancel_timeout_once(active)
                raise DecisionDeadlineExpired("the 10-minute decision limit expired")
            if parsed_type not in {EventType.CANCEL, EventType.NOTEBOOK_READY}:
                self._enforce_deadline(active)
            trial = self._trial_events(
                active.participant.session_id, active.assigned.trial_id
            )
            event = self._build_event(
                active, parsed_type, event_index=len(trial), **fields
            )
            if event_uuid is not None:
                event = StudyEvent.from_dict(
                    {**event.to_dict(), "event_uuid": event_uuid}
                )
            self.event_store.append(event)
            if parsed_type in {EventType.CONFIRM, EventType.CANCEL}:
                self._cancel_decision_task(active)
            return event

    def record_ready(
        self, participant_id: str, trial_id: str
    ) -> StudyEvent | None:
        participant = self.participant(participant_id)
        assigned = next(
            (task for task in participant.task_sequence if task.trial_id == trial_id),
            None,
        )
        if assigned is None:
            raise StudyHubError("readiness trial is absent from assignment")
        key = (participant.session_id, trial_id)
        if key not in self._trial_starts:
            # A restart intentionally leaves readiness missing.
            return None
        trial = self._trial_events(*key)
        if any(event["event_type"] == "notebook_ready" for event in trial):
            return None
        confirm = next(
            (event for event in trial if event["event_type"] == "confirm"), None
        )
        if confirm is None:
            raise StudyHubError("notebook readiness arrived before confirmation")
        active = ActiveStudyTask(
            participant=participant,
            assigned=assigned,
            scenario=self._tasks[assigned.task_id]["scenario"],
        )
        if self._elapsed(active) - float(confirm["monotonic_seconds"]) > READINESS_TIMEOUT_SECONDS:
            self._readiness_missing.add(key)
            deadline_task = self._readiness_tasks.pop(key, None)
            if deadline_task is not None:
                deadline_task.cancel()
            return None
        event = self._build_event(
            active,
            EventType.NOTEBOOK_READY,
            event_index=len(trial),
            profile_id=confirm["profile_id"],
            image_id=confirm["image_id"],
        )
        self.event_store.append(event)
        task = self._readiness_tasks.pop(key, None)
        if task is not None:
            task.cancel()
        return event

    async def issue_preview(
        self,
        participant_id: str,
        trial_id: str,
        intent: object,
        *,
        recommendation_username: str | None = None,
    ) -> dict[str, Any]:
        active = self._active_for_trial(participant_id, trial_id)
        if active.assigned.condition is not Condition.P2:
            raise StudyHubError("B0 never invokes the recommendation backend")
        if not isinstance(intent, str) or not intent.strip():
            raise StudyHubError("workload intent must be non-blank")
        if len(intent) > 2000:
            raise StudyHubError("workload intent exceeds 2000 characters")
        token_username = recommendation_username or participant_id
        if not re.fullmatch(r"^[Pp]-[0-9a-f]{12}$", token_username):
            raise StudyHubError("recommendation username is not a study pseudonym")
        if "P-" + token_username[2:] != participant_id:
            raise StudyHubError(
                "recommendation username differs from the issued participant ID"
            )
        self.record(participant_id, trial_id, EventType.PREVIEW_REQUESTED)
        try:
            # The value is transient.  It is deliberately not copied into any
            # study record or operational log.
            response = await self.recommendation_runtime.issue(
                token_username, {"intent": intent}
            )
        except asyncio.TimeoutError:
            self.record(
                participant_id,
                trial_id,
                EventType.PREVIEW_RECEIVED,
                preview_status=PreviewStatus.TIMEOUT,
            )
            raise
        except Exception:
            self.record(
                participant_id,
                trial_id,
                EventType.PREVIEW_RECEIVED,
                preview_status=PreviewStatus.ERROR,
            )
            raise
        self.record(
            participant_id,
            trial_id,
            EventType.PREVIEW_RECEIVED,
            profile_id=response["applied_profile"],
            image_id=response["recommendation"]["image_id"],
            preview_status=PreviewStatus.SUCCESS,
        )
        return response

    def arm_decision_timeout(self, active: ActiveStudyTask) -> None:
        key = (active.participant.session_id, active.assigned.trial_id)
        if key in self._decision_tasks:
            return

        async def expire() -> None:
            try:
                remaining = max(0.0, DECISION_TIMEOUT_SECONDS - self._elapsed(active))
                await asyncio.sleep(remaining)
                with self._lock:
                    self._cancel_timeout_once(active)
            except asyncio.CancelledError:
                return
            except Exception:
                # The handler logger records operational detail.  Study logs
                # remain content-free and fail closed on subsequent action.
                return

        try:
            self._decision_tasks[key] = asyncio.create_task(expire())
        except RuntimeError:
            # Pure unit tests can call form helpers without a running loop.
            return

    def _cancel_decision_task(self, active: ActiveStudyTask) -> None:
        key = (active.participant.session_id, active.assigned.trial_id)
        task = self._decision_tasks.pop(key, None)
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if task is not None and task is not current:
            task.cancel()

    def schedule_readiness_deadline(
        self, spawner: object, participant_id: str, trial_id: str
    ) -> None:
        participant = self.participant(participant_id)
        key = (participant.session_id, trial_id)
        if key in self._readiness_tasks:
            return

        async def expire() -> None:
            try:
                await asyncio.sleep(READINESS_TIMEOUT_SECONDS)
                trial = self._trial_events(*key)
                if any(event["event_type"] == "notebook_ready" for event in trial):
                    return
                self._readiness_missing.add(key)
                stop = getattr(spawner, "stop", None)
                if callable(stop):
                    result = stop(now=True)
                    if hasattr(result, "__await__"):
                        await asyncio.wait_for(
                            result, timeout=CLEANUP_TIMEOUT_SECONDS
                        )
            except (asyncio.CancelledError, asyncio.TimeoutError):
                return
            except Exception:
                return

        try:
            self._readiness_tasks[key] = asyncio.create_task(expire())
        except RuntimeError:
            return

    def readiness_missing(self, participant_id: str, trial_id: str) -> bool:
        participant = self.participant(participant_id)
        return (participant.session_id, trial_id) in self._readiness_missing


def _apply_fixed_selection(
    spawner: object,
    *,
    profile_id: str,
    image_id: str,
    images: Mapping[str, Mapping[str, Any]],
) -> None:
    if profile_id not in PROFILE_RESOURCES:
        raise StudyHubError("profile selection is not allowlisted")
    if image_id not in images:
        raise StudyHubError("image selection is not allowlisted")
    resources = PROFILE_RESOURCES[profile_id]
    spawner.cpu_guarantee = resources["cpu_guarantee"]
    spawner.cpu_limit = resources["cpu_limit"]
    spawner.mem_guarantee = resources["mem_guarantee"]
    spawner.mem_limit = resources["mem_limit"]
    spawner.extra_resource_guarantees = {}
    spawner.extra_resource_limits = {}
    spawner.image = images[image_id]["reference"]


def apply_b0_selection(
    spawner: object,
    runtime: RecommendationPreviewRuntime,
    options: Mapping[str, Any],
) -> None:
    """Apply B0's validated manual selection without invoking P2."""

    if options.get("study_condition") != Condition.B0.value:
        raise StudyHubError("B0 spawn options contain the wrong condition")
    _apply_fixed_selection(
        spawner,
        profile_id=str(options.get("applied_profile", "")),
        image_id=str(options.get("applied_image_id", "")),
        images=runtime.images,
    )


def bind_study_spawn_annotations(
    spawner: object,
    options: Mapping[str, Any],
    *,
    participant_id: str,
    condition: str,
) -> dict[str, str]:
    """Bind one Pod to its content-free trial and final selection."""

    annotations = {
        "intent-spawner.openai.com/study-id": str(options["study_id"]),
        "intent-spawner.openai.com/assignment-id": str(
            options["study_assignment_id"]
        ),
        "intent-spawner.openai.com/session-id": str(options["study_session_id"]),
        "intent-spawner.openai.com/participant-id": participant_id,
        "intent-spawner.openai.com/trial-id": str(options["study_trial_id"]),
        "intent-spawner.openai.com/task-id": str(options["study_task_id"]),
        "intent-spawner.openai.com/pair-id": str(options["study_pair_id"]),
        "intent-spawner.openai.com/condition": condition,
        "intent-spawner.openai.com/final-profile-id": str(
            options["applied_profile"]
        ),
        "intent-spawner.openai.com/final-image-id": str(
            options["applied_image_id"]
        ),
    }
    existing = dict(getattr(spawner, "extra_annotations", {}) or {})
    existing.update(annotations)
    spawner.extra_annotations = existing
    return annotations


def _reference_panel(snapshot: Mapping[str, Any]) -> str:
    profile_rows = "".join(
        "<tr><th>{}</th><td>{}</td><td>{}</td></tr>".format(
            html.escape(item["display_name"]),
            html.escape(item["description"]),
            html.escape(json.dumps(item["resources"], sort_keys=True)),
        )
        for item in snapshot["profiles"]
    )
    image_rows = "".join(
        "<tr><th>{}</th><td>{}</td><td><code>{}</code></td></tr>".format(
            html.escape(item["display_name"]),
            html.escape(item["description"]),
            html.escape(item["reference"]),
        )
        for item in snapshot["images"]
    )
    return (
        '<section id="shared-reference"><h3>Available environments</h3>'
        '<p>This ordered reference is identical in B0 and P2.</p>'
        '<h4>Profiles</h4><table><tbody>'
        + profile_rows
        + '</tbody></table><h4>Images</h4><table><tbody>'
        + image_rows
        + "</tbody></table></section>"
    )


def _profile_options(snapshot: Mapping[str, Any]) -> str:
    return '<option value="">Select a profile</option>' + "".join(
        '<option value="{}">{} — {}</option>'.format(
            html.escape(item["profile_id"]),
            html.escape(item["display_name"]),
            html.escape(item["description"]),
        )
        for item in snapshot["profiles"]
    )


def _image_options(snapshot: Mapping[str, Any]) -> str:
    return '<option value="">Select an image</option>' + "".join(
        '<option value="{}">{} — {}</option>'.format(
            html.escape(item["image_id"]),
            html.escape(item["display_name"]),
            html.escape(item["description"]),
        )
        for item in snapshot["images"]
    )


def options_form(
    runtime: RecommendationPreviewRuntime,
    active: ActiveStudyTask,
    *,
    preview_endpoint: str,
    event_endpoint: str,
    advance_endpoint: str,
    consent_version: str,
) -> str:
    """Render a condition-specific decision UI plus one shared option panel."""

    snapshot = shared_option_snapshot(runtime)
    profiles = _profile_options(snapshot)
    images = _image_options(snapshot)
    condition = active.assigned.condition.value
    phase = "Warm-up (not scored)" if active.assigned.phase.value == "warm_up" else "Measured task"
    if active.assigned.condition is Condition.B0:
        condition_ui = f"""
        <section id="b0-controls">
          <p>Select a profile and image manually. No recommendation is made.</p>
          <label for="study_profile"><strong>Resource profile</strong></label>
          <select id="study_profile" name="study_profile" required>{profiles}</select>
          <label for="study_image_id"><strong>Notebook image</strong></label>
          <select id="study_image_id" name="study_image_id" required>{images}</select>
          <button id="study-confirm" type="button" class="btn btn-success">Confirm environment</button>
        </section>"""
    else:
        condition_ui = f"""
        <section id="p2-controls">
          <label for="intent"><strong>Describe what you plan to do</strong></label>
          <textarea id="intent" maxlength="2000" rows="4" style="width:100%"></textarea>
          <button id="preview-recommendation" type="button" class="btn btn-primary">Preview recommendation</button>
          <p id="preview-error" role="alert"></p>
          <section id="recommendation-preview" hidden>
            <h3>Recommendation preview</h3>
            <p><strong>Profile:</strong> <span id="preview-profile"></span></p>
            <p><strong>Image:</strong> <span id="preview-image"></span></p>
            <button id="confirm-recommendation" type="button" class="btn btn-success">Confirm recommendation</button>
            <button id="show-override" type="button" class="btn btn-warning">Edit / override</button>
          </section>
          <section id="override-panel" hidden>
            <label for="override_profile">Resource profile</label>
            <select id="override_profile" name="override_profile">{profiles}</select>
            <label for="override_image_id">Notebook image</label>
            <select id="override_image_id" name="override_image_id">{images}</select>
            <button id="submit-override" type="button" class="btn btn-warning">Confirm override</button>
          </section>
          <input id="decision_action" name="decision_action" type="hidden" value=""/>
          <input id="recommendation_preview_id" name="recommendation_preview_id" type="hidden" value=""/>
          <input name="preview_version" type="hidden" value="{html.escape(PREVIEW_VERSION)}"/>
        </section>"""
    template = r"""
    <div id="protocol-v5-study" style="max-width:900px;margin:0 auto;font-family:sans-serif">
      <h2>Environment-selection study</h2>
      <p><strong>__PHASE__</strong> · condition <strong>__CONDITION__</strong> · task __POSITION__ of 4 in this period</p>
      <section id="study-scenario"><h3>Scenario</h3><p>__SCENARIO__</p></section>
      <input name="study_trial_id" type="hidden" value="__TRIAL_ID__"/>
      <input name="study_consent_version" type="hidden" value="__CONSENT_VERSION__"/>
      __CONDITION_UI__
      <button id="study-cancel" type="button" class="btn btn-secondary">Cancel task</button>
      __REFERENCE_PANEL__
    </div>
    <script>
    (()=>{
      const root=document.getElementById("protocol-v5-study"),form=root.closest("form");
      const condition=__CONDITION_JSON__,trialId=__TRIAL_JSON__,previewEndpoint=__PREVIEW_ENDPOINT__,eventEndpoint=__EVENT_ENDPOINT__,advanceEndpoint=__ADVANCE_ENDPOINT__;
      const cookie=name=>document.cookie.split(";").map(v=>v.trim()).find(v=>v.startsWith(name+"="))?.split("=").slice(1).join("=")||"";
      const headers={"Content-Type":"application/json","X-XSRFToken":decodeURIComponent(cookie("_xsrf"))};
      let queue=Promise.resolve(),allowSubmit=false,latest=null,editSent=false;
      const event=(event_type,fields={})=>{const body={trial_id:trialId,event_uuid:crypto.randomUUID(),event_type,...fields};queue=queue.then(async()=>{const response=await fetch(eventEndpoint,{method:"POST",credentials:"same-origin",headers,body:JSON.stringify(body)});if(!response.ok){const payload=await response.json().catch(()=>({}));throw new Error(payload.error||"study instrumentation failed");}});return queue;};
      const changed=(element,eventType,oldField,newField)=>{element.addEventListener("change",()=>{const previous=element.dataset.studyPrevious||null,current=element.value||null;event(eventType,{[oldField]:previous,[newField]:current});element.dataset.studyPrevious=current||"";});};
      const submit=async()=>{try{await queue;allowSubmit=true;form.requestSubmit();}catch(error){window.alert(error.message);}};
      form.addEventListener("submit",e=>{if(!allowSubmit)e.preventDefault();});
      form.querySelectorAll('input[type="submit"],button[type="submit"]').forEach(button=>button.style.display="none");
      document.getElementById("study-cancel").addEventListener("click",async()=>{try{await event("cancel",{cancel_reason:"participant_cancelled"});window.location.assign(advanceEndpoint);}catch(error){window.alert(error.message);}});
      window.setTimeout(async()=>{try{await event("cancel",{cancel_reason:"decision_timeout"});window.location.assign(advanceEndpoint);}catch(_){window.location.reload();}},600000);
      if(condition==="B0"){
        changed(document.getElementById("study_profile"),"profile_changed","old_profile_id","new_profile_id");
        changed(document.getElementById("study_image_id"),"image_changed","old_image_id","new_image_id");
        document.getElementById("study-confirm").addEventListener("click",()=>submit());
      }else{
        const intent=document.getElementById("intent"),token=document.getElementById("recommendation_preview_id"),action=document.getElementById("decision_action"),panel=document.getElementById("recommendation-preview"),overridePanel=document.getElementById("override-panel"),profile=document.getElementById("override_profile"),image=document.getElementById("override_image_id"),error=document.getElementById("preview-error");
        intent.addEventListener("focus",()=>{editSent=false;event("intent_focus");});
        intent.addEventListener("input",()=>{if(!editSent){editSent=true;event("intent_edit");}token.value="";panel.hidden=true;overridePanel.hidden=true;latest=null;});
        intent.addEventListener("blur",()=>{editSent=false;});
        changed(profile,"profile_changed","old_profile_id","new_profile_id");changed(image,"image_changed","old_image_id","new_image_id");
        document.getElementById("preview-recommendation").addEventListener("click",async()=>{error.textContent="";try{await queue;const response=await fetch(previewEndpoint,{method:"POST",credentials:"same-origin",headers,body:JSON.stringify({trial_id:trialId,intent:intent.value})});const payload=await response.json();if(!response.ok)throw new Error(payload.error||"preview failed");latest={profile:payload.applied_profile,image:payload.recommendation.image_id};token.value=payload.recommendation_preview_id;profile.value=latest.profile;image.value=latest.image;profile.dataset.studyPrevious=latest.profile;image.dataset.studyPrevious=latest.image;document.getElementById("preview-profile").textContent=latest.profile;document.getElementById("preview-image").textContent=payload.image_display_name+" ("+latest.image+")";panel.hidden=false;}catch(reason){error.textContent=reason.message||"preview failed";}});
        document.getElementById("confirm-recommendation").addEventListener("click",()=>{if(!latest)return;action.value="accept";submit();});
        document.getElementById("show-override").addEventListener("click",()=>{overridePanel.hidden=false;});
        document.getElementById("submit-override").addEventListener("click",async()=>{if(!latest)return;const selected={profile:profile.value,image:image.value};if(!selected.profile||!selected.image){window.alert("Select both a profile and an image.");return;}if(selected.profile===latest.profile&&selected.image===latest.image){window.alert("This matches the recommendation; use Confirm recommendation.");return;}try{await event("override",{profile_id:selected.profile,image_id:selected.image});action.value="override";submit();}catch(reason){window.alert(reason.message);}});
      }
    })();
    </script>
    """
    replacements = {
        "__PHASE__": html.escape(phase),
        "__CONDITION__": html.escape(condition),
        "__POSITION__": str(active.assigned.position_in_period + 1),
        "__SCENARIO__": html.escape(active.scenario),
        "__TRIAL_ID__": html.escape(active.assigned.trial_id),
        "__CONSENT_VERSION__": html.escape(consent_version),
        "__CONDITION_UI__": condition_ui,
        "__REFERENCE_PANEL__": _reference_panel(snapshot),
        "__CONDITION_JSON__": safe_json_dumps(condition),
        "__TRIAL_JSON__": safe_json_dumps(active.assigned.trial_id),
        "__PREVIEW_ENDPOINT__": safe_json_dumps(preview_endpoint),
        "__EVENT_ENDPOINT__": safe_json_dumps(event_endpoint),
        "__ADVANCE_ENDPOINT__": safe_json_dumps(advance_endpoint),
    }
    for marker, replacement in replacements.items():
        template = template.replace(marker, replacement)
    return template


def _message_form(title: str, message: str, action_url: str, action: str) -> str:
    return f"""
    <div style="max-width:800px;margin:0 auto;font-family:sans-serif">
      <h2>{html.escape(title)}</h2><p>{html.escape(message)}</p>
      <a class="btn btn-primary" href="{html.escape(action_url)}">{html.escape(action)}</a>
    </div><script>(()=>{{const form=document.currentScript.closest('form');if(form)form.querySelectorAll('input[type="submit"],button[type="submit"]').forEach(button=>button.style.display="none");}})();</script>
    """


def _scale_radios(name: str, low: int, high: int) -> str:
    return " ".join(
        f"<label><input type='radio' name='{html.escape(name)}' value='{value}'/> {value}</label>"
        for value in range(low, high + 1)
    )


def questionnaire_form(spec: Mapping[str, Any], xsrf_token: str, response_uuid: str) -> str:
    """Render one closed-response form; blank answers and explicit skip are valid."""

    kind = QuestionnaireType(str(spec["questionnaire_type"]))
    if kind is QuestionnaireType.SEQ_TASK:
        title = "Task ease"
        fields = (
            "<fieldset><legend>Overall, how difficult or easy was this task?</legend>"
            + _scale_radios(SEQ_ITEM_ID, 1, 7)
            + "<p>1 = Very difficult; 7 = Very easy.</p></fieldset>"
        )
    elif kind is QuestionnaireType.POST_CONDITION:
        title = f"Post-condition questionnaire: {html.escape(str(spec['condition']))}"
        sus = "".join(
            "<fieldset><legend>{}. {}</legend>{}<p>1 = Strongly disagree; 5 = Strongly agree.</p></fieldset>".format(
                index,
                html.escape(statement),
                _scale_radios(item_id, 1, 5),
            )
            for index, (item_id, statement) in enumerate(
                zip(SUS_ITEM_IDS, SUS_ITEMS), start=1
            )
        )
        custom = "".join(
            "<fieldset><legend>{}</legend>{}<p>1 = Strongly disagree; 7 = Strongly agree.</p></fieldset>".format(
                html.escape(statement), _scale_radios(item_id, 1, 7)
            )
            for item_id, statement in CUSTOM_ITEMS.items()
        )
        fields = (
            "<h2>System Usability Scale (SUS)</h2>"
            "<p>For these standard SUS statements, ‘this system’ means the environment-selection method you just used.</p>"
            + sus
            + "<h2>CUSTOM Likert items (not SUS dimensions)</h2>"
            + custom
        )
    else:
        title = "Final preference"
        fields = (
            "<fieldset><legend>Overall, which method would you prefer to use to select a notebook environment?</legend>"
            "<label><input type='radio' name='final_preference' value='B0'/> B0</label> "
            "<label><input type='radio' name='final_preference' value='P2'/> P2</label> "
            "<label><input type='radio' name='final_preference' value='NO_PREFERENCE'/> No preference</label>"
            "</fieldset>"
        )
    return (
        "<!doctype html><html lang='en'><body style='max-width:900px;margin:2rem auto;font-family:sans-serif'>"
        f"<h1>{title}</h1><p>Every response is optional. No comments or free text are collected.</p>"
        f"<form method='post'><input type='hidden' name='_xsrf' value='{html.escape(xsrf_token)}'/>"
        f"<input type='hidden' name='questionnaire_id' value='{html.escape(str(spec['questionnaire_id']))}'/>"
        f"<input type='hidden' name='response_uuid' value='{html.escape(response_uuid)}'/>{fields}"
        "<button type='submit' name='action' value='submit'>Submit responses</button> "
        "<button type='submit' name='action' value='skip'>Skip all responses</button>"
        "</form></body></html>"
    )


def _study_options_from_form(
    study: StudySessionRuntime,
    spawner: object,
    formdata: Mapping[str, Any],
) -> dict[str, Any]:
    participant_id = _study_participant_id(spawner)
    active = study.current_task(participant_id)
    if active is None:
        raise StudyHubError("all assigned study tasks are complete")
    trial_id = _first(formdata, "study_trial_id")
    if trial_id != active.assigned.trial_id:
        raise StudyHubError("submitted task differs from the current assignment")
    if _first(formdata, "study_consent_version") != study.manifest.consent_version:
        raise StudyHubError("consent version changed; reload the study page")
    if active.assigned.condition is Condition.B0:
        profile_id = _first(formdata, "study_profile")
        image_id = _first(formdata, "study_image_id")
        if profile_id not in PROFILE_RESOURCES or image_id not in study.recommendation_runtime.images:
            raise StudyHubError("manual selection is incomplete or not allowlisted")
        study.record(
            participant_id,
            trial_id,
            EventType.CONFIRM,
            profile_id=profile_id,
            image_id=image_id,
        )
        options: dict[str, Any] = {
            "study_condition": Condition.B0.value,
            "applied_profile": profile_id,
            "applied_image_id": image_id,
        }
    else:
        options = study.recommendation_runtime.options_from_form(spawner, formdata)
        profile_id = str(options["applied_profile"])
        image_id = str(options["applied_image_id"])
        study.record(
            participant_id,
            trial_id,
            EventType.CONFIRM,
            profile_id=profile_id,
            image_id=image_id,
        )
        options["study_condition"] = Condition.P2.value
    options.update(
        {
            "study_id": study.manifest.study_id,
            "study_assignment_id": study.manifest.assignment_id,
            "study_session_id": active.participant.session_id,
            "study_participant_id": participant_id,
            "study_trial_id": trial_id,
            "study_task_id": active.assigned.task_id,
            "study_pair_id": active.assigned.pair_id,
            "study_consent_version": study.manifest.consent_version,
            "study_consent_acknowledged": True,
        }
    )
    return options


def install_user_study(
    c: Any,
    recommendation_runtime: RecommendationPreviewRuntime,
    *,
    environ: Mapping[str, str] | None = None,
    event_store: AppendOnlyEventStore | None = None,
    gate_store: StudyGateStore | None = None,
) -> dict[str, Any]:
    """Install the study-only handlers and replace the normal spawn form.

    JupyterHub and Tornado imports are intentionally local so schema, form, and
    B0 tests require neither package.  The adapter is inert unless the explicit
    study enable flag is present.
    """

    selected = os.environ if environ is None else environ
    if selected.get("INTENT_SPAWNER_USER_STUDY_ENABLED", "").lower() != "true":
        raise StudyHubError("study adapter requires explicit opt-in enablement")
    validate_study_adapter_package(
        Path(__file__).parent,
        expected_checksum=selected.get(STUDY_HUB_PACKAGE_CHECKSUM_ENV, ""),
        expected_version=selected.get(STUDY_HUB_PACKAGE_VERSION_ENV, ""),
    )
    assignment_path = selected.get("INTENT_SPAWNER_USER_STUDY_ASSIGNMENT_PATH", "")
    browser_path = selected.get("INTENT_SPAWNER_USER_STUDY_BROWSER_TASKS_PATH", "")
    evidence_root = Path(
        selected.get("INTENT_SPAWNER_USER_STUDY_EVIDENCE_ROOT", "/srv/user-study")
    )
    if not assignment_path or not browser_path:
        raise StudyHubError("study assignment and browser task paths are required")
    manifest = load_assignment_manifest(assignment_path)
    browser_tasks = load_browser_task_set(browser_path)
    if selected.get(STUDY_ASSIGNMENT_CHECKSUM_ENV, "") != manifest.checksum:
        raise StudyHubError("live assignment checksum differs from rollout identity")
    if selected.get(STUDY_CONFIG_IDENTITY_ENV, "") != manifest.config_identity:
        raise StudyHubError("live configuration identity differs from assignment")
    if selected.get(STUDY_ENVIRONMENT_ID_ENV, "") != str(
        manifest.environment_identity["environment_id"]
    ):
        raise StudyHubError("live environment identity differs from assignment")
    store = event_store or AppendOnlyEventStore(
        evidence_root / "events.jsonl", evidence_root / "completion-markers"
    )
    gates = gate_store or StudyGateStore(evidence_root)
    study = StudySessionRuntime(
        assignment_manifest=manifest,
        browser_task_set=browser_tasks,
        recommendation_runtime=recommendation_runtime,
        event_store=store,
        gate_store=gates,
    )

    from jupyterhub.handlers.base import BaseHandler
    from tornado import web

    base_url = str(getattr(c.JupyterHub, "base_url", "/") or "/")
    if not base_url.startswith("/"):
        base_url = "/" + base_url
    if not base_url.endswith("/"):
        base_url += "/"
    preview_endpoint = f"{base_url}hub/study/preview"
    event_endpoint = f"{base_url}hub/study/event"
    consent_endpoint = f"{base_url}hub/study/consent"
    transition_endpoint = f"{base_url}hub/study/transition"
    questionnaire_endpoint = f"{base_url}hub/study/questionnaire"
    advance_endpoint = f"{base_url}hub/study/advance"
    spawn_endpoint = f"{base_url}hub/spawn"
    c.JupyterHub.template_paths = [str(Path(__file__).parent)]
    c.JupyterHub.template_vars = {"study_advance_url": advance_endpoint}

    class StudyConsentHandler(BaseHandler):
        @web.authenticated
        async def get(self) -> None:
            participant_id = _study_participant_id(self)
            if study.has_consent(participant_id):
                self.redirect(spawn_endpoint)
                return
            token = self.xsrf_token
            if isinstance(token, bytes):
                token = token.decode("ascii")
            self.finish(
                "<!doctype html><html lang='en'><body style='max-width:800px;margin:2rem auto;font-family:sans-serif'>"
                "<h1>Study consent acknowledgement</h1>"
                f"<p>Consent document version: <strong>{html.escape(manifest.consent_version)}</strong>.</p>"
                "<p>This gate records only your issued pseudonym, the version, acknowledgement, and time. "
                "It is bookkeeping and is not legal or IRB approval; institutional requirements remain the researcher's responsibility.</p>"
                f"<form method='post'><input type='hidden' name='_xsrf' value='{html.escape(str(token))}'/>"
                "<label><input type='checkbox' name='acknowledged' value='yes' required/> I acknowledge the stated consent version.</label>"
                "<button type='submit'>Continue</button></form></body></html>"
            )

        @web.authenticated
        async def post(self) -> None:
            participant_id = _study_participant_id(self)
            if self.get_body_argument("acknowledged", default="") != "yes":
                raise web.HTTPError(400, "consent acknowledgement is required")
            study.acknowledge_consent(participant_id)
            self.redirect(spawn_endpoint)

    class StudyTransitionHandler(BaseHandler):
        @web.authenticated
        async def get(self) -> None:
            participant_id = _study_participant_id(self)
            active = study.current_task(participant_id)
            if active is None or not study.transition_required(active):
                self.redirect(spawn_endpoint)
                return
            token = self.xsrf_token
            if isinstance(token, bytes):
                token = token.decode("ascii")
            self.finish(
                "<!doctype html><html lang='en'><body style='max-width:800px;margin:2rem auto;font-family:sans-serif'>"
                "<h1>Condition transition</h1><p>Take the standardized break now. "
                "The next period begins with an unscored warm-up.</p>"
                f"<form method='post'><input type='hidden' name='_xsrf' value='{html.escape(str(token))}'/>"
                "<button type='submit'>Begin next period</button></form></body></html>"
            )

        @web.authenticated
        async def post(self) -> None:
            participant_id = _study_participant_id(self)
            active = study.current_task(participant_id)
            if active is None or active.assigned.sequence_index != 4:
                raise web.HTTPError(409, "transition is not currently due")
            study.acknowledge_transition(participant_id)
            self.redirect(spawn_endpoint)

    class StudyEventHandler(BaseHandler):
        @web.authenticated
        async def post(self) -> None:
            try:
                payload = json.loads(self.request.body.decode("utf-8"))
                allowed = {
                    "trial_id",
                    "event_uuid",
                    "event_type",
                    "profile_id",
                    "image_id",
                    "old_profile_id",
                    "new_profile_id",
                    "old_image_id",
                    "new_image_id",
                    "cancel_reason",
                }
                if not isinstance(payload, Mapping) or set(payload) - allowed:
                    raise StudyHubError("event request contains unsupported fields")
                # Browser UUID is parsed for retry identity, but StudySessionRuntime
                # retains server ownership of all participant/task identity.
                browser_uuid = payload.pop("event_uuid", None)
                if not isinstance(browser_uuid, str):
                    raise StudyHubError("event_uuid is required")
                parsed_uuid = uuid.UUID(browser_uuid)
                if str(parsed_uuid) != browser_uuid.lower():
                    raise StudyHubError("event_uuid must use canonical notation")
                event_type = EventType(payload.pop("event_type"))
                if event_type not in {
                    EventType.INTENT_FOCUS,
                    EventType.INTENT_EDIT,
                    EventType.PROFILE_CHANGED,
                    EventType.IMAGE_CHANGED,
                    EventType.OVERRIDE,
                    EventType.CANCEL,
                }:
                    raise StudyHubError("event type is server-owned")
                trial_id = str(payload.pop("trial_id"))
                if "cancel_reason" in payload:
                    payload["cancel_reason"] = CancelReason(payload["cancel_reason"])
                    if payload["cancel_reason"] not in {
                        CancelReason.PARTICIPANT_CANCELLED,
                        CancelReason.DECISION_TIMEOUT,
                    }:
                        raise StudyHubError("client cancel reason is unsupported")
                event = study.record(
                    _study_participant_id(self),
                    trial_id,
                    event_type,
                    event_uuid=str(parsed_uuid),
                    **payload,
                )
                response = {"accepted": True, "event_uuid": event.event_uuid}
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                self.set_status(400)
                response = {"error": str(exc)}
            self.set_header("Content-Type", "application/json")
            self.finish(json.dumps(response))

    class StudyPreviewHandler(BaseHandler):
        @web.authenticated
        async def post(self) -> None:
            try:
                payload = json.loads(self.request.body.decode("utf-8"))
                if not isinstance(payload, Mapping) or set(payload) != {"trial_id", "intent"}:
                    raise StudyHubError("preview request fields are invalid")
                response = await study.issue_preview(
                    _study_participant_id(self),
                    str(payload["trial_id"]),
                    payload["intent"],
                    recommendation_username=(
                        self.current_user
                        if isinstance(self.current_user, str)
                        else str(getattr(self.current_user, "name", ""))
                    ),
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                self.set_status(400)
                response = {"error": str(exc)}
            except Exception:
                self.log.exception("study recommendation preview failed")
                self.set_status(503)
                response = {"error": "recommendation backend unavailable"}
            self.set_header("Content-Type", "application/json")
            self.finish(json.dumps(response))

    class StudyQuestionnaireHandler(BaseHandler):
        @web.authenticated
        async def get(self) -> None:
            participant_id = _study_participant_id(self)
            pending = study.pending_questionnaire(participant_id)
            if pending is None:
                self.redirect(advance_endpoint)
                return
            token = self.xsrf_token
            if isinstance(token, bytes):
                token = token.decode("ascii")
            self.finish(
                questionnaire_form(pending, str(token), str(uuid.uuid4()))
            )

        @web.authenticated
        async def post(self) -> None:
            participant_id = _study_participant_id(self)
            pending = study.pending_questionnaire(participant_id)
            if pending is None:
                raise web.HTTPError(409, "no questionnaire is currently due")
            questionnaire_id = self.get_body_argument("questionnaire_id", default="")
            if questionnaire_id != pending["questionnaire_id"]:
                raise web.HTTPError(409, "submitted questionnaire is stale")
            action = self.get_body_argument("action", default="")
            if action not in {"submit", "skip"}:
                raise web.HTTPError(400, "questionnaire action is invalid")
            kind = QuestionnaireType(str(pending["questionnaire_type"]))
            if kind is QuestionnaireType.SEQ_TASK:
                keys = (SEQ_ITEM_ID,)
            elif kind is QuestionnaireType.POST_CONDITION:
                keys = (*SUS_ITEM_IDS, *CUSTOM_ITEM_IDS)
            else:
                keys = (FINAL_PREFERENCE_ID,)
            responses: dict[str, Any] = {}
            for key in keys:
                raw = None if action == "skip" else self.get_body_argument(key, default="")
                if raw in {None, ""}:
                    responses[key] = None
                elif key == FINAL_PREFERENCE_ID:
                    responses[key] = raw
                else:
                    try:
                        responses[key] = int(str(raw))
                    except ValueError as exc:
                        raise web.HTTPError(400, f"{key} is not an integer") from exc
            try:
                study.record_questionnaire(
                    participant_id,
                    questionnaire_id,
                    self.get_body_argument("response_uuid", default=""),
                    responses,
                )
            except (QuestionnaireValidationError, StudyHubError, ValueError) as exc:
                raise web.HTTPError(400, str(exc)) from exc
            if kind is QuestionnaireType.POST_CONDITION and pending["period"] == 1:
                self.redirect(transition_endpoint)
            else:
                self.redirect(advance_endpoint)

    class StudyAdvanceHandler(BaseHandler):
        @web.authenticated
        async def get(self) -> None:
            participant_id = _study_participant_id(self)
            user = self.current_user
            user_spawner = getattr(user, "spawner", None)
            if bool(getattr(user, "running", False)) or bool(
                getattr(user_spawner, "pending", False)
            ):
                try:
                    future = await self.stop_single_user(user)
                    if future is not None:
                        await asyncio.wait_for(
                            future, timeout=CLEANUP_TIMEOUT_SECONDS
                        )
                except asyncio.TimeoutError:
                    self.log.warning("bounded study server cleanup timed out")
            if study.pending_questionnaire(participant_id) is not None:
                self.redirect(questionnaire_endpoint)
                return
            if study.current_task(participant_id) is None:
                participant = study.participant(participant_id)
                if not study.questionnaire_complete(participant_id):
                    raise StudyHubError("session questionnaire schedule is incomplete")
                if not store.is_session_complete(participant.session_id):
                    store.complete_session(
                        participant.session_id,
                        expected_trial_ids={
                            task.trial_id for task in participant.task_sequence
                        },
                    )
                completion = store.read_completion_marker(participant.session_id)
                if completion is None:
                    raise StudyHubError("completed session marker is unavailable")
                gates.record_completed_session(
                    manifest,
                    participant,
                    completed_at_utc=str(completion["completed_at_utc"]),
                )
                self.finish(
                    "<!doctype html><html lang='en'><body><h1>Study session complete</h1>"
                    "<p>Your pseudonymous session evidence has been sealed.</p></body></html>"
                )
                return
            self.redirect(spawn_endpoint)

    c.JupyterHub.extra_handlers.extend(
        [
            (r"/study/consent", StudyConsentHandler),
            (r"/study/transition", StudyTransitionHandler),
            (r"/study/event", StudyEventHandler),
            (r"/study/preview", StudyPreviewHandler),
            (r"/study/questionnaire", StudyQuestionnaireHandler),
            (r"/study/advance", StudyAdvanceHandler),
        ]
    )

    async def context_options_form(spawner: object) -> str:
        participant_id = _study_participant_id(spawner)
        if not study.has_consent(participant_id):
            return _message_form(
                "Consent acknowledgement required",
                f"Acknowledge consent version {manifest.consent_version} before the first task.",
                consent_endpoint,
                "Review acknowledgement",
            )
        try:
            pending = study.pending_questionnaire(participant_id)
            if pending is not None:
                return _message_form(
                    "Questionnaire due",
                    "Submit or explicitly skip the scheduled closed-response form before continuing.",
                    questionnaire_endpoint,
                    "Open questionnaire",
                )
            active = study.current_task(participant_id)
        except StudySessionIncompleteError as exc:
            return _message_form(
                "Session incomplete", str(exc), consent_endpoint, "Return"
            )
        if active is None:
            return _message_form(
                "Study session complete",
                "All assigned tasks are terminal.",
                advance_endpoint,
                "Seal session",
            )
        if study.transition_required(active):
            return _message_form(
                "Condition transition",
                "Take the standardized break before beginning the next warm-up.",
                transition_endpoint,
                "Open transition",
            )
        study.ensure_task_shown(active)
        study.arm_decision_timeout(active)
        return options_form(
            recommendation_runtime,
            active,
            preview_endpoint=preview_endpoint,
            event_endpoint=event_endpoint,
            advance_endpoint=advance_endpoint,
            consent_version=manifest.consent_version,
        )

    def context_options_from_form(
        formdata: Mapping[str, Any], *, spawner: object
    ) -> dict[str, Any]:
        return _study_options_from_form(study, spawner, formdata)

    async def context_pre_spawn_hook(spawner: object) -> None:
        options = dict(getattr(spawner, "user_options", {}) or {})
        participant_id = _study_participant_id(spawner)
        if options.get("study_participant_id") != participant_id:
            raise StudyHubError("spawn options differ from the pseudonymous assignment")
        if options.get("study_assignment_id") != manifest.assignment_id:
            raise StudyHubError("spawn options use a stale assignment")
        if options.get("study_consent_version") != manifest.consent_version:
            raise StudyHubError("spawn options use a stale consent version")
        condition = options.get("study_condition")
        if condition == Condition.B0.value:
            apply_b0_selection(spawner, recommendation_runtime, options)
        elif condition == Condition.P2.value:
            await recommendation_runtime.pre_spawn(spawner)
        else:
            raise StudyHubError("spawn options contain an unsupported condition")
        # Bind the actual Kubernetes Pod to the immutable trial and final
        # selection using content-free, allowlisted identifiers. These
        # operational annotations are identical for both conditions and let a
        # deployment smoke prove pod/readiness correlation without copying
        # intent, task text, Hub usernames, or other identity data.
        bind_study_spawn_annotations(
            spawner,
            options,
            participant_id=participant_id,
            condition=str(condition),
        )
        study.schedule_readiness_deadline(
            spawner, participant_id, str(options["study_trial_id"])
        )

    async def progress_ready_hook(
        spawner: object, ready_event: Mapping[str, Any]
    ) -> dict[str, Any]:
        event = dict(ready_event)
        options = dict(getattr(spawner, "user_options", {}) or {})
        participant_id = str(options.get("study_participant_id", ""))
        trial_id = str(options.get("study_trial_id", ""))
        if participant_id and trial_id:
            study.record_ready(participant_id, trial_id)
            event["url"] = advance_endpoint
            event["message"] = "Notebook ready; recording readiness and advancing the study."
        return event

    c.KubeSpawner.options_form = context_options_form
    c.KubeSpawner.options_from_form = context_options_from_form
    c.KubeSpawner.pre_spawn_hook = context_pre_spawn_hook
    c.Spawner.progress_ready_hook = progress_ready_hook
    c.Spawner.start_timeout = int(READINESS_TIMEOUT_SECONDS)

    return {
        "USER_STUDY_RUNTIME": study,
        "USER_STUDY_EVENT_STORE": store,
        "USER_STUDY_GATE_STORE": gates,
        "USER_STUDY_ASSIGNMENT": manifest,
        "USER_STUDY_BROWSER_TASKS": browser_tasks,
        "USER_STUDY_SHARED_OPTIONS": shared_option_snapshot(recommendation_runtime),
        "USER_STUDY_SHARED_OPTIONS_SHA256": shared_option_snapshot_sha256(
            recommendation_runtime
        ),
        "StudyConsentHandler": StudyConsentHandler,
        "StudyTransitionHandler": StudyTransitionHandler,
        "StudyEventHandler": StudyEventHandler,
        "StudyPreviewHandler": StudyPreviewHandler,
        "StudyQuestionnaireHandler": StudyQuestionnaireHandler,
        "StudyAdvanceHandler": StudyAdvanceHandler,
        "context_options_form": context_options_form,
        "context_options_from_form": context_options_from_form,
        "context_pre_spawn_hook": context_pre_spawn_hook,
        "progress_ready_hook": progress_ready_hook,
    }


__all__ = [
    "CLEANUP_TIMEOUT_SECONDS",
    "CONSENT_ACK_SCHEMA_VERSION",
    "DECISION_TIMEOUT_SECONDS",
    "INCOMPLETE_SCHEMA_VERSION",
    "READINESS_TIMEOUT_SECONDS",
    "STUDY_HUB_MAX_PACKAGE_BYTES",
    "STUDY_HUB_PACKAGE_CHECKSUM_ENV",
    "STUDY_HUB_PACKAGE_VERSION_ENV",
    "STUDY_ASSIGNMENT_CHECKSUM_ENV",
    "STUDY_CONFIG_IDENTITY_ENV",
    "STUDY_ENVIRONMENT_ID_ENV",
    "STUDY_HUB_RUNTIME_FILES",
    "STUDY_HUB_ADAPTER_VERSION",
    "TRANSITION_SCHEMA_VERSION",
    "ActiveStudyTask",
    "DecisionDeadlineExpired",
    "StudyGateStore",
    "StudyHubError",
    "StudySessionIncompleteError",
    "StudySessionRuntime",
    "apply_b0_selection",
    "bind_study_spawn_annotations",
    "compute_study_adapter_checksum",
    "install_user_study",
    "load_browser_task_set",
    "options_form",
    "shared_option_snapshot",
    "shared_option_snapshot_sha256",
    "validate_browser_task_set",
    "validate_study_adapter_package",
]
