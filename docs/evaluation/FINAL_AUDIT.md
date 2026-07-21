# Final Independent Research-Artifact Audit

Audit date: 2026-07-21 (Asia/Ho_Chi_Minh)

Exact audited input commit:
`1eeeee0cc7f358373ddf4875141946812048f078`

This report and the narrowly justified corrections found during the audit are
committed in a child of that input commit. A report cannot contain the hash of
the commit that contains itself; the report-bearing commit is recorded in the
final handoff. The verdict applies to that child after the corrections listed
under “Audit corrections,” while all empirical raw evidence remains unchanged.

## Overall verdict: Ready with conditions

The artifact is ready to defend as an explainable pre-spawn prototype with a
reproducible local synthetic evaluation and a scoped, reproducible,
Kubernetes-backed single-node evaluation. Neutral and negative findings are
methodologically acceptable: context-aware did not outperform intent-only, no
OOM reduction was observable, and intent-only produced the strongest result in
the tested environment.

The conditions are scope and publication conditions, not requirements that the
system outperform intent-only:

1. Treat the benchmark as synthetic and the Kubernetes evidence as a local
   single-node direct-pod experiment, not a production JupyterHub effectiveness
   or density study.
2. Publish the report-bearing child on the authoritative defense branch or
   archival release. At audit entry, `main` and fetched `origin/main` were
   identical at the exact audited input commit; this new report commit is local
   until explicitly pushed.
3. Preserve the corrected CPU semantics: 202 full-window averages, 86 legacy
   hybrid maxima, and zero genuine continuous CPU peaks.
4. Do not promote diagnostic acceptable-profile rates into generalized claims
   of OOM, waste, timing, context, history, GPU, or production benefit.

## Verdict by evidence class

| Evidence class | Verdict | Defensible boundary |
| --- | --- | --- |
| Prototype mechanics | **Ready** | A deterministic explainable recommendation, policy fallback, privacy-minimizing metadata, and profile-to-resource application exist and are tested. The Helm pre-spawn path is mechanics evidence, not an end-to-end effectiveness trial. |
| Synthetic evaluation | **Ready with conditions** | The 180-record local matrix, dry-run planning, immutable records, missing-value handling, and regeneration are reproducible. `static_manual` is an oracle-style deterministic comparator because it reads manifest expectations. |
| Kubernetes-backed evaluation | **Ready with conditions** | The 108-run ground-truth sweep, 180-run comparison, and principal 108-pod capacity-v2 corpus reconcile. They cover one ARM64 Minikube node, short synthetic jobs, direct pod creation, and one fixed profile table. |
| Reproducibility | **Ready with conditions** | Clean setup/check/smoke/regeneration and isolated live reruns passed. The report-bearing commit still needs publication, and live Kubernetes reproduction requires Docker, Minikube, and sufficient local resources. |

## Entry state, ancestry, branch, and remote

### Mandated command record

The following commands were run before the audit proceeded:

```bash
git status
git rev-parse HEAD
git branch -vv
git log --graph --decorate --oneline --all --date-order -50
```

The captured entry state was:

- branch: `main`;
- tracked tree: no tracked modifications;
- `HEAD`: `1eeeee0cc7f358373ddf4875141946812048f078`;
- tracking state before and after `git fetch --prune origin`: `main` at
  `1eeeee0`, tracking `origin/main` at the same commit;
- fetched divergence: `git rev-list --left-right --count HEAD...origin/main`
  returned `0 0`;
- remote: `https://github.com/mthang1201/intent-spawner.git` for fetch and
  push;
- entry status also contained 48 untracked duplicate-copy files whose names
  ended in ` 2`. Every file had been verified byte-identical to its tracked
  counterpart at entry. Four duplicate `__pycache__` files were later created
  when pytest collected the stale test copies. The 52 files were moved without
  deletion, preserving directory structure, to
  `/tmp/intent-spawner-entry-duplicate-backup-20260721`. This removed stale test
  discovery and permits a clean final worktree.

The relevant `git branch -vv` record was:

