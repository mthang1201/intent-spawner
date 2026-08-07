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


def test_config_loads_without_missing_handler_imports():
    values, _, namespace = load_reprovision_config()
    assert "IntentReprovisionHandler" in namespace
    assert issubclass(namespace["IntentReprovisionHandler"], namespace["BaseHandler"])
    assert hasattr(namespace["web"], "authenticated")


def test_reprovision_overlay_enables_stable_dynamic_home_storage_and_handler():
    values, kube_spawner_config, namespace = load_reprovision_config()

    assert values["singleuser"]["storage"] == {"type": "dynamic", "capacity": "1Gi"}
    assert kube_spawner_config.pre_spawn_hook is namespace["reprovision_pre_spawn_hook"]
    assert any(path == r"/reprovision" for path, _ in namespace["c"].JupyterHub.extra_handlers)

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
    namespace.pop("build_preview_payload", None)
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


def test_delayed_pod_termination():
    _, _, namespace = load_reprovision_config()
    user = running_user()

    poll_count = 0

    async def delayed_poll():
        nonlocal poll_count
        poll_count += 1
        if poll_count < 3:
            return None
        return 0

    user.spawner.poll = delayed_poll
    user.spawner.slow_stop_timeout = 1
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

    asyncio.run(namespace["run_reprovision"](handler, user, options))
    assert poll_count >= 3
    assert len(spawn_calls) == 1


def test_stop_timeout():
    _, _, namespace = load_reprovision_config()
    user = running_user()

    async def never_stopped():
        return None

    user.spawner.poll = never_stopped
    user.spawner.slow_stop_timeout = 0.1
    preview = namespace["build_reprovision_preview"](user, preview_values())
    options = namespace["build_reprovision_options"](user, confirmed_values(preview))
    logged_errors = []

    async def stop_single_user(_user):
        completed = asyncio.get_running_loop().create_future()
        completed.set_result(None)
        return completed

    async def spawn_single_user(*args, **kwargs):
        pass

    handler = SimpleNamespace(
        stop_single_user=stop_single_user,
        spawn_single_user=spawn_single_user,
        log=SimpleNamespace(
            info=lambda *args, **kwargs: None,
            error=lambda msg, *args: logged_errors.append(msg % args if args else msg),
        ),
    )

    with pytest.raises(RuntimeError, match="did not stop within the expected timeout"):
        asyncio.run(namespace["run_reprovision"](handler, user, options))

    assert any("reprovision_failed" in log and "stop" in log for log in logged_errors)


def test_stop_exception_releases_lock():
    _, _, namespace = load_reprovision_config()
    user = running_user()
    namespace["REPROVISION_TASKS"][user.name] = "stub-task"

    async def failing_stop(_user):
        raise RuntimeError("stop failed in K8s")

    handler = SimpleNamespace(
        stop_single_user=failing_stop,
        spawn_single_user=None,
        log=SimpleNamespace(
            info=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        ),
    )

    options = {
        "event_id": "test",
        "reprovision_generation": 1,
        "previous_event_id": "old",
        "previous_applied_profile": "small",
        "applied_profile": "large",
        "previous_applied_image_id": "minimal-python",
        "applied_image_id": "scipy-data-science",
    }

    async def scenario():
        task = asyncio.create_task(namespace["run_reprovision"](handler, user, options))
        task.add_done_callback(
            lambda completed: namespace["_reprovision_task_done"](user.name, completed, handler.log)
        )
        with pytest.raises(RuntimeError, match="stop failed in K8s"):
            await task

    asyncio.run(scenario())
    assert user.name not in namespace["REPROVISION_TASKS"]


