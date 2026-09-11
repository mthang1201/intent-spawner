# Protocol-v5 Adversarial Regression Traceability

Every row is bound to the adversarial JUnit command record. Test fixtures are not experiment evidence.

## P1-1 — CRITICAL — SATISFIED

- Original exploit: A development override could overwrite or downgrade an existing OBSERVED or claim-sensitive artifact.
- Regression node IDs: `tests/test_evaluation_v5.py::test_development_override_cannot_downgrade_existing_observed_manifest; tests/test_evaluation_v5.py::test_development_override_protects_claim_sensitive_target_payloads`
- Fixture/artifact: Temporary OBSERVED manifest and claim-sensitive payloads exercised through the development writer.
- Artifact types: `SYNTHETIC_TEST`
- Expected behavior: Reject overwrite before any protected byte changes.
- Actual result: `tests/test_evaluation_v5.py::test_development_override_cannot_downgrade_existing_observed_manifest=PASSED; tests/test_evaluation_v5.py::test_development_override_protects_claim_sensitive_target_payloads[protected_payload0]=PASSED; tests/test_evaluation_v5.py::test_development_override_protects_claim_sensitive_target_payloads[protected_payload1]=PASSED; tests/test_evaluation_v5.py::test_development_override_protects_claim_sensitive_target_payloads[protected_payload2]=PASSED; tests/test_evaluation_v5.py::test_development_override_protects_claim_sensitive_target_payloads[protected_payload3]=PASSED`
- Closure behavior: `FAIL_CLOSED`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P2-1 — CRITICAL — SATISFIED

- Original exploit: Caller-created split/freeze identities or fragmented source literals could authorize or confuse confirmatory isolation.
- Regression node IDs: `tests/test_evaluation_v5_isolation.py::test_arbitrary_freeze_identity_string_cannot_authorize_runner; tests/test_evaluation_v5_isolation.py::test_isolation_audit_parses_python_literals_without_joining_fixture_fragments; tests/test_evaluation_v5_isolation.py::test_isolation_audit_still_detects_complete_python_literal_bundle; tests/test_evaluation_v5_isolation.py::test_isolation_audit_detects_complete_concatenated_python_literal`
- Fixture/artifact: Forged temporary split/freeze envelopes and complete/fragmented Python literal bundle fixtures.
- Artifact types: `SYNTHETIC_TEST`
- Expected behavior: Reject arbitrary authority and detect complete embedded bundles without joining unrelated fragments.
- Actual result: `tests/test_evaluation_v5_isolation.py::test_arbitrary_freeze_identity_string_cannot_authorize_runner=PASSED; tests/test_evaluation_v5_isolation.py::test_isolation_audit_parses_python_literals_without_joining_fixture_fragments=PASSED; tests/test_evaluation_v5_isolation.py::test_isolation_audit_still_detects_complete_python_literal_bundle=PASSED; tests/test_evaluation_v5_isolation.py::test_isolation_audit_detects_complete_concatenated_python_literal=PASSED`
- Closure behavior: `FAIL_CLOSED`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P8-1 — CRITICAL — SATISFIED

- Original exploit: A forged persisted P3 retained flag could bypass the authenticated development gate computation.
- Regression node IDs: `tests/test_evaluation_v5_component_scoring.py::test_p3_gate_recomputes_decision_and_rejects_forged_retained_status`
- Fixture/artifact: Temporary forged retained decision with checksum-bound development gate inputs.
- Artifact types: `SYNTHETIC_TEST`
- Expected behavior: Recompute the gate and reject the forged retained state.
- Actual result: `tests/test_evaluation_v5_component_scoring.py::test_p3_gate_recomputes_decision_and_rejects_forged_retained_status=PASSED`
- Closure behavior: `FAIL_CLOSED`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P11-1 — CRITICAL — SATISFIED

- Original exploit: A fake resource adapter or an on-disk status edit could be relabelled OBSERVED.
- Regression node IDs: `tests/test_resource_authenticity_adversarial.py::test_forced_observed_status_on_disk_fails_closed; tests/test_resource_envelope_v5.py::test_fake_adapter_drives_search_and_manual_review_gate`
- Fixture/artifact: Synthetic adapter run plus an on-disk forced-OBSERVED tamper fixture.
- Artifact types: `SYNTHETIC_TEST`
- Expected behavior: Keep the adapter synthetic and reject the forged observation at validation.
- Actual result: `tests/test_resource_authenticity_adversarial.py::test_forced_observed_status_on_disk_fails_closed=PASSED; tests/test_resource_envelope_v5.py::test_fake_adapter_drives_search_and_manual_review_gate=PASSED`
- Closure behavior: `NON_OBSERVED_NON_CLAIMABLE`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P14-1 — CRITICAL — SATISFIED

