"""Build immutable experiment records from benchmark metadata and evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
from uuid import uuid4

import yaml

from experiments.jsonl_io import append_jsonl
from experiments.kubernetes_evidence import extract_metric_peaks, extract_pod_evidence, load_json
from experiments.kubernetes_evidence import collect_kubernetes_artifacts
from experiments.methods import METHODS, decide_method
from experiments.result_schema import (
    PROFILE_RESOURCES,
    current_git_commit,
    empty_record,
    make_run_id,
    now_utc_iso,
    validate_record,
)


ROOT = Path(__file__).resolve().parents[1]


def load_workloads(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        manifest = yaml.safe_load(handle)
    return list(manifest["workloads"])


def workload_by_id(path: Path, workload_id: str) -> dict[str, Any]:
    for workload in load_workloads(path):
        if workload["workload_id"] == workload_id:
            return workload
    raise ValueError(f"unknown workload_id {workload_id!r}")


def _supporting_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _write_artifact(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)
    return _supporting_path(path)


def _load_workload_metadata(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("workload_id"):
            return payload
    return None


def run_local_workload(
    workload: dict[str, Any],
    seed: int,
    artifact_dir: Path,
    *,
    timeout_seconds: int | float | None = None,
) -> dict[str, Any]:
    command = [str(part) for part in workload["workload"]["command"]]
    if "--seed" in command:
        command[command.index("--seed") + 1] = str(seed)

    stdout_path = artifact_dir / "workload_stdout.jsonl"
    stderr_path = artifact_dir / "workload_stderr.txt"
    effective_timeout_seconds = float(timeout_seconds if timeout_seconds is not None else workload["timeout_seconds"])
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=effective_timeout_seconds,
        )
        stdout_ref = _write_artifact(stdout_path, result.stdout)
        stderr_ref = _write_artifact(stderr_path, result.stderr)
        metadata = _load_workload_metadata(result.stdout)
        return {
            "exit_code": result.returncode,
            "timeout": False,
            "success": result.returncode == 0,
            "exit_reason": "Completed" if result.returncode == 0 else "Error",
            "error_message": None if result.returncode == 0 else (result.stderr.strip() or "workload exited non-zero"),
            "metadata": metadata,
            "supporting_log_paths": [stdout_ref, stderr_ref],
            "cleanup_status": "completed",
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        stdout_ref = _write_artifact(stdout_path, stdout)
        stderr_ref = _write_artifact(stderr_path, stderr)
        return {
            "exit_code": None,
            "timeout": True,
            "success": False,
            "exit_reason": "Timeout",
            "error_message": f"workload exceeded timeout_seconds={effective_timeout_seconds:g}",
            "metadata": _load_workload_metadata(stdout),
            "supporting_log_paths": [stdout_ref, stderr_ref],
            "cleanup_status": "completed",
        }


def build_record(
    *,
    workload: dict[str, Any],
    method: str,
    repeat_index: int,
    seed: int,
    environment_id: str,
    run_id: str | None = None,
    applied_profile: str | None = None,
    local_result: dict[str, Any] | None = None,
    pod_json: dict[str, Any] | None = None,
    events_json: dict[str, Any] | None = None,
    metrics_json: dict[str, Any] | None = None,
    supporting_log_paths: list[str] | None = None,
    cleanup_status: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    if method not in METHODS:
        raise ValueError(f"unsupported method {method!r}")

    decision = decide_method(
        method,
        workload,
        applied_profile=applied_profile,
    )
    resources = PROFILE_RESOURCES.get(decision.applied_profile or "", {})
    k8s_evidence = extract_pod_evidence(pod_json, events_json)
    k8s_resources = k8s_evidence.get("requests_limits", {})
    metric_peaks = extract_metric_peaks(metrics_json)

    record = empty_record()
    record.update(
        {
            "run_id": run_id or f"{make_run_id(workload['workload_id'])}-{uuid4().hex[:8]}",
            "timestamp": now_utc_iso(),
            "git_commit": current_git_commit(ROOT),
            "environment_id": environment_id,
            "method": method,
            "workload_id": workload["workload_id"],
            "repeat_index": repeat_index,
            "random_seed": seed,
            "input_intent": workload["intent"],
            "dataset_size_hint_gb": float(workload["dataset_size_hint_gb"]),
            "context_signal_summary": decision.context_signal_summary,
            "recommended_profile": decision.recommended_profile,
            "applied_profile": decision.applied_profile,
            "recommendation_reasons": decision.recommendation_reasons,
            "policy_warnings": decision.policy_warnings,
            "cpu_request_m": k8s_resources.get("cpu_request_m") or resources.get("cpu_request_m"),
            "cpu_limit_m": k8s_resources.get("cpu_limit_m") or resources.get("cpu_limit_m"),
            "memory_request_mi": k8s_resources.get("memory_request_mi") or resources.get("memory_request_mi"),
            "memory_limit_mi": k8s_resources.get("memory_limit_mi") or resources.get("memory_limit_mi"),
            "peak_cpu_m": metric_peaks["peak_cpu_m"],
            "peak_memory_mi": metric_peaks["peak_memory_mi"],
            "resource_measurement_source": metric_peaks["resource_measurement_source"],
            "pod_pending_duration_seconds": k8s_evidence.get("pod_pending_duration_seconds"),
            "workload_runtime_seconds": k8s_evidence.get("workload_runtime_seconds"),
            "time_to_success_seconds": k8s_evidence.get("time_to_success_seconds"),
            "oom_killed": k8s_evidence.get("oom_killed"),
            "exit_reason": k8s_evidence.get("termination_reason"),
            "exit_code": k8s_evidence.get("termination_exit_code"),
            "restart_or_respawn_count": k8s_evidence.get("restart_count"),
            "success": None,
            "timeout": False,
            "cleanup_status": cleanup_status or "not_required",
            "error_message": error_message,
            "supporting_log_paths": list(supporting_log_paths or []),
            "kubernetes_evidence": k8s_evidence,
        }
    )

    if local_result:
        metadata = local_result.get("metadata") or {}
        runtime = metadata.get("runtime", {}) if isinstance(metadata, dict) else {}
        record["exit_code"] = local_result["exit_code"]
        record["timeout"] = local_result["timeout"]
        record["success"] = local_result["success"]
        record["exit_reason"] = local_result["exit_reason"]
        record["error_message"] = local_result["error_message"]
        record["cleanup_status"] = local_result["cleanup_status"]
        record["supporting_log_paths"].extend(local_result["supporting_log_paths"])
        if runtime.get("elapsed_seconds") is not None:
            record["workload_runtime_seconds"] = runtime["elapsed_seconds"]
            if local_result["success"]:
                record["time_to_success_seconds"] = runtime["elapsed_seconds"]
        if runtime.get("max_rss_platform_units") is not None and record["resource_measurement_source"] == "not_available":
            record["peak_memory_mi"] = _max_rss_to_mi(int(runtime["max_rss_platform_units"]))
            record["resource_measurement_source"] = "python_resource_getrusage"
        record["oom_killed"] = local_result["exit_code"] == 137
        record["restart_or_respawn_count"] = 0

    if record["success"] is None:
        record["success"] = (
            k8s_evidence.get("phase") == "Succeeded"
            and record["exit_code"] == 0
            and not record["timeout"]
        )

    validate_record(record)
    return record


def _max_rss_to_mi(max_rss: int) -> int:
    if sys.platform == "darwin":
        return int(max_rss / 1024 / 1024)
    return int(max_rss / 1024)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record one normalized experiment result.")
    parser.add_argument("--manifest", type=Path, default=ROOT / "benchmarks" / "workloads.yaml")
    parser.add_argument("--workload-id", required=True)
    parser.add_argument("--method", required=True, choices=METHODS)
    parser.add_argument("--repeat-index", type=int, default=0)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--run-id")
    parser.add_argument("--environment-id", default="local-smoke")
    parser.add_argument("--applied-profile", choices=("small", "medium", "large"))
    parser.add_argument("--run-local-workload", action="store_true")
    parser.add_argument("--timeout", type=float, help="Override workload timeout seconds for local execution.")
    parser.add_argument("--artifact-dir", type=Path, default=ROOT / "experiments" / "raw")
    parser.add_argument("--raw-jsonl", type=Path, default=ROOT / "experiments" / "raw" / "results.jsonl")
    parser.add_argument("--pod-json", type=Path)
    parser.add_argument("--events-json", type=Path)
    parser.add_argument("--metrics-json", type=Path)
    parser.add_argument("--namespace", help="Optional namespace for read-only live pod evidence capture.")
    parser.add_argument("--pod-name", help="Optional pod name for read-only live pod evidence capture.")
    parser.add_argument("--metrics-samples", type=int, default=1)
    parser.add_argument("--metrics-interval-seconds", type=float, default=1.0)
    parser.add_argument("--cleanup-status")
    parser.add_argument("--error-message")
    parser.add_argument("--no-append", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    workload = workload_by_id(args.manifest, args.workload_id)
    seed = args.seed if args.seed is not None else int(workload["deterministic_seed"])
    run_id = args.run_id or make_run_id(args.workload_id)
    artifact_dir = args.artifact_dir / (run_id or "pending-run") / args.workload_id
    local_result = (
        run_local_workload(workload, seed, artifact_dir, timeout_seconds=args.timeout)
        if args.run_local_workload
        else None
    )
    live_supporting_paths: list[str] = []
    if args.namespace and args.pod_name:
        artifacts = collect_kubernetes_artifacts(
            namespace=args.namespace,
            pod_name=args.pod_name,
            artifact_dir=artifact_dir / "kubernetes",
            metrics_samples=args.metrics_samples,
            metrics_interval_seconds=args.metrics_interval_seconds,
        )
        args.pod_json = args.pod_json or artifacts["pod_json"]
        args.events_json = args.events_json or artifacts["events_json"]
        args.metrics_json = args.metrics_json or artifacts["metrics_json"]
        live_supporting_paths.extend(
            _supporting_path(path)
            for path in artifacts.values()
            if isinstance(path, Path)
        )
    pod_json = load_json(args.pod_json) if args.pod_json else None
    events_json = load_json(args.events_json) if args.events_json else None
    metrics_json = load_json(args.metrics_json) if args.metrics_json else None
    supporting_paths = []
    for path in (args.pod_json, args.events_json, args.metrics_json):
        if path:
            supporting_paths.append(_supporting_path(path))
    supporting_paths.extend(path for path in live_supporting_paths if path not in supporting_paths)

    record = build_record(
        workload=workload,
        method=args.method,
        repeat_index=args.repeat_index,
        seed=seed,
        environment_id=args.environment_id,
        run_id=run_id,
        applied_profile=args.applied_profile,
        local_result=local_result,
        pod_json=pod_json,
        events_json=events_json,
        metrics_json=metrics_json,
        supporting_log_paths=supporting_paths,
        cleanup_status=args.cleanup_status,
        error_message=args.error_message,
    )
    if not args.no_append:
        append_jsonl(args.raw_jsonl, record)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
