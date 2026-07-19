from __future__ import annotations

import argparse
import csv
from pathlib import Path

from experiments.analyze_results import analyze
from experiments.jsonl_io import append_jsonl
from experiments.recorder import build_record, workload_by_id


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "workloads.yaml"


def test_analysis_generates_requested_tables_from_raw_records(tmp_path):
    workload = workload_by_id(MANIFEST, "light_basic_python")
    experiment_dir = tmp_path / "raw" / "fixture-analysis"
    results_jsonl = experiment_dir / "results.jsonl"
    record = build_record(
        workload=workload,
        method="context_aware",
        repeat_index=0,
        seed=1101,
        environment_id="pytest-analysis",
        run_id="fixture-analysis-context-aware",
    )
    append_jsonl(results_jsonl, record)

    args = argparse.Namespace(
        experiment_dir=experiment_dir,
        raw_jsonl=None,
        manifest=MANIFEST,
        results_dir=tmp_path / "results",
        results_md=tmp_path / "docs" / "RESULTS.md",
        environment_report=None,
        overwrite=False,
    )

    outputs = analyze(args)
    output_names = {path.name for path in outputs}

    assert "summary.csv" in output_names
    assert "run_counts_and_exclusions.csv" in output_names
    assert "oom_failure_rates.csv" in output_names
    assert "restart_respawn_comparison.csv" in output_names
    assert "time_to_success_comparison.csv" in output_names
    assert "pending_time_comparison.csv" in output_names
    assert "requested_vs_peak_scatter.csv" in output_names
    assert "waste_ratio_comparison.csv" in output_names
    assert "recommendation_confusion.csv" in output_names
    assert "ablation.csv" in output_names
    assert "per_workload_results.csv" in output_names
    assert "robustness_boundary_summary.csv" in output_names
    assert "RESULTS.md" in output_names

    with (args.results_dir / "summary.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["run_id"] == "fixture-analysis-context-aware"
    assert rows[0]["recommendation_outcome"] == "acceptable"
