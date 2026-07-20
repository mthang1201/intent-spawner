# Final Thesis Repository Audit

> **Historical audit status:** This document records Chat 8's audit of commit
> `8c1ca92` plus its fixes on branch `codex/final-audit`. Its statements that the
> experiment runner, method isolation, result schema, raw results, and analysis
> regeneration were absent were correct for that stale audited tree, but are
> superseded by `docs/evaluation/BRANCH_INTEGRATION_REPORT.md` on the integrated
> branch. Concrete privacy, portability, Kubernetes-safety, and wording fixes
> from this audit remain part of the integrated artifact.

Audit date: 2026-07-19 (Asia/Ho_Chi_Minh)<br>
Audited merged base: `8c1ca92755f09612bb3d8b4f2b601285f95b5d7e` (`main`)<br>
Audit branch: `codex/final-audit`

## Overall Verdict: Not ready

The repository supports a runnable prototype claim: rule-based intent,
dataset-size, and code-context signals can select a resource profile and a
KubeSpawner pre-spawn hook can apply the corresponding CPU and memory settings
with a derived explanation.

The repository does **not** support the principal outcome claims normally
expected from the thesis title. There is no merged comparative experiment
runner, isolated intent-only condition, fair automated static baseline, result
schema, raw comparative result set, repeated trial record, statistical summary,
or figure-regeneration path. Another person can verify the prototype mechanics
and deterministic workload suite, but cannot reproduce a finding that the
method reduces OOMs, waste, pending time, restarts, or time to success.

This is an evidence deficit, not a failed statistical result. Thesis wording
must stay at prototype feasibility and mechanism demonstration until the
missing evaluation is implemented and run.

## Scope And Provenance

`git fetch origin main --prune` showed that `origin/main` remains at the initial
commit `96a29164001f33e391227a16c1963fac6945c1ff`. The local `main` was a clean,
three-commit descendant at `8c1ca92` and was treated as the latest merged main
requested for this audit. Newer topic branches contain unmerged experiment and
result artifacts; they were deliberately excluded because unmerged branch
content is not evidence in the audited repository.

This provenance creates a publication blocker: a fresh clone of the configured
remote does not contain even the audited local-main base. The final audit commit
must be merged and pushed before another person can verify the same tree.

## Gate Summary

| Gate | Status | Exact evidence |
| --- | --- | --- |
| Clean setup | Pass | A new Python 3.14.5 virtual environment installed the exact versions in `requirements-dev.txt`; `pip check` reported no broken requirements. |
| Repository checks | Pass | Final `make check`: 32 tests passed; Python and shell syntax passed; both Helm renders passed; all three Kubernetes manifests passed client dry-run. |
| Context-aware implementation | Pass for prototype | [`helm/proposed-values.yaml`](../../helm/proposed-values.yaml) lines 53-177 consume intent, dataset size, and code context, then apply profile resources before spawn. |
| Baseline resource parity | Pass for configuration | [`tests/test_config_validation.py`](../../tests/test_config_validation.py) lines 65-92 verify identical Small/Medium/Large mappings between baseline and proposed configuration. |
| Deterministic workload suite | Pass | [`benchmarks/workloads.yaml`](../../benchmarks/workloads.yaml) defines 12 unique workloads; the final audit smoke executed 12/12, with zero failures and 12 unique digests. |
| Manual mechanism demos | Pass, non-comparative | The audit observed an OOMKilled Small pod, request-induced Pending pods, and a successful recommendation-applied workload. These are one-off mechanics, not result trials. |
| Intent-only isolation | Fail | No named runnable method prevents code context from being passed. Manifest field separation is not method isolation. |
| Static baseline fairness | Fail | Static selection remains manual; there is no frozen assignment policy or paired method runner. Resource-band parity alone does not make the comparison fair. |
| History-aware evaluation | Not applicable / unsupported | No history capture, storage, features, decision path, or trials exist. Documentation now treats it only as future work. |
| Experiment integrity | Fail | Zero merged experiment records; expected comparative count is undefined; no run IDs, trial indices, exclusion log, failure ledger, or commit field exists. |
| Result regeneration | Fail | No results directory, analysis command, or sanitized raw comparative evidence is merged. |
| Figure regeneration | Fail | No figures or figure-generation command is merged. |
| Usage metrics | Fail for usage claims | `kubectl top nodes` returned `Metrics API not available`; no alternative time-series collector exists. |
| Privacy of tracked artifacts | Pass after fixes | Zero `.ipynb` files, no committed shared password, no tracked machine-specific capacity file, and no raw intent/code copied by the recommendation hook. |
| Data provenance/licensing | Partial | Every workload declares synthetic generated data with no external dataset dependency, but reuse rights remain tied to the missing project license. |
| Software licensing | Fail | No project software license is present; redistribution rights are unresolved. |
| Remote reproducibility | Fail | `origin/main` is three local-main commits behind the audited base before this audit commit. |

