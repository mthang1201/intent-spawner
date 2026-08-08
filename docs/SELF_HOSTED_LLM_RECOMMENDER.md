# Self-hosted LLM Recommender

## Scope

`self_hosted_llm` is an adapter from the existing recommender framework to an
already-running, locally managed inference API. It implements the same
`Recommender.recommend(RecommendationRequest) -> SpawnRecommendation` contract
as the rule-based and external-API backends.

This repository does not train a model, install or start an inference server,
deploy a model, or provision GPUs. Operators own those concerns separately.

## Architecture and behavior

```text
RecommendationRequest
  -> SelfHostedLLMRecommender
  -> shared ExternalLLMRecommender prompt/retry/validation flow
  -> OpenAICompatibleClient
  -> locally hosted HTTP endpoint
  -> SpawnRecommendation
  -> PolicyValidator
```

The self-hosted class intentionally reuses the external backend's prompting,
strict JSON parsing, image-catalog enforcement, timeout/retry behavior, and
rule-based fallback. The only differences are its configuration namespace and
the successful result metadata:

- `backend_name=self_hosted_llm`;
- `backend_version=self-hosted-llm-v1`.

If local inference fails or returns invalid output, the same
`RuleBasedRecommender` fallback runs and identifies itself as `rule_based`.
This makes fallback visible to preview and audit consumers.

The included client expects an OpenAI-compatible Chat Completions response.
Both [vLLM](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)
and [Ollama](https://docs.ollama.com/api/openai-compatibility) document a
`/v1/chat/completions` compatibility endpoint. Other local servers work when
they accept the same request envelope and return assistant content in
`choices[0].message.content`.

## Configuration

Select the backend with `RECOMMENDER_BACKEND=self_hosted_llm`.

| Environment variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `SELF_HOSTED_LLM_ENDPOINT` | Yes | none | Absolute HTTP(S) Chat Completions endpoint |
| `SELF_HOSTED_LLM_MODEL` | Yes | none | Locally served model name sent in each request |
| `SELF_HOSTED_LLM_TIMEOUT` | No | `10` | Positive per-attempt timeout in seconds |
| `SELF_HOSTED_LLM_TOTAL_TIMEOUT` | No | `30` | Total admission/retry/backoff budget in seconds |
| `SELF_HOSTED_LLM_MAX_CONCURRENT_RECOMMENDATIONS` | No | `4` | Fixed network worker/admission limit (1–64) |
| `SELF_HOSTED_LLM_API_KEY` | No | empty | Optional bearer credential |
| `SELF_HOSTED_LLM_TEMPERATURE` | No | `0` | Sampling temperature from 0 through 2 |
| `SELF_HOSTED_LLM_MAX_RETRIES` | No | `2` | Retries after the first attempt |
| `SELF_HOSTED_LLM_RETRY_BACKOFF_SECONDS` | No | `0` | Initial exponential-backoff delay |

Authentication is omitted when `SELF_HOSTED_LLM_API_KEY` is blank. When set,
the adapter sends `Authorization: Bearer <value>`. Load credentials from the
deployment's secret mechanism; do not commit them to Helm values or source.

Example for a vLLM-style local endpoint:

```bash
export RECOMMENDER_BACKEND=self_hosted_llm
export SELF_HOSTED_LLM_ENDPOINT=http://127.0.0.1:8000/v1/chat/completions
export SELF_HOSTED_LLM_MODEL=locally-served-model
export SELF_HOSTED_LLM_TIMEOUT=15
export SELF_HOSTED_LLM_TOTAL_TIMEOUT=25
export SELF_HOSTED_LLM_MAX_CONCURRENT_RECOMMENDATIONS=4
export SELF_HOSTED_LLM_API_KEY='optional-local-token'
```

Example for Ollama's OpenAI-compatible endpoint:

```bash
export RECOMMENDER_BACKEND=self_hosted_llm
export SELF_HOSTED_LLM_ENDPOINT=http://127.0.0.1:11434/v1/chat/completions
export SELF_HOSTED_LLM_MODEL=installed-model-name
export SELF_HOSTED_LLM_TIMEOUT=30
```

The configured endpoint must be reachable from the process running the Hub.
For a containerized Hub, `127.0.0.1` refers to that Hub container rather than
the operator's workstation, so use a deployment-resolvable service address.

Unlike `external_llm`, the backend library permits HTTP with an optional bearer
token. The production Helm wiring requires HTTPS by default and needs an
explicit `SELF_HOSTED_LLM_ALLOW_INSECURE_HTTP=true` assertion for a local mock
or trusted in-cluster path. The operator is asserting that the path is inside a
trusted network boundary. HTTP exposes the token and request content to
anything able to observe that path, so production deployments should prefer
HTTPS/mTLS and enforce namespace isolation, NetworkPolicy, service identity,
and least-privilege credentials. Never route this HTTP exception over an
untrusted or public network.

Python composition can inject configuration directly:

```python
backend = create_recommender(
    "self_hosted_llm",
    config=SelfHostedLLMConfig(
        endpoint="http://inference.internal:8000/v1/chat/completions",
        model="locally-served-model",
        timeout=10,
        api_key="loaded-from-a-secret-source",
    ),
)
recommendation = backend.recommend(request)
```

## Validation and tests

The model returns only profile, reasons, score, image ID, and image reasons.
The adapter rejects unknown profiles/images, malformed or extra fields,
unbounded reasons, and invalid scores. It then fills the immutable image
reference and version fields from local trusted data and returns
`SpawnRecommendation`.

`tests/test_recommender_backends_integration.py` selects rule-based, external,
and self-hosted backends through `RECOMMENDER_BACKEND`, exercises both LLM
paths through a real local HTTP stub, and passes all three outputs through the
same `PolicyValidator`. `recommender/test_self_hosted_llm.py` covers local
configuration, successful output metadata, and deterministic fallback.
