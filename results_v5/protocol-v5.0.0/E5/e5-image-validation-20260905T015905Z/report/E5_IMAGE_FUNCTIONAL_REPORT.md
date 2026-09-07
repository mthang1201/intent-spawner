# Protocol-v5 E5 Functional Validation Report: `e5-image-validation-20260905T015905Z`

## 1. Executive Summary and Provenance

- **Execution Timestamp (UTC)**: 2026-09-05T01:59:05.876794Z
- **Git Revision**: `4af44621a03f78b33bcfb4317a428f45510d78ee` (dirty: True)
- **Execution Mode**: `dry_run`
- **Evidence Status**: `DRY_RUN`
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
| **P1** | 18 | 72.2% | 94.4% | 0.0% | 0.0% |
| **P2** | 18 | 72.2% | 94.4% | 0.0% | 0.0% |

## 3. Mismatch and Discrepancy Detection

- **Catalog vs Probe Failures (`CATALOG_PROBE_MISMATCH`)**: 0
- **Label vs Operational Discrepancies (`LABEL_PASS_FUNCTIONAL_FAIL` / `LABEL_FAIL_FUNCTIONAL_PASS`)**: 0

## 4. Security Enforcement

- **Administrator Catalog Boundary**: All tested images were strictly validated against the frozen administrator catalog.
- **Digest Immutability**: Arbitrary user-specified image tags were prohibited; all executed images used verified `@sha256:` content digests.

## 5. Limitations and Operational Constraints

> [!NOTE]
> **Dry-Run Notice**: No live container or Kubernetes workloads were executed in this run. Probe outcomes are logged as `NOT_EXECUTED_DRY_RUN`. No operational performance claims are made.
