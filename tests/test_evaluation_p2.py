from __future__ import annotations

import json

import pytest

from evaluation_p2.dataset import load_evaluation_dataset
from evaluation_p2.runner import run_evaluation


def test_versioned_dataset_composes_frozen_base_without_modifying_it():
    dataset = load_evaluation_dataset()
    assert len(dataset["items"]) == 66
    assert sum(not item["gold"]["request_feasible"] for item in dataset["items"]) == 4
    assert dataset["base_dataset_id"] == "intent-gold-en-vi-2026-08-08"
    assert len(dataset["base_dataset_sha256"]) == 64
    assert len(dataset["supplement_file_sha256"]) == 64


def test_offline_evaluation_separates_raw_predictions_and_never_overwrites(tmp_path):
    target = run_evaluation(output_root=tmp_path, run_id="observed-test-run")
    manifest = json.loads((target / "manifest.json").read_text())
    metrics = json.loads((target / "aggregates" / "metrics.json").read_text())
    errors = json.loads((target / "analysis" / "p2_errors.json").read_text())
    decision = json.loads((target / "analysis" / "p3_decision.json").read_text())
    raw_lines = (target / "raw" / "predictions.jsonl").read_text().splitlines()

    assert manifest["sample_count"] == 66
    assert manifest["prediction_count"] == 132
    assert manifest["primary_systems"] == ["p1", "p2"]
    assert manifest["dense_only_and_sparse_only"] == "not_run"
    assert len(raw_lines) == 132
    assert set(metrics["systems"]) == {"p1", "p2"}
    for system in ("p1", "p2"):
        system_metrics = metrics["systems"][system]
        assert "top1_accuracy" in system_metrics
        assert "acceptable_candidate_hit_at_k" in system_metrics
        assert "mrr" in system_metrics
        assert "ndcg_at_5" in system_metrics
        assert "constraint_violation_rate" in system_metrics
        assert "infeasible_request_detection" in system_metrics
        assert "latency_seconds" in system_metrics
        assert "fallback_rate" in system_metrics
    assert sum(errors["categories"].values()) + errors["no_error"] == 66
    assert decision["p3_implemented"] is False
    assert isinstance(decision["meaningful_reranking_headroom"], bool)
    assert "private" not in (target / "aggregates" / "metrics.json").read_text()

    with pytest.raises(FileExistsError):
        run_evaluation(output_root=tmp_path, run_id="observed-test-run")
