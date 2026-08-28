from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from cluster_evaluation.resource_adapter_v5 import (
    build_pod_spec, evaluate_cluster_eligibility,
)
from evaluation_v5.resource.comparison import classify_axis, compare_allocations
from evaluation_v5.resource.contracts import (
    load_cluster_policy, load_crosswalk, load_semantic_independence,
    static_independence_scan,
)
from evaluation_v5.resource.derive import (
    derive_safe_envelopes, reference_is_stable, wilson_interval,
)
from evaluation_v5.resource.evidence import validate_evidence_package
from evaluation_v5.resource.manifest import (
    CPU_LATTICE_M,
    MEMORY_LATTICE_MIB,
    load_resource_manifest,
    validate_resource_manifest,
    verify_workload_markers,
    workload_fingerprint,
)
from evaluation_v5.resource.models import TrialObservation
from evaluation_v5.resource.planner import build_calibration_plan, make_trial_spec
from evaluation_v5.resource.runner import (
    create_dry_run_package,
    record_manual_review,
    run_calibration,
)
from evaluation_v5.resource.workloads import execute_workload, verify_workload_result


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "example.invalid/intent-spawner-resource-v5@sha256:" + "a" * 64


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _metrics(cpu: int, memory: int, peak: float) -> dict:
    return {
        "source": "cgroup_v2_in_container", "cgroup_version": "v2",
        "controllers": ["cpu", "memory", "pids"],
        "memory_peak_mib": peak, "cpu_full_window_average_m": min(cpu, 100),
        "cpu_usage_usec_delta": 1000, "cpu_max": f"{cpu * 100} 100000",
        "memory_max": str(memory * 1024 * 1024), "memory_events_delta": {"oom": 0},
    }


def _row(
    workload: dict,
    phase: str,
    cpu: int,
    memory: int,
    repeat: int,
    *,
    runtime: float = 1.0,
    success: bool = True,
    infrastructure: bool = False,
    metrics: bool = True,
) -> TrialObservation:
    marker = workload["expected_marker_sha256"] if success else "f" * 64
    return TrialObservation(
        run_id=f"fixture-{workload['family_id']}-{phase}-{cpu}-{memory}-{repeat}",
        family_id=workload["family_id"], workload_instance_id=workload["workload_instance_id"],
        workload_fingerprint=workload_fingerprint(workload), phase=phase, cpu_m=cpu,
        memory_mib=memory, repeat_index=repeat,
        deterministic_seed=workload["deterministic_seed"],
        expected_marker_sha256=workload["expected_marker_sha256"],
        observed_marker_sha256=marker, exit_code=0 if success else 1,
        exit_reason="Completed" if success else "Error", oom_killed=False,
        timeout=False, runtime_seconds=runtime, correctness_marker_ok=success,
        correctness_invariants_ok=success, correctness_details={"fixture": success},
        infrastructure_invalid=infrastructure,
        exclusion_reason="synthetic_infrastructure_failure" if infrastructure else None,
        cgroup_version="v2" if metrics else None,
        cgroup_metrics=_metrics(cpu, memory, memory - 10) if metrics else {},
        kubernetes={}, replacement_of=None, recorded_at_utc=_now(),
    )


