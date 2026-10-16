# Protocol-v5 E5 Functional Validation Report: `e5-image-validation-20260928T030939Z`

## 1. Executive Summary and Provenance

- **Execution Timestamp (UTC)**: 2026-09-28T03:10:13.866784Z
- **Git Revision**: `f7a4426472043d4652bede07556656800605ad95` (dirty: True)
- **Execution Mode**: `docker`
- **Evidence Status**: `OBSERVED`
- **Catalog Version**: `2026-08-06.1` (SHA-256: `f45b04efc2ea6f271d49c6806b58bfc0f30503cb68944930609f6e0f71882a71`)
- **Total Probe Specifications**: 17
- **Total Recommendations Evaluated**: 166
- **Source Recommendation Run**: `run-e1-offline` (recommendations SHA-256: `832ab0e3500e9e368b7d3336f89a520a7165ab121aa0f79e6f0709412cd9a153`)

## 2. Multi-Dimensional Recommendation Performance

Performance is separated across three independent dimensions:
- **Dimension A (Gold-Label Correctness)**: Recommendation matches benchmark gold label.
- **Dimension B (Catalog Capability Coverage)**: Administrator image catalog declares all required workload capabilities.
- **Dimension C (Actual Functional Execution)**: In-container functional capability probes pass when executed.

### Dimension A: Gold-Label Benchmark Correctness

| System | Total Cases | Preferred Match | Preferred Rate | Acceptable Match | Acceptable Rate |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **P1** | 83 | 34/83 | 41.0% | 34/83 | 41.0% |
| **P2** | 83 | 46/83 | 55.4% | 46/83 | 55.4% |

### Dimensions B & C: Catalog Coverage, Execution, and Operational Adequacy

| System | Total | With Image | Catalog Covered (B) | Functional Eligible | Functional Executed | Functional Pass (C) | Operational Adequacy | Joint (A & C) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **P1** | 83 | 83 | 69.9% (58/83) | 65 | 100.0% (65/65) | 100.0% (65/65) | 69.9% (58/83) | 52.3% |
| **P2** | 83 | 83 | 84.3% (70/83) | 76 | 100.0% (76/76) | 100.0% (76/76) | 84.3% (70/83) | 60.5% |

## 3. Mismatch and Discrepancy Detection

- **Catalog vs Probe Failures (`CATALOG_PROBE_MISMATCH`)**: 0
- **Catalog Underclaim Functional Pass (`CATALOG_UNDERCLAIM_FUNCTIONAL_PASS`)**: 13
- **Label vs Operational Discrepancies (`LABEL_PASS_FUNCTIONAL_FAIL` / `LABEL_FAIL_FUNCTIONAL_PASS`)**: 48

### Catalog Underclaim (Metadata Absent, Empirical Probe Passed)
| Case ID | System | Predicted Image | Underclaimed Capabilities |
| :--- | :--- | :--- | :--- |
| `p2-minimum-eight-cpu-vi-abbrev` | P1 | `pytorch-deep-learning` | python |
| `p2-minimum-eight-cpu-vi-constraint` | P1 | `pytorch-deep-learning` | python |
| `negative-constraint-minimal-canonical-en` | P1 | `tensorflow-deep-learning` | python |
| `negative-constraint-minimal-paraphrase-en` | P1 | `tensorflow-deep-learning` | python |
| `negative-constraint-minimal-vi-natural` | P1 | `tensorflow-deep-learning` | python |
| `negative-constraint-minimal-vi-pipeline` | P1 | `tensorflow-deep-learning` | python |
| `negative-constraint-minimal-vi-abbrev` | P1 | `tensorflow-deep-learning` | python |
| `basic-python-vi-natural` | P2 | `pytorch-deep-learning` | python |
| `p2-minimum-eight-cpu-vi-abbrev` | P2 | `pytorch-deep-learning` | python |
| `p2-minimum-eight-cpu-vi-constraint` | P2 | `pytorch-deep-learning` | python |

### Label vs Operational Discrepancies
| Case ID | System | Predicted Image | Gold Preferred | Mismatch Category |
| :--- | :--- | :--- | :--- | :--- |
| `p2-minimum-eight-cpu-en` | P1 | `minimal-python` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-minimum-eight-cpu-vi-natural` | P1 | `minimal-python` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-minimum-eight-memory-en` | P1 | `scipy-data-science` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-required-gpu-unavailable-en` | P1 | `pytorch-deep-learning` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-required-gpu-vi-natural` | P1 | `pytorch-deep-learning` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-required-gpu-vi-abbrev` | P1 | `pytorch-deep-learning` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-required-gpu-vi-constraint` | P1 | `pytorch-deep-learning` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-required-rapids-unsupported-en` | P1 | `minimal-python` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-required-rapids-vi-natural` | P1 | `pytorch-deep-learning` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-required-rapids-vi-abbrev` | P1 | `pytorch-deep-learning` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |

## 4. Security Enforcement

- **Administrator Catalog Boundary**: All tested images were strictly validated against the frozen administrator catalog.
- **Digest Immutability**: Arbitrary user-specified image tags were prohibited; all executed images used verified `@sha256:` content digests.

## 5. Limitations and Operational Constraints

- Probes are bounded to single-process capability verification.
- Workload memory limits were restricted to 1GiB.
- GPU hardware execution was not claimed; CPU fallbacks were validated.
