# Threats To Validity

## Protocol-v4 Revised Evaluation (2026-08-13)

The Protocol-v4 repair was prompted by a schema omission observed in the
original held-out run. The repair changed only interface compliance—an explicit
five-field prompt and native Ollama JSON Schema enforcement—and was first
validated on development samples. Nevertheless, the subsequent held-out run is
a revised confirmatory protocol rather than the original frozen experiment.

The revised recommendation dataset is synthetic, multilingual, and limited to
48 held-out samples in 20 workload families. The local model result covers only
`llama3:latest`, temperature zero, one Apple Silicon host, and one prompt
contract. Five identical outputs per sample establish repeat consistency under
that configuration but do not add independent accuracy observations.

The external matrix now covers `gemini-3.5-flash` after an explicit pre-held-out
model-only amendment: Google retired the originally frozen
`gemini-2.0-flash` before any external held-out trial. The amended development
gate passed credentials, endpoint/model identity, schema, policy, latency, and
token checks. In the held-out run, however, only 21/240 trials returned a raw
completion; 219 exhausted retries and used the rule fallback. Successful calls
were concentrated in the first two repeat blocks (19 then two), with none in
blocks 2–4. Execution order is therefore confounded with provider availability.
The 8.75% response rate, raw full-denominator metrics, and fallback-assisted
applied metrics are valid operational results for the evaluated account and
time window, but the 21-response subset is insufficient for a broad intrinsic
Gemini-quality conclusion.

All final external failures were sanitized as `transport_error`. Sanitization
protects provider-controlled text and credentials, but it also prevents the
retained evidence from distinguishing HTTP quota/rate limits, 5xx responses,
DNS failures, or other transport causes. The pattern is consistent with quota
exhaustion but does not prove it. External all-trial latency is biased downward
by fast failures; successful-completion latency must be reported separately.
Token telemetry exists only for successful calls. Monetary cost is unavailable
because no reproducible pricing snapshot was configured. Provider energy and
resource use, local energy, local hardware cost, provider retention behavior,
and user privacy outcomes were not measured.

The 2026-08-13 one-repeat Stage C validation has been superseded for applied
system inference by the 320-trial confirmatory corpus. The confirmatory corpus
still uses a single node and only eight executable workload families, with ten
runtime repeats per four-method-by-eight-family cell. Repeats estimate runtime
variability but do not create independent recommendation-quality observations.
The effective family-level sample is eight, and only three families distinguish
static-large success from each adaptive method, limiting exact-test power. The
corpus provides genuine OOM, timeout, spawn, and cgroup evidence but no basis
for production generalization. Metrics are available only for workloads that
survived long enough to emit the cgroup payload, creating survivorship
conditioning in utilization summaries. Metrics Server was absent; successful
rows use in-container cgroup-v2 window measurements. The run recorded a clean
Git worktree and frozen commit, plan, values, model, prompt, policy, catalog,
image digests, and cluster state.

The Stage C method identifiers (`static_small`, `static_large`,
`rule_based_context`, and `self_hosted_local_ollama_llm`) test operational
envelopes and are not identical to the
Stage A/B canonical static-medium and rule-based quality comparison. Cross-stage
conclusions must respect that distinction. User acceptance and reprovisioning
were not observed.

## Protocol-v5 development-evidence signal

This section is scoped to Protocol-v5; the surrounding sections of this document
describe Protocol-v4 and earlier artifacts. It is an **additive interpretive
reading** of evidence that already exists in the repository. It introduces no
new experiment, promotes no package, and changes no status. Every
machine-readable decision, disposition, and reason code in
[PROTOCOL_V5_FINAL_REPORT.md](PROTOCOL_V5_FINAL_REPORT.md) and in the
authenticated evaluated-claim registry remains authoritative and is unaffected
by anything written here. Where this section and a recorded status appear to
speak to the same question, the recorded status governs the *claim* and this
section governs only the *narrative interpretation of observed numbers*.

### What NOT_EXECUTED does and does not mean

