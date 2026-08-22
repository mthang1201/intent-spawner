# P2 Backend Integration and Evaluation v1

## Scope and implementation

The new `p2` backend composes the requested pipeline without modifying the P1
execution path:

```text
RecommendationRequest
→ StructuredIntentExtractor
→ BM25 sparse retrieval + dense embedding retrieval
→ Reciprocal Rank Fusion
→ deterministic hard-constraint evaluation
→ deterministic ranking
→ trusted EnvironmentCandidate resolution
→ existing SpawnRecommendation
→ existing PolicyValidator in the preview runtime
```

The default P2 configuration uses the versioned local explicit parser and the
versioned local feature-hash embedding provider. Both remain replaceable behind
their typed provider interfaces. P1 remains the default deployment backend and
the `p2` Helm overlay opts into P2 explicitly.

Final profile, image ID, immutable image reference, and resource profile values
are resolved from the administrator-owned candidate corpus. Extractor output
cannot carry these values. Unknown retrieved or ranked IDs fail trusted corpus
resolution and trigger the deterministic corpus-resolved fallback. The existing
`PolicyValidator` remains the final trust boundary.

The existing preview endpoint, authentication/XSRF behavior, user binding,
preview TTL, browser input fingerprint invalidation, one-time confirmation,
manual override, immutable-image enforcement, and no-recompute pre-spawn hook
are unchanged. A P2 no-feasible or unsupported-catalog result disables accept
and requires the existing allowlisted manual override.

## Safe operational provenance

P2 records backend/pipeline versions, StructuredIntent schema version,
extractor model/parser and prompt-contract versions, prompt-contract checksum,
dense embedding model revision, dense/sparse/hybrid index versions and
checksums, RRF configuration, corpus/catalog versions, candidate and feasible
counts, final candidate ID, constraint/ranker versions, and fallback category.

Operational provenance contains no raw intent, source code, prompt, model
response, or retrieved free text. Full structured stage traces are retained only
as internal/offline evaluation objects.

## Versioned offline evaluation

The observed run
[`20260821T-observed-p1-p2-v1-4`](../../evaluation_p2/results/20260821T-observed-p1-p2-v1-4/manifest.json)
contains 66 synthetic cases: the unchanged 60-case frozen v4 gold set plus six
versioned P2 constraint diagnostics, including four infeasible requests. It
contains 132 raw predictions. The manifest records dataset, supplement,
evaluation-code, and runtime-package checksums and explicitly records that the
worktree was dirty. Earlier observed diagnostic runs are preserved rather than
overwritten.

Raw predictions and aggregates are separate:

- [Raw predictions](../../evaluation_p2/results/20260821T-observed-p1-p2-v1-4/raw/predictions.jsonl)
- [Aggregate metrics](../../evaluation_p2/results/20260821T-observed-p1-p2-v1-4/aggregates/metrics.json)
- [P2 error categorization](../../evaluation_p2/results/20260821T-observed-p1-p2-v1-4/analysis/p2_errors.json)
- [P3 decision](../../evaluation_p2/results/20260821T-observed-p1-p2-v1-4/analysis/p3_decision.json)

| Metric | P1 | P2 |
| --- | ---: | ---: |
| Preferred Top-1 accuracy | 62.90% | 51.61% |
| Acceptable Hit@1 | 74.19% | 67.74% |
| Acceptable Hit@3 | 74.19% | 88.71% |
| Acceptable Hit@5 | 74.19% | 91.94% |
| MRR | 0.7419 | 0.7948 |
| nDCG@5 | 0.5485 | 0.8102 |
| Constraint violation rate (feasible cases) | 9.68% | 11.29% |
| Infeasible-request recall | 0.00% | 100.00% |
| Mean offline latency | 0.084 ms | 2.140 ms |
| Fallback rate | 0.00% | 6.06% |
| Final policy compliance | 100.00% | 100.00% |

P1 emits only one decision, so its evaluation ranked list contains one
candidate; consequently its Hit@3/Hit@5 cannot improve over Hit@1. Infeasible
detection is based on only four diagnostic cases and must not be generalized as
a production rate.

## P2 error categorization

The final observed run categorizes P2 cases as follows:

- extraction error: 0
- retrieval miss: 0
- constraint error: 0
- ranking error: 20
- unsupported catalog: 4
- infrastructure/provider failure: 0
- no error: 42

## P3 decision

P2 leaves meaningful reranking headroom under the frozen decision rule. In 62
cases, at least one acceptable candidate was both retrieved and feasible; in 20
of those cases (32.26%), deterministic P2 did not rank an acceptable candidate
first. This exceeds the predeclared threshold of at least
`max(3, ceil(5% of eligible cases))` cases and a headroom rate of at least 5%.

This is evidence of ranking headroom, not evidence that an LLM reranker will
improve results. P3 was not implemented.

## Remaining limitations

- P2 preferred Top-1 and constraint-violation results are currently worse than P1 despite stronger Hit@3/Hit@5, MRR, and nDCG@5.
- The default local feature-hash embedding is dependency-light and reproducible but is not a pretrained semantic language model; semantic generalization remains limited.
- The constraint supplement is small and synthetic.
- Dense-only and sparse-only ablations were not run as primary systems.
- No Kubernetes cluster was mutated, and this work package did not perform a live cluster effectiveness evaluation.
