# Final Independent Research-Artifact Audit

> Historical audit notice: this report records the blockers at audited commit
> `92a9c25`. Blocker remediation is tracked in
> `AUDIT_BLOCKER_RESOLUTION.md`; the historical findings below are preserved
> for provenance and are not a statement that current source still lacks the
> schema-v2 timing/CPU rules or capacity-v2 runner.

Audit date: 2026-07-20 (Asia/Ho_Chi_Minh)

Exact audited commit: `92a9c25b53d8437a448c21a29b3d4269f000c691`

Kubernetes workload-evaluation commit recorded by the raw corpus:
`39b69731a9aeaa85247c01e946e26656beae6e64`

This report is committed after the audited tree. A file cannot contain the hash
of the commit that contains itself, so the report-bearing commit is expected to
be a child of the exact audited commit above.

## Overall verdict: Not ready

The artifact is strong enough to defend as a deterministic prototype with a
reproducible synthetic analysis pipeline and a carefully scoped single-node
Kubernetes observation. It is not ready for defense if the thesis claims that
context-aware selection improves accuracy, OOM risk, time to success, resource
waste, or cluster density.

Three High-severity failures prevent a `Ready` verdict:

1. The exact capacity batch generator is absent from evaluated commit
   `39b6973`. The retained plan and batch records reconcile, but the density
   experiment cannot be reproduced from its recorded code revision.
2. The preregistered acceptable-envelope analysis used one-second Kubernetes
   timestamps for millisecond jobs. Five larger-profile acceptances were caused
   by `1.0` versus `0.0` second quantization. The audit correction is explicit
   and preserves raw data, but it is post hoc; corrected acceptability rates are
   diagnostic, not confirmatory.
3. `origin/main` remains at `96a2916`, 20 commits behind the audited tree. A
   default fresh clone does not contain the integrated artifact or this audit.

The raw Kubernetes corpus also mislabeled a full-job CPU average as
`peak_cpu_m` in 202/288 short runs with no periodic sample. Current code fixes
future records, but those historical values cannot support CPU-peak claims.

## Evidence-class verdicts

| Evidence class | Verdict | Boundary |
| --- | --- | --- |
| Prototype mechanics | **Ready with conditions** | Recommender logic, Helm/KubeSpawner pre-spawn application, profile parity, policy fallback, and privacy behavior are implemented and tested. The operational evaluation bypasses JupyterHub and directly creates pods, so it is not an end-to-end JupyterHub effectiveness trial. No committed raw OOM demo is available. |
| Synthetic comparative evaluation | **Ready with conditions** | The 180-record local matrix, method isolation, boundary cases, immutable records, and analysis regeneration are reproducible. `static_manual` reads manifest acceptable profiles and is an oracle-style deterministic comparator, not a fair real-user baseline; manifest expectations are not independent operational ground truth. |
| Kubernetes-backed operational evaluation | **Not ready** | Requests/limits, outcomes, cgroup memory peaks, Pending events, repeats, and cleanup are retained and reconcile. No OOM occurred, fine-grained time effects are unresolved, most CPU peaks are unavailable, the workloads are much smaller than their declared hints, and the capacity generator provenance is incomplete. |

Evidence from these rows must not be promoted across classes. In particular,
Helm mechanics do not prove operational benefit, local process RSS does not
prove pod utilization, and retained capacity events do not validate production
cluster density.

## Git and ancestry verification

### Mandated entry snapshot

Before auditing, the following commands were run exactly:

```bash
git status
git rev-parse HEAD
git log --graph --decorate --oneline --all --date-order -40
```

The worktree was clean on `codex/integrated-research-artifact`; entry HEAD was
`a14e86c63b1436634f636d5d6f7c5a5dc6bf3f1c`. The graph showed the real
Kubernetes result commit `22d6253`, the cluster harness lineage beginning at
`d7c17bb`, integration report `6240333`, merge `9c7aabf`, both integration
parents `a965ab6` and `266ac36`, and the earlier research commits. The audit
then made two justified correction commits, `2d732a5` and `92a9c25`; the latter
is the exact audited tree.

### Ancestor checks

`git merge-base --is-ancestor <commit> HEAD` passed for all intended commits:

