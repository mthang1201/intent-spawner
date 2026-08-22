# P3 Incremental Evaluation over Frozen P2

## Decision

**Does P3 provide enough incremental value over P2 to justify including it in
the main thesis contribution?**

**No.** In this frozen paired evaluation, P3 produced no wrong-to-correct
queries, changed one P2-correct query to wrong, reduced preferred Top-1 and
acceptable-candidate accuracy by 1.61 percentage points, reduced MRR and
nDCG@5, returned invalid output in 12 of 62 reranker invocations (19.35%), and
increased median end-to-end latency from 8.59 ms to 30.69 s. The observed
reduction in gold-labeled hard-constraint violations does not offset the
ranking regressions, invalid-output rate, and latency cost.

P2 should remain the main thesis contribution. This P3 configuration can be
reported as a negative/optional extension result, not as evidence of a better
primary system.

## Experimental isolation and provenance

P3 adds exactly one conceptual component:

```text
frozen P2 feasible ranking
→ schema-validated LLM reranker
```

The P3 backend calls `P2Recommender` once. It has no independent extraction,
retrieval, RRF, catalog, hard-constraint, or deterministic-ranking parameters.
It sends the complete P2-feasible ordered candidate set to the reranker. The
model may only reorder supplied IDs, return normalized scores, and provide
bounded explanations. Every selected ID is re-resolved through the same
administrator corpus and must have a feasible P2 constraint evaluation. Any
timeout, provider error, unknown/duplicate/omitted ID, or schema failure returns
the exact P2 recommendation.

The run used the unchanged 66-query dataset from the observed P2 evaluation:
60 frozen v4 queries plus six P2 constraint diagnostics. It made no B0 user
experiment. A hash gate covered P1, P2, the catalog/corpus builders, the local
StructuredIntent parser/prompt contract, sparse and dense retrieval, RRF,
constraint evaluation/ranking, and both dataset sources. The embedded P2
outputs matched the preserved P2 reference on all 66 queries across final ID,
full ranking, retrieval/feasible lists, infeasible detection, constraint result,
policy result, and fallback category.

Observed model configuration:

- Provider: local Ollama OpenAI-compatible endpoint; no external API billing.
- Model: `llama3:latest`, digest
  `365c0bd3c000a25d28ddbf732fe1c6add414de7275464c4e4d1c3b5fcb5d8ad1`.
- Artifact: GGUF, 8.0B, Q4_0, 8,192-token context.
- Temperature: 0; retries: 0; attempt timeout: 120 s; total timeout: 180 s.
- Reranker prompt: `p3-reranker-prompt-v1.0.0`, SHA-256
  `696dabd22010a03c110b315c48b84efb5b8517733c2174a21aa79be4807d1e44`.

Primary evidence:

- [Run manifest](../../evaluation_p3/results/20260821T-observed-p2-p3-ollama-llama3-v1/manifest.json)
- [Unchanged raw paired predictions](../../evaluation_p3/results/20260821T-observed-p2-p3-ollama-llama3-v1/raw/predictions.jsonl)
- [Aggregate metrics](../../evaluation_p3/results/20260821T-observed-p2-p3-ollama-llama3-v1/aggregates/metrics.json)
- [Corrected exact per-query paired changes](../../evaluation_p3/results/20260821T-observed-p2-p3-ollama-llama3-v1/corrections/20260821T-transition-definition-v1.1/paired_changes.json)
- [Corrected error transitions](../../evaluation_p3/results/20260821T-observed-p2-p3-ollama-llama3-v1/corrections/20260821T-transition-definition-v1.1/error_transitions.json)
- [Correction provenance](../../evaluation_p3/results/20260821T-observed-p2-p3-ollama-llama3-v1/corrections/20260821T-transition-definition-v1.1/correction-manifest.json)

The original transition artifact is preserved. Its v1.0 definition treated the
four correctly rejected infeasible queries as wrong because they have empty
acceptable-candidate sets. The non-overwriting v1.1 correction counts
acceptable Top-1 for feasible queries and infeasible detection for infeasible
queries. Raw model and P2 predictions were not changed.

## Quality

| Metric | P2 | P3 | P3 − P2 |
| --- | ---: | ---: | ---: |
| Preferred Top-1 accuracy | 32/62 (51.61%) | 31/62 (50.00%) | -1.61 pp |
| Acceptable-candidate accuracy | 42/62 (67.74%) | 41/62 (66.13%) | -1.61 pp |
| MRR | 0.7948 | 0.7814 | -0.0134 |
| nDCG@5 | 0.8102 | 0.8027 | -0.0076 |