```text
  codex/comparable-evaluation-runner 6948ee4 experiments: add comparable evaluation runner
  codex/evaluation-analysis-run      6c19a1a [origin/main: ahead 5, behind 26] results: add reproducible local evaluation analysis
  codex/experiment-instrumentation   d6f8d71 experiments: add immutable result instrumentation
  codex/final-audit                  266ac36 [origin/main: behind 22] audit: verify thesis claims and reproducibility
  codex/integrated-research-artifact 1eeeee0 docs: record final audit blocker resolution
  codex/reproducibility-artifact     a965ab6 [origin/main: behind 21] docs: package reproducible research artifact
* main                               1eeeee0 [origin/main] docs: record final audit blocker resolution
```

The date-ordered graph placed the required lineage on `main` in this order:

```text
1eeeee0 docs: record final audit blocker resolution
6997e62 audit: add reproducible capacity evidence and corrected results
ca2e74b audit: align capacity disk-size preflight units
f759c45 audit: preregister metric and capacity corrections
0ffbd9a audit: deliver final independent artifact verdict
92a9c25 audit: distinguish sampled CPU peak from job average
2d732a5 audit: correct cluster analysis and provenance gates
a14e86c analysis: chart queued pod pending time
67c6c18 analysis: handle zero pending-time plots
22d6253 results: add real Kubernetes comparative evaluation
39b6973 privacy: sanitize cluster environment provenance
9f6e326 experiments: separate source dirtiness from raw outputs
0243831 experiments: preserve clean preflight provenance
84e8d2c build: make evaluation image context reproducible
d7c17bb experiments: add Kubernetes-backed evaluation harness
6240333 docs: record branch integration audit
9c7aabf merge: integrate research artifact branches
```

### Required ancestry checks

`git merge-base --is-ancestor <commit> HEAD` passed for every required commit:

| Required work | Exact verified ancestor | Result |
| --- | --- | --- |
| Integrated research artifact | `9c7aabf69c25f3ca07260dbb4629d2a1ab986680` | Pass |
| Kubernetes-backed evaluation result | `22d625387b8477455cdda3ff8da6ba47c7caa494` | Pass |
| Previous final audit | `0ffbd9a2bd0d5df58b2095db141a53851a1677fa` | Pass |
| Capacity/metric preregistration | `f759c45a3246916d2a9f9048ffaab17bbbea6982` | Pass |
| Capacity preflight unit correction | `ca2e74b2043a5ea85a68119097d6c325fe84c294` | Pass |
| Corrected evidence artifact | `6997e62a264e44362c32f79669afe9680fe319e6` | Pass |
| Audit-blocker resolution report | `1eeeee0cc7f358373ddf4875141946812048f078` | Pass |

The stale sibling branches visible in the graph are not required ancestors;
`BRANCH_INTEGRATION_REPORT.md` maps them to integrated or superseding commits.
The wrong-tree stop condition was not triggered.

### Authoritative branch conclusion

Former remote blocker: **resolved for the audited input artifact**. A fresh
fetch showed local `main` and `origin/main` at the same required final
blocker-resolution commit, with zero divergence. The report-bearing child must
still be pushed or archived before defense; this is a publication condition,
not evidence that the audited input came from stale `origin/main`.

## Status of the four former blockers

| Former blocker | Status | Independent verification |
| --- | --- | --- |
| 1. Historical capacity runner reproducibility | **Resolved by the new-rerun path** | The historical `capacity-39b6973` corpus remains byte-preserved, explicitly supplementary, and excluded from principal claims. Runner/protocol commit `f759c45` and correction `ca2e74b` precede evidence commit `6997e62`; capacity-v2 environment metadata records clean commit `ca2e74b`, exact image ID, node allocation, method order, hold, sampling, and cleanup. The committed full corpus reconciles 9 batches/108 pods. |
| 2. Quantized timing-analysis methodology | **Resolved for current claims** | Rule 2.0.0 is versioned in code, uses `[max(0,d-1), d+1)`, keeps zero valid, preserves missing, rejects negative/inconsistent timestamps, adds no offset or smoothing, and requires non-overlapping threshold intervals. All method medians are `1 [0,2)` and are declared indistinguishable. |
| 3. Historical CPU averages mislabeled as peaks | **Resolved after one audit correction** | Raw schema-1 bytes remain unchanged. Code at evaluated commit `39b6973` proves 202 unsampled values are full-window averages. It also proves the other 86 values are the maximum of the interval-sample maximum and full-window average, not pure sample maxima. This audit corrects the compatibility layer, table, figure, schema prose, and tests to label those 86 as legacy hybrid maxima. Zero values are claimed as genuine continuous CPU peaks. |
| 4. Authoritative main branch and remote status | **Resolved for audited input; report publication pending** | After `git fetch --prune`, `HEAD`, local `main`, and `origin/main` were all `1eeeee0`; divergence was `0 0`. The new report-bearing child is intentionally not described as remote until pushed. |

