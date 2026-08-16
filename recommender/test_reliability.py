from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from recommender import (
    AsyncRecommendationExecutor,
    ExternalLLMConfig,
    ExternalLLMFallbackError,
    ExternalLLMRecommender,
    RecommendationRequest,
)
from recommender.external_llm import LLMTimeoutError
from recommender.reliability import RecommendationMetadata


ROOT = Path(__file__).resolve().parents[1]


def test_operational_metadata_excludes_raw_provider_response():
    metadata = RecommendationMetadata(
        requested_backend="external_llm",
        effective_backend="external_llm",
        fallback_used=False,
        fallback_error_category=None,
        attempt_count=1,
        total_elapsed_seconds=0.1,
        timed_out=False,
        deadline_exhausted=False,
        raw_response='{"echoed_intent":"sensitive"}',
    )

    assert metadata.to_dict()["raw_response"] == '{"echoed_intent":"sensitive"}'
    assert "raw_response" not in metadata.to_operational_dict()


def _valid_output() -> str:
    return json.dumps(
        {
            "profile": "medium",
            "reasons": ["Moderate resources are appropriate."],
            "score": 50,
            "image_id": "minimal-python",
            "image_reasons": ["The default image is sufficient."],
        }
    )


class _TrackingClient:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.active = 0
        self.maximum_active = 0
        self.calls = 0
        self._lock = threading.Lock()

    def complete(self, request, *, timeout):
        with self._lock:
            self.calls += 1
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        try:
            time.sleep(self.delay)
            return _valid_output()
        finally:
            with self._lock:
                self.active -= 1


def _backend(client, *, concurrency=2, total_timeout=2.0, **overrides):
    return ExternalLLMRecommender(
        config=ExternalLLMConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="test-model",
            timeout=overrides.pop("timeout", 1.0),
            total_timeout=total_timeout,
            max_concurrent_recommendations=concurrency,
            max_retries=overrides.pop("max_retries", 0),
            retry_backoff_seconds=overrides.pop("retry_backoff_seconds", 0),
            **overrides,
        ),
        client=client,
    )


def test_network_recommendations_run_concurrently_instead_of_serially():
    client = _TrackingClient(0.2)
    backend = _backend(client, concurrency=2)
    executor = AsyncRecommendationExecutor(2)

    async def scenario():
        started = time.monotonic()
        results = await asyncio.gather(
            executor.recommend(backend, RecommendationRequest(intent="first")),
            executor.recommend(backend, RecommendationRequest(intent="second")),
        )
        return time.monotonic() - started, results

    try:
        elapsed, results = asyncio.run(scenario())
    finally:
        executor.shutdown()

    assert elapsed < 0.35
    assert client.maximum_active == 2
    assert all(result.metadata.fallback_used is False for result in results)


def test_configured_concurrency_limit_is_enforced_without_unbounded_submission():
    client = _TrackingClient(0.1)
    backend = _backend(client, concurrency=2)
    executor = AsyncRecommendationExecutor(2)

    async def scenario():
        started = time.monotonic()
        await asyncio.gather(
            *(
                executor.recommend(backend, RecommendationRequest(intent=str(index)))
                for index in range(5)
            )
        )
        return time.monotonic() - started

    try:
        elapsed = asyncio.run(scenario())
    finally:
        executor.shutdown()

    assert client.maximum_active == 2
    assert elapsed >= 0.28
    assert elapsed < 0.5


def test_total_deadline_returns_fallback_within_budget():
    class SlowClient:
        def complete(self, request, *, timeout):
            time.sleep(timeout + 0.15)
            raise LLMTimeoutError("secret provider timeout detail")

    backend = _backend(SlowClient(), concurrency=1, total_timeout=0.12, timeout=1)
    executor = AsyncRecommendationExecutor(1)

    async def scenario():
        started = time.monotonic()
        result = await executor.recommend(backend, RecommendationRequest(intent="demo"))
        return time.monotonic() - started, result

    try:
        elapsed, result = asyncio.run(scenario())
    finally:
        executor.shutdown()

    assert elapsed < 0.2
    assert result.recommendation.backend_name == "rule_based"
    assert result.metadata.deadline_exhausted is True
    assert result.metadata.timed_out is True
    assert result.metadata.fallback_error_category == "deadline_exhausted"


def test_retries_and_backoff_stop_immediately_when_budget_is_exhausted():
    class FakeClock:
        def __init__(self):
            self.now = 100.0

        def monotonic(self):
            return self.now

        def sleep(self, delay):
            self.now += delay

    class TimeoutClient:
        def __init__(self, clock):
            self.clock = clock
            self.timeouts = []

        def complete(self, request, *, timeout):
            self.timeouts.append(timeout)
            self.clock.now += timeout
            raise LLMTimeoutError("provider detail")

    clock = FakeClock()
    client = TimeoutClient(clock)
    backend = ExternalLLMRecommender(
        config=ExternalLLMConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="test-model",
            timeout=0.6,
            total_timeout=1.0,
            max_retries=10,
            retry_backoff_seconds=0.3,
        ),
        client=client,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = backend.recommend_with_metadata(RecommendationRequest(intent="demo"))

    assert len(client.timeouts) == 2
    assert client.timeouts[0] == pytest.approx(0.6)
    assert client.timeouts[1] == pytest.approx(0.05)
    assert result.metadata.attempt_count == 2
    assert result.metadata.deadline_exhausted is True
    assert clock.now == pytest.approx(100.95)


