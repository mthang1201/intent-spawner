from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from evaluation_v4.analyze import (
    _round_p,
    analyze,
    analyze_reprovision_trials,
    analyze_system_trials,
    analyze_user_events,
    compare_system_trials,
    compare_user_acceptance,
)
from evaluation_v4.dataset import DEFAULT_DATASET, dataset_summary, load_dataset, validate_dataset
from evaluation_v4.run_recommenders import build_matrix, run
from evaluation_v4.plan_system import build_system_plan
from evaluation_v4.pod_runner import CgroupWindowSampler
from evaluation_v4.run_system import (
    HubSession,
    _load_resume_prefix,
    _next_attempt_directory,
    _workload_error_category,
)
from evaluation_v4.schemas import (
    REPROVISION_SCHEMA,
    SYSTEM_SCHEMA,
    SYSTEM_SCHEMA_V4_1,
    USER_SCHEMA,
    validate_system_trial,
)
from evaluation_v4.statistics import exact_mcnemar, holm_adjust


def test_gold_dataset_is_family_split_and_stratified():
    dataset = load_dataset()
    summary = dataset_summary(dataset)

    assert summary["samples"] == 60
    assert summary["families"] == 24
    assert summary["splits"] == {"development": 12, "test": 48}
    assert summary["languages"] == {"en": 36, "vi": 24}
    assert summary["system_families"] == 8
    assert len(summary["strata"]) >= 4

    family_splits: dict[str, set[str]] = {}
    for item in dataset["items"]:
        family_splits.setdefault(item["workload_family"], set()).add(item["split"])
    assert all(len(splits) == 1 for splits in family_splits.values())


def test_dataset_validator_rejects_family_leakage():
    dataset = deepcopy(load_dataset())
    family = dataset["items"][0]["workload_family"]
    same_family = [item for item in dataset["items"] if item["workload_family"] == family]
    same_family[-1]["split"] = "test"

    with pytest.raises(ValueError, match="must not cross"):
        validate_dataset(dataset)


def test_default_recommender_matrix_covers_locked_test_set():
    dataset = load_dataset()
    methods = [
        "static_small",
        "static_large",
        "rule_based_intent_only",
        "rule_based_context",
    ]

    matrix = build_matrix(dataset, methods, split="test", repeats=2, seed=7)

    assert len(matrix) == 48 * 4 * 2
    assert {item[1]["split"] for item in matrix} == {"test"}
    assert len({item[3] for item in matrix}) == len(matrix)


def test_system_plan_is_paired_and_randomized_by_repeat_block():
    methods = ["static_small", "static_large", "rule_based_context"]
    plan = build_system_plan(load_dataset(), methods, repeats=2, seed=11)

    assert len(plan) == 8 * 3 * 2
    assert [row["plan_index"] for row in plan] == list(range(len(plan)))
    cells: dict[tuple[int, str], list[dict]] = {}
    for row in plan:
        cells.setdefault((row["repeat_block"], row["workload_family"]), []).append(row)
    assert all({row["recommender"] for row in rows} == set(methods) for rows in cells.values())
    assert all(len({row["paired_workload_seed"] for row in rows}) == 1 for rows in cells.values())
    assert {row["cache_condition"] for row in plan} == {"warm_required"}


def test_v4_cgroup_sampler_reports_window_means_and_memory_peak(tmp_path):
    (tmp_path / "cpu.stat").write_text(
        "usage_usec 1000\nnr_periods 2\nnr_throttled 0\nthrottled_usec 0\n",
        encoding="utf-8",
    )
    (tmp_path / "memory.current").write_text(str(128 * 2**20), encoding="utf-8")
    (tmp_path / "memory.peak").write_text(str(160 * 2**20), encoding="utf-8")
    sampler = CgroupWindowSampler(0.05, cgroup_root=tmp_path)

    sampler.start()
    (tmp_path / "cpu.stat").write_text(
        "usage_usec 51000\nnr_periods 3\nnr_throttled 1\nthrottled_usec 100\n",
        encoding="utf-8",
    )
    metrics = sampler.stop()

    assert metrics["source"] == "cgroup_v2_in_container_window"
    assert metrics["cpu_usage_mean_m"] is not None
    assert metrics["memory_usage_mean_mib"] == 128.0
    assert metrics["memory_usage_peak_mib"] == 160.0
    assert metrics["sample_count"] >= 2


def test_hub_session_extracts_reprovision_javascript_xsrf():
    assert HubSession._xsrf('<script>const xsrf = "token-123";</script>') == "token-123"


def test_stage_c_classifies_inner_runner_timeout():
    executed = subprocess.CompletedProcess(
        ["kubectl", "exec"],
        1,
        stdout=b"",
        stderr=b"Traceback\nTimeoutError: CPU workload exceeded its bounded deadline\n",
    )

    assert _workload_error_category(executed) == "workload_timeout"
    assert _workload_error_category(
        subprocess.CompletedProcess(["kubectl", "exec"], 137, b"", b"OOM")
    ) == "workload_process_failure"


