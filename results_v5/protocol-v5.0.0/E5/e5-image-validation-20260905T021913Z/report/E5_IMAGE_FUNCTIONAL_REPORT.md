# Protocol-v5 E5 Functional Validation Report: `e5-image-validation-20260905T021913Z`

## 1. Executive Summary and Provenance

- **Execution Timestamp (UTC)**: 2026-09-05T02:19:30.717353Z
- **Git Revision**: `4af44621a03f78b33bcfb4317a428f45510d78ee` (dirty: True)
- **Execution Mode**: `docker`
- **Evidence Status**: `INCOMPLETE`
- **Catalog Version**: `2026-08-06.1` (SHA-256: `f45b04efc2ea6f271d49c6806b58bfc0f30503cb68944930609f6e0f71882a71`)
- **Total Probe Specifications**: 17
- **Total Recommendations Evaluated**: 36

## 2. Multi-Dimensional Recommendation Performance

Performance is separated across three independent dimensions:
- **Dimension A (Gold-Label Correctness)**: Image matches benchmark YAML label.
- **Dimension B (Catalog Capability Coverage)**: Catalog declares all required capabilities.
- **Dimension C (Actual Functional Execution)**: Bounded in-container probes pass.

| System | Evaluated | Gold Acceptable (A) | Catalog Coverage (B) | Functional Coverage | Functional Pass (among executed) | Joint A & C |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **B0** | 18 | 33.3% (6/18) | 44.4% (8/18) | 44.4% (8/18) | 100.0% (8/8) | 75.0% |
| **P2** | 18 | 77.8% (14/18) | 77.8% (14/18) | 77.8% (14/18) | 100.0% (14/14) | 100.0% |

## 3. Mismatch and Discrepancy Detection

- **Catalog vs Probe Failures (`CATALOG_PROBE_MISMATCH`)**: 0
- **Label vs Operational Discrepancies (`LABEL_PASS_FUNCTIONAL_FAIL` / `LABEL_FAIL_FUNCTIONAL_PASS`)**: 2

### Label vs Operational Discrepancies
| Case ID | System | Predicted Image | Gold Preferred | Mismatch Category |
| :--- | :--- | :--- | :--- | :--- |
| `p2-minimum-eight-cpu-en` | B0 | `minimal-python` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |
| `p2-required-rapids-unsupported-en` | B0 | `minimal-python` | `None` | LABEL_FAIL_FUNCTIONAL_PASS |

## 4. Security Enforcement

- **Administrator Catalog Boundary**: All tested images were strictly validated against the frozen administrator catalog.
- **Digest Immutability**: Arbitrary user-specified image tags were prohibited; all executed images used verified `@sha256:` content digests.

## 5. Limitations and Operational Constraints

> [!WARNING]
> **Incomplete Run Notice**: One or more image probes were not executed or were unavailable in the container runtime. Evidence status is sealed as `INCOMPLETE`. No complete empirical claims are made.