| Work | Verified ancestor |
| --- | --- |
| Initial roadmap and stabilization | `e5508d9`, `2bfc07e`, `8c1ca92` |
| Integrated synthetic/reproducibility line | `061dedf`, `3b8c5a9`, `11d8cc5`, `d0bfe3e`, `a965ab6` |
| Historical independent audit | `266ac36` |
| Integration | `9c7aabf`, `6240333` |
| Kubernetes harness and provenance fixes | `d7c17bb`, `84e8d2c`, `0243831`, `9f6e326`, `39b6973` |
| Kubernetes results and analysis | `22d6253`, `67c6c18`, `a14e86c` |

The sibling commits `d6f8d71`, `6948ee4`, and `6c19a1a` are not required
ancestors; `BRANCH_INTEGRATION_REPORT.md` documents their equivalent or
superseding integrated commits. This is not an ancestry failure.

`git rev-list --left-right --count origin/main...HEAD` returned `0 20` at the
audited commit. The correct local branch was audited, but the configured remote
does not publish it through `origin/main`.

## Gate results

| Gate | Result | Severity and evidence |
| --- | --- | --- |
| Intended commits are ancestors | Pass | All commits in the table above passed `merge-base --is-ancestor`. |
| Worktree clean at entry and audited commit | Pass | Entry `a14e86c` and audited `92a9c25` both had empty short status; clean detached-worktree regeneration also ended clean. |
| Raw results exist | Pass | Local: 180 matrix records. Cluster: 108 ground-truth records, 180 comparative records, nine capacity batches containing 108 pod outcomes. |
| Raw/result reconciliation | Pass | `cluster_evaluation.validate_artifacts` matches plans, unique run IDs, JSONL records, sidecars, evidence, paths, commits, and applied resources. |
| No tracked result overwritten | Pass for tracked corpus | Cluster raw files were added once in `22d6253`; 288 record sidecars equal their JSONL entries and use exclusive creation. Capacity sidecars equal all nine JSONL entries. The missing capacity generator prevents a stronger provenance pass. |
| Result commit matches evaluated workload code | Pass for ground/comparative | Every cluster record and environment names clean commit `39b6973`; that commit exists and is an ancestor. Current corrections do not rewrite historical raw records. |
| Summary counts reconcile | Pass | 108/108 ground truth, 180/180 comparative, 108/108 capacity pods; zero failures, timeouts, OOMs, exclusions in analyzed matrices, or cleanup failures. |
| Every figure regenerates | Pass | Local and cluster CSV/SVG/YAML/Markdown regeneration was byte-stable at audited commit `92a9c25`; LF output is enforced for CSV portability. |
| Synthetic and cluster outputs separated | Pass | `experiments/raw` + `results/*.csv` versus `results/cluster/raw` + `results/cluster/derived`; reports state the boundary. |
| Intent-only cannot access context | Pass | Mutation tests prove dataset size and code hints cannot affect `intent_only`; the operational call fixes size to zero and context to empty. |
| Context-aware input is documented | Pass | It receives synthetic intent, declared size hint, joined code-context hints, and permitted policy fields—no history. |
| Operational baseline fair and named | Pass | `static_default` applies Medium to every workload and cannot read intent, context, hints, or ground truth. It is distinct from local oracle-style `static_manual`. |
| History-aware excluded | Pass | No history method, storage, result, or claim is present. |
| Ground truth independent of recommender | Pass for cluster sweep | The three-profile sweep never calls the recommender and excludes manifest expectations. Local manifest expectations do not qualify as independent ground truth. |
| Acceptable envelopes measurement-valid | Fail | **High:** original time branch was quantization-contaminated; the disclosed post-hoc adequacy guard changes rates from 20/40/25 to 5/30/20. |
| Repeated runs and exclusions | Pass with caveat | Ground truth has three repeats/profile; comparative has five repeats/method; capacity has three counterbalanced repeats. The unsanitized pilot exclusion is documented and not used, but remains as an ignored local directory. |
| Recommended/applied profile verified | Pass | All 288 ground/comparative pod records match recorded profile definitions and sanitized pod requests/limits; an independent 36-pod rerun also matched 36/36. |
| Metrics source documented | Conditional | Memory uses cgroup-v2 `memory.peak`. Metrics Server was available but retained zero job snapshots. Only 86/288 runs have periodic CPU samples; 202 historical CPU averages cannot be called peaks. |
| OOM Kubernetes-observed | Fail for outcome claims | Zero of 288 evaluated pods was OOMKilled. Sanitized parser fixtures and historical manual prose test mechanics only. |
| Pending Kubernetes-observed | Pass, scoped | Capacity records retain `FailedScheduling` with insufficient CPU/memory: 15 static, 9 intent-only, 15 context-aware pods; queued median was 22 seconds for each method. |
| Controlled density experiment | Fail provenance | **High:** environment and counterbalanced raw batches are retained, but the exact evaluated batch generator is absent. Only descriptive reservation concurrency is allowed. |
| Missing metrics remain missing | Fail historically, corrected prospectively | **Medium:** 202 CPU averages were historically put in a peak field. The audit forbids using them and changes future code to keep peak null when unsampled. Raw evidence was not altered. |
| Failed runs remain visible | Pass | No analyzed run failed; schemas and ledgers preserve failure fields. The excluded pilot is explicitly documented. |
| Clean setup/check/smoke/matrix validation | Pass | Fresh detached worktree at `92a9c25`: setup succeeded, 60 tests passed, Helm and Kubernetes client checks passed, smoke 1/1 succeeded, dry-run planned 180. |
| One cluster experiment | Pass for committed ground-truth runner | A clean worktree at entry commit `a14e86c` ran a one-repeat 36-pod sweep on a recreated disposable Minikube profile: 36 successes, 36 cleanups, 36 resource matches. The profile was deleted and `orbstack` restored. Later changes only correct analysis/validation and future CPU field semantics. |
| Capacity experiment reproducible | Fail | **High:** exact generator source missing from recorded code. |
| Privacy scan | Pass for tracked tree | Zero tracked `.ipynb`, private-key files, credentials, raw datasets, or person-identifying cluster/user names were found. Generic experiment pseudonyms and hashed pod names are documented. |
| License and declarations | Partial | Synthetic-data and no-external-dataset declarations are clear. **Medium:** no project software license exists, so redistribution rights remain unresolved. |
| Remote clean-clone reproducibility | Fail | **High:** `origin/main` is still the initial commit and lacks the integrated artifact. |

