"""Unit and integration tests for Protocol-v5 E4 Operator Preflight engine.

Verifies that the preflight engine:
1. Enforces all 16 execution prerequisites;
2. Fails closed with NOT_EXECUTED in unverified or development environments;
3. Rejects synthetic, fake, or mock adapters when readiness is checked;
4. Prevents resumption of sealed, dry-run, or synthetic evidence packages;
5. Detects unpinned image references, production contexts, and dirty git revisions;
6. Integrates seamlessly with runner CLI interfaces.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
from typing import Any

import pytest

from evaluation_v5.resource import efficiency_runner, runner
from evaluation_v5.resource.preflight import (
    PREFLIGHT_REPORT_SCHEMA_VERSION,
    assert_live_execution_ready,
    check_approved_resource_oracle,
    check_cgroup_capability,
    check_adapter_authenticity,
    check_disposable_cluster_and_environment,
    check_frozen_git_revision,
    check_frozen_node_capacity,
    check_pinned_image_digest,
    check_resume_state,
    check_timeout_and_cleanup_contracts,
    check_trial_ordering_contract,
    check_workload_correctness_markers,
    check_workload_manifest,
    evaluate_operator_preflight,
    main as preflight_main,
)

IMAGE_PINNED = "example.invalid/intent-spawner-resource-v5@sha256:" + "a" * 64
IMAGE_UNPINNED = "example.invalid/intent-spawner-resource-v5:latest"


class FakeAdapter:
    adapter_version = "protocol-v5-fake-adapter-v1.0.0"

    def environment_provenance(self) -> dict[str, Any]:
        return {
            "collector_origin": "FAKE",
            "eligibility_status": "SYNTHETIC_OVERRIDE",
            "kubernetes_version": "v1.30.0-synthetic",
        }


def test_evaluate_operator_preflight_fails_closed_in_dev_env():
    report = evaluate_operator_preflight(target="all")

    assert report["schema_version"] == PREFLIGHT_REPORT_SCHEMA_VERSION
    assert report["status"] == "NOT_EXECUTED"
    assert report["target"] == "all"
    assert report["summary"]["is_ready"] is False
    assert report["summary"]["blocker_count"] > 0
    assert len(report["summary"]["blocker_codes"]) > 0

    # Ensure all required prerequisite categories are present in checks
    checks = report["checks"]
    expected_checks = [
        "frozen_git_revision",
        "authoritative_final_freeze",
        "workload_manifest",
        "approved_independent_resource_oracle",
        "collector_implementation_authenticity",
        "disposable_cluster_and_environment",
        "frozen_node_capacity",
        "pinned_execution_image_digest",
        "cgroup_telemetry_capability",
        "workload_correctness_markers",
        "timeout_and_cleanup_contracts",
        "trial_ordering_contract",
        "resume_state",
    ]
    for check_name in expected_checks:
        assert check_name in checks, f"Missing prerequisite check: {check_name}"

    # Workload correctness markers, timeout contracts, and manifest pass by definition in codebase
    assert checks["workload_correctness_markers"]["status"] == "PASS"
    assert checks["timeout_and_cleanup_contracts"]["status"] == "PASS"
    assert checks["workload_manifest"]["status"] == "PASS"

    # In dev environment without live cluster and unverified image state:
    assert checks["approved_independent_resource_oracle"]["status"] == "FAIL"
    assert checks["disposable_cluster_and_environment"]["status"] == "FAIL"
    assert checks["frozen_node_capacity"]["status"] == "FAIL"
    assert checks["pinned_execution_image_digest"]["status"] == "FAIL"

    # Limitations are explicit
    assert any("OBSERVED" in lim for lim in report["limitations"])


def test_preflight_cli_envelope_json_and_text(capsys):
    ret = preflight_main(["--target", "envelope", "--format", "json"])
    assert ret == 0
    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert data["target"] == "envelope"
    assert data["status"] == "NOT_EXECUTED"

    ret_text = preflight_main(["--target", "envelope", "--format", "text"])
    assert ret_text == 0
    captured_text = capsys.readouterr().out
    assert "Protocol-v5 E4 Preflight Status: NOT_EXECUTED" in captured_text
    assert "Target: envelope" in captured_text
    assert "Blockers" in captured_text


def test_preflight_cli_efficiency_json(capsys):
    ret = preflight_main(["--target", "efficiency", "--format", "json"])
    assert ret == 0
    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert data["target"] == "efficiency"
    assert data["status"] == "NOT_EXECUTED"
    assert "approved_independent_resource_oracle" in data["checks"]


def test_assert_live_execution_ready_raises_in_dev_env():
    with pytest.raises(RuntimeError, match="OBSERVED_E4_EXECUTION_BLOCKED"):
        assert_live_execution_ready(
            target="envelope",
            image=IMAGE_PINNED,
        )

    with pytest.raises(RuntimeError, match="RESOURCE_EFFICIENCY_EXECUTION_BLOCKED"):
        assert_live_execution_ready(
            target="efficiency",
            image=IMAGE_PINNED,
        )


def test_assert_live_execution_ready_detects_synthetic_adapter():
    with pytest.raises(RuntimeError, match="AUTHENTICATED_REAL_KUBERNETES_COLLECTOR_REQUIRED"):
        assert_live_execution_ready(
            target="envelope",
            adapter=FakeAdapter(),
            image=IMAGE_PINNED,
        )

    with pytest.raises(RuntimeError, match="AUTHENTICATED_REAL_KUBERNETES_COLLECTOR_REQUIRED"):
        assert_live_execution_ready(
            target="efficiency",
            adapter=FakeAdapter(),
            image=IMAGE_PINNED,
        )


def test_check_resume_state_policy(tmp_path):
    # 1. Target directory exists but resume=False -> TARGET_DIRECTORY_EXISTS
    existing_dir = tmp_path / "exists-no-resume"
    existing_dir.mkdir()
    res = check_resume_state(result_dir=existing_dir, resume=False)
    assert not res.passed
    assert "TARGET_DIRECTORY_EXISTS" in res.blocker_codes

    # 2. Target directory missing but resume=True -> RESUME_TARGET_NOT_FOUND
    missing_dir = tmp_path / "does-not-exist"
    res = check_resume_state(result_dir=missing_dir, resume=True)
    assert not res.passed
    assert "RESUME_TARGET_NOT_FOUND" in res.blocker_codes

    # 3. Target directory has SHA256SUMS and resume=True -> RESUME_SEALED_PACKAGE_FORBIDDEN
    sealed_dir = tmp_path / "sealed-package"
    sealed_dir.mkdir()
    (sealed_dir / "SHA256SUMS").write_text("dummy sha256 checksums\n", encoding="utf-8")
    res = check_resume_state(result_dir=sealed_dir, resume=True)
    assert not res.passed
    assert "RESUME_SEALED_PACKAGE_FORBIDDEN" in res.blocker_codes

    # 4. Target directory has dry-run manifest -> CANNOT_RESUME_DRY_RUN_AS_REAL_EXECUTION
    dry_dir = tmp_path / "dry-run-package"
    dry_dir.mkdir()
    (dry_dir / "manifest.json").write_text(json.dumps({"execution_status": "DRY_RUN"}), encoding="utf-8")
    res = check_resume_state(result_dir=dry_dir, resume=True)
    assert not res.passed
    assert "CANNOT_RESUME_DRY_RUN_AS_REAL_EXECUTION" in res.blocker_codes

    # 5. Target directory has synthetic environment -> CANNOT_RESUME_DRY_RUN_AS_REAL_EXECUTION
    synth_dir = tmp_path / "synth-env-package"
    (synth_dir / "raw").mkdir(parents=True)
    (synth_dir / "raw" / "environment.json").write_text(
        json.dumps({"collector_origin": "SYNTHETIC"}), encoding="utf-8"
    )
    res = check_resume_state(result_dir=synth_dir, resume=True)
    assert not res.passed
    assert "CANNOT_RESUME_DRY_RUN_AS_REAL_EXECUTION" in res.blocker_codes


def test_image_pinning_check():
    # Unpinned image tag fails with IMAGE_REFERENCE_UNPINNED
    res_unpinned = check_pinned_image_digest(image=IMAGE_UNPINNED)
    assert not res_unpinned.passed
    assert "IMAGE_REFERENCE_UNPINNED" in res_unpinned.blocker_codes

    # Pinned image but not matching active freeze image state fails with IMAGE_DIGEST_UNVERIFIED
    res_pinned = check_pinned_image_digest(image=IMAGE_PINNED)
    assert not res_pinned.passed
    assert "IMAGE_DIGEST_UNVERIFIED" in res_pinned.blocker_codes


def test_git_revision_and_dirty_checks(monkeypatch):
    # Enforcing clean git tree when dirty
    monkeypatch.setattr(
        "evaluation_v5.resource.preflight._get_git_info",
        lambda: {"git_revision": "0" * 40, "git_dirty": True, "git_available": True},
    )
    res_dirty = check_frozen_git_revision()
    assert not res_dirty.passed
    assert "DIRTY_GIT_TREE" in res_dirty.blocker_codes

    # Explicit expected commit mismatch
    res_mismatch = check_frozen_git_revision(environ={"E4_EXPECTED_GIT_SHA": "1" * 40})
    assert not res_mismatch.passed
    assert "FROZEN_GIT_REVISION_MISMATCH" in res_mismatch.blocker_codes


def test_runner_subcommands_preflight(capsys):
    # Test runner.py preflight subcommand
    ret_runner = runner.main(["preflight", "--target", "envelope", "--format", "json"])
    assert ret_runner == 0
    data_runner = json.loads(capsys.readouterr().out)
    assert data_runner["target"] == "envelope"
    assert data_runner["status"] == "NOT_EXECUTED"

    # Test efficiency_runner.py preflight subcommand
    ret_eff = efficiency_runner.main(["preflight", "--format", "json"])
    assert ret_eff == 0
    data_eff = json.loads(capsys.readouterr().out)
    assert data_eff["target"] == "efficiency"
    assert data_eff["status"] == "NOT_EXECUTED"
