"""Provider-neutral dense retrieval for the P2 pipeline.

Candidate vectors are produced outside the retrieval algorithm through an
``EmbeddingProvider``.  The index binds every vector to model, catalog, and
normalization provenance and verifies a canonical checksum before use.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
import re
from typing import Protocol, runtime_checkable
import unicodedata

from .candidate_corpus import CandidateCorpus, canonical_json_checksum
from .models import (
    ContractValidationError,
    RetrievalHit,
    RetrievalSource,
    StructuredIntent,
    _normalized_identifier,
    _provenance_label,
    _schema_version,
    _version,
)


EMBEDDING_MODEL_METADATA_SCHEMA_VERSION = "embedding-model-metadata-v1"
DENSE_INDEX_SCHEMA_VERSION = "dense-embedding-index-v1"
DENSE_QUERY_REPRESENTATION_VERSION = "dense-query-representation-v1"
DENSE_RETRIEVER_VERSION = "cosine-dense-retriever-v1"
DEFAULT_DENSE_INDEX_VERSION = "environment-dense-index-v1"
SUPPORTED_NORMALIZATION_SETTINGS = frozenset({"none", "l2"})

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class DenseRetrievalError(RuntimeError):
    """A provider call or dense-vector operation failed safely."""


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractValidationError(f"{label} must be a positive integer")
    return value


def _validated_vector(
    vector: object,
    *,
    dimensions: int,
    label: str,
    require_nonzero: bool = True,
) -> tuple[float, ...]:
    if isinstance(vector, (str, bytes, Mapping)) or not isinstance(vector, Sequence):
        raise ContractValidationError(f"{label} must be a vector sequence")
    if len(vector) != dimensions:
        raise ContractValidationError(
            f"{label} dimension mismatch: expected {dimensions}, got {len(vector)}"
        )
    values: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ContractValidationError(f"{label} values must be finite numbers")
        number = float(value)
        if not math.isfinite(number):
            raise ContractValidationError(f"{label} values must be finite numbers")
        values.append(0.0 if number == 0 else number)
    result = tuple(values)
    if require_nonzero and math.sqrt(sum(value * value for value in result)) == 0:
        raise ContractValidationError(f"{label} must not be a zero vector")
    return result


@dataclass(frozen=True, slots=True)
class EmbeddingModelMetadata:
    """Identity and vector-shape contract exposed by an embedding provider."""

    model_id: str
    model_revision: str
    dimensions: int
    normalization: str
    schema_version: str = EMBEDDING_MODEL_METADATA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_id", _provenance_label(self.model_id, "model_id"))
        object.__setattr__(
            self,
            "model_revision",
            _provenance_label(self.model_revision, "model_revision"),
        )
        object.__setattr__(self, "dimensions", _positive_integer(self.dimensions, "dimensions"))
        if self.normalization not in SUPPORTED_NORMALIZATION_SETTINGS:
            supported = ", ".join(sorted(SUPPORTED_NORMALIZATION_SETTINGS))
            raise ContractValidationError(f"normalization must be one of: {supported}")
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(
                self.schema_version, EMBEDDING_MODEL_METADATA_SCHEMA_VERSION
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "dimensions": self.dimensions,
            "normalization": self.normalization,
            "schema_version": self.schema_version,
        }


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Independent provider contract for candidate and query embeddings."""

    @property
    def metadata(self) -> EmbeddingModelMetadata:
        ...

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        ...


@dataclass(frozen=True, slots=True)
class CandidateEmbedding:
    """One administrator-catalog candidate vector within a versioned index."""

    candidate_id: str
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _normalized_identifier(self.candidate_id, "candidate_id")
        )
        if isinstance(self.vector, (str, bytes, Mapping)) or not isinstance(
            self.vector, Sequence
        ):
            raise ContractValidationError("candidate vector must be a vector sequence")
        object.__setattr__(self, "vector", tuple(self.vector))


