# External LLM Blocker and Zero-Fabrication Report

## Status

No external LLM credential or operator-supplied endpoint/model configuration
was available. The environment was checked only for presence; secret values
were neither printed nor persisted. The frozen default requested model was
`gemini-2.0-flash`. Its availability could not be queried without a configured
endpoint and credential, and no substitute model was selected.

The revised held-out run still preserves the complete randomized matrix. It
contains 240 `external_llm` records, each with `error_category` set to
`missing_credentials`, `attempt_count` zero, no raw response, no token usage,
and no monetary cost. These rows represent explicit unavailability, not zero
accuracy and not successful external calls.

## Claim impact

- External recommendation quality, latency, retries, schema validity, cost,
  repeat consistency, and provider reliability are not measured.
- RQ1–RQ3 are only partially claimable because the static, rule-based, and
  local-Ollama conditions have real evidence but the fourth condition does not.
- RQ5 is not claimable because no empirical external-versus-local comparison
  exists.
- No simulated completion, token count, latency, price, or provider response is
  present in the evidence.

## Reproduction when securely configured

First run the development split with the repository's configured endpoint and
model. Do not silently replace the requested model. Example command (the shell
must already contain secret configuration):

```bash
.venv/bin/python -m evaluation_v4.run_recommenders \
  --recommenders external_llm \
  --split development --repeats 1 --seed 20260812 \
  --prompt-version prompt-v4.1.0 \
  --experiment-id protocol-v4-external-development-<timestamp> \
  --output results/v4-external-development-<timestamp>
```

Only after validating model identity, schema validity, and redaction should a
new held-out experiment ID be used. Pricing must remain N/A unless a dated,
versioned price and source are explicitly configured.