def _resume_fixture(tmp_path: Path) -> tuple[Path, list[dict], dict]:
    output = tmp_path / "stage-c"
    run_dir = output / "runs" / "trial-0"
    run_dir.mkdir(parents=True)
    sidecars = []
    for name in (
        "preview.json",
        "spawn-result.json",
        "pod-evidence.json",
        "workload.stdout",
        "workload.stderr",
        "cleanup.json",
    ):
        path = run_dir / name
        path.write_text("{}\n", encoding="utf-8")
        sidecars.append(str(path.relative_to(tmp_path)))
    (run_dir / "trial-metadata.json").write_text("{}\n", encoding="utf-8")
    environment = {
        "environment_id": "fixture-environment",
        "git_commit": "a" * 40,
        "plan_sha256": "b" * 64,
    }
    (output / "environment.json").write_text(
        json.dumps(environment), encoding="utf-8"
    )
    (output / "run-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "system-run-manifest-v4.0.0",
                "experiment_id": "fixture-stage-c",
                "plan_sha256": environment["plan_sha256"],
                "attempted_trials": 2,
            }
        ),
        encoding="utf-8",
    )
    plan = [
        {
            "trial_id": f"trial-{index}",
            "recommender": "rule_based_context",
            "representative_sample_id": "small-csv-canonical-en",
            "workload_family": "small-csv",
            "repeat_block": index,
        }
        for index in range(2)
    ]
    record = {
        **_system_record("observed", "trial-0"),
        "schema_version": SYSTEM_SCHEMA_V4_1,
        "experiment_id": "fixture-stage-c",
        "environment_id": environment["environment_id"],
        "git_commit": environment["git_commit"],
        "repeat_index": 0,
        "spawn_success": True,
        "timeout_event": False,
        "cpu_limit_m": 1000,
        "memory_limit_mib": 512,
        "fallback_used": False,
        "pod_identity_hash": "pod-sha256:" + "c" * 64,
        "node_identity_hash": "node-sha256:" + "d" * 64,
        "trial_error_category": None,
        "supporting_evidence_paths": sidecars,
    }
    (output / "system-trials.jsonl").write_text(
        json.dumps(record) + "\n", encoding="utf-8"
    )
    return output, plan, environment


def test_stage_c_resume_accepts_only_a_valid_completed_prefix(tmp_path):
    output, plan, environment = _resume_fixture(tmp_path)

    records = _load_resume_prefix(
        output,
        plan,
        experiment_id="fixture-stage-c",
        environment=environment,
        root=tmp_path,
    )

    assert [record["trial_id"] for record in records] == ["trial-0"]
    interrupted = output / "runs" / "trial-1"
    interrupted.mkdir()
    next_dir, preserved = _next_attempt_directory(output, "trial-1")
    assert next_dir.name == "trial-1--attempt-02"
    assert preserved == ["trial-1"]


@pytest.mark.parametrize("defect", ["duplicate", "plan_mismatch", "missing_sidecar"])
def test_stage_c_resume_rejects_corrupt_or_nonprefix_evidence(tmp_path, defect):
    output, plan, environment = _resume_fixture(tmp_path)
    trials = output / "system-trials.jsonl"
    if defect == "duplicate":
        trials.write_text(trials.read_text(encoding="utf-8") * 2, encoding="utf-8")
    elif defect == "plan_mismatch":
        plan[0] = {**plan[0], "trial_id": "different-trial"}
    else:
        (output / "runs" / "trial-0" / "preview.json").unlink()

    with pytest.raises(RuntimeError, match="duplicate|prefix|missing sidecar"):
        _load_resume_prefix(
            output,
            plan,
            experiment_id="fixture-stage-c",
            environment=environment,
            root=tmp_path,
        )


