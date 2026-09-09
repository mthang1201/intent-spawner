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
    CollectorExecutionResult,
    CollectorImplementationAssessment,
    ResourceCollectorOutcome,
    _mint_production_execution_result,
    authenticate_adapter,
    validate_collection_outcome,
    validate_collector_implementation,
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


def test_real_trial_adapter_constructor_is_not_observed_outcome():
    """P11-R7: Instantiating real trial adapter establishes implementation eligibility only, never OBSERVED outcome."""
    adapter = KubernetesTrialAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)
    assert impl.is_production_implementation is True
    assert impl.is_authenticated_real_collector is True
    assert impl.declared_collector_origin == COLLECTOR_ORIGIN_REAL_KUBERNETES
    # Implementation assessment alone must NOT derive or authorize OBSERVED
    assert impl.derived_execution_status == "UNEXECUTED"
    assert impl.derived_execution_status != "OBSERVED"
    assert impl.derived_cluster_measurement_status == "NOT_EXECUTED"
    assert impl.derived_cluster_measurement_status != "OBSERVED"

    # Outcome evaluation with 0 trials yields NOT_EXECUTED, never OBSERVED
    outcome = validate_collection_outcome(
        implementation=impl,
        environment=None,
        observations_or_trials=[],
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == "NOT_EXECUTED"
    assert outcome.cluster_measurement_status == "NOT_EXECUTED"


def test_real_efficiency_adapter_constructor_is_not_observed_outcome():
    """P11-R7: Instantiating real efficiency adapter establishes implementation eligibility only, never OBSERVED outcome."""
    adapter = KubernetesResourceEfficiencyAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)
    assert impl.is_production_implementation is True
    assert impl.is_authenticated_real_collector is True
    assert impl.declared_collector_origin == COLLECTOR_ORIGIN_REAL_KUBERNETES
    assert impl.derived_execution_status == "UNEXECUTED"
    assert impl.derived_execution_status != "OBSERVED"
    assert impl.derived_cluster_measurement_status == "NOT_EXECUTED"
    assert impl.derived_cluster_measurement_status != "OBSERVED"

    outcome = validate_collection_outcome(
        implementation=impl,
        environment=None,
        observations_or_trials=[],
        is_efficiency=True,
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == "NOT_EXECUTED"
    assert outcome.cluster_measurement_status == "NOT_EXECUTED"


def test_environment_provenance_before_collection_cannot_authenticate_observation():
    """P11-R7: Environment provenance captured before collection cannot authenticate observation."""
    adapter = KubernetesTrialAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)
    mock_env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "real-cluster-env-xyz",
        "read_only_preflight": {
            "failure_codes": [],
            "kubernetes_cluster": "real-k8s-cluster",
            "kubernetes_version": "v1.28.0",
        },
        "hardware_measurements": {
            "node_name": "worker-node-1",
            "node_uid": "00000000-1111-2222-3333-444444444444",
        },
        "cgroup_measurements": {
            "cgroup_version": "v2",
        },
    }
    # No trials have executed yet
    outcome = validate_collection_outcome(
        implementation=impl,
        environment=mock_env,
        observations_or_trials=[],
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == "NOT_EXECUTED"
    assert outcome.cluster_measurement_status == "NOT_EXECUTED"
    assert "NO_OBSERVATIONS_RECORDED" in outcome.failure_reasons


def test_failed_real_collector_preflight_never_yields_observed():
    """P11-R7: Failed collector preflight fails closed and never authorizes OBSERVED."""
    adapter = KubernetesTrialAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)
    failed_env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "failed-cluster-env",
        "read_only_preflight": {
            "failure_codes": ["CLUSTER_UNAVAILABLE", "PREFLIGHT_TIMEOUT"],
        },
    }
    # Even if trial records are passed, failed preflight must reject OBSERVED
    mock_trials = [{"run_id": "trial-1", "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES}]
    outcome = validate_collection_outcome(
        implementation=impl,
        environment=failed_env,
        observations_or_trials=mock_trials,
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status != "OBSERVED"
    assert outcome.execution_status == "FAILED"
    assert any("PREFLIGHT" in r for r in outcome.failure_reasons)


def test_real_collector_missing_runtime_identity_never_yields_observed():
    """P11-R7: Real collector trials missing runtime identity (pod_uid, node_uid, cgroup_v2) never yield OBSERVED."""
    import hashlib
    adapter = KubernetesTrialAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)

    valid_env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {
            "node_name": "worker-node-1",
            "node_uid": "00000000-1111-2222-3333-444444444444",
        },
        "cgroup_measurements": {"cgroup_version": "v2"},
    }

    run_id = "run-001"
    valid_pod_name = "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]

    def _make_trial(overrides=None):
        t = {
            "run_id": run_id,
            "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
            "kubernetes": {
                "pod_name": valid_pod_name,
                "pod_uid": "12345678-abcd-ef01-2345-6789abcdef01",
                "node_name": "worker-node-1",
                "started_at": "2026-03-01T00:01:00.000000Z",
                "finished_at": "2026-03-01T00:02:00.000000Z",
            },
            "cgroup_version": "v2",
            "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
        }
        if overrides:
            for k, v in overrides.items():
                if isinstance(v, dict) and isinstance(t.get(k), dict):
                    t[k].update(v)
                else:
                    t[k] = v
        return t

    # 1. Missing pod_uid
    t_no_pod_uid = _make_trial({"kubernetes": {"pod_uid": ""}})
    out1 = validate_collection_outcome(implementation=impl, environment=valid_env, observations_or_trials=[t_no_pod_uid])
    assert out1.is_observed_eligible is False
    assert out1.execution_status != "OBSERVED"

    # 2. Synthetic token in pod_uid
    t_fake_pod_uid = _make_trial({"kubernetes": {"pod_uid": "12345678-abcd-fake-2345-6789abcdef01"}})
    out2 = validate_collection_outcome(implementation=impl, environment=valid_env, observations_or_trials=[t_fake_pod_uid])
    assert out2.is_observed_eligible is False
    assert out2.execution_status != "OBSERVED"

    # 3. Pod name mismatch (does not match deterministic hash)
    t_bad_pod_name = _make_trial({"kubernetes": {"pod_name": "e4-mismatched-pod-name"}})
    out3 = validate_collection_outcome(implementation=impl, environment=valid_env, observations_or_trials=[t_bad_pod_name])
    assert out3.is_observed_eligible is False
    assert out3.execution_status != "OBSERVED"

    # 4. Node name mismatch
    t_bad_node = _make_trial({"kubernetes": {"node_name": "other-node"}})
    out4 = validate_collection_outcome(implementation=impl, environment=valid_env, observations_or_trials=[t_bad_node])
    assert out4.is_observed_eligible is False
    assert out4.execution_status != "OBSERVED"

    # 5. Invalid cgroup version (v1)
    t_cgroup_v1 = _make_trial({"cgroup_version": "v1"})
    out5 = validate_collection_outcome(implementation=impl, environment=valid_env, observations_or_trials=[t_cgroup_v1])
    assert out5.is_observed_eligible is False
    assert out5.execution_status != "OBSERVED"


def test_observed_status_derives_from_validated_collection_outcome_not_adapter_identity():
    """P11-R7: OBSERVED status derives strictly from validated collection outcome, never adapter identity alone."""
    import hashlib
    adapter = KubernetesTrialAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)
    # The adapter identity alone has NO authority to declare OBSERVED
    assert impl.derived_execution_status != "OBSERVED"

    valid_env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {
            "node_name": "worker-node-1",
            "node_uid": "00000000-1111-2222-3333-444444444444",
        },
        "cgroup_measurements": {"cgroup_version": "v2"},
    }

    run_id = "run-cal-01"
    valid_pod_name = "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    valid_trial = {
        "run_id": run_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": valid_pod_name,
            "pod_uid": "12345678-abcd-ef01-2345-6789abcdef01",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }

    exec_result = _mint_production_execution_result(
        collector_implementation="cluster_evaluation.resource_adapter_v5.KubernetesTrialAdapter",
        collector_version="protocol-v5-kubernetes-trial-adapter-v1.2.0",
        environment=valid_env,
        trials=[valid_trial],
    )
    # P11-R10 Attack A: Direct factory/helper call lacks execution authority and cannot mint OBSERVED
    assert exec_result.is_production_authorized is False
    assert exec_result.authority_origin == COLLECTOR_ORIGIN_SYNTHETIC
    outcome = validate_collection_outcome(
        implementation=impl,
        environment=valid_env,
        observations_or_trials=[valid_trial],
        execution_result=exec_result,
        expected_trial_count=1,
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == COLLECTOR_ORIGIN_SYNTHETIC
    assert outcome.cluster_measurement_status == "NOT_EXECUTED"
    assert "CALLER_FABRICATED_DATA_LACKS_EXECUTION_AUTHORITY" in outcome.failure_reasons


def test_test_fixture_can_validate_real_outcome_schema_without_becoming_observed():
    """P11-R7: Test fixture / synthetic adapter validating real outcome schema can NEVER become OBSERVED."""
    import hashlib
    fake_adapter = FakeAdapter()
    impl = validate_collector_implementation(fake_adapter)
    assert impl.is_production_implementation is False
    assert impl.declared_collector_origin == COLLECTOR_ORIGIN_SYNTHETIC

    run_id = "fixture-run-01"
    valid_pod_name = "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    trial = {
        "run_id": run_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": valid_pod_name,
            "pod_uid": "12345678-abcd-ef01-2345-6789abcdef01",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }
    env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "environment_id": "cluster-env",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {"node_name": "worker-node-1", "node_uid": "00000000-1111-2222-3333-444444444444"},
        "cgroup_measurements": {"cgroup_version": "v2"},
    }

    outcome = validate_collection_outcome(
        implementation=impl,
        environment=env,
        observations_or_trials=[trial],
    )
    # Synthetic implementation CANNOT be laundered into OBSERVED
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == COLLECTOR_ORIGIN_SYNTHETIC
    assert outcome.execution_status != "OBSERVED"
    assert outcome.cluster_measurement_status == "NOT_EXECUTED"


