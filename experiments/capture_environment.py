"""Capture local evaluation capability metadata as sanitized JSON."""

from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Any

from experiments.result_schema import current_git_commit


ROOT = Path(__file__).resolve().parents[1]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture evaluation environment capability metadata.")
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "environment-capability.json")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def _run(command: list[str]) -> dict[str, Any]:
    if shutil.which(command[0]) is None:
        return {"available": False, "returncode": None, "stdout": "", "stderr": "command not found"}
    result = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
    return {
        "available": True,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _sysctl_value(name: str) -> int | None:
    result = _run(["sysctl", "-n", name])
    if result["returncode"] != 0:
        return None
    try:
        return int(result["stdout"].splitlines()[0])
    except (IndexError, ValueError):
        return None


def capture() -> dict[str, Any]:
    docker_version = _run(["docker", "version"])
    kubectl_context = _run(["kubectl", "config", "current-context"])
    kubectl_cluster_info = _run(["kubectl", "cluster-info"])
    kubectl_top_nodes = _run(["kubectl", "top", "nodes"])
    metrics_api = _run(["kubectl", "get", "apiservice", "v1beta1.metrics.k8s.io", "-o", "yaml"])
    helm_version = _run(["helm", "version", "--short"])
    helm_chart = _run(["helm", "show", "chart", "jupyterhub/jupyterhub", "--version", "4.0.0"])
    minikube = _run(["minikube", "status"])
    kind = _run(["kind", "get", "clusters"])
    k3d = _run(["k3d", "cluster", "list"])
    node_summary = _run(["kubectl", "get", "nodes", "-o", "wide"])
    disk = _run(["df", "-k", "."])
    status = _run(["git", "status", "--short"])

    runtime = "docker" if docker_version["returncode"] == 0 else None
    if runtime == "docker" and "orbstack" in (docker_version["stdout"] + docker_version["stderr"]).lower():
        runtime = "docker-orbstack"

    return {
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "git_commit": current_git_commit(ROOT),
        "git_dirty": bool(status["stdout"]),
        "platform": platform.platform(),
        "python_version": sys.version.split()[0],
        "container_runtime": runtime,
        "docker_available": docker_version["returncode"] == 0,
        "orbstack_cli_available": shutil.which("orb") is not None,
        "kind_available": shutil.which("kind") is not None,
        "kind_status": kind,
        "minikube_available": shutil.which("minikube") is not None,
        "minikube_status": minikube,
        "k3d_available": shutil.which("k3d") is not None,
        "k3d_status": k3d,
        "kubectl_available": shutil.which("kubectl") is not None,
        "kubectl_context": kubectl_context["stdout"] if kubectl_context["returncode"] == 0 else None,
        "kubectl_cluster_accessible": kubectl_cluster_info["returncode"] == 0,
        "node_summary": node_summary,
        "helm_available": helm_version["returncode"] == 0,
        "helm_version": helm_version["stdout"] if helm_version["returncode"] == 0 else None,
        "jupyterhub_chart_4_0_0_available": helm_chart["returncode"] == 0,
        "cpu_count": _sysctl_value("hw.ncpu"),
        "memory_bytes": _sysctl_value("hw.memsize"),
        "disk_free_report": disk,
        "metrics_api_available": kubectl_top_nodes["returncode"] == 0 and metrics_api["returncode"] == 0,
        "kubectl_top_nodes": kubectl_top_nodes,
        "metrics_api_service": metrics_api,
        "full_cluster_resource_experiment_blocker": None
        if kubectl_top_nodes["returncode"] == 0 and metrics_api["returncode"] == 0
        else "Kubernetes Metrics API is unavailable; kubectl top nodes and kubectl top pods -A --containers must succeed before live resource-metric claims are supported.",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.out.exists() and not args.overwrite:
        print(f"environment capture failure: refusing to overwrite {args.out}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(capture(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
