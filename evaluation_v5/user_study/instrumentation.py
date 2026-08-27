"""Content-free, crash-safe event storage for the Protocol-v5 user study.

The live study writes one canonical JSON object per line.  This module is
deliberately small and synchronous: a study event is not acknowledged until
the append has been flushed to the kernel and ``fsync`` has completed.  It is
intended for the study-only JupyterHub process and not for general application
logging.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterator


COMPLETION_SCHEMA_VERSION = (
    "protocol-v5-user-study-session-completion-v1.0.0"
)

_COMPLETION_FIELDS = frozenset(
    {
        "schema_version",
        "session_id",
        "participant_id",
        "assignment_id",
        "completed_at_utc",
        "event_count",
        "trial_count",
        "trial_ids_sha256",
        "last_event_uuid",
        "events_sha256",
    }
)
_SAFE_FILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EMAIL_LIKE = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"
)
_FORBIDDEN_CONTENT_FIELDS = frozenset(
    {
        "payload",
        "data",
        "metadata",
        "attributes",
        "details",
        "message",
        "text",
        "intent",
        "intent_text",
        "raw_intent",
        "raw_text",
        "scenario",
        "notebook",
        "notebook_content",
        "code",
        "email",
        "name",
        "participant_name",
        "username",
        "user_name",
    }
)


class InstrumentationError(ValueError):
    """Base error raised when study instrumentation cannot be trusted."""


class InstrumentationPrivacyError(InstrumentationError):
    """Raised before content-bearing or identifying data can be persisted."""


class InstrumentationCorruptionError(InstrumentationError):
    """Raised when an existing append-only stream is malformed."""


class EventUUIDConflictError(InstrumentationError):
    """Raised when an event UUID is reused for different event content."""


class SessionAlreadyCompleteError(InstrumentationError):
    """Raised when an immutable completed session would be changed."""


def _mapping(value: object, label: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, Mapping):
            return dict(converted)
    raise InstrumentationError(f"{label} must be a mapping or expose to_dict()")


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise InstrumentationError("study event must be canonical JSON data") from exc


def _reject_content(value: object, *, path: str = "event") -> None:
    """Reject generic payloads and common sources of participant PII.

    Exact schema validation remains the primary allow-list.  This recursive
    check is defence in depth for callers that inject a custom validator.
    """

    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            if key.casefold() in _FORBIDDEN_CONTENT_FIELDS:
                raise InstrumentationPrivacyError(
                    f"{path}.{key} is forbidden in content-free research logs"
                )
            _reject_content(child, path=f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_content(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and _EMAIL_LIKE.search(value):
        raise InstrumentationPrivacyError(
            f"{path} appears to contain an email address"
        )


def _default_record_validator(value: Mapping[str, Any]) -> dict[str, Any]:
    # Imported lazily so schema code can import this module without a cycle.
    from .schemas import StudyEvent

    return StudyEvent.from_dict(value).to_dict()


def _default_stream_validator(events: Sequence[Mapping[str, Any]]) -> None:
    from .schemas import validate_event_stream

    # The live append log is necessarily a prefix while a participant is
    # interacting with a trial.  Prefix mode still enforces every transition
    # seen so far; strict terminal-state validation is performed by finalization
    # and completion sealing below.
    validate_event_stream(events, allow_incomplete=True)


def _normalise_event(
    event: object,
    validator: Callable[[Mapping[str, Any]], object] | None,
) -> dict[str, Any]:
    raw = _mapping(event, "event")
    _reject_content(raw)
    validated = (validator or _default_record_validator)(raw)
    if validated is None:
        normalised = raw
    else:
        normalised = _mapping(validated, "validated event")
    _reject_content(normalised)
    if not isinstance(normalised.get("event_uuid"), str) or not normalised[
        "event_uuid"
    ]:
        raise InstrumentationError("event.event_uuid must be a non-blank string")
    _canonical_json(normalised)
    return normalised


def _validate_utc(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InstrumentationError(f"{label} must be a non-blank UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InstrumentationError(f"{label} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise InstrumentationError(f"{label} must include the UTC offset")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_session_id(session_id: object) -> str:
    if not isinstance(session_id, str) or not _SAFE_FILE_ID.fullmatch(session_id):
        raise InstrumentationError(
            "session_id must contain only safe identifier characters"
        )
    return session_id


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    directory_fd = os.open(path, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("append-only event write made no progress")
        view = view[written:]


def _read_all(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _decode_jsonl(data: bytes, path: Path) -> list[dict[str, Any]]:
    if not data:
        return []
    if not data.endswith(b"\n"):
        raise InstrumentationCorruptionError(
            f"{path}: append-only JSONL has an incomplete final record"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InstrumentationCorruptionError(f"{path}: JSONL is not UTF-8") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            raise InstrumentationCorruptionError(
                f"{path}:{line_number}: blank JSONL records are not allowed"
            )
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InstrumentationCorruptionError(
                f"{path}:{line_number}: invalid JSONL record"
            ) from exc
        if not isinstance(value, Mapping):
            raise InstrumentationCorruptionError(
                f"{path}:{line_number}: event must be a JSON object"
            )
        records.append(dict(value))
    return records


class AppendOnlyEventStore:
    """Locked JSONL event store with UUID idempotency and completion seals."""

    def __init__(
        self,
        events_path: str | Path,
        completion_dir: str | Path | None = None,
        *,
        validator: Callable[[Mapping[str, Any]], object] | None = None,
        stream_validator: Callable[[Sequence[Mapping[str, Any]]], object]
        | None = None,
    ) -> None:
        self.events_path = Path(events_path)
        self.completion_dir = (
            Path(completion_dir)
            if completion_dir is not None
            else self.events_path.parent / "completion-markers"
        )
        self._validator = validator
        self._stream_validator = stream_validator

    @contextmanager
    def _locked_events_fd(self, *, exclusive: bool) -> Iterator[int]:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        fd = os.open(self.events_path, flags, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield fd
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _marker_path(self, session_id: object) -> Path:
        safe_id = _safe_session_id(session_id)
        return self.completion_dir / f"{safe_id}.complete.json"

    def _validated_records_from_fd(self, fd: int) -> list[dict[str, Any]]:
        raw_records = _decode_jsonl(_read_all(fd), self.events_path)
        records = [
            _normalise_event(record, self._validator) for record in raw_records
        ]
        (self._stream_validator or _default_stream_validator)(records)
        return records

    def append(self, event: object) -> bool:
        """Durably append one event.

        Returns ``False`` for an exact retry of an already-durable UUID.  Reuse
        of a UUID with different content is an error, as is any append after
        the event's session has been completed.
        """

        record = _normalise_event(event, self._validator)
        session_id = record.get("session_id")
        marker_path = self._marker_path(session_id)
        encoded = _canonical_json(record)

        with self._locked_events_fd(exclusive=True) as fd:
            if marker_path.exists():
                raise SessionAlreadyCompleteError(
                    f"session {session_id!r} already has a completion marker"
                )
            records = self._validated_records_from_fd(fd)
            for existing in records:
                if existing.get("event_uuid") != record["event_uuid"]:
                    continue
                if _canonical_json(existing) == encoded:
                    return False
                raise EventUUIDConflictError(
                    f"event_uuid {record['event_uuid']!r} was reused with different content"
                )

            prospective = [*records, record]
            (self._stream_validator or _default_stream_validator)(prospective)
            _write_all(fd, (encoded + "\n").encode("utf-8"))
            os.fsync(fd)
        return True

    def read_events(self) -> list[dict[str, Any]]:
        """Read and validate the durable event-stream prefix."""

        with self._locked_events_fd(exclusive=False) as fd:
            return self._validated_records_from_fd(fd)

    def is_session_complete(self, session_id: str) -> bool:
        return self._marker_path(session_id).is_file()

    def complete_session(
        self,
        session_id: str,
        *,
        completed_at_utc: str | None = None,
        expected_trial_ids: Sequence[str] | None = None,
    ) -> Path:
        """Exclusively create an immutable completion marker for one session."""

        safe_session_id = _safe_session_id(session_id)
        marker_path = self._marker_path(safe_session_id)
        timestamp = _validate_utc(
            completed_at_utc if completed_at_utc is not None else _utc_now(),
            "completed_at_utc",
        )

        with self._locked_events_fd(exclusive=True) as events_fd:
            if marker_path.exists():
                raise SessionAlreadyCompleteError(
                    f"session {safe_session_id!r} is already complete"
                )
            records = self._validated_records_from_fd(events_fd)
            session_events = [
                record
                for record in records
                if record.get("session_id") == safe_session_id
            ]
            if not session_events:
                raise InstrumentationError(
                    f"session {safe_session_id!r} has no durable events"
                )

            terminal_by_trial: dict[str, bool] = {}
            for record in session_events:
                trial_id = str(record["trial_id"])
                terminal_by_trial.setdefault(trial_id, False)
                if record["event_type"] in {"confirm", "cancel"}:
                    terminal_by_trial[trial_id] = True
            incomplete = sorted(
                trial_id
                for trial_id, terminal in terminal_by_trial.items()
                if not terminal
            )
            if incomplete:
                raise InstrumentationError(
                    "cannot complete a session with unterminated trials: "
                    + ", ".join(incomplete)
                )
            observed_trial_ids = sorted(terminal_by_trial)
            if expected_trial_ids is not None:
                if isinstance(expected_trial_ids, (str, bytes, bytearray)):
                    raise InstrumentationError(
                        "expected_trial_ids must be a sequence of trial IDs"
                    )
                expected = tuple(_safe_session_id(item) for item in expected_trial_ids)
                if not expected or len(set(expected)) != len(expected):
                    raise InstrumentationError(
                        "expected_trial_ids must be a non-empty unique sequence"
                    )
                missing = sorted(set(expected) - set(observed_trial_ids))
                unexpected = sorted(set(observed_trial_ids) - set(expected))
                if missing or unexpected:
                    details = []
                    if missing:
                        details.append("missing " + ", ".join(missing))
                    if unexpected:
                        details.append("unexpected " + ", ".join(unexpected))
                    raise InstrumentationError(
                        "session trial coverage differs from its assignment: "
                        + "; ".join(details)
                    )

            # A completion seal is never created from prefix-only validation.
            # Validate the isolated session in strict mode so every trial must
            # have reached confirm or cancel even if another session in the
            # shared JSONL is still active.
            from .schemas import validate_event_stream

            validate_event_stream(session_events)

            participant_ids = {record["participant_id"] for record in session_events}
            assignment_ids = {record["assignment_id"] for record in session_events}
            if len(participant_ids) != 1 or len(assignment_ids) != 1:
                raise InstrumentationCorruptionError(
                    "session events disagree on participant_id or assignment_id"
                )

            session_json = "".join(
                _canonical_json(record) + "\n" for record in session_events
            ).encode("utf-8")
            trial_ids_json = _canonical_json({"trial_ids": observed_trial_ids}).encode(
                "utf-8"
            )
            marker = {
                "schema_version": COMPLETION_SCHEMA_VERSION,
                "session_id": safe_session_id,
                "participant_id": next(iter(participant_ids)),
                "assignment_id": next(iter(assignment_ids)),
                "completed_at_utc": timestamp,
                "event_count": len(session_events),
                "trial_count": len(observed_trial_ids),
                "trial_ids_sha256": hashlib.sha256(trial_ids_json).hexdigest(),
                "last_event_uuid": session_events[-1]["event_uuid"],
                "events_sha256": hashlib.sha256(session_json).hexdigest(),
            }
            marker_json = _canonical_json(marker) + "\n"
            self.completion_dir.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            try:
                marker_fd = os.open(marker_path, flags, 0o600)
            except FileExistsError as exc:
                raise SessionAlreadyCompleteError(
                    f"session {safe_session_id!r} is already complete"
                ) from exc
            try:
                _write_all(marker_fd, marker_json.encode("utf-8"))
                os.fsync(marker_fd)
            finally:
                os.close(marker_fd)
            _fsync_directory(self.completion_dir)
        return marker_path

    def read_completion_marker(self, session_id: str) -> dict[str, Any] | None:
        """Return a validated completion marker, or ``None`` if not complete."""

        marker_path = self._marker_path(session_id)
        if not marker_path.exists():
            return None
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InstrumentationCorruptionError(
                f"{marker_path}: invalid completion marker"
            ) from exc
        if not isinstance(marker, Mapping) or set(marker) != _COMPLETION_FIELDS:
            raise InstrumentationCorruptionError(
                f"{marker_path}: completion marker fields do not match the schema"
            )
        if marker.get("schema_version") != COMPLETION_SCHEMA_VERSION:
            raise InstrumentationCorruptionError(
                f"{marker_path}: unsupported completion marker schema"
            )
        if marker.get("session_id") != session_id:
            raise InstrumentationCorruptionError(
                f"{marker_path}: completion marker session mismatch"
            )
        _validate_utc(marker.get("completed_at_utc"), "completed_at_utc")
        if not isinstance(marker.get("event_count"), int) or isinstance(
            marker.get("event_count"), bool
        ) or marker["event_count"] < 1:
            raise InstrumentationCorruptionError(
                f"{marker_path}: event_count must be a positive integer"
            )
        if not isinstance(marker.get("trial_count"), int) or isinstance(
            marker.get("trial_count"), bool
        ) or marker["trial_count"] < 1:
            raise InstrumentationCorruptionError(
                f"{marker_path}: trial_count must be a positive integer"
            )
        for checksum_field in ("trial_ids_sha256", "events_sha256"):
            if not isinstance(marker.get(checksum_field), str) or not re.fullmatch(
                r"[0-9a-f]{64}", marker[checksum_field]
            ):
                raise InstrumentationCorruptionError(
                    f"{marker_path}: invalid {checksum_field}"
                )
        _reject_content(marker, path="completion_marker")

        # Rebind the seal to the current durable session records.  This catches
        # accidental edits/truncation even when the marker file itself remains
        # syntactically valid.  Other sessions may continue appending safely.
        with self._locked_events_fd(exclusive=False) as events_fd:
            records = self._validated_records_from_fd(events_fd)
        session_events = [
            record for record in records if record.get("session_id") == session_id
        ]
        if not session_events:
            raise InstrumentationCorruptionError(
                f"{marker_path}: completion marker has no durable session events"
            )
        from .schemas import validate_event_stream

        validate_event_stream(session_events)
        participant_ids = {record["participant_id"] for record in session_events}
        assignment_ids = {record["assignment_id"] for record in session_events}
        session_json = "".join(
            _canonical_json(record) + "\n" for record in session_events
        ).encode("utf-8")
        observed_trial_ids = sorted({str(record["trial_id"]) for record in session_events})
        trial_ids_json = _canonical_json({"trial_ids": observed_trial_ids}).encode(
            "utf-8"
        )
        expected = {
            "participant_id": next(iter(participant_ids))
            if len(participant_ids) == 1
            else None,
            "assignment_id": next(iter(assignment_ids))
            if len(assignment_ids) == 1
            else None,
            "event_count": len(session_events),
            "trial_count": len(observed_trial_ids),
            "trial_ids_sha256": hashlib.sha256(trial_ids_json).hexdigest(),
            "last_event_uuid": session_events[-1]["event_uuid"],
            "events_sha256": hashlib.sha256(session_json).hexdigest(),
        }
        for field_name, expected_value in expected.items():
            if marker.get(field_name) != expected_value:
                raise InstrumentationCorruptionError(
                    f"{marker_path}: completion marker {field_name} mismatch"
                )
        return dict(marker)


# Clear alias for callers that prefer the domain name over the storage detail.
StudyEventStore = AppendOnlyEventStore


def append_event(
    path: str | Path,
    event: object,
    *,
    completion_dir: str | Path | None = None,
    validator: Callable[[Mapping[str, Any]], object] | None = None,
    stream_validator: Callable[[Sequence[Mapping[str, Any]]], object] | None = None,
) -> bool:
    """Convenience wrapper around :class:`AppendOnlyEventStore.append`."""

    return AppendOnlyEventStore(
        path,
        completion_dir,
        validator=validator,
        stream_validator=stream_validator,
    ).append(event)


def read_events(
    path: str | Path,
    *,
    completion_dir: str | Path | None = None,
    validator: Callable[[Mapping[str, Any]], object] | None = None,
    stream_validator: Callable[[Sequence[Mapping[str, Any]]], object] | None = None,
) -> list[dict[str, Any]]:
    """Convenience wrapper around :class:`AppendOnlyEventStore.read_events`."""

    return AppendOnlyEventStore(
        path,
        completion_dir,
        validator=validator,
        stream_validator=stream_validator,
    ).read_events()