def test_exact_trial_adapter_plus_fabricated_valid_observations_cannot_mint_observed():
    """P11-R8: Exact production trial adapter + caller-fabricated valid observations cannot mint OBSERVED."""
    import hashlib
    adapter = KubernetesTrialAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)
    assert impl.is_production_implementation is True

    valid_env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {
            "node_name": "worker-node-1",
            "node_uid": "00000000-1111-2222-3333-444444444444",
        },
        "cgroup_measurements": {"cgroup_version": "v2"},
    }
    run_id = "trial-fab-01"
    valid_pod_name = "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    valid_trial = {
        "run_id": run_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": valid_pod_name,
            "pod_uid": "12345678-abcd-ef01-2345-6789abcdef01",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }

    # Case A: adapter.produce_execution_result() without actual trial execution
    adapter_result = adapter.produce_execution_result()
    assert adapter_result.is_production_authorized is False

    outcome_a = validate_collection_outcome(
        implementation=impl,
        environment=valid_env,
        observations_or_trials=[valid_trial],
        execution_result=adapter_result,
        expected_trial_count=1,
    )
    assert outcome_a.is_observed_eligible is False
    assert outcome_a.execution_status == "SYNTHETIC"
    assert outcome_a.cluster_measurement_status == "NOT_EXECUTED"
    assert "CALLER_FABRICATED_DATA_LACKS_EXECUTION_AUTHORITY" in outcome_a.failure_reasons

    # Case B: Calling validate_collection_outcome directly without execution_result
    outcome_b = validate_collection_outcome(
        implementation=impl,
        environment=valid_env,
        observations_or_trials=[valid_trial],
        expected_trial_count=1,
    )
    assert outcome_b.is_observed_eligible is False
    assert outcome_b.execution_status == "SYNTHETIC"
    assert outcome_b.cluster_measurement_status == "NOT_EXECUTED"
    assert "CALLER_FABRICATED_DATA_LACKS_EXECUTION_AUTHORITY" in outcome_b.failure_reasons


