"""Versioned contracts for the unified Protocol-v5 research analysis layer."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator
import yaml


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "benchmarks_v5" / "protocol-v5-claim-registry-v1.yaml"
REGISTRY_SCHEMA_PATH = ROOT / "benchmarks_v5" / "protocol-v5-claim-registry-v1.schema.json"
EVALUATED_CLAIM_SCHEMA_PATH = ROOT / "benchmarks_v5" / "protocol-v5-evaluated-claim-v1.schema.json"
SELECTION_SCHEMA_PATH = ROOT / "benchmarks_v5" / "protocol-v5-evidence-selection-v1.schema.json"
STORAGE_SCHEMA_PATH = ROOT / "benchmarks_v5" / "protocol-v5-image-storage-evidence-v1.schema.json"
P3_THRESHOLD_SCHEMA_PATH = ROOT / "benchmarks_v5" / "protocol-v5-p3-overhead-threshold-v1.schema.json"

CLAIM_REGISTRY_SCHEMA_VERSION = "protocol-v5-claim-registry-v1.0.0"
EVALUATED_CLAIM_SCHEMA_VERSION = "protocol-v5-evaluated-claim-v1.0.0"
SELECTION_SCHEMA_VERSION = "protocol-v5-evidence-selection-v1.0.0"
STORAGE_SCHEMA_VERSION = "protocol-v5-image-storage-evidence-v1.0.0"
P3_THRESHOLD_SCHEMA_VERSION = "protocol-v5-p3-overhead-threshold-v1.0.0"
CLAIM_STATUSES = ("SUPPORTED", "NOT_SUPPORTED", "NOT_EXECUTED")
EXPECTED_CLAIMS = frozenset({"H1", "H2", "H3", "H4", "H5", "H6", "H7", "H7F", "H8"})
FORBIDDEN_B0_RANKING_TOKENS = ("mrr", "ndcg", "hit_at", "hit@")


class ResearchContractError(ValueError):
    """Raised when a research-analysis contract is invalid."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResearchContractError(f"{path}: invalid JSON") from exc
    if not isinstance(value, dict):
        raise ResearchContractError(f"{path}: expected an object")
    return value


def _yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ResearchContractError(f"{path}: invalid YAML") from exc
    if not isinstance(value, dict):
        raise ResearchContractError(f"{path}: expected an object")
    return value


def _validate_schema(value: Mapping[str, Any], schema_path: Path) -> None:
    schema = _json(schema_path)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/" + "/".join(str(part) for part in first.absolute_path)
        raise ResearchContractError(f"{schema_path.name}{location}: {first.message}")


def load_claim_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    registry = _yaml(path)
    _validate_schema(registry, REGISTRY_SCHEMA_PATH)
    rq_ids = [row["id"] for row in registry["research_questions"]]
    claim_ids = [row["id"] for row in registry["claims"]]
    evidence_ids = [row["id"] for row in registry["evidence_requirements"]]
    if len(rq_ids) != len(set(rq_ids)):
        raise ResearchContractError("research-question IDs must be unique")
    if len(claim_ids) != len(set(claim_ids)) or set(claim_ids) != EXPECTED_CLAIMS:
        raise ResearchContractError("claim registry must contain H1-H8 and H7F exactly once")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ResearchContractError("evidence-requirement IDs must be unique")
    rq_set = set(rq_ids)
    evidence_set = set(evidence_ids)
    for claim in registry["claims"]:
        if claim["research_question"] not in rq_set:
            raise ResearchContractError(f"{claim['id']}: unknown research question")
        if not set(claim["required_evidence"]).issubset(evidence_set):
            raise ResearchContractError(f"{claim['id']}: unknown evidence requirement")
        for metric in claim["metrics"]:
            key = str(metric["id"]).lower()
            if "B0" in metric["systems"] and any(token in key for token in FORBIDDEN_B0_RANKING_TOKENS):
                raise ResearchContractError(
                    f"{claim['id']}: B0 does not produce ranking metrics ({metric['id']})"
                )
    return registry


def load_selection(path: Path, *, registry_path: Path = REGISTRY_PATH) -> dict[str, Any]:
    selection = _yaml(path) if path.suffix.lower() in {".yaml", ".yml"} else _json(path)
    _validate_schema(selection, SELECTION_SCHEMA_PATH)
    expected = file_sha256(registry_path)
    if selection["registry_sha256"] != expected:
        raise ResearchContractError(
            "selection registry_sha256 does not match the selected claim registry"
        )
    return selection


