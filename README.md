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

**Final Protocol-v5 audit: FAIL. Confirmatory experiments: NOT EXECUTED.**
The [final report](docs/evaluation/PROTOCOL_V5_FINAL_REPORT.md) records exact
observations, checksums, unresolved findings, and the defense-summary table.

| Evidence stream | Available evidence |
| --- | --- |
| E1 recommendation quality | 36 development records: P1/P2 on 18 cases across 10 families. Complete component/statistical analysis is **NOT EXECUTED**. |
| E2 natural-language robustness | Formal robustness analysis is **NOT EXECUTED**. |
| E3 human outcomes | **NOT EXECUTED**; zero participant sessions. |
| E4 resource efficiency | **NOT EXECUTED**; planning/readiness evidence only. |
| E5 image functionality | Archived development observations remain valid legacy evidence with explicit recommendation-provenance limitations; no current v1.4 observation has been collected. |
| E5 image storage | **NOT EXECUTED**. |
| Protocol-v5 confirmation | **NOT EXECUTED**; no authoritative final freeze or supplied confirmatory evidence. |

The audit preserves checksum/provenance defects and unavailable analyses.
Passing software tests or container probes does not establish user satisfaction,
resource savings, storage reuse, or confirmatory recommendation quality.
Protocol-v4 results remain [historical/formative evidence](docs/evaluation/PROTOCOL_V4_REVISED_EVALUATION_REPORT.md).

## Quickstart and reproduction

From a local checkout:

```bash
bash scripts/setup.sh
make v5-audit
```

The audit collects no new observations and contacts no recommender, LLM provider,
participant, container registry, or Kubernetes cluster. It writes a new immutable
run under `results_v5/protocol-v5.0.0/final-audit/` and produces its report before
returning nonzero for integrity failures. **The current evidence yields exit 2**;
a valid `NOT_EXECUTED` state alone is not a failure.

For staged commands, shared run IDs, historical evidence validation, software
tests, and deployment instructions, follow [Getting Started](docs/GETTING_STARTED.md).
The interactive demo uses local-only DummyAuthenticator; its default installer
selects P1. The guide documents the explicit P2 overlay.

## Documentation

- [Artifact and documentation index](docs/ARTIFACT_MANIFEST.md): source map, current guides, Protocol-v5 contracts, and historical evidence.
- [Architecture](docs/ARCHITECTURE.md): implemented pipelines, safeguards, and remaining research boundaries.
- [Final Protocol-v5 report](docs/evaluation/PROTOCOL_V5_FINAL_REPORT.md) and [audit verification record](docs/evaluation/PROTOCOL_V5_FINAL_AUDIT_VERIFICATION.md).
- [Evidence packaging and preservation](results_v5/README.md).
- [Demo script](DEMO_SCRIPT.md), [data governance](docs/DATA_GOVERNANCE.md), and [cleanup runbook](CLEANUP.md).