## Audit corrections

Only three artifact corrections were justified:

1. Replace the inaccurate “sample maximum” compatibility label for 86 legacy
   CPU records with an explicit hybrid statistic derived from the evaluated
   schema-1 implementation, then regenerate the affected CSV/SVG and align the
   schema, protocol, limitations, roadmap, README, and tests.
2. Reject an out-of-repository capacity evidence directory before preflight or
   pod creation, because retained supporting paths are intentionally
   repository-relative. This converts a late live-run failure into an early,
   cluster-free validation error.
3. Add an exact derived-input map to the generated cluster report and correct
   its earlier overstatement that every aggregate CSV row directly carried all
   supporting run IDs.

No raw file, empirical outcome, acceptable-profile count, memory value, timing
value, capacity count, or claim direction was changed.

## Clean validation record

| Required validation | Result |
| --- | --- |
| Clean worktree/clone setup | Detached worktree at `1eeeee0`; `scripts/setup.sh` created an isolated Python 3.14.5 environment from pinned requirements. |
| Complete repository check | Final tracked tree: 9 passed, 0 failed, 2 intentional skips (optional read-only cluster inspection and cluster-mutating demo scripts). |
| All tests | 71 passed after corrections. The clean audited-input worktree had 70/70 before the new regression test was added. |
| Benchmark validation | Five-repeat full-matrix dry run planned 180 unique runs with zero execution attempts. |
| Smoke experiment | One local smoke run completed with zero timeout in the clean detached worktree. |
| Raw-to-derived regeneration | Local analysis regenerated 12 CSVs, four SVGs, and `RESULTS.md`; cluster analysis regenerated its CSV/SVG/envelope/report set. `git diff --exit-code` was clean before audit corrections, and repeated post-correction generation was stable. |
| Integrity validation | Current 1,877-file manifest and 1,541-file baseline passed; cluster plans, sidecars, resource mappings, outcomes, and cleanup reconciled. |
| Kubernetes-backed rerun | New clean-source 36-pod ground-truth sweep: 36 successes, 0 OOM, 0 timeout, 0 cleanup failure, 36/36 resource matches, 36/36 sidecar matches. |
| Capacity dry run | Protocol 2.0.0 planned 108 pods in nine counterbalanced batches. |
| Capacity safe live run | Documented in-repository path: 36/36 pods across three one-repeat batches, zero failure/cleanup residue, three matching batch sidecars, all 36 supporting-file sets present. |
| Cluster cleanup | Both audit-created Minikube profiles were deleted; each namespace had zero remaining pods; prior `orbstack` context was restored; the pre-existing `minikube` profile was not modified. |

## Raw-evidence integrity

### Immutable checksum chain

| Manifest | Files | Manifest SHA-256 | Verification |
| --- | ---: | --- | --- |
| `RAW_EVIDENCE_SHA256SUMS.before-0ffbd9a.txt` | 1,541 | `c6e094430a3e1ed7cfb27bb32c55bdd9208f08108b3828e7ed493dc67e75cdb9` | Every entry independently matched a `git archive` of commit `0ffbd9a`. |
| `RAW_EVIDENCE_SHA256SUMS.txt` | 1,877 | `fb3294ad1105b10b7fc384f09bd39f9b8f525be85aa5b90319feab5816361c4e` | Current manifest exactly matched all tracked raw paths. |

