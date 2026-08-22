"""Focused tests for P2 deterministic constraint filtering and ranking."""

from __future__ import annotations

from dataclasses import replace

import pytest

from recommender.candidate_corpus import load_candidate_corpus
from recommender.constraint_evaluator import (
    CONSTRAINT_EVALUATOR_VERSION,
    CONSTRAINT_POLICY_VERSION,
    DETERMINISTIC_RANKER_VERSION,
    ConstraintEvaluator,
)
from recommender.models import (
    ContractValidationError,
    EnvironmentCandidate,
    GPURequirement,
    ResourceConstraints,
    RetrievalHit,
    RetrievalSource,
    StructuredIntent,
)


@pytest.fixture
def corpus():
    return load_candidate_corpus()


@pytest.fixture
def evaluator(corpus):
    return ConstraintEvaluator(corpus)


def _candidate(corpus, candidate_id: str) -> EnvironmentCandidate:
    document = corpus.get(candidate_id)
    assert document is not None
    return document.to_environment_candidate()


def _hit(candidate_id: str, rank: int) -> RetrievalHit:
    return RetrievalHit(
        candidate_id=candidate_id,
        source=RetrievalSource.FUSED,
        rank=rank,
        score=1.0 / (60 + rank),
        retriever_version="hybrid-v1",
        index_version="hybrid-index-v1",
    )


@pytest.mark.parametrize(
    ("intent", "matching_id", "nonmatching_id", "constraint"),
    [
        (
            StructuredIntent(required_features=("visualization",)),
            "medium-scipy-data-science",
            "medium-minimal-python",
            "feature:visualization",
        ),
        (
            StructuredIntent(required_frameworks=("pytorch",)),
            "medium-pytorch-deep-learning",
            "medium-tensorflow-deep-learning",
            "framework:pytorch",
        ),
        (
            StructuredIntent(required_libraries=("pandas",)),
            "medium-scipy-data-science",
            "medium-minimal-python",
            "library:pandas",
        ),
    ],
)
def test_required_semantic_facts_are_hard_constraints(
    evaluator, corpus, intent, matching_id, nonmatching_id, constraint
):
    matching = evaluator.evaluate(intent, _candidate(corpus, matching_id))
    nonmatching = evaluator.evaluate(intent, _candidate(corpus, nonmatching_id))

    assert matching.feasible is True
    assert constraint in matching.matched_hard_constraints
    assert nonmatching.feasible is False
    assert constraint in nonmatching.violated_hard_constraints
    assert nonmatching.unsupported_constraints == ()


def test_unsupported_required_fact_fails_closed(evaluator, corpus):
    evaluation = evaluator.evaluate(
        StructuredIntent(required_features=("quantum accelerator",)),
        _candidate(corpus, "large-minimal-python"),
    )

    assert evaluation.feasible is False
    assert evaluation.violated_hard_constraints == (
        "feature:quantum accelerator",
    )
    assert evaluation.unsupported_constraints == (
        "feature:quantum accelerator",
    )
    assert "hard_feature_unsupported" in evaluation.explanation_codes


def test_preferred_feature_mismatch_penalizes_but_remains_feasible(evaluator, corpus):
    intent = StructuredIntent(preferred_features=("visualization",))
    matching = evaluator.evaluate(
        intent, _candidate(corpus, "small-scipy-data-science")
    )
    nonmatching = evaluator.evaluate(
        intent, _candidate(corpus, "small-minimal-python")
    )

    assert matching.feasible is nonmatching.feasible is True
    assert matching.soft_preference_score == 1.0
    assert nonmatching.soft_preference_score == 0.0
    assert matching.soft_preference_components[0].matched is True
    assert nonmatching.soft_preference_components[0].matched is False


def test_unsupported_preference_is_reported_without_hard_failure(evaluator, corpus):
    evaluation = evaluator.evaluate(
        StructuredIntent(preferred_libraries=("nonexistent-library",)),
        _candidate(corpus, "small-minimal-python"),
    )

    assert evaluation.feasible is True
    assert evaluation.soft_preference_score == 0.0
    assert evaluation.unsupported_constraints == (
        "preferred_library:nonexistent library",
    )


