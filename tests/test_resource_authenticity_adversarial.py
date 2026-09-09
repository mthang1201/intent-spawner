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
from evaluation_v5.resource.efficiency_analysis import load_approved_oracle
from evaluation_v5.resource.efficiency_contracts import (
    EXECUTION_ORDER_ALGORITHM,
    FAMILY_COUNT,
)
from evaluation_v5.resource.efficiency_plan import (
    build_efficiency_plan,
    validate_efficiency_plan,
)
from evaluation_v5.resource.efficiency_runner import (
    execute_plan,
    write_analysis_package,
    write_not_executed,
)
from evaluation_v5.resource.runner import (
    create_dry_run_package,
    record_manual_review,
    run_calibration,
)
from tests.test_resource_envelope_v5 import (
    IMAGE,
    FakeAdapter,
)
from tests.test_resource_efficiency_v5 import (
    SuccessfulExecutionAdapter,
    _plan_with_counting_adapters,
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
    collector_origin = "REAL_KUBERNETES_COLLECTOR"


class SubclassedEfficiencyFakeAdapter(KubernetesResourceEfficiencyAdapter):
    """Adversarial efficiency adapter subclassing real adapter outside blessed modules."""

    adapter_version = "protocol-v5-kubernetes-efficiency-adapter-v1.0.0"
    collector_origin = "REAL_KUBERNETES_COLLECTOR"


def test_caller_injected_strings_and_subclasses_rejected():
    # 1. Plain injected duck-typed class
    spoofed = InjectedStringFakeAdapter()
    auth = authenticate_adapter(spoofed)
    assert not auth.is_authenticated_real_collector
    assert auth.collector_origin == COLLECTOR_ORIGIN_SYNTHETIC
    assert auth.derived_execution_status == "SYNTHETIC"
    assert auth.derived_cluster_measurement_status == "NOT_EXECUTED"

    # 2. Subclass outside blessed modules (trial adapter)
    subclassed = SubclassedFakeAdapter.__new__(SubclassedFakeAdapter)
    auth_sub = authenticate_adapter(subclassed)
    assert not auth_sub.is_authenticated_real_collector
    assert auth_sub.collector_origin == COLLECTOR_ORIGIN_SYNTHETIC

    # 3. Subclass outside blessed modules (efficiency adapter)
    subclassed_eff = SubclassedEfficiencyFakeAdapter.__new__(SubclassedEfficiencyFakeAdapter)
    auth_sub_eff = authenticate_adapter(subclassed_eff)
    assert not auth_sub_eff.is_authenticated_real_collector
    assert auth_sub_eff.collector_origin == COLLECTOR_ORIGIN_SYNTHETIC

    # 4. Uninitialized instance of real class created via __new__
    uninit_trial = KubernetesTrialAdapter.__new__(KubernetesTrialAdapter)
    assert not authenticate_adapter(uninit_trial).is_authenticated_real_collector

    uninit_eff = KubernetesResourceEfficiencyAdapter.__new__(KubernetesResourceEfficiencyAdapter)
    assert not authenticate_adapter(uninit_eff).is_authenticated_real_collector

    # 5. Method monkey-patching in __dict__ is detected and rejected
    patched_trial = KubernetesTrialAdapter.__new__(KubernetesTrialAdapter)
    patched_trial._initialized = True
    patched_trial.run_trial = lambda spec: {}
    assert not authenticate_adapter(patched_trial).is_authenticated_real_collector

    # 6. Arbitrary objects / primitives
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


def test_readiness_enforcement_blocks_synthetic(tmp_path, monkeypatch):
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


def test_manual_review_strictly_prohibits_synthetic_and_unobserved_packages(tmp_path, monkeypatch):
    from evaluation_v5.resource import runner

    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "0" * 40, "git_dirty": False})

    # 1. Synthetic run cannot be approved
    result_dir = tmp_path / "synthetic-review"
    run_calibration(
        result_dir=result_dir,
        run_id="synthetic-review",
        adapter=FakeAdapter(),
        image=IMAGE,
        enforce_readiness=False,
    )
    with pytest.raises(ValueError, match="illegal manual-review state transition"):
        record_manual_review(
            result_dir,
            reviewer_id="adversary",
            decision="APPROVED",
            reason="Adversarial attempt to approve synthetic evidence",
        )

    # 2. Dry-run package cannot be approved
    dry_dir = tmp_path / "dry-review"
    create_dry_run_package(
        result_dir=dry_dir,
        run_id="dry-review",
        image=IMAGE,
        unavailable_reason="test",
    )
    with pytest.raises((ValueError, FileExistsError)):
        record_manual_review(
            dry_dir,
            reviewer_id="adversary",
            decision="APPROVED",
            reason="Adversarial attempt to approve dry run",
        )


