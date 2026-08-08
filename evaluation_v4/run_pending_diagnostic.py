"""Fault-inject one bounded unschedulable pod and retain diagnostic evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from evaluation_v4.dataset import DEFAULT_DATASET, load_dataset
from evaluation_v4.run_system import (
    NAMESPACE,
    ROOT,
    _get_json,
    _kubectl,
    _load_locked_image_catalog,
    _write_json_new,
    _write_new,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", default="orbstack")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    catalog = _load_locked_image_catalog(load_dataset(DEFAULT_DATASET))
    name = "v4-pending-diagnostic"
    manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {"app": "v4-pending-diagnostic"},
        },
        "spec": {
            "restartPolicy": "Never",
            "containers": [{
                "name": "diagnostic",
                "image": catalog["images"]["minimal-python"]["reference"],
                "command": ["sh", "-c", "sleep 60"],
                "resources": {
                    "requests": {"cpu": "99", "memory": "64Mi"},
                    "limits": {"cpu": "99", "memory": "64Mi"},
                },
            }],
        },
    }
    _write_json_new(output / "requested-pod.json", manifest)
    created = _kubectl(
        args.context,
        ["create", "-f", "-"],
        input_bytes=(json.dumps(manifest) + "\n").encode(),
        timeout=30,
    )
    if created.returncode != 0:
        raise RuntimeError(created.stderr.decode(errors="replace"))
    detected = False
    evidence_events = []
    try:
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            payload = _get_json(
                args.context,
                ["get", "events", "-n", NAMESPACE, "--field-selector", f"involvedObject.name={name}"],
            ) or {"items": []}
            evidence_events = [{
                "reason": item.get("reason"),
                "type": item.get("type"),
                "message": item.get("message"),
                "event_time": item.get("eventTime") or item.get("lastTimestamp"),
            } for item in payload.get("items", [])]
            detected = any(
                item["reason"] == "FailedScheduling" and "Insufficient cpu" in (item["message"] or "")
                for item in evidence_events
            )
            if detected:
                break
            time.sleep(0.5)
        pod = _get_json(args.context, ["get", "pod", name, "-n", NAMESPACE]) or {}
        _write_json_new(output / "observed-pod.json", {
            "phase": pod.get("status", {}).get("phase"),
            "conditions": pod.get("status", {}).get("conditions", []),
            "requested_resources": manifest["spec"]["containers"][0]["resources"],
        })
        _write_json_new(output / "observed-events.json", evidence_events)
    finally:
        deleted = _kubectl(
            args.context,
            ["delete", "pod", name, "-n", NAMESPACE, "--ignore-not-found=true", "--wait=true", "--timeout=60s"],
            timeout=70,
        )
    result = {
        "schema_version": "pending-detector-diagnostic-v4.0.0",
        "evidence_class": "observed",
        "diagnostic_only": True,
        "timestamp_utc": _now(),
        "namespace": NAMESPACE,
        "requested_cpu_cores": 99,
        "pending_detected": detected,
        "unschedulable_detected": detected,
        "failed_scheduling_reason_detected": detected,
        "cleanup_completed": deleted.returncode == 0,
    }
    _write_json_new(output / "result.json", result)
    if not detected or deleted.returncode != 0:
        raise RuntimeError(f"diagnostic failed: {result}")
    sums = []
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS":
            sums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    _write_new(output / "SHA256SUMS", "".join(sums))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
