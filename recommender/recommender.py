"""Explainable rule-based recommender for JupyterHub/KubeSpawner profiles.

The recommender intentionally stays simple for the thesis demo: it converts
intent text, an estimated dataset size, and optional code context into one of
four profile names. The Helm prototype mirrors this logic in JupyterHub
extraConfig so the recommendation can be applied before a user server spawns.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import re
from typing import Iterable


PROFILES = ("small", "medium", "large", "gpu_or_large")

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

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "reasons": self.reasons,
            "score": self.score,
        }


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


def recommend_profile(
    intent: str = "",
    dataset_size_gb: float = 0.0,
    code_context: str = "",
) -> Recommendation:
    """Return a recommended profile and human-readable reasons."""

    text = _normalize_text([intent, code_context])
    reasons: list[str] = []

    gpu_hits = _contains_any(text, GPU_TERMS)
    if gpu_hits:
        return Recommendation(
            profile="gpu_or_large",
            score=99,
            reasons=[
                "GPU/deep-learning context detected: " + ", ".join(gpu_hits[:4]),
                "Demo environment has no real GPU, so this maps to Large resources.",
            ],
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

    return Recommendation(profile=profile, score=score, reasons=reasons)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recommend a demo JupyterHub profile.")
    parser.add_argument("--intent", default="", help="Natural-language user intent.")
    parser.add_argument("--dataset-gb", type=float, default=0.0, help="Estimated dataset size in GB.")
    parser.add_argument("--code-context", default="", help="Optional imports or notebook snippet.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    rec = recommend_profile(args.intent, args.dataset_gb, args.code_context)
    print(json.dumps(rec.to_dict(), indent=2))


if __name__ == "__main__":
    main()

