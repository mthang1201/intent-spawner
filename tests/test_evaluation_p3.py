"""Tests for frozen paired P2-versus-P3 evaluation evidence."""

from __future__ import annotations

import json

from evaluation_p3 import runner
from evaluation_p3.metrics import aggregate_metrics
from recommender.external_llm import ExternalLLMConfig
from recommender.models import RankedCandidate
from recommender.p3_reranker import P3RerankingResult


class IdentityEvaluationReranker:
    reranker_name = "test-identity-reranker"
    reranker_version = "test-identity-reranker-v1"
    prompt_version = "test-prompt-v1"
    prompt_sha256 = "a" * 64

    def __init__(self, config):
        self.config = config

    def rerank(
        self,
        request,
        structured_intent,
        feasible_candidates,
        corpus,
        constraint_evaluations,
        deterministic_ranked,
        *,
        deadline=None,
    ):
        ranked = tuple(
            RankedCandidate(
                candidate_id=item.candidate_id,
                rank=item.rank,
                score=min(1.0, item.score),
                ranking_reasons=(
                    "p3_llm_reranked",
                    "p3_explanation:identity test order",
                ),
                ranker_version=self.reranker_version,
            )
            for item in deterministic_ranked
        )
        return P3RerankingResult(
            reranked_candidates=ranked,
            degraded=False,
            reranker_name=self.reranker_name,
            reranker_version=self.reranker_version,
            prompt_version=self.prompt_version,
            prompt_sha256=self.prompt_sha256,
            model_id=self.config.model,
            raw_response=json.dumps(
                {"ranking": [item.candidate_id for item in ranked]}
            ),
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            inference_latency_seconds=0.01,
            attempt_count=1,
        )


def test_frozen_inputs_and_paired_runner_match_reference_p2(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "P3Reranker", IdentityEvaluationReranker)
    target = runner.run_evaluation(
        llm_config=ExternalLLMConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="test-identity-model",
            api_key="test-only",
            max_retries=0,
        ),
        provider_provenance="mocked unit-test provider; no observed quality claim",
        output_root=tmp_path,
        run_id="test-p2-p3-paired",
    )

    manifest = json.loads((target / "manifest.json").read_text())
    metrics = json.loads((target / "aggregates/metrics.json").read_text())
    transitions = json.loads(
        (target / "analysis/error_transitions.json").read_text()
    )
    paired = json.loads((target / "analysis/paired_changes.json").read_text())

    assert manifest["frozen_inputs"]["p2_reference"]["matched"] is True
    assert manifest["frozen_inputs"]["p2_reference"]["matched_sample_count"] == 66
    assert manifest["b0_user_experiments"] == "not_performed"
    assert metrics["incremental_delta_p3_minus_p2"]["top1_accuracy"] == 0
    assert metrics["p3_correctness_cost_complexity"][
        "invalid_reranker_output_rate"
    ]["value"] == 0
    assert sum(transitions["counts"].values()) == 66
    assert len(paired["queries"]) == 66


def test_transition_categories_use_acceptable_top1_correctness():
    items = []
    predictions = []
    outcomes = (
        ("wrong-candidate", "acceptable", "p2_wrong_to_p3_correct"),
        ("acceptable", "acceptable", "p2_correct_to_p3_correct"),
        ("acceptable", "wrong-candidate", "p2_correct_to_p3_wrong"),
        ("wrong-candidate", "other-wrong", "p2_wrong_to_p3_wrong"),
    )
    for index, (p2_top, p3_top, _) in enumerate(outcomes):
        sample_id = f"sample-{index}"
        items.append(
            {
                "sample_id": sample_id,
                "workload_family": "test",
                "gold": {
                    "preferred_candidate_id": "acceptable",
                    "acceptable_candidate_ids": ["acceptable"],
                    "request_feasible": True,
                },
            }
        )
        for system, top in (("p2", p2_top), ("p3", p3_top)):
            predictions.append(
                {
                    "system": system,
                    "sample_id": sample_id,
                    "ranked_candidate_ids": [top],
                    "constraint_violated": False,
                    "latency_seconds": 1.0 if system == "p2" else 2.0,
                    "reranker_invoked": system == "p3",
                    "reranker_degraded": False,
                    "invalid_reranker_output": False,
                    "provider_failure": False,
                    "selected_outside_p2_feasible": False,
                }
            )

    _, paired, transitions = aggregate_metrics({"items": items}, predictions)

    assert transitions["counts"] == {name: 1 for _, _, name in outcomes}
    assert [item["transition"] for item in paired["queries"]] == [
        expected for _, _, expected in outcomes
    ]


def test_infeasible_detection_counts_as_query_correctness():
    dataset = {
        "items": [
            {
                "sample_id": "infeasible",
                "workload_family": "test",
                "gold": {
                    "preferred_candidate_id": None,
                    "acceptable_candidate_ids": [],
                    "request_feasible": False,
                },
            }
        ]
    }
    predictions = [
        {
            "system": "p2",
            "sample_id": "infeasible",
            "ranked_candidate_ids": [],
            "detected_infeasible": True,
            "constraint_violated": True,
            "latency_seconds": 0.01,
        },
        {
            "system": "p3",
            "sample_id": "infeasible",
            "ranked_candidate_ids": [],
            "detected_infeasible": True,
            "constraint_violated": True,
            "latency_seconds": 0.02,
            "reranker_invoked": False,
            "reranker_degraded": True,
            "invalid_reranker_output": False,
            "provider_failure": False,
            "selected_outside_p2_feasible": False,
        },
    ]

    _, paired, transitions = aggregate_metrics(dataset, predictions)

    assert transitions["counts"]["p2_correct_to_p3_correct"] == 1
    assert paired["queries"][0]["p2_query_correct"] is True
    assert paired["queries"][0]["p3_query_correct"] is True
