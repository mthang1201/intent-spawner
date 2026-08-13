# Protocol-v4 Combined Stage A/B/C Evaluation and Claim Matrix

## Authoritative evidence

- Historical Stage A/B source, retained unchanged:
  `results/v4-revised-test-20260812T095453Z`.
- New external source:
  `results/v4-external-confirmatory-20260813T045543Z`, 240/240 live
  `gemini-3.5-flash` trials.
- Derived four-method view:
  `results/v4-combined-evidence-20260813T050500Z`, 960 rows. It copies the 720
  historical static/rule/Ollama rows and replaces only the 240 historical
  `missing_credentials` cells with the new external rows. The historical source
  is not modified.
- Completed Stage C:
  `results/v4-stage-c-confirmatory-20260813T021600Z`, 320 observed trials.
- Corrected combined analysis:
  `results/v4-final-combined-external-analysis-v2-20260813T050836Z`.

The derived prediction SHA-256 is
`751a8ab32d323647770d04391c838c16233f7b987e586d37841591860230b055`;
the Stage C SHA-256 remains
`a76a334f74cd0dc928ce158f87106bc6f8576a17ec518ed0ef756cbbd61ff256`.

## Recommendation quality

Rates below use all 240 trial rows per method. For deterministic methods the
five repeats are identical; for LLMs they quantify stability and availability.
Primary inference aggregates to 48 held-out samples and resamples the 20
workload-family clusters.

| Method | Applied profile acceptable | Applied image acceptable | Applied joint | Under | Over | Fallback |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Static medium | 62.50% | 12.50% | 4.17% | 33.33% | 4.17% | 0% |
| Rule-based | 79.17% | 100% | 68.75% | 16.67% | 4.17% | 0% |
| Local Ollama | 58.33% | 60.42% | 43.75% | 37.50% | 4.17% | 0% |
| External Gemini pipeline | 77.92% | 100% | 67.92% | 18.75% | 3.33% | 91.25% |

The external applied row is fallback-assisted: 219/240 decisions came from the
rule engine. Its standalone raw rates were 5.00% profile acceptable, 8.75%
image acceptable, 5.00% joint acceptable, and 8.75% valid-response coverage on
the full denominator. Conditional on the 21 responses, profile/joint accuracy
was 57.14% and image accuracy was 100%. Local Ollama returned 240/240 valid raw
responses with raw rates of 58.33%, 60.42%, and 43.75% respectively.

## Corrected statistical conclusions

The recommendation-quality inferential unit is the held-out sample (`N=48`),
not 240 repeated trial rows. Confidence intervals use 2,000 family-clustered
bootstrap replicates over 20 workload families. Exact paired tests use one
aggregated value per sample and Holm correction within the relevant hypothesis
families. Repetitions remain stability/latency evidence.

No pairwise applied-profile difference was statistically supported after Holm
correction:

| Applied profile comparison A−B | Risk difference, family-clustered 95% CI | Holm McNemar p | Conclusion |
| --- | --- | ---: | --- |
| External − rule | −1.25 pp [−4.90, +2.33] | 1.0 | no supported difference |
| External − Ollama | +19.58 pp [−5.45, +42.56] | 0.2471 | no supported difference |
| External − static | +15.42 pp [−7.91, +37.87] | 0.4609 | no supported difference |
| Rule − Ollama | +20.83 pp [−6.82, +44.90] | 0.2471 | no supported difference |
| Rule − static | +16.67 pp [−9.30, +40.74] | 0.4609 | no supported difference |
| Ollama − static | −4.17 pp [−12.77, 0.00] | 1.0 | no supported difference |

Applied joint accuracy was higher for the external pipeline than Ollama by
24.17 pp [2.98, 44.09], Holm McNemar `p=0.0418`. This is a statistically
supported pipeline/fallback difference, not evidence that Gemini itself was
more accurate: rule-based minus Ollama was similarly +25.00 pp [1.95, 47.06],
`p=0.0418`.

The fallback-isolated raw LLM comparison strongly favored local Ollama:

| Raw endpoint, external − Ollama | Risk difference, family-clustered 95% CI | Holm McNemar p |
| --- | --- | ---: |
| Valid response | −91.25 pp [−94.29, −87.92] | 2.84e−14 |
| Profile acceptable | −53.33 pp [−73.02, −33.47] | 1.49e−8 |
| Image acceptable | −51.67 pp [−71.38, −32.55] | 1.12e−8 |
| Joint acceptable | −38.75 pp [−59.56, −19.57] | 9.54e−7 |

These differences are statistically supported operational outcomes for the
configured services. They are dominated by external transport availability and
must not be generalized as an intrinsic Gemini-versus-Llama capability ranking.

## Latency, reliability, tokens, and cost

