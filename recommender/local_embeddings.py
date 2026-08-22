"""Dependency-light, deterministic local embedding provider for P2.

The provider is intentionally small enough for the existing ConfigMap runtime.
It hashes Unicode word and character n-gram features into a fixed dense vector
and L2 normalizes the result.  Production deployments can replace it through
the existing :class:`EmbeddingProvider` interface without changing P2 ranking
or trust boundaries.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import math
import unicodedata
from collections.abc import Sequence

from .dense_retrieval import EmbeddingModelMetadata


LOCAL_EMBEDDING_MODEL_ID = "intent-spawner-local-feature-hash"
LOCAL_EMBEDDING_MODEL_REVISION = "feature-hash-embedding-v1.0.0"
LOCAL_EMBEDDING_DIMENSIONS = 384
LOCAL_EMBEDDING_TOKENIZER_VERSION = "unicode-subword-features-v1.0.0"


def _tokens(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    values: list[str] = []
    current: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if category[0] in {"L", "N"} or (category[0] == "M" and current):
            current.append(character)
        elif current:
            values.append("".join(current))
            current = []
    if current:
        values.append("".join(current))
    return tuple(values)


def _features(text: str) -> Counter[str]:
    tokens = _tokens(text)
    features: Counter[str] = Counter()
    for token in tokens:
        features[f"w:{token}"] += 4
        bounded = f"^{token}$"
        for size in (3, 4, 5):
            for offset in range(max(0, len(bounded) - size + 1)):
                features[f"c{size}:{bounded[offset:offset + size]}"] += 1
    for left, right in zip(tokens, tokens[1:]):
        features[f"b:{left}:{right}"] += 2
    if not features:
        features["w:__empty__"] = 1
    return features


def _feature_location(feature: str, dimensions: int) -> tuple[int, float]:
    digest = hashlib.sha256(feature.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") % dimensions
    sign = 1.0 if digest[8] & 1 else -1.0
    return bucket, sign


class LocalFeatureHashEmbeddingProvider:
    """Generate reproducible local embeddings with explicit model provenance."""

    def __init__(self, *, dimensions: int = LOCAL_EMBEDDING_DIMENSIONS) -> None:
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 32:
            raise ValueError("local embedding dimensions must be an integer >= 32")
        self._metadata = EmbeddingModelMetadata(
            model_id=LOCAL_EMBEDDING_MODEL_ID,
            model_revision=LOCAL_EMBEDDING_MODEL_REVISION,
            dimensions=dimensions,
            normalization="l2",
        )

    @property
    def metadata(self) -> EmbeddingModelMetadata:
        return self._metadata

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if isinstance(texts, (str, bytes)) or not isinstance(texts, Sequence):
            raise ValueError("embedding input must be a sequence of strings")
        vectors: list[tuple[float, ...]] = []
        for text in texts:
            if not isinstance(text, str):
                raise ValueError("embedding input must contain only strings")
            vector = [0.0] * self.metadata.dimensions
            for feature, weight in _features(text).items():
                bucket, sign = _feature_location(feature, self.metadata.dimensions)
                vector[bucket] += sign * float(weight)
            norm = math.sqrt(sum(value * value for value in vector))
            vectors.append(tuple(value / norm for value in vector))
        return tuple(vectors)


__all__ = [
    "LOCAL_EMBEDDING_DIMENSIONS",
    "LOCAL_EMBEDDING_MODEL_ID",
    "LOCAL_EMBEDDING_MODEL_REVISION",
    "LOCAL_EMBEDDING_TOKENIZER_VERSION",
    "LocalFeatureHashEmbeddingProvider",
]
