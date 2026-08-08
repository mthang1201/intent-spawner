"""Self-hosted LLM adapter for OpenAI-compatible local inference servers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Any

from .base import Recommender
from .external_llm import ExternalLLMConfig, ExternalLLMRecommender, LLMClient
from .rule_based import DEFAULT_CATALOG_PATH


BACKEND_NAME = "self_hosted_llm"
BACKEND_VERSION = "self-hosted-llm-v1"

ENDPOINT_ENV_VAR = "SELF_HOSTED_LLM_ENDPOINT"
MODEL_ENV_VAR = "SELF_HOSTED_LLM_MODEL"
TIMEOUT_ENV_VAR = "SELF_HOSTED_LLM_TIMEOUT"
API_KEY_ENV_VAR = "SELF_HOSTED_LLM_API_KEY"
TEMPERATURE_ENV_VAR = "SELF_HOSTED_LLM_TEMPERATURE"
MAX_RETRIES_ENV_VAR = "SELF_HOSTED_LLM_MAX_RETRIES"
RETRY_BACKOFF_ENV_VAR = "SELF_HOSTED_LLM_RETRY_BACKOFF_SECONDS"
TOTAL_TIMEOUT_ENV_VAR = "SELF_HOSTED_LLM_TOTAL_TIMEOUT"
MAX_CONCURRENT_ENV_VAR = "SELF_HOSTED_LLM_MAX_CONCURRENT_RECOMMENDATIONS"


@dataclass(frozen=True)
class SelfHostedLLMConfig(ExternalLLMConfig):
    """Configuration for a locally managed OpenAI-compatible inference API."""

    # HTTP is allowed only inside the administrator-defined local/in-cluster
    # trust boundary documented for this backend.
    _allow_api_key_over_http = True

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "SelfHostedLLMConfig":
        """Load self-hosted endpoint settings from environment variables."""

        selected = os.environ if environ is None else environ
        endpoint = selected.get(ENDPOINT_ENV_VAR, "")
        model = selected.get(MODEL_ENV_VAR, "")
        if not endpoint:
            raise ValueError(f"{ENDPOINT_ENV_VAR} is required for the self_hosted_llm backend")
        if not model:
            raise ValueError(f"{MODEL_ENV_VAR} is required for the self_hosted_llm backend")

        try:
            timeout = float(selected.get(TIMEOUT_ENV_VAR, "10"))
            temperature = float(selected.get(TEMPERATURE_ENV_VAR, "0"))
            max_retries = int(selected.get(MAX_RETRIES_ENV_VAR, "2"))
            retry_backoff_seconds = float(selected.get(RETRY_BACKOFF_ENV_VAR, "0"))
            total_timeout = float(selected.get(TOTAL_TIMEOUT_ENV_VAR, "30"))
            max_concurrent_recommendations = int(
                selected.get(MAX_CONCURRENT_ENV_VAR, "4")
            )
        except ValueError as exc:
            raise ValueError("self-hosted LLM numeric configuration is invalid") from exc

        return cls(
            endpoint=endpoint,
            model=model,
            timeout=timeout,
            api_key=selected.get(API_KEY_ENV_VAR, ""),
            temperature=temperature,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            total_timeout=total_timeout,
            max_concurrent_recommendations=max_concurrent_recommendations,
        )


class SelfHostedLLMRecommender(ExternalLLMRecommender):
    """Run the shared LLM recommendation flow against local inference."""

    backend_name = BACKEND_NAME
    backend_version = BACKEND_VERSION

    def __init__(
        self,
        *,
        config: SelfHostedLLMConfig | None = None,
        client: LLMClient | None = None,
        fallback: Recommender | None = None,
        catalog: Mapping[str, Any] | None = None,
        catalog_path: str | Path = DEFAULT_CATALOG_PATH,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            config=config if config is not None else SelfHostedLLMConfig.from_environ(),
            client=client,
            fallback=fallback,
            catalog=catalog,
            catalog_path=catalog_path,
            sleep=sleep,
            monotonic=monotonic,
        )


__all__ = [
    "API_KEY_ENV_VAR",
    "BACKEND_NAME",
    "BACKEND_VERSION",
    "ENDPOINT_ENV_VAR",
    "MAX_CONCURRENT_ENV_VAR",
    "MAX_RETRIES_ENV_VAR",
    "MODEL_ENV_VAR",
    "RETRY_BACKOFF_ENV_VAR",
    "SelfHostedLLMConfig",
    "SelfHostedLLMRecommender",
    "TEMPERATURE_ENV_VAR",
    "TIMEOUT_ENV_VAR",
    "TOTAL_TIMEOUT_ENV_VAR",
]
