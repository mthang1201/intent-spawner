from __future__ import annotations

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
from threading import Thread
import time
from types import SimpleNamespace

from tornado import httpserver, netutil, web
from tornado.httpclient import AsyncHTTPClient, HTTPRequest

from recommender import (
    AsyncRecommendationExecutor,
    ExternalLLMConfig,
    ExternalLLMRecommender,
)
from test_config_validation import load_proposed_extra_config


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
        content = self.server.content
        response = json.dumps(
            {"choices": [{"message": {"content": content}}]}
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
def _mock_inference(*, delay=0.0, content=VALID_OUTPUT):
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


class _PreviewBaseHandler(web.RequestHandler):
    log_records = []

    def set_default_headers(self):
        pass

    def get_current_user(self):
        return SimpleNamespace(name="local-validation")

    @property
    def log(self):
        return SimpleNamespace(
            info=lambda *args, **kwargs: self.log_records.append(("info", args)),
            error=lambda *args, **kwargs: self.log_records.append(("error", args)),
        )


async def _serve_preview_requests(namespace, count=1):
    handler = namespace["RecommendationPreviewHandler"]
    app = web.Application([(r"/preview", handler)], login_url="/login")
    sockets = netutil.bind_sockets(0, address="127.0.0.1")
    server = httpserver.HTTPServer(app)
    server.add_sockets(sockets)
    port = sockets[0].getsockname()[1]
    client = AsyncHTTPClient()
    body = json.dumps(
        {"intent": "demo", "dataset_size_gb": 0.1, "code_context": ""}
    )
    heartbeat_ticks = 0
    finished = False

    async def heartbeat():
        nonlocal heartbeat_ticks
        while not finished:
            heartbeat_ticks += 1
            await asyncio.sleep(0.01)

    heartbeat_task = asyncio.create_task(heartbeat())
    started = time.monotonic()
    try:
        responses = await asyncio.gather(
            *(
                client.fetch(
                    HTTPRequest(
                        f"http://127.0.0.1:{port}/preview",
                        method="POST",
                        body=body,
                        headers={"Content-Type": "application/json"},
                    ),
                    raise_error=False,
                )
                for _ in range(count)
            )
        )
        elapsed = time.monotonic() - started
    finally:
        finished = True
        await heartbeat_task
        server.stop()
        for listening_socket in sockets:
            listening_socket.close()
    return elapsed, heartbeat_ticks, [json.loads(response.body) for response in responses]


def _configured_preview(
    endpoint, *, delay_budget=1.0, attempt_timeout=0.08, attempts=0, api_key=""
):
    _, namespace = load_proposed_extra_config(
        base_handler=_PreviewBaseHandler,
        web_module=web,
        values_path="results/recommender-audit-2026-08-06/final-release-values.yaml",
    )
    old_executor = namespace.get("NETWORK_RECOMMENDATION_EXECUTOR")
    backend = ExternalLLMRecommender(
        config=ExternalLLMConfig(
            endpoint=endpoint,
            model="local-mock",
            api_key=api_key,
            allow_insecure_http=bool(api_key),
            timeout=min(attempt_timeout, delay_budget),
            total_timeout=delay_budget,
            max_retries=attempts,
            max_concurrent_recommendations=2,
        )
    )
    executor = AsyncRecommendationExecutor(2)
    namespace["ACTIVE_RECOMMENDER"] = backend
    namespace["NETWORK_RECOMMENDATION_EXECUTOR"] = executor
    return namespace, old_executor, executor


def test_two_live_preview_requests_overlap_and_hub_loop_remains_responsive():
    secret = "local-validation-secret"
    _PreviewBaseHandler.log_records = []
    with _mock_inference(delay=0.2) as (endpoint, inference):
        namespace, old_executor, executor = _configured_preview(
            endpoint, delay_budget=0.8, attempt_timeout=0.5, api_key=secret
        )
        try:
            elapsed, heartbeat_ticks, payloads = asyncio.run(
                _serve_preview_requests(namespace, count=2)
            )
        finally:
            executor.shutdown()
            if old_executor:
                old_executor.shutdown()

    assert elapsed < 0.35
    assert heartbeat_ticks >= 10
    assert all(payload["metadata"]["fallback_used"] is False for payload in payloads)
    assert inference.authorization_headers == [f"Bearer {secret}"] * 2
    assert secret not in repr(_PreviewBaseHandler.log_records)
    print(json.dumps({
        "case": "two_concurrent_previews",
        "request_count": 2,
        "mock_delay_seconds": 0.2,
        "total_elapsed_seconds": round(elapsed, 6),
        "event_loop_heartbeat_ticks": heartbeat_ticks,
        "secret_present_in_logs": secret in repr(_PreviewBaseHandler.log_records),
    }, sort_keys=True))


def test_live_preview_fallback_telemetry_for_timeout_malformed_and_connection_failure():
    observed = {}

    with _mock_inference(delay=0.2) as (endpoint, _):
        namespace, old_executor, executor = _configured_preview(
            endpoint, delay_budget=0.12, attempts=3
        )
        try:
            elapsed, _, payloads = asyncio.run(_serve_preview_requests(namespace))
        finally:
            executor.shutdown()
            if old_executor:
                old_executor.shutdown()
        observed["timeout"] = (elapsed, payloads[0]["metadata"])

    with _mock_inference(content="not JSON") as (endpoint, _):
        namespace, old_executor, executor = _configured_preview(endpoint)
        try:
            _, _, payloads = asyncio.run(_serve_preview_requests(namespace))
        finally:
            executor.shutdown()
            if old_executor:
                old_executor.shutdown()
        observed["malformed"] = payloads[0]["metadata"]

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    unavailable_port = probe.getsockname()[1]
    probe.close()
    namespace, old_executor, executor = _configured_preview(
        f"http://127.0.0.1:{unavailable_port}/v1/chat/completions"
    )
    try:
        _, _, payloads = asyncio.run(_serve_preview_requests(namespace))
    finally:
        executor.shutdown()
        if old_executor:
            old_executor.shutdown()
    observed["connection"] = payloads[0]["metadata"]

    timeout_elapsed, timeout_metadata = observed["timeout"]
    assert timeout_elapsed < 0.2
    assert timeout_metadata["fallback_used"] is True
    assert timeout_metadata["deadline_exhausted"] is True
    assert observed["malformed"]["fallback_error_category"] == "invalid_response"
    assert observed["connection"]["fallback_error_category"] == "transport_error"
    assert all(
        item["requested_backend"] == "external_llm"
        and item["effective_backend"] == "rule_based"
        for item in (
            timeout_metadata,
            observed["malformed"],
            observed["connection"],
        )
    )
    print(json.dumps({
        "case": "fallback_failures",
        "timeout_elapsed_seconds": round(timeout_elapsed, 6),
        "timeout": timeout_metadata,
        "malformed": observed["malformed"],
        "connection": observed["connection"],
    }, sort_keys=True))


def test_fallback_metadata_is_in_spawn_audit_and_bounded_pod_annotations():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    unavailable_port = probe.getsockname()[1]
    probe.close()
    namespace, old_executor, executor = _configured_preview(
        f"http://127.0.0.1:{unavailable_port}/v1/chat/completions"
    )
    log_records = []
    formdata = {
        "intent": ["basic Python"],
        "dataset_size_gb": ["0.1"],
        "code_context": [""],
        "preview_version": [namespace["PREVIEW_VERSION"]],
        "decision_action": ["accept"],
    }
    user_options = namespace["context_options_from_form"](formdata)
    spawner = SimpleNamespace(
        user_options=user_options,
        environment={},
        extra_annotations={},
        log=SimpleNamespace(
            info=lambda *args, **kwargs: log_records.append((args, kwargs))
        ),
    )
    try:
        asyncio.run(namespace["context_pre_spawn_hook"](spawner))
    finally:
        executor.shutdown()
        if old_executor:
            old_executor.shutdown()

    metadata = spawner.user_options["recommendation_metadata"]
    assert metadata["requested_backend"] == "external_llm"
    assert metadata["effective_backend"] == "rule_based"
    assert spawner.extra_annotations[
        "z2jh-context-demo.local/fallback-error-category"
    ] == "transport_error"
    assert all(
        len(str(value)) <= 63
        for key, value in spawner.extra_annotations.items()
        if any(token in key for token in ("backend", "fallback", "attempt", "elapsed", "deadline"))
    )
    audit = json.loads(log_records[-1][0][1])
    assert audit["recommendation_metadata"] == metadata
