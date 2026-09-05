from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from evaluation_v5.analysis import research_analysis as research_module
from evaluation_v5.analysis.research_analysis import (
    EXIT_FAILED,
    EXIT_INCOMPLETE,
    EXIT_SUCCESS,
    EvidenceCandidate,
    _adapt_image_storage,
    _h1_metrics,
    _h2_metrics,
    _h5_metrics,
    _h6_metrics,
    _package_status,
    _validate_e3_claim_contract,
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
    json_pointer_get,
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
        manifest.write_text(json.dumps({"claims": {claim_id: metrics}}, sort_keys=True) + "\n", encoding="utf-8")
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
        metric_lineage={
            claim_id: {
                field: [
                    {
                        "requirement_id": requirement,
                        "source_artifact": str(manifest.resolve()),
                        "artifact_sha256": file_sha256(manifest),
                        "evidence_schema_version": schema or schemas[requirement],
                        "locator": {
                            "format": "json",
                            "json_pointers": [f"/claims/{claim_id}/{field}"],
                            "matched_record_count": 1,
                        },
                        "transformation": "Synthetic test fixture direct normalization.",
                    }
                ]
                for field in metrics
            }
        },
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


def _attach_claim_metrics(candidate: EvidenceCandidate, claim_id: str, metrics: dict) -> None:
    document = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    document.setdefault("claims", {})[claim_id] = metrics
    candidate.manifest_path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    sha = file_sha256(candidate.manifest_path)
    candidate.manifest_sha256 = sha
    candidate.artifacts = [
        {"path": str(candidate.manifest_path.resolve()), "package_relative_path": "manifest.json", "sha256": sha}
    ]
    for sources in candidate.metric_lineage.values():
        for entries in sources.values():
            for entry in entries:
                entry["artifact_sha256"] = sha
    candidate.metrics[claim_id] = metrics
    candidate.metric_lineage[claim_id] = {
        field: [
            {
                "requirement_id": candidate.requirement_id,
                "source_artifact": str(candidate.manifest_path.resolve()),
                "artifact_sha256": sha,
                "evidence_schema_version": candidate.schema_version,
                "locator": {
                    "format": "json",
                    "json_pointers": [f"/claims/{claim_id}/{field}"],
                    "matched_record_count": 1,
                },
                "transformation": "Synthetic test fixture direct normalization.",
            }
        ]
        for field in metrics
    }


def _evaluate_one(registry: dict, candidate: EvidenceCandidate) -> dict:
    selected = {candidate.requirement_id: candidate}
    rows = evaluate_claims(
        registry=registry,
        selected=selected,
        selection_report=_selection_report(registry, selected),
    )
    return next(row for row in rows if row["claim_id"] in candidate.metrics)


def _freeze_semantics(registry: dict, freeze: dict, requirement: str) -> dict:
    definition = next(row for row in registry["evidence_requirements"] if row["id"] == requirement)
    return {
        field["key"]: json_pointer_get(freeze, field["freeze_pointer"])
        for field in definition["semantic_provenance"]
    }


def _complete_mandatory_candidates(tmp_path: Path) -> list[EvidenceCandidate]:
    candidates = [
        _candidate(tmp_path / "h1", "offline_recommendation", "H1", {
            "effect": .2, "ci_low": .1, "p_value": .01, "test_available": True,
        }),
        _candidate(tmp_path / "h2", "natural_language_robustness", "H2", {
            "effect": -.2, "ci_high": -.1, "p_value": .01, "test_available": True,
        }),
        _candidate(tmp_path / "e3", "user_study", "H3", {
            "selection_effect": .1, "selection_ci_low": .01, "selection_p_holm": .02,
            "time_effect": -10, "time_ci_high": -1, "time_p_holm": .03,
            "tests_available": True,
        }, evidence_class="E3"),
        _candidate(tmp_path / "e4", "resource_efficiency", "H5", {
            "pareto_classification": "STRICT_FRONTIER_IMPROVEMENT",
            "pareto_report_consistent": True, "reliability_preserved": True,
            "cpu_effect": -2, "cpu_ci_high": -1, "cpu_p_holm": .01,
            "memory_effect": -2, "memory_ci_high": -1, "memory_p_holm": .02,
            "tests_available": True,
        }, evidence_class="E4"),
        _candidate(tmp_path / "h7f", "image_functional", "H7F", {
            "conservative_success": 1.0, "operational_adequacy": 1.0,
            "required_probe_not_defined_count": 0, "execution_unavailable_count": 0,
            "failed_probe_count": 0, "all_digests_immutable": True,
        }, evidence_class="E5"),
        _candidate(tmp_path / "h7", "image_storage", "H7", {
            "catalog_prefix_count": 3, "all_prefixes_nonexpanding": True,
            "final_savings_bytes": 10, "expansion_naive_bytes": 20,
            "expansion_growth_difference": -5,
            "strictly_slower_catalog_expansion": True, "prefix_order_valid": True,
        }, evidence_class="E5"),
    ]
    _attach_claim_metrics(candidates[2], "H4", {
        "seq_effect": 1, "seq_ci_low": .1, "sus_effect": 5,
        "sus_ci_low": 1, "estimates_available": True,
    })
    _attach_claim_metrics(candidates[3], "H6", {
        "oracle_independence_verified": True,
        "cpu_effect": -2, "cpu_ci_high": -1, "cpu_p_holm": .01,
        "memory_effect": -2, "memory_ci_high": -1, "memory_p_holm": .02,
        "tests_available": True,
    })
    return candidates


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


def test_registry_rejects_convenient_metric_or_statistical_unit_substitution(tmp_path: Path):
    registry = load_claim_registry()
    invalid = json.loads(json.dumps(registry))
    h1 = next(row for row in invalid["claims"] if row["id"] == "H1")
    h1["metrics"][0]["source_endpoints"] = ["hit_at_3"]
    path = tmp_path / "substituted-endpoint.yaml"
    path.write_text(yaml.safe_dump(invalid, sort_keys=False), encoding="utf-8")
    with pytest.raises(ResearchContractError, match="source endpoints differ"):
        load_claim_registry(path)

    invalid = json.loads(json.dumps(registry))
    h2 = next(row for row in invalid["claims"] if row["id"] == "H2")
    h2["statistical_tests"][0]["independent_unit"] = "surface_form_variant"
    path = tmp_path / "inflated-unit.yaml"
    path.write_text(yaml.safe_dump(invalid, sort_keys=False), encoding="utf-8")
    with pytest.raises(ResearchContractError, match="independent statistical unit"):
        load_claim_registry(path)


def test_h1_adapter_does_not_substitute_other_ranking_metrics():
    rows = [{
        "comparison_id": "P2_minus_P1",
        "endpoint": "hit_at_3",
        "effect_sizes": {"mean_difference": .5},
        "effect_ci_low": .2,
        "effect_ci_high": .8,
        "p_value_raw": .001,
        "hypothesis_status": "TESTED",
        "statistical_decision": "REJECT_NULL",
    }]
    result = _h1_metrics(rows)
    assert result["effect"] is None
    assert result["p_value"] is None
    assert result["test_available"] is False


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


def test_conflicting_observed_evidence_is_order_independent_and_fatal_without_lock(tmp_path: Path):
    registry = load_claim_registry()
    positive = _candidate(tmp_path / "positive", "offline_recommendation", "H1", {
        "effect": .2, "ci_low": .1, "p_value": .01, "test_available": True,
    })
    negative = _candidate(tmp_path / "negative", "offline_recommendation", "H1", {
        "effect": -.2, "ci_low": -.3, "p_value": .8, "test_available": True,
    })
    for ordering in ([positive, negative], [negative, positive]):
        selected, report, fatal = select_evidence(ordering, registry, repository_root=ROOT)
        row = next(item for item in report["requirements"] if item["requirement_id"] == "offline_recommendation")
        assert "offline_recommendation" not in selected
        assert fatal == {"offline_recommendation"}
        assert row["selection_mode"] == "AMBIGUOUS"
        assert row["conflict_status"] == "CONFLICTING"
        assert {item["disposition"] for item in row["candidate_records"]} == {
            "UNRESOLVED_ELIGIBLE_CANDIDATE"
        }

    lock = {
        "selections": {
            "offline_recommendation": {
                "package_path": str(negative.package_path),
                "manifest_sha256": negative.manifest_sha256,
            }
        }
    }
    selected, report, fatal = select_evidence(
        [positive, negative], registry, selection=lock, repository_root=ROOT
    )
    row = next(item for item in report["requirements"] if item["requirement_id"] == "offline_recommendation")
    assert selected["offline_recommendation"] is negative and not fatal
    assert row["conflict_status"] == "CONFLICTING"
    assert {item["disposition"] for item in row["candidate_records"]} == {
        "SELECTED", "NOT_SELECTED_BY_EXPLICIT_LOCK"
    }
    assert row["selected_manifest_sha256"] == negative.manifest_sha256


def test_duplicate_evidence_never_uses_filesystem_order(tmp_path: Path):
    registry = load_claim_registry()
    first = _candidate(tmp_path / "first", "offline_recommendation", "H1", {
        "effect": .2, "ci_low": .1, "p_value": .01, "test_available": True,
    })
    exact_duplicate_reference = replace(first)
    selected, report, fatal = select_evidence(
        [first, exact_duplicate_reference], registry, repository_root=ROOT
    )
    row = next(item for item in report["requirements"] if item["requirement_id"] == "offline_recommendation")
    assert selected["offline_recommendation"] is first and not fatal
    assert row["unique_candidate_count"] == 1
    assert [item["disposition"] for item in row["candidate_records"]].count("DUPLICATE_REFERENCE") == 1

    second = _candidate(tmp_path / "second", "offline_recommendation", "H1", dict(first.metrics["H1"]))
    assert first.manifest_sha256 == second.manifest_sha256
    selected, report, fatal = select_evidence([second, first], registry, repository_root=ROOT)
    row = next(item for item in report["requirements"] if item["requirement_id"] == "offline_recommendation")
    assert not selected and not fatal
    assert row["conflict_status"] == "EQUIVALENT_DUPLICATES"
    assert row["selection_mode"] == "AMBIGUOUS"


def test_selection_rechecks_registered_and_artifact_checksums(tmp_path: Path):
    registry = load_claim_registry()
    candidate = _candidate(tmp_path, "offline_recommendation", "H1", {
        "effect": .2, "ci_low": .1, "p_value": .01, "test_available": True,
    })
    registered = candidate.manifest_sha256
    candidate.manifest_path.write_text('{"mutated":true}\n', encoding="utf-8")
    lock = {
        "selections": {
            "offline_recommendation": {
                "package_path": str(candidate.package_path),
                "manifest_sha256": registered,
            }
        }
    }
    selected, report, fatal = select_evidence(
        [candidate], registry, selection=lock, repository_root=ROOT
    )
    row = next(item for item in report["requirements"] if item["requirement_id"] == "offline_recommendation")
    assert not selected and fatal == {"offline_recommendation"}
    assert "SELECTION_CHECKSUM_MISMATCH" in row["reason_codes"]
    assert row["candidate_records"][0]["actual_manifest_sha256"] != registered
    assert row["candidate_records"][0]["integrity_errors"]

    other = _candidate(tmp_path / "artifact", "offline_recommendation", "H1", {
        "effect": .2, "ci_low": .1, "p_value": .01, "test_available": True,
    })
    observation = other.package_path / "observation.json"
    observation.write_text('{"value":1}\n', encoding="utf-8")
    other.artifacts.append({
        "path": str(observation.resolve()),
        "package_relative_path": "observation.json",
        "sha256": file_sha256(observation),
    })
    observation.write_text('{"value":2}\n', encoding="utf-8")
    selected, report, fatal = select_evidence([other], registry, repository_root=ROOT)
    row = next(item for item in report["requirements"] if item["requirement_id"] == "offline_recommendation")
    assert not selected and fatal == {"offline_recommendation"}
    assert any(code.startswith("ARTIFACT_CHECKSUM_MISMATCH") for code in row["reason_codes"])


def test_observed_confirmatory_evidence_with_failed_validator_is_fatal(tmp_path: Path):
    registry = load_claim_registry()
    candidate = _candidate(
        tmp_path, "offline_recommendation", "H1", {}, validation="FAIL"
    )
    candidate.execution_status = "OBSERVED"
    candidate.claims_permitted = True
    selected, report, fatal = select_evidence([candidate], registry, repository_root=ROOT)
    row = next(item for item in report["requirements"] if item["requirement_id"] == "offline_recommendation")
    assert not selected and fatal == {"offline_recommendation"}
    assert "EVIDENCE_CANDIDATE_INVALID" in row["reason_codes"]


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


def test_e3_contract_freezes_pairing_holm_endpoints_and_timeout_policy():
    from evaluation_v5.user_study.questionnaires import (
        ANALYSIS_PLAN,
        ANALYSIS_PLAN_SHA256,
        ANALYSIS_PLAN_VERSION,
    )

    manifest = {
        "execution_status": "OBSERVED",
        "contracts": {
            "analysis_plan_version": ANALYSIS_PLAN_VERSION,
            "analysis_plan_sha256": ANALYSIS_PLAN_SHA256,
        },
    }
    status = {
        "execution_status": "OBSERVED",
        "task_set_stage": "confirmatory",
        "task_set_status": "frozen",
    }
    analysis = {
        "execution_status": "OBSERVED",
        "analysis_plan_version": ANALYSIS_PLAN_VERSION,
        "analysis_plan_sha256": ANALYSIS_PLAN_SHA256,
        "primary_inference_registry": {
            "method": "holm_step_down",
            "family_alpha": .05,
            "family_size": 2,
            "unavailable_endpoint_policy": "retain_in_family_and_never_reduce_family_size",
            "hypotheses": [
                {"endpoint": "selection_success", "sidedness": "two_sided"},
                {"endpoint": "decision_time_seconds", "sidedness": "two_sided"},
            ],
        },
        "effects": {
            "holm_family": {
                "family": ["selection_success", "decision_time_seconds"],
                "alpha": .05,
                "method": "holm_two_sided",
            },
            "decision_time_seconds": {
                "estimand": ANALYSIS_PLAN["decision_time_seconds"]["estimand"],
                "nonconfirmation_policy": ANALYSIS_PLAN["decision_time_seconds"]["nonconfirmation_policy"],
                "primary_timeout_or_pseudotime_policy": "none",
            },
        },
    }
    _validate_e3_claim_contract(manifest, analysis, status)
    analysis["primary_inference_registry"]["hypotheses"][1]["endpoint"] = "custom_selection_convenience"
    with pytest.raises(research_module.ResearchAnalysisError, match="co-primary"):
        _validate_e3_claim_contract(manifest, analysis, status)
    analysis["primary_inference_registry"]["hypotheses"][1]["endpoint"] = "decision_time_seconds"
    analysis["effects"]["decision_time_seconds"]["primary_timeout_or_pseudotime_policy"] = "impute_timeout"
    with pytest.raises(research_module.ResearchAnalysisError, match="timeout policy"):
        _validate_e3_claim_contract(manifest, analysis, status)


@pytest.mark.parametrize(
    ("claim_id", "requirement", "metrics", "flip"),
    [
        ("H1", "offline_recommendation", {"effect": .2, "ci_low": .1, "p_value": .01, "test_available": True}, ("effect", -.2)),
        ("H2", "natural_language_robustness", {"effect": -.2, "ci_high": -.1, "p_value": .01, "test_available": True}, ("effect", .2)),
        ("H3", "user_study", {"selection_effect": .1, "selection_ci_low": .01, "selection_p_holm": .02, "time_effect": -10, "time_ci_high": -1, "time_p_holm": .03, "tests_available": True}, ("time_ci_high", 1)),
        ("H4", "user_study", {"seq_effect": 1, "seq_ci_low": .1, "sus_effect": 5, "sus_ci_low": 1, "estimates_available": True}, ("sus_ci_low", -1)),
        ("H5", "resource_efficiency", {"pareto_classification": "STRICT_FRONTIER_IMPROVEMENT", "pareto_report_consistent": True, "reliability_preserved": True, "cpu_effect": -2, "cpu_ci_high": -1, "cpu_p_holm": .01, "memory_effect": -2, "memory_ci_high": -1, "memory_p_holm": .02, "tests_available": True}, ("reliability_preserved", False)),
        ("H6", "resource_efficiency", {"oracle_independence_verified": True, "cpu_effect": -2, "cpu_ci_high": -1, "cpu_p_holm": .01, "memory_effect": -2, "memory_ci_high": -1, "memory_p_holm": .02, "tests_available": True}, ("memory_ci_high", 1)),
        ("H7F", "image_functional", {"conservative_success": 1.0, "operational_adequacy": 1.0, "required_probe_not_defined_count": 0, "execution_unavailable_count": 0, "failed_probe_count": 0, "all_digests_immutable": True}, ("failed_probe_count", 1)),
        ("H7", "image_storage", {"catalog_prefix_count": 3, "all_prefixes_nonexpanding": True, "final_savings_bytes": 10, "expansion_naive_bytes": 20, "expansion_growth_difference": -5, "strictly_slower_catalog_expansion": True, "prefix_order_valid": True}, ("expansion_growth_difference", 0)),
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


def test_h5_resource_reduction_without_reliability_preservation_is_not_supported(tmp_path: Path):
    registry = load_claim_registry()
    candidate = _candidate(tmp_path, "resource_efficiency", "H5", {
        "pareto_classification": "EFFICIENCY_RELIABILITY_TRADEOFF",
        "pareto_report_consistent": True,
        "reliability_preserved": False,
        "cpu_effect": -5, "cpu_ci_high": -1, "cpu_p_holm": .001,
        "memory_effect": -5, "memory_ci_high": -1, "memory_p_holm": .001,
        "tests_available": True,
    })
    assert _evaluate_one(registry, candidate)["claim_status"] == "NOT_SUPPORTED"

    reference = {
        "condition": "STATIC_LARGE", "cpu_cost_per_success": 10,
        "memory_cost_per_success": 10, "oom_rate": 0, "timeout_rate": 0,
        "pending_or_admission_rate": 0, "runtime_error_rate": 0,
        "incorrect_rate": 0, "success_rate": 1, "correct_completion_rate": 1,
    }
    cheaper_but_worse = {
        **reference,
        "condition": "P2_CATALOG",
        "cpu_cost_per_success": 5,
        "memory_cost_per_success": 5,
        "success_rate": .5,
        "correct_completion_rate": .5,
    }
    recomputed = _h5_metrics(
        [],
        [{"condition": "P2_CATALOG", "reference": "STATIC_LARGE", "classification": "STRICT_FRONTIER_IMPROVEMENT"}],
        [reference, cheaper_but_worse],
    )
    assert recomputed["pareto_recomputed_classification"] == "EFFICIENCY_RELIABILITY_TRADEOFF"
    assert recomputed["pareto_report_consistent"] is False
    assert recomputed["reliability_preserved"] is False


def test_h6_requires_independently_bound_oracle_provenance(tmp_path: Path):
    registry = load_claim_registry()
    candidate = _candidate(tmp_path, "resource_efficiency", "H6", {
        "oracle_independence_verified": False,
        "cpu_effect": -2, "cpu_ci_high": -1, "cpu_p_holm": .01,
        "memory_effect": -2, "memory_ci_high": -1, "memory_p_holm": .02,
        "tests_available": True,
    })
    assert _evaluate_one(registry, candidate)["claim_status"] == "NOT_SUPPORTED"
    candidate.metrics["H6"]["oracle_independence_verified"] = None
    assert _evaluate_one(registry, candidate)["claim_status"] == "NOT_EXECUTED"


def test_h7_requires_post_baseline_catalog_growth_not_one_scale_savings(tmp_path: Path):
    registry = load_claim_registry()
    one_scale = _candidate(tmp_path / "one", "image_storage", "H7", {
        "catalog_prefix_count": 1,
        "all_prefixes_nonexpanding": True,
        "final_savings_bytes": 100,
        "expansion_naive_bytes": None,
        "expansion_growth_difference": None,
        "strictly_slower_catalog_expansion": None,
        "prefix_order_valid": True,
    })
    assert _evaluate_one(registry, one_scale)["claim_status"] == "NOT_EXECUTED"

    inherited_only = _candidate(tmp_path / "flat", "image_storage", "H7", {
        "catalog_prefix_count": 3,
        "all_prefixes_nonexpanding": True,
        "final_savings_bytes": 100,
        "expansion_naive_bytes": 200,
        "expansion_growth_difference": 0,
        "strictly_slower_catalog_expansion": False,
        "prefix_order_valid": True,
    })
    assert _evaluate_one(registry, inherited_only)["claim_status"] == "NOT_SUPPORTED"


def test_h7_functional_and_storage_claims_cannot_imply_each_other(tmp_path: Path):
    registry = load_claim_registry()
    functional = _candidate(tmp_path / "functional", "image_functional", "H7F", {
        "conservative_success": 1.0, "operational_adequacy": 1.0,
        "required_probe_not_defined_count": 0, "execution_unavailable_count": 0,
        "failed_probe_count": 0, "all_digests_immutable": True,
    })
    selected = {"image_functional": functional}
    claims = evaluate_claims(
        registry=registry,
        selected=selected,
        selection_report=_selection_report(registry, selected),
    )
    by_id = {row["claim_id"]: row for row in claims}
    assert by_id["H7F"]["claim_status"] == "SUPPORTED"
    assert by_id["H7"]["claim_status"] == "NOT_EXECUTED"

    storage = _candidate(tmp_path / "storage", "image_storage", "H7", {
        "catalog_prefix_count": 3, "all_prefixes_nonexpanding": True,
        "final_savings_bytes": 10, "expansion_naive_bytes": 20,
        "expansion_growth_difference": -5,
        "strictly_slower_catalog_expansion": True, "prefix_order_valid": True,
    })
    selected = {"image_storage": storage}
    claims = evaluate_claims(
        registry=registry,
        selected=selected,
        selection_report=_selection_report(registry, selected),
    )
    by_id = {row["claim_id"]: row for row in claims}
    assert by_id["H7"]["claim_status"] == "SUPPORTED"
    assert by_id["H7F"]["claim_status"] == "NOT_EXECUTED"


def test_development_or_failed_validation_never_decides_claim(tmp_path: Path):
    registry = load_claim_registry()
    metrics = {"effect": .2, "ci_low": .1, "p_value": .01, "test_available": True}
    candidate = _candidate(tmp_path, "offline_recommendation", "H1", metrics, stage="development")
    selected, report, _ = select_evidence([candidate], registry, repository_root=ROOT)
    result = evaluate_claims(registry=registry, selected=selected, selection_report=report)
    assert next(row for row in result if row["claim_id"] == "H1")["claim_status"] == "NOT_EXECUTED"


def test_exact_metric_lineage_is_required_and_contains_reproducible_fields(tmp_path: Path):
    registry = load_claim_registry()
    candidate = _candidate(tmp_path, "offline_recommendation", "H1", {
        "effect": .2, "ci_low": .1, "p_value": .01, "test_available": True,
    })
    claim = _evaluate_one(registry, candidate)
    assert claim["claim_status"] == "SUPPORTED"
    assert claim["decision_rule"]["conditions"] == next(
        row["support_all_of"] for row in registry["claims"] if row["id"] == "H1"
    )
    evidence = claim["evidence"][0]
    assert evidence["experiment_id"] == candidate.experiment_id
    assert evidence["schema_version"] == candidate.schema_version
    source = claim["result"]["metric_lineage"]["effect"][0]
    assert source["artifact_sha256"] == file_sha256(candidate.manifest_path)
    assert source["locator"]["json_pointers"] == ["/claims/H1/effect"]

    candidate.metric_lineage["H1"].pop("effect")
    claim = _evaluate_one(registry, candidate)
    assert claim["claim_status"] == "NOT_EXECUTED"
    assert "EXACT_METRIC_LINEAGE_UNAVAILABLE" in claim["reason_codes"]


def test_missing_metric_is_absent_not_numeric_zero(tmp_path: Path):
    registry = load_claim_registry()
    candidate = _candidate(tmp_path, "offline_recommendation", "H1", {
        "ci_low": .1, "p_value": .01, "test_available": True,
    })
    claim = _evaluate_one(registry, candidate)
    assert claim["claim_status"] == "NOT_EXECUTED"
    assert "effect" not in claim["result"]["normalized_metrics"]
    check = next(row for row in claim["result"]["decision_checks"] if row["path"] == "metrics.effect")
    assert check["observed"] is None and check["passed"] is None


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


@pytest.mark.parametrize(
    ("semantic_key", "expected_scope"),
    [
        ("p2.pipeline_version", "FREEZE"),
        ("catalog.version", "FREEZE"),
        ("indexes.dense.sha256", "FREEZE"),
        ("extractor.prompt_sha256", "FREEZE"),
    ],
)
def test_semantic_freeze_catalog_index_prompt_and_system_mismatches_block(
    tmp_path: Path, semantic_key: str, expected_scope: str
):
    registry = load_claim_registry()
    freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    candidate = _candidate(tmp_path, "offline_recommendation", "H1", {})
    candidate.semantic_provenance = _freeze_semantics(
        registry, freeze, "offline_recommendation"
    )
    candidate.semantic_provenance.update({
        "benchmark.dataset_sha256": "a" * 64,
        "benchmark.split_id": "confirmatory-split",
    })
    candidate.semantic_provenance[semantic_key] = "mismatch"
    report, blocked = check_provenance(
        {"offline_recommendation": candidate}, registry, freeze
    )
    assert blocked == {"offline_recommendation"}
    assert any(
        row["scope"] == expected_scope
        and row["semantic_key"] == semantic_key
        and row["status"] == "MISMATCH"
        for row in report["semantic_comparisons"]
    )


def test_cross_experiment_dataset_and_split_mismatch_blocks_both_claims(tmp_path: Path):
    registry = load_claim_registry()
    freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    e1 = _candidate(tmp_path / "e1", "offline_recommendation", "H1", {})
    e2 = _candidate(tmp_path / "e2", "natural_language_robustness", "H2", {})
    for candidate in (e1, e2):
        candidate.semantic_provenance = _freeze_semantics(
            registry, freeze, candidate.requirement_id
        )
        candidate.semantic_provenance.update({
            "benchmark.dataset_sha256": "a" * 64,
            "benchmark.split_id": "confirmatory-a",
        })
    e2.semantic_provenance["benchmark.dataset_sha256"] = "b" * 64
    e2.semantic_provenance["benchmark.split_id"] = "confirmatory-b"
    report, blocked = check_provenance(
        {"offline_recommendation": e1, "natural_language_robustness": e2},
        registry,
        freeze,
    )
    assert blocked == {"offline_recommendation", "natural_language_robustness"}
    mismatches = {
        row["semantic_key"]
        for row in report["semantic_comparisons"]
        if row["scope"] == "CROSS_EXPERIMENT_DECLARED" and row["status"] == "MISMATCH"
    }
    assert mismatches == {"benchmark.dataset_sha256", "benchmark.split_id"}


def test_harness_only_git_revision_difference_is_disclosed_not_blocked(tmp_path: Path):
    registry = load_claim_registry()
    freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    e1 = _candidate(tmp_path / "e1", "offline_recommendation", "H1", {})
    e2 = _candidate(tmp_path / "e2", "natural_language_robustness", "H2", {})
    for revision, candidate in (("revision-a", e1), ("revision-b", e2)):
        candidate.semantic_provenance = _freeze_semantics(
            registry, freeze, candidate.requirement_id
        )
        candidate.semantic_provenance.update({
            "benchmark.dataset_sha256": "a" * 64,
            "benchmark.split_id": "confirmatory-a",
        })
        candidate.provenance = {"git_revision": revision}
    report, blocked = check_provenance(
        {"offline_recommendation": e1, "natural_language_robustness": e2},
        registry,
        freeze,
    )
    assert not blocked and report["semantic_status"] == "PASS"
    disclosure = next(row for row in report["disclosures"] if row["type"] == "GIT_REVISION")
    assert disclosure["status"] == "DISCLOSED_DIFFERENCE"
    assert disclosure["blocking"] is False


def test_incompatible_digest_namespaces_never_compare_as_equal(tmp_path: Path):
    registry = load_claim_registry()
    freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    altered = json.loads(json.dumps(registry))
    requirement = next(
        row for row in altered["evidence_requirements"] if row["id"] == "offline_recommendation"
    )
    corpus = next(row for row in requirement["semantic_provenance"] if row["key"] == "corpus.sha256")
    corpus["key"] = "catalog.file_sha256"
    altered_path = tmp_path / "incompatible-registry.yaml"
    altered_path.write_text(yaml.safe_dump(altered, sort_keys=False), encoding="utf-8")
    with pytest.raises(ResearchContractError, match="digest namespaces are incompatible"):
        load_claim_registry(altered_path)
    candidate = _candidate(tmp_path, "offline_recommendation", "H1", {})
    candidate.semantic_provenance = _freeze_semantics(
        altered, freeze, "offline_recommendation"
    )
    candidate.semantic_provenance.update({
        "benchmark.dataset_sha256": "a" * 64,
        "benchmark.split_id": "confirmatory-a",
    })
    report, blocked = check_provenance(
        {"offline_recommendation": candidate}, altered, freeze
    )
    assert blocked == {"offline_recommendation"}
    assert any(
        row["status"] == "INCOMPATIBLE_DIGEST_NAMESPACE"
        and row["digest_namespace"] == "catalog_file_bytes"
        and row["freeze_digest_namespace"] == "candidate_corpus_canonical"
        for row in report["semantic_comparisons"]
    )


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
    e4.metadata = {
        "success_noninferiority_margin": None,
        "success_noninferiority_margin_declared": True,
        "cluster_identity": {"name": "cluster-a"},
    }
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


def test_threat_generator_does_not_invent_absent_cluster_or_platform_metadata(tmp_path: Path):
    registry = load_claim_registry()
    e4 = _candidate(tmp_path / "e4", "resource_efficiency", "H5", {}, evidence_class="E4")
    e5 = _candidate(tmp_path / "e5", "image_functional", "H7F", {}, evidence_class="E5")
    selected = {row.requirement_id: row for row in (e4, e5)}
    threats = generate_threats(
        registry=registry,
        candidates=[e4, e5],
        selected=selected,
        selection_report=_selection_report(registry, selected),
        provenance_report={"disclosures": [], "semantic_comparisons": []},
    )
    codes = {row["code"] for row in threats["threats"]}
    assert "SINGLE_CLUSTER_GENERALIZATION" not in codes
    assert "IMAGE_PLATFORM_DEPENDENCE" not in codes
    assert "NO_SUCCESS_NONINFERIORITY_MARGIN" not in codes


def test_provenance_failures_generate_metadata_bound_internal_threats(tmp_path: Path):
    registry = load_claim_registry()
    candidate = _candidate(tmp_path, "image_storage", "H7", {}, evidence_class="E5")
    selected = {"image_storage": candidate}
    provenance = {
        "disclosures": [],
        "semantic_comparisons": [{
            "requirement_id": "image_storage",
            "semantic_key": "catalog.file_sha256",
            "expected": "a" * 64,
            "observed": "b" * 64,
            "digest_namespace": "catalog_file_bytes",
            "freeze_digest_namespace": "catalog_file_bytes",
            "status": "MISMATCH",
        }],
    }
    threats = generate_threats(
        registry=registry,
        candidates=[candidate],
        selected=selected,
        selection_report=_selection_report(registry, selected),
        provenance_report=provenance,
    )
    threat = next(row for row in threats["threats"] if row["code"] == "SEMANTIC_PROVENANCE_MISMATCH")
    assert threat["category"] == "internal"
    assert threat["observed_value"]["semantic_key"] == "catalog.file_sha256"
    assert threat["source_pointer"] == "/semantic_comparisons/0"


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
    evidence["prefixes"][1]["image_digests"].reverse()
    evidence["prefixes"][1]["unique_layer_bytes"] = 11
    with pytest.raises(ResearchContractError, match="nondecreasing"):
        validate_storage_evidence(evidence)


def test_storage_adapter_computes_catalog_expansion_growth_not_only_final_savings(tmp_path: Path):
    package = tmp_path / "storage"
    derived = package / "derived"
    derived.mkdir(parents=True)
    digests = ["sha256:" + char * 64 for char in ("a", "b", "c")]
    evidence = {
        "schema_version": "protocol-v5-image-storage-evidence-v1.0.0",
        "protocol_version": "5.0.0",
        "experiment_id": "E5",
        "execution_status": "OBSERVED",
        "split_stage": "confirmatory",
        "claims_permitted": True,
        "measured_at_utc": "2026-09-05T00:00:00Z",
        "catalog": {"version": "v", "file_sha256": "d" * 64, "ordered_image_digests": digests},
        "platform": {"environment_id": "env", "runtime": "containerd", "operating_system": "linux", "architecture": "amd64"},
        "measurement_method": "content-store layer digest accounting",
        "prefixes": [
            {"prefix_size": 1, "image_digests": digests[:1], "naive_logical_bytes": 100, "unique_layer_bytes": 80},
            {"prefix_size": 2, "image_digests": digests[:2], "naive_logical_bytes": 200, "unique_layer_bytes": 180},
            {"prefix_size": 3, "image_digests": digests[:3], "naive_logical_bytes": 300, "unique_layer_bytes": 280},
        ],
        "provenance": {"git_revision": "abc", "dataset_sha256": "e" * 64, "backend_system_versions": {"P2": "p2-pipeline-v1.0.0"}},
    }
    path = derived / "storage_metrics.json"
    path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
    (package / "SHA256SUMS").write_text(
        f"{file_sha256(path)}  derived/storage_metrics.json\n", encoding="utf-8"
    )
    candidate = _adapt_image_storage(package, path)
    assert candidate.metrics["H7"]["final_savings_bytes"] == 20
    assert candidate.metrics["H7"]["expansion_growth_difference"] == 0
    assert candidate.metrics["H7"]["strictly_slower_catalog_expansion"] is False


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


def test_complete_mandatory_evidence_exits_zero_while_optional_h8_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    candidates = _complete_mandatory_candidates(tmp_path / "evidence")
    monkeypatch.setattr(
        research_module,
        "discover_evidence",
        lambda *args, **kwargs: candidates,
    )
    monkeypatch.setattr(
        research_module,
        "check_provenance",
        lambda *args, **kwargs: (
            {
                "schema_version": research_module.PROVENANCE_SCHEMA_VERSION,
                "semantic_comparisons": [],
                "disclosures": [],
                "blocked_requirements": [],
                "semantic_status": "PASS",
            },
            set(),
        ),
    )
    package, status, exit_code = run_research_analysis(
        results_root=tmp_path / "results",
        output_root=tmp_path / "analysis",
        run_id="complete-without-optional-h8",
        freeze_path=FREEZE_PATH,
    )
    assert status == "COMPLETE" and exit_code == EXIT_SUCCESS
    assert validate_research_analysis_package(package)["status"] == "PASS"
    claims = json.loads((package / "derived" / "evaluated-claim-registry.json").read_text())["claims"]
    by_id = {row["claim_id"]: row for row in claims}
    assert by_id["H8"]["claim_status"] == "NOT_EXECUTED"
    assert all(by_id[claim]["claimable"] for claim in EXPECTED_CLAIMS - {"H8"})
    completeness = json.loads((package / "derived" / "evidence-completeness.json").read_text())
    assert completeness["all_required_claims_decided"] is True


def test_contradictory_claimable_evidence_writes_failed_audit_and_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    candidates = [
        _candidate(tmp_path / "positive", "offline_recommendation", "H1", {
            "effect": .2, "ci_low": .1, "p_value": .01, "test_available": True,
        }),
        _candidate(tmp_path / "negative", "offline_recommendation", "H1", {
            "effect": -.2, "ci_low": -.3, "p_value": .8, "test_available": True,
        }),
    ]
    monkeypatch.setattr(research_module, "discover_evidence", lambda *args, **kwargs: candidates)
    package, status, exit_code = run_research_analysis(
        results_root=tmp_path / "results",
        output_root=tmp_path / "analysis",
        run_id="contradictory",
        freeze_path=FREEZE_PATH,
    )
    assert status == "FAILED" and exit_code == EXIT_FAILED
    validation = validate_research_analysis_package(package)
    assert validation["status"] == "PASS" and validation["package_status"] == "FAILED"
    selection = json.loads((package / "derived" / "evidence-selection.json").read_text())
    row = next(item for item in selection["requirements"] if item["requirement_id"] == "offline_recommendation")
    assert row["conflict_status"] == "CONFLICTING"
    claims = json.loads((package / "derived" / "evaluated-claim-registry.json").read_text())["claims"]
    assert not any(row["claimable"] for row in claims)


def test_valid_dry_run_evidence_never_upgrades_confirmatory_claim(tmp_path: Path):
    registry = load_claim_registry()
    candidate = _candidate(tmp_path, "offline_recommendation", "H1", {
        "effect": .2, "ci_low": .1, "p_value": .01, "test_available": True,
    })
    candidate.execution_status = "DRY_RUN"
    candidate.claims_permitted = False
    candidate.claim_eligibility = "INELIGIBLE"
    candidate.reason_codes = ["EVIDENCE_NOT_OBSERVED_COMPLETE", "CLAIMS_NOT_PERMITTED"]
    selected, report, fatal = select_evidence([candidate], registry, repository_root=ROOT)
    claims = evaluate_claims(registry=registry, selected=selected, selection_report=report)
    assert not fatal
    assert next(row for row in claims if row["claim_id"] == "H1")["claim_status"] == "NOT_EXECUTED"


def test_optional_h8_never_controls_required_package_completeness(tmp_path: Path):
    registry = load_claim_registry()
    candidates = _complete_mandatory_candidates(tmp_path)
    selected = {candidate.requirement_id: candidate for candidate in candidates}
    claims = evaluate_claims(
        registry=registry,
        selected=selected,
        selection_report=_selection_report(registry, selected),
    )
    assert next(row for row in claims if row["claim_id"] == "H8")["claim_status"] == "NOT_EXECUTED"
    assert _package_status(claims, fatal=False) == "COMPLETE"


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
