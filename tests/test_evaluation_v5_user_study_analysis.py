"""SYNTHETIC-only tests for the Protocol-v5 E3 analysis pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import jsonschema
import pytest

from evaluation_v5.user_study import analysis as analysis_module
from evaluation_v5.user_study.analysis import (
    UserStudyAnalysisError,
    analyze_user_study,
    audit_report_privacy,
    write_analysis_artifacts,
)
from evaluation_v5.user_study.assignment import generate_assignment_manifest
from evaluation_v5.user_study.hub import StudyGateStore, StudyHubError
from evaluation_v5.user_study.questionnaires import (
    CUSTOM_ITEM_IDS,
    FINAL_PREFERENCE_ID,
    QUESTIONNAIRE_INSTRUMENT_VERSION,
    QUESTIONNAIRE_INSTRUMENT_SHA256,
    QUESTIONNAIRE_SCHEMA_VERSION,
    QUESTIONNAIRE_SCHEMA_SHA256,
    SEQ_ITEM_ID,
    SUS_ITEM_IDS,
    QuestionnaireValidationError,
    derive_questionnaire_outcomes,
    expected_questionnaire_ids,
    score_sus,
    validate_questionnaire_record,
    validate_questionnaire_stream,
)
from evaluation_v5.user_study.runner import (
    EXCLUSION_REASONS,
    UserStudyRunnerError,
    _validate_complete_sessions,
    main as user_study_main,
)
from evaluation_v5.user_study.schemas import (
    EVENT_SCHEMA_VERSION,
    CancelReason,
    EventType,
    browser_safe_task_set,
    load_task_set,
    validate_event,
)


ROOT = Path(__file__).resolve().parents[1]
TASK_SET_PATH = ROOT / "benchmarks_v5" / "user-study-draft-v1.yaml"
QUESTIONNAIRE_SCHEMA_PATH = (
    ROOT / "benchmarks_v5" / "protocol-v5-user-study-questionnaire-v1.schema.json"
)


@pytest.fixture()
def synthetic_assignment():
    return generate_assignment_manifest(
        load_task_set(TASK_SET_PATH),
        study_id="e3-analysis-synthetic",
        participant_count=2,
        seed=20260827,
        consent_version="consent-synthetic-v1",
        generated_at_utc="2026-08-27T00:00:00Z",
    )


def _record(assignment, participant, kind, questionnaire_id, **scope):
    if kind == "seq_task":
        responses = {SEQ_ITEM_ID: 6}
    elif kind == "post_condition":
        responses = {item: (5 if index % 2 else 1) for index, item in enumerate(SUS_ITEM_IDS, start=1)}
        responses.update({item: 6 for item in CUSTOM_ITEM_IDS})
    else:
        responses = {FINAL_PREFERENCE_ID: "NO_PREFERENCE"}
    return {
        "schema_version": QUESTIONNAIRE_SCHEMA_VERSION,
        "instrument_version": QUESTIONNAIRE_INSTRUMENT_VERSION,
        "study_id": assignment.study_id,
        "assignment_id": assignment.assignment_id,
        "session_id": participant.session_id,
        "participant_id": participant.participant_id,
        "response_uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, participant.session_id + questionnaire_id)),
        "questionnaire_type": kind,
        "questionnaire_id": questionnaire_id,
        "condition": scope.get("condition"),
        "period": scope.get("period"),
        "trial_id": scope.get("trial_id"),
        "task_id": scope.get("task_id"),
        "pair_id": scope.get("pair_id"),
        "responses": responses,
        "submitted_at_utc": "2026-08-27T00:00:00Z",
    }


def _complete_questionnaires(assignment, participant):
    rows = []
    for task in participant.task_sequence:
        if task.phase.value != "measured":
            continue
        rows.append(
            _record(
                assignment,
                participant,
                "seq_task",
                f"seq:{task.trial_id}",
                condition=task.condition.value,
                period=task.period,
                trial_id=task.trial_id,
                task_id=task.task_id,
                pair_id=task.pair_id,
            )
        )
    for period, condition in enumerate(participant.condition_order, start=1):
        rows.append(
            _record(
                assignment,
                participant,
                "post_condition",
                f"post_condition:{period}",
                condition=condition.value,
                period=period,
            )
        )
    rows.append(_record(assignment, participant, "final_preference", "final_preference"))
    return rows


def test_questionnaire_schema_sus_and_missing_policy(synthetic_assignment):
    assert hashlib.sha256(QUESTIONNAIRE_SCHEMA_PATH.read_bytes()).hexdigest() == QUESTIONNAIRE_SCHEMA_SHA256
    assert EXCLUSION_REASONS == {
        "consent_withdrawal",
        "duplicate_or_invalid_assignment",
        "checksum_or_protocol_drift",
        "instrumentation_corruption",
    }
    assert synthetic_assignment.questionnaire_schema_sha256 == QUESTIONNAIRE_SCHEMA_SHA256
    assert synthetic_assignment.questionnaire_instrument_sha256 == QUESTIONNAIRE_INSTRUMENT_SHA256
    participant = synthetic_assignment.assignments[0]
    rows = _complete_questionnaires(synthetic_assignment, participant)
    schema = json.loads(QUESTIONNAIRE_SCHEMA_PATH.read_text())
    for row in rows:
        jsonschema.Draft202012Validator(schema).validate(row)
    parsed = validate_questionnaire_stream(rows, synthetic_assignment)
    assert len(parsed) == 9
    assert {row.questionnaire_id for row in parsed} == expected_questionnaire_ids(participant)
    post = next(row for row in parsed if row.questionnaire_type.value == "post_condition")
    assert score_sus(post.responses) == 100.0
    missing = dict(post.responses)
    missing["sus_01"] = None
    assert score_sus(missing) is None

    invalid = json.loads(json.dumps(rows[0]))
    invalid["responses"][SEQ_ITEM_ID] = 8
    with pytest.raises(QuestionnaireValidationError, match="1..7"):
        validate_questionnaire_record(invalid)
    private = json.loads(json.dumps(rows[0]))
    private["responses"]["comment"] = "person@example.test"
    with pytest.raises(QuestionnaireValidationError, match="fields"):
        validate_questionnaire_record(private)
    with pytest.raises(QuestionnaireValidationError, match="duplicate"):
        validate_questionnaire_stream([rows[0], rows[0]], synthetic_assignment)

    skipped = json.loads(json.dumps(next(
        row for row in rows if row["questionnaire_type"] == "post_condition"
    )))
    skipped["responses"] = {key: None for key in skipped["responses"]}
    parsed_skip = validate_questionnaire_record(skipped)
    derived_skip = derive_questionnaire_outcomes([parsed_skip])[0]
    assert derived_skip["answered_item_count"] == 0
    assert derived_skip["missing_item_count"] == 13
    assert derived_skip["sus_score"] is None


def test_questionnaire_append_is_idempotent_and_cli_validates(
    tmp_path, synthetic_assignment, capsys
):
    participant = synthetic_assignment.assignments[0]
    rows = _complete_questionnaires(synthetic_assignment, participant)
    gates = StudyGateStore(tmp_path / "staging")
    first = gates.append_questionnaire(rows[0])
    assert gates.append_questionnaire(rows[0]) == first
    conflict = json.loads(json.dumps(rows[0]))
    conflict["response_uuid"] = str(uuid.uuid4())
    with pytest.raises(StudyHubError, match="already submitted"):
        gates.append_questionnaire(conflict)

    assignment_path = tmp_path / "assignment.json"
    questionnaire_path = tmp_path / "questionnaires.jsonl"
    assignment_path.write_text(json.dumps(synthetic_assignment.to_dict()) + "\n")
    questionnaire_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert user_study_main(
        [
            "validate-questionnaires",
            str(questionnaire_path),
            "--assignments",
            str(assignment_path),
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "VALID"
    assert result["record_count"] == 9


def _terminal_events(assignment, participant):
    events = []
    for task_index, task in enumerate(participant.task_sequence):
        for event_index, event_type in enumerate((EventType.TASK_SHOWN, EventType.CANCEL)):
            payload = {
                "schema_version": EVENT_SCHEMA_VERSION,
                "study_id": assignment.study_id,
                "assignment_id": assignment.assignment_id,
                "session_id": participant.session_id,
                "participant_id": participant.participant_id,
                "trial_id": task.trial_id,
                "task_id": task.task_id,
                "pair_id": task.pair_id,
                "condition": task.condition.value,
                "consent_version": assignment.consent_version,
                "event_uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"complete:{task.trial_id}:{event_type.value}")),
                "event_index": event_index,
                "timestamp_utc": f"2026-08-27T00:{task_index:02d}:0{event_index}Z",
                "monotonic_seconds": float(event_index),
                "event_type": event_type.value,
                "profile_id": None,
                "image_id": None,
                "old_profile_id": None,
                "new_profile_id": None,
                "old_image_id": None,
                "new_image_id": None,
                "preview_status": None,
                "cancel_reason": (
                    CancelReason.PARTICIPANT_CANCELLED.value
                    if event_type is EventType.CANCEL
                    else None
                ),
            }
            events.append(validate_event(payload))
    return events


def test_complete_session_requires_all_nine_scheduled_forms(synthetic_assignment):
    participant = synthetic_assignment.assignments[0]
    session = {
        "session_id": participant.session_id,
        "session_status": "complete",
    }
    events = _terminal_events(synthetic_assignment, participant)
    questionnaires = validate_questionnaire_stream(
        _complete_questionnaires(synthetic_assignment, participant),
        synthetic_assignment,
    )
    with pytest.raises(UserStudyRunnerError, match="scheduled questionnaire"):
        _validate_complete_sessions(
            [session], events, synthetic_assignment, set(), questionnaires[:-1]
        )
    assert _validate_complete_sessions(
        [session], events, synthetic_assignment, set(), questionnaires
    ) == {participant.session_id}


def _synthetic_analysis_rows(participant_count: int = 12):
    tasks = []
    questionnaires = []
    pairs = ("pair-light", "pair-data", "pair-ml")
    for participant in range(participant_count):
        participant_id = f"P-{participant:012x}"
        order = "B0-then-P2" if participant % 2 == 0 else "P2-then-B0"
        for pair_index, pair_id in enumerate(pairs):
            for condition in ("B0", "P2"):
                success = ((participant + pair_index + (condition == "P2")) % 3) != 0
                tasks.append(
                    {
                        "participant_id": participant_id,
                        "trial_id": f"trial-{participant}-{pair_index}-{condition}",
                        "pair_id": pair_id,
                        "condition": condition,
                        "variant_slot": (participant + (condition == "P2")) % 2,
                        "period": 1 if order.startswith(condition) else 2,
                        "condition_order": order,
                        "selection_success": success,
                        "confirmed": True,
                        "decision_time_seconds": 45 + participant + pair_index - (8 if condition == "P2" else 0),
                        "total_action_count": 5 + pair_index - (1 if condition == "P2" else 0),
                        "interaction_count": 5 + pair_index - (1 if condition == "P2" else 0),
                        "correction_count": (participant + pair_index + (condition == "B0")) % 2,
                        "notebook_ready_observed": True,
                        "end_to_end_seconds": 70 + participant + pair_index - (7 if condition == "P2" else 0),
                        "final_override": False if condition == "P2" else None,
                    }
                )
                questionnaires.append(
                    {
                        "questionnaire_type": "seq_task",
                        "participant_id": participant_id,
                        "trial_id": f"trial-{participant}-{pair_index}-{condition}",
                        "pair_id": pair_id,
                        "condition": condition,
                        "period": 1 if order.startswith(condition) else 2,
                        SEQ_ITEM_ID: 4 + int(condition == "P2"),
                    }
                )
        for condition in ("B0", "P2"):
            questionnaires.append(
                {
                    "questionnaire_type": "post_condition",
                    "participant_id": participant_id,
                    "condition": condition,
                    "period": 1 if order.startswith(condition) else 2,
                    "sus_score": 65 + 5 * int(condition == "P2") + participant % 3,
                    **{item: 4 + int(condition == "P2") for item in CUSTOM_ITEM_IDS},
                }
            )
        questionnaires.append(
            {
                "questionnaire_type": "final_preference",
                "participant_id": participant_id,
                FINAL_PREFERENCE_ID: ("P2", "B0", "NO_PREFERENCE")[participant % 3],
            }
        )
    return tasks, questionnaires


def test_synthetic_analysis_emits_effects_intervals_and_aggregate_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis_module, "BOOTSTRAP_REPLICATES", 20)
    tasks, questionnaires = _synthetic_analysis_rows()
    questionnaires[-1][FINAL_PREFERENCE_ID] = None
    analysis = analyze_user_study(
        execution_status="DRY_RUN",
        task_rows=tasks,
        questionnaire_rows=questionnaires,
        sessions=[],
        exclusions=[],
        assignment_manifest=SimpleNamespace(participant_count=12),
    )
    assert analysis["synthetic_only"] is True
    assert analysis["effects"]["selection_success"]["risk_difference"] is not None
    assert analysis["effects"]["decision_time_seconds"]["raw_paired_effect"]["confidence_interval_95"] != [None, None]
    assert analysis["effects"]["holm_family"]["family"] == ["selection_success", "decision_time_seconds"]
    assert next(
        row for row in analysis["condition_summary"] if row["condition"] == "B0"
    )["final_override_rate_among_confirmed"] is None
    assert len(analysis["preference"]) == 3
    assert {row["answered_denominator"] for row in analysis["preference"]} == {11}
    assert {row["missing_response_count"] for row in analysis["preference"]} == {1}
    paths = write_analysis_artifacts(tmp_path, analysis)
    assert len([path for path in paths if path.suffix == ".svg"]) == 5
    assert (tmp_path / "report" / "privacy-audit.json").is_file()
    assert (tmp_path / "derived" / "model-effects.json").is_file()
    report_manifest = json.loads(
        (tmp_path / "report" / "analysis-manifest.json").read_text()
    )
    assert "report/privacy-audit.json" in report_manifest["generated_files"]
    assert "P-000000000000" not in "".join(path.read_text() for path in paths if path.is_file())


def test_zero_time_uses_predeclared_robust_fallback(monkeypatch):
    monkeypatch.setattr(analysis_module, "BOOTSTRAP_REPLICATES", 20)
    tasks, questionnaires = _synthetic_analysis_rows()
    tasks[0]["decision_time_seconds"] = 0.0
    analysis = analyze_user_study(
        execution_status="DRY_RUN",
        task_rows=tasks,
        questionnaire_rows=questionnaires,
        sessions=[],
        exclusions=[],
        assignment_manifest=SimpleNamespace(participant_count=12),
    )
    decision = analysis["effects"]["decision_time_seconds"]
    assert decision["status"] == "FALLBACK"
    assert decision["fallback_reason"] == "nonpositive_time"
    assert decision["geometric_mean_ratio"] is None


@pytest.mark.parametrize(
    "payload",
    [
        '{"participant_id":"P-000000000000","email":"person@example.test"}\n',
        '{"comment":"participant supplied prose"}\n',
        '{"source":"/Users/example/private/export.jsonl"}\n',
        "participant_id,score\nP-000000000000,7\n",
    ],
)
def test_privacy_audit_rejects_direct_identifiers(tmp_path, payload):
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text(payload)
    with pytest.raises(UserStudyAnalysisError, match="privacy audit failed"):
        audit_report_privacy([unsafe])


def test_not_executed_finalization_generates_full_placeholder_surface(
    tmp_path, synthetic_assignment, capsys
):
    assignment_path = tmp_path / "assignment.json"
    assignment_path.write_text(json.dumps(synthetic_assignment.to_dict()) + "\n")
    output = tmp_path / "not-executed"
    assert user_study_main(
        [
            "finalize",
            "--run-id",
            "synthetic-not-executed-contract-test",
            "--task-set",
            str(TASK_SET_PATH),
            "--assignments",
            str(assignment_path),
            "--execution-status",
            "NOT_EXECUTED",
            "--output-dir",
            str(output),
            "--created-at-utc",
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        ]
    ) == 0
    capsys.readouterr()
    assert "NOT_EXECUTED" in (output / "report" / "USER_STUDY_REPORT.md").read_text()
    assert len(list((output / "report" / "figures").glob("*.svg"))) == 5
    derived = json.loads((output / "derived" / "analysis.json").read_text())
    assert derived["condition_summary"] == []
    assert derived["effects"] == {}
    provenance = json.loads((output / "manifest.json").read_text())
    assert provenance["contracts"]["analysis_plan_sha256"] == synthetic_assignment.analysis_plan_sha256
    assert provenance["contracts"]["questionnaire_schema_version"] == synthetic_assignment.questionnaire_schema_version
    assert provenance["runtime"]["analysis_dependencies"] == analysis_module.PINNED_ANALYSIS_DEPENDENCIES
