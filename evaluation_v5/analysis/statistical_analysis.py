"""Family-level statistical analysis for Protocol-v5 offline evidence.

This module is deliberately downstream of the append-only offline runner.  It
validates completed evidence before reading gold labels, never invokes a
recommender, and never treats variants or stochastic repeats as independent
workload families.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import statistics as std_statistics
import subprocess
from typing import Any

from evaluation_v4.dataset import file_sha256
from evaluation_v5.gold_dataset import (
    GoldDatasetValidationError,
    candidate_satisfies_gold,
)
from evaluation_v5.offline.runner import (
    COMPLETION_FILENAME,
    LOCK_FILENAME,
    OFFLINE_PROVENANCE_SCHEMA_VERSION,
    PROVENANCE_FILENAME,
    RAW_DIRECTORY_NAME,
    RECORDS_FILENAME,
    REPORT_DIRECTORY_NAME,
)
from evaluation_v5.offline.validate_evidence import (
    OfflineEvidenceValidationError,
    _read_json as _read_offline_json,
    _read_records as _read_offline_records,
    _validate_completion as _validate_offline_completion,
)
from evaluation_v5.split_dataset import (
    SPLIT_BUNDLE_SCHEMA_VERSION_V2,
    SplitBundleValidationError,
)
from recommender.candidate_corpus import CandidateCorpus, load_candidate_corpus
from recommender.models import ContractValidationError

from .component_scoring import (
    ComponentAnalysisError,
    GoldSource,
    load_component_gold,
    load_validated_evidence,
)
from .statistics import (
    DEFAULT_BOOTSTRAP_REPLICATES,
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_CONFIDENCE_LEVEL as DEFAULT_CI_LEVEL,
    MIN_PAIRED_DECISION_FAMILY_N as DEFAULT_MINIMUM_CLAIM_FAMILIES,
    SEED_DERIVATION_ALGORITHM,
    SMALL_EFFECTIVE_FAMILY_N_THRESHOLD as DEFAULT_SMALL_N_WARNING_FAMILIES,
    STATISTICS_SCHEMA_VERSION,
    derive_bootstrap_seed,
    family_bootstrap_ci,
    family_n_warnings,
    holm_adjust,
    mean,
    paired_effect_sizes,
    paired_family_bootstrap_ci,
    paired_test,
    quantile,
    statistical_decision,
)


PROTOCOL_VERSION = "5.0.0"
STATISTICAL_ANALYSIS_SCHEMA_VERSION = "protocol-v5-statistical-analysis-v1.0.0"
FAMILY_ESTIMATE_SCHEMA_VERSION = "protocol-v5-family-estimate-v1.0.0"
SYSTEM_ESTIMATE_SCHEMA_VERSION = "protocol-v5-system-estimate-v1.0.0"
PAIRED_COMPARISON_SCHEMA_VERSION = "protocol-v5-paired-comparison-v1.0.0"
STRATIFIED_ESTIMATE_SCHEMA_VERSION = "protocol-v5-stratified-estimate-v1.0.0"

OUTPUT_FILES = {
    "family_estimates": "family-estimates.jsonl",
    "system_estimates": "system-estimates.jsonl",
    "paired_comparisons": "paired-comparisons.jsonl",
    "stratified_estimates": "stratified-estimates.jsonl",
}

VARIANT_STRATA = (
    "canonical",
    "paraphrase",
    "vietnamese",
    "noisy",
    "code_centric",
)
_VARIANT_STRATUM_MAP = {
    "canonical_en": "canonical",
    "paraphrase_en": "paraphrase",
    "vietnamese": "vietnamese",
    "informal_or_noisy": "noisy",
    "optional_code_context": "code_centric",
}

DEFAULT_RETRIEVAL_KS = (1, 3, 5)
ALPHA = 0.05
HOLM_REGISTRY_VERSION = "protocol-v5-holm-registry-v1.0.0"
P3_RETAINED = "retained"
P3_NOT_RETAINED = "not_retained"
P3_GATE_STATUSES = frozenset({P3_RETAINED, P3_NOT_RETAINED})
LATENCY_POPULATION = "all_validated_recommendation_attempts_with_recorded_duration"


class StatisticalAnalysisError(RuntimeError):
    """Inputs or derived outputs cannot support Protocol-v5 statistics."""


@dataclass(frozen=True, slots=True)
class StatisticalAnalysisResult:
    family_estimates: tuple[Mapping[str, Any], ...]
    system_estimates: tuple[Mapping[str, Any], ...]
    paired_comparisons: tuple[Mapping[str, Any], ...]
    stratified_estimates: tuple[Mapping[str, Any], ...]
    seed_registry: Mapping[str, int]
    metric_registry: Mapping[str, Mapping[str, Any]]
    holm_registry: Mapping[str, Any]
    p3_inference: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class EndpointDefinition:
    key: str
    direction: str
    domain: str
    unit: str
    higher_is_better: bool
    null_value: float = 0.0
    retrieval_only: bool = False
    diagnostic: bool = False


def _retrieval_endpoint_definitions(ks: Sequence[int]) -> tuple[EndpointDefinition, ...]:
    values: list[EndpointDefinition] = []
    for k in ks:
        values.extend(
            (
                EndpointDefinition(f"retrieval_hit_at_{k}", "higher_is_better", "retrieval", "proportion", True, retrieval_only=True),
                EndpointDefinition(f"retrieval_recall_at_{k}", "higher_is_better", "retrieval", "proportion", True, retrieval_only=True),
                EndpointDefinition(f"retrieval_ndcg_at_{k}", "higher_is_better", "retrieval", "unit_interval_score", True, retrieval_only=True),
            )
        )
    values.append(
        EndpointDefinition("retrieval_mrr", "higher_is_better", "retrieval", "unit_interval_score", True, retrieval_only=True)
    )
    return tuple(values)


def _endpoint_definitions(ks: Sequence[int]) -> tuple[EndpointDefinition, ...]:
    return (
        EndpointDefinition("joint_accept_at_1", "higher_is_better", "primary", "proportion", True),
        EndpointDefinition("profile_acceptable_accuracy", "higher_is_better", "quality_safety", "proportion", True),
        EndpointDefinition("image_acceptable_accuracy", "higher_is_better", "quality_safety", "proportion", True),
        EndpointDefinition("hard_constraint_violation_rate", "lower_is_better", "quality_safety", "proportion", False),
        EndpointDefinition("robustness_rate", "higher_is_better", "robustness", "proportion", True),
        *_retrieval_endpoint_definitions(ks),
        EndpointDefinition("latency_seconds", "lower_is_better", "latency", "seconds", False),
        EndpointDefinition("selection_coverage", "higher_is_better", "diagnostic", "proportion", True, diagnostic=True),
        EndpointDefinition("infeasible_detection_accuracy", "higher_is_better", "diagnostic", "proportion", True, diagnostic=True),
        EndpointDefinition("ambiguity_detection_accuracy", "higher_is_better", "diagnostic", "proportion", True, diagnostic=True),
    )


def _metric_fields(endpoint: EndpointDefinition) -> dict[str, Any]:
    return {
        "metric": endpoint.key,
        "effect_definition": "second_system_minus_first_system",
        "direction": endpoint.direction,
        "unit": endpoint.unit,
        "higher_is_better": endpoint.higher_is_better,
        "null_value": endpoint.null_value,
        "holm_domain": endpoint.domain,
    }


def _metric_registry(
    endpoints: Sequence[EndpointDefinition],
) -> dict[str, dict[str, Any]]:
    return {endpoint.key: _metric_fields(endpoint) for endpoint in endpoints}


def _safe_mean(values: Sequence[float | int | bool | None]) -> float | None:
    return mean(value for value in values if value is not None)


def _distribution(values: Sequence[float | int | None]) -> dict[str, Any]:
    selected = sorted(
        float(value)
        for value in values
        if value is not None and math.isfinite(float(value))
    )
    return {
        "count": len(selected),
        "mean": _safe_mean(selected),
        "median": quantile(selected, 0.5),
        "p95": quantile(selected, 0.95),
        "minimum": min(selected) if selected else None,
        "maximum": max(selected) if selected else None,
        "standard_deviation": (
            std_statistics.pstdev(selected) if len(selected) >= 2 else None
        ),
    }


def _ndcg(ranked: Sequence[str], acceptable: set[str], k: int) -> float:
    if not acceptable:
        return 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, candidate_id in enumerate(ranked[:k], start=1)
        if candidate_id in acceptable
    )
    ideal = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, min(k, len(acceptable)) + 1)
    )
    return dcg / ideal if ideal else 0.0


def _reciprocal_rank(ranked: Sequence[str], acceptable: set[str]) -> float:
    for rank, candidate_id in enumerate(ranked, start=1):
        if candidate_id in acceptable:
            return 1.0 / rank
    return 0.0


def _variant_stratum(variant_class: str) -> str | None:
    return _VARIANT_STRATUM_MAP.get(variant_class)


def _require_v2_gold(gold: GoldSource) -> None:
    if gold.split is None or gold.split.bundle.schema_version != SPLIT_BUNDLE_SCHEMA_VERSION_V2:
        raise StatisticalAnalysisError(
            "Protocol-v5 statistics require frozen family gold or compiled split v2 metadata"
        )
    for case in gold.split.bundle.cases:
        if case.family_metadata is None or case.variant_metadata is None:
            raise StatisticalAnalysisError(
                "Protocol-v5 statistics require trusted variant classes and workload strata"
            )


def _prevalidate_completed_evidence_envelope(evidence_dir: Path) -> None:
    """Verify immutable raw completion before opening any external gold file.

    This first gate deliberately performs no case/gold join.  The existing
    offline validator still performs the authoritative full validation after
    the isolated split has been loaded.
    """

    root = evidence_dir.resolve()
    if not root.is_dir():
        raise StatisticalAnalysisError(f"evidence directory does not exist: {root}")
    if (root / LOCK_FILENAME).exists():
        raise StatisticalAnalysisError(
            "evidence directory is locked by an active or interrupted runner"
        )
    provenance = _read_offline_json(
        root / RAW_DIRECTORY_NAME / PROVENANCE_FILENAME,
        PROVENANCE_FILENAME,
    )
    if provenance.get("schema_version") != OFFLINE_PROVENANCE_SCHEMA_VERSION:
        raise StatisticalAnalysisError("offline provenance schema is unsupported")
    records_path = root / RAW_DIRECTORY_NAME / RECORDS_FILENAME
    records = _read_offline_records(records_path)
    _validate_offline_completion(
        _read_offline_json(
            root / REPORT_DIRECTORY_NAME / COMPLETION_FILENAME,
            COMPLETION_FILENAME,
        ),
        provenance=provenance,
        records_path=records_path,
        records=records,
    )


def _validate_retrieval_ks(values: Sequence[int]) -> tuple[int, ...]:
    selected = tuple(sorted(set(values)))
    if not selected or any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in selected):
        raise StatisticalAnalysisError("retrieval K values must be positive integers")
    return selected


def _score_execution(
    record: Mapping[str, Any],
    case: Any,
    *,
    corpus: CandidateCorpus,
    retrieval_ks: Sequence[int],
) -> dict[str, Any]:
    family_metadata = dict(case.family_metadata or {})
    variant_metadata = dict(case.variant_metadata or {})
    gold = dict(case.gold)
    candidate_gold = dict(gold["candidate_gold"])
    profile_gold = dict(gold["profile_gold"])
    image_gold = dict(gold["image_gold"])
    policy_gold = dict(gold["policy_gold"])
    structured_gold = dict(gold["gold_structured_intent"])

    system = str(record["system_id"])
    expected_feasibility = str(policy_gold["expected_feasibility"])
    feasible = expected_feasibility == "feasible"
    predicted_candidate = record.get("predicted_candidate_id")
    predicted_profile = record.get("predicted_profile_id")
    predicted_image = record.get("predicted_image_id")
    candidate_id_present = bool(
        isinstance(predicted_candidate, str) and predicted_candidate
    )
    selected_candidate = (
        corpus.get(str(predicted_candidate))
        if candidate_id_present
        else None
    )
    hard_violation = (
        bool(
            selected_candidate is None
            or not candidate_satisfies_gold(
                selected_candidate,
                structured_gold,
                image_gold,
            )
        )
        if candidate_id_present
        else None
    )
    acceptable_candidates = set(candidate_gold["acceptable_candidate_ids"])
    acceptable_profiles = set(profile_gold["acceptable_profile_ids"])
    acceptable_images = set(image_gold["acceptable_image_ids"])
    selection_present = bool(
        candidate_id_present
        and record.get("status") == "completed"
    )
    profile_acceptable = (
        bool(selection_present and predicted_profile in acceptable_profiles)
        if feasible
        else None
    )
    image_acceptable = (
        bool(selection_present and predicted_image in acceptable_images)
        if feasible
        else None
    )
    joint_acceptable = (
        bool(
            selection_present
            and predicted_candidate in acceptable_candidates
            and profile_acceptable
            and image_acceptable
            and not hard_violation
        )
        if feasible
        else None
    )

    ranked = [
        str(item["candidate_id"])
        for item in record.get("candidate_top_k", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("candidate_id"), str)
        and item.get("candidate_id")
    ]
    retrieval: dict[str, float | bool | None] = {}
    for k in retrieval_ks:
        if system == "P1" or not feasible:
            hit = recall = ndcg = None
        else:
            found = acceptable_candidates & set(ranked[:k])
            hit = bool(found)
            recall = len(found) / len(acceptable_candidates)
            ndcg = _ndcg(ranked, acceptable_candidates, k)
        retrieval[f"retrieval_hit_at_{k}"] = hit
        retrieval[f"retrieval_recall_at_{k}"] = recall
        retrieval[f"retrieval_ndcg_at_{k}"] = ndcg
    retrieval["retrieval_mrr"] = (
        None
        if system == "P1" or not feasible
        else _reciprocal_rank(ranked, acceptable_candidates)
    )

    constraint_summary = record.get("constraint_summary")
    no_feasible = bool(
        constraint_summary.get("no_feasible_candidate", False)
        if isinstance(constraint_summary, Mapping)
        else False
    )
    metric_inputs = record.get("metric_inputs")
    if isinstance(metric_inputs, Mapping):
        no_feasible = no_feasible or bool(metric_inputs.get("infeasible_request_signal", False))
    structured_intent = record.get("structured_intent")
    predicted_ambiguity = bool(
        isinstance(structured_intent, Mapping)
        and structured_intent.get("ambiguities")
    ) or no_feasible

    latency_components = record.get("latency_components")
    latency = (
        latency_components.get("total_elapsed_seconds")
        if isinstance(latency_components, Mapping)
        else None
    )
    if latency is not None:
        latency = float(latency)
    fallback = record.get("fallback")
    fallback_used = bool(
        fallback.get("used", False)
        if isinstance(fallback, Mapping)
        else metric_inputs.get("fallback_used", False)
        if isinstance(metric_inputs, Mapping)
        else record.get("fallback_used", False)
    )
    if isinstance(latency_components, Mapping):
        timed_out = bool(latency_components.get("timed_out", False))
    else:
        timed_out = False
    execution_status = str(record.get("status"))

    variant_class = str(variant_metadata["variant_class"])
    equivalence_status = str(variant_metadata["equivalence_status"])
    return {
        "system_id": system,
        "case_id": str(case.case_id),
        "family_id": str(case.family_id),
        "variant_id": str(case.variant_id),
        "repeat_index": int(record["repeat_index"]),
        "variant_class": variant_class,
        "variant_stratum": _variant_stratum(variant_class),
        "equivalence_status": equivalence_status,
        "language": str(case.language),
        "workload_stratum": str(family_metadata["workload_stratum"]),
        "expected_feasibility": expected_feasibility,
        "joint_accept_at_1": joint_acceptable,
        "profile_acceptable_accuracy": profile_acceptable,
        "image_acceptable_accuracy": image_acceptable,
        "hard_constraint_violation_rate": hard_violation if feasible else None,
        "selection_coverage": selection_present if feasible else None,
        "infeasible_detection_accuracy": no_feasible if expected_feasibility == "infeasible" else None,
        "ambiguity_detection_accuracy": predicted_ambiguity if expected_feasibility == "ambiguous" else None,
        "latency_seconds": latency,
        "latency_population": LATENCY_POPULATION,
        "latency_observation_status": (
            "RECORDED" if latency is not None else "MISSING"
        ),
        "execution_outcome": (
            "timeout"
            if timed_out
            else "fallback"
            if fallback_used
            else execution_status
        ),
        **retrieval,
    }


def _aggregate_variant_rows(
    execution_rows: Sequence[Mapping[str, Any]],
    endpoints: Sequence[EndpointDefinition],
) -> tuple[dict[str, Any], ...]:
    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in execution_rows:
        grouped[(str(row["system_id"]), str(row["family_id"]), str(row["variant_id"]))].append(row)
    results: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        rows = sorted(rows, key=lambda item: int(item["repeat_index"]))
        first = rows[0]
        values = {
            endpoint.key: _safe_mean([row.get(endpoint.key) for row in rows])
            for endpoint in endpoints
            if endpoint.key != "robustness_rate"
        }
        results.append(
            {
                "system_id": key[0],
                "family_id": key[1],
                "variant_id": key[2],
                "variant_class": first["variant_class"],
                "variant_stratum": first["variant_stratum"],
                "equivalence_status": first["equivalence_status"],
                "language": first["language"],
                "workload_stratum": first["workload_stratum"],
                "expected_feasibility": first["expected_feasibility"],
                "repeat_count": len(rows),
                "repeat_indices": [int(row["repeat_index"]) for row in rows],
                "values": values,
                "execution_latency_values": [
                    float(row["latency_seconds"])
                    for row in rows
                    if row.get("latency_seconds") is not None
                ],
                "execution_latency_distribution": _distribution(
                    [row.get("latency_seconds") for row in rows]
                ),
                "latency_population": LATENCY_POPULATION,
                "latency_outcome_counts": {
                    outcome: sum(
                        row.get("execution_outcome") == outcome for row in rows
                    )
                    for outcome in sorted(
                        {str(row.get("execution_outcome")) for row in rows}
                    )
                },
                "execution_value_distributions": {
                    endpoint.key: _distribution(
                        [row.get(endpoint.key) for row in rows]
                    )
                    for endpoint in endpoints
                    if endpoint.key != "robustness_rate"
                },
            }
        )
    return tuple(results)


def _aggregate_family_rows(
    variant_rows: Sequence[Mapping[str, Any]],
    endpoints: Sequence[EndpointDefinition],
) -> tuple[dict[str, Any], ...]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in variant_rows:
        grouped[(str(row["system_id"]), str(row["family_id"]))].append(row)
    results: list[dict[str, Any]] = []
    for (system, family), rows in sorted(grouped.items()):
        rows = sorted(rows, key=lambda item: str(item["variant_id"]))
        workload_strata = {str(row["workload_stratum"]) for row in rows}
        if len(workload_strata) != 1:
            raise StatisticalAnalysisError(f"family {family!r} spans multiple workload strata")
        values: dict[str, float | None] = {}
        denominators: dict[str, int] = {}
        sums: dict[str, float] = {}
        for endpoint in endpoints:
            if endpoint.key == "robustness_rate":
                selected = [
                    row["values"].get("joint_accept_at_1")
                    for row in rows
                    if row["equivalence_status"] == "reviewed_equivalent"
                    and row["variant_class"] != "canonical_en"
                ]
            else:
                selected = [row["values"].get(endpoint.key) for row in rows]
            finite = [float(value) for value in selected if value is not None]
            values[endpoint.key] = _safe_mean(finite)
            denominators[endpoint.key] = len(finite)
            sums[endpoint.key] = sum(finite)
        execution_count = sum(int(row["repeat_count"]) for row in rows)
        reviewed_equivalent_count = sum(
            row["equivalence_status"] == "reviewed_equivalent" for row in rows
        )
        results.append(
            {
                "schema_version": FAMILY_ESTIMATE_SCHEMA_VERSION,
                "system_id": system,
                "family_id": family,
                "workload_stratum": next(iter(workload_strata)),
                "variant_ids": [str(row["variant_id"]) for row in rows],
                "variant_classes": sorted({str(row["variant_class"]) for row in rows}),
                "variant_strata": sorted(
                    {str(row["variant_stratum"]) for row in rows if row["variant_stratum"] is not None}
                ),
                "variant_count": len(rows),
                "reviewed_equivalent_variant_count": reviewed_equivalent_count,
                "execution_count": execution_count,
                "repeat_counts": {
                    str(row["variant_id"]): int(row["repeat_count"]) for row in rows
                },
                "values": values,
                "endpoint_applicability": {
                    endpoint.key: (
                        "NOT_APPLICABLE"
                        if endpoint.retrieval_only and system == "P1"
                        else (
                            "AVAILABLE"
                            if values[endpoint.key] is not None
                            else "NOT_AVAILABLE"
                        )
                    )
                    for endpoint in endpoints
                },
                "endpoint_directions": {
                    endpoint.key: endpoint.direction for endpoint in endpoints
                },
                "metric_registry": {
                    endpoint.key: _metric_fields(endpoint) for endpoint in endpoints
                },
                "endpoint_provenance": {
                    endpoint.key: (
                        "INHERITED_FROM_P2_INTERNAL_PIPELINE"
                        if endpoint.retrieval_only and system == "P3"
                        else "NATIVE_P2_RETRIEVAL"
                        if endpoint.retrieval_only and system == "P2"
                        else "NOT_APPLICABLE"
                        if endpoint.retrieval_only
                        else "DIRECTLY_DERIVED"
                    )
                    for endpoint in endpoints
                },
                "endpoint_variant_denominators": denominators,
                "endpoint_variant_sums": sums,
                "latency_execution_distribution": _distribution(
                    [
                        value
                        for row in rows
                        for value in row["execution_latency_values"]
                    ]
                ),
                "latency_population": LATENCY_POPULATION,
                "latency_outcome_counts": {
                    outcome: sum(
                        int(row["latency_outcome_counts"].get(outcome, 0))
                        for row in rows
                    )
                    for outcome in sorted(
                        {
                            outcome
                            for row in rows
                            for outcome in row["latency_outcome_counts"]
                        }
                    )
                },
                "execution_stability_by_variant": {
                    str(row["variant_id"]): {
                        "repeat_count": int(row["repeat_count"]),
                        "endpoint_distributions": row[
                            "execution_value_distributions"
                        ],
                    }
                    for row in rows
                },
                "aggregation_unit": "workload_family",
                "applicability": "AVAILABLE",
                "direction": "endpoint_specific_in_values",
                "family_count": 1,
                "effective_family_n": 1,
                "ci_method": None,
                "bootstrap_seed": None,
                "bootstrap_seed_namespace": None,
                "warning_codes": [
                    *family_n_warnings(1),
                    "DESCRIPTIVE_FAMILY_ROW_NO_INFERENCE",
                ],
            }
        )
    return tuple(results)


def _seed_for(
    registry: dict[str, int],
    *,
    base_seed: int,
    namespace: str,
) -> int:
    seed = derive_bootstrap_seed(base_seed, namespace)
    existing = registry.get(namespace)
    if existing is not None and existing != seed:
        raise AssertionError("bootstrap seed namespace changed during analysis")
    registry[namespace] = seed
    return seed


def _not_available_system_estimate(
    *,
    system: str,
    endpoint: EndpointDefinition,
    applicability: str,
    warning_codes: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": SYSTEM_ESTIMATE_SCHEMA_VERSION,
        "system_id": system,
        "endpoint": endpoint.key,
        "domain": endpoint.domain,
        **_metric_fields(endpoint),
        "applicability": applicability,
        "estimate": None,
        "ci_level": DEFAULT_CI_LEVEL,
        "ci_low": None,
        "ci_high": None,
        "ci_method": None,
        "family_count": 0,
        "effective_family_n": 0,
        "variant_count": 0,
        "execution_count": 0,
        "bootstrap_seed": None,
        "bootstrap_seed_namespace": None,
        "warning_codes": list(warning_codes),
        "aggregation_unit": "workload_family",
        "retrieval_provenance": (
            "INHERITED_FROM_P2_INTERNAL_PIPELINE"
            if endpoint.retrieval_only and system == "P3"
            else "NOT_APPLICABLE"
            if endpoint.retrieval_only
            else None
        ),
        "population_definition": (
            LATENCY_POPULATION if endpoint.key == "latency_seconds" else None
        ),
    }


def _system_estimate(
    system: str,
    endpoint: EndpointDefinition,
    family_rows: Sequence[Mapping[str, Any]],
    *,
    replicates: int,
    base_seed: int,
    seed_registry: dict[str, int],
) -> dict[str, Any]:
    if endpoint.retrieval_only and system == "P1":
        return _not_available_system_estimate(
            system=system,
            endpoint=endpoint,
            applicability="NOT_APPLICABLE",
            warning_codes=("P1_HAS_NO_RETRIEVAL_STAGE",),
        )
    selected = [
        row for row in family_rows
        if row["system_id"] == system and row["values"].get(endpoint.key) is not None
    ]
    if not selected:
        return _not_available_system_estimate(
            system=system,
            endpoint=endpoint,
            applicability="NOT_AVAILABLE",
            warning_codes=("NO_ELIGIBLE_FAMILIES",),
        )
    namespace = f"system|{system}|{endpoint.key}|overall"
    seed = _seed_for(seed_registry, base_seed=base_seed, namespace=namespace)
    values = [float(row["values"][endpoint.key]) for row in selected]
    bootstrap_rows = [
        {"family_id": str(row["family_id"]), "value": float(row["values"][endpoint.key])}
        for row in selected
    ]
    low, high = family_bootstrap_ci(
        bootstrap_rows,
        "value",
        replicates=replicates,
        seed=seed,
    )
    warnings = family_n_warnings(len(selected))
    return {
        "schema_version": SYSTEM_ESTIMATE_SCHEMA_VERSION,
        "system_id": system,
        "endpoint": endpoint.key,
        "domain": endpoint.domain,
        **_metric_fields(endpoint),
        "applicability": "AVAILABLE",
        "estimate": _safe_mean(values),
        "ci_level": DEFAULT_CI_LEVEL,
        "ci_low": low,
        "ci_high": high,
        "ci_method": "workload_family_percentile_bootstrap" if low is not None else None,
        "family_count": len(selected),
        "effective_family_n": len(selected),
        "variant_count": sum(int(row["endpoint_variant_denominators"][endpoint.key]) for row in selected),
        "execution_count": sum(int(row["execution_count"]) for row in selected),
        "descriptive_variant_micro_estimate": (
            (
                sum(float(row["endpoint_variant_sums"][endpoint.key]) for row in selected)
                / sum(int(row["endpoint_variant_denominators"][endpoint.key]) for row in selected)
            )
            if endpoint.key == "robustness_rate"
            and sum(int(row["endpoint_variant_denominators"][endpoint.key]) for row in selected) > 0
            else None
        ),
        "bootstrap_seed": seed,
        "bootstrap_seed_namespace": namespace,
        "warning_codes": warnings,
        "aggregation_unit": "workload_family",
        "retrieval_provenance": (
            "INHERITED_FROM_P2_INTERNAL_PIPELINE"
            if endpoint.retrieval_only and system == "P3"
            else "NATIVE_P2_RETRIEVAL"
            if endpoint.retrieval_only and system == "P2"
            else None
        ),
        "population_definition": (
            LATENCY_POPULATION if endpoint.key == "latency_seconds" else None
        ),
        "execution_outcome_counts": (
            {
                outcome: sum(
                    int(row["latency_outcome_counts"].get(outcome, 0))
                    for row in selected
                )
                for outcome in sorted(
                    {
                        outcome
                        for row in selected
                        for outcome in row["latency_outcome_counts"]
                    }
                )
            }
            if endpoint.key == "latency_seconds"
            else None
        ),
    }


def _pair_rows(
    family_rows: Sequence[Mapping[str, Any]],
    *,
    first: str,
    second: str,
    endpoint: str,
) -> list[dict[str, Any]]:
    indexed: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in family_rows:
        if row["system_id"] in {first, second} and row["values"].get(endpoint) is not None:
            indexed[str(row["family_id"])][str(row["system_id"])] = row
    paired: list[dict[str, Any]] = []
    for family, systems in sorted(indexed.items()):
        if set(systems) != {first, second}:
            continue
        first_value = float(systems[first]["values"][endpoint])
        second_value = float(systems[second]["values"][endpoint])
        paired.append(
            {
                "family_id": family,
                "first": first_value,
                "second": second_value,
                "difference": second_value - first_value,
                "first_variant_denominator": int(
                    systems[first]["endpoint_variant_denominators"][endpoint]
                ),
                "second_variant_denominator": int(
                    systems[second]["endpoint_variant_denominators"][endpoint]
                ),
                "first_execution_count": int(systems[first]["execution_count"]),
                "second_execution_count": int(systems[second]["execution_count"]),
            }
        )
    return paired


def _p3_inference_policy(gold: GoldSource, systems: Sequence[str]) -> dict[str, Any]:
    if "P3" not in systems:
        return {
            "evidence_present": False,
            "gate_status": "not_applicable",
            "inference_permitted": False,
            "source": None,
            "reason_code": "P3_EVIDENCE_ABSENT",
        }
    identity = gold.p3_gate_identity
    status = identity.get("status") if isinstance(identity, Mapping) else None
    if status not in P3_GATE_STATUSES:
        return {
            "evidence_present": True,
            "gate_status": "not_available",
            "inference_permitted": False,
            "source": None,
            "reason_code": "P3_FROZEN_GATE_NOT_AVAILABLE",
        }
    return {
        "evidence_present": True,
        "gate_status": status,
        "inference_permitted": status == P3_RETAINED,
        "source": dict(identity),
        "reason_code": (
            "P3_RETAINED_FOR_INFERENCE"
            if status == P3_RETAINED
            else "P3_NOT_RETAINED_FOR_INFERENCE"
        ),
    }


def _noninferential_comparison(
    family_rows: Sequence[Mapping[str, Any]],
    endpoint: EndpointDefinition,
    *,
    first: str,
    second: str,
    applicability: str,
    reason_code: str,
    extra_warnings: Sequence[str] = (),
) -> dict[str, Any]:
    paired = _pair_rows(
        family_rows,
        first=first,
        second=second,
        endpoint=endpoint.key,
    )
    family_n = len(paired)
    planned_family = (
        None
        if endpoint.domain in {"primary", "diagnostic"}
        or applicability == "NOT_RETAINED"
        else f"{second}_minus_{first}:{endpoint.domain}"
    )
    return {
        "schema_version": PAIRED_COMPARISON_SCHEMA_VERSION,
        "comparison_id": f"{second}_minus_{first}",
        "first_system": first,
        "second_system": second,
        "endpoint": endpoint.key,
        "domain": endpoint.domain,
        **_metric_fields(endpoint),
        "applicability": applicability,
        "unavailability_reason": reason_code,
        "hypothesis_status": f"UNTESTED_{applicability}",
        "family_count": family_n,
        "effective_family_n": family_n,
        "variant_count": sum(
            row["first_variant_denominator"] + row["second_variant_denominator"]
            for row in paired
        ),
        "execution_count": sum(
            row["first_execution_count"] + row["second_execution_count"]
            for row in paired
        ),
        "first_variant_count": sum(
            row["first_variant_denominator"] for row in paired
        ),
        "second_variant_count": sum(
            row["second_variant_denominator"] for row in paired
        ),
        "first_execution_count": sum(
            row["first_execution_count"] for row in paired
        ),
        "second_execution_count": sum(
            row["second_execution_count"] for row in paired
        ),
        "test_method": None,
        "test_details": {"reason_code": reason_code},
        "p_value_raw": None,
        "p_value_holm": None,
        "planned_multiplicity_family": planned_family,
        "multiplicity_family": None,
        "multiplicity_method": "NONE_UNTESTED",
        "holm_hypotheses_tested": 0,
        "alpha": ALPHA,
        "effect_direction": "second_minus_first",
        "effect_label": f"{second}_minus_{first}",
        "positive_effect_favors": (
            second if endpoint.higher_is_better else first
        ),
        "effect_sizes": None,
        "effect_ci_low": None,
        "effect_ci_high": None,
        "effect_ci_level": DEFAULT_CI_LEVEL,
        "effect_ci_method": None,
        "ci_method": None,
        "bootstrap_seed": None,
        "bootstrap_seed_namespace": None,
        "statistical_decision": "NOT_COMPUTABLE",
        "warning_codes": [
            *family_n_warnings(family_n),
            reason_code,
            *extra_warnings,
        ],
        "aggregation_unit": "paired_workload_family",
        "retrieval_provenance": (
            "INHERITED_FROM_P2_INTERNAL_PIPELINE"
            if endpoint.retrieval_only and second == "P3"
            else "P1_HAS_NO_RETRIEVAL_STAGE"
            if endpoint.retrieval_only and first == "P1"
            else None
        ),
        "population_definition": (
            LATENCY_POPULATION if endpoint.key == "latency_seconds" else None
        ),
    }


def _paired_comparison(
    family_rows: Sequence[Mapping[str, Any]],
    endpoint: EndpointDefinition,
    *,
    first: str,
    second: str,
    replicates: int,
    base_seed: int,
    seed_registry: dict[str, int],
) -> dict[str, Any]:
    paired = _pair_rows(
        family_rows,
        first=first,
        second=second,
        endpoint=endpoint.key,
    )
    namespace = f"comparison|{second}_minus_{first}|{endpoint.key}|overall"
    family_n = len(paired)
    seed = (
        _seed_for(seed_registry, base_seed=base_seed, namespace=namespace)
        if family_n >= 2
        else None
    )
    warnings = family_n_warnings(family_n)
    binary_outcome = bool(
        paired
        and endpoint.key
        in {
            "joint_accept_at_1",
            "profile_acceptable_accuracy",
            "image_acceptable_accuracy",
            "hard_constraint_violation_rate",
        }
        and all(
            row["first_variant_denominator"] == 1
            and row["second_variant_denominator"] == 1
            and row["first_execution_count"] == 1
            and row["second_execution_count"] == 1
            and row["first"] in {0.0, 1.0}
            and row["second"] in {0.0, 1.0}
            for row in paired
        )
    )
    if family_n < 2:
        test = {
            "test_method": None,
            "p_value_raw": None,
            "pairs": family_n,
            "informative_pairs": 0,
        }
        effects = {
            "effect_direction": "second_minus_first",
            "pairs": family_n,
            "mean_difference": (
                paired[0]["difference"] if paired else None
            ),
            "risk_difference": (
                paired[0]["difference"]
                if binary_outcome
                else None
            ),
            "median_paired_difference": (
                paired[0]["difference"] if paired else None
            ),
            "cohens_dz": None,
            "matched_pairs_rank_biserial": None,
        }
        ci_low, ci_high = None, None
    else:
        first_values = [row["first"] for row in paired]
        second_values = [row["second"] for row in paired]
        test = paired_test(first_values, second_values, binary_outcome=binary_outcome)
        effects = paired_effect_sizes(first_values, second_values)
        if not binary_outcome:
            effects["risk_difference"] = None
        ci_low, ci_high = paired_family_bootstrap_ci(
            paired,
            "first",
            "second",
            replicates=replicates,
            seed=seed,
        )
    hypothesis_tested = test.get("p_value_raw") is not None
    unavailability_reason = (
        None
        if hypothesis_tested
        else "NO_COMMON_ELIGIBLE_FAMILIES"
        if family_n == 0
        else "INSUFFICIENT_EFFECTIVE_FAMILY_N"
        if family_n < 2
        else "TEST_NOT_COMPUTABLE"
    )
    if unavailability_reason is not None and unavailability_reason not in warnings:
        warnings.append(unavailability_reason)
    planned_family = (
        None
        if endpoint.domain in {"primary", "diagnostic"}
        else f"{second}_minus_{first}:{endpoint.domain}"
    )
    return {
        "schema_version": PAIRED_COMPARISON_SCHEMA_VERSION,
        "comparison_id": f"{second}_minus_{first}",
        "first_system": first,
        "second_system": second,
        "endpoint": endpoint.key,
        "domain": endpoint.domain,
        **_metric_fields(endpoint),
        "applicability": "AVAILABLE" if paired else "NOT_AVAILABLE",
        "unavailability_reason": unavailability_reason,
        "hypothesis_status": (
            "TESTED" if hypothesis_tested else "UNTESTED_NOT_COMPUTABLE"
        ),
        "family_count": family_n,
        "effective_family_n": family_n,
        "variant_count": sum(
            row["first_variant_denominator"] + row["second_variant_denominator"]
            for row in paired
        ),
        "execution_count": sum(
            row["first_execution_count"] + row["second_execution_count"]
            for row in paired
        ),
        "first_variant_count": sum(
            row["first_variant_denominator"] for row in paired
        ),
        "second_variant_count": sum(
            row["second_variant_denominator"] for row in paired
        ),
        "first_execution_count": sum(
            row["first_execution_count"] for row in paired
        ),
        "second_execution_count": sum(
            row["second_execution_count"] for row in paired
        ),
        "test_method": test.get("test_method"),
        "test_details": {
            key: value
            for key, value in test.items()
            if key not in {"test_method", "p_value_raw"}
        },
        "p_value_raw": test.get("p_value_raw"),
        "p_value_holm": None,
        "planned_multiplicity_family": planned_family,
        "multiplicity_family": (
            planned_family if hypothesis_tested else None
        ),
        "multiplicity_method": (
            "NONE_PRIMARY"
            if endpoint.domain == "primary"
            else "HOLM"
            if hypothesis_tested
            else "NONE_UNTESTED"
        ),
        "holm_hypotheses_tested": 0,
        "alpha": ALPHA,
        "effect_direction": "second_minus_first",
        "effect_label": f"{second}_minus_{first}",
        "positive_effect_favors": (
            second if endpoint.higher_is_better else first
        ),
        "effect_sizes": effects,
        "effect_ci_low": ci_low,
        "effect_ci_high": ci_high,
        "effect_ci_level": DEFAULT_CI_LEVEL,
        "effect_ci_method": (
            "paired_workload_family_percentile_bootstrap"
            if ci_low is not None
            else None
        ),
        "ci_method": (
            "paired_workload_family_percentile_bootstrap"
            if ci_low is not None
            else None
        ),
        "bootstrap_seed": seed,
        "bootstrap_seed_namespace": namespace if seed is not None else None,
        "statistical_decision": statistical_decision(
            test.get("p_value_raw"),
            family_n,
            alpha=ALPHA,
        ),
        "warning_codes": warnings,
        "aggregation_unit": "paired_workload_family",
        "retrieval_provenance": (
            "INHERITED_FROM_P2_INTERNAL_PIPELINE"
            if endpoint.retrieval_only and second == "P3"
            else None
        ),
        "population_definition": (
            LATENCY_POPULATION if endpoint.key == "latency_seconds" else None
        ),
    }


def _apply_holm(comparisons: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in comparisons:
        family = row.get("multiplicity_family")
        if row.get("hypothesis_status") == "TESTED" and row.get("domain") != "primary":
            if family is None or row.get("p_value_raw") is None:
                raise StatisticalAnalysisError(
                    "tested secondary hypothesis lacks its predeclared Holm family"
                )
            grouped[str(family)].append(row)
    for family, rows in sorted(grouped.items()):
        adjusted = holm_adjust([float(row["p_value_raw"]) for row in rows])
        for row, value in zip(rows, adjusted):
            row["p_value_holm"] = value
            row["statistical_decision"] = statistical_decision(
                value,
                int(row["effective_family_n"]),
                alpha=ALPHA,
            )
            row["holm_hypotheses_tested"] = len(rows)
            row["multiplicity_family"] = family
    for row in comparisons:
        if row["domain"] == "primary":
            row["holm_hypotheses_tested"] = 0
        elif row.get("multiplicity_family") is None:
            row["holm_hypotheses_tested"] = 0


def _holm_registry(
    comparisons: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    registry: dict[str, Any] = {
        "registry_version": HOLM_REGISTRY_VERSION,
        "alpha": ALPHA,
        "membership_rule": (
            "predeclared_by_comparison_endpoint_and_domain; activation depends "
            "only on structural applicability and test computability, never on p-value magnitude"
        ),
        "comparisons_separate": True,
        "comparison_families": {},
        "ineligible_comparisons": {},
    }
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    ineligible: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in comparisons:
        domain = str(row["domain"])
        if domain in {"primary", "diagnostic"}:
            continue
        if row.get("applicability") == "NOT_RETAINED":
            ineligible[str(row["comparison_id"])].append(
                {
                    "endpoint": str(row["endpoint"]),
                    "status": row.get("hypothesis_status"),
                    "reason": row.get("unavailability_reason"),
                }
            )
            continue
        grouped[(str(row["comparison_id"]), domain)].append(row)
    families: dict[str, Any] = {}
    for (comparison_id, domain), rows in sorted(grouped.items()):
        comparison = families.setdefault(comparison_id, {})
        comparison[domain] = {
            "planned_hypotheses": [
                str(row["endpoint"]) for row in sorted(rows, key=lambda item: str(item["endpoint"]))
            ],
            "tested_hypotheses": [
                str(row["endpoint"])
                for row in sorted(rows, key=lambda item: str(item["endpoint"]))
                if row.get("hypothesis_status") == "TESTED"
            ],
            "untested_hypotheses": [
                {
                    "endpoint": str(row["endpoint"]),
                    "status": row.get("hypothesis_status"),
                    "reason": row.get("unavailability_reason"),
                }
                for row in sorted(rows, key=lambda item: str(item["endpoint"]))
                if row.get("hypothesis_status") != "TESTED"
            ],
        }
    registry["comparison_families"] = families
    registry["ineligible_comparisons"] = {
        comparison_id: {
            "holm_family_activated": False,
            "untested_hypotheses": sorted(
                rows, key=lambda item: str(item["endpoint"])
            ),
        }
        for comparison_id, rows in sorted(ineligible.items())
    }
    return registry


def _stratified_cell(
    variant_rows: Sequence[Mapping[str, Any]],
    family_rows: Sequence[Mapping[str, Any]],
    endpoint: EndpointDefinition,
    *,
    system: str,
    dimension: str,
    value: str,
    replicates: int,
    base_seed: int,
    seed_registry: dict[str, int],
) -> dict[str, Any]:
    if endpoint.retrieval_only and system == "P1":
        applicable = "NOT_APPLICABLE"
        selected_values: list[float] = []
        selected_family_rows: list[dict[str, Any]] = []
        variant_count = execution_count = 0
        warning_codes = ["P1_HAS_NO_RETRIEVAL_STAGE"]
    elif dimension == "variant_stratum":
        variant_endpoint = (
            "joint_accept_at_1"
            if endpoint.key == "robustness_rate"
            else endpoint.key
        )
        selected_variants = [
            row for row in variant_rows
            if row["system_id"] == system
            and row.get("variant_stratum") == value
            and row["values"].get(variant_endpoint) is not None
            and (
                endpoint.key != "robustness_rate"
                or (
                    row["equivalence_status"] == "reviewed_equivalent"
                    and row["variant_class"] != "canonical_en"
                )
            )
        ]
        by_family: dict[str, list[float]] = defaultdict(list)
        for row in selected_variants:
            by_family[str(row["family_id"])].append(
                float(row["values"][variant_endpoint])
            )
        selected_family_rows = [
            {"family_id": family_id, "value": float(_safe_mean(values))}
            for family_id, values in sorted(by_family.items())
        ]
        selected_values = [row["value"] for row in selected_family_rows]
        variant_count = len(selected_variants)
        execution_count = sum(int(row["repeat_count"]) for row in selected_variants)
        applicable = "AVAILABLE" if selected_values else "NOT_AVAILABLE"
        warning_codes = (
            family_n_warnings(len(selected_values))
            if selected_values
            else ["NO_ELIGIBLE_FAMILIES"]
        )
    else:
        selected_families = [
            row for row in family_rows
            if row["system_id"] == system
            and row["workload_stratum"] == value
            and row["values"].get(endpoint.key) is not None
        ]
        selected_values = [float(row["values"][endpoint.key]) for row in selected_families]
        selected_family_rows = [
            {
                "family_id": str(row["family_id"]),
                "value": float(row["values"][endpoint.key]),
            }
            for row in selected_families
        ]
        variant_count = sum(
            int(row["endpoint_variant_denominators"][endpoint.key]) for row in selected_families
        )
        execution_count = sum(int(row["execution_count"]) for row in selected_families)
        applicable = "AVAILABLE" if selected_values else "NOT_AVAILABLE"
        warning_codes = (
            family_n_warnings(len(selected_values))
            if selected_values
            else ["NO_ELIGIBLE_FAMILIES"]
        )
    namespace = f"stratum|{system}|{endpoint.key}|{dimension}|{value}"
    seed = (
        _seed_for(seed_registry, base_seed=base_seed, namespace=namespace)
        if selected_values
        else None
    )
    low, high = (
        family_bootstrap_ci(
            selected_family_rows,
            "value",
            replicates=replicates,
            seed=seed,
        )
        if seed is not None
        else (None, None)
    )
    return {
        "schema_version": STRATIFIED_ESTIMATE_SCHEMA_VERSION,
        "system_id": system,
        "endpoint": endpoint.key,
        "domain": endpoint.domain,
        **_metric_fields(endpoint),
        "dimension": dimension,
        "value": value,
        "applicability": applicable,
        "family_count": len(selected_values),
        "estimate": _safe_mean(selected_values),
        "ci_level": DEFAULT_CI_LEVEL,
        "ci_low": low,
        "ci_high": high,
        "ci_method": "workload_family_percentile_bootstrap" if low is not None else None,
        "effective_family_n": len(selected_values),
        "variant_count": variant_count,
        "execution_count": execution_count,
        "bootstrap_seed": seed,
        "bootstrap_seed_namespace": namespace if seed is not None else None,
        "warning_codes": warning_codes,
        "hypothesis_tested": False,
        "aggregation_unit": "workload_family_within_stratum",
        "retrieval_provenance": (
            "INHERITED_FROM_P2_INTERNAL_PIPELINE"
            if endpoint.retrieval_only and system == "P3"
            else "NATIVE_P2_RETRIEVAL"
            if endpoint.retrieval_only and system == "P2"
            else "NOT_APPLICABLE"
            if endpoint.retrieval_only
            else None
        ),
        "population_definition": (
            LATENCY_POPULATION if endpoint.key == "latency_seconds" else None
        ),
    }


def analyze_statistical_records(
    gold: GoldSource,
    records: Sequence[Mapping[str, Any]],
    *,
    retrieval_ks: Sequence[int] = DEFAULT_RETRIEVAL_KS,
    bootstrap_replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
    corpus: CandidateCorpus | None = None,
) -> StatisticalAnalysisResult:
    """Derive family-level estimates from already validated offline rows."""

    _require_v2_gold(gold)
    ks = _validate_retrieval_ks(retrieval_ks)
    if isinstance(bootstrap_replicates, bool) or bootstrap_replicates < 1:
        raise StatisticalAnalysisError("bootstrap replicates must be positive")
    if isinstance(bootstrap_seed, bool) or not isinstance(bootstrap_seed, int):
        raise StatisticalAnalysisError("bootstrap seed must be an integer")
    systems = sorted({str(record.get("system_id")) for record in records})
    if not {"P1", "P2"}.issubset(systems):
        raise StatisticalAnalysisError("statistical evidence requires complete P1 and P2 systems")
    if set(systems) - {"P1", "P2", "P3"}:
        raise StatisticalAnalysisError("statistical evidence contains an unsupported system")

    selected_corpus = corpus or load_candidate_corpus()
    if selected_corpus.corpus_checksum != gold.catalog_identity.get("candidate_corpus_sha256"):
        raise StatisticalAnalysisError("live candidate corpus does not match frozen statistical gold")
    split = gold.split
    if split is None:  # Defensive guard; _require_v2_gold already rejects this.
        raise StatisticalAnalysisError("Protocol-v5 statistics require compiled split metadata")
    case_index = {case.case_id: case for case in split.bundle.cases}
    if len(case_index) != len(split.bundle.cases):
        raise StatisticalAnalysisError("statistical gold case IDs are not unique")
    execution_rows: list[dict[str, Any]] = []
    for record in records:
        case_id = str(record.get("case_id"))
        if case_id not in case_index:
            raise StatisticalAnalysisError(f"raw evidence references unknown case {case_id!r}")
        execution_rows.append(
            _score_execution(
                record,
                case_index[case_id],
                corpus=selected_corpus,
                retrieval_ks=ks,
            )
        )
    endpoints = _endpoint_definitions(ks)
    p3_inference = _p3_inference_policy(gold, systems)
    variant_rows = _aggregate_variant_rows(execution_rows, endpoints)
    family_rows = _aggregate_family_rows(variant_rows, endpoints)
    seed_registry: dict[str, int] = {}

    system_estimates = tuple(
        _system_estimate(
            system,
            endpoint,
            family_rows,
            replicates=bootstrap_replicates,
            base_seed=bootstrap_seed,
            seed_registry=seed_registry,
        )
        for system in systems
        for endpoint in endpoints
    )

    comparisons: list[dict[str, Any]] = []
    primary_endpoints = [endpoint for endpoint in endpoints if not endpoint.diagnostic]
    for endpoint in primary_endpoints:
        if endpoint.retrieval_only:
            comparisons.append(
                _noninferential_comparison(
                    family_rows,
                    endpoint,
                    first="P1",
                    second="P2",
                    applicability="NOT_APPLICABLE",
                    reason_code="P1_HAS_NO_RETRIEVAL_STAGE",
                )
            )
        else:
            comparisons.append(
                _paired_comparison(
                    family_rows,
                    endpoint,
                    first="P1",
                    second="P2",
                    replicates=bootstrap_replicates,
                    base_seed=bootstrap_seed,
                    seed_registry=seed_registry,
                )
            )
    if "P3" in systems:
        for endpoint in primary_endpoints:
            if not p3_inference["inference_permitted"]:
                comparisons.append(
                    _noninferential_comparison(
                        family_rows,
                        endpoint,
                        first="P2",
                        second="P3",
                        applicability="NOT_RETAINED",
                        reason_code=str(p3_inference["reason_code"]),
                        extra_warnings=(
                            ("P3_RETRIEVAL_INHERITED_FROM_P2",)
                            if endpoint.retrieval_only
                            else ()
                        ),
                    )
                )
            elif endpoint.retrieval_only:
                comparisons.append(
                    _noninferential_comparison(
                        family_rows,
                        endpoint,
                        first="P2",
                        second="P3",
                        applicability="NOT_APPLICABLE",
                        reason_code="P3_RETRIEVAL_INHERITED_FROM_P2",
                    )
                )
            else:
                comparisons.append(
                    _paired_comparison(
                        family_rows,
                        endpoint,
                        first="P2",
                        second="P3",
                        replicates=bootstrap_replicates,
                        base_seed=bootstrap_seed,
                        seed_registry=seed_registry,
                    )
                )
    _apply_holm(comparisons)

    workload_strata = sorted(
        {
            str(case.family_metadata["workload_stratum"])
            for case in split.bundle.cases
            if case.family_metadata is not None
        }
    )
    stratified: list[dict[str, Any]] = []
    for system in systems:
        for endpoint in endpoints:
            for value in VARIANT_STRATA:
                stratified.append(
                    _stratified_cell(
                        variant_rows,
                        family_rows,
                        endpoint,
                        system=system,
                        dimension="variant_stratum",
                        value=value,
                        replicates=bootstrap_replicates,
                        base_seed=bootstrap_seed,
                        seed_registry=seed_registry,
                    )
                )
            for value in workload_strata:
                stratified.append(
                    _stratified_cell(
                        variant_rows,
                        family_rows,
                        endpoint,
                        system=system,
                        dimension="workload_stratum",
                        value=value,
                        replicates=bootstrap_replicates,
                        base_seed=bootstrap_seed,
                        seed_registry=seed_registry,
                    )
                )

    return StatisticalAnalysisResult(
        family_estimates=tuple(family_rows),
        system_estimates=system_estimates,
        paired_comparisons=tuple(comparisons),
        stratified_estimates=tuple(stratified),
        seed_registry=dict(sorted(seed_registry.items())),
        metric_registry=_metric_registry(endpoints),
        holm_registry=_holm_registry(comparisons),
        p3_inference=p3_inference,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _git_revision() -> str | None:
    try:
        value = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return value or None


def _write_jsonl_exclusive(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
        handle.flush()
        os.fsync(handle.fileno())


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _strict_json_loads(text: str, label: str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise StatisticalAnalysisError(
                    f"{label} contains duplicate field {key!r}"
                )
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise StatisticalAnalysisError(
            f"{label} contains non-finite JSON constant {value}"
        )

    try:
        return json.loads(
            text,
            object_pairs_hook=pairs,
            parse_constant=constant,
        )
    except json.JSONDecodeError as exc:
        raise StatisticalAnalysisError(f"{label} is malformed") from exc


def _strict_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = _strict_json_loads(path.read_text(encoding="utf-8"), label)
    except (OSError, UnicodeDecodeError) as exc:
        raise StatisticalAnalysisError(f"{label} is unreadable or malformed") from exc
    if not isinstance(value, dict):
        raise StatisticalAnalysisError(f"{label} must contain an object")
    return value


def _strict_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise StatisticalAnalysisError(f"{label} is unreadable") from exc
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        if not line:
            raise StatisticalAnalysisError(f"{label} contains a blank line at {index}")
        value = _strict_json_loads(line, f"{label} line {index}")
        if not isinstance(value, dict):
            raise StatisticalAnalysisError(f"{label} row {index} must be an object")
        rows.append(value)
    return rows


def _output_schema(name: str) -> str:
    return {
        "family_estimates": FAMILY_ESTIMATE_SCHEMA_VERSION,
        "system_estimates": SYSTEM_ESTIMATE_SCHEMA_VERSION,
        "paired_comparisons": PAIRED_COMPARISON_SCHEMA_VERSION,
        "stratified_estimates": STRATIFIED_ESTIMATE_SCHEMA_VERSION,
    }[name]


def validate_statistical_package(output_dir: Path) -> dict[str, Any]:
    """Validate schemas, checksums, seeds, and family-level claim guards."""

    manifest = _strict_json_object(output_dir / "analysis-manifest.json", "analysis manifest")
    if manifest.get("schema_version") != STATISTICAL_ANALYSIS_SCHEMA_VERSION:
        raise StatisticalAnalysisError("statistical analysis manifest schema is unsupported")
    status = manifest.get("status")
    files = {path.name for path in output_dir.iterdir()}
    if status == "NOT_EXECUTED":
        if files != {"analysis-manifest.json"}:
            raise StatisticalAnalysisError("NOT_EXECUTED package must contain only its manifest")
        if (
            manifest.get("claims_permitted") is not False
            or manifest.get("outputs") != {}
            or not isinstance(manifest.get("reason_code"), str)
            or not isinstance(manifest.get("reason"), str)
        ):
            raise StatisticalAnalysisError("NOT_EXECUTED manifest is invalid")
        return {
            "schema_version": STATISTICAL_ANALYSIS_SCHEMA_VERSION,
            "status": "PASS",
            "analysis_status": status,
            "outputs_validated": 0,
        }
    if status != "DERIVED_EVIDENCE_COMPLETE":
        raise StatisticalAnalysisError("statistical analysis status is unsupported")
    source = manifest.get("source")
    if (
        not isinstance(source, Mapping)
        or not isinstance(source.get("gold_catalog_identity"), Mapping)
    ):
        raise StatisticalAnalysisError(
            "statistical manifest lacks frozen gold catalog identity"
        )
    required_provenance_mappings = (
        "backend_system_versions",
        "candidate_catalog",
        "offline_frozen_configuration",
        "source_environment_identity",
    )
    if any(
        not isinstance(manifest.get(field), Mapping)
        for field in required_provenance_mappings
    ):
        raise StatisticalAnalysisError(
            "statistical manifest lacks required source provenance"
        )
    metric_registry = manifest.get("metric_registry")
    if not isinstance(metric_registry, Mapping) or not metric_registry:
        raise StatisticalAnalysisError("statistical manifest lacks its metric registry")
    for endpoint, definition in metric_registry.items():
        if (
            not isinstance(endpoint, str)
            or not isinstance(definition, Mapping)
            or definition.get("metric") != endpoint
            or definition.get("effect_definition") != "second_system_minus_first_system"
            or definition.get("direction") not in {"higher_is_better", "lower_is_better"}
            or not isinstance(definition.get("unit"), str)
            or not isinstance(definition.get("higher_is_better"), bool)
            or not isinstance(definition.get("null_value"), (int, float))
            or not isinstance(definition.get("holm_domain"), str)
        ):
            raise StatisticalAnalysisError("statistical metric registry is invalid")
    holm_registry = manifest.get("holm_registry")
    if (
        not isinstance(holm_registry, Mapping)
        or holm_registry.get("registry_version") != HOLM_REGISTRY_VERSION
        or not isinstance(holm_registry.get("comparison_families"), Mapping)
        or not isinstance(holm_registry.get("ineligible_comparisons"), Mapping)
    ):
        raise StatisticalAnalysisError("statistical Holm registry is invalid")
    p3_inference = manifest.get("p3_inference")
    if (
        not isinstance(p3_inference, Mapping)
        or not isinstance(p3_inference.get("evidence_present"), bool)
        or not isinstance(p3_inference.get("inference_permitted"), bool)
    ):
        raise StatisticalAnalysisError("statistical P3 inference provenance is invalid")
    manifest_systems = manifest.get("systems")
    if (
        not isinstance(manifest_systems, list)
        or ("P3" in manifest_systems) != p3_inference["evidence_present"]
        or (
            p3_inference["inference_permitted"]
            and p3_inference.get("gate_status") != P3_RETAINED
        )
    ):
        raise StatisticalAnalysisError(
            "statistical P3 inference provenance disagrees with systems or gate"
        )
    expected_files = {"analysis-manifest.json", *OUTPUT_FILES.values()}
    if files != expected_files:
        raise StatisticalAnalysisError("statistical output file registry is incomplete")
    output_manifest = manifest.get("outputs")
    if not isinstance(output_manifest, Mapping) or set(output_manifest) != set(OUTPUT_FILES):
        raise StatisticalAnalysisError("statistical manifest output registry is invalid")
    parsed: dict[str, list[dict[str, Any]]] = {}
    for name, filename in OUTPUT_FILES.items():
        identity = output_manifest[name]
        path = output_dir / filename
        if (
            not isinstance(identity, Mapping)
            or identity.get("path") != filename
            or identity.get("sha256") != file_sha256(path)
        ):
            raise StatisticalAnalysisError(f"statistical output checksum mismatch for {name}")
        rows = _strict_jsonl(path, name)
        if any(row.get("schema_version") != _output_schema(name) for row in rows):
            raise StatisticalAnalysisError(f"statistical row schema mismatch for {name}")
        if identity.get("row_count") != len(rows):
            raise StatisticalAnalysisError(f"statistical row count mismatch for {name}")
        parsed[name] = rows
    if dict(holm_registry) != _holm_registry(parsed["paired_comparisons"]):
        raise StatisticalAnalysisError(
            "statistical Holm registry disagrees with comparison rows"
        )

    bootstrap = manifest.get("bootstrap")
    if not isinstance(bootstrap, Mapping):
        raise StatisticalAnalysisError("statistical manifest lacks bootstrap provenance")
    if bootstrap.get("seed_derivation") != SEED_DERIVATION_ALGORITHM:
        raise StatisticalAnalysisError("statistical seed derivation algorithm is invalid")
    base_seed = bootstrap.get("base_seed")
    if isinstance(base_seed, bool) or not isinstance(base_seed, int):
        raise StatisticalAnalysisError("statistical base bootstrap seed is invalid")
    seed_registry = bootstrap.get("derived_seeds")
    if not isinstance(seed_registry, Mapping):
        raise StatisticalAnalysisError("statistical manifest lacks derived bootstrap seeds")
    for namespace, seed in seed_registry.items():
        if (
            not isinstance(namespace, str)
            or isinstance(seed, bool)
            or not isinstance(seed, int)
            or seed != derive_bootstrap_seed(base_seed, namespace)
        ):
            raise StatisticalAnalysisError("statistical derived bootstrap seed is invalid")
    referenced_seed_namespaces: set[str] = set()
    for name in ("system_estimates", "paired_comparisons", "stratified_estimates"):
        for row in parsed[name]:
            seed = row.get("bootstrap_seed")
            namespace = row.get("bootstrap_seed_namespace")
            if seed is None:
                if namespace is not None:
                    raise StatisticalAnalysisError(
                        f"{name} contains a seed namespace without a seed"
                    )
                continue
            if not isinstance(namespace, str) or seed_registry.get(namespace) != seed:
                raise StatisticalAnalysisError(f"{name} contains an unregistered bootstrap seed")
            referenced_seed_namespaces.add(namespace)
    if referenced_seed_namespaces != set(seed_registry):
        raise StatisticalAnalysisError("statistical manifest contains unreferenced bootstrap seeds")
    for row in parsed["paired_comparisons"]:
        n = row.get("effective_family_n")
        if not isinstance(n, int) or n < 0:
            raise StatisticalAnalysisError("paired comparison has invalid effective family N")
        if row.get("p_value_raw") is not None:
            effects = row.get("effect_sizes")
            if not isinstance(effects, Mapping) or effects.get("mean_difference") is None:
                raise StatisticalAnalysisError("paired p-value lacks an effect size")
            if row.get("effect_ci_low") is None or row.get("effect_ci_high") is None:
                raise StatisticalAnalysisError("paired p-value lacks an effect confidence interval")
        endpoint = row.get("endpoint")
        definition = metric_registry.get(endpoint)
        if not isinstance(definition, Mapping) or any(
            row.get(field) != definition.get(field)
            for field in (
                "metric",
                "effect_definition",
                "direction",
                "unit",
                "higher_is_better",
                "null_value",
                "holm_domain",
            )
        ):
            raise StatisticalAnalysisError(
                "paired comparison disagrees with the metric registry"
            )
        expected_favored = (
            row.get("second_system")
            if definition.get("higher_is_better") is True
            else row.get("first_system")
        )
        if row.get("positive_effect_favors") != expected_favored:
            raise StatisticalAnalysisError(
                "paired comparison reverses the registered metric direction"
            )
        if row.get("applicability") in {"NOT_RETAINED", "NOT_APPLICABLE"} and any(
            row.get(field) is not None
            for field in (
                "test_method",
                "p_value_raw",
                "p_value_holm",
                "effect_sizes",
                "effect_ci_low",
                "effect_ci_high",
                "bootstrap_seed",
            )
        ):
            raise StatisticalAnalysisError(
                "ineligible paired comparison exposes inferential output"
            )
        if (
            row.get("comparison_id") == "P3_minus_P2"
            and row.get("domain") == "retrieval"
            and row.get("hypothesis_status") == "TESTED"
        ):
            raise StatisticalAnalysisError(
                "P3 inherited retrieval cannot be tested against P2"
            )
        if (
            row.get("comparison_id") == "P3_minus_P2"
            and not p3_inference["inference_permitted"]
            and row.get("hypothesis_status") == "TESTED"
        ):
            raise StatisticalAnalysisError(
                "P3 comparison was tested without frozen retention permission"
            )
        if n < DEFAULT_MINIMUM_CLAIM_FAMILIES and row.get("statistical_decision") not in {
            "WITHHELD_SMALL_N",
            "NOT_COMPUTABLE",
        }:
            raise StatisticalAnalysisError("small-N paired comparison exposes a significance claim")
    family_keys = [
        (row.get("system_id"), row.get("family_id"))
        for row in parsed["family_estimates"]
    ]
    if len(family_keys) != len(set(family_keys)):
        raise StatisticalAnalysisError("family estimates contain duplicate system/family rows")
    return {
        "schema_version": STATISTICAL_ANALYSIS_SCHEMA_VERSION,
        "status": "PASS",
        "analysis_status": status,
        "outputs_validated": len(OUTPUT_FILES),
        "family_rows_validated": len(parsed["family_estimates"]),
        "comparison_rows_validated": len(parsed["paired_comparisons"]),
    }


def write_not_executed(
    output_dir: Path,
    *,
    reason: str,
    reason_code: str = "INPUTS_NOT_SUPPLIED",
) -> Path:
    if not isinstance(reason, str) or not reason.strip():
        raise StatisticalAnalysisError("NOT_EXECUTED reason must be non-blank")
    if not isinstance(reason_code, str) or not reason_code.strip():
        raise StatisticalAnalysisError("NOT_EXECUTED reason code must be non-blank")
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": STATISTICAL_ANALYSIS_SCHEMA_VERSION,
        "statistics_schema_version": STATISTICS_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "status": "NOT_EXECUTED",
        "claims_permitted": False,
        "created_at_utc": _utc_now(),
        "git_revision": _git_revision(),
        "reason_code": reason_code.strip(),
        "reason": reason.strip(),
        "outputs": {},
    }
    _write_json_exclusive(output_dir / "analysis-manifest.json", manifest)
    validate_statistical_package(output_dir)
    return output_dir


def write_analysis_package(
    output_dir: Path,
    *,
    result: StatisticalAnalysisResult,
    gold: GoldSource,
    evidence_dir: Path,
    provenance: Mapping[str, Any],
    retrieval_ks: Sequence[int],
    bootstrap_replicates: int,
    bootstrap_seed: int,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=False)
    output_paths = {name: output_dir / filename for name, filename in OUTPUT_FILES.items()}
    _write_jsonl_exclusive(output_paths["family_estimates"], result.family_estimates)
    _write_jsonl_exclusive(output_paths["system_estimates"], result.system_estimates)
    _write_jsonl_exclusive(output_paths["paired_comparisons"], result.paired_comparisons)
    _write_jsonl_exclusive(output_paths["stratified_estimates"], result.stratified_estimates)

    raw_records = evidence_dir / RAW_DIRECTORY_NAME / RECORDS_FILENAME
    raw_provenance = evidence_dir / RAW_DIRECTORY_NAME / PROVENANCE_FILENAME
    raw_completion = evidence_dir / REPORT_DIRECTORY_NAME / COMPLETION_FILENAME
    split_stage = gold.role
    manifest = {
        "schema_version": STATISTICAL_ANALYSIS_SCHEMA_VERSION,
        "statistics_schema_version": STATISTICS_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "status": "DERIVED_EVIDENCE_COMPLETE",
        "claims_permitted": split_stage == "confirmatory",
        "claim_scope": (
            "frozen_confirmatory_family_level_analysis"
            if split_stage == "confirmatory"
            else "development_formative_only"
        ),
        "created_at_utc": _utc_now(),
        "git_revision": _git_revision(),
        "source": {
            "offline_run_id": provenance.get("run_id"),
            "offline_provenance_fingerprint": provenance.get("provenance_fingerprint"),
            "offline_provenance_sha256": file_sha256(raw_provenance),
            "offline_recommendations_sha256": file_sha256(raw_records),
            "offline_completion_sha256": file_sha256(raw_completion),
            "offline_split_identity": provenance.get("split"),
            "dataset_id": gold.dataset_id,
            "dataset_schema_version": gold.schema_version,
            "dataset_source_file_sha256": gold.source_file_sha256,
            "dataset_canonical_sha256": gold.canonical_sha256,
            "gold_catalog_identity": dict(gold.catalog_identity),
            "split_role": split_stage,
            "split_id": gold.split.manifest.split_id if gold.split is not None else None,
            "split_checksum": (
                gold.split.manifest.checksum if gold.split is not None else None
            ),
            "freeze_identity": gold.freeze_identity,
            "p3_gate_identity": gold.p3_gate_identity,
        },
        "systems": sorted({str(row["system_id"]) for row in result.family_estimates}),
        "source_systems": list(provenance.get("systems", [])),
        "backend_system_versions": provenance.get("system_frozen_provenance", {}),
        "candidate_catalog": provenance.get("candidate_catalog"),
        "offline_frozen_configuration": provenance.get("frozen_configuration", {}),
        "source_environment_identity": provenance.get("environment_identity"),
        "evidence_validation": {
            "raw_completion_verified_before_external_gold_load": True,
            "full_split_bound_offline_validation": "PASS",
            "analysis_reads_gold_downstream_only": True,
        },
        "analysis_environment_identity": {
            "python_version": platform.python_version(),
            "platform": platform.system(),
            "platform_release": platform.release(),
        },
        "aggregation_policy": {
            "independent_unit": "workload_family",
            "repeat_handling": "mean within variant",
            "variant_handling": "mean within family",
            "cross_family_handling": "equal-weight macro mean",
            "accuracy_eligibility": "feasible requests only",
            "robustness_eligibility": "non-canonical reviewed_equivalent variants",
            "repeated_calls_are_accuracy_samples": False,
            "latency_population": LATENCY_POPULATION,
            "latency_includes": [
                "completed_attempts",
                "recorded_fallback_attempts",
                "recorded_error_attempts",
                "recorded_timeout_attempts",
            ],
            "latency_missing_duration_policy": (
                "full validated packages reject missing durations; direct in-memory rows are unavailable"
            ),
        },
        "bootstrap": {
            "method": "workload_family_percentile_bootstrap",
            "ci_level": DEFAULT_CI_LEVEL,
            "replicates": bootstrap_replicates,
            "base_seed": bootstrap_seed,
            "seed_derivation": SEED_DERIVATION_ALGORITHM,
            "derived_seeds": dict(result.seed_registry),
        },
        "testing_policy": {
            "alpha": ALPHA,
            "two_sided": True,
            "binary_family_outcomes": "exact_mcnemar",
            "fractional_or_continuous_family_outcomes": "paired_wilcoxon_signed_rank",
            "small_n_warning_below": DEFAULT_SMALL_N_WARNING_FAMILIES,
            "significance_withheld_below": DEFAULT_MINIMUM_CLAIM_FAMILIES,
            "stratified_hypothesis_tests": False,
        },
        "metric_registry": dict(result.metric_registry),
        "holm_registry": dict(result.holm_registry),
        "p3_inference": dict(result.p3_inference),
        "configuration": {
            "primary_comparison": "P2_minus_P1",
            "optional_comparison": "P3_minus_P2",
            "retrieval_ks": list(retrieval_ks),
            "variant_strata": list(VARIANT_STRATA),
            "workload_strata": sorted(
                {
                    str(row["value"])
                    for row in result.stratified_estimates
                    if row["dimension"] == "workload_stratum"
                }
            ),
        },
        "outputs": {
            name: {
                "path": path.name,
                "sha256": file_sha256(path),
                "row_count": len(getattr(result, name)),
            }
            for name, path in output_paths.items()
        },
        "raw_evidence_unchanged": True,
        "backend_changed": False,
    }
    _write_json_exclusive(output_dir / "analysis-manifest.json", manifest)
    validate_statistical_package(output_dir)
    return output_dir


def analyze_statistical_evidence(
    evidence_dir: Path,
    gold_path: Path,
    output_dir: Path,
    *,
    role: str = "development",
    freeze_path: Path | None = None,
    split_id: str | None = None,
    retrieval_ks: Sequence[int] = DEFAULT_RETRIEVAL_KS,
    bootstrap_replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> Path:
    """Validate real inputs, derive statistics, and create an immutable package."""

    try:
        _prevalidate_completed_evidence_envelope(evidence_dir)
        gold = load_component_gold(
            gold_path,
            role=role,
            freeze_path=freeze_path,
            split_id=split_id,
        )
        _require_v2_gold(gold)
        provenance, records = load_validated_evidence(
            evidence_dir,
            gold,
            systems=("P1", "P2", "P3"),
            require_systems=False,
        )
        systems = {str(record.get("system_id")) for record in records}
        if not {"P1", "P2"}.issubset(systems):
            raise StatisticalAnalysisError("validated evidence does not contain both P1 and P2")
        result = analyze_statistical_records(
            gold,
            records,
            retrieval_ks=retrieval_ks,
            bootstrap_replicates=bootstrap_replicates,
            bootstrap_seed=bootstrap_seed,
        )
    except (
        ComponentAnalysisError,
        ContractValidationError,
        GoldDatasetValidationError,
        OfflineEvidenceValidationError,
        SplitBundleValidationError,
        StatisticalAnalysisError,
        OSError,
        ValueError,
    ) as exc:
        return write_not_executed(
            output_dir,
            reason=f"Statistical inputs unavailable or invalid: {exc}",
            reason_code="INPUTS_UNAVAILABLE_OR_INVALID",
        )
    return write_analysis_package(
        output_dir,
        result=result,
        gold=gold,
        evidence_dir=evidence_dir,
        provenance=provenance,
        retrieval_ks=_validate_retrieval_ks(retrieval_ks),
        bootstrap_replicates=bootstrap_replicates,
        bootstrap_seed=bootstrap_seed,
    )


def _parse_ks(value: str) -> tuple[int, ...]:
    try:
        selected = tuple(sorted({int(item.strip()) for item in value.split(",")}))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("retrieval K must be comma-separated integers") from exc
    if not selected or any(item < 1 for item in selected):
        raise argparse.ArgumentTypeError("retrieval K values must be positive")
    return selected


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--gold-dataset", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--role", choices=("development", "confirmatory"), default="development")
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--split-id")
    parser.add_argument("--retrieval-k", type=_parse_ks, default=DEFAULT_RETRIEVAL_KS)
    parser.add_argument("--bootstrap-replicates", type=int, default=DEFAULT_BOOTSTRAP_REPLICATES)
    parser.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument(
        "--not-executed-reason",
        default="Complete validated Protocol-v5 offline evidence and v2 gold were not supplied.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.status_only:
            if args.evidence_dir is not None or args.gold_dataset is not None:
                raise StatisticalAnalysisError(
                    "--status-only cannot be combined with evidence or gold inputs"
                )
            output = write_not_executed(
                args.output_dir,
                reason=args.not_executed_reason,
            )
        elif args.evidence_dir is None or args.gold_dataset is None:
            output = write_not_executed(
                args.output_dir,
                reason="Complete validated Protocol-v5 offline evidence and v2 gold were not both supplied.",
                reason_code="INPUTS_NOT_SUPPLIED",
            )
        else:
            output = analyze_statistical_evidence(
                args.evidence_dir,
                args.gold_dataset,
                args.output_dir,
                role=args.role,
                freeze_path=args.freeze,
                split_id=args.split_id,
                retrieval_ks=args.retrieval_k,
                bootstrap_replicates=args.bootstrap_replicates,
                bootstrap_seed=args.bootstrap_seed,
            )
        status = _strict_json_object(
            output / "analysis-manifest.json", "analysis manifest"
        )["status"]
        print(json.dumps({"status": status, "output_dir": str(output)}, sort_keys=True))
        return 0
    except (
        ComponentAnalysisError,
        ContractValidationError,
        GoldDatasetValidationError,
        OfflineEvidenceValidationError,
        SplitBundleValidationError,
        StatisticalAnalysisError,
        OSError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {
                    "schema_version": STATISTICAL_ANALYSIS_SCHEMA_VERSION,
                    "status": "ERROR",
                    "claims_permitted": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "FAMILY_ESTIMATE_SCHEMA_VERSION",
    "PAIRED_COMPARISON_SCHEMA_VERSION",
    "STATISTICAL_ANALYSIS_SCHEMA_VERSION",
    "STRATIFIED_ESTIMATE_SCHEMA_VERSION",
    "SYSTEM_ESTIMATE_SCHEMA_VERSION",
    "StatisticalAnalysisError",
    "StatisticalAnalysisResult",
    "analyze_statistical_evidence",
    "analyze_statistical_records",
    "main",
    "validate_statistical_package",
    "write_analysis_package",
    "write_not_executed",
]
