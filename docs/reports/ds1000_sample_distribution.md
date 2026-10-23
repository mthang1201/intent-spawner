# DS-1000 Stratified Sample Distribution Report

- **Generated At**: 2026-10-21T09:08:42.049382+00:00
- **Seed**: `20261021`
- **Image Catalog**: `recommender/image-catalog.yaml`
- **Full Population**: 1000 problems
- **Stratified Sample**: 150 problems (`ds1000_sample150.jsonl`)
- **Pilot Subset**: 20 problems (`ds1000_pilot20.jsonl`)

## Stratification Constraints & Methodology

1. **50% Maximum Image Cap**: In the full population, `scipy-data-science` represents 88.7% of problems. To ensure rigorous evaluation across deep learning workloads without bias, `scipy-data-science` is capped at **50.0%** (75/150 in full sample, 10/20 in pilot).
2. **Library Balance**: Within `scipy-data-science`, sample quota is divided equally across all 5 constituent libraries (Pandas, Numpy, Matplotlib, Scikit-learn, Scipy) at **15 items each** (and **2 items each** in pilot).
3. **Pilot Alignment**: The pilot dataset comprises the first 20 items of a deterministic permutation of the sample, preserving image-level stratification (10 scipy-data-science, 5 pytorch, 5 tensorflow).
4. **No Duplicates & Full Reproducibility**: Each stratum uses independent SHA-256 derived seeds without replacement.

## Distribution Breakdown (image_id × library)

| Image ID | Library | Population N | Pop % | Sample 150 N | Sample % | Pilot 20 N | Pilot % |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| `pytorch-deep-learning` | Pytorch | 68 | 6.8% | 38 | 25.3% | 5 | 25.0% |
| **Subtotal (pytorch-deep-learning)** | *All* | **68** | **6.8%** | **38** | **25.3%** | **5** | **25.0%** |
| `scipy-data-science` | Matplotlib | 155 | 15.5% | 15 | 10.0% | 2 | 10.0% |
| `scipy-data-science` | Numpy | 220 | 22.0% | 15 | 10.0% | 2 | 10.0% |
| `scipy-data-science` | Pandas | 291 | 29.1% | 15 | 10.0% | 2 | 10.0% |
| `scipy-data-science` | Scikit-learn | 115 | 11.5% | 15 | 10.0% | 2 | 10.0% |
| `scipy-data-science` | Scipy | 106 | 10.6% | 15 | 10.0% | 2 | 10.0% |
| **Subtotal (scipy-data-science)** | *All* | **887** | **88.7%** | **75** | **50.0%** | **10** | **50.0%** |
| `tensorflow-deep-learning` | Tensorflow | 45 | 4.5% | 37 | 24.7% | 5 | 25.0% |
| **Subtotal (tensorflow-deep-learning)** | *All* | **45** | **4.5%** | **37** | **24.7%** | **5** | **25.0%** |
| **TOTAL** | **7 libraries** | **1000** | **100.0%** | **150** | **100.0%** | **20** | **100.0%** |

## Quality & Audit Attestation

- **Max image share in Sample 150**: `50.0%` (Constraint <= 50%: **PASS**)
- **Max image share in Pilot 20**: `50.0%` (Constraint <= 50%: **PASS**)
- **Unique problem IDs in Sample 150**: `150/150` (**PASS**)
- **Pilot 20 is exact prefix of Sample 150**: `True` (**PASS**)