from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from benchmarks.workload_runner import EXIT_USAGE, SCALES, WORKLOAD_PLANS, main, run_workload
from recommender.recommender import recommend_profile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "benchmarks" / "workloads.yaml"
VALID_PROFILES = {"small", "medium", "large", "gpu_or_large"}
REQUIRED_CATEGORIES = {
    "light",
    "data_processing",
    "machine_learning",
    "boundary",
    "conflicting_signal",
    "policy",
}


def load_manifest() -> dict:
    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_manifest_schema_and_required_fields():
    manifest = load_manifest()

    assert manifest["schema_version"] == "1.0"
    assert set(manifest["profile_labels"]) == VALID_PROFILES
    assert isinstance(manifest["workloads"], list)
    assert 10 <= len(manifest["workloads"]) <= 12

    required_workload_fields = {
        "workload_id",
        "category",
        "description",
        "intent",
        "dataset_size_hint_gb",
        "code_context_hints",
        "deterministic_seed",
        "workload",
        "timeout_seconds",
        "expected_acceptable_profiles",
        "expected_recommender_profiles",
        "expected_classification_reason",
        "expected_pressure_type",
        "data_source",
        "license",
    }
    required_workload_command_fields = {"script", "operation", "scale", "command"}

    for workload in manifest["workloads"]:
        assert required_workload_fields <= workload.keys()
        assert required_workload_command_fields <= workload["workload"].keys()
        assert isinstance(workload["code_context_hints"], list)
        assert workload["code_context_hints"]
        assert isinstance(workload["expected_acceptable_profiles"], list)
        assert workload["expected_acceptable_profiles"]
        assert isinstance(workload["expected_recommender_profiles"], list)
        assert workload["expected_recommender_profiles"]
        assert workload["data_source"]["type"] == "synthetic"
        assert "Synthetic data" in workload["license"]


def test_workload_ids_are_unique_and_match_runner_plans():
    workloads = load_manifest()["workloads"]
    workload_ids = [workload["workload_id"] for workload in workloads]

    assert len(workload_ids) == len(set(workload_ids))
    assert set(workload_ids) == set(WORKLOAD_PLANS)


def test_thresholds_and_scale_values_are_valid():
    manifest = load_manifest()
    dataset_thresholds = manifest["thresholds"]["dataset_size_gb"]
    score_thresholds = manifest["thresholds"]["recommender_score"]

    assert dataset_thresholds["medium_signal_min"] == 0.5
    assert dataset_thresholds["large_signal_min"] == 2.0
    assert dataset_thresholds["medium_signal_min"] < dataset_thresholds["large_signal_min"]
    assert score_thresholds["medium_min"] == 1
    assert score_thresholds["large_min"] == 3
    assert score_thresholds["medium_min"] < score_thresholds["large_min"]
    assert set(manifest["thresholds"]["scale_values"]) == set(SCALES)

    by_id = {workload["workload_id"]: workload for workload in manifest["workloads"]}
    assert by_id["boundary_below_0_5_ambiguous"]["dataset_size_hint_gb"] == pytest.approx(0.49)
    assert by_id["boundary_above_0_5_conflicting"]["dataset_size_hint_gb"] == pytest.approx(0.51)

    for workload in manifest["workloads"]:
        assert workload["dataset_size_hint_gb"] >= 0
        assert workload["workload"]["scale"] in SCALES


def test_all_referenced_scripts_exist_and_commands_are_well_formed():
    for workload in load_manifest()["workloads"]:
        script_path = ROOT / workload["workload"]["script"]
        command = workload["workload"]["command"]

        assert script_path.exists()
        assert command[:3] == ["python3", "-m", "benchmarks.workload_runner"]
        assert "--workload-id" in command
        assert workload["workload_id"] in command
        assert command[command.index("--scale") + 1] == workload["workload"]["scale"]
        assert "--seed" in command
        assert str(workload["deterministic_seed"]) in command


def test_expected_profile_labels_are_valid():
    for workload in load_manifest()["workloads"]:
        assert set(workload["expected_acceptable_profiles"]) <= VALID_PROFILES
        assert set(workload["expected_recommender_profiles"]) <= VALID_PROFILES
        allowed = workload.get("policy_constraints", {}).get("allowed_profiles")
        disallowed = workload.get("policy_constraints", {}).get("disallowed_profiles")
        if allowed:
            assert set(allowed) <= VALID_PROFILES
        if disallowed:
            assert set(disallowed) <= VALID_PROFILES


def test_manifest_recommender_expectations_match_current_rules():
    for workload in load_manifest()["workloads"]:
        rec = recommend_profile(
            workload["intent"],
            workload["dataset_size_hint_gb"],
            "\n".join(workload["code_context_hints"]),
        )

        assert rec.profile in workload["expected_recommender_profiles"]


def test_each_required_benchmark_category_is_represented():
    categories = {workload["category"] for workload in load_manifest()["workloads"]}
    assert REQUIRED_CATEGORIES <= categories


def test_required_coverage_tags_are_represented():
    tags = {
        tag
        for workload in load_manifest()["workloads"]
        for tag in workload.get("coverage_tags", [])
    }

    assert {
        "light.basic_python",
        "light.small_csv_read",
        "light.visualization",
        "data_processing.pandas_read_transform",
        "data_processing.dataframe_join",
        "data_processing.increasing_dataset_sizes",
        "machine_learning.sklearn_fit_small",
        "machine_learning.sklearn_fit_medium",
        "machine_learning.sklearn_fit_larger_data",
        "boundary.below_0_5gb",
        "boundary.above_0_5gb",
        "boundary.ambiguous_incomplete_intent",
        "conflicting_signal.harmless_intent_strong_code_context",
        "conflicting_signal.strong_intent_weak_context",
        "conflicting_signal.misleading_dataset_size_hint",
        "policy.gpu_unavailable_or_disallowed",
        "policy.recommended_profile_outside_allowed_policy",
    } <= tags


def test_reproducible_data_generation_for_representative_workload():
    first = run_workload("data_dataframe_join_medium", "tiny", 2102)
    second = run_workload("data_dataframe_join_medium", "tiny", 2102)
    third = run_workload("data_dataframe_join_medium", "tiny", 2103)

    assert first["deterministic_digest"] == second["deterministic_digest"]
    assert first["result"] == second["result"]
    assert first["deterministic_digest"] != third["deterministic_digest"]
    assert isinstance(first["runtime"]["max_rss_bytes"], int)
    assert first["runtime"]["max_rss_bytes"] > 0
    assert "max_rss_platform_units" not in first["runtime"]


def test_metadata_output_is_immutable(tmp_path):
    output_path = tmp_path / "metadata.json"
    args = [
        "--workload-id",
        "light_basic_python",
        "--scale",
        "tiny",
        "--seed",
        "1101",
        "--metadata-out",
        str(output_path),
    ]

    assert main(args) == 0
    original = output_path.read_bytes()
    assert main(args) == EXIT_USAGE
    assert output_path.read_bytes() == original
