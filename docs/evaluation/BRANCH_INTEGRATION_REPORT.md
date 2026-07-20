# Branch Integration Report

Audit and integration date: 2026-07-20 (Asia/Ho_Chi_Minh)

Integration branch: `codex/integrated-research-artifact`

Integrated merge commit: `9c7aabf69c25f3ca07260dbb4629d2a1ab986680`

The merge commit has parents
`a965ab6a83962cf60733ab94efecd0629f0850a0` and
`266ac3666ed758fd2c298a4d233513f263ffa8ca`. This report is committed after
that merge, so the report-bearing branch-tip hash is necessarily different from
the hash above. A file inside a Git commit cannot contain that commit's own hash
without changing the hash; use `git rev-parse HEAD` for the authoritative
report-bearing tip.

## Outcome

All eight reported commits exist. No known commit was impossible to recover.

The integrated branch uses Chat 7 as the base because it contains the largest
coherent research subset: benchmark execution, the three comparative methods,
immutable instrumentation, the experiment runner, committed raw snapshots,
analysis regeneration, derived results, setup scripts, governance, and
threats-to-validity documentation. Chat 8 was merged as a second parent because
it carries the exact Chat 1-3 history and valid privacy, Kubernetes, portability,
and wording fixes.

Exact known commits included in the integrated ancestry:

- Chat 1: `e5508d9f35d6e657145c3cf18291f521883528cb`
- Chat 2: `2bfc07e58d1aca39fbd2989ae9a2738d49233eef`
- Chat 3: `8c1ca92755f09612bb3d8b4f2b601285f95b5d7e`
- Chat 7: `a965ab6a83962cf60733ab94efecd0629f0850a0`
- Chat 8: `266ac3666ed758fd2c298a4d233513f263ffa8ca`

Known commits whose work is present through equivalent or superseding commits,
rather than as ancestors:

- Chat 4 `d6f8d71`: superseded by the comparable-runner implementation.
- Chat 5 `6948ee4`: its experiment component is reproduced on the Chat 7 line
  by `3b8c5a9`.
- Chat 6 `6c19a1a`: its complete tree is byte-identical to `d0bfe3e`, the
  parent of Chat 7.

## Preserved Phase 1 Git Output

The initial worktree was clean on `codex/final-audit`. The exact compact
status captured before switching branches was:

```text
## codex/final-audit...origin/main [ahead 4]
```

The configured remote was:

```text
origin  https://github.com/mthang1201/intent-spawner.git (fetch)
origin  https://github.com/mthang1201/intent-spawner.git (push)
```

The initial branch inventory was:

```text
  codex/comparable-evaluation-runner 6948ee4 experiments: add comparable evaluation runner
  codex/evaluation-analysis-run      6c19a1a [origin/main: ahead 5] results: add reproducible local evaluation analysis
  codex/experiment-instrumentation   d6f8d71 experiments: add immutable result instrumentation
* codex/final-audit                  266ac36 [origin/main: ahead 4] audit: verify thesis claims and reproducibility
  codex/reproducibility-artifact     a965ab6 [origin/main: ahead 5] docs: package reproducible research artifact
  main                               8c1ca92 [origin/main: ahead 3] benchmarks: add deterministic workload suite
```

The initial all-reference graph was:

```text
* 266ac36 (HEAD -> codex/final-audit) audit: verify thesis claims and reproducibility
| * a965ab6 (codex/reproducibility-artifact) docs: package reproducible research artifact
| * d0bfe3e results: add reproducible local evaluation analysis
| * 11d8cc5 analysis: capture evaluation environment capability
| * 3b8c5a9 experiments: add comparable evaluation runner
| * 061dedf benchmarks: add deterministic workload suite
| | * 6c19a1a (codex/evaluation-analysis-run) results: add reproducible local evaluation analysis
| | * fea6204 analysis: capture evaluation environment capability
| | * 2c37b98 analysis: add reproducible evaluation outputs
| | * dedb93f experiments: add comparable evaluation runner
| | * 2bd4167 benchmarks: add deterministic workload suite
| |/
| | * 6948ee4 (codex/comparable-evaluation-runner) experiments: add comparable evaluation runner
| |/
|/|
| | * d6f8d71 (codex/experiment-instrumentation) experiments: add immutable result instrumentation
| |/
|/|
* | 8c1ca92 (main) benchmarks: add deterministic workload suite
* | 2bfc07e test: stabilize prototype verification
* | e5508d9 docs: add evaluation implementation roadmap
|/
* 96a2916 (origin/main) first commit
```

