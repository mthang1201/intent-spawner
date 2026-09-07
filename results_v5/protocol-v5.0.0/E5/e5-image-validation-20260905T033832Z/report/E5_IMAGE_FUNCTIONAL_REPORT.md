# Protocol-v5 E5 Functional Validation Report: `e5-image-validation-20260905T033832Z`

## 1. Executive Summary and Provenance

- **Execution Timestamp (UTC)**: 2026-09-05T03:38:50.586812Z
- **Git Revision**: `d0f955caeb5d0efa9627305bbe8b0b9b2395e7de` (dirty: False)
- **Execution Mode**: `docker`
- **Evidence Status**: `OBSERVED`
- **Catalog Version**: `2026-08-06.1` (SHA-256: `f45b04efc2ea6f271d49c6806b58bfc0f30503cb68944930609f6e0f71882a71`)
- **Total Probe Specifications**: 17
- **Total Recommendations Evaluated**: 36
- **Recommendations Input**: `results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1/raw/recommendations.jsonl` (SHA-256: `25868752054231fa271e540210aad0845113ba3974722376029b18184959daf8`)

## 2. Multi-Dimensional Recommendation Performance

Performance is separated across three independent dimensions:
- **Dimension A (Gold-Label Correctness)**: Recommendation matches benchmark gold label.
- **Dimension B (Catalog Capability Coverage)**: Administrator image catalog declares all required workload capabilities.
- **Dimension C (Actual Functional Execution)**: In-container functional capability probes pass when executed.

### Dimension A: Gold-Label Benchmark Correctness

| System | Total Cases | Preferred Match | Preferred Rate | Acceptable Match | Acceptable Rate |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **P1** | 18 | 13/18 | 72.2% | 13/18 | 72.2% |
| **P2** | 18 | 13/18 | 72.2% | 13/18 | 72.2% |

### Dimensions B & C: Catalog Coverage, Execution, and Operational Adequacy

| System | Total | With Image | Catalog Covered (B) | Functional Eligible | Functional Executed | Functional Pass (C) | Operational Adequacy | Joint (A & C) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **P1** | 18 | 18 | 88.9% (16/18) | 16 | 100.0% (16/16) | 100.0% (16/16) | 88.9% (16/18) | 81.2% |
| **P2** | 18 | 18 | 88.9% (16/18) | 16 | 100.0% (16/16) | 100.0% (16/16) | 88.9% (16/18) | 81.2% |

## 3. Mismatch and Discrepancy Detection

- **Catalog vs Probe Failures (`CATALOG_PROBE_MISMATCH`)**: 0
- **Label vs Operational Discrepancies (`LABEL_PASS_FUNCTIONAL_FAIL` / `LABEL_FAIL_FUNCTIONAL_PASS`)**: 6

### Label vs Operational Discrepancies
| Case ID | System | Predicted Image | Gold Preferred | Mismatch Category |
| :--- | :--- | :--- | :--- | :--- |
| `p2-required-gpu-unavailable-en` | P1 | `pytorch-deep-learning` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-minimum-eight-cpu-en` | P1 | `minimal-python` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-minimum-eight-memory-en` | P1 | `scipy-data-science` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-required-gpu-unavailable-en` | P2 | `pytorch-deep-learning` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-minimum-eight-cpu-en` | P2 | `minimal-python` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-minimum-eight-memory-en` | P2 | `scipy-data-science` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |

## 4. Security Enforcement

- **Administrator Catalog Boundary**: All tested images were strictly validated against the frozen administrator catalog.
- **Digest Immutability**: Arbitrary user-specified image tags were prohibited; all executed images used verified `@sha256:` content digests.

## 5. Limitations and Operational Constraints

- Probes are bounded to single-process capability verification.
- Workload memory limits were restricted to 1GiB.
- GPU hardware execution was not claimed; CPU fallbacks were validated.
