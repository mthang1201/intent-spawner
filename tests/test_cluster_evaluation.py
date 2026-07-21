from copy import deepcopy
import json
from pathlib import Path

import pytest

from cluster_evaluation.analyze import derive_envelopes
from cluster_evaluation.capacity_runner import (
    _minikube_profile,
    generate_capacity_plan,
    main as capacity_main,
)
from cluster_evaluation.policies import PROFILE_RESOURCES, decide_cluster_method
from cluster_evaluation.result_compat import cpu_reconciliation, normalize_cpu_measurement
from cluster_evaluation.raw_integrity import verify, verify_baseline
from cluster_evaluation.runner import (
    _parse_cpu_quantity_m,
    build_pod_spec,
    generate_plan,
    load_workloads,
)
from cluster_evaluation.validate_artifacts import validate
from cluster_evaluation.timing import (
    improvement_is_distinguishable,
    interval_censored_duration,
    median_censored_duration,
)


ROOT = Path(__file__).resolve().parents[1]


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
    assert result["cpu_interval_sample_max_m"] is None
    assert result["cpu_full_window_average_m"] is not None


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
    assert summary["sampled_cpu_records"] == 0
    assert summary["legacy_hybrid_cpu_records"] == 86
    assert summary["full_window_average_cpu_records"] == 202
    assert summary["genuine_cgroup_cpu_peak_records"] == 0


def test_current_and_pre_audit_raw_manifests_verify():
    assert verify()["verified_files"] >= 1541
    assert verify_baseline()["baseline_verified_files"] == 1541


def test_historical_cpu_compatibility_reconciles_without_mutating_raw():
    rows = []
    for name in (
        "ground-truth-39b6973-seed20260720",
        "comparative-39b6973-seed20260720",
    ):
        path = ROOT / "results" / "cluster" / "raw" / name / "results.jsonl"
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    before = json.dumps(rows[0], sort_keys=True)
    reconciliation = cpu_reconciliation(rows)
    assert reconciliation == {
        "genuine_cgroup_peak": 0,
        "average": 202,
        "sampled_instantaneous": 0,
        "legacy_hybrid_maximum": 86,
        "unavailable": 0,
        "total_records": 288,
    }
    normalized = normalize_cpu_measurement(rows[0], root=ROOT)
    assert normalized["legacy_source_field"] == "peak_cpu_m"
    assert json.dumps(rows[0], sort_keys=True) == before

    sampled_legacy = next(row for row in rows if row.get("cgroup_sample_count", 0) > 0)
    normalized_sampled = normalize_cpu_measurement(sampled_legacy, root=ROOT)
    assert (
        normalized_sampled["cpu_measurement_statistic"]
        == "legacy_interval_sample_or_full_window_maximum"
    )
    assert normalized_sampled["cpu_reconciliation_category"] == "legacy_hybrid_maximum"


def test_zero_duration_is_valid_and_interval_censored_without_offset():
    observation = interval_censored_duration(0)
    assert observation is not None
    assert observation.observed_seconds == 0
    assert observation.lower_seconds == 0
    assert observation.upper_seconds == 1


def test_one_second_quantization_requires_separated_intervals():
    baseline = median_censored_duration([1, 1, 1])
    candidate = median_censored_duration([0, 0, 0])
    assert improvement_is_distinguishable(baseline, candidate) is False
    assert improvement_is_distinguishable(
        median_censored_duration([7, 7, 7]),
        median_censored_duration([3, 3, 3]),
    ) is True


def test_missing_and_negative_timing_observations():
    assert interval_censored_duration(None) is None
    assert median_censored_duration([None, None]) is None
    with pytest.raises(ValueError, match="non-negative"):
        interval_censored_duration(-1)


def test_inconsistent_source_timestamps_are_rejected():
    from experiments.kubernetes_evidence import extract_pod_evidence

    fixture_path = ROOT / "tests" / "fixtures" / "kubernetes" / "pod_succeeded.json"
    pod = json.loads(fixture_path.read_text(encoding="utf-8"))
    pod["status"]["containerStatuses"][0]["state"]["terminated"]["finishedAt"] = (
        "2026-07-19T03:59:59Z"
    )
    with pytest.raises(ValueError, match="inconsistent timestamps"):
        extract_pod_evidence(pod)


def test_timing_envelope_regeneration_is_deterministic():
    path = ROOT / "results" / "cluster" / "raw" / "ground-truth-39b6973-seed20260720" / "results.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert derive_envelopes(deepcopy(rows)) == derive_envelopes(deepcopy(rows))


def test_capacity_plan_controls_population_concurrency_and_order():
    plan = generate_capacity_plan(repeats=3, seed=20260721, experiment_id="capacity-v2-test")
    assert len(plan) == 108
    assert len({item["run_id"] for item in plan}) == 108
    for repeat in range(3):
        for method in ("static_default", "intent_only", "context_aware"):
            selected = [
                item for item in plan if item["repeat_index"] == repeat and item["method"] == method
            ]
            assert len(selected) == 12


def test_capacity_runner_dry_run_is_cluster_free(capsys):
    assert capacity_main(
        [
            "--experiment-id",
            "capacity-v2-test",
            "--image",
            "example.invalid/eval@sha256:abc",
            "--dry-run",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["planned_pods"] == 108
    assert payload["planned_batches"] == 9
    assert payload["population_per_batch"] == 12


def test_capacity_runner_rejects_out_of_tree_evidence_before_cluster_access(tmp_path):
    with pytest.raises(ValueError, match="must be inside the repository"):
        capacity_main(
            [
                "--experiment-id",
                "capacity-v2-test",
                "--experiment-dir",
                str(tmp_path / "raw"),
                "--image",
                "example.invalid/eval:capacity-v2-test",
            ]
        )


def test_capacity_environment_sanitizes_minikube_profile(monkeypatch):
    from subprocess import CompletedProcess

    profile_payload = {
        "valid": [
            {
                "Name": "intent-spawner-capacity-v2",
                "Config": {
                    "CPUs": 6,
                    "Memory": 6144,
                    "DiskSize": 20480,
                    "Driver": "docker",
                    "KicBaseImage": "example.invalid/base@sha256:abc",
                    "MountString": "/Users/private:/host",
                    "Nodes": [{"IP": "192.0.2.1", "SSHKey": "/Users/private/key"}],
                    "KubernetesConfig": {
                        "KubernetesVersion": "v1.33.1",
                        "ContainerRuntime": "containerd",
                        "NetworkPlugin": "cni",
                        "CNI": "bridge",
                        "ServiceCIDR": "10.96.0.0/12",
                        "FeatureGates": "",
                        "ExtraOptions": {
                            "kubelet.system-reserved": "cpu=2,memory=2Gi"
                        },
                    },
                },
            }
        ]
    }
    monkeypatch.setattr(
        "cluster_evaluation.capacity_runner.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(
            args=args[0], returncode=0, stdout=json.dumps(profile_payload), stderr=""
        ),
    )
    captured = _minikube_profile("intent-spawner-capacity-v2")
    encoded = json.dumps(captured, sort_keys=True)
    assert captured["extra_options"] == {
        "kubelet.system-reserved": "cpu=2,memory=2Gi"
    }
    assert "/Users/private" not in encoded
    assert "192.0.2.1" not in encoded
