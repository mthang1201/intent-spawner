# Protocol-v5 16-prompt Completion Audit

Verdict: **16/16 SATISFIED**.

This is a software and evidence-boundary audit, not experiment evidence. Synthetic regression fixtures cannot support a thesis claim.

| Issue | Severity | Before | After | Disposition | Regression evidence |
| --- | --- | --- | --- | --- | --- |
| P1-1 | CRITICAL | Observed overwrite exploit was reproducible in the original audit. | Observed and claim-sensitive artifacts remain immutable under development override attempts. | SATISFIED | tests/test_evaluation_v5.py::test_development_override_cannot_downgrade_existing_observed_manifest=PASSED; tests/test_evaluation_v5.py::test_development_override_protects_claim_sensitive_target_payloads[protected_payload0]=PASSED; tests/test_evaluation_v5.py::test_development_override_protects_claim_sensitive_target_payloads[protected_payload1]=PASSED; tests/test_evaluation_v5.py::test_development_override_protects_claim_sensitive_target_payloads[protected_payload2]=PASSED; tests/test_evaluation_v5.py::test_development_override_protects_claim_sensitive_target_payloads[protected_payload3]=PASSED |
| P2-1 | CRITICAL | Fake confirmatory split/freeze exploit was reproducible in the original audit. | A caller-constructed split or arbitrary freeze identity cannot authorize confirmatory execution. | SATISFIED | tests/test_evaluation_v5_isolation.py::test_arbitrary_freeze_identity_string_cannot_authorize_runner=PASSED |
| P8-1 | CRITICAL | Forged P3 gate exploit was reproducible in the original audit. | P3 retention is recomputed from authenticated development evidence; a forged retained flag fails closed. | SATISFIED | tests/test_evaluation_v5_component_scoring.py::test_p3_gate_recomputes_decision_and_rejects_forged_retained_status=PASSED |
| P11-1 | CRITICAL | Fake resource adapter OBSERVED exploit was reproducible in the original audit. | Synthetic adapters stay synthetic and on-disk OBSERVED relabeling fails validation. | SATISFIED | tests/test_resource_authenticity_adversarial.py::test_forced_observed_status_on_disk_fails_closed=PASSED; tests/test_resource_envelope_v5.py::test_fake_adapter_drives_search_and_manual_review_gate=PASSED |
| P14-1 | CRITICAL | Synthetic storage to H7 exploit was reproducible in the original audit. | Synthetic storage remains non-observed, non-claimable, and cannot support H7 after caller flag forgery. | SATISFIED | tests/test_protocol_v5_research_analysis.py::test_controlled_synthetic_storage_reproduction_never_supports_h7=PASSED |
| P3-1 | HIGH | Forbidden gold classification exploit was reproducible in the original audit. | Confirmatory classification cannot be forged over development or otherwise non-confirmatory gold provenance. | SATISFIED | tests/test_evaluation_v5_gold_dataset.py::test_forged_confirmatory_classification_over_non_confirmatory_source_fails_closed=PASSED |
| P4-1 | HIGH | Confirmatory robustness generation exploit was reproducible in the original audit. | Robustness draft generation is prohibited for confirmatory families. | SATISFIED | tests/test_evaluation_v5_robustness.py::test_generator_unconditionally_rejects_confirmatory_families=PASSED |
| P13-1 | HIGH | False CUDA success exploit was reproducible in the original audit. | CUDA availability requires the exact runtime contract; missing site packages cannot become success. | SATISFIED | tests/test_evaluation_v5_image_functional.py::test_cuda_probe_without_site_packages_is_unavailable=PASSED |
| P13-2 | HIGH | Lifecycle and cleanup exploit was reproducible in the original audit. | Timeout and interrupt paths deterministically clean the exact container or pod and preserve lifecycle failure state. | SATISFIED | tests/test_evaluation_v5_image_functional.py::test_docker_timeout_and_interrupt_always_remove_exact_container=PASSED; tests/test_evaluation_v5_image_functional.py::test_kubernetes_pending_timeout_and_interrupt_delete_exact_pod=PASSED |
| P13-3 | HIGH | Caller-supplied E5 provenance exploit was reproducible in the original audit. | E5 provenance is derived from a verified recommendation run; wrong or stale caller input fails before execution. | SATISFIED | tests/test_evaluation_v5_image_functional.py::test_e5_rejects_wrong_or_stale_source_run_before_execution=PASSED |
| P14-2 | HIGH | V1-shaped catalog scale scorer exploit was reproducible in the original audit. | Catalog-scale scoring requires canonical v2 acceptable gold and cannot infer it from a v1-shaped record. | SATISFIED | tests/test_evaluation_v5_image_storage.py::test_catalog_scale_rejects_missing_canonical_v2_acceptable_gold=PASSED |
| P15-1 | HIGH | Freeze mismatch exploit was reproducible in the original audit. | Caller-duplicated provenance cannot override canonical source identity or a production-freeze mismatch. | SATISFIED | tests/test_protocol_v5_research_analysis.py::test_caller_duplicate_source_identity_mismatch_fails_closed=PASSED |
| P16-1 | HIGH | Hardcoded final report exploit was reproducible in the original audit. | The final audit consumes authenticated selected claims and renders statuses, values, counts, P3/E3/E4/E5 states, and prose from them. | SATISFIED | tests/test_protocol_v5_final_audit.py::test_final_audit_consumes_explicit_authenticated_claim_package=PASSED; tests/test_protocol_v5_final_audit.py::test_report_renders_changed_validated_claim_state_instead_of_snapshot_prose=PASSED; tests/test_protocol_v5_final_audit.py::test_collector_origin_scan_rejects_synthetic_observed_fixture_outside_repository=PASSED |
| P4-2 | MEDIUM | Draft CLI isolation exploit was reproducible in the original audit. | The public draft CLI fails closed on confirmatory input. | SATISFIED | tests/test_evaluation_v5_robustness.py::test_cli_draft_refuses_confirmatory_input=PASSED |
| P11-2 | MEDIUM | E4 legacy compatibility exploit was reproducible in the original audit. | All bounded legacy E4 directories validate under explicit compatibility semantics without promotion to observed evidence. | SATISFIED | tests/test_resource_authenticity_adversarial.py::test_all_20_e4_directories_in_repository_validate=PASSED |
| P10-1 | LOW | E3 newline/checksum exploit was reproducible in the original audit. | Historical bytes and identity are preserved while a new LF-policy compatibility package records its own checksum. | SATISFIED | tests/test_protocol_v5_final_audit.py::test_e3_lf_regeneration_is_versioned_and_preserves_historical_identity=PASSED |

