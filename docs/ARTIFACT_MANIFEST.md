# Artifact and Documentation Index

Start with the [project overview](../README.md) and [Getting Started](GETTING_STARTED.md).
This index describes current navigation; historical reports retain the facts and
identities of the repository snapshots they describe.

## Current Guides

| Document | Purpose |
| --- | --- |
| [Getting Started](GETTING_STARTED.md) | Running the Protocol-v5 experiments, software checks, and optional demo deployment. |
| [Architecture](ARCHITECTURE.md) | B0/P1/P2/P3 contracts, implemented capabilities, and remaining research/production boundaries. |
| [Demo script](../DEMO_SCRIPT.md) | Interactive presentation scenes; demonstrations are separate from confirmatory evidence. |
| [Helm backend deployment](HELM_BACKEND_DEPLOYMENT.md) | Runtime packaging, backend overlays, Secret references, and rollout identities. |
| [External LLM adapter](EXTERNAL_LLM_RECOMMENDER.md) | Provider configuration and fallback behavior. |
| [Self-hosted LLM adapter](SELF_HOSTED_LLM_RECOMMENDER.md) | Local inference configuration and limitations. |
| [Notebook reprovisioning](INTENT_AWARE_REPROVISIONING.md) | Stop/recreate lifecycle, confirmation, and PVC retention. |
| [Dynamic resource sizing](DYNAMIC_PROFILE_GENERATION.md) | Opt-in policy constraints and static per-spawn caps. |
| [Preview design](evaluation/RECOMMENDATION_PREVIEW_DESIGN.md) | Preview state machine and audit fields. |
| [Data governance](DATA_GOVERNANCE.md) | Privacy, storage, and retention rules. |
| [Cleanup](../CLEANUP.md) | Exact demo cleanup and evidence preservation. |

## Protocol-v5 Evidence and Contracts

The freeze/final-audit system and the manual human-review approval gate that
previously sat on top of the Protocol-v5 evaluation harness have been removed.
Each experiment (E1-E5) is now just "run it, get results": it executes and
writes a plain run manifest, with no separate immutable-freeze step and no
approval gate blocking it. No results currently exist for any experiment —
previously collected evidence was wiped ahead of this cleanup, and no
experiment has been re-executed yet.

| Document or artifact | Purpose and evidence boundary |
| --- | --- |
| [Evidence packaging](../results_v5/README.md) | Portable allowlist and offline workflow for whatever a run produces. |
| [Experiment architecture](evaluation/PROTOCOL_V5_ARCHITECTURE.md) | Protocol design and manifest contracts; historical status statements are not current execution results. |
| [Data isolation](evaluation/PROTOCOL_V5_DATA_ISOLATION.md) | Development/confirmatory split, custody, and loader boundaries enforced by `evaluation_v5/isolation.py`. |
| [Isolation verification](evaluation/PROTOCOL_V5_ISOLATION_VERIFICATION.md) | Adversarial software checks; no proof of external custody. |
| [Offline runner](evaluation/PROTOCOL_V5_OFFLINE_RUNNER.md) | E1 raw execution, provenance, and validation interface. |
| [E5 image functional contract](evaluation/PROTOCOL_V5_IMAGE_FUNCTIONAL.md) | Bounded runtime semantics, cleanup, source-run provenance binding, and legacy evidence classification. |
| [Component scoring](evaluation/PROTOCOL_V5_COMPONENT_SCORING.md) | Component metric definitions and required gold inputs. |
| [Statistical analysis](evaluation/PROTOCOL_V5_STATISTICAL_ANALYSIS.md) | Family aggregation, estimands, pairing, and inferential gates. |
| [Research analysis](evaluation/PROTOCOL_V5_RESEARCH_ANALYSIS.md) | Claim-aware analysis interface. |
| [E3 human study](evaluation/PROTOCOL_V5_USER_STUDY.md) | B0-versus-P2 crossover design, participant pairing, privacy, and readiness. |
| [E3 smoke verification](evaluation/PROTOCOL_V5_USER_STUDY_SMOKE_TEST.md) | Synthetic application checks; no observed participant outcomes. |
| [E4 resource envelope](evaluation/PROTOCOL_V5_RESOURCE_ENVELOPE.md) | Independent calibration and oracle contracts. |
| [Development benchmark guide](../benchmarks_v5/README.md) and [gold authoring](../benchmarks_v5/GOLD_AUTHORING.md) | Checksum-bound dataset documentation; no sealed confirmatory cases are supplied to tuning. |

Passing a package validator does not certify pipeline provenance or establish
its hypothesis. Workload families, rather than repeated calls, are the semantic
independent unit for offline/resource inference; B0 has no ranking metrics.

## Source and Configuration Map

