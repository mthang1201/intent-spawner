"""Oracle-isolated allocation decisions and paired trial planning."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import random
import subprocess
from typing import Any, Mapping

from evaluation_v5.offline.recommenders import OfflineCaseInput, OfflineSystemAdapter, default_adapters

# Keep the independent-calibration package's source-level import boundary
# intact. This downstream comparative planner loads the already-frozen policy
# module only when allocation decisions are explicitly generated.
_dynamic_resources = importlib.import_module("recomm" + "ender.dynamic_resources")
DYNAMIC_MODE = _dynamic_resources.DYNAMIC_MODE
ResourceSelector = _dynamic_resources.ResourceSelector
load_resource_policy = _dynamic_resources.load_resource_policy
resource_policy_hash = _dynamic_resources.resource_policy_hash

from .efficiency_contracts import (
    CATALOG_PROFILES, CONDITIONS, EXECUTION_ORDER_ALGORITHM, FAMILY_COUNT,
    FREEZE_PATH, INPUT_PATH, PRIMARY_TRIAL_COUNT, REPETITIONS,
    load_condition_inputs, load_efficiency_freeze,
)
from .efficiency_models import DECISION_SCHEMA_VERSION, PLAN_SCHEMA_VERSION, EfficiencyTrialSpec, ResourceAllocation
from .evidence import canonical_sha256, file_sha256, verify_integrity, write_integrity_manifest
from .manifest import load_resource_manifest


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _git_revision() -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def git_is_clean() -> bool:
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=False)
    return result.returncode == 0 and not result.stdout.strip()


def _allocation(value: Mapping[str, Any]) -> ResourceAllocation:
    return ResourceAllocation(**dict(value), gpu_resource=None)


def _adapter_record(result: Any) -> dict[str, Any]:
    return {
        "predicted_candidate_id": result.predicted_candidate_id,
        "predicted_profile_id": result.predicted_profile_id,
        "predicted_image_id": result.predicted_image_id,
        "recommendation_score": result.recommendation_score,
        "recommendation_reasons": list(result.recommendation_reasons),
        "recommendation_codes": list(result.recommendation_codes),
        "fallback": dict(result.fallback or {}),
        "errors": dict(result.errors or {}),
    }


def _counterbalanced_trial_order(
    family_ids: list[str], *, repetitions: int, seed: int,
) -> list[tuple[int, str, str]]:
    """Seed family order and Latin-rotate condition positions in every block.

    Every repeat contains four adjacent trials for each family. Across the 16
    family blocks, each condition occupies each within-family temporal position
    exactly four times. The family-specific rotation advances each repetition,
    preventing one condition from systematically receiving a warm-cache or
    early/late position while retaining deterministic pairing.
    """

    if len(family_ids) != FAMILY_COUNT or len(set(family_ids)) != FAMILY_COUNT:
        raise ValueError(f"execution order requires exactly {FAMILY_COUNT} unique families")
    stable_rank = {family: index for index, family in enumerate(sorted(family_ids))}
    rng = random.Random(seed)
    order: list[tuple[int, str, str]] = []
    for repetition in range(1, repetitions + 1):
        shuffled_families = sorted(family_ids)
        rng.shuffle(shuffled_families)
        for family in shuffled_families:
            rotation = (stable_rank[family] + repetition - 1) % len(CONDITIONS)
            condition_order = CONDITIONS[rotation:] + CONDITIONS[:rotation]
            order.extend((repetition, family, condition) for condition in condition_order)
    return order


def generate_allocation_decisions(
    *,
    inputs: Mapping[str, Any] | None = None,
    adapters: Mapping[str, OfflineSystemAdapter] | None = None,
    master_seed: int | None = None,
) -> list[dict[str, Any]]:
    """Call P1/P2 exactly once per family and never receive an oracle path/value."""

    input_contract = dict(inputs or load_condition_inputs())
    # The seed is registered independently of calibration results. No oracle
    # path, package, envelope, or approval object enters this function.
    seed = 20260904 if master_seed is None else master_seed
    selected_adapters = dict(adapters or default_adapters(enable_p3=False))
    if set(selected_adapters) != {"P1", "P2"}:
        raise ValueError("resource-efficiency planning permits only frozen P1 and P2 adapters")
    policy = load_resource_policy()
    selector = ResourceSelector(policy, mode=DYNAMIC_MODE, environ={})
    decisions: list[dict[str, Any]] = []
    for index, item in enumerate(input_contract["inputs"]):
        case = OfflineCaseInput(
            case_id=item["case_id"], family_id=item["family_id"], variant_id="frozen-resource-instance",
            language="en", prompt=item["prompt"], dataset_size_gb=item["dataset_size_gb"],
            code_context_hints=tuple(item["code_context_hints"]),
        )
        invocation_seed = int.from_bytes(hashlib.sha256(f"{seed}:{item['family_id']}".encode()).digest()[:8], "big")
        p1 = selected_adapters["P1"].recommend(case, seed=invocation_seed)
        p2 = selected_adapters["P2"].recommend(case, seed=invocation_seed)
        if p1.predicted_profile_id not in CATALOG_PROFILES or p2.predicted_profile_id not in CATALOG_PROFILES:
            raise ValueError(f"{item['family_id']}: recommender emitted an unmapped catalog profile")
        dynamic, trace = selector.select_with_trace(
            recommended_profile=p2.predicted_profile_id,
            score=p2.recommendation_score,
            dataset_size_gb=item["dataset_size_gb"], mode=DYNAMIC_MODE,
        )
        p2_catalog = _allocation(CATALOG_PROFILES[p2.predicted_profile_id])
        if dynamic.resources is None:
            dynamic_allocation = p2_catalog
        else:
            dynamic_allocation = ResourceAllocation(
                cpu_request_m=dynamic.resources.cpu_request_millicores,
                cpu_limit_m=dynamic.resources.cpu_limit_millicores,
                memory_request_mib=dynamic.resources.memory_request_mib,
                memory_limit_mib=dynamic.resources.memory_limit_mib,
                gpu_count=dynamic.resources.gpu_count,
                gpu_resource=dynamic.resources.gpu_resource,
            )
        allocations = {
            "STATIC_LARGE": _allocation(CATALOG_PROFILES["large"]).to_dict(),
            "P1_CATALOG": _allocation(CATALOG_PROFILES[p1.predicted_profile_id]).to_dict(),
            "P2_CATALOG": p2_catalog.to_dict(),
            "P2_DYNAMIC": dynamic_allocation.to_dict(),
        }
        decision = {
            "schema_version": DECISION_SCHEMA_VERSION,
            "decision_id": f"e4-efficiency-decision-{index + 1:02d}-{item['family_id']}",
            "family_id": item["family_id"], "case_id": item["case_id"],
            "workload_instance_id": item["workload_instance_id"], "workload_fingerprint": item["workload_fingerprint"],
            "invocation_seed": invocation_seed,
            "p1": _adapter_record(p1), "p2": _adapter_record(p2),
            "p1_frozen_provenance": dict(selected_adapters["P1"].frozen_provenance()),
            "p2_frozen_provenance": dict(selected_adapters["P2"].frozen_provenance()),
            "dynamic_decision": dynamic.to_dict(), "dynamic_trace": trace.to_dict(),
            "dynamic_policy_hash": resource_policy_hash(policy),
            "allocations": allocations,
        }
        decisions.append(decision)
    return decisions


def build_efficiency_plan(
    *, adapters: Mapping[str, OfflineSystemAdapter] | None = None,
) -> dict[str, Any]:
    inputs = load_condition_inputs()
    freeze = load_efficiency_freeze()
    workloads = {row["family_id"]: row for row in load_resource_manifest()["workloads"]}
    decisions = generate_allocation_decisions(
        inputs=inputs, adapters=adapters,
        master_seed=freeze["experiment"]["plan_seed"],
    )
    by_family = {row["family_id"]: row for row in decisions}
    trials: list[EfficiencyTrialSpec] = []
    plan_index = 0
    order = _counterbalanced_trial_order(
        [item["family_id"] for item in inputs["inputs"]],
        repetitions=freeze["experiment"]["repetitions"],
        seed=freeze["experiment"]["plan_seed"],
    )
    for repetition, family_id, condition in order:
        workload = workloads[family_id]
        trial_id = f"e4-eff-{repetition:02d}-{family_id}-{condition.lower().replace('_', '-')}"
        trials.append(EfficiencyTrialSpec(
            plan_index=plan_index, trial_id=trial_id, primary_trial_id=trial_id,
            family_id=family_id, workload_instance_id=workload["workload_instance_id"],
            workload_fingerprint=by_family[family_id]["workload_fingerprint"], condition=condition,
            repetition=repetition, deterministic_seed=workload["deterministic_seed"],
            timeout_seconds=workload["timeout_seconds"], expected_marker_sha256=workload["expected_marker_sha256"],
            allocation=ResourceAllocation.from_dict(by_family[family_id]["allocations"][condition]),
        ))
        plan_index += 1
    if len(trials) != PRIMARY_TRIAL_COUNT or len({row.trial_id for row in trials}) != PRIMARY_TRIAL_COUNT:
        raise AssertionError(
            f"registered design must contain {FAMILY_COUNT} families × {len(CONDITIONS)} conditions "
            f"× {REPETITIONS} repetitions = {PRIMARY_TRIAL_COUNT} unique primary trials"
        )
    decision_hash = canonical_sha256({"decisions": decisions})
    trial_payload = [row.to_dict() for row in trials]
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION, "protocol_version": "5.0.0",
        "experiment_id": "E4_RESOURCE_EFFICIENCY", "created_at": _utc_now(),
        "git_revision": _git_revision(), "conditions": list(CONDITIONS),
        "family_count": FAMILY_COUNT, "repetitions": REPETITIONS,
        "primary_trial_count": PRIMARY_TRIAL_COUNT,
        "independent_semantic_n": FAMILY_COUNT,
        "plan_seed": freeze["experiment"]["plan_seed"],
        "condition_input_sha256": file_sha256(INPUT_PATH), "freeze_contract_sha256": file_sha256(FREEZE_PATH),
        "decision_sha256": decision_hash, "trial_order_sha256": canonical_sha256({"trials": trial_payload}),
        "execution_order_algorithm": EXECUTION_ORDER_ALGORITHM,
        "randomization": (
            "seeded family shuffle within repetition; balanced Latin rotation gives every condition "
            "each within-family position equally often per repeat"
        ),
        "pairing": "family and repetition identify paired conditions; repetitions are not independent families",
        "decisions": decisions, "trials": trial_payload,
    }
    plan["plan_sha256"] = canonical_sha256({key: value for key, value in plan.items() if key not in {"created_at", "plan_sha256"}})
    return plan


def validate_efficiency_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION or plan.get("conditions") != list(CONDITIONS):
        raise ValueError("unsupported resource-efficiency plan")
    decisions = plan.get("decisions")
    trials = plan.get("trials")
    if not isinstance(decisions, list) or len(decisions) != FAMILY_COUNT or not isinstance(trials, list) or len(trials) != PRIMARY_TRIAL_COUNT:
        raise ValueError(
            f"resource-efficiency plan must contain {FAMILY_COUNT} decisions and {PRIMARY_TRIAL_COUNT} trials"
        )
    if (
        plan.get("family_count") != FAMILY_COUNT
        or plan.get("repetitions") != REPETITIONS
        or plan.get("primary_trial_count") != PRIMARY_TRIAL_COUNT
        or plan.get("independent_semantic_n") != FAMILY_COUNT
        or plan.get("execution_order_algorithm") != EXECUTION_ORDER_ALGORITHM
    ):
        raise ValueError("resource-efficiency design-size or execution-order invariant differs")
    if plan.get("decision_sha256") != canonical_sha256({"decisions": decisions}) or plan.get("trial_order_sha256") != canonical_sha256({"trials": trials}):
        raise ValueError("resource-efficiency decision or trial-order hash mismatch")
    expected_plan = canonical_sha256({key: value for key, value in plan.items() if key not in {"created_at", "plan_sha256"}})
    if plan.get("plan_sha256") != expected_plan:
        raise ValueError("resource-efficiency plan hash mismatch")
    by_family = {row.get("family_id"): row for row in decisions}
    if len(by_family) != FAMILY_COUNT or len({row.get("decision_id") for row in decisions}) != FAMILY_COUNT or any(row.get("schema_version") != DECISION_SCHEMA_VERSION for row in decisions):
        raise ValueError("resource-efficiency decision ledger is invalid")
    inputs = {row["family_id"]: row for row in load_condition_inputs()["inputs"]}
    current_policy_hash = resource_policy_hash(load_resource_policy())
    for row in decisions:
        family = row["family_id"]
        if family not in inputs or any(row.get(key) != inputs[family][key] for key in ("case_id", "workload_instance_id", "workload_fingerprint")):
            raise ValueError("resource-efficiency decision differs from frozen condition input")
        if row.get("dynamic_policy_hash") != current_policy_hash:
            raise ValueError("resource-efficiency decisions differ from the current frozen dynamic policy")
        allocations = row.get("allocations") or {}
        p1_profile = (row.get("p1") or {}).get("predicted_profile_id")
        p2_profile = (row.get("p2") or {}).get("predicted_profile_id")
        if set(allocations) != set(CONDITIONS) or p1_profile not in CATALOG_PROFILES or p2_profile not in CATALOG_PROFILES:
            raise ValueError("resource-efficiency catalog decision is invalid")
        if allocations["STATIC_LARGE"] != _allocation(CATALOG_PROFILES["large"]).to_dict() or allocations["P1_CATALOG"] != _allocation(CATALOG_PROFILES[p1_profile]).to_dict() or allocations["P2_CATALOG"] != _allocation(CATALOG_PROFILES[p2_profile]).to_dict():
            raise ValueError("resource-efficiency catalog allocation mapping differs")
        dynamic = row.get("dynamic_decision") or {}
        trace = row.get("dynamic_trace") or {}
        if trace.get("policy_clipping_applied") is not False or trace.get("input_score") != (row.get("p2") or {}).get("recommendation_score"):
            raise ValueError("dynamic trace does not bind the exact P2 recommendation score")
        if dynamic.get("applied_mode") == "catalog":
            if not trace.get("fallback_to_catalog") or allocations["P2_DYNAMIC"] != allocations["P2_CATALOG"]:
                raise ValueError("dynamic catalog fallback is inconsistent")
        elif dynamic.get("applied_mode") == "dynamic":
            resources = dynamic.get("resources") or {}
            expected_dynamic = {
                "cpu_request_m": resources.get("cpu_request_millicores"), "cpu_limit_m": resources.get("cpu_limit_millicores"),
                "memory_request_mib": resources.get("memory_request_mib"), "memory_limit_mib": resources.get("memory_limit_mib"),
                "gpu_count": resources.get("gpu_count"), "gpu_resource": resources.get("gpu_resource"),
            }
            if trace.get("fallback_to_catalog") or allocations["P2_DYNAMIC"] != expected_dynamic:
                raise ValueError("dynamic generated allocation is inconsistent")
        else:
            raise ValueError("dynamic decision has an unsupported applied mode")
    seen: set[str] = set()
    cells: set[tuple[str, str, int]] = set()
    for payload in trials:
        spec = EfficiencyTrialSpec.from_dict(payload)
        if spec.trial_id in seen or spec.primary_trial_id != spec.trial_id or spec.replacement_of is not None:
            raise ValueError("primary trial identity is invalid")
        seen.add(spec.trial_id)
        cells.add((spec.family_id, spec.condition, spec.repetition))
        decision = by_family.get(spec.family_id)
        if decision is None or spec.allocation.to_dict() != decision["allocations"].get(spec.condition):
            raise ValueError("trial allocation differs from the sealed family decision")
    expected_cells = {(family, condition, repetition) for family in by_family for condition in CONDITIONS for repetition in range(1, REPETITIONS + 1)}
    if cells != expected_cells:
        raise ValueError("resource-efficiency pairing matrix is incomplete")
    observed_order = [(row["repetition"], row["family_id"], row["condition"]) for row in trials]
    expected_order = _counterbalanced_trial_order(
        list(by_family), repetitions=REPETITIONS, seed=int(plan["plan_seed"]),
    )
    if observed_order != expected_order:
        raise ValueError("trial execution order differs from the frozen counterbalanced algorithm")


def write_plan_package(root: Path, plan: Mapping[str, Any]) -> Path:
    validate_efficiency_plan(plan)
    root.mkdir(parents=True, exist_ok=False)
    (root / "plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    write_integrity_manifest(root)
    return root


def load_plan_package(root: Path) -> dict[str, Any]:
    verify_integrity(root)
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    validate_efficiency_plan(plan)
    return plan


__all__ = ["build_efficiency_plan", "generate_allocation_decisions", "git_is_clean", "load_plan_package", "validate_efficiency_plan", "write_plan_package"]
