# Protocol-v5 E5 Functional Validation Report: `e5-image-validation-20260905T020014Z`

## 1. Executive Summary and Provenance

- **Execution Timestamp (UTC)**: 2026-09-05T02:00:29.706341Z
- **Git Revision**: `4af44621a03f78b33bcfb4317a428f45510d78ee` (dirty: True)
- **Execution Mode**: `docker`
- **Evidence Status**: `OBSERVED`
- **Catalog Version**: `2026-08-06.1` (SHA-256: `f45b04efc2ea6f271d49c6806b58bfc0f30503cb68944930609f6e0f71882a71`)
- **Total Probe Specifications**: 17
- **Total Recommendations Evaluated**: 36

## 2. Multi-Dimensional Recommendation Performance

Performance is separated across three independent dimensions:
- **Dimension A (Gold-Label Correctness)**: Image matches benchmark YAML label.
- **Dimension B (Catalog Capability Coverage)**: Catalog declares all required capabilities.
- **Dimension C (Actual Functional Execution)**: Bounded in-container probes pass.

| System | Evaluated | Gold Acceptable (A) | Catalog Coverage (B) | Functional Pass (C) | Joint A & C |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **P1** | 18 | 72.2% | 94.4% | 88.9% | 72.2% |
| **P2** | 18 | 72.2% | 94.4% | 88.9% | 72.2% |

## 3. Mismatch and Discrepancy Detection

- **Catalog vs Probe Failures (`CATALOG_PROBE_MISMATCH`)**: 4
- **Label vs Operational Discrepancies (`LABEL_PASS_FUNCTIONAL_FAIL` / `LABEL_FAIL_FUNCTIONAL_PASS`)**: 6

### Catalog vs Probe Failures
| Case ID | System | Predicted Image | Failed Probes |
| :--- | :--- | :--- | :--- |
| `p2-required-gpu-unavailable-en` | P1 | `pytorch-deep-learning` | probe:pytorch-deep-learning:python(CONTAINER_LAUNCH_FAILED), probe:pytorch-deep-learning:pytorch(CONTAINER_LAUNCH_FAILED) |
| `p2-cpu-only-pandas-feasible-en` | P1 | `pytorch-deep-learning` | probe:pytorch-deep-learning:pandas(missing), probe:pytorch-deep-learning:python(CONTAINER_LAUNCH_FAILED) |
| `threshold-below-vi` | P2 | `pytorch-deep-learning` | probe:pytorch-deep-learning:python(CONTAINER_LAUNCH_FAILED) |
| `p2-required-gpu-unavailable-en` | P2 | `pytorch-deep-learning` | probe:pytorch-deep-learning:python(CONTAINER_LAUNCH_FAILED), probe:pytorch-deep-learning:pytorch(CONTAINER_LAUNCH_FAILED) |

### Label vs Operational Discrepancies
| Case ID | System | Predicted Image | Gold Preferred | Mismatch Category |
| :--- | :--- | :--- | :--- | :--- |
| `p2-minimum-eight-cpu-en` | P1 | `minimal-python` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-minimum-eight-memory-en` | P1 | `scipy-data-science` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-required-rapids-unsupported-en` | P1 | `minimal-python` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
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
