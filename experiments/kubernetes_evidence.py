"""Kubernetes evidence parsing and sanitization."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Any


SAFE_ENV_NAMES = {
    "CONTEXT_DATASET_SIZE_GB",
    "RECOMMENDED_PROFILE",
    "RECOMMENDATION_REASONS",
    "SELECTED_STATIC_PROFILE",
}
SAFE_ANNOTATION_PREFIXES = ("z2jh-context-demo.local/",)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_cpu_m(value: Any) -> int | None:
    if value in (None, ""):
        return None
    text = str(value)
    if text.endswith("m"):
        return int(float(text[:-1]))
    return int(float(text) * 1000)


def parse_memory_mi(value: Any) -> int | None:
    if value in (None, ""):
        return None
    text = str(value)
    units = (
        ("Ki", 1 / 1024),
        ("Mi", 1),
        ("Gi", 1024),
        ("Ti", 1024 * 1024),
        ("K", 1000 / 1024 / 1024),
        ("M", 1000 * 1000 / 1024 / 1024),
        ("G", 1000 * 1000 * 1000 / 1024 / 1024),
    )
    for suffix, factor in units:
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * factor)
    return int(float(text) / 1024 / 1024)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _seconds_between(start: str | None, end: str | None) -> float | None:
    started = _parse_timestamp(start)
    ended = _parse_timestamp(end)
    if started is None or ended is None:
        return None
    return max(0.0, round((ended - started).total_seconds(), 6))


def _safe_metadata_name(value: str | None) -> str | None:
    if value is None:
        return None
    if re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,62}", value):
        return value
    return "redacted"


def _safe_annotations(annotations: dict[str, Any] | None) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in (annotations or {}).items():
        if key.startswith(SAFE_ANNOTATION_PREFIXES):
            safe[key] = str(value)
    return safe


def _safe_env(containers: list[dict[str, Any]]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for container in containers:
        for entry in container.get("env", []) or []:
            name = entry.get("name")
            if name in SAFE_ENV_NAMES and "value" in entry:
                safe[name] = str(entry["value"])
    return safe


def _resources_from_container(container: dict[str, Any]) -> dict[str, int | None]:
    resources = container.get("resources", {})
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})
    return {
        "cpu_request_m": parse_cpu_m(requests.get("cpu")),
        "cpu_limit_m": parse_cpu_m(limits.get("cpu")),
        "memory_request_mi": parse_memory_mi(requests.get("memory")),
        "memory_limit_mi": parse_memory_mi(limits.get("memory")),
    }


def _termination_from_statuses(statuses: list[dict[str, Any]]) -> dict[str, Any]:
    for status in statuses:
        state = status.get("state", {})
        last_state = status.get("lastState", {})
        terminated = state.get("terminated") or last_state.get("terminated")
        if terminated:
            return {
                "container_name": status.get("name"),
                "reason": terminated.get("reason"),
                "exit_code": terminated.get("exitCode"),
                "started_at": terminated.get("startedAt"),
                "finished_at": terminated.get("finishedAt"),
            }
    return {
        "container_name": None,
        "reason": None,
        "exit_code": None,
        "started_at": None,
        "finished_at": None,
    }


def _phase_transitions(pod: dict[str, Any]) -> list[dict[str, Any]]:
    transitions = []
    for condition in pod.get("status", {}).get("conditions", []) or []:
        transitions.append(
            {
                "condition": condition.get("type"),
                "status": condition.get("status"),
                "last_transition_time": condition.get("lastTransitionTime"),
                "reason": condition.get("reason"),
                "message": condition.get("message"),
            }
        )
    return transitions


def _events(events_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    items = [] if events_json is None else events_json.get("items", [])
    events = []
    for item in items:
        events.append(
            {
                "type": item.get("type"),
                "reason": item.get("reason"),
                "message": item.get("message"),
                "count": item.get("count"),
                "first_timestamp": item.get("firstTimestamp") or item.get("eventTime"),
                "last_timestamp": item.get("lastTimestamp") or item.get("eventTime"),
            }
        )
    return events


def _pending_reasons(pod: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for condition in pod.get("status", {}).get("conditions", []) or []:
        if (
            condition.get("type") == "PodScheduled"
            and condition.get("status") == "False"
            and condition.get("reason")
        ):
            reasons.append(str(condition["reason"]))
    for event in events:
        if event.get("reason") in {"FailedScheduling", "NotTriggerScaleUp", "FailedMount"}:
            message = event.get("message")
            reasons.append(f"{event['reason']}: {message}" if message else str(event["reason"]))
    return reasons


def extract_pod_evidence(
    pod_json: dict[str, Any] | None,
    events_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if pod_json is None:
        return {}

    metadata = pod_json.get("metadata", {})
    spec = pod_json.get("spec", {})
    status = pod_json.get("status", {})
    containers = spec.get("containers", []) or []
    container_statuses = status.get("containerStatuses", []) or []
    resources = _resources_from_container(containers[0]) if containers else {}
    termination = _termination_from_statuses(container_statuses)
    events = _events(events_json)
    creation_time = metadata.get("creationTimestamp")
    scheduled_time = None
    for condition in status.get("conditions", []) or []:
        if condition.get("type") == "PodScheduled" and condition.get("status") == "True":
            scheduled_time = condition.get("lastTransitionTime")
            break

    return {
        "pod_name": _safe_metadata_name(metadata.get("name")),
        "namespace": _safe_metadata_name(metadata.get("namespace")),
        "phase": status.get("phase"),
        "phase_transitions": _phase_transitions(pod_json),
        "events": events,
        "termination_reason": termination["reason"],
        "termination_exit_code": termination["exit_code"],
        "restart_count": sum(int(item.get("restartCount", 0)) for item in container_statuses),
        "requests_limits": resources,
        "annotations": _safe_annotations(metadata.get("annotations")),
        "environment_variables": _safe_env(containers),
        "scheduling_or_pending_reasons": _pending_reasons(pod_json, events),
        "pod_pending_duration_seconds": _seconds_between(creation_time, scheduled_time),
        "workload_runtime_seconds": _seconds_between(termination["started_at"], termination["finished_at"]),
        "time_to_success_seconds": (
            _seconds_between(creation_time, termination["finished_at"])
            if status.get("phase") == "Succeeded" and termination["exit_code"] == 0
            else None
        ),
        "oom_killed": termination["reason"] == "OOMKilled" or termination["exit_code"] == 137,
    }


def extract_metric_peaks(metrics_json: dict[str, Any] | None) -> dict[str, Any]:
    if metrics_json is None:
        return {
            "peak_cpu_m": None,
            "peak_memory_mi": None,
            "resource_measurement_source": "not_available",
        }

    snapshots = metrics_json.get("snapshots", [])
    source = str(metrics_json.get("source") or "metrics_server")
    cpu_values: list[int] = []
    memory_values: list[int] = []
    for snapshot in snapshots:
        for container in snapshot.get("containers", []) or []:
            cpu = parse_cpu_m(container.get("cpu"))
            memory = parse_memory_mi(container.get("memory"))
            if cpu is not None:
                cpu_values.append(cpu)
            if memory is not None:
                memory_values.append(memory)

    return {
        "peak_cpu_m": max(cpu_values) if cpu_values else None,
        "peak_memory_mi": max(memory_values) if memory_values else None,
        "resource_measurement_source": source if cpu_values or memory_values else "not_available",
    }


def _run_kubectl(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _write_artifact(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _parse_top_pod_output(output: str) -> list[dict[str, str]]:
    containers = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            containers.append({"pod": parts[0], "container": parts[1], "cpu": parts[2], "memory": parts[3]})
        elif len(parts) >= 3:
            containers.append({"pod": parts[0], "container": None, "cpu": parts[1], "memory": parts[2]})
    return containers


def collect_kubernetes_artifacts(
    *,
    namespace: str,
    pod_name: str,
    artifact_dir: Path,
    metrics_samples: int = 1,
    metrics_interval_seconds: float = 1.0,
) -> dict[str, Path | None]:
    """Collect read-only Kubernetes evidence for one pod.

    This function does not create, delete, or mutate cluster resources. If the
    Metrics API is unavailable, it writes the error text and returns no metrics
    JSON path so normalized peak measurements remain null.
    """

    artifacts: dict[str, Path | None] = {
        "pod_json": None,
        "pod_unavailable": None,
        "events_json": None,
        "events_unavailable": None,
        "logs": None,
        "metrics_json": None,
        "metrics_unavailable": None,
    }

    pod = _run_kubectl(["get", "pod", pod_name, "-n", namespace, "-o", "json"])
    if pod.returncode == 0:
        artifacts["pod_json"] = _write_artifact(artifact_dir / "pod.json", pod.stdout)
    else:
        artifacts["pod_unavailable"] = _write_artifact(artifact_dir / "pod_unavailable.txt", pod.stderr or pod.stdout)

    events = _run_kubectl(
        [
            "get",
            "events",
            "-n",
            namespace,
            "--field-selector",
            f"involvedObject.name={pod_name}",
            "-o",
            "json",
        ]
    )
    if events.returncode == 0:
        artifacts["events_json"] = _write_artifact(artifact_dir / "events.json", events.stdout)
    else:
        artifacts["events_unavailable"] = _write_artifact(
            artifact_dir / "events_unavailable.txt",
            events.stderr or events.stdout,
        )

    logs = _run_kubectl(["logs", pod_name, "-n", namespace, "--all-containers=true"])
    artifacts["logs"] = _write_artifact(artifact_dir / "pod.log", logs.stdout or logs.stderr)

    snapshots = []
    metrics_errors = []
    for index in range(max(0, metrics_samples)):
        top = _run_kubectl(["top", "pod", pod_name, "-n", namespace, "--containers", "--no-headers"])
        if top.returncode == 0:
            snapshots.append(
                {
                    "sample_index": index,
                    "timestamp": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "containers": _parse_top_pod_output(top.stdout),
                }
            )
        else:
            metrics_errors.append(top.stderr.strip() or top.stdout.strip() or "kubectl top failed")
            break
        if index + 1 < metrics_samples:
            time.sleep(metrics_interval_seconds)

    if snapshots:
        payload = {"source": "metrics_server", "snapshots": snapshots}
        artifacts["metrics_json"] = _write_artifact(
            artifact_dir / "metrics_snapshots.json",
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )
    elif metrics_errors:
        artifacts["metrics_unavailable"] = _write_artifact(
            artifact_dir / "metrics_unavailable.txt",
            "\n".join(metrics_errors) + "\n",
        )

    return artifacts
