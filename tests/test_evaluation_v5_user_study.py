"""Synthetic-only tests for the Protocol-v5 B0-vs-P2 study contracts."""

from __future__ import annotations

import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
import uuid

import jsonschema
import pytest
import yaml

from evaluation_v5.user_study import metrics as metrics_module
from evaluation_v5.user_study.assignment import (
    AssignmentManifest,
    generate_assignment_manifest,
    validate_assignment_manifest,
)
from evaluation_v5.user_study.instrumentation import (
    AppendOnlyEventStore,
    EventUUIDConflictError,
    InstrumentationCorruptionError,
    InstrumentationError,
    InstrumentationPrivacyError,
    SessionAlreadyCompleteError,
)
from evaluation_v5.user_study.hub import (
    ActiveStudyTask,
    StudyGateStore,
    StudyHubError,
    StudySessionIncompleteError,
    StudySessionRuntime,
    _study_participant_id,
    apply_b0_selection,
    bind_study_spawn_annotations,
    options_form,
    questionnaire_form,
    shared_option_snapshot,
    validate_browser_task_set,
)
from evaluation_v5.user_study.fairness import (
    build_fairness_manifest,
    validate_fairness_manifest,
    validate_study_environment_identity,
    verify_fairness_manifest,
)
from evaluation_v5.user_study.metrics import derive_study_metrics
from evaluation_v5.user_study.questionnaires import (
    CUSTOM_ITEM_IDS,
    FINAL_PREFERENCE_ID,
    SEQ_ITEM_ID,
    SUS_ITEM_IDS,
    QuestionnaireType,
    expected_questionnaire_ids,
)
from evaluation_v5.user_study.runner import main as user_study_main
from evaluation_v5.user_study.scoring import score_final_selection
from evaluation_v5.user_study.smoke import run_synthetic_hub_smoke
from evaluation_v5.user_study.schemas import (
    EVENT_SCHEMA_VERSION,
    CancelReason,
    Condition,
    EventType,
    Difficulty,
    PairGold,
    ReviewStatus,
    UserStudyValidationError,
    browser_safe_task_set,
    load_task_set,
    validate_event,
    validate_event_stream,
    validate_task_set,
)
from recommender.candidate_corpus import build_candidate_corpus
from recommender.rule_based import load_image_catalog


ROOT = Path(__file__).resolve().parents[1]
TASK_SET_PATH = ROOT / "benchmarks_v5" / "user-study-draft-v1.yaml"
TASK_SCHEMA_PATH = (
    ROOT / "benchmarks_v5" / "protocol-v5-user-study-task-set-v1.schema.json"
)
FIXED_GENERATED_AT = "2026-08-26T00:00:00Z"


def test_authenticator_username_mapping_restores_only_canonical_pseudonyms():
    lowered = SimpleNamespace(user=SimpleNamespace(name="p-82901fbcd097"))
    canonical = SimpleNamespace(user=SimpleNamespace(name="P-82901fbcd097"))
    assert _study_participant_id(lowered) == "P-82901fbcd097"
    assert _study_participant_id(canonical) == "P-82901fbcd097"

    for invalid in ("researcher", "p-82901FBCD097", "p-82901fbcd097-extra"):
        with pytest.raises(StudyHubError, match="issued pseudonymous"):
            _study_participant_id(SimpleNamespace(user=SimpleNamespace(name=invalid)))


@pytest.mark.parametrize("condition", ["B0", "P2"])
def test_spawn_annotations_bind_same_safe_trial_fields_for_both_conditions(condition):
    spawner = SimpleNamespace(extra_annotations={"existing.example/key": "retained"})
    options = {
        "study_id": "e3-smoke",
        "study_assignment_id": "A-abc123",
        "study_session_id": "S-def456",
        "study_trial_id": "T-789abc",
        "study_task_id": "task-a",
        "study_pair_id": "pair-a",
        "applied_profile": "small",
        "applied_image_id": "minimal-python",
    }
    added = bind_study_spawn_annotations(
        spawner,
        options,
        participant_id="P-82901fbcd097",
        condition=condition,
    )
    assert spawner.extra_annotations["existing.example/key"] == "retained"
    assert spawner.extra_annotations["intent-spawner.openai.com/condition"] == condition
    assert added["intent-spawner.openai.com/trial-id"] == "T-789abc"
    assert added["intent-spawner.openai.com/final-profile-id"] == "small"
    assert added["intent-spawner.openai.com/final-image-id"] == "minimal-python"
    serialized = json.dumps(added).lower()
    assert "raw-intent" not in serialized
    assert "intent-text" not in serialized
    assert "scenario" not in serialized


@pytest.fixture(scope="module")
def task_set():
    return load_task_set(TASK_SET_PATH)


@pytest.fixture(scope="module")
def assignment(task_set):
    return generate_assignment_manifest(
        task_set,
        study_id="e3-synthetic-test",
        participant_count=36,
        seed=20260826,
        consent_version="consent-test-v1",
        git_revision="a" * 40,
        freeze_id="development-unfrozen",
        config_identity="study-config-test-v1",
        environment_identity={"environment_id": "synthetic-no-cluster"},
        generated_at_utc=FIXED_GENERATED_AT,
    )


def _assigned_task(assignment: AssignmentManifest, condition: Condition):
    participant = assignment.assignments[0]
    return participant, next(
        task
        for task in participant.task_sequence
        if task.condition is condition and task.phase.value == "measured"
    )


def _event(
    assignment: AssignmentManifest,
    participant,
    task,
    index: int,
    event_type: EventType,
    *,
    elapsed: float | None = None,
    **fields,
) -> dict[str, object]:
    seconds = float(index if elapsed is None else elapsed)
    timestamp = datetime(2026, 8, 26, tzinfo=timezone.utc) + timedelta(
        seconds=seconds
    )
    payload: dict[str, object] = {
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
        "event_uuid": str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{participant.participant_id}:{task.trial_id}:{index}:{event_type.value}",
            )
        ),
        "event_index": index,
        "timestamp_utc": timestamp.isoformat().replace("+00:00", "Z"),
        "monotonic_seconds": seconds,
        "event_type": event_type.value,
        "profile_id": None,
        "image_id": None,
        "old_profile_id": None,
        "new_profile_id": None,
        "old_image_id": None,
        "new_image_id": None,
        "preview_status": None,
        "cancel_reason": None,
    }
    payload.update(fields)
    return payload


def _b0_events(assignment: AssignmentManifest):
    participant, task = _assigned_task(assignment, Condition.B0)
    return [
        _event(assignment, participant, task, 0, EventType.TASK_SHOWN),
        _event(
            assignment,
            participant,
            task,
            1,
            EventType.PROFILE_CHANGED,
            old_profile_id=None,
            new_profile_id="small",
        ),
        _event(
            assignment,
            participant,
            task,
            2,
            EventType.IMAGE_CHANGED,
            old_image_id=None,
            new_image_id="minimal-python",
        ),
        _event(
            assignment,
            participant,
            task,
            3,
            EventType.CONFIRM,
            profile_id="small",
            image_id="minimal-python",
        ),
        _event(
            assignment,
            participant,
            task,
            4,
            EventType.NOTEBOOK_READY,
            profile_id="small",
            image_id="minimal-python",
        ),
    ]


