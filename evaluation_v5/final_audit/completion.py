"""Build the immutable 16-issue completion audit from machine-readable runs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence

from .common import file_sha256, read_json, verify_seal, write_bytes, write_json
from .evidence import junit_details, verify_command_evidence
from .publication import verify_attestation


ISSUES = (
    {
        "id": "P1-1", "severity": "CRITICAL", "title": "Observed overwrite",
        "tests": (
            "tests/test_evaluation_v5.py::test_development_override_cannot_downgrade_existing_observed_manifest",
            "tests/test_evaluation_v5.py::test_development_override_protects_claim_sensitive_target_payloads",
        ),
        "code": ("evaluation_v5/provenance.py",),
        "closed_outcome": "Observed and claim-sensitive artifacts remain immutable under development override attempts.",
    },
    {
        "id": "P2-1", "severity": "CRITICAL", "title": "Fake confirmatory split/freeze",
        "tests": (
            "tests/test_evaluation_v5_isolation.py::test_arbitrary_freeze_identity_string_cannot_authorize_runner",
            "tests/test_evaluation_v5_isolation.py::test_isolation_audit_parses_python_literals_without_joining_fixture_fragments",
            "tests/test_evaluation_v5_isolation.py::test_isolation_audit_still_detects_complete_python_literal_bundle",
            "tests/test_evaluation_v5_isolation.py::test_isolation_audit_detects_complete_concatenated_python_literal",
        ),
        "code": ("evaluation_v5/freeze.py", "evaluation_v5/offline/runner.py"),
        "closed_outcome": "A caller-constructed split or arbitrary freeze identity cannot authorize confirmatory execution.",
    },
    {
        "id": "P8-1", "severity": "CRITICAL", "title": "Forged P3 gate",
        "tests": ("tests/test_evaluation_v5_component_scoring.py::test_p3_gate_recomputes_decision_and_rejects_forged_retained_status",),
        "code": ("evaluation_v5/p3_gate.py", "evaluation_v5/analysis/component_scoring.py"),
        "closed_outcome": "P3 retention is recomputed from authenticated development evidence; a forged retained flag fails closed.",
    },
    {
        "id": "P11-1", "severity": "CRITICAL", "title": "Fake resource adapter OBSERVED",
        "tests": (
            "tests/test_resource_authenticity_adversarial.py::test_forced_observed_status_on_disk_fails_closed",
            "tests/test_resource_envelope_v5.py::test_fake_adapter_drives_search_and_manual_review_gate",
        ),
        "code": ("evaluation_v5/resource/evidence.py", "cluster_evaluation/resource_adapter_v5.py"),
        "closed_outcome": "Synthetic adapters stay synthetic and on-disk OBSERVED relabeling fails validation.",
    },
    {
        "id": "P14-1", "severity": "CRITICAL", "title": "Synthetic storage to H7",
        "tests": (
            "tests/test_protocol_v5_research_analysis.py::test_controlled_synthetic_storage_reproduction_never_supports_h7",
            "tests/test_protocol_v5_research_analysis.py::test_external_normal_layout_synthetic_storage_is_discovered_but_never_selected",
        ),
        "code": ("evaluation_v5/analysis/research_analysis.py", "evaluation_v5/image_storage/validate_evidence.py"),
        "closed_outcome": "Synthetic storage remains non-observed, non-claimable, and cannot support H7 after caller flag forgery.",
    },
    {
        "id": "P3-1", "severity": "HIGH", "title": "Forbidden gold classification",
        "tests": ("tests/test_evaluation_v5_gold_dataset.py::test_forged_confirmatory_classification_over_non_confirmatory_source_fails_closed",),
        "code": ("evaluation_v5/gold_dataset.py",),
        "closed_outcome": "Confirmatory classification cannot be forged over development or otherwise non-confirmatory gold provenance.",
    },
    {
        "id": "P4-1", "severity": "HIGH", "title": "Confirmatory robustness generation",
        "tests": ("tests/test_evaluation_v5_robustness.py::test_generator_unconditionally_rejects_confirmatory_families",),
        "code": ("evaluation_v5/robustness/generator.py",),
        "closed_outcome": "Robustness draft generation is prohibited for confirmatory families.",
    },
    {
        "id": "P13-1", "severity": "HIGH", "title": "False CUDA success",
        "tests": ("tests/test_evaluation_v5_image_functional.py::test_cuda_probe_without_site_packages_is_unavailable",),
        "code": ("evaluation_v5/image_storage/manifest.py", "evaluation_v5/image_storage/runner.py"),
        "closed_outcome": "CUDA availability requires the exact runtime contract; missing site packages cannot become success.",
    },
    {
        "id": "P13-2", "severity": "HIGH", "title": "Lifecycle and cleanup",
        "tests": (
            "tests/test_evaluation_v5_image_functional.py::test_docker_timeout_and_interrupt_always_remove_exact_container",
            "tests/test_evaluation_v5_image_functional.py::test_kubernetes_pending_timeout_and_interrupt_delete_exact_pod",
        ),
        "code": ("evaluation_v5/image_storage/runner.py", "evaluation_v5/image_storage/validate_evidence.py"),
        "closed_outcome": "Timeout and interrupt paths deterministically clean the exact container or pod and preserve lifecycle failure state.",
    },
    {
        "id": "P13-3", "severity": "HIGH", "title": "Caller-supplied E5 provenance",
        "tests": ("tests/test_evaluation_v5_image_functional.py::test_e5_rejects_wrong_or_stale_source_run_before_execution",),
        "code": ("evaluation_v5/image_storage/functional_provenance.py",),
        "closed_outcome": "E5 provenance is derived from a verified recommendation run; wrong or stale caller input fails before execution.",
    },
    {
        "id": "P14-2", "severity": "HIGH", "title": "V1-shaped catalog scale scorer",
        "tests": ("tests/test_evaluation_v5_image_storage.py::test_catalog_scale_rejects_missing_canonical_v2_acceptable_gold",),
        "code": ("evaluation_v5/image_storage/metrics.py",),
        "closed_outcome": "Catalog-scale scoring requires canonical v2 acceptable gold and cannot infer it from a v1-shaped record.",
    },
    {
        "id": "P15-1", "severity": "HIGH", "title": "Freeze mismatch",
        "tests": ("tests/test_protocol_v5_research_analysis.py::test_caller_duplicate_source_identity_mismatch_fails_closed",),
        "code": ("evaluation_v5/analysis/research_analysis.py", "evaluation_v5/freeze.py"),
        "closed_outcome": "Caller-duplicated provenance cannot override canonical source identity or a production-freeze mismatch.",
    },
    {
        "id": "P16-1", "severity": "HIGH", "title": "Hardcoded final report",
        "tests": (
            "tests/test_protocol_v5_final_audit.py::test_p16_case_a_no_authenticated_real_evidence_has_no_fabricated_results",
            "tests/test_protocol_v5_research_analysis.py::test_p16_case_b_observed_fixture_drives_status_metrics_and_report",
            "tests/test_protocol_v5_research_analysis.py::test_p16_case_c_incomplete_evidence_never_becomes_positive",
            "tests/test_protocol_v5_research_analysis.py::test_contradictory_claimable_evidence_writes_failed_audit_and_exits_two",
            "tests/test_protocol_v5_research_analysis.py::test_p16_case_d_nonempty_authenticated_selection_reaches_completion",
        ),
        "code": ("evaluation_v5/final_audit/claims.py", "evaluation_v5/final_audit/checks.py", "evaluation_v5/final_audit/reporting.py"),
        "closed_outcome": "The final audit consumes authenticated selected claims and renders statuses, values, counts, P3/E3/E4/E5 states, and prose from them.",
    },
    {
        "id": "P4-2", "severity": "MEDIUM", "title": "Draft CLI isolation",
        "tests": ("tests/test_evaluation_v5_robustness.py::test_cli_draft_refuses_confirmatory_input",),
        "code": ("evaluation_v5/robustness/__main__.py",),
        "closed_outcome": "The public draft CLI fails closed on confirmatory input.",
    },
    {
        "id": "P11-2", "severity": "MEDIUM", "title": "E4 legacy compatibility",
        "tests": ("tests/test_resource_authenticity_adversarial.py::test_all_20_e4_directories_in_repository_validate",),
        "code": ("evaluation_v5/resource/evidence.py", "evaluation_v5/resource/efficiency_evidence.py"),
        "closed_outcome": "All bounded legacy E4 directories validate under explicit compatibility semantics without promotion to observed evidence.",
    },
    {
        "id": "P10-1", "severity": "LOW", "title": "E3 newline/checksum",
        "tests": ("tests/test_protocol_v5_final_audit.py::test_e3_lf_regeneration_is_versioned_and_preserves_historical_identity",),
        "code": ("results_v5/protocol-v5.0.0/compatibility/E3/b0-p2-user-study-readiness-regeneration-v2/manifest.json",),
        "closed_outcome": "Historical bytes and identity are preserved while a new LF-policy compatibility package records its own checksum.",
    },
)

TRACE_METADATA = {
    "P1-1": {
        "original_exploit": "A development override could overwrite or downgrade an existing OBSERVED or claim-sensitive artifact.",
        "fixture_or_artifact": "Temporary OBSERVED manifest and claim-sensitive payloads exercised through the development writer.",
        "artifact_types": ["SYNTHETIC_TEST"],
        "expected_behavior": "Reject overwrite before any protected byte changes.",
        "closure_behavior": "FAIL_CLOSED",
    },
    "P2-1": {
        "original_exploit": "Caller-created split/freeze identities or fragmented source literals could authorize or confuse confirmatory isolation.",
        "fixture_or_artifact": "Forged temporary split/freeze envelopes and complete/fragmented Python literal bundle fixtures.",
        "artifact_types": ["SYNTHETIC_TEST"],
        "expected_behavior": "Reject arbitrary authority and detect complete embedded bundles without joining unrelated fragments.",
        "closure_behavior": "FAIL_CLOSED",
    },
    "P8-1": {
        "original_exploit": "A forged persisted P3 retained flag could bypass the authenticated development gate computation.",
        "fixture_or_artifact": "Temporary forged retained decision with checksum-bound development gate inputs.",
        "artifact_types": ["SYNTHETIC_TEST"],
        "expected_behavior": "Recompute the gate and reject the forged retained state.",
        "closure_behavior": "FAIL_CLOSED",
    },
    "P11-1": {
        "original_exploit": "A fake resource adapter or an on-disk status edit could be relabelled OBSERVED.",
        "fixture_or_artifact": "Synthetic adapter run plus an on-disk forced-OBSERVED tamper fixture.",
        "artifact_types": ["SYNTHETIC_TEST"],
        "expected_behavior": "Keep the adapter synthetic and reject the forged observation at validation.",
        "closure_behavior": "NON_OBSERVED_NON_CLAIMABLE",
    },
    "P14-1": {
        "original_exploit": "Synthetic image-storage data could be promoted through a normal-looking package to support H7.",
        "fixture_or_artifact": "Controlled synthetic storage packages in temporary external normal-layout evidence roots, including forged eligibility flags.",
        "artifact_types": ["SYNTHETIC_TEST"],
        "expected_behavior": "Discovery must retain SYNTHETIC_TEST origin; selection must reject it and H7 must remain NOT_EXECUTED.",
        "closure_behavior": "NON_OBSERVED_NON_CLAIMABLE",
    },
    "P3-1": {
        "original_exploit": "A caller could label development gold as confirmatory.",
        "fixture_or_artifact": "Temporary development gold source wrapped in a forged confirmatory classification.",
        "artifact_types": ["SYNTHETIC_TEST"],
        "expected_behavior": "Recompute source classification and reject the wrapper.",
        "closure_behavior": "FAIL_CLOSED",
    },
    "P4-1": {
        "original_exploit": "The robustness generator could create variants for confirmatory families.",
        "fixture_or_artifact": "Temporary confirmatory-family generation request.",
        "artifact_types": ["SYNTHETIC_TEST"],
        "expected_behavior": "Unconditionally refuse confirmatory generation.",
        "closure_behavior": "FAIL_CLOSED",
    },
    "P13-1": {
        "original_exploit": "A CUDA probe could report success without the required site-packages contract.",
        "fixture_or_artifact": "Synthetic CUDA process response with site-packages deliberately absent.",
        "artifact_types": ["SYNTHETIC_TEST"],
        "expected_behavior": "Classify the probe unavailable, never successful.",
        "closure_behavior": "FAIL_CLOSED",
    },
    "P13-2": {
        "original_exploit": "Timeout or interrupt paths could leak the exact Docker container or Kubernetes pod and obscure lifecycle failure.",
        "fixture_or_artifact": "Fake Docker/Kubernetes clients exercising timeout and interrupt lifecycle branches.",
        "artifact_types": ["SYNTHETIC_TEST"],
        "expected_behavior": "Remove the exact object and preserve the failure state on every branch.",
        "closure_behavior": "FAIL_CLOSED",
    },
    "P13-3": {
        "original_exploit": "Caller-supplied wrong or stale recommendation provenance could reach E5 execution.",
        "fixture_or_artifact": "Temporary wrong/stale recommendation source-run fixture.",
        "artifact_types": ["SYNTHETIC_TEST"],
        "expected_behavior": "Reject provenance before any image execution.",
        "closure_behavior": "FAIL_CLOSED",
    },
    "P14-2": {
        "original_exploit": "A v1-shaped storage record could be scored as though canonical v2 acceptable-gold were present.",
        "fixture_or_artifact": "Temporary v1-shaped catalog-scale storage metric record lacking canonical v2 acceptable gold.",
        "artifact_types": ["SYNTHETIC_TEST"],
        "expected_behavior": "Reject catalog-scale scoring rather than infer missing gold.",
        "closure_behavior": "FAIL_CLOSED",
    },
    "P15-1": {
        "original_exploit": "Duplicated caller provenance could override a canonical source identity or production-freeze mismatch.",
        "fixture_or_artifact": "Verified test freeze plus deliberately mismatched canonical/caller dataset identities.",
        "artifact_types": ["SYNTHETIC_TEST"],
        "expected_behavior": "Reject the selected evidence and block the affected requirement.",
        "closure_behavior": "FAIL_CLOSED",
    },
    "P16-1": {
        "original_exploit": "The final report could discard selected evidence or hardcode the current all-NOT_EXECUTED snapshot.",
        "fixture_or_artifact": "Authenticated current package plus isolated empty, observed-path, incomplete, and non-empty-selection propagation fixtures.",
        "artifact_types": ["HISTORICAL", "SYNTHETIC_TEST"],
        "expected_behavior": "Derive status, metrics, intervals, counts, reasons, prose, and completion propagation from evaluated evidence.",
        "closure_behavior": "FAIL_CLOSED",
    },
    "P4-2": {
        "original_exploit": "The public robustness draft CLI could accept confirmatory input even if the internal generator refused it.",
        "fixture_or_artifact": "Temporary confirmatory input passed through the public draft CLI.",
        "artifact_types": ["SYNTHETIC_TEST"],
        "expected_behavior": "Exit nonzero before producing a draft.",
        "closure_behavior": "FAIL_CLOSED",
    },
    "P11-2": {
        "original_exploit": "Legacy E4 directories could either evade validation or be promoted under current semantics.",
        "fixture_or_artifact": "All 20 preserved repository E4 legacy directories.",
        "artifact_types": ["HISTORICAL"],
        "expected_behavior": "Validate under bounded compatibility rules without OBSERVED or claim-eligible promotion.",
        "closure_behavior": "NON_OBSERVED_NON_CLAIMABLE",
    },
    "P10-1": {
        "original_exploit": "Newline normalization could silently replace the preserved E3 participant-flow identity.",
        "fixture_or_artifact": "Preserved E3 participant-flow.csv and its versioned LF/current-policy derivative manifest.",
        "artifact_types": ["HISTORICAL", "COMPATIBILITY_DERIVATIVE"],
        "expected_behavior": "Preserve both identities and bind the derivative explicitly to the historical source.",
        "closure_behavior": "NON_OBSERVED_NON_CLAIMABLE",
    },
}

REQUIRED_TEST_EVIDENCE = {"adversarial", "focused", "full"}
REQUIRED_VALIDATOR_EVIDENCE = {
    "protocol_v4",
    "historical_cluster",
    "raw_integrity",
    "isolation",
    "resource_oracle",
    "resource_efficiency",
    "e5",
    "claim_registry",
    "publication_delta",
}
REQUIRED_WORKFLOW_EVIDENCE = {
    "v5_validate": "v5-validate",
    "v5_analyze": "v5-analyze",
    "v5_figures": "v5-figures",
    "v5_audit": "v5-audit",
}
REQUIRED_REPRODUCIBILITY_EVIDENCE = {"canonical_reproducibility"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_evidence_records(
    paths: Sequence[Path],
    *,
    kind: str,
    required_ids: set[str],
    expected_revision: str,
) -> list[dict[str, Any]]:
    records = []
    for path in paths:
        directory = path.resolve() if path.is_dir() else path.resolve().parent
        record = verify_command_evidence(directory)
        if record.get("kind") != kind:
            raise ValueError(f"{record.get('evidence_id')}: expected {kind} evidence")
        if record.get("git_revision") != expected_revision:
            raise ValueError(
                f"{record.get('evidence_id')}: command evidence revision differs from final implementation"
            )
        if record.get("git_dirty_before") is not False:
            raise ValueError(f"{record.get('evidence_id')}: command ran from a dirty revision")
        records.append({**record, "package": directory})
    ids = [str(record.get("evidence_id")) for record in records]
    if set(ids) != required_ids or len(ids) != len(set(ids)):
        raise ValueError(f"{kind} evidence IDs are incomplete or duplicated")
    return sorted(records, key=lambda record: record["evidence_id"])


def _test_cases(record: dict[str, Any]) -> dict[str, str]:
    junit = record.get("junit") or {}
    relative = junit.get("path")
    if not isinstance(relative, str) or junit.get("missing"):
        raise ValueError(f"{record['evidence_id']}: JUnit evidence is missing")
    path = record["package"] / relative
    if file_sha256(path) != junit.get("sha256"):
        raise ValueError(f"{record['evidence_id']}: JUnit checksum differs from its record")
    details = junit_details(path)
    counts = {key: value for key, value in details.items() if key != "cases"}
    if counts != junit.get("counts"):
        raise ValueError(f"{record['evidence_id']}: JUnit counts do not recompute")
    return details["cases"]


def _matches(cases: dict[str, str], expected: str) -> list[tuple[str, str]]:
    return sorted((nodeid, status) for nodeid, status in cases.items() if nodeid.startswith(expected))


def _git_revision(root: Path) -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _verify_final_audit_run(
    root: Path,
    run_root: Path,
    *,
    expected_revision: str,
    workflows: list[dict[str, Any]],
) -> dict[str, Any]:
    run_root = run_root.resolve()
    run = read_json(run_root / "run.json")
    if run.get("audit_git_revision") != expected_revision or run.get("audit_git_dirty") is not False:
        raise ValueError("final audit run was not generated from the clean final implementation revision")
    stages = []
    for stage in ("validation", "analysis", "figures", "report"):
        manifest = verify_seal(run_root / stage)
        if (
            manifest.get("audit_git_revision") != expected_revision
            or manifest.get("run_id") != run.get("run_id")
            or manifest.get("implementation_sha256") != run.get("implementation_sha256")
        ):
            raise ValueError(f"{stage}: final audit stage provenance differs from run.json")
        stages.append(
            {
                "stage": stage,
                "manifest_path": _display_path(run_root / stage / "manifest.json", root),
                "manifest_sha256": file_sha256(run_root / stage / "manifest.json"),
                "git_revision": manifest["audit_git_revision"],
            }
        )
    expected_argument = "V5_RUN_ID=" + str(run["run_id"])
    for record in workflows:
        target = REQUIRED_WORKFLOW_EVIDENCE[record["evidence_id"]]
        if record.get("argv", [])[:2] != ["make", target] or expected_argument not in record.get("argv", []):
            raise ValueError(f"{record['evidence_id']}: workflow command is not bound to the final audit run")
    final_audit_path = run_root / "report/audit.json"
    return {
        "run_id": run["run_id"],
        "git_revision": expected_revision,
        "audit_git_dirty": run["audit_git_dirty"],
        "input_inventory_sha256": run["input_inventory_sha256"],
        "implementation_sha256": run["implementation_sha256"],
        "run_manifest": {
            "path": _display_path(run_root / "run.json", root),
            "sha256": file_sha256(run_root / "run.json"),
        },
        "stages": stages,
        "final_audit": {
            "path": _display_path(final_audit_path, root),
            "sha256": file_sha256(final_audit_path),
        },
        "payload": read_json(final_audit_path),
    }


def _verify_canonical_reproducibility(
    root: Path, record: dict[str, Any], *, expected_revision: str
) -> dict[str, Any]:
    argv = record.get("argv") or []
    if argv[:2] != ["make", "v5-audit"]:
        raise ValueError("canonical reproducibility evidence did not execute make v5-audit")
    run_arguments = [arg for arg in argv[2:] if str(arg).startswith("V5_RUN_ID=")]
    if len(run_arguments) != 1:
        raise ValueError("canonical reproducibility evidence lacks one explicit run ID")
    run_id = str(run_arguments[0]).split("=", 1)[1]
    run_root = root / "results_v5/protocol-v5.0.0/final-audit" / run_id
    run = read_json(run_root / "run.json")
    if run.get("audit_git_revision") != expected_revision or run.get("audit_git_dirty") is not False:
        raise ValueError("canonical reproducibility run is not bound to the tested implementation")
    stages = []
    for stage in ("validation", "analysis", "figures", "report"):
        manifest = verify_seal(run_root / stage)
        if manifest.get("audit_git_revision") != expected_revision or manifest.get("run_id") != run_id:
            raise ValueError("canonical reproducibility stage provenance differs: " + stage)
        stages.append(
            {
                "stage": stage,
                "manifest_path": _display_path(run_root / stage / "manifest.json", root),
                "manifest_sha256": file_sha256(run_root / stage / "manifest.json"),
            }
        )
    makefile = root / "Makefile"
    documentation = root / "docs/evaluation/PROTOCOL_V5_FINAL_REPORT.md"
    make_text = makefile.read_text(encoding="utf-8")
    docs_text = documentation.read_text(encoding="utf-8")
    recipe = "v5-audit:\n\t$(V5_PYTHON) -m evaluation_v5.final_audit audit $(V5_AUDIT_ARGS)"
    if recipe not in make_text:
        raise ValueError("Makefile does not define v5-audit as the final audit orchestrator")
    if "make v5-audit" not in docs_text or "`make v5-audit` runs every stage" not in docs_text:
        raise ValueError("repository documentation does not identify make v5-audit as canonical")
    return {
        "status": "PASS",
        "canonical_command": "make v5-audit",
        "argv": argv,
        "run_id": run_id,
        "git_revision": expected_revision,
        "exit_code": record.get("exit_code"),
        "classifications": record.get("classifications") or [],
        "nonzero_reasons": record.get("nonzero_reasons") or [],
        "environment": record.get("environment") or {},
        "stdout": record.get("stdout"),
        "stderr": record.get("stderr"),
        "configuration_proof": {
            "makefile": {"path": "Makefile", "sha256": file_sha256(makefile)},
            "documentation": {
                "path": "docs/evaluation/PROTOCOL_V5_FINAL_REPORT.md",
                "sha256": file_sha256(documentation),
            },
            "target": "v5-audit",
            "recipe": "$(V5_PYTHON) -m evaluation_v5.final_audit audit $(V5_AUDIT_ARGS)",
            "orchestrated_stages": ["validation", "analysis", "figures", "report"],
        },
        "stages": stages,
    }


def claim_flow_attestation(final_audit: Mapping[str, Any]) -> dict[str, Any]:
    """Prove selection-derived evaluated claims survive into completion input."""

    claims = final_audit.get("claims") or []
    evaluated = final_audit.get("evaluated_claims") or []
    summary_by_id = {str(row.get("id")): row for row in claims}
    evaluated_by_id = {str(row.get("claim_id")): row for row in evaluated}
    if set(summary_by_id) != set(evaluated_by_id):
        raise ValueError("final-audit claim summaries differ from evaluated registry IDs")
    propagation = []
    selected_requirements = set()
    for claim_id, raw in sorted(evaluated_by_id.items()):
        summary = summary_by_id[claim_id]
        metrics = (raw.get("result") or {}).get("normalized_metrics") or {}
        if (
            summary.get("claim_status") != raw.get("claim_status")
            or summary.get("normalized_metrics") != metrics
            or summary.get("reason_codes") != list(raw.get("reason_codes") or [])
        ):
            raise ValueError(claim_id + ": final-audit projection discarded evaluated claim state")
        for evidence in raw.get("evidence") or []:
            selected_requirements.add(str(evidence.get("requirement_id")))
        propagation.append(
            {
                "claim_id": claim_id,
                "status": raw.get("claim_status"),
                "metrics_sha256": hashlib.sha256(
                    json.dumps(metrics, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                ).hexdigest(),
                "reason_codes": list(raw.get("reason_codes") or []),
                "evidence_requirement_count": len(raw.get("evidence") or []),
            }
        )
    return {
        "schema_version": "protocol-v5-claim-flow-attestation-v1.0.0",
        "status": "PASS",
        "authenticated_selection_source": (final_audit.get("claim_evidence_sources") or {}).get("selection"),
        "evaluated_claim_source": (final_audit.get("claim_evidence_sources") or {}).get("evaluated_claims"),
        "selected_requirement_count": len(selected_requirements),
        "genuinely_empty_authenticated_selection": not selected_requirements,
        "selection_was_silently_discarded": False,
        "claim_propagation": propagation,
    }


def _e3_preservation(root: Path, final_audit: Mapping[str, Any]) -> dict[str, Any]:
    historical = Path(
        "results_v5/protocol-v5.0.0/E3/b0-p2-user-study-readiness/report/tables/participant-flow.csv"
    )
    derivative_root = Path(
        "results_v5/protocol-v5.0.0/compatibility/E3/b0-p2-user-study-readiness-regeneration-v2"
    )
    derivative = derivative_root / "report/tables/participant-flow.csv"
    derivative_manifest = read_json(root / derivative_root / "manifest.json")
    inventory = read_json(root / str((final_audit.get("source_inventory") or {})["path"]))
    actual_historical = file_sha256(root / historical)
    actual_derivative = file_sha256(root / derivative)
    relationship = derivative_manifest.get("source") or {}
    if inventory.get("files", {}).get(str(historical)) != actual_historical:
        raise ValueError("historical E3 bytes differ from the audit-start inventory")
    if derivative_manifest.get("output_checksums", {}).get("report/tables/participant-flow.csv") != actual_derivative:
        raise ValueError("E3 derivative checksum differs from its manifest")
    if relationship.get("historical_bytes_modified") is not False or actual_historical == actual_derivative:
        raise ValueError("E3 historical/derivative identities are not explicitly distinct")
    return {
        "status": "PASS",
        "historical": {
            "path": str(historical),
            "sha256": actual_historical,
            "recorded_historical_manifest_identity_sha256": relationship.get("historical_manifest_sha256"),
            "byte_preserved_against_audit_inventory": True,
        },
        "current_policy_derivative": {
            "path": str(derivative),
            "sha256": actual_derivative,
            "manifest_path": str(derivative_root / "manifest.json"),
            "manifest_sha256": file_sha256(root / derivative_root / "manifest.json"),
        },
        "relationship": {
            "type": "VERSIONED_CRLF_TO_LF_COMPATIBILITY_DERIVATIVE",
            "newline_policy_transform": relationship.get("newline_policy_transform"),
            "historical_bytes_modified": relationship.get("historical_bytes_modified"),
            "identities_are_substitutable": False,
        },
    }


def _published_history(root: Path, final_revision: str) -> list[dict[str, Any]]:
    result_root = root / "results_v5/protocol-v5.0.0/final-audit"
    tracked = set(
        subprocess.run(
            ["git", "ls-files", "results_v5/protocol-v5.0.0/final-audit"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    )
    history = []
    for path in sorted(result_root.glob("final-audit-*/run.json")):
        relative = str(path.relative_to(root))
        if relative not in tracked:
            continue
        run = read_json(path)
        revision = run.get("audit_git_revision")
        history.append(
            {
                "artifact": path.parent.name,
                "artifact_type": "final_audit_run",
                "implementation_revision": revision,
                "run_manifest_sha256": file_sha256(path),
                "authoritative_for_final_revision": revision == final_revision,
                "disposition": (
                    "CURRENT_FINAL_IMPLEMENTATION"
                    if revision == final_revision
                    else "SUPERSEDED_BY_LATER_IMPLEMENTATION"
                ),
            }
        )
    for path in sorted(result_root.glob("completion-audit-*/completion-audit.json")):
        relative = str(path.relative_to(root))
        if relative not in tracked:
            continue
        completion = read_json(path)
        workflow_path = path.parent / "evidence/workflow-results.json"
        workflow = read_json(workflow_path) if workflow_path.is_file() else {}
        completion_revision = completion.get("git_revision")
        workflow_revision = workflow.get("git_revision")
        history.append(
            {
                "artifact": path.parent.name,
                "artifact_type": "completion_audit",
                "implementation_revision": completion_revision,
                "bundled_workflow_revision": workflow_revision,
                "revision_consistent": completion_revision == workflow_revision,
                "completion_sha256": file_sha256(path),
                "authoritative_for_final_revision": False,
                "disposition": (
                    "SUPERSEDED_WORKFLOW_REVISION_MISMATCH"
                    if completion_revision != workflow_revision
                    else "SUPERSEDED_BY_LATER_IMPLEMENTATION"
                ),
            }
        )
    return history


def _copy_evidence_package(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*")):
        if path.is_file():
            write_bytes(destination / path.relative_to(source), path.read_bytes())


def _remaining_execution_requirements(
    final_audit: dict[str, Any], blocking_checks: list[dict[str, Any]]
) -> list[str]:
    state_by_experiment: dict[str, list[str]] = {}
    for row in final_audit.get("experiment_states") or []:
        if row.get("status") in {"NOT_EXECUTED", "DEVELOPMENT_ONLY", "UNRESOLVED", "UNSUPPORTED"}:
            state_by_experiment.setdefault(str(row.get("experiment")), []).append(str(row.get("status")))
    remaining = []
    for experiment, states in sorted(state_by_experiment.items()):
        if experiment == "E6" and not str(final_audit.get("p3_state") or "").startswith("RETAINED_"):
            remaining.append(
                "E6: no confirmatory execution is authorized unless a frozen authenticated development gate retains P3 "
                f"(current state {final_audit.get('p3_state', 'UNSUPPORTED')})"
            )
        else:
            remaining.append(
                f"{experiment}: authenticated real evidence ({'/'.join(sorted(set(states)))})"
            )
    if blocking_checks:
        remaining.insert(0, "authoritative freeze, sealed split/custody, and isolation gates must pass")
    return remaining


def _markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Protocol-v5 16-prompt Completion Audit",
        "",
        f"Verdict: **{audit['verdict']}**.",
        "",
        "This is a software and evidence-boundary audit, not experiment evidence. Synthetic regression fixtures cannot support a thesis claim.",
        "",
        "| Issue | Severity | Before | After | Disposition | Regression evidence |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for issue in audit["issues"]:
        evidence = "; ".join(row["nodeid"] + "=" + row["status"] for row in issue["test_evidence"])
        lines.append(
            f"| {issue['id']} | {issue['severity']} | {issue['before']} | {issue['after']} | {issue['status']} | {evidence} |"
        )
    lines.extend(
        [
            "",
            "## Revision-bound test evidence",
            "",
            "| Suite | Revision | Passed | Failed | Errors | Skipped | Exit | Command |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in audit["test_evidence"]:
        counts = row["counts"]
        lines.append(
            f"| {row['evidence_id']} | `{row['git_revision']}` | {counts['passed']} | {counts['failed']} | "
            f"{counts['errors']} | {counts['skipped']} | {row['exit_code']} | `{row['command']}` |"
        )
    lines.extend(
        [
            "",
            "## Validator evidence",
            "",
            "| Validator | Revision | Exit | Classification | Command |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for row in audit["validator_evidence"]:
        lines.append(
            f"| {row['evidence_id']} | `{row['git_revision']}` | {row['exit_code']} | "
            f"{'; '.join(row['classifications'])} | `{row['command']}` |"
        )
    lines.extend(
        [
            "",
            "## Workflow evidence",
            "",
            "| Stage | Revision | Exit | Classification | Command |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for row in audit["workflows"]:
        lines.append(
            f"| {row['evidence_id']} | `{row['git_revision']}` | {row['exit_code']} | "
            f"{'; '.join(row['classifications'])} | `{row['command']}` |"
        )
    reproduction = audit["canonical_reproducibility_workflow"]
    lines.extend(
        [
            "",
            "## Canonical final reproducibility workflow",
            "",
            f"`{' '.join(reproduction['argv'])}` ran at `{reproduction['git_revision']}` and exited "
            f"{reproduction['exit_code']}. Configuration proof: Makefile "
            f"`{reproduction['configuration_proof']['makefile']['sha256']}` and documentation "
            f"`{reproduction['configuration_proof']['documentation']['sha256']}`. Nonzero reasons: "
            + ("; ".join(
                row["code"] + "=" + row["category"]
                for row in reproduction.get("nonzero_reasons") or []
            ) or "none"),
            "",
        ]
    )
    chain = audit["provenance_chain"]
    isolation = audit["isolation_failure_disposition"]["finding"]
    lines.extend(
        [
            "",
            "## Provenance chain and superseded artifacts",
            "",
            f"Final implementation revision: `{chain['final_implementation_revision']}`. "
            f"Tests, validators, Make workflow, all four audit stages, final report, and this completion audit are revision-consistent: **{str(chain['revision_consistent']).lower()}**.",
            "",
            f"The prior isolation failure came from `{isolation['artifact_relative_path']}` at SHA-256 `{isolation['artifact_sha256']}`. "
            f"It is classified **{isolation['classification']}** with repair status **{audit['isolation_failure_disposition']['repair']['status']}**; "
            f"confirmatory-eligible={str(isolation['eligible_for_confirmatory_execution']).lower()}, thesis-claim-eligible={str(isolation['eligible_to_support_thesis_claim']).lower()}.",
            "",
            "| Historical artifact | Implementation revision | Workflow revision | Disposition |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in audit["published_history"]:
        lines.append(
            f"| {row['artifact']} | `{row.get('implementation_revision')}` | "
            f"`{row.get('bundled_workflow_revision', 'N/A')}` | {row.get('disposition', 'SUPERSEDED_BY_FINAL_RUN')} |"
        )
    gates = audit["scientific_gates"]
    lines.extend(
        [
            "",
            "## Scientific gates",
            "",
            f"- Sealed confirmatory execution safe now: **{str(gates['sealed_confirmatory_execution_safe']).lower()}**.",
            f"- Synthetic evidence can become OBSERVED: **{str(gates['synthetic_can_become_observed']).lower()}**.",
            f"- Synthetic evidence can support a thesis claim: **{str(gates['synthetic_can_support_thesis_claim']).lower()}**.",
            f"- Authenticated confirmatory status: **{gates['confirmatory_status']}**.",
            "- Remaining real-execution requirements: " + ("; ".join(gates["remaining_real_execution_requirements"]) or "none"),
            "",
        ]
    )
    return "\n".join(lines)


def _traceability_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Protocol-v5 Adversarial Regression Traceability",
        "",
        "Every row is bound to the adversarial JUnit command record. Test fixtures are not experiment evidence.",
        "",
    ]
    for issue in audit["issues"]:
        lines.extend(
            [
                f"## {issue['id']} — {issue['severity']} — {issue['final_disposition']}",
                "",
                f"- Original exploit: {issue['original_exploit']}",
                f"- Regression node IDs: `{'; '.join(issue['regression_test_node_ids'])}`",
                f"- Fixture/artifact: {issue['fixture_or_artifact']}",
                f"- Artifact types: `{'; '.join(issue['artifact_types'])}`",
                f"- Expected behavior: {issue['expected_security_or_evidence_behavior']}",
                "- Actual result: `" + "; ".join(
                    row["nodeid"] + "=" + row["result"] for row in issue["actual_result"]
                ) + "`",
                f"- Closure behavior: `{issue['closure_behavior']}`",
                f"- Evidence record: `{issue['evidence_record_path']}`",
                f"- Evidence SHA-256: `{issue['evidence_record_sha256']}`",
                "",
            ]
        )
    return "\n".join(lines)


def _command_summary(record: dict[str, Any], root: Path) -> dict[str, Any]:
    package = Path(record["package"])
    summary = {key: value for key, value in record.items() if key != "package"}
    summary["record"] = {
        "path": _display_path(package / "record.json", root),
        "sha256": file_sha256(package / "record.json"),
    }
    summary["manifest"] = {
        "path": _display_path(package / "manifest.json", root),
        "sha256": file_sha256(package / "manifest.json"),
    }
    if record["kind"] == "test":
        details = junit_details(package / record["junit"]["path"])
        summary["counts"] = {key: value for key, value in details.items() if key != "cases"}
    return summary


def _validate_command_outcomes(records: list[dict[str, Any]], *, tests: bool = False) -> list[str]:
    defects = []
    for record in records:
        evidence_id = str(record["evidence_id"])
        classifications = set(record.get("classifications") or [])
        reasons = record.get("nonzero_reasons") or []
        reason_categories = {str(reason.get("category")) for reason in reasons}
        exit_code = int(record.get("exit_code", -1))
        if classifications & {"IMPLEMENTATION_DEFECT", "REMAINING_IMPLEMENTATION_DEFECT"}:
            defects.append(evidence_id + ": classified IMPLEMENTATION_DEFECT")
        if "REMAINING_IMPLEMENTATION_DEFECT" in reason_categories:
            defects.append(evidence_id + ": nonzero reason classified REMAINING_IMPLEMENTATION_DEFECT")
        if tests:
            counts = record.get("junit", {}).get("counts") or {}
            if exit_code != 0 or counts.get("failed") or counts.get("errors"):
                defects.append(evidence_id + ": tests did not pass")
            if classifications != {"PASS"}:
                defects.append(evidence_id + ": passing regression evidence must be classified PASS")
        elif exit_code == 0 and classifications != {"PASS"}:
            defects.append(evidence_id + ": zero exit must be classified PASS")
        elif exit_code != 0:
            if not reasons:
                defects.append(evidence_id + ": nonzero exit lacks exact reason codes")
            if not classifications or "PASS" in classifications:
                defects.append(evidence_id + ": nonzero exit lacks an explicit non-PASS classification")
            if classifications != reason_categories:
                defects.append(evidence_id + ": reason categories differ from command classifications")
    return defects


def build_completion_audit(
    *,
    root: Path,
    test_records: list[dict[str, Any]],
    validator_records: list[dict[str, Any]],
    workflow_records: list[dict[str, Any]],
    reproducibility_record: dict[str, Any],
    reproducibility_verification: dict[str, Any],
    publication_delta_attestation: dict[str, Any],
    final_run: dict[str, Any],
) -> dict[str, Any]:
    final_revision = _git_revision(root)
    revision_sets = {
        "tests": sorted({row["git_revision"] for row in test_records}),
        "validators": sorted({row["git_revision"] for row in validator_records}),
        "workflows": sorted({row["git_revision"] for row in workflow_records}),
        "reproducibility": [reproducibility_record["git_revision"]],
        "final_audit_stages": sorted({row["git_revision"] for row in final_run["stages"]}),
    }
    revision_consistent = all(values == [final_revision] for values in revision_sets.values())
    if final_run["git_revision"] != final_revision or not revision_consistent:
        raise ValueError("completion inputs do not share the final implementation revision")

    adversarial = next(row for row in test_records if row["evidence_id"] == "adversarial")
    cases = _test_cases(adversarial)
    for record in test_records:
        _test_cases(record)
    implementation_defects = [
        *_validate_command_outcomes(test_records, tests=True),
        *_validate_command_outcomes(validator_records),
        *_validate_command_outcomes(workflow_records),
        *_validate_command_outcomes([reproducibility_record]),
    ]
    isolation_validator = next(row for row in validator_records if row["evidence_id"] == "isolation")
    if isolation_validator["exit_code"] != 0 or isolation_validator["classifications"] != ["PASS"]:
        implementation_defects.append("isolation: repaired standalone audit did not pass")

    final_audit = final_run["payload"]
    diagnostic = final_audit.get("isolation_diagnostic") or {}
    isolation_check = next((row for row in final_audit.get("checks") or [] if row.get("id") == 3), {})
    if (
        diagnostic.get("finding", {}).get("classification") != "REMAINING_IMPLEMENTATION_DEFECT"
        or diagnostic.get("repair", {}).get("status") != "REPAIRED"
        or diagnostic.get("source_blob_verified") is not True
        or not isolation_check.get("details")
        or isolation_check["details"][0].get("repository_scan") != "PASS"
    ):
        implementation_defects.append("isolation: exact prior defect disposition is not authenticated as repaired")

    issues = []
    adversarial_record_path = _display_path(
        Path(adversarial["package"]) / "record.json", root
    )
    adversarial_record_sha256 = file_sha256(Path(adversarial["package"]) / "record.json")
    for definition in ISSUES:
        evidence = []
        missing = []
        for expected in definition["tests"]:
            matches = _matches(cases, expected)
            if not matches:
                missing.append(expected)
            evidence.extend({"nodeid": nodeid, "status": status} for nodeid, status in matches)
        if missing:
            status = "PARTIALLY_SATISFIED" if evidence else "NOT_SATISFIED"
        elif any(row["status"] != "PASSED" for row in evidence):
            status = "NOT_SATISFIED"
        else:
            status = "SATISFIED"
        code_evidence = []
        for relative in definition["code"]:
            path = root / relative
            code_evidence.append(
                {"path": relative, "sha256": file_sha256(path) if path.is_file() else None, "present": path.is_file()}
            )
            if not path.is_file():
                status = "PARTIALLY_SATISFIED" if evidence else "NOT_SATISFIED"
        trace = TRACE_METADATA[definition["id"]]
        issues.append(
            {
                "id": definition["id"],
                "severity": definition["severity"],
                "title": definition["title"],
                "before": definition["title"] + " exploit was reproducible in the original audit.",
                "after": definition["closed_outcome"],
                "status": status,
                "missing_tests": missing,
                "test_evidence": evidence,
                "code_evidence": code_evidence,
                "original_exploit": trace["original_exploit"],
                "regression_test_node_ids": list(definition["tests"]),
                "fixture_or_artifact": trace["fixture_or_artifact"],
                "artifact_types": trace["artifact_types"],
                "expected_security_or_evidence_behavior": trace["expected_behavior"],
                "actual_result": [
                    {"nodeid": row["nodeid"], "result": row["status"]}
                    for row in evidence
                ],
                "closure_behavior": trace["closure_behavior"],
                "evidence_record_path": adversarial_record_path,
                "evidence_record_sha256": adversarial_record_sha256,
                "final_disposition": status,
            }
        )
    satisfied = sum(issue["status"] == "SATISFIED" for issue in issues)
    blocking_checks = [
        row for row in final_audit.get("checks") or []
        if row.get("id") in {1, 2, 3, 4, 5} and row.get("verdict") != "PASS"
    ]
    remaining = _remaining_execution_requirements(final_audit, blocking_checks)
    critical_synthetic = {"P11-1", "P14-1", "P16-1"}
    synthetic_closed = all(
        issue["status"] == "SATISFIED" for issue in issues if issue["id"] in critical_synthetic
    )
    if implementation_defects:
        verdict = f"{satisfied}/16 SATISFIED; UNRESOLVED IMPLEMENTATION DEFECT"
    elif satisfied == 16:
        verdict = "16/16 SATISFIED"
    else:
        verdict = f"{satisfied}/16 SATISFIED; REMAINDER REQUIRES REPAIR"
    inventory = read_json(root / final_audit["source_inventory"]["path"])
    return {
        "schema_version": "protocol-v5-completion-audit-v3.0.0",
        "protocol_version": "5.0.0",
        "created_at_utc": _utc_now(),
        "git_revision": final_revision,
        "artifact_role": "software_and_evidence_boundary_audit",
        "is_experiment_evidence": False,
        "supports_thesis_claim": False,
        "issues": issues,
        "severity_counts": {severity: sum(issue["severity"] == severity for issue in issues) for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW")},
        "status_counts": {status: sum(issue["status"] == status for issue in issues) for status in ("SATISFIED", "SATISFIED_WITH_MINOR_ISSUES", "PARTIALLY_SATISFIED", "NOT_SATISFIED")},
        "test_evidence": [_command_summary(row, root) for row in test_records],
        "validator_evidence": [_command_summary(row, root) for row in validator_records],
        "workflows": [_command_summary(row, root) for row in workflow_records],
        "canonical_reproducibility_workflow": {
            **reproducibility_verification,
            "evidence": _command_summary(reproducibility_record, root),
        },
        "final_audit_evidence": {key: value for key, value in final_run.items() if key != "payload"},
        "claim_flow_attestation": claim_flow_attestation(final_audit),
        "e3_preservation": _e3_preservation(root, final_audit),
        "publication_delta_attestation": publication_delta_attestation,
        "revision_model": {
            "tested_code_revision": final_revision,
            "publication_revision": None,
            "publication_revision_status": "RESOLVED_ONLY_AFTER_GENERATED_ARTIFACT_PUBLICATION",
            "self_reference_policy": "Never claim the enclosing future publication commit was the tested commit.",
            "previous_tested_publication_gap": {
                "tested_code_revision": publication_delta_attestation["tested_code_revision"],
                "publication_revision": publication_delta_attestation["publication_revision"],
                "verdict": publication_delta_attestation["verdict"],
            },
        },
        "provenance_chain": {
            "final_implementation_revision": final_revision,
            "test_evidence_revisions": revision_sets["tests"],
            "validator_evidence_revisions": revision_sets["validators"],
            "workflow_evidence_revisions": revision_sets["workflows"],
            "reproducibility_evidence_revisions": revision_sets["reproducibility"],
            "final_audit_stage_revisions": revision_sets["final_audit_stages"],
            "final_audit_run_id": final_run["run_id"],
            "final_report": final_run["final_audit"],
            "completion_generator_revision": final_revision,
            "historical_collection_revision": inventory.get("source_git_revision"),
            "revision_consistent": revision_consistent,
        },
        "published_history": _published_history(root, final_revision),
        "workflow_revision_mismatch_disposition": {
            "artifact": "completion-audit-20260910-final3",
            "completion_revision": "ad098134e7a2d74cab75dfbaa4519bdaf582fbb0",
            "bundled_workflow_revision": "a297f622889761145e20567dab9f5fe42f275cb2",
            "revision_consistent": False,
            "historical_artifacts_mutated": False,
            "disposition": "SUPERSEDED_WORKFLOW_REVISION_MISMATCH",
        },
        "isolation_failure_disposition": diagnostic,
        "unresolved_implementation_defects": implementation_defects,
        "scientific_gates": {
            "sealed_confirmatory_execution_safe": not blocking_checks,
            "confirmatory_status": final_audit.get("confirmatory_status", "UNSUPPORTED"),
            "synthetic_can_become_observed": False if synthetic_closed else None,
            "synthetic_can_support_thesis_claim": False if synthetic_closed else None,
            "claim_counts": final_audit.get("claim_counts") or {},
            "remaining_real_execution_requirements": remaining,
        },
        "software_evidence_boundary_verdict": verdict,
        "scientific_readiness_verdict": (
            "READY" if not blocking_checks and final_audit.get("confirmatory_status") == "EXECUTED_COMPLETE"
            else "NOT_READY"
        ),
        "verdict": verdict,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-evidence", type=Path, action="append", required=True)
    parser.add_argument("--validator-evidence", type=Path, action="append", required=True)
    parser.add_argument("--workflow-evidence", type=Path, action="append", required=True)
    parser.add_argument("--reproducibility-evidence", type=Path, required=True)
    parser.add_argument("--publication-attestation", type=Path, required=True)
    parser.add_argument("--final-audit-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    final_revision = _git_revision(root)
    for command in (
        ["git", "diff", "--quiet", "HEAD", "--"],
        ["git", "diff", "--cached", "--quiet", "HEAD", "--"],
    ):
        if subprocess.run(command, cwd=root, check=False).returncode != 0:
            raise ValueError("completion audit requires no tracked changes after evidence execution")
    test_records = _load_evidence_records(
        args.test_evidence, kind="test", required_ids=REQUIRED_TEST_EVIDENCE,
        expected_revision=final_revision,
    )
    validator_records = _load_evidence_records(
        args.validator_evidence, kind="validator", required_ids=REQUIRED_VALIDATOR_EVIDENCE,
        expected_revision=final_revision,
    )
    workflow_records = _load_evidence_records(
        args.workflow_evidence, kind="workflow", required_ids=set(REQUIRED_WORKFLOW_EVIDENCE),
        expected_revision=final_revision,
    )
    reproducibility_records = _load_evidence_records(
        [args.reproducibility_evidence], kind="workflow",
        required_ids=REQUIRED_REPRODUCIBILITY_EVIDENCE,
        expected_revision=final_revision,
    )
    reproducibility_verification = _verify_canonical_reproducibility(
        root, reproducibility_records[0], expected_revision=final_revision
    )
    publication_payload = read_json(args.publication_attestation.resolve())
    verify_attestation(root, publication_payload)
    final_run = _verify_final_audit_run(
        root, args.final_audit_run, expected_revision=final_revision, workflows=workflow_records
    )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    copied: dict[str, list[dict[str, Any]]] = {
        "test": [], "validator": [], "workflow": [], "reproducibility": [],
    }
    for kind, records in (
        ("test", test_records), ("validator", validator_records), ("workflow", workflow_records)
        , ("reproducibility", reproducibility_records)
    ):
        for record in records:
            destination = output / "evidence" / kind / record["evidence_id"]
            _copy_evidence_package(record["package"], destination)
            copied[kind].append({**verify_command_evidence(destination), "package": destination})
    publication_copy = output / "evidence/publication-delta-attestation.json"
    write_bytes(publication_copy, args.publication_attestation.resolve().read_bytes())
    final_copy = output / "evidence/final-audit"
    source_run = args.final_audit_run.resolve()
    for relative in (
        "run.json", "validation/manifest.json", "analysis/manifest.json",
        "figures/manifest.json", "report/manifest.json", "report/audit.json",
        "report/PROTOCOL_V5_FINAL_REPORT.md",
    ):
        write_bytes(final_copy / relative, (source_run / relative).read_bytes())
    final_run["completion_copy"] = {
        "path": _display_path(final_copy, root),
        "files": {
            str(path.relative_to(final_copy)): file_sha256(path)
            for path in sorted(final_copy.rglob("*")) if path.is_file()
        },
    }
    audit = build_completion_audit(
        root=root,
        test_records=copied["test"],
        validator_records=copied["validator"],
        workflow_records=copied["workflow"],
        reproducibility_record=copied["reproducibility"][0],
        reproducibility_verification=reproducibility_verification,
        publication_delta_attestation=publication_payload,
        final_run=final_run,
    )
    write_json(output / "completion-audit.json", audit)
    write_bytes(output / "COMPLETION_AUDIT.md", _markdown(audit).encode())
    write_json(
        output / "adversarial-traceability.json",
        {
            "schema_version": "protocol-v5-adversarial-traceability-v1.0.0",
            "git_revision": audit["git_revision"],
            "artifact_role": "software_regression_evidence",
            "is_experiment_evidence": False,
            "supports_thesis_claim": False,
            "issues": audit["issues"],
        },
    )
    write_bytes(
        output / "ADVERSARIAL_TRACEABILITY.md",
        _traceability_markdown(audit).encode(),
    )
    outputs = {
        str(path.relative_to(output)): file_sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    manifest = {
        "schema_version": "protocol-v5-completion-audit-package-v3.0.0",
        "created_at_utc": audit["created_at_utc"],
        "git_revision": audit["git_revision"],
        "protocol_version": "5.0.0",
        "status": "COMPLETE" if audit["verdict"] == "16/16 SATISFIED" else "INCOMPLETE",
        "is_experiment_evidence": False,
        "supports_thesis_claim": False,
        "output_checksums": outputs,
    }
    write_json(output / "manifest.json", manifest)
    sums = {**outputs, "manifest.json": file_sha256(output / "manifest.json")}
    write_bytes(output / "SHA256SUMS", "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())).encode())
    print(json.dumps({
        "output": str(output),
        "verdict": audit["verdict"],
        "test_counts": {row["evidence_id"]: row["counts"] for row in audit["test_evidence"]},
        "final_implementation_revision": audit["git_revision"],
        "final_audit_run_id": audit["provenance_chain"]["final_audit_run_id"],
    }, indent=2))
    return 0 if audit["verdict"] == "16/16 SATISFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
