"""Versioned result schema and validation helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any


SCHEMA_VERSION = "1.0.0"

METHODS = ("static_manual", "intent_only", "context_aware")

PROFILE_RESOURCES = {
    "small": {
        "cpu_request_m": 100,
        "cpu_limit_m": 500,
        "memory_request_mi": 244,
        "memory_limit_mi": 366,
    },
    "medium": {
        "cpu_request_m": 500,
        "cpu_limit_m": 1000,
        "memory_request_mi": 732,
        "memory_limit_mi": 953,
    },
    "large": {
        "cpu_request_m": 1500,
        "cpu_limit_m": 2000,
        "memory_request_mi": 1464,
        "memory_limit_mi": 1907,
    },
}

REQUIRED_FIELDS = (
    "schema_version",
    "run_id",
    "timestamp",
    "git_commit",
    "environment_id",
    "method",
    "workload_id",
    "repeat_index",
    "random_seed",
    "input_intent",
    "dataset_size_hint_gb",
    "context_signal_summary",
    "recommended_profile",
    "applied_profile",
    "recommendation_reasons",
    "policy_warnings",
    "cpu_request_m",
    "cpu_limit_m",
    "memory_request_mi",
    "memory_limit_mi",
    "peak_cpu_m",
    "peak_memory_mi",
    "resource_measurement_source",
    "pod_pending_duration_seconds",
    "workload_runtime_seconds",
    "time_to_success_seconds",
    "oom_killed",
    "exit_reason",
    "exit_code",
    "restart_or_respawn_count",
    "success",
    "timeout",
    "cleanup_status",
    "error_message",
    "supporting_log_paths",
    "kubernetes_evidence",
)

LIST_FIELDS = {
    "recommendation_reasons",
    "policy_warnings",
    "supporting_log_paths",
}

DICT_FIELDS = {
    "context_signal_summary",
    "kubernetes_evidence",
}

BOOL_FIELDS = {"oom_killed", "success", "timeout"}

NUMBER_OR_NULL_FIELDS = {
    "dataset_size_hint_gb",
    "cpu_request_m",
    "cpu_limit_m",
    "memory_request_mi",
    "memory_limit_mi",
    "peak_cpu_m",
    "peak_memory_mi",
    "pod_pending_duration_seconds",
    "workload_runtime_seconds",
    "time_to_success_seconds",
}

INT_OR_NULL_FIELDS = {
    "repeat_index",
    "random_seed",
    "exit_code",
    "restart_or_respawn_count",
}

CSV_FIELDS = REQUIRED_FIELDS

JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://example.invalid/intent-spawner/result-schema/1.0.0",
    "title": "Intent Spawner Experiment Result",
    "type": "object",
    "additionalProperties": False,
    "required": list(REQUIRED_FIELDS),
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "run_id": {"type": "string", "minLength": 1},
        "timestamp": {"type": "string", "format": "date-time"},
        "git_commit": {"type": "string", "minLength": 1},
        "environment_id": {"type": "string", "minLength": 1},
        "method": {"type": "string", "enum": list(METHODS)},
        "workload_id": {"type": "string", "minLength": 1},
        "repeat_index": {"type": ["integer", "null"], "minimum": 0},
        "random_seed": {"type": ["integer", "null"]},
        "input_intent": {"type": ["string", "null"]},
        "dataset_size_hint_gb": {"type": ["number", "null"]},
        "context_signal_summary": {"type": "object"},
        "recommended_profile": {
            "type": ["string", "null"],
            "enum": ["small", "medium", "large", "gpu_or_large", None],
        },
        "applied_profile": {
            "type": ["string", "null"],
            "enum": ["small", "medium", "large", None],
        },
        "recommendation_reasons": {"type": "array", "items": {"type": "string"}},
        "policy_warnings": {"type": "array", "items": {"type": "string"}},
        "cpu_request_m": {"type": ["integer", "null"]},
        "cpu_limit_m": {"type": ["integer", "null"]},
        "memory_request_mi": {"type": ["integer", "null"]},
        "memory_limit_mi": {"type": ["integer", "null"]},
        "peak_cpu_m": {"type": ["number", "null"]},
        "peak_memory_mi": {"type": ["number", "null"]},
        "resource_measurement_source": {"type": "string", "minLength": 1},
        "pod_pending_duration_seconds": {"type": ["number", "null"]},
        "workload_runtime_seconds": {"type": ["number", "null"]},
        "time_to_success_seconds": {"type": ["number", "null"]},
        "oom_killed": {"type": ["boolean", "null"]},
        "exit_reason": {"type": ["string", "null"]},
        "exit_code": {"type": ["integer", "null"]},
        "restart_or_respawn_count": {"type": ["integer", "null"], "minimum": 0},
        "success": {"type": ["boolean", "null"]},
        "timeout": {"type": ["boolean", "null"]},
        "cleanup_status": {"type": "string", "minLength": 1},
        "error_message": {"type": ["string", "null"]},
        "supporting_log_paths": {"type": "array", "items": {"type": "string"}},
        "kubernetes_evidence": {"type": "object"},
    },
}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_run_id(workload_id: str) -> str:
    timestamp = now_utc_iso().replace(":", "").replace("-", "")
    return f"{timestamp}-{workload_id}"


def current_git_commit(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def empty_record() -> dict[str, Any]:
    record: dict[str, Any] = {field: None for field in REQUIRED_FIELDS}
    record["schema_version"] = SCHEMA_VERSION
    record["recommendation_reasons"] = []
    record["policy_warnings"] = []
    record["context_signal_summary"] = {}
    record["resource_measurement_source"] = "not_available"
    record["timeout"] = False
    record["cleanup_status"] = "not_started"
    record["supporting_log_paths"] = []
    record["kubernetes_evidence"] = {}
    return record


def validate_record(record: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_FIELDS if field not in record]
    if missing:
        raise ValueError(f"missing required result fields: {', '.join(missing)}")

    extra = sorted(set(record) - set(REQUIRED_FIELDS))
    if extra:
        raise ValueError(f"unexpected result fields: {', '.join(extra)}")

    if record["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {record['schema_version']!r}")

    if record["method"] not in METHODS:
        raise ValueError(f"unsupported method {record['method']!r}")

    for field in LIST_FIELDS:
        if not isinstance(record[field], list):
            raise ValueError(f"{field} must be a list")

    for field in DICT_FIELDS:
        if not isinstance(record[field], dict):
            raise ValueError(f"{field} must be an object")

    for field in BOOL_FIELDS:
        if record[field] is not None and not isinstance(record[field], bool):
            raise ValueError(f"{field} must be a boolean or null")

    for field in NUMBER_OR_NULL_FIELDS:
        if record[field] is not None and not isinstance(record[field], (int, float)):
            raise ValueError(f"{field} must be a number or null")

    for field in INT_OR_NULL_FIELDS:
        if record[field] is not None and not isinstance(record[field], int):
            raise ValueError(f"{field} must be an integer or null")

    if record["recommended_profile"] not in (None, "small", "medium", "large", "gpu_or_large"):
        raise ValueError("recommended_profile must be small, medium, large, gpu_or_large, or null")

    if record["applied_profile"] not in (None, "small", "medium", "large"):
        raise ValueError("applied_profile must be small, medium, large, or null")


def write_json_schema(path: Path) -> None:
    path.write_text(json.dumps(JSON_SCHEMA, indent=2, sort_keys=True) + "\n", encoding="utf-8")