class FakeAdapter:
    adapter_version = "fake-resource-adapter-v1"

    def __init__(
        self,
        *,
        memory_threshold: int = 256,
        cpu_threshold: int = 500,
        interrupt_on_call: int | None = None,
        infrastructure_once: bool = False,
    ) -> None:
        self.memory_threshold = memory_threshold
        self.cpu_threshold = cpu_threshold
        self.interrupt_on_call = interrupt_on_call
        self.infrastructure_once = infrastructure_once
        self.calls = []
        self._failed = False

    def environment_provenance(self):
        return {
            "schema_version": "fixture",
            "environment_id": "synthetic-fake-adapter",
            "cgroup_requirement": "v2",
            "hardware_measurements": "synthetic_fixture_not_evidence",
        }

    def run_trial(self, spec):
        self.calls.append(spec)
        if self.interrupt_on_call == len(self.calls):
            raise KeyboardInterrupt("fixture interruption")
        if self.infrastructure_once and not self._failed:
            self._failed = True
            return TrialObservation(
                run_id=spec.run_id, family_id=spec.family_id, phase=spec.phase,
                workload_instance_id=spec.workload_instance_id,
                workload_fingerprint=spec.workload_fingerprint,
                cpu_m=spec.cpu_m, memory_mib=spec.memory_mib,
                repeat_index=spec.repeat_index, deterministic_seed=spec.deterministic_seed,
                expected_marker_sha256=spec.expected_marker_sha256,
                observed_marker_sha256=None, exit_code=None, exit_reason="FixtureInfra",
                oom_killed=False, timeout=False, runtime_seconds=None,
                correctness_marker_ok=False, infrastructure_invalid=True,
                correctness_invariants_ok=False, correctness_details={},
                exclusion_reason="fixture_infrastructure_failure", cgroup_version=None,
                cgroup_metrics={}, kubernetes={}, replacement_of=spec.replacement_of,
                recorded_at_utc=_now(),
            )
        enough_memory = spec.memory_mib >= self.memory_threshold
        enough_cpu = spec.cpu_m >= self.cpu_threshold
        runtime = 1.0 if enough_cpu else 2.0
        marker = spec.expected_marker_sha256 if enough_memory else None
        return TrialObservation(
            run_id=spec.run_id, family_id=spec.family_id, phase=spec.phase,
            workload_instance_id=spec.workload_instance_id,
            workload_fingerprint=spec.workload_fingerprint,
            cpu_m=spec.cpu_m, memory_mib=spec.memory_mib,
            repeat_index=spec.repeat_index, deterministic_seed=spec.deterministic_seed,
            expected_marker_sha256=spec.expected_marker_sha256,
            observed_marker_sha256=marker, exit_code=0 if enough_memory else 137,
            exit_reason="Completed" if enough_memory else "OOMKilled",
            oom_killed=not enough_memory, timeout=False,
            runtime_seconds=runtime if enough_memory else None,
            correctness_marker_ok=enough_memory, infrastructure_invalid=False,
            correctness_invariants_ok=enough_memory, correctness_details={"fixture": enough_memory},
            exclusion_reason=None, cgroup_version="v2",
            cgroup_metrics=_metrics(spec.cpu_m, spec.memory_mib, min(spec.memory_mib - 1, self.memory_threshold)),
            kubernetes={"cleanup_status": "succeeded"},
            replacement_of=spec.replacement_of, recorded_at_utc=_now(),
        )


class AlwaysInfrastructureAdapter(FakeAdapter):
    adapter_version = "fake-resource-always-infrastructure-v1"

    def run_trial(self, spec):
        row = super().run_trial(spec)
        return replace(
            row, observed_marker_sha256=None, exit_code=None, exit_reason="FixtureInfra",
            runtime_seconds=None, correctness_marker_ok=False, correctness_invariants_ok=False,
            correctness_details={}, infrastructure_invalid=True,
            exclusion_reason="fixture_infrastructure_failure", cgroup_version=None,
            cgroup_metrics={},
        )


@pytest.fixture(scope="module")
def manifest():
    return load_resource_manifest()


def test_manifest_has_sixteen_independent_bounded_families(manifest):
    assert len(manifest["workloads"]) == 16
    assert len({item["family_id"] for item in manifest["workloads"]}) == 16
    assert len({item["operation"] for item in manifest["workloads"]}) == 16
    assert manifest["candidate_lattices"] == {
        "memory_mib": MEMORY_LATTICE_MIB,
        "cpu_m": CPU_LATTICE_M,
    }
    encoded = json.dumps(manifest).lower()
    for forbidden in ("recommended_profile", "expected_minimum_profile", '"intent"', '"resource_oracle"'):
        assert forbidden not in encoded
    assert verify_workload_markers(manifest) == {"status": "pass", "verified_markers": 16}
    assert len({item["workload_instance_id"] for item in manifest["workloads"]}) == 16
    assert len({workload_fingerprint(item) for item in manifest["workloads"]}) == 16
    schema = json.loads((ROOT / "benchmarks_v5/protocol-v5-resource-workloads-v1.schema.json").read_text())
    Draft202012Validator(schema).validate(manifest)


def test_semantic_independence_and_crosswalk_cover_manifest_exactly(manifest):
    semantic = load_semantic_independence()
    crosswalk = load_crosswalk()
    expected = {item["family_id"] for item in manifest["workloads"]}
    assert {item["family_id"] for item in semantic["entries"]} == expected
    assert {item["family_id"] for item in crosswalk["entries"]} == expected
    assert semantic["assertion_scope"] == "design_review_not_statistical_inference"


def test_static_independence_guard_scans_calibration_runtime():
    assert static_independence_scan()["recommender_imports"] == 0


def test_manifest_rejects_recommendation_or_oracle_fields(manifest):
    modified = deepcopy(manifest)
    modified["workloads"][0]["recommended_profile"] = "small"
    with pytest.raises(ValueError):
        validate_resource_manifest(modified)


