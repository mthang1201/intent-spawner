"""Focused tests for the `v5-final-execution-freeze-v2` identity and its E1 evidence.

Commit 842109c ("Core algorithm fix") corrected a P2 deterministic
constraint/ranking defect. These tests pin the resulting frozen identity so
that a later drift is reported rather than absorbed:

1. the predecessor freeze is still present, unmodified and truthful about the
   retired pre-fix algorithm;
2. the v2 freeze records the corrected P2 identity and leaves P1 untouched;
3. `evaluation_p3.runner.verify_frozen_inputs()` agrees with the v2 identity;
4. the fresh E1 development run is bound to v2, is complete, and is separate
   from the original pre-fix run, which stays byte-for-byte intact;
5. the recomputed development-split numbers are exactly what the raw evidence
   supports - and in particular that P2 still trails P1 on this split;
6. the paired P2/P3 harness is anchored to a P2 reference collected under the
   v2 identity, with the pre-fix reference preserved;
7. the E4 efficiency design-generation registry keeps pre-3f896eb packages
   validating without letting them count as current-design evidence.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from evaluation_p3.runner import (
    DEFAULT_REFERENCE_RUN,
    FROZEN_INPUT_SHA256,
    RETIRED_REFERENCE_RUNS,
    verify_frozen_inputs,
)
from evaluation_v4.dataset import file_sha256
from evaluation_v5.freeze import validate_freeze_manifest
from evaluation_v5.offline.validate_evidence import validate_offline_evidence
from evaluation_v5.resource.efficiency_contracts import (
    CONDITIONS,
    CURRENT_DESIGN_ID,
    FAMILY_COUNT,
    RESOURCE_EFFICIENCY_DESIGNS,
    resolve_design_generation,
)
from evaluation_v5.resource.efficiency_plan import validate_efficiency_plan


ROOT = Path(__file__).resolve().parents[1]
FREEZES = ROOT / "results_v5/protocol-v5.0.0/freezes"
RETIRED = FREEZES / "v5-final-execution-freeze"
CURRENT = FREEZES / "v5-final-execution-freeze-v2"
E1 = ROOT / "results_v5/protocol-v5.0.0/E1"
E1_PREFIX = E1 / "20260825T-observed-p1-p2-development-v1"
E1_CURRENT = E1 / "20260921T-observed-p1-p2-development-v2"

# Families of the development split that are not part of the P2 constraint
# supplement. "Ordinary" in the audit's sense: a feasible request with a
# non-empty acceptable set that P1 was designed to answer.
ORDINARY_FAMILIES = frozenset(
    {"basic-python", "pandas-transform", "sklearn-small", "threshold-below"}
)


def _manifest(directory: Path) -> dict:
    return json.loads((directory / "freeze-manifest.json").read_text(encoding="utf-8"))


def _records(run: Path) -> dict[str, dict[str, dict]]:
    by_system: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    text = (run / "raw/recommendations.jsonl").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip():
            row = json.loads(line)
            by_system[row["system_id"]][row["case_id"]] = row
    return by_system


def _accepted(row: dict) -> bool:
    acceptable = row["evaluation_gold"]["acceptable_candidate_ids"] or []
    return row["predicted_candidate_id"] in acceptable


def _score(run: Path, system: str) -> tuple[int, int, int, int]:
    """Return (accepted, total, accepted_ordinary, total_ordinary)."""
    rows = _records(run)[system]
    ordinary = [r for r in rows.values() if r["family_id"] in ORDINARY_FAMILIES]
    return (
        sum(_accepted(r) for r in rows.values()),
        len(rows),
        sum(_accepted(r) for r in ordinary),
        len(ordinary),
    )


def test_retired_freeze_is_preserved_and_still_describes_the_prefix_ranker():
    """The predecessor freeze is immutable evidence and is not rewritten."""
    retired = _manifest(RETIRED)
    validate_freeze_manifest(retired)
    assert retired["freeze_id"] == "v5-final-execution-freeze"
    assert retired["status"] == "FROZEN"
    configuration = retired["configuration_snapshot"]["configuration"]
    # Truthfully still the pre-fix identity.
    assert configuration["ranking"]["ranker_version"] == "p2-deterministic-ranker-v1.0.0"
    assert configuration["ranking"]["tie_breaker"] == "candidate_id"
    assert configuration["P2"]["config_version"] == "p2-config-v1.0.0"


def test_v2_freeze_records_the_corrected_p2_and_leaves_p1_frozen():
    current = _manifest(CURRENT)
    validate_freeze_manifest(current)
    assert current["freeze_id"] == "v5-final-execution-freeze-v2"
    assert current["status"] == "FROZEN"

    retired = _manifest(RETIRED)
    old = retired["configuration_snapshot"]
    new = current["configuration_snapshot"]

    # P2 moved.
    assert new["configuration"]["ranking"]["ranker_version"] == "p2-deterministic-ranker-v2.0.0"
    assert new["configuration"]["constraints"]["evaluator_version"] == (
        "p2-deterministic-constraint-evaluator-v1.1.0"
    )
    assert new["configuration"]["P2"]["config_version"] == "p2-config-v1.1.0"
    assert new["configuration"]["ranking"]["tie_breaker"] == (
        "resource_cost,retrieval_rank,candidate_id"
    )
    assert new["systems"]["P2"]["implementation"]["file_sha256"] != (
        old["systems"]["P2"]["implementation"]["file_sha256"]
    )
    assert new["runtime_package"]["sha256"] != old["runtime_package"]["sha256"]

    # P1 did not move: it remains a frozen comparator.
    assert new["systems"]["P1"] == old["systems"]["P1"]

    # The development split and catalog did not move either.
    assert new["development_dataset"] == old["development_dataset"]
    assert new["indexes"] == old["indexes"]

    # P3 stays excluded; this freeze does not reopen its gate.
    assert new["p3_gate"]["status"] == "not_retained"
    assert new["p3_gate"]["verification_status"] == "VERIFIED_EXCLUSION"

    # The freeze read no sealed data.
    rules = current["integrity_rules"]
    assert rules["sealed_data_not_read_by_freeze"] is True
    assert rules["created_before_sealed_data_supply"] is True


def test_supersession_note_names_the_retired_evidence():
    note = (CURRENT / "SUPERSESSION.md").read_text(encoding="utf-8")
    assert "842109c" in note
    assert "v5-final-execution-freeze" in note
    assert "20260825T-observed-p1-p2-development-v1" in note


def test_p3_frozen_input_table_matches_the_v2_identity():
    """verify_frozen_inputs() fails closed and now agrees with the live tree."""
    observed = verify_frozen_inputs()
    assert observed == dict(FROZEN_INPUT_SHA256)

    frozen_p2 = _manifest(CURRENT)["configuration_snapshot"]["systems"]
    assert FROZEN_INPUT_SHA256["recommender/p2_backend.py"] == (
        frozen_p2["P2"]["implementation"]["file_sha256"]
    )
    # P1's entry is the same bytes the retired freeze recorded.
    assert FROZEN_INPUT_SHA256["recommender/rule_based.py"] == (
        _manifest(RETIRED)["configuration_snapshot"]["systems"]["P1"]["implementation"][
            "file_sha256"
        ]
    )


def test_original_e1_run_is_untouched():
    """AGENTS.md rule 11: the pre-fix run directory is immutable."""
    assert validate_offline_evidence(E1_PREFIX)["status"] == "PASS"
    provenance = json.loads(
        (E1_PREFIX / "raw/offline-run-provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["run_id"] == "20260825T-observed-p1-p2-development-v1"
    # Still the pre-fix configuration, not silently re-pointed at v2.
    assert provenance["frozen_configuration"]["configuration"]["constraints"][
        "ranker_version"
    ] == "p2-deterministic-ranker-v1.0.0"
    assert "freeze_id" not in provenance["frozen_configuration"]


def test_fresh_e1_run_is_complete_and_bound_to_the_v2_freeze():
    assert E1_CURRENT != E1_PREFIX
    assert validate_offline_evidence(E1_CURRENT)["status"] == "PASS"

    provenance = json.loads(
        (E1_CURRENT / "raw/offline-run-provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["run_id"] == "20260921T-observed-p1-p2-development-v2"
    assert provenance["experiment_id"] == "E1"
    assert provenance["systems"] == ["P1", "P2"]
    assert provenance["seed"] == 20260824

    frozen = provenance["frozen_configuration"]
    assert frozen["freeze_id"] == "v5-final-execution-freeze-v2"
    assert frozen["configuration"]["ranking"]["ranker_version"] == (
        "p2-deterministic-ranker-v2.0.0"
    )
    # Same development cases as the pre-fix run.
    assert frozen["development_dataset"]["split_id"] == "v5-development"
    assert frozen["development_dataset"]["canonical_sha256"] == (
        "18894b73ec98d895348498bf6b1c4dd4d2dc6004437202bd8b93c17d09b0dc0b"
    )

    completion = json.loads(
        (E1_CURRENT / "report/offline-run-completion.json").read_text(encoding="utf-8")
    )
    assert completion["claims_permitted"] is False

    records = _records(E1_CURRENT)
    assert sorted(records) == ["P1", "P2"]
    assert len(records["P1"]) == 18
    assert len(records["P2"]) == 18
    assert records["P1"].keys() == _records(E1_PREFIX)["P1"].keys()


def test_p1_output_is_identical_across_the_two_e1_runs():
    """P1 is unchanged by the fix, so the harness must reproduce it exactly."""
    before, after = _records(E1_PREFIX)["P1"], _records(E1_CURRENT)["P1"]
    assert {c: r["predicted_candidate_id"] for c, r in before.items()} == {
        c: r["predicted_candidate_id"] for c, r in after.items()
    }


@pytest.mark.parametrize(
    "run, system, expected",
    [
        (E1_PREFIX, "P1", (13, 18, 12, 12)),
        (E1_PREFIX, "P2", (8, 18, 6, 12)),
        (E1_CURRENT, "P1", (13, 18, 12, 12)),
        (E1_CURRENT, "P2", (9, 18, 7, 12)),
    ],
)
def test_recomputed_development_split_acceptable_at_1(run, system, expected):
    """Development-split only; not confirmatory and not a performance claim."""
    assert _score(run, system) == expected


def test_the_fix_improved_p2_without_regressing_any_case_or_overtaking_p1():
    before, after = _records(E1_PREFIX)["P2"], _records(E1_CURRENT)["P2"]
    regressions = [c for c in before if _accepted(before[c]) and not _accepted(after[c])]
    improvements = [c for c in before if not _accepted(before[c]) and _accepted(after[c])]
    assert regressions == []
    assert improvements == ["threshold-below-canonical-en"]
    # The gap to P1 narrowed but did not close on this split.
    assert _score(E1_CURRENT, "P2")[0] < _score(E1_CURRENT, "P1")[0]


def test_new_evidence_is_registered_in_the_reviewed_inventory():
    """Nothing new is left unregistered, so Check 6 does not see stray files."""
    from evaluation_v5.final_audit.common import LOCK

    inventory = json.loads((ROOT / LOCK).read_text(encoding="utf-8"))
    assert inventory["schema_version"] == "protocol-v5-final-audit-inputs-v1.5.0"
    assert inventory["superseding_freeze"]["freeze_id"] == "v5-final-execution-freeze-v2"
    # The retired freeze still governs the registered packages.
    assert inventory["authoritative_freeze"].endswith(
        "v5-final-execution-freeze/freeze-manifest.json"
    )

    expected = [
        CURRENT / "freeze-manifest.json",
        CURRENT / "SUPERSESSION.md",
        FREEZES / "frozen-configuration-v2.json",
        E1_CURRENT / "raw/offline-run-provenance.json",
        E1_CURRENT / "raw/recommendations.jsonl",
        E1_CURRENT / "report/offline-run-completion.json",
    ]
    for path in expected:
        relative = str(path.relative_to(ROOT))
        assert inventory["files"][relative] == file_sha256(path)

    # The fresh development-split run is not promoted into the package set.
    assert not any("20260921T" in package for package in inventory["packages"])


# ---------------------------------------------------------------------------
# Paired P2/P3 reference identity
# ---------------------------------------------------------------------------


def test_paired_p3_reference_is_collected_under_the_v2_p2_identity():
    """The P2/P3 pairing invariant is re-anchored, not relaxed."""
    manifest = json.loads((DEFAULT_REFERENCE_RUN / "manifest.json").read_text(encoding="utf-8"))
    frozen = _manifest(CURRENT)["configuration_snapshot"]
    assert manifest["runtime_package_checksum"] == frozen["runtime_package"]["sha256"]
    assert manifest["sample_count"] == 66

    retired = json.loads(
        (RETIRED_REFERENCE_RUNS[0] / "manifest.json").read_text(encoding="utf-8")
    )
    # Same frozen dataset, so the two references are directly comparable.
    assert retired["dataset_sha256"] == manifest["dataset_sha256"]
    assert retired["sample_count"] == manifest["sample_count"]
    # ...but a different P2 runtime, which is exactly why a new one was needed.
    assert retired["runtime_package_checksum"] != manifest["runtime_package_checksum"]


def test_retired_p2_reference_runs_are_preserved():
    for run in RETIRED_REFERENCE_RUNS:
        assert (run / "manifest.json").is_file()
        assert (run / "raw/predictions.jsonl").is_file()
        assert run != DEFAULT_REFERENCE_RUN


def test_p1_is_bit_identical_between_the_two_p2_reference_runs():
    """P1 is frozen, so the Protocol-v4 lane must reproduce it exactly."""
    def predictions(run: Path) -> dict[str, dict]:
        rows = (run / "raw/predictions.jsonl").read_text(encoding="utf-8").splitlines()
        parsed = [json.loads(line) for line in rows if line.strip()]
        return {r["sample_id"]: r for r in parsed if r["system"] == "p1"}

    fields = (
        "final_candidate_id", "ranked_candidate_ids", "retrieved_candidate_ids",
        "feasible_candidate_ids", "detected_infeasible", "constraint_violated",
        "policy_compliant", "fallback_category",
    )
    before = predictions(RETIRED_REFERENCE_RUNS[0])
    after = predictions(DEFAULT_REFERENCE_RUN)
    assert before.keys() == after.keys()
    assert [s for s in before if any(before[s].get(f) != after[s].get(f) for f in fields)] == []


def test_ranking_fix_traded_top1_accuracy_for_constraint_compliance():
    """Honest record of a mixed outcome on the Protocol-v4 formative dataset.

    The fix improves the Protocol-v5 development split by one case, but on this
    older 66-sample dataset top-1 ranking quality drops while constraint
    violations fall. Pinned so the trade-off cannot be quietly reversed or
    reported as a uniform improvement.
    """
    def p2_metrics(run: Path) -> dict:
        return json.loads(
            (run / "aggregates/metrics.json").read_text(encoding="utf-8")
        )["systems"]["p2"]

    before = p2_metrics(RETIRED_REFERENCE_RUNS[0])
    after = p2_metrics(DEFAULT_REFERENCE_RUN)

    # Worse at rank 1.
    assert before["top1_accuracy"]["numerator"] == 32
    assert after["top1_accuracy"]["numerator"] == 30
    assert before["acceptable_candidate_hit_at_k"]["1"]["numerator"] == 42
    assert after["acceptable_candidate_hit_at_k"]["1"]["numerator"] == 36
    assert after["mrr"]["value"] < before["mrr"]["value"]
    assert after["ndcg_at_5"]["value"] < before["ndcg_at_5"]["value"]

    # Better deeper in the ranking, and fewer constraint violations.
    assert before["acceptable_candidate_hit_at_k"]["5"]["numerator"] == 57
    assert after["acceptable_candidate_hit_at_k"]["5"]["numerator"] == 59
    assert before["constraint_violation_rate"]["numerator"] == 7
    assert after["constraint_violation_rate"]["numerator"] == 5


# ---------------------------------------------------------------------------
# E4 efficiency design generations
# ---------------------------------------------------------------------------


def test_design_generation_registry_is_closed_and_self_consistent():
    assert CURRENT_DESIGN_ID == RESOURCE_EFFICIENCY_DESIGNS[0]["design_id"]
    assert [d["current"] for d in RESOURCE_EFFICIENCY_DESIGNS] == [True, False]
    assert RESOURCE_EFFICIENCY_DESIGNS[0]["family_count"] == FAMILY_COUNT

    for design in RESOURCE_EFFICIENCY_DESIGNS:
        trials = design["family_count"] * len(CONDITIONS) * design["repetitions"]
        resolved = resolve_design_generation(
            design["family_count"], design["repetitions"], trials
        )
        assert resolved is not None
        assert resolved["design_id"] == design["design_id"]

    # An unregistered size is still rejected, so the gate stays fail-closed.
    assert resolve_design_generation(18, 10, 18 * len(CONDITIONS) * 10) is None
    # A registered size with an inconsistent trial count is rejected too.
    assert resolve_design_generation(16, 10, 800) is None


def test_superseded_e4_plan_validates_but_is_not_current_evidence():
    """AGENTS.md rule 13: pre-3f896eb E4 packages must keep validating."""
    plan_path = (
        ROOT
        / "results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-plan-20260905T082000Z/plan.json"
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["family_count"] == 16
    assert len(plan["trials"]) == 640

    design = validate_efficiency_plan(plan)
    assert design["design_id"] == "e4-efficiency-design-v1-16-families"
    assert design["current"] is False

    # New work may not be planned or executed against a superseded design.
    with pytest.raises(ValueError, match="requires the current design"):
        validate_efficiency_plan(plan, require_current_design=True)


def test_audit_marks_superseded_e4_packages_legacy_valid():
    from evaluation_v5.final_audit.checks import inspect
    from evaluation_v5.final_audit.common import Inputs

    audit = inspect(Inputs(), isolation=False, historical=False)
    efficiency = [
        p for p in audit["packages"]
        if p["kind"] in ("resource_efficiency", "resource_plan")
    ]
    assert efficiency
    assert all(p["validation"] == "PASS" for p in efficiency)
    statuses = {
        p["validator_result"].get("validator_status")
        for p in efficiency
        if p["validator_result"].get("design_generation")
    }
    assert statuses == {"LEGACY_VALID"}
    assert all(
        p["validator_result"]["eligible_as_current_e4_evidence"] is False
        for p in efficiency
        if p["validator_result"].get("design_generation")
    )
