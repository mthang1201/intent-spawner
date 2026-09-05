from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from evaluation_v5.analysis.research_analysis import (
    EXIT_FAILED,
    EXIT_INCOMPLETE,
    EvidenceCandidate,
    _h2_metrics,
    _h6_metrics,
    check_provenance,
    evaluate_claims,
    generate_threats,
    run_research_analysis,
    select_evidence,
    validate_research_analysis_package,
)
from evaluation_v5.analysis.research_contracts import (
    EXPECTED_CLAIMS,
    REGISTRY_PATH,
    ResearchContractError,
    file_sha256,
    load_claim_registry,
    validate_storage_evidence,
)


ROOT = Path(__file__).resolve().parents[1]
FREEZE_PATH = ROOT / "results_v5" / "protocol-v5.0.0" / "freezes" / "frozen-configuration.json"


def _candidate(
    tmp_path: Path,
    requirement: str,
    claim_id: str,
    metrics: dict,
    *,
    schema: str | None = None,
    evidence_class: str = "E1",
    stage: str = "confirmatory",
    validation: str = "PASS",
) -> EvidenceCandidate:
    package = tmp_path / f"{requirement}-package"
    package.mkdir(parents=True, exist_ok=True)
    manifest = package / "manifest.json"
    if not manifest.exists():
        manifest.write_text("{}\n", encoding="utf-8")
    schemas = {
        "offline_recommendation": "protocol-v5-statistical-analysis-v1.0.0",
        "natural_language_robustness": "protocol-v5-statistical-analysis-v1.0.0",
        "user_study": "protocol-v5-user-study-provenance-v1.0.0",
        "resource_efficiency": "protocol-v5-resource-efficiency-analysis-package-v1.0.0",
        "image_functional": "protocol-v5-manifest-v1.0.0",
        "image_storage": "protocol-v5-image-storage-evidence-v1.0.0",
        "p2_p3": "protocol-v5-statistical-analysis-v1.0.0",
    }
    return EvidenceCandidate(
        requirement_id=requirement,
        evidence_class=evidence_class,
        experiment_id=evidence_class,
        package_path=package,
        manifest_path=manifest,
        manifest_sha256=file_sha256(manifest),
        schema_version=schema or schemas[requirement],
        stage=stage,
        execution_status="DERIVED_EVIDENCE_COMPLETE",
        validation_status=validation,
        claims_permitted=stage == "confirmatory" and validation == "PASS",
        claim_eligibility=(
            "ELIGIBLE_CONFIRMATORY"
            if stage == "confirmatory" and validation == "PASS"
            else "INELIGIBLE"
        ),
        metrics={claim_id: metrics},
        artifacts=[{"path": str(manifest.resolve()), "package_relative_path": "manifest.json", "sha256": file_sha256(manifest)}],
    )


def _selection_report(registry: dict, selected: dict[str, EvidenceCandidate]) -> dict:
    return {
        "schema_version": "protocol-v5-evidence-selection-result-v1.0.0",
        "global_errors": [],
        "requirements": [
            {
                "requirement_id": row["id"],
                "selection_mode": "AUTO_SINGLE_ELIGIBLE" if row["id"] in selected else "NONE",
                "selected_package": str(selected[row["id"]].package_path) if row["id"] in selected else None,
                "reason_codes": [] if row["id"] in selected else ["NO_ELIGIBLE_CONFIRMATORY_EVIDENCE"],
            }
            for row in registry["evidence_requirements"]
        ],
    }


def _evaluate_one(registry: dict, candidate: EvidenceCandidate) -> dict:
    selected = {candidate.requirement_id: candidate}
    rows = evaluate_claims(
        registry=registry,
        selected=selected,
        selection_report=_selection_report(registry, selected),
    )
    return next(row for row in rows if row["claim_id"] in candidate.metrics)


def test_registry_is_complete_and_b0_has_no_ranking_metric(tmp_path: Path):
    registry = load_claim_registry()
    assert {row["id"] for row in registry["claims"]} == EXPECTED_CLAIMS
    assert len(registry["claims"]) == 9
    for claim in registry["claims"]:
        assert claim["required_evidence"]
        assert claim["statistical_tests"]
        for metric in claim["metrics"]:
            if "B0" in metric["systems"]:
                assert not any(token in metric["id"].lower() for token in ("mrr", "ndcg", "hit_at", "hit@"))
    invalid = json.loads(json.dumps(registry))
    invalid["claims"][2]["metrics"][0]["id"] = "mrr_at_10"
    invalid_path = tmp_path / "invalid-registry.yaml"
    invalid_path.write_text(yaml.safe_dump(invalid, sort_keys=False), encoding="utf-8")
    with pytest.raises(ResearchContractError, match="B0 does not produce ranking metrics"):
        load_claim_registry(invalid_path)