After the integration merge, exact `git status` output was:

```text
On branch codex/integrated-research-artifact
nothing to commit, working tree clean
```

## Ancestry Audit

The known-commit ancestor matrix below uses rows as possible ancestors and
columns in Chat 1-8 order. `Y` means
`git merge-base --is-ancestor <row> <column>` succeeded.

```text
Chat 1  Y Y Y Y Y . . Y
Chat 2  . Y Y Y Y . . Y
Chat 3  . . Y Y Y . . Y
Chat 4  . . . Y . . . .
Chat 5  . . . . Y . . .
Chat 6  . . . . . Y . .
Chat 7  . . . . . . Y .
Chat 8  . . . . . . . Y
```

The numbered chats are therefore not a linear sequence. Chats 4, 5, and 8 are
siblings with Chat 3 as parent. Chats 6 and 7 are on separately replayed
histories originating at the initial commit.

### Per-commit findings

| Chat | Commit and parent | Branches containing it before integration | Changed-file summary | Disposition |
| --- | --- | --- | --- | --- |
| 1 | `e5508d9` ← `96a2916` | comparable runner, instrumentation, final audit, main | Added `AGENTS.md` and the initial implementation roadmap. | Included exactly through Chat 8. |
| 2 | `2bfc07e` ← `e5508d9` | comparable runner, instrumentation, final audit, main | Added Make/check/config validation; stabilized recommender parsing, Helm behavior, tests, README, and dependencies. | Included exactly through Chat 8. |
| 3 | `8c1ca92` ← `2bfc07e` | comparable runner, instrumentation, final audit, main | Added benchmark package, 12-workload manifest, benchmark design, runner, and tests. | Included exactly through Chat 8; the replayed `061dedf` benchmark on Chat 7 was reconciled with it. |
| 4 | `d6f8d71` ← `8c1ca92` | instrumentation | Added result schema, recorder, JSONL/CSV helpers, Kubernetes evidence fixtures/parsers, raw/summaries policies, and instrumentation tests. | Not an ancestor; superseded by Chat 5's larger runner commit and the equivalent Chat 7 implementation. |
| 5 | `6948ee4` ← `8c1ca92` | comparable runner | Added the Chat 4 instrumentation plus method separation, experiment protocol, runner, and runner tests. | Not an ancestor; `3b8c5a9` on Chat 7 has identical experiment files. |
| 6 | `6c19a1a` ← `fea6204` | evaluation-analysis run | Added/updated analysis code, `RESULTS.md`, 12 CSV tables, four SVG figures, and environment capability evidence. | Not an ancestor; `git diff 6c19a1a d0bfe3e` is empty, so Chat 7 already contains an identical tree. |
| 7 | `a965ab6` ← `d0bfe3e` | reproducibility artifact | Added artifact/governance/threats documentation, setup and environment scripts, requirements, and 384 files dominated by sanitized preserved raw snapshots. | Selected base and included exactly. |
| 8 | `266ac36` ← `8c1ca92` | final audit | Added the historical final audit and governance document; fixed images, privacy, demo safety, Kubernetes fixtures/names, RSS units, immutable metadata, and misleading claims; removed notebooks and generated capacity values. | Merged exactly as second parent, with stale absence claims explicitly marked historical. |

### Duplicate and superseded work

- `d6f8d71` and `6948ee4` start at the same parent. Chat 5 is the justified
  superset: it retains the instrumentation interface and adds the three-method
  runner and protocol.
- `6948ee4` and `3b8c5a9` differ in unrelated ancestor files, but their
  experiment implementation is identical. Retaining both implementations would
  duplicate the same component.
- `6c19a1a` and `d0bfe3e` have identical complete trees. Chat 6 is therefore
  fully superseded by Chat 7's parent.
- `8c1ca92` and `061dedf` independently add the benchmark. The merge retains
  one benchmark implementation, Chat 2's stabilization/config tests, and Chat
  8's portable RSS and immutable metadata fixes.
