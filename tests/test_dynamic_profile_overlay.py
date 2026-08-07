import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_overlay_code() -> tuple[dict, str]:
    with (ROOT / "helm" / "dynamic-values.yaml").open(encoding="utf-8") as handle:
        values = yaml.safe_load(handle)
    code = values["hub"]["extraConfig"]["01-policy-bounded-dynamic-resources"]
    return values, code


def execute_overlay(monkeypatch):
    values, code = load_overlay_code()
    monkeypatch.setenv("RESOURCE_SELECTION_MODE", values["hub"]["extraEnv"]["RESOURCE_SELECTION_MODE"])

    async def catalog_hook(spawner):
        resources = PROFILE_RESOURCES[spawner.user_options["applied_profile"]]
        spawner.cpu_guarantee = resources["cpu_guarantee"]
        spawner.cpu_limit = resources["cpu_limit"]
        spawner.mem_guarantee = resources["mem_guarantee"]
        spawner.mem_limit = resources["mem_limit"]

    kube_spawner = SimpleNamespace(pre_spawn_hook=catalog_hook)
    namespace = {
        "c": SimpleNamespace(KubeSpawner=kube_spawner),
        "PROFILE_RESOURCES": PROFILE_RESOURCES,
        "json": json,
    }
    exec(compile(code, "helm/dynamic-values.yaml::extraConfig", "exec"), namespace)
    return values, kube_spawner, namespace


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


class LogCapture:
    def __init__(self):
        self.records = []

    def info(self, message, value):
        self.records.append((message, value))


def spawner_for(*, action="accept", profile="medium", score=2, dataset=0.8):
    return SimpleNamespace(
        user_options={
            "decision_action": action,
            "event_id": "test-event",
            "recommended_profile": profile,
            "applied_profile": "large" if profile == "gpu_or_large" else profile,
            "score": score,
            "dataset_size_gb": dataset,
        },
        environment={},
        extra_annotations={},
        extra_resource_guarantees={},
        extra_resource_limits={},
        log=LogCapture(),
    )


def test_overlay_is_explicit_opt_in_and_python_config_compiles(monkeypatch):
    values, _, namespace = execute_overlay(monkeypatch)

    assert values["hub"]["extraEnv"]["RESOURCE_SELECTION_MODE"] == "dynamic"
    assert values["hub"]["extraEnv"]["PYTHONPATH"] == "/opt/intent-spawner"
    assert values["hub"]["extraVolumes"][0]["configMap"]["name"] == (
        "intent-spawner-recommender"
    )
    assert values["hub"]["extraVolumeMounts"][0] == {
        "name": "recommender-runtime",
        "mountPath": "/opt/intent-spawner/recommender",
        "readOnly": True,
    }
    assert namespace["RESOURCE_SELECTOR"].mode == "dynamic"


def test_overlay_applies_validated_dynamic_resources_after_catalog_hook(monkeypatch):
    _, kube_spawner, _ = execute_overlay(monkeypatch)
    spawner = spawner_for()

    asyncio.run(kube_spawner.pre_spawn_hook(spawner))

    assert spawner.cpu_guarantee == 0.5
    assert spawner.cpu_limit == 0.9
    assert spawner.mem_guarantee == "768Mi"
    assert spawner.mem_limit == "1024Mi"
    assert spawner.environment["RESOURCE_SELECTION_MODE_APPLIED"] == "dynamic"
    assert spawner.extra_annotations[
        "z2jh-context-demo.local/dynamic-policy-version"
    ] == "dynamic-resource-policy-v1"
    audit = json.loads(spawner.log.records[-1][1])
    assert audit["event"] == "resource_mode_decision"
    assert audit["resources"]["cpu_limit_millicores"] == 900


def test_overlay_falls_back_to_catalog_for_disallowed_gpu(monkeypatch):
    _, kube_spawner, _ = execute_overlay(monkeypatch)
    spawner = spawner_for(profile="gpu_or_large", score=99, dataset=0.2)

    asyncio.run(kube_spawner.pre_spawn_hook(spawner))

    assert spawner.cpu_limit == 2
    assert spawner.mem_limit == "2G"
    assert spawner.environment["RESOURCE_SELECTION_MODE_APPLIED"] == "catalog"
    assert "GPU count" in spawner.extra_annotations[
        "z2jh-context-demo.local/dynamic-fallback"
    ]


def test_manual_override_always_preserves_catalog_resources(monkeypatch):
    _, kube_spawner, _ = execute_overlay(monkeypatch)
    spawner = spawner_for(action="override", profile="medium")

    asyncio.run(kube_spawner.pre_spawn_hook(spawner))

    assert spawner.cpu_limit == 1
    assert spawner.mem_limit == "1G"
    assert spawner.environment["RESOURCE_SELECTION_MODE_APPLIED"] == "catalog"
    assert spawner.extra_annotations[
        "z2jh-context-demo.local/dynamic-fallback"
    ] == "manual catalog override"
