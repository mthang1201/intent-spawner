# Threats To Validity

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

The matrix uses repeated deterministic runs, but it is still small. The
reported comparisons should be read as artifact evidence for the prototype and
analysis pipeline, not as a definitive statistical proof of production impact.
The cluster matrix observed no OOM and therefore cannot estimate OOM reduction.
Its peak values are cgroup observations, not a monitoring-system time series.
Capacity concurrency is descriptive because the evaluated batch-generator
source was not committed.

## Local-Cluster Limitations

The Helm demo targets disposable local Kubernetes environments such as
OrbStack, kind, minikube, or k3d. Local clusters have simpler scheduling,
storage, image-cache, and contention behavior than production clusters. The
Kubernetes-backed environment had Metrics Server, but it retained zero per-job
snapshots because the jobs were short. Cgroup-v2 final counters and
`memory.peak` provide pod-boundary observations; 202/288 jobs completed before
one periodic 10ms sample. The Helm demo and preserved evaluation are not the
same deployment path.

## Synthetic-Workload Limitations

Synthetic workloads use standard-library operations to emulate data-processing,
visualization, model-fitting, memory-pressure, and policy-boundary scenarios.
They avoid heavy dependencies and real datasets, which improves portability but
misses native library behavior, pandas/scikit-learn allocator patterns, GPU
kernels, file formats, I/O bottlenecks, and multi-user arrival patterns.

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
second medians as a 100% improvement. The final audit added a disclosed
measurement-adequacy guard and regenerated derived outputs without editing raw
records. Because the guard is post hoc, the corrected acceptable-profile rates
remain diagnostic rather than confirmatory.

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
