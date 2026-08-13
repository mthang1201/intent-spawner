# Protocol-v4 Revised Evaluation and Claim Matrix

## Evidence included

- Immutable historical evidence: `results/v4-live-20260810`.
- Revised Stage A/B: `results/v4-revised-test-20260812T095453Z`, 960 rows
  (48 test samples × five repeats × four matrix conditions).
- Authoritative Stage C validation:
  `results/v4-stage-c-validation-v4.2-20260813T013600Z`, 32 observed rows.
- Combined analysis: `results/v4-final-combined-analysis-20260813T015500Z`.

The external matrix cells are explicit unavailable records, so comparative
quality inference covers three real methods, not four.

## Stage A/B recommendation quality

| Method | Profile acceptable | Image acceptable | Joint acceptable | Under | Over | Policy rejection |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Static medium baseline | 0.6250 | 0.1250 | 0.0417 | 0.3333 | 0.0417 | 0.0000 |
| Rule-based mapping | 0.7917 | 1.0000 | 0.6875 | 0.1667 | 0.0417 | 0.1042 |
| Local Ollama | 0.5833 | 0.6042 | 0.4375 | 0.3750 | 0.0417 | 0.0000 |
| External LLM | Unavailable | Unavailable | Unavailable | N/A | N/A | N/A |

The rule engine's 10.42% policy-rejection rate means its pre-policy
recommendation was outside the allowed resource/image policy and was changed by
the policy layer. Local Ollama had no policy changes or fallback changes in the
revised run, so its raw and applied accuracy are identical. External missing-
credential rows must not be read as zero accuracy.

## Corrected primary inference

The primary unit is the held-out sample (N=48), not the 240 repeated trial
rows. Confidence intervals resample 20 workload-family clusters. The five
repeats remain descriptive evidence for latency and consistency.

For profile acceptability:

| Comparison A−B | Risk difference, clustered 95% CI | Exact McNemar raw / Holm p | Conclusion |
| --- | --- | --- | --- |
| Rule − static | +0.1667 [−0.1064, +0.4152] | 0.0768 / 0.1536 | Not significant |
| Rule − Ollama | +0.2083 [−0.0652, +0.4510] | 0.0309 / 0.0927 | Not significant |
| Ollama − static | −0.0417 [−0.1277, 0.0000] | 0.5000 / 0.5000 | Not significant |

Thus the evidence does not establish a statistically significant profile-
selection improvement. Rule-based mapping has the largest observed profile
rate and a practically meaningful point difference, but uncertainty includes
no effect. Joint comparisons strongly favor rule-based mapping, largely because
image selection contributes heavily; they must not replace the profile result.

## Reliability and overhead

Local Ollama produced 240/240 schema-valid raw responses, no retry, fallback,
or error, and identical outputs across all five repeats for each sample. Its
median/p95 end-to-end latency was 9.204/14.736 seconds, versus 0.295/0.834 ms
for rules. Mean token usage was 584.85 prompt, 95.40 completion, and 680.25
total. Monetary cost is N/A because no versioned pricing was configured; local
compute energy and hardware cost were not measured. External overhead remains
unmeasured.

## Stage C validation

All 32 pods spawned. Workload success was 8/8 static-large, 5/8 rule-based,
5/8 real Ollama, and 2/8 static-small. There were 11 OOMs total and one
static-small bounded CPU timeout, with no Pending, image-pull, or cleanup
failures. This is one runtime repetition per cell and is descriptive validation,
not confirmatory evidence of stable efficiency differences.

## RQ1–RQ5 claim matrix

| RQ | Status | Evidence-safe conclusion |
| --- | --- | --- |
| RQ1: approach quality differences | **PARTIALLY CLAIMABLE** | Static, rule, and local Ollama can be compared; external quality is unavailable. Rule has the highest observed profile rate. |
| RQ2: whether LLMs improve quality | **PARTIALLY CLAIMABLE** | Local Ollama did not improve profile accuracy over static or rules; no external LLM result supports a general LLM conclusion. |
| RQ3: LLM overhead | **PARTIALLY CLAIMABLE** | Local latency, reliability, tokens, fallback, and Stage C outcomes are measured; external latency/cost and local energy/hardware cost are not. |
| RQ4: applied Kubernetes effects | **CLAIMABLE** | The observed 4×8×10 confirmatory matrix is complete. Static-large was fully successful but over-allocated; adaptive methods reduced requests but each failed three families. Family-level success/OOM tests remain underpowered at eight families. |
| RQ5: external vs local trade-off | **NOT CLAIMABLE** | The external backend made no provider attempts, so no empirical head-to-head comparison exists. |

## Thesis-safe claims

1. Protocol-v4 demonstrates an auditable, policy-aware recommendation and
   JupyterHub application pipeline with explicit raw, fallback, and applied
   semantics.
2. On this synthetic held-out set, rule-based mapping had higher observed
   profile acceptability than static medium and local Ollama, but the corrected
   clustered sample-level intervals and Holm-adjusted tests do not establish a
   significant profile advantage.
3. The repaired local Ollama interface was operationally reliable at
   temperature zero, but added roughly nine seconds median recommendation
   latency and did not improve profile accuracy.
4. A single-repeat live cluster validation showed the consequences of profile
   choice (OOM, timeout, success) and verified end-to-end integration; it does
   not estimate production performance or repeat-to-repeat variance.
5. No external-LLM quality, latency, cost, or privacy comparison is supported.