def test_resource_package_has_no_recommender_imports():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "evaluation_v5" / "resource").glob("*.py")
    )
    assert "from recommender" not in source
    assert "import recommender" not in source
    dockerfile = (ROOT / "cluster_evaluation" / "Dockerfile.resource-v5").read_text()
    assert "recommender/" not in dockerfile


def test_plan_is_deterministic_and_bounded(manifest):
    assert build_calibration_plan(manifest) == build_calibration_plan(manifest)
    plan = build_calibration_plan(manifest)
    assert plan["maximum_primary_trials"] == 448
    assert plan["measurement_status"] == "NOT_EXECUTED"
    assert plan["cluster_mutation"] is False


def test_wilson_interval_is_descriptive_and_finite():
    assert wilson_interval(0, 0) == (None, None)
    lower, upper = wilson_interval(5, 5)
    assert 0 < lower < upper == 1


def test_reference_stability_rule_stable_unstable_and_borderline():
    assert reference_is_stable([0.9, 1.0, 1.1]) == pytest.approx((True, 0.2))
    stable, statistic = reference_is_stable([0.89, 1.0, 1.11])
    assert stable is False
    assert statistic == pytest.approx(0.22)


def test_frozen_correctness_invariant_rejects_corruption(manifest):
    workload = manifest["workloads"][0]
    result = execute_workload(workload)
    assert result.correctness_invariants_ok is True
    corrupted = dict(result.marker_payload)
    corrupted["sum"] += 1
    assert verify_workload_result(workload, corrupted)["all_invariants_match"] is False


def _safe_rows(workload):
    rows = [_row(workload, "reference", 2000, 2048, i) for i in range(3)]
    rows += [_row(workload, "memory_probe", 2000, 192, i, success=False) for i in range(2)]
    rows += [_row(workload, "memory_probe", 2000, 256, i) for i in range(2)]
    rows += [_row(workload, "cpu_probe", 300, 256, i, runtime=2.0) for i in range(2)]
    rows += [_row(workload, "cpu_probe", 500, 256, i) for i in range(2)]
    rows += [_row(workload, "joint_verification", 500, 256, i) for i in range(5)]
    return rows


def test_derivation_returns_interval_censored_safe_envelope(manifest):
    workload = manifest["workloads"][0]
    result = derive_safe_envelopes(manifest, _safe_rows(workload))
    envelope = result["envelopes"][0]
    assert envelope["status"] == "CALIBRATED_PENDING_REVIEW"
    assert envelope["cpu_selected_m"] == 500
    assert envelope["memory_selected_mib"] == 256
    assert envelope["cpu_minimum_interval"]["lower_exclusive"] == 300
    assert envelope["memory_minimum_interval"]["lower_exclusive"] == 192
    assert envelope["manual_review_status"] == "PENDING"
    assert envelope["eligible_for_comparison"] is False
    assert envelope["workload_instance_id"] == workload["workload_instance_id"]
    assert envelope["memory_minimum_interval"]["immediate_lower_neighbor_tested"] is True
    assert envelope["cpu_minimum_interval"]["interval_notation"] == "(300, 500]"


def test_derivation_refuses_interval_without_immediate_neighbor(manifest):
    workload = manifest["workloads"][0]
    rows = [_row(workload, "reference", 2000, 2048, i) for i in range(3)]
    rows += [_row(workload, "memory_probe", 2000, 256, i) for i in range(2)]
    envelope = derive_safe_envelopes(manifest, rows)["envelopes"][0]
    assert envelope["memory_minimum_interval"]["selected_point_tested"] is True
    assert envelope["memory_minimum_interval"]["immediate_lower_neighbor_tested"] is False
    assert envelope["memory_minimum_interval"]["ordinary_interval_supported"] is False
    assert "MEMORY_LOCAL_BOUNDARY_EVIDENCE_INCOMPLETE" in envelope["reason_codes"]


def test_lowest_lattice_passes_is_one_sided(manifest):
    workload = manifest["workloads"][0]
    rows = [_row(workload, "reference", 2000, 2048, i) for i in range(3)]
    rows += [_row(workload, "memory_probe", 2000, 64, i) for i in range(2)]
    rows += [_row(workload, "cpu_probe", 100, 64, i) for i in range(2)]
    rows += [_row(workload, "joint_verification", 100, 64, i) for i in range(5)]
    envelope = derive_safe_envelopes(manifest, rows)["envelopes"][0]
    assert envelope["status"] == "CALIBRATED_PENDING_REVIEW"
    assert envelope["cpu_minimum_interval"]["one_sided"] is True
    assert envelope["memory_minimum_interval"]["largest_tested_rejected"] is None


