"""Run the preregistered v2 request-capacity experiment on a disposable cluster."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import subprocess
import time
from typing import Any

from cluster_evaluation.policies import METHODS, PROFILE_RESOURCES, decide_cluster_method
from cluster_evaluation.runner import PlanItem, build_pod_spec, load_workloads
from experiments.kubernetes_evidence import extract_pod_evidence
from experiments.result_schema import current_git_commit


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_VERSION = "2.0.0"
REQUIRED_CONTEXT = "intent-spawner-capacity-v2"
NAMESPACE = "z2jh-context-demo"
NAMESPACE_SAFETY_LABEL = "z2jh-context-demo.local/disposable-capacity-v2"
EXPERIMENT_LABEL = "z2jh-context-demo.local/capacity-experiment"
BATCH_LABEL = "z2jh-context-demo.local/capacity-batch"
METHOD_ORDERS = (
    ("static_default", "intent_only", "context_aware"),
    ("intent_only", "context_aware", "static_default"),
    ("context_aware", "static_default", "intent_only"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _run_kubectl(
    context: str,
    args: list[str],
    *,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", "--context", context, *args],
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


def _append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def _minikube_profile(context: str) -> dict[str, Any]:
    result = subprocess.run(
        ["minikube", "profile", "list", "-o", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "cannot inspect Minikube profiles")
    profiles = json.loads(result.stdout).get("valid", [])
    profile = next((item for item in profiles if item.get("Name") == context), None)
    if profile is None:
        raise RuntimeError(f"Minikube profile {context!r} is not present")
    config = profile.get("Config", {})
    kubernetes = config.get("KubernetesConfig", {})
    return {
        "name": profile.get("Name"),
        "cpus": config.get("CPUs"),
        "memory_mb": config.get("Memory"),
        "disk_size_mb": config.get("DiskSize"),
        "driver": config.get("Driver"),
        "base_image": config.get("KicBaseImage"),
        "kubernetes_version": kubernetes.get("KubernetesVersion"),
        "container_runtime": kubernetes.get("ContainerRuntime"),
        "network_plugin": kubernetes.get("NetworkPlugin"),
        "cni": kubernetes.get("CNI"),
        "service_cidr": kubernetes.get("ServiceCIDR"),
        "feature_gates": kubernetes.get("FeatureGates"),
        "extra_options": kubernetes.get("ExtraOptions"),
    }


def _local_image_metadata(image: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or f"cannot inspect container image {image!r}")
    payload = json.loads(result.stdout)
    if len(payload) != 1:
        raise RuntimeError(f"expected one local image for {image!r}, found {len(payload)}")
    inspected = payload[0]
    return {
        "reference": image,
        "local_image_id": inspected.get("Id"),
        "repo_digests": inspected.get("RepoDigests") or [],
        "repo_tags": inspected.get("RepoTags") or [],
    }


def _parse_log(log_text: str) -> dict[str, Any] | None:
    for line in reversed(log_text.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("pod_runner_schema_version"):
            return payload
    return None


def method_order(repeat_index: int) -> tuple[str, ...]:
    base = METHOD_ORDERS[repeat_index % len(METHOD_ORDERS)]
    return tuple(base)


def generate_capacity_plan(
    *,
    repeats: int,
    seed: int,
    experiment_id: str,
) -> list[dict[str, Any]]:
    if repeats <= 0:
        raise ValueError("repeats must be positive")
    workloads = load_workloads()
    plan: list[dict[str, Any]] = []
    plan_index = 0
    for repeat_index in range(repeats):
        for method in method_order(repeat_index):
            batch_id = f"cap-v2-r{repeat_index:02d}-{method.replace('_', '-')}"
            batch_items: list[PlanItem] = []
            for workload in workloads:
                decision = decide_cluster_method(method, workload)
                run_id = (
                    f"{experiment_id}-{method}-{workload['workload_id']}-"
                    f"r{repeat_index:02d}-{decision.applied_profile}"
                )
                batch_items.append(
                    PlanItem(
                        plan_index=-1,
                        run_id=run_id,
                        experiment_kind="capacity-v2",
                        method=method,
                        workload_id=workload["workload_id"],
                        repeat_index=repeat_index,
                        random_seed=int(workload["deterministic_seed"]) + seed * 10_000 + repeat_index,
                        recommended_profile=decision.recommended_profile,
                        applied_profile=decision.applied_profile,
                        recommendation_reasons=list(decision.recommendation_reasons),
                        policy_warnings=list(decision.policy_warnings),
                        context_signal_summary=dict(decision.context_signal_summary),
                    )
                )
            random.Random(seed + repeat_index * 100 + METHODS.index(method)).shuffle(batch_items)
            for item in batch_items:
                item = replace(item, plan_index=plan_index)
                plan.append({**asdict(item), "batch_id": batch_id})
                plan_index += 1
    return plan


def _cleanup(context: str, namespace: str, experiment_id: str) -> dict[str, Any]:
    selector = f"{EXPERIMENT_LABEL}={experiment_id}"
    result = _run_kubectl(
        context,
        ["delete", "pod", "-n", namespace, "-l", selector, "--wait=true", "--timeout=90s"],
    )
    return {
        "selector": selector,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "status": "completed" if result.returncode == 0 else "failed",
    }


def _preflight(
    *,
    context: str,
    namespace: str,
    image: str,
    expected_node_cpu: str,
    expected_node_memory_ki: int,
    repeats: int,
    population_size: int,
    hold_seconds: float,
    sample_interval_seconds: float,
    expected_minikube_driver: str,
    expected_kubernetes_version: str,
    expected_container_runtime: str,
    expected_minikube_cpus: int,
    expected_minikube_memory_mb: int,
    expected_minikube_disk_size_mb: int,
) -> dict[str, Any]:
    if context != REQUIRED_CONTEXT:
        raise RuntimeError(f"capacity runner requires disposable context {REQUIRED_CONTEXT!r}")
    if namespace != NAMESPACE:
        raise RuntimeError(f"capacity runner requires namespace {NAMESPACE!r}")
    status = subprocess.run(
        ["git", "status", "--short", "--untracked-files=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if status:
        raise RuntimeError("capacity experiment requires a clean tracked worktree")

    git_commit = current_git_commit(ROOT)
    if git_commit[:12] not in image:
        raise RuntimeError(
            "container image reference must include the first 12 characters of the "
            f"preregistered Git commit ({git_commit[:12]})"
        )
    minikube_profile = _minikube_profile(context)
    expected_profile = {
        "driver": expected_minikube_driver,
        "kubernetes_version": expected_kubernetes_version,
        "container_runtime": expected_container_runtime,
        "cpus": expected_minikube_cpus,
        "memory_mb": expected_minikube_memory_mb,
        "disk_size_mb": expected_minikube_disk_size_mb,
    }
    profile_mismatches = {
        key: {"observed": minikube_profile.get(key), "expected": expected}
        for key, expected in expected_profile.items()
        if minikube_profile.get(key) != expected
    }
    if profile_mismatches:
        raise RuntimeError(f"Minikube profile differs from protocol: {profile_mismatches}")
    image_metadata = _local_image_metadata(image)

    namespace_result = _run_kubectl(context, ["get", "namespace", namespace, "-o", "json"])
    if namespace_result.returncode != 0:
        raise RuntimeError(f"required namespace {namespace!r} does not exist")
    namespace_payload = json.loads(namespace_result.stdout)
    labels = namespace_payload.get("metadata", {}).get("labels", {})
    if labels.get(NAMESPACE_SAFETY_LABEL) != "true":
        raise RuntimeError(f"namespace lacks safety label {NAMESPACE_SAFETY_LABEL}=true")

    nodes_result = _run_kubectl(context, ["get", "nodes", "-o", "json"])
    if nodes_result.returncode != 0:
        raise RuntimeError(nodes_result.stderr or "cannot read evaluation node")
    nodes = json.loads(nodes_result.stdout).get("items", [])
    if len(nodes) != 1:
        raise RuntimeError(f"expected exactly one disposable node, found {len(nodes)}")
    node = nodes[0]
    allocatable = node.get("status", {}).get("allocatable", {})
    if str(allocatable.get("cpu")) != expected_node_cpu:
        raise RuntimeError(
            f"node allocatable CPU {allocatable.get('cpu')!r} != {expected_node_cpu!r}"
        )
    if str(allocatable.get("memory")) != f"{expected_node_memory_ki}Ki":
        raise RuntimeError(
            f"node allocatable memory {allocatable.get('memory')!r} != "
            f"{expected_node_memory_ki}Ki"
        )
    quotas = _run_kubectl(context, ["get", "resourcequota", "-n", namespace, "-o", "json"])
    if quotas.returncode != 0 or json.loads(quotas.stdout).get("items"):
        raise RuntimeError("capacity namespace must have no ResourceQuota")

    node_info = node.get("status", {}).get("nodeInfo", {})
    return {
        "protocol_version": PROTOCOL_VERSION,
        "captured_at": _utc_now(),
        "required_context": context,
        "namespace": namespace,
        "namespace_safety_label": f"{NAMESPACE_SAFETY_LABEL}=true",
        "container_image": image,
        "container_image_metadata": image_metadata,
        "git_commit": git_commit,
        "git_dirty": False,
        "minikube_profile": minikube_profile,
        "node_count": 1,
        "node_capacity": node.get("status", {}).get("capacity", {}),
        "node_allocatable": allocatable,
        "node_info": {
            key: node_info.get(key)
            for key in (
                "architecture",
                "containerRuntimeVersion",
                "kernelVersion",
                "kubeletVersion",
                "operatingSystem",
                "osImage",
            )
        },
        "profile_resources": PROFILE_RESOURCES,
        "workload_population": [item["workload_id"] for item in load_workloads()],
        "population_size": population_size,
        "launch_concurrency": population_size,
        "repeats": repeats,
        "method_order_by_repeat": [list(method_order(index)) for index in range(repeats)],
        "capacity_hold_seconds": hold_seconds,
        "phase_sample_interval_seconds": sample_interval_seconds,
        "pending_reason_source": "PodScheduled conditions and namespace-scoped pod events",
        "resource_quota": "none",
        "admission_configuration": "unchanged",
    }


def _pod_json(context: str, namespace: str, pod_name: str) -> dict[str, Any] | None:
    result = _run_kubectl(context, ["get", "pod", pod_name, "-n", namespace, "-o", "json"])
    return json.loads(result.stdout) if result.returncode == 0 else None


def _events_json(context: str, namespace: str, pod_name: str) -> dict[str, Any]:
    result = _run_kubectl(
        context,
        [
            "get",
            "events",
            "-n",
            namespace,
            "--field-selector",
            f"involvedObject.name={pod_name}",
            "-o",
            "json",
        ],
    )
    return json.loads(result.stdout) if result.returncode == 0 else {"items": []}


def _run_batch(
    *,
    context: str,
    namespace: str,
    experiment_id: str,
    batch_id: str,
    items: list[dict[str, Any]],
    workloads: dict[str, dict[str, Any]],
    image: str,
    hold_seconds: float,
    sample_interval_seconds: float,
    timeout_seconds: float,
    output_dir: Path,
    git_commit: str,
) -> dict[str, Any]:
    started_at = _utc_now()
    pod_names: list[str] = []
    cleanup: dict[str, Any] | None = None
    try:
        for raw_item in items:
            item = PlanItem(**{key: raw_item[key] for key in PlanItem.__dataclass_fields__})
            spec = build_pod_spec(
                item,
                workloads[item.workload_id],
                image,
                hold_seconds=hold_seconds,
                namespace=namespace,
            )
            spec["metadata"]["labels"].update(
                {
                    EXPERIMENT_LABEL: experiment_id,
                    BATCH_LABEL: batch_id,
                }
            )
            result = _run_kubectl(context, ["create", "-f", "-"], input_text=json.dumps(spec))
            if result.returncode != 0:
                raise RuntimeError(
                    f"pod creation failed for {item.run_id}: {result.stderr or result.stdout}"
                )
            pod_names.append(spec["metadata"]["name"])

        samples: list[dict[str, Any]] = []
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            result = _run_kubectl(
                context,
                ["get", "pods", "-n", namespace, "-l", f"{BATCH_LABEL}={batch_id}", "-o", "json"],
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr or "cannot sample capacity pod phases")
            pods = json.loads(result.stdout).get("items", [])
            counts = {"Pending": 0, "Running": 0, "Succeeded": 0, "Failed": 0, "Unknown": 0}
            for pod in pods:
                phase = pod.get("status", {}).get("phase") or "Unknown"
                counts[phase if phase in counts else "Unknown"] += 1
            samples.append(
                {
                    "timestamp": _utc_now(),
                    "pending": counts["Pending"],
                    "running": counts["Running"],
                    "succeeded": counts["Succeeded"],
                    "failed": counts["Failed"],
                    "unknown": counts["Unknown"],
                }
            )
            if len(pods) == len(items) and counts["Succeeded"] + counts["Failed"] == len(items):
                break
            time.sleep(sample_interval_seconds)
        else:
            raise TimeoutError(f"batch {batch_id} exceeded timeout_seconds={timeout_seconds:g}")

        pod_records = []
        for raw_item, pod_name in zip(items, pod_names):
            pod = _pod_json(context, namespace, pod_name)
            events = _events_json(context, namespace, pod_name)
            log_result = _run_kubectl(context, ["logs", pod_name, "-n", namespace, "--all-containers=true"])
            log_text = log_result.stdout or log_result.stderr
            payload = _parse_log(log_text)
            evidence = extract_pod_evidence(pod, events)
            pod_dir = output_dir / "batches" / batch_id / "pods" / raw_item["run_id"]
            _write_new(pod_dir / "pod.log", log_text)
            _write_json_new(pod_dir / "pod-evidence.json", evidence)
            _write_json_new(pod_dir / "events.json", events)
            resources = PROFILE_RESOURCES[raw_item["applied_profile"]]
            pod_records.append(
                {
                    "run_id": raw_item["run_id"],
                    "workload_id": raw_item["workload_id"],
                    "method": raw_item["method"],
                    "repeat_index": raw_item["repeat_index"],
                    "recommended_profile": raw_item["recommended_profile"],
                    "applied_profile": raw_item["applied_profile"],
                    "requests_limits": {
                        key: resources[key]
                        for key in (
                            "cpu_request_m",
                            "cpu_limit_m",
                            "memory_request_mi",
                            "memory_limit_mi",
                        )
                    },
                    "phase": evidence.get("phase"),
                    "success": evidence.get("phase") == "Succeeded"
                    and evidence.get("termination_exit_code") == 0,
                    "exit_reason": evidence.get("termination_reason"),
                    "exit_code": evidence.get("termination_exit_code"),
                    "container_image": evidence.get("container_image"),
                    "container_image_id": evidence.get("container_image_id"),
                    "pending_seconds": evidence.get("pod_pending_duration_seconds"),
                    "pending_reasons": evidence.get("scheduling_or_pending_reasons", []),
                    "timing_source_timestamps": evidence.get("timing_source_timestamps", {}),
                    "timing_timestamp_resolution_seconds": evidence.get(
                        "timing_timestamp_resolution_seconds"
                    ),
                    "timing_durations_are_quantized": evidence.get(
                        "timing_durations_are_quantized"
                    ),
                    "benchmark_runtime_seconds": (payload or {}).get("workload_elapsed_seconds"),
                    "cgroup_metrics": (payload or {}).get("cgroup_metrics", {}),
                    "supporting_log_paths": [
                        str(path.relative_to(ROOT))
                        for path in (
                            pod_dir / "pod.log",
                            pod_dir / "pod-evidence.json",
                            pod_dir / "events.json",
                        )
                    ],
                }
            )
    finally:
        cleanup = _cleanup(context, namespace, experiment_id)

    if cleanup["status"] != "completed":
        raise RuntimeError(f"cleanup failed for {batch_id}: {cleanup['stderr']}")
    completed = sum(bool(pod["success"]) for pod in pod_records)
    record = {
        "capacity_schema_version": "2.0.0",
        "protocol_version": PROTOCOL_VERSION,
        "batch_id": batch_id,
        "experiment_id": experiment_id,
        "method": items[0]["method"],
        "repeat_index": items[0]["repeat_index"],
        "population_size": len(items),
        "launch_concurrency": len(items),
        "hold_seconds": hold_seconds,
        "phase_sample_interval_seconds": sample_interval_seconds,
        "started_at": started_at,
        "recorded_at": _utc_now(),
        "git_commit": git_commit,
        "completed": completed,
        "failed": len(items) - completed,
        "max_concurrent_running": max(sample["running"] for sample in samples),
        "pending_sample_count": len(samples),
        "pending_samples": samples,
        "pods": pod_records,
        "cleanup_status": cleanup["status"],
        "cleanup_selector": cleanup["selector"],
    }
    _write_json_new(output_dir / "batches" / f"{batch_id}.json", record)
    return record


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", type=Path)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--context", default=REQUIRED_CONTEXT)
    parser.add_argument("--namespace", default=NAMESPACE)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260721)
    parser.add_argument("--hold-seconds", type=float, default=20.0)
    parser.add_argument("--sample-interval-seconds", type=float, default=0.3)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--expected-node-cpu", default="6")
    parser.add_argument("--expected-node-memory-ki", type=int, default=6088560)
    parser.add_argument("--expected-minikube-driver", default="docker")
    parser.add_argument("--expected-kubernetes-version", default="v1.33.1")
    parser.add_argument("--expected-container-runtime", default="containerd")
    parser.add_argument("--expected-minikube-cpus", type=int, default=6)
    parser.add_argument("--expected-minikube-memory-mb", type=int, default=6144)
    parser.add_argument("--expected-minikube-disk-size-mb", type=int, default=20000)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cleanup-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    plan = generate_capacity_plan(
        repeats=args.repeats,
        seed=args.seed,
        experiment_id=args.experiment_id,
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "dry-run",
                    "protocol_version": PROTOCOL_VERSION,
                    "context": args.context,
                    "namespace": args.namespace,
                    "planned_pods": len(plan),
                    "planned_batches": args.repeats * len(METHODS),
                    "population_per_batch": len(load_workloads()),
                    "repeats": args.repeats,
                    "hold_seconds": args.hold_seconds,
                    "method_order_by_repeat": [
                        list(method_order(index)) for index in range(args.repeats)
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.cleanup_only:
        result = _cleanup(args.context, args.namespace, args.experiment_id)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "completed" else 1
    if args.experiment_dir is None:
        raise ValueError("--experiment-dir is required unless --dry-run or --cleanup-only is used")

    environment = _preflight(
        context=args.context,
        namespace=args.namespace,
        image=args.image,
        expected_node_cpu=args.expected_node_cpu,
        expected_node_memory_ki=args.expected_node_memory_ki,
        repeats=args.repeats,
        population_size=len(load_workloads()),
        hold_seconds=args.hold_seconds,
        sample_interval_seconds=args.sample_interval_seconds,
        expected_minikube_driver=args.expected_minikube_driver,
        expected_kubernetes_version=args.expected_kubernetes_version,
        expected_container_runtime=args.expected_container_runtime,
        expected_minikube_cpus=args.expected_minikube_cpus,
        expected_minikube_memory_mb=args.expected_minikube_memory_mb,
        expected_minikube_disk_size_mb=args.expected_minikube_disk_size_mb,
    )
    output_dir = args.experiment_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    _write_json_new(output_dir / "environment.json", environment)
    _write_new(
        output_dir / "matrix.jsonl",
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in plan),
    )
    workloads = {item["workload_id"]: item for item in load_workloads()}
    for repeat_index in range(args.repeats):
        for method in method_order(repeat_index):
            batch_id = f"cap-v2-r{repeat_index:02d}-{method.replace('_', '-')}"
            items = [item for item in plan if item["batch_id"] == batch_id]
            batch = _run_batch(
                context=args.context,
                namespace=args.namespace,
                experiment_id=args.experiment_id,
                batch_id=batch_id,
                items=items,
                workloads=workloads,
                image=args.image,
                hold_seconds=args.hold_seconds,
                sample_interval_seconds=args.sample_interval_seconds,
                timeout_seconds=args.timeout_seconds,
                output_dir=output_dir,
                git_commit=environment["git_commit"],
            )
            _append_jsonl(output_dir / "results.jsonl", batch)
            print(
                json.dumps(
                    {
                        "batch_id": batch_id,
                        "completed": batch["completed"],
                        "failed": batch["failed"],
                        "cleanup_status": batch["cleanup_status"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
