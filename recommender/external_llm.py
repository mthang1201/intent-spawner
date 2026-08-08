"""Provider-neutral external LLM spawn recommendation backend."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import socket
import time
from typing import Any, Protocol, runtime_checkable
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from .base import Recommender
from .models import POLICY_VERSION, RecommendationRequest, SpawnRecommendation
from .reliability import (
    MAX_CONCURRENT_NETWORK_RECOMMENDATIONS,
    RecommendationCallState,
    RecommendationMetadata,
    RecommendationResult,
    network_work_deadline,
)
from .rule_based import (
    DEFAULT_CATALOG_PATH,
    PROFILES,
    RuleBasedRecommender,
    coerce_dataset_size_gb,
    load_image_catalog,
    validate_image_catalog,
)


BACKEND_NAME = "external_llm"
BACKEND_VERSION = "external-llm-v1"

ENDPOINT_ENV_VAR = "EXTERNAL_LLM_ENDPOINT"
MODEL_ENV_VAR = "EXTERNAL_LLM_MODEL"
TIMEOUT_ENV_VAR = "EXTERNAL_LLM_TIMEOUT"
API_KEY_ENV_VAR = "EXTERNAL_LLM_API_KEY"
TEMPERATURE_ENV_VAR = "EXTERNAL_LLM_TEMPERATURE"
MAX_RETRIES_ENV_VAR = "EXTERNAL_LLM_MAX_RETRIES"
RETRY_BACKOFF_ENV_VAR = "EXTERNAL_LLM_RETRY_BACKOFF_SECONDS"
TOTAL_TIMEOUT_ENV_VAR = "EXTERNAL_LLM_TOTAL_TIMEOUT"
MAX_CONCURRENT_ENV_VAR = "EXTERNAL_LLM_MAX_CONCURRENT_RECOMMENDATIONS"
ALLOW_INSECURE_HTTP_ENV_VAR = "EXTERNAL_LLM_ALLOW_INSECURE_HTTP"

MAX_TOTAL_TIMEOUT_SECONDS = 300.0
MAX_ATTEMPT_TIMEOUT_SECONDS = 300.0
MAX_CONFIGURED_RETRIES = 10
MAX_RETRY_BACKOFF_SECONDS = 60.0


class ExternalLLMError(RuntimeError):
    """Base class for external LLM backend failures."""


class LLMClientError(ExternalLLMError):
    """The configured LLM client could not produce a usable response."""


class LLMTimeoutError(LLMClientError):
    """The external request exceeded its configured timeout."""


class LLMDeadlineExceededError(LLMTimeoutError):
    """The total recommendation deadline was exhausted."""


class LLMResponseError(LLMClientError):
    """The provider response envelope was malformed."""


class LLMOutputValidationError(ExternalLLMError):
    """The model content did not satisfy the recommendation contract."""


class ExternalLLMFallbackError(ExternalLLMError):
    """Both the external backend and its safe fallback failed."""

    def __init__(self, external_error: Exception, fallback_error: Exception) -> None:
        super().__init__("external LLM recommendation and fallback backend both failed")
        self.external_error = external_error
        self.fallback_error = fallback_error


@dataclass(frozen=True)
class ExternalLLMConfig:
    """Runtime configuration shared by provider adapters."""

    endpoint: str
    model: str
    timeout: float = 10.0
    api_key: str = field(default="", repr=False)
    temperature: float = 0.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.0
    total_timeout: float = 30.0
    max_concurrent_recommendations: int = 4
    allow_insecure_http: bool = False

    # Subclasses may explicitly define a different deployment trust boundary.
    _allow_api_key_over_http = False

    def __post_init__(self) -> None:
        parsed_endpoint = urllib_parse.urlparse(self.endpoint)
        if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.netloc:
            raise ValueError("external LLM endpoint must be an absolute HTTP(S) URL")
        if parsed_endpoint.username is not None or parsed_endpoint.password is not None:
            raise ValueError("external LLM endpoint must not contain credentials")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("external LLM model must not be blank")
        if not isinstance(self.timeout, (int, float)) or isinstance(self.timeout, bool):
            raise ValueError("external LLM timeout must be a positive number")
        if (
            not math.isfinite(float(self.timeout))
            or float(self.timeout) <= 0
            or float(self.timeout) > MAX_ATTEMPT_TIMEOUT_SECONDS
        ):
            raise ValueError("external LLM timeout must be a positive number")
        if not isinstance(self.temperature, (int, float)) or isinstance(self.temperature, bool):
            raise ValueError("external LLM temperature must be between 0 and 2")
        if not math.isfinite(float(self.temperature)) or not 0 <= float(self.temperature) <= 2:
            raise ValueError("external LLM temperature must be between 0 and 2")
        if not isinstance(self.max_retries, int) or isinstance(self.max_retries, bool):
            raise ValueError("external LLM max_retries must be a non-negative integer")
        if not 0 <= self.max_retries <= MAX_CONFIGURED_RETRIES:
            raise ValueError("external LLM max_retries must be a non-negative integer")
        if (
            not isinstance(self.retry_backoff_seconds, (int, float))
            or isinstance(self.retry_backoff_seconds, bool)
            or not math.isfinite(float(self.retry_backoff_seconds))
            or not 0 <= float(self.retry_backoff_seconds) <= MAX_RETRY_BACKOFF_SECONDS
        ):
            raise ValueError("external LLM retry_backoff_seconds must be non-negative")
        if not isinstance(self.api_key, str):
            raise ValueError("external LLM api_key must be a string")
        if (
            not isinstance(self.total_timeout, (int, float))
            or isinstance(self.total_timeout, bool)
            or not math.isfinite(float(self.total_timeout))
            or not 0 < float(self.total_timeout) <= MAX_TOTAL_TIMEOUT_SECONDS
        ):
            raise ValueError("external LLM total_timeout must be a positive bounded number")
        if (
            not isinstance(self.max_concurrent_recommendations, int)
            or isinstance(self.max_concurrent_recommendations, bool)
            or not 1
            <= self.max_concurrent_recommendations
            <= MAX_CONCURRENT_NETWORK_RECOMMENDATIONS
        ):
            raise ValueError(
                "external LLM max_concurrent_recommendations must be between 1 and "
                f"{MAX_CONCURRENT_NETWORK_RECOMMENDATIONS}"
            )
        if not isinstance(self.allow_insecure_http, bool):
            raise ValueError("external LLM allow_insecure_http must be a boolean")
        if (
            parsed_endpoint.scheme == "http"
            and self.api_key
            and not self.allow_insecure_http
            and not self._allow_api_key_over_http
        ):
            raise ValueError(
                "external LLM API keys require HTTPS; development-only insecure "
                "HTTP must be explicitly enabled"
            )

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "ExternalLLMConfig":
        """Load configuration from environment variables."""

        selected = os.environ if environ is None else environ
        endpoint = selected.get(ENDPOINT_ENV_VAR, "")
        model = selected.get(MODEL_ENV_VAR, "")
        if not endpoint:
            raise ValueError(f"{ENDPOINT_ENV_VAR} is required for the external_llm backend")
        if not model:
            raise ValueError(f"{MODEL_ENV_VAR} is required for the external_llm backend")

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
            raise ValueError("external LLM numeric configuration is invalid") from exc

        insecure_value = selected.get(ALLOW_INSECURE_HTTP_ENV_VAR, "false").strip().lower()
        if insecure_value not in {"true", "false"}:
            raise ValueError(
                f"{ALLOW_INSECURE_HTTP_ENV_VAR} must be exactly true or false"
            )

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
            allow_insecure_http=insecure_value == "true",
        )


@dataclass(frozen=True)
class LLMMessage:
    """Provider-neutral chat message."""

    role: str
    content: str


@dataclass(frozen=True)
class LLMCompletionRequest:
    """Provider-neutral structured-completion request."""

    model: str
    messages: tuple[LLMMessage, ...]
    temperature: float
    response_schema: Mapping[str, Any]


@runtime_checkable
class LLMClient(Protocol):
    """Adapter boundary implemented by an external LLM provider client."""

    def complete(self, request: LLMCompletionRequest, *, timeout: float) -> str:
        """Return only the assistant's structured text content."""

        ...