| Method | Median / p95 end-to-end | Valid raw responses | Retry | Fallback | Mean tokens per token-bearing response |
| --- | --- | ---: | ---: | ---: | ---: |
| Rule-based | 0.000295 / 0.000834 s | N/A | N/A | 0% | N/A |
| Local Ollama | 9.2037 / 14.7365 s | 100% | 0% | 0% | 680.25 |
| External, all trials | 1.2961 / 3.9594 s | 8.75% | 93.33% | 91.25% | 1,399.62 |

The external all-trial latency is shortened by fast transport failures.
Successful external completions had 4.0095-second median end-to-end and
3.9582-second median provider latency. Therefore the aggregate result supports
“external failed faster than local completed,” not an unqualified external
latency advantage. The sample-level latency tests find the observed all-trial
differences significant after Holm correction, but the estimands differ in
success coverage.

External token totals were 12,468 prompt, 3,262 completion, and 29,392 total
across 21 completions. Ollama means were 584.85 prompt, 95.40 completion, and
680.25 total across all 240 responses. Monetary cost is unavailable because no
reproducible pricing snapshot was configured. Provider energy/resources, local
energy, and local hardware cost were not measured.

## Privacy and operational boundary

Implementation evidence shows the external request sends intent text, dataset
size, code context, catalog metadata, and the response schema over HTTPS to the
configured Google endpoint using a Secret-backed bearer credential. The local
condition sends the same recommendation context to a loopback Ollama service.
Thus local deployment keeps inference traffic within the evaluated host trust
boundary, while external deployment crosses it. No raw notebooks, datasets,
usernames, or secrets were included in this synthetic evaluation, and no user
privacy outcome or provider retention behavior was measured.

Operationally, external deployment needs network/provider availability, API
credentials, quota management, and retry/fallback handling. Local Ollama needs
model hosting, local compute capacity, model lifecycle management, and roughly
nine seconds median inference in the evaluated configuration.

## Completed Stage C

The 320-trial Stage C conclusions are unchanged. Static-large succeeded in
80/80 trials; rule-based and Ollama each succeeded in 50/80; static-small
succeeded in 29/80. There were 110 OOMs, one bounded static-small timeout, and
no Pending, image-pull, spawn-readiness, or cleanup failures.

Relative to static-large, rule-based reduced average CPU requests by 62.5% and
memory requests by 47.9%; Ollama reduced them by 66.7% and 50%, respectively.
These operational savings came with 37.5 percentage-point lower success and
higher OOM rates for each adaptive method. Family-level success/OOM tests did
not survive Holm correction because the effective sample is eight families;
static-large CPU request differences did survive correction against both
adaptive methods.

## RQ1–RQ5 claim matrix

| RQ | Status | Evidence-safe conclusion |
| --- | --- | --- |
| RQ1: four-approach recommendation quality | **CLAIMABLE** | All four 48×5 matrices are complete. Applied operational quality is comparable; raw external quality must remain separate because response coverage was 8.75%. No applied profile difference is statistically supported. |
| RQ2: whether LLMs improve recommendation quality | **CLAIMABLE** | The evidence does not support a general LLM profile-quality improvement. Local Ollama trailed rules descriptively; external applied accuracy was fallback-driven, and raw external outcomes were significantly worse because of availability. |
| RQ3: LLM overhead | **PARTIALLY CLAIMABLE** | Latency, reliability, retries, fallbacks, and tokens are measured for both LLM paths, with Stage C resource outcomes for local Ollama. Monetary cost, energy, external provider resources, and local hardware cost are unavailable. |
| RQ4: applied Kubernetes effects | **CLAIMABLE** | The 4×8×10 Stage C matrix is complete. It establishes the observed robustness-versus-request trade-off, while family-level success/OOM significance remains underpowered. |
| RQ5: external-vs-local trade-off | **CLAIMABLE WITH LIMITATIONS** | A complete operational head-to-head supports quality, latency, reliability, token, privacy-boundary, and deployment-overhead comparisons. Cost/energy is unavailable, and low external response coverage prevents a broad intrinsic model-quality comparison. |

## Thesis-safe claims

1. Rule-based mapping had the highest observed profile acceptability (79.17%),
   closely followed by the fallback-assisted external pipeline (77.92%), but no
   applied profile difference was statistically supported after family-aware
   intervals and Holm correction.
2. Local Ollama completed all 240 calls without retry or fallback but had
   9.20-second median latency and did not improve profile accuracy over the
   deterministic baselines.
3. The configured external service returned only 21/240 valid completions.
   Rule fallback preserved 67.92% applied joint accuracy, but raw external joint
   accuracy was 5.00%; fallback output is never credited to Gemini.
4. External successful calls were faster than local inference descriptively,
   but aggregate external latency is biased downward by 219 fast failures.
5. Stage C supports a robust-static versus efficient-adaptive operational
   trade-off in the evaluated single-node environment, not a production-wide
   superiority claim.
6. Local inference avoids crossing the evaluated host boundary; external
   inference requires sending sanitized recommendation context to a provider.
   No user privacy outcome, monetary cost, or energy consumption was measured.
