"""Protocol-v5 E4 calibration orchestration and command-line interface."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Mapping, Sequence

from evaluation_v5.provenance import write_json_exclusive

from .derive import (
    cell_acceptable, derive_safe_envelopes, reference_is_stable,
    trial_basic_success,
)
from .contracts import (
    COMPARISON_SCHEMA_PATH, CROSSWALK_PATH, ELIGIBILITY_PATH, FREEZE_CONTRACT_PATH, IMAGE_STATE_PATH,
    SEMANTIC_PATH, freeze_is_confirmatory, image_state_is_verified,
    load_cluster_policy, load_crosswalk, load_freeze_contract, load_image_state,
    load_semantic_independence, static_independence_scan,
)
from .evidence import (
    append_jsonl_fsync,
    canonical_sha256,
    file_sha256,
    load_observations,
    validate_evidence_package,
    validate_trial_observation,
    write_integrity_manifest,
)
from .manifest import (
    CPU_LATTICE_M,
    DEFAULT_MANIFEST,
    MEMORY_LATTICE_MIB,
    load_resource_manifest,
    verify_workload_markers,
    workload_fingerprint,
    workloads_by_id,
)
from .models import TrialAdapter, TrialObservation, TrialSpec
from .planner import build_calibration_plan, make_trial_spec


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FREEZE = ROOT / "results_v5" / "protocol-v5.0.0" / "freezes" / "frozen-configuration.json"
RUN_SCHEMA_VERSION = "protocol-v5-resource-calibration-run-v1.0.0"
RUNNER_VERSION = "protocol-v5-resource-calibration-runner-v1.1.0"


class InfrastructureExhausted(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _git_identity() -> dict[str, Any]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = bool(subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip())
    return {"git_revision": revision, "git_dirty": dirty}


def _comparison_provenance(freeze_path: Path) -> dict[str, Any]:
    payload = json.loads(freeze_path.read_text(encoding="utf-8"))
    systems = payload.get("systems", {})
    p3 = payload.get("p3_gate", {})
    if not isinstance(systems, dict) or "P1" not in systems or "P2" not in systems:
        raise ValueError("frozen comparison snapshot lacks P1/P2 identities")
    if p3.get("status") != "not_retained" or p3.get("p3_active") is not False:
        raise ValueError("E4 calibration requires the frozen not-retained P3 gate")
    return {
        "role": "comparison_provenance_only_not_calibration_input",
        "freeze_path": str(freeze_path.relative_to(ROOT)),
        "freeze_sha256": file_sha256(freeze_path),
        "systems": {"P1": systems["P1"], "P2": systems["P2"]},
        "candidate_catalog": payload.get("candidate_catalog"),
        "indexes": payload.get("indexes"),
        "p3_gate": {"status": "not_retained", "p3_active": False},
    }


def _base_provenance(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    freeze_path: Path,
    *,
    run_id: str,
    image: str,
    adapter_version: str,
) -> dict[str, Any]:
    plan = build_calibration_plan(manifest)
    comparison = _comparison_provenance(freeze_path)
    semantic = load_semantic_independence(manifest_path=manifest_path)
    policy = load_cluster_policy()
    image_state = load_image_state()
    freeze_contract = load_freeze_contract()
    crosswalk = load_crosswalk(manifest_path=manifest_path)
    static_guard = static_independence_scan()
    contracts = {
        "semantic_independence": {
            "path": str(SEMANTIC_PATH.relative_to(ROOT)),
            "schema_version": semantic["schema_version"],
            "sha256": file_sha256(SEMANTIC_PATH),
        },
        "cluster_eligibility": {
            "path": str(ELIGIBILITY_PATH.relative_to(ROOT)),
            "schema_version": policy["schema_version"],
            "sha256": file_sha256(ELIGIBILITY_PATH),
        },
        "image_state": {
            "path": str(IMAGE_STATE_PATH.relative_to(ROOT)),
            "schema_version": image_state["schema_version"],
            "sha256": file_sha256(IMAGE_STATE_PATH),
            "status": image_state["status"],
        },
        "freeze_contract": {
            "path": str(FREEZE_CONTRACT_PATH.relative_to(ROOT)),
            "schema_version": freeze_contract["schema_version"],
            "sha256": file_sha256(FREEZE_CONTRACT_PATH),
            "status": freeze_contract["confirmatory_freeze_status"],
        },
        "allocation_comparison": {
            "version": crosswalk["comparison_contract_version"],
            "schema_path": str(COMPARISON_SCHEMA_PATH.relative_to(ROOT)),
            "schema_sha256": file_sha256(COMPARISON_SCHEMA_PATH),
            "crosswalk_path": str(CROSSWALK_PATH.relative_to(ROOT)),
            "crosswalk_sha256": file_sha256(CROSSWALK_PATH),
        },
    }
    identity = {
        "run_id": run_id,
        "manifest_sha256": file_sha256(manifest_path),
        "freeze_sha256": comparison["freeze_sha256"],
        "image": image,
        "adapter_version": adapter_version,
        "plan_sha256": canonical_sha256(plan),
        "contracts": contracts,
    }
    return {
        "schema_version": "protocol-v5-resource-run-provenance-v1.0.0",
        "protocol_version": "5.0.0",
        "experiment_id": "E4",
        "run_id": run_id,
        "runner_version": RUNNER_VERSION,
        "created_at_utc": _utc_now(),
        **_git_identity(),
        "workload_manifest": {
            "path": str(manifest_path.relative_to(ROOT)),
            "schema_version": manifest["schema_version"],
            "sha256": file_sha256(manifest_path),
            "family_count": 16,
        },
        "safe_rule": {
            "version": manifest["safe_rule"]["version"],
            "reference_stability_rule_version": manifest["safe_rule"]["reference_stability_rule_version"],
        },
        "frozen_contracts": contracts,
        "comparison_provenance": comparison,
        "container_image": image,
        "adapter_version": adapter_version,
        "plan_fingerprint": canonical_sha256(identity),
        "collection_status": "INCOMPLETE_UNTIL_ROOT_MANIFEST_AND_REVIEW",
        "calibration_independence": {
            "recommendation_backends_called": False,
            "recommendation_outputs_used": False,
            "profile_labels_used": False,
            "pressure_padding_used": False,
            "static_guard": static_guard,
        },
    }


def _make_directories(result_dir: Path, *, resume: bool) -> None:
    if resume:
        if (
            not result_dir.is_dir()
            or (result_dir / "SHA256SUMS").exists()
            or (result_dir / "manifest.json").exists()
        ):
            raise ValueError("resume requires an existing unsealed resource run")
    else:
        result_dir.mkdir(parents=True, exist_ok=False)
        for name in ("raw", "derived", "report"):
            (result_dir / name).mkdir()


def _write_text_exclusive(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def create_dry_run_package(
    *,
    result_dir: Path,
    run_id: str,
    manifest_path: Path = DEFAULT_MANIFEST,
    freeze_path: Path = DEFAULT_FREEZE,
    image: str,
    unavailable_reason: str,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    freeze_path = freeze_path.resolve()
    manifest = load_resource_manifest(manifest_path)
    _make_directories(result_dir, resume=False)
    plan = build_calibration_plan(manifest)
    provenance = _base_provenance(
        manifest_path, manifest, freeze_path, run_id=run_id, image=image,
        adapter_version="dry-run-adapter-v1",
    )
    from cluster_evaluation.resource_adapter_v5 import IMAGE_RE, collect_read_only_preflight

    policy = load_cluster_policy()
    declared_image_state = load_image_state()
    preflight = collect_read_only_preflight(image=image, policy=policy, image_state=declared_image_state)
    freeze_contract = load_freeze_contract()
    image_state = {
        "reference_configured": bool(image),
        "digest_syntactically_pinned": bool(IMAGE_RE.fullmatch(image)),
        "built": declared_image_state.get("built"),
        "resolved_digest": declared_image_state.get("resolved_digest"),
        "digest_verified": declared_image_state.get("digest_verified"),
        "pre_pulled_on_eligible_node": declared_image_state.get("pre_pulled_on_eligible_node"),
        "operationally_verified": declared_image_state.get("operationally_verified"),
        "declared_state_status": declared_image_state.get("status"),
    }
    readiness_failures = list(preflight["failure_codes"])
    if provenance["git_dirty"]:
        readiness_failures.append("DIRTY_GIT_TREE")
    if not freeze_is_confirmatory(freeze_contract):
        readiness_failures.append("CONFIRMATORY_FREEZE_NOT_ACTIVE")
    if not image_state_is_verified(declared_image_state, image):
        readiness_failures.append("IMAGE_DIGEST_UNVERIFIED")
    readiness_failures = sorted(set(readiness_failures))
    environment = {
        "schema_version": "protocol-v5-resource-environment-v1.0.0",
        "captured_at_utc": _utc_now(),
        "environment_id": "local-dry-run-no-cluster-measurement",
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "platform_release": platform.release(),
        "cluster_measurement_status": "NOT_EXECUTED",
        "reason_code": "KUBERNETES_PRECONDITION_UNAVAILABLE",
        "reason": unavailable_reason,
        "read_only_cluster_preflight": preflight,
        "cluster_eligibility_status": "ELIGIBLE" if not preflight["failure_codes"] else "CLUSTER_INELIGIBLE",
        "observed_execution_readiness": "READY" if not readiness_failures else "BLOCKED",
        "observed_execution_blockers": readiness_failures,
        "image_state": image_state,
        "hardware_measurements": None,
        "cgroup_measurements": None,
        "kubernetes_mutations": [],
    }
    root_manifest = {
        "schema_version": RUN_SCHEMA_VERSION,
        "protocol_version": "5.0.0",
        "experiment_id": "E4",
        "run_id": run_id,
        "execution_timestamp_utc": _utc_now(),
        "execution_status": "DRY_RUN",
        "cluster_measurement_status": "NOT_EXECUTED",
        "measurement_claims_permitted": False,
        "git_revision": provenance["git_revision"],
        "git_dirty": provenance["git_dirty"],
        "workload_manifest_sha256": provenance["workload_manifest"]["sha256"],
        "frozen_comparison_sha256": provenance["comparison_provenance"]["freeze_sha256"],
        "semantic_independence_sha256": provenance["frozen_contracts"]["semantic_independence"]["sha256"],
        "comparison_crosswalk_sha256": provenance["frozen_contracts"]["allocation_comparison"]["crosswalk_sha256"],
        "comparison_contract_version": provenance["frozen_contracts"]["allocation_comparison"]["version"],
        "comparison_contract_sha256": provenance["frozen_contracts"]["allocation_comparison"]["schema_sha256"],
        "safe_rule_version": manifest["safe_rule"]["version"],
        "reference_stability_rule_version": manifest["safe_rule"]["reference_stability_rule_version"],
        "cluster_eligibility_policy_version": policy["schema_version"],
        "cluster_eligibility_policy_sha256": provenance["frozen_contracts"]["cluster_eligibility"]["sha256"],
        "image_state_sha256": provenance["frozen_contracts"]["image_state"]["sha256"],
        "freeze_contract_sha256": provenance["frozen_contracts"]["freeze_contract"]["sha256"],
        "container_image": image,
        "environment_identity": environment["environment_id"],
        "manual_review_status": "NOT_APPLICABLE",
        "observed_execution_readiness": environment["observed_execution_readiness"],
    }
    status = {
        "status": "DRY_RUN",
        "cluster_measurement_status": "NOT_EXECUTED",
        "planned_families": 16,
        "executed_trials": 0,
        "derived_envelopes": 0,
        "manual_review_status": "NOT_APPLICABLE",
        "reason": unavailable_reason,
        "cluster_eligibility_status": environment["cluster_eligibility_status"],
        "observed_execution_readiness": environment["observed_execution_readiness"],
        "observed_execution_blockers": readiness_failures,
        "image_state": image_state,
        "limitations": [
            "No Kubernetes workload was executed.",
            "No CPU, memory, runtime, OOM, hardware, or cgroup value was observed.",
            "This package validates planning and provenance only and supports no empirical claim.",
        ],
    }
    write_json_exclusive(result_dir / "raw" / "plan.json", plan)
    write_json_exclusive(result_dir / "raw" / "run-provenance.json", provenance)
    write_json_exclusive(result_dir / "raw" / "environment.json", environment)
    write_json_exclusive(result_dir / "report" / "status.json", status)
    write_json_exclusive(result_dir / "report" / "manual-review.json", {
        "status": "NOT_APPLICABLE", "reason": "No empirical observations exist in a dry-run package."
    })
    write_json_exclusive(result_dir / "manifest.json", root_manifest)
    write_integrity_manifest(result_dir)
    return validate_evidence_package(result_dir)


def _observation_by_id(path: Path) -> dict[str, TrialObservation]:
    return {item.run_id: item for item in load_observations(path)}


def _decision_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value.get("decision_id"), str) or value["decision_id"] in ids:
                raise ValueError("invalid or duplicate adaptive decision ledger entry")
            ids.add(value["decision_id"])
    return ids


def _record_decision(path: Path, seen: set[str], payload: Mapping[str, Any]) -> None:
    decision_id = str(payload["decision_id"])
    if decision_id in seen:
        return
    append_jsonl_fsync(path, {
        "schema_version": "protocol-v5-resource-search-decision-v1.0.0",
        "recorded_at_utc": _utc_now(),
        **dict(payload),
    })
    seen.add(decision_id)


def _record_cell_decision(
    path: Path,
    seen: set[str],
    workload: Mapping[str, Any],
    *,
    phase: str,
    cpu_m: int,
    memory_mib: int,
    rows: Sequence[TrialObservation],
    accepted: bool,
    reference_median: float | None,
) -> None:
    _record_decision(path, seen, {
        "decision_id": f"{workload['family_id']}:{phase}:c{cpu_m}:m{memory_mib}",
        "decision_type": "tested_cell_classification",
        "family_id": workload["family_id"],
        "workload_instance_id": workload["workload_instance_id"],
        "workload_fingerprint": workload_fingerprint(workload),
        "phase": phase,
        "cpu_m": cpu_m,
        "memory_mib": memory_mib,
        "source_trial_ids": [row.run_id for row in rows],
        "reference_median_runtime_seconds": reference_median,
        "accepted_under_frozen_safe_rule": accepted,
    })


def _execute(
    adapter: TrialAdapter,
    spec: TrialSpec,
    *,
    records_path: Path,
    run_directory: Path,
    cached: dict[str, TrialObservation],
) -> TrialObservation:
    if spec.run_id in cached:
        observation = cached[spec.run_id]
        validate_trial_observation(observation, spec)
        sidecar_directory = run_directory / spec.run_id
        if not (sidecar_directory / "record.json").is_file():
            sidecar_directory.mkdir(parents=True, exist_ok=True)
            write_json_exclusive(sidecar_directory / "record.json", observation.to_dict())
    else:
        observation = adapter.run_trial(spec)
        validate_trial_observation(observation, spec)
        append_jsonl_fsync(records_path, observation.to_dict())
        (run_directory / spec.run_id).mkdir(parents=True, exist_ok=False)
        write_json_exclusive(run_directory / spec.run_id / "record.json", observation.to_dict())
        cached[spec.run_id] = observation
    if not observation.infrastructure_invalid:
        return observation
    if spec.replacement_of is not None:
        raise InfrastructureExhausted(spec.replacement_of)
    replacement = replace(
        spec,
        run_id=f"{spec.run_id}-replacement",
        replacement_of=spec.run_id,
    )
    replacement_observation = _execute(
        adapter, replacement, records_path=records_path,
        run_directory=run_directory, cached=cached,
    )
    if replacement_observation.infrastructure_invalid:
        raise InfrastructureExhausted(spec.run_id)
    return replacement_observation


def _run_cell(
    adapter: TrialAdapter,
    workload: Mapping[str, Any],
    *,
    phase: str,
    cpu_m: int,
    memory_mib: int,
    repeats: int,
    next_index: list[int],
    records_path: Path,
    run_directory: Path,
    cached: dict[str, TrialObservation],
) -> list[TrialObservation]:
    rows = []
    for repeat in range(repeats):
        spec = make_trial_spec(
            workload, phase=phase, cpu_m=cpu_m, memory_mib=memory_mib,
            repeat_index=repeat, plan_index=next_index[0],
        )
        next_index[0] += 1
        rows.append(_execute(
            adapter, spec, records_path=records_path,
            run_directory=run_directory, cached=cached,
        ))
    return rows


def run_calibration(
    *,
    result_dir: Path,
    run_id: str,
    adapter: TrialAdapter,
    manifest_path: Path = DEFAULT_MANIFEST,
    freeze_path: Path = DEFAULT_FREEZE,
    image: str,
    resume: bool = False,
    enforce_readiness: bool = True,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    freeze_path = freeze_path.resolve()
    if not resume and result_dir.exists():
        raise FileExistsError(result_dir)
    if resume and (
        not result_dir.is_dir()
        or (result_dir / "SHA256SUMS").exists()
        or (result_dir / "manifest.json").exists()
    ):
        raise ValueError("resume requires an existing unsealed resource run")
    manifest = load_resource_manifest(manifest_path)
    workloads = workloads_by_id(manifest)
    provenance = _base_provenance(
        manifest_path, manifest, freeze_path, run_id=run_id, image=image,
        adapter_version=adapter.adapter_version,
    )
    environment_snapshot: Mapping[str, Any] | None = None
    if enforce_readiness:
        blockers: list[str] = []
        if provenance["git_dirty"]:
            blockers.append("DIRTY_GIT_TREE")
        if not freeze_is_confirmatory(load_freeze_contract()):
            blockers.append("CONFIRMATORY_FREEZE_NOT_ACTIVE")
        if not image_state_is_verified(load_image_state(), image):
            blockers.append("IMAGE_DIGEST_UNVERIFIED")
        if blockers:
            raise RuntimeError("OBSERVED_E4_EXECUTION_BLOCKED: " + ",".join(blockers))
        environment_snapshot = adapter.environment_provenance()
        if environment_snapshot.get("eligibility_status") != "ELIGIBLE":
            raise RuntimeError("OBSERVED_E4_EXECUTION_BLOCKED: CLUSTER_INELIGIBLE")
    _make_directories(result_dir, resume=resume)
    provenance_path = result_dir / "raw" / "run-provenance.json"
    plan_path = result_dir / "raw" / "plan.json"
    environment_path = result_dir / "raw" / "environment.json"
    if resume:
        prior = json.loads(provenance_path.read_text(encoding="utf-8"))
        if prior.get("plan_fingerprint") != provenance["plan_fingerprint"]:
            raise ValueError("resume provenance fingerprint mismatch")
    else:
        if provenance["git_dirty"] and enforce_readiness:
            raise RuntimeError("observed E4 calibration requires a clean Git tree")
        write_json_exclusive(provenance_path, provenance)
        write_json_exclusive(plan_path, build_calibration_plan(manifest))
        write_json_exclusive(environment_path, dict(environment_snapshot or adapter.environment_provenance()))
    records_path = result_dir / "raw" / "trials.jsonl"
    decisions_path = result_dir / "raw" / "decision-ledger.jsonl"
    run_directory = result_dir / "raw" / "runs"
    cached = _observation_by_id(records_path)
    decisions_seen = _decision_ids(decisions_path)
    next_index = [len(cached)]

    for workload in workloads.values():
        try:
            reference = _run_cell(
                adapter, workload, phase="reference", cpu_m=2000, memory_mib=2048,
                repeats=3, next_index=next_index, records_path=records_path,
                run_directory=run_directory, cached=cached,
            )
            reference_usable = len(reference) == 3 and all(trial_basic_success(row) for row in reference)
            if not reference_usable:
                _record_cell_decision(
                    decisions_path, decisions_seen, workload, phase="reference",
                    cpu_m=2000, memory_mib=2048, rows=reference,
                    accepted=False, reference_median=None,
                )
                continue
            reference_median = sorted(float(row.runtime_seconds) for row in reference if row.runtime_seconds is not None)[1]
            reference_stable, _ = reference_is_stable(
                [float(row.runtime_seconds) for row in reference if row.runtime_seconds is not None]
            )
            _record_cell_decision(
                decisions_path, decisions_seen, workload, phase="reference",
                cpu_m=2000, memory_mib=2048, rows=reference,
                accepted=reference_stable, reference_median=reference_median,
            )
            if not reference_stable:
                continue

            max_memory_rows = _run_cell(
                adapter, workload, phase="memory_probe", cpu_m=2000,
                memory_mib=MEMORY_LATTICE_MIB[-1], repeats=2,
                next_index=next_index, records_path=records_path,
                run_directory=run_directory, cached=cached,
            )
            max_memory_accepted = cell_acceptable(max_memory_rows, reference_median, 2)
            _record_cell_decision(
                decisions_path, decisions_seen, workload, phase="memory_probe",
                cpu_m=2000, memory_mib=MEMORY_LATTICE_MIB[-1], rows=max_memory_rows,
                accepted=max_memory_accepted, reference_median=reference_median,
            )
            if not max_memory_accepted:
                continue
            low, high = -1, len(MEMORY_LATTICE_MIB) - 1
            while high - low > 1:
                middle = (low + high) // 2
                rows = _run_cell(
                    adapter, workload, phase="memory_probe", cpu_m=2000,
                    memory_mib=MEMORY_LATTICE_MIB[middle], repeats=2,
                    next_index=next_index, records_path=records_path,
                    run_directory=run_directory, cached=cached,
                )
                accepted = cell_acceptable(rows, reference_median, 2)
                _record_cell_decision(
                    decisions_path, decisions_seen, workload, phase="memory_probe",
                    cpu_m=2000, memory_mib=MEMORY_LATTICE_MIB[middle], rows=rows,
                    accepted=accepted, reference_median=reference_median,
                )
                if accepted:
                    high = middle
                else:
                    low = middle
            selected_memory = MEMORY_LATTICE_MIB[high]
            if high > 0:
                lower_rows = _run_cell(
                    adapter, workload, phase="memory_probe", cpu_m=2000,
                    memory_mib=MEMORY_LATTICE_MIB[high - 1], repeats=2,
                    next_index=next_index, records_path=records_path,
                    run_directory=run_directory, cached=cached,
                )
                _record_cell_decision(
                    decisions_path, decisions_seen, workload, phase="memory_probe",
                    cpu_m=2000, memory_mib=MEMORY_LATTICE_MIB[high - 1], rows=lower_rows,
                    accepted=cell_acceptable(lower_rows, reference_median, 2),
                    reference_median=reference_median,
                )

            max_cpu_rows = _run_cell(
                adapter, workload, phase="cpu_probe", cpu_m=CPU_LATTICE_M[-1],
                memory_mib=selected_memory, repeats=2, next_index=next_index,
                records_path=records_path, run_directory=run_directory, cached=cached,
            )
            max_cpu_accepted = cell_acceptable(max_cpu_rows, reference_median, 2)
            _record_cell_decision(
                decisions_path, decisions_seen, workload, phase="cpu_probe",
                cpu_m=CPU_LATTICE_M[-1], memory_mib=selected_memory, rows=max_cpu_rows,
                accepted=max_cpu_accepted, reference_median=reference_median,
            )
            if not max_cpu_accepted:
                continue
            low, high = -1, len(CPU_LATTICE_M) - 1
            while high - low > 1:
                middle = (low + high) // 2
                rows = _run_cell(
                    adapter, workload, phase="cpu_probe", cpu_m=CPU_LATTICE_M[middle],
                    memory_mib=selected_memory, repeats=2, next_index=next_index,
                    records_path=records_path, run_directory=run_directory, cached=cached,
                )
                accepted = cell_acceptable(rows, reference_median, 2)
                _record_cell_decision(
                    decisions_path, decisions_seen, workload, phase="cpu_probe",
                    cpu_m=CPU_LATTICE_M[middle], memory_mib=selected_memory, rows=rows,
                    accepted=accepted, reference_median=reference_median,
                )
                if accepted:
                    high = middle
                else:
                    low = middle
            selected_cpu = CPU_LATTICE_M[high]
            if high > 0:
                lower_rows = _run_cell(
                    adapter, workload, phase="cpu_probe", cpu_m=CPU_LATTICE_M[high - 1],
                    memory_mib=selected_memory, repeats=2, next_index=next_index,
                    records_path=records_path, run_directory=run_directory, cached=cached,
                )
                _record_cell_decision(
                    decisions_path, decisions_seen, workload, phase="cpu_probe",
                    cpu_m=CPU_LATTICE_M[high - 1], memory_mib=selected_memory,
                    rows=lower_rows, accepted=cell_acceptable(lower_rows, reference_median, 2),
                    reference_median=reference_median,
                )
            joint_rows = _run_cell(
                adapter, workload, phase="joint_verification", cpu_m=selected_cpu,
                memory_mib=selected_memory, repeats=5, next_index=next_index,
                records_path=records_path, run_directory=run_directory, cached=cached,
            )
            _record_cell_decision(
                decisions_path, decisions_seen, workload, phase="joint_verification",
                cpu_m=selected_cpu, memory_mib=selected_memory, rows=joint_rows,
                accepted=cell_acceptable(joint_rows, reference_median, 5),
                reference_median=reference_median,
            )
        except InfrastructureExhausted:
            _record_decision(decisions_path, decisions_seen, {
                "decision_id": f"{workload['family_id']}:infrastructure-replacement-exhausted",
                "decision_type": "family_execution_stopped",
                "family_id": workload["family_id"],
                "workload_instance_id": workload["workload_instance_id"],
                "workload_fingerprint": workload_fingerprint(workload),
                "reason_code": "INFRASTRUCTURE_REPLACEMENT_EXHAUSTED",
            })
            continue

    observations = load_observations(records_path)
    derived = derive_safe_envelopes(manifest, observations)
    manual = "REQUIRED" if any(item["manual_review_status"] == "REQUIRED" for item in derived["envelopes"]) else "PENDING"
    root_manifest = {
        "schema_version": RUN_SCHEMA_VERSION,
        "protocol_version": "5.0.0",
        "experiment_id": "E4",
        "run_id": run_id,
        "execution_timestamp_utc": _utc_now(),
        "execution_status": "OBSERVED",
        "cluster_measurement_status": "OBSERVED",
        "measurement_claims_permitted": False,
        "git_revision": provenance["git_revision"],
        "git_dirty": False,
        "workload_manifest_sha256": provenance["workload_manifest"]["sha256"],
        "frozen_comparison_sha256": provenance["comparison_provenance"]["freeze_sha256"],
        "semantic_independence_sha256": provenance["frozen_contracts"]["semantic_independence"]["sha256"],
        "comparison_contract_version": provenance["frozen_contracts"]["allocation_comparison"]["version"],
        "comparison_contract_sha256": provenance["frozen_contracts"]["allocation_comparison"]["schema_sha256"],
        "comparison_crosswalk_sha256": provenance["frozen_contracts"]["allocation_comparison"]["crosswalk_sha256"],
        "safe_rule_version": manifest["safe_rule"]["version"],
        "reference_stability_rule_version": manifest["safe_rule"]["reference_stability_rule_version"],
        "cluster_eligibility_policy_version": provenance["frozen_contracts"]["cluster_eligibility"]["schema_version"],
        "cluster_eligibility_policy_sha256": provenance["frozen_contracts"]["cluster_eligibility"]["sha256"],
        "image_state_sha256": provenance["frozen_contracts"]["image_state"]["sha256"],
        "freeze_contract_sha256": provenance["frozen_contracts"]["freeze_contract"]["sha256"],
        "container_image": image,
        "environment_identity": dict(adapter.environment_provenance()).get("environment_id"),
        "manual_review_status": manual,
    }
    write_json_exclusive(result_dir / "derived" / "safe-envelopes.json", derived)
    review_components = {
        relative: file_sha256(result_dir / relative)
        for relative in (
            "raw/plan.json", "raw/run-provenance.json", "raw/environment.json",
            "raw/trials.jsonl", "raw/decision-ledger.jsonl", "derived/safe-envelopes.json",
        )
    }
    review_input_fingerprint = canonical_sha256(review_components)
    write_json_exclusive(result_dir / "report" / "status.json", {
        "status": "OBSERVED_PENDING_MANUAL_REVIEW",
        "cluster_measurement_status": "OBSERVED",
        "executed_trials": len(observations),
        "manual_review_status": manual,
        "eligible_for_comparison": False,
        "review_input_components": review_components,
        "review_input_fingerprint": review_input_fingerprint,
        "limitations": [
            "Repeated executions estimate stability and runtime variability; they are not independent workload samples.",
            "Envelope minima are interval-censored by the fixed CPU and memory lattices.",
            "No result is eligible for comparison until an exclusive manual-review attestation is recorded.",
        ],
    })
    write_json_exclusive(result_dir / "manifest.json", root_manifest)
    return validate_evidence_package(result_dir, allow_unsealed=True)


def record_manual_review(result_dir: Path, *, reviewer_id: str, decision: str, reason: str) -> dict[str, Any]:
    if decision not in {"APPROVED", "REJECTED"}:
        raise ValueError("manual review decision must be APPROVED or REJECTED")
    if not reviewer_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in reviewer_id):
        raise ValueError("reviewer_id must be a pseudonymous filesystem-safe identifier")
    if not reason.strip():
        raise ValueError("manual review requires a reason")
    if (result_dir / "SHA256SUMS").exists():
        raise FileExistsError("resource package is already sealed")
    existing_review = result_dir / "report" / "manual-review.json"
    if existing_review.exists():
        raise FileExistsError("manual-review attestation already exists")
    root = json.loads((result_dir / "manifest.json").read_text(encoding="utf-8"))
    if root.get("execution_status") != "OBSERVED" or root.get("manual_review_status") not in {"PENDING", "REQUIRED"}:
        raise ValueError("illegal manual-review state transition")
    source = result_dir / "derived" / "safe-envelopes.json"
    if not source.is_file():
        raise ValueError("manual review requires derived envelopes")
    status = json.loads((result_dir / "report" / "status.json").read_text(encoding="utf-8"))
    components = status.get("review_input_components")
    expected_fingerprint = status.get("review_input_fingerprint")
    if not isinstance(components, dict) or not isinstance(expected_fingerprint, str):
        raise ValueError("manual review lacks pre-review fingerprint")
    actual_components = {relative: file_sha256(result_dir / relative) for relative in components}
    if actual_components != components or canonical_sha256(actual_components) != expected_fingerprint:
        raise ValueError("manual review input fingerprint mismatch")
    attestation = {
        "schema_version": "protocol-v5-resource-manual-review-v1.1.0",
        "prior_state": root["manual_review_status"],
        "decision": decision,
        "reviewer_id": reviewer_id,
        "reviewed_at_utc": _utc_now(),
        "reason": reason,
        "safe_envelopes_sha256": file_sha256(source),
        "review_input_fingerprint": expected_fingerprint,
        "review_input_components": components,
        "eligible_for_comparison": decision == "APPROVED",
    }
    write_json_exclusive(result_dir / "report" / "manual-review.json", attestation)
    write_integrity_manifest(result_dir)
    return validate_evidence_package(result_dir)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Protocol-v5 E4 resource-envelope calibration")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-manifest")
    validate.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    dry = sub.add_parser("dry-run")
    dry.add_argument("--result-dir", type=Path, required=True)
    dry.add_argument("--run-id", required=True)
    dry.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    dry.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    dry.add_argument("--image", required=True)
    dry.add_argument("--reason", required=True)
    execute = sub.add_parser("execute")
    execute.add_argument("--result-dir", type=Path, required=True)
    execute.add_argument("--run-id", required=True)
    execute.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    execute.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    execute.add_argument("--image", required=True)
    execute.add_argument("--resume", action="store_true")
    verify = sub.add_parser("validate-evidence")
    verify.add_argument("--result-dir", type=Path, required=True)
    verify.add_argument("--allow-unsealed", action="store_true")
    derive = sub.add_parser("derive")
    derive.add_argument("--result-dir", type=Path, required=True)
    derive.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    review = sub.add_parser("review")
    review.add_argument("--result-dir", type=Path, required=True)
    review.add_argument("--reviewer-id", required=True)
    review.add_argument("--decision", choices=("APPROVED", "REJECTED"), required=True)
    review.add_argument("--reason", required=True)
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "validate-manifest":
        manifest = load_resource_manifest(args.manifest)
        marker_report = verify_workload_markers(manifest)
        semantic = load_semantic_independence(manifest_path=args.manifest)
        crosswalk = load_crosswalk(manifest_path=args.manifest)
        guard = static_independence_scan()
        print(json.dumps({
            "status": "valid", "families": len(manifest["workloads"]),
            "schema_version": manifest["schema_version"], **marker_report,
            "semantic_independence_version": semantic["schema_version"],
            "cluster_eligibility_version": load_cluster_policy()["schema_version"],
            "freeze_contract_version": load_freeze_contract()["schema_version"],
            "image_state_version": load_image_state()["schema_version"],
            "comparison_contract_version": crosswalk["comparison_contract_version"],
            "static_independence": guard,
        }, sort_keys=True))
        return 0
    if args.command == "dry-run":
        report = create_dry_run_package(
            result_dir=args.result_dir.resolve(), run_id=args.run_id,
            manifest_path=args.manifest, freeze_path=args.freeze,
            image=args.image, unavailable_reason=args.reason,
        )
    elif args.command == "execute":
        from cluster_evaluation.resource_adapter_v5 import KubernetesTrialAdapter
        adapter = KubernetesTrialAdapter(image=args.image)
        report = run_calibration(
            result_dir=args.result_dir.resolve(), run_id=args.run_id,
            adapter=adapter, manifest_path=args.manifest,
            freeze_path=args.freeze, image=args.image, resume=args.resume,
        )
    elif args.command == "validate-evidence":
        report = validate_evidence_package(args.result_dir, allow_unsealed=args.allow_unsealed)
    elif args.command == "derive":
        observations = load_observations(args.result_dir / "raw" / "trials.jsonl")
        report = derive_safe_envelopes(load_resource_manifest(args.manifest), observations)
        output = args.result_dir / "derived" / "safe-envelopes.json"
        write_json_exclusive(output, report)
    else:
        report = record_manual_review(
            args.result_dir.resolve(), reviewer_id=args.reviewer_id,
            decision=args.decision, reason=args.reason,
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