## Claim-to-evidence matrix

| Claim | Evidence class | Direct evidence | Decision |
| --- | --- | --- | --- |
| The recommender is deterministic, rule-based, and explainable | Prototype | Recommender tests and explicit reasons/rules | Allowed |
| KubeSpawner can apply the recommended profile before spawn | Prototype | Proposed Helm hook plus profile-resource tests | Allowed as mechanics |
| Profile/policy metadata avoids storing raw intent and code | Prototype | Hook behavior, allowlists, privacy tests, governance | Allowed for this implementation |
| Intent-only is isolated from dataset and code context | Synthetic + operational method construction | Mutation tests and fixed zero/empty call | Allowed |
| Context-aware uses exactly intent, size hint, code hints, and policy | Synthetic + operational method construction | Method code, plan records, tests | Allowed |
| The local pipeline is reproducible | Synthetic | 180 immutable records; exact regenerated tables/figures | Allowed |
| `static_manual` is a fair empirical baseline | Synthetic | It reads expected acceptable profiles | Remove; call it oracle-style deterministic comparator |
| Context improves recommendation quality | Cluster comparative | Corrected diagnostic acceptability: context 20/60, intent-only 30/60 | Remove; observed ordering is opposite |
| Context reduces reservation waste | Cluster comparative | Median waste: context 0.979, intent-only 0.958, static 0.979 | Remove |
| Context improves time to success | Cluster comparative | All method medians are 1.0 s; timestamp resolution is one second | Remove |
| The method reduces OOM or reruns | Cluster comparative | 0 OOM, 0 failures, 0 restarts in every method | Remove; no effect can be estimated |
| Applied requests/limits match profile definitions | Cluster operational | 288/288 raw evidence matches; independent rerun 36/36 | Allowed for the direct pod runner |
| Memory peak and request waste were pod-observed | Cluster operational | cgroup-v2 `memory.peak` retained for 288 runs | Allowed, scoped to this image/workload/node |
| CPU peak was observed for all jobs | Cluster operational | Only 86 periodic samples; 202 full-window averages mislabeled historically | Remove; use only the 86 sampled values with caveat |
| Intent-only admitted 9 versus 7 concurrent pods | Cluster capacity | Three retained counterbalanced batches/method and scheduler events | Allowed only as a descriptive observation of the retained run |
| The approach improves cluster density generally | Cluster capacity | Missing evaluated generator; one local node and synthetic population | Remove |
| GPU or history-aware behavior is effective | None | No GPU execution or history method | Remove |
| Results generalize to production JupyterHub users | None | No real users, notebooks, datasets, multi-node cluster, or production load | Remove |

