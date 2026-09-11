"""Container and Kubernetes functional probe runner for approved images."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re
import subprocess
import time
from typing import Any, Mapping, Sequence
import uuid

from .contracts import (
    CapabilityProbeStatus,
    IMAGE_PROBE_RECORD_SCHEMA_VERSION,
    ImageProbeManifest,
    ImageProbeResult,
    ImageProbeSpec,
    ProbeExecutionError,
    ProbeExecutionOrigin,
    ProbeExecutionStatus,
    ProbeSpec,
    SecurityVerificationError,
    validate_approved_image_spec,
)
from .manifest import validate_approved_probe_spec


PROBE_META_PREFIX = "PROBE_META:"
_RUNTIME_DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
_LIVE_RUNNER_CONSTRUCTION_KEY = object()


@dataclass(frozen=True, slots=True)
class RuntimeImageIdentity:
    present: bool
    digest: str | None
    platform: str | None
    runtime_image_id: str | None
    error: str | None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _extract_probe_metadata(stdout: str) -> dict[str, str]:
    """Parse JSON metadata printed on a PROBE_META: prefixed line."""
    for line in stdout.splitlines():
        trimmed = line.strip()
        if trimmed.startswith(PROBE_META_PREFIX):
            try:
                data = json.loads(trimmed[len(PROBE_META_PREFIX):])
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
            except (json.JSONDecodeError, ValueError):
                pass
    return {}


def _categorize_error(
    returncode: int | None,
    stderr: str,
    timed_out: bool,
) -> str:
    """Categorize execution errors based on exit code and error messages."""
    if timed_out:
        return "TIMEOUT"
    if returncode == 3:
        return "CAPABILITY_UNAVAILABLE"
    if returncode == 4:
        return "CUDA_API_FAILURE"
    if returncode == 137 or "Killed" in stderr or "OOM" in stderr:
        return "OOM"
    stderr_lower = stderr.lower()
    if "no such image" in stderr_lower or "unable to find image" in stderr_lower:
        return "IMAGE_NOT_PRESENT"
    if "modulenotfounderror" in stderr_lower or "importerror" in stderr_lower:
        return "IMPORT_ERROR"
    if "assertionerror" in stderr_lower:
        return "ASSERTION_FAILURE"
    if "syntaxerror" in stderr_lower:
        return "SYNTAX_ERROR"
    if "permission denied" in stderr_lower:
        return "CONTAINER_LAUNCH_FAILED"
    return "RUNTIME_ERROR"


def _bounded_python_script(script: str, timeout_seconds: float) -> str:
    """Add an in-container deadline; the client timeout remains a second guard."""
    alarm_seconds = max(1, int(math.ceil(timeout_seconds)))
    return (
        "import signal\n"
        "def _probe_deadline(_signum, _frame):\n"
        "    raise TimeoutError('approved probe exceeded runtime deadline')\n"
        f"signal.signal(signal.SIGALRM, _probe_deadline)\n"
        f"signal.alarm({alarm_seconds})\n"
        f"exec(compile({script!r}, '<approved-capability-probe>', 'exec'))\n"
    )


def _classify_probe_output(
    probe: ProbeSpec,
    *,
    returncode: int | None,
    stdout: str,
    stderr: str,
    timed_out: bool,
) -> tuple[str, bool, dict[str, str], str | None, str | None]:
    """Classify an executed probe without treating process exit as sufficient proof."""
    metadata = _extract_probe_metadata(stdout)
    if timed_out:
        return (
            CapabilityProbeStatus.FAILURE.value,
            False,
            metadata,
            "TIMEOUT",
            stderr.strip() or "Probe execution timed out",
        )

    if probe.capability == "cuda-userspace":
        cuda_status = metadata.get("cuda_probe_status")
        if cuda_status not in {item.value for item in CapabilityProbeStatus}:
            return (
                CapabilityProbeStatus.FAILURE.value,
                False,
                metadata,
                "PROBE_OUTPUT_INVALID",
                "CUDA probe did not emit a valid cuda_probe_status",
            )
        if cuda_status == CapabilityProbeStatus.SUCCESS.value:
            required = {"cuda_probe_status", "cuda_api", "cuda_library"}
            if returncode != 0 or not required.issubset(metadata):
                return (
                    CapabilityProbeStatus.FAILURE.value,
                    False,
                    metadata,
                    "PROBE_OUTPUT_INVALID",
                    "CUDA success lacked a supported library/API observation",
                )
            return cuda_status, True, metadata, None, None
        category = (
            "CAPABILITY_UNAVAILABLE"
            if cuda_status == CapabilityProbeStatus.UNAVAILABLE.value
            else "CUDA_API_FAILURE"
        )
        return cuda_status, False, metadata, category, stderr.strip() or category

    missing_metadata = set(probe.expected_metadata_keys) - set(metadata)
    if returncode == 0 and not missing_metadata:
        return CapabilityProbeStatus.SUCCESS.value, True, metadata, None, None
    if returncode == 0:
        return (
            CapabilityProbeStatus.FAILURE.value,
            False,
            metadata,
            "PROBE_OUTPUT_INVALID",
            f"Probe output omitted required metadata keys: {sorted(missing_metadata)}",
        )
    category = _categorize_error(returncode, stderr, False)
    return (
        CapabilityProbeStatus.FAILURE.value,
        False,
        metadata,
        category,
        stderr.strip() or "Probe execution failed",
    )


class BaseProbeRunner:
    """Abstract base class for running functional probes against approved images."""

    def __init__(self, catalog: Mapping[str, Any]) -> None:
        self.catalog = catalog

    def run_probe(
        self,
        image_spec: ImageProbeSpec,
        probe: ProbeSpec,
    ) -> ImageProbeResult:
        raise NotImplementedError

    def run_all(
        self,
        manifest: ImageProbeManifest,
    ) -> list[ImageProbeResult]:
        # Validate the complete caller-supplied manifest before starting even
        # one workload, so an arbitrary trailing program cannot create a
        # partially executed package.
        for image_spec in manifest.images:
            validate_approved_image_spec(image_spec, self.catalog)
            for probe in image_spec.probes:
                validate_approved_probe_spec(image_spec, probe)
        results: list[ImageProbeResult] = []
        for image_spec in manifest.images:
            for probe in image_spec.probes:
                results.append(self.run_probe(image_spec, probe))
        return results


class DryRunProbeRunner(BaseProbeRunner):
    """Dry-run probe runner that produces explicit NOT_EXECUTED records without fabrication."""

    def __init__(
        self,
        catalog: Mapping[str, Any],
        *,
        simulated_status: str = ProbeExecutionStatus.NOT_EXECUTED_DRY_RUN.value,
    ) -> None:
        super().__init__(catalog)
        self.simulated_status = simulated_status

    def run_probe(
        self,
        image_spec: ImageProbeSpec,
        probe: ProbeSpec,
    ) -> ImageProbeResult:
        digest = validate_approved_image_spec(image_spec, self.catalog)
        validate_approved_probe_spec(image_spec, probe)

        return ImageProbeResult(
            schema_version=IMAGE_PROBE_RECORD_SCHEMA_VERSION,
            probe_id=probe.probe_id,
            image_id=image_spec.image_id,
            image_reference=image_spec.image_reference,
            image_digest=digest,
            capability=probe.capability,
            success=False,
            functional_status=CapabilityProbeStatus.UNAVAILABLE.value,
            execution_status=self.simulated_status,
            execution_origin=ProbeExecutionOrigin.DRY_RUN.value,
            resolved_image_digest=None,
            import_version_metadata={},
            runtime_seconds=0.0,
            error_category=self.simulated_status,
            error_message="Probe not executed: dry-run mode active",
            stdout=None,
            execution_mode="dry_run",
            timestamp_utc=_utc_now(),
        )


class SyntheticProbeRunner(BaseProbeRunner):
    """Configurable synthetic runner for hermetic testing of success, failure, and mismatches."""

    def __init__(
        self,
        catalog: Mapping[str, Any],
        *,
        failing_capabilities: Mapping[str, Sequence[str]] | None = None,
        unavailable_images: Sequence[str] | None = None,
        injected_metadata: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        super().__init__(catalog)
        self.failing_capabilities = {
            k: set(v) for k, v in (failing_capabilities or {}).items()
        }
        self.unavailable_images = set(unavailable_images or ())
        self.injected_metadata = injected_metadata or {}

    def run_probe(
        self,
        image_spec: ImageProbeSpec,
        probe: ProbeSpec,
    ) -> ImageProbeResult:
        digest = validate_approved_image_spec(image_spec, self.catalog)
        validate_approved_probe_spec(image_spec, probe)

        # 1. Simulate image unavailable / not present
        if image_spec.image_id in self.unavailable_images:
            return ImageProbeResult(
                schema_version=IMAGE_PROBE_RECORD_SCHEMA_VERSION,
                probe_id=probe.probe_id,
                image_id=image_spec.image_id,
                image_reference=image_spec.image_reference,
                image_digest=digest,
                capability=probe.capability,
                success=False,
                functional_status=CapabilityProbeStatus.UNAVAILABLE.value,
                execution_status=ProbeExecutionStatus.IMAGE_NOT_PRESENT.value,
                execution_origin=ProbeExecutionOrigin.SYNTHETIC_TEST.value,
                resolved_image_digest=None,
                import_version_metadata={},
                runtime_seconds=0.0,
                error_category="IMAGE_NOT_PRESENT",
                error_message=f"Image {image_spec.image_id} is not present in local store",
                stdout=None,
                execution_mode="synthetic",
                timestamp_utc=_utc_now(),
            )

        # 2. Simulate executed probe (either success or genuine functional failure)
        failing = self.failing_capabilities.get(image_spec.image_id, set())
        is_fail = probe.capability in failing
        metadata = dict(self.injected_metadata.get(f"{image_spec.image_id}:{probe.capability}", {}))
        if not metadata and not is_fail:
            if probe.capability == "cuda-userspace":
                metadata.update(
                    {
                        "cuda_probe_status": CapabilityProbeStatus.SUCCESS.value,
                        "cuda_api": "synthetic.test",
                        "cuda_library": "synthetic",
                    }
                )
            else:
                metadata[f"{probe.capability}_version"] = "1.0.0-synthetic"
        elif not metadata and probe.capability == "cuda-userspace":
            metadata.update(
                {
                    "cuda_probe_status": CapabilityProbeStatus.FAILURE.value,
                    "cuda_api": "synthetic.test",
                    "cuda_library": "synthetic",
                }
            )

        return ImageProbeResult(
            schema_version=IMAGE_PROBE_RECORD_SCHEMA_VERSION,
            probe_id=probe.probe_id,
            image_id=image_spec.image_id,
            image_reference=image_spec.image_reference,
            image_digest=digest,
            capability=probe.capability,
            success=not is_fail,
            functional_status=(
                CapabilityProbeStatus.FAILURE.value
                if is_fail
                else CapabilityProbeStatus.SUCCESS.value
            ),
            execution_status=ProbeExecutionStatus.EXECUTED.value,
            execution_origin=ProbeExecutionOrigin.SYNTHETIC_TEST.value,
            execution_identity=f"synthetic:{image_spec.image_id}:{probe.capability}",
            resolved_image_digest=digest,
            resolved_image_platform="synthetic/test",
            runtime_image_id=f"synthetic@{digest}",
            cleanup_succeeded=None,
            import_version_metadata=metadata,
            runtime_seconds=0.015,
            error_category="IMPORT_ERROR" if is_fail else None,
            error_message=f"Synthetic genuine probe failure for {probe.capability}" if is_fail else None,
            stdout=f"PROBE_META:{json.dumps(metadata)}" if not is_fail else None,
            execution_mode="synthetic",
            timestamp_utc=_utc_now(),
        )


class DockerProbeRunner(BaseProbeRunner):
    """Live probe runner executing bounded probes in Docker containers."""

    def __init__(
        self,
        catalog: Mapping[str, Any],
        *,
        default_cpu_limit: str = "1.0",
        default_memory_limit: str = "1g",
        pids_limit: int = 100,
        pull_policy: str = "never",
        image_setup_timeout_seconds: float = 120.0,
        _construction_key: object | None = None,
    ) -> None:
        super().__init__(catalog)
        self.default_cpu_limit = default_cpu_limit
        self.default_memory_limit = default_memory_limit
        if not 1 <= pids_limit <= 256:
            raise ProbeExecutionError("Docker pids_limit must be between 1 and 256")
        if pull_policy not in {"never", "missing"}:
            raise ProbeExecutionError("Docker pull policy must be 'never' or 'missing'")
        if not 0 < image_setup_timeout_seconds <= 300:
            raise ProbeExecutionError("Docker image setup timeout must be in (0, 300] seconds")
        self.pids_limit = pids_limit
        self.pull_policy = pull_policy
        self.image_setup_timeout_seconds = image_setup_timeout_seconds
        self._execution_origin = (
            ProbeExecutionOrigin.LIVE_DOCKER.value
            if _construction_key is _LIVE_RUNNER_CONSTRUCTION_KEY
            else ProbeExecutionOrigin.SYNTHETIC_TEST.value
        )

    @property
    def execution_origin(self) -> str:
        return self._execution_origin

    def inspect_image_identity(self, image_reference: str) -> RuntimeImageIdentity:
        """Resolve immutable digest and platform from the local Docker store."""
        try:
            inspect_proc = subprocess.run(
                ["docker", "image", "inspect", image_reference],
                capture_output=True,
                text=True,
                timeout=min(15.0, self.image_setup_timeout_seconds),
                check=False,
            )
            if inspect_proc.returncode != 0:
                return RuntimeImageIdentity(
                    False, None, None, None, inspect_proc.stderr.strip()
                )

            payload = json.loads(inspect_proc.stdout)
            if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], Mapping):
                raise ValueError("docker image inspect returned an unexpected document")
            image = payload[0]
            repo_digests = image.get("RepoDigests")
            if not isinstance(repo_digests, list):
                repo_digests = []
            runtime_image_id: str | None = None
            resolved_digest: str | None = None
            fallback: tuple[str, str] | None = None
            for item in repo_digests:
                if isinstance(item, str):
                    match = _RUNTIME_DIGEST.search(item)
                    if match:
                        candidate = match.group(0)
                        fallback = fallback or (item, candidate)
                        expected = _RUNTIME_DIGEST.search(image_reference)
                        if expected and candidate == expected.group(0):
                            runtime_image_id = item
                            resolved_digest = candidate
                            break
            if resolved_digest is None and fallback is not None:
                runtime_image_id, resolved_digest = fallback
            os_name = image.get("Os")
            architecture = image.get("Architecture")
            variant = image.get("Variant")
            platform_name = None
            if isinstance(os_name, str) and os_name and isinstance(architecture, str) and architecture:
                platform_name = f"{os_name}/{architecture}"
                if isinstance(variant, str) and variant:
                    platform_name += f"/{variant}"
            if resolved_digest is None or platform_name is None:
                return RuntimeImageIdentity(
                    True,
                    resolved_digest,
                    platform_name,
                    runtime_image_id,
                    "Docker image identity lacks RepoDigest or platform",
                )
            return RuntimeImageIdentity(
                True, resolved_digest, platform_name, runtime_image_id, None
            )
        except Exception as exc:
            return RuntimeImageIdentity(False, None, None, None, str(exc))

    def _ensure_image_identity(
        self, image_reference: str, expected_digest: str
    ) -> RuntimeImageIdentity:
        identity = self.inspect_image_identity(image_reference)
        if not identity.present and self.pull_policy == "missing":
            try:
                pull = subprocess.run(
                    ["docker", "pull", image_reference],
                    capture_output=True,
                    text=True,
                    timeout=self.image_setup_timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                return RuntimeImageIdentity(
                    False, None, None, None, f"approved image pull timed out: {exc}"
                )
            if pull.returncode != 0:
                return RuntimeImageIdentity(
                    False, None, None, None, pull.stderr.strip() or "approved image pull failed"
                )
            identity = self.inspect_image_identity(image_reference)
        if identity.present and identity.digest != expected_digest:
            raise SecurityVerificationError(
                f"Runtime image digest {identity.digest!r} does not match expected pinned digest {expected_digest!r}"
            )
        return identity

    @staticmethod
    def _container_started(container_name: str) -> bool:
        try:
            result = subprocess.run(
                [
                    "docker",
                    "container",
                    "inspect",
                    container_name,
                    "--format",
                    "{{json .State.StartedAt}}",
                ],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
        except Exception:
            return False
        started_at = result.stdout.strip().strip('"')
        return result.returncode == 0 and bool(started_at) and not started_at.startswith("0001-")

    @staticmethod
    def _cleanup_container(container_name: str) -> bool:
        """Stop then force-remove the exact durable container identity."""
        try:
            subprocess.run(
                ["docker", "stop", "--time=1", container_name],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
        except BaseException:
            # Removal is still attempted; KeyboardInterrupt is re-raised by the
            # caller only after this finally path completes.
            pass
        try:
            removed = subprocess.run(
                ["docker", "rm", "--force", container_name],
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
        except BaseException:
            return False
        message = (removed.stderr or "").lower()
        return removed.returncode == 0 or "no such container" in message

    def run_probe(
        self,
        image_spec: ImageProbeSpec,
        probe: ProbeSpec,
    ) -> ImageProbeResult:
        digest = validate_approved_image_spec(image_spec, self.catalog)
        validate_approved_probe_spec(image_spec, probe)
        identity = self._ensure_image_identity(image_spec.image_reference, digest)
        if not identity.present or identity.digest is None or identity.platform is None:
            return ImageProbeResult(
                schema_version=IMAGE_PROBE_RECORD_SCHEMA_VERSION,
                probe_id=probe.probe_id,
                image_id=image_spec.image_id,
                image_reference=image_spec.image_reference,
                image_digest=digest,
                capability=probe.capability,
                success=False,
                functional_status=CapabilityProbeStatus.UNAVAILABLE.value,
                execution_status=ProbeExecutionStatus.IMAGE_NOT_PRESENT.value,
                execution_origin=self.execution_origin,
                resolved_image_digest=identity.digest,
                resolved_image_platform=identity.platform,
                runtime_image_id=identity.runtime_image_id,
                cleanup_succeeded=None,
                import_version_metadata={},
                runtime_seconds=0.0,
                error_category="IMAGE_NOT_PRESENT",
                error_message=(
                    f"Approved image {image_spec.image_reference} is unavailable or lacks "
                    f"verifiable runtime identity: {identity.error}"
                ),
                stdout=None,
                execution_mode="docker",
                timestamp_utc=_utc_now(),
            )

        cpu_val = str(float(probe.cpu_limit[:-1]) / 1000.0)
        mem_val = probe.memory_limit.lower().replace("i", "")
        container_name = (
            f"intent-spawner-e5-{image_spec.image_id[:18]}-{uuid.uuid4().hex[:12]}"
        )

        cmd = [
            "docker",
            "run",
            "--name",
            container_name,
            f"--pull={self.pull_policy}",
            "--network=none",
            "--stop-timeout=1",
            f"--cpus={cpu_val}",
            f"--memory={mem_val}",
            f"--memory-swap={mem_val}",
            f"--pids-limit={self.pids_limit}",
            "--read-only",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=64m",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            image_spec.image_reference,
            "python3",
            "-c",
            _bounded_python_script(probe.script, probe.timeout_seconds),
        ]

        started = time.perf_counter()
        timed_out = False
        returncode: int | None = None
        stdout = ""
        stderr = ""
        container_started = False
        cleanup_succeeded = False

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=probe.timeout_seconds + 3.0,
                check=False,
            )
            returncode = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
            container_started = returncode != 125
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = -1
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            container_started = self._container_started(container_name)
        except Exception as exc:
            returncode = -1
            stderr = str(exc)
            container_started = self._container_started(container_name)
        finally:
            cleanup_succeeded = self._cleanup_container(container_name)

        elapsed = time.perf_counter() - started
        execution_status = (
            ProbeExecutionStatus.EXECUTED.value
            if container_started
            else ProbeExecutionStatus.CONTAINER_UNAVAILABLE.value
        )
        if execution_status == ProbeExecutionStatus.EXECUTED.value:
            functional_status, success, metadata, error_category, error_message = (
                _classify_probe_output(
                    probe,
                    returncode=returncode,
                    stdout=stdout,
                    stderr=stderr,
                    timed_out=timed_out,
                )
            )
        else:
            functional_status = CapabilityProbeStatus.UNAVAILABLE.value
            success = False
            metadata = _extract_probe_metadata(stdout)
            error_category = "CONTAINER_LAUNCH_FAILED"
            error_message = stderr.strip() or "Docker container did not start"
            elapsed = 0.0
        if not cleanup_succeeded:
            functional_status = CapabilityProbeStatus.FAILURE.value
            success = False
            error_category = "CLEANUP_FAILED"
            error_message = f"Failed to remove Docker container {container_name}"

        return ImageProbeResult(
            schema_version=IMAGE_PROBE_RECORD_SCHEMA_VERSION,
            probe_id=probe.probe_id,
            image_id=image_spec.image_id,
            image_reference=image_spec.image_reference,
            image_digest=digest,
            capability=probe.capability,
            success=success,
            functional_status=functional_status,
            execution_status=execution_status,
            execution_origin=self.execution_origin,
            execution_identity=container_name,
            resolved_image_digest=identity.digest,
            resolved_image_platform=identity.platform,
            runtime_image_id=identity.runtime_image_id,
            cleanup_succeeded=cleanup_succeeded,
            import_version_metadata=metadata,
            runtime_seconds=elapsed,
            error_category=error_category,
            error_message=error_message,
            stdout=stdout,
            execution_mode="docker",
            timestamp_utc=_utc_now(),
        )


class KubernetesProbeRunner(BaseProbeRunner):
    """Live probe runner executing bounded probes as ephemeral pods via kubectl."""

    def __init__(
        self,
        catalog: Mapping[str, Any],
        *,
        namespace: str = "default",
        context: str | None = None,
        poll_interval_seconds: float = 0.25,
        _construction_key: object | None = None,
    ) -> None:
        super().__init__(catalog)
        if not 0 <= poll_interval_seconds <= 5:
            raise ProbeExecutionError("Kubernetes poll interval must be between 0 and 5 seconds")
        self.namespace = namespace
        self.context = context
        self.poll_interval_seconds = poll_interval_seconds
        self._execution_origin = (
            ProbeExecutionOrigin.LIVE_KUBERNETES.value
            if _construction_key is _LIVE_RUNNER_CONSTRUCTION_KEY
            else ProbeExecutionOrigin.SYNTHETIC_TEST.value
        )

    @property
    def execution_origin(self) -> str:
        return self._execution_origin

    def _kubectl(self, args: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        base_cmd = ["kubectl"]
        if self.context:
            base_cmd.extend(["--context", self.context])
        base_cmd.extend(["-n", self.namespace])
        base_cmd.extend(args)
        return subprocess.run(
            base_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def _node_platform(self, node_name: str, timeout: float) -> str | None:
        result = self._kubectl(
            ["get", "node", node_name, "-o", "json"], timeout=timeout
        )
        if result.returncode != 0:
            return None
        try:
            node = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        labels = node.get("metadata", {}).get("labels", {})
        os_name = labels.get("kubernetes.io/os")
        architecture = labels.get("kubernetes.io/arch")
        if not os_name or not architecture:
            node_info = node.get("status", {}).get("nodeInfo", {})
            os_name = os_name or node_info.get("operatingSystem")
            architecture = architecture or node_info.get("architecture")
        if isinstance(os_name, str) and os_name and isinstance(architecture, str) and architecture:
            return f"{os_name}/{architecture}"
        return None

    def _cleanup_pod(self, pod_name: str) -> bool:
        try:
            deleted = self._kubectl(
                [
                    "delete",
                    f"pod/{pod_name}",
                    "--ignore-not-found=true",
                    "--grace-period=0",
                    "--force",
                    "--wait=true",
                    "--timeout=10s",
                ],
                timeout=15.0,
            )
        except BaseException:
            return False
        return deleted.returncode == 0

    @staticmethod
    def _termination_details(pod: Mapping[str, Any]) -> tuple[bool, int | None, str, str | None]:
        statuses = pod.get("status", {}).get("containerStatuses", [])
        if not isinstance(statuses, list) or not statuses:
            return False, None, "", None
        status = statuses[0] if isinstance(statuses[0], Mapping) else {}
        state = status.get("state", {}) if isinstance(status, Mapping) else {}
        terminated = state.get("terminated") if isinstance(state, Mapping) else None
        running = state.get("running") if isinstance(state, Mapping) else None
        started = isinstance(running, Mapping) or isinstance(terminated, Mapping)
        if not isinstance(terminated, Mapping):
            return started, None, "", status.get("imageID") if isinstance(status, Mapping) else None
        exit_code = terminated.get("exitCode")
        reason = str(terminated.get("reason") or "")
        message = str(terminated.get("message") or "")
        detail = ": ".join(item for item in (reason, message) if item)
        return started, int(exit_code) if isinstance(exit_code, int) else None, detail, status.get("imageID")

    @staticmethod
    def _pending_reason(pod: Mapping[str, Any]) -> str:
        statuses = pod.get("status", {}).get("containerStatuses", [])
        if isinstance(statuses, list) and statuses and isinstance(statuses[0], Mapping):
            waiting = statuses[0].get("state", {}).get("waiting", {})
            if isinstance(waiting, Mapping):
                detail = ": ".join(
                    str(waiting.get(key))
                    for key in ("reason", "message")
                    if waiting.get(key)
                )
                if detail:
                    return detail
        conditions = pod.get("status", {}).get("conditions", [])
        if isinstance(conditions, list):
            for condition in conditions:
                if isinstance(condition, Mapping) and condition.get("status") == "False":
                    detail = ": ".join(
                        str(condition.get(key))
                        for key in ("reason", "message")
                        if condition.get(key)
                    )
                    if detail:
                        return detail
        return str(pod.get("status", {}).get("phase") or "pod did not reach a terminal phase")

    def run_probe(
        self,
        image_spec: ImageProbeSpec,
        probe: ProbeSpec,
    ) -> ImageProbeResult:
        digest = validate_approved_image_spec(image_spec, self.catalog)
        validate_approved_probe_spec(image_spec, probe)
        pod_name = (
            f"intent-spawner-e5-{image_spec.image_id[:16]}-{uuid.uuid4().hex[:10]}"
        )

        started = time.perf_counter()
        deadline = time.monotonic() + probe.timeout_seconds
        timed_out = False
        returncode: int | None = None
        stdout = ""
        stderr = ""
        container_started = False
        runtime_image_id: str | None = None
        resolved_digest: str | None = None
        resolved_platform: str | None = None
        cleanup_succeeded = False
        terminal_phase: str | None = None
        last_pod: Mapping[str, Any] = {}

        try:
            overrides = {
                "spec": {
                    "restartPolicy": "Never",
                    "activeDeadlineSeconds": max(1, int(math.ceil(probe.timeout_seconds))),
                    "terminationGracePeriodSeconds": 1,
                    "automountServiceAccountToken": False,
                    "containers": [
                        {
                            "name": "probe",
                            "image": image_spec.image_reference,
                            "imagePullPolicy": "IfNotPresent",
                            "command": [
                                "python3",
                                "-c",
                                _bounded_python_script(
                                    probe.script, probe.timeout_seconds
                                ),
                            ],
                            "resources": {
                                "requests": {
                                    "cpu": probe.cpu_limit,
                                    "memory": probe.memory_limit,
                                },
                                "limits": {
                                    "cpu": probe.cpu_limit,
                                    "memory": probe.memory_limit,
                                }
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                                "readOnlyRootFilesystem": True,
                                "seccompProfile": {"type": "RuntimeDefault"},
                            },
                            "volumeMounts": [
                                {"name": "probe-tmp", "mountPath": "/tmp"}
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "probe-tmp",
                            "emptyDir": {"sizeLimit": "64Mi"},
                        }
                    ],
                }
            }
            run_cmd = [
                "run",
                pod_name,
                f"--image={image_spec.image_reference}",
                "--restart=Never",
                f"--overrides={json.dumps(overrides)}",
            ]
            launch = self._kubectl(run_cmd, timeout=15.0)
            if launch.returncode != 0:
                returncode = launch.returncode
                stderr = launch.stderr
            else:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        stderr = self._pending_reason(last_pod)
                        break
                    status_res = self._kubectl(
                        ["get", f"pod/{pod_name}", "-o", "json"],
                        timeout=min(5.0, max(0.1, remaining)),
                    )
                    if status_res.returncode != 0:
                        stderr = status_res.stderr.strip() or "could not observe pod status"
                        break
                    try:
                        parsed = json.loads(status_res.stdout)
                    except json.JSONDecodeError:
                        stderr = "kubectl returned invalid pod status JSON"
                        break
                    if not isinstance(parsed, Mapping):
                        stderr = "kubectl returned a non-object pod status"
                        break
                    last_pod = parsed
                    terminal_phase = str(parsed.get("status", {}).get("phase") or "")
                    (
                        container_started,
                        terminated_exit,
                        termination_detail,
                        runtime_image_id,
                    ) = self._termination_details(parsed)
                    if terminal_phase in {"Succeeded", "Failed"}:
                        if terminated_exit is None:
                            stderr = termination_detail or "terminal pod lacks container termination status"
                            returncode = -1
                        else:
                            returncode = terminated_exit
                            stderr = termination_detail if terminated_exit != 0 else ""
                        break
                    if self.poll_interval_seconds:
                        time.sleep(min(self.poll_interval_seconds, max(0.0, remaining)))

                if last_pod:
                    (
                        container_started,
                        terminated_exit,
                        termination_detail,
                        runtime_image_id,
                    ) = self._termination_details(last_pod)
                    if returncode is None and terminated_exit is not None:
                        returncode = terminated_exit
                    if not stderr and termination_detail and returncode not in (None, 0):
                        stderr = termination_detail
                    match = _RUNTIME_DIGEST.search(runtime_image_id or "")
                    resolved_digest = match.group(0) if match else None
                    if resolved_digest and resolved_digest != digest:
                        raise SecurityVerificationError(
                            f"Kubernetes runtime image digest {resolved_digest!r} "
                            f"does not match expected pinned digest {digest!r}"
                        )
                    node_name = last_pod.get("spec", {}).get("nodeName")
                    if isinstance(node_name, str) and node_name:
                        resolved_platform = self._node_platform(node_name, timeout=5.0)
                if terminal_phase in {"Succeeded", "Failed"}:
                    log_res = self._kubectl(["logs", pod_name], timeout=10.0)
                    stdout = log_res.stdout
                    if log_res.returncode != 0 and not stderr:
                        stderr = log_res.stderr.strip()
        except SecurityVerificationError:
            raise
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = -1
            stderr = f"Kubernetes API call timed out: {exc}"
        except Exception as exc:
            returncode = -1
            stderr = str(exc)
        finally:
            cleanup_succeeded = self._cleanup_pod(pod_name)

        elapsed = time.perf_counter() - started
        execution_status = (
            ProbeExecutionStatus.EXECUTED.value
            if container_started
            else ProbeExecutionStatus.CONTAINER_UNAVAILABLE.value
        )
        if execution_status == ProbeExecutionStatus.EXECUTED.value:
            functional_status, success, metadata, error_category, error_message = (
                _classify_probe_output(
                    probe,
                    returncode=returncode,
                    stdout=stdout,
                    stderr=stderr,
                    timed_out=timed_out,
                )
            )
        else:
            functional_status = CapabilityProbeStatus.UNAVAILABLE.value
            success = False
            metadata = _extract_probe_metadata(stdout)
            error_category = "TIMEOUT" if timed_out else "CONTAINER_LAUNCH_FAILED"
            error_message = stderr.strip() or "Kubernetes probe container did not start"
            elapsed = 0.0
        if terminal_phase in {"Succeeded", "Failed"} and not runtime_image_id:
            functional_status = CapabilityProbeStatus.FAILURE.value
            success = False
            error_category = "RUNTIME_IDENTITY_UNAVAILABLE"
            error_message = "Terminal pod lacks a verifiable container image ID"
        if terminal_phase in {"Succeeded", "Failed"} and not resolved_platform:
            functional_status = CapabilityProbeStatus.FAILURE.value
            success = False
            error_category = "RUNTIME_IDENTITY_UNAVAILABLE"
            error_message = "Terminal pod node platform could not be resolved"
        if not cleanup_succeeded:
            functional_status = CapabilityProbeStatus.FAILURE.value
            success = False
            error_category = "CLEANUP_FAILED"
            error_message = f"Failed to delete Kubernetes pod {pod_name}"

        return ImageProbeResult(
            schema_version=IMAGE_PROBE_RECORD_SCHEMA_VERSION,
            probe_id=probe.probe_id,
            image_id=image_spec.image_id,
            image_reference=image_spec.image_reference,
            image_digest=digest,
            capability=probe.capability,
            success=success,
            functional_status=functional_status,
            execution_status=execution_status,
            execution_origin=self.execution_origin,
            execution_identity=pod_name,
            resolved_image_digest=resolved_digest,
            resolved_image_platform=resolved_platform,
            runtime_image_id=runtime_image_id,
            cleanup_succeeded=cleanup_succeeded,
            import_version_metadata=metadata,
            runtime_seconds=elapsed,
            error_category=error_category,
            error_message=error_message,
            stdout=stdout,
            execution_mode="kubernetes",
            timestamp_utc=_utc_now(),
        )


def detect_runtime() -> str:
    """Detect available container runtime or cluster.

    Returns 'docker', 'kubernetes', or 'dry_run'.
    """
    try:
        res = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if res.returncode == 0:
            return "docker"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        res = subprocess.run(
            ["kubectl", "cluster-info"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if res.returncode == 0:
            return "kubernetes"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return "dry_run"


def create_probe_runner(
    catalog: Mapping[str, Any],
    mode: str = "auto",
    *,
    dry_run_if_unavailable: bool = True,
    k8s_namespace: str = "default",
    k8s_context: str | None = None,
    pull_policy: str = "never",
) -> BaseProbeRunner:
    """Factory creating the appropriate probe runner based on request and availability."""
    selected_mode = mode.lower()
    if selected_mode == "auto":
        detected = detect_runtime()
        if detected == "docker":
            return DockerProbeRunner(
                catalog,
                pull_policy=pull_policy,
                _construction_key=_LIVE_RUNNER_CONSTRUCTION_KEY,
            )
        elif detected == "kubernetes":
            return KubernetesProbeRunner(
                catalog,
                namespace=k8s_namespace,
                context=k8s_context,
                _construction_key=_LIVE_RUNNER_CONSTRUCTION_KEY,
            )
        elif dry_run_if_unavailable:
            return DryRunProbeRunner(catalog)
        else:
            raise ProbeExecutionError("No container runtime or cluster detected and dry-run fallback disabled.")

    if selected_mode in ("docker", "local", "docker-local"):
        return DockerProbeRunner(
            catalog,
            pull_policy=pull_policy,
            _construction_key=_LIVE_RUNNER_CONSTRUCTION_KEY,
        )
    if selected_mode == "kubernetes":
        return KubernetesProbeRunner(
            catalog,
            namespace=k8s_namespace,
            context=k8s_context,
            _construction_key=_LIVE_RUNNER_CONSTRUCTION_KEY,
        )
    if selected_mode in ("dry-run", "dry_run"):
        return DryRunProbeRunner(catalog)
    if selected_mode == "synthetic":
        return SyntheticProbeRunner(catalog)

    raise ValueError(f"Unsupported runner mode: {mode!r}")
