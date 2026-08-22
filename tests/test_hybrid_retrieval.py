"""Focused tests for P2 Reciprocal Rank Fusion hybrid retrieval."""

from __future__ import annotations

from dataclasses import replace
import pytest

from recommender.candidate_corpus import (
    CandidateCorpus,
    canonical_json_checksum,
    load_candidate_corpus,
)
from recommender.dense_retrieval import (
    DenseRetriever,
    DeterministicFakeEmbeddingProvider,
    EmbeddingModelMetadata,
    build_dense_index,
    build_dense_query,
)
from recommender.hybrid_retrieval import (
    DEFAULT_DENSE_WEIGHT,
    DEFAULT_HYBRID_INDEX_VERSION,
    DEFAULT_RRF_K,
    DEFAULT_SPARSE_WEIGHT,
    DEFAULT_TOP_K,
    HYBRID_HIT_SCHEMA_VERSION,
    HYBRID_INDEX_SCHEMA_VERSION,
    HYBRID_RETRIEVER_VERSION,
    HybridCandidateHit,
    HybridRetrievalResult,
    HybridRetriever,
    HybridRetrieverMetadata,
    build_sparse_query,
    reciprocal_rank_fusion,
)
from recommender.models import (
    ContractValidationError,
    RetrievalHit,
    RetrievalSource,
    StructuredIntent,
    TaskType,
)
from recommender.sparse_retrieval import (
    SPARSE_RETRIEVER_VERSION,
    SparseBM25Retriever,
)


MODEL = EmbeddingModelMetadata(
    model_id="fake-semantic-encoder",
    model_revision="test-revision-v1",
    dimensions=2,
    normalization="none",
)


def _small_corpus() -> CandidateCorpus:
    source = load_candidate_corpus()
    documents = tuple(sorted(source.candidates, key=lambda item: item.candidate_id)[:4])
    checksum = canonical_json_checksum([item.to_dict() for item in documents])
    return CandidateCorpus(
        candidates=documents,
        corpus_version="hybrid-test-corpus-v1",
        source_image_catalog_version=source.source_image_catalog_version,
        source_image_catalog_checksum=source.source_image_catalog_checksum,
        source_profile_catalog_checksum=source.source_profile_catalog_checksum,
        corpus_checksum=checksum,
    )


def _intent() -> StructuredIntent:
    return StructuredIntent(
        task_types=(TaskType.MODEL_TRAINING,),
        required_frameworks=("PyTorch",),
        preferred_libraries=("pandas",),
        required_features=("cuda",),
        normalized_query="train a pytorch model",
        extraction_confidence=1.0,
    )


def _hit(
    candidate_id: str,
    source: RetrievalSource,
    rank: int,
    score: float,
    *,
    retriever_version: str = "test-retriever-v1",
    index_version: str = "test-index-v1",
) -> RetrievalHit:
    return RetrievalHit(
        candidate_id=candidate_id,
        source=source,
        rank=rank,
        score=score,
        retriever_version=retriever_version,
        index_version=index_version,
    )


def _setup_retrievers(
    corpus: CandidateCorpus,
    candidate_vectors: tuple[tuple[float, ...], ...],
    query_vector: tuple[float, ...],
    *,
    original_intent: str = "train pytorch model",
    structured_intent: StructuredIntent | None = None,
) -> tuple[SparseBM25Retriever, DenseRetriever]:
    intent = structured_intent or _intent()
    dense_query = build_dense_query(original_intent, intent)
    mapping = {
        candidate.retrieval_text: vector
        for candidate, vector in zip(corpus.candidates, candidate_vectors)
    }
    mapping[dense_query] = query_vector
    provider = DeterministicFakeEmbeddingProvider(mapping, metadata=MODEL)
    dense_index = build_dense_index(corpus, provider)
    dense_retriever = DenseRetriever(corpus, dense_index, provider, top_k=len(corpus))
    sparse_retriever = SparseBM25Retriever(corpus, top_k=len(corpus))
    return sparse_retriever, dense_retriever