def validate_storage_evidence(value: Mapping[str, Any]) -> None:
    _validate_schema(value, STORAGE_SCHEMA_PATH)
    catalog_digests = value["catalog"]["ordered_image_digests"]
    prefixes = value["prefixes"]
    if len(prefixes) != len(catalog_digests):
        raise ResearchContractError("storage evidence must contain every ordered catalog prefix")
    for index, row in enumerate(prefixes, start=1):
        if row["prefix_size"] != index:
            raise ResearchContractError("storage prefix_size values must be consecutive from one")
        if row["image_digests"] != catalog_digests[:index]:
            raise ResearchContractError("storage prefix images differ from the frozen catalog order")
    if value["execution_status"] == "OBSERVED":
        if value["split_stage"] != "confirmatory" and value["claims_permitted"]:
            raise ResearchContractError("development storage evidence cannot permit claims")
    elif value["claims_permitted"]:
        raise ResearchContractError("NOT_EXECUTED storage evidence cannot permit claims")


def load_p3_threshold(path: Path) -> dict[str, Any]:
    threshold = _yaml(path) if path.suffix.lower() in {".yaml", ".yml"} else _json(path)
    _validate_schema(threshold, P3_THRESHOLD_SCHEMA_PATH)
    metrics = [row["metric"] for row in threshold["overhead_limits"]]
    if len(metrics) != len(set(metrics)):
        raise ResearchContractError("P3 overhead metric limits must be unique")
    return threshold


def validate_evaluated_claim(value: Mapping[str, Any]) -> None:
    _validate_schema(value, EVALUATED_CLAIM_SCHEMA_PATH)
    if value["claimable"] and value["claim_status"] == "NOT_EXECUTED":
        raise ResearchContractError("NOT_EXECUTED claims cannot be claimable")


def dotted_get(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def json_pointer_get(value: Mapping[str, Any], pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    current: Any = value
    for encoded in pointer[1:].split("/"):
        part = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(pointer)
        current = current[part]
    return current


def evaluate_conditions(
    conditions: Sequence[Mapping[str, Any]], context: Mapping[str, Any]
) -> tuple[bool | None, list[dict[str, Any]]]:
    """Evaluate declarative conditions; ``None`` means an input was unavailable."""

    results: list[dict[str, Any]] = []
    unavailable = False
    all_pass = True
    for condition in conditions:
        path = str(condition["path"])
        operator = str(condition["operator"])
        expected = condition.get("value")
        try:
            observed = dotted_get(context, path)
        except KeyError:
            observed = None
            unavailable = True
            passed = None
        else:
            if observed is None or (
                isinstance(observed, float) and not math.isfinite(observed)
            ):
                unavailable = True
                passed = None
            else:
                try:
                    passed = {
                        "eq": lambda: observed == expected,
                        "ne": lambda: observed != expected,
                        "gt": lambda: observed > expected,
                        "ge": lambda: observed >= expected,
                        "lt": lambda: observed < expected,
                        "le": lambda: observed <= expected,
                        "in": lambda: observed in expected,
                    }[operator]()
                except (KeyError, TypeError):
                    passed = None
                    unavailable = True
                if passed is False:
                    all_pass = False
        results.append(
            {
                "path": path,
                "operator": operator,
                "expected": expected,
                "observed": observed,
                "passed": passed,
            }
        )
    return (None if unavailable else all_pass), results


__all__ = [
    "CLAIM_REGISTRY_SCHEMA_VERSION",
    "CLAIM_STATUSES",
    "EVALUATED_CLAIM_SCHEMA_VERSION",
    "EXPECTED_CLAIMS",
    "P3_THRESHOLD_SCHEMA_VERSION",
    "REGISTRY_PATH",
    "ResearchContractError",
    "SELECTION_SCHEMA_VERSION",
    "STORAGE_SCHEMA_VERSION",
    "canonical_json_sha256",
    "dotted_get",
    "evaluate_conditions",
    "file_sha256",
    "json_pointer_get",
    "load_claim_registry",
    "load_p3_threshold",
    "load_selection",
    "validate_evaluated_claim",
    "validate_storage_evidence",
]