H1, H2, H5 and H6 are recorded as `NOT_EXECUTED`. That decision means
**confirmatory evidence is absent**: no checksum- and provenance-valid package
bound to the authoritative global execution freeze was available and eligible to
be evaluated against the frozen predicate. It does not mean that no data were
collected, that the systems were never run, or that the observations that do
exist are uninformative. The final report already states that a `NOT_EXECUTED`
decision "is neither a zero effect nor evidence against the hypothesis" — that
is a correct statement about the *claim status*, and it is important not to
silently extend it into the false statement that no measured signal exists.

Real signal does exist, in two places, and it is recorded below with its exact
sample sizes and limits. Both sources are development or non-global evidence and
neither can decide a hypothesis. Both are nevertheless real observations of real
executions of the frozen P1 and P2 implementations.

### E1 — offline top-one recommendation quality (development split)

Source: `results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1/raw/recommendations.jsonl`;
SHA-256 `25868752054231fa271e540210aad0845113ba3974722376029b18184959daf8`.
The package records `status: RAW_EVIDENCE_COMPLETE`, 36 records, 0 error
records, and `claims_permitted: false`.

The package's own scoring and statistical stages were **not** run (its offline
report carries `NOT_EXECUTED` / `INPUTS_NOT_SUPPLIED`, pending compiled v2 gold
labels or a sealed confirmatory dataset). The counts below were therefore
computed directly from the raw file, by testing whether each record's
`predicted_candidate_id` is a member of that record's inline
`evaluation_gold.acceptable_candidate_ids`. A candidate identifier encodes both
profile and image (for example `small-minimal-python`), so this is a joint
profile-and-image acceptance at rank one. It is **not** the frozen
`JointAccept@1` estimator that H1 is defined over, and it must not be reported
as such.

All 36 records have `status: completed`: 18 for P1 and 18 for P2, covering 18
cases across 10 workload families.

| Case stratum | Cases per system | P1 acceptable@1 | P2 acceptable@1 |
| --- | --- | --- | --- |
| All cases | 18 | 13/18 | 8/18 |
| Ordinary (non-diagnostic) | 12 | 12/12 | 6/12 |
| P2-diagnostic | 6 | 1/6 | 2/6 |

The 18 cases are not one homogeneous population. Twelve are "ordinary" cases:
four families (`basic-python`, `pandas-transform`, `sklearn-small`,
`threshold-below`) each presented in three surface variants — canonical English,
English paraphrase, and Vietnamese. The remaining six are single-case families
designed to exercise P2 constraint handling (`p2-cpu-only-pandas-feasible`,
`p2-minimum-eight-cpu`, `p2-minimum-eight-memory`, `p2-required-gpu-unavailable`,
`p2-required-rapids-unsupported`, `p2-tensorflow-inference-feasible`). Pooling
the two strata into a single 18-case rate mixes populations built for different
purposes; the stratified rows above are the more honest presentation, and they
are the reason the ordinary split is reported separately rather than only the
pooled 13/18 versus 8/18.

On surface-form stability, the four ordinary multi-variant families give the
following top-one behaviour across the three variants of the same underlying
request:

- **P1: 0 of 4 families unstable.** All four families return an identical
  top-one candidate for all three variants, and all are acceptable.
- **P2: 3 of 4 families unstable.** `basic-python` moves from
  `small-minimal-python` (canonical) to `large-minimal-python` (paraphrase and
  Vietnamese). `sklearn-small` moves from `small-scipy-data-science` to
  `large-scipy-data-science` on the Vietnamese variant. `threshold-below`
  returns three different answers for three variants — `large-minimal-python`,
  `small-minimal-python`, and `large-pytorch-deep-learning` — the last of which
  changes the image family, not merely the size tier. Only `pandas-transform` is
  stable and correct across all three variants.

This is a directly relevant observation for RQ2, whose premise is that P2 is
*more* robust than P1 to semantically equivalent surface forms. In the only
real, executed P1-versus-P2 variant data currently in the repository, the
observed direction is the opposite.

**E1 limits.** Development split, explicitly not confirmatory. The workload
family — not the case and not the variant — is the semantic unit under the
frozen protocol, so the effective sample is 10 families overall and 4 families
for the ordinary/robustness reading. That is very small. There is a single
execution per case with no repetition, so within-case run-to-run variability is
unmeasured. Gold labels are the inline per-record labels, not a sealed
confirmatory label set. No significance test is reported here because none is
recorded in this package and the family counts would not support one.

