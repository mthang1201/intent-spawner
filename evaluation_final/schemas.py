"""Strict schemas for final-evaluation user-study and prediction evidence."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .systems import active_primary_system_ids, validate_primary_system_id


RQ1_TASK_SET_SCHEMA_VERSION = "final-rq1-task-set-v1.0.0"
RQ1_EVENT_SCHEMA_VERSION = "final-rq1-user-event-v1.0.0"
RQ1_PROTOCOL_VERSION = "final-rq1-user-study-protocol-v1.0.0"

RQ1_EVENT_TYPES = (
    "study_started",
    "candidate_selected",
    "recommendation_previewed",
    "recommendation_accepted",
    "recommendation_rejected",
    "manual_correction",
    "task_completed",
    "task_abandoned",
)

_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "study_id",
        "protocol_version",
        "participant_id",
        "session_id",
        "task_id",
        "system_id",
        "event_index",
        "elapsed_seconds",
        "event_type",
        "candidate_id",
    }
)
_CANDIDATE_REQUIRED_EVENTS = frozenset(
    {"candidate_selected", "recommendation_accepted", "manual_correction", "task_completed"}
)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-blank string")
    return value.strip()


def _string_list(value: object, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValueError(f"{label} must be a {'possibly empty ' if allow_empty else 'non-empty '}list")
    selected = [_nonblank(item, label) for item in value]
    if len(selected) != len(set(selected)):
        raise ValueError(f"{label} must not contain duplicates")
    return selected


def validate_rq1_task_set(document: object) -> dict[str, Any]:
    root = dict(_mapping(document, "RQ1 task set"))
    allowed_root_fields = {
        "schema_version",
        "protocol_version",
        "task_set_id",
        "frozen_at_utc",
        "tasks",
    }
    unknown_root_fields = set(root) - allowed_root_fields
    if unknown_root_fields:
        raise ValueError(
            f"RQ1 task set has unsupported fields: {sorted(unknown_root_fields)}"
        )
    if root.get("schema_version") != RQ1_TASK_SET_SCHEMA_VERSION:
        raise ValueError("unsupported RQ1 task-set schema_version")
    if root.get("protocol_version") != RQ1_PROTOCOL_VERSION:
        raise ValueError("unsupported RQ1 protocol_version")
    _nonblank(root.get("task_set_id"), "task_set_id")
    _nonblank(root.get("frozen_at_utc"), "frozen_at_utc")
    tasks = root.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("RQ1 task set requires a non-empty tasks list")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    allowed_fields = {
        "task_id",
        "task_version",
        "workload_family",
        "acceptable_candidate_ids",
        "preferred_candidate_id",
    }
    for index, raw in enumerate(tasks):
        item = dict(_mapping(raw, f"tasks[{index}]"))
        unknown = set(item) - allowed_fields
        if unknown:
            raise ValueError(f"tasks[{index}] has unsupported fields: {sorted(unknown)}")
        task_id = _nonblank(item.get("task_id"), f"tasks[{index}].task_id")
        if task_id in seen:
            raise ValueError("RQ1 task IDs must be unique")
        seen.add(task_id)
        acceptable = _string_list(
            item.get("acceptable_candidate_ids"),
            f"tasks[{index}].acceptable_candidate_ids",
        )
        preferred = item.get("preferred_candidate_id")
        if preferred is not None and preferred not in acceptable:
            raise ValueError("preferred_candidate_id must belong to acceptable_candidate_ids")
        normalized.append(
            {
                "task_id": task_id,
                "task_version": _nonblank(
                    item.get("task_version"), f"tasks[{index}].task_version"
                ),
                "workload_family": _nonblank(
                    item.get("workload_family"), f"tasks[{index}].workload_family"
                ),
                "acceptable_candidate_ids": acceptable,
                "preferred_candidate_id": preferred,
            }
        )
    return {**root, "tasks": normalized}


def validate_rq1_event(
    record: object, *, p3_gate_status: str
) -> dict[str, Any]:
    event = dict(_mapping(record, "RQ1 event"))
    unknown = set(event) - _EVENT_FIELDS
    missing = _EVENT_FIELDS - set(event)
    if unknown or missing:
        raise ValueError(
            f"RQ1 event fields differ from the schema; missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}"
        )
    if event["schema_version"] != RQ1_EVENT_SCHEMA_VERSION:
        raise ValueError("unsupported RQ1 event schema_version")
    if event["protocol_version"] != RQ1_PROTOCOL_VERSION:
        raise ValueError("unsupported RQ1 event protocol_version")
    for field in ("study_id", "participant_id", "session_id", "task_id"):
        event[field] = _nonblank(event[field], field)
    system_id = validate_primary_system_id(event["system_id"])
    if system_id not in active_primary_system_ids(p3_gate_status):
        raise ValueError(f"{system_id} is not active under the recorded P3 gate")
    index = event["event_index"]
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("event_index must be a non-negative integer")
    elapsed = event["elapsed_seconds"]
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(float(elapsed))
        or float(elapsed) < 0
    ):
        raise ValueError("elapsed_seconds must be finite and non-negative")
    event_type = event["event_type"]
    if event_type not in RQ1_EVENT_TYPES:
        raise ValueError(f"unsupported RQ1 event_type {event_type!r}")
    if system_id == "B0" and event_type.startswith("recommendation_"):
        raise ValueError("B0 cannot emit recommendation events")
    candidate_id = event["candidate_id"]
    if event_type in _CANDIDATE_REQUIRED_EVENTS:
        event["candidate_id"] = _nonblank(candidate_id, "candidate_id")
    elif candidate_id is not None:
        event["candidate_id"] = _nonblank(candidate_id, "candidate_id")
    return event


def validate_rq1_events(
    records: Sequence[object], *, p3_gate_status: str
) -> list[dict[str, Any]]:
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise ValueError("RQ1 events must be a sequence")
    if not records:
        raise ValueError("RQ1 events must not be empty")
    return [
        validate_rq1_event(record, p3_gate_status=p3_gate_status)
        for record in records
    ]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{number} must contain a JSON object")
        records.append(value)
    return records


__all__ = [
    "RQ1_EVENT_SCHEMA_VERSION",
    "RQ1_EVENT_TYPES",
    "RQ1_PROTOCOL_VERSION",
    "RQ1_TASK_SET_SCHEMA_VERSION",
    "read_json",
    "read_jsonl",
    "validate_rq1_event",
    "validate_rq1_events",
    "validate_rq1_task_set",
]
