# Protocol-v5 E5 Image Storage and Catalog Scalability Report: `e5-storage-scalability-20260912T124502Z`

## 1. Executive Summary & Provenance

- **Execution Timestamp (UTC)**: 2026-09-12T12:45:03.600427Z
- **Git Revision**: `38877fa71d04f4caad30f253a7cbbb049ecda4f8` (dirty: False)
- **Execution Status**: `OBSERVED`
- **Split Stage**: `confirmatory`
- **Claims Permitted**: `True`
- **Collector Origin**: `REAL_REGISTRY`
- **Collector**: `docker-manifest-inspect` (`storage-collector-v1.0.0`)
- **Size Domain**: `compressed_oci_manifest_layer_bytes` (compressed OCI manifest layer blobs)
- **Measurement Method**: `docker manifest inspect OCI layer digest accounting (platform: linux/amd64)`
- **Target Platform**: `linux/amd64`
- **Catalog Version**: `2026-08-06.1` (SHA-256: `f45b04efc2ea6f271d49c6806b58bfc0f30503cb68944930609f6e0f71882a71`)
- **Total Approved Images**: 4
- **Configured Catalog Scales**: 4, 8, 16

## 2. Catalog Scalability Matrix

| Requested Scale | Approved Images Available | Storage Status | P2 Rec Status | Execution Status | Notes |
| :---: | :---: | :---: | :---: | :---: | :--- |
| 4 | 4 | `OBSERVED` | `NOT_EXECUTED` | `OBSERVED` | confirmatory_dataset_not_provided: sealed confirmatory split required for confirmatory stage |
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
| 1 | **minimal-python** | 575,161,576 B | 575,161,288 B | 288 B | 0.00% | 1 digest(s) (288 B) |
| 2 | **scipy-data-science** | 1,878,375,513 B | 1,303,213,553 B | 575,161,960 B | 30.62% | 1 digest(s) (384 B) |
| 3 | **pytorch-deep-learning** | 7,183,064,355 B | 5,304,688,426 B | 1,878,375,929 B | 26.15% | 1 digest(s) (416 B) |
| 4 | **tensorflow-deep-learning** | 12,070,068,082 B | 8,888,478,152 B | 3,181,589,930 B | 26.36% | 1 digest(s) (448 B) |

## 5. Marginal Storage per Introduced Image (U_n - U_{n-1})

| Step | Image Introduced | Previous Unique Bytes | New Unique Bytes | Marginal Unique Bytes | Cumulative Logical | Cumulative Unique |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| 1 | **minimal-python** | 0 B | 575,161,288 B | **575,161,288 B** | 575,161,576 B | 575,161,288 B |
| 2 | **scipy-data-science** | 575,161,288 B | 1,303,213,553 B | **728,052,265 B** | 1,878,375,513 B | 1,303,213,553 B |
| 3 | **pytorch-deep-learning** | 1,303,213,553 B | 5,304,688,426 B | **4,001,474,873 B** | 7,183,064,355 B | 5,304,688,426 B |
| 4 | **tensorflow-deep-learning** | 5,304,688,426 B | 8,888,478,152 B | **3,583,789,726 B** | 12,070,068,082 B | 8,888,478,152 B |

## 6. Pairwise Layer-Reuse Matrices

Layer matching uses exact content digest equality (`layer.digest == other.digest`).
Pairwise shared layer count and byte metrics operate on **unique content-addressed layer digests**, not on ordered descriptor occurrences. Diagonal entries reflect the self unique layer digest count and self unique layer byte volume of each image.

### 6.1 Pairwise Shared Layer Storage (Bytes - Unique Digest Semantics)

| Image | minimal-python | scipy | pytorch | tensorflow |
| :--- | :---: | :---: | :---: | :---: |
| **minimal-python** | 575,161,288 | 575,161,288 | 575,161,288 | 575,161,288 |
| **scipy** | 575,161,288 | 1,303,213,553 | 1,303,213,553 | 1,303,213,553 |
| **pytorch** | 575,161,288 | 1,303,213,553 | 5,304,688,426 | 1,303,213,553 |
| **tensorflow** | 575,161,288 | 1,303,213,553 | 1,303,213,553 | 4,887,003,279 |

### 6.2 Pairwise Shared Layer Count (Unique Content Digests)

| Image | minimal-python | scipy | pytorch | tensorflow |
| :--- | :---: | :---: | :---: | :---: |
| **minimal-python** | 23 | 23 | 23 | 23 |
| **scipy** | 23 | 26 | 26 | 26 |
| **pytorch** | 23 | 26 | 27 | 26 |
| **tensorflow** | 23 | 26 | 26 | 30 |

## 7. Joint Recommendation Scalability & Split Provenance

> [!NOTE]
> **Zero Confirmatory Recommendation Observations**: Scale 4 recommendation is `NOT_EXECUTED` (`confirmatory_dataset_not_provided: sealed confirmatory split required for confirmatory stage`); Scales 8 and 16 are `NOT_EXECUTED` (`insufficient_approved_images`).

| Scale | Storage Status | P2 Rec Status | Stage | Split Role | Dataset ID | Cases | P2 Acceptable Acc | P2 Preferred Acc | P2 Recall@5 | Mean Latency |
| :---: | :---: | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| 4 | `OBSERVED` | `NOT_EXECUTED` | `confirmatory` | `none` | `none` | 0 | N/A | N/A | N/A | N/A |
| 8 | `NOT_EXECUTED` | `NOT_EXECUTED` | `confirmatory` | `none` | `none` | 0 | N/A | N/A | N/A | N/A |
| 16 | `NOT_EXECUTED` | `NOT_EXECUTED` | `confirmatory` | `none` | `none` | 0 | N/A | N/A | N/A | N/A |

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

> [!IMPORTANT]
> **Constrained Claim Verdict (PASS_WITH_LIMITATIONS)**:
> For the frozen four-image amd64 catalog, direct OCI manifest inspection measured 12,070,068,082 logical compressed layer bytes and 8,888,478,152 unique content-addressed compressed layer bytes, corresponding to 26.36% deduplication under the experiment's digest-based storage accounting. > This empirically confirms Hypothesis **H7** for the frozen 4-image catalog.
>
> **Storage Claim Semantics & Size Domain**:
> The measured domain is `compressed_oci_manifest_layer_bytes` (content-addressed compressed OCI layer-blob > accounting under digest deduplication). This represents immutable registry/container image manifest layer bytes > and does not represent unpacked container filesystem disk usage, snapshotter storage, overlay filesystem overhead, > or actual filesystem block allocation.
>
> **Recommendation Evaluation Scope**: Zero confirmatory recommendation observations (Scale 4 NOT_EXECUTED due to absence of sealed confirmatory split; Scales 8 and 16 NOT_EXECUTED due to insufficient approved images). Storage measurement has 1 observed scale (N=4); recommendation has 0 confirmatory observed scales.
>
> **Scalability Boundary Limitation**:
> Larger catalog scales (8 and 16 images) remain `NOT_EXECUTED` because additional administrator-approved > immutable images have not been published in the repository catalog. With only scale 4 observed for storage, > **no empirical 4→8→16 multi-scale trend is yet estimable**.
