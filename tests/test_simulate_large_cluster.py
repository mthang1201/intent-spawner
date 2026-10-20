"""Tests for Large-Scale Cluster Multi-Tenancy Simulator."""

import json
from pathlib import Path
import pytest

from scripts.simulate_large_cluster import (
    ClusterConfig,
    KubernetesClusterSimulator,
    WorkloadProfile,
    apply_allocation_policy,
    generate_notebook_workload_trace,
    load_e4_workloads,
    render_simulation_figures,
)


def test_load_e4_workloads() -> None:
    """Verify that all 20 empirical E4 workload families are properly loaded."""
    workloads = load_e4_workloads()
    assert len(workloads) == 20, f"Expected 20 workload families, got {len(workloads)}"

    # Check key workloads from Stream E4
    assert "large_hash_map_allocation" in workloads
    assert "dense_array_allocation" in workloads
    assert "streaming_statistics" in workloads

    # Check empirical metrics integrity
    hash_map = workloads["large_hash_map_allocation"]
    assert hash_map.peak_mem_mib > 1500.0, "Hash map should have large peak memory footprint"
    assert hash_map.safe_mem_mib >= 2048

    dense_array = workloads["dense_array_allocation"]
    assert dense_array.peak_mem_mib > 1200.0


def test_cluster_config_capacity() -> None:
    """Verify target cluster capacity conforms to 1,000 vCPU, 2,000 GB RAM, 100 GPU."""
    cfg = ClusterConfig(
        num_nodes=50,
        cpu_per_node_milli=20_000,
        ram_per_node_mib=40_960,
        gpu_per_node=2,
    )
    assert cfg.num_nodes == 50
    assert cfg.total_cpu_vcpu == 1000.0
    assert cfg.total_ram_gb == 2000.0
    assert cfg.total_gpu == 100


def test_allocation_policies() -> None:
    """Test resource allocation mechanics for B0, P1, and P2."""
    workloads = load_e4_workloads()

    # Session with memory-heavy workload
    heavy_sess = next(
        iter(
            generate_notebook_workload_trace(
                workloads={"large_hash_map_allocation": workloads["large_hash_map_allocation"]},
                total_sessions=1,
                duration_hours=1.0,
                seed=42,
            )
        )
    )

    # B0 should assign fixed large resources (4 vCPU, 8 GB, 1 GPU)
    alloc_b0 = apply_allocation_policy(heavy_sess, "B0")
    assert alloc_b0.req_cpu_m == 4000
    assert alloc_b0.req_ram_mib == 8192
    assert alloc_b0.req_gpu == 1
    assert not alloc_b0.is_oom_vulnerable

    # P1 should assign Small profile due to lack of ML keywords, causing OOM
    alloc_p1 = apply_allocation_policy(heavy_sess, "P1")
    assert alloc_p1.req_ram_mib <= 512
    assert alloc_p1.is_oom_vulnerable is True

    # P2 should assign calibrated safe envelope, protecting against OOM
    alloc_p2 = apply_allocation_policy(heavy_sess, "P2")
    assert alloc_p2.req_ram_mib >= 2048
    assert alloc_p2.is_oom_vulnerable is False


def test_discrete_event_simulation_execution(tmp_path: Path) -> None:
    """Run a scaled fast simulation to verify simulator engine and metrics."""
    workloads = load_e4_workloads()
    cfg = ClusterConfig(num_nodes=10, cpu_per_node_milli=20_000, ram_per_node_mib=40_960, gpu_per_node=2)

    sessions = generate_notebook_workload_trace(
        workloads=workloads,
        total_sessions=30,
        duration_hours=1.0,
        mean_duration_minutes=10.0,
        seed=123,
    )
    assert len(sessions) == 30

    sim = KubernetesClusterSimulator(cfg)
    telemetry, completed, summary = sim.run(sessions, "P2", duration_hours=1.0, sample_interval_s=60.0)

    assert len(telemetry) > 0
    assert len(completed) > 0
    assert summary["strategy"] == "P2"
    assert summary["oom_rate_pct"] == 0.0
    assert "cluster_capacity_stranding_pct" in summary
    assert "cpu" in summary["cluster_capacity_stranding_pct"]


def test_figure_generation(tmp_path: Path) -> None:
    """Verify that the plotting module successfully creates PDF and PNG files."""
    workloads = load_e4_workloads()
    cfg = ClusterConfig(num_nodes=10, cpu_per_node_milli=20_000, ram_per_node_mib=40_960, gpu_per_node=2)

    sessions = generate_notebook_workload_trace(
        workloads=workloads,
        total_sessions=20,
        duration_hours=1.0,
        mean_duration_minutes=5.0,
        seed=999,
    )

    sim = KubernetesClusterSimulator(cfg)
    results = {}
    for strat in ["B0", "P1", "P2"]:
        tel, comp, summ = sim.run(sessions, strat, duration_hours=1.0, sample_interval_s=60.0)
        results[strat] = (tel, summ)

    pdf_out = tmp_path / "test_sim.pdf"
    png_out = tmp_path / "test_sim.png"

    render_simulation_figures(results, pdf_out, png_out, cfg)
    assert pdf_out.is_file()
    assert pdf_out.stat().st_size > 1000
    assert png_out.is_file()
    assert png_out.stat().st_size > 1000
