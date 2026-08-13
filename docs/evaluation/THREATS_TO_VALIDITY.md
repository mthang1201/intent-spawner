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
