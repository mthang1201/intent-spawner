"""Fail-closed validator for Protocol-v5 offline recommendation evidence."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime
import json
import math
from pathlib import Path
import re
from typing import Any

from evaluation_v4.dataset import file_sha256
from evaluation_v5.split_dataset import LoadedSplit, load_development_split
from recommender.candidate_corpus import load_candidate_corpus

from .recommenders import OfflineAdapterResult, candidate_catalog_snapshot
from .runner import (
    COMPLETION_FILENAME,
    LOCK_FILENAME,
    OFFLINE_COMPLETION_SCHEMA_VERSION,
    OFFLINE_PROVENANCE_SCHEMA_VERSION,
    PROVENANCE_FILENAME,
    RAW_DIRECTORY_NAME,
    RECORDS_FILENAME,
    REPEAT_POLICY_VERSION,
    REPORT_DIRECTORY_NAME,
    EvidenceRecordError,
    MatrixEntry,
    _entry_seed,
    _freeze_identity,
    _input_identity,
    _metric_inputs,
    _record_key,
    _sha256,
    provenance_fingerprint,
    validate_raw_record,
)


VALIDATION_SCHEMA_VERSION = "protocol-v5-offline-evidence-validation-v1.0.0"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
_PROVENANCE_FIELDS = frozenset(
    {
        "schema_version",
        "protocol_version",
        "experiment_id",
        "run_id",
        "split",
        "freeze_identity",
        "git_revision",
        "git_worktree_dirty",
        "systems",
        "system_frozen_provenance",
        "candidate_catalog",
        "frozen_configuration",
        "seed",
        "requested_repeats",
        "effective_repeats",
        "repeat_policy",
        "p3_explicitly_enabled",
        "planned_record_count",
        "benchmark_prompt_policy",
        "provenance_fingerprint",
        "created_utc",
        "environment_identity",
    }
)
_COMPLETION_FIELDS = frozenset(
    {
        "schema_version",
        "provenance_fingerprint",
        "completed_utc",
        "records",
        "error_records",
        "recommendations_jsonl_sha256",
        "claims_permitted",
        "status",
    }
)
_METRIC_INPUT_FIELDS = frozenset(
    {
        "request_feasible",
        "preferred_candidate_id",
        "acceptable_candidate_ids",
        "acceptable_profile_ids",
        "acceptable_image_ids",
        "required_image_capabilities",
        "predicted_candidate_id",
        "predicted_profile_id",
        "predicted_image_id",
        "candidate_top_k_ids",
        "final_ranking_candidate_ids",
        "hard_constraints_satisfied",
        "constraint_violation_codes",
        "infeasible_request_signal",
        "unsupported_request_signal",
        "fallback_used",
        "fallback_category",
    }
)
_P2_PROVENANCE_FIELDS = frozenset(
    {
        "backend_name",
        "backend_version",
        "pipeline_version",
        "structured_intent_schema_version",
        "extractor_name",
        "extractor_version",
        "extractor_model_id",
        "extractor_prompt_version",
        "extractor_prompt_sha256",
        "embedding_model_id",
        "embedding_model_revision",
        "dense_index_version",
        "dense_index_sha256",
        "sparse_index_version",
        "sparse_index_sha256",
        "hybrid_index_version",
        "hybrid_index_sha256",
        "retrieval_configuration",
        "constraint_ranking_configuration",
        "config",
        "generation",
        "candidate_catalog",
    }
)


class OfflineEvidenceValidationError(RuntimeError):
    """A result directory is incomplete, inconsistent, or untrusted."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise OfflineEvidenceValidationError(message)


def _exact_mapping(
    value: object, fields: frozenset[str], label: str
) -> dict[str, Any]:
    _require(isinstance(value, Mapping), f"{label} must be an object")
    payload = dict(value)
    missing = sorted(fields - set(payload))
    extra = sorted(set(payload) - fields)
    _require(not missing, f"{label} missing fields: {', '.join(missing)}")
    _require(not extra, f"{label} unexpected fields: {', '.join(extra)}")
    return payload