def test_offline_runner_and_analyzer_generate_auditable_outputs(tmp_path):
    prediction_dir = tmp_path / "predictions"
    run_args = argparse.Namespace(
        dataset=DEFAULT_DATASET,
        recommenders="static_small,static_large,rule_based_intent_only,rule_based_context",
        split="test",
        repeats=1,
        seed=20260808,
        output=prediction_dir,
        dry_run=False,
    )
    manifest = run(run_args)

    assert manifest["records"] == 192
    assert manifest["errors"] == 0
    assert (prediction_dir / "predictions.jsonl").is_file()
    assert (prediction_dir / "run-manifest.json").is_file()

    analysis_dir = tmp_path / "analysis"
    analysis_args = argparse.Namespace(
        dataset=DEFAULT_DATASET,
        predictions=prediction_dir / "predictions.jsonl",
        system_trials=None,
        user_events=None,
        reprovision_trials=None,
        bootstrap_replicates=50,
        seed=20260808,
        out=analysis_dir,
    )
    analysis_manifest = analyze(analysis_args)

    assert analysis_manifest["record_counts"]["predictions"] == 192
    assert "RQ1" in analysis_manifest["claim_gates"]
    assert "RQ5" in analysis_manifest["claim_gates"]
    assert analysis_manifest["secondary_evidence_dimensions"] == {
        "system_effectiveness_observed": False,
        "user_acceptance_observed": False,
        "reprovisioning_observed": False,
    }
    expected = {
        "recommendation-summary.csv",
        "recommendation-breakdowns.csv",
        "pairwise-mcnemar-holm.csv",
        "pairwise-wilcoxon-holm.csv",
            "pairwise-sample-mcnemar-holm.csv",
            "pairwise-sample-wilcoxon-holm.csv",
            "pairwise-raw-llm-mcnemar-holm.csv",
            "pairwise-raw-llm-wilcoxon-holm.csv",
            "raw-llm-effect-sizes.csv",
        "pairwise-trial-mcnemar-descriptive.csv",
        "pairwise-trial-wilcoxon-descriptive.csv",
        "effect-sizes.csv",
        "latency-cost-summary.csv",
        "family-robustness.csv",
        "repeat-consistency.csv",
        "system-effectiveness.csv",
        "system-paired-binary.csv",
        "system-paired-continuous.csv",
        "system-family-paired.csv",
        "user-acceptance.csv",
        "user-paired-acceptance.csv",
        "reprovisioning-effectiveness.csv",
        "profile-confusion-matrices.json",
        "analysis.json",
        "analysis-manifest.json",
        "REPORT.md",
        "SHA256SUMS",
    }
    assert expected == {path.name for path in analysis_dir.iterdir()}



def _system_record(evidence_class: str, trial_id: str, *, oom: bool = False) -> dict:
    return {
        "schema_version": SYSTEM_SCHEMA,
        "evidence_class": evidence_class,
        "trial_id": trial_id,
        "experiment_id": "fixture-system",
        "timestamp_utc": "2026-08-08T00:00:00Z",
        "git_commit": "a" * 40,
        "environment_id": "disposable-fixture",
        "recommender": "rule_based_context",
        "sample_id": "small-csv-canonical-en",
        "workload_family": "small-csv",
        "repeat_index": 0,
        "applied_profile": "small",
        "applied_image_id": "scipy-data-science",
        "cpu_request_m": 100,
        "memory_request_mib": 244,
        "cpu_usage_mean_m": 50,
        "memory_usage_mean_mib": 122,
        "memory_usage_peak_mib": 180,
        "measurement_window_seconds": 30,
        "measurement_source": "fixture-cgroup-v2",
        "pod_ready": not oom,
        "pending_failure": False,
        "pending_duration_seconds": 1.0,
        "oom_killed": oom,
        "image_pull_failure": False,
        "workload_success": not oom,
        "time_to_ready_seconds": 2.0 if not oom else None,
        "workload_duration_seconds": 30.0,
        "cleanup_status": "completed",
        "supporting_evidence_paths": ["fixture/pod.json"] if evidence_class == "observed" else [],
    }


def test_system_metrics_keep_observed_and_simulated_separate():
    records = [
        validate_system_trial(_system_record("observed", "observed-1")),
        validate_system_trial(_system_record("simulated", "simulated-1", oom=True)),
    ]

    rows = analyze_system_trials(records, load_dataset(), replicates=10, seed=1)

    assert len(rows) == 2
    by_class = {row["evidence_class"]: row for row in rows}
    assert by_class["observed"]["cpu_request_utilization_mean"] == 0.5
    assert by_class["observed"]["memory_request_utilization_mean"] == 0.5
    assert by_class["observed"]["oom_killed_rate"] == 0.0
    assert by_class["simulated"]["oom_killed_rate"] == 1.0


def test_system_comparisons_are_paired_within_evidence_class():
    first = validate_system_trial(_system_record("observed", "first"))
    second_raw = _system_record("observed", "second", oom=True)
    second_raw["recommender"] = "static_small"
    second = validate_system_trial(second_raw)

    comparisons = compare_system_trials(
        [first, second], load_dataset(), replicates=10, seed=2
    )

    oom = next(row for row in comparisons["binary"] if row["endpoint"] == "oom_killed")
    assert oom["pairs"] == 1
    assert oom["discordant_pairs"] == 1
    assert oom["p_value_holm"] == 1.0
    family_oom = next(
        row for row in comparisons["family"] if row["endpoint"] == "oom_killed"
    )
    assert family_oom["inference_unit"] == "workload_family_repeat_mean"
    assert family_oom["paired_families"] == 1
    assert family_oom["p_value_raw"] == 1.0


