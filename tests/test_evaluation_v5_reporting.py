"""Tests for Protocol-v5 offline reporting layer (E1/E2)."""

from __future__ import annotations

import json
from pathlib import Path
import platform
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from evaluation_v4.dataset import file_sha256
from evaluation_v5.analysis.component_scoring import GoldCase, GoldSource
from evaluation_v5.analysis.reporting import (
    FIGURE_FILES,
    LIMITATIONS_MD,
    P3_DECISION_MD,
    RECOMMENDATION_QUALITY_MD,
    REPORT_MANIFEST_FILENAME,
    REPORTING_SCHEMA_VERSION,
    ROBUSTNESS_MD,
    SYNTHESIS_REPORT_FILENAME,
    TABLE_FILES,
    ReportingError,
    compute_confidence_intervals_data,
    compute_error_taxonomy_data,
    compute_limitations_block,
    compute_p3_development_decision,
    compute_paired_family_outcomes_data,
    compute_recommendation_quality_data,
    compute_retrieval_ablation_data,
    compute_robustness_table_data,
    format_limitations_md,
    format_p3_decision_md,
    format_recommendation_quality_md,
    format_robustness_md,
    generate_offline_report,
    generate_synthesis_report,
    main,
    render_confidence_intervals_svg,
    render_error_taxonomy_svg,
    render_paired_family_outcomes_svg,
    render_retrieval_recall_svg,
    write_not_executed_report,
)
from evaluation_v5.offline.runner import (
    COMPLETION_FILENAME,
    OFFLINE_COMPLETION_SCHEMA_VERSION,
    OFFLINE_PROVENANCE_SCHEMA_VERSION,
    OFFLINE_RAW_RECORD_SCHEMA_VERSION,
    PROVENANCE_FILENAME,
    RAW_DIRECTORY_NAME,
    RECORDS_FILENAME,
    REPEAT_POLICY_VERSION,
    REPORT_DIRECTORY_NAME,
    _finite_json,
    provenance_fingerprint,
)
from evaluation_v5.offline.validate_evidence import OfflineEvidenceValidationError
from evaluation_v5.split_dataset import (
    LoadedSplit,
    SPLIT_BUNDLE_SCHEMA_VERSION_V2,
    SplitRole,
)
from recommender.candidate_corpus import load_candidate_corpus

CORPUS = load_candidate_corpus()
GOOD_CANDIDATE = "medium-scipy-data-science"
OTHER_ACCEPTABLE_CANDIDATE = "large-scipy-data-science"
VIOLATING_CANDIDATE = "small-minimal-python"


def _make_case(
    family_id: str,
    variant_id: str,
    *,
    variant_class: str = "canonical_en",
    workload_stratum: str = "data_processing",
    expected_feasibility: str = "feasible",
) -> GoldCase:
    language = "vi" if variant_class == "vietnamese" else "en"
    return GoldCase(
        case_id=f"{family_id}-{variant_id}",
        family_id=family_id,
        variant_id=variant_id,
        language=language,
        prompt=f"Run data analysis for {family_id}",
        dataset_size_gb=1.0,
        code_context_hints=("import pandas as pd",),
        gold_structured_intent={
            "task_types": ["data_analysis"],
            "required_features": ["pandas"],
            "preferred_features": [],
            "forbidden_features": [],
            "required_frameworks": [],
            "preferred_frameworks": [],
            "gpu_semantics": "unspecified",
            "minimum_cpu_cores": 2,
            "minimum_memory_gb": 4,
            "dataset_size_gb": 1.0,
            "ambiguities": [],
        },
        candidate_gold={
            "preferred_candidate_ids": [GOOD_CANDIDATE],
            "acceptable_candidate_ids": [GOOD_CANDIDATE, OTHER_ACCEPTABLE_CANDIDATE],
        },
        image_gold={
            "preferred_image_ids": ["scipy-data-science"],
            "acceptable_image_ids": ["scipy-data-science"],
            "required_capabilities": ["pandas"],
        },
        policy_gold={
            "required_constraints": [],
            "explicitly_unsupported_requirements": [],
            "expected_feasibility": expected_feasibility,
        },
    )


def _make_gold_source(cases, *, role: str = "development") -> GoldSource:
    split_cases = tuple(
        SimpleNamespace(
            case_id=c.case_id,
            family_id=c.family_id,
            variant_id=c.variant_id,
            language=c.language,
            prompt=c.prompt,
            inputs={
                "dataset_size_gb": c.dataset_size_gb,
                "code_context_hints": list(c.code_context_hints),
            },
            family_metadata={"workload_stratum": "data_processing"},
            variant_metadata={
                "variant_class": (
                    "canonical_en"
                    if "canonical" in c.variant_id
                    else (
                        "vietnamese"
                        if "vi" in c.variant_id
                        else (
                            "paraphrase_en"
                            if "paraphrase" in c.variant_id
                            else (
                                "informal_or_noisy"
                                if "noisy" in c.variant_id
                                else "optional_code_context"
                            )
                        )
                    )
                ),
                "equivalence_status": (
                    "canonical_reference"
                    if "canonical" in c.variant_id
                    else "reviewed_equivalent"
                ),
            },
            gold={
                "gold_structured_intent": c.gold_structured_intent,
                "candidate_gold": c.candidate_gold,
                "profile_gold": {
                    "preferred_profile_ids": ["medium"],
                    "acceptable_profile_ids": ["medium", "large"],
                },
                "image_gold": c.image_gold,
                "policy_gold": c.policy_gold,
            },
        )
        for c in cases
    )

    family_count = len({c.family_id for c in cases})
    manifest = SimpleNamespace(
        dataset_id="synthetic-reporting-gold",
        split_id=f"synthetic-{role}",
        role=SplitRole.DEVELOPMENT if role == "development" else SplitRole.CONFIRMATORY,
        checksum="a" * 64,
        case_count=len(cases),
        family_count=family_count,
        freeze_metadata=SimpleNamespace(
            frozen_at_utc="2026-08-25T08:00:00Z",
            frozen_by="test-author",
        ),
    )
    bundle = SimpleNamespace(
        schema_version=SPLIT_BUNDLE_SCHEMA_VERSION_V2,
        split_manifest=manifest,
        cases=split_cases,
    )
    split = LoadedSplit(
        bundle=bundle,
        source_file_sha256="b" * 64,
    )
    freeze_id_dict = {
        "freeze_id": None,
        "frozen_at_utc": "2026-08-25T08:00:00Z",
        "frozen_by": "test-author",
        "source": "development_split_manifest",
    }
    return GoldSource(
        role=role,
        dataset_id="synthetic-reporting-gold",
        schema_version="protocol-v5-gold-family-v1.0.0",
        source_file_sha256="b" * 64,
        canonical_sha256="a" * 64,
        catalog_identity={
            "candidate_corpus_version": CORPUS.corpus_version,
            "candidate_corpus_sha256": CORPUS.corpus_checksum,
            "image_catalog_version": CORPUS.source_image_catalog_version,
            "image_catalog_sha256": CORPUS.source_image_catalog_checksum,
            "profile_catalog_sha256": CORPUS.source_profile_catalog_checksum,
        },
        cases=tuple(cases),
        split=split,
        freeze_identity=freeze_id_dict,
        p3_gate_identity=None,
    )


