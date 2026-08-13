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
from .token_pricing import PricingProvenance


BACKEND_NAME = "external_llm"
BACKEND_VERSION = "external-llm-v2"

PROMPT_VERSION_V4_0 = "prompt-v4.0.0"
PROMPT_VERSION_V4_1 = "prompt-v4.1.0"
DEFAULT_PROMPT_VERSION = PROMPT_VERSION_V4_1
PROMPT_VERSION_ENV_VAR = "EXTERNAL_LLM_PROMPT_VERSION"
LEGACY_PROMPT_VERSION_ENV_VAR = "LLM_PROMPT_VERSION"
SUPPORTED_PROMPT_VERSIONS = {PROMPT_VERSION_V4_0, PROMPT_VERSION_V4_1}

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
PROMPT_PRICE_PER_M_ENV_VAR = "EXTERNAL_LLM_PROMPT_PRICE_PER_M"
COMPLETION_PRICE_PER_M_ENV_VAR = "EXTERNAL_LLM_COMPLETION_PRICE_PER_M"
PRICING_ID_ENV_VAR = "EXTERNAL_LLM_PRICING_ID"
PRICING_DATE_ENV_VAR = "EXTERNAL_LLM_PRICING_DATE"
PRICING_SOURCE_ENV_VAR = "EXTERNAL_LLM_PRICING_SOURCE"
PRICING_CONFIG_PATH_ENV_VAR = "EXTERNAL_LLM_PRICING_CONFIG_PATH"

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
    prompt_price_per_m: float | None = None
    completion_price_per_m: float | None = None
    pricing: PricingProvenance | None = None
    prompt_version: str = DEFAULT_PROMPT_VERSION

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
        if self.prompt_version not in SUPPORTED_PROMPT_VERSIONS:
            raise ValueError(
                "external LLM prompt_version must be one of: "
                + ", ".join(sorted(SUPPORTED_PROMPT_VERSIONS))
            )
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
        if self.pricing is not None:
            if not isinstance(self.pricing, PricingProvenance):
                raise ValueError("pricing must be an instance of PricingProvenance")
            object.__setattr__(self, "prompt_price_per_m", self.pricing.prompt_price_per_m)
            object.__setattr__(self, "completion_price_per_m", self.pricing.completion_price_per_m)
        elif self.prompt_price_per_m is not None and self.completion_price_per_m is not None:
            if not isinstance(self.prompt_price_per_m, (int, float)) or isinstance(self.prompt_price_per_m, bool) or not math.isfinite(float(self.prompt_price_per_m)) or float(self.prompt_price_per_m) < 0:
                raise ValueError("prompt_price_per_m must be a non-negative number")
            if not isinstance(self.completion_price_per_m, (int, float)) or isinstance(self.completion_price_per_m, bool) or not math.isfinite(float(self.completion_price_per_m)) or float(self.completion_price_per_m) < 0:
                raise ValueError("completion_price_per_m must be a non-negative number")
            prov = PricingProvenance(
                pricing_id="custom-explicit-v1",
                snapshot_date="2026-08-01",
                provider="custom",
                applicable_model=self.model,
                prompt_price_per_m=float(self.prompt_price_per_m),
                completion_price_per_m=float(self.completion_price_per_m),
                source_provenance="explicit-runtime-configuration",
            )
            object.__setattr__(self, "pricing", prov)
        elif self.prompt_price_per_m is not None or self.completion_price_per_m is not None:
            raise ValueError("both prompt_price_per_m and completion_price_per_m are required together")

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "ExternalLLMConfig":
        """Load configuration from environment variables."""

        selected = os.environ if environ is None else environ
        endpoint = selected.get(ENDPOINT_ENV_VAR, "")
        model = selected.get(MODEL_ENV_VAR, "")
        api_key = selected.get(API_KEY_ENV_VAR, "").strip()
        prompt_version = (
            selected.get(PROMPT_VERSION_ENV_VAR)
            or selected.get(LEGACY_PROMPT_VERSION_ENV_VAR)
            or DEFAULT_PROMPT_VERSION
        ).strip()
        if not endpoint:
            raise ValueError(f"{ENDPOINT_ENV_VAR} is required for the external_llm backend")
        if not model:
            raise ValueError(f"{MODEL_ENV_VAR} is required for the external_llm backend")
        if not api_key:
            raise ValueError(f"{API_KEY_ENV_VAR} is required for the external_llm backend (missing_credentials)")

        try:
            timeout = float(selected.get(TIMEOUT_ENV_VAR, "10"))
            temperature = float(selected.get(TEMPERATURE_ENV_VAR, "0"))
            max_retries = int(selected.get(MAX_RETRIES_ENV_VAR, "2"))
            retry_backoff_seconds = float(selected.get(RETRY_BACKOFF_ENV_VAR, "0"))
            total_timeout = float(selected.get(TOTAL_TIMEOUT_ENV_VAR, "30"))
            max_concurrent_recommendations = int(
                selected.get(MAX_CONCURRENT_ENV_VAR, "4")
            )
            pricing: PricingProvenance | None = None
            pricing_config_path = selected.get(PRICING_CONFIG_PATH_ENV_VAR, "").strip()
            if pricing_config_path:
                pricing = PricingProvenance.from_file(pricing_config_path)
            elif (
                PROMPT_PRICE_PER_M_ENV_VAR in selected
                and selected[PROMPT_PRICE_PER_M_ENV_VAR].strip()
                and COMPLETION_PRICE_PER_M_ENV_VAR in selected
                and selected[COMPLETION_PRICE_PER_M_ENV_VAR].strip()
            ):
                prompt_p = float(selected[PROMPT_PRICE_PER_M_ENV_VAR])
                completion_p = float(selected[COMPLETION_PRICE_PER_M_ENV_VAR])
                pricing_id = selected.get(PRICING_ID_ENV_VAR, "env-custom-v1").strip() or "env-custom-v1"
                pricing_date = selected.get(PRICING_DATE_ENV_VAR, "2026-08-01").strip() or "2026-08-01"
                pricing_source = selected.get(PRICING_SOURCE_ENV_VAR, "environment-configuration").strip() or "environment-configuration"
                pricing = PricingProvenance(
                    pricing_id=pricing_id,
                    snapshot_date=pricing_date,
                    provider="openai-compatible",
                    applicable_model=model,
                    prompt_price_per_m=prompt_p,
                    completion_price_per_m=completion_p,
                    source_provenance=pricing_source,
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
            pricing=pricing,
            prompt_version=prompt_version,
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


class LLMCompletionResponse(str):
    """Response string with attached token usage, latency, and raw envelope metadata."""

    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    inference_latency_seconds: float | None
    raw_response_envelope: Mapping[str, Any] | None

    def __new__(
        cls,
        content: str,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        inference_latency_seconds: float | None = None,
        raw_response_envelope: Mapping[str, Any] | None = None,
    ) -> "LLMCompletionResponse":
        instance = super().__new__(cls, content)
        instance.prompt_tokens = prompt_tokens
        instance.completion_tokens = completion_tokens
        instance.total_tokens = total_tokens
        instance.inference_latency_seconds = inference_latency_seconds
        instance.raw_response_envelope = raw_response_envelope
        return instance

    @property
    def content(self) -> str:
        return str(self)



@runtime_checkable
class LLMClient(Protocol):
    """Adapter boundary implemented by an external LLM provider client."""

    def complete(
        self, request: LLMCompletionRequest, *, timeout: float
    ) -> LLMCompletionResponse | str:
        """Return structured completion response or assistant text content."""

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

    def complete(
        self, request: LLMCompletionRequest, *, timeout: float
    ) -> LLMCompletionResponse:
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
        started = time.monotonic()
        response = self._transport.post_json(
            self._endpoint,
            headers=headers,
            payload=payload,
            timeout=timeout,
        )
        inference_latency = max(0.0, time.monotonic() - started)
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(
                "external LLM response is missing assistant content"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("external LLM assistant content must be non-empty text")
        usage = response.get("usage")
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None
        if isinstance(usage, dict):
            if isinstance(usage.get("prompt_tokens"), int):
                prompt_tokens = usage["prompt_tokens"]
            if isinstance(usage.get("completion_tokens"), int):
                completion_tokens = usage["completion_tokens"]
            if isinstance(usage.get("total_tokens"), int):
                total_tokens = usage["total_tokens"]
        return LLMCompletionResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            inference_latency_seconds=inference_latency,
            raw_response_envelope=response,
        )


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


def system_prompt_for_version(version: str) -> str:
    """Return the frozen system prompt for a supported interface version."""

    if version == PROMPT_VERSION_V4_0:
        return (
            "You recommend one JupyterHub resource profile and one administrator-"
            "allowlisted notebook image. Return exactly one JSON object matching the "
            "provided schema. Do not include Markdown, code fences, or extra fields. "
            "Never invent an image ID. Keep reasons concise and grounded only in the input."
        )
    if version == PROMPT_VERSION_V4_1:
        return (
            "You are a resource recommendation engine for JupyterHub. "
            "Recommend one resource profile (small, medium, or large) and one notebook image ID from the administrator catalog. "
            "You MUST return a single valid JSON object containing exactly these five fields:\n"
            "- \"profile\": string, one of [\"small\", \"medium\", \"large\"]\n"
            "- \"reasons\": list of non-empty strings explaining why this profile was chosen\n"
            "- \"score\": number between 0 and 100 representing confidence, or null\n"
            "- \"image_id\": string, exactly matching an allowed image ID from the catalog\n"
            "- \"image_reasons\": list of non-empty strings explaining why this image was chosen\n"
            "The score field is required even when its value is null. "
            "Do not wrap in markdown fences or include explanations outside the JSON object. Output pure JSON only."
        )
    raise ValueError(f"unsupported prompt version: {version}")


def prompt_contract_sha256(version: str) -> str:
    """Hash the exact prompt and response schema used by the backend."""

    import hashlib

    payload = json.dumps(
        {"system_prompt": system_prompt_for_version(version), "response_schema": RESPONSE_SCHEMA},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
        system_prompt = system_prompt_for_version(self.config.prompt_version)
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
        raw_response: str | None = None,
        parsed_profile: str | None = None,
        parsed_image_id: str | None = None,
        validation_error: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        inference_latency_seconds: float | None = None,
        estimated_cost_usd: float | None = None,
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
                    raw_response=raw_response,
                    parsed_profile=parsed_profile,
                    parsed_image_id=parsed_image_id,
                    validation_error=validation_error,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    inference_latency_seconds=inference_latency_seconds,
                    estimated_cost_usd=estimated_cost_usd,
                    pricing_id=self.config.pricing.pricing_id if self.config.pricing else None,
                    pricing_provenance=self.config.pricing.source_provenance if self.config.pricing else None,
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
        last_raw_response: str | None = None
        last_parsed_profile: str | None = None
        last_parsed_image_id: str | None = None
        last_validation_error: str | None = None
        last_prompt_tokens: int | None = None
        last_completion_tokens: int | None = None
        last_total_tokens: int | None = None
        last_inference_latency: float | None = None
        last_estimated_cost: float | None = None

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
                completion = self._client.complete(
                    completion_request,
                    timeout=min(float(self.config.timeout), remaining),
                )
                if isinstance(completion, LLMCompletionResponse):
                    content = completion.content
                    last_prompt_tokens = completion.prompt_tokens
                    last_completion_tokens = completion.completion_tokens
                    last_total_tokens = completion.total_tokens
                    last_inference_latency = completion.inference_latency_seconds
                else:
                    content = str(completion)

                last_raw_response = content
                if self.config.pricing is not None and last_prompt_tokens is not None and last_completion_tokens is not None:
                    last_estimated_cost = self.config.pricing.calculate_cost_usd(
                        last_prompt_tokens, last_completion_tokens
                    )
                else:
                    last_estimated_cost = None

                try:
                    decoded = _decode_structured_output(content)
                    if isinstance(decoded, dict):
                        last_parsed_profile = str(decoded["profile"]) if "profile" in decoded else None
                        last_parsed_image_id = str(decoded["image_id"]) if "image_id" in decoded else None
                except Exception as parse_exc:
                    last_validation_error = type(parse_exc).__name__

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
                        raw_response=last_raw_response,
                        parsed_profile=last_parsed_profile,
                        parsed_image_id=last_parsed_image_id,
                        validation_error=None,
                        prompt_tokens=last_prompt_tokens,
                        completion_tokens=last_completion_tokens,
                        total_tokens=last_total_tokens,
                        inference_latency_seconds=last_inference_latency,
                        estimated_cost_usd=last_estimated_cost,
                        pricing_id=self.config.pricing.pricing_id if self.config.pricing else None,
                        pricing_provenance=self.config.pricing.source_provenance if self.config.pricing else None,
                    ),
                )
            except Exception as exc:
                last_error = self._sanitized_error(exc)
                category = self._error_category(exc)
                last_category = category
                last_validation_error = type(exc).__name__
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
            raw_response=last_raw_response,
            parsed_profile=last_parsed_profile,
            parsed_image_id=last_parsed_image_id,
            validation_error=last_validation_error,
            prompt_tokens=last_prompt_tokens,
            completion_tokens=last_completion_tokens,
            total_tokens=last_total_tokens,
            inference_latency_seconds=last_inference_latency,
            estimated_cost_usd=last_estimated_cost,
        )

    def recommend(self, request: RecommendationRequest) -> SpawnRecommendation:
        """Preserve the synchronous legacy API and return only its recommendation."""

        return self.recommend_with_metadata(request).recommendation