def test_maximum_memory_failure_has_no_safe_bound(manifest):
    workload = manifest["workloads"][0]
    rows = [_row(workload, "reference", 2000, 2048, i) for i in range(3)]
    rows += [_row(workload, "memory_probe", 2000, 2048, i, success=False) for i in range(2)]
    envelope = derive_safe_envelopes(manifest, rows)["envelopes"][0]
    assert envelope["status"] == "NO_SAFE_BOUND_WITHIN_SEARCH_SPACE"
    assert "NO_SAFE_MEMORY_WITHIN_SEARCH_SPACE" in envelope["reason_codes"]


def test_unstable_reference_fails_closed(manifest):
    workload = manifest["workloads"][0]
    rows = [
        _row(workload, "reference", 2000, 2048, 0, runtime=0.8),
        _row(workload, "reference", 2000, 2048, 1, runtime=1.0),
        _row(workload, "reference", 2000, 2048, 2, runtime=1.2),
    ]
    envelope = derive_safe_envelopes(manifest, rows)["envelopes"][0]
    assert envelope["status"] == "REFERENCE_RUNTIME_UNSTABLE_REQUIRES_REVIEW"
    assert envelope["reference_runtime_relative_spread"] == pytest.approx(0.4)


@pytest.mark.parametrize("failure", ["wrong_marker", "missing_metrics", "slow_runtime"])
def test_joint_failures_require_review(manifest, failure):
    workload = manifest["workloads"][0]
    rows = _safe_rows(workload)
    target = next(row for row in rows if row.phase == "joint_verification")
    replacement = target
    if failure == "wrong_marker":
        replacement = _row(workload, target.phase, target.cpu_m, target.memory_mib, target.repeat_index, success=False)
    elif failure == "missing_metrics":
        replacement = _row(workload, target.phase, target.cpu_m, target.memory_mib, target.repeat_index, metrics=False)
    else:
        replacement = _row(workload, target.phase, target.cpu_m, target.memory_mib, target.repeat_index, runtime=2.0)
    rows[rows.index(target)] = replacement
    envelope = derive_safe_envelopes(manifest, rows)["envelopes"][0]
    assert envelope["status"] == "NO_SAFE_BOUND_WITHIN_SEARCH_SPACE"
    assert envelope["manual_review_status"] == "REQUIRED"


def test_timeout_and_mixed_probe_outcomes_are_not_safe(manifest):
    workload = manifest["workloads"][0]
    rows = _safe_rows(workload)
    joint = next(row for row in rows if row.phase == "joint_verification")
    rows[rows.index(joint)] = replace(
        joint, timeout=True, exit_code=124, exit_reason="DeadlineExceeded",
        runtime_seconds=None,
    )
    mixed = next(row for row in rows if row.phase == "memory_probe" and row.memory_mib == 256)
    rows[rows.index(mixed)] = replace(
        mixed, observed_marker_sha256="f" * 64, correctness_marker_ok=False,
        exit_code=1, exit_reason="WorkloadError",
    )
    envelope = derive_safe_envelopes(manifest, rows)["envelopes"][0]
    assert envelope["status"] == "NO_SAFE_BOUND_WITHIN_SEARCH_SPACE"
    assert "MIXED_MEMORY_PROBE_OUTCOME" in envelope["reason_codes"]
    assert "JOINT_VERIFICATION_NOT_SAFE_5_OF_5" in envelope["reason_codes"]


def test_non_monotonic_probe_requires_review(manifest):
    workload = manifest["workloads"][0]
    rows = _safe_rows(workload)
    rows += [_row(workload, "memory_probe", 2000, 384, i, success=False) for i in range(2)]
    envelope = derive_safe_envelopes(manifest, rows)["envelopes"][0]
    assert "NON_MONOTONIC_BOUNDARY_REQUIRES_REVIEW" in envelope["reason_codes"]
    assert envelope["manual_review_status"] == "REQUIRED"


def test_reference_failure_reports_no_safe_bound(manifest):
    workload = manifest["workloads"][0]
    rows = [_row(workload, "reference", 2000, 2048, i, success=False) for i in range(3)]
    envelope = derive_safe_envelopes(manifest, rows)["envelopes"][0]
    assert envelope["status"] == "NO_SAFE_BOUND_WITHIN_SEARCH_SPACE"
    assert envelope["cpu_selected_m"] is None


