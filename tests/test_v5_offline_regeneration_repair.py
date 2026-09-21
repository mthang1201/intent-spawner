"""Focused regression tests for Protocol-v5 offline pipeline repairs.

Covers:
1. Unavailable gold handling across offline pipeline modules.
2. Incomplete current-schema evidence handling.
3. Legacy v1 split-bundle input handling.
4. Provenance mismatch detection and tamper fail-closed behavior.
5. Stale outputs rejection and directory safety invariants.
6. Explicit NOT_EXECUTED package semantics and CLI behavior.
7. Deterministic figure rendering.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import pytest

from evaluation_v4.dataset import file_sha256
from evaluation_v5.analysis.component_scoring import (
    ComponentAnalysisError,
    analyze_component_evidence,
    write_not_executed as write_not_executed_component,
)
from evaluation_v5.analysis.statistical_analysis import (
    StatisticalAnalysisError,
    analyze_statistical_evidence,
    write_not_executed as write_not_executed_statistical,
)
from evaluation_v5.analysis.reporting import (
    REPORT_MANIFEST_FILENAME,
    REPORTING_SCHEMA_VERSION,
    ReportingError,
    SYNTHESIS_REPORT_FILENAME,
    TABLE_FILES,
    generate_offline_report,
    main as reporting_main,
    render_confidence_intervals_svg,
    render_error_taxonomy_svg,
    render_paired_family_outcomes_svg,
    render_retrieval_recall_svg,
    write_not_executed_report,
)
from evaluation_v5.offline.runner import run_offline_recommendations
from evaluation_v5.offline.validate_evidence import OfflineEvidenceValidationError
from evaluation_v5.paths import ROOT
from evaluation_v5.split_dataset import load_development_split


@pytest.fixture(scope="module")
def valid_e1_evidence(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real, schema-valid E1 development-split offline evidence directory.

    Built at test time with the default P1/P2 adapters against the tracked
    development split, rather than depending on any previously-collected
    results_v5/ evidence (which is intentionally not present on disk)."""

    result_dir = tmp_path_factory.mktemp("v5-offline-regen") / "run"
    run_offline_recommendations(
        load_development_split(),
        result_dir=result_dir,
        system_ids=("P1", "P2"),
        seed=20260824,
        frozen_configuration={"snapshot": "regeneration-repair-test-v1"},
    )
    return result_dir


# ---------------------------------------------------------------------------
# 1. Unavailable Gold
# ---------------------------------------------------------------------------