def test_exact_efficiency_adapter_plus_fabricated_valid_observations_cannot_mint_observed():
    """P11-R8: Exact production efficiency adapter + caller-fabricated valid observations cannot mint OBSERVED."""
    import hashlib
    adapter = KubernetesResourceEfficiencyAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)
    assert impl.is_production_implementation is True

    valid_env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {
            "node_name": "worker-node-1",
            "node_uid": "00000000-1111-2222-3333-444444444444",
        },
        "cgroup_measurements": {"cgroup_version": "v2"},
    }
    run_id = "trial-eff-fab-01"
    valid_pod_name = "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    valid_trial = {
        "trial_id": run_id,
        "primary_trial_id": run_id,
        "run_id": run_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": valid_pod_name,
            "pod_uid": "12345678-abcd-ef01-2345-6789abcdef01",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
        "infrastructure_invalid": False,
    }

    adapter_result = adapter.produce_execution_result()
    assert adapter_result.is_production_authorized is False

    outcome = validate_collection_outcome(
        implementation=impl,
        environment=valid_env,
        observations_or_trials=[valid_trial],
        execution_result=adapter_result,
        expected_trial_count=1,
        is_efficiency=True,
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == "SYNTHETIC"
    assert outcome.cluster_measurement_status == "NOT_EXECUTED"
    assert "CALLER_FABRICATED_DATA_LACKS_EXECUTION_AUTHORITY" in outcome.failure_reasons


def test_valid_uuids_and_pod_names_are_not_execution_authority():
    """P11-R8: Valid UUIDs and pod names conform to schema but do NOT confer execution authority."""
    import uuid
    import hashlib
    run_id = "uuid-test-01"
    real_uuid = str(uuid.uuid4())
    node_uuid = str(uuid.uuid4())
    pod_name = "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]

    env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": node_uuid,
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {"node_name": "worker-node-1", "node_uid": node_uuid},
        "cgroup_measurements": {"cgroup_version": "v2"},
    }
    trial = {
        "run_id": run_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": pod_name,
            "pod_uid": real_uuid,
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }

    adapter = KubernetesTrialAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)

    # Direct instantiation of CollectorExecutionResult without internal authority token fails closed
    forged_result = CollectorExecutionResult(
        collector_implementation="cluster_evaluation.resource_adapter_v5.KubernetesTrialAdapter",
        collector_version="protocol-v5-kubernetes-trial-adapter-v1.2.0",
        environment=env,
        trials=(trial,),
        is_production_authorized=True,
        authority_origin=COLLECTOR_ORIGIN_REAL_KUBERNETES,
    )
    # The __post_init__ guard revokes is_production_authorized because _authority_token was not provided
    assert forged_result.is_production_authorized is False
    assert forged_result.authority_origin == COLLECTOR_ORIGIN_SYNTHETIC

    outcome = validate_collection_outcome(
        implementation=impl,
        environment=env,
        observations_or_trials=[trial],
        execution_result=forged_result,
        expected_trial_count=1,
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == "SYNTHETIC"


def test_valid_environment_dict_is_not_execution_authority():
    """P11-R8: A valid environment dictionary does not confer execution authority."""
    valid_env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {
            "node_name": "worker-node-1",
            "node_uid": "00000000-1111-2222-3333-444444444444",
        },
        "cgroup_measurements": {"cgroup_version": "v2"},
    }
    adapter = KubernetesTrialAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)

    # Presenting valid_env without authorized execution fails closed
    outcome = validate_collection_outcome(
        implementation=impl,
        environment=valid_env,
        observations_or_trials=[],
        execution_result=None,
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == "NOT_EXECUTED"


def test_direct_validate_collection_outcome_call_cannot_launder_fixture_into_observed():
    """P11-R8: Direct invocation of validate_collection_outcome cannot launder fixtures into OBSERVED."""
    import hashlib
    adapter = KubernetesTrialAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)

    run_id = "direct-call-01"
    valid_pod_name = "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    trial = {
        "run_id": run_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": valid_pod_name,
            "pod_uid": "12345678-abcd-ef01-2345-6789abcdef01",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }
    env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {"node_name": "worker-node-1", "node_uid": "00000000-1111-2222-3333-444444444444"},
        "cgroup_measurements": {"cgroup_version": "v2"},
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
    }

    # Direct call with no execution_result
    outcome1 = validate_collection_outcome(
        implementation=impl,
        environment=env,
        observations_or_trials=[trial],
        execution_result=None,
    )
    assert outcome1.is_observed_eligible is False
    assert outcome1.execution_status == "SYNTHETIC"

    # Direct call with execution_result having unverified token
    outcome2 = validate_collection_outcome(
        implementation=impl,
        environment=env,
        observations_or_trials=[trial],
        execution_result=CollectorExecutionResult(
            collector_implementation="cluster_evaluation.resource_adapter_v5.KubernetesTrialAdapter",
            collector_version="protocol-v5-kubernetes-trial-adapter-v1.2.0",
            environment=env,
            trials=(trial,),
            is_production_authorized=True,
            authority_origin="REAL_KUBERNETES_COLLECTOR",
            _authority_token="forged-token",
        ),
    )
    assert outcome2.is_observed_eligible is False
    assert outcome2.execution_status == "SYNTHETIC"


