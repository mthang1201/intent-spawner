"""Run a bounded observed re-provisioning matrix through the live Hub flow."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping

from evaluation_v4.dataset import DEFAULT_DATASET, load_dataset
from evaluation_v4.run_system import (
    HubSession,
    NAMESPACE,
    ROOT,
    _clean_preview,
    _copy_sources,
    _events,
    _get_json,
    _load_locked_image_catalog,
    _parse_payload,
    _pod_ready,
    _single_user_pods,
    _verify_pod,
    _write_json_new,
    _write_new,
    _kubectl,
)
from evaluation_v4.schemas import REPROVISION_SCHEMA, validate_reprovision_trial
from experiments.kubernetes_evidence import extract_pod_evidence


SCENARIOS = (
    {
        "name": "invalid-preview-pre-stop",
        "sample_id": "large-aggregation-canonical-en",
        "workload_family": "large-aggregation",
        "from_values": {
            "intent": "Run a few basic Python calculations and check a checksum.",
            "dataset_size_gb": 0.05,
            "code_context": "",
        },
        "to_values": {
            "intent": "Aggregate a larger CSV and compare group totals.",
            "dataset_size_gb": 2.2,
            "code_context": "import pandas as pd\ndf.groupby('group').amount.sum()",
        },
        "inject_invalid_preview": True,
        "expected_outcome": "failed_pre_stop",
    },
    {
        "name": "small-to-large",
        "sample_id": "large-aggregation-canonical-en",
        "workload_family": "large-aggregation",
        "from_values": {
            "intent": "Run a few basic Python calculations and check a checksum.",
            "dataset_size_gb": 0.05,
            "code_context": "",
        },
        "to_values": {
            "intent": "Aggregate a larger CSV and compare group totals.",
            "dataset_size_gb": 2.2,
            "code_context": "import pandas as pd\ndf.groupby('group').amount.sum()",
        },
        "inject_invalid_preview": False,
        "expected_outcome": "completed",
    },
    {
        "name": "large-to-small",
        "sample_id": "small-csv-canonical-en",
        "workload_family": "small-csv",
        "from_values": {
            "intent": "Aggregate a larger CSV and compare group totals.",
            "dataset_size_gb": 2.2,
            "code_context": "import pandas as pd\ndf.groupby('group').amount.sum()",
        },
        "to_values": {
            "intent": "Run a few basic Python calculations and check a checksum.",
            "dataset_size_gb": 0.05,
            "code_context": "",
        },
        "inject_invalid_preview": False,
        "expected_outcome": "completed",
    },
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _wait_ready(
    context: str,
    username: str,
    *,
    previous_uid: str | None = None,
    timeout: float = 240,
) -> tuple[dict[str, Any] | None, float]:
    started = time.monotonic()
    deadline = started + timeout
    unavailable_at: float | None = None
    while time.monotonic() < deadline:
        pods = _single_user_pods(context, username)
        if len(pods) > 1:
            raise RuntimeError("multiple synthetic-user pods found")
        if pods:
            pod = pods[0]
            uid = pod.get("metadata", {}).get("uid")
            if uid != previous_uid and _pod_ready(pod):
                reference = unavailable_at if previous_uid is not None else started
                return pod, time.monotonic() - (reference or started)
            if previous_uid is not None and uid != previous_uid and unavailable_at is None:
                unavailable_at = time.monotonic()
        elif previous_uid is not None and unavailable_at is None:
            unavailable_at = time.monotonic()
        time.sleep(0.25)
    return None, time.monotonic() - started


def _xsrf_reprovision(hub: HubSession, *, timeout: float = 60) -> str:
    """Wait for Hub state to catch up after the Kubernetes pod becomes Ready."""

    deadline = time.monotonic() + timeout
    last_status: int | None = None
    while time.monotonic() < deadline:
        status, text, _ = hub._call("GET", "/hub/reprovision")
        last_status = status
        if status == 200:
            return hub._xsrf(text)
        if status != 409:
            raise RuntimeError(f"re-provision page returned HTTP {status}")
        time.sleep(0.25)
    raise RuntimeError(
        f"Hub did not report the server running before timeout; last HTTP {last_status}"
    )


def _emergency_cleanup(context: str, hub_url: str, username: str) -> None:
    """Best-effort cleanup for a partially completed synthetic-user scenario."""

    try:
        hub = HubSession(hub_url, username)
        hub.login()
        hub.stop()
    except Exception:
        pass
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and _single_user_pods(context, username):
        time.sleep(0.5)
    _kubectl(
        context,
        [
            "delete",
            "pvc",
            f"claim-{username}",
            "-n",
            NAMESPACE,
            "--ignore-not-found=true",
            "--wait=true",
            "--timeout=60s",
        ],
        timeout=70,
    )


def _reprovision_preview(
    hub: HubSession, values: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    xsrf = _xsrf_reprovision(hub)
    status, text, _ = hub._call(
        "POST",
        "/hub/reprovision",
        payload={"action": "preview", **values},
        headers={"X-XSRFToken": xsrf},
    )
    if status != 200:
        raise RuntimeError(f"re-provision preview returned HTTP {status}: {text[:300]}")
    return json.loads(text), xsrf


def _accept_payload(
    preview: Mapping[str, Any], values: Mapping[str, Any], *, invalid: bool
) -> dict[str, Any]:
    proposed = preview["proposed"]
    dynamic = preview.get("dynamic_resource_preview", {})
    return {
        "action": "accept",
        "acknowledge_restart": True,
        "preview_version": preview["preview_version"],
        "expected_current_event_id": preview["current"]["event_id"],
        "expected_recommended_profile": proposed["recommended_profile"],
        "expected_recommended_image_id": proposed["recommended_image_id"],
        "expected_policy_version": proposed["policy_version"],
        "expected_catalog_version": proposed["catalog_version"],
        "dynamic_preview_id": (
            "invalid-preview-id" if invalid else dynamic.get("dynamic_preview_id", "")
        ),
        **values,
    }


def _sentinel(context: str, pod_name: str, content: str) -> str:
    code = (
        "from pathlib import Path; "
        f"Path('/home/jovyan/v4-reprovision-sentinel.txt').write_text({content!r}, encoding='utf-8')"
    )
    result = _kubectl(
        context,
        ["exec", "-n", NAMESPACE, pod_name, "--", "python", "-c", code],
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("cannot create PVC sentinel")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _sentinel_hash(context: str, pod_name: str) -> str | None:
    code = (
        "from pathlib import Path; import hashlib; "
        "print(hashlib.sha256(Path('/home/jovyan/v4-reprovision-sentinel.txt').read_bytes()).hexdigest())"
    )
    result = _kubectl(
        context,
        ["exec", "-n", NAMESPACE, pod_name, "--", "python", "-c", code],
        timeout=30,
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()


def _resume_workload(
    context: str, pod_name: str, seed: int
) -> tuple[bool, str, str, dict[str, Any] | None]:
    _copy_sources(context, pod_name)
    result = _kubectl(
        context,
        [
            "exec",
            "-n",
            NAMESPACE,
            pod_name,
            "--",
            "env",
            "PYTHONPATH=/tmp/v4",
            "python",
            "/tmp/v4/pod_runner.py",
            "--workload-id",
            "h01_small_stream",
            "--seed",
            str(seed),
            "--sample-interval",
            "0.1",
            "--manifest",
            "/tmp/v4/benchmarks/workloads-v3.yaml",
        ],
        timeout=105,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    payload = _parse_payload(stdout)
    return result.returncode == 0 and payload is not None, stdout, stderr, payload


def _stop_and_delete_pvc(context: str, hub: HubSession, username: str) -> dict[str, Any]:
    stop_status = hub.stop()
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and _single_user_pods(context, username):
        time.sleep(0.5)
    remaining = _single_user_pods(context, username)
    pvc_name = f"claim-{username}"
    deleted = _kubectl(
        context,
        ["delete", "pvc", pvc_name, "-n", NAMESPACE, "--wait=true", "--timeout=60s"],
        timeout=70,
    )
    return {
        "stop_http_status": stop_status,
        "remaining_user_pods": len(remaining),
        "pvc_name_pseudonym": "claim-synthetic-reprovision-user",
        "pvc_delete_returncode": deleted.returncode,
        "status": (
            "completed"
            if stop_status in {202, 204, 404} and not remaining and deleted.returncode == 0
            else "failed"
        ),
    }


def _run_scenario(
    *,
    scenario: Mapping[str, Any],
    index: int,
    context: str,
    hub_url: str,
    git_commit: str,
    environment_id: str,
    output: Path,
    catalog: Mapping[str, Any],
    experiment_id: str,
) -> dict[str, Any]:
    username = f"v4-reprovision-{index}"
    hub = HubSession(hub_url, username)
    hub.login()
    run_dir = output / "runs" / scenario["name"]
    run_dir.mkdir(parents=True, exist_ok=False)
    initial_preview = hub.preview(scenario["from_values"])
    initial_rec = initial_preview["recommendation"]
    initial_profile = (
        "large" if initial_rec["profile"] == "gpu_or_large" else initial_rec["profile"]
    )
    initial_image = initial_rec["image_id"]
    spawn = hub.spawn(
        scenario["from_values"],
        initial_preview,
        action="accept",
        applied_profile=initial_profile,
        applied_image_id=initial_image,
    )
    if not spawn["accepted"]:
        raise RuntimeError("initial server spawn was rejected")
    old_pod, _ = _wait_ready(context, username)
    if old_pod is None:
        raise RuntimeError("initial server did not become ready")
    _verify_pod(
        old_pod,
        profile=initial_profile,
        image_id=initial_image,
        image_reference=catalog["images"][initial_image]["reference"],
    )
    sentinel_content = f"protocol-v4-sentinel-{index}-20260808"
    sentinel_expected = _sentinel(context, old_pod["metadata"]["name"], sentinel_content)
    preview, xsrf = _reprovision_preview(hub, scenario["to_values"])
    proposed = preview["proposed"]
    to_profile = (
        "large"
        if proposed["applied_profile"] == "gpu_or_large"
        else proposed["applied_profile"]
    )
    to_image = proposed["recommended_image_id"]
    accept_body = _accept_payload(
        preview, scenario["to_values"], invalid=scenario["inject_invalid_preview"]
    )
    accepted_at = _utc_now()
    started = time.monotonic()
    status, text, _ = hub._call(
        "POST",
        "/hub/reprovision",
        payload=accept_body,
        headers={"X-XSRFToken": xsrf},
    )
    accept_response = {
        "http_status": status,
        "payload": json.loads(text),
        "invalid_preview_injected": scenario["inject_invalid_preview"],
    }
    replacement: dict[str, Any] | None = None
    downtime: float | None = None
    outcome: str
    if scenario["inject_invalid_preview"]:
        outcome = "failed_pre_stop"
        current = _single_user_pods(context, username)
        if len(current) != 1 or current[0]["metadata"]["uid"] != old_pod["metadata"]["uid"]:
            raise RuntimeError("pre-stop rejection changed the running pod")
        replacement = current[0]
        downtime = 0.0
    else:
        if status != 202:
            raise RuntimeError(f"re-provision accept returned HTTP {status}")
        replacement, downtime = _wait_ready(
            context,
            username,
            previous_uid=old_pod["metadata"]["uid"],
        )
        outcome = "completed" if replacement is not None else "failed_after_stop"
    if replacement is None:
        replacement_ready = False
        pvc_ok = False
        resume_ok = False
        stdout = ""
        stderr = ""
        workload_payload = None
        new_evidence: dict[str, Any] = {}
    else:
        replacement_ready = _pod_ready(replacement)
        if outcome == "completed":
            _verify_pod(
                replacement,
                profile=to_profile,
                image_id=to_image,
                image_reference=catalog["images"][to_image]["reference"],
            )
        pvc_ok = _sentinel_hash(context, replacement["metadata"]["name"]) == sentinel_expected
        resume_ok, stdout, stderr, workload_payload = _resume_workload(
            context, replacement["metadata"]["name"], 20260808 + index
        )
        refreshed = _get_json(
            context,
            ["get", "pod", replacement["metadata"]["name"], "-n", NAMESPACE],
        ) or replacement
        new_evidence = extract_pod_evidence(
            refreshed, _events(context, replacement["metadata"]["name"])
        )
    old_evidence = extract_pod_evidence(
        old_pod, _events(context, old_pod["metadata"]["name"])
    )
    _write_json_new(run_dir / "initial-preview.json", _clean_preview(initial_preview))
    _write_json_new(run_dir / "reprovision-preview.json", preview)
    _write_json_new(run_dir / "accept-response.json", accept_response)
    _write_json_new(run_dir / "old-pod-evidence.json", old_evidence)
    _write_json_new(run_dir / "replacement-pod-evidence.json", new_evidence)
    _write_json_new(
        run_dir / "sentinel-proof.json",
        {
            "expected_sha256": sentinel_expected,
            "observed_sha256": (
                _sentinel_hash(context, replacement["metadata"]["name"])
                if replacement is not None
                else None
            ),
            "content_retained": False,
        },
    )
    _write_new(run_dir / "resume-workload.stdout", stdout)
    _write_new(run_dir / "resume-workload.stderr", stderr)
    cleanup = _stop_and_delete_pvc(context, hub, username)
    _write_json_new(run_dir / "cleanup.json", cleanup)
    paths = [
        str(path.relative_to(ROOT))
        for path in sorted(run_dir.iterdir())
        if path.is_file()
    ]
    record = {
        "schema_version": REPROVISION_SCHEMA,
        "evidence_class": "observed",
        "trial_id": f"v4-reprovision-{scenario['name']}",
        "experiment_id": experiment_id,
        "timestamp_utc": accepted_at,
        "git_commit": git_commit,
        "environment_id": environment_id,
        "recommender": "rule_based_context",
        "sample_id": scenario["sample_id"],
        "workload_family": scenario["workload_family"],
        "repeat_index": index,
        "from_profile": initial_profile,
        "to_profile": to_profile,
        "from_image_id": initial_image,
        "to_image_id": to_image,
        "outcome": outcome,
        "replacement_ready": replacement_ready if outcome == "completed" else False,
        "pvc_continuity_verified": pvc_ok,
        "workload_resume_verified": resume_ok,
        "pending_failure": False,
        "oom_killed": bool(new_evidence.get("oom_killed")),
        "downtime_seconds": round(downtime, 6) if downtime is not None else None,
        "rollback_attempted": False,
        "rollback_successful": False,
        "cleanup_status": cleanup["status"],
        "supporting_evidence_paths": paths,
    }
    validate_reprovision_trial(record)
    if record["outcome"] != scenario["expected_outcome"]:
        raise RuntimeError("re-provision scenario outcome differs from its pre-specified expectation")
    if cleanup["status"] != "completed":
        raise RuntimeError("re-provision cleanup failed")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run bounded protocol-v4 re-provision trials.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--context", default="orbstack")
    parser.add_argument("--hub-url", default="http://127.0.0.1:18000")
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output = args.output.resolve()
    args.output.relative_to(ROOT)
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")
    args.output.mkdir(parents=True)
    dataset = load_dataset(args.dataset)
    catalog = _load_locked_image_catalog(dataset)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    _write_json_new(
        args.output / "run-manifest.json",
        {
            "schema_version": "reprovision-run-manifest-v4.0.0",
            "experiment_id": args.experiment_id,
            "created_at_utc": _utc_now(),
            "scenarios": list(SCENARIOS),
            "sentinel_content_retained": False,
            "failure_injection": "invalid server-side dynamic preview token; rejected before stop",
        },
    )
    results_path = args.output / "reprovision-trials.jsonl"
    for index, scenario in enumerate(SCENARIOS):
        try:
            record = _run_scenario(
                scenario=scenario,
                index=index,
                context=args.context,
                hub_url=args.hub_url,
                git_commit=commit,
                environment_id=args.environment_id,
                output=args.output,
                catalog=catalog,
                experiment_id=args.experiment_id,
            )
        except Exception:
            _emergency_cleanup(args.context, args.hub_url, f"v4-reprovision-{index}")
            raise
        with results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        print(json.dumps({"trial_id": record["trial_id"], "outcome": record["outcome"]}), flush=True)
    sums = []
    for path in sorted(args.output.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            sums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(args.output)}\n")
    _write_new(args.output / "SHA256SUMS", "".join(sums))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