def test_rrf_combines_disjoint_and_overlapping_candidates():
    sparse_hits = (
        _hit("cand-a", RetrievalSource.SPARSE, 1, 15.0),
        _hit("cand-b", RetrievalSource.SPARSE, 2, 8.5),
    )
    dense_hits = (
        _hit("cand-b", RetrievalSource.DENSE, 1, 0.95),
        _hit("cand-c", RetrievalSource.DENSE, 2, 0.70),
    )

    fused = reciprocal_rank_fusion(
        sparse_hits,
        dense_hits,
        rrf_k=60.0,
        sparse_weight=1.0,
        dense_weight=1.0,
        top_k=5,
    )

    assert len(fused) == 3
    # cand-b appears in both: sparse rank 2, dense rank 1 -> 1/62 + 1/61
    expected_score_b = 1.0 / 62.0 + 1.0 / 61.0
    # cand-a appears only in sparse: rank 1 -> 1/61
    expected_score_a = 1.0 / 61.0
    # cand-c appears only in dense: rank 2 -> 1/62
    expected_score_c = 1.0 / 62.0

    assert fused[0].candidate_id == "cand-b"
    assert fused[0].rank == 1
    assert fused[0].score == pytest.approx(expected_score_b)
    assert fused[0].sparse_rank == 2
    assert fused[0].sparse_score == 8.5
    assert fused[0].dense_rank == 1
    assert fused[0].dense_score == 0.95
    assert fused[0].retrieval_legs == (RetrievalSource.DENSE, RetrievalSource.SPARSE)

    assert fused[1].candidate_id == "cand-a"
    assert fused[1].rank == 2
    assert fused[1].score == pytest.approx(expected_score_a)
    assert fused[1].sparse_rank == 1
    assert fused[1].sparse_score == 15.0
    assert fused[1].dense_rank is None
    assert fused[1].dense_score is None
    assert fused[1].retrieval_legs == (RetrievalSource.SPARSE,)

    assert fused[2].candidate_id == "cand-c"
    assert fused[2].rank == 3
    assert fused[2].score == pytest.approx(expected_score_c)
    assert fused[2].sparse_rank is None
    assert fused[2].sparse_score is None
    assert fused[2].dense_rank == 2
    assert fused[2].dense_score == 0.70
    assert fused[2].retrieval_legs == (RetrievalSource.DENSE,)


def test_rrf_is_invariant_to_incompatible_raw_score_scales():
    # Sparse raw score is 10,000 vs Dense score of 0.001
    sparse_hits = (
        _hit("cand-x", RetrievalSource.SPARSE, 1, 10000.0),
        _hit("cand-y", RetrievalSource.SPARSE, 2, 5000.0),
    )
    dense_hits = (
        _hit("cand-y", RetrievalSource.DENSE, 1, 0.9),
        _hit("cand-x", RetrievalSource.DENSE, 2, 0.001),
    )

    fused = reciprocal_rank_fusion(sparse_hits, dense_hits, rrf_k=60.0)

    # Both candidates have sparse rank 1 & dense rank 2, or sparse rank 2 & dense rank 1
    # cand-x score: 1/61 + 1/62
    # cand-y score: 1/62 + 1/61
    # Exactly equal RRF scores regardless of whether raw BM25 is 10000.0 or cosine is 0.001!
    assert fused[0].score == pytest.approx(fused[1].score)
    # Tie broken by candidate_id: cand-x before cand-y
    assert fused[0].candidate_id == "cand-x"
    assert fused[1].candidate_id == "cand-y"


def test_deterministic_tie_breaking_uses_ascending_candidate_id():
    sparse_hits = (
        _hit("beta-candidate", RetrievalSource.SPARSE, 1, 10.0),
        _hit("alpha-candidate", RetrievalSource.SPARSE, 1, 10.0),
        _hit("gamma-candidate", RetrievalSource.SPARSE, 1, 10.0),
    )
    dense_hits = ()

    fused = reciprocal_rank_fusion(sparse_hits, dense_hits, rrf_k=60.0)

    assert [hit.candidate_id for hit in fused] == [
        "alpha-candidate",
        "beta-candidate",
        "gamma-candidate",
    ]
    assert [hit.rank for hit in fused] == [1, 2, 3]
    assert all(hit.score == pytest.approx(1.0 / 61.0) for hit in fused)


def test_configurable_rrf_parameters_and_weights():
    sparse_hits = (_hit("cand-s", RetrievalSource.SPARSE, 1, 10.0),)
    dense_hits = (_hit("cand-d", RetrievalSource.DENSE, 1, 1.0),)

    # Weighted: sparse weight 3.0, dense weight 1.0, k=20
    fused = reciprocal_rank_fusion(
        sparse_hits,
        dense_hits,
        rrf_k=20.0,
        sparse_weight=3.0,
        dense_weight=1.0,
    )

    # cand-s score = 3.0 / (20 + 1) = 3/21 = 1/7 ~= 0.142857
    # cand-d score = 1.0 / (20 + 1) = 1/21 ~= 0.047619
    assert fused[0].candidate_id == "cand-s"
    assert fused[0].score == pytest.approx(3.0 / 21.0)
    assert fused[1].candidate_id == "cand-d"
    assert fused[1].score == pytest.approx(1.0 / 21.0)


def test_hybrid_retriever_end_to_end_with_corpus():
    corpus = _small_corpus()
    # 4 candidates in corpus:
    # Set vectors so dense retriever returns them in reverse order
    vectors = ((0.1, 0.9), (0.3, 0.7), (0.6, 0.4), (1.0, 0.0))
    query_vec = (1.0, 0.0)

    sparse_retriever, dense_retriever = _setup_retrievers(corpus, vectors, query_vec)
    hybrid = HybridRetriever(
        corpus,
        sparse_retriever,
        dense_retriever,
        top_k=3,
        sparse_top_k=2,
        dense_top_k=2,
        rrf_k=60.0,
    )

    intent = _intent()
    hits = hybrid.retrieve("train pytorch model", intent)

    assert len(hits) <= 3
    assert all(isinstance(hit, RetrievalHit) for hit in hits)
    assert all(hit.source is RetrievalSource.FUSED for hit in hits)
    assert [hit.rank for hit in hits] == list(range(1, len(hits) + 1))
    assert all(hit.score > 0 for hit in hits)


