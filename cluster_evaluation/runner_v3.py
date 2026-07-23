"""Preregistered sequential direct-pod runner for protocol v3.

No Kubernetes mutation occurs unless ``--execute`` is explicitly supplied.
The v2 runner and all preserved evidence remain untouched.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import time
from typing import Any, Iterable

from benchmarks.resource_envelope_runner import MANIFEST, deterministic_seed, load_manifest
from cluster_evaluation.policies import METHODS, PROFILE_RESOURCES, decide_cluster_method
from cluster_evaluation.result_schema_v3 import IMAGE_RE, validate_record
from experiments.kubernetes_evidence import extract_pod_evidence, parse_memory_mi
from experiments.result_schema import current_git_commit


ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "z2jh-context-demo"
REQUIRED_CONTEXT = "intent-spawner-eval-v3"
SAFETY_LABEL = "z2jh-context-demo.local/disposable-experiment-v3"
SAFETY_LABEL_VALUE = "true"
MASTER_SEED = 20260723
PROFILE_ORDER = ("small", "medium", "large")
METHOD_ORDER = ("static_default", "intent_only", "context_aware")
EXPERIMENT_LABEL = "z2jh-context-demo.local/experiment-v3"
REQUIRED_NODE_CPU = "6"
REQUIRED_NODE_MEMORY = "6088560Ki"
MIN_LOCAL_FREE_BYTES = 5 * 1024**3
ANALYSIS = ROOT / "cluster_evaluation" / "analyze_v3.py"
PROTOCOL = ROOT / "docs" / "evaluation" / "RESOURCE_ENVELOPE_PROTOCOL_V3.md"
CRITICAL_COMMIT_PATHS = (
    MANIFEST,
    ROOT / "benchmarks" / "resource_envelope_runner.py",
    ROOT / "cluster_evaluation" / "runner_v3.py",
    ROOT / "cluster_evaluation" / "pod_runner_v3.py",
    ROOT / "cluster_evaluation" / "result_schema_v3.py",
    ROOT / "cluster_evaluation" / "analyze_v3.py",
    ROOT / "cluster_evaluation" / "policies.py",
    ROOT / "recommender" / "recommender.py",
    PROTOCOL,
)


@dataclass(frozen=True)
class PlanItem:
    plan_index: int
    experiment_kind: str
    experiment_path: str
    run_id: str
    evaluation_set: str
    workload_id: str
    repeat_index: int
    random_seed: int
    method: str
    recommended_profile: str | None
    applied_profile: str
    recommendation_reasons: list[str]
    context_signal_summary: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _input_sha256(workload: dict[str, Any]) -> str:
    return _sha256_json(
        {
            "workload_id": workload["workload_id"],
            "intent": workload["intent"],
            "dataset_size_hint_gb": workload["dataset_size_hint_gb"],
            "code_context_hints": workload["code_context_hints"],
        }
    )


def _supporting_checksums(paths: Iterable[Path]) -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)): _sha256(path)
        for path in paths
    }


def _append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def _write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def _write_json_new(path: Path, payload: Any) -> None:
    _write_new(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _inside_repository(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def _require_frozen_commit() -> None:
    relative_paths = [str(path.relative_to(ROOT)) for path in CRITICAL_COMMIT_PATHS]
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", *relative_paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode != 0:
        raise RuntimeError(
            "v3 execution inputs are not all tracked by the recorded Git commit"
        )
    dirty = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no", "--"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if dirty.returncode != 0 or dirty.stdout:
        raise RuntimeError("v3 execution requires a clean tracked Git tree")


def load_workloads() -> list[dict[str, Any]]:
    return list(load_manifest(MANIFEST)["workloads"])


def _rotated(values: tuple[str, ...], repeat: int) -> tuple[str, ...]:
    offset = repeat % len(values)
    return values[offset:] + values[:offset]


def _ordered_workloads(
    workloads: Iterable[dict[str, Any]], repeat: int, master_seed: int
) -> list[dict[str, Any]]:
    ordered = list(workloads)
    random.Random(master_seed + repeat).shuffle(ordered)
    return ordered


def _plan_id(
    experiment_id: str,
    kind: str,
    method: str,
    workload_id: str,
    repeat: int,
    profile: str,
) -> str:
    return f"{experiment_id}-{kind}-{method}-{workload_id}-r{repeat:02d}-{profile}"


def generate_plan(
    kind: str,
    experiment_id: str,
    *,
    master_seed: int = MASTER_SEED,
    repeats: int | None = None,
) -> list[PlanItem]:
    workloads = load_workloads()
    if kind == "calibration":
        selected = [item for item in workloads if item["evaluation_set"] == "calibration"]
        repeat_count = 3 if repeats is None else repeats
    elif kind in {"ground-truth", "comparative"}:
        selected = [item for item in workloads if item["evaluation_set"].startswith("holdout_")]
        repeat_count = 5 if repeats is None else repeats
    else:
        raise ValueError(f"unsupported v3 experiment kind {kind!r}")
    expected_repeats = 3 if kind == "calibration" else 5
    if repeat_count != expected_repeats:
        raise ValueError(f"{kind} is preregistered with exactly {expected_repeats} repeats")

    pending: list[PlanItem] = []
    for repeat in range(repeat_count):
        for workload in _ordered_workloads(selected, repeat, master_seed):
            seed = deterministic_seed(workload["workload_id"], repeat, master_seed)
            if kind == "calibration":
                calibration_profiles = tuple(workload["calibration_profiles"])
                profile_axis = tuple(
                    profile
                    for profile in _rotated(PROFILE_ORDER, repeat)
                    if profile in calibration_profiles
                )
                decisions = [
                    (
                        "calibration_profile_sweep",
                        None,
                        profile,
                        ["forced calibration profile independent of recommender"],
                        {
                            "raw_context_stored": False,
                            "raw_context_available": False,
                            "hint_count": 0,
                            "detected_terms": [],
                            "dataset_size_signal_used": False,
                        },
                    )
                    for profile in profile_axis
                ]
            elif kind == "ground-truth":
                decisions = [
                    (
                        "profile_sweep",
                        None,
                        profile,
                        ["forced hold-out profile sweep independent of recommender"],
                        {
                            "raw_context_stored": False,
                            "raw_context_available": False,
                            "hint_count": 0,
                            "detected_terms": [],
                            "dataset_size_signal_used": False,
                        },
                    )
                    for profile in _rotated(PROFILE_ORDER, repeat)
                ]
            else:
                decisions = []
                for method in _rotated(METHOD_ORDER, repeat):
                    decision = decide_cluster_method(method, workload)
                    decisions.append(
                        (
                            method,
                            decision.recommended_profile,
                            decision.applied_profile,
                            decision.recommendation_reasons,
                            decision.context_signal_summary,
                        )
                    )

            for method, recommended, applied, reasons, signals in decisions:
                pending.append(
                    PlanItem(
                        plan_index=-1,
                        experiment_kind=kind,
                        experiment_path="direct_pod",
                        run_id=_plan_id(
                            experiment_id,
                            kind,
                            method,
                            workload["workload_id"],
                            repeat,
                            applied,
                        ),
                        evaluation_set=workload["evaluation_set"],
                        workload_id=workload["workload_id"],
                        repeat_index=repeat,
                        random_seed=seed,
                        method=method,
                        recommended_profile=recommended,
                        applied_profile=applied,
                        recommendation_reasons=list(reasons),
                        context_signal_summary=dict(signals),
                    )
                )
    plan = [
        PlanItem(**{**asdict(item), "plan_index": index})
        for index, item in enumerate(pending)
    ]
    run_ids = [item.run_id for item in plan]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("generated v3 plan contains duplicate run IDs")
    return plan


def _pod_name(run_id: str) -> str:
    return "v3-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]


def build_pod_spec(item: PlanItem, workload: dict[str, Any], image: str) -> dict[str, Any]:
    resources = PROFILE_RESOURCES[item.applied_profile]
    deadline = int(workload["workload_deadline_seconds"])
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": _pod_name(item.run_id),
            "namespace": NAMESPACE,
            "labels": {
                EXPERIMENT_LABEL: "true",
                "app.kubernetes.io/name": "intent-spawner-resource-envelope-v3",
            },
            "annotations": {
                "z2jh-context-demo.local/run-id": item.run_id,
                "z2jh-context-demo.local/method": item.method,
                "z2jh-context-demo.local/applied-profile": item.applied_profile,
                "z2jh-context-demo.local/evaluation-set": item.evaluation_set,
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "activeDeadlineSeconds": deadline + 30,
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 10001,
                "runAsGroup": 10001,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "benchmark",
                    "image": image,
                    "imagePullPolicy": "IfNotPresent",
                    "args": [
                        "--workload-id",
                        item.workload_id,
                        "--seed",
                        str(item.random_seed),
                        "--sample-interval",
                        "0.1",
                    ],
                    "env": [
                        {"name": "RECOMMENDED_PROFILE", "value": item.recommended_profile or "none"},
                        {"name": "SELECTED_STATIC_PROFILE", "value": item.applied_profile},
                    ],
                    "resources": {
                        "requests": {
                            "cpu": resources["cpu_request"],
                            "memory": resources["memory_request"],
                        },
                        "limits": {
                            "cpu": resources["cpu_limit"],
                            "memory": resources["memory_limit"],
                        },
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                        "readOnlyRootFilesystem": True,
                    },
                    "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                }
            ],
            "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}],
        },
    }


def _kubectl(
    args: list[str],
    input_text: str | None = None,
    *,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", "--context", REQUIRED_CONTEXT, *args],
        cwd=ROOT,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _get_json(args: list[str]) -> dict[str, Any] | None:
    result = _kubectl([*args, "-o", "json"])
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def _node_health(node: dict[str, Any]) -> tuple[bool, list[str]]:
    conditions = {
        item.get("type"): item.get("status")
        for item in node.get("status", {}).get("conditions", [])
    }
    reasons = []
    if conditions.get("Ready") != "True":
        reasons.append("node is not Ready")
    for name in ("MemoryPressure", "DiskPressure", "PIDPressure"):
        if conditions.get(name) == "True":
            reasons.append(name)
    return not reasons, reasons


def _preflight(image: str) -> dict[str, Any]:
    if not IMAGE_RE.fullmatch(image):
        raise RuntimeError("v3 execution requires an immutable image digest")
    context = _kubectl(["config", "current-context"])
    if context.returncode != 0 or context.stdout.strip() != REQUIRED_CONTEXT:
        raise RuntimeError(f"current context must be {REQUIRED_CONTEXT!r}")
    namespace = _get_json(["get", "namespace", NAMESPACE])
    if namespace is None:
        raise RuntimeError(f"required namespace {NAMESPACE!r} does not exist")
    labels = namespace.get("metadata", {}).get("labels", {})
    if labels.get(SAFETY_LABEL) != SAFETY_LABEL_VALUE:
        raise RuntimeError(f"namespace requires safety label {SAFETY_LABEL}=true")
    _require_frozen_commit()
    if shutil.disk_usage(ROOT).free < MIN_LOCAL_FREE_BYTES:
        raise RuntimeError("less than 5 GiB is free for append-only evidence")
    nodes = _get_json(["get", "nodes"])
    if nodes is None or len(nodes.get("items", [])) != 1:
        raise RuntimeError("v3 requires exactly one disposable evaluation node")
    node = nodes["items"][0]
    healthy, reasons = _node_health(node)
    if not healthy:
        raise RuntimeError("unsafe node condition: " + ", ".join(reasons))
    allocatable = node["status"]["allocatable"]
    if (
        allocatable.get("cpu") != REQUIRED_NODE_CPU
        or allocatable.get("memory") != REQUIRED_NODE_MEMORY
    ):
        raise RuntimeError("node allocatable resources differ from preregistration")
    pods = _get_json(["get", "pods", "-n", NAMESPACE])
    if pods is None:
        raise RuntimeError("cannot inspect experiment namespace")
    if pods.get("items"):
        raise RuntimeError("direct-pod phase requires an empty experiment namespace")
    quotas = _get_json(["get", "resourcequota", "-n", NAMESPACE])
    if quotas is None or quotas.get("items"):
        raise RuntimeError("v3 namespace must have no ResourceQuota")
    metrics = _kubectl(["get", "apiservice", "v1beta1.metrics.k8s.io"])
    if metrics.returncode != 0:
        raise RuntimeError("Metrics API is unavailable")
    image_names = {
        name
        for status in node.get("status", {}).get("images", [])
        for name in status.get("names", [])
    }
    image_digest = image.split("@", maxsplit=1)[1]
    if image not in image_names and not any(
        name.endswith("@" + image_digest) for name in image_names
    ):
        raise RuntimeError("immutable v3 image is not pre-pulled on the node")
    return {
        "schema_version": "3.0.0",
        "protocol_version": "3.0.0",
        "experiment_path": "direct_pod",
        "captured_at": _utc_now(),
        "required_context": REQUIRED_CONTEXT,
        "namespace": NAMESPACE,
        "namespace_safety_label": f"{SAFETY_LABEL}=true",
        "git_commit": current_git_commit(ROOT),
        "git_dirty": False,
        "manifest_path": str(MANIFEST.relative_to(ROOT)),
        "manifest_sha256": _sha256(MANIFEST),
        "container_image": image,
        "master_seed": MASTER_SEED,
        "profile_resources": PROFILE_RESOURCES,
        "node_capacity": node["status"]["capacity"],
        "node_allocatable": allocatable,
        "node_conditions": {
            item.get("type"): item.get("status")
            for item in node.get("status", {}).get("conditions", [])
        },
        "kubernetes_version": (
            _get_json(["version"]) or {}
        ),
        "container_runtime": node.get("status", {})
        .get("nodeInfo", {})
        .get("containerRuntimeVersion"),
        "kernel_version": node.get("status", {}).get("nodeInfo", {}).get("kernelVersion"),
        "stop_policy": {
            "max_consecutive_infrastructure_invalid": 3,
            "max_infrastructure_invalid_fraction": 0.10,
            "cleanup_failure_stops_before_next_trial": True,
            "single_active_workload": True,
        },
    }


def _write_preflight_report(image: str, path: Path) -> bool:
    if not _inside_repository(path):
        raise ValueError("preflight report must remain inside the repository")
    report: dict[str, Any] = {
        "schema_version": "3.0.0",
        "protocol_version": "3.0.0",
        "captured_at": _utc_now(),
        "mode": "read_only_preflight",
        "container_image": image,
        "mutating_commands_executed": [],
    }
    try:
        report["environment"] = _preflight(image)
        report["status"] = "pass"
        report["failed_precondition"] = None
        report["commands_not_executed"] = []
        passed = True
    except Exception as exc:
        report["status"] = "blocked"
        report["failed_precondition"] = str(exc)
        report["minimal_remediation"] = (
            "Satisfy the reported frozen protocol precondition, then run a new "
            "append-only preflight report. Do not bypass the gate."
        )
        report["commands_not_executed"] = [
            "kubectl create",
            "kubectl delete",
            "kubectl exec",
            "helm upgrade --install",
            "all v3 workload execution",
        ]
        passed = False
    _write_json_new(path, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return passed


def _parse_payload(log_text: str) -> dict[str, Any] | None:
    for line in reversed(log_text.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("pod_runner_schema_version") == "3.0.0":
            return payload
    return None


def _metric_snapshot(pod_name: str) -> dict[str, Any] | None:
    result = _kubectl(
        [
            "get",
            "--raw",
            f"/apis/metrics.k8s.io/v1beta1/namespaces/{NAMESPACE}/pods/{pod_name}",
        ]
    )
    if result.returncode != 0:
        return None
    payload = json.loads(result.stdout)
    return {
        "timestamp": _utc_now(),
        "containers": [
            {
                "name": item.get("name"),
                "cpu": item.get("usage", {}).get("cpu"),
                "memory": item.get("usage", {}).get("memory"),
            }
            for item in payload.get("containers", [])
        ],
    }


def _events(pod_name: str) -> dict[str, Any]:
    return _get_json(
        [
            "get",
            "events",
            "-n",
            NAMESPACE,
            "--field-selector",
            f"involvedObject.name={pod_name}",
        ]
    ) or {"items": []}


def _observed_profile(evidence: dict[str, Any]) -> str | None:
    actual = evidence.get("requests_limits", {})
    for profile, expected in PROFILE_RESOURCES.items():
        if actual == {
            "cpu_request_m": expected["cpu_request_m"],
            "cpu_limit_m": expected["cpu_limit_m"],
            "memory_request_mi": expected["memory_request_mi"],
            "memory_limit_mi": expected["memory_limit_mi"],
        }:
            return profile
    return None


def _infrastructure_exclusion(evidence: dict[str, Any], create_failed: bool = False) -> str | None:
    if create_failed:
        return "kubernetes_api_or_admission_failure_before_scheduling"
    if evidence.get("termination_reason") in {"Evicted", "NodeLost", "Shutdown"}:
        return f"node_infrastructure_failure:{evidence['termination_reason']}"
    event_reasons = {
        event.get("reason") for event in evidence.get("events", [])
    }
    if event_reasons & {
        "FailedMount",
        "ErrImagePull",
        "ImagePullBackOff",
        "FailedToRetrieveImagePullSecret",
    }:
        return "image_or_mount_infrastructure_failure"
    pending = " ".join(evidence.get("scheduling_or_pending_reasons", []))
    if "ImagePull" in pending or "FailedMount" in pending:
        return "image_or_mount_infrastructure_failure"
    return None


def _record_for_create_failure(
    item: PlanItem,
    workload: dict[str, Any],
    image: str,
    created_at: str,
    reason: str,
    supporting_path: Path,
) -> dict[str, Any]:
    resources = PROFILE_RESOURCES[item.applied_profile]
    record = {
        "schema_version": "3.0.0",
        "protocol_version": "3.0.0",
        "experiment_path": "direct_pod",
        "experiment_kind": item.experiment_kind,
        "run_id": item.run_id,
        "plan_index": item.plan_index,
        "evaluation_set": item.evaluation_set,
        "workload_id": item.workload_id,
        "repeat_index": item.repeat_index,
        "random_seed": item.random_seed,
        "method": item.method,
        "recommended_profile": item.recommended_profile,
        "applied_profile": item.applied_profile,
        "observed_profile": None,
        "recommendation_reasons": item.recommendation_reasons,
        "context_signal_summary": item.context_signal_summary,
        "cpu_request_m": resources["cpu_request_m"],
        "cpu_limit_m": resources["cpu_limit_m"],
        "memory_request_mi": resources["memory_request_mi"],
        "memory_limit_mi": resources["memory_limit_mi"],
        "target_cgroup_mib": int(workload["target_cgroup_mib"]),
        "target_band_mib": list(workload["target_band_mib"]),
        "actual_cgroup_peak_mib": None,
        "useful_allocation_bytes": None,
        "pressure_padding_bytes": None,
        "hold_seconds": float(workload["hold_seconds"]),
        "cpu_full_window_average_m": None,
        "cpu_interval_sample_max_m": None,
        "cpu_nr_periods_delta": None,
        "cpu_nr_throttled_delta": None,
        "cpu_throttled_usec_delta": None,
        "cgroup_sample_interval_seconds": None,
        "cgroup_sample_count": None,
        "benchmark_runtime_seconds": None,
        "pod_pending_duration_seconds": None,
        "spawn_latency_seconds": None,
        "time_to_outcome_seconds": None,
        "phase": None,
        "exit_code": None,
        "exit_reason": None,
        "oom_killed": False,
        "timeout": False,
        "restart_count": 0,
        "checksum": None,
        "success": False,
        "infrastructure_invalid": True,
        "exclusion_reason": reason,
        "replacement_run_id": f"{item.run_id}-replacement01",
        "cleanup_status": "not_required",
        "failure_category": "scheduler_failure",
        "git_commit": current_git_commit(ROOT),
        "container_image": image,
        "configuration_identity": None,
        "input_sha256": _input_sha256(workload),
        "supporting_evidence_sha256": _supporting_checksums([supporting_path]),
        "supporting_log_paths": [str(supporting_path.relative_to(ROOT))],
        "timestamp_created": created_at,
        "timestamp_recorded": _utc_now(),
    }
    validate_record(record)
    return record


def run_item(
    item: PlanItem,
    workload: dict[str, Any],
    image: str,
    run_dir: Path,
) -> dict[str, Any]:
    pod_spec = build_pod_spec(item, workload, image)
    pod_name = pod_spec["metadata"]["name"]
    created_at = _utc_now()
    create = _kubectl(["create", "-f", "-"], input_text=json.dumps(pod_spec))
    if create.returncode != 0:
        reason = _infrastructure_exclusion({}, create_failed=True)
        supporting_path = run_dir / "create-error.log"
        _write_new(supporting_path, create.stderr or create.stdout)
        record = _record_for_create_failure(
            item,
            workload,
            image,
            created_at,
            reason or "create_failure",
            supporting_path,
        )
        _write_json_new(run_dir / "record.json", record)
        return record

    controller_started = time.monotonic()
    deadline = controller_started + int(workload["workload_deadline_seconds"]) + 30
    final_pod: dict[str, Any] | None = None
    snapshots: list[dict[str, Any]] = []
    next_metric = 0.0
    timed_out = False
    while time.monotonic() < deadline:
        final_pod = _get_json(["get", "pod", pod_name, "-n", NAMESPACE])
        if final_pod is None:
            time.sleep(0.1)
            continue
        phase = final_pod.get("status", {}).get("phase")
        if phase == "Running" and time.monotonic() >= next_metric:
            snapshot = _metric_snapshot(pod_name)
            if snapshot is not None:
                snapshots.append(snapshot)
            next_metric = time.monotonic() + 0.5
        if phase in {"Succeeded", "Failed"}:
            break
        time.sleep(0.1)
    else:
        timed_out = True

    event_payload = _events(pod_name)
    logs = _kubectl(["logs", pod_name, "-n", NAMESPACE, "--all-containers=true"])
    log_text = logs.stdout or logs.stderr
    payload = _parse_payload(log_text)
    if final_pod is None:
        final_pod = _get_json(["get", "pod", pod_name, "-n", NAMESPACE])
    evidence = extract_pod_evidence(final_pod, event_payload)
    exclusion = _infrastructure_exclusion(evidence)
    pod_reason = (final_pod or {}).get("status", {}).get("reason")
    if pod_reason in {"Evicted", "NodeLost", "Shutdown"}:
        exclusion = f"node_infrastructure_failure:{pod_reason}"
    if final_pod is None:
        exclusion = "pod_unavailable_after_create"
    delete = _kubectl(
        ["delete", "pod", pod_name, "-n", NAMESPACE, "--wait=true", "--timeout=60s"]
    )
    cleanup_status = "completed" if delete.returncode == 0 else "failed"
    elapsed = time.monotonic() - controller_started
    resources = PROFILE_RESOURCES[item.applied_profile]
    cgroup = (payload or {}).get("cgroup_metrics", {})
    workload_payload = (payload or {}).get("workload", {})
    actual_peak = cgroup.get("peak_memory_mib")
    observed = _observed_profile(evidence)
    success = bool(
        not timed_out
        and exclusion is None
        and evidence.get("phase") == "Succeeded"
        and evidence.get("termination_exit_code") == 0
        and payload is not None
        and workload_payload.get("checksum")
        and observed == item.applied_profile
    )
    supporting_paths = [
        run_dir / "pod.log",
        run_dir / "pod-evidence.json",
        run_dir / "metrics-server-snapshots.json",
    ]
    timeout_outcome = bool(
        timed_out
        or evidence.get("termination_reason") == "DeadlineExceeded"
        or (payload or {}).get("status") == "timeout"
    )
    if success:
        failure_category = "success"
    elif evidence.get("oom_killed"):
        failure_category = "oom_killed"
    elif timeout_outcome:
        failure_category = "timeout"
    elif exclusion is not None:
        failure_category = "infrastructure_failure"
    elif payload is None or observed != item.applied_profile:
        failure_category = "validation_failure"
    else:
        failure_category = "workload_failure"
    _write_new(run_dir / "pod.log", log_text)
    _write_json_new(run_dir / "pod-evidence.json", evidence)
    _write_json_new(
        run_dir / "metrics-server-snapshots.json",
        {"source": "metrics_server_secondary_only", "snapshots": snapshots},
    )
    record = {
        "schema_version": "3.0.0",
        "protocol_version": "3.0.0",
        "experiment_path": "direct_pod",
        "experiment_kind": item.experiment_kind,
        "run_id": item.run_id,
        "plan_index": item.plan_index,
        "evaluation_set": item.evaluation_set,
        "workload_id": item.workload_id,
        "repeat_index": item.repeat_index,
        "random_seed": item.random_seed,
        "method": item.method,
        "recommended_profile": item.recommended_profile,
        "applied_profile": item.applied_profile,
        "observed_profile": observed,
        "recommendation_reasons": item.recommendation_reasons,
        "context_signal_summary": item.context_signal_summary,
        "cpu_request_m": resources["cpu_request_m"],
        "cpu_limit_m": resources["cpu_limit_m"],
        "memory_request_mi": resources["memory_request_mi"],
        "memory_limit_mi": resources["memory_limit_mi"],
        "target_cgroup_mib": int(workload["target_cgroup_mib"]),
        "target_band_mib": list(workload["target_band_mib"]),
        "actual_cgroup_peak_mib": actual_peak,
        "useful_allocation_bytes": workload_payload.get("useful_allocation_bytes"),
        "pressure_padding_bytes": workload_payload.get("pressure_padding_bytes"),
        "hold_seconds": float(workload["hold_seconds"]),
        "cpu_full_window_average_m": cgroup.get("cpu_full_window_average_m"),
        "cpu_interval_sample_max_m": cgroup.get("cpu_interval_sample_max_m"),
        "cpu_nr_periods_delta": cgroup.get("cpu_nr_periods_delta"),
        "cpu_nr_throttled_delta": cgroup.get("cpu_nr_throttled_delta"),
        "cpu_throttled_usec_delta": cgroup.get("cpu_throttled_usec_delta"),
        "cgroup_sample_interval_seconds": cgroup.get("sample_interval_seconds"),
        "cgroup_sample_count": cgroup.get("sample_count"),
        "benchmark_runtime_seconds": workload_payload.get("elapsed_seconds"),
        "pod_pending_duration_seconds": evidence.get("pod_pending_duration_seconds"),
        "spawn_latency_seconds": None,
        "time_to_outcome_seconds": round(elapsed, 6),
        "phase": evidence.get("phase"),
        "exit_code": evidence.get("termination_exit_code"),
        "exit_reason": "Timeout" if timed_out else evidence.get("termination_reason"),
        "oom_killed": bool(evidence.get("oom_killed")),
        "timeout": timeout_outcome,
        "restart_count": int(evidence.get("restart_count") or 0),
        "checksum": workload_payload.get("checksum"),
        "success": success,
        "infrastructure_invalid": exclusion is not None,
        "exclusion_reason": exclusion,
        "replacement_run_id": (
            f"{item.run_id}-replacement01" if exclusion is not None else None
        ),
        "cleanup_status": cleanup_status,
        "failure_category": failure_category,
        "git_commit": current_git_commit(ROOT),
        "container_image": image,
        "configuration_identity": None,
        "input_sha256": _input_sha256(workload),
        "supporting_evidence_sha256": _supporting_checksums(supporting_paths),
        "supporting_log_paths": [
            str(path.relative_to(ROOT)) for path in supporting_paths
        ],
        "timestamp_created": created_at,
        "timestamp_recorded": _utc_now(),
    }
    validate_record(record)
    _write_json_new(run_dir / "record.json", record)
    return record


def _write_integrity_manifest(experiment_dir: Path) -> Path:
    path = experiment_dir / "SHA256SUMS"
    lines = []
    for candidate in sorted(experiment_dir.rglob("*")):
        if candidate.is_file() and candidate != path:
            lines.append(f"{_sha256(candidate)}  {candidate.relative_to(experiment_dir)}\n")
    if path.exists():
        raise FileExistsError(f"refusing to overwrite integrity manifest {path}")
    _write_new(path, "".join(lines))
    return path


def _dry_run_summary(plan: list[PlanItem], kind: str) -> dict[str, Any]:
    return {
        "protocol_version": "3.0.0",
        "kind": kind,
        "namespace": NAMESPACE,
        "required_context": REQUIRED_CONTEXT,
        "master_seed": MASTER_SEED,
        "planned_trials": len(plan),
        "workloads": len({item.workload_id for item in plan}),
        "methods": sorted({item.method for item in plan}),
        "profiles": sorted({item.applied_profile for item in plan}),
        "cluster_mutation": False,
        "first_five": [asdict(item) for item in plan[:5]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Protocol-v3 direct-pod experiment runner.")
    parser.add_argument("--kind", choices=("calibration", "ground-truth", "comparative"), required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--image", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--experiment-dir", type=Path)
    parser.add_argument("--preflight-report", type=Path)
    parser.add_argument("--calibration-evidence", type=Path)
    parser.add_argument("--ground-truth-evidence", type=Path)
    parser.add_argument("--calibration-round", type=int, choices=(1, 2), default=1)
    args = parser.parse_args(argv)

    plan = generate_plan(args.kind, args.experiment_id)
    if args.dry_run:
        print(json.dumps(_dry_run_summary(plan, args.kind), indent=2, sort_keys=True))
        return 0
    if args.preflight_only:
        if args.preflight_report is None:
            raise ValueError("--preflight-report is required with --preflight-only")
        return 0 if _write_preflight_report(
            args.image, args.preflight_report.resolve()
        ) else 2

    prerequisite_integrity: dict[str, Any] = {}
    if args.kind in {"ground-truth", "comparative"}:
        if args.calibration_evidence is None:
            raise ValueError(
                f"--calibration-evidence is required before {args.kind} execution"
            )
        from cluster_evaluation.analyze_v3 import _load_records, validate_calibration
        from cluster_evaluation.evidence_v3 import validate_experiment

        calibration_report = validate_experiment(
            args.calibration_evidence, "calibration"
        )
        gate = validate_calibration(_load_records(args.calibration_evidence))
        if gate["status"] != "pass":
            raise RuntimeError("calibration gate failed; hold-out execution is forbidden")
        prerequisite_integrity["calibration"] = calibration_report["integrity"]
    if args.kind == "comparative":
        if args.ground_truth_evidence is None:
            raise ValueError(
                "--ground-truth-evidence is required before comparative execution"
            )
        from cluster_evaluation.evidence_v3 import validate_experiment

        ground_report = validate_experiment(
            args.ground_truth_evidence, "ground-truth"
        )
        prerequisite_integrity["ground_truth"] = ground_report["integrity"]

    experiment_dir = (
        args.experiment_dir
        or ROOT / "results" / "cluster" / "raw-v3" / args.experiment_id
    ).resolve()
    if not _inside_repository(experiment_dir):
        raise ValueError("v3 evidence directory must be inside the repository")
    if experiment_dir.exists():
        raise FileExistsError(f"refusing to overwrite experiment directory {experiment_dir}")
    environment = _preflight(args.image)
    experiment_dir.mkdir(parents=True, exist_ok=False)
    matrix_text = "".join(
        json.dumps(asdict(item), sort_keys=True) + "\n" for item in plan
    )
    environment.update(
        {
            "experiment_kind": args.kind,
            "calibration_round": (
                args.calibration_round if args.kind == "calibration" else None
            ),
            "repeat_count": 3 if args.kind == "calibration" else 5,
            "planned_trials": len(plan),
            "matrix_sha256": hashlib.sha256(matrix_text.encode("utf-8")).hexdigest(),
            "analysis_path": str(ANALYSIS.relative_to(ROOT)),
            "analysis_sha256": _sha256(ANALYSIS),
            "protocol_path": str(PROTOCOL.relative_to(ROOT)),
            "protocol_sha256": _sha256(PROTOCOL),
            "helm_chart_version": "4.0.0",
            "exclusion_policy": (
                "OOM/timeout/workload failures retained; only independent "
                "infrastructure failures replaced once with the same seed"
            ),
            "prerequisite_integrity": prerequisite_integrity,
        }
    )
    _write_json_new(experiment_dir / "environment.json", environment)
    _write_new(experiment_dir / "matrix.jsonl", matrix_text)

    workloads = {item["workload_id"]: item for item in load_workloads()}
    invalid_count = 0
    consecutive_invalid = 0
    replacements: list[PlanItem] = []
    checksum_by_pair: dict[tuple[str, int], str] = {}
    for item in plan:
        live = _preflight(args.image)
        if live["manifest_sha256"] != environment["manifest_sha256"]:
            raise RuntimeError("manifest hash drifted after preregistration")
        run_dir = experiment_dir / "runs" / item.run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        record = run_item(item, workloads[item.workload_id], args.image, run_dir)
        _append_jsonl(experiment_dir / "results.jsonl", record)
        if record["infrastructure_invalid"]:
            invalid_count += 1
            consecutive_invalid += 1
            replacements.append(
                PlanItem(
                    **{
                        **asdict(item),
                        "plan_index": len(plan) + len(replacements),
                        "run_id": record["replacement_run_id"],
                    }
                )
            )
        else:
            consecutive_invalid = 0
        if record["checksum"]:
            pair = (item.workload_id, item.repeat_index)
            previous = checksum_by_pair.setdefault(pair, record["checksum"])
            if previous != record["checksum"]:
                raise RuntimeError("same-seed workload checksum mismatch")
        if record["cleanup_status"] == "failed":
            raise RuntimeError("cleanup failed; protocol stops before the next trial")
        if (
            item.applied_profile == "large"
            and record["exclusion_reason"]
            and record["exclusion_reason"].startswith("node_infrastructure_failure:")
        ):
            raise RuntimeError("Large trial caused or encountered node-level eviction")
        if record["exit_code"] in {70, 124}:
            raise RuntimeError("workload allocation/deadline safety guard fired")
        if args.kind == "calibration" and record["oom_killed"]:
            minimum = workloads[item.workload_id]["expected_minimum_profile"]
            expected_success = (
                item.workload_id == "cal_cpu_units"
                or PROFILE_ORDER.index(item.applied_profile)
                >= PROFILE_ORDER.index(minimum)
            )
            if expected_success:
                raise RuntimeError("expected-success calibration profile OOM-killed")
        completed = item.plan_index + 1
        if consecutive_invalid >= 3 or (
            completed >= 10 and invalid_count / completed > 0.10
        ):
            raise RuntimeError("infrastructure-invalid stop threshold reached")
        print(
            json.dumps(
                {
                    "completed": item.plan_index + 1,
                    "planned": len(plan),
                    "run_id": item.run_id,
                    "success": record["success"],
                    "oom_killed": record["oom_killed"],
                }
            ),
            flush=True,
        )
    if replacements:
        _write_new(
            experiment_dir / "replacement-matrix.jsonl",
            "".join(json.dumps(asdict(item), sort_keys=True) + "\n" for item in replacements),
        )
    for item in replacements:
        live = _preflight(args.image)
        if live["manifest_sha256"] != environment["manifest_sha256"]:
            raise RuntimeError("manifest hash drifted before replacement")
        run_dir = experiment_dir / "runs" / item.run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        record = run_item(item, workloads[item.workload_id], args.image, run_dir)
        _append_jsonl(experiment_dir / "results.jsonl", record)
        if record["infrastructure_invalid"]:
            raise RuntimeError("the single permitted infrastructure replacement also failed")
        if record["cleanup_status"] == "failed":
            raise RuntimeError("replacement cleanup failed")
    _write_integrity_manifest(experiment_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
