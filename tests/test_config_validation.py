from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
import yaml

from recommender.deployment import DeploymentMetadata
from recommender.jupyterhub_integration import (
    PREVIEW_VERSION,
    PROFILE_RESOURCES,
    RecommendationPreviewRuntime,
    options_form,
    safe_escape_truncate,
)
from recommender.rule_based import RuleBasedRecommender, load_image_catalog


ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}$")


def load_yaml(path: str | Path) -> dict:
    with (ROOT / path).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def preview_runtime(*, monotonic=None) -> RecommendationPreviewRuntime:
    catalog = load_image_catalog()
    kwargs = {}
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    return RecommendationPreviewRuntime(
        deployment=DeploymentMetadata(
            backend="rule_based",
            backend_version="rule-based-v1",
            package_version="intent-spawner-recommender-v2",
            package_checksum="a" * 64,
        ),
        catalog=catalog,
        backend=RuleBasedRecommender(catalog=catalog),
        **kwargs,
    )


def spawner(username: str = "alice", *, options=None):
    logs = []
    return SimpleNamespace(
        user=SimpleNamespace(name=username),
        user_options=options or {},
        environment={},
        extra_annotations={},
        extra_resource_guarantees={},
        extra_resource_limits={},
        log=SimpleNamespace(info=lambda *args: logs.append(args)),
        logs=logs,
    )


def issue(runtime: RecommendationPreviewRuntime, **values):
    return asyncio.run(runtime.issue("alice", values))


def confirm(runtime: RecommendationPreviewRuntime, preview: dict, *, action="accept", **extra):
    form = {
        "preview_version": [PREVIEW_VERSION],
        "decision_action": [action],
        "recommendation_preview_id": [preview["recommendation_preview_id"]],
        **{key: [value] for key, value in extra.items()},
    }
    return runtime.options_from_form(spawner(), form)


def test_baseline_profiles_match_runtime_catalog_policy():
    values = load_yaml("helm/baseline-values.yaml")
    profiles = values["singleuser"]["profileList"]
    assert [item["slug"] for item in profiles] == ["small", "medium", "large"]
    assert {
        item["slug"]: {
            key: item["kubespawner_override"][key] for key in PROFILE_RESOURCES[item["slug"]]
        }
        for item in profiles
    } == PROFILE_RESOURCES


def test_proposed_helm_mounts_runtime_and_has_no_client_side_recommender():
    values = load_yaml("helm/proposed-values.yaml")
    hub = values["hub"]
    code = hub["extraConfig"]["00-context-aware-recommender"]
    assert hub["extraEnv"]["RECOMMENDER_BACKEND"] == "rule_based"
    assert hub["extraEnv"]["PYTHONPATH"] == "/opt/intent-spawner"
    assert hub["extraVolumes"][0]["configMap"]["name"] == "intent-spawner-recommender"
    assert "install_jupyterhub" in code
    assert "recommendResource" not in code
    assert "recommendImage" not in code


def test_jupyterhub_options_callback_uses_supported_signature(monkeypatch):
    import recommender.jupyterhub_integration as integration

    runtime = preview_runtime()
    monkeypatch.setattr(integration, "RecommendationPreviewRuntime", lambda: runtime)
    config = SimpleNamespace(
        JupyterHub=SimpleNamespace(extra_handlers=[], base_url="/"),
        KubeSpawner=SimpleNamespace(),
    )
    integration.install_jupyterhub(SimpleNamespace(**vars(config)))
    signature = inspect.signature(config.KubeSpawner.options_from_form)
    assert list(signature.parameters) == ["formdata", "spawner"]
    assert signature.parameters["spawner"].kind is inspect.Parameter.KEYWORD_ONLY
    runtime.executor.shutdown()


def test_demo_images_are_digest_pinned_and_catalog_is_authoritative():
    catalog = load_image_catalog()
    proposed = load_yaml("helm/proposed-values.yaml")
    assert IMAGE_DIGEST_PATTERN.search(proposed["singleuser"]["image"]["tag"])
    assert all(IMAGE_DIGEST_PATTERN.search(item["reference"]) for item in catalog["images"].values())


