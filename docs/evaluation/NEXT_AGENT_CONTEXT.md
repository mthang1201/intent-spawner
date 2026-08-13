# Protocol-v4 Stage C Confirmatory Handoff

Last updated: 2026-08-13 11:35 Asia/Ho_Chi_Minh. Stage C and analysis are complete.

## Exact current state

- Repository: `/Users/mthang1201/Documents/datn/intent-spawner`
- Branch: `main`, seven commits ahead of `origin/main` before this handoff update.
- Current analytical checkpoint: `02ed23b` (`Analyze Protocol-v4 Stage C confirmatory results`).
- Execution-source commit frozen in the observed run: `99707b8da8e4c065a1a451332f8555193614144a`, clean worktree.
- Earlier checkpoints: `b2d7fac` hardened Protocol-v4; `83cb4af` added strict resume and the 320-row plan.
- Historical evidence was not modified. The new run and analysis are under ignored `results/` paths and remain local, append-only artifacts.
- No Stage C executor, benchmark, or port-forward process remains. No synthetic single-user pod remains.

## Authoritative plan, run, and analysis

- Plan: `results/v4-stage-c-confirmatory-plan-20260813T021239Z`
  - 320 rows = 4 methods x 8 workload families x 10 runtime repeats.
  - Seed `20260808`; randomized within repeat blocks; all methods share a deterministic seed in each family/repeat block.
  - 320 unique trial IDs; 80 paired family/repeat blocks; ten distinct deterministic shuffled sequences.
  - Plan SHA-256: `718adf39c82023755db3dd60a8d1b4730eaef4fb92ff909f52b2180e957bbf10`.
- Observed run: `results/v4-stage-c-confirmatory-20260813T021600Z`
  - Experiment ID: `protocol-v4-stage-c-confirmatory-20260813T021600Z`.
  - Completed without interruption/resume: 320/320 records and 320 unique IDs.
  - `system-trials.jsonl` SHA-256: `a76a334f74cd0dc928ce158f87106bc6f8576a17ec518ed0ef756cbbd61ff256`.
  - 2,244 finalized files are covered by `SHA256SUMS`; all verified.
  - All 320 records match the plan in exact order and have six supporting sidecars, trial metadata, and cleanup `completed`.
  - No retry directories or `resume-events.jsonl` exist.
- Final analysis: `results/v4-stage-c-confirmatory-analysis-v3-20260813T054000Z`
  - Inputs bind the 960-row validated Stage B stream and the exact Stage C SHA-256 above.
  - 10,000 workload-family bootstrap replicates, seed `20260812`.
  - 23 files covered by its `SHA256SUMS`; all verified.
  - Includes `system-family-paired.csv`, where ten repeats are aggregated within each of eight families before exact tests.
- Human-readable result: `docs/evaluation/STAGE_C_CONFIRMATORY_REPORT.md`.

## Exact outcomes

| Method | Trials | Success | OOM | Timeout | Mean CPU request | Mean memory request |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `static_small` | 80 | 29 | 50 | 1 | 100m | 256 MiB |
| `static_large` | 80 | 80 | 0 | 0 | 1,500m | 1,536 MiB |
| `rule_based_context` | 80 | 50 | 30 | 0 | 562.5m | 800 MiB |
| `self_hosted_local_ollama_llm` | 80 | 50 | 30 | 0 | 500m | 768 MiB |