## Workflow evidence

| Command | Exit | Classification | Result |
| --- | --- | --- | --- |
| `make v5-validate` | 2 | INTENTIONALLY_UNAVAILABLE_REAL_EVIDENCE_AND_PRESERVED_INVALID_HISTORY | audit_status=FAIL; final freeze/custody are unavailable, isolation is not certifiable, and invalid historical packages remain rejected |
| `make v5-analyze` | 2 | UNAVAILABLE_REAL_EXPERIMENT_EVIDENCE | regeneration_status=PASS_WITH_UNAVAILABLE_ANALYSES; the nonzero exit propagates the validation audit failure |
| `make v5-figures` | 2 | PASSING_STAGE_WITH_PROPAGATED_AUDIT_FAILURE | figure_status=PASS; the versioned E3 LF compatibility baseline reproduces, while the nonzero exit preserves the audit failure |
| `make v5-audit` | 2 | SCIENTIFIC_FAIL_CLOSED | audit_status=FAIL; confirmatory_status=NOT_EXECUTED; no empirical claim is authorized |

## Scientific gates

- Sealed confirmatory execution safe now: **false**.
- Synthetic evidence can become OBSERVED: **false**.
- Synthetic evidence can support a thesis claim: **false**.
- Authenticated confirmatory status: **NOT_EXECUTED**.
- Remaining real-execution requirements: authoritative freeze, sealed split/custody, and isolation gates must pass; E1: authenticated real evidence (NOT_EXECUTED); E2: authenticated real evidence (NOT_EXECUTED); E3: authenticated real evidence (NOT_EXECUTED); E4: authenticated real evidence (NOT_EXECUTED); E5_FUNCTIONAL: authenticated real evidence (DEVELOPMENT_ONLY); E5_STORAGE: authenticated real evidence (NOT_EXECUTED); E6: no confirmatory execution is authorized unless a frozen authenticated development gate retains P3 (current state NOT_RETAINED_OR_NOT_PRESENT)