@dataclass(frozen=True, slots=True)
class DenseIndexMetadata:
    """Required model, vector, catalog, and checksum provenance for an index."""

    index_version: str
    index_checksum: str
    model_id: str
    model_revision: str
    dimensions: int
    normalization: str
    catalog_version: str
    corpus_checksum: str
    query_representation_version: str = DENSE_QUERY_REPRESENTATION_VERSION
    retriever_version: str = DENSE_RETRIEVER_VERSION
    schema_version: str = DENSE_INDEX_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "index_version", _version(self.index_version, "index_version"))
        object.__setattr__(self, "model_id", _provenance_label(self.model_id, "model_id"))
        object.__setattr__(
            self,
            "model_revision",
            _provenance_label(self.model_revision, "model_revision"),
        )
        object.__setattr__(self, "dimensions", _positive_integer(self.dimensions, "dimensions"))
        if self.normalization not in SUPPORTED_NORMALIZATION_SETTINGS:
            supported = ", ".join(sorted(SUPPORTED_NORMALIZATION_SETTINGS))
            raise ContractValidationError(f"normalization must be one of: {supported}")
        object.__setattr__(
            self, "catalog_version", _version(self.catalog_version, "catalog_version")
        )
        for name in ("index_checksum", "corpus_checksum"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
                raise ContractValidationError(
                    f"{name} must be a lowercase SHA-256 digest"
                )
        object.__setattr__(
            self,
            "query_representation_version",
            _version(self.query_representation_version, "query_representation_version"),
        )
        object.__setattr__(
            self,
            "retriever_version",
            _version(self.retriever_version, "retriever_version"),
        )
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, DENSE_INDEX_SCHEMA_VERSION),
        )

    def checksum_fields(self) -> dict[str, object]:
        return {
            "index_version": self.index_version,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "dimensions": self.dimensions,
            "normalization": self.normalization,
            "catalog_version": self.catalog_version,
            "corpus_checksum": self.corpus_checksum,
            "query_representation_version": self.query_representation_version,
            "retriever_version": self.retriever_version,
            "schema_version": self.schema_version,
        }


def dense_index_checksum(
    metadata: DenseIndexMetadata,
    embeddings: Sequence[CandidateEmbedding],
) -> str:
    """Calculate the canonical checksum covering metadata and all candidate vectors."""
    payload = {
        **metadata.checksum_fields(),
        "embeddings": [
            {"candidate_id": item.candidate_id, "vector": list(item.vector)}
            for item in sorted(embeddings, key=lambda item: item.candidate_id)
        ],
    }
    return canonical_json_checksum(payload)


@dataclass(frozen=True, slots=True)
class DenseEmbeddingIndex:
    """Immutable, checksum-verified collection of candidate embeddings."""

    metadata: DenseIndexMetadata
    embeddings: tuple[CandidateEmbedding, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, DenseIndexMetadata):
            raise ContractValidationError("metadata must be DenseIndexMetadata")
        if isinstance(self.embeddings, (str, bytes, Mapping)) or not isinstance(
            self.embeddings, Sequence
        ):
            raise ContractValidationError("embeddings must be a sequence")
        ordered = tuple(sorted(self.embeddings, key=lambda item: item.candidate_id))
        if not all(isinstance(item, CandidateEmbedding) for item in ordered):
            raise ContractValidationError(
                "embeddings must contain only CandidateEmbedding objects"
            )
        candidate_ids = [item.candidate_id for item in ordered]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ContractValidationError("embedding candidate IDs must be unique")

        validated: list[CandidateEmbedding] = []
        for item in ordered:
            vector = _validated_vector(
                item.vector,
                dimensions=self.metadata.dimensions,
                label=f"candidate {item.candidate_id} vector",
            )
            if self.metadata.normalization == "l2":
                norm = math.sqrt(sum(value * value for value in vector))
                if not math.isclose(norm, 1.0, rel_tol=1e-6, abs_tol=1e-6):
                    raise ContractValidationError(
                        f"candidate {item.candidate_id} vector is not L2-normalized"
                    )
            validated.append(CandidateEmbedding(item.candidate_id, vector))
        object.__setattr__(self, "embeddings", tuple(validated))

        expected = dense_index_checksum(self.metadata, self.embeddings)
        if self.metadata.index_checksum != expected:
            raise ContractValidationError("dense index checksum mismatch")


def _provider_metadata(provider: EmbeddingProvider) -> EmbeddingModelMetadata:
    try:
        metadata = provider.metadata
    except Exception as exc:
        raise ContractValidationError("embedding provider metadata is unavailable") from exc
    if not isinstance(metadata, EmbeddingModelMetadata):
        raise ContractValidationError(
            "embedding provider metadata must be EmbeddingModelMetadata"
        )
    return metadata


def _embed_batch(
    provider: EmbeddingProvider,
    texts: Sequence[str],
    *,
    operation: str,
) -> Sequence[Sequence[float]]:
    try:
        vectors = provider.embed(texts)
    except Exception as exc:
        raise DenseRetrievalError(f"embedding provider failed during {operation}") from exc
    if isinstance(vectors, (str, bytes, Mapping)) or not isinstance(vectors, Sequence):
        raise DenseRetrievalError(
            f"embedding provider returned an invalid result during {operation}"
        )
    if len(vectors) != len(texts):
        raise DenseRetrievalError(
            f"embedding provider returned {len(vectors)} vectors for {len(texts)} texts"
        )
    return vectors


