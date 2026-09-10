"""Adversarial and fail-closed security tests for E4 resource authenticity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cluster_evaluation.resource_adapter_v5 import (
    KubernetesTrialAdapter,
)
from cluster_evaluation.resource_efficiency_adapter_v5 import (
    KubernetesResourceEfficiencyAdapter,
)
from evaluation_v5.resource.authenticity import (
    COLLECTOR_ORIGIN_DRY_RUN,
    COLLECTOR_ORIGIN_REAL_KUBERNETES,
    COLLECTOR_ORIGIN_SYNTHETIC,
    authenticate_adapter,
    validate_resource_authenticity,
)
from evaluation_v5.resource.efficiency_evidence import (
    validate_analysis_package,
    validate_raw_package,
)
from evaluation_v5.resource.efficiency_plan import load_plan_package
from evaluation_v5.resource.efficiency_runner import (
    execute_plan,
    write_analysis_package,
)
from evaluation_v5.resource.evidence import (
    validate_evidence_package,
)
from evaluation_v5.resource.legacy_compatibility import (
    BOUNDED_LEGACY_E4_PACKAGES,
    get_bounded_legacy_metadata,
    is_bounded_legacy_package,
    verify_bounded_legacy_integrity,
)
from evaluation_v5.resource.runner import (
    record_manual_review,
    run_calibration,
)
from tests.test_resource_envelope_v5 import (
    IMAGE,
    FakeAdapter,
    make_authenticated_test_adapter,
)


class InjectedStringFakeAdapter:
    """Adversarial adapter attempting to spoof authenticity via string attributes."""

    collector_origin = "REAL_KUBERNETES_COLLECTOR"
    _is_authenticated_real_kubernetes_collector = True
    adapter_version = "protocol-v5-kubernetes-trial-adapter-v1.2.0"

    def environment_provenance(self):
        return {
            "collector_origin": "REAL_KUBERNETES_COLLECTOR",
            "cluster_measurement_status": "OBSERVED",
            "environment_id": "spoofed-env",
        }


class SubclassedFakeAdapter(KubernetesTrialAdapter):
    """Adversarial adapter subclassing real adapter outside blessed modules."""

    adapter_version = "protocol-v5-kubernetes-trial-adapter-v1.2.0"
    _is_authenticated_real_kubernetes_collector = True
    collector_origin = "REAL_KUBERNETES_COLLECTOR"


def test_caller_injected_strings_and_subclasses_rejected():
    # 1. Plain injected duck-typed class
    spoofed = InjectedStringFakeAdapter()
    auth = authenticate_adapter(spoofed)
    assert not auth.is_authenticated_real_collector
    assert auth.collector_origin == COLLECTOR_ORIGIN_SYNTHETIC
    assert auth.derived_execution_status == "SYNTHETIC"
    assert auth.derived_cluster_measurement_status == "NOT_EXECUTED"

    # 2. Subclass outside blessed modules
    subclassed = SubclassedFakeAdapter.__new__(SubclassedFakeAdapter)
    auth_sub = authenticate_adapter(subclassed)
    assert not auth_sub.is_authenticated_real_collector
    assert auth_sub.collector_origin == COLLECTOR_ORIGIN_SYNTHETIC

    # 3. Arbitrary objects / primitives
    assert not authenticate_adapter("REAL_KUBERNETES_COLLECTOR").is_authenticated_real_collector
    assert not authenticate_adapter(None).is_authenticated_real_collector
    assert not authenticate_adapter(42).is_authenticated_real_collector


def test_synthetic_adapter_runs_as_synthetic_and_rejects_manual_review(tmp_path, monkeypatch):
    from evaluation_v5.resource import runner

    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "0" * 40, "git_dirty": False})
    result_dir = tmp_path / "synthetic-run"

    report = run_calibration(
        result_dir=result_dir,
        run_id="adv-synthetic-run",
        adapter=FakeAdapter(),
        image=IMAGE,
        enforce_readiness=False,
    )

    assert report["execution_status"] == "SYNTHETIC"
    manifest = json.loads((result_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["execution_status"] == "SYNTHETIC"
    assert manifest["collector_origin"] == "SYNTHETIC"
    assert manifest["cluster_measurement_status"] == "NOT_EXECUTED"
    assert manifest["manual_review_status"] == "NOT_APPLICABLE"

    status = json.loads((result_dir / "report" / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "SYNTHETIC_COMPLETED"
    assert status["cluster_measurement_status"] == "NOT_EXECUTED"

    # Manual review is strictly prohibited on synthetic runs
    with pytest.raises(ValueError, match="illegal manual-review state transition"):
        record_manual_review(
            result_dir,
            reviewer_id="adversary",
            decision="APPROVED",
            reason="Adversarial attempt to approve synthetic evidence",
        )


def test_forced_observed_status_on_disk_fails_closed(tmp_path, monkeypatch):
    from evaluation_v5.resource import runner

    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "0" * 40, "git_dirty": False})
    result_dir = tmp_path / "forged-run"

    run_calibration(
        result_dir=result_dir,
        run_id="forged-run",
        adapter=FakeAdapter(),
        image=IMAGE,
        enforce_readiness=False,
    )

    manifest_path = result_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Forgery attempt 1: Set execution_status = OBSERVED
    manifest["execution_status"] = "OBSERVED"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="authenticated 'REAL_KUBERNETES_COLLECTOR' origin"):
        validate_evidence_package(result_dir, allow_unsealed=True)

    # Forgery attempt 2: Also set collector_origin = REAL_KUBERNETES_COLLECTOR
    manifest["collector_origin"] = "REAL_KUBERNETES_COLLECTOR"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="environment provenance lacks authenticated"):
        validate_evidence_package(result_dir, allow_unsealed=True)

    # Forgery attempt 3: Also edit environment.json to claim real collector origin
    env_path = result_dir / "raw" / "environment.json"
    env = json.loads(env_path.read_text(encoding="utf-8"))
    env["collector_origin"] = "REAL_KUBERNETES_COLLECTOR"
    env["cluster_measurement_status"] = "OBSERVED"
    env_path.write_text(json.dumps(env, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="synthetic environment marker present|lacks required kubernetes_version"):
        validate_evidence_package(result_dir, allow_unsealed=True)


def test_readiness_enforcement_blocks_synthetic_and_allows_real(tmp_path, monkeypatch):
    from evaluation_v5.resource import runner

    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "0" * 40, "git_dirty": False})
    result_dir = tmp_path / "readiness-blocked"

    # Synthetic adapter with enforce_readiness=True must fail closed
    with pytest.raises(RuntimeError, match="AUTHENTICATED_REAL_KUBERNETES_COLLECTOR_REQUIRED"):
        run_calibration(
            result_dir=result_dir,
            run_id="readiness-blocked",
            adapter=FakeAdapter(),
            image=IMAGE,
            enforce_readiness=True,
        )


def test_manual_review_approval_requires_observed_real_evidence(tmp_path, monkeypatch):
    from evaluation_v5.resource import runner

    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "0" * 40, "git_dirty": False})
    result_dir = tmp_path / "valid-observed"

    # Authenticated test adapter produces OBSERVED package
    report = run_calibration(
        result_dir=result_dir,
        run_id="valid-observed",
        adapter=make_authenticated_test_adapter(),
        image=IMAGE,
        enforce_readiness=False,
    )
    assert report["execution_status"] == "OBSERVED"

    # Manual review succeeds on genuine observed package
    reviewed = record_manual_review(
        result_dir,
        reviewer_id="authorized-reviewer",
        decision="APPROVED",
        reason="Genuine manual verification",
    )
    assert reviewed["sealed"] is True
    assert reviewed["eligible_for_comparison"] is True


def test_efficiency_runner_authenticity_and_analysis_handoff(tmp_path):
    from tests.test_resource_efficiency_v5 import (
        SuccessfulExecutionAdapter,
        _plan_with_counting_adapters,
    )

    plan, _, _ = _plan_with_counting_adapters()
    raw_root = tmp_path / "efficiency-raw-synthetic"

    # Synthetic adapter execution produces SYNTHETIC package
    execute_plan(
        root=raw_root,
        run_id="eff-synthetic",
        plan=plan,
        adapter=SuccessfulExecutionAdapter(),
        enforce_readiness=False,
    )

    raw_manifest = json.loads((raw_root / "manifest.json").read_text(encoding="utf-8"))
    assert raw_manifest["execution_status"] == "SYNTHETIC"
    assert raw_manifest["collector_origin"] == "SYNTHETIC"
    assert raw_manifest["cluster_measurement_status"] == "NOT_EXECUTED"

    # Raw validation succeeds with SYNTHETIC status
    validated_raw = validate_raw_package(raw_root)
    assert validated_raw["execution_status"] == "SYNTHETIC"

    # Comparative analysis MUST reject synthetic raw package
    analysis_root = tmp_path / "efficiency-analysis"
    with pytest.raises(ValueError, match="analysis requires an observed raw package"):
        write_analysis_package(
            raw_root=raw_root,
            analysis_root=analysis_root,
            oracle_root=tmp_path / "oracle",
        )


def test_all_bounded_legacy_packages_registered_and_tamper_resistant():
    assert len(BOUNDED_LEGACY_E4_PACKAGES) == 8
    base = Path("results_v5/protocol-v5.0.0/E4")

    for name, meta in BOUNDED_LEGACY_E4_PACKAGES.items():
        pkg_path = base / name
        assert pkg_path.is_dir(), f"Legacy package {name} must exist"
        assert is_bounded_legacy_package(pkg_path)
        legacy_meta = get_bounded_legacy_metadata(pkg_path)
        assert legacy_meta["sha256sums_digest"] == meta["sha256sums_digest"]

        # Integrity verification passes
        verified = verify_bounded_legacy_integrity(pkg_path)
        assert verified["status"] == "pass"
        assert verified["claim_eligible"] is False


def test_all_20_e4_directories_in_repository_validate():
    base = Path("results_v5/protocol-v5.0.0/E4")
    directories = sorted(p for p in base.iterdir() if p.is_dir())
    assert len(directories) == 20, f"Expected 20 E4 directories, found {len(directories)}"

    for p in directories:
        if (p / "report" / "pareto.json").exists():
            res = validate_analysis_package(p)
            assert res["status"] == "pass"
        elif (p / "plan.json").exists() and (p / "raw" / "decisions.jsonl").exists():
            res = validate_raw_package(p)
            assert res["status"] == "pass"
        elif (p / "plan.json").exists() and (p / "SHA256SUMS").exists() and not (p / "raw").exists():
            res = load_plan_package(p)
            assert "plan_sha256" in res
        elif (p / "manifest.json").exists():
            res = validate_evidence_package(p)
            assert res["status"] == "pass"
        else:
            pytest.fail(f"Unrecognized E4 package layout: {p.name}")
