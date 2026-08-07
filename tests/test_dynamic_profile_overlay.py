import asyncio
from copy import deepcopy
from http.cookies import SimpleCookie
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
import yaml

from recommender import dynamic_resources


ROOT = Path(__file__).resolve().parents[1]

PROFILE_RESOURCES = {
    "small": {
        "cpu_guarantee": 0.1,
        "cpu_limit": 0.5,
        "mem_guarantee": "256M",
        "mem_limit": "384M",
    },
    "medium": {
        "cpu_guarantee": 0.5,
        "cpu_limit": 1,
        "mem_guarantee": "768M",
        "mem_limit": "1G",
    },
    "large": {
        "cpu_guarantee": 1.5,
        "cpu_limit": 2,
        "mem_guarantee": "1536M",
        "mem_limit": "2G",
    },
}

IMAGE_CATALOG = {
    "minimal-python": {"display_name": "Minimal Python", "reference": "minimal@sha256:test"},
    "scipy-data-science": {"display_name": "SciPy", "reference": "scipy@sha256:test"},
    "pytorch-deep-learning": {"display_name": "PyTorch", "reference": "torch@sha256:test"},
}


def load_overlay_code() -> tuple[dict, str]:
    with (ROOT / "helm" / "dynamic-values.yaml").open(encoding="utf-8") as handle:
        values = yaml.safe_load(handle)
    return values, values["hub"]["extraConfig"]["01-policy-bounded-dynamic-resources"]


def _first(formdata, name, default=""):
    value = formdata.get(name, [default])
    value = value[0] if isinstance(value, (list, tuple)) else value
    return value.decode() if isinstance(value, bytes) else value


def _recommend(values):
    intent = values.get("intent", "").lower()
    dataset = float(values.get("dataset_size_gb", 0) or 0)
    if "gpu" in intent:
        profile, score, image = "gpu_or_large", 99, "pytorch-deep-learning"
    elif "large" in intent or dataset >= 2:
        profile, score, image = "large", 3, "scipy-data-science"
    else:
        profile, score, image = "medium", 2, "minimal-python"
    return {
        "profile": profile,
        "score": score,
        "reasons": ["server recommendation"],
        "image_id": image,
        "image_reasons": ["server image"],
        "policy_version": "recommendation-policy-v1",
        "catalog_version": "catalog-v1",
    }


def _base_options_form(_spawner):
    return """
    <form>
      <div id="recommendation-flow">
        <input id="intent" name="intent"/><input id="dataset_size_gb" name="dataset_size_gb"/><input id="code_context" name="code_context"/>
        <input id="decision_action" name="decision_action" type="hidden" value=""/>
        <input name="preview_version" value=""/><span id="preview-error"></span>
        <button id="preview-recommendation" type="button">Preview</button>
        <section id="recommendation-preview" hidden><dl><dt>Resource profile</dt><dd id="preview-profile"></dd><dt>Notebook image</dt><dd id="preview-image"></dd><ul id="preview-reasons"></ul></dl>
          <button id="confirm-recommendation" type="submit" disabled>Confirm</button>
        </section>
        <select id="override_profile" name="override_profile"><option>small</option><option>medium</option><option>large</option></select>
        <select id="override_image_id" name="override_image_id"><option>minimal-python</option><option>scipy-data-science</option><option>pytorch-deep-learning</option></select>
        <button id="submit-override" type="submit" disabled>Override</button>
      </div>
    </form>
    """