def build_dense_index(
    corpus: CandidateCorpus,
    provider: EmbeddingProvider,
    *,
    index_version: str = DEFAULT_DENSE_INDEX_VERSION,
) -> DenseEmbeddingIndex:
    """Embed approved candidate retrieval text and construct a verified index."""
    if not isinstance(corpus, CandidateCorpus):
        raise ContractValidationError("corpus must be a CandidateCorpus")
    model = _provider_metadata(provider)
    index_version = _version(index_version, "index_version")
    ordered = tuple(sorted(corpus.candidates, key=lambda item: item.candidate_id))
    raw_vectors = _embed_batch(
        provider,
        tuple(candidate.retrieval_text for candidate in ordered),
        operation="candidate indexing",
    )
    embeddings = tuple(
        CandidateEmbedding(
            candidate_id=candidate.candidate_id,
            vector=_validated_vector(
                vector,
                dimensions=model.dimensions,
                label=f"candidate {candidate.candidate_id} vector",
            ),
        )
        for candidate, vector in zip(ordered, raw_vectors)
    )
    provisional = DenseIndexMetadata(
        index_version=index_version,
        index_checksum="0" * 64,
        model_id=model.model_id,
        model_revision=model.model_revision,
        dimensions=model.dimensions,
        normalization=model.normalization,
        catalog_version=corpus.source_image_catalog_version,
        corpus_checksum=corpus.corpus_checksum,
    )
    metadata = DenseIndexMetadata(
        index_version=index_version,
        index_checksum=dense_index_checksum(provisional, embeddings),
        model_id=model.model_id,
        model_revision=model.model_revision,
        dimensions=model.dimensions,
        normalization=model.normalization,
        catalog_version=corpus.source_image_catalog_version,
        corpus_checksum=corpus.corpus_checksum,
    )
    return DenseEmbeddingIndex(metadata=metadata, embeddings=embeddings)


def _normalized_original_intent(value: str) -> str:
    if not isinstance(value, str):
        raise ContractValidationError("original_intent must be a string")
    return " ".join(unicodedata.normalize("NFKC", value).split())


