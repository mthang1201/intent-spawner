from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from recommender.dynamic_resources import (
    CATALOG_MODE,
    DYNAMIC_MODE,
    DynamicResourceRejected,
    DynamicResourceSpec,
    QuotaCaps,
    ResourcePolicyConfigurationError,
    ResourceSelector,
    configured_resource_mode,
    load_resource_policy,
    resource_policy_hash,
    validate_resource_policy,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "recommender" / "resource-policy.yaml"


def raw_policy() -> dict:
    with POLICY_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def gpu_enabled_policy():
    raw = deepcopy(raw_policy())
    raw["allowlist"]["gpu_resources"] = ["nvidia.com/gpu"]
    raw["allowlist"]["gpu_images"] = ["pytorch-deep-learning"]
    raw["dynamic"]["gpu_count"]["max"] = 1
    raw["dynamic"]["quota"]["gpu_count"] = 1
    return validate_resource_policy(raw)


def test_default_policy_keeps_catalog_mode_and_emits_no_dynamic_values():
    policy = load_resource_policy()
    selector = ResourceSelector(policy, environ={})

    decision = selector.select(
        recommended_profile="medium",
        score=2,
        dataset_size_gb=0.8,
    )

    assert policy.default_mode == CATALOG_MODE
    assert decision.requested_mode == CATALOG_MODE
    assert decision.applied_mode == CATALOG_MODE
    assert decision.catalog_profile == "medium"
    assert decision.resources is None
    assert decision.fallback_reason is None


def test_dynamic_mode_generates_aligned_values_inside_every_policy_bound():
    policy = load_resource_policy()
    selector = ResourceSelector(policy, mode=DYNAMIC_MODE)

    decision = selector.select(
        recommended_profile="medium",
        score=2,
        dataset_size_gb=0.8,
    )

    assert decision.applied_mode == DYNAMIC_MODE
    assert decision.resources is not None
    resources = decision.resources
    assert policy.cpu_request.contains(resources.cpu_request_millicores)
    assert policy.cpu_limit.contains(resources.cpu_limit_millicores)
    assert policy.memory_request.contains(resources.memory_request_mib)
    assert policy.memory_limit.contains(resources.memory_limit_mib)
    assert policy.gpu_count.contains(resources.gpu_count)
    assert resources.cpu_request_millicores <= resources.cpu_limit_millicores
    assert resources.memory_request_mib <= resources.memory_limit_mib
    assert resources.cpu_limit_millicores <= policy.quota.cpu_limit_millicores
    assert resources.memory_limit_mib <= policy.quota.memory_limit_mib
    assert resources.gpu_count == 0
    assert resources.to_kubespawner_resources() == {
        "cpu_guarantee": 0.5,
        "cpu_limit": 0.9,
        "mem_guarantee": 768 * 2**20,
        "mem_limit": 1024 * 2**20,
    }


def test_dynamic_mode_can_be_selected_by_the_environment():
    selector = ResourceSelector(
        load_resource_policy(),
        environ={"RESOURCE_SELECTION_MODE": " DYNAMIC "},
    )

    assert selector.mode == DYNAMIC_MODE
    assert selector.select(recommended_profile="small").applied_mode == DYNAMIC_MODE


@pytest.mark.parametrize("mode", ["", "adaptive", "catalogue", "dynamic-now"])
def test_unknown_mode_fails_closed(mode):
    with pytest.raises(ResourcePolicyConfigurationError, match="must be catalog or dynamic"):
        configured_resource_mode(mode, environ={})


def test_gpu_signal_falls_back_to_catalog_large_when_gpu_is_not_permitted():
    selector = ResourceSelector(load_resource_policy(), mode=DYNAMIC_MODE)

    decision = selector.select(
        recommended_profile="gpu_or_large",
        score=99,
        dataset_size_gb=0.2,
    )

    assert decision.requested_mode == DYNAMIC_MODE
    assert decision.applied_mode == CATALOG_MODE
    assert decision.catalog_profile == "large"
    assert decision.resources is None
    assert decision.fallback_reason == "GPU count is outside its min/max/step policy"


def test_gpu_is_generated_only_with_nonzero_bounds_quota_and_allowlisted_resource():
    selector = ResourceSelector(gpu_enabled_policy(), mode=DYNAMIC_MODE)

    decision = selector.select(
        recommended_profile="gpu_or_large",
        score=99,
        dataset_size_gb=0.2,
    )

    assert decision.applied_mode == DYNAMIC_MODE
    assert decision.resources is not None
    assert decision.resources.gpu_count == 1
    assert decision.resources.gpu_resource == "nvidia.com/gpu"
    assert decision.resources.to_kubespawner_resources()["extra_resource_limits"] == {
        "nvidia.com/gpu": 1
    }


def test_runtime_quota_headroom_rejection_falls_back_to_catalog():
    selector = ResourceSelector(load_resource_policy(), mode=DYNAMIC_MODE)

    decision = selector.select(
        recommended_profile="medium",
        score=2,
        dataset_size_gb=0.8,
        quota_headroom=QuotaCaps(
            cpu_limit_millicores=800,
            memory_limit_mib=2048,
            gpu_count=0,
        ),
    )

    assert decision.applied_mode == CATALOG_MODE
    assert decision.catalog_profile == "medium"
    assert decision.fallback_reason == "CPU limit exceeds available quota"


def test_oversized_target_falls_back_instead_of_clamping_to_policy_maximum():
    selector = ResourceSelector(load_resource_policy(), mode=DYNAMIC_MODE)

    decision = selector.select(
        recommended_profile="large",
        score=3,
        dataset_size_gb=10,
    )

    assert decision.applied_mode == CATALOG_MODE
    assert decision.fallback_reason == "CPU request target exceeds the configured maximum"


def test_candidate_validator_rejects_off_step_and_unallowlisted_gpu_values():
    selector = ResourceSelector(gpu_enabled_policy(), mode=DYNAMIC_MODE)

    with pytest.raises(DynamicResourceRejected, match="CPU request"):
        selector.validate_dynamic_resources(
            DynamicResourceSpec(
                cpu_request_millicores=101,
                cpu_limit_millicores=500,
                memory_request_mib=256,
                memory_limit_mib=384,
            )
        )

    with pytest.raises(DynamicResourceRejected, match="not allowlisted"):
        selector.validate_dynamic_resources(
            DynamicResourceSpec(
                cpu_request_millicores=100,
                cpu_limit_millicores=500,
                memory_request_mib=256,
                memory_limit_mib=384,
                gpu_count=1,
                gpu_resource="example.com/gpu",
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cpu_request_millicores", -100),
        ("cpu_request_millicores", 0),
        ("cpu_request_millicores", 100.0),
        ("cpu_request_millicores", "100m"),
        ("cpu_request_millicores", 101),
        ("cpu_limit_millicores", 10**100),
        ("memory_request_mib", "256Mi"),
        ("memory_limit_mib", 384.5),
        ("gpu_count", -1),
        ("gpu_count", 2),
    ],
)
def test_candidate_validator_rejects_malformed_negative_off_step_and_huge_values(
    field, value
):
    selector = ResourceSelector(gpu_enabled_policy(), mode=DYNAMIC_MODE)
    values = {
        "cpu_request_millicores": 100,
        "cpu_limit_millicores": 500,
        "memory_request_mib": 256,
        "memory_limit_mib": 384,
        "gpu_count": 0,
    }
    values[field] = value

    with pytest.raises(DynamicResourceRejected):
        selector.validate_dynamic_resources(DynamicResourceSpec(**values))


@pytest.mark.parametrize("value", ["bad", -1, float("nan"), float("inf"), "1e309"])
def test_invalid_dataset_signals_fail_to_visible_catalog_fallback(value):
    decision = ResourceSelector(load_resource_policy(), mode=DYNAMIC_MODE).select(
        recommended_profile="small",
        dataset_size_gb=value,
    )

    assert decision.applied_mode == CATALOG_MODE
    assert decision.fallback_reason == "dataset size must be a finite non-negative number"


def test_rounding_boundaries_are_deterministic_and_always_round_up():
    selector = ResourceSelector(load_resource_policy(), mode=DYNAMIC_MODE)

    below = selector.select(recommended_profile="small", dataset_size_gb="0.49999999999999994")
    exact = selector.select(recommended_profile="small", dataset_size_gb="0.5")
    above = selector.select(recommended_profile="small", dataset_size_gb="0.5000000000000001")

    assert below.resources.cpu_request_millicores == 200
    assert exact.resources.cpu_request_millicores == 200
    assert above.resources.cpu_request_millicores == 300


def test_overflow_like_policy_fails_at_configuration_not_resource_conversion():
    policy = raw_policy()
    policy["dynamic"]["cpu_millicores"]["limit"]["max"] = 10**100

    with pytest.raises(ResourcePolicyConfigurationError, match="Kubernetes quantity limit"):
        validate_resource_policy(policy)


def test_semantic_policy_hash_changes_even_when_version_label_is_reused():
    first = raw_policy()
    second = deepcopy(first)
    second["dynamic"]["cpu_millicores"]["request"]["max"] -= 100

    assert resource_policy_hash(validate_resource_policy(first)) != resource_policy_hash(
        validate_resource_policy(second)
    )

@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda policy: policy["dynamic"]["cpu_millicores"]["request"].update(step=0),
            "step must be greater than zero",
        ),
        (
            lambda policy: policy["dynamic"]["memory_mib"]["limit"].update(min=True),
            "must be a non-negative integer",
        ),
        (
            lambda policy: policy["allowlist"].update(catalog_profiles=["small", "small"]),
            "must not contain duplicates",
        ),
        (
            lambda policy: policy["dynamic"].update(typo={}),
            "fields are invalid",
        ),
        (
            lambda policy: policy["dynamic"]["gpu_count"].update(max=1),
            "gpu_resources is required",
        ),
        (
            lambda policy: (
                policy["allowlist"].update(gpu_resources=["nvidia.com/gpu"]),
                policy["dynamic"]["gpu_count"].update(max=1),
            ),
            "gpu_images is required",
        ),
    ],
)
def test_invalid_admin_policy_fails_fast(mutate, message):
    policy = raw_policy()
    mutate(policy)

    with pytest.raises(ResourcePolicyConfigurationError, match=message):
        validate_resource_policy(policy)


def test_missing_and_malformed_policy_fail_when_loaded(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_resource_policy(tmp_path / "missing-policy.yaml")

    malformed = tmp_path / "malformed-policy.yaml"
    malformed.write_text("dynamic: [not, a, policy]\n", encoding="utf-8")
    with pytest.raises(ResourcePolicyConfigurationError, match="fields are invalid"):
        load_resource_policy(malformed)


def test_unknown_recommendation_uses_admin_fallback_profile():
    selector = ResourceSelector(load_resource_policy(), mode=CATALOG_MODE)

    decision = selector.select(recommended_profile="backend-invented-profile")

    assert decision.catalog_profile == "small"
    assert decision.fallback_reason is not None


def test_preview_cli_emits_both_recommendation_and_resource_decision_json():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/preview-resource-decision.py",
            "--mode",
            "dynamic",
            "--intent",
            "explore a CSV file",
            "--dataset-gb",
            "0.8",
            "--code-context",
            "import pandas as pd",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["recommendation"]["profile"] == "medium"
    assert payload["resource_decision"]["requested_mode"] == DYNAMIC_MODE
    assert payload["resource_decision"]["applied_mode"] == DYNAMIC_MODE
    assert payload["resource_decision"]["resources"]["cpu_request_millicores"] == 500
