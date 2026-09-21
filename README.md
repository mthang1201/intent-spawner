# Intent- and Context-Aware Profile Recommendation for JupyterHub

This thesis prototype lets users describe a workload, preview a resource profile
and administrator-allowlisted notebook image, then confirm or override the
selection before KubeSpawner creates a pod. It also implements notebook
reprovisioning with PVC retention and opt-in policy-bounded resource sizing.

## Thesis systems

| System | Role |
| --- | --- |
| **B0** | Default/manual JupyterHub selection; no recommendation ranking. |
| **P1** | Existing rule-based recommender; frozen comparator. |
| **P2** | Structured Intent + hybrid retrieval + deterministic constraints/ranking; **main proposed method**. |
| **P3** | P2 + grounded LLM reranking; **not retained** after its formative development gate. |

Direct external and self-hosted LLM adapters remain available as implementations
and historical Protocol-v4 reference systems. Their results are separate from
Protocol-v5 evaluation of P2.

## Current evidence

Experiments are being re-run after removing the freeze/audit/human-review
layer that previously sat on top of them. There is no results collection,
"frozen" comparator manifest, or approval gate anymore: each experiment
(E1-E5 under `evaluation_v5/`) runs and simply produces results in a plain
run manifest. No results currently exist — previously collected
`results_v5/`, `results/`, and formative evidence were wiped ahead of this
cleanup, and no experiment has been re-executed yet.

Protocol-v4 results remain [historical/formative evidence](docs/evaluation/PROTOCOL_V4_REVISED_EVALUATION_REPORT.md).

## Quickstart and reproduction

From a local checkout:

```bash
bash scripts/setup.sh
make v5-test
```

For staged commands, shared run IDs, historical evidence validation, software
tests, and deployment instructions, follow [Getting Started](docs/GETTING_STARTED.md).
The interactive demo uses local-only DummyAuthenticator; its default installer
selects P1. The guide documents the explicit P2 overlay.

## Documentation

- [Artifact and documentation index](docs/ARTIFACT_MANIFEST.md): source map, current guides, Protocol-v5 contracts, and historical evidence.
- [Architecture](docs/ARCHITECTURE.md): implemented pipelines, safeguards, and remaining research boundaries.
- [Evidence packaging and preservation](results_v5/README.md).
- [Demo script](DEMO_SCRIPT.md), [data governance](docs/DATA_GOVERNANCE.md), and [cleanup runbook](CLEANUP.md).