## Allowed thesis claims

1. A deterministic and explainable intent/context rule layer can be integrated
   into a JupyterHub pre-spawn path and mapped to bounded CPU/memory profiles.
2. Intent-only and context-aware methods can be isolated by construction, and
   the repository tests that hidden context cannot influence intent-only.
3. The artifact provides reproducible synthetic workloads, immutable local
   results, and a raw-to-table/figure analysis pipeline.
4. In one disposable single-node Kubernetes evaluation, 288 direct benchmark
   pods applied the intended profile requests/limits and completed without OOM,
   timeout, restart, or cleanup failure.
5. On this synthetic suite, larger requested profiles generally produced high
   memory reservation waste; this is a suite-specific observation, not a
   production effect.
6. The retained capacity batches descriptively show request-driven scheduler
   waves and `FailedScheduling` events under controlled node capacity and a
   20-second hold, subject to the missing-generator provenance caveat.

## Claims that must be softened or removed

- Replace “context-aware improves recommendation accuracy” with “the artifact
  evaluates deterministic method behavior; the retained suite does not show a
  context advantage over intent-only.”
- Remove claims of reduced OOM, reruns, restarts, or late failures.
- Remove claims of improved time to success; coarse medians are equal.
- Remove claims that context-aware reduces waste; its median equals the static
  default to three decimals and exceeds intent-only.
- Replace “improves cluster density” with the exact descriptive 9-versus-7
  reservation observation and disclose missing capacity-runner source.
- Do not call 202 historical CPU values peaks.
- Do not present the post-hoc envelope guard or corrected acceptability rates as
  preregistered confirmatory analysis.
- Do not call the direct-pod operational runner an end-to-end JupyterHub trial.
- Remove history-aware, GPU scheduling, real-user, and production-generalization
  claims.

## Unresolved blockers

| Severity | Blocker | Required resolution |
| --- | --- | --- |
| High | Capacity batch generator absent from evaluated revision | Reimplement and preregister the runner, commit it before execution, rerun on a disposable cluster, and retain new raw evidence. Do not backfill source provenance for the old run. |
| High | Corrected resource-envelope rates are post hoc after invalid timestamp use | Run longer workloads with a preregistered timing source/adequacy rule and an independent envelope derivation; keep the current rates diagnostic. |
| High | Integrated/audited branch absent from `origin/main` | Push the audited lineage and make the exact commit reachable from the repository/archival release used for defense. |
| Medium | CPU peak unavailable for 202/288 historical runs | Rerun with longer jobs and a time-series source or enough periodic samples; preserve null peaks when unsampled. |
| Medium | Synthetic workload size is inconsistent with declared GB hints | Scale workloads to measured resource envelopes or clearly frame hints as intentionally conflicting recommender inputs. |
| Medium | No OOM failures occurred | Add preregistered bounded OOM-sensitive cases only if an OOM outcome claim is essential. |
| Medium | No project software license | Select and add an institution-approved license before redistribution. |
| Medium | Ignored pilot contains machine identifiers | Keep it local and excluded; sanitize only into a new derived/public copy while retaining an access-controlled original if policy requires preservation. |

## Five strongest evidence items

1. The Git lineage is explicit: both integration parents, merge, cluster harness,
   provenance fixes, evaluated commit, result commit, and audit corrections are
   ancestors of the audited tree.
2. `cluster_evaluation.validate_artifacts` reconciles 108 ground-truth records,
   180 comparative records, nine capacity batches/108 pods, commits, sidecars,
   resources, paths, and outcomes with no missing run IDs.
3. A clean detached worktree at `92a9c25` passed 60 tests, both Helm renders,
   Kubernetes client dry-runs, smoke, 180-run matrix planning, and exact local
   plus cluster result/figure regeneration.
4. Every one of the 288 retained ground/comparative pod records contains
   Kubernetes-observed requests/limits matching its applied profile, and the
   independent 36-pod rerun reproduced those matches.
5. The capacity raw records preserve repeated scheduler evidence: 39 pods with
   `FailedScheduling`, exact insufficient-resource messages, Pending durations,
   concurrency samples, fixed population, and counterbalanced order—useful
   descriptive evidence despite the source-provenance blocker.

## Five likely committee criticisms and evidence-based answers

1. **“Did context help?”** No advantage is demonstrated. After correcting the
   quantized envelope analysis, intent-only matches 30/60 diagnostic envelopes
   and context-aware 20/60; context also has higher median waste. The defensible
   contribution is method construction and artifact evaluation, not superiority.
