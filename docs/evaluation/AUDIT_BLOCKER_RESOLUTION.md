# Audit Blocker Resolution

Resolution date: 2026-07-20 (Asia/Ho_Chi_Minh)

Exact corrected artifact commit: `6997e62a264e44362c32f79669afe9680fe319e6`

This report is added in the immediate child of that immutable artifact commit.
The report-bearing commit and authoritative `main` head are stated in the final
handoff because a Git commit cannot contain its own hash. All source, raw
evidence, derived outputs, and corrected claims described below are present at
the exact artifact commit above.

## Entry point and ancestry

Work started from final-audit commit
`0ffbd9a2bd0d5df58b2095db141a53851a1677fa` on
`codex/integrated-research-artifact`. The worktree was clean and `HEAD` matched
that commit exactly. `git merge-base --is-ancestor` confirms that the entry
commit, the integrated evaluation commits named by the historical audit, the
capacity preregistration commits, and the corrected artifact commit are on one
linear ancestry path. No parallel stale branch was used as an evidence source.

The two pre-execution capacity commits are:

- `f759c45a3246916d2a9f9048ffaab17bbbea6982`: schema/CPU correction,
  interval-censored timing rule 2.0.0, raw-integrity tooling, capacity protocol
  2.0.0, and the new runner;
- `ca2e74b2043a5ea85a68119097d6c325fe84c294`: the preflight-only correction
  from 20,000 to 20,480 MiB for Minikube's recorded `--disk-size=20g` value.

The second commit was created after inspecting an empty disposable cluster and
before any experiment pod or capacity-v2 raw directory existed. The recorded
run identifies `ca2e74b2043a5ea85a68119097d6c325fe84c294` as its clean source.

## Raw-evidence preservation

No pre-existing raw file was edited or deleted. A file-by-file SHA-256 baseline
was captured before blocker work in
`RAW_EVIDENCE_SHA256SUMS.before-0ffbd9a.txt`:

- baseline files: 1,541;
- baseline manifest SHA-256:
  `c6e094430a3e1ed7cfb27bb32c55bdd9208f08108b3828e7ed493dc67e75cdb9`.

The current `RAW_EVIDENCE_SHA256SUMS.txt` contains every baseline entry plus the
336 newly committed capacity-v2 files:

- current files: 1,877;
- current manifest SHA-256:
  `fb3294ad1105b10b7fc384f09bd39f9b8f525be85aa5b90319feab5816361c4e`.

`python -m cluster_evaluation.raw_integrity` verifies the current manifest as
an exact match to all tracked raw paths and independently verifies the baseline
as an immutable subset. Both checks pass at the corrected artifact commit.

## CPU blocker

The raw Kubernetes schema-1 field `peak_cpu_m` was not rewritten. A versioned
compatibility view now exposes what was actually measured:

| Reconciled statistic | Records | Permitted interpretation |
| --- | ---: | --- |
| Genuine cgroup CPU peak | 0 | No peak claim is available |
| Full-window cgroup CPU average | 202 | Average over the recorded container window |
| Maximum of 10 ms interval-delta samples | 86 | Sample maximum, not a continuous peak |
| Unavailable | 0 | Explicitly unavailable |

Future local and cluster records use schema 2.0.0 fields
`cpu_usage_m`, `cpu_measurement_statistic`, sampling interval, measurement
window, and source. Analysis tables and prose no longer call the 202 averages
peaks and make no CPU-peak or CPU-waste claim from either historical class.
Tests cover unsampled averages, schema migration without input mutation, and
the 202/86/0 reconciliation.

## Timing blocker

The post-hoc minimum-delta/continuity-style adjustment was removed. Timing rule
2.0.0 is fixed in `cluster_evaluation/timing.py`:

- Kubernetes timestamp resolution is one second;
- an observed non-negative duration `d` is represented as
  `[max(0, d - 1), d + 1)` seconds;
- zero remains a valid observation;
- missing remains missing;
- negative or inconsistent source timestamps are rejected;
- no offset, smoothing, imputation, or arbitrary guard is added;
- the original 20% improvement threshold is unchanged, but a larger profile
  clears it only when its complete upper interval is at least 20% below the
  baseline lower interval.

The regenerated method medians are all 1 second with the same `[0, 2)` interval.
They are therefore indistinguishable at the available resolution and no timing
advantage is claimed. Diagnostic acceptable-profile counts are 5/60 for
static-default, 30/60 for intent-only, and 20/60 for context-aware. These are
synthetic-suite diagnostics, not confirmatory production accuracy.

Tests cover zero, missing, negative, inconsistent timestamp order, quantized
one-second comparisons, clearly separated intervals, and deterministic envelope
regeneration.

## Capacity blocker

### Recovery attempt