def test_only_collector_execution_result_can_enter_observed_candidate_path():
    """P11-R8: Only an authenticated CollectorExecutionResult with production authority can enter OBSERVED candidate path."""
    import hashlib
    adapter = KubernetesTrialAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)

    run_id = "candidate-path-01"
    valid_pod_name = "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    trial = {
        "run_id": run_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": valid_pod_name,
            "pod_uid": "12345678-abcd-ef01-2345-6789abcdef01",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }
    env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {
            "node_name": "worker-node-1",
            "node_uid": "00000000-1111-2222-3333-444444444444",
        },
        "cgroup_measurements": {"cgroup_version": "v2"},
    }

    # 1. Unauthenticated execution result -> rejected
    unauth_result = CollectorExecutionResult(
        collector_implementation="cluster_evaluation.resource_adapter_v5.KubernetesTrialAdapter",
        collector_version="protocol-v5-kubernetes-trial-adapter-v1.2.0",
        environment=env,
        trials=(trial,),
        is_production_authorized=False,
        authority_origin=COLLECTOR_ORIGIN_SYNTHETIC,
    )
    outcome_rejected = validate_collection_outcome(
        implementation=impl,
        environment=env,
        observations_or_trials=[trial],
        execution_result=unauth_result,
        expected_trial_count=1,
    )
    assert outcome_rejected.is_observed_eligible is False
    assert outcome_rejected.execution_status == "SYNTHETIC"

    # 2. Direct helper call cannot mint production authority -> rejected
    minted_result = _mint_production_execution_result(
        collector_implementation="cluster_evaluation.resource_adapter_v5.KubernetesTrialAdapter",
        collector_version="protocol-v5-kubernetes-trial-adapter-v1.2.0",
        environment=env,
        trials=[trial],
    )
    assert minted_result.is_production_authorized is False
    assert minted_result.authority_origin == COLLECTOR_ORIGIN_SYNTHETIC
    outcome_minted = validate_collection_outcome(
        implementation=impl,
        environment=env,
        observations_or_trials=[trial],
        execution_result=minted_result,
        expected_trial_count=1,
    )
    assert outcome_minted.is_observed_eligible is False
    assert outcome_minted.execution_status == "SYNTHETIC"
    assert "CALLER_FABRICATED_DATA_LACKS_EXECUTION_AUTHORITY" in outcome_minted.failure_reasons