def execute_overlay(monkeypatch, *, base_handler=None, web_module=None, policy=None):
    values, code = load_overlay_code()
    monkeypatch.setenv("RESOURCE_SELECTION_MODE", "dynamic")
    if policy is not None:
        monkeypatch.setattr(dynamic_resources, "load_resource_policy", lambda: policy)

    async def catalog_hook(spawner):
        options = spawner.user_options
        required = {
            "decision_action", "recommended_profile", "applied_profile",
            "recommended_image_id", "applied_image_id", "policy_version", "catalog_version",
        }
        if not required.issubset(options):
            raise ValueError("missing confirmed recommendation decision")
        if options["applied_profile"] not in PROFILE_RESOURCES:
            raise ValueError("applied profile is not allowlisted")
        if options["applied_image_id"] not in IMAGE_CATALOG:
            raise ValueError("applied image is not allowlisted")
        if options["policy_version"] != "recommendation-policy-v1":
            raise ValueError("stale recommendation policy")
        resources = PROFILE_RESOURCES[options["applied_profile"]]
        for key, value in resources.items():
            setattr(spawner, key, value)

    def build_preview_payload(raw):
        recommendation = _recommend(raw)
        return {
            "preview_version": "recommendation-preview-v1",
            "recommendation": recommendation,
            "applied_profile": "large" if recommendation["profile"] == "gpu_or_large" else recommendation["profile"],
            "image_display_name": IMAGE_CATALOG[recommendation["image_id"]]["display_name"],
        }

    def options_from_form(formdata):
        values_from_form = {
            "intent": _first(formdata, "intent"),
            "dataset_size_gb": _first(formdata, "dataset_size_gb", "0"),
            "code_context": _first(formdata, "code_context"),
        }
        recommendation = _recommend(values_from_form)
        action = _first(formdata, "decision_action")
        profile = "large" if recommendation["profile"] == "gpu_or_large" else recommendation["profile"]
        image = recommendation["image_id"]
        if action == "override":
            profile = _first(formdata, "override_profile")
            image = _first(formdata, "override_image_id")
        return {
            "decision_action": action,
            "event_id": "test-event",
            "recommended_profile": recommendation["profile"],
            "applied_profile": profile,
            "recommended_image_id": recommendation["image_id"],
            "applied_image_id": image,
            "profile_reasons": recommendation["reasons"],
            "image_reasons": recommendation["image_reasons"],
            "score": recommendation["score"],
            "dataset_size_gb": float(values_from_form["dataset_size_gb"] or 0),
            "policy_version": recommendation["policy_version"],
            "catalog_version": recommendation["catalog_version"],
        }

    kube_spawner = SimpleNamespace(
        options_form=_base_options_form,
        options_from_form=options_from_form,
        pre_spawn_hook=catalog_hook,
    )
    jupyterhub = SimpleNamespace(extra_handlers=[])
    namespace = {
        "c": SimpleNamespace(JupyterHub=jupyterhub, KubeSpawner=kube_spawner),
        "PROFILE_RESOURCES": PROFILE_RESOURCES,
        "IMAGE_CATALOG": IMAGE_CATALOG,
        "PREVIEW_VERSION": "recommendation-preview-v1",
        "RECOMMENDATION_POLICY_VERSION": "recommendation-policy-v1",
        "IMAGE_CATALOG_VERSION": "catalog-v1",
        "build_preview_payload": build_preview_payload,
        "recommend_workload": lambda *args: _recommend({
            "intent": args[0], "dataset_size_gb": args[1], "code_context": args[2]
        }),
    }

    class StubBaseHandler:
        pass

    if base_handler is None:
        base_handler = StubBaseHandler
    if web_module is None:
        web_module = SimpleNamespace(
            authenticated=lambda method: setattr(method, "jupyterhub_authenticated", True) or method
        )
    fake_modules = {
        "jupyterhub": ModuleType("jupyterhub"),
        "jupyterhub.handlers": ModuleType("jupyterhub.handlers"),
        "jupyterhub.handlers.base": ModuleType("jupyterhub.handlers.base"),
        "tornado": ModuleType("tornado"),
    }
    fake_modules["jupyterhub.handlers.base"].BaseHandler = base_handler
    fake_modules["tornado"].web = web_module
    previous = {name: sys.modules.get(name) for name in fake_modules}
    sys.modules.update(fake_modules)
    try:
        exec(compile(code, "helm/dynamic-values.yaml::extraConfig", "exec"), namespace)
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return values, kube_spawner, namespace


def preview_and_options(namespace, kube_spawner, *, intent="ordinary", dataset="0.8", action="accept"):
    values = {"intent": intent, "dataset_size_gb": dataset, "code_context": ""}
    payload = namespace["issue_dynamic_resource_preview"]("alice", values)
    formdata = {
        "intent": [intent],
        "dataset_size_gb": [dataset],
        "code_context": [""],
        "preview_version": [payload["preview_version"]],
        "decision_action": [action],
        "dynamic_preview_id": [payload["dynamic_preview_id"]],
        "override_profile": ["large"],
        "override_image_id": ["scipy-data-science"],
    }
    return payload, kube_spawner.options_from_form(formdata)


class LogCapture:
    def __init__(self):
        self.records = []

    def info(self, message, value):
        self.records.append((message, value))


def spawner_for(options):
    return SimpleNamespace(
        user_options=options,
        user=SimpleNamespace(name="alice"),
        environment={},
        extra_annotations={},
        extra_resource_guarantees={},
        extra_resource_limits={},
        log=LogCapture(),
    )