def _make_raw_record(
    case: GoldCase,
    *,
    system_id: str,
    predicted_candidate: str,
    latency: float = 0.05,
    repeat_index: int = 0,
    fingerprint: str = "f" * 64,
    sparse_hits: tuple[str, ...] = (GOOD_CANDIDATE, OTHER_ACCEPTABLE_CANDIDATE),
    dense_hits: tuple[str, ...] = (GOOD_CANDIDATE, OTHER_ACCEPTABLE_CANDIDATE),
    hybrid_hits: tuple[str, ...] = (GOOD_CANDIDATE, OTHER_ACCEPTABLE_CANDIDATE),
) -> dict[str, Any]:
    acc_candidates = set(case.candidate_gold.get("acceptable_candidate_ids", []))
    is_feasible = case.policy_gold.get("expected_feasibility") == "feasible"
    is_acceptable = predicted_candidate in acc_candidates and is_feasible

    return {
        "schema_version": OFFLINE_RAW_RECORD_SCHEMA_VERSION,
        "provenance_fingerprint": fingerprint,
        "run_id": "test-run-001",
        "record_id": f"{system_id}-{case.case_id}-{repeat_index}",
        "timestamp_utc": "2026-08-25T08:00:00.000000Z",
        "case_id": case.case_id,
        "family_id": case.family_id,
        "variant_id": case.variant_id,
        "system_id": system_id,
        "repeat_index": repeat_index,
        "seed": 12345,
        "input_identity": {
            "case_id": case.case_id,
            "family_id": case.family_id,
            "variant_id": case.variant_id,
            "prompt_sha256": "1" * 64,
            "dataset_size_gb": case.dataset_size_gb,
        },
        "benchmark_prompt": case.prompt,
        "evaluation_gold": {
            "preferred_candidate_id": GOOD_CANDIDATE,
            "acceptable_candidate_ids": [GOOD_CANDIDATE, OTHER_ACCEPTABLE_CANDIDATE],
            "acceptable_profile_ids": ["medium", "large"],
            "acceptable_image_ids": ["scipy-data-science"],
            "required_image_capabilities": ["pandas"],
            "request_feasible": is_feasible,
        },
        "adapter_provenance": {"system_id": system_id, "adapter_version": "v1"},
        "backend_provenance": {},
        "status": "completed",
        "predicted_candidate_id": predicted_candidate,
        "predicted_profile_id": "medium",
        "predicted_image_id": "scipy-data-science",
        "recommendation_reasons": ["Matched requirements"],
        "recommendation_codes": ["OK"],
        "structured_intent": {
            "required_libraries": ["pandas"],
            "required_features": [],
            "required_frameworks": [],
            "preferred_features": [],
            "preferred_libraries": [],
            "preferred_frameworks": [],
            "forbidden_features": [],
            "resource_constraints": {
                "gpu_requirement": "unspecified",
                "minimum_cpu_cores": 2,
                "minimum_memory_gb": 4,
                "dataset_size_gb": 1.0,
            },
            "ambiguities": [],
            "normalized_query": case.prompt,
            "extraction_confidence": 1.0,
        },
        "sparse_ranks": [{"candidate_id": cid, "rank": i, "score": 1.0 / (i + 1)} for i, cid in enumerate(sparse_hits, start=1)],
        "dense_ranks": [{"candidate_id": cid, "rank": i, "score": 1.0 / (i + 1)} for i, cid in enumerate(dense_hits, start=1)],
        "hybrid_ranks_scores": [{"candidate_id": cid, "rank": i, "score": 1.0 / (i + 1)} for i, cid in enumerate(hybrid_hits, start=1)],
        "candidate_top_k": [{"candidate_id": cid, "rank": i, "score": 1.0 / (i + 1)} for i, cid in enumerate(hybrid_hits, start=1)],
        "constraint_evaluations": [{"candidate_id": predicted_candidate, "passed": True}],
        "feasible_top_k": [{"candidate_id": predicted_candidate, "rank": 1}],
        "final_ranking": [{"candidate_id": predicted_candidate, "rank": 1}],
        "constraint_summary": {"no_feasible_candidate": False, "unmet_constraints": []},
        "latency_components": {"total_seconds": latency},
        "fallback": {"used": False, "category": None},
        "errors": None,
        "metric_inputs": {
            "request_feasible": is_feasible,
            "preferred_candidate_id": GOOD_CANDIDATE,
            "acceptable_candidate_ids": [GOOD_CANDIDATE, OTHER_ACCEPTABLE_CANDIDATE],
            "acceptable_profile_ids": ["medium", "large"],
            "acceptable_image_ids": ["scipy-data-science"],
            "required_image_capabilities": ["pandas"],
            "predicted_candidate_id": predicted_candidate,
            "predicted_profile_id": "medium",
            "predicted_image_id": "scipy-data-science",
            "candidate_top_k_ids": list(hybrid_hits),
            "final_ranking_candidate_ids": [predicted_candidate],
            "hard_constraints_satisfied": True,
            "constraint_violation_codes": [],
            "infeasible_request_signal": False,
            "unsupported_request_signal": False,
            "fallback_used": False,
            "fallback_category": None,
        },
    }