The exact historical capacity generator tied to evaluated commit `39b6973` was
not recovered. The search covered all Git refs and history, branch/reflog
reachability, ignored and untracked repository files, raw metadata and command
records, targeted shell history, and unreachable Git objects reported by
`git fsck`. The only relevant unreachable source was an earlier analysis
variant, not the batch runner that produced the retained corpus.

The historical plan, outcomes, and sidecars remain byte-for-byte preserved and
are labeled `supplementary_historical_runner_unavailable`. They are excluded
from principal claim support; missing provenance was not backfilled.

### Replacement protocol and observed run

Protocol 2.0.0 created only the disposable Minikube profile/context
`intent-spawner-capacity-v2` and namespace `z2jh-context-demo`. Preflight
required the namespace safety label, one node, 6 allocatable CPUs,
6088560Ki allocatable memory, no ResourceQuota, the committed profile table, a
clean tracked worktree, a commit-derived image tag, and the exact local image
ID.

The sanitized environment records Kubernetes v1.33.1, containerd 1.7.27,
Docker driver, 6 CPUs, 6144 MiB configured memory, 20 GiB disk, kubelet
`system-reserved=cpu=2,memory=2Gi`, and image:

`sha256:bee0fc6942d2c9001053b1923d6ea23a2c34fb8735853ffc0ee806e5e5aede83`

A bounded non-root smoke pod completed with exit code 0 and zero restarts, then
was deleted. The full experiment launched all 12 workloads concurrently per
batch, for three counterbalanced repeats and nine batches, using a 20-second
post-workload hold and 0.3-second phase sampling.

Observed and validated outcomes:

| Method | Batches | Pods completed | Pod failures | Median maximum Running | Pods with FailedScheduling evidence | Median affected Pending |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Static-default | 3 | 36 | 0 | 7 | 15 | 22 s |
| Intent-only | 3 | 36 | 0 | 10 | 6 | 22 s |
| Context-aware | 3 | 36 | 0 | 7 | 14 | 22 s |

All nine exact-label cleanups succeeded. These results describe request
reservation and scheduler waves for this fixed synthetic workload population
on one disposable node. They do not establish production density, utilization,
real-user benefit, or general superiority.

The raw corpus is
`results/cluster/raw/capacity-v2-ca2e74b-seed20260721/`. The prior capacity
corpus remains separately derived and labeled supplementary.

## Cleanup and external-state safety

After evidence validation, the namespace contained zero remaining pods. The
new profile was removed with
`minikube delete -p intent-spawner-capacity-v2`, and the prior `orbstack`
context was restored. The pre-existing user profile named `minikube` remained
present and was not changed or deleted. No shared or production cluster was
used for mutations.

## Claim corrections

The corrected artifact now makes only the following scoped statements:

- memory peaks are cgroup-v2 `memory.peak` measurements;
- CPU records comprise 202 full-window averages and 86 interval sample maxima,
  with zero genuine continuous peaks;
- method-level time to success is indistinguishable at one-second resolution;
- no OOM benefit is demonstrated because the evaluated matrices observed zero
  OOMs;
- capacity-v2 shows controlled request-reservation differences for the fixed
  single-node protocol, not production density;
- the historical capacity result is supplementary and non-reproducible from
  its evaluated commit;
- no history-aware, GPU, real-user, or production effectiveness result exists.

`docs/evaluation/CLUSTER_RESULTS.md`, `README.md`, the schema/protocol,
provenance, threats-to-validity, roadmap, and artifact manifest carry the same
boundaries.

## Verification record

All required checks passed after the new evidence and claim regeneration:

- Python/unit/smoke tests: 70 passed, 0 failed;
- repository check script: 9 passed, 0 failed, 2 intentional skips;
- formal schema and migration checks: passed in the test suite;
- preserved cluster artifact reconciliation: 108 ground-truth records, 180
  comparative records, 9 capacity-v2 batches/108 pods, 0 run failures, 0 OOMs,
  and 0 cleanup failures;
- raw integrity: 1,877 current files and all 1,541 baseline files passed;
- capacity dry-run: 108 pods, 9 batches, 12 workloads per batch, 3 repeats;
- Python compilation and shell syntax: passed;
- Helm 4.0.0 baseline and proposed renders: passed;
- Kubernetes client dry-run for both pod manifests and the ResourceQuota:
  passed;
- live disposable-cluster smoke pod: passed and cleaned up;
- full local and cluster analysis/figure regeneration: two consecutive passes
  produced byte-identical SHA-256 manifests.

The two skipped repository checks are deliberate: optional read-only inspection
of the unrelated current cluster and execution of cluster-mutating demo scripts.
The separately authorized disposable smoke and full capacity-v2 execution
provide the live cluster path for this resolution.
