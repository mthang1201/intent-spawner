"""CLI and immutable evidence finalization for Protocol-v5 E3.

The command surface prepares and validates a study, but never recruits a
participant or fabricates an observation.  ``finalize`` copies validated raw
inputs into a new, exclusive result directory before publishing descriptive
derived outcomes and provenance.  Development task bundles are restricted to
``DRY_RUN`` or ``NOT_EXECUTED`` status.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import Any
import uuid

from evaluation_v5.paths import DEFAULT_RESULTS_ROOT, PROTOCOL_DIRECTORY
from evaluation_v5.provenance import write_json_exclusive

from .assignment import (
    ASSIGNMENT_GENERATOR_VERSION,
    PARTICIPANT_TARGET,
    AssignmentManifest,
    generate_assignment_manifest,
    load_assignment_manifest,
    validate_assignment_manifest,
)
from .metrics import (
    MATCHED_PAIR_OUTCOME_SCHEMA_VERSION,
    SUMMARY_SCHEMA_VERSION,
    TASK_OUTCOME_SCHEMA_VERSION,
    derive_study_metrics,
)
from .fairness import (
    FAIRNESS_MANIFEST_SCHEMA_VERSION,
    build_fairness_manifest,
    validate_study_environment_identity,
    verify_fairness_manifest,
)
from .scoring import FINAL_SELECTION_SCORING_VERSION
from .questionnaires import (
    ANALYSIS_PLAN_SHA256,
    ANALYSIS_PLAN_VERSION,
    QUESTIONNAIRE_INSTRUMENT_SHA256,
    QUESTIONNAIRE_INSTRUMENT_VERSION,
    QUESTIONNAIRE_OUTCOME_SCHEMA_VERSION,
    QUESTIONNAIRE_SCHEMA_VERSION,
    QUESTIONNAIRE_SCHEMA_SHA256,
    QuestionnaireRecord,
    QuestionnaireValidationError,
    derive_questionnaire_outcomes,
    expected_questionnaire_ids,
    validate_questionnaire_stream,
)
from .analysis import (
    ANALYSIS_SCHEMA_VERSION,
    UserStudyAnalysisError,
    analyze_user_study,
    analysis_dependency_versions,
    validate_analysis_dependencies,
    write_analysis_artifacts,
)
from .schemas import (
    ASSIGNMENT_SCHEMA_VERSION,
    BROWSER_TASK_SET_SCHEMA_VERSION,
    EVENT_SCHEMA_VERSION,
    TASK_SET_SCHEMA_VERSION,
    CancelReason,
    EventType,
    ReviewStatus,
    StudyEvent,
    TaskPhase,
    TaskSet,
    TaskSetStage,
    TaskSetStatus,
    UserStudyValidationError,
    browser_safe_task_set,
    canonical_json_sha256,
    load_task_set,
    validate_event,
    validate_event_stream,
    validate_task_set,
)


PROTOCOL_VERSION = "5.0.0"
EXPERIMENT_ID = "E3"
FINALIZER_VERSION = "protocol-v5-user-study-finalizer-v1.1.0"
PROVENANCE_SCHEMA_VERSION = "protocol-v5-user-study-provenance-v1.0.0"
STATUS_SCHEMA_VERSION = "protocol-v5-user-study-status-v1.0.0"
SESSION_SCHEMA_VERSION = "protocol-v5-user-study-session-v1.0.0"
EXCLUSION_SCHEMA_VERSION = "protocol-v5-user-study-exclusion-v1.0.0"
EXCLUSION_REASON_VERSION = "protocol-v5-user-study-exclusion-reasons-v1.0.0"

EXECUTION_STATUSES = frozenset(
    {"NOT_EXECUTED", "DRY_RUN", "INCOMPLETE", "OBSERVED"}
)
SESSION_STATUSES = frozenset({"complete", "incomplete", "withdrawn", "excluded"})
EXCLUSION_REASONS = frozenset(
    {
        "consent_withdrawal",
        "duplicate_or_invalid_assignment",
        "checksum_or_protocol_drift",
        "instrumentation_corruption",
    }
)

_SESSION_FIELDS = frozenset(
    {
        "schema_version",
        "study_id",
        "assignment_id",
        "session_id",
        "participant_id",
        "consent_version",
        "consent_acknowledged",
        "session_status",
        "started_at_utc",
        "ended_at_utc",
    }
)
_EXCLUSION_FIELDS = frozenset(
    {
        "schema_version",
        "reason_version",
        "study_id",
        "assignment_id",
        "session_id",
        "participant_id",
        "reason_code",
        "recorded_at_utc",
    }
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PSEUDONYM = re.compile(r"^P-[0-9a-f]{12}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class UserStudyRunnerError(ValueError):
    """A CLI input or evidence-finalization precondition failed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise UserStudyRunnerError(message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _validate_utc(value: object, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    _require(isinstance(value, str) and value.endswith("Z"), f"{label} must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise UserStudyRunnerError(f"{label} must be ISO-8601 UTC") from exc
    _require(parsed.utcoffset() == timezone.utc.utcoffset(parsed), f"{label} must be UTC")
    return value


def _exact(value: object, fields: frozenset[str], label: str) -> dict[str, Any]:
    _require(isinstance(value, Mapping), f"{label} must be an object")
    payload = dict(value)
    missing = sorted(fields - set(payload))
    extra = sorted(set(payload) - fields)
    _require(not missing, f"{label} missing fields: {', '.join(missing)}")
    _require(not extra, f"{label} unexpected fields: {', '.join(extra)}")
    return payload


def _safe_id(value: object, label: str) -> str:
    _require(isinstance(value, str) and bool(_SAFE_ID.fullmatch(value)), f"{label} must be a safe identifier")
    return value


def _pseudonym(value: object, label: str) -> str:
    _require(isinstance(value, str) and bool(_PSEUDONYM.fullmatch(value)), f"{label} must be P-<12 hex>")
    return value


def _canonical_bytes(value: object, *, pretty: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    revision = result.stdout.strip().lower()
    return revision if re.fullmatch(r"[0-9a-f]{40}", revision) else "unknown"


def _load_json_object(path: Path | None, label: str) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UserStudyRunnerError(f"{label} is not valid JSON") from exc
    _require(isinstance(value, Mapping), f"{label} must be a JSON object")
    try:
        json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise UserStudyRunnerError(f"{label} must be finite JSON") from exc
    return dict(value)


def _load_jsonl(path: Path | None, label: str) -> tuple[list[dict[str, Any]], bytes]:
    if path is None:
        return [], b""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UserStudyRunnerError(f"cannot read {label}") from exc
    if raw and not raw.endswith(b"\n"):
        raise UserStudyRunnerError(f"{label} must end with a newline")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UserStudyRunnerError(f"{label} must be UTF-8") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        _require(bool(line), f"{label}:{line_number} cannot be blank")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise UserStudyRunnerError(
                f"{label}:{line_number} is invalid JSON"
            ) from exc
        _require(isinstance(value, Mapping), f"{label}:{line_number} must be an object")
        records.append(dict(value))
    return records, raw


def _authoritative_task_set(
    task_set: TaskSet,
    *,
    catalog_path: Path | None,
    confirmatory: bool,
) -> tuple[TaskSet, set[str], set[str], dict[str, Any], dict[str, Any], Any]:
    """Validate task gold against the frozen administrator catalogs."""

    from recommender.candidate_corpus import build_candidate_corpus
    from recommender.rule_based import load_image_catalog

    catalog = load_image_catalog(catalog_path) if catalog_path is not None else load_image_catalog()
    corpus = build_candidate_corpus(image_catalog=catalog)
    validated = validate_task_set(
        task_set,
        catalog=catalog,
        corpus=corpus,
        confirmatory=confirmatory,
        require_protocol_design=True,
    )
    profiles = {candidate.profile_id for candidate in corpus.candidates}
    images = {candidate.image_id for candidate in corpus.candidates}
    identity = {
        "catalog_version": catalog["catalog_version"],
        "catalog_sha256": canonical_json_sha256(catalog),
        "corpus_version": corpus.corpus_version,
        "corpus_sha256": corpus.corpus_checksum,
        "policy_version": corpus.policy_version,
        "catalog_source_role": (
            "explicit_catalog" if catalog_path is not None else "repository_default"
        ),
    }
    return validated, profiles, images, identity, catalog, corpus


def _validate_sessions(
    records: Sequence[Mapping[str, Any]],
    manifest: AssignmentManifest,
) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(records):
        row = _exact(raw, _SESSION_FIELDS, f"session[{index}]")
        _require(row["schema_version"] == SESSION_SCHEMA_VERSION, "unsupported session schema")
        _require(row["study_id"] == manifest.study_id, "session study_id drift")
        _require(row["assignment_id"] == manifest.assignment_id, "session assignment_id drift")
        _safe_id(row["session_id"], "session.session_id")
        _pseudonym(row["participant_id"], "session.participant_id")
        _require(row["consent_version"] == manifest.consent_version, "session consent_version drift")
        _require(isinstance(row["consent_acknowledged"], bool), "session consent_acknowledged must be boolean")
        _require(row["session_status"] in SESSION_STATUSES, "session_status is unsupported")
        started = _validate_utc(row["started_at_utc"], "session.started_at_utc")
        ended = _validate_utc(row["ended_at_utc"], "session.ended_at_utc", nullable=True)
        if ended is not None:
            started_time = datetime.fromisoformat(str(started)[:-1] + "+00:00")
            ended_time = datetime.fromisoformat(ended[:-1] + "+00:00")
            _require(ended_time >= started_time, "session ended before it started")
        _require(row["session_id"] not in seen, "duplicate session record")
        seen.add(row["session_id"])
        sessions.append(row)
    return sessions


def _validate_exclusions(
    records: Sequence[Mapping[str, Any]],
    manifest: AssignmentManifest,
) -> list[dict[str, Any]]:
    exclusions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(records):
        row = _exact(raw, _EXCLUSION_FIELDS, f"exclusion[{index}]")
        _require(row["schema_version"] == EXCLUSION_SCHEMA_VERSION, "unsupported exclusion schema")
        _require(row["reason_version"] == EXCLUSION_REASON_VERSION, "unsupported exclusion reason version")
        _require(row["study_id"] == manifest.study_id, "exclusion study_id drift")
        _require(row["assignment_id"] == manifest.assignment_id, "exclusion assignment_id drift")
        _safe_id(row["session_id"], "exclusion.session_id")
        _pseudonym(row["participant_id"], "exclusion.participant_id")
        _require(row["reason_code"] in EXCLUSION_REASONS, "exclusion reason is not predeclared")
        _validate_utc(row["recorded_at_utc"], "exclusion.recorded_at_utc")
        _require(row["session_id"] not in seen, "a session has duplicate exclusion records")
        seen.add(row["session_id"])
        exclusions.append(row)
    return exclusions


def _assignment_index(
    manifest: AssignmentManifest,
) -> tuple[dict[str, tuple[str, str]], dict[tuple[str, str], str]]:
    participants: dict[str, tuple[str, str]] = {}
    trials: dict[tuple[str, str], str] = {}
    for assignment in manifest.assignments:
        participants[assignment.session_id] = (
            assignment.participant_id,
            assignment.counterbalance_cell,
        )
        for task in assignment.task_sequence:
            trials[(assignment.session_id, task.trial_id)] = task.task_id
    return participants, trials


def _validate_events_against_assignment(
    events: Sequence[StudyEvent], manifest: AssignmentManifest
) -> None:
    assignments = {
        assignment.participant_id: assignment for assignment in manifest.assignments
    }
    for event in events:
        _require(event.study_id == manifest.study_id, "event study_id drift")
        _require(event.assignment_id == manifest.assignment_id, "event assignment_id drift")
        _require(event.consent_version == manifest.consent_version, "event consent_version drift")
        participant = assignments.get(event.participant_id)
        _require(participant is not None, "event participant is absent from assignment")
        _require(event.session_id == participant.session_id, "event session differs from assignment")
        assigned = next(
            (task for task in participant.task_sequence if task.trial_id == event.trial_id),
            None,
        )
        _require(assigned is not None, "event trial is absent from assignment")
        _require(
            (
                event.task_id,
                event.pair_id,
                event.condition,
            )
            == (
                assigned.task_id,
                assigned.pair_id,
                assigned.condition,
            ),
            "event task identity differs from assignment",
        )


def _validate_session_bindings(
    sessions: Sequence[Mapping[str, Any]],
    exclusions: Sequence[Mapping[str, Any]],
    events: Sequence[StudyEvent],
    manifest: AssignmentManifest,
    *,
    require_session_records: bool,
) -> tuple[set[str], set[str]]:
    excluded = {str(row["session_id"]) for row in exclusions}
    participant_index, trial_index = _assignment_index(manifest)
    by_session = {str(row["session_id"]): row for row in sessions}
    for exclusion in exclusions:
        session = by_session.get(str(exclusion["session_id"]))
        if session is not None:
            _require(
                session["participant_id"] == exclusion["participant_id"],
                "session/exclusion participant mismatch",
            )
            _require(
                session["session_status"] in {"withdrawn", "excluded"},
                "excluded session must have withdrawn or excluded status",
            )
    for session_id, session in by_session.items():
        if session["session_status"] in {"withdrawn", "excluded"}:
            _require(
                session_id in excluded,
                "withdrawn/excluded session lacks a versioned exclusion record",
            )

    event_sessions = {event.session_id for event in events}
    if require_session_records:
        _require(event_sessions <= set(by_session), "an event session lacks a session record")
    for session_id, row in by_session.items():
        if session_id in excluded:
            continue
        expected = participant_index.get(session_id)
        _require(expected is not None, "unexcluded session is absent from assignment")
        _require(row["participant_id"] == expected[0], "session participant differs from assignment")
        _require(bool(row["consent_acknowledged"]), "unexcluded session lacks consent acknowledgement")
        _require(row["session_status"] not in {"withdrawn", "excluded"}, "unexcluded session has an exclusion status")

    for event in events:
        if event.session_id in excluded:
            continue
        expected = participant_index.get(event.session_id)
        _require(expected is not None, "unexcluded event session is absent from assignment")
        _require(event.participant_id == expected[0], "event participant differs from assignment session")
        _require(
            trial_index.get((event.session_id, event.trial_id)) == event.task_id,
            "event trial differs from assignment",
        )
        session = by_session.get(event.session_id)
        if session is not None:
            _require(bool(session["consent_acknowledged"]), "events cannot be retained without consent acknowledgement")
    incomplete = {
        session_id
        for session_id, row in by_session.items()
        if row["session_status"] == "incomplete"
    }
    nonanalyzable = excluded | incomplete
    return excluded, nonanalyzable


def _validate_raw_prefixes(
    events: Sequence[StudyEvent],
    sessions: Sequence[Mapping[str, Any]],
    exclusions: Sequence[Mapping[str, Any]],
) -> None:
    """Validate state-machine prefixes without inventing durable events.

    The strict schema validator intentionally accepts only terminal trials.
    Live staging may end with a prefix after a restart or instrumentation
    failure.  For validation only, this function completes a nonterminal prefix
    with an in-memory ``session_terminated`` sentinel, runs the same strict
    state machine, and discards the sentinel.  The raw prefix is accepted only
    when its session is explicitly incomplete or has a versioned exclusion.
    Nothing synthetic is written to evidence.
    """

    session_status = {
        str(row["session_id"]): str(row["session_status"]) for row in sessions
    }
    exclusion_by_session = {
        str(row["session_id"]): str(row["reason_code"]) for row in exclusions
    }
    grouped: dict[tuple[str, str], list[StudyEvent]] = defaultdict(list)
    uuid_sessions: dict[str, list[str]] = defaultdict(list)
    for event in events:
        grouped[(event.session_id, event.trial_id)].append(event)
        uuid_sessions[event.event_uuid].append(event.session_id)

    for event_uuid, owning_sessions in uuid_sessions.items():
        if len(owning_sessions) < 2:
            continue
        _require(
            all(
                exclusion_by_session.get(session_id)
                == "instrumentation_corruption"
                for session_id in owning_sessions
            ),
            f"duplicate event_uuid {event_uuid!r} lacks instrumentation-corruption exclusions",
        )

    for trial_key, trial in grouped.items():
        trial.sort(key=lambda event: event.event_index)
        terminal = any(
            event.event_type in {EventType.CONFIRM, EventType.CANCEL}
            for event in trial
        )
        validation_trial = list(trial)
        if not terminal:
            session_id = trial_key[0]
            _require(
                session_id in exclusion_by_session
                or session_status.get(session_id) == "incomplete",
                f"nonterminal trial {trial_key!r} lacks an incomplete session or exclusion record",
            )
            last = trial[-1]
            sentinel = StudyEvent(
                study_id=last.study_id,
                assignment_id=last.assignment_id,
                session_id=last.session_id,
                participant_id=last.participant_id,
                trial_id=last.trial_id,
                task_id=last.task_id,
                pair_id=last.pair_id,
                condition=last.condition,
                consent_version=last.consent_version,
                event_uuid=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"prefix-validation:{last.session_id}:{last.trial_id}",
                    )
                ),
                event_index=last.event_index + 1,
                timestamp_utc=last.timestamp_utc,
                monotonic_seconds=last.monotonic_seconds,
                event_type=EventType.CANCEL,
                profile_id=None,
                image_id=None,
                old_profile_id=None,
                new_profile_id=None,
                old_image_id=None,
                new_image_id=None,
                preview_status=None,
                cancel_reason=CancelReason.SESSION_TERMINATED,
            )
            validation_trial.append(sentinel)
        validate_event_stream(validation_trial)


def _validate_complete_sessions(
    sessions: Sequence[Mapping[str, Any]],
    events: Sequence[StudyEvent],
    manifest: AssignmentManifest,
    excluded: set[str],
    questionnaires: Sequence[QuestionnaireRecord],
) -> set[str]:
    event_types: dict[tuple[str, str], set[EventType]] = defaultdict(set)
    for event in events:
        if event.session_id not in excluded:
            event_types[(event.session_id, event.trial_id)].add(event.event_type)
    assignment_by_session = {
        assignment.session_id: assignment for assignment in manifest.assignments
    }
    complete: set[str] = set()
    questionnaire_ids: dict[str, set[str]] = defaultdict(set)
    for record in questionnaires:
        if record.session_id not in excluded:
            questionnaire_ids[record.session_id].add(record.questionnaire_id)
    for row in sessions:
        session_id = str(row["session_id"])
        if session_id in excluded or row["session_status"] != "complete":
            continue
        assignment = assignment_by_session.get(session_id)
        _require(assignment is not None, "complete session is absent from assignment")
        for task in assignment.task_sequence:
            terminal = event_types.get((session_id, task.trial_id), set())
            _require(
                EventType.CONFIRM in terminal or EventType.CANCEL in terminal,
                "complete session is missing an assigned terminal trial",
            )
        _require(
            questionnaire_ids.get(session_id, set())
            == expected_questionnaire_ids(assignment),
            "complete session is missing a scheduled questionnaire submission",
        )
        complete.add(session_id)
    return complete


def _write_bytes_exclusive(path: Path, payload: bytes, *, mode: int = 0o644) -> Path:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(path, flags, mode)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("exclusive evidence write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def _write_jsonl_exclusive(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    payload = b"".join(_canonical_bytes(dict(row)) for row in rows)
    return _write_bytes_exclusive(path, payload)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_staging_directory(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.finalizing-{uuid.uuid4().hex}"
    staging.mkdir(mode=0o700, exist_ok=False)
    return staging


def _cleanup_staging_directory(staging: Path, target: Path) -> None:
    """Remove only the validated sibling directory created by this run."""

    expected_prefix = f".{target.name}.finalizing-"
    if (
        staging.parent == target.parent
        and staging.name.startswith(expected_prefix)
        and staging.is_dir()
    ):
        shutil.rmtree(staging)


def _publish_staging_directory(staging: Path, target: Path) -> None:
    """Serialize publishers, refuse an existing target, then atomically rename."""

    lock = target.parent / f".{target.name}.publish.lock"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    lock_fd = os.open(lock, flags, 0o600)
    try:
        os.fsync(lock_fd)
    finally:
        os.close(lock_fd)
    try:
        _require(not target.exists(), f"result directory already exists: {target}")
        os.rename(staging, target)
        _fsync_directory(target.parent)
    finally:
        if lock.exists():
            lock.unlink()
            _fsync_directory(target.parent)


def _read_assignment_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    _require(bool(raw), "assignment manifest cannot be empty")
    return raw


def prepare_environment_command(args: argparse.Namespace) -> dict[str, Any]:
    """Create a secret-free frozen fairness identity for study preparation."""

    from recommender.candidate_corpus import build_candidate_corpus
    from recommender.rule_based import load_image_catalog

    catalog = (
        load_image_catalog(args.catalog)
        if args.catalog is not None
        else load_image_catalog()
    )
    corpus = build_candidate_corpus(image_catalog=catalog)
    config = _load_json_object(args.config_identity, "config identity")
    _require(config is not None, "prepare-environment requires --config-identity")
    config_sha256 = canonical_json_sha256(config)
    config_id = "config-" + config_sha256[:24]
    deployment_revision = args.deployment_revision or _git_revision()
    fairness = build_fairness_manifest(
        catalog=catalog,
        corpus=corpus,
        freeze_id=args.freeze_id,
        config_identity=config_id,
        deployment_revision=deployment_revision,
        kubernetes_environment_id=args.kubernetes_environment_id,
    )
    environment_identity = {
        "environment_id": args.environment_id,
        "mode": (
            "development_unfrozen"
            if args.freeze_id == "development-unfrozen"
            else "frozen"
        ),
        "config_identity_sha256": config_sha256,
        "fairness_manifest": fairness,
    }
    validate_study_environment_identity(
        environment_identity,
        confirmatory=args.freeze_id != "development-unfrozen",
    )
    _require(not args.output.exists(), f"environment output already exists: {args.output}")
    write_json_exclusive(args.output, environment_identity)
    return {
        "schema_version": "protocol-v5-user-study-environment-preparation-v1.0.0",
        "status": "GENERATED",
        "output": str(args.output),
        "environment_id": args.environment_id,
        "fairness_manifest_sha256": canonical_json_sha256(fairness),
        "config_identity": config_id,
        "deployment_revision": deployment_revision,
    }


def validate_tasks_command(args: argparse.Namespace) -> dict[str, Any]:
    tasks = load_task_set(args.task_set)
    validated, _, _, _, _, _ = _authoritative_task_set(
        tasks,
        catalog_path=args.catalog,
        confirmatory=args.confirmatory,
    )
    projection = browser_safe_task_set(validated)
    return {
        "schema_version": "protocol-v5-user-study-task-validation-v1.0.0",
        "status": "VALID",
        "confirmatory_ready": (
            validated.stage is TaskSetStage.CONFIRMATORY
            and validated.status is TaskSetStatus.FROZEN
            and all(
                pair.gold.equivalence_review_status is ReviewStatus.APPROVED
                for pair in validated.pairs
            )
        ),
        "task_set_id": validated.task_set_id,
        "task_set_sha256": validated.checksum,
        "browser_task_set_schema_version": BROWSER_TASK_SET_SCHEMA_VERSION,
        "browser_task_set_sha256": canonical_json_sha256(projection),
        "warm_up_pair_count": sum(
            pair.phase is TaskPhase.WARM_UP for pair in validated.pairs
        ),
        "measured_pair_count": sum(
            pair.phase is TaskPhase.MEASURED for pair in validated.pairs
        ),
    }


def generate_assignments_command(args: argparse.Namespace) -> dict[str, Any]:
    tasks = load_task_set(args.task_set)
    validated, _, _, _, catalog, corpus = _authoritative_task_set(
        tasks,
        catalog_path=args.catalog,
        confirmatory=args.confirmatory,
    )
    environment = _load_json_object(args.environment_identity, "environment identity")
    config = _load_json_object(args.config_identity, "config identity")
    if args.confirmatory:
        _require(environment is not None, "confirmatory assignment requires --environment-identity")
        _require(config is not None, "confirmatory assignment requires --config-identity")
        _require(args.freeze_id != "development-unfrozen", "confirmatory assignment requires a frozen freeze_id")
    environment_payload = dict(
        environment
        or {
            "environment_id": "not-recorded",
            "mode": "development_unfrozen",
        }
    )
    config_sha256 = canonical_json_sha256(config) if config is not None else None
    config_id = (
        "config-" + config_sha256[:24]
        if config_sha256 is not None
        else "development-unfrozen"
    )
    if config_sha256 is not None:
        environment_payload["config_identity_sha256"] = config_sha256
    deployment_revision = args.git_revision or _git_revision()
    environment_payload = validate_study_environment_identity(
        environment_payload, confirmatory=args.confirmatory
    )
    fairness = environment_payload.get("fairness_manifest")
    if fairness is not None:
        verify_fairness_manifest(
            fairness,
            catalog=catalog,
            corpus=corpus,
            freeze_id=args.freeze_id,
            config_identity=config_id,
            deployment_revision=deployment_revision,
            kubernetes_environment_id=str(
                fairness["kubernetes_environment_id"]
            ),
            confirmatory=args.confirmatory,
        )
    generator_kwargs: dict[str, Any] = {
        "study_id": args.study_id,
        "participant_count": args.participant_count,
        "seed": args.seed,
        "consent_version": args.consent_version,
        "git_revision": deployment_revision,
        "freeze_id": args.freeze_id,
        "config_identity": config_id,
        "environment_identity": environment_payload,
        "generated_at_utc": args.generated_at_utc,
        "confirmatory": args.confirmatory,
    }
    manifest = generate_assignment_manifest(validated, **generator_kwargs)
    projection = browser_safe_task_set(validated)
    output = args.output_dir
    _require(not output.exists(), f"assignment output already exists: {output}")
    output.mkdir(parents=True, exist_ok=False)
    write_json_exclusive(output / "assignment-manifest.json", manifest.to_dict())
    write_json_exclusive(output / "browser-task-set.json", projection)
    _fsync_directory(output)
    return {
        "schema_version": "protocol-v5-user-study-assignment-generation-v1.0.0",
        "status": "GENERATED",
        "assignment_id": manifest.assignment_id,
        "assignment_sha256": manifest.checksum,
        "config_identity": manifest.config_identity,
        "seed": manifest.seed,
        "participant_count": manifest.participant_count,
        "output_directory": str(output),
        "files": ["assignment-manifest.json", "browser-task-set.json"],
        "balance_audit": dict(manifest.balance_audit),
    }


def validate_events_command(args: argparse.Namespace) -> dict[str, Any]:
    tasks = load_task_set(args.task_set)
    tasks, profile_ids, image_ids, _, _, _ = _authoritative_task_set(
        tasks,
        catalog_path=args.catalog,
        confirmatory=args.confirmatory,
    )
    assignments = load_assignment_manifest(str(args.assignments))
    validate_assignment_manifest(assignments, task_set=tasks)
    raw_records, _ = _load_jsonl(args.events, "event JSONL")
    parsed = validate_event_stream(
        raw_records,
        assignment_manifest=assignments,
        task_set=tasks,
        allowed_profile_ids=profile_ids,
        allowed_image_ids=image_ids,
    )
    _validate_events_against_assignment(parsed, assignments)
    trial_ids = {(event.session_id, event.trial_id) for event in parsed}
    return {
        "schema_version": "protocol-v5-user-study-event-validation-v1.0.0",
        "status": "VALID",
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "event_count": len(parsed),
        "trial_count": len(trial_ids),
        "participant_count": len({event.participant_id for event in parsed}),
        "event_file_sha256": _file_sha256(args.events),
    }


def validate_questionnaires_command(args: argparse.Namespace) -> dict[str, Any]:
    assignments = load_assignment_manifest(str(args.assignments))
    raw_records, _ = _load_jsonl(args.questionnaires, "questionnaire JSONL")
    parsed = validate_questionnaire_stream(raw_records, assignments)
    return {
        "schema_version": "protocol-v5-user-study-questionnaire-validation-v1.0.0",
        "status": "VALID",
        "questionnaire_schema_version": QUESTIONNAIRE_SCHEMA_VERSION,
        "record_count": len(parsed),
        "participant_count": len({record.participant_id for record in parsed}),
        "questionnaire_file_sha256": _file_sha256(args.questionnaires),
    }


def _empty_summary(execution_status: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "execution_status": execution_status,
        "outcomes_generated": False,
        "measured_task_count": None,
        "participant_count": None,
        "condition_distributions": None,
        "matched_pair_row_count": None,
        "complete_matched_pair_count": None,
        "paired_delta_distributions": None,
        "participant_level_paired_deltas": None,
        "formal_inference": {
            "status": "NOT_COMPUTED",
            "reason": reason,
        },
    }


def _limitations_text(
    *,
    status: str,
    task_set: TaskSet,
    exclusions: int,
) -> str:
    evidence = {
        "NOT_EXECUTED": "No participant or synthetic event stream was finalized.",
        "DRY_RUN": "All finalized events are declared synthetic dry-run inputs, not participant responses.",
        "INCOMPLETE": "The confirmatory recruitment target was not met; results are incomplete.",
        "OBSERVED": "This package contains descriptive observations but no formal inference.",
    }[status]
    return (
        "# Protocol-v5 E3 limitations\n\n"
        f"- Evidence status: `{status}`. {evidence}\n"
        f"- Task set: `{task_set.task_set_id}` ({task_set.stage.value}/{task_set.status.value}).\n"
        f"- Whole-session exclusions recorded: {exclusions}.\n"
        "- NOT_EXECUTED and DRY_RUN packages emit no empirical significance claim; real packages use the frozen two-outcome Holm family.\n"
        "- Inference accounts for repeated tasks clustered within participant, with matched task pair modeled as a fixed factor.\n"
        "- Findings are bounded to the recruited population, frozen scenarios, catalog, policy, Hub, and cluster identity.\n"
        "- Institutional consent, ethics review, retention, withdrawal, and legal requirements remain the researcher's responsibility.\n"
    )


def _resolve_output(args: argparse.Namespace) -> Path:
    _require(bool(_RUN_ID.fullmatch(args.run_id)), "run_id must be a safe identifier")
    if args.output_dir is not None:
        return args.output_dir.resolve()
    return (
        args.results_root.resolve()
        / PROTOCOL_DIRECTORY
        / EXPERIMENT_ID
        / args.run_id
    )


def finalize_command(args: argparse.Namespace) -> dict[str, Any]:
    status = args.execution_status
    _require(status in EXECUTION_STATUSES, "unsupported execution status")
    target = _resolve_output(args)
    _require(not target.exists(), f"result directory already exists: {target}")

    task_set_raw = args.task_set.read_bytes()
    tasks = load_task_set(args.task_set)
    real_status = status in {"INCOMPLETE", "OBSERVED"}
    tasks, profile_ids, image_ids, catalog_identity, catalog, corpus = _authoritative_task_set(
        tasks,
        catalog_path=args.catalog,
        confirmatory=real_status,
    )
    if tasks.stage is TaskSetStage.DEVELOPMENT or tasks.status is TaskSetStatus.DRAFT:
        _require(
            status in {"NOT_EXECUTED", "DRY_RUN"},
            "development/draft task sets can only produce NOT_EXECUTED or DRY_RUN evidence",
        )

    assignment_raw = _read_assignment_bytes(args.assignments)
    try:
        assignment_payload = json.loads(assignment_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UserStudyRunnerError("assignment manifest is invalid JSON") from exc
    assignments = validate_assignment_manifest(
        AssignmentManifest.from_dict(assignment_payload), task_set=tasks
    )
    _require(assignments.task_set_sha256 == tasks.checksum, "assignment/task-set checksum drift")
    environment_identity = validate_study_environment_identity(
        assignments.environment_identity, confirmatory=real_status
    )
    fairness_manifest = environment_identity.get("fairness_manifest")
    if fairness_manifest is not None:
        verify_fairness_manifest(
            fairness_manifest,
            catalog=catalog,
            corpus=corpus,
            freeze_id=assignments.freeze_id,
            config_identity=assignments.config_identity,
            deployment_revision=assignments.git_revision,
            kubernetes_environment_id=str(
                fairness_manifest["kubernetes_environment_id"]
            ),
            confirmatory=real_status,
        )
    elif real_status:
        raise UserStudyRunnerError(
            "real evidence requires a frozen B0/P2 fairness manifest"
        )
    if real_status:
        current_revision = _git_revision()
        _require(assignments.git_revision != "unknown", "real evidence requires a frozen Git revision")
        _require(current_revision == assignments.git_revision, "current Git revision differs from assignment freeze")

    event_records, event_raw = _load_jsonl(args.events, "event JSONL")
    session_records, session_raw = _load_jsonl(args.sessions, "session JSONL")
    exclusion_records, exclusion_raw = _load_jsonl(args.exclusions, "exclusion JSONL")
    questionnaire_records, questionnaire_raw = _load_jsonl(
        args.questionnaires, "questionnaire JSONL"
    )
    if status == "NOT_EXECUTED":
        _require(
            not event_records
            and not session_records
            and not exclusion_records
            and not questionnaire_records,
            "NOT_EXECUTED finalization rejects event, session, exclusion, or questionnaire records",
        )
    if real_status:
        _require(args.questionnaires is not None, "real evidence requires --questionnaires")

    sessions = _validate_sessions(session_records, assignments)
    exclusions = _validate_exclusions(exclusion_records, assignments)
    questionnaires = validate_questionnaire_stream(
        questionnaire_records, assignments
    )
    parsed_events = [validate_event(row) for row in event_records]
    if parsed_events:
        _validate_raw_prefixes(parsed_events, sessions, exclusions)
    excluded, nonanalyzable_sessions = _validate_session_bindings(
        sessions,
        exclusions,
        parsed_events,
        assignments,
        require_session_records=real_status,
    )
    analyzable_events = [
        event
        for event in parsed_events
        if event.session_id not in nonanalyzable_sessions
    ]
    analyzable_questionnaires = [
        record
        for record in questionnaires
        if record.session_id not in nonanalyzable_sessions
    ]
    if analyzable_events:
        validate_event_stream(
            analyzable_events,
            assignment_manifest=assignments,
            task_set=tasks,
            allowed_profile_ids=profile_ids,
            allowed_image_ids=image_ids,
        )
    elif status == "OBSERVED":
        raise UserStudyRunnerError(f"{status} finalization requires analyzable events")

    complete_sessions = _validate_complete_sessions(
        sessions,
        analyzable_events,
        assignments,
        nonanalyzable_sessions,
        analyzable_questionnaires,
    )
    if real_status:
        _require(bool(sessions), "real evidence requires versioned session records")
    if status == "OBSERVED":
        _require(
            len(complete_sessions) == PARTICIPANT_TARGET,
            f"OBSERVED requires {PARTICIPANT_TARGET} valid completed crossovers",
        )
        complete_rows = [row for row in sessions if row["session_id"] in complete_sessions]
        _require(
            len({row["participant_id"] for row in complete_rows}) == PARTICIPANT_TARGET,
            "OBSERVED completed participants are not unique",
        )
        participant_assignments = {
            assignment.session_id: assignment for assignment in assignments.assignments
        }
        cell_counts = Counter(
            participant_assignments[str(row["session_id"])].counterbalance_cell
            for row in complete_rows
        )
        _require(
            len(cell_counts) == 12 and set(cell_counts.values()) == {3},
            "OBSERVED valid crossovers must preserve three participants per counterbalance cell",
        )
        _require(
            {event.session_id for event in analyzable_events} == complete_sessions,
            "OBSERVED evidence cannot include an unexcluded incomplete session",
        )

    if analyzable_events:
        metric_layers = derive_study_metrics(
            analyzable_events,
            tasks,
            assignments,
            execution_status=status,
        )
        task_rows = metric_layers["task_outcomes"]
        pair_rows = metric_layers["matched_pair_outcomes"]
        summary = metric_layers["summary"]
        summary["outcomes_generated"] = True
    else:
        task_rows = []
        pair_rows = []
        summary = _empty_summary(
            status,
            "No analyzable event stream was supplied; no outcome denominator was invented.",
        )
    questionnaire_rows = derive_questionnaire_outcomes(
        analyzable_questionnaires
    )
    analysis_dependencies = (
        analysis_dependency_versions()
        if status == "NOT_EXECUTED"
        else validate_analysis_dependencies()
    )
    analysis = analyze_user_study(
        execution_status=status,
        task_rows=task_rows,
        questionnaire_rows=questionnaire_rows,
        sessions=sessions,
        exclusions=exclusions,
        assignment_manifest=assignments,
    )
    summary["session_coverage"] = {
        "session_record_count": len(sessions),
        "complete_session_count": len(complete_sessions),
        "excluded_session_count": len(excluded),
        "incomplete_session_count": len(
            nonanalyzable_sessions - excluded
        ),
        "analyzable_event_session_count": len(
            {event.session_id for event in analyzable_events}
        ),
    }
    summary["exclusions"] = {
        "count": len(exclusions),
        "by_reason": dict(sorted(Counter(row["reason_code"] for row in exclusions).items())),
    }
    if status == "OBSERVED":
        _require(
            len(task_rows) == PARTICIPANT_TARGET * 6,
            "OBSERVED evidence requires six measured outcomes per valid crossover",
        )
        _require(
            len(pair_rows) == PARTICIPANT_TARGET * 3
            and all(row["pair_complete"] for row in pair_rows),
            "OBSERVED evidence requires three complete matched pairs per participant",
        )

    _require(
        args.task_set.read_bytes() == task_set_raw,
        "task-set source changed during finalization",
    )
    _require(
        args.assignments.read_bytes() == assignment_raw,
        "assignment source changed during finalization",
    )

    created_at = _validate_utc(
        args.created_at_utc or _utc_now(), "created_at_utc"
    )
    status_report = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "run_id": args.run_id,
        "execution_status": status,
        "experiment_executed": real_status,
        "synthetic_only": status == "DRY_RUN",
        "observed_evidence": real_status,
        "formal_inference_emitted": real_status
        and any(
            analysis.get("effects", {}).get(endpoint, {}).get("p_value_raw")
            is not None
            for endpoint in ("selection_success", "decision_time_seconds")
        ),
        "task_set_stage": tasks.stage.value,
        "task_set_status": tasks.status.value,
        "raw_event_count": len(parsed_events),
        "analyzable_event_count": len(analyzable_events),
        "raw_questionnaire_submission_count": len(questionnaires),
        "analyzable_questionnaire_submission_count": len(
            analyzable_questionnaires
        ),
        "measured_task_outcome_count": len(task_rows),
        "participant_target": PARTICIPANT_TARGET,
        "valid_completed_crossover_count": len(complete_sessions),
    }
    limitations = _limitations_text(
        status=status,
        task_set=tasks,
        exclusions=len(exclusions),
    ).encode("utf-8")

    input_checksums = {
        "task_set": {
            "artifact_role": "authoritative_task_set",
            "file_sha256": _sha256_bytes(task_set_raw),
            "canonical_sha256": tasks.checksum,
        },
        "assignment_manifest": {
            "artifact_role": "assignment_manifest",
            "file_sha256": _sha256_bytes(assignment_raw),
            "canonical_sha256": assignments.checksum,
        },
        "events": {
            "artifact_role": "staging_events",
            "supplied": args.events is not None,
            "file_sha256": _sha256_bytes(event_raw) if args.events is not None else None,
            "record_count": len(parsed_events),
        },
        "sessions": {
            "artifact_role": "staging_sessions",
            "supplied": args.sessions is not None,
            "file_sha256": _sha256_bytes(session_raw) if args.sessions is not None else None,
            "record_count": len(sessions),
        },
        "exclusions": {
            "artifact_role": "staging_exclusions",
            "supplied": args.exclusions is not None,
            "file_sha256": _sha256_bytes(exclusion_raw) if args.exclusions is not None else None,
            "record_count": len(exclusions),
        },
        "questionnaires": {
            "artifact_role": "staging_questionnaires",
            "supplied": args.questionnaires is not None,
            "file_sha256": (
                _sha256_bytes(questionnaire_raw)
                if args.questionnaires is not None
                else None
            ),
            "record_count": len(questionnaires),
        },
    }
    staging = _create_staging_directory(target)
    try:
        raw_dir = staging / "raw"
        derived_dir = staging / "derived"
        report_dir = staging / "report"
        for directory in (raw_dir, derived_dir, report_dir):
            directory.mkdir(exist_ok=False)

        raw_assignment = _write_bytes_exclusive(
            raw_dir / "assignment-manifest.json", assignment_raw
        )
        raw_events = _write_bytes_exclusive(raw_dir / "events.jsonl", event_raw)
        raw_sessions = _write_bytes_exclusive(raw_dir / "sessions.jsonl", session_raw)
        raw_exclusions = _write_bytes_exclusive(
            raw_dir / "exclusions.jsonl", exclusion_raw
        )
        raw_questionnaires = _write_bytes_exclusive(
            raw_dir / "questionnaires.jsonl", questionnaire_raw
        )
        task_output = _write_jsonl_exclusive(
            derived_dir / "task-outcomes.jsonl", task_rows
        )
        pair_output = _write_jsonl_exclusive(
            derived_dir / "matched-pair-outcomes.jsonl", pair_rows
        )
        questionnaire_output = _write_jsonl_exclusive(
            derived_dir / "questionnaire-outcomes.jsonl", questionnaire_rows
        )
        summary_output = write_json_exclusive(derived_dir / "summary.json", summary)
        status_output = write_json_exclusive(report_dir / "status.json", status_report)
        limitations_output = _write_bytes_exclusive(
            report_dir / "limitations.md", limitations
        )
        analysis_outputs = write_analysis_artifacts(staging, analysis)
        output_paths = (
            raw_assignment,
            raw_events,
            raw_sessions,
            raw_exclusions,
            raw_questionnaires,
            task_output,
            pair_output,
            questionnaire_output,
            summary_output,
            status_output,
            limitations_output,
            *analysis_outputs,
        )
        output_checksums = {
            str(path.relative_to(staging)): _file_sha256(path)
            for path in output_paths
        }
        provenance = {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "experiment_id": EXPERIMENT_ID,
            "run_id": args.run_id,
            "execution_status": status,
            "created_at_utc": created_at,
            "finalizer_version": FINALIZER_VERSION,
            "git_revision": _git_revision(),
            "runtime": {
                "python_version": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "platform": platform.platform(),
                "analysis_dependencies": analysis_dependencies,
            },
            "contracts": {
                "task_set_schema_version": TASK_SET_SCHEMA_VERSION,
                "assignment_schema_version": ASSIGNMENT_SCHEMA_VERSION,
                "assignment_generator_version": ASSIGNMENT_GENERATOR_VERSION,
                "event_schema_version": EVENT_SCHEMA_VERSION,
                "selection_scoring_version": FINAL_SELECTION_SCORING_VERSION,
                "questionnaire_schema_version": QUESTIONNAIRE_SCHEMA_VERSION,
                "questionnaire_schema_sha256": QUESTIONNAIRE_SCHEMA_SHA256,
                "questionnaire_instrument_version": QUESTIONNAIRE_INSTRUMENT_VERSION,
                "questionnaire_instrument_sha256": QUESTIONNAIRE_INSTRUMENT_SHA256,
                "questionnaire_outcome_schema_version": QUESTIONNAIRE_OUTCOME_SCHEMA_VERSION,
                "analysis_plan_version": ANALYSIS_PLAN_VERSION,
                "analysis_plan_sha256": ANALYSIS_PLAN_SHA256,
                "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
                "task_outcome_schema_version": TASK_OUTCOME_SCHEMA_VERSION,
                "matched_pair_outcome_schema_version": MATCHED_PAIR_OUTCOME_SCHEMA_VERSION,
                "fairness_manifest_schema_version": FAIRNESS_MANIFEST_SCHEMA_VERSION,
                "session_schema_version": SESSION_SCHEMA_VERSION,
                "exclusion_schema_version": EXCLUSION_SCHEMA_VERSION,
                "summary_schema_version": SUMMARY_SCHEMA_VERSION,
            },
            "study_identity": {
                "study_id": assignments.study_id,
                "assignment_id": assignments.assignment_id,
                "task_set_id": tasks.task_set_id,
                "consent_version": assignments.consent_version,
                "freeze_id": assignments.freeze_id,
                "seed": assignments.seed,
                "catalog_version": assignments.catalog_version,
                "corpus_version": assignments.corpus_version,
                "policy_version": assignments.policy_version,
                "environment_identity": dict(assignments.environment_identity),
                "fairness_manifest_sha256": (
                    canonical_json_sha256(fairness_manifest)
                    if fairness_manifest is not None
                    else None
                ),
                "config_identity": assignments.config_identity,
                "authoritative_catalog": catalog_identity,
            },
            "input_checksums": input_checksums,
            "output_checksums": output_checksums,
        }
        write_json_exclusive(staging / "manifest.json", provenance)
        _fsync_directory(raw_dir)
        _fsync_directory(derived_dir)
        _fsync_directory(report_dir)
        _fsync_directory(staging)
        _publish_staging_directory(staging, target)
    except Exception:
        _cleanup_staging_directory(staging, target)
        raise
    manifest_output = target / "manifest.json"
    return {
        "schema_version": "protocol-v5-user-study-finalization-result-v1.0.0",
        "status": "FINALIZED",
        "execution_status": status,
        "run_id": args.run_id,
        "result_directory": str(target),
        "manifest": str(manifest_output),
        "raw_event_count": len(parsed_events),
        "questionnaire_submission_count": len(questionnaires),
        "measured_task_outcome_count": len(task_rows),
        "matched_pair_outcome_count": len(pair_rows),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Protocol-v5 B0-versus-P2 human-study framework"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    environment_parser = commands.add_parser(
        "prepare-environment",
        help="Create a frozen, secret-free B0/P2 fairness identity",
    )
    environment_parser.add_argument("--output", type=Path, required=True)
    environment_parser.add_argument("--environment-id", required=True)
    environment_parser.add_argument("--kubernetes-environment-id", required=True)
    environment_parser.add_argument("--freeze-id", required=True)
    environment_parser.add_argument("--config-identity", type=Path, required=True)
    environment_parser.add_argument("--deployment-revision")
    environment_parser.add_argument("--catalog", type=Path)
    environment_parser.set_defaults(handler=prepare_environment_command)

    task_parser = commands.add_parser(
        "validate-tasks", help="Validate a strict matched-pair task bundle"
    )
    task_parser.add_argument("task_set", type=Path)
    task_parser.add_argument("--catalog", type=Path)
    task_parser.add_argument("--confirmatory", action="store_true")
    task_parser.set_defaults(handler=validate_tasks_command)

    assignment_parser = commands.add_parser(
        "generate-assignments", help="Generate a deterministic counterbalanced manifest"
    )
    assignment_parser.add_argument("task_set", type=Path)
    assignment_parser.add_argument(
        "--output-dir", "--output", dest="output_dir", type=Path, required=True
    )
    assignment_parser.add_argument("--study-id", required=True)
    assignment_parser.add_argument("--participant-count", type=int, default=PARTICIPANT_TARGET)
    assignment_parser.add_argument("--seed", type=int, required=True)
    assignment_parser.add_argument("--consent-version", required=True)
    assignment_parser.add_argument("--git-revision")
    assignment_parser.add_argument("--freeze-id", default="development-unfrozen")
    assignment_parser.add_argument("--environment-identity", type=Path)
    assignment_parser.add_argument("--config-identity", type=Path)
    assignment_parser.add_argument("--generated-at-utc")
    assignment_parser.add_argument("--catalog", type=Path)
    assignment_parser.add_argument("--confirmatory", action="store_true")
    assignment_parser.set_defaults(handler=generate_assignments_command)

    event_parser = commands.add_parser(
        "validate-events", help="Validate a complete content-free event stream"
    )
    event_parser.add_argument("events", type=Path)
    event_parser.add_argument("--task-set", type=Path, required=True)
    event_parser.add_argument("--assignments", type=Path, required=True)
    event_parser.add_argument("--catalog", type=Path)
    event_parser.add_argument("--confirmatory", action="store_true")
    event_parser.set_defaults(handler=validate_events_command)

    questionnaire_parser = commands.add_parser(
        "validate-questionnaires",
        help="Validate closed-response questionnaire exports",
    )
    questionnaire_parser.add_argument("questionnaires", type=Path)
    questionnaire_parser.add_argument("--assignments", type=Path, required=True)
    questionnaire_parser.set_defaults(handler=validate_questionnaires_command)

    finalize_parser = commands.add_parser(
        "finalize", help="Validate staging data and create an immutable E3 result"
    )
    finalize_parser.add_argument("--run-id", required=True)
    finalize_parser.add_argument("--task-set", type=Path, required=True)
    finalize_parser.add_argument("--assignments", type=Path, required=True)
    finalize_parser.add_argument("--events", type=Path)
    finalize_parser.add_argument("--sessions", type=Path)
    finalize_parser.add_argument("--exclusions", type=Path)
    finalize_parser.add_argument("--questionnaires", type=Path)
    finalize_parser.add_argument("--catalog", type=Path)
    finalize_parser.add_argument("--execution-status", choices=sorted(EXECUTION_STATUSES), default="NOT_EXECUTED")
    finalize_parser.add_argument("--created-at-utc")
    finalize_parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    finalize_parser.add_argument("--output-dir", type=Path)
    finalize_parser.set_defaults(handler=finalize_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
    except (
        UserStudyRunnerError,
        UserStudyValidationError,
        QuestionnaireValidationError,
        UserStudyAnalysisError,
        FileExistsError,
        OSError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {
                    "schema_version": "protocol-v5-user-study-cli-error-v1.0.0",
                    "status": "ERROR",
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


__all__ = [
    "EXCLUSION_REASONS",
    "EXCLUSION_REASON_VERSION",
    "EXCLUSION_SCHEMA_VERSION",
    "FINALIZER_VERSION",
    "PROVENANCE_SCHEMA_VERSION",
    "SESSION_SCHEMA_VERSION",
    "STATUS_SCHEMA_VERSION",
    "UserStudyRunnerError",
    "finalize_command",
    "generate_assignments_command",
    "main",
    "prepare_environment_command",
    "validate_events_command",
    "validate_tasks_command",
]