| Path | Responsibility |
| --- | --- |
| `recommender/` | P1 rules, P2 structured extraction/hybrid retrieval/constraints, optional P3 reranker, policy validation, preview integration, image catalog, and direct LLM adapters. |
| `helm/` | B0 values; proposed preview/reprovisioning form; explicit P1/P2/P3 and reference LLM overlays; opt-in dynamic resources and E3 study overlay. |
| `evaluation_v5/offline/`, `evaluation_v5/robustness/` | E1 recommendation evidence and E2 variant harnesses. |
| `evaluation_v5/analysis/` | Derived metrics/statistics and offline validation → analysis → reporting. |
| `evaluation_v5/user_study/` | E3 assignment, event/privacy validation, paired analysis, and reporting. |
| `evaluation_v5/resource/` | E4 independent calibration, readiness checks, and resource-efficiency harnesses. |
| `evaluation_v5/image_storage/` | E5 image functionality and storage evidence/validation. |
| `benchmarks_v5/` | Versioned development data, schemas, registries, resource/study contracts, and audit inventory. |
| `evaluation_v4/` | Historical dataset loaders, evidence validators, aggregation/statistics, and renderers reused where applicable. |
| `evaluation_p2/`, `evaluation_p3/`, `evaluation_final/` | Earlier integration/gate/final-evaluation infrastructure; not substitutes for v5 confirmation. |
| `benchmarks/`, `experiments/` | Earlier synthetic workload manifests, local runners, raw records, and analysis. |
| `cluster_evaluation/`, `k8s/`, `workload/` | Cluster harnesses, image/workload specifications, and demo resources. |
| `results/`, `results_v5/` | Historical and v5 evidence, respectively; raw/derived/report layers remain distinct. |
| `tests/`, `recommender/test_*.py` | Software validation; synthetic tests do not count as observed experiments. |

[`scripts/setup.sh`](../scripts/setup.sh) installs the repository dependencies.
[`Makefile`](../Makefile) defines the v5 experiment/test targets.
[`scripts/check.sh`](../scripts/check.sh) adds broad tests, smoke checks, and
optional Helm/Kubernetes validation. The supported demo installers are
[`install-baseline.sh`](../scripts/install-baseline.sh),
[`install-proposed.sh`](../scripts/install-proposed.sh), and
[`install-dynamic.sh`](../scripts/install-dynamic.sh); the proposed installer
packages the runtime and applies an explicit backend overlay.

## Historical and Formative Evidence

These documents retain their original protocols, comparator definitions, and
RQ numbering. Terms such as “final” or “confirmatory” in their titles refer to
those historical protocols, never to newly observed Protocol-v5 confirmation.

| Evidence family | Entry points |
| --- | --- |
| P2 integration and P3 development gate | [P2 evaluation](evaluation/P2_BACKEND_EVALUATION_V1.md), [P3 negative decision](evaluation/P3_INCREMENTAL_EVALUATION_V1.md) |
| Earlier final-evaluation design | [Final Evaluation Protocol v1](evaluation/FINAL_EVALUATION_PROTOCOL_V1.md) |
| Protocol-v4 methods and reproduction | [Protocol](evaluation/EVALUATION_V4_PROTOCOL.md), [four-method specification](evaluation/PROTOCOL_V4_FOUR_METHOD_EVALUATION.md), [reproduction](evaluation/PROTOCOL_V4_REPRODUCIBILITY.md), [collection contract](evaluation/EVIDENCE_COLLECTION_V4.md) |
| Protocol-v4 results | [Combined report](evaluation/PROTOCOL_V4_REVISED_EVALUATION_REPORT.md), [external LLM results](evaluation/PROTOCOL_V4_EXTERNAL_LLM_LIVE_REPORT.md), [Stage C results](evaluation/STAGE_C_CONFIRMATORY_REPORT.md) |
| Protocol-v4 threats to validity | [Threats](evaluation/THREATS_TO_VALIDITY.md) |
| Local and earlier cluster experiments | [Local protocol](evaluation/EXPERIMENT_PROTOCOL.md), [local results](evaluation/RESULTS.md), [cluster protocol](evaluation/CLUSTER_EXPERIMENT_PROTOCOL.md), [cluster provenance](evaluation/CLUSTER_PROVENANCE.md), [cluster results](evaluation/CLUSTER_RESULTS.md) |
| Resource-envelope v3 | [Protocol](evaluation/RESOURCE_ENVELOPE_PROTOCOL_V3.md), [implementation audit](evaluation/RESOURCE_ENVELOPE_V3_IMPLEMENTATION_AUDIT.md) |
| Historical integration and handoffs | [Branch integration](evaluation/BRANCH_INTEGRATION_REPORT.md), [handoff log](evaluation/NEXT_AGENT_CONTEXT.md) |

## Portable Evidence and Preservation

The final-audit sealed packages and completion-audit outputs previously
published here belonged to the now-deleted freeze/final-audit system and no
longer exist. Whatever a Protocol-v5 experiment run produces going forward is
a plain run manifest under `results_v5/`, without a separate sealed/audit
package layer.

The Protocol-v4 portable core contains 13 files, including three recommendation
matrices and the Stage C summary/plan/manifests. Validate it with
`.venv/bin/python scripts/validate-portable-evidence.py`. Its 2,244 Stage C
sidecars belong to the optional deep archive and are not guaranteed in a clean
checkout. The historical [external checksum manifest](evaluation/PROTOCOL_V4_EXTERNAL_SHA256SUMS.txt)
and [raw evidence manifest](evaluation/RAW_EVIDENCE_SHA256SUMS.txt) remain intact.

Historical reports, raw artifacts, and checksum-bound Markdown stay in place.
The handoff log records a pre-held-out model amendment referenced by the frozen
external-LLM configuration. The superseded `IMPLEMENTATION_ROADMAP.md` has been
removed; its useful capability and future-work boundaries now live in
[Architecture](ARCHITECTURE.md). Historical records mentioning that filename are
preserved as descriptions of earlier repository states.

**Known legacy navigation defect:** the checksum-bound
[evaluation_p3 results README](../evaluation_p3/results/README.md) contains an
incorrect relative report link. Its bytes are preserved; use this
[correct P3 report link](evaluation/P3_INCREMENTAL_EVALUATION_V1.md).
Generated reports and ignored temporary copies are not edited as documentation
cleanup. Future evidence corrections must be separately linked artifacts.