def test_forbidden_feature_present_is_infeasible_and_absent_is_feasible(
    evaluator, corpus
):
    intent = StructuredIntent(forbidden_features=("visualization",))
    present = evaluator.evaluate(
        intent, _candidate(corpus, "large-scipy-data-science")
    )
    absent = evaluator.evaluate(
        intent, _candidate(corpus, "large-minimal-python")
    )

    assert present.feasible is False
    assert present.violated_hard_constraints == (
        "forbidden_feature:visualization",
    )
    assert absent.feasible is True
    assert absent.matched_hard_constraints == (
        "forbidden_feature:visualization",
    )


def test_unknown_forbidden_feature_is_explicitly_unsupported_but_not_assumed_present(
    evaluator, corpus
):
    evaluation = evaluator.evaluate(
        StructuredIntent(forbidden_features=("proprietary compiler",)),
        _candidate(corpus, "large-minimal-python"),
    )

    assert evaluation.feasible is True
    assert evaluation.unsupported_constraints == (
        "forbidden_feature:proprietary compiler",
    )
    assert evaluation.violated_hard_constraints == ()


@pytest.mark.parametrize(
    ("profile", "feasible"),
    [("small", False), ("medium", False), ("large", True)],
)
def test_explicit_cpu_and_memory_minimums_are_hard_constraints(
    evaluator, corpus, profile, feasible
):
    intent = StructuredIntent(
        resource_constraints=ResourceConstraints(
            minimum_cpu_cores=1.5,
            minimum_memory_gb=1.5,
        )
    )
    evaluation = evaluator.evaluate(
        intent, _candidate(corpus, f"{profile}-minimal-python")
    )

    assert evaluation.feasible is feasible
    expected = {"minimum_cpu_cores:1.5", "minimum_memory_gb:1.5"}
    actual = (
        set(evaluation.matched_hard_constraints)
        if feasible
        else set(evaluation.violated_hard_constraints)
    )
    assert actual == expected


def test_unspecified_constraints_add_no_penalty(evaluator, corpus):
    evaluation = evaluator.evaluate(
        StructuredIntent(), _candidate(corpus, "small-minimal-python")
    )

    assert evaluation.feasible is True
    assert evaluation.matched_hard_constraints == ()
    assert evaluation.violated_hard_constraints == ()
    assert evaluation.unsupported_constraints == ()
    assert evaluation.soft_preference_score == 0.0
    assert evaluation.soft_preference_components == ()


def test_current_no_gpu_corpus_produces_explicit_no_feasible_result(
    evaluator, corpus
):
    intent = StructuredIntent(
        resource_constraints=ResourceConstraints(
            gpu_requirement=GPURequirement.REQUIRED
        )
    )
    candidates = tuple(document.to_environment_candidate() for document in corpus)
    hits = tuple(_hit(candidate.candidate_id, rank) for rank, candidate in enumerate(candidates, 1))

    result = evaluator.evaluate_and_rank(intent, candidates, hits)

    assert len(result.evaluations) == 12
    assert all(not evaluation.feasible for evaluation in result.evaluations)
    assert all(
        evaluation.violated_hard_constraints == ("gpu:required",)
        for evaluation in result.evaluations
    )
    assert all(
        evaluation.unsupported_constraints == ("gpu:required",)
        for evaluation in result.evaluations
    )
    assert result.no_feasible_candidate is True
    assert result.ranked_candidates == ()
    assert result.unmet_constraints == ("gpu:required",)
    assert result.unsupported_constraints == ("gpu:required",)
    assert "no_feasible_candidate" in result.explanation_codes


