# Benchmark Design

## Purpose

This benchmark suite defines deterministic workload scenarios for comparing
static/manual, intent-only, and context-aware resource-profile selection. It is
metadata-first on purpose: the repository can validate the benchmark shape
without running a full Kubernetes experiment matrix or fabricating results.

The workload manifest is `benchmarks/workloads.yaml`. The executable helper is
`benchmarks/workload_runner.py`. All data is synthetic, generated from explicit
seeds, and either held in memory or written only to temporary files that are
deleted before process exit.

## Why Each Workload Exists

`light_basic_python` is the control case for a genuinely small interactive
session. It should remain compatible with the Small profile and helps measure
whether a recommender overreacts when no data, model, or GPU signal is present.

`light_small_csv_read` represents the common "open a CSV" notebook. It is
operationally light, but its pandas/read_csv hints are useful for testing when
context-aware mode moves from Small to Medium.

`light_visual_aggregation` covers quick exploratory plots and simple
aggregations. The runner produces histogram-like metadata instead of image files
so validation remains machine-readable.

`data_pandas_read_transform` exercises tabular read/filter/transform signals at
a declared dataset size above the 0.5GB threshold. It exists to test whether
intent-only and context-aware modes avoid treating medium tabular work as a
basic notebook.

`data_dataframe_join_medium` represents joins, where intermediate memory can be
larger than the source tables suggest. Medium and Large are both acceptable
because policy and cluster capacity may justify either.

`data_large_aggregation` is the high-end tabular case. Its dataset-size hint is
above 2GB, so it checks whether the Large signal threshold is honored before any
cluster run.

`ml_sklearn_fit_small` covers a small model fit. Training intent alone should
not imply GPU, but it is enough to make Medium reasonable.

`ml_sklearn_fit_medium` combines training, pandas context, and a medium dataset
hint. It maps to the Large band under the current rule set and is the core
machine-learning comparison case for intent-only versus context-aware modes.

`ml_sklearn_fit_memory_pressure` adds bounded scratch allocation to make memory
pressure visible without an unbounded OOM script. It is meant for later
Kubernetes trials, not for tuning thresholds in this task.

`boundary_below_0_5_ambiguous` sits immediately below the 0.5GB dataset
threshold with incomplete intent. It tests stability around threshold edges and
records that Small and Medium may both be acceptable operational choices.

`boundary_above_0_5_conflicting` sits immediately above the 0.5GB threshold and
has strong training intent but weak pasted code context. It also covers a
misleading or uncertain dataset-size hint, which helps evaluate whether context
adds useful caution.

`policy_gpu_disallowed` combines harmless natural language with strong
deep-learning code hints. The raw recommender should notice GPU/deep-learning
signals, while the scenario's policy constraints require fallback or manual
review because GPU and Large profiles are unavailable.

## Limits Of Representativeness

The runner intentionally uses standard-library synthetic operations instead of
real pandas, scikit-learn, TensorFlow, or GPU kernels. This makes the benchmark
portable and deterministic, but it cannot reproduce every allocator behavior,
native library thread pool, file format, or GPU scheduling constraint.

Dataset-size hints are declared inputs, not measured file sizes. They represent
what a user or notebook context might tell the recommender before spawn. Later
experiments may compare declared hints with observed memory and runtime, but
this task does not produce those results.

The suite is small by design. It covers representative decision boundaries and
failure modes for the prototype, not the full space of notebook workloads,
multi-user arrivals, storage systems, or production cluster policies.

## What The Benchmark Can Prove

The benchmark can support non-cluster claims such as:

- the manifest is complete, deterministic, and machine-readable;
- every workload has an auditable expected acceptable profile set;
- benchmark categories and edge cases are represented;
- the synthetic runner can execute representative workloads and emit JSON
  metadata;
- recommendation inputs can be evaluated consistently across static/manual,
  intent-only, and context-aware modes.

## What The Benchmark Cannot Prove

The benchmark alone cannot prove that context-aware selection reduces OOMs,
improves cluster utilization, or improves real user experience. Those claims
require later repeated Kubernetes experiments, raw output preservation, metrics
collection or documented metrics absence, and statistical summaries.

It also cannot prove history-aware behavior. The roadmap explicitly treats
history-aware provisioning as future work, and this suite does not collect or
consume user history.

## Mapping To Research Questions

RQ1 asks whether intent and lightweight context can be mapped to approved
profiles before spawn. Every workload provides intent text, dataset-size hint,
code-context hints, expected acceptable profiles, and a rationale.

RQ2 asks whether recommendations reduce underprovisioning compared with static
Small choices. The large aggregation and memory-pressure training workloads are
the future trial candidates for that question, but no result files are created
in this task.

RQ3 asks whether recommendations reduce defensive over-requesting for light
workloads. The three light workloads provide Small-compatible cases where a
manual Large choice would be wasteful.

RQ4 asks whether context adds value beyond intent-only. The boundary and
conflicting-signal workloads intentionally separate intent, dataset hints, and
code-context signals so an ablation can compare modes without changing
thresholds after observing outcomes.

RQ5 asks about safety and policy boundaries. The GPU-disallowed policy workload
tests whether a strong recommendation signal can still be constrained by an
allowed profile policy and represented without executing GPU code or storing raw
notebooks.

