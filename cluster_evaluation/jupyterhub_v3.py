"""Plan and execute the protocol-v3 JupyterHub sentinel matrix.

Execution requires an admin API token in an environment variable. The token is
never serialized. Dry-run planning performs no HTTP or Kubernetes calls.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
from urllib import error, parse, request

from benchmarks.resource_envelope_runner import deterministic_seed
from cluster_evaluation.policies import PROFILE_RESOURCES, decide_cluster_method
from cluster_evaluation.runner_v3 import (
    ANALYSIS,
    MASTER_SEED,
    MANIFEST,
    MIN_LOCAL_FREE_BYTES,
    NAMESPACE,
    REQUIRED_NODE_CPU,
    REQUIRED_NODE_MEMORY,
    REQUIRED_CONTEXT,
    SAFETY_LABEL,
    SAFETY_LABEL_VALUE,
    ROOT,
    PROTOCOL,
    _get_json,
    _input_sha256,
    _kubectl,
    _events,
    _node_health,
    _observed_profile,
    _parse_payload,
    _rotated,
    _sha256,
    _write_integrity_manifest,
    _require_frozen_commit,
    load_workloads,
)
from cluster_evaluation.result_schema_v3 import IMAGE_RE, validate_record
from experiments.kubernetes_evidence import extract_pod_evidence
from experiments.result_schema import current_git_commit


METHODS = ("static_default", "intent_only", "context_aware")


@dataclass(frozen=True)
class EndToEndPlanItem:
    plan_index: int
    run_id: str
    experiment_path: str
    evaluation_set: str
    workload_id: str
    repeat_index: int
    random_seed: int
    method: str
    recommended_profile: str
    applied_profile: str
    synthetic_username: str
    recommendation_reasons: list[str]
    context_signal_summary: dict[str, Any]


def _username(run_id: str) -> str:
    return "v3-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]


def generate_plan(experiment_id: str) -> list[EndToEndPlanItem]:
    sentinels = [
        item
        for item in load_workloads()
        if item.get("sentinel_end_to_end") and item["evaluation_set"] == "holdout_core"
    ]
    plan: list[EndToEndPlanItem] = []
    for repeat in range(3):
        for workload in sorted(sentinels, key=lambda item: item["workload_id"]):
            for method in _rotated(METHODS, repeat):
                decision = decide_cluster_method(method, workload)
                run_id = (
                    f"{experiment_id}-jupyterhub-{method}-"
                    f"{workload['workload_id']}-r{repeat:02d}"
                )
                plan.append(
                    EndToEndPlanItem(
                        plan_index=len(plan),
                        run_id=run_id,
                        experiment_path="jupyterhub_e2e",
                        evaluation_set=workload["evaluation_set"],
                        workload_id=workload["workload_id"],
                        repeat_index=repeat,
                        random_seed=deterministic_seed(
                            workload["workload_id"], repeat, MASTER_SEED
                        ),
                        method=method,
                        recommended_profile=decision.recommended_profile or decision.applied_profile,
                        applied_profile=decision.applied_profile,
                        synthetic_username=_username(run_id),
                        recommendation_reasons=decision.recommendation_reasons,
                        context_signal_summary=decision.context_signal_summary,
                    )
                )
    run_ids = [item.run_id for item in plan]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("generated JupyterHub plan contains duplicate run IDs")
    return plan


def _api(
    base_url: str,
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        data=body,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            text = response.read().decode("utf-8")
            return response.status, json.loads(text) if text else None
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8")
        return exc.code, json.loads(text) if text else None


def _preflight(image: str) -> dict[str, Any]:
    if not IMAGE_RE.fullmatch(image):
        raise RuntimeError("end-to-end execution requires an immutable Jupyter image digest")
    current = _kubectl(["config", "current-context"])
    if current.returncode != 0 or current.stdout.strip() != REQUIRED_CONTEXT:
        raise RuntimeError(f"current context must be {REQUIRED_CONTEXT!r}")
    namespace = _get_json(["get", "namespace", NAMESPACE])
    if namespace is None:
        raise RuntimeError("v3 namespace is unavailable")
    labels = namespace.get("metadata", {}).get("labels", {})
    if labels.get(SAFETY_LABEL) != SAFETY_LABEL_VALUE:
        raise RuntimeError("v3 namespace safety label is missing")
    pods = _get_json(["get", "pods", "-n", NAMESPACE]) or {"items": []}
    user_pods = [
        pod
        for pod in pods["items"]
        if pod.get("metadata", {}).get("labels", {}).get("component") == "singleuser-server"
    ]
    if user_pods:
        raise RuntimeError("end-to-end phase must start with no single-user servers")
    _require_frozen_commit()
    if shutil.disk_usage(ROOT).free < MIN_LOCAL_FREE_BYTES:
        raise RuntimeError("less than 5 GiB is free for append-only evidence")
    nodes = _get_json(["get", "nodes"])
    if nodes is None or len(nodes.get("items", [])) != 1:
        raise RuntimeError("end-to-end v3 requires exactly one disposable node")
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
    quotas = _get_json(["get", "resourcequota", "-n", NAMESPACE])
    if quotas is None or quotas.get("items"):
        raise RuntimeError("v3 JupyterHub namespace must have no ResourceQuota")
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
        raise RuntimeError("immutable Jupyter image is not pre-pulled on the node")
    hub = _get_json(["get", "deployment", "hub", "-n", NAMESPACE])
    if hub is None or int(hub.get("status", {}).get("availableReplicas") or 0) != 1:
        raise RuntimeError("the pinned JupyterHub deployment is not available")
    return {
        "schema_version": "3.0.0",
        "protocol_version": "3.0.0",
        "experiment_path": "jupyterhub_e2e",
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
        "container_runtime": node.get("status", {})
        .get("nodeInfo", {})
        .get("containerRuntimeVersion"),
        "jupyterhub_deployment": {
            "name": hub.get("metadata", {}).get("name"),
            "uid": hub.get("metadata", {}).get("uid"),
            "chart": hub.get("metadata", {}).get("labels", {}).get("chart"),
            "image": hub.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [{}])[0]
            .get("image"),
        },
    }


def _write_preflight_report(image: str, path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("preflight report must remain inside the repository") from exc
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
    report: dict[str, Any] = {
        "schema_version": "3.0.0",
        "protocol_version": "3.0.0",
        "captured_at": now,
        "mode": "read_only_jupyterhub_preflight",
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
            "Satisfy the reported frozen protocol precondition and create a new "
            "append-only preflight report."
        )
        report["commands_not_executed"] = [
            "JupyterHub user creation",
            "JupyterHub server spawn",
            "kubectl exec",
            "all v3 JupyterHub workload execution",
        ]
        passed = False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return passed


def _wait_user_pod(username: str, timeout: float = 120) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pods = _get_json(
            [
                "get",
                "pods",
                "-n",
                NAMESPACE,
                "-l",
                f"hub.jupyter.org/username={username}",
            ]
        ) or {"items": []}
        if len(pods["items"]) == 1:
            pod = pods["items"][0]
            ready = any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in pod.get("status", {}).get("conditions", [])
            )
            if ready:
                return pod
        time.sleep(0.5)
    raise TimeoutError(f"single-user pod for {username} did not become Ready")


def _verify_resources(
    pod: dict[str, Any],
    profile: str,
    method: str,
    run_id: str,
    expected_image: str,
) -> None:
    container = pod["spec"]["containers"][0]
    if container.get("image") != expected_image:
        raise RuntimeError("JupyterHub pod image differs from the preregistered digest")
    actual = container["resources"]
    expected = PROFILE_RESOURCES[profile]
    if actual["requests"] != {
        "cpu": expected["cpu_request"],
        "memory": expected["memory_request"],
    }:
        raise RuntimeError("JupyterHub pod requests do not match the applied profile")
    if actual["limits"] != {
        "cpu": expected["cpu_limit"],
        "memory": expected["memory_limit"],
    }:
        raise RuntimeError("JupyterHub pod limits do not match the applied profile")
    annotations = pod["metadata"].get("annotations", {})
    expected_annotations = {
        "z2jh-context-demo.local/evaluation-method": method,
        "z2jh-context-demo.local/applied-profile": profile,
        "z2jh-context-demo.local/run-id": run_id,
    }
    for key, value in expected_annotations.items():
        if annotations.get(key) != value:
            raise RuntimeError(f"missing or wrong JupyterHub annotation {key}")


def _user_options(workload: dict[str, Any], item: EndToEndPlanItem) -> dict[str, Any]:
    return {
        "evaluation_method": item.method,
        "intent": workload["intent"],
        "dataset_size_gb": workload["dataset_size_hint_gb"],
        "code_context": "\n".join(workload["code_context_hints"]),
        "run_id": item.run_id[:63],
    }


def execute_item(
    item: EndToEndPlanItem,
    workload: dict[str, Any],
    *,
    base_url: str,
    token: str,
    image: str,
) -> dict[str, Any]:
    username = item.synthetic_username
    _api(base_url, token, "POST", f"/hub/api/users/{username}", {})
    started = time.monotonic()
    status, response = _api(
        base_url,
        token,
        "POST",
        f"/hub/api/users/{username}/server",
        _user_options(workload, item),
    )
    if status not in {201, 202}:
        raise RuntimeError(f"JupyterHub spawn failed with HTTP {status}: {response}")
    pod = _wait_user_pod(username)
    spawn_latency = time.monotonic() - started
    _verify_resources(
        pod, item.applied_profile, item.method, item.run_id[:63], image
    )
    pod_name = pod["metadata"]["name"]
    command = [
        "exec",
        "-n",
        NAMESPACE,
        pod_name,
        "--",
        "python3",
        "-m",
        "cluster_evaluation.pod_runner_v3",
        "--workload-id",
        item.workload_id,
        "--seed",
        str(item.random_seed),
        "--sample-interval",
        "0.1",
    ]
    workload_started = time.monotonic()
    workload_timeout = float(workload["workload_deadline_seconds"]) + 30
    exec_timed_out = False
    try:
        executed = _kubectl(command, timeout=workload_timeout)
    except subprocess.TimeoutExpired as exc:
        exec_timed_out = True
        executed = subprocess.CompletedProcess(
            args=exc.cmd,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + "\nJupyterHub workload exec exceeded its hard timeout.\n",
        )
    workload_elapsed = time.monotonic() - workload_started
    final_pod = _get_json(["get", "pod", pod_name, "-n", NAMESPACE]) or pod
    evidence = extract_pod_evidence(final_pod, _events(pod_name))
    payload = _parse_payload(executed.stdout)
    cgroup = (payload or {}).get("cgroup_metrics", {})
    workload_payload = (payload or {}).get("workload", {})
    resources = PROFILE_RESOURCES[item.applied_profile]
    observed_profile = _observed_profile(evidence)
    oom_killed = bool(evidence.get("oom_killed"))
    success = bool(
        executed.returncode == 0
        and payload is not None
        and workload_payload.get("checksum")
        and observed_profile == item.applied_profile
        and not oom_killed
        and not exec_timed_out
    )
    # Stop through Hub even after an expected OOM. A 404 means the failed
    # single-user server has already disappeared.
    stop_status, _ = _api(
        base_url, token, "DELETE", f"/hub/api/users/{username}/server"
    )
    if stop_status not in {202, 204, 404}:
        raise RuntimeError(f"JupyterHub stop failed with HTTP {stop_status}")
    cleanup_deadline = time.monotonic() + 90
    while time.monotonic() < cleanup_deadline:
        remaining = _get_json(
            [
                "get",
                "pods",
                "-n",
                NAMESPACE,
                "-l",
                f"hub.jupyter.org/username={username}",
            ]
        ) or {"items": []}
        if not remaining["items"]:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("single-user pod did not disappear after Hub stop")
    delete_user_status, _ = _api(
        base_url, token, "DELETE", f"/hub/api/users/{username}"
    )
    if delete_user_status not in {204, 404}:
        raise RuntimeError(f"synthetic user cleanup failed with HTTP {delete_user_status}")
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if success:
        failure_category = "success"
    elif oom_killed:
        failure_category = "oom_killed"
    elif exec_timed_out:
        failure_category = "timeout"
    elif payload is None or observed_profile != item.applied_profile:
        failure_category = "validation_failure"
    else:
        failure_category = "workload_failure"
    record = {
        "schema_version": "3.0.0",
        "protocol_version": "3.0.0",
        "experiment_path": "jupyterhub_e2e",
        "experiment_kind": "jupyterhub",
        "run_id": item.run_id,
        "plan_index": item.plan_index,
        "evaluation_set": item.evaluation_set,
        "workload_id": item.workload_id,
        "repeat_index": item.repeat_index,
        "random_seed": item.random_seed,
        "method": item.method,
        "recommended_profile": item.recommended_profile,
        "applied_profile": item.applied_profile,
        "observed_profile": observed_profile,
        "recommendation_reasons": item.recommendation_reasons,
        "context_signal_summary": item.context_signal_summary,
        "cpu_request_m": resources["cpu_request_m"],
        "cpu_limit_m": resources["cpu_limit_m"],
        "memory_request_mi": resources["memory_request_mi"],
        "memory_limit_mi": resources["memory_limit_mi"],
        "target_cgroup_mib": int(workload["target_cgroup_mib"]),
        "target_band_mib": list(workload["target_band_mib"]),
        "actual_cgroup_peak_mib": cgroup.get("peak_memory_mib"),
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
        "spawn_latency_seconds": round(spawn_latency, 6),
        "time_to_outcome_seconds": round(workload_elapsed, 6),
        "phase": evidence.get("phase"),
        "exit_code": executed.returncode,
        "exit_reason": (
            "Completed"
            if executed.returncode == 0
            else evidence.get("termination_reason") or "WorkloadExecFailed"
        ),
        "oom_killed": oom_killed,
        "timeout": exec_timed_out,
        "restart_count": int(evidence.get("restart_count") or 0),
        "checksum": workload_payload.get("checksum"),
        "success": success,
        "infrastructure_invalid": False,
        "exclusion_reason": None,
        "replacement_run_id": None,
        "cleanup_status": "completed" if stop_status in {202, 204, 404} else "failed",
        "failure_category": failure_category,
        "git_commit": current_git_commit(ROOT),
        "container_image": image,
        "configuration_identity": (
            "helm-chart-4.0.0+values-sha256:" + _sha256(
                ROOT / "helm" / "experiment-v3-values.yaml"
            )
        ),
        "input_sha256": _input_sha256(workload),
        "supporting_evidence_sha256": {},
        "supporting_log_paths": [
            f"runs/{item.run_id}/workload.stdout",
            f"runs/{item.run_id}/workload.stderr",
            f"runs/{item.run_id}/pod-evidence.json",
        ],
        "timestamp_created": (
            pod.get("metadata", {}).get("creationTimestamp") or now
        ),
        "timestamp_recorded": now,
        "_workload_stdout": executed.stdout,
        "_workload_stderr": executed.stderr,
        "_pod_evidence": evidence,
    }
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Protocol-v3 JupyterHub sentinel harness.")
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--hub-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token-env", default="JUPYTERHUB_API_TOKEN")
    parser.add_argument("--image")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--preflight-report", type=Path)
    parser.add_argument("--calibration-evidence", type=Path)
    parser.add_argument("--ground-truth-evidence", type=Path)
    parser.add_argument("--comparative-evidence", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    plan = generate_plan(args.experiment_id)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "protocol_version": "3.0.0",
                    "experiment_path": "jupyterhub_e2e",
                    "planned_trials": len(plan),
                    "workloads": sorted({item.workload_id for item in plan}),
                    "methods": list(METHODS),
                    "repeats": 3,
                    "cluster_or_http_mutation": False,
                    "first_five": [asdict(item) for item in plan[:5]],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.preflight_only:
        if not args.image:
            raise ValueError("--image is required with --preflight-only")
        if args.preflight_report is None:
            raise ValueError("--preflight-report is required with --preflight-only")
        return 0 if _write_preflight_report(
            args.image, args.preflight_report.resolve()
        ) else 2
    token = os.environ.get(args.token_env)
    if not token:
        raise RuntimeError(f"missing JupyterHub API token in {args.token_env}")
    if args.out is None:
        raise ValueError("--out is required with --execute")
    if not args.image:
        raise ValueError("--image is required with --execute")
    prerequisite_paths = {
        "calibration": args.calibration_evidence,
        "ground-truth": args.ground_truth_evidence,
        "comparative": args.comparative_evidence,
    }
    missing_prerequisites = [
        kind for kind, path in prerequisite_paths.items() if path is None
    ]
    if missing_prerequisites:
        raise ValueError(
            "JupyterHub execution requires completed evidence for: "
            + ", ".join(missing_prerequisites)
        )
    from cluster_evaluation.analyze_v3 import _load_records, validate_calibration
    from cluster_evaluation.evidence_v3 import validate_experiment

    prerequisite_integrity = {
        kind: validate_experiment(path, kind)["integrity"]
        for kind, path in prerequisite_paths.items()
        if path is not None
    }
    if validate_calibration(_load_records(args.calibration_evidence))["status"] != "pass":
        raise RuntimeError("calibration gate failed; JupyterHub execution is forbidden")
    output = args.out.resolve()
    try:
        output.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("end-to-end evidence must remain inside the repository") from exc
    environment = _preflight(args.image)
    output.mkdir(parents=True, exist_ok=False)
    workloads = {item["workload_id"]: item for item in load_workloads()}
    matrix_text = "".join(
        json.dumps(asdict(item), sort_keys=True) + "\n" for item in plan
    )
    environment.update(
        {
            "planned_trials": len(plan),
            "repeat_count": 3,
            "matrix_sha256": hashlib.sha256(matrix_text.encode("utf-8")).hexdigest(),
            "analysis_path": str(ANALYSIS.relative_to(ROOT)),
            "analysis_sha256": _sha256(ANALYSIS),
            "protocol_path": str(PROTOCOL.relative_to(ROOT)),
            "protocol_sha256": _sha256(PROTOCOL),
            "helm_chart_version": "4.0.0",
            "configuration_identity": (
                "helm-chart-4.0.0+values-sha256:"
                + _sha256(ROOT / "helm" / "experiment-v3-values.yaml")
            ),
            "prerequisite_integrity": prerequisite_integrity,
        }
    )
    with (output / "environment.json").open("x", encoding="utf-8") as handle:
        json.dump(environment, handle, indent=2, sort_keys=True)
        handle.write("\n")
    with (output / "matrix.jsonl").open("x", encoding="utf-8") as handle:
        handle.write(matrix_text)
    for item in plan:
        live = _preflight(args.image)
        if live["manifest_sha256"] != environment["manifest_sha256"]:
            raise RuntimeError("manifest hash drifted during end-to-end matrix")
        try:
            record = execute_item(
                item,
                workloads[item.workload_id],
                base_url=args.hub_url,
                token=token,
                image=args.image,
            )
        except Exception:
            _api(
                args.hub_url,
                token,
                "DELETE",
                f"/hub/api/users/{item.synthetic_username}/server",
            )
            _api(
                args.hub_url,
                token,
                "DELETE",
                f"/hub/api/users/{item.synthetic_username}",
            )
            raise
        # stdout/stderr are synthetic workload output only; token and raw Hub
        # response headers are never stored.
        run_dir = output / "runs" / item.run_id
        run_dir.mkdir(parents=True)
        stdout = record.pop("_workload_stdout")
        stderr = record.pop("_workload_stderr")
        pod_evidence = record.pop("_pod_evidence")
        stdout_path = run_dir / "workload.stdout"
        stderr_path = run_dir / "workload.stderr"
        evidence_path = run_dir / "pod-evidence.json"
        with stdout_path.open("x", encoding="utf-8") as handle:
            handle.write(stdout)
        with stderr_path.open("x", encoding="utf-8") as handle:
            handle.write(stderr)
        with evidence_path.open("x", encoding="utf-8") as handle:
            json.dump(pod_evidence, handle, indent=2, sort_keys=True)
            handle.write("\n")
        record["supporting_evidence_sha256"] = {
            str(path.relative_to(ROOT)): _sha256(path)
            for path in (stdout_path, stderr_path, evidence_path)
        }
        validate_record(record)
        with (run_dir / "record.json").open("x", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.write("\n")
        with (output / "results.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    _write_integrity_manifest(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
