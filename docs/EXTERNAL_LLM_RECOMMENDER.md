# External LLM Recommender

## Architecture

The `external_llm` backend extends the existing recommender framework without
changing its caller contract:

```text
RecommendationRequest
  -> ExternalLLMRecommender (prompting, retry, parsing, validation)
  -> LLMClient (provider-neutral protocol)
  -> provider adapter and JSONHTTPTransport
  -> SpawnRecommendation
```

`ExternalLLMRecommender` contains recommendation behavior but no provider HTTP
format. It builds a provider-neutral `LLMCompletionRequest`, calls an injected
`LLMClient`, and validates the returned text. `OpenAICompatibleClient` is the
included chat-completions adapter. `UrllibJSONTransport` provides the default
dependency-free HTTP implementation and can be replaced in tests or at
deployment boundaries. JupyterHub calls network backends through one bounded
`AsyncRecommendationExecutor`; its fixed worker count matches the configured
network concurrency limit. Rule-based recommendations bypass the executor.

Gemini- or Anthropic-native APIs can be supported by adding an `LLMClient`
adapter that translates `LLMCompletionRequest` to that provider's request and
response envelopes. The prompt, output validation, fallback, registry, callers,
and `SpawnRecommendation` conversion do not change. OpenAI-compatible services
can use the included adapter by configuring their full chat-completions
endpoint.

## Output and trust boundary

The model must return exactly these JSON fields:

```json
{
  "profile": "small | medium | large | gpu_or_large",
  "reasons": ["one or more concise reasons"],
  "score": 0,
  "image_id": "an ID from the administrator catalog",
  "image_reasons": ["one or more concise reasons"]
}
```

`score` may also be `null`. Extra fields, missing fields, invalid JSON,
non-finite or out-of-range scores, unknown profiles, unbounded reason lists,
and image IDs outside the catalog are rejected.

The model never supplies an image registry reference, catalog version, policy
version, schema version, or backend metadata. After validation, the backend
resolves the immutable image digest and catalog version from
`recommender/image-catalog.yaml`, adds the existing policy/schema metadata, and
returns the unchanged `SpawnRecommendation` dataclass.

## Configuration

Select the backend with `RECOMMENDER_BACKEND=external_llm`. Its settings are:

| Environment variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `EXTERNAL_LLM_ENDPOINT` | Yes | none | Absolute HTTP(S) chat-completions endpoint |
| `EXTERNAL_LLM_MODEL` | Yes | none | Model identifier passed to the adapter |
| `EXTERNAL_LLM_TIMEOUT` | No | `10` | Per-attempt timeout in seconds; must be positive |
| `EXTERNAL_LLM_TOTAL_TIMEOUT` | No | `30` | End-to-end budget including admission, retries, and backoff |
| `EXTERNAL_LLM_MAX_CONCURRENT_RECOMMENDATIONS` | No | `4` | Fixed network worker/admission limit (1–64) |
| `EXTERNAL_LLM_API_KEY` | Yes in Helm deployment | empty in library composition | Bearer credential loaded from a required Kubernetes Secret |
| `EXTERNAL_LLM_TEMPERATURE` | No | `0` | Sampling temperature from 0 through 2 |
| `EXTERNAL_LLM_MAX_RETRIES` | No | `2` | Retries after the initial attempt |
| `EXTERNAL_LLM_RETRY_BACKOFF_SECONDS` | No | `0` | Initial exponential-backoff delay |
| `EXTERNAL_LLM_ALLOW_INSECURE_HTTP` | No | `false` | Development-only API-key-over-HTTP override |

Example for an OpenAI-compatible endpoint:

```bash
export RECOMMENDER_BACKEND=external_llm
export EXTERNAL_LLM_ENDPOINT=https://llm.example.invalid/v1/chat/completions
export EXTERNAL_LLM_MODEL=deployment-model-name
export EXTERNAL_LLM_TIMEOUT=10
export EXTERNAL_LLM_TOTAL_TIMEOUT=20
export EXTERNAL_LLM_MAX_CONCURRENT_RECOMMENDATIONS=4
export EXTERNAL_LLM_API_KEY='replace-with-a-secret-source'
export EXTERNAL_LLM_TEMPERATURE=0
export EXTERNAL_LLM_MAX_RETRIES=2
export EXTERNAL_LLM_RETRY_BACKOFF_SECONDS=0.25
```

Do not commit API keys or place them directly in Helm values. The production
Helm wiring in `HELM_BACKEND_DEPLOYMENT.md` supplies the key through a required
`secretKeyRef`; library-only composition may still use an unauthenticated mock.
Configuration is validated
when the backend is created; missing endpoint/model values and invalid numeric
ranges fail startup. Per-attempt and total timeouts are capped at 300 seconds,
retries at 10, and the initial backoff at 60 seconds.

An external endpoint carrying an API key must use HTTPS. Plain HTTP with a key
fails startup by default. `EXTERNAL_LLM_ALLOW_INSECURE_HTTP=true` exists only
for isolated development mocks; it must not be enabled in a real deployment.

The external service receives the request's intent, normalized dataset-size
hint, and code context. Operators must review the selected service's retention,
training, residency, and access-control terms before enabling this backend and
must not send notebook code or intent that violates local data-governance
policy. API keys are excluded from configuration representations and never
included in prompts.

Python composition and tests may pass an explicit `ExternalLLMConfig` and an
injected client without changing callers:

```python
backend = create_recommender(
    "external_llm",
    config=ExternalLLMConfig(
        endpoint="https://llm.example.invalid/v1/chat/completions",
        model="deployment-model-name",
        timeout=10,
        api_key="loaded-from-a-secret-source",
        temperature=0,
    ),
    client=provider_adapter,
)
recommendation = backend.recommend(request)
```

## Retry and fallback behavior

Each attempt uses the smaller of its configured timeout and the remaining total
budget. Provider errors, timeouts, malformed
provider envelopes, invalid assistant JSON, and output-validation failures are
retried up to `EXTERNAL_LLM_MAX_RETRIES`. Optional backoff is exponential but
truncated to the remaining deadline. No attempt starts after budget exhaustion.
A small internal reserve (10% of the budget, capped at 50 ms) is kept for the
local rule fallback and response serialization rather than being spent on HTTP.

When attempts are exhausted, the backend delegates the same request to
`RuleBasedRecommender`. That recommendation retains `backend_name=rule_based`,
so callers and audit records can distinguish fallback output. The internal
result records requested/effective backend, fallback status, a fixed error
category, attempts, elapsed time, and timeout/deadline status. It never retains
provider response bodies or exception messages. If the fallback also fails,
`ExternalLLMFallbackError` contains only sanitized typed failures.

Cancellation does not release an executor permit while synchronous HTTP work is
still running. The abandoned call remains bounded by its request/deadline and
the fixed pool size, preventing cancellation from creating an unbounded queue.

## Tests

`recommender/test_external_llm.py` uses injected clients and transports; it
makes no live API calls. It covers prompt construction, exact schema output,
locally resolved images, retry recovery, timeout exhaustion, invalid JSON,
missing and extra fields, malformed provider envelopes, invalid profile/image/
score/reason values, environment configuration, registry creation, and fallback
failure.

Run:

```bash
.venv/bin/python -m pytest -q \
  recommender/test_external_llm.py recommender/test_reliability.py \
  recommender/test_recommender.py
```