### E4 — resource efficiency under real Kubernetes (OrbStack lineage)

Source: `results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-orbstack-analysis-repaired-final-v1/statistics/results.json`;
SHA-256 `e9ec8f9ca1233e4445bdd969967930489cba4ba063074da518b29423cccf2740`.
Condition levels quoted below come from `derived/condition-summaries.json` in
the same package; SHA-256
`e5688a374631f9ca472eee090ef20e966ddf820141cb269308de824c3158bde7`.

This package contains 640 real trials — 16 workload families × 4 conditions × 10
repetitions, 160 trials per condition. Trial records carry
`collector_origin: REAL_KUBERNETES_COLLECTOR` with in-container cgroup-v2
measurements. The statistics file records `family_is_primary_unit: true`,
`repetitions_are_independent_families: false`, and
`independent_semantic_n: 16`, so inference uses the family as the unit and the
repetitions are not treated as independent samples. Tests are Wilcoxon
signed-rank, two-sided, with Holm correction applied within each endpoint;
confidence intervals are bootstrap intervals over 2000 replicates.

P2_CATALOG versus P1_CATALOG, reported as candidate minus reference:

| Endpoint | Role | Pairs | Median paired difference | Holm p (within endpoint) | Inference status |
| --- | --- | --- | --- | --- | --- |
| `cpu_cost_per_success` | primary | 16 | +0.0758 | 0.001930 | ELIGIBLE |
| `memory_cost_per_success` | primary | 16 | +59.816 MiB·s | 0.001930 | ELIGIBLE |
| `success_rate` | primary | 16 | 0.0 | 1.0 | ELIGIBLE |
| `oom_rate` | primary | 16 | 0.0 | 1.0 | ELIGIBLE |
| `cpu_request_ratio` | secondary | 16 | −4.428 | 0.001930 | ELIGIBLE |
| `runtime_seconds` | secondary | 16 | −0.0581 s | 0.003409 | ELIGIBLE |
| `cpu_request_error_signed` | secondary | 9 | +1400 m | 0.023438 | **WITHHELD_SMALL_N** |

At the condition level, `cpu_cost_per_success` is 0.02661 for P1_CATALOG and
0.15004 for P2_CATALOG — P2 costs **5.64×** as much CPU per successful workload.
`memory_cost_per_success` is 60.50 versus 158.19 MiB·s, or **2.61×**. Both cost
endpoints are primary, both have all 16 of 16 pairs moving in the same
direction, matched-pairs rank-biserial of 1.0, and Cohen's dz of 1.03. The
recorded 95% bootstrap intervals are [0.0717, 0.1839] for CPU cost and
[57.70, 143.94] for memory cost; both exclude zero.

Three caveats on this table are load-bearing:

1. **The CPU-request-error row is withheld by the package itself.** The
   `cpu_request_error_signed` endpoint has only 9 family pairs (8 non-zero), and
   the file records `inference_status: WITHHELD_SMALL_N` alongside its Holm
   p-value of 0.023438. The +1400 m figure is the median *paired difference* in
   signed oracle error between P2 and P1 on those 9 families — that is, P2 sits
   1400 m further toward over-request than P1 — and not P2's absolute
   over-request, which is +372.2 m at the condition level against P1's −827.8 m
   under-request. It should be read as a descriptive median on 9 families, not
   as a supported inferential result. The same withholding applies to the
   memory-request, CPU-limit and memory-limit signed-error endpoints.
2. **The reliability endpoints are at ceiling and therefore uninformative.**
   All four conditions — including STATIC_LARGE — record `success_rate` 1.0,
   `oom_rate` 0.0, `timeout_rate` 0.0 and `correctness_rate` 1.0, with 160
   successful tasks each. The zero differences and p-values of 1.0 on those
   endpoints mean the environment never applied enough resource pressure to
   discriminate between conditions. So the correct statement is "P2 spent 5.64×
   the CPU cost without any measured reliability benefit **in an environment
   where no condition ever failed**" — not "P2 spends more and is less
   reliable." A pressured environment could in principle reward P2's larger
   requests; this run cannot say.
3. **Every row carries `SMALL_EFFECTIVE_FAMILY_N`.** Sixteen families is the
   whole inferential sample.

