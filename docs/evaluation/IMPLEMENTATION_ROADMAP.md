# Research Evaluation Implementation Roadmap

## Purpose

This roadmap describes the integrated repository state. It distinguishes the
implemented local synthetic research artifact from the Kubernetes demonstration
and from future production or history-aware work. Plans and hypotheses are not
treated as observed results.

## Integrated State

The repository now contains:

- static Small, Medium, and Large JupyterHub profiles;
- an intent/context-aware JupyterHub spawn form and pre-spawn resource hook;
- a deterministic 12-workload synthetic benchmark;
- a local comparative experiment runner with `static_manual`, `intent_only`,
  and `context_aware` methods;
- schema-validated, append-only JSONL result recording;
- committed sanitized local raw snapshots;
- deterministic analysis regeneration into CSV tables, SVG figures, and
  `docs/evaluation/RESULTS.md`;
- setup, verification, environment-reporting, resume, dry-run, smoke, and
  aggregation commands;
- data-governance and threats-to-validity documentation.
- a preserved Kubernetes-backed corpus with independently swept profiles,
  isolated operational methods, applied-resource evidence, cgroup peaks, and a
  separate capacity-pressure record;
- cluster raw-integrity validation and deterministic table/figure regeneration.

The preserved comparative run is local and synthetic. It is not a live
multi-user Kubernetes evaluation and does not establish production cluster
efficiency, real-user accuracy, or causal generalization.

## Capability Matrix

| Capability | Status | Evidence |
| --- | --- | --- |
| Static profile demo | Implemented | `helm/baseline-values.yaml` |
| Context-aware spawn demo | Implemented | `helm/proposed-values.yaml` |
| Recommender stabilization | Implemented and tested | `recommender/recommender.py`, `recommender/test_recommender.py` |
| Deterministic benchmark | Implemented and tested | `benchmarks/workloads.yaml`, `benchmarks/workload_runner.py` |
| Static comparative method | Implemented for local synthetic runs | `experiments/methods.py` |
| Intent-only isolation | Implemented for local synthetic runs | `experiments/methods.py`, `tests/test_experiment_runner.py` |
| Context-aware isolation | Implemented for local synthetic runs | `experiments/methods.py`, `tests/test_experiment_runner.py` |
| Immutable raw recording | Implemented | `experiments/recorder.py`, `experiments/jsonl_io.py` |
| Versioned result schema | Implemented | `experiments/result_schema.schema.json` |
| Runner, resume, dry-run | Implemented | `experiments/runner.py` |
| Analysis regeneration | Implemented and tested | `experiments/analyze_results.py`, `tests/test_analysis_outputs.py` |
| Sanitized preserved evidence | Implemented | `experiments/raw/`, `results/` |
| Kubernetes pod experiment orchestration | Implemented for ground-truth/comparative matrices | `cluster_evaluation/runner.py` |
| Kubernetes raw validation and regeneration | Implemented | `cluster_evaluation/validate_artifacts.py`, `cluster_evaluation/analyze.py` |
| Kubernetes cgroup peak observations | Implemented with limitations | `results/cluster/raw/`; zero Metrics Server job snapshots and 202/288 zero periodic samples |
| Capacity batch reproduction | Blocked | Raw plan/batches exist, but evaluated generator source is absent |
| History-aware method | Not implemented or evaluated | No history collection, storage, or decision path |
| Project software license | Not resolved | No project license file |

## Method Boundary

### `static_manual`

The local experiment uses the workload manifest's frozen static profile as a
repeatable proxy for a manual choice. It uses the same resource-band mapping as
the other methods. It does not model human interaction or adapt after failure.

### `intent_only`

This method passes only intent text to the recommender. Dataset-size and
code-context fields are unavailable by construction, and tests verify that
changing those fields cannot change an intent-only decision.

### `context_aware`

This method uses the documented intent, dataset-size hint, and code-context
signals. Stored experiment records contain a derived signal summary rather than
raw notebook contents.

### History-aware

History-aware provisioning is excluded. A defensible implementation would need
privacy-preserving workload identity, event and metric collection, durable
storage, retention rules, policy guardrails, and cold-start versus warm-history
trials. It remains future work.

## Evidence And Claim Boundary

The integrated artifact supports claims about deterministic method construction,
profile recommendation, bounded local workload execution, schema-valid result
recording, and exact regeneration of the committed local synthetic summaries.

It does not support claims about representative production workloads,
production cluster utilization, real users, GPU scheduling, OOM reduction, or
the effectiveness of history-aware provisioning. The retained capacity result
is descriptive rather than fully reproducible. See
`THREATS_TO_VALIDITY.md` and `RESULTS.md`.

## Remaining Evaluation Work

1. Recreate and preregister the capacity runner, then repeat the capacity
   experiment from committed source.
2. Use longer, resource-representative workloads and a time-series metrics
   source before making performance or utilization claims.
3. Add OOM-producing cases if the thesis needs an OOM-reduction claim.
4. Report uncertainty and sensitivity to workload mix, thresholds, static
   assignments, and cluster capacity.
5. Resolve the project software license before redistribution.
6. Treat any real-user study as a separate ethics, consent, privacy, and
   retention project.

No new evaluation is performed by the branch-integration task.
