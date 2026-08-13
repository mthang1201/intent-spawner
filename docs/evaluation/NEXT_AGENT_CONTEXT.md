# Protocol-v4 Stage C Confirmatory Handoff

Last updated: 2026-08-13 09:26 Asia/Ho_Chi_Minh, after authoritative Stage C preflight and immediately before execution.

## Exact repository state

- Repository: `/Users/mthang1201/Documents/datn/intent-spawner`
- Branch: `main`, one commit ahead of `origin/main` at inspection time.
- Frozen checkpoint: `b2d7facabb01f64c9495de1aca656c1ebe0686dd` (`Harden Protocol-v4 evaluation`).
- Stage C execution checkpoint: `83cb4af` (`Checkpoint Protocol-v4 Stage C execution`). This contains strict resume support, tests, reproducibility documentation, this handoff, and the new 320-row plan.
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

- Hub port-forward is active:
  - PID: `66540`
  - Codex exec session ID: `47436`
  - Command: `kubectl --context orbstack -n z2jh-context-demo port-forward service/proxy-public 18000:80`
  - Output/health: listening on `127.0.0.1:18000` and `[::1]:18000`; `GET /hub/health` returned HTTP 200.
  - Safe stop: send Ctrl-C to exec session `47436`, or `kill 66540` after verifying the exact command.
- No Stage C experiment process has started yet.

## Live preflight observations

- Current Kubernetes context: `orbstack`.
- Namespace `z2jh-context-demo` has `z2jh-context-demo.local/disposable-experiment-v4=true`.
- One node, `Ready`, Kubernetes `v1.33.9+orb1`, OrbStack/docker runtime.
- Hub deployment is 1/1 available; Helm release `context-demo` is deployed at chart 4.0.0 / JupyterHub 5.2.1.
- No pod with `component=singleuser-server` exists.
- Local Ollama is reachable on `127.0.0.1:11434` and has `llama3:latest` (digest begins `365c0bd3...`).
- An old unrelated `idle-large-example` pod is in `Error`; it is not running and is not a single-user/Stage C pod. It was not deleted.
- The exact 320-row executor `--preflight-only` command passed at `2026-08-13T02:25:39.860Z`.
- Preflight recorded Git commit `bb4c5f34ec5835d57634b3fcae8a358a86ea3a07` with `git_dirty=false`, plan SHA-256 `718adf39...bf10`, warm digests for `minimal-python` and `scipy-data-science`, 8 CPU / 8,185,712 KiB allocatable memory, no Metrics API, cgroup-v2 window metrics, no HPA, and no resource quota.
- All full-plan local Ollama reliability checks completed without fallback; model/prompt were `llama3:latest` / `prompt-v4.1.0`.

## Remaining tasks

1. Commit this successful preflight handoff so execution records a clean Git state.
2. Execute the new plan into `results/v4-stage-c-confirmatory-20260813T021600Z`; use `--resume` only if interrupted. Never overwrite historical runs.
3. Validate 320 unique completed records, all cleanup sidecars/statuses, `SHA256SUMS`, and failure classifications; analyze with family/repeated-measure-aware inference.
4. Update RQ4/claim documentation only to the extent supported. External LLM credentials remain a separate blocker and must not block Stage C.
5. Run the remaining Helm/Kubernetes/evidence/checksum/duplicate/cleanup/diff/secret validation suite and report final Git status.

## Exact next action

Commit this handoff update, then execute the exact Stage C command below.

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

```bash
.venv/bin/python -m evaluation_v4.run_system \
  --plan results/v4-stage-c-confirmatory-plan-20260813T021239Z/system-plan.jsonl \
  --experiment-id protocol-v4-stage-c-confirmatory-20260813T021600Z \
  --context orbstack --hub-url http://127.0.0.1:18000 \
  --ollama-endpoint http://127.0.0.1:11434/api/chat \
  --ollama-model llama3:latest --ollama-prompt-version prompt-v4.1.0 \
  --ollama-temperature 0 --ollama-timeout 60 --preflight-only
```

If preflight passes, use the exact same arguments with:

```bash
--output results/v4-stage-c-confirmatory-20260813T021600Z --execute
```

The preflight command passed with exit status 0 after approximately eight minutes. The exact next full command is:

```bash
.venv/bin/python -m evaluation_v4.run_system \
  --plan results/v4-stage-c-confirmatory-plan-20260813T021239Z/system-plan.jsonl \
  --experiment-id protocol-v4-stage-c-confirmatory-20260813T021600Z \
  --context orbstack --hub-url http://127.0.0.1:18000 \
  --ollama-endpoint http://127.0.0.1:11434/api/chat \
  --ollama-model llama3:latest --ollama-prompt-version prompt-v4.1.0 \
  --ollama-temperature 0 --ollama-timeout 60 \
  --output results/v4-stage-c-confirmatory-20260813T021600Z --execute
```