## Claim-To-Evidence Review

### Supported

1. **The recommender is deterministic, rule-based, and explainable.** The rule
   implementation is in [`recommender/recommender.py`](../../recommender/recommender.py)
   lines 98-147; deterministic explanations and all four label paths are tested
   in `recommender/test_recommender.py`.
2. **The proposed Helm path uses the documented signals.** Intent,
   `dataset_size_gb`, and `code_context` enter the embedded recommender at
   [`helm/proposed-values.yaml`](../../helm/proposed-values.yaml) lines 138-146.
3. **The recommendation is applied before pod creation.** The same hook sets
   CPU guarantee/limit and memory guarantee/limit at lines 146-150 and is
   installed as `pre_spawn_hook` at line 177.
4. **Baseline and proposed use the same resource bands.** The tested mappings
   are Small `100m/256M`, Medium `500m/768M`, and Large `1500m/1536M` requests,
   with matching limits; see `tests/test_config_validation.py` lines 12-92.
5. **The benchmark is synthetic and deterministic.** The manifest lists seeds
   and commands, while [`benchmarks/workload_runner.py`](../../benchmarks/workload_runner.py)
   lines 279-310 emits a deterministic digest and explicit runtime units.
6. **GPU-or-Large is a label with CPU Large fallback, not GPU support.** The
   embedded hook maps `gpu_or_large` to the Large resource band. No GPU resource
   request is applied.

### Stronger Than The Evidence

The following statements are unsupported if presented as findings:

- recommendations reduce late failures or reruns;
- recommendations reduce defensive over-requesting or request-to-usage waste;
- recommendations improve schedulable concurrency or cluster utilization;
- context-aware selection outperforms intent-only selection;
- the recommender has predictive accuracy against real workload needs;
- the benchmark is representative of production pandas, scikit-learn, deep
  learning, multi-user arrivals, or real datasets;
- GPU policy is enforced;
- history improves later spawns;
- user behavior changes because of the interface.

README and demo wording were softened so these are now hypotheses or prohibited
claims rather than repository results.

## Method Isolation

### Static baseline

The static Helm values expose Small, Medium, and Large and use the same resource
mapping as the proposed method. This is a valid interface demonstration and a
necessary fairness precondition. It is not a fair experimental baseline because
the repository does not specify who chooses which static profile, freeze that
choice per workload, or automate paired trials under identical conditions.

### Intent-only

Intent-only is not implemented as a method. Although
`benchmarks/workloads.yaml` stores intent separately from code-context hints,
there is no mode flag or runner that constructs the condition with code context
provably empty. No intent-only result may be claimed.

### Context-aware

Context-aware is implemented for the prototype. The live audit submitted a
training intent, dataset size `1.5`, and pandas/`.fit` code hints. The resulting
pod had profile `large`, CPU request `1500m`, memory request `1610612736` bytes,
CPU limit `2`, and memory limit `2147483648` bytes. The bounded 512 MiB workload
then completed. This verifies signal application and mechanics only.

