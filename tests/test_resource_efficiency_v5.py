from __future__ import annotations

from collections import Counter
from itertools import groupby
import json
from pathlib import Path

import pytest

from cluster_evaluation.resource_adapter_v5 import _cpu_m, _memory_mib, evaluate_cluster_eligibility
from cluster_evaluation.resource_efficiency_adapter_v5 import KubernetesResourceEfficiencyAdapter, build_pod_spec, classify_kubernetes_outcome
from evaluation_v5.offline.recommenders import OfflineAdapterResult
from evaluation_v5.resource.contracts import load_cluster_policy, load_image_state
from evaluation_v5.resource.efficiency_analysis import analyze_trials, canonical_allocation_identity, classify_pareto, derive_trial, statistical_results, summarize_dynamic_allocations, summarize_families
from evaluation_v5.resource.efficiency_capacity import CAPACITY_SOURCE, EVIDENCE_TYPE, LABEL, SCHEDULER_INPUT, simulate_capacity, verify_observed_requests
from evaluation_v5.resource.efficiency_contracts import CONDITIONS, FAMILY_COUNT, PARETO_OBJECTIVES, PRIMARY_TRIAL_COUNT, REPETITIONS, confirmatory_readiness, load_capacity_contract, load_condition_inputs, load_efficiency_freeze, validate_efficiency_contracts
from evaluation_v5.resource.efficiency_evidence import validate_analysis_package, validate_raw_package, validate_trial_record
from evaluation_v5.resource.efficiency_models import EfficiencyTrialSpec, primary_outcome
from evaluation_v5.resource.efficiency_plan import build_efficiency_plan
from evaluation_v5.resource.efficiency_runner import execute_plan, write_not_executed
from recommender.dynamic_resources import QuotaCaps, ResourceSelector, load_resource_policy


class CountingAdapter:
    stochastic = False

    def __init__(self, system_id: str, profile: str, score: float = 1.0):
        self.system_id, self.profile, self.score, self.calls = system_id, profile, score, []

    def frozen_provenance(self):
        return {"adapter_version": f"test-{self.system_id.lower()}-v1"}

    def recommend(self, case, *, seed):
        self.calls.append((case.family_id, seed))
        return OfflineAdapterResult(
            predicted_candidate_id=f"{self.profile}-image", predicted_profile_id=self.profile,
            predicted_image_id="image", recommendation_score=self.score,
        )


class SuccessfulExecutionAdapter:
    adapter_version = "synthetic-efficiency-adapter-v1"

    def __init__(self, *, fail_after: int | None = None, infrastructure_first: str | None = None):
        self.calls = 0
        self.fail_after = fail_after
        self.infrastructure_first = infrastructure_first

    def environment_provenance(self):
        return {"environment_id": "synthetic-validation", "measurements_are_real": False}

    def run_trial(self, spec):
        if self.fail_after is not None and self.calls >= self.fail_after:
            raise RuntimeError("synthetic interruption")
        self.calls += 1
        if self.infrastructure_first == spec.primary_trial_id and spec.replacement_of is None:
            return KubernetesResourceEfficiencyAdapter._record(
                spec, planned=spec.allocation.to_dict(), infrastructure_invalid=True,
                exclusion_reason="SYNTHETIC_INFRASTRUCTURE_FAILURE",
            )
        return KubernetesResourceEfficiencyAdapter._record(
            spec, planned=spec.allocation.to_dict(), observed=spec.allocation.to_dict(),
            pod_created=True, scheduled=True, correctness=True, success=True,
            observed_marker=spec.expected_marker_sha256, correctness_invariants_ok=True,
            correctness_details={"synthetic": "pass"},
            workload_runtime=1.0, container_runtime=1.1,
            cgroup_metrics={"mean_cpu_m": 50.0, "peak_memory_mib": 100.0},
            kubernetes={"cleanup_status": "succeeded"},
        )


def _plan_with_counting_adapters():
    p1 = CountingAdapter("P1", "small")
    p2 = CountingAdapter("P2", "medium")
    return build_efficiency_plan(adapters={"P1": p1, "P2": p2}), p1, p2


def _row(spec: EfficiencyTrialSpec, **changes):
    values = dict(
        planned=spec.allocation.to_dict(), observed=spec.allocation.to_dict(), pod_created=True,
        scheduled=True, correctness=True, success=True, workload_runtime=1.0,
        observed_marker=spec.expected_marker_sha256, correctness_invariants_ok=True,
        correctness_details={"synthetic": "pass"},
        container_runtime=1.0, cgroup_metrics={"mean_cpu_m": 50.0, "peak_memory_mib": 100.0},
    )
    values.update(changes)
    return KubernetesResourceEfficiencyAdapter._record(spec, **values)


