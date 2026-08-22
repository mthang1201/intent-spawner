"""Focused tests for provider-neutral P2 dense semantic retrieval."""

from __future__ import annotations

from dataclasses import replace

import pytest

from recommender.candidate_corpus import (
    CandidateCorpus,
    canonical_json_checksum,
    load_candidate_corpus,
)
from recommender.dense_retrieval import (
    DENSE_QUERY_REPRESENTATION_VERSION,
    CandidateEmbedding,
    DenseEmbeddingIndex,
    DenseRetrievalError,
    DenseRetriever,
    DeterministicFakeEmbeddingProvider,
    EmbeddingModelMetadata,
    build_dense_index,
    build_dense_query,
)
from recommender.models import ContractValidationError, RetrievalSource, StructuredIntent


MODEL = EmbeddingModelMetadata(
    model_id="fake-semantic-encoder",
    model_revision="test-revision-v1",
    dimensions=2,
    normalization="none",
)


def _small_corpus() -> CandidateCorpus:
    source = load_candidate_corpus()
    documents = tuple(sorted(source.candidates, key=lambda item: item.candidate_id)[:3])
    checksum = canonical_json_checksum([item.to_dict() for item in documents])
    return CandidateCorpus(
        candidates=documents,
        corpus_version="dense-test-corpus-v1",
        source_image_catalog_version=source.source_image_catalog_version,
        source_image_catalog_checksum=source.source_image_catalog_checksum,
        source_profile_catalog_checksum=source.source_profile_catalog_checksum,
        corpus_checksum=checksum,
    )


def _intent() -> StructuredIntent:
    return StructuredIntent(
        task_types=("model_training",),
        required_frameworks=("PyTorch",),
        preferred_libraries=("pandas",),
        normalized_query="train a pytorch model",
        extraction_confidence=1.0,
    )


def _provider_and_query(
    corpus: CandidateCorpus,
    candidate_vectors: tuple[tuple[float, ...], ...],
    query_vector: tuple[float, ...],
    *,
    model: EmbeddingModelMetadata = MODEL,
    original_intent: str = "Train my model",
    intent: StructuredIntent | None = None,
    failure: Exception | None = None,
) -> tuple[DeterministicFakeEmbeddingProvider, str]:
    structured = intent or _intent()
    query = build_dense_query(original_intent, structured)
    mapping = {
        candidate.retrieval_text: vector
        for candidate, vector in zip(corpus.candidates, candidate_vectors)
    }
    mapping[query] = query_vector
    return (
        DeterministicFakeEmbeddingProvider(
            mapping, metadata=model, failure=failure
        ),
        query,
    )


def _built_retriever(
    candidate_vectors: tuple[tuple[float, ...], ...],
    query_vector: tuple[float, ...],
    *,
    top_k: int = 3,
) -> tuple[CandidateCorpus, DenseRetriever]:
    corpus = _small_corpus()
    provider, _ = _provider_and_query(corpus, candidate_vectors, query_vector)
    index = build_dense_index(corpus, provider)
    return corpus, DenseRetriever(corpus, index, provider, top_k=top_k)


def test_expected_cosine_ordering_and_retrieval_hits():
    corpus, retriever = _built_retriever(
        ((1.0, 0.0), (0.8, 0.6), (0.0, 1.0)),
        (1.0, 0.0),
    )

    hits = retriever.retrieve("Train my model", _intent())

    assert [hit.candidate_id for hit in hits] == list(corpus.candidate_ids)
    assert [hit.rank for hit in hits] == [1, 2, 3]
    assert [hit.score for hit in hits] == pytest.approx([1.0, 0.9, 0.5])
    assert all(hit.source is RetrievalSource.DENSE for hit in hits)
    assert all(hit.index_version == retriever.metadata.index_version for hit in hits)


def test_query_representation_combines_original_and_normalized_structured_intent():
    query = build_dense_query("  Huấn luyện   mô hình PyTorch  ", _intent())

    assert f"Query representation: {DENSE_QUERY_REPRESENTATION_VERSION}" in query
    assert "Original intent: Huấn luyện mô hình PyTorch" in query
    assert '"required_frameworks":["pytorch"]' in query
    assert '"normalized_query":"train a pytorch model"' in query
    assert "fake-semantic-encoder" not in query


@pytest.mark.parametrize(
    "bad_query_vector",
    [(0.0, 0.0), (float("nan"), 0.0), (float("inf"), 0.0)],
)
def test_zero_and_invalid_query_vectors_fail_safely(bad_query_vector):
    _, retriever = _built_retriever(
        ((1.0, 0.0), (0.8, 0.6), (0.0, 1.0)),
        bad_query_vector,
    )

    with pytest.raises(DenseRetrievalError, match="query vector"):
        retriever.retrieve("Train my model", _intent())


@pytest.mark.parametrize(
    "bad_candidate_vector",
    [(0.0, 0.0), (float("nan"), 0.0), (float("inf"), 0.0)],
)
def test_zero_and_invalid_candidate_vectors_are_rejected(bad_candidate_vector):
    corpus = _small_corpus()
    provider, _ = _provider_and_query(
        corpus,
        (bad_candidate_vector, (1.0, 0.0), (0.0, 1.0)),
        (1.0, 0.0),
    )

    with pytest.raises(ContractValidationError, match="candidate .* vector"):
        build_dense_index(corpus, provider)


