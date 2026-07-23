# Resource-Envelope Hold-out Protocol v3.0.0

## Status and evidence boundary

This document preregisters the next experiment. The implementation is present,
but no v3 cluster result is committed and no v3 claim is currently observed.
The v1/v2 manifest, runner, raw evidence, checksums, and derived reports remain
unchanged.

The 2026-07-23 independent implementation audit found and corrected
pre-execution safety/provenance defects without changing any scientific
decision. This revision freezes the reviewed source, but real execution remains
blocked: no intended registry is configured, no registry manifest digests
exist, and the experiment Helm image remains mutable. See
`RESOURCE_ENVELOPE_V3_IMPLEMENTATION_AUDIT.md` and the append-only blocked
readiness report under `results/cluster/preflight-v3/`.

Protocol v3 addresses the fact that the earlier declared dataset-size hints
were much larger than the executed workloads. It uses bounded synthetic
resource envelopes with real page-touched anonymous memory, separates
calibration from hold-out evaluation, and keeps direct-pod evidence distinct
from JupyterHub end-to-end evidence.

All cluster commands in this document are mutating unless explicitly marked as
dry-run. They may run only on a disposable local cluster. The only permitted
namespace is `z2jh-context-demo`.

## Research questions and hypotheses

1. Can the suite reproducibly establish Small, Small-to-Medium, and
   Medium-to-Large memory boundaries?
2. Does context-aware selection reduce OOM and underprovisioning relative to a
   fixed Medium default and intent-only selection on the truthful hold-out?
3. Does it reduce memory reservation waste for light work without reducing
   reliability?
4. Do dataset-size and derived code-context signals add value beyond intent?
5. Does the two-worker CPU workload run at least 20% faster on Medium than
   Small?
6. What failures occur when a declared hint is overstated or the true demand is
   hidden?
7. Does the JupyterHub pre-spawn path apply the same profile resources observed
   in direct-pod planning?

The noisy-input cases are not preregistered to favor context-aware selection.
They are robustness cases intended to expose false-positive overprovisioning
and false-negative underprovisioning.

## Fixed profile matrix

| Profile | CPU request | CPU limit | Memory request | Memory limit | Limit in MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| Small | 100m | 500m | 256M | 384M | 366.2 |
| Medium | 500m | 1 | 768M | 1G | 953.7 |
| Large | 1500m | 2 | 1536M | 2G | 1907.3 |

The successful cgroup peak bands are 315–335 MiB, 820–880 MiB, and
1600–1700 MiB. The baseline is `static_default=medium`. Intent-only receives
intent and no other recommender input. Context-aware receives intent, declared
size, and derived code-context signals. `static_manual` is excluded because its
historical implementation reads oracle-style acceptable-profile fields.

## Workload implementation and bounds

The machine-readable source of truth is `benchmarks/workloads-v3.yaml`. There
are four calibration workloads, six confirmatory hold-outs, and two robustness
hold-outs:

| ID | Set | Operation | Target or CPU units | Expected minimum |
| --- | --- | --- | --- | --- |
| `cal_small_envelope` | calibration | stream aggregation | 325 MiB | Small |
| `cal_small_medium_boundary` | calibration | table transform | 850 MiB | Medium |
| `cal_medium_large_boundary` | calibration | materialization | 1650 MiB | Large |
| `cal_cpu_units` | calibration | two CPU workers | 600 units/worker | Small reliability |
| `h01_small_stream` | core | stream aggregation | 325 MiB | Small |
| `h02_medium_size_signal` | core | table transform | 850 MiB | Medium |
| `h03_medium_code_signal` | core | table join | 850 MiB | Medium |
| `h04_large_honest` | core | sort/group | 1650 MiB | Large |
| `h05_large_context_recovery` | core | encoded fit | 1650 MiB | Large |
| `h06_cpu_parallel` | core | two CPU workers | 600 units/worker | Small reliability |
| `h07_noisy_overstated` | robustness | stream aggregation | 325 MiB | Small |
| `h08_hidden_large` | robustness | materialization | 1650 MiB | Large |

Memory workloads first perform a deterministic synthetic operation. The runner
then allocates 8 MiB anonymous chunks, touches every 4 KiB page, retains every
block, reads `memory.current`, and stops at the total-cgroup target. It holds
the peak for eight seconds. Records distinguish useful Python object bytes from
pressure-padding bytes.

Hard limits enforced in code are:

- target at most 1700 MiB;
- workload deadline at most 120 seconds;
- total-cgroup overshoot guard of target +16 MiB;
- at most two CPU worker processes;
- no external input, download, persistent dataset, or unbounded file;
- deterministic checksum over workload ID, operation, seed, and result.

