"""Disposable Kubernetes adapter for Protocol-v5 E4 calibration."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from evaluation_v5.resource.contracts import (
    IMAGE_STATE_PATH, image_state_is_verified, load_cluster_policy, load_image_state,
)
from evaluation_v5.resource.models import TRIAL_SCHEMA_VERSION, TrialObservation, TrialSpec


REQUIRED_CONTEXT = "intent-spawner-eval-v5"
NAMESPACE = "z2jh-context-demo"
SAFETY_LABEL = "z2jh-context-demo.local/disposable-experiment-v5"
IMAGE_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?/[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$")
E4_LABEL_SELECTOR = "app.kubernetes.io/name=intent-spawner-resource-envelope-v5"
POD_LIFECYCLE_GRACE_SECONDS = 30
ADAPTER_MONITOR_GRACE_SECONDS = 5


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _pod_name(run_id: str) -> str:
    return "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]


def _cpu_m(value: str | None) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return int(value[:-1]) if value.endswith("m") else int(float(value) * 1000)
    except ValueError:
        return None


def _memory_mib(value: str | None) -> int | None:
    if not isinstance(value, str):
        return None
    factors = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024}
    for suffix, factor in factors.items():
        if value.endswith(suffix):
            try:
                return int(float(value[:-len(suffix)]) * factor)
            except ValueError:
                return None
    return None


def evaluate_cluster_eligibility(
    *,
    policy: Mapping[str, Any],
    image: str,
    image_state: Mapping[str, Any],
    current_context: str | None,
    namespace: Mapping[str, Any] | None = None,
    nodes: Mapping[str, Any] | None = None,
    all_pods: Mapping[str, Any] | None = None,
    quotas: Mapping[str, Any] | None = None,
    api_access: Mapping[str, bool] | None = None,
    kubernetes_version: Mapping[str, Any] | None = None,
    cgroup_probe: Mapping[str, Any] | None = None,
    require_cgroup_probe: bool = True,
) -> dict[str, Any]:
    failures: list[str] = []
    if current_context != policy["expected_context"]:
        failures.append("WRONG_KUBERNETES_CONTEXT")
    namespace_labels = ((namespace or {}).get("metadata") or {}).get("labels") or {}
    for field, code in (
        ("cluster_identity_label", "WRONG_CLUSTER_FINGERPRINT"),
        ("namespace_safety_label", "CLUSTER_INELIGIBLE"),
    ):
        expected = policy[field]
        if namespace_labels.get(expected["key"]) != expected["value"]:
            failures.append(code)
    node_items = list((nodes or {}).get("items") or [])
    node: Mapping[str, Any] = node_items[0] if len(node_items) == policy["required_node_count"] else {}
    if not node:
        failures.append("WRONG_NODE_COUNT")
    node_labels = (node.get("metadata") or {}).get("labels") or {}
    if node:
        for field, code in (
            ("node_identity_label", "WRONG_NODE_IDENTITY"),
            ("node_isolation_label", "NODE_ISOLATION_REQUIREMENT_NOT_MET"),
        ):
            expected = policy[field]
            if node_labels.get(expected["key"]) != expected["value"]:
                failures.append(code)
        conditions = {item.get("type"): item.get("status") for item in (node.get("status") or {}).get("conditions", [])}
        if conditions.get("Ready") != "True" or any(conditions.get(name) == "True" for name in ("MemoryPressure", "DiskPressure", "PIDPressure")):
            failures.append("NODE_NOT_HEALTHY")
        allocatable = (node.get("status") or {}).get("allocatable") or {}
        if (_cpu_m(allocatable.get("cpu")) or 0) < policy["minimum_allocatable"]["cpu_m"] or (_memory_mib(allocatable.get("memory")) or 0) < policy["minimum_allocatable"]["memory_mib"]:
            failures.append("INSUFFICIENT_NODE_CAPACITY")
        node_info = (node.get("status") or {}).get("nodeInfo") or {}
        if any(not node_info.get(key) for key in ("kubeletVersion", "containerRuntimeVersion", "kernelVersion", "operatingSystem", "architecture")):
            failures.append("NODE_RUNTIME_IDENTITY_INCOMPLETE")
        image_names = {
            name for item in (node.get("status") or {}).get("images", [])
            for name in item.get("names", [])
        }
        digest = image.split("@", 1)[1] if "@" in image else ""
        if image not in image_names and not any(name.endswith("@" + digest) for name in image_names):
            failures.append("IMAGE_NOT_PREPULLED")
    if not IMAGE_RE.fullmatch(image) or not image_state_is_verified(image_state, image):
        failures.append("IMAGE_DIGEST_UNVERIFIED")
    if (quotas or {}).get("items"):
        failures.append("RESOURCE_QUOTA_PRESENT")
    pod_items = list((all_pods or {}).get("items") or [])
    conflicting = [item for item in pod_items if ((item.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/name") == "intent-spawner-resource-envelope-v5"]
    if conflicting:
        failures.append("CONFLICTING_CALIBRATION_WORKLOAD")
    node_name = (node.get("metadata") or {}).get("name")
    non_daemon = [
        item for item in pod_items
        if (item.get("spec") or {}).get("nodeName") == node_name
        and not any(owner.get("kind") == "DaemonSet" for owner in ((item.get("metadata") or {}).get("ownerReferences") or []))
        and ((item.get("metadata") or {}).get("namespace") not in {"kube-system"})
    ]
    if node and non_daemon:
        failures.append("NODE_ISOLATION_REQUIREMENT_NOT_MET")
    if api_access is None or not api_access or not all(api_access.values()):
        failures.append("REQUIRED_API_ACCESS_MISSING")
    if not kubernetes_version:
        failures.append("KUBERNETES_VERSION_UNAVAILABLE")
    if require_cgroup_probe:
        if not cgroup_probe:
            failures.append("CGROUP_V2_REQUIRED")
        else:
            if cgroup_probe.get("cgroup_version") != "v2":
                failures.append("CGROUP_V2_REQUIRED")
            missing_controllers = set(policy["required_cgroup_controllers"]) - set(cgroup_probe.get("controllers") or [])
            if missing_controllers:
                failures.append("CGROUP_CONTROLLER_MISSING")
            missing_files = set(policy["required_cgroup_files"]) - set(cgroup_probe.get("available_files") or [])
            if missing_files:
                failures.append("CGROUP_MEASUREMENT_FILE_MISSING")
            missing_memory_events = set(policy["required_memory_event_keys"]) - set(cgroup_probe.get("memory_event_keys") or [])
            if missing_memory_events:
                failures.append("CGROUP_MEMORY_EVENT_KEY_MISSING")
            if cgroup_probe.get("cleanup_status") != "succeeded":
                failures.append("ELIGIBILITY_PROBE_CLEANUP_FAILED")
    return {
        "schema_version": "protocol-v5-resource-cluster-preflight-v1.1.0",
        "eligibility_status": "ELIGIBLE" if not failures else "CLUSTER_INELIGIBLE",
        "failure_codes": sorted(set(failures)),
        "facts": {
            "current_context": current_context,
            "expected_context": policy["expected_context"],
            "namespace_name": ((namespace or {}).get("metadata") or {}).get("name"),
            "namespace_labels": namespace_labels,
            "node_name": (node.get("metadata") or {}).get("name"),
            "node_uid": (node.get("metadata") or {}).get("uid"),
            "node_labels": node_labels,
            "node_capacity": (node.get("status") or {}).get("capacity"),
            "node_allocatable": (node.get("status") or {}).get("allocatable"),
            "node_info": (node.get("status") or {}).get("nodeInfo"),
            "kubernetes_version": kubernetes_version,
            "api_access": dict(api_access or {}),
            "image_reference": image,
            "image_reference_pinned": bool(IMAGE_RE.fullmatch(image)),
            "image_declared_state": dict(image_state),
            "cgroup_probe": dict(cgroup_probe or {}),
        },
    }


def collect_read_only_preflight(*, image: str, policy: Mapping[str, Any], image_state: Mapping[str, Any]) -> dict[str, Any]:
    """Collect only non-mutating facts; it deliberately does not run the cgroup pod probe."""
    try:
        current = subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        result = evaluate_cluster_eligibility(
            policy=policy, image=image, image_state=image_state, current_context=None,
            require_cgroup_probe=True,
        )
        result["failure_codes"] = sorted(set(result["failure_codes"] + ["KUBECTL_UNAVAILABLE"]))
        return result
    context = current.stdout.strip() if current.returncode == 0 else None
    if context != policy["expected_context"]:
        return evaluate_cluster_eligibility(
            policy=policy, image=image, image_state=image_state, current_context=context,
            require_cgroup_probe=True,
        )

    def get_json(args: list[str]) -> dict[str, Any] | None:
        result = subprocess.run(
            ["kubectl", "--context", context, *args, "-o", "json"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return None
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else None

    access: dict[str, bool] = {}
    for entry in policy["required_api_access"]:
        result = subprocess.run(
            ["kubectl", "--context", context, "auth", "can-i", entry["verb"], entry["resource"], "-n", policy["expected_namespace"]],
            capture_output=True, text=True, check=False,
        )
        access[f"{entry['verb']}:{entry['resource']}"] = result.returncode == 0 and result.stdout.strip() == "yes"
    return evaluate_cluster_eligibility(
        policy=policy, image=image, image_state=image_state, current_context=context,
        namespace=get_json(["get", "namespace", policy["expected_namespace"]]),
        nodes=get_json(["get", "nodes"]), all_pods=get_json(["get", "pods", "-A"]),
        quotas=get_json(["get", "resourcequota", "-n", policy["expected_namespace"]]),
        api_access=access, kubernetes_version=get_json(["version"]),
        require_cgroup_probe=True,
    )


def build_pod_spec(spec: TrialSpec, image: str) -> dict[str, Any]:
    if not IMAGE_RE.fullmatch(image):
        raise ValueError("E4 execution requires an immutable sha256 image reference")
    cpu = f"{spec.cpu_m}m"
    memory = f"{spec.memory_mib}Mi"
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": _pod_name(spec.run_id),
            "namespace": NAMESPACE,
            "labels": {
                "app.kubernetes.io/name": "intent-spawner-resource-envelope-v5",
                "z2jh-context-demo.local/experiment-v5": "true",
            },
            "annotations": {
                "z2jh-context-demo.local/run-id": spec.run_id,
                "z2jh-context-demo.local/family-id": spec.family_id,
                "z2jh-context-demo.local/phase": spec.phase,
                "z2jh-context-demo.local/workload-timeout-seconds": str(spec.timeout_seconds),
                "z2jh-context-demo.local/pod-lifecycle-grace-seconds": str(POD_LIFECYCLE_GRACE_SECONDS),
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "activeDeadlineSeconds": spec.timeout_seconds + POD_LIFECYCLE_GRACE_SECONDS,
            "automountServiceAccountToken": False,
            "nodeSelector": {
                "z2jh-context-demo.local/node-identity": "e4-node-v1",
                "z2jh-context-demo.local/dedicated-e4": "true",
            },
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 10001,
                "runAsGroup": 10001,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [{
                "name": "calibration",
                "image": image,
                "imagePullPolicy": "Never",
                "args": ["--family-id", spec.family_id, "--sample-interval", "0.1"],
                "resources": {
                    "requests": {"cpu": cpu, "memory": memory},
                    "limits": {"cpu": cpu, "memory": memory},
                },
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                    "readOnlyRootFilesystem": True,
                },
                "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
            }],
            "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}],
        },
    }


class KubernetesTrialAdapter:
    adapter_version = "protocol-v5-kubernetes-trial-adapter-v1.2.0"

    def __init__(self, *, image: str, image_state_path: Path = IMAGE_STATE_PATH) -> None:
        if not IMAGE_RE.fullmatch(image):
            raise ValueError("E4 execution requires an immutable sha256 image reference")
        self.image = image
        self.policy = load_cluster_policy()
        self.image_state = load_image_state(image_state_path)
        self._environment: dict[str, Any] | None = None

    def _kubectl(self, args: list[str], *, input_text: str | None = None, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["kubectl", "--context", REQUIRED_CONTEXT, *args], input=input_text,
            capture_output=True, text=True, check=False, timeout=timeout,
        )

    def _json(self, args: list[str]) -> dict[str, Any] | None:
        result = self._kubectl([*args, "-o", "json"])
        if result.returncode != 0:
            return None
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else None

    def _preflight(self) -> dict[str, Any]:
        read_only = collect_read_only_preflight(
            image=self.image, policy=self.policy, image_state=self.image_state,
        )
        non_probe_failures = [code for code in read_only["failure_codes"] if code != "CGROUP_V2_REQUIRED"]
        if non_probe_failures:
            raise RuntimeError("CLUSTER_INELIGIBLE: " + ",".join(non_probe_failures))
        probe = self._run_cgroup_probe()
        probe_failures: list[str] = []
        if probe.get("cgroup_version") != "v2":
            probe_failures.append("CGROUP_V2_REQUIRED")
        if set(self.policy["required_cgroup_controllers"]) - set(probe.get("controllers") or []):
            probe_failures.append("CGROUP_CONTROLLER_MISSING")
        if set(self.policy["required_cgroup_files"]) - set(probe.get("available_files") or []):
            probe_failures.append("CGROUP_MEASUREMENT_FILE_MISSING")
        if set(self.policy["required_memory_event_keys"]) - set(probe.get("memory_event_keys") or []):
            probe_failures.append("CGROUP_MEMORY_EVENT_KEY_MISSING")
        if probe.get("cleanup_status") != "succeeded":
            probe_failures.append("ELIGIBILITY_PROBE_CLEANUP_FAILED")
        if probe_failures:
            raise RuntimeError("CLUSTER_INELIGIBLE: " + ",".join(sorted(set(probe_failures))))
        facts = read_only["facts"]
        node_info = facts.get("node_info") or {}
        return {
            "schema_version": "protocol-v5-resource-environment-v1.1.0",
            "captured_at_utc": _utc_now(),
            "environment_id": f"{REQUIRED_CONTEXT}:{NAMESPACE}",
            "eligibility_status": "ELIGIBLE",
            "eligibility_policy_version": self.policy["schema_version"],
            "read_only_preflight": read_only,
            "cgroup_eligibility_probe": probe,
            "required_context": REQUIRED_CONTEXT,
            "namespace": NAMESPACE,
            "namespace_safety_label": f"{SAFETY_LABEL}=true",
            "container_image": self.image,
            "node_capacity": facts.get("node_capacity"),
            "node_allocatable": facts.get("node_allocatable"),
            "kubernetes_version": facts.get("kubernetes_version"),
            "kubelet_version": node_info.get("kubeletVersion"),
            "container_runtime": node_info.get("containerRuntimeVersion"),
            "kernel_version": node_info.get("kernelVersion"),
            "operating_system": node_info.get("operatingSystem"),
            "architecture": node_info.get("architecture"),
            "cgroup_requirement": "v2",
            "required_memory_event_keys": self.policy["required_memory_event_keys"],
            "workload_timeout_basis": "measured_workload_runtime_seconds",
            "pod_lifecycle_grace_seconds": POD_LIFECYCLE_GRACE_SECONDS,
            "adapter_monitor_grace_seconds": ADAPTER_MONITOR_GRACE_SECONDS,
            "single_active_workload": True,
        }

    def _run_cgroup_probe(self) -> dict[str, Any]:
        name = "e4-cgroup-eligibility-probe"
        script = (
            "import json,pathlib; p=pathlib.Path('/sys/fs/cgroup'); "
            "files=['cgroup.controllers','cpu.max','cpu.stat','memory.current','memory.events','memory.max','memory.peak']; "
            "e=p/'memory.events'; "
            "print(json.dumps({'cgroup_version':'v2' if (p/'cgroup.controllers').is_file() else None,"
            "'controllers':(p/'cgroup.controllers').read_text().split() if (p/'cgroup.controllers').is_file() else [],"
            "'available_files':[x for x in files if (p/x).is_file()],"
            "'memory_event_keys':[line.split()[0] for line in e.read_text().splitlines() if line.split()] if e.is_file() else []}))"
        )
        identity = self.policy["node_identity_label"]
        pod = {
            "apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": NAMESPACE, "labels": {"app.kubernetes.io/name": "intent-spawner-resource-envelope-v5-probe"}},
            "spec": {
                "restartPolicy": "Never", "activeDeadlineSeconds": 30,
                "automountServiceAccountToken": False,
                "nodeSelector": {identity["key"]: identity["value"]},
                "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{
                    "name": "probe", "image": self.image, "imagePullPolicy": "Never",
                    "command": ["python3", "-c", script],
                    "resources": {"requests": {"cpu": "100m", "memory": "64Mi"}, "limits": {"cpu": "100m", "memory": "64Mi"}},
                    "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}, "readOnlyRootFilesystem": True},
                }],
            },
        }
        created = self._kubectl(["create", "-f", "-"], input_text=json.dumps(pod))
        result: dict[str, Any] = {}
        if created.returncode == 0:
            self._kubectl(["wait", "--for=jsonpath={.status.phase}=Succeeded", "pod", name, "-n", NAMESPACE, "--timeout=30s"], timeout=35)
            logs = self._kubectl(["logs", name, "-n", NAMESPACE], timeout=10)
            if logs.returncode == 0:
                try:
                    value = json.loads(logs.stdout.strip().splitlines()[-1])
                    if isinstance(value, dict):
                        result.update(value)
                except (json.JSONDecodeError, IndexError):
                    pass
        deletion = self._kubectl(["delete", "pod", name, "-n", NAMESPACE, "--ignore-not-found=true", "--wait=true", "--timeout=30s"], timeout=35)
        result["cleanup_status"] = "succeeded" if deletion.returncode == 0 else "failed"
        result["probe_status"] = "succeeded" if created.returncode == 0 and result.get("cgroup_version") else "failed"
        return result

    def environment_provenance(self) -> Mapping[str, Any]:
        if self._environment is None:
            self._environment = self._preflight()
        return dict(self._environment)

    @staticmethod
    def _payload(logs: str) -> dict[str, Any] | None:
        for line in reversed(logs.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if value.get("schema_version") == "protocol-v5-resource-pod-result-v1.0.0":
                return value
        return None

    def run_trial(self, spec: TrialSpec) -> TrialObservation:
        self.environment_provenance()
        pod_name = _pod_name(spec.run_id)
        created = self._kubectl(
            ["create", "-f", "-"], input_text=json.dumps(build_pod_spec(spec, self.image))
        )
        if created.returncode != 0:
            return self._observation(spec, infrastructure_invalid=True, exclusion_reason="pod_create_failed", exit_reason="CreateFailed")
        deadline = time.monotonic() + spec.timeout_seconds + POD_LIFECYCLE_GRACE_SECONDS + ADAPTER_MONITOR_GRACE_SECONDS
        pod: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            pod = self._json(["get", "pod", pod_name, "-n", NAMESPACE])
            phase = (pod or {}).get("status", {}).get("phase")
            if phase in {"Succeeded", "Failed"}:
                break
            time.sleep(0.5)
        logs_result = self._kubectl(["logs", pod_name, "-n", NAMESPACE], timeout=20)
        logs = logs_result.stdout if logs_result.returncode == 0 else ""
        payload = self._payload(logs)
        status = ((pod or {}).get("status", {}).get("containerStatuses") or [{}])[0]
        terminated = status.get("state", {}).get("terminated", {})
        reason = terminated.get("reason") or (pod or {}).get("status", {}).get("reason")
        exit_code = terminated.get("exitCode")
        runtime = (payload or {}).get("runtime_seconds")
        infrastructure_reason = None
        if reason in {"Evicted", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError"}:
            infrastructure_reason = f"kubernetes_{reason}"
        deletion = self._kubectl(["delete", "pod", pod_name, "-n", NAMESPACE, "--wait=true", "--timeout=30s"], timeout=35)
        if deletion.returncode != 0 and infrastructure_reason is None:
            infrastructure_reason = "pod_cleanup_failed"
        metrics = dict((payload or {}).get("cgroup_metrics") or {})
        memory_events = metrics.get("memory_events_delta")
        oom_kill_event = bool(
            isinstance(memory_events, Mapping)
            and any(
                isinstance(memory_events.get(key), int)
                and not isinstance(memory_events.get(key), bool)
                and memory_events[key] > 0
                for key in ("oom_kill", "oom_group_kill")
            )
        )
        oom = reason == "OOMKilled" or oom_kill_event
        timeout = bool(
            time.monotonic() >= deadline
            or reason == "DeadlineExceeded"
            or (
                isinstance(runtime, (int, float))
                and not isinstance(runtime, bool)
                and runtime > spec.timeout_seconds
            )
        )
        if oom or timeout:
            infrastructure_reason = None
        return self._observation(
            spec,
            observed_marker=(payload or {}).get("observed_marker_sha256"),
            exit_code=exit_code,
            exit_reason=reason,
            oom=oom,
            timeout=timeout,
            runtime=runtime,
            metrics=metrics,
            correctness_invariants_ok=bool((payload or {}).get("correctness_invariants_ok")),
            correctness_details=dict((payload or {}).get("correctness_details") or {}),
            infrastructure_invalid=infrastructure_reason is not None,
            exclusion_reason=infrastructure_reason,
            kubernetes={
                "pod_name": pod_name,
                "phase": (pod or {}).get("status", {}).get("phase"),
                "started_at": terminated.get("startedAt"),
                "finished_at": terminated.get("finishedAt"),
                "restart_count": status.get("restartCount"),
                "cleanup_status": "succeeded" if deletion.returncode == 0 else "failed",
                "workload_timeout_seconds": spec.timeout_seconds,
                "pod_lifecycle_grace_seconds": POD_LIFECYCLE_GRACE_SECONDS,
                "pod_active_deadline_seconds": spec.timeout_seconds + POD_LIFECYCLE_GRACE_SECONDS,
                "adapter_monitor_grace_seconds": ADAPTER_MONITOR_GRACE_SECONDS,
            },
        )

    def _observation(
        self,
        spec: TrialSpec,
        *,
        observed_marker: str | None = None,
        exit_code: int | None = None,
        exit_reason: str | None = None,
        oom: bool = False,
        timeout: bool = False,
        runtime: float | None = None,
        metrics: Mapping[str, Any] | None = None,
        correctness_invariants_ok: bool = False,
        correctness_details: Mapping[str, Any] | None = None,
        infrastructure_invalid: bool = False,
        exclusion_reason: str | None = None,
        kubernetes: Mapping[str, Any] | None = None,
    ) -> TrialObservation:
        metrics = dict(metrics or {})
        memory_events = metrics.get("memory_events_delta")
        if isinstance(memory_events, Mapping):
            oom = oom or any(
                isinstance(memory_events.get(key), int)
                and not isinstance(memory_events.get(key), bool)
                and memory_events[key] > 0
                for key in ("oom_kill", "oom_group_kill")
            )
        timeout = bool(
            timeout
            or (
                isinstance(runtime, (int, float))
                and not isinstance(runtime, bool)
                and runtime > spec.timeout_seconds
            )
        )
        return TrialObservation(
            schema_version=TRIAL_SCHEMA_VERSION,
            run_id=spec.run_id, family_id=spec.family_id,
            workload_instance_id=spec.workload_instance_id,
            workload_fingerprint=spec.workload_fingerprint, phase=spec.phase,
            cpu_m=spec.cpu_m, memory_mib=spec.memory_mib,
            repeat_index=spec.repeat_index, deterministic_seed=spec.deterministic_seed,
            expected_marker_sha256=spec.expected_marker_sha256,
            observed_marker_sha256=observed_marker, exit_code=exit_code,
            exit_reason=exit_reason, oom_killed=oom, timeout=timeout,
            workload_timeout_seconds=spec.timeout_seconds,
            runtime_seconds=runtime,
            correctness_marker_ok=observed_marker == spec.expected_marker_sha256,
            correctness_invariants_ok=correctness_invariants_ok,
            correctness_details=dict(correctness_details or {}),
            infrastructure_invalid=infrastructure_invalid,
            exclusion_reason=exclusion_reason,
            cgroup_version=metrics.get("cgroup_version"), cgroup_metrics=metrics,
            kubernetes=dict(kubernetes or {}), replacement_of=spec.replacement_of,
            recorded_at_utc=_utc_now(),
        )


__all__ = ["KubernetesTrialAdapter", "build_pod_spec"]