The reranker changed the full ordering on 8 of 66 queries and changed Top-1 on
3. Only one Top-1 change altered correctness, and it was a regression:

| Query | P2 Top-1 | P3 Top-1 | Paired result |
| --- | --- | --- | --- |
| `pytorch-training-canonical-en` | `large-pytorch-deep-learning` | `small-pytorch-deep-learning` | P2 correct → P3 wrong |
| `gpu-policy-canonical-en` | `large-pytorch-deep-learning` | `small-pytorch-deep-learning` | P2 wrong → P3 wrong |
| `gpu-policy-vi` | `large-pytorch-deep-learning` | `small-pytorch-deep-learning` | P2 wrong → P3 wrong |

The versioned paired-change artifact reports P2/P3 Top-1 IDs, preferred and
acceptable correctness, first acceptable rank, transition, ranking/top-1
change flags, latency delta, reranker outcome, token usage, and cost for every
one of the 66 queries.

## Correctness and trust boundaries

| Metric | P2 | P3 |
| --- | ---: | ---: |
| Gold-labeled hard-constraint violation rate | 7/62 (11.29%) | 5/62 (8.06%) |
| Selected outside P2-feasible set | n/a | 0/62 (0.00%) |
| PolicyValidator compliance | 66/66 (100%) | 66/66 (100%) |
| Invalid reranker output rate | n/a | 12/62 (19.35%) |

The two fewer gold-labeled violations were `gpu-policy-canonical-en` and
`gpu-policy-vi`. Both remained wrong under acceptable-candidate accuracy, so
they are not ranking corrections. The authoritative P3 safety boundary held:
the reranker never selected outside the frozen P2-feasible set, no invented ID
influenced a recommendation, and all final recommendations passed the existing
`PolicyValidator`.

Invalid outputs were handled deterministically:

- 7 omitted one or more required candidate IDs;
- 4 returned an unknown or infeasible candidate ID;
- 1 duplicated a candidate ID.

All 12 degraded to the exact P2 recommendation.

## Cost and complexity

| Metric | P2 | P3 |
| --- | ---: | ---: |
| End-to-end latency p50 | 0.0086 s | 30.6898 s |
| End-to-end latency p95 | 0.0149 s | 42.2344 s |
| Provider failure rate | n/a | 0/62 (0.00%) |
| Provider failure or fallback rate | n/a | 12/62 (19.35%) |

Token usage over 62 invocations:

| Usage | Sum | p50/query | p95/query |
| --- | ---: | ---: | ---: |
| Prompt tokens | 124,430 | 2,032 | 2,060 |
| Completion tokens | 12,730 | 192 | 296.4 |
| Total tokens | 137,160 | 2,222 | 2,356.4 |

External cost is **not available/applicable** for this run: inference was local
and no external price provenance applied. Hardware amortization and energy cost
were not measured, so no monetary cost was manufactured.

## Error transitions

Query correctness is acceptable Top-1 for feasible queries and correct
infeasible detection without reranking for infeasible queries.

| Transition | Count |
| --- | ---: |
| P2 wrong → P3 correct | 0 |
| P2 correct → P3 correct | 45 |
| P2 correct → P3 wrong | 1 |
| P2 wrong → P3 wrong | 20 |

Net corrections are -1. Among the 62 feasible queries alone, the counts are
0, 41, 1, and 20 respectively; all four infeasible queries were correctly
detected by both systems and bypassed reranking.

## Limitations and work not performed

- This is one observed temperature-zero run of one local quantized 8B model;
  it does not establish that every possible reranker or provider must fail.
- Provider/model comparison is not a primary research question and was not
  expanded after this negative result.
- The 66-query dataset is synthetic and includes only six P2 constraint
  diagnostics. No B0 user study or Kubernetes cluster experiment was run.
- Local latency is hardware/provider-specific. External latency and cost were
  not inferred without evidence.
- No post-hoc prompt tuning, P2 change, catalog change, or test-set-specific
  reranker repair was performed after observing results.
- P3 remains integrated behind a separate backend with deterministic P2
  fallback, but this evidence does not justify promoting it into the main
  thesis contribution.