Further limits: this is a single-node local OrbStack cluster, not a production
or multi-node JupyterHub deployment. Measured runtimes are very short — median
`runtime_seconds` is 0.207 s for P1_CATALOG and 0.116 s for P2_CATALOG — and the
cost endpoints are defined as request × accounting runtime, so the request side
dominates the comparison and the results should not be read as steady-state
efficiency under realistic notebook sessions. The package records
`analysis_status: REPAIRED_REPRODUCIBLE` with `EMPIRICAL_EXECUTION_NOT_RERUN:
true` and `RAW_EVIDENCE_REUSED: true`: the analysis was re-derived from preserved
raw evidence rather than re-executed, and it is bound to a third freeze
(`v5-e4-orbstack-analysis-repair-freeze-v1`). Its capacity sidecar is
`SIMULATED_CAPACITY` and is not used for any statement above.

### The INCOMPATIBLE_FREEZE boundary is a harness boundary, not a systems boundary

The E4 packages above are excluded from the final report as
`INCOMPATIBLE_FREEZE` because they are bound to the separate OrbStack execution
freeze rather than the authoritative global one. That exclusion is procedurally
correct and this section does not contest it. It is worth recording precisely
*what* differs between the two freezes, because the exclusion reason is easy to
misread as "the OrbStack runs measured a different recommender."

Comparing
`results_v5/protocol-v5.0.0/freezes/v5-final-execution-freeze/freeze-manifest.json`
(SHA-256 `6228673d9459ab2f447c342adb17e0f3f60867c55a74ff98dac2291a01811b8b`,
frozen execution SHA `0f73c0a2da34916df1ef6134cc898ce01512acff`) against
`results_v5/protocol-v5.0.0/freezes/v5-e4-orbstack-confirmatory-freeze-v1/freeze-manifest.json`
(SHA-256 `6d47b403e90df618165951c0d301854e4bfe8c22e9a2ab869e288056936ef32f`,
frozen execution SHA `3d9a777390ae50cac266ceee0a58c718a34d6725`):

- **12 of the 13 `configuration_snapshot` blocks are byte-identical**, including
  `systems`, `candidate_catalog`, `configuration`, `prompts`, `indexes`,
  `structured_intent`, `dynamic_resource_policy`, `development_dataset`,
  `environment`, `environment_requirements`, `runtime_package` and `p3_gate`.
  The shared `systems` block pins P1 to `recommender/rule_based.py` at
  `063d7024…`, P2 to `recommender/p2_backend.py` at `3ca68ea7…`, and P3 to
  `recommender/p3_backend.py` at `e0d5b216…`. The shared `configuration` block
  pins the P2 retrieval configuration (`p2-config-v1.0.0`, RRF k=60, dense and
  sparse top-k 10, equal weights).
- **Only `experiment_contracts` differs, and only its E4 entry.** Within that
  entry, 12 files are identical by path and digest; 4 files changed content at
  the same path (`evaluation_v5/resource/contracts.py`,
  `efficiency_runner.py`, `preflight.py`, `runner.py`); and 10 files exist only
  in the OrbStack freeze — the OrbStack cluster adapters
  (`cluster_evaluation/resource_adapter_v5.py`,
  `resource_efficiency_adapter_v5.py`, `orbstack_readiness_v5.py`), an OrbStack
  image-state YAML, a v2 cluster-eligibility YAML, and five E4 oracle-readiness
  attestation schemas.
- As an independent cross-check outside the manifests,
  `git diff 0f73c0a2 3d9a7773 -- recommender/` is **empty**: the recommender
  tree is byte-identical between the two frozen execution commits. The six
  differing `benchmarks_v5/` files between the two commits are the five
  readiness-attestation schemas and the cluster-eligibility v2 YAML.

So the recommender and configuration identity — the thing being compared — does
not differ between the two freezes. What differs is E4 execution-harness and
cluster-readiness plumbing. The `INCOMPATIBLE_FREEZE` disposition correctly
prevents these packages from certifying a global claim; it does not imply that
the P1 and P2 under test were different code, and it should not be cited as if
it did.

### Where the current evidence favours P2

Reporting the direction honestly requires recording the points that run the
other way, all of which are small:

