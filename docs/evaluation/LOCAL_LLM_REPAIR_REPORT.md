# Local Ollama Repair and Validation Report

## Outcome

The historical run `results/v4-live-20260810` remains unchanged. Its local
`llama3:latest` condition made 240 live calls, but every response omitted the
required `score` field and every trial fell back to the rule engine. Those
fallback recommendations are not LLM accuracy.

The repaired interface was developed on the 12-sample development split and
then frozen before a new held-out run. Development evidence is in
`results/v4-ollama-dev-20260812T095254Z`; revised held-out evidence is in
`results/v4-revised-test-20260812T095453Z`.

## Root cause and repair

The incident was an interface-compliance failure, not a justification for
weakening the contract. The legacy prompt embedded a schema but did not plainly
enumerate its five required fields, and the native Ollama request used
`format: "json"`, which guarantees JSON syntax but does not enforce the object
shape. `llama3:latest` consequently returned JSON without `score`.

The repair introduced `prompt-v4.1.0` and backend version
`self-hosted-llm-v2`:

- the system instruction explicitly requires exactly `profile`, `reasons`,
  `score`, `image_id`, and `image_reasons`;
- `score` remains required and may be a number from 0 through 100 or `null`;
- missing or additional fields remain invalid and invoke the recorded fallback;
- native `/api/chat` requests pass the full JSON Schema in `format`, rather
  than the weaker `"json"` mode;
- the exact prompt plus schema is frozen by SHA-256
  `14f73b70950da7e20451916f6580768da74fd1ea9abaf1d91d324f099415ccfe`.

No semantic profile/image rules, thresholds, gold labels, or policy mappings
were changed. Because the historical held-out failure revealed the interface
defect, the new test run is explicitly a revised confirmatory protocol, not the
original frozen experiment.

## Development validation

The 12 development records produced 12/12 schema-valid raw responses, zero
retries, zero fallbacks, and zero errors. Median end-to-end latency was 6.104 s
and p95 was 9.884 s. This smoke run established interface viability; it was not
used as held-out evidence.

## Frozen held-out results

The revised run contains 240 local-Ollama trials: 48 held-out samples times five
repeats. All 240 responses were raw-valid; schema failures, retries, fallbacks,
and errors were all 0/240. The prediction-stream SHA-256 is
`5bdbf1575ff747366e700fb9c8d6c34d4811099f548ff7e6a52ba58fcab32484`.

| Metric | Local Ollama |
| --- | ---: |
| Raw/applied profile acceptable | 0.5833 |
| Raw/applied image acceptable | 0.6042 |
| Raw/applied joint acceptable | 0.4375 |
| Under-provisioned | 0.3750 |
| Over-provisioned | 0.0417 |
| Policy rejection | 0.0000 |
| Median / p95 end-to-end latency | 9.204 / 14.736 s |
| Median native inference latency | 8.320 s |
| Mean prompt / completion / total tokens | 584.85 / 95.40 / 680.25 |
| Configured monetary cost | N/A |

At temperature zero, all 48 samples had identical raw and applied outputs across
their five repeats (dominant-output rate 1.0). Operational reliability was
therefore high, but recommendation quality did not improve over the baselines:
profile acceptability was 4.17 percentage points below static and 20.83 points
below rule-based. Neither profile difference was statistically significant
after Holm correction and workload-family-clustered confidence intervals both
included zero.

## Stage C qualification

The clean system run `results/v4-stage-c-validation-v4.2-20260813T013600Z`
froze `http://127.0.0.1:11434/api/chat`, `llama3:latest`, prompt v4.1.0,
temperature 0, and timeout 60 seconds. A preflight guard rejected fallback
decisions before pod creation. All eight Ollama Stage C decisions were genuine,
schema-valid, and non-fallback; five workloads completed and three were
OOM-killed.

The earlier `v4-stage-c-validation-v4.1-20260813T012000Z` run used an
unreachable default endpoint and all eight nominal Ollama cells fell back to
rules. It is retained only as diagnostic evidence and is excluded from the
combined analysis.