def test_selection_zero_one_many_and_stale_lock(tmp_path: Path):
    registry = load_claim_registry()
    good = _candidate(tmp_path, "offline_recommendation", "H1", {})
    selected, report, fatal = select_evidence([], registry, repository_root=ROOT)
    assert not selected and not fatal
    assert next(row for row in report["requirements"] if row["requirement_id"] == "offline_recommendation")["selection_mode"] == "NONE"

    selected, report, fatal = select_evidence([good], registry, repository_root=ROOT)
    assert selected["offline_recommendation"] is good
    assert not fatal

    second = _candidate(tmp_path / "other", "offline_recommendation", "H1", {})
    selected, report, fatal = select_evidence([good, second], registry, repository_root=ROOT)
    assert "offline_recommendation" not in selected
    assert next(row for row in report["requirements"] if row["requirement_id"] == "offline_recommendation")["selection_mode"] == "AMBIGUOUS"

    lock = {
        "selections": {
            "offline_recommendation": {
                "package_path": str(good.package_path),
                "manifest_sha256": "0" * 64,
            }
        }
    }
    selected, _, fatal = select_evidence([good, second], registry, selection=lock, repository_root=ROOT)
    assert not selected
    assert fatal == {"offline_recommendation"}


def test_h2_uses_family_losses_and_not_variants_as_samples():
    rows = []
    for index in range(10):
        common = {
            "family_id": f"family-{index}",
            "endpoint_variant_denominators": {"joint_accept_at_1": 2, "robustness_rate": 1},
        }
        rows.append({
            **common,
            "system_id": "P1",
            "endpoint_variant_sums": {"joint_accept_at_1": 1.0, "robustness_rate": 0.0},
        })
        rows.append({
            **common,
            "system_id": "P2",
            "endpoint_variant_sums": {"joint_accept_at_1": 2.0, "robustness_rate": 1.0},
        })
    result = _h2_metrics(rows)
    assert result["effective_family_n"] == 10
    assert result["effect"] == pytest.approx(-1.0)
    assert result["ci_high"] < 0
    assert result["test_available"] is True


def test_h6_requires_both_family_level_request_axes():
    trials = []
    for index in range(10):
        for condition, value in (("P2_CATALOG", 2.0), ("P2_DYNAMIC", 1.0)):
            trials.append({
                "family_id": f"family-{index}",
                "condition": condition,
                "valid_attempt": True,
                "cpu_request_allocation_error_absolute": value,
                "memory_request_allocation_error_absolute": value,
            })
    result = _h6_metrics(trials)
    assert result["cpu_effect"] == pytest.approx(-1.0)
    assert result["memory_effect"] == pytest.approx(-1.0)
    assert result["cpu_p_holm"] < 0.05
    assert result["memory_p_holm"] < 0.05
    assert result["tests_available"] is True