- On the six P2-diagnostic cases in E1, P2 is acceptable on 2 of 6 against P1's
  1 of 6. These cases were built to exercise P2 constraint handling, and both
  systems fail most of them.
- In E4, P2_CATALOG workloads finish faster than P1_CATALOG — median paired
  difference −0.0581 s, Holm p 0.003409 — and P2's `cpu_request_ratio` of 1.138
  sits closer to 1.0 than P1's 5.281, indicating P1 systematically
  under-requests CPU relative to observed use. P1's recorded
  `cpu_request_error_signed` of −827.8 m against oracle is an under-request,
  where P2's +372.2 m is an over-request.
- In the E5 functional development run already reported in the final report, P2
  records conservative functional success of 18/18 against P1's 17/18, on equal
  image-label matches of 13/18 each.

None of these reverses the overall picture. P2's speed advantage is measured in
hundredths of a second on sub-second synthetic workloads, and it is purchased
with 5.64× the CPU cost per success. P1's under-request never produced a single
OOM or failure in 160 trials, so in this environment its smaller requests were
not merely cheaper but sufficient.

### Plain reading of the current evidence

All real evidence currently available in this repository points against P2
outperforming P1.

On top-one recommendation quality, P1 is ahead on both the pooled development
split (13/18 against 8/18) and the ordinary non-diagnostic split (12/12 against
6/12). On robustness to equivalent surface forms — the property P2 is
specifically proposed to provide — P1 is stable on 4 of 4 multi-variant families
and P2 on 1 of 4. On resource efficiency under 640 real Kubernetes trials, P2
costs 5.64× more CPU and 2.61× more memory per successful workload, with all 16
of 16 families moving the same way and Holm-corrected p of 0.00193 on both
primary cost endpoints, while delivering no measurable reliability benefit in an
environment that produced no failures under any condition.

This is a statement about direction, not a certified result, and it rests on
small samples: 4 ordinary families for the robustness reading, 10 families for
E1 overall, and 16 families for E4. Two of these three readings would need to
reverse, not merely soften, for the thesis proposition to hold. Nothing here
constitutes a confirmatory refutation of H1, H2, H5 or H6, and none of it can
be promoted to one without confirmatory execution against the global freeze.
What it does establish is that the absence of confirmatory evidence is not
neutral ground: the observations that exist lean consistently one way, and a
confirmatory campaign should be designed and powered on the expectation that it
may well confirm P1's advantage rather than P2's.

### Status authority

Nothing in this section alters any recorded status. H1, H2, H5 and H6 remain
`NOT_EXECUTED`. E4_ORBSTACK_EFFICIENCY, E4_ORBSTACK_ORACLE and
E4_PROVENANCE_AUDIT remain `INCOMPATIBLE_FREEZE`. The E1 development package
remains `OBSERVED` at development stage with `claims_permitted: false`. The
overall audit verdict remains FAIL with confirmatory evidence
`EXECUTED_INCOMPLETE`. No number in this section was generated by re-running a
recommender, a container probe, or a Kubernetes job; each is either read
directly from a cited artifact or computed from cited raw records by the
arithmetic described above. No confidence interval, p-value, or effect size
appears here that is not recorded in the cited files.

## Construct Validity

The benchmark operationalizes "good profile selection" as matching expected
acceptable profiles, avoiding under/over profile deltas, preserving policy
warnings, and comparing requested resources with local peak memory proxies.
These constructs approximate the thesis question, but they do not fully capture
interactive user satisfaction, notebook startup perception, productivity, or
administrator policy goals.

## Internal Validity

The rule-based recommender, workload manifest, synthetic runner, and analysis
code live in the same repository. Implementation bugs in any layer can bias the
results. The artifact reduces this risk with unit tests, schema validation,
immutable JSONL records, repeated trials, deterministic seeds, and explicit
method isolation between `static_manual`, `intent_only`, and `context_aware`.

## External Validity

The preserved evidence includes a local process matrix and a separate
single-node Minikube corpus. Neither should be generalized directly to
production JupyterHub deployments, real notebook users, heterogeneous clusters,
networked storage, larger datasets, or institution-specific profile policies.

## Conclusion Validity

