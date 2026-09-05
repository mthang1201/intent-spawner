# Protocol-v5 Independent Resource-Envelope Calibration

Protocol `5.0.0`, experiment `E4`. The harness is implemented; no real E4
Kubernetes measurement has been performed in this repository.

## Scope and independence

E4 estimates a CPU/memory reference without asking B0, P1, P2, or P3 for an
answer. Calibration modules do not import or call recommendation, retrieval,
ranking, or reranking code. The frozen P1/P2/catalog/index snapshot is copied
into provenance only so later evidence can be identified. P3 remains absent
because its frozen development gate is `not_retained`.

For a manifest entry `w`, `R*_w` applies only to its frozen canonical executable
instance: operation implementation, scale parameters, seed/generated input,
correctness invariants, timeout, and workload fingerprint. `family_id` names a
semantic design family; an envelope for the canonical dense-matrix instance,
for example, is not a universal claim about every dense-matrix workload or
scale.

The 16-family design assertion is frozen in
`benchmarks_v5/resource-envelope-semantic-independence-v1.yaml`. It records each
kernel, representation, dominant resource mechanism, algorithmic operation,
distinctness rationale, and overlap limitation. Its schema enforces exact
coverage of the workload manifest. Semantic independence is a design/review
assertion, not a statistical conclusion from repeated executions.

Protocol-v3 infrastructure and historical
`benchmarks/observed_resource_envelopes.yaml` remain historical/formative.
Protocol-v5 never overwrites or reinterprets them as E4 observations.

## Pilot and confirmatory separation

Before confirmatory freeze, formative pilot runs may select bounded scale,
input sizes, correctness feasibility, timeout feasibility, and whether an
instance lies trivially outside the lattice. Pilot rows must use a separate
location and evidence role. They may not be merged into confirmatory E4,
reported as final envelopes, or used as repeat replacements.

Before the first observed E4 trial, the freeze contract requires a clean,
tracked Git revision and freezes workload code, manifest/scale/seeds,
correctness markers and invariant checker, lattices, timeout, safe and reference
stability rules, schemas, semantic assertion, eligibility policy, comparison
contract/crosswalk, and the built/verified image digest. After activation,
observed outcomes must not tune any frozen component. The repository's
`resource-envelope-freeze-contract-v1.yaml` deliberately remains
`NOT_FROZEN` during development; execution fails closed until a clean committed
revision activates it independently of E4 outcomes.

## Correctness

Inputs are deterministic, synthetic, in-memory, and not retained. Workloads do
useful family-specific work and do not allocate padding toward a cgroup target.
Each result is checked in two ways:

1. canonical output SHA-256 equals the frozen marker; and
2. an independent frozen invariant payload equals the observed semantic
   quantities (counts, sums, dimensions, traversal cardinality, seeded sample
   aggregate, or lossless round-trip digest as appropriate).

Hashing an arbitrary just-produced value is therefore insufficient. A trial
with either check false is a workload failure, not an infrastructure failure.

## Search, stability, and boundary meaning

Memory candidates are `64, 96, 128, 192, 256, 384, 512, 768, 1024, 1280,
1536, 1792, 2048 MiB`; CPU candidates are `100, 200, 300, 500, 750, 1000,
1500, 2000m`. Hard bounds are 2 CPU, 2 GiB, 120 seconds, cgroup v2, and one
active E4 workload pod.

For each frozen instance the runner performs three 2000m/2048Mi reference
runs, memory bisection at 2000m, CPU bisection conditional on selected safe
memory, and five unchanged joint verification runs. Every probe has two valid
repeats. The maximum candidate is tested explicitly. The selected accepted
candidate and its immediately lower lattice neighbor are explicitly tested
when that neighbor exists.

Before any probe tolerance is applied, the reference runtimes must pass frozen
rule `max-relative-spread-v1.0.0`:

```text
(max(reference runtimes) - min(reference runtimes)) / median <= 0.20
```

This is a deterministic stability gate, not a hypothesis test. Failure emits
`REFERENCE_RUNTIME_UNSTABLE_REQUIRES_REVIEW` and prevents normal derivation.