def _p2_provenance():
    return {
        "backend_name": "p2-recommender",
        "backend_version": "p2-backend-v1",
        "pipeline_version": "p2-pipeline-v1.0.0",
        "structured_intent_schema_version": "structured-intent-v1",
        "extractor_name": "heuristic",
        "extractor_version": "1.0",
        "extractor_model_id": "none",
        "extractor_prompt_version": "1.0",
        "extractor_prompt_sha256": "0" * 64,
        "embedding_model_id": "sentence-transformers/all-MiniLM-L6-v2",
        "embedding_model_revision": "1.0",
        "dense_index_version": "1.0",
        "dense_index_sha256": "0" * 64,
        "sparse_index_version": "1.0",
        "sparse_index_sha256": "0" * 64,
        "hybrid_index_version": "1.0",
        "hybrid_index_sha256": "0" * 64,
        "retrieval_configuration": {"top_k": 5, "retriever_version": "1.0"},
        "constraint_ranking_configuration": {
            "constraint_evaluator_version": "1.0",
            "constraint_policy_version": "1.0",
            "ranker_version": "1.0",
        },
        "config": {},
        "generation": {},
        "candidate_catalog": {
            "catalog_version": CORPUS.source_image_catalog_version,
            "catalog_sha256": CORPUS.source_image_catalog_checksum,
            "corpus_version": CORPUS.corpus_version,
            "corpus_sha256": CORPUS.corpus_checksum,
            "candidates": [
                {
                    "candidate_id": item.candidate_id,
                    "profile_id": item.profile_id,
                    "image_id": item.image_id,
                    "catalog_version": item.catalog_version,
                    "policy_version": item.policy_version,
                }
                for item in sorted(CORPUS.candidates, key=lambda c: c.candidate_id)
            ],
        },
    }


def _create_synthetic_evidence_dir(
    tmp_path: Path,
    cases: Sequence[GoldCase],
    gold: GoldSource,
    *,
    systems: tuple[str, ...] = ("P1", "P2"),
    p1_accuracy: float = 0.5,
    p2_accuracy: float = 0.9,
) -> Path:
    evidence_dir = tmp_path / "offline_evidence"
    raw_dir = evidence_dir / RAW_DIRECTORY_NAME
    report_dir = evidence_dir / REPORT_DIRECTORY_NAME
    raw_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    from evaluation_v5.offline.recommenders import default_adapters
    adapters = default_adapters(enable_p3="P3" in systems)
    system_provenance = {s: adapters[s].frozen_provenance() for s in systems}
    catalog_snapshot = provenance_catalog = system_provenance["P2"]["candidate_catalog"]

    from evaluation_v5.offline.validate_evidence import _freeze_identity

    split_id = gold.split.manifest.split_id if gold.split is not None else "synthetic-development"
    split_bundle_chk = gold.split.manifest.checksum if gold.split is not None else gold.canonical_sha256
    split_dataset_sha = gold.source_file_sha256
    split_role = gold.split.manifest.role.value if gold.split is not None else gold.role
    dataset_id = gold.split.manifest.dataset_id if gold.split is not None else gold.dataset_id
    case_count = gold.split.manifest.case_count if gold.split is not None else len(cases)
    family_count = gold.split.manifest.family_count if gold.split is not None else len({c.family_id for c in cases})
    planned_records = case_count * len(systems)
    freeze_identity = _freeze_identity(gold.split) if gold.split is not None else (dict(gold.freeze_identity) if gold.freeze_identity is not None else None)

    plan = {
        "schema_version": OFFLINE_PROVENANCE_SCHEMA_VERSION,
        "protocol_version": "5.0.0",
        "experiment_id": "E6" if "P3" in systems else "E1",
        "run_id": "test-run-001",
        "split": {
            "dataset_id": dataset_id,
            "split_id": split_id,
            "role": split_role,
            "bundle_checksum": split_bundle_chk,
            "dataset_sha256": split_dataset_sha,
            "case_count": case_count,
            "family_count": family_count,
        },
        "freeze_identity": freeze_identity,
        "git_revision": "0" * 40,
        "git_worktree_dirty": False,
        "systems": list(systems),
        "system_frozen_provenance": system_provenance,
        "candidate_catalog": catalog_snapshot,
        "frozen_configuration": {},
        "seed": 42,
        "requested_repeats": 1,
        "effective_repeats": {s: 1 for s in systems},
        "repeat_policy": {
            "version": REPEAT_POLICY_VERSION,
            "deterministic_systems": list(systems),
            "stochastic_systems": [],
            "deterministic_effective_repeats": 1,
            "requested_repeats_apply_only_to_stochastic_systems": True,
        },
        "p3_explicitly_enabled": "P3" in systems,
        "planned_record_count": planned_records,
        "benchmark_prompt_policy": {
            "stored_in_raw_evidence": True,
            "operational_logging": "prohibited",
        },
        "environment_identity": {
            "python_version": sys.version.split()[0],
            "platform": sys.platform,
            "platform_release": platform.release(),
        },
    }
    fingerprint = provenance_fingerprint(plan)
    provenance = {
        **plan,
        "provenance_fingerprint": fingerprint,
        "created_utc": "2026-08-25T08:00:00.000000Z",
    }
    with (raw_dir / PROVENANCE_FILENAME).open("w", encoding="utf-8") as f:
        json.dump(provenance, f, indent=2)

    from evaluation_v5.offline.runner import (
        MatrixEntry,
        OfflineAdapterResult,
        _record_for_entry,
        build_execution_matrix,
    )
    matrix = build_execution_matrix(gold.split, system_ids=systems, adapters=adapters, repeats=1, seed=42, enable_p3=("P3" in systems))
    records = []
    candidate_map = {c.candidate_id: c for c in CORPUS.candidates}
    for c_idx, entry in enumerate(matrix):
        sys_id = entry.system_id
        acc = p2_accuracy if sys_id == "P2" else (0.95 if sys_id == "P3" else p1_accuracy)
        is_correct = ((c_idx * 7) % 10) / 10.0 < acc
        cand = GOOD_CANDIDATE if is_correct else VIOLATING_CANDIDATE

        is_p2 = sys_id in {"P2", "P3"}
        intent_dict = {
            "schema_version": "structured-intent-v1",
            "task_types": ["data_analysis"],
            "required_libraries": ["pandas"],
            "required_features": ["pandas"],
            "required_frameworks": [],
            "preferred_features": [],
            "preferred_libraries": [],
            "preferred_frameworks": [],
            "forbidden_features": [],
            "resource_constraints": {
                "gpu_requirement": "unspecified",
                "minimum_cpu_cores": 2,
                "minimum_memory_gb": 4,
                "dataset_size_gb": 1.0,
            },
            "ambiguities": [],
            "normalized_query": entry.case.prompt,
            "extraction_confidence": 1.0,
        } if is_p2 else None

        candidate_obj = candidate_map[cand]
        pred_profile = candidate_obj.profile_id
        pred_image = candidate_obj.image_id

        adapter_res = OfflineAdapterResult(
            predicted_candidate_id=cand,
            predicted_profile_id=pred_profile,
            predicted_image_id=pred_image,
            recommendation_reasons=("Matched requirements",),
            recommendation_codes=("OK",),
            structured_intent=intent_dict,
            sparse_ranks=tuple({"candidate_id": cid, "rank": i, "score": 1.0 / (i + 1)} for i, cid in enumerate((GOOD_CANDIDATE, OTHER_ACCEPTABLE_CANDIDATE), start=1)) if is_p2 else (),
            dense_ranks=tuple({"candidate_id": cid, "rank": i, "score": 1.0 / (i + 1)} for i, cid in enumerate((GOOD_CANDIDATE, OTHER_ACCEPTABLE_CANDIDATE), start=1)) if is_p2 else (),
            hybrid_ranks_scores=tuple({"candidate_id": cid, "rank": i, "score": 1.0 / (i + 1)} for i, cid in enumerate((GOOD_CANDIDATE, OTHER_ACCEPTABLE_CANDIDATE), start=1)) if is_p2 else (),
            candidate_top_k=tuple({"candidate_id": cid, "rank": i, "score": 1.0 / (i + 1)} for i, cid in enumerate((GOOD_CANDIDATE, OTHER_ACCEPTABLE_CANDIDATE), start=1)) if is_p2 else (),
            final_ranking=({"candidate_id": cand, "rank": 1, "score": 1.0},) if is_p2 else (),
            feasible_top_k=({"candidate_id": cand, "rank": 1, "score": 1.0},) if is_p2 else (),
            constraint_evaluations=({
                "candidate_id": cand,
                "feasible": True,
                "hard_constraints_satisfied": True,
                "violated_hard_constraints": [],
                "unsupported_constraints": [],
                "explanation_codes": [],
            },) if is_p2 else (),
            constraint_summary={"no_feasible_candidate": False, "unmet_constraints": [], "unsupported_constraints": []} if is_p2 else None,
            latency_components={"total_seconds": 0.03 if sys_id == "P1" else 0.08, "total_elapsed_seconds": 0.03 if sys_id == "P1" else 0.08},
            fallback={"used": False, "category": None},
        )
        rec = _record_for_entry(
            entry,
            split=gold.split,
            result=adapter_res,
            adapter_provenance=system_provenance[sys_id],
            provenance_fingerprint=fingerprint,
            run_id="test-run-001",
            include_benchmark_prompts=True,
        )
        records.append(rec)

    records_file = raw_dir / RECORDS_FILENAME
    with records_file.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    completion = {
        "schema_version": OFFLINE_COMPLETION_SCHEMA_VERSION,
        "provenance_fingerprint": fingerprint,
        "completed_utc": "2026-08-25T08:05:00.000000Z",
        "records": len(records),
        "error_records": 0,
        "recommendations_jsonl_sha256": file_sha256(records_file),
        "claims_permitted": False,
        "status": "RAW_EVIDENCE_COMPLETE",
    }
    with (report_dir / COMPLETION_FILENAME).open("w", encoding="utf-8") as f:
        json.dump(completion, f, indent=2)

    return evidence_dir