### History-aware

No evaluated history-aware method exists. No history-derived claim is allowed.

## Experiment Integrity

### Counts

| Population | Expected | Actual | Failed | Excluded | Interpretation |
| --- | ---: | ---: | ---: | ---: | --- |
| Comparative method trials | Not declared | 0 merged | Not recorded | Not recorded | Completeness cannot be assessed. |
| Final workload smoke | 12 manifest entries | 12 | 0 | 0 | Validates workload execution only. |
| Final automated tests | 32 collected | 32 passed | 0 | 0 | Validates code/config invariants only. |

### Integrity checks

- Workload IDs are unique and match runner plans by automated test.
- There are no experiment run IDs to de-duplicate.
- There are no result records in which to check missing fields, failed trials,
  excluded trials, or commit mismatches.
- Runtime RSS is now normalized to `max_rss_bytes`; the ambiguous
  `max_rss_platform_units` field was removed.
- Benchmark `--metadata-out` now uses exclusive creation and refuses to
  overwrite an existing file; this is tested at
  [`tests/test_benchmark_workloads.py`](../../tests/test_benchmark_workloads.py)
  lines 183-202.
- The committed machine-specific `helm/generated-values.yaml` was removed and
  ignored. Its generator no longer emits a node name.
- No comparative output can be judged suspiciously overwritten because no
  comparative output is merged. That absence fails the evidence gate rather
  than passing the integrity gate.

## Reproducibility

### Verified paths

- Clean dependency installation with exact versions.
- `make check` from a clean virtual environment.
- One direct deterministic workload command and all 12 manifest commands.
- Helm chart `4.0.0` rendering for baseline and proposed values.
- Kubernetes client dry-runs.
- Live proposed Helm install, local login, spawn-form submission, pinned image
  resolution, resource application, and namespace cleanup.

The final live pod requested the committed image reference
`quay.io/jupyter/scipy-notebook:latest@sha256:e760028814b48e503f8991e20f89ad7ba2725b34ca7d937b104584b78f11169f`,
and Kubernetes reported the same resolved digest.

### Missing paths

- No smoke **experiment** compares methods; only workloads and manual demos run.
- No result regeneration exists.
- No figure regeneration exists.
- No environment/commit metadata is bound to result records.
- No CI repeats the cluster-free checks.
- The configured remote does not contain the audited local-main base.

## Privacy And Compliance

