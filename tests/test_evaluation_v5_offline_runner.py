from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation_v5.offline.recommenders import (
    OfflineAdapterResult,
    OfflineCaseInput,
    P1FrozenAdapter,
    P2FrozenAdapter,
    candidate_catalog_snapshot,
)
from evaluation_v5.offline.runner import (
    DuplicateRecordError,
    EvidenceRecordError,
    ProvenanceMismatchError,
    build_execution_matrix,
    run_offline_recommendations,
)
from evaluation_v5.split_dataset import load_development_split
from recommender.candidate_corpus import load_candidate_corpus


FAKE_CANDIDATE_CATALOG = candidate_catalog_snapshot(load_candidate_corpus())


class FakeAdapter:
    def __init__(
        self,
        system_id: str,
        *,
        stochastic: bool = False,
        interrupt_on_call: int | None = None,
        fail_on_call: int | None = None,
        fallback_category: str | None = None,
    ) -> None:
        self.system_id = system_id
        self.stochastic = stochastic
        self.interrupt_on_call = interrupt_on_call
        self.fail_on_call = fail_on_call
        self.fallback_category = fallback_category
        self.calls: list[tuple[str, int]] = []

    def frozen_provenance(self):
        return {
            "adapter_version": "fake-adapter-v1",
            "system_version": f"fake-{self.system_id.lower()}-v1",
            "candidate_catalog": FAKE_CANDIDATE_CATALOG,
        }

    def recommend(self, case: OfflineCaseInput, *, seed: int) -> OfflineAdapterResult:
        self.calls.append((case.case_id, seed))
        if self.interrupt_on_call == len(self.calls):
            raise KeyboardInterrupt("test interruption")
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("benchmark text must not be persisted")
        candidate = "small-minimal-python"
        return OfflineAdapterResult(
            predicted_candidate_id=candidate,
            predicted_profile_id="small",
            predicted_image_id="minimal-python",
            recommendation_reasons=("fake_reason",),
            recommendation_codes=("fake_code",),
            structured_intent={"schema_version": "fake-intent-v1", "task": "fake"},
            sparse_ranks=(
                {"candidate_id": candidate, "rank": 1, "score": 0.8},
            ),
            dense_ranks=(
                {"candidate_id": candidate, "rank": 1, "score": 0.7},
            ),
            hybrid_ranks_scores=(
                {"candidate_id": candidate, "rank": 1, "score": 0.9},
            ),
            candidate_top_k=(
                {"candidate_id": candidate, "rank": 1, "score": 0.9},
            ),
            constraint_evaluations=(
                {
                    "candidate_id": candidate,
                    "feasible": True,
                    "violated_hard_constraints": [],
                    "explanation_codes": ["all_constraints_satisfied"],
                },
            ),
            feasible_top_k=(
                {"candidate_id": candidate, "rank": 1, "score": 0.95},
            ),
            final_ranking=(
                {"candidate_id": candidate, "rank": 1, "score": 0.95},
            ),
            constraint_summary={
                "no_feasible_candidate": False,
                "unsupported_constraints": [],
            },
            latency_components={"total_elapsed_seconds": 0.01, "inference_latency_seconds": 0.005},
            fallback={"used": self.fallback_category is not None, "category": self.fallback_category},
            errors=None,
            backend_provenance={"backend_version": "fake-v1"},
        )


@pytest.fixture()
def split():
    return load_development_split()


def _records(result_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (result_dir / "raw" / "recommendations.jsonl").read_text().splitlines()
    ]


def test_resume_does_not_rerun_completed_rows(split, tmp_path: Path):
    p1 = FakeAdapter("P1")
    p2 = FakeAdapter("P2")
    result_dir = tmp_path / "run"

    first = run_offline_recommendations(
        split,
        result_dir=result_dir,
        adapters={"P1": p1, "P2": p2},
        frozen_configuration={"snapshot": "frozen-v1"},
    )
    expected = len(split.bundle.cases) * 2
    assert first.executed_records == expected
    assert len(p1.calls) + len(p2.calls) == expected

    resumed_p1 = FakeAdapter("P1")
    resumed_p2 = FakeAdapter("P2")
    second = run_offline_recommendations(
        split,
        result_dir=result_dir,
        adapters={"P1": resumed_p1, "P2": resumed_p2},
        frozen_configuration={"snapshot": "frozen-v1"},
        resume=True,
    )
    assert second.executed_records == 0
    assert second.skipped_records == expected
    assert not resumed_p1.calls and not resumed_p2.calls


