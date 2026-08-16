# Protocol-v4 Execution and Evaluation Handoff Log

Current completion note (2026-08-16): Stage C, the amended external-LLM
matrix, the derived four-method evidence, combined analysis, validation, and
checkpoint commits are complete. There are no remaining Protocol-v4 execution
or analysis blockers. The latest implementation checkpoint also fixes the
recommendation-preview JavaScript matcher and adds a regression assertion.

Authoritative current summaries:

- `PROTOCOL_V4_REVISED_EVALUATION_REPORT.md` for the combined RQ1-RQ5 claim
  matrix;
- `PROTOCOL_V4_EXTERNAL_LLM_LIVE_REPORT.md` for external raw/applied outcomes;
- `STAGE_C_CONFIRMATORY_REPORT.md` for observed cluster effects; and
- `PROTOCOL_V4_REPRODUCIBILITY.md` for safe validation/reproduction commands.

The remainder of this file is an append-only chronological handoff log. Older
“blocked” and “next action” sections are intentionally preserved as historical
state and are superseded by later entries and the completion note above.

## Historical Stage C handoff (2026-08-13 12:05 Asia/Ho_Chi_Minh)

### Stage C checkpoint state

- Repository: `/Users/mthang1201/Documents/datn/intent-spawner`
- Branch: `main`, seven commits ahead of `origin/main` before this handoff update.
- Current analytical checkpoint: `02ed23b` (`Analyze Protocol-v4 Stage C confirmatory results`).
- Execution-source commit frozen in the observed run: `99707b8da8e4c065a1a451332f8555193614144a`, clean worktree.
- Earlier checkpoints: `b2d7fac` hardened Protocol-v4; `83cb4af` added strict resume and the 320-row plan.
- Historical evidence was not modified. The new run and analysis are under ignored `results/` paths and remain local, append-only artifacts.
- No Stage C executor, benchmark, or port-forward process remains. No synthetic single-user pod remains.

### Authoritative plan, run, and analysis

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

### Exact outcomes

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

### Defects fixed during continuation

1. `evaluation_v4.run_system` had no interruption-safe resume. `--resume` now validates experiment ID, plan hash, frozen environment, exact schema-valid completed prefix, sidecars, cleanup, final checksums, and duplicates; interrupted attempt directories are retained.
2. Stage C binary inference used 80 correlated runtime rows. Trial McNemar output is now labeled descriptive, and `system-family-paired.csv` aggregates repeats within eight workload families for exact Wilcoxon/Holm inference.
3. Stage C summaries now include absolute CPU/memory allocations and observed usage, and analysis directories now receive a verified `SHA256SUMS`.

### Commands executed and validation results

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

### Historical Stage C-only blockers and tasks

- Stage C has no remaining work.
- External LLM remains blocked by missing secure provider credentials/configuration. It does not block Stage C and no external-vs-local empirical claim is supported.
- User acceptance and re-provisioning evidence remain separate streams and were not created by this task.
- The seven-plus local commits are not pushed. The 95 MiB raw run and 356 KiB analysis remain ignored local artifacts by repository policy; do not delete them.

### Historical next action

Do not rerun Stage C. Review `docs/evaluation/STAGE_C_CONFIRMATORY_REPORT.md` and, if repository publication is desired, push the current `main` commits and separately archive the ignored run and analysis directories with their checksum manifests. Only resume external-LLM evaluation after credentials are securely configured; never substitute fabricated output.

## External LLM credential/backend verification (2026-08-13)

