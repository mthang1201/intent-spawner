# Protocol-v5 Final Reproducibility and Evidence Report

**Audit verdict: FAIL. Confirmatory evidence: NOT_EXECUTED. Primary proposed method: P2.**

This report is rendered from the checksum-bound input inventory and the explicitly selected evaluated-claim package. It does not rerun claim evaluation or infer a result from a filename, timestamp, or missing value.

Input inventory: [benchmarks_v5/protocol-v5-final-audit-inputs-v2.json](../../../../../benchmarks_v5/protocol-v5-final-audit-inputs-v2.json); SHA-256 `ae4c7a0fb3b0efbd595d51399ce01ca5cb8fdfafb7f3bd84cffd2cf9ed74e6ca`

Archived audit outputs: [all findings](audit.json), [run provenance](../run.json), [regeneration comparisons](../analysis/regeneration.json).

The reviewed inventory was captured at `2026-09-10T08:28:29Z` from source revision `53b614dba72a22cde3f4099b6fa7bccacadef926`. Historical bytes remain unchanged; current audit runtime provenance is recorded separately in run.json and the sealed stage manifests.

Authenticated claim-analysis package: [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/manifest.json](../../../analysis/research-analysis-20260905T-final/manifest.json); SHA-256 `b614f527da9d12c8f9d06a102eb933d17d42b64b7c72749083c15deacb681669`
Evidence selection: [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evidence-selection.json](../../../analysis/research-analysis-20260905T-final/derived/evidence-selection.json); SHA-256 `dc36cd5fadd936a81641d4eabd251b27fdb722281ffed0a85103ee6227641d12`
Evaluated registry: [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json); SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

## Research questions and hypotheses

| Question | Definition |
| --- | --- |
| RQ1 | Does P2 improve top-one recommendation quality relative to frozen P1? |
| RQ2 | Is P2 more robust than P1 to semantically equivalent natural-language surface forms? |
| RQ3 | Does P2 improve real-user selection effectiveness, speed, and perceived usability relative to B0? |
| RQ4 | Does P2 improve resource efficiency and oracle calibration under the frozen Kubernetes protocol? |
| RQ5 | Are P2 image selections functionally correct and is shared-layer storage lower than naive catalog storage? |
| RQ6 | If retained, does P3 improve recommendation quality enough to remain within its frozen practical-overhead budget? |

| Hypothesis | RQ | Predeclared statement | Decision | Validated result | Reason codes |
| --- | --- | --- | --- | --- | --- |
| H1 | RQ1 | P2 has higher JointAccept@1 than P1. | NOT_EXECUTED | N/A | CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE |
| H2 | RQ2 | P2 loses less JointAccept@1 under reviewed-equivalent surface-form changes than P1. | NOT_EXECUTED | N/A | CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE |
| H3 | RQ3 | Users achieve acceptable selections more often and faster with P2 than B0. | NOT_EXECUTED | N/A | NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE |
| H4 | RQ3 | Users report greater task ease and usability with P2 than B0. | NOT_EXECUTED | N/A | NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE |
| H5 | RQ4 | P2 Catalog reduces requested-resource cost per successful workload while preserving reliability relative to Static Large. | NOT_EXECUTED | N/A | CLAIMS_NOT_PERMITTED; DERIVED_ANALYSIS_PACKAGE_REQUIRED; EVIDENCE_NOT_OBSERVED_COMPLETE; EVIDENCE_VALIDATION_FAILED; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE |
| H6 | RQ4 | P2 Dynamic has lower CPU-request and memory-request absolute oracle error than P2 Catalog. | NOT_EXECUTED | N/A | CLAIMS_NOT_PERMITTED; DERIVED_ANALYSIS_PACKAGE_REQUIRED; EVIDENCE_NOT_OBSERVED_COMPLETE; EVIDENCE_VALIDATION_FAILED; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE |
| H7F | RQ5 | P2 recommended images satisfy every required functional capability probe at immutable digests. | NOT_EXECUTED | N/A | CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; EVIDENCE_VALIDATION_FAILED; LEGACY_E5_SCHEMA; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE |
| H7 | RQ5 | Shared image layers require less cumulative storage than a naive logical sum as the frozen catalog grows. | NOT_EXECUTED | N/A | NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE |
| H8 | RQ6 | Retained P3 improves JointAccept@1 over P2 while remaining within a separately frozen practical-overhead budget. | NOT_EXECUTED | N/A | NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; P3_NOT_RETAINED_OR_NOT_PRESENT; REQUIRED_METRIC_OR_TEST_UNAVAILABLE |

