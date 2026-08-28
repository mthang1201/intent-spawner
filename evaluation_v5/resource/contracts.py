"""Frozen non-measurement contracts for Protocol-v5 E4."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from jsonschema import Draft202012Validator
import yaml

from .manifest import DEFAULT_MANIFEST, load_resource_manifest, workload_fingerprint


ROOT = Path(__file__).resolve().parents[2]
SEMANTIC_PATH = ROOT / "benchmarks_v5" / "resource-envelope-semantic-independence-v1.yaml"
SEMANTIC_SCHEMA_PATH = ROOT / "benchmarks_v5" / "protocol-v5-resource-semantic-independence-v1.schema.json"
ELIGIBILITY_PATH = ROOT / "benchmarks_v5" / "resource-envelope-cluster-eligibility-v1.yaml"
IMAGE_STATE_PATH = ROOT / "cluster_evaluation" / "resource-v5-image-state.yaml"
FREEZE_CONTRACT_PATH = ROOT / "benchmarks_v5" / "resource-envelope-freeze-contract-v1.yaml"
CROSSWALK_PATH = ROOT / "benchmarks_v5" / "resource-allocation-crosswalk-v1.yaml"
COMPARISON_SCHEMA_PATH = ROOT / "benchmarks_v5" / "protocol-v5-resource-allocation-comparison-v1.schema.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: contract must be an object")
    return value


def load_semantic_independence(
    path: Path = SEMANTIC_PATH,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    value = _load_yaml(path)
    schema = json.loads(SEMANTIC_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(value)
    manifest_ids = [item["family_id"] for item in load_resource_manifest(manifest_path)["workloads"]]
    entry_ids = [item["family_id"] for item in value["entries"]]
    if len(entry_ids) != len(set(entry_ids)) or set(entry_ids) != set(manifest_ids):
        raise ValueError("semantic independence entries must match all 16 manifest families exactly once")
    encoded = json.dumps(value, sort_keys=True).lower()
    for forbidden in ("recommended_profile", "profile_label", "rulebasedrecommender", "structured-intent"):
        if forbidden in encoded:
            raise ValueError(f"semantic independence artifact contains forbidden token {forbidden!r}")
    return value


def load_cluster_policy(path: Path = ELIGIBILITY_PATH) -> dict[str, Any]:
    value = _load_yaml(path)
    required = {
        "schema_version", "protocol_version", "expected_context", "expected_namespace",
        "cluster_identity_label", "namespace_safety_label", "node_identity_label",
        "node_isolation_label", "required_node_count", "required_cgroup_version",
        "required_cgroup_controllers", "required_cgroup_files", "minimum_allocatable",
        "maximum_trial", "required_api_access", "single_active_e4_pod",
        "require_no_resource_quotas", "require_pre_pulled_image",
        "require_verified_image_state", "require_dedicated_node",
        "require_no_non_daemonset_workloads_on_node", "eligibility_probe",
    }
    if set(value) != required or value["schema_version"] != "protocol-v5-resource-cluster-eligibility-v1.0.0":
        raise ValueError("cluster eligibility contract shape/version differs from frozen v1")
    if value["required_cgroup_version"] != "v2" or value["required_node_count"] != 1:
        raise ValueError("cluster eligibility contract weakened")
    if set(value["required_cgroup_controllers"]) != {"cpu", "memory", "pids"}:
        raise ValueError("required cgroup controllers differ from frozen contract")
    return value


def load_image_state(path: Path = IMAGE_STATE_PATH) -> dict[str, Any]:
    value = _load_yaml(path)
    if value.get("schema_version") != "protocol-v5-resource-image-state-v1.0.0":
        raise ValueError("unsupported image-state contract")
    for path_key, checksum_key in (
        ("dockerfile_path", "dockerfile_sha256"),
        ("build_context_dockerignore_path", "build_context_dockerignore_sha256"),
    ):
        source = ROOT / str(value.get(path_key, ""))
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != value.get(checksum_key):
            raise ValueError(f"image-state build input mismatch for {path_key}")
    return value


def image_state_is_verified(value: Mapping[str, Any], image: str) -> bool:
    return bool(
        value.get("reference_configured") is True
        and value.get("digest_syntactically_pinned") is True
        and value.get("built") is True
        and value.get("digest_verified") is True
        and value.get("pre_pulled_on_eligible_node") is True
        and value.get("operationally_verified") is True
        and value.get("image_reference") == image
        and isinstance(value.get("resolved_digest"), str)
        and image.endswith("@" + value["resolved_digest"])
    )


def load_freeze_contract(path: Path = FREEZE_CONTRACT_PATH) -> dict[str, Any]:
    value = _load_yaml(path)
    if value.get("schema_version") != "protocol-v5-resource-freeze-contract-v1.0.0":
        raise ValueError("unsupported freeze contract")
    return value


def freeze_is_confirmatory(value: Mapping[str, Any]) -> bool:
    return value.get("current_phase") == "confirmatory" and value.get("confirmatory_freeze_status") == "FROZEN"


def load_crosswalk(path: Path = CROSSWALK_PATH, *, manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    value = _load_yaml(path)
    if value.get("schema_version") != "protocol-v5-resource-allocation-crosswalk-v1.0.0":
        raise ValueError("unsupported resource allocation crosswalk")
    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) != 16:
        raise ValueError("resource allocation crosswalk requires 16 entries")
    manifest = load_resource_manifest(manifest_path)
    expected = {
        item["family_id"]: (item["workload_instance_id"], workload_fingerprint(item))
        for item in manifest["workloads"]
    }
    cases: set[str] = set()
    seen: set[str] = set()
    for entry in entries:
        if set(entry) != {"allocation_case_id", "family_id", "workload_instance_id", "workload_fingerprint"}:
            raise ValueError("crosswalk entry shape differs from frozen contract")
        family = entry["family_id"]
        if family in seen or entry["allocation_case_id"] in cases or family not in expected:
            raise ValueError("crosswalk contains duplicate or unmatched entries")
        if (entry["workload_instance_id"], entry["workload_fingerprint"]) != expected[family]:
            raise ValueError(f"crosswalk fingerprint mismatch for {family}")
        if not SHA256_RE.fullmatch(entry["workload_fingerprint"]):
            raise ValueError("crosswalk fingerprint must be SHA-256")
        seen.add(family)
        cases.add(entry["allocation_case_id"])
    if seen != set(expected):
        raise ValueError("crosswalk does not cover every workload instance")
    return value


def static_independence_scan(directory: Path | None = None) -> dict[str, Any]:
    """Reject recommender imports/calls from calibration runtime modules.

    The data-only comparator is deliberately excluded from the runtime scan.
    """
    directory = directory or Path(__file__).resolve().parent
    violations: list[str] = []
    files = [path for path in directory.glob("*.py") if path.name != "comparison.py"]
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "recommender" or name.startswith("recommender.") for name in names):
                violations.append(f"{path.name}: recommender import")
        forbidden_calls = {
            "RuleBasedRecommender", "StructuredIntentRecommender", "P1", "P2", "P3",
            "recommend", "get_recommendation", "rank_candidates", "rerank_candidates",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else ""
            )
            if called in forbidden_calls:
                violations.append(f"{path.name}: forbidden runtime call {called}")
    if violations:
        raise ValueError("E4 calibration independence violations: " + "; ".join(violations))
    return {"status": "pass", "files_scanned": len(files), "recommender_imports": 0}


__all__ = [
    "COMPARISON_SCHEMA_PATH", "CROSSWALK_PATH", "ELIGIBILITY_PATH", "FREEZE_CONTRACT_PATH", "IMAGE_STATE_PATH",
    "SEMANTIC_PATH", "freeze_is_confirmatory", "image_state_is_verified",
    "load_cluster_policy", "load_crosswalk", "load_freeze_contract", "load_image_state",
    "load_semantic_independence", "static_independence_scan",
]
