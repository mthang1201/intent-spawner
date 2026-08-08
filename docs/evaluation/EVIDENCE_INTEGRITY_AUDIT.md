# Evidence Integrity Audit & Traceability Matrix

This audit document establishes the authoritative traceability matrix for all empirical claims in the thesis evaluation. Every thesis claim is mapped to its underlying immutable raw evidence file, record count, analysis pipeline function, and claim gate status.

Any claim not directly supported by an append-only raw evidence file in this matrix is strictly excluded from empirical thesis results.

---

## 1. Authoritative Evidence Traceability Matrix

| Thesis Claim | Primary Metric(s) | Authoritative Raw Evidence File | Record Count | Analysis Pipeline Function | Status |
| :--- | :--- | :--- | :---: | :--- | :---: |
| **Deterministic Recommendation Quality (RQ1, RQ2)**: Rule Context achieves 68.75% Joint Acceptable accuracy on 48 held-out test samples (79.17% Profile Acceptable, 100% Image Acceptable), reducing under-provisioning to 16.67% and over-provisioning to 4.17%, with 10.42% policy violations. Static baselines achieve 4.17% joint acceptable; Intent-only achieves 39.58%. | `profile_acceptable`<br>`image_acceptable`<br>`joint_acceptable`<br>`underprovisioned`<br>`overprovisioned`<br>`policy_violation` | `experiments/raw/v4-offline-deterministic-20260808/predictions.jsonl` | 192 prediction records<br>(48 test samples × 4 methods) | `evaluation_v4.analyze:`<br>`analyze_recommendations`<br>`score_predictions` | **Authoritative & Verified** |
| **Pairwise Recommender Statistical Comparisons (H1, H2)**: Rule Context significantly outperforms Rule Intent-only on joint accuracy (exact McNemar discordant pairs: 16 vs 2, $p_{\text{raw}} = 0.000656$, $p_{\text{Holm}} = 0.00262$), and significantly outperforms Static Small and Static Large (discordant pairs: 31 vs 0, $p_{\text{raw}} < 10^{-9}$, $p_{\text{Holm}} < 10^{-8}$). | `first_only_correct`<br>`second_only_correct`<br>`p_value_raw`<br>`p_value_holm` | `experiments/raw/v4-offline-deterministic-20260808/predictions.jsonl` | 192 prediction records<br>(48 paired test samples per comparison) | `evaluation_v4.analyze:`<br>`analyze_recommendations`<br>`evaluation_v4.statistics:`<br>`exact_mcnemar`, `holm_adjust` | **Authoritative & Verified** |
| **Rule Context Failure Taxonomy (RQ1)**: Across 48 held-out test samples, Rule Context incurs exactly 15 joint failures: 8 under-provisioned samples, 2 over-provisioned samples, 5 policy violations, and 0 image selection errors. | Disjoint failure counts on test split | `experiments/raw/v4-offline-deterministic-20260808/predictions.jsonl` | 48 test prediction records | `evaluation_v4.analyze:`<br>`score_predictions` | **Authoritative & Verified** |
| **Cross-Lingual Evaluation (EN vs. VI)**: Rule Context achieves 71.43% joint accuracy on English samples ($N=28$) and 65.00% on Vietnamese samples ($N=20$) with Fisher's exact test $p = 0.744$, demonstrating no statistically significant language disparity. | Subgroup joint accuracy by language | `experiments/raw/v4-offline-deterministic-20260808/predictions.jsonl` | 48 test prediction records<br>(28 EN, 20 VI) | `evaluation_v4.analyze:`<br>`analyze_recommendations` (breakdowns) | **Authoritative (Descriptive)** |
| **Self-Hosted LLM Recommendation Quality (Qwen2.5-1.5B)**: Self-hosted `Qwen/Qwen2.5-1.5B-Instruct` achieves 50.00% Joint Acceptable accuracy (75.00% Profile Acceptable, 70.83% Image Acceptable, 20.83% under-provisioning, 4.17% over-provisioning, 8.33% policy violations) across 48 test samples. 68.75% of predictions (33/48) triggered rule-based fallback due to formatting/timeout; 31.25% (15/48) were genuine inference. In paired comparison against Rule Context, Rule Context achieved 33 vs Qwen 24 joint acceptable (discordant pairs: 10 vs 1, exact McNemar $p_{\text{raw}} = 0.0117$). | `joint_acceptable`<br>`profile_acceptable`<br>`image_acceptable`<br>`fallback_used`<br>`latency_seconds`<br>exact McNemar test | `experiments/raw/v4-offline-llm-20260808/predictions.jsonl` | 48 test prediction records | `evaluation_v4.analyze:`<br>`score_predictions`<br>`analyze_recommendations` | **Authoritative & Corrected**<br>(Recomputed 50.00% replaces corrupted 60.42%) |
| **Self-Hosted LLM Latency & Hardware Profile**: Qwen2.5-1.5B on Apple Silicon MPS hardware required mean latency of 45.27 s (median 38.92 s, p95 82.97 s, range 9.37 s–98.25 s) with 13,119 total generated tokens (mean 594.3 tokens/sample). | `latency_seconds`<br>token counts | `experiments/raw/v4-offline-llm-20260808/predictions.jsonl`<br>`run-manifest.json` | 48 recorded observations | `evaluation_v4.analyze:`<br>`analyze_recommendations`<br>`quantile`, `mean` | **Authoritative & Verified** |
| **External Cloud LLM Empirical Quality**: Cloud endpoint credentials (`EXTERNAL_LLM_API_KEY`) were unavailable; no live external API inference was executed. Claim remains CLOSED. | N/A | None (closed claim) | 0 live experimental records | N/A | **Closed (No Empirical Quality Claim)** |
| **External Backend Fallback Diagnostic**: Transport errors or missing credentials cleanly trigger local `RuleBasedRecommender` fallback without exception leaks (12/12 fallback executions, 100% fallback rate, effective backend = `rule_based`, 0 unhandled errors). | `fallback_used`<br>`fallback_error_category`<br>`effective_backend` | `experiments/raw/v4-fallback-diagnostic-20260808/predictions.jsonl` | 12 fault-injection diagnostic records | `recommender.reliability:`<br>`recommend_with_metadata` | **Verified Diagnostic Mechanics** |
| **Randomized Controlled Kubernetes Trials (RQ3, H4)**: In 240 controlled Kubernetes trials on JupyterHub 5.2.1 / K8s v1.33.9 across 8 workload families and 10 paired repeats:<br>• **Rule Context**: 62.5% success (50/80), 37.5% OOM (30/80), 0% Pending, CPU/request mean = 0.656, memory/request mean = 0.634, peak/request mean = 0.657.<br>• **Static Large**: 100.0% success (80/80), 0.0% OOM (0/80), 0% Pending, CPU/request mean = 0.189, memory/request mean = 0.590, peak/request mean = 0.614.<br>• **Static Small**: 37.5% success (30/80), 62.5% OOM (50/80), 0% Pending, CPU/request mean = 2.172, memory/request mean = 1.165, peak/request mean = 1.213.<br>• **Pod Ready**: 100% (240/240).<br>• **Cleanup**: 100% (240/240).<br>• **Mean time-to-Ready**: Rule Context = 2.392 s, Static Large = 2.338 s, Static Small = 2.327 s. | `pod_ready`<br>`workload_success`<br>`oom_killed`<br>`pending_failure`<br>`cpu_request_utilization`<br>`memory_request_utilization`<br>`peak_memory_to_request`<br>`time_to_ready_seconds` | `experiments/raw/v4-system-full-20260808/system-trials.jsonl` | 240 observed system trial records<br>(8 families × 3 methods × 10 repeats) | `evaluation_v4.analyze:`<br>`analyze_system_trials`<br>`compare_system_trials` | **Authoritative & Verified** |
| **Kubernetes Paired Hypothesis Tests & Continuous Differences (RQ3)**: Rule Context reduces OOMKilled by 20 trials and increases success by 20 trials compared to Static Small on 80 paired trials (McNemar raw $p = 1.907 \times 10^{-6}$, Holm $p = 9.537 \times 10^{-6}$). Static Large has 30 fewer OOMs than Rule Context (McNemar Holm $p = 1.304 \times 10^{-8}$). On 50 shared successful pairs, Static Large has lower utilization than Rule Context (CPU/request mean diff: -0.379 [CI: -1.047, -0.017], memory/request mean diff: -0.211 [CI: -0.368, -0.055]). | Paired McNemar tests,<br>family-clustered bootstrap intervals | `experiments/raw/v4-system-full-20260808/system-trials.jsonl` | 240 observed system trial records (80 paired blocks) | `evaluation_v4.analyze:`<br>`compare_system_trials` | **Authoritative & Verified** |
| **Pending Scheduling Detection Diagnostic**: Pending scheduling failure rate in the 240-trial matrix was 0/240. An isolated fault-injection diagnostic (99 CPU request on 8 CPU node) verified that the Pending detector correctly identifies `FailedScheduling: Insufficient cpu`. | `pending_failure`<br>event log detection | `experiments/raw/v4-pending-diagnostic-20260808/result.json` | 1 diagnostic record | `evaluation_v4.run_pending_diagnostic` | **Verified Diagnostic Detector** |
| **Intent-Aware Re-Provisioning Workflow (RQ5, H5)**: In 3 observed re-provisioning trials:<br>• 2 successful replacements (small→large and large→small) achieved pod Ready status, preserved PVC data (100%, 3/3), and resumed workloads (100%, 3/3), with replacement downtime of 1.990 s and 1.857 s (median 1.857 s, mean 1.924 s).<br>• 1 invalid preview was safely rejected before stopping the running pod (0.0 s downtime, pod preserved).<br>• Strict success rate: 2/3 = 66.7% [95% CI: 50.0%, 100.0%].<br>• Workflow is pod teardown/restart with volume reattachment, NOT live migration or in-place vertical pod resizing. | `reprovision_success`<br>`replacement_ready`<br>`pvc_continuity_verified`<br>`workload_resume_verified`<br>`downtime_seconds` | `experiments/raw/v4-reprovision-20260808-r4/reprovision-trials.jsonl` | 3 observed transaction trial records | `evaluation_v4.analyze:`<br>`analyze_reprovision_trials` | **Authoritative & Verified** |
| **User Acceptance & Consented Human Study (RQ4)**: User acceptance claims remain CLOSED; no consented live human study records were collected (`user_events` count = 0). | `acceptance_rate_decided`<br>`task_success_rate` | None (closed claim) | 0 user event records | `evaluation_v4.analyze:`<br>`analyze_user_events` | **Closed (No Consented Human Records)** |
| **Rollback After Pod Stop**: Rollback after-stop recovery remains CLOSED because no failed-after-stop scenario was executed in observed evidence. | `rollback_successful_rate` | `experiments/raw/v4-reprovision-20260808-r4/reprovision-trials.jsonl` | 3 records (0 after-stop rollback attempts) | `evaluation_v4.analyze:`<br>`analyze_reprovision_trials` | **Closed (Claim Gate Not Met)** |