def _pareto_row(**changes):
    values = {
        "cpu_cost_per_success": 4.0, "memory_cost_per_success": 4.0,
        "success_rate": 1.0, "correct_completion_rate": 1.0,
        "oom_rate": 0.0, "timeout_rate": 0.0,
        "pending_or_admission_rate": 0.0, "runtime_error_rate": 0.0,
        "incorrect_rate": 0.0,
    }
    values.update(changes)
    return values


def _frozen_capacity(cpu_m=4000, memory_mib=4096):
    return {
        "freeze_status": "FROZEN", "evidence_type": EVIDENCE_TYPE,
        "capacity_source": CAPACITY_SOURCE, "physical_capacity_permitted": False,
        "allocatable": {"cpu_m": cpu_m, "memory_mib": memory_mib, "gpu_count": 0, "gpu_resource": None},
    }


def test_contracts_bind_exact_16_by_4_by_10_design():
    status = validate_efficiency_contracts()
    assert FAMILY_COUNT == 16 and len(CONDITIONS) == 4 and REPETITIONS == 10
    assert PRIMARY_TRIAL_COUNT == FAMILY_COUNT * len(CONDITIONS) * REPETITIONS == 640
    assert status["primary_trial_count"] == PRIMARY_TRIAL_COUNT
    assert CONDITIONS == ("STATIC_LARGE", "P1_CATALOG", "P2_CATALOG", "P2_DYNAMIC")
    assert "P3" not in " ".join(CONDITIONS)
    assert status["confirmatory_freeze_status"] == "NOT_FROZEN"
    assert status["capacity_freeze_status"] == "NOT_FROZEN"
    assert len(load_condition_inputs()["inputs"]) == 16


def test_plan_is_paired_randomized_and_calls_each_recommender_once_per_family():
    plan, p1, p2 = _plan_with_counting_adapters()
    assert len(plan["trials"]) == PRIMARY_TRIAL_COUNT
    assert plan["independent_semantic_n"] == FAMILY_COUNT
    assert len(p1.calls) == len(p2.calls) == 16
    assert Counter(row["condition"] for row in plan["trials"]) == {condition: 160 for condition in CONDITIONS}
    for repetition in range(1, 11):
        block = [row for row in plan["trials"] if row["repetition"] == repetition]
        assert len(block) == 64
        assert set(Counter((row["family_id"], row["condition"]) for row in block).values()) == {1}
    assert [row["trial_id"] for row in plan["trials"][:64]] != [row["trial_id"] for row in plan["trials"][64:128]]


def test_execution_order_is_reproducible_balanced_and_interleaved():
    first, _, _ = _plan_with_counting_adapters()
    second, _, _ = _plan_with_counting_adapters()
    assert [row["trial_id"] for row in first["trials"]] == [row["trial_id"] for row in second["trials"]]
    for repetition in range(1, REPETITIONS + 1):
        block = [row for row in first["trials"] if row["repetition"] == repetition]
        chunks = [block[index:index + len(CONDITIONS)] for index in range(0, len(block), len(CONDITIONS))]
        assert all(len({row["family_id"] for row in chunk}) == 1 for chunk in chunks)
        assert all({row["condition"] for row in chunk} == set(CONDITIONS) for chunk in chunks)
        for position in range(len(CONDITIONS)):
            assert Counter(chunk[position]["condition"] for chunk in chunks) == {
                condition: FAMILY_COUNT // len(CONDITIONS) for condition in CONDITIONS
            }
        condition_sequence = [row["condition"] for row in block]
        assert max(
            sum(1 for _ in group)
            for _, group in groupby(condition_sequence)
        ) <= 2


def test_p2_catalog_and_dynamic_reuse_one_frozen_p2_result():
    plan, _, _ = _plan_with_counting_adapters()
    assert all(row["p2"]["predicted_profile_id"] == "medium" for row in plan["decisions"])
    assert all(row["allocations"]["P2_CATALOG"]["cpu_request_m"] == 500 for row in plan["decisions"])
    assert all(row["dynamic_trace"]["input_score"] == 1.0 for row in plan["decisions"])
    assert all(row["dynamic_trace"]["policy_clipping_applied"] is False for row in plan["decisions"])


