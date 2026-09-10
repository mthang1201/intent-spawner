"""Authenticated Kubernetes collector model and runtime identity verification for E4.

This module provides the authoritative boundary between REAL_KUBERNETES_COLLECTOR
execution and synthetic/test/fake/dry-run adapters. Caller-supplied strings are
explicitly rejected as proof of authenticity.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping, Sequence

COLLECTOR_ORIGIN_REAL_KUBERNETES = "REAL_KUBERNETES_COLLECTOR"
COLLECTOR_ORIGIN_SYNTHETIC = "SYNTHETIC"
COLLECTOR_ORIGIN_TEST = "TEST"
COLLECTOR_ORIGIN_FAKE = "FAKE"
COLLECTOR_ORIGIN_DRY_RUN = "DRY_RUN"

VALID_COLLECTOR_ORIGINS = {
    COLLECTOR_ORIGIN_REAL_KUBERNETES,
    COLLECTOR_ORIGIN_SYNTHETIC,
    COLLECTOR_ORIGIN_TEST,
    COLLECTOR_ORIGIN_FAKE,
    COLLECTOR_ORIGIN_DRY_RUN,
}

AUTHENTICATED_COLLECTOR_MODULES = {
    "cluster_evaluation.resource_adapter_v5",
    "cluster_evaluation.resource_efficiency_adapter_v5",
}

AUTHENTICATED_COLLECTOR_CLASSES = {
    "KubernetesTrialAdapter",
    "KubernetesResourceEfficiencyAdapter",
}

AUTHENTICATED_COLLECTOR_VERSIONS = {
    "protocol-v5-kubernetes-trial-adapter-v1.2.0",
    "protocol-v5-resource-efficiency-kubernetes-adapter-v1.0.0",
}

FORBIDDEN_SYNTHETIC_TOKENS = (
    "synthetic",
    "fixture",
    "mock",
    "fake",
    "dry_run",
    "no-cluster-measurement",
    "unknown",
)

UID_PATTERN = re.compile(r"^[0-9a-zA-Z-._]{8,64}$")
TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


@dataclass(frozen=True, slots=True)
class AdapterAuthenticity:
    """Authenticated assessment of an execution adapter."""

    is_authenticated_real_collector: bool
    collector_origin: str
    derived_execution_status: str
    derived_cluster_measurement_status: str
    claims_permitted: bool


def authenticate_adapter(adapter: Any) -> AdapterAuthenticity:
    """Authenticate an adapter instance.

    Caller-provided strings or duck-typed attributes are NOT sufficient to
    establish authenticity. Only genuinely imported and validated adapter
    classes running real cluster collection obtain REAL_KUBERNETES_COLLECTOR.
    """
    adapter_cls = type(adapter)
    module_name = getattr(adapter_cls, "__module__", "")
    class_name = getattr(adapter_cls, "__qualname__", "")
    version = getattr(adapter, "adapter_version", "")
    is_marker_set = getattr(
        adapter, "_is_authenticated_real_kubernetes_collector", False
    )

    is_genuine = (
        is_marker_set is True
        and module_name in AUTHENTICATED_COLLECTOR_MODULES
        and class_name in AUTHENTICATED_COLLECTOR_CLASSES
        and version in AUTHENTICATED_COLLECTOR_VERSIONS
    )

    if is_genuine:
        return AdapterAuthenticity(
            is_authenticated_real_collector=True,
            collector_origin=COLLECTOR_ORIGIN_REAL_KUBERNETES,
            derived_execution_status="OBSERVED",
            derived_cluster_measurement_status="OBSERVED",
            claims_permitted=False,
        )

    # Fake, synthetic, dry-run, or caller-injected adapter
    version_lower = str(version).lower()
    if "dry" in version_lower:
        derived_status = "DRY_RUN"
        origin = COLLECTOR_ORIGIN_DRY_RUN
    else:
        derived_status = "SYNTHETIC"
        origin = COLLECTOR_ORIGIN_SYNTHETIC

    return AdapterAuthenticity(
        is_authenticated_real_collector=False,
        collector_origin=origin,
        derived_execution_status=derived_status,
        derived_cluster_measurement_status="NOT_EXECUTED",
        claims_permitted=False,
    )


def _scan_for_synthetic_tokens(value: Any, path: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, str):
        lowered = value.casefold()
        for token in ("synthetic", "fixture", "mock"):
            if token in lowered:
                findings.append(f"{path}: contains forbidden token '{token}'")
    elif isinstance(value, Mapping):
        for k, v in value.items():
            findings.extend(_scan_for_synthetic_tokens(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            findings.extend(_scan_for_synthetic_tokens(item, f"{path}[{i}]"))
    return findings


def validate_resource_authenticity(
    manifest: Mapping[str, Any],
    environment: Mapping[str, Any],
    observations_or_trials: Sequence[Any],
    *,
    is_efficiency: bool = False,
) -> None:
    """Validate collector authenticity and runtime identities.

    Fails closed if OBSERVED is claimed without authenticated collector origin,
    required runtime identity, or if synthetic markers are present.
    """
    execution_status = manifest.get("execution_status")

    if execution_status == "OBSERVED":
        collector_origin = manifest.get("collector_origin")
        if collector_origin != COLLECTOR_ORIGIN_REAL_KUBERNETES:
            raise ValueError(
                f"OBSERVED resource evidence requires authenticated "
                f"'{COLLECTOR_ORIGIN_REAL_KUBERNETES}' origin, got {collector_origin!r}"
            )

        env_origin = environment.get("collector_origin")
        if env_origin != COLLECTOR_ORIGIN_REAL_KUBERNETES:
            raise ValueError(
                f"environment provenance lacks authenticated "
                f"'{COLLECTOR_ORIGIN_REAL_KUBERNETES}' origin, got {env_origin!r}"
            )

        if environment.get("cluster_measurement_status") != "OBSERVED":
            raise ValueError(
                "environment cluster_measurement_status must be 'OBSERVED' for observed packages"
            )

        # Environment must not contain synthetic markers
        env_findings = _scan_for_synthetic_tokens(environment, path="environment")
        if env_findings:
            raise ValueError(
                f"synthetic environment marker present in claimed OBSERVED package: "
                + "; ".join(env_findings[:3])
            )

        # Required runtime identity in environment
        k8s_version = environment.get("kubernetes_version")
        if not isinstance(k8s_version, Mapping) or not any(
            k8s_version.get(k) for k in ("gitVersion", "major", "serverVersion")
        ):
            if not isinstance(k8s_version, Mapping) or not k8s_version:
                raise ValueError("OBSERVED environment lacks required kubernetes_version identity")

        context = environment.get("required_context") or environment.get("read_only_preflight", {}).get("facts", {}).get("current_context")
        if context != "intent-spawner-eval-v5":
            raise ValueError("OBSERVED environment lacks expected Kubernetes context identity")

        namespace = environment.get("namespace") or environment.get("read_only_preflight", {}).get("facts", {}).get("namespace_name")
        if namespace != "z2jh-context-demo":
            raise ValueError("OBSERVED environment lacks expected Kubernetes namespace identity")

        node_name = (
            environment.get("node_name")
            or environment.get("read_only_preflight", {}).get("facts", {}).get("node_name")
        )
        node_uid = (
            environment.get("node_uid")
            or environment.get("read_only_preflight", {}).get("facts", {}).get("node_uid")
        )
        if not node_name or not isinstance(node_name, str):
            raise ValueError("OBSERVED environment lacks node_name runtime identity")
        if not node_uid or not isinstance(node_uid, str) or not UID_PATTERN.fullmatch(node_uid):
            raise ValueError("OBSERVED environment lacks valid node_uid runtime identity")

        node_info = environment.get("read_only_preflight", {}).get("facts", {}).get("node_info") or {}
        for req_field in ("kubelet_version", "container_runtime", "kernel_version", "operating_system", "architecture"):
            val = environment.get(req_field) or node_info.get(req_field) or node_info.get(
                {"kubelet_version": "kubeletVersion", "container_runtime": "containerRuntimeVersion", "kernel_version": "kernelVersion", "operating_system": "operatingSystem", "architecture": "architecture"}[req_field]
            )
            if not val or not isinstance(val, str):
                raise ValueError(f"OBSERVED environment lacks required node identity field: {req_field}")

        if not observations_or_trials:
            raise ValueError("OBSERVED resource package must contain observation records")

        # Required runtime identities in observations / trials
        for row in observations_or_trials:
            row_dict = row.to_dict() if hasattr(row, "to_dict") else dict(row)

            # Check trial collector origin
            trial_origin = row_dict.get("collector_origin")
            if trial_origin is not None and trial_origin != COLLECTOR_ORIGIN_REAL_KUBERNETES:
                raise ValueError(
                    f"trial {row_dict.get('run_id') or row_dict.get('trial_id')} "
                    f"has non-real collector origin: {trial_origin!r}"
                )

            # Check for synthetic markers in trial
            trial_findings = _scan_for_synthetic_tokens(
                {
                    "correctness_details": row_dict.get("correctness_details"),
                    "exclusion_reason": row_dict.get("exclusion_reason"),
                    "exit_reason": row_dict.get("exit_reason"),
                },
                path=str(row_dict.get("run_id") or row_dict.get("trial_id")),
            )
            if trial_findings:
                raise ValueError(
                    f"synthetic trial marker present in claimed OBSERVED package: "
                    + "; ".join(trial_findings[:3])
                )

            # Kubernetes runtime identity
            k8s_data = row_dict.get("kubernetes")
            if not isinstance(k8s_data, Mapping) or not k8s_data:
                raise ValueError(
                    f"trial {row_dict.get('run_id') or row_dict.get('trial_id')} "
                    "lacks kubernetes runtime identity object"
                )

            pod_name = k8s_data.get("pod_name")
            if not pod_name or not isinstance(pod_name, str) or not (pod_name.startswith("e4-") or pod_name.startswith("e4e-")):
                raise ValueError(
                    f"trial {row_dict.get('run_id') or row_dict.get('trial_id')} "
                    f"has invalid pod_name: {pod_name!r}"
                )

            pod_uid = k8s_data.get("pod_uid")
            if not pod_uid or not isinstance(pod_uid, str) or not UID_PATTERN.fullmatch(pod_uid):
                raise ValueError(
                    f"trial {row_dict.get('run_id') or row_dict.get('trial_id')} "
                    f"lacks valid pod_uid runtime identity: {pod_uid!r}"
                )

            row_node = k8s_data.get("node_name")
            if row_node and row_node != node_name:
                raise ValueError(
                    f"trial node_name {row_node!r} does not match environment node_name {node_name!r}"
                )

            started = k8s_data.get("started_at")
            finished = k8s_data.get("finished_at")
            if started and not TIMESTAMP_PATTERN.fullmatch(started):
                raise ValueError(f"invalid started_at timestamp: {started!r}")
            if finished and not TIMESTAMP_PATTERN.fullmatch(finished):
                raise ValueError(f"invalid finished_at timestamp: {finished!r}")

            # cgroup v2 runtime markers
            cgroup = row_dict.get("cgroup_metrics") or {}
            if not isinstance(cgroup, Mapping):
                raise ValueError("trial cgroup_metrics must be a mapping")
            if row_dict.get("cgroup_version") not in (None, "v2") or cgroup.get("cgroup_version") not in (None, "v2"):
                raise ValueError("cgroup_version must be v2 for real Kubernetes execution")

    elif execution_status in (COLLECTOR_ORIGIN_SYNTHETIC, "TEST_ONLY"):
        if manifest.get("measurement_claims_permitted") is True:
            raise ValueError(f"{execution_status} evidence cannot permit measurement claims")
        if manifest.get("manual_review_status") == "APPROVED":
            raise ValueError(f"{execution_status} evidence cannot have an APPROVED manual review")


__all__ = [
    "AdapterAuthenticity",
    "AUTHENTICATED_COLLECTOR_CLASSES",
    "AUTHENTICATED_COLLECTOR_MODULES",
    "AUTHENTICATED_COLLECTOR_VERSIONS",
    "COLLECTOR_ORIGIN_DRY_RUN",
    "COLLECTOR_ORIGIN_FAKE",
    "COLLECTOR_ORIGIN_REAL_KUBERNETES",
    "COLLECTOR_ORIGIN_SYNTHETIC",
    "COLLECTOR_ORIGIN_TEST",
    "VALID_COLLECTOR_ORIGINS",
    "authenticate_adapter",
    "validate_resource_authenticity",
]
