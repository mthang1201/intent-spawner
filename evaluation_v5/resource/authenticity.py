"""Authenticated Kubernetes collector model and runtime identity verification for E4.

This module provides the authoritative boundary between REAL_KUBERNETES_COLLECTOR
execution and synthetic/test/fake/dry-run adapters. Caller-supplied strings are
explicitly rejected as proof of authenticity.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
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
)

UID_PATTERN = re.compile(r"^[0-9a-zA-Z-._]{8,64}$")
TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


@dataclass(frozen=True, slots=True)
class CollectorImplementationAssessment:
    """Structural assessment of collector implementation (NOT observation authenticity).

    Proves that the object is the expected production Kubernetes collector implementation
    and has not obviously been substituted, mocked, or monkeypatched.
    Implementation validity alone does NOT authorize OBSERVED evidence.
    """

    is_production_implementation: bool
    declared_collector_origin: str
    implementation_class: str
    implementation_version: str
    structural_findings: tuple[str, ...]

    @property
    def is_authenticated_real_collector(self) -> bool:
        """Structural check: is this the genuine production collector implementation?"""
        return self.is_production_implementation

    @property
    def collector_origin(self) -> str:
        return self.declared_collector_origin

    @property
    def derived_execution_status(self) -> str:
        """Structural check alone never authorizes OBSERVED."""
        if self.declared_collector_origin == COLLECTOR_ORIGIN_DRY_RUN:
            return "DRY_RUN"
        elif not self.is_production_implementation:
            return "SYNTHETIC"
        return "UNEXECUTED"

    @property
    def derived_cluster_measurement_status(self) -> str:
        return "NOT_EXECUTED"

    @property
    def claims_permitted(self) -> bool:
        return False


AdapterAuthenticity = CollectorImplementationAssessment


@dataclass(frozen=True, slots=True)
class ResourceCollectorOutcome:
    """Authoritative outcome of resource collection, bound to execution provenance."""

    execution_status: str  # "OBSERVED", "SYNTHETIC", "DRY_RUN", "NOT_EXECUTED", "INCOMPLETE", "FAILED"
    collector_origin: str  # "REAL_KUBERNETES_COLLECTOR", "SYNTHETIC", "DRY_RUN", "NOT_EXECUTED"
    cluster_measurement_status: str  # "OBSERVED", "NOT_EXECUTED"
    is_observed_eligible: bool
    failure_reasons: tuple[str, ...]
    trial_count: int
    environment_identity: str | None
    node_name: str | None
    node_uid: str | None


def validate_collector_implementation(adapter: Any) -> CollectorImplementationAssessment:
    """Validate collector implementation structure.

    Caller-provided strings, monkey-patched flags, __new__ without __init__,
    subclasses outside blessed modules, or method overwrites are explicitly
    rejected.

    NOTE: Structural implementation validity alone does NOT establish observation
    authenticity. Observation authenticity requires validate_collection_outcome().
    """
    if adapter is None or isinstance(adapter, (str, int, float, bool, list, dict, set, tuple)):
        return CollectorImplementationAssessment(
            is_production_implementation=False,
            declared_collector_origin=COLLECTOR_ORIGIN_SYNTHETIC,
            implementation_class=type(adapter).__qualname__,
            implementation_version="",
            structural_findings=("NON_OBJECT_ADAPTER",),
        )

    adapter_cls = type(adapter)
    module_name = getattr(adapter_cls, "__module__", "")
    class_name = getattr(adapter_cls, "__qualname__", "")

    # Lazy import to inspect class definitions without cyclic import
    from cluster_evaluation.resource_adapter_v5 import KubernetesTrialAdapter
    from cluster_evaluation.resource_efficiency_adapter_v5 import KubernetesResourceEfficiencyAdapter

    allowed_classes = (KubernetesTrialAdapter, KubernetesResourceEfficiencyAdapter)

    # 1. Exact class identity (reject subclasses and duck-typed classes)
    is_exact_class = (
        adapter_cls in allowed_classes
        and module_name in AUTHENTICATED_COLLECTOR_MODULES
        and class_name in AUTHENTICATED_COLLECTOR_CLASSES
    )

    # 2. Must be initialized via authentic __init__ (reject __new__ bypass)
    is_initialized = getattr(adapter, "_initialized", False) is True

    # 3. Method monkeypatching detection: execution methods must not be shadowed in instance __dict__
    instance_dict = getattr(adapter, "__dict__", {})
    critical_methods = ("run_trial", "environment_provenance", "_kubectl", "_preflight", "_json")
    is_monkeypatched = any(m in instance_dict for m in critical_methods)

    # 4. Method identity verification: bound method functions must match authentic class functions
    method_identities_match = True
    for m in critical_methods:
        func = getattr(getattr(adapter, m, None), "__func__", None)
        class_func = getattr(adapter_cls, m, None)
        if class_func is not None and func is not class_func:
            method_identities_match = False
            break

    # 5. Version check
    version = getattr(adapter, "adapter_version", "")
    version_ok = version in AUTHENTICATED_COLLECTOR_VERSIONS

    # 6. Scan instance attributes for forbidden synthetic tokens
    synthetic_findings = _scan_for_synthetic_tokens(instance_dict, path="adapter")

    findings = []
    if not is_exact_class:
        findings.append("CLASS_NOT_PRODUCTION_KUBERNETES_COLLECTOR")
    if not is_initialized:
        findings.append("ADAPTER_NOT_INITIALIZED_VIA_CONSTRUCTOR")
    if is_monkeypatched:
        findings.append("INSTANCE_METHODS_MONKEYPATCHED")
    if not method_identities_match:
        findings.append("METHOD_FUNCTION_IDENTITY_MISMATCH")
    if not version_ok:
        findings.append("INVALID_COLLECTOR_VERSION")
    if synthetic_findings:
        findings.extend(synthetic_findings)

    is_genuine = (
        is_exact_class
        and is_initialized
        and not is_monkeypatched
        and method_identities_match
        and version_ok
        and not synthetic_findings
    )

    if is_genuine:
        return CollectorImplementationAssessment(
            is_production_implementation=True,
            declared_collector_origin=COLLECTOR_ORIGIN_REAL_KUBERNETES,
            implementation_class=f"{module_name}.{class_name}",
            implementation_version=str(version),
            structural_findings=(),
        )

    # Fake, synthetic, dry-run, or caller-injected adapter
    existing_origin = getattr(adapter, "collector_origin", None)
    if isinstance(existing_origin, str) and existing_origin in VALID_COLLECTOR_ORIGINS and existing_origin != COLLECTOR_ORIGIN_REAL_KUBERNETES:
        origin = existing_origin
    else:
        version_lower = str(version).lower()
        origin_lower = str(existing_origin).lower()
        if "dry" in version_lower or "dry" in origin_lower:
            origin = COLLECTOR_ORIGIN_DRY_RUN
        else:
            origin = COLLECTOR_ORIGIN_SYNTHETIC

    return CollectorImplementationAssessment(
        is_production_implementation=False,
        declared_collector_origin=origin,
        implementation_class=f"{module_name}.{class_name}",
        implementation_version=str(version),
        structural_findings=tuple(findings),
    )


def authenticate_adapter(adapter: Any) -> CollectorImplementationAssessment:
    """Validate collector implementation structure.

    Retained for backwards-compatibility. Implementation validation alone
    does NOT establish observation authenticity.
    """
    return validate_collector_implementation(adapter)


def _scan_for_synthetic_tokens(value: Any, path: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, str):
        lowered = value.casefold()
        for token in FORBIDDEN_SYNTHETIC_TOKENS:
            if token in lowered:
                findings.append(f"{path}: contains forbidden token '{token}'")
    elif isinstance(value, Mapping):
        for k, v in value.items():
            findings.extend(_scan_for_synthetic_tokens(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(value, (list, tuple, set)):
        for i, item in enumerate(value):
            findings.extend(_scan_for_synthetic_tokens(item, f"{path}[{i}]"))
    return findings


def validate_collection_outcome(
    *,
    implementation: CollectorImplementationAssessment,
    environment: Mapping[str, Any] | None,
    observations_or_trials: Sequence[Any],
    expected_trial_count: int | None = None,
    is_efficiency: bool = False,
) -> ResourceCollectorOutcome:
    """Authoritatively validate execution outcome and runtime provenance.

    Authenticity belongs to the EXECUTION OUTCOME, not to the adapter object.
    OBSERVED is authorized ONLY when:
    1. implementation is a valid production collector (is_production_implementation is True).
    2. environment contains complete, authentic Kubernetes cluster and node identity.
    3. observations_or_trials contains non-empty, complete trial records matching expected count.
    4. each trial record contains genuine Kubernetes runtime identity (deterministic pod name,
       valid pod UID without synthetic tokens, node name matching environment, cgroup v2 metrics).
    5. no synthetic tokens or mock attributes exist in environment or observations.

    Missing required identity, failed preflight, or synthetic collectors produce
    NOT_EXECUTED, INCOMPLETE, FAILED, or SYNTHETIC. Never OBSERVED.
    """
    env_dict = dict(environment or {}) if isinstance(environment, Mapping) else {}
    env_id = env_dict.get("environment_id")
    node_name = env_dict.get("node_name") or env_dict.get("read_only_preflight", {}).get("facts", {}).get("node_name")
    node_uid = env_dict.get("node_uid") or env_dict.get("read_only_preflight", {}).get("facts", {}).get("node_uid")

    if not implementation.is_production_implementation:
        if implementation.declared_collector_origin == COLLECTOR_ORIGIN_DRY_RUN:
            return ResourceCollectorOutcome(
                execution_status=COLLECTOR_ORIGIN_DRY_RUN,
                collector_origin=COLLECTOR_ORIGIN_DRY_RUN,
                cluster_measurement_status="NOT_EXECUTED",
                is_observed_eligible=False,
                failure_reasons=("NON_PRODUCTION_COLLECTOR_DRY_RUN",),
                trial_count=len(observations_or_trials),
                environment_identity=env_id,
                node_name=None,
                node_uid=None,
            )
        return ResourceCollectorOutcome(
            execution_status=COLLECTOR_ORIGIN_SYNTHETIC,
            collector_origin=COLLECTOR_ORIGIN_SYNTHETIC,
            cluster_measurement_status="NOT_EXECUTED",
            is_observed_eligible=False,
            failure_reasons=("NON_PRODUCTION_COLLECTOR_SYNTHETIC",),
            trial_count=len(observations_or_trials),
            environment_identity=env_id,
            node_name=None,
            node_uid=None,
        )

    # Implementation is production Kubernetes, now inspect the actual collection outcome
    reasons: list[str] = []

    if not observations_or_trials:
        return ResourceCollectorOutcome(
            execution_status="NOT_EXECUTED",
            collector_origin=COLLECTOR_ORIGIN_REAL_KUBERNETES,
            cluster_measurement_status="NOT_EXECUTED",
            is_observed_eligible=False,
            failure_reasons=("NO_OBSERVATIONS_RECORDED",),
            trial_count=0,
            environment_identity=env_id,
            node_name=node_name,
            node_uid=node_uid,
        )

    if not environment or not isinstance(environment, Mapping):
        return ResourceCollectorOutcome(
            execution_status="FAILED",
            collector_origin=COLLECTOR_ORIGIN_REAL_KUBERNETES,
            cluster_measurement_status="NOT_EXECUTED",
            is_observed_eligible=False,
            failure_reasons=("MISSING_ENVIRONMENT_PROVENANCE",),
            trial_count=len(observations_or_trials),
            environment_identity=None,
            node_name=None,
            node_uid=None,
        )

    # 1. Environment preflight & authenticity
    preflight_failures = (env_dict.get("read_only_preflight") or {}).get("failure_codes") or []
    for code in preflight_failures:
        reasons.append(f"PREFLIGHT_FAILURE_{code}")
    if env_dict.get("eligibility_status") != "ELIGIBLE":
        reasons.append(f"PREFLIGHT_INELIGIBLE_{env_dict.get('eligibility_status')}")
    if env_dict.get("collector_origin") != COLLECTOR_ORIGIN_REAL_KUBERNETES:
        reasons.append("ENVIRONMENT_ORIGIN_NOT_REAL_KUBERNETES")
    if env_dict.get("cluster_measurement_status") != "OBSERVED":
        reasons.append("ENVIRONMENT_MEASUREMENT_STATUS_NOT_OBSERVED")

    env_findings = _scan_for_synthetic_tokens(env_dict, path="environment")
    if env_findings:
        reasons.append("SYNTHETIC_ENVIRONMENT_MARKERS")

    k8s_version = env_dict.get("kubernetes_version")
    if not isinstance(k8s_version, Mapping) or not k8s_version:
        reasons.append("MISSING_KUBERNETES_VERSION")

    context = env_dict.get("required_context") or env_dict.get("read_only_preflight", {}).get("facts", {}).get("current_context")
    if context != "intent-spawner-eval-v5":
        reasons.append("WRONG_KUBERNETES_CONTEXT")

    namespace = env_dict.get("namespace") or env_dict.get("read_only_preflight", {}).get("facts", {}).get("namespace_name")
    if namespace != "z2jh-context-demo":
        reasons.append("WRONG_KUBERNETES_NAMESPACE")

    if not node_name or not isinstance(node_name, str):
        reasons.append("MISSING_NODE_NAME")
    if not node_uid or not isinstance(node_uid, str) or not UID_PATTERN.fullmatch(node_uid):
        reasons.append("INVALID_NODE_UID")
    elif any(token in node_uid.casefold() for token in FORBIDDEN_SYNTHETIC_TOKENS):
        reasons.append("SYNTHETIC_TOKEN_IN_NODE_UID")

    node_info = env_dict.get("read_only_preflight", {}).get("facts", {}).get("node_info") or {}
    for req_field in ("kubelet_version", "container_runtime", "kernel_version", "operating_system", "architecture"):
        val = env_dict.get(req_field) or node_info.get(req_field) or node_info.get(
            {"kubelet_version": "kubeletVersion", "container_runtime": "containerRuntimeVersion", "kernel_version": "kernelVersion", "operating_system": "operatingSystem", "architecture": "architecture"}[req_field]
        )
        if not val or not isinstance(val, str):
            reasons.append(f"MISSING_NODE_FIELD_{req_field.upper()}")

    # 2. Observations completeness & runtime provenance
    if expected_trial_count is not None and len(observations_or_trials) < expected_trial_count:
        reasons.append(f"INCOMPLETE_TRIAL_COUNT_{len(observations_or_trials)}_OF_{expected_trial_count}")

    for row in observations_or_trials:
        row_dict = row.to_dict() if hasattr(row, "to_dict") else dict(row)
        trial_id = str(row_dict.get("run_id") or row_dict.get("trial_id") or "unknown")

        trial_origin = row_dict.get("collector_origin")
        if trial_origin != COLLECTOR_ORIGIN_REAL_KUBERNETES:
            reasons.append(f"TRIAL_{trial_id}_ORIGIN_NOT_REAL")

        trial_findings = _scan_for_synthetic_tokens(
            {
                "correctness_details": row_dict.get("correctness_details"),
                "exclusion_reason": row_dict.get("exclusion_reason"),
                "exit_reason": row_dict.get("exit_reason"),
            },
            path=trial_id,
        )
        if trial_findings:
            reasons.append(f"TRIAL_{trial_id}_SYNTHETIC_TOKENS")

        k8s_data = row_dict.get("kubernetes")
        if not isinstance(k8s_data, Mapping) or not k8s_data:
            reasons.append(f"TRIAL_{trial_id}_MISSING_KUBERNETES_DATA")
        else:
            pod_name = k8s_data.get("pod_name")
            if not pod_name or not isinstance(pod_name, str) or not (pod_name.startswith("e4-") or pod_name.startswith("e4e-")):
                reasons.append(f"TRIAL_{trial_id}_INVALID_POD_NAME")
            run_id = row_dict.get("run_id")
            t_id = row_dict.get("trial_id")
            if run_id is not None:
                expected_pod_name = "e4-" + hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()[:24]
                if pod_name != expected_pod_name:
                    reasons.append(f"TRIAL_{trial_id}_POD_NAME_MISMATCH")
            elif t_id is not None:
                expected_pod_name = "e4e-" + hashlib.sha256(str(t_id).encode("utf-8")).hexdigest()[:24]
                if pod_name != expected_pod_name:
                    reasons.append(f"TRIAL_{trial_id}_POD_NAME_MISMATCH")

            pod_uid = k8s_data.get("pod_uid")
            if not pod_uid or not isinstance(pod_uid, str) or not UID_PATTERN.fullmatch(pod_uid):
                reasons.append(f"TRIAL_{trial_id}_INVALID_POD_UID")
            elif any(token in pod_uid.casefold() for token in FORBIDDEN_SYNTHETIC_TOKENS):
                reasons.append(f"TRIAL_{trial_id}_SYNTHETIC_TOKEN_IN_POD_UID")

            row_node = k8s_data.get("node_name")
            if row_node and node_name and row_node != node_name:
                reasons.append(f"TRIAL_{trial_id}_NODE_NAME_MISMATCH")

            started = k8s_data.get("started_at")
            finished = k8s_data.get("finished_at")
            if started and not TIMESTAMP_PATTERN.fullmatch(started):
                reasons.append(f"TRIAL_{trial_id}_INVALID_STARTED_AT")
            if finished and not TIMESTAMP_PATTERN.fullmatch(finished):
                reasons.append(f"TRIAL_{trial_id}_INVALID_FINISHED_AT")

        cgroup = row_dict.get("cgroup_metrics") or {}
        if not isinstance(cgroup, Mapping):
            reasons.append(f"TRIAL_{trial_id}_INVALID_CGROUP_METRICS")
        elif row_dict.get("cgroup_version") not in (None, "v2") or cgroup.get("cgroup_version") not in (None, "v2"):
            reasons.append(f"TRIAL_{trial_id}_INVALID_CGROUP_VERSION")

    if reasons:
        is_incomplete = any("INCOMPLETE" in r for r in reasons)
        status = "INCOMPLETE" if is_incomplete else "FAILED"
        return ResourceCollectorOutcome(
            execution_status=status,
            collector_origin=COLLECTOR_ORIGIN_REAL_KUBERNETES,
            cluster_measurement_status="NOT_EXECUTED",
            is_observed_eligible=False,
            failure_reasons=tuple(reasons),
            trial_count=len(observations_or_trials),
            environment_identity=env_id,
            node_name=node_name,
            node_uid=node_uid,
        )

    return ResourceCollectorOutcome(
        execution_status="OBSERVED",
        collector_origin=COLLECTOR_ORIGIN_REAL_KUBERNETES,
        cluster_measurement_status="OBSERVED",
        is_observed_eligible=True,
        failure_reasons=(),
        trial_count=len(observations_or_trials),
        environment_identity=env_id,
        node_name=node_name,
        node_uid=node_uid,
    )


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
        for token in FORBIDDEN_SYNTHETIC_TOKENS:
            if token in node_uid.casefold():
                raise ValueError(f"OBSERVED environment node_uid contains forbidden token '{token}'")

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
            run_id = row_dict.get("run_id")
            trial_id = row_dict.get("trial_id")
            if run_id is not None:
                expected_pod_name = "e4-" + hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()[:24]
                if pod_name != expected_pod_name:
                    raise ValueError(
                        f"trial {run_id} pod_name {pod_name!r} does not match expected deterministic name {expected_pod_name!r}"
                    )
            elif trial_id is not None:
                expected_pod_name = "e4e-" + hashlib.sha256(str(trial_id).encode("utf-8")).hexdigest()[:24]
                if pod_name != expected_pod_name:
                    raise ValueError(
                        f"trial {trial_id} pod_name {pod_name!r} does not match expected deterministic name {expected_pod_name!r}"
                    )

            pod_uid = k8s_data.get("pod_uid")
            if not pod_uid or not isinstance(pod_uid, str) or not UID_PATTERN.fullmatch(pod_uid):
                raise ValueError(
                    f"trial {row_dict.get('run_id') or row_dict.get('trial_id')} "
                    f"lacks valid pod_uid runtime identity: {pod_uid!r}"
                )
            for token in FORBIDDEN_SYNTHETIC_TOKENS:
                if token in pod_uid.casefold():
                    raise ValueError(
                        f"trial {row_dict.get('run_id') or row_dict.get('trial_id')} "
                        f"pod_uid contains forbidden token '{token}'"
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

    elif execution_status in (COLLECTOR_ORIGIN_SYNTHETIC, "TEST_ONLY", COLLECTOR_ORIGIN_DRY_RUN, "NOT_EXECUTED"):
        if manifest.get("measurement_claims_permitted") is True:
            raise ValueError(f"{execution_status} evidence cannot permit measurement claims")
        if manifest.get("manual_review_status") == "APPROVED":
            raise ValueError(f"{execution_status} evidence cannot have an APPROVED manual review")
        if manifest.get("eligible_for_comparison") is True:
            raise ValueError(f"{execution_status} evidence cannot be eligible for comparison")


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
    "CollectorImplementationAssessment",
    "ResourceCollectorOutcome",
    "VALID_COLLECTOR_ORIGINS",
    "authenticate_adapter",
    "validate_collection_outcome",
    "validate_collector_implementation",
    "validate_resource_authenticity",
]
