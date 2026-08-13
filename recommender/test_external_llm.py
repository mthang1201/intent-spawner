import json

import pytest

from recommender import RecommendationRequest, SpawnRecommendation, create_recommender
from recommender.external_llm import (
    ExternalLLMConfig,
    ExternalLLMFallbackError,
    ExternalLLMRecommender,
    LLMClientError,
    LLMResponseError,
    LLMTimeoutError,
    OpenAICompatibleClient,
)


def valid_output(**overrides):
    payload = {
        "profile": "large",
        "reasons": ["Model training and the stated dataset need additional memory."],
        "score": 82,
        "image_id": "scipy-data-science",
        "image_reasons": ["The workload uses pandas and scikit-learn."],
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_external_backend_constructs_prompt_and_returns_exact_shared_schema():
    class RecordingClient:
        def __init__(self):
            self.calls = []

        def complete(self, request, *, timeout):
            self.calls.append((request, timeout))
            return valid_output()

    client = RecordingClient()
    config = ExternalLLMConfig(
        endpoint="https://llm.example.test/v1/chat/completions",
        model="portable-model",
        timeout=3.5,
        api_key="secret",
        temperature=0.2,
        max_retries=0,
    )
    backend = ExternalLLMRecommender(config=config, client=client)

    recommendation = backend.recommend(
        RecommendationRequest(
            intent="Train a classifier",
            dataset_size_gb=1.5,
            code_context="import pandas as pd\nfrom sklearn.linear_model import LogisticRegression",
        )
    )

    assert isinstance(recommendation, SpawnRecommendation)
    assert recommendation.profile == "large"
    assert recommendation.backend_name == "external_llm"
    assert recommendation.backend_version in {"external-llm-v1", "external-llm-v2"}
    assert recommendation.image_id == "scipy-data-science"
    assert recommendation.image_reference.startswith(
        "quay.io/jupyter/scipy-notebook@sha256:"
    )
    assert set(recommendation.to_unified_dict()) == {
        "profile",
        "reasons",
        "score",
        "image_id",
        "image_reference",
        "image_reasons",
        "catalog_version",
        "policy_version",
        "schema_version",
        "backend_name",
        "backend_version",
    }

    completion_request, timeout = client.calls[0]
    assert completion_request.model == "portable-model"
    assert completion_request.temperature == 0.2
    assert timeout == 3.5
    prompt = json.loads(completion_request.messages[1].content)
    assert prompt["workload"]["intent"] == "Train a classifier"
    assert prompt["workload"]["dataset_size_gb"] == 1.5
    assert "scipy-data-science" in prompt["allowed_images"]
    assert "image_reference" not in prompt["response_schema"]["properties"]


def test_retry_recovers_after_timeout_without_using_fallback():
    class FlakyClient:
        def __init__(self):
            self.calls = 0

        def complete(self, request, *, timeout):
            self.calls += 1
            if self.calls == 1:
                raise LLMTimeoutError("timed out")
            return valid_output(profile="medium", score=45)

    class FallbackMustNotRun:
        def recommend(self, request):
            raise AssertionError("fallback should not run")

    client = FlakyClient()
    sleeps = []
    backend = ExternalLLMRecommender(
        config=ExternalLLMConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="portable-model",
            max_retries=2,
            retry_backoff_seconds=0.25,
        ),
        client=client,
        fallback=FallbackMustNotRun(),
        sleep=sleeps.append,
    )

    recommendation = backend.recommend(RecommendationRequest(intent="analyze data"))

    assert recommendation.profile == "medium"
    assert client.calls == 2
    assert sleeps == [0.25]


@pytest.mark.parametrize(
    "response",
    [
        "not JSON",
        None,
        json.dumps(["not", "an", "object"]),
        json.dumps({"profile": "small"}),
        valid_output(profile="unbounded"),
        valid_output(image_id="user-supplied-image"),
        valid_output(score=float("nan")),
        valid_output(reasons=[]),
        valid_output(extra_field="not allowed"),
    ],
)
def test_invalid_or_malformed_model_output_falls_back_to_rule_backend(response):
    class StaticClient:
        def complete(self, request, *, timeout):
            return response

    backend = ExternalLLMRecommender(
        config=ExternalLLMConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="portable-model",
            max_retries=0,
        ),
        client=StaticClient(),
    )

    recommendation = backend.recommend(
        RecommendationRequest(intent="basic Python loops", dataset_size_gb=0.05)
    )

    assert recommendation.profile == "small"
    assert recommendation.backend_name == "rule_based"


def test_timeout_is_retried_then_falls_back():
    class TimeoutClient:
        def __init__(self):
            self.calls = 0

        def complete(self, request, *, timeout):
            self.calls += 1
            raise LLMTimeoutError("timed out")

    client = TimeoutClient()
    backend = ExternalLLMRecommender(
        config=ExternalLLMConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="portable-model",
            max_retries=1,
        ),
        client=client,
    )

    recommendation = backend.recommend(
        RecommendationRequest(intent="train a model", dataset_size_gb=2)
    )

    assert client.calls == 2
    assert recommendation.backend_name == "rule_based"
    assert recommendation.profile == "large"


