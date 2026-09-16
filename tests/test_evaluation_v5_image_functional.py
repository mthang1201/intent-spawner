"""Unit and integration tests for Protocol-v5 image functional validation (E5).

Validates the fail-closed contracts, 3-state Dimension C evaluation, explicit
count denominators, security verification, and evidence package validation.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import MagicMock
import pytest
import yaml

from evaluation_v5.image_storage import (
    BaseProbeRunner,
    CapabilityProbeStatus,
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
    ProbeExecutionOrigin,
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
from evaluation_v5.image_storage.runner import RuntimeImageIdentity
from evaluation_v5.offline.source_run import (
    SourceRunProvenanceError,
    VerifiedRecommendationRunProvenance,
    verify_recommendation_run_provenance,
)
from evaluation_v5.schemas import EvidenceStatus, ProtocolV5Manifest


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "recommender" / "image-catalog.yaml"
SPLIT_PATH = ROOT / "benchmarks_v5" / "v5-development.yaml"
SOURCE_RUN_DIR = (
    ROOT
    / "results_v5"
    / "protocol-v5.0.0"
    / "E1"
    / "20260825T-observed-p1-p2-development-v1"
)


@pytest.fixture
def catalog_data() -> dict:
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def recommendation_run() -> VerifiedRecommendationRunProvenance:
    return verify_recommendation_run_provenance(SOURCE_RUN_DIR)


def _rewrite_checksums(package_dir: Path) -> None:
    lines = []
    for path in sorted(package_dir.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f"{digest}  {path.relative_to(package_dir)}")
    (package_dir / "SHA256SUMS").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


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
                stdout=json.dumps(
                    [
                        {
                            "RepoDigests": [tampered_ref],
                            "Os": "linux",
                            "Architecture": "amd64",
                        }
                    ]
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_inspect)

    with pytest.raises(SecurityVerificationError, match="Runtime image digest .* does not match expected"):
        runner.run_probe(image_spec, probe)


def test_cuda_probe_without_site_packages_is_unavailable():
    """Python -S cannot turn the CUDA probe into a false success."""
    probe = create_capability_probe("tensorflow-deep-learning", "cuda-userspace")
    result = subprocess.run(
        [sys.executable, "-S", "-c", probe.script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 3
    metadata = json.loads(
        next(
            line.removeprefix("PROBE_META:")
            for line in result.stdout.splitlines()
            if line.startswith("PROBE_META:")
        )
    )
    assert metadata["cuda_probe_status"] == CapabilityProbeStatus.UNAVAILABLE.value
    assert metadata["cuda_api"] == "none"
    assert metadata["cuda_library"] == "none"


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

    from evaluation_v5.image_storage import CAPABILITY_PROBE_TEMPLATES

    with pytest.raises(TypeError):
        CAPABILITY_PROBE_TEMPLATES["python"]["script"] = "arbitrary"  # type: ignore[index]


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


def test_live_origin_requires_runtime_factory(catalog_data):
    assert (
        DockerProbeRunner(catalog_data).execution_origin
        == ProbeExecutionOrigin.SYNTHETIC_TEST.value
    )
    assert (
        KubernetesProbeRunner(catalog_data).execution_origin
        == ProbeExecutionOrigin.SYNTHETIC_TEST.value
    )
    assert (
        create_probe_runner(catalog_data, mode="docker").execution_origin
        == ProbeExecutionOrigin.LIVE_DOCKER.value
    )
    assert (
        create_probe_runner(catalog_data, mode="kubernetes").execution_origin
        == ProbeExecutionOrigin.LIVE_KUBERNETES.value
    )


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
                stdout=json.dumps(
                    [
                        {
                            "RepoDigests": [image_spec.image_reference],
                            "Os": "linux",
                            "Architecture": "amd64",
                        }
                    ]
                ),
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
    assert res.execution_origin == ProbeExecutionOrigin.SYNTHETIC_TEST.value
    assert res.cleanup_succeeded is True


def test_kubernetes_probe_runner_mocked(catalog_data, monkeypatch):
    manifest = build_image_probe_manifest(catalog_data, CATALOG_PATH)
    image_spec = manifest.images[0]
    probe = image_spec.probes[0]

    runner = KubernetesProbeRunner(catalog_data, namespace="test-ns")

    def mock_kubectl(args, timeout=30.0):
        if "run" in args:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="pod created", stderr="")
        if "logs" in args:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout='PROBE_META:{"python_version": "3.11.8"}\n', stderr=""
            )
        if args[:2] == ["get", "node"]:
            node = {
                "metadata": {
                    "labels": {
                        "kubernetes.io/os": "linux",
                        "kubernetes.io/arch": "amd64",
                    }
                }
            }
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=json.dumps(node), stderr=""
            )
        if args[0] == "get" and args[1].startswith("pod/"):
            pod = {
                "spec": {"nodeName": "worker-1"},
                "status": {
                    "phase": "Succeeded",
                    "containerStatuses": [
                        {
                            "imageID": f"docker-pullable://approved@{image_spec.image_digest}",
                            "state": {
                                "terminated": {"exitCode": 0, "reason": "Completed"}
                            },
                        }
                    ],
                },
            }
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=json.dumps(pod), stderr=""
            )
        if "delete" in args:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="deleted", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_kubectl", mock_kubectl)

    res = runner.run_probe(image_spec, probe)
    assert res.success is True
    assert res.is_executed is True
    assert res.execution_mode == "kubernetes"
    assert res.import_version_metadata == {"python_version": "3.11.8"}
    assert res.execution_origin == ProbeExecutionOrigin.SYNTHETIC_TEST.value
    assert res.cleanup_succeeded is True


def test_probe_bounds_and_program_allowlist_fail_before_execution(
    catalog_data, monkeypatch
):
    with pytest.raises(ProbeExecutionError, match="timeout"):
        create_capability_probe("minimal-python", "python", timeout_seconds=121)
    with pytest.raises(ProbeExecutionError, match="CPU"):
        create_capability_probe(
            "minimal-python", "python", cpu_limit="3000m"
        )
    with pytest.raises(ProbeExecutionError, match="memory"):
        create_capability_probe(
            "minimal-python", "python", memory_limit="3Gi"
        )

    image_spec = build_image_probe_manifest(catalog_data, CATALOG_PATH).images[0]
    approved = image_spec.probes[0]
    tampered = ProbeSpec(
        probe_id=approved.probe_id,
        capability=approved.capability,
        description=approved.description,
        script="print('arbitrary program')",
        timeout_seconds=approved.timeout_seconds,
        cpu_limit=approved.cpu_limit,
        memory_limit=approved.memory_limit,
        expected_metadata_keys=approved.expected_metadata_keys,
    )
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("runtime must not be invoked")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(ProbeExecutionError, match="approved manifest probe"):
        DockerProbeRunner(catalog_data).run_probe(image_spec, tampered)
    assert called is False


def test_docker_timeout_and_interrupt_always_remove_exact_container(
    catalog_data, monkeypatch
):
    image_spec = build_image_probe_manifest(catalog_data, CATALOG_PATH).images[0]
    probe = image_spec.probes[0]
    commands: list[list[str]] = []

    def timeout_run(cmd, *args, **kwargs):
        commands.append(cmd)
        if cmd[:3] == ["docker", "image", "inspect"]:
            payload = [
                {
                    "RepoDigests": [image_spec.image_reference],
                    "Os": "linux",
                    "Architecture": "amd64",
                }
            ]
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(payload), stderr=""
            )
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd, timeout=probe.timeout_seconds)
        if cmd[:3] == ["docker", "container", "inspect"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout='"2026-09-09T00:00:00Z"\n', stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", timeout_run)
    result = DockerProbeRunner(catalog_data).run_probe(image_spec, probe)
    run_cmd = next(cmd for cmd in commands if cmd[:2] == ["docker", "run"])
    name = run_cmd[run_cmd.index("--name") + 1]
    assert result.execution_identity == name
    assert result.error_category == "TIMEOUT"
    assert ["docker", "stop", "--time=1", name] in commands
    assert ["docker", "rm", "--force", name] in commands
    assert "--rm" not in run_cmd
    assert "--network=none" in run_cmd
    assert "--read-only" in run_cmd
    assert "--cap-drop=ALL" in run_cmd
    assert any(item.startswith("--cpus=") for item in run_cmd)
    assert any(item.startswith("--memory=") for item in run_cmd)
    assert any(item.startswith("--pids-limit=") for item in run_cmd)

    commands.clear()

    def interrupt_run(cmd, *args, **kwargs):
        commands.append(cmd)
        if cmd[:3] == ["docker", "image", "inspect"]:
            payload = [
                {
                    "RepoDigests": [image_spec.image_reference],
                    "Os": "linux",
                    "Architecture": "amd64",
                }
            ]
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(payload), stderr=""
            )
        if cmd[:2] == ["docker", "run"]:
            raise KeyboardInterrupt
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", interrupt_run)
    with pytest.raises(KeyboardInterrupt):
        DockerProbeRunner(catalog_data).run_probe(image_spec, probe)
    run_cmd = next(cmd for cmd in commands if cmd[:2] == ["docker", "run"])
    name = run_cmd[run_cmd.index("--name") + 1]
    assert ["docker", "stop", "--time=1", name] in commands
    assert ["docker", "rm", "--force", name] in commands


def test_kubernetes_pending_timeout_and_interrupt_delete_exact_pod(
    catalog_data, monkeypatch
):
    manifest = build_image_probe_manifest(
        catalog_data, CATALOG_PATH, timeout_seconds=0.01
    )
    image_spec = manifest.images[0]
    probe = image_spec.probes[0]
    calls: list[list[str]] = []
    runner = KubernetesProbeRunner(
        catalog_data, namespace="test-ns", poll_interval_seconds=0
    )

    def pending(args, timeout=30.0):
        calls.append(args)
        if args[0] == "run":
            return subprocess.CompletedProcess(args, 0, stdout="created", stderr="")
        if args[0] == "get":
            pod = {
                "status": {
                    "phase": "Pending",
                    "conditions": [
                        {
                            "type": "Ready",
                            "status": "False",
                            "reason": "ContainersNotReady",
                        }
                    ],
                    "containerStatuses": [
                        {
                            "state": {
                                "waiting": {
                                    "reason": "ImagePullBackOff",
                                    "message": "pull refused",
                                }
                            }
                        }
                    ],
                }
            }
            return subprocess.CompletedProcess(
                args, 0, stdout=json.dumps(pod), stderr=""
            )
        return subprocess.CompletedProcess(args, 0, stdout="deleted", stderr="")

    monkeypatch.setattr(runner, "_kubectl", pending)
    result = runner.run_probe(image_spec, probe)
    run_args = next(args for args in calls if args[0] == "run")
    pod_name = run_args[1]
    overrides = json.loads(
        next(item.removeprefix("--overrides=") for item in run_args if item.startswith("--overrides="))
    )
    pod_spec = overrides["spec"]
    container = pod_spec["containers"][0]
    assert pod_spec["activeDeadlineSeconds"] == 1
    assert pod_spec["automountServiceAccountToken"] is False
    assert container["resources"]["requests"] == container["resources"]["limits"]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    delete_args = next(args for args in calls if args[0] == "delete")
    assert delete_args[1] == f"pod/{pod_name}"
    assert result.execution_identity == pod_name
    assert result.error_category == "TIMEOUT"
    assert result.execution_status == ProbeExecutionStatus.CONTAINER_UNAVAILABLE.value

    calls.clear()

    def interrupted(args, timeout=30.0):
        calls.append(args)
        if args[0] == "run":
            raise KeyboardInterrupt
        return subprocess.CompletedProcess(args, 0, stdout="deleted", stderr="")

    monkeypatch.setattr(runner, "_kubectl", interrupted)
    with pytest.raises(KeyboardInterrupt):
        runner.run_probe(image_spec, probe)
    run_args = next(args for args in calls if args[0] == "run")
    delete_args = next(args for args in calls if args[0] == "delete")
    assert delete_args[1] == f"pod/{run_args[1]}"


def test_kubernetes_terminal_failure_captures_termination_reason(
    catalog_data, monkeypatch
):
    image_spec = build_image_probe_manifest(catalog_data, CATALOG_PATH).images[0]
    probe = image_spec.probes[0]
    runner = KubernetesProbeRunner(
        catalog_data, namespace="test-ns", poll_interval_seconds=0
    )

    def failed(args, timeout=30.0):
        if args[0] == "run":
            return subprocess.CompletedProcess(args, 0, stdout="created", stderr="")
        if args[:2] == ["get", "node"]:
            node = {
                "metadata": {
                    "labels": {
                        "kubernetes.io/os": "linux",
                        "kubernetes.io/arch": "amd64",
                    }
                }
            }
            return subprocess.CompletedProcess(
                args, 0, stdout=json.dumps(node), stderr=""
            )
        if args[0] == "get":
            pod = {
                "spec": {"nodeName": "worker-1"},
                "status": {
                    "phase": "Failed",
                    "containerStatuses": [
                        {
                            "imageID": f"containerd://approved@{image_spec.image_digest}",
                            "state": {
                                "terminated": {
                                    "exitCode": 1,
                                    "reason": "Error",
                                    "message": "probe assertion failed",
                                }
                            },
                        }
                    ],
                },
            }
            return subprocess.CompletedProcess(
                args, 0, stdout=json.dumps(pod), stderr=""
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_kubectl", failed)
    result = runner.run_probe(image_spec, probe)
    assert result.execution_status == ProbeExecutionStatus.EXECUTED.value
    assert result.functional_status == CapabilityProbeStatus.FAILURE.value
    assert result.error_category == "RUNTIME_ERROR"
    assert "probe assertion failed" in result.error_message
    assert result.cleanup_succeeded is True


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


def test_e5_partial_execution_marks_package_incomplete(
    tmp_path, monkeypatch, recommendation_run
):
    """Regression Test 3: One unavailable image among four marks package as INCOMPLETE."""
    out_dir = tmp_path / "e5-test-incomplete"

    def mock_create(catalog, mode="auto", **kwargs):
        return SyntheticProbeRunner(
            catalog, unavailable_images=["pytorch-deep-learning"]
        )

    monkeypatch.setattr("evaluation_v5.image_storage.__main__.create_probe_runner", mock_create)

    run_e5_evaluation(
        catalog_path=CATALOG_PATH,
        recommendation_run=recommendation_run,
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


def test_e5_fake_full_execution_cannot_be_relabelled_observed(
    tmp_path, monkeypatch, recommendation_run
):
    """Selecting Docker mode cannot relabel synthetic observations as OBSERVED."""
    out_dir = tmp_path / "e5-test-observed"

    def mock_create(catalog, mode="auto", **kwargs):
        return SyntheticProbeRunner(catalog)

    monkeypatch.setattr("evaluation_v5.image_storage.__main__.create_probe_runner", mock_create)

    run_e5_evaluation(
        catalog_path=CATALOG_PATH,
        recommendation_run=recommendation_run,
        mode="docker",
        output_dir=out_dir,
        run_id="e5-test-observed",
    )

    manifest_raw = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_raw["execution_status"] == EvidenceStatus.INCOMPLETE.value

    status_raw = json.loads((out_dir / "report" / "status.json").read_text(encoding="utf-8"))
    assert status_raw["status"] == EvidenceStatus.INCOMPLETE.value

    # The package is valid but explicitly ineligible as current observed evidence.
    res = validate_e5_evidence(out_dir)
    assert res["status"] == "PASS"
    assert res["execution_status"] == EvidenceStatus.INCOMPLETE.value
    assert res["probes_unavailable"] == 0
    assert res["probes_executed"] == res["total_probes_configured"]
    assert res["eligible_as_current_e5_evidence"] is False


def test_e5_validate_evidence_recomputes_and_validates(
    tmp_path, recommendation_run
):
    """Regression Test 11: validate_e5_evidence verifies SHA256SUMS and enforces semantic consistency."""
    out_dir = tmp_path / "e5-test-validation"
    run_e5_evaluation(
        catalog_path=CATALOG_PATH,
        recommendation_run=recommendation_run,
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


def test_end_to_end_cli_dry_run(tmp_path, recommendation_run):
    out_dir = tmp_path / "e5-test-dry-run"
    run_e5_evaluation(
        catalog_path=CATALOG_PATH,
        recommendation_run=recommendation_run,
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


def test_end_to_end_with_verified_recommendation_run(tmp_path, recommendation_run):
    out_dir = tmp_path / "e5-test-recs"

    run_e5_evaluation(
        catalog_path=CATALOG_PATH,
        recommendation_run=recommendation_run,
        mode="synthetic",
        output_dir=out_dir,
        run_id="e5-test-recs",
    )

    metrics_raw = json.loads((out_dir / "derived" / "functional_metrics.json").read_text(encoding="utf-8"))
    assert metrics_raw["total_evaluations"] == 36
    assert "P1" in metrics_raw["systems"]
    assert "P2" in metrics_raw["systems"]

    source_raw = (out_dir / "raw" / "source-recommendations.jsonl").read_bytes()
    original_raw = (SOURCE_RUN_DIR / "raw" / "recommendations.jsonl").read_bytes()
    assert source_raw == original_raw
    evaluations = [
        json.loads(line)
        for line in (
            out_dir / "raw" / "functional_evaluations.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert all(row["source_run_sha256"] for row in evaluations)
    assert all(row["source_recommendation_record_id"] for row in evaluations)
    assert all(row["source_configuration_identity_sha256"] for row in evaluations)


def test_e5_rejects_wrong_or_stale_source_run_before_execution(
    tmp_path, catalog_data, monkeypatch
):
    with pytest.raises(TypeError, match="VerifiedRecommendationRunProvenance"):
        run_e5_evaluation(
            catalog_path=CATALOG_PATH,
            recommendation_run={"caller": "supplied"},  # type: ignore[arg-type]
            mode="dry-run",
            output_dir=tmp_path / "wrong-source",
        )

    copied_source = tmp_path / "source"
    shutil.copytree(SOURCE_RUN_DIR, copied_source)
    capability = verify_recommendation_run_provenance(copied_source)
    records_path = copied_source / "raw" / "recommendations.jsonl"
    records_path.write_bytes(records_path.read_bytes() + b" ")

    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("runtime must not be constructed")

    monkeypatch.setattr(
        "evaluation_v5.image_storage.__main__.create_probe_runner", forbidden
    )
    with pytest.raises(SourceRunProvenanceError):
        run_e5_evaluation(
            catalog_path=CATALOG_PATH,
            recommendation_run=capability,
            mode="dry-run",
            output_dir=tmp_path / "stale-source",
        )
    assert called is False
    assert not (tmp_path / "stale-source").exists()


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("source-record", "source recommendation snapshot checksum"),
        ("source-image", "record/image mismatches"),
        ("configuration", "does not derive from source recommendation provenance"),
        ("record-id", "do not join one-to-one"),
        ("selected-digest", "selected image digest mismatches"),
    ],
)
def test_e5_validator_rejects_provenance_tampering(
    tmp_path, recommendation_run, mutation, error
):
    package = tmp_path / mutation
    run_e5_evaluation(
        catalog_path=CATALOG_PATH,
        recommendation_run=recommendation_run,
        mode="dry-run",
        output_dir=package,
        run_id=f"tamper-{mutation}",
    )

    if mutation in {"source-record", "source-image"}:
        path = package / "raw" / "source-recommendations.jsonl"
        rows = path.read_text(encoding="utf-8").splitlines()
        first = json.loads(rows[0])
        first["predicted_image_id"] = "forged-image"
        rows[0] = json.dumps(first)
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        if mutation == "source-image":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            provenance_path = package / "raw" / "source-recommendation-run.json"
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            provenance["recommendation_run_sha256"] = digest
            provenance["source_artifacts"]["recommendations_sha256"] = digest
            provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    elif mutation == "configuration":
        path = package / "manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["retrieval_configuration"]["dense_weight"] = 999
        path.write_text(json.dumps(value), encoding="utf-8")
    else:
        path = package / "raw" / "functional_evaluations.jsonl"
        rows = path.read_text(encoding="utf-8").splitlines()
        first = json.loads(rows[0])
        if mutation == "record-id":
            first["source_recommendation_record_id"] = "forged-record"
        else:
            first["selected_image_digest"] = "sha256:" + "f" * 64
        rows[0] = json.dumps(first)
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    _rewrite_checksums(package)

    with pytest.raises(EvidenceValidationError, match=error):
        validate_e5_evidence(package)


def test_archived_e5_cannot_be_forged_into_current_evidence(tmp_path):
    source = Path(
        "results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T040730Z"
    )
    if not source.is_dir():
        pytest.skip("archived v1.3 E5 package not present")
    package = tmp_path / "forged-current"
    shutil.copytree(source, package)
    probe_manifest = package / "raw" / "probe_manifest.json"
    value = json.loads(probe_manifest.read_text(encoding="utf-8"))
    value["schema_version"] = "protocol-v5-image-probe-manifest-v1.2.0"
    probe_manifest.write_text(json.dumps(value), encoding="utf-8")
    _rewrite_checksums(package)

    with pytest.raises(EvidenceValidationError, match="missing source provenance"):
        validate_e5_evidence(package)


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
        source_recommendation_run=None,
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


def test_e5_archived_v13_package_is_valid_with_provenance_limitations():
    """Archived v1.3 stays valid but cannot satisfy the sealed-source contract."""
    v13_dir = Path("results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T040730Z")
    if not v13_dir.is_dir():
        pytest.skip("v1.3 package 040730Z not present")

    res = validate_e5_evidence(v13_dir)
    assert res["status"] == "PASS"
    assert res["validator_status"] == "LEGACY_VALID"
    assert res["eligible_as_current_e5_evidence"] is False
    assert res["validation_profile"] == "LEGACY_SCHEMA_V1_3"
    assert "UNSEALED_RECOMMENDATION_PROVENANCE" in res["limitations"]
    assert "MISSING_RECOMMENDATION_RECORD_JOIN" in res["limitations"]

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


def test_probe_spec_and_result_version_tracking():
    """Test probe_id and probe_version tracking on ProbeSpec and ImageProbeResult with backwards compatibility."""
    from evaluation_v5.image_storage.contracts import ImageProbeResult, ProbeSpec

    spec = ProbeSpec(
        probe_id="probe:test:python",
        capability="python",
        description="Test python probe",
        script="print(1)",
        probe_version="v1.2.0",
    )
    data = spec.to_dict()
    assert data["probe_version"] == "v1.2.0"
    reconstructed = ProbeSpec.from_dict(data)
    assert reconstructed.probe_version == "v1.2.0"

    # Backwards compatibility: older dict without probe_version defaults to v1.0.0
    legacy_data = {
        "probe_id": "probe:legacy:python",
        "capability": "python",
        "description": "Legacy",
        "script": "print(1)",
    }
    legacy_spec = ProbeSpec.from_dict(legacy_data)
    assert legacy_spec.probe_version == "v1.0.0"

    res = ImageProbeResult(
        probe_id="probe:test:python",
        probe_version="v1.2.0",
        image_id="test-img",
        image_reference="test@sha256:" + "a" * 64,
        image_digest="sha256:" + "a" * 64,
        capability="python",
        execution_status="EXECUTED",
        success=True,
    )
    res_data = res.to_dict()
    assert res_data["probe_version"] == "v1.2.0"
    reconstructed_res = ImageProbeResult.from_dict(res_data)
    assert reconstructed_res.probe_version == "v1.2.0"

    # Backwards compatibility for ImageProbeResult
    legacy_res_data = dict(res_data)
    del legacy_res_data["probe_version"]
    legacy_res = ImageProbeResult.from_dict(legacy_res_data)
    assert legacy_res.probe_version == "v1.0.0"


def test_create_probe_runner_local_mode(catalog_data):
    """Test create_probe_runner supports 'local' and 'docker-local' modes."""
    from evaluation_v5.image_storage.runner import DockerProbeRunner, create_probe_runner

    runner = create_probe_runner(catalog_data, mode="local")
    assert isinstance(runner, DockerProbeRunner)

    runner_docker_local = create_probe_runner(catalog_data, mode="docker-local")
    assert isinstance(runner_docker_local, DockerProbeRunner)