---

## 2. Integrity Audit of Disallowed / Corrupted Claims

The following unsupported claims appeared in recent drafts and are strictly removed from thesis conclusions:

| Disallowed / Corrupted Claim | Reason for Rejection | Authoritative Raw Reality |
| :--- | :--- | :--- |
| **Context Rule = 0% OOM / 100% workload success** | Contradicts raw Kubernetes evidence (`system-trials.jsonl`). | Context Rule observed **37.5% OOMKilled** (30/80) and **62.5% workload success** (50/80) due to under-provisioning on 3 large workload families (`code-only-pandas`, `code-only-training`, `hidden-large-demand`). |
| **Static Small = 77.5% or 77.1% OOM** | Hallucinated number not present in raw data. | Static Small observed **62.5% OOMKilled** (50/80) and **37.5% workload success** (30/80). |
| **Static Large = 37.5% quota failure** | No ResourceQuota was applied during full system trials. | Static Large observed **0.0% quota failure** and **100.0% workload success** (80/80) on the dedicated test node. |
| **248.6 GiB·s / 894.2 GiB·s memory-seconds** | Fabricated aggregate continuous integral metrics. | Memory efficiency is measured via mean request utilization (**0.634** for Rule Context vs **0.590** for Static Large) and peak-to-request ratio (**0.657** vs **0.614**). |
| **External Cloud LLM (Simulated SOTA) empirical column** | No live external API calls were executed (`EXTERNAL_LLM_API_KEY` unset). | External LLM quality claim is **CLOSED**. Missing-endpoint fallback is reported strictly as a diagnostic. |
| **Qwen2.5-1.5B = 60.42% Joint Acceptable** | Metric calculation error from prior session. | Recomputing Qwen predictions against frozen gold labels yields **50.00% Joint Acceptable** (24/48). |
| **Optimal Pareto frontier / Dominant production strategy** | Overclaiming and uncalibrated superlatives. | Delineated as an **operational trade-off**: Rule Context balances resource allocation but experiences 37.5% OOM when code context is missing or hints understate demand. |
| **Automatic memory threshold >85% for 15s / Live cgroup scaling / In-place vertical pod resizing / Live migration** | Architecture hallucination. System uses pre-spawn recommendation and manual re-provisioning. | The system implements **pre-spawn profile recommendation** and **discrete pod replacement with PVC reattachment** (1.857 s median downtime). No live kernel migration or in-place resizing exists. |

