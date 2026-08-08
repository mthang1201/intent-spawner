"""Explainable rule-based spawn recommendation backend."""

from __future__ import annotations

from pathlib import Path
import math
import re
from typing import Any, Iterable, Mapping

import yaml

from .models import POLICY_VERSION, RecommendationRequest, SpawnRecommendation


PROFILES = ("small", "medium", "large", "gpu_or_large")
BACKEND_NAME = "rule_based"
BACKEND_VERSION = "rule-based-v1"
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


def coerce_dataset_size_gb(value: float | int | str | None) -> float:
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


class RuleBasedRecommender:
    """Deterministic implementation of the existing resource and image rules."""

    def __init__(
        self,
        *,
        catalog: Mapping[str, Any] | None = None,
        catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    ) -> None:
        self._catalog = (
            validate_image_catalog(catalog)
            if catalog is not None
            else load_image_catalog(catalog_path)
        )

    @property
    def catalog(self) -> dict[str, Any]:
        """Return the validated catalog used by this backend."""

        return self._catalog

    def recommend_image(self, intent: str = "", code_context: str = "") -> tuple[str, str, list[str], str]:
        """Select only from the configured administrator catalog."""

        images = self._catalog["images"]
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
                    self._catalog["catalog_version"],
                )

        image_id = self._catalog["default_image"]
        item = images[image_id]
        return (
            image_id,
            item["reference"],
            ["no specialized image signal detected", "selected catalog default"],
            self._catalog["catalog_version"],
        )

    def recommend(self, request: RecommendationRequest) -> SpawnRecommendation:
        """Return the existing deterministic recommendation in the unified model."""

        dataset_size_gb = coerce_dataset_size_gb(request.dataset_size_gb)
        text = _normalize_text([request.intent, request.code_context])
        reasons: list[str] = []
        image_id, image_reference, image_reasons, catalog_version = self.recommend_image(
            request.intent,
            request.code_context,
        )

        gpu_hits = _contains_any(text, GPU_TERMS)
        if gpu_hits:
            return SpawnRecommendation(
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
                policy_version=POLICY_VERSION,
                backend_name=BACKEND_NAME,
                backend_version=BACKEND_VERSION,
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

        return SpawnRecommendation(
            profile=profile,
            score=score,
            reasons=reasons,
            image_id=image_id,
            image_reference=image_reference,
            image_reasons=image_reasons,
            catalog_version=catalog_version,
            policy_version=POLICY_VERSION,
            backend_name=BACKEND_NAME,
            backend_version=BACKEND_VERSION,
        )