def test_dry_run_is_immutable_and_contains_no_observations(tmp_path, manifest):
    result_dir = tmp_path / "dry"
    report = create_dry_run_package(
        result_dir=result_dir, run_id="fixture-dry-run", image=IMAGE,
        unavailable_reason="fixture context is not the required disposable cluster",
    )
    assert report["execution_status"] == "DRY_RUN"
    assert report["trial_records"] == 0
    assert report["eligible_for_comparison"] is False
    root = json.loads((result_dir / "manifest.json").read_text())
    assert root["cluster_measurement_status"] == "NOT_EXECUTED"
    assert not (result_dir / "derived" / "safe-envelopes.json").exists()
    with pytest.raises(FileExistsError):
        create_dry_run_package(
            result_dir=result_dir, run_id="fixture-dry-run", image=IMAGE,
            unavailable_reason="fixture",
        )


def test_checksum_tampering_is_detected(tmp_path):
    result_dir = tmp_path / "dry"
    create_dry_run_package(
        result_dir=result_dir, run_id="fixture-integrity", image=IMAGE,
        unavailable_reason="fixture",
    )
    status = result_dir / "report" / "status.json"
    status.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        validate_evidence_package(result_dir)


def test_fake_adapter_drives_search_and_manual_review_gate(tmp_path, monkeypatch):
    from evaluation_v5.resource import runner

    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "1" * 40, "git_dirty": False})
    result_dir = tmp_path / "observed"
    report = run_calibration(
        result_dir=result_dir, run_id="fixture-observed", adapter=FakeAdapter(), image=IMAGE,
        enforce_readiness=False,
    )
    assert report["execution_status"] == "OBSERVED"
    assert report["sealed"] is False
    derived = json.loads((result_dir / "derived" / "safe-envelopes.json").read_text())
    assert len(derived["envelopes"]) == 16
    assert all(item["cpu_selected_m"] == 500 for item in derived["envelopes"])
    assert all(item["memory_selected_mib"] == 256 for item in derived["envelopes"])
    decisions = [json.loads(line) for line in (result_dir / "raw" / "decision-ledger.jsonl").read_text().splitlines()]
    assert any(row.get("phase") == "memory_probe" and row.get("memory_mib") == 192 for row in decisions)
    assert any(row.get("phase") == "cpu_probe" and row.get("cpu_m") == 300 for row in decisions)
    reviewed = record_manual_review(
        result_dir, reviewer_id="reviewer-fixture", decision="APPROVED",
        reason="Synthetic fixture verifies the review gate only.",
    )
    assert reviewed["sealed"] is True
    assert reviewed["eligible_for_comparison"] is True
    with pytest.raises(FileExistsError):
        record_manual_review(
            result_dir, reviewer_id="reviewer-fixture", decision="REJECTED", reason="duplicate",
        )


def test_fake_adapter_replaces_one_infrastructure_failure(tmp_path, monkeypatch):
    from evaluation_v5.resource import runner

    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "2" * 40, "git_dirty": False})
    adapter = FakeAdapter(infrastructure_once=True)
    result_dir = tmp_path / "replacement"
    run_calibration(result_dir=result_dir, run_id="fixture-replacement", adapter=adapter, image=IMAGE, enforce_readiness=False)
    records = [json.loads(line) for line in (result_dir / "raw" / "trials.jsonl").read_text().splitlines()]
    assert any(row["infrastructure_invalid"] for row in records)
    assert any(row["replacement_of"] for row in records)


def test_second_infrastructure_failure_is_exhausted(tmp_path, monkeypatch):
    from evaluation_v5.resource import runner
    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "6" * 40, "git_dirty": False})
    result_dir = tmp_path / "infra-exhausted"
    run_calibration(
        result_dir=result_dir, run_id="infra-exhausted", adapter=AlwaysInfrastructureAdapter(),
        image=IMAGE, enforce_readiness=False,
    )
    derived = json.loads((result_dir / "derived" / "safe-envelopes.json").read_text())
    assert all("INFRASTRUCTURE_REPLACEMENT_EXHAUSTED" in item["reason_codes"] for item in derived["envelopes"])


def test_workload_oom_is_not_replaced_as_infrastructure(tmp_path, monkeypatch):
    from evaluation_v5.resource import runner
    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "7" * 40, "git_dirty": False})
    result_dir = tmp_path / "oom-not-replaced"
    run_calibration(
        result_dir=result_dir, run_id="oom", adapter=FakeAdapter(memory_threshold=384),
        image=IMAGE, enforce_readiness=False,
    )
    rows = [json.loads(line) for line in (result_dir / "raw" / "trials.jsonl").read_text().splitlines()]
    assert any(row["oom_killed"] for row in rows)
    assert not any(row["replacement_of"] for row in rows if row["oom_killed"])


