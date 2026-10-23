# Protocol-v5 Benchmark v1 Split Summary Report

- **Date:** 2026-10-22
- **Unified Gold Source:** `benchmarks_v5/gold_families_unified_v5.yaml`
- **Total Families:** 1051
- **Total Cases:** 3153 (3 variants/family: canonical, informal/noisy, vietnamese)
- **Zero Cross-Split Leakage:** Strictly verified (0 prompt overlap across all splits)

## Split Distribution

| Split | Families | Cases | Small (cases) | Medium (cases) | Large (cases) |
|---|---|---|---|---|---|
| **TRAIN** | 739 | 2217 | 1833 | 354 | 30 |
| **DEV** | 155 | 465 | 387 | 75 | 3 |
| **TEST** | 157 | 471 | 390 | 78 | 3 |

## Library Stratification Breakdown

| Library | Train Cases | Dev Cases | Test Cases | Total Cases |
|---|---|---|---|---|
| `arcade` | 219 | 39 | 42 | 300 |
| `matplotlib` | 324 | 69 | 72 | 465 |
| `numpy` | 456 | 99 | 99 | 654 |
| `pandas` | 549 | 114 | 114 | 777 |
| `pytorch` | 138 | 30 | 30 | 198 |
| `scikit-learn` | 225 | 48 | 48 | 321 |
| `scipy` | 213 | 45 | 45 | 303 |
| `tensorflow` | 93 | 21 | 21 | 135 |
