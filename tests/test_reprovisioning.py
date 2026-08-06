import asyncio
import html
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_config_validation import load_proposed_extra_config  # noqa: E402


def load_reprovision_config():
    kube_spawner_config, namespace = load_proposed_extra_config()
    with (ROOT / "helm/reprovision-values.yaml").open(encoding="utf-8") as handle:
        values = yaml.safe_load(handle)
    code = values["hub"]["extraConfig"]["10-intent-aware-reprovisioning"]
    exec(compile(code, "helm/reprovision-values.yaml::extraConfig", "exec"), namespace)
    return values, kube_spawner_config, namespace


async def _stopped_poll():
    return 1


def running_user(options=None):
    spawner = SimpleNamespace(
        ready=True,
        pending=None,
        poll=_stopped_poll,
        user_options=options
        or {
            "event_id": "old-event",
            "applied_profile": "small",
            "applied_image_id": "minimal-python",
            "reprovision_generation": 0,
        },
    )
    return SimpleNamespace(name="pytest-user", escaped_name="pytest-user", spawner=spawner)


def preview_values():
    return {
        "intent": "train a scikit-learn model",
        "dataset_size_gb": "1.5",
        "code_context": "model.fit(X, y)",
    }


def confirmed_values(preview):
    proposed = preview["proposed"]
    return {
        "action": "accept",
        "acknowledge_restart": True,
        "preview_version": preview["preview_version"],
        "expected_current_event_id": preview["current"]["event_id"],
        "expected_recommended_profile": proposed["recommended_profile"],
        "expected_recommended_image_id": proposed["recommended_image_id"],
        "expected_policy_version": proposed["policy_version"],
        "expected_catalog_version": proposed["catalog_version"],
        **preview_values(),
    }


def test_reprovision_overlay_enables_stable_dynamic_home_storage_and_handler():
    values, kube_spawner_config, namespace = load_reprovision_config()

    assert values["singleuser"]["storage"] == {"type": "dynamic", "capacity": "1Gi"}
    assert kube_spawner_config.pre_spawn_hook is namespace["reprovision_pre_spawn_hook"]
    assert [path for path, _ in namespace["c"].JupyterHub.extra_handlers] == [
        r"/recommendation-preview",
        r"/reprovision",
    ]

    source = values["hub"]["extraConfig"]["10-intent-aware-reprovisioning"]
    assert "await stop_future" in source
    assert source.index("await stop_future") < source.index(
        "await handler.spawn_single_user"
    )
    assert "delete pvc" not in source.lower()
    assert "delete_pvc" not in source.lower()


def test_reprovision_preview_compares_current_and_proposed_and_states_data_loss_boundary():
    _, _, namespace = load_reprovision_config()
    user = running_user()

    preview = namespace["build_reprovision_preview"](user, preview_values())

    assert preview["current"] == {
        "event_id": "old-event",
        "applied_profile": "small",
        "applied_image_id": "minimal-python",
        "generation": 0,
    }
    assert preview["proposed"]["recommended_profile"] == "large"
    assert preview["proposed"]["applied_profile"] == "large"
    assert preview["proposed"]["recommended_image_id"] == "scipy-data-science"
    assert preview["persistence"] == {
        "home_volume_retained": True,
        "kernel_state_retained": False,
        "live_migration": False,
    }
    warning_text = " ".join(preview["warnings"]).lower()
    assert "kernel" in warning_text
    assert "live migration" in warning_text
    assert "persistentvolumeclaim" in warning_text


def test_reprovision_preview_supports_original_inline_recommender_contract():
    _, _, namespace = load_reprovision_config()
    namespace.pop("build_preview_payload")
    namespace["recommend_workload"] = lambda *args: {
        "profile": "medium",
        "applied_profile": "medium",
        "score": 1,
        "profile_reasons": ["resource reason"],
        "image_id": "minimal-python",
        "image_reasons": ["image reason"],
    }

    preview = namespace["build_reprovision_preview"](
        running_user(),
        {"intent": "changed task", "dataset_size_gb": "0.5", "code_context": ""},
    )

    assert preview["proposed"] == {
        "recommended_profile": "medium",
        "applied_profile": "medium",
        "recommended_image_id": "minimal-python",
        "image_display_name": namespace["IMAGE_CATALOG"]["minimal-python"][
            "display_name"
        ],
        "policy_version": namespace["RECOMMENDATION_POLICY_VERSION"],
        "catalog_version": namespace["IMAGE_CATALOG_VERSION"],
        "reasons": ["resource reason", "image reason"],
    }


