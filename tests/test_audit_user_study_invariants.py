"""Comprehensive invariant audit tests for Protocol-v5 E3 user study execution.

Covers the 10 mandated protocol checks:
1. Assignment is deterministic for a fixed seed.
2. B0/P2-first assignment is counterbalanced.
3. Matched task variants are balanced.
4. Task order randomization is deterministic and recorded.
5. No task tells the P2 participant exactly what sentence to type.
6. Event timestamps are sufficient to derive DecisionTime automatically.
7. SelectionSuccess is computed from the final confirmed profile/image/policy state.
8. Direct identifiers are rejected or excluded from research exports/reports.
9. Synthetic fixtures can never silently become observed evidence.
10. Empty/no-real-data production analysis returns NOT_EXECUTED rather than fabricated statistics.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import pytest

from evaluation_v5.user_study.assignment import (
    generate_assignment_manifest,
)
from evaluation_v5.user_study.metrics import derive_study_metrics
from evaluation_v5.user_study.runner import (
    main as user_study_main,
)
from evaluation_v5.user_study.schemas import (
    StudyEvent,
    browser_safe_task_set,
    load_task_set,
    validate_event,
)
from evaluation_v5.user_study.scoring import score_final_selection
from evaluation_v5.user_study.analysis import (
    UserStudyAnalysisError,
    audit_report_privacy,
)
from recommender.candidate_corpus import build_candidate_corpus
from recommender.rule_based import load_image_catalog


ROOT = Path(__file__).resolve().parents[1]
TASK_SET_PATH = ROOT / "benchmarks_v5" / "user-study-draft-v1.yaml"


@pytest.fixture
def task_set():
    return load_task_set(TASK_SET_PATH)


def test_1_assignment_is_deterministic_for_fixed_seed(task_set):
    """Check 1: Assignment is deterministic for a fixed seed."""
    manifest1 = generate_assignment_manifest(
        task_set,
        study_id="test-determinism",
        participant_count=36,
        seed=20260827,
        consent_version="consent-v1",
        git_revision="2cf206773e63f7086e3c9716a5077793b189fa6d",
        generated_at_utc="2026-08-26T00:00:00Z",
    )
    manifest2 = generate_assignment_manifest(
        task_set,
        study_id="test-determinism",
        participant_count=36,
        seed=20260827,
        consent_version="consent-v1",
        git_revision="2cf206773e63f7086e3c9716a5077793b189fa6d",
        generated_at_utc="2026-08-26T00:00:00Z",
    )
    assert manifest1.checksum == manifest2.checksum
    assert manifest1.to_dict() == manifest2.to_dict()

    manifest_diff_seed = generate_assignment_manifest(
        task_set,
        study_id="test-determinism",
        participant_count=36,
        seed=20260828,
        consent_version="consent-v1",
        git_revision="2cf206773e63f7086e3c9716a5077793b189fa6d",
        generated_at_utc="2026-08-26T00:00:00Z",
    )
    assert manifest1.checksum != manifest_diff_seed.checksum


def test_2_b0_p2_first_assignment_is_counterbalanced(task_set):
    """Check 2: B0/P2-first assignment is counterbalanced."""
    manifest = generate_assignment_manifest(
        task_set,
        study_id="test-balance",
        participant_count=36,
        seed=20260827,
        consent_version="consent-v1",
    )
    first_conditions = [
        p.task_sequence[0].condition.value for p in manifest.assignments
    ]
    counts = Counter(first_conditions)
    assert counts["B0"] == 18
    assert counts["P2"] == 18
    assert manifest.balance_audit["condition_first"] == {"B0": 18, "P2": 18}


def test_3_matched_task_variants_are_balanced(task_set):
    """Check 3: Matched task variants are balanced."""
    manifest = generate_assignment_manifest(
        task_set,
        study_id="test-variants",
        participant_count=36,
        seed=20260827,
        consent_version="consent-v1",
    )
    audit = manifest.balance_audit
    assert audit["cell_count"] == 12
    assert audit["exact_target_balance"] is True
    for cell, count in audit["counterbalance_cells"].items():
        assert count == 3
    for pair_variant_cond, count in audit["variant_by_condition"].items():
        assert count == 18


def test_4_task_order_randomization_is_deterministic_and_recorded(task_set):
    """Check 4: Task order randomization is deterministic and recorded."""
    manifest = generate_assignment_manifest(
        task_set,
        study_id="test-order",
        participant_count=36,
        seed=20260827,
        consent_version="consent-v1",
    )
    assert "counterbalance_cells" in manifest.balance_audit
    for assignment in manifest.assignments:
        assert assignment.counterbalance_cell.startswith("C-")
        assert len(assignment.task_sequence) == 8
        assert [t.sequence_index for t in assignment.task_sequence] == list(range(8))
        assert assignment.task_sequence[0].phase.value == "warm_up"
        assert assignment.task_sequence[4].phase.value == "warm_up"
        assert all(assignment.task_sequence[i].phase.value == "measured" for i in (1, 2, 3, 5, 6, 7))


def test_5_no_task_tells_p2_participant_what_to_type(task_set):
    """Check 5: No task tells the P2 participant exactly what sentence to type."""
    raw_yaml = TASK_SET_PATH.read_text(encoding="utf-8")
    for forbidden_field in ["exact_input", "suggested_wording", "prescribed_prompt", "target_sentence"]:
        assert forbidden_field not in raw_yaml

    for pair in task_set.pairs:
        for task in pair.tasks:
            assert "type:" not in task.scenario.lower()
            assert "enter the prompt" not in task.scenario.lower()
            assert "say:" not in task.scenario.lower()
            assert len(task.scenario) > 20


def test_6_event_timestamps_derive_decision_time_automatically(task_set):
    """Check 6: Event timestamps are sufficient to derive DecisionTime automatically."""
    manifest = generate_assignment_manifest(
        task_set,
        study_id="test-decision-time",
        participant_count=1,
        seed=20260827,
        consent_version="consent-v1",
    )
    participant = manifest.assignments[0]
    task = participant.task_sequence[1]

    events = [
        {
            "schema_version": "protocol-v5-user-study-event-v1.0.0",
            "study_id": manifest.study_id,
            "assignment_id": manifest.assignment_id,
            "session_id": participant.session_id,
            "participant_id": participant.participant_id,
            "trial_id": task.trial_id,
            "task_id": task.task_id,
            "pair_id": task.pair_id,
            "condition": task.condition.value,
            "consent_version": manifest.consent_version,
            "event_index": 0,
            "event_type": "task_shown",
            "event_uuid": "11111111-1111-4111-8111-111111111111",
            "timestamp_utc": "2026-08-26T10:00:00Z",
            "monotonic_seconds": 10.0,
            "profile_id": None,
            "image_id": None,
            "old_profile_id": None,
            "new_profile_id": None,
            "old_image_id": None,
            "new_image_id": None,
            "preview_status": None,
            "cancel_reason": None,
        },
        {
            "schema_version": "protocol-v5-user-study-event-v1.0.0",
            "study_id": manifest.study_id,
            "assignment_id": manifest.assignment_id,
            "session_id": participant.session_id,
            "participant_id": participant.participant_id,
            "trial_id": task.trial_id,
            "task_id": task.task_id,
            "pair_id": task.pair_id,
            "condition": task.condition.value,
            "consent_version": manifest.consent_version,
            "event_index": 1,
            "event_type": "profile_changed",
            "event_uuid": "22222222-2222-4222-8222-222222222222",
            "timestamp_utc": "2026-08-26T10:00:20Z",
            "monotonic_seconds": 30.0,
            "profile_id": None,
            "image_id": None,
            "old_profile_id": None,
            "new_profile_id": "small",
            "old_image_id": None,
            "new_image_id": None,
            "preview_status": None,
            "cancel_reason": None,
        },
        {
            "schema_version": "protocol-v5-user-study-event-v1.0.0",
            "study_id": manifest.study_id,
            "assignment_id": manifest.assignment_id,
            "session_id": participant.session_id,
            "participant_id": participant.participant_id,
            "trial_id": task.trial_id,
            "task_id": task.task_id,
            "pair_id": task.pair_id,
            "condition": task.condition.value,
            "consent_version": manifest.consent_version,
            "event_index": 2,
            "event_type": "image_changed",
            "event_uuid": "33333333-3333-4333-8333-333333333333",
            "timestamp_utc": "2026-08-26T10:00:30Z",
            "monotonic_seconds": 40.0,
            "profile_id": None,
            "image_id": None,
            "old_profile_id": None,
            "new_profile_id": None,
            "old_image_id": None,
            "new_image_id": "minimal-python",
            "preview_status": None,
            "cancel_reason": None,
        },
        {
            "schema_version": "protocol-v5-user-study-event-v1.0.0",
            "study_id": manifest.study_id,
            "assignment_id": manifest.assignment_id,
            "session_id": participant.session_id,
            "participant_id": participant.participant_id,
            "trial_id": task.trial_id,
            "task_id": task.task_id,
            "pair_id": task.pair_id,
            "condition": task.condition.value,
            "consent_version": manifest.consent_version,
            "event_index": 3,
            "event_type": "confirm",
            "event_uuid": "44444444-4444-4444-8444-444444444444",
            "timestamp_utc": "2026-08-26T10:00:45Z",
            "monotonic_seconds": 55.0,
            "profile_id": "small",
            "image_id": "minimal-python",
            "old_profile_id": None,
            "new_profile_id": None,
            "old_image_id": None,
            "new_image_id": None,
            "preview_status": None,
            "cancel_reason": None,
        },
    ]
    parsed_events = [validate_event(e) for e in events]
    derived = derive_study_metrics(
        parsed_events,
        task_set,
        manifest,
        execution_status="DRY_RUN",
    )
    task_outcomes = derived["task_outcomes"]
    assert len(task_outcomes) == 1
    assert task_outcomes[0]["decision_time_seconds"] == pytest.approx(45.0)
    assert task_outcomes[0]["decision_time_status"] == "available"


def test_7_selection_success_computed_from_final_confirmed_state(task_set):
    """Check 7: SelectionSuccess is computed from the final confirmed profile/image/policy state."""
    pair = task_set.pairs[1]

    # Correct/acceptable candidate
    score_ok = score_final_selection(
        pair.gold,
        profile_id="small",
        image_id="minimal-python",
    )
    assert score_ok.selection_acceptable is True
    assert score_ok.selection_correct is True
    assert score_ok.hard_constraints_satisfied is True

    # Wrong image
    score_wrong_img = score_final_selection(
        pair.gold,
        profile_id="small",
        image_id="pytorch-deep-learning",
    )
    assert score_wrong_img.selection_acceptable is False
    assert score_wrong_img.selection_correct is False

    # Oversized profile violating smallest adequate profile policy
    score_wrong_profile = score_final_selection(
        pair.gold,
        profile_id="large",
        image_id="minimal-python",
    )
    assert score_wrong_profile.selection_acceptable is False


def test_8_direct_identifiers_rejected_or_excluded(tmp_path):
    """Check 8: Direct identifiers are rejected or excluded from research exports/reports."""
    safe_file = tmp_path / "safe.json"
    safe_file.write_text('{"status": "PASS", "count": 42}\n')
    assert audit_report_privacy([safe_file])["status"] == "PASS"

    email_file = tmp_path / "email.json"
    email_file.write_text('{"email": "alice@institution.edu"}\n')
    with pytest.raises(UserStudyAnalysisError, match="aggregate report privacy audit failed"):
        audit_report_privacy([email_file])

    pseudonym_file = tmp_path / "pseudonym.json"
    pseudonym_file.write_text('{"user": "P-1234567890ab"}\n')
    with pytest.raises(UserStudyAnalysisError, match="aggregate report privacy audit failed"):
        audit_report_privacy([pseudonym_file])

    path_file = tmp_path / "path.json"
    path_file.write_text('{"path": "/Users/researcher/thesis/export.json"}\n')
    with pytest.raises(UserStudyAnalysisError, match="aggregate report privacy audit failed"):
        audit_report_privacy([path_file])


def test_9_synthetic_fixtures_cannot_become_observed_evidence(tmp_path, task_set, capsys):
    """Check 9: Synthetic fixtures can never silently become observed evidence."""
    manifest = generate_assignment_manifest(
        task_set,
        study_id="test-obs-rejection",
        participant_count=36,
        seed=20260827,
        consent_version="consent-v1",
    )
    assignment_path = tmp_path / "assignment.json"
    assignment_path.write_text(json.dumps(manifest.to_dict()) + "\n")
    output_dir = tmp_path / "attempted-observed"

    exit_code = user_study_main(
        [
            "finalize",
            "--run-id",
            "attempt-obs",
            "--task-set",
            str(TASK_SET_PATH),
            "--assignments",
            str(assignment_path),
            "--execution-status",
            "OBSERVED",
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "confirmatory preparation rejects a development task set" in err


def test_10_empty_production_analysis_returns_not_executed(tmp_path, task_set):
    """Check 10: Empty/no-real-data production analysis returns NOT_EXECUTED rather than fabricated statistics."""
    manifest = generate_assignment_manifest(
        task_set,
        study_id="test-not-executed",
        participant_count=36,
        seed=20260827,
        consent_version="consent-v1",
    )
    assignment_path = tmp_path / "assignment.json"
    assignment_path.write_text(json.dumps(manifest.to_dict()) + "\n")
    output_dir = tmp_path / "not-executed-run"

    exit_code = user_study_main(
        [
            "finalize",
            "--run-id",
            "audit-not-executed",
            "--task-set",
            str(TASK_SET_PATH),
            "--assignments",
            str(assignment_path),
            "--execution-status",
            "NOT_EXECUTED",
            "--output-dir",
            str(output_dir),
            "--created-at-utc",
            "2026-08-26T12:00:00Z",
        ]
    )
    assert exit_code == 0
    status_json = json.loads((output_dir / "report" / "status.json").read_text())
    assert status_json["execution_status"] == "NOT_EXECUTED"
    assert status_json["experiment_executed"] is False
    assert status_json["observed_evidence"] is False
    assert status_json["raw_event_count"] == 0

    analysis_json = json.loads((output_dir / "derived" / "analysis.json").read_text())
    assert analysis_json["condition_summary"] == []
    assert analysis_json["effects"] == {}

    report_md = (output_dir / "report" / "USER_STUDY_REPORT.md").read_text()
    assert "Evidence status: `NOT_EXECUTED`." in report_md
    assert "NOT_EXECUTED — no empirical condition estimates are available." in report_md
    assert "NOT_EXECUTED — effect sizes, confidence intervals, and p-values are unavailable." in report_md