A candidate passes only if all required valid repeats have: zero `oom` and
`oom_kill` deltas in cgroup-v2 `memory.events`; zero `oom_group_kill` when that
counter is exposed; no Kubernetes OOM kill; successful exit; marker and
invariant correctness; measured workload runtime no greater than the frozen
120-second boundary; required cgroup-v2 metrics; median runtime at most 125% of
the stable reference median; and every runtime at most 150% of it. Missing
required OOM counters, non-integer or negative deltas, and deltas that cannot be
computed because either the baseline or final sample is missing all fail closed.
The joint pair is safe only at 5/5 passes.

The pod `activeDeadlineSeconds` is 150 seconds: the 120-second measured-workload
boundary plus a separately recorded 30-second lifecycle allowance for container
startup and result/log collection. That allowance never expands the safe
runtime criterion. A completed workload measured above 120 seconds is recorded
as a timeout outcome and rejected. The adapter's separately recorded five-second
monitoring cushion only allows Kubernetes deadline propagation to be observed;
it also cannot affect workload safety.

Evidence reports the largest *tested* rejected point, smallest *tested*
accepted point, selected jointly verified safe point, and interval
`(tested reject, tested accept]`. It does not call those global extrema and
never claims an exact continuous minimum. If the lattice minimum passes, the
result is one-sided within the tested lattice. If the maximum fails, status is
`NO_SAFE_BOUND_WITHIN_SEARCH_SPACE`. An ordinary interval is forbidden without
selected-point and immediate-lower-neighbor evidence.

A lower accepted point together with a higher rejected point, or any other
contradictory tested pattern, emits
`NON_MONOTONIC_BOUNDARY_REQUIRES_REVIEW`, blocks eligibility, and is not repaired
by assuming monotonicity. Memory is conditional on generous CPU; CPU is
conditional on selected safe memory; only the final pair is jointly verified.

Infrastructure-invalid runs are excluded and receive at most one identical-seed
replacement. OOM, timeout, slow runtime, and incorrect output are workload
outcomes and are never silently replaced as infrastructure.

The Wilson 95% interval is descriptive metadata for identical-instance repeat
stability only. Repeats are not independent workload samples, and Wilson values
must not be generalized to a workload population. Primary E4 uncertainty is
the interval-censored resource boundary.

## Cluster and image gates

`resource-envelope-cluster-eligibility-v1.yaml` freezes fail-closed checks for
the exact context and cluster label, namespace, one labeled dedicated node,
node identity/isolation, health, Kubernetes/kubelet/runtime/kernel/architecture,
capacity/allocatable headroom, API access, quotas, conflicting E4 pods,
non-DaemonSet node workloads, and image pre-pull. A pre-trial eligibility pod
must confirm cgroup v2, controllers `cpu`, `memory`, and `pids`, all frozen
measurement files, and successful cleanup. Calibration trial 1 cannot start
until these checks pass. The probe also records the available `memory.events`
keys and requires `oom` and `oom_kill`, so an observed run cannot silently use a
cgroup interface that is unable to enforce the frozen no-OOM rule. The complete
observed key set is retained in environment provenance; absence of the optional
`oom_group_kill` key is therefore explicit and is never inferred to mean a zero
delta.

Image state distinguishes reference configured, digest syntax, build, resolved
digest verification, eligible-node pre-pull, and operational verification. A
digest-looking string alone is not verification. The checked-in image-state
record says `NOT_BUILT_OR_VERIFIED`; real execution requires a separately
frozen verified record matching the exact image.

Dry-run preflight uses read-only `kubectl config`, `get`, `version`, and
`auth can-i` operations only. It never creates the cgroup probe or a workload.
The current `orbstack` context remains ineligible and dry-run-only.

## Evidence, review, and retention

Packages contain root manifest, plan/provenance/environment facts, append-only
JSONL trials, an append-only adaptive decision ledger, trial sidecars, derived
envelopes, status/review, and `SHA256SUMS`. Each v1.1 trial record carries its
record schema and exact `workload_timeout_seconds`; Kubernetes evidence records
the separate `pod_lifecycle_grace_seconds` and `pod_active_deadline_seconds`.
Environment and Kubernetes evidence also identify the adapter monitoring grace.
Older Protocol-v5 trial rows lacking these fields or using a prior record schema
are rejected rather than upgraded implicitly.
Raw observations stay separate from derivation and interpretation. Resume is
permitted only for an existing unsealed partial package with an identical plan
fingerprint; sealed or completed packages refuse resume and overwrite.

