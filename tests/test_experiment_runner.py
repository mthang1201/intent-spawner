from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from experiments.jsonl_io import append_jsonl, read_jsonl
from experiments.methods import METHODS, decide_method
from experiments.recorder import build_record, load_workloads, run_local_workload, workload_by_id
from experiments.runner import (
    completed_keys,
    generate_matrix,
    run_matrix,
    select_workloads,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "workloads.yaml"


def _workloads() -> list[dict]:
    return load_workloads(MANIFEST)


def test_matrix_generation_uses_manifest_size_and_selected_methods():
    workloads = _workloads()
    matrix = generate_matrix(
        workloads,
        ["static_manual", "intent_only", "context_aware"],
        repeats=5,
        seed=0,
        experiment_id="fixture-matrix",
    )

    assert len(matrix) == len(workloads) * len(METHODS) * 5
    assert {item.method for item in matrix} == set(METHODS)
    assert {item.workload_id for item in matrix} == {workload["workload_id"] for workload in workloads}


def test_repeat_indexing_is_zero_based_per_combination():
    workload = workload_by_id(MANIFEST, "light_basic_python")
    matrix = generate_matrix(
        [workload],
        ["context_aware"],
        repeats=4,
        seed=0,
        experiment_id="fixture-repeats",
    )

    assert [item.repeat_index for item in matrix] == [0, 1, 2, 3]


def test_deterministic_seed_derivation_is_stable_and_seeded():
    workload = workload_by_id(MANIFEST, "data_dataframe_join_medium")
    first = generate_matrix([workload], ["intent_only"], repeats=3, seed=7, experiment_id="fixture")
    second = generate_matrix([workload], ["intent_only"], repeats=3, seed=7, experiment_id="fixture")
    third = generate_matrix([workload], ["intent_only"], repeats=3, seed=8, experiment_id="fixture")

    assert [item.seed for item in first] == [item.seed for item in second]
    assert [item.run_id for item in first] == [item.run_id for item in second]
    assert [item.seed for item in first] != [item.seed for item in third]


def test_resume_skips_completed_records_without_rewriting(tmp_path):
    workload = workload_by_id(MANIFEST, "light_basic_python")
    matrix = generate_matrix(
        [workload],
        ["context_aware"],
        repeats=2,
        seed=0,
        experiment_id="fixture-resume",
    )
    experiment_dir = tmp_path / "raw" / "fixture-resume"
    first_record = build_record(
        workload=workload,
        method=matrix[0].method,
        repeat_index=matrix[0].repeat_index,
        seed=matrix[0].seed,
        environment_id="pytest",
        run_id=matrix[0].run_id,
    )
    append_jsonl(experiment_dir / "results.jsonl", first_record)

    summary = run_matrix(
        matrix=matrix,
        workloads_by_id={workload["workload_id"]: workload},
        experiment_dir=experiment_dir,
        environment_id="pytest",
        resume=True,
    )

    records = read_jsonl(experiment_dir / "results.jsonl")
    assert summary["skipped_completed"] == 1
    assert summary["attempted"] == 1
    assert [record["repeat_index"] for record in records] == [0, 1]
    assert completed_keys(experiment_dir / "results.jsonl") == {
        ("context_aware", "light_basic_python", 0),
        ("context_aware", "light_basic_python", 1),
    }


def test_timeout_behavior_is_recorded(tmp_path):
    workload = deepcopy(workload_by_id(MANIFEST, "ml_sklearn_fit_memory_pressure"))
    local_result = run_local_workload(workload, 3103, tmp_path / "timeout-artifacts", timeout_seconds=0.001)

    assert local_result["timeout"] is True
    assert local_result["success"] is False
    assert local_result["exit_reason"] == "Timeout"
    assert local_result["cleanup_status"] == "completed"


def test_cleanup_status_is_recorded_after_failed_local_workload(tmp_path):
    workload = deepcopy(workload_by_id(MANIFEST, "light_basic_python"))
    workload["workload"]["command"] = [
        "python3",
        "-c",
        "import sys; print('intentional failure'); sys.exit(7)",
    ]

    local_result = run_local_workload(workload, 1101, tmp_path / "failed-artifacts")

    assert local_result["exit_code"] == 7
    assert local_result["success"] is False
    assert local_result["cleanup_status"] == "completed"


def test_matrix_run_ids_are_unique():
    matrix = generate_matrix(
        _workloads(),
        METHODS,
        repeats=5,
        seed=3,
        experiment_id="fixture-unique",
    )
    run_ids = [item.run_id for item in matrix]

    assert len(run_ids) == len(set(run_ids))


def test_method_isolation_for_same_workload():
    workload = workload_by_id(MANIFEST, "policy_gpu_disallowed")

    static_decision = decide_method("static_manual", workload)
    intent_decision = decide_method("intent_only", workload)
    context_decision = decide_method("context_aware", workload)

    assert static_decision.recommended_profile is None
    assert static_decision.applied_profile == "medium"
    assert intent_decision.recommended_profile == "small"
    assert intent_decision.applied_profile == "small"
    assert context_decision.recommended_profile == "gpu_or_large"
    assert context_decision.applied_profile == "medium"
    assert context_decision.policy_warnings


def test_intent_only_does_not_use_dataset_size_or_code_context_fields():
    base = deepcopy(workload_by_id(MANIFEST, "light_basic_python"))
    changed_context = deepcopy(base)
    changed_context["dataset_size_hint_gb"] = 9.9
    changed_context["code_context_hints"] = [
        "import torch",
        "model.cuda()",
        "pd.read_csv('large.csv')",
    ]

    base_decision = decide_method("intent_only", base)
    changed_decision = decide_method("intent_only", changed_context)

    assert base_decision == changed_decision
    assert changed_decision.context_signal_summary["raw_context_available"] is False
    assert changed_decision.context_signal_summary["hint_count"] == 0
    assert changed_decision.context_signal_summary["detected_terms"] == []
    assert changed_decision.context_signal_summary["dataset_size_signal_used"] is False


def test_workload_and_category_selection():
    workloads = _workloads()

    selected_by_workload = select_workloads(workloads, workload_ids=["light_basic_python"])
    selected_by_category = select_workloads(workloads, categories=["machine_learning"])

    assert [workload["workload_id"] for workload in selected_by_workload] == ["light_basic_python"]
    assert selected_by_category
    assert {workload["category"] for workload in selected_by_category} == {"machine_learning"}
