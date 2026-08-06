"""Explainable rule-based recommender for JupyterHub/KubeSpawner decisions.

The recommender intentionally stays simple for the thesis demo: it converts
intent text, an estimated dataset size, and optional code context into a
resource profile and an admin-allowlisted notebook image. The Helm prototype
mirrors this logic in JupyterHub extraConfig so the confirmed decision can be
applied before a user server spawns.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import yaml


PROFILES = ("small", "medium", "large", "gpu_or_large")
POLICY_VERSION = "resource-image-policy-v1"
DEFAULT_CATALOG_PATH = Path(__file__).with_name("image-catalog.yaml")
IMAGE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
IMMUTABLE_IMAGE_PATTERN = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")

GPU_TERMS = (
    "torch",
    "tensorflow",
    "cuda",
    "gpu",
    "deep learning",
    "resnet",
    "bert",
)

TRAINING_TERMS = (
    "train",
    "training",
    "fit",
    ".fit(",
    "sklearn",
    "scikit-learn",
    "xgboost",
    "model",
)

DATA_TERMS = (
    "pandas",
    "read_csv",
    "dataframe",
    "csv",
    "parquet",
)


@dataclass(frozen=True)
class Recommendation:
    profile: str
    reasons: list[str]
    score: int
    image_id: str
    image_reference: str
    image_reasons: list[str]
    catalog_version: str
    policy_version: str = POLICY_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "reasons": self.reasons,
            "score": self.score,
            "image_id": self.image_id,
            "image_reference": self.image_reference,
            "image_reasons": self.image_reasons,
            "catalog_version": self.catalog_version,
            "policy_version": self.policy_version,
        }


def validate_image_catalog(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate catalog data and return a shallow normalized copy."""

    if not isinstance(raw, dict):
        raise ValueError("image catalog must be a mapping")

    version = raw.get("catalog_version")
    default_image = raw.get("default_image")
    images = raw.get("images")
    if not isinstance(version, str) or not version:
        raise ValueError("image catalog requires catalog_version")
    if not isinstance(images, dict) or not images:
        raise ValueError("image catalog requires at least one image")
    if default_image not in images:
        raise ValueError("default_image must identify an allowlisted image")

    for image_id, item in images.items():
        if not isinstance(image_id, str) or not IMAGE_ID_PATTERN.fullmatch(image_id):
            raise ValueError(f"invalid image ID {image_id!r}")
        if not isinstance(item, dict):
            raise ValueError(f"catalog entry {image_id!r} must be a mapping")
        reference = item.get("reference")
        if not isinstance(reference, str) or not IMMUTABLE_IMAGE_PATTERN.fullmatch(reference):
            raise ValueError(f"catalog entry {image_id!r} must use an immutable sha256 reference")
        if not isinstance(item.get("display_name"), str) or not item["display_name"]:
            raise ValueError(f"catalog entry {image_id!r} requires display_name")
        if not isinstance(item.get("description"), str) or not item["description"]:
            raise ValueError(f"catalog entry {image_id!r} requires description")
        if not isinstance(item.get("capabilities"), list) or not all(
            isinstance(value, str) and value for value in item["capabilities"]
        ):
            raise ValueError(f"catalog entry {image_id!r} requires string capabilities")
        if not isinstance(item.get("match_terms"), list) or not all(
            isinstance(value, str) and value for value in item["match_terms"]
        ):
            raise ValueError(f"catalog entry {image_id!r} requires string match_terms")
        if not isinstance(item.get("priority"), int):
            raise ValueError(f"catalog entry {image_id!r} requires integer priority")

    return dict(raw)


def load_image_catalog(path: str | Path = DEFAULT_CATALOG_PATH) -> dict[str, Any]:
    """Load and strictly validate the administrator-managed image allowlist."""

    with Path(path).open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return validate_image_catalog(raw)


def _normalize_text(parts: Iterable[str | None]) -> str:
    return "\n".join(part or "" for part in parts).lower()


def _contains_any(text: str, terms: Iterable[str]) -> list[str]:
    found: list[str] = []
    for term in terms:
        if term.startswith("."):
            if term in text:
                found.append(term)
            continue

        pattern = r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])"
        if re.search(pattern, text):
            found.append(term)
    return found


