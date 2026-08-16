# Protocol-v4 Stage C Confirmatory Report

## Evidence identity and execution

The authoritative run is
`results/v4-stage-c-confirmatory-20260813T021600Z`. It executed the frozen
`results/v4-stage-c-confirmatory-plan-20260813T021239Z/system-plan.jsonl`
(SHA-256 `718adf39c82023755db3dd60a8d1b4730eaef4fb92ff909f52b2180e957bbf10`)
against the disposable, labelled `orbstack` namespace. The run recorded the
clean source commit `99707b8da8e4c065a1a451332f8555193614144a` and completed
without interruption or resume.

The matrix contains exactly 320 observed trials: four methods, eight workload
families, and ten runtime repeats. All 320 trial IDs are unique and match the
randomized plan in order. Every family/repeat block used one deterministic
workload seed across all four methods. Runtime repeats measure execution
variability; they are not treated as independent recommendation-quality
samples.

The `system-trials.jsonl` SHA-256 is
`a76a334f74cd0dc928ce158f87106bc6f8576a17ec518ed0ef756cbbd61ff256`.
All 2,244 files listed in the run's `SHA256SUMS` verify. All 320 records have
six supporting sidecars plus trial metadata and cleanup status `completed`.
No retry directory or resume event exists.

## Observed outcomes

| Method | Trials | Success | OOMKilled | Timeout | Pending | Image pull | Fallback | Mean CPU request | Mean memory request |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `static_small` | 80 | 29 (36.25%) | 50 (62.5%) | 1 (1.25%) | 0 | 0 | 0 | 100m | 256 MiB |
| `static_large` | 80 | 80 (100%) | 0 | 0 | 0 | 0 | 0 | 1,500m | 1,536 MiB |
| `rule_based_context` | 80 | 50 (62.5%) | 30 (37.5%) | 0 | 0 | 0 | 0 | 562.5m | 800 MiB |
| `self_hosted_local_ollama_llm` | 80 | 50 (62.5%) | 30 (37.5%) | 0 | 0 | 0 | 0 | 500m | 768 MiB |

All 320 spawns became Ready. Successful workloads supplied 209 cgroup-v2
measurement windows; OOM and timeout rows retain null usage rather than an
imputed measurement. Static-small's CPU workload timed out once and succeeded
in the other nine repeats. The 110 OOMs were stable family/resource outcomes.

Rule-based and Ollama had equal aggregate success and OOM counts but did not
fail on exactly the same families. Ollama succeeded on `code-only-pandas`,
where the lower-memory rule allocation failed; the rule method succeeded on
`large-aggregation`, where Ollama's medium profile failed. Each method also
failed on `code-only-training` and `hidden-large-demand`.

## Efficiency and over-provisioning

Relative to static-large, rule-based reduced average CPU requests by 937.5m
(62.5%) and memory requests by 736 MiB (47.9%). Ollama reduced them by 1,000m
(66.7%) and 768 MiB (50%). These savings came with a 37.5 percentage-point
lower workload-success rate and a 37.5 percentage-point higher OOM rate.

On shared successful families only, static-large had lower request utilization:

| Comparison (`static-large` minus adaptive) | CPU/request | Memory/request | Peak memory/request |
| --- | ---: | ---: | ---: |
| versus rule-based | -0.404 [family-bootstrap 95% CI -1.057, -0.027] | -0.221 [-0.388, -0.054] | -0.232 [-0.409, -0.056] |
| versus Ollama | -0.199 [-0.445, -0.063] | -0.328 [-0.470, -0.187] | -0.342 [-0.494, -0.191] |

These utilization comparisons are explicitly survivor-conditioned: five
families had successful measurements for both methods. They demonstrate that
static-large uses its requests less intensively on shared successes, but they
must not be read as full-matrix efficiency estimates.

## Family-aware statistical conclusions

Primary Stage C inference aggregates the ten runtime repeats within each of
the eight workload families. Family-clustered bootstrap intervals quantify
effect uncertainty; exact paired Wilcoxon tests use the eight family means and
Holm correction is applied across method pairs within each endpoint. The
80-pair McNemar table is retained only as descriptive runtime-trial telemetry.

- Static-large minus rule-based success was +0.375 (95% family-bootstrap CI
  +0.125 to +0.750); OOM was -0.375 (-0.750 to -0.125). Static-large versus
  Ollama had the same aggregate effects. Only three families were discordant,
  so the exact family-level raw p-value was 0.25 and Holm p-value was 1.0.
- Rule-based and Ollama had no aggregate success/OOM difference; their
  family-level CI was -0.375 to +0.375 and p=1.0.
- Rule-based minus static-small success was +0.2625 (the table stores
  `static_small - rule_based = -0.2625`; CI -0.625 to -0.0125). Its exact
  family-level raw p-value was 0.25 and Holm p-value was 1.0. Ollama had the
  same aggregate effect.
- Static-large requested 937.5m more CPU than rule-based (CI +612.5 to
  +1,212.5m; Holm p=0.046875) and 736 MiB more memory (CI +400 to +1,040 MiB;
  Holm p=0.0625). Against Ollama, static-large requested exactly 1,000m more
  CPU and 768 MiB more memory across families; both Holm p=0.046875.
- Utilization effect intervals favored adaptive methods over static-large on
  shared successes, but exact family-level tests did not survive multiplicity
  correction (Holm p at least 0.375).

Thus the experiment supports a strong operational trade-off: static-large is
the robust but over-allocating baseline, while rule-based and Ollama halve
memory requests and materially reduce CPU requests but fail three of eight
families. Because effective independent N is eight and only three families
drive the success contrast, the conservative family-level hypothesis tests do
not establish a statistically significant success/OOM advantage. H4 is
directionally and operationally supported, but not confirmed as a family-level
significance claim.

## Claim status and boundaries

The revised applied-system **RQ4 is CLAIMABLE** because the preregistered
four-method, eight-family, ten-repeat observed matrix is complete. “Claimable”
means the question can now be answered from observed evidence; it does not mean
every pairwise hypothesis is significant. Thesis-safe conclusions are limited
to this single-node disposable environment, the eight frozen executable
families, warm images, and the frozen model/policy/configuration.

Stage C preregistered at most one development-qualified LLM backend and used
the qualified local Ollama method, so the later external matrix does not alter
these cluster outcomes. The external `gemini-3.5-flash` matrix subsequently
completed and now supports an operational external-versus-local comparison
with limitations; see `PROTOCOL_V4_EXTERNAL_LLM_LIVE_REPORT.md` and
`PROTOCOL_V4_REVISED_EVALUATION_REPORT.md`. Its 21/240 raw-response coverage is
insufficient for a broad intrinsic model-quality ranking.

The authoritative family-aware analysis is
`results/v4-stage-c-confirmatory-analysis-v3-20260813T054000Z`.