2. **“Is the ground truth circular?”** The Kubernetes profile sweep is
   independent of recommender output and excludes manifest expectations. The
   local `static_manual` method is oracle-style and is not used as the
   operational baseline. The corrected envelope guard is post hoc, so those
   rates remain diagnostic.
3. **“Can I reproduce the density result?”** Not exactly. The raw plan, batches,
   pod outcomes, and environment reconcile, but the evaluated batch generator
   is absent. Therefore only the retained-run observation may be described; a
   general or confirmatory density claim is withdrawn.
4. **“Where is the OOM evidence?”** The evaluated matrices had zero OOMs. A
   parser fixture and manual demo narrative verify mechanics, not comparative
   reduction. The thesis must remove an OOM-benefit claim or run a new bounded,
   preregistered OOM-sensitive experiment.
5. **“Are the peak metrics trustworthy?”** Memory peak is read from cgroup-v2
   `memory.peak`. Metrics Server captured no per-job samples. Only 86 jobs have
   periodic CPU observations; 202 historical CPU values are full-job averages
   and are explicitly disallowed as peak evidence.

## Final defense commands

Run these at the exact audited commit or the report-bearing child:

```bash
git status
git rev-parse HEAD
git merge-base --is-ancestor 9c7aabf HEAD
git merge-base --is-ancestor 39b6973 HEAD
git merge-base --is-ancestor 22d6253 HEAD
git merge-base --is-ancestor 92a9c25 HEAD

bash scripts/setup.sh
bash scripts/check.sh

.venv/bin/python -m experiments.runner \
  --smoke --environment-id defense-smoke --timeout 60
.venv/bin/python -m experiments.runner \
  --full-matrix --repeats 5 --seed 20260719 --dry-run \
  --environment-id defense-dry-run

make validate-cluster-results
make regenerate-cluster-results
git diff --exit-code
```

For an isolated cluster-backed defense rerun, first confirm that the named
Minikube profile is disposable. These commands mutate and then delete only that
profile and namespace:

```bash
minikube start -p intent-spawner-eval \
  --driver=docker --kubernetes-version=v1.33.1 \
  --cpus=6 --memory=6144mb --disk-size=20g \
  --container-runtime=containerd \
  --extra-config=kubelet.system-reserved=cpu=2,memory=2Gi
minikube addons enable metrics-server -p intent-spawner-eval
kubectl --context intent-spawner-eval create namespace z2jh-context-demo
docker build -t intent-spawner-defense:92a9c25 \
  -f cluster_evaluation/Dockerfile .
minikube image load intent-spawner-defense:92a9c25 \
  -p intent-spawner-eval
kubectl --context intent-spawner-eval wait \
  --for=condition=Available deployment/metrics-server \
  -n kube-system --timeout=180s

.venv/bin/python -m cluster_evaluation.runner \
  --kind ground-truth \
  --experiment-dir /tmp/intent-spawner-defense-ground-truth \
  --image intent-spawner-defense:92a9c25 \
  --repeats 1 --seed 20260720 --timeout 120

minikube delete -p intent-spawner-eval
kubectl config use-context orbstack
```

Do not rerun the historical capacity claim until a reviewed capacity runner is
committed and preregistered.

## Final student checklist

- [ ] Make the audited/report commits reachable from the defense repository or
  archival release; do not point examiners at current `origin/main`.
- [ ] Use “prototype mechanics,” “synthetic comparative evaluation,” and
  “Kubernetes-backed operational observation” as separate evidence classes.
- [ ] Present context-aware 20/60 versus intent-only 30/60 only as a corrected,
  post-hoc diagnostic result.
- [ ] Remove OOM reduction, time improvement, waste improvement, production
  density, history-aware, GPU, and real-user effectiveness claims.
- [ ] Describe capacity as retained descriptive evidence and disclose the
  missing generator before showing the 9-versus-7 chart.
- [ ] State that memory peak is cgroup-observed; never call the 202 unsampled CPU
  averages peaks.
- [ ] Show `make validate-cluster-results` and `git diff --exit-code` live.
- [ ] Keep the ignored unsanitized pilot private and excluded.
- [ ] Add a software license before distributing the artifact beyond the
  examination context.
- [ ] If stronger outcome claims are required, rerun a preregistered experiment
  with committed capacity source, longer representative workloads, valid timing
  and CPU telemetry, and bounded OOM-sensitive cases.