def test_cancellation_keeps_capacity_reserved_until_abandoned_work_finishes():
    first_started = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    class BlockingFirstClient:
        def complete(self, request, *, timeout):
            nonlocal call_count
            with call_lock:
                call_count += 1
                current = call_count
            if current == 1:
                first_started.set()
                release_first.wait(timeout=timeout)
            return _valid_output()

    backend = _backend(BlockingFirstClient(), concurrency=1, total_timeout=1)
    executor = AsyncRecommendationExecutor(1)

    async def scenario():
        first = asyncio.create_task(
            executor.recommend(backend, RecommendationRequest(intent="cancelled"))
        )
        await asyncio.to_thread(first_started.wait, 0.5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        second = asyncio.create_task(
            executor.recommend(backend, RecommendationRequest(intent="second"))
        )
        await asyncio.sleep(0.05)
        with call_lock:
            assert call_count == 1
        release_first.set()
        result = await second
        return result

    try:
        result = asyncio.run(scenario())
    finally:
        release_first.set()
        executor.shutdown()

    assert result.recommendation.backend_name == "external_llm"
    assert call_count == 2


@pytest.mark.parametrize(
    "failure,category",
    [
        (LLMTimeoutError("token=super-secret"), "timeout"),
        (OSError("password=super-secret"), "transport_error"),
        (ValueError("api_key=super-secret"), "internal_error"),
    ],
)
def test_fallback_metadata_is_sanitized_and_identifies_requested_effective_backend(
    failure, category
):
    class FailedClient:
        def complete(self, request, *, timeout):
            raise failure

    result = _backend(FailedClient()).recommend_with_metadata(
        RecommendationRequest(intent="demo")
    )
    metadata = result.metadata.to_dict()

    assert metadata["requested_backend"] == "external_llm"
    assert metadata["effective_backend"] == "rule_based"
    assert metadata["fallback_used"] is True
    assert metadata["fallback_error_category"] == category
    assert "super-secret" not in json.dumps(metadata)


def test_external_api_key_over_http_is_secure_by_default_and_override_is_explicit():
    with pytest.raises(ValueError, match="require HTTPS") as raised:
        ExternalLLMConfig(
            endpoint="http://127.0.0.1:8000/v1/chat/completions",
            model="test-model",
            api_key="super-secret",
        )
    assert "super-secret" not in str(raised.value)

    config = ExternalLLMConfig(
        endpoint="http://127.0.0.1:8000/v1/chat/completions",
        model="test-model",
        api_key="super-secret",
        allow_insecure_http=True,
    )
    assert config.allow_insecure_http is True
    assert "super-secret" not in repr(config)


@pytest.mark.parametrize(
    "overrides",
    [
        {"total_timeout": 0},
        {"total_timeout": 301},
        {"max_concurrent_recommendations": 0},
        {"max_concurrent_recommendations": 65},
        {"max_retries": 11},
        {"retry_backoff_seconds": 61},
    ],
)
def test_reliability_configuration_is_bounded_at_startup(overrides):
    with pytest.raises(ValueError):
        ExternalLLMConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="test-model",
            **overrides,
        )


def test_insecure_override_environment_value_is_strictly_validated():
    base = {
        "EXTERNAL_LLM_ENDPOINT": "http://127.0.0.1:8000/v1/chat/completions",
        "EXTERNAL_LLM_MODEL": "test-model",
        "EXTERNAL_LLM_API_KEY": "super-secret",
    }
    with pytest.raises(ValueError, match="exactly true or false"):
        ExternalLLMConfig.from_environ(
            {**base, "EXTERNAL_LLM_ALLOW_INSECURE_HTTP": "yes"}
        )
    config = ExternalLLMConfig.from_environ(
        {**base, "EXTERNAL_LLM_ALLOW_INSECURE_HTTP": "true"}
    )
    assert config.allow_insecure_http is True


def test_fallback_failure_exception_does_not_retain_credentials():
    class FailedClient:
        def complete(self, request, *, timeout):
            raise RuntimeError("Authorization: Bearer super-secret")

    class FailedFallback:
        def recommend(self, request):
            raise RuntimeError("fallback password super-secret")

    backend = ExternalLLMRecommender(
        config=ExternalLLMConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="test-model",
            api_key="super-secret",
            max_retries=0,
        ),
        client=FailedClient(),
        fallback=FailedFallback(),
    )

    with pytest.raises(ExternalLLMFallbackError) as raised:
        backend.recommend(RecommendationRequest(intent="demo"))

    rendered = repr(raised.value) + repr(raised.value.external_error) + repr(
        raised.value.fallback_error
    )
    assert "super-secret" not in rendered
    assert raised.value.__cause__ is None


def test_legacy_cli_json_remains_the_exact_eight_key_contract():
    completed = subprocess.run(
        [
            sys.executable,
            "recommender/recommender.py",
            "--intent",
            "basic Python",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert list(payload) == [
        "profile",
        "reasons",
        "score",
        "image_id",
        "image_reference",
        "image_reasons",
        "catalog_version",
        "policy_version",
    ]