def test_observed_system_trial_requires_evidence_path():
    record = _system_record("observed", "missing-evidence")
    record["supporting_evidence_paths"] = []

    with pytest.raises(ValueError, match="supporting evidence"):
        validate_system_trial(record)


def test_user_acceptance_uses_decided_denominator_and_evidence_class():
    base = {
        "schema_version": USER_SCHEMA,
        "evidence_class": "observed",
        "study_id": "fixture-study",
        "timestamp_utc": "2026-08-08T00:00:00Z",
        "participant_block_id": "participant-block-01",
        "recommender": "rule_based_context",
        "sample_id": "small-csv-canonical-en",
        "workload_family": "small-csv",
        "recommended_profile": "small",
        "recommended_image_id": "scipy-data-science",
        "applied_profile": "small",
        "applied_image_id": "scipy-data-science",
        "explanation_seen": True,
        "decision_time_seconds": 3.0,
        "task_success": True,
        "consent_version": "consent-v1",
    }
    actions = []
    for index, action in enumerate(("accept", "override", "cancel")):
        record = {**base, "event_id": f"event-{index}", "session_index": index, "action": action}
        if action == "cancel":
            record["applied_profile"] = None
            record["applied_image_id"] = None
        actions.append(record)

    summary = analyze_user_events(actions, load_dataset())[0]

    assert summary["acceptance_rate_decided"] == 0.5
    assert summary["acceptance_rate_all_exposures"] == 0.333333
    assert summary["cancel_rate"] == 0.333333


def test_user_acceptance_comparison_pairs_participant_and_task():
    base = {
        "schema_version": USER_SCHEMA,
        "evidence_class": "observed",
        "study_id": "fixture-study",
        "timestamp_utc": "2026-08-08T00:00:00Z",
        "participant_block_id": "participant-block-01",
        "session_index": 0,
        "sample_id": "small-csv-canonical-en",
        "workload_family": "small-csv",
        "recommended_profile": "small",
        "recommended_image_id": "scipy-data-science",
        "applied_profile": "small",
        "applied_image_id": "scipy-data-science",
        "explanation_seen": True,
        "decision_time_seconds": 3.0,
        "task_success": True,
        "consent_version": "consent-v1",
    }
    records = [
        {**base, "event_id": "event-a", "recommender": "rule_based_context", "action": "accept"},
        {**base, "event_id": "event-b", "recommender": "static_small", "action": "override"},
    ]

    comparison = compare_user_acceptance(records, load_dataset())[0]

    assert comparison["participant_task_pairs"] == 1
    assert comparison["discordant_pairs"] == 1
    assert comparison["p_value_holm"] == 1.0


def test_reprovision_success_requires_ready_pvc_and_workload_resume():
    base = {
        "schema_version": REPROVISION_SCHEMA,
        "evidence_class": "observed",
        "experiment_id": "fixture-reprovision",
        "timestamp_utc": "2026-08-08T00:00:00Z",
        "git_commit": "a" * 40,
        "environment_id": "disposable-fixture",
        "recommender": "rule_based_context",
        "sample_id": "large-aggregation-canonical-en",
        "workload_family": "large-aggregation",
        "from_profile": "small",
        "to_profile": "large",
        "from_image_id": "minimal-python",
        "to_image_id": "scipy-data-science",
        "outcome": "completed",
        "replacement_ready": True,
        "pvc_continuity_verified": True,
        "workload_resume_verified": True,
        "pending_failure": False,
        "oom_killed": False,
        "downtime_seconds": 12.0,
        "rollback_attempted": False,
        "rollback_successful": False,
        "cleanup_status": "completed",
        "supporting_evidence_paths": ["fixture/reprovision.json"],
    }
    records = [
        {**base, "trial_id": "success", "repeat_index": 0},
        {
            **base,
            "trial_id": "missing-pvc",
            "repeat_index": 1,
            "pvc_continuity_verified": False,
        },
    ]

    summary = analyze_reprovision_trials(records, load_dataset())[0]

    assert summary["success_rate"] == 0.5
    assert summary["replacement_ready_rate"] == 1.0
    assert summary["pvc_continuity_rate"] == 0.5


def test_exact_paired_test_and_holm_adjustment_are_deterministic():
    result = exact_mcnemar(
        [True, True, False, False],
        [True, False, True, True],
    )

    assert result["first_only_correct"] == 1
    assert result["second_only_correct"] == 2
    assert result["p_value_raw"] == 1.0
    assert holm_adjust([0.01, 0.04, 0.2]) == pytest.approx([0.03, 0.08, 0.2])


def test_small_p_values_are_never_rounded_to_statistical_zero():
    assert _round_p(2 / 2**30) == pytest.approx(1.86264514923e-09)
    assert _round_p(0.0) == 0.0
