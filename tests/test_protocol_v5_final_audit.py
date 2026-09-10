"""Final-audit regressions: existing evidence, synthetic negative cases, and portability."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import copy
import shutil
import socket
import subprocess
import sys

import pytest

from evaluation_v5.final_audit.common import (
    Inputs, LOCK, ROOT, file_sha256, read_json, safe_path, seal, verify_seal, write_bytes, write_json,
)
from evaluation_v5.final_audit.checks import (
    CHECKS, b0_ranking_findings, cluster_findings, execution_findings, inference_findings,
    inspect, load_isolation_diagnostic, p3_findings, placeholder_findings,
    storage_identity_findings,
)
from evaluation_v5.final_audit.claims import load_claim_evidence, synthetic_origin_scan
from evaluation_v5.final_audit import completion as completion_module
from evaluation_v5.final_audit.completion import (
    _load_evidence_records, _remaining_execution_requirements, _validate_command_outcomes,
)
from evaluation_v5.final_audit.evidence import capture, junit_details, verify_command_evidence
from evaluation_v5.final_audit.reproduce import analyze, compare_json
from evaluation_v5.final_audit.reporting import figures, render_report


@pytest.fixture(scope="module")
def current_audit():
    return inspect(Inputs(), isolation=False, historical=False)


def test_all_seventeen_checks_and_missing_freeze_are_explicit(current_audit):
    assert set(CHECKS) == set(range(1, 18))
    assert {c["id"] for c in current_audit["checks"]} == set(CHECKS)
    assert current_audit["checks"][0]["verdict"] == "UNVERIFIED"
    assert current_audit["checks"][1]["verdict"] == "UNVERIFIED"
    assert current_audit["audit_status"] == "FAIL"
    assert all(c["claim_status"] == "NOT_EXECUTED" for c in current_audit["claims"])
    assert all(c["estimate"] is None and c["effect_size"] is None for c in current_audit["claims"])
    assert len(current_audit["evaluated_claims"]) == 9


def test_snapshot_is_not_a_production_freeze():
    from evaluation_v5.freeze import validate_freeze_manifest, FreezeValidationError
    snapshot = Inputs().json("results_v5/protocol-v5.0.0/freezes/frozen-configuration.json")
    with pytest.raises(FreezeValidationError):
        validate_freeze_manifest(snapshot)


def test_isolation_failure_is_exactly_classified_and_repaired():
    inputs = Inputs()
    diagnostic = load_isolation_diagnostic(inputs)
    finding = diagnostic["finding"]
    assert diagnostic["source_blob_verified"] is True
    assert finding | {
        "artifact_relative_path": "tests/test_evaluation_v5_gold_dataset.py",
        "artifact_sha256": "3e978556fa831877c959ee1dc3824315f9933d2f8d12d993cc78019842acc918",
        "artifact_role": "synthetic_adversarial_test_source",
        "artifact_type": "python_source",
        "parser": "evaluation_v5.isolation_audit._contains_embedded_confirmatory_bundle",
        "schema_signature": "protocol-v5-gold-family-v1.0.0",
        "failure_category": "SOURCE_LITERAL_FRAGMENT_FALSE_POSITIVE",
        "original_error_category": "UNPARSEABLE_EMBEDDED_CONFIRMATORY_BUNDLE",
        "classification": "REMAINING_IMPLEMENTATION_DEFECT",
        "historical": False,
        "immutable_preserved_evidence": False,
        "eligible_for_confirmatory_execution": False,
        "eligible_to_support_thesis_claim": False,
    } == finding
    audit = inspect(inputs, isolation=True, historical=False)
    isolation_check = audit["checks"][2]
    assert isolation_check["verdict"] == "UNVERIFIED"
    assert isolation_check["details"][0]["repository_scan"] == "PASS"
    assert isolation_check["details"][1]["prior_failure_diagnostic"]["repair"]["status"] == "REPAIRED"


def test_command_evidence_captures_revision_command_exit_and_hashes(tmp_path):
    output = tmp_path / "command-evidence"
    exit_code = capture(
        root=ROOT,
        output=output,
        evidence_id="unit_capture",
        kind="validator",
        classifications=["PASS"],
        command=[sys.executable, "-c", "print('validated')"],
        junit=None,
    )
    record = verify_command_evidence(output)
    assert exit_code == record["exit_code"] == 0
    assert len(record["git_revision"]) == 40
    assert record["argv"] == [sys.executable, "-c", "print('validated')"]
    assert record["classifications"] == ["PASS"]
    assert record["stdout"]["sha256"] == file_sha256(output / "stdout.txt")


def test_junit_counts_are_recomputed_from_cases(tmp_path):
    junit = tmp_path / "junit.xml"
    write_bytes(
        junit,
        b'<testsuite><testcase classname="tests.test_sample" name="pass"/>'
        b'<testcase classname="tests.test_sample" name="fail"><failure/></testcase>'
        b'<testcase classname="tests.test_sample" name="error"><error/></testcase>'
        b'<testcase classname="tests.test_sample" name="skip"><skipped/></testcase></testsuite>',
    )
    details = junit_details(junit)
    assert {key: value for key, value in details.items() if key != "cases"} == {
        "total": 4, "passed": 1, "failed": 1, "errors": 1, "skipped": 1,
    }


def test_completion_rejects_command_evidence_from_another_revision(tmp_path, monkeypatch):
    package = tmp_path / "evidence"
    package.mkdir()
    monkeypatch.setattr(
        completion_module,
        "verify_command_evidence",
        lambda _path: {
            "evidence_id": "only", "kind": "validator", "git_revision": "0" * 40,
            "git_dirty_before": False,
        },
    )
    with pytest.raises(ValueError, match="revision differs"):
        _load_evidence_records(
            [package], kind="validator", required_ids={"only"}, expected_revision="1" * 40,
        )


def test_scientific_nonzero_is_not_misclassified_as_implementation_defect():
    records = [{
        "evidence_id": "audit", "exit_code": 2,
        "classifications": ["SCIENTIFICALLY_CORRECT_FAIL_CLOSED_STATE"],
    }]
    assert _validate_command_outcomes(records) == []


def test_existing_failures_are_preserved_and_not_selected_away(current_audit):
    packages = current_audit["packages"]
    assert len(packages) == 34
    study = next(p for p in packages if p["kind"] == "user_study")
    mismatch = next(e for e in study["errors"] if e["code"] == "ORIGINAL_CHECKSUM_MISMATCH")
    assert mismatch["crlf_reconstruction_matches_recorded_hash"] is True
    assert mismatch["actual_sha256"] != mismatch["recorded_sha256"]
    assert all(p["validation"] == "PASS" for p in packages if p["kind"] == "resource_efficiency")
    assert all(p["status"] == "NOT_EXECUTED" for p in packages if p["kind"] == "resource_efficiency")
    observed_failure = next(p for p in packages if p["path"].endswith("e5-image-validation-20260905T020014Z"))
    assert observed_failure["status"] == "OBSERVED"
    assert observed_failure["validation"] == "FAIL"
    assert current_audit["checks"][12]["verdict"] == "PASS"


def test_final_audit_consumes_explicit_authenticated_claim_package(current_audit):
    inputs = Inputs()
    bundle = load_claim_evidence(inputs)
    configured = inputs.lock["claim_analysis_package"]
    persisted = inputs.json(configured + "/derived/evaluated-claim-registry.json")["claims"]
    assert bundle["evaluated_claims"] == persisted == current_audit["evaluated_claims"]
    assert current_audit["claim_evidence_sources"]["selection"] == inputs.ref(
        configured + "/derived/evidence-selection.json"
    )
    assert current_audit["claim_counts"] == {
        "SUPPORTED": 0,
        "NOT_SUPPORTED": 0,
        "NOT_EXECUTED": 9,
    }
    source = (ROOT / "evaluation_v5/final_audit/checks.py").read_text()
    assert "selected={}" not in source
    assert 'confirmatory_status": "NOT_EXECUTED"' not in source


def test_report_renders_changed_validated_claim_state_instead_of_snapshot_prose(
    current_audit, tmp_path
):
    audit = copy.deepcopy(current_audit)
    audit["audit_status"] = "INCOMPLETE"
    audit["confirmatory_status"] = "EXECUTED_INCOMPLETE"
    audit["claim_counts"] = {"SUPPORTED": 1, "NOT_SUPPORTED": 1, "NOT_EXECUTED": 7}
    audit["claims"][0].update(
        claim_status="SUPPORTED",
        normalized_metrics={"effect": 0.25, "ci_low": 0.10, "ci_high": 0.40, "family_n": 12},
        confidence_intervals={"primary": {"low": 0.10, "high": 0.40}},
        counts={"family_n": 12},
        effect_sizes={},
        reason_codes=[],
    )
    audit["claims"][1].update(
        claim_status="NOT_SUPPORTED",
        normalized_metrics={"effect": -0.05, "ci_low": -0.15, "ci_high": 0.02},
        confidence_intervals={"primary": {"low": -0.15, "high": 0.02}},
        counts={},
        effect_sizes={},
        reason_codes=[],
    )
    analysis = {
        "claims": audit["claims"],
        "observed_functional": [],
        "legacy_functional": [],
        "observed_offline_counts": [],
        "defense_sources": {key: [] for key in ("human", "resources", "offline", "functional", "storage")},
    }
    report = render_report(Inputs(), audit, analysis, {}, tmp_path / "REPORT.md")
    assert "Confirmatory evidence: EXECUTED_INCOMPLETE" in report
    assert "SUPPORTED=1, NOT_SUPPORTED=1, NOT_EXECUTED=7" in report
    assert "**H1 — SUPPORTED**" in report and '"effect":0.25' in report
    assert "**H2 — NOT_SUPPORTED**" in report and '"effect":-0.05' in report
    assert "None has sufficient authenticated confirmatory evidence" not in report


def test_collector_origin_scan_rejects_synthetic_observed_fixture_outside_repository(tmp_path):
    package = tmp_path / "normal-layout" / "results_v5" / "protocol-v5.0.0" / "E4" / "observed-run"
    selected = {
        "schema_version": "protocol-v5-evidence-selection-result-v1.0.0",
        "requirements": [{
            "requirement_id": "resource_efficiency",
            "selected_package": str(package),
            "selected_manifest_sha256": "a" * 64,
        }],
        "global_errors": [],
    }
    inventory = {
        "candidates": [{
            "requirement_id": "resource_efficiency",
            "package_path": str(package),
            "execution_status": "OBSERVED",
            "stage": "confirmatory",
            "validation_status": "PASS",
            "claims_permitted": True,
            "claim_eligibility": "ELIGIBLE_CONFIRMATORY",
            "authentication": {
                "collector_origin": "SYNTHETIC",
                "collector_authentic": False,
                "source_checksums_verified": True,
            },
            "reason_codes": ["SYNTHETIC_RESOURCE_EVIDENCE"],
        }]
    }
    result = synthetic_origin_scan(selected, inventory)
    assert result["status"] == "FAIL"
    assert result["promoted_synthetic_count"] == 1
    assert result["findings"][0]["disposition"] == "FAIL_PROMOTED"
    selected["requirements"][0]["selected_package"] = None
    inventory["candidates"][0].update(claims_permitted=False, claim_eligibility="INELIGIBLE")
    bounded = synthetic_origin_scan(selected, inventory)
    assert bounded["status"] == "PASS"
    assert bounded["findings"][0]["disposition"] == "NON_CLAIMABLE"


def test_e3_lf_regeneration_is_versioned_and_preserves_historical_identity():
    inputs = Inputs()
    historical = "results_v5/protocol-v5.0.0/E3/b0-p2-user-study-readiness"
    regenerated = inputs.lock["e3_readiness_regeneration_package"]
    historical_bytes = inputs.path(historical + "/report/tables/participant-flow.csv").read_bytes()
    manifest = inputs.json(regenerated + "/manifest.json")
    regenerated_bytes = inputs.path(regenerated + "/report/tables/participant-flow.csv").read_bytes()
    assert b"\r\n" not in historical_bytes and b"\r\n" not in regenerated_bytes
    assert regenerated_bytes != historical_bytes
    assert file_sha256(inputs.path(historical + "/report/tables/participant-flow.csv")) == "bee024512c5b6f407a9f1d273d2abbdfe2be640322b05d06f9a641404e8dd73c"
    assert manifest["source"]["historical_manifest_sha256"] == "a142b8c930e1f84d123aca8c39fe564efd52027ab62b637f236db1bc8fa5686b"
    assert manifest["source"]["historical_bytes_modified"] is False
    assert manifest["source"]["regenerated_pre_normalization_sha256"] == "9d76ebf517d544a7913cfd744f67ca478620a4d5c93605baf2746c5873520416"
    assert manifest["source"]["newline_policy_transform"] == "CRLF_TO_LF"
    assert manifest["output_checksums"]["report/tables/participant-flow.csv"] == file_sha256(
        inputs.path(regenerated + "/report/tables/participant-flow.csv")
    )
    assert manifest["execution_status"] == "NOT_EXECUTED"
    assert manifest["claims_permitted"] is False


def test_completion_audit_makes_p3_confirmatory_execution_conditional():
    remaining = _remaining_execution_requirements(
        {
            "p3_state": "NOT_RETAINED_OR_NOT_PRESENT",
            "experiment_states": [
                {"experiment": "E1", "status": "NOT_EXECUTED"},
                {"experiment": "E6", "status": "NOT_EXECUTED"},
            ],
        },
        [{"id": 1, "verdict": "UNVERIFIED"}],
    )
    assert remaining[0].startswith("authoritative freeze")
    assert "E1: authenticated real evidence (NOT_EXECUTED)" in remaining
    assert any("E6: no confirmatory execution is authorized unless" in row for row in remaining)
    assert not any("E6: authenticated real evidence" in row for row in remaining)


def test_legacy_v1_probes_are_not_misclassified_as_unexecuted():
    from evaluation_v5.final_audit.common import read_rows
    source = Inputs().path("results_v5/protocol-v5.0.0/E5/e5-image-validation-20260905T020014Z/raw/probe_results.jsonl")
    records = read_rows(source)
    assert all("execution_status" not in row for row in records)
    from evaluation_v5.image_storage.contracts import ImageProbeResult
    assert sum(ImageProbeResult.from_dict(row).is_executed for row in records) == 13
    assert not execution_findings("OBSERVED", records, kind="image_functional")


def test_valid_not_executed_resource_packages_are_accepted(current_audit):
    p = next(p for p in current_audit["packages"] if p["path"].endswith("e4-resource-efficiency-observed-run-20260905T081825Z"))
    assert p["status"] == "NOT_EXECUTED"
    assert p["validation"] == "PASS"
    assert p["raw_records"] == 0


def test_metadata_mismatch_is_not_erased_by_functional_validation(current_audit):
    check = current_audit["checks"][4]
    assert check["verdict"] == "FAIL"
    assert any(d["field"] == "/extractor/extractor_prompt_sha256" for d in check["details"])


@pytest.mark.parametrize("status,rows,kind,expected", [
    ("OBSERVED", [], "offline", "OBSERVED_WITHOUT_OBSERVATIONS"),
    ("OBSERVED", [{"execution_status": "EXECUTED", "execution_mode": "synthetic"}], "image_functional", "SYNTHETIC_OBSERVED"),
    ("OBSERVED", [{"synthetic_only": True}], "offline", "SYNTHETIC_OBSERVED"),
    ("NOT_EXECUTED", [{"runtime_seconds": 0}], "resource_efficiency", "UNEXECUTED_PACKAGE_CONTAINS_OBSERVATIONS"),
    ("DRY_RUN", [{"execution_status": "EXECUTED"}], "image_functional", "UNEXECUTED_PACKAGE_CONTAINS_OBSERVATIONS"),
])
def test_execution_origin_and_empty_status_fail_closed(status, rows, kind, expected):
    assert expected in execution_findings(status, rows, kind=kind)


def test_empty_not_executed_is_valid_and_counts_are_not_imputed():
    assert execution_findings("NOT_EXECUTED", [], kind="resource_efficiency") == []
    assert placeholder_findings({"status": "NOT_EXECUTED", "estimate": None, "observed_count": 0}) == []
    assert placeholder_findings({"status": "NOT_EXECUTED", "estimate": 0})
    assert placeholder_findings({"status": "NOT_EXECUTED", "claims_permitted": True})


@pytest.mark.parametrize("value", [
    {"system_id": "B0", "mrr": None},
    {"B0": {"metrics": {"nDCG@5": .8}}},
    [{"system": "B0", "metric": "Hit@3", "estimate": 1}],
])
def test_b0_ranking_metrics_rejected_even_in_nested_tables(value):
    assert b0_ranking_findings(value)


def test_b0_selection_correctness_is_not_a_ranking_metric():
    assert not b0_ranking_findings({"system_id": "B0", "selection_success_rate": .5})


@pytest.mark.parametrize("unit", [None, "call", "repetition", "surface_form_variant", "case"])
def test_p_values_cannot_use_repeated_or_undeclared_units(unit):
    assert inference_findings({"independent_unit": unit, "p_value": .01}, experiment="E1")


def test_family_inference_and_human_pairing_are_distinct():
    assert not inference_findings({"independent_unit": "workload_family", "test": {"p_value": .2}}, experiment="E1")
    assert not inference_findings({"independent_unit": "participant", "test": {"p_value": .2}}, experiment="E3")
    assert inference_findings({"independent_unit": "family", "p_value": .01, "effective_family_n": 100}, experiment="E1", family_count=10)


def test_no_p3_primary_claim_without_retained_gate():
    assert p3_findings({"primary_system": "P3"}, False)
    assert p3_findings({"claim_id": "H8", "claim_status": "SUPPORTED"}, False)
    assert not p3_findings({"primary_system": "P2", "P3": "not_retained"}, False)


def test_environment_and_image_platform_are_required_for_observed_measurements():
    assert cluster_findings("OBSERVED", {"platform": "macOS-arm64"})
    assert not cluster_findings("NOT_EXECUTED", {})
    digest = "sha256:" + hashlib.sha256(b"synthetic-image-identity").hexdigest()
    image = {"image_digest": digest, "image_reference": "example.invalid/image@" + digest,
             "platform": {"os": "linux", "architecture": "arm64"}}
    assert storage_identity_findings([image]) == []
    assert storage_identity_findings([{**image, "platform": "macOS-arm64"}])
    assert storage_identity_findings([{**image, "image_reference": "example.invalid/image:latest"}])


def test_privacy_validator_rejects_identifiers_without_echoing_values(tmp_path):
    from evaluation_v5.user_study.analysis import audit_report_privacy, UserStudyAnalysisError
    p = tmp_path / "participant.json"
    address = "synthetic.person@example.invalid"
    write_json(p, {"email": address})
    with pytest.raises(UserStudyAnalysisError) as error:
        audit_report_privacy([p])
    assert address not in str(error.value)


def test_portable_relocation_requires_explicit_map_and_original_hash(tmp_path):
    root = tmp_path.resolve()
    write_bytes(root / "raw.json", b"{}\n")
    digest = file_sha256(root / "raw.json")
    legacy = "/old/checkout/raw.json"
    write_json(root / LOCK, {"schema_version": "protocol-v5-final-audit-inputs-v1.0.0", "files": {"raw.json": digest},
                            "legacy_reference_map": {hashlib.sha256(legacy.encode()).hexdigest(): "raw.json"}})
    inputs = Inputs(root)
    assert inputs.resolve(legacy, digest) == root / "raw.json"
    with pytest.raises(ValueError):
        inputs.resolve("/different/checkout/raw.json", digest)
    with pytest.raises(ValueError):
        inputs.resolve(legacy, "0" * 64)
    (root / "raw.json").write_text("changed")
    assert inputs.verify()


def test_exclusive_publication_and_sealed_output_tampering(tmp_path):
    write_json(tmp_path / "derived.json", {"estimate": None})
    with pytest.raises(FileExistsError):
        write_json(tmp_path / "derived.json", {"estimate": 0})
    seal(tmp_path, {"status": "NOT_EXECUTED"})
    verify_seal(tmp_path)
    (tmp_path / "derived.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        verify_seal(tmp_path)


def test_input_paths_cannot_escape_or_follow_symlinks(tmp_path):
    with pytest.raises(ValueError):
        safe_path(tmp_path, "../sealed.yaml")
    (tmp_path / "link").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError):
        safe_path(tmp_path, "link")


def test_regeneration_compares_real_values_not_just_provenance():
    assert compare_json({"estimate": 1}, {"estimate": 2})["status"] == "FAIL"
    assert compare_json({"status": "NOT_EXECUTED", "git_revision": "old"},
                        {"status": "NOT_EXECUTED", "git_revision": "new"},
                        ignored_fields=("git_revision",))["status"] == "PASS"


def test_current_raw_analysis_and_figures_reproduce_without_collectors(tmp_path, current_audit, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("reproduction attempted collection/network access")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    from recommender.p2_backend import P2Recommender
    from recommender.p3_backend import P3Recommender
    monkeypatch.setattr(P2Recommender, "recommend", forbidden)
    monkeypatch.setattr(P3Recommender, "recommend", forbidden)
    inputs = Inputs()
    before = {p: file_sha256(inputs.root / p) for p in inputs.files}
    a = analyze(inputs, current_audit, tmp_path / "analysis")
    assert a["status"] == "PASS_WITH_UNAVAILABLE_ANALYSES"
    assert a["observed_functional"] == []
    assert len(a["legacy_functional"]) == 9
    assert all(not row["claim_eligible"] for row in a["legacy_functional"])
    assert a["observed_offline_counts"][0]["families"] == 10
    assert all(c["status"] == "PASS" for c in a["comparisons"])
    first = figures(inputs, a, tmp_path / "analysis", tmp_path / "figures1")
    figures(inputs, a, tmp_path / "analysis", tmp_path / "figures2")
    failures = [c for c in first["comparisons"] if c["status"] == "FAIL"]
    assert failures == [] and first["status"] == "PASS"
    participant_flow = next(
        row for row in first["comparisons"] if row["artifact"].endswith("participant-flow.csv")
    )
    assert participant_flow["baseline_artifact"].startswith(
        inputs.lock["e3_readiness_regeneration_package"]
    )
    assert participant_flow["preserved_original_sha256"] == "bee024512c5b6f407a9f1d273d2abbdfe2be640322b05d06f9a641404e8dd73c"
    assert not (tmp_path / "figures1/functional-development.svg").exists()
    assert file_sha256(tmp_path / "figures1/tables/functional-results.json") == file_sha256(tmp_path / "figures2/tables/functional-results.json")
    assert all(file_sha256(inputs.root / p) == digest for p, digest in before.items())
    report = render_report(inputs, current_audit, a, first, tmp_path / "REPORT.md")
    assert "NOT EXECUTED" in report and "Threats to validity" in report
    assert "Current v1.4 functional observations" in report and "SHA-256" in report
    assert "MISSING_RECOMMENDATION_RECORD_JOIN" in report
    assert "participant-flow CSV" in report


def test_clean_relocated_checkout_runs_full_audit_with_only_portable_core(tmp_path):
    """A subprocess is essential: module ROOT constants must resolve in the clone."""
    root = tmp_path / "checkout"
    root.mkdir()
    inputs = Inputs()
    candidates = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    names = {n.decode() for n in candidates.split(b"\0") if n} | set(inputs.files)
    for relative in sorted(names):
        source = ROOT / relative
        if source.is_file() and not source.is_symlink() and not relative.startswith(".codex"):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Audit Test", "-c", "user.email=audit@example.invalid",
                    "commit", "-qm", "Synthetic portability checkout"], check=True)
    assert subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"]) == b""
    # Force an actual checkout of committed blobs to exercise .gitattributes, too.
    subprocess.run(["git", "-C", str(root), "checkout-index", "-a", "-f"], check=True)
    process = subprocess.run([sys.executable, "-m", "evaluation_v5.final_audit", "audit", "--run-id", "portable-test"],
                             cwd=root, capture_output=True, text=True, timeout=120)
    assert process.returncode == 2, process.stdout + process.stderr
    result = json.loads(process.stdout)
    assert result["audit_status"] == "FAIL"
    package = root / "results_v5/protocol-v5.0.0/final-audit/portable-test"
    audit = read_json(package / "report/audit.json")
    assert audit["checks"][8]["verdict"] == "PASS"  # historical portable core
    assert audit["checks"][6]["verdict"] == "UNVERIFIED"  # legacy analyses remain bounded
    assert all(c["status"] == "PASS" for c in read_json(package / "analysis/regeneration.json")["comparisons"])
    assert all(p["validation"] == "PASS" for p in audit["packages"] if p["kind"] == "research_analysis")
    assert not Inputs(root).verify()
    assert subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"]) == b""
    assert (package / "report/PROTOCOL_V5_FINAL_REPORT.md").is_file()
    for phase in ("validation", "analysis", "figures", "report"):
        verify_seal(package / phase)
    # Integrity failure must still produce a reviewable report, without parsing
    # or interpreting the changed file as a new dataset/configuration.
    (root / "results_v5/protocol-v5.0.0/freezes/frozen-configuration.json").write_text("invalid changed input")
    broken = subprocess.run([sys.executable, "-m", "evaluation_v5.final_audit", "audit", "--run-id", "corrupt-input-test"],
                            cwd=root, capture_output=True, text=True, timeout=120)
    assert broken.returncode == 2
    bad_package = root / "results_v5/protocol-v5.0.0/final-audit/corrupt-input-test"
    result = read_json(bad_package / "report/audit.json")
    assert result["input_integrity_blocked"] is True and not result["claims"]
    assert (bad_package / "report/PROTOCOL_V5_FINAL_REPORT.md").is_file()
