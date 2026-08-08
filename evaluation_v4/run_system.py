"""Execute a protocol-v4 randomized system plan through JupyterHub.

The harness deliberately reuses the frozen v3 bounded workloads.  It drives
the real Hub preview/confirm flow, executes the workload inside the resulting
single-user container, preserves sanitized Kubernetes evidence, and writes
the strict ``system-trial-v4.0.0`` stream consumed by ``evaluation_v4.analyze``.

No cluster mutation occurs unless ``--execute`` is supplied.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Mapping
from urllib import error, parse, request

import yaml

from benchmarks.resource_envelope_runner import load_manifest
from cluster_evaluation.policies import PROFILE_RESOURCES
from evaluation_v4.dataset import DEFAULT_DATASET, canonical_sha256, load_dataset
from evaluation_v4.recommenders import create_backend, evaluate_item
from evaluation_v4.schemas import SYSTEM_SCHEMA, validate_system_trial
from experiments.kubernetes_evidence import (
    extract_pod_evidence,
    parse_cpu_m,
    parse_memory_mi,
)


ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "z2jh-context-demo"
RELEASE = "context-demo"
SAFETY_LABEL = "z2jh-context-demo.local/disposable-experiment-v4"
SAFETY_VALUE = "true"
ALLOWED_METHODS = {"static_small", "static_large", "rule_based_context"}
PENDING_DEADLINE_SECONDS = 120
SPAWN_DEADLINE_SECONDS = 180
SAMPLE_INTERVAL_SECONDS = 0.1
MAX_PLAN_RECORDS = 320
SOURCE_FILES = (
    ROOT / "benchmarks" / "__init__.py",
    ROOT / "benchmarks" / "resource_envelope_runner.py",
    ROOT / "benchmarks" / "workloads-v3.yaml",
    ROOT / "evaluation_v4" / "pod_runner.py",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_new(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(value)


def _write_json_new(path: Path, value: Any) -> None:
    _write_new(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _run(
    args: list[str],
    *,
    input_bytes: bytes | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        args,
        cwd=ROOT,
        input=input_bytes,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _kubectl(
    context: str,
    args: list[str],
    *,
    input_bytes: bytes | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return _run(
        ["kubectl", "--context", context, *args],
        input_bytes=input_bytes,
        timeout=timeout,
    )


def _json_command(result: subprocess.CompletedProcess[bytes], label: str) -> dict[str, Any]:
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"{label} failed: {message[:500]}")
    return json.loads(result.stdout.decode("utf-8"))


def _get_json(context: str, args: list[str]) -> dict[str, Any] | None:
    result = _kubectl(context, [*args, "-o", "json"])
    if result.returncode != 0:
        return None
    return json.loads(result.stdout.decode("utf-8"))


def _git_state() -> tuple[str, bool, list[str]]:
    commit = _run(["git", "rev-parse", "HEAD"])
    if commit.returncode != 0:
        raise RuntimeError("cannot determine Git commit")
    status = _run(["git", "status", "--porcelain=v1"])
    lines = status.stdout.decode("utf-8", errors="replace").splitlines()
    return commit.stdout.decode().strip(), bool(lines), [line[3:] for line in lines]


def _representatives(dataset: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    by_family: dict[str, list[dict[str, Any]]] = {}
    for item in dataset["items"]:
        by_family.setdefault(item["workload_family"], []).append(item)
    return {
        family: sorted(
            items,
            key=lambda item: (item["variant"] != "canonical", item["sample_id"]),
        )[0]
        for family, items in by_family.items()
    }


def _load_locked_image_catalog(dataset: Mapping[str, Any]) -> dict[str, Any]:
    metadata = dataset["image_catalog"]
    path = ROOT / metadata["source_path"]
    if _sha256(path) != metadata["source_sha256"]:
        raise RuntimeError("locked image-catalog source checksum does not match the dataset")
    with path.open(encoding="utf-8") as handle:
        catalog = yaml.safe_load(handle)
    if catalog.get("catalog_version") != metadata["catalog_version"]:
        raise RuntimeError("image-catalog version does not match the dataset")
    if set(catalog.get("images", {})) != set(metadata["images"]):
        raise RuntimeError("image-catalog IDs do not match the dataset")
    return catalog


def _read_plan(path: Path, dataset: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if value.get("plan_schema_version") != "system-plan-v4.0.0":
                raise ValueError(f"{path}:{line_number}: unsupported plan schema")
            records.append(value)
    if not records or len(records) > MAX_PLAN_RECORDS:
        raise ValueError(f"system plan must contain 1..{MAX_PLAN_RECORDS} records")
    if [row.get("plan_index") for row in records] != list(range(len(records))):
        raise ValueError("system plan indices must be consecutive and ordered")
    if len({row.get("trial_id") for row in records}) != len(records):
        raise ValueError("system plan contains duplicate trial IDs")
    representatives = _representatives(dataset)
    mappings = {
        item["workload_family"]: item for item in dataset["system_workload_mapping"]
    }
    for row in records:
        family = row.get("workload_family")
        if row.get("recommender") not in ALLOWED_METHODS:
            raise ValueError(f"plan method is not enabled for this environment: {row.get('recommender')}")
        if family not in mappings:
            raise ValueError(f"plan contains unmapped workload family {family!r}")
        expected = mappings[family]
        if row.get("system_workload_id") != expected["workload_id"]:
            raise ValueError(f"plan workload ID drift for family {family}")
        if row.get("system_manifest_path") != expected["manifest_path"]:
            raise ValueError(f"plan manifest drift for family {family}")
        if row.get("representative_sample_id") != representatives[family]["sample_id"]:
            raise ValueError(f"plan representative sample drift for family {family}")
        if row.get("cache_condition") != "warm_required":
            raise ValueError("v4 system execution requires warm_required trials")
    return records


class HubSession:
    """Authenticated synthetic user session; credentials/tokens stay in memory."""

    def __init__(self, base_url: str, username: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.cookies = http.cookiejar.CookieJar()
        self.opener = request.build_opener(request.HTTPCookieProcessor(self.cookies))
        self.api_token: str | None = None
        self.api_token_id: str | None = None

    def _call(
        self,
        method: str,
        path: str,
        *,
        form: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30,
    ) -> tuple[int, str, Mapping[str, str]]:
        body: bytes | None = None
        final_headers = dict(headers or {})
        if form is not None:
            body = parse.urlencode(form).encode("utf-8")
            final_headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif payload is not None:
            body = json.dumps(payload).encode("utf-8")
            final_headers["Content-Type"] = "application/json"
        req = request.Request(
            parse.urljoin(self.base_url + "/", path.lstrip("/")),
            data=body,
            method=method,
            headers=final_headers,
        )
        try:
            with self.opener.open(req, timeout=timeout) as response:
                return response.status, response.read().decode("utf-8"), response.headers
        except error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace"), exc.headers

    @staticmethod
    def _xsrf(text: str) -> str:
        patterns = (
            r'name="_xsrf" value="([^"]+)"',
            r'xsrf_token: "([^"]+)"',
            r'const xsrf = "([^"]+)"',
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        raise RuntimeError("Hub page did not contain an XSRF token")

    def login(self) -> None:
        status, text, _ = self._call("GET", "/hub/login")
        if status != 200:
            raise RuntimeError(f"Hub login page returned HTTP {status}")
        xsrf = self._xsrf(text)
        status, _, _ = self._call(
            "POST",
            "/hub/login?next=",
            form={"_xsrf": xsrf, "username": self.username, "password": self.username},
        )
        if status not in {200, 302}:
            raise RuntimeError(f"DummyAuthenticator login returned HTTP {status}")
        status, text, _ = self._call("GET", "/hub/token")
        if status != 200:
            raise RuntimeError(f"Hub token page returned HTTP {status}")
        xsrf = self._xsrf(text)
        status, text, _ = self._call(
            "POST",
            f"/hub/api/users/{self.username}/tokens",
            payload={"note": "protocol-v4-system-harness", "expires_in": 43200},
            headers={"X-XSRFToken": xsrf},
        )
        if status != 201:
            raise RuntimeError(f"self-scoped API token creation returned HTTP {status}")
        token = json.loads(text)
        self.api_token = token["token"]
        self.api_token_id = str(token["id"])

    def preview(self, values: Mapping[str, Any]) -> dict[str, Any]:
        status, page, _ = self._call("GET", "/hub/spawn")
        if status != 200:
            raise RuntimeError(f"Hub spawn page returned HTTP {status}")
        xsrf = self._xsrf(page)
        status, text, _ = self._call(
            "POST",
            "/hub/dynamic-resource-preview",
            payload=values,
            headers={"X-XSRFToken": xsrf},
        )
        if status != 200:
            raise RuntimeError(f"dynamic preview returned HTTP {status}: {text[:300]}")
        payload = json.loads(text)
        payload["_xsrf_for_spawn"] = xsrf
        return payload

    def spawn(
        self,
        values: Mapping[str, Any],
        preview: Mapping[str, Any],
        *,
        action: str,
        applied_profile: str,
        applied_image_id: str,
    ) -> dict[str, Any]:
        form = {
            "_xsrf": preview["_xsrf_for_spawn"],
            "decision_action": action,
            "dynamic_preview_id": preview["dynamic_preview_id"],
            "preview_version": preview["preview_version"],
            "override_profile": applied_profile,
            "override_image_id": applied_image_id,
            **values,
        }
        started = time.monotonic()
        status, text, headers = self._call(
            "POST", "/hub/spawn", form=form, timeout=45
        )
        return {
            "http_status": status,
            "location": headers.get("Location"),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "accepted": status in {200, 302},
            "response_category": "accepted" if status in {200, 302} else "http_error",
            "response_excerpt": "" if status in {200, 302} else text[:300],
        }

    def stop(self) -> int:
        if not self.api_token:
            raise RuntimeError("Hub API token is unavailable")
        status, _, _ = self._call(
            "DELETE",
            f"/hub/api/users/{self.username}/server",
            headers={"Authorization": f"token {self.api_token}"},
        )
        return status


def _single_user_pods(context: str, username: str) -> list[dict[str, Any]]:
    payload = _get_json(
        context,
        [
            "get",
            "pods",
            "-n",
            NAMESPACE,
            "-l",
            f"hub.jupyter.org/username={username}",
        ],
    ) or {"items": []}
    return list(payload.get("items", []))


def _pod_ready(pod: Mapping[str, Any]) -> bool:
    return any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in pod.get("status", {}).get("conditions", [])
    )


def _pod_image_pull_failure(pod: Mapping[str, Any]) -> bool:
    for status in pod.get("status", {}).get("containerStatuses", []) or []:
        waiting = status.get("state", {}).get("waiting", {})
        if waiting.get("reason") in {"ErrImagePull", "ImagePullBackOff", "InvalidImageName"}:
            return True
    return False


def _events(context: str, pod_name: str) -> dict[str, Any]:
    return _get_json(
        context,
        [
            "get",
            "events",
            "-n",
            NAMESPACE,
            "--field-selector",
            f"involvedObject.kind=Pod,involvedObject.name={pod_name}",
        ],
    ) or {"items": []}


def _wait_for_ready(
    context: str, username: str
) -> tuple[dict[str, Any] | None, bool, bool, float]:
    started = time.monotonic()
    deadline = started + SPAWN_DEADLINE_SECONDS
    pod: dict[str, Any] | None = None
    image_pull_failure = False
    while time.monotonic() < deadline:
        pods = _single_user_pods(context, username)
        if len(pods) > 1:
            raise RuntimeError("more than one single-user pod exists for the synthetic user")
        if pods:
            pod = pods[0]
            image_pull_failure = image_pull_failure or _pod_image_pull_failure(pod)
            if _pod_ready(pod):
                return pod, True, image_pull_failure, time.monotonic() - started
            if pod.get("status", {}).get("phase") == "Failed":
                break
        time.sleep(0.25)
    return pod, False, image_pull_failure, time.monotonic() - started


def _copy_sources(context: str, pod_name: str) -> None:
    mkdir = _kubectl(
        context,
        ["exec", "-n", NAMESPACE, pod_name, "--", "mkdir", "-p", "/tmp/v4/benchmarks"],
        timeout=30,
    )
    if mkdir.returncode != 0:
        raise RuntimeError("cannot create the ephemeral workload source directory")
    destinations = {
        SOURCE_FILES[0]: "/tmp/v4/benchmarks/__init__.py",
        SOURCE_FILES[1]: "/tmp/v4/benchmarks/resource_envelope_runner.py",
        SOURCE_FILES[2]: "/tmp/v4/benchmarks/workloads-v3.yaml",
        SOURCE_FILES[3]: "/tmp/v4/pod_runner.py",
    }
    for source, destination in destinations.items():
        copied = _run(
            [
                "kubectl",
                "--context",
                context,
                "cp",
                str(source),
                f"{NAMESPACE}/{pod_name}:{destination}",
            ],
            timeout=45,
        )
        if copied.returncode != 0:
            raise RuntimeError(
                "workload source copy failed: "
                + copied.stderr.decode("utf-8", errors="replace")[:300]
            )


def _parse_payload(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("pod_runner_schema_version") == "4.0.0":
            return payload
    return None


def _verify_pod(
    pod: Mapping[str, Any],
    *,
    profile: str,
    image_id: str,
    image_reference: str,
) -> dict[str, Any]:
    annotations = pod.get("metadata", {}).get("annotations", {})
    if annotations.get("z2jh-context-demo.local/applied-profile") != profile:
        raise RuntimeError("pod applied-profile annotation does not match the trial")
    if annotations.get("z2jh-context-demo.local/applied-image") != image_id:
        raise RuntimeError("pod applied-image annotation does not match the trial")
    container = pod["spec"]["containers"][0]
    if container.get("image") != image_reference:
        raise RuntimeError("pod image does not match the locked catalog digest")
    requests = container.get("resources", {}).get("requests", {})
    cpu_request = parse_cpu_m(requests.get("cpu"))
    memory_request = parse_memory_mi(requests.get("memory"))
    if not cpu_request or not memory_request:
        raise RuntimeError("pod does not expose positive CPU and memory requests")
    return {
        "cpu_request_m": cpu_request,
        "memory_request_mib": memory_request,
        "resources": container.get("resources", {}),
    }


def _intended_requests(
    method: str, profile: str, preview: Mapping[str, Any] | None
) -> tuple[int, int]:
    if method == "rule_based_context" and preview:
        dynamic = preview.get("resource_decision", {}).get("resources")
        if dynamic:
            return int(dynamic["cpu_request_millicores"]), int(dynamic["memory_request_mib"])
    resources = PROFILE_RESOURCES[profile]
    return int(resources["cpu_request_m"]), int(resources["memory_request_mi"])


def _clean_preview(preview: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in preview.items() if not key.startswith("_")}


def _cleanup(
    context: str, hub: HubSession, username: str, pvc_names_before: list[str]
) -> dict[str, Any]:
    try:
        stop_status = hub.stop()
    except Exception as exc:  # cleanup evidence must survive a stop API failure
        stop_status = None
        stop_error = type(exc).__name__
    else:
        stop_error = None
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and _single_user_pods(context, username):
        time.sleep(0.5)
    remaining = [
        pod.get("metadata", {}).get("name") for pod in _single_user_pods(context, username)
    ]
    pvcs = _get_json(context, ["get", "pvc", "-n", NAMESPACE]) or {"items": []}
    pvc_names_after = sorted(
        item.get("metadata", {}).get("name") for item in pvcs.get("items", [])
    )
    return {
        "stop_http_status": stop_status,
        "stop_error_category": stop_error,
        "remaining_user_pods": remaining,
        "pvc_names_before": pvc_names_before,
        "pvc_names_after": pvc_names_after,
        "pvc_policy": "JupyterHub home PVC persists; workloads execute only under /tmp/v4",
        "status": "completed" if not remaining and stop_status in {202, 204, 404} else "failed",
    }


def _trial(
    *,
    row: Mapping[str, Any],
    dataset: Mapping[str, Any],
    image_catalog: Mapping[str, Any],
    workloads: Mapping[str, dict[str, Any]],
    context: str,
    hub: HubSession,
    username: str,
    experiment_id: str,
    environment_id: str,
    git_commit: str,
    run_dir: Path,
) -> dict[str, Any]:
    if _single_user_pods(context, username):
        raise RuntimeError("state leakage detected: a synthetic-user pod exists before trial")
    item = next(
        item for item in dataset["items"] if item["sample_id"] == row["representative_sample_id"]
    )
    method = str(row["recommender"])
    decision = evaluate_item(
        method,
        item,
        backend=create_backend(method),
        catalog_images=image_catalog["images"],
    )
    if not decision.applied_profile or not decision.predicted_image_id:
        raise RuntimeError("recommender did not produce an applicable system decision")
    profile = decision.applied_profile
    image_id = decision.predicted_image_id
    image_reference = image_catalog["images"][image_id]["reference"]
    values = {
        "intent": item["inputs"]["intent"],
        "dataset_size_gb": item["inputs"]["dataset_size_gb"],
        "code_context": "\n".join(item["inputs"]["code_context_hints"]),
    }
    timestamp = _utc_now()
    pvcs = _get_json(context, ["get", "pvc", "-n", NAMESPACE]) or {"items": []}
    pvc_names_before = sorted(
        candidate.get("metadata", {}).get("name") for candidate in pvcs.get("items", [])
    )
    preview: dict[str, Any] | None = None
    spawn_result: dict[str, Any] = {"accepted": False, "response_category": "not_attempted"}
    pod: dict[str, Any] | None = None
    ready = False
    image_pull_failure = False
    time_to_ready: float | None = None
    executed: subprocess.CompletedProcess[bytes] | None = None
    payload: dict[str, Any] | None = None
    trial_error_category: str | None = None
    workload_elapsed: float | None = None
    try:
        preview = hub.preview(values)
        recommendation = preview["recommendation"]
        if method == "rule_based_context":
            preview_profile = "large" if recommendation["profile"] == "gpu_or_large" else recommendation["profile"]
            if preview_profile != profile or recommendation["image_id"] != image_id:
                raise RuntimeError("Hub preview differs from the frozen local recommender decision")
            action = "accept"
        else:
            action = "override"
        spawn_result = hub.spawn(
            values,
            preview,
            action=action,
            applied_profile=profile,
            applied_image_id=image_id,
        )
        if not spawn_result["accepted"]:
            raise RuntimeError("Hub rejected the spawn submission")
        pod, ready, image_pull_failure, time_to_ready = _wait_for_ready(context, username)
        if not ready or pod is None:
            trial_error_category = "image_pull_failure" if image_pull_failure else "spawn_timeout_or_failure"
        else:
            observed = _verify_pod(
                pod,
                profile=profile,
                image_id=image_id,
                image_reference=image_reference,
            )
            _copy_sources(context, pod["metadata"]["name"])
            workload = workloads[row["system_workload_id"]]
            command = [
                "exec",
                "-n",
                NAMESPACE,
                pod["metadata"]["name"],
                "--",
                "env",
                "PYTHONPATH=/tmp/v4",
                "python",
                "/tmp/v4/pod_runner.py",
                "--workload-id",
                row["system_workload_id"],
                "--seed",
                str(row["paired_workload_seed"]),
                "--sample-interval",
                str(SAMPLE_INTERVAL_SECONDS),
                "--manifest",
                "/tmp/v4/benchmarks/workloads-v3.yaml",
            ]
            workload_started = time.monotonic()
            try:
                executed = _kubectl(
                    context,
                    command,
                    timeout=float(workload["workload_deadline_seconds"]) + 30,
                )
            except subprocess.TimeoutExpired as exc:
                executed = subprocess.CompletedProcess(
                    exc.cmd,
                    124,
                    stdout=exc.stdout or b"",
                    stderr=(exc.stderr or b"") + b"\ncontroller workload timeout\n",
                )
                trial_error_category = "workload_timeout"
            workload_elapsed = time.monotonic() - workload_started
            payload = _parse_payload(executed.stdout.decode("utf-8", errors="replace"))
            time.sleep(1.0)
            refreshed = _get_json(
                context,
                ["get", "pod", pod["metadata"]["name"], "-n", NAMESPACE],
            )
            if refreshed is not None:
                pod = refreshed
            if executed.returncode != 0 and trial_error_category is None:
                trial_error_category = "workload_process_failure"
    except Exception as exc:
        if trial_error_category is None:
            trial_error_category = type(exc).__name__
    finally:
        pod_name = pod.get("metadata", {}).get("name") if pod else None
        event_payload = _events(context, pod_name) if pod_name else {"items": []}
        pod_evidence = extract_pod_evidence(pod, event_payload)
        cleanup = _cleanup(context, hub, username, pvc_names_before)

    stdout = "" if executed is None else executed.stdout.decode("utf-8", errors="replace")
    stderr = "" if executed is None else executed.stderr.decode("utf-8", errors="replace")
    _write_json_new(run_dir / "preview.json", _clean_preview(preview) if preview else {})
    _write_json_new(run_dir / "spawn-result.json", spawn_result)
    _write_json_new(run_dir / "pod-evidence.json", pod_evidence)
    _write_new(run_dir / "workload.stdout", stdout)
    _write_new(run_dir / "workload.stderr", stderr)
    _write_json_new(run_dir / "cleanup.json", cleanup)
    intended_cpu, intended_memory = _intended_requests(method, profile, preview)
    actual_resources = pod_evidence.get("requests_limits", {})
    cpu_request = actual_resources.get("cpu_request_m") or intended_cpu
    memory_request = actual_resources.get("memory_request_mi") or intended_memory
    cgroup = (payload or {}).get("cgroup_metrics", {})
    workload_payload = (payload or {}).get("workload", {})
    oom_killed = bool(
        pod_evidence.get("oom_killed")
        or (executed is not None and executed.returncode == 137)
    )
    pending_reasons = pod_evidence.get("scheduling_or_pending_reasons", [])
    pending_failure = bool(
        pod is not None
        and not ready
        and (
            (time_to_ready or 0) >= PENDING_DEADLINE_SECONDS
            or any("FailedScheduling" in reason for reason in pending_reasons)
        )
    )
    workload_success = bool(
        ready
        and executed is not None
        and executed.returncode == 0
        and workload_payload.get("checksum")
        and not oom_killed
    )
    supporting = [
        str(path.relative_to(ROOT))
        for path in (
            run_dir / "preview.json",
            run_dir / "spawn-result.json",
            run_dir / "pod-evidence.json",
            run_dir / "workload.stdout",
            run_dir / "workload.stderr",
            run_dir / "cleanup.json",
        )
    ]
    record = {
        "schema_version": SYSTEM_SCHEMA,
        "evidence_class": "observed",
        "trial_id": row["trial_id"],
        "experiment_id": experiment_id,
        "timestamp_utc": timestamp,
        "git_commit": git_commit,
        "environment_id": environment_id,
        "recommender": method,
        "sample_id": row["representative_sample_id"],
        "workload_family": row["workload_family"],
        "repeat_index": row["repeat_block"],
        "applied_profile": profile,
        "applied_image_id": image_id,
        "cpu_request_m": cpu_request,
        "memory_request_mib": memory_request,
        "cpu_usage_mean_m": cgroup.get("cpu_usage_mean_m"),
        "memory_usage_mean_mib": cgroup.get("memory_usage_mean_mib"),
        "memory_usage_peak_mib": cgroup.get("memory_usage_peak_mib"),
        "measurement_window_seconds": cgroup.get("measurement_window_seconds"),
        "measurement_source": cgroup.get("source", "unavailable:cgroup_v2_in_container_window"),
        "pod_ready": ready,
        "pending_failure": pending_failure,
        "pending_duration_seconds": pod_evidence.get("pod_pending_duration_seconds"),
        "oom_killed": oom_killed,
        "image_pull_failure": image_pull_failure,
        "workload_success": workload_success,
        "time_to_ready_seconds": round(time_to_ready, 6) if ready and time_to_ready is not None else None,
        "workload_duration_seconds": (
            workload_payload.get("elapsed_seconds")
            if workload_payload
            else (round(workload_elapsed, 6) if workload_elapsed is not None else None)
        ),
        "cleanup_status": cleanup["status"],
        "supporting_evidence_paths": supporting,
    }
    validate_system_trial(record)
    metadata = {
        "plan": dict(row),
        "decision": {
            "applied_profile": profile,
            "applied_image_id": image_id,
            "requested_backend": decision.requested_backend,
            "effective_backend": decision.effective_backend,
            "backend_version": decision.backend_version,
            "fallback_used": decision.fallback_used,
            "fallback_error_category": decision.fallback_error_category,
        },
        "trial_error_category": trial_error_category,
        "workload_exit_code": None if executed is None else executed.returncode,
        "workload_checksum": workload_payload.get("checksum"),
        "actual_resources": actual_resources,
        "pending_reasons": pending_reasons,
    }
    _write_json_new(run_dir / "trial-metadata.json", metadata)
    if cleanup["status"] != "completed":
        raise RuntimeError(f"cleanup failed after trial {row['trial_id']}")
    return record


def _preflight(
    *,
    context: str,
    dataset: Mapping[str, Any],
    image_catalog: Mapping[str, Any],
    plan: list[Mapping[str, Any]],
    plan_path: Path,
) -> dict[str, Any]:
    current = _kubectl(context, ["config", "current-context"])
    if current.returncode != 0 or current.stdout.decode().strip() != context:
        raise RuntimeError("selected Kubernetes context is not current/available")
    namespace = _get_json(context, ["get", "namespace", NAMESPACE])
    if namespace is None:
        raise RuntimeError("demo namespace is unavailable")
    if namespace.get("metadata", {}).get("labels", {}).get(SAFETY_LABEL) != SAFETY_VALUE:
        raise RuntimeError(f"namespace requires {SAFETY_LABEL}={SAFETY_VALUE}")
    nodes = _get_json(context, ["get", "nodes"])
    if nodes is None or len(nodes.get("items", [])) != 1:
        raise RuntimeError("v4 execution requires one disposable node")
    node = nodes["items"][0]
    conditions = {
        item.get("type"): item.get("status")
        for item in node.get("status", {}).get("conditions", [])
    }
    if conditions.get("Ready") != "True" or any(
        conditions.get(name) == "True"
        for name in ("MemoryPressure", "DiskPressure", "PIDPressure")
    ):
        raise RuntimeError("evaluation node is not healthy")
    hub = _get_json(context, ["get", "deployment", "hub", "-n", NAMESPACE])
    if hub is None or int(hub.get("status", {}).get("availableReplicas") or 0) != 1:
        raise RuntimeError("JupyterHub deployment is not available")
    all_pods = _get_json(context, ["get", "pods", "-n", NAMESPACE]) or {"items": []}
    user_pods = [
        pod
        for pod in all_pods.get("items", [])
        if pod.get("metadata", {}).get("labels", {}).get("component") == "singleuser-server"
    ]
    if user_pods:
        raise RuntimeError("preflight requires no running single-user servers")
    quotas = _get_json(context, ["get", "resourcequota", "-n", NAMESPACE]) or {"items": []}
    release = _run(["helm", "status", RELEASE, "-n", NAMESPACE, "-o", "json"])
    release_json = _json_command(release, "helm status")
    live_values = _run(["helm", "get", "values", RELEASE, "-n", NAMESPACE, "-a", "-o", "json"])
    if live_values.returncode != 0:
        raise RuntimeError("cannot read live Helm values")
    images = {
        name
        for status in node.get("status", {}).get("images", [])
        for name in (status.get("names") or [])
    }
    needed_image_ids: set[str] = set()
    index = {item["sample_id"]: item for item in dataset["items"]}
    for row in plan:
        item = index[row["representative_sample_id"]]
        decision = evaluate_item(
            row["recommender"],
            item,
            backend=create_backend(row["recommender"]),
            catalog_images=image_catalog["images"],
        )
        if not decision.predicted_image_id:
            raise RuntimeError("plan contains an unavailable image decision")
        needed_image_ids.add(decision.predicted_image_id)
    missing_images = []
    for image_id in sorted(needed_image_ids):
        reference = image_catalog["images"][image_id]["reference"]
        digest = reference.split("@", 1)[1]
        if reference not in images and not any(name.endswith("@" + digest) for name in images):
            missing_images.append(image_id)
    if missing_images:
        raise RuntimeError("warm-cache precondition failed for images: " + ", ".join(missing_images))
    commit, dirty, dirty_paths = _git_state()
    version = _get_json(context, ["version"]) or {}
    hpas = _get_json(context, ["get", "hpa", "-A"]) or {"items": []}
    inputs = {
        str(path.relative_to(ROOT)): _sha256(path)
        for path in (
            DEFAULT_DATASET,
            ROOT / "docs" / "evaluation" / "EVALUATION_V4_PROTOCOL.md",
            ROOT / "docs" / "evaluation" / "EVIDENCE_COLLECTION_V4.md",
            ROOT / "evaluation_v4" / "plan_system.py",
            ROOT / "evaluation_v4" / "run_system.py",
            ROOT / dataset["image_catalog"]["source_path"],
            *SOURCE_FILES,
        )
    }
    return {
        "protocol_version": "4.0.0",
        "captured_at_utc": _utc_now(),
        "environment_id": "local-single-node-v4-" + _sha256_text(context)[:12],
        "kubernetes_context_pseudonym": "context-sha256:" + _sha256_text(context),
        "namespace": NAMESPACE,
        "namespace_safety_label": f"{SAFETY_LABEL}={SAFETY_VALUE}",
        "kubernetes_version": version.get("serverVersion", {}),
        "node_count": 1,
        "node_capacity": node.get("status", {}).get("capacity", {}),
        "node_allocatable": node.get("status", {}).get("allocatable", {}),
        "node_conditions": conditions,
        "container_runtime": node.get("status", {}).get("nodeInfo", {}).get("containerRuntimeVersion"),
        "resource_quota_count": len(quotas.get("items", [])),
        "horizontal_pod_autoscaler_count_clusterwide": len(hpas.get("items", [])),
        "metrics_api_available": _kubectl(context, ["get", "apiservice", "v1beta1.metrics.k8s.io"]).returncode == 0,
        "primary_measurement_source": "cgroup_v2_in_container_window",
        "sample_interval_seconds": SAMPLE_INTERVAL_SECONDS,
        "pending_deadline_seconds": PENDING_DEADLINE_SECONDS,
        "spawn_deadline_seconds": SPAWN_DEADLINE_SECONDS,
        "helm_release": {
            "name": RELEASE,
            "status": release_json.get("info", {}).get("status"),
            "chart": release_json.get("chart"),
            "revision": release_json.get("version"),
            "computed_values_sha256": hashlib.sha256(live_values.stdout).hexdigest(),
        },
        "git_commit": commit,
        "git_dirty": dirty,
        "git_dirty_paths": dirty_paths,
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": canonical_sha256(dataset),
        "plan_path": str(plan_path.relative_to(ROOT)),
        "plan_sha256": _sha256(plan_path),
        "plan_records": len(plan),
        "methods": sorted({row["recommender"] for row in plan}),
        "repeats": len({row["repeat_block"] for row in plan}),
        "cache_condition": "warm_required_verified_from_node_image_digests",
        "required_image_ids": sorted(needed_image_ids),
        "input_sha256": inputs,
        "privacy": "no credentials, usernames, node names, addresses, or raw notebook content retained",
    }


def _integrity_manifest(output: Path) -> None:
    path = output / "SHA256SUMS"
    lines = []
    for candidate in sorted(output.rglob("*")):
        if candidate.is_file() and candidate != path:
            lines.append(f"{_sha256(candidate)}  {candidate.relative_to(output)}\n")
    _write_new(path, "".join(lines))


def execute(args: argparse.Namespace) -> dict[str, Any]:
    dataset = load_dataset(args.dataset)
    image_catalog = _load_locked_image_catalog(dataset)
    plan = _read_plan(args.plan, dataset)
    if args.dry_run:
        return {
            "mode": "dry_run",
            "cluster_mutation": False,
            "records": len(plan),
            "methods": sorted({row["recommender"] for row in plan}),
            "families": len({row["workload_family"] for row in plan}),
            "repeats": len({row["repeat_block"] for row in plan}),
        }
    environment = _preflight(
        context=args.context,
        dataset=dataset,
        image_catalog=image_catalog,
        plan=plan,
        plan_path=args.plan,
    )
    if args.preflight_only:
        return {"mode": "preflight_only", "status": "pass", "environment": environment}
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite system evidence directory {args.output}")
    args.output.mkdir(parents=True)
    _write_json_new(args.output / "environment.json", environment)
    _write_json_new(
        args.output / "run-manifest.json",
        {
            "schema_version": "system-run-manifest-v4.0.0",
            "experiment_id": args.experiment_id,
            "created_at_utc": _utc_now(),
            "plan_path": environment["plan_path"],
            "plan_sha256": environment["plan_sha256"],
            "attempted_trials": len(plan),
            "failure_retention": "all attempted trials remain in system-trials.jsonl",
            "cleanup_policy": "stop through Hub and verify no synthetic-user pod before next trial",
            "username_policy": "one pseudonymous synthetic user; username is not retained in evidence",
        },
    )
    hub = HubSession(args.hub_url, args.username)
    hub.login()
    workloads = {
        item["workload_id"]: item for item in load_manifest(ROOT / "benchmarks" / "workloads-v3.yaml")["workloads"]
    }
    completed = 0
    for row in plan:
        run_dir = args.output / "runs" / row["trial_id"]
        run_dir.mkdir(parents=True, exist_ok=False)
        record = _trial(
            row=row,
            dataset=dataset,
            image_catalog=image_catalog,
            workloads=workloads,
            context=args.context,
            hub=hub,
            username=args.username,
            experiment_id=args.experiment_id,
            environment_id=environment["environment_id"],
            git_commit=environment["git_commit"],
            run_dir=run_dir,
        )
        _append_jsonl(args.output / "system-trials.jsonl", record)
        completed += 1
        print(
            json.dumps(
                {
                    "completed": completed,
                    "planned": len(plan),
                    "trial_id": record["trial_id"],
                    "pod_ready": record["pod_ready"],
                    "workload_success": record["workload_success"],
                    "oom_killed": record["oom_killed"],
                    "pending_failure": record["pending_failure"],
                    "metrics_available": record["cpu_usage_mean_m"] is not None
                    and record["memory_usage_mean_mib"] is not None,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    _integrity_manifest(args.output)
    return {
        "mode": "execute",
        "status": "completed",
        "attempted_trials": len(plan),
        "completed_records": completed,
        "output": str(args.output),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run protocol-v4 system trials through JupyterHub.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--context", default="orbstack")
    parser.add_argument("--hub-url", default="http://127.0.0.1:18000")
    parser.add_argument("--username", default="v4-system-eval")
    parser.add_argument("--output", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.execute and args.output is None:
        parser.error("--output is required with --execute")
    if args.output is not None:
        args.output = args.output.resolve()
        try:
            args.output.relative_to(ROOT)
        except ValueError as exc:
            raise ValueError("system evidence output must remain inside the repository") from exc
    args.plan = args.plan.resolve()
    try:
        args.plan.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("system plan must remain inside the repository") from exc
    print(json.dumps(execute(args), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