def test_unavailable_gold_produces_not_executed(tmp_path: Path, valid_e1_evidence: Path):
    """Missing or corrupted gold produces an explicit NOT_EXECUTED package."""
    evidence_dir = valid_e1_evidence

    # Non-existent gold file
    missing_gold = tmp_path / "non_existent_gold.yaml"
    out_dir = tmp_path / "missing_gold_report"
    res = generate_offline_report(
        evidence_dir=evidence_dir,
        gold_path=missing_gold,
        output_dir=out_dir,
    )
    assert res == out_dir
    manifest = json.loads((out_dir / REPORT_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["status"] == "NOT_EXECUTED"
    assert manifest["claims_permitted"] is False
    assert manifest["reason_code"] == "INPUTS_UNAVAILABLE_OR_INVALID"

    # Corrupted YAML gold file
    corrupt_gold = tmp_path / "corrupt_gold.yaml"
    corrupt_gold.write_text("not_valid_yaml: [unclosed", encoding="utf-8")
    out_dir_corrupt = tmp_path / "corrupt_gold_report"
    res_corrupt = generate_offline_report(
        evidence_dir=evidence_dir,
        gold_path=corrupt_gold,
        output_dir=out_dir_corrupt,
    )
    manifest_corrupt = json.loads((out_dir_corrupt / REPORT_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest_corrupt["status"] == "NOT_EXECUTED"

    # Component scoring also handles missing gold gracefully
    out_comp = tmp_path / "comp_missing_gold"
    analyze_component_evidence(evidence_dir, missing_gold, out_comp)
    comp_manifest = json.loads((out_comp / "analysis-manifest.json").read_text(encoding="utf-8"))
    assert comp_manifest["status"] == "NOT_EXECUTED"

    # Statistical analysis also handles missing gold gracefully
    out_stat = tmp_path / "stat_missing_gold"
    analyze_statistical_evidence(evidence_dir, missing_gold, out_stat)
    stat_manifest = json.loads((out_stat / "analysis-manifest.json").read_text(encoding="utf-8"))
    assert stat_manifest["status"] == "NOT_EXECUTED"


# ---------------------------------------------------------------------------
# 2. Incomplete Current-Schema Input
# ---------------------------------------------------------------------------


def test_incomplete_current_schema_input(tmp_path: Path):
    """Incomplete evidence envelope emits NOT_EXECUTED rather than crashing."""
    incomplete_evidence = tmp_path / "incomplete_evidence"
    incomplete_evidence.mkdir()
    (incomplete_evidence / "raw").mkdir()
    # Empty/missing completion JSON and recommendations
    (incomplete_evidence / "raw/offline-run-provenance.json").write_text("{}", encoding="utf-8")

    gold_path = ROOT / "benchmarks_v5/v5-development.yaml"
    out_dir = tmp_path / "incomplete_out"

    res = generate_offline_report(
        evidence_dir=incomplete_evidence,
        gold_path=gold_path,
        output_dir=out_dir,
    )
    manifest = json.loads((out_dir / REPORT_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["status"] == "NOT_EXECUTED"
    assert manifest["reason_code"] == "INPUTS_UNAVAILABLE_OR_INVALID"


# ---------------------------------------------------------------------------
# 3. Legacy Input
# ---------------------------------------------------------------------------


def test_legacy_v1_split_input_handling(
    tmp_path: Path,
    valid_e1_evidence: Path,
    capsys: pytest.CaptureFixture[str],
):
    """V1 development split cannot complete v2 scoring and emits NOT_EXECUTED."""
    evidence_dir = valid_e1_evidence
    gold_path = ROOT / "benchmarks_v5/v5-development.yaml"
    output_dir = tmp_path / "legacy_v1_report"

    # CLI invocation on E1 development evidence returns exit 0 with NOT_EXECUTED
    code = reporting_main([
        "--evidence-dir", str(evidence_dir),
        "--gold-dataset", str(gold_path),
        "--output-dir", str(output_dir),
        "--role", "development",
    ])
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "NOT_EXECUTED"
    assert payload["output_dir"] == str(output_dir)

    manifest = json.loads((output_dir / REPORT_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["status"] == "NOT_EXECUTED"
    assert "complete gold must be a frozen family dataset or compiled split v2" in manifest["reason"]


# ---------------------------------------------------------------------------
# 4. Deterministic Regeneration
# ---------------------------------------------------------------------------
#
# Deterministic raw -> derived -> report regeneration and legacy-functional-
# package handling were previously exercised through evaluation_v5.final_audit
# (Inputs/inspect/analyze), which has been deleted along with the freeze/audit
# governance layer it implemented. The underlying analysis/reporting modules
# (evaluation_v5.analysis.*) are still covered directly by the tests above and
# below; there is no remaining final_audit-specific behavior to test here.


# ---------------------------------------------------------------------------
# 5. Provenance Mismatch Detection
# ---------------------------------------------------------------------------


def test_provenance_mismatch_fails_closed(tmp_path: Path, valid_e1_evidence: Path):
    """Perturbed completion provenance fingerprint raises ReportingError."""
    evidence_dir = tmp_path / "tampered_e1"
    shutil.copytree(valid_e1_evidence, evidence_dir)

    # Tamper with the completion provenance fingerprint
    comp_file = evidence_dir / "report" / "offline-run-completion.json"
    comp_data = json.loads(comp_file.read_text(encoding="utf-8"))
    comp_data["provenance_fingerprint"] = "0" * 64
    comp_file.write_text(json.dumps(comp_data), encoding="utf-8")

    out_dir = tmp_path / "mismatch_out"
    with pytest.raises((ReportingError, OfflineEvidenceValidationError), match="completion provenance does not match"):
        generate_offline_report(
            evidence_dir=evidence_dir,
            gold_path=ROOT / "benchmarks_v5/v5-development.yaml",
            output_dir=out_dir,
        )


# ---------------------------------------------------------------------------
# 6. Stale Outputs Rejection
# ---------------------------------------------------------------------------


def test_stale_output_directory_rejected(tmp_path: Path, valid_e1_evidence: Path):
    """Output directory safety prevents overwriting an existing directory."""
    existing_dir = tmp_path / "already_exists"
    existing_dir.mkdir()
    (existing_dir / "stale_file.txt").write_text("old data", encoding="utf-8")

    evidence_dir = valid_e1_evidence
    gold_path = ROOT / "benchmarks_v5/v5-development.yaml"

    with pytest.raises(FileExistsError):
        generate_offline_report(
            evidence_dir=evidence_dir,
            gold_path=gold_path,
            output_dir=existing_dir,
        )


# ---------------------------------------------------------------------------
# 7. NOT_EXECUTED Behavior
# ---------------------------------------------------------------------------


def test_write_not_executed_report_structure(tmp_path: Path):
    """write_not_executed_report produces compliant manifest and markdown."""
    out_dir = tmp_path / "not_executed_test"
    write_not_executed_report(
        out_dir,
        reason="Test deliberate not executed reason.",
        reason_code="MANUAL_TEST_NOT_EXECUTED",
    )

    manifest_path = out_dir / REPORT_MANIFEST_FILENAME
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "NOT_EXECUTED"
    assert manifest["claims_permitted"] is False
    assert manifest["reason_code"] == "MANUAL_TEST_NOT_EXECUTED"
    assert "Test deliberate not executed reason." in manifest["reason"]
    assert manifest["outputs"] == {}

    md_path = out_dir / SYNTHESIS_REPORT_FILENAME
    assert md_path.is_file()
    content = md_path.read_text(encoding="utf-8")
    assert "**Status**: `NOT_EXECUTED`" in content
    assert "Claims permitted: `False`" in content


def test_cli_status_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """CLI --status-only emits NOT_EXECUTED manifest and exits 0."""
    out_dir = tmp_path / "status_only_dir"
    code = reporting_main([
        "--output-dir", str(out_dir),
        "--status-only",
        "--not-executed-reason", "Explicit status-only test invocation.",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "NOT_EXECUTED"


# ---------------------------------------------------------------------------
# 8. Figure and Table Regeneration
# ---------------------------------------------------------------------------


def test_svg_figures_deterministic():
    """Figure renderers produce deterministic, valid SVGs."""
    mock_ci = {
        "estimates": [
            {
                "metric_key": "joint_accept_at_1",
                "metric_name": "JointAccept@1",
                "P1": {
                    "estimate": 0.5,
                    "ci_low": 0.4,
                    "ci_high": 0.6,
                },
                "P2": {
                    "estimate": 0.7,
                    "ci_low": 0.6,
                    "ci_high": 0.8,
                },
                "diff": {
                    "estimate": 0.2,
                    "ci_low": 0.1,
                    "ci_high": 0.3,
                },
            },
        ]
    }
    svg1 = render_confidence_intervals_svg(mock_ci)
    svg2 = render_confidence_intervals_svg(mock_ci)
    assert svg1 == svg2
    assert "<svg" in svg1
    assert "</svg>" in svg1
    assert "JointAccept@1" in svg1


# Table/figure regeneration through evaluation_v5.final_audit's figures()
# command was removed along with that deleted module; render_*_svg coverage
# above is the remaining figure-rendering behavior.