Authenticated decision counts: SUPPORTED=0, NOT_SUPPORTED=0, NOT_EXECUTED=9. A NOT_EXECUTED decision is neither a zero effect nor evidence against the hypothesis.

## Systems

| System | Definition |
| --- | --- |
| B0 | Ordinary/manual JupyterHub selection; no recommendation ranking. |
| P1 | Frozen existing rule-based recommender. |
| P2 | Structured Intent + hybrid retrieval + deterministic constraints/ranking; main proposed method. |
| P3 | P2 plus grounded LLM reranking; authenticated state: NOT_RETAINED_OR_NOT_PRESENT. |

B0 has no MRR, nDCG or Hit@K outcome. Artifact names containing 'observed-run' do not determine execution status.

## Experiment matrix and sample boundaries

| Experiment | Evidence requirement | Derived state | Candidates | Eligible | Reason codes |
| --- | --- | --- | --- | --- | --- |
| E1 | offline_recommendation | NOT_EXECUTED | 1 | 0 | CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE |
| E2 | natural_language_robustness | NOT_EXECUTED | 1 | 0 | CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE |
| E3 | user_study | NOT_EXECUTED | 0 | 0 | NO_ELIGIBLE_CONFIRMATORY_EVIDENCE |
| E4 | resource_efficiency | NOT_EXECUTED | 5 | 0 | CLAIMS_NOT_PERMITTED; DERIVED_ANALYSIS_PACKAGE_REQUIRED; EVIDENCE_NOT_OBSERVED_COMPLETE; EVIDENCE_VALIDATION_FAILED; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE |
| E5_FUNCTIONAL | image_functional | DEVELOPMENT_ONLY | 10 | 0 | CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; EVIDENCE_VALIDATION_FAILED; LEGACY_E5_SCHEMA; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE |
| E5_STORAGE | image_storage | NOT_EXECUTED | 0 | 0 | NO_ELIGIBLE_CONFIRMATORY_EVIDENCE |
| E6 | p2_p3 | NOT_EXECUTED | 0 | 0 | NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; P3_NOT_RETAINED_OR_NOT_PRESENT |

E1 raw execution: **36 records**, **18 cases**, **10 workload families**; P1: 18 records, P2: 18 records. These are development observations, not confirmatory accuracy samples. [results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1/raw/recommendations.jsonl](../../../E1/20260825T-observed-p1-p2-development-v1/raw/recommendations.jsonl); SHA-256 `25868752054231fa271e540210aad0845113ba3974722376029b18184959daf8`

E4 **design only**: 16 workload families × 4 conditions × 10 repetition blocks = 640 planned trials. Zero observed hardware trials. [results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-plan-20260905T082000Z/plan.json](../../../E4/e4-resource-efficiency-plan-20260905T082000Z/plan.json) `/trials`; SHA-256 `5260288122847397f2ec7d1db80f20044be2a7fa39e8dab7a885847e38d6db81`

## Methods and statistical boundaries

Only a validated claim package can produce SUPPORTED or NOT_SUPPORTED. Its exact metric values, confidence intervals, counts, effect sizes, tests, reason codes, and lineage are retained in the evaluated registry linked above.

The workload family is the semantic unit for offline and resource inference. Variants and repeated executions describe within-family robustness or stability, not additional independent accuracy samples. E3 uses its frozen participant/task pairing contract. B0 never produces ranking metrics.

Development, historical, synthetic, dry-run, incomplete, and invalid packages may remain visible for traceability, but they cannot be promoted to confirmatory claim evidence.

## Exact available development observations

| Run | System | Image-label matches | Functional passes / eligible cases | Cases with undefined required probe |
| --- | --- | --- | --- | --- |

Current v1.4 functional observations: **NOT EXECUTED**. Archived packages remain available with these explicit limitations:

