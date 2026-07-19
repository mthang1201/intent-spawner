# Experiment Protocol

## Purpose

This protocol compares three pre-spawn profile-selection methods over the
benchmark scenarios in `benchmarks/workloads.yaml`:

- `static_manual`: deterministic baseline for a careful static/manual profile
  choice.
- `intent_only`: uses only the benchmark intent text and policy.
- `context_aware`: uses intent text, permitted dataset-size hint, derived
  context signals, and policy while preserving explanation output.

`history_aware` is excluded. The repository does not currently implement,
test, or document a persisted history store, prior-run feature capture, or
history-derived recommendation policy. Treat it as future work only.

## Cluster Assumptions

Cluster-backed runs must use a non-production Kubernetes cluster dedicated to
the experiment window. The namespace is `z2jh-context-demo` unless a later
document explicitly justifies another namespace. Operators must confirm the
current context before any mutating command:

```bash
kubectl config current-context
```

The local synthetic runner is the default safe path for smoke validation and
non-cluster tests. Cluster claims about pending time, OOMKilled events, and
usage metrics require preserved Kubernetes evidence. If `metrics-server` or
Prometheus is unavailable, peak usage fields remain null and the absence is
reported rather than inferred.

## Profile Definitions

Approved applied profiles are the same Small, Medium, and Large profile bands
used by the Helm demo:

| Profile | CPU request | CPU limit | Memory request | Memory limit |
| --- | ---: | ---: | ---: | ---: |
| `small` | 100m | 500m | 256M | 384M |
| `medium` | 500m | 1 CPU | 768M | 1G |
| `large` | 1500m | 2 CPU | 1536M | 2G |

The recommender may emit `gpu_or_large`, but this demo has no real GPU profile.
Policy maps that signal to an approved CPU profile or records a warning when a
scenario disallows it.

## Method Policies

`static_manual` selects the smallest approved profile listed in the workload's
`expected_acceptable_profiles`. This is deterministic and intentionally fair:
it does not choose an obviously wrong profile merely to make another method
look better.

`intent_only` calls the recommender with the intent text, `dataset_size_gb=0`,
and empty code context. Dataset-size hints and code-context hints are still
stored as benchmark inputs where the schema requires them, but they are not
used by this method.

`context_aware` calls the recommender with the intent text, dataset-size hint,
and joined code-context hints from the manifest. Raw code is not stored in raw
records; only derived context summaries are retained.

All methods apply the same workload policy constraints after recommendation or
static selection.

## Controlled Variables

Use a fixed Git commit, workload manifest, profile definitions, namespace,
container image set, chart version, and random seed offset for a complete run.
Do not tune thresholds after observing results. Do not change the workload
manifest mid-matrix; if the manifest changes, start a new experiment directory.

The main matrix size is derived from the manifest, not hard-coded. With the
current manifest this is approximately 10-12 workloads x 3 methods x 5 repeats.

## Measurement Procedure

Run a smoke validation before any full run:

```bash
.venv/bin/python -m experiments.runner --smoke --environment-id local-smoke
```

Preview a complete matrix without executing workloads:

```bash
.venv/bin/python -m experiments.runner --full-matrix --repeats 5 --seed 20260719 --dry-run --environment-id local-benchmark
```

Run one method across selected workloads:

```bash
.venv/bin/python -m experiments.runner --method intent_only --repeats 5 --seed 20260719 --environment-id local-benchmark
```

Run one workload across all methods:

```bash
.venv/bin/python -m experiments.runner --workload-id ml_sklearn_fit_medium --repeats 5 --seed 20260719 --environment-id local-benchmark
```

Run the complete matrix:

```bash
.venv/bin/python -m experiments.runner --full-matrix --repeats 5 --seed 20260719 --timeout 120 --environment-id local-benchmark
```

Resume an interrupted matrix from its existing immutable run directory:

```bash
.venv/bin/python -m experiments.runner --resume --experiment-dir experiments/raw/<experiment-dir> --environment-id local-benchmark
```

Aggregate completed raw records:

```bash
.venv/bin/python -m experiments.runner --aggregate --experiment-dir experiments/raw/<experiment-dir> --csv-out experiments/summaries/<experiment-dir>.csv
```

## Repeats And Seeds

Repeats are zero-indexed. For each workload and method, repeat indices run from
`0` to `repeats - 1`. The runner derives each workload seed from the manifest's
`deterministic_seed`, the operator-supplied `--seed` offset, and the repeat
index. Reusing the same manifest, experiment ID, seed, methods, workloads, and
repeat count produces the same planned matrix.

## Exclusion Criteria

Exclude a run from comparative summaries only when the raw record documents one
of these conditions:

- infrastructure failure prevented the workload from being attempted;
- the operator interrupted the run before a raw record was appended;
- cluster evidence is missing for a cluster-only metric;
- cleanup failed and the failure could contaminate later combinations.

Do not silently skip failed combinations. Resume mode skips only combinations
that already have raw records in the experiment directory.

## Failure Handling

Every attempted local workload writes stdout and stderr artifacts before the
normalized record is appended. Timeouts are recorded with
`exit_reason="Timeout"` and `timeout=true`. Local cleanup is marked
`completed` because synthetic workloads use temporary files that are deleted by
the workload process before exit.

Infrastructure failures such as duplicate run directories, missing workload
IDs, invalid matrices, unreadable raw JSONL, or write failures return a non-zero
exit status and print an explicit infrastructure failure message.

## Analysis Plan

Primary analysis compares applied profiles, policy warnings, success, timeout,
OOM evidence where available, runtime, time to success, and requested resources
by workload, method, and repeat. Derived summaries should report repeated-trial
counts and should keep failed or timed-out attempts visible.

For recommendation-quality analysis, compare each method's applied profile to
the workload's acceptable profile set. For context value, compare `intent_only`
against `context_aware` on the same workload and repeat index without changing
thresholds.

## Limitations

The synthetic runner does not prove cluster-wide efficiency, scheduler behavior,
or real user experience. Dataset-size hints are declared inputs, not measured
file sizes. Local `resource.getrusage` peak memory is useful for smoke checks
but is not equivalent to Kubernetes pod metrics. GPU behavior is represented as
a policy signal only; no GPU workload is executed.
