# Profile Label Distribution Report — `ds1000`

- **Generated at:** 2026-10-22 09:28:04 UTC
- **Headroom Metric & Threshold:** `anon_peak_mib_max > 0.85 × memory_limit(profile)` (slack=1)
- **Total Input Workloads:** 1000
- **Successfully Labeled Workloads:** 951 (95.1% of input)
- **Excluded Workloads (non-labelable):** 49

## 1. Workload Filtering & Exclusion Summary

| Category | Detail / Status | Count |
| :--- | :--- | :---: |
| **Input Records** | Total records parsed | 1000 |
| **Labeled Records** | status in (`ok`, `infeasible_over_capacity`) | 951 |
| **Excluded Records** | status in (`error`, `timeout`) | 49 |
| Excluded Status | status=`error` | 49 |
| Excluded Image | image=`pytorch-deep-learning` | 2 |
| Excluded Image | image=`scipy-data-science` | 47 |

## 2. Preferred Profile Distribution

| Preferred Profile | Memory Limit (MiB) | Labeled Count | Share (%) |
| :--- | :---: | :---: | :---: |
| **`small`** (Small: low CPU/RAM) | 384.0 MiB | 834 | 87.7% |
| **`medium`** (Medium: moderate CPU/RAM) | 1024.0 MiB | 117 | 12.3% |
| **`large`** (Large: high CPU/RAM) | 2048.0 MiB | 0 | 0.0% |

### Preferred Profile Distribution by Image

| Image ID | Total Labeled | `small` | `medium` | `large` | `infeasible` |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `pytorch-deep-learning` | 68 | 0 | 68 | 0 | 0 |
| `scipy-data-science` | 832 | 830 | 2 | 0 | 0 |
| `tensorflow-deep-learning` | 51 | 4 | 47 | 0 | 0 |

## 3. Headroom Bump Analysis (Before vs After)

- **Bumped for Headroom:** 49 / 951 (5.15%)
- **Headroom Exceeded at Max Profile:** 0 (0.0%)
- **Import-Dominated Workloads (`peak < 1.10 × baseline`):** 790 / 951 (83.07%)

### Bumps by Original Profile (Pre-Bump)

| Original Profile | Limit (MiB) | Bump Threshold (85%) | Bumps Escalated | Share of Tier |
| :--- | :---: | :---: | :---: | :---: |
| `small` | 384.0 | 326.40 MiB | 49 / 883 | 5.55% |
| `medium` | 1024.0 | 870.40 MiB | 0 / 68 | 0.0% |
| `large` | 2048.0 | 1740.80 MiB | 0 / 0 | 0.0% |

### Bumps by Image Catalog ID

| Image ID | Total Labeled | Bumps Escalated | Bump Share (%) |
| :--- | :---: | :---: | :---: |
| `pytorch-deep-learning` | 68 | 0 | 0.0% |
| `scipy-data-science` | 832 | 2 | 0.24% |
| `tensorflow-deep-learning` | 51 | 47 | 92.16% |

## 4. Policy & Dataset Review Warnings

> [!WARNING] Preferred profile 'small' exceeds maximum allowed share: 87.7% > 50.0%.
> [!WARNING] Image 'scipy-data-science' exceeds maximum allowed share: 87.49% > 50.0%.