def test_resume_after_interruption_skips_completed_trials(tmp_path, monkeypatch):
    from evaluation_v5.resource import runner

    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "3" * 40, "git_dirty": False})
    result_dir = tmp_path / "resume"
    with pytest.raises(KeyboardInterrupt):
        run_calibration(
            result_dir=result_dir, run_id="fixture-resume",
            adapter=FakeAdapter(interrupt_on_call=4), image=IMAGE, enforce_readiness=False,
        )
    completed = len((result_dir / "raw" / "trials.jsonl").read_text().splitlines())
    adapter = FakeAdapter()
    run_calibration(
        result_dir=result_dir, run_id="fixture-resume", adapter=adapter,
        image=IMAGE, resume=True, enforce_readiness=False,
    )
    assert all(spec.run_id not in {
        json.loads(line)["run_id"]
        for line in (result_dir / "raw" / "trials.jsonl").read_text().splitlines()[:completed]
    } for spec in adapter.calls)


def test_resume_rejects_fingerprint_mismatch(tmp_path, monkeypatch):
    from evaluation_v5.resource import runner
    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "8" * 40, "git_dirty": False})
    result_dir = tmp_path / "resume-mismatch"
    with pytest.raises(KeyboardInterrupt):
        run_calibration(
            result_dir=result_dir, run_id="resume-mismatch",
            adapter=FakeAdapter(interrupt_on_call=2), image=IMAGE, enforce_readiness=False,
        )
    adapter = FakeAdapter()
    adapter.adapter_version = "different-adapter-version"
    with pytest.raises(ValueError, match="fingerprint"):
        run_calibration(
            result_dir=result_dir, run_id="resume-mismatch", adapter=adapter,
            image=IMAGE, resume=True, enforce_readiness=False,
        )


def test_kubernetes_spec_is_guaranteed_hardened_and_recommender_free(manifest):
    workload = manifest["workloads"][0]
    trial = make_trial_spec(
        workload, phase="memory_probe", cpu_m=750, memory_mib=512,
        repeat_index=0, plan_index=0,
    )
    pod = build_pod_spec(trial, IMAGE)
    container = pod["spec"]["containers"][0]
    assert container["resources"]["requests"] == container["resources"]["limits"] == {
        "cpu": "750m", "memory": "512Mi"
    }
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert pod["spec"]["restartPolicy"] == "Never"
    assert pod["spec"]["activeDeadlineSeconds"] == 150
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["imagePullPolicy"] == "Never"
    assert pod["spec"]["nodeSelector"]["z2jh-context-demo.local/dedicated-e4"] == "true"
    encoded = json.dumps(pod).lower()
    assert "recommended_profile" not in encoded
    assert "applied_profile" not in encoded
    assert "recommendation_reasons" not in encoded
    assert '"method"' not in encoded


def _eligible_cluster_fixture():
    policy = load_cluster_policy()
    digest = "sha256:" + "a" * 64
    image_state = {
        "image_reference": IMAGE,
        "reference_configured": True, "digest_syntactically_pinned": True,
        "built": True, "resolved_digest": digest, "digest_verified": True,
        "pre_pulled_on_eligible_node": True, "operationally_verified": True,
    }
    namespace = {"metadata": {"name": policy["expected_namespace"], "labels": {
        policy["cluster_identity_label"]["key"]: policy["cluster_identity_label"]["value"],
        policy["namespace_safety_label"]["key"]: policy["namespace_safety_label"]["value"],
    }}}
    node = {"metadata": {"name": "e4-node", "uid": "node-fixture", "labels": {
        policy["node_identity_label"]["key"]: policy["node_identity_label"]["value"],
        policy["node_isolation_label"]["key"]: policy["node_isolation_label"]["value"],
    }}, "status": {
        "conditions": [{"type": "Ready", "status": "True"}],
        "capacity": {"cpu": "4", "memory": "8Gi"},
        "allocatable": {"cpu": "3500m", "memory": "7Gi"},
        "images": [{"names": [IMAGE]}],
        "nodeInfo": {"kubeletVersion": "v1.fixture", "containerRuntimeVersion": "containerd://fixture", "kernelVersion": "fixture", "operatingSystem": "linux", "architecture": "arm64"},
    }}
    probe = {
        "cgroup_version": "v2", "controllers": policy["required_cgroup_controllers"],
        "available_files": policy["required_cgroup_files"], "cleanup_status": "succeeded",
    }
    kwargs = dict(
        policy=policy, image=IMAGE, image_state=image_state,
        current_context=policy["expected_context"], namespace=namespace,
        nodes={"items": [node]}, all_pods={"items": []}, quotas={"items": []},
        api_access={"fixture": True}, kubernetes_version={"serverVersion": {"gitVersion": "v1.fixture"}},
        cgroup_probe=probe,
    )
    return kwargs