The matrix uses repeated deterministic runs across only eight workload
families, so family-level inference remains small. The reported comparisons
should be read as artifact evidence for the prototype and analysis pipeline,
not as definitive statistical proof of production impact. The confirmatory
matrix observed 110 OOMs, but method differences are concentrated in a small
number of families. Its cgroup-v2 memory values are genuine memory peaks. Its CPU values are either
full-window averages or legacy maxima combining the interval-sample maximum
with the full-window average, not a continuous peak time series. Historical capacity concurrency is supplementary because the evaluated
batch-generator source was not committed. Capacity-v2 was evaluated separately
from committed protocol `ca2e74b2043a`; it supports only controlled
request-reservation observations on that disposable single-node environment,
not a production-density conclusion.

## Local-Cluster Limitations

The Helm demo targets disposable local Kubernetes environments such as
OrbStack, kind, minikube, or k3d. Local clusters have simpler scheduling,
storage, image-cache, and contention behavior than production clusters. The
Kubernetes-backed environment had Metrics Server, but it retained zero per-job
snapshots because the jobs were short. Cgroup-v2 `memory.peak` provides a
pod-boundary memory peak. CPU reconciliation contains 202 full-window averages
and 86 legacy maxima of the interval-sample maximum and full-window average;
there are no genuine cgroup CPU peaks. Neither CPU class supports a peak-based
waste claim. The Helm demo and
preserved evaluation are not the same deployment path.

## Synthetic-Workload Limitations

Synthetic workloads use standard-library operations to emulate data-processing,
visualization, model-fitting, memory-pressure, and policy-boundary scenarios.
They avoid heavy dependencies and real datasets, which improves portability but
misses native library behavior, pandas/scikit-learn allocator patterns, GPU
kernels, file formats, I/O bottlenecks, and multi-user arrival patterns.

Protocol v3 improves operational contrast by targeting total cgroup memory near
the committed profile limits. It still uses synthetic computation and explicit
pressure padding. This establishes controlled resource envelopes, not the
claim that an equivalently sized real dataframe, model, allocator, or notebook
will behave identically. Calibration records are excluded from v3 method
comparisons, and noisy-input cases are reported as a separate robustness
stratum.

## Threshold Sensitivity

The recommender uses fixed rule thresholds such as the 0.5GB Medium signal and
2.0GB Large signal. Results near those boundaries may change if thresholds are
tuned, if dataset-size hints are noisy, or if administrators define different
resource bands. Thresholds must be fixed before evaluating a new matrix.

## Workload Drift

Notebook workloads evolve over time as courses, libraries, assignments, and
user behavior change. A manifest that represents one semester or local demo may
be stale for later deployments. Future evaluations should version workload
manifests and avoid mixing results across changed workload definitions.

## Incorrect User Intent

The approach assumes user intent text and optional context signals are at least
partly informative. Users may omit details, misunderstand their task, paste
irrelevant code, exaggerate dataset size, or request resources defensively.
The policy layer can constrain recommendations, but it cannot guarantee that
the inferred intent matches the eventual notebook behavior.

## Measurement Limitations

Local runs use Python runtime and `resource.getrusage` signals as portable
proxies. These are not equivalent to Kubernetes cgroup metrics or Prometheus
time series. Missing metrics are represented as null rather than inferred.
Short workloads may make runtime and peak measurements noisy.

Kubernetes creation and termination timestamps are quantized to one second in
the retained corpus. The original envelope analysis treated `1.0` versus `0.0`
second medians as a 100% improvement, and a later arbitrary minimum-delta guard
was post hoc. Timing rule 2.0.0 removes that guard. It treats each duration as
an interval, keeps zero valid, keeps missing values missing, rejects negative
timestamps, and adds no offset or smoothing. Method-level medians remain
indistinguishable at this resolution; no method timing advantage is supported.

## GPU Scope

The prototype can emit `gpu_or_large`, but the local demo maps GPU-like signals
to CPU profiles because no real GPU pool is configured. The artifact does not
evaluate GPU scheduling, accelerator utilization, CUDA availability, or
contention for GPU nodes.

## Missing History-Aware Evaluation

History-aware recommendation is future work. The current artifact does not
persist prior user runs, does not collect longitudinal identifiers, and does not
evaluate history-derived features. Any claim about history-aware performance is
outside the evidence provided here.
