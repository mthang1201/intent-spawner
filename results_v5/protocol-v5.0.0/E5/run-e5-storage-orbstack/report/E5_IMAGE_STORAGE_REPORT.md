# Protocol-v5 E5 Image Storage and Catalog Scalability Report: `e5-storage-scalability-20260928T030813Z`

## 1. Executive Summary & Provenance

- **Execution Timestamp (UTC)**: 2026-09-28T03:08:18.177572Z
- **Git Revision**: `f7a4426472043d4652bede07556656800605ad95` (dirty: True)
- **Execution Status**: `OBSERVED`
- **Split Stage**: `development`
- **Claims Permitted**: `False`
- **Collector Origin**: `REAL_REGISTRY`
- **Collector**: `docker-manifest-inspect` (`storage-collector-v1.0.0`)
- **Size Domain**: `compressed_oci_manifest_layer_bytes` (compressed OCI manifest layer blobs)
- **Measurement Method**: `docker manifest inspect OCI layer digest accounting (platform: linux/arm64)`
- **Target Platform**: `linux/arm64`
- **Catalog Version**: `2026-08-06.1` (SHA-256: `f45b04efc2ea6f271d49c6806b58bfc0f30503cb68944930609f6e0f71882a71`)
- **Total Approved Images**: 4
- **Configured Catalog Scales**: 4, 8, 16

## 2. Catalog Scalability Matrix

| Requested Scale | Approved Images Available | Storage Status | P2 Rec Status | Execution Status | Notes |
| :---: | :---: | :---: | :---: | :---: | :--- |
| 4 | 4 | `OBSERVED` | `OBSERVED` | `OBSERVED` | Fully executed |
| 8 | 4 | `NOT_EXECUTED` | `NOT_EXECUTED` | `NOT_EXECUTED` | insufficient_approved_images: catalog defines 4 approved image(s), 8 required |
| 16 | 4 | `NOT_EXECUTED` | `NOT_EXECUTED` | `NOT_EXECUTED` | insufficient_approved_images: catalog defines 4 approved image(s), 16 required |

## 3. Administrator-Approved Catalog Immutability Audit

Every participating image must satisfy the immutable input contract (`is_digest_pinned == True`).

| Priority | Image ID | Requested Reference | Digest Pinned? | Resolved Digest | Canonical Resolved Reference | Gate Status |
| :---: | :--- | :--- | :---: | :--- | :--- | :---: |
| 1 | **minimal-python** | `quay.io/jupyter/minimal-notebook@sha256:a153ceb6b41db4f86b7d7dc20c7b63d08e75e2038d5e8758b954fda50ed2e18d` | YES | `sha256:a153ceb6b41d...` | `quay.io/jupyter/minimal-n...` | PASS (Immutable) |
| 2 | **scipy-data-science** | `quay.io/jupyter/scipy-notebook@sha256:1a91a693c8cb086f3607f2ed38a2743ecd53dd1dac2d3e84e6cd647b33fd2bba` | YES | `sha256:1a91a693c8cb...` | `quay.io/jupyter/scipy-not...` | PASS (Immutable) |
| 3 | **pytorch-deep-learning** | `quay.io/jupyter/pytorch-notebook@sha256:69c72823a4e0dbee17114bbe44d0377bc9a39504a76f6314995f7d5bfaa98d60` | YES | `sha256:69c72823a4e0...` | `quay.io/jupyter/pytorch-n...` | PASS (Immutable) |
| 4 | **tensorflow-deep-learning** | `quay.io/jupyter/tensorflow-notebook@sha256:25ddc4f73bea5a252335775b59c0e1d4969bbf70ee78d3c6d1d9da02ce58fb68` | YES | `sha256:25ddc4f73bea...` | `quay.io/jupyter/tensorflo...` | PASS (Immutable) |

## 4. Cumulative Storage & Prefix Deduplication (Hypothesis H7)

Hypothesis H7 states: *Shared image layers require less cumulative storage than a naive logical sum as the frozen catalog grows.*

**Storage Accounting Semantics**:
- `LogicalImageBytes`: Sum of every ordered manifest layer descriptor occurrence in the prefix.
- `UniqueLayerBytes`: Cumulative byte volume of unique content-addressed layer digests in the prefix.
- `Deduplication Savings`: `LogicalImageBytes - UniqueLayerBytes`.

> [!NOTE]
> Prefix 1 contains repeated layer descriptors. Its logical-versus-unique difference is exactly 288 B, derived from the raw ordered descriptors in this package.

| Prefix | Introduced Image | Naive Logical Bytes | Unique Layer Bytes | Deduplication Savings | Savings Ratio | Within-Image Dups |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| 1 | **minimal-python** | 574,632,165 B | 574,631,877 B | 288 B | 0.00% | 1 digest(s) (288 B) |
| 2 | **scipy-data-science** | 1,860,068,091 B | 1,285,435,542 B | 574,632,549 B | 30.89% | 1 digest(s) (384 B) |
| 3 | **pytorch-deep-learning** | 6,802,924,134 B | 4,942,855,627 B | 1,860,068,507 B | 27.34% | 1 digest(s) (416 B) |
| 4 | **tensorflow-deep-learning** | 11,392,935,522 B | 8,247,431,025 B | 3,145,504,497 B | 27.61% | 1 digest(s) (448 B) |