- Credential source: the existing Kubernetes Secret `intent-spawner-external-llm` in `z2jh-context-demo`; only key presence was displayed and the value was injected directly into subprocess environments. The key was not printed or written to evidence.
- Frozen configuration from `helm/gemini-values.yaml`: Google Gemini, model `gemini-2.0-flash`, HTTPS OpenAI-compatible chat-completions endpoint, prompt `prompt-v4.1.0` (repository default), temperature 0, 10-second attempt timeout, 30-second total timeout, two configured retries, and 0.25-second initial backoff. None was changed.
- Required endpoint/model/key values were present in the verification subprocess and passed `ExternalLLMConfig.from_environ()` validation.
- A minimal request reached the provider host but returned HTTP 404 in 0.202 seconds. It was not a 401/403 rejection, but because the 404 had no parseable provider error message, authentication success cannot be positively established and the evidence cannot distinguish a retired `gemini-2.0-flash` model from an unavailable frozen endpoint route.
- One existing development sample, `basic-python-canonical-en`, traversed the actual `evaluation_v4` -> `external_llm` pipeline. All three attempts ended as `transport_error`; the pipeline returned the rule-based fallback after 1.498 seconds. No raw response or raw LLM prediction existed, so schema validity was not established. The fallback applied `small` and was policy-compliant; it must not be credited as an external-LLM prediction.
- Token counts, provider inference latency, and cost were unavailable because no completion was returned. Captured telemetry: pipeline total latency 1.498 seconds, attempt count 3, retry used yes, fallback used yes; direct HTTP diagnostic latency 0.202 seconds.
- No development or held-out evaluation matrix was run. In particular, the held-out `test` split was not accessed by an evaluator.
- Post-check secret scan examined 45,048 repository files for the exact Kubernetes Secret value and found zero occurrences. A generic scan covered 2,109 tracked/unignored files; its matches were reviewed as documented placeholders, test fixtures, or ordinary program variables rather than live credentials. `gitleaks` was unavailable. `git diff --check` and 44 focused external/four-method tests passed.

### Exact next action

The provider/configuration owner must confirm whether the frozen endpoint route or frozen `gemini-2.0-flash` model is unavailable/deprecated. Do not select another endpoint or model without an explicit protocol decision. After the frozen configuration returns a schema-valid development response, run the full external evaluation exactly as follows (the key remains sourced from the existing Secret and is never placed in a CLI argument):

```bash
EXTERNAL_LLM_API_KEY="$(kubectl -n z2jh-context-demo get secret intent-spawner-external-llm -o jsonpath='{.data.api-key}' | base64 --decode)" \
RECOMMENDER_BACKEND=external_llm \
EXTERNAL_LLM_ENDPOINT='https://generativelanguage.googleapis.com/v1beta/openai/chat/completions' \
EXTERNAL_LLM_MODEL='gemini-3.5-flash' \
EXTERNAL_LLM_TIMEOUT='10' EXTERNAL_LLM_TOTAL_TIMEOUT='30' \
EXTERNAL_LLM_MAX_RETRIES='2' EXTERNAL_LLM_RETRY_BACKOFF_SECONDS='0.25' \
EXTERNAL_LLM_TEMPERATURE='0' EXTERNAL_LLM_MAX_CONCURRENT_RECOMMENDATIONS='4' \
EXTERNAL_LLM_ALLOW_INSECURE_HTTP='false' \
.venv/bin/python -m evaluation_v4.run_recommenders \
  --recommenders external_llm \
  --split test --repeats 5 --seed 20260808 --randomize-order \
  --prompt-version prompt-v4.1.0 \
  --experiment-id protocol-v4-external-confirmatory \
  --output results/v4-external-confirmatory
```

## Explicit external-LLM model amendment and development gate (2026-08-13)

Official Google Gemini documentation now verifies the old HTTP 404 root cause:

