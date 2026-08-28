"""Strict workload-manifest loading for Protocol-v5 E4."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from .workloads import OPERATIONS, execute_workload, validate_parameters


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "benchmarks_v5" / "resource-envelope-workloads-v1.yaml"
SCHEMA_VERSION = "protocol-v5-resource-workloads-v1.0.0"
PROTOCOL_VERSION = "5.0.0"
MEMORY_LATTICE_MIB = [64, 96, 128, 192, 256, 384, 512, 768, 1024, 1280, 1536, 1792, 2048]
CPU_LATTICE_M = [100, 200, 300, 500, 750, 1000, 1500, 2000]
FAMILY_FIELDS = frozenset({
    "family_id", "workload_instance_id", "workload_instance_version", "operation",
    "description", "deterministic_seed", "parameters", "expected_marker_sha256",
    "correctness_oracle", "timeout_seconds", "data_source",
})
FORBIDDEN_KEYS = frozenset({
    "intent", "method", "recommendation", "recommended_profile", "profile",
    "oracle", "acceptable_profiles", "expected_minimum_profile",
})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
INSTANCE_RE = re.compile(r"^[a-z][a-z0-9._-]{2,127}$")
SAFE_RULE = {
    "version": "protocol-v5-resource-safe-rule-v1.1.0",
    "reference_repeats": 3,
    "probe_repeats": 2,
    "joint_verification_repeats": 5,
    "median_runtime_ratio_max": 1.25,
    "individual_runtime_ratio_max": 1.5,
    "required_joint_successes": 5,
    "infrastructure_replacements_per_trial": 1,
    "reference_stability_rule_version": "max-relative-spread-v1.0.0",
    "reference_max_relative_spread": 0.20,
}


def _exact(value: Mapping[str, Any], fields: set[str] | frozenset[str], label: str) -> None:
    missing = sorted(set(fields) - set(value))
    extra = sorted(set(value) - set(fields))
    if missing or extra:
        raise ValueError(f"{label}: missing={missing} extra={extra}")


def validate_resource_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise ValueError("resource workload manifest must be an object")
    fields = {
        "schema_version", "protocol_version", "evidence_role", "description",
        "master_seed", "candidate_lattices", "limits", "safe_rule", "workloads",
    }
    _exact(manifest, fields, "resource workload manifest")
    if manifest["schema_version"] != SCHEMA_VERSION or manifest["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError("unsupported resource workload manifest version")
    if manifest["evidence_role"] != "confirmatory_reference":
        raise ValueError("resource workload manifest must be a confirmatory reference")
    if not isinstance(manifest["master_seed"], int) or isinstance(manifest["master_seed"], bool):
        raise ValueError("master_seed must be an integer")
    lattices = manifest["candidate_lattices"]
    if lattices != {"memory_mib": MEMORY_LATTICE_MIB, "cpu_m": CPU_LATTICE_M}:
        raise ValueError("candidate lattices differ from the frozen E4 contract")
    if manifest["limits"] != {
        "max_cpu_m": 2000,
        "max_memory_mib": 2048,
        "max_timeout_seconds": 120,
        "single_active_workload": True,
        "required_cgroup_version": "v2",
    }:
        raise ValueError("resource limits differ from the frozen E4 contract")
    if manifest["safe_rule"] != SAFE_RULE:
        raise ValueError("safe rule differs from the frozen E4 contract")
    workloads = manifest["workloads"]
    if not isinstance(workloads, list) or len(workloads) != 16:
        raise ValueError("resource workload manifest requires exactly 16 families")
    ids: set[str] = set()
    instance_ids: set[str] = set()
    operations: set[str] = set()
    for index, workload in enumerate(workloads):
        if not isinstance(workload, Mapping):
            raise ValueError(f"workloads[{index}] must be an object")
        _exact(workload, FAMILY_FIELDS, f"workloads[{index}]")
        if set(workload) & FORBIDDEN_KEYS:
            raise ValueError(f"workloads[{index}] contains recommendation/oracle fields")
        family_id = workload["family_id"]
        if not isinstance(family_id, str) or not ID_RE.fullmatch(family_id) or family_id in ids:
            raise ValueError(f"invalid or duplicate family_id {family_id!r}")
        ids.add(family_id)
        instance_id = workload["workload_instance_id"]
        if (
            not isinstance(instance_id, str)
            or not INSTANCE_RE.fullmatch(instance_id)
            or instance_id in instance_ids
        ):
            raise ValueError(f"invalid or duplicate workload_instance_id {instance_id!r}")
        instance_ids.add(instance_id)
        if workload["workload_instance_version"] != "1.0.0":
            raise ValueError(f"{family_id}: unsupported workload instance version")
        operation = workload["operation"]
        if operation not in OPERATIONS or operation in operations:
            raise ValueError(f"invalid or duplicate operation {operation!r}")
        operations.add(operation)
        if not isinstance(workload["description"], str) or not workload["description"].strip():
            raise ValueError(f"{family_id}: description must be non-blank")
        if not isinstance(workload["deterministic_seed"], int) or isinstance(workload["deterministic_seed"], bool):
            raise ValueError(f"{family_id}: deterministic_seed must be an integer")
        validate_parameters(operation, workload["parameters"])
        if not isinstance(workload["expected_marker_sha256"], str) or not SHA256_RE.fullmatch(workload["expected_marker_sha256"]):
            raise ValueError(f"{family_id}: expected marker must be SHA-256")
        oracle = workload["correctness_oracle"]
        if (
            not isinstance(oracle, Mapping)
            or set(oracle) != {"checker_version", "expected_invariants"}
            or oracle["checker_version"] != "frozen-invariant-checker-v1.0.0"
            or not isinstance(oracle["expected_invariants"], Mapping)
            or not oracle["expected_invariants"]
        ):
            raise ValueError(f"{family_id}: invalid frozen correctness oracle")
        if workload["timeout_seconds"] != 120:
            raise ValueError(f"{family_id}: timeout must be the frozen 120-second boundary")
        if workload["data_source"] != {
            "type": "synthetic",
            "external_input": False,
            "persist_generated_data": False,
        }:
            raise ValueError(f"{family_id}: only non-persisted synthetic data is permitted")
    if operations != set(OPERATIONS):
        raise ValueError("manifest must contain every registered workload operation exactly once")
    return dict(manifest)


def load_resource_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    return validate_resource_manifest(payload)


def workloads_by_id(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    validated = validate_resource_manifest(manifest)
    return {item["family_id"]: dict(item) for item in validated["workloads"]}


def verify_workload_markers(manifest: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_resource_manifest(manifest)
    mismatches = []
    for workload in validated["workloads"]:
        result = execute_workload(workload)
        if (
            result.marker_sha256 != workload["expected_marker_sha256"]
            or not result.correctness_invariants_ok
        ):
            mismatches.append(workload["family_id"])
    if mismatches:
        raise ValueError(f"frozen workload correctness markers differ for {mismatches}")
    return {"status": "pass", "verified_markers": len(validated["workloads"])}


def workload_fingerprint(workload: Mapping[str, Any]) -> str:
    import hashlib
    import json

    payload = {
        key: workload[key]
        for key in (
            "family_id", "workload_instance_id", "workload_instance_version",
            "operation", "deterministic_seed", "parameters",
            "expected_marker_sha256", "correctness_oracle", "timeout_seconds",
        )
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
