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

A candidate passes only if all required valid repeats have: no OOM; successful
exit; marker and invariant correctness; no timeout; required cgroup-v2 metrics;
median runtime at most 125% of the stable reference median; and every runtime
at most 150% of it. The joint pair is safe only at 5/5 passes.

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
until these checks pass.

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
envelopes, status/review, and `SHA256SUMS`.
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
