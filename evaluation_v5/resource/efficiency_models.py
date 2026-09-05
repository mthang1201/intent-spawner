"""Typed plan and raw-observation records for E4 resource efficiency."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


PLAN_SCHEMA_VERSION = "protocol-v5-resource-efficiency-plan-v1.0.0"
DECISION_SCHEMA_VERSION = "protocol-v5-resource-efficiency-decision-v1.0.0"
TRIAL_SCHEMA_VERSION = "protocol-v5-resource-efficiency-trial-v1.0.0"
OUTCOMES = ("PENDING_OR_ADMISSION_FAILURE", "OOM", "TIMEOUT", "INCORRECT", "RUNTIME_ERROR", "SUCCESS")
OUTCOME_PRECEDENCE = (
    "INFRASTRUCTURE_INVALID", "PENDING_OR_ADMISSION_FAILURE", "OOM",
    "TIMEOUT", "INCORRECT", "RUNTIME_ERROR", "SUCCESS",
)


@dataclass(frozen=True, slots=True)
class ResourceAllocation:
    cpu_request_m: int
    cpu_limit_m: int
    memory_request_mib: int
    memory_limit_mib: int
    gpu_count: int = 0
    gpu_resource: str | None = None

    def __post_init__(self) -> None:
        values = (self.cpu_request_m, self.cpu_limit_m, self.memory_request_mib, self.memory_limit_mib, self.gpu_count)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("resource quantities must be non-negative integers")
        if self.cpu_request_m <= 0 or self.memory_request_mib <= 0 or self.cpu_request_m > self.cpu_limit_m or self.memory_request_mib > self.memory_limit_mib:
            raise ValueError("resource requests must be positive and not exceed limits")
        if bool(self.gpu_count) != bool(self.gpu_resource):
            raise ValueError("GPU resource identity/count mismatch")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResourceAllocation":
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class EfficiencyTrialSpec:
    plan_index: int
    trial_id: str
    primary_trial_id: str
    family_id: str
    workload_instance_id: str
    workload_fingerprint: str
    condition: str
    repetition: int
    deterministic_seed: int
    timeout_seconds: int
    expected_marker_sha256: str
    allocation: ResourceAllocation
    replacement_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["schema_version"] = TRIAL_SCHEMA_VERSION
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EfficiencyTrialSpec":
        payload = dict(value)
        if payload.pop("schema_version", TRIAL_SCHEMA_VERSION) != TRIAL_SCHEMA_VERSION:
            raise ValueError("unsupported comparative trial schema")
        payload["allocation"] = ResourceAllocation.from_dict(payload["allocation"])
        return cls(**payload)


def primary_outcome(row: Mapping[str, Any]) -> str:
    if row.get("infrastructure_invalid"):
        return "INFRASTRUCTURE_INVALID"
    if row.get("pending_or_admission_failure"):
        return "PENDING_OR_ADMISSION_FAILURE"
    if row.get("oom"):
        return "OOM"
    if row.get("timeout"):
        return "TIMEOUT"
    if row.get("correctness") is False:
        return "INCORRECT"
    if row.get("runtime_error") or row.get("success") is not True:
        return "RUNTIME_ERROR"
    return "SUCCESS"


__all__ = ["DECISION_SCHEMA_VERSION", "EfficiencyTrialSpec", "OUTCOMES", "OUTCOME_PRECEDENCE", "PLAN_SCHEMA_VERSION", "ResourceAllocation", "TRIAL_SCHEMA_VERSION", "primary_outcome"]