def test_p2_dynamic_unique_allocation_identity_is_global_final_and_canonical():
    plan, _, _ = _plan_with_counting_adapters()
    summary = summarize_dynamic_allocations(plan["decisions"])
    assert summary["generated_family_count"] == 16
    assert summary["unique_generated_allocation_count"] == 1
    definition = summary["unique_allocation_definition"]
    assert definition["scope"] == "global_across_generated_P2_DYNAMIC_families"
    assert definition["stage"] == "after_quantization_and_policy_validation_final_allocation"
    assert definition["canonical_units"] == {"cpu": "millicores", "memory": "MiB", "gpu": "integer_extended_resource_count"}
    assert definition["catalog_fallbacks_included"] is False
    spelled = {
        "cpu_request_m": _cpu_m("1"), "cpu_limit_m": _cpu_m("2"),
        "memory_request_mib": _memory_mib("1Gi"), "memory_limit_mib": _memory_mib("2Gi"),
        "gpu_count": 0, "gpu_resource": None,
    }
    canonical = {
        "cpu_request_m": 1000, "cpu_limit_m": 2000,
        "memory_request_mib": 1024, "memory_limit_mib": 2048,
        "gpu_count": 0, "gpu_resource": None,
    }
    assert canonical_allocation_identity(spelled) == canonical_allocation_identity(canonical)


def test_optional_score_does_not_change_existing_offline_serialization():
    result = OfflineAdapterResult("c", "small", "i", recommendation_score=3.0)
    assert "recommendation_score" not in result.to_dict()
    assert result.to_dict(include_recommendation_score=True)["recommendation_score"] == 3.0


def test_dynamic_trace_records_upward_quantization_and_quota_fallback():
    selector = ResourceSelector(load_resource_policy(), mode="dynamic", environ={})
    decision, trace = selector.select_with_trace(recommended_profile="small", score=1, dataset_size_gb=0)
    assert decision.applied_mode == "dynamic"
    assert trace.quantization_deltas["memory_request_mib"] == 32
    fallback, rejected = selector.select_with_trace(
        recommended_profile="large", score=1, dataset_size_gb=0,
        quota_headroom=QuotaCaps(cpu_limit_millicores=1000, memory_limit_mib=1000, gpu_count=0),
    )
    assert fallback.applied_mode == "catalog"
    assert rejected.fallback_to_catalog is True
    assert rejected.policy_clipping_applied is False
    assert "quota" in rejected.fallback_reason.lower()


def test_p2_dynamic_fallback_lineage_ends_at_catalog_allocation():
    p1 = CountingAdapter("P1", "small")
    p2 = CountingAdapter("P2", "medium", score="not-a-number")
    plan = build_efficiency_plan(adapters={"P1": p1, "P2": p2})
    decision = plan["decisions"][0]
    spec = EfficiencyTrialSpec.from_dict(next(
        row for row in plan["trials"]
        if row["family_id"] == decision["family_id"] and row["condition"] == "P2_DYNAMIC"
    ))
    lineage = derive_trial(_row(spec), decision=decision)["telemetry"]["dynamic_allocation"]
    assert lineage["fallback_to_catalog"] is True
    assert lineage["fallback_reason"]
    assert lineage["final_allocation"] == decision["allocations"]["P2_CATALOG"]


def test_hardened_pod_has_exact_separate_requests_and_limits():
    plan, _, _ = _plan_with_counting_adapters()
    spec = EfficiencyTrialSpec.from_dict(next(row for row in plan["trials"] if row["condition"] == "P2_CATALOG"))
    image = "example.invalid/resource@sha256:" + "a" * 64
    pod = build_pod_spec(spec, image)
    container = pod["spec"]["containers"][0]
    assert container["resources"] == {"requests": {"cpu": "500m", "memory": "768Mi"}, "limits": {"cpu": "1000m", "memory": "1024Mi"}}
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"pending_or_admission_failure": True}, "PENDING_OR_ADMISSION_FAILURE"),
        ({"oom": True, "timeout": True}, "OOM"),
        ({"timeout": True}, "TIMEOUT"),
        ({"correctness": False}, "INCORRECT"),
        ({"runtime_error": True}, "RUNTIME_ERROR"),
        ({"success": True, "correctness": True}, "SUCCESS"),
    ],
)
def test_primary_outcome_classes_and_oom_precedence(fields, expected):
    base = {"infrastructure_invalid": False, "pending_or_admission_failure": False, "oom": False, "timeout": False, "correctness": None, "runtime_error": False, "success": False}
    assert primary_outcome({**base, **fields}) == expected