| Archived run | Recorded status | Validation profile | Limitations |
| --- | --- | --- | --- |
| e5-image-validation-20260905T015905Z | DRY_RUN | LEGACY_SCHEMA_V1_0 | UNSEALED_RECOMMENDATION_PROVENANCE |
| e5-image-validation-20260905T021913Z | INCOMPLETE | LEGACY_SCHEMA_V1_1 | UNSEALED_RECOMMENDATION_PROVENANCE |
| e5-image-validation-20260905T024426Z | OBSERVED | LEGACY_SCHEMA_V1_1 | UNSEALED_RECOMMENDATION_PROVENANCE |
| e5-image-validation-20260905T024633Z | OBSERVED | LEGACY_SCHEMA_V1_1 | UNSEALED_RECOMMENDATION_PROVENANCE |
| e5-image-validation-20260905T032437Z | OBSERVED | LEGACY_SCHEMA_V1_2 | UNSEALED_RECOMMENDATION_PROVENANCE |
| e5-image-validation-20260905T033805Z | OBSERVED | LEGACY_SCHEMA_V1_2 | UNSEALED_RECOMMENDATION_PROVENANCE |
| e5-image-validation-20260905T033832Z | OBSERVED | LEGACY_SCHEMA_V1_2 | UNSEALED_RECOMMENDATION_PROVENANCE |
| e5-image-validation-20260905T040730Z | OBSERVED | LEGACY_SCHEMA_V1_3 | UNSEALED_RECOMMENDATION_PROVENANCE, MISSING_RECOMMENDATION_RECORD_JOIN, MISSING_SELECTED_IMAGE_PLATFORM_BINDING |
| e5-image-validation-20260905T071910Z | OBSERVED | LEGACY_SCHEMA_V1_3 | UNSEALED_RECOMMENDATION_PROVENANCE, MISSING_RECOMMENDATION_RECORD_JOIN, MISSING_SELECTED_IMAGE_PLATFORM_BINDING |


These development observations are descriptive only. Their presence cannot change the authenticated confirmatory decisions above.

### Evidence-driven claim conclusions

