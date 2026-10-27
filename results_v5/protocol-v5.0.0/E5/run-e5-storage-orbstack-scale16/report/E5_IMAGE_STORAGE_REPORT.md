# Protocol-v5 E5 Image Storage and Catalog Scalability Report: `e5-storage-scalability-20261024T094314Z`

## 1. Executive Summary & Provenance

- **Execution Timestamp (UTC)**: 2026-10-24T09:43:18.328211Z
- **Git Revision**: `00eca1ee4d4fe3f7a9fef370a94aa9453718dcc8` (dirty: True)
- **Execution Status**: `OBSERVED`
- **Split Stage**: `development`
- **Claims Permitted**: `False`
- **Collector Origin**: `REAL_REGISTRY`
- **Collector**: `docker-manifest-inspect` (`storage-collector-v1.0.0`)
- **Size Domain**: `compressed_oci_manifest_layer_bytes` (compressed OCI manifest layer blobs)
- **Measurement Method**: `docker manifest inspect OCI layer digest accounting (platform: linux/arm64)`
- **Target Platform**: `linux/arm64`
- **Catalog Version**: `2026-10-24.1` (SHA-256: `b6ed61bb8a9d7aea3d8d7d5c69c4a58db2af8df3f6b31c3c13acfcc8527e68c3`)
- **Total Approved Images**: 16
- **Configured Catalog Scales**: 4, 8, 16

## 2. Catalog Scalability Matrix

| Requested Scale | Approved Images Available | Storage Status | P2 Rec Status | Execution Status | Notes |
| :---: | :---: | :---: | :---: | :---: | :--- |
| 4 | 4 | `OBSERVED` | `NOT_EXECUTED` | `OBSERVED` | recommendation_eval_skipped |
| 8 | 8 | `OBSERVED` | `NOT_EXECUTED` | `OBSERVED` | recommendation_eval_skipped |
| 16 | 16 | `OBSERVED` | `NOT_EXECUTED` | `OBSERVED` | recommendation_eval_skipped |

## 3. Administrator-Approved Catalog Immutability Audit

Every participating image must satisfy the immutable input contract (`is_digest_pinned == True`).

