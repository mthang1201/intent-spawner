"""Append-only raw evidence, integrity, and schema validation for E4."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from evaluation_v5.provenance import write_json_exclusive

from .derive import DERIVATION_SCHEMA_VERSION
from .models import TRIAL_SCHEMA_VERSION, TrialObservation, TrialSpec


OBSERVATION_SCHEMA_VERSION = TRIAL_SCHEMA_VERSION
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def validate_trial_observation(observation: TrialObservation, spec: TrialSpec | None = None) -> None:
    if not isinstance(observation, TrialObservation):
        raise ValueError("trial observation has the wrong type")
    if observation.schema_version != OBSERVATION_SCHEMA_VERSION:
        raise ValueError("unsupported resource trial observation version")
    if observation.phase not in {"reference", "memory_probe", "cpu_probe", "joint_verification"}:
        raise ValueError("unsupported calibration phase")
    if observation.cpu_m <= 0 or observation.cpu_m > 2000:
        raise ValueError("trial CPU is outside the E4 hard bound")
    if observation.memory_mib <= 0 or observation.memory_mib > 2048:
        raise ValueError("trial memory is outside the E4 hard bound")
    if not SHA256_RE.fullmatch(observation.expected_marker_sha256):
        raise ValueError("expected marker is not SHA-256")
    if observation.observed_marker_sha256 is not None and not SHA256_RE.fullmatch(observation.observed_marker_sha256):
        raise ValueError("observed marker is not SHA-256")
    if (
        not isinstance(observation.workload_timeout_seconds, int)
        or isinstance(observation.workload_timeout_seconds, bool)
        or not 1 <= observation.workload_timeout_seconds <= 120
    ):
        raise ValueError("trial timeout boundary is outside the E4 hard bound")
    if observation.runtime_seconds is not None and (
        not isinstance(observation.runtime_seconds, (int, float))
        or isinstance(observation.runtime_seconds, bool)
        or not math.isfinite(observation.runtime_seconds)
        or observation.runtime_seconds < 0
    ):
        raise ValueError("runtime must be a finite non-negative number")
    if (
        observation.runtime_seconds is not None
        and observation.runtime_seconds > observation.workload_timeout_seconds
        and not observation.timeout
    ):
        raise ValueError("runtime exceeds the trial timeout boundary without a timeout outcome")
    if observation.infrastructure_invalid != bool(observation.exclusion_reason):
        raise ValueError("infrastructure exclusion fields disagree")
    if observation.infrastructure_invalid and (observation.oom_killed or observation.timeout):
        raise ValueError("workload OOM or timeout cannot be infrastructure-invalid")
    if observation.correctness_marker_ok != (
        observation.observed_marker_sha256 == observation.expected_marker_sha256
    ):
        raise ValueError("correctness marker fields disagree")
    if not isinstance(observation.correctness_invariants_ok, bool):
        raise ValueError("correctness invariant result must be boolean")
    if not isinstance(observation.correctness_details, Mapping):
        raise ValueError("correctness details must be an object")
    if spec is not None:
        for name in (
            "run_id", "family_id", "workload_instance_id", "workload_fingerprint",
            "phase", "cpu_m", "memory_mib", "repeat_index",
            "deterministic_seed", "expected_marker_sha256", "replacement_of",
        ):
            if getattr(observation, name) != getattr(spec, name):
                raise ValueError(f"trial observation/spec mismatch for {name}")
        if observation.workload_timeout_seconds != spec.timeout_seconds:
            raise ValueError("trial observation/spec mismatch for workload_timeout_seconds")


def append_jsonl_fsync(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_observations(path: Path) -> list[TrialObservation]:
    if not path.exists():
        return []
    rows: list[TrialObservation] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            observation = TrialObservation.from_dict(payload)
            validate_trial_observation(observation)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path}:{line_number}: invalid resource trial record") from exc
        if observation.run_id in seen:
            raise ValueError(f"duplicate resource trial run_id {observation.run_id}")
        seen.add(observation.run_id)
        rows.append(observation)
    return rows


def load_decisions(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid decision JSON") from exc
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != "protocol-v5-resource-search-decision-v1.0.0"
            or not isinstance(value.get("decision_id"), str)
            or value["decision_id"] in seen
            or not isinstance(value.get("family_id"), str)
            or not isinstance(value.get("workload_instance_id"), str)
            or not SHA256_RE.fullmatch(str(value.get("workload_fingerprint", "")))
        ):
            raise ValueError(f"{path}:{line_number}: invalid decision record")
        seen.add(value["decision_id"])
        rows.append(value)
    return rows


def write_integrity_manifest(root: Path) -> Path:
    target = root / "SHA256SUMS"
    lines = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != target:
            lines.append(f"{file_sha256(path)}  {path.relative_to(root)}\n")
    with target.open("x", encoding="utf-8") as handle:
        handle.write("".join(lines))
        handle.flush()
        os.fsync(handle.fileno())
    return target


def verify_integrity(root: Path) -> dict[str, Any]:
    manifest = root / "SHA256SUMS"
    if not manifest.is_file():
        raise ValueError("resource evidence package is not sealed")
    expected: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        if not SHA256_RE.fullmatch(digest) or relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError("invalid resource integrity entry")
        if relative in expected:
            raise ValueError("duplicate resource integrity entry")
        expected[relative] = digest
    actual = {
        str(path.relative_to(root)): file_sha256(path)
        for path in root.rglob("*") if path.is_file() and path != manifest
    }
    if expected != actual:
        raise ValueError("resource evidence integrity mismatch")
    return {"status": "pass", "verified_files": len(actual), "manifest_sha256": file_sha256(manifest)}


def validate_evidence_package(root: Path, *, allow_unsealed: bool = False) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("resource evidence package lacks manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "protocol-v5-resource-calibration-run-v1.1.0":
        raise ValueError("unsupported resource run manifest")
    if manifest.get("trial_observation_schema_version") != OBSERVATION_SCHEMA_VERSION:
        raise ValueError("resource run manifest has an incompatible trial observation schema")
    integrity = None
    if (root / "SHA256SUMS").exists():
        integrity = verify_integrity(root)
    elif not allow_unsealed:
        raise ValueError("resource evidence package is not sealed")
    status = manifest.get("execution_status")
    records_path = root / "raw" / "trials.jsonl"
    observations = load_observations(records_path)
    decisions = load_decisions(root / "raw" / "decision-ledger.jsonl")
    trial_ids = {row.run_id for row in observations}
    if any(not set(row.get("source_trial_ids") or []).issubset(trial_ids) for row in decisions):
        raise ValueError("adaptive decision references an unknown trial")
    for observation in observations:
        sidecar = root / "raw" / "runs" / observation.run_id / "record.json"
        if not sidecar.is_file() or json.loads(sidecar.read_text(encoding="utf-8")) != observation.to_dict():
            raise ValueError(f"resource trial sidecar mismatch for {observation.run_id}")
    if status == "DRY_RUN":
        if observations or decisions or manifest.get("cluster_measurement_status") != "NOT_EXECUTED":
            raise ValueError("dry-run package contains or claims observations")
        if (root / "derived" / "safe-envelopes.json").exists():
            raise ValueError("dry-run package must not contain derived envelopes")
        environment = json.loads((root / "raw" / "environment.json").read_text(encoding="utf-8"))
        report = json.loads((root / "report" / "status.json").read_text(encoding="utf-8"))
        if (
            report.get("executed_trials") != 0
            or environment.get("hardware_measurements") is not None
            or environment.get("cgroup_measurements") is not None
            or environment.get("kubernetes_mutations") != []
        ):
            raise ValueError("dry-run package contains measurements, trials, or mutation claims")
    elif status == "OBSERVED":
        if not observations or not decisions:
            raise ValueError("observed resource package lacks trials or adaptive decisions")
        derived_path = root / "derived" / "safe-envelopes.json"
        if not derived_path.is_file():
            raise ValueError("observed resource package lacks derived envelopes")
        derived = json.loads(derived_path.read_text(encoding="utf-8"))
        if derived.get("schema_version") != DERIVATION_SCHEMA_VERSION:
            raise ValueError("observed resource package has an incompatible derivation schema")
    review_status = "NOT_APPLICABLE" if status == "DRY_RUN" else manifest.get("manual_review_status")
    eligible = False
    review_path = root / "report" / "manual-review.json"
    if review_path.is_file():
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review_status = review.get("decision", review.get("status"))
        eligible = review.get("decision") == "APPROVED" and review.get("eligible_for_comparison") is True
        if review.get("decision") in {"APPROVED", "REJECTED"}:
            source = root / "derived" / "safe-envelopes.json"
            if not source.is_file() or review.get("safe_envelopes_sha256") != file_sha256(source):
                raise ValueError("manual review is not bound to the derived envelopes")
            if review.get("prior_state") not in {"PENDING", "REQUIRED"}:
                raise ValueError("illegal manual-review prior state")
            status_payload = json.loads((root / "report" / "status.json").read_text(encoding="utf-8"))
            components = review.get("review_input_components")
            if (
                not isinstance(components, dict)
                or components != status_payload.get("review_input_components")
                or review.get("review_input_fingerprint") != status_payload.get("review_input_fingerprint")
                or canonical_sha256(components) != review.get("review_input_fingerprint")
                or any(file_sha256(root / relative) != digest for relative, digest in components.items())
            ):
                raise ValueError("manual review input fingerprint mismatch")
    return {
        "status": "pass",
        "execution_status": status,
        "trial_records": len(observations),
        "decision_records": len(decisions),
        "sealed": integrity is not None,
        "manual_review_status": review_status,
        "eligible_for_comparison": eligible,
        "integrity": integrity,
    }


__all__ = [
    "OBSERVATION_SCHEMA_VERSION", "append_jsonl_fsync", "canonical_sha256",
    "file_sha256", "load_decisions", "load_observations", "validate_evidence_package",
    "validate_trial_observation", "verify_integrity", "write_integrity_manifest",
]
