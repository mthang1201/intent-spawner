"""Validation and integrity helpers for the protocol-v4 intent gold set."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "benchmarks" / "intent-gold-v4.yaml"
DATASET_SCHEMA_VERSION = "intent-gold-v4.0.0"
PROFILE_ORDER = {"small": 0, "medium": 1, "large": 2}
SPLITS = {"development", "test"}
ADJUDICATION_STATUSES = {"protocol_locked", "expert_adjudicated"}


def normalize_profile(profile: str | None) -> str | None:
    """Map canonical profile tokens (such as gpu_or_large) to evaluated profiles."""
    if profile == "gpu_or_large":
        return "large"
    return profile if profile in PROFILE_ORDER else None


def canonical_sha256(value: Any) -> str:
    """Hash a JSON-compatible value independently of YAML formatting."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _require_nonblank(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-blank string")
    return value


def _require_string_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise ValueError(f"{label} must be {qualifier}")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{label} must contain only non-blank strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} must not contain duplicates")
    return list(value)


def validate_dataset(document: Any) -> dict[str, Any]:
    """Validate leakage controls, label provenance, and catalog references."""

    root = _require_mapping(document, "dataset")
    if root.get("schema_version") != DATASET_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must be {DATASET_SCHEMA_VERSION!r}"
        )
    _require_nonblank(root.get("dataset_id"), "dataset_id")
    _require_nonblank(root.get("label_policy_version"), "label_policy_version")
    _require_nonblank(root.get("frozen_at_utc"), "frozen_at_utc")

    profile_order = root.get("profile_order")
    if profile_order != ["small", "medium", "large"]:
        raise ValueError("profile_order must be exactly small, medium, large")

    catalog = _require_mapping(root.get("image_catalog"), "image_catalog")
    _require_nonblank(catalog.get("catalog_version"), "image_catalog.catalog_version")
    _require_nonblank(catalog.get("source_path"), "image_catalog.source_path")
    source_sha256 = _require_nonblank(
        catalog.get("source_sha256"), "image_catalog.source_sha256"
    )
    if len(source_sha256) != 64 or any(character not in "0123456789abcdef" for character in source_sha256):
        raise ValueError("image_catalog.source_sha256 must be a lowercase SHA-256 digest")
    catalog_images = _require_mapping(catalog.get("images"), "image_catalog.images")
    if not catalog_images:
        raise ValueError("image_catalog.images must not be empty")
    for image_id, image in catalog_images.items():
        _require_nonblank(image_id, "image ID")
        image_data = _require_mapping(image, f"image_catalog.images.{image_id}")
        _require_string_list(
            image_data.get("capabilities"),
            f"image_catalog.images.{image_id}.capabilities",
        )

    items = root.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list")

    sample_ids: set[str] = set()
    family_splits: dict[str, set[str]] = defaultdict(set)
    family_variants: dict[str, set[str]] = defaultdict(set)
    split_counts: Counter[str] = Counter()
    strata: Counter[str] = Counter()

    for index, item_value in enumerate(items):
        label = f"items[{index}]"
        item = _require_mapping(item_value, label)
        sample_id = _require_nonblank(item.get("sample_id"), f"{label}.sample_id")
        if sample_id in sample_ids:
            raise ValueError(f"duplicate sample_id {sample_id!r}")
        sample_ids.add(sample_id)
        family = _require_nonblank(item.get("workload_family"), f"{label}.workload_family")
        variant = _require_nonblank(item.get("variant"), f"{label}.variant")
        split = item.get("split")
        if split not in SPLITS:
            raise ValueError(f"{label}.split must be development or test")
        language = item.get("language")
        if language not in {"en", "vi"}:
            raise ValueError(f"{label}.language must be en or vi")
        _require_nonblank(item.get("stratum"), f"{label}.stratum")

        family_splits[family].add(split)
        if variant in family_variants[family]:
            raise ValueError(f"duplicate variant {variant!r} in family {family!r}")
        family_variants[family].add(variant)
        split_counts[split] += 1
        strata[str(item["stratum"])] += 1

        inputs = _require_mapping(item.get("inputs"), f"{label}.inputs")
        _require_nonblank(inputs.get("intent"), f"{label}.inputs.intent")
        size = inputs.get("dataset_size_gb")
        if not isinstance(size, (int, float)) or isinstance(size, bool) or size < 0:
            raise ValueError(f"{label}.inputs.dataset_size_gb must be non-negative")
        _require_string_list(
            inputs.get("code_context_hints"),
            f"{label}.inputs.code_context_hints",
            allow_empty=True,
        )

        gold = _require_mapping(item.get("gold"), f"{label}.gold")
        preferred_profile = gold.get("preferred_profile")
        acceptable_profiles = _require_string_list(
            gold.get("acceptable_profiles"), f"{label}.gold.acceptable_profiles"
        )
        if any(profile not in PROFILE_ORDER for profile in acceptable_profiles):
            raise ValueError(f"{label}.gold.acceptable_profiles has an unknown profile")
        if preferred_profile not in acceptable_profiles:
            raise ValueError(f"{label}.gold.preferred_profile must be acceptable")
        acceptable_images = _require_string_list(
            gold.get("acceptable_image_ids"), f"{label}.gold.acceptable_image_ids"
        )
        if any(image_id not in catalog_images for image_id in acceptable_images):
            raise ValueError(f"{label}.gold.acceptable_image_ids has an unknown image")
        if gold.get("preferred_image_id") not in acceptable_images:
            raise ValueError(f"{label}.gold.preferred_image_id must be acceptable")
        required_capabilities = _require_string_list(
            gold.get("required_image_capabilities"),
            f"{label}.gold.required_image_capabilities",
        )
        if not any(
            set(required_capabilities).issubset(set(catalog_images[image_id]["capabilities"]))
            for image_id in acceptable_images
        ):
            raise ValueError(
                f"{label} has no acceptable image that covers required capabilities"
            )

        policy = _require_mapping(item.get("policy_constraints"), f"{label}.policy_constraints")
        allowed_profiles = _require_string_list(
            policy.get("allowed_profiles"), f"{label}.policy_constraints.allowed_profiles"
        )
        if any(profile not in PROFILE_ORDER for profile in allowed_profiles):
            raise ValueError(f"{label}.policy_constraints has an unknown profile")
        if not set(acceptable_profiles).issubset(set(allowed_profiles)):
            raise ValueError(f"{label}.gold profiles must satisfy policy constraints")
        if not isinstance(policy.get("gpu_allowed"), bool):
            raise ValueError(f"{label}.policy_constraints.gpu_allowed must be boolean")

        provenance = _require_mapping(item.get("provenance"), f"{label}.provenance")
        _require_nonblank(provenance.get("resource_label_basis"), f"{label}.provenance.resource_label_basis")
        _require_nonblank(provenance.get("image_label_basis"), f"{label}.provenance.image_label_basis")
        status = provenance.get("adjudication_status")
        if status not in ADJUDICATION_STATUSES:
            raise ValueError(f"{label}.provenance.adjudication_status is unsupported")
        evidence_ids = _require_string_list(
            provenance.get("evidence_ids"), f"{label}.provenance.evidence_ids"
        )
        if split == "test" and status not in ADJUDICATION_STATUSES:
            raise ValueError(f"{label} test label is not locked")
        if not evidence_ids:
            raise ValueError(f"{label} must cite label evidence")

    leaking = sorted(family for family, splits in family_splits.items() if len(splits) != 1)
    if leaking:
        raise ValueError(
            "workload families must not cross development/test splits: "
            + ", ".join(leaking)
        )
    missing_splits = sorted(SPLITS - set(split_counts))
    if missing_splits:
        raise ValueError("dataset is missing split(s): " + ", ".join(missing_splits))
    if len(strata) < 4:
        raise ValueError("dataset must cover at least four workload strata")
    if any(len(variants) < 2 for variants in family_variants.values()):
        raise ValueError("every workload family must contain at least two variants")

    system_mapping = root.get("system_workload_mapping")
    if not isinstance(system_mapping, list) or not system_mapping:
        raise ValueError("system_workload_mapping must be a non-empty list")
    mapped_families: set[str] = set()
    mapped_workloads: set[tuple[str, str]] = set()
    for index, mapping_value in enumerate(system_mapping):
        label = f"system_workload_mapping[{index}]"
        mapping = _require_mapping(mapping_value, label)
        family = _require_nonblank(mapping.get("workload_family"), f"{label}.workload_family")
        manifest_path = _require_nonblank(mapping.get("manifest_path"), f"{label}.manifest_path")
        workload_id = _require_nonblank(mapping.get("workload_id"), f"{label}.workload_id")
        if family not in family_splits:
            raise ValueError(f"{label} references an unknown workload family")
        if family_splits[family] != {"test"}:
            raise ValueError(f"{label} must reference a test-only family")
        if family in mapped_families:
            raise ValueError(f"duplicate system workload family {family!r}")
        key = (manifest_path, workload_id)
        if key in mapped_workloads:
            raise ValueError(f"duplicate system workload mapping {key!r}")
        mapped_families.add(family)
        mapped_workloads.add(key)
    if len(mapped_families) < 6:
        raise ValueError("system workload mapping must cover at least six test families")

    return dict(root)


