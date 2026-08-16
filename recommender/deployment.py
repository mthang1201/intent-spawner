"""Deployment-time validation and observable runtime package metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
from urllib import parse as urllib_parse


PACKAGE_VERSION = "intent-spawner-recommender-v2"
PACKAGE_CHECKSUM_ENV_VAR = "RECOMMENDER_PACKAGE_CHECKSUM"
PACKAGE_VERSION_ENV_VAR = "RECOMMENDER_PACKAGE_VERSION"
SELF_HOSTED_ALLOW_INSECURE_HTTP_ENV_VAR = (
    "SELF_HOSTED_LLM_ALLOW_INSECURE_HTTP"
)
SELF_HOSTED_AUTH_REQUIRED_ENV_VAR = "SELF_HOSTED_LLM_AUTH_REQUIRED"

# This is the single allowlist used to build and verify the externally managed
# ConfigMap. Tests, caches, documentation, and dynamic-resource experiments are
# deliberately absent.
RUNTIME_FILES = (
    "__init__.py",
    "base.py",
    "deployment.py",
    "dynamic_resources.py",
    "external_llm.py",
    "image-catalog.yaml",
    "jupyterhub_integration.py",
    "models.py",
    "policy.py",
    "recommender.py",
    "registry.py",
    "reliability.py",
    "resource-policy.yaml",
    "rule_based.py",
    "self_hosted_llm.py",
    "token_pricing.py",
)
MAX_CONFIGMAP_PAYLOAD_BYTES = 700 * 1024
CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_BACKENDS = frozenset({"rule_based", "external_llm", "self_hosted_llm"})
BACKEND_VERSIONS = {
    "rule_based": "rule-based-v1",
    "external_llm": "external-llm-v2",
    "self_hosted_llm": "self-hosted-llm-v2",
}


@dataclass(frozen=True)
class DeploymentMetadata:
    """Non-secret deployment identity safe for logs and audit records."""

    backend: str
    backend_version: str
    package_version: str
    package_checksum: str

    def to_dict(self) -> dict[str, str]:
        return {
            "backend": self.backend,
            "backend_version": self.backend_version,
            "package_version": self.package_version,
            "package_checksum": self.package_checksum,
        }


def _parse_boolean(environ: Mapping[str, str], name: str, default: str = "false") -> bool:
    value = environ.get(name, default).strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be exactly true or false")
    return value == "true"


def package_payload_size(package_dir: str | Path) -> int:
    root = Path(package_dir)
    return sum((root / name).stat().st_size for name in RUNTIME_FILES)


def compute_package_checksum(package_dir: str | Path) -> str:
    """Hash exact file names, byte lengths, and contents in stable order."""

    root = Path(package_dir)
    digest = hashlib.sha256()
    for name in RUNTIME_FILES:
        content = (root / name).read_bytes()
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


def validate_runtime_package(
    package_dir: str | Path,
    *,
    expected_checksum: str,
    expected_version: str,
) -> None:
    """Fail startup when the mounted ConfigMap does not match the pod template."""

    root = Path(package_dir)
    missing = [name for name in RUNTIME_FILES if not (root / name).is_file()]
    if missing:
        raise RuntimeError(
            "recommender ConfigMap is missing required runtime files: "
            + ", ".join(missing)
        )
    if expected_version != PACKAGE_VERSION:
        raise RuntimeError(
            "recommender package version does not match the mounted runtime"
        )
    if not CHECKSUM_PATTERN.fullmatch(expected_checksum):
        raise RuntimeError("recommender package checksum must be a SHA-256 digest")
    payload_size = package_payload_size(root)
    if payload_size > MAX_CONFIGMAP_PAYLOAD_BYTES:
        raise RuntimeError(
            "recommender ConfigMap payload exceeds the deployment safety limit"
        )
    actual_checksum = compute_package_checksum(root)
    if actual_checksum != expected_checksum:
        raise RuntimeError(
            "recommender ConfigMap content does not match the Hub pod-template checksum"
        )


def _required_nonblank(environ: Mapping[str, str], name: str, backend: str) -> str:
    value = environ.get(name, "")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required for the {backend} backend")
    return value


def _validate_secure_endpoint(
    environ: Mapping[str, str],
    *,
    backend: str,
    endpoint_name: str,
    allow_insecure_name: str,
) -> None:
    endpoint = _required_nonblank(environ, endpoint_name, backend)
    parsed = urllib_parse.urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{endpoint_name} must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{endpoint_name} must not contain credentials")
    allow_insecure = _parse_boolean(environ, allow_insecure_name)
    if parsed.scheme != "https" and not allow_insecure:
        raise ValueError(
            f"{endpoint_name} must use HTTPS unless {allow_insecure_name}=true "
            "is explicitly set for an isolated development or trusted in-cluster path"
        )


def validate_deployment_environment(
    environ: Mapping[str, str] | None = None,
    *,
    package_dir: str | Path | None = None,
) -> DeploymentMetadata:
    """Validate deployment wiring before JupyterHub begins serving requests."""

    selected = os.environ if environ is None else environ
    backend = selected.get("RECOMMENDER_BACKEND", "rule_based").strip()
    if backend not in ALLOWED_BACKENDS:
        available = ", ".join(sorted(ALLOWED_BACKENDS))
        raise ValueError(
            f"unknown recommender backend {backend!r}; supported backends: {available}"
        )

    checksum = _required_nonblank(
        selected, PACKAGE_CHECKSUM_ENV_VAR, "recommender deployment"
    )
    version = _required_nonblank(
        selected, PACKAGE_VERSION_ENV_VAR, "recommender deployment"
    )
    validate_runtime_package(
        Path(__file__).parent if package_dir is None else package_dir,
        expected_checksum=checksum,
        expected_version=version,
    )

    if backend == "external_llm":
        _validate_secure_endpoint(
            selected,
            backend=backend,
            endpoint_name="EXTERNAL_LLM_ENDPOINT",
            allow_insecure_name="EXTERNAL_LLM_ALLOW_INSECURE_HTTP",
        )
        _required_nonblank(selected, "EXTERNAL_LLM_MODEL", backend)
        _required_nonblank(selected, "EXTERNAL_LLM_API_KEY", backend)
    elif backend == "self_hosted_llm":
        _validate_secure_endpoint(
            selected,
            backend=backend,
            endpoint_name="SELF_HOSTED_LLM_ENDPOINT",
            allow_insecure_name=SELF_HOSTED_ALLOW_INSECURE_HTTP_ENV_VAR,
        )
        _required_nonblank(selected, "SELF_HOSTED_LLM_MODEL", backend)
        if _parse_boolean(selected, SELF_HOSTED_AUTH_REQUIRED_ENV_VAR):
            _required_nonblank(selected, "SELF_HOSTED_LLM_API_KEY", backend)

    # Constructors validate numeric ranges (attempt timeout, total deadline,
    # retries, backoff, temperature, and concurrency) immediately after this
    # deployment validation. Keep one implementation of those constraints.
    return DeploymentMetadata(
        backend=backend,
        backend_version=BACKEND_VERSIONS[backend],
        package_version=version,
        package_checksum=checksum,
    )


__all__ = [
    "ALLOWED_BACKENDS",
    "BACKEND_VERSIONS",
    "DeploymentMetadata",
    "MAX_CONFIGMAP_PAYLOAD_BYTES",
    "PACKAGE_CHECKSUM_ENV_VAR",
    "PACKAGE_VERSION",
    "PACKAGE_VERSION_ENV_VAR",
    "RUNTIME_FILES",
    "SELF_HOSTED_ALLOW_INSECURE_HTTP_ENV_VAR",
    "SELF_HOSTED_AUTH_REQUIRED_ENV_VAR",
    "compute_package_checksum",
    "package_payload_size",
    "validate_deployment_environment",
    "validate_runtime_package",
]