| Priority | Image ID | Requested Reference | Digest Pinned? | Resolved Digest | Canonical Resolved Reference | Gate Status |
| :---: | :--- | :--- | :---: | :--- | :--- | :---: |
| 1 | **minimal-python** | `quay.io/jupyter/minimal-notebook@sha256:a153ceb6b41db4f86b7d7dc20c7b63d08e75e2038d5e8758b954fda50ed2e18d` | YES | `sha256:a153ceb6b41d...` | `quay.io/jupyter/minimal-n...` | PASS (Immutable) |
| 2 | **scipy-data-science** | `quay.io/jupyter/scipy-notebook@sha256:1a91a693c8cb086f3607f2ed38a2743ecd53dd1dac2d3e84e6cd647b33fd2bba` | YES | `sha256:1a91a693c8cb...` | `quay.io/jupyter/scipy-not...` | PASS (Immutable) |
| 3 | **pytorch-deep-learning** | `quay.io/jupyter/pytorch-notebook@sha256:69c72823a4e0dbee17114bbe44d0377bc9a39504a76f6314995f7d5bfaa98d60` | YES | `sha256:69c72823a4e0...` | `quay.io/jupyter/pytorch-n...` | PASS (Immutable) |
| 4 | **tensorflow-deep-learning** | `quay.io/jupyter/tensorflow-notebook@sha256:25ddc4f73bea5a252335775b59c0e1d4969bbf70ee78d3c6d1d9da02ce58fb68` | YES | `sha256:25ddc4f73bea...` | `quay.io/jupyter/tensorflo...` | PASS (Immutable) |
| 5 | **base-notebook** | `quay.io/jupyter/base-notebook@sha256:cc7aa7b4f008920a301a72d50b2523490a9f918e9a32c427b7a890a4a35afe24` | YES | `sha256:cc7aa7b4f008...` | `quay.io/jupyter/base-note...` | PASS (Immutable) |
| 6 | **r-data-science** | `quay.io/jupyter/r-notebook@sha256:9a3cd29ccb1c4399d4cef51d4da7f0de319a2223a46b36520eab4b521e7ca044` | YES | `sha256:9a3cd29ccb1c...` | `quay.io/jupyter/r-noteboo...` | PASS (Immutable) |
| 7 | **datascience-notebook** | `quay.io/jupyter/datascience-notebook@sha256:f67d85ca84554300afa4e755c993e3cc8a8c02c433a319187e9cad4cc7907ed1` | YES | `sha256:f67d85ca8455...` | `quay.io/jupyter/datascien...` | PASS (Immutable) |
| 8 | **pyspark-notebook** | `quay.io/jupyter/pyspark-notebook@sha256:23791276fb4faf2b879e3cac898c07e9570738f573d9c379e54033bf9a350e46` | YES | `sha256:23791276fb4f...` | `quay.io/jupyter/pyspark-n...` | PASS (Immutable) |
| 9 | **all-spark-notebook** | `quay.io/jupyter/all-spark-notebook@sha256:e451d23c14f3754c0877fc44c035f52f77220208152dd9431b4859c887b6392d` | YES | `sha256:e451d23c14f3...` | `quay.io/jupyter/all-spark...` | PASS (Immutable) |
| 10 | **docker-stacks-foundation** | `quay.io/jupyter/docker-stacks-foundation@sha256:c5f8bbc0c63e59732b276b4b0d6ca2d569695106d45b1ad4beec169513620da0` | YES | `sha256:c5f8bbc0c63e...` | `quay.io/jupyter/docker-st...` | PASS (Immutable) |
| 11 | **julia-notebook** | `quay.io/jupyter/julia-notebook@sha256:a881516f6103e3d10d0a6f8d4e5528a7d9fbd5b8e0b3ef8b89c798e0b2f00dde` | YES | `sha256:a881516f6103...` | `quay.io/jupyter/julia-not...` | PASS (Immutable) |
| 12 | **pytorch-cuda** | `quay.io/jupyter/pytorch-notebook@sha256:133a7fc7ad0e569b0d4aa90fd6ec64e9f15126ae355bb0a086760b7270217926` | YES | `sha256:133a7fc7ad0e...` | `quay.io/jupyter/pytorch-n...` | PASS (Immutable) |
| 13 | **tensorflow-cuda** | `quay.io/jupyter/tensorflow-notebook@sha256:79e91ff9b50f4ca4b7b88df0dafe88cff79598c062d16521f808c7b5e5a1ced5` | YES | `sha256:79e91ff9b50f...` | `quay.io/jupyter/tensorflo...` | PASS (Immutable) |
| 14 | **scipy-python313** | `quay.io/jupyter/scipy-notebook@sha256:987bbee395831e8a2b1ee4e989cb41122cc79099ca8052681dc2af5948d64f86` | YES | `sha256:987bbee39583...` | `quay.io/jupyter/scipy-not...` | PASS (Immutable) |
| 15 | **minimal-python313** | `quay.io/jupyter/minimal-notebook@sha256:9e94dfc5e4e794791b923bea89084530020f6bcb1c1236066d847e477ba21503` | YES | `sha256:9e94dfc5e4e7...` | `quay.io/jupyter/minimal-n...` | PASS (Immutable) |
| 16 | **scipy-notebook-legacy** | `quay.io/jupyter/scipy-notebook@sha256:6cd9b6bad850b10a34777702e3ecb8e610e9ecf25cb50200ffbf02de9095f60a` | YES | `sha256:6cd9b6bad850...` | `quay.io/jupyter/scipy-not...` | PASS (Immutable) |

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
| 5 | **base-notebook** | 11,735,944,424 B | 8,590,439,639 B | 3,145,504,785 B | 26.80% | 1 digest(s) (256 B) |
| 6 | **r-data-science** | 13,102,519,634 B | 9,614,005,851 B | 3,488,513,783 B | 26.62% | 1 digest(s) (352 B) |
| 7 | **datascience-notebook** | 15,550,448,654 B | 11,496,433,755 B | 4,054,014,899 B | 26.07% | 1 digest(s) (448 B) |
| 8 | **pyspark-notebook** | 17,490,144,287 B | 12,147,583,516 B | 5,342,560,771 B | 30.55% | 1 digest(s) (480 B) |
| 9 | **all-spark-notebook** | 19,893,600,970 B | 12,611,344,502 B | 7,282,256,468 B | 36.61% | 1 digest(s) (544 B) |
| 10 | **docker-stacks-foundation** | 20,093,798,371 B | 12,611,344,502 B | 7,482,453,869 B | 37.24% | 1 digest(s) (128 B) |
| 11 | **julia-notebook** | 21,162,531,143 B | 13,114,576,254 B | 8,047,954,889 B | 38.03% | 1 digest(s) (352 B) |
| 12 | **pytorch-cuda** | 26,104,294,055 B | 16,767,793,358 B | 9,336,500,697 B | 35.77% | 1 digest(s) (416 B) |
| 13 | **tensorflow-cuda** | 30,632,414,100 B | 20,007,367,563 B | 10,625,046,537 B | 34.69% | 1 digest(s) (448 B) |
| 14 | **scipy-python313** | 31,920,959,876 B | 20,007,367,563 B | 11,913,592,313 B | 37.32% | 1 digest(s) (384 B) |
| 15 | **minimal-python313** | 32,486,460,832 B | 20,007,367,563 B | 12,479,093,269 B | 38.41% | 1 digest(s) (288 B) |
| 16 | **scipy-notebook-legacy** | 33,668,458,552 B | 21,189,364,400 B | 12,479,094,152 B | 37.06% | 2 digest(s) (851 B) |

