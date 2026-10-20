## 4. Automatic Limitations & Provenance Block

- **Independent Workload Families ($N$)**: `18`
- **Total Prompt Variants Evaluated**: `83`
- **Dataset Split**: `v5-development` (Role: `development`)
- **Dataset Canonical SHA-256**: `079ca97e88719b2e...`
- **Claims Permitted**: `NO (Formative/Development Evidence Only)`
- **Fallback Invocations**: `0`

### Missing Evidence & Completeness Status

- *No missing evidence: all planned offline evaluation cells for E1/E2 were executed and validated.*

### Unexecuted Experimental Conditions

- E3 (B0 vs P2 Real Human Usability Study): NOT_EXECUTED in offline harness
- E4 (Resource Efficiency & Live Cluster Execution): NOT_EXECUTED in offline harness
- E5 (Image Correctness & Multi-Node Image Storage): NOT_EXECUTED in offline harness

### Machine-Readable Statistical Statements

- *No confirmatory hypothesis tests meet significance thresholds.*

### Claim Boundary Constraints

- Workload FAMILY is the semantic independent unit; variants and repeats do not inflate N.
- Never claim general superiority unless Holm-adjusted p < 0.05 and confidence intervals strictly exclude zero.
- B0 does not generate rankings; MRR/nDCG/Hit@K are prohibited for B0.
