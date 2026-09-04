"""Typed, serializable contracts for independent E4 calibration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol


TRIAL_SCHEMA_VERSION = "protocol-v5-resource-trial-v1.1.0"


@dataclass(frozen=True, slots=True)
class TrialSpec:
    run_id: str
    plan_index: int
    family_id: str
    workload_instance_id: str
    workload_fingerprint: str
    phase: str
    cpu_m: int
    memory_mib: int
    repeat_index: int
    deterministic_seed: int
    expected_marker_sha256: str
    timeout_seconds: int
    replacement_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TrialObservation:
    schema_version: str
    run_id: str
    family_id: str
    workload_instance_id: str
    workload_fingerprint: str
    phase: str
    cpu_m: int
    memory_mib: int
    repeat_index: int
    deterministic_seed: int
    expected_marker_sha256: str
    observed_marker_sha256: str | None
    exit_code: int | None
    exit_reason: str | None
    oom_killed: bool
    timeout: bool
    workload_timeout_seconds: int
    runtime_seconds: float | None
    correctness_marker_ok: bool
    correctness_invariants_ok: bool
    correctness_details: Mapping[str, Any]
    infrastructure_invalid: bool
    exclusion_reason: str | None
    cgroup_version: str | None
    cgroup_metrics: Mapping[str, Any]
    kubernetes: Mapping[str, Any]
    replacement_of: str | None
    recorded_at_utc: str

    @property
    def workload_success(self) -> bool:
        return (
            not self.infrastructure_invalid
            and not self.oom_killed
            and not self.timeout
            and self.exit_code == 0
            and self.correctness_marker_ok
            and self.correctness_invariants_ok
            and self.runtime_seconds is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrialObservation":
        return cls(**dict(payload))


class TrialAdapter(Protocol):
    """Execution boundary; fake and Kubernetes adapters implement this API."""

    adapter_version: str

    def environment_provenance(self) -> Mapping[str, Any]: ...

    def run_trial(self, spec: TrialSpec) -> TrialObservation: ...


@dataclass(frozen=True, slots=True)
class SafeEnvelope:
    family_id: str
    workload_instance_id: str
    workload_fingerprint: str
    status: str
    cpu_selected_m: int | None
    memory_selected_mib: int | None
    cpu_minimum_interval: Mapping[str, Any]
    memory_minimum_interval: Mapping[str, Any]
    reference_median_runtime_seconds: float | None
    reference_runtime_relative_spread: float | None
    reference_stability_threshold: float
    reference_stability_rule_version: str
    joint_successes: int
    joint_trials: int
    joint_success_wilson_95: tuple[float | None, float | None]
    manual_review_status: str
    eligible_for_comparison: bool
    reason_codes: tuple[str, ...]
    source_run_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["joint_success_wilson_95"] = list(self.joint_success_wilson_95)
        payload["reason_codes"] = list(self.reason_codes)
        payload["source_run_ids"] = list(self.source_run_ids)
        return payload
