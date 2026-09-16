# Protocol-v5 Final Reproducibility and Evidence Report

**Audit verdict: FAIL. Confirmatory evidence: EXECUTED_INCOMPLETE. Primary proposed method: P2.**

This report is rendered from the checksum-bound input inventory and the explicitly selected evaluated-claim package. It does not rerun claim evaluation or infer a result from a filename, timestamp, or missing value.

Input inventory: [benchmarks_v5/protocol-v5-final-audit-inputs-v4.json](../../benchmarks_v5/protocol-v5-final-audit-inputs-v4.json); SHA-256 `8fdeaa5f4880e4b3f58d05b832c5359d6fe991b324b0fc7faabb74d6c3db25da`

Archived audit outputs: [all findings](../../results_v5/protocol-v5.0.0/final-audit/final-audit-20260916T070444Z-reporting-repair-v1/report/audit.json), [run provenance](../../results_v5/protocol-v5.0.0/final-audit/final-audit-20260916T070444Z-reporting-repair-v1/run.json), [regeneration comparisons](../../results_v5/protocol-v5.0.0/final-audit/final-audit-20260916T070444Z-reporting-repair-v1/analysis/regeneration.json).

The reviewed inventory was captured at `2026-09-16T00:00:00Z` from source revision `75611764c2d5d18960d3f08256fb9134a37b2810`. Historical bytes remain unchanged; current audit runtime provenance is recorded separately in run.json and the sealed stage manifests.

Authenticated claim-analysis package: [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/manifest.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/manifest.json); SHA-256 `b614f527da9d12c8f9d06a102eb933d17d42b64b7c72749083c15deacb681669`
Evidence selection: [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evidence-selection.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evidence-selection.json); SHA-256 `dc36cd5fadd936a81641d4eabd251b27fdb722281ffed0a85103ee6227641d12`
Evaluated registry: [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json); SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

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
| H7 | RQ5 | Shared image layers require less cumulative storage than a naive logical sum as the frozen catalog grows. | SUPPORTED | {"all_prefixes_nonexpanding":true,"catalog_prefix_count":4,"expansion_growth_difference":-3181589642,"expansion_naive_bytes":11494906506,"final_savings_bytes":3181589930,"prefix_order_valid":true,"strictly_slower_catalog_expansion":true} | — |
| H8 | RQ6 | Retained P3 improves JointAccept@1 over P2 while remaining within a separately frozen practical-overhead budget. | NOT_EXECUTED | N/A | NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; P3_NOT_RETAINED_OR_NOT_PRESENT; REQUIRED_METRIC_OR_TEST_UNAVAILABLE |

Authenticated decision counts: SUPPORTED=1, NOT_SUPPORTED=0, NOT_EXECUTED=8. A NOT_EXECUTED decision is neither a zero effect nor evidence against the hypothesis.

## Systems

| System | Definition |
| --- | --- |
| B0 | Ordinary/manual JupyterHub selection; no recommendation ranking. |
| P1 | Frozen existing rule-based recommender. |
| P2 | Structured Intent + hybrid retrieval + deterministic constraints/ranking; main proposed method. |
| P3 | P2 plus grounded LLM reranking; authenticated state: NOT_RETAINED_OR_NOT_PRESENT. |

B0 has no MRR, nDCG or Hit@K outcome. Artifact names containing 'observed-run' do not determine execution status.

## Candidate evidence dispositions

