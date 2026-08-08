from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

from recommender import (
    DEFAULT_REGISTRY,
    PROFILES,
    ExternalLLMConfig,
    PolicyValidator,
    RecommendationRequest,
    SelfHostedLLMConfig,
    SpawnRecommendation,
    create_recommender,
    load_image_catalog,
)


MODEL_OUTPUT = {
    "profile": "large",
    "reasons": ["Training and the dataset size require additional memory."],
    "score": 80,
    "image_id": "scipy-data-science",
    "image_reasons": ["The workload uses pandas and scikit-learn."],
}


class _InferenceHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(content_length))
        self.server.recorded_requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "payload": payload,
            }
        )
        response = json.dumps(
            {"choices": [{"message": {"content": json.dumps(MODEL_OUTPUT)}}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format, *args):
        return


@contextmanager
def _inference_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _InferenceHandler)
    server.recorded_requests = []
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/v1/chat/completions", server.recorded_requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_all_configured_backends_share_interface_and_policy_schema():
    request = RecommendationRequest(
        intent="Train a classifier",
        dataset_size_gb=2.0,
        code_context="import pandas as pd\nfrom sklearn.linear_model import LogisticRegression",
    )
    catalog = load_image_catalog()
    policy_validator = PolicyValidator.from_catalog(
        profiles=PROFILES,
        catalog=catalog,
    )

    with _inference_server() as (endpoint, recorded_requests):
        backends = {
            "rule_based": create_recommender(
                None,
                environ={"RECOMMENDER_BACKEND": "rule_based"},
            ),
            "external_llm": create_recommender(
                None,
                environ={"RECOMMENDER_BACKEND": "external_llm"},
                config=ExternalLLMConfig(
                    endpoint=endpoint,
                    model="external-model",
                    api_key="external-token",
                    allow_insecure_http=True,
                    max_retries=0,
                ),
            ),
            "self_hosted_llm": create_recommender(
                None,
                environ={"RECOMMENDER_BACKEND": "self_hosted_llm"},
                config=SelfHostedLLMConfig(
                    endpoint=endpoint,
                    model="local-model",
                    api_key="local-token",
                    max_retries=0,
                ),
            ),
        }

        recommendations = {
            name: policy_validator.validate(backend.recommend(request))
            for name, backend in backends.items()
        }

    assert {"rule_based", "external_llm", "self_hosted_llm"}.issubset(
        DEFAULT_REGISTRY.names
    )
    assert all(
        isinstance(recommendation, SpawnRecommendation)
        for recommendation in recommendations.values()
    )
    schema_keys = [
        tuple(recommendation.to_unified_dict())
        for recommendation in recommendations.values()
    ]
    assert schema_keys[1:] == schema_keys[:-1]
    assert {
        name: recommendation.backend_name
        for name, recommendation in recommendations.items()
    } == {
        "rule_based": "rule_based",
        "external_llm": "external_llm",
        "self_hosted_llm": "self_hosted_llm",
    }
    assert [item["payload"]["model"] for item in recorded_requests] == [
        "external-model",
        "local-model",
    ]
    assert [item["authorization"] for item in recorded_requests] == [
        "Bearer external-token",
        "Bearer local-token",
    ]