- Google lists `gemini-2.0-flash` and `gemini-2.0-flash-001` as shut down on 2026-06-01. This is an externally imposed protocol blocker, not an experimental failure. Sources: [Gemini deprecations](https://ai.google.dev/gemini-api/docs/deprecations) and [Gemini API release notes](https://ai.google.dev/gemini-api/docs/changelog).
- Google still documents `https://generativelanguage.googleapis.com/v1beta/openai/` as the OpenAI-compatible base URL and `POST /chat/completions` as valid. Source: [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai).
- The operator explicitly selected the stable concrete model `gemini-3.5-flash`; a moving `gemini-flash-latest` alias is not used. Google documents `gemini-3.5-flash` as stable. Source: [Gemini 3.5 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash).

The explicit pre-held-out protocol amendment is model-only: old frozen model `gemini-2.0-flash` -> new frozen model `gemini-3.5-flash`. The replacement is externally imposed because the provider shut down the old model before any held-out external-LLM trial. Endpoint, prompt `prompt-v4.1.0`, request schema, temperature 0, 10-second per-attempt timeout, 30-second total timeout, retry count 2, 0.25-second initial backoff, concurrency, policy, scoring, profiles, labels, and fallback behavior remain unchanged.

An earlier non-amended `gemini-3.6-flash` probe timed out and is superseded by the explicit `gemini-3.5-flash` amendment. The required `gemini-3.5-flash` development gate result is recorded below after execution. Zero held-out external-LLM trials were run before the amendment.

Direct amended-model gate result: the existing Kubernetes Secret authenticated successfully; the unchanged endpoint returned HTTP 200 in 0.998 seconds and identified both the requested and response model as `gemini-3.5-flash`. The OpenAI-compatible `choices` envelope parsed, and usage reported 6 prompt tokens, 0 completion tokens, and 18 total tokens. However, no non-empty assistant content could be extracted, so the direct gate failed at response-content validation. This was not an authentication, endpoint, model-unavailable, or transport failure.

Per the required stop rule, `basic-python-canonical-en` was not run through the repository pipeline, focused tests were not run after this live probe, and no confirmatory evaluation was run. Raw prediction, recommendation schema validity, fallback, retry, and pipeline telemetry therefore remain unverified for `gemini-3.5-flash`. Zero held-out external-LLM trials have been executed.

Exact next action: diagnose why the valid HTTP 200 chat-completions envelope contained no assistant content while preserving the frozen endpoint, prompt version, temperature, timeouts, retries, schema, and policy. Repeat only the direct development gate. Do not run the canonical pipeline sample or confirmatory command until a non-empty assistant response is extracted.

## Active continuation: external gate diagnosis (2026-08-13)

- The current operator accepted the explicit `gemini-3.5-flash` model amendment and requested completion of the external evaluation.
- No held-out external trial has been started in this continuation.
- The previous direct gate still blocks execution because it did not return non-empty assistant content; schema parsing and pipeline telemetry are therefore not yet verified.
- Official Gemini documentation was checked for the amended model. It confirms `gemini-3.5-flash` is a stable model with structured-output and thinking support, and documents the existing OpenAI-compatible chat-completions endpoint. Gemini 3.5 defaults to medium thinking; Google recommends `reasoning_effort`/`thinking_level` controls for OpenAI-compatible requests, but the frozen temperature and prompt/schema/configuration have not been changed.
- Current action: verify the Kubernetes Secret by key presence only, then repeat a development-only direct probe using the exact repository request payload. Inspect only bounded envelope metadata and assistant content needed to establish the gate; never print or persist the credential.

If interrupted now, run the credential/key-presence and direct development probe only. Do not access `--split test` until a non-empty response is schema-valid and the repository pipeline development sample records provider/model identity, token/latency telemetry, and no fallback.

### Development gate passed

- Exact repository payload direct probe: requested/response model `gemini-3.5-flash`, valid OpenAI-compatible envelope, non-empty JSON content, `finish_reason: stop`, 3.739782-second latency, and 579/148/1,303 prompt/completion/total provider-reported tokens.
- Repository pipeline development sample `basic-python-canonical-en`: schema valid, raw `small` + `minimal-python`, policy compliant, one attempt, no retry, no fallback, 3.842254-second end-to-end latency, 3.842043-second provider latency, and 579/142/1,272 prompt/completion/total tokens.
- Monetary cost remains unavailable because no explicit reproducible pricing snapshot is configured.
- Verification report: `docs/evaluation/EXTERNAL_LLM_GEMINI_3_5_VERIFICATION.md`.
- The held-out test split remained untouched through this gate.
- Focused test command initially produced 48 passes and one stale-model assertion failure in `tests/test_four_method_evaluation.py`; the assertion was updated from the retired model to the amended model and must be rerun before the live matrix.

Exact next command if interrupted:

```bash
.venv/bin/python -m pytest -q tests/test_four_method_evaluation.py tests/test_statistical_and_llm_hardening.py tests/test_evaluation_v4.py
```

If that and `git diff --check` pass, commit the model-only amendment and gate evidence so the live matrix records a clean frozen Git commit. Then create a new timestamped `results/v4-external-confirmatory-<UTC>` directory and run the 48 x 5 matrix with the exact command already recorded above, using `gemini-3.5-flash` and no pricing variables.

## Active held-out external matrix (prepared 2026-08-13T04:55:43Z)

- Pre-run model amendment and successful gate checkpoint: `a678456`.
- Focused tests: 49 passed. `git diff --check`: passed. Historical raw checksum manifest: passed.
- Dry run: exactly 240 records = 48 test samples x 5 repetitions; randomized/counterbalanced by repeat block; seed `20260808`.
- New immutable output path: `results/v4-external-confirmatory-20260813T045543Z`.
- Experiment ID: `protocol-v4-external-confirmatory-20260813T045543Z`.
- Pricing variables are intentionally unset; monetary cost must remain unavailable.
- The existing `results/v4-revised-test-20260812T095453Z` missing-credentials evidence and all Stage A/B/C evidence remain untouched.

Launch command:

```bash
EXTERNAL_LLM_API_KEY="$(kubectl -n z2jh-context-demo get secret intent-spawner-external-llm -o jsonpath='{.data.api-key}' | base64 --decode)" \
RECOMMENDER_BACKEND=external_llm \
EXTERNAL_LLM_ENDPOINT='https://generativelanguage.googleapis.com/v1beta/openai/chat/completions' \
EXTERNAL_LLM_MODEL='gemini-3.5-flash' \
EXTERNAL_LLM_TIMEOUT='10' EXTERNAL_LLM_TOTAL_TIMEOUT='30' \
EXTERNAL_LLM_MAX_RETRIES='2' EXTERNAL_LLM_RETRY_BACKOFF_SECONDS='0.25' \
EXTERNAL_LLM_TEMPERATURE='0' EXTERNAL_LLM_MAX_CONCURRENT_RECOMMENDATIONS='4' \
EXTERNAL_LLM_ALLOW_INSECURE_HTTP='false' \
.venv/bin/python -m evaluation_v4.run_recommenders \
  --recommenders external_llm \
  --split test --repeats 5 --seed 20260808 --randomize-order \
  --prompt-version prompt-v4.1.0 \
  --experiment-id protocol-v4-external-confirmatory-20260813T045543Z \
  --output results/v4-external-confirmatory-20260813T045543Z
```

If interrupted after at least one prediction is written, execute the identical command with `--resume` appended. The runner validates existing keys, retains the original run ID, skips the exact completed prefix, and appends only missing trials. Before resuming, record the process status and current line count here. Never start a replacement directory merely because the command was interrupted.

## External matrix completed and analyzed (2026-08-13T05:08:49Z)

- Live process exited 0; no experiment process is running and no resume is required.
- Authoritative external directory: `results/v4-external-confirmatory-20260813T045543Z`.
- Run ID: `v4-recommenders-20260813T045633Z-daae41dd`.
- Records: 240 expected, 240 observed, 240 unique keys; evidence validator passed.
- Predictions SHA-256: `62dccc1ce650066bfb9d1f87b2b923c3be6a0e17294677fc14b8d0a92826b7bd`.
- Raw success/failure: 21 schema-valid completions and 219 transport-error fallbacks; zero schema failures and zero runner-level terminal errors.
- Attempts: 16 trials with one, three with two, 221 with three; 224 retrying trials; all 219 fallback trials exhausted three attempts.
- Raw full-denominator accuracy: profile exact/acceptable 12/240 (5.00%), image exact/acceptable 21/240 (8.75%), joint 12/240 (5.00%). Conditional on the 21 responses: 57.14% profile/joint and 100% image.
- Applied accuracy: profile exact 66.25%, profile acceptable 77.92%, image exact/acceptable 100%, joint 67.92%; under 18.75%, over 3.33%, raw-model policy rejection 0%.
- All-trial latency mean/median/p95: 1.5565/1.2961/3.9594 seconds. Successful completion end-to-end median/p95: 4.0095/5.4042 seconds; provider median/p95: 3.9582/5.0042 seconds. Failure fallback median: 1.2902 seconds.
- Successful-call tokens: 12,468 prompt, 3,262 completion, 29,392 total. Pricing was unconfigured; cost is unavailable. Energy/resource use was not measured.
- Successes were temporally concentrated: 19 in repeat 0, two in repeat 1, none in repeats 2–4. Sanitized evidence cannot prove whether quota/rate limiting caused the transport failures.
- External report: `docs/evaluation/PROTOCOL_V4_EXTERNAL_LLM_LIVE_REPORT.md`.

Derived combined evidence was created without altering the historical source:

- Directory: `results/v4-combined-evidence-20260813T050500Z`.
- Records: 960 = 720 historical static/rule/Ollama + 240 new external; validator passed.
- Predictions SHA-256: `751a8ab32d323647770d04391c838c16233f7b987e586d37841591860230b055`.
- Reproduction tool: `python -m evaluation_v4.combine_external_results`; an independent temporary reproduction produced the identical predictions hash and passed validation.
- Corrected combined analysis: `results/v4-final-combined-external-analysis-v2-20260813T050836Z` with 960 recommendation records and the authoritative 320 Stage C records.
- Current claim gates: RQ1 CLAIMABLE; RQ2 CLAIMABLE; RQ3 PARTIALLY CLAIMABLE; RQ4 CLAIMABLE; RQ5 CLAIMABLE with limitations documented in the narrative report.
- Raw external-minus-Ollama differences: valid response -91.25 pp [family-clustered 95% CI -94.29, -87.92], raw profile -53.33 pp [-73.02, -33.47], raw image -51.67 pp [-71.38, -32.55], raw joint -38.75 pp [-59.56, -19.57]; all survive the separate Holm correction. These are operational reliability-dominated differences, not an intrinsic model ranking.
- Applied profile differences remain statistically unsupported after Holm correction. Applied external-minus-Ollama joint is +24.17 pp [2.98, 44.09], Holm McNemar p=0.0418, but this is fallback-driven and cannot be credited to Gemini.

Focused tests after analysis changes: 49 passed. Exact next command if interrupted now:

```bash
.venv/bin/python -m pytest
```

Then run evidence validators for the external and combined directories, record/duplicate checks, a secret scan, historical-evidence checksum checks, `git diff --check`, generate new checksum manifests, update this file with final validation/commit state, and commit the validated result as a new checkpoint.

### Final validation completed before checkpoint

- Full pytest: 310 passed in 33.87 seconds.
- Focused external/statistical/evaluation tests: 49 passed.
- `compileall`, shell syntax checks, and `git diff --check`: passed.
- External evidence validator: passed for 240 records and its analysis.
- Combined evidence validator: passed for 960 records and the 320-trial Stage C analysis.
- Record/duplicate checks: external 240/240 unique, combined 960/960 unique, Stage C 320/320 unique.
- Checksum verification: historical raw manifest, full 2,244-file Stage C manifest, new external run, new combined view, both new analysis directories, and the tracked consolidated manifest all passed.
- Historical immutability: no tracked diff exists under `experiments/raw`, the historical revised prediction source, or the Stage C run. The historical revised prediction hash remains `5bdbf1575ff747366e700fb9c8d6c34d4811099f548ff7e6a52ba58fcab32484`.
- Secret scan: 2,173 tracked/unignored/new evidence files scanned for the exact Kubernetes Secret value; zero matches. The generic scan found only three pre-existing false-positive/test-fixture paths (`task-e-*` names matching an `sk-` heuristic and test Bearer placeholders). A second generic scan of 64 new evidence/report files found zero matches. `gitleaks` is unavailable.
- New tracked checksum manifest: `docs/evaluation/PROTOCOL_V4_EXTERNAL_SHA256SUMS.txt`.
- No evaluation process is running. No resume action is needed.

Exact next command if interrupted before the final commit:

```bash
git add docs/evaluation/NEXT_AGENT_CONTEXT.md \
  docs/evaluation/PROTOCOL_V4_REPRODUCIBILITY.md \
  docs/evaluation/PROTOCOL_V4_REVISED_EVALUATION_REPORT.md \
  docs/evaluation/RUNBOOK_FOUR_METHOD_EVALUATION.md \
  docs/evaluation/THREATS_TO_VALIDITY.md \
  docs/evaluation/PROTOCOL_V4_EXTERNAL_LLM_LIVE_REPORT.md \
  docs/evaluation/PROTOCOL_V4_EXTERNAL_SHA256SUMS.txt \
  evaluation_v4/analyze.py evaluation_v4/combine_external_results.py \
  tests/test_evaluation_v4.py tests/test_four_method_evaluation.py
git diff --cached --check
git commit -m "Complete Protocol-v4 external LLM evaluation"
```

The validated result checkpoint completed as
`e86c8fed874bb210d2e37cda02ced06cffd2efd4` (`Complete Protocol-v4 external
LLM evaluation`). The worktree was clean immediately after that commit. There
are no remaining execution, evidence, analysis, validation, secret, or commit
blockers. The only optional next action is repository publication (push the
current `main`) and separate archival of the ignored `results/` directories
with their manifests; do not rerun or overwrite any authoritative directory.
