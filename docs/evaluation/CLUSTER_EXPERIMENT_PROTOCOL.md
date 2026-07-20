# Kubernetes Cluster Experiment Protocol

## Evidence boundary

This protocol governs the real Kubernetes-backed evaluation. It is separate
from the preserved local synthetic matrix under `experiments/raw/` and
`results/`. Local process results are not cluster operational evidence.

The evaluated Git commit must be clean before a matrix starts. Every pod is
created in `z2jh-context-demo` on the disposable context
`intent-spawner-eval`. The runner refuses any other current context.

## Disposable environment and mutations

The experiment uses a named Minikube Docker-driver cluster, not the pre-existing
`orbstack` context. The exact setup mutations are:

```bash
minikube start -p intent-spawner-eval \
  --driver=docker \
  --kubernetes-version=v1.33.1 \
  --cpus=6 \
  --memory=6144mb \
  --disk-size=20g \
  --container-runtime=containerd \
  --extra-config=kubelet.system-reserved=cpu=2,memory=2Gi
minikube addons enable metrics-server -p intent-spawner-eval
kubectl create namespace z2jh-context-demo
```

The first cluster creation without `kubelet.system-reserved` was deleted before
any experiment run because kubelet advertised the 8-CPU host rather than the
6-CPU container constraint. The replacement advertises 6 CPUs and 6088560Ki
allocatable memory. Metrics Server is pinned by Minikube 1.36.0 to
`registry.k8s.io/metrics-server/metrics-server:v0.7.2@sha256:ffcb2bf004d6aa0a17d90e0247cf94f2865c8901dcab4427034c341951c239f9`.

A disposable `metrics-probe` pod was created with a 250m CPU limit, observed by
`kubectl top pod --containers` at 251m, and deleted before the matrix. This
verified real pod metrics. No security setting was weakened on the existing
OrbStack cluster.

## Profile enforcement

The applied profile changes the pod's first-container requests and limits:

| Profile | CPU request | CPU limit | Memory request | Memory limit |
| --- | ---: | ---: | ---: | ---: |
| small | 100m | 500m | 256M | 384M |
| medium | 500m | 1 | 768M | 1G |
| large | 1500m | 2 | 1536M | 2G |

Every normalized record is checked against the sanitized pod evidence. A label
without matching resources is not an evaluated application of a profile.

## Peak measurement

Metrics Server snapshots are retained when its API reports a running pod.
Because its scrape cadence can miss short jobs, it is not treated as a precise
peak collector. Each benchmark container also samples its own cgroup-v2
`cpu.stat` and `memory.current` every 10ms and reads `memory.peak` before exit.
For the historical evaluated corpus, jobs shorter than one interval stored the
start-to-finish cgroup CPU delta in `peak_cpu_m`. The final audit determined
that this is an average, not a peak: 202/288 records are affected and their CPU
peak must be treated as missing. Current code stores that observation as
`full_window_average_cpu_m` and leaves `peak_cpu_m` null unless at least one
periodic sample exists.
The cgroup is the Kubernetes container's resource-accounting boundary. Records
identify the source and preserve nulls if these files are unavailable.

Memory reservation waste is preregistered as:

```text
max(0, (memory_request_mi - cgroup_peak_memory_mi) / memory_request_mi)
```

Request-to-peak ratios and requested-versus-peak values are also retained.
Limits are not substituted for missing peaks.

## Independent ground truth

The ground-truth sweep forces every workload under Small, Medium, and Large.
It never calls the recommender. Each workload/profile combination runs three
times with deterministic seeds and randomized order. A profile is reliable only
if all three runs succeed without OOM, timeout, restart, or cleanup failure.

The smallest reliable profile is the independently grounded minimum. A larger
profile remains in the acceptable set only if it is reliable and either:

- improves median time-to-success by at least 20% relative to the smallest
  reliable profile; or
- has median memory reservation waste below 50%.

Other successful larger profiles are classified as over-reserved. These rules
are fixed before the sweep is inspected. The manifest's
`expected_acceptable_profiles` and explanatory threshold text are never inputs
to this derivation. Each observed envelope lists all supporting run IDs and
flags the pre-existing expectation as not operationally grounded.

### Final-audit measurement correction

The preregistered 20% threshold above was not changed. The final audit found
that Kubernetes creation and termination timestamps in the retained records
have one-second resolution. Five short-workload acceptances were caused by
`1.0` versus `0.0` second medians and were not measurement-valid. The corrected
analysis requires an observed time-to-success difference greater than two
seconds before the time branch can accept a larger profile, accounting for two
independently quantized endpoints. This adequacy guard was added after results
were observed, is recorded as such, and must not be presented as preregistered.
The raw records are unchanged; per-profile output records the guard and
acceptance basis.

## Fair comparative methods

The operational baseline is `static_default`, not `static_manual`. It applies
Medium to every workload, subject only to permitted policy fields. It represents
a deployment configured with one moderate default profile. It cannot receive
intent, dataset-size hints, code context, or observed ground truth.

`intent_only` receives the intent and permitted policy fields. Its recommender
call fixes dataset size to zero and code context to empty. `context_aware`
receives intent, the declared dataset-size hint, derived code-context signals,
and the same policy fields. Tests mutate hidden inputs to verify isolation.

All three methods use the same workload implementation, image, cluster,
namespace, timeout, profile table, and deterministic seed for paired repeats.
The full 12-workload matrix uses five repeats and a seeded randomized order.
The historical `static_manual` local result is retained only as synthetic
evidence; because it reads expected acceptable profiles, it is an oracle-style
policy and is excluded from the cluster comparison.

## Capacity-pressure experiment

The separately reported capacity experiment launches the same population of 12
validated workloads for each method. All pods use the same image and benchmark
commands; only applied profile resources differ. A 20-second post-workload hold
keeps request reservations present long enough to observe scheduler waves. The
hold is excluded from benchmark runtime and is used only for request-based
density, Pending, and makespan analysis.

The experiment uses the fixed 6-CPU/6088560Ki node allocation, the same
namespace, no ResourceQuota, and unchanged admission configuration for every
method. It runs three repeats in counterbalanced method order. Reports must
distinguish request reservation from cgroup utilization and must list every
Pending reason. Density is not inferred from profile labels.

The historical capacity plan, batch records, per-pod outcomes, and environment
record are retained, but the exact batch-generator source is absent from
evaluated commit `39b6973`. Consequently the observations can describe this
controlled run but cannot pass an exact end-to-end reproduction gate or support
a general cluster-density claim.

## Integrity and cleanup

Plans are written before execution. Run order and unsuccessful attempts are
preserved. Each run writes a new directory with `pod.log`, sanitized pod/event
evidence, Metrics Server snapshots, and a normalized record; existing files are
never overwritten. Raw notebook code, datasets, full pod objects, node names,
UIDs, secrets, and user identifiers are not stored.

Pods are deleted only after evidence collection. Cleanup status is part of the
record. The final teardown deletes only the named disposable Minikube profile
and restores the pre-existing `orbstack` context.