def test_query_vector_dimension_mismatch_is_rejected():
    _, retriever = _built_retriever(
        ((1.0, 0.0), (0.8, 0.6), (0.0, 1.0)),
        (1.0, 0.0, 0.0),
    )

    with pytest.raises(DenseRetrievalError, match="dimension mismatch"):
        retriever.retrieve("Train my model", _intent())


@pytest.mark.parametrize(
    "incompatible_model",
    [
        EmbeddingModelMetadata("other-model", "test-revision-v1", 2, "none"),
        EmbeddingModelMetadata("fake-semantic-encoder", "revision-v2", 2, "none"),
        EmbeddingModelMetadata("fake-semantic-encoder", "test-revision-v1", 3, "none"),
        EmbeddingModelMetadata("fake-semantic-encoder", "test-revision-v1", 2, "l2"),
    ],
)
def test_incompatible_provider_model_index_metadata_is_rejected(incompatible_model):
    corpus = _small_corpus()
    provider, _ = _provider_and_query(
        corpus,
        ((1.0, 0.0), (0.8, 0.6), (0.0, 1.0)),
        (1.0, 0.0),
    )
    index = build_dense_index(corpus, provider)
    incompatible, _ = _provider_and_query(
        corpus,
        ((1.0, 0.0), (0.8, 0.6), (0.0, 1.0)),
        (1.0, 0.0),
        model=incompatible_model,
    )

    with pytest.raises(ContractValidationError, match="incompatible"):
        DenseRetriever(corpus, index, incompatible)


def test_stale_catalog_metadata_is_rejected():
    corpus = _small_corpus()
    stale_corpus = replace(corpus, source_image_catalog_version="stale-catalog-v1")
    provider, _ = _provider_and_query(
        stale_corpus,
        ((1.0, 0.0), (0.8, 0.6), (0.0, 1.0)),
        (1.0, 0.0),
    )
    stale_index = build_dense_index(stale_corpus, provider)

    with pytest.raises(ContractValidationError, match="catalog_version is stale"):
        DenseRetriever(corpus, stale_index, provider)


def test_tampered_index_checksum_is_rejected():
    corpus = _small_corpus()
    provider, _ = _provider_and_query(
        corpus,
        ((1.0, 0.0), (0.8, 0.6), (0.0, 1.0)),
        (1.0, 0.0),
    )
    index = build_dense_index(corpus, provider)
    tampered_metadata = replace(index.metadata, index_checksum="a" * 64)

    with pytest.raises(ContractValidationError, match="checksum mismatch"):
        DenseEmbeddingIndex(
            metadata=tampered_metadata,
            embeddings=index.embeddings,
        )


def test_provider_failure_is_wrapped_without_fallback_or_partial_hits():
    corpus = _small_corpus()
    good_provider, _ = _provider_and_query(
        corpus,
        ((1.0, 0.0), (0.8, 0.6), (0.0, 1.0)),
        (1.0, 0.0),
    )
    index = build_dense_index(corpus, good_provider)
    failing_provider, _ = _provider_and_query(
        corpus,
        ((1.0, 0.0), (0.8, 0.6), (0.0, 1.0)),
        (1.0, 0.0),
        failure=TimeoutError("simulated timeout"),
    )
    retriever = DenseRetriever(corpus, index, failing_provider)

    with pytest.raises(DenseRetrievalError, match="query embedding") as exc_info:
        retriever.retrieve("Train my model", _intent())
    assert isinstance(exc_info.value.__cause__, TimeoutError)


def test_deterministic_tie_handling_uses_candidate_id():
    corpus, retriever = _built_retriever(
        ((1.0, 0.0), (1.0, 0.0), (1.0, 0.0)),
        (1.0, 0.0),
    )

    first = retriever.retrieve("Train my model", _intent())
    second = retriever.retrieve("Train my model", _intent())

    assert first == second
    assert tuple(hit.candidate_id for hit in first) == tuple(sorted(corpus.candidate_ids))
    assert [hit.score for hit in first] == [1.0, 1.0, 1.0]


def test_dense_index_records_required_provenance():
    corpus = _small_corpus()
    provider, _ = _provider_and_query(
        corpus,
        ((1.0, 0.0), (0.8, 0.6), (0.0, 1.0)),
        (1.0, 0.0),
    )
    index = build_dense_index(corpus, provider)
    retriever = DenseRetriever(corpus, index, provider)
    metadata = retriever.metadata

    assert metadata.model_id == MODEL.model_id
    assert metadata.model_revision == MODEL.model_revision
    assert metadata.dimensions == MODEL.dimensions
    assert metadata.normalization == MODEL.normalization
    assert metadata.catalog_version == corpus.source_image_catalog_version
    assert metadata.corpus_checksum == corpus.corpus_checksum
    assert len(metadata.index_checksum) == 64
    assert all(isinstance(item, CandidateEmbedding) for item in index.embeddings)