def _timestamp(value: object, label: str) -> None:
    _require(
        isinstance(value, str) and value.endswith("Z"), f"{label} must be UTC"
    )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OfflineEvidenceValidationError(f"{label} is invalid") from exc
    _require(
        parsed.utcoffset() is not None
        and parsed.utcoffset().total_seconds() == 0,
        f"{label} must use UTC",
    )


def _strict_json_loads(text: str, label: str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise OfflineEvidenceValidationError(
                    f"{label} has duplicate field {key!r}"
                )
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise OfflineEvidenceValidationError(
            f"{label} has non-finite number {value}"
        )

    try:
        return json.loads(
            text, object_pairs_hook=pairs, parse_constant=constant
        )
    except OfflineEvidenceValidationError:
        raise
    except json.JSONDecodeError as exc:
        raise OfflineEvidenceValidationError(
            f"{label} is malformed JSON"
        ) from exc


def _read_json(path: Path, label: str) -> dict[str, Any]:
    _require(path.is_file(), f"{label} is missing")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OfflineEvidenceValidationError(f"{label} is unreadable") from exc
    value = _strict_json_loads(text, label)
    _require(isinstance(value, Mapping), f"{label} must contain an object")
    return dict(value)


def _validate_p2_provenance(value: object, label: str) -> None:
    _require(isinstance(value, Mapping), f"{label} must be an object")
    payload = dict(value)
    missing = sorted(_P2_PROVENANCE_FIELDS - set(payload))
    _require(not missing, f"{label} missing fields: {', '.join(missing)}")
    for field in (
        "backend_name",
        "backend_version",
        "pipeline_version",
        "structured_intent_schema_version",
        "extractor_name",
        "extractor_version",
        "extractor_model_id",
        "extractor_prompt_version",
        "embedding_model_id",
        "embedding_model_revision",
        "dense_index_version",
        "sparse_index_version",
        "hybrid_index_version",
    ):
        _require(
            isinstance(payload[field], str) and bool(payload[field]),
            f"{label} {field} must be non-blank",
        )
    for field in (
        "extractor_prompt_sha256",
        "dense_index_sha256",
        "sparse_index_sha256",
        "hybrid_index_sha256",
    ):
        _require(
            isinstance(payload[field], str)
            and bool(_SHA256.fullmatch(payload[field])),
            f"{label} {field} must be a SHA-256",
        )
    for field in (
        "retrieval_configuration",
        "constraint_ranking_configuration",
        "config",
        "generation",
        "candidate_catalog",
    ):
        _require(
            isinstance(payload[field], Mapping),
            f"{label} {field} must be an object",
        )


def _validate_provenance(
    value: Mapping[str, Any],
    *,
    split: LoadedSplit,
    freeze_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    provenance = _exact_mapping(
        value, _PROVENANCE_FIELDS, "offline provenance"
    )
    _require(
        provenance["schema_version"] == OFFLINE_PROVENANCE_SCHEMA_VERSION,
        "offline provenance schema is unsupported",
    )
    _require(
        provenance["protocol_version"] == "5.0.0",
        "offline provenance protocol version is unsupported",
    )
    _require(
        provenance["experiment_id"] in {"E1", "E6"},
        "offline provenance experiment ID is unsupported",
    )
    _require(
        isinstance(provenance["run_id"], str) and bool(provenance["run_id"]),
        "offline provenance run_id is invalid",
    )
    _require(
        isinstance(provenance["git_revision"], str)
        and bool(_GIT_REVISION.fullmatch(provenance["git_revision"])),
        "offline provenance requires a full Git revision",
    )
    _require(
        isinstance(provenance["git_worktree_dirty"], bool),
        "git_worktree_dirty must be boolean",
    )
    _timestamp(provenance["created_utc"], "offline provenance created_utc")
    environment = _exact_mapping(
        provenance["environment_identity"],
        frozenset({"python_version", "platform", "platform_release"}),
        "environment_identity",
    )
    _require(
        all(isinstance(item, str) and bool(item) for item in environment.values()),
        "environment_identity values must be non-blank",
    )

    fingerprint = provenance["provenance_fingerprint"]
    _require(
        isinstance(fingerprint, str) and bool(_SHA256.fullmatch(fingerprint)),
        "offline provenance fingerprint is invalid",
    )
    _require(
        provenance_fingerprint(provenance) == fingerprint,
        "offline provenance fingerprint does not match its execution plan",
    )

    split_identity = _exact_mapping(
        provenance["split"],
        frozenset(
            {
                "dataset_id",
                "split_id",
                "role",
                "bundle_checksum",
                "dataset_sha256",
                "case_count",
                "family_count",
            }
        ),
        "offline provenance split",
    )
    expected_split = {
        "dataset_id": split.manifest.dataset_id,
        "split_id": split.manifest.split_id,
        "role": split.manifest.role.value,
        "bundle_checksum": split.manifest.checksum,
        "dataset_sha256": split.source_file_sha256,
        "case_count": split.manifest.case_count,
        "family_count": split.manifest.family_count,
    }
    _require(
        split_identity == expected_split,
        "offline provenance does not match the supplied frozen dataset/split",
    )
    expected_freeze = dict(freeze_identity or _freeze_identity(split))
    _require(
        provenance["freeze_identity"] == expected_freeze,
        "offline provenance freeze identity does not match",
    )

    systems = provenance["systems"]
    _require(
        isinstance(systems, list) and bool(systems),
        "offline provenance systems must be a non-empty list",
    )
    _require(
        all(system in {"P1", "P2", "P3"} for system in systems),
        "offline provenance contains an unsupported system",
    )
    _require(
        len(systems) == len(set(systems)),
        "offline provenance systems contain duplicates",
    )
    _require("B0" not in systems, "B0 cannot appear in offline evidence")
    p3_enabled = provenance["p3_explicitly_enabled"]
    _require(
        isinstance(p3_enabled, bool),
        "p3_explicitly_enabled must be boolean",
    )
    _require(
        ("P3" not in systems) or p3_enabled,
        "P3 evidence lacks explicit enablement",
    )
    _require(
        provenance["experiment_id"] == ("E6" if "P3" in systems else "E1"),
        "experiment ID does not match participating systems",
    )

    system_provenance = provenance["system_frozen_provenance"]
    _require(
        isinstance(system_provenance, Mapping)
        and set(system_provenance) == set(systems),
        "system frozen provenance does not match selected systems",
    )
    catalog = provenance["candidate_catalog"]
    _require(
        isinstance(catalog, Mapping), "candidate_catalog must be an object"
    )
    expected_catalog = candidate_catalog_snapshot(load_candidate_corpus())
    _require(
        catalog == expected_catalog,
        "candidate catalog/corpus identity does not match the frozen repository catalog",
    )
    for system in systems:
        selected = system_provenance[system]
        _require(
            isinstance(selected, Mapping),
            f"{system} provenance must be an object",
        )
        _require(
            selected.get("candidate_catalog") == catalog,
            f"{system} provenance uses a different candidate catalog",
        )
        _require(
            isinstance(selected.get("adapter_version"), str)
            and bool(selected["adapter_version"]),
            f"{system} provenance lacks an adapter version",
        )
        if system == "P1":
            for field in ("backend_name", "backend_version", "catalog_version"):
                _require(
                    isinstance(selected.get(field), str) and bool(selected[field]),
                    f"P1 provenance lacks {field}",
                )
        elif system == "P2":
            _validate_p2_provenance(selected, "P2 frozen provenance")
        elif system == "P3":
            frozen_p2 = selected.get("frozen_p2_provenance")
            _validate_p2_provenance(frozen_p2, "P3 frozen P2 provenance")
            assert isinstance(frozen_p2, Mapping)
            _require(
                frozen_p2.get("candidate_catalog") == catalog,
                "P3 frozen P2 provenance uses a different candidate catalog",
            )
            _require(
                isinstance(selected.get("reranker_name"), str)
                and bool(selected["reranker_name"])
                and isinstance(selected.get("reranker_version"), str)
                and bool(selected["reranker_version"]),
                "P3 provenance lacks the reranker identity",
            )

    requested = provenance["requested_repeats"]
    _require(
        isinstance(requested, int)
        and not isinstance(requested, bool)
        and requested >= 1,
        "requested_repeats must be positive",
    )
    effective = provenance["effective_repeats"]
    _require(
        isinstance(effective, Mapping) and set(effective) == set(systems),
        "effective_repeats must cover every selected system",
    )
    _require(
        all(
            isinstance(item, int)
            and not isinstance(item, bool)
            and item >= 1
            for item in effective.values()
        ),
        "effective repeats must be positive integers",
    )
    policy = _exact_mapping(
        provenance["repeat_policy"],
        frozenset(
            {
                "version",
                "deterministic_systems",
                "stochastic_systems",
                "deterministic_effective_repeats",
                "requested_repeats_apply_only_to_stochastic_systems",
            }
        ),
        "repeat_policy",
    )
    _require(
        policy["version"] == REPEAT_POLICY_VERSION,
        "repeat policy version is unsupported",
    )
    deterministic = policy["deterministic_systems"]
    stochastic = policy["stochastic_systems"]
    _require(
        isinstance(deterministic, list) and isinstance(stochastic, list),
        "repeat policy system groups must be lists",
    )
    _require(
        set(deterministic).isdisjoint(stochastic)
        and set(deterministic) | set(stochastic) == set(systems),
        "repeat policy system groups must partition selected systems",
    )
    _require(
        policy["deterministic_effective_repeats"] == 1,
        "deterministic repeat policy must collapse to one",
    )
    _require(
        policy["requested_repeats_apply_only_to_stochastic_systems"] is True,
        "repeat policy must restrict repeats to stochastic systems",
    )
    _require(
        all(effective[system] == 1 for system in deterministic),
        "deterministic system effective repeats must equal one",
    )
    _require(
        all(effective[system] == requested for system in stochastic),
        "stochastic system effective repeats must equal requested repeats",
    )
    _require("P1" not in stochastic, "P1 cannot be stochastic")
    if "P2" in stochastic:
        p2_config = system_provenance["P2"].get("config", {})
        _require(
            isinstance(p2_config, Mapping)
            and p2_config.get("extractor_mode") == "llm",
            "only provider-dependent P2 extraction may be repeated",
        )

    prompt_policy = _exact_mapping(
        provenance["benchmark_prompt_policy"],
        frozenset({"stored_in_raw_evidence", "operational_logging"}),
        "benchmark_prompt_policy",
    )
    _require(
        isinstance(prompt_policy["stored_in_raw_evidence"], bool),
        "prompt storage policy must be boolean",
    )
    _require(
        prompt_policy["operational_logging"] == "prohibited",
        "operational benchmark prompt logging must be prohibited",
    )
    _require(
        isinstance(provenance["seed"], int)
        and not isinstance(provenance["seed"], bool),
        "seed must be an integer",
    )
    expected_count = len(split.bundle.cases) * sum(effective.values())
    _require(
        provenance["planned_record_count"] == expected_count,
        "planned record count does not match the execution matrix",
    )
    _require(
        isinstance(provenance["frozen_configuration"], Mapping),
        "frozen_configuration must be an object",
    )
    return provenance


def _read_records(path: Path) -> list[dict[str, Any]]:
    _require(path.is_file(), "raw recommendations.jsonl is missing")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise OfflineEvidenceValidationError(
            "raw recommendations.jsonl is unreadable"
        ) from exc
    _require(bool(data), "raw recommendations.jsonl is empty")
    _require(
        data.endswith(b"\n"),
        "raw recommendations.jsonl has an unterminated final record",
    )
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(data.splitlines(), start=1):
        _require(
            bool(raw_line.strip()),
            f"raw recommendations.jsonl line {line_number} is blank",
        )
        try:
            value = _strict_json_loads(
                raw_line.decode("utf-8"), f"raw record line {line_number}"
            )
            record = validate_raw_record(value)
        except (UnicodeDecodeError, EvidenceRecordError) as exc:
            raise OfflineEvidenceValidationError(
                f"raw record line {line_number} is invalid: {exc}"
            ) from exc
        records.append(record)
    return records


def _expected_gold(case: Any) -> dict[str, Any]:
    return {
        "request_feasible": case.gold["request_feasible"],
        "preferred_candidate_id": case.gold["preferred_candidate_id"],
        "acceptable_candidate_ids": list(case.gold["acceptable_candidate_ids"]),
        "required_image_capabilities": list(
            case.gold["required_image_capabilities"]
        ),
        "allowed_profiles": list(case.gold["allowed_profiles"]),
        "gpu_allowed": case.gold["gpu_allowed"],
    }


def _adapter_result(record: Mapping[str, Any]) -> OfflineAdapterResult:
    return OfflineAdapterResult(
        predicted_candidate_id=record["predicted_candidate_id"],
        predicted_profile_id=record["predicted_profile_id"],
        predicted_image_id=record["predicted_image_id"],
        recommendation_reasons=tuple(record["recommendation_reasons"]),
        recommendation_codes=tuple(record["recommendation_codes"]),
        structured_intent=record["structured_intent"],
        sparse_ranks=tuple(record["sparse_ranks"]),
        dense_ranks=tuple(record["dense_ranks"]),
        hybrid_ranks_scores=tuple(record["hybrid_ranks_scores"]),
        candidate_top_k=tuple(record["candidate_top_k"]),
        constraint_evaluations=tuple(record["constraint_evaluations"]),
        feasible_top_k=tuple(record["feasible_top_k"]),
        final_ranking=tuple(record["final_ranking"]),
        constraint_summary=record["constraint_summary"],
        latency_components=record["latency_components"],
        fallback=record["fallback"],
        errors=record["errors"],
        backend_provenance=record["backend_provenance"],
    )


def _ranked_candidate_ids(record: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for field in (
        "sparse_ranks",
        "dense_ranks",
        "hybrid_ranks_scores",
        "candidate_top_k",
        "constraint_evaluations",
        "feasible_top_k",
        "final_ranking",
    ):
        for item in record[field]:
            _require(
                isinstance(item, Mapping),
                f"{field} entries must be objects",
            )
            candidate_id = item.get("candidate_id")
            _require(
                isinstance(candidate_id, str) and bool(candidate_id),
                f"{field} entry has invalid candidate_id",
            )
            result.append(candidate_id)
    return result


def _validate_rank_list(record: Mapping[str, Any], field: str) -> None:
    ranks: list[int] = []
    candidate_ids: list[str] = []
    for item in record[field]:
        rank = item.get("rank")
        _require(
            isinstance(rank, int)
            and not isinstance(rank, bool)
            and rank >= 1,
            f"{field} rank must be positive",
        )
        score = item.get("score")
        _require(
            isinstance(score, (int, float))
            and not isinstance(score, bool)
            and math.isfinite(float(score)),
            f"{field} score must be finite",
        )
        ranks.append(rank)
        candidate_ids.append(item["candidate_id"])
    _require(
        len(ranks) == len(set(ranks)), f"{field} ranks must be unique"
    )
    _require(
        len(candidate_ids) == len(set(candidate_ids)),
        f"{field} candidate IDs must be unique",
    )
    if ranks:
        _require(
            sorted(ranks) == list(range(1, len(ranks) + 1)),
            f"{field} ranks must be contiguous from one",
        )


def _validate_record(
    record: Mapping[str, Any],
    *,
    case: Any,
    split: LoadedSplit,
    provenance: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
) -> None:
    _require(
        record["run_id"] == provenance["run_id"],
        "raw record run_id does not match provenance",
    )
    _require(
        record["provenance_fingerprint"]
        == provenance["provenance_fingerprint"],
        "raw record provenance does not match result directory",
    )
    _require(
        record["input_identity"] == _input_identity(case, split),
        "raw record case/input checksum does not match the frozen dataset",
    )
    _require(
        record["family_id"] == case.family_id
        and record["variant_id"] == case.variant_id,
        "raw record family/variant does not match case_id",
    )
    _require(
        record["evaluation_gold"] == _expected_gold(case),
        "raw record labels do not match the frozen dataset",
    )
    _require(
        record["adapter_provenance"]
        == provenance["system_frozen_provenance"][record["system_id"]],
        "raw record adapter provenance does not match the frozen system",
    )
    prompt_stored = provenance["benchmark_prompt_policy"][
        "stored_in_raw_evidence"
    ]
    _require(
        record["benchmark_prompt"] == (case.prompt if prompt_stored else None),
        "raw record benchmark prompt violates storage policy",
    )

    provisional = MatrixEntry(
        case=case,
        system_id=record["system_id"],
        repeat_index=record["repeat_index"],
        seed=0,
    )
    expected_seed = _entry_seed(provenance["seed"], provisional)
    _require(
        record["seed"] == expected_seed,
        "raw record seed does not match its matrix identity",
    )
    expected_record_id = _sha256(
        {
            "provenance_fingerprint": provenance["provenance_fingerprint"],
            "key": list(provisional.key),
            "seed": expected_seed,
            "case_sha256": record["input_identity"]["case_sha256"],
        }
    )
    _require(
        record["record_id"] == expected_record_id,
        "raw record ID does not match its execution identity",
    )

    _require(
        set(record["metric_inputs"]) == _METRIC_INPUT_FIELDS,
        "raw record metric inputs are incomplete",
    )
    result = _adapter_result(record)
    _require(
        record["metric_inputs"] == _metric_inputs(case, result),
        "raw record metric inputs are inconsistent",
    )

    trusted_ids = set(candidates)
    predicted = record["predicted_candidate_id"]
    if predicted is not None:
        _require(
            predicted in trusted_ids,
            "raw record predicted an unknown candidate ID",
        )
        candidate = candidates[predicted]
        _require(
            record["predicted_profile_id"] == candidate["profile_id"],
            "predicted profile does not match the trusted candidate",
        )
        _require(
            record["predicted_image_id"] == candidate["image_id"],
            "predicted image does not match the trusted candidate",
        )
    _require(
        all(
            candidate_id in trusted_ids
            for candidate_id in _ranked_candidate_ids(record)
        ),
        "ranking trace contains an unknown candidate ID",
    )
    for field in (
        "sparse_ranks",
        "dense_ranks",
        "hybrid_ranks_scores",
        "candidate_top_k",
        "feasible_top_k",
        "final_ranking",
    ):
        _validate_rank_list(record, field)
    for evaluation in record["constraint_evaluations"]:
        _require(
            isinstance(evaluation.get("feasible"), bool),
            "constraint evaluation feasible must be boolean",
        )
        for field in (
            "violated_hard_constraints",
            "unsupported_constraints",
            "explanation_codes",
        ):
            values = evaluation.get(field)
            _require(
                isinstance(values, list)
                and all(isinstance(item, str) for item in values),
                f"constraint evaluation {field} must be a string list",
            )
    _require(
        record["candidate_top_k"] == record["hybrid_ranks_scores"],
        "candidate Top-K must preserve the hybrid ranking trace",
    )

    fallback = record["fallback"]
    _require(
        isinstance(fallback, Mapping)
        and isinstance(fallback.get("used"), bool)
        and "category" in fallback,
        "fallback evidence is incomplete",
    )
    latency = record["latency_components"]
    _require(
        isinstance(latency, Mapping) and "total_elapsed_seconds" in latency,
        "latency evidence is incomplete",
    )
    elapsed = latency["total_elapsed_seconds"]
    _require(
        isinstance(elapsed, (int, float))
        and not isinstance(elapsed, bool)
        and math.isfinite(float(elapsed))
        and elapsed >= 0,
        "total latency must be finite and non-negative",
    )

    if record["status"] == "error":
        _require(
            isinstance(record["errors"], Mapping) and bool(record["errors"]),
            "failure row must include a complete error record",
        )
        _require(
            predicted is None,
            "failure row cannot claim a predicted candidate",
        )
        return
    _require(
        record["errors"] is None,
        "completed row cannot contain an execution error",
    )
    _require(predicted is not None, "completed row requires a prediction")

    system = record["system_id"]
    if system == "P1":
        _require(
            record["structured_intent"] is None,
            "P1 must not fabricate StructuredIntent evidence",
        )
        for field in (
            "sparse_ranks",
            "dense_ranks",
            "hybrid_ranks_scores",
            "candidate_top_k",
            "constraint_evaluations",
            "feasible_top_k",
            "final_ranking",
        ):
            _require(record[field] == [], f"P1 must not fabricate {field}")
        return

    _require(
        isinstance(record["structured_intent"], Mapping),
        f"{system} completed evidence requires StructuredIntent",
    )
    _require(
        isinstance(record["structured_intent"].get("schema_version"), str)
        and bool(record["structured_intent"]["schema_version"]),
        f"{system} StructuredIntent lacks its schema identity",
    )
    summary = record["constraint_summary"]
    _require(
        isinstance(summary, Mapping)
        and isinstance(summary.get("no_feasible_candidate"), bool)
        and isinstance(summary.get("unsupported_constraints"), list)
        and all(
            isinstance(item, str)
            for item in summary.get("unsupported_constraints", [])
        ),
        f"{system} constraint summary is incomplete",
    )
    if not fallback["used"]:
        for field in (
            "hybrid_ranks_scores",
            "candidate_top_k",
            "constraint_evaluations",
            "feasible_top_k",
            "final_ranking",
        ):
            _require(
                bool(record[field]),
                f"{system} non-fallback evidence requires {field}",
            )
        _require(
            record["final_ranking"][0]["candidate_id"] == predicted,
            f"{system} prediction does not match final rank one",
        )
        selected_evaluation = next(
            (
                item
                for item in record["constraint_evaluations"]
                if item["candidate_id"] == predicted
            ),
            None,
        )
        _require(
            selected_evaluation is not None and selected_evaluation["feasible"] is True,
            f"{system} final selection lacks a satisfied hard-constraint evaluation",
        )
        _require(
            fallback["category"] is None,
            f"{system} non-fallback evidence cannot have a fallback category",
        )
    else:
        _require(
            isinstance(fallback["category"], str)
            and bool(fallback["category"]),
            f"{system} fallback requires a category",
        )


def _validate_completion(
    value: Mapping[str, Any],
    *,
    provenance: Mapping[str, Any],
    records_path: Path,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    completion = _exact_mapping(
        value, _COMPLETION_FIELDS, "offline completion"
    )
    _require(
        completion["schema_version"] == OFFLINE_COMPLETION_SCHEMA_VERSION,
        "completion schema is unsupported",
    )
    _require(
        completion["provenance_fingerprint"]
        == provenance["provenance_fingerprint"],
        "completion provenance does not match",
    )
    _timestamp(completion["completed_utc"], "completion completed_utc")
    _require(
        completion["status"] == "RAW_EVIDENCE_COMPLETE",
        "completion status is not complete",
    )
    _require(
        completion["claims_permitted"] is False,
        "raw completion must not permit statistical claims",
    )
    _require(
        completion["records"] == len(records),
        "completion record count does not match JSONL",
    )
    _require(
        completion["error_records"]
        == sum(record["status"] == "error" for record in records),
        "completion error count does not match JSONL",
    )
    _require(
        completion["recommendations_jsonl_sha256"]
        == file_sha256(records_path),
        "completion JSONL checksum does not match",
    )
    return completion


def validate_offline_evidence(
    evidence_dir: Path,
    *,
    split: LoadedSplit | None = None,
    freeze_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a complete result directory without deriving statistics."""

    root = evidence_dir.resolve()
    raw = root / RAW_DIRECTORY_NAME
    report = root / REPORT_DIRECTORY_NAME
    provenance_path = raw / PROVENANCE_FILENAME
    records_path = raw / RECORDS_FILENAME
    completion_path = report / COMPLETION_FILENAME
    _require(
        root.is_dir(), f"evidence directory does not exist: {root}"
    )
    _require(
        not (root / LOCK_FILENAME).exists(),
        "evidence directory is locked by an active or interrupted runner",
    )
    provenance_value = _read_json(provenance_path, PROVENANCE_FILENAME)
    raw_split = provenance_value.get("split")
    role = (
        raw_split.get("role") if isinstance(raw_split, Mapping) else None
    )
    if split is None:
        _require(
            role == "development",
            "confirmatory validation requires an isolation-verified split",
        )
        split = load_development_split()
    provenance = _validate_provenance(
        provenance_value, split=split, freeze_identity=freeze_identity
    )
    records = _read_records(records_path)

    cases = {case.case_id: case for case in split.bundle.cases}
    _require(
        len(cases) == len(split.bundle.cases),
        "frozen dataset case IDs are not unique",
    )
    effective = provenance["effective_repeats"]
    expected_keys = {
        (
            case.case_id,
            case.family_id,
            case.variant_id,
            system,
            repeat_index,
        )
        for case in split.bundle.cases
        for system in provenance["systems"]
        for repeat_index in range(effective[system])
    }
    seen: set[tuple[str, str, str, str, int]] = set()
    candidate_rows = provenance["candidate_catalog"]["candidates"]
    _require(
        isinstance(candidate_rows, list) and bool(candidate_rows),
        "candidate catalog candidates must be a non-empty list",
    )
    candidates = {
        item["candidate_id"]: item
        for item in candidate_rows
        if isinstance(item, Mapping) and isinstance(item.get("candidate_id"), str)
    }
    _require(
        len(candidates) == len(candidate_rows),
        "candidate catalog contains invalid or duplicate IDs",
    )
    for record in records:
        key = _record_key(record)
        _require(key not in seen, f"duplicate logical execution row: {key!r}")
        _require(
            key in expected_keys,
            f"unexpected logical execution row: {key!r}",
        )
        seen.add(key)
        case = cases.get(record["case_id"])
        _require(
            case is not None,
            "raw record does not join to a unique frozen dataset case",
        )
        _validate_record(
            record,
            case=case,
            split=split,
            provenance=provenance,
            candidates=candidates,
        )
    missing = expected_keys - seen
    _require(
        not missing,
        f"offline evidence is incomplete; missing {len(missing)} expected row(s)",
    )
    _require(
        len(records) == provenance["planned_record_count"],
        "JSONL record count does not match execution plan",
    )
    completion = _validate_completion(
        _read_json(completion_path, COMPLETION_FILENAME),
        provenance=provenance,
        records_path=records_path,
        records=records,
    )
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "status": "PASS",
        "claims_permitted": False,
        "evidence_dir": str(root),
        "run_id": provenance["run_id"],
        "experiment_id": provenance["experiment_id"],
        "dataset_id": split.manifest.dataset_id,
        "dataset_sha256": split.source_file_sha256,
        "split_id": split.manifest.split_id,
        "split_role": split.manifest.role.value,
        "systems": list(provenance["systems"]),
        "requested_repeats": provenance["requested_repeats"],
        "effective_repeats": dict(provenance["effective_repeats"]),
        "records_validated": len(records),
        "error_records": completion["error_records"],
        "recommendations_jsonl_sha256": completion[
            "recommendations_jsonl_sha256"
        ],
        "metric_input_sufficiency": "PASS",
        "statistical_interpretation_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument(
        "--dataset",
        type=Path,
        help="External confirmatory dataset; development uses the tracked split.",
    )
    parser.add_argument(
        "--freeze", type=Path, help="Authoritative confirmatory freeze artifact."
    )
    parser.add_argument("--split-id", help="Expected confirmatory split ID.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        split: LoadedSplit | None = None
        freeze_identity: Mapping[str, Any] | None = None
        if args.dataset is not None or args.freeze is not None:
            _require(
                args.dataset is not None and args.freeze is not None,
                "confirmatory validation requires both --dataset and --freeze",
            )
            from evaluation_v5.isolation import load_confirmatory_split

            loaded = load_confirmatory_split(
                args.dataset,
                args.freeze,
                expected_split_id=args.split_id or "v5-confirmatory",
            )
            split = loaded.split
            freeze_identity = {
                "freeze_id": loaded.freeze_manifest["freeze_id"],
                "freeze_manifest_sha256": file_sha256(args.freeze),
                "frozen_at_utc": loaded.freeze_manifest["created_at_utc"],
                "frozen_by": "authoritative_protocol_v5_freeze",
                "source": "confirmatory_freeze_manifest",
            }
        result = validate_offline_evidence(
            args.dir, split=split, freeze_identity=freeze_identity
        )
        print(
            json.dumps(
                result, ensure_ascii=False, indent=2, sort_keys=True
            )
        )
        return 0
    except (OfflineEvidenceValidationError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": VALIDATION_SCHEMA_VERSION,
                    "status": "FAIL",
                    "claims_permitted": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "OfflineEvidenceValidationError",
    "VALIDATION_SCHEMA_VERSION",
    "validate_offline_evidence",
]
