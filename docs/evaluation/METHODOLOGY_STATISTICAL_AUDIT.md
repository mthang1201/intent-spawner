# Protocol-v4 Statistical Methodology Audit & Inferential Hardening

## Executive Summary

This document provides a rigorous statistical audit and revision of the inferential methodology used in Protocol-v4 of the `intent-spawner` evaluation framework.

In historical evaluation runs (e.g., `results/v4-live-20260810`), deterministic and stochastic recommendation methods were evaluated by repeating each test sample ($N=48$) across $R=5$ repetition blocks, resulting in $N \times R = 240$ prediction records per method. Pairwise inferential tests (such as exact McNemar and paired Wilcoxon signed-rank tests) were previously calculated directly across all 240 rows.

This audit establishes that treating repeated trials on the same workload sample as independent observations constitutes **pseudo-replication** for deterministic methods (and inflates nominal degrees of freedom for stochastic methods). We define, implement, and validate a methodologically sound statistical architecture where:
1. The **primary inferential unit** is the held-out sample ($N=48$), clustered across 20 distinct `workload_family` clusters.
2. Pairwise inferential comparisons are conducted on sample-level aggregated scores.
3. 95% confidence intervals are estimated via **cluster percentile bootstrap** resampling workload families.
4. Effect sizes (risk differences, paired Cohen's $d_z$, Cliff's delta, and matched-pairs rank-biserial effects) are reported alongside exact Holm-adjusted $p$-values.
5. Statistical significance is explicitly distinguished from operational significance.
6. The metric hierarchy clearly separates **resource-profile selection** (primary thesis objective) from **image selection** and **joint operational performance**.

---

## 1. Problem Formulation: The Pseudo-Replication Hazard

### 1.0 Audited prediction-stage semantics

The implementation keeps the following stages distinct; these definitions are preserved rather than retrospectively reinterpreted:

- `raw_response` is the provider assistant text from the last attempt. It is `null` when no provider response was received.
- `parsed_profile` and `parsed_image_id` are diagnostic fields extracted from a JSON object when those keys exist, even if the complete response later fails schema validation. They are not, by themselves, a schema-valid model recommendation.
- `validation_error` is a sanitized exception class. A schema failure is counted only when `fallback_error_category == "invalid_response"`; transport and timeout fallbacks are separate.
- `fallback_used` means the effective recommendation came from the deterministic rule backend. `effective_backend` identifies that backend. Protocol-v4.0 did not store a separate nested fallback object; when fallback is used, `predicted_profile`, `predicted_image_id`, and usually `applied_profile` describe the fallback output, while `parsed_*` retains any extractable raw-model values.
- `policy_compliant` is evaluated on the recommendation returned by the active backend. `applied_profile` is the normalized, policy-allowed profile after nearest-allowed mapping; `predicted_profile` is the normalized recommendation before that mapping. `policy_rejection` in the revised analysis excludes unavailable responses and schema fallbacks.
- Applied profile/image accuracy uses `applied_profile` and `predicted_image_id`. Raw LLM accuracy requires a schema-valid, non-fallback LLM response; a correct fallback is never credited to the model.
- Latency is end-to-end recommender elapsed time, including retries and fallback. Provider/native inference latency is reported separately when supplied by the backend.

The historical schema therefore supports exact raw-versus-applied separation for successful and schema-invalid LLM responses, but does not retain a standalone fallback image/profile object separate from the final predicted fields. That is a documented schema limitation, not reconstructed after the fact.

### 1.1 Gold Dataset Structure
The evaluation dataset (`benchmarks/intent-gold-v4.yaml`) comprises 60 structured workload descriptions partitioned into:
- **Development Split**: 12 samples across 4 workload families.
- **Held-Out Test Split**: 48 samples across 20 workload families.

Each sample $s_i \in \mathcal{S}_{\text{test}}$ ($i = 1, \dots, 48$) contains:
- `intent`: Natural language query describing user intent.
- `dataset_size_gb`: Declared or inferred dataset size.
- `code_context`: Python script snippet or imported libraries.
- `gold_preferred_profile`: Target resource profile $\in \{\text{small}, \text{medium}, \text{large}\}$.
- `gold_acceptable_profiles`: Set of non-failing, non-wasteful profiles.
- `gold_preferred_image`: Target container image ID.
- `gold_acceptable_images`: Set of valid image IDs satisfying capability requirements.
- `workload_family`: Semantic cluster identifier (e.g., `basic-python`, `pandas-pipeline`, `pytorch-distributed`).