@pytest.mark.parametrize(
    ("claim_id", "requirement", "metrics", "flip"),
    [
        ("H1", "offline_recommendation", {"effect": .2, "ci_low": .1, "p_value": .01, "test_available": True}, ("effect", -.2)),
        ("H2", "natural_language_robustness", {"effect": -.2, "ci_high": -.1, "p_value": .01, "test_available": True}, ("effect", .2)),
        ("H3", "user_study", {"selection_effect": .1, "selection_ci_low": .01, "selection_p_holm": .02, "time_effect": -10, "time_ci_high": -1, "time_p_holm": .03, "tests_available": True}, ("time_ci_high", 1)),
        ("H4", "user_study", {"seq_effect": 1, "seq_ci_low": .1, "sus_effect": 5, "sus_ci_low": 1, "estimates_available": True}, ("sus_ci_low", -1)),
        ("H5", "resource_efficiency", {"pareto_classification": "STRICT_FRONTIER_IMPROVEMENT", "cpu_effect": -2, "cpu_ci_high": -1, "cpu_p_holm": .01, "memory_effect": -2, "memory_ci_high": -1, "memory_p_holm": .02, "tests_available": True}, ("pareto_classification", "EFFICIENCY_RELIABILITY_TRADEOFF")),
        ("H6", "resource_efficiency", {"cpu_effect": -2, "cpu_ci_high": -1, "cpu_p_holm": .01, "memory_effect": -2, "memory_ci_high": -1, "memory_p_holm": .02, "tests_available": True}, ("memory_ci_high", 1)),
        ("H7F", "image_functional", {"conservative_success": 1.0, "operational_adequacy": 1.0, "required_probe_not_defined_count": 0, "execution_unavailable_count": 0, "failed_probe_count": 0, "all_digests_immutable": True}, ("failed_probe_count", 1)),
        ("H7", "image_storage", {"all_prefixes_nonexpanding": True, "final_savings_bytes": 10, "prefix_order_valid": True}, ("final_savings_bytes", 0)),
        ("H8", "p2_p3", {"gate_retained": True, "threshold_frozen": True, "quality_effect": .1, "quality_ci_low": .01, "quality_p_value": .02, "all_overhead_ci_within_limits": True, "tests_available": True}, ("all_overhead_ci_within_limits", False)),
    ],
)
def test_claim_outcomes_are_data_driven(tmp_path: Path, claim_id: str, requirement: str, metrics: dict, flip: tuple):
    registry = load_claim_registry()
    candidate = _candidate(tmp_path, requirement, claim_id, dict(metrics))
    assert _evaluate_one(registry, candidate)["claim_status"] == "SUPPORTED"
    candidate.metrics[claim_id][flip[0]] = flip[1]
    assert _evaluate_one(registry, candidate)["claim_status"] == "NOT_SUPPORTED"
    candidate.metrics[claim_id].pop(next(iter(candidate.metrics[claim_id])))
    assert _evaluate_one(registry, candidate)["claim_status"] == "NOT_EXECUTED"


def test_development_or_failed_validation_never_decides_claim(tmp_path: Path):
    registry = load_claim_registry()
    metrics = {"effect": .2, "ci_low": .1, "p_value": .01, "test_available": True}
    candidate = _candidate(tmp_path, "offline_recommendation", "H1", metrics, stage="development")
    selected, report, _ = select_evidence([candidate], registry, repository_root=ROOT)
    result = evaluate_claims(registry=registry, selected=selected, selection_report=report)
    assert next(row for row in result if row["claim_id"] == "H1")["claim_status"] == "NOT_EXECUTED"


def test_semantic_provenance_blocks_but_git_difference_only_discloses(tmp_path: Path):
    registry = load_claim_registry()
    freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    storage = _candidate(tmp_path, "image_storage", "H7", {})
    storage.semantic_provenance = {
        "catalog.version": freeze["candidate_catalog"]["version"],
        "catalog.file_sha256": freeze["candidate_catalog"]["file_sha256"],
        "p2.pipeline_version": freeze["systems"]["P2"]["pipeline_version"],
    }
    storage.provenance = {"git_revision": "revision-a", "environment": {"id": "one"}}
    report, blocked = check_provenance({"image_storage": storage}, registry, freeze)
    assert not blocked and report["semantic_status"] == "PASS"
    storage.semantic_provenance["catalog.file_sha256"] = "f" * 64
    report, blocked = check_provenance({"image_storage": storage}, registry, freeze)
    assert blocked == {"image_storage"}
    assert any(row["digest_namespace"] == "catalog_file_bytes" for row in report["semantic_comparisons"])


def test_threats_are_metadata_triggered_and_cover_required_categories(tmp_path: Path):
    registry = load_claim_registry()
    e3 = _candidate(tmp_path, "user_study", "H3", {}, evidence_class="E3")
    e3.metadata = {
        "participant_target": 36,
        "completed_participants": 35,
        "design_diagnostics": {"condition_order_counts": {"B0_FIRST": 18, "P2_FIRST": 17}, "period_counts": {"1": 105, "2": 105}},
        "missingness": [{"outcome": "decision_time_seconds", "missing_count": 1, "denominator": 210}],
    }
    e4 = _candidate(tmp_path, "resource_efficiency", "H5", {}, evidence_class="E4")
    e4.metadata = {"success_noninferiority_margin": None, "cluster_identity": {"name": "cluster-a"}}
    e5 = _candidate(tmp_path, "image_functional", "H7F", {}, evidence_class="E5")
    e5.metadata = {"environment": {"runtime": "docker", "architecture": "arm64"}}
    e5.provenance = {"git_dirty": True}
    development = _candidate(
        tmp_path / "development",
        "offline_recommendation",
        "H1",
        {},
        stage="development",
    )
    selected = {row.requirement_id: row for row in (e3, e4, e5)}
    selection = _selection_report(registry, selected)
    threats = generate_threats(
        registry=registry,
        candidates=[development, e3, e4, e5],
        selected=selected,
        selection_report=selection,
        provenance_report={"disclosures": []},
    )
    categories = {row["category"] for row in threats["threats"]}
    assert {
        "construct", "internal", "external", "statistical_conclusion",
        "benchmark_contamination", "human_study_learning_order",
        "single_cluster_generalization", "image_platform_dependence",
    } <= categories
    assert all(row["source_artifact"] and row["source_pointer"] for row in threats["threats"])


