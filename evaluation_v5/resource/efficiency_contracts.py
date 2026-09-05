"""Strict, fail-closed contracts for the Protocol-v5 E4 comparison."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
import yaml

from .evidence import file_sha256
from .manifest import DEFAULT_MANIFEST, load_resource_manifest, workload_fingerprint


ROOT = Path(__file__).resolve().parents[2]
INPUT_PATH = ROOT / "benchmarks_v5" / "resource-efficiency-inputs-v1.yaml"
INPUT_SCHEMA_PATH = ROOT / "benchmarks_v5" / "protocol-v5-resource-efficiency-inputs-v1.schema.json"
FREEZE_PATH = ROOT / "benchmarks_v5" / "resource-efficiency-freeze-contract-v1.yaml"
FREEZE_SCHEMA_PATH = ROOT / "benchmarks_v5" / "protocol-v5-resource-efficiency-freeze-v1.schema.json"
CAPACITY_PATH = ROOT / "benchmarks_v5" / "resource-efficiency-capacity-v1.yaml"
CAPACITY_SCHEMA_PATH = ROOT / "benchmarks_v5" / "protocol-v5-resource-efficiency-capacity-v1.schema.json"

CONDITIONS = ("STATIC_LARGE", "P1_CATALOG", "P2_CATALOG", "P2_DYNAMIC")
FAMILY_COUNT = 16
REPETITIONS = 10
PRIMARY_TRIAL_COUNT = FAMILY_COUNT * len(CONDITIONS) * REPETITIONS
EXECUTION_ORDER_ALGORITHM = "seeded-family-shuffle-with-balanced-latin-condition-rotation-v1"
CATALOG_PROFILES = {
    "small": {"cpu_request_m": 100, "cpu_limit_m": 500, "memory_request_mib": 256, "memory_limit_mib": 384, "gpu_count": 0},
    "medium": {"cpu_request_m": 500, "cpu_limit_m": 1000, "memory_request_mib": 768, "memory_limit_mib": 1024, "gpu_count": 0},
    "large": {"cpu_request_m": 1500, "cpu_limit_m": 2000, "memory_request_mib": 1536, "memory_limit_mib": 2048, "gpu_count": 0},
}
CONTRASTS = (
    ("P2_CATALOG", "STATIC_LARGE"),
    ("P2_DYNAMIC", "STATIC_LARGE"),
    ("P2_DYNAMIC", "P2_CATALOG"),
    ("P2_CATALOG", "P1_CATALOG"),
)
PARETO_OBJECTIVES = {
    "minimize": [
        "cpu_cost_per_success", "memory_cost_per_success", "oom_rate",
        "timeout_rate", "pending_or_admission_rate", "runtime_error_rate",
        "incorrect_rate",
    ],
    "maximize": ["success_rate", "correct_completion_rate"],
    "undefined_cost": "INDETERMINATE",
    "lower_cost_with_worse_reliability": "EFFICIENCY_RELIABILITY_TRADEOFF",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected an object")
    return value


def _schema_validate(value: Mapping[str, Any], schema_path: Path) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(value)


def mechanical_prompt(workload: Mapping[str, Any]) -> str:
    parameters = json.dumps(workload["parameters"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{str(workload['description']).rstrip()} Parameters: {parameters}."


def load_condition_inputs(path: Path = INPUT_PATH, *, workload_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    value = _load_yaml(path)
    _schema_validate(value, INPUT_SCHEMA_PATH)
    workload_manifest = load_resource_manifest(workload_path)
    workloads = {row["family_id"]: row for row in workload_manifest["workloads"]}
    if len(workloads) != FAMILY_COUNT:
        raise ValueError(f"comparative design requires exactly {FAMILY_COUNT} frozen workload families")
    seen: set[str] = set()
    for row in value["inputs"]:
        family = row["family_id"]
        if family in seen or family not in workloads:
            raise ValueError(f"invalid or duplicate comparative family {family!r}")
        seen.add(family)
        source = workloads[family]
        expected = {
            "case_id": "e4-resource-" + family.replace("_", "-"),
            "workload_instance_id": source["workload_instance_id"],
            "workload_fingerprint": workload_fingerprint(source),
            "prompt": mechanical_prompt(source),
            "dataset_size_gb": 0.0,
            "code_context_hints": [],
        }
        for key, expected_value in expected.items():
            if row[key] != expected_value:
                raise ValueError(f"{family}: comparative input {key} is not mechanically bound to the frozen workload")
    if seen != set(workloads):
        raise ValueError("comparative inputs must bind every frozen resource workload exactly once")
    return value


def load_efficiency_freeze(path: Path = FREEZE_PATH) -> dict[str, Any]:
    value = _load_yaml(path)
    _schema_validate(value, FREEZE_SCHEMA_PATH)
    experiment = value["experiment"]
    if list(value["conditions"]) != list(CONDITIONS):
        raise ValueError("comparative condition set or order differs from the registered contract")
    if experiment != {
        "experiment_id": "E4_RESOURCE_EFFICIENCY",
        "workload_input_path": "benchmarks_v5/resource-efficiency-inputs-v1.yaml",
        "workload_input_sha256": file_sha256(INPUT_PATH),
        "repetitions": REPETITIONS,
        "plan_seed": 20260904,
        "primary_trial_count": PRIMARY_TRIAL_COUNT,
        "single_active_pod": True,
        "infrastructure_replacements_per_primary_trial": 1,
        "execution_order_algorithm": EXECUTION_ORDER_ALGORITHM,
    }:
        raise ValueError("comparative experiment design differs from the registered contract")
    if value["catalog_profiles"] != CATALOG_PROFILES:
        raise ValueError("catalog resource table differs from the registered contract")
    decision = value["decision_policy"]
    if set(decision) != {"static_large_profile", "p1_adapter", "p2_adapter", "dynamic_policy_path", "dynamic_policy_sha256", "oracle_data_permitted", "calls_per_family", "reuse_p2_for_conditions", "policy_clipping_semantics", "p3"}:
        raise ValueError("decision policy shape differs from the registered contract")
    dynamic_path = ROOT / str(decision.get("dynamic_policy_path", ""))
    if not dynamic_path.is_file() or decision.get("dynamic_policy_sha256") != file_sha256(dynamic_path):
        raise ValueError("dynamic policy content differs from the freeze binding")
    if decision.get("static_large_profile") != "large" or decision.get("p1_adapter") != "protocol-v5-p1-frozen-adapter-v1" or decision.get("p2_adapter") != "protocol-v5-p2-frozen-adapter-v1":
        raise ValueError("frozen comparator adapter binding differs")
    if decision.get("oracle_data_permitted") is not False or decision.get("calls_per_family") != {"P1": 1, "P2": 1} or decision.get("reuse_p2_for_conditions") != ["P2_CATALOG", "P2_DYNAMIC"] or decision.get("policy_clipping_semantics") != "reject_and_catalog_fallback":
        raise ValueError("decision generation must be oracle-free and once per family")
    p3 = decision.get("p3") or {}
    gate_path = ROOT / str(p3.get("gate_path", ""))
    if p3 != {"included": False, "authoritative_gate": "not_retained", "gate_path": "results_v5/protocol-v5.0.0/freezes/frozen-configuration.json", "gate_sha256": file_sha256(gate_path) if gate_path.is_file() else None}:
        raise ValueError("P3 is excluded because the authoritative gate is not_retained")
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    if (gate.get("p3_gate") or {}).get("status") != "not_retained" or (gate.get("p3_gate") or {}).get("p3_active") is not False:
        raise ValueError("authoritative P3 gate no longer supports exclusion")
    contrasts = tuple(tuple(item) for item in value["statistics"].get("contrasts", []))
    statistics = value["statistics"]
    if set(statistics) != {"family_is_primary_unit", "repetitions_are_independent_families", "primary_endpoints", "secondary_endpoints", "pareto_objectives", "success_noninferiority_margin", "contrasts", "multiplicity", "bootstrap_replicates"}:
        raise ValueError("statistical contract shape differs")
    if contrasts != CONTRASTS or statistics.get("family_is_primary_unit") is not True or statistics.get("repetitions_are_independent_families") is not False or statistics.get("multiplicity") != "holm_within_endpoint" or statistics.get("bootstrap_replicates") != 2000:
        raise ValueError("registered family-first statistical design differs")
    if statistics.get("primary_endpoints") != ["family_success_rate", "family_oom_rate", "cpu_cost_per_success", "memory_cost_per_success"]:
        raise ValueError("primary endpoints differ from the registered contract")
    if statistics.get("secondary_endpoints") != ["timeout_rate", "pending_or_admission_rate", "runtime_error_rate", "correctness_rate", "correct_completion_rate", "incorrect_rate", "runtime_seconds", "cpu_request_ratio", "memory_request_ratio", "oracle_error"]:
        raise ValueError("secondary endpoints differ from the registered contract")
    if statistics.get("pareto_objectives") != PARETO_OBJECTIVES:
        raise ValueError("Pareto objectives differ from the registered contract")
    if statistics.get("success_noninferiority_margin") is not None:
        raise ValueError("no post-hoc success noninferiority margin is permitted")
    oracle = value["oracle_package"]
    if set(oracle) != {"path", "sha256", "manual_approval_status", "required_envelope_status"} or oracle.get("required_envelope_status") != "APPROVED":
        raise ValueError("oracle binding shape differs")
    image = value["image"]
    if set(image) != {"reference", "digest_verified"}:
        raise ValueError("image binding shape differs")
    capacity = value["capacity"]
    if capacity != {"contract_path": "benchmarks_v5/resource-efficiency-capacity-v1.yaml", "contract_sha256": file_sha256(CAPACITY_PATH), "evidence_label": "SIMULATED_DETERMINISTIC_REQUEST_PACKING"}:
        raise ValueError("capacity contract binding differs")
    if value["confirmatory_freeze_status"] == "NOT_FROZEN":
        if oracle != {"path": None, "sha256": None, "manual_approval_status": "NOT_APPROVED", "required_envelope_status": "APPROVED"} or image != {"reference": None, "digest_verified": False}:
            raise ValueError("development freeze must not claim oracle or image approval")
    elif value["current_phase"] != "confirmatory":
        raise ValueError("a FROZEN comparative contract must be confirmatory")
    return value


def load_capacity_contract(path: Path = CAPACITY_PATH, *, require_frozen: bool = False) -> dict[str, Any]:
    value = _load_yaml(path)
    _schema_validate(value, CAPACITY_SCHEMA_PATH)
    allocatable = value["allocatable"]
    if set(value["eligible_node"]) != {"name", "uid", "verified_at", "source_preflight_sha256"} or set(allocatable) != {"cpu_m", "memory_mib", "gpu_count", "gpu_resource"}:
        raise ValueError("capacity identity/allocatable shape differs")
    if value.get("evidence_type") != "SIMULATED_CAPACITY" or value.get("capacity_source") != "KUBERNETES_NODE_STATUS_ALLOCATABLE" or value.get("physical_capacity_permitted") is not False:
        raise ValueError("capacity contract must use Kubernetes node allocatable and remain simulation-only")
    if value["packing"] != {
        "requests_only": True,
        "homogeneous_formula": "minimum_integer_floor_across_requested_resources",
        "balanced_mix": "one_session_per_each_of_16_families",
        "algorithm": "multidimensional-first-fit-decreasing-v1",
        "sort_keys": ["maximum_normalized_pressure_descending", "total_normalized_pressure_descending", "family_id_ascending"],
        "concurrency_claim_permitted": False,
    }:
        raise ValueError("capacity packing algorithm differs from the registered contract")
    frozen = value["freeze_status"] == "FROZEN"
    quantities = (allocatable.get("cpu_m"), allocatable.get("memory_mib"), allocatable.get("gpu_count"))
    if frozen:
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in quantities) or quantities[0] <= 0 or quantities[1] <= 0:
            raise ValueError("frozen capacity requires verified positive CPU/memory and non-negative GPU")
        if (quantities[2] > 0) != isinstance(allocatable.get("gpu_resource"), str):
            raise ValueError("frozen GPU capacity requires an exact extended-resource identity")
        node = value["eligible_node"]
        if not all(node.get(key) for key in ("name", "uid", "verified_at", "source_preflight_sha256")):
            raise ValueError("frozen capacity lacks eligible-node provenance")
    elif any(item is not None for item in (*quantities, allocatable.get("gpu_resource"))):
        raise ValueError("NOT_FROZEN capacity must not contain invented allocatable quantities")
    if require_frozen and not frozen:
        raise ValueError("node capacity contract is NOT_FROZEN")
    return value


def validate_efficiency_contracts() -> dict[str, Any]:
    inputs = load_condition_inputs()
    freeze = load_efficiency_freeze()
    capacity = load_capacity_contract()
    return {
        "status": "pass",
        "protocol_version": "5.0.0",
        "family_count": len(inputs["inputs"]),
        "condition_count": len(CONDITIONS),
        "repetitions": freeze["experiment"]["repetitions"],
        "primary_trial_count": len(inputs["inputs"]) * len(CONDITIONS) * freeze["experiment"]["repetitions"],
        "confirmatory_freeze_status": freeze["confirmatory_freeze_status"],
        "capacity_freeze_status": capacity["freeze_status"],
        "sha256": {"inputs": file_sha256(INPUT_PATH), "freeze": file_sha256(FREEZE_PATH), "capacity": file_sha256(CAPACITY_PATH)},
    }


def confirmatory_readiness(freeze: Mapping[str, Any], capacity: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    if freeze.get("current_phase") != "confirmatory" or freeze.get("confirmatory_freeze_status") != "FROZEN":
        failures.append("CONFIRMATORY_FREEZE_INACTIVE")
    oracle = freeze.get("oracle_package") or {}
    if oracle.get("manual_approval_status") != "APPROVED" or not oracle.get("path") or not oracle.get("sha256"):
        failures.append("APPROVED_ORACLE_UNAVAILABLE")
    image = freeze.get("image") or {}
    if not image.get("reference") or image.get("digest_verified") is not True:
        failures.append("IMAGE_DIGEST_UNVERIFIED")
    if capacity.get("freeze_status") != "FROZEN":
        failures.append("NODE_CAPACITY_NOT_FROZEN")
    return failures


__all__ = [
    "CAPACITY_PATH", "CATALOG_PROFILES", "CONDITIONS", "CONTRASTS",
    "EXECUTION_ORDER_ALGORITHM", "FAMILY_COUNT", "FREEZE_PATH", "INPUT_PATH",
    "PARETO_OBJECTIVES", "PRIMARY_TRIAL_COUNT", "REPETITIONS",
    "confirmatory_readiness", "load_capacity_contract", "load_condition_inputs",
    "load_efficiency_freeze", "mechanical_prompt", "validate_efficiency_contracts",
]