## 5. Marginal Storage per Introduced Image (U_n - U_{n-1})

| Step | Image Introduced | Previous Unique Bytes | New Unique Bytes | Marginal Unique Bytes | Cumulative Logical | Cumulative Unique |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| 1 | **minimal-python** | 0 B | 574,631,877 B | **574,631,877 B** | 574,632,165 B | 574,631,877 B |
| 2 | **scipy-data-science** | 574,631,877 B | 1,285,435,542 B | **710,803,665 B** | 1,860,068,091 B | 1,285,435,542 B |
| 3 | **pytorch-deep-learning** | 1,285,435,542 B | 4,942,855,627 B | **3,657,420,085 B** | 6,802,924,134 B | 4,942,855,627 B |
| 4 | **tensorflow-deep-learning** | 4,942,855,627 B | 8,247,431,025 B | **3,304,575,398 B** | 11,392,935,522 B | 8,247,431,025 B |
| 5 | **base-notebook** | 8,247,431,025 B | 8,590,439,639 B | **343,008,614 B** | 11,735,944,424 B | 8,590,439,639 B |
| 6 | **r-data-science** | 8,590,439,639 B | 9,614,005,851 B | **1,023,566,212 B** | 13,102,519,634 B | 9,614,005,851 B |
| 7 | **datascience-notebook** | 9,614,005,851 B | 11,496,433,755 B | **1,882,427,904 B** | 15,550,448,654 B | 11,496,433,755 B |
| 8 | **pyspark-notebook** | 11,496,433,755 B | 12,147,583,516 B | **651,149,761 B** | 17,490,144,287 B | 12,147,583,516 B |
| 9 | **all-spark-notebook** | 12,147,583,516 B | 12,611,344,502 B | **463,760,986 B** | 19,893,600,970 B | 12,611,344,502 B |
| 10 | **docker-stacks-foundation** | 12,611,344,502 B | 12,611,344,502 B | **0 B** | 20,093,798,371 B | 12,611,344,502 B |
| 11 | **julia-notebook** | 12,611,344,502 B | 13,114,576,254 B | **503,231,752 B** | 21,162,531,143 B | 13,114,576,254 B |
| 12 | **pytorch-cuda** | 13,114,576,254 B | 16,767,793,358 B | **3,653,217,104 B** | 26,104,294,055 B | 16,767,793,358 B |
| 13 | **tensorflow-cuda** | 16,767,793,358 B | 20,007,367,563 B | **3,239,574,205 B** | 30,632,414,100 B | 20,007,367,563 B |
| 14 | **scipy-python313** | 20,007,367,563 B | 20,007,367,563 B | **0 B** | 31,920,959,876 B | 20,007,367,563 B |
| 15 | **minimal-python313** | 20,007,367,563 B | 20,007,367,563 B | **0 B** | 32,486,460,832 B | 20,007,367,563 B |
| 16 | **scipy-notebook-legacy** | 20,007,367,563 B | 21,189,364,400 B | **1,181,996,837 B** | 33,668,458,552 B | 21,189,364,400 B |

## 6. Pairwise Layer-Reuse Matrices

