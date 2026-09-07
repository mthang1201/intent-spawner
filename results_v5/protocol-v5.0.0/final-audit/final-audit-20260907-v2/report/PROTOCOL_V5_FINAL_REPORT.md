# Protocol-v5 Final Reproducibility and Evidence Report

**Audit verdict: FAIL. Confirmatory experiments: NOT EXECUTED. Primary proposed method: P2.**

This report closes the reviewed repository evidence snapshot. Audit completion does not certify that every requirement passed. Design, genuine development execution, historical evidence and confirmatory evidence are separated below.

Input inventory: [benchmarks_v5/protocol-v5-final-audit-inputs-v1.json](../../../../../benchmarks_v5/protocol-v5-final-audit-inputs-v1.json); SHA-256 `8898df1187694e6a3e185d7ba23b253c23377ef2f05aba2f69378f3c1bf70f62`

Archived audit outputs: [all findings](audit.json), [run provenance](../run.json), [regeneration comparisons](../analysis/regeneration.json).

The inventory was captured at `2026-09-07T01:42:29.953662+00:00` from Git revision `ca2ab18ec410c8765f921683241b3f3433d5e118`. It preserves the bytes found, including damaged or incomplete packages; it is not a retroactive experiment freeze. Each source manifest records its own collection revision, timestamp, dataset and environment. Audit implementation/runtime provenance is recorded in the generated run and stage manifests.

## Research questions and hypotheses

| Question | Definition |
| --- | --- |
| RQ1 | Does P2 improve top-one recommendation quality relative to frozen P1? |
| RQ2 | Is P2 more robust than P1 to semantically equivalent natural-language surface forms? |
| RQ3 | Does P2 improve real-user selection effectiveness, speed, and perceived usability relative to B0? |
| RQ4 | Does P2 improve resource efficiency and oracle calibration under the frozen Kubernetes protocol? |
| RQ5 | Are P2 image selections functionally correct and is shared-layer storage lower than naive catalog storage? |
| RQ6 | If retained, does P3 improve recommendation quality enough to remain within its frozen practical-overhead budget? |

| Hypothesis | RQ | Predeclared statement | Confirmatory decision |
| --- | --- | --- | --- |
| H1 | RQ1 | P2 has higher JointAccept@1 than P1. | NOT_EXECUTED |
| H2 | RQ2 | P2 loses less JointAccept@1 under reviewed-equivalent surface-form changes than P1. | NOT_EXECUTED |
| H3 | RQ3 | Users achieve acceptable selections more often and faster with P2 than B0. | NOT_EXECUTED |
| H4 | RQ3 | Users report greater task ease and usability with P2 than B0. | NOT_EXECUTED |
| H5 | RQ4 | P2 Catalog reduces requested-resource cost per successful workload while preserving reliability relative to Static Large. | NOT_EXECUTED |
| H6 | RQ4 | P2 Dynamic has lower CPU-request and memory-request absolute oracle error than P2 Catalog. | NOT_EXECUTED |
| H7F | RQ5 | P2 recommended images satisfy every required functional capability probe at immutable digests. | NOT_EXECUTED |
| H7 | RQ5 | UniqueLayerBytes grows more slowly than naive LogicalImageBytes over the frozen ordered catalog expansion. | NOT_EXECUTED |
| H8 | RQ6 | Retained P3 improves JointAccept@1 over P2 while remaining within a separately frozen practical-overhead budget. | NOT_EXECUTED |

Hypotheses and decision predicates are reused from the checksum-bound claim registry. None has sufficient authenticated confirmatory evidence for SUPPORTED or NOT_SUPPORTED. Missing results do not contradict a hypothesis.

## Systems

| System | Definition |
| --- | --- |
| B0 | Ordinary/manual JupyterHub selection; no recommendation ranking. |
| P1 | Frozen existing rule-based recommender. |
| P2 | Structured Intent + hybrid retrieval + deterministic constraints/ranking; main proposed method. |
| P3 | P2 plus grounded LLM reranking; not retained by the development decision. |

B0 has no MRR, nDCG or Hit@K outcome. Artifact names containing 'observed-run' do not determine execution status.

## Experiment matrix and sample boundaries

