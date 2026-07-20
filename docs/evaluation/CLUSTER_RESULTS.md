# Kubernetes Cluster Results

Evaluated workload commit: `39b69731a9aeaa85247c01e946e26656beae6e64`. Evidence scope: one disposable ARM64 Minikube v1.33.1 node with 6 CPUs and 6088560Ki allocatable memory.

## Run accounting

| Stage | Planned | Completed | Failed | Timed out | Excluded |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ground truth | 108 | 108 | 0 | 0 | 0 |
| Comparative | 180 | 180 | 0 | 0 | 0 |
| Capacity | 108 pods / 9 batches | 108 pods / 9 batches | 0 | 0 | 0 |

## Ground truth

All 12 workloads completed reliably under Small. The manifest expectations were excluded from derivation. The preregistered 20% time-improvement branch was not measurement-valid for differences of two seconds or less because Kubernetes creation and termination timestamps have one-second resolution. The final audit therefore added a disclosed measurement-adequacy guard; this is a correction to analysis validity, not a newly optimized effect threshold.

## Comparative outcome

| Method | Acceptable / 60 | Median waste | Median time-to-success (s) | OOM |
| --- | ---: | ---: | ---: | ---: |
| static_default | 5 | 0.979 | 1.000 | 0 |
| intent_only | 30 | 0.958 | 1.000 | 0 |
| context_aware | 20 | 0.979 | 1.000 | 0 |

An earlier 108-run ground-truth pilot is excluded from every table and figure because its environment file retained unnecessary machine identifiers. Its ignored raw directory remains local and no pilot value was copied into the sanitized matrix.

All methods completed every run without OOM. Success alone therefore does not establish recommendation quality. The workload implementations are much smaller than their declared dataset-size hints, so these acceptable-profile rates diagnose behavior on this synthetic suite rather than predictive accuracy for real notebooks.

## Capacity pressure

The retained records show median maximum concurrency of 9 pods for intent-only and 7 for static-default and context-aware across three counterbalanced repeats. Fifteen static-default pods, nine intent-only pods, and fifteen context-aware pods retained FailedScheduling evidence, with median queued Pending time of 22 seconds for each method. These are request-reservation observations under the fixed 20-second hold.

The exact capacity batch generator is not present in evaluated commit `39b6973`; only its plan, nine immutable batch records, per-pod outcomes, and environment record are retained. The observation is therefore descriptive operational evidence, not a fully reproducible density result, and must not be generalized to production cluster density.

## Measurement limits

The standard-library workloads are short and small relative to their declared dataset hints. Results apply only to this benchmark, image, profile table, and local single-node cluster. No history-aware or GPU evaluation was performed. Metrics Server availability was verified by a documented probe, but it captured zero per-job snapshots for the 288 short ground-truth/comparative pods. Memory peaks come from cgroup-v2 `memory.peak`. Only 86 jobs had at least one 10ms CPU sample. For the other 202, evaluated code stored the full-job CPU average in the historical `peak_cpu_m` field; those values must not be cited as CPU peaks. Current code preserves the average separately and leaves an unsampled peak missing.

Raw inputs: `results/cluster/raw/`. `python -m cluster_evaluation.validate_artifacts` reconciles every retained plan, record, sidecar, resource mapping, and supporting path. Every derived CSV row contains supporting run IDs.