def test_test_fixture_with_complete_real_schema_remains_test_only():
    """P11-R8: A test fixture conforming completely to the real production schema remains TEST_ONLY/SYNTHETIC."""
    import hashlib
    fake_adapter = FakeAdapter()
    impl = validate_collector_implementation(fake_adapter)
    assert impl.is_production_implementation is False

    run_id = "fixture-complete-01"
    pod_name = "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    trial = {
        "run_id": run_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": pod_name,
            "pod_uid": "12345678-abcd-ef01-2345-6789abcdef01",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }
    env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {"node_name": "worker-node-1", "node_uid": "00000000-1111-2222-3333-444444444444"},
        "cgroup_measurements": {"cgroup_version": "v2"},
    }

    # Even if paired with an authorized execution result, non-production implementation fails closed
    auth_result = _mint_production_execution_result(
        collector_implementation="tests.test_resource_envelope_v5.FakeAdapter",
        collector_version="fake-adapter-v1",
        environment=env,
        trials=[trial],
    )
    outcome = validate_collection_outcome(
        implementation=impl,
        environment=env,
        observations_or_trials=[trial],
        execution_result=auth_result,
        expected_trial_count=1,
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == COLLECTOR_ORIGIN_SYNTHETIC
    assert outcome.execution_status != "OBSERVED"
    assert outcome.cluster_measurement_status == "NOT_EXECUTED"


def test_direct_private_mint_helper_cannot_forge_production_execution_authority():
    """P11-R10 Attack A: Direct invocation of _mint_production_execution_result cannot forge production execution authority."""
    import hashlib
    adapter = KubernetesTrialAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)

    run_id = "attack-a-01"
    pod_name = "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    trial = {
        "run_id": run_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": pod_name,
            "pod_uid": "00000000-1111-2222-3333-444444444444",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }
    env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {"node_name": "worker-node-1", "node_uid": "00000000-1111-2222-3333-444444444444"},
        "cgroup_measurements": {"cgroup_version": "v2"},
    }

    minted = _mint_production_execution_result(
        collector_implementation="cluster_evaluation.resource_adapter_v5.KubernetesTrialAdapter",
        collector_version="protocol-v5-kubernetes-trial-adapter-v1.2.0",
        environment=env,
        trials=[trial],
    )
    assert minted.is_production_authorized is False
    assert minted.authority_origin == COLLECTOR_ORIGIN_SYNTHETIC

    outcome = validate_collection_outcome(
        implementation=impl,
        environment=env,
        observations_or_trials=[trial],
        execution_result=minted,
        expected_trial_count=1,
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == COLLECTOR_ORIGIN_SYNTHETIC
    assert outcome.cluster_measurement_status == "NOT_EXECUTED"
    assert "CALLER_FABRICATED_DATA_LACKS_EXECUTION_AUTHORITY" in outcome.failure_reasons


def test_trial_adapter_mutable_environment_and_trials_cannot_forge_execution_authority():
    """P11-R10 Attack B: Injecting mutable state on KubernetesTrialAdapter cannot forge production execution authority."""
    import hashlib
    adapter = KubernetesTrialAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)

    run_id = "attack-b-trial-01"
    pod_name = "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    trial = {
        "run_id": run_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": pod_name,
            "pod_uid": "00000000-1111-2222-3333-444444444444",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }
    env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {"node_name": "worker-node-1", "node_uid": "00000000-1111-2222-3333-444444444444"},
        "cgroup_measurements": {"cgroup_version": "v2"},
    }

    # Attack: caller populates mutable attributes without running collector methods
    adapter._environment = env
    adapter._executed_trials = [trial]

    res = adapter.produce_execution_result()
    assert res.is_production_authorized is False
    assert res.authority_origin == COLLECTOR_ORIGIN_SYNTHETIC

    outcome = validate_collection_outcome(
        implementation=impl,
        environment=env,
        observations_or_trials=[trial],
        execution_result=res,
        expected_trial_count=1,
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == COLLECTOR_ORIGIN_SYNTHETIC
    assert outcome.cluster_measurement_status == "NOT_EXECUTED"
    assert "CALLER_FABRICATED_DATA_LACKS_EXECUTION_AUTHORITY" in outcome.failure_reasons


def test_efficiency_adapter_mutable_environment_and_trials_cannot_forge_execution_authority():
    """P11-R10 Attack B: Injecting mutable state on KubernetesResourceEfficiencyAdapter cannot forge authority."""
    import hashlib
    adapter = KubernetesResourceEfficiencyAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)

    trial_id = "attack-b-eff-01"
    pod_name = "e4e-" + hashlib.sha256(trial_id.encode("utf-8")).hexdigest()[:24]
    trial = {
        "trial_id": trial_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": pod_name,
            "pod_uid": "00000000-1111-2222-3333-444444444444",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }
    env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {"node_name": "worker-node-1", "node_uid": "00000000-1111-2222-3333-444444444444"},
        "cgroup_measurements": {"cgroup_version": "v2"},
    }

    # Attack: inject mutable adapter attributes on efficiency adapter
    adapter._environment = env
    adapter._executed_trials = [trial]

    res = adapter.produce_execution_result()
    assert res.is_production_authorized is False
    assert res.authority_origin == COLLECTOR_ORIGIN_SYNTHETIC

    outcome = validate_collection_outcome(
        implementation=impl,
        environment=env,
        observations_or_trials=[trial],
        execution_result=res,
        expected_trial_count=1,
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == COLLECTOR_ORIGIN_SYNTHETIC
    assert outcome.cluster_measurement_status == "NOT_EXECUTED"
    assert "CALLER_FABRICATED_DATA_LACKS_EXECUTION_AUTHORITY" in outcome.failure_reasons


def test_valid_internal_state_shapes_are_not_execution_proof():
    """P11-R10: Valid dictionary/internal state shapes alone are not execution proof."""
    import hashlib
    adapter = KubernetesTrialAdapter(image=IMAGE)

    run_id = "valid-shape-01"
    pod_name = "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    trial = {
        "run_id": run_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": pod_name,
            "pod_uid": "00000000-1111-2222-3333-444444444444",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }
    env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {"node_name": "worker-node-1", "node_uid": "00000000-1111-2222-3333-444444444444"},
        "cgroup_measurements": {"cgroup_version": "v2"},
    }

    # Populate valid internal state shapes on the adapter
    adapter._environment = dict(env)
    adapter._executed_trials = [dict(trial)]

    res = adapter.produce_execution_result()
    assert res.is_production_authorized is False
    assert res.authority_origin == COLLECTOR_ORIGIN_SYNTHETIC

    # Outcome rejects shaped state without execution authority
    outcome = validate_collection_outcome(
        implementation=validate_collector_implementation(adapter),
        environment=env,
        observations_or_trials=[trial],
        execution_result=res,
        expected_trial_count=1,
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == COLLECTOR_ORIGIN_SYNTHETIC
    assert "CALLER_FABRICATED_DATA_LACKS_EXECUTION_AUTHORITY" in outcome.failure_reasons


def test_produce_execution_result_requires_actual_collector_execution():
    """P11-R10: produce_execution_result requires actual collector execution lifecycle."""
    adapter = KubernetesTrialAdapter(image=IMAGE)

    # 1. Unexecuted adapter produces unauthorized result
    res_unexec = adapter.produce_execution_result()
    assert res_unexec.is_production_authorized is False
    assert res_unexec.authority_origin == COLLECTOR_ORIGIN_SYNTHETIC

    # 2. Outside caller attempting to record into session directly is ignored
    adapter._session.record_preflight_execution({"fake": "env"})
    adapter._session.record_trial_execution(None, {"fake": "trial"})
    assert adapter._session.is_authorized() is False

    # 3. Direct assignment to session attributes is blocked
    with pytest.raises(AttributeError, match="Direct assignment"):
        adapter._session._lifecycle_preflight_recorded = True

    # 4. A subclass override cannot record lifecycle authority
    from evaluation_v5.resource.authenticity import CollectorExecutionSession
    class SubclassAdapter(KubernetesTrialAdapter):
        def _preflight(self):
            self._environment = {"key": "value"}
            self._session.record_preflight_execution(self._environment)
            return self._environment

        def run_trial(self, spec=None):
            obs = {"trial": 1}
            self._executed_trials = [obs]
            self._session.record_trial_execution(spec, obs)
            return obs

    sub_adapter = SubclassAdapter.__new__(SubclassAdapter)
    sub_adapter._session = CollectorExecutionSession(sub_adapter)
    sub_adapter._preflight()
    sub_adapter.run_trial()
    assert sub_adapter._session.is_authorized() is False
    assert sub_adapter._session._lifecycle_preflight_recorded is False
    assert sub_adapter._session._lifecycle_trials_recorded is False

    # 5. Attack C: Tampering with adapter state relative to session state fails closed
    session = adapter._session
    env = {"key": "value"}
    trial = {"trial": 1}
    object.__setattr__(session, "_preflight_environment", env)
    object.__setattr__(session, "_lifecycle_preflight_recorded", True)
    object.__setattr__(session, "_executed_trials", (trial,))
    object.__setattr__(session, "_lifecycle_trials_recorded", True)
    adapter._environment = env
    adapter._executed_trials = [trial]

    # Matching state is authorized:
    assert session.is_authorized() is True

    # Attack C1: Tamper with adapter._environment -> authorization revoked
    adapter._environment = {"key": "value", "tampered": True}
    assert session.is_authorized() is False

    # Attack C2: Injected trial into adapter._executed_trials -> authorization revoked
    adapter._environment = env
    adapter._executed_trials.append({"trial": 2, "injected": True})
    assert session.is_authorized() is False


def test_direct_execution_result_factory_cannot_launder_fixture_into_observed():
    """P11-R10: Direct CollectorExecutionResult factory invocation cannot launder fixtures into OBSERVED."""
    import hashlib
    adapter = KubernetesTrialAdapter(image=IMAGE)
    impl = validate_collector_implementation(adapter)

    run_id = "launder-01"
    pod_name = "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    trial = {
        "run_id": run_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": pod_name,
            "pod_uid": "00000000-1111-2222-3333-444444444444",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }
    env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {"node_name": "worker-node-1", "node_uid": "00000000-1111-2222-3333-444444444444"},
        "cgroup_measurements": {"cgroup_version": "v2"},
    }

    # Attempt direct instantiation claiming production authority
    direct_res = CollectorExecutionResult(
        collector_implementation="cluster_evaluation.resource_adapter_v5.KubernetesTrialAdapter",
        collector_version="protocol-v5-kubernetes-trial-adapter-v1.2.0",
        environment=env,
        trials=(trial,),
        is_production_authorized=True,
        authority_origin=COLLECTOR_ORIGIN_REAL_KUBERNETES,
    )
    assert direct_res.is_production_authorized is False
    assert direct_res.authority_origin == COLLECTOR_ORIGIN_SYNTHETIC

    outcome_direct = validate_collection_outcome(
        implementation=impl,
        environment=env,
        observations_or_trials=[trial],
        execution_result=direct_res,
        expected_trial_count=1,
    )
    assert outcome_direct.is_observed_eligible is False
    assert outcome_direct.execution_status == COLLECTOR_ORIGIN_SYNTHETIC
    assert outcome_direct.cluster_measurement_status == "NOT_EXECUTED"

    # Attempt private helper mint claiming production authority
    helper_res = _mint_production_execution_result(
        collector_implementation="cluster_evaluation.resource_adapter_v5.KubernetesTrialAdapter",
        collector_version="protocol-v5-kubernetes-trial-adapter-v1.2.0",
        environment=env,
        trials=[trial],
    )
    assert helper_res.is_production_authorized is False
    assert helper_res.authority_origin == COLLECTOR_ORIGIN_SYNTHETIC

    outcome_helper = validate_collection_outcome(
        implementation=impl,
        environment=env,
        observations_or_trials=[trial],
        execution_result=helper_res,
        expected_trial_count=1,
    )
    assert outcome_helper.is_observed_eligible is False
    assert outcome_helper.execution_status == COLLECTOR_ORIGIN_SYNTHETIC
    assert outcome_helper.cluster_measurement_status == "NOT_EXECUTED"


