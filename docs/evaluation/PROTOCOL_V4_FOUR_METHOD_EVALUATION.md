# Protocol-v4 Four-Method Recommender Evaluation Specification

## 1. Objective and Evaluation Boundary

This specification defines the rigorous, reproducible experimental methodology comparing four approaches for selecting JupyterHub notebook resource profiles and container images:

1. `static_profile_baseline`: Frozen single operational baseline (`medium` profile, `minimal-python` image), ignoring intent, dataset size, and code context.
2. `rule_based_mapping`: Deterministic rule-based recommender parsing intent keywords, dataset size thresholds, and code/library import hints.
3. `external_llm`: Configurable external LLM API (e.g. Gemini-compatible via OpenAI chat-completions endpoint), parsing structured JSON recommendations and locally validating against the cluster profile list and administrator image catalog.
4. `self_hosted_local_ollama_llm`: Configurable self-hosted local inference endpoint (e.g. Ollama `llama3:latest`), executing locally within cluster boundaries.

---

## 2. Research Questions and Endpoints

| ID | Authoritative Research Question | Primary Quantitative Metric | Statistical Comparison |
| :--- | :--- | :--- | :--- |
| **RQ1** | **How do the four approaches differ in recommendation quality?** | Joint Acceptable Accuracy (Profile acceptable AND Image acceptable AND Policy compliant), Profile Accuracy, Image Capability Coverage | Exact McNemar test with Holm step-down adjustment, Cluster bootstrap CI |
| **RQ2** | **Do LLM-based approaches improve recommendation quality compared with the static baseline and rule-based mapping?** | Joint Acceptable Accuracy Delta, Under/Over-provisioning reduction | Exact McNemar paired test with Holm adjustment |
| **RQ3** | **What additional latency, failures, fallbacks, monetary cost, resource consumption, and operational overhead do LLM approaches introduce?** | Median & P95 latency (seconds), Prompt/Completion tokens, Estimated cost ($ / 1k requests), Fallback rate, Error categorization | Paired Wilcoxon signed-rank test with Holm correction, Resampling CI |
| **RQ4** | **When recommendations are applied, how does each approach affect workload success, OOM events, Pending failures, runtime, and resource efficiency in Kubernetes and JupyterHub?** | Workload success rate, OOM kill rate, Pending failure rate, Time-to-ready (s), Request CPU/memory utilization | Stage C observed cluster trial telemetry, McNemar-Holm |
| **RQ5** | **What are the quality–latency–reliability–cost–privacy trade-offs between an external LLM and a locally hosted Ollama model?** | Multi-criteria trade-off frontier: Joint accuracy, Median latency, Failure/fallback rate, Token cost ($), Data boundary/privacy | Empirical head-to-head comparison matrix |

---

## 3. Pre-Registered Hypotheses

- **H1 (Accuracy Dominance)**: `rule_based_mapping`, `external_llm`, and `self_hosted_local_ollama_llm` achieve significantly higher joint acceptable accuracy than `static_profile_baseline` on the locked test split ($p < 0.05$, Holm-adjusted).
- **H2 (Sizing Balance)**: Context-aware recommenders yield strictly lower under-provisioning rates than static small profiles and strictly lower over-provisioning rates than static large profiles.
- **H3 (Allowlisting and Safety Guarantee)**: All four methods enforce local allowlist verification, guaranteeing $0\%$ execution of unapproved container images or illegal profiles.
- **H4 (Latency Hierarchy)**: `static_profile_baseline` < `rule_based_mapping` < `self_hosted_local_ollama_llm` < `external_llm` in wall-clock recommendation latency ($p < 0.05$, Wilcoxon-Holm).

---

## 4. Frozen Experimental Controls

### 4.1. Single Frozen Operational Static Baseline
- **Authoritative Baseline Definition**: `profile = medium` (2 CPU cores, 4 GB RAM limit), `image = minimal-python`.
- **Integrity Rule**: The static baseline is never converted into "the best static profile for each workload". It represents the standard fixed spawner default without user context.

### 4.2. Model Independence & Zero Secrets
- The Antigravity coding agent's internal model is never automatically substituted as the evaluated experiment model.
- Evaluated models are explicitly configured via CLI flags (`--external-model`, `--ollama-model`) or environment variables (`EXTERNAL_LLM_MODEL`, `OLLAMA_MODEL`).
- No API keys or tokens are stored in logs, manifests, or version control. Missing credentials produce explicit `effective_backend="unavailable"` error records rather than silent fabrication.

### 4.3. Five-Stage Telemetry and Fallback Isolation
For every LLM recommendation trial, the evaluation runner isolates and records:
1. `raw_response`: Verbatim string envelope from the provider.
2. `parsed_profile` & `parsed_image_id`: Structured entities extracted before validation.
3. `validation_error`: Reason for rejection (e.g. unknown profile, unapproved image, malformed JSON).
4. `fallback_used`: Boolean indicator if the fallback backend was engaged.
5. `applied_profile` & `predicted_image_id`: Final applied resource decision.

> [!IMPORTANT]
> **Fairness Constraint**: When an LLM fails and falls back to rules, the trial is counted as a fallback and raw LLM error. It is **never** credited as a successful raw LLM prediction.

---

## 5. Statistical Methodology

- **Resampling Strategy**: Percentile bootstrap with 2,000 replicates clustered by `workload_family`, preserving correlation across multilingual paraphrases.
- **Binary Paired Comparisons**: Two-tailed exact McNemar tests with Holm-Bonferroni correction ($\alpha = 0.05$).
- **Continuous Paired Comparisons**: Two-tailed Wilcoxon signed-rank tests with continuity and tie correction for paired latency and token distributions.
- **Multi-Class Confusion Matrices**: Computed per method for `small`, `medium`, and `large` resource profile selections against ground-truth preferred profiles.

---

## 6. Execution Command Reference

```bash
# 1. Deterministic offline evaluation matrix across all 4 methods
.venv/bin/python -m evaluation_v4.run_recommenders \
  --split test \
  --repeats 5 \
  --seed 20260808 \
  --randomize-order \
  --output results/v4-predictions

# 2. Comprehensive statistical analysis and thesis report generation
.venv/bin/python -m evaluation_v4.analyze \
  --predictions results/v4-predictions/predictions.jsonl \
  --out results/v4-analysis
```