| Experiment | Comparison | Evidence status |
| --- | --- | --- |
| E1 | P1 vs P2 | Development raw outputs available; component/statistical analysis NOT EXECUTED |
| E2 | P1 vs P2 natural-language variants | Formal robustness analysis NOT EXECUTED |
| E3 | B0 vs P2 human crossover | NOT EXECUTED; zero participant sessions |
| E4 | Static Large, P1 Catalog, P2 Catalog, P2 Dynamic | NOT EXECUTED; planning/readiness packages only |
| E5 functional | Image labels, catalog capabilities, container probes | Development observations; unresolved manifest provenance |
| E5 storage | Shared-layer reuse and catalog expansion | NOT EXECUTED |
| E6 | Optional P2 vs P3 confirmation | NOT EXECUTED; P3 not retained |

E1 raw execution: **36 records**, **18 cases**, **10 workload families**; P1: 18 records, P2: 18 records. These are development observations, not confirmatory accuracy samples. [results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1/raw/recommendations.jsonl](../../../E1/20260825T-observed-p1-p2-development-v1/raw/recommendations.jsonl); SHA-256 `25868752054231fa271e540210aad0845113ba3974722376029b18184959daf8`

E4 **design only**: 16 workload families × 4 conditions × 10 repetition blocks = 640 planned trials. Zero observed hardware trials. [results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-plan-20260905T082000Z/plan.json](../../../E4/e4-resource-efficiency-plan-20260905T082000Z/plan.json) `/trials`; SHA-256 `5260288122847397f2ec7d1db80f20044be2a7fa39e8dab7a885847e38d6db81`

E3 target enrollment is 36 participants in the readiness design; observed enrollment and measured outcomes are zero. Assignments and synthetic smoke actions are not participant observations.

## Methods and statistical boundaries

E1 preserves paired outputs from frozen P1/P2 on the visible development split. The raw validator recomputes matrix coverage, checksums and case bindings. The current complete component/statistical pipeline requires frozen family gold or compiled split v2; the visible v1 bundle is insufficient. The audit does not fill its missing labels.

E5 regeneration rejoins original recommendations, visible gold, catalog metadata and preserved immutable-image probe outcomes. It independently recomputes functional evaluation rows and aggregate metrics. Gold-label agreement, catalog capability declarations and in-container functional success are different constructs. A passing import probe is not proof of GPU hardware, workload success, correct resource allocation, image storage savings or overall recommendation quality.

The workload family is the semantic unit for offline/resource inference. Variants and repeated calls describe within-family variation. Human study analysis follows its participant/task pairing and crossover contract. Image probes share digests across recommendations and are not independent human or semantic samples. The two current E5 runs are reported separately and never pooled.

## Exact available development observations

| Run | System | Image-label matches | Functional passes / eligible cases | Cases with undefined required probe |
| --- | --- | --- | --- | --- |
| e5-image-validation-20260905T040730Z | P1 | 13/18 | 16/16 | 2 |
| e5-image-validation-20260905T040730Z | P2 | 13/18 | 17/17 | 1 |
| e5-image-validation-20260905T071910Z | P1 | 13/18 | 16/16 | 2 |
| e5-image-validation-20260905T071910Z | P2 | 13/18 | 17/17 | 1 |

`e5-image-validation-20260905T040730Z`: 17/17 configured probes passed, 0 failed, 0 unavailable. These probes are reused when evaluating recommendations. [results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T040730Z/derived/functional_metrics.json](../../../E5/e5-image-validation-20260905T040730Z/derived/functional_metrics.json) `/probe_summary`; SHA-256 `ca2ac88bf9d89e94c7df8b472e284652bacd662e612607730371e7c7921dafea`

P1: conservative functional success 16/18 (recorded rate 0.8889); catalog-underclaim cases 0; label-fail/functional-pass cases 3. [results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T040730Z/derived/functional_metrics.json](../../../E5/e5-image-validation-20260905T040730Z/derived/functional_metrics.json) `/systems/P1`; SHA-256 `ca2ac88bf9d89e94c7df8b472e284652bacd662e612607730371e7c7921dafea`

P2: conservative functional success 17/18 (recorded rate 0.9444); catalog-underclaim cases 1; label-fail/functional-pass cases 3. [results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T040730Z/derived/functional_metrics.json](../../../E5/e5-image-validation-20260905T040730Z/derived/functional_metrics.json) `/systems/P2`; SHA-256 `ca2ac88bf9d89e94c7df8b472e284652bacd662e612607730371e7c7921dafea`

