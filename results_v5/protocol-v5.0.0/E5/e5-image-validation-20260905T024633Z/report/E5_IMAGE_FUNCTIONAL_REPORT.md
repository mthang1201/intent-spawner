# Protocol-v5 E5 Functional Validation Report: `e5-image-validation-20260905T024633Z`

## 1. Executive Summary and Provenance

- **Execution Timestamp (UTC)**: 2026-09-05T02:46:52.289352Z
- **Git Revision**: `fbf91ee65f1623102970e7863226baf0c8a27b0b` (dirty: False)
- **Execution Mode**: `docker`
- **Evidence Status**: `OBSERVED`
- **Catalog Version**: `2026-08-06.1` (SHA-256: `f45b04efc2ea6f271d49c6806b58bfc0f30503cb68944930609f6e0f71882a71`)
- **Total Probe Specifications**: 17
- **Total Recommendations Evaluated**: 36
- **Recommendations Input**: `results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1/raw/recommendations.jsonl` (SHA-256: `25868752054231fa271e540210aad0845113ba3974722376029b18184959daf8`)

## 2. Multi-Dimensional Recommendation Performance

Performance is separated across three independent dimensions:
- **Dimension A (Gold-Label Correctness)**: Image matches benchmark YAML label.
- **Dimension B (Catalog Capability Coverage)**: Catalog declares all required capabilities.
- **Dimension C (Actual Functional Execution)**: Bounded in-container probes pass.

| System | Evaluated | Gold Acceptable (A) | Catalog Coverage (B) | Functional Coverage | Functional Pass (among executed) | Joint A & C |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **P1** | 18 | 72.2% (13/18) | 94.4% (17/18) | 94.4% (17/18) | 100.0% (17/17) | 76.5% |
| **P2** | 18 | 72.2% (13/18) | 94.4% (17/18) | 100.0% (18/18) | 100.0% (18/18) | 72.2% |

## 3. Mismatch and Discrepancy Detection

- **Catalog vs Probe Failures (`CATALOG_PROBE_MISMATCH`)**: 0
- **Label vs Operational Discrepancies (`LABEL_PASS_FUNCTIONAL_FAIL` / `LABEL_FAIL_FUNCTIONAL_PASS`)**: 8

### Label vs Operational Discrepancies
| Case ID | System | Predicted Image | Gold Preferred | Mismatch Category |
| :--- | :--- | :--- | :--- | :--- |
| `p2-required-gpu-unavailable-en` | P1 | `pytorch-deep-learning` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-minimum-eight-cpu-en` | P1 | `minimal-python` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-minimum-eight-memory-en` | P1 | `scipy-data-science` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-required-rapids-unsupported-en` | P1 | `minimal-python` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-required-gpu-unavailable-en` | P2 | `pytorch-deep-learning` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-minimum-eight-cpu-en` | P2 | `minimal-python` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-minimum-eight-memory-en` | P2 | `scipy-data-science` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-required-rapids-unsupported-en` | P2 | `minimal-python` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |

## 4. Security Enforcement

- **Administrator Catalog Boundary**: All tested images were strictly validated against the frozen administrator catalog.
- **Digest Immutability**: Arbitrary user-specified image tags were prohibited; all executed images used verified `@sha256:` content digests.

## 5. Limitations and Operational Constraints

- Probes are bounded to single-process capability verification.
- Workload memory limits were restricted to 1GiB.
- GPU hardware execution was not claimed; CPU fallbacks were validated.