def test_duplicate_raw_rows_are_rejected_on_resume(split, tmp_path: Path):
    result_dir = tmp_path / "run"
    run_offline_recommendations(
        split,
        result_dir=result_dir,
        adapters={"P1": FakeAdapter("P1")},
        system_ids=("P1",),
    )
    records_path = result_dir / "raw" / "recommendations.jsonl"
    first_line = records_path.read_text(encoding="utf-8").splitlines()[0]
    with records_path.open("a", encoding="utf-8") as handle:
        handle.write(first_line + "\n")

    with pytest.raises(DuplicateRecordError):
        run_offline_recommendations(
            split,
            result_dir=result_dir,
            adapters={"P1": FakeAdapter("P1")},
            system_ids=("P1",),
            resume=True,
        )


def test_resume_recovers_after_interrupt_and_unterminated_tail(split, tmp_path: Path):
    result_dir = tmp_path / "run"
    interrupted = FakeAdapter("P1", interrupt_on_call=2)
    with pytest.raises(KeyboardInterrupt):
        run_offline_recommendations(
            split,
            result_dir=result_dir,
            adapters={"P1": interrupted},
            system_ids=("P1",),
        )
    assert len(_records(result_dir)) == 1
    records_path = result_dir / "raw" / "recommendations.jsonl"
    with records_path.open("ab") as handle:
        handle.write(b'{"partial":')

    resumed = FakeAdapter("P1")
    result = run_offline_recommendations(
        split,
        result_dir=result_dir,
        adapters={"P1": resumed},
        system_ids=("P1",),
        resume=True,
    )
    assert result.completed_records == len(split.bundle.cases)
    assert result.skipped_records == 1
    assert len(resumed.calls) == len(split.bundle.cases) - 1
    assert len(_records(result_dir)) == len(split.bundle.cases)


def test_provenance_mismatch_refuses_to_mix_evidence(split, tmp_path: Path):
    result_dir = tmp_path / "run"
    run_offline_recommendations(
        split,
        result_dir=result_dir,
        adapters={"P1": FakeAdapter("P1")},
        system_ids=("P1",),
        frozen_configuration={"ranker": "frozen-v1"},
    )

    with pytest.raises(ProvenanceMismatchError):
        run_offline_recommendations(
            split,
            result_dir=result_dir,
            adapters={"P1": FakeAdapter("P1")},
            system_ids=("P1",),
            frozen_configuration={"ranker": "changed-v2"},
            resume=True,
        )


def test_deterministic_adapters_are_not_repeated(split):
    p1 = FakeAdapter("P1")
    p2 = FakeAdapter("P2")
    matrix = build_execution_matrix(
        split,
        system_ids=("P1", "P2"),
        adapters={"P1": p1, "P2": p2},
        repeats=4,
        seed=123,
    )
    assert len(matrix) == len(split.bundle.cases) * 2
    assert {entry.repeat_index for entry in matrix} == {0}


def test_stochastic_p3_is_repeated_only_when_explicitly_enabled(split, tmp_path: Path):
    p3 = FakeAdapter("P3", stochastic=True)
    result = run_offline_recommendations(
        split,
        result_dir=tmp_path / "p3-run",
        adapters={"P3": p3},
        system_ids=("P3",),
        repeats=3,
        enable_p3=True,
    )
    assert result.planned_records == len(split.bundle.cases) * 3
    assert result.effective_repeats == {"P3": 3}
    assert len(p3.calls) == len(split.bundle.cases) * 3


def test_fallback_label_and_complete_ranking_trace_are_preserved(split, tmp_path: Path):
    result_dir = tmp_path / "run"
    run_offline_recommendations(
        split,
        result_dir=result_dir,
        adapters={"P2": FakeAdapter("P2", fallback_category="retrieval_empty")},
        system_ids=("P2",),
    )
    record = _records(result_dir)[0]
    assert record["fallback"] == {"used": True, "category": "retrieval_empty"}
    assert record["sparse_ranks"] == [
        {"candidate_id": "small-minimal-python", "rank": 1, "score": 0.8}
    ]
    assert record["dense_ranks"] == [
        {"candidate_id": "small-minimal-python", "rank": 1, "score": 0.7}
    ]
    assert record["hybrid_ranks_scores"] == [
        {"candidate_id": "small-minimal-python", "rank": 1, "score": 0.9}
    ]
    assert record["constraint_evaluations"][0]["feasible"] is True
    assert record["final_ranking"] == [
        {"candidate_id": "small-minimal-python", "rank": 1, "score": 0.95}
    ]


def test_dry_run_creates_no_evidence_and_never_calls_adapter(split, tmp_path: Path):
    p1 = FakeAdapter("P1")
    result = run_offline_recommendations(
        split,
        result_dir=tmp_path / "dry-run",
        adapters={"P1": p1},
        system_ids=("P1",),
        dry_run=True,
    )
    assert result.dry_run is True
    assert result.to_dict()["status"] == "DRY_RUN"
    assert result.to_dict()["claims_permitted"] is False
    assert result.planned_records == len(split.bundle.cases)
    assert not p1.calls
    assert not (tmp_path / "dry-run").exists()