---

## 3. Evidence File Integrity and Checksum Manifest

| Evidence Directory | Raw Data File | SHA-256 Checksum | Record Count | Protocol Version |
| :--- | :--- | :--- | :---: | :---: |
| `experiments/raw/v4-offline-deterministic-20260808` | `predictions.jsonl` | `f7e228f6db4da8e22ffd5ead7a233ee8053b41e88740a7aa1ea1675e5dbd0b27` | 192 | 4.0.0 |
| `experiments/raw/v4-offline-llm-20260808` | `predictions.jsonl` | `f9673b8169d3338b9299e348b2e143ce22417f8821575793ec742dcb8fd7e56e` | 48 | 4.0.0 |
| `experiments/raw/v4-system-full-20260808` | `system-trials.jsonl` | `939691f7543a14b9919cefe20e56e91650b160ff9a736e41fad8b6256dd265b6` | 240 | 4.0.0 |
| `experiments/raw/v4-reprovision-20260808-r4` | `reprovision-trials.jsonl` | `71a71c290472e36e2af7dfe4ba67b0d67c38a67e53d6cdf65e503c0f10d47a91` | 3 | 4.0.0 |
| `experiments/raw/v4-fallback-diagnostic-20260808` | `predictions.jsonl` | `7c370a8b5a043d99ba27883f3a29d37953caccb1b43aa53a6375dd810d19f596` | 12 | 4.0.0 |
| `experiments/raw/v4-pending-diagnostic-20260808` | `result.json` | `1c70f16c59edc93e1ca32b9bef43d4bf4c8476d70923806941bc05c906dea693` | 1 | 4.0.0 |
| `benchmarks/` | `intent-gold-v4.yaml` | `a0f23920e90c6f4b338b51ec4517a4ba49216940bf041729c8c7b0db452afc4d` | 60 (48 test) | 4.0.0 |

---

## 4. Verification and Reproducibility Command

To verify that all analysis artifacts match this audit exactly:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v4.analyze \
  --dataset benchmarks/intent-gold-v4.yaml \
  --predictions experiments/raw/v4-offline-deterministic-20260808/predictions.jsonl \
  --system-trials experiments/raw/v4-system-full-20260808/system-trials.jsonl \
  --reprovision-trials experiments/raw/v4-reprovision-20260808-r4/reprovision-trials.jsonl \
  --bootstrap-replicates 5000 \
  --seed 20260808 \
  --out /tmp/v4-audit-verify
```
