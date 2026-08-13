# Protocol-v4 Stage C Confirmatory Handoff

Last updated: 2026-08-13 (Asia/Ho_Chi_Minh), after implementing safe resume and before the authoritative 320-trial run.

## Exact repository state

- Repository: `/Users/mthang1201/Documents/datn/intent-spawner`
- Branch: `main`, one commit ahead of `origin/main` at inspection time.
- Frozen checkpoint: `b2d7facabb01f64c9495de1aca656c1ebe0686dd` (`Harden Protocol-v4 evaluation`).
- The checkpoint contains the current Protocol-v4 source, tests, documentation, and evidence-related hardening. The worktree was clean when inspected.
- No Stage C, benchmark, pod-creating, Helm install/upgrade, or intent-spawner experiment process was running when inspected.
- Existing historical evidence has not been modified.

## Existing Stage C evidence and plans

- Authoritative one-repeat integration validation: `results/v4-stage-c-validation-v4.2-20260813T013600Z`
  - 32 records = 4 methods x 8 families x 1 repeat.
  - `system-trials.jsonl` SHA-256 documented as `087db516208d5d9774d90752a2821585d9bdd078d2febe6442e0179a97fd354a`.
- Existing full plan: `results/v4-stage-c-plan-20260812T095453Z/system-plan.jsonl`
  - SHA-256: `d45e080621e8b6994de127bfda6e5ec17d778052e78d5325d1fb7cd98e1efa1d`.
  - 240 rows only: `static_small`, `static_large`, and `rule_based_context`, 8 families, 10 repeats.
  - Do not use this plan for the requested four-method confirmatory run.
- Validation plan: `results/v4-stage-c-validation-plan-20260812T102652Z/system-plan.jsonl`
  - SHA-256: `d1b10b973c2f5cfa818f07a5e4dabcc653ac89c092578a02fd8c819f8ef2b81e`.
  - 32 rows, including `self_hosted_local_ollama_llm`.
- New authoritative confirmatory plan: `results/v4-stage-c-confirmatory-plan-20260813T021239Z`
  - 320 rows = 4 methods x 8 workload families x 10 repeats.
  - Methods each have 80 trials; each family has 40 trials; each repeat has 32 trials.
  - 320 unique trial IDs and 80 paired family/repeat blocks; all four methods in every block share one deterministic workload seed.
  - All ten repeat-block execution sequences are distinct deterministic shuffles.
  - `system-plan.jsonl` SHA-256: `718adf39c82023755db3dd60a8d1b4730eaef4fb92ff909f52b2180e957bbf10`.

## Commands already executed

```bash
git status --short --branch
git log -5 --oneline --decorate
git show --stat --oneline --summary HEAD
ps -axo pid,ppid,etime,command | grep -Ei 'stage.?c|benchmark|protocol.?v4|run.*trial|kubectl.*(run|apply)|helm.*(install|upgrade)|intent-spawner'
.venv/bin/python -m evaluation_v4.run_system --help
```

Read-only inspection also counted plan methods/families/repeats and reviewed:

- `docs/evaluation/EVALUATION_V4_PROTOCOL.md`
- `docs/evaluation/PROTOCOL_V4_REPRODUCIBILITY.md`
- `docs/evaluation/STAGE_C_VALIDATION_AND_BLOCKER_REPORT.md`
- `evaluation_v4/plan_system.py`
- `evaluation_v4/run_system.py`
- `evaluation_v4/validate_evidence.py`

`rg` is unavailable; use `find` and `grep`. `shasum` fails because the inherited `C.UTF-8` locale is unavailable; use `LC_ALL=C LANG=C openssl dgst -sha256 <file>`.

## Defect found and fixed before execution

`evaluation_v4.run_system` originally refused an existing output directory and had no resume mode. A strict `--resume` path is now implemented. It validates the experiment ID, plan hash, stable environment identity, completed record schemas/keys/order, sidecar completeness, and cleanup success before skipping only a valid completed prefix. Interrupted attempt directories remain unchanged and a retry uses `--attempt-NN`. Focused tests pass for valid-prefix continuation and rejection of duplicates, plan mismatch, and missing sidecars. No authoritative run has been started yet.

## Active processes / background work

- No experiment or port-forward has been started.
- No PID or process needs cleanup.

## Remaining tasks

1. Commit the resume fix, this handoff, and the new execution plan as a new Protocol-v4 Stage C execution checkpoint so live preflight records a clean Git state.
2. Verify the disposable `orbstack` cluster, labelled namespace, Hub, warm images, local Ollama endpoint/model, and zero synthetic user pods. Start and record the Hub port-forward.
3. Execute the new plan into a new versioned `results/` directory using `--resume` only if interrupted. Never overwrite historical runs.
4. Validate 320 unique completed records, all cleanup sidecars/statuses, `SHA256SUMS`, and failure classifications; analyze with family/repeated-measure-aware inference.
5. Update RQ4/claim documentation only to the extent supported. External LLM credentials remain a separate blocker and must not block Stage C.
6. Run the remaining Helm/Kubernetes/evidence/checksum/duplicate/cleanup/diff/secret validation suite and report final Git status.

## Exact next action

Commit the clean Stage C execution checkpoint, then perform cluster/Ollama/Hub preflight.

## New commands executed after initial inspection

```bash
.venv/bin/python -m pytest -q tests/test_evaluation_v4.py -k 'stage_c or system_plan'
.venv/bin/python -m compileall -q evaluation_v4/run_system.py tests/test_evaluation_v4.py
git diff --check
```

Result: 6 focused tests passed; compilation and diff check passed.

```bash
.venv/bin/python -m evaluation_v4.plan_system \
  --methods static_small,static_large,rule_based_context,self_hosted_local_ollama_llm \
  --repeats 10 --seed 20260808 \
  --output results/v4-stage-c-confirmatory-plan-20260813T021239Z
```

Result: 320-row plan created and the matrix, unique IDs, paired seeds, repeat blocks, deterministic shuffled orders, and SHA-256 were independently asserted.

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q recommender workload scripts evaluation_v4 benchmarks cluster_evaluation experiments tests
bash -n scripts/*.sh
.venv/bin/python -m evaluation_v4.run_system \
  --plan results/v4-stage-c-confirmatory-plan-20260813T021239Z/system-plan.jsonl \
  --experiment-id protocol-v4-stage-c-confirmatory-dry-run --dry-run
git diff --check
```

Result: full pytest, compilation, shell syntax, 320-row executor dry-run, and diff check passed (exit status 0).