- Original exploit: Synthetic image-storage data could be promoted through a normal-looking package to support H7.
- Regression node IDs: `tests/test_protocol_v5_research_analysis.py::test_controlled_synthetic_storage_reproduction_never_supports_h7; tests/test_protocol_v5_research_analysis.py::test_external_normal_layout_synthetic_storage_is_discovered_but_never_selected`
- Fixture/artifact: Controlled synthetic storage packages in temporary external normal-layout evidence roots, including forged eligibility flags.
- Artifact types: `SYNTHETIC_TEST`
- Expected behavior: Discovery must retain SYNTHETIC_TEST origin; selection must reject it and H7 must remain NOT_EXECUTED.
- Actual result: `tests/test_protocol_v5_research_analysis.py::test_controlled_synthetic_storage_reproduction_never_supports_h7=PASSED; tests/test_protocol_v5_research_analysis.py::test_external_normal_layout_synthetic_storage_is_discovered_but_never_selected=PASSED`
- Closure behavior: `NON_OBSERVED_NON_CLAIMABLE`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P3-1 — HIGH — SATISFIED

- Original exploit: A caller could label development gold as confirmatory.
- Regression node IDs: `tests/test_evaluation_v5_gold_dataset.py::test_forged_confirmatory_classification_over_non_confirmatory_source_fails_closed`
- Fixture/artifact: Temporary development gold source wrapped in a forged confirmatory classification.
- Artifact types: `SYNTHETIC_TEST`
- Expected behavior: Recompute source classification and reject the wrapper.
- Actual result: `tests/test_evaluation_v5_gold_dataset.py::test_forged_confirmatory_classification_over_non_confirmatory_source_fails_closed=PASSED`
- Closure behavior: `FAIL_CLOSED`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P4-1 — HIGH — SATISFIED

- Original exploit: The robustness generator could create variants for confirmatory families.
- Regression node IDs: `tests/test_evaluation_v5_robustness.py::test_generator_unconditionally_rejects_confirmatory_families`
- Fixture/artifact: Temporary confirmatory-family generation request.
- Artifact types: `SYNTHETIC_TEST`
- Expected behavior: Unconditionally refuse confirmatory generation.
- Actual result: `tests/test_evaluation_v5_robustness.py::test_generator_unconditionally_rejects_confirmatory_families=PASSED`
- Closure behavior: `FAIL_CLOSED`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P13-1 — HIGH — SATISFIED

- Original exploit: A CUDA probe could report success without the required site-packages contract.
- Regression node IDs: `tests/test_evaluation_v5_image_functional.py::test_cuda_probe_without_site_packages_is_unavailable`
- Fixture/artifact: Synthetic CUDA process response with site-packages deliberately absent.
- Artifact types: `SYNTHETIC_TEST`
- Expected behavior: Classify the probe unavailable, never successful.
- Actual result: `tests/test_evaluation_v5_image_functional.py::test_cuda_probe_without_site_packages_is_unavailable=PASSED`
- Closure behavior: `FAIL_CLOSED`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P13-2 — HIGH — SATISFIED

- Original exploit: Timeout or interrupt paths could leak the exact Docker container or Kubernetes pod and obscure lifecycle failure.
- Regression node IDs: `tests/test_evaluation_v5_image_functional.py::test_docker_timeout_and_interrupt_always_remove_exact_container; tests/test_evaluation_v5_image_functional.py::test_kubernetes_pending_timeout_and_interrupt_delete_exact_pod`
- Fixture/artifact: Fake Docker/Kubernetes clients exercising timeout and interrupt lifecycle branches.
- Artifact types: `SYNTHETIC_TEST`
- Expected behavior: Remove the exact object and preserve the failure state on every branch.
- Actual result: `tests/test_evaluation_v5_image_functional.py::test_docker_timeout_and_interrupt_always_remove_exact_container=PASSED; tests/test_evaluation_v5_image_functional.py::test_kubernetes_pending_timeout_and_interrupt_delete_exact_pod=PASSED`
- Closure behavior: `FAIL_CLOSED`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P13-3 — HIGH — SATISFIED

- Original exploit: Caller-supplied wrong or stale recommendation provenance could reach E5 execution.
- Regression node IDs: `tests/test_evaluation_v5_image_functional.py::test_e5_rejects_wrong_or_stale_source_run_before_execution`
- Fixture/artifact: Temporary wrong/stale recommendation source-run fixture.
- Artifact types: `SYNTHETIC_TEST`
- Expected behavior: Reject provenance before any image execution.
- Actual result: `tests/test_evaluation_v5_image_functional.py::test_e5_rejects_wrong_or_stale_source_run_before_execution=PASSED`
- Closure behavior: `FAIL_CLOSED`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P14-2 — HIGH — SATISFIED

- Original exploit: A v1-shaped storage record could be scored as though canonical v2 acceptable-gold were present.
- Regression node IDs: `tests/test_evaluation_v5_image_storage.py::test_catalog_scale_rejects_missing_canonical_v2_acceptable_gold`
- Fixture/artifact: Temporary v1-shaped catalog-scale storage metric record lacking canonical v2 acceptable gold.
- Artifact types: `SYNTHETIC_TEST`
- Expected behavior: Reject catalog-scale scoring rather than infer missing gold.
- Actual result: `tests/test_evaluation_v5_image_storage.py::test_catalog_scale_rejects_missing_canonical_v2_acceptable_gold=PASSED`
- Closure behavior: `FAIL_CLOSED`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P15-1 — HIGH — SATISFIED