`git diff --name-status 0ffbd9a..1eeeee0 -- experiments/raw
results/cluster/raw` reported exactly 336 additions and no modifications or
deletions. Thus the pre-fix checksum set is unchanged; new capacity-v2 evidence
is stored under its own directory rather than replacing historical evidence.

### Evidence-class separation and provenance

| Evidence set | Raw input | Derived identification |
| --- | --- | --- |
| Local synthetic | `experiments/raw/20260719T140431Z-matrix-aed48949` | `docs/evaluation/RESULTS.md` names the exact JSONL, record count, environment, and commit, then lists every generated CSV/SVG. |
| Kubernetes ground truth | `results/cluster/raw/ground-truth-39b6973-seed20260720` | Ground-truth outcome table and observed envelopes retain run IDs and are mapped in `CLUSTER_RESULTS.md`. |
| Kubernetes comparative | `results/cluster/raw/comparative-39b6973-seed20260720` | Method, workload, memory, timing, and boundary tables retain run IDs; figures are explicitly mapped in `CLUSTER_RESULTS.md`. |
| Principal capacity-v2 | `results/cluster/raw/capacity-v2-ca2e74b-seed20260721` | Capacity table retains batch and run IDs; principal concurrency and Pending figures are mapped to this set only. |
| Historical capacity | `results/cluster/raw/capacity-39b6973-seed20260721` | Filenames and rows say `historical`/`supplementary_historical_runner_unavailable`; no principal figure reads this set. |

The generated cluster report now contains an explicit derived-input map. The
aggregate CPU reconciliation is traceable through the row-level
`cpu_measurements.csv`, rather than incorrectly claiming that every aggregate
row itself carries all run IDs.

### Excluded pilot and audit reruns

The excluded pilot remains visible at
`experiments/raw/cluster-pilot-ground-truth-9f6e326-unsanitized-env` in the local
audit environment and is named in the generated cluster report. It is ignored,
not used by analysis, and excluded because its environment retained unnecessary
machine identifiers. No pilot value appears in principal tables or figures.

New audit-validation evidence is segregated and is not principal evidence:

- successful 36-pod ground-truth rerun:
  `/tmp/intent-spawner-final-audit-ground-1eeeee0`;
- visible failed out-of-tree capacity attempt, cleaned after 12 pods and without
  a finalized batch record:
  `/tmp/intent-spawner-final-audit-capacity-1eeeee0`;
- successful one-repeat capacity live validation, preserved with repository
  layout under
  `/tmp/intent-spawner-final-audit-capacity-evidence/results/cluster/raw/final-audit-capacity-live-1eeeee0`.

These audit runs did not alter tracked raw evidence or derived empirical claims.

## Metric correctness

### Source, statistic, units, and missingness

| Principal metric | Statistic and unit | Source | Audit result |
| --- | --- | --- | --- |
| Local CPU | Unavailable | No CPU metric source in the preserved local matrix | Pass: nulls remain blank; no CPU values are imputed. |
| Local memory | Process peak RSS, MiB | Python `resource.getrusage` | Pass with scope: valid local-process peak, never relabeled as pod utilization. |
| Kubernetes CPU, 202 records | Full-window mean, millicores | Start-to-stop delta of cgroup-v2 `cpu.stat` | Pass after compatibility labeling; never called a peak. |
| Kubernetes CPU, 86 records | Legacy maximum of interval-sample maximum and full-window average, millicores | Evaluated schema-1 cgroup sampler at `39b6973` | Corrected in this audit; cannot be narrowed to a pure sample maximum. |
| Genuine continuous Kubernetes CPU peak | Unavailable, 0 records | No continuous peak collector | Pass: no peak/waste claim is made. |
| Kubernetes memory | Peak, MiB | In-container cgroup-v2 `memory.peak` | Pass: all 288 values are present and match the retained pod-log payloads exactly. |
| Kubernetes time to success/Pending | Quantized duration, seconds | Kubernetes pod timestamps | Pass: values are non-negative integer seconds; table/figure precision is one second with censoring intervals. |
| Capacity maximum Running | Maximum sampled count, pods | `kubectl get pods` phase samples every 0.3 seconds | Pass: labeled maximum of samples, not continuous utilization. |
| Capacity affected Pending | Median observed duration, seconds | `PodScheduled` timestamps plus `FailedScheduling` events | Pass: missing values are filtered only where explicitly stated; no zero replacement for missing. |

