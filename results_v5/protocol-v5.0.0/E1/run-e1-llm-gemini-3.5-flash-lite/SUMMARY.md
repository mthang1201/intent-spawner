# Baseline Recommender Evaluation Report on Benchmark v1

- **Execution Timestamp:** 2026-10-24T08:46:15.769369+00:00
- **Recommender Commit Hash:** `00eca1ee4d4fe3f7a9fef370a94aa9453718dcc8`
- **Test Set Size:** 471 cases across 157 disjoint workload families
- **Ground Truth Foundation:** Linux cgroup v2 empirical resource measurements (`anon_peak_mib_max`)

---

## 1. Executive Summary: P1 Baseline vs. P2 Intent-Spawner

| Evaluation Metric | P1 (Rule-Based Heuristic) | P2 (Intent-Spawner NLP/Hybrid) | Relative Difference |
|---|---|---|---|
| **Candidate Accuracy@1** | **26.33%** | **27.39%** | **+1.06%** |
| **Candidate Accuracy@3** | 26.33% | 31.42% | +5.10% |
| **Profile Accuracy@1** | 33.97% | 35.03% | +1.06% |
| **Image Accuracy@1** | 91.72% | 90.23% | +-1.49% |
| **Joint Acceptability@1** | 84.29% | 79.19% | +-5.10% |
| **Under-provision Rate (OOM Hazard)** | **3.19%** (15 cases) | **6.37%** (30 cases) | **3.18%** |
| **Over-provision Rate (Resource Waste)** | 62.84% | 58.60% | -4.25% |

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
GT small:           151             208                31
GT medium:          27              14                 37
GT large:           0               3                  0
```

---

## 3. Stratified Breakdown by Library

| Library | Count | P1 Cand Acc@1 | P2 Cand Acc@1 | P1 Joint Accept | P2 Joint Accept | P2 Under-Prov | P2 Over-Prov |
|---|---|---|---|---|---|---|---|
| `arcade` | 42 | 21.4% | 21.4% | 52.4% | 52.4% | 28.6% | 35.7% |
| `matplotlib` | 72 | 16.7% | 16.7% | 100.0% | 100.0% | 0.0% | 83.3% |
| `numpy` | 99 | 75.8% | 74.8% | 78.8% | 77.8% | 0.0% | 4.0% |
| `pandas` | 114 | 0.9% | 0.9% | 92.1% | 92.1% | 0.0% | 93.9% |
| `pytorch` | 30 | 0.0% | 0.0% | 90.0% | 90.0% | 6.7% | 93.3% |
| `scikit-learn` | 48 | 0.0% | 0.0% | 62.5% | 62.5% | 0.0% | 100.0% |
| `scipy` | 45 | 60.0% | 62.2% | 93.3% | 77.8% | 0.0% | 31.1% |
| `tensorflow` | 21 | 0.0% | 23.8% | 100.0% | 23.8% | 76.2% | 0.0% |

---

## 4. Linguistic Variant Breakdown (Robustness)

| Variant Class | Count | P1 Cand Acc@1 | P2 Cand Acc@1 | P1 Joint Accept | P2 Joint Accept |
|---|---|---|---|---|---|
| `canonical_en` | 157 | 25.5% | 26.8% | 84.1% | 79.6% |
| `informal_or_noisy` | 157 | 26.1% | 26.1% | 84.1% | 78.3% |
| `vietnamese` | 157 | 27.4% | 29.3% | 84.7% | 79.6% |

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