class JSONHTTPTransport(Protocol):
    """Small injectable HTTP boundary used by provider adapters."""

    def post_json(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        """POST a JSON object and return a decoded JSON object."""

        ...


class UrllibJSONTransport:
    """Dependency-free JSON HTTP transport with explicit timeout handling."""

    def post_json(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib_request.Request(
            endpoint,
            data=body,
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=timeout) as response:
                raw_response = response.read().decode("utf-8")
        except (TimeoutError, socket.timeout) as exc:
            raise LLMTimeoutError("external LLM request timed out") from exc
        except urllib_error.HTTPError as exc:
            raise LLMClientError(
                f"external LLM endpoint returned HTTP {exc.code}"
            ) from exc
        except urllib_error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise LLMTimeoutError("external LLM request timed out") from exc
            raise LLMClientError("external LLM endpoint could not be reached") from exc
        except OSError as exc:
            raise LLMClientError("external LLM transport failed") from exc

        try:
            decoded = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise LLMResponseError("external LLM response envelope was not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise LLMResponseError("external LLM response envelope must be a JSON object")
        return decoded


class OpenAICompatibleClient:
    """Chat-completions adapter for OpenAI-compatible HTTP endpoints."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str = "",
        transport: JSONHTTPTransport | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._transport = transport or UrllibJSONTransport()

    def complete(self, request: LLMCompletionRequest, *, timeout: float) -> str:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": request.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "temperature": request.temperature,
            "response_format": {"type": "json_object"},
        }
        response = self._transport.post_json(
            self._endpoint,
            headers=headers,
            payload=payload,
            timeout=timeout,
        )
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(
                "external LLM response is missing assistant content"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("external LLM assistant content must be non-empty text")
        return content


RESPONSE_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["profile", "reasons", "score", "image_id", "image_reasons"],
    "properties": {
        "profile": {"type": "string", "enum": list(PROFILES)},
        "reasons": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "score": {"type": ["number", "null"]},
        "image_id": {"type": "string"},
        "image_reasons": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        },
    },
}

_EXPECTED_OUTPUT_FIELDS = frozenset(RESPONSE_SCHEMA["required"])
_MAX_REASON_COUNT = 8
_MAX_REASON_LENGTH = 500


def _validate_reason_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > _MAX_REASON_COUNT:
        raise LLMOutputValidationError(
            f"external LLM field {field_name!r} must be a non-empty bounded string list"
        )
    if not all(
        isinstance(item, str) and item.strip() and len(item) <= _MAX_REASON_LENGTH
        for item in value
    ):
        raise LLMOutputValidationError(
            f"external LLM field {field_name!r} contains an invalid reason"
        )
    return [item.strip() for item in value]


def _decode_structured_output(content: str) -> Mapping[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise LLMOutputValidationError(
            "external LLM output must be non-empty JSON text"
        )
    candidate = content.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[0].strip().lower() in {"```", "```json"}:
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        decoded = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMOutputValidationError("external LLM output is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise LLMOutputValidationError("external LLM output must be a JSON object")
    return decoded


class ExternalLLMRecommender:
    """Use a provider-neutral LLM client and validate all model output locally."""

    backend_name = BACKEND_NAME
    backend_version = BACKEND_VERSION
    network_bound = True

    def __init__(
        self,
        *,
        config: ExternalLLMConfig | None = None,
        client: LLMClient | None = None,
        fallback: Recommender | None = None,
        catalog: Mapping[str, Any] | None = None,
        catalog_path: str | Path = DEFAULT_CATALOG_PATH,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config if config is not None else ExternalLLMConfig.from_environ()
        self._catalog = (
            validate_image_catalog(catalog)
            if catalog is not None
            else load_image_catalog(catalog_path)
        )
        self._client = (
            client
            if client is not None
            else OpenAICompatibleClient(
                endpoint=self.config.endpoint,
                api_key=self.config.api_key,
            )
        )
        self._fallback = (
            fallback
            if fallback is not None
            else RuleBasedRecommender(catalog=self._catalog)
        )
        self._sleep = sleep
        self._monotonic = monotonic

    def _completion_request(self, request: RecommendationRequest) -> LLMCompletionRequest:
        catalog_prompt = {
            image_id: {
                "description": image["description"],
                "capabilities": image["capabilities"],
            }
            for image_id, image in self._catalog["images"].items()
        }
        system_prompt = (
            "You recommend one JupyterHub resource profile and one administrator-"
            "allowlisted notebook image. Return exactly one JSON object matching the "
            "provided schema. Do not include Markdown, code fences, or extra fields. "
            "Never invent an image ID. Keep reasons concise and grounded only in the input."
        )
        user_prompt = json.dumps(
            {
                "task": "Recommend a spawn profile and notebook image.",
                "workload": {
                    "intent": request.intent,
                    "dataset_size_gb": coerce_dataset_size_gb(request.dataset_size_gb),
                    "code_context": request.code_context,
                },
                "allowed_profiles": list(PROFILES),
                "allowed_images": catalog_prompt,
                "response_schema": RESPONSE_SCHEMA,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return LLMCompletionRequest(
            model=self.config.model,
            messages=(
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ),
            temperature=self.config.temperature,
            response_schema=RESPONSE_SCHEMA,
        )

    def _to_recommendation(self, content: str) -> SpawnRecommendation:
        output = _decode_structured_output(content)
        if set(output) != _EXPECTED_OUTPUT_FIELDS:
            missing = sorted(_EXPECTED_OUTPUT_FIELDS - set(output))
            extra = sorted(set(output) - _EXPECTED_OUTPUT_FIELDS)
            detail = []
            if missing:
                detail.append("missing: " + ", ".join(missing))
            if extra:
                detail.append("unexpected: " + ", ".join(extra))
            raise LLMOutputValidationError(
                "external LLM output fields are invalid (" + "; ".join(detail) + ")"
            )

        profile = output["profile"]
        if profile not in PROFILES:
            raise LLMOutputValidationError("external LLM returned an unknown profile")
        image_id = output["image_id"]
        if not isinstance(image_id, str) or image_id not in self._catalog["images"]:
            raise LLMOutputValidationError(
                "external LLM returned an image outside the administrator catalog"
            )

        score = output["score"]
        if score is not None:
            if (
                not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not math.isfinite(float(score))
                or not 0 <= float(score) <= 100
            ):
                raise LLMOutputValidationError(
                    "external LLM score must be null or a finite number from 0 to 100"
                )

        image = self._catalog["images"][image_id]
        return SpawnRecommendation(
            profile=profile,
            reasons=_validate_reason_list(output["reasons"], "reasons"),
            score=score,
            image_id=image_id,
            image_reference=image["reference"],
            image_reasons=_validate_reason_list(output["image_reasons"], "image_reasons"),
            catalog_version=self._catalog["catalog_version"],
            policy_version=POLICY_VERSION,
            backend_name=self.backend_name,
            backend_version=self.backend_version,
        )

    @staticmethod
    def _error_category(error: Exception) -> str:
        if isinstance(error, LLMDeadlineExceededError):
            return "deadline_exhausted"
        if isinstance(error, LLMTimeoutError):
            return "timeout"
        if isinstance(error, (LLMResponseError, LLMOutputValidationError)):
            return "invalid_response"
        if isinstance(error, (LLMClientError, OSError)):
            return "transport_error"
        return "internal_error"

    @staticmethod
    def _sanitized_error(error: Exception) -> Exception:
        """Keep exception types useful without retaining provider-controlled text."""

        if isinstance(error, LLMDeadlineExceededError):
            return LLMDeadlineExceededError("recommendation deadline exhausted")
        if isinstance(error, LLMTimeoutError):
            return LLMTimeoutError("external LLM request timed out")
        if isinstance(error, LLMResponseError):
            return LLMResponseError("external LLM response was invalid")
        if isinstance(error, LLMOutputValidationError):
            return LLMOutputValidationError("external LLM output was invalid")
        if isinstance(error, LLMClientError):
            return LLMClientError("external LLM request failed")
        return ExternalLLMError("external LLM recommendation failed")

    def fallback_result(
        self,
        request: RecommendationRequest,
        *,
        error_category: str,
        attempt_count: int,
        started: float,
        timed_out: bool,
        deadline_exhausted: bool,
        external_error: Exception | None = None,
    ) -> RecommendationResult:
        """Return the rule fallback plus safe metadata, or a sanitized typed error."""

        try:
            recommendation = self._fallback.recommend(request)
            if not isinstance(recommendation, SpawnRecommendation):
                raise TypeError("fallback backend returned an invalid recommendation type")
            return RecommendationResult(
                recommendation=recommendation,
                metadata=RecommendationMetadata(
                    requested_backend=self.backend_name,
                    effective_backend=recommendation.backend_name,
                    fallback_used=True,
                    fallback_error_category=error_category,
                    attempt_count=attempt_count,
                    total_elapsed_seconds=max(0.0, self._monotonic() - started),
                    timed_out=timed_out,
                    deadline_exhausted=deadline_exhausted,
                ),
            )
        except Exception as fallback_error:
            safe_external = self._sanitized_error(
                external_error
                if external_error is not None
                else LLMDeadlineExceededError("recommendation deadline exhausted")
            )
            safe_fallback = RuntimeError("fallback backend failed")
            raise ExternalLLMFallbackError(safe_external, safe_fallback) from None

    def recommend_with_metadata(
        self,
        request: RecommendationRequest,
        *,
        deadline: float | None = None,
        state: RecommendationCallState | None = None,
    ) -> RecommendationResult:
        """Call, retry within one total deadline, and return safe telemetry."""

        started = self._monotonic()
        configured_deadline = started + float(self.config.total_timeout)
        total_deadline = (
            configured_deadline
            if deadline is None
            else min(configured_deadline, deadline)
        )
        effective_deadline = network_work_deadline(started, total_deadline)
        completion_request = self._completion_request(request)
        last_error: Exception | None = None
        last_category = "internal_error"
        attempt_count = 0
        timed_out = False
        deadline_exhausted = False

        for attempt_index in range(self.config.max_retries + 1):
            remaining = effective_deadline - self._monotonic()
            if remaining <= 0:
                deadline_exhausted = True
                last_error = LLMDeadlineExceededError(
                    "recommendation deadline exhausted"
                )
                last_category = "deadline_exhausted"
                break

            attempt_count += 1
            if state is not None:
                state.mark_attempt(attempt_count)
            try:
                content = self._client.complete(
                    completion_request,
                    timeout=min(float(self.config.timeout), remaining),
                )
                if self._monotonic() >= effective_deadline:
                    raise LLMDeadlineExceededError(
                        "recommendation deadline exhausted"
                    )
                recommendation = self._to_recommendation(content)
                if self._monotonic() >= effective_deadline:
                    raise LLMDeadlineExceededError(
                        "recommendation deadline exhausted"
                    )
                return RecommendationResult(
                    recommendation=recommendation,
                    metadata=RecommendationMetadata(
                        requested_backend=self.backend_name,
                        effective_backend=recommendation.backend_name,
                        fallback_used=False,
                        fallback_error_category=None,
                        attempt_count=attempt_count,
                        total_elapsed_seconds=max(0.0, self._monotonic() - started),
                        timed_out=timed_out,
                        deadline_exhausted=False,
                    ),
                )
            except Exception as exc:
                last_error = self._sanitized_error(exc)
                category = self._error_category(exc)
                last_category = category
                timed_out = timed_out or category in {"timeout", "deadline_exhausted"}
                if category == "deadline_exhausted":
                    deadline_exhausted = True

            remaining = effective_deadline - self._monotonic()
            if remaining <= 0:
                deadline_exhausted = True
                last_error = LLMDeadlineExceededError(
                    "recommendation deadline exhausted"
                )
                last_category = "deadline_exhausted"
                break
            if attempt_index >= self.config.max_retries:
                break

            backoff = min(
                float(self.config.retry_backoff_seconds) * (2**attempt_index),
                remaining,
            )
            if backoff > 0:
                self._sleep(backoff)
            if self._monotonic() >= effective_deadline:
                deadline_exhausted = True
                timed_out = True
                last_error = LLMDeadlineExceededError(
                    "recommendation deadline exhausted"
                )
                last_category = "deadline_exhausted"
                break

        if last_error is None:
            last_error = ExternalLLMError("external LLM recommendation failed")
        error_category = last_category
        timed_out = timed_out or error_category in {"timeout", "deadline_exhausted"}
        deadline_exhausted = deadline_exhausted or error_category == "deadline_exhausted"
        return self.fallback_result(
            request,
            error_category=error_category,
            attempt_count=attempt_count,
            started=started,
            timed_out=timed_out,
            deadline_exhausted=deadline_exhausted,
            external_error=last_error,
        )

    def recommend(self, request: RecommendationRequest) -> SpawnRecommendation:
        """Preserve the synchronous legacy API and return only its recommendation."""

        return self.recommend_with_metadata(request).recommendation


__all__ = [
    "API_KEY_ENV_VAR",
    "ALLOW_INSECURE_HTTP_ENV_VAR",
    "BACKEND_NAME",
    "BACKEND_VERSION",
    "ENDPOINT_ENV_VAR",
    "ExternalLLMConfig",
    "ExternalLLMError",
    "ExternalLLMFallbackError",
    "ExternalLLMRecommender",
    "LLMClient",
    "LLMClientError",
    "LLMCompletionRequest",
    "LLMDeadlineExceededError",
    "JSONHTTPTransport",
    "LLMMessage",
    "LLMOutputValidationError",
    "LLMResponseError",
    "LLMTimeoutError",
    "MAX_RETRIES_ENV_VAR",
    "MAX_CONCURRENT_ENV_VAR",
    "MODEL_ENV_VAR",
    "OpenAICompatibleClient",
    "RESPONSE_SCHEMA",
    "RETRY_BACKOFF_ENV_VAR",
    "TEMPERATURE_ENV_VAR",
    "TIMEOUT_ENV_VAR",
    "TOTAL_TIMEOUT_ENV_VAR",
    "UrllibJSONTransport",
]