Independent raw/log checks found zero memory mismatches and zero legacy CPU
payload mismatches across all 288 ground/comparative records. Memory request and
limit units are normalized to MiB/millicores from the same committed profile
table; limits are never substituted for missing usage.

### Quantized timing rule

For each observed Kubernetes duration `d` at one-second timestamp resolution,
rule 2.0.0 reports `[max(0,d-1), d+1)`. In particular:

- `0` remains `0 [0,1)` and is not changed to 0.5 or 1;
- `1` becomes `1 [0,2)`;
- missing stays missing;
- negative values and reversed source timestamps fail validation;
- a candidate passes the 20% timing branch only if its complete upper interval
  is no more than 80% of the baseline lower interval.

The 288 historical pod records retain computed quantized durations but not the
full original creation/termination timestamp tuple. Their evaluated source code
and retained event/phase evidence establish the one-second source, while the
absence of every source timestamp prevents a stronger row-by-row timestamp
recalculation. The interval rule is therefore appropriately conservative, and
no timing advantage is claimed.

### Principal table and figure audit

- Local figures use the single named synthetic matrix; memory axes say observed
  peak memory and the report identifies `resource.getrusage`.
- `requested_vs_peak.svg` and `waste_comparison.svg` use cgroup memory peak and
  MiB/request-ratio units.
- `time_to_success_intervals.svg` displays `1s [0,2)` and states one-second
  timestamp resolution.
- `cpu_metric_reconciliation.svg` now separates full-window averages,
  unambiguous schema-2 sample maxima, legacy hybrid maxima, genuine peaks, and
  unavailable values.
- `capacity_concurrency.svg` and `pending_time.svg` use only principal
  capacity-v2 records; historical equivalents are visibly named supplementary.
- CSV missing fields remain empty/null. No principal table silently imputes a
  CPU value, memory peak, Pending time, OOM, or failure outcome.

## Capacity reproducibility: new-rerun path

The recovered-historical path was not used. The exact historical generator is
still unavailable, and its corpus is supplementary.

The replacement path satisfies the required ordering and controls:

1. `f759c45` committed runner, schema, timing rule, integrity tooling, and
   capacity protocol before the new run.
2. `ca2e74b` corrected only Minikube's documented 20 GiB/20,480 MiB preflight
   representation before evidence existed in Git.
3. `6997e62` first added the 336 capacity-v2 raw files and regenerated claims.
4. Raw `environment.json` records clean source commit `ca2e74b`, protocol 2.0.0,
   exact image ID
   `sha256:bee0fc6942d2c9001053b1923d6ea23a2c34fb8735853ffc0ee806e5e5aede83`,
   one 6-CPU/6088560Ki node, 12-pod population, three repeats, counterbalanced
   order, 20-second hold, 0.3-second sampling, no ResourceQuota, and successful
   label-scoped cleanup.
5. Principal capacity tables and claims read only capacity-v2; historical
   capacity has separate supplementary outputs.

The committed result is reproducible as a controlled request-reservation
experiment. It does not prove real utilization or production density.

One audit live attempt supplied an out-of-tree evidence directory and revealed
that supporting paths were serialized relative to the repository only after
the first batch ran. Cleanup succeeded, but the attempt failed before a batch
record was finalized. This audit adds an early check requiring the evidence
directory to be inside the repository, so the same misuse now fails before
preflight or pod creation. The documented path and a subsequent 36-pod live
validation passed.

## Claim-to-evidence matrix