def _p2_events(assignment: AssignmentManifest):
    participant, task = _assigned_task(assignment, Condition.P2)
    return [
        _event(assignment, participant, task, 0, EventType.TASK_SHOWN),
        _event(assignment, participant, task, 1, EventType.INTENT_FOCUS),
        _event(assignment, participant, task, 2, EventType.INTENT_EDIT),
        _event(assignment, participant, task, 3, EventType.PREVIEW_REQUESTED),
        _event(
            assignment,
            participant,
            task,
            4,
            EventType.PREVIEW_RECEIVED,
            profile_id="small",
            image_id="minimal-python",
            preview_status="success",
        ),
        _event(
            assignment,
            participant,
            task,
            5,
            EventType.PROFILE_CHANGED,
            old_profile_id="small",
            new_profile_id="medium",
        ),
        _event(
            assignment,
            participant,
            task,
            6,
            EventType.IMAGE_CHANGED,
            old_image_id="minimal-python",
            new_image_id="scipy-data-science",
        ),
        _event(
            assignment,
            participant,
            task,
            7,
            EventType.OVERRIDE,
            profile_id="medium",
            image_id="scipy-data-science",
        ),
        _event(
            assignment,
            participant,
            task,
            8,
            EventType.CONFIRM,
            profile_id="medium",
            image_id="scipy-data-science",
        ),
    ]