| Candidate | Experiment | Source commit | Disposition | Integrity | Reason |
| --- | --- | --- | --- | --- | --- |
| E1_CONFIRMATORY | E1 | 0f73c0a2da34916df1ef6134cc898ce01512acff | NOT_EXECUTED | PASS | The fetched ref is the frozen execution commit and contains no E1 observed evidence package. |
| E2_CONFIRMATORY | E2 | 0f73c0a2da34916df1ef6134cc898ce01512acff | NOT_EXECUTED | PASS | The fetched ref is the frozen execution commit and contains no E2 observed evidence package. |
| E3_FINAL_ANALYSIS | E3 | N/A | NOT_EXECUTED | PASS | The final E3 analysis ref and package are absent; no custody branch was opened. |
| E5_FUNCTIONAL_DEVELOPMENT | E5_FUNCTIONAL | 6df0bcf92559ba43124f11bef57898ccd2c8dcdb | ACCEPTED_OBSERVED_NON_CONFIRMATORY | PASS | Checksum-valid development-split observation; descriptive only and unable to decide global H7F. |
| E5_STORAGE_OLD | E5_STORAGE | f2a3720529cde0c04a59eaa2d97bf0864def8dd0 | SUPERSEDED | PASS | Checksum/provenance-valid compatible rerun selected; this older package records the freeze-artifact commit and is never global confirmatory evidence. |
| E5_STORAGE_RERUN | E5_STORAGE | 6ae13486b2979f56e454b63eaa7eacddcbe49927 | ACCEPTED_CONFIRMATORY | PASS | Checksum-valid real-registry observation bound to the global frozen execution SHA; 8/16-image scales remain NOT_EXECUTED. |
| E4_GLOBAL_FINAL | E4 | ddbf44cc6cb27435b3852d8a99cee489f4b9b43c | NOT_EXECUTED | PASS | The ref contains readiness/source-test changes but no global E4 observed evidence package; active changes were not imported. |
| E4_ORBSTACK_ORACLE | E4_ORACLE | 687a785544eb99ccda0f77fd24e98ec99c8d473f | INCOMPATIBLE_FREEZE | PASS | Bound to the separate OrbStack frozen execution SHA, not the authoritative global Protocol-v5 execution SHA. |
| E4_ORBSTACK_EFFICIENCY | E4 | 3f332254db477105ce669898812f2c191609239a | INCOMPATIBLE_FREEZE | PASS | Real bounded observations exist, but the lineage uses the separate OrbStack freeze and the analysis remains OBSERVED_INCOMPLETE for global claims. |
| E4_PROVENANCE_AUDIT | E4_PROVENANCE | b4566d7996e41b76147bc39e38534b7804bda619 | INCOMPATIBLE_FREEZE | PASS | The provenance repair is reproducible but remains bound to the separate OrbStack freeze and cannot certify a global claim. |

The older E5 storage package is SUPERSEDED only because the compatible rerun passed checksum and provenance validation. If that rerun fails, the rule resolves the older package to INCOMPATIBLE_FREEZE; it is never selected as global confirmatory evidence.

## Experiment matrix and sample boundaries

| Experiment | Evidence requirement | Derived state | Candidates | Eligible | Reason codes |
| --- | --- | --- | --- | --- | --- |
| E1 | offline_recommendation | NOT_EXECUTED | 1 | 0 | CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE |
| E2 | natural_language_robustness | NOT_EXECUTED | 1 | 0 | CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE |
| E3 | user_study | NOT_EXECUTED | 0 | 0 | NO_ELIGIBLE_CONFIRMATORY_EVIDENCE |
| E4 | resource_efficiency | NOT_EXECUTED | 5 | 0 | CLAIMS_NOT_PERMITTED; DERIVED_ANALYSIS_PACKAGE_REQUIRED; EVIDENCE_NOT_OBSERVED_COMPLETE; EVIDENCE_VALIDATION_FAILED; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE |
| E5_FUNCTIONAL | image_functional | DEVELOPMENT_ONLY | 10 | 0 | CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; EVIDENCE_VALIDATION_FAILED; LEGACY_E5_SCHEMA; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE |
| E5_STORAGE | image_storage | OBSERVED | 2 | 1 | — |
| E6 | p2_p3 | NOT_EXECUTED | 0 | 0 | NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; P3_NOT_RETAINED_OR_NOT_PRESENT |

E1 raw execution: **36 records**, **18 cases**, **10 workload families**; P1: 18 records, P2: 18 records. These are development observations, not confirmatory accuracy samples. [results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1/raw/recommendations.jsonl](../../results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1/raw/recommendations.jsonl); SHA-256 `25868752054231fa271e540210aad0845113ba3974722376029b18184959daf8`

E4 **design only**: 16 workload families × 4 conditions × 10 repetition blocks = 640 planned trials. Zero observed hardware trials. [results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-plan-20260905T082000Z/plan.json](../../results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-plan-20260905T082000Z/plan.json) `/trials`; SHA-256 `5260288122847397f2ec7d1db80f20044be2a7fa39e8dab7a885847e38d6db81`

## Methods and statistical boundaries