def test_derive_missing_metrics_and_abrupt_failure_runtime_rules():
    plan, _, _ = _plan_with_counting_adapters()
    spec = EfficiencyTrialSpec.from_dict(plan["trials"][0])
    oom = _row(spec, correctness=None, success=False, oom=True, workload_runtime=None, container_runtime=2.0, cgroup_metrics={})
    derived = derive_trial(oom)
    assert derived["accounting_runtime_reason"] == "KUBERNETES_CONTAINER_DURATION_FALLBACK"
    assert derived["observed_runtime_seconds"] == 2.0
    assert derived["cpu_request_time_cpu_seconds"] == spec.allocation.cpu_request_m * 2 / 1000
    assert derived["mean_cpu_m"] is None and derived["peak_memory_mib"] is None
    assert derived["cpu_usage_unavailable_reason"] == "CGROUP_CPU_USAGE_MISSING"
    assert derived["peak_memory_unavailable_reason"] == "CGROUP_MEMORY_PEAK_MISSING"
    pending = _row(spec, observed=None, pod_created=False, scheduled=False, correctness=None, success=False, pending=True, workload_runtime=None, container_runtime=None)
    pending_derived = derive_trial(pending)
    assert pending_derived["cpu_request_time_cpu_seconds"] == 0
    assert pending_derived["observed_runtime_seconds"] is None


def test_per_trial_telemetry_contract_has_canonical_units_and_dynamic_lineage():
    plan, _, _ = _plan_with_counting_adapters()
    payload = next(row for row in plan["trials"] if row["condition"] == "P2_DYNAMIC")
    spec = EfficiencyTrialSpec.from_dict(payload)
    decision = next(row for row in plan["decisions"] if row["family_id"] == spec.family_id)
    oracle = {
        spec.family_id: {
            "cpu_selected_m": 500, "memory_selected_mib": 768,
            "cpu_minimum_interval": {"ordinary_interval_supported": True, "largest_tested_rejected": 300, "smallest_tested_accepted": 500},
            "memory_minimum_interval": {"ordinary_interval_supported": True, "largest_tested_rejected": 512, "smallest_tested_accepted": 768},
        }
    }
    derived = derive_trial(_row(spec), oracle, decision)
    telemetry = derived["telemetry"]
    assert telemetry["canonical_units"]["cpu_allocation"] == "millicores"
    assert telemetry["canonical_units"]["memory_allocation"] == "MiB"
    assert telemetry["request_and_limit"]["gpu_request_count"] == telemetry["request_and_limit"]["gpu_limit_count"] == 0
    assert telemetry["usage"]["cpu_request_ratio"] == 50 / spec.allocation.cpu_request_m
    assert telemetry["oracle_comparison"]["cpu_request"]["definition"] == "allocation_minus_oracle_selected"
    dynamic = telemetry["dynamic_allocation"]
    assert dynamic["status"] == "AVAILABLE" and dynamic["applicable"] is True
    assert dynamic["raw_generated_targets"] == decision["dynamic_trace"]["formula_targets"]
    assert dynamic["final_allocation"] == spec.allocation.to_dict()
    catalog = EfficiencyTrialSpec.from_dict(next(row for row in plan["trials"] if row["condition"] == "P2_CATALOG" and row["family_id"] == spec.family_id))
    assert derive_trial(_row(catalog), oracle, decision)["telemetry"]["dynamic_allocation"]["status"] == "NOT_APPLICABLE"


def test_kubernetes_classifier_separates_oom_timeout_pending_and_infrastructure():
    oom_timeout = classify_kubernetes_outcome(
        phase="Failed", pod_reason=None, terminated_reason=None, waiting_reason=None,
        condition_messages="", cgroup_metrics={"memory_events_delta": {"oom": 1}},
        monitor_deadline_reached=True, workload_runtime_seconds=None, timeout_seconds=10,
    )
    assert oom_timeout["oom"] is True and oom_timeout["timeout"] is True
    assert primary_outcome({
        "infrastructure_invalid": False, "pending_or_admission_failure": False,
        "oom": True, "timeout": True, "correctness": None,
        "runtime_error": False, "success": False,
    }) == "OOM"
    image_pull = classify_kubernetes_outcome(
        phase="Pending", pod_reason=None, terminated_reason=None, waiting_reason="ImagePullBackOff",
        condition_messages="", cgroup_metrics={}, monitor_deadline_reached=True,
        workload_runtime_seconds=None, timeout_seconds=10,
    )
    assert image_pull["infrastructure_reason"] == "KUBERNETES_IMAGEPULLBACKOFF"
    assert image_pull["pending_or_admission_failure"] is False and image_pull["timeout"] is False
    pending = classify_kubernetes_outcome(
        phase="Pending", pod_reason=None, terminated_reason=None, waiting_reason=None,
        condition_messages="0/1 nodes are available: Insufficient memory", cgroup_metrics={},
        monitor_deadline_reached=True, workload_runtime_seconds=None, timeout_seconds=10,
    )
    assert pending["pending_or_admission_failure"] is True and pending["timeout"] is False


