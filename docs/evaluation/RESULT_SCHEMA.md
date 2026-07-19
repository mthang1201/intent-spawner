# Experiment Result Schema

## Purpose

Raw experiment output uses newline-delimited JSON (`.jsonl`) so each run can be
appended without rewriting earlier records. The formal schema is versioned as
`1.0.0` in `experiments/result_schema.py` and mirrored in
`experiments/result_schema.schema.json`.

CSV summaries are derived with:

```bash
python3 -m experiments.export_results_csv \
  --raw-jsonl experiments/raw/results.jsonl \
  --csv-out experiments/summaries/results.csv
```

The exporter refuses to overwrite an existing CSV unless `--overwrite` is
passed. Derived summaries must never replace raw JSONL or supporting logs.

## Raw Record Format

Each line is one complete JSON object. Every field listed below is required in
every record. If a field is not applicable or unavailable, it must be present as
`null`, `[]`, or `{}` according to its nullability rule. Missing measurements
must never be guessed or replaced with illustrative values.

## Field Definitions

| Field | Type | Unit | Nullable | Source |
| --- | --- | --- | --- | --- |
| `schema_version` | string | none | no | Constant from `experiments.result_schema.SCHEMA_VERSION`; current value is `1.0.0`. |
| `run_id` | string | none | no | CLI argument or generated UTC timestamp plus workload ID. Must identify one attempted run. |
| `timestamp` | string | ISO-8601 UTC | no | Recorder wall-clock time when the normalized record is built. |
| `git_commit` | string | Git SHA | no | `git rev-parse HEAD` from the repository root. |
| `environment_id` | string | none | no | Operator-supplied environment label such as `local-smoke`, `fixture-cluster`, or a sanitized cluster label. Do not use usernames. |
| `method` | string enum | none | no | One of `static_manual`, `intent_only`, or `context_aware`. |
| `workload_id` | string | none | no | `workload_id` from `benchmarks/workloads.yaml`. |
| `repeat_index` | integer | count | no | Zero-based repeat/trial index for repeated experiments. |
| `random_seed` | integer | seed value | no | Explicit seed used by the synthetic workload; normally `deterministic_seed` from the manifest. |
| `input_intent` | string | none | yes | Benchmark intent text or sanitized operator-provided intent. Do not store raw notebook code or datasets here. |
| `dataset_size_hint_gb` | number | GB | yes | Declared input hint from the benchmark manifest or spawn form. This is an input signal, not measured file size. |
| `context_signal_summary` | object | none | no | Derived summary only. Current keys include `raw_context_stored`, `raw_context_available`, `hint_count`, `detected_terms`, and `dataset_size_signal_used`. Raw code context is not stored. |
| `recommended_profile` | string enum | none | yes | Recommender output: `small`, `medium`, `large`, or `gpu_or_large`. Null for `static_manual`. |
| `applied_profile` | string enum | none | yes | Actual profile applied after policy mapping: `small`, `medium`, or `large`. Null only when no valid profile could be applied. |
| `recommendation_reasons` | string array | none | no | Human-readable reasons returned by the recommender, or the deterministic static/manual selection policy note for `static_manual`. |
| `policy_warnings` | string array | none | no | Policy fallback or warning messages. Empty when no policy adjustment occurred. |
| `cpu_request_m` | integer | millicores | yes | Applied profile request, or Kubernetes pod spec when captured. Null if no applied profile/resource evidence exists. |
| `cpu_limit_m` | integer | millicores | yes | Applied profile limit, or Kubernetes pod spec when captured. Null if unavailable. |
| `memory_request_mi` | integer | MiB | yes | Applied memory request converted from Kubernetes quantity or profile policy. Decimal Kubernetes units such as `M` and `G` are converted to MiB. |
| `memory_limit_mi` | integer | MiB | yes | Applied memory limit converted from Kubernetes quantity or profile policy. Null if unavailable. |
| `peak_cpu_m` | number | millicores | yes | Observed peak CPU from metrics snapshots. Null when Prometheus or metrics-server data is unavailable. |
| `peak_memory_mi` | number | MiB | yes | Observed peak memory from metrics snapshots, or local smoke-process `resource.getrusage` fallback. Null when unavailable. |
| `resource_measurement_source` | string | none | no | `metrics_server`, `prometheus` if added later, `python_resource_getrusage` for local smoke fallback, or `not_available`. |
| `pod_pending_duration_seconds` | number | seconds | yes | Kubernetes pod creation timestamp to `PodScheduled=True`. Null without pod evidence or if unscheduled. |
| `workload_runtime_seconds` | number | seconds | yes | Kubernetes container `startedAt` to `finishedAt`, or local workload runner elapsed seconds. |
| `time_to_success_seconds` | number | seconds | yes | Pod creation to successful container finish, or local successful workload elapsed seconds. Null for failures/timeouts. |
| `oom_killed` | boolean | none | yes | True when Kubernetes termination reason is `OOMKilled`, exit code is 137, or local smoke exits 137. Null when no status evidence exists. |
| `exit_reason` | string | none | yes | Kubernetes termination reason or local runner classification such as `Completed`, `Error`, or `Timeout`. |
| `exit_code` | integer | process code | yes | Container or local process exit code. Null for timeouts without an exit code. |
| `restart_or_respawn_count` | integer | count | yes | Sum of Kubernetes container restart counts or local `0`. Null when unavailable. |
| `success` | boolean | none | yes | True only for observed successful completion. False for observed failures or timeouts. Null only when outcome is not yet known. |
| `timeout` | boolean | none | no | True when the configured workload timeout was reached. |
| `cleanup_status` | string | none | no | `completed`, `not_required`, `not_started`, or an operator-supplied status. Partial cleanup failures should be recorded, not hidden. |
| `error_message` | string | none | yes | Error or timeout detail. Null for successful runs with no error. |
| `supporting_log_paths` | string array | paths | no | Paths to preserved stdout, stderr, pod JSON, event JSON, pod logs, metrics snapshots, or metrics-unavailable text. |
| `kubernetes_evidence` | object | none | no | Sanitized Kubernetes evidence object. Empty when no pod evidence is available. |