| Claim | Direct evidence | Decision |
| --- | --- | --- |
| Explainable pre-spawn recommendation exists | Rule-based recommender, human-readable reasons, proposed Helm hook, tests | **Supported as prototype mechanics** |
| Profile decisions are applied to Kubernetes pod resources | Helm resource mapping tests; 288 committed direct-pod records; independent 36/36 rerun resource matches | **Supported, scoped to mechanics/direct pod runner** |
| Policy constraints are implemented | GPU-disallowed fallback, allowed profiles, profile bounds, policy warnings, tests | **Supported** |
| Privacy behavior is implemented | Raw intent/code minimization, annotation/environment allowlists, sanitized environment records, tests | **Supported for this implementation** |
| Experiment and analysis artifact is reproducible | Clean setup/check, immutable checksums, dry runs, deterministic regeneration, live reruns | **Supported with local-tooling condition** |
| Every tested workload completed reliably under Small | 36/36 Small ground-truth runs, three repeats for each of 12 workloads | **Supported for this benchmark** |
| Current benchmark demonstrates OOM reduction | Zero OOM in all 288 ground/comparative runs | **Unsupported; no effect can be estimated** |
| Context-aware outperformed intent-only | Acceptable counts 20/60 versus 30/60; equal timing interval; higher median waste | **Contradicted in this environment** |
| Context-aware may over-provision relative to observed envelopes | Context median waste `0.978759`; intent-only `0.957941`; several context Large decisions for small observed peaks | **Supported as a suite-specific observation** |
| Intent-only gave the strongest tested result | 30/60 acceptable versus context 20/60/static 5/60; lowest median waste; no reliability/timing penalty | **Supported only for this fixed environment and metrics** |
| Context-aware generally reduces waste | Current ordering is opposite and workload hints exceed implementations | **Unsupported** |
| Context-aware generally reduces OOM | No OOM occurred | **Unsupported** |
| Context-aware improves production density | One local node measures request-reservation waves, not production utilization | **Unsupported** |
| Context-aware is superior to intent-only | Current empirical ordering favors intent-only | **Unsupported** |
| History-aware recommendation is effective | No history method or longitudinal evaluation exists | **Unsupported** |
| GPU recommendation is effective | No GPU node or workload was executed | **Unsupported** |

## Supported current findings

1. All 12 workloads completed reliably under Small: 36/36 Small-profile
   ground-truth runs succeeded without OOM, timeout, restart, or cleanup failure.
2. No OOM reduction can be demonstrated because all 288 ground/comparative runs
   recorded zero OOMs.
3. Context-aware did not outperform intent-only: diagnostic acceptability is
   20/60 versus 30/60, while both have `1 [0,2)` median time to success.
4. Context-aware may over-provision relative to the observed synthetic workload
   envelopes: its median memory reservation waste is about 0.979 versus 0.958
   for intent-only.
5. Intent-only is the strongest method in the tested environment by acceptable
   count and median waste, with equal completion/OOM/timing outcomes.
6. Capacity-v2 shows request-driven scheduling waves on the fixed node: median
   maximum Running is 7 static-default, 10 intent-only, and 7 context-aware;
   FailedScheduling affects 15, 6, and 14 pods respectively, with 22-second
   median affected Pending for each method. This is not production density.

## Unsupported claims that must not appear in the thesis

- generalized reduction of OOM, failures, reruns, or restarts;
- generalized reduction of resource waste;
- production cluster-density or utilization improvement;
- superiority of context-aware over intent-only;
- fine-grained Kubernetes timing improvement;
- history-aware effectiveness;
- GPU scheduling or accelerator effectiveness;
- end-to-end JupyterHub user benefit;
- generalization to real notebooks, datasets, users, or multi-node clusters.

## Remaining limitations and conditions

- Workloads and inputs are synthetic; declared GB hints are recommendation
  signals rather than physically realized datasets.
- The Kubernetes experiment uses one disposable ARM64 Minikube node and direct
  pods, not a production or multi-user JupyterHub deployment.
- Metrics Server captured zero per-job samples for the short jobs. Memory peak
  remains valid through cgroup `memory.peak`; CPU does not have continuous peak
  telemetry.
- Original source timestamp tuples are not retained for the 288 historical
  ground/comparative durations. The conservative rule prevents overclaiming but
  cannot restore missing provenance.
- Acceptable-profile rates depend on fixed thresholds and the derived
  ground-truth envelope; they are diagnostic rather than universal accuracy.
- The local `static_manual` comparator is oracle-style and must not be presented
  as a fair operational baseline.