@pytest.fixture
def synthetic_benchmark():
    # 12 families x 5 variants = 60 cases
    cases = []
    for fam_idx in range(1, 13):
        fam_id = f"family_{fam_idx:02d}"
        for var_name in ("canonical", "paraphrase", "vietnamese", "noisy", "code_centric"):
            var_id = f"{fam_id}_{var_name}"
            cases.append(_make_case(fam_id, var_id, variant_class=f"{var_name}_en" if var_name in {"canonical", "paraphrase"} else var_name))
    gold = _make_gold_source(cases, role="development")
    return cases, gold


def test_generate_offline_report_creates_all_8_outputs(tmp_path, synthetic_benchmark, monkeypatch):
    cases, gold = synthetic_benchmark
    evidence_dir = _create_synthetic_evidence_dir(tmp_path, cases, gold, systems=("P1", "P2"))
    output_dir = tmp_path / "final_report"

    monkeypatch.setattr(
        "evaluation_v5.analysis.reporting.load_component_gold",
        lambda *args, **kwargs: gold,
    )

    result_dir = generate_offline_report(
        evidence_dir=evidence_dir,
        gold_path=tmp_path / "dummy_gold.yaml",
        output_dir=output_dir,
        role="development",
    )

    assert result_dir == output_dir
    assert (output_dir / REPORT_MANIFEST_FILENAME).is_file()
    assert (output_dir / SYNTHESIS_REPORT_FILENAME).is_file()

    # 1. Recommendation Quality Table
    assert (output_dir / TABLE_FILES["recommendation_quality"]).is_file()
    assert (output_dir / TABLE_FILES["recommendation_quality_json"]).is_file()
    assert (output_dir / RECOMMENDATION_QUALITY_MD).is_file()
    rq_json = json.loads((output_dir / TABLE_FILES["recommendation_quality_json"]).read_text())
    assert rq_json["schema_version"] == REPORTING_SCHEMA_VERSION
    assert len(rq_json["rows"]) >= 6

    # 2. Robustness Table
    assert (output_dir / TABLE_FILES["robustness"]).is_file()
    assert (output_dir / TABLE_FILES["robustness_json"]).is_file()
    assert (output_dir / ROBUSTNESS_MD).is_file()
    rob_json = json.loads((output_dir / TABLE_FILES["robustness_json"]).read_text())
    assert len(rob_json["rows"]) == 2
    assert "overall_srr" in rob_json["rows"][0]
    assert "worst_case_family_robustness" in rob_json["rows"][0]

    # 3. Retrieval Ablation Figure
    assert (output_dir / FIGURE_FILES["retrieval_recall_at_k"]).is_file()
    assert (output_dir / TABLE_FILES["retrieval_ablation"]).is_file()
    ret_json = json.loads((output_dir / TABLE_FILES["retrieval_ablation_json"]).read_text())
    assert any(r["channel"] == "Sparse" for r in ret_json["results"])
    assert any(r["channel"] == "Dense" for r in ret_json["results"])
    assert any(r["channel"] == "Hybrid" for r in ret_json["results"])

    # 4. Error Taxonomy Figure
    assert (output_dir / FIGURE_FILES["error_taxonomy"]).is_file()
    assert (output_dir / TABLE_FILES["error_taxonomy"]).is_file()
    err_json = json.loads((output_dir / TABLE_FILES["error_taxonomy_json"]).read_text())
    assert len(err_json["categories"]) >= 5

    # 5. Paired Family Outcome Visualization
    assert (output_dir / FIGURE_FILES["paired_family_outcomes"]).is_file()
    assert (output_dir / TABLE_FILES["paired_family_outcomes"]).is_file()
    pair_json = json.loads((output_dir / TABLE_FILES["paired_family_outcomes_json"]).read_text())
    assert pair_json["total_families"] == 12

    # 6. Confidence Interval Visualization
    assert (output_dir / FIGURE_FILES["confidence_intervals"]).is_file()
    assert (output_dir / TABLE_FILES["confidence_intervals"]).is_file()

    # 7. P3 Development Decision Report
    assert (output_dir / P3_DECISION_MD).is_file()
    assert (output_dir / TABLE_FILES["p3_development_decision_json"]).is_file()
    p3_json = json.loads((output_dir / TABLE_FILES["p3_development_decision_json"]).read_text())
    assert p3_json["decision"] in {"RETAINED", "NOT_RETAINED"}

    # 8. Limitations Block
    assert (output_dir / LIMITATIONS_MD).is_file()
    lim_content = (output_dir / LIMITATIONS_MD).read_text()
    assert "Independent Workload Families" in lim_content
    assert "Total Prompt Variants Evaluated" in lim_content


