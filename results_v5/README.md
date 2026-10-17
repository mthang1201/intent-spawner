# Protocol-v5 Evaluation Results and Artifacts

This directory contains the experimental evidence, raw execution traces, derived metrics, and synthesis reports for the **Protocol-v5** evaluation of the Intent-Spawner framework.

## Structure

```text
results_v5/
└── protocol-v5.0.0/
    ├── E1/                 # Recommendation Quality (P1 vs P2)
    │   ├── run-e1-local/   # Deterministic Regex / Local Extractor
    │   └── run-e1-llm-*/   # LLM Extractor (e.g., gemini-3.5-flash-lite)
    ├── E2/                 # Natural Language Robustness
    │   ├── run-e2-local/   # Robustness report across linguistic variants
    │   └── run-e2-llm-*/   # LLM Robustness evaluation
    ├── E3/                 # Human Usability Study (B0 vs P2)
    │   ├── staging/        # Intermediate study event streams and sessions
    │   ├── run-e3-smoke/   # Synthetic harness validation & smoke checks
    │   ├── run-e3-pilot/   # Initial pilot run
    │   └── run-e3-observed/# Final observed participant trial outcomes
    ├── E4/                 # Kubernetes Resource Sizing & Pod Outcomes
    │   ├── run-e4-observed-local/      # Pod trials with local recommendations
    │   ├── run-e4-observed-llm-*/      # Pod trials with LLM recommendations
    │   └── *-analysis/                 # Derived resource efficiency packages
    ├── E5/                 # Container Image Storage & Functionality
    │   ├── run-e5-storage-orbstack/    # Image layer sharing & deduplication
    │   └── run-e5-functional-orbstack/ # Library probe validation in containers
    └── research-analysis/  # Cross-Experiment Thesis Claims & Adjudication
        ├── research-analysis-local-*/  # Local extractor thesis claim matrix
        └── research-analysis-llm-*/    # LLM extractor thesis claim matrix
```

## Experiment Summary

| Stream | Focus | Primary Artifacts | Key Metrics |
| :--- | :--- | :--- | :--- |
| **E1** | Recommendation Quality | `SUMMARY.md`, `summary_metrics.json` | Top-1 Profile Match, Image Match, Joint Accuracy, Latency |
| **E2** | NL Robustness | `ROBUSTNESS_REPORT.md` | Variant Stability, Paraphrase Consistency, Multilingual (EN/VI) |
| **E3** | Human Usability Study | `USER_STUDY_REPORT.md`, `limitations.md` | Decision Time, Selection Correctness, Task Completion, SUS Score |
| **E4** | Resource Allocation | `E4_EFFICIENCY_REPORT.md`, `tables/` | Safe Memory Envelopes, OOM Prevention, Overprovisioning Reduction |
| **E5** | Image Footprint & Probes | `E5_IMAGE_STORAGE_REPORT.md`, `E5_IMAGE_FUNCTIONAL_REPORT.md` | Layer Deduplication Ratio, Pull Latency, Functional Probe Pass Rate |
| **Research** | Thesis Synthesis | `thesis-results.md`, `claim-matrix.md`, `THREATS_TO_VALIDITY.md` | Formal Hypotheses H1–H8 Status across Local and LLM models |

## Execution & Reproduction

To reproduce or inspect these experiments, use the unified runner:

```bash
# Interactive menu:
make experiments
# or: python scripts/run_experiments.py

# Offline evaluation:
make eval-offline-local
make eval-offline-llm

# Synthesis & Research Analysis:
make research-analysis
```

See [`docs/PROTOCOL_V5_EXPERIMENT_GUIDE.md`](../docs/PROTOCOL_V5_EXPERIMENT_GUIDE.md) for full execution instructions.