def test_ambiguous_pending_and_completed_outcome_record_is_rejected():
    plan, _, _ = _plan_with_counting_adapters()
    spec = EfficiencyTrialSpec.from_dict(plan["trials"][0])
    row = _row(
        spec, correctness=None, success=False, pending=True, oom=True,
        workload_runtime=None, observed_marker=None,
        correctness_invariants_ok=None, correctness_details={},
    )
    with pytest.raises(ValueError, match="Pending/admission"):
        validate_trial_record(row)


def test_cost_per_success_includes_failed_attempts_and_zero_success_is_null():
    plan, _, _ = _plan_with_counting_adapters()
    spec = EfficiencyTrialSpec.from_dict(plan["trials"][0])
    success = derive_trial(_row(spec))
    failure = derive_trial(_row(spec, correctness=None, success=False, oom=True, workload_runtime=None, container_runtime=1.0))
    summary = summarize_families([success, failure])[0]
    assert summary["successful_tasks"] == 1
    assert summary["cpu_cost_per_success"] == spec.allocation.cpu_request_m * 2 / 1000
    zero = summarize_families([failure])[0]
    assert zero["cpu_cost_per_success"] is None and zero["memory_cost_per_success"] is None
    assert zero["cost_unavailable_reason"] == "ZERO_SUCCESS"


def test_scheduled_failure_with_missing_duration_makes_cost_unavailable_not_zero():
    plan, _, _ = _plan_with_counting_adapters()
    spec = EfficiencyTrialSpec.from_dict(plan["trials"][0])
    success = derive_trial(_row(spec))
    missing = derive_trial(_row(
        spec, correctness=None, success=False, oom=True,
        workload_runtime=None, container_runtime=None, observed_marker=None,
        correctness_invariants_ok=None, correctness_details={},
    ))
    assert missing["accounting_runtime_seconds"] is None
    assert missing["cpu_request_time_cpu_seconds"] is None
    summary = summarize_families([success, missing])[0]
    assert summary["cpu_cost_per_success"] is None
    assert summary["memory_cost_per_success"] is None
    assert summary["cost_unavailable_reason"] == "INCOMPLETE_OR_MISSING_DURATION_EVIDENCE"


def test_tiny_allocation_with_many_ooms_cannot_appear_superior():
    plan, _, _ = _plan_with_counting_adapters()
    family = plan["decisions"][0]["family_id"]
    reference_specs = [
        EfficiencyTrialSpec.from_dict(row) for row in plan["trials"]
        if row["family_id"] == family and row["condition"] == "STATIC_LARGE"
    ]
    candidate_specs = [
        EfficiencyTrialSpec.from_dict(row) for row in plan["trials"]
        if row["family_id"] == family and row["condition"] == "P2_DYNAMIC"
    ]
    reference_rows = [derive_trial(_row(spec, workload_runtime=4.0, container_runtime=4.0)) for spec in reference_specs]
    candidate_rows = []
    for index, spec in enumerate(candidate_specs):
        if index < 4:
            candidate_rows.append(derive_trial(_row(spec, workload_runtime=1.0, container_runtime=1.0)))
        else:
            candidate_rows.append(derive_trial(_row(
                spec, correctness=None, success=False, oom=True,
                workload_runtime=None, container_runtime=0.1, observed_marker=None,
                correctness_invariants_ok=None, correctness_details={},
            )))
    summaries = summarize_families([*reference_rows, *candidate_rows], expected_repetitions=REPETITIONS)
    by_condition = {row["condition"]: row for row in summaries}
    reference = by_condition["STATIC_LARGE"]
    candidate = by_condition["P2_DYNAMIC"]
    assert candidate["memory_cost_per_success"] < reference["memory_cost_per_success"]
    assert candidate["success_rate"] == 0.4 and candidate["oom_rate"] == 0.6
    assert candidate["outcome_counts_in_cost_numerator"] == {"OOM": 6, "SUCCESS": 4}
    assert classify_pareto(candidate, reference) == "EFFICIENCY_RELIABILITY_TRADEOFF"


def test_tiny_allocation_with_timeout_or_incorrect_completion_cannot_dominate():
    reference = _pareto_row()
    timed_out = _pareto_row(
        cpu_cost_per_success=1.0, memory_cost_per_success=1.0,
        success_rate=0.9, correct_completion_rate=0.9, timeout_rate=0.1,
    )
    incorrect = _pareto_row(
        cpu_cost_per_success=1.0, memory_cost_per_success=1.0,
        success_rate=0.9, correct_completion_rate=0.9, incorrect_rate=0.1,
    )
    assert classify_pareto(timed_out, reference) == "EFFICIENCY_RELIABILITY_TRADEOFF"
    assert classify_pareto(incorrect, reference) == "EFFICIENCY_RELIABILITY_TRADEOFF"


