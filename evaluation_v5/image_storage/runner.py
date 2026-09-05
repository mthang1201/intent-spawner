"""Container and Kubernetes functional probe runner for approved images."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Mapping, Sequence

from .contracts import (
    IMAGE_PROBE_RECORD_SCHEMA_VERSION,
    ImageProbeManifest,
    ImageProbeResult,
    ImageProbeSpec,
    ProbeExecutionError,
    ProbeExecutionStatus,
    ProbeSpec,
    SecurityVerificationError,
    validate_approved_image_reference,
)


PROBE_META_PREFIX = "PROBE_META:"


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
        digest = validate_approved_image_reference(image_spec.image_reference, self.catalog)

        return ImageProbeResult(
            schema_version=IMAGE_PROBE_RECORD_SCHEMA_VERSION,
            probe_id=probe.probe_id,
            image_id=image_spec.image_id,
            image_reference=image_spec.image_reference,
            image_digest=digest,
            capability=probe.capability,
            success=False,
            execution_status=self.simulated_status,
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
        digest = validate_approved_image_reference(image_spec.image_reference, self.catalog)

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
                execution_status=ProbeExecutionStatus.IMAGE_NOT_PRESENT.value,
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
            metadata[f"{probe.capability}_version"] = "1.0.0-synthetic"

        return ImageProbeResult(
            schema_version=IMAGE_PROBE_RECORD_SCHEMA_VERSION,
            probe_id=probe.probe_id,
            image_id=image_spec.image_id,
            image_reference=image_spec.image_reference,
            image_digest=digest,
            capability=probe.capability,
            success=not is_fail,
            execution_status=ProbeExecutionStatus.EXECUTED.value,
            resolved_image_digest=digest,
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
    ) -> None:
        super().__init__(catalog)
        self.default_cpu_limit = default_cpu_limit
        self.default_memory_limit = default_memory_limit
        self.pids_limit = pids_limit
        self.pull_policy = pull_policy

    def inspect_image_identity(self, image_reference: str) -> tuple[bool, str | None, str | None]:
        """Inspect image in local Docker store to verify image presence and RepoDigests.

        Returns (is_present, resolved_digest, error_message).
        """
        try:
            inspect_proc = subprocess.run(
                ["docker", "image", "inspect", image_reference, "--format", "{{json .RepoDigests}}"],
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
            if inspect_proc.returncode != 0:
                return False, None, inspect_proc.stderr.strip()

            raw_digests = json.loads(inspect_proc.stdout.strip() or "[]")
            for rd in raw_digests:
                if "@" in rd:
                    return True, rd.split("@", 1)[1], None
            return True, None, None
        except Exception as exc:
            return False, None, str(exc)

    def run_probe(
        self,
        image_spec: ImageProbeSpec,
        probe: ProbeSpec,
    ) -> ImageProbeResult:
        digest = validate_approved_image_reference(image_spec.image_reference, self.catalog)

        # Pre-check image presence if pull_policy is never
        if self.pull_policy == "never":
            present, resolved, err = self.inspect_image_identity(image_spec.image_reference)
            if not present:
                return ImageProbeResult(
                    schema_version=IMAGE_PROBE_RECORD_SCHEMA_VERSION,
                    probe_id=probe.probe_id,
                    image_id=image_spec.image_id,
                    image_reference=image_spec.image_reference,
                    image_digest=digest,
                    capability=probe.capability,
                    success=False,
                    execution_status=ProbeExecutionStatus.IMAGE_NOT_PRESENT.value,
                    resolved_image_digest=None,
                    import_version_metadata={},
                    runtime_seconds=0.0,
                    error_category="IMAGE_NOT_PRESENT",
                    error_message=f"Image {image_spec.image_reference} is not present in local Docker store: {err}",
                    stdout=None,
                    execution_mode="docker",
                    timestamp_utc=_utc_now(),
                )
            if resolved and resolved != digest:
                raise SecurityVerificationError(
                    f"Runtime image digest {resolved!r} does not match expected pinned digest {digest!r}"
                )

        cpu_val = "1.0"
        if probe.cpu_limit.endswith("m"):
            try:
                cpu_val = str(float(probe.cpu_limit[:-1]) / 1000.0)
            except ValueError:
                cpu_val = self.default_cpu_limit
        mem_val = probe.memory_limit.lower().replace("i", "")

        cmd = [
            "docker",
            "run",
            "--rm",
            f"--pull={self.pull_policy}",
            "--network=none",
            f"--cpus={cpu_val}",
            f"--memory={mem_val}",
            f"--pids-limit={self.pids_limit}",
            image_spec.image_reference,
            "python3",
            "-c",
            probe.script,
        ]

        started = time.perf_counter()
        timed_out = False
        returncode: int | None = None
        stdout = ""
        stderr = ""

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=probe.timeout_seconds,
                check=False,
            )
            returncode = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = -1
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        except Exception as exc:
            returncode = -1
            stderr = str(exc)

        elapsed = time.perf_counter() - started
        success = (returncode == 0) and not timed_out
        metadata: dict[str, str] = {}
        error_category: str | None = None
        error_message: str | None = None
        execution_status = ProbeExecutionStatus.EXECUTED.value

        if success:
            metadata = _extract_probe_metadata(stdout)
        else:
            error_category = _categorize_error(returncode, stderr, timed_out)
            error_message = stderr.strip() or ("Timed out" if timed_out else "Execution failed")
            if error_category == "IMAGE_NOT_PRESENT":
                execution_status = ProbeExecutionStatus.IMAGE_NOT_PRESENT.value
                elapsed = 0.0
            elif error_category == "CONTAINER_LAUNCH_FAILED":
                execution_status = ProbeExecutionStatus.CONTAINER_UNAVAILABLE.value
                elapsed = 0.0

        return ImageProbeResult(
            schema_version=IMAGE_PROBE_RECORD_SCHEMA_VERSION,
            probe_id=probe.probe_id,
            image_id=image_spec.image_id,
            image_reference=image_spec.image_reference,
            image_digest=digest,
            capability=probe.capability,
            success=success,
            execution_status=execution_status,
            resolved_image_digest=digest if execution_status == ProbeExecutionStatus.EXECUTED.value else None,
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
    ) -> None:
        super().__init__(catalog)
        self.namespace = namespace
        self.context = context

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

    def run_probe(
        self,
        image_spec: ImageProbeSpec,
        probe: ProbeSpec,
    ) -> ImageProbeResult:
        digest = validate_approved_image_reference(image_spec.image_reference, self.catalog)
        pod_name = f"probe-{image_spec.image_id[:12]}-{probe.capability[:8]}-{int(time.time())}"

        started = time.perf_counter()
        timed_out = False
        returncode: int | None = None
        stdout = ""
        stderr = ""
        launch_failed = False

        try:
            overrides = {
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "probe",
                            "image": image_spec.image_reference,
                            "command": ["python3", "-c", probe.script],
                            "resources": {
                                "limits": {
                                    "cpu": probe.cpu_limit,
                                    "memory": probe.memory_limit,
                                }
                            },
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
                launch_failed = True
                returncode = launch.returncode
                stderr = launch.stderr
            else:
                wait_cmd = ["wait", f"pod/{pod_name}", "--for=condition=Ready=false", f"--timeout={int(probe.timeout_seconds)}s"]
                self._kubectl(wait_cmd, timeout=probe.timeout_seconds + 5.0)

                log_res = self._kubectl(["logs", pod_name], timeout=10.0)
                stdout = log_res.stdout
                stderr = log_res.stderr

                status_res = self._kubectl(["get", f"pod/{pod_name}", "-o", "jsonpath={.status.phase}"], timeout=5.0)
                phase = status_res.stdout.strip()
                returncode = 0 if phase == "Succeeded" else 1
        except subprocess.TimeoutExpired:
            timed_out = True
            returncode = -1
            stderr = "Pod execution timed out"
        except Exception as exc:
            returncode = -1
            stderr = str(exc)
        finally:
            try:
                self._kubectl(["delete", f"pod/{pod_name}", "--ignore-not-found", "--now"], timeout=10.0)
            except Exception:
                pass

        elapsed = time.perf_counter() - started
        success = (returncode == 0) and not timed_out
        metadata: dict[str, str] = {}
        error_category: str | None = None
        error_message: str | None = None
        execution_status = ProbeExecutionStatus.EXECUTED.value

        if success:
            metadata = _extract_probe_metadata(stdout)
        else:
            error_category = _categorize_error(returncode, stderr, timed_out)
            error_message = stderr.strip() or ("Timed out" if timed_out else "Kubernetes probe failed")
            if launch_failed:
                execution_status = ProbeExecutionStatus.CONTAINER_UNAVAILABLE.value
                elapsed = 0.0

        return ImageProbeResult(
            schema_version=IMAGE_PROBE_RECORD_SCHEMA_VERSION,
            probe_id=probe.probe_id,
            image_id=image_spec.image_id,
            image_reference=image_spec.image_reference,
            image_digest=digest,
            capability=probe.capability,
            success=success,
            execution_status=execution_status,
            resolved_image_digest=digest if execution_status == ProbeExecutionStatus.EXECUTED.value else None,
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
            return DockerProbeRunner(catalog, pull_policy=pull_policy)
        elif detected == "kubernetes":
            return KubernetesProbeRunner(catalog, namespace=k8s_namespace, context=k8s_context)
        elif dry_run_if_unavailable:
            return DryRunProbeRunner(catalog)
        else:
            raise ProbeExecutionError("No container runtime or cluster detected and dry-run fallback disabled.")

    if selected_mode == "docker":
        return DockerProbeRunner(catalog, pull_policy=pull_policy)
    if selected_mode == "kubernetes":
        return KubernetesProbeRunner(catalog, namespace=k8s_namespace, context=k8s_context)
    if selected_mode in ("dry-run", "dry_run"):
        return DryRunProbeRunner(catalog)
    if selected_mode == "synthetic":
        return SyntheticProbeRunner(catalog)

    raise ValueError(f"Unsupported runner mode: {mode!r}")
