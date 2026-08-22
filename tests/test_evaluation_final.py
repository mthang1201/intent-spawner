from __future__ import annotations

import json

import pytest

from evaluation_final.analysis import analyze_rq1, analyze_rq2, analyze_rq3
from evaluation_final.runner import create_analysis_run, create_frozen_run
from evaluation_final.schemas import (
    RQ1_EVENT_SCHEMA_VERSION,
    RQ1_PROTOCOL_VERSION,
    RQ1_TASK_SET_SCHEMA_VERSION,
)
from evaluation_final.systems import (
    PRIMARY_SYSTEM_IDS,
    active_primary_system_ids,
    system_registry,
)


def test_primary_registry_is_closed_and_p3_is_gate_conditional():
    excluded = system_registry("not_retained")
    retained = system_registry("retained")

    assert PRIMARY_SYSTEM_IDS == ("B0", "P1", "P2", "P3")
    assert tuple(excluded["systems"]) == PRIMARY_SYSTEM_IDS
    assert active_primary_system_ids("not_retained") == ("B0", "P1", "P2")
    assert active_primary_system_ids("retained") == PRIMARY_SYSTEM_IDS
    assert excluded["systems"]["P2"]["secondary_ablations"] == [
        "sparse_only",
        "dense_only",
    ]
    assert excluded["classification_rules"][
        "direct_external_or_local_llm_experiments"
    ] == "historical_reference_only"
    assert retained["systems"]["P3"]["active_in_final_evaluation"] is True


def _rq1_event(
    participant: str,
    system: str,
    session: str,
    index: int,
    elapsed: float,
    event_type: str,
    candidate_id: str | None,
):
    return {
        "schema_version": RQ1_EVENT_SCHEMA_VERSION,
        "study_id": "study-v1",
        "protocol_version": RQ1_PROTOCOL_VERSION,
        "participant_id": participant,
        "session_id": session,
        "task_id": "task-1",
        "system_id": system,
        "event_index": index,
        "elapsed_seconds": elapsed,
        "event_type": event_type,
        "candidate_id": candidate_id,
    }


def test_rq1_uses_user_facing_measures_and_never_ranking_metrics_for_b0():
    tasks = {
        "schema_version": RQ1_TASK_SET_SCHEMA_VERSION,
        "protocol_version": RQ1_PROTOCOL_VERSION,
        "task_set_id": "tasks-v1",
        "frozen_at_utc": "2026-08-22T00:00:00Z",
        "tasks": [
            {
                "task_id": "task-1",
                "task_version": "task-1-v1",
                "workload_family": "tabular",
                "acceptable_candidate_ids": ["small-scipy"],
                "preferred_candidate_id": "small-scipy",
            }
        ],
    }
    events = []
    outcomes = {
        ("participant-1", "B0"): "small-minimal",
        ("participant-1", "P2"): "small-scipy",
        ("participant-2", "B0"): "small-scipy",
        ("participant-2", "P2"): "small-scipy",
    }
    for participant_index, participant in enumerate(("participant-1", "participant-2")):
        for system in ("B0", "P2"):
            session = f"session-{participant_index}-{system}"
            candidate = outcomes[(participant, system)]
            events.extend(
                [
                    _rq1_event(participant, system, session, 0, 0, "study_started", None),
                    _rq1_event(
                        participant,
                        system,
                        session,
                        1,
                        10 + participant_index,
                        "candidate_selected",
                        candidate,
                    ),
                    _rq1_event(
                        participant,
                        system,
                        session,
                        2,
                        20 + participant_index,
                        "task_completed",
                        candidate,
                    ),
                ]
            )

    metrics = analyze_rq1(
        tasks,
        events,
        p3_gate_status="not_retained",
        bootstrap_replicates=50,
        bootstrap_seed=7,
    )

    assert metrics["systems"]["B0"]["correct_environment_selection"]["value"] == 0.5
    assert metrics["systems"]["P2"]["correct_environment_selection"]["value"] == 1.0
    assert "P2" in metrics["paired_comparisons_against_B0"]
    serialized = json.dumps(metrics).lower()
    for forbidden in ("top1", "mrr", "ndcg", "reciprocal_rank"):
        assert forbidden not in serialized


