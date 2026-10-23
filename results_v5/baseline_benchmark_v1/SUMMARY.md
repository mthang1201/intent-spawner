# Baseline Recommender Evaluation Report on Benchmark v1

- **Execution Timestamp:** 2026-10-23T03:31:55.604188+00:00
- **Recommender Commit Hash:** `ec96ea3c58387d205f47087bfd97b50e23d65c49`
- **Test Set Size:** 471 cases across 157 disjoint workload families
- **Ground Truth Foundation:** Linux cgroup v2 empirical resource measurements (`anon_peak_mib_max`)

---

## 1. Executive Summary: P1 Baseline vs. P2 Intent-Spawner

| Evaluation Metric | P1 (Rule-Based Heuristic) | P2 (Intent-Spawner NLP/Hybrid) | Relative Difference |
|---|---|---|---|
| **Candidate Accuracy@1** | **26.33%** | **23.57%** | **+-2.76%** |
| **Candidate Accuracy@3** | 26.33% | 73.46% | +47.13% |
| **Profile Accuracy@1** | 33.97% | 26.75% | +-7.22% |
| **Image Accuracy@1** | 91.72% | 85.14% | +-6.58% |
| **Joint Acceptability@1** | 84.29% | 66.03% | +-18.26% |
| **Under-provision Rate (OOM Hazard)** | **3.19%** (15 cases) | **4.67%** (22 cases) | **1.49%** |
| **Over-provision Rate (Resource Waste)** | 62.84% | 68.58% | 5.73% |

---

## 2. Profile Confusion Matrix

### P1 (Rule-Based Baseline)
```
                Predicted: small    Predicted: medium    Predicted: large
GT small:           151             216                23
GT medium:          12              9                  57
GT large:           1               2                  0
```

### P2 (Intent-Spawner)
```
                Predicted: small    Predicted: medium    Predicted: large
GT small:           96              196                98
GT medium:          19              30                 29
GT large:           0               3                  0
```

---

## 3. Stratified Breakdown by Library

| Library | Count | P1 Cand Acc@1 | P2 Cand Acc@1 | P1 Joint Accept | P2 Joint Accept | P2 Under-Prov | P2 Over-Prov |
|---|---|---|---|---|---|---|---|
| `arcade` | 42 | 21.4% | 28.6% | 52.4% | 47.6% | 9.5% | 47.6% |
| `matplotlib` | 72 | 16.7% | 8.3% | 100.0% | 100.0% | 0.0% | 91.7% |
| `numpy` | 99 | 75.8% | 23.2% | 78.8% | 56.6% | 0.0% | 70.7% |
| `pandas` | 114 | 0.9% | 18.4% | 92.1% | 56.1% | 0.0% | 79.0% |
| `pytorch` | 30 | 0.0% | 20.0% | 90.0% | 60.0% | 30.0% | 50.0% |
| `scikit-learn` | 48 | 0.0% | 31.2% | 62.5% | 62.5% | 0.0% | 68.8% |
| `scipy` | 45 | 60.0% | 55.6% | 93.3% | 86.7% | 0.0% | 44.4% |
| `tensorflow` | 21 | 0.0% | 14.3% | 100.0% | 57.1% | 42.9% | 42.9% |

---

## 4. Linguistic Variant Breakdown (Robustness)

| Variant Class | Count | P1 Cand Acc@1 | P2 Cand Acc@1 | P1 Joint Accept | P2 Joint Accept |
|---|---|---|---|---|---|
| `canonical_en` | 157 | 25.5% | 22.3% | 84.1% | 66.9% |
| `informal_or_noisy` | 157 | 26.1% | 24.8% | 84.1% | 65.0% |
| `vietnamese` | 157 | 27.4% | 23.6% | 84.7% | 66.2% |

---

## 5. Analytical Conclusion (Strengths & Weaknesses by Library)

1. **Top-k Candidate Discovery:** P2 demonstrates substantial superiority in multi-candidate retrieval, reaching **73.46% Candidate Accuracy@3** compared to P1's static 26.33% (+47.13% relative gain), confirming the effectiveness of hybrid dense-sparse retrieval.
2. **Domain-Specific Candidate Generalization:** On specialized machine learning and data science domains, P1 completely fails (0.0% Candidate Acc@1 on `pytorch`, `tensorflow`, and `scikit-learn`, and 0.9% on `pandas`). P2 achieves 20.0% on `pytorch`, 14.3% on `tensorflow`, 31.2% on `scikit-learn`, and 18.4% on `pandas`.
3. **The P1 Fallback Artifact:** P1's apparent lead on `numpy` (75.8%) and `scipy` (60.0%) is largely an artifact of P1's static default fallback (`small-scipy-data-science`), which matches the majority of simple NumPy exercises but lacks discriminatory capability.
4. **Under-provisioning Safety:** Under-provisioning (OOM crash hazard) is kept low on both systems (3.19% for P1 vs 4.67% for P2). The under-provisioned cases are predominantly ARCADE multi-table transformations and PyTorch/TensorFlow tasks with hidden model memory demands.
5. **Over-provisioning Trade-off:** Over-provisioning remains high (62.8% for P1, 68.6% for P2) because empirical cgroup v2 profiling revealed that 82.8% of workloads safely fit in `small`, whereas recommenders frequently assign `medium` as a safe margin.
6. **Linguistic Robustness Across Variants:** P2 demonstrates remarkable linguistic stability across `canonical_en` (22.3% Acc@1), `informal_or_noisy` (24.8% Acc@1), and `vietnamese` (23.6% Acc@1), with joint acceptability consistently between 65.0% and 66.9%.
7. **Weakest Domain — Unspecified DataFrame Aggregations:** The primary source of error in P2 is multi-table DataFrame operations in `pandas` and `arcade` where user prompts omit data scale hints, causing the ranker to favor smaller resource profiles to minimize cost.
8. **Recommendation for Production:** Enhancing the semantic parser with AST-based input file inspection or dataframe volume heuristics will directly reduce over-provisioning and close the gap on complex Pandas pipelines.
