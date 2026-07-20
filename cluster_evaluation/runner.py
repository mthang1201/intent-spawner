"""Run immutable, sanitized Kubernetes-backed experiment matrices."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import time
from typing import Any

import yaml

from experiments.kubernetes_evidence import extract_pod_evidence, parse_memory_mi
from experiments.result_schema import current_git_commit
from cluster_evaluation.policies import METHODS, PROFILES, PROFILE_RESOURCES, decide_cluster_method


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "workloads.yaml"
NAMESPACE = "z2jh-context-demo"
REQUIRED_CONTEXT = "intent-spawner-eval"


@dataclass(frozen=True)
class PlanItem:
    plan_index: int
    run_id: str
    experiment_kind: str
    method: str
    workload_id: str
    repeat_index: int
    random_seed: int
    recommended_profile: str | None
    applied_profile: str
    recommendation_reasons: list[str]
    policy_warnings: list[str]
    context_signal_summary: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _run_kubectl(args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", "--context", REQUIRED_CONTEXT, *args],
        cwd=ROOT,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def _write_json_new(path: Path, payload: Any) -> None:
    _write_new(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def load_workloads() -> list[dict[str, Any]]:
    with MANIFEST.open(encoding="utf-8") as handle:
        return list(yaml.safe_load(handle)["workloads"])


def _seed(workload: dict[str, Any], seed_offset: int, repeat: int) -> int:
    return int(workload["deterministic_seed"]) + seed_offset * 10_000 + repeat


def generate_plan(kind: str, repeats: int, seed_offset: int, experiment_id: str) -> list[PlanItem]:
    workloads = load_workloads()
    pending: list[PlanItem] = []
    for workload in workloads:
        if kind == "ground-truth":
            decisions = [
                (
                    "profile_sweep",
                    None,
                    profile,
                    ["forced profile sweep independent of recommender output"],
                    [],
                    {
                        "raw_context_stored": False,
                        "raw_context_available": False,
                        "hint_count": 0,
                        "detected_terms": [],
                        "dataset_size_signal_used": False,
                    },
                )
                for profile in PROFILES
            ]
        elif kind == "comparative":
            decisions = []
            for method in METHODS:
                decision = decide_cluster_method(method, workload)
                decisions.append(
                    (
                        method,
                        decision.recommended_profile,
                        decision.applied_profile,
                        decision.recommendation_reasons,
                        decision.policy_warnings,
                        decision.context_signal_summary,
                    )
                )
        else:
            raise ValueError(f"unsupported experiment kind {kind!r}")

        for repeat in range(repeats):
            for method, recommended, applied, reasons, warnings, summary in decisions:
                run_id = f"{experiment_id}-{method}-{workload['workload_id']}-r{repeat:02d}-{applied}"
                pending.append(
                    PlanItem(
                        plan_index=-1,
                        run_id=run_id,
                        experiment_kind=kind,
                        method=method,
                        workload_id=workload["workload_id"],
                        repeat_index=repeat,
                        random_seed=_seed(workload, seed_offset, repeat),
                        recommended_profile=recommended,
                        applied_profile=applied,
                        recommendation_reasons=list(reasons),
                        policy_warnings=list(warnings),
                        context_signal_summary=dict(summary),
                    )
                )

    random.Random(seed_offset).shuffle(pending)
    return [PlanItem(**{**asdict(item), "plan_index": index}) for index, item in enumerate(pending)]


def _pod_name(run_id: str) -> str:
    return "ce-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:20]


def build_pod_spec(item: PlanItem, workload: dict[str, Any], image: str, hold_seconds: float = 0) -> dict[str, Any]:
    resources = PROFILE_RESOURCES[item.applied_profile]
    scale = str(workload["workload"]["scale"])
    env = [
        {"name": "RECOMMENDED_PROFILE", "value": item.recommended_profile or "none"},
        {"name": "CONTEXT_DATASET_SIZE_GB", "value": str(workload["dataset_size_hint_gb"])},
    ]
    if item.method == "static_default":
        env.append({"name": "SELECTED_STATIC_PROFILE", "value": item.applied_profile})
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": _pod_name(item.run_id),
            "namespace": NAMESPACE,
            "annotations": {
                "z2jh-context-demo.local/run-id": item.run_id,
                "z2jh-context-demo.local/method": item.method,
                "z2jh-context-demo.local/applied-profile": item.applied_profile,
            },
            "labels": {"app.kubernetes.io/name": "intent-spawner-cluster-evaluation"},
        },
        "spec": {
            "restartPolicy": "Never",
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
                        "--scale",
                        scale,
                        "--seed",
                        str(item.random_seed),
                        "--sample-interval",
                        "0.01",
                        "--hold-seconds",
                        str(hold_seconds),
                    ],
                    "env": env,
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
            "volumes": [{"name": "tmp", "emptyDir": {}}],
        },
    }


def _get_json(args: list[str]) -> dict[str, Any] | None:
    result = _run_kubectl([*args, "-o", "json"])
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def _metric_snapshot(pod_name: str) -> dict[str, Any] | None:
    result = _run_kubectl(
        ["get", "--raw", f"/apis/metrics.k8s.io/v1beta1/namespaces/{NAMESPACE}/pods/{pod_name}"]
    )
    if result.returncode != 0:
        return None
    payload = json.loads(result.stdout)
    return {
        "timestamp": _utc_now(),
        "containers": [
            {
                "name": container.get("name"),
                "cpu": container.get("usage", {}).get("cpu"),
                "memory": container.get("usage", {}).get("memory"),
            }
            for container in payload.get("containers", [])
        ],
    }


def _parse_log(log_text: str) -> dict[str, Any] | None:
    for line in reversed(log_text.splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _parse_cpu_quantity_m(value: str | None) -> float | None:
    if not value:
        return None
    if value.endswith("n"):
        return float(value[:-1]) / 1_000_000
    if value.endswith("u"):
        return float(value[:-1]) / 1000
    if value.endswith("m"):
        return float(value[:-1])
    return float(value) * 1000


def _metric_server_peaks(snapshots: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    cpus: list[float] = []
    memories: list[int] = []
    for snapshot in snapshots:
        for container in snapshot["containers"]:
            cpu = _parse_cpu_quantity_m(container.get("cpu"))
            memory = parse_memory_mi(container.get("memory"))
            if cpu is not None:
                cpus.append(cpu)
            if memory is not None:
                memories.append(memory)
    return (max(cpus) if cpus else None, max(memories) if memories else None)


def _collect_events(pod_name: str) -> dict[str, Any]:
    return _get_json(
        ["get", "events", "-n", NAMESPACE, "--field-selector", f"involvedObject.name={pod_name}"]
    ) or {"items": []}


def run_item(
    item: PlanItem,
    workload: dict[str, Any],
    image: str,
    run_dir: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    pod_spec = build_pod_spec(item, workload, image)
    pod_name = pod_spec["metadata"]["name"]
    created_at = _utc_now()
    create = _run_kubectl(["create", "-f", "-"], input_text=json.dumps(pod_spec))
    if create.returncode != 0:
        raise RuntimeError(f"pod creation failed for {item.run_id}: {create.stderr or create.stdout}")

    deadline = time.monotonic() + timeout_seconds
    snapshots: list[dict[str, Any]] = []
    final_pod: dict[str, Any] | None = None
    timed_out = False
    next_metric = 0.0
    while time.monotonic() < deadline:
        pod = _get_json(["get", "pod", pod_name, "-n", NAMESPACE])
        if pod is None:
            time.sleep(0.1)
            continue
        final_pod = pod
        phase = pod.get("status", {}).get("phase")
        if time.monotonic() >= next_metric and phase == "Running":
            snapshot = _metric_snapshot(pod_name)
            if snapshot is not None:
                snapshots.append(snapshot)
            next_metric = time.monotonic() + 0.5
        if phase in {"Succeeded", "Failed"}:
            break
        time.sleep(0.1)
    else:
        timed_out = True

    events = _collect_events(pod_name)
    log_result = _run_kubectl(["logs", pod_name, "-n", NAMESPACE, "--all-containers=true"])
    log_text = log_result.stdout or log_result.stderr
    pod_payload = _parse_log(log_text)
    if final_pod is None:
        final_pod = _get_json(["get", "pod", pod_name, "-n", NAMESPACE])
    evidence = extract_pod_evidence(final_pod, events)
    server_peak_cpu, server_peak_memory = _metric_server_peaks(snapshots)

    delete = _run_kubectl(["delete", "pod", pod_name, "-n", NAMESPACE, "--wait=true", "--timeout=60s"])
    cleanup_status = "completed" if delete.returncode == 0 else "failed"
    cgroup = (pod_payload or {}).get("cgroup_metrics", {})
    resources = PROFILE_RESOURCES[item.applied_profile]
    peak_cpu = cgroup.get("peak_cpu_m")
    peak_memory = cgroup.get("peak_memory_mi")
    success = bool(
        not timed_out
        and evidence.get("phase") == "Succeeded"
        and evidence.get("termination_exit_code") == 0
        and pod_payload is not None
    )
    supporting = [
        _display_path(run_dir / name)
        for name in ("pod.log", "pod-evidence.json", "metrics-server-snapshots.json")
    ]
    record = {
        "cluster_schema_version": "1.0.0",
        "run_id": item.run_id,
        "experiment_kind": item.experiment_kind,
        "plan_index": item.plan_index,
        "timestamp_created": created_at,
        "timestamp_recorded": _utc_now(),
        "git_commit": current_git_commit(ROOT),
        "environment_id": "minikube-intent-spawner-eval",
        "method": item.method,
        "workload_id": item.workload_id,
        "repeat_index": item.repeat_index,
        "random_seed": item.random_seed,
        "recommended_profile": item.recommended_profile,
        "applied_profile": item.applied_profile,
        "recommendation_reasons": item.recommendation_reasons,
        "policy_warnings": item.policy_warnings,
        "context_signal_summary": item.context_signal_summary,
        "cpu_request_m": resources["cpu_request_m"],
        "cpu_limit_m": resources["cpu_limit_m"],
        "memory_request_mi": resources["memory_request_mi"],
        "memory_limit_mi": resources["memory_limit_mi"],
        "peak_cpu_m": peak_cpu,
        "peak_memory_mi": peak_memory,
        "resource_measurement_source": cgroup.get("source", "not_available"),
        "cgroup_sample_interval_seconds": cgroup.get("sample_interval_seconds"),
        "cgroup_sample_count": cgroup.get("sample_count"),
        "metrics_server_peak_cpu_m": server_peak_cpu,
        "metrics_server_peak_memory_mi": server_peak_memory,
        "metrics_server_snapshot_count": len(snapshots),
        "pod_pending_duration_seconds": evidence.get("pod_pending_duration_seconds"),
        "pending_reasons": evidence.get("scheduling_or_pending_reasons", []),
        "workload_runtime_seconds": evidence.get("workload_runtime_seconds"),
        "benchmark_runtime_seconds": (pod_payload or {}).get("workload_elapsed_seconds"),
        "time_to_success_seconds": evidence.get("time_to_success_seconds"),
        "oom_killed": evidence.get("oom_killed"),
        "restart_or_respawn_count": evidence.get("restart_count"),
        "success": success,
        "timeout": timed_out,
        "exit_reason": "Timeout" if timed_out else evidence.get("termination_reason"),
        "exit_code": evidence.get("termination_exit_code"),
        "cleanup_status": cleanup_status,
        "container_image": image,
        "container_image_id": ((final_pod or {}).get("status", {}).get("containerStatuses") or [{}])[0].get("imageID"),
        "memory_request_to_peak_ratio": (
            None if not peak_memory else round(float(resources["memory_request_mi"]) / peak_memory, 6)
        ),
        "memory_reservation_waste_ratio": (
            None
            if not peak_memory
            else round(max(0.0, (float(resources["memory_request_mi"]) - peak_memory) / float(resources["memory_request_mi"])), 6)
        ),
        "supporting_log_paths": supporting,
        "kubernetes_evidence": evidence,
    }
    _write_new(run_dir / "pod.log", log_text)
    _write_json_new(run_dir / "pod-evidence.json", evidence)
    _write_json_new(
        run_dir / "metrics-server-snapshots.json",
        {"source": "metrics_server_v0.7.2", "snapshots": snapshots},
    )
    _write_json_new(run_dir / "record.json", record)
    return record


def _preflight(image: str) -> dict[str, Any]:
    context = _run_kubectl(["config", "current-context"])
    if context.returncode != 0 or context.stdout.strip() != REQUIRED_CONTEXT:
        raise RuntimeError(f"current context must be {REQUIRED_CONTEXT!r}")
    namespace = _run_kubectl(["get", "namespace", NAMESPACE])
    if namespace.returncode != 0:
        raise RuntimeError(f"required namespace {NAMESPACE!r} does not exist")
    metrics = _run_kubectl(["get", "apiservice", "v1beta1.metrics.k8s.io", "-o", "json"])
    if metrics.returncode != 0:
        raise RuntimeError("Metrics API is unavailable")
    node = _get_json(["get", "node", REQUIRED_CONTEXT])
    if node is None:
        raise RuntimeError("evaluation node is unavailable")
    return {
        "captured_at": _utc_now(),
        "required_context": REQUIRED_CONTEXT,
        "namespace": NAMESPACE,
        "container_image": image,
        "git_commit": current_git_commit(ROOT),
        "git_dirty": bool(subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout),
        "node_capacity": node["status"]["capacity"],
        "node_allocatable": node["status"]["allocatable"],
        "node_info": node["status"]["nodeInfo"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("ground-truth", "comparative"), required=True)
    parser.add_argument("--experiment-dir", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--repeats", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    experiment_dir = args.experiment_dir.resolve()
    results_path = experiment_dir / "results.jsonl"
    workloads = {item["workload_id"]: item for item in load_workloads()}
    if args.resume:
        plan = [PlanItem(**json.loads(line)) for line in (experiment_dir / "matrix.jsonl").read_text().splitlines()]
        completed = {
            json.loads(line)["run_id"] for line in results_path.read_text(encoding="utf-8").splitlines()
        } if results_path.exists() else set()
    else:
        experiment_dir.mkdir(parents=True, exist_ok=False)
        plan = generate_plan(args.kind, args.repeats, args.seed, experiment_dir.name)
        _write_new(
            experiment_dir / "matrix.jsonl",
            "".join(json.dumps(asdict(item), sort_keys=True) + "\n" for item in plan),
        )
        _write_json_new(experiment_dir / "environment.json", _preflight(args.image))
        completed = set()

    for item in plan:
        if item.run_id in completed:
            continue
        run_dir = experiment_dir / "runs" / item.run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        record = run_item(item, workloads[item.workload_id], args.image, run_dir, args.timeout)
        _append_jsonl(results_path, record)
        print(json.dumps({"completed": item.plan_index + 1, "planned": len(plan), "run_id": item.run_id, "success": record["success"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