def test_hundreds_of_synthetic_trials_remain_synthetic_and_non_claimable(tmp_path, monkeypatch):
    """P11-R5.1: No quantity of synthetic trials can become observed, confirmatory, or claim-eligible."""
    from evaluation_v5.resource import runner

    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "0" * 40, "git_dirty": False})
    result_dir = tmp_path / "synthetic-scale"

    # Full calibration run executes multiple lattice searches across all families (96 trials)
    report = run_calibration(
        result_dir=result_dir,
        run_id="synthetic-scale-run",
        adapter=FakeAdapter(),
        image=IMAGE,
        enforce_readiness=False,
    )
    assert report["execution_status"] == "SYNTHETIC"
    assert report["eligible_for_comparison"] is False
    assert report["sealed"] is False

    raw_trials = [
        json.loads(line)
        for line in (result_dir / "raw" / "trials.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(raw_trials) >= 16
    for trial in raw_trials:
        assert trial["collector_origin"] == "SYNTHETIC"
        assert trial.get("cgroup_version") in (None, "v2")

    # Validation confirms non-claimability
    manifest = json.loads((result_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["execution_status"] == "SYNTHETIC"
    assert manifest["collector_origin"] == "SYNTHETIC"
    assert manifest["measurement_claims_permitted"] is False

    validated = validate_evidence_package(result_dir, allow_unsealed=True)
    assert validated["execution_status"] == "SYNTHETIC"
    assert validated["eligible_for_comparison"] is False

    # Manual review is strictly rejected
    with pytest.raises(ValueError, match="illegal manual-review state transition"):
        record_manual_review(
            result_dir,
            reviewer_id="adversary",
            decision="APPROVED",
            reason="Attempting to claim synthetic scale",
        )


def test_dry_run_remains_not_executed_and_non_claimable(tmp_path):
    """P11-R5.2: Calibration and efficiency dry runs remain NOT_EXECUTED and non-claimable."""
    # 1. Calibration dry run
    cal_dry = tmp_path / "cal-dry"
    cal_report = create_dry_run_package(
        result_dir=cal_dry,
        run_id="cal-dry-run",
        image=IMAGE,
        unavailable_reason="Disposable cluster unavailable in test environment",
    )
    assert cal_report["execution_status"] == "DRY_RUN"
    assert cal_report["eligible_for_comparison"] is False
    cal_manifest = json.loads((cal_dry / "manifest.json").read_text(encoding="utf-8"))
    assert cal_manifest["collector_origin"] == "DRY_RUN"
    assert cal_manifest["cluster_measurement_status"] == "NOT_EXECUTED"
    assert cal_manifest["measurement_claims_permitted"] is False

    with pytest.raises((ValueError, FileExistsError)):
        record_manual_review(
            cal_dry,
            reviewer_id="adversary",
            decision="APPROVED",
            reason="Attempt to review dry run",
        )

    # 2. Efficiency dry run
    eff_dry = tmp_path / "eff-dry"
    eff_manifest = write_not_executed(
        root=eff_dry,
        run_id="eff-dry-run",
        image=IMAGE,
        reason="Disposable cluster unavailable in test environment",
    )
    assert eff_manifest["execution_status"] == "NOT_EXECUTED"
    assert eff_manifest["collector_origin"] == "NOT_EXECUTED"
    assert eff_manifest["cluster_measurement_status"] == "NOT_EXECUTED"
    validated_raw = validate_raw_package(eff_dry)
    assert validated_raw["execution_status"] == "NOT_EXECUTED"

    status_data = json.loads((eff_dry / "report" / "status.json").read_text(encoding="utf-8"))
    assert status_data["status"] == "NOT_EXECUTED"
    assert status_data["empirical_claims_permitted"] is False

    # Efficiency analysis rejects dry-run raw package
    with pytest.raises(ValueError, match="analysis requires an observed raw package"):
        write_analysis_package(
            raw_root=eff_dry,
            analysis_root=tmp_path / "eff-dry-analysis",
            oracle_root=tmp_path / "oracle",
        )


def test_zero_kubernetes_availability_fails_closed_or_emits_not_executed(tmp_path, monkeypatch):
    """P11-R5.3: In environments without Kubernetes, execution fails closed or emits NOT_EXECUTED."""
    from evaluation_v5.resource import runner

    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "0" * 40, "git_dirty": False})
    result_dir = tmp_path / "zero-k8s"

    # 1. Enforcing readiness on synthetic adapter fails closed
    with pytest.raises(RuntimeError, match="AUTHENTICATED_REAL_KUBERNETES_COLLECTOR_REQUIRED"):
        run_calibration(
            result_dir=result_dir / "cal",
            run_id="zero-k8s-cal",
            adapter=FakeAdapter(),
            image=IMAGE,
            enforce_readiness=True,
        )

    # 2. Real adapter without cluster fails closed on environment provenance preflight
    real_adapter = KubernetesTrialAdapter(image=IMAGE)
    with pytest.raises(RuntimeError, match="CLUSTER_INELIGIBLE"):
        real_adapter.environment_provenance()

    # 3. Explicit not-executed emit succeeds with NOT_EXECUTED status
    eff_dir = result_dir / "eff-not-executed"
    manifest = write_not_executed(
        root=eff_dir,
        run_id="zero-k8s-not-executed",
        image=IMAGE,
        reason="Disposable cluster unavailable in test runner",
    )
    assert manifest["execution_status"] == "NOT_EXECUTED"
    assert manifest["collector_origin"] == "NOT_EXECUTED"
    assert manifest["cluster_measurement_status"] == "NOT_EXECUTED"


def test_oracle_to_efficiency_handoff_rejects_synthetic_or_unapproved_oracle(tmp_path, monkeypatch):
    """P11-R5.4: Efficiency analysis rejects synthetic or unapproved calibration oracle packages."""
    from evaluation_v5.resource import runner

    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "0" * 40, "git_dirty": False})
    synthetic_oracle_dir = tmp_path / "synthetic-oracle"

    run_calibration(
        result_dir=synthetic_oracle_dir,
        run_id="syn-oracle",
        adapter=FakeAdapter(),
        image=IMAGE,
        enforce_readiness=False,
    )

    # Direct loader rejects synthetic calibration package
    with pytest.raises(ValueError, match="resource evidence package is not sealed|oracle must be a sealed manually approved"):
        load_approved_oracle(synthetic_oracle_dir)

    # Analysis handoff rejects synthetic raw package
    with pytest.raises(ValueError, match="comparative raw package is not sealed|analysis requires an observed raw package"):
        write_analysis_package(
            raw_root=synthetic_oracle_dir,
            analysis_root=tmp_path / "analysis",
            oracle_root=synthetic_oracle_dir,
        )


