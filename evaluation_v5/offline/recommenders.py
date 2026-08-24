"""Frozen-system adapters for Protocol-v5 offline recommendation evidence.

The adapters deliberately receive only ``OfflineCaseInput``.  In particular,
the split case's gold labels are never passed to a recommender, which keeps the
same boundary for development and confirmatory evaluation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import math
from typing import Any, Protocol, runtime_checkable

from recommender.candidate_corpus import CandidateCorpus, build_candidate_corpus
from recommender.models import STRUCTURED_INTENT_SCHEMA_VERSION, RecommendationRequest
from recommender.p2_backend import P2DetailedResult, P2Recommender
from recommender.p3_backend import P3DetailedResult, P3Recommender
from recommender.rule_based import RuleBasedRecommender


SYSTEM_IDS = frozenset({"P1", "P2", "P3"})
P1_ADAPTER_VERSION = "protocol-v5-p1-frozen-adapter-v1"
P2_ADAPTER_VERSION = "protocol-v5-p2-frozen-adapter-v1"
P3_ADAPTER_VERSION = "protocol-v5-p3-frozen-adapter-v1"


def _json_value(value: object) -> Any:
    """Convert versioned backend contracts to finite JSON-compatible data."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("adapter output must not contain non-finite numbers")
        return 0.0 if value == 0 else value
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("adapter output mappings must use string keys")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _json_value(to_dict())
    if is_dataclass(value):
        return _json_value(asdict(value))
    raise TypeError(f"adapter output is not JSON-compatible: {type(value).__name__}")


def _safe_metadata(metadata: object) -> dict[str, Any]:
    """Use the backend's safe telemetry view and exclude provider raw output."""

    to_operational_dict = getattr(metadata, "to_operational_dict", None)
    if not callable(to_operational_dict):
        return {}
    value = _json_value(to_operational_dict())
    assert isinstance(value, dict)
    # Validation messages and pricing provenance may include provider text or a
    # URL.  The raw evidence needs categories and versions, not either value.
    value.pop("validation_error", None)
    value.pop("pricing_provenance", None)
    value.pop("raw_response", None)
    return value


def candidate_catalog_snapshot(corpus: CandidateCorpus) -> dict[str, Any]:
    return {
        "catalog_version": corpus.source_image_catalog_version,
        "catalog_sha256": corpus.source_image_catalog_checksum,
        "corpus_version": corpus.corpus_version,
        "corpus_sha256": corpus.corpus_checksum,
        "candidates": [
            {
                "candidate_id": item.candidate_id,
                "profile_id": item.profile_id,
                "image_id": item.image_id,
                "catalog_version": item.catalog_version,
                "policy_version": item.policy_version,
            }
            for item in sorted(corpus.candidates, key=lambda candidate: candidate.candidate_id)
        ],
    }


def _p2_frozen_provenance(backend: P2Recommender) -> dict[str, Any]:
    dense = backend.retriever.dense_retriever.metadata
    sparse = backend.retriever.sparse_retriever.metadata
    hybrid = backend.retriever.metadata
    extractor = backend.extractor
    embedding = backend.embedding_provider.metadata
    return {
        "backend_name": backend.backend_name,
        "backend_version": backend.backend_version,
        "pipeline_version": "p2-pipeline-v1.0.0",
        "structured_intent_schema_version": STRUCTURED_INTENT_SCHEMA_VERSION,
        "extractor_name": getattr(extractor, "extractor_name", type(extractor).__name__),
        "extractor_version": getattr(extractor, "extractor_version", None),
        "extractor_model_id": getattr(extractor, "model_id", None),
        "extractor_prompt_version": getattr(extractor, "prompt_version", None),
        "extractor_prompt_sha256": getattr(extractor, "prompt_sha256", None),
        "embedding_model_id": embedding.model_id,
        "embedding_model_revision": embedding.model_revision,
        "dense_index_version": dense.index_version,
        "dense_index_sha256": dense.index_checksum,
        "sparse_index_version": sparse.index_version,
        "sparse_index_sha256": sparse.index_checksum,
        "hybrid_index_version": hybrid.index_version,
        "hybrid_index_sha256": hybrid.index_checksum,
        "retrieval_configuration": {
            "retriever_version": hybrid.retriever_version,
            "top_k": hybrid.top_k,
            "sparse_top_k": hybrid.sparse_top_k,
            "dense_top_k": hybrid.dense_top_k,
            "rrf_k": hybrid.rrf_k,
            "sparse_weight": hybrid.sparse_weight,
            "dense_weight": hybrid.dense_weight,
        },
        "constraint_ranking_configuration": {
            "constraint_evaluator_version": backend.evaluator.evaluator_version,
            "constraint_policy_version": backend.evaluator.constraint_policy_version,
            "ranker_version": backend.evaluator.ranker_version,
        },
        "config": _json_value(backend.config),
        "generation": _json_value(backend.generation),
        "candidate_catalog": candidate_catalog_snapshot(backend.corpus),
    }