`e5-image-validation-20260905T071910Z`: 17/17 configured probes passed, 0 failed, 0 unavailable. These probes are reused when evaluating recommendations. [results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T071910Z/derived/functional_metrics.json](../../../E5/e5-image-validation-20260905T071910Z/derived/functional_metrics.json) `/probe_summary`; SHA-256 `ca2ac88bf9d89e94c7df8b472e284652bacd662e612607730371e7c7921dafea`

P1: conservative functional success 16/18 (recorded rate 0.8889); catalog-underclaim cases 0; label-fail/functional-pass cases 3. [results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T071910Z/derived/functional_metrics.json](../../../E5/e5-image-validation-20260905T071910Z/derived/functional_metrics.json) `/systems/P1`; SHA-256 `ca2ac88bf9d89e94c7df8b472e284652bacd662e612607730371e7c7921dafea`

P2: conservative functional success 17/18 (recorded rate 0.9444); catalog-underclaim cases 1; label-fail/functional-pass cases 3. [results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T071910Z/derived/functional_metrics.json](../../../E5/e5-image-validation-20260905T071910Z/derived/functional_metrics.json) `/systems/P2`; SHA-256 `ca2ac88bf9d89e94c7df8b472e284652bacd662e612607730371e7c7921dafea`

The image-label match counts do not show an advantage for P2 in these runs. This is a bounded descriptive observation, not a family-level hypothesis test. The extractor provenance mismatch blocks claims that the complete pipeline matched a final freeze.

### Confidence intervals and effect sizes

Protocol-v5 inferential confidence intervals, p-values and standardized effect sizes: **N/A — NOT EXECUTED**. Complete offline gold is unavailable; human, resource and storage experiments were not executed. No interval is inferred from repetitions or recycled probes.

### Failure analysis and P3 decision

The E3 participant-flow CSV no longer matches its recorded checksum. In-memory LF→CRLF reconstruction matches the historical digest, consistent with the repository CSV newline policy. Original bytes and checksums are preserved; regeneration creates a separate corrected artifact. Older E4 contracts fail current validators and remain historical development packages. An older E5 OBSERVED package lacks required retrieval provenance. Its v1.0 probe records predate execution_status and must be interpreted with the legacy error-category adapter; missing fields do not mean missing executions. These are audit limitations and failures, not inferred performance effects.

The preserved P3 development/formative decision excludes P3 from the main contribution: its historical evaluation reported no wrong-to-correct transitions, one regression, and substantial reranking overhead. This audit does not relabel that earlier evaluation as Protocol-v5 confirmation. [docs/evaluation/P3_INCREMENTAL_EVALUATION_V1.md](../../../../../docs/evaluation/P3_INCREMENTAL_EVALUATION_V1.md); SHA-256 `3ed59ae76bd79e5e86cb08f63f86d7a49c8c5632f578b74f8ee51a1f8c4b8d9f`

## Human, resource and image-storage outcomes

Human study: **NOT EXECUTED**. Satisfaction, usability, decision-time saving and participant selection outcomes are unavailable. Resource study: **NOT EXECUTED**. CPU/memory savings, capacity, OOM and runtime effects are unavailable. Image storage study: **NOT EXECUTED**. No measured logical bytes, unique layer bytes, node storage use or expansion savings exist. The functional image manifests record digests, but host metadata alone does not establish container platform identity.

## Defense-summary table

