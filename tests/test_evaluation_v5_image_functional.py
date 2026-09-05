"""Unit and integration tests for Protocol-v5 image functional validation (E5).

Validates the fail-closed contracts, 3-state Dimension C evaluation, explicit
count denominators, security verification, and evidence package validation.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from unittest.mock import MagicMock
import pytest
import yaml

from evaluation_v5.image_storage import (
    BaseProbeRunner,
    DimensionCStatus,
    DockerProbeRunner,
    DryRunProbeRunner,
    EvidenceValidationError,
    FunctionalEvaluationRecord,
    FunctionalMetricsReport,
    ImageProbeManifest,
    ImageProbeResult,
    ImageProbeSpec,
    KubernetesProbeRunner,
    ProbeExecutionError,
    ProbeExecutionStatus,
    ProbeSpec,
    SecurityVerificationError,
    SyntheticProbeRunner,
    SystemFunctionalSummary,
    build_image_probe_manifest,
    build_image_probes,
    compute_functional_metrics,
    create_capability_probe,
    create_probe_runner,
    detect_runtime,
    evaluate_recommendation_functional,
    parse_image_digest,
    validate_approved_image_reference,
    validate_e5_evidence,
)
from evaluation_v5.image_storage.__main__ import _format_markdown_report, run_e5_evaluation
from evaluation_v5.schemas import EvidenceStatus, ProtocolV5Manifest


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "recommender" / "image-catalog.yaml"
SPLIT_PATH = ROOT / "benchmarks_v5" / "v5-development.yaml"


@pytest.fixture
def catalog_data() -> dict:
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# =============================================================================
# 1. Security & Identity Verification Tests
# =============================================================================


def test_parse_image_digest():
    ref = "quay.io/jupyter/minimal-notebook@sha256:a153ceb6b41db4f86b7d7dc20c7b63d08e75e2038d5e8758b954fda50ed2e18d"
    digest = parse_image_digest(ref)
    assert digest == "sha256:a153ceb6b41db4f86b7d7dc20c7b63d08e75e2038d5e8758b954fda50ed2e18d"

    # Reject unpinned tags
    with pytest.raises(SecurityVerificationError, match="not pinned"):
        parse_image_digest("quay.io/jupyter/minimal-notebook:latest")

    with pytest.raises(SecurityVerificationError, match="not pinned"):
        parse_image_digest("python:3.11-slim")


def test_validate_approved_image_reference(catalog_data):
    approved_ref = catalog_data["images"]["minimal-python"]["reference"]
    digest = validate_approved_image_reference(approved_ref, catalog_data)
    assert digest.startswith("sha256:")

    # Item 10: Arbitrary/non-catalog image rejected before execution
    unapproved_ref = "quay.io/jupyter/arbitrary-image@sha256:a153ceb6b41db4f86b7d7dc20c7b63d08e75e2038d5e8758b954fda50ed2e18d"
    with pytest.raises(SecurityVerificationError, match="not an administrator-approved image"):
        validate_approved_image_reference(unapproved_ref, catalog_data)


def test_e5_arbitrary_image_rejected_before_execution(catalog_data):
    """Regression Test 10: Unapproved image references are rejected before container launch."""
    runner = DryRunProbeRunner(catalog_data)
    unapproved_spec = ImageProbeSpec(
        image_id="malicious-image",
        image_reference="quay.io/evil/container@sha256:0000000000000000000000000000000000000000000000000000000000000000",
        image_digest="sha256:0000000000000000000000000000000000000000000000000000000000000000",
        documented_capabilities=("python",),
        probes=(create_capability_probe("minimal-python", "python"),),
    )
    with pytest.raises(SecurityVerificationError, match="not an administrator-approved image"):
        runner.run_probe(unapproved_spec, unapproved_spec.probes[0])


def test_e5_runtime_digest_mismatch_raises_security_error(catalog_data, monkeypatch):
    """Regression Test 12: Runtime digest mismatch between local store and catalog raises SecurityVerificationError."""
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    image_spec = manifest.images[0]
    probe = image_spec.probes[0]

    runner = DockerProbeRunner(catalog_data, pull_policy="never")

    # Mock docker image inspect returning a different digest
    tampered_digest = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    tampered_ref = f"{image_spec.image_reference.split('@')[0]}@{tampered_digest}"

    def mock_inspect(cmd, *args, **kwargs):
        if "inspect" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=json.dumps([tampered_ref]),
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_inspect)

    with pytest.raises(SecurityVerificationError, match="Runtime image digest .* does not match expected"):
        runner.run_probe(image_spec, probe)


# =============================================================================
# 2. Probe Manifest & Capability Construction
# =============================================================================


def test_create_capability_probe():
    probe_py = create_capability_probe("minimal-python", "python", timeout_seconds=10.0)
    assert probe_py.probe_id == "probe:minimal-python:python"
    assert probe_py.capability == "python"
    assert "PROBE_META:" in probe_py.script
    assert probe_py.timeout_seconds == 10.0

    probe_pandas = create_capability_probe("scipy-data-science", "pandas")
    assert probe_pandas.capability == "pandas"
    assert "pd.DataFrame" in probe_pandas.script


def test_e5_unknown_catalog_capability_fails_closed():
    """Regression Test 9: Unknown capability without a defined template fails closed with ValueError."""
    with pytest.raises(ValueError, match="quantum-computing"):
        create_capability_probe("custom-image", "quantum-computing")


def test_build_image_probe_manifest(catalog_data):
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    assert manifest.catalog_version == catalog_data["catalog_version"]
    assert len(manifest.images) == len(catalog_data["images"])

    images_by_id = {img.image_id: img for img in manifest.images}
    assert "minimal-python" in images_by_id
    assert "scipy-data-science" in images_by_id
    assert "pytorch-deep-learning" in images_by_id
    assert "tensorflow-deep-learning" in images_by_id

    # Verify scipy-notebook probes cover numpy, pandas, scipy, scikit-learn, visualization
    scipy_img = images_by_id["scipy-data-science"]
    caps_probed = {p.capability for p in scipy_img.probes}
    assert {"python", "numpy", "pandas", "scipy", "scikit-learn", "visualization"}.issubset(caps_probed)

    # Manifest roundtrip
    as_dict = manifest.to_dict()
    reloaded = ImageProbeManifest.from_dict(as_dict)
    assert reloaded.catalog_version == manifest.catalog_version
    assert len(reloaded.images) == len(manifest.images)


# =============================================================================
# 3. Runner Behavior: DryRun, Synthetic, Docker, Kubernetes
# =============================================================================


def test_dry_run_probe_runner(catalog_data):
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    runner = DryRunProbeRunner(catalog_data)

    results = runner.run_all(manifest)
    assert len(results) > 0
    for res in results:
        assert res.execution_mode == "dry_run"
        assert res.success is False
        assert res.is_executed is False
        assert res.is_genuine_probe_failure is False
        assert res.execution_status == ProbeExecutionStatus.NOT_EXECUTED_DRY_RUN.value
        assert res.error_category == "NOT_EXECUTED_DRY_RUN"
        assert res.runtime_seconds == 0.0


def test_synthetic_probe_runner(catalog_data):
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    runner = SyntheticProbeRunner(
        catalog_data,
        failing_capabilities={"scipy-data-science": ["pandas"]},
        unavailable_images=["minimal-python"],
    )
    results = runner.run_all(manifest)

    res_map = {(r.image_id, r.capability): r for r in results}

    # Passed probe
    passed = res_map[("scipy-data-science", "numpy")]
    assert passed.success is True
    assert passed.is_executed is True
    assert passed.is_genuine_probe_failure is False

    # Genuine probe failure
    failed = res_map[("scipy-data-science", "pandas")]
    assert failed.success is False
    assert failed.is_executed is True
    assert failed.is_genuine_probe_failure is True
    assert failed.error_category == "IMPORT_ERROR"

    # Unavailable image probe
    unavail = res_map[("minimal-python", "python")]
    assert unavail.success is False
    assert unavail.is_executed is False
    assert unavail.is_genuine_probe_failure is False
    assert unavail.execution_status == ProbeExecutionStatus.IMAGE_NOT_PRESENT.value
    assert unavail.error_category == "IMAGE_NOT_PRESENT"


def test_e5_missing_local_image_pull_policy_never_no_catalog_mismatch(catalog_data, monkeypatch):
    """Regression Test 1: Missing local image under --pull-policy never yields IMAGE_NOT_PRESENT, not CATALOG_PROBE_MISMATCH."""
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    image_spec = manifest.images[0]
    probe = image_spec.probes[0]

    runner = DockerProbeRunner(catalog_data, pull_policy="never")

    # Mock docker image inspect returning returncode=1 (not in local store)
    def mock_inspect(cmd, *args, **kwargs):
        if "inspect" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="Error response from daemon: No such image",
            )
        raise RuntimeError("docker run should not be called if image inspect failed")

    monkeypatch.setattr(subprocess, "run", mock_inspect)

    res = runner.run_probe(image_spec, probe)
    assert res.success is False
    assert res.is_executed is False
    assert res.is_genuine_probe_failure is False
    assert res.execution_status == ProbeExecutionStatus.IMAGE_NOT_PRESENT.value
    assert res.error_category == "IMAGE_NOT_PRESENT"

    # Evaluate recommendation with this result
    probe_map = {(res.image_id, res.capability): res}
    eval_rec = evaluate_recommendation_functional(
        case_id="case-missing-test",
        system_id="P2",
        predicted_image_id=image_spec.image_id,
        required_capabilities=[probe.capability],
        gold_preferred_image_id=image_spec.image_id,
        gold_acceptable_image_ids=[image_spec.image_id],
        catalog=catalog_data,
        probe_results=probe_map,
    )

    # Must be NOT_EXECUTED, NOT FAIL, and NEVER emit CATALOG_PROBE_MISMATCH
    assert eval_rec.dimension_c_status == DimensionCStatus.NOT_EXECUTED.value
    assert eval_rec.dimension_c_functional_satisfied is None
    assert eval_rec.dimension_c_execution_coverage is False
    assert "CATALOG_PROBE_MISMATCH" not in eval_rec.mismatch_types
    assert "LABEL_PASS_FUNCTIONAL_FAIL" not in eval_rec.mismatch_types
    assert "EXECUTION_UNAVAILABLE" in eval_rec.mismatch_types


def test_docker_probe_runner_mocked(catalog_data, monkeypatch):
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    image_spec = manifest.images[0]
    probe = image_spec.probes[0]

    runner = DockerProbeRunner(catalog_data)

    def mock_run(cmd, *args, **kwargs):
        if "inspect" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=json.dumps([image_spec.image_reference]),
                stderr="",
            )
        if "run" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='PROBE_META:{"python_version": "3.11.8"}\n',
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    res = runner.run_probe(image_spec, probe)
    assert res.success is True
    assert res.is_executed is True
    assert res.import_version_metadata == {"python_version": "3.11.8"}
    assert res.execution_mode == "docker"


def test_kubernetes_probe_runner_mocked(catalog_data, monkeypatch):
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    image_spec = manifest.images[0]
    probe = image_spec.probes[0]

    runner = KubernetesProbeRunner(catalog_data, namespace="test-ns")

    def mock_kubectl(args, timeout=30.0):
        if "run" in args:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="pod created", stderr="")
        if "wait" in args:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="condition met", stderr="")
        if "logs" in args:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout='PROBE_META:{"python_version": "3.11.8"}\n', stderr=""
            )
        if "get" in args and "jsonpath={.status.phase}" in args:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="Succeeded", stderr="")
        if "delete" in args:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="deleted", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_kubectl", mock_kubectl)

    res = runner.run_probe(image_spec, probe)
    assert res.success is True
    assert res.is_executed is True
    assert res.execution_mode == "kubernetes"
    assert res.import_version_metadata == {"python_version": "3.11.8"}


# =============================================================================
# 4. 3-State Dimension C & Mismatch Detection
# =============================================================================


def test_e5_unavailable_recommendation_is_not_executed_not_fail(catalog_data):
    """Regression Test 5: Recommendation referencing unavailable image is Dimension C NOT_EXECUTED, not FAIL."""
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    runner = SyntheticProbeRunner(
        catalog_data,
        unavailable_images=["scipy-data-science"],
    )
    probe_map = {(r.image_id, r.capability): r for r in runner.run_all(manifest)}

    eval_rec = evaluate_recommendation_functional(
        case_id="case-unavail",
        system_id="P2",
        predicted_image_id="scipy-data-science",
        required_capabilities=["numpy", "pandas"],
        gold_preferred_image_id="scipy-data-science",
        gold_acceptable_image_ids=["scipy-data-science"],
        catalog=catalog_data,
        probe_results=probe_map,
    )

    assert eval_rec.dimension_c_status == DimensionCStatus.NOT_EXECUTED.value
    assert eval_rec.dimension_c_functional_satisfied is None
    assert eval_rec.dimension_c_execution_coverage is False
    assert "EXECUTION_UNAVAILABLE" in eval_rec.mismatch_types
    assert "CATALOG_PROBE_MISMATCH" not in eval_rec.mismatch_types
    assert "LABEL_PASS_FUNCTIONAL_FAIL" not in eval_rec.mismatch_types


def test_e5_real_probe_failure_emits_catalog_probe_mismatch(catalog_data):
    """Regression Test 6: Container started & executed probe fails -> FAIL and CATALOG_PROBE_MISMATCH."""
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    runner = SyntheticProbeRunner(
        catalog_data,
        failing_capabilities={"scipy-data-science": ["pandas"]},
    )
    probe_map = {(r.image_id, r.capability): r for r in runner.run_all(manifest)}

    eval_rec = evaluate_recommendation_functional(
        case_id="case-real-fail",
        system_id="P2",
        predicted_image_id="scipy-data-science",
        required_capabilities=["numpy", "pandas"],
        gold_preferred_image_id="scipy-data-science",
        gold_acceptable_image_ids=["scipy-data-science"],
        catalog=catalog_data,
        probe_results=probe_map,
    )

    assert eval_rec.dimension_c_status == DimensionCStatus.FAIL.value
    assert eval_rec.dimension_c_functional_satisfied is False
    assert eval_rec.dimension_c_execution_coverage is True
    assert "CATALOG_PROBE_MISMATCH" in eval_rec.mismatch_types
    assert "LABEL_PASS_FUNCTIONAL_FAIL" in eval_rec.mismatch_types


def test_dimension_separation_and_mismatch_detection(catalog_data):
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)

    # Scenario 1: All pass
    runner_pass = SyntheticProbeRunner(catalog_data)
    results_pass = {(r.image_id, r.capability): r for r in runner_pass.run_all(manifest)}

    eval_pass = evaluate_recommendation_functional(
        case_id="case-1",
        system_id="P2",
        predicted_image_id="scipy-data-science",
        required_capabilities=["numpy", "pandas"],
        gold_preferred_image_id="scipy-data-science",
        gold_acceptable_image_ids=["scipy-data-science"],
        catalog=catalog_data,
        probe_results=results_pass,
    )
    assert eval_pass.dimension_a_gold_match is True
    assert eval_pass.dimension_b_catalog_satisfied is True
    assert eval_pass.dimension_c_status == DimensionCStatus.PASS.value
    assert eval_pass.dimension_c_functional_satisfied is True
    assert len(eval_pass.mismatch_types) == 0

    # Scenario 2: Label fail, functional pass
    eval_alt = evaluate_recommendation_functional(
        case_id="case-3",
        system_id="P2",
        predicted_image_id="pytorch-deep-learning",
        required_capabilities=["pytorch"],
        gold_preferred_image_id="minimal-python",
        gold_acceptable_image_ids=["minimal-python"],
        catalog=catalog_data,
        probe_results=results_pass,
    )
    assert eval_alt.dimension_a_gold_match is False
    assert eval_alt.dimension_b_catalog_satisfied is True
    assert eval_alt.dimension_c_status == DimensionCStatus.PASS.value
    assert "LABEL_FAIL_FUNCTIONAL_PASS" in eval_alt.mismatch_types


# =============================================================================
# 5. Denominators & Metric Computation
# =============================================================================


def test_e5_dry_run_reports_none_for_empirical_rates(catalog_data):
    """Regression Test 2: Dry-run mode reports None/null for empirical functional success rate."""
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    runner = DryRunProbeRunner(catalog_data)
    results = runner.run_all(manifest)
    probe_map = {(r.image_id, r.capability): r for r in results}

    evals = [
        evaluate_recommendation_functional(
            case_id="case-dry",
            system_id="P2",
            predicted_image_id="scipy-data-science",
            required_capabilities=["numpy"],
            gold_preferred_image_id="scipy-data-science",
            gold_acceptable_image_ids=["scipy-data-science"],
            catalog=catalog_data,
            probe_results=probe_map,
        )
    ]

    report = compute_functional_metrics(evals, catalog_data, probe_results=results)
    summary = report.systems["P2"]
    assert summary.total_recommendations == 1
    assert summary.functional_executed_count == 0
    assert summary.functional_passed_count == 0
    assert summary.functional_unavailable_count == 1
    assert summary.functional_execution_coverage == 0.0
    assert summary.functional_success_rate_among_executed is None
    assert summary.conservative_functional_success_rate is None
    assert summary.joint_gold_and_functional_rate is None

    # Verify JSON serialization has null, not 0.0
    dumped = json.dumps(report.to_dict())
    loaded = json.loads(dumped)
    p2_loaded = loaded["systems"]["P2"]
    assert p2_loaded["functional_success_rate_among_executed"] is None
    assert p2_loaded["functional_executed_count"] == 0


def test_e5_execution_coverage_denominator(catalog_data):
    """Regression Test 7: functional_execution_coverage = functional_executed_count / total_recommendations."""
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    runner = SyntheticProbeRunner(
        catalog_data,
        unavailable_images=["scipy-data-science"],
    )
    results = runner.run_all(manifest)
    probe_map = {(r.image_id, r.capability): r for r in results}

    evals = [
        # minimal-python is available (executed)
        evaluate_recommendation_functional(
            case_id="c1",
            system_id="P2",
            predicted_image_id="minimal-python",
            required_capabilities=["python"],
            gold_preferred_image_id="minimal-python",
            gold_acceptable_image_ids=["minimal-python"],
            catalog=catalog_data,
            probe_results=probe_map,
        ),
        # scipy-data-science is unavailable (unexecuted)
        evaluate_recommendation_functional(
            case_id="c2",
            system_id="P2",
            predicted_image_id="scipy-data-science",
            required_capabilities=["numpy"],
            gold_preferred_image_id="scipy-data-science",
            gold_acceptable_image_ids=["scipy-data-science"],
            catalog=catalog_data,
            probe_results=probe_map,
        ),
    ]

    report = compute_functional_metrics(evals, catalog_data, probe_results=results)
    summary = report.systems["P2"]
    assert summary.total_recommendations == 2
    assert summary.functional_executed_count == 1
    assert summary.functional_unavailable_count == 1
    assert summary.functional_execution_coverage == 0.5


def test_e5_functional_success_among_executed_denominator(catalog_data):
    """Regression Test 8: functional_success_rate_among_executed = functional_passed_count / functional_executed_count."""
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    runner = SyntheticProbeRunner(
        catalog_data,
        failing_capabilities={"scipy-data-science": ["pandas"]},
        unavailable_images=["pytorch-deep-learning"],
    )
    results = runner.run_all(manifest)
    probe_map = {(r.image_id, r.capability): r for r in results}

    evals = [
        # 1. Executed & passed
        evaluate_recommendation_functional(
            case_id="c1",
            system_id="P2",
            predicted_image_id="minimal-python",
            required_capabilities=["python"],
            gold_preferred_image_id="minimal-python",
            gold_acceptable_image_ids=["minimal-python"],
            catalog=catalog_data,
            probe_results=probe_map,
        ),
        # 2. Executed & failed
        evaluate_recommendation_functional(
            case_id="c2",
            system_id="P2",
            predicted_image_id="scipy-data-science",
            required_capabilities=["numpy", "pandas"],
            gold_preferred_image_id="scipy-data-science",
            gold_acceptable_image_ids=["scipy-data-science"],
            catalog=catalog_data,
            probe_results=probe_map,
        ),
        # 3. Unavailable
        evaluate_recommendation_functional(
            case_id="c3",
            system_id="P2",
            predicted_image_id="pytorch-deep-learning",
            required_capabilities=["pytorch"],
            gold_preferred_image_id="pytorch-deep-learning",
            gold_acceptable_image_ids=["pytorch-deep-learning"],
            catalog=catalog_data,
            probe_results=probe_map,
        ),
    ]

    report = compute_functional_metrics(evals, catalog_data, probe_results=results)
    summary = report.systems["P2"]
    assert summary.total_recommendations == 3
    assert summary.functional_executed_count == 2
    assert summary.functional_passed_count == 1
    assert summary.functional_failed_count == 1
    assert summary.functional_unavailable_count == 1
    # 1 pass out of 2 executed = 50%
    assert summary.functional_success_rate_among_executed == 0.5
    # 1 pass out of 3 total = 33.3%
    assert summary.conservative_functional_success_rate == pytest.approx(1 / 3, rel=1e-3)


# =============================================================================
# 6. End-to-End CLI & Evidence Package Validation Tests
# =============================================================================


def test_e5_partial_execution_marks_package_incomplete(tmp_path, monkeypatch):
    """Regression Test 3: One unavailable image among four marks package as INCOMPLETE."""
    out_dir = tmp_path / "e5-test-incomplete"

    # Mock runner creation to return a synthetic runner with one unavailable image
    from evaluation_v5.image_storage import runner as runner_module

    original_create = runner_module.create_probe_runner

    def mock_create(catalog, mode="auto", **kwargs):
        # Return a DockerProbeRunner where inspect_image_identity fails for pytorch
        runner = DockerProbeRunner(catalog, pull_policy="never")

        def mock_inspect(ref):
            if "pytorch" in ref:
                return False, None, "Image not found locally"
            # Return valid identity for all other images
            return True, ref.split("@", 1)[1] if "@" in ref else None, None

        runner.inspect_image_identity = mock_inspect
        # Mock run_probe to return successful executed result for available images
        original_run_probe = runner.run_probe

        def mock_run_probe(img_spec, probe):
            if "pytorch" in img_spec.image_id:
                return original_run_probe(img_spec, probe)
            return ImageProbeResult(
                schema_version="protocol-v5-image-probe-record-v1.1.0",
                probe_id=probe.probe_id,
                image_id=img_spec.image_id,
                image_reference=img_spec.image_reference,
                image_digest=img_spec.image_digest,
                capability=probe.capability,
                success=True,
                execution_status=ProbeExecutionStatus.EXECUTED.value,
                resolved_image_digest=img_spec.image_digest,
                import_version_metadata={f"{probe.capability}_version": "1.0.0"},
                runtime_seconds=0.1,
                error_category=None,
                error_message=None,
                stdout='PROBE_META:{"ok": true}',
                execution_mode="docker",
                timestamp_utc="2026-09-05T00:00:00Z",
            )

        runner.run_probe = mock_run_probe
        return runner

    monkeypatch.setattr("evaluation_v5.image_storage.__main__.create_probe_runner", mock_create)

    run_e5_evaluation(
        catalog_path=CATALOG_PATH,
        split_path=SPLIT_PATH,
        mode="docker",
        output_dir=out_dir,
        run_id="e5-test-incomplete",
    )

    manifest_raw = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_raw["execution_status"] == EvidenceStatus.INCOMPLETE.value

    status_raw = json.loads((out_dir / "report" / "status.json").read_text(encoding="utf-8"))
    assert status_raw["status"] == EvidenceStatus.INCOMPLETE.value

    # Package must still validate as valid INCOMPLETE package
    res = validate_e5_evidence(out_dir)
    assert res["status"] == "PASS"
    assert res["execution_status"] == EvidenceStatus.INCOMPLETE.value
    assert res["probes_unavailable"] > 0


def test_e5_full_execution_marks_package_observed(tmp_path, monkeypatch):
    """Regression Test 4: All required catalog probes executed marks package as OBSERVED."""
    out_dir = tmp_path / "e5-test-observed"

    def mock_create(catalog, mode="auto", **kwargs):
        runner = DockerProbeRunner(catalog, pull_policy="never")
        runner.inspect_image_identity = lambda ref: (True, ref.split("@", 1)[1] if "@" in ref else None, None)

        def mock_run_probe(img_spec, probe):
            return ImageProbeResult(
                schema_version="protocol-v5-image-probe-record-v1.1.0",
                probe_id=probe.probe_id,
                image_id=img_spec.image_id,
                image_reference=img_spec.image_reference,
                image_digest=img_spec.image_digest,
                capability=probe.capability,
                success=True,
                execution_status=ProbeExecutionStatus.EXECUTED.value,
                resolved_image_digest=img_spec.image_digest,
                import_version_metadata={f"{probe.capability}_version": "1.0.0"},
                runtime_seconds=0.1,
                error_category=None,
                error_message=None,
                stdout='PROBE_META:{"ok": true}',
                execution_mode="docker",
                timestamp_utc="2026-09-05T00:00:00Z",
            )

        runner.run_probe = mock_run_probe
        return runner

    monkeypatch.setattr("evaluation_v5.image_storage.__main__.create_probe_runner", mock_create)

    run_e5_evaluation(
        catalog_path=CATALOG_PATH,
        split_path=SPLIT_PATH,
        mode="docker",
        output_dir=out_dir,
        run_id="e5-test-observed",
    )

    manifest_raw = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_raw["execution_status"] == EvidenceStatus.OBSERVED.value

    status_raw = json.loads((out_dir / "report" / "status.json").read_text(encoding="utf-8"))
    assert status_raw["status"] == EvidenceStatus.OBSERVED.value

    # Package must validate as OBSERVED
    res = validate_e5_evidence(out_dir)
    assert res["status"] == "PASS"
    assert res["execution_status"] == EvidenceStatus.OBSERVED.value
    assert res["probes_unavailable"] == 0
    assert res["probes_executed"] == res["total_probes_configured"]


def test_e5_validate_evidence_recomputes_and_validates(tmp_path):
    """Regression Test 11: validate_e5_evidence verifies SHA256SUMS and enforces semantic consistency."""
    out_dir = tmp_path / "e5-test-validation"
    run_e5_evaluation(
        catalog_path=CATALOG_PATH,
        split_path=SPLIT_PATH,
        mode="dry-run",
        output_dir=out_dir,
        run_id="e5-test-validation",
    )

    # 1. Valid dry-run package passes
    res = validate_e5_evidence(out_dir)
    assert res["status"] == "PASS"

    # 2. Tampering a file breaks SHA256SUMS check
    manifest_file = out_dir / "manifest.json"
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["random_seeds"] = [999]
    manifest_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(EvidenceValidationError, match="Checksum mismatch"):
        validate_e5_evidence(out_dir)


def test_end_to_end_cli_dry_run(tmp_path):
    out_dir = tmp_path / "e5-test-dry-run"
    run_e5_evaluation(
        catalog_path=CATALOG_PATH,
        split_path=SPLIT_PATH,
        mode="dry-run",
        output_dir=out_dir,
        run_id="e5-test-dry-run",
    )

    status_raw = json.loads((out_dir / "report" / "status.json").read_text(encoding="utf-8"))
    assert status_raw["status"] == "DRY_RUN"
    assert status_raw["execution_mode"] == "dry_run"
    assert status_raw["probes_passed"] == 0

    manifest_raw = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest = ProtocolV5Manifest.from_dict(manifest_raw)
    assert manifest.execution_status.value == "DRY_RUN"


def test_end_to_end_cli_with_recommendations_file(tmp_path):
    rec_file = ROOT / "results_v5" / "protocol-v5.0.0" / "E1" / "20260825T-observed-p1-p2-development-v1" / "raw" / "recommendations.jsonl"
    out_dir = tmp_path / "e5-test-recs"

    run_e5_evaluation(
        catalog_path=CATALOG_PATH,
        recommendations_path=rec_file,
        split_path=SPLIT_PATH,
        mode="synthetic",
        output_dir=out_dir,
        run_id="e5-test-recs",
    )

    metrics_raw = json.loads((out_dir / "derived" / "functional_metrics.json").read_text(encoding="utf-8"))
    assert metrics_raw["total_evaluations"] == 36
    assert "P1" in metrics_raw["systems"]
    assert "P2" in metrics_raw["systems"]


def test_e5_rapids_unsupported_workload_semantics(catalog_data):
    """Regression Test 13: Workload requiring unsupported library (e.g. RAPIDS) fails Dimension B and C."""
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    runner = SyntheticProbeRunner(catalog_data)
    results = runner.run_all(manifest)
    probe_map = {(r.image_id, r.capability): r for r in results}

    # Workload requiring rapids, recommended minimal-python
    eval_rec = evaluate_recommendation_functional(
        case_id="case-rapids",
        system_id="P2",
        predicted_image_id="minimal-python",
        required_capabilities=["rapids"],
        gold_preferred_image_id=None,
        gold_acceptable_image_ids=[],
        catalog=catalog_data,
        probe_results=probe_map,
    )

    assert eval_rec.dimension_b_catalog_satisfied is False
    assert eval_rec.missing_catalog_capabilities == ("rapids",)
    assert eval_rec.dimension_c_status == DimensionCStatus.NOT_EXECUTED.value
    assert eval_rec.dimension_c_functional_satisfied is None
    assert eval_rec.dimension_c_execution_coverage is False
    assert "CAPABILITY_UNSATISFIED" in eval_rec.mismatch_types
    assert "LABEL_FAIL_FUNCTIONAL_PASS" not in eval_rec.mismatch_types
    assert "EXECUTION_UNAVAILABLE" not in eval_rec.mismatch_types

    # Metrics aggregation: must not be in functional_validation_eligible_count
    report = compute_functional_metrics([eval_rec], catalog_data, probe_results=results)
    summary = report.systems["P2"]
    assert summary.total_recommendations == 1
    assert summary.catalog_capability_satisfied_count == 0
    assert summary.catalog_unsatisfied_count == 1
    assert summary.functional_validation_eligible_count == 0
    assert summary.functional_executed_count == 0
    assert summary.functional_passed_count == 0
    assert summary.capability_unsatisfied_count == 1
    assert summary.execution_unavailable_count == 0
    assert summary.operationally_adequate_count == 0


def test_e5_no_image_recommendation_semantics(catalog_data):
    """Regression Test 14: Absent image recommendation is NOT_APPLICABLE and NO_IMAGE_RECOMMENDATION."""
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    runner = SyntheticProbeRunner(catalog_data)
    results = runner.run_all(manifest)
    probe_map = {(r.image_id, r.capability): r for r in results}

    eval_rec = evaluate_recommendation_functional(
        case_id="case-no-img",
        system_id="P1",
        predicted_image_id=None,
        required_capabilities=["python"],
        gold_preferred_image_id="minimal-python",
        gold_acceptable_image_ids=["minimal-python"],
        catalog=catalog_data,
        probe_results=probe_map,
    )

    assert eval_rec.dimension_b_catalog_satisfied is False
    assert eval_rec.dimension_c_status == DimensionCStatus.NOT_APPLICABLE.value
    assert eval_rec.dimension_c_functional_satisfied is None
    assert eval_rec.dimension_c_execution_coverage is False
    assert "NO_IMAGE_RECOMMENDATION" in eval_rec.mismatch_types
    assert "EXECUTION_UNAVAILABLE" not in eval_rec.mismatch_types

    report = compute_functional_metrics([eval_rec], catalog_data, probe_results=results)
    summary = report.systems["P1"]
    assert summary.total_recommendations == 1
    assert summary.recommendations_with_image_count == 0
    assert summary.no_image_recommendation_count == 1
    assert summary.functional_validation_eligible_count == 0
    assert summary.functional_executed_count == 0
    assert summary.functional_passed_count == 0
    assert summary.execution_unavailable_count == 0


def test_e5_explicit_denominators_mixed_workload(catalog_data):
    """Regression Test 15: Denominators correctly separate total, eligible, executed, passed, and adequate."""
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    runner = SyntheticProbeRunner(catalog_data)
    results = runner.run_all(manifest)
    probe_map = {(r.image_id, r.capability): r for r in results}

    evals = [
        # 1. Eligible, executed, passed (adequate)
        evaluate_recommendation_functional(
            case_id="c1",
            system_id="SYS",
            predicted_image_id="minimal-python",
            required_capabilities=["python"],
            gold_preferred_image_id="minimal-python",
            gold_acceptable_image_ids=["minimal-python"],
            catalog=catalog_data,
            probe_results=probe_map,
        ),
        # 2. Unsupported capability (ineligible)
        evaluate_recommendation_functional(
            case_id="c2",
            system_id="SYS",
            predicted_image_id="minimal-python",
            required_capabilities=["rapids"],
            gold_preferred_image_id=None,
            gold_acceptable_image_ids=[],
            catalog=catalog_data,
            probe_results=probe_map,
        ),
        # 3. No image recommendation (ineligible)
        evaluate_recommendation_functional(
            case_id="c3",
            system_id="SYS",
            predicted_image_id=None,
            required_capabilities=["python"],
            gold_preferred_image_id="minimal-python",
            gold_acceptable_image_ids=["minimal-python"],
            catalog=catalog_data,
            probe_results=probe_map,
        ),
    ]

    report = compute_functional_metrics(evals, catalog_data, probe_results=results)
    summary = report.systems["SYS"]
    assert summary.total_recommendations == 3
    assert summary.recommendations_with_image_count == 2
    assert summary.no_image_recommendation_count == 1
    assert summary.catalog_capability_satisfied_count == 1
    assert summary.catalog_unsatisfied_count == 2
    assert summary.functional_validation_eligible_count == 1
    assert summary.functional_executed_count == 1
    assert summary.functional_passed_count == 1
    assert summary.operationally_adequate_count == 1
    assert summary.functional_execution_coverage == 1.0
    assert summary.functional_success_rate_among_executed == 1.0
    assert summary.conservative_functional_success_rate == pytest.approx(1 / 3)
    assert summary.operational_adequacy_rate == pytest.approx(1 / 3)
    assert summary.capability_unsatisfied_count == 1
    assert summary.execution_unavailable_count == 0


def test_e5_null_source_recommendation_remains_no_image_recommendation():
    """Regression Test 1: Null source recommendation remains NO_IMAGE_RECOMMENDATION."""
    catalog_data = {
        "catalog_version": "2026-08-06.1",
        "images": {
            "minimal-python": {"capabilities": ["python"]},
        },
    }
    rec = evaluate_recommendation_functional(
        case_id="null-rec-1",
        system_id="P1",
        predicted_image_id=None,
        source_predicted_image_value=None,
        required_capabilities=["python"],
        gold_preferred_image_id="minimal-python",
        gold_acceptable_image_ids=["minimal-python"],
        catalog=catalog_data,
        probe_results={},
    )
    assert rec.predicted_image_id is None
    assert rec.source_predicted_image_value is None
    assert "NO_IMAGE_RECOMMENDATION" in rec.mismatch_types
    assert rec.dimension_c_status == DimensionCStatus.NOT_APPLICABLE.value
    assert rec.dimension_c_execution_coverage is False
    assert rec.dimension_c_functional_satisfied is None


def test_e5_raises_error_if_synthesis_attempted_from_null():
    """Regression Test 2: E5 raises error if synthesis attempted from null."""
    catalog_data = {"catalog_version": "2026-08-06.1", "images": {}}
    with pytest.raises(ValueError, match="cannot synthesize predicted_image_id"):
        evaluate_recommendation_functional(
            case_id="synth-1",
            system_id="P1",
            predicted_image_id="pytorch-deep-learning",
            source_predicted_image_value=None,
            required_capabilities=["python"],
            gold_preferred_image_id="pytorch-deep-learning",
            gold_acceptable_image_ids=["pytorch-deep-learning"],
            catalog=catalog_data,
            probe_results={},
        )

    with pytest.raises(ValueError, match="cannot synthesize predicted_image_id"):
        evaluate_recommendation_functional(
            case_id="synth-2",
            system_id="P1",
            predicted_image_id="pytorch-deep-learning",
            source_predicted_image_value="",
            required_capabilities=["python"],
            gold_preferred_image_id="pytorch-deep-learning",
            gold_acceptable_image_ids=["pytorch-deep-learning"],
            catalog=catalog_data,
            probe_results={},
        )


def test_e5_canonical_image_id_preserves_both_source_and_normalized_values():
    """Regression Test 3: Canonical image ID preserves both source and normalized values."""
    catalog_data = {
        "catalog_version": "2026-08-06.1",
        "images": {
            "pytorch-deep-learning": {"capabilities": ["python", "pytorch"]},
        },
    }
    rec = evaluate_recommendation_functional(
        case_id="norm-1",
        system_id="P1",
        source_predicted_image_value="large-pytorch-deep-learning",
        predicted_image_id="pytorch-deep-learning",
        required_capabilities=["python", "pytorch"],
        gold_preferred_image_id="pytorch-deep-learning",
        gold_acceptable_image_ids=["pytorch-deep-learning"],
        catalog=catalog_data,
        probe_results={},
    )
    assert rec.source_predicted_image_value == "large-pytorch-deep-learning"
    assert rec.predicted_image_id == "pytorch-deep-learning"
    d = rec.to_dict()
    assert d["source_predicted_image_value"] == "large-pytorch-deep-learning"
    assert d["predicted_image_id"] == "pytorch-deep-learning"


def test_e5_same_recommendation_input_yields_deterministic_classification():
    """Regression Test 4: Same recommendation input yields deterministic with-image/no-image classification."""
    recs_path = Path("results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1/raw/recommendations.jsonl")
    if not recs_path.is_file():
        pytest.skip("Frozen E1 recommendations file not present")

    raw_recs = [json.loads(line) for line in recs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    p1_recs = [r for r in raw_recs if r.get("system_id") == "P1"]
    p2_recs = [r for r in raw_recs if r.get("system_id") == "P2"]

    assert len(p1_recs) == 18
    assert len(p2_recs) == 18

    # Verify that every single row has non-null predicted_image_id
    for r in p1_recs:
        assert r.get("predicted_image_id") is not None
        assert r.get("predicted_image_id") != ""
    for r in p2_recs:
        assert r.get("predicted_image_id") is not None
        assert r.get("predicted_image_id") != ""


def test_e5_legacy_package_not_mistaken_for_current_v12_valid():
    """Regression Test 5: Legacy package cannot be mistaken for current-v1.2-valid evidence."""
    legacy_dir = Path("results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T024633Z")
    if not legacy_dir.is_dir():
        pytest.skip("Historical package 024633Z not present")

    res = validate_e5_evidence(legacy_dir)
    assert res["status"] == "PASS"
    assert res["validator_status"] == "LEGACY_VALID"
    assert res["eligible_as_current_e5_evidence"] is False
    assert res["validation_profile"] == "LEGACY_SCHEMA_V1_1"

    # Also check invalid package 020014Z
    invalid_dir = Path("results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T020014Z")
    if invalid_dir.is_dir():
        with pytest.raises(EvidenceValidationError):
            validate_e5_evidence(invalid_dir)


def test_e5_legacy_v12_package_validates_under_legacy_profile():
    """Regression Test 6: Historical v1.2 package validates as LEGACY_VALID with eligible_as_current_e5_evidence == False."""
    v12_dir = Path("results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T032437Z")
    if not v12_dir.is_dir():
        pytest.skip("v1.2 package 032437Z not present")

    res = validate_e5_evidence(v12_dir)
    assert res["status"] == "PASS"
    assert res["validator_status"] == "LEGACY_VALID"
    assert res["eligible_as_current_e5_evidence"] is False
    assert res["validation_profile"] == "LEGACY_SCHEMA_V1_2"


def test_e5_dimension_a_appears_independently_in_report_and_metrics():
    """Regression Test 7: Dimension A appears independently in report and derived metrics."""
    v12_dir = Path("results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T032437Z")
    if not v12_dir.is_dir():
        pytest.skip("v1.2 package 032437Z not present")

    metrics_data = json.loads((v12_dir / "derived" / "functional_metrics.json").read_text(encoding="utf-8"))
    for sys_id in ("P1", "P2"):
        s = metrics_data["systems"][sys_id]
        assert "gold_preferred_count" in s
        assert "gold_acceptable_count" in s
        assert "gold_preferred_rate" in s
        assert "gold_acceptable_rate" in s
        assert s["gold_preferred_count"] == 13
        assert s["gold_acceptable_count"] == 13
        assert s["gold_preferred_rate"] == pytest.approx(13 / 18, rel=1e-3)
        assert s["gold_acceptable_rate"] == pytest.approx(13 / 18, rel=1e-3)

    # Check that the markdown formatting contains Dimension A section
    manifest = ImageProbeManifest.from_dict(json.loads((v12_dir / "raw" / "probe_manifest.json").read_text(encoding="utf-8")))
    md = _format_markdown_report(
        run_id="test-run",
        manifest=manifest,
        metrics_report=metrics_data,
        execution_mode="docker",
        execution_status="OBSERVED",
        git_info={"git_revision": "test", "git_dirty": False},
        recommendations_path=Path("results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1/raw/recommendations.jsonl"),
    )
    assert "### Dimension A: Gold-Label Benchmark Correctness" in md
    assert "Preferred Match" in md
    assert "Acceptable Match" in md
    assert "13/18" in md


def test_e5_abc_counts_recompute_exactly_from_raw_records():
    """Regression Test 8: A/B/C counts recompute exactly from raw records."""
    v12_dir = Path("results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T032437Z")
    if not v12_dir.is_dir():
        pytest.skip("v1.2 package 032437Z not present")

    eval_records = [
        json.loads(line)
        for line in (v12_dir / "raw" / "functional_evaluations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    metrics_data = json.loads((v12_dir / "derived" / "functional_metrics.json").read_text(encoding="utf-8"))

    for sys_id in ("P1", "P2"):
        sys_evals = [r for r in eval_records if r["system_id"] == sys_id]
        s = metrics_data["systems"][sys_id]

        # Dimension A
        pref_a = sum(1 for r in sys_evals if r["dimension_a_preferred_match"])
        acc_a = sum(1 for r in sys_evals if r["dimension_a_gold_match"])
        assert s["gold_preferred_count"] == pref_a
        assert s["gold_acceptable_count"] == acc_a

        # Dimension B
        sat_b = sum(1 for r in sys_evals if r["dimension_b_catalog_satisfied"])
        unsat_b = sum(1 for r in sys_evals if not r["dimension_b_catalog_satisfied"])
        assert s["catalog_capability_satisfied_count"] == sat_b
        assert s["catalog_unsatisfied_count"] == unsat_b

        # Dimension C
        eligible_c = sum(1 for r in sys_evals if r["predicted_image_id"] is not None and r["dimension_b_catalog_satisfied"])
        exec_c = sum(1 for r in sys_evals if r["dimension_c_execution_coverage"])
        pass_c = sum(1 for r in sys_evals if r["dimension_c_status"] == "PASS")
        fail_c = sum(1 for r in sys_evals if r["dimension_c_status"] == "FAIL")
        unavail_c = sum(
            1 for r in sys_evals
            if r["predicted_image_id"] is not None and r["dimension_b_catalog_satisfied"] and r["dimension_c_status"] == "NOT_EXECUTED"
        )
        assert s["functional_validation_eligible_count"] == eligible_c
        assert s["functional_executed_count"] == exec_c
        assert s["functional_passed_count"] == pass_c
        assert s["functional_failed_count"] == fail_c
        assert s["functional_unavailable_count"] == unavail_c


def test_e5_dimension_b_exact_recomputability_invariant(catalog_data):
    """Regression Test 9: Dimension B is strictly recomputable as required subset of declared."""
    v12_dir = Path("results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T033832Z")
    if not v12_dir.is_dir():
        pytest.skip("Final package 033832Z not present")

    eval_records = [
        json.loads(line)
        for line in (v12_dir / "raw" / "functional_evaluations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    catalog_images = catalog_data.get("images", {})

    for rec in eval_records:
        img_id = rec.get("predicted_image_id")
        req_caps = set(rec.get("required_capabilities", []))

        # Check non-image constraints did not leak into required_capabilities
        for non_image_term in ("small", "medium", "large", "cpu", "memory", "gpu", "cuda"):
            assert non_image_term not in req_caps, f"Non-image constraint '{non_image_term}' leaked in {rec['case_id']}"

        if not img_id:
            assert rec["dimension_b_catalog_satisfied"] is False
            continue

        cat_entry = catalog_images.get(img_id)
        assert cat_entry is not None, f"Image {img_id} not found in catalog"

        declared = set(cat_entry.get("capabilities", []))
        expected_missing = sorted(list(req_caps - declared))
        actual_missing = sorted(list(rec.get("missing_catalog_capabilities", [])))

        # 1. Missing capabilities must be mathematically exact (required - declared)
        assert actual_missing == expected_missing, (
            f"Case {rec['case_id']} ({rec['system_id']}): missing mismatch {actual_missing} vs {expected_missing}"
        )

        # 2. Declared capabilities must NEVER be reported as missing
        for dec in declared:
            assert dec not in actual_missing, f"Declared cap {dec} reported missing in {rec['case_id']}"

        # 3. Dimension B satisfied iff expected_missing is empty
        assert rec["dimension_b_catalog_satisfied"] == (len(expected_missing) == 0)


def test_e5_catalog_underclaim_is_not_container_execution_failure(catalog_data):
    """Regression Test 10: CATALOG_UNDERCLAIM_FUNCTIONAL_PASS is distinct from execution failure."""
    v12_dir = Path("results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T033832Z")
    if not v12_dir.is_dir():
        pytest.skip("Final package 033832Z not present")

    # 1. Inspect P2 threshold-below-vi in evaluation records
    eval_records = [
        json.loads(line)
        for line in (v12_dir / "raw" / "functional_evaluations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    target_rec = next(
        (r for r in eval_records if r["case_id"] == "threshold-below-vi" and r["system_id"] == "P2"),
        None,
    )
    assert target_rec is not None
    assert target_rec["predicted_image_id"] == "pytorch-deep-learning"
    assert target_rec["required_capabilities"] == ["python"]

    # Catalog does NOT declare python -> Dimension B is False
    assert target_rec["dimension_b_catalog_satisfied"] is False
    assert target_rec["missing_catalog_capabilities"] == ["python"]
    assert "CAPABILITY_UNSATISFIED" in target_rec["mismatch_types"]

    # Dimension C is gated out (NOT_EXECUTED), but no probe failed
    assert target_rec["dimension_c_status"] == "NOT_EXECUTED"
    assert target_rec["failed_probes"] == []

    # 2. Inspect the probe results for pytorch-deep-learning:python
    probe_results = [
        json.loads(line)
        for line in (v12_dir / "raw" / "probe_results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    python_probe = next(
        (p for p in probe_results if p["probe_id"] == "probe:pytorch-deep-learning:python"),
        None,
    )
    assert python_probe is not None
    # Crucial distinction: the container itself is operationally functional for Python!
    assert python_probe["execution_status"] == "EXECUTED"
    assert python_probe["success"] is True
    assert "python_version" in python_probe["import_version_metadata"]


# =============================================================================
# 6. Decoupled Dimension C & Discrepancy Taxonomy Regression Tests (v1.3)
# =============================================================================


def test_e5_target_case_threshold_below_vi_underclaim_functional_pass(catalog_data):
    """Regression Test 1: threshold-below-vi has B=False, Python probe PASS, C=PASS, and CATALOG_UNDERCLAIM_FUNCTIONAL_PASS."""
    python_probe = ImageProbeResult(
        probe_id="probe:pytorch-deep-learning:python",
        image_id="pytorch-deep-learning",
        image_reference=catalog_data["images"]["pytorch-deep-learning"]["reference"],
        image_digest=parse_image_digest(catalog_data["images"]["pytorch-deep-learning"]["reference"]),
        capability="python",
        success=True,
        execution_status=ProbeExecutionStatus.EXECUTED.value,
        runtime_seconds=0.15,
        execution_mode="docker",
    )
    probe_map = {("pytorch-deep-learning", "python"): python_probe}

    eval_rec = evaluate_recommendation_functional(
        case_id="threshold-below-vi",
        system_id="P2",
        predicted_image_id="pytorch-deep-learning",
        required_capabilities=["python"],
        gold_preferred_image_id="minimal-python",
        gold_acceptable_image_ids=["minimal-python"],
        catalog=catalog_data,
        probe_results=probe_map,
    )

    # Invariant: Dimension B is False because catalog metadata does not claim python
    assert eval_rec.dimension_b_catalog_satisfied is False
    assert eval_rec.missing_catalog_capabilities == ("python",)

    # Invariant: Dimension C is PASS because actual probe executed and passed
    assert eval_rec.dimension_c_status == DimensionCStatus.PASS.value
    assert eval_rec.dimension_c_functional_satisfied is True
    assert eval_rec.dimension_c_execution_coverage is True
    assert eval_rec.dimension_c_eligible is True

    # Invariant: CATALOG_UNDERCLAIM_FUNCTIONAL_PASS is present
    assert "CATALOG_UNDERCLAIM_FUNCTIONAL_PASS" in eval_rec.mismatch_types
    assert "EXECUTION_UNAVAILABLE" not in eval_rec.mismatch_types


def test_e5_rapids_no_exact_probe_not_executed(catalog_data):
    """Regression Test 2: RAPIDS workload has B=False, no probe defined, C=NOT_EXECUTED, and REQUIRED_PROBE_NOT_DEFINED."""
    # Selected image is minimal-python, required capability is rapids (no probe defined)
    eval_rec = evaluate_recommendation_functional(
        case_id="p2-required-rapids-unsupported-en",
        system_id="P2",
        predicted_image_id="minimal-python",
        required_capabilities=["rapids"],
        gold_preferred_image_id=None,
        gold_acceptable_image_ids=[],
        catalog=catalog_data,
        probe_results={},
    )

    # Dimension B = False
    assert eval_rec.dimension_b_catalog_satisfied is False
    assert eval_rec.missing_catalog_capabilities == ("rapids",)

    # Dimension C = NOT_EXECUTED, reason = REQUIRED_PROBE_NOT_DEFINED
    assert eval_rec.dimension_c_status == DimensionCStatus.NOT_EXECUTED.value
    assert eval_rec.dimension_c_functional_satisfied is None
    assert eval_rec.dimension_c_execution_coverage is False
    assert eval_rec.dimension_c_eligible is False
    assert "REQUIRED_PROBE_NOT_DEFINED" in eval_rec.mismatch_types
    assert "CAPABILITY_UNSATISFIED_UNOBSERVED" in eval_rec.mismatch_types
    # Must NOT claim functional failure or execution unavailable
    assert eval_rec.dimension_c_status != DimensionCStatus.FAIL.value
    assert "EXECUTION_UNAVAILABLE" not in eval_rec.mismatch_types


def test_e5_p1_pandas_no_silent_data_science_substitution(catalog_data):
    """Regression Test 3: Broad data-science probe does not silently substitute for pandas without an exact probe."""
    # On pytorch-deep-learning, a data-science probe passes, but there is no exact pandas probe
    data_science_probe = ImageProbeResult(
        probe_id="probe:pytorch-deep-learning:data-science",
        image_id="pytorch-deep-learning",
        image_reference=catalog_data["images"]["pytorch-deep-learning"]["reference"],
        image_digest=parse_image_digest(catalog_data["images"]["pytorch-deep-learning"]["reference"]),
        capability="data-science",
        success=True,
        execution_status=ProbeExecutionStatus.EXECUTED.value,
        runtime_seconds=0.2,
        execution_mode="docker",
    )
    probe_map = {("pytorch-deep-learning", "data-science"): data_science_probe}

    eval_rec = evaluate_recommendation_functional(
        case_id="p2-cpu-only-pandas-feasible-en",
        system_id="P1",
        predicted_image_id="pytorch-deep-learning",
        required_capabilities=["pandas"],
        gold_preferred_image_id="scipy-data-science",
        gold_acceptable_image_ids=["scipy-data-science"],
        catalog=catalog_data,
        probe_results=probe_map,
    )

    # Must be NOT_EXECUTED due to REQUIRED_PROBE_NOT_DEFINED; data-science cannot substitute
    assert eval_rec.dimension_b_catalog_satisfied is False
    assert eval_rec.dimension_c_status == DimensionCStatus.NOT_EXECUTED.value
    assert eval_rec.dimension_c_functional_satisfied is None
    assert eval_rec.dimension_c_eligible is False
    assert "REQUIRED_PROBE_NOT_DEFINED" in eval_rec.mismatch_types


def test_e5_exact_probe_failure_yields_c_fail(catalog_data):
    """Regression Test 4: Genuine probe failure produces Dimension C = FAIL regardless of Dimension B."""
    # Case A: B=True, probe fails
    failed_pandas_probe = ImageProbeResult(
        probe_id="probe:scipy-data-science:pandas",
        image_id="scipy-data-science",
        image_reference=catalog_data["images"]["scipy-data-science"]["reference"],
        image_digest=parse_image_digest(catalog_data["images"]["scipy-data-science"]["reference"]),
        capability="pandas",
        success=False,
        execution_status=ProbeExecutionStatus.EXECUTED.value,
        error_category="ASSERTION_ERROR",
        error_message="DataFrame assertion failed",
        runtime_seconds=0.1,
        execution_mode="docker",
    )
    eval_b_true_fail = evaluate_recommendation_functional(
        case_id="pandas-fail-test",
        system_id="P2",
        predicted_image_id="scipy-data-science",
        required_capabilities=["pandas"],
        gold_preferred_image_id="scipy-data-science",
        gold_acceptable_image_ids=["scipy-data-science"],
        catalog=catalog_data,
        probe_results={("scipy-data-science", "pandas"): failed_pandas_probe},
    )
    assert eval_b_true_fail.dimension_b_catalog_satisfied is True
    assert eval_b_true_fail.dimension_c_status == DimensionCStatus.FAIL.value
    assert eval_b_true_fail.dimension_c_functional_satisfied is False
    assert "CATALOG_PROBE_MISMATCH" in eval_b_true_fail.mismatch_types

    # Case B: B=False, probe executed and failed
    failed_python_probe = ImageProbeResult(
        probe_id="probe:pytorch-deep-learning:python",
        image_id="pytorch-deep-learning",
        image_reference=catalog_data["images"]["pytorch-deep-learning"]["reference"],
        image_digest=parse_image_digest(catalog_data["images"]["pytorch-deep-learning"]["reference"]),
        capability="python",
        success=False,
        execution_status=ProbeExecutionStatus.EXECUTED.value,
        error_category="RUNTIME_ERROR",
        error_message="Python crashed",
        runtime_seconds=0.1,
        execution_mode="docker",
    )
    eval_b_false_fail = evaluate_recommendation_functional(
        case_id="python-fail-test",
        system_id="P2",
        predicted_image_id="pytorch-deep-learning",
        required_capabilities=["python"],
        gold_preferred_image_id="minimal-python",
        gold_acceptable_image_ids=["minimal-python"],
        catalog=catalog_data,
        probe_results={("pytorch-deep-learning", "python"): failed_python_probe},
    )
    assert eval_b_false_fail.dimension_b_catalog_satisfied is False
    assert eval_b_false_fail.dimension_c_status == DimensionCStatus.FAIL.value
    assert eval_b_false_fail.dimension_c_functional_satisfied is False
    assert len(eval_b_false_fail.failed_probes) == 1


def test_e5_runtime_unavailable_distinct_from_missing_probe(catalog_data):
    """Regression Test 5: Exact probe exists but container unavailable -> C=NOT_EXECUTED, EXECUTION_UNAVAILABLE."""
    unavail_probe = ImageProbeResult(
        probe_id="probe:minimal-python:python",
        image_id="minimal-python",
        image_reference=catalog_data["images"]["minimal-python"]["reference"],
        image_digest=parse_image_digest(catalog_data["images"]["minimal-python"]["reference"]),
        capability="python",
        success=False,
        execution_status=ProbeExecutionStatus.IMAGE_NOT_PRESENT.value,
        error_category="IMAGE_NOT_PRESENT",
        runtime_seconds=0.0,
        execution_mode="docker",
    )
    eval_rec = evaluate_recommendation_functional(
        case_id="unavail-test",
        system_id="P1",
        predicted_image_id="minimal-python",
        required_capabilities=["python"],
        gold_preferred_image_id="minimal-python",
        gold_acceptable_image_ids=["minimal-python"],
        catalog=catalog_data,
        probe_results={("minimal-python", "python"): unavail_probe},
    )
    assert eval_rec.dimension_c_status == DimensionCStatus.NOT_EXECUTED.value
    assert eval_rec.dimension_c_functional_satisfied is None
    assert eval_rec.dimension_c_execution_coverage is False
    assert "EXECUTION_UNAVAILABLE" in eval_rec.mismatch_types
    assert "REQUIRED_PROBE_NOT_DEFINED" not in eval_rec.mismatch_types


def test_e5_operational_adequacy_requires_both_b_and_c(catalog_data):
    """Regression Test 6: B=False + C=PASS produces operational_adequacy = False."""
    python_probe = ImageProbeResult(
        probe_id="probe:pytorch-deep-learning:python",
        image_id="pytorch-deep-learning",
        image_reference=catalog_data["images"]["pytorch-deep-learning"]["reference"],
        image_digest=parse_image_digest(catalog_data["images"]["pytorch-deep-learning"]["reference"]),
        capability="python",
        success=True,
        execution_status=ProbeExecutionStatus.EXECUTED.value,
        runtime_seconds=0.1,
        execution_mode="docker",
    )
    eval_underclaim = evaluate_recommendation_functional(
        case_id="threshold-below-vi",
        system_id="P2",
        predicted_image_id="pytorch-deep-learning",
        required_capabilities=["python"],
        gold_preferred_image_id="minimal-python",
        gold_acceptable_image_ids=["minimal-python"],
        catalog=catalog_data,
        probe_results={("pytorch-deep-learning", "python"): python_probe},
    )
    assert eval_underclaim.dimension_b_catalog_satisfied is False
    assert eval_underclaim.dimension_c_status == DimensionCStatus.PASS.value

    report = compute_functional_metrics([eval_underclaim], catalog_data, probe_results=[python_probe])
    summary = report.systems["P2"]

    assert summary.total_recommendations == 1
    assert summary.catalog_capability_satisfied_count == 0
    assert summary.catalog_unsatisfied_count == 1
    assert summary.functional_validation_eligible_count == 1
    assert summary.functional_executed_count == 1
    assert summary.functional_passed_count == 1
    assert summary.catalog_underclaim_count == 1
    # Operational adequacy strictly requires both B and C:
    assert summary.operationally_adequate_count == 0
    assert summary.operational_adequacy_rate == 0.0


def test_e5_deterministic_raw_to_derived_recomputation(catalog_data):
    """Regression Test 7: Raw records recompute B and C deterministically with identical metrics."""
    python_probe = ImageProbeResult(
        probe_id="probe:minimal-python:python",
        image_id="minimal-python",
        image_reference=catalog_data["images"]["minimal-python"]["reference"],
        image_digest=parse_image_digest(catalog_data["images"]["minimal-python"]["reference"]),
        capability="python",
        success=True,
        execution_status=ProbeExecutionStatus.EXECUTED.value,
        runtime_seconds=0.1,
        execution_mode="docker",
    )
    probe_map = {("minimal-python", "python"): python_probe}

    eval1 = evaluate_recommendation_functional(
        case_id="case-det",
        system_id="P2",
        predicted_image_id="minimal-python",
        required_capabilities=["python"],
        gold_preferred_image_id="minimal-python",
        gold_acceptable_image_ids=["minimal-python"],
        catalog=catalog_data,
        probe_results=probe_map,
    )
    eval2 = evaluate_recommendation_functional(
        case_id="case-det",
        system_id="P2",
        predicted_image_id="minimal-python",
        required_capabilities=["python"],
        gold_preferred_image_id="minimal-python",
        gold_acceptable_image_ids=["minimal-python"],
        catalog=catalog_data,
        probe_results=probe_map,
    )
    assert eval1.to_dict() == eval2.to_dict()

    rep1 = compute_functional_metrics([eval1], catalog_data, probe_results=[python_probe])
    rep2 = compute_functional_metrics([eval2], catalog_data, probe_results=[python_probe])
    assert rep1.to_dict() == rep2.to_dict()


def test_e5_current_v13_package_is_current_valid_and_eligible():
    """Regression Test 8: Current v1.3 package is explicitly CURRENT_VALID and eligible_as_current_e5_evidence == True."""
    v13_dir = Path("results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T040730Z")
    if not v13_dir.is_dir():
        pytest.skip("v1.3 package 040730Z not present")

    res = validate_e5_evidence(v13_dir)
    assert res["status"] == "PASS"
    assert res["validator_status"] == "CURRENT_VALID"
    assert res["eligible_as_current_e5_evidence"] is True
    assert res["validation_profile"] == "CURRENT_V1_3"

    eval_records = [
        json.loads(line)
        for line in (v13_dir / "raw" / "functional_evaluations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    target_rec = next(
        (r for r in eval_records if r["case_id"] == "threshold-below-vi" and r["system_id"] == "P2"),
        None,
    )
    assert target_rec is not None
    assert target_rec["dimension_b_catalog_satisfied"] is False
    assert target_rec["dimension_c_status"] == "PASS"
    assert target_rec["dimension_c_functional_satisfied"] is True
    assert "CATALOG_UNDERCLAIM_FUNCTIONAL_PASS" in target_rec["mismatch_types"]


def test_e5_r7_validator_detects_derived_metrics_disagreement(tmp_path):
    """R7: Validator detects derived metrics that disagree with raw evaluation records."""
    import hashlib
    import shutil

    src_dir = Path("results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T040730Z")
    if not src_dir.is_dir():
        pytest.skip("040730Z package not present")

    pkg_dir = tmp_path / "pkg"
    shutil.copytree(src_dir, pkg_dir)

    def recompute_sha256sums():
        lines = []
        for p in sorted(pkg_dir.rglob("*")):
            if p.is_file() and p.name != "SHA256SUMS":
                rel = p.relative_to(pkg_dir)
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
                lines.append(f"{digest}  {rel}")
        (pkg_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Base package validates
    assert validate_e5_evidence(pkg_dir)["status"] == "PASS"

    metrics_path = pkg_dir / "derived" / "functional_metrics.json"
    orig_content = metrics_path.read_text(encoding="utf-8")

    fields_to_test = [
        ("catalog_capability_satisfied_count", "catalog_capability_satisfied_count mismatch"),
        ("functional_validation_eligible_count", "functional_validation_eligible_count mismatch"),
        ("functional_passed_count", "functional_passed_count mismatch"),
        ("required_probe_not_defined_count", "required_probe_not_defined_count mismatch"),
        ("execution_unavailable_count", "execution_unavailable_count mismatch"),
        ("catalog_underclaim_count", "catalog_underclaim_count mismatch"),
        ("operationally_adequate_count", "operationally_adequate_count mismatch"),
    ]

    for field_name, expected_error in fields_to_test:
        data = json.loads(orig_content)
        data["systems"]["P2"][field_name] = 999
        metrics_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        recompute_sha256sums()

        with pytest.raises(EvidenceValidationError, match=expected_error):
            validate_e5_evidence(pkg_dir)

    # Restore and verify clean pass
    metrics_path.write_text(orig_content, encoding="utf-8")
    recompute_sha256sums()
    assert validate_e5_evidence(pkg_dir)["status"] == "PASS"

