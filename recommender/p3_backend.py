"""P3 backend: frozen P2 followed by retrieval-grounded LLM reranking.

P3 deliberately does not construct or configure extraction, retrieval, or
constraint/ranking stages. It consumes the detailed output of a P2Recommender,
passes only P2-feasible candidates to the reranker, and resolves the selected
identifier through the same administrator-owned P2 corpus. Any reranker
failure returns the exact P2 recommendation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
import os
import time
from typing import Any

from .candidate_corpus import CandidateCorpus, CandidateDocument
from .dense_retrieval import EmbeddingProvider
from .models import (
    ContractValidationError,
    EnvironmentCandidate,
    RankedCandidate,
    RecommendationRequest,
    RecommendationTrace,
    SpawnRecommendation,
)
from .p2_backend import (
    P2_BACKEND_VERSION,
    P2_PIPELINE_VERSION,
    P2Config,
    P2DetailedResult,
    P2Recommender,
)
from .p3_reranker import (
    DEGRADED_RERANKER_NAME,
    DEGRADED_RERANKER_VERSION,
    P3RerankerProtocol,
    P3RerankingResult,
    create_p3_reranker,
)
from .reliability import (
    MAX_CONCURRENT_NETWORK_RECOMMENDATIONS,
    RecommendationCallState,
    RecommendationMetadata,
    RecommendationResult,
)
from .rule_based import DEFAULT_CATALOG_PATH, RuleBasedRecommender
from .structured_intent import StructuredIntentExtractor


P3_BACKEND_NAME = "p3"
P3_BACKEND_VERSION = "p3-reranker-v1.0.0"
P3_PIPELINE_VERSION = "p3-pipeline-v1.0.0"
P3_OPERATIONAL_PROVENANCE_SCHEMA_VERSION = "p3-operational-provenance-v1"

P3_RERANKER_MODE_ENV_VAR = "P3_RERANKER_MODE"
P3_TOTAL_TIMEOUT_ENV_VAR = "P3_TOTAL_TIMEOUT"
P3_MAX_CONCURRENT_ENV_VAR = "P3_MAX_CONCURRENT_RECOMMENDATIONS"

P3_MANUAL_OVERRIDE_FALLBACKS = frozenset(
    {"no_feasible_candidate", "unsupported_catalog"}
)
_INVALID_OUTPUT_REASONS = frozenset(
    {
        "reranker_invalid_output",
        "reranker_unknown_candidate_id",
        "reranker_duplicate_candidate_id",
        "reranker_missing_candidate_id",
        "reranker_invalid_input",
    }
)
_PROVIDER_FAILURE_REASONS = frozenset(
    {"reranker_timeout", "reranker_provider_error", "reranker_error"}
)


class P3PipelineError(RuntimeError):
    """A P3 stage failed before a trusted recommendation could be produced."""


class P3FallbackError(P3PipelineError):
    """The frozen P2 backend could not provide the deterministic fallback."""


@dataclass(frozen=True, slots=True)
class P3Config:
    """Versioned P3-only operational configuration.

    P2 algorithm parameters intentionally do not appear here. P3 inherits the
    complete P2 configuration from ``p2_backend`` and adds only reranker mode,
    total request deadline, and concurrency bounds.
    """

    reranker_mode: str = "llm"
    total_timeout: float = 30.0
    max_concurrent_recommendations: int = 4
    config_version: str = "p3-config-v1.0.0"

    def __post_init__(self) -> None:
        if self.reranker_mode not in {"deterministic", "llm"}:
            raise ValueError("P3 reranker_mode must be deterministic or llm")
        if (
            isinstance(self.total_timeout, bool)
            or not isinstance(self.total_timeout, (int, float))
            or not math.isfinite(float(self.total_timeout))
            or not 0 < float(self.total_timeout) <= 300.0
        ):
            raise ValueError("P3 total_timeout must be a positive bounded number")
        if (
            isinstance(self.max_concurrent_recommendations, bool)
            or not isinstance(self.max_concurrent_recommendations, int)
            or not 1
            <= self.max_concurrent_recommendations
            <= MAX_CONCURRENT_NETWORK_RECOMMENDATIONS
        ):
            raise ValueError(
                "P3 max_concurrent_recommendations must be between 1 and "
                f"{MAX_CONCURRENT_NETWORK_RECOMMENDATIONS}"
            )
        if not isinstance(self.config_version, str) or not self.config_version.strip():
            raise ValueError("P3 config_version must be non-blank")

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> "P3Config":
        selected = os.environ if environ is None else environ
        try:
            return cls(
                reranker_mode=selected.get(P3_RERANKER_MODE_ENV_VAR, "llm").strip(),
                total_timeout=float(
                    selected.get(
                        P3_TOTAL_TIMEOUT_ENV_VAR,
                        selected.get("EXTERNAL_LLM_TOTAL_TIMEOUT", "30"),
                    )
                ),
                max_concurrent_recommendations=int(
                    selected.get(
                        P3_MAX_CONCURRENT_ENV_VAR,
                        selected.get(
                            "EXTERNAL_LLM_MAX_CONCURRENT_RECOMMENDATIONS", "4"
                        ),
                    )
                ),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("P3 numeric configuration is invalid") from exc


@dataclass(frozen=True, slots=True)
class P3OperationalProvenance:
    """Low-cardinality P3 provenance safe for previews, logs, and metadata."""

    backend_name: str
    backend_version: str
    pipeline_version: str
    config_version: str
    frozen_p2_backend_version: str
    frozen_p2_pipeline_version: str
    frozen_p2_provenance: Mapping[str, object]
    reranker_invoked: bool
    reranker_name: str
    reranker_version: str
    reranker_model_id: str | None
    reranker_prompt_version: str | None
    reranker_prompt_sha256: str | None
    reranker_degraded: bool
    reranker_degraded_reason: str | None
    invalid_reranker_output: bool
    provider_failure: bool
    final_candidate_id: str | None
    p2_fallback_category: str
    fallback_category: str
    schema_version: str = P3_OPERATIONAL_PROVENANCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "backend_name": self.backend_name,
            "backend_version": self.backend_version,
            "pipeline_version": self.pipeline_version,
            "config_version": self.config_version,
            "frozen_p2_backend_version": self.frozen_p2_backend_version,
            "frozen_p2_pipeline_version": self.frozen_p2_pipeline_version,
            "frozen_p2_provenance": dict(self.frozen_p2_provenance),
            "reranker_invoked": self.reranker_invoked,
            "reranker_name": self.reranker_name,
            "reranker_version": self.reranker_version,
            "reranker_model_id": self.reranker_model_id,
            "reranker_prompt_version": self.reranker_prompt_version,
            "reranker_prompt_sha256": self.reranker_prompt_sha256,
            "reranker_degraded": self.reranker_degraded,
            "reranker_degraded_reason": self.reranker_degraded_reason,
            "invalid_reranker_output": self.invalid_reranker_output,
            "provider_failure": self.provider_failure,
            "final_candidate_id": self.final_candidate_id,
            "p2_fallback_category": self.p2_fallback_category,
            "fallback_category": self.fallback_category,
        }


@dataclass(frozen=True, slots=True)
class P3DetailedResult:
    """Internal P2 and P3 stage outputs retained for offline evaluation."""

    recommendation: SpawnRecommendation
    metadata: RecommendationMetadata
    trace: RecommendationTrace | None
    p2_result: P2DetailedResult
    reranking_result: P3RerankingResult | None
    final_candidate_id: str
    fallback_category: str

    @property
    def retrieval_result(self):
        return self.p2_result.retrieval_result

    @property
    def ranking_result(self):
        return self.p2_result.ranking_result


class P3Recommender:
    """Apply one LLM reranking component to an otherwise frozen P2 backend."""

    backend_name = P3_BACKEND_NAME
    backend_version = P3_BACKEND_VERSION

    def __init__(
        self,
        *,
        config: P3Config | None = None,
        p2_backend: P2Recommender | None = None,
        p2_config: P2Config | None = None,
        catalog: Mapping[str, Any] | None = None,
        catalog_path: str = str(DEFAULT_CATALOG_PATH),
        corpus: CandidateCorpus | None = None,
        extractor: StructuredIntentExtractor | None = None,
        reranker: P3RerankerProtocol | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        fallback: RuleBasedRecommender | None = None,
        monotonic: Any = time.monotonic,
    ) -> None:
        if p2_backend is not None and any(
            value is not None
            for value in (
                p2_config,
                catalog,
                corpus,
                extractor,
                embedding_provider,
                fallback,
            )
        ):
            raise ValueError(
                "P3 accepts either p2_backend or P2 construction inputs, not both"
            )
        self.config = config or P3Config.from_environ()
        self.p2_backend = p2_backend or P2Recommender(
            config=p2_config,
            catalog=catalog,
            catalog_path=catalog_path,
            corpus=corpus,
            extractor=extractor,
            embedding_provider=embedding_provider,
            fallback=fallback,
            monotonic=monotonic,
        )
        self.catalog = self.p2_backend.catalog
        self.corpus = self.p2_backend.corpus
        self.reranker = reranker if reranker is not None else self._configured_reranker()
        self._monotonic = monotonic
        self.network_bound = bool(
            getattr(self.p2_backend, "network_bound", False)
            or (self.config.reranker_mode == "llm" and self.reranker is not None)
        )

    def _configured_reranker(self) -> P3RerankerProtocol | None:
        if self.config.reranker_mode != "llm":
            return None
        try:
            return create_p3_reranker()
        except (ValueError, KeyError):
            return None

    @property
    def generation(self) -> dict[str, str]:
        """Versions/checksums that invalidate outstanding preview decisions."""

        generation = dict(self.p2_backend.generation)
        generation.update(
            {
                "p3_backend_version": self.backend_version,
                "p3_pipeline_version": P3_PIPELINE_VERSION,
                "p3_config_version": self.config.config_version,
                "p3_reranker_version": str(
                    getattr(self.reranker, "reranker_version", DEGRADED_RERANKER_VERSION)
                ),
            }
        )
        prompt_version = getattr(self.reranker, "prompt_version", None)
        prompt_sha256 = getattr(self.reranker, "prompt_sha256", None)
        model_id = getattr(getattr(self.reranker, "config", None), "model", None)
        if prompt_version is not None:
            generation["p3_reranker_prompt_version"] = str(prompt_version)
        if prompt_sha256 is not None:
            generation["p3_reranker_prompt_sha256"] = str(prompt_sha256)
        if model_id is not None:
            generation["p3_reranker_model_id"] = str(model_id)
        return generation

    def _resolve_document(self, candidate_id: str) -> CandidateDocument:
        document = self.corpus.get(candidate_id)
        if document is None:
            raise ContractValidationError(
                "P3 reranking returned an ID outside the frozen trusted P2 corpus"
            )
        return document

    @staticmethod
    def _p2_provenance(p2_result: P2DetailedResult) -> dict[str, object]:
        provenance = p2_result.metadata.p2_provenance
        return dict(provenance) if provenance is not None else {}

    def _provenance(
        self,
        *,
        p2_result: P2DetailedResult,
        reranking_result: P3RerankingResult | None,
        reranker_invoked: bool,
        final_candidate_id: str | None,
        fallback_category: str,
    ) -> P3OperationalProvenance:
        reason = (
            reranking_result.degraded_reason
            if reranking_result is not None
            else None
        )
        reranker_object = self.reranker
        return P3OperationalProvenance(
            backend_name=self.backend_name,
            backend_version=self.backend_version,
            pipeline_version=P3_PIPELINE_VERSION,
            config_version=self.config.config_version,
            frozen_p2_backend_version=P2_BACKEND_VERSION,
            frozen_p2_pipeline_version=P2_PIPELINE_VERSION,
            frozen_p2_provenance=self._p2_provenance(p2_result),
            reranker_invoked=reranker_invoked,
            reranker_name=(
                reranking_result.reranker_name
                if reranking_result is not None
                else str(
                    getattr(
                        reranker_object,
                        "reranker_name",
                        DEGRADED_RERANKER_NAME,
                    )
                )
            ),
            reranker_version=(
                reranking_result.reranker_version
                if reranking_result is not None
                else str(
                    getattr(
                        reranker_object,
                        "reranker_version",
                        DEGRADED_RERANKER_VERSION,
                    )
                )
            ),
            reranker_model_id=(
                reranking_result.model_id
                if reranking_result is not None
                else getattr(getattr(reranker_object, "config", None), "model", None)
            ),
            reranker_prompt_version=(
                reranking_result.prompt_version
                if reranking_result is not None
                else getattr(reranker_object, "prompt_version", None)
            ),
            reranker_prompt_sha256=(
                reranking_result.prompt_sha256
                if reranking_result is not None
                else getattr(reranker_object, "prompt_sha256", None)
            ),
            reranker_degraded=(
                reranking_result.degraded
                if reranking_result is not None
                else True
            ),
            reranker_degraded_reason=reason,
            invalid_reranker_output=reason in _INVALID_OUTPUT_REASONS,
            provider_failure=reason in _PROVIDER_FAILURE_REASONS,
            final_candidate_id=final_candidate_id,
            p2_fallback_category=p2_result.fallback_category,
            fallback_category=fallback_category,
        )

    def _metadata(
        self,
        *,
        p2_result: P2DetailedResult,
        recommendation: SpawnRecommendation,
        provenance: P3OperationalProvenance,
        started: float,
        reranking_result: P3RerankingResult | None,
        timed_out: bool = False,
        deadline_exhausted: bool = False,
    ) -> RecommendationMetadata:
        prompt_tokens = (
            reranking_result.prompt_tokens if reranking_result is not None else None
        )
        completion_tokens = (
            reranking_result.completion_tokens if reranking_result is not None else None
        )
        pricing = getattr(getattr(self.reranker, "config", None), "pricing", None)
        estimated_cost = (
            pricing.calculate_cost_usd(prompt_tokens, completion_tokens)
            if pricing is not None
            else None
        )
        return RecommendationMetadata(
            requested_backend=self.backend_name,
            effective_backend=recommendation.backend_name,
            fallback_used=provenance.fallback_category != "none",
            fallback_error_category=(
                provenance.fallback_category
                if provenance.fallback_category != "none"
                else None
            ),
            attempt_count=(
                reranking_result.attempt_count
                if reranking_result is not None
                else p2_result.metadata.attempt_count
            ),
            total_elapsed_seconds=max(0.0, self._monotonic() - started),
            timed_out=timed_out,
            deadline_exhausted=deadline_exhausted,
            raw_response=(
                reranking_result.raw_response if reranking_result is not None else None
            ),
            validation_error=(
                reranking_result.validation_error
                if reranking_result is not None
                else None
            ),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=(
                reranking_result.total_tokens if reranking_result is not None else None
            ),
            inference_latency_seconds=(
                reranking_result.inference_latency_seconds
                if reranking_result is not None
                else None
            ),
            estimated_cost_usd=estimated_cost,
            pricing_id=(pricing.pricing_id if pricing is not None else None),
            pricing_provenance=(
                pricing.source_provenance if pricing is not None else None
            ),
            p2_provenance=self._p2_provenance(p2_result),
            p3_provenance=provenance.to_dict(),
        )

    @staticmethod
    def _can_rerank(p2_result: P2DetailedResult) -> bool:
        ranking = p2_result.ranking_result
        trace = p2_result.trace
        return bool(
            ranking is not None
            and not ranking.no_feasible_candidate
            and ranking.ranked_candidates
            and trace is not None
        )

    def _p3_trace(
        self,
        p2_result: P2DetailedResult,
        ranked_candidates: Sequence[RankedCandidate],
        selected: EnvironmentCandidate,
    ) -> RecommendationTrace:
        if p2_result.trace is None:
            raise ContractValidationError("P3 cannot rerank without a P2 trace")
        return RecommendationTrace(
            pipeline_version=P3_PIPELINE_VERSION,
            catalog_version=p2_result.trace.catalog_version,
            index_version=p2_result.trace.index_version,
            structured_intent=p2_result.trace.structured_intent,
            retrieval_hits=p2_result.trace.retrieval_hits,
            constraint_evaluations=p2_result.trace.constraint_evaluations,
            ranked_candidates=tuple(ranked_candidates),
            selected_candidate=selected,
        )

    def _result_from_p2(
        self,
        *,
        p2_result: P2DetailedResult,
        started: float,
        fallback_category: str,
        reranking_result: P3RerankingResult | None = None,
        reranker_invoked: bool = False,
        timed_out: bool = False,
        deadline_exhausted: bool = False,
    ) -> P3DetailedResult:
        provenance = self._provenance(
            p2_result=p2_result,
            reranking_result=reranking_result,
            reranker_invoked=reranker_invoked,
            final_candidate_id=p2_result.final_candidate_id,
            fallback_category=fallback_category,
        )
        return P3DetailedResult(
            recommendation=p2_result.recommendation,
            metadata=self._metadata(
                p2_result=p2_result,
                recommendation=p2_result.recommendation,
                provenance=provenance,
                started=started,
                reranking_result=reranking_result,
                timed_out=timed_out,
                deadline_exhausted=deadline_exhausted,
            ),
            trace=p2_result.trace,
            p2_result=p2_result,
            reranking_result=reranking_result,
            final_candidate_id=p2_result.final_candidate_id,
            fallback_category=fallback_category,
        )

    def recommend_detailed(
        self,
        request: RecommendationRequest,
        *,
        deadline: float | None = None,
        state: RecommendationCallState | None = None,
    ) -> P3DetailedResult:
        if not isinstance(request, RecommendationRequest):
            raise TypeError("request must be a RecommendationRequest")
        started = self._monotonic()
        p2_result = self.p2_backend.recommend_detailed(
            request,
            deadline=deadline,
            state=state,
        )

        if not self._can_rerank(p2_result):
            category = (
                p2_result.fallback_category
                if p2_result.fallback_category != "none"
                else "reranking_no_feasible_p2_ranking"
            )
            return self._result_from_p2(
                p2_result=p2_result,
                started=started,
                fallback_category=category,
            )

        ranking = p2_result.ranking_result
        assert ranking is not None
        deterministic_ranked = tuple(ranking.ranked_candidates)

        if self.config.reranker_mode != "llm":
            reranking_result = P3RerankingResult(
                reranked_candidates=deterministic_ranked,
                degraded=True,
                degraded_reason="deterministic_mode",
                reranker_name=DEGRADED_RERANKER_NAME,
                reranker_version=DEGRADED_RERANKER_VERSION,
                prompt_version=None,
                prompt_sha256=None,
                model_id=None,
                attempt_count=0,
            )
        elif self.reranker is None:
            reranking_result = P3RerankingResult(
                reranked_candidates=deterministic_ranked,
                degraded=True,
                degraded_reason="reranker_not_configured",
                reranker_name=DEGRADED_RERANKER_NAME,
                reranker_version=DEGRADED_RERANKER_VERSION,
                prompt_version=None,
                prompt_sha256=None,
                model_id=None,
                attempt_count=0,
            )
        else:
            feasible_candidates = tuple(
                self._resolve_document(item.candidate_id).to_environment_candidate()
                for item in deterministic_ranked
            )
            try:
                reranking_result = self.reranker.rerank(
                    request,
                    p2_result.trace.structured_intent,
                    feasible_candidates,
                    self.corpus,
                    ranking.evaluations,
                    deterministic_ranked,
                    deadline=deadline,
                )
            except Exception as exc:
                reranking_result = P3RerankingResult(
                    reranked_candidates=deterministic_ranked,
                    degraded=True,
                    degraded_reason="reranker_error",
                    reranker_name=str(
                        getattr(self.reranker, "reranker_name", "p3-reranker")
                    ),
                    reranker_version=str(
                        getattr(self.reranker, "reranker_version", "p3-reranker-v1")
                    ),
                    prompt_version=getattr(self.reranker, "prompt_version", None),
                    prompt_sha256=getattr(self.reranker, "prompt_sha256", None),
                    model_id=getattr(
                        getattr(self.reranker, "config", None), "model", None
                    ),
                    validation_error=str(exc),
                    attempt_count=1,
                )

        if reranking_result.degraded:
            return self._result_from_p2(
                p2_result=p2_result,
                started=started,
                fallback_category=f"reranking_{reranking_result.degraded_reason}",
                reranking_result=reranking_result,
                reranker_invoked=self.reranker is not None
                and self.config.reranker_mode == "llm",
            )

        p2_ids = {item.candidate_id for item in deterministic_ranked}
        reranked_ids = [item.candidate_id for item in reranking_result.reranked_candidates]
        if len(reranked_ids) != len(set(reranked_ids)) or set(reranked_ids) != p2_ids:
            invalid_result = P3RerankingResult(
                reranked_candidates=deterministic_ranked,
                degraded=True,
                degraded_reason="reranker_invalid_output",
                reranker_name=reranking_result.reranker_name,
                reranker_version=reranking_result.reranker_version,
                prompt_version=reranking_result.prompt_version,
                prompt_sha256=reranking_result.prompt_sha256,
                model_id=reranking_result.model_id,
                raw_response=reranking_result.raw_response,
                validation_error=(
                    "validated P3 ranking did not preserve the complete feasible P2 set"
                ),
                prompt_tokens=reranking_result.prompt_tokens,
                completion_tokens=reranking_result.completion_tokens,
                total_tokens=reranking_result.total_tokens,
                inference_latency_seconds=reranking_result.inference_latency_seconds,
                attempt_count=reranking_result.attempt_count,
            )
            return self._result_from_p2(
                p2_result=p2_result,
                started=started,
                fallback_category="reranking_reranker_invalid_output",
                reranking_result=invalid_result,
                reranker_invoked=True,
            )
        selected_ranked = reranking_result.reranked_candidates[0]
        evaluation_by_id = {item.candidate_id: item for item in ranking.evaluations}
        selected_evaluation = evaluation_by_id.get(selected_ranked.candidate_id)
        if selected_evaluation is None or not selected_evaluation.feasible:
            invalid_result = P3RerankingResult(
                reranked_candidates=deterministic_ranked,
                degraded=True,
                degraded_reason="reranker_invalid_output",
                reranker_name=reranking_result.reranker_name,
                reranker_version=reranking_result.reranker_version,
                prompt_version=reranking_result.prompt_version,
                prompt_sha256=reranking_result.prompt_sha256,
                model_id=reranking_result.model_id,
                raw_response=reranking_result.raw_response,
                validation_error=(
                    "P3 selected a candidate without a feasible P2 constraint evaluation"
                ),
                prompt_tokens=reranking_result.prompt_tokens,
                completion_tokens=reranking_result.completion_tokens,
                total_tokens=reranking_result.total_tokens,
                inference_latency_seconds=reranking_result.inference_latency_seconds,
                attempt_count=reranking_result.attempt_count,
            )
            return self._result_from_p2(
                p2_result=p2_result,
                started=started,
                fallback_category="reranking_reranker_invalid_output",
                reranking_result=invalid_result,
                reranker_invoked=True,
            )

        document = self._resolve_document(selected_ranked.candidate_id)
        selected = document.to_environment_candidate()
        explanation = next(
            (
                reason.removeprefix("p3_explanation:")
                for reason in selected_ranked.ranking_reasons
                if reason.startswith("p3_explanation:")
            ),
            "Selected by the retrieval-grounded P3 reranker",
        )
        recommendation = document.to_spawn_recommendation(
            reasons=(
                f"Selected trusted candidate {selected_ranked.candidate_id}",
                "Passed frozen P2 deterministic hard-constraint evaluation",
                explanation,
            ),
            image_reasons=(
                f"Resolved administrator image {document.image_id}",
                "Immutable image reference resolved from the trusted catalog",
            ),
            score=round(100.0 * selected_ranked.score, 6),
            backend_name=self.backend_name,
            backend_version=self.backend_version,
        )
        fallback_category = (
            "none"
            if p2_result.fallback_category == "none"
            else f"p2_{p2_result.fallback_category}"
        )
        provenance = self._provenance(
            p2_result=p2_result,
            reranking_result=reranking_result,
            reranker_invoked=True,
            final_candidate_id=document.candidate_id,
            fallback_category=fallback_category,
        )
        return P3DetailedResult(
            recommendation=recommendation,
            metadata=self._metadata(
                p2_result=p2_result,
                recommendation=recommendation,
                provenance=provenance,
                started=started,
                reranking_result=reranking_result,
            ),
            trace=self._p3_trace(
                p2_result,
                reranking_result.reranked_candidates,
                selected,
            ),
            p2_result=p2_result,
            reranking_result=reranking_result,
            final_candidate_id=document.candidate_id,
            fallback_category=fallback_category,
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
        try:
            p2_result = self.p2_backend.recommend_detailed(request)
        except Exception as exc:
            raise P3FallbackError("frozen P2 fallback failed") from exc
        detailed = self._result_from_p2(
            p2_result=p2_result,
            started=started,
            fallback_category="reranking_provider_deadline",
            timed_out=timed_out,
            deadline_exhausted=deadline_exhausted,
        )
        return RecommendationResult(detailed.recommendation, detailed.metadata)

    def recommend(self, request: RecommendationRequest) -> SpawnRecommendation:
        return self.recommend_detailed(request).recommendation


def p3_requires_manual_override(metadata: Mapping[str, object]) -> bool:
    provenance = metadata.get("p3_provenance")
    return bool(
        isinstance(provenance, Mapping)
        and (
            provenance.get("p2_fallback_category") in P3_MANUAL_OVERRIDE_FALLBACKS
            or provenance.get("fallback_category") in P3_MANUAL_OVERRIDE_FALLBACKS
        )
    )


__all__ = [
    "P3_BACKEND_NAME",
    "P3_BACKEND_VERSION",
    "P3_MANUAL_OVERRIDE_FALLBACKS",
    "P3_OPERATIONAL_PROVENANCE_SCHEMA_VERSION",
    "P3_PIPELINE_VERSION",
    "P3Config",
    "P3DetailedResult",
    "P3FallbackError",
    "P3OperationalProvenance",
    "P3PipelineError",
    "P3Recommender",
    "p3_requires_manual_override",
]
