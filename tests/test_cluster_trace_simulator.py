#!/usr/bin/env python3
"""Unit tests for evaluation_v5/analysis/cluster_trace_simulator.py."""

from __future__ import annotations

import json
from pathlib import Path
import random
import tempfile
import pytest

from evaluation_v5.analysis.cluster_trace_simulator import (
    PROFILE_CATALOG,
    ClusterSimulator,
    PodInstance,
    TraceJob,
    bootstrap_ci_95,
    load_e1_confusion_matrices,
    parse_b0_mix,
    promote_profile,
    scale_trace_demands,
    select_profile_for_job,
)


def create_synthetic_jobs() -> list[TraceJob]:
    """Create deterministic jobs spanning small, medium, and large demands."""
    return [
        TraceJob(
            job_id="job_small",
            arrival_time_s=0.0,
            duration_s=100.0,
            raw_peak_mem_mib=200.0,
            actual_peak_mem_mib=150.0,  # <= 384 MiB (small)
            actual_peak_cpu_m=80.0,
            is_gpu=False,
            gold_profile="small",
        ),
        TraceJob(
            job_id="job_med",
            arrival_time_s=10.0,
            duration_s=200.0,
            raw_peak_mem_mib=600.0,
            actual_peak_mem_mib=500.0,  # > 384 and <= 1024 MiB (medium)
            actual_peak_cpu_m=400.0,
            is_gpu=False,
            gold_profile="medium",
        ),
        TraceJob(
            job_id="job_large",
            arrival_time_s=20.0,
            duration_s=300.0,
            raw_peak_mem_mib=1800.0,
            actual_peak_mem_mib=1500.0,  # > 1024 and <= 2048 MiB (large)
            actual_peak_cpu_m=1200.0,
            is_gpu=True,
            gold_profile="large",
        ),
    ]


def test_reproducibility_seed() -> None:
    """Test invariant: Two simulation runs with the same seed yield identical outputs."""
    jobs = create_synthetic_jobs()
    sim1 = ClusterSimulator(num_nodes=5, cores_per_node=4, mem_gb_per_node=8.0, total_gpus=5)
    sim2 = ClusterSimulator(num_nodes=5, cores_per_node=4, mem_gb_per_node=8.0, total_gpus=5)

    conf_p1 = {"small": {"small": 1.0}, "medium": {"medium": 1.0}, "large": {"large": 1.0}}
    conf_p2 = {"small": {"small": 0.8, "medium": 0.2}, "medium": {"medium": 0.9, "large": 0.1}, "large": {"large": 1.0}}

    outcome1 = sim1.run_simulation(
        jobs=jobs,
        policy="P2",
        policy_label="P2",
        seed=12345,
        confusion_p1=conf_p1,
        confusion_p2=conf_p2,
    )

    outcome2 = sim2.run_simulation(
        jobs=jobs,
        policy="P2",
        policy_label="P2",
        seed=12345,
        confusion_p1=conf_p1,
        confusion_p2=conf_p2,
    )

    assert outcome1.oom_events == outcome2.oom_events
    assert outcome1.retry_events == outcome2.retry_events
    assert outcome1.queue_wait_p50_s == outcome2.queue_wait_p50_s
    assert outcome1.ram_slack_pct == outcome2.ram_slack_pct
    assert outcome1.max_concurrency == outcome2.max_concurrency


def test_resource_conservation() -> None:
    """Test invariant: Node allocated resources never exceed node total capacity."""
    jobs = create_synthetic_jobs() * 10  # 30 concurrent jobs on a small cluster
    sim = ClusterSimulator(num_nodes=2, cores_per_node=4, mem_gb_per_node=4.0, total_gpus=2)

    conf = {"small": {"small": 1.0}, "medium": {"medium": 1.0}, "large": {"large": 1.0}}
    outcome = sim.run_simulation(
        jobs=jobs,
        policy="ORACLE",
        policy_label="ORACLE",
        seed=42,
        confusion_p1=conf,
        confusion_p2=conf,
    )

    # Invariants are checked via assert statements in run_simulation during execution
    # After simulation completes, all resources must be fully restored to 100% available
    for node in sim.nodes:
        assert node.avail_cpu_m == node.total_cpu_m
        assert node.avail_ram_mib == node.total_ram_mib
        assert node.avail_gpu == node.total_gpu
    assert outcome.completed_jobs == len(jobs)