def test_same_name_run_trial_frame_cannot_record_trial_authority():
    """P11-R12 Attack A: Caller-defined function named 'run_trial' cannot record lifecycle authority."""
    adapter = KubernetesTrialAdapter(image=IMAGE)
    session = adapter._session

    def run_trial(self, session, spec, obs):
        session.record_trial_execution(spec, obs)

    trial_obs = {
        "run_id": "spoof-trial-01",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": "e4-000000000000000000000000",
            "pod_uid": "00000000-1111-2222-3333-444444444444",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }

    run_trial(adapter, session, None, trial_obs)
    assert session._lifecycle_trials_recorded is False
    assert session._executed_trials == ()
    assert session.is_authorized() is False
    assert session.produce_result().is_production_authorized is False

    # Repeat for KubernetesResourceEfficiencyAdapter
    eff_adapter = KubernetesResourceEfficiencyAdapter(image=IMAGE)
    eff_session = eff_adapter._session
    run_trial(eff_adapter, eff_session, None, trial_obs)
    assert eff_session._lifecycle_trials_recorded is False
    assert eff_session._executed_trials == ()
    assert eff_session.is_authorized() is False
    assert eff_session.produce_result().is_production_authorized is False


def test_same_name_preflight_frame_cannot_record_preflight_authority():
    """P11-R12 Attack B: Caller-defined function named '_preflight' cannot record lifecycle authority."""
    adapter = KubernetesTrialAdapter(image=IMAGE)
    session = adapter._session

    def _preflight(self, session, env):
        session.record_preflight_execution(env)

    env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {"node_name": "worker-node-1", "node_uid": "00000000-1111-2222-3333-444444444444"},
        "cgroup_measurements": {"cgroup_version": "v2"},
    }

    _preflight(adapter, session, env)
    assert session._lifecycle_preflight_recorded is False
    assert session._preflight_environment is None
    assert session.is_authorized() is False
    assert session.produce_result().is_production_authorized is False

    # Repeat for KubernetesResourceEfficiencyAdapter
    eff_adapter = KubernetesResourceEfficiencyAdapter(image=IMAGE)
    eff_session = eff_adapter._session
    _preflight(eff_adapter, eff_session, env)
    assert eff_session._lifecycle_preflight_recorded is False
    assert eff_session._preflight_environment is None
    assert eff_session.is_authorized() is False
    assert eff_session.produce_result().is_production_authorized is False


