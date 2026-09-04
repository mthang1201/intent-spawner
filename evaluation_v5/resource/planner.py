"""Deterministic bounded-search plan generation for E4."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from .manifest import (
    CPU_LATTICE_M, MEMORY_LATTICE_MIB, validate_resource_manifest,
    workload_fingerprint,
)
from .models import TRIAL_SCHEMA_VERSION, TrialSpec


PLAN_SCHEMA_VERSION = "protocol-v5-resource-calibration-plan-v1.1.0"


def _seed(master_seed: int, family_id: str, phase: str, repeat: int) -> int:
    value = f"e4|{master_seed}|{family_id}|{phase}|{repeat}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(value).digest()[:4], "big")


def make_trial_spec(
    workload: Mapping[str, Any],
    *,
    phase: str,
    cpu_m: int,
    memory_mib: int,
    repeat_index: int,
    plan_index: int,
    replacement_of: str | None = None,
) -> TrialSpec:
    family_id = str(workload["family_id"])
    suffix = "" if replacement_of is None else "-replacement"
    run_id = (
        f"e4-{family_id}-{phase}-c{cpu_m}-m{memory_mib}-"
        f"r{repeat_index:02d}{suffix}"
    )
    return TrialSpec(
        run_id=run_id,
        plan_index=plan_index,
        family_id=family_id,
        workload_instance_id=str(workload["workload_instance_id"]),
        workload_fingerprint=workload_fingerprint(workload),
        phase=phase,
        cpu_m=cpu_m,
        memory_mib=memory_mib,
        repeat_index=repeat_index,
        deterministic_seed=int(workload["deterministic_seed"]),
        expected_marker_sha256=str(workload["expected_marker_sha256"]),
        timeout_seconds=int(workload["timeout_seconds"]),
        replacement_of=replacement_of,
    )


def build_calibration_plan(manifest: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_resource_manifest(manifest)
    families = [item["family_id"] for item in validated["workloads"]]
    instances = [
        {
            "family_id": item["family_id"],
            "workload_instance_id": item["workload_instance_id"],
            "workload_fingerprint": workload_fingerprint(item),
            "parameters": item["parameters"],
            "deterministic_seed": item["deterministic_seed"],
        }
        for item in validated["workloads"]
    ]
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "protocol_version": "5.0.0",
        "experiment_id": "E4",
        "evidence_role": "confirmatory_reference",
        "trial_observation_schema_version": TRIAL_SCHEMA_VERSION,
        "master_seed": validated["master_seed"],
        "family_count": len(families),
        "family_ids": families,
        "workload_instances": instances,
        "candidate_lattices": validated["candidate_lattices"],
        "safe_rule": validated["safe_rule"],
        "algorithm": {
            "version": "deterministic-discrete-bisection-v1",
            "reference": {"cpu_m": 2000, "memory_mib": 2048, "repeats": 3},
            "memory_search": {"fixed_cpu_m": 2000, "repeats_per_probe": 2},
            "cpu_search": {"memory_source": "selected_memory_upper_bound", "repeats_per_probe": 2},
            "joint_verification_repeats": 5,
            "midpoint_rule": "floor((known_unsafe_index + known_safe_index) / 2)",
            "boundary_completion": "test selected accepted point and its immediate lower lattice neighbor when present",
            "infrastructure_replacement_limit": 1,
        },
        "maximum_primary_trials_per_family": 3 + 2 * 5 + 2 * 5 + 5,
        "maximum_primary_trials": len(families) * (3 + 2 * 5 + 2 * 5 + 5),
        "possible_replacement_trials": len(families) * (3 + 2 * 5 + 2 * 5 + 5),
        "memory_lattice_mib": list(MEMORY_LATTICE_MIB),
        "cpu_lattice_m": list(CPU_LATTICE_M),
        "cluster_mutation": False,
        "measurement_status": "NOT_EXECUTED",
    }


__all__ = ["PLAN_SCHEMA_VERSION", "build_calibration_plan", "make_trial_spec"]
