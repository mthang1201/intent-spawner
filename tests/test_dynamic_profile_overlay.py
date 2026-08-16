from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from recommender.dynamic_resources import DynamicResourceSpec
from test_config_validation import confirm, issue, preview_runtime, spawner


ROOT = Path(__file__).resolve().parents[1]


def enable_dynamic(runtime):
    values = yaml.safe_load((ROOT / "helm/dynamic-values.yaml").read_text(encoding="utf-8"))
    code = values["hub"]["extraConfig"]["01-policy-bounded-dynamic-resources"]
    namespace = {
        "RECOMMENDATION_RUNTIME": runtime,
        "IMAGE_CATALOG": runtime.images,
    }
    exec(compile(code, "helm/dynamic-values.yaml::extraConfig", "exec"), namespace)
    return values, namespace


def test_dynamic_overlay_is_opt_in_and_uses_unified_preview_record():
    runtime = preview_runtime()
    values, namespace = enable_dynamic(runtime)
    preview = issue(runtime, intent="ordinary dataframe", dataset_size_gb=0.8, code_context="")
    assert values["hub"]["extraEnv"]["RESOURCE_SELECTION_MODE"] == "dynamic"
    assert preview["resource_decision"]["applied_mode"] == "dynamic"
    assert "dynamic_preview_id" not in preview
    record = runtime.previews[preview["recommendation_preview_id"]]
    assert record["resource_decision"] == preview["resource_decision"]
    assert record["generation"]["resource_policy_hash"] == namespace["DYNAMIC_RESOURCE_POLICY_HASH"]
    runtime.executor.shutdown()


def test_dynamic_resources_match_preview_and_pod_exactly():
    runtime = preview_runtime()
    enable_dynamic(runtime)
    preview = issue(runtime, intent="ordinary dataframe", dataset_size_gb=0.8, code_context="")
    resources = preview["resource_decision"]["resources"]
    options = confirm(runtime, preview)
    target = spawner(options=options)
    asyncio.run(runtime.pre_spawn(target))
    assert target.cpu_guarantee == resources["cpu_request_millicores"] / 1000
    assert target.cpu_limit == resources["cpu_limit_millicores"] / 1000
    assert target.mem_guarantee == resources["memory_request_mib"] * 2**20
    assert target.mem_limit == resources["memory_limit_mib"] * 2**20
    runtime.executor.shutdown()


def test_dynamic_decision_is_revalidated_immediately_before_spawn():
    runtime = preview_runtime()
    enable_dynamic(runtime)
    preview = issue(runtime, intent="ordinary", dataset_size_gb=0.2, code_context="")
    options = confirm(runtime, preview)
    record = runtime.previews[preview["recommendation_preview_id"]]
    record["resource_decision"]["resources"]["cpu_limit_millicores"] = 999999
    with pytest.raises(ValueError, match="outside|quota"):
        asyncio.run(runtime.pre_spawn(spawner(options=options)))
    runtime.executor.shutdown()


def test_dynamic_policy_hash_change_invalidates_preview():
    runtime = preview_runtime()
    _, namespace = enable_dynamic(runtime)
    preview = issue(runtime, intent="ordinary", dataset_size_gb=0.2, code_context="")
    options = confirm(runtime, preview)
    namespace["DYNAMIC_RESOURCE_POLICY_HASH"] = "0" * 64
    # The closure reads the overlay global at call time.
    with pytest.raises(ValueError, match="policy changed"):
        asyncio.run(runtime.pre_spawn(spawner(options=options)))
    runtime.executor.shutdown()


def test_gpu_request_falls_back_visibly_under_default_no_gpu_policy():
    runtime = preview_runtime()
    enable_dynamic(runtime)
    preview = issue(runtime, intent="gpu torch training", dataset_size_gb=0.2, code_context="")
    decision = preview["resource_decision"]
    assert decision["applied_mode"] == "catalog"
    assert decision["resources"] is None
    assert "GPU count" in decision["fallback_reason"]
    options = confirm(runtime, preview)
    target = spawner(options=options)
    asyncio.run(runtime.pre_spawn(target))
    assert target.cpu_limit == 2
    runtime.executor.shutdown()


def test_manual_override_uses_catalog_resources_not_stale_dynamic_decision():
    runtime = preview_runtime()
    enable_dynamic(runtime)
    preview = issue(runtime, intent="ordinary dataframe", dataset_size_gb=0.8, code_context="")
    options = confirm(
        runtime,
        preview,
        action="override",
        override_profile="large",
        override_image_id="scipy-data-science",
    )
    target = spawner(options=options)
    asyncio.run(runtime.pre_spawn(target))
    assert target.cpu_limit == 2
    assert target.mem_limit == "2G"
    runtime.executor.shutdown()


def test_dynamic_policy_is_static_per_spawn_caps_not_live_quota_headroom():
    source = (ROOT / "helm/dynamic-values.yaml").read_text(encoding="utf-8")
    assert "ResourceQuota" not in source
    assert "quota_headroom" not in source
    spec = DynamicResourceSpec(100, 500, 256, 512)
    assert spec.to_kubespawner_resources()["cpu_limit"] == 0.5