def test_oom_trigger_and_promotion() -> None:
    """Test invariant: When limit < peak, OOM triggers midway and promotes profile."""
    # Job needs 500 MiB (medium), but policy assigns small (384 MiB limit)
    job = TraceJob(
        job_id="oom_test_job",
        arrival_time_s=0.0,
        duration_s=100.0,
        raw_peak_mem_mib=600.0,
        actual_peak_mem_mib=500.0,
        actual_peak_cpu_m=200.0,
        is_gpu=False,
        gold_profile="medium",
    )

    sim = ClusterSimulator(num_nodes=2, cores_per_node=4, mem_gb_per_node=8.0, total_gpus=2)

    # Force policy to assign small initially
    b0_all_small = {"small": 1.0, "medium": 0.0, "large": 0.0}

    outcome = sim.run_simulation(
        jobs=[job],
        policy="B0_forced_small",
        policy_label="B0 Small",
        seed=42,
        confusion_p1={},
        confusion_p2={},
        b0_distribution=b0_all_small,
    )

    # 1 OOM event should occur, pod dies at 50s, retries as medium and succeeds at 150s
    assert outcome.oom_events == 1
    assert outcome.retry_events == 1
    assert outcome.oom_rate_pct == 100.0
    assert outcome.wasted_ram_gb_hours > 0.0


def test_oracle_never_oom() -> None:
    """Test invariant: ORACLE presciently knows peak demand and NEVER triggers OOM."""
    jobs = create_synthetic_jobs() * 20  # 60 varied jobs
    sim = ClusterSimulator(num_nodes=5, cores_per_node=8, mem_gb_per_node=16.0, total_gpus=10)

    outcome = sim.run_simulation(
        jobs=jobs,
        policy="ORACLE",
        policy_label="oracle, not P2",
        seed=999,
        confusion_p1={},
        confusion_p2={},
    )

    assert outcome.oom_events == 0
    assert outcome.retry_events == 0
    assert outcome.oom_rate_pct == 0.0
    assert outcome.wasted_core_hours == 0.0
    assert outcome.wasted_ram_gb_hours == 0.0


def test_profile_promotion_cascade() -> None:
    assert promote_profile("small") == "medium"
    assert promote_profile("medium") == "large"
    assert promote_profile("large") == "large"


def test_parse_b0_mix() -> None:
    mix = parse_b0_mix("0.2:0.3:0.5")
    assert pytest.approx(mix["small"]) == 0.2
    assert pytest.approx(mix["medium"]) == 0.3
    assert pytest.approx(mix["large"]) == 0.5


def test_demand_scaling() -> None:
    raw_records = [
        {"job_id": "j1", "arrival_time_s": 0.0, "duration_seconds": 10.0, "actual_peak_mem_mib": 1000.0},
        {"job_id": "j2", "arrival_time_s": 5.0, "duration_seconds": 20.0, "actual_peak_mem_mib": 50000.0},
    ]
    profiler_peaks = [50.0, 150.0, 300.0, 800.0, 1600.0]

    # Quantile mapping test
    scaled = scale_trace_demands(raw_records, profiler_peaks, scaling_rule="quantile_map")
    assert len(scaled) == 2
    assert 50.0 <= scaled[0].actual_peak_mem_mib <= 1600.0
    assert 50.0 <= scaled[1].actual_peak_mem_mib <= 1600.0
    assert scaled[0].actual_peak_mem_mib < scaled[1].actual_peak_mem_mib


def test_bootstrap_ci_95() -> None:
    vals = [10.0, 10.5, 9.5, 10.2, 9.8, 10.1]
    ci_low, ci_high = bootstrap_ci_95(vals, n_bootstrap=500, seed=42)
    assert ci_low <= sum(vals) / len(vals) <= ci_high