Review states are:

- `NOT_APPLICABLE`: dry run with no empirical rows;
- `PENDING`: complete ordinary derivation still awaiting mandatory independent
  review;
- `REQUIRED`: an automated anomaly requires explicit adjudication;
- `APPROVED` or `REJECTED`: terminal exclusive-created attestation.

Legal transitions are `PENDING -> APPROVED|REJECTED` and
`REQUIRED -> APPROVED|REJECTED`. The attestation is pseudonymous, timestamped,
bound to a pre-review checksum fingerprint over plan/provenance/environment/raw
trials/derived envelopes, and included in final package sealing.

Checksum sealing and exclusive-create behavior provide application-level
immutability only. An ignored local results directory is not external immutable
storage. After a real approved run, copy the sealed directory and its checksum
manifest to the thesis evidence archive with versioned retention and read-only
or object-lock controls; record the archive URI/receipt outside the sealed
package. Never modify historical results in place.

## Downstream allocation comparison

`evaluation_v5/resource/comparison.py` consumes only sealed, approved observed
E4 evidence, separately frozen allocation JSON, and the frozen instance
crosswalk. It never invokes P1/P2/P3. Per axis:

- allocation `<=` tested rejected boundary is `EMPIRICALLY_INSUFFICIENT`;
- allocation `>=` tested accepted/verified reference is
  `EMPIRICALLY_SUPPORTED`;
- allocation strictly between boundaries (or below a one-sided accepted lowest
  point) is `INDETERMINATE_UNTESTED_INTERVAL`;
- ineligible, failed, non-monotonic, rejected, or no-safe evidence yields
  `NO_REFERENCE_AVAILABLE`.

CPU and RAM ratios, absolute/percentage excess, and joint categories are
reported separately. No arbitrary scalar combines the axes.

## Commands and dry-run limitation

```bash
make v5-resource-validate
make v5-resource-test
make v5-resource-dry-run
```

Real execution is intentionally explicit and will refuse the checked-in
development freeze/image state:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v5.resource execute \
  --result-dir results_v5/protocol-v5.0.0/E4/<run-id> \
  --run-id <run-id> \
  --image <repository>@sha256:<64-hex-digest>