**H1 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/0`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H2 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/1`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H3 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/2`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H4 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/3`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H5 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `CLAIMS_NOT_PERMITTED; DERIVED_ANALYSIS_PACKAGE_REQUIRED; EVIDENCE_NOT_OBSERVED_COMPLETE; EVIDENCE_VALIDATION_FAILED; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/4`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H6 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `CLAIMS_NOT_PERMITTED; DERIVED_ANALYSIS_PACKAGE_REQUIRED; EVIDENCE_NOT_OBSERVED_COMPLETE; EVIDENCE_VALIDATION_FAILED; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/5`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H7F — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; EVIDENCE_VALIDATION_FAILED; LEGACY_E5_SCHEMA; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/6`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H7 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/7`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H8 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; P3_NOT_RETAINED_OR_NOT_PRESENT; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/8`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

### Historical E3 newline compatibility

The preserved E3 participant-flow CSV and its historical manifest identity were not rewritten. A versioned package records the current regenerated table under the LF policy plus both preserved identities: [results_v5/protocol-v5.0.0/compatibility/E3/b0-p2-user-study-readiness-regeneration-v2/manifest.json](../../../compatibility/E3/b0-p2-user-study-readiness-regeneration-v2/manifest.json); SHA-256 `aa5607bb7099f55fe3b0401dea09256e219864dadbc28dcea8d52eaa21aeb5bd`.

### Synthetic-origin scan

Collector-origin scan: **PASS** across 17 discovered candidates; synthetic candidates=0, promoted synthetic candidates=0, unauthenticated exposed candidates=0. The scan uses collector provenance and claim eligibility, not filenames.

### P3 state

Authenticated P3 state: **NOT_RETAINED_OR_NOT_PRESENT**. Historical P3 material remains formative unless the selected claim package contains a validated retained-gate confirmatory decision. [docs/evaluation/P3_INCREMENTAL_EVALUATION_V1.md](../../../../../docs/evaluation/P3_INCREMENTAL_EVALUATION_V1.md); SHA-256 `3ed59ae76bd79e5e86cb08f63f86d7a49c8c5632f578b74f8ee51a1f8c4b8d9f`

## Defense-summary table

| Professor criterion | Experiment | Metric | Observed result | Evidence reference |
| --- | --- | --- | --- | --- |
| satisfaction | E3 | SEQ ease; SUS usability | NOT_EXECUTED | [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/3`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad` |
| time saving | E3 | Paired decision time and selection effectiveness | NOT_EXECUTED | [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/2`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad` |
| correct image | E5 functional | Gold image match; required functional probes | NOT_EXECUTED | [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/6`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad` |
| image storage reuse | E5 storage | LogicalImageBytes; UniqueLayerBytes; marginal reuse | NOT_EXECUTED | [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/7`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad` |
| additional/fine-grained profiles | E4 | Dynamic CPU/memory oracle error; allocation coverage | NOT_EXECUTED | [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/5`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad` |
| resource saving | E4 | CPU/memory request cost per successful workload; reliability | NOT_EXECUTED | [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/4`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad` |
| flexible natural-language interaction | E2 | Family-level robustness across equivalent variants | NOT_EXECUTED | [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../../analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/1`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad` |

## Seventeen audit checks

| ID | Requirement | Verdict | Evidence boundary |
| --- | --- | --- | --- |
| 1 | Authoritative final experiment freeze | UNVERIFIED | No authoritative final freeze exists. frozen-configuration.json is a design snapshot, not a FROZEN envelope. |
| 2 | Confirmatory dataset checksum and split manifest | UNVERIFIED | Confirmatory split and safe custodian checksum attestation are unavailable; no sealed file was opened. |
| 3 | Development/confirmatory isolation | FAIL | an embedded Protocol-v5 confirmatory bundle could not be parsed |
| 4 | Frozen P1/P2/P3 implementation identities | UNVERIFIED | Recommender bytes checked against audit-start inventory. No final authority exists to certify confirmatory revisions; audit revision is separate from collection revision. |
| 5 | Catalog, corpus, index, prompt and configuration provenance | FAIL | Recorded metadata compared with the design snapshot without rebuilding indexes or invoking recommenders. Snapshot agreement alone cannot certify confirmation. |
| 6 | Raw evidence preservation and package integrity | FAIL | Original seals and reviewed input bytes checked; failed packages remain preserved. |
| 7 | Raw-to-derived regeneration | UNVERIFIED | Available current-schema raw-to-derived outputs regenerated; unavailable and legacy analyses remain explicitly bounded. |
| 8 | Derived-to-figures/tables regeneration | PASS | Current derived tables/figures regenerated; differences from preserved historical artifacts are retained. |
| 9 | Historical Protocol-v4 preservation | PASS | Protocol-v4 portable checksums and reproduced headline values; external deep sidecars remain a separate boundary. |
| 10 | Human-study direct-identifier exclusion | PASS | Direct-identifier checks applied to available human-study files. No participant sessions were observed; public aggregate reports exclude pseudonyms. |
| 11 | Kubernetes environment identity | NOT_APPLICABLE | No observed Kubernetes trials exist; readiness identities are not hardware measurements. |
| 12 | Image-storage immutable digests and platforms | NOT_APPLICABLE | No storage measurements exist. Functional-probe host metadata does not establish an image platform or storage reuse. |
| 13 | Observed execution versus synthetic fixtures | PASS | Raw records and every discovered claim candidate were checked for collector origin and claim eligibility; path names do not establish authenticity. |
| 14 | Independent statistical units | PASS | No available v5 inferential p-value was found using repetitions as semantic samples. Family/participant contracts are also checked by the existing claim-registry validator. |
| 15 | No B0 ranking metrics | PASS | Result-bearing JSON, JSONL and CSV artifacts checked for B0 ranking metrics. |
| 16 | P3 development gate and primary-system boundary | PASS | P3 state is derived from the authenticated evaluated-claim package: NOT_RETAINED_OR_NOT_PRESENT. |
| 17 | Missing experiments and placeholder values | PASS | Unavailable experiments remain NOT_EXECUTED with null estimates; planned counts and fixture image identifiers are design only. |

Detailed per-file errors, source hashes, privacy results and provenance differences are in the generated validation/audit JSON. An INCOMPLETE or FAIL audit does not authorize empirical claims.

## Complete package inventory

| Package | Kind | Recorded status | Stage | Validation | Failure / limitation |
| --- | --- | --- | --- | --- | --- |
| 20260825T-observed-p1-p2-development-v1 | offline | OBSERVED | development | PASS | — |
| b0-p2-user-study-readiness | user_study | NOT_EXECUTED | development | FAIL | ORIGINAL_CHECKSUM_MISMATCH; output checksum mismatch: report/tables/participant-flow.csv |
| e4-resource-efficiency-dry-run-20260904T093503Z | resource_efficiency | NOT_EXECUTED | development | PASS | — |
| e4-resource-efficiency-dry-run-20260904T094050Z | resource_efficiency | NOT_EXECUTED | development | PASS | — |
| e4-resource-efficiency-dry-run-20260904T094316Z | resource_efficiency | NOT_EXECUTED | development | PASS | — |
| e4-resource-efficiency-dry-run-20260905T013330Z | resource_efficiency | NOT_EXECUTED | development | PASS | — |
| e4-resource-efficiency-dry-run-20260905T013619Z | resource_efficiency | NOT_EXECUTED | development | PASS | — |
| e4-resource-efficiency-observed-run-20260905T081825Z | resource_efficiency | NOT_EXECUTED | development | PASS | — |
| e4-resource-efficiency-plan-20260905T082000Z | resource_plan | PLANNED | development | PASS | — |
| e4-resource-envelope-dry-run-20260828 | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260828T065331Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T075658Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T081412Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T081601Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T081753Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T081907Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T082658Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T082806Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T083119Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-dry-run-20260904T083327Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-observed-run-20260905T081833Z | resource_envelope | DRY_RUN | development | PASS | — |
| e4-resource-envelope-readiness-dry-run-20260828T074359Z | resource_envelope | DRY_RUN | development | PASS | — |
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

Package names above are entries in the reviewed input inventory; every constituent file has a SHA-256. Filesystem ordering and timestamps never select claim evidence.

## Threats to validity and evidence boundaries

Claim-specific limitations are copied from the authenticated evaluated registry. No narrative sentence can override a machine-readable status, decision predicate, or reason code.

| Claim | Limitation | Severity | Recorded statement |
| --- | --- | --- | --- |
| H1 | REQUIRED_EVIDENCE_UNAVAILABLE | blocking | The required confirmatory evidence was unavailable or unselected, so the linked claim cannot be decided. |
| H1 | NON_CONFIRMATORY_EVIDENCE_PRESENT | boundary | Development, historical, or unknown-stage packages are inventoried but excluded from confirmatory claim decisions. |
| H2 | REQUIRED_EVIDENCE_UNAVAILABLE | blocking | The required confirmatory evidence was unavailable or unselected, so the linked claim cannot be decided. |
| H2 | NON_CONFIRMATORY_EVIDENCE_PRESENT | boundary | Development, historical, or unknown-stage packages are inventoried but excluded from confirmatory claim decisions. |
| H3 | REQUIRED_EVIDENCE_UNAVAILABLE | blocking | The required confirmatory evidence was unavailable or unselected, so the linked claim cannot be decided. |
| H4 | REQUIRED_EVIDENCE_UNAVAILABLE | blocking | The required confirmatory evidence was unavailable or unselected, so the linked claim cannot be decided. |
| H5 | REQUIRED_EVIDENCE_UNAVAILABLE | blocking | The required confirmatory evidence was unavailable or unselected, so the linked claim cannot be decided. |
| H5 | NON_CONFIRMATORY_EVIDENCE_PRESENT | boundary | Development, historical, or unknown-stage packages are inventoried but excluded from confirmatory claim decisions. |
| H6 | REQUIRED_EVIDENCE_UNAVAILABLE | blocking | The required confirmatory evidence was unavailable or unselected, so the linked claim cannot be decided. |
| H6 | NON_CONFIRMATORY_EVIDENCE_PRESENT | boundary | Development, historical, or unknown-stage packages are inventoried but excluded from confirmatory claim decisions. |
| H7F | REQUIRED_EVIDENCE_UNAVAILABLE | blocking | The required confirmatory evidence was unavailable or unselected, so the linked claim cannot be decided. |
| H7F | NON_CONFIRMATORY_EVIDENCE_PRESENT | boundary | Development, historical, or unknown-stage packages are inventoried but excluded from confirmatory claim decisions. |
| H7 | REQUIRED_EVIDENCE_UNAVAILABLE | blocking | The required confirmatory evidence was unavailable or unselected, so the linked claim cannot be decided. |
| H8 | REQUIRED_EVIDENCE_UNAVAILABLE | blocking | The required confirmatory evidence was unavailable or unselected, so the linked claim cannot be decided. |


Repository/archive isolation scanning cannot replace external custody attestation. Functional image checks, storage measurements, user outcomes, resource outcomes, and recommendation quality remain distinct constructs.

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

Each command creates only missing stages under `results_v5/protocol-v5.0.0/final-audit/<run-id>/`; completed stages are checksum-verified before reuse. A changed input lock or implementation requires a new ID. `make v5-audit` writes the report before returning nonzero for integrity failures. Valid NOT_EXECUTED evidence does not itself cause a nonzero exit.

No reproduction command runs recommenders, LLM providers, container probes, registry pulls or Kubernetes jobs. All raw inputs are the privacy-reviewed allowlisted files. Legacy absolute references resolve only through checksum-bound mappings; they are never edited. Missing external evidence remains unavailable.

The run contains validation findings, regenerated derived artifacts, JSON/CSV tables, deterministic SVGs, exact reproduction comparisons, manifests and SHA256SUMS. Stage manifests bind input inventory, code hashes and runtime. Regeneration ignores only `created_at_utc` and `git_revision` when comparing status-manifest semantics; all empirical values and other fields must match. Figures use deterministic SVG metadata and fresh output directories.

Read-only historical validation and focused tests:

```bash
.venv/bin/python scripts/validate-portable-evidence.py
.venv/bin/python -m evaluation_v5.isolation_audit
.venv/bin/python -m pytest -q tests/test_protocol_v5_final_audit.py
```
