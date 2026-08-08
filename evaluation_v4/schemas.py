"""Strict record contracts for protocol-v4 evidence streams."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping


PREDICTION_SCHEMA = "recommendation-prediction-v4.0.0"
SYSTEM_SCHEMA = "system-trial-v4.0.0"
USER_SCHEMA = "user-decision-v4.0.0"
REPROVISION_SCHEMA = "reprovision-trial-v4.0.0"
EVIDENCE_CLASSES = {"observed", "simulated", "replay"}

PREDICTION_FIELDS = {
    "schema_version",
    "run_id",
    "timestamp_utc",
    "dataset_id",
    "dataset_sha256",
    "git_commit",
    "sample_id",
    "workload_family",
    "split",
    "recommender",
    "repeat_index",
    "random_seed",
    "requested_backend",
    "effective_backend",
    "backend_version",
    "model_id",
    "raw_profile",
    "predicted_profile",
    "applied_profile",
    "predicted_image_id",
    "policy_compliant",
    "fallback_used",
    "fallback_error_category",
    "attempt_count",
    "latency_seconds",
    "error_category",
    "execution_mode",
}

SYSTEM_FIELDS = {
    "schema_version",
    "evidence_class",
    "trial_id",
    "experiment_id",
    "timestamp_utc",
    "git_commit",
    "environment_id",
    "recommender",
    "sample_id",
    "workload_family",
    "repeat_index",
    "applied_profile",
    "applied_image_id",
    "cpu_request_m",
    "memory_request_mib",
    "cpu_usage_mean_m",
    "memory_usage_mean_mib",
    "memory_usage_peak_mib",
    "measurement_window_seconds",
    "measurement_source",
    "pod_ready",
    "pending_failure",
    "pending_duration_seconds",
    "oom_killed",
    "image_pull_failure",
    "workload_success",
    "time_to_ready_seconds",
    "workload_duration_seconds",
    "cleanup_status",
    "supporting_evidence_paths",
}

USER_FIELDS = {
    "schema_version",
    "evidence_class",
    "event_id",
    "study_id",
    "timestamp_utc",
    "participant_block_id",
    "session_index",
    "recommender",
    "sample_id",
    "workload_family",
    "recommended_profile",
    "recommended_image_id",
    "action",
    "applied_profile",
    "applied_image_id",
    "explanation_seen",
    "decision_time_seconds",
    "task_success",
    "consent_version",
}

REPROVISION_FIELDS = {
    "schema_version",
    "evidence_class",
    "trial_id",
    "experiment_id",
    "timestamp_utc",
    "git_commit",
    "environment_id",
    "recommender",
    "sample_id",
    "workload_family",
    "repeat_index",
    "from_profile",
    "to_profile",
    "from_image_id",
    "to_image_id",
    "outcome",
    "replacement_ready",
    "pvc_continuity_verified",
    "workload_resume_verified",
    "pending_failure",
    "oom_killed",
    "downtime_seconds",
    "rollback_attempted",
    "rollback_successful",
    "cleanup_status",
    "supporting_evidence_paths",
}


def _exact_fields(record: Mapping[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(record))
    extra = sorted(set(record) - expected)
    if missing:
        raise ValueError(f"{label} missing fields: {', '.join(missing)}")
    if extra:
        raise ValueError(f"{label} unexpected fields: {', '.join(extra)}")


def _nonblank(record: Mapping[str, Any], field: str, label: str) -> None:
    if not isinstance(record.get(field), str) or not str(record[field]).strip():
        raise ValueError(f"{label}.{field} must be a non-blank string")


def _bool_or_none(record: Mapping[str, Any], field: str, label: str) -> None:
    if record.get(field) is not None and not isinstance(record[field], bool):
        raise ValueError(f"{label}.{field} must be boolean or null")


def _number_or_none(
    record: Mapping[str, Any],
    field: str,
    label: str,
    *,
    minimum: float | None = None,
) -> None:
    value = record.get(field)
    if value is None:
        return
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{label}.{field} must be a finite number or null")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label}.{field} must be >= {minimum}")


def _list_of_strings(record: Mapping[str, Any], field: str, label: str) -> None:
    value = record.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label}.{field} must be a list of strings")


def validate_prediction(record: Mapping[str, Any]) -> dict[str, Any]:
    label = "prediction"
    _exact_fields(record, PREDICTION_FIELDS, label)
    if record.get("schema_version") != PREDICTION_SCHEMA:
        raise ValueError("prediction schema_version is unsupported")
    for field in (
        "run_id",
        "timestamp_utc",
        "dataset_id",
        "dataset_sha256",
        "git_commit",
        "sample_id",
        "workload_family",
        "split",
        "recommender",
        "requested_backend",
        "effective_backend",
        "backend_version",
        "execution_mode",
    ):
        _nonblank(record, field, label)
    if record["split"] not in {"development", "test"}:
        raise ValueError("prediction.split is unsupported")
    for field in ("repeat_index", "random_seed", "attempt_count"):
        value = record.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"prediction.{field} must be a non-negative integer")
    for field in ("policy_compliant", "fallback_used"):
        if not isinstance(record.get(field), bool):
            raise ValueError(f"prediction.{field} must be boolean")
    _number_or_none(record, "latency_seconds", label, minimum=0)
    for field in (
        "model_id",
        "raw_profile",
        "predicted_profile",
        "applied_profile",
        "predicted_image_id",
        "fallback_error_category",
        "error_category",
    ):
        if record.get(field) is not None and not isinstance(record[field], str):
            raise ValueError(f"prediction.{field} must be string or null")
    return dict(record)


def _validate_evidence_header(record: Mapping[str, Any], label: str) -> None:
    if record.get("evidence_class") not in EVIDENCE_CLASSES:
        raise ValueError(f"{label}.evidence_class is unsupported")
    for field in ("timestamp_utc", "recommender", "sample_id", "workload_family"):
        _nonblank(record, field, label)


def validate_system_trial(record: Mapping[str, Any]) -> dict[str, Any]:
    label = "system_trial"
    _exact_fields(record, SYSTEM_FIELDS, label)
    if record.get("schema_version") != SYSTEM_SCHEMA:
        raise ValueError("system trial schema_version is unsupported")
    _validate_evidence_header(record, label)
    for field in ("trial_id", "experiment_id", "git_commit", "environment_id", "measurement_source", "cleanup_status"):
        _nonblank(record, field, label)
    if record.get("applied_profile") not in {"small", "medium", "large"}:
        raise ValueError("system_trial.applied_profile is unsupported")
    _nonblank(record, "applied_image_id", label)
    if not isinstance(record.get("repeat_index"), int) or record["repeat_index"] < 0:
        raise ValueError("system_trial.repeat_index must be non-negative")
    for field in (
        "cpu_request_m",
        "memory_request_mib",
        "cpu_usage_mean_m",
        "memory_usage_mean_mib",
        "memory_usage_peak_mib",
        "measurement_window_seconds",
        "pending_duration_seconds",
        "time_to_ready_seconds",
        "workload_duration_seconds",
    ):
        _number_or_none(record, field, label, minimum=0)
    if not isinstance(record.get("cpu_request_m"), (int, float)) or record["cpu_request_m"] <= 0:
        raise ValueError("system_trial.cpu_request_m must be positive")
    if not isinstance(record.get("memory_request_mib"), (int, float)) or record["memory_request_mib"] <= 0:
        raise ValueError("system_trial.memory_request_mib must be positive")
    for field in ("pod_ready", "pending_failure", "oom_killed", "image_pull_failure", "workload_success"):
        if not isinstance(record.get(field), bool):
            raise ValueError(f"system_trial.{field} must be boolean")
    _list_of_strings(record, "supporting_evidence_paths", label)
    if record["evidence_class"] == "observed" and not record["supporting_evidence_paths"]:
        raise ValueError("observed system trials require supporting evidence paths")
    return dict(record)


def validate_user_event(record: Mapping[str, Any]) -> dict[str, Any]:
    label = "user_event"
    _exact_fields(record, USER_FIELDS, label)
    if record.get("schema_version") != USER_SCHEMA:
        raise ValueError("user event schema_version is unsupported")
    _validate_evidence_header(record, label)
    for field in ("event_id", "study_id", "participant_block_id", "consent_version"):
        _nonblank(record, field, label)
    if not isinstance(record.get("session_index"), int) or record["session_index"] < 0:
        raise ValueError("user_event.session_index must be non-negative")
    if record.get("action") not in {"accept", "override", "cancel"}:
        raise ValueError("user_event.action is unsupported")
    for field in ("recommended_profile", "applied_profile"):
        if record.get(field) is not None and record[field] not in {"small", "medium", "large"}:
            raise ValueError(f"user_event.{field} is unsupported")
    for field in ("recommended_image_id", "applied_image_id"):
        if record.get(field) is not None and not isinstance(record[field], str):
            raise ValueError(f"user_event.{field} must be string or null")
    for field in ("explanation_seen", "task_success"):
        _bool_or_none(record, field, label)
    _number_or_none(record, "decision_time_seconds", label, minimum=0)
    if record["action"] == "cancel" and (record["applied_profile"] is not None or record["applied_image_id"] is not None):
        raise ValueError("cancelled user events cannot have an applied decision")
    if record["evidence_class"] == "observed" and not record["consent_version"].strip():
        raise ValueError("observed user events require consent_version")
    return dict(record)


def validate_reprovision_trial(record: Mapping[str, Any]) -> dict[str, Any]:
    label = "reprovision_trial"
    _exact_fields(record, REPROVISION_FIELDS, label)
    if record.get("schema_version") != REPROVISION_SCHEMA:
        raise ValueError("reprovision trial schema_version is unsupported")
    _validate_evidence_header(record, label)
    for field in ("trial_id", "experiment_id", "git_commit", "environment_id", "from_image_id", "to_image_id", "cleanup_status"):
        _nonblank(record, field, label)
    for field in ("from_profile", "to_profile"):
        if record.get(field) not in {"small", "medium", "large"}:
            raise ValueError(f"reprovision_trial.{field} is unsupported")
    if record.get("outcome") not in {"completed", "rolled_back", "failed_pre_stop", "failed_after_stop"}:
        raise ValueError("reprovision_trial.outcome is unsupported")
    if not isinstance(record.get("repeat_index"), int) or record["repeat_index"] < 0:
        raise ValueError("reprovision_trial.repeat_index must be non-negative")
    for field in (
        "replacement_ready",
        "pvc_continuity_verified",
        "workload_resume_verified",
        "pending_failure",
        "oom_killed",
        "rollback_attempted",
        "rollback_successful",
    ):
        if not isinstance(record.get(field), bool):
            raise ValueError(f"reprovision_trial.{field} must be boolean")
    _number_or_none(record, "downtime_seconds", label, minimum=0)
    _list_of_strings(record, "supporting_evidence_paths", label)
    if record["evidence_class"] == "observed" and not record["supporting_evidence_paths"]:
        raise ValueError("observed re-provision trials require supporting evidence paths")
    if record["outcome"] == "completed" and not record["replacement_ready"]:
        raise ValueError("completed re-provision trials require replacement_ready")
    if record["rollback_successful"] and not record["rollback_attempted"]:
        raise ValueError("rollback_successful requires rollback_attempted")
    return dict(record)


def read_jsonl(path: Path, validator: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(value, Mapping):
                raise ValueError(f"{path}:{line_number}: record must be an object")
            try:
                records.append(validator(value))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    if not records:
        raise ValueError(f"{path} contains no records")
    return records


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


__all__ = [
    "EVIDENCE_CLASSES",
    "PREDICTION_SCHEMA",
    "REPROVISION_SCHEMA",
    "SYSTEM_SCHEMA",
    "USER_SCHEMA",
    "read_jsonl",
    "validate_prediction",
    "validate_reprovision_trial",
    "validate_system_trial",
    "validate_user_event",
    "write_jsonl",
]