- The excluded unsanitized pilot must stay private/access-controlled and outside
  derived results.
- The repository still has no project software license; institutional approval
  is needed before broad redistribution.
- The final report-bearing commit must be pushed or included in the exact
  archival release used for defense.

## Strongest evidence

1. Exact Git ancestry and a fetched `0 0` local/remote divergence establish the
   authoritative audited input tree.
2. Independent checksum verification establishes an unchanged 1,541-file
   pre-fix corpus plus 336 separately added capacity-v2 files.
3. Artifact validation reconciles 108 ground-truth records, 180 comparative
   records, 9 principal capacity-v2 batches/108 pods, commits, sidecars,
   resources, outcomes, and cleanup.
4. All 288 Kubernetes memory peaks match the retained cgroup log payloads; all
   applied requests/limits match the committed profile table and pod evidence.
5. Clean setup/check, local smoke, 180-plan dry run, byte-stable local/cluster
   table and figure regeneration, a 36-pod Kubernetes sweep, and a 36-pod
   capacity live run all completed in this audit.

## Likely committee criticisms and evidence-based answers

1. **“Did context help?”** No. In this fixed suite, intent-only has 30/60
   acceptable runs and context-aware 20/60, with context also showing higher
   median memory waste. The contribution is explainable mechanics and honest
   evaluation, not claimed context superiority.
2. **“Where is the OOM benefit?”** There is none in this benchmark. Every
   evaluated method had zero OOMs, so OOM reduction is not estimable.
3. **“Is this a production density result?”** No. Capacity-v2 is reproducible
   evidence of request-reservation and scheduler waves on one fixed local node.
   It does not measure real-user utilization or production density.
4. **“Are the CPU peaks valid?”** No continuous CPU peak is claimed. There are
   202 full-window averages and 86 legacy hybrid maxima; memory, not CPU, has a
   genuine cgroup peak source.
5. **“Is the timing comparison precise enough?”** No fine-grained difference is
   resolvable. All method medians are `1 [0,2)` at one-second resolution, so the
   defensible finding is indistinguishability.
6. **“Can the result be reproduced from a fresh checkout?”** Yes for setup,
   tests, raw validation, regeneration, smoke, and planning. The principal
   capacity-v2 runner and protocol predate its evidence. Live reproduction also
   needs the documented local Docker/Minikube resources.
7. **“Is the baseline fair?”** The operational static baseline is fixed Medium
   and blind to intent/context. The local `static_manual` comparator is
   explicitly oracle-style and not used to claim operational superiority.

## Final demonstration commands

### Read-only and local validation

```bash
git fetch --prune origin
git status
git rev-parse HEAD
git branch -vv
git log --graph --decorate --oneline --all --date-order -50
git merge-base --is-ancestor 9c7aabf HEAD
git merge-base --is-ancestor 22d6253 HEAD
git merge-base --is-ancestor 0ffbd9a HEAD
git merge-base --is-ancestor 1eeeee0 HEAD

bash scripts/setup.sh
bash scripts/check.sh

.venv/bin/python -m experiments.runner \
  --smoke --environment-id defense-smoke --timeout 60
.venv/bin/python -m experiments.runner \
  --full-matrix --repeats 5 --seed 20260719 --dry-run \
  --environment-id defense-plan

.venv/bin/python -m experiments.analyze_results \
  --experiment-dir experiments/raw/20260719T140431Z-matrix-aed48949 \
  --results-dir results \
  --results-md docs/evaluation/RESULTS.md \
  --environment-report results/environment-capability.json \
  --overwrite
make regenerate-cluster-results
make validate-raw-integrity
make capacity-dry-run
git diff --exit-code
```

### Optional isolated Kubernetes rerun

These commands create and delete only the named disposable profile and demo
namespace. Confirm that `intent-spawner-eval` does not already contain user
work before running them.

