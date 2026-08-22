"""Complete P2 backend composition behind the existing Recommender protocol.

The pipeline resolves every retrieved and selected identifier through one
administrator-owned CandidateCorpus.  Only the resolved CandidateDocument can
construct the final SpawnRecommendation, which remains subject to the existing
PolicyValidator in the preview runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import os
import time
from typing import Any

from .candidate_corpus import CandidateCorpus, CandidateDocument, build_candidate_corpus
from .constraint_evaluator import ConstraintEvaluator, ConstraintRankingResult
from .dense_retrieval import (
    DenseRetrievalError,
    DenseRetriever,
    EmbeddingProvider,
    build_dense_index,
)
from .external_llm import LLMClientError
from .hybrid_retrieval import HybridRetrievalResult, HybridRetriever
from .local_embeddings import LocalFeatureHashEmbeddingProvider
from .local_structured_intent import LocalStructuredIntentExtractor
from .models import (
    STRUCTURED_INTENT_SCHEMA_VERSION,
    ContractValidationError,
    EnvironmentCandidate,
    ExtractionMode,
    RecommendationRequest,
    RecommendationTrace,
    SpawnRecommendation,
    StructuredIntent,
)
from .reliability import (
    RecommendationCallState,
    RecommendationMetadata,
    RecommendationResult,
)
from .rule_based import (
    DEFAULT_CATALOG_PATH,
    RuleBasedRecommender,
    load_image_catalog,
    validate_image_catalog,
)
from .sparse_retrieval import SparseBM25Retriever
from .structured_intent import (
    StructuredIntentExtractor,
    create_primary_structured_intent_extractor,
)


P2_BACKEND_NAME = "p2"
P2_BACKEND_VERSION = "p2-hybrid-v1.0.0"
P2_PIPELINE_VERSION = "p2-pipeline-v1.0.0"
P2_OPERATIONAL_PROVENANCE_SCHEMA_VERSION = "p2-operational-provenance-v1"

P2_EXTRACTOR_MODE_ENV_VAR = "P2_STRUCTURED_EXTRACTOR"
P2_TOP_K_ENV_VAR = "P2_TOP_K"
P2_SPARSE_TOP_K_ENV_VAR = "P2_SPARSE_TOP_K"
P2_DENSE_TOP_K_ENV_VAR = "P2_DENSE_TOP_K"
P2_RRF_K_ENV_VAR = "P2_RRF_K"
P2_SPARSE_WEIGHT_ENV_VAR = "P2_SPARSE_WEIGHT"
P2_DENSE_WEIGHT_ENV_VAR = "P2_DENSE_WEIGHT"
P2_TOTAL_TIMEOUT_ENV_VAR = "P2_TOTAL_TIMEOUT"
P2_MAX_CONCURRENT_ENV_VAR = "P2_MAX_CONCURRENT_RECOMMENDATIONS"

P2_MANUAL_OVERRIDE_FALLBACKS = frozenset(
    {"no_feasible_candidate", "unsupported_catalog"}
)


class P2PipelineError(RuntimeError):
    """A P2 stage failed before a trusted recommendation could be produced."""


class P2FallbackError(P2PipelineError):
    """Both P2 and its deterministic trusted fallback failed."""


@dataclass(frozen=True, slots=True)
class P2Config:
    """Versioned runtime configuration for deterministic P2 retrieval/ranking."""

    extractor_mode: str = "local"
    top_k: int = 10
    sparse_top_k: int = 10
    dense_top_k: int = 10
    rrf_k: float = 60.0
    sparse_weight: float = 1.0
    dense_weight: float = 1.0
    total_timeout: float = 30.0
    max_concurrent_recommendations: int = 4
    config_version: str = "p2-config-v1.0.0"

    def __post_init__(self) -> None:
        if self.extractor_mode not in {"local", "llm"}:
            raise ValueError("P2 extractor_mode must be local or llm")
        for name in ("top_k", "sparse_top_k", "dense_top_k"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"P2 {name} must be a positive integer")
        for name in ("rrf_k", "sparse_weight", "dense_weight", "total_timeout"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0
            ):
                raise ValueError(f"P2 {name} must be a finite non-negative number")
        if self.rrf_k <= 0 or self.total_timeout <= 0:
            raise ValueError("P2 rrf_k and total_timeout must be positive")
        if self.sparse_weight == 0 and self.dense_weight == 0:
            raise ValueError("P2 requires at least one non-zero retrieval weight")
        if (
            isinstance(self.max_concurrent_recommendations, bool)
            or not isinstance(self.max_concurrent_recommendations, int)
            or not 1 <= self.max_concurrent_recommendations <= 64
        ):
            raise ValueError(
                "P2 max_concurrent_recommendations must be between 1 and 64"
            )

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> "P2Config":
        selected = os.environ if environ is None else environ
        try:
            return cls(
                extractor_mode=selected.get(P2_EXTRACTOR_MODE_ENV_VAR, "local").strip(),
                top_k=int(selected.get(P2_TOP_K_ENV_VAR, "10")),
                sparse_top_k=int(selected.get(P2_SPARSE_TOP_K_ENV_VAR, "10")),
                dense_top_k=int(selected.get(P2_DENSE_TOP_K_ENV_VAR, "10")),
                rrf_k=float(selected.get(P2_RRF_K_ENV_VAR, "60")),
                sparse_weight=float(selected.get(P2_SPARSE_WEIGHT_ENV_VAR, "1")),
                dense_weight=float(selected.get(P2_DENSE_WEIGHT_ENV_VAR, "1")),
                total_timeout=float(selected.get(P2_TOTAL_TIMEOUT_ENV_VAR, "30")),
                max_concurrent_recommendations=int(
                    selected.get(P2_MAX_CONCURRENT_ENV_VAR, "4")
                ),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("P2 numeric configuration is invalid") from exc


@dataclass(frozen=True, slots=True)
class P2OperationalProvenance:
    """Low-cardinality P2 provenance safe for previews, logs, and metadata."""

    backend_name: str
    backend_version: str
    pipeline_version: str
    structured_intent_schema_version: str
    extractor_name: str
    extractor_version: str
    extractor_model_id: str | None
    extractor_prompt_version: str | None
    extractor_prompt_sha256: str | None
    extraction_mode: str | None
    dense_embedding_model_id: str
    dense_embedding_model_revision: str
    dense_index_version: str
    dense_index_checksum: str
    sparse_index_version: str
    sparse_index_checksum: str
    hybrid_index_version: str
    hybrid_index_checksum: str
    hybrid_retriever_version: str
    hybrid_rrf: Mapping[str, int | float]
    corpus_version: str
    corpus_checksum: str
    catalog_version: str
    candidate_count: int
    retrieved_candidate_count: int
    feasible_candidate_count: int
    final_candidate_id: str | None
    constraint_evaluator_version: str
    constraint_policy_version: str
    ranker_version: str
    fallback_category: str
    schema_version: str = P2_OPERATIONAL_PROVENANCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "backend_name": self.backend_name,
            "backend_version": self.backend_version,
            "pipeline_version": self.pipeline_version,
            "structured_intent_schema_version": self.structured_intent_schema_version,
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "extractor_model_id": self.extractor_model_id,
            "extractor_prompt_version": self.extractor_prompt_version,
            "extractor_prompt_sha256": self.extractor_prompt_sha256,
            "extraction_mode": self.extraction_mode,
            "dense_embedding_model_id": self.dense_embedding_model_id,
            "dense_embedding_model_revision": self.dense_embedding_model_revision,
            "dense_index_version": self.dense_index_version,
            "dense_index_checksum": self.dense_index_checksum,
            "sparse_index_version": self.sparse_index_version,
            "sparse_index_checksum": self.sparse_index_checksum,
            "hybrid_index_version": self.hybrid_index_version,
            "hybrid_index_checksum": self.hybrid_index_checksum,
            "hybrid_retriever_version": self.hybrid_retriever_version,
            "hybrid_rrf": dict(self.hybrid_rrf),
            "corpus_version": self.corpus_version,
            "corpus_checksum": self.corpus_checksum,
            "catalog_version": self.catalog_version,
            "candidate_count": self.candidate_count,
            "retrieved_candidate_count": self.retrieved_candidate_count,
            "feasible_candidate_count": self.feasible_candidate_count,
            "final_candidate_id": self.final_candidate_id,
            "constraint_evaluator_version": self.constraint_evaluator_version,
            "constraint_policy_version": self.constraint_policy_version,
            "ranker_version": self.ranker_version,
            "fallback_category": self.fallback_category,
        }


@dataclass(frozen=True, slots=True)
class P2DetailedResult:
    """Internal stage outputs used by offline evaluation, never operational logs."""

    recommendation: SpawnRecommendation
    metadata: RecommendationMetadata
    trace: RecommendationTrace | None
    retrieval_result: HybridRetrievalResult | None
    ranking_result: ConstraintRankingResult | None
    final_candidate_id: str
    fallback_category: str


class P2Recommender:
    """Compose StructuredIntent, hybrid retrieval, constraints, and trusted resolution."""

    backend_name = P2_BACKEND_NAME
    backend_version = P2_BACKEND_VERSION

    def __init__(
        self,
        *,
        config: P2Config | None = None,
        catalog: Mapping[str, Any] | None = None,
        catalog_path: str = str(DEFAULT_CATALOG_PATH),
        corpus: CandidateCorpus | None = None,
        extractor: StructuredIntentExtractor | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        fallback: RuleBasedRecommender | None = None,
        monotonic: Any = time.monotonic,
    ) -> None:
        self.config = config or P2Config.from_environ()
        self.catalog = (
            validate_image_catalog(catalog)
            if catalog is not None
            else load_image_catalog(catalog_path)
        )
        self.corpus = corpus or build_candidate_corpus(image_catalog=self.catalog)
        if self.corpus.source_image_catalog_version != self.catalog["catalog_version"]:
            raise ContractValidationError("P2 corpus catalog version is stale")
        self.extractor = extractor or self._configured_extractor()
        self.embedding_provider = embedding_provider or LocalFeatureHashEmbeddingProvider()
        dense_index = build_dense_index(self.corpus, self.embedding_provider)
        dense = DenseRetriever(
            self.corpus,
            dense_index,
            self.embedding_provider,
            top_k=self.config.dense_top_k,
        )
        sparse = SparseBM25Retriever(
            self.corpus,
            top_k=self.config.sparse_top_k,
        )
        self.retriever = HybridRetriever(
            self.corpus,
            sparse,
            dense,
            top_k=self.config.top_k,
            sparse_top_k=self.config.sparse_top_k,
            dense_top_k=self.config.dense_top_k,
            rrf_k=self.config.rrf_k,
            sparse_weight=self.config.sparse_weight,
            dense_weight=self.config.dense_weight,
        )
        self.evaluator = ConstraintEvaluator(self.corpus)
        self._fallback = fallback or RuleBasedRecommender(catalog=self.catalog)
        self._monotonic = monotonic
        self.network_bound = bool(getattr(self.extractor, "network_bound", False))

    def _configured_extractor(self) -> StructuredIntentExtractor:
        if self.config.extractor_mode == "llm":
            return create_primary_structured_intent_extractor()
        return LocalStructuredIntentExtractor()

    @property
    def generation(self) -> dict[str, str]:
        """Versions/checksums that invalidate outstanding preview decisions."""

        return {
            "p2_backend_version": self.backend_version,
            "p2_corpus_version": self.corpus.corpus_version,
            "p2_corpus_checksum": self.corpus.corpus_checksum,
            "p2_dense_index_version": self.retriever.dense_retriever.metadata.index_version,
            "p2_dense_index_checksum": self.retriever.dense_retriever.metadata.index_checksum,
            "p2_sparse_index_version": self.retriever.sparse_retriever.metadata.index_version,
            "p2_sparse_index_checksum": self.retriever.sparse_retriever.metadata.index_checksum,
            "p2_hybrid_index_version": self.retriever.metadata.index_version,
            "p2_hybrid_index_checksum": self.retriever.metadata.index_checksum,
        }

    def _resolve_document(self, candidate_id: str) -> CandidateDocument:
        document = self.corpus.get(candidate_id)
        if document is None:
            raise ContractValidationError(
                "retrieval or ranking returned an ID outside the trusted candidate corpus"
            )
        return document

    def _provenance(
        self,
        *,
        structured_intent: StructuredIntent | None,
        retrieval_result: HybridRetrievalResult | None,
        ranking_result: ConstraintRankingResult | None,
        final_candidate_id: str | None,
        fallback_category: str,
    ) -> P2OperationalProvenance:
        extraction = (
            structured_intent.extraction_provenance
            if structured_intent is not None
            else None
        )
        dense = self.retriever.dense_retriever.metadata
        sparse = self.retriever.sparse_retriever.metadata
        hybrid = self.retriever.metadata
        return P2OperationalProvenance(
            backend_name=self.backend_name,
            backend_version=self.backend_version,
            pipeline_version=P2_PIPELINE_VERSION,
            structured_intent_schema_version=STRUCTURED_INTENT_SCHEMA_VERSION,
            extractor_name=(
                extraction.extractor_name
                if extraction is not None
                else str(getattr(self.extractor, "extractor_name", "unavailable"))
            ),
            extractor_version=(
                extraction.extractor_version
                if extraction is not None
                else str(getattr(self.extractor, "extractor_version", "unavailable-v1"))
            ),
            extractor_model_id=(
                extraction.model_id
                if extraction is not None
                else getattr(self.extractor, "model_id", None)
            ),
            extractor_prompt_version=(
                extraction.prompt_version
                if extraction is not None
                else getattr(self.extractor, "prompt_version", None)
            ),
            extractor_prompt_sha256=(
                extraction.prompt_sha256
                if extraction is not None
                else getattr(self.extractor, "prompt_sha256", None)
            ),
            extraction_mode=(extraction.mode.value if extraction is not None else None),
            dense_embedding_model_id=dense.model_id,
            dense_embedding_model_revision=dense.model_revision,
            dense_index_version=dense.index_version,
            dense_index_checksum=dense.index_checksum,
            sparse_index_version=sparse.index_version,
            sparse_index_checksum=sparse.index_checksum,
            hybrid_index_version=hybrid.index_version,
            hybrid_index_checksum=hybrid.index_checksum,
            hybrid_retriever_version=hybrid.retriever_version,
            hybrid_rrf={
                "rrf_k": hybrid.rrf_k,
                "sparse_weight": hybrid.sparse_weight,
                "dense_weight": hybrid.dense_weight,
                "top_k": hybrid.top_k,
                "sparse_top_k": hybrid.sparse_top_k,
                "dense_top_k": hybrid.dense_top_k,
            },
            corpus_version=self.corpus.corpus_version,
            corpus_checksum=self.corpus.corpus_checksum,
            catalog_version=self.corpus.source_image_catalog_version,
            candidate_count=len(self.corpus.candidates),
            retrieved_candidate_count=(
                len(retrieval_result.fused_hits) if retrieval_result is not None else 0
            ),
            feasible_candidate_count=(
                len(ranking_result.ranked_candidates) if ranking_result is not None else 0
            ),
            final_candidate_id=final_candidate_id,
            constraint_evaluator_version=self.evaluator.evaluator_version,
            constraint_policy_version=self.evaluator.constraint_policy_version,
            ranker_version=self.evaluator.ranker_version,
            fallback_category=fallback_category,
        )

    def _trace(
        self,
        structured_intent: StructuredIntent | None,
        retrieval_result: HybridRetrievalResult | None,
        ranking_result: ConstraintRankingResult | None,
        selected: EnvironmentCandidate | None,
    ) -> RecommendationTrace | None:
        if structured_intent is None:
            return None
        return RecommendationTrace(
            pipeline_version=P2_PIPELINE_VERSION,
            catalog_version=self.corpus.source_image_catalog_version,
            index_version=self.retriever.metadata.index_version,
            structured_intent=structured_intent,
            retrieval_hits=(
                retrieval_result.to_retrieval_hits()
                if retrieval_result is not None
                else ()
            ),
            constraint_evaluations=(
                ranking_result.evaluations if ranking_result is not None else ()
            ),
            ranked_candidates=(
                ranking_result.ranked_candidates if ranking_result is not None else ()
            ),
            selected_candidate=selected,
        )

    def _metadata(
        self,
        *,
        recommendation: SpawnRecommendation,
        provenance: P2OperationalProvenance,
        started: float,
        attempt_count: int,
        timed_out: bool = False,
        deadline_exhausted: bool = False,
    ) -> RecommendationMetadata:
        fallback_used = provenance.fallback_category != "none"
        return RecommendationMetadata(
            requested_backend=self.backend_name,
            effective_backend=recommendation.backend_name,
            fallback_used=fallback_used,
            fallback_error_category=(
                provenance.fallback_category if fallback_used else None
            ),
            attempt_count=attempt_count,
            total_elapsed_seconds=max(0.0, self._monotonic() - started),
            timed_out=timed_out,
            deadline_exhausted=deadline_exhausted,
            p2_provenance=provenance.to_dict(),
        )

    def _trusted_fallback_document(
        self, request: RecommendationRequest
    ) -> tuple[CandidateDocument, SpawnRecommendation]:
        fallback = self._fallback.recommend(request)
        if not isinstance(fallback, SpawnRecommendation):
            raise TypeError("P2 fallback returned an invalid recommendation type")
        profile_id = "large" if fallback.profile == "gpu_or_large" else fallback.profile
        document = self._resolve_document(f"{profile_id}-{fallback.image_id}")
        resolved = document.to_spawn_recommendation(
            reasons=fallback.reasons,
            image_reasons=fallback.image_reasons,
            score=fallback.score,
            backend_name=fallback.backend_name,
            backend_version=fallback.backend_version,
        )
        return document, resolved

    def _fallback_detailed(
        self,
        request: RecommendationRequest,
        *,
        fallback_category: str,
        started: float,
        attempt_count: int,
        structured_intent: StructuredIntent | None = None,
        retrieval_result: HybridRetrievalResult | None = None,
        ranking_result: ConstraintRankingResult | None = None,
        timed_out: bool = False,
        deadline_exhausted: bool = False,
    ) -> P2DetailedResult:
        try:
            document, recommendation = self._trusted_fallback_document(request)
        except Exception as exc:
            raise P2FallbackError("P2 trusted fallback failed") from exc
        provenance = self._provenance(
            structured_intent=structured_intent,
            retrieval_result=retrieval_result,
            ranking_result=ranking_result,
            final_candidate_id=document.candidate_id,
            fallback_category=fallback_category,
        )
        return P2DetailedResult(
            recommendation=recommendation,
            metadata=self._metadata(
                recommendation=recommendation,
                provenance=provenance,
                started=started,
                attempt_count=attempt_count,
                timed_out=timed_out,
                deadline_exhausted=deadline_exhausted,
            ),
            trace=self._trace(
                structured_intent, retrieval_result, ranking_result, selected=None
            ),
            retrieval_result=retrieval_result,
            ranking_result=ranking_result,
            final_candidate_id=document.candidate_id,
            fallback_category=fallback_category,
        )

    @staticmethod
    def _exception_fallback_category(error: Exception) -> str:
        if isinstance(error, (DenseRetrievalError, LLMClientError, OSError, TimeoutError)):
            return "infrastructure_provider_failure"
        if isinstance(error, (ContractValidationError, ValueError, TypeError)):
            return "pipeline_validation_failure"
        return "infrastructure_provider_failure"

    def recommend_detailed(
        self,
        request: RecommendationRequest,
        *,
        deadline: float | None = None,
        state: RecommendationCallState | None = None,
    ) -> P2DetailedResult:
        if not isinstance(request, RecommendationRequest):
            raise TypeError("request must be a RecommendationRequest")
        started = self._monotonic()
        attempt_count = 1
        if state is not None:
            state.mark_attempt(attempt_count)
        structured_intent: StructuredIntent | None = None
        retrieval_result: HybridRetrievalResult | None = None
        ranking_result: ConstraintRankingResult | None = None
        try:
            if deadline is not None and self._monotonic() >= deadline:
                return self._fallback_detailed(
                    request,
                    fallback_category="infrastructure_provider_failure",
                    started=started,
                    attempt_count=attempt_count,
                    timed_out=True,
                    deadline_exhausted=True,
                )
            structured_intent = self.extractor.extract(request)
            fallback_category = "none"
            extraction = structured_intent.extraction_provenance
            if extraction.mode is ExtractionMode.DETERMINISTIC_DEGRADED:
                fallback_category = f"extraction_{extraction.degraded_reason}"

            retrieval_result = self.retriever.retrieve_detailed(
                request.intent,
                structured_intent,
            )
            if not retrieval_result.fused_hits:
                return self._fallback_detailed(
                    request,
                    fallback_category="retrieval_empty",
                    started=started,
                    attempt_count=attempt_count,
                    structured_intent=structured_intent,
                    retrieval_result=retrieval_result,
                )

            candidates = tuple(
                self._resolve_document(hit.candidate_id).to_environment_candidate()
                for hit in retrieval_result.fused_hits
            )
            ranking_result = self.evaluator.evaluate_and_rank(
                structured_intent,
                candidates,
                retrieval_result.to_retrieval_hits(),
            )
            if ranking_result.no_feasible_candidate:
                category = (
                    "unsupported_catalog"
                    if ranking_result.unsupported_constraints
                    else "no_feasible_candidate"
                )
                return self._fallback_detailed(
                    request,
                    fallback_category=category,
                    started=started,
                    attempt_count=attempt_count,
                    structured_intent=structured_intent,
                    retrieval_result=retrieval_result,
                    ranking_result=ranking_result,
                )

            ranked = ranking_result.ranked_candidates[0]
            document = self._resolve_document(ranked.candidate_id)
            selected = document.to_environment_candidate()
            recommendation = document.to_spawn_recommendation(
                reasons=[
                    f"Selected trusted candidate {document.candidate_id}",
                    "Passed deterministic hard-constraint evaluation",
                    "Ranked by hybrid retrieval and deterministic preferences",
                ],
                image_reasons=[
                    f"Resolved administrator image {document.image_id}",
                    "Immutable image reference resolved from the trusted catalog",
                ],
                score=round(100.0 * ranked.score, 6),
                backend_name=self.backend_name,
                backend_version=self.backend_version,
            )
            provenance = self._provenance(
                structured_intent=structured_intent,
                retrieval_result=retrieval_result,
                ranking_result=ranking_result,
                final_candidate_id=document.candidate_id,
                fallback_category=fallback_category,
            )
            return P2DetailedResult(
                recommendation=recommendation,
                metadata=self._metadata(
                    recommendation=recommendation,
                    provenance=provenance,
                    started=started,
                    attempt_count=attempt_count,
                ),
                trace=self._trace(
                    structured_intent,
                    retrieval_result,
                    ranking_result,
                    selected,
                ),
                retrieval_result=retrieval_result,
                ranking_result=ranking_result,
                final_candidate_id=document.candidate_id,
                fallback_category=fallback_category,
            )
        except Exception as exc:
            return self._fallback_detailed(
                request,
                fallback_category=self._exception_fallback_category(exc),
                started=started,
                attempt_count=attempt_count,
                structured_intent=structured_intent,
                retrieval_result=retrieval_result,
                ranking_result=ranking_result,
            )

    def recommend_with_metadata(
        self,
        request: RecommendationRequest,
        *,
        deadline: float | None = None,
        state: RecommendationCallState | None = None,
    ) -> RecommendationResult:
        detailed = self.recommend_detailed(request, deadline=deadline, state=state)
        return RecommendationResult(detailed.recommendation, detailed.metadata)

    def fallback_result(
        self,
        request: RecommendationRequest,
        *,
        error_category: str,
        attempt_count: int,
        started: float,
        timed_out: bool,
        deadline_exhausted: bool,
    ) -> RecommendationResult:
        detailed = self._fallback_detailed(
            request,
            fallback_category="infrastructure_provider_failure",
            started=started,
            attempt_count=attempt_count,
            timed_out=timed_out,
            deadline_exhausted=deadline_exhausted,
        )
        return RecommendationResult(detailed.recommendation, detailed.metadata)

    def recommend(self, request: RecommendationRequest) -> SpawnRecommendation:
        return self.recommend_detailed(request).recommendation


def p2_requires_manual_override(metadata: Mapping[str, object]) -> bool:
    provenance = metadata.get("p2_provenance")
    return bool(
        isinstance(provenance, Mapping)
        and provenance.get("fallback_category") in P2_MANUAL_OVERRIDE_FALLBACKS
    )


__all__ = [
    "P2_BACKEND_NAME",
    "P2_BACKEND_VERSION",
    "P2_MANUAL_OVERRIDE_FALLBACKS",
    "P2_OPERATIONAL_PROVENANCE_SCHEMA_VERSION",
    "P2_PIPELINE_VERSION",
    "P2Config",
    "P2DetailedResult",
    "P2FallbackError",
    "P2OperationalProvenance",
    "P2PipelineError",
    "P2Recommender",
    "p2_requires_manual_override",
]