def test_confirm_recomputes_and_rejects_missing_acknowledgement_or_stale_preview():
    _, _, namespace = load_reprovision_config()
    user = running_user()
    preview = namespace["build_reprovision_preview"](user, preview_values())
    values = confirmed_values(preview)

    without_ack = dict(values, acknowledge_restart=False)
    with pytest.raises(ValueError, match="must be acknowledged"):
        namespace["build_reprovision_options"](user, without_ack)

    stale_server = dict(values, expected_current_event_id="another-event")
    with pytest.raises(ValueError, match="server changed"):
        namespace["build_reprovision_options"](user, stale_server)

    stale_recommendation = dict(values, expected_recommended_profile="small")
    with pytest.raises(ValueError, match="changed after preview"):
        namespace["build_reprovision_options"](user, stale_recommendation)


def test_confirmed_options_are_privacy_minimized_and_track_transition_generation():
    _, _, namespace = load_reprovision_config()
    user = running_user()
    preview = namespace["build_reprovision_preview"](user, preview_values())

    options = namespace["build_reprovision_options"](user, confirmed_values(preview))

    assert options["provisioning_mode"] == "reprovision"
    assert options["reprovision_generation"] == 1
    assert options["previous_event_id"] == "old-event"
    assert options["previous_applied_profile"] == "small"
    assert options["previous_applied_image_id"] == "minimal-python"
    assert options["applied_profile"] == "large"
    assert options["applied_image_id"] == "scipy-data-science"
    assert "intent" not in options
    assert "code_context" not in options
    assert "train a scikit-learn model" not in json.dumps(options)


def test_reprovision_waits_for_old_pod_stop_before_starting_replacement():
    _, _, namespace = load_reprovision_config()
    user = running_user()
    preview = namespace["build_reprovision_preview"](user, preview_values())
    options = namespace["build_reprovision_options"](user, confirmed_values(preview))
    calls = []

    async def scenario():
        stop_completed = asyncio.get_running_loop().create_future()

        async def stop_single_user(stopped_user):
            assert stopped_user is user
            calls.append("stop-requested")
            return stop_completed

        async def spawn_single_user(spawned_user, options):
            assert spawned_user is user
            calls.append("spawn-requested")

        handler = SimpleNamespace(
            stop_single_user=stop_single_user,
            spawn_single_user=spawn_single_user,
            log=SimpleNamespace(info=lambda *args, **kwargs: None),
        )
        task = asyncio.create_task(namespace["run_reprovision"](handler, user, options))
        await asyncio.sleep(0)
        assert calls == ["stop-requested"]
        stop_completed.set_result(None)
        await task

    asyncio.run(scenario())
    assert calls == ["stop-requested", "spawn-requested"]


def test_reprovision_does_not_spawn_while_old_pod_is_still_observed():
    _, _, namespace = load_reprovision_config()
    user = running_user()

    async def still_running():
        return None

    user.spawner.poll = still_running
    preview = namespace["build_reprovision_preview"](user, preview_values())
    options = namespace["build_reprovision_options"](user, confirmed_values(preview))
    spawn_calls = []

    async def stop_single_user(_user):
        completed = asyncio.get_running_loop().create_future()
        completed.set_result(None)
        return completed

    async def spawn_single_user(*args, **kwargs):
        spawn_calls.append((args, kwargs))

    handler = SimpleNamespace(
        stop_single_user=stop_single_user,
        spawn_single_user=spawn_single_user,
        log=SimpleNamespace(info=lambda *args, **kwargs: None),
    )

    with pytest.raises(RuntimeError, match="old notebook pod still exists"):
        asyncio.run(namespace["run_reprovision"](handler, user, options))
    assert spawn_calls == []


def test_reprovision_pre_spawn_hook_marks_replacement_without_leaking_context():
    _, kube_spawner_config, namespace = load_reprovision_config()
    user = running_user()
    preview = namespace["build_reprovision_preview"](user, preview_values())
    options = namespace["build_reprovision_options"](user, confirmed_values(preview))
    spawner = SimpleNamespace(
        user_options=options,
        environment={},
        extra_annotations={},
        log=SimpleNamespace(info=lambda *args, **kwargs: None),
    )

    asyncio.run(kube_spawner_config.pre_spawn_hook(spawner))

    assert spawner.environment["PROVISIONING_MODE"] == "reprovision"
    assert spawner.environment["REPROVISION_GENERATION"] == "1"
    assert spawner.environment["JUPYTERHUB_REPROVISION_URL"] == "/hub/reprovision"
    assert spawner.extra_annotations[
        "z2jh-context-demo.local/reprovision-generation"
    ] == "1"
    assert "train a scikit-learn model" not in str(spawner.environment)
    assert "model.fit" not in str(spawner.extra_annotations)


def test_reprovision_page_requires_explicit_kernel_state_loss_acknowledgement():
    _, _, namespace = load_reprovision_config()

    page = namespace["_reprovision_page"]("xsrf-token", "/hub/")

    assert "Restart required — no live migration" in page
    assert "kernel and terminal state will be lost" in page
    assert 'id="ack" type="checkbox"' in page
    assert 'id="confirm" type="button" disabled' in page
    assert 'const endpoint = "/hub/reprovision"' in page
    assert html.escape("xsrf-token", quote=True) in page