```bash
minikube start -p intent-spawner-eval \
  --driver=docker --kubernetes-version=v1.33.1 \
  --cpus=6 --memory=6144mb --disk-size=20g \
  --container-runtime=containerd \
  --extra-config=kubelet.system-reserved=cpu=2,memory=2Gi
minikube addons enable metrics-server -p intent-spawner-eval
kubectl --context intent-spawner-eval create namespace z2jh-context-demo

AUDIT_SHORT="$(git rev-parse --short=12 HEAD)"
docker build -t "intent-spawner-defense:${AUDIT_SHORT}" \
  -f cluster_evaluation/Dockerfile .
minikube image load -p intent-spawner-eval \
  "intent-spawner-defense:${AUDIT_SHORT}"
kubectl --context intent-spawner-eval wait \
  --for=condition=Available deployment/metrics-server \
  -n kube-system --timeout=180s

.venv/bin/python -m cluster_evaluation.runner \
  --kind ground-truth \
  --experiment-dir "/tmp/intent-spawner-defense-ground-${AUDIT_SHORT}" \
  --image "intent-spawner-defense:${AUDIT_SHORT}" \
  --repeats 1 --seed 20260721 --timeout 120

minikube delete -p intent-spawner-eval
kubectl config use-context orbstack
```

For capacity live validation, use the separate required profile and keep the
experiment directory inside the repository (new raw directories remain
untracked until explicitly reviewed):

```bash
minikube start -p intent-spawner-capacity-v2 \
  --driver=docker --kubernetes-version=v1.33.1 \
  --cpus=6 --memory=6144mb --disk-size=20g \
  --container-runtime=containerd \
  --extra-config=kubelet.system-reserved=cpu=2,memory=2Gi
kubectl --context intent-spawner-capacity-v2 create namespace z2jh-context-demo
kubectl --context intent-spawner-capacity-v2 label namespace z2jh-context-demo \
  z2jh-context-demo.local/disposable-capacity-v2=true

CAPACITY_SHORT="$(git rev-parse --short=12 HEAD)"
CAPACITY_IMAGE="intent-spawner-cluster-eval:capacity-v2-${CAPACITY_SHORT}"
docker build -t "${CAPACITY_IMAGE}" -f cluster_evaluation/Dockerfile .
minikube image load -p intent-spawner-capacity-v2 "${CAPACITY_IMAGE}"

.venv/bin/python -m cluster_evaluation.capacity_runner \
  --experiment-id "defense-capacity-${CAPACITY_SHORT}" \
  --experiment-dir "results/cluster/raw/defense-capacity-${CAPACITY_SHORT}" \
  --image "${CAPACITY_IMAGE}" --repeats 1 --seed 20260721 \
  --hold-seconds 20 --sample-interval-seconds 0.3 \
  --expected-node-cpu 6 --expected-node-memory-ki 6088560

minikube delete -p intent-spawner-capacity-v2
kubectl config use-context orbstack
```

## Final student checklist

- [ ] Push the report-bearing commit to the authoritative defense branch or
  include its exact hash in the archival release.
- [ ] Verify the four required ancestry checks and a clean `git status` before
  presenting.
- [ ] Present prototype mechanics, local synthetic evaluation, Kubernetes
  comparative evaluation, and capacity-v2 as separate evidence classes.
- [ ] State the negative result plainly: context-aware did not beat intent-only;
  intent-only was strongest in this tested environment.
- [ ] State that all Small runs succeeded and therefore no OOM reduction is
  demonstrated.
- [ ] Use `1 [0,2)` for Kubernetes method timing and claim no timing advantage.
- [ ] Call the CPU statistics 202 full-window averages and 86 legacy hybrid
  maxima; claim zero genuine continuous CPU peaks.
- [ ] Describe memory peak as cgroup `memory.peak` for Kubernetes and process RSS
  for local synthetic results.
- [ ] Keep historical capacity supplementary and show only capacity-v2 as the
  reproducible controlled request-reservation experiment.
- [ ] Do not claim generalized OOM reduction, waste reduction, production
  density, context superiority, history effectiveness, or GPU effectiveness.
- [ ] Keep the unsanitized pilot private and excluded.
- [ ] Preserve any new live-run raw evidence separately; do not overwrite or
  mix it into committed principal evidence without a new reviewed protocol.
- [ ] Add an institution-approved software license before broad redistribution.
- [ ] Delete only explicitly disposable Minikube profiles and restore the prior
  context after live demonstrations.