@dataclass(frozen=True, slots=True)
class OfflineCaseInput:
    """The only benchmark data released to an offline system adapter."""

    case_id: str
    family_id: str
    variant_id: str
    language: str
    prompt: str
    dataset_size_gb: int | float
    code_context_hints: tuple[str, ...]

    def request(self) -> RecommendationRequest:
        return RecommendationRequest(
            intent=self.prompt,
            dataset_size_gb=self.dataset_size_gb,
            code_context="\n".join(self.code_context_hints),
        )


@dataclass(frozen=True, slots=True)
class OfflineAdapterResult:
    """Complete, JSON-safe stage evidence from one adapter invocation.

    Empty traces mean a stage does not exist for that frozen system; they do
    not mean the runner has inferred a ranking for it.
    """

    predicted_candidate_id: str | None
    predicted_profile_id: str | None
    predicted_image_id: str | None
    recommendation_reasons: tuple[str, ...] = ()
    recommendation_codes: tuple[str, ...] = ()
    structured_intent: Mapping[str, Any] | None = None
    sparse_ranks: tuple[Mapping[str, Any], ...] = ()
    dense_ranks: tuple[Mapping[str, Any], ...] = ()
    hybrid_ranks_scores: tuple[Mapping[str, Any], ...] = ()
    candidate_top_k: tuple[Mapping[str, Any], ...] = ()
    constraint_evaluations: tuple[Mapping[str, Any], ...] = ()
    feasible_top_k: tuple[Mapping[str, Any], ...] = ()
    final_ranking: tuple[Mapping[str, Any], ...] = ()
    constraint_summary: Mapping[str, Any] | None = None
    latency_components: Mapping[str, Any] | None = None
    fallback: Mapping[str, Any] | None = None
    errors: Mapping[str, Any] | None = None
    backend_provenance: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))


@runtime_checkable
class OfflineSystemAdapter(Protocol):
    """Minimal adapter contract used by the runner and fake test systems."""

    system_id: str
    stochastic: bool

    def frozen_provenance(self) -> Mapping[str, Any]: ...

    def recommend(
        self,
        case: OfflineCaseInput,
        *,
        seed: int,
    ) -> OfflineAdapterResult: ...


def _normalized_profile(value: str) -> str:
    return "large" if value == "gpu_or_large" else value


def _detail_result(detail: P2DetailedResult | P3DetailedResult) -> OfflineAdapterResult:
    recommendation = detail.recommendation
    metadata = _safe_metadata(detail.metadata)
    trace = detail.trace
    retrieval = detail.retrieval_result
    ranking = detail.ranking_result

    sparse_ranks = ()
    dense_ranks = ()
    hybrid_ranks_scores = ()
    candidate_top_k = ()
    if retrieval is not None:
        sparse_ranks = tuple(_json_value(hit) for hit in retrieval.sparse_hits)
        dense_ranks = tuple(_json_value(hit) for hit in retrieval.dense_hits)
        hybrid_ranks_scores = tuple(_json_value(hit) for hit in retrieval.fused_hits)
        candidate_top_k = hybrid_ranks_scores

    constraint_evaluations = ()
    feasible_top_k = ()
    final_ranking = ()
    constraint_summary: dict[str, Any] | None = None
    if ranking is not None:
        constraint_evaluations = tuple(_json_value(item) for item in ranking.evaluations)
        feasible_top_k = tuple(_json_value(item) for item in ranking.ranked_candidates)
        constraint_summary = {
            "no_feasible_candidate": ranking.no_feasible_candidate,
            "unmet_constraints": list(ranking.unmet_constraints),
            "unsupported_constraints": list(ranking.unsupported_constraints),
            "explanation_codes": list(ranking.explanation_codes),
            "evaluator_version": ranking.evaluator_version,
            "constraint_policy_version": ranking.constraint_policy_version,
            "ranker_version": ranking.ranker_version,
        }
    if trace is not None:
        final_ranking = tuple(_json_value(item) for item in trace.ranked_candidates)

    return OfflineAdapterResult(
        predicted_candidate_id=detail.final_candidate_id,
        predicted_profile_id=_normalized_profile(recommendation.profile),
        predicted_image_id=recommendation.image_id,
        recommendation_reasons=tuple(str(item) for item in recommendation.reasons),
        recommendation_codes=tuple(
            sorted(
                {
                    str(code)
                    for item in constraint_evaluations
                    for code in item.get("explanation_codes", [])
                }
            )
        ),
        structured_intent=(
            _json_value(trace.structured_intent) if trace is not None else None
        ),
        sparse_ranks=sparse_ranks,
        dense_ranks=dense_ranks,
        hybrid_ranks_scores=hybrid_ranks_scores,
        candidate_top_k=candidate_top_k,
        constraint_evaluations=constraint_evaluations,
        feasible_top_k=feasible_top_k,
        final_ranking=final_ranking,
        constraint_summary=constraint_summary,
        latency_components={
            "total_elapsed_seconds": metadata.get("total_elapsed_seconds"),
            "inference_latency_seconds": metadata.get("inference_latency_seconds"),
            "attempt_count": metadata.get("attempt_count"),
            "timed_out": metadata.get("timed_out"),
            "deadline_exhausted": metadata.get("deadline_exhausted"),
        },
        fallback={
            "used": bool(metadata.get("fallback_used", False)),
            "category": metadata.get("fallback_error_category"),
        },
        errors=None,
        backend_provenance={
            key: value
            for key, value in metadata.items()
            if key in {"requested_backend", "effective_backend", "p2_provenance", "p3_provenance"}
        },
    )