def test_detailed_retrieval_result_and_trace_export():
    corpus = _small_corpus()
    vectors = ((1.0, 0.0), (0.8, 0.6), (0.5, 0.5), (0.0, 1.0))
    query_vec = (1.0, 0.0)

    sparse_retriever, dense_retriever = _setup_retrievers(corpus, vectors, query_vec)
    hybrid = HybridRetriever(corpus, sparse_retriever, dense_retriever, top_k=4)

    intent = _intent()
    detailed = hybrid.retrieve_detailed("train pytorch model", intent)

    assert isinstance(detailed, HybridRetrievalResult)
    assert len(detailed.fused_hits) > 0
    assert len(detailed.dense_hits) > 0

    all_hits = detailed.all_retrieval_hits()
    assert all(isinstance(h, RetrievalHit) for h in all_hits)
    sources = {h.source for h in all_hits}
    assert RetrievalSource.FUSED in sources
    assert RetrievalSource.DENSE in sources

    serialized = detailed.to_dict()
    assert "fused_hits" in serialized
    assert "sparse_hits" in serialized
    assert "dense_hits" in serialized
    assert "metadata" in serialized
    assert serialized["metadata"]["retriever_version"] == HYBRID_RETRIEVER_VERSION


def test_build_sparse_query_extracts_structured_terms():
    intent = StructuredIntent(
        task_types=(TaskType.DATA_ANALYSIS, TaskType.MODEL_TRAINING),
        required_frameworks=("PyTorch",),
        preferred_frameworks=("TensorFlow",),
        required_libraries=("pandas",),
        preferred_libraries=("NumPy",),
        required_features=("cuda",),
        preferred_features=("jupyterlab",),
        normalized_query="train neural network",
    )

    query = build_sparse_query("Huấn luyện mô hình", intent)

    assert "Huấn luyện mô hình" in query
    assert "train neural network" in query
    assert "pytorch" in query
    assert "tensorflow" in query
    assert "pandas" in query
    assert "numpy" in query
    assert "cuda" in query
    assert "jupyterlab" in query


def test_hybrid_retriever_rejects_incompatible_corpus_or_index_versions():
    corpus = _small_corpus()
    stale_corpus = replace(corpus, source_image_catalog_version="stale-catalog-v1")
    vectors = ((1.0, 0.0), (0.8, 0.6), (0.5, 0.5), (0.0, 1.0))
    query_vec = (1.0, 0.0)

    sparse_retriever, dense_retriever = _setup_retrievers(stale_corpus, vectors, query_vec)

    with pytest.raises(ContractValidationError, match="catalog_version does not match"):
        HybridRetriever(corpus, sparse_retriever, dense_retriever)


def test_hybrid_retriever_metadata_validations():
    with pytest.raises(ContractValidationError, match="rrf_k must be"):
        HybridRetrieverMetadata(
            index_version=DEFAULT_HYBRID_INDEX_VERSION,
            index_checksum="a" * 64,
            catalog_version="cat-v1",
            corpus_version="corp-v1",
            corpus_checksum="b" * 64,
            sparse_index_version="sp-v1",
            dense_index_version="de-v1",
            sparse_retriever_version=SPARSE_RETRIEVER_VERSION,
            dense_retriever_version="dense-v1",
            rrf_k=-5.0,
        )

    with pytest.raises(ContractValidationError, match="rrf_k must be positive"):
        HybridRetrieverMetadata(
            index_version=DEFAULT_HYBRID_INDEX_VERSION,
            index_checksum="a" * 64,
            catalog_version="cat-v1",
            corpus_version="corp-v1",
            corpus_checksum="b" * 64,
            sparse_index_version="sp-v1",
            dense_index_version="de-v1",
            sparse_retriever_version=SPARSE_RETRIEVER_VERSION,
            dense_retriever_version="dense-v1",
            rrf_k=0.0,
        )

    with pytest.raises(ContractValidationError, match="at least one retrieval leg weight"):
        HybridRetrieverMetadata(
            index_version=DEFAULT_HYBRID_INDEX_VERSION,
            index_checksum="a" * 64,
            catalog_version="cat-v1",
            corpus_version="corp-v1",
            corpus_checksum="b" * 64,
            sparse_index_version="sp-v1",
            dense_index_version="de-v1",
            sparse_retriever_version=SPARSE_RETRIEVER_VERSION,
            dense_retriever_version="dense-v1",
            sparse_weight=0.0,
            dense_weight=0.0,
        )


def test_hybrid_candidate_hit_validations():
    with pytest.raises(ContractValidationError, match="sparse_score is required"):
        HybridCandidateHit(
            candidate_id="cand-1",
            rank=1,
            score=0.5,
            sparse_rank=1,
            sparse_score=None,
            retrieval_legs=(RetrievalSource.SPARSE,),
        )

    with pytest.raises(ContractValidationError, match="retrieval_legs must not be empty"):
        HybridCandidateHit(
            candidate_id="cand-1",
            rank=1,
            score=0.5,
            retrieval_legs=(),
        )
