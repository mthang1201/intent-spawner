from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Thread
from unittest.mock import patch

import pytest
import yaml

from recommender import RecommendationRequest, create_recommender
from recommender.deployment import (
    MAX_CONFIGMAP_PAYLOAD_BYTES,
    PACKAGE_VERSION,
    RUNTIME_FILES,
    compute_package_checksum,
    package_payload_size,
    validate_deployment_environment,
    validate_runtime_package,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "recommender"
CHART_VERSION = "4.0.0"
CHECKSUM_ANNOTATION = "intent-spawner.openai.com/recommender-checksum"


class _MockLLMHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.server.requests.append(
            {
                "model": payload.get("model"),
                "authorization": self.headers.get("Authorization"),
            }
        )
        content = json.dumps(
            {
                "profile": "medium",
                "reasons": ["The local mock selected bounded resources."],
                "score": 50,
                "image_id": "minimal-python",
                "image_reasons": ["The default image is sufficient."],
            }
        )
        response = json.dumps(
            {"choices": [{"message": {"content": content}}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format, *args):
        return


@contextmanager
def _mock_llm_service():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockLLMHandler)
    server.requests = []
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/v1/chat/completions", server.requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _base_environment(backend: str) -> dict[str, str]:
    return {
        "RECOMMENDER_BACKEND": backend,
        "RECOMMENDER_PACKAGE_CHECKSUM": compute_package_checksum(PACKAGE_DIR),
        "RECOMMENDER_PACKAGE_VERSION": PACKAGE_VERSION,
    }


def _load_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _render_hub(tmp_path: Path, overlays: list[str], checksum: str | None = None):
    if shutil.which("helm") is None:
        pytest.skip("helm is not installed")
    digest = checksum or compute_package_checksum(PACKAGE_DIR)
    rollout = tmp_path / f"rollout-{digest}.yaml"
    rollout.write_text(
        yaml.safe_dump(
            {
                "hub": {
                    "annotations": {
                        CHECKSUM_ANNOTATION: digest,
                        "intent-spawner.openai.com/recommender-version": PACKAGE_VERSION,
                    },
                    "extraEnv": {
                        "RECOMMENDER_PACKAGE_CHECKSUM": digest,
                        "RECOMMENDER_PACKAGE_VERSION": PACKAGE_VERSION,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    command = [
        "helm",
        "template",
        "context-demo",
        "jupyterhub/jupyterhub",
        "--version",
        CHART_VERSION,
        "--namespace",
        "z2jh-context-demo",
        "--values",
        str(ROOT / "helm/proposed-values.yaml"),
    ]
    for overlay in overlays:
        command.extend(("--values", str(ROOT / overlay)))
    command.extend(("--values", str(rollout)))
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    documents = [item for item in yaml.safe_load_all(result.stdout) if item]
    deployment = next(
        item
        for item in documents
        if item.get("kind") == "Deployment" and item["metadata"]["name"] == "hub"
    )
    return deployment, result.stdout


def _hub_environment(deployment: dict) -> dict[str, dict]:
    container = next(
        item for item in deployment["spec"]["template"]["spec"]["containers"]
        if item["name"] == "hub"
    )
    return {item["name"]: item for item in container["env"]}


def test_base_values_always_mount_and_import_runtime_package():
    values = yaml.safe_load((ROOT / "helm/proposed-values.yaml").read_text())
    hub = values["hub"]
    assert hub["extraEnv"]["PYTHONPATH"] == "/opt/intent-spawner"
    assert hub["extraEnv"]["RECOMMENDER_BACKEND"] == "rule_based"
    assert hub["extraVolumes"] == [
        {
            "name": "recommender-runtime",
            "configMap": {"name": "intent-spawner-recommender"},
        }
    ]
    code = hub["extraConfig"]["00-context-aware-recommender"]
    assert "install_jupyterhub(c)" in code
    assert "recommendResource" not in code


def test_dynamic_install_is_only_a_mode_wrapper():
    source = (ROOT / "scripts/install-dynamic.sh").read_text()
    assert "MODE_VALUES" in source
    assert "install-proposed.sh" in source
    assert "kubectl create configmap" not in source


def test_supported_install_flow_has_locked_backend_auth_and_mode_interfaces():
    source = (ROOT / "scripts/install-proposed.sh").read_text()
    assert "BACKEND_VALUES" in source
    assert "recommender-rule-based-values.yaml" in source
    assert "BACKEND_AUTH_VALUES" in source
    assert "MODE_VALUES" in source
    assert "recommender_package.py\" manifest" in source
    assert "validate_secret_refs.py" in source


@pytest.mark.parametrize(
    ("overlays", "backend"),
    [
        (["helm/recommender-rule-based-values.yaml"], "rule_based"),
        (["helm/recommender-external-llm-values.example.yaml"], "external_llm"),
        (["helm/recommender-self-hosted-llm-values.example.yaml"], "self_hosted_llm"),
        (["helm/recommender-external-llm-mock.example.yaml"], "external_llm"),
        (["helm/recommender-external-llm-mock-fallback.example.yaml"], "external_llm"),
        (["helm/recommender-self-hosted-llm-mock.example.yaml"], "self_hosted_llm"),
        (["helm/recommender-p2-values.yaml"], "p2"),
        (["helm/recommender-p3-values.yaml"], "p3"),
    ],
)
def test_all_backend_values_render_with_explicit_configuration(
    tmp_path, overlays, backend
):
    deployment, _ = _render_hub(tmp_path, overlays)
    env = _hub_environment(deployment)
    assert env["RECOMMENDER_BACKEND"]["value"] == backend
    assert deployment["spec"]["template"]["metadata"]["annotations"][
        CHECKSUM_ANNOTATION
    ] == compute_package_checksum(PACKAGE_DIR)


def test_external_secret_reference_is_required_and_no_api_key_is_rendered(tmp_path):
    deployment, manifest = _render_hub(
        tmp_path, ["helm/recommender-external-llm-values.example.yaml"]
    )
    api_key = _hub_environment(deployment)["EXTERNAL_LLM_API_KEY"]
    assert api_key == {
        "name": "EXTERNAL_LLM_API_KEY",
        "valueFrom": {
            "secretKeyRef": {
                "name": "intent-spawner-external-llm",
                "key": "api-key",
                "optional": False,
            }
        },
    }
    assert "Bearer " not in manifest
    assert "replace-with-api-key" not in manifest


def test_self_hosted_auth_overlay_renders_required_secret_reference(tmp_path):
    deployment, _ = _render_hub(
        tmp_path,
        [
            "helm/recommender-self-hosted-llm-values.example.yaml",
            "helm/recommender-self-hosted-auth-values.example.yaml",
        ],
    )
    env = _hub_environment(deployment)
    assert env["SELF_HOSTED_LLM_AUTH_REQUIRED"]["value"] == "true"
    assert env["SELF_HOSTED_LLM_API_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "intent-spawner-self-hosted-llm",
        "key": "api-key",
        "optional": False,
    }


def test_self_hosted_without_auth_does_not_render_credential_environment(tmp_path):
    deployment, _ = _render_hub(
        tmp_path, ["helm/recommender-self-hosted-llm-values.example.yaml"]
    )
    assert "SELF_HOSTED_LLM_API_KEY" not in _hub_environment(deployment)


def test_required_secret_and_key_preflight_fail_clearly(monkeypatch):
    module = _load_script_module(
        "validate_secret_refs_test", ROOT / "scripts/validate_secret_refs.py"
    )
    values = ROOT / "helm/recommender-external-llm-values.example.yaml"

    def missing_secret(namespace, name):
        raise RuntimeError(f"required Kubernetes Secret {name!r} was not found")

    monkeypatch.setattr(module, "load_secret_metadata", missing_secret)
    with pytest.raises(RuntimeError, match="was not found"):
        module.validate_secret_refs(values, "test")

    monkeypatch.setattr(module, "load_secret_metadata", lambda namespace, name: {"data": {}})
    with pytest.raises(RuntimeError, match="required key 'api-key' is missing"):
        module.validate_secret_refs(values, "test")


def test_runtime_configmap_allowlist_is_small_and_excludes_non_runtime_files():
    assert package_payload_size(PACKAGE_DIR) < MAX_CONFIGMAP_PAYLOAD_BYTES
    assert "deployment.py" in RUNTIME_FILES
    assert "dynamic_resources.py" in RUNTIME_FILES
    assert "resource-policy.yaml" in RUNTIME_FILES
    assert "token_pricing.py" in RUNTIME_FILES
    assert "p2_backend.py" in RUNTIME_FILES
    assert "p3_backend.py" in RUNTIME_FILES
    assert "p3_reranker.py" in RUNTIME_FILES
    assert "local_embeddings.py" in RUNTIME_FILES
    assert "local_structured_intent.py" in RUNTIME_FILES
    assert not any(name.startswith("test_") for name in RUNTIME_FILES)
    assert not any("cache" in name or name.endswith(".md") for name in RUNTIME_FILES)


def test_generated_runtime_package_imports_in_isolation(tmp_path):
    package_copy = tmp_path / "recommender"
    package_copy.mkdir()
    for name in RUNTIME_FILES:
        shutil.copyfile(PACKAGE_DIR / name, package_copy / name)

    result = subprocess.run(
        [sys.executable, "-c", "import recommender; print(recommender.PACKAGE_VERSION)"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
    )
    assert result.stdout.strip() == PACKAGE_VERSION


def test_missing_runtime_configmap_content_fails_clearly(tmp_path):
    with pytest.raises(RuntimeError, match="ConfigMap is missing required runtime files"):
        validate_runtime_package(
            tmp_path,
            expected_checksum="0" * 64,
            expected_version=PACKAGE_VERSION,
        )


def test_checksum_is_stable_for_identical_content_and_changes_with_content(tmp_path):
    package_copy = tmp_path / "recommender"
    package_copy.mkdir()
    for name in RUNTIME_FILES:
        shutil.copyfile(PACKAGE_DIR / name, package_copy / name)
    first = compute_package_checksum(package_copy)
    second = compute_package_checksum(package_copy)
    assert first == second
    with (package_copy / "models.py").open("a", encoding="utf-8") as handle:
        handle.write("\n# simulated package rollout change\n")
    assert compute_package_checksum(package_copy) != first


def test_configmap_change_changes_hub_pod_template_and_identical_content_does_not(
    tmp_path,
):
    first_checksum = compute_package_checksum(PACKAGE_DIR)
    first, _ = _render_hub(
        tmp_path, ["helm/recommender-rule-based-values.yaml"], first_checksum
    )
    identical, _ = _render_hub(
        tmp_path, ["helm/recommender-rule-based-values.yaml"], first_checksum
    )
    changed_checksum = "f" * 64 if first_checksum != "f" * 64 else "e" * 64
    changed, _ = _render_hub(
        tmp_path, ["helm/recommender-rule-based-values.yaml"], changed_checksum
    )
    # `helm template` generates a fresh internal proxy token when no release is
    # present, so normalize only that unrelated chart-owned checksum.
    normalized = []
    for deployment in (first, identical, changed):
        template = deepcopy(deployment["spec"]["template"])
        template["metadata"]["annotations"].pop("checksum/secret", None)
        normalized.append(template)
    assert normalized[0] == normalized[1]
    assert normalized[0] != normalized[2]
    assert changed["spec"]["template"]["metadata"]["annotations"][
        CHECKSUM_ANNOTATION
    ] == changed_checksum


def test_unknown_backend_and_missing_external_credential_fail_startup():
    unknown = _base_environment("not_registered")
    with pytest.raises(ValueError, match="unknown recommender backend"):
        validate_deployment_environment(unknown, package_dir=PACKAGE_DIR)

    missing_key = {
        **_base_environment("external_llm"),
        "EXTERNAL_LLM_ENDPOINT": "https://llm.example.invalid/v1/chat/completions",
        "EXTERNAL_LLM_MODEL": "mock-model",
    }
    with pytest.raises(ValueError, match="EXTERNAL_LLM_API_KEY is required"):
        validate_deployment_environment(missing_key, package_dir=PACKAGE_DIR)

    missing_p3_key = {
        **_base_environment("p3"),
        "P2_STRUCTURED_EXTRACTOR": "local",
        "P3_RERANKER_MODE": "llm",
        "EXTERNAL_LLM_ENDPOINT": "https://llm.example.invalid/v1/chat/completions",
        "EXTERNAL_LLM_MODEL": "mock-reranker",
    }
    with pytest.raises(ValueError, match="EXTERNAL_LLM_API_KEY is required"):
        validate_deployment_environment(missing_p3_key, package_dir=PACKAGE_DIR)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("EXTERNAL_LLM_TIMEOUT", "0", "timeout must be a positive number"),
        ("EXTERNAL_LLM_TOTAL_TIMEOUT", "0", "total_timeout must be a positive"),
        ("EXTERNAL_LLM_MAX_RETRIES", "-1", "max_retries must be"),
        ("EXTERNAL_LLM_RETRY_BACKOFF_SECONDS", "-1", "retry_backoff_seconds"),
        ("EXTERNAL_LLM_TEMPERATURE", "3", "temperature must be between"),
    ],
)
def test_invalid_network_limits_fail_during_startup(name, value, message):
    environ = {
        **_base_environment("external_llm"),
        "EXTERNAL_LLM_ENDPOINT": "https://llm.example.invalid/v1/chat/completions",
        "EXTERNAL_LLM_MODEL": "mock-model",
        "EXTERNAL_LLM_API_KEY": "dummy-test-only",
        name: value,
    }
    validate_deployment_environment(environ, package_dir=PACKAGE_DIR)
    with patch.dict(os.environ, environ, clear=True):
        with pytest.raises(ValueError, match=message):
            create_recommender()


def test_plain_http_requires_explicit_development_or_trusted_path_opt_in():
    external = {
        **_base_environment("external_llm"),
        "EXTERNAL_LLM_ENDPOINT": "http://127.0.0.1:8080/v1/chat/completions",
        "EXTERNAL_LLM_MODEL": "mock-model",
        "EXTERNAL_LLM_API_KEY": "dummy-test-only",
    }
    with pytest.raises(ValueError, match="must use HTTPS"):
        validate_deployment_environment(external, package_dir=PACKAGE_DIR)

    self_hosted = {
        **_base_environment("self_hosted_llm"),
        "SELF_HOSTED_LLM_ENDPOINT": "http://inference.svc/v1/chat/completions",
        "SELF_HOSTED_LLM_MODEL": "mock-model",
    }
    with pytest.raises(ValueError, match="must use HTTPS"):
        validate_deployment_environment(self_hosted, package_dir=PACKAGE_DIR)


def test_all_backend_startup_configurations_are_ready_with_local_mock_service():
    request = RecommendationRequest(intent="analyze a CSV", dataset_size_gb=0.2)
    ready = []
    with _mock_llm_service() as (endpoint, requests):
        configurations = [
            _base_environment("rule_based"),
            {
                **_base_environment("external_llm"),
                "EXTERNAL_LLM_ENDPOINT": endpoint,
                "EXTERNAL_LLM_MODEL": "external-mock",
                "EXTERNAL_LLM_API_KEY": "dummy-external-test-only",
                "EXTERNAL_LLM_ALLOW_INSECURE_HTTP": "true",
                "EXTERNAL_LLM_MAX_RETRIES": "0",
            },
            {
                **_base_environment("self_hosted_llm"),
                "SELF_HOSTED_LLM_ENDPOINT": endpoint,
                "SELF_HOSTED_LLM_MODEL": "self-hosted-mock",
                "SELF_HOSTED_LLM_ALLOW_INSECURE_HTTP": "true",
                "SELF_HOSTED_LLM_AUTH_REQUIRED": "true",
                "SELF_HOSTED_LLM_API_KEY": "dummy-self-hosted-test-only",
                "SELF_HOSTED_LLM_MAX_RETRIES": "0",
            },
            _base_environment("p2"),
            {
                **_base_environment("p3"),
                "P2_STRUCTURED_EXTRACTOR": "local",
                "P3_RERANKER_MODE": "llm",
                "EXTERNAL_LLM_ENDPOINT": endpoint,
                "EXTERNAL_LLM_MODEL": "p3-mock",
                "EXTERNAL_LLM_API_KEY": "dummy-p3-test-only",
                "EXTERNAL_LLM_ALLOW_INSECURE_HTTP": "true",
                "EXTERNAL_LLM_MAX_RETRIES": "0",
            },
        ]
        for environ in configurations:
            metadata = validate_deployment_environment(environ, package_dir=PACKAGE_DIR)
            with patch.dict(os.environ, environ, clear=True):
                recommendation = create_recommender().recommend(request)
            assert recommendation.profile in {"small", "medium", "large", "gpu_or_large"}
            ready.append(metadata.backend)

    assert ready == ["rule_based", "external_llm", "self_hosted_llm", "p2", "p3"]
    assert [item["model"] for item in requests] == [
        "external-mock",
        "self-hosted-mock",
        "p3-mock",
    ]
    assert [item["authorization"] for item in requests] == [
        "Bearer dummy-external-test-only",
        "Bearer dummy-self-hosted-test-only",
        "Bearer dummy-p3-test-only",
    ]