def test_b0_is_rejected_as_manual_only(split, tmp_path: Path):
    with pytest.raises(ValueError, match="manual human-selection"):
        run_offline_recommendations(
            split,
            result_dir=tmp_path / "b0",
            adapters={},
            system_ids=("B0",),
            dry_run=True,
        )


def test_p3_requires_explicit_enablement(split, tmp_path: Path):
    with pytest.raises(PermissionError, match="explicit"):
        run_offline_recommendations(
            split,
            result_dir=tmp_path / "p3",
            adapters={"P3": FakeAdapter("P3", stochastic=True)},
            system_ids=("P3",),
            dry_run=True,
        )


def test_production_p1_and_p2_preserve_system_specific_contracts(split, tmp_path: Path):
    result_dir = tmp_path / "production"
    run_offline_recommendations(
        split,
        result_dir=result_dir,
        adapters={"P1": P1FrozenAdapter(), "P2": P2FrozenAdapter()},
        frozen_configuration={"snapshot": "adapter-contract-v1"},
        repeats=4,
    )
    records = _records(result_dir)
    p1 = next(record for record in records if record["system_id"] == "P1")
    p2 = next(
        record
        for record in records
        if record["system_id"] == "P2"
        and not record["fallback"]["used"]
        and record["sparse_ranks"]
    )

    assert p1["structured_intent"] is None
    assert p1["sparse_ranks"] == p1["dense_ranks"] == []
    assert p1["candidate_top_k"] == p1["final_ranking"] == []
    assert p2["structured_intent"] is not None
    assert p2["sparse_ranks"]
    assert p2["dense_ranks"]
    assert p2["hybrid_ranks_scores"] == p2["candidate_top_k"]
    assert p2["constraint_evaluations"]
    assert p2["final_ranking"]
    assert "total_elapsed_seconds" in p2["latency_components"]
    assert p2["fallback"] == {"used": False, "category": None}
    assert p2["errors"] is None

    provenance = json.loads(
        (result_dir / "raw" / "offline-run-provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["requested_repeats"] == 4
    assert provenance["effective_repeats"] == {"P1": 1, "P2": 1}
    assert provenance["repeat_policy"]["deterministic_systems"] == ["P1", "P2"]
    assert provenance["candidate_catalog"]["catalog_sha256"]
    assert provenance["candidate_catalog"]["corpus_sha256"]


def test_secret_bearing_configuration_is_rejected_before_writes(split, tmp_path: Path):
    result_dir = tmp_path / "secret"
    with pytest.raises(ValueError, match="secret-bearing"):
        run_offline_recommendations(
            split,
            result_dir=result_dir,
            adapters={"P1": FakeAdapter("P1")},
            system_ids=("P1",),
            frozen_configuration={"provider_api_key": "must-not-persist"},
            dry_run=True,
        )
    assert not result_dir.exists()


def test_adapter_failure_is_a_complete_safe_evidence_row(split, tmp_path: Path):
    result_dir = tmp_path / "failure"
    result = run_offline_recommendations(
        split,
        result_dir=result_dir,
        adapters={"P1": FakeAdapter("P1", fail_on_call=1)},
        system_ids=("P1",),
    )
    failed = _records(result_dir)[0]

    assert result.error_records == 1
    assert failed["status"] == "error"
    assert failed["errors"] == {
        "category": "RuntimeError",
        "code": "adapter_execution_error",
    }
    assert failed["predicted_candidate_id"] is None
    assert failed["benchmark_prompt"] is None
    assert failed["latency_components"]["total_elapsed_seconds"] >= 0
    assert "benchmark text" not in json.dumps(failed)


def test_resume_rejects_durable_malformed_jsonl_line(split, tmp_path: Path):
    result_dir = tmp_path / "malformed"
    with pytest.raises(KeyboardInterrupt):
        run_offline_recommendations(
            split,
            result_dir=result_dir,
            adapters={"P1": FakeAdapter("P1", interrupt_on_call=2)},
            system_ids=("P1",),
        )
    with (result_dir / "raw" / "recommendations.jsonl").open("ab") as handle:
        handle.write(b'{"durable": malformed}\n')

    with pytest.raises(EvidenceRecordError, match="malformed JSON"):
        run_offline_recommendations(
            split,
            result_dir=result_dir,
            adapters={"P1": FakeAdapter("P1")},
            system_ids=("P1",),
            resume=True,
        )
