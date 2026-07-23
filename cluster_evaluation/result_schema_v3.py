"""Validation for append-only v3 Kubernetes experiment records."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any


SCHEMA_VERSION = "3.0.0"
PROTOCOL_VERSION = "3.0.0"
EXPERIMENT_PATHS = {"direct_pod", "jupyterhub_e2e"}
EVALUATION_SETS = {"calibration", "holdout_core", "holdout_robustness"}
FAILURE_CATEGORIES = {
    "success",
    "oom_killed",
    "timeout",
    "scheduler_failure",
    "infrastructure_failure",
    "validation_failure",
    "harness_failure",
    "workload_failure",
}
IMAGE_RE = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?/"
    r"[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$"
)
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

REQUIRED_FIELDS = (
    "schema_version",
    "protocol_version",
    "experiment_path",
    "experiment_kind",
    "run_id",
    "plan_index",
    "evaluation_set",
    "workload_id",
    "repeat_index",
    "random_seed",
    "method",
    "recommended_profile",
    "applied_profile",
    "observed_profile",
    "recommendation_reasons",
    "context_signal_summary",
    "cpu_request_m",
    "cpu_limit_m",
    "memory_request_mi",
    "memory_limit_mi",
    "target_cgroup_mib",
    "target_band_mib",
    "actual_cgroup_peak_mib",
    "useful_allocation_bytes",
    "pressure_padding_bytes",
    "hold_seconds",
    "cpu_full_window_average_m",
    "cpu_interval_sample_max_m",
    "cpu_nr_periods_delta",
    "cpu_nr_throttled_delta",
    "cpu_throttled_usec_delta",
    "cgroup_sample_interval_seconds",
    "cgroup_sample_count",
    "benchmark_runtime_seconds",
    "pod_pending_duration_seconds",
    "spawn_latency_seconds",
    "time_to_outcome_seconds",
    "phase",
    "exit_code",
    "exit_reason",
    "oom_killed",
    "timeout",
    "restart_count",
    "checksum",
    "success",
    "infrastructure_invalid",
    "exclusion_reason",
    "replacement_run_id",
    "cleanup_status",
    "failure_category",
    "git_commit",
    "container_image",
    "configuration_identity",
    "input_sha256",
    "supporting_evidence_sha256",
    "supporting_log_paths",
    "timestamp_created",
    "timestamp_recorded",
)


def validate_record(record: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_FIELDS if field not in record]
    extra = sorted(set(record) - set(REQUIRED_FIELDS))
    if missing:
        raise ValueError(f"v3 record is missing fields: {missing}")
    if extra:
        raise ValueError(f"v3 record contains undeclared fields: {extra}")
    if record["schema_version"] != SCHEMA_VERSION:
        raise ValueError("wrong v3 schema version")
    if record["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError("wrong v3 protocol version")
    if record["experiment_path"] not in EXPERIMENT_PATHS:
        raise ValueError("invalid experiment_path")
    if record["experiment_kind"] not in {
        "calibration",
        "ground-truth",
        "comparative",
        "jupyterhub",
    }:
        raise ValueError("invalid experiment_kind")
    if record["experiment_path"] == "jupyterhub_e2e" and record[
        "experiment_kind"
    ] != "jupyterhub":
        raise ValueError("JupyterHub records require experiment_kind=jupyterhub")
    if record["experiment_path"] == "direct_pod" and record[
        "experiment_kind"
    ] == "jupyterhub":
        raise ValueError("direct-pod records cannot use experiment_kind=jupyterhub")
    if record["evaluation_set"] not in EVALUATION_SETS:
        raise ValueError("invalid evaluation_set")
    if record["applied_profile"] not in {"small", "medium", "large"}:
        raise ValueError("invalid applied_profile")
    if record["observed_profile"] not in {None, "small", "medium", "large"}:
        raise ValueError("invalid observed_profile")
    if record["repeat_index"] < 0 or record["plan_index"] < 0:
        raise ValueError("indices must be non-negative")
    if not isinstance(record["run_id"], str) or not record["run_id"]:
        raise ValueError("run_id must be a non-empty string")
    if not isinstance(record["workload_id"], str) or not record["workload_id"]:
        raise ValueError("workload_id must be a non-empty string")
    if not isinstance(record["random_seed"], int) or isinstance(
        record["random_seed"], bool
    ):
        raise ValueError("random_seed must be an integer")
    if not isinstance(record["target_band_mib"], list) or len(record["target_band_mib"]) != 2:
        raise ValueError("target_band_mib must be a two-value list")
    if record["target_cgroup_mib"] > 1700:
        raise ValueError("target exceeds v3 safety cap")
    if record["target_cgroup_mib"] < 0:
        raise ValueError("target cannot be negative")
    for name in (
        "oom_killed",
        "timeout",
        "success",
        "infrastructure_invalid",
    ):
        if not isinstance(record[name], bool):
            raise ValueError(f"{name} must be boolean")
    if record["infrastructure_invalid"] and not record["exclusion_reason"]:
        raise ValueError("infrastructure-invalid records require an exclusion reason")
    if not record["infrastructure_invalid"] and record["exclusion_reason"] is not None:
        raise ValueError("valid records cannot carry an exclusion reason")
    if not isinstance(record["recommendation_reasons"], list):
        raise ValueError("recommendation_reasons must be a list")
    if not isinstance(record["context_signal_summary"], dict):
        raise ValueError("context_signal_summary must be an object")
    if not isinstance(record["supporting_log_paths"], list):
        raise ValueError("supporting_log_paths must be a list")
    if any(str(path).startswith("/") for path in record["supporting_log_paths"]):
        raise ValueError("supporting evidence paths must be relative")
    if record["failure_category"] not in FAILURE_CATEGORIES:
        raise ValueError("invalid failure_category")
    if record["success"] != (record["failure_category"] == "success"):
        raise ValueError("success and failure_category disagree")
    if record["oom_killed"] and record["failure_category"] != "oom_killed":
        raise ValueError("OOM records require failure_category=oom_killed")
    if record["timeout"] and record["failure_category"] != "timeout":
        raise ValueError("timeout records require failure_category=timeout")
    if record["infrastructure_invalid"] and record["failure_category"] not in {
        "scheduler_failure",
        "infrastructure_failure",
        "harness_failure",
    }:
        raise ValueError("infrastructure-invalid record has the wrong failure category")
    if not isinstance(record["git_commit"], str) or not COMMIT_RE.fullmatch(
        record["git_commit"]
    ):
        raise ValueError("git_commit must be a full lowercase Git SHA")
    if not isinstance(record["container_image"], str) or not IMAGE_RE.fullmatch(
        record["container_image"]
    ):
        raise ValueError("container_image must be an immutable sha256 reference")
    if record["experiment_path"] == "jupyterhub_e2e":
        if not isinstance(record["configuration_identity"], str) or not record[
            "configuration_identity"
        ]:
            raise ValueError("JupyterHub records require configuration_identity")
    elif record["configuration_identity"] is not None:
        raise ValueError("direct-pod records cannot carry configuration_identity")
    if not isinstance(record["input_sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", record["input_sha256"]
    ):
        raise ValueError("input_sha256 must be a SHA-256 digest")
    checksums = record["supporting_evidence_sha256"]
    if not isinstance(checksums, dict) or any(
        not isinstance(path, str)
        or path.startswith("/")
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        for path, digest in checksums.items()
    ):
        raise ValueError("supporting_evidence_sha256 must map relative paths to SHA-256")
    for field in ("timestamp_created", "timestamp_recorded"):
        if not isinstance(record[field], str):
            raise ValueError(f"{field} must be a timestamp string")
        try:
            datetime.fromisoformat(record[field].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} is not an ISO-8601 timestamp") from exc
    created = datetime.fromisoformat(record["timestamp_created"].replace("Z", "+00:00"))
    recorded = datetime.fromisoformat(record["timestamp_recorded"].replace("Z", "+00:00"))
    if recorded < created:
        raise ValueError("timestamp_recorded precedes timestamp_created")