def test_preview_response_has_locked_schema_and_stores_no_raw_context():
    runtime = preview_runtime()
    payload = issue(
        runtime,
        intent="train private customer model",
        dataset_size_gb="1.5",
        code_context="SECRET_CODE = 'do-not-store'\nmodel.fit(X, y)",
    )
    assert set(payload) == {
        "preview_version",
        "recommendation_preview_id",
        "recommendation",
        "applied_profile",
        "image_display_name",
        "metadata",
    }
    assert payload["preview_version"] == PREVIEW_VERSION
    assert payload["recommendation"]["backend_name"] == "rule_based"
    assert "raw_response" not in payload["metadata"]
    stored = runtime.previews[payload["recommendation_preview_id"]]
    rendered = json.dumps(stored)
    assert "private customer" not in rendered
    assert "SECRET_CODE" not in rendered
    runtime.executor.shutdown()


def test_submit_requires_existing_preview_and_never_creates_one_implicitly():
    runtime = preview_runtime()
    with pytest.raises(ValueError, match="server-side recommendation preview"):
        runtime.options_from_form(
            spawner(),
            {"preview_version": [PREVIEW_VERSION], "decision_action": ["accept"]},
        )
    assert runtime.previews == {}
    runtime.executor.shutdown()


def test_confirmed_preview_applies_resources_image_and_safe_telemetry():
    runtime = preview_runtime()
    preview = issue(runtime, intent="deep learning with torch", dataset_size_gb=0.2, code_context="")
    options = confirm(runtime, preview)
    target = spawner(options=options)
    asyncio.run(runtime.pre_spawn(target))
    assert target.cpu_limit == PROFILE_RESOURCES["large"]["cpu_limit"]
    assert target.image == runtime.images["pytorch-deep-learning"]["reference"]
    assert preview["recommendation_preview_id"] not in runtime.previews
    assert all("reason" not in key for key in target.extra_annotations)
    audit = json.loads(target.logs[-1][1])
    assert set(audit) == {
        "event", "event_id", "backend", "backend_version", "profile", "image_id",
        "fallback_category", "attempts", "latency_seconds", "policy_version",
        "catalog_version", "package_version", "package_checksum",
    }
    runtime.executor.shutdown()


def test_manual_override_is_allowlisted_and_bound_at_confirmation():
    runtime = preview_runtime()
    preview = issue(runtime, intent="basic Python", dataset_size_gb=0.1, code_context="")
    with pytest.raises(ValueError, match="not allowlisted"):
        confirm(
            runtime,
            preview,
            action="override",
            override_profile="medium",
            override_image_id="evil.example/latest",
        )
    options = confirm(
        runtime,
        preview,
        action="override",
        override_profile="medium",
        override_image_id="scipy-data-science",
    )
    target = spawner(options=options)
    asyncio.run(runtime.pre_spawn(target))
    assert target.cpu_limit == PROFILE_RESOURCES["medium"]["cpu_limit"]
    assert target.image == runtime.images["scipy-data-science"]["reference"]
    runtime.executor.shutdown()


def test_options_form_fetches_server_endpoint_with_xsrf_and_no_client_rules():
    runtime = preview_runtime()
    rendered = options_form(runtime, "/hub/recommendation-preview")
    assert 'fetch(__ENDPOINT__' not in rendered
    assert 'fetch("/hub/recommendation-preview"' in rendered
    assert "/hub/recommendation-preview" in rendered
    assert "X-XSRFToken" in rendered
    assert "recommendResource" not in rendered
    assert "recommendImage" not in rendered
    assert 'name="intent"' not in rendered
    assert 'name="code_context"' not in rendered
    runtime.executor.shutdown()


def test_input_validation_and_html_serialization_are_bounded():
    runtime = preview_runtime()
    for value in ("bad", "1e309", -1, float("nan")):
        with pytest.raises(ValueError, match="finite non-negative"):
            issue(runtime, intent="x", dataset_size_gb=value, code_context="")
    escaped = safe_escape_truncate("<script>alert(1)</script>&unfinished", 20)
    assert "<script>" not in escaped
    assert len(escaped) <= 20
    runtime.executor.shutdown()


def test_repository_contains_no_raw_notebooks():
    assert not list(ROOT.rglob("*.ipynb"))