def test_cluster_eligibility_accepts_complete_fixture():
    report = evaluate_cluster_eligibility(**_eligible_cluster_fixture())
    assert report["eligibility_status"] == "ELIGIBLE"
    assert report["failure_codes"] == []


@pytest.mark.parametrize("mutation,code", [
    ("context", "WRONG_KUBERNETES_CONTEXT"),
    ("cluster", "WRONG_CLUSTER_FINGERPRINT"),
    ("cgroup", "CGROUP_V2_REQUIRED"),
    ("controller", "CGROUP_CONTROLLER_MISSING"),
    ("capacity", "INSUFFICIENT_NODE_CAPACITY"),
    ("conflict", "CONFLICTING_CALIBRATION_WORKLOAD"),
    ("image", "IMAGE_DIGEST_UNVERIFIED"),
])
def test_cluster_eligibility_fails_closed(mutation, code):
    kwargs = _eligible_cluster_fixture()
    policy = kwargs["policy"]
    if mutation == "context":
        kwargs["current_context"] = "orbstack"
    elif mutation == "cluster":
        kwargs["namespace"]["metadata"]["labels"][policy["cluster_identity_label"]["key"]] = "wrong"
    elif mutation == "cgroup":
        kwargs["cgroup_probe"]["cgroup_version"] = "v1"
    elif mutation == "controller":
        kwargs["cgroup_probe"]["controllers"] = ["cpu", "memory"]
    elif mutation == "capacity":
        kwargs["nodes"]["items"][0]["status"]["allocatable"] = {"cpu": "1000m", "memory": "1Gi"}
    elif mutation == "conflict":
        kwargs["all_pods"] = {"items": [{"metadata": {"labels": {"app.kubernetes.io/name": "intent-spawner-resource-envelope-v5"}}, "spec": {}}]}
    else:
        kwargs["image_state"]["digest_verified"] = False
    assert code in evaluate_cluster_eligibility(**kwargs)["failure_codes"]


def test_axis_classifier_equality_and_censoring():
    interval = {"ordinary_interval_supported": True, "largest_tested_rejected": 300, "smallest_tested_accepted": 500}
    assert classify_axis(299, interval, 500) == "EMPIRICALLY_INSUFFICIENT"
    assert classify_axis(300, interval, 500) == "EMPIRICALLY_INSUFFICIENT"
    assert classify_axis(400, interval, 500) == "INDETERMINATE_UNTESTED_INTERVAL"
    assert classify_axis(500, interval, 500) == "EMPIRICALLY_SUPPORTED"
    assert classify_axis(750, interval, 500) == "EMPIRICALLY_SUPPORTED"
    one_sided = {"ordinary_interval_supported": True, "largest_tested_rejected": None, "smallest_tested_accepted": 100}
    assert classify_axis(50, one_sided, 100) == "INDETERMINATE_UNTESTED_INTERVAL"
    assert classify_axis(100, one_sided, 100) == "EMPIRICALLY_SUPPORTED"
    assert classify_axis(2048, {"ordinary_interval_supported": False, "largest_tested_rejected": 2048, "smallest_tested_accepted": None}, None) == "NO_REFERENCE_AVAILABLE"