### 1.2 The Pseudo-Replication Fallacy
Let $Y_{i, r, m} \in \{0, 1\}$ denote the binary evaluation outcome (e.g., profile acceptable) for sample $i$, repetition $r \in \{1, \dots, 5\}$, under recommender method $m$.

For deterministic methods:
$$\forall r_1, r_2 \in \{1, \dots, 5\}, \quad Y_{i, r_1, m} \equiv Y_{i, r_2, m}$$

When pairing Method $A$ and Method $B$ across all 240 trial records:
- If sample $i$ is discordant ($Y_{i, r, A} \neq Y_{i, r, B}$), this single discordant sample contributes **5 identical discordant pairs** to the contingency table.
- In McNemar's test, the binomial probability $P(K \le k \mid n, p=0.5)$ scales exponentially with $n = 5 \times n_{\text{samples}}$.
- For example, 16 discordant samples become 80 repeated discordant trial rows (60 vs 20); treating those rows as independent produces an artificially tiny p-value.
- At the true sample level ($N=48$, 12 vs 4 discordant samples), the exact two-tailed binomial $p$-value is $p = 0.0768$.

Treating $N=240$ trials as independent artificially depresses $p$-values by dozens of orders of magnitude, creating false claims of extreme statistical significance.

---

## 2. Corrected Inferential Architecture

### 2.1 Primary Inferential Unit: Sample-Level Aggregation
For each held-out test sample $s_i$ ($i = 1, \dots, 48$) and recommender $m$, we compute the sample-level performance score across all $R=5$ repetitions:

$$\bar{Y}_{i, m} = \frac{1}{R} \sum_{r=1}^R Y_{i, r, m} \in [0, 1]$$

For deterministic methods (e.g., `static_profile_baseline`, `rule_based_mapping`), $\bar{Y}_{i, m} \in \{0, 1\}$. For stochastic LLMs with potential non-zero temperature or sampling variation, $\bar{Y}_{i, m}$ reflects empirical expected accuracy per sample.

### 2.2 Paired Binary Inference: Exact McNemar Test
For sample-level binary classification (where $\bar{Y}_{i, m} \ge 0.5$ designates sample success):
Let $b$ denote the number of samples correct under Method $A$ but incorrect under Method $B$, and $c$ denote samples correct under $B$ but incorrect under $A$.
The exact two-tailed $p$-value under null hypothesis $H_0: p_b = p_c = 0.5$ is:

$$p = 2 \times \sum_{k=0}^{\min(b, c)} \binom{b + c}{k} \left(\frac{1}{2}\right)^{b+c}$$

### 2.3 Paired Continuous Inference: Wilcoxon Signed-Rank Test
For sample-level mean scores $\bar{Y}_{i, A}$ and $\bar{Y}_{i, B}$, and sample mean latencies $\bar{L}_{i, A}$ and $\bar{L}_{i, B}$:
1. Compute non-zero paired differences $D_i = \bar{Y}_{i, A} - \bar{Y}_{i, B}$ for $i \in \{1, \dots, n\}$.
2. Rank absolute differences $|D_i|$, assigning average fractional ranks to ties.
3. Compute test statistic $T = \min(W^+, W^-)$ where $W^+ = \sum_{D_i > 0} R_i$ and $W^- = \sum_{D_i < 0} R_i$.
4. Compute exact permutation $p$-value for $n \le 15$, or asymptotic normal approximation with continuity and tie corrections for $n > 15$.

### 2.4 Cluster-Aware Percentile Bootstrap Confidence Intervals
Because samples within the same `workload_family` (e.g., canonical vs paraphrase vs multilingual translations) exhibit natural semantic correlations, standard IID resampling violates exchangeability.

We implement **cluster bootstrap resampling** over the $K=20$ test workload families:
1. In each replicate $b \in \{1, \dots, B\}$ ($B = 2,000$):
   - Sample $K=20$ workload families with replacement: $\mathcal{F}^{*b} = \{f_1^*, \dots, f_K^*\}$.
   - Include all sample records belonging to the selected families: $\mathcal{S}^{*b} = \bigcup_{k=1}^K \mathcal{S}_{f_k^*}$.
   - Compute metric estimate $\hat{\theta}^{*b} = \text{Metric}(\mathcal{S}^{*b})$ and paired difference $\Delta^{*b} = \text{Metric}_A(\mathcal{S}^{*b}) - \text{Metric}_B(\mathcal{S}^{*b})$.