def test_spawn_failure_releases_lock():
    _, _, namespace = load_reprovision_config()
    user = running_user()

    async def stop_single_user(_user):
        completed = asyncio.get_running_loop().create_future()
        completed.set_result(None)
        return completed

    async def failing_spawn(*args, **kwargs):
        raise RuntimeError("insufficient CPU quota")

    handler = SimpleNamespace(
        stop_single_user=stop_single_user,
        spawn_single_user=failing_spawn,
        log=SimpleNamespace(
            info=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        ),
    )

    options = {
        "event_id": "test",
        "reprovision_generation": 1,
        "previous_event_id": "old",
        "previous_applied_profile": "small",
        "applied_profile": "large",
        "previous_applied_image_id": "minimal-python",
        "applied_image_id": "scipy-data-science",
    }

    async def scenario():
        task = asyncio.create_task(namespace["run_reprovision"](handler, user, options))
        namespace["REPROVISION_TASKS"][user.name] = task
        task.add_done_callback(
            lambda completed: namespace["_reprovision_task_done"](user.name, completed, handler.log)
        )
        with pytest.raises(RuntimeError, match="insufficient CPU quota"):
            await task

    asyncio.run(scenario())
    assert user.name not in namespace["REPROVISION_TASKS"]


def test_forged_override_action_rejected():
    _, _, namespace = load_reprovision_config()
    user = running_user()
    preview = namespace["build_reprovision_preview"](user, preview_values())
    values = confirmed_values(preview)
    values["action"] = "override"

    with pytest.raises(ValueError, match="only accept action is supported"):
        namespace["build_reprovision_options"](user, values)


def test_replayed_dynamic_preview_token_consumed():
    _, _, namespace = load_reprovision_config()
    user = running_user()

    consumed_tokens = []

    def stub_validate_dynamic_resource_preview(options, username, *, consume=False):
        token = options.get("dynamic_preview_id")
        if token in consumed_tokens:
            raise ValueError("dynamic preview is unknown or has already been used")
        if consume:
            consumed_tokens.append(token)

    namespace["validate_dynamic_resource_preview"] = stub_validate_dynamic_resource_preview

    preview = namespace["build_reprovision_preview"](user, preview_values())
    values = confirmed_values(preview)
    values["dynamic_preview_id"] = "test-dynamic-token-123"

    options = namespace["build_reprovision_options"](user, values)
    assert options["provisioning_mode"] == "reprovision"
    assert "test-dynamic-token-123" in consumed_tokens

    # Second attempt with same token fails
    with pytest.raises(ValueError, match="already been used"):
        namespace["build_reprovision_options"](user, values)


def test_invalid_profile_or_non_allowlisted_image_rejected_in_hook():
    _, kube_spawner_config, namespace = load_reprovision_config()

    invalid_profile_spawner = SimpleNamespace(
        user_options={
            "provisioning_mode": "reprovision",
            "decision_action": "accept",
            "event_id": "e1",
            "recommended_profile": "small",
            "applied_profile": "unapproved-huge",
            "recommended_image_id": "minimal-python",
            "applied_image_id": "minimal-python",
            "profile_reasons": [],
            "image_reasons": [],
            "policy_version": namespace["RECOMMENDATION_POLICY_VERSION"],
            "catalog_version": namespace["IMAGE_CATALOG_VERSION"],
        },
        environment={},
        extra_annotations={},
    )
    with pytest.raises(ValueError, match="applied profile is not allowlisted"):
        asyncio.run(kube_spawner_config.pre_spawn_hook(invalid_profile_spawner))

    invalid_image_spawner = SimpleNamespace(
        user_options={
            "provisioning_mode": "reprovision",
            "decision_action": "accept",
            "event_id": "e1",
            "recommended_profile": "small",
            "applied_profile": "small",
            "recommended_image_id": "malicious-image",
            "applied_image_id": "malicious-image",
            "profile_reasons": [],
            "image_reasons": [],
            "policy_version": namespace["RECOMMENDATION_POLICY_VERSION"],
            "catalog_version": namespace["IMAGE_CATALOG_VERSION"],
        },
        environment={},
        extra_annotations={},
    )
    with pytest.raises(ValueError, match="applied image is not allowlisted"):
        asyncio.run(kube_spawner_config.pre_spawn_hook(invalid_image_spawner))


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
