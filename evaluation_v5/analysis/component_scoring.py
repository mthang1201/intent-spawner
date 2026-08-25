"""Protocol-v5 component scoring and earliest-failure analysis for P2/P3.

The analyzer is intentionally downstream of the append-only offline runner.
It never calls a recommender and never passes gold labels to backend code.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
from typing import Any

import yaml

from evaluation_v4.dataset import canonical_sha256, file_sha256
from evaluation_v5.gold_dataset import (
    GOLD_DATASET_SCHEMA_VERSION,
    GoldDatasetValidationError,
    candidate_satisfies_gold,
    compile_gold_dataset,
    load_gold_dataset,
)
from evaluation_v5.isolation import load_confirmatory_split
from evaluation_v5.offline.runner import (
    PROVENANCE_FILENAME,
    RAW_DIRECTORY_NAME,
    RECORDS_FILENAME,
    _sha256,
)
from evaluation_v5.offline.validate_evidence import (
    OfflineEvidenceValidationError,
    validate_offline_evidence,
)
from evaluation_v5.split_dataset import (
    SPLIT_BUNDLE_SCHEMA_VERSION_V2,
    LoadedSplit,
    SplitBundleValidationError,
    SplitRole,
    _read_split_bundle,
)
from recommender.candidate_corpus import CandidateCorpus, load_candidate_corpus
from recommender.models import ContractValidationError, StructuredIntent


COMPONENT_ANALYSIS_SCHEMA_VERSION = "protocol-v5-component-analysis-v1.0.0"
COMPONENT_AGGREGATES_SCHEMA_VERSION = "protocol-v5-component-aggregates-v1.0.0"
PER_RECOMMENDATION_SCHEMA_VERSION = "protocol-v5-component-recommendation-v1.0.0"
PER_FAMILY_SCHEMA_VERSION = "protocol-v5-component-family-v1.0.0"
P3_HEADROOM_SCHEMA_VERSION = "protocol-v5-p3-headroom-gate-v1.0.0"
PROTOCOL_VERSION = "5.0.0"

_NOT_EXECUTED_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "protocol_version",
        "status",
        "claims_permitted",
        "created_at_utc",
        "git_revision",
        "reason_code",
        "reason",
        "p3_headroom_gate_status",
        "outputs",
    }
)

PRIMARY_CATEGORIES = (
    "EXTRACTION_ERROR",
    "RETRIEVAL_MISS",
    "CONSTRAINT_ERROR",
    "RANKING_ERROR",
    "UNSUPPORTED_CATALOG",
    "PROVIDER_FAILURE",
    "OTHER",
)
_CATEGORY_ORDER = {
    "UNSUPPORTED_CATALOG": 0,
    "PROVIDER_FAILURE": 1,
    "EXTRACTION_ERROR": 2,
    "RETRIEVAL_MISS": 3,
    "CONSTRAINT_ERROR": 4,
    "RANKING_ERROR": 5,
    "OTHER": 6,
}


class ComponentAnalysisError(RuntimeError):
    """Raw evidence or gold data is insufficient or inconsistent."""


@dataclass(frozen=True, slots=True)
class GoldCase:
    case_id: str
    family_id: str
    variant_id: str
    language: str
    prompt: str
    dataset_size_gb: int | float | None
    code_context_hints: tuple[str, ...]
    gold_structured_intent: Mapping[str, Any]
    candidate_gold: Mapping[str, Any]
    image_gold: Mapping[str, Any]
    policy_gold: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class GoldSource:
    role: str
    dataset_id: str
    schema_version: str
    source_file_sha256: str
    canonical_sha256: str
    catalog_identity: Mapping[str, Any]
    cases: tuple[GoldCase, ...]
    split: LoadedSplit | None = None
    freeze_identity: Mapping[str, Any] | None = None
    p3_gate_identity: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    aggregates: Mapping[str, Any]
    recommendations: tuple[Mapping[str, Any], ...]
    families: tuple[Mapping[str, Any], ...]
    p3_headroom: Mapping[str, Any]


def _safe_rate(numerator: int | float, denominator: int | float) -> float | None:
    return float(numerator) / float(denominator) if denominator else None


def _mean(values: Sequence[float | int | bool | None]) -> float | None:
    selected = [float(item) for item in values if item is not None]
    return statistics.fmean(selected) if selected else None


def _set_metrics(gold: set[str], predicted: set[str]) -> dict[str, Any]:
    tp = len(gold & predicted)
    fp = len(predicted - gold)
    fn = len(gold - predicted)
    precision = _safe_rate(tp, tp + fp)
    recall = _safe_rate(tp, tp + fn)
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if precision is not None
        and recall is not None
        and precision + recall > 0
        else None
    )
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "exact_match": gold == predicted,
        "gold": sorted(gold),
        "predicted": sorted(predicted),
    }


def _strict_numeric_equal(gold: object, predicted: object) -> bool:
    if gold is None or predicted is None:
        return gold is None and predicted is None
    if (
        isinstance(gold, bool)
        or isinstance(predicted, bool)
        or not isinstance(gold, (int, float))
        or not isinstance(predicted, (int, float))
    ):
        return False
    return float(gold) == float(predicted)


def _numeric_diagnostic(gold: object, predicted: object) -> dict[str, Any]:
    if gold is None and predicted is None:
        outcome = "correct_absent"
    elif gold is None:
        outcome = "spurious"
    elif predicted is None:
        outcome = "omitted"
    elif _strict_numeric_equal(gold, predicted):
        outcome = "correct_value"
    else:
        outcome = "value_mismatch"
    return {
        "gold": gold,
        "predicted": predicted,
        "exact": outcome in {"correct_absent", "correct_value"},
        "outcome": outcome,
    }


def _binary_diagnostic(actual: bool, predicted: bool) -> dict[str, Any]:
    if actual and predicted:
        outcome = "true_positive"
    elif actual:
        outcome = "false_negative"
    elif predicted:
        outcome = "false_positive"
    else:
        outcome = "true_negative"
    return {
        "actual": actual,
        "predicted": predicted,
        "correct": actual == predicted,
        "outcome": outcome,
    }


def _ndcg(ranked: Sequence[str], acceptable: set[str], k: int) -> float | None:
    if not acceptable:
        return None
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, candidate_id in enumerate(ranked[:k], start=1)
        if candidate_id in acceptable
    )
    ideal = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, min(k, len(acceptable)) + 1)
    )
    return dcg / ideal if ideal else None


def _reciprocal_rank(ranked: Sequence[str], acceptable: set[str]) -> float | None:
    if not acceptable:
        return None
    for rank, candidate_id in enumerate(ranked, start=1):
        if candidate_id in acceptable:
            return 1.0 / rank
    return 0.0


def _gold_cases_from_family(path: Path) -> GoldSource:
    loaded = load_gold_dataset(path)
    dataset = loaded.dataset
    metadata = dataset.dataset_metadata
    if metadata["role"] != "development":
        raise ComponentAnalysisError(
            "family-authored gold is accepted only for development analysis"
        )
    if metadata["lifecycle"] != "frozen":
        raise ComponentAnalysisError("component scoring requires frozen gold labels")
    if any(family.label_review["status"] != "approved" for family in dataset.families):
        raise ComponentAnalysisError("component scoring requires approved family labels")
    compiled = compile_gold_dataset(loaded)
    cases = tuple(
        GoldCase(
            case_id=variant.variant_id,
            family_id=family.family_id,
            variant_id=variant.variant_id,
            language=variant.language,
            prompt=variant.intent,
            dataset_size_gb=family.gold_structured_intent["dataset_size_gb"],
            code_context_hints=variant.code_context,
            gold_structured_intent=dict(family.gold_structured_intent),
            candidate_gold=dict(family.candidate_gold),
            image_gold=dict(family.image_gold),
            policy_gold=dict(family.policy_gold),
        )
        for family in dataset.families
        for variant in family.variants
    )
    return GoldSource(
        role="development",
        dataset_id=str(metadata["dataset_id"]),
        schema_version=dataset.schema_version,
        source_file_sha256=loaded.source_file_sha256,
        canonical_sha256=loaded.source_canonical_sha256,
        catalog_identity=dict(dataset.catalog_identity),
        cases=cases,
        # Prompt-5 executes the compiled projection, whose byte-level source
        # checksum depends on its JSON/YAML serialization.  The semantic bundle
        # checksum is fixed here; load_component_evidence binds the source byte
        # checksum from the fingerprint-validated raw provenance.
        split=LoadedSplit(
            bundle=compiled,
            source_file_sha256=loaded.source_file_sha256,
        ),
    )


def _catalog_identity_from_split(split: LoadedSplit) -> dict[str, Any]:
    identities = {
        canonical_sha256(case.source_provenance["catalog_identity"]): dict(
            case.source_provenance["catalog_identity"]
        )
        for case in split.bundle.cases
    }
    if len(identities) != 1:
        raise ComponentAnalysisError("compiled gold cases disagree on catalog identity")
    return next(iter(identities.values()))


def _gold_cases_from_split(
    split: LoadedSplit,
    *,
    freeze_identity: Mapping[str, Any] | None,
    p3_gate_identity: Mapping[str, Any] | None = None,
) -> GoldSource:
    if split.bundle.schema_version != SPLIT_BUNDLE_SCHEMA_VERSION_V2:
        raise ComponentAnalysisError(
            "full component scoring requires Protocol-v5 compiled split v2 gold"
        )
    cases = tuple(
        GoldCase(
            case_id=case.case_id,
            family_id=case.family_id,
            variant_id=case.variant_id,
            language=case.language,
            prompt=case.prompt,
            dataset_size_gb=case.inputs["dataset_size_gb"],
            code_context_hints=tuple(case.inputs["code_context_hints"]),
            gold_structured_intent=dict(case.gold["gold_structured_intent"]),
            candidate_gold=dict(case.gold["candidate_gold"]),
            image_gold=dict(case.gold["image_gold"]),
            policy_gold=dict(case.gold["policy_gold"]),
        )
        for case in split.bundle.cases
    )
    return GoldSource(
        role=split.manifest.role.value,
        dataset_id=split.manifest.dataset_id,
        schema_version=split.bundle.schema_version,
        source_file_sha256=split.source_file_sha256,
        canonical_sha256=split.manifest.checksum,
        catalog_identity=_catalog_identity_from_split(split),
        cases=cases,
        split=split,
        freeze_identity=dict(freeze_identity) if freeze_identity is not None else None,
        p3_gate_identity=(
            dict(p3_gate_identity) if p3_gate_identity is not None else None
        ),
    )


def load_component_gold(
    path: Path,
    *,
    role: str = "development",
    freeze_path: Path | None = None,
    split_id: str | None = None,
) -> GoldSource:
    """Load complete component gold without weakening confirmatory isolation."""

    if role not in {"development", "confirmatory"}:
        raise ComponentAnalysisError("role must be development or confirmatory")
    if role == "confirmatory":
        if freeze_path is None:
            raise ComponentAnalysisError(
                "confirmatory component scoring requires an authoritative freeze"
            )
        loaded = load_confirmatory_split(
            path,
            freeze_path,
            expected_split_id=split_id or "v5-confirmatory",
        )
        freeze_identity = {
            "freeze_id": loaded.freeze_manifest["freeze_id"],
            "freeze_manifest_sha256": file_sha256(freeze_path),
            "frozen_at_utc": loaded.freeze_manifest["created_at_utc"],
            "frozen_by": "authoritative_protocol_v5_freeze",
            "source": "confirmatory_freeze_manifest",
        }
        gate = loaded.freeze_manifest["configuration_snapshot"]["p3_gate"]
        p3_gate_identity = {
            "status": gate["status"],
            "p3_active": gate["p3_active"],
            "snapshot_version": gate["snapshot_version"],
            "evidence_sha256": gate["evidence_sha256"],
            "source": "authoritative_protocol_v5_freeze",
        }
        return _gold_cases_from_split(
            loaded.split,
            freeze_identity=freeze_identity,
            p3_gate_identity=p3_gate_identity,
        )
    if freeze_path is not None:
        raise ComponentAnalysisError("development scoring prohibits --freeze")

    try:
        raw = path.read_bytes()
        document = yaml.safe_load(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ComponentAnalysisError("gold dataset could not be read") from exc
    if not isinstance(document, Mapping):
        raise ComponentAnalysisError("gold dataset must contain an object")
    schema_version = document.get("schema_version")
    if schema_version == GOLD_DATASET_SCHEMA_VERSION:
        return _gold_cases_from_family(path)
    if schema_version != SPLIT_BUNDLE_SCHEMA_VERSION_V2:
        raise ComponentAnalysisError(
            "complete gold must be a frozen family dataset or compiled split v2"
        )
    selected_split_id = split_id
    manifest = document.get("split_manifest")
    if selected_split_id is None and isinstance(manifest, Mapping):
        selected_split_id = str(manifest.get("split_id") or "")
    if not selected_split_id:
        raise ComponentAnalysisError("compiled development gold lacks a split ID")
    split = _read_split_bundle(
        path,
        expected_role=SplitRole.DEVELOPMENT,
        expected_split_id=selected_split_id,
    )
    return _gold_cases_from_split(split, freeze_identity=None)


def _strict_json_lines(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ComponentAnalysisError("raw recommendation evidence is unreadable") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise ComponentAnalysisError(
                f"raw recommendation evidence has a blank line at {line_number}"
            )
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ComponentAnalysisError(
                f"raw recommendation evidence is malformed at line {line_number}"
            ) from exc
        if not isinstance(item, dict):
            raise ComponentAnalysisError("raw recommendation row must be an object")
        records.append(item)
    return records


def load_validated_evidence(
    evidence_dir: Path,
    gold: GoldSource,
    *,
    systems: Sequence[str] | None = None,
    require_systems: bool = True,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Validate complete Prompt-5 evidence and retain selected system rows.

    ``systems=None`` retains every participating system from the authenticated
    provenance.  An explicit system selection is restricted to P1/P2/P3 and,
    by default, every requested system must be present.  Setting
    ``require_systems=False`` permits callers to select the available subset;
    it never weakens validation of the complete offline evidence package.
    """

    if not isinstance(require_systems, bool):
        raise ComponentAnalysisError("require_systems must be boolean")
    requested_systems: tuple[str, ...] | None
    if systems is None:
        requested_systems = None
    else:
        if isinstance(systems, (str, bytes)):
            raise ComponentAnalysisError("systems must be a sequence of system IDs")
        requested_systems = tuple(systems)
        if not requested_systems:
            raise ComponentAnalysisError("systems must not be empty")
        if any(not isinstance(system, str) for system in requested_systems):
            raise ComponentAnalysisError("systems must contain only system IDs")
        unsupported = sorted(set(requested_systems) - {"P1", "P2", "P3"})
        if unsupported:
            raise ComponentAnalysisError(
                "unsupported evidence system(s): " + ", ".join(unsupported)
            )
        if len(requested_systems) != len(set(requested_systems)):
            raise ComponentAnalysisError("systems must not contain duplicates")

    provenance_path = evidence_dir / RAW_DIRECTORY_NAME / PROVENANCE_FILENAME
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ComponentAnalysisError("offline provenance is unreadable") from exc
    if not isinstance(provenance, dict):
        raise ComponentAnalysisError("offline provenance must be an object")
    validation_split = gold.split
    if gold.schema_version == GOLD_DATASET_SCHEMA_VERSION:
        raw_split = provenance.get("split")
        dataset_sha256 = (
            raw_split.get("dataset_sha256")
            if isinstance(raw_split, Mapping)
            else None
        )
        if (
            validation_split is None
            or not isinstance(dataset_sha256, str)
            or len(dataset_sha256) != 64
            or any(character not in "0123456789abcdef" for character in dataset_sha256)
        ):
            raise ComponentAnalysisError(
                "family gold cannot bind the Prompt-5 compiled split identity"
            )
        validation_split = LoadedSplit(
            bundle=validation_split.bundle,
            source_file_sha256=dataset_sha256,
        )
    validate_offline_evidence(
        evidence_dir,
        split=validation_split,
        freeze_identity=gold.freeze_identity,
    )
    try:
        validated_provenance = json.loads(
            provenance_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ComponentAnalysisError(
            "validated offline provenance is unreadable"
        ) from exc
    if validated_provenance != provenance:
        raise ComponentAnalysisError(
            "offline provenance changed during component validation"
        )
    participating_systems = tuple(provenance["systems"])
    if requested_systems is None:
        selected_systems = participating_systems
    else:
        missing = tuple(
            system
            for system in requested_systems
            if system not in participating_systems
        )
        if missing and require_systems:
            raise ComponentAnalysisError(
                "evidence is missing requested system(s): " + ", ".join(missing)
            )
        selected_systems = tuple(
            system
            for system in requested_systems
            if system in participating_systems
        )

    records_path = evidence_dir / RAW_DIRECTORY_NAME / RECORDS_FILENAME
    all_records = _strict_json_lines(records_path)
    records = tuple(
        record for record in all_records if record.get("system_id") in selected_systems
    )
    if require_systems and selected_systems:
        observed_systems = {record.get("system_id") for record in records}
        missing_rows = tuple(
            system for system in selected_systems if system not in observed_systems
        )
        if missing_rows:
            raise ComponentAnalysisError(
                "evidence has no rows for requested system(s): "
                + ", ".join(missing_rows)
            )
    if records:
        _validate_gold_evidence_join(gold, provenance, records)
    return provenance, records


def load_component_evidence(
    evidence_dir: Path,
    gold: GoldSource,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Validate Prompt-5 evidence before exposing P2/P3 rows to the scorer."""

    provenance, all_records = load_validated_evidence(
        evidence_dir,
        gold,
        systems=None,
        require_systems=False,
    )
    records = tuple(
        record
        for record in all_records
        if record.get("system_id") in {"P2", "P3"}
    )
    if not records:
        raise ComponentAnalysisError("evidence contains no P2 or P3 rows")
    _validate_gold_evidence_join(gold, provenance, records)
    return provenance, records


def _validate_gold_evidence_join(
    gold: GoldSource,
    provenance: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> None:
    cases = {case.case_id: case for case in gold.cases}
    if len(cases) != len(gold.cases):
        raise ComponentAnalysisError("component gold case IDs are not unique")
    evidence_case_ids = {str(record.get("case_id")) for record in records}
    if evidence_case_ids != set(cases):
        raise ComponentAnalysisError(
            "component gold coverage does not exactly match Prompt-5 evidence"
        )
    for record in records:
        case = cases[str(record["case_id"])]
        if (
            record.get("family_id") != case.family_id
            or record.get("variant_id") != case.variant_id
        ):
            raise ComponentAnalysisError("gold family/variant identity mismatch")
        expected_identity = {
            "case_id": case.case_id,
            "family_id": case.family_id,
            "variant_id": case.variant_id,
            "language": case.language,
            "prompt": case.prompt,
            "inputs": {
                "dataset_size_gb": case.dataset_size_gb,
                "code_context_hints": list(case.code_context_hints),
            },
        }
        identity = record.get("input_identity")
        if not isinstance(identity, Mapping):
            raise ComponentAnalysisError("raw evidence lacks input identity")
        if identity.get("prompt_sha256") != hashlib.sha256(
            case.prompt.encode("utf-8")
        ).hexdigest():
            raise ComponentAnalysisError("gold prompt does not match raw evidence")
        if identity.get("case_sha256") != _sha256(expected_identity):
            raise ComponentAnalysisError("gold case inputs do not match raw evidence")

    raw_catalog = provenance.get("candidate_catalog")
    if not isinstance(raw_catalog, Mapping):
        raise ComponentAnalysisError("offline provenance lacks candidate catalog")
    expected = gold.catalog_identity
    comparisons = (
        ("candidate_corpus_sha256", "corpus_sha256"),
        ("candidate_corpus_version", "corpus_version"),
        ("image_catalog_sha256", "catalog_sha256"),
        ("image_catalog_version", "catalog_version"),
    )
    for gold_field, raw_field in comparisons:
        if expected.get(gold_field) != raw_catalog.get(raw_field):
            raise ComponentAnalysisError(
                f"gold and raw evidence disagree on {gold_field}"
            )


def _provider_diagnostic(record: Mapping[str, Any]) -> tuple[bool, int | None, str | None]:
    fallback = record.get("fallback")
    fallback_category = (
        fallback.get("category") if isinstance(fallback, Mapping) else None
    )
    backend = record.get("backend_provenance")
    p3 = backend.get("p3_provenance") if isinstance(backend, Mapping) else None
    if isinstance(p3, Mapping) and p3.get("provider_failure") is True:
        return True, 4, str(p3.get("reranker_degraded_reason") or "p3_provider_failure")

    category_text = str(fallback_category or "").casefold()
    if any(
        token in category_text
        for token in ("provider", "timeout", "deadline", "infrastructure")
    ):
        if record.get("structured_intent") is None:
            stage = 1
        elif not record.get("candidate_top_k"):
            stage = 2
        else:
            stage = 4
        return True, stage, str(fallback_category)

    errors = record.get("errors")
    error_category = errors.get("category") if isinstance(errors, Mapping) else None
    text = str(error_category or "").casefold()
    if any(
        token in text
        for token in ("provider", "timeout", "connection", "oserror", "llm")
    ):
        if record.get("structured_intent") is None:
            stage = 1
        elif not record.get("candidate_top_k"):
            stage = 2
        elif not record.get("constraint_evaluations"):
            stage = 3
        else:
            stage = 4
        return True, stage, str(error_category)
    return False, None, None


def _score_extraction(
    record: Mapping[str, Any],
    case: GoldCase,
) -> tuple[dict[str, Any], bool]:
    observed = record.get("structured_intent")
    if observed is None:
        return {
            "status": "NOT_OBSERVED",
            "schema_valid": None,
            "mismatch_fields": [],
        }, False
    try:
        intent = StructuredIntent.from_dict(observed)
    except (ContractValidationError, TypeError, ValueError):
        return {
            "status": "INVALID",
            "schema_valid": False,
            "mismatch_fields": ["schema"],
        }, True
    normalized = intent.to_dict()
    schema_exact = set(observed) == set(normalized)
    for nested_field in ("resource_constraints", "extraction_provenance"):
        raw_nested = observed.get(nested_field)
        normalized_nested = normalized[nested_field]
        schema_exact = bool(
            schema_exact
            and isinstance(raw_nested, Mapping)
            and set(raw_nested) == set(normalized_nested)
        )
    if not schema_exact:
        return {
            "status": "INVALID",
            "schema_valid": False,
            "mismatch_fields": ["schema"],
        }, True
    gold = case.gold_structured_intent
    predicted_required = set(normalized["required_features"]) | set(
        normalized["required_libraries"]
    )
    predicted_preferred = set(normalized["preferred_features"]) | set(
        normalized["preferred_libraries"]
    )
    required_features = _set_metrics(
        set(gold["required_features"]), predicted_required
    )
    preferred_features = _set_metrics(
        set(gold["preferred_features"]), predicted_preferred
    )
    forbidden_features = _set_metrics(
        set(gold["forbidden_features"]), set(normalized["forbidden_features"])
    )
    required_frameworks = _set_metrics(
        set(gold["required_frameworks"]), set(normalized["required_frameworks"])
    )
    preferred_frameworks = _set_metrics(
        set(gold["preferred_frameworks"]), set(normalized["preferred_frameworks"])
    )
    constraints = normalized["resource_constraints"]
    gpu = {
        "gold": gold["gpu_semantics"],
        "predicted": constraints["gpu_requirement"],
        "exact": gold["gpu_semantics"] == constraints["gpu_requirement"],
    }
    cpu = _numeric_diagnostic(
        gold["minimum_cpu_cores"], constraints["minimum_cpu_cores"]
    )
    memory = _numeric_diagnostic(
        gold["minimum_memory_gb"], constraints["minimum_memory_gb"]
    )
    ambiguity = _binary_diagnostic(
        bool(gold["ambiguities"]), bool(normalized["ambiguities"])
    )
    mismatch_fields: list[str] = []
    for name, metric in (
        ("required_features", required_features),
        ("preferred_features", preferred_features),
        ("forbidden_features", forbidden_features),
        ("required_frameworks", required_frameworks),
        ("preferred_frameworks", preferred_frameworks),
    ):
        if not metric["exact_match"]:
            mismatch_fields.append(name)
    if not gpu["exact"]:
        mismatch_fields.append("gpu_semantics")
    if not cpu["exact"]:
        mismatch_fields.append("minimum_cpu_cores")
    if not memory["exact"]:
        mismatch_fields.append("minimum_memory_gb")
    if not ambiguity["correct"]:
        mismatch_fields.append("ambiguity_detection")
    return {
        "status": "VALID",
        "schema_valid": True,
        "required_features": required_features,
        "preferred_features": preferred_features,
        "forbidden_features": forbidden_features,
        "required_frameworks": required_frameworks,
        "preferred_frameworks": preferred_frameworks,
        "gpu_semantics": gpu,
        "minimum_cpu_cores": cpu,
        "minimum_memory_gb": memory,
        "ambiguity_detection": ambiguity,
        "mismatch_fields": mismatch_fields,
    }, bool(mismatch_fields)


def _score_retrieval(
    record: Mapping[str, Any],
    case: GoldCase,
    ks: Sequence[int],
) -> tuple[dict[str, Any], bool]:
    ranked = [
        str(item.get("candidate_id"))
        for item in record.get("candidate_top_k", [])
        if isinstance(item, Mapping) and item.get("candidate_id")
    ]
    acceptable = set(case.candidate_gold["acceptable_candidate_ids"])
    by_k: dict[str, Any] = {}
    for k in ks:
        found = acceptable & set(ranked[:k])
        by_k[str(k)] = {
            "recall": _safe_rate(len(found), len(acceptable)),
            "hit": bool(found) if acceptable else None,
            "ndcg": _ndcg(ranked, acceptable, k),
        }
    feasible_request = case.policy_gold["expected_feasibility"] == "feasible"
    fallback = record.get("fallback")
    fallback_category = (
        fallback.get("category") if isinstance(fallback, Mapping) else None
    )
    stage_observed = bool(ranked) or bool(
        record.get("status") == "completed"
        and record.get("structured_intent") is not None
        and fallback_category == "retrieval_empty"
    )
    retrieval_miss = bool(
        feasible_request
        and stage_observed
        and not (acceptable & set(ranked))
    )
    return {
        "stage_observed": stage_observed,
        "candidate_top_k_ids": ranked,
        "retrieved_count": len(ranked),
        "metrics_at_k": by_k,
        "mrr": _reciprocal_rank(ranked, acceptable),
        "acceptable_retrieved_ids": sorted(acceptable & set(ranked)),
        "retrieval_miss": retrieval_miss,
    }, retrieval_miss


def _score_constraints(
    record: Mapping[str, Any],
    case: GoldCase,
    corpus: CandidateCorpus,
    retrieval: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    observed = {
        str(item["candidate_id"]): bool(item.get("feasible"))
        for item in record.get("constraint_evaluations", [])
        if isinstance(item, Mapping) and item.get("candidate_id")
    }
    stage_observed = bool(observed) or bool(
        record.get("status") == "completed"
        and isinstance(record.get("constraint_summary"), Mapping)
    )
    retrieved = set(retrieval["candidate_top_k_ids"])
    acceptable_retrieved = set(retrieval["acceptable_retrieved_ids"])
    gold_feasible = {
        candidate.candidate_id
        for candidate in corpus.candidates
        if candidate_satisfies_gold(
            candidate,
            case.gold_structured_intent,
            case.image_gold,
        )
    }
    false_rejected = {
        candidate_id
        for candidate_id in acceptable_retrieved
        if observed.get(candidate_id) is not True
    }
    acceptable_feasible = {
        candidate_id
        for candidate_id in acceptable_retrieved
        if observed.get(candidate_id) is True
    }
    oracle_infeasible_retrieved = retrieved - gold_feasible
    infeasible_survivors = {
        candidate_id
        for candidate_id in oracle_infeasible_retrieved
        if observed.get(candidate_id) is True
    }
    predicted = record.get("predicted_candidate_id")
    expected_feasibility = case.policy_gold["expected_feasibility"]
    summary = record.get("constraint_summary")
    no_feasible = bool(
        summary.get("no_feasible_candidate") if isinstance(summary, Mapping) else False
    )
    fallback = record.get("fallback")
    fallback_category = (
        fallback.get("category") if isinstance(fallback, Mapping) else None
    )
    unsupported_signal = bool(
        summary.get("unsupported_constraints") if isinstance(summary, Mapping) else []
    ) or fallback_category == "unsupported_catalog"
    actual_infeasible = expected_feasibility == "infeasible"
    expected_unsupported = bool(
        case.policy_gold["explicitly_unsupported_requirements"]
    )
    selected_hard_violation = (
        stage_observed
        and expected_feasibility == "feasible"
        and isinstance(predicted, str)
        and predicted not in gold_feasible
    )
    missed_infeasible = stage_observed and actual_infeasible and not no_feasible
    acceptable_all_rejected = bool(acceptable_retrieved) and not acceptable_feasible
    constraint_error = bool(
        selected_hard_violation
        or acceptable_all_rejected
        or missed_infeasible
    )
    return {
        "stage_observed": stage_observed,
        "gold_feasible_candidate_ids": sorted(gold_feasible),
        "observed_feasible_candidate_ids": sorted(
            candidate_id for candidate_id, feasible in observed.items() if feasible
        ),
        "acceptable_feasible_candidate_ids": sorted(acceptable_feasible),
        "acceptable_false_rejected_ids": sorted(false_rejected),
        "acceptable_false_rejection_rate": _safe_rate(
            len(false_rejected), len(acceptable_retrieved)
        ),
        "gold_infeasible_retrieved_ids": sorted(oracle_infeasible_retrieved),
        "infeasible_survivor_ids": sorted(infeasible_survivors),
        "infeasible_candidate_survival_rate": _safe_rate(
            len(infeasible_survivors), len(oracle_infeasible_retrieved)
        ),
        "selected_hard_constraint_violation": selected_hard_violation,
        "infeasible_request_detection": _binary_diagnostic(actual_infeasible, no_feasible),
        "unsupported_constraint_handling": _binary_diagnostic(
            expected_unsupported, unsupported_signal
        ),
        "constraint_error": constraint_error,
    }, constraint_error


def score_recommendation(
    record: Mapping[str, Any],
    case: GoldCase,
    *,
    corpus: CandidateCorpus,
    retrieval_ks: Sequence[int],
) -> dict[str, Any]:
    """Score one already-observed Prompt-5 P2/P3 execution."""

    extraction, extraction_error = _score_extraction(record, case)
    retrieval, retrieval_miss = _score_retrieval(record, case, retrieval_ks)
    constraints, constraint_error = _score_constraints(
        record, case, corpus, retrieval
    )
    predicted = record.get("predicted_candidate_id")
    acceptable = set(case.candidate_gold["acceptable_candidate_ids"])
    expected_feasibility = case.policy_gold["expected_feasibility"]
    final_ranked = [
        str(item.get("candidate_id"))
        for item in record.get("final_ranking", [])
        if isinstance(item, Mapping) and item.get("candidate_id")
    ]
    final_top1 = final_ranked[0] if final_ranked else predicted
    acceptable_feasible = set(constraints["acceptable_feasible_candidate_ids"])
    top1_acceptable = final_top1 in acceptable if acceptable else None
    ranking_signal = bool(
        expected_feasibility == "feasible"
        and acceptable_feasible
        and not top1_acceptable
    )
    provider_failure, provider_stage, provider_reason = _provider_diagnostic(record)
    infeasible_detected = constraints["infeasible_request_detection"]["predicted"]
    if expected_feasibility == "feasible":
        recommendation_failed = predicted not in acceptable
        query_correct = not recommendation_failed
    elif expected_feasibility == "infeasible":
        recommendation_failed = True
        query_correct = bool(infeasible_detected)
    else:
        recommendation_failed = not bool(acceptable and predicted in acceptable)
        query_correct = not recommendation_failed

    secondary_tags: list[str] = []
    if extraction_error:
        secondary_tags.append("EXTRACTION_MISMATCH")
    if retrieval_miss:
        secondary_tags.append("NO_ACCEPTABLE_IN_RETRIEVED_TOP_K")
    if constraints["acceptable_false_rejected_ids"]:
        secondary_tags.append("ACCEPTABLE_CANDIDATE_FALSE_REJECTION")
    if constraints["infeasible_survivor_ids"]:
        secondary_tags.append("INFEASIBLE_CANDIDATE_SURVIVED")
    if ranking_signal:
        secondary_tags.append("ACCEPTABLE_FEASIBLE_NOT_TOP1")
    if provider_failure:
        secondary_tags.append("PROVIDER_DEGRADED")
    if constraints["unsupported_constraint_handling"]["actual"]:
        secondary_tags.append("GOLD_UNSUPPORTED_REQUIREMENT")

    primary: str | None = None
    primary_stage: str | None = None
    if recommendation_failed:
        if expected_feasibility == "infeasible" and infeasible_detected:
            primary = "UNSUPPORTED_CATALOG"
            primary_stage = "catalog"
        elif provider_failure and provider_stage is not None and provider_stage <= 1:
            primary = "PROVIDER_FAILURE"
            primary_stage = "provider_or_extraction"
        elif extraction_error:
            primary = "EXTRACTION_ERROR"
            primary_stage = "extraction"
        elif provider_failure and provider_stage == 2:
            primary = "PROVIDER_FAILURE"
            primary_stage = "retrieval_provider"
        elif retrieval_miss:
            primary = "RETRIEVAL_MISS"
            primary_stage = "retrieval"
        elif provider_failure and provider_stage == 3:
            primary = "PROVIDER_FAILURE"
            primary_stage = "constraint_provider"
        elif constraint_error:
            primary = "CONSTRAINT_ERROR"
            primary_stage = "constraints"
        elif provider_failure and provider_stage == 4:
            primary = "PROVIDER_FAILURE"
            primary_stage = "ranking_provider"
        elif ranking_signal:
            primary = "RANKING_ERROR"
            primary_stage = "ranking"
        elif provider_failure:
            primary = "PROVIDER_FAILURE"
            primary_stage = "provider_or_reranking"
        else:
            primary = "OTHER"
            primary_stage = "unresolved"

    if primary is not None and primary not in PRIMARY_CATEGORIES:
        raise AssertionError("unsupported primary error category")
    return {
        "schema_version": PER_RECOMMENDATION_SCHEMA_VERSION,
        "record_id": record.get("record_id"),
        "run_id": record.get("run_id"),
        "case_id": case.case_id,
        "family_id": case.family_id,
        "variant_id": case.variant_id,
        "system_id": record.get("system_id"),
        "repeat_index": record.get("repeat_index"),
        "seed": record.get("seed"),
        "expected_feasibility": expected_feasibility,
        "predicted_candidate_id": predicted,
        "extraction": extraction,
        "retrieval": retrieval,
        "constraints": constraints,
        "ranking": {
            "final_ranking_candidate_ids": final_ranked,
            "final_top1_candidate_id": final_top1,
            "top1_acceptable": top1_acceptable,
            "ranking_error": primary == "RANKING_ERROR",
            "ranking_error_signal": ranking_signal,
        },
        "provider": {
            "failure": provider_failure,
            "stage_index": provider_stage,
            "reason": provider_reason,
        },
        "end_to_end": {
            "recommendation_failed": recommendation_failed,
            "query_correct": query_correct,
            "primary_category": primary,
            "primary_stage": primary_stage,
            "secondary_tags": sorted(set(secondary_tags)),
        },
    }


def _validate_primary_attribution(row: Mapping[str, Any]) -> None:
    """Enforce the exclusive P2/P3 end-to-end attribution contract."""

    if row.get("system_id") not in {"P2", "P3"}:
        return
    end_to_end = row.get("end_to_end")
    if not isinstance(end_to_end, Mapping):
        raise ComponentAnalysisError(
            "a P2/P3 recommendation lacks end-to-end attribution"
        )
    failed = end_to_end.get("recommendation_failed")
    primary = end_to_end.get("primary_category")
    secondary = end_to_end.get("secondary_tags")
    if not isinstance(failed, bool):
        raise ComponentAnalysisError(
            "a P2/P3 recommendation has an invalid failure indicator"
        )
    if failed and primary not in PRIMARY_CATEGORIES:
        raise ComponentAnalysisError(
            "a failed P2/P3 recommendation lacks exactly one valid primary category"
        )
    if not failed and primary is not None:
        raise ComponentAnalysisError(
            "a successful P2/P3 recommendation must not have a primary category"
        )
    if not isinstance(secondary, list) or not all(
        isinstance(tag, str) for tag in secondary
    ):
        raise ComponentAnalysisError(
            "a P2/P3 recommendation has invalid secondary diagnostics"
        )
    if primary is not None and primary in secondary:
        raise ComponentAnalysisError(
            "a secondary diagnostic duplicates the primary category"
        )


def _hierarchical_mean(
    rows: Sequence[Mapping[str, Any]],
    getter: Callable[[Mapping[str, Any]], float | int | bool | None],
) -> dict[str, Any]:
    variants: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        value = getter(row)
        if value is not None:
            variants[(str(row["family_id"]), str(row["variant_id"]))].append(
                float(value)
            )
    families: dict[str, list[float]] = defaultdict(list)
    for (family_id, _), values in variants.items():
        families[family_id].append(statistics.fmean(values))
    family_values = [statistics.fmean(values) for values in families.values()]
    return {
        "value": statistics.fmean(family_values) if family_values else None,
        "family_denominator": len(family_values),
        "variant_denominator": len(variants),
    }


def _weighted_binary(
    rows: Sequence[Mapping[str, Any]],
    actual: Callable[[Mapping[str, Any]], bool],
    predicted: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    by_family_variant: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    family_variants: Counter[str] = Counter()
    for row in rows:
        by_family_variant[(str(row["family_id"]), str(row["variant_id"]))].append(row)
    for family_id, _ in by_family_variant:
        family_variants[family_id] += 1
    counts = {
        name: 0.0
        for name in (
            "true_positive",
            "false_positive",
            "true_negative",
            "false_negative",
        )
    }
    for (family_id, _), selected in by_family_variant.items():
        weight = 1.0 / family_variants[family_id] / len(selected)
        for row in selected:
            observed = actual(row)
            signal = predicted(row)
            if observed and signal:
                counts["true_positive"] += weight
            elif observed:
                counts["false_negative"] += weight
            elif signal:
                counts["false_positive"] += weight
            else:
                counts["true_negative"] += weight
    tp = counts["true_positive"]
    fp = counts["false_positive"]
    tn = counts["true_negative"]
    fn = counts["false_negative"]
    precision = _safe_rate(tp, tp + fp)
    recall = _safe_rate(tp, tp + fn)
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if precision is not None
        and recall is not None
        and precision + recall > 0
        else None
    )
    return {
        **counts,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": _safe_rate(tp + tn, tp + fp + tn + fn),
        "unit": "workload_family_weighted",
    }


def _weighted_counts(
    rows: Sequence[Mapping[str, Any]],
    getter: Callable[[Mapping[str, Any]], str | None],
) -> dict[str, Any]:
    by_family_variant: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    family_variants: Counter[str] = Counter()
    for row in rows:
        by_family_variant[(str(row["family_id"]), str(row["variant_id"]))].append(row)
    for family_id, _ in by_family_variant:
        family_variants[family_id] += 1
    counts: Counter[str] = Counter()
    for (family_id, _), selected in by_family_variant.items():
        weight = 1.0 / family_variants[family_id] / len(selected)
        for row in selected:
            value = getter(row)
            if value is not None:
                counts[value] += weight
    return {
        "counts": dict(sorted(counts.items())),
        "unit": "workload_family_weighted",
    }


def _system_aggregates(
    rows: Sequence[Mapping[str, Any]],
    ks: Sequence[int],
) -> dict[str, Any]:
    set_fields = (
        "required_features",
        "preferred_features",
        "forbidden_features",
        "required_frameworks",
        "preferred_frameworks",
    )
    extraction_sets: dict[str, Any] = {}
    for field in set_fields:
        extraction_sets[field] = {
            metric: _hierarchical_mean(
                rows,
                lambda row, field=field, metric=metric: (
                    row["extraction"].get(field, {}).get(metric)
                    if row["extraction"]["status"] == "VALID"
                    else None
                ),
            )
            for metric in ("precision", "recall", "f1", "exact_match")
        }
    extraction = {
        "schema_validity": _hierarchical_mean(
            rows, lambda row: row["extraction"]["schema_valid"]
        ),
        **extraction_sets,
        "gpu_semantics_accuracy": _hierarchical_mean(
            rows,
            lambda row: row["extraction"].get("gpu_semantics", {}).get("exact"),
        ),
        "gpu_semantics_confusion": _weighted_counts(
            [row for row in rows if row["extraction"]["status"] == "VALID"],
            lambda row: (
                f"{row['extraction']['gpu_semantics']['gold']}->"
                f"{row['extraction']['gpu_semantics']['predicted']}"
            ),
        ),
        "minimum_cpu_cores_accuracy": _hierarchical_mean(
            rows,
            lambda row: row["extraction"].get("minimum_cpu_cores", {}).get("exact"),
        ),
        "minimum_cpu_cores_outcomes": _weighted_counts(
            [row for row in rows if row["extraction"]["status"] == "VALID"],
            lambda row: row["extraction"]["minimum_cpu_cores"]["outcome"],
        ),
        "minimum_memory_gb_accuracy": _hierarchical_mean(
            rows,
            lambda row: row["extraction"].get("minimum_memory_gb", {}).get("exact"),
        ),
        "minimum_memory_gb_outcomes": _weighted_counts(
            [row for row in rows if row["extraction"]["status"] == "VALID"],
            lambda row: row["extraction"]["minimum_memory_gb"]["outcome"],
        ),
        "ambiguity_detection": _weighted_binary(
            [row for row in rows if row["extraction"]["status"] == "VALID"],
            lambda row: bool(row["extraction"]["ambiguity_detection"]["actual"]),
            lambda row: bool(row["extraction"]["ambiguity_detection"]["predicted"]),
        ),
        "overall_f1": None,
        "overall_f1_prohibited": True,
    }
    retrieval_by_k = {
        str(k): {
            metric: _hierarchical_mean(
                rows,
                lambda row, k=k, metric=metric: row["retrieval"]["metrics_at_k"][str(k)][metric],
            )
            for metric in ("recall", "hit", "ndcg")
        }
        for k in ks
    }
    retrieval = {
        "at_k": retrieval_by_k,
        "mrr": _hierarchical_mean(rows, lambda row: row["retrieval"]["mrr"]),
        "retrieval_miss_rate": _hierarchical_mean(
            rows, lambda row: row["retrieval"]["retrieval_miss"]
        ),
    }
    constraints = {
        "final_selection_hard_constraint_violation_rate": _hierarchical_mean(
            [row for row in rows if row["expected_feasibility"] == "feasible"],
            lambda row: row["constraints"]["selected_hard_constraint_violation"],
        ),
        "acceptable_candidate_false_rejection_rate": _hierarchical_mean(
            rows,
            lambda row: row["constraints"]["acceptable_false_rejection_rate"],
        ),
        "infeasible_candidate_survival_rate": _hierarchical_mean(
            rows,
            lambda row: row["constraints"]["infeasible_candidate_survival_rate"],
        ),
        "infeasible_request_detection": _weighted_binary(
            [row for row in rows if row["expected_feasibility"] != "ambiguous"],
            lambda row: bool(row["constraints"]["infeasible_request_detection"]["actual"]),
            lambda row: bool(row["constraints"]["infeasible_request_detection"]["predicted"]),
        ),
        "unsupported_constraint_handling": _weighted_binary(
            rows,
            lambda row: bool(row["constraints"]["unsupported_constraint_handling"]["actual"]),
            lambda row: bool(row["constraints"]["unsupported_constraint_handling"]["predicted"]),
        ),
    }
    return {
        "family_count": len({row["family_id"] for row in rows}),
        "variant_count": len({(row["family_id"], row["variant_id"]) for row in rows}),
        "execution_count": len(rows),
        "aggregation_unit": "workload_family",
        "extraction": extraction,
        "retrieval": retrieval,
        "constraints": constraints,
        "ranking": {
            "ranking_error_rate": _hierarchical_mean(
                rows, lambda row: row["ranking"]["ranking_error"]
            )
        },
        "end_to_end": {
            "query_correctness": _hierarchical_mean(
                rows, lambda row: row["end_to_end"]["query_correct"]
            ),
            "recommendation_failure_rate": _hierarchical_mean(
                rows, lambda row: row["end_to_end"]["recommendation_failed"]
            ),
        },
    }


def _family_rows(
    recommendations: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in recommendations:
        grouped[(str(row["system_id"]), str(row["family_id"]))].append(row)
    results: list[dict[str, Any]] = []
    for (system_id, family_id), rows in sorted(grouped.items()):
        by_variant: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            by_variant[str(row["variant_id"])].append(row)
        variant_summaries: list[dict[str, Any]] = []
        for variant_id, variant_rows in sorted(by_variant.items()):
            variant_failed = [
                row
                for row in variant_rows
                if row["end_to_end"]["recommendation_failed"]
            ]
            variant_categories = Counter(
                str(row["end_to_end"]["primary_category"])
                for row in variant_failed
                if row["end_to_end"]["primary_category"] is not None
            )
            variant_primary = (
                min(
                    variant_categories,
                    key=lambda item: (_CATEGORY_ORDER[item], item),
                )
                if variant_categories
                else None
            )
            variant_summaries.append(
                {
                    "variant_id": variant_id,
                    "repeat_count": len(variant_rows),
                    "repeat_indices": sorted(
                        row["repeat_index"] for row in variant_rows
                    ),
                    "query_correctness": _mean(
                        [
                            row["end_to_end"]["query_correct"]
                            for row in variant_rows
                        ]
                    ),
                    "recommendation_failure_rate": _mean(
                        [
                            row["end_to_end"]["recommendation_failed"]
                            for row in variant_rows
                        ]
                    ),
                    "primary_category": variant_primary,
                    "repeat_primary_category_counts": dict(
                        sorted(variant_categories.items())
                    ),
                    "secondary_tags": sorted(
                        {
                            tag
                            for row in variant_rows
                            for tag in row["end_to_end"]["secondary_tags"]
                        }
                    ),
                    "acceptable_feasible_in_top_k": any(
                        bool(
                            row["constraints"][
                                "acceptable_feasible_candidate_ids"
                            ]
                        )
                        for row in variant_rows
                    ),
                    "recommendation_record_ids": [
                        row["record_id"] for row in variant_rows
                    ],
                }
            )
        failed = [row for row in rows if row["end_to_end"]["recommendation_failed"]]
        categories = Counter(
            str(row["end_to_end"]["primary_category"])
            for row in failed
            if row["end_to_end"]["primary_category"] is not None
        )
        primary = (
            min(categories, key=lambda item: (_CATEGORY_ORDER[item], item))
            if categories
            else None
        )
        secondary = sorted(
            {
                tag
                for row in rows
                for tag in row["end_to_end"]["secondary_tags"]
            }
        )
        results.append(
            {
                "schema_version": PER_FAMILY_SCHEMA_VERSION,
                "system_id": system_id,
                "family_id": family_id,
                "variant_ids": sorted({str(row["variant_id"]) for row in rows}),
                "variant_count": len(variant_summaries),
                "execution_count": len(rows),
                "failed_execution_count": len(failed),
                "failed_variant_count": sum(
                    summary["recommendation_failure_rate"] > 0
                    for summary in variant_summaries
                ),
                "query_correctness": _mean(
                    [summary["query_correctness"] for summary in variant_summaries]
                ),
                "family_weighted_diagnostics": {
                    "recommendation_failure_rate": _mean(
                        [
                            summary["recommendation_failure_rate"]
                            for summary in variant_summaries
                        ]
                    ),
                    "extraction_mismatch_rate": _mean(
                        [
                            _mean(
                                [
                                    bool(row["extraction"]["mismatch_fields"])
                                    for row in by_variant[summary["variant_id"]]
                                ]
                            )
                            for summary in variant_summaries
                        ]
                    ),
                    "retrieval_miss_rate": _mean(
                        [
                            _mean(
                                [
                                    row["retrieval"]["retrieval_miss"]
                                    for row in by_variant[summary["variant_id"]]
                                ]
                            )
                            for summary in variant_summaries
                        ]
                    ),
                    "constraint_error_rate": _mean(
                        [
                            _mean(
                                [
                                    row["constraints"]["constraint_error"]
                                    for row in by_variant[summary["variant_id"]]
                                ]
                            )
                            for summary in variant_summaries
                        ]
                    ),
                    "ranking_error_rate": _mean(
                        [
                            _mean(
                                [
                                    row["ranking"]["ranking_error"]
                                    for row in by_variant[summary["variant_id"]]
                                ]
                            )
                            for summary in variant_summaries
                        ]
                    ),
                },
                "primary_category": primary,
                "execution_primary_category_counts": dict(sorted(categories.items())),
                "secondary_tags": secondary,
                "acceptable_feasible_in_top_k": any(
                    bool(row["constraints"]["acceptable_feasible_candidate_ids"])
                    for row in rows
                ),
                "variant_summaries": variant_summaries,
                "recommendation_record_ids": [row["record_id"] for row in rows],
            }
        )
    return tuple(results)


def p3_headroom_report(
    families: Sequence[Mapping[str, Any]],
    *,
    role: str,
    minimum_count: int = 3,
    minimum_fraction: float = 0.05,
) -> dict[str, Any]:
    if minimum_count < 1:
        raise ComponentAnalysisError("P3 gate minimum_count must be positive")
    if not 0 < minimum_fraction <= 1:
        raise ComponentAnalysisError("P3 gate minimum_fraction must be in (0, 1]")
    if role != "development":
        return {
            "schema_version": P3_HEADROOM_SCHEMA_VERSION,
            "status": "NOT_APPLICABLE_CONFIRMATORY",
            "advisory_only": True,
            "backend_changed": False,
        }
    p2 = [family for family in families if family["system_id"] == "P2"]
    if not p2:
        return {
            "schema_version": P3_HEADROOM_SCHEMA_VERSION,
            "status": "NOT_EXECUTED",
            "reason_code": "NO_COMPLETE_P2_FAMILY_ROWS",
            "advisory_only": True,
            "backend_changed": False,
        }
    error_families = [family for family in p2 if family["primary_category"] is not None]
    ranking_errors = [
        family for family in error_families if family["primary_category"] == "RANKING_ERROR"
    ]
    eligible = [family for family in p2 if family["acceptable_feasible_in_top_k"]]
    errors_with_feasible = [
        family for family in error_families if family["acceptable_feasible_in_top_k"]
    ]
    error_fraction = _safe_rate(len(ranking_errors), len(error_families))
    eligible_rate = _safe_rate(len(ranking_errors), len(eligible))
    required = max(
        minimum_count,
        math.ceil(minimum_fraction * len(eligible)) if eligible else minimum_count,
    )
    passed = bool(
        eligible
        and len(ranking_errors) >= required
        and eligible_rate is not None
        and eligible_rate >= minimum_fraction
    )
    return {
        "schema_version": P3_HEADROOM_SCHEMA_VERSION,
        "status": "EVALUATED",
        "unit": "workload_family",
        "p2_error_family_count": len(error_families),
        "ranking_error_family_count": len(ranking_errors),
        "ranking_error_fraction_of_p2_errors": error_fraction,
        "p2_error_families_with_acceptable_feasible_top_k": len(errors_with_feasible),
        "eligible_family_count": len(eligible),
        "ranking_error_fraction_of_eligible_families": eligible_rate,
        "ranking_error_family_ids": sorted(family["family_id"] for family in ranking_errors),
        "gate_configuration": {
            "minimum_absolute_ranking_errors": minimum_count,
            "minimum_ranking_error_fraction": minimum_fraction,
            "required_ranking_error_count": required,
            "criterion": (
                "ranking errors >= max(minimum_count, ceil(minimum_fraction * "
                "eligible families)) and ranking-error fraction of eligible families "
                ">= minimum_fraction"
            ),
        },
        "criterion_met": passed,
        "advisory_decision": "retained" if passed else "not_retained",
        "advisory_only": True,
        "backend_changed": False,
    }


def _validate_scoring_coverage(
    gold: GoldSource,
    records: Sequence[Mapping[str, Any]],
) -> None:
    cases = {case.case_id: case for case in gold.cases}
    if not cases or len(cases) != len(gold.cases):
        raise ComponentAnalysisError("component gold coverage is empty or ambiguous")
    if not records:
        raise ComponentAnalysisError("component scoring requires complete raw evidence")
    systems = {record.get("system_id") for record in records}
    if not systems or not systems.issubset({"P2", "P3"}):
        raise ComponentAnalysisError("component evidence must contain only P2/P3 rows")
    run_ids = {record.get("run_id") for record in records}
    if len(run_ids) != 1 or None in run_ids:
        raise ComponentAnalysisError("component evidence cannot mix raw runs")

    logical_keys: set[tuple[str, str, int]] = set()
    repeats_by_system_case: dict[tuple[str, str], set[int]] = defaultdict(set)
    for record in records:
        case_id = record.get("case_id")
        if case_id not in cases:
            raise ComponentAnalysisError("component evidence contains an unknown case")
        case = cases[str(case_id)]
        if (
            record.get("family_id") != case.family_id
            or record.get("variant_id") != case.variant_id
        ):
            raise ComponentAnalysisError(
                "component evidence family/variant identity is inconsistent"
            )
        repeat_index = record.get("repeat_index")
        if (
            isinstance(repeat_index, bool)
            or not isinstance(repeat_index, int)
            or repeat_index < 0
        ):
            raise ComponentAnalysisError("component evidence repeat index is invalid")
        system_id = str(record["system_id"])
        key = (str(case_id), system_id, repeat_index)
        if key in logical_keys:
            raise ComponentAnalysisError("component evidence has a duplicate logical row")
        logical_keys.add(key)
        repeats_by_system_case[(system_id, str(case_id))].add(repeat_index)

    expected_cases = set(cases)
    for system_id in systems:
        observed_cases = {
            case_id
            for selected_system, case_id in repeats_by_system_case
            if selected_system == system_id
        }
        if observed_cases != expected_cases:
            raise ComponentAnalysisError(
                "component evidence does not exactly cover gold for every system"
            )
        repeat_sets = {
            tuple(sorted(repeats_by_system_case[(str(system_id), case_id)]))
            for case_id in expected_cases
        }
        if len(repeat_sets) != 1:
            raise ComponentAnalysisError(
                "component evidence repeat coverage is incomplete"
            )
        selected_repeats = next(iter(repeat_sets))
        if selected_repeats != tuple(range(len(selected_repeats))):
            raise ComponentAnalysisError(
                "component evidence repeats must be contiguous from zero"
            )


def score_component_records(
    gold: GoldSource,
    records: Sequence[Mapping[str, Any]],
    *,
    retrieval_ks: Sequence[int] = (1, 3, 5),
    gate_minimum_count: int = 3,
    gate_minimum_fraction: float = 0.05,
    corpus: CandidateCorpus | None = None,
) -> AnalysisResult:
    ks = tuple(sorted(set(retrieval_ks)))
    if not ks or any(isinstance(k, bool) or not isinstance(k, int) or k < 1 for k in ks):
        raise ComponentAnalysisError("retrieval K values must be positive integers")
    _validate_scoring_coverage(gold, records)
    case_index = {case.case_id: case for case in gold.cases}
    selected_corpus = corpus or load_candidate_corpus()
    if selected_corpus.corpus_checksum != gold.catalog_identity.get(
        "candidate_corpus_sha256"
    ):
        raise ComponentAnalysisError("live candidate corpus does not match component gold")
    scored = tuple(
        score_recommendation(
            record,
            case_index[str(record["case_id"])],
            corpus=selected_corpus,
            retrieval_ks=ks,
        )
        for record in records
    )
    for row in scored:
        _validate_primary_attribution(row)
    families = _family_rows(scored)
    systems = {
        system: _system_aggregates(
            [row for row in scored if row["system_id"] == system], ks
        )
        for system in sorted({str(row["system_id"]) for row in scored})
    }
    aggregates = {
        "schema_version": COMPONENT_AGGREGATES_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "status": "DERIVED",
        "aggregation_policy": {
            "independent_unit": "workload_family",
            "repeat_handling": "mean within variant before family aggregation",
            "variant_handling": "mean within family before cross-family aggregation",
            "binary_retrieval_relevance": "gold acceptable candidate IDs",
            "hard_soft_extraction_scores_separate": True,
        },
        "systems": systems,
        "error_taxonomy": {
            "primary_categories": list(PRIMARY_CATEGORIES),
            "per_recommendation_counts": {
                system: dict(
                    sorted(
                        Counter(
                            row["end_to_end"]["primary_category"]
                            for row in scored
                            if row["system_id"] == system
                            and row["end_to_end"]["primary_category"] is not None
                        ).items()
                    )
                )
                for system in systems
            },
            "per_family_counts": {
                system: dict(
                    sorted(
                        Counter(
                            family["primary_category"]
                            for family in families
                            if family["system_id"] == system
                            and family["primary_category"] is not None
                        ).items()
                    )
                )
                for system in systems
            },
        },
    }
    headroom = p3_headroom_report(
        families,
        role=gold.role,
        minimum_count=gate_minimum_count,
        minimum_fraction=gate_minimum_fraction,
    )
    return AnalysisResult(
        aggregates=aggregates,
        recommendations=scored,
        families=families,
        p3_headroom=headroom,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


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


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


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


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ComponentAnalysisError(f"{label} is unreadable or malformed") from exc
    if not isinstance(value, dict):
        raise ComponentAnalysisError(f"{label} must contain an object")
    return value


def validate_analysis_package(output_dir: Path) -> dict[str, Any]:
    """Recompute derived-output checksums and enforce analysis invariants."""

    manifest = _read_json_object(
        output_dir / "analysis-manifest.json", "analysis manifest"
    )
    if manifest.get("schema_version") != COMPONENT_ANALYSIS_SCHEMA_VERSION:
        raise ComponentAnalysisError("analysis manifest schema is unsupported")
    status = manifest.get("status")
    if status == "NOT_EXECUTED":
        if set(manifest) != _NOT_EXECUTED_MANIFEST_FIELDS:
            raise ComponentAnalysisError(
                "NOT_EXECUTED analysis manifest contains unsupported fields"
            )
        if (
            manifest.get("claims_permitted") is not False
            or manifest.get("outputs") != {}
            or manifest.get("p3_headroom_gate_status") != "NOT_EXECUTED"
        ):
            raise ComponentAnalysisError("NOT_EXECUTED analysis manifest is invalid")
        if any(
            not isinstance(manifest.get(field), str)
            or not str(manifest[field]).strip()
            for field in ("reason_code", "reason")
        ):
            raise ComponentAnalysisError(
                "NOT_EXECUTED analysis manifest lacks a machine-readable reason"
            )
        if {
            item.name for item in output_dir.iterdir()
        } != {"analysis-manifest.json"}:
            raise ComponentAnalysisError(
                "NOT_EXECUTED package must not contain derived metric files"
            )
        return {
            "schema_version": COMPONENT_ANALYSIS_SCHEMA_VERSION,
            "status": "PASS",
            "analysis_status": status,
            "outputs_validated": 0,
        }
    if status != "DERIVED_EVIDENCE_COMPLETE":
        raise ComponentAnalysisError("analysis package status is unsupported")
    expected = {
        "aggregates": ("aggregates.json", COMPONENT_AGGREGATES_SCHEMA_VERSION),
        "per_recommendation": (
            "per-recommendation.jsonl",
            PER_RECOMMENDATION_SCHEMA_VERSION,
        ),
        "per_family": ("per-family.jsonl", PER_FAMILY_SCHEMA_VERSION),
        "p3_headroom_gate": ("p3-headroom-gate.json", P3_HEADROOM_SCHEMA_VERSION),
    }
    output_manifest = manifest.get("outputs")
    if not isinstance(output_manifest, Mapping) or set(output_manifest) != set(expected):
        raise ComponentAnalysisError("analysis manifest output registry is incomplete")
    parsed: dict[str, Any] = {}
    for name, (filename, schema_version) in expected.items():
        identity = output_manifest[name]
        if not isinstance(identity, Mapping) or identity.get("path") != filename:
            raise ComponentAnalysisError(f"analysis output identity is invalid for {name}")
        path = output_dir / filename
        if not path.is_file() or identity.get("sha256") != file_sha256(path):
            raise ComponentAnalysisError(f"analysis output checksum mismatch for {name}")
        if path.suffix == ".jsonl":
            rows = _strict_json_lines(path)
            if any(row.get("schema_version") != schema_version for row in rows):
                raise ComponentAnalysisError(f"analysis row schema mismatch for {name}")
            parsed[name] = rows
        else:
            payload = _read_json_object(path, name)
            if payload.get("schema_version") != schema_version:
                raise ComponentAnalysisError(f"analysis object schema mismatch for {name}")
            parsed[name] = payload
    for row in parsed["per_recommendation"]:
        _validate_primary_attribution(row)
    return {
        "schema_version": COMPONENT_ANALYSIS_SCHEMA_VERSION,
        "status": "PASS",
        "analysis_status": status,
        "outputs_validated": len(expected),
        "recommendations_validated": len(parsed["per_recommendation"]),
        "families_validated": len(parsed["per_family"]),
    }


def write_not_executed(
    output_dir: Path,
    *,
    reason: str,
    reason_code: str = "INPUTS_NOT_SUPPLIED",
) -> Path:
    if not isinstance(reason, str) or not reason.strip():
        raise ComponentAnalysisError("NOT_EXECUTED reason must be non-blank")
    if not isinstance(reason_code, str) or not reason_code.strip():
        raise ComponentAnalysisError("NOT_EXECUTED reason_code must be non-blank")
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": COMPONENT_ANALYSIS_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "status": "NOT_EXECUTED",
        "claims_permitted": False,
        "created_at_utc": _utc_now(),
        "git_revision": _git_revision(),
        "reason_code": reason_code.strip(),
        "reason": reason.strip(),
        "p3_headroom_gate_status": "NOT_EXECUTED",
        "outputs": {},
    }
    _write_json_exclusive(output_dir / "analysis-manifest.json", manifest)
    validate_analysis_package(output_dir)
    return output_dir


def write_analysis_package(
    output_dir: Path,
    *,
    result: AnalysisResult,
    gold: GoldSource,
    evidence_dir: Path,
    provenance: Mapping[str, Any],
    retrieval_ks: Sequence[int],
    gate_minimum_count: int,
    gate_minimum_fraction: float,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=False)
    outputs = {
        "aggregates": output_dir / "aggregates.json",
        "per_recommendation": output_dir / "per-recommendation.jsonl",
        "per_family": output_dir / "per-family.jsonl",
        "p3_headroom_gate": output_dir / "p3-headroom-gate.json",
    }
    _write_json_exclusive(outputs["aggregates"], result.aggregates)
    _write_jsonl_exclusive(outputs["per_recommendation"], result.recommendations)
    _write_jsonl_exclusive(outputs["per_family"], result.families)
    _write_json_exclusive(outputs["p3_headroom_gate"], result.p3_headroom)
    raw_path = evidence_dir / RAW_DIRECTORY_NAME / RECORDS_FILENAME
    manifest = {
        "schema_version": COMPONENT_ANALYSIS_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "status": "DERIVED_EVIDENCE_COMPLETE",
        "claims_permitted": True,
        "claim_scope": "descriptive component diagnostics only",
        "created_at_utc": _utc_now(),
        "git_revision": _git_revision(),
        "source": {
            "offline_run_id": provenance.get("run_id"),
            "offline_provenance_fingerprint": provenance.get("provenance_fingerprint"),
            "offline_recommendations_sha256": file_sha256(raw_path),
            "gold_dataset_id": gold.dataset_id,
            "gold_schema_version": gold.schema_version,
            "gold_source_file_sha256": gold.source_file_sha256,
            "gold_canonical_sha256": gold.canonical_sha256,
            "split_role": gold.role,
            "freeze_identity": gold.freeze_identity,
        },
        "systems": list(provenance.get("systems", [])),
        "backend_system_versions": provenance.get("system_frozen_provenance", {}),
        "candidate_catalog": provenance.get("candidate_catalog"),
        "source_environment_identity": provenance.get("environment_identity"),
        "configuration": {
            "retrieval_ks": list(retrieval_ks),
            "p3_gate_minimum_count": gate_minimum_count,
            "p3_gate_minimum_fraction": gate_minimum_fraction,
            "offline_frozen_configuration": provenance.get(
                "frozen_configuration", {}
            ),
        },
        "environment_identity": {
            "python_version": platform.python_version(),
            "platform": platform.system(),
            "platform_release": platform.release(),
        },
        "outputs": {
            name: {
                "path": path.name,
                "sha256": file_sha256(path),
            }
            for name, path in outputs.items()
        },
        "raw_evidence_unchanged": True,
        "backend_changed": False,
    }
    _write_json_exclusive(output_dir / "analysis-manifest.json", manifest)
    validate_analysis_package(output_dir)
    return output_dir


def analyze_component_evidence(
    evidence_dir: Path,
    gold_path: Path,
    output_dir: Path,
    *,
    role: str = "development",
    freeze_path: Path | None = None,
    split_id: str | None = None,
    retrieval_ks: Sequence[int] = (1, 3, 5),
    gate_minimum_count: int = 3,
    gate_minimum_fraction: float = 0.05,
) -> Path:
    try:
        gold = load_component_gold(
            gold_path,
            role=role,
            freeze_path=freeze_path,
            split_id=split_id,
        )
        provenance, records = load_component_evidence(evidence_dir, gold)
        result = score_component_records(
            gold,
            records,
            retrieval_ks=retrieval_ks,
            gate_minimum_count=gate_minimum_count,
            gate_minimum_fraction=gate_minimum_fraction,
        )
    except (
        ComponentAnalysisError,
        ContractValidationError,
        GoldDatasetValidationError,
        OfflineEvidenceValidationError,
        SplitBundleValidationError,
        OSError,
        ValueError,
    ) as exc:
        return write_not_executed(
            output_dir,
            reason=f"Component inputs unavailable or invalid: {exc}",
            reason_code="INPUTS_UNAVAILABLE_OR_INVALID",
        )
    return write_analysis_package(
        output_dir,
        result=result,
        gold=gold,
        evidence_dir=evidence_dir,
        provenance=provenance,
        retrieval_ks=retrieval_ks,
        gate_minimum_count=gate_minimum_count,
        gate_minimum_fraction=gate_minimum_fraction,
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
    parser.add_argument("--retrieval-k", type=_parse_ks, default=(1, 3, 5))
    parser.add_argument("--gate-minimum-count", type=int, default=3)
    parser.add_argument("--gate-minimum-fraction", type=float, default=0.05)
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument(
        "--not-executed-reason",
        default="Complete Prompt-3 gold and Prompt-5 raw evidence were not supplied.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.status_only:
            if args.evidence_dir is not None or args.gold_dataset is not None:
                raise ComponentAnalysisError(
                    "--status-only cannot be combined with evidence or gold inputs"
                )
            output = write_not_executed(
                args.output_dir,
                reason=args.not_executed_reason,
            )
        else:
            if args.evidence_dir is None or args.gold_dataset is None:
                output = write_not_executed(
                    args.output_dir,
                    reason=(
                        "Complete Prompt-3 gold and Prompt-5 raw evidence were "
                        "not both supplied."
                    ),
                    reason_code="INPUTS_NOT_SUPPLIED",
                )
            else:
                output = analyze_component_evidence(
                    args.evidence_dir,
                    args.gold_dataset,
                    args.output_dir,
                    role=args.role,
                    freeze_path=args.freeze,
                    split_id=args.split_id,
                    retrieval_ks=args.retrieval_k,
                    gate_minimum_count=args.gate_minimum_count,
                    gate_minimum_fraction=args.gate_minimum_fraction,
                )
        analysis_status = _read_json_object(
            output / "analysis-manifest.json", "analysis manifest"
        )["status"]
        print(
            json.dumps(
                {"status": analysis_status, "output_dir": str(output)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except (
        ComponentAnalysisError,
        ContractValidationError,
        GoldDatasetValidationError,
        OfflineEvidenceValidationError,
        SplitBundleValidationError,
        OSError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {
                    "schema_version": COMPONENT_ANALYSIS_SCHEMA_VERSION,
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
    "AnalysisResult",
    "COMPONENT_AGGREGATES_SCHEMA_VERSION",
    "COMPONENT_ANALYSIS_SCHEMA_VERSION",
    "ComponentAnalysisError",
    "GoldCase",
    "GoldSource",
    "P3_HEADROOM_SCHEMA_VERSION",
    "PER_FAMILY_SCHEMA_VERSION",
    "PER_RECOMMENDATION_SCHEMA_VERSION",
    "PRIMARY_CATEGORIES",
    "analyze_component_evidence",
    "load_component_evidence",
    "load_component_gold",
    "load_validated_evidence",
    "main",
    "p3_headroom_report",
    "score_component_records",
    "score_recommendation",
    "validate_analysis_package",
    "write_analysis_package",
    "write_not_executed",
]