- Original exploit: Duplicated caller provenance could override a canonical source identity or production-freeze mismatch.
- Regression node IDs: `tests/test_protocol_v5_research_analysis.py::test_caller_duplicate_source_identity_mismatch_fails_closed`
- Fixture/artifact: Verified test freeze plus deliberately mismatched canonical/caller dataset identities.
- Artifact types: `SYNTHETIC_TEST`
- Expected behavior: Reject the selected evidence and block the affected requirement.
- Actual result: `tests/test_protocol_v5_research_analysis.py::test_caller_duplicate_source_identity_mismatch_fails_closed=PASSED`
- Closure behavior: `FAIL_CLOSED`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P16-1 — HIGH — SATISFIED

- Original exploit: The final report could discard selected evidence or hardcode the current all-NOT_EXECUTED snapshot.
- Regression node IDs: `tests/test_protocol_v5_final_audit.py::test_p16_case_a_no_authenticated_real_evidence_has_no_fabricated_results; tests/test_protocol_v5_research_analysis.py::test_p16_case_b_observed_fixture_drives_status_metrics_and_report; tests/test_protocol_v5_research_analysis.py::test_p16_case_c_incomplete_evidence_never_becomes_positive; tests/test_protocol_v5_research_analysis.py::test_contradictory_claimable_evidence_writes_failed_audit_and_exits_two; tests/test_protocol_v5_research_analysis.py::test_p16_case_d_nonempty_authenticated_selection_reaches_completion`
- Fixture/artifact: Authenticated current package plus isolated empty, observed-path, incomplete, and non-empty-selection propagation fixtures.
- Artifact types: `HISTORICAL; SYNTHETIC_TEST`
- Expected behavior: Derive status, metrics, intervals, counts, reasons, prose, and completion propagation from evaluated evidence.
- Actual result: `tests/test_protocol_v5_final_audit.py::test_p16_case_a_no_authenticated_real_evidence_has_no_fabricated_results=PASSED; tests/test_protocol_v5_research_analysis.py::test_p16_case_b_observed_fixture_drives_status_metrics_and_report=PASSED; tests/test_protocol_v5_research_analysis.py::test_p16_case_c_incomplete_evidence_never_becomes_positive=PASSED; tests/test_protocol_v5_research_analysis.py::test_contradictory_claimable_evidence_writes_failed_audit_and_exits_two=PASSED; tests/test_protocol_v5_research_analysis.py::test_p16_case_d_nonempty_authenticated_selection_reaches_completion=PASSED`
- Closure behavior: `FAIL_CLOSED`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P4-2 — MEDIUM — SATISFIED

- Original exploit: The public robustness draft CLI could accept confirmatory input even if the internal generator refused it.
- Regression node IDs: `tests/test_evaluation_v5_robustness.py::test_cli_draft_refuses_confirmatory_input`
- Fixture/artifact: Temporary confirmatory input passed through the public draft CLI.
- Artifact types: `SYNTHETIC_TEST`
- Expected behavior: Exit nonzero before producing a draft.
- Actual result: `tests/test_evaluation_v5_robustness.py::test_cli_draft_refuses_confirmatory_input=PASSED`
- Closure behavior: `FAIL_CLOSED`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P11-2 — MEDIUM — SATISFIED

- Original exploit: Legacy E4 directories could either evade validation or be promoted under current semantics.
- Regression node IDs: `tests/test_resource_authenticity_adversarial.py::test_all_20_e4_directories_in_repository_validate`
- Fixture/artifact: All 20 preserved repository E4 legacy directories.
- Artifact types: `HISTORICAL`
- Expected behavior: Validate under bounded compatibility rules without OBSERVED or claim-eligible promotion.
- Actual result: `tests/test_resource_authenticity_adversarial.py::test_all_20_e4_directories_in_repository_validate=PASSED`
- Closure behavior: `NON_OBSERVED_NON_CLAIMABLE`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`

## P10-1 — LOW — SATISFIED

- Original exploit: Newline normalization could silently replace the preserved E3 participant-flow identity.
- Regression node IDs: `tests/test_protocol_v5_final_audit.py::test_e3_lf_regeneration_is_versioned_and_preserves_historical_identity`
- Fixture/artifact: Preserved E3 participant-flow.csv and its versioned LF/current-policy derivative manifest.
- Artifact types: `HISTORICAL; COMPATIBILITY_DERIVATIVE`
- Expected behavior: Preserve both identities and bind the derivative explicitly to the historical source.
- Actual result: `tests/test_protocol_v5_final_audit.py::test_e3_lf_regeneration_is_versioned_and_preserves_historical_identity=PASSED`
- Closure behavior: `NON_OBSERVED_NON_CLAIMABLE`
- Evidence record: `results_v5/protocol-v5.0.0/final-audit/completion-audit-20260911-final5/evidence/test/adversarial/record.json`
- Evidence SHA-256: `8c965faaa3c3db0b646fad593f8168b49ae22e855ac38d86472661df160898f1`