def test_non_finite_dataset_hint_is_normalized_before_prompt_serialization():
    class RecordingClient:
        def complete(self, request, *, timeout):
            prompt = json.loads(request.messages[1].content)
            assert prompt["workload"]["dataset_size_gb"] == 0.0
            return valid_output(profile="small", score=0)

    backend = ExternalLLMRecommender(
        config=ExternalLLMConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="portable-model",
            max_retries=0,
        ),
        client=RecordingClient(),
    )

    assert backend.recommend(
        RecommendationRequest(intent="basic Python", dataset_size_gb=float("nan"))
    ).profile == "small"


def test_fallback_failure_raises_combined_typed_error():
    class FailedClient:
        def complete(self, request, *, timeout):
            raise LLMClientError("provider unavailable")

    class FailedFallback:
        def recommend(self, request):
            raise RuntimeError("fallback unavailable")

    backend = ExternalLLMRecommender(
        config=ExternalLLMConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="portable-model",
            max_retries=0,
        ),
        client=FailedClient(),
        fallback=FailedFallback(),
    )

    with pytest.raises(ExternalLLMFallbackError) as raised:
        backend.recommend(RecommendationRequest(intent="demo"))

    assert isinstance(raised.value.external_error, LLMClientError)
    assert isinstance(raised.value.fallback_error, RuntimeError)


def test_openai_compatible_adapter_translates_neutral_request_and_response():
    class RecordingTransport:
        def __init__(self):
            self.call = None

        def post_json(self, endpoint, *, headers, payload, timeout):
            self.call = (endpoint, headers, payload, timeout)
            return {"choices": [{"message": {"content": valid_output()}}]}

    transport = RecordingTransport()
    config = ExternalLLMConfig(
        endpoint="https://compatible.example.test/v1/chat/completions",
        model="compatible-model",
        api_key="top-secret",
        timeout=7,
        max_retries=0,
    )
    probe = ExternalLLMRecommender(config=config, client=None)
    neutral_request = probe._completion_request(RecommendationRequest(intent="demo"))
    client = OpenAICompatibleClient(
        endpoint=config.endpoint,
        api_key=config.api_key,
        transport=transport,
    )

    content = client.complete(neutral_request, timeout=config.timeout)

    assert json.loads(content)["profile"] == "large"
    endpoint, headers, payload, timeout = transport.call
    assert endpoint == config.endpoint
    assert headers["Authorization"] == "Bearer top-secret"
    assert payload["model"] == "compatible-model"
    assert payload["response_format"] == {"type": "json_object"}
    assert timeout == 7


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": ""}}]},
    ],
)
def test_openai_compatible_adapter_rejects_malformed_response_envelopes(response):
    class StaticTransport:
        def post_json(self, endpoint, *, headers, payload, timeout):
            return response

    client = OpenAICompatibleClient(
        endpoint="https://compatible.example.test/v1/chat/completions",
        transport=StaticTransport(),
    )
    backend = ExternalLLMRecommender(
        config=ExternalLLMConfig(
            endpoint="https://compatible.example.test/v1/chat/completions",
            model="compatible-model",
        )
    )

    with pytest.raises(LLMResponseError):
        client.complete(
            backend._completion_request(RecommendationRequest()),
            timeout=1,
        )


def test_environment_configuration_and_registry_selection():
    config = ExternalLLMConfig.from_environ(
        {
            "EXTERNAL_LLM_ENDPOINT": "https://llm.example.test/v1/chat/completions",
            "EXTERNAL_LLM_MODEL": "portable-model",
            "EXTERNAL_LLM_TIMEOUT": "4.5",
            "EXTERNAL_LLM_API_KEY": "secret",
            "EXTERNAL_LLM_TEMPERATURE": "0.3",
            "EXTERNAL_LLM_MAX_RETRIES": "3",
            "EXTERNAL_LLM_RETRY_BACKOFF_SECONDS": "0.1",
            "EXTERNAL_LLM_TOTAL_TIMEOUT": "15",
            "EXTERNAL_LLM_MAX_CONCURRENT_RECOMMENDATIONS": "5",
        }
    )

    backend = create_recommender(
        "external_llm",
        config=config,
        client=type(
            "StaticClient",
            (),
            {"complete": lambda self, request, *, timeout: valid_output()},
        )(),
    )

    assert isinstance(backend, ExternalLLMRecommender)
    assert backend.config.endpoint == "https://llm.example.test/v1/chat/completions"
    assert backend.config.model == "portable-model"
    assert backend.config.timeout == 4.5
    assert backend.config.api_key == "secret"
    assert backend.config.temperature == 0.3
    assert backend.config.max_retries == 3
    assert backend.config.retry_backoff_seconds == 0.1
    assert backend.config.total_timeout == 15
    assert backend.config.max_concurrent_recommendations == 5


@pytest.mark.parametrize(
    "kwargs",
    [
        {"endpoint": "relative/path", "model": "model"},
        {"endpoint": "https://example.test/v1", "model": ""},
        {"endpoint": "https://example.test/v1", "model": "model", "timeout": 0},
        {"endpoint": "https://example.test/v1", "model": "model", "temperature": 3},
        {"endpoint": "https://example.test/v1", "model": "model", "max_retries": -1},
    ],
)
def test_configuration_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        ExternalLLMConfig(**kwargs)
