# Protocol-v5 Evaluation Results and Artifacts

This directory contains the experimental evidence, raw execution traces, derived metrics, confusion matrices, and synthesis reports for the **Protocol-v5** evaluation of the Intent-Spawner framework.

## Directory Structure

```text
results_v5/
├── baseline_benchmark_v1/             # E1 Benchmark v1 Evaluation Results (Prompt 3.2)
│   ├── SUMMARY.md                     # Markdown summary table with stratified breakdown
│   ├── metrics.json                   # Comprehensive metrics (Acc@1, Acc@3, Acceptability, Under/Over)
│   ├── predictions.jsonl              # Per-test-case predictions for P1 and P2
│   ├── confusion_p1.png               # Confusion matrix plot for P1 (Rule-Based)
│   └── confusion_p2.png               # Confusion matrix plot for P2 (Intent-Spawner)
├── cluster_simulation_summary.csv     # E4 Large-scale cluster simulation aggregate metrics
├── cluster_simulation_results.json    # E4 Detailed multi-seed cluster simulation telemetry
└── protocol-v5.0.0/
    ├── E1/                            # Recommendation Quality (P1 vs P2)
    │   ├── run-e1-local/              # Deterministic Local NLP Extractor (confusion, metrics, predictions)
    │   └── run-e1-llm-*/              # LLM Extractor partitions (e.g. gemini-3.5-flash-lite)
    ├── E2/                            # Natural Language Robustness
    │   ├── run-e2-local/              # Robustness report across 68 linguistic variants
    │   └── run-e2-llm-*/              # LLM Robustness evaluation
    ├── E3/                            # Human Usability Study (B0 vs P2)
    │   ├── staging/                   # Intermediate study event streams and sessions
    │   ├── run-e3-smoke/              # Synthetic harness validation & smoke checks
    │   ├── run-e3-pilot/              # Initial pilot run
    │   └── run-e3-observed/           # Final observed participant trial outcomes
    ├── E4/                            # Kubernetes Resource Sizing & Pod Outcomes
    │   ├── run-e4-observed-local/     # Physical pod trials with local recommendations
    │   ├── run-e4-observed-llm-*/     # Physical pod trials with LLM recommendations
    │   └── *-analysis/                # Derived resource efficiency packages
    ├── E5/                            # Container Image Storage & Functionality
    │   ├── run-e5-storage-orbstack/   # Image layer sharing & deduplication
    │   └── run-e5-functional-orbstack/# Library probe validation in containers
    └── research-analysis/             # Cross-Experiment Thesis Claims & Adjudication
        ├── research-analysis-local-*/ # Local extractor thesis claim matrix & LaTeX tables
        └── research-analysis-llm-*/   # LLM extractor thesis claim matrix & LaTeX tables
```

## Experiment Summary

| Stream | Focus | Primary Artifacts | Key Metrics |
| :--- | :--- | :--- | :--- |
| **E1** | Recommendation Quality | `baseline_benchmark_v1/SUMMARY.md`, `metrics.json` | Top-1 & Top-3 Profile/Image Match, Joint Acceptability, OOM Risk, Resource Waste |
| **E2** | NL Robustness | `E2/run-e2-local/ROBUSTNESS_REPORT.md` | Variant Stability, Paraphrase Consistency, Multilingual (EN/VI) |
| **E3** | Human Usability Study | `E3/run-e3-observed/USER_STUDY_REPORT.md` | Decision Time, Selection Correctness, Task Completion, SUS Score |
| **E4** | Resource Allocation & Simulation | `cluster_simulation_summary.csv`, `figure_large_cluster_simulation.pdf` | OOM Cascades, Queuing Delay, Resource Utilization, Safe Envelopes |
| **E5** | Image Footprint & Probes | `E5/run-e5-storage-orbstack/`, `E5/run-e5-functional-orbstack/` | Layer Deduplication Ratio, Pull Latency, Functional Probe Pass Rate |
| **Research** | Thesis Synthesis | `thesis-results.md`, `claim-matrix.md`, `THREATS_TO_VALIDITY.md` | Formal Hypotheses H1–H8 Status across Local and LLM models |

## Execution & Reproduction

```bash
# Interactive menu:
make experiments
# or: python scripts/run_experiments.py

# Offline Benchmark v1 evaluation:
make eval-offline-local
make eval-offline-llm

# Large-scale cluster simulation:
make simulate-large-cluster

# Synthesis & Research Analysis:
make report
make research-analysis
```

See [`docs/PROTOCOL_V5_EXPERIMENT_GUIDE.md`](../docs/PROTOCOL_V5_EXPERIMENT_GUIDE.md) for full execution instructions.
