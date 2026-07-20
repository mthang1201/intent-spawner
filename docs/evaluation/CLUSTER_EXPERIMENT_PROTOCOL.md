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

## CPU and memory measurement semantics

Metrics Server snapshots are retained when its API reports a running pod.
Because its scrape cadence can miss short jobs, it is not treated as a precise
peak collector. Each benchmark container also samples its own cgroup-v2
`cpu.stat` and `memory.current` every 10ms and reads `memory.peak` before exit.
For the historical evaluated corpus, jobs shorter than one interval stored the
start-to-finish cgroup CPU delta in the legacy `peak_cpu_m` field. That value is
a full-window average: 202/288 records are affected. The immutable bytes remain
unchanged, while the schema-2 compatibility view exposes the values only as
`cpu_measurement_statistic="full_window_average"`. The other 86 values are
maxima over retained 10 ms interval-delta samples and are exposed as
`sample_maximum`, not continuous CPU peaks. No CPU-peak or CPU-waste claim is
derived from either class. Future records use `cpu_usage_m` plus explicit
statistic, interval, window, and source fields.
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

### Timing analysis rule 2.0.0

The 20% threshold above is unchanged. Rule 2.0.0 replaces the undocumented
post-hoc adequacy guard with a versioned rule declared before the capacity-v2
rerun. Kubernetes creation, scheduling, start, and finish timestamps have
one-second resolution in the retained corpus. A duration `d` is therefore
reported as the interval `[max(0, d - 1), d + 1)` seconds. Zero is a valid
observation and is not changed to 0.5 or any other offset. Missing observations
stay missing; negative or reversed timestamps fail validation.

For median comparisons, the analysis reports the median observed duration and
the medians of the lower and upper interval bounds. A candidate clears the
unchanged 20% branch only if its upper bound is at most 80% of the baseline
lower bound. If the intervals overlap at that threshold, the timing source
cannot distinguish the profiles. No smoothing, continuity correction, or
arbitrary minimum delta is used. Local monotonic benchmark runtimes remain a
separate higher-resolution diagnostic and are not substituted for Kubernetes
time to success.

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

## Historical capacity-pressure experiment

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
evaluated commit `39b6973`. Git history, branches, reflogs, ignored/untracked
files, targeted shell history, raw metadata, and unreachable Git objects were
searched without recovering code tied to the run. The historical result is
therefore non-reproducible supplementary evidence and is excluded from
principal claim support.

## Capacity protocol 2.0.0

This protocol and `cluster_evaluation.capacity_runner` must be committed in a
clean tree before execution. It fixes the following controls:

- one disposable Minikube profile/context named `intent-spawner-capacity-v2`;
- namespace `z2jh-context-demo`, labeled
  `z2jh-context-demo.local/disposable-capacity-v2=true`;
- one node with exactly 6 allocatable CPUs and 6088560Ki allocatable memory;
- the committed Small/Medium/Large request and limit table;
- all 12 manifest workloads launched concurrently once per batch;
- three repeats and the counterbalanced method orders recorded in environment
  metadata;
- a 20-second post-workload hold and 0.3-second phase sampling;
- `PodScheduled` conditions and namespace-scoped pod events as Pending-reason
  sources;
- exclusive-create raw files, per-pod logs/evidence/events, batch sidecars, Git
  and environment metadata, and exact-label cleanup after every batch. The
  environment record contains a sanitized Minikube profile, exact local image
  ID, image tag tied to the first 12 characters of the committed protocol, node
  capacity/allocatable resources, Kubernetes/runtime versions, and network
  configuration without host paths, node names, or machine identifiers.

The disposable environment is created with:

```bash
minikube start -p intent-spawner-capacity-v2 \
  --driver=docker \
  --kubernetes-version=v1.33.1 \
  --cpus=6 \
  --memory=6144mb \
  --disk-size=20g \
  --container-runtime=containerd \
  --extra-config=kubelet.system-reserved=cpu=2,memory=2Gi
kubectl --context intent-spawner-capacity-v2 create namespace z2jh-context-demo
kubectl --context intent-spawner-capacity-v2 label namespace z2jh-context-demo \
  z2jh-context-demo.local/disposable-capacity-v2=true
CAPACITY_SHORT="$(git rev-parse --short=12 HEAD)"
docker build -f cluster_evaluation/Dockerfile \
  -t "intent-spawner-cluster-eval:capacity-v2-${CAPACITY_SHORT}" .
minikube image load -p intent-spawner-capacity-v2 \
  "intent-spawner-cluster-eval:capacity-v2-${CAPACITY_SHORT}"
```

The non-mutating plan check and full reproducibility command are:

```bash
.venv/bin/python -m cluster_evaluation.capacity_runner \
  --experiment-id capacity-v2-preregistered \
  --image "intent-spawner-cluster-eval:capacity-v2-${CAPACITY_SHORT}" \
  --dry-run

.venv/bin/python -m cluster_evaluation.capacity_runner \
  --experiment-id capacity-v2-preregistered \
  --experiment-dir results/cluster/raw/capacity-v2-preregistered \
  --image "intent-spawner-cluster-eval:capacity-v2-${CAPACITY_SHORT}" \
  --repeats 3 --seed 20260721 --hold-seconds 20 \
  --sample-interval-seconds 0.3 \
  --expected-node-cpu 6 --expected-node-memory-ki 6088560
```

If interrupted, cleanup is restricted to the experiment label in the protected
namespace:

```bash
.venv/bin/python -m cluster_evaluation.capacity_runner \
  --experiment-id capacity-v2-preregistered \
  --image "intent-spawner-cluster-eval:capacity-v2-${CAPACITY_SHORT}" \
  --cleanup-only
minikube delete -p intent-spawner-capacity-v2
```

### Capacity-v2 execution record

The protocol was preregistered in `f759c45a3246916d2a9f9048ffaab17bbbea6982`.
An empty-cluster preflight then showed that Minikube records the documented
`--disk-size=20g` setting as 20,480 MiB, not 20,000 MiB. No experiment pod or
raw record existed at that point. The unit expectation was corrected and
committed in `ca2e74b2043a5ea85a68119097d6c325fe84c294` before execution.

The bounded smoke pod succeeded and was deleted. The full run used image
`intent-spawner-cluster-eval:capacity-v2-ca2e74b2043a` with local image ID
`sha256:bee0fc6942d2c9001053b1923d6ea23a2c34fb8735853ffc0ee806e5e5aede83`.
All 108 pods in all nine batches succeeded; every exact-label batch cleanup
succeeded. The validated raw directory is
`results/cluster/raw/capacity-v2-ca2e74b-seed20260721/`. After validation, the
named Minikube profile was deleted and the prior `orbstack` context restored;
the pre-existing `minikube` profile was not changed or deleted.

## Integrity and cleanup

Plans are written before execution. Run order and unsuccessful attempts are
preserved. Each run writes a new directory with `pod.log`, sanitized pod/event
evidence, Metrics Server snapshots, and a normalized record; existing files are
never overwritten. Raw notebook code, datasets, full pod objects, node names,
UIDs, secrets, and user identifiers are not stored.

Pods are deleted only after evidence collection. Cleanup status is part of the
record. The final teardown deletes only the named disposable Minikube profile;
no existing Minikube or OrbStack profile is modified or removed.
