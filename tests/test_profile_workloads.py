"""Unit tests for workload resource profiler using FakeWorkloadRunner."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from recommender.candidate_corpus import DEFAULT_PROFILE_DEFINITIONS
from scripts.datasets.profile_workloads import (
    FakeWorkloadRunner,
    ProfileSpec,
    TrialResult,
    generate_pilot_markdown_report,
    get_sorted_profiles,
    profile_single_problem,
    run_pipeline,
)


@pytest.fixture
def test_profiles():
    return get_sorted_profiles()


def test_sorted_profiles_order(test_profiles):
    """Profiles must be strictly ascending by memory limit bytes."""
    assert len(test_profiles) == 3
    slugs = [p.slug for p in test_profiles]
    assert slugs == ["small", "medium", "large"]
    assert test_profiles[0].mem_limit_bytes < test_profiles[1].mem_limit_bytes < test_profiles[2].mem_limit_bytes
    assert test_profiles[0].cpu_limit < test_profiles[1].cpu_limit < test_profiles[2].cpu_limit


def test_classify_oom_advances_profile_to_completed(test_profiles):
    """When small OOMs, the runner should advance to medium and stop on completed."""
    problem_id = "test_oom_advances"
    behavior = {
        (f"{problem_id}-r1", "small"): TrialResult(
            profile_tried="small",
            outcome="oom",
            peak_mib=384.0,
            cpu_seconds=0.2,
            wall_seconds=0.5,
            peak_method="memory.peak",
            oom_kill_count=1,
        ),
        (f"{problem_id}-r1", "medium"): TrialResult(
            profile_tried="medium",
            outcome="completed",
            peak_mib=512.0,
            cpu_seconds=0.8,
            wall_seconds=1.2,
            peak_method="memory.peak",
        ),
    }
    runner = FakeWorkloadRunner(behavior_map=behavior)

    res = profile_single_problem(
        problem_id=problem_id,
        source="ds1000",
        image_id="scipy-data-science",
        image_reference="quay.io/jupyter/scipy-notebook@sha256:1a91a693c8cb086f3607f2ed38a2743ecd53dd1dac2d3e84e6cd647b33fd2bba",
        code_to_run="print('hello')",
        input_files=None,
        runner=runner,
        profiles=test_profiles,
        repeats=1,
        baseline_median=200.0,
    )

    assert res["status"] == "ok"
    assert res["min_passing_profile_per_repeat"] == ["medium"]
    assert res["final_min_passing_profile"] == "medium"
    assert len(res["repeats"]) == 2
    assert res["repeats"][0]["profile_tried"] == "small"
    assert res["repeats"][0]["outcome"] == "oom"
    assert res["repeats"][1]["profile_tried"] == "medium"
    assert res["repeats"][1]["outcome"] == "completed"
    # Never tried large because medium completed
    assert not any(r["profile_tried"] == "large" for r in res["repeats"])


def test_classify_error_does_not_advance_profile(test_profiles):
    """When small fails with code error (non-OOM), it MUST NOT advance to larger profiles."""
    problem_id = "test_syntax_error"
    behavior = {
        (f"{problem_id}-r1", "small"): TrialResult(
            profile_tried="small",
            outcome="error",
            peak_mib=120.0,
            cpu_seconds=0.1,
            wall_seconds=0.2,
            peak_method="memory.peak",
            error_traceback="SyntaxError: invalid syntax",
        ),
    }
    runner = FakeWorkloadRunner(behavior_map=behavior)

    res = profile_single_problem(
        problem_id=problem_id,
        source="ds1000",
        image_id="scipy-data-science",
        image_reference="quay.io/jupyter/scipy-notebook@sha256:1a91a693c8cb086f3607f2ed38a2743ecd53dd1dac2d3e84e6cd647b33fd2bba",
        code_to_run="invalid python code !!!",
        input_files=None,
        runner=runner,
        profiles=test_profiles,
        repeats=1,
        baseline_median=100.0,
    )

    assert res["status"] == "error"
    assert res["min_passing_profile_per_repeat"] == []
    assert res["final_min_passing_profile"] is None
    # Must only have tried small, not medium or large
    assert len(res["repeats"]) == 1
    assert res["repeats"][0]["profile_tried"] == "small"
    assert res["repeats"][0]["outcome"] == "error"


def test_infeasible_over_capacity_when_all_profiles_oom(test_profiles):
    """When all profiles (small, medium, large) OOM, status must be infeasible_over_capacity."""
    problem_id = "test_huge_workload"
    behavior = {
        (f"{problem_id}-r1", "small"): TrialResult(
            profile_tried="small", outcome="oom", peak_mib=384.0, cpu_seconds=0.1, wall_seconds=0.2, peak_method="memory.peak"
        ),
        (f"{problem_id}-r1", "medium"): TrialResult(
            profile_tried="medium", outcome="oom", peak_mib=1024.0, cpu_seconds=0.2, wall_seconds=0.4, peak_method="memory.peak"
        ),
        (f"{problem_id}-r1", "large"): TrialResult(
            profile_tried="large", outcome="oom", peak_mib=2048.0, cpu_seconds=0.3, wall_seconds=0.6, peak_method="memory.peak"
        ),
    }
    runner = FakeWorkloadRunner(behavior_map=behavior)

    res = profile_single_problem(
        problem_id=problem_id,
        source="ds1000",
        image_id="scipy-data-science",
        image_reference="quay.io/jupyter/scipy-notebook@sha256:1a91a693c8cb086f3607f2ed38a2743ecd53dd1dac2d3e84e6cd647b33fd2bba",
        code_to_run="allocate_10gb()",
        input_files=None,
        runner=runner,
        profiles=test_profiles,
        repeats=1,
        baseline_median=150.0,
    )

    assert res["status"] == "infeasible_over_capacity"
    assert res["final_min_passing_profile"] is None
    assert len(res["repeats"]) == 3


def test_final_min_passing_is_maximum_across_repeats(test_profiles):
    """Final min passing profile must be the LARGEST passing profile among repeats."""
    problem_id = "test_multi_repeat_variance"
    # Repeat 1: small passes
    # Repeat 2: small OOMs, medium passes
    # Repeat 3: small passes
    # Expect: final = medium (max(small, medium, small))
    behavior = {
        (f"{problem_id}-r1", "small"): TrialResult(
            profile_tried="small", outcome="completed", peak_mib=300.0, cpu_seconds=0.5, wall_seconds=1.0, peak_method="memory.peak"
        ),
        (f"{problem_id}-r2", "small"): TrialResult(
            profile_tried="small", outcome="oom", peak_mib=384.0, cpu_seconds=0.5, wall_seconds=1.0, peak_method="memory.peak"
        ),
        (f"{problem_id}-r2", "medium"): TrialResult(
            profile_tried="medium", outcome="completed", peak_mib=420.0, cpu_seconds=0.5, wall_seconds=1.0, peak_method="memory.peak"
        ),
        (f"{problem_id}-r3", "small"): TrialResult(
            profile_tried="small", outcome="completed", peak_mib=310.0, cpu_seconds=0.5, wall_seconds=1.0, peak_method="memory.peak"
        ),
    }
    runner = FakeWorkloadRunner(behavior_map=behavior)

    res = profile_single_problem(
        problem_id=problem_id,
        source="ds1000",
        image_id="scipy-data-science",
        image_reference="quay.io/jupyter/scipy-notebook@sha256:1a91a693c8cb086f3607f2ed38a2743ecd53dd1dac2d3e84e6cd647b33fd2bba",
        code_to_run="do_work()",
        input_files=None,
        runner=runner,
        profiles=test_profiles,
        repeats=3,
        baseline_median=250.0,
    )

    assert res["status"] == "ok"
    assert res["min_passing_profile_per_repeat"] == ["small", "medium", "small"]
    assert res["final_min_passing_profile"] == "medium"
    assert res["peak_mib_max"] == 420.0
    # 420.0 >= 1.10 * 250 (275) -> import_dominated is False
    assert res["import_dominated"] is False


def test_import_dominated_flag(test_profiles):
    """When peak_mib_max < 1.10 * baseline, import_dominated must be True."""
    problem_id = "test_light_workload"
    # peak is 105 MiB, baseline is 100 MiB -> 105 < 1.10 * 100 (110) -> True
    behavior = {
        (f"{problem_id}-r1", "small"): TrialResult(
            profile_tried="small", outcome="completed", peak_mib=105.0, cpu_seconds=0.1, wall_seconds=0.2, peak_method="memory.peak"
        )
    }
    runner = FakeWorkloadRunner(behavior_map=behavior)

    res = profile_single_problem(
        problem_id=problem_id,
        source="ds1000",
        image_id="scipy-data-science",
        image_reference="quay.io/jupyter/scipy-notebook@sha256:1a91a693c8cb086f3607f2ed38a2743ecd53dd1dac2d3e84e6cd647b33fd2bba",
        code_to_run="x = 1 + 1",
        input_files=None,
        runner=runner,
        profiles=test_profiles,
        repeats=1,
        baseline_median=100.0,
    )

    assert res["status"] == "ok"
    assert res["import_dominated"] is True


def test_resume_pipeline(tmp_path, test_profiles):
    """Resume flag should skip already completed problem_ids and append new results."""
    input_file = tmp_path / "input.jsonl"
    output_file = tmp_path / "output.jsonl"

    problems = [
        {"problem_id": "P_1", "image": "scipy-data-science", "reference_code": "a = 1"},
        {"problem_id": "P_2", "image": "scipy-data-science", "reference_code": "b = 2"},
        {"problem_id": "P_3", "image": "scipy-data-science", "reference_code": "c = 3"},
    ]
    with open(input_file, "w", encoding="utf-8") as f:
        for p in problems:
            f.write(json.dumps(p) + "\n")

    # Seed output_file with P_1 already completed
    existing_p1 = {
        "problem_id": "P_1",
        "source": "ds1000",
        "image_id": "scipy-data-science",
        "repeats": [],
        "min_passing_profile_per_repeat": ["small"],
        "final_min_passing_profile": "small",
        "peak_mib_max": 150.0,
        "baseline_peak_mib_median": 100.0,
        "import_dominated": False,
        "status": "ok",
        "peak_method": "memory.peak",
        "runner_version": "profile-workloads-v1.0.0",
    }
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(existing_p1) + "\n")

    runner = FakeWorkloadRunner(default_baseline=100.0)

    # Run pipeline with resume=True
    records = run_pipeline(
        input_file=input_file,
        source="ds1000",
        output_file=output_file,
        repeats=1,
        resume=True,
        runner=runner,
    )

    # P_1 was skipped; runner should only have run P_2 and P_3
    run_problem_ids = {h["problem_id"].split("-")[0] for h in runner.history}
    assert "P_1" not in run_problem_ids
    assert "P_2" in run_problem_ids
    assert "P_3" in run_problem_ids

    # Output file must contain all 3 records
    with open(output_file, "r", encoding="utf-8") as f:
        lines = [json.loads(l) for l in f if l.strip()]

    assert len(lines) == 3
    assert [l["problem_id"] for l in lines] == ["P_1", "P_2", "P_3"]


def test_pilot_report_generation(tmp_path):
    """Pilot report must generate valid markdown table with distributions."""
    records = [
        {"problem_id": "P1", "image_id": "img1", "final_min_passing_profile": "small", "status": "ok", "import_dominated": True},
        {"problem_id": "P2", "image_id": "img1", "final_min_passing_profile": "medium", "status": "ok", "import_dominated": False},
        {"problem_id": "P3", "image_id": "img2", "final_min_passing_profile": None, "status": "infeasible_over_capacity", "import_dominated": False},
    ]
    report_file = tmp_path / "report.md"
    content = generate_pilot_markdown_report(source="ds1000", records=records, output_path=report_file)

    assert report_file.is_file()
    assert "# Profile Pilot Workload Report — `ds1000`" in content
    assert "small" in content
    assert "medium" in content
    assert "infeasible_over_capacity" in content
    assert "Import-dominated workloads (`peak < 1.10 × baseline`):** 1 / 3 (33.33%)" in content


def test_classify_timeout(test_profiles):
    """When a workload times out, status must be timeout and no advancement to larger profiles."""
    problem_id = "test_infinite_loop"
    behavior = {
        (f"{problem_id}-r1", "small"): TrialResult(
            profile_tried="small",
            outcome="timeout",
            peak_mib=None,
            cpu_seconds=None,
            wall_seconds=120.0,
            peak_method="memory.peak",
            error_traceback="Container execution timed out",
        ),
    }
    runner = FakeWorkloadRunner(behavior_map=behavior)

    res = profile_single_problem(
        problem_id=problem_id,
        source="ds1000",
        image_id="scipy-data-science",
        image_reference="quay.io/jupyter/scipy-notebook@sha256:1a91a693c8cb086f3607f2ed38a2743ecd53dd1dac2d3e84e6cd647b33fd2bba",
        code_to_run="while True: pass",
        input_files=None,
        runner=runner,
        profiles=test_profiles,
        repeats=1,
        baseline_median=100.0,
    )

    assert res["status"] == "timeout"
    assert res["final_min_passing_profile"] is None
    assert len(res["repeats"]) == 1
    assert res["repeats"][0]["outcome"] == "timeout"


def test_arcade_source_preparation():
    """Verify ARCADE problem preparation concatenates setup cells and target cell with input files."""
    from scripts.datasets.profile_workloads import prepare_problem_execution
    from recommender.rule_based import load_image_catalog, DEFAULT_CATALOG_PATH

    catalog = load_image_catalog(DEFAULT_CATALOG_PATH)
    item = {
        "problem_id": "arcade_task_42",
        "image_id": "scipy-data-science",
        "input_files": {"data.csv": "col1,col2\n1,2\n"},
        "setup_cells": ["import pandas as pd", "df = pd.read_csv('data.csv')"],
        "target_cell": "ans = df['col1'].sum()",
    }
    pid, img_id, img_ref, code, in_files, imp_code = prepare_problem_execution("arcade", item, catalog)

    assert pid == "arcade_task_42"
    assert img_id == "scipy-data-science"
    assert "df = pd.read_csv('data.csv')" in code
    assert "ans = df['col1'].sum()" in code
    assert in_files == {"data.csv": "col1,col2\n1,2\n"}
    assert "import pandas as pd" in imp_code


def test_peak_method_fallback_recorded(test_profiles):
    """When peak_method is memory.current_sample_max, it must be recorded in the result."""
    problem_id = "test_fallback_peak"
    behavior = {
        (f"{problem_id}-r1", "small"): TrialResult(
            profile_tried="small",
            outcome="completed",
            peak_mib=180.5,
            cpu_seconds=0.4,
            wall_seconds=0.8,
            peak_method="memory.current_sample_max",
        )
    }
    runner = FakeWorkloadRunner(behavior_map=behavior)

    res = profile_single_problem(
        problem_id=problem_id,
        source="ds1000",
        image_id="scipy-data-science",
        image_reference="quay.io/jupyter/scipy-notebook@sha256:1a91a693c8cb086f3607f2ed38a2743ecd53dd1dac2d3e84e6cd647b33fd2bba",
        code_to_run="print('done')",
        input_files=None,
        runner=runner,
        profiles=test_profiles,
        repeats=1,
        baseline_median=120.0,
    )

    assert res["status"] == "ok"
    assert res["peak_method"] == "memory.current_sample_max"


def test_cli_main_execution(tmp_path):
    """CLI main function should parse args, run pipeline, and return 0."""
    from scripts.datasets.profile_workloads import main

    in_file = tmp_path / "cli_input.jsonl"
    out_file = tmp_path / "cli_output.jsonl"

    items = [{"problem_id": "P_CLI_1", "image": "scipy-data-science", "reference_code": "x = 1"}]
    in_file.write_text(json.dumps(items[0]) + "\n", encoding="utf-8")

    from unittest.mock import patch
    with patch("scripts.datasets.profile_workloads.DockerWorkloadRunner", return_value=FakeWorkloadRunner()):
        exit_code = main([
            "--input", str(in_file),
            "--source", "ds1000",
            "--output", str(out_file),
            "--repeats", "1",
            "--limit", "1",
            "--pilot",
        ])

    assert exit_code == 0
    assert out_file.is_file()


def test_baseline_by_profile_selection(test_profiles):
    """Ensure baseline is chosen matching final_min_passing_profile."""
    problem_id = "test_baseline_match"
    behavior = {
        (f"{problem_id}-r1", "small"): TrialResult(
            profile_tried="small",
            outcome="oom",
            peak_mib=384.0,
            cpu_seconds=0.2,
            wall_seconds=0.5,
            peak_method="memory.peak",
            oom_kill_count=1,
        ),
        (f"{problem_id}-r1", "medium"): TrialResult(
            profile_tried="medium",
            outcome="completed",
            peak_mib=500.0,
            cpu_seconds=0.8,
            wall_seconds=1.2,
            peak_method="memory.peak",
        ),
    }
    runner = FakeWorkloadRunner(behavior_map=behavior)
    baseline_map = {
        "small": 380.0,
        "medium": 480.0,
        "large": 600.0,
    }

    res = profile_single_problem(
        problem_id=problem_id,
        source="ds1000",
        image_id="pytorch-deep-learning",
        image_reference="dummy_ref",
        code_to_run="x = 1",
        input_files=None,
        runner=runner,
        profiles=test_profiles,
        repeats=1,
        baseline_by_profile=baseline_map,
    )

    assert res["status"] == "ok"
    assert res["final_min_passing_profile"] == "medium"
    assert res["baseline_profile"] == "medium"
    assert res["baseline_peak_mib_median"] == 480.0
    # 500.0 < 1.10 * 480.0 (= 528.0) -> True
    assert res["import_dominated"] is True


def test_real_docker_oom_escalation_medium():
    """Verify in real Docker container that a 600 MiB allocation OOMs on small and passes on medium."""
    import shutil
    if not shutil.which("docker"):
        pytest.skip("Docker CLI not available")

    from scripts.datasets.profile_workloads import DockerWorkloadRunner, get_sorted_profiles
    profiles = get_sorted_profiles()
    runner = DockerWorkloadRunner()
    image_ref = "quay.io/jupyter/scipy-notebook@sha256:1a91a693c8cb086f3607f2ed38a2743ecd53dd1dac2d3e84e6cd647b33fd2bba"

    code_600mb = """