2. Compute the 95% bootstrap confidence interval as $[\hat{\theta}_{0.025}, \hat{\theta}_{0.975}]$.

### 2.5 Effect Size Formulations
To ensure practical magnitude is evaluated independently of sample size:
1. **Risk Difference (Mean Difference)**:
   $$\Delta = \bar{X}_A - \bar{X}_B$$
2. **Paired Cohen's $d_z$**:
   $$d_z = \frac{\bar{D}}{s_D} = \frac{\frac{1}{N}\sum_{i=1}^N (X_{i, A} - X_{i, B})}{\sqrt{\frac{1}{N-1}\sum_{i=1}^N (D_i - \bar{D})^2}}$$
3. **Cliff's Delta** (all cross-method observations, not a renamed paired sign count):
   $$\delta = \frac{\#(X_{i,A} > X_{j,B}) - \#(X_{i,A} < X_{j,B})}{N_A N_B}$$
4. **Matched-pairs rank-biserial effect**:
   $$r_{rb} = \frac{W^+ - W^-}{W^+ + W^-}$$

### 2.6 Multiplicity Control: Step-Down Holm-Bonferroni Correction
To control family-wise error rate across all $M$ pairwise comparisons without excessive conservatism:
1. Order unadjusted $p$-values: $p_{(1)} \le p_{(2)} \le \dots \le p_{(M)}$.
2. Compute adjusted values:
   $$p_{(k)}^{\text{adj}} = \max_{j \le k} \min\left(1, (M - j + 1) \times p_{(j)}\right)$$

---

## 3. Metric Hierarchy and Disentanglement

A critical threat to validity identified in early protocols was conflating container image selection with Kubernetes resource profile selection.

### 3.1 Primary Metric: Resource Profile Selection
The core research objective of `intent-spawner` is allocating right-sized hardware envelopes (CPU/Memory) to prevent Pod Out-Of-Memory (OOM) crashes and cluster over-commitments.
- **Profile Acceptable Rate**: Spawned profile $\in \text{gold\_acceptable\_profiles}$.
- **Profile Exact Rate**: Spawned profile $== \text{gold\_preferred\_profile}$.
- **Under-provisioning Rate**: Spawned profile smaller than required (direct OOM risk).
- **Over-provisioning Rate**: Spawned profile larger than required (resource waste).
- **Policy Violation Rate**: Recommended profile rejected by administrator resource constraints.

### 3.2 Secondary Metric: Notebook Image Matching
- **Image Acceptable Rate**: Selected image satisfies all declared dependencies and tool requirements.
- **Capability Coverage Rate**: Fraction of required capability tags present in selected image.

### 3.3 System Joint Metric: Joint Operational Acceptability
- **Joint Acceptable Rate (Applied)**: Both profile and image acceptable under operational policy.
- **Raw Model Joint Acceptable Rate**: Joint acceptability achieved strictly by raw model generation without fallback intervention.

---

## 4. Statistical vs Operational Significance

| Dimension | Statistical Significance ($\alpha = 0.05$) | Operational Significance in Production |
| :--- | :--- | :--- |
| **Profile Accuracy** | Rejection of $H_0: \text{Acc}_A = \text{Acc}_B$ | Prevention of user pod OOMs and interactive spawn delays |
| **Recommendation Latency** | A small deterministic difference can be statistically detectable | A sub-millisecond difference is negligible beside pod startup |
| **LLM Network Latency** | Revised evidence finds a significant latency difference | Local Ollama adds 9.204 s median end-to-end latency versus 0.295 ms for rules |
| **Fallback Rate** | Significant if fallback rate > 0 | **Critical**: High fallback rate indicates schema fragility and unreliability |

---

## 5. Summary of Audit Recommendations

1. **Adopt Sample-Level Primary Inferential Tables**: All thesis claims regarding accuracy improvements must report $N=48$ sample-level exact McNemar and Wilcoxon tests.
2. **Preserve Trial-Level Records for Descriptive Telemetry**: 240-trial data remain valuable for stability and variance analysis, but must be labeled as descriptive trial-level statistics.
3. **Report Cluster Bootstrap CIs on Risk Differences**: Always present $[\Delta_{\text{low}}, \Delta_{\text{high}}]$ alongside effect sizes.
4. **Isolate Fallbacks from Model Capability Claims**: Models triggering fallbacks must not be credited with accurate recommendations.