Layer matching uses exact content digest equality (`layer.digest == other.digest`).
Pairwise shared layer count and byte metrics operate on **unique content-addressed layer digests**, not on ordered descriptor occurrences. Diagonal entries reflect the self unique layer digest count and self unique layer byte volume of each image.

### 6.1 Pairwise Shared Layer Storage (Bytes - Unique Digest Semantics)

| Image | minimal-python | scipy | pytorch | tensorflow | base-notebook | r | datascience-notebook | pyspark-notebook | all-spark-notebook | docker-stacks-foundation | julia-notebook | pytorch-cuda | tensorflow-cuda | scipy-python313 | minimal-python313 | scipy-notebook-legacy |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **minimal-python** | 574,631,877 | 574,631,877 | 574,631,877 | 574,631,877 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 |
| **scipy** | 574,631,877 | 1,285,435,542 | 1,285,435,542 | 1,285,435,542 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 |
| **pytorch** | 574,631,877 | 1,285,435,542 | 4,942,855,627 | 1,285,435,542 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 |
| **tensorflow** | 574,631,877 | 1,285,435,542 | 1,285,435,542 | 4,590,010,940 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 |
| **base-notebook** | 32 | 32 | 32 | 32 | 343,008,646 | 343,008,646 | 343,008,646 | 343,008,646 | 343,008,646 | 200,197,273 | 343,008,646 | 343,008,646 | 343,008,646 | 343,008,646 | 343,008,646 | 32 |
| **r** | 32 | 32 | 32 | 32 | 343,008,646 | 1,366,574,858 | 565,500,668 | 565,500,668 | 565,500,668 | 200,197,273 | 565,500,668 | 565,500,668 | 565,500,668 | 565,500,668 | 565,500,668 | 32 |
| **datascience-notebook** | 32 | 32 | 32 | 32 | 343,008,646 | 565,500,668 | 2,447,928,572 | 1,288,545,392 | 1,288,545,392 | 200,197,273 | 565,500,668 | 1,288,545,392 | 1,288,545,392 | 1,288,545,392 | 565,500,668 | 32 |
| **pyspark-notebook** | 32 | 32 | 32 | 32 | 343,008,646 | 565,500,668 | 1,288,545,392 | 1,939,695,153 | 1,939,695,153 | 200,197,273 | 565,500,668 | 1,288,545,392 | 1,288,545,392 | 1,288,545,392 | 565,500,668 | 32 |
| **all-spark-notebook** | 32 | 32 | 32 | 32 | 343,008,646 | 565,500,668 | 1,288,545,392 | 1,939,695,153 | 2,403,456,139 | 200,197,273 | 565,500,668 | 1,288,545,392 | 1,288,545,392 | 1,288,545,392 | 565,500,668 | 32 |
| **docker-stacks-foundation** | 32 | 32 | 32 | 32 | 200,197,273 | 200,197,273 | 200,197,273 | 200,197,273 | 200,197,273 | 200,197,273 | 200,197,273 | 200,197,273 | 200,197,273 | 200,197,273 | 200,197,273 | 32 |
| **julia-notebook** | 32 | 32 | 32 | 32 | 343,008,646 | 565,500,668 | 565,500,668 | 565,500,668 | 565,500,668 | 200,197,273 | 1,068,732,420 | 565,500,668 | 565,500,668 | 565,500,668 | 565,500,668 | 32 |
| **pytorch-cuda** | 32 | 32 | 32 | 32 | 343,008,646 | 565,500,668 | 1,288,545,392 | 1,288,545,392 | 1,288,545,392 | 200,197,273 | 565,500,668 | 4,941,762,496 | 1,288,545,392 | 1,288,545,392 | 565,500,668 | 32 |
| **tensorflow-cuda** | 32 | 32 | 32 | 32 | 343,008,646 | 565,500,668 | 1,288,545,392 | 1,288,545,392 | 1,288,545,392 | 200,197,273 | 565,500,668 | 1,288,545,392 | 4,528,119,597 | 1,288,545,392 | 565,500,668 | 32 |
| **scipy-python313** | 32 | 32 | 32 | 32 | 343,008,646 | 565,500,668 | 1,288,545,392 | 1,288,545,392 | 1,288,545,392 | 200,197,273 | 565,500,668 | 1,288,545,392 | 1,288,545,392 | 1,288,545,392 | 565,500,668 | 32 |
| **minimal-python313** | 32 | 32 | 32 | 32 | 343,008,646 | 565,500,668 | 565,500,668 | 565,500,668 | 565,500,668 | 200,197,273 | 565,500,668 | 565,500,668 | 565,500,668 | 565,500,668 | 565,500,668 | 32 |
| **scipy-notebook-legacy** | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 32 | 1,181,996,869 |

