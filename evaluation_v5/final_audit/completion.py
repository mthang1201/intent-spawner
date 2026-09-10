"""Build the immutable 16-issue completion audit from machine-readable runs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any, Sequence

from .common import file_sha256, read_json, verify_seal, write_bytes, write_json
from .evidence import junit_details, verify_command_evidence


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
        "tests": ("tests/test_protocol_v5_research_analysis.py::test_controlled_synthetic_storage_reproduction_never_supports_h7",),
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
            "tests/test_protocol_v5_final_audit.py::test_final_audit_consumes_explicit_authenticated_claim_package",
            "tests/test_protocol_v5_final_audit.py::test_report_renders_changed_validated_claim_state_instead_of_snapshot_prose",
            "tests/test_protocol_v5_final_audit.py::test_collector_origin_scan_rejects_synthetic_observed_fixture_outside_repository",
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
}
REQUIRED_WORKFLOW_EVIDENCE = {
    "v5_validate": "v5-validate",
    "v5_analyze": "v5-analyze",
    "v5_figures": "v5-figures",
    "v5_audit": "v5-audit",
}


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
        exit_code = int(record.get("exit_code", -1))
        if "IMPLEMENTATION_DEFECT" in classifications:
            defects.append(evidence_id + ": classified IMPLEMENTATION_DEFECT")
        if tests:
            counts = record.get("junit", {}).get("counts") or {}
            if exit_code != 0 or counts.get("failed") or counts.get("errors"):
                defects.append(evidence_id + ": tests did not pass")
            if classifications != {"PASS"}:
                defects.append(evidence_id + ": passing regression evidence must be classified PASS")
        elif exit_code == 0 and classifications != {"PASS"}:
            defects.append(evidence_id + ": zero exit must be classified PASS")
        elif exit_code != 0 and (not classifications or "PASS" in classifications):
            defects.append(evidence_id + ": nonzero exit lacks an explicit non-PASS classification")
    return defects


def build_completion_audit(
    *,
    root: Path,
    test_records: list[dict[str, Any]],
    validator_records: list[dict[str, Any]],
    workflow_records: list[dict[str, Any]],
    final_run: dict[str, Any],
) -> dict[str, Any]:
    final_revision = _git_revision(root)
    revision_sets = {
        "tests": sorted({row["git_revision"] for row in test_records}),
        "validators": sorted({row["git_revision"] for row in validator_records}),
        "workflows": sorted({row["git_revision"] for row in workflow_records}),
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
        "schema_version": "protocol-v5-completion-audit-v2.0.0",
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
        "final_audit_evidence": {key: value for key, value in final_run.items() if key != "payload"},
        "provenance_chain": {
            "final_implementation_revision": final_revision,
            "test_evidence_revisions": revision_sets["tests"],
            "validator_evidence_revisions": revision_sets["validators"],
            "workflow_evidence_revisions": revision_sets["workflows"],
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
    final_run = _verify_final_audit_run(
        root, args.final_audit_run, expected_revision=final_revision, workflows=workflow_records
    )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    copied: dict[str, list[dict[str, Any]]] = {"test": [], "validator": [], "workflow": []}
    for kind, records in (
        ("test", test_records), ("validator", validator_records), ("workflow", workflow_records)
    ):
        for record in records:
            destination = output / "evidence" / kind / record["evidence_id"]
            _copy_evidence_package(record["package"], destination)
            copied[kind].append({**verify_command_evidence(destination), "package": destination})
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
        final_run=final_run,
    )
    write_json(output / "completion-audit.json", audit)
    write_bytes(output / "COMPLETION_AUDIT.md", _markdown(audit).encode())
    outputs = {
        str(path.relative_to(output)): file_sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    manifest = {
        "schema_version": "protocol-v5-completion-audit-package-v2.0.0",
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