def build_dense_query(original_intent: str, structured_intent: StructuredIntent) -> str:
    """Create the versioned query text from original and normalized intent data."""
    if not isinstance(structured_intent, StructuredIntent):
        raise ContractValidationError("structured_intent must be a StructuredIntent")
    constraints = structured_intent.resource_constraints
    normalized_fields = {
        "task_types": [item.value for item in structured_intent.task_types],
        "required_features": list(structured_intent.required_features),
        "preferred_features": list(structured_intent.preferred_features),
        "forbidden_features": list(structured_intent.forbidden_features),
        "required_frameworks": list(structured_intent.required_frameworks),
        "preferred_frameworks": list(structured_intent.preferred_frameworks),
        "required_libraries": list(structured_intent.required_libraries),
        "preferred_libraries": list(structured_intent.preferred_libraries),
        "resource_constraints": {
            "gpu_requirement": constraints.gpu_requirement.value,
            "minimum_cpu_cores": constraints.minimum_cpu_cores,
            "minimum_memory_gb": constraints.minimum_memory_gb,
            "dataset_size_gb": constraints.dataset_size_gb,
        },
        "normalized_query": structured_intent.normalized_query,
        "schema_version": structured_intent.schema_version,
    }
    return "\n".join(
        (
            f"Query representation: {DENSE_QUERY_REPRESENTATION_VERSION}",
            f"Original intent: {_normalized_original_intent(original_intent)}",
            "Structured intent: "
            + json.dumps(
                normalized_fields,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )


class DenseRetriever:
    """Cosine-rank a verified candidate index using a compatible provider."""

    def __init__(
        self,
        corpus: CandidateCorpus,
        index: DenseEmbeddingIndex,
        provider: EmbeddingProvider,
        *,
        top_k: int = 10,
    ) -> None:
        if not isinstance(corpus, CandidateCorpus):
            raise ContractValidationError("corpus must be a CandidateCorpus")
        if not isinstance(index, DenseEmbeddingIndex):
            raise ContractValidationError("index must be a DenseEmbeddingIndex")
        self._corpus = corpus
        self._index = index
        self._provider = provider
        self._top_k = _positive_integer(top_k, "top_k")
        self._validate_compatibility()

    @property
    def metadata(self) -> DenseIndexMetadata:
        return self._index.metadata

    @property
    def top_k(self) -> int:
        return self._top_k

    def _validate_compatibility(self) -> None:
        provider = _provider_metadata(self._provider)
        index = self._index.metadata
        comparisons = {
            "model_id": (provider.model_id, index.model_id),
            "model_revision": (provider.model_revision, index.model_revision),
            "dimensions": (provider.dimensions, index.dimensions),
            "normalization": (provider.normalization, index.normalization),
        }
        for label, (actual, expected) in comparisons.items():
            if actual != expected:
                raise ContractValidationError(
                    f"embedding provider {label} is incompatible with dense index"
                )
        if index.catalog_version != self._corpus.source_image_catalog_version:
            raise ContractValidationError("dense index catalog_version is stale")
        if index.corpus_checksum != self._corpus.corpus_checksum:
            raise ContractValidationError("dense index corpus_checksum is stale")
        index_ids = tuple(item.candidate_id for item in self._index.embeddings)
        corpus_ids = tuple(sorted(self._corpus.candidate_ids))
        if index_ids != corpus_ids:
            raise ContractValidationError(
                "dense index candidate IDs do not match the candidate corpus"
            )

    def retrieve(
        self,
        original_intent: str,
        structured_intent: StructuredIntent,
        *,
        top_k: int | None = None,
    ) -> tuple[RetrievalHit, ...]:
        """Embed one query and return deterministic cosine-ordered hits."""
        limit = self._top_k if top_k is None else _positive_integer(top_k, "top_k")
        self._validate_compatibility()
        query_text = build_dense_query(original_intent, structured_intent)
        raw_vectors = _embed_batch(
            self._provider, (query_text,), operation="query embedding"
        )
        try:
            query_vector = _validated_vector(
                raw_vectors[0],
                dimensions=self.metadata.dimensions,
                label="query vector",
            )
        except ContractValidationError as exc:
            raise DenseRetrievalError(str(exc)) from exc

        query_norm = math.sqrt(sum(value * value for value in query_vector))
        if self.metadata.normalization == "l2" and not math.isclose(
            query_norm, 1.0, rel_tol=1e-6, abs_tol=1e-6
        ):
            raise DenseRetrievalError(
                "query vector is incompatible with the index L2 normalization setting"
            )
        scored: list[tuple[str, float]] = []
        for embedding in self._index.embeddings:
            candidate_norm = math.sqrt(
                sum(value * value for value in embedding.vector)
            )
            cosine = sum(
                query_value * candidate_value
                for query_value, candidate_value in zip(
                    query_vector, embedding.vector
                )
            ) / (query_norm * candidate_norm)
            cosine = max(-1.0, min(1.0, cosine))
            # RetrievalHit has a non-negative score contract.  This affine
            # transform preserves the complete cosine ordering and all ties.
            score = (cosine + 1.0) / 2.0
            scored.append((embedding.candidate_id, score))

        scored.sort(key=lambda item: (-item[1], item[0]))
        return tuple(
            RetrievalHit(
                candidate_id=candidate_id,
                source=RetrievalSource.DENSE,
                rank=rank,
                score=score,
                retriever_version=self.metadata.retriever_version,
                index_version=self.metadata.index_version,
            )
            for rank, (candidate_id, score) in enumerate(scored[:limit], start=1)
        )


class DeterministicFakeEmbeddingProvider:
    """Exact text-to-vector mapping for deterministic unit and integration tests."""

    def __init__(
        self,
        vectors: Mapping[str, Sequence[float]],
        *,
        metadata: EmbeddingModelMetadata,
        failure: Exception | None = None,
    ) -> None:
        if not isinstance(vectors, Mapping):
            raise ContractValidationError("fake embedding vectors must be a mapping")
        if not isinstance(metadata, EmbeddingModelMetadata):
            raise ContractValidationError("metadata must be EmbeddingModelMetadata")
        self._vectors = {key: tuple(value) for key, value in vectors.items()}
        if not all(isinstance(key, str) for key in self._vectors):
            raise ContractValidationError("fake embedding keys must be strings")
        self._metadata = metadata
        self._failure = failure

    @property
    def metadata(self) -> EmbeddingModelMetadata:
        return self._metadata

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if self._failure is not None:
            raise self._failure
        vectors: list[tuple[float, ...]] = []
        for text in texts:
            if text not in self._vectors:
                raise KeyError(f"no deterministic fake embedding for text {text!r}")
            vectors.append(self._vectors[text])
        return tuple(vectors)


__all__ = [
    "DEFAULT_DENSE_INDEX_VERSION",
    "DENSE_INDEX_SCHEMA_VERSION",
    "DENSE_QUERY_REPRESENTATION_VERSION",
    "DENSE_RETRIEVER_VERSION",
    "EMBEDDING_MODEL_METADATA_SCHEMA_VERSION",
    "SUPPORTED_NORMALIZATION_SETTINGS",
    "CandidateEmbedding",
    "DenseEmbeddingIndex",
    "DenseIndexMetadata",
    "DenseRetrievalError",
    "DenseRetriever",
    "DeterministicFakeEmbeddingProvider",
    "EmbeddingModelMetadata",
    "EmbeddingProvider",
    "build_dense_index",
    "build_dense_query",
    "dense_index_checksum",
]
