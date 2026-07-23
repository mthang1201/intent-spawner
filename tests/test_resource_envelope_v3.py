from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from benchmarks.resource_envelope_runner import (
    MAX_DEADLINE_SECONDS,
    MAX_TARGET_MIB,
    deterministic_seed,
    load_manifest,
    main as workload_main,
    run_workload,
    validate_workload,
)
from cluster_evaluation.analyze_v3 import (
    _mcnemar_exact,
    analyze_comparative,
    derive_ground_truth,
    validate_calibration,
    wilson_interval,
)
from cluster_evaluation.jupyterhub_v3 import generate_plan as generate_jupyterhub_plan
from cluster_evaluation.evidence_v3 import verify_sha256sums
from cluster_evaluation.image_policy_v3 import (
    helm_singleuser_image,
    validate_dockerfiles,
)
from cluster_evaluation.policies import PROFILE_RESOURCES, decide_cluster_method
from cluster_evaluation.result_schema_v3 import REQUIRED_FIELDS, validate_record
from cluster_evaluation.runner_v3 import (
    build_pod_spec,
    generate_plan,
    load_workloads,
    main as runner_main,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "workloads-v3.yaml"


def _workload(workload_id: str) -> dict:
    return next(
        item for item in load_manifest()["workloads"] if item["workload_id"] == workload_id
    )


def test_v3_manifest_is_separate_bounded_and_synthetic():
    manifest = load_manifest()
    assert manifest["schema_version"] == "3.0.0"
    assert manifest["master_seed"] == 20260723
    assert len(manifest["workloads"]) == 12
    assert len({item["workload_id"] for item in manifest["workloads"]}) == 12
    assert sum(item["evaluation_set"] == "calibration" for item in manifest["workloads"]) == 4
    assert sum(item["evaluation_set"] == "holdout_core" for item in manifest["workloads"]) == 6
    assert sum(item["evaluation_set"] == "holdout_robustness" for item in manifest["workloads"]) == 2
    assert sum(bool(item.get("sentinel_end_to_end")) for item in manifest["workloads"]) == 5
    for workload in manifest["workloads"]:
        assert workload["target_cgroup_mib"] <= MAX_TARGET_MIB
        assert workload["workload_deadline_seconds"] <= MAX_DEADLINE_SECONDS
        assert workload["hold_seconds"] <= 8
        assert workload["data_source"]["type"] == "synthetic"
        assert "no external dataset license" in workload["license"].lower()


def test_validate_only_does_not_execute_pressure_workloads(capsys):
    assert workload_main(["--manifest", str(MANIFEST), "--validate-only"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": "3.0.0",
        "status": "valid",
        "workloads": 12,
    }


def test_v3_hard_caps_cannot_be_overridden():
    workload = deepcopy(_workload("h01_small_stream"))
    workload["target_cgroup_mib"] = MAX_TARGET_MIB + 1
    with pytest.raises(ValueError, match="target exceeds"):
        validate_workload(workload)
    workload = deepcopy(_workload("h01_small_stream"))
    workload["workload_deadline_seconds"] = MAX_DEADLINE_SECONDS + 1
    with pytest.raises(ValueError, match="deadline exceeds"):
        validate_workload(workload)


def test_v3_manifest_rejects_cross_stratum_fields():
    workload = deepcopy(_workload("h01_small_stream"))
    workload["calibration_profiles"] = ["small"]
    with pytest.raises(ValueError, match="hold-out cannot"):
        validate_workload(workload)


def test_v3_dockerfile_base_images_use_digest_without_mutable_tag():
    bases = validate_dockerfiles()
    assert len(bases) == 2
    assert all("@sha256:" in image for image in bases)
    assert all(":latest@" not in image for image in bases)


def test_v3_helm_placeholder_fails_immutable_policy():
    with pytest.raises(ValueError, match="image.tag must be empty"):
        helm_singleuser_image()


def test_v3_helm_accepts_full_immutable_reference(tmp_path):
    values = tmp_path / "values.yaml"
    values.write_text(
        "singleuser:\n"
        "  image:\n"
        "    name: registry.example/research/jupyter@sha256:"
        + "a" * 64
        + "\n"
        "    tag: \"\"\n",
        encoding="utf-8",
    )
    assert helm_singleuser_image(values).endswith("a" * 64)


def test_v3_checksum_manifest_rejects_uncovered_file(tmp_path):
    (tmp_path / "evidence.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "SHA256SUMS").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected"):
        verify_sha256sums(tmp_path)


def test_non_pressure_execution_is_deterministic_without_large_allocation():
    workload = deepcopy(_workload("h01_small_stream"))
    workload.update(
        {
            "target_cgroup_mib": 0,
            "target_band_mib": [0, 16],
            "hold_seconds": 0,
            "max_allocation_mib": 16,
        }
    )
    first = run_workload(workload, 123)
    second = run_workload(workload, 123)
    assert first["checksum"] == second["checksum"]
    assert first["pressure_padding_bytes"] == 0
    assert first["synthetic_data"] is True
    assert first["data_persisted"] is False


def test_seed_formula_is_stable_and_paired():
    assert deterministic_seed("h01_small_stream", 0) == deterministic_seed(
        "h01_small_stream", 0
    )
    assert deterministic_seed("h01_small_stream", 0) != deterministic_seed(
        "h01_small_stream", 1
    )


def test_method_expectations_match_the_current_recommender():
    for workload in load_manifest()["workloads"]:
        if not workload["evaluation_set"].startswith("holdout_"):
            continue
        actual = {
            method: decide_cluster_method(method, workload).applied_profile
            for method in ("static_default", "intent_only", "context_aware")
        }
        assert actual == workload["expected_method_profiles"]


def test_preregistered_direct_pod_plan_sizes_and_latin_pairing():
    calibration = generate_plan("calibration", "v3-test")
    ground = generate_plan("ground-truth", "v3-test")
    comparison = generate_plan("comparative", "v3-test")
    assert len(calibration) == 24
    assert len(ground) == 120
    assert len(comparison) == 120
    assert {item.evaluation_set for item in comparison} == {
        "holdout_core",
        "holdout_robustness",
    }
    for workload_id in {item.workload_id for item in comparison}:
        for repeat in range(5):
            selected = [
                item
                for item in comparison
                if item.workload_id == workload_id and item.repeat_index == repeat
            ]
            assert len(selected) == 3
            assert len({item.random_seed for item in selected}) == 1
            assert {item.method for item in selected} == {
                "static_default",
                "intent_only",
                "context_aware",
            }


def test_direct_pod_spec_enforces_resources_deadline_and_data_minimization():
    workload = _workload("h05_large_context_recovery")
    item = next(
        item
        for item in generate_plan("comparative", "v3-test")
        if item.workload_id == workload["workload_id"]
        and item.method == "context_aware"
        and item.repeat_index == 0
    )
    spec = build_pod_spec(item, workload, "example.invalid/v3@sha256:abc")
    container = spec["spec"]["containers"][0]
    expected = PROFILE_RESOURCES["large"]
    assert spec["metadata"]["namespace"] == "z2jh-context-demo"
    assert spec["spec"]["restartPolicy"] == "Never"
    assert spec["spec"]["automountServiceAccountToken"] is False
    assert spec["spec"]["activeDeadlineSeconds"] == workload["workload_deadline_seconds"] + 30
    assert container["resources"]["limits"] == {
        "cpu": expected["cpu_limit"],
        "memory": expected["memory_limit"],
    }
    encoded = json.dumps(spec)
    assert workload["intent"] not in encoded
    assert all(hint not in encoded for hint in workload["code_context_hints"])


def test_direct_runner_dry_run_is_cluster_free(capsys):
    assert (
        runner_main(
            [
                "--kind",
                "comparative",
                "--experiment-id",
                "v3-dry",
                "--image",
                "example.invalid/v3@sha256:abc",
                "--dry-run",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["planned_trials"] == 120
    assert payload["cluster_mutation"] is False
    assert payload["namespace"] == "z2jh-context-demo"


def test_jupyterhub_plan_has_five_sentinels_three_methods_three_repeats():
    plan = generate_jupyterhub_plan("v3-e2e")
    assert len(plan) == 45
    assert len({item.workload_id for item in plan}) == 5
    assert {item.method for item in plan} == {
        "static_default",
        "intent_only",
        "context_aware",
    }
    assert all(item.synthetic_username.startswith("v3-") for item in plan)


def test_v3_schema_rejects_undeclared_fields():
    record = {field: None for field in REQUIRED_FIELDS}
    record.update(
        {
            "schema_version": "3.0.0",
            "protocol_version": "3.0.0",
            "experiment_path": "direct_pod",
            "experiment_kind": "comparative",
            "run_id": "run",
            "plan_index": 0,
            "evaluation_set": "holdout_core",
            "workload_id": "h01_small_stream",
            "repeat_index": 0,
            "random_seed": 1,
            "method": "context_aware",
            "applied_profile": "small",
            "observed_profile": "small",
            "recommendation_reasons": [],
            "context_signal_summary": {},
            "target_cgroup_mib": 325,
            "target_band_mib": [315, 335],
            "oom_killed": False,
            "timeout": False,
            "success": True,
            "infrastructure_invalid": False,
            "exclusion_reason": None,
            "supporting_log_paths": [],
            "failure_category": "success",
            "git_commit": "0" * 40,
            "container_image": "example.invalid/v3@sha256:" + "0" * 64,
            "configuration_identity": None,
            "input_sha256": "0" * 64,
            "supporting_evidence_sha256": {},
            "timestamp_created": "2026-07-23T00:00:00Z",
            "timestamp_recorded": "2026-07-23T00:01:00Z",
        }
    )
    validate_record(record)
    record["raw_notebook"] = "must not be stored"
    with pytest.raises(ValueError, match="undeclared"):
        validate_record(record)


def test_wilson_interval_handles_empty_and_observed_counts():
    assert wilson_interval(0, 0) == [None, None]
    lower, upper = wilson_interval(5, 5)
    assert 0 < lower < upper == 1


def test_exact_mcnemar_uses_paired_workload_repeat_keys():
    rows = []
    for repeat in range(5):
        rows.extend(
            [
                {
                    "workload_id": "h02_medium_size_signal",
                    "repeat_index": repeat,
                    "method": "context_aware",
                    "success": True,
                },
                {
                    "workload_id": "h02_medium_size_signal",
                    "repeat_index": repeat,
                    "method": "intent_only",
                    "success": False,
                },
            ]
        )
    result = _mcnemar_exact(rows, "intent_only", "success")
    assert result["context_only"] == 5
    assert result["baseline_only"] == 0
    assert result["discordant_pairs"] == 5
    assert result["two_sided_exact_p"] == pytest.approx(0.0625)


def _simulated_record(item, workload, *, success: bool, oom: bool, peak: float | None, runtime: float):
    resources = PROFILE_RESOURCES[item.applied_profile]
    return {
        "schema_version": "3.0.0",
        "protocol_version": "3.0.0",
        "experiment_path": "direct_pod",
        "experiment_kind": item.experiment_kind,
        "run_id": item.run_id,
        "plan_index": item.plan_index,
        "evaluation_set": item.evaluation_set,
        "workload_id": item.workload_id,
        "repeat_index": item.repeat_index,
        "random_seed": item.random_seed,
        "method": item.method,
        "recommended_profile": item.recommended_profile,
        "applied_profile": item.applied_profile,
        "observed_profile": item.applied_profile,
        "recommendation_reasons": item.recommendation_reasons,
        "context_signal_summary": item.context_signal_summary,
        "cpu_request_m": resources["cpu_request_m"],
        "cpu_limit_m": resources["cpu_limit_m"],
        "memory_request_mi": resources["memory_request_mi"],
        "memory_limit_mi": resources["memory_limit_mi"],
        "target_cgroup_mib": workload["target_cgroup_mib"],
        "target_band_mib": workload["target_band_mib"],
        "actual_cgroup_peak_mib": peak,
        "useful_allocation_bytes": 1024 if success else None,
        "pressure_padding_bytes": 2048 if success else None,
        "hold_seconds": workload["hold_seconds"],
        "cpu_full_window_average_m": 500 if success else None,
        "cpu_interval_sample_max_m": 510 if success else None,
        "cpu_nr_periods_delta": 300 if success else None,
        "cpu_nr_throttled_delta": 3 if success else None,
        "cpu_throttled_usec_delta": 100 if success else None,
        "cgroup_sample_interval_seconds": 0.1 if success else None,
        "cgroup_sample_count": 300 if success else None,
        "benchmark_runtime_seconds": runtime if success else None,
        "pod_pending_duration_seconds": 1,
        "spawn_latency_seconds": None,
        "time_to_outcome_seconds": runtime,
        "phase": "Succeeded" if success else "Failed",
        "exit_code": 0 if success else 137,
        "exit_reason": "Completed" if success else "OOMKilled",
        "oom_killed": oom,
        "timeout": False,
        "restart_count": 0,
        "checksum": f"checksum-{item.workload_id}-{item.repeat_index}" if success else None,
        "success": success,
        "infrastructure_invalid": False,
        "exclusion_reason": None,
        "replacement_run_id": None,
        "cleanup_status": "completed",
        "failure_category": "success" if success else ("oom_killed" if oom else "workload_failure"),
        "git_commit": "0" * 40,
        "container_image": "example.invalid/v3@sha256:" + "0" * 64,
        "configuration_identity": None,
        "input_sha256": "0" * 64,
        "supporting_evidence_sha256": {},
        "supporting_log_paths": [],
        "timestamp_created": "2026-07-23T00:00:00Z",
        "timestamp_recorded": "2026-07-23T00:01:00Z",
    }


def test_v3_calibration_ground_truth_and_comparative_analysis_pipeline():
    workloads = {item["workload_id"]: item for item in load_workloads()}
    calibration_records = []
    for item in generate_plan("calibration", "sim-cal"):
        workload = workloads[item.workload_id]
        minimum = workload["expected_minimum_profile"]
        success = (
            item.workload_id == "cal_cpu_units"
            or ("small", "medium", "large").index(item.applied_profile)
            >= ("small", "medium", "large").index(minimum)
        )
        peak = workload["target_cgroup_mib"] if success and workload["target_cgroup_mib"] else 100
        runtime = 35 if item.workload_id == "cal_cpu_units" and item.applied_profile == "medium" else 25
        calibration_records.append(
            _simulated_record(
                item,
                workload,
                success=success,
                oom=not success,
                peak=peak if success else None,
                runtime=runtime,
            )
        )
    assert validate_calibration(calibration_records)["status"] == "pass"

    ground_records = []
    for item in generate_plan("ground-truth", "sim-ground"):
        workload = workloads[item.workload_id]
        minimum = workload["expected_minimum_profile"]
        success = ("small", "medium", "large").index(item.applied_profile) >= (
            "small",
            "medium",
            "large",
        ).index(minimum)
        runtime = {
            "small": 60,
            "medium": 35,
            "large": 20,
        }[item.applied_profile] if item.workload_id == "h06_cpu_parallel" else 20
        ground_records.append(
            _simulated_record(
                item,
                workload,
                success=success,
                oom=not success,
                peak=workload["target_cgroup_mib"] if success else None,
                runtime=runtime,
            )
        )
    ground = derive_ground_truth(ground_records)
    assert ground["workloads"]["h01_small_stream"]["smallest_reliable_profile"] == "small"
    assert ground["workloads"]["h02_medium_size_signal"]["smallest_reliable_profile"] == "medium"
    assert ground["workloads"]["h04_large_honest"]["smallest_reliable_profile"] == "large"

    comparative_records = []
    for item in generate_plan("comparative", "sim-comparison"):
        workload = workloads[item.workload_id]
        minimum = workload["expected_minimum_profile"]
        success = ("small", "medium", "large").index(item.applied_profile) >= (
            "small",
            "medium",
            "large",
        ).index(minimum)
        comparative_records.append(
            _simulated_record(
                item,
                workload,
                success=success,
                oom=not success,
                peak=workload["target_cgroup_mib"] if success else None,
                runtime=30,
            )
        )
    comparison = analyze_comparative(comparative_records, ground)
    assert comparison["primary_stratum"] == "h01-h06"
    assert len(comparison["summaries"]) == 3
    assert len(comparison["robustness_cases"]) == 6
    assert len(comparison["supplementary_exact_mcnemar"]) == 4


def test_experiment_helm_values_are_explicitly_isolated():
    text = (ROOT / "helm" / "experiment-v3-values.yaml").read_text(encoding="utf-8")
    assert "EVALUATION_METHODS" in text
    assert "static_default" in text
    assert "intent_only" in text
    assert "context_aware" in text
    assert "z2jh-context-demo.local/experiment-v3" in text
    assert "authenticator_class: dummy" in text
    assert "Never expose" in text