def test_numeric_requirement_can_produce_no_feasible_candidates(evaluator, corpus):
    intent = StructuredIntent(
        resource_constraints=ResourceConstraints(minimum_memory_gb=3.0)
    )
    candidates = tuple(
        _candidate(corpus, candidate_id)
        for candidate_id in (
            "small-minimal-python",
            "medium-minimal-python",
            "large-minimal-python",
        )
    )
    result = evaluator.evaluate_and_rank(
        intent,
        candidates,
        tuple(_hit(candidate.candidate_id, rank) for rank, candidate in enumerate(candidates, 1)),
    )

    assert result.no_feasible_candidate is True
    assert result.unmet_constraints == ("minimum_memory_gb:3",)


def test_multiple_feasible_candidates_use_retrieval_and_soft_preferences(
    evaluator, corpus
):
    intent = StructuredIntent(preferred_features=("visualization",))
    candidate_ids = (
        "medium-minimal-python",
        "medium-scipy-data-science",
        "medium-pytorch-deep-learning",
    )
    candidates = tuple(_candidate(corpus, item) for item in candidate_ids)
    hits = tuple(_hit(candidate_id, rank) for rank, candidate_id in enumerate(candidate_ids, 1))

    result = evaluator.evaluate_and_rank(intent, candidates, hits)

    assert result.no_feasible_candidate is False
    assert len(result.ranked_candidates) == 3
    assert [item.candidate_id for item in result.ranked_candidates] == [
        "medium-minimal-python",
        "medium-scipy-data-science",
        "medium-pytorch-deep-learning",
    ]
    scores = {item.candidate_id: item.score for item in result.ranked_candidates}
    assert scores["medium-minimal-python"] == 0.75
    assert scores["medium-scipy-data-science"] == 0.625
    assert scores["medium-pytorch-deep-learning"] == 0.25


def test_deterministic_tie_breaker_and_input_order_invariance(evaluator, corpus):
    intent = StructuredIntent(preferred_features=("numpy", "tensorflow"))
    first_id = "medium-pytorch-deep-learning"
    second_id = "medium-scipy-data-science"
    candidates = (_candidate(corpus, first_id), _candidate(corpus, second_id))
    hits = (_hit(first_id, 2), _hit(second_id, 3))

    forward = evaluator.evaluate_and_rank(intent, candidates, hits)
    reverse = evaluator.evaluate_and_rank(
        intent, tuple(reversed(candidates)), tuple(reversed(hits))
    )

    assert forward.to_json() == reverse.to_json()
    assert [item.score for item in forward.ranked_candidates] == [0.375, 0.375]
    assert [item.candidate_id for item in forward.ranked_candidates] == [
        first_id,
        second_id,
    ]


def test_all_configured_profile_image_combinations_are_evaluable(evaluator, corpus):
    evaluations = tuple(
        evaluator.evaluate(StructuredIntent(), document.to_environment_candidate())
        for document in corpus
    )

    assert len(evaluations) == 12
    assert tuple(item.candidate_id for item in evaluations) == corpus.candidate_ids
    assert all(item.feasible for item in evaluations)
    assert {corpus.get(item.candidate_id).profile_id for item in evaluations} == {
        "small",
        "medium",
        "large",
    }
    assert {corpus.get(item.candidate_id).image_id for item in evaluations} == {
        "minimal-python",
        "scipy-data-science",
        "pytorch-deep-learning",
        "tensorflow-deep-learning",
    }


def test_candidate_must_resolve_with_exact_trusted_provenance(evaluator, corpus):
    candidate = _candidate(corpus, "large-minimal-python")

    with pytest.raises(ContractValidationError, match="trusted corpus"):
        evaluator.evaluate(
            StructuredIntent(), replace(candidate, profile_id="medium")
        )


def test_output_records_frozen_evaluator_and_ranker_provenance(evaluator, corpus):
    candidate = _candidate(corpus, "small-minimal-python")
    result = evaluator.evaluate_and_rank(
        StructuredIntent(), (candidate,), (_hit(candidate.candidate_id, 1),)
    )

    assert result.evaluations[0].evaluator_version == CONSTRAINT_EVALUATOR_VERSION
    assert result.evaluations[0].constraint_policy_version == CONSTRAINT_POLICY_VERSION
    assert result.ranked_candidates[0].ranker_version == DETERMINISTIC_RANKER_VERSION