def test_same_name_environment_provenance_frame_cannot_record_preflight_authority():
    """P11-R12 Attack B: Caller-defined function named 'environment_provenance' cannot record lifecycle authority."""
    adapter = KubernetesTrialAdapter(image=IMAGE)
    session = adapter._session

    def environment_provenance(self, session, env):
        session.record_preflight_execution(env)

    env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "eligibility_status": "ELIGIBLE",
    }

    environment_provenance(adapter, session, env)
    assert session._lifecycle_preflight_recorded is False
    assert session._preflight_environment is None
    assert session.is_authorized() is False
    assert session.produce_result().is_production_authorized is False


def test_combined_same_name_frame_spoof_cannot_mint_observed_trial_adapter():
    """P11-R12 Attack C: Combined same-name frame spoof on KubernetesTrialAdapter cannot mint OBSERVED."""
    import hashlib
    adapter = KubernetesTrialAdapter(image=IMAGE)
    session = adapter._session
    impl = validate_collector_implementation(adapter)

    run_id = "combined-spoof-trial-01"
    pod_name = "e4-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    trial = {
        "run_id": run_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": pod_name,
            "pod_uid": "00000000-1111-2222-3333-444444444444",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }
    env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {"node_name": "worker-node-1", "node_uid": "00000000-1111-2222-3333-444444444444"},
        "cgroup_measurements": {"cgroup_version": "v2"},
    }

    # Combined spoof attempt:
    def _preflight(self, env):
        session.record_preflight_execution(env)

    def run_trial(self, spec, obs):
        session.record_trial_execution(spec, obs)

    _preflight(adapter, env)
    run_trial(adapter, None, trial)

    adapter._environment = env
    adapter._executed_trials = [trial]

    res = adapter.produce_execution_result()
    assert res.is_production_authorized is False
    assert res.authority_origin == COLLECTOR_ORIGIN_SYNTHETIC

    outcome = validate_collection_outcome(
        implementation=impl,
        environment=env,
        observations_or_trials=[trial],
        execution_result=res,
        expected_trial_count=1,
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == COLLECTOR_ORIGIN_SYNTHETIC
    assert outcome.cluster_measurement_status == "NOT_EXECUTED"
    assert "CALLER_FABRICATED_DATA_LACKS_EXECUTION_AUTHORITY" in outcome.failure_reasons


def test_combined_same_name_frame_spoof_cannot_mint_observed_efficiency_adapter():
    """P11-R12 Attack C: Combined same-name frame spoof on KubernetesResourceEfficiencyAdapter cannot mint OBSERVED."""
    import hashlib
    adapter = KubernetesResourceEfficiencyAdapter(image=IMAGE)
    session = adapter._session
    impl = validate_collector_implementation(adapter)

    trial_id = "combined-spoof-eff-01"
    pod_name = "e4e-" + hashlib.sha256(trial_id.encode("utf-8")).hexdigest()[:24]
    trial = {
        "trial_id": trial_id,
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "kubernetes": {
            "pod_name": pod_name,
            "pod_uid": "00000000-1111-2222-3333-444444444444",
            "node_name": "worker-node-1",
            "started_at": "2026-03-01T00:01:00.000000Z",
            "finished_at": "2026-03-01T00:02:00.000000Z",
        },
        "cgroup_version": "v2",
        "cgroup_metrics": {"cgroup_version": "v2", "cpu_usage_usec": 1000},
    }
    env = {
        "schema_version": "protocol-v5-resource-environment-v1.1.0",
        "captured_at": "2026-03-01T00:00:00.000000Z",
        "environment_id": "intent-spawner-eval-v5:z2jh-context-demo",
        "collector_origin": COLLECTOR_ORIGIN_REAL_KUBERNETES,
        "cluster_measurement_status": "OBSERVED",
        "eligibility_status": "ELIGIBLE",
        "required_context": "intent-spawner-eval-v5",
        "namespace": "z2jh-context-demo",
        "kubernetes_version": {"major": "1", "minor": "28"},
        "node_name": "worker-node-1",
        "node_uid": "00000000-1111-2222-3333-444444444444",
        "kubelet_version": "v1.28.0",
        "container_runtime": "containerd://1.7.0",
        "kernel_version": "6.1.0",
        "operating_system": "linux",
        "architecture": "amd64",
        "read_only_preflight": {"failure_codes": []},
        "hardware_measurements": {"node_name": "worker-node-1", "node_uid": "00000000-1111-2222-3333-444444444444"},
        "cgroup_measurements": {"cgroup_version": "v2"},
    }

    def environment_provenance(self, env):
        session.record_preflight_execution(env)

    def run_trial(self, spec, obs):
        session.record_trial_execution(spec, obs)

    environment_provenance(adapter, env)
    run_trial(adapter, None, trial)

    adapter._environment = env
    adapter._executed_trials = [trial]

    res = adapter.produce_execution_result()
    assert res.is_production_authorized is False
    assert res.authority_origin == COLLECTOR_ORIGIN_SYNTHETIC

    outcome = validate_collection_outcome(
        implementation=impl,
        environment=env,
        observations_or_trials=[trial],
        execution_result=res,
        expected_trial_count=1,
    )
    assert outcome.is_observed_eligible is False
    assert outcome.execution_status == COLLECTOR_ORIGIN_SYNTHETIC
    assert outcome.cluster_measurement_status == "NOT_EXECUTED"
    assert "CALLER_FABRICATED_DATA_LACKS_EXECUTION_AUTHORITY" in outcome.failure_reasons


def test_lifecycle_authorization_requires_exact_blessed_method_code_object():
    """P11-R12 Attack D: Lifecycle authorization requires exact blessed method code object identity."""
    from evaluation_v5.resource.authenticity import (
        _blessed_preflight_code_objects,
        _blessed_trial_code_objects,
    )

    blessed_preflight = _blessed_preflight_code_objects()
    blessed_trial = _blessed_trial_code_objects()

    assert KubernetesTrialAdapter._preflight.__code__ in blessed_preflight
    assert KubernetesTrialAdapter.environment_provenance.__code__ in blessed_preflight
    assert KubernetesTrialAdapter.run_trial.__code__ in blessed_trial
    assert KubernetesResourceEfficiencyAdapter.run_trial.__code__ in blessed_trial

    # 1. Caller-created functions with identical names are rejected
    def _preflight(self, env): pass
    def environment_provenance(self): pass
    def run_trial(self, spec): pass

    assert _preflight.__code__ not in blessed_preflight
    assert environment_provenance.__code__ not in blessed_preflight
    assert run_trial.__code__ not in blessed_trial

    # 2. Subclass overrides are rejected
    class SubclassedTrialAdapter(KubernetesTrialAdapter):
        def _preflight(self): pass
        def run_trial(self, spec): pass

    assert SubclassedTrialAdapter._preflight.__code__ not in blessed_preflight
    assert SubclassedTrialAdapter.run_trial.__code__ not in blessed_trial

    # 3. Monkey-patched functions on instance are rejected
    adapter = KubernetesTrialAdapter(image=IMAGE)
    def monkeypatched_run(self, spec): pass
    adapter.run_trial = monkeypatched_run
    assert getattr(adapter.run_trial, "__code__", None) not in blessed_trial
