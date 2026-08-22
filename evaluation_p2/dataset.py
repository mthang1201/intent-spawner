"""Compose the frozen v4 gold set with versioned P2 diagnostic cases."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from evaluation_v4.dataset import canonical_sha256, load_dataset


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUPPLEMENT = ROOT / "benchmarks" / "p2-infeasible-supplement-v1.yaml"
SUPPLEMENT_SCHEMA_VERSION = "p2-infeasible-supplement-v1.0.0"
EVALUATION_DATASET_SCHEMA_VERSION = "p1-p2-evaluation-dataset-v1.0.0"
KNOWN_PROFILES = frozenset({"small", "medium", "large"})


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_base_item(item: Mapping[str, Any]) -> dict[str, Any]:
    gold = item["gold"]
    acceptable = sorted(
        f"{profile}-{image_id}"
        for profile in gold["acceptable_profiles"]
        for image_id in gold["acceptable_image_ids"]
    )
    return {
        "sample_id": item["sample_id"],
        "workload_family": item["workload_family"],
        "language": item["language"],
        "source_dataset": "intent-gold-v4",
        "inputs": {
            "intent": item["inputs"]["intent"],
            "dataset_size_gb": item["inputs"]["dataset_size_gb"],
            "code_context_hints": list(item["inputs"]["code_context_hints"]),
        },
        "gold": {
            "request_feasible": True,
            "preferred_candidate_id": (
                f"{gold['preferred_profile']}-{gold['preferred_image_id']}"
            ),
            "acceptable_candidate_ids": acceptable,
            "required_image_capabilities": list(gold["required_image_capabilities"]),
            "allowed_profiles": list(item["policy_constraints"]["allowed_profiles"]),
            "gpu_allowed": bool(item["policy_constraints"]["gpu_allowed"]),
            "expected_extraction": None,
        },
    }


def _string_list(value: object, label: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValueError(f"{label} must be a list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{label} must contain non-blank strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} must not contain duplicates")
    return list(value)


def validate_supplement(document: object) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise ValueError("P2 supplement must be a mapping")
    root = dict(document)
    if root.get("schema_version") != SUPPLEMENT_SCHEMA_VERSION:
        raise ValueError("P2 supplement schema_version is unsupported")
    for field in ("dataset_id", "frozen_at_utc", "description", "license"):
        if not isinstance(root.get(field), str) or not root[field].strip():
            raise ValueError(f"P2 supplement {field} must be non-blank")
    items = root.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("P2 supplement items must be a non-empty list")
    sample_ids: set[str] = set()
    for index, item in enumerate(items):
        label = f"items[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} must be a mapping")
        sample_id = item.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id.strip() or sample_id in sample_ids:
            raise ValueError(f"{label}.sample_id must be unique and non-blank")
        sample_ids.add(sample_id)
        inputs = item.get("inputs")
        gold = item.get("gold")
        if not isinstance(inputs, Mapping) or not isinstance(gold, Mapping):
            raise ValueError(f"{label} requires inputs and gold mappings")
        if not isinstance(inputs.get("intent"), str) or not inputs["intent"].strip():
            raise ValueError(f"{label}.inputs.intent must be non-blank")
        size = inputs.get("dataset_size_gb")
        if isinstance(size, bool) or not isinstance(size, (int, float)) or size < 0:
            raise ValueError(f"{label}.inputs.dataset_size_gb must be non-negative")
        _string_list(inputs.get("code_context_hints"), f"{label}.code_context_hints")
        if not isinstance(gold.get("request_feasible"), bool):
            raise ValueError(f"{label}.gold.request_feasible must be boolean")
        acceptable = _string_list(
            gold.get("acceptable_candidate_ids"),
            f"{label}.gold.acceptable_candidate_ids",
        )
        preferred = gold.get("preferred_candidate_id")
        if gold["request_feasible"]:
            if not acceptable or preferred not in acceptable:
                raise ValueError(f"{label} feasible gold requires a preferred acceptable candidate")
        elif acceptable or preferred is not None:
            raise ValueError(f"{label} infeasible gold cannot define candidates")
        profiles = _string_list(
            gold.get("allowed_profiles"), f"{label}.gold.allowed_profiles", allow_empty=False
        )
        if not set(profiles).issubset(KNOWN_PROFILES):
            raise ValueError(f"{label} contains an unknown allowed profile")
        _string_list(
            gold.get("required_image_capabilities"),
            f"{label}.gold.required_image_capabilities",
        )
        expected = gold.get("expected_extraction")
        if not isinstance(expected, Mapping):
            raise ValueError(f"{label}.gold.expected_extraction must be a mapping")
        _string_list(
            expected.get("required_libraries"),
            f"{label}.gold.expected_extraction.required_libraries",
        )
    return root


def load_evaluation_dataset(
    supplement_path: Path = DEFAULT_SUPPLEMENT,
) -> dict[str, Any]:
    base = load_dataset()
    with supplement_path.open(encoding="utf-8") as handle:
        supplement = validate_supplement(yaml.safe_load(handle))
    items = [_normalized_base_item(item) for item in base["items"]]
    items.extend(
        {
            "sample_id": item["sample_id"],
            "workload_family": item["workload_family"],
            "language": item["language"],
            "source_dataset": supplement["dataset_id"],
            "inputs": dict(item["inputs"]),
            "gold": dict(item["gold"]),
        }
        for item in supplement["items"]
    )
    if len({item["sample_id"] for item in items}) != len(items):
        raise ValueError("composed P1/P2 evaluation sample IDs must be unique")
    identity = {
        "schema_version": EVALUATION_DATASET_SCHEMA_VERSION,
        "base_dataset_id": base["dataset_id"],
        "base_dataset_sha256": canonical_sha256(base),
        "supplement_dataset_id": supplement["dataset_id"],
        "supplement_file_sha256": _file_sha256(supplement_path),
        "sample_ids": [item["sample_id"] for item in items],
    }
    return {
        **identity,
        "dataset_id": f"{base['dataset_id']}+{supplement['dataset_id']}",
        "dataset_sha256": hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "items": items,
    }


__all__ = [
    "DEFAULT_SUPPLEMENT",
    "EVALUATION_DATASET_SCHEMA_VERSION",
    "SUPPLEMENT_SCHEMA_VERSION",
    "load_evaluation_dataset",
    "validate_supplement",
]