Only eligible checksum/provenance-valid confirmatory evidence evaluated against the frozen predicate can produce SUPPORTED or NOT_SUPPORTED. Nonconfirmatory, historical, incompatible, incomplete, or unverified evidence may descriptively support or contradict a criterion but cannot change the frozen global decision.

The workload family is the semantic unit for offline and resource inference. Variants and repeated executions describe within-family robustness or stability, not additional independent accuracy samples. E3 uses its frozen participant/task pairing contract. B0 never produces ranking metrics.

Development, historical, synthetic, dry-run, incomplete, and invalid packages may remain visible for traceability, but they cannot be promoted to confirmatory claim evidence.

## Exact available development observations

| Run | System | Image-label matches | Functional passes / eligible cases | Cases with undefined required probe |
| --- | --- | --- | --- | --- |
| e5-image-validation-20260912T124154Z | P1 | 13/18 | 17/17 | 1 |
| e5-image-validation-20260912T124154Z | P2 | 13/18 | 18/18 | 0 |

`e5-image-validation-20260912T124154Z`: 16/17 configured probes passed, 0 failed, 1 unavailable. These probes are reused when evaluating recommendations. [results_v5/protocol-v5.0.0/E5/e5-image-validation-20260912T124154Z/derived/functional_metrics.json](../../results_v5/protocol-v5.0.0/E5/e5-image-validation-20260912T124154Z/derived/functional_metrics.json) `/probe_summary`; SHA-256 `601bd6dc1a7c91d64f9e995fe4856d814bc4f64473db83da8c270c9bceb387c3`

P1: conservative functional success 17/18 (recorded rate 0.9444); catalog-underclaim cases 0; label-fail/functional-pass cases 4. [results_v5/protocol-v5.0.0/E5/e5-image-validation-20260912T124154Z/derived/functional_metrics.json](../../results_v5/protocol-v5.0.0/E5/e5-image-validation-20260912T124154Z/derived/functional_metrics.json) `/systems/P1`; SHA-256 `601bd6dc1a7c91d64f9e995fe4856d814bc4f64473db83da8c270c9bceb387c3`

P2: conservative functional success 18/18 (recorded rate 1.0); catalog-underclaim cases 1; label-fail/functional-pass cases 4. [results_v5/protocol-v5.0.0/E5/e5-image-validation-20260912T124154Z/derived/functional_metrics.json](../../results_v5/protocol-v5.0.0/E5/e5-image-validation-20260912T124154Z/derived/functional_metrics.json) `/systems/P2`; SHA-256 `601bd6dc1a7c91d64f9e995fe4856d814bc4f64473db83da8c270c9bceb387c3`

These development observations are descriptive only. Their presence cannot change the authenticated confirmatory decisions above.

### Evidence-driven claim conclusions

**H1 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/0`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H2 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/1`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H3 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/2`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H4 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/3`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H5 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `CLAIMS_NOT_PERMITTED; DERIVED_ANALYSIS_PACKAGE_REQUIRED; EVIDENCE_NOT_OBSERVED_COMPLETE; EVIDENCE_VALIDATION_FAILED; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/4`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H6 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `CLAIMS_NOT_PERMITTED; DERIVED_ANALYSIS_PACKAGE_REQUIRED; EVIDENCE_NOT_OBSERVED_COMPLETE; EVIDENCE_VALIDATION_FAILED; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/5`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H7F — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `CLAIMS_NOT_PERMITTED; EVIDENCE_NOT_OBSERVED_COMPLETE; EVIDENCE_VALIDATION_FAILED; LEGACY_E5_SCHEMA; NON_CONFIRMATORY_EVIDENCE; NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/6`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

**H7 — SUPPORTED**. Validated metrics: `{"all_prefixes_nonexpanding":true,"catalog_prefix_count":4,"expansion_growth_difference":-3181589642,"expansion_naive_bytes":11494906506,"final_savings_bytes":3181589930,"prefix_order_valid":true,"strictly_slower_catalog_expansion":true}`. Confidence intervals: `N/A`. Counts: `{"catalog_prefix_count":4}`. Effect sizes: `N/A`. Reason codes: `none`. [results_v5/protocol-v5.0.0/E5/e5-storage-scalability-20260914T012024Z/derived/storage_metrics.json](../../results_v5/protocol-v5.0.0/E5/e5-storage-scalability-20260914T012024Z/derived/storage_metrics.json) `/prefixes`; SHA-256 `7d5fb90720ad8aae992320bd44fa008bd9a96145ec25fa71ad54dc51970f6a61`