def test_svg_figures_valid_xml(tmp_path, synthetic_benchmark, monkeypatch):
    cases, gold = synthetic_benchmark
    evidence_dir = _create_synthetic_evidence_dir(tmp_path, cases, gold, systems=("P1", "P2"))
    output_dir = tmp_path / "svg_report"

    monkeypatch.setattr(
        "evaluation_v5.analysis.reporting.load_component_gold",
        lambda *args, **kwargs: gold,
    )

    generate_offline_report(
        evidence_dir=evidence_dir,
        gold_path=tmp_path / "dummy.yaml",
        output_dir=output_dir,
    )

    for fig_rel in FIGURE_FILES.values():
        fig_path = output_dir / fig_rel
        assert fig_path.is_file(), f"missing SVG figure {fig_rel}"
        content = fig_path.read_text(encoding="utf-8")
        assert content.startswith("<svg")
        root = ET.fromstring(content)
        assert root.tag.endswith("svg")
        assert "viewBox" in root.attrib


def test_p3_development_gate_isolation_confirmatory(synthetic_benchmark):
    cases, _ = synthetic_benchmark
    confirmatory_gold = _make_gold_source(cases, role="confirmatory")

    component_res = SimpleNamespace(
        aggregates={"P2": {}},
        p3_headroom={"gate_status": "RETAINED", "eligible_families": 12, "ranking_error_families": 4},
    )

    decision = compute_p3_development_decision(component_res, confirmatory_gold)
    assert decision["gate_status"] == "NOT_AVAILABLE"
    assert decision["decision"] == "UNAVAILABLE_NO_FROZEN_DEVELOPMENT_GATE"
    assert decision["confirmatory_inspection"] == "PROHIBITED"
    assert decision["claims_permitted"] is False


def test_p3_development_gate_with_freeze_identity(synthetic_benchmark):
    cases, _ = synthetic_benchmark
    freeze_p3_gate = {
        "status": "retained",
        "p3_active": True,
        "snapshot_version": "1.0.0",
        "evidence_sha256": "d" * 64,
    }
    confirmatory_gold = GoldSource(
        role="confirmatory",
        dataset_id="synthetic-reporting-gold",
        schema_version="protocol-v5-gold-family-v1.0.0",
        source_file_sha256="b" * 64,
        canonical_sha256="a" * 64,
        catalog_identity={
            "candidate_corpus_version": CORPUS.corpus_version,
            "candidate_corpus_sha256": CORPUS.corpus_checksum,
            "image_catalog_version": CORPUS.source_image_catalog_version,
            "image_catalog_sha256": CORPUS.source_image_catalog_checksum,
            "profile_catalog_sha256": CORPUS.source_profile_catalog_checksum,
        },
        cases=tuple(cases),
        split=None,
        freeze_identity=None,
        p3_gate_identity=freeze_p3_gate,
    )

    component_res = SimpleNamespace(
        aggregates={"P2": {}},
        p3_headroom={"gate_status": "NOT_RETAINED", "eligible_families": 12, "ranking_error_families": 0},
    )

    decision = compute_p3_development_decision(component_res, confirmatory_gold)
    assert decision["gate_status"] == "RETAINED"
    assert decision["decision"] == "RETAINED"
    assert decision["source_type"] == "confirmatory_freeze_manifest_snapshot"
    assert decision["confirmatory_inspection"] == "PROHIBITED"


def test_p3_mandatory_adversarial_isolation(tmp_path, synthetic_benchmark):
    cases, _ = synthetic_benchmark
    # Frozen development decision: P3 fails gate (NOT_RETAINED)
    frozen_dev_decision = {
        "schema_version": REPORTING_SCHEMA_VERSION,
        "gate_status": "NOT_RETAINED",
        "decision": "NOT_RETAINED",
        "source_split_role": "development",
        "rationale": "Insufficient ranking headroom on development set.",
    }

    # Case A: Confirmatory P3 observations look extremely favorable (100% accuracy)
    comp_a = SimpleNamespace(
        aggregates={"P2": {}, "P3": {"joint_accept_at_1": 1.0}},
        p3_headroom={"gate_status": "RETAINED", "eligible_families": 20, "ranking_error_families": 10},
    )
    conf_gold_a = _make_gold_source(cases, role="confirmatory")
    dec_a = compute_p3_development_decision(comp_a, conf_gold_a, p3_development_decision=frozen_dev_decision)

    # Case B: Confirmatory P3 observations look extremely unfavorable (0% accuracy)
    comp_b = SimpleNamespace(
        aggregates={"P2": {}, "P3": {"joint_accept_at_1": 0.0}},
        p3_headroom={"gate_status": "NOT_RETAINED", "eligible_families": 20, "ranking_error_families": 0},
    )
    conf_gold_b = _make_gold_source(cases, role="confirmatory")
    dec_b = compute_p3_development_decision(comp_b, conf_gold_b, p3_development_decision=frozen_dev_decision)

    # Gate decision in A and B must be IDENTICAL and equal to NOT_RETAINED
    assert dec_a["decision"] == dec_b["decision"] == "NOT_RETAINED"
    assert dec_a["gate_status"] == dec_b["gate_status"] == "NOT_RETAINED"
    assert dec_a["confirmatory_inspection"] == dec_b["confirmatory_inspection"] == "PROHIBITED"
    assert dec_a == dec_b


