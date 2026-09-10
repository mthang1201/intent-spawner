"""Build the immutable 16-issue completion audit from machine-readable runs."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
from typing import Any, Sequence

from .common import file_sha256, write_bytes, write_json


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
        "tests": ("tests/test_evaluation_v5_isolation.py::test_arbitrary_freeze_identity_string_cannot_authorize_runner",),
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _test_results(path: Path) -> tuple[dict[str, str], dict[str, int]]:
    root = ET.parse(path).getroot()
    cases: dict[str, str] = {}
    for case in root.iter("testcase"):
        classname = str(case.get("classname") or "").replace(".", "/") + ".py"
        nodeid = classname + "::" + str(case.get("name") or "")
        if case.find("failure") is not None or case.find("error") is not None:
            status = "FAILED"
        elif case.find("skipped") is not None:
            status = "SKIPPED"
        else:
            status = "PASSED"
        cases[nodeid] = status
    counts = Counter(cases.values())
    return cases, {key: counts.get(key, 0) for key in ("PASSED", "FAILED", "SKIPPED")}


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
            "## Workflow evidence",
            "",
            "| Command | Exit | Classification | Result |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in audit["workflows"]:
        lines.append(f"| `{row['command']}` | {row['exit_code']} | {row['classification']} | {row['result']} |")
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


def build_completion_audit(
    *, root: Path, junit_xml: Path, workflow_json: Path, final_audit_json: Path
) -> dict[str, Any]:
    cases, test_counts = _test_results(junit_xml)
    workflow = json.loads(workflow_json.read_text(encoding="utf-8"))
    final_audit = json.loads(final_audit_json.read_text(encoding="utf-8"))
    workflows = workflow.get("workflows") or []
    required_workflows = {"make v5-validate", "make v5-analyze", "make v5-figures", "make v5-audit"}
    if {row.get("command") for row in workflows} != required_workflows:
        raise ValueError("workflow evidence must contain the four exact Make stages")
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
    state_by_experiment: dict[str, list[str]] = {}
    for row in final_audit.get("experiment_states") or []:
        if row.get("status") in {"NOT_EXECUTED", "DEVELOPMENT_ONLY", "UNRESOLVED", "UNSUPPORTED"}:
            state_by_experiment.setdefault(str(row.get("experiment")), []).append(str(row.get("status")))
    remaining = [
        f"{experiment}: authenticated real evidence ({'/'.join(sorted(set(states)))})"
        for experiment, states in sorted(state_by_experiment.items())
    ]
    if blocking_checks:
        remaining.insert(0, "authoritative freeze, sealed split/custody, and isolation gates must pass")
    critical_synthetic = {"P11-1", "P14-1", "P16-1"}
    synthetic_closed = all(
        issue["status"] == "SATISFIED" for issue in issues if issue["id"] in critical_synthetic
    )
    verdict = f"{satisfied}/16 SATISFIED" if satisfied == 16 else f"{satisfied}/16 SATISFIED; REMAINDER REQUIRES REPAIR"
    return {
        "schema_version": "protocol-v5-completion-audit-v1.0.0",
        "protocol_version": "5.0.0",
        "created_at_utc": _utc_now(),
        "git_revision": _git_revision(root),
        "artifact_role": "software_and_evidence_boundary_audit",
        "is_experiment_evidence": False,
        "supports_thesis_claim": False,
        "junit": {"path": _display_path(junit_xml, root), "sha256": file_sha256(junit_xml), "counts": test_counts},
        "workflow_evidence": {"path": _display_path(workflow_json, root), "sha256": file_sha256(workflow_json)},
        "final_audit_evidence": {"path": _display_path(final_audit_json, root), "sha256": file_sha256(final_audit_json)},
        "issues": issues,
        "severity_counts": {severity: sum(issue["severity"] == severity for issue in issues) for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW")},
        "status_counts": {status: sum(issue["status"] == status for issue in issues) for status in ("SATISFIED", "SATISFIED_WITH_MINOR_ISSUES", "PARTIALLY_SATISFIED", "NOT_SATISFIED")},
        "workflows": workflows,
        "scientific_gates": {
            "sealed_confirmatory_execution_safe": not blocking_checks,
            "confirmatory_status": final_audit.get("confirmatory_status", "UNSUPPORTED"),
            "synthetic_can_become_observed": not synthetic_closed,
            "synthetic_can_support_thesis_claim": not synthetic_closed,
            "remaining_real_execution_requirements": remaining,
        },
        "verdict": verdict,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit-xml", type=Path, required=True)
    parser.add_argument("--workflow-json", type=Path, required=True)
    parser.add_argument("--final-audit-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    junit_copy = output / "evidence/adversarial-regressions.xml"
    workflow_copy = output / "evidence/workflow-results.json"
    write_bytes(junit_copy, args.junit_xml.resolve().read_bytes())
    write_bytes(workflow_copy, args.workflow_json.resolve().read_bytes())
    audit = build_completion_audit(
        root=root,
        junit_xml=junit_copy,
        workflow_json=workflow_copy,
        final_audit_json=args.final_audit_json.resolve(),
    )
    write_json(output / "completion-audit.json", audit)
    write_bytes(output / "COMPLETION_AUDIT.md", _markdown(audit).encode())
    manifest = {
        "schema_version": "protocol-v5-completion-audit-package-v1.0.0",
        "created_at_utc": audit["created_at_utc"],
        "git_revision": audit["git_revision"],
        "protocol_version": "5.0.0",
        "status": "COMPLETE" if audit["verdict"] == "16/16 SATISFIED" else "INCOMPLETE",
        "is_experiment_evidence": False,
        "supports_thesis_claim": False,
        "output_checksums": {
            "completion-audit.json": file_sha256(output / "completion-audit.json"),
            "COMPLETION_AUDIT.md": file_sha256(output / "COMPLETION_AUDIT.md"),
            "evidence/adversarial-regressions.xml": file_sha256(junit_copy),
            "evidence/workflow-results.json": file_sha256(workflow_copy),
        },
    }
    write_json(output / "manifest.json", manifest)
    sums = {**manifest["output_checksums"], "manifest.json": file_sha256(output / "manifest.json")}
    write_bytes(output / "SHA256SUMS", "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())).encode())
    print(json.dumps({"output": str(output), "verdict": audit["verdict"], "test_counts": audit["junit"]["counts"]}, indent=2))
    return 0 if audit["verdict"] == "16/16 SATISFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
