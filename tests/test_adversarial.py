import asyncio
import time
from types import SimpleNamespace

import pytest

from tests.test_config_validation import load_proposed_extra_config
from tests.test_dynamic_profile_overlay import execute_overlay


def test_proposed_config_rejects_forged_api_user_options():
    kube_spawner_config, namespace = load_proposed_extra_config()

    forged_user_options = {
        "decision_action": "accept",
        "event_id": "forged-uuid-1234",
        "recommended_profile": "small",
        "applied_profile": "large",
        "recommended_image_id": "minimal-python",
        "applied_image_id": "pytorch-deep-learning",
        "profile_reasons": ["Forged justification"],
        "image_reasons": ["Forged image justification"],
        "policy_version": namespace["RECOMMENDATION_POLICY_VERSION"],
        "catalog_version": namespace["IMAGE_CATALOG_VERSION"],
    }

    spawner = SimpleNamespace(
        user_options=forged_user_options,
        environment={},
        extra_annotations={},
        user=SimpleNamespace(name="attacker"),
        log=SimpleNamespace(info=lambda *args, **kwargs: None),
    )

    with pytest.raises(ValueError, match="missing or invalid recommendation preview token"):
        asyncio.run(kube_spawner_config.pre_spawn_hook(spawner))


def test_proposed_config_rejects_cross_user_preview_tokens():
    kube_spawner_config, namespace = load_proposed_extra_config()

    preview_item = namespace["create_recommendation_preview"](
        username="userA",
        intent="small python job",
        dataset_size_gb=0.1,
    )

    spawner_user_b = SimpleNamespace(
        user_options={
            "recommendation_preview_id": preview_item["preview_id"],
            "decision_action": "accept",
        },
        environment={},
        extra_annotations={},
        user=SimpleNamespace(name="userB"),
        log=SimpleNamespace(info=lambda *args, **kwargs: None),
    )

    with pytest.raises(ValueError, match="token is not bound to user"):
        asyncio.run(kube_spawner_config.pre_spawn_hook(spawner_user_b))


def test_proposed_config_rejects_missing_preview_token():
    kube_spawner_config, _ = load_proposed_extra_config()

    spawner = SimpleNamespace(
        user_options={"decision_action": "accept"},
        environment={},
        extra_annotations={},
        user=SimpleNamespace(name="userA"),
        log=SimpleNamespace(info=lambda *args, **kwargs: None),
    )

    with pytest.raises(ValueError, match="missing or invalid recommendation preview token"):
        asyncio.run(kube_spawner_config.pre_spawn_hook(spawner))


def test_proposed_config_rejects_stale_preview_token_and_policy_changes():
    kube_spawner_config, namespace = load_proposed_extra_config()

    preview_item = namespace["create_recommendation_preview"](
        username="userA",
        intent="small python job",
        dataset_size_gb=0.1,
    )

    preview_item["issued_at"] = time.time() - 4000
    namespace["RECOMMENDATION_PREVIEWS"][preview_item["preview_id"]] = preview_item

    spawner = SimpleNamespace(
        user_options={
            "recommendation_preview_id": preview_item["preview_id"],
            "decision_action": "accept",
        },
        environment={},
        extra_annotations={},
        user=SimpleNamespace(name="userA"),
        log=SimpleNamespace(info=lambda *args, **kwargs: None),
    )

    with pytest.raises(ValueError, match="missing or invalid recommendation preview token"):
        asyncio.run(kube_spawner_config.pre_spawn_hook(spawner))


def test_proposed_config_enforces_single_use_token_replay_protection():
    kube_spawner_config, namespace = load_proposed_extra_config()

    preview_item = namespace["create_recommendation_preview"](
        username="userA",
        intent="small python job",
        dataset_size_gb=0.1,
    )
    token = preview_item["preview_id"]

    spawner1 = SimpleNamespace(
        user_options={
            "recommendation_preview_id": token,
            "decision_action": "accept",
        },
        environment={},
        extra_annotations={},
        user=SimpleNamespace(name="userA"),
        log=SimpleNamespace(info=lambda *args, **kwargs: None),
    )

    asyncio.run(kube_spawner_config.pre_spawn_hook(spawner1))

    spawner2 = SimpleNamespace(
        user_options={
            "recommendation_preview_id": token,
            "decision_action": "accept",
        },
        environment={},
        extra_annotations={},
        user=SimpleNamespace(name="userA"),
        log=SimpleNamespace(info=lambda *args, **kwargs: None),
    )

    with pytest.raises(ValueError, match="missing or invalid recommendation preview token"):
        asyncio.run(kube_spawner_config.pre_spawn_hook(spawner2))


def test_dynamic_config_rejects_forged_and_replayed_tokens(monkeypatch):
    _, kube_spawner_config, namespace = execute_overlay(monkeypatch)

    spawner_forged = SimpleNamespace(
        user_options={
            "dynamic_preview_id": "forged-preview-id",
            "decision_action": "accept",
        },
        environment={},
        extra_annotations={},
        user=SimpleNamespace(name="attacker"),
        log=SimpleNamespace(info=lambda *args, **kwargs: None),
    )

    with pytest.raises(ValueError, match="missing"):
        asyncio.run(kube_spawner_config.pre_spawn_hook(spawner_forged))