- All 320 pods spawned and became Ready; Pending, unschedulable, and image-pull failures were 0.
- Fallback was 0/320. Cleanup completed 320/320. Successful cgroup measurement windows: 209/320.
- Rule-based reduced mean requests versus static-large by 937.5m CPU (62.5%) and 736 MiB memory (47.9%). Ollama reduced them by 1,000m CPU (66.7%) and 768 MiB memory (50%). Both adaptive methods traded that saving for 37.5 percentage points lower success and 37.5 points more OOM.
- On five shared successful families, static-large had lower CPU/request, memory/request, and peak-memory/request utilization than either adaptive method. These comparisons are survivor-conditioned.
- Eight-family exact success/OOM tests were not significant after Holm correction; only three families were discordant (raw p=0.25, Holm p=1.0). Family-bootstrap effect intervals favored static-large success, but they do not override the conservative exact test.
- Static-large CPU allocation exceeded rule-based by 937.5m (Holm p=0.046875) and Ollama by 1,000m (Holm p=0.046875). Its memory excess versus rule-based was 736 MiB (Holm p=0.0625) and versus Ollama 768 MiB (Holm p=0.046875).
- Revised applied-system RQ4 is **CLAIMABLE** because the preregistered matrix is complete. H4 is directionally/operationally supported but is not a confirmed family-level significance claim.

## Defects fixed during continuation

1. `evaluation_v4.run_system` had no interruption-safe resume. `--resume` now validates experiment ID, plan hash, frozen environment, exact schema-valid completed prefix, sidecars, cleanup, final checksums, and duplicates; interrupted attempt directories are retained.
2. Stage C binary inference used 80 correlated runtime rows. Trial McNemar output is now labeled descriptive, and `system-family-paired.csv` aggregates repeats within eight workload families for exact Wilcoxon/Holm inference.
3. Stage C summaries now include absolute CPU/memory allocations and observed usage, and analysis directories now receive a verified `SHA256SUMS`.

## Commands executed and validation results

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q recommender workload scripts evaluation_v4 benchmarks cluster_evaluation experiments tests
bash -n scripts/*.sh
git diff --check
```

Passed after the final analysis changes.

```bash
helm template context-demo jupyterhub/jupyterhub --version 4.0.0 \
  --namespace z2jh-context-demo --values helm/baseline-values.yaml
helm template context-demo jupyterhub/jupyterhub --version 4.0.0 \
  --namespace z2jh-context-demo --values helm/proposed-values.yaml
kubectl apply --dry-run=client -f k8s/idle-large-pod.yaml
kubectl apply --dry-run=client -f k8s/idle-small-pod.yaml
kubectl apply --dry-run=client -f k8s/resource-quota.yaml
```

Both Helm renders and all three client dry-runs passed.

```bash
.venv/bin/python scripts/validate_secret_refs.py --values helm/baseline-values.yaml --namespace z2jh-context-demo
.venv/bin/python scripts/validate_secret_refs.py --values helm/proposed-values.yaml --namespace z2jh-context-demo
.venv/bin/python -m pytest -q tests/test_historical_evidence_immutability.py
.venv/bin/python -m evaluation_v4.validate_evidence \
  --dir results/v4-revised-test-20260812T095453Z \
  --analysis-dir results/v4-stage-c-confirmatory-analysis-v3-20260813T054000Z
```

Both secret-reference checks, historical evidence immutability, and the evidence/analysis validator passed. A tracked-file credential-pattern scan covered 2,108 files with zero hits. Raw Stage C duplicate, plan-order, schema, sidecar, cleanup, checksum, completion-manifest, and pod-leak checks passed.

## Remaining blockers and tasks

- Stage C has no remaining work.
- External LLM remains blocked by missing secure provider credentials/configuration. It does not block Stage C and no external-vs-local empirical claim is supported.
- User acceptance and re-provisioning evidence remain separate streams and were not created by this task.
- The seven-plus local commits are not pushed. The 95 MiB raw run and 356 KiB analysis remain ignored local artifacts by repository policy; do not delete them.

## Exact next action

Do not rerun Stage C. Review `docs/evaluation/STAGE_C_CONFIRMATORY_REPORT.md` and, if repository publication is desired, push the current `main` commits and separately archive the ignored run and analysis directories with their checksum manifests. Only resume external-LLM evaluation after credentials are securely configured; never substitute fabricated output.
