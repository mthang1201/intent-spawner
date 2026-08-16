from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from recommender.jupyterhub_integration import PREVIEW_VERSION, safe_json_dumps
from test_config_validation import preview_runtime


ROOT = Path(__file__).resolve().parents[1]


def load_reprovision(runtime):
    values = yaml.safe_load((ROOT / "helm/reprovision-values.yaml").read_text(encoding="utf-8"))
    code = values["hub"]["extraConfig"]["10-intent-aware-reprovisioning"]
    c = SimpleNamespace(
        JupyterHub=SimpleNamespace(extra_handlers=[]),
        KubeSpawner=SimpleNamespace(delete_pvc=True),
    )
    namespace = {
        "c": c,
        "RECOMMENDATION_RUNTIME": runtime,
        "PREVIEW_VERSION": PREVIEW_VERSION,
        "safe_json_dumps": safe_json_dumps,
    }
    exec(compile(code, "helm/reprovision-values.yaml::extraConfig", "exec"), namespace)
    return values, namespace


def user_for_runtime(runtime, *, event_id="old-event"):
    async def poll():
        return 1

    logs = []
    spawner = SimpleNamespace(
        ready=True,
        pending=None,
        delete_pvc=False,
        pvc_name="claim-alice",
        slow_stop_timeout=0.1,
        user=SimpleNamespace(name="alice"),
        user_options={
            "event_id": event_id,
            "applied_profile": "small",
            "applied_image_id": "minimal-python",
            "reprovision_generation": 0,
        },
        environment={},
        extra_annotations={},
        extra_resource_guarantees={},
        extra_resource_limits={},
        log=SimpleNamespace(info=lambda *args: logs.append(args)),
        poll=poll,
    )
    return SimpleNamespace(
        name="alice",
        escaped_name="alice",
        running=True,
        spawner=spawner,
    )


def test_overlay_registers_handler_and_stable_dynamic_storage():
    runtime = preview_runtime()
    values, namespace = load_reprovision(runtime)
    assert values["singleuser"]["storage"] == {"type": "dynamic", "capacity": "1Gi"}
    assert namespace["c"].JupyterHub.extra_handlers[-1][0] == r"/reprovision"
    assert namespace["c"].KubeSpawner.delete_pvc is False
    runtime.executor.shutdown()


def test_reprovision_preview_reuses_unified_one_time_token():
    runtime = preview_runtime()
    _, namespace = load_reprovision(runtime)
    user = user_for_runtime(runtime)
    preview = asyncio.run(
        namespace["build_reprovision_preview"](
            user,
            {"intent": "train model", "dataset_size_gb": 2, "code_context": "model.fit(X,y)"},
        )
    )
    token = preview["proposed"]["recommendation_preview_id"]
    assert token in runtime.previews
    assert preview["persistence"] == {
        "home_volume_retained": True,
        "kernel_state_retained": False,
        "live_migration": False,
    }
    assert "dynamic_preview_id" not in str(preview)
    runtime.executor.shutdown()


def test_reprovision_accept_requires_ack_and_current_server_identity():
    runtime = preview_runtime()
    _, namespace = load_reprovision(runtime)
    user = user_for_runtime(runtime)
    preview = asyncio.run(namespace["build_reprovision_preview"](user, {"intent": "x"}))
    base = {
        "preview_version": preview["preview_version"],
        "recommendation_preview_id": preview["proposed"]["recommendation_preview_id"],
        "expected_current_event_id": "old-event",
    }
    with pytest.raises(ValueError, match="acknowledged"):
        namespace["build_reprovision_options"](user, base)
    with pytest.raises(ValueError, match="server changed"):
        namespace["build_reprovision_options"](
            user,
            {**base, "acknowledge_restart": True, "expected_current_event_id": "other"},
        )
    runtime.executor.shutdown()


def test_reprovision_options_are_privacy_minimized_and_generation_bound():
    runtime = preview_runtime()
    _, namespace = load_reprovision(runtime)
    user = user_for_runtime(runtime)
    preview = asyncio.run(
        namespace["build_reprovision_preview"](
            user,
            {"intent": "private intent", "code_context": "private code", "dataset_size_gb": 0.2},
        )
    )
    options = namespace["build_reprovision_options"](
        user,
        {
            "preview_version": preview["preview_version"],
            "recommendation_preview_id": preview["proposed"]["recommendation_preview_id"],
            "expected_current_event_id": "old-event",
            "acknowledge_restart": True,
        },
    )
    assert options["provisioning_mode"] == "reprovision"
    assert options["reprovision_generation"] == 1
    assert "private intent" not in str(options)
    assert "private code" not in str(options)
    runtime.executor.shutdown()


def test_reprovision_stops_then_spawns_with_same_pvc_and_consumes_token():
    runtime = preview_runtime()
    _, namespace = load_reprovision(runtime)
    user = user_for_runtime(runtime)
    preview = asyncio.run(namespace["build_reprovision_preview"](user, {"intent": "large", "dataset_size_gb": 2}))
    token = preview["proposed"]["recommendation_preview_id"]
    options = namespace["build_reprovision_options"](
        user,
        {
            "preview_version": preview["preview_version"],
            "recommendation_preview_id": token,
            "expected_current_event_id": "old-event",
            "acknowledge_restart": True,
        },
    )
    order = []

    class Handler:
        log = SimpleNamespace(info=lambda *args: None)

        async def stop_single_user(self, selected_user):
            order.append(("stop", selected_user.spawner.pvc_name))
            return None

        async def spawn_single_user(self, selected_user, *, options):
            order.append(("spawn", selected_user.spawner.pvc_name))
            selected_user.spawner.user_options = options
            await runtime.pre_spawn(selected_user.spawner)

    asyncio.run(namespace["run_reprovision"](Handler(), user, options))
    assert order == [("stop", "claim-alice"), ("spawn", "claim-alice")]
    assert token not in runtime.previews
    assert user.spawner.image == runtime.images[options["applied_image_id"]]["reference"]
    runtime.executor.shutdown()


def test_reprovision_rejects_pvc_deletion_configuration():
    runtime = preview_runtime()
    _, namespace = load_reprovision(runtime)
    user = user_for_runtime(runtime)
    user.spawner.delete_pvc = True
    with pytest.raises(RuntimeError, match="PVC deletion"):
        asyncio.run(namespace["run_reprovision"](SimpleNamespace(), user, {}))
    runtime.executor.shutdown()


def test_reprovision_page_requires_explicit_acknowledgement():
    runtime = preview_runtime()
    _, namespace = load_reprovision(runtime)
    page = namespace["_reprovision_page"]("xsrf", "/hub/reprovision")
    assert "I saved my files" in page
    assert "kernel state" in page
    assert "confirm.disabled=!ack.checked" in page
    runtime.executor.shutdown()