- No unrecoverable commit or artifact was found.

## Conflict Record

The merge base was `96a29164001f33e391227a16c1963fac6945c1ff`.
Nine paths had Git conflicts, followed by one semantic compatibility failure
found by tests.

| Path | Resolution |
| --- | --- |
| `.gitignore` | Combined Chat 7's committed-raw allowlist and generated-summary policy with Chat 8's ignore rule for machine-specific `helm/generated-values.yaml`. |
| `README.md` | Kept the implemented setup/runner/regeneration instructions. Added Chat 8's local-only DummyAuthenticator warning, removed the deleted notebooks from the layout, and rejected stale “no results” wording. |
| `benchmarks/workload_runner.py` | Used Chat 8's cross-platform `max_rss_bytes` output and exclusive-create metadata output. |
| `docs/DATA_GOVERNANCE.md` | Kept Chat 7's experiment governance and retention rules; added Chat 8's in-memory input handling, local-only authentication, operational-identifier, and unresolved-license boundaries. |
| `docs/evaluation/BENCHMARK_DESIGN.md` | Kept current three-method/local-runner facts, retained the stronger Kubernetes claim boundary, and removed stale statements that method isolation/results were absent. |
| `docs/evaluation/IMPLEMENTATION_ROADMAP.md` | Replaced the mutually stale documents with a current integrated capability matrix and future-work boundary. History-aware remains excluded. |
| `requirements-dev.txt` | Kept the runtime requirements include and added Chat 8's exact pytest dependency pins. |
| `scripts/check.sh` | Kept the superset check covering benchmarks, experiments, setup/environment scripts, Helm, and Kubernetes manifests. |
| `tests/test_benchmark_workloads.py` | Kept all prior benchmark tests and added Chat 8's byte-unit and no-overwrite tests. |
| `experiments/recorder.py` | The first full check exposed use of the removed `max_rss_platform_units` field. The recorder now converts `max_rss_bytes` directly to MiB. The failing smoke-record test and all other tests then passed. |

The historical `FINAL_AUDIT.md` is retained with a prominent notice that it
audited the stale Chat 8 tree. This preserves its provenance without presenting
its absence findings as current facts.

## Integrated Artifact Checks

All required paths exist:

- `AGENTS.md`
- `Makefile`
- `benchmarks/workloads.yaml`
- `benchmarks/workload_runner.py`
- `experiments/methods.py`
- `experiments/runner.py`
- `experiments/result_schema.schema.json`
- `docs/evaluation/IMPLEMENTATION_ROADMAP.md`
- `docs/evaluation/BENCHMARK_DESIGN.md`
- `docs/evaluation/RESULT_SCHEMA.md`
- `docs/evaluation/EXPERIMENT_PROTOCOL.md`
- `docs/evaluation/RESULTS.md`
- `docs/evaluation/THREATS_TO_VALIDITY.md`
- `docs/evaluation/FINAL_AUDIT.md`
- `docs/DATA_GOVERNANCE.md`
- `results/`

Coherence findings:

- `METHODS` is exactly `static_manual`, `intent_only`, and
  `context_aware`.
- Intent-only passes `dataset_size_gb=0.0` and `code_context=""` to the
  recommender. A mutation test proves dataset-size and code-context changes do
  not affect its decision or context summary.
- Context-aware passes the documented intent, dataset-size hint, and joined
  code-context hints.
- History-aware has no method enum, runner path, result record, or claimed
  evaluation; it is future work only.
- Raw result JSONL and workload artifacts use append or exclusive-create
  semantics. Tests verify duplicate output is refused.
- Derived CSV/SVG/Markdown outputs are regenerated from a preserved raw
  experiment directory and never replace raw inputs.
- Kubernetes fixtures use synthetic names, omit UIDs/owner references/node-name
  fields, and exercise allowlist filtering with synthetic sensitive sentinels.
- Searches of tracked evidence found no email addresses, home-directory user
  paths, private-key headers, bearer/access-token patterns, pod UIDs,
  `nodeName`, or owner references. No real credentials or user identifiers are
  committed.

## Verification Results

No new comparative evaluation was run. The only newly generated experiment
record was the requested bounded smoke verification, stored in an ignored
directory and not used as research evidence.