def test_overlay_is_opt_in_and_browser_preview_is_server_bound(monkeypatch):
    values, kube_spawner, namespace = execute_overlay(monkeypatch)
    rendered = asyncio.run(kube_spawner.options_form(SimpleNamespace()))

    assert values["hub"]["extraEnv"]["RESOURCE_SELECTION_MODE"] == "dynamic"
    assert namespace["RESOURCE_SELECTOR"].mode == "dynamic"
    assert 'name="dynamic_preview_id"' in rendered
    assert "/dynamic-resource-preview" in rendered
    assert 'headers["X-XSRFToken"]' in rendered
    assert 'id="preview-dynamic-resources"' in rendered
    assert "fallback_reason" in rendered
    assert namespace["DynamicResourcePreviewHandler"].post.jupyterhub_authenticated is True


def test_overlay_applies_preview_bound_canonical_resources_and_revalidates(monkeypatch):
    _, kube_spawner, namespace = execute_overlay(monkeypatch)
    payload, options = preview_and_options(namespace, kube_spawner)
    spawner = spawner_for(options)

    asyncio.run(kube_spawner.pre_spawn_hook(spawner))

    assert payload["resource_decision"]["applied_mode"] == "dynamic"
    assert spawner.cpu_guarantee == "500m"
    assert spawner.cpu_limit == "900m"
    assert spawner.mem_guarantee == "768Mi"
    assert spawner.mem_limit == "1024Mi"
    assert spawner.environment["DYNAMIC_RESOURCE_POLICY_HASH"] == namespace[
        "DYNAMIC_RESOURCE_POLICY_HASH"
    ]
    assert len(spawner.extra_annotations["z2jh-context-demo.local/dynamic-policy-hash"]) == 64


@pytest.mark.parametrize(
    "patch",
    [
        {"score": 10},
        {"dataset_size_gb": 5.0},
        {"recommended_profile": "large"},
        {"policy_version": "forged"},
        {"profile_reasons": ["forged audit reason"]},
        {"event_id": "forged-event"},
    ],
)
def test_forged_user_options_cannot_change_preview_bound_decision(monkeypatch, patch):
    _, kube_spawner, namespace = execute_overlay(monkeypatch)
    _, options = preview_and_options(namespace, kube_spawner)
    options.update(patch)

    with pytest.raises(
        ValueError, match="changed after dynamic preview|stale recommendation policy"
    ):
        asyncio.run(kube_spawner.pre_spawn_hook(spawner_for(options)))


def test_forged_mode_and_normalized_resources_are_ignored(monkeypatch):
    _, kube_spawner, namespace = execute_overlay(monkeypatch)
    _, options = preview_and_options(namespace, kube_spawner)
    options.update({
        "resource_selection_mode": "catalog",
        "resources": {"cpu_limit_millicores": 999999},
        "applied_cpu_limit_millicores": 999999,
        "dynamic_policy_version": "forged",
        "dynamic_policy_hash": "f" * 64,
    })
    spawner = spawner_for(options)

    asyncio.run(kube_spawner.pre_spawn_hook(spawner))

    assert spawner.cpu_limit == "900m"
    assert spawner.environment["RESOURCE_SELECTION_MODE_APPLIED"] == "dynamic"


def test_missing_preview_and_replayed_preview_fail_closed(monkeypatch):
    _, kube_spawner, namespace = execute_overlay(monkeypatch)
    _, options = preview_and_options(namespace, kube_spawner)
    missing = deepcopy(options)
    missing.pop("dynamic_preview_id")

    with pytest.raises(ValueError, match="server-side dynamic preview"):
        asyncio.run(kube_spawner.pre_spawn_hook(spawner_for(missing)))

    asyncio.run(kube_spawner.pre_spawn_hook(spawner_for(options)))
    with pytest.raises(ValueError, match="already been used"):
        asyncio.run(kube_spawner.pre_spawn_hook(spawner_for(options)))


def test_policy_hash_change_invalidates_preview_even_if_version_is_unchanged(monkeypatch):
    _, kube_spawner, namespace = execute_overlay(monkeypatch)
    _, options = preview_and_options(namespace, kube_spawner)
    namespace["DYNAMIC_RESOURCE_POLICY_HASH"] = "0" * 64

    with pytest.raises(ValueError, match="policy changed"):
        asyncio.run(kube_spawner.pre_spawn_hook(spawner_for(options)))