### 6.2 Pairwise Shared Layer Count (Unique Content Digests)

| Image | minimal-python | scipy | pytorch | tensorflow | base-notebook | r | datascience-notebook | pyspark-notebook | all-spark-notebook | docker-stacks-foundation | julia-notebook | pytorch-cuda | tensorflow-cuda | scipy-python313 | minimal-python313 | scipy-notebook-legacy |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **minimal-python** | 23 | 23 | 23 | 23 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| **scipy** | 23 | 26 | 26 | 26 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| **pytorch** | 23 | 26 | 27 | 26 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| **tensorflow** | 23 | 26 | 26 | 30 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| **base-notebook** | 1 | 1 | 1 | 1 | 20 | 20 | 20 | 20 | 20 | 15 | 20 | 20 | 20 | 20 | 20 | 1 |
| **r** | 1 | 1 | 1 | 1 | 20 | 26 | 24 | 24 | 24 | 15 | 24 | 24 | 24 | 24 | 24 | 1 |
| **datascience-notebook** | 1 | 1 | 1 | 1 | 20 | 24 | 31 | 27 | 27 | 15 | 24 | 27 | 27 | 27 | 24 | 1 |
| **pyspark-notebook** | 1 | 1 | 1 | 1 | 20 | 24 | 27 | 34 | 34 | 15 | 24 | 27 | 27 | 27 | 24 | 1 |
| **all-spark-notebook** | 1 | 1 | 1 | 1 | 20 | 24 | 27 | 34 | 37 | 15 | 24 | 27 | 27 | 27 | 24 | 1 |
| **docker-stacks-foundation** | 1 | 1 | 1 | 1 | 15 | 15 | 15 | 15 | 15 | 15 | 15 | 15 | 15 | 15 | 15 | 1 |
| **julia-notebook** | 1 | 1 | 1 | 1 | 20 | 24 | 24 | 24 | 24 | 15 | 26 | 24 | 24 | 24 | 24 | 1 |
| **pytorch-cuda** | 1 | 1 | 1 | 1 | 20 | 24 | 27 | 27 | 27 | 15 | 24 | 28 | 27 | 27 | 24 | 1 |
| **tensorflow-cuda** | 1 | 1 | 1 | 1 | 20 | 24 | 27 | 27 | 27 | 15 | 24 | 27 | 31 | 27 | 24 | 1 |
| **scipy-python313** | 1 | 1 | 1 | 1 | 20 | 24 | 27 | 27 | 27 | 15 | 24 | 27 | 27 | 27 | 24 | 1 |
| **minimal-python313** | 1 | 1 | 1 | 1 | 20 | 24 | 24 | 24 | 24 | 15 | 24 | 24 | 24 | 24 | 24 | 1 |
| **scipy-notebook-legacy** | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 26 |

## 7. Joint Recommendation Scalability & Split Provenance

> [!NOTE]
> **Zero Confirmatory Recommendation Observations**: Scale 4 recommendation is `NOT_EXECUTED` (`confirmatory_dataset_not_provided: sealed confirmatory split required for confirmatory stage`); Scales 8 and 16 are `NOT_EXECUTED` (`insufficient_approved_images`).

| Scale | Storage Status | P2 Rec Status | Stage | Split Role | Dataset ID | Cases | P2 Acceptable Acc | P2 Preferred Acc | P2 Recall@5 | Mean Latency |
| :---: | :---: | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| 4 | `OBSERVED` | `NOT_EXECUTED` | `development` | `none` | `none` | 0 | N/A | N/A | N/A | N/A |
| 8 | `OBSERVED` | `NOT_EXECUTED` | `development` | `none` | `none` | 0 | N/A | N/A | N/A | N/A |
| 16 | `OBSERVED` | `NOT_EXECUTED` | `development` | `none` | `none` | 0 | N/A | N/A | N/A | N/A |

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