def test_incorrect_completion_is_not_a_success_or_cost_denominator_member():
    plan, _, _ = _plan_with_counting_adapters()
    spec = EfficiencyTrialSpec.from_dict(plan["trials"][0])
    incorrect = derive_trial(_row(
        spec, correctness=False, success=False, observed_marker="0" * 64,
        correctness_invariants_ok=True,
    ))
    summary = summarize_families([incorrect])[0]
    assert incorrect["primary_outcome"] == "INCORRECT"
    assert summary["successful_tasks"] == 0
    assert summary["cpu_cost_per_success"] is None
    assert summary["cost_unavailable_reason"] == "ZERO_SUCCESS"


def test_oracle_interval_censoring_compares_requests_and_limits():
    plan, _, _ = _plan_with_counting_adapters()
    spec = EfficiencyTrialSpec.from_dict(next(row for row in plan["trials"] if row["condition"] == "P2_CATALOG"))
    oracle = {spec.family_id: {"cpu_selected_m": 500, "memory_selected_mib": 768, "cpu_minimum_interval": {"ordinary_interval_supported": True, "largest_tested_rejected": 300, "smallest_tested_accepted": 500}, "memory_minimum_interval": {"ordinary_interval_supported": True, "largest_tested_rejected": 512, "smallest_tested_accepted": 768}}}
    derived = derive_trial(_row(spec), oracle)
    assert derived["cpu_request_error"]["signed"] == 0
    assert derived["cpu_limit_error"]["signed"] == 500
    assert derived["cpu_limit_error"]["absolute"] == 500
    assert derived["cpu_limit_error"]["percentage"] == 100
    assert derived["cpu_limit_error"]["over"] == 500
    assert derived["cpu_limit_error"]["under"] == 0
    assert derived["cpu_request_error"]["comparison_role"] == "capacity_request_comparison"
    assert derived["cpu_limit_error"]["comparison_role"] == "oom_and_runtime_safety_limit_comparison"
    altered = dict(_row(spec)); altered["planned_resources"] = {**altered["planned_resources"], "cpu_request_m": 400}
    assert derive_trial(altered, oracle)["cpu_request_error"]["classification"] == "INDETERMINATE_UNTESTED_INTERVAL"


def test_pareto_never_calls_cheaper_but_less_reliable_an_improvement():
    reference = _pareto_row(cpu_cost_per_success=2, memory_cost_per_success=2)
    candidate = _pareto_row(cpu_cost_per_success=1, memory_cost_per_success=1, success_rate=.4, correct_completion_rate=.4, oom_rate=.6)
    assert classify_pareto(candidate, reference) == "EFFICIENCY_RELIABILITY_TRADEOFF"


def test_family_first_analysis_has_16_effective_units_not_160_repetitions():
    plan, _, _ = _plan_with_counting_adapters()
    rows = [_row(EfficiencyTrialSpec.from_dict(item)) for item in plan["trials"]]
    analysis = analyze_trials(rows, bootstrap_replicates=20)
    assert len(analysis["repetition_summaries"]) == PRIMARY_TRIAL_COUNT
    assert len(analysis["family_condition_summaries"]) == 64
    assert {row["effective_family_n"] for row in analysis["statistics"] if row["endpoint_role"] == "primary"} == {16}
    assert analysis["design_counts"] == {
        "number_of_families": 16,
        "repetitions_per_family_condition": 10,
        "raw_primary_trial_count": 640,
        "independent_semantic_n": 16,
    }
    assert max(row["effective_family_n"] for row in analysis["statistics"]) <= 16


def test_statistics_reject_duplicate_family_condition_rows():
    rows = [
        {"family_id": "f1", "condition": condition, **_pareto_row()}
        for condition in CONDITIONS
    ]
    with pytest.raises(ValueError, match="one summary per family-condition"):
        statistical_results([*rows, dict(rows[0])], bootstrap_replicates=10)


