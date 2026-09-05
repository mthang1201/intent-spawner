"""CLI and crash-safe orchestration for Protocol-v5 E4 resource efficiency."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping, Protocol

from evaluation_v5.provenance import write_json_exclusive

from .contracts import load_cluster_policy, load_image_state
from .efficiency_analysis import analyze_trials, load_approved_oracle, summarize_dynamic_allocations
from .efficiency_capacity import simulate_capacity
from .efficiency_contracts import (
    FAMILY_COUNT, PRIMARY_TRIAL_COUNT, REPETITIONS, confirmatory_readiness,
    load_capacity_contract, load_efficiency_freeze, validate_efficiency_contracts,
)
from .efficiency_evidence import ANALYSIS_MANIFEST_VERSION, RAW_MANIFEST_VERSION, append_jsonl, load_jsonl, seal_package, validate_analysis_package, validate_raw_package, validate_trial_against_spec, validate_trial_record, write_sidecar
from .efficiency_models import EfficiencyTrialSpec
from .efficiency_plan import build_efficiency_plan, git_is_clean, load_plan_package, validate_efficiency_plan, write_plan_package
from .evidence import canonical_sha256, file_sha256


class EfficiencyAdapter(Protocol):
    adapter_version: str
    def environment_provenance(self) -> Mapping[str, Any]: ...
    def run_trial(self, spec: EfficiencyTrialSpec) -> Mapping[str, Any]: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _stable_provenance(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _stable_provenance(item) for key, item in value.items() if key not in {"captured_at", "captured_at_utc", "started_at", "finished_at", "verified_at"}}
    if isinstance(value, list):
        return [_stable_provenance(item) for item in value]
    return value


def _git_revision() -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _capacity_preflight_failures(preflight: Mapping[str, Any], capacity: Mapping[str, Any]) -> list[str]:
    from cluster_evaluation.resource_adapter_v5 import _cpu_m, _memory_mib

    failures = [code for code in preflight.get("failure_codes", []) if code != "CGROUP_V2_REQUIRED"]
    facts = preflight.get("facts") or {}
    eligible = capacity.get("eligible_node") or {}
    allocatable = facts.get("node_allocatable") or {}
    if facts.get("node_name") != eligible.get("name") or facts.get("node_uid") != eligible.get("uid"):
        failures.append("FROZEN_NODE_IDENTITY_MISMATCH")
    expected = capacity.get("allocatable") or {}
    gpu_resource = expected.get("gpu_resource")
    raw_gpu = 0 if not gpu_resource else allocatable.get(gpu_resource)
    if isinstance(raw_gpu, str) and raw_gpu.isdigit():
        raw_gpu = int(raw_gpu)
    observed = {"cpu_m": _cpu_m(allocatable.get("cpu")), "memory_mib": _memory_mib(allocatable.get("memory")), "gpu_count": raw_gpu, "gpu_resource": gpu_resource}
    if observed != expected:
        failures.append("FROZEN_NODE_ALLOCATABLE_MISMATCH")
    return failures


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    write_json_exclusive(path, value)


def _record_attempt(root: Path, row: Mapping[str, Any], spec: EfficiencyTrialSpec) -> dict[str, Any]:
    payload = {**row, "sidecar_paths": {"record": f"raw/runs/{row['trial_id']}/record.json"}}
    validate_trial_record(payload)
    validate_trial_against_spec(payload, spec)
    write_sidecar(root, payload)
    append_jsonl(root / "raw" / "trials.jsonl", payload)
    return payload


def _write_plan_sidecars(root: Path, plan: Mapping[str, Any]) -> None:
    decision_path = root / "raw" / "decisions.jsonl"
    trial_path = root / "raw" / "trial-plan.jsonl"
    existing_decisions = load_jsonl(decision_path)
    existing_trials = load_jsonl(trial_path)
    if existing_decisions != plan["decisions"][:len(existing_decisions)] or existing_trials != plan["trials"][:len(existing_trials)]:
        raise ValueError("partial plan sidecars are not a prefix of the sealed plan")
    for decision in plan["decisions"][len(existing_decisions):]:
        append_jsonl(decision_path, decision)
    for trial in plan["trials"][len(existing_trials):]:
        append_jsonl(trial_path, trial)


def _replacement_spec(spec: EfficiencyTrialSpec, prior_id: str) -> EfficiencyTrialSpec:
    return EfficiencyTrialSpec(
        plan_index=spec.plan_index, trial_id=spec.trial_id + "-infra-replacement-1", primary_trial_id=spec.primary_trial_id,
        family_id=spec.family_id, workload_instance_id=spec.workload_instance_id, workload_fingerprint=spec.workload_fingerprint,
        condition=spec.condition, repetition=spec.repetition, deterministic_seed=spec.deterministic_seed,
        timeout_seconds=spec.timeout_seconds, expected_marker_sha256=spec.expected_marker_sha256,
        allocation=spec.allocation, replacement_of=prior_id,
    )


def execute_plan(
    *, root: Path, run_id: str, plan: Mapping[str, Any], adapter: EfficiencyAdapter,
    resume: bool = False, enforce_readiness: bool = True,
) -> dict[str, Any]:
    validate_efficiency_plan(plan)
    if not enforce_readiness and adapter.adapter_version == "protocol-v5-resource-efficiency-kubernetes-adapter-v1.0.0":
        raise ValueError("readiness gates cannot be disabled for the Kubernetes adapter")
    freeze = load_efficiency_freeze()
    capacity = load_capacity_contract()
    blockers = confirmatory_readiness(freeze, capacity)
    if enforce_readiness and not git_is_clean():
        blockers.append("GIT_TREE_NOT_CLEAN")
    if enforce_readiness:
        if plan.get("condition_input_sha256") != freeze["experiment"]["workload_input_sha256"] or plan.get("freeze_contract_sha256") != file_sha256(Path(__file__).resolve().parents[2] / "benchmarks_v5" / "resource-efficiency-freeze-contract-v1.yaml"):
            blockers.append("PLAN_CONTRACT_BINDING_MISMATCH")
        if plan.get("git_revision") != _git_revision():
            blockers.append("PLAN_GIT_REVISION_MISMATCH")
        if getattr(adapter, "image", None) != freeze["image"].get("reference"):
            blockers.append("FROZEN_IMAGE_MISMATCH")
        if not blockers:
            oracle_path = Path(__file__).resolve().parents[2] / str(freeze["oracle_package"]["path"])
            load_approved_oracle(oracle_path, expected_sha256=freeze["oracle_package"]["sha256"])
            read_only = getattr(adapter, "read_only_preflight", None)
            if not callable(read_only):
                blockers.append("READ_ONLY_PREFLIGHT_UNAVAILABLE")
            else:
                blockers.extend(_capacity_preflight_failures(read_only(), capacity))
    if enforce_readiness and blockers:
        raise RuntimeError("RESOURCE_EFFICIENCY_EXECUTION_BLOCKED: " + ",".join(sorted(set(blockers))))
    environment = dict(adapter.environment_provenance())
    provenance_hash = canonical_sha256({"adapter_version": adapter.adapter_version, "environment": _stable_provenance(environment)})
    state = {
        "schema_version": RAW_MANIFEST_VERSION, "protocol_version": "5.0.0", "run_id": run_id,
        "execution_status": "IN_PROGRESS", "cluster_measurement_status": "IN_PROGRESS",
        "plan_sha256": plan["plan_sha256"], "provenance_sha256": provenance_hash,
        "adapter_version": adapter.adapter_version, "started_at": _utc_now(),
    }
    if resume:
        if (root / "SHA256SUMS").exists():
            raise ValueError("sealed or completed comparative packages cannot be resumed")
        if (root / "manifest.json").exists():
            prior_manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            status = validate_raw_package(root, allow_unsealed=True)
            if status["execution_status"] != "OBSERVED" or status["plan_sha256"] != plan["plan_sha256"]:
                raise ValueError("unsealed completion manifest does not match the requested resume")
            if (root / "run-state.json").exists():
                os.unlink(root / "run-state.json")
            seal_package(root)
            validate_raw_package(root)
            return prior_manifest
        prior = json.loads((root / "run-state.json").read_text(encoding="utf-8"))
        if prior.get("plan_sha256") != plan["plan_sha256"] or prior.get("provenance_sha256") != provenance_hash:
            raise ValueError("resume plan/provenance hash differs")
    else:
        root.mkdir(parents=True, exist_ok=False)
        (root / "raw" / "runs").mkdir(parents=True)
        _write_json(root / "run-state.json", state)
    if not (root / "plan.json").exists():
        _write_json(root / "plan.json", plan)
    if not (root / "raw" / "environment.json").exists():
        _write_json(root / "raw" / "environment.json", environment)
    _write_plan_sidecars(root, plan)
    # A crash can occur after an exclusive sidecar is fsynced but before its
    # JSONL line is appended. Recover that sole safe prefix record verbatim.
    jsonl_path = root / "raw" / "trials.jsonl"
    recorded_ids = {row["trial_id"] for row in load_jsonl(jsonl_path)}
    orphan_records = []
    for sidecar in sorted((root / "raw" / "runs").glob("*/record.json")):
        value = json.loads(sidecar.read_text(encoding="utf-8"))
        if value.get("trial_id") not in recorded_ids:
            orphan_records.append(value)
    if len(orphan_records) > 1:
        raise ValueError("unsealed package has more than one non-prefix sidecar")
    if orphan_records:
        validate_trial_record(orphan_records[0])
        append_jsonl(jsonl_path, orphan_records[0])
    existing = load_jsonl(root / "raw" / "trials.jsonl")
    completed: set[str] = set()
    by_primary: dict[str, list[dict[str, Any]]] = {}
    for row in existing:
        by_primary.setdefault(row["primary_trial_id"], []).append(row)
    for primary, attempts in by_primary.items():
        if not attempts[-1]["infrastructure_invalid"] or len(attempts) >= 2:
            completed.add(primary)
    for trial_payload in plan["trials"]:
        spec = EfficiencyTrialSpec.from_dict(trial_payload)
        if spec.primary_trial_id in completed:
            continue
        attempts = by_primary.get(spec.primary_trial_id, [])
        current = spec if not attempts else _replacement_spec(spec, attempts[-1]["trial_id"])
        row = dict(adapter.run_trial(current))
        row = _record_attempt(root, row, current)
        attempts.append(row)
        by_primary[spec.primary_trial_id] = attempts
        if (row.get("kubernetes") or {}).get("cleanup_status") == "failed":
            raise RuntimeError("POD_CLEANUP_FAILED_ABORTING_EXPERIMENT")
        if row["infrastructure_invalid"] and len(attempts) == 1:
            replacement = _replacement_spec(spec, row["trial_id"])
            replacement_row = dict(adapter.run_trial(replacement))
            replacement_row = _record_attempt(root, replacement_row, replacement)
            attempts.append(replacement_row)
            if (replacement_row.get("kubernetes") or {}).get("cleanup_status") == "failed":
                raise RuntimeError("POD_CLEANUP_FAILED_ABORTING_EXPERIMENT")
        completed.add(spec.primary_trial_id)
    final_rows = load_jsonl(root / "raw" / "trials.jsonl")
    completion_path = root / "completion-manifest.json"
    if completion_path.exists():
        completion = json.loads(completion_path.read_text(encoding="utf-8"))
        if completion.get("completed_primary_trials") != len({row["primary_trial_id"] for row in final_rows}) or completion.get("attempt_records") != len(final_rows):
            raise ValueError("existing completion manifest differs from recovered prefix")
    else:
        completion = {"schema_version": "protocol-v5-resource-efficiency-completion-v1.0.0", "expected_primary_trials": PRIMARY_TRIAL_COUNT, "completed_primary_trials": len({row["primary_trial_id"] for row in final_rows}), "attempt_records": len(final_rows), "completed_at": _utc_now()}
        _write_json(completion_path, completion)
    manifest = {**state, "execution_status": "OBSERVED", "cluster_measurement_status": "OBSERVED", "completed_at": completion["completed_at"], "primary_trial_count": PRIMARY_TRIAL_COUNT, "attempt_record_count": len(final_rows)}
    _write_json(root / "manifest.json", manifest)
    os.unlink(root / "run-state.json")
    seal_package(root)
    validate_raw_package(root)
    return manifest


def write_not_executed(*, root: Path, run_id: str, image: str, reason: str) -> dict[str, Any]:
    from cluster_evaluation.resource_adapter_v5 import collect_read_only_preflight

    plan = build_efficiency_plan()
    freeze = load_efficiency_freeze()
    capacity = load_capacity_contract()
    blockers = confirmatory_readiness(freeze, capacity)
    if not git_is_clean():
        blockers.append("GIT_TREE_NOT_CLEAN")
    preflight = collect_read_only_preflight(image=image, policy=load_cluster_policy(), image_state=load_image_state())
    blockers.extend(preflight.get("failure_codes") or [])
    root.mkdir(parents=True, exist_ok=False)
    (root / "raw").mkdir()
    (root / "report").mkdir()
    _write_json(root / "plan.json", plan)
    _write_plan_sidecars(root, plan)
    environment = {"captured_at": _utc_now(), "read_only_preflight": preflight, "hardware_measurements": None, "cgroup_measurements": None, "kubernetes_mutations": []}
    _write_json(root / "raw" / "environment.json", environment)
    manifest = {
        "schema_version": RAW_MANIFEST_VERSION, "protocol_version": "5.0.0", "run_id": run_id,
        "execution_status": "NOT_EXECUTED", "cluster_measurement_status": "NOT_EXECUTED",
        "plan_sha256": plan["plan_sha256"], "created_at": _utc_now(), "image": image,
        "reason": reason, "blocker_codes": sorted(set(blockers)), "kubernetes_mutations": [],
    }
    _write_json(root / "manifest.json", manifest)
    _write_json(root / "report" / "status.json", {"status": "NOT_EXECUTED", "executed_trials": 0, "empirical_claims_permitted": False, "reason": reason, "blocker_codes": sorted(set(blockers))})
    seal_package(root)
    validate_raw_package(root)
    return manifest


def write_analysis_package(*, raw_root: Path, analysis_root: Path, oracle_root: Path, bootstrap_replicates: int = 2000) -> dict[str, Any]:
    raw_status = validate_raw_package(raw_root)
    if raw_status["execution_status"] != "OBSERVED":
        raise ValueError("analysis requires an observed raw package")
    freeze = load_efficiency_freeze()
    oracle = load_approved_oracle(oracle_root, expected_sha256=freeze["oracle_package"]["sha256"])
    rows = load_jsonl(raw_root / "raw" / "trials.jsonl")
    plan = json.loads((raw_root / "plan.json").read_text(encoding="utf-8"))
    decisions = plan["decisions"]
    result = analyze_trials(
        rows, oracle=oracle, decisions=decisions,
        bootstrap_replicates=bootstrap_replicates,
    )
    capacity = simulate_capacity(result["derived_trials"], load_capacity_contract(require_frozen=True))
    dynamic = summarize_dynamic_allocations(decisions)
    analysis_root.mkdir(parents=True, exist_ok=False)
    for directory in ("derived", "statistics", "capacity", "report"):
        (analysis_root / directory).mkdir()
    for row in result["derived_trials"]:
        append_jsonl(analysis_root / "derived" / "trials.jsonl", row)
    for row in result["repetition_summaries"]:
        append_jsonl(analysis_root / "derived" / "repetition-summaries.jsonl", row)
    _write_json(analysis_root / "derived" / "family-condition-summaries.json", {"schema_version": "protocol-v5-resource-efficiency-family-summary-v1.0.0", "rows": result["family_condition_summaries"]})
    _write_json(analysis_root / "derived" / "condition-summaries.json", {"schema_version": "protocol-v5-resource-efficiency-condition-summary-v1.0.0", "rows": result["condition_summaries"]})
    _write_json(analysis_root / "derived" / "dynamic-allocation-summary.json", dynamic)
    _write_json(analysis_root / "statistics" / "results.json", {
        "schema_version": "protocol-v5-resource-efficiency-statistics-v1.0.0",
        "family_is_primary_unit": True,
        "repetitions_are_independent_families": False,
        "hierarchy": result["analysis_hierarchy"],
        "design_counts": {
            "number_of_families": FAMILY_COUNT,
            "repetitions_per_family_condition": REPETITIONS,
            "raw_primary_trial_count": PRIMARY_TRIAL_COUNT,
            "independent_semantic_n": FAMILY_COUNT,
        },
        "rows": result["statistics"],
    })
    _write_json(analysis_root / "capacity" / "simulation.json", capacity)
    _write_json(analysis_root / "report" / "pareto.json", {
        "schema_version": "protocol-v5-resource-efficiency-pareto-v1.0.0",
        "objectives": result["pareto_objectives"],
        "success_noninferiority_margin": None,
        "rows": result["pareto"],
        "safeguard": "Lower cost with worse success, correct completion, OOM, timeout, Pending/admission, runtime error, or incorrect completion is a trade-off, never an improvement.",
    })
    _write_json(analysis_root / "report" / "report.json", {
        "experiment": "E4_RESOURCE_EFFICIENCY",
        "empirical_scope": "single-pod sequential Kubernetes evidence",
        "primary_semantic_unit": "workload family",
        "repetitions_role": "within-family run variability",
        "capacity_scope": "deterministic request-packing simulation only",
        "capacity_evidence_type": "SIMULATED_CAPACITY",
        "observed_concurrency_claims_permitted": False,
        "design_counts": {
            "number_of_families": FAMILY_COUNT,
            "repetitions_per_family_condition": REPETITIONS,
            "raw_primary_trial_count": PRIMARY_TRIAL_COUNT,
            "independent_semantic_n": FAMILY_COUNT,
        },
        "cost_semantics": {
            "cpu": "sum CPU request-time across all valid attempts divided by successful tasks",
            "memory": "sum memory request-time across all valid attempts divided by successful tasks",
            "zero_success": "undefined (null with ZERO_SUCCESS), never zero",
            "missing_scheduled_duration": "undefined, never imputed as zero",
        },
        "pareto": result["pareto"],
        "limitations": [
            "No claim about real concurrent-cluster throughput follows from request packing.",
            "Allocation-oracle comparisons apply only to the exact frozen workload instances.",
        ],
    })
    manifest = {"schema_version": ANALYSIS_MANIFEST_VERSION, "protocol_version": "5.0.0", "created_at": _utc_now(), "raw_package_sha256": file_sha256(raw_root / "SHA256SUMS"), "raw_plan_sha256": raw_status["plan_sha256"], "oracle_package_sha256": file_sha256(oracle_root / "SHA256SUMS"), "bootstrap_replicates": bootstrap_replicates, "capacity_evidence_label": "SIMULATED_DETERMINISTIC_REQUEST_PACKING", "capacity_evidence_type": "SIMULATED_CAPACITY"}
    _write_json(analysis_root / "manifest.json", manifest)
    seal_package(analysis_root)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    plan = commands.add_parser("plan"); plan.add_argument("--result-dir", type=Path, required=True)
    dry = commands.add_parser("dry-run")
    dry.add_argument("--result-dir", type=Path, required=True); dry.add_argument("--run-id", required=True); dry.add_argument("--image", required=True); dry.add_argument("--reason", required=True)
    check = commands.add_parser("validate-package"); check.add_argument("path", type=Path)
    execute = commands.add_parser("execute")
    execute.add_argument("--result-dir", type=Path, required=True); execute.add_argument("--run-id", required=True); execute.add_argument("--plan-dir", type=Path, required=True); execute.add_argument("--image", required=True); execute.add_argument("--resume", action="store_true")
    analyze = commands.add_parser("analyze")
    analyze.add_argument("--raw-result", type=Path, required=True); analyze.add_argument("--analysis-dir", type=Path, required=True); analyze.add_argument("--oracle", type=Path, required=True); analyze.add_argument("--bootstrap-replicates", type=int, default=2000)
    check_analysis = commands.add_parser("validate-analysis"); check_analysis.add_argument("path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "validate":
        print(json.dumps(validate_efficiency_contracts(), sort_keys=True)); return 0
    if args.command == "plan":
        write_plan_package(args.result_dir, build_efficiency_plan()); print(json.dumps({"status": "sealed", "path": str(args.result_dir)}, sort_keys=True)); return 0
    if args.command == "dry-run":
        print(json.dumps(write_not_executed(root=args.result_dir, run_id=args.run_id, image=args.image, reason=args.reason), sort_keys=True)); return 0
    if args.command == "execute":
        from cluster_evaluation.resource_efficiency_adapter_v5 import KubernetesResourceEfficiencyAdapter
        value = execute_plan(root=args.result_dir, run_id=args.run_id, plan=load_plan_package(args.plan_dir), adapter=KubernetesResourceEfficiencyAdapter(image=args.image), resume=args.resume)
        print(json.dumps(value, sort_keys=True)); return 0
    if args.command == "analyze":
        value = write_analysis_package(raw_root=args.raw_result, analysis_root=args.analysis_dir, oracle_root=args.oracle, bootstrap_replicates=args.bootstrap_replicates)
        print(json.dumps(value, sort_keys=True)); return 0
    if args.command == "validate-analysis":
        print(json.dumps(validate_analysis_package(args.path), sort_keys=True)); return 0
    print(json.dumps(validate_raw_package(args.path), sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["execute_plan", "main", "write_analysis_package", "write_not_executed"]
