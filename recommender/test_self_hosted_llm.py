import json

import pytest

from recommender import RecommendationRequest, SpawnRecommendation, create_recommender
from recommender.self_hosted_llm import (
    SelfHostedLLMConfig,
    SelfHostedLLMRecommender,
)


def _valid_output() -> str:
    return json.dumps(
        {
            "profile": "medium",
            "reasons": ["The dataset and dataframe operations need moderate resources."],
            "score": 55,
            "image_id": "scipy-data-science",
            "image_reasons": ["The workload uses pandas."],
        }
    )


def test_self_hosted_backend_reuses_shared_llm_flow_and_returns_contract():
    class RecordingClient:
        def __init__(self):
            self.calls = []

        def complete(self, request, *, timeout):
            self.calls.append((request, timeout))
            return _valid_output()

    client = RecordingClient()
    backend = SelfHostedLLMRecommender(
        config=SelfHostedLLMConfig(
            endpoint="http://inference.internal:8000/v1/chat/completions",
            model="local-model",
            timeout=6,
            api_key="optional-local-token",
            max_retries=0,
        ),
        client=client,
    )

    recommendation = backend.recommend(
        RecommendationRequest(
            intent="Analyze a dataframe",
            dataset_size_gb=0.8,
            code_context="import pandas as pd",
        )
    )

    assert isinstance(recommendation, SpawnRecommendation)
    assert recommendation.profile == "medium"
    assert recommendation.backend_name == "self_hosted_llm"
    assert recommendation.backend_version == "self-hosted-llm-v1"
    assert client.calls[0][0].model == "local-model"
    assert client.calls[0][1] == 6


def test_self_hosted_environment_configuration_and_registry_selection():
    config = SelfHostedLLMConfig.from_environ(
        {
            "SELF_HOSTED_LLM_ENDPOINT": "http://vllm.local:8000/v1/chat/completions",
            "SELF_HOSTED_LLM_MODEL": "Qwen/Qwen3-8B",
            "SELF_HOSTED_LLM_TIMEOUT": "12.5",
            "SELF_HOSTED_LLM_API_KEY": "local-secret",
            "SELF_HOSTED_LLM_TEMPERATURE": "0.1",
            "SELF_HOSTED_LLM_MAX_RETRIES": "1",
            "SELF_HOSTED_LLM_RETRY_BACKOFF_SECONDS": "0.2",
            "SELF_HOSTED_LLM_TOTAL_TIMEOUT": "18",
            "SELF_HOSTED_LLM_MAX_CONCURRENT_RECOMMENDATIONS": "3",
        }
    )
    backend = create_recommender(
        None,
        environ={"RECOMMENDER_BACKEND": "self_hosted_llm"},
        config=config,
        client=type(
            "StaticClient",
            (),
            {"complete": lambda self, request, *, timeout: _valid_output()},
        )(),
    )

    assert isinstance(backend, SelfHostedLLMRecommender)
    assert backend.config.endpoint == "http://vllm.local:8000/v1/chat/completions"
    assert backend.config.model == "Qwen/Qwen3-8B"
    assert backend.config.timeout == 12.5
    assert backend.config.api_key == "local-secret"
    assert backend.config.temperature == 0.1
    assert backend.config.max_retries == 1
    assert backend.config.retry_backoff_seconds == 0.2
    assert backend.config.total_timeout == 18
    assert backend.config.max_concurrent_recommendations == 3


def test_self_hosted_failure_uses_the_same_rule_based_fallback():
    class FailedClient:
        def complete(self, request, *, timeout):
            raise OSError("local inference is unavailable")

    backend = SelfHostedLLMRecommender(
        config=SelfHostedLLMConfig(
            endpoint="http://127.0.0.1:11434/v1/chat/completions",
            model="local-model",
            max_retries=0,
        ),
        client=FailedClient(),
    )

    recommendation = backend.recommend(
        RecommendationRequest(intent="basic Python loops", dataset_size_gb=0.05)
    )

    assert recommendation.backend_name == "rule_based"
    assert recommendation.profile == "small"


@pytest.mark.parametrize(
    "environ,missing_name",
    [
        ({"SELF_HOSTED_LLM_MODEL": "model"}, "SELF_HOSTED_LLM_ENDPOINT"),
        (
            {"SELF_HOSTED_LLM_ENDPOINT": "http://127.0.0.1:8000/v1/chat/completions"},
            "SELF_HOSTED_LLM_MODEL",
        ),
    ],
)
def test_self_hosted_configuration_requires_endpoint_and_model(environ, missing_name):
    with pytest.raises(ValueError, match=missing_name):
        SelfHostedLLMConfig.from_environ(environ)