def test_capacity_packing_is_deterministic_and_separately_labeled():
    plan, _, _ = _plan_with_counting_adapters()
    rows = [_row(EfficiencyTrialSpec.from_dict(item), cgroup_metrics={"mean_cpu_m": 999999, "peak_memory_mib": 999999}) for item in plan["trials"]]
    capacity = _frozen_capacity()
    first = simulate_capacity(rows, capacity)
    assert first == simulate_capacity(list(reversed(rows)), capacity)
    assert first["evidence_label"] == LABEL and first["concurrent_cluster_evidence"] is False
    assert first["evidence_type"] == "SIMULATED_CAPACITY"
    assert first["capacity_source"] == "KUBERNETES_NODE_STATUS_ALLOCATABLE"
    assert first["scheduler_input"] == "OBSERVED_POD_RESOURCE_REQUESTS"
    assert len(first["homogeneous_family_density"]) == 64
    assert len(first["balanced_family_mix"]) == 4
    static = next(row for row in first["homogeneous_family_density"] if row["condition"] == "STATIC_LARGE")
    small = next(row for row in first["homogeneous_family_density"] if row["family_id"] == static["family_id"] and row["condition"] == "P1_CATALOG")
    assert static["schedulable_sessions"] == 2
    assert small["capacity_gain_sessions_vs_static_large"] == 14
    assert small["dominant_constraint"] == "MEMORY"


def test_capacity_refuses_disagreeing_observed_requests():
    plan, _, _ = _plan_with_counting_adapters()
    rows = [_row(EfficiencyTrialSpec.from_dict(item)) for item in plan["trials"]]
    rows[0] = dict(rows[0]); rows[0]["observed_resources"] = {**rows[0]["observed_resources"], "cpu_request_m": rows[0]["observed_resources"]["cpu_request_m"] + 1}
    with pytest.raises(ValueError, match="disagree"):
        verify_observed_requests(rows)


def test_capacity_refuses_physical_capacity_and_incomplete_repetitions():
    plan, _, _ = _plan_with_counting_adapters()
    rows = [_row(EfficiencyTrialSpec.from_dict(item)) for item in plan["trials"]]
    physical = {**_frozen_capacity(), "capacity_source": "RAW_PHYSICAL_CAPACITY"}
    result = simulate_capacity(rows, physical)
    assert result["status"] == "NOT_EXECUTED"
    assert result["reason"] == "CAPACITY_CONTRACT_IS_NOT_ALLOCATABLE_ONLY"
    incomplete = simulate_capacity(rows[:-1], _frozen_capacity())
    assert incomplete["status"] == "NOT_EXECUTED"
    assert incomplete["reason"] == "OBSERVED_REQUESTS_INCOMPLETE_OR_INCONSISTENT"


def test_one_infrastructure_replacement_and_sealing(tmp_path):
    plan, _, _ = _plan_with_counting_adapters()
    primary = plan["trials"][0]["primary_trial_id"]
    root = tmp_path / "raw"
    execute_plan(root=root, run_id="synthetic", plan=plan, adapter=SuccessfulExecutionAdapter(infrastructure_first=primary), enforce_readiness=False)
    status = validate_raw_package(root)
    assert status["trials"] == 641 and status["sealed"] is True
    rows = [json.loads(line) for line in (root / "raw" / "trials.jsonl").read_text().splitlines()]
    assert sum(row.get("replacement_of") is not None for row in rows) == 1
    with pytest.raises(ValueError, match="sealed or completed"):
        execute_plan(root=root, run_id="synthetic", plan=plan, adapter=SuccessfulExecutionAdapter(), resume=True, enforce_readiness=False)


def test_unsealed_prefix_resume_requires_identical_provenance(tmp_path):
    plan, _, _ = _plan_with_counting_adapters()
    root = tmp_path / "raw"
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        execute_plan(root=root, run_id="synthetic", plan=plan, adapter=SuccessfulExecutionAdapter(fail_after=3), enforce_readiness=False)
    assert not (root / "SHA256SUMS").exists()
    changed = SuccessfulExecutionAdapter()
    changed.environment_provenance = lambda: {"environment_id": "different-synthetic-environment", "measurements_are_real": False}
    with pytest.raises(ValueError, match="provenance hash differs"):
        execute_plan(root=root, run_id="synthetic", plan=plan, adapter=changed, resume=True, enforce_readiness=False)
    execute_plan(root=root, run_id="synthetic", plan=plan, adapter=SuccessfulExecutionAdapter(), resume=True, enforce_readiness=False)
    assert validate_raw_package(root)["trials"] == 640


def test_read_only_dry_run_is_explicit_not_executed(tmp_path):
    root = tmp_path / "dry"
    manifest = write_not_executed(
        root=root, run_id="dry", image="example.invalid/resource@sha256:" + "a" * 64,
        reason="contracts intentionally unavailable",
    )
    assert manifest["execution_status"] == "NOT_EXECUTED"
    assert manifest["kubernetes_mutations"] == []
    assert {"CONFIRMATORY_FREEZE_INACTIVE", "APPROVED_ORACLE_UNAVAILABLE", "IMAGE_DIGEST_UNVERIFIED", "NODE_CAPACITY_NOT_FROZEN"}.issubset(manifest["blocker_codes"])
    assert validate_raw_package(root)["trials"] == 0
    manifest_path = root / "manifest.json"
    manifest_path.write_text(manifest_path.read_text().replace("NOT_EXECUTED", "OBSERVED", 1))
    with pytest.raises(ValueError, match="integrity|checksum|SHA",):
        validate_raw_package(root)