| Professor criterion | Experiment | Metric | Observed result | Evidence reference |
| --- | --- | --- | --- | --- |
| satisfaction | E3 | SEQ ease; SUS usability | NOT EXECUTED | [results_v5/protocol-v5.0.0/E3/b0-p2-user-study-readiness/report/status.json](../../../E3/b0-p2-user-study-readiness/report/status.json) `/execution_status`; SHA-256 `de94e53ae14942647411e1e9033dbc9d1ca0b6880e38c7cc5c33e39dc2fb08e2` |
| time saving | E3 | Paired decision time | NOT EXECUTED | [results_v5/protocol-v5.0.0/E3/b0-p2-user-study-readiness/report/status.json](../../../E3/b0-p2-user-study-readiness/report/status.json) `/execution_status`; SHA-256 `de94e53ae14942647411e1e9033dbc9d1ca0b6880e38c7cc5c33e39dc2fb08e2` |
| correct image | E5 functional | Gold image match; required functional probes | e5-image-validation-20260905T040730Z: P1 13/18 image-label matches; e5-image-validation-20260905T040730Z: P2 13/18 image-label matches; e5-image-validation-20260905T071910Z: P1 13/18 image-label matches; e5-image-validation-20260905T071910Z: P2 13/18 image-label matches; development only; provenance limitations apply | [results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T040730Z/derived/functional_metrics.json](../../../E5/e5-image-validation-20260905T040730Z/derived/functional_metrics.json) `/systems`; SHA-256 `ca2ac88bf9d89e94c7df8b472e284652bacd662e612607730371e7c7921dafea`; [results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T071910Z/derived/functional_metrics.json](../../../E5/e5-image-validation-20260905T071910Z/derived/functional_metrics.json) `/systems`; SHA-256 `ca2ac88bf9d89e94c7df8b472e284652bacd662e612607730371e7c7921dafea` |
| image storage reuse | E5 storage | LogicalImageBytes; UniqueLayerBytes; marginal reuse | NOT EXECUTED | [benchmarks_v5/protocol-v5-final-audit-inputs-v1.json](../../../../../benchmarks_v5/protocol-v5-final-audit-inputs-v1.json); SHA-256 `8898df1187694e6a3e185d7ba23b253c23377ef2f05aba2f69378f3c1bf70f62` |
| additional/fine-grained profiles | E4 | Dynamic CPU/memory oracle error; allocation coverage | NOT EXECUTED | [results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-plan-20260905T082000Z/plan.json](../../../E4/e4-resource-efficiency-plan-20260905T082000Z/plan.json) `/primary_trial_count`; SHA-256 `5260288122847397f2ec7d1db80f20044be2a7fa39e8dab7a885847e38d6db81` |
| resource saving | E4 | CPU/memory request cost per successful workload; reliability | NOT EXECUTED | [results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-plan-20260905T082000Z/plan.json](../../../E4/e4-resource-efficiency-plan-20260905T082000Z/plan.json) `/primary_trial_count`; SHA-256 `5260288122847397f2ec7d1db80f20044be2a7fa39e8dab7a885847e38d6db81` |
| flexible natural-language interaction | E2 | Family-level robustness across equivalent variants | NOT EXECUTED | [results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1/derived/statistical_analysis/analysis-manifest.json](../../../E1/20260825T-observed-p1-p2-development-v1/derived/statistical_analysis/analysis-manifest.json) `/status`; SHA-256 `772422aa46e8ae19ffb77cc1c0f395fca4e26919a21e78417603dd35c6c81e93` |

All non-E5 rows point to the exact source packages in the inventory below; the correct-image rows point to the checksum-and-locator references above. Planned profile flexibility is design evidence only.

## Seventeen audit checks

