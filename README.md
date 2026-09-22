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
bash scripts/setup.sh
make v5-test
```

## Documentation

The essential documentation and experiment runbooks are located in [`docs/`](docs/):

- [**Getting Started**](docs/GETTING_STARTED.md): Local environment setup, software verification, and deployment instructions.
- [**System Architecture**](docs/ARCHITECTURE.md): End-to-end design, P2 hybrid retrieval pipeline, constraint evaluation, and preview state machine.
- [**Experiment Reproduction Guide**](docs/PROTOCOL_V5_EXPERIMENT_GUIDE.md): Comprehensive 4-stage runbook to reproduce Protocol-v5 experiments (E1–E5).
- [**Demo Script**](docs/DEMO_SCRIPT.md): Step-by-step walkthrough for live interactive presentations.
- [**Artifact & Documentation Index**](docs/ARTIFACT_MANIFEST.md): Complete index of all technical documents, specifications, and research artifacts.