__all__ = [
    "API_KEY_ENV_VAR",
    "ALLOW_INSECURE_HTTP_ENV_VAR",
    "BACKEND_NAME",
    "BACKEND_VERSION",
    "COMPLETION_PRICE_PER_M_ENV_VAR",
    "ENDPOINT_ENV_VAR",
    "ExternalLLMConfig",
    "ExternalLLMError",
    "ExternalLLMFallbackError",
    "ExternalLLMRecommender",
    "JSONHTTPTransport",
    "LLMClient",
    "LLMClientError",
    "LLMCompletionRequest",
    "LLMCompletionResponse",
    "LLMDeadlineExceededError",
    "LLMMessage",
    "LLMOutputValidationError",
    "LLMResponseError",
    "LLMTimeoutError",
    "MAX_CONCURRENT_ENV_VAR",
    "MAX_RETRIES_ENV_VAR",
    "MODEL_ENV_VAR",
    "OpenAICompatibleClient",
    "PRICING_CONFIG_PATH_ENV_VAR",
    "PRICING_DATE_ENV_VAR",
    "PRICING_ID_ENV_VAR",
    "PRICING_SOURCE_ENV_VAR",
    "PricingProvenance",
    "PROMPT_PRICE_PER_M_ENV_VAR",
    "RESPONSE_SCHEMA",
    "RETRY_BACKOFF_ENV_VAR",
    "TEMPERATURE_ENV_VAR",
    "TIMEOUT_ENV_VAR",
    "TOTAL_TIMEOUT_ENV_VAR",
    "UrllibJSONTransport",
]