| ID | Requirement | Verdict | Evidence boundary |
| --- | --- | --- | --- |
| 1 | Authoritative final experiment freeze | UNVERIFIED | No authoritative final freeze exists. frozen-configuration.json is a design snapshot, not a FROZEN envelope. |
| 2 | Confirmatory dataset checksum and split manifest | UNVERIFIED | Confirmatory split and safe custodian checksum attestation are unavailable; no sealed file was opened. |
| 3 | Development/confirmatory isolation | UNVERIFIED | Repository/archive isolation scan completed. External custody and semantic independence cannot be proven without custodian evidence. |
| 4 | Frozen P1/P2/P3 implementation identities | UNVERIFIED | Recommender bytes checked against audit-start inventory. No final authority exists to certify confirmatory revisions; audit revision is separate from collection revision. |
| 5 | Catalog, corpus, index, prompt and configuration provenance | FAIL | Recorded metadata compared with the design snapshot without rebuilding indexes or invoking recommenders. Snapshot agreement alone cannot certify confirmation. |
| 6 | Raw evidence preservation and package integrity | FAIL | Original seals and reviewed input bytes checked; failed packages remain preserved. |
| 7 | Raw-to-derived regeneration | UNVERIFIED | Available current-schema raw-to-derived outputs regenerated; unavailable and legacy analyses remain explicitly bounded. |
| 8 | Derived-to-figures/tables regeneration | FAIL | Current derived tables/figures regenerated; differences from preserved historical artifacts are retained. |
| 9 | Historical Protocol-v4 preservation | PASS | Protocol-v4 portable checksums and reproduced headline values; external deep sidecars remain a separate boundary. |
| 10 | Human-study direct-identifier exclusion | PASS | Direct-identifier checks applied to available human-study files. No participant sessions were observed; public aggregate reports exclude pseudonyms. |
| 11 | Kubernetes environment identity | NOT_APPLICABLE | No observed Kubernetes trials exist; readiness identities are not hardware measurements. |
| 12 | Image-storage immutable digests and platforms | NOT_APPLICABLE | No storage measurements exist. Functional-probe host metadata does not establish an image platform or storage reuse. |
| 13 | Observed execution versus synthetic fixtures | PASS | Available records checked for missing observations and synthetic/mock origins; v1.0 probes use the existing legacy error-category adapter. This is artifact consistency, not independent attestation of collection. |
| 14 | Independent statistical units | PASS | No available v5 inferential p-value was found using repetitions as semantic samples. Family/participant contracts are also checked by the existing claim-registry validator. |
| 15 | No B0 ranking metrics | PASS | Result-bearing JSON, JSONL and CSV artifacts checked for B0 ranking metrics. |
| 16 | P3 development gate and primary-system boundary | PASS | P2 remains primary; recorded P3 development decision is not_retained. No v5 confirmatory P3 conclusion is authorized. |
| 17 | Missing experiments and placeholder values | PASS | Unavailable experiments remain NOT_EXECUTED with null estimates; planned counts and fixture image identifiers are design only. |

Detailed per-file errors, source hashes, privacy results and provenance differences are in the generated validation/audit JSON. An INCOMPLETE or FAIL audit does not authorize empirical claims.

## Complete package inventory

| Package | Kind | Recorded status | Stage | Validation | Failure / limitation |
| --- | --- | --- | --- | --- | --- |
| 20260825T-observed-p1-p2-development-v1 | offline | OBSERVED | development | PASS | — |
| b0-p2-user-study-readiness | user_study | NOT_EXECUTED | development | FAIL | ORIGINAL_CHECKSUM_MISMATCH; output checksum mismatch: report/tables/participant-flow.csv |
| e4-resource-efficiency-dry-run-20260904T093503Z | resource_efficiency | NOT_EXECUTED | development | FAIL | resource-efficiency design-size or execution-order invariant differs |
| e4-resource-efficiency-dry-run-20260904T094050Z | resource_efficiency | NOT_EXECUTED | development | FAIL | resource-efficiency design-size or execution-order invariant differs |
| e4-resource-efficiency-dry-run-20260904T094316Z | resource_efficiency | NOT_EXECUTED | development | FAIL | resource-efficiency design-size or execution-order invariant differs |
| e4-resource-efficiency-dry-run-20260905T013330Z | resource_efficiency | NOT_EXECUTED | development | PASS | — |
| e4-resource-efficiency-dry-run-20260905T013619Z | resource_efficiency | NOT_EXECUTED | development | PASS | — |
| e4-resource-efficiency-observed-run-20260905T081825Z | resource_efficiency | NOT_EXECUTED | development | PASS | — |
| e4-resource-efficiency-plan-20260905T082000Z | resource_plan | PLANNED | development | PASS | — |
| e4-resource-envelope-dry-run-20260828 | resource_envelope | DRY_RUN | development | FAIL | unsupported resource run manifest |
| e4-resource-envelope-dry-run-20260828T065331Z | resource_envelope | DRY_RUN | development | FAIL | unsupported resource run manifest |
| e4-resource-envelope-dry-run-20260904T075658Z | resource_envelope | DRY_RUN | development | FAIL | resource run manifest has an incompatible trial observation schema |
| e4-resource-envelope-dry-run-20260904T081412Z | resource_envelope | DRY_RUN | development | FAIL | resource run manifest has an incompatible trial observation schema |
| e4-resource-envelope-dry-run-20260904T081601Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T081753Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T081907Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T082658Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T082806Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T083119Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T083327Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-observed-run-20260905T081833Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-readiness-dry-run-20260828T074359Z | resource_envelope | DRY_RUN | development | FAIL | unsupported resource run manifest |
| e5-image-validation-20260905T015905Z | image_functional | DRY_RUN | development | PASS | — |
| e5-image-validation-20260905T020014Z | image_functional | OBSERVED | development | FAIL | Invalid manifest in results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T020014Z: OBSERVED manifest requires retrieval_configuration |
| e5-image-validation-20260905T021913Z | image_functional | INCOMPLETE | development | PASS | — |
| e5-image-validation-20260905T024426Z | image_functional | OBSERVED | development | PASS | — |
| e5-image-validation-20260905T024633Z | image_functional | OBSERVED | development | PASS | — |
| e5-image-validation-20260905T032437Z | image_functional | OBSERVED | development | PASS | — |
| e5-image-validation-20260905T033805Z | image_functional | OBSERVED | development | PASS | — |
| e5-image-validation-20260905T033832Z | image_functional | OBSERVED | development | PASS | — |
| e5-image-validation-20260905T040730Z | image_functional | OBSERVED | development | PASS | — |
| e5-image-validation-20260905T071910Z | image_functional | OBSERVED | development | PASS | — |
| research-analysis-20260905T-final | research_analysis | INCOMPLETE | analysis | PASS | — |
| research-analysis-20260905T-implementation | research_analysis | INCOMPLETE | analysis | PASS | — |