The manifest can be validated without executing pressure:

```bash
.venv/bin/python -m benchmarks.resource_envelope_runner --validate-only
```

## Preregistration and planning

The master seed is `20260723`. A paired trial seed is the first unsigned
32 bits of `SHA256("v3|20260723|<workload_id>|<repeat_index>")`.

The same workload/repeat seed is used across all profiles and methods. Workload
order is deterministically shuffled per repeat. Profiles and methods use Latin
rotations.

`make v3-dry-run` safely previews all matrices without allocating pressure
memory or accessing Kubernetes. It must report 24 calibration trials, 120
direct-pod ground-truth trials, 120 comparative trials, and 45 JupyterHub
sentinel trials.

Before execution, freeze and hash the clean Git commit, this document, the
manifest, analysis implementation, immutable image, Helm chart 4.0.0, profile
table, and matrix.

## Cluster safety preflight

The v3 runners require the exact disposable context
`intent-spawner-eval-v3`. The namespace must be `z2jh-context-demo` and carry:

```text
z2jh-context-demo.local/disposable-experiment-v3=true
```

Execution refuses to start unless:

- the tracked Git tree is clean;
- the image is specified by digest and is pre-pulled;
- one node is Ready with no Memory, Disk, or PID pressure;
- allocatable resources are 6 CPU and 6088560Ki memory;
- at least 5 GiB remains for local append-only evidence;
- Metrics API is available;
- no ResourceQuota is installed;
- the direct-pod phase starts with an empty namespace;
- the JupyterHub phase starts with no single-user servers.

Every direct pod uses `restartPolicy: Never`, no service-account token,
read-only root, a 64 MiB `/tmp`, dropped Linux capabilities, and
`activeDeadlineSeconds = workload deadline + 30`. Trials are strictly
sequential. This protocol has no capacity, concurrency, quota, or load-wave
phase.

Execution is deliberately explicit and cluster-mutating:

```bash
.venv/bin/python -m cluster_evaluation.runner_v3 \
  --kind calibration \
  --experiment-id <immutable-id> \
  --image <repository>@sha256:<digest> \
  --execute
```

Ground-truth execution additionally requires
`--calibration-evidence <passing-directory>`. Comparative execution requires
both that option and `--ground-truth-evidence <complete-directory>`. The
JupyterHub harness requires all three completed direct-pod evidence
directories. These are implementation-enforced prerequisites, not new
scientific decisions.

Do not run that command on a shared or production context.

## Calibration gate

Calibration is independent of recommendation output and excluded from all
method statistics. Each required profile condition has three repeats.

The gate requires:

- Small calibration: Small succeeds 3/3 with a 315–335 MiB peak;
- Small/Medium boundary: Small OOMs 3/3 and Medium succeeds 3/3 with an
  820–880 MiB peak;
- Medium/Large boundary: Medium OOMs 3/3 and Large succeeds 3/3 with a
  1600–1700 MiB peak;
- CPU calibration: all profiles succeed 3/3 with at least 200 samples and
  Medium median runtime is 30–45 seconds.

Only one global target-controller/work-unit adjustment is allowed. If the
second calibration directory fails, no hold-out run is permitted.

## Direct-pod ground truth and comparative evaluation

Ground truth forces each hold-out through Small, Medium, and Large without
calling the recommender. A profile is reliable only if all five valid trials
succeed with no OOM, timeout, restart, or checksum error. The lowest reliable
profile is the operational minimum.

A larger reliable profile is utility-acceptable only when it improves median
time-to-outcome by at least 20% or has median memory-request waste below 50%.
Manifest expectations are never inputs to this derivation.

The comparative matrix then applies `static_default`, `intent_only`, and
`context_aware`. `h01`–`h06` form the primary stratum. `h07` and `h08` remain
separate robustness cases.

## JupyterHub end-to-end evaluation

`helm/experiment-v3-values.yaml` is isolated experiment configuration. It uses
one allowlisted method field so all methods share one deployment. Static
applies Medium, intent-only discards declared size and code context, and
context-aware uses all permitted synthetic inputs.

The pre-spawn hook writes only method, recommended/applied profile, synthetic
run ID, and reasons to allowlisted annotations/environment. Raw intent and raw
code context are not copied to the pod.