import numpy as np
arr = np.ones((75_000_000,), dtype=np.int64)
s = arr.sum()
"""
    record = profile_single_problem(
        problem_id="Test_OOM_600MB",
        source="ds1000",
        image_id="scipy-data-science",
        image_reference=image_ref,
        code_to_run=code_600mb,
        input_files=None,
        runner=runner,
        profiles=profiles,
        repeats=1,
        timeout_seconds=30,
        baseline_median=191.0,
    )

    assert record["status"] == "ok"
    assert record["final_min_passing_profile"] == "medium"
    assert len(record["repeats"]) == 2
    assert record["repeats"][0]["profile_tried"] == "small"
    assert record["repeats"][0]["outcome"] == "oom"
    assert record["repeats"][1]["profile_tried"] == "medium"
    assert record["repeats"][1]["outcome"] == "completed"


def test_anon_peak_measurement(test_profiles):
    """Verify anon_peak_mib is recorded and anon_peak_mib_max is computed."""
    problem_id = "test_anon_peak"
    behavior = {
        (f"{problem_id}-r1", "small"): TrialResult(
            profile_tried="small",
            outcome="completed",
            peak_mib=350.0,
            anon_peak_mib=210.0,
            cpu_seconds=0.5,
            wall_seconds=1.0,
            peak_method="memory.peak",
        ),
    }
    runner = FakeWorkloadRunner(behavior_map=behavior)

    res = profile_single_problem(
        problem_id=problem_id,
        source="ds1000",
        image_id="scipy-data-science",
        image_reference="scipy:test",
        code_to_run="x = 1",
        input_files=None,
        runner=runner,
        profiles=test_profiles,
        repeats=1,
    )

    assert res["status"] == "ok"
    assert res["peak_mib_max"] == 350.0
    assert res["anon_peak_mib_max"] == 210.0
    assert res["repeats"][0]["anon_peak_mib"] == 210.0