def _rq2_fixture():
    dataset = {
        "dataset_sha256": "d" * 64,
        "items": [
            {
                "sample_id": "feasible",
                "workload_family": "family-a",
                "gold": {
                    "request_feasible": True,
                    "preferred_candidate_id": "good",
                    "acceptable_candidate_ids": ["good"],
                },
            },
            {
                "sample_id": "unsupported",
                "workload_family": "family-b",
                "gold": {
                    "request_feasible": False,
                    "preferred_candidate_id": None,
                    "acceptable_candidate_ids": [],
                },
            },
        ],
    }
    predictions = [
        {
            "system": "p1",
            "sample_id": "feasible",
            "ranked_candidate_ids": ["bad"],
            "retrieved_candidate_ids": ["bad"],
            "feasible_candidate_ids": ["bad"],
            "detected_infeasible": False,
            "constraint_violated": True,
            "latency_seconds": 0.1,
            "fallback_used": False,
            "fallback_category": None,
            "policy_compliant": True,
        },
        {
            "system": "p2",
            "sample_id": "feasible",
            "ranked_candidate_ids": ["good"],
            "retrieved_candidate_ids": ["good"],
            "feasible_candidate_ids": ["good"],
            "detected_infeasible": False,
            "constraint_violated": False,
            "latency_seconds": 0.2,
            "fallback_used": False,
            "fallback_category": None,
            "policy_compliant": True,
        },
        {
            "system": "p1",
            "sample_id": "unsupported",
            "ranked_candidate_ids": ["bad"],
            "retrieved_candidate_ids": ["bad"],
            "feasible_candidate_ids": ["bad"],
            "detected_infeasible": False,
            "constraint_violated": True,
            "latency_seconds": 0.1,
            "fallback_used": False,
            "fallback_category": None,
            "policy_compliant": True,
        },
        {
            "system": "p2",
            "sample_id": "unsupported",
            "ranked_candidate_ids": [],
            "retrieved_candidate_ids": [],
            "feasible_candidate_ids": [],
            "detected_infeasible": True,
            "constraint_violated": True,
            "latency_seconds": 0.2,
            "fallback_used": True,
            "fallback_category": "unsupported_catalog",
            "policy_compliant": True,
        },
    ]
    ablations = [
        {**record, "ablation_id": "dense_only"}
        for record in predictions
        if record["system"] == "p2"
    ]
    return dataset, predictions, ablations


def test_rq2_is_paired_and_keeps_retrieval_variants_under_p2():
    dataset, predictions, ablations = _rq2_fixture()

    result = analyze_rq2(
        dataset,
        predictions,
        ablation_predictions=ablations,
        bootstrap_replicates=50,
        bootstrap_seed=9,
    )

    assert result["primary_systems"] == ["P1", "P2"]
    assert result["systems"]["P2"]["top1_accuracy"]["value"] == 1.0
    assert result["systems"]["P2"]["unsupported_request_detection"]["recall"] == 1.0
    assert result["paired_P2_versus_P1"]["metrics"]["query_correct"][
        "mean_difference"
    ] == 1.0
    assert result["P2_failure_categories"]["counts"] == {
        "no_error": 1,
        "unsupported_catalog": 1,
    }
    assert set(result["P2_ablations"]["variants"]) == {"dense_only"}
    assert "dense_only" not in result["primary_systems"]


def test_rq3_emits_no_metrics_and_rejects_observations_after_failed_gate():
    dataset, predictions, _ = _rq2_fixture()
    status = analyze_rq3(
        dataset,
        None,
        p3_gate_status="not_retained",
        gate_evidence={"status": "not_retained", "evidence_sha256": "e" * 64},
    )

    assert status["status"] == "not_applicable_after_gate"
    assert status["metrics_generated"] is False
    assert "paired_metrics" not in status
    with pytest.raises(ValueError, match="non-retained gate"):
        analyze_rq3(
            dataset,
            predictions,
            p3_gate_status="not_retained",
            gate_evidence={"status": "not_retained"},
        )


def test_freeze_and_empty_analysis_are_versioned_non_overwriting_and_non_fabricating(
    tmp_path,
):
    freeze = create_frozen_run(
        output_root=tmp_path,
        run_id="final-freeze-test",
        p3_gate_status="not_retained",
    )
    manifest = json.loads((freeze / "freeze-manifest.json").read_text())
    status = json.loads((freeze / "interpretation/status.json").read_text())

    assert manifest["allowed_primary_system_ids"] == ["B0", "P1", "P2", "P3"]
    assert manifest["active_primary_system_ids"] == ["B0", "P1", "P2"]
    assert manifest["frozen_inputs"]["dataset"]["sample_count"] == 66
    assert manifest["frozen_inputs"]["prompts"]["P2_extractor"]["sha256"]
    assert manifest["frozen_inputs"]["retrieval_and_indexes"]["hybrid"][
        "index_checksum"
    ]
    assert manifest["frozen_inputs"]["constraint_rules"]["policy_version"]
    assert status["claims_permitted"] is False
    assert status["real_user_study_executed"] is False

    with pytest.raises(FileExistsError):
        create_frozen_run(
            output_root=tmp_path,
            run_id="final-freeze-test",
            p3_gate_status="not_retained",
        )

    analysis = create_analysis_run(
        freeze_directory=freeze,
        output_root=tmp_path,
        run_id="final-analysis-test",
        bootstrap_replicates=10,
    )
    rq1 = json.loads((analysis / "derived/RQ1.json").read_text())
    rq2 = json.loads((analysis / "derived/RQ2.json").read_text())
    rq3 = json.loads((analysis / "derived/RQ3.json").read_text())
    assert rq1["metrics_generated"] is False
    assert rq2["metrics_generated"] is False
    assert rq3["status"] == "not_applicable_after_gate"
    assert rq3["metrics_generated"] is False
