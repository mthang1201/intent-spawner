# Intent- and Context-Aware Profile Recommendation for JupyterHub

This thesis prototype lets users describe a workload, preview a resource profile
and administrator-allowlisted notebook image, then confirm or override the
selection before KubeSpawner creates a pod. It also implements notebook
reprovisioning with PVC retention and opt-in policy-bounded resource sizing.

## Thesis Systems

| System | Description | Research Role |
| --- | --- | --- |
| **B0** | Default JupyterHub manual hardware profile selection. | Baseline comparator for human study (E3). |
| **P1** | Rule-based heuristic profile recommender. | Deterministic baseline comparator (E1/E2). |
| **P2** | Structured Intent + Hybrid Retrieval (BM25 + Dense) + Deterministic Constraints. | **Main proposed method** evaluated across experiments E1–E5. |

## Quickstart

From a local checkout:

```bash
# 1. Environment setup
bash scripts/setup.sh

# 2. Run core unit tests
make test

# 3. Interactive experiment runner (E1–E5)
make experiments
# or directly: python scripts/run_experiments.py

# 4. Quick offline evaluation
make eval-offline-local
```

## Documentation

The essential documentation, experiment runbooks, and evaluation results are located in [`docs/`](docs/) and [`results_v5/`](results_v5/):

- [**Getting Started**](docs/GETTING_STARTED.md): Local environment setup, software verification, and deployment instructions.
- [**System Architecture**](docs/ARCHITECTURE.md): End-to-end design, P2 hybrid retrieval pipeline, constraint evaluation, and preview state machine.
- [**Experiment Reproduction Guide**](docs/PROTOCOL_V5_EXPERIMENT_GUIDE.md): Comprehensive runbook to reproduce Protocol-v5 experiments (E1–E5).
- [**Evaluation Results & Evidence**](results_v5/README.md): Tracked experimental evidence for E1 (Quality), E2 (Robustness), E3 (User Study), E4 (Resources), E5 (Images), and Cross-Experiment Synthesis.
- [**Demo Script**](docs/DEMO_SCRIPT.md): Step-by-step walkthrough for live interactive presentations.
- [**Artifact & Documentation Index**](docs/ARTIFACT_MANIFEST.md): Complete index of all technical documents, specifications, and research artifacts.