def test_p3_development_gate_decisions(synthetic_benchmark):
    cases, dev_gold = synthetic_benchmark

    # 1. Gate criterion NOT met -> NOT_RETAINED, claims_permitted = False
    comp_not_retained = SimpleNamespace(
        aggregates={"P2": {}},
        p3_headroom={
            "gate_status": "NOT_RETAINED",
            "eligible_families": 20,
            "ranking_error_families": 0,
            "ranking_error_rate": 0.0,
            "required_error_families": 3,
            "required_error_rate": 0.05,
            "rationale": "Insufficient ranking headroom on development set.",
        },
    )
    dec_nr = compute_p3_development_decision(comp_not_retained, dev_gold)
    assert dec_nr["decision"] == "NOT_RETAINED"
    assert dec_nr["gate_status"] == "NOT_RETAINED"
    assert dec_nr["claims_permitted"] is False

    # 2. Gate criterion MET -> RETAINED, claims_permitted = True
    comp_retained = SimpleNamespace(
        aggregates={"P2": {}},
        p3_headroom={
            "gate_status": "RETAINED",
            "eligible_families": 20,
            "ranking_error_families": 4,
            "ranking_error_rate": 0.20,
            "required_error_families": 3,
            "required_error_rate": 0.05,
            "rationale": "Sufficient ranking headroom observed.",
        },
    )
    dec_ret = compute_p3_development_decision(comp_retained, dev_gold)
    assert dec_ret["decision"] == "RETAINED"
    assert dec_ret["gate_status"] == "RETAINED"
    assert dec_ret["claims_permitted"] is False


def test_provenance_mismatch_rejection(tmp_path, synthetic_benchmark, monkeypatch):
    cases, gold = synthetic_benchmark
    evidence_dir = _create_synthetic_evidence_dir(tmp_path, cases, gold, systems=("P1", "P2"))
    output_dir = tmp_path / "mismatch_report"

    # Mismatched dataset checksum in gold
    mismatched_manifest = SimpleNamespace(
        dataset_id=gold.dataset_id,
        split_id=gold.split.manifest.split_id,
        role=gold.split.manifest.role,
        checksum="9" * 64,
        case_count=gold.split.manifest.case_count,
        family_count=gold.split.manifest.family_count,
        freeze_metadata=gold.split.manifest.freeze_metadata,
    )
    mismatched_bundle = SimpleNamespace(
        schema_version=gold.split.bundle.schema_version,
        split_manifest=mismatched_manifest,
        cases=gold.split.bundle.cases,
    )
    mismatched_split = LoadedSplit(
        bundle=mismatched_bundle,
        source_file_sha256="9" * 64,
    )
    mismatched_gold = GoldSource(
        role=gold.role,
        dataset_id=gold.dataset_id,
        schema_version=gold.schema_version,
        source_file_sha256="9" * 64,
        canonical_sha256="9" * 64,  # mismatched checksum
        catalog_identity=gold.catalog_identity,
        cases=gold.cases,
        split=mismatched_split,
        freeze_identity=gold.freeze_identity,
        p3_gate_identity=gold.p3_gate_identity,
    )

    monkeypatch.setattr(
        "evaluation_v5.analysis.reporting.load_component_gold",
        lambda *args, **kwargs: mismatched_gold,
    )

    with pytest.raises((ReportingError, OfflineEvidenceValidationError), match="offline provenance does not match|evidence (bundle|dataset) checksum mismatch"):
        generate_offline_report(
            evidence_dir=evidence_dir,
            gold_path=tmp_path / "dummy.yaml",
            output_dir=output_dir,
        )


def test_split_mismatch_rejection(tmp_path, synthetic_benchmark, monkeypatch):
    cases, gold = synthetic_benchmark
    evidence_dir = _create_synthetic_evidence_dir(tmp_path, cases, gold, systems=("P1", "P2"))
    output_dir = tmp_path / "split_mismatch_report"

    # Gold role is confirmatory, but evidence was run on development
    mismatched_role_gold = GoldSource(
        role="confirmatory",
        dataset_id=gold.dataset_id,
        schema_version=gold.schema_version,
        source_file_sha256=gold.source_file_sha256,
        canonical_sha256=gold.canonical_sha256,
        catalog_identity=gold.catalog_identity,
        cases=gold.cases,
        split=gold.split,
        freeze_identity=gold.freeze_identity,
        p3_gate_identity=gold.p3_gate_identity,
    )

    monkeypatch.setattr(
        "evaluation_v5.analysis.reporting.load_component_gold",
        lambda *args, **kwargs: mismatched_role_gold,
    )

    with pytest.raises(ReportingError, match="evidence split role mismatch"):
        generate_offline_report(
            evidence_dir=evidence_dir,
            gold_path=tmp_path / "dummy.yaml",
            output_dir=output_dir,
        )


def test_deterministic_reproducible_outputs(tmp_path, synthetic_benchmark, monkeypatch):
    cases, gold = synthetic_benchmark
    evidence_dir = _create_synthetic_evidence_dir(tmp_path, cases, gold, systems=("P1", "P2"))
    out_a = tmp_path / "deterministic_run_a"
    out_b = tmp_path / "deterministic_run_b"

    monkeypatch.setattr(
        "evaluation_v5.analysis.reporting.load_component_gold",
        lambda *args, **kwargs: gold,
    )

    canonical_time = "2026-08-25T08:00:00Z"
    generate_offline_report(
        evidence_dir=evidence_dir,
        gold_path=tmp_path / "dummy.yaml",
        output_dir=out_a,
        created_at_utc=canonical_time,
        bootstrap_seed=42,
    )
    generate_offline_report(
        evidence_dir=evidence_dir,
        gold_path=tmp_path / "dummy.yaml",
        output_dir=out_b,
        created_at_utc=canonical_time,
        bootstrap_seed=42,
    )

    # Compare all files between run_a and run_b
    all_files = list(TABLE_FILES.values()) + list(FIGURE_FILES.values()) + [
        RECOMMENDATION_QUALITY_MD,
        ROBUSTNESS_MD,
        P3_DECISION_MD,
        LIMITATIONS_MD,
        SYNTHESIS_REPORT_FILENAME,
        REPORT_MANIFEST_FILENAME,
    ]
    for rel_path in all_files:
        fa = out_a / rel_path
        fb = out_b / rel_path
        assert fa.is_file(), f"Missing file in run_a: {rel_path}"
        assert fb.is_file(), f"Missing file in run_b: {rel_path}"
        assert fa.read_bytes() == fb.read_bytes(), f"Non-deterministic byte mismatch in {rel_path}"