def test_manual_review_pre_fingerprint_detects_tampering(tmp_path, monkeypatch):
    from evaluation_v5.resource import runner
    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "4" * 40, "git_dirty": False})
    result_dir = tmp_path / "tampered-review"
    run_calibration(result_dir=result_dir, run_id="tamper", adapter=FakeAdapter(), image=IMAGE, enforce_readiness=False)
    source = result_dir / "derived" / "safe-envelopes.json"
    source.write_text(source.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint"):
        record_manual_review(result_dir, reviewer_id="reviewer", decision="APPROVED", reason="fixture")


def test_comparison_requires_sealed_approved_e4_and_checks_ratios(tmp_path, monkeypatch, manifest):
    from evaluation_v5.resource import runner
    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "5" * 40, "git_dirty": False})
    result_dir = tmp_path / "comparison-e4"
    run_calibration(result_dir=result_dir, run_id="comparison", adapter=FakeAdapter(), image=IMAGE, enforce_readiness=False)
    first = load_crosswalk()["entries"][0]
    allocation_path = tmp_path / "allocation.json"
    allocation_path.write_text(json.dumps({
        "schema_version": "protocol-v5-resource-allocation-evidence-v1.0.0",
        "protocol_version": "5.0.0", "frozen": True, "source_id": "fixture",
        "cases": [{**{key: first[key] for key in ("allocation_case_id", "workload_instance_id", "workload_fingerprint")}, "cpu_m": 750, "memory_mib": 512}],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="sealed"):
        compare_allocations(e4_package=result_dir, allocation_evidence=allocation_path)
    record_manual_review(result_dir, reviewer_id="reviewer", decision="APPROVED", reason="fixture only")
    report = compare_allocations(e4_package=result_dir, allocation_evidence=allocation_path)
    row = report["results"][0]
    assert row["joint_classification"] == "EMPIRICALLY_SUPPORTED_BOTH_AXES"
    assert row["cpu_ratio_to_safe_reference"] == pytest.approx(1.5)
    assert row["memory_absolute_excess_mib"] == 256
    payload = json.loads(allocation_path.read_text())
    payload["cases"][0]["workload_fingerprint"] = "f" * 64
    allocation_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch"):
        compare_allocations(e4_package=result_dir, allocation_evidence=allocation_path)


def test_comparison_rejects_missing_or_mismatched_crosswalk(tmp_path):
    assert classify_axis(500, {"ordinary_interval_supported": False}, None) == "NO_REFERENCE_AVAILABLE"
    crosswalk = load_crosswalk()
    crosswalk["entries"] = crosswalk["entries"][:-1]
    path = tmp_path / "missing-crosswalk.yaml"
    path.write_text(yaml.safe_dump(crosswalk), encoding="utf-8")
    with pytest.raises(ValueError, match="16 entries"):
        load_crosswalk(path)


@pytest.mark.parametrize("decision,memory_threshold", [("REJECTED", 256), ("APPROVED", 4096)])
def test_comparison_reports_no_reference_for_rejected_or_no_safe_package(
    tmp_path, monkeypatch, decision, memory_threshold,
):
    from evaluation_v5.resource import runner
    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "a" * 40, "git_dirty": False})
    result_dir = tmp_path / f"no-reference-{decision.lower()}"
    run_calibration(
        result_dir=result_dir, run_id=f"no-reference-{decision.lower()}",
        adapter=FakeAdapter(memory_threshold=memory_threshold), image=IMAGE,
        enforce_readiness=False,
    )
    record_manual_review(result_dir, reviewer_id="reviewer", decision=decision, reason="fixture only")
    link = load_crosswalk()["entries"][0]
    allocation_path = tmp_path / f"allocation-{decision.lower()}.json"
    allocation_path.write_text(json.dumps({
        "schema_version": "protocol-v5-resource-allocation-evidence-v1.0.0",
        "protocol_version": "5.0.0", "frozen": True, "source_id": "fixture",
        "cases": [{
            "allocation_case_id": link["allocation_case_id"],
            "workload_instance_id": link["workload_instance_id"],
            "workload_fingerprint": link["workload_fingerprint"],
            "cpu_m": 2000, "memory_mib": 2048,
        }],
    }), encoding="utf-8")
    report = compare_allocations(e4_package=result_dir, allocation_evidence=allocation_path)
    assert report["results"][0]["joint_classification"] == "NO_REFERENCE_AVAILABLE"


def test_dry_run_uses_only_read_only_context_probe(tmp_path, monkeypatch):
    import cluster_evaluation.resource_adapter_v5 as adapter_module
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return __import__("subprocess").CompletedProcess(args, 0, stdout="orbstack\n", stderr="")

    monkeypatch.setattr(adapter_module.subprocess, "run", fake_run)
    result_dir = tmp_path / "read-only-dry"
    create_dry_run_package(
        result_dir=result_dir, run_id="read-only", image=IMAGE,
        unavailable_reason="wrong context fixture",
    )
    kubectl_calls = [call for call in calls if call and call[0] == "kubectl"]
    assert kubectl_calls == [["kubectl", "config", "current-context"]]
    environment = json.loads((result_dir / "raw" / "environment.json").read_text())
    assert environment["kubernetes_mutations"] == []
    assert environment["hardware_measurements"] is None
    assert environment["cgroup_measurements"] is None


def test_observed_execution_gate_fails_before_creating_result_directory(tmp_path, monkeypatch):
    from evaluation_v5.resource import runner
    monkeypatch.setattr(runner, "_git_identity", lambda: {"git_revision": "9" * 40, "git_dirty": True})
    result_dir = tmp_path / "blocked-observed"
    with pytest.raises(RuntimeError, match="DIRTY_GIT_TREE"):
        run_calibration(result_dir=result_dir, run_id="blocked", adapter=FakeAdapter(), image=IMAGE)
    assert not result_dir.exists()