class P1FrozenAdapter:
    """Adapter for the frozen existing rule-based recommender (P1)."""

    system_id = "P1"
    stochastic = False

    def __init__(self, backend: RuleBasedRecommender | None = None) -> None:
        self.backend = backend or RuleBasedRecommender()
        self.corpus = build_candidate_corpus(image_catalog=self.backend.catalog)

    def frozen_provenance(self) -> Mapping[str, Any]:
        return {
            "adapter_version": P1_ADAPTER_VERSION,
            "backend_name": "rule_based",
            "backend_version": "rule-based-v1",
            "catalog_version": self.backend.catalog["catalog_version"],
            "candidate_catalog": candidate_catalog_snapshot(self.corpus),
        }

    def recommend(self, case: OfflineCaseInput, *, seed: int) -> OfflineAdapterResult:
        # P1 has no RNG.  The runner still records the derived seed for a
        # uniform, reproducible record identity.
        del seed
        recommendation = self.backend.recommend(case.request())
        profile = _normalized_profile(recommendation.profile)
        return OfflineAdapterResult(
            predicted_candidate_id=f"{profile}-{recommendation.image_id}",
            predicted_profile_id=profile,
            predicted_image_id=recommendation.image_id,
            recommendation_reasons=tuple(recommendation.reasons),
            recommendation_codes=(),
            latency_components={"total_elapsed_seconds": None, "inference_latency_seconds": None},
            fallback={"used": False, "category": None},
            errors=None,
            backend_provenance={
                "backend_name": recommendation.backend_name,
                "backend_version": recommendation.backend_version,
                "catalog_version": recommendation.catalog_version,
                "policy_version": recommendation.policy_version,
            },
        )


class P2FrozenAdapter:
    """Adapter for the frozen Structured Intent + Hybrid + Constraints P2."""

    system_id = "P2"

    def __init__(self, backend: P2Recommender | None = None) -> None:
        self.backend = backend or P2Recommender()

    @property
    def stochastic(self) -> bool:
        return bool(self.backend.network_bound)

    def frozen_provenance(self) -> Mapping[str, Any]:
        return {"adapter_version": P2_ADAPTER_VERSION, **_p2_frozen_provenance(self.backend)}

    def recommend(self, case: OfflineCaseInput, *, seed: int) -> OfflineAdapterResult:
        # Current P2 has no seed argument.  Recording this seed permits a
        # provider-backed extractor to be audited without changing P2 semantics.
        del seed
        return _detail_result(self.backend.recommend_detailed(case.request()))


class P3FrozenAdapter:
    """Adapter for P3; the runner requires explicit opt-in before using it."""

    system_id = "P3"

    def __init__(self, backend: P3Recommender | None = None) -> None:
        self.backend = backend or P3Recommender()

    @property
    def stochastic(self) -> bool:
        return bool(self.backend.network_bound)

    def frozen_provenance(self) -> Mapping[str, Any]:
        reranker = self.backend.reranker
        return {
            "adapter_version": P3_ADAPTER_VERSION,
            "backend_name": self.backend.backend_name,
            "backend_version": self.backend.backend_version,
            "pipeline_version": "p3-pipeline-v1.0.0",
            "config": _json_value(self.backend.config),
            "frozen_p2_provenance": _p2_frozen_provenance(self.backend.p2_backend),
            "candidate_catalog": candidate_catalog_snapshot(self.backend.corpus),
            "reranker_name": getattr(reranker, "reranker_name", None),
            "reranker_version": getattr(reranker, "reranker_version", None),
        }

    def recommend(self, case: OfflineCaseInput, *, seed: int) -> OfflineAdapterResult:
        del seed
        return _detail_result(self.backend.recommend_detailed(case.request()))


def default_adapters(*, enable_p3: bool = False) -> dict[str, OfflineSystemAdapter]:
    """Construct production adapters without selecting P3 implicitly."""

    adapters: dict[str, OfflineSystemAdapter] = {
        "P1": P1FrozenAdapter(),
        "P2": P2FrozenAdapter(),
    }
    if enable_p3:
        adapters["P3"] = P3FrozenAdapter()
    return adapters


__all__ = [
    "OfflineAdapterResult",
    "OfflineCaseInput",
    "OfflineSystemAdapter",
    "P1FrozenAdapter",
    "P2FrozenAdapter",
    "P3FrozenAdapter",
    "SYSTEM_IDS",
    "candidate_catalog_snapshot",
    "default_adapters",
]
