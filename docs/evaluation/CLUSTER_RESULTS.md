# Kubernetes Cluster Results

Evaluated commit: `39b69731a9aeaa85247c01e946e26656beae6e64`. Evidence scope: one disposable ARM64 Minikube v1.33.1 node with 6 CPUs and 6088560Ki allocatable memory.

## Run accounting

| Stage | Planned | Completed | Failed | Timed out | Excluded |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ground truth | 108 | 108 | 0 | 0 | 0 |
| Comparative | 180 | 180 | 0 | 0 | 0 |
| Capacity | 108 pods / 9 batches | 108 pods / 9 batches | 0 | 0 | 0 |

## Ground truth

All 12 workloads completed reliably under Small. The preregistered waste/time rule, not recommender output, determines the acceptable sets in `benchmarks/observed_resource_envelopes.yaml`. The prior manifest expectations are flagged as not operationally grounded.

## Comparative outcome

| Method | Acceptable / 60 | Median waste | Median time-to-success (s) | OOM |
| --- | ---: | ---: | ---: | ---: |
| static_default | 20 | 0.979 | 1.000 | 0 |
| intent_only | 40 | 0.958 | 1.000 | 0 |
| context_aware | 25 | 0.979 | 1.000 | 0 |

An earlier 108-run ground-truth pilot completed successfully but is excluded from every table and figure because its environment file contained unnecessary machine identifiers. Its immutable raw directory is retained locally under `experiments/raw/` and no pilot value was copied into the sanitized matrix.

All methods completed every run without OOM. Success alone therefore does not establish recommendation quality. Under the independently observed envelopes, larger profiles chiefly increased reservation waste.

## Capacity pressure

Across three counterbalanced repeats, intent-only admitted 9 pods concurrently; static-default and context-aware admitted 7. All 12 pods per batch eventually completed. This is a scheduler reservation result under the tested requests and 20-second hold, not proof of production utilization or general cluster density.

## Limits

The standard-library workloads are short and small relative to their declared dataset hints. Results apply only to this benchmark, image, profile table, and local single-node cluster. No history-aware or GPU evaluation was performed. Metrics Server verified pod telemetry; precise peaks use in-container cgroup-v2 observations.

Raw inputs: `results/cluster/raw/`. Every CSV row contains its supporting run IDs.