The audit removed three tracked `.ipynb` files and the notebook ConfigMap/mount,
because the same bounded workload scripts can be run from a JupyterLab terminal
without retaining notebook artifacts. It also removed the committed dummy
shared password. JupyterHub 5.2.1 DummyAuthenticator accepts any username and
non-empty password by default, which is suitable only for isolated testing and
is explicitly documented as insecure in the
[official JupyterHub documentation](https://jupyterhub.readthedocs.io/en/5.2.1/reference/authenticators.html).

The corrected hook retains only the recommended profile, derived matched-signal
reasons, and normalized dataset size. A live sentinel check found neither raw
intent nor raw code in the pod object or hub log. JupyterHub and Kubernetes do
still use and log the operational username and pod name; therefore raw platform
logs must not be committed without sanitization. The governing rules are in
[`docs/DATA_GOVERNANCE.md`](../DATA_GOVERNANCE.md).

No committed credential pattern or sensitive dataset was found. The benchmark
declarations make external-data provenance clear, but they do not grant reuse
rights. The missing project software license remains a compliance blocker for
redistribution.

## Documentation Consistency

The README, benchmark design, roadmap, demo script, and data-governance document
now agree on these facts:

- this is a prototype and mechanism demo, not a completed comparative study;
- intent-only and history-aware methods were not evaluated;
- the 12-workload suite is synthetic and not evidence of production benefit;
- no comparative results or figures are merged;
- raw notebooks and raw form inputs are not repository evidence;
- result and figure regeneration commands do not yet exist.

Dedicated experiment-protocol, result-schema, results, and threats-to-validity
documents are absent. `BENCHMARK_DESIGN.md`, this audit, and the roadmap cover
some design and threat boundaries, but cannot substitute for documents tied to
an actual evaluation. Adding empty or hypothetical result documents would be
misleading, so they remain unresolved blockers rather than fabricated fixes.

## Five Strongest Pieces Of Evidence

1. **KubeSpawner integration test and live spawn:** tested resource parity plus
   a live Large pod with the expected requests/limits and derived explanation.
2. **Complete local check:** 32/32 tests, syntax checks, both Helm renders, and
   three Kubernetes manifest dry-runs passed from a clean environment.
3. **Deterministic benchmark shape:** 12 unique workload IDs, fixed seeds,
   executable commands, 12/12 final smoke completions, and 12 unique digests.
4. **Bounded OOM mechanism:** the Small pod terminated `OOMKilled` with exit 137
   after reporting allocation through 352 MiB under a 384 MiB limit.
5. **Request-based scheduling mechanism:** with each idle pod requesting 4400m
   CPU and 4396Mi memory, one ran and two remained Pending with explicit
   `Insufficient cpu` and `Insufficient memory` events.

Only items 1-3 directly support prototype/reproducibility claims. Items 4-5
support mechanism explanations, not comparative effectiveness.

## Five Likely Committee Criticisms And Defensible Responses

1. **“Where are the principal comparative results?”** They are not in merged
   main. The only defensible response is to restrict the thesis to prototype
   feasibility or complete the planned evaluation before defense.
2. **“How do you know context adds value beyond intent?”** The repository does
   not know; intent-only is not isolated and no ablation exists. Do not claim an
   incremental context effect.
3. **“Is the baseline fair?”** Resource bands are exactly matched, which is good
   configuration evidence, but profile assignment is manual and unpaired. This
   is insufficient for an outcome comparison.
4. **“Do synthetic standard-library workloads represent real notebooks?”** They
   provide portable, deterministic decision-boundary coverage only. They do not
   reproduce pandas/scikit-learn allocators, real datasets, GPU execution, user
   arrivals, or production scheduling.
5. **“Can I reproduce the reported numbers from a clone?”** There are no
   defensible thesis outcome numbers to reproduce, and the remote main is stale.
   A reviewer can reproduce checks and demos only after the final commits are
   merged/pushed; outcome reproducibility still requires raw results and an
   analysis pipeline.

## Allowed Thesis Claims

- A rule-based prototype maps intent, dataset-size hints, and optional code
  context to Small, Medium, Large, or GPU-or-Large labels.
- The prototype applies the corresponding approved CPU and memory band in a
  KubeSpawner pre-spawn hook.
- The prototype emits a deterministic, derived explanation and does not retain
  raw intent or code context in its added pod metadata/log message.
- Baseline and proposed configurations use the same Small/Medium/Large resource
  bands.
- Twelve synthetic workloads cover documented light, data, ML, boundary,
  conflicting-signal, and policy scenarios and execute deterministically in the
  audited environment.
- Bounded demos illustrate Small-limit OOM and request-based scheduling
  pressure mechanisms.
- GPU-or-Large falls back to Large CPU/memory resources in this demo; it is not
  a GPU provisioning result.

## Prohibited Or Unsupported Claims

- Any quantified or general reduction in OOMs, failures, reruns, restarts,
  pending time, time to success, requested resources, or resource waste.
- Improved cluster utilization, density, throughput, or user experience.
- Superiority over static/manual selection or intent-only selection.
- Predictive accuracy, generalization, or production readiness.
- A fair static comparison or isolated intent-only ablation.
- Real GPU provisioning or general policy enforcement.
- History-aware recommendation or benefit.
- Result reproducibility, statistical significance, or figure reproducibility.

## Unresolved Blockers

1. Merge and publish the audited tree; remote main is stale.
2. Implement the already-scoped static, intent-only, and context-aware method
   runner without changing the architecture.
3. Freeze baseline assignment, trial counts, seeds/order, failure classes, and
   exclusion rules before observing outcomes.
4. Add a versioned result schema and immutable recorder with commit SHA and
   sanitized artifact hashes.
5. Run repeated comparative trials and preserve failures and exclusions.
6. Add a validated usage-metrics source or explicitly restrict analysis to
   request/outcome metrics.
7. Add deterministic result/table/figure regeneration and CI.
8. Obtain and commit the author's software-license decision.

Items 2-7 are outside the permitted final-audit fix scope because they are the
missing evaluation, not correctness patches to an existing evaluation.

## Final Demonstration Commands

### Clean setup and full check

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip check
make check
```

### Deterministic workload smoke

```bash
python -m benchmarks.workload_runner \
  --workload-id light_basic_python \
  --scale tiny \
  --seed 1101
```

Run the commands recorded under each entry in `benchmarks/workloads.yaml` to
smoke all 12 workloads. This is not a method comparison.

### Local Kubernetes mechanics

Run only on an isolated local cluster:

```bash
bash scripts/check-cluster.sh
bash scripts/demo-underprovisioning.sh
kubectl wait --for=jsonpath='{.status.phase}'=Failed \
  pod/underprovision-small -n z2jh-context-demo --timeout=90s
kubectl get pod underprovision-small -n z2jh-context-demo \
  -o jsonpath='{.status.containerStatuses[0].state.terminated.reason}{"\n"}'

bash scripts/demo-overprovisioning.sh
kubectl get pods -n z2jh-context-demo -l demo=overprovisioning

bash scripts/demo-defensive-overrequesting.sh
kubectl logs defensive-large-light -n z2jh-context-demo
```

### Proposed live path

```bash
bash scripts/install-proposed.sh
bash scripts/port-forward.sh
```

Open `http://127.0.0.1:8000`, use any username and any non-empty password, enter
the README training example, and inspect:

```bash
kubectl get pod -n z2jh-context-demo \
  -l component=singleuser-server -o json
```

Inside a JupyterLab terminal:

```bash
python /home/jovyan/demo/workload/train_like_workload.py
```

Cleanup:

```bash
bash scripts/uninstall.sh
```

There is intentionally no result- or figure-regeneration command in this final
command list because no merged comparative result package exists.

## Final Checklist For The Student

- [ ] Keep the thesis claim within the allowed list above unless new merged
  evidence is added.
- [ ] Do not describe intent-only or history-aware behavior as evaluated.
- [ ] Do not present manual mechanism demos as comparative results.
- [ ] Merge and push the audit commit and all intended prerequisites to main.
- [ ] Choose and commit a project software license.
- [ ] Freeze the method matrix, baseline assignment, repetitions, seeds,
  timeouts, failures, exclusions, and units before running experiments.
- [ ] Record expected and actual counts before analyzing outcomes.
- [ ] Preserve failed runs; never silently drop or overwrite outputs.
- [ ] Bind every result to a commit SHA and sanitized environment record.
- [ ] Remove usernames, pod UIDs, node names, hostnames, cluster contexts, raw
  intent, and raw code from committed evidence.
- [ ] Add metrics only after validating the collector and sampling limitations.
- [ ] Regenerate all tables and figures from committed sanitized evidence with
  one documented command.
- [ ] Re-run clean setup, `make check`, the smoke matrix, result regeneration,
  figure regeneration, secret scanning, and `git status` before submission.
- [ ] Leave history-aware provisioning explicitly as future work unless its
  full implementation, privacy design, and evaluation are completed.