## Kubernetes Evidence Object

When pod evidence is available, `kubernetes_evidence` includes:

| Key | Meaning |
| --- | --- |
| `pod_name`, `namespace` | Sanitized Kubernetes metadata names. Non DNS-label values are replaced with `redacted`. |
| `phase` | Current pod phase from `status.phase`. |
| `phase_transitions` | Pod condition transition timestamps and reasons from `status.conditions`. Kubernetes does not preserve every historical phase; this records the available condition evidence. |
| `events` | Sanitized event type, reason, message, count, and timestamps from `kubectl get events`. |
| `termination_reason`, `termination_exit_code` | Container termination reason and exit code from current or last terminated state. |
| `restart_count` | Sum of container restart counts. |
| `requests_limits` | CPU and memory requests/limits from the first container. |
| `annotations` | Allowlisted annotations only, currently keys starting with `z2jh-context-demo.local/`. |
| `environment_variables` | Allowlisted environment variables only: `RECOMMENDED_PROFILE`, `RECOMMENDATION_REASONS`, `CONTEXT_DATASET_SIZE_GB`, and `SELECTED_STATIC_PROFILE`. |
| `scheduling_or_pending_reasons` | Unschedulable condition reasons and warning event messages such as `FailedScheduling`. |
| `pod_pending_duration_seconds` | Derived scheduling latency. |
| `workload_runtime_seconds` | Derived container runtime. |
| `time_to_success_seconds` | Derived creation-to-success time for successful pods. |
| `oom_killed` | Derived OOMKilled flag. |

The live recorder path can collect read-only evidence with `--namespace` and
`--pod-name`. It writes pod JSON, event JSON, pod logs, and metrics snapshots
under `experiments/raw/<run_id>/<workload_id>/kubernetes/`.

## Resource Measurement Sources

Preferred order for peak usage:

1. Prometheus query results, if a future evaluator provides them in the metrics
   snapshot format and records `resource_measurement_source="prometheus"`.
2. Metrics-server snapshots from `kubectl top pod --containers`.
3. Local smoke fallback from Python `resource.getrusage`, which can report peak
   memory for the local workload process but not Kubernetes pod CPU.
4. `not_available`, with `peak_cpu_m=null` and `peak_memory_mi=null`.

If metrics-server is unavailable, the recorder preserves the `kubectl top`
error text in supporting logs and leaves missing peak fields as `null`. It must
not infer usage from requests, limits, workload class, or expected profile.

## Privacy And Integrity Rules

- Raw JSONL is append-only; do not edit earlier lines to correct a run.
- Preserve raw stdout, stderr, pod JSON, event JSON, pod logs, and metrics
  snapshots beside the JSONL record when available.
- Do not store raw notebook code, raw datasets, secrets, usernames, or broad
  Kubernetes metadata dumps in committed examples.
- Store derived context features and allowlisted annotations/env vars instead
  of raw code snippets.
- Sanitized fixtures may be committed under `tests/fixtures/`; live raw outputs
  stay ignored under `experiments/raw/`.
- Partial failures, timeouts, missing metrics, and cleanup failures must be
  represented explicitly in the record.

## Smoke Command

This command records one safe local workload without a live cluster:

```bash
python3 -m experiments.runner \
  --smoke \
  --environment-id local-smoke
```

The lower-level single-record command remains available:

```bash
python3 -m experiments.recorder \
  --workload-id light_basic_python \
  --method context_aware \
  --repeat-index 0 \
  --environment-id local-smoke \
  --run-local-workload
```