def test_preview_is_bound_to_authenticated_user(monkeypatch):
    _, kube_spawner, namespace = execute_overlay(monkeypatch)
    _, options = preview_and_options(namespace, kube_spawner)
    spawner = spawner_for(options)
    spawner.user.name = "mallory"

    with pytest.raises(ValueError, match="different user"):
        asyncio.run(kube_spawner.pre_spawn_hook(spawner))


def test_fallback_is_in_preview_and_manual_override_remains_catalog_bounded(monkeypatch):
    _, kube_spawner, namespace = execute_overlay(monkeypatch)
    payload, options = preview_and_options(namespace, kube_spawner, intent="gpu training")

    assert payload["resource_decision"]["applied_mode"] == "catalog"
    assert "GPU count" in payload["resource_decision"]["fallback_reason"]
    spawner = spawner_for(options)
    asyncio.run(kube_spawner.pre_spawn_hook(spawner))
    assert spawner.cpu_limit == 2
    assert spawner.environment["RESOURCE_SELECTION_MODE_APPLIED"] == "catalog"

    _, override_options = preview_and_options(
        namespace, kube_spawner, action="override"
    )
    override = spawner_for(override_options)
    asyncio.run(kube_spawner.pre_spawn_hook(override))
    assert override.cpu_limit == 2
    assert override.environment["RESOURCE_SELECTION_MODE_APPLIED"] == "catalog"


def test_gpu_requires_compatible_image_and_old_gpu_state_is_cleared(monkeypatch):
    raw = yaml.safe_load((ROOT / "recommender/resource-policy.yaml").read_text())
    raw["allowlist"]["gpu_resources"] = ["nvidia.com/gpu"]
    raw["allowlist"]["gpu_images"] = ["pytorch-deep-learning"]
    raw["dynamic"]["gpu_count"]["max"] = 1
    raw["dynamic"]["quota"]["gpu_count"] = 1
    policy = dynamic_resources.validate_resource_policy(raw)
    _, kube_spawner, namespace = execute_overlay(monkeypatch, policy=policy)
    _, gpu_options = preview_and_options(namespace, kube_spawner, intent="gpu training")
    spawner = spawner_for(gpu_options)

    asyncio.run(kube_spawner.pre_spawn_hook(spawner))
    assert spawner.extra_resource_limits == {"nvidia.com/gpu": 1}

    _, cpu_options = preview_and_options(namespace, kube_spawner, intent="ordinary")
    spawner.user_options = cpu_options
    asyncio.run(kube_spawner.pre_spawn_hook(spawner))
    assert spawner.extra_resource_guarantees == {}
    assert spawner.extra_resource_limits == {}



def test_gpu_image_incompatibility_falls_back_visibly(monkeypatch):
    raw = yaml.safe_load((ROOT / "recommender/resource-policy.yaml").read_text())
    raw["allowlist"]["gpu_resources"] = ["nvidia.com/gpu"]
    raw["allowlist"]["gpu_images"] = ["scipy-data-science"]
    raw["dynamic"]["gpu_count"]["max"] = 1
    raw["dynamic"]["quota"]["gpu_count"] = 1
    policy = dynamic_resources.validate_resource_policy(raw)
    _, _, namespace = execute_overlay(monkeypatch, policy=policy)

    preview = namespace["issue_dynamic_resource_preview"](
        "alice", {"intent": "gpu training", "dataset_size_gb": "0.2", "code_context": ""}
    )

    assert preview["resource_decision"]["applied_mode"] == "catalog"
    assert preview["resource_decision"]["fallback_reason"] == (
        "recommended notebook image is not approved for GPU allocation"
    )


def test_dynamic_preview_rejects_malformed_huge_and_negative_dataset_values(monkeypatch):
    _, _, namespace = execute_overlay(monkeypatch)

    for value in ("bad", "1e309", -1, float("nan")):
        with pytest.raises(ValueError, match="finite non-negative"):
            namespace["issue_dynamic_resource_preview"](
                "alice", {"intent": "x", "dataset_size_gb": value, "code_context": ""}
            )


