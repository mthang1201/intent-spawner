"""Automatic, non-inferential outcomes for the Protocol-v5 E3 user study.

The functions in this module operate only on the content-free event contract.
They do not consume intent text and they deliberately emit no p-values,
confidence claims, B0 recommendation metrics, or other formal inference.
Warm-up trials are excluded from the default measured outputs.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
import math
import statistics
from typing import Any

from .assignment import (
    AssignmentManifest,
    validate_assignment_manifest,
)
from .schemas import (
    Condition,
    EventType,
    PreviewStatus,
    StudyEvent,
    TaskPhase,
    TaskSet,
    UserStudyValidationError,
    parse_task_set,
    validate_event_stream,
)
from .scoring import FINAL_SELECTION_SCORING_VERSION, score_final_selection


TASK_OUTCOME_SCHEMA_VERSION = "protocol-v5-user-study-task-outcome-v1.1.0"
MATCHED_PAIR_OUTCOME_SCHEMA_VERSION = (
    "protocol-v5-user-study-matched-pair-outcome-v1.1.0"
)
SUMMARY_SCHEMA_VERSION = "protocol-v5-user-study-summary-v1.1.0"

_CONTROL_ACTIONS = frozenset(
    {
        EventType.PREVIEW_REQUESTED,
        EventType.PROFILE_CHANGED,
        EventType.IMAGE_CHANGED,
        EventType.OVERRIDE,
        EventType.CONFIRM,
        EventType.CANCEL,
    }
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise UserStudyValidationError(message)


def _assignment_binding(
    events: Sequence[StudyEvent],
    manifest: AssignmentManifest,
    task_set: TaskSet,
) -> None:
    """Enforce assignment fields not covered by the generic stream validator."""

    validate_assignment_manifest(manifest, task_set=task_set)
    _require(
        manifest.task_set_sha256 == task_set.checksum,
        "assignment task-set checksum differs from scoring task set",
    )
    assigned: dict[tuple[str, str], tuple[str, str, str, Condition]] = {}
    for participant in manifest.assignments:
        for task in participant.task_sequence:
            assigned[(participant.participant_id, task.trial_id)] = (
                participant.session_id,
                task.task_id,
                task.pair_id,
                task.condition,
            )
    for event in events:
        _require(event.study_id == manifest.study_id, "event study_id drift")
        _require(
            event.assignment_id == manifest.assignment_id,
            "event assignment_id drift",
        )
        _require(
            event.consent_version == manifest.consent_version,
            "event consent_version drift",
        )
        expected = assigned.get((event.participant_id, event.trial_id))
        _require(expected is not None, "event trial is absent from assignment")
        _require(
            expected
            == (
                event.session_id,
                event.task_id,
                event.pair_id,
                event.condition,
            ),
            "event session/trial identity differs from assignment",
        )


def _candidate(profile_id: str | None, image_id: str | None) -> str | None:
    if profile_id is None or image_id is None:
        return None
    return f"{profile_id}-{image_id}"


def _trial_corrections(trial: Sequence[StudyEvent]) -> int:
    """Count participant changes to an already selected component.

    Initial selection from a blank B0 selector is not a correction.  A P2
    preview is system output and is not itself a correction.  A later manual
    profile/image change with a non-null old value counts once per component;
    override and confirm annotate/commit that state and are not double-counted.
    """

    return sum(
        (
            event.event_type is EventType.PROFILE_CHANGED
            and event.old_profile_id is not None
        )
        or (
            event.event_type is EventType.IMAGE_CHANGED
            and event.old_image_id is not None
        )
        for event in trial
    )


def _parse_events(
    events: Iterable[StudyEvent | Mapping[str, Any]],
    *,
    task_set: TaskSet,
    assignment_manifest: AssignmentManifest | Mapping[str, Any] | None,
) -> tuple[StudyEvent, ...]:
    raw = tuple(events)
    if not raw:
        return ()
    manifest: AssignmentManifest | None
    if assignment_manifest is None:
        manifest = None
    elif isinstance(assignment_manifest, AssignmentManifest):
        manifest = assignment_manifest
    else:
        manifest = AssignmentManifest.from_dict(assignment_manifest)
    parsed = validate_event_stream(
        raw,
        assignment_manifest=manifest,
        task_set=task_set,
    )
    if manifest is not None:
        _assignment_binding(parsed, manifest, task_set)
    return parsed


def derive_task_outcomes(
    events: Iterable[StudyEvent | Mapping[str, Any]],
    task_set: TaskSet | Mapping[str, Any],
    assignment_manifest: AssignmentManifest | Mapping[str, Any] | None = None,
    *,
    include_warmups: bool = False,
) -> list[dict[str, Any]]:
    """Derive one deterministic task row from each complete event trial.

    Cancellations remain in the denominator with ``correct_selection=false``.
    Decision and end-to-end time remain null when their terminal event was not
    observed.  The output contains no participant-provided content.
    """

    tasks = parse_task_set(task_set)
    manifest = (
        assignment_manifest
        if isinstance(assignment_manifest, AssignmentManifest)
        else (
            AssignmentManifest.from_dict(assignment_manifest)
            if assignment_manifest is not None
            else None
        )
    )
    parsed = _parse_events(
        events,
        task_set=tasks,
        assignment_manifest=manifest,
    )
    design: dict[tuple[str, str], dict[str, Any]] = {}
    if manifest is not None:
        for participant in manifest.assignments:
            for assigned in participant.task_sequence:
                variants = sorted(
                    tasks.pair_by_id(assigned.pair_id).tasks,
                    key=lambda item: item.variant_id,
                )
                variant_slot = next(
                    index
                    for index, variant in enumerate(variants)
                    if variant.variant_id == assigned.variant_id
                )
                design[(participant.participant_id, assigned.trial_id)] = {
                    "matched_pair_id": assigned.pair_id,
                    "variant_id": assigned.variant_id,
                    "variant_slot": variant_slot,
                    "within_pair_variant_slot": variant_slot,
                    "period": assigned.period,
                    "position": assigned.position_in_period,
                    "position_in_period": assigned.position_in_period,
                    "sequence_index": assigned.sequence_index,
                    "condition_order": "-then-".join(
                        item.value for item in participant.condition_order
                    ),
                    "counterbalance_cell": participant.counterbalance_cell,
                }
    grouped: dict[tuple[str, str], list[StudyEvent]] = defaultdict(list)
    for event in parsed:
        grouped[(event.session_id, event.trial_id)].append(event)

    outcomes: list[dict[str, Any]] = []
    for trial in grouped.values():
        trial.sort(key=lambda item: item.event_index)
        first = trial[0]
        task = tasks.task_by_id(first.task_id)
        pair = tasks.pair_by_id(first.pair_id)
        if task.phase is TaskPhase.WARM_UP and not include_warmups:
            continue

        shown = first.monotonic_seconds
        confirmation = next(
            (event for event in trial if event.event_type is EventType.CONFIRM),
            None,
        )
        cancellation = next(
            (event for event in trial if event.event_type is EventType.CANCEL),
            None,
        )
        ready = next(
            (
                event
                for event in trial
                if event.event_type is EventType.NOTEBOOK_READY
            ),
            None,
        )
        profile_id = confirmation.profile_id if confirmation is not None else None
        image_id = confirmation.image_id if confirmation is not None else None
        selection_score = score_final_selection(
            pair.gold, profile_id=profile_id, image_id=image_id
        )
        selection_success = (
            selection_score.profile_acceptable
            and selection_score.image_acceptable
            and selection_score.hard_constraints_satisfied
        )
        _require(
            selection_success == selection_score.selection_acceptable,
            "SelectionSuccess differs from frozen acceptable-candidate scoring",
        )
        candidate_id = selection_score.candidate_id
        recommendation: tuple[str, str] | None = None
        for event in trial:
            if (
                event.event_type is EventType.PREVIEW_RECEIVED
                and event.preview_status is PreviewStatus.SUCCESS
            ):
                recommendation = (event.profile_id, event.image_id)  # type: ignore[arg-type]
        override_count: int | None
        final_override: bool | None
        if first.condition is Condition.P2:
            override_count = sum(
                event.event_type is EventType.OVERRIDE for event in trial
            )
            final_override = (
                None
                if confirmation is None
                else (
                    recommendation is not None
                    and (confirmation.profile_id, confirmation.image_id)
                    != recommendation
                )
            )
        else:
            override_count = None
            final_override = None

        control_count = sum(
            event.event_type in _CONTROL_ACTIONS for event in trial
        )
        edit_count = sum(
            event.event_type is EventType.INTENT_EDIT for event in trial
        )
        outcomes.append(
            {
                "schema_version": TASK_OUTCOME_SCHEMA_VERSION,
                "study_id": first.study_id,
                "assignment_id": first.assignment_id,
                "session_id": first.session_id,
                "participant_id": first.participant_id,
                "trial_id": first.trial_id,
                "task_id": first.task_id,
                "pair_id": first.pair_id,
                "phase": task.phase.value,
                "condition": first.condition.value,
                **design.get(
                    (first.participant_id, first.trial_id),
                    {
                        "matched_pair_id": first.pair_id,
                        "variant_id": None,
                        "variant_slot": None,
                        "within_pair_variant_slot": None,
                        "period": None,
                        "position": None,
                        "position_in_period": None,
                        "sequence_index": None,
                        "condition_order": None,
                        "counterbalance_cell": None,
                    },
                ),
                "confirmed": confirmation is not None,
                "cancelled": cancellation is not None,
                "cancel_reason": (
                    cancellation.cancel_reason.value
                    if cancellation is not None
                    and cancellation.cancel_reason is not None
                    else None
                ),
                "profile_id": profile_id,
                "image_id": image_id,
                "candidate_id": candidate_id,
                **selection_score.to_dict(),
                "selection_success": selection_success,
                # Compatibility alias retained for pre-observation v1 tooling.
                # Its frozen meaning is selection acceptability, which remains
                # the primary binary accuracy outcome.
                "correct_selection": selection_score.selection_acceptable,
                "decision_time_seconds": (
                    confirmation.monotonic_seconds - shown
                    if confirmation is not None
                    else None
                ),
                "decision_time_status": (
                    "available"
                    if confirmation is not None
                    else (
                        "unavailable_cancelled"
                        if cancellation is not None
                        else "unavailable_no_confirmation"
                    )
                ),
                "notebook_ready_observed": ready is not None,
                "end_to_end_seconds": (
                    ready.monotonic_seconds - shown if ready is not None else None
                ),
                "end_to_end_status": (
                    "available"
                    if ready is not None
                    else (
                        "unavailable_no_confirmation"
                        if confirmation is None
                        else "unavailable_notebook_ready"
                    )
                ),
                "control_action_count": control_count,
                "edit_count": edit_count,
                "total_action_count": control_count + edit_count,
                "interaction_count": control_count + edit_count,
                "override_count": override_count,
                "final_override": final_override,
                "correction_count": _trial_corrections(trial),
            }
        )
    outcomes.sort(
        key=lambda row: (
            str(row["participant_id"]),
            str(row["pair_id"]),
            str(row["condition"]),
            str(row["trial_id"]),
        )
    )
    return outcomes


def derive_matched_pair_outcomes(
    task_outcomes: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Join crossover observations on participant and matched pair.

    Rows with only one observed condition are retained as unpaired missingness
    rows.  Delta fields use the documented ``P2 - B0`` direction and are null
    where the relevant outcome is missing.
    """

    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for raw in task_outcomes:
        row = dict(raw)
        if row.get("phase") != TaskPhase.MEASURED.value:
            continue
        participant_id = row.get("participant_id")
        pair_id = row.get("pair_id")
        condition = row.get("condition")
        _require(isinstance(participant_id, str), "task outcome participant_id is invalid")
        _require(isinstance(pair_id, str), "task outcome pair_id is invalid")
        _require(condition in {Condition.B0.value, Condition.P2.value}, "task outcome condition is invalid")
        key = (participant_id, pair_id)
        _require(condition not in grouped[key], "duplicate participant/pair/condition outcome")
        grouped[key][condition] = row

    paired: list[dict[str, Any]] = []
    for (participant_id, pair_id), conditions in sorted(grouped.items()):
        b0 = conditions.get(Condition.B0.value)
        p2 = conditions.get(Condition.P2.value)

        def value(row: dict[str, Any] | None, field: str) -> Any:
            return row.get(field) if row is not None else None

        def delta(field: str) -> float | int | None:
            left = value(b0, field)
            right = value(p2, field)
            if left is None or right is None:
                return None
            return right - left

        correctness_delta = None
        exact_correctness_delta = None
        if b0 is not None and p2 is not None:
            correctness_delta = int(bool(p2["selection_acceptable"])) - int(
                bool(b0["selection_acceptable"])
            )
            exact_correctness_delta = int(bool(p2["selection_correct"])) - int(
                bool(b0["selection_correct"])
            )
        paired.append(
            {
                "schema_version": MATCHED_PAIR_OUTCOME_SCHEMA_VERSION,
                "participant_id": participant_id,
                "pair_id": pair_id,
                "pair_complete": b0 is not None and p2 is not None,
                "b0_task_id": value(b0, "task_id"),
                "p2_task_id": value(p2, "task_id"),
                "b0_correct_selection": value(b0, "correct_selection"),
                "p2_correct_selection": value(p2, "correct_selection"),
                "b0_selection_acceptable": value(b0, "selection_acceptable"),
                "p2_selection_acceptable": value(p2, "selection_acceptable"),
                "b0_selection_success": value(b0, "selection_success"),
                "p2_selection_success": value(p2, "selection_success"),
                "b0_selection_correct": value(b0, "selection_correct"),
                "p2_selection_correct": value(p2, "selection_correct"),
                "correctness_delta_p2_minus_b0": correctness_delta,
                "exact_correctness_delta_p2_minus_b0": exact_correctness_delta,
                "b0_confirmed": value(b0, "confirmed"),
                "p2_confirmed": value(p2, "confirmed"),
                "b0_cancelled": value(b0, "cancelled"),
                "p2_cancelled": value(p2, "cancelled"),
                "b0_cancel_reason": value(b0, "cancel_reason"),
                "p2_cancel_reason": value(p2, "cancel_reason"),
                "b0_decision_time_seconds": value(b0, "decision_time_seconds"),
                "p2_decision_time_seconds": value(p2, "decision_time_seconds"),
                "decision_time_delta_p2_minus_b0": delta(
                    "decision_time_seconds"
                ),
                "b0_total_action_count": value(b0, "total_action_count"),
                "p2_total_action_count": value(p2, "total_action_count"),
                "total_action_delta_p2_minus_b0": delta("total_action_count"),
                "b0_interaction_count": value(b0, "interaction_count"),
                "p2_interaction_count": value(p2, "interaction_count"),
                "interaction_delta_p2_minus_b0": delta("interaction_count"),
                "b0_end_to_end_seconds": value(b0, "end_to_end_seconds"),
                "p2_end_to_end_seconds": value(p2, "end_to_end_seconds"),
                "end_to_end_delta_p2_minus_b0": delta("end_to_end_seconds"),
            }
        )
    return paired