def test_missing_evidence_limitations_block(tmp_path, synthetic_benchmark, monkeypatch):
    cases, gold = synthetic_benchmark
    evidence_dir = _create_synthetic_evidence_dir(tmp_path, cases, gold, systems=("P1", "P2"))

    # 1. Complete evidence -> missing_evidence is empty list
    out_complete = tmp_path / "complete_report"
    monkeypatch.setattr(
        "evaluation_v5.analysis.reporting.load_component_gold",
        lambda *args, **kwargs: gold,
    )
    generate_offline_report(
        evidence_dir=evidence_dir,
        gold_path=tmp_path / "dummy.yaml",
        output_dir=out_complete,
    )
    lim_json = json.loads((out_complete / REPORT_MANIFEST_FILENAME).read_text())
    lim_md = (out_complete / LIMITATIONS_MD).read_text()
    assert "No missing evidence" in lim_md

    # 2. Explicit missing evidence supplied
    out_missing = tmp_path / "missing_report"
    generate_offline_report(
        evidence_dir=evidence_dir,
        gold_path=tmp_path / "dummy.yaml",
        output_dir=out_missing,
        missing_evidence=["Pending cluster run for E4 (Resource Efficiency)"],
    )
    lim_missing_md = (out_missing / LIMITATIONS_MD).read_text()
    assert "Pending cluster run for E4 (Resource Efficiency)" in lim_missing_md


def test_protocol_v4_protection(tmp_path, synthetic_benchmark, monkeypatch):
    cases, gold = synthetic_benchmark
    evidence_dir = _create_synthetic_evidence_dir(tmp_path, cases, gold, systems=("P1", "P2"))

    root = Path(__file__).resolve().parents[1]
    v4_target = root / "results" / "v4-combined-evidence-test"

    monkeypatch.setattr(
        "evaluation_v5.analysis.reporting.load_component_gold",
        lambda *args, **kwargs: gold,
    )

    with pytest.raises(ReportingError, match="cannot write into historical Protocol-v4 results"):
        generate_offline_report(
            evidence_dir=evidence_dir,
            gold_path=tmp_path / "dummy.yaml",
            output_dir=v4_target,
        )


def test_statistical_prose_hardening_adversarial(tmp_path, synthetic_benchmark):
    cases, dev_gold = synthetic_benchmark
    conf_gold = _make_gold_source(cases, role="confirmatory")

    stat_res_null_cross = SimpleNamespace(
        paired_comparisons=(
            {
                "comparison_id": "P2_minus_P1",
                "endpoint": "joint_accept_at_1",
                "hypothesis_status": "TESTED",
                "statistical_decision": "FAIL_TO_REJECT_NULL",
                "effects": {"mean_difference": 0.05},
                "p_value_raw": 0.04,
                "p_value_holm": 0.08,  # Holm p >= alpha
                "ci_low": -0.02,        # CI crosses zero
                "ci_high": 0.12,
            },
        ),
    )
    comp_res = SimpleNamespace(aggregates={"P2": {}})

    # Case 1: Confirmatory split, but Holm p >= alpha and CI crosses 0 -> no superiority claim
    lim_1 = compute_limitations_block(stat_res_null_cross, comp_res, conf_gold, {}, "OBSERVED")
    assert not any("significantly outperforms" in s for s in lim_1["supported_statistical_statements"])
    assert any("Observed JointAccept@1 mean difference" in s for s in lim_1["supported_statistical_statements"])

    # Case 2: Development split, even if p < alpha and CI > 0 -> claims_permitted is False, no superiority claim
    stat_res_sig = SimpleNamespace(
        paired_comparisons=(
            {
                "comparison_id": "P2_minus_P1",
                "endpoint": "joint_accept_at_1",
                "hypothesis_status": "TESTED",
                "statistical_decision": "REJECT_NULL",
                "effects": {"mean_difference": 0.20},
                "p_value_raw": 0.001,
                "p_value_holm": 0.001,
                "ci_low": 0.08,
                "ci_high": 0.32,
            },
        ),
    )
    lim_dev = compute_limitations_block(stat_res_sig, comp_res, dev_gold, {}, "OBSERVED")
    assert lim_dev["claims_permitted"] is False
    assert not any("significantly outperforms" in s for s in lim_dev["supported_statistical_statements"])

    # Case 3: Confirmatory split + observed evidence + p < alpha + CI > 0 -> superiority claim permitted
    lim_conf = compute_limitations_block(stat_res_sig, comp_res, conf_gold, {}, "OBSERVED")
    assert lim_conf["claims_permitted"] is True
    assert any("P2 significantly outperforms P1 on JointAccept@1" in s for s in lim_conf["supported_statistical_statements"])


def test_robustness_aggregation_semantics_asymmetric(tmp_path, synthetic_benchmark):
    cases, gold = synthetic_benchmark
    records = []
    # Create an intentionally asymmetric fixture:
    # Family 1: 10 paraphrase variants, all pass
    # Family 2: 1 paraphrase variant, fails
    # Macro mean: (1.0 + 0.0) / 2 = 0.50
    # Pooled micro rate: (10 + 0) / 11 = 0.909
    # Worst-case family rate: 1 passing all variants out of 2 families = 0.50
    f1_paraphrase = [c for c in cases if c.family_id == "family_01" and "paraphrase" in c.variant_id][0]
    f2_paraphrase = [c for c in cases if c.family_id == "family_02" and "paraphrase" in c.variant_id][0]

    for i in range(10):
        records.append(_make_raw_record(f1_paraphrase, system_id="P2", predicted_candidate=GOOD_CANDIDATE, repeat_index=i))
    records.append(_make_raw_record(f2_paraphrase, system_id="P2", predicted_candidate=VIOLATING_CANDIDATE, repeat_index=0))

    stat_res = SimpleNamespace(
        stratified_estimates=(
            {"system_id": "P2", "dimension": "variant_stratum", "value": "canonical", "estimate": 1.0, "ci_low": 1.0, "ci_high": 1.0},
            {"system_id": "P2", "dimension": "variant_stratum", "value": "paraphrase", "estimate": 0.50, "ci_low": 0.0, "ci_high": 1.0},
            {"system_id": "P2", "dimension": "variant_stratum", "value": "vietnamese", "estimate": 0.50, "ci_low": 0.0, "ci_high": 1.0},
            {"system_id": "P2", "dimension": "variant_stratum", "value": "noisy", "estimate": 0.50, "ci_low": 0.0, "ci_high": 1.0},
            {"system_id": "P2", "dimension": "variant_stratum", "value": "code_centric", "estimate": 0.50, "ci_low": 0.0, "ci_high": 1.0},
        ),
        system_estimates=(
            {"system_id": "P2", "endpoint": "robustness_rate", "estimate": 0.50, "ci_low": 0.0, "ci_high": 1.0},
        ),
    )

    rob_data = compute_robustness_table_data(stat_res, records, gold)
    p2_row = next(r for r in rob_data["rows"] if r["system_id"] == "P2")
    assert p2_row["overall_srr"]["estimate"] == 0.50  # Equal-weight family macro mean preserved
    assert p2_row["worst_case_family_robustness"]["estimate"] == 0.50  # 1/2 families