def load_dataset(path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    dataset = validate_dataset(document)
    source = (ROOT / dataset["image_catalog"]["source_path"]).resolve()
    if not source.is_relative_to(ROOT.resolve()):
        raise ValueError("image_catalog.source_path must remain inside the repository")
    if not source.is_file():
        raise ValueError(f"image catalog source does not exist: {source}")
    if file_sha256(source) != dataset["image_catalog"]["source_sha256"]:
        raise ValueError("runtime image catalog checksum differs from the frozen gold set")
    with source.open(encoding="utf-8") as handle:
        runtime_catalog = yaml.safe_load(handle)
    if runtime_catalog.get("catalog_version") != dataset["image_catalog"]["catalog_version"]:
        raise ValueError("runtime image catalog version differs from the frozen gold set")
    runtime_images = runtime_catalog.get("images", {})
    frozen_images = dataset["image_catalog"]["images"]
    if set(runtime_images) != set(frozen_images):
        raise ValueError("runtime image IDs differ from the frozen gold set")
    for image_id, frozen in frozen_images.items():
        if runtime_images[image_id].get("capabilities") != frozen["capabilities"]:
            raise ValueError(
                f"runtime capabilities for {image_id!r} differ from the frozen gold set"
            )
    workload_manifests: dict[Path, set[str]] = {}
    for mapping in dataset["system_workload_mapping"]:
        manifest_path = (ROOT / mapping["manifest_path"]).resolve()
        if not manifest_path.is_relative_to(ROOT.resolve()) or not manifest_path.is_file():
            raise ValueError("system workload manifest must exist inside the repository")
        if manifest_path not in workload_manifests:
            with manifest_path.open(encoding="utf-8") as handle:
                manifest = yaml.safe_load(handle)
            workloads = manifest.get("workloads") if isinstance(manifest, Mapping) else None
            if not isinstance(workloads, list):
                raise ValueError(f"system workload manifest is invalid: {manifest_path}")
            workload_manifests[manifest_path] = {
                str(item.get("workload_id"))
                for item in workloads
                if isinstance(item, Mapping)
            }
        if mapping["workload_id"] not in workload_manifests[manifest_path]:
            raise ValueError(
                f"unknown system workload {mapping['workload_id']!r} in {manifest_path}"
            )
    return dataset


def dataset_index(dataset: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["sample_id"]): dict(item) for item in dataset["items"]}


def dataset_summary(dataset: Mapping[str, Any]) -> dict[str, Any]:
    items = list(dataset["items"])
    return {
        "dataset_id": dataset["dataset_id"],
        "canonical_sha256": canonical_sha256(dataset),
        "samples": len(items),
        "families": len({item["workload_family"] for item in items}),
        "splits": dict(sorted(Counter(item["split"] for item in items).items())),
        "strata": dict(sorted(Counter(item["stratum"] for item in items).items())),
        "languages": dict(sorted(Counter(item["language"] for item in items).items())),
        "system_families": len(dataset["system_workload_mapping"]),
    }


__all__ = [
    "DATASET_SCHEMA_VERSION",
    "DEFAULT_DATASET",
    "PROFILE_ORDER",
    "canonical_sha256",
    "dataset_index",
    "dataset_summary",
    "file_sha256",
    "load_dataset",
    "normalize_profile",
    "validate_dataset",
]
