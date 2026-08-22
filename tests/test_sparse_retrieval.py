"""Focused tests for deterministic P2 sparse lexical retrieval."""

from __future__ import annotations

from dataclasses import replace

import pytest

from recommender.candidate_corpus import (
    CandidateCorpus,
    canonical_json_checksum,
    load_candidate_corpus,
)
from recommender.models import ContractValidationError, RetrievalHit, RetrievalSource
from recommender.sparse_retrieval import (
    SPARSE_RETRIEVER_VERSION,
    SPARSE_TOKENIZER_VERSION,
    SparseBM25Retriever,
    tokenize_sparse_text,
)


def _corpus_with_texts(texts: tuple[str, ...], *, reverse: bool = False) -> CandidateCorpus:
    source = load_candidate_corpus()
    base_documents = tuple(sorted(source.candidates, key=lambda item: item.candidate_id))
    documents = tuple(
        replace(document, retrieval_text=text)
        for document, text in zip(base_documents, texts)
    )
    if reverse:
        documents = tuple(reversed(documents))
    checksum = canonical_json_checksum(
        [item.to_dict() for item in sorted(documents, key=lambda item: item.candidate_id)]
    )
    return CandidateCorpus(
        candidates=documents,
        corpus_version="sparse-test-corpus-v1",
        source_image_catalog_version=source.source_image_catalog_version,
        source_image_catalog_checksum=source.source_image_catalog_checksum,
        source_profile_catalog_checksum=source.source_profile_catalog_checksum,
        corpus_checksum=checksum,
    )


@pytest.mark.parametrize(
    ("term", "image_fragment"),
    [
        ("PyTorch", "pytorch-deep-learning"),
        ("TensorFlow", "tensorflow-deep-learning"),
        ("scipy", "scipy-data-science"),
    ],
)
def test_exact_framework_and_library_terms_rank_matching_images(term, image_fragment):
    retriever = SparseBM25Retriever(load_candidate_corpus(), top_k=3)

    hits = retriever.retrieve(term)

    assert len(hits) == 3
    assert all(image_fragment in hit.candidate_id for hit in hits)
    assert all(isinstance(hit, RetrievalHit) for hit in hits)
    assert all(hit.source is RetrievalSource.SPARSE for hit in hits)
    assert [hit.rank for hit in hits] == [1, 2, 3]


def test_repeated_query_terms_contribute_repeatable_bm25_weight():
    retriever = SparseBM25Retriever(load_candidate_corpus(), top_k=3)

    single = retriever.retrieve("pytorch")
    repeated = retriever.retrieve("pytorch pytorch")

    assert [hit.candidate_id for hit in repeated] == [
        hit.candidate_id for hit in single
    ]
    assert [hit.score for hit in repeated] == pytest.approx(
        [2 * hit.score for hit in single]
    )


@pytest.mark.parametrize("query", ["", "   ", "--- !!!"])
def test_empty_or_tokenless_query_returns_no_hits(query):
    assert SparseBM25Retriever(load_candidate_corpus()).retrieve(query) == ()


def test_unicode_tokenization_and_vietnamese_retrieval_are_stable():
    decomposed = "PHA\u0302N TI\u0301CH dữ liệu tiếng Việt"
    assert tokenize_sparse_text(decomposed) == (
        "phân",
        "tích",
        "dữ",
        "liệu",
        "tiếng",
        "việt",
    )
    corpus = _corpus_with_texts(
        (
            "Phân tích dữ liệu tiếng Việt với pandas",
            "Huấn luyện mô hình học sâu",
        )
    )

    hits = SparseBM25Retriever(corpus).retrieve("phân tích dữ liệu")

    assert hits[0].candidate_id == corpus.candidate_ids[0]
    assert hits[0].score > 0


def test_ties_and_index_provenance_are_deterministic_by_candidate_id():
    texts = ("identical technical text cuda",) * 3
    first_corpus = _corpus_with_texts(texts)
    second_corpus = _corpus_with_texts(texts, reverse=True)
    first = SparseBM25Retriever(first_corpus, top_k=3)
    second = SparseBM25Retriever(second_corpus, top_k=3)

    first_hits = first.retrieve("cuda")
    second_hits = second.retrieve("cuda")

    expected_ids = tuple(sorted(first_corpus.candidate_ids))
    assert tuple(hit.candidate_id for hit in first_hits) == expected_ids
    assert first_hits == second_hits
    assert first.metadata.index_checksum == second.metadata.index_checksum
    assert first.metadata.tokenizer_version == SPARSE_TOKENIZER_VERSION
    assert first.metadata.retriever_version == SPARSE_RETRIEVER_VERSION
    assert first.metadata.catalog_version == first_corpus.source_image_catalog_version
    assert len(first.metadata.index_checksum) == 64


@pytest.mark.parametrize("top_k", [0, -1, True, 1.5])
def test_top_k_must_be_a_positive_integer(top_k):
    with pytest.raises(ContractValidationError, match="top_k"):
        SparseBM25Retriever(load_candidate_corpus(), top_k=top_k)
