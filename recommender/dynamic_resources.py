"""Policy-bounded resource generation layered on catalog recommendations.

Catalog mode deliberately returns no generated quantities: the existing caller
continues to apply its administrator-owned profile mapping. Dynamic mode is an
opt-in extension that emits validated KubeSpawner CPU, memory, and GPU values.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

import yaml


CATALOG_MODE = "catalog"
DYNAMIC_MODE = "dynamic"
RESOURCE_SELECTION_MODE_ENV_VAR = "RESOURCE_SELECTION_MODE"
SUPPORTED_MODES = frozenset({CATALOG_MODE, DYNAMIC_MODE})
DEFAULT_RESOURCE_POLICY_PATH = Path(__file__).with_name("resource-policy.yaml")
KUBERNETES_MAX_QUANTITY = 2**63 - 1
KUBERNETES_MAX_MEMORY_MIB = KUBERNETES_MAX_QUANTITY // (2**20)


class ResourcePolicyError(ValueError):
    """Base error for invalid policy or rejected generated resources."""


class ResourcePolicyConfigurationError(ResourcePolicyError):
    """The administrator-owned policy is invalid and must not be used."""


class DynamicResourceRejected(ResourcePolicyError):
    """A generated candidate cannot be safely applied under the policy."""


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResourcePolicyConfigurationError(f"{label} must be a mapping")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise ResourcePolicyConfigurationError(f"{label} fields are invalid ({'; '.join(details)})")


def _require_non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResourcePolicyConfigurationError(f"{label} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class QuantityBounds:
    """Inclusive stepped range; alignment is relative to ``minimum``."""

    minimum: int
    maximum: int
    step: int

    @classmethod
    def from_mapping(cls, raw: object, label: str) -> "QuantityBounds":
        values = _require_mapping(raw, label)
        _require_exact_keys(values, {"min", "max", "step"}, label)
        minimum = _require_non_negative_int(values["min"], f"{label}.min")
        maximum = _require_non_negative_int(values["max"], f"{label}.max")
        step = _require_non_negative_int(values["step"], f"{label}.step")
        if maximum < minimum:
            raise ResourcePolicyConfigurationError(f"{label}.max must be >= min")
        if step == 0:
            raise ResourcePolicyConfigurationError(f"{label}.step must be greater than zero")
        return cls(minimum=minimum, maximum=maximum, step=step)

    def contains(self, value: int) -> bool:
        return (
            self.minimum <= value <= self.maximum
            and (value - self.minimum) % self.step == 0
        )

    def align_up(self, target: float, label: str) -> int:
        if not math.isfinite(target):
            raise DynamicResourceRejected(f"{label} target is not finite")
        if target <= self.minimum:
            return self.minimum
        steps = math.ceil((target - self.minimum) / self.step)
        candidate = self.minimum + steps * self.step
        if candidate > self.maximum:
            raise DynamicResourceRejected(f"{label} target exceeds the configured maximum")
        return candidate


@dataclass(frozen=True)
class QuotaCaps:
    """Conservative per-spawn caps or a snapshot of remaining quota headroom."""

    cpu_limit_millicores: int
    memory_limit_mib: int
    gpu_count: int

    @classmethod
    def from_mapping(cls, raw: object, label: str = "dynamic.quota") -> "QuotaCaps":
        values = _require_mapping(raw, label)
        expected = {"cpu_limit_millicores", "memory_limit_mib", "gpu_count"}
        _require_exact_keys(values, expected, label)
        return cls(
            cpu_limit_millicores=_require_non_negative_int(
                values["cpu_limit_millicores"], f"{label}.cpu_limit_millicores"
            ),
            memory_limit_mib=_require_non_negative_int(
                values["memory_limit_mib"], f"{label}.memory_limit_mib"
            ),
            gpu_count=_require_non_negative_int(values["gpu_count"], f"{label}.gpu_count"),
        )


@dataclass(frozen=True)
class DynamicResourceSpec:
    """Normalized resource candidate after generation and validation."""

    cpu_request_millicores: int
    cpu_limit_millicores: int
    memory_request_mib: int
    memory_limit_mib: int
    gpu_count: int = 0
    gpu_resource: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "cpu_request_millicores": self.cpu_request_millicores,
            "cpu_limit_millicores": self.cpu_limit_millicores,
            "memory_request_mib": self.memory_request_mib,
            "memory_limit_mib": self.memory_limit_mib,
            "gpu_count": self.gpu_count,
            "gpu_resource": self.gpu_resource,
        }

    def to_kubespawner_resources(self) -> dict[str, object]:
        """Return values suitable for KubeSpawner resource attributes."""

        values: dict[str, object] = {
            # KubeSpawner 7 models CPU guarantee/limit as Float traits.  The
            # Kubernetes-style ``500m`` strings accepted by pod specs are not
            # valid trait values and fail before a pod is created.
            "cpu_guarantee": self.cpu_request_millicores / 1000,
            "cpu_limit": self.cpu_limit_millicores / 1000,
            # JupyterHub's ByteSpecification accepts byte integers or decimal
            # K/M/G/T suffixes, not Kubernetes binary ``Mi`` strings.  Integers
            # preserve the policy's exact MiB quantities without rounding.
            "mem_guarantee": self.memory_request_mib * 2**20,
            "mem_limit": self.memory_limit_mib * 2**20,
        }
        if self.gpu_count:
            gpu_val = int(self.gpu_count)
            values["extra_resource_guarantees"] = {self.gpu_resource: gpu_val}
            values["extra_resource_limits"] = {self.gpu_resource: gpu_val}
        return values


@dataclass(frozen=True)
class DynamicResourcePolicy:
    policy_version: str
    default_mode: str
    fallback_profile: str
    catalog_profile_allowlist: tuple[str, ...]
    gpu_resource_allowlist: tuple[str, ...]
    gpu_image_allowlist: tuple[str, ...]
    cpu_request: QuantityBounds
    cpu_limit: QuantityBounds
    memory_request: QuantityBounds
    memory_limit: QuantityBounds
    gpu_count: QuantityBounds
    quota: QuotaCaps


@dataclass(frozen=True)
class ResourceDecision:
    """Mode decision returned to preview and pre-spawn adapters."""

    requested_mode: str
    applied_mode: str
    catalog_profile: str
    resources: DynamicResourceSpec | None
    reasons: tuple[str, ...]
    policy_version: str
    fallback_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "requested_mode": self.requested_mode,
            "applied_mode": self.applied_mode,
            "catalog_profile": self.catalog_profile,
            "resources": self.resources.to_dict() if self.resources else None,
            "reasons": list(self.reasons),
            "policy_version": self.policy_version,
            "fallback_reason": self.fallback_reason,
        }


@dataclass(frozen=True)
class ResourceGenerationTrace:
    """JSON-safe audit details for one unchanged resource decision.

    This trace is observational only.  In particular, ``select`` and its
    serialized ``ResourceDecision`` retain their established behavior and
    shape; resource experiments opt in through ``select_with_trace``.
    """

    requested_mode: str
    applied_mode: str
    recommended_profile: str
    catalog_profile: str
    dataset_size_gb: float | None
    input_score: int | float | str | None
    bounded_score: float | None
    formula_targets: Mapping[str, float] | None
    profile_floors: Mapping[str, int] | None
    floor_adjusted_targets: Mapping[str, float] | None
    quantized_resources: Mapping[str, int | str | None] | None
    quantization_deltas: Mapping[str, float] | None
    quantization_policy: Mapping[str, Mapping[str, int]]
    profile_floor_applied: Mapping[str, bool] | None
    policy_clipping_applied: bool
    clipping_semantics: str
    fallback_to_catalog: bool
    fallback_reason: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "requested_mode": self.requested_mode,
            "applied_mode": self.applied_mode,
            "recommended_profile": self.recommended_profile,
            "catalog_profile": self.catalog_profile,
            "dataset_size_gb": self.dataset_size_gb,
            "input_score": self.input_score,
            "bounded_score": self.bounded_score,
            "formula_targets": dict(self.formula_targets) if self.formula_targets else None,
            "profile_floors": dict(self.profile_floors) if self.profile_floors else None,
            "floor_adjusted_targets": (
                dict(self.floor_adjusted_targets) if self.floor_adjusted_targets else None
            ),
            "quantized_resources": (
                dict(self.quantized_resources) if self.quantized_resources else None
            ),
            "quantization_deltas": (
                dict(self.quantization_deltas) if self.quantization_deltas else None
            ),
            "quantization_policy": {
                key: dict(value) for key, value in self.quantization_policy.items()
            },
            "profile_floor_applied": (
                dict(self.profile_floor_applied) if self.profile_floor_applied else None
            ),
            "policy_clipping_applied": self.policy_clipping_applied,
            "clipping_semantics": self.clipping_semantics,
            "fallback_to_catalog": self.fallback_to_catalog,
            "fallback_reason": self.fallback_reason,
        }


def validate_resource_policy(raw: object) -> DynamicResourcePolicy:
    """Strictly validate administrator configuration without silent defaults."""

    root = _require_mapping(raw, "resource policy")
    _require_exact_keys(
        root,
        {
            "policy_version",
            "default_mode",
            "fallback_profile",
            "allowlist",
            "dynamic",
        },
        "resource policy",
    )
    policy_version = root["policy_version"]
    if not isinstance(policy_version, str) or not policy_version.strip():
        raise ResourcePolicyConfigurationError("policy_version must be a non-empty string")
    default_mode = root["default_mode"]
    if default_mode not in SUPPORTED_MODES:
        raise ResourcePolicyConfigurationError("default_mode must be catalog or dynamic")

    allowlist = _require_mapping(root["allowlist"], "allowlist")
    _require_exact_keys(
        allowlist,
        {"catalog_profiles", "gpu_resources", "gpu_images"},
        "allowlist",
    )
    profiles = allowlist["catalog_profiles"]
    gpu_resources = allowlist["gpu_resources"]
    gpu_images = allowlist["gpu_images"]
    if not isinstance(profiles, list) or not profiles or not all(
        isinstance(value, str) and value.strip() for value in profiles
    ):
        raise ResourcePolicyConfigurationError("allowlist.catalog_profiles must be non-empty strings")
    if len(set(profiles)) != len(profiles):
        raise ResourcePolicyConfigurationError("allowlist.catalog_profiles must not contain duplicates")
    if not isinstance(gpu_resources, list) or not all(
        isinstance(value, str) and value.strip() for value in gpu_resources
    ):
        raise ResourcePolicyConfigurationError("allowlist.gpu_resources must contain strings")
    if len(set(gpu_resources)) != len(gpu_resources):
        raise ResourcePolicyConfigurationError("allowlist.gpu_resources must not contain duplicates")
    if not isinstance(gpu_images, list) or not all(
        isinstance(value, str) and value.strip() for value in gpu_images
    ):
        raise ResourcePolicyConfigurationError("allowlist.gpu_images must contain strings")
    if len(set(gpu_images)) != len(gpu_images):
        raise ResourcePolicyConfigurationError("allowlist.gpu_images must not contain duplicates")
    fallback_profile = root["fallback_profile"]
    if fallback_profile not in profiles:
        raise ResourcePolicyConfigurationError("fallback_profile must be catalog-allowlisted")

    dynamic = _require_mapping(root["dynamic"], "dynamic")
    _require_exact_keys(dynamic, {"cpu_millicores", "memory_mib", "gpu_count", "quota"}, "dynamic")
    cpu = _require_mapping(dynamic["cpu_millicores"], "dynamic.cpu_millicores")
    memory = _require_mapping(dynamic["memory_mib"], "dynamic.memory_mib")
    _require_exact_keys(cpu, {"request", "limit"}, "dynamic.cpu_millicores")
    _require_exact_keys(memory, {"request", "limit"}, "dynamic.memory_mib")

    cpu_request = QuantityBounds.from_mapping(cpu["request"], "dynamic.cpu_millicores.request")
    cpu_limit = QuantityBounds.from_mapping(cpu["limit"], "dynamic.cpu_millicores.limit")
    memory_request = QuantityBounds.from_mapping(memory["request"], "dynamic.memory_mib.request")
    memory_limit = QuantityBounds.from_mapping(memory["limit"], "dynamic.memory_mib.limit")
    gpu_count = QuantityBounds.from_mapping(dynamic["gpu_count"], "dynamic.gpu_count")
    quota = QuotaCaps.from_mapping(dynamic["quota"])

    if cpu_request.minimum > cpu_limit.maximum:
        raise ResourcePolicyConfigurationError("CPU request range cannot fit below the CPU limit range")
    if memory_request.minimum > memory_limit.maximum:
        raise ResourcePolicyConfigurationError("memory request range cannot fit below the memory limit range")
    if quota.cpu_limit_millicores < cpu_limit.minimum:
        raise ResourcePolicyConfigurationError("CPU quota cannot fit the minimum CPU limit")
    if quota.memory_limit_mib < memory_limit.minimum:
        raise ResourcePolicyConfigurationError("memory quota cannot fit the minimum memory limit")
    if quota.gpu_count < gpu_count.minimum:
        raise ResourcePolicyConfigurationError("GPU quota cannot fit the minimum GPU count")
    if gpu_count.maximum > 0 and not gpu_resources:
        raise ResourcePolicyConfigurationError(
            "allowlist.gpu_resources is required when dynamic GPU count can exceed zero"
        )
    if gpu_count.maximum > 0 and not gpu_images:
        raise ResourcePolicyConfigurationError(
            "allowlist.gpu_images is required when dynamic GPU count can exceed zero"
        )
    quantity_limits = (
        (cpu_request.maximum, KUBERNETES_MAX_QUANTITY, "CPU request max"),
        (cpu_limit.maximum, KUBERNETES_MAX_QUANTITY, "CPU limit max"),
        (memory_request.maximum, KUBERNETES_MAX_MEMORY_MIB, "memory request max"),
        (memory_limit.maximum, KUBERNETES_MAX_MEMORY_MIB, "memory limit max"),
        (gpu_count.maximum, KUBERNETES_MAX_QUANTITY, "GPU count max"),
        (quota.cpu_limit_millicores, KUBERNETES_MAX_QUANTITY, "CPU quota cap"),
        (quota.memory_limit_mib, KUBERNETES_MAX_MEMORY_MIB, "memory quota cap"),
        (quota.gpu_count, KUBERNETES_MAX_QUANTITY, "GPU quota cap"),
    )
    for value, maximum, label in quantity_limits:
        if value > maximum:
            raise ResourcePolicyConfigurationError(
                f"{label} exceeds the Kubernetes quantity limit"
            )

    return DynamicResourcePolicy(
        policy_version=policy_version,
        default_mode=default_mode,
        fallback_profile=fallback_profile,
        catalog_profile_allowlist=tuple(profiles),
        gpu_resource_allowlist=tuple(gpu_resources),
        gpu_image_allowlist=tuple(gpu_images),
        cpu_request=cpu_request,
        cpu_limit=cpu_limit,
        memory_request=memory_request,
        memory_limit=memory_limit,
        gpu_count=gpu_count,
        quota=quota,
    )


def load_resource_policy(path: str | Path = DEFAULT_RESOURCE_POLICY_PATH) -> DynamicResourcePolicy:
    with Path(path).open(encoding="utf-8") as handle:
        return validate_resource_policy(yaml.safe_load(handle))


def resource_policy_hash(policy: DynamicResourcePolicy) -> str:
    """Return a semantic hash so previews bind to policy content, not a label."""

    payload = {
        "policy_version": policy.policy_version,
        "default_mode": policy.default_mode,
        "fallback_profile": policy.fallback_profile,
        "allowlist": {
            "catalog_profiles": list(policy.catalog_profile_allowlist),
            "gpu_resources": list(policy.gpu_resource_allowlist),
            "gpu_images": list(policy.gpu_image_allowlist),
        },
        "dynamic": {
            "cpu_request": vars(policy.cpu_request),
            "cpu_limit": vars(policy.cpu_limit),
            "memory_request": vars(policy.memory_request),
            "memory_limit": vars(policy.memory_limit),
            "gpu_count": vars(policy.gpu_count),
            "quota": vars(policy.quota),
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def configured_resource_mode(
    mode: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    default: str = CATALOG_MODE,
) -> str:
    """Resolve the explicit/env/default mode and reject unknown values."""

    source = os.environ if environ is None else environ
    selected = mode if mode is not None else source.get(RESOURCE_SELECTION_MODE_ENV_VAR, default)
    if not isinstance(selected, str) or selected.strip().lower() not in SUPPORTED_MODES:
        raise ResourcePolicyConfigurationError(
            f"{RESOURCE_SELECTION_MODE_ENV_VAR} must be catalog or dynamic"
        )
    return selected.strip().lower()


class ResourceSelector:
    """Select unchanged catalog behavior or an opt-in validated dynamic spec."""

    _PROFILE_ALIASES = {"gpu_or_large": "large"}
    _PROFILE_FLOORS = {
        "small": (100, 500, 256, 384),
        "medium": (300, 700, 512, 768),
        "large": (800, 1200, 1024, 1280),
        "gpu_or_large": (800, 1200, 1024, 1280),
    }

    def __init__(
        self,
        policy: DynamicResourcePolicy,
        *,
        mode: str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.policy = policy
        self.mode = configured_resource_mode(
            mode,
            environ=environ,
            default=policy.default_mode,
        )

    def _catalog_profile(self, recommended_profile: str) -> tuple[str, str | None]:
        selected = self._PROFILE_ALIASES.get(recommended_profile, recommended_profile)
        if selected in self.policy.catalog_profile_allowlist:
            return selected, None
        return (
            self.policy.fallback_profile,
            f"recommended profile {recommended_profile!r} is not catalog-allowlisted",
        )

    @staticmethod
    def _coerce_dataset_size_gb(value: float | int | str | None) -> float:
        if value in (None, ""):
            return 0.0
        if isinstance(value, bool):
            raise DynamicResourceRejected("dataset size must be a finite non-negative number")
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise DynamicResourceRejected(
                "dataset size must be a finite non-negative number"
            ) from None
        if not math.isfinite(parsed) or parsed < 0:
            raise DynamicResourceRejected("dataset size must be a finite non-negative number")
        return parsed

    @staticmethod
    def _coerce_score(value: object) -> float:
        if value in (None, ""):
            return 0.0
        if isinstance(value, bool):
            raise DynamicResourceRejected("score must be a finite non-negative number")
        try:
            score = float(value)
        except (TypeError, ValueError):
            raise DynamicResourceRejected("score must be a finite non-negative number") from None
        if not math.isfinite(score) or score < 0:
            raise DynamicResourceRejected("score must be a finite non-negative number")
        return min(score, 10.0)

    def validate_dynamic_resources(
        self,
        resources: DynamicResourceSpec,
        *,
        quota_headroom: QuotaCaps | None = None,
    ) -> DynamicResourceSpec:
        """Validate a generated or externally supplied spec against all bounds."""

        checks = (
            ("CPU request", resources.cpu_request_millicores, self.policy.cpu_request),
            ("CPU limit", resources.cpu_limit_millicores, self.policy.cpu_limit),
            ("memory request", resources.memory_request_mib, self.policy.memory_request),
            ("memory limit", resources.memory_limit_mib, self.policy.memory_limit),
            ("GPU count", resources.gpu_count, self.policy.gpu_count),
        )
        for label, value, bounds in checks:
            if isinstance(value, bool) or not isinstance(value, int) or not bounds.contains(value):
                raise DynamicResourceRejected(f"{label} is outside its min/max/step policy")
        if resources.cpu_request_millicores > resources.cpu_limit_millicores:
            raise DynamicResourceRejected("CPU request exceeds CPU limit")
        if resources.memory_request_mib > resources.memory_limit_mib:
            raise DynamicResourceRejected("memory request exceeds memory limit")
        if resources.gpu_count:
            if resources.gpu_resource not in self.policy.gpu_resource_allowlist:
                raise DynamicResourceRejected("GPU resource is not allowlisted")
        elif resources.gpu_resource is not None:
            raise DynamicResourceRejected("GPU resource must be null when GPU count is zero")

        caps = [self.policy.quota]
        if quota_headroom is not None:
            caps.append(quota_headroom)
        for cap in caps:
            if resources.cpu_limit_millicores > cap.cpu_limit_millicores:
                raise DynamicResourceRejected("CPU limit exceeds available quota")
            if resources.memory_limit_mib > cap.memory_limit_mib:
                raise DynamicResourceRejected("memory limit exceeds available quota")
            if resources.gpu_count > cap.gpu_count:
                raise DynamicResourceRejected("GPU count exceeds available quota")
        return resources

    def _generate_candidate(
        self,
        recommended_profile: str,
        score: object,
        dataset_size_gb: float | int | str | None,
    ) -> DynamicResourceSpec:
        floors = self._PROFILE_FLOORS.get(recommended_profile)
        if floors is None:
            raise DynamicResourceRejected("dynamic generation requires a recognized recommendation profile")

        dataset = self._coerce_dataset_size_gb(dataset_size_gb)
        bounded_score = self._coerce_score(score)
        cpu_request_target = max(floors[0], 100 + dataset * 200 + bounded_score * 100)
        memory_request_target = max(floors[2], 256 + dataset * 384 + bounded_score * 96)

        cpu_request = self.policy.cpu_request.align_up(cpu_request_target, "CPU request")
        memory_request = self.policy.memory_request.align_up(memory_request_target, "memory request")
        cpu_limit = self.policy.cpu_limit.align_up(
            max(floors[1], cpu_request + 400), "CPU limit"
        )
        memory_limit = self.policy.memory_limit.align_up(
            max(floors[3], memory_request + 256), "memory limit"
        )

        gpu_count = 1 if recommended_profile == "gpu_or_large" else 0
        gpu_resource = (
            self.policy.gpu_resource_allowlist[0]
            if gpu_count and self.policy.gpu_resource_allowlist
            else None
        )
        return DynamicResourceSpec(
            cpu_request_millicores=cpu_request,
            cpu_limit_millicores=cpu_limit,
            memory_request_mib=memory_request,
            memory_limit_mib=memory_limit,
            gpu_count=gpu_count,
            gpu_resource=gpu_resource,
        )

    def _generate(
        self,
        recommended_profile: str,
        score: object,
        dataset_size_gb: float | int | str | None,
        quota_headroom: QuotaCaps | None,
    ) -> DynamicResourceSpec:
        candidate = self._generate_candidate(recommended_profile, score, dataset_size_gb)
        return self.validate_dynamic_resources(candidate, quota_headroom=quota_headroom)

    def _quantization_policy(self) -> dict[str, dict[str, int]]:
        return {
            "cpu_request_millicores": vars(self.policy.cpu_request),
            "cpu_limit_millicores": vars(self.policy.cpu_limit),
            "memory_request_mib": vars(self.policy.memory_request),
            "memory_limit_mib": vars(self.policy.memory_limit),
            "gpu_count": vars(self.policy.gpu_count),
        }

    @staticmethod
    def _trace_input(value: object) -> int | float | str | None:
        if value is None or isinstance(value, (int, str)) and not isinstance(value, bool):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else repr(value)
        if isinstance(value, bool):
            return str(value).lower()
        return type(value).__name__

    def select_with_trace(
        self,
        *,
        recommended_profile: str,
        score: object = None,
        dataset_size_gb: float | int | str | None = 0.0,
        mode: str | None = None,
        quota_headroom: QuotaCaps | None = None,
    ) -> tuple[ResourceDecision, ResourceGenerationTrace]:
        """Return the normal decision plus non-applying generation telemetry."""

        requested_mode = configured_resource_mode(
            mode if mode is not None else self.mode,
            environ={},
            default=self.policy.default_mode,
        )
        catalog_profile, catalog_warning = self._catalog_profile(recommended_profile)
        trace_common = {
            "requested_mode": requested_mode,
            "recommended_profile": recommended_profile,
            "catalog_profile": catalog_profile,
            "input_score": self._trace_input(score),
            "quantization_policy": self._quantization_policy(),
            "policy_clipping_applied": False,
            "clipping_semantics": (
                "the frozen selector never clips; a target beyond policy or quota "
                "bounds is rejected and falls back to catalog"
            ),
        }
        if requested_mode == CATALOG_MODE:
            reasons = ["Catalog Mode preserves the administrator-owned profile mapping."]
            if catalog_warning:
                reasons.append(catalog_warning)
            decision = ResourceDecision(
                requested_mode=requested_mode,
                applied_mode=CATALOG_MODE,
                catalog_profile=catalog_profile,
                resources=None,
                reasons=tuple(reasons),
                policy_version=self.policy.policy_version,
                fallback_reason=catalog_warning,
            )
            return decision, ResourceGenerationTrace(
                **trace_common,
                applied_mode=CATALOG_MODE,
                dataset_size_gb=None,
                bounded_score=None,
                formula_targets=None,
                profile_floors=None,
                floor_adjusted_targets=None,
                quantized_resources=None,
                quantization_deltas=None,
                profile_floor_applied=None,
                fallback_to_catalog=False,
                fallback_reason=catalog_warning,
            )

        dataset: float | None = None
        bounded_score: float | None = None
        formula_targets: dict[str, float] | None = None
        floors_payload: dict[str, int] | None = None
        adjusted_targets: dict[str, float] | None = None
        floor_applied: dict[str, bool] | None = None
        quantized_candidate: DynamicResourceSpec | None = None
        quantization_deltas: dict[str, float] | None = None
        try:
            floors = self._PROFILE_FLOORS.get(recommended_profile)
            if floors is None:
                raise DynamicResourceRejected(
                    "dynamic generation requires a recognized recommendation profile"
                )
            dataset = self._coerce_dataset_size_gb(dataset_size_gb)
            bounded_score = self._coerce_score(score)
            formula_targets = {
                "cpu_request_millicores": 100 + dataset * 200 + bounded_score * 100,
                "memory_request_mib": 256 + dataset * 384 + bounded_score * 96,
            }
            floors_payload = {
                "cpu_request_millicores": floors[0],
                "cpu_limit_millicores": floors[1],
                "memory_request_mib": floors[2],
                "memory_limit_mib": floors[3],
            }
            cpu_request_target = max(floors[0], formula_targets["cpu_request_millicores"])
            memory_request_target = max(floors[2], formula_targets["memory_request_mib"])
            adjusted_targets = {
                "cpu_request_millicores": cpu_request_target,
                "memory_request_mib": memory_request_target,
            }
            floor_applied = {
                "cpu_request_millicores": cpu_request_target > formula_targets["cpu_request_millicores"],
                "memory_request_mib": memory_request_target > formula_targets["memory_request_mib"],
            }
            cpu_request = self.policy.cpu_request.align_up(cpu_request_target, "CPU request")
            memory_request = self.policy.memory_request.align_up(memory_request_target, "memory request")
            cpu_limit = self.policy.cpu_limit.align_up(
                max(floors[1], cpu_request + 400), "CPU limit"
            )
            memory_limit = self.policy.memory_limit.align_up(
                max(floors[3], memory_request + 256), "memory limit"
            )
            gpu_count = 1 if recommended_profile == "gpu_or_large" else 0
            gpu_resource = (
                self.policy.gpu_resource_allowlist[0]
                if gpu_count and self.policy.gpu_resource_allowlist
                else None
            )
            quantized_candidate = DynamicResourceSpec(
                cpu_request_millicores=cpu_request,
                cpu_limit_millicores=cpu_limit,
                memory_request_mib=memory_request,
                memory_limit_mib=memory_limit,
                gpu_count=gpu_count,
                gpu_resource=gpu_resource,
            )
            limit_targets = {
                "cpu_limit_millicores": max(floors_payload["cpu_limit_millicores"], quantized_candidate.cpu_request_millicores + 400),
                "memory_limit_mib": max(floors_payload["memory_limit_mib"], quantized_candidate.memory_request_mib + 256),
            }
            adjusted_targets = {**adjusted_targets, **limit_targets}
            floor_applied = {
                **floor_applied,
                "cpu_limit_millicores": floors_payload["cpu_limit_millicores"] > quantized_candidate.cpu_request_millicores + 400,
                "memory_limit_mib": floors_payload["memory_limit_mib"] > quantized_candidate.memory_request_mib + 256,
            }
            quantized_payload = quantized_candidate.to_dict()
            quantization_deltas = {
                key: float(quantized_payload[key]) - target
                for key, target in adjusted_targets.items()
            }
            resources = self.validate_dynamic_resources(
                quantized_candidate, quota_headroom=quota_headroom
            )
        except DynamicResourceRejected as exc:
            fallback_reason = str(exc)
            decision = ResourceDecision(
                requested_mode=requested_mode,
                applied_mode=CATALOG_MODE,
                catalog_profile=catalog_profile,
                resources=None,
                reasons=(
                    "Dynamic candidate was rejected; Catalog Mode was applied.",
                    fallback_reason,
                ),
                policy_version=self.policy.policy_version,
                fallback_reason=fallback_reason,
            )
            return decision, ResourceGenerationTrace(
                **trace_common,
                applied_mode=CATALOG_MODE,
                dataset_size_gb=dataset,
                bounded_score=bounded_score,
                formula_targets=formula_targets,
                profile_floors=floors_payload,
                floor_adjusted_targets=adjusted_targets,
                quantized_resources=(
                    quantized_candidate.to_dict() if quantized_candidate else None
                ),
                quantization_deltas=quantization_deltas,
                profile_floor_applied=floor_applied,
                fallback_to_catalog=True,
                fallback_reason=fallback_reason,
            )

        decision = ResourceDecision(
            requested_mode=requested_mode,
            applied_mode=DYNAMIC_MODE,
            catalog_profile=catalog_profile,
            resources=resources,
            reasons=(
                "Dynamic resources were generated deterministically from bounded workload signals.",
                "CPU, memory, GPU, quota, step, and allowlist validation passed.",
            ),
            policy_version=self.policy.policy_version,
        )
        assert adjusted_targets is not None and quantization_deltas is not None
        quantized = resources.to_dict()
        return decision, ResourceGenerationTrace(
            **trace_common,
            applied_mode=DYNAMIC_MODE,
            dataset_size_gb=dataset,
            bounded_score=bounded_score,
            formula_targets=formula_targets,
            profile_floors=floors_payload,
            floor_adjusted_targets=adjusted_targets,
            quantized_resources=quantized,
            quantization_deltas=quantization_deltas,
            profile_floor_applied=floor_applied,
            fallback_to_catalog=False,
            fallback_reason=None,
        )

    def select(
        self,
        *,
        recommended_profile: str,
        score: object = None,
        dataset_size_gb: float | int | str | None = 0.0,
        mode: str | None = None,
        quota_headroom: QuotaCaps | None = None,
    ) -> ResourceDecision:
        decision, _trace = self.select_with_trace(
            recommended_profile=recommended_profile,
            score=score,
            dataset_size_gb=dataset_size_gb,
            mode=mode,
            quota_headroom=quota_headroom,
        )
        return decision


__all__ = [
    "CATALOG_MODE",
    "DEFAULT_RESOURCE_POLICY_PATH",
    "DYNAMIC_MODE",
    "DynamicResourcePolicy",
    "DynamicResourceRejected",
    "DynamicResourceSpec",
    "QuantityBounds",
    "QuotaCaps",
    "RESOURCE_SELECTION_MODE_ENV_VAR",
    "ResourceDecision",
    "ResourceGenerationTrace",
    "ResourcePolicyConfigurationError",
    "ResourcePolicyError",
    "ResourceSelector",
    "configured_resource_mode",
    "load_resource_policy",
    "resource_policy_hash",
    "validate_resource_policy",
]