def _distribution(values: Iterable[float | int | None]) -> dict[str, Any]:
    present = [float(value) for value in values if value is not None]
    _require(all(math.isfinite(value) for value in present), "metric contains a non-finite value")
    if not present:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "minimum": None,
            "maximum": None,
        }
    return {
        "count": len(present),
        "mean": statistics.fmean(present),
        "median": statistics.median(present),
        "minimum": min(present),
        "maximum": max(present),
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _condition_summary(rows: Sequence[Mapping[str, Any]], condition: Condition) -> dict[str, Any]:
    selected = [row for row in rows if row["condition"] == condition.value]
    count = len(selected)
    confirmed = sum(bool(row["confirmed"]) for row in selected)
    ready = sum(bool(row["notebook_ready_observed"]) for row in selected)
    cancelled = sum(bool(row["cancelled"]) for row in selected)
    acceptable = sum(bool(row["selection_acceptable"]) for row in selected)
    success = sum(bool(row["selection_success"]) for row in selected)
    exact = sum(bool(row["selection_correct"]) for row in selected)
    result: dict[str, Any] = {
        "task_count": count,
        "participant_count": len({row["participant_id"] for row in selected}),
        "correct_count": acceptable,
        "correct_selection_rate": _rate(acceptable, count),
        "selection_acceptable_count": acceptable,
        "selection_acceptable_rate": _rate(acceptable, count),
        "selection_success_count": success,
        "selection_success_rate": _rate(success, count),
        "selection_correct_count": exact,
        "selection_correct_rate": _rate(exact, count),
        "confirmed_count": confirmed,
        "completion_rate": _rate(confirmed, count),
        "cancelled_count": cancelled,
        "cancellation_rate": _rate(cancelled, count),
        "decision_time_missing_count": count
        - sum(row["decision_time_seconds"] is not None for row in selected),
        "decision_time_seconds": _distribution(
            row["decision_time_seconds"] for row in selected
        ),
        "notebook_ready_count": ready,
        "readiness_rate_among_confirmed": _rate(ready, confirmed),
        "end_to_end_missing_count": count
        - sum(row["end_to_end_seconds"] is not None for row in selected),
        "end_to_end_seconds": _distribution(
            row["end_to_end_seconds"] for row in selected
        ),
        "control_action_count": _distribution(
            row["control_action_count"] for row in selected
        ),
        "edit_count": _distribution(row["edit_count"] for row in selected),
        "total_action_count": _distribution(
            row["total_action_count"] for row in selected
        ),
        "interaction_count": _distribution(
            row["interaction_count"] for row in selected
        ),
        "correction_count": _distribution(
            row["correction_count"] for row in selected
        ),
    }
    if condition is Condition.P2:
        result["override_count"] = _distribution(
            row["override_count"] for row in selected
        )
        result["final_override_count"] = sum(
            bool(row["final_override"]) for row in selected
        )
        result["final_override_observed_count"] = sum(
            row["final_override"] is not None for row in selected
        )
    else:
        result["override_count"] = None
        result["final_override_count"] = None
        result["final_override_observed_count"] = None
    return result


def _participant_deltas(
    paired_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in paired_rows:
        grouped[str(row["participant_id"])].append(row)
    results: list[dict[str, Any]] = []
    for participant_id, rows in sorted(grouped.items()):
        complete = [row for row in rows if row["pair_complete"]]

        def mean(field: str) -> float | None:
            values = [float(row[field]) for row in complete if row[field] is not None]
            return statistics.fmean(values) if values else None

        results.append(
            {
                "participant_id": participant_id,
                "observed_pair_count": len(rows),
                "complete_pair_count": len(complete),
                "mean_correctness_delta_p2_minus_b0": mean(
                    "correctness_delta_p2_minus_b0"
                ),
                "mean_decision_time_delta_p2_minus_b0": mean(
                    "decision_time_delta_p2_minus_b0"
                ),
                "mean_total_action_delta_p2_minus_b0": mean(
                    "total_action_delta_p2_minus_b0"
                ),
                "mean_interaction_delta_p2_minus_b0": mean(
                    "interaction_delta_p2_minus_b0"
                ),
                "mean_end_to_end_delta_p2_minus_b0": mean(
                    "end_to_end_delta_p2_minus_b0"
                ),
            }
        )
    return results


def summarize_outcomes(
    task_outcomes: Iterable[Mapping[str, Any]],
    matched_pair_outcomes: Iterable[Mapping[str, Any]] | None = None,
    *,
    execution_status: str = "DRY_RUN",
) -> dict[str, Any]:
    """Produce descriptive distributions and comparison-ready paired inputs."""

    tasks = [dict(row) for row in task_outcomes]
    pairs = (
        [dict(row) for row in matched_pair_outcomes]
        if matched_pair_outcomes is not None
        else derive_matched_pair_outcomes(tasks)
    )
    participant_rows = _participant_deltas(pairs)
    complete_pairs = [row for row in pairs if row["pair_complete"]]
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "execution_status": execution_status,
        "measured_task_count": len(tasks),
        "participant_count": len({row["participant_id"] for row in tasks}),
        "condition_distributions": {
            condition.value: _condition_summary(tasks, condition)
            for condition in (Condition.B0, Condition.P2)
        },
        "matched_pair_row_count": len(pairs),
        "complete_matched_pair_count": len(complete_pairs),
        "paired_delta_distributions": {
            field: _distribution(row[field] for row in complete_pairs)
            for field in (
                "correctness_delta_p2_minus_b0",
                "decision_time_delta_p2_minus_b0",
                "total_action_delta_p2_minus_b0",
                "interaction_delta_p2_minus_b0",
                "end_to_end_delta_p2_minus_b0",
            )
        },
        "participant_level_paired_deltas": participant_rows,
        "analysis_unit": "participant",
        "cluster_id": "participant_id",
        "formal_inference": {
            "status": "NOT_COMPUTED",
            "reason": (
                "Descriptive derivation does not test the two co-primary "
                "hypotheses or emit significance claims."
            ),
        },
        "selection_scoring_version": FINAL_SELECTION_SCORING_VERSION,
    }


def derive_study_metrics(
    events: Iterable[StudyEvent | Mapping[str, Any]],
    task_set: TaskSet | Mapping[str, Any],
    assignment_manifest: AssignmentManifest | Mapping[str, Any] | None = None,
    *,
    execution_status: str = "DRY_RUN",
) -> dict[str, Any]:
    """Convenience wrapper returning all three serializable metric layers."""

    task_rows = derive_task_outcomes(
        events,
        task_set,
        assignment_manifest,
        include_warmups=False,
    )
    pair_rows = derive_matched_pair_outcomes(task_rows)
    return {
        "task_outcomes": task_rows,
        "matched_pair_outcomes": pair_rows,
        "summary": summarize_outcomes(
            task_rows,
            pair_rows,
            execution_status=execution_status,
        ),
    }


__all__ = [
    "MATCHED_PAIR_OUTCOME_SCHEMA_VERSION",
    "SUMMARY_SCHEMA_VERSION",
    "TASK_OUTCOME_SCHEMA_VERSION",
    "derive_matched_pair_outcomes",
    "derive_study_metrics",
    "derive_task_outcomes",
    "summarize_outcomes",
]
