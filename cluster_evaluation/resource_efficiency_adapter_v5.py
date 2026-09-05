"""Dedicated single-pod Kubernetes adapter for Protocol-v5 E4 efficiency trials."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import time
from typing import Any, Mapping

from cluster_evaluation.resource_adapter_v5 import (
    ADAPTER_MONITOR_GRACE_SECONDS, IMAGE_RE, NAMESPACE, POD_LIFECYCLE_GRACE_SECONDS,
    KubernetesTrialAdapter as CalibrationKubernetesAdapter, _cpu_m, _memory_mib,
    collect_read_only_preflight,
)
from evaluation_v5.resource.efficiency_models import EfficiencyTrialSpec, TRIAL_SCHEMA_VERSION, primary_outcome


ADAPTER_VERSION = "protocol-v5-resource-efficiency-kubernetes-adapter-v1.0.0"
LABEL = "app.kubernetes.io/name=intent-spawner-resource-efficiency-v5"
INFRASTRUCTURE_REASONS = {
    "Evicted", "NodeLost", "ImagePullBackOff", "ErrImagePull",
    "CreateContainerConfigError", "CreateContainerError",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _pod_name(trial_id: str) -> str:
    return "e4e-" + hashlib.sha256(trial_id.encode("utf-8")).hexdigest()[:24]


def _sanitize_reason(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.lower()
    for token, code in (
        ("failedscheduling", "FAILED_SCHEDULING"), ("unschedulable", "UNSCHEDULABLE"),
        ("exceeded quota", "QUOTA_REJECTED"), ("forbidden", "ADMISSION_FORBIDDEN"),
        ("insufficient cpu", "INSUFFICIENT_CPU"), ("insufficient memory", "INSUFFICIENT_MEMORY"),
        ("deadlineexceeded", "DEADLINE_EXCEEDED"),
    ):
        if token in lowered:
            return code
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_").upper()
    return cleaned[:96] or "UNKNOWN"


def build_pod_spec(spec: EfficiencyTrialSpec, image: str) -> dict[str, Any]:
    if not IMAGE_RE.fullmatch(image):
        raise ValueError("comparative E4 execution requires an immutable sha256 image reference")
    allocation = spec.allocation
    requests: dict[str, Any] = {"cpu": f"{allocation.cpu_request_m}m", "memory": f"{allocation.memory_request_mib}Mi"}
    limits: dict[str, Any] = {"cpu": f"{allocation.cpu_limit_m}m", "memory": f"{allocation.memory_limit_mib}Mi"}
    if allocation.gpu_count:
        requests[allocation.gpu_resource] = allocation.gpu_count
        limits[allocation.gpu_resource] = allocation.gpu_count
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {
            "name": _pod_name(spec.trial_id), "namespace": NAMESPACE,
            "labels": {"app.kubernetes.io/name": "intent-spawner-resource-efficiency-v5", "z2jh-context-demo.local/experiment-v5": "true"},
            "annotations": {
                "z2jh-context-demo.local/trial-id": spec.trial_id,
                "z2jh-context-demo.local/family-id": spec.family_id,
                "z2jh-context-demo.local/condition": spec.condition,
                "z2jh-context-demo.local/repetition": str(spec.repetition),
            },
        },
        "spec": {
            "restartPolicy": "Never", "activeDeadlineSeconds": spec.timeout_seconds + POD_LIFECYCLE_GRACE_SECONDS,
            "automountServiceAccountToken": False,
            "nodeSelector": {"z2jh-context-demo.local/node-identity": "e4-node-v1", "z2jh-context-demo.local/dedicated-e4": "true"},
            "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{
                "name": "workload", "image": image, "imagePullPolicy": "Never",
                "args": ["--family-id", spec.family_id, "--sample-interval", "0.1"],
                "resources": {"requests": requests, "limits": limits},
                "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}, "readOnlyRootFilesystem": True},
                "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
            }],
            "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}],
        },
    }


def _duration(started: str | None, finished: str | None) -> float | None:
    try:
        if not started or not finished:
            return None
        return (datetime.fromisoformat(finished.replace("Z", "+00:00")) - datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return None


def _observed_resources(pod: Mapping[str, Any] | None, allocation: Mapping[str, Any]) -> dict[str, Any] | None:
    resources = (((pod or {}).get("spec") or {}).get("containers") or [{}])[0].get("resources") or {}
    requests, limits = resources.get("requests") or {}, resources.get("limits") or {}
    cpu_request, cpu_limit = _cpu_m(requests.get("cpu")), _cpu_m(limits.get("cpu"))
    memory_request, memory_limit = _memory_mib(requests.get("memory")), _memory_mib(limits.get("memory"))
    if None in {cpu_request, cpu_limit, memory_request, memory_limit}:
        return None
    gpu_resource = allocation.get("gpu_resource")
    gpu_request = 0 if not gpu_resource else requests.get(gpu_resource)
    gpu_limit = 0 if not gpu_resource else limits.get(gpu_resource)
    if isinstance(gpu_request, str) and gpu_request.isdigit():
        gpu_request = int(gpu_request)
    if isinstance(gpu_limit, str) and gpu_limit.isdigit():
        gpu_limit = int(gpu_limit)
    if (
        isinstance(gpu_request, bool) or not isinstance(gpu_request, int)
        or isinstance(gpu_limit, bool) or not isinstance(gpu_limit, int)
        or gpu_request != gpu_limit
    ):
        return None
    return {"cpu_request_m": cpu_request, "cpu_limit_m": cpu_limit, "memory_request_mib": memory_request, "memory_limit_mib": memory_limit, "gpu_count": gpu_request, "gpu_resource": gpu_resource}


def classify_kubernetes_outcome(
    *, phase: str | None, pod_reason: str | None, terminated_reason: str | None,
    waiting_reason: str | None, condition_messages: str,
    cgroup_metrics: Mapping[str, Any], monitor_deadline_reached: bool,
    workload_runtime_seconds: Any, timeout_seconds: int,
) -> dict[str, Any]:
    """Classify independent Kubernetes signals before deterministic precedence."""

    reason = terminated_reason or waiting_reason or pod_reason
    infrastructure_reason = (
        f"KUBERNETES_{reason.upper()}" if reason in INFRASTRUCTURE_REASONS else None
    )
    memory_events = cgroup_metrics.get("memory_events_delta") or {}
    oom = terminated_reason == "OOMKilled" or any(
        isinstance(memory_events.get(key), int)
        and not isinstance(memory_events.get(key), bool)
        and memory_events[key] > 0
        for key in ("oom", "oom_kill", "oom_group_kill")
    )
    scheduling_code = _sanitize_reason(condition_messages)
    pending_signal = phase == "Pending" or scheduling_code in {
        "FAILED_SCHEDULING", "UNSCHEDULABLE", "INSUFFICIENT_CPU",
        "INSUFFICIENT_MEMORY", "QUOTA_REJECTED", "ADMISSION_FORBIDDEN",
    }
    pending = bool(pending_signal and infrastructure_reason is None and not oom)
    runtime_timeout = (
        isinstance(workload_runtime_seconds, (int, float))
        and not isinstance(workload_runtime_seconds, bool)
        and workload_runtime_seconds > timeout_seconds
    )
    timeout = bool(
        terminated_reason == "DeadlineExceeded"
        or pod_reason == "DeadlineExceeded"
        or runtime_timeout
        or (monitor_deadline_reached and not pending and infrastructure_reason is None)
    )
    return {
        "reason": reason,
        "pending_or_admission_failure": pending,
        "oom": oom,
        "timeout": timeout,
        "infrastructure_reason": infrastructure_reason,
        "admission_or_scheduling_reason": _sanitize_reason(
            condition_messages or waiting_reason or terminated_reason or pod_reason
        ),
    }


class KubernetesResourceEfficiencyAdapter(CalibrationKubernetesAdapter):
    adapter_version = ADAPTER_VERSION

    def read_only_preflight(self) -> Mapping[str, Any]:
        result = collect_read_only_preflight(image=self.image, policy=self.policy, image_state=self.image_state)
        pods = self._json(["get", "pods", "-A"])
        conflicts = [
            item for item in (pods or {}).get("items", [])
            if ((item.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/name")
            == "intent-spawner-resource-efficiency-v5"
        ]
        if conflicts:
            result = dict(result)
            result["failure_codes"] = sorted(set([
                *(result.get("failure_codes") or []),
                "CONFLICTING_RESOURCE_EFFICIENCY_WORKLOAD",
            ]))
            result["eligibility_status"] = "CLUSTER_INELIGIBLE"
        return result

    def run_trial(self, spec: EfficiencyTrialSpec) -> Mapping[str, Any]:
        self.environment_provenance()
        pod_name = _pod_name(spec.trial_id)
        planned = spec.allocation.to_dict()
        created = self._kubectl(["create", "-f", "-"], input_text=json.dumps(build_pod_spec(spec, self.image)))
        if created.returncode != 0:
            reason = _sanitize_reason(created.stderr)
            admission = reason in {"QUOTA_REJECTED", "ADMISSION_FORBIDDEN", "UNSCHEDULABLE", "INSUFFICIENT_CPU", "INSUFFICIENT_MEMORY"}
            return self._record(spec, planned=planned, pending=admission, runtime_error=False, infrastructure_invalid=not admission, exclusion_reason=None if admission else "POD_CREATE_INFRASTRUCTURE_FAILURE", admission_reason=reason)
        deadline = time.monotonic() + spec.timeout_seconds + POD_LIFECYCLE_GRACE_SECONDS + ADAPTER_MONITOR_GRACE_SECONDS
        pod: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            pod = self._json(["get", "pod", pod_name, "-n", NAMESPACE])
            phase = ((pod or {}).get("status") or {}).get("phase")
            if phase in {"Succeeded", "Failed"}:
                break
            time.sleep(0.5)
        monitor_deadline_reached = time.monotonic() >= deadline
        logs_result = self._kubectl(["logs", pod_name, "-n", NAMESPACE], timeout=20)
        logs = logs_result.stdout if logs_result.returncode == 0 else ""
        payload = self._payload(logs)
        status = (((pod or {}).get("status") or {}).get("containerStatuses") or [{}])[0]
        terminated = ((status.get("state") or {}).get("terminated") or {})
        waiting = ((status.get("state") or {}).get("waiting") or {})
        events_payload = self._json(["get", "events", "-n", NAMESPACE, "--field-selector", f"involvedObject.name={pod_name}"])
        event_rows = [{"type": item.get("type"), "reason": item.get("reason"), "message": item.get("message"), "count": item.get("count"), "first_timestamp": item.get("firstTimestamp"), "last_timestamp": item.get("lastTimestamp")} for item in (events_payload or {}).get("items", [])]
        phase = ((pod or {}).get("status") or {}).get("phase")
        pod_reason = ((pod or {}).get("status") or {}).get("reason")
        messages = " ".join(str(item.get("message", "")) for item in (((pod or {}).get("status") or {}).get("conditions") or []))
        metrics = dict((payload or {}).get("cgroup_metrics") or {})
        workload_runtime = (payload or {}).get("runtime_seconds")
        classification = classify_kubernetes_outcome(
            phase=phase, pod_reason=pod_reason,
            terminated_reason=terminated.get("reason"), waiting_reason=waiting.get("reason"),
            condition_messages=messages, cgroup_metrics=metrics,
            monitor_deadline_reached=monitor_deadline_reached,
            workload_runtime_seconds=workload_runtime,
            timeout_seconds=spec.timeout_seconds,
        )
        reason = classification["reason"]
        pending = classification["pending_or_admission_failure"]
        oom = classification["oom"]
        timeout = classification["timeout"]
        infrastructure_reason = classification["infrastructure_reason"]
        deletion = self._kubectl(["delete", "pod", pod_name, "-n", NAMESPACE, "--wait=true", "--timeout=30s"], timeout=35)
        cleanup = "succeeded" if deletion.returncode == 0 else "failed"
        if cleanup != "succeeded" and not (oom or timeout or pending):
            infrastructure_reason = "POD_CLEANUP_FAILED"
        observed = _observed_resources(pod, planned)
        marker = (payload or {}).get("observed_marker_sha256")
        output_exists = payload is not None
        correctness = None if not output_exists else bool(marker == spec.expected_marker_sha256 and (payload or {}).get("correctness_invariants_ok"))
        exit_code = terminated.get("exitCode")
        success = bool(not infrastructure_reason and not pending and not oom and not timeout and exit_code == 0 and correctness is True)
        return self._record(
            spec, planned=planned, observed=observed, pod_created=True, scheduled=bool(((pod or {}).get("spec") or {}).get("nodeName")),
            pending=pending, oom=oom, timeout=timeout, correctness=correctness,
            runtime_error=not infrastructure_reason and not success and not pending and not oom and not timeout and correctness is not False,
            success=success, workload_runtime=workload_runtime, container_runtime=_duration(terminated.get("startedAt"), terminated.get("finishedAt")),
            observed_marker=marker,
            correctness_invariants_ok=None if not output_exists else bool((payload or {}).get("correctness_invariants_ok")),
            correctness_details=dict((payload or {}).get("correctness_details") or {}),
            cgroup_metrics=metrics, infrastructure_invalid=infrastructure_reason is not None, exclusion_reason=infrastructure_reason,
            admission_reason=classification["admission_or_scheduling_reason"],
            kubernetes={"pod_name": pod_name, "pod_uid": ((pod or {}).get("metadata") or {}).get("uid"), "phase": phase, "reason": reason, "terminated_reason": terminated.get("reason"), "waiting_reason": waiting.get("reason"), "exit_code": exit_code, "started_at": terminated.get("startedAt"), "finished_at": terminated.get("finishedAt"), "restart_count": status.get("restartCount"), "node_name": ((pod or {}).get("spec") or {}).get("nodeName"), "image_reference": self.image, "image_id": status.get("imageID"), "events": event_rows, "cleanup_status": cleanup},
        )

    @staticmethod
    def _record(
        spec: EfficiencyTrialSpec, *, planned: Mapping[str, Any], observed: Mapping[str, Any] | None = None,
        pod_created: bool = False, scheduled: bool = False, pending: bool = False, oom: bool = False,
        timeout: bool = False, correctness: bool | None = None, runtime_error: bool = False, success: bool = False,
        workload_runtime: float | None = None, container_runtime: float | None = None,
        observed_marker: str | None = None, correctness_invariants_ok: bool | None = None,
        correctness_details: Mapping[str, Any] | None = None,
        cgroup_metrics: Mapping[str, Any] | None = None, infrastructure_invalid: bool = False,
        exclusion_reason: str | None = None, admission_reason: str | None = None,
        kubernetes: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = {
            "schema_version": TRIAL_SCHEMA_VERSION, **spec.to_dict(),
            "planned_resources": dict(planned), "observed_resources": None if observed is None else dict(observed),
            "pod_created": pod_created, "scheduled": scheduled, "pending_or_admission_failure": pending,
            "admission_or_scheduling_reason": admission_reason, "oom": oom, "timeout": timeout,
            "correctness": correctness, "runtime_error": runtime_error, "success": success,
            "workload_output_exists": correctness is not None,
            "observed_marker_sha256": observed_marker,
            "correctness_invariants_ok": correctness_invariants_ok,
            "correctness_details": dict(correctness_details or {}),
            "workload_runtime_seconds": workload_runtime, "container_runtime_seconds": container_runtime,
            "cgroup_metrics": dict(cgroup_metrics or {}), "kubernetes": dict(kubernetes or {}),
            "infrastructure_invalid": infrastructure_invalid, "exclusion_reason": exclusion_reason,
            "recorded_at_utc": _utc_now(),
        }
        # spec.to_dict contains a nested allocation; the raw contract names this planned_resources only.
        row.pop("allocation", None)
        row["primary_outcome"] = primary_outcome(row)
        return row


__all__ = ["ADAPTER_VERSION", "KubernetesResourceEfficiencyAdapter", "build_pod_spec", "classify_kubernetes_outcome"]
