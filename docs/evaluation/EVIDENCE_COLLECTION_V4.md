# Protocol-v4 Evidence Collection

## Purpose

This document specifies the three system-facing JSONL streams accepted by
`evaluation_v4.analyze`. Prediction records are generated automatically by
`evaluation_v4.run_recommenders`. System, user, and re-provisioning records are
collected separately because they have different units of analysis and consent
requirements.

Every line is one JSON object. Files are append-only. Preserve source logs,
Kubernetes objects, and metric snapshots before generating a summary. Paths in
records must be repository-relative, privacy-reviewed paths.

## Evidence classes

- `observed`: directly measured in the declared environment with supporting
  evidence where required;
- `simulated`: generated for planning or analyzer tests; and
- `replay`: produced by replaying a previously captured response or event.

The analyzer groups evidence classes separately. Only `observed` records open a
claim gate for system effectiveness, user acceptance, or re-provisioning.

## System trial record

Schema version: `system-trial-v4.0.0`.

The unit is one attempted method × workload-family × repeat trial. Required
fields include provenance, applied profile/image, requests, time-window mean
usage, peak memory, metric source/window, ready/Pending/OOM/image-pull/workload
outcomes, timing, cleanup, and supporting paths.

Important semantics:

- `pending_failure` means the pre-specified scheduling deadline was exceeded or
  the pod was unschedulable. It does not mean the pod briefly entered Pending.
- `cpu_usage_mean_m` and `memory_usage_mean_mib` are window means. Use `null`
  when unavailable; do not insert a peak or zero.
- `memory_usage_peak_mib` is a separate secondary statistic.
- `workload_success` remains `false` after an outcome failure; do not exclude
  the record.
- an observed record requires at least one `supporting_evidence_paths` entry.

Illustrative simulated shape:

```json
{"schema_version":"system-trial-v4.0.0","evidence_class":"simulated","trial_id":"example-only","experiment_id":"example-only","timestamp_utc":"2026-08-08T00:00:00Z","git_commit":"example","environment_id":"example","recommender":"rule_based_context","sample_id":"small-csv-canonical-en","workload_family":"small-csv","repeat_index":0,"applied_profile":"small","applied_image_id":"scipy-data-science","cpu_request_m":100,"memory_request_mib":244,"cpu_usage_mean_m":null,"memory_usage_mean_mib":null,"memory_usage_peak_mib":null,"measurement_window_seconds":null,"measurement_source":"unavailable","pod_ready":false,"pending_failure":false,"pending_duration_seconds":null,"oom_killed":false,"image_pull_failure":false,"workload_success":false,"time_to_ready_seconds":null,"workload_duration_seconds":null,"cleanup_status":"not-run","supporting_evidence_paths":[]}
```

This example is not a result.

## User decision record

Schema version: `user-decision-v4.0.0`.

The unit is one recommendation exposure. Actions are `accept`, `override`, or
`cancel`. A cancelled record must have null applied profile/image. Store a
random study-local `participant_block_id`, not a username or stable user ID.
Observed records require a nonblank consent version.

The participant block is needed to avoid treating repeated sessions from one
participant as independent. Keep the identity mapping outside the artifact.
Do not record raw intent, notebook code, dataset contents, or free-form user
text.

## Re-provisioning trial record

Schema version: `reprovision-trial-v4.0.0`.

The unit is one accepted re-provision transaction. Outcomes are:

- `completed`;
- `rolled_back`;
- `failed_pre_stop`; or
- `failed_after_stop`.

A `completed` outcome requires `replacement_ready: true`, but strict analysis
success additionally requires PVC continuity, workload resume, no Pending
failure, and no OOM. A successful rollback is reported as recovery and remains
a failed requested re-provision.

Observed records require supporting evidence. Recommended evidence includes the
sanitized re-provision audit sequence, old/new pod evidence, hashed PVC sentinel
proof, timing source, and cleanup record.

## Pre-run manifest

Before a system or re-provisioning experiment, freeze a manifest containing:

- protocol and dataset IDs/checksums;
- Git commit and dirty-tree status;
- Kubernetes context pseudonym, version, node allocatable resources, quota, and
  autoscaling state;
- Helm release/chart and rendered-values checksum;
- recommender backend/model, package checksum, policy and catalog versions;
- immutable notebook/workload image digests;
- workload seeds, repeat count, randomized order seed, cache condition, and
  deadlines;
- metric source and sampling interval/window; and
- cleanup and abort criteria.

Use `python -m evaluation_v4.plan_system` to create the paired randomized
system plan. It is read-only with respect to Kubernetes and refuses to
overwrite an existing plan directory.

Never store credentials, tokens, raw usernames, node addresses, or broad
unsanitized cluster dumps.

## Observed Kubernetes executor

The repository now includes the bounded executor used for the 2026-08-08
observed run. It drives the authenticated Hub preview/confirm flow, verifies
the resulting pod metadata and immutable image, runs the frozen v3 workload in
the pod, captures cgroup-v2 window metrics, retains failures, stops the server,
and verifies cleanup before advancing the plan.

```bash
.venv/bin/python -m evaluation_v4.run_system \
  --plan experiments/raw/v4-system-plan-YYYYMMDD/system-plan.jsonl \
  --experiment-id v4-system-YYYYMMDD \
  --output experiments/raw/v4-system-YYYYMMDD \
  --execute
```

The command refuses to mutate Kubernetes unless `--execute` is supplied and
refuses to overwrite an output directory. The target namespace must carry the
disposable-experiment safety label. When Metrics Server is unavailable, the
executor uses the in-container cgroup-v2 sampler in
`evaluation_v4.pod_runner`; missing measurements remain null.

Re-provision transactions and the explicit scheduler diagnostic are separate
streams:

```bash
.venv/bin/python -m evaluation_v4.run_reprovision \
  --experiment-id v4-reprovision-YYYYMMDD \
  --environment-id local-cluster-YYYYMMDD \
  --output experiments/raw/v4-reprovision-YYYYMMDD

.venv/bin/python -m evaluation_v4.run_pending_diagnostic \
  --output experiments/raw/v4-pending-diagnostic-YYYYMMDD
```

The Pending diagnostic deliberately requests more CPU than the disposable
node can supply, records the `FailedScheduling` event, labels itself
`diagnostic_only`, and deletes the exact pod in a `finally` block. It must not
be pooled with randomized effectiveness trials.

## Validation

The main analyzer validates exact fields and types before analysis:

```bash
.venv/bin/python -m evaluation_v4.analyze \
  --predictions /path/to/predictions.jsonl \
  --system-trials /path/to/system-trials.jsonl \
  --user-events /path/to/user-events.jsonl \
  --reprovision-trials /path/to/reprovision-trials.jsonl \
  --out /new/output/directory
```

It refuses to overwrite an existing directory, hashes every input, reports
metric availability, and keeps evidence classes separate.

Publication SVGs can be regenerated from the analyzer CSV files without
copying values by hand:

```bash
.venv/bin/python -m evaluation_v4.render_figures /new/output/directory
```