def test_context_safe_serialization_prevents_script_breakout_xss():
    kube_spawner_config, namespace = load_proposed_extra_config()

    # Inject malicious string containing </script><script>alert(1)</script> into IMAGE_CATALOG
    malicious_catalog = dict(namespace["IMAGE_CATALOG"])
    malicious_catalog["custom_xss"] = {
        "display_name": "Injected </script><script>alert(1)</script>",
        "description": "Exploit </script><script>alert('xss')</script>",
        "match_terms": ["</script><script>alert(2)</script>"],
        "priority": 100,
        "image": "registry.example.com/custom:latest",
    }
    namespace["IMAGE_CATALOG"] = malicious_catalog

    spawner = SimpleNamespace()
    rendered_form = asyncio.run(namespace["context_options_form"](spawner))

    # Assert that no literal </script> tag exists inside the JavaScript constants section
    script_start = rendered_form.find("<script>")
    script_end = rendered_form.rfind("</script>")
    assert script_start != -1 and script_end != -1
    script_content = rendered_form[script_start + len("<script>"):script_end]

    assert "</script>" not in script_content
    assert "\\u003c/script\\u003e" in script_content

    # Check reprovisioning page XSS serialization
    from tests.test_reprovisioning import load_reprovision_config
    _, _, ns_rep = load_reprovision_config()
    reprovision_page_fn = ns_rep["_reprovision_page"]

    malicious_endpoint = "/hub/reprovision</script><script>alert('ep')</script>"
    malicious_xsrf = "xsrf-123'</script><script>alert('xsrf')</script>"
    rep_html = reprovision_page_fn(malicious_xsrf, malicious_endpoint)

    rep_script_start = rep_html.find("<script>")
    rep_script_end = rep_html.rfind("</script>")
    rep_script_content = rep_html[rep_script_start + len("<script>"):rep_script_end]

    assert "</script>" not in rep_script_content
    assert "\\u003c/script\\u003e" in rep_script_content


def test_server_restart_invalidates_in_flight_previews_fail_closed():
    kube_spawner_config, namespace = load_proposed_extra_config()

    preview_item = namespace["create_recommendation_preview"](
        username="userA",
        intent="machine learning model",
        dataset_size_gb=1.0,
    )
    token = preview_item["preview_id"]

    # Simulate Hub process restart / deployment update by clearing preview state dictionary
    namespace["RECOMMENDATION_PREVIEWS"].clear()

    spawner = SimpleNamespace(
        user_options={
            "recommendation_preview_id": token,
            "decision_action": "accept",
        },
        environment={},
        extra_annotations={},
        user=SimpleNamespace(name="userA"),
        log=SimpleNamespace(info=lambda *args, **kwargs: None),
    )

    with pytest.raises(ValueError, match="session may have expired or server restarted"):
        asyncio.run(kube_spawner_config.pre_spawn_hook(spawner))


def test_multi_tab_preview_isolation_and_bounds():
    kube_spawner_config, namespace = load_proposed_extra_config()

    # Generate previews in Tab 1 and Tab 2 for the same user
    tab1_preview = namespace["create_recommendation_preview"](
        username="userA",
        intent="data processing tab 1",
        dataset_size_gb=0.5,
    )
    tab2_preview = namespace["create_recommendation_preview"](
        username="userA",
        intent="deep learning tab 2",
        dataset_size_gb=2.0,
    )

    assert tab1_preview["preview_id"] != tab2_preview["preview_id"]
    assert tab1_preview["preview_id"] in namespace["RECOMMENDATION_PREVIEWS"]
    assert tab2_preview["preview_id"] in namespace["RECOMMENDATION_PREVIEWS"]

    # Tab 1 spawns pod
    spawner_tab1 = SimpleNamespace(
        user_options={
            "recommendation_preview_id": tab1_preview["preview_id"],
            "decision_action": "accept",
        },
        environment={},
        extra_annotations={},
        user=SimpleNamespace(name="userA"),
        log=SimpleNamespace(info=lambda *args, **kwargs: None),
    )
    asyncio.run(kube_spawner_config.pre_spawn_hook(spawner_tab1))

    # Tab 1 token consumed
    assert tab1_preview["preview_id"] not in namespace["RECOMMENDATION_PREVIEWS"]
    # Tab 2 token remains valid until consumed or expired
    assert tab2_preview["preview_id"] in namespace["RECOMMENDATION_PREVIEWS"]

    # Re-submitting Tab 1 token fails (replay protection)
    with pytest.raises(ValueError, match="session may have expired or server restarted"):
        asyncio.run(kube_spawner_config.pre_spawn_hook(spawner_tab1))

    # Tab 2 spawns pod
    spawner_tab2 = SimpleNamespace(
        user_options={
            "recommendation_preview_id": tab2_preview["preview_id"],
            "decision_action": "accept",
        },
        environment={},
        extra_annotations={},
        user=SimpleNamespace(name="userA"),
        log=SimpleNamespace(info=lambda *args, **kwargs: None),
    )
    asyncio.run(kube_spawner_config.pre_spawn_hook(spawner_tab2))
    assert tab2_preview["preview_id"] not in namespace["RECOMMENDATION_PREVIEWS"]


def test_preview_memory_bounds_eviction():
    kube_spawner_config, namespace = load_proposed_extra_config()

    max_entries = namespace["RECOMMENDATION_PREVIEW_MAX_ENTRIES"]
    first_preview = namespace["create_recommendation_preview"](
        username="userA",
        intent="first workload",
    )

    # Issue max_entries additional previews
    for i in range(max_entries):
        namespace["create_recommendation_preview"](
            username=f"user_{i}",
            intent=f"workload {i}",
        )

    # Memory bound cap enforced
    assert len(namespace["RECOMMENDATION_PREVIEWS"]) == max_entries
    # Oldest preview evicted
    assert first_preview["preview_id"] not in namespace["RECOMMENDATION_PREVIEWS"]