| Check | Result |
| --- | --- |
| `bash scripts/setup.sh` | Pass; pinned dependencies available in `.venv`. |
| `bash scripts/check.sh`, final run | Pass: 6 passed checks, 0 failed, 2 intentionally skipped. |
| All Python tests | 50 passed. |
| Benchmark manifest/runner tests | 10 passed. |
| Python compilation | Pass. |
| Shell syntax | Pass. |
| Full-matrix dry run | Pass; 36 planned records, 0 attempted. |
| One smoke experiment | Pass; 1/1 attempted, successful, schema-valid, no timeout. |
| Analysis regeneration | Pass; 12 CSVs, four SVGs, and one Markdown report generated from the committed 180-record raw matrix. |
| Derived comparison | All common regenerated CSV/SVG files byte-identical to committed outputs. Markdown differed only because the verification used an absolute temporary results path. |
| Baseline Helm render | Pass with JupyterHub chart 4.0.0. |
| Proposed Helm render | Pass with JupyterHub chart 4.0.0. |
| Kubernetes client dry-runs | Pass for idle-large, idle-small, and ResourceQuota manifests. |
| Diff checks | `git diff --check` and `git diff --cached --check` passed. |
| Cluster-mutating demos | Intentionally not run. |
| Read-only live cluster check | Not requested by `scripts/check.sh`; skipped unless `RUN_CLUSTER_CHECKS=1`. |

The first `scripts/check.sh` attempt reported 49 passed and one failed test
because of the RSS field mismatch described above. After the recorder fix, the
final run passed all 50 tests.

## Missing Or Deliberately Excluded Artifacts

No required integrated file is missing.

Deliberately absent or unresolved:

- no history-aware method or history store;
- no new live Kubernetes comparative evaluation;
- no reliable live Kubernetes peak-usage evidence in the preserved environment;
- no project software license;
- no claim that the local synthetic result generalizes to production users or
  clusters.

## Was Chat 8 Stale?

Yes. Chat 8's parent is `8c1ca92`, while Chats 5-7 were on other branches.
Its own audit states that it deliberately excluded newer unmerged topic
branches. Its “runner/results/schema absent” conclusions were accurate for the
tree it audited, but stale relative to the recoverable repository as a whole.
The integrated branch preserves Chat 8's valid fixes and marks its absence
findings as historical.

## Reproduction Commands

This branch has not been pushed or merged into `origin/main`. In a repository
that contains the local commits, reproduce the integrated implementation tree
with:

```bash
git switch --detach 9c7aabf69c25f3ca07260dbb4629d2a1ab986680
git status
```

To inspect the report-bearing final branch tip after this report commit:

```bash
git switch codex/integrated-research-artifact
git rev-parse HEAD
git status
```

To reconstruct the topology before applying the documented resolutions:

```bash
git switch -c codex/integrated-research-artifact-reconstruction \
  a965ab6a83962cf60733ab94efecd0629f0850a0
git merge --no-ff 266ac3666ed758fd2c298a4d233513f263ffa8ca
```

Run the integrated verification:

```bash
bash scripts/setup.sh
bash scripts/check.sh
.venv/bin/python -m pytest
.venv/bin/python -m pytest tests/test_benchmark_workloads.py

.venv/bin/python -m experiments.runner \
  --full-matrix --repeats 1 --seed 20260720 --dry-run \
  --environment-id integration-dry-run

.venv/bin/python -m experiments.runner \
  --smoke --seed 20260720 --timeout 60 \
  --environment-id integration-smoke

analysis_dir=$(mktemp -d /tmp/intent-spawner-integration-analysis.XXXXXX)
.venv/bin/python -m experiments.analyze_results \
  --experiment-dir experiments/raw/20260719T140431Z-matrix-aed48949 \
  --results-dir "$analysis_dir/results" \
  --results-md "$analysis_dir/RESULTS.md" \
  --environment-report results/environment-capability.json \
  --overwrite

diff -qr results "$analysis_dir/results"
git diff --check
git status --short --ignored
```

The expected `diff -qr` output is only
`Only in results: environment-capability.json`, because that capability report
is supplied as an input to Markdown generation rather than regenerated into the
temporary results directory.