## 5. Marginal Storage per Introduced Image (U_n - U_{n-1})

| Step | Image Introduced | Previous Unique Bytes | New Unique Bytes | Marginal Unique Bytes | Cumulative Logical | Cumulative Unique |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| 1 | **minimal-python** | 0 B | 574,631,877 B | **574,631,877 B** | 574,632,165 B | 574,631,877 B |
| 2 | **scipy-data-science** | 574,631,877 B | 1,285,435,542 B | **710,803,665 B** | 1,860,068,091 B | 1,285,435,542 B |
| 3 | **pytorch-deep-learning** | 1,285,435,542 B | 4,942,855,627 B | **3,657,420,085 B** | 6,802,924,134 B | 4,942,855,627 B |
| 4 | **tensorflow-deep-learning** | 4,942,855,627 B | 8,247,431,025 B | **3,304,575,398 B** | 11,392,935,522 B | 8,247,431,025 B |

## 6. Pairwise Layer-Reuse Matrices

Layer matching uses exact content digest equality (`layer.digest == other.digest`).
Pairwise shared layer count and byte metrics operate on **unique content-addressed layer digests**, not on ordered descriptor occurrences. Diagonal entries reflect the self unique layer digest count and self unique layer byte volume of each image.

### 6.1 Pairwise Shared Layer Storage (Bytes - Unique Digest Semantics)

| Image | minimal-python | scipy | pytorch | tensorflow |
| :--- | :---: | :---: | :---: | :---: |
| **minimal-python** | 574,631,877 | 574,631,877 | 574,631,877 | 574,631,877 |
| **scipy** | 574,631,877 | 1,285,435,542 | 1,285,435,542 | 1,285,435,542 |
| **pytorch** | 574,631,877 | 1,285,435,542 | 4,942,855,627 | 1,285,435,542 |
| **tensorflow** | 574,631,877 | 1,285,435,542 | 1,285,435,542 | 4,590,010,940 |

### 6.2 Pairwise Shared Layer Count (Unique Content Digests)

| Image | minimal-python | scipy | pytorch | tensorflow |
| :--- | :---: | :---: | :---: | :---: |
| **minimal-python** | 23 | 23 | 23 | 23 |
| **scipy** | 23 | 26 | 26 | 26 |
| **pytorch** | 23 | 26 | 27 | 26 |
| **tensorflow** | 23 | 26 | 26 | 30 |

## 7. Joint Recommendation Scalability & Split Provenance

| Scale | Storage Status | P2 Rec Status | Stage | Split Role | Dataset ID | Cases | P2 Acceptable Acc | P2 Preferred Acc | P2 Recall@5 | Mean Latency |
| :---: | :---: | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| 4 | `OBSERVED` | `OBSERVED` | `development` | `development` | `v5-development-reconstructed-v1` | 83 | 87.27% | 87.27% | 77.73% | 2.23 ms |
| 8 | `NOT_EXECUTED` | `NOT_EXECUTED` | `development` | `none` | `none` | 0 | N/A | N/A | N/A | N/A |
| 16 | `NOT_EXECUTED` | `NOT_EXECUTED` | `development` | `none` | `none` | 0 | N/A | N/A | N/A | N/A |

## 8. Metric Definitions & Auditability

- **`image_acceptable_accuracy`**: Primary Protocol-v5 image acceptability metric defined in `evaluation_v5/analysis/statistical_analysis.py`. Evaluates the proportion of feasible benchmark requests where the recommended image belongs to the gold acceptable candidate image set.
- **`image_preferred_accuracy`**: Strict Top-1 metric evaluating the proportion of feasible benchmark requests where the recommended image exactly matches the primary gold preferred image ID.
- **`retrieval_recall_at_k`**: Macro Recall@K of acceptable candidates within the top-K pre-constraint hybrid retrieval fused hit list ($K=5$ by default, consistent with `DEFAULT_RETRIEVAL_KS` in Protocol-v5 reporting).
- **`recommendation_latency`**: Total end-to-end elapsed time in seconds from request arrival to recommendation generation.
- **`marginal_unique_bytes`**: $U_n - U_{n-1}$, the incremental unique storage introduced by each new image in priority sequence.
- **`requested_reference`**: The administrator-approved reference configured in the catalog, audited for syntactical digest pinning (`@sha256:`).
- **`canonical_resolved_reference`**: The immutable content-addressable reference (`repository@sha256:<digest>`).

## 9. Generated Reproducible Figures

- **Figure A (Cumulative Storage)**: `figures/figure_a_cumulative_storage.png`
- **Figure B (Marginal Storage)**: `figures/figure_b_marginal_storage.png`
- **Figure C (Pairwise Reuse - Bytes)**: `figures/figure_c_pairwise_reuse_bytes.png`
- **Figure C (Pairwise Reuse - Count)**: `figures/figure_c_pairwise_reuse_count.png`
- **Figure D (Recommendation Quality)**: `figures/figure_d_recommendation_quality.png` (Configured scale markers; zero observed recommendation points plotted when confirmatory split is not supplied)
- **Figure E (Recommendation Latency)**: `figures/figure_e_recommendation_latency.png` (Configured scale markers; zero latency points or error bars plotted when recommendation evaluation is not executed)

## 10. Honest Scientific Claim Boundary
