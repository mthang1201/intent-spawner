"""Deterministic dependency-light sparse retrieval for the P2 pipeline.

This module indexes only administrator-approved ``CandidateDocument.retrieval_text``
values.  It implements ordinary BM25 term scoring; it deliberately contains no
P1 keyword rules, environment-selection policy, or candidate-specific boosts.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re
import unicodedata

from .candidate_corpus import CandidateCorpus, canonical_json_checksum
from .models import (
    ContractValidationError,
    RetrievalHit,
    RetrievalSource,
    _schema_version,
    _version,
)


SPARSE_TOKENIZER_VERSION = "unicode-alphanumeric-tokenizer-v1"
SPARSE_RETRIEVER_VERSION = "bm25-okapi-retriever-v1"
SPARSE_INDEX_SCHEMA_VERSION = "sparse-retrieval-index-v1"
DEFAULT_SPARSE_INDEX_VERSION = "environment-sparse-index-v1"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractValidationError(f"{label} must be a positive integer")
    return value


def tokenize_sparse_text(text: str) -> tuple[str, ...]:
    """Tokenize text deterministically using Unicode letter/number boundaries.

    NFKC and case-folding make matching stable across compatible Unicode forms.
    Letter, number, and attached combining-mark code points are retained, which
    handles both English technical names and precomposed/decomposed Vietnamese.
    Punctuation is a boundary rather than a source of hand-written keyword rules.
    """
    if not isinstance(text, str):
        raise ContractValidationError("sparse retrieval text must be a string")

    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    current: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if category[0] in {"L", "N"} or (category[0] == "M" and current):
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


@dataclass(frozen=True, slots=True)
class SparseIndexMetadata:
    """Version and checksum provenance for one immutable sparse index."""

    index_version: str
    index_checksum: str
    catalog_version: str
    corpus_version: str
    corpus_checksum: str
    tokenizer_version: str = SPARSE_TOKENIZER_VERSION
    retriever_version: str = SPARSE_RETRIEVER_VERSION
    k1: float = 1.5
    b: float = 0.75
    schema_version: str = SPARSE_INDEX_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "index_version",
            "catalog_version",
            "corpus_version",
            "tokenizer_version",
            "retriever_version",
        ):
            object.__setattr__(self, name, _version(getattr(self, name), name))
        for name in ("index_checksum", "corpus_checksum"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
                raise ContractValidationError(
                    f"{name} must be a lowercase SHA-256 digest"
                )
        if (
            isinstance(self.k1, bool)
            or not isinstance(self.k1, (int, float))
            or not math.isfinite(self.k1)
            or self.k1 <= 0
        ):
            raise ContractValidationError("k1 must be a finite positive number")
        if (
            isinstance(self.b, bool)
            or not isinstance(self.b, (int, float))
            or not math.isfinite(self.b)
            or not 0 <= self.b <= 1
        ):
            raise ContractValidationError("b must be a finite number from 0 to 1")
        object.__setattr__(self, "k1", float(self.k1))
        object.__setattr__(self, "b", float(self.b))
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, SPARSE_INDEX_SCHEMA_VERSION),
        )


class SparseBM25Retriever:
    """Immutable BM25 index over approved candidate retrieval documents."""

    def __init__(
        self,
        corpus: CandidateCorpus,
        *,
        top_k: int = 10,
        index_version: str = DEFAULT_SPARSE_INDEX_VERSION,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        if not isinstance(corpus, CandidateCorpus):
            raise ContractValidationError("corpus must be a CandidateCorpus")
        self._top_k = _positive_integer(top_k, "top_k")
        index_version = _version(index_version, "index_version")
        if (
            isinstance(k1, bool)
            or not isinstance(k1, (int, float))
            or not math.isfinite(k1)
            or k1 <= 0
        ):
            raise ContractValidationError("k1 must be a finite positive number")
        if (
            isinstance(b, bool)
            or not isinstance(b, (int, float))
            or not math.isfinite(b)
            or not 0 <= b <= 1
        ):
            raise ContractValidationError("b must be a finite number from 0 to 1")
        self._k1 = float(k1)
        self._b = float(b)

        ordered = tuple(sorted(corpus.candidates, key=lambda item: item.candidate_id))
        self._term_frequencies = {
            candidate.candidate_id: Counter(tokenize_sparse_text(candidate.retrieval_text))
            for candidate in ordered
        }
        self._document_lengths = {
            candidate_id: sum(frequencies.values())
            for candidate_id, frequencies in self._term_frequencies.items()
        }
        document_count = len(ordered)
        self._average_document_length = (
            sum(self._document_lengths.values()) / document_count
            if document_count
            else 0.0
        )
        document_frequencies: Counter[str] = Counter()
        for frequencies in self._term_frequencies.values():
            document_frequencies.update(frequencies.keys())
        self._inverse_document_frequencies = {
            term: math.log(
                1.0
                + (document_count - frequency + 0.5) / (frequency + 0.5)
            )
            for term, frequency in document_frequencies.items()
        }

        checksum_payload = {
            "schema_version": SPARSE_INDEX_SCHEMA_VERSION,
            "index_version": index_version,
            "catalog_version": corpus.source_image_catalog_version,
            "corpus_version": corpus.corpus_version,
            "corpus_checksum": corpus.corpus_checksum,
            "tokenizer_version": SPARSE_TOKENIZER_VERSION,
            "retriever_version": SPARSE_RETRIEVER_VERSION,
            "k1": self._k1,
            "b": self._b,
            "documents": [
                {
                    "candidate_id": candidate_id,
                    "term_frequencies": dict(sorted(frequencies.items())),
                }
                for candidate_id, frequencies in self._term_frequencies.items()
            ],
        }
        self.metadata = SparseIndexMetadata(
            index_version=index_version,
            index_checksum=canonical_json_checksum(checksum_payload),
            catalog_version=corpus.source_image_catalog_version,
            corpus_version=corpus.corpus_version,
            corpus_checksum=corpus.corpus_checksum,
            k1=self._k1,
            b=self._b,
        )

    @property
    def top_k(self) -> int:
        return self._top_k

    def retrieve(self, query: str, *, top_k: int | None = None) -> tuple[RetrievalHit, ...]:
        """Return positive-scoring BM25 hits ordered by score then candidate ID."""
        limit = self._top_k if top_k is None else _positive_integer(top_k, "top_k")
        query_frequencies = Counter(tokenize_sparse_text(query))
        if not query_frequencies or not self._term_frequencies:
            return ()

        scored: list[tuple[str, float]] = []
        for candidate_id, frequencies in self._term_frequencies.items():
            document_length = self._document_lengths[candidate_id]
            score = 0.0
            for term, query_frequency in query_frequencies.items():
                term_frequency = frequencies.get(term, 0)
                inverse_document_frequency = self._inverse_document_frequencies.get(term)
                if not term_frequency or inverse_document_frequency is None:
                    continue
                length_normalization = (
                    document_length / self._average_document_length
                    if self._average_document_length
                    else 0.0
                )
                denominator = term_frequency + self._k1 * (
                    1.0 - self._b + self._b * length_normalization
                )
                score += query_frequency * inverse_document_frequency * (
                    term_frequency * (self._k1 + 1.0) / denominator
                )
            if score > 0:
                scored.append((candidate_id, score))

        scored.sort(key=lambda item: (-item[1], item[0]))
        return tuple(
            RetrievalHit(
                candidate_id=candidate_id,
                source=RetrievalSource.SPARSE,
                rank=rank,
                score=score,
                retriever_version=self.metadata.retriever_version,
                index_version=self.metadata.index_version,
            )
            for rank, (candidate_id, score) in enumerate(scored[:limit], start=1)
        )


__all__ = [
    "DEFAULT_SPARSE_INDEX_VERSION",
    "SPARSE_INDEX_SCHEMA_VERSION",
    "SPARSE_RETRIEVER_VERSION",
    "SPARSE_TOKENIZER_VERSION",
    "SparseBM25Retriever",
    "SparseIndexMetadata",
    "tokenize_sparse_text",
]
