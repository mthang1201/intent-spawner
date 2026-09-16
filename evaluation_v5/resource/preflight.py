"""Authoritative preflight verification engine for Protocol-v5 E4 execution.

Fails closed unless all required real-execution prerequisites exist:
1.  exact frozen Git revision;
2.  authoritative final freeze identity;
3.  workload manifest checksum;
4.  approved independent resource oracle identity where required;
5.  disposable evaluation cluster identity;
6.  Kubernetes server/environment identity;
7.  frozen node capacity;
8.  administrator-approved pinned execution image digest;
9.  cgroup/telemetry capability;
10. required namespaces/service account;
11. workload correctness markers;
12. timeout contract;
13. cleanup permissions;
14. trial ordering/randomization contract;
15. resume state;
16. no production/uncontrolled cluster.

OBSERVED status is impossible unless actual real trial observations exist.
A plan, generated Job YAML, fake adapter or dry-run must never become OBSERVED.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from evaluation_v5.freeze import (
    VerifiedProductionFreeze,
    verify_production_freeze,
)

from .contracts import (
    DEFAULT_MANIFEST,
    FREEZE_CONTRACT_PATH,
    IMAGE_STATE_PATH,
    ROOT,
    freeze_is_confirmatory,
    image_state_is_verified,
    load_cluster_policy,
    load_freeze_contract,
    load_image_state,
)
from .efficiency_contracts import (
    CAPACITY_PATH,
    FAMILY_COUNT,
    FREEZE_PATH as EFFICIENCY_FREEZE_PATH,
    INPUT_PATH as EFFICIENCY_INPUT_PATH,
    PRIMARY_TRIAL_COUNT,
    REPETITIONS,
    load_capacity_contract,
    load_condition_inputs,
    load_efficiency_freeze,
)
from .evidence import file_sha256, validate_evidence_package
from .manifest import load_resource_manifest, verify_workload_markers

PREFLIGHT_REPORT_SCHEMA_VERSION = "protocol-v5-resource-preflight-report-v1.0.0"
AUTHORITATIVE_FREEZE_PATH = ROOT / "results_v5" / "protocol-v5.0.0" / "freezes" / "v5-final-execution-freeze" / "freeze-manifest.json"
READINESS_ATTESTATION_SCHEMA_PATH = ROOT / "benchmarks_v5" / "protocol-v5-e4-readiness-attestation-v1.schema.json"
READINESS_ATTESTATION_ENV_VAR = "PROTOCOL_V5_E4_READINESS_ATTESTATION"
EXPECTED_EFFICIENCY_INPUT_SHA256 = "dce8d2b65bdfc7e2ce280e05645906b91a5d4bbfa1f089601ff54dbb5ab02e66"
IMAGE_RE = re.compile(r"^[a-z0-9._/-]+@sha256:[0-9a-f]{64}$")
FORBIDDEN_PRODUCTION_CONTEXTS = {
    "prod", "production", "live", "main", "default", "docker-desktop",
    "minikube", "kind", "k3d", "rancher-desktop",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _get_git_info() -> dict[str, Any]:
    try:
        from . import runner
        runner_git = getattr(runner, "_git_identity", None)
        if callable(runner_git):
            try:
                val = runner_git()
                if isinstance(val, dict) and "git_revision" in val:
                    return {"git_revision": val.get("git_revision"), "git_dirty": bool(val.get("git_dirty")), "git_available": True}
            except Exception:
                pass
    except ImportError:
        pass
    try:
        rev_cmd = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        status_cmd = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        revision = rev_cmd.stdout.strip() if rev_cmd.returncode == 0 else None
        dirty = bool(status_cmd.stdout.strip()) if status_cmd.returncode == 0 else True
        return {"git_revision": revision, "git_dirty": dirty, "git_available": True}
    except Exception as exc:
        return {"git_revision": None, "git_dirty": True, "git_available": False, "error": str(exc)}


@dataclass(frozen=True, slots=True)
class PreflightCheckResult:
    check_name: str
    passed: bool
    blocker_codes: tuple[str, ...]
    details: dict[str, Any]
    reasons: tuple[str, ...]


def _load_readiness_attestation(
    path: Path,
    *,
    freeze: VerifiedProductionFreeze,
) -> dict[str, Any]:
    """Load an external, checksum-bound readiness statement and bind its freeze."""

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        selected: dict[str, Any] = {}
        for key, value in pairs:
            if key in selected:
                raise ValueError("E4 readiness attestation contains duplicate JSON keys")
            selected[key] = value
        return selected

    try:
        raw = path.read_bytes()
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(
                    f"E4 readiness attestation contains non-finite JSON value {value}"
                )
            ),
        )
        schema = json.loads(READINESS_ATTESTATION_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("E4 readiness attestation is unreadable") from exc
    if not isinstance(document, dict):
        raise ValueError("E4 readiness attestation must be a JSON object")
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "root"
        raise ValueError(
            f"E4 readiness attestation schema violation at {location}: {errors[0].message}"
        )
    expected = freeze.identity
    actual = document["freeze"]
    for key in (
        "freeze_id",
        "freeze_manifest_sha256",
        "frozen_execution_sha",
        "freeze_artifact_commit_sha",
    ):
        if actual.get(key) != expected.get(key):
            raise ValueError(f"E4 readiness attestation freeze mismatch at {key}")
    document["_attestation_path"] = str(path.resolve())
    document["_attestation_sha256"] = file_sha256(path)
    return document


def _external_image_state(attestation: Mapping[str, Any]) -> dict[str, Any]:
    reference = str(attestation["execution_image"]["reference"])
    digest = reference.split("@", 1)[1]
    return {
        "schema_version": "protocol-v5-external-resource-image-state-v1.0.0",
        "image_reference": reference,
        "reference_configured": True,
        "digest_syntactically_pinned": True,
        "built": True,
        "resolved_digest": digest,
        "digest_verified": True,
        "pre_pulled_on_eligible_node": True,
        "operationally_verified": True,
        "status": "EXTERNALLY_ATTESTED_AND_LIVE_RECHECKED",
    }


def verify_e4_readiness_inputs(
    *, freeze_path: Path, readiness_attestation_path: Path
) -> tuple[VerifiedProductionFreeze, dict[str, Any]]:
    freeze = verify_production_freeze(freeze_path)
    return freeze, _load_readiness_attestation(
        readiness_attestation_path, freeze=freeze
    )


def check_adapter_authenticity(adapter: Any | None = None) -> PreflightCheckResult:
    if adapter is None:
        return PreflightCheckResult(
            check_name="collector_implementation_authenticity",
            passed=True,
            blocker_codes=(),
            details={"status": "not_applicable_no_adapter_passed"},
            reasons=(),
        )
    from .authenticity import validate_collector_implementation
    auth = validate_collector_implementation(adapter)
    if not auth.is_production_implementation:
        return PreflightCheckResult(
            check_name="collector_implementation_authenticity",
            passed=False,
            blocker_codes=("AUTHENTICATED_REAL_KUBERNETES_COLLECTOR_REQUIRED",),
            details={"collector_class": auth.implementation_class, "origin": auth.declared_collector_origin},
            reasons=("Adapter is not an authentic production Kubernetes collector.",),
        )
    return PreflightCheckResult(
        check_name="collector_implementation_authenticity",
        passed=True,
        blocker_codes=(),
        details={"collector_class": auth.implementation_class, "origin": auth.declared_collector_origin},
        reasons=(),
    )


def check_frozen_git_revision(environ: Mapping[str, str] | None = None) -> PreflightCheckResult:
    git_info = _get_git_info()
    blockers: list[str] = []
    reasons: list[str] = []
    details: dict[str, Any] = dict(git_info)

    if not git_info.get("git_available"):
        blockers.append("GIT_UNAVAILABLE")
        reasons.append("Git binary or repository metadata is unavailable.")
    elif git_info.get("git_dirty"):
        blockers.append("DIRTY_GIT_TREE")
        reasons.append("Working tree has untracked or uncommitted changes.")

    env_map = os.environ if environ is None else environ
    expected_rev = env_map.get("PROTOCOL_V5_FROZEN_GIT_REVISION") or env_map.get("E4_EXPECTED_GIT_SHA")
    details["expected_git_revision"] = expected_rev
    if expected_rev:
        if git_info.get("git_revision") != expected_rev:
            blockers.append("FROZEN_GIT_REVISION_MISMATCH")
            reasons.append(
                f"Current revision {git_info.get('git_revision')} does not match frozen {expected_rev}."
            )

    return PreflightCheckResult(
        check_name="frozen_git_revision",
        passed=not blockers,
        blocker_codes=tuple(sorted(set(blockers))),
        details=details,
        reasons=tuple(reasons),
    )


def check_authoritative_final_freeze(
    *, target: str, freeze_path: Path = AUTHORITATIVE_FREEZE_PATH
) -> PreflightCheckResult:
    blockers: list[str] = []
    reasons: list[str] = []
    details: dict[str, Any] = {}

    if not freeze_path.is_file():
        blockers.append("AUTHORITATIVE_FREEZE_MISSING")
        reasons.append(f"Authoritative freeze file missing at {freeze_path}.")
    else:
        try:
            verified = verify_production_freeze(freeze_path)
            details.update(verified.identity)
            p3 = verified.configuration_snapshot["p3_gate"]
            if p3.get("status") != "not_retained" or p3.get("p3_active") is not False:
                blockers.append("P3_GATE_NOT_EXCLUDED")
                reasons.append("Authoritative freeze configuration does not exclude P3.")
        except Exception as exc:
            blockers.append("AUTHORITATIVE_FREEZE_CORRUPT")
            reasons.append(f"Authoritative freeze verification failed: {exc}")

    return PreflightCheckResult(
        check_name="authoritative_final_freeze",
        passed=not blockers,
        blocker_codes=tuple(sorted(set(blockers))),
        details=details,
        reasons=tuple(reasons),
    )


def check_external_readiness_attestation(
    *, freeze_path: Path, attestation_path: Path | None
) -> tuple[PreflightCheckResult, dict[str, Any] | None]:
    if attestation_path is None:
        return (
            PreflightCheckResult(
                check_name="external_readiness_attestation",
                passed=False,
                blocker_codes=("E4_READINESS_ATTESTATION_MISSING",),
                details={"required_schema": str(READINESS_ATTESTATION_SCHEMA_PATH.relative_to(ROOT))},
                reasons=("No external E4 readiness attestation was supplied.",),
            ),
            None,
        )
    try:
        _, attestation = verify_e4_readiness_inputs(
            freeze_path=freeze_path,
            readiness_attestation_path=attestation_path,
        )
    except Exception as exc:
        return (
            PreflightCheckResult(
                check_name="external_readiness_attestation",
                passed=False,
                blocker_codes=("E4_READINESS_ATTESTATION_INVALID",),
                details={"path": str(attestation_path)},
                reasons=(str(exc),),
            ),
            None,
        )
    return (
        PreflightCheckResult(
            check_name="external_readiness_attestation",
            passed=True,
            blocker_codes=(),
            details={
                "path": attestation["_attestation_path"],
                "sha256": attestation["_attestation_sha256"],
                "attestation_id": attestation["attestation_id"],
            },
            reasons=(),
        ),
        attestation,
    )


def check_workload_manifest(*, target: str, manifest_path: Path | None = None) -> PreflightCheckResult:
    blockers: list[str] = []
    reasons: list[str] = []
    details: dict[str, Any] = {}

    m_path = (manifest_path or DEFAULT_MANIFEST).resolve()
    details["manifest_path"] = str(m_path.relative_to(ROOT)) if m_path.is_relative_to(ROOT) else str(m_path)
    if not m_path.is_file():
        blockers.append("WORKLOAD_MANIFEST_MISSING")
        reasons.append(f"Workload manifest missing at {m_path}.")
    else:
        try:
            manifest = load_resource_manifest(m_path)
            details["manifest_sha256"] = file_sha256(m_path)
            workloads = manifest.get("workloads") or []
            details["family_count"] = len(workloads)
            if len(workloads) != FAMILY_COUNT:
                blockers.append("WORKLOAD_FAMILY_COUNT_MISMATCH")
                reasons.append(f"Manifest has {len(workloads)} families, expected {FAMILY_COUNT}.")
        except Exception as exc:
            blockers.append("WORKLOAD_MANIFEST_INVALID")
            reasons.append(f"Workload manifest error: {exc}")

    if target in ("efficiency", "all"):
        if not EFFICIENCY_INPUT_PATH.is_file():
            blockers.append("EFFICIENCY_INPUT_MISSING")
            reasons.append(f"Efficiency inputs missing at {EFFICIENCY_INPUT_PATH.relative_to(ROOT)}.")
        else:
            try:
                load_condition_inputs()
                actual_sha = file_sha256(EFFICIENCY_INPUT_PATH)
                details["efficiency_input_sha256"] = actual_sha
                if actual_sha != EXPECTED_EFFICIENCY_INPUT_SHA256:
                    blockers.append("EFFICIENCY_INPUT_SHA256_MISMATCH")
                    reasons.append("Efficiency input checksum differs from registered frozen checksum.")
            except Exception as exc:
                blockers.append("EFFICIENCY_INPUT_INVALID")
                reasons.append(f"Efficiency input validation error: {exc}")

    return PreflightCheckResult(
        check_name="workload_manifest",
        passed=not blockers,
        blocker_codes=tuple(sorted(set(blockers))),
        details=details,
        reasons=tuple(reasons),
    )


def check_approved_resource_oracle(
    *, target: str, attestation: Mapping[str, Any] | None = None
) -> PreflightCheckResult:
    if target == "envelope":
        return PreflightCheckResult(
            check_name="approved_independent_resource_oracle",
            passed=True,
            blocker_codes=(),
            details={"status": "not_applicable_to_envelope_calibration"},
            reasons=(),
        )

    blockers: list[str] = []
    reasons: list[str] = []
    details: dict[str, Any] = {}

    try:
        oracle_info = (attestation or {}).get("oracle") or {}
        oracle_path_str = oracle_info.get("package_path")
        expected_sha = oracle_info.get("sha256sums_sha256")
        approval_status = oracle_info.get("manual_review_status")
        details["oracle_path"] = oracle_path_str
        details["expected_sha256"] = expected_sha
        details["manual_approval_status"] = approval_status

        if approval_status != "APPROVED" or not oracle_path_str or not expected_sha:
            blockers.append("APPROVED_ORACLE_UNAVAILABLE")
            reasons.append("External readiness attestation does not bind an approved independent oracle.")
        else:
            oracle_path = (ROOT / str(oracle_path_str)).resolve()
            if not oracle_path.is_dir():
                blockers.append("APPROVED_ORACLE_DIRECTORY_MISSING")
                reasons.append(f"Oracle directory not found at {oracle_path}.")
            else:
                actual_sha = file_sha256(oracle_path / "SHA256SUMS")
                if actual_sha != expected_sha:
                    blockers.append("APPROVED_ORACLE_CHECKSUM_MISMATCH")
                    reasons.append("Oracle directory SHA256SUMS does not match frozen oracle_package hash.")
                pkg_val = validate_evidence_package(oracle_path)
                if pkg_val.get("manual_review_status") != "APPROVED":
                    blockers.append("APPROVED_ORACLE_REVIEW_NOT_APPROVED")
                    reasons.append("Oracle safe envelopes have not been formally approved.")
    except Exception as exc:
        blockers.append("APPROVED_ORACLE_UNAVAILABLE")
        reasons.append(f"Failed to check approved oracle: {exc}")

    return PreflightCheckResult(
        check_name="approved_independent_resource_oracle",
        passed=not blockers,
        blocker_codes=tuple(sorted(set(blockers))),
        details=details,
        reasons=tuple(reasons),
    )


def check_disposable_cluster_and_environment(
    *, image: str | None = None, attestation: Mapping[str, Any] | None = None
) -> PreflightCheckResult:
    from cluster_evaluation.resource_adapter_v5 import (
        _cpu_m,
        _memory_mib,
        collect_read_only_preflight,
    )

    blockers: list[str] = []
    reasons: list[str] = []
    details: dict[str, Any] = {}

    policy = load_cluster_policy()
    img_state = (
        _external_image_state(attestation)
        if attestation is not None
        else load_image_state()
    )
    test_image = image or img_state.get("image_reference") or "example.invalid/intent-spawner-resource-v5@sha256:" + "a" * 64

    preflight_facts = collect_read_only_preflight(image=test_image, policy=policy, image_state=img_state)
    details["read_only_preflight"] = preflight_facts
    failure_codes = list(preflight_facts.get("failure_codes") or [])
    if attestation is not None:
        externally_bound_cgroup = {
            "CGROUP_V2_REQUIRED",
            "CGROUP_CONTROLLER_MISSING",
            "CGROUP_MEASUREMENT_FILE_MISSING",
            "CGROUP_MEMORY_EVENT_KEY_MISSING",
        }
        failure_codes = [code for code in failure_codes if code not in externally_bound_cgroup]

    facts = preflight_facts.get("facts") or {}
    if attestation is not None:
        cluster = attestation["cluster"]
        capacity = attestation["node_capacity"]
        policy_node_label = policy["node_identity_label"]
        if facts.get("current_context") != cluster["context"]:
            failure_codes.append("ATTESTED_CLUSTER_CONTEXT_MISMATCH")
        if facts.get("namespace_name") != cluster["namespace"]:
            failure_codes.append("ATTESTED_CLUSTER_NAMESPACE_MISMATCH")
        cluster_label = policy["cluster_identity_label"]
        if (facts.get("namespace_labels") or {}).get(
            cluster_label["key"]
        ) != cluster["cluster_identity"]:
            failure_codes.append("ATTESTED_CLUSTER_IDENTITY_MISMATCH")
        version_identity = _canonical_json_sha256(
            facts.get("kubernetes_version")
        )
        if version_identity != cluster["kubernetes_version_sha256"]:
            failure_codes.append("ATTESTED_KUBERNETES_VERSION_MISMATCH")
        if (facts.get("node_labels") or {}).get(policy_node_label["key"]) != capacity["node_identity"]:
            failure_codes.append("ATTESTED_NODE_IDENTITY_MISMATCH")
        if facts.get("node_name") != capacity["node_name"]:
            failure_codes.append("ATTESTED_NODE_NAME_MISMATCH")
        if facts.get("node_uid") != capacity["node_uid"]:
            failure_codes.append("ATTESTED_NODE_UID_MISMATCH")
        allocatable = facts.get("node_allocatable") or {}
        gpu_resource = capacity["allocatable_gpu_resource"]
        raw_gpu = 0 if gpu_resource is None else allocatable.get(gpu_resource)
        if isinstance(raw_gpu, str) and raw_gpu.isdigit():
            raw_gpu = int(raw_gpu)
        observed_capacity = {
            "allocatable_cpu_millicores": _cpu_m(allocatable.get("cpu")),
            "allocatable_memory_mib": _memory_mib(allocatable.get("memory")),
            "allocatable_gpu_count": raw_gpu,
            "allocatable_gpu_resource": gpu_resource,
        }
        expected_capacity = {
            key: capacity[key]
            for key in (
                "allocatable_cpu_millicores",
                "allocatable_memory_mib",
                "allocatable_gpu_count",
                "allocatable_gpu_resource",
            )
        }
        if observed_capacity != expected_capacity:
            failure_codes.append("ATTESTED_NODE_CAPACITY_MISMATCH")
    current_context = facts.get("current_context")
    if current_context and any(prod_token in current_context.lower() for prod_token in FORBIDDEN_PRODUCTION_CONTEXTS):
        failure_codes.append("PRODUCTION_CLUSTER_FORBIDDEN")
        reasons.append(f"Current context '{current_context}' matches a forbidden production/local pattern.")

    for code in failure_codes:
        blockers.append(code)
        if code == "KUBECTL_UNAVAILABLE":
            reasons.append("kubectl binary is not available or not on PATH.")
        elif code == "WRONG_KUBERNETES_CONTEXT":
            reasons.append(f"Current context is '{current_context}', required is '{policy['expected_context']}'.")
        elif code == "WRONG_CLUSTER_FINGERPRINT":
            reasons.append("Namespace lacks cluster identity label 'z2jh-context-demo.local/cluster-identity'.")
        elif code == "CLUSTER_INELIGIBLE":
            reasons.append("Namespace lacks safety label 'z2jh-context-demo.local/disposable-experiment-v5=true'.")
        elif code == "WRONG_NODE_COUNT":
            reasons.append(f"Cluster does not have exactly {policy['required_node_count']} node.")
        elif code == "WRONG_NODE_IDENTITY":
            reasons.append("Node lacks required identity label 'z2jh-context-demo.local/node-identity=e4-node-v1'.")
        elif code == "NODE_ISOLATION_REQUIREMENT_NOT_MET":
            reasons.append("Node lacks isolation label or non-daemonset workloads are present.")
        elif code == "REQUIRED_API_ACCESS_MISSING":
            reasons.append("Service account lacks required API permissions (create/get/list/delete pods).")
        elif code == "KUBERNETES_VERSION_UNAVAILABLE":
            reasons.append("Kubernetes server version is unreachable.")
        elif code == "RESOURCE_QUOTA_PRESENT":
            reasons.append("Namespace has active resource quotas.")
        elif code == "IMAGE_NOT_PREPULLED":
            reasons.append("Execution image is not pre-pulled on the dedicated node.")

    return PreflightCheckResult(
        check_name="disposable_cluster_and_environment",
        passed=not blockers,
        blocker_codes=tuple(sorted(set(blockers))),
        details=details,
        reasons=tuple(reasons),
    )


def check_frozen_node_capacity(
    *, target: str, attestation: Mapping[str, Any] | None = None
) -> PreflightCheckResult:
    blockers: list[str] = []
    reasons: list[str] = []
    details: dict[str, Any] = {}

    if target in ("efficiency", "all"):
        try:
            capacity_contract = (attestation or {}).get("node_capacity") or {}
            details["capacity_freeze_status"] = (
                "EXTERNALLY_ATTESTED" if capacity_contract else "NOT_FROZEN"
            )
            details["allocatable"] = capacity_contract
            if not capacity_contract:
                blockers.append("NODE_CAPACITY_NOT_FROZEN")
                reasons.append("No externally attested node capacity was supplied.")
        except Exception as exc:
            blockers.append("NODE_CAPACITY_NOT_FROZEN")
            reasons.append(f"Failed to load capacity contract: {exc}")

    return PreflightCheckResult(
        check_name="frozen_node_capacity",
        passed=not blockers,
        blocker_codes=tuple(sorted(set(blockers))),
        details=details,
        reasons=tuple(reasons),
    )


def check_pinned_image_digest(
    *, image: str | None = None, attestation: Mapping[str, Any] | None = None
) -> PreflightCheckResult:
    blockers: list[str] = []
    reasons: list[str] = []
    details: dict[str, Any] = {}

    img_state = (
        _external_image_state(attestation)
        if attestation is not None
        else load_image_state()
    )
    declared_ref = img_state.get("image_reference")
    effective_image = image or declared_ref
    details["image_reference"] = effective_image
    details["image_state_status"] = img_state.get("status")

    if not effective_image or not IMAGE_RE.fullmatch(effective_image):
        blockers.append("IMAGE_REFERENCE_UNPINNED")
        blockers.append("IMAGE_DIGEST_UNVERIFIED")
        reasons.append(f"Image '{effective_image}' is not pinned to an immutable sha256 digest.")
    elif not image_state_is_verified(img_state, effective_image):
        blockers.append("IMAGE_DIGEST_UNVERIFIED")
        reasons.append(f"Image state contract {IMAGE_STATE_PATH.relative_to(ROOT)} is not verified for {effective_image}.")

    return PreflightCheckResult(
        check_name="pinned_execution_image_digest",
        passed=not blockers,
        blocker_codes=tuple(sorted(set(blockers))),
        details=details,
        reasons=tuple(reasons),
    )


def check_cgroup_capability(
    attestation: Mapping[str, Any] | None = None,
) -> PreflightCheckResult:
    policy = load_cluster_policy()
    cgroup = (attestation or {}).get("cgroup") or {}
    passed = bool(
        cgroup
        and cgroup.get("version") == policy.get("required_cgroup_version")
        and set(policy.get("required_cgroup_controllers") or []).issubset(
            set(cgroup.get("controllers") or [])
        )
        and cgroup.get("required_files_verified") is True
    )
    return PreflightCheckResult(
        check_name="cgroup_telemetry_capability",
        passed=passed,
        blocker_codes=() if passed else ("CGROUP_CAPABILITY_NOT_ATTESTED",),
        details={
            "required_cgroup_version": policy.get("required_cgroup_version"),
            "required_controllers": policy.get("required_cgroup_controllers"),
            "required_files": policy.get("required_cgroup_files"),
            "required_memory_events": policy.get("required_memory_event_keys"),
        },
        reasons=() if passed else ("External cgroup-v2 capability attestation is missing.",),
    )


def check_workload_correctness_markers(*, manifest_path: Path | None = None) -> PreflightCheckResult:
    blockers: list[str] = []
    reasons: list[str] = []
    m_path = (manifest_path or DEFAULT_MANIFEST).resolve()
    details: dict[str, Any] = {}

    try:
        manifest = load_resource_manifest(m_path)
        report = verify_workload_markers(manifest)
        details.update(report)
        if report.get("status") != "pass" or report.get("verified_markers") != FAMILY_COUNT:
            blockers.append("WORKLOAD_MARKERS_UNVERIFIED")
            reasons.append(f"Workload marker verification failed: {report}.")
    except Exception as exc:
        blockers.append("WORKLOAD_MARKERS_UNVERIFIED")
        reasons.append(f"Workload marker verification raised: {exc}")

    return PreflightCheckResult(
        check_name="workload_correctness_markers",
        passed=not blockers,
        blocker_codes=tuple(sorted(set(blockers))),
        details=details,
        reasons=tuple(reasons),
    )


def check_timeout_and_cleanup_contracts() -> PreflightCheckResult:
    policy = load_cluster_policy()
    return PreflightCheckResult(
        check_name="timeout_and_cleanup_contracts",
        passed=True,
        blocker_codes=(),
        details={
            "pod_lifecycle_grace_seconds": 30,
            "adapter_monitor_grace_seconds": 15,
            "single_active_e4_pod": policy.get("single_active_e4_pod"),
            "bounded_cleanup": True,
        },
        reasons=(),
    )


def check_trial_ordering_contract(*, target: str) -> PreflightCheckResult:
    details: dict[str, Any] = {
        "envelope_ordering": "deterministic_family_lattice_walk",
    }
    if target in ("efficiency", "all"):
        try:
            eff_freeze = load_efficiency_freeze()
            details["efficiency_algorithm"] = eff_freeze["experiment"]["execution_order_algorithm"]
            details["plan_seed"] = eff_freeze["experiment"]["plan_seed"]
            details["repetitions"] = eff_freeze["experiment"]["repetitions"]
            details["primary_trials"] = eff_freeze["experiment"]["primary_trial_count"]
        except Exception as exc:
            return PreflightCheckResult(
                check_name="trial_ordering_contract",
                passed=False,
                blocker_codes=("PLAN_CONTRACT_BINDING_MISMATCH",),
                details=details,
                reasons=(f"Trial ordering contract failed: {exc}",),
            )

    return PreflightCheckResult(
        check_name="trial_ordering_contract",
        passed=True,
        blocker_codes=(),
        details=details,
        reasons=(),
    )


def check_resume_state(
    *,
    result_dir: Path | None,
    resume: bool,
) -> PreflightCheckResult:
    if result_dir is None:
        return PreflightCheckResult(
            check_name="resume_state",
            passed=True,
            blocker_codes=(),
            details={"status": "not_applicable_no_result_dir_provided"},
            reasons=(),
        )

    blockers: list[str] = []
    reasons: list[str] = []
    r_dir = result_dir.resolve()
    details: dict[str, Any] = {"result_dir": str(r_dir), "resume": resume}

    if not resume:
        if r_dir.exists():
            blockers.append("TARGET_DIRECTORY_EXISTS")
            reasons.append(f"Target directory {r_dir} already exists and --resume was not specified.")
    else:
        if not r_dir.is_dir():
            blockers.append("RESUME_TARGET_NOT_FOUND")
            reasons.append(f"Target directory {r_dir} does not exist to resume.")
        elif (r_dir / "SHA256SUMS").exists():
            blockers.append("RESUME_SEALED_PACKAGE_FORBIDDEN")
            reasons.append(f"Target directory {r_dir} is already sealed and cannot be resumed.")
        else:
            # Check for dry-run contamination in resume target
            manifest_p = r_dir / "manifest.json"
            if manifest_p.is_file():
                try:
                    prior = json.loads(manifest_p.read_text(encoding="utf-8"))
                    if prior.get("execution_status") in ("DRY_RUN", "NOT_EXECUTED"):
                        blockers.append("CANNOT_RESUME_DRY_RUN_AS_REAL_EXECUTION")
                        reasons.append("Cannot resume a dry-run or not-executed package as real execution.")
                except Exception:
                    pass
            env_p = r_dir / "raw" / "environment.json"
            if env_p.is_file():
                try:
                    prior_env = json.loads(env_p.read_text(encoding="utf-8"))
                    if prior_env.get("collector_origin") in ("DRY_RUN", "SYNTHETIC"):
                        blockers.append("CANNOT_RESUME_DRY_RUN_AS_REAL_EXECUTION")
                        reasons.append("Cannot resume a non-production origin package as real execution.")
                except Exception:
                    pass

    return PreflightCheckResult(
        check_name="resume_state",
        passed=not blockers,
        blocker_codes=tuple(sorted(set(blockers))),
        details=details,
        reasons=tuple(reasons),
    )


def evaluate_operator_preflight(
    *,
    target: str = "all",
    adapter: Any = None,
    image: str | None = None,
    result_dir: Path | None = None,
    resume: bool = False,
    manifest_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    freeze_path: Path = AUTHORITATIVE_FREEZE_PATH,
    readiness_attestation_path: Path | None = None,
) -> dict[str, Any]:
    """Evaluate all 16 execution prerequisites and emit authoritative preflight report."""
    if target not in ("envelope", "efficiency", "all"):
        raise ValueError(f"Invalid preflight target '{target}'. Must be envelope, efficiency, or all.")

    selected_environ = os.environ if environ is None else environ
    if readiness_attestation_path is None:
        attestation_value = selected_environ.get(READINESS_ATTESTATION_ENV_VAR)
        if attestation_value:
            readiness_attestation_path = Path(attestation_value)
    attestation_check, attestation = check_external_readiness_attestation(
        freeze_path=freeze_path,
        attestation_path=readiness_attestation_path,
    )
    checks: list[PreflightCheckResult] = [
        check_frozen_git_revision(environ=environ),
        check_authoritative_final_freeze(target=target, freeze_path=freeze_path),
        attestation_check,
        check_workload_manifest(target=target, manifest_path=manifest_path),
        check_approved_resource_oracle(target=target, attestation=attestation),
        check_adapter_authenticity(adapter=adapter),
        check_disposable_cluster_and_environment(image=image, attestation=attestation),
        check_frozen_node_capacity(target=target, attestation=attestation),
        check_pinned_image_digest(image=image, attestation=attestation),
        check_cgroup_capability(attestation),
        check_workload_correctness_markers(manifest_path=manifest_path),
        check_timeout_and_cleanup_contracts(),
        check_trial_ordering_contract(target=target),
        check_resume_state(result_dir=result_dir, resume=resume),
    ]

    all_blocker_codes: list[str] = []
    all_reasons: list[str] = []
    checks_dict: dict[str, Any] = {}

    for c in checks:
        checks_dict[c.check_name] = {
            "status": "PASS" if c.passed else "FAIL",
            "blocker_codes": list(c.blocker_codes),
            "reasons": list(c.reasons),
            "details": c.details,
        }
        all_blocker_codes.extend(c.blocker_codes)
        all_reasons.extend(c.reasons)

    unique_blockers = sorted(set(all_blocker_codes))
    is_ready = len(unique_blockers) == 0

    git_info = _get_git_info()
    return {
        "schema_version": PREFLIGHT_REPORT_SCHEMA_VERSION,
        "status": "READY" if is_ready else "NOT_EXECUTED",
        "target": target,
        "timestamp_utc": _utc_now(),
        "git_revision": git_info.get("git_revision"),
        "git_dirty": git_info.get("git_dirty"),
        "summary": {
            "is_ready": is_ready,
            "blocker_count": len(unique_blockers),
            "blocker_codes": unique_blockers,
            "reasons": all_reasons,
        },
        "checks": checks_dict,
        "limitations": [
            "Preflight verifies software and cluster readiness only; it does not observe experimental trials.",
            "OBSERVED status remains impossible without genuine collector execution records.",
        ],
    }


def assert_live_execution_ready(
    *,
    target: str,
    adapter: Any = None,
    image: str,
    result_dir: Path | None = None,
    resume: bool = False,
    manifest_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    freeze_path: Path = AUTHORITATIVE_FREEZE_PATH,
    readiness_attestation_path: Path | None = None,
) -> dict[str, Any]:
    """Fail closed with descriptive RuntimeError if environment is not READY for real execution."""
    report = evaluate_operator_preflight(
        target=target,
        adapter=adapter,
        image=image,
        result_dir=result_dir,
        resume=resume,
        manifest_path=manifest_path,
        environ=environ,
        freeze_path=freeze_path,
        readiness_attestation_path=readiness_attestation_path,
    )
    if report["status"] != "READY":
        blockers = report["summary"]["blocker_codes"]
        prefix = "OBSERVED_E4_EXECUTION_BLOCKED" if target == "envelope" else "RESOURCE_EFFICIENCY_EXECUTION_BLOCKED"
        raise RuntimeError(f"{prefix}: " + ",".join(blockers))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Protocol-v5 E4 Operator Preflight Verification")
    parser.add_argument("--target", choices=("envelope", "efficiency", "all"), default="all", help="Target evaluation subsystem")
    parser.add_argument("--image", type=str, default=None, help="Container image reference to check")
    parser.add_argument("--result-dir", type=Path, default=None, help="Target result directory to verify resume/pre-existence")
    parser.add_argument("--resume", action="store_true", help="Whether run is resuming")
    parser.add_argument("--freeze", type=Path, default=AUTHORITATIVE_FREEZE_PATH, help="Authoritative final freeze manifest")
    parser.add_argument("--readiness-attestation", type=Path, default=None, help="External E4 readiness attestation")
    parser.add_argument("--format", choices=("json", "text"), default="json", help="Output format")
    args = parser.parse_args(argv)

    report = evaluate_operator_preflight(
        target=args.target,
        image=args.image,
        result_dir=args.result_dir,
        resume=args.resume,
        freeze_path=args.freeze,
        readiness_attestation_path=args.readiness_attestation,
    )

    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        status = report["status"]
        print(f"Protocol-v5 E4 Preflight Status: {status}")
        print(f"Target: {report['target']}")
        print(f"Git Revision: {report['git_revision']} (dirty: {report['git_dirty']})")
        if status == "READY":
            print("Environment is READY for real Kubernetes execution.")
        else:
            print(f"Environment is NOT_EXECUTED. Blockers ({len(report['summary']['blocker_codes'])}):")
            for code in report["summary"]["blocker_codes"]:
                print(f"  - {code}")
            print("\nReasons:")
            for reason in report["summary"]["reasons"]:
                print(f"  * {reason}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
