from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from experiments.jsonl_io import append_jsonl, export_csv, read_jsonl
from experiments.kubernetes_evidence import extract_metric_samples, extract_pod_evidence, load_json
from experiments.recorder import build_record, run_local_workload, workload_by_id
from experiments.result_schema import JSON_SCHEMA, REQUIRED_FIELDS, SCHEMA_VERSION, validate_record


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "kubernetes"
MANIFEST = ROOT / "benchmarks" / "workloads.yaml"


def fixture(name: str) -> dict:
    return load_json(FIXTURES / name)


def test_formal_schema_lists_every_required_result_field():
    schema_path = ROOT / "experiments" / "result_schema.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["properties"].keys() >= set(REQUIRED_FIELDS)
    assert schema["required"] == list(REQUIRED_FIELDS)
    assert schema["properties"]["schema_version"]["const"] == SCHEMA_VERSION
    assert JSON_SCHEMA["required"] == list(REQUIRED_FIELDS)


def test_kubernetes_fixture_evidence_extracts_resources_status_and_sanitized_metadata():
    evidence = extract_pod_evidence(
        fixture("pod_succeeded.json"),
        fixture("events_succeeded.json"),
    )

    assert evidence["phase"] == "Succeeded"
    assert evidence["termination_reason"] == "Completed"
    assert evidence["termination_exit_code"] == 0
    assert evidence["pod_pending_duration_seconds"] == 3
    assert evidence["workload_runtime_seconds"] == 20
    assert evidence["time_to_success_seconds"] == 28
    assert evidence["requests_limits"] == {
        "cpu_request_m": 1500,
        "cpu_limit_m": 2000,
        "memory_request_mi": 1464,
        "memory_limit_mi": 1907,
    }
    assert evidence["annotations"] == {
        "z2jh-context-demo.local/recommendation-reasons": "dataset size &gt;= 0.5GB; training/modeling context detected",
        "z2jh-context-demo.local/recommended-profile": "large",
    }
    assert "SECRET_TOKEN" not in evidence["environment_variables"]
    assert "CONTEXT_INTENT" not in evidence["environment_variables"]
    assert evidence["scheduling_or_pending_reasons"] == []


def test_kubernetes_fixture_records_oom_and_pending_reasons():
    evidence = extract_pod_evidence(
        fixture("pod_oomkilled.json"),
        fixture("events_pending.json"),
    )

    assert evidence["termination_reason"] == "OOMKilled"
    assert evidence["termination_exit_code"] == 137
    assert evidence["restart_count"] == 1
    assert evidence["oom_killed"] is True
    assert evidence["pod_pending_duration_seconds"] == 15
    assert any("FailedScheduling" in reason for reason in evidence["scheduling_or_pending_reasons"])


def test_metric_snapshot_statistics_are_observed_not_invented():
    samples = extract_metric_samples(fixture("metrics_succeeded.json"))
    missing = extract_metric_samples(None)

    assert samples == {
        "cpu_usage_m": 42,
        "cpu_measurement_statistic": "sample_maximum",
        "cpu_sampling_interval_seconds": None,
        "cpu_measurement_window_seconds": None,
        "cpu_measurement_source": "metrics_server",
        "peak_memory_mi": 212,
        "resource_measurement_source": "metrics_server",
    }
    assert missing == {
        "cpu_usage_m": None,
        "cpu_measurement_statistic": "unavailable",
        "cpu_sampling_interval_seconds": None,
        "cpu_measurement_window_seconds": None,
        "cpu_measurement_source": "not_available",
        "peak_memory_mi": None,
        "resource_measurement_source": "not_available",
    }


def test_build_record_contains_required_fields_and_policy_fallback():
    workload = workload_by_id(MANIFEST, "policy_gpu_disallowed")
    record = build_record(
        workload=workload,
        method="context_aware",
        repeat_index=2,
        seed=6101,
        environment_id="fixture-cluster",
        run_id="fixture-policy-run",
        pod_json=fixture("pod_succeeded.json"),
        events_json=fixture("events_succeeded.json"),
        metrics_json=fixture("metrics_succeeded.json"),
        supporting_log_paths=["tests/fixtures/kubernetes/pod_succeeded.json"],
    )

    validate_record(record)
    assert set(record) == set(REQUIRED_FIELDS)
    assert record["recommended_profile"] == "gpu_or_large"
    assert record["applied_profile"] == "medium"
    assert record["policy_warnings"]
    assert record["cpu_usage_m"] == 42
    assert record["cpu_measurement_statistic"] == "sample_maximum"
    assert record["success"] is True


def test_append_only_jsonl_and_derived_csv_export(tmp_path):
    workload = workload_by_id(MANIFEST, "light_basic_python")
    first = build_record(
        workload=workload,
        method="intent_only",
        repeat_index=0,
        seed=1101,
        environment_id="fixture-local",
        run_id="fixture-jsonl-1",
    )
    second = build_record(
        workload=workload,
        method="static_manual",
        repeat_index=1,
        seed=1102,
        environment_id="fixture-local",
        run_id="fixture-jsonl-2",
        error_message="example partial failure retained",
        cleanup_status="completed",
    )
    jsonl_path = tmp_path / "raw" / "results.jsonl"
    csv_path = tmp_path / "summaries" / "results.csv"

    append_jsonl(jsonl_path, first)
    original_size = jsonl_path.stat().st_size
    append_jsonl(jsonl_path, second)

    assert jsonl_path.stat().st_size > original_size
    assert [record["run_id"] for record in read_jsonl(jsonl_path)] == [
        "fixture-jsonl-1",
        "fixture-jsonl-2",
    ]

    export_csv(read_jsonl(jsonl_path), csv_path)
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["run_id"] == "fixture-jsonl-1"
    assert rows[1]["error_message"] == "example partial failure retained"
    with pytest.raises(FileExistsError):
        export_csv(read_jsonl(jsonl_path), csv_path)


def test_local_smoke_workload_can_be_recorded_without_cluster(tmp_path):
    workload = workload_by_id(MANIFEST, "light_basic_python")
    local_result = run_local_workload(workload, 1101, tmp_path / "artifacts")
    record = build_record(
        workload=workload,
        method="context_aware",
        repeat_index=0,
        seed=1101,
        environment_id="local-smoke",
        run_id="fixture-local-smoke",
        local_result=local_result,
    )

    validate_record(record)
    assert record["success"] is True
    assert record["exit_code"] == 0
    assert record["workload_runtime_seconds"] is not None
    assert record["peak_memory_mi"] is not None
    assert record["resource_measurement_source"] == "python_resource_getrusage"
    assert len(record["supporting_log_paths"]) == 2
