"""Append-only raw and immutable analysis packages for E4 efficiency."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from evaluation_v5.provenance import write_json_exclusive

from .efficiency_contracts import CONDITIONS, FAMILY_COUNT, PARETO_OBJECTIVES, PRIMARY_TRIAL_COUNT, REPETITIONS
from .efficiency_models import PLAN_SCHEMA_VERSION, TRIAL_SCHEMA_VERSION, primary_outcome
from .evidence import canonical_sha256, verify_integrity, write_integrity_manifest


RAW_MANIFEST_VERSION = "protocol-v5-resource-efficiency-raw-package-v1.0.0"
ANALYSIS_MANIFEST_VERSION = "protocol-v5-resource-efficiency-analysis-package-v1.0.0"


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected object")
        values.append(value)
    return values


def validate_trial_record(row: Mapping[str, Any]) -> None:
    if row.get("schema_version") != TRIAL_SCHEMA_VERSION:
        raise ValueError("unsupported comparative trial record")
    for key in ("trial_id", "primary_trial_id", "family_id", "condition"):
        if not isinstance(row.get(key), str) or not row[key]:
            raise ValueError(f"comparative trial lacks {key}")
    if row["condition"] not in CONDITIONS:
        raise ValueError("comparative trial has an unregistered condition")
    if isinstance(row.get("repetition"), bool) or not isinstance(row.get("repetition"), int) or not 1 <= row["repetition"] <= REPETITIONS:
        raise ValueError("comparative trial repetition is outside the frozen design")
    if row.get("correctness") not in {True, False, None}:
        raise ValueError("correctness must be tri-state")
    if not isinstance(row.get("workload_output_exists"), bool) or (row.get("correctness") is None) == row["workload_output_exists"]:
        raise ValueError("correctness must be null exactly when workload output is absent")
    if row["workload_output_exists"]:
        if not isinstance(row.get("observed_marker_sha256"), str) or not isinstance(row.get("correctness_invariants_ok"), bool) or not isinstance(row.get("correctness_details"), Mapping):
            raise ValueError("workload output must retain marker and invariant evidence")
        expected_correctness = row["observed_marker_sha256"] == row.get("expected_marker_sha256") and row["correctness_invariants_ok"]
        if row["correctness"] != expected_correctness:
            raise ValueError("correctness result disagrees with marker/invariant evidence")
    elif row.get("observed_marker_sha256") is not None or row.get("correctness_invariants_ok") is not None:
        raise ValueError("missing workload output cannot claim marker/invariant evidence")
    for key in ("pod_created", "scheduled", "pending_or_admission_failure", "oom", "timeout", "runtime_error", "success", "infrastructure_invalid"):
        if not isinstance(row.get(key), bool):
            raise ValueError(f"{key} must be boolean")
    if row["infrastructure_invalid"] != bool(row.get("exclusion_reason")):
        raise ValueError("infrastructure exclusion fields disagree")
    if row["infrastructure_invalid"] and (
        any(row[key] for key in ("oom", "timeout", "pending_or_admission_failure", "runtime_error", "success"))
    ):
        raise ValueError("workload outcomes cannot be infrastructure-invalid")
    if row["pending_or_admission_failure"] and (
        row["oom"] or row["timeout"] or row["runtime_error"] or row["success"]
        or row.get("correctness") is not None
    ):
        raise ValueError("Pending/admission evidence cannot also claim a completed workload outcome")
    if row["success"] and any(row[key] for key in ("pending_or_admission_failure", "oom", "timeout", "runtime_error")):
        raise ValueError("success cannot coexist with a failure outcome")
    if row.get("correctness") is False and row["runtime_error"]:
        raise ValueError("incorrect completion and runtime failure are distinct primary outcomes")
    if row["runtime_error"] and (row["oom"] or row["timeout"]):
        raise ValueError("runtime error cannot duplicate OOM/timeout; retain those independent flags instead")
    if row.get("primary_outcome") != primary_outcome(row):
        raise ValueError("deterministic primary outcome disagrees with independent outcome fields")
    if row["primary_outcome"] == "SUCCESS" and row.get("correctness") is not True:
        raise ValueError("successful task must have verified correctness")
    for key in ("workload_runtime_seconds", "container_runtime_seconds"):
        value = row.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0):
            raise ValueError(f"{key} must be null or finite and non-negative")
    if row["primary_outcome"] == "SUCCESS" and row.get("workload_runtime_seconds") is None:
        raise ValueError("successful task must retain workload runtime")
    if not row.get("pod_created") and row.get("scheduled"):
        raise ValueError("uncreated pod cannot be scheduled")
    planned = row.get("planned_resources")
    if not isinstance(planned, Mapping):
        raise ValueError("planned resources missing")
    from .efficiency_models import ResourceAllocation
    ResourceAllocation.from_dict(planned)
    if row.get("observed_resources") is not None:
        if not isinstance(row["observed_resources"], Mapping):
            raise ValueError("observed resources must be an object or null")
        ResourceAllocation.from_dict(row["observed_resources"])
    if not isinstance(row.get("cgroup_metrics"), Mapping) or not isinstance(row.get("kubernetes"), Mapping):
        raise ValueError("cgroup and Kubernetes evidence must be objects")


def validate_trial_against_spec(row: Mapping[str, Any], spec: Any) -> None:
    for field in ("trial_id", "primary_trial_id", "family_id", "workload_instance_id", "workload_fingerprint", "condition", "repetition", "deterministic_seed", "timeout_seconds", "replacement_of"):
        if row.get(field) != getattr(spec, field):
            raise ValueError(f"trial observation/spec mismatch for {field}")
    if row.get("planned_resources") != spec.allocation.to_dict():
        raise ValueError("trial planned resources differ from the sealed allocation decision")
    observed = row.get("observed_resources")
    if observed is not None and observed != spec.allocation.to_dict():
        raise ValueError("observed pod resources differ from the sealed allocation decision")


def write_sidecar(root: Path, row: Mapping[str, Any]) -> Path:
    target = root / "raw" / "runs" / str(row["trial_id"])
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        raise FileExistsError(f"trial sidecar directory is not empty: {target}")
    return write_json_exclusive(target / "record.json", row)


def validate_raw_package(root: Path, *, allow_unsealed: bool = False) -> dict[str, Any]:
    if (root / "SHA256SUMS").exists():
        integrity = verify_integrity(root)
    elif not allow_unsealed:
        raise ValueError("comparative raw package is not sealed")
    else:
        integrity = None
    manifest_path = root / "manifest.json"
    state_path = root / "run-state.json"
    meta_path = manifest_path if manifest_path.exists() else state_path
    if not meta_path.exists():
        raise ValueError("comparative package lacks state/manifest")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("schema_version") != RAW_MANIFEST_VERSION:
        raise ValueError("unsupported comparative raw package")
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    from .efficiency_plan import validate_efficiency_plan
    validate_efficiency_plan(plan)
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION or plan.get("plan_sha256") != meta.get("plan_sha256"):
        raise ValueError("raw package plan binding mismatch")
    decisions_path = root / "raw" / "decisions.jsonl"
    trial_plan_path = root / "raw" / "trial-plan.jsonl"
    if not decisions_path.is_file() or load_jsonl(decisions_path) != plan["decisions"]:
        raise ValueError("decision sidecar differs from sealed plan")
    if not trial_plan_path.is_file() or load_jsonl(trial_plan_path) != plan["trials"]:
        raise ValueError("trial-plan sidecar differs from sealed plan")
    trials = load_jsonl(root / "raw" / "trials.jsonl")
    from .efficiency_models import EfficiencyTrialSpec
    plan_specs = {item["primary_trial_id"]: EfficiencyTrialSpec.from_dict(item) for item in plan["trials"]}
    seen: set[str] = set()
    replacement_counts: dict[str, int] = {}
    attempts_by_primary: dict[str, list[dict[str, Any]]] = {}
    for row in trials:
        validate_trial_record(row)
        if row["trial_id"] in seen:
            raise ValueError("duplicate trial attempt id")
        seen.add(row["trial_id"])
        sidecar = root / "raw" / "runs" / row["trial_id"] / "record.json"
        if not sidecar.is_file() or json.loads(sidecar.read_text(encoding="utf-8")) != row:
            raise ValueError("trial sidecar mismatch")
        if row.get("sidecar_paths") != {"record": f"raw/runs/{row['trial_id']}/record.json"}:
            raise ValueError("trial sidecar path provenance mismatch")
        base_spec = plan_specs.get(row["primary_trial_id"])
        if base_spec is None:
            raise ValueError("trial does not belong to the sealed plan")
        if row.get("replacement_of"):
            expected_spec = EfficiencyTrialSpec(
                plan_index=base_spec.plan_index, trial_id=base_spec.trial_id + "-infra-replacement-1",
                primary_trial_id=base_spec.primary_trial_id, family_id=base_spec.family_id,
                workload_instance_id=base_spec.workload_instance_id, workload_fingerprint=base_spec.workload_fingerprint,
                condition=base_spec.condition, repetition=base_spec.repetition,
                deterministic_seed=base_spec.deterministic_seed, timeout_seconds=base_spec.timeout_seconds,
                expected_marker_sha256=base_spec.expected_marker_sha256, allocation=base_spec.allocation,
                replacement_of=base_spec.trial_id,
            )
            replacement_counts[row["primary_trial_id"]] = replacement_counts.get(row["primary_trial_id"], 0) + 1
            prior_attempts = attempts_by_primary.get(row["primary_trial_id"], [])
            if len(prior_attempts) != 1 or prior_attempts[0]["trial_id"] != row["replacement_of"] or not prior_attempts[0]["infrastructure_invalid"]:
                raise ValueError("replacement must reference an infrastructure-invalid prior attempt")
        else:
            expected_spec = base_spec
            if attempts_by_primary.get(row["primary_trial_id"]):
                raise ValueError("duplicate primary attempt")
        validate_trial_against_spec(row, expected_spec)
        attempts_by_primary.setdefault(row["primary_trial_id"], []).append(dict(row))
    if any(count > 1 for count in replacement_counts.values()):
        raise ValueError("more than one infrastructure replacement was recorded")
    primary_order = [row["primary_trial_id"] for row in trials if not row.get("replacement_of")]
    planned_order = [row["primary_trial_id"] for row in plan["trials"]]
    if primary_order != planned_order[:len(primary_order)]:
        raise ValueError("trial attempts are not a crash-safe prefix of the sealed plan")
    if meta.get("execution_status") == "NOT_EXECUTED":
        if trials or meta.get("cluster_measurement_status") != "NOT_EXECUTED" or meta.get("kubernetes_mutations") != []:
            raise ValueError("NOT_EXECUTED package contains measurements or mutation claims")
    if manifest_path.exists() and meta.get("execution_status") == "OBSERVED" and len({row["primary_trial_id"] for row in trials}) != PRIMARY_TRIAL_COUNT:
        raise ValueError(f"completed observed package lacks the {PRIMARY_TRIAL_COUNT} primary trial cells")
    if manifest_path.exists() and meta.get("execution_status") == "OBSERVED" and any(len(attempts) == 1 and attempts[0]["infrastructure_invalid"] for attempts in attempts_by_primary.values()):
        raise ValueError("completed observed package omitted an allowed infrastructure replacement")
    if manifest_path.exists() and meta.get("execution_status") == "OBSERVED":
        completion_path = root / "completion-manifest.json"
        environment_path = root / "raw" / "environment.json"
        if not completion_path.is_file() or not environment_path.is_file():
            raise ValueError("completed observed package lacks completion/environment provenance")
        completion = json.loads(completion_path.read_text(encoding="utf-8"))
        if completion.get("expected_primary_trials") != PRIMARY_TRIAL_COUNT or completion.get("completed_primary_trials") != PRIMARY_TRIAL_COUNT or completion.get("attempt_records") != len(trials):
            raise ValueError("completion manifest disagrees with raw trials")
    return {"status": "pass", "sealed": integrity is not None, "execution_status": meta.get("execution_status"), "trials": len(trials), "plan_sha256": meta.get("plan_sha256")}


def seal_package(root: Path) -> Path:
    return write_integrity_manifest(root)


def validate_analysis_package(root: Path) -> dict[str, Any]:
    integrity = verify_integrity(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != ANALYSIS_MANIFEST_VERSION:
        raise ValueError("unsupported comparative analysis package")
    required = (
        "derived/trials.jsonl", "derived/repetition-summaries.jsonl", "derived/family-condition-summaries.json",
        "derived/condition-summaries.json", "derived/dynamic-allocation-summary.json",
        "statistics/results.json", "capacity/simulation.json", "report/pareto.json",
        "report/report.json",
    )
    if any(not (root / path).is_file() for path in required):
        raise ValueError("comparative analysis package is incomplete")
    capacity = json.loads((root / "capacity" / "simulation.json").read_text(encoding="utf-8"))
    if (
        capacity.get("evidence_label") != "SIMULATED_DETERMINISTIC_REQUEST_PACKING"
        or capacity.get("evidence_type") != "SIMULATED_CAPACITY"
        or capacity.get("capacity_source") != "KUBERNETES_NODE_STATUS_ALLOCATABLE"
        or capacity.get("scheduler_input") != "OBSERVED_POD_RESOURCE_REQUESTS"
        or capacity.get("concurrent_cluster_evidence") is not False
    ):
        raise ValueError("capacity simulation/evidence separation is missing")
    statistics = json.loads((root / "statistics" / "results.json").read_text(encoding="utf-8"))
    if statistics.get("family_is_primary_unit") is not True or statistics.get("repetitions_are_independent_families") is not False:
        raise ValueError("statistical package changes the frozen semantic unit")
    if statistics.get("hierarchy") != [
        "raw_trial_attempt", "family_condition_repetition",
        "family_condition", "paired_cross_family_inference",
    ]:
        raise ValueError("statistical package omits the nested repetition hierarchy")
    design = statistics.get("design_counts") or {}
    if design != {
        "number_of_families": FAMILY_COUNT,
        "repetitions_per_family_condition": REPETITIONS,
        "raw_primary_trial_count": PRIMARY_TRIAL_COUNT,
        "independent_semantic_n": FAMILY_COUNT,
    }:
        raise ValueError("statistical package does not expose the frozen nested design")
    effective_sizes = [row.get("effective_family_n") for row in statistics.get("rows") or []]
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > FAMILY_COUNT
        for value in effective_sizes
    ):
        raise ValueError("statistical inference inflated repeated trials into independent units")
    report = json.loads((root / "report" / "report.json").read_text(encoding="utf-8"))
    if report.get("capacity_evidence_type") != "SIMULATED_CAPACITY" or report.get("observed_concurrency_claims_permitted") is not False:
        raise ValueError("report confuses simulated capacity with observed concurrency")
    pareto = json.loads((root / "report" / "pareto.json").read_text(encoding="utf-8"))
    if pareto.get("objectives") != PARETO_OBJECTIVES or pareto.get("success_noninferiority_margin", "missing") is not None:
        raise ValueError("report changes the frozen Pareto objectives or invents a noninferiority margin")
    return {"status": "pass", "sealed": True, "verified_files": integrity["verified_files"], "raw_package_sha256": manifest.get("raw_package_sha256")}


__all__ = ["ANALYSIS_MANIFEST_VERSION", "RAW_MANIFEST_VERSION", "append_jsonl", "load_jsonl", "seal_package", "validate_analysis_package", "validate_raw_package", "validate_trial_against_spec", "validate_trial_record", "write_sidecar"]
