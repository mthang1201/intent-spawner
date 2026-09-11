"""Focused regression tests for Protocol-v5 offline pipeline repairs.

Covers:
1. Unavailable gold handling across offline pipeline modules.
2. Incomplete current-schema evidence handling.
3. Legacy input handling (v1 split bundle and legacy functional evidence).
4. Deterministic regeneration of raw -> derived -> report artifacts.
5. Provenance mismatch detection and tamper fail-closed behavior.
6. Stale outputs rejection and directory safety invariants.
7. Explicit NOT_EXECUTED package semantics and CLI behavior.
8. Deterministic figure and table regeneration.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
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
from evaluation_v5.final_audit.common import Inputs, ROOT, LOCK
from evaluation_v5.final_audit.checks import inspect
from evaluation_v5.final_audit.reproduce import analyze, VOLATILE_MANIFEST_FIELDS, compare_json
from evaluation_v5.final_audit.reporting import figures
from evaluation_v5.offline.validate_evidence import OfflineEvidenceValidationError


# ---------------------------------------------------------------------------
# 1. Unavailable Gold
# ---------------------------------------------------------------------------


def test_unavailable_gold_produces_not_executed(tmp_path: Path):
    """Missing or corrupted gold produces an explicit NOT_EXECUTED package."""
    evidence_dir = ROOT / "results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1"

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


def test_legacy_v1_split_input_handling(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """V1 development split cannot complete v2 scoring and emits NOT_EXECUTED."""
    evidence_dir = ROOT / "results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1"
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


def test_legacy_functional_packages_not_reinterpreted():
    """Legacy E5 functional packages are bounded as UNVERIFIED rather than reinterpreted."""
    inputs = Inputs(ROOT, LOCK)
    audit = inspect(inputs)
    legacy_pkgs = [p for p in audit["packages"] if p["kind"] == "image_functional" and p.get("validator_result", {}).get("validator_status") == "LEGACY_VALID"]
    assert len(legacy_pkgs) == 9

    with tempfile.TemporaryDirectory() as tmpdir:
        res = analyze(inputs, audit, Path(tmpdir) / "analysis")
        # Every legacy package is marked UNVERIFIED in reproduction
        legacy_entries = [p for p in res["packages"] if p["path"] in {lp["path"] for lp in legacy_pkgs}]
        assert all(entry["status"] == "UNVERIFIED" for entry in legacy_entries)
        assert all("Legacy or invalid functional package retained" in entry["reason"] for entry in legacy_entries)


# ---------------------------------------------------------------------------
# 4. Deterministic Regeneration
# ---------------------------------------------------------------------------


def test_deterministic_offline_regeneration():
    """Offline E1 evidence reproduces raw counts, manifests, and reports bit-for-bit."""
    inputs = Inputs(ROOT, LOCK)
    audit = inspect(inputs)

    with tempfile.TemporaryDirectory() as tmpdir1, tempfile.TemporaryDirectory() as tmpdir2:
        res1 = analyze(inputs, audit, Path(tmpdir1) / "analysis")
        res2 = analyze(inputs, audit, Path(tmpdir2) / "analysis")

        # Package status is REGENERATED
        e1_entry1 = next(p for p in res1["packages"] if "E1" in p["path"])
        e1_entry2 = next(p for p in res2["packages"] if "E1" in p["path"])
        assert e1_entry1["status"] == "REGENERATED"
        assert e1_entry2["status"] == "REGENERATED"
        assert all(c["status"] == "PASS" for c in e1_entry1["comparisons"])

        # Generated artifacts match bit-for-bit between independent runs
        path1 = Path(tmpdir1) / "analysis/E1/20260825T-observed-p1-p2-development-v1"
        path2 = Path(tmpdir2) / "analysis/E1/20260825T-observed-p1-p2-development-v1"

        assert (path1 / "raw_counts.json").read_bytes() == (path2 / "raw_counts.json").read_bytes()
        assert (path1 / "report/offline_report/E1_E2_OFFLINE_REPORT.md").read_bytes() == (
            path2 / "report/offline_report/E1_E2_OFFLINE_REPORT.md"
        ).read_bytes()

        # Check raw counts match exactly
        counts = json.loads((path1 / "raw_counts.json").read_text(encoding="utf-8"))
        assert counts["records"] == 36
        assert counts["cases"] == 18
        assert counts["families"] == 10
        assert counts["per_system"] == {"P1": 18, "P2": 18}


# ---------------------------------------------------------------------------
# 5. Provenance Mismatch Detection
# ---------------------------------------------------------------------------


def test_provenance_mismatch_fails_closed(tmp_path: Path):
    """Perturbed completion provenance fingerprint raises ReportingError."""
    src_evidence = ROOT / "results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1"
    evidence_dir = tmp_path / "tampered_e1"
    shutil.copytree(src_evidence, evidence_dir)

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


def test_authentic_provenance_mismatch_retained_in_audit():
    """Historical prompt hash mismatch in Check 5 is detected and retained as FAIL."""
    inputs = Inputs(ROOT, LOCK)
    audit = inspect(inputs)
    check5 = audit["checks"][4]
    assert check5["verdict"] == "FAIL"
    fields = {item["field"] for item in check5["details"]}
    assert "/extractor/extractor_prompt_sha256" in fields


# ---------------------------------------------------------------------------
# 6. Stale Outputs Rejection
# ---------------------------------------------------------------------------


def test_stale_output_directory_rejected(tmp_path: Path):
    """Output directory safety prevents overwriting an existing directory."""
    existing_dir = tmp_path / "already_exists"
    existing_dir.mkdir()
    (existing_dir / "stale_file.txt").write_text("old data", encoding="utf-8")

    evidence_dir = ROOT / "results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1"
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


def test_derived_figures_and_tables_regeneration():
    """Final audit figures command deterministically regenerates tables and SVGs."""
    inputs = Inputs(ROOT, LOCK)
    audit = inspect(inputs)

    with tempfile.TemporaryDirectory() as tmpdir:
        analysis_dir = Path(tmpdir) / "analysis"
        figures_dir = Path(tmpdir) / "figures"
        derived = analyze(inputs, audit, analysis_dir)
        fig_result = figures(inputs, derived, analysis_dir, figures_dir)

        assert fig_result["status"] == "PASS"
        assert all(c["status"] == "PASS" for c in fig_result["comparisons"])

        # Tables are created and valid
        assert (figures_dir / "tables/functional-results.json").is_file()
        assert (figures_dir / "tables/defense-summary.json").is_file()
        assert (figures_dir / "tables/defense-summary.md").is_file()
