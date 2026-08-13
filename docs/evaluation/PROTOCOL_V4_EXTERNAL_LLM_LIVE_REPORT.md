# Protocol-v4 External LLM Live Evaluation Report

## Evidence identity

The authoritative external run is
`results/v4-external-confirmatory-20260813T045543Z`.

- Run ID: `v4-recommenders-20260813T045633Z-daae41dd`.
- Experiment ID: `protocol-v4-external-confirmatory-20260813T045543Z`.
- Source commit: `3d9eaa8e63f94f257e9cb4a8867aaa059e984dd1`, clean worktree.
- Dataset: `intent-gold-en-vi-2026-08-08`, canonical SHA-256
  `18d4bd33a58f7aa1cea91c223cb6e0f537b6030c91f3954af762e019a8dc7ec0`.
- Predictions SHA-256:
  `62dccc1ce650066bfb9d1f87b2b923c3be6a0e17294677fc14b8d0a92826b7bd`.
- Matrix: 48 held-out samples x five repetitions = 240/240 unique
  records, randomized within repeat blocks with seed `20260808`.
- Provider/endpoint: Google Gemini OpenAI-compatible HTTPS chat completions.
- Requested and verified response model: `gemini-3.5-flash`.
- Prompt/schema: `prompt-v4.1.0`, contract SHA-256
  `14f73b70950da7e20451916f6580768da74fd1ea9abaf1d91d324f099415ccfe`.
- Pricing: unconfigured; monetary cost is unavailable.

The prerequisite gate is documented in
`EXTERNAL_LLM_GEMINI_3_5_VERIFICATION.md`. It verified Secret-backed
credentials without exposing them, HTTP endpoint/model identity, a non-empty
assistant response, schema parsing, policy application, token telemetry,
provider latency, and no fallback on a development sample before the held-out
split was accessed.

## Raw API/model reliability

| Result | Trials | Rate |
| --- | ---: | ---: |
| Schema-valid raw model completion | 21 | 8.75% |
| No raw completion; rule fallback applied | 219 | 91.25% |
| Final sanitized category `transport_error` | 219 | 91.25% |
| Schema failure | 0 | 0% |
| Runner-level terminal error | 0 | 0% |
| Trial used at least one retry | 224 | 93.33% |

Attempt counts were 16 trials with one attempt, three with two attempts, and
221 with three attempts, for 685 provider attempts in total. All 219 fallback
trials exhausted three attempts. Of the 21 successful trials, 16 succeeded on
attempt one, three on attempt two, and two on attempt three.

The implementation intentionally sanitizes provider-controlled HTTP details,
so `transport_error` cannot be subdivided retrospectively into HTTP 429, 5xx,
DNS, or other transport causes. The temporal pattern—19 successes in repeat
block 0, two in block 1, and none in blocks 2–4—is consistent with quota or
rate-limit exhaustion, but that cause is an inference, not directly observed
evidence.

## Accuracy and policy outcomes

Fallback predictions are never credited as raw Gemini accuracy.

| Metric | Raw Gemini, full 240-trial denominator | Raw Gemini, conditional on 21 responses | Applied after fallback/policy |
| --- | ---: | ---: | ---: |
| Exact profile | 12/240 (5.00%) | 12/21 (57.14%) | 159/240 (66.25%) |
| Acceptable profile | 12/240 (5.00%) | 12/21 (57.14%) | 187/240 (77.92%) |
| Exact image | 21/240 (8.75%) | 21/21 (100%) | 240/240 (100%) |
| Acceptable image | 21/240 (8.75%) | 21/21 (100%) | 240/240 (100%) |
| Joint acceptable | 12/240 (5.00%) | 12/21 (57.14%) | 163/240 (67.92%) |

Applied underprovisioning was 45/240 (18.75%); overprovisioning was 8/240
(3.33%). No schema-valid raw Gemini response was rejected by policy. Twenty-four
applied trials (10%) were marked noncompliant before the policy-adjusted
profile was scored; these came from the fallback path and are not raw-model
policy rejections.

The applied result is therefore primarily a reliability/fallback result, not
an estimate of standalone Gemini quality. Its 67.92% applied joint rate is
close to the rule engine's 68.75% because the rule engine supplied 219 of 240
external-condition decisions.

## Latency, tokens, cost, and consistency

| Stratum | Mean end-to-end | Median end-to-end | P95 end-to-end | Median provider latency | P95 provider latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| All 240 trials | 1.5565 s | 1.2961 s | 3.9594 s | N/A | N/A |
| 21 successful completions | 4.2094 s | 4.0095 s | 5.4042 s | 3.9582 s | 5.0042 s |
| 219 fallback trials | 1.3021 s | 1.2902 s | 1.3795 s | unavailable | unavailable |

Token telemetry exists only for the 21 successful completions: 12,468 prompt,
3,262 completion, and 29,392 provider-reported total tokens. Per successful
completion, the means were 593.71, 155.33, and 1,399.62 respectively. Provider
total tokens include non-output/thinking usage and therefore need not equal
prompt plus completion tokens.

Monetary cost is not reported because no versioned model-specific pricing
snapshot was configured. Provider energy/resource consumption, local energy,
and local hardware cost were not measured.

Only two samples received at least two raw completions; both repeated the same
profile/image output. That is too little evidence for a general raw Gemini
repeat-consistency claim. Applied outputs were fully stable for 36/48 samples
with a mean dominant-output rate of 94.58%, but this is largely deterministic
fallback consistency.

## Interpretation boundary

The matrix is complete as an operational evaluation of the configured external
API pipeline. It supports strong conclusions about the observed reliability,
retry/fallback behavior, and post-fallback applied outcomes under this account,
endpoint, model, and time window. It does not support a broad intrinsic-quality
claim for Gemini 3.5 Flash because only 21 responses from 19 held-out samples
were observed and success was strongly confounded with execution order.