Package names above are unambiguous entries in the reviewed input inventory; every constituent file has a SHA-256. No timestamp ordering selected the reported runs: both currently valid v1.3 functional packages are shown, and every legacy/invalid package remains listed.

## Threats to validity and evidence boundaries

Construct validity: image gold agreement, catalog capability descriptions, functional probes, user satisfaction and workload success measure different things. Undefined probes and label/operational discrepancies are retained.

Internal validity: no final freeze/custody record establishes confirmatory isolation or frozen execution revisions. Several E5 extractor fields disagree with the recommendation source. Integrity failures cannot be repaired by accepting a new inventory checksum.

External validity: ten visible development families, a small administrator catalog, developer-machine container probes and repeated use of the same image digests do not establish general performance. There is no participant population or measured eligible Kubernetes environment to generalize from.

Statistical validity: cases/variants/repeats are not independent families; probes are reused. No new p-values, intervals, effect sizes or causal improvements are claimed. Failure to execute a hypothesis test is neither support nor contradiction.

Custody/privacy: repository/archive scanning detects visible contamination patterns, not undisclosed external access or semantic overlap. Private confirmatory data was not opened. Human-study files are empty of participant observations. Future real human/cluster/storage collection requires a separately authorized, preregistered execution package.

Historical Protocol-v4 evidence remains historical/formative. Its portable checksum/headline reproduction passes independently of the v5 verdict; external deep-archive sidecars are not required for the portable workflow. Raw observations, derived metrics and report interpretation remain separate.

## Reproducibility

Use the pinned `requirements-dev.txt` / `requirements-analysis.txt` environment (CPython 3.12–3.14; source observation provenance specifies its actual Python). Run from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make v5-audit
```

For inspectable separate stages, reuse one ID:

```bash
export V5_RUN_ID=review-20260907-01
make v5-validate
make v5-analyze
make v5-figures
make v5-audit
```

Each command creates only missing stages under `results_v5/protocol-v5.0.0/final-audit/<run-id>/`; completed stages are checksum-verified before reuse. A changed input lock or implementation requires a new ID. `make v5-audit` runs every stage and writes the report before returning nonzero for integrity failures. On this snapshot nonzero is expected; do not suppress it as a success. Valid NOT_EXECUTED evidence does not itself cause a nonzero exit.

No reproduction command runs recommenders, LLM providers, container probes, registry pulls or Kubernetes jobs. All raw inputs are the privacy-reviewed allowlisted files. Legacy absolute references resolve only through checksum-bound mappings; they are never edited. Missing external evidence remains unavailable.

The run contains validation findings, regenerated derived artifacts, JSON/CSV tables, deterministic SVGs, exact reproduction comparisons, manifests and SHA256SUMS. Stage manifests bind input inventory, code hashes and runtime. Regeneration ignores only `created_at_utc` and `git_revision` when comparing status-manifest semantics; all empirical values and other fields must match. Figures use deterministic SVG metadata and fresh output directories.

Read-only historical validation and focused tests:

```bash
.venv/bin/python scripts/validate-portable-evidence.py
.venv/bin/python -m evaluation_v5.isolation_audit
.venv/bin/python -m pytest -q tests/test_protocol_v5_final_audit.py
```