def test_wrong_context_and_disabled_kubernetes_readiness_are_fail_closed(tmp_path):
    image = "example.invalid/resource@sha256:" + "a" * 64
    eligibility = evaluate_cluster_eligibility(
        policy=load_cluster_policy(), image=image, image_state=load_image_state(),
        current_context="unapproved-context", require_cgroup_probe=False,
    )
    assert "WRONG_KUBERNETES_CONTEXT" in eligibility["failure_codes"]
    plan, _, _ = _plan_with_counting_adapters()
    adapter = KubernetesResourceEfficiencyAdapter(image=image)
    with pytest.raises(ValueError, match="readiness gates cannot be disabled"):
        execute_plan(
            root=tmp_path / "must-not-exist", run_id="blocked", plan=plan,
            adapter=adapter, enforce_readiness=False,
        )
    assert not (tmp_path / "must-not-exist").exists()


def test_all_static_live_execution_prerequisites_remain_blockers():
    blockers = confirmatory_readiness(load_efficiency_freeze(), load_capacity_contract())
    assert blockers == [
        "CONFIRMATORY_FREEZE_INACTIVE", "APPROVED_ORACLE_UNAVAILABLE",
        "IMAGE_DIGEST_UNVERIFIED", "NODE_CAPACITY_NOT_FROZEN",
    ]


def test_analysis_report_contract_cannot_label_simulation_as_observed(tmp_path, monkeypatch):
    root = tmp_path / "analysis"
    for directory in ("derived", "statistics", "capacity", "report"):
        (root / directory).mkdir(parents=True)
    required = {
        "derived/trials.jsonl": "",
        "derived/repetition-summaries.jsonl": "",
        "derived/family-condition-summaries.json": "{}",
        "derived/condition-summaries.json": "{}",
        "derived/dynamic-allocation-summary.json": "{}",
        "statistics/results.json": json.dumps({
            "family_is_primary_unit": True,
            "repetitions_are_independent_families": False,
            "hierarchy": [
                "raw_trial_attempt", "family_condition_repetition",
                "family_condition", "paired_cross_family_inference",
            ],
            "design_counts": {
                "number_of_families": 16, "repetitions_per_family_condition": 10,
                "raw_primary_trial_count": 640, "independent_semantic_n": 16,
            },
            "rows": [{"effective_family_n": 16}],
        }),
        "capacity/simulation.json": json.dumps({
            "evidence_label": LABEL, "evidence_type": EVIDENCE_TYPE,
            "capacity_source": CAPACITY_SOURCE, "scheduler_input": SCHEDULER_INPUT,
            "concurrent_cluster_evidence": False,
        }),
        "report/pareto.json": json.dumps({
            "objectives": PARETO_OBJECTIVES,
            "success_noninferiority_margin": None,
        }),
        "report/report.json": json.dumps({
            "capacity_evidence_type": "SIMULATED_CAPACITY",
            "observed_concurrency_claims_permitted": False,
        }),
    }
    for relative, content in required.items():
        (root / relative).write_text(content)
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": "protocol-v5-resource-efficiency-analysis-package-v1.0.0",
        "raw_package_sha256": "a" * 64,
    }))
    from evaluation_v5.resource import efficiency_evidence
    monkeypatch.setattr(efficiency_evidence, "verify_integrity", lambda _: {"verified_files": 9})
    assert validate_analysis_package(root)["status"] == "pass"
    (root / "report" / "report.json").write_text(json.dumps({
        "capacity_evidence_type": "OBSERVED_CONCURRENCY",
        "observed_concurrency_claims_permitted": True,
    }))
    with pytest.raises(ValueError, match="simulated capacity"):
        validate_analysis_package(root)


def test_checked_in_capacity_contains_no_invented_values():
    capacity = load_capacity_contract()
    assert capacity["freeze_status"] == "NOT_FROZEN"
    assert capacity["allocatable"] == {"cpu_m": None, "memory_mib": None, "gpu_count": None, "gpu_resource": None}
    p3 = load_efficiency_freeze()["decision_policy"]["p3"]
    assert p3["included"] is False and p3["authoritative_gate"] == "not_retained"