def test_efficiency_forged_collector_fails_authenticity_and_analysis_handoff(tmp_path):
    """P11-R5.5: Adversarial efficiency collector fails authenticity and handoff to analysis."""
    class AdversarialEfficiencyAdapter(KubernetesResourceEfficiencyAdapter):
        adapter_version = "protocol-v5-kubernetes-efficiency-adapter-v1.0.0"
        collector_origin = "REAL_KUBERNETES_COLLECTOR"

        def __init__(self):
            self._initialized = True
            self.image = IMAGE

        def environment_provenance(self):
            return {
                "schema_version": "protocol-v5-resource-environment-v1.1.0",
                "collector_origin": "REAL_KUBERNETES_COLLECTOR",
                "cluster_measurement_status": "OBSERVED",
                "environment_id": "adversarial-env",
            }

        def run_trial(self, spec):
            return SuccessfulExecutionAdapter().run_trial(spec)

    forged = AdversarialEfficiencyAdapter()
    auth = authenticate_adapter(forged)
    assert not auth.is_authenticated_real_collector
    assert auth.collector_origin == "SYNTHETIC"
    assert auth.derived_execution_status == "SYNTHETIC"
    assert auth.derived_cluster_measurement_status == "NOT_EXECUTED"

    plan, _, _ = _plan_with_counting_adapters()
    raw_root = tmp_path / "forged-raw"
    execute_plan(
        root=raw_root,
        run_id="forged-eff-run",
        plan=plan,
        adapter=forged,
        enforce_readiness=False,
    )

    manifest = json.loads((raw_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["execution_status"] == "SYNTHETIC"
    assert manifest["collector_origin"] == "SYNTHETIC"

    with pytest.raises(ValueError, match="analysis requires an observed raw package"):
        write_analysis_package(
            raw_root=raw_root,
            analysis_root=tmp_path / "forged-analysis",
            oracle_root=tmp_path / "oracle",
        )


def test_current_schema_strictness_versus_bounded_legacy_compatibility():
    """P11-R5.6: Current schema enforces frozen counts while bounded legacy packages validate read-only."""
    # 1. Current strict plan schema
    plan = build_efficiency_plan()
    validate_efficiency_plan(plan, allow_legacy=False)

    # Non-conforming independent_semantic_n fails under current schema
    mutated_plan = dict(plan)
    mutated_plan["independent_semantic_n"] = 12
    with pytest.raises(ValueError, match="lacks required independent_semantic_n"):
        validate_efficiency_plan(mutated_plan, allow_legacy=False)

    # Non-conforming execution_order_algorithm fails under current schema
    mutated_plan2 = dict(plan)
    mutated_plan2["execution_order_algorithm"] = "arbitrary_order"
    with pytest.raises(ValueError, match="lacks required execution_order_algorithm"):
        validate_efficiency_plan(mutated_plan2, allow_legacy=False)

    # 2. Bounded legacy packages validate with allow_legacy=True and claim_eligible=False
    base = Path("results_v5/protocol-v5.0.0/E4")
    assert len(BOUNDED_LEGACY_E4_PACKAGES) == 8
    for name, meta in BOUNDED_LEGACY_E4_PACKAGES.items():
        pkg_path = base / name
        assert is_bounded_legacy_package(pkg_path)
        meta_read = get_bounded_legacy_metadata(pkg_path)
        assert meta_read["sha256sums_digest"] == meta["sha256sums_digest"]

        integrity = verify_bounded_legacy_integrity(pkg_path)
        assert integrity["status"] == "pass"
        assert integrity["claim_eligible"] is False


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
