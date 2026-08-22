"""Reciprocal Rank Fusion hybrid retrieval for the P2 recommendation pipeline.

This module combines deterministic lexical retrieval (``SparseBM25Retriever``) and
semantic vector retrieval (``DenseRetriever``) using Reciprocal Rank Fusion (RRF).
BM25 scores and cosine similarities are on non-comparable scales; RRF operates
strictly on candidate ranks from each retrieval leg, ensuring robust multi-channel
ranking without raw score mixing or ad-hoc scale matching.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any

from .candidate_corpus import CandidateCorpus, canonical_json_checksum
from .dense_retrieval import DenseRetriever
from .models import (
    ContractValidationError,
    RetrievalHit,
    RetrievalSource,
    StructuredIntent,
    _finite_number,
    _normalized_identifier,
    _positive_integer,
    _provenance_label,
    _schema_version,
    _version,
)
from .sparse_retrieval import SparseBM25Retriever


HYBRID_RETRIEVER_VERSION = "reciprocal-rank-fusion-hybrid-retriever-v1"
HYBRID_QUERY_REPRESENTATION_VERSION = "hybrid-query-representation-v1"
HYBRID_INDEX_SCHEMA_VERSION = "hybrid-retrieval-metadata-v1"
DEFAULT_HYBRID_INDEX_VERSION = "environment-hybrid-index-v1"
HYBRID_HIT_SCHEMA_VERSION = "hybrid-candidate-hit-v1"

DEFAULT_RRF_K: float = 60.0
DEFAULT_SPARSE_WEIGHT: float = 1.0
DEFAULT_DENSE_WEIGHT: float = 1.0
DEFAULT_TOP_K: int = 10

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _positive_number(value: object, label: str) -> float:
    number = _finite_number(value, label, minimum=0.0)
    if number <= 0:
        raise ContractValidationError(f"{label} must be positive")
    return number


def _non_negative_number(value: object, label: str) -> float:
    return _finite_number(value, label, minimum=0.0)


def build_sparse_query(original_intent: str, structured_intent: StructuredIntent) -> str:
    """Construct deterministic lexical query text from user intent and structured intent."""
    if not isinstance(structured_intent, StructuredIntent):
        raise ContractValidationError("structured_intent must be a StructuredIntent")
    if not isinstance(original_intent, str):
        raise ContractValidationError("original_intent must be a string")

    parts: list[str] = []
    trimmed_intent = original_intent.strip()
    if trimmed_intent:
        parts.append(trimmed_intent)

    if (
        structured_intent.normalized_query
        and structured_intent.normalized_query.casefold() != trimmed_intent.casefold()
    ):
        parts.append(structured_intent.normalized_query)

    for seq in (
        structured_intent.required_frameworks,
        structured_intent.preferred_frameworks,
        structured_intent.required_libraries,
        structured_intent.preferred_libraries,
        structured_intent.required_features,
        structured_intent.preferred_features,
        tuple(t.value for t in structured_intent.task_types),
    ):
        for item in seq:
            if item and item.strip():
                parts.append(item.strip())

    return " ".join(parts)


@dataclass(frozen=True, slots=True)
class HybridRetrieverMetadata:
    """Version and parameter provenance for one immutable hybrid retriever configuration."""

    index_version: str
    index_checksum: str
    catalog_version: str
    corpus_version: str
    corpus_checksum: str
    sparse_index_version: str
    dense_index_version: str
    sparse_retriever_version: str
    dense_retriever_version: str
    retriever_version: str = HYBRID_RETRIEVER_VERSION
    rrf_k: float = DEFAULT_RRF_K
    sparse_weight: float = DEFAULT_SPARSE_WEIGHT
    dense_weight: float = DEFAULT_DENSE_WEIGHT
    sparse_top_k: int = DEFAULT_TOP_K
    dense_top_k: int = DEFAULT_TOP_K
    top_k: int = DEFAULT_TOP_K
    schema_version: str = HYBRID_INDEX_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "index_version",
            "catalog_version",
            "corpus_version",
            "sparse_index_version",
            "dense_index_version",
            "sparse_retriever_version",
            "dense_retriever_version",
            "retriever_version",
        ):
            object.__setattr__(self, name, _version(getattr(self, name), name))

        for name in ("index_checksum", "corpus_checksum"):
            val = getattr(self, name)
            if not isinstance(val, str) or not _SHA256_PATTERN.fullmatch(val):
                raise ContractValidationError(
                    f"{name} must be a lowercase SHA-256 digest"
                )

        object.__setattr__(self, "rrf_k", _positive_number(self.rrf_k, "rrf_k"))
        object.__setattr__(
            self, "sparse_weight", _non_negative_number(self.sparse_weight, "sparse_weight")
        )
        object.__setattr__(
            self, "dense_weight", _non_negative_number(self.dense_weight, "dense_weight")
        )
        if self.sparse_weight == 0.0 and self.dense_weight == 0.0:
            raise ContractValidationError(
                "at least one retrieval leg weight (sparse_weight or dense_weight) must be > 0"
            )

        for name in ("sparse_top_k", "dense_top_k", "top_k"):
            object.__setattr__(self, name, _positive_integer(getattr(self, name), name))

        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, HYBRID_INDEX_SCHEMA_VERSION),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "index_version": self.index_version,
            "index_checksum": self.index_checksum,
            "catalog_version": self.catalog_version,
            "corpus_version": self.corpus_version,
            "corpus_checksum": self.corpus_checksum,
            "sparse_index_version": self.sparse_index_version,
            "dense_index_version": self.dense_index_version,
            "sparse_retriever_version": self.sparse_retriever_version,
            "dense_retriever_version": self.dense_retriever_version,
            "retriever_version": self.retriever_version,
            "rrf_k": self.rrf_k,
            "sparse_weight": self.sparse_weight,
            "dense_weight": self.dense_weight,
            "sparse_top_k": self.sparse_top_k,
            "dense_top_k": self.dense_top_k,
            "top_k": self.top_k,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class HybridCandidateHit:
    """Detailed hit carrying fused RRF score along with individual leg ranks/scores."""

    candidate_id: str
    rank: int
    score: float
    sparse_rank: int | None = None
    sparse_score: float | None = None
    dense_rank: int | None = None
    dense_score: float | None = None
    retrieval_legs: tuple[RetrievalSource, ...] = ()
    retriever_version: str = HYBRID_RETRIEVER_VERSION
    index_version: str = DEFAULT_HYBRID_INDEX_VERSION
    schema_version: str = HYBRID_HIT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _normalized_identifier(self.candidate_id, "candidate_id")
        )
        object.__setattr__(self, "rank", _positive_integer(self.rank, "rank"))
        object.__setattr__(
            self, "score", _non_negative_number(self.score, "score")
        )

        if self.sparse_rank is not None:
            object.__setattr__(
                self, "sparse_rank", _positive_integer(self.sparse_rank, "sparse_rank")
            )
            if self.sparse_score is None:
                raise ContractValidationError("sparse_score is required when sparse_rank is present")
            object.__setattr__(
                self, "sparse_score", _non_negative_number(self.sparse_score, "sparse_score")
            )
        elif self.sparse_score is not None:
            raise ContractValidationError("sparse_rank is required when sparse_score is present")

        if self.dense_rank is not None:
            object.__setattr__(
                self, "dense_rank", _positive_integer(self.dense_rank, "dense_rank")
            )
            if self.dense_score is None:
                raise ContractValidationError("dense_score is required when dense_rank is present")
            object.__setattr__(
                self, "dense_score", _non_negative_number(self.dense_score, "dense_score")
            )
        elif self.dense_score is not None:
            raise ContractValidationError("dense_rank is required when dense_score is present")

        legs: list[RetrievalSource] = []
        for leg in self.retrieval_legs:
            if isinstance(leg, RetrievalSource):
                legs.append(leg)
            elif isinstance(leg, str):
                legs.append(RetrievalSource(leg))
            else:
                raise ContractValidationError("retrieval_legs item must be a RetrievalSource")
        ordered_legs = tuple(sorted(set(legs), key=lambda item: item.value))
        if not ordered_legs:
            raise ContractValidationError("retrieval_legs must not be empty")
        object.__setattr__(self, "retrieval_legs", ordered_legs)

        object.__setattr__(
            self, "retriever_version", _version(self.retriever_version, "retriever_version")
        )
        object.__setattr__(
            self, "index_version", _version(self.index_version, "index_version")
        )
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, HYBRID_HIT_SCHEMA_VERSION),
        )

    def to_retrieval_hit(self) -> RetrievalHit:
        """Convert to standard P2 RetrievalHit with source=RetrievalSource.FUSED."""
        return RetrievalHit(
            candidate_id=self.candidate_id,
            source=RetrievalSource.FUSED,
            rank=self.rank,
            score=self.score,
            retriever_version=self.retriever_version,
            index_version=self.index_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "rank": self.rank,
            "score": self.score,
            "sparse_rank": self.sparse_rank,
            "sparse_score": self.sparse_score,
            "dense_rank": self.dense_rank,
            "dense_score": self.dense_score,
            "retrieval_legs": [item.value for item in self.retrieval_legs],
            "retriever_version": self.retriever_version,
            "index_version": self.index_version,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class HybridRetrievalResult:
    """Comprehensive multi-leg retrieval result suitable for offline evaluation and traces."""

    fused_hits: tuple[HybridCandidateHit, ...]
    sparse_hits: tuple[RetrievalHit, ...]
    dense_hits: tuple[RetrievalHit, ...]
    metadata: HybridRetrieverMetadata

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, HybridRetrieverMetadata):
            raise ContractValidationError("metadata must be HybridRetrieverMetadata")
        if not all(isinstance(h, HybridCandidateHit) for h in self.fused_hits):
            raise ContractValidationError("fused_hits must contain only HybridCandidateHit objects")
        if not all(isinstance(h, RetrievalHit) and h.source is RetrievalSource.SPARSE for h in self.sparse_hits):
            raise ContractValidationError("sparse_hits must contain only sparse RetrievalHit objects")
        if not all(isinstance(h, RetrievalHit) and h.source is RetrievalSource.DENSE for h in self.dense_hits):
            raise ContractValidationError("dense_hits must contain only dense RetrievalHit objects")

    def to_retrieval_hits(self) -> tuple[RetrievalHit, ...]:
        """Return fused candidates as standard RetrievalHit objects."""
        return tuple(hit.to_retrieval_hit() for hit in self.fused_hits)

    def all_retrieval_hits(self) -> tuple[RetrievalHit, ...]:
        """Return all retrieval hits (fused + sparse + dense) for trace recording."""
        fused = [hit.to_retrieval_hit() for hit in self.fused_hits]
        return tuple(sorted(
            fused + list(self.sparse_hits) + list(self.dense_hits),
            key=lambda item: (item.source.value, item.rank, item.candidate_id),
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "fused_hits": [h.to_dict() for h in self.fused_hits],
            "sparse_hits": [h.to_dict() for h in self.sparse_hits],
            "dense_hits": [h.to_dict() for h in self.dense_hits],
            "metadata": self.metadata.to_dict(),
        }


def reciprocal_rank_fusion(
    sparse_hits: Sequence[RetrievalHit],
    dense_hits: Sequence[RetrievalHit],
    *,
    rrf_k: float = DEFAULT_RRF_K,
    sparse_weight: float = DEFAULT_SPARSE_WEIGHT,
    dense_weight: float = DEFAULT_DENSE_WEIGHT,
    top_k: int = DEFAULT_TOP_K,
    retriever_version: str = HYBRID_RETRIEVER_VERSION,
    index_version: str = DEFAULT_HYBRID_INDEX_VERSION,
) -> tuple[HybridCandidateHit, ...]:
    """Combine sparse and dense candidate ranks using Reciprocal Rank Fusion.

    Parameters:
        sparse_hits: Ranked hits returned from the sparse retrieval leg.
        dense_hits: Ranked hits returned from the dense retrieval leg.
        rrf_k: Smoothing constant added to candidate rank (default: 60).
        sparse_weight: Weight multiplier for the sparse leg contribution.
        dense_weight: Weight multiplier for the dense leg contribution.
        top_k: Maximum number of fused candidate hits to return.
        retriever_version: Machine-readable version for the fused hits.
        index_version: Version identifier for the hybrid index configuration.

    Returns:
        Deterministic tuple of HybridCandidateHit sorted by descending RRF score,
        with ties broken alphabetically by candidate_id.
    """
    k = _positive_number(rrf_k, "rrf_k")
    s_weight = _non_negative_number(sparse_weight, "sparse_weight")
    d_weight = _non_negative_number(dense_weight, "dense_weight")
    limit = _positive_integer(top_k, "top_k")
    r_ver = _version(retriever_version, "retriever_version")
    i_ver = _version(index_version, "index_version")

    if s_weight == 0.0 and d_weight == 0.0:
        raise ContractValidationError("at least one retrieval leg weight must be > 0")

    sparse_by_id: dict[str, RetrievalHit] = {h.candidate_id: h for h in sparse_hits}
    dense_by_id: dict[str, RetrievalHit] = {h.candidate_id: h for h in dense_hits}

    all_candidate_ids = sorted(set(sparse_by_id) | set(dense_by_id))

    scored_candidates: list[tuple[str, float, RetrievalHit | None, RetrievalHit | None]] = []

    for candidate_id in all_candidate_ids:
        s_hit = sparse_by_id.get(candidate_id)
        d_hit = dense_by_id.get(candidate_id)

        rrf_score = 0.0
        if s_hit is not None and s_weight > 0:
            rrf_score += s_weight / (k + s_hit.rank)
        if d_hit is not None and d_weight > 0:
            rrf_score += d_weight / (k + d_hit.rank)

        if rrf_score > 0:
            scored_candidates.append((candidate_id, rrf_score, s_hit, d_hit))

    # Sort deterministically: descending score (-score), ascending candidate_id
    scored_candidates.sort(key=lambda item: (-item[1], item[0]))

    fused_hits: list[HybridCandidateHit] = []
    for rank, (candidate_id, score, s_hit, d_hit) in enumerate(
        scored_candidates[:limit], start=1
    ):
        legs: list[RetrievalSource] = []
        if s_hit is not None:
            legs.append(RetrievalSource.SPARSE)
        if d_hit is not None:
            legs.append(RetrievalSource.DENSE)

        fused_hits.append(
            HybridCandidateHit(
                candidate_id=candidate_id,
                rank=rank,
                score=score,
                sparse_rank=s_hit.rank if s_hit is not None else None,
                sparse_score=s_hit.score if s_hit is not None else None,
                dense_rank=d_hit.rank if d_hit is not None else None,
                dense_score=d_hit.score if d_hit is not None else None,
                retrieval_legs=tuple(legs),
                retriever_version=r_ver,
                index_version=i_ver,
            )
        )

    return tuple(fused_hits)


class HybridRetriever:
    """Reciprocal Rank Fusion hybrid retriever combining sparse and dense legs.

    Takes a CandidateCorpus, SparseBM25Retriever, and DenseRetriever, validates
    catalog and corpus checksum compatibility, and performs deterministic rank
    fusion.
    """

    def __init__(
        self,
        corpus: CandidateCorpus,
        sparse_retriever: SparseBM25Retriever,
        dense_retriever: DenseRetriever,
        *,
        top_k: int = DEFAULT_TOP_K,
        sparse_top_k: int = DEFAULT_TOP_K,
        dense_top_k: int = DEFAULT_TOP_K,
        rrf_k: float = DEFAULT_RRF_K,
        sparse_weight: float = DEFAULT_SPARSE_WEIGHT,
        dense_weight: float = DEFAULT_DENSE_WEIGHT,
        index_version: str = DEFAULT_HYBRID_INDEX_VERSION,
    ) -> None:
        if not isinstance(corpus, CandidateCorpus):
            raise ContractValidationError("corpus must be a CandidateCorpus")
        if not isinstance(sparse_retriever, SparseBM25Retriever):
            raise ContractValidationError("sparse_retriever must be a SparseBM25Retriever")
        if not isinstance(dense_retriever, DenseRetriever):
            raise ContractValidationError("dense_retriever must be a DenseRetriever")

        self._corpus = corpus
        self._sparse_retriever = sparse_retriever
        self._dense_retriever = dense_retriever

        self._top_k = _positive_integer(top_k, "top_k")
        self._sparse_top_k = _positive_integer(sparse_top_k, "sparse_top_k")
        self._dense_top_k = _positive_integer(dense_top_k, "dense_top_k")
        self._rrf_k = _positive_number(rrf_k, "rrf_k")
        self._sparse_weight = _non_negative_number(sparse_weight, "sparse_weight")
        self._dense_weight = _non_negative_number(dense_weight, "dense_weight")
        if self._sparse_weight == 0.0 and self._dense_weight == 0.0:
            raise ContractValidationError("at least one of sparse_weight or dense_weight must be > 0")

        self._index_version = _version(index_version, "index_version")
        self._validate_compatibility()

        checksum_payload = {
            "schema_version": HYBRID_INDEX_SCHEMA_VERSION,
            "index_version": self._index_version,
            "catalog_version": corpus.source_image_catalog_version,
            "corpus_version": corpus.corpus_version,
            "corpus_checksum": corpus.corpus_checksum,
            "sparse_index_version": sparse_retriever.metadata.index_version,
            "sparse_index_checksum": sparse_retriever.metadata.index_checksum,
            "dense_index_version": dense_retriever.metadata.index_version,
            "dense_index_checksum": dense_retriever.metadata.index_checksum,
            "retriever_version": HYBRID_RETRIEVER_VERSION,
            "rrf_k": self._rrf_k,
            "sparse_weight": self._sparse_weight,
            "dense_weight": self._dense_weight,
            "sparse_top_k": self._sparse_top_k,
            "dense_top_k": self._dense_top_k,
            "top_k": self._top_k,
        }

        self.metadata = HybridRetrieverMetadata(
            index_version=self._index_version,
            index_checksum=canonical_json_checksum(checksum_payload),
            catalog_version=corpus.source_image_catalog_version,
            corpus_version=corpus.corpus_version,
            corpus_checksum=corpus.corpus_checksum,
            sparse_index_version=sparse_retriever.metadata.index_version,
            dense_index_version=dense_retriever.metadata.index_version,
            sparse_retriever_version=sparse_retriever.metadata.retriever_version,
            dense_retriever_version=dense_retriever.metadata.retriever_version,
            retriever_version=HYBRID_RETRIEVER_VERSION,
            rrf_k=self._rrf_k,
            sparse_weight=self._sparse_weight,
            dense_weight=self._dense_weight,
            sparse_top_k=self._sparse_top_k,
            dense_top_k=self._dense_top_k,
            top_k=self._top_k,
        )

    @property
    def corpus(self) -> CandidateCorpus:
        return self._corpus

    @property
    def sparse_retriever(self) -> SparseBM25Retriever:
        return self._sparse_retriever

    @property
    def dense_retriever(self) -> DenseRetriever:
        return self._dense_retriever

    @property
    def top_k(self) -> int:
        return self._top_k

    @property
    def sparse_top_k(self) -> int:
        return self._sparse_top_k

    @property
    def dense_top_k(self) -> int:
        return self._dense_top_k

    @property
    def rrf_k(self) -> float:
        return self._rrf_k

    @property
    def sparse_weight(self) -> float:
        return self._sparse_weight

    @property
    def dense_weight(self) -> float:
        return self._dense_weight

    def _validate_compatibility(self) -> None:
        """Ensure sparse and dense retriever indices align with the corpus."""
        catalog_ver = self._corpus.source_image_catalog_version
        corpus_chk = self._corpus.corpus_checksum

        if self._sparse_retriever.metadata.catalog_version != catalog_ver:
            raise ContractValidationError("sparse index catalog_version does not match corpus")
        if self._sparse_retriever.metadata.corpus_checksum != corpus_chk:
            raise ContractValidationError("sparse index corpus_checksum does not match corpus")

        if self._dense_retriever.metadata.catalog_version != catalog_ver:
            raise ContractValidationError("dense index catalog_version does not match corpus")
        if self._dense_retriever.metadata.corpus_checksum != corpus_chk:
            raise ContractValidationError("dense index corpus_checksum does not match corpus")

    def retrieve(
        self,
        original_intent: str,
        structured_intent: StructuredIntent,
        *,
        top_k: int | None = None,
    ) -> tuple[RetrievalHit, ...]:
        """Perform hybrid retrieval and return fused candidates as standard RetrievalHit objects."""
        result = self.retrieve_detailed(original_intent, structured_intent, top_k=top_k)
        return result.to_retrieval_hits()

    def retrieve_detailed(
        self,
        original_intent: str,
        structured_intent: StructuredIntent,
        *,
        top_k: int | None = None,
        sparse_top_k: int | None = None,
        dense_top_k: int | None = None,
    ) -> HybridRetrievalResult:
        """Perform hybrid retrieval and return full multi-channel trace and hit data."""
        self._validate_compatibility()
        limit = self._top_k if top_k is None else _positive_integer(top_k, "top_k")
        s_limit = self._sparse_top_k if sparse_top_k is None else _positive_integer(sparse_top_k, "sparse_top_k")
        d_limit = self._dense_top_k if dense_top_k is None else _positive_integer(dense_top_k, "dense_top_k")

        sparse_query = build_sparse_query(original_intent, structured_intent)
        sparse_hits = self._sparse_retriever.retrieve(sparse_query, top_k=s_limit)

        dense_hits = self._dense_retriever.retrieve(
            original_intent, structured_intent, top_k=d_limit
        )

        fused_hits = reciprocal_rank_fusion(
            sparse_hits,
            dense_hits,
            rrf_k=self._rrf_k,
            sparse_weight=self._sparse_weight,
            dense_weight=self._dense_weight,
            top_k=limit,
            retriever_version=self.metadata.retriever_version,
            index_version=self.metadata.index_version,
        )

        return HybridRetrievalResult(
            fused_hits=fused_hits,
            sparse_hits=sparse_hits,
            dense_hits=dense_hits,
            metadata=self.metadata,
        )


__all__ = [
    "DEFAULT_DENSE_WEIGHT",
    "DEFAULT_HYBRID_INDEX_VERSION",
    "DEFAULT_RRF_K",
    "DEFAULT_SPARSE_WEIGHT",
    "DEFAULT_TOP_K",
    "HYBRID_HIT_SCHEMA_VERSION",
    "HYBRID_INDEX_SCHEMA_VERSION",
    "HYBRID_QUERY_REPRESENTATION_VERSION",
    "HYBRID_RETRIEVER_VERSION",
    "HybridCandidateHit",
    "HybridRetrievalResult",
    "HybridRetriever",
    "HybridRetrieverMetadata",
    "build_sparse_query",
    "reciprocal_rank_fusion",
]
