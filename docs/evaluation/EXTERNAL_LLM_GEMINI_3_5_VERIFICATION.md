# Protocol-v4 External LLM Verification: Gemini 3.5 Flash

Verification date: 2026-08-13 (Asia/Ho_Chi_Minh)

## Scope and decision

This is pre-held-out verification evidence for the explicitly selected
`gemini-3.5-flash` model. It does not contain held-out predictions and does not
modify any historical Stage A/B/C evidence.

The gate passed. The Protocol-v4 external held-out matrix may run with the
model-only amendment from the provider-retired `gemini-2.0-flash` model to the
stable `gemini-3.5-flash` model. The endpoint, prompt/schema, policy/catalog,
temperature, timeouts, retries, backoff, and scoring remain frozen.

## Credential and endpoint verification

- Credential source: Kubernetes Secret `intent-spawner-external-llm` in
  namespace `z2jh-context-demo`, key `api-key`.
- Verification exposed only Secret/key presence. The value was injected into
  subprocess environments and was never printed, written to evidence, or
  supplied as a command-line argument.
- Endpoint:
  `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`.
- Requested model: `gemini-3.5-flash`.
- Provider response model: `gemini-3.5-flash`.
- The exact repository payload returned a valid OpenAI-compatible envelope,
  one choice, non-empty assistant content, and `finish_reason: stop`.
- Direct request latency: 3.739782 seconds.
- Direct request usage: 579 prompt tokens, 148 completion tokens, 1,303 total
  tokens. The total includes provider-reported non-output/thinking usage and
  therefore need not equal prompt plus completion tokens.

An earlier direct probe returned HTTP 200 but empty assistant content. It is
retained as a transient failed gate in `NEXT_AGENT_CONTEXT.md`; it was not
treated as a pass and no held-out trial was run after it.

## Repository pipeline verification

Development sample: `basic-python-canonical-en`.

| Check | Observed result |
| --- | --- |
| Requested/effective backend | `external_llm` / `external_llm` |
| Requested model | `gemini-3.5-flash` |
| Schema parsing | pass |
| Raw profile/image | `small` / `minimal-python` |
| Applied profile | `small` |
| Policy compliant | yes |
| Attempts/retry | 1 / no |
| Fallback | no |
| End-to-end latency | 3.842254 seconds |
| Provider latency | 3.842043 seconds |
| Prompt/completion/total tokens | 579 / 142 / 1,272 |
| Monetary cost | unavailable; no reproducible pricing snapshot configured |

This verifies credentials, endpoint reachability, configured/returned model
identity, structured response parsing, policy application, fallback isolation,
latency telemetry, and token telemetry before the held-out matrix.

## Frozen live configuration

```text
model=gemini-3.5-flash
prompt_version=prompt-v4.1.0
temperature=0
attempt_timeout_seconds=10
total_timeout_seconds=30
max_retries=2
initial_retry_backoff_seconds=0.25
max_concurrent_recommendations=4
allow_insecure_http=false
pricing=unconfigured
```

Official provider references checked at verification time:

- https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash
- https://ai.google.dev/gemini-api/docs/openai
- https://ai.google.dev/gemini-api/docs/deprecations

## Gate conclusion

PASS. It is permissible to execute exactly 48 test samples times five
repetitions in randomized/counterbalanced order. Raw LLM metrics must exclude
fallback outputs; applied metrics may describe the post-fallback operational
decision separately.
