from __future__ import annotations

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
from threading import Thread
import time

from recommender import ExternalLLMConfig, ExternalLLMRecommender
from recommender.deployment import DeploymentMetadata
from recommender.jupyterhub_integration import RecommendationPreviewRuntime
from recommender.rule_based import load_image_catalog
from test_config_validation import confirm, spawner


VALID_OUTPUT = json.dumps(
    {
        "profile": "medium",
        "reasons": ["Moderate resources are appropriate."],
        "score": 50,
        "image_id": "minimal-python",
        "image_reasons": ["The default image is sufficient."],
    }
)


class _MockInferenceHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.server.authorization_headers.append(self.headers.get("Authorization"))
        time.sleep(self.server.delay)
        response = json.dumps(
            {"choices": [{"message": {"content": self.server.content}}]}
        ).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        return


@contextmanager
def mock_inference(*, delay=0.0, content=VALID_OUTPUT):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockInferenceHandler)
    server.delay = delay
    server.content = content
    server.authorization_headers = []
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/v1/chat/completions", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def external_runtime(endpoint, *, timeout=0.5, total_timeout=1.0, retries=0, api_key=""):
    catalog = load_image_catalog()
    backend = ExternalLLMRecommender(
        config=ExternalLLMConfig(
            endpoint=endpoint,
            model="simulated-local-mock",
            api_key=api_key,
            allow_insecure_http=True,
            timeout=timeout,
            total_timeout=total_timeout,
            max_retries=retries,
            max_concurrent_recommendations=2,
        ),
        catalog=catalog,
    )
    return RecommendationPreviewRuntime(
        deployment=DeploymentMetadata(
            backend="external_llm",
            backend_version="external-llm-v2",
            package_version="intent-spawner-recommender-v2",
            package_checksum="b" * 64,
        ),
        catalog=catalog,
        backend=backend,
    )


def test_two_simulated_network_previews_overlap_without_blocking_event_loop():
    secret = "simulated-secret"
    with mock_inference(delay=0.2) as (endpoint, server):
        runtime = external_runtime(endpoint, api_key=secret)

        async def scenario():
            ticks = 0
            done = False

            async def heartbeat():
                nonlocal ticks
                while not done:
                    ticks += 1
                    await asyncio.sleep(0.01)

            heartbeat_task = asyncio.create_task(heartbeat())
            started = time.monotonic()
            payloads = await asyncio.gather(
                runtime.issue("alice", {"intent": "one"}),
                runtime.issue("bob", {"intent": "two"}),
            )
            elapsed = time.monotonic() - started
            done = True
            await heartbeat_task
            return elapsed, ticks, payloads

        elapsed, ticks, payloads = asyncio.run(scenario())
        runtime.executor.shutdown()

    assert elapsed < 0.35
    assert ticks >= 10
    assert all(not item["metadata"]["fallback_used"] for item in payloads)
    assert server.authorization_headers == [f"Bearer {secret}"] * 2
    assert secret not in json.dumps(payloads)


def test_simulated_timeout_malformed_and_connection_failure_fallback_metadata():
    observed = {}
    with mock_inference(delay=0.2) as (endpoint, _):
        runtime = external_runtime(endpoint, timeout=0.08, total_timeout=0.12, retries=3)
        observed["timeout"] = asyncio.run(runtime.issue("alice", {"intent": "x"}))["metadata"]
        runtime.executor.shutdown()
    with mock_inference(content="not JSON") as (endpoint, _):
        runtime = external_runtime(endpoint)
        observed["malformed"] = asyncio.run(runtime.issue("alice", {"intent": "x"}))["metadata"]
        runtime.executor.shutdown()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    runtime = external_runtime(f"http://127.0.0.1:{port}/v1/chat/completions")
    observed["connection"] = asyncio.run(runtime.issue("alice", {"intent": "x"}))["metadata"]
    runtime.executor.shutdown()

    assert observed["timeout"]["fallback_used"] is True
    assert observed["timeout"]["deadline_exhausted"] is True
    assert observed["malformed"]["fallback_error_category"] == "invalid_response"
    assert observed["connection"]["fallback_error_category"] == "transport_error"
    assert all("raw_response" not in item for item in observed.values())


def test_fallback_metadata_reaches_only_safe_audit_and_annotations():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    runtime = external_runtime(f"http://127.0.0.1:{port}/v1/chat/completions")
    preview = asyncio.run(runtime.issue("alice", {"intent": "private intent"}))
    options = confirm(runtime, preview)
    target = spawner(options=options)
    asyncio.run(runtime.pre_spawn(target))
    runtime.executor.shutdown()

    assert target.extra_annotations["intent-spawner.local/fallback-category"] == "transport_error"
    assert "private intent" not in json.dumps(target.extra_annotations)
    assert "raw_response" not in json.dumps(target.extra_annotations)
    audit = json.loads(target.logs[-1][1])
    assert audit["fallback_category"] == "transport_error"
    assert "private intent" not in json.dumps(audit)
