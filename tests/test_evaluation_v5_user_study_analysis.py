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
from evaluation_v5.user_study import runner as runner_module
from evaluation_v5.user_study.analysis import (
    UserStudyAnalysisError,
    analyze_user_study,
    audit_report_privacy,
    write_analysis_artifacts,
)
from evaluation_v5.user_study.assignment import (
    AssignmentManifest,
    generate_assignment_manifest,
)
from evaluation_v5.user_study.hub import StudyGateStore, StudyHubError
from evaluation_v5.user_study.questionnaires import (
    ANALYSIS_PLAN,
    ANALYSIS_PLAN_SHA256,
    ANALYSIS_PLAN_VERSION,
    CUSTOM_ITEM_IDS,
    FINAL_PREFERENCE_ID,
    QUESTIONNAIRE_INSTRUMENT_VERSION,
    QUESTIONNAIRE_INSTRUMENT_SHA256,
    QUESTIONNAIRE_SCHEMA_VERSION,
    QUESTIONNAIRE_SCHEMA_SHA256,
    SEQ_ITEM_ID,
    SUS_ITEMS,
    SUS_ITEM_IDS,
    QuestionnaireValidationError,
    derive_questionnaire_outcomes,
    expected_questionnaire_ids,
    score_sus,
    validate_questionnaire_record,
    validate_questionnaire_stream,
)
from evaluation_v5.user_study.runner import (
    EXCLUSION_REASON_VERSION,
    EXCLUSION_REASONS,
    EXCLUSION_SCHEMA_VERSION,
    UserStudyRunnerError,
    _validate_complete_sessions,
    _validate_exclusions,
    main as user_study_main,
)
from evaluation_v5.user_study.schemas import (
    EVENT_SCHEMA_VERSION,
    STUDY_TIMING_CONTRACT,
    STUDY_TIMING_CONTRACT_SHA256,
    STUDY_TIMING_CONTRACT_VERSION,
    CancelReason,
    EventType,
    UserStudyValidationError,
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
    assert QUESTIONNAIRE_INSTRUMENT_SHA256 == "5b651d19628d15ef2afa4365cad9eaab5ab2196ea86f84389b09d5d24a000db5"
    assert SUS_ITEMS == (
        "I think that I would like to use this system frequently.",
        "I found the system unnecessarily complex.",
        "I thought the system was easy to use.",
        "I think that I would need the support of a technical person to be able to use this system.",
        "I found the various functions in this system were well integrated.",
        "I thought there was too much inconsistency in this system.",
        "I would imagine that most people would learn to use this system very quickly.",
        "I found the system very cumbersome to use.",
        "I felt very confident using the system.",
        "I needed to learn a lot of things before I could get going with this system.",
    )
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
    assert score_sus({item: 3 for item in SUS_ITEM_IDS}) == 50.0
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


def test_complete_session_requires_every_form_derived_from_schedule(synthetic_assignment):
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
    measured_count = sum(
        task.phase.value == "measured" for task in participant.task_sequence
    )
    assert len(questionnaires) == measured_count + len(participant.condition_order) + 1
    assert len(questionnaires) == 9  # consequence of the current frozen schedule
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
                condition_p2 = int(condition == "P2")
                success = (
                    (participant * 7 + pair_index * 3 + condition_p2 * 5) % 13
                ) < (8 + condition_p2)
                interaction_count = 3 + (
                    (participant * 5 + pair_index * 3 + condition_p2 * 2) % 7
                )
                decision_time = (
                    40
                    + participant * 2
                    + pair_index * 3
                    - condition_p2 * 5
                    + ((participant * 3 + pair_index * 5 + condition_p2 * 7) % 11)
                )
                notebook_ready_time = (
                    20
                    + participant * 1.5
                    + pair_index * 2
                    - condition_p2 * 2
                    + ((participant * 5 + pair_index * 2 + condition_p2 * 3) % 7)
                )
                tasks.append(
                    {
                        "participant_id": participant_id,
                        "trial_id": f"trial-{participant}-{pair_index}-{condition}",
                        "pair_id": pair_id,
                        "condition": condition,
                        "variant_slot": ((participant // 2) + condition_p2) % 2,
                        "period": 1 if order.startswith(condition) else 2,
                        "condition_order": order,
                        "selection_success": success,
                        "confirmed": True,
                        "decision_time_seconds": decision_time,
                        "decision_time_unavailability_reason": None,
                        "decision_time_timeout_sensitivity_seconds": decision_time,
                        "total_action_count": interaction_count,
                        "interaction_count": interaction_count,
                        "correction_count": (
                            participant * 3 + pair_index * 2 + condition_p2
                        ) % 4,
                        "notebook_ready_observed": True,
                        "notebook_ready_time_seconds": notebook_ready_time,
                        "notebook_ready_time_status": "available",
                        "end_to_end_seconds": decision_time + notebook_ready_time,
                        "end_to_end_status": "available",
                        "position": pair_index + 1,
                        "counterbalance_cell": f"cell-{participant % 12:02d}",
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
                        SEQ_ITEM_ID: min(
                            7,
                            1
                            + (participant % 4)
                            + condition_p2
                            + ((participant + pair_index) % 3),
                        ),
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
    assert analysis["effects"]["decision_time_seconds"]["status"] == "MODELED"
    assert analysis["effects"]["decision_time_seconds"]["estimand"] == (
        ANALYSIS_PLAN["decision_time_seconds"]["estimand"]
    )
    assert analysis["effects"]["decision_time_seconds"][
        "primary_timeout_or_pseudotime_policy"
    ] == "none"
    assert analysis["effects"]["seq_ease"]["status"] == "MODELED"
    assert analysis["effects"]["holm_family"]["family"] == ["selection_success", "decision_time_seconds"]
    registry = analysis["primary_inference_registry"]
    assert registry["family_size"] == 2
    assert [row["endpoint"] for row in registry["hypotheses"]] == [
        "selection_success",
        "decision_time_seconds",
    ]
    selection = analysis["effects"]["selection_success"]
    assert selection["risk_difference_estimand"].startswith("average_over_observed")
    assert selection["risk_difference"] != pytest.approx(
        __import__("math").log(selection["odds_ratio"])
    )
    count = analysis["effects"]["interaction_count"]
    assert count["actual_method"] == "participant_clustered_quasipoisson_gee_exchangeable"
    assert count["dispersion_method"] == "pearson_scale_estimated_from_fit"
    assert count["dispersion_scale"] > 0
    assert analysis["effects"]["notebook_ready_time"]["clock_definition"].startswith(
        "notebook_ready_timestamp_minus_confirmed"
    )
    assert analysis["effects"]["end_to_end_launch_time"]["clock_definition"].endswith(
        "task_shown_timestamp"
    )
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
    assert report_manifest["analysis_dependencies"] == analysis_module.PINNED_ANALYSIS_DEPENDENCIES
    assert report_manifest["supported_python"] == ">=3.12,<3.15"
    assert report_manifest["pinned_analysis_dependency_requires_python"] == (
        analysis_module.PINNED_ANALYSIS_REQUIRES_PYTHON
    )
    assert report_manifest["decision_time_contract"][
        "nonconfirmation_bound_seconds"
    ] == 600
    model_effects = json.loads(
        (tmp_path / "derived" / "model-effects.json").read_text()
    )
    assert model_effects["decision_time_timeout_sensitivity"][
        "primary_holm_family"
    ] is False
    report_text = (tmp_path / "report" / "USER_STUDY_REPORT.md").read_text()
    assert "Non-confirmation is outcome unavailability" in report_text
    assert "no timeout penalty or pseudo-time enters the primary" in report_text
    assert report_manifest["python_version"]
    assert report_manifest["figure_renderer"] == "matplotlib_svg"
    assert report_manifest["generated_file_sha256"]
    for logical_name, expected_sha256 in report_manifest[
        "generated_file_sha256"
    ].items():
        assert not logical_name.startswith("/")
        assert hashlib.sha256((tmp_path / logical_name).read_bytes()).hexdigest() == expected_sha256
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
    assert decision["requested_method"] == "log_time_participant_random_intercept_mixedlm"
    assert decision["actual_method"] == "participant_paired_robust_raw_scale"
    assert decision["converged"] is None


def test_all_technical_model_fallbacks_are_deterministic_and_self_describing(monkeypatch):
    monkeypatch.setattr(analysis_module, "BOOTSTRAP_REPLICATES", 20)
    tasks, questionnaires = _synthetic_analysis_rows()

    no_variation = [dict(row, selection_success=True) for row in tasks]
    selection = analysis_module._selection_analysis(no_variation)
    assert selection["actual_method"] == "participant_paired_risk_difference"
    assert selection["fallback_reason"] == "insufficient_clusters_or_outcome_variation"
    assert selection["p_value_raw"] == 1.0

    def fail_fit(*args, **kwargs):
        raise RuntimeError("forced technical failure")

    monkeypatch.setattr(analysis_module, "_selection_fit", fail_fit)
    selection_failed = analysis_module._selection_analysis(tasks)
    assert selection_failed["converged"] is False
    assert selection_failed["fallback_reason"].startswith(
        "gee_nonconvergence_or_nonidentifiability"
    )

    monkeypatch.setattr(
        analysis_module, "_fit_with_numerical_warnings_as_errors", fail_fit
    )
    decision = analysis_module._log_mixed_analysis(
        tasks, "decision_time_seconds", seed_offset=701
    )
    assert decision["actual_method"] == "participant_paired_robust_raw_scale"
    assert decision["converged"] is False
    seq_rows = [
        row for row in questionnaires if row.get("questionnaire_type") == "seq_task"
    ]
    by_trial = {row["trial_id"]: row for row in tasks}
    seq_rows = [
        {
            **row,
            "variant_slot": by_trial[row["trial_id"]]["variant_slot"],
            "condition_order": by_trial[row["trial_id"]]["condition_order"],
        }
        for row in seq_rows
    ]
    seq = analysis_module._scale_mixed_analysis(seq_rows, SEQ_ITEM_ID, seed_offset=702)
    assert seq["actual_method"] == "participant_paired_scale_difference"
    assert seq["converged"] is False
    count = analysis_module._count_analysis(tasks, "interaction_count")
    assert count["actual_method"] == "participant_paired_count_difference"
    assert count["converged"] is False


def test_bootstrap_seed_reproducibility_and_failed_refit_threshold(monkeypatch):
    monkeypatch.setattr(analysis_module, "BOOTSTRAP_REPLICATES", 20)
    values = {f"p-{index}": float(index) for index in range(8)}
    first = analysis_module._participant_bootstrap(values, seed_offset=77)
    second = analysis_module._participant_bootstrap(values, seed_offset=77)
    assert first == second
    assert first[1]["rng_algorithm"] == "numpy.random.PCG64"
    assert first[1]["successful_fraction"] == 1.0

    tasks, _ = _synthetic_analysis_rows()
    frame = analysis_module._model_frame(tasks, "selection_success")

    inspected = 0

    def inspect_complete_clusters(sampled):
        nonlocal inspected
        inspected += 1
        assert set(sampled.groupby("participant_id").size()) == {6}
        return None, float(sampled["selection_success"].mean())

    monkeypatch.setattr(analysis_module, "_selection_fit", inspect_complete_clusters)
    interval, contract = analysis_module._selection_bootstrap(frame)
    assert inspected == 20
    assert interval != [None, None]
    assert contract["successful_fraction"] == 1.0

    def fail_fit(*args, **kwargs):
        raise RuntimeError("forced failed bootstrap refit")

    monkeypatch.setattr(analysis_module, "_selection_fit", fail_fit)
    interval, contract = analysis_module._selection_bootstrap(frame)
    assert interval == [None, None]
    assert contract["successful_replicates"] == 0
    assert contract["ci_available"] is False


def test_holm_family_stays_two_when_one_primary_is_unavailable():
    adjusted = analysis_module._holm(
        {"selection_success": 0.03, "decision_time_seconds": None}
    )
    assert adjusted == {
        "selection_success": pytest.approx(0.06),
        "decision_time_seconds": None,
    }


def test_preference_wilson_intervals_and_missing_are_separate():
    rows = [
        {"questionnaire_type": "final_preference", FINAL_PREFERENCE_ID: "B0"},
        {"questionnaire_type": "final_preference", FINAL_PREFERENCE_ID: "P2"},
        {"questionnaire_type": "final_preference", FINAL_PREFERENCE_ID: None},
    ]
    summary = analysis_module._preference_summary(rows)
    b0 = next(row for row in summary if row["preference"] == "B0")
    from scipy.stats import norm

    z = float(norm.ppf(1 - 0.05 / 6))
    n, proportion = 2, 0.5
    denominator = 1 + z * z / n
    center = (proportion + z * z / (2 * n)) / denominator
    radius = z * (
        proportion * (1 - proportion) / n + z * z / (4 * n * n)
    ) ** 0.5 / denominator
    assert b0["simultaneous_confidence_interval_95"] == pytest.approx(
        [(center - radius) * 100, (center + radius) * 100]
    )
    assert b0["answered_denominator"] == 2
    assert b0["missing_response_count"] == 1
    assert next(row for row in summary if row["preference"] == "NO_PREFERENCE")[
        "count"
    ] == 0


def test_nonconfirmation_is_missing_outcome_not_exclusion(monkeypatch):
    monkeypatch.setattr(analysis_module, "BOOTSTRAP_REPLICATES", 20)
    tasks, questionnaires = _synthetic_analysis_rows()
    tasks[0].update(
        {
            "selection_success": False,
            "confirmed": False,
            "decision_time_seconds": None,
            "decision_time_unavailability_reason": "participant_cancelled",
            "decision_time_timeout_sensitivity_seconds": 600,
            "notebook_ready_observed": False,
            "notebook_ready_time_seconds": None,
            "notebook_ready_time_status": "unavailable_no_confirmation",
            "end_to_end_seconds": None,
            "end_to_end_status": "unavailable_no_confirmation",
        }
    )
    analysis = analyze_user_study(
        execution_status="DRY_RUN",
        task_rows=tasks,
        questionnaire_rows=questionnaires,
        sessions=[],
        exclusions=[],
        assignment_manifest=SimpleNamespace(participant_count=12),
    )
    b0 = next(row for row in analysis["condition_summary"] if row["condition"] == "B0")
    assert b0["measured_task_count"] == 36
    assert b0["nonconfirmation_count"] == 1
    assert b0["nonconfirmation_reasons"] == {"participant_cancelled": 1}
    assert analysis["participant_flow"][4] == {"stage": "excluded_sessions", "count": 0}
    decision = analysis["effects"]["decision_time_seconds"]
    accounting = decision["population_accounting"]
    assert decision["response_variable"] == "decision_time_seconds"
    assert decision["eligibility"]["eligible_row_count"] == 70
    assert accounting["assigned_measured_trial_count"] == 72
    assert accounting["primary_eligible_matched_pair_count"] == 35
    assert accounting["condition_denominators"]["B0"] == {
        "assigned_measured_trial_count": 36,
        "confirmed_trial_count": 35,
        "nonconfirmed_trial_count": 1,
        "decision_time_available_count": 35,
        "decision_time_unavailable_count": 1,
    }
    assert accounting["condition_denominators"]["P2"] == {
        "assigned_measured_trial_count": 36,
        "confirmed_trial_count": 36,
        "nonconfirmed_trial_count": 0,
        "decision_time_available_count": 36,
        "decision_time_unavailable_count": 0,
    }
    sensitivity = analysis["effects"]["decision_time_timeout_sensitivity"]
    assert sensitivity["timeout_bound_seconds"] == 600
    assert sensitivity["timing_contract_version"] == STUDY_TIMING_CONTRACT_VERSION
    assert sensitivity["timing_contract_sha256"] == STUDY_TIMING_CONTRACT_SHA256
    assert sensitivity["analysis_plan_version"] == ANALYSIS_PLAN_VERSION
    assert sensitivity["analysis_plan_sha256"] == ANALYSIS_PLAN_SHA256
    assert sensitivity["primary_holm_family"] is False
    assert analysis["primary_inference_registry"]["family_size"] == 2


def test_frozen_analysis_runtime_rejects_python_311_and_records_metadata():
    with pytest.raises(UserStudyAnalysisError, match=r"Python 3\.11.*>=3\.12,<3\.15"):
        analysis_module.validate_analysis_runtime(python_version=(3, 11))
    assert analysis_module.validate_analysis_dependencies(
        python_version=(3, 12)
    ) == analysis_module.PINNED_ANALYSIS_DEPENDENCIES
    assert analysis_module.analysis_dependency_python_requirements() == (
        analysis_module.PINNED_ANALYSIS_REQUIRES_PYTHON
    )
    assert analysis_module.PINNED_ANALYSIS_REQUIRES_PYTHON["numpy"] == ">=3.12"
    assert analysis_module.PINNED_ANALYSIS_REQUIRES_PYTHON["scipy"] == ">=3.12"


def test_frozen_timeout_contract_is_assignment_bound_and_drift_fails_closed(
    synthetic_assignment,
):
    frozen = ANALYSIS_PLAN["decision_time_nonconfirmation_bound"]
    assert STUDY_TIMING_CONTRACT[
        "decision_time_nonconfirmation_bound_seconds"
    ] == 600
    assert frozen == {
        "seconds": 600,
        "semantics": STUDY_TIMING_CONTRACT[
            "decision_time_nonconfirmation_bound_semantics"
        ],
        "timing_contract_version": STUDY_TIMING_CONTRACT_VERSION,
        "timing_contract_sha256": STUDY_TIMING_CONTRACT_SHA256,
        "source": "frozen_server_enforced_study_task_timing_contract",
        "tuned_from_participant_results": False,
    }
    assert synthetic_assignment.analysis_plan_version == ANALYSIS_PLAN_VERSION
    assert synthetic_assignment.analysis_plan_sha256 == ANALYSIS_PLAN_SHA256

    payload = synthetic_assignment.to_dict()
    payload["analysis_plan_sha256"] = "0" * 64
    with pytest.raises(
        UserStudyValidationError,
        match="assignment analysis-plan checksum is unsupported",
    ):
        AssignmentManifest.from_dict(payload)


@pytest.mark.parametrize(
    "forbidden_reason",
    [
        "poor_performance",
        "participant_cancelled",
        "missing_questionnaire_answer",
        "slow_response",
        "manual_override",
        "notebook_not_ready",
        "unsuccessful_task",
    ],
)
def test_outcomes_and_missingness_can_never_become_exclusion_codes(
    synthetic_assignment, forbidden_reason
):
    participant = synthetic_assignment.assignments[0]
    record = {
        "schema_version": EXCLUSION_SCHEMA_VERSION,
        "reason_version": EXCLUSION_REASON_VERSION,
        "study_id": synthetic_assignment.study_id,
        "assignment_id": synthetic_assignment.assignment_id,
        "session_id": participant.session_id,
        "participant_id": participant.participant_id,
        "reason_code": forbidden_reason,
        "recorded_at_utc": "2026-08-27T00:00:00Z",
    }
    with pytest.raises(UserStudyRunnerError, match="not predeclared"):
        _validate_exclusions([record], synthetic_assignment)


@pytest.mark.parametrize(
    "payload",
    [
        '{"participant_id":"P-000000000000","email":"person@example.test"}\n',
        '{"comment":"participant supplied prose"}\n',
        '{"source":"/Users/example/private/export.jsonl"}\n',
        '{"source":"/home/example/private/export.jsonl"}\n',
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
    for figure in (output / "report" / "figures").glob("*.svg"):
        content = figure.read_text()
        assert "NOT_EXECUTED — no real consented participant data available" in content
        assert "0.000" not in content
    derived = json.loads((output / "derived" / "analysis.json").read_text())
    assert derived["condition_summary"] == []
    assert derived["effects"] == {}
    provenance = json.loads((output / "manifest.json").read_text())
    assert provenance["contracts"]["analysis_plan_sha256"] == synthetic_assignment.analysis_plan_sha256
    assert provenance["contracts"]["study_timing_contract_version"] == STUDY_TIMING_CONTRACT_VERSION
    assert provenance["contracts"]["study_timing_contract_sha256"] == STUDY_TIMING_CONTRACT_SHA256
    assert provenance["contracts"]["decision_time_nonconfirmation_bound_seconds"] == 600
    assert provenance["contracts"]["questionnaire_schema_version"] == synthetic_assignment.questionnaire_schema_version
    assert provenance["runtime"]["analysis_dependencies"] == analysis_module.PINNED_ANALYSIS_DEPENDENCIES
    assert provenance["runtime"]["supported_python"] == ">=3.12,<3.15"


def test_not_executed_finalization_enforces_runtime_contract(
    tmp_path, synthetic_assignment, monkeypatch, capsys
):
    assignment_path = tmp_path / "assignment.json"
    assignment_path.write_text(json.dumps(synthetic_assignment.to_dict()) + "\n")
    output = tmp_path / "unsupported-runtime"

    def reject_runtime():
        raise UserStudyAnalysisError(
            "unsupported Python 3.11; requires >=3.12,<3.15"
        )

    monkeypatch.setattr(runner_module, "validate_analysis_runtime", reject_runtime)
    assert user_study_main(
        [
            "finalize",
            "--run-id",
            "synthetic-unsupported-runtime-contract-test",
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
    ) == 2
    assert "unsupported Python 3.11" in capsys.readouterr().err
    assert not output.exists()