def test_storage_contract_enforces_order_but_not_the_claim_result():
    evidence = {
        "schema_version": "protocol-v5-image-storage-evidence-v1.0.0",
        "protocol_version": "5.0.0",
        "experiment_id": "E5",
        "execution_status": "OBSERVED",
        "split_stage": "confirmatory",
        "claims_permitted": True,
        "measured_at_utc": "2026-09-05T00:00:00Z",
        "catalog": {"version": "v", "file_sha256": "a" * 64, "ordered_image_digests": ["sha256:" + "b" * 64, "sha256:" + "c" * 64]},
        "platform": {"environment_id": "env", "runtime": "containerd", "operating_system": "linux", "architecture": "amd64"},
        "measurement_method": "content-store layer digest accounting",
        "prefixes": [
            {"prefix_size": 1, "image_digests": ["sha256:" + "b" * 64], "naive_logical_bytes": 10, "unique_layer_bytes": 12},
            {"prefix_size": 2, "image_digests": ["sha256:" + "b" * 64, "sha256:" + "c" * 64], "naive_logical_bytes": 20, "unique_layer_bytes": 18},
        ],
        "provenance": {"git_revision": "abc", "dataset_sha256": "d" * 64, "backend_system_versions": {"P2": "p2-pipeline-v1.0.0"}},
    }
    validate_storage_evidence(evidence)
    evidence["prefixes"][1]["image_digests"].reverse()
    with pytest.raises(ResearchContractError):
        validate_storage_evidence(evidence)


def test_empty_tree_writes_valid_incomplete_package_and_never_overwrites(tmp_path: Path):
    results = tmp_path / "results"
    results.mkdir()
    output = tmp_path / "analysis"
    package, status, exit_code = run_research_analysis(
        results_root=results,
        output_root=output,
        run_id="empty",
        freeze_path=FREEZE_PATH,
    )
    assert status == "INCOMPLETE" and exit_code == EXIT_INCOMPLETE
    assert validate_research_analysis_package(package)["status"] == "PASS"
    claims = json.loads((package / "derived" / "evaluated-claim-registry.json").read_text())["claims"]
    assert all(row["claim_status"] == "NOT_EXECUTED" for row in claims)
    with pytest.raises(FileExistsError):
        run_research_analysis(
            results_root=results,
            output_root=output,
            run_id="empty",
            freeze_path=FREEZE_PATH,
        )


def test_invalid_selection_writes_failed_nonclaimable_audit_package(tmp_path: Path):
    results = tmp_path / "results"
    results.mkdir()
    selection = tmp_path / "selection.yaml"
    selection.write_text(
        yaml.safe_dump(
            {
                "schema_version": "protocol-v5-evidence-selection-v1.0.0",
                "protocol_version": "5.0.0",
                "registry_sha256": file_sha256(REGISTRY_PATH),
                "selections": {
                    "offline_recommendation": {
                        "package_path": "does/not/exist",
                        "manifest_sha256": "a" * 64,
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    package, status, exit_code = run_research_analysis(
        results_root=results,
        output_root=tmp_path / "analysis",
        run_id="failed",
        freeze_path=FREEZE_PATH,
        selection_path=selection,
    )
    assert status == "FAILED" and exit_code == EXIT_FAILED
    assert validate_research_analysis_package(package)["package_status"] == "FAILED"
    claims = json.loads((package / "derived" / "evaluated-claim-registry.json").read_text())["claims"]
    assert not any(row["claimable"] for row in claims)