def test_draft_task_bundle_is_strict_valid_and_browser_projection_is_gold_free(
    task_set,
):
    raw = yaml.safe_load(TASK_SET_PATH.read_text(encoding="utf-8"))
    schema = json.loads(TASK_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(raw)

    validated = validate_task_set(
        task_set,
        catalog=load_image_catalog(),
        corpus=build_candidate_corpus(),
        require_protocol_design=True,
    )
    assert Counter(pair.phase.value for pair in validated.pairs) == {
        "warm_up": 1,
        "measured": 3,
    }
    assert all(len(pair.tasks) == 2 for pair in validated.pairs)
    public = browser_safe_task_set(validated)
    assert public["source_task_set_sha256"] == validated.checksum
    assert "gold" not in json.dumps(public, sort_keys=True)
    assert "acceptable_candidate_ids" not in json.dumps(public, sort_keys=True)


def test_draft_and_prescribed_intent_fields_fail_confirmatory_preparation(task_set):
    with pytest.raises(UserStudyValidationError, match="development task set"):
        validate_task_set(task_set, confirmatory=True)

    payload = task_set.to_dict()
    payload["pairs"][0]["tasks"][0]["suggested_wording"] = "Choose this"
    with pytest.raises(UserStudyValidationError, match="prescribed-intent"):
        validate_task_set(payload)

    payload = task_set.to_dict()
    payload["pairs"][0]["tasks"][0]["scenario"] = "Type exactly the following phrase."
    with pytest.raises(UserStudyValidationError, match="not prescribe"):
        validate_task_set(payload)

    payload = task_set.to_dict()
    payload["pairs"][0]["tasks"][1]["scenario"] = payload["pairs"][0]["tasks"][0][
        "scenario"
    ]
    with pytest.raises(UserStudyValidationError, match="scenarios must be distinct"):
        validate_task_set(payload)

    payload = task_set.to_dict()
    payload["pairs"][0]["tasks"][1]["variant_id"] = "C"
    with pytest.raises(UserStudyValidationError, match="exactly A and B"):
        validate_task_set(payload)


def test_final_selection_scorer_distinguishes_preferred_acceptable_and_constraints():
    gold = PairGold(
        requirements=("python", "hard_pair_constraint"),
        acceptable_profile_ids=("small", "medium"),
        acceptable_image_ids=("minimal-python", "scipy-data-science"),
        acceptable_candidate_ids=(
            "small-minimal-python",
            "medium-scipy-data-science",
        ),
        preferred_candidate_id="small-minimal-python",
        policy_constraints=("approved_pairings_only",),
        difficulty=Difficulty.MEDIUM,
        equivalence_review_status=ReviewStatus.APPROVED,
    )
    exact = score_final_selection(
        gold, profile_id="small", image_id="minimal-python"
    )
    alternative = score_final_selection(
        gold, profile_id="medium", image_id="scipy-data-science"
    )
    wrong_profile = score_final_selection(
        gold, profile_id="large", image_id="minimal-python"
    )
    wrong_image = score_final_selection(
        gold, profile_id="small", image_id="pytorch-deep-learning"
    )
    hard_violation = score_final_selection(
        gold, profile_id="small", image_id="scipy-data-science"
    )
    assert exact.selection_correct and exact.selection_acceptable
    assert not alternative.selection_correct and alternative.selection_acceptable
    assert not wrong_profile.profile_acceptable and not wrong_profile.selection_acceptable
    assert not wrong_image.image_acceptable and not wrong_image.selection_acceptable
    assert hard_violation.profile_acceptable and hard_violation.image_acceptable
    assert not hard_violation.hard_constraints_satisfied
    assert score_final_selection(
        gold, profile_id="small", image_id="minimal-python"
    ) == exact
    missing = score_final_selection(gold, profile_id=None, image_id=None)
    assert missing.scoring_status == "unavailable_no_confirmation"
    assert not missing.selection_acceptable


def _fairness_fixture(*, freeze_id="development-unfrozen"):
    catalog = load_image_catalog()
    corpus = build_candidate_corpus(image_catalog=catalog)
    manifest = build_fairness_manifest(
        catalog=catalog,
        corpus=corpus,
        freeze_id=freeze_id,
        config_identity="config-test-v1",
        deployment_revision=("a" * 40 if freeze_id != "development-unfrozen" else "unknown"),
        kubernetes_environment_id=(
            "study-cluster-v1" if freeze_id != "development-unfrozen" else "not-recorded"
        ),
    )
    return catalog, corpus, manifest


def _recompute_fairness_hashes(payload):
    components = {
        field: payload[field]
        for field in (
            "profile_catalog_sha256",
            "image_catalog_sha256",
            "policy_sha256",
            "description_config_sha256",
            "configuration_sha256",
            "deployment_revision",
            "kubernetes_environment_id",
        )
    }
    from evaluation_v5.user_study.schemas import canonical_json_sha256

    digest = canonical_json_sha256(components)
    payload["b0_environment_sha256"] = digest
    payload["p2_environment_sha256"] = digest
    payload["shared_environment_sha256"] = digest
    return payload


def test_fairness_manifest_enforces_identical_conditions_and_frozen_components():
    catalog, corpus, fairness = _fairness_fixture(freeze_id="study-freeze-v1")
    assert validate_fairness_manifest(fairness, confirmatory=True) == fairness
    verify_fairness_manifest(
        fairness,
        catalog=catalog,
        corpus=corpus,
        freeze_id="study-freeze-v1",
        config_identity="config-test-v1",
        deployment_revision="a" * 40,
        kubernetes_environment_id="study-cluster-v1",
        confirmatory=True,
    )

    arms_differ = deepcopy(fairness)
    arms_differ["p2_environment_sha256"] = "0" * 64
    with pytest.raises(UserStudyValidationError, match="B0 and P2"):
        validate_fairness_manifest(arms_differ, confirmatory=True)

    for field in (
        "profile_catalog_sha256",
        "image_catalog_sha256",
        "policy_sha256",
        "description_config_sha256",
        "configuration_sha256",
    ):
        drift = _recompute_fairness_hashes(
            {**fairness, field: ("0" * 64 if fairness[field] != "0" * 64 else "1" * 64)}
        )
        with pytest.raises(UserStudyValidationError, match="drift"):
            verify_fairness_manifest(
                drift,
                catalog=catalog,
                corpus=corpus,
                freeze_id="study-freeze-v1",
                config_identity="config-test-v1",
                deployment_revision="a" * 40,
                kubernetes_environment_id="study-cluster-v1",
                confirmatory=True,
            )

    with pytest.raises(UserStudyValidationError, match="fairness_manifest"):
        validate_study_environment_identity(
            {"environment_id": "study-cluster-v1"}, confirmatory=True
        )
    assert validate_study_environment_identity(
        {
            "environment_id": "not-recorded",
            "mode": "development_unfrozen",
        },
        confirmatory=False,
    )["mode"] == "development_unfrozen"


def test_assignment_is_reproducible_seeded_bound_and_exactly_balanced(
    task_set, assignment
):
    repeated = generate_assignment_manifest(
        task_set,
        study_id=assignment.study_id,
        participant_count=36,
        seed=assignment.seed,
        consent_version=assignment.consent_version,
        git_revision=assignment.git_revision,
        freeze_id=assignment.freeze_id,
        config_identity=assignment.config_identity,
        environment_identity=assignment.environment_identity,
        generated_at_utc=FIXED_GENERATED_AT,
    )
    assert repeated.to_dict() == assignment.to_dict()
    assert AssignmentManifest.from_dict(assignment.to_dict()) == assignment
    assert assignment.config_identity == "study-config-test-v1"
    assert assignment.balance_audit["condition_first"] == {"B0": 18, "P2": 18}
    assert set(assignment.balance_audit["counterbalance_cells"].values()) == {3}
    assert set(assignment.balance_audit["variant_by_condition"].values()) == {18}
    assert set(assignment.balance_audit["measured_pair_positions"].values()) == {24}

    for participant in assignment.assignments:
        by_pair: dict[str, list] = {}
        for task in participant.task_sequence:
            by_pair.setdefault(task.pair_id, []).append(task)
        assert all(len(tasks) == 2 for tasks in by_pair.values())
        assert all(
            {task.condition for task in tasks} == {Condition.B0, Condition.P2}
            and len({task.task_id for task in tasks}) == 2
            for tasks in by_pair.values()
        )

    changed_seed = generate_assignment_manifest(
        task_set,
        study_id=assignment.study_id,
        participant_count=36,
        seed=assignment.seed + 1,
        consent_version=assignment.consent_version,
        generated_at_utc=FIXED_GENERATED_AT,
    )
    assert changed_seed.assignments != assignment.assignments


def test_assignment_rejects_config_and_counterbalance_drift(task_set, assignment):
    payload = assignment.to_dict()
    del payload["config_identity"]
    with pytest.raises(UserStudyValidationError, match="config_identity"):
        AssignmentManifest.from_dict(payload)

    payload = assignment.to_dict()
    payload["config_identity"] = "different-study-config"
    with pytest.raises(UserStudyValidationError, match="assignment_id does not bind"):
        validate_assignment_manifest(payload, task_set=task_set)

    payload = assignment.to_dict()
    original = payload["assignments"][0]["task_sequence"][1]["variant_id"]
    payload["assignments"][0]["task_sequence"][1]["variant_id"] = (
        "B" if original == "A" else "A"
    )
    with pytest.raises(UserStudyValidationError, match="variant"):
        validate_assignment_manifest(payload, task_set=task_set)

    payload = assignment.to_dict()
    payload["environment_identity"] = {
        "environment_id": "study-cluster",
        "api_token": "must-not-enter-research-evidence",
    }
    with pytest.raises(UserStudyValidationError, match="forbidden"):
        AssignmentManifest.from_dict(payload)

    payload = assignment.to_dict()
    payload["environment_identity"] = {
        "environment_id": "study-cluster",
        "location": "person@example.test",
    }
    with pytest.raises(UserStudyValidationError, match="email address"):
        AssignmentManifest.from_dict(payload)

    payload = assignment.to_dict()
    payload["event_schema_version"] = "unsupported-event-contract"
    with pytest.raises(UserStudyValidationError, match="event_schema_version"):
        AssignmentManifest.from_dict(payload)

    payload = assignment.to_dict()
    payload["selection_scoring_version"] = "unsupported-scoring-contract"
    with pytest.raises(UserStudyValidationError, match="selection_scoring_version"):
        AssignmentManifest.from_dict(payload)


def test_confirmatory_assignment_requires_frozen_fairness_and_contract_provenance(
    task_set,
):
    payload = task_set.to_dict()
    payload["stage"] = "confirmatory"
    payload["status"] = "frozen"
    for pair in payload["pairs"]:
        pair["gold"]["equivalence_review_status"] = "approved"
    frozen = validate_task_set(
        payload,
        catalog=load_image_catalog(),
        corpus=build_candidate_corpus(),
        confirmatory=True,
        require_protocol_design=True,
    )
    with pytest.raises(UserStudyValidationError, match="fairness_manifest"):
        generate_assignment_manifest(
            frozen,
            study_id="e3-confirmatory-gate-test",
            participant_count=12,
            seed=20260827,
            consent_version="consent-frozen-v1",
            git_revision="a" * 40,
            freeze_id="study-freeze-v1",
            config_identity="config-test-v1",
            environment_identity={"environment_id": "study-cluster-v1"},
            generated_at_utc=FIXED_GENERATED_AT,
            confirmatory=True,
        )

    catalog, corpus, fairness = _fairness_fixture(freeze_id="study-freeze-v1")
    manifest = generate_assignment_manifest(
        frozen,
        study_id="e3-confirmatory-gate-test",
        participant_count=12,
        seed=20260827,
        consent_version="consent-frozen-v1",
        git_revision="a" * 40,
        freeze_id="study-freeze-v1",
        config_identity="config-test-v1",
        environment_identity={
            "environment_id": "study-cluster-v1",
            "mode": "frozen",
            "fairness_manifest": fairness,
        },
        generated_at_utc=FIXED_GENERATED_AT,
        confirmatory=True,
    )
    assert manifest.event_schema_version.endswith("v1.0.0")
    assert manifest.selection_scoring_version.endswith("v1.0.0")
    assert manifest.environment_identity["fairness_manifest"] == fairness


@pytest.mark.parametrize("participant_count", [1, 5, 11, 12, 13, 24, 25])
def test_assignment_partial_blocks_are_deterministic_and_maximally_balanced(
    task_set, participant_count
):
    kwargs = {
        "study_id": "e3-partial-block-test",
        "participant_count": participant_count,
        "seed": 20260827,
        "consent_version": "consent-test-v1",
        "generated_at_utc": FIXED_GENERATED_AT,
    }
    first = generate_assignment_manifest(task_set, **kwargs)
    repeated = generate_assignment_manifest(task_set, **kwargs)
    assert first.to_dict() == repeated.to_dict()
    condition_counts = Counter(
        item.condition_order[0].value for item in first.assignments
    )
    slot_counts = Counter(item.b0_variant_slot for item in first.assignments)
    row_counts = Counter(item.order_row for item in first.assignments)
    assert max(condition_counts.values(), default=0) - min(
        [condition_counts.get("B0", 0), condition_counts.get("P2", 0)]
    ) <= 1
    assert max(slot_counts.values(), default=0) - min(
        [slot_counts.get(0, 0), slot_counts.get(1, 0)]
    ) <= 1
    assert max(row_counts.values(), default=0) - min(
        [row_counts.get(0, 0), row_counts.get(1, 0), row_counts.get(2, 0)]
    ) <= 1
    if participant_count >= 12:
        cell_counts = Counter(
            item.counterbalance_cell for item in first.assignments
        )
        assert max(cell_counts.values()) - min(cell_counts.values()) <= 1

    # Earlier issued assignments are a stable prefix; recruitment/dropout does
    # not rewrite prior cells based on outcomes.
    extended = generate_assignment_manifest(
        task_set, **{**kwargs, "participant_count": participant_count + 1}
    )
    assert extended.assignments[:participant_count] == first.assignments


def test_valid_b0_and_p2_event_state_machines_bind_assignment_and_ids(
    task_set, assignment
):
    allowed_profiles = {"small", "medium", "large"}
    allowed_images = set(load_image_catalog()["images"])
    events = [*_b0_events(assignment), *_p2_events(assignment)]
    assert len(
        validate_event_stream(
            events,
            assignment_manifest=assignment,
            task_set=task_set,
            allowed_profile_ids=allowed_profiles,
            allowed_image_ids=allowed_images,
        )
    ) == len(events)

    for field_name, replacement in (
        ("study_id", "different-study"),
        ("assignment_id", "different-assignment"),
        ("session_id", "different-session"),
        ("trial_id", "different-trial"),
        ("consent_version", "different-consent"),
    ):
        drift = deepcopy(_b0_events(assignment))
        for event in drift:
            event[field_name] = replacement
        with pytest.raises(UserStudyValidationError, match=field_name):
            validate_event_stream(drift, assignment_manifest=assignment)


def test_event_prefix_mode_only_relaxes_completion(assignment):
    prefix = _p2_events(assignment)[:4]
    with pytest.raises(UserStudyValidationError, match="incomplete"):
        validate_event_stream(prefix)
    assert len(validate_event_stream(prefix, allow_incomplete=True)) == 4
    assert validate_event_stream([], allow_incomplete=True) == ()

    invalid = deepcopy(prefix)
    invalid[-1]["event_index"] = 9
    with pytest.raises(UserStudyValidationError, match="contiguous"):
        validate_event_stream(invalid, allow_incomplete=True)


def test_event_contract_rejects_condition_and_state_machine_violations(assignment):
    participant, task = _assigned_task(assignment, Condition.B0)
    b0_intent = _event(
        assignment, participant, task, 0, EventType.INTENT_FOCUS
    )
    with pytest.raises(UserStudyValidationError, match="not valid under B0"):
        validate_event(b0_intent)

    shown = _event(assignment, participant, task, 0, EventType.TASK_SHOWN)
    ready = _event(assignment, participant, task, 1, EventType.NOTEBOOK_READY)
    with pytest.raises(UserStudyValidationError, match="before confirm"):
        validate_event_stream([shown, ready])

    cancelled = _event(
        assignment,
        participant,
        task,
        1,
        EventType.CANCEL,
        cancel_reason="participant_cancelled",
    )
    changed = _event(
        assignment,
        participant,
        task,
        2,
        EventType.PROFILE_CHANGED,
        old_profile_id=None,
        new_profile_id="small",
    )
    with pytest.raises(UserStudyValidationError, match="after terminal"):
        validate_event_stream([shown, cancelled, changed])

    backwards = deepcopy(_b0_events(assignment))
    backwards[2]["monotonic_seconds"] = 0.5
    with pytest.raises(UserStudyValidationError, match="backwards"):
        validate_event_stream(backwards)

    duplicate_uuid = deepcopy(_b0_events(assignment))
    duplicate_uuid[1]["event_uuid"] = duplicate_uuid[0]["event_uuid"]
    with pytest.raises(UserStudyValidationError, match="duplicate event_uuid"):
        validate_event_stream(duplicate_uuid)


def test_event_contract_enforces_decision_and_readiness_deadlines(assignment):
    participant, task = _assigned_task(assignment, Condition.B0)
    late_confirm = _b0_events(assignment)[:4]
    late_confirm[-1] = _event(
        assignment,
        participant,
        task,
        3,
        EventType.CONFIRM,
        elapsed=600.01,
        profile_id="small",
        image_id="minimal-python",
    )
    with pytest.raises(UserStudyValidationError, match="600-second"):
        validate_event_stream(late_confirm)

    early_timeout = [
        _event(assignment, participant, task, 0, EventType.TASK_SHOWN),
        _event(
            assignment,
            participant,
            task,
            1,
            EventType.CANCEL,
            elapsed=599.0,
            cancel_reason="decision_timeout",
        ),
    ]
    with pytest.raises(UserStudyValidationError, match="before 600"):
        validate_event_stream(early_timeout)
    early_timeout[-1] = _event(
        assignment,
        participant,
        task,
        1,
        EventType.CANCEL,
        elapsed=600.0,
        cancel_reason="decision_timeout",
    )
    assert len(validate_event_stream(early_timeout)) == 2

    late_ready = _b0_events(assignment)
    late_ready[-1] = _event(
        assignment,
        participant,
        task,
        4,
        EventType.NOTEBOOK_READY,
        elapsed=184.0,
        profile_id="small",
        image_id="minimal-python",
    )
    with pytest.raises(UserStudyValidationError, match="180-second"):
        validate_event_stream(late_ready)


def test_event_contract_rejects_repeated_confirm_stale_ready_and_missing_shown(
    assignment,
):
    participant, task = _assigned_task(assignment, Condition.B0)
    repeated_confirm = _b0_events(assignment)[:4]
    repeated_confirm.append(
        _event(
            assignment,
            participant,
            task,
            4,
            EventType.CONFIRM,
            profile_id="small",
            image_id="minimal-python",
        )
    )
    with pytest.raises(UserStudyValidationError, match="only notebook_ready"):
        validate_event_stream(repeated_confirm)

    missing_shown = [
        _event(
            assignment,
            participant,
            task,
            0,
            EventType.PROFILE_CHANGED,
            old_profile_id=None,
            new_profile_id="small",
        )
    ]
    with pytest.raises(UserStudyValidationError, match="task_shown"):
        validate_event_stream(missing_shown)

    stale_ready = deepcopy(_b0_events(assignment))
    stale_ready[-1]["profile_id"] = "medium"
    with pytest.raises(UserStudyValidationError, match="differs from confirm"):
        validate_event_stream(stale_ready)

    negative = deepcopy(_b0_events(assignment))
    negative[0]["monotonic_seconds"] = -0.01
    with pytest.raises(UserStudyValidationError, match="non-negative"):
        validate_event_stream(negative)

    after_ready = deepcopy(_b0_events(assignment))
    after_ready.append(
        _event(
            assignment,
            participant,
            task,
            5,
            EventType.PROFILE_CHANGED,
            old_profile_id="small",
            new_profile_id="medium",
        )
    )
    with pytest.raises(UserStudyValidationError, match="after terminal"):
        validate_event_stream(after_ready)


def test_append_store_accepts_live_prefix_idempotently_and_seals_strict_session(
    tmp_path: Path, assignment
):
    store = AppendOnlyEventStore(tmp_path / "events.jsonl")
    events = _b0_events(assignment)[:-1]
    assert store.append(events[0]) is True
    assert store.append(events[0]) is False
    for event in events[1:]:
        assert store.append(event) is True
    assert store.read_events() == events

    with pytest.raises(InstrumentationError, match="trial coverage"):
        store.complete_session(
            events[0]["session_id"],
            expected_trial_ids=[events[0]["trial_id"], "T-missing-assigned-trial"],
        )
    marker_path = store.complete_session(
        events[0]["session_id"],
        completed_at_utc="2026-08-26T00:10:00Z",
        expected_trial_ids=[events[0]["trial_id"]],
    )
    assert marker_path.is_file()
    marker = store.read_completion_marker(events[0]["session_id"])
    assert marker is not None
    assert marker["event_count"] == len(events)
    with pytest.raises(SessionAlreadyCompleteError):
        store.append(_b0_events(assignment)[-1])


def test_append_store_rejects_uuid_conflicts_private_content_and_marker_tampering(
    tmp_path: Path, assignment
):
    store = AppendOnlyEventStore(tmp_path / "events.jsonl")
    events = _b0_events(assignment)[:-1]
    store.append(events[0])

    conflict = deepcopy(events[1])
    conflict["event_uuid"] = events[0]["event_uuid"]
    with pytest.raises(EventUUIDConflictError):
        store.append(conflict)

    private = deepcopy(events[1])
    private["email"] = "participant@example.test"
    with pytest.raises(InstrumentationPrivacyError):
        store.append(private)

    for event in events[1:]:
        store.append(event)
    marker_path = store.complete_session(events[0]["session_id"])
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["events_sha256"] = "0" * 64
    marker_path.write_text(json.dumps(marker) + "\n", encoding="utf-8")
    with pytest.raises(InstrumentationCorruptionError, match="events_sha256 mismatch"):
        store.read_completion_marker(events[0]["session_id"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("raw_intent", "My name is Ada Example"),
        ("email", "ada@example.test"),
        ("hub_username", "real-hub-user"),
        ("authorization", "Bearer test-secret-token"),
        ("api_token", "sk-test-obvious-secret"),
    ],
)
def test_research_event_contract_rejects_free_text_identity_and_secrets(
    assignment, field, value
):
    private = deepcopy(_b0_events(assignment)[0])
    private[field] = value
    with pytest.raises(UserStudyValidationError, match="unexpected fields"):
        validate_event(private)


class _FakeStudyRecommendationRuntime:
    def __init__(self) -> None:
        self.catalog = load_image_catalog()
        self.images = self.catalog["images"]
        self.policy = SimpleNamespace(policy_version="resource-image-policy-v1")
        self.deployment = SimpleNamespace(backend="p2")
        self.resource_enricher = None
        self.backend_call_count = 0
        self.issued_username = None

    async def issue(self, username, values):
        self.backend_call_count += 1
        self.issued_username = username
        return {
            "applied_profile": "small",
            "recommendation": {"image_id": "minimal-python"},
        }


def _study_runtime(tmp_path: Path, assignment: AssignmentManifest, task_set):
    fake = _FakeStudyRecommendationRuntime()
    store = AppendOnlyEventStore(tmp_path / "events.jsonl")
    gates = StudyGateStore(tmp_path)
    runtime = StudySessionRuntime(
        assignment_manifest=assignment,
        browser_task_set=browser_safe_task_set(task_set),
        recommendation_runtime=fake,
        event_store=store,
        gate_store=gates,
        mark_restart_incomplete=False,
    )
    return runtime, fake, store, gates


def test_hub_routes_every_terminal_task_through_schedule_derived_forms(
    tmp_path: Path, assignment, task_set
):
    runtime, _, _, gates = _study_runtime(tmp_path, assignment, task_set)
    participant = assignment.assignments[0]
    runtime.acknowledge_consent(participant.participant_id)
    observed_types = []
    while (active := runtime.current_task(participant.participant_id)) is not None:
        if runtime.transition_required(active):
            runtime.acknowledge_transition(participant.participant_id)
        runtime.ensure_task_shown(active)
        runtime.record(
            participant.participant_id,
            active.assigned.trial_id,
            EventType.CANCEL,
            cancel_reason=CancelReason.PARTICIPANT_CANCELLED,
        )
        while (pending := runtime.pending_questionnaire(participant.participant_id)):
            kind = QuestionnaireType(pending["questionnaire_type"])
            observed_types.append(kind.value)
            if kind is QuestionnaireType.SEQ_TASK:
                responses = {SEQ_ITEM_ID: None}
            elif kind is QuestionnaireType.POST_CONDITION:
                responses = {
                    **{item: None for item in SUS_ITEM_IDS},
                    **{item: None for item in CUSTOM_ITEM_IDS},
                }
            else:
                responses = {FINAL_PREFERENCE_ID: None}
            runtime.record_questionnaire(
                participant.participant_id,
                pending["questionnaire_id"],
                str(uuid.uuid4()),
                responses,
            )
            if kind is QuestionnaireType.FINAL_PREFERENCE:
                break
    observed = {
        row["questionnaire_id"] for row in gates.questionnaire_records()
    }
    assert observed == expected_questionnaire_ids(participant)
    assert Counter(observed_types) == {
        QuestionnaireType.SEQ_TASK.value: 6,
        QuestionnaireType.POST_CONDITION.value: 2,
        QuestionnaireType.FINAL_PREFERENCE.value: 1,
    }
    assert runtime.questionnaire_complete(participant.participant_id)


def test_questionnaire_ui_freezes_anchors_and_separates_custom_items():
    seq_html = questionnaire_form(
        {
            "questionnaire_type": "seq_task",
            "questionnaire_id": "seq:synthetic",
            "condition": "B0",
        },
        "xsrf",
        str(uuid.uuid4()),
    )
    assert "1 = Very difficult; 7 = Very easy" in seq_html
    post_html = questionnaire_form(
        {
            "questionnaire_type": "post_condition",
            "questionnaire_id": "post_condition:1",
            "condition": "P2",
        },
        "xsrf",
        str(uuid.uuid4()),
    )
    assert "1 = Strongly disagree; 5 = Strongly agree" in post_html
    assert "CUSTOM Likert items (not SUS dimensions)" in post_html


def test_preview_token_binding_accepts_authenticator_case_mapping_only(
    tmp_path: Path, assignment, task_set
):
    runtime, fake, _, _ = _study_runtime(tmp_path, assignment, task_set)
    participant = next(
        item for item in assignment.assignments if item.condition_order[0] is Condition.P2
    )
    runtime.acknowledge_consent(participant.participant_id)
    active = runtime.current_task(participant.participant_id)
    assert active is not None and active.assigned.condition is Condition.P2
    runtime.ensure_task_shown(active)
    runtime.record(
        participant.participant_id, active.assigned.trial_id, EventType.INTENT_FOCUS
    )
    runtime.record(
        participant.participant_id, active.assigned.trial_id, EventType.INTENT_EDIT
    )

    asyncio.run(
        runtime.issue_preview(
            participant.participant_id,
            active.assigned.trial_id,
            "synthetic standard-library Python smoke",
            recommendation_username=participant.participant_id.lower(),
        )
    )
    assert fake.issued_username == participant.participant_id.lower()
    with pytest.raises(StudyHubError, match="differs from the issued"):
        asyncio.run(
            runtime.issue_preview(
                participant.participant_id,
                active.assigned.trial_id,
                "synthetic standard-library Python smoke",
                recommendation_username="p-000000000000",
            )
        )


def test_hub_uses_one_gold_free_option_snapshot_and_b0_never_calls_p2(
    tmp_path: Path, assignment, task_set
):
    runtime, fake, _, _ = _study_runtime(tmp_path, assignment, task_set)
    participant = next(
        item for item in assignment.assignments if item.condition_order[0] is Condition.B0
    )
    b0_task = participant.task_sequence[0]
    p2_task = next(
        item for item in participant.task_sequence if item.condition is Condition.P2
    )
    snapshot = shared_option_snapshot(fake)
    assert [item["profile_id"] for item in snapshot["profiles"]] == [
        "small",
        "medium",
        "large",
    ]
    assert [item["image_id"] for item in snapshot["images"]] == list(fake.images)

    common = {
        "preview_endpoint": "/hub/study/preview",
        "event_endpoint": "/hub/study/event",
        "advance_endpoint": "/hub/study/advance",
        "consent_version": assignment.consent_version,
    }
    b0_html = options_form(
        fake,
        ActiveStudyTask(participant, b0_task, "Synthetic B0 scenario"),
        **common,
    )
    p2_html = options_form(
        fake,
        ActiveStudyTask(participant, p2_task, "Synthetic P2 scenario"),
        **common,
    )
    panel = re.compile(r'<section id="shared-reference".*?</section>', re.DOTALL)
    assert panel.search(b0_html).group(0) == panel.search(p2_html).group(0)
    assert "No recommendation is made" in b0_html
    assert "Preview recommendation" in p2_html
    assert "if(!editSent)" in p2_html
    assert 'event("intent_edit")' in p2_html
    assert "event.data" not in p2_html and "event.key" not in p2_html

    spawner = SimpleNamespace(
        extra_resource_guarantees={"stale": 1},
        extra_resource_limits={"stale": 1},
    )
    apply_b0_selection(
        spawner,
        fake,
        {
            "study_condition": "B0",
            "applied_profile": "small",
            "applied_image_id": "minimal-python",
        },
    )
    assert spawner.image == fake.images["minimal-python"]["reference"]
    assert spawner.cpu_limit == 0.5
    assert fake.backend_call_count == 0
    assert "gold" not in json.dumps(runtime.browser_task_set)


def test_hub_restart_marks_prefix_excluded_and_exports_content_free_staging(
    tmp_path: Path, assignment, task_set
):
    runtime, fake, store, gates = _study_runtime(tmp_path, assignment, task_set)
    participant = assignment.assignments[0]
    runtime.acknowledge_consent(participant.participant_id)
    active = runtime.current_task(participant.participant_id)
    assert active is not None
    first = runtime.ensure_task_shown(active)
    assert runtime.ensure_task_shown(active).event_uuid == first.event_uuid

    restarted = StudySessionRuntime(
        assignment_manifest=assignment,
        browser_task_set=browser_safe_task_set(task_set),
        recommendation_runtime=fake,
        event_store=store,
        gate_store=gates,
        mark_restart_incomplete=True,
    )
    with pytest.raises(StudySessionIncompleteError):
        restarted.current_task(participant.participant_id)

    sessions = [
        json.loads(line)
        for line in (tmp_path / "sessions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    exclusions = [
        json.loads(line)
        for line in (tmp_path / "exclusions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(sessions) == len(exclusions) == 1
    assert sessions[0]["participant_id"] == participant.participant_id
    assert sessions[0]["session_status"] == "excluded"
    assert sessions[0]["consent_acknowledged"] is True
    assert exclusions[0]["reason_code"] == "instrumentation_corruption"
    assert "name" not in json.dumps({"sessions": sessions, "exclusions": exclusions})
    assert "email" not in json.dumps({"sessions": sessions, "exclusions": exclusions})


def test_gate_store_exports_completed_session_idempotently(tmp_path: Path, assignment):
    gates = StudyGateStore(tmp_path)
    participant = assignment.assignments[0]
    timestamp = "2026-08-26T00:00:00Z"
    gates.acknowledge_consent(
        assignment,
        participant,
        acknowledged_at_utc=timestamp,
    )
    first = gates.record_completed_session(
        assignment,
        participant,
        completed_at_utc=timestamp,
    )
    second = gates.record_completed_session(
        assignment,
        participant,
        completed_at_utc=timestamp,
    )
    assert first == second
    rows = first.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    record = json.loads(rows[0])
    assert record["session_status"] == "complete"
    assert record["consent_version"] == assignment.consent_version


def test_metric_derivation_uses_matched_pairs_and_never_emits_b0_rankings(
    assignment, task_set
):
    events = [*_b0_events(assignment), *_p2_events(assignment)]
    derived = derive_study_metrics(
        events,
        task_set,
        assignment,
        execution_status="DRY_RUN",
    )
    task_rows = derived["task_outcomes"]
    assert len(task_rows) == 2
    assert {row["condition"] for row in task_rows} == {"B0", "P2"}
    b0 = next(row for row in task_rows if row["condition"] == "B0")
    p2 = next(row for row in task_rows if row["condition"] == "P2")
    assert b0["decision_time_seconds"] == 3.0
    assert b0["decision_time_status"] == "available"
    assert b0["end_to_end_seconds"] == 4.0
    assert b0["end_to_end_status"] == "available"
    assert b0["control_action_count"] == 3
    assert b0["edit_count"] == 0
    assert b0["override_count"] is None
    assert p2["edit_count"] == 1
    assert p2["control_action_count"] == 5
    assert p2["total_action_count"] == 6
    assert p2["override_count"] == 1
    assert p2["final_override"] is True
    assert p2["correction_count"] == 2
    assert p2["end_to_end_seconds"] is None
    assert p2["end_to_end_status"] == "unavailable_notebook_ready"

    pair_rows = derived["matched_pair_outcomes"]
    assert len(pair_rows) == 1
    assert pair_rows[0]["participant_id"] == assignment.assignments[0].participant_id
    assert pair_rows[0]["pair_complete"] is True
    rendered = json.dumps(derived, sort_keys=True).lower()
    assert not any(term in rendered for term in ("hit@", "mrr", "ndcg", "b0_acceptance"))
    assert derived["summary"]["formal_inference"]["status"] == "NOT_COMPUTED"


def _p2_preview_to_final_events(assignment, preview_candidate, final_candidate):
    participant, task = _assigned_task(assignment, Condition.P2)
    events = [
        _event(assignment, participant, task, 0, EventType.TASK_SHOWN),
        _event(assignment, participant, task, 1, EventType.INTENT_FOCUS),
        _event(assignment, participant, task, 2, EventType.INTENT_EDIT),
        _event(assignment, participant, task, 3, EventType.PREVIEW_REQUESTED),
        _event(
            assignment,
            participant,
            task,
            4,
            EventType.PREVIEW_RECEIVED,
            profile_id=preview_candidate.profile_id,
            image_id=preview_candidate.image_id,
            preview_status="success",
        ),
    ]
    index = 5
    if final_candidate.profile_id != preview_candidate.profile_id:
        events.append(
            _event(
                assignment,
                participant,
                task,
                index,
                EventType.PROFILE_CHANGED,
                old_profile_id=preview_candidate.profile_id,
                new_profile_id=final_candidate.profile_id,
            )
        )
        index += 1
    if final_candidate.image_id != preview_candidate.image_id:
        events.append(
            _event(
                assignment,
                participant,
                task,
                index,
                EventType.IMAGE_CHANGED,
                old_image_id=preview_candidate.image_id,
                new_image_id=final_candidate.image_id,
            )
        )
        index += 1
    if final_candidate.candidate_id != preview_candidate.candidate_id:
        events.append(
            _event(
                assignment,
                participant,
                task,
                index,
                EventType.OVERRIDE,
                profile_id=final_candidate.profile_id,
                image_id=final_candidate.image_id,
            )
        )
        index += 1
    events.append(
        _event(
            assignment,
            participant,
            task,
            index,
            EventType.CONFIRM,
            profile_id=final_candidate.profile_id,
            image_id=final_candidate.image_id,
        )
    )
    return events


def test_scoring_uses_final_human_selection_not_p2_recommendation(
    assignment, task_set
):
    participant, task = _assigned_task(assignment, Condition.P2)
    pair = task_set.pair_by_id(task.pair_id)
    corpus = build_candidate_corpus()
    preferred = corpus.get(pair.gold.preferred_candidate_id)
    assert preferred is not None
    wrong = next(
        candidate
        for candidate in corpus.candidates
        if candidate.candidate_id not in pair.gold.acceptable_candidate_ids
        and candidate.profile_id != preferred.profile_id
        and candidate.image_id != preferred.image_id
    )

    corrected = derive_study_metrics(
        _p2_preview_to_final_events(assignment, wrong, preferred),
        task_set,
        assignment,
    )["task_outcomes"][0]
    overridden_wrong = derive_study_metrics(
        _p2_preview_to_final_events(assignment, preferred, wrong),
        task_set,
        assignment,
    )["task_outcomes"][0]
    assert corrected["selection_correct"] is True
    assert corrected["selection_acceptable"] is True
    assert corrected["selection_success"] is (
        corrected["profile_acceptable"]
        and corrected["image_acceptable"]
        and corrected["hard_constraints_satisfied"]
    )
    assert corrected["selection_success"] == corrected["selection_acceptable"]
    assert corrected["final_override"] is True
    assert overridden_wrong["selection_correct"] is False
    assert overridden_wrong["selection_acceptable"] is False
    assert overridden_wrong["final_override"] is True

    # Scoring has no condition argument: identical final selections necessarily
    # produce identical labels for B0 and P2.
    b0_label = score_final_selection(
        pair.gold,
        profile_id=preferred.profile_id,
        image_id=preferred.image_id,
    )
    p2_label = score_final_selection(
        pair.gold,
        profile_id=preferred.profile_id,
        image_id=preferred.image_id,
    )
    assert b0_label == p2_label


def test_selection_success_disagreement_with_legacy_alias_fails_closed(
    assignment, task_set, monkeypatch
):
    real_scorer = metrics_module.score_final_selection

    def drifting_scorer(*args, **kwargs):
        score = real_scorer(*args, **kwargs)
        from dataclasses import replace

        return replace(score, selection_acceptable=not score.selection_acceptable)

    monkeypatch.setattr(metrics_module, "score_final_selection", drifting_scorer)
    with pytest.raises(
        UserStudyValidationError,
        match="SelectionSuccess differs from frozen acceptable-candidate scoring",
    ):
        derive_study_metrics(_b0_events(assignment), task_set, assignment)


def test_b0_final_manual_correction_scores_the_confirmed_selection(
    assignment, task_set
):
    participant, task = _assigned_task(assignment, Condition.B0)
    pair = task_set.pair_by_id(task.pair_id)
    corpus = build_candidate_corpus()
    preferred = corpus.get(pair.gold.preferred_candidate_id)
    assert preferred is not None
    wrong = next(
        candidate
        for candidate in corpus.candidates
        if candidate.profile_id != preferred.profile_id
        and candidate.image_id != preferred.image_id
    )
    events = [
        _event(assignment, participant, task, 0, EventType.TASK_SHOWN),
        _event(
            assignment,
            participant,
            task,
            1,
            EventType.PROFILE_CHANGED,
            old_profile_id=None,
            new_profile_id=wrong.profile_id,
        ),
        _event(
            assignment,
            participant,
            task,
            2,
            EventType.IMAGE_CHANGED,
            old_image_id=None,
            new_image_id=wrong.image_id,
        ),
        _event(
            assignment,
            participant,
            task,
            3,
            EventType.PROFILE_CHANGED,
            old_profile_id=wrong.profile_id,
            new_profile_id=preferred.profile_id,
        ),
        _event(
            assignment,
            participant,
            task,
            4,
            EventType.IMAGE_CHANGED,
            old_image_id=wrong.image_id,
            new_image_id=preferred.image_id,
        ),
        _event(
            assignment,
            participant,
            task,
            5,
            EventType.CONFIRM,
            profile_id=preferred.profile_id,
            image_id=preferred.image_id,
        ),
    ]
    row = derive_study_metrics(events, task_set, assignment)["task_outcomes"][0]
    assert row["selection_correct"] is True
    assert row["selection_acceptable"] is True
    assert row["correction_count"] == 2


def test_semantic_action_counts_ignore_focus_noise_and_system_events(
    assignment, task_set
):
    participant, task = _assigned_task(assignment, Condition.P2)

    def stream(focus_count):
        events = [_event(assignment, participant, task, 0, EventType.TASK_SHOWN)]
        index = 1
        for _ in range(focus_count):
            events.append(
                _event(
                    assignment,
                    participant,
                    task,
                    index,
                    EventType.INTENT_FOCUS,
                )
            )
            index += 1
        events.extend(
            [
                _event(
                    assignment,
                    participant,
                    task,
                    index,
                    EventType.INTENT_EDIT,
                ),
                _event(
                    assignment,
                    participant,
                    task,
                    index + 1,
                    EventType.PREVIEW_REQUESTED,
                ),
                _event(
                    assignment,
                    participant,
                    task,
                    index + 2,
                    EventType.PREVIEW_RECEIVED,
                    profile_id="small",
                    image_id="minimal-python",
                    preview_status="success",
                ),
                _event(
                    assignment,
                    participant,
                    task,
                    index + 3,
                    EventType.CONFIRM,
                    profile_id="small",
                    image_id="minimal-python",
                ),
            ]
        )
        return events

    one_focus = derive_study_metrics(stream(1), task_set, assignment)[
        "task_outcomes"
    ][0]
    many_focus = derive_study_metrics(stream(5), task_set, assignment)[
        "task_outcomes"
    ][0]
    assert one_focus["control_action_count"] == many_focus["control_action_count"] == 2
    assert one_focus["edit_count"] == many_focus["edit_count"] == 1
    assert one_focus["total_action_count"] == many_focus["total_action_count"] == 3
    assert one_focus["correction_count"] == many_focus["correction_count"] == 0


def test_cancelled_task_remains_in_correctness_and_action_denominators(
    assignment, task_set
):
    participant, task = _assigned_task(assignment, Condition.B0)
    events = [
        _event(assignment, participant, task, 0, EventType.TASK_SHOWN),
        _event(
            assignment,
            participant,
            task,
            1,
            EventType.CANCEL,
            cancel_reason="participant_cancelled",
        ),
    ]
    row = derive_study_metrics(events, task_set, assignment)["task_outcomes"][0]
    assert row["confirmed"] is False
    assert row["cancelled"] is True
    assert row["correct_selection"] is False
    assert row["decision_time_seconds"] is None
    assert row["decision_time_status"] == "unavailable_cancelled"
    assert row["end_to_end_seconds"] is None
    assert row["end_to_end_status"] == "unavailable_no_confirmation"
    assert row["control_action_count"] == 1
    assert row["total_action_count"] == 1


def test_cli_synthetic_prepare_validate_finalize_cycle_is_immutable(
    tmp_path: Path, assignment, task_set, capsys
):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    assignment_path = prepared / "assignment-manifest.json"
    browser_path = prepared / "browser-task-set.json"
    assignment_path.write_text(
        json.dumps(assignment.to_dict(), sort_keys=True) + "\n", encoding="utf-8"
    )
    browser_path.write_text(
        json.dumps(browser_safe_task_set(task_set), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    events_path = tmp_path / "events.jsonl"
    events = [*_b0_events(assignment), *_p2_events(assignment)]
    events_path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )

    assert user_study_main(
        [
            "validate-events",
            str(events_path),
            "--task-set",
            str(TASK_SET_PATH),
            "--assignments",
            str(assignment_path),
        ]
    ) == 0
    capsys.readouterr()

    result = tmp_path / "result"
    finalize_args = [
        "finalize",
        "--run-id",
        "synthetic-cycle",
        "--task-set",
        str(TASK_SET_PATH),
        "--assignments",
        str(assignment_path),
        "--events",
        str(events_path),
        "--execution-status",
        "DRY_RUN",
        "--created-at-utc",
        FIXED_GENERATED_AT,
        "--output-dir",
        str(result),
    ]
    assert user_study_main(finalize_args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["execution_status"] == "DRY_RUN"
    assert (result / "raw" / "events.jsonl").read_bytes() == events_path.read_bytes()
    assert (result / "derived" / "task-outcomes.jsonl").is_file()
    assert (result / "derived" / "matched-pair-outcomes.jsonl").is_file()
    status = json.loads((result / "report" / "status.json").read_text())
    assert status["observed_evidence"] is False
    provenance = json.loads((result / "manifest.json").read_text())
    assert provenance["execution_status"] == "DRY_RUN"
    assert str(tmp_path) not in json.dumps(provenance)

    assert user_study_main(finalize_args) == 2
    assert "already exists" in capsys.readouterr().err

    package_result = subprocess.run(
        [
            str(ROOT / ".venv" / "bin" / "python"),
            str(ROOT / "scripts" / "user_study_package.py"),
            "study-config-manifest",
            "--assignment",
            str(assignment_path),
            "--browser-tasks",
            str(browser_path),
            "--allow-development",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    mounted = json.loads(package_result.stdout)
    mounted_data = mounted["data"]
    assert set(mounted_data) == {
        "assignment-manifest.json",
        "browser-task-set.json",
    }
    assert "gold" not in mounted_data["browser-task-set.json"]
    rollout_result = subprocess.run(
        [
            str(ROOT / ".venv" / "bin" / "python"),
            str(ROOT / "scripts" / "user_study_package.py"),
            "rollout-values",
            "--assignment",
            str(assignment_path),
            "--browser-tasks",
            str(browser_path),
            "--allow-development",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rollout_env = json.loads(rollout_result.stdout)["hub"]["extraEnv"]
    assert rollout_env["INTENT_SPAWNER_USER_STUDY_ASSIGNMENT_CHECKSUM"] == assignment.checksum
    assert rollout_env["INTENT_SPAWNER_USER_STUDY_CONFIG_IDENTITY"] == assignment.config_identity


def test_cli_prepares_and_binds_secret_free_fairness_identity(tmp_path: Path, capsys):
    config_path = tmp_path / "study-config.json"
    config_path.write_text(
        json.dumps({"study_values_revision": "synthetic-v1"}) + "\n",
        encoding="utf-8",
    )
    environment_path = tmp_path / "environment.json"
    assert user_study_main(
        [
            "prepare-environment",
            "--output",
            str(environment_path),
            "--environment-id",
            "synthetic-study-environment",
            "--kubernetes-environment-id",
            "synthetic-no-cluster",
            "--freeze-id",
            "development-unfrozen",
            "--config-identity",
            str(config_path),
            "--deployment-revision",
            "a" * 40,
        ]
    ) == 0
    capsys.readouterr()
    environment = json.loads(environment_path.read_text())
    assert environment["mode"] == "development_unfrozen"
    assert environment["fairness_manifest"]["b0_environment_sha256"] == environment[
        "fairness_manifest"
    ]["p2_environment_sha256"]

    output = tmp_path / "prepared"
    assert user_study_main(
        [
            "generate-assignments",
            str(TASK_SET_PATH),
            "--output-dir",
            str(output),
            "--study-id",
            "e3-cli-fairness-test",
            "--participant-count",
            "1",
            "--seed",
            "20260827",
            "--consent-version",
            "consent-test-v1",
            "--git-revision",
            "a" * 40,
            "--environment-identity",
            str(environment_path),
            "--config-identity",
            str(config_path),
            "--generated-at-utc",
            FIXED_GENERATED_AT,
        ]
    ) == 0
    capsys.readouterr()
    manifest = json.loads((output / "assignment-manifest.json").read_text())
    assert manifest["environment_identity"]["fairness_manifest"] == environment[
        "fairness_manifest"
    ]
    assert manifest["event_schema_version"] == EVENT_SCHEMA_VERSION
    assert "selection_scoring_version" in manifest


def test_study_helm_overlay_is_opt_in_and_package_verifies_without_data():
    values = yaml.safe_load((ROOT / "helm" / "user-study-values.yaml").read_text())
    assert values["hub"]["extraEnv"]["INTENT_SPAWNER_USER_STUDY_ENABLED"] == "true"
    assert any(
        volume.get("persistentVolumeClaim", {}).get("claimName")
        == "intent-spawner-user-study-evidence"
        for volume in values["hub"]["extraVolumes"]
    )
    code = values["hub"]["extraConfig"]["50-protocol-v5-user-study"]
    assert "install_user_study" in code
    result = subprocess.run(
        [
            str(ROOT / ".venv" / "bin" / "python"),
            str(ROOT / "scripts" / "user_study_package.py"),
            "verify",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["study_config_validated"] is False
    assert "spawn_pending.html" in payload["runtime_files"]
    assert "fairness.py" in payload["runtime_files"]
    assert "scoring.py" in payload["runtime_files"]

    installer = (ROOT / "scripts" / "install-user-study.sh").read_text()
    assert installer.count(
        "kubectl apply --server-side --field-manager=intent-spawner-user-study"
    ) == 4
    for manifest_name in (
        '"$RECOMMENDER_MANIFEST"',
        '"$STUDY_ADAPTER_MANIFEST"',
        '"$STUDY_CONFIG_MANIFEST"',
        '"$STUDY_PVC_MANIFEST"',
    ):
        assert f'-f {manifest_name}' in installer


def test_real_adapter_synthetic_smoke_path_reaches_immutable_finalization():
    result = run_synthetic_hub_smoke(TASK_SET_PATH)
    assert result["status"] == "PASS"
    assert result["evidence_class"] == "SYNTHETIC_DRY_RUN"
    assert result["task_count"] == 8
    assert result["condition_task_counts"] == {"B0": 4, "P2": 4}
    assert result["notebook_ready_count"] == 8
    assert result["finalized_measured_task_count"] == 6
    assert result["required_analysis_output_count"] == 17
    assert result["privacy_audit_status"] == "PASS"
    assert result["temporary_output_removed"] is True
