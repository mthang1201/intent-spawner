import asyncio
import html
from pathlib import Path
from types import SimpleNamespace

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCIPY_NOTEBOOK_DIGEST = "sha256:e760028814b48e503f8991e20f89ad7ba2725b34ca7d937b104584b78f11169f"

EXPECTED_PROFILE_RESOURCES = {
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


def load_yaml(path: str) -> dict:
    with (ROOT / path).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def parse_cpu_m(value: str) -> int:
    text = str(value)
    if text.endswith("m"):
        return int(text[:-1])
    return int(float(text) * 1000)


def parse_memory_mi(value: str) -> int:
    text = str(value)
    for suffix, factor in (("Gi", 1024), ("Mi", 1), ("G", 1000), ("M", 1000 / 1024)):
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * factor)
    return int(float(text) / 1024 / 1024)


def load_proposed_extra_config() -> tuple[SimpleNamespace, dict]:
    values = load_yaml("helm/proposed-values.yaml")
    code = values["hub"]["extraConfig"]["00-context-aware-recommender"]
    kube_spawner_config = SimpleNamespace()
    namespace = {"c": SimpleNamespace(KubeSpawner=kube_spawner_config)}

    exec(compile(code, "helm/proposed-values.yaml::extraConfig", "exec"), namespace)

    return kube_spawner_config, namespace


def test_baseline_helm_profiles_define_approved_small_medium_large_resources():
    values = load_yaml("helm/baseline-values.yaml")
    profiles = values["singleuser"]["profileList"]

    assert [profile["slug"] for profile in profiles] == ["small", "medium", "large"]
    assert sum(1 for profile in profiles if profile.get("default")) == 1

    for profile in profiles:
        slug = profile["slug"]
        overrides = profile["kubespawner_override"]
        expected = EXPECTED_PROFILE_RESOURCES[slug]

        assert {
            "cpu_guarantee": overrides["cpu_guarantee"],
            "cpu_limit": overrides["cpu_limit"],
            "mem_guarantee": overrides["mem_guarantee"],
            "mem_limit": overrides["mem_limit"],
        } == expected
        assert overrides["environment"]["SELECTED_STATIC_PROFILE"] == slug


def test_proposed_helm_resources_match_baseline_policy_without_static_profile_list():
    values = load_yaml("helm/proposed-values.yaml")
    _, namespace = load_proposed_extra_config()

    assert "profileList" not in values["singleuser"]
    assert namespace["PROFILE_RESOURCES"] == EXPECTED_PROFILE_RESOURCES
    assert set(namespace["PROFILE_RESOURCES"]) == {"small", "medium", "large"}


def test_demo_images_are_digest_pinned_and_no_shared_password_is_committed():
    baseline = load_yaml("helm/baseline-values.yaml")
    proposed = load_yaml("helm/proposed-values.yaml")

    for values in (baseline, proposed):
        assert "DummyAuthenticator" not in values["hub"]["config"]
        assert values["singleuser"]["image"]["tag"].endswith(f"@{SCIPY_NOTEBOOK_DIGEST}")


def test_repository_contains_no_raw_notebook_artifacts():
    assert not list(ROOT.rglob("*.ipynb"))


def test_options_from_form_accepts_jupyterhub_callback_shapes_and_invalid_dataset_size():
    kube_spawner_config, _ = load_proposed_extra_config()
    options_from_form = kube_spawner_config.options_from_form

    assert options_from_form({"dataset_size_gb": ["not-a-number"]}) == {
        "intent": "",
        "dataset_size_gb": 0.0,
        "code_context": "",
    }
    assert options_from_form({"dataset_size_gb": ["-1"]})["dataset_size_gb"] == 0.0
    assert options_from_form(
        SimpleNamespace(),
        {
            "intent": [b"train a model"],
            "dataset_size_gb": [b"1.5"],
            "code_context": [b"model.fit(X, y)"],
        },
    ) == {
        "intent": "train a model",
        "dataset_size_gb": 1.5,
        "code_context": "model.fit(X, y)",
    }


def test_kubespawner_pre_spawn_hook_applies_large_for_gpu_or_large_with_explanation():
    kube_spawner_config, _ = load_proposed_extra_config()
    log_calls = []
    spawner = SimpleNamespace(
        user_options={
            "intent": "deep learning image classifier",
            "dataset_size_gb": 0.2,
            "code_context": "import torch\nmodel.cuda()",
        },
        environment={"EXISTING": "kept"},
        extra_annotations={},
        user=SimpleNamespace(name="pytest-user"),
        log=SimpleNamespace(info=lambda *args, **kwargs: log_calls.append((args, kwargs))),
    )

    asyncio.run(kube_spawner_config.pre_spawn_hook(spawner))

    assert spawner.cpu_guarantee == EXPECTED_PROFILE_RESOURCES["large"]["cpu_guarantee"]
    assert spawner.cpu_limit == EXPECTED_PROFILE_RESOURCES["large"]["cpu_limit"]
    assert spawner.mem_guarantee == EXPECTED_PROFILE_RESOURCES["large"]["mem_guarantee"]
    assert spawner.mem_limit == EXPECTED_PROFILE_RESOURCES["large"]["mem_limit"]
    assert spawner.environment["EXISTING"] == "kept"
    assert spawner.environment["RECOMMENDED_PROFILE"] == "gpu_or_large"
    assert "GPU/deep-learning context detected" in spawner.environment["RECOMMENDATION_REASONS"]
    assert "CONTEXT_INTENT" not in spawner.environment
    assert "deep learning image classifier" not in str(spawner.environment)
    assert "pytest-user" not in str(log_calls)
    assert spawner.extra_annotations["z2jh-context-demo.local/recommended-profile"] == "gpu_or_large"
    assert spawner.extra_annotations["z2jh-context-demo.local/recommendation-reasons"] == html.escape(
        spawner.environment["RECOMMENDATION_REASONS"]
    )[:240]


def test_kubernetes_demo_manifests_are_valid_yaml_and_quota_constrains_large_overrequesting():
    idle_small = load_yaml("k8s/idle-small-pod.yaml")
    idle_large = load_yaml("k8s/idle-large-pod.yaml")
    quota = load_yaml("k8s/resource-quota.yaml")

    assert idle_small["kind"] == "Pod"
    assert idle_large["kind"] == "Pod"
    assert quota["kind"] == "ResourceQuota"

    small_requests = idle_small["spec"]["containers"][0]["resources"]["requests"]
    large_requests = idle_large["spec"]["containers"][0]["resources"]["requests"]
    hard = quota["spec"]["hard"]

    assert parse_cpu_m(small_requests["cpu"]) == 100
    assert parse_memory_mi(small_requests["memory"]) == 256
    assert parse_cpu_m(large_requests["cpu"]) == 1500
    assert parse_memory_mi(large_requests["memory"]) == 1536

    assert parse_cpu_m(large_requests["cpu"]) * 2 > parse_cpu_m(hard["requests.cpu"])
    assert parse_memory_mi(large_requests["memory"]) * 2 > parse_memory_mi(hard["requests.memory"])
    assert parse_cpu_m(small_requests["cpu"]) * 2 <= parse_cpu_m(hard["requests.cpu"])
    assert parse_memory_mi(small_requests["memory"]) * 2 <= parse_memory_mi(hard["requests.memory"])