def test_reprovision_preview_carries_same_one_time_dynamic_binding(monkeypatch):
    _, kube_spawner, namespace = execute_overlay(monkeypatch)
    namespace["context_options_from_form"] = kube_spawner.options_from_form
    reprovision = yaml.safe_load((ROOT / "helm/reprovision-values.yaml").read_text())
    code = reprovision["hub"]["extraConfig"]["10-intent-aware-reprovisioning"]
    exec(compile(code, "helm/reprovision-values.yaml::extraConfig", "exec"), namespace)
    user = SimpleNamespace(
        name="alice",
        escaped_name="alice",
        spawner=SimpleNamespace(
            ready=True,
            pending=None,
            user_options={
                "event_id": "old-event",
                "applied_profile": "small",
                "applied_image_id": "minimal-python",
            },
        ),
    )
    values = {"intent": "large workload", "dataset_size_gb": "2", "code_context": ""}

    preview = namespace["build_reprovision_preview"](user, values)
    dynamic_preview = preview["dynamic_resource_preview"]
    proposed = preview["proposed"]
    options = namespace["build_reprovision_options"](
        user,
        {
            "action": "accept",
            "acknowledge_restart": True,
            "preview_version": preview["preview_version"],
            "expected_current_event_id": "old-event",
            "expected_recommended_profile": proposed["recommended_profile"],
            "expected_recommended_image_id": proposed["recommended_image_id"],
            "expected_policy_version": proposed["policy_version"],
            "expected_catalog_version": proposed["catalog_version"],
            "dynamic_preview_id": dynamic_preview["dynamic_preview_id"],
            **values,
        },
    )

    assert dynamic_preview["resource_decision"]["applied_mode"] == "dynamic"
    assert options["dynamic_preview_id"] == dynamic_preview["dynamic_preview_id"]
    namespace["validate_dynamic_resource_preview"](options, "alice", consume=True)
    with pytest.raises(ValueError, match="already been used"):
        namespace["validate_dynamic_resource_preview"](options, "alice")


def test_browser_equivalent_post_requires_xsrf_and_accepts_correct_header(monkeypatch):
    from tornado import httpserver, netutil, web
    from tornado.httpclient import AsyncHTTPClient, HTTPRequest

    class TestBaseHandler(web.RequestHandler):
        def get_current_user(self):
            return SimpleNamespace(name="alice")

        @property
        def log(self):
            return SimpleNamespace(error=lambda *args, **kwargs: None)

    _, _, namespace = execute_overlay(
        monkeypatch, base_handler=TestBaseHandler, web_module=web
    )
    dynamic_handler = namespace["DynamicResourcePreviewHandler"]

    class TokenHandler(TestBaseHandler):
        def get(self):
            self.write(self.xsrf_token)

    async def scenario():
        app = web.Application(
            [(r"/token", TokenHandler), (r"/dynamic-resource-preview", dynamic_handler)],
            cookie_secret="test-cookie-secret",
            xsrf_cookies=True,
            login_url="/login",
        )
        sockets = netutil.bind_sockets(0, address="127.0.0.1")
        server = httpserver.HTTPServer(app)
        server.add_sockets(sockets)
        port = sockets[0].getsockname()[1]
        client = AsyncHTTPClient()
        try:
            token_response = await client.fetch(f"http://127.0.0.1:{port}/token")
            cookies = SimpleCookie()
            for header in token_response.headers.get_list("Set-Cookie"):
                cookies.load(header)
            xsrf = cookies["_xsrf"].value
            body = json.dumps({
                "intent": "ordinary", "dataset_size_gb": "0.8", "code_context": ""
            })
            missing = await client.fetch(
                HTTPRequest(
                    f"http://127.0.0.1:{port}/dynamic-resource-preview",
                    method="POST",
                    body=body,
                ),
                raise_error=False,
            )
            accepted = await client.fetch(
                HTTPRequest(
                    f"http://127.0.0.1:{port}/dynamic-resource-preview",
                    method="POST",
                    body=body,
                    headers={"Cookie": f"_xsrf={xsrf}", "X-XSRFToken": xsrf},
                ),
                raise_error=False,
            )
            namespace["_base_build_preview_payload"] = lambda _values: (_ for _ in ()).throw(
                RuntimeError("recommender unavailable")
            )
            unavailable = await client.fetch(
                HTTPRequest(
                    f"http://127.0.0.1:{port}/dynamic-resource-preview",
                    method="POST",
                    body=body,
                    headers={"Cookie": f"_xsrf={xsrf}", "X-XSRFToken": xsrf},
                ),
                raise_error=False,
            )
            return missing, accepted, unavailable
        finally:
            server.stop()
            for sock in sockets:
                sock.close()

    missing, accepted, unavailable = asyncio.run(scenario())
    assert missing.code == 403
    assert accepted.code == 200
    assert json.loads(accepted.body)["dynamic_preview_id"]
    assert unavailable.code == 503
    assert json.loads(unavailable.body) == {
        "error": "dynamic resource preview is temporarily unavailable"
    }
