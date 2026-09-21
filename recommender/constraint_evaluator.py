"""Deterministic hard filtering and post-retrieval ranking for P2.

This module is deliberately independent of the P1 rule-based recommender.  An
``EnvironmentCandidate`` contains identifiers only, so every evaluation first
resolves and verifies those identifiers against the administrator-owned
``CandidateCorpus``.  The resulting candidate is still not directly spawnable;
conversion to ``SpawnRecommendation`` and ``PolicyValidator`` validation remain
separate mandatory boundaries.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
import json
import re
from typing import Any, ClassVar

from .candidate_corpus import CandidateCorpus, CandidateDocument
from .models import (
    ContractValidationError,
    ConstraintEvaluation,
    EnvironmentCandidate,
    GPURequirement,
    RankedCandidate,
    RetrievalHit,
    RetrievalSource,
    SoftPreferenceComponent,
    StructuredIntent,
    TaskType,
    _normalized_strings,
    _normalized_text,
    _schema_version,
    _version,
)


CONSTRAINT_EVALUATOR_VERSION = "p2-deterministic-constraint-evaluator-v1.1.0"
CONSTRAINT_POLICY_VERSION = "p2-constraint-policy-v1.0.0"
RESOURCE_COST_POLICY_VERSION = "p2-resource-cost-policy-v1.0.0"
DETERMINISTIC_RANKER_VERSION = "p2-deterministic-ranker-v2.0.0"
CONSTRAINT_RANKING_RESULT_SCHEMA_VERSION = "constraint-ranking-result-v1"

# Ranking score = RETRIEVAL_RANK_WEIGHT * retrieval rank utility
#               + SOFT_PREFERENCE_WEIGHT * normalized soft preference score
#               + RESOURCE_FIT_WEIGHT * catalog resource fit score.
#
# All three terms are in [0, 1] and the weights sum to 1, so the score is in
# [0, 1].  Ranker v1 used ``0.75 * (1 / rank) + 0.25 * soft``, whose rank-1
# floor (0.75) exceeded its rank-2 ceiling (0.625); no non-retrieval evidence
# could ever change the top-1 candidate.  v2 replaces reciprocal rank with a
# linear decay so that one retrieval rank costs a *fixed* amount of score, and
# the override budget of the other two terms is therefore statable in ranks:
#
#   one retrieval rank      = RETRIEVAL_RANK_WEIGHT * RETRIEVAL_RANK_DECAY = 0.06
#   soft preferences alone  = 0.25 / 0.06  -> promotes from rank <= 5 to rank 1
#   resource fit alone      = 0.15 / 0.06  -> promotes from rank <= 3 to rank 1
#   both together           = 0.40 / 0.06  -> promotes from rank <= 7 to rank 1
#
# Retrieval therefore remains the dominant prior over long distances while
# deterministic constraint evidence stays decisive inside a documented
# neighbourhood.
RETRIEVAL_RANK_WEIGHT = 0.60
SOFT_PREFERENCE_WEIGHT = 0.25
RESOURCE_FIT_WEIGHT = 0.15

# Retrieval rank utility decays linearly and reaches zero at the horizon.  P2's
# configured ``top_k`` is 10, so every retrieved candidate keeps a non-zero
# retrieval utility; beyond the horizon the ranking degrades gracefully to the
# explicit tie-break chain rather than inverting.
RETRIEVAL_RANK_DECAY = 0.10
RETRIEVAL_RANK_HORIZON = 11

# Relative cost of one fully-consumed catalog maximum per resource dimension.
# GPUs are weighted higher than CPU/memory because they are the scarcest
# cluster resource, not because of any measured price.  Dimensions the catalog
# does not offer at all are dropped and the remaining weights renormalized, so
# a GPU-free catalog yields an even CPU/memory split.
RESOURCE_COST_DIMENSION_WEIGHTS = (("cpu", 1.0), ("memory", 1.0), ("gpu", 2.0))

RANKING_FORMULA_CODE = (
    "ranking_formula:0.6_rank_utility_plus_0.25_soft_plus_0.15_resource_fit"
)
RANKING_TIE_BREAKER_CODE = (
    "tie_break:resource_cost_then_retrieval_rank_then_candidate_id"
)

_SEPARATOR_PATTERN = re.compile(r"[-_]+")


def retrieval_rank_utility(rank: int) -> float:
    """Linear, strictly decreasing rank prior that is zero past the horizon."""

    return max(0.0, 1.0 - RETRIEVAL_RANK_DECAY * (rank - 1))


def _semantic_fact(value: str) -> str:
    """Canonicalize separators without inventing aliases or fuzzy matches."""

    return _normalized_text(
        _SEPARATOR_PATTERN.sub(" ", value), "semantic candidate fact"
    )


def _semantic_facts(values: Collection[str]) -> frozenset[str]:
    return frozenset(_semantic_fact(value) for value in values)


def _feature_facts(candidate: CandidateDocument) -> frozenset[str]:
    return _semantic_facts(
        (
            *candidate.capabilities,
            *candidate.match_terms,
            *candidate.preference_tags,
            *candidate.suitability_tags,
        )
    )


def _task_facts(candidate: CandidateDocument) -> frozenset[str]:
    return frozenset(task.value for task in candidate.task_types)


def _constraint_label(kind: str, value: str | float) -> str:
    rendered = f"{value:g}" if isinstance(value, float) else str(value)
    return f"{kind}:{rendered}"


def _resource_cost_table(corpus: CandidateCorpus) -> dict[str, float]:
    """Normalize every candidate's footprint against the catalog maximum.

    The cost is a pure function of the administrator-owned catalog, not of the
    query or of the surviving candidate set, so a candidate's footprint score
    is stable across requests and is fully determined by the recorded catalog
    and corpus checksums.
    """

    dimensions = {
        "cpu": {
            candidate.candidate_id: candidate.resource_metadata.cpu_limit_cores
            for candidate in corpus.candidates
        },
        "memory": {
            candidate.candidate_id: candidate.resource_metadata.memory_limit_gb
            for candidate in corpus.candidates
        },
        "gpu": {
            candidate.candidate_id: float(candidate.resource_metadata.gpu_count)
            for candidate in corpus.candidates
        },
    }
    active = [
        (name, weight, max(dimensions[name].values(), default=0.0))
        for name, weight in RESOURCE_COST_DIMENSION_WEIGHTS
        if max(dimensions[name].values(), default=0.0) > 0.0
    ]
    total_weight = sum(weight for _, weight, _ in active)
    if not active or total_weight <= 0.0:
        # A catalog that advertises no CPU, memory or GPU at all carries no
        # discriminating footprint evidence; every candidate costs the same.
        return {candidate.candidate_id: 0.0 for candidate in corpus.candidates}
    return {
        candidate.candidate_id: min(
            1.0,
            max(
                0.0,
                sum(
                    weight * (dimensions[name][candidate.candidate_id] / maximum)
                    for name, weight, maximum in active
                )
                / total_weight,
            ),
        )
        for candidate in corpus.candidates
    }


def _component(
    preference: str,
    *,
    matched: bool,
    explanation_code: str,
) -> SoftPreferenceComponent:
    return SoftPreferenceComponent(
        preference=preference,
        matched=matched,
        weight=1.0,
        score=1.0 if matched else 0.0,
        explanation_code=explanation_code,
    )


@dataclass(frozen=True, slots=True)
class ConstraintRankingResult:
    """Validated batch outcome, including an explicit no-feasible result."""

    evaluations: tuple[ConstraintEvaluation, ...]
    ranked_candidates: tuple[RankedCandidate, ...]
    no_feasible_candidate: bool
    unmet_constraints: tuple[str, ...]
    unsupported_constraints: tuple[str, ...]
    explanation_codes: tuple[str, ...]
    evaluator_version: str = CONSTRAINT_EVALUATOR_VERSION
    constraint_policy_version: str = CONSTRAINT_POLICY_VERSION
    ranker_version: str = DETERMINISTIC_RANKER_VERSION
    schema_version: str = CONSTRAINT_RANKING_RESULT_SCHEMA_VERSION

    SUPPORTED_SCHEMA_VERSION: ClassVar[str] = CONSTRAINT_RANKING_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        evaluations = tuple(sorted(self.evaluations, key=lambda item: item.candidate_id))
        if not all(isinstance(item, ConstraintEvaluation) for item in evaluations):
            raise ContractValidationError(
                "evaluations must contain only ConstraintEvaluation objects"
            )
        if len({item.candidate_id for item in evaluations}) != len(evaluations):
            raise ContractValidationError("evaluation candidate IDs must be unique")
        object.__setattr__(self, "evaluations", evaluations)

        ranked = tuple(sorted(self.ranked_candidates, key=lambda item: item.rank))
        if not all(isinstance(item, RankedCandidate) for item in ranked):
            raise ContractValidationError(
                "ranked_candidates must contain only RankedCandidate objects"
            )
        if [item.rank for item in ranked] != list(range(1, len(ranked) + 1)):
            raise ContractValidationError("ranked candidate ranks must be contiguous from 1")
        feasible_ids = {item.candidate_id for item in evaluations if item.feasible}
        if {item.candidate_id for item in ranked} != feasible_ids:
            raise ContractValidationError(
                "ranked candidates must be exactly the feasible evaluated candidates"
            )
        object.__setattr__(self, "ranked_candidates", ranked)

        if not isinstance(self.no_feasible_candidate, bool):
            raise ContractValidationError("no_feasible_candidate must be a boolean")
        if self.no_feasible_candidate != (not feasible_ids):
            raise ContractValidationError(
                "no_feasible_candidate must reflect the evaluated candidate set"
            )
        for name in (
            "unmet_constraints",
            "unsupported_constraints",
            "explanation_codes",
        ):
            object.__setattr__(
                self, name, _normalized_strings(getattr(self, name), name)
            )
        if self.no_feasible_candidate and not self.explanation_codes:
            raise ContractValidationError(
                "a no-feasible-candidate result requires an explanation code"
            )
        for name in (
            "evaluator_version",
            "constraint_policy_version",
            "ranker_version",
        ):
            object.__setattr__(self, name, _version(getattr(self, name), name))
        if any(
            item.evaluator_version != self.evaluator_version
            or item.constraint_policy_version != self.constraint_policy_version
            for item in evaluations
        ):
            raise ContractValidationError(
                "all evaluations must match the result evaluator and constraint policy versions"
            )
        if any(item.ranker_version != self.ranker_version for item in ranked):
            raise ContractValidationError(
                "all ranked candidates must match the result ranker_version"
            )
        object.__setattr__(
            self,
            "schema_version",
            _schema_version(self.schema_version, self.SUPPORTED_SCHEMA_VERSION),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluations": [item.to_dict() for item in self.evaluations],
            "ranked_candidates": [item.to_dict() for item in self.ranked_candidates],
            "no_feasible_candidate": self.no_feasible_candidate,
            "unmet_constraints": list(self.unmet_constraints),
            "unsupported_constraints": list(self.unsupported_constraints),
            "explanation_codes": list(self.explanation_codes),
            "evaluator_version": self.evaluator_version,
            "constraint_policy_version": self.constraint_policy_version,
            "ranker_version": self.ranker_version,
            "schema_version": self.schema_version,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )


class ConstraintEvaluator:
    """Evaluate P2 constraints using only version-matched corpus facts."""

    evaluator_version = CONSTRAINT_EVALUATOR_VERSION
    constraint_policy_version = CONSTRAINT_POLICY_VERSION
    resource_cost_policy_version = RESOURCE_COST_POLICY_VERSION
    ranker_version = DETERMINISTIC_RANKER_VERSION

    def __init__(self, corpus: CandidateCorpus) -> None:
        if not isinstance(corpus, CandidateCorpus):
            raise ContractValidationError("corpus must be a CandidateCorpus")
        self.corpus = corpus
        self._resource_cost = _resource_cost_table(corpus)
        self._supported_features = frozenset().union(
            *(_feature_facts(candidate) for candidate in corpus.candidates)
        )
        self._supported_frameworks = frozenset().union(
            *(_semantic_facts(candidate.frameworks) for candidate in corpus.candidates)
        )
        self._supported_libraries = frozenset().union(
            *(_semantic_facts(candidate.libraries) for candidate in corpus.candidates)
        )
        self._corpus_has_gpu = any(
            candidate.resource_metadata.gpu_count > 0
            for candidate in corpus.candidates
        )

    def resource_cost_score(self, candidate_id: str) -> float:
        """Catalog-normalized resource footprint of a trusted candidate."""

        try:
            return self._resource_cost[candidate_id]
        except KeyError:
            raise ContractValidationError(
                f"candidate_id {candidate_id!r} is not in the trusted corpus"
            ) from None

    def resource_fit_score(self, candidate_id: str) -> float:
        """Reward for a smaller footprint: ``1 - resource_cost_score``.

        Feasibility already establishes that the candidate clears every hard
        floor, so among feasible candidates a lower cost means *smaller
        sufficient*, which is the preference the gold labels encode.
        """

        return 1.0 - self.resource_cost_score(candidate_id)

    def _resolve(self, candidate: EnvironmentCandidate) -> CandidateDocument:
        if not isinstance(candidate, EnvironmentCandidate):
            raise ContractValidationError("candidate must be an EnvironmentCandidate")
        document = self.corpus.get(candidate.candidate_id)
        if document is None:
            raise ContractValidationError(
                f"candidate_id {candidate.candidate_id!r} is not in the trusted corpus"
            )
        expected = document.to_environment_candidate()
        if candidate != expected:
            raise ContractValidationError(
                "candidate identifiers or provenance do not match the trusted corpus"
            )
        return document

    def evaluate(
        self,
        structured_intent: StructuredIntent,
        candidate: EnvironmentCandidate,
    ) -> ConstraintEvaluation:
        if not isinstance(structured_intent, StructuredIntent):
            raise ContractValidationError(
                "structured_intent must be a StructuredIntent"
            )
        document = self._resolve(candidate)
        matched_hard: list[str] = []
        violated_hard: list[str] = []
        unsupported: list[str] = []
        components: list[SoftPreferenceComponent] = []
        explanations: set[str] = set()

        feature_facts = _feature_facts(document)
        framework_facts = _semantic_facts(document.frameworks)
        library_facts = _semantic_facts(document.libraries)

        required_features = _semantic_facts(structured_intent.required_features)
        forbidden_features = _semantic_facts(structured_intent.forbidden_features)
        canonical_conflicts = required_features & forbidden_features
        if canonical_conflicts:
            raise ContractValidationError(
                "features become both required and forbidden after constraint "
                "normalization: " + ", ".join(sorted(canonical_conflicts))
            )
        required_frameworks = _semantic_facts(structured_intent.required_frameworks)
        required_libraries = _semantic_facts(structured_intent.required_libraries)
        preferred_features = _semantic_facts(
            structured_intent.preferred_features
        ) - required_features - forbidden_features
        preferred_frameworks = _semantic_facts(
            structured_intent.preferred_frameworks
        ) - required_frameworks
        preferred_libraries = _semantic_facts(
            structured_intent.preferred_libraries
        ) - required_libraries

        hard_groups = (
            (
                "feature",
                required_features,
                feature_facts,
                self._supported_features,
            ),
            (
                "framework",
                required_frameworks,
                framework_facts,
                self._supported_frameworks,
            ),
            (
                "library",
                required_libraries,
                library_facts,
                self._supported_libraries,
            ),
        )
        for kind, requirements, candidate_facts, supported_facts in hard_groups:
            for requirement in sorted(requirements):
                label = _constraint_label(kind, requirement)
                if requirement in candidate_facts:
                    matched_hard.append(label)
                    explanations.add(f"hard_{kind}_matched")
                else:
                    violated_hard.append(label)
                    explanations.add(f"hard_{kind}_violated")
                    if requirement not in supported_facts:
                        unsupported.append(label)
                        explanations.add(f"hard_{kind}_unsupported")

        for feature in sorted(forbidden_features):
            label = _constraint_label("forbidden_feature", feature)
            if feature in feature_facts:
                violated_hard.append(label)
                explanations.add("forbidden_feature_present")
            elif feature in self._supported_features:
                matched_hard.append(label)
                explanations.add("forbidden_feature_absent")
            else:
                unsupported.append(label)
                explanations.add("forbidden_feature_unsupported")

        constraints = structured_intent.resource_constraints
        gpu_present = document.resource_metadata.gpu_count > 0
        if constraints.gpu_requirement is GPURequirement.REQUIRED:
            label = "gpu:required"
            if gpu_present:
                matched_hard.append(label)
                explanations.add("gpu_required_matched")
            else:
                violated_hard.append(label)
                explanations.add("gpu_required_violated")
                if not self._corpus_has_gpu:
                    unsupported.append(label)
                    explanations.add("gpu_required_unsupported_by_corpus")
        elif constraints.gpu_requirement is GPURequirement.FORBIDDEN:
            label = "gpu:forbidden"
            if gpu_present:
                violated_hard.append(label)
                explanations.add("gpu_forbidden_present")
            else:
                matched_hard.append(label)
                explanations.add("gpu_forbidden_absent")
        elif constraints.gpu_requirement is GPURequirement.PREFERRED:
            components.append(
                _component(
                    "gpu:preferred",
                    matched=gpu_present,
                    explanation_code=(
                        "gpu_preferred_matched"
                        if gpu_present
                        else "gpu_preferred_unmatched"
                    ),
                )
            )
            if not self._corpus_has_gpu:
                unsupported.append("gpu:preferred")
                explanations.add("gpu_preferred_unsupported_by_corpus")
        # NOT_NEEDED and UNSPECIFIED are both non-preferences.  They neither
        # require nor penalize a GPU-capable candidate.

        numeric_constraints = (
            (
                "minimum_cpu_cores",
                constraints.minimum_cpu_cores,
                document.resource_metadata.cpu_limit_cores,
            ),
            (
                "minimum_memory_gb",
                constraints.minimum_memory_gb,
                document.resource_metadata.memory_limit_gb,
            ),
        )
        for kind, minimum, available in numeric_constraints:
            if minimum is None:
                continue
            label = _constraint_label(kind, minimum)
            if available >= minimum:
                matched_hard.append(label)
                explanations.add(f"{kind}_matched")
            else:
                violated_hard.append(label)
                explanations.add(f"{kind}_violated")

        soft_groups = (
            (
                "preferred_feature",
                preferred_features,
                feature_facts,
                self._supported_features,
            ),
            (
                "preferred_framework",
                preferred_frameworks,
                framework_facts,
                self._supported_frameworks,
            ),
            (
                "preferred_library",
                preferred_libraries,
                library_facts,
                self._supported_libraries,
            ),
        )
        for kind, preferences, candidate_facts, supported_facts in soft_groups:
            for preference in sorted(preferences):
                label = _constraint_label(kind, preference)
                is_matched = preference in candidate_facts
                components.append(
                    _component(
                        label,
                        matched=is_matched,
                        explanation_code=(
                            f"{kind}_matched" if is_matched else f"{kind}_unmatched"
                        ),
                    )
                )
                if preference not in supported_facts:
                    unsupported.append(label)
                    explanations.add(f"{kind}_unsupported")

        task_facts = _task_facts(document)
        for task_type in structured_intent.task_types:
            if task_type is TaskType.UNSPECIFIED:
                continue
            label = _constraint_label("task_type", task_type.value)
            is_matched = task_type.value in task_facts
            components.append(
                _component(
                    label,
                    matched=is_matched,
                    explanation_code=(
                        "task_type_matched" if is_matched else "task_type_unmatched"
                    ),
                )
            )

        soft_score = (
            sum(item.score for item in components)
            / sum(item.weight for item in components)
            if components
            else 0.0
        )
        feasible = not violated_hard
        explanations.add("candidate_feasible" if feasible else "candidate_infeasible")
        return ConstraintEvaluation(
            candidate_id=candidate.candidate_id,
            feasible=feasible,
            matched_hard_constraints=tuple(matched_hard),
            violated_hard_constraints=tuple(violated_hard),
            unsupported_constraints=tuple(unsupported),
            soft_preference_score=soft_score,
            soft_preference_components=tuple(components),
            explanation_codes=tuple(explanations),
            evaluator_version=self.evaluator_version,
            constraint_policy_version=self.constraint_policy_version,
        )

    def evaluate_and_rank(
        self,
        structured_intent: StructuredIntent,
        candidates: Sequence[EnvironmentCandidate],
        retrieval_hits: Sequence[RetrievalHit],
    ) -> ConstraintRankingResult:
        """Hard-filter candidates, then rank feasible IDs reproducibly.

        The frozen score is ``0.60 * retrieval_rank_utility(fused_rank)
        + 0.25 * soft_score + 0.15 * resource_fit_score``.  Hard constraints
        establish sufficiency; the resource-fit term then makes the *smallest
        sufficient* candidate the preferred one, and the soft-preference term
        carries enough weight to change the top-1 candidate (see the weight
        commentary at the top of this module).

        Exact score ties are broken by ascending resource cost — the explicit
        minimal-sufficient rule — then by ascending fused retrieval rank, then
        by ascending candidate ID for a total deterministic order.
        """

        if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
            raise ContractValidationError("candidates must be a sequence")
        if isinstance(retrieval_hits, (str, bytes)) or not isinstance(
            retrieval_hits, Sequence
        ):
            raise ContractValidationError("retrieval_hits must be a sequence")
        candidate_items = tuple(candidates)
        hit_items = tuple(retrieval_hits)
        if not all(isinstance(item, EnvironmentCandidate) for item in candidate_items):
            raise ContractValidationError(
                "candidates must contain only EnvironmentCandidate objects"
            )
        if not all(isinstance(item, RetrievalHit) for item in hit_items):
            raise ContractValidationError(
                "retrieval_hits must contain only RetrievalHit objects"
            )
        candidate_ids = [item.candidate_id for item in candidate_items]
        hit_ids = [item.candidate_id for item in hit_items]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ContractValidationError("candidate IDs must be unique")
        if len(set(hit_ids)) != len(hit_ids):
            raise ContractValidationError("retrieval hit candidate IDs must be unique")
        if len({item.rank for item in hit_items}) != len(hit_items):
            raise ContractValidationError("fused retrieval hit ranks must be unique")
        if any(item.source is not RetrievalSource.FUSED for item in hit_items):
            raise ContractValidationError("ranking requires fused retrieval hits")
        if set(candidate_ids) != set(hit_ids):
            raise ContractValidationError(
                "candidate IDs must exactly match fused retrieval hit IDs"
            )

        evaluations = tuple(
            self.evaluate(structured_intent, candidate) for candidate in candidate_items
        )
        evaluation_by_id = {item.candidate_id: item for item in evaluations}
        hit_by_id = {item.candidate_id: item for item in hit_items}
        scored: list[tuple[float, float, int, str]] = []
        for candidate_id, evaluation in evaluation_by_id.items():
            if not evaluation.feasible:
                continue
            hit = hit_by_id[candidate_id]
            cost = self.resource_cost_score(candidate_id)
            score = round(
                RETRIEVAL_RANK_WEIGHT * retrieval_rank_utility(hit.rank)
                + SOFT_PREFERENCE_WEIGHT * evaluation.soft_preference_score
                + RESOURCE_FIT_WEIGHT * (1.0 - cost),
                12,
            )
            scored.append((score, cost, hit.rank, candidate_id))

        scored.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))
        ranked = tuple(
            RankedCandidate(
                candidate_id=candidate_id,
                rank=rank,
                score=score,
                ranking_reasons=(
                    RANKING_FORMULA_CODE,
                    RANKING_TIE_BREAKER_CODE,
                    f"retrieval_rank:{retrieval_rank}",
                    f"retrieval_rank_utility:{retrieval_rank_utility(retrieval_rank):.12g}",
                    "soft_preference_score:"
                    f"{evaluation_by_id[candidate_id].soft_preference_score:.12g}",
                    f"resource_cost_score:{cost:.12g}",
                    f"resource_fit_score:{1.0 - cost:.12g}",
                ),
                ranker_version=self.ranker_version,
            )
            for rank, (score, cost, retrieval_rank, candidate_id) in enumerate(
                scored, start=1
            )
        )

        no_feasible = not ranked
        unmet = tuple(
            sorted(
                {
                    constraint
                    for evaluation in evaluations
                    for constraint in evaluation.violated_hard_constraints
                }
            )
        )
        unsupported = tuple(
            sorted(
                {
                    constraint
                    for evaluation in evaluations
                    for constraint in evaluation.unsupported_constraints
                }
            )
        )
        if no_feasible and evaluations:
            result_codes = ("no_feasible_candidate", "unmet_hard_constraint")
        elif no_feasible:
            result_codes = ("candidate_pool_empty", "no_feasible_candidate")
        else:
            result_codes = ("feasible_candidates_ranked",)
        return ConstraintRankingResult(
            evaluations=evaluations,
            ranked_candidates=ranked,
            no_feasible_candidate=no_feasible,
            unmet_constraints=unmet,
            unsupported_constraints=unsupported,
            explanation_codes=result_codes,
        )