**H8 — NOT_EXECUTED**. Validated metrics: `N/A`. Confidence intervals: `N/A`. Counts: `N/A`. Effect sizes: `N/A`. Reason codes: `NO_ELIGIBLE_CONFIRMATORY_EVIDENCE; P3_NOT_RETAINED_OR_NOT_PRESENT; REQUIRED_METRIC_OR_TEST_UNAVAILABLE`. [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/8`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad`

### Historical E3 newline compatibility

The preserved E3 participant-flow CSV and its historical manifest identity were not rewritten. A versioned package records the current regenerated table under the LF policy plus both preserved identities: [results_v5/protocol-v5.0.0/compatibility/E3/b0-p2-user-study-readiness-regeneration-v2/manifest.json](../../results_v5/protocol-v5.0.0/compatibility/E3/b0-p2-user-study-readiness-regeneration-v2/manifest.json); SHA-256 `aa5607bb7099f55fe3b0401dea09256e219864dadbc28dcea8d52eaa21aeb5bd`.

### Synthetic-origin scan

Collector-origin scan: **PASS** across 17 discovered candidates; synthetic candidates=0, promoted synthetic candidates=0, unauthenticated exposed candidates=0. The scan uses collector provenance and claim eligibility, not filenames.

### Isolation parser diagnostic

Prior failure classification: **REMAINING_IMPLEMENTATION_DEFECT**; repair status: **REPAIRED**. Artifact `tests/test_evaluation_v5_gold_dataset.py` at observed SHA-256 `3e978556fa831877c959ee1dc3824315f9933d2f8d12d993cc78019842acc918` was a `synthetic_adversarial_test_source` / `python_source`. Parser `evaluation_v5.isolation_audit._contains_embedded_confirmatory_bundle` encountered schema signature `protocol-v5-gold-family-v1.0.0` and produced `SOURCE_LITERAL_FRAGMENT_FALSE_POSITIVE`. Historical=false, immutable-preserved-evidence=false, confirmatory-eligible=false, thesis-claim-eligible=false. [benchmarks_v5/protocol-v5-isolation-diagnostic-v1.json](../../benchmarks_v5/protocol-v5-isolation-diagnostic-v1.json); SHA-256 `ed271f8dfc5ea13f01093f62d692f93e6e41000fc82ee043e1455c96875d7917`

### P3 state

Authenticated P3 state: **NOT_RETAINED_OR_NOT_PRESENT**. Historical P3 material remains formative unless the selected claim package contains a validated retained-gate confirmatory decision. [docs/evaluation/P3_INCREMENTAL_EVALUATION_V1.md](P3_INCREMENTAL_EVALUATION_V1.md); SHA-256 `3ed59ae76bd79e5e86cb08f63f86d7a49c8c5632f578b74f8ee51a1f8c4b8d9f`

## Defense-summary table

| Professor criterion | Experiment | Metric | Observed result | Evidence reference |
| --- | --- | --- | --- | --- |
| satisfaction | E3 | SEQ ease; SUS usability | NOT_EXECUTED | [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/3`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad` |
| time saving | E3 | Paired decision time and selection effectiveness | NOT_EXECUTED | [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/2`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad` |
| correct image | E5 functional | Gold image match; required functional probes | NOT_EXECUTED | [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/6`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad` |
| image storage reuse | E5 storage | LogicalImageBytes; UniqueLayerBytes; marginal reuse; 4 observed prefixes from 1 bounded run | SUPPORTED; {"all_prefixes_nonexpanding":true,"catalog_prefix_count":4,"expansion_growth_difference":-3181589642,"expansion_naive_bytes":11494906506,"final_savings_bytes":3181589930,"prefix_order_valid":true,"strictly_slower_catalog_expansion":true} | [results_v5/protocol-v5.0.0/E5/e5-storage-scalability-20260914T012024Z/derived/storage_metrics.json](../../results_v5/protocol-v5.0.0/E5/e5-storage-scalability-20260914T012024Z/derived/storage_metrics.json) `/prefixes`; SHA-256 `7d5fb90720ad8aae992320bd44fa008bd9a96145ec25fa71ad54dc51970f6a61` |
| additional/fine-grained profiles | E4 | Dynamic CPU/memory oracle error; allocation coverage; 16 independent families; 4 conditions × 10 repetitions = 640 trial rows | NOT_EXECUTED | [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/5`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad` |
| resource saving | E4 | CPU/memory request cost per successful workload; reliability; 16 independent families; 4 conditions × 10 repetitions = 640 trial rows | NOT_EXECUTED | [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/4`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad` |
| flexible natural-language interaction | E2 | Family-level robustness across equivalent variants | NOT_EXECUTED | [results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json](../../results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final/derived/evaluated-claim-registry.json) `/claims/1`; SHA-256 `d98c4453da7583a9c6c695ec13cfac940b249a318023c72bf2e8c244adee6cad` |

## Detailed criterion audit

| Criterion | Experiment | Hypothesis | Metric definition | Independent N | Family N | Conditions | Repetitions / family-condition | Total trial / observation rows | Observed scale points / prefixes | Independent runs | Estimate / CI / effect | Global decision | Evidence class | Execution status | Artifact / checksum | Source commit | Frozen SHA | Limitation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| satisfaction | E3 | H4 | SEQ ease; SUS usability | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | N/A | N/A | N/A | The final E3 analysis ref and package are absent; no custody branch was opened. |
| time saving | E3 | H3 | Paired decision time and selection effectiveness | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | N/A | N/A | N/A | The final E3 analysis ref and package are absent; no custody branch was opened. |
| correct image | E5 | H7F | Conservative functional success and operational adequacy | 18 | 18 | N/A | N/A | 18 | N/A | N/A | N/A | NOT_EXECUTED | ACCEPTED_OBSERVED_NON_CONFIRMATORY | OBSERVED | results_v5/protocol-v5.0.0/E5/e5-image-validation-20260912T124154Z; SHA-256 fd1b103bfb000cdf4583e43d357e633acd508410fcca746f143af73bd0fb27cb | 6df0bcf92559ba43124f11bef57898ccd2c8dcdb | 0f73c0a2da34916df1ef6134cc898ce01512acff | Checksum-valid development-split observation; descriptive only and unable to decide global H7F. |
| image storage reuse | E5 | H7 | Ordered-prefix unique-layer versus logical-byte growth | N/A | N/A | N/A | N/A | 4 | 4 | 1 | {"all_prefixes_nonexpanding":true,"catalog_prefix_count":4,"expansion_growth_difference":-3181589642,"expansion_naive_bytes":11494906506,"final_savings_bytes":3181589930,"prefix_order_valid":true,"strictly_slower_catalog_expansion":true} | SUPPORTED | ACCEPTED_CONFIRMATORY | OBSERVED | results_v5/protocol-v5.0.0/E5/e5-storage-scalability-20260914T012024Z; SHA-256 b2584b57ccc90448e755f61907b34a49c07c47b573de69a9b5e0d0617ca9949c | 6ae13486b2979f56e454b63eaa7eacddcbe49927 | 0f73c0a2da34916df1ef6134cc898ce01512acff | Checksum-valid real-registry observation bound to the global frozen execution SHA; 8/16-image scales remain NOT_EXECUTED. |
| additional/fine-grained profiles | E4 | H6 | Dynamic CPU/memory oracle error and allocation coverage | 16 | 16 | 4 | 10 | 640 | N/A | N/A | N/A | NOT_EXECUTED | INCOMPATIBLE_FREEZE | OBSERVED_INCOMPLETE | results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-orbstack-observed-final-v1; SHA-256 e0574c34885edcb8a5ba24127cce17b9e4f6dba317f756480f360fff5e4a1974 | 3f332254db477105ce669898812f2c191609239a | 3d9a777390ae50cac266ceee0a58c718a34d6725 | Real bounded observations exist, but the lineage uses the separate OrbStack freeze and the analysis remains OBSERVED_INCOMPLETE for global claims. |
| resource saving | E4 | H5 | CPU/memory request cost per successful workload and reliability | 16 | 16 | 4 | 10 | 640 | N/A | N/A | N/A | NOT_EXECUTED | INCOMPATIBLE_FREEZE | OBSERVED_INCOMPLETE | results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-orbstack-observed-final-v1; SHA-256 e0574c34885edcb8a5ba24127cce17b9e4f6dba317f756480f360fff5e4a1974 | 3f332254db477105ce669898812f2c191609239a | 3d9a777390ae50cac266ceee0a58c718a34d6725 | Real bounded observations exist, but the lineage uses the separate OrbStack freeze and the analysis remains OBSERVED_INCOMPLETE for global claims. |
| flexible natural-language interaction | E2 | H2 | Family-level robustness across equivalent variants | N/A | 0 | N/A | N/A | N/A | N/A | N/A | N/A | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | N/A | 0f73c0a2da34916df1ef6134cc898ce01512acff | 0f73c0a2da34916df1ef6134cc898ce01512acff | The fetched ref is the frozen execution commit and contains no E2 observed evidence package. |

## Seventeen audit checks

| ID | Requirement | Verdict | Evidence boundary |
| --- | --- | --- | --- |
| 1 | Authoritative final experiment freeze | PASS | The inventory-selected global production freeze validates; separately governed freezes remain explicitly incompatible. |
| 2 | Confirmatory dataset checksum and split manifest | UNVERIFIED | Confirmatory split and safe custodian checksum attestation are unavailable; no sealed file was opened. |
| 3 | Development/confirmatory isolation | UNVERIFIED | Repository/archive isolation scan completed. The prior source-literal parser false positive is classified and repaired; external custody remains unavailable and is not inferred from this scan. |
| 4 | Frozen P1/P2/P3 implementation identities | UNVERIFIED | Recommender bytes checked against audit-start inventory. No final authority exists to certify confirmatory revisions; audit revision is separate from collection revision. |
| 5 | Catalog, corpus, index, prompt and configuration provenance | FAIL | Recorded catalog/corpus/index/prompt/configuration provenance was compared with the design snapshot, but snapshot agreement alone does not independently certify confirmatory provenance or authority. |
| 6 | Raw evidence preservation and package integrity | FAIL | Historical failed package and integrity findings are intentionally preserved and are not rewritten to produce a green audit; original seals, reviewed input bytes, candidate package digests, grouped artifact digests, and conditional supersession were checked. |
| 7 | Raw-to-derived regeneration | UNVERIFIED | Available current-schema raw-to-derived outputs regenerated; unavailable and legacy analyses remain explicitly bounded. |
| 8 | Derived-to-figures/tables regeneration | PASS | Current derived tables/figures regenerated; differences from preserved historical artifacts are retained. |
| 9 | Historical Protocol-v4 preservation | PASS | Protocol-v4 portable checksums and reproduced headline values; external deep sidecars remain a separate boundary. |
| 10 | Human-study direct-identifier exclusion | PASS | Direct-identifier checks applied to available human-study files. No participant sessions were observed; public aggregate reports exclude pseudonyms. |
| 11 | Kubernetes environment identity | PASS | Observed cluster identities and original package seals were validated where applicable; the OrbStack packages remain separately governed and globally incompatible. |
| 12 | Image-storage immutable digests and platforms | PASS | Observed storage image references, immutable digests, platforms, and source manifests were validated where present. |
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
| e5-image-validation-20260912T124154Z | image_functional | OBSERVED | development | PASS | — |
| e5-storage-scalability-20260912T124502Z | image_storage | OBSERVED | confirmatory | PASS | — |
| e5-storage-scalability-20260914T012024Z | image_storage | OBSERVED | confirmatory | PASS | — |

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
| H7 | CATALOG_AND_PLATFORM_SCOPE | LIMITATION | Exact result is limited to the four-image frozen catalog prefix and linux/amd64 registry manifests; configured 8/16-image scales were not executed. |
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

The run contains validation findings, regenerated derived artifacts, JSON/CSV tables, deterministic SVGs, exact reproduction comparisons, manifests and SHA256SUMS. Deterministic artifacts are compared byte-for-byte. Intentionally nondeterministic provenance fields—including run IDs, timestamps, checkout revision metadata, environment identity, and stage manifests containing them—are enumerated and compared separately rather than being claimed byte-identical. Empirical values and all other deterministic fields must match.

Read-only historical validation and focused tests:

```bash
.venv/bin/python scripts/validate-portable-evidence.py
.venv/bin/python -m evaluation_v5.isolation_audit
.venv/bin/python -m pytest -q tests/test_protocol_v5_final_audit.py
```