def _coerce_dataset_size_gb(value: float | int | str | None) -> float:
    """Treat missing, invalid, or negative dataset-size hints as unknown."""

    if value in (None, ""):
        return 0.0

    try:
        dataset_size_gb = float(value)
    except (TypeError, ValueError):
        return 0.0

    if not math.isfinite(dataset_size_gb) or dataset_size_gb < 0:
        return 0.0
    return dataset_size_gb


def recommend_image(
    intent: str = "",
    code_context: str = "",
    *,
    catalog: Mapping[str, Any] | None = None,
) -> tuple[str, str, list[str], str]:
    """Select only from the catalog and return ID, reference, reasons, version."""

    selected_catalog = validate_image_catalog(catalog) if catalog is not None else load_image_catalog()
    images = selected_catalog["images"]
    text = _normalize_text([intent, code_context])
    ranked = sorted(images.items(), key=lambda pair: (-pair[1]["priority"], pair[0]))
    for image_id, item in ranked:
        hits = _contains_any(text, item["match_terms"])
        if hits:
            return (
                image_id,
                item["reference"],
                [
                    "image capability match: " + ", ".join(hits[:4]),
                    "selected from administrator catalog only",
                ],
                selected_catalog["catalog_version"],
            )

    image_id = selected_catalog["default_image"]
    item = images[image_id]
    return (
        image_id,
        item["reference"],
        ["no specialized image signal detected", "selected catalog default"],
        selected_catalog["catalog_version"],
    )


def recommend_profile(
    intent: str = "",
    dataset_size_gb: float | int | str | None = 0.0,
    code_context: str = "",
    *,
    catalog: Mapping[str, Any] | None = None,
) -> Recommendation:
    """Return resource and notebook-image recommendations with explanations."""

    dataset_size_gb = _coerce_dataset_size_gb(dataset_size_gb)
    text = _normalize_text([intent, code_context])
    reasons: list[str] = []
    image_id, image_reference, image_reasons, catalog_version = recommend_image(
        intent,
        code_context,
        catalog=catalog,
    )

    gpu_hits = _contains_any(text, GPU_TERMS)
    if gpu_hits:
        return Recommendation(
            profile="gpu_or_large",
            score=99,
            reasons=[
                "GPU/deep-learning context detected: " + ", ".join(gpu_hits[:4]),
                "Demo environment has no real GPU, so this maps to Large resources.",
            ],
            image_id=image_id,
            image_reference=image_reference,
            image_reasons=image_reasons,
            catalog_version=catalog_version,
        )

    score = 0

    if dataset_size_gb >= 2.0:
        score += 3
        reasons.append("dataset size >= 2GB")
    elif dataset_size_gb >= 0.5:
        score += 1
        reasons.append("dataset size >= 0.5GB")

    data_hits = _contains_any(text, DATA_TERMS)
    if data_hits:
        score += 1
        reasons.append("data-processing context detected: " + ", ".join(data_hits[:4]))

    training_hits = _contains_any(text, TRAINING_TERMS)
    if training_hits:
        score += 2
        reasons.append("training/modeling context detected: " + ", ".join(training_hits[:4]))

    if score >= 3:
        profile = "large"
    elif score >= 1:
        profile = "medium"
    else:
        profile = "small"
        reasons.append("basic/light workload context")

    return Recommendation(
        profile=profile,
        score=score,
        reasons=reasons,
        image_id=image_id,
        image_reference=image_reference,
        image_reasons=image_reasons,
        catalog_version=catalog_version,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recommend a demo JupyterHub profile.")
    parser.add_argument("--intent", default="", help="Natural-language user intent.")
    parser.add_argument("--dataset-gb", type=float, default=0.0, help="Estimated dataset size in GB.")
    parser.add_argument("--code-context", default="", help="Optional imports or notebook snippet.")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG_PATH,
        help="Administrator-managed notebook image catalog.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    rec = recommend_profile(
        args.intent,
        args.dataset_gb,
        args.code_context,
        catalog=load_image_catalog(args.catalog),
    )
    print(json.dumps(rec.to_dict(), indent=2))


if __name__ == "__main__":
    main()