```

A dry run has zero trials, `NOT_EXECUTED`, null hardware/cgroup trial
measurements, no derived envelope, and an empty Kubernetes-mutation list. It
supports planning/readiness claims only—never hardware performance or empirical
resource claims.

## Comparative resource efficiency layer

The independent calibration above is also the oracle boundary for the separate
Protocol-v5 comparative E4 layer. The layer does not define new thesis systems:
it applies `STATIC_LARGE`, `P1_CATALOG`, `P2_CATALOG`, and `P2_DYNAMIC`
allocation conditions to the same 16 immutable workload instances. Ten
executions per family/condition form 640 paired primary trials. P1 and P2 are
called once per family before execution; their decisions are reused across all
repetitions, and the same P2 result feeds both P2 conditions. P3 is excluded by
its authoritative `not_retained` gate.

The matrix identity is frozen as 16 families × 4 conditions × 10 repetitions =
640 primary trials. Family is the semantic independent unit (`N = 16`);
repetitions are nested run-variability observations. Within each repetition the
planner seed-shuffles families, then uses a balanced Latin rotation of the four
condition positions. Each condition occupies every within-family position four
times per repetition, and the rotation advances across repetitions. The order
is reproducible and no condition receives a fixed early, late, cold-cache, or
warm-cache position.

The checked-in comparative freeze and capacity contracts remain development
`NOT_FROZEN`. The read-only dry run therefore emits an immutable
`NOT_EXECUTED` package and makes no Kubernetes measurement. Real execution is
blocked until the independent calibration package is sealed and approved, the
image digest and dedicated disposable cluster are verified, the exact node
allocatable capacity is frozen, and the repository is clean.
The live path additionally requires the exact approved context, safety-labeled
namespace and node identity, no competing workload, a pre-pulled digest image,
matching node allocatable values, API access, and a successful cgroup-v2
telemetry probe and cleanup before any workload trial. The probe is permitted
only after all non-mutating readiness gates pass. Dry-run never runs it and
retains an empty Kubernetes mutation log.

The one-way analysis hierarchy is raw attempt → family/condition/repetition →
family/condition → paired cross-family inference. Infrastructure-invalid
attempts are kept as a separate evidence class; at most one identical
replacement is allowed. A repetition without a valid workload attempt makes
that family-condition estimate incomplete rather than silently shrinking its
denominator. Family-level paired bootstrap and tests resample the 16 families,
never the 640 primary trials.

CPU request-time is `cpu_request_m / 1000 × accounting_runtime_seconds` in
CPU-seconds. Memory request-time is `memory_request_mib ×
accounting_runtime_seconds` in MiB-seconds. CPUCostPerSuccess and
MemoryCostPerSuccess sum those request-times over every valid attempt—including
OOM, timeout, incorrect, runtime-error, and Pending/admission attempts—and
divide only by `SUCCESS` outcomes. Unscheduled attempts contribute the frozen
zero request-time accounting value; their Pending/admission rate remains a
Pareto reliability dimension. A scheduled attempt with missing duration makes
cost unavailable. Zero-success cells are null with `ZERO_SUCCESS`, never zero
or an efficiency win.

Pareto minimization dimensions are CPUCostPerSuccess, MemoryCostPerSuccess,
OOM rate, timeout rate, Pending/admission rate, and incorrect-completion rate.
Runtime-error rate is also minimized.
Maximization dimensions are success rate and correct-completion rate. Strict
improvement requires every dimension to be no worse and at least one to be
better. Lower request cost with worse reliability is
`EFFICIENCY_RELIABILITY_TRADEOFF`; unavailable cost or reliability is
`INDETERMINATE`. No post-hoc noninferiority margin exists in this contract.
OOM precedes timeout only when both independent signals are present; Pending,
admission, infrastructure, incorrect, and runtime-error evidence remain
distinct.

Every derived trial exposes canonical millicores, MiB, integer GPU counts,
request and limit fields, usage/request ratios, explicit missingness reasons,
and signed/absolute/percentage oracle errors. Error is allocation minus the
independent oracle-selected value; percentage uses that oracle value as its
denominator. Request errors are capacity comparisons and limit errors are
OOM/runtime-safety comparisons. P2_DYNAMIC lineage retains formula targets,
profile floors, upward quantization, policy validation, clipping status,
fallback reason, and final allocation. Generated-allocation uniqueness is
counted globally across generated P2_DYNAMIC families after quantization and
validation, using final canonical `(CPU request, CPU limit, memory request,
memory limit, GPU count, GPU resource)` identities; catalog fallbacks are not
included.

Capacity output uses only resource requests read back from all ten observed pod
specifications and the same frozen Kubernetes node-status `allocatable` CPU,
memory, and GPU values for every condition. Raw physical capacity and usage
metrics are forbidden scheduler inputs. Homogeneous density and the frozen
one-of-each-family first-fit-decreasing mix are always labeled
`SIMULATED_CAPACITY` and `SIMULATED_DETERMINISTIC_REQUEST_PACKING`; they are not
observed concurrent-cluster evidence and support no measured-throughput claim.

```bash
make v5-resource-efficiency-validate
make v5-resource-efficiency-test
make v5-resource-efficiency-dry-run
```

After independent review and freeze activation, decision generation and cluster
execution remain separate commands so every pod is bound to a sealed allocation
plan:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v5.resource.efficiency_runner plan \
  --result-dir results_v5/protocol-v5.0.0/E4/<plan-id>
PYTHONPATH=. .venv/bin/python -m evaluation_v5.resource.efficiency_runner execute \
  --plan-dir results_v5/protocol-v5.0.0/E4/<plan-id> \
  --result-dir results_v5/protocol-v5.0.0/E4/<raw-run-id> \
  --run-id <raw-run-id> --image <repository>@sha256:<64-hex-digest>
PYTHONPATH=. .venv/bin/python -m evaluation_v5.resource.efficiency_runner analyze \
  --raw-result results_v5/protocol-v5.0.0/E4/<raw-run-id> \
  --analysis-dir results_v5/protocol-v5.0.0/E4/<analysis-id> \
  --oracle <sealed-approved-independent-calibration-package>
```

`execute --resume` accepts only an unsealed crash prefix whose plan and stable
environment provenance hashes match. Sealed results, non-prefix attempts, and
changed plans or provenance are rejected.