def test_error_taxonomy_explicit_denominators():
    comp_res = SimpleNamespace(
        aggregates={
            "P2": {
                "total_recommendations": 100,
                "failed_recommendations": 20,
                "primary_categories": {
                    "EXTRACTION_ERROR": 5,
                    "RETRIEVAL_MISS": 10,
                    "CONSTRAINT_ERROR": 3,
                    "RANKING_ERROR": 2,
                },
            }
        }
    )
    data = compute_error_taxonomy_data(comp_res)
    assert data["total_recommendations"] == 100
    assert data["failed_recommendations"] == 20

    ret_cat = next(c for c in data["categories"] if c["category"] == "RETRIEVAL_MISS")
    assert ret_cat["count"] == 10
    assert ret_cat["fraction_of_failures"] == 0.50  # 10 / 20
    assert ret_cat["fraction_of_total"] == 0.10     # 10 / 100


def test_paired_family_eligibility(synthetic_benchmark):
    cases, gold = synthetic_benchmark
    stat_res = SimpleNamespace(
        family_estimates=(
            {"system_id": "P1", "family_id": "family_01", "values": {"joint_accept_at_1": 0.5}},
            {"system_id": "P2", "family_id": "family_01", "values": {"joint_accept_at_1": 1.0}},
            # family_02 has P1 only
            {"system_id": "P1", "family_id": "family_02", "values": {"joint_accept_at_1": 0.0}},
        )
    )
    paired_data = compute_paired_family_outcomes_data(stat_res, gold)
    assert paired_data["total_families"] == 2
    assert paired_data["eligible_paired_families"] == 1
    assert paired_data["ineligible_unpaired_families"] == 1
    assert paired_data["p2_wins"] == 1

    f1 = next(f for f in paired_data["families"] if f["family_id"] == "family_01")
    assert f1["pairing_status"] == "PAIRED"
    assert f1["delta_P2_minus_P1"] == 0.5

    f2 = next(f for f in paired_data["families"] if f["family_id"] == "family_02")
    assert f2["pairing_status"] == "INELIGIBLE_UNPAIRED"
    assert f2["delta_P2_minus_P1"] is None


def test_not_executed_report(tmp_path):
    out_dir = tmp_path / "not_executed_report"
    write_not_executed_report(
        out_dir,
        reason="Real Prompt-5 evidence not executed.",
        reason_code="EVIDENCE_NOT_EXECUTED",
    )

    manifest_path = out_dir / REPORT_MANIFEST_FILENAME
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "NOT_EXECUTED"
    assert manifest["claims_permitted"] is False

    synthesis_path = out_dir / SYNTHESIS_REPORT_FILENAME
    assert synthesis_path.is_file()
    synthesis = synthesis_path.read_text()
    assert "NOT_EXECUTED" in synthesis
    assert "Real Prompt-5 evidence not executed." in synthesis


def test_main_cli_status_only(tmp_path):
    out_dir = tmp_path / "cli_status_only"
    ret = main(["--status-only", "--output-dir", str(out_dir)])
    assert ret == 0
    assert (out_dir / REPORT_MANIFEST_FILENAME).is_file()
    manifest = json.loads((out_dir / REPORT_MANIFEST_FILENAME).read_text())
    assert manifest["status"] == "NOT_EXECUTED"


def test_exclusive_directory_creation_refuses_overwrite(tmp_path, synthetic_benchmark, monkeypatch):
    cases, gold = synthetic_benchmark
    evidence_dir = _create_synthetic_evidence_dir(tmp_path, cases, gold, systems=("P1", "P2"))
    output_dir = tmp_path / "no_overwrite_report"
    output_dir.mkdir()

    monkeypatch.setattr(
        "evaluation_v5.analysis.reporting.load_component_gold",
        lambda *args, **kwargs: gold,
    )

    with pytest.raises(FileExistsError):
        generate_offline_report(
            evidence_dir=evidence_dir,
            gold_path=tmp_path / "dummy.yaml",
            output_dir=output_dir,
        )


def test_retrieval_ablation_labels_and_formatting(tmp_path, synthetic_benchmark, monkeypatch):
    cases, gold = synthetic_benchmark
    evidence_dir = _create_synthetic_evidence_dir(tmp_path, cases, gold, systems=("P1", "P2"))
    output_dir = tmp_path / "ablation_report"

    monkeypatch.setattr(
        "evaluation_v5.analysis.reporting.load_component_gold",
        lambda *args, **kwargs: gold,
    )

    generate_offline_report(
        evidence_dir=evidence_dir,
        gold_path=tmp_path / "dummy.yaml",
        output_dir=output_dir,
    )

    csv_path = output_dir / TABLE_FILES["retrieval_ablation"]
    assert csv_path.is_file()
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "channel,is_ablation,is_proposed,k,recall_at_k" in csv_text
    assert "Sparse,True,False" in csv_text
    assert "Dense,True,False" in csv_text
    assert "Hybrid,False,True" in csv_text

    svg_path = output_dir / FIGURE_FILES["retrieval_recall_at_k"]
    svg_text = svg_path.read_text(encoding="utf-8")
    assert "Sparse" in svg_text
    assert "Dense" in svg_text
    assert "Hybrid" in svg_text
    assert "These are P2 ablations, not primary system IDs." in svg_text


def test_main_cli_end_to_end(tmp_path, synthetic_benchmark, monkeypatch):
    cases, gold = synthetic_benchmark
    evidence_dir = _create_synthetic_evidence_dir(tmp_path, cases, gold, systems=("P1", "P2"))
    output_dir = tmp_path / "cli_e2e_report"

    monkeypatch.setattr(
        "evaluation_v5.analysis.reporting.load_component_gold",
        lambda *args, **kwargs: gold,
    )

    ret = main([
        "--evidence-dir", str(evidence_dir),
        "--gold-dataset", str(tmp_path / "dummy.yaml"),
        "--output-dir", str(output_dir),
        "--role", "development",
        "--bootstrap-replicates", "50",
    ])
    assert ret == 0
    assert (output_dir / REPORT_MANIFEST_FILENAME).is_file()
    manifest = json.loads((output_dir / REPORT_MANIFEST_FILENAME).read_text())
    assert manifest["status"] == "REPORT_COMPLETE"
    assert manifest["claims_permitted"] is False  # development split does not permit final confirmatory claims
    assert (output_dir / SYNTHESIS_REPORT_FILENAME).is_file()


