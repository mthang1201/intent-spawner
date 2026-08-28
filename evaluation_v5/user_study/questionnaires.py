"""Versioned, content-free questionnaire contracts for Protocol-v5 E3.

The module deliberately permits only numeric closed responses and the frozen
three-value final preference.  It never accepts comments or other free text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any
import uuid

from .schemas import (
    STUDY_TIMING_CONTRACT,
    STUDY_TIMING_CONTRACT_SHA256,
    STUDY_TIMING_CONTRACT_VERSION,
)


QUESTIONNAIRE_SCHEMA_VERSION = "protocol-v5-user-study-questionnaire-v1.1.0"
QUESTIONNAIRE_INSTRUMENT_VERSION = (
    "protocol-v5-user-study-questionnaire-instrument-v1.1.0"
)
ANALYSIS_PLAN_VERSION = "protocol-v5-user-study-analysis-plan-v1.2.0"
QUESTIONNAIRE_OUTCOME_SCHEMA_VERSION = (
    "protocol-v5-user-study-questionnaire-outcome-v1.0.0"
)

SEQ_ITEM_ID = "seq_ease"
SUS_ITEM_IDS = tuple(f"sus_{index:02d}" for index in range(1, 11))
CUSTOM_ITEM_IDS = (
    "custom_confidence_selected_environment",
    "custom_natural_workload_expression",
    "custom_selection_convenience",
)
FINAL_PREFERENCE_ID = "final_preference"
FINAL_PREFERENCES = frozenset({"B0", "P2", "NO_PREFERENCE"})
SEQ_ANCHORS = {"1": "Very difficult", "7": "Very easy"}
SUS_ANCHORS = {"1": "Strongly disagree", "5": "Strongly agree"}
CUSTOM_ANCHORS = {"1": "Strongly disagree", "7": "Strongly agree"}

SUS_ITEMS = (
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
CUSTOM_ITEMS = {
    CUSTOM_ITEM_IDS[0]: "I felt confident that I selected appropriate environments for the tasks in this condition.",
    CUSTOM_ITEM_IDS[1]: "This selection method allowed me to express my workload needs naturally.",
    CUSTOM_ITEM_IDS[2]: "Selecting an environment with this method was convenient.",
}

ANALYSIS_PLAN = {
    "version": ANALYSIS_PLAN_VERSION,
    "co_primary_outcomes": ["selection_success", "decision_time_seconds"],
    "family_alpha": 0.05,
    "multiplicity": "holm_two_sided",
    "sampling_structure": {
        "participant": "cluster_or_random_intercept",
        "task_pair": "fixed_repeated_factor",
        "counterbalance_cell": "coverage_diagnostic_only_redundant_with_frozen_design_terms",
    },
    "fixed_effects": [
        "condition",
        "pair_id",
        "variant_slot",
        "period",
        "condition_order",
    ],
    "selection_success": {
        "model": "participant_clustered_binomial_gee_exchangeable",
        "effects": ["marginal_risk_difference", "odds_ratio"],
        "confidence_interval": "participant_cluster_refit_percentile_bootstrap",
        "fallback": "participant_paired_risk_difference",
    },
    "decision_time_seconds": {
        "estimand": (
            "conditional_on_matched_task_pairs_with_valid_positive_confirmation_times_in_both_B0_and_P2"
        ),
        "eligibility": "confirmed_complete_matched_tasks_with_positive_times",
        "assigned_trial_accounting": (
            "all_assigned_measured_trials_remain_in_flow_and_missingness_denominators"
        ),
        "nonconfirmation_policy": (
            "outcome_unavailable_not_participant_or_task_exclusion"
        ),
        "model": "log_time_participant_random_intercept_mixedlm",
        "effects": ["geometric_mean_ratio", "percent_change", "raw_paired_difference"],
        "fallback": "participant_paired_robust_raw_scale",
        "zero_policy": "retain_and_fallback_without_offset",
        "nonconfirmation_sensitivity": {
            "estimand": "timeout_bound_decision_completion_composite",
            "bound_contract_field": (
                "decision_time_nonconfirmation_bound_seconds"
            ),
            "method": "participant_paired_robust_raw_scale",
            "primary_holm_family": False,
        },
    },
    "decision_time_nonconfirmation_bound": {
        "seconds": STUDY_TIMING_CONTRACT[
            "decision_time_nonconfirmation_bound_seconds"
        ],
        "semantics": STUDY_TIMING_CONTRACT[
            "decision_time_nonconfirmation_bound_semantics"
        ],
        "timing_contract_version": STUDY_TIMING_CONTRACT_VERSION,
        "timing_contract_sha256": STUDY_TIMING_CONTRACT_SHA256,
        "source": "frozen_server_enforced_study_task_timing_contract",
        "tuned_from_participant_results": False,
    },
    "interaction_and_correction_counts": {
        "model": "participant_clustered_quasipoisson_gee_exchangeable",
        "dispersion": "pearson_scale_estimated_from_fit",
        "effect": "incidence_rate_ratio",
        "fallback": "participant_paired_count_difference",
    },
    "notebook_ready_time": {
        "primary_clock": "notebook_ready_minus_confirmed_environment",
        "separate_end_to_end_clock": "notebook_ready_minus_task_shown",
        "model": "decision_time_log_or_robust_equivalent",
        "report_confirmation_and_readiness_missingness": True,
    },
    "seq": {
        "model": "participant_random_intercept_mixedlm_pair_fixed",
        "fallback": "participant_paired_scale_difference",
    },
    "sus_and_custom": {
        "model": "participant_paired_difference",
        "standardized_effect": "cohens_dz_mean_difference_divided_by_sd_difference",
        "confidence_interval": "participant_percentile_bootstrap",
    },
    "fallback_hierarchy": {
        "modeled_to_fallback_triggers": [
            "insufficient_eligible_participants_or_pairs",
            "outcome_nonvariation_or_nonidentifiability",
            "nonpositive_value_for_log_model",
            "convergence_failure",
            "singular_random_effect_or_hessian",
            "nonfinite_inference",
        ],
        "forbidden_triggers": [
            "p_value",
            "statistical_significance",
            "effect_direction",
            "effect_magnitude",
            "confidence_interval_width",
        ],
    },
    "preference": {
        "categories": ["B0", "P2", "NO_PREFERENCE"],
        "interval": "bonferroni_adjusted_wilson_simultaneous_95",
        "denominator": "answered_responses",
    },
    "bootstrap_replicates": 2000,
    "bootstrap_seed": 20260827,
    "bootstrap_rng": "numpy.random.PCG64",
    "bootstrap_resampling_unit": "participant_all_rows_together",
    "bootstrap_ci": "percentile_equal_tailed_95",
    "bootstrap_minimum_success_fraction": 0.95,
    "bootstrap_failure_policy": (
        "discard_failed_refit_and_mark_ci_unavailable_below_threshold"
    ),
    "sus_missing_policy": "strict_complete_case",
    "other_missing_policy": "no_imputation",
    "trimming_policy": "none",
    "seq_scale": [1, 7],
    "custom_scale": [1, 7],
    "preference_values": sorted(FINAL_PREFERENCES),
    "exclusion_reason_version": "protocol-v5-user-study-exclusion-reasons-v1.0.0",
}
QUESTIONNAIRE_INSTRUMENT = {
    "instrument_version": QUESTIONNAIRE_INSTRUMENT_VERSION,
    "seq_prompt": "Overall, how difficult or easy was this task?",
    "seq_anchors": SEQ_ANCHORS,
    "sus_items": list(SUS_ITEMS),
    "sus_anchors": SUS_ANCHORS,
    "custom_items": CUSTOM_ITEMS,
    "custom_anchors": CUSTOM_ANCHORS,
    "preference_prompt": "Overall, which method would you prefer to use to select a notebook environment?",
    "preference_labels": {
        "B0": "B0",
        "P2": "P2",
        "NO_PREFERENCE": "No preference",
    },
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


ANALYSIS_PLAN_SHA256 = hashlib.sha256(_canonical(ANALYSIS_PLAN)).hexdigest()
# SHA-256 of benchmarks_v5/protocol-v5-user-study-questionnaire-v1.schema.json.
# It is literal because the study-Hub runtime intentionally does not mount the
# authoritative benchmark directory.
QUESTIONNAIRE_SCHEMA_SHA256 = (
    "7c2f5950644f16a26f24bec5939646dd4c4a21d6fd1723797c70b29748dbea56"
)
QUESTIONNAIRE_INSTRUMENT_SHA256 = hashlib.sha256(
    _canonical(QUESTIONNAIRE_INSTRUMENT)
).hexdigest()

_PSEUDONYM = re.compile(r"^P-[0-9a-f]{12}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_FIELDS = frozenset(
    {
        "schema_version",
        "instrument_version",
        "study_id",
        "assignment_id",
        "session_id",
        "participant_id",
        "response_uuid",
        "questionnaire_type",
        "questionnaire_id",
        "condition",
        "period",
        "trial_id",
        "task_id",
        "pair_id",
        "responses",
        "submitted_at_utc",
    }
)


class QuestionnaireValidationError(ValueError):
    """A questionnaire record violates the frozen contract."""


class QuestionnaireType(str, Enum):
    SEQ_TASK = "seq_task"
    POST_CONDITION = "post_condition"
    FINAL_PREFERENCE = "final_preference"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise QuestionnaireValidationError(message)


def _utc(value: object) -> str:
    _require(isinstance(value, str) and value.endswith("Z"), "submitted_at_utc must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise QuestionnaireValidationError("submitted_at_utc is invalid") from exc
    _require(parsed.utcoffset() is not None, "submitted_at_utc must be timezone-aware")
    return value


def _scale(value: object, low: int, high: int, label: str) -> int | None:
    if value is None:
        return None
    _require(
        isinstance(value, int) and not isinstance(value, bool) and low <= value <= high,
        f"{label} must be null or an integer in {low}..{high}",
    )
    return value


@dataclass(frozen=True, slots=True)
class QuestionnaireRecord:
    schema_version: str
    instrument_version: str
    study_id: str
    assignment_id: str
    session_id: str
    participant_id: str
    response_uuid: str
    questionnaire_type: QuestionnaireType
    questionnaire_id: str
    condition: str | None
    period: int | None
    trial_id: str | None
    task_id: str | None
    pair_id: str | None
    responses: Mapping[str, int | str | None]
    submitted_at_utc: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instrument_version": self.instrument_version,
            "study_id": self.study_id,
            "assignment_id": self.assignment_id,
            "session_id": self.session_id,
            "participant_id": self.participant_id,
            "response_uuid": self.response_uuid,
            "questionnaire_type": self.questionnaire_type.value,
            "questionnaire_id": self.questionnaire_id,
            "condition": self.condition,
            "period": self.period,
            "trial_id": self.trial_id,
            "task_id": self.task_id,
            "pair_id": self.pair_id,
            "responses": dict(self.responses),
            "submitted_at_utc": self.submitted_at_utc,
        }


def validate_questionnaire_record(value: object) -> QuestionnaireRecord:
    _require(isinstance(value, Mapping), "questionnaire record must be an object")
    _require(set(value) == _FIELDS, "questionnaire record fields differ from the schema")
    _require(value["schema_version"] == QUESTIONNAIRE_SCHEMA_VERSION, "unsupported questionnaire schema")
    _require(value["instrument_version"] == QUESTIONNAIRE_INSTRUMENT_VERSION, "unsupported questionnaire instrument")
    for field in ("study_id", "assignment_id", "session_id", "questionnaire_id"):
        _require(isinstance(value[field], str) and bool(_SAFE_ID.fullmatch(value[field])), f"{field} is invalid")
    _require(isinstance(value["participant_id"], str) and bool(_PSEUDONYM.fullmatch(value["participant_id"])), "participant_id must be an issued pseudonym")
    try:
        parsed_uuid = uuid.UUID(str(value["response_uuid"]))
    except (AttributeError, TypeError, ValueError) as exc:
        raise QuestionnaireValidationError("response_uuid must be a canonical UUID") from exc
    _require(str(parsed_uuid) == str(value["response_uuid"]).lower(), "response_uuid must use canonical notation")
    try:
        kind = QuestionnaireType(value["questionnaire_type"])
    except (TypeError, ValueError) as exc:
        raise QuestionnaireValidationError("questionnaire_type is unsupported") from exc
    _require(isinstance(value["responses"], Mapping), "responses must be an object")
    raw_responses = dict(value["responses"])
    if kind is QuestionnaireType.SEQ_TASK:
        _require(set(raw_responses) == {SEQ_ITEM_ID}, "SEQ response fields are invalid")
        responses: dict[str, int | str | None] = {
            SEQ_ITEM_ID: _scale(raw_responses[SEQ_ITEM_ID], 1, 7, SEQ_ITEM_ID)
        }
        _require(value["condition"] in {"B0", "P2"}, "SEQ condition is invalid")
        _require(value["period"] in {1, 2}, "SEQ period is invalid")
        _require(all(isinstance(value[field], str) for field in ("trial_id", "task_id", "pair_id")), "SEQ scope is incomplete")
    elif kind is QuestionnaireType.POST_CONDITION:
        expected = set(SUS_ITEM_IDS) | set(CUSTOM_ITEM_IDS)
        _require(set(raw_responses) == expected, "post-condition response fields are invalid")
        responses = {
            item: _scale(raw_responses[item], 1, 5, item) for item in SUS_ITEM_IDS
        }
        responses.update(
            {item: _scale(raw_responses[item], 1, 7, item) for item in CUSTOM_ITEM_IDS}
        )
        _require(value["condition"] in {"B0", "P2"}, "post-condition condition is invalid")
        _require(value["period"] in {1, 2}, "post-condition period is invalid")
        _require(all(value[field] is None for field in ("trial_id", "task_id", "pair_id")), "post-condition record cannot carry task scope")
    else:
        _require(set(raw_responses) == {FINAL_PREFERENCE_ID}, "preference response fields are invalid")
        preference = raw_responses[FINAL_PREFERENCE_ID]
        _require(preference is None or preference in FINAL_PREFERENCES, "final preference is unsupported")
        responses = {FINAL_PREFERENCE_ID: preference}
        _require(value["condition"] is None and value["period"] is None, "final preference cannot carry condition scope")
        _require(all(value[field] is None for field in ("trial_id", "task_id", "pair_id")), "final preference cannot carry task scope")
    return QuestionnaireRecord(
        schema_version=str(value["schema_version"]),
        instrument_version=str(value["instrument_version"]),
        study_id=str(value["study_id"]),
        assignment_id=str(value["assignment_id"]),
        session_id=str(value["session_id"]),
        participant_id=str(value["participant_id"]),
        response_uuid=str(value["response_uuid"]),
        questionnaire_type=kind,
        questionnaire_id=str(value["questionnaire_id"]),
        condition=value["condition"],
        period=value["period"],
        trial_id=value["trial_id"],
        task_id=value["task_id"],
        pair_id=value["pair_id"],
        responses=responses,
        submitted_at_utc=_utc(value["submitted_at_utc"]),
    )


def expected_questionnaire_ids(participant: Any) -> set[str]:
    measured = [task for task in participant.task_sequence if task.phase.value == "measured"]
    return {
        *(f"seq:{task.trial_id}" for task in measured),
        *(
            f"post_condition:{period}"
            for period, _ in enumerate(participant.condition_order, start=1)
        ),
        "final_preference",
    }


def validate_questionnaire_stream(
    values: Sequence[object], assignment_manifest: Any
) -> tuple[QuestionnaireRecord, ...]:
    participants = {item.participant_id: item for item in assignment_manifest.assignments}
    seen_uuid: set[str] = set()
    seen_id: set[tuple[str, str]] = set()
    parsed: list[QuestionnaireRecord] = []
    for value in values:
        record = validate_questionnaire_record(value)
        _require(record.study_id == assignment_manifest.study_id, "questionnaire study_id drift")
        _require(record.assignment_id == assignment_manifest.assignment_id, "questionnaire assignment_id drift")
        participant = participants.get(record.participant_id)
        _require(participant is not None, "questionnaire participant is absent from assignment")
        _require(record.session_id == participant.session_id, "questionnaire session differs from assignment")
        _require(record.response_uuid not in seen_uuid, "duplicate questionnaire response_uuid")
        key = (record.session_id, record.questionnaire_id)
        _require(key not in seen_id, "duplicate scheduled questionnaire")
        seen_uuid.add(record.response_uuid)
        seen_id.add(key)
        if record.questionnaire_type is QuestionnaireType.SEQ_TASK:
            task = next((item for item in participant.task_sequence if item.trial_id == record.trial_id), None)
            _require(task is not None and task.phase.value == "measured", "SEQ trial is not an assigned measured task")
            _require(record.questionnaire_id == f"seq:{task.trial_id}", "SEQ questionnaire_id is invalid")
            _require((record.task_id, record.pair_id, record.condition, record.period) == (task.task_id, task.pair_id, task.condition.value, task.period), "SEQ scope differs from assignment")
        elif record.questionnaire_type is QuestionnaireType.POST_CONDITION:
            _require(record.questionnaire_id == f"post_condition:{record.period}", "post-condition questionnaire_id is invalid")
            _require(record.condition == participant.condition_order[record.period - 1].value, "post-condition scope differs from assignment")
        else:
            _require(record.questionnaire_id == "final_preference", "final preference questionnaire_id is invalid")
        parsed.append(record)
    return tuple(parsed)


def score_sus(responses: Mapping[str, int | str | None]) -> float | None:
    """Return the standard 0..100 SUS score, or null for any missing item."""

    values = [responses.get(item) for item in SUS_ITEM_IDS]
    if any(value is None for value in values):
        return None
    _require(all(isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5 for value in values), "SUS responses must be integers in 1..5")
    contribution = 0
    for index, value in enumerate(values, start=1):
        assert isinstance(value, int)
        contribution += value - 1 if index % 2 else 5 - value
    score = contribution * 2.5
    _require(math.isfinite(score) and 0 <= score <= 100, "SUS score is invalid")
    return score


def derive_questionnaire_outcomes(
    records: Sequence[QuestionnaireRecord],
) -> list[dict[str, Any]]:
    """Flatten validated closed responses without timestamps or response UUIDs."""

    outcomes: list[dict[str, Any]] = []
    for record in records:
        row: dict[str, Any] = {
            "schema_version": QUESTIONNAIRE_OUTCOME_SCHEMA_VERSION,
            "study_id": record.study_id,
            "assignment_id": record.assignment_id,
            "session_id": record.session_id,
            "participant_id": record.participant_id,
            "questionnaire_type": record.questionnaire_type.value,
            "questionnaire_id": record.questionnaire_id,
            "condition": record.condition,
            "period": record.period,
            "trial_id": record.trial_id,
            "task_id": record.task_id,
            "pair_id": record.pair_id,
            "answered_item_count": sum(
                value is not None for value in record.responses.values()
            ),
            "missing_item_count": sum(
                value is None for value in record.responses.values()
            ),
        }
        if record.questionnaire_type is QuestionnaireType.SEQ_TASK:
            row[SEQ_ITEM_ID] = record.responses[SEQ_ITEM_ID]
        elif record.questionnaire_type is QuestionnaireType.POST_CONDITION:
            row["sus_score"] = score_sus(record.responses)
            row["sus_complete"] = row["sus_score"] is not None
            row["sus_answered_item_count"] = sum(
                record.responses[item] is not None for item in SUS_ITEM_IDS
            )
            for item in CUSTOM_ITEM_IDS:
                row[item] = record.responses[item]
        else:
            row[FINAL_PREFERENCE_ID] = record.responses[FINAL_PREFERENCE_ID]
        outcomes.append(row)
    outcomes.sort(
        key=lambda row: (
            str(row["participant_id"]),
            str(row["questionnaire_type"]),
            str(row["questionnaire_id"]),
        )
    )
    return outcomes


__all__ = [
    "ANALYSIS_PLAN",
    "ANALYSIS_PLAN_SHA256",
    "ANALYSIS_PLAN_VERSION",
    "CUSTOM_ITEMS",
    "CUSTOM_ANCHORS",
    "CUSTOM_ITEM_IDS",
    "FINAL_PREFERENCES",
    "FINAL_PREFERENCE_ID",
    "QUESTIONNAIRE_INSTRUMENT_VERSION",
    "QUESTIONNAIRE_INSTRUMENT_SHA256",
    "QUESTIONNAIRE_OUTCOME_SCHEMA_VERSION",
    "QUESTIONNAIRE_SCHEMA_VERSION",
    "QUESTIONNAIRE_SCHEMA_SHA256",
    "QuestionnaireRecord",
    "QuestionnaireType",
    "QuestionnaireValidationError",
    "SEQ_ITEM_ID",
    "SEQ_ANCHORS",
    "SUS_ITEMS",
    "SUS_ANCHORS",
    "SUS_ITEM_IDS",
    "expected_questionnaire_ids",
    "derive_questionnaire_outcomes",
    "score_sus",
    "validate_questionnaire_record",
    "validate_questionnaire_stream",
]
