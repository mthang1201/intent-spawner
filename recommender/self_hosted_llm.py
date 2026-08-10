"""Self-hosted LLM adapter for Ollama and OpenAI-compatible local inference servers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Any

from .base import Recommender
from .external_llm import (
    ExternalLLMConfig,
    ExternalLLMRecommender,
    JSONHTTPTransport,
    LLMClient,
    LLMCompletionRequest,
    LLMCompletionResponse,
    LLMResponseError,
    OpenAICompatibleClient,
    UrllibJSONTransport,
)
from .rule_based import DEFAULT_CATALOG_PATH


BACKEND_NAME = "self_hosted_llm"
OLLAMA_BACKEND_NAME = "self_hosted_local_ollama_llm"
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

OLLAMA_ENDPOINT_ENV_VAR = "OLLAMA_ENDPOINT"
OLLAMA_MODEL_ENV_VAR = "OLLAMA_MODEL"
OLLAMA_TIMEOUT_ENV_VAR = "OLLAMA_TIMEOUT"
OLLAMA_TEMPERATURE_ENV_VAR = "OLLAMA_TEMPERATURE"


class OllamaClient(OpenAICompatibleClient):
    """Client for Ollama local inference supporting OpenAI-compatible and native envelopes."""

    def complete(
        self, request: LLMCompletionRequest, *, timeout: float
    ) -> LLMCompletionResponse:
        # If the endpoint ends with /api/chat (native Ollama API)
        if self._endpoint.rstrip("/").endswith("/api/chat"):
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            payload = {
                "model": request.model,
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ],
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": request.temperature,
                },
            }
            started = time.monotonic()
            response = self._transport.post_json(
                self._endpoint,
                headers=headers,
                payload=payload,
                timeout=timeout,
            )
            inference_latency = max(0.0, time.monotonic() - started)
            try:
                content = response["message"]["content"]
            except (KeyError, TypeError) as exc:
                raise LLMResponseError(
                    "Ollama native response is missing message content"
                ) from exc
            if not isinstance(content, str) or not content.strip():
                raise LLMResponseError("Ollama assistant content must be non-empty text")
            prompt_tokens = response.get("prompt_eval_count")
            completion_tokens = response.get("eval_count")
            total_tokens = (
                prompt_tokens + completion_tokens
                if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int)
                else None
            )
            eval_duration_ns = response.get("eval_duration")
            if isinstance(eval_duration_ns, (int, float)) and eval_duration_ns > 0:
                inference_latency = float(eval_duration_ns) / 1e9

            return LLMCompletionResponse(
                content=content,
                prompt_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
                completion_tokens=completion_tokens if isinstance(completion_tokens, int) else None,
                total_tokens=total_tokens,
                inference_latency_seconds=inference_latency,
                raw_response_envelope=response,
            )

        # Standard OpenAI-compatible path (/v1/chat/completions)
        return super().complete(request, timeout=timeout)


@dataclass(frozen=True)
class SelfHostedLLMConfig(ExternalLLMConfig):
    """Configuration for a locally managed Ollama or OpenAI-compatible inference API."""

    # HTTP is allowed inside the local trust boundary.
    _allow_api_key_over_http = True

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "SelfHostedLLMConfig":
        """Load self-hosted / Ollama endpoint settings from environment variables."""

        selected = os.environ if environ is None else environ
        endpoint = (
            selected.get(ENDPOINT_ENV_VAR)
            or selected.get(OLLAMA_ENDPOINT_ENV_VAR)
            or ""
        )
        model = (
            selected.get(MODEL_ENV_VAR)
            or selected.get(OLLAMA_MODEL_ENV_VAR)
            or ""
        )
        if not endpoint:
            raise ValueError(
                f"{ENDPOINT_ENV_VAR} or {OLLAMA_ENDPOINT_ENV_VAR} is required for the self_hosted_llm backend"
            )
        if not model:
            raise ValueError(
                f"{MODEL_ENV_VAR} or {OLLAMA_MODEL_ENV_VAR} is required for the self_hosted_llm backend"
            )

        try:
            timeout = float(
                selected.get(TIMEOUT_ENV_VAR)
                or selected.get(OLLAMA_TIMEOUT_ENV_VAR)
                or "10"
            )
            temperature = float(
                selected.get(TEMPERATURE_ENV_VAR)
                or selected.get(OLLAMA_TEMPERATURE_ENV_VAR)
                or "0"
            )
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
    """Run the shared LLM recommendation flow against local Ollama or inference servers."""

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
        resolved_config = config if config is not None else SelfHostedLLMConfig.from_environ()
        resolved_client = (
            client
            if client is not None
            else OllamaClient(
                endpoint=resolved_config.endpoint,
                api_key=resolved_config.api_key,
            )
        )
        super().__init__(
            config=resolved_config,
            client=resolved_client,
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
    "LEGACY_BACKEND_NAME",
    "MAX_CONCURRENT_ENV_VAR",
    "MAX_RETRIES_ENV_VAR",
    "MODEL_ENV_VAR",
    "OllamaClient",
    "OLLAMA_ENDPOINT_ENV_VAR",
    "OLLAMA_MODEL_ENV_VAR",
    "OLLAMA_TEMPERATURE_ENV_VAR",
    "OLLAMA_TIMEOUT_ENV_VAR",
    "RETRY_BACKOFF_ENV_VAR",
    "SelfHostedLLMConfig",
    "SelfHostedLLMRecommender",
    "TEMPERATURE_ENV_VAR",
    "TIMEOUT_ENV_VAR",
    "TOTAL_TIMEOUT_ENV_VAR",
]