Build `cluster_evaluation/Dockerfile.jupyter-v3`, replace the placeholder image
with its immutable local digest, and preregister that digest. The harness uses
an admin API token from `JUPYTERHUB_API_TOKEN`; it never writes the token. It
spawns a hashed synthetic identity, verifies pod resources, executes the
workload inside the single-user container, stops the server, waits for pod
deletion, and deletes the synthetic Hub user.

This path validates spawn/resource fidelity. Because workload launch uses
controlled `kubectl exec`, it is not evidence about interactive notebook
behavior or user experience.

## Metrics and raw evidence

Primary metric sources are:

- outcome and OOM: container termination status and sanitized pod events;
- memory peak: cgroup-v2 `memory.peak`; sampled `memory.current` is a labeled
  fallback only;
- CPU average/sample maximum and throttling: `cpu.stat` deltas;
- benchmark runtime: in-process monotonic clock;
- pending/time-to-outcome: Kubernetes timestamps/controller clock;
- JupyterHub spawn latency: Hub request to Ready pod.

Metrics Server snapshots are secondary and are never called precise peaks.
Missing metrics stay null.

Schema `cluster_evaluation/result_schema_v3.py` requires method inputs as
derived summaries, exact resources, target/actual cgroup fields, CPU
throttling, timing, outcome, exclusion, replacement, cleanup, and relative
evidence paths. The implementation audit strengthened the schema to require
the full Git commit, immutable image reference, explicit failure category,
safe input hash, JupyterHub configuration identity, and supporting-evidence
hashes in every applicable record. Each phase writes append-only matrix/results streams,
per-trial sidecars, an environment record, and final `SHA256SUMS`.

No raw notebook, real username, token, credential, broad environment, full
cluster dump, host path, or user dataset is retained.

## Exclusions and stopping

OOM, timeout, nonzero workload exit, and a wrong recommendation are valid
outcomes. Infrastructure exclusion is limited to node loss/eviction, image or
mount failure, API/admission failure before scheduling, or evidence-writer
failure. One same-seed replacement is planned at the end; its failure stops the
phase. Missing memory peak excludes only memory-specific analysis.

Stop immediately for context/namespace/label mismatch, node pressure,
allocatable drift, less than 5 GiB evidence storage, unexpected namespace
resources, node-level eviction during a Large trial, cleanup failure, checksum
mismatch, allocation/deadline violation, three consecutive infrastructure
failures, or more than 10% infrastructure-invalid trials after ten attempts.
Expected container OOM at an undersized forced profile is not a stop.

## Statistical analysis and uncertainty

The workload is the generalization unit; repeats are stability measurements.
Primary estimands are paired differences in success and OOM, under/over profile
steps, and exact-minimum-profile rate. Secondary metrics are waste, runtime,
throttling, utility acceptability, and direct/end-to-end concordance.

The analysis reports counts, denominators, medians, Wilson 95% intervals, and
10,000 workload-cluster bootstrap samples. Fixed-suite tests are supplementary.
Robustness cases are never folded into the headline comparison. Missing values
are not imputed.

At ICC 0.5, 40 repeated observations have effective sample size near 13. The
suite is powered only for approximately 45–55 percentage-point effects and is
not a production-population efficacy study. Three end-to-end repeats are
replication/fidelity evidence, not powered inference.

Analysis is run only after a passing calibration gate:

```bash
.venv/bin/python -m cluster_evaluation.analyze_v3 \
  --calibration <calibration-directory> \
  --ground-truth <ground-truth-directory> \
  --comparative <comparative-directory> \
  --end-to-end <optional-jupyterhub-directory> \
  --out <new-empty-output-directory>
```

`cluster_evaluation/evidence_v3.py` must validate every source directory before
the full analysis. The analysis records source integrity identities and emits
its own `SHA256SUMS`.

## Cost and claim boundary

The planned maximum is 24 calibration, 120 ground-truth, 120 comparative, and
45 JupyterHub trials. One additional calibration round may add 24 trials.
Expected cluster/operator time is 5–8 hours. Only one workload is active; its
maximum limit is 2 CPU and 2G memory. Raw evidence should remain below 1 GiB.

If all gates pass, evidence may support reproducible synthetic OOM boundaries,
fixed-suite method comparisons, noisy-input failure modes, CPU-limit behavior,
and JupyterHub resource-application fidelity on the pinned single-node
environment.

It cannot establish production OOM reduction, real-user or real-notebook
effectiveness, dataset-hint accuracy in the wild, multi-user density,
autoscaling, storage/network behavior, user satisfaction, GPU/history-aware
behavior, security/fairness, continuous CPU peaks, or SLA reliability. Pressure
padding must never be described as a real dataset size.
