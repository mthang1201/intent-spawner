from copy import deepcopy

from cluster_evaluation.policies import PROFILE_RESOURCES, decide_cluster_method
from cluster_evaluation.runner import (
    _parse_cpu_quantity_m,
    build_pod_spec,
    generate_plan,
    load_workloads,
)
from cluster_evaluation.validate_artifacts import validate


def _workload(workload_id: str = "data_large_aggregation"):
    return next(item for item in load_workloads() if item["workload_id"] == workload_id)


def test_static_default_is_fixed_and_cannot_read_oracle_fields():
    original = _workload()
    mutated = deepcopy(original)
    mutated["intent"] = "completely different intent"
    mutated["dataset_size_hint_gb"] = 999
    mutated["code_context_hints"] = ["torch cuda tensorflow"]
    mutated["expected_acceptable_profiles"] = ["small"]

    before = decide_cluster_method("static_default", original)
    after = decide_cluster_method("static_default", mutated)
    assert before == after
    assert before.applied_profile == "medium"


def test_intent_only_does_not_receive_dataset_or_code_context():
    original = _workload("data_dataframe_join_medium")
    mutated = deepcopy(original)
    mutated["dataset_size_hint_gb"] = 999
    mutated["code_context_hints"] = ["torch cuda tensorflow"]
    assert decide_cluster_method("intent_only", original) == decide_cluster_method("intent_only", mutated)


def test_ground_truth_plan_sweeps_all_profiles_three_times():
    plan = generate_plan("ground-truth", 3, 20260720, "ground")
    assert len(plan) == len(load_workloads()) * 3 * 3
    for workload in load_workloads():
        selected = [item for item in plan if item.workload_id == workload["workload_id"]]
        assert {item.applied_profile for item in selected} == {"small", "medium", "large"}
        assert all(item.recommended_profile is None for item in selected)


def test_comparative_plan_is_deterministic_and_has_five_repeats():
    first = generate_plan("comparative", 5, 20260720, "comparison")
    second = generate_plan("comparative", 5, 20260720, "comparison")
    assert first == second
    assert len(first) == len(load_workloads()) * 3 * 5
    assert {item.method for item in first} == {"static_default", "intent_only", "context_aware"}


def test_applied_profile_changes_actual_pod_resources():
    workload = _workload("light_basic_python")
    items = {
        item.applied_profile: item
        for item in generate_plan("ground-truth", 1, 1, "ground")
        if item.workload_id == workload["workload_id"]
    }
    for profile, item in items.items():
        spec = build_pod_spec(item, workload, "example.invalid/benchmark@sha256:abc")
        actual = spec["spec"]["containers"][0]["resources"]
        expected = PROFILE_RESOURCES[profile]
        assert actual["requests"] == {
            "cpu": expected["cpu_request"],
            "memory": expected["memory_request"],
        }
        assert actual["limits"] == {
            "cpu": expected["cpu_limit"],
            "memory": expected["memory_limit"],
        }


def test_pod_spec_contains_no_raw_intent_or_code_context():
    workload = _workload()
    item = next(
        candidate
        for candidate in generate_plan("comparative", 1, 1, "comparison")
        if candidate.workload_id == workload["workload_id"] and candidate.method == "context_aware"
    )
    encoded = str(build_pod_spec(item, workload, "image"))
    assert workload["intent"] not in encoded
    assert all(hint not in encoded for hint in workload["code_context_hints"])


def test_cpu_quantity_parser_handles_metrics_api_units():
    assert _parse_cpu_quantity_m("250m") == 250
    assert _parse_cpu_quantity_m("500000n") == 0.5
    assert _parse_cpu_quantity_m("2500u") == 2.5
    assert _parse_cpu_quantity_m("1") == 1000


def test_unsampled_cpu_average_is_not_reported_as_a_peak(monkeypatch):
    from itertools import count

    from cluster_evaluation import pod_runner

    readings = count(1000, 100)
    monkeypatch.setattr(pod_runner, "_cpu_usage_usec", lambda: next(readings))
    sampler = pod_runner.CgroupSampler(60)
    sampler.start()
    result = sampler.stop()
    assert result["sample_count"] == 0
    assert result["peak_cpu_m"] is None
    assert result["full_window_average_cpu_m"] is not None


def test_runner_source_does_not_allowlist_machine_identifiers():
    source = __import__("inspect").getsource(__import__("cluster_evaluation.runner", fromlist=["x"])._preflight)
    assert "bootID" not in source
    assert "machineID" not in source
    assert "systemUUID" not in source


def test_preserved_cluster_artifacts_reconcile():
    summary = validate()
    assert summary["status"] == "pass"
    assert summary["ground_truth_records"] == 108
    assert summary["comparative_records"] == 180
    assert summary["capacity_batches"] == 9
    assert summary["capacity_pods"] == 108
    assert summary["periodically_sampled_cpu_records"] == 86
    assert summary["historical_full_window_cpu_values_mislabeled_as_peak"] == 202
