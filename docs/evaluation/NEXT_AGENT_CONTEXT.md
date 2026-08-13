# Protocol-v4 Stage C Confirmatory Handoff

Last updated: 2026-08-13 12:05 Asia/Ho_Chi_Minh. Stage C and analysis are complete; the frozen external-LLM credential/backend was rechecked.

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
