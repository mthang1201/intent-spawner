"""Unit tests for evaluation_v5/dataset_pipeline/derive_profile_labels.py."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from recommender.candidate_corpus import DEFAULT_PROFILE_DEFINITIONS, parse_memory_to_bytes
from evaluation_v5.dataset_pipeline.derive_profile_labels import (
    DEFAULT_SCHEMA_PATH,
    ProfileMetadata,
    derive_label_for_record,
    generate_source_distribution_report,
    load_sorted_profile_metadata,
    main,
    process_workload_records,
    verify_schema_contract,
)


@pytest.fixture
def sorted_profiles():
    return load_sorted_profile_metadata()


def test_schema_contract_valid():
    """Verify that protocol-v5-gold-family-v1.schema.json contains required reviewPolicy fields."""
    p_share, img_share = verify_schema_contract(DEFAULT_SCHEMA_PATH)
    assert p_share == 0.5
    assert img_share == 0.5


def test_schema_contract_missing_field(tmp_path):
    """If schema is missing max_preferred_profile_share or max_preferred_image_share, it raises KeyError."""
    bad_schema = tmp_path / "bad_schema.json"
    bad_schema.write_text(json.dumps({
        "$defs": {
            "reviewPolicy": {
                "type": "object",
                "properties": {
                    # Missing max_preferred_profile_share and max_preferred_image_share
                    "other_prop": {"type": "number"}
                }
            }
        }
    }), encoding="utf-8")

    with pytest.raises(KeyError, match="missing required reviewPolicy field"):
        verify_schema_contract(bad_schema)


def test_load_sorted_profile_metadata_and_unit_conversion():
    """Verify profiles are dynamically loaded and sorted by memory bytes, converted to MiB in one place."""
    profiles = load_sorted_profile_metadata()
    assert len(profiles) == len(DEFAULT_PROFILE_DEFINITIONS)
    for i in range(len(profiles) - 1):
        assert profiles[i].mem_limit_bytes < profiles[i + 1].mem_limit_bytes
        assert profiles[i].mem_limit_mib < profiles[i + 1].mem_limit_mib

    # Check explicit unit conversion matches parse_memory_to_bytes
    for p in profiles:
        raw_limit = DEFAULT_PROFILE_DEFINITIONS[p.slug]["mem_limit"]
        expected_bytes = parse_memory_to_bytes(raw_limit)
        expected_mib = expected_bytes / (1024.0 * 1024.0)
        assert p.mem_limit_bytes == expected_bytes
        assert pytest.approx(p.mem_limit_mib, 0.0001) == expected_mib


def test_normal_case_no_bump(sorted_profiles):
    """When peak <= max_utilization * limit, preferred profile is unchanged, slack expands acceptable profiles."""
    small_prof = sorted_profiles[0]
    # Small is 384 MiB. 0.85 * 384 = 326.4 MiB. Peak at 200 MiB is safely below.
    rec = {
        "problem_id": "test_normal_1",
        "source": "ds1000",
        "image_id": "scipy-data-science",
        "status": "ok",
        "final_min_passing_profile": small_prof.slug,
        "peak_mib_max": 200.0,
        "import_dominated": False,
        "peak_method": "memory.peak",
    }

    labeled = derive_label_for_record(
        rec,
        sorted_profiles,
        headroom_metric="peak_mib_max",
        max_utilization=0.85,
        acceptable_slack=1,
    )

    assert labeled is not None
    assert labeled["preferred_profile"] == small_prof.slug
    assert labeled["preferred_profile_ids"] == [small_prof.slug]
    assert labeled["bumped_for_headroom"] is False
    assert labeled["headroom_exceeded_at_max"] is False
    assert labeled["expected_feasibility"] == "feasible"
    # Slack = 1: small + medium
    assert labeled["acceptable_profiles"] == [sorted_profiles[0].slug, sorted_profiles[1].slug]
    assert labeled["acceptable_profile_ids"] == [sorted_profiles[0].slug, sorted_profiles[1].slug]


def test_headroom_bump_escalation(sorted_profiles):
    """When peak > 0.85 * limit, preferred profile is bumped by 1 tier and acceptable computed post-bump."""
    small_prof = sorted_profiles[0]
    # Small is 384 MiB. 0.85 * 384 = 326.4 MiB. Peak at 350 MiB exceeds threshold.
    rec = {
        "problem_id": "test_bump_1",
        "source": "ds1000",
        "image_id": "scipy-data-science",
        "status": "ok",
        "final_min_passing_profile": small_prof.slug,
        "peak_mib_max": 350.0,
        "import_dominated": False,
        "peak_method": "memory.peak",
    }

    labeled = derive_label_for_record(
        rec,
        sorted_profiles,
        headroom_metric="peak_mib_max",
        max_utilization=0.85,
        acceptable_slack=1,
    )

    assert labeled is not None
    # Bumped from small -> medium
    assert labeled["final_min_passing_profile"] == small_prof.slug
    assert labeled["preferred_profile"] == sorted_profiles[1].slug
    assert labeled["preferred_profile_ids"] == [sorted_profiles[1].slug]
    assert labeled["bumped_for_headroom"] is True
    assert labeled["headroom_exceeded_at_max"] is False
    # Acceptable computed AFTER bump: medium + large (slack=1)
    assert labeled["acceptable_profiles"] == [sorted_profiles[1].slug, sorted_profiles[2].slug]
    assert labeled["acceptable_profile_ids"] == [sorted_profiles[1].slug, sorted_profiles[2].slug]


def test_headroom_exceeded_at_max_profile(sorted_profiles):
    """When peak exceeds 0.85 * limit on the largest profile, cannot bump further, set headroom_exceeded_at_max: true."""
    max_prof = sorted_profiles[-1]
    limit_mib = max_prof.mem_limit_mib
    peak_over = limit_mib * 0.95  # 95% utilization

    rec = {
        "problem_id": "test_max_exceeded",
        "source": "ds1000",
        "image_id": "pytorch-deep-learning",
        "status": "ok",
        "final_min_passing_profile": max_prof.slug,
        "peak_mib_max": peak_over,
        "import_dominated": False,
        "peak_method": "memory.peak",
    }

    labeled = derive_label_for_record(
        rec,
        sorted_profiles,
        headroom_metric="peak_mib_max",
        max_utilization=0.85,
        acceptable_slack=1,
    )

    assert labeled is not None
    assert labeled["preferred_profile"] == max_prof.slug
    assert labeled["bumped_for_headroom"] is False
    assert labeled["headroom_exceeded_at_max"] is True
    assert labeled["acceptable_profiles"] == [max_prof.slug]


def test_infeasible_over_capacity(sorted_profiles):
    """Infeasible workload returns expected_feasibility: infeasible and empty profile lists."""
    rec = {
        "problem_id": "test_infeasible_workload",
        "source": "arcade",
        "image_id": "scipy-data-science",
        "status": "infeasible_over_capacity",
        "final_min_passing_profile": None,
        "peak_mib_max": 2500.0,
        "import_dominated": False,
        "peak_method": "memory.peak",
    }

    labeled = derive_label_for_record(
        rec,
        sorted_profiles,
    )

    assert labeled is not None
    assert labeled["expected_feasibility"] == "infeasible"
    assert labeled["preferred_profile"] is None
    assert labeled["preferred_profile_ids"] == []
    assert labeled["acceptable_profiles"] == []
    assert labeled["acceptable_profile_ids"] == []
    assert labeled["bumped_for_headroom"] is False


def test_slack_zero_vs_slack_one(sorted_profiles):
    """Verify slack=0 only includes preferred profile; slack=1 includes up to 1 tier higher, capped at max."""
    rec = {
        "problem_id": "test_slack",
        "source": "ds1000",
        "image_id": "scipy-data-science",
        "status": "ok",
        "final_min_passing_profile": sorted_profiles[1].slug,  # medium
        "peak_mib_max": 400.0,  # below 0.85 * 1024, no bump
    }

    # slack = 0
    lbl_slack0 = derive_label_for_record(rec, sorted_profiles, acceptable_slack=0)
    assert lbl_slack0["acceptable_profiles"] == [sorted_profiles[1].slug]

    # slack = 1
    lbl_slack1 = derive_label_for_record(rec, sorted_profiles, acceptable_slack=1)
    assert lbl_slack1["acceptable_profiles"] == [sorted_profiles[1].slug, sorted_profiles[2].slug]

    # slack = 2 (capped at max)
    lbl_slack2 = derive_label_for_record(rec, sorted_profiles, acceptable_slack=2)
    assert lbl_slack2["acceptable_profiles"] == [sorted_profiles[1].slug, sorted_profiles[2].slug]


def test_skip_error_and_timeout_records(sorted_profiles):
    """Records with status error or timeout must return None from derive_label_for_record and be counted in skipped."""
    records = [
        {"problem_id": "p_ok", "status": "ok", "source": "ds1000", "image_id": "scipy-data-science", "final_min_passing_profile": sorted_profiles[0].slug, "peak_mib_max": 100.0},
        {"problem_id": "p_err", "status": "error", "source": "ds1000", "image_id": "scipy-data-science", "error_message": "ModuleNotFoundError"},
        {"problem_id": "p_timeout", "status": "timeout", "source": "ds1000", "image_id": "scipy-data-science"},
        {"problem_id": "p_inf", "status": "infeasible_over_capacity", "source": "ds1000", "image_id": "scipy-data-science"},
    ]

    labeled, skipped = process_workload_records(records, sorted_profiles)
    assert len(labeled) == 2
    assert {r["problem_id"] for r in labeled} == {"p_ok", "p_inf"}
    assert len(skipped) == 2
    assert {r["problem_id"] for r in skipped} == {"p_err", "p_timeout"}


def test_report_generation_and_warnings(sorted_profiles):
    """Verify report generation captures distributions, bump transitions, and review warnings."""
    # Create synthetic records that trigger:
    # 1. bump share > 0.25 (e.g. 50% bumped)
    # 2. preferred profile share > 0.5
    records = []
    # 10 records: 6 on small with high peak -> all 6 bump to medium
    # 4 on medium with normal peak -> stay on medium
    # Total 10: preferred is medium for all 10 (100% > 50%), bump is 6/10 (60% > 25%)
    for i in range(6):
        records.append({
            "problem_id": f"bump_{i}",
            "source": "test_src",
            "image_id": "img_a",
            "status": "ok",
            "final_min_passing_profile": sorted_profiles[0].slug,
            "peak_mib_max": 350.0,  # exceeds small limit 85%
        })
    for i in range(4):
        records.append({
            "problem_id": f"med_{i}",
            "source": "test_src",
            "image_id": "img_a",
            "status": "ok",
            "final_min_passing_profile": sorted_profiles[1].slug,
            "peak_mib_max": 400.0,
        })

    labeled, skipped = process_workload_records(records, sorted_profiles)
    report_text, warnings = generate_source_distribution_report(
        source="test_src",
        total_input=10,
        labeled_records=labeled,
        skipped_records=skipped,
        sorted_profiles=sorted_profiles,
        headroom_metric="peak_mib_max",
        max_utilization=0.85,
        acceptable_slack=1,
        warn_bump_share=0.25,
        max_preferred_profile_share=0.5,
        max_preferred_image_share=0.5,
    )

    assert "60.0%" in report_text
    assert "# Profile Label Distribution Report — `test_src`" in report_text
    # Should contain warnings for high bump share, preferred profile share, and image share
    assert any("High headroom bump share" in w for w in warnings)
    assert any("Preferred profile" in w and "exceeds maximum allowed share" in w for w in warnings)
    assert any("Image" in w and "exceeds maximum allowed share" in w for w in warnings)


def test_cli_end_to_end_multisource(tmp_path):
    """Test CLI main() with multi-source input producing separate label and report files."""
    ds1000_file = tmp_path / "ds1000_profiles.jsonl"
    arcade_file = tmp_path / "arcade_profiles.jsonl"

    profiles = load_sorted_profile_metadata()

    # ds1000 synthetic lines
    ds1000_records = [
        {"problem_id": "ds_1", "source": "ds1000", "image_id": "scipy-data-science", "status": "ok", "final_min_passing_profile": profiles[0].slug, "peak_mib_max": 150.0},
        {"problem_id": "ds_2", "source": "ds1000", "image_id": "scipy-data-science", "status": "error", "error_message": "failed"},
    ]
    with open(ds1000_file, "w") as f:
        for r in ds1000_records:
            f.write(json.dumps(r) + "\n")

    # arcade synthetic lines
    arcade_records = [
        {"problem_id": "arc_1", "source": "arcade", "image_id": "scipy-data-science", "status": "ok", "final_min_passing_profile": profiles[1].slug, "peak_mib_max": 500.0},
        {"problem_id": "arc_2", "source": "arcade", "image_id": "scipy-data-science", "status": "infeasible_over_capacity", "peak_mib_max": 3000.0},
    ]
    with open(arcade_file, "w") as f:
        for r in arcade_records:
            f.write(json.dumps(r) + "\n")

    out_dir = tmp_path / "oracle"
    report_dir = tmp_path / "reports"

    ret = main([
        "--input", str(ds1000_file), str(arcade_file),
        "--output-dir", str(out_dir),
        "--report-dir", str(report_dir),
    ])
    assert ret == 0

    # Verify separate files exist
    ds_labels = out_dir / "profile_labels_ds1000.jsonl"
    arc_labels = out_dir / "profile_labels_arcade.jsonl"
    assert ds_labels.is_file()
    assert arc_labels.is_file()

    ds_report = report_dir / "profile_label_distribution_ds1000.md"
    arc_report = report_dir / "profile_label_distribution_arcade.md"
    assert ds_report.is_file()
    assert arc_report.is_file()

    # Check ds1000 output content
    ds_lines = [json.loads(line) for line in ds_labels.read_text().splitlines() if line]
    assert len(ds_lines) == 1
    assert ds_lines[0]["problem_id"] == "ds_1"
    assert ds_lines[0]["source"] == "ds1000"

    # Check arcade output content
    arc_lines = [json.loads(line) for line in arc_labels.read_text().splitlines() if line]
    assert len(arc_lines) == 2
    assert {l["problem_id"] for l in arc_lines} == {"arc_1", "arc_2"}
    inf_rec = next(l for l in arc_lines if l["problem_id"] == "arc_2")
    assert inf_rec["expected_feasibility"] == "infeasible"
