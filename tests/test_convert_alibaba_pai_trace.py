#!/usr/bin/env python3
"""Tests for Alibaba PAI cluster trace converter and normalizer."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import pytest

from scripts.datasets.convert_alibaba_pai_trace import (
    categorize_workload,
    compute_actual_peak_cpu,
    compute_actual_peak_mem,
    extract_raw_fields,
    generate_empirical_pai_sample,
    normalize_cpu_request,
    normalize_gpu_request,
    normalize_mem_request,
    normalize_trace_records,
    read_raw_pai_trace,
    export_simulation_jsonl,
)


def test_normalize_cpu_request() -> None:
    # Percentage format (PAI default: 100% = 1 core = 1000m)
    assert normalize_cpu_request(600.0) == 6000
    assert normalize_cpu_request(100.0) == 1000
    assert normalize_cpu_request(50.0) == 500

    # Core count format
    assert normalize_cpu_request(4.0) == 4000
    assert normalize_cpu_request(8.0) == 8000
    assert normalize_cpu_request(0.5) == 500
    assert normalize_cpu_request(0.0) == 1000  # Default minimum 1 vCPU


def test_normalize_mem_request() -> None:
    assert normalize_mem_request(16.0) == 16384.0
    assert normalize_mem_request(8.0) == 8192.0
    assert normalize_mem_request(0.0) == 1024.0


def test_normalize_gpu_request() -> None:
    # Percentage format (PAI default: 100% = 1 GPU)
    assert normalize_gpu_request(100.0) == 1.0
    assert normalize_gpu_request(200.0) == 2.0
    assert normalize_gpu_request(50.0) == 0.5

    # Count format
    assert normalize_gpu_request(1.0) == 1.0
    assert normalize_gpu_request(4.0) == 4.0
    assert normalize_gpu_request(0.0) == 0.0


def test_compute_actual_peak_cpu() -> None:
    # 4 vCPU = 4000m, 50% utilization -> 2000m
    assert compute_actual_peak_cpu(4000, 50.0) == 2000.0

    # Multi-core percentage (>100% e.g. 140% = 1.4 cores = 1400m)
    assert compute_actual_peak_cpu(4000, 140.0) == 1400.0

    # Zero or idling
    assert compute_actual_peak_cpu(4000, 0.0) == 320.0


def test_compute_actual_peak_mem() -> None:
    # Explicit max_mem_gb provided
    assert compute_actual_peak_mem(16384.0, max_mem_gb=4.0) == 4096.0

    # Explicit mem_util percentage provided (50% of 16384 MiB)
    assert compute_actual_peak_mem(16384.0, mem_util=50.0) == 8192.0

    # Empirical ratio fallback for GPU task
    peak = compute_actual_peak_mem(16384.0, is_gpu=True, task_category="deep_learning", seed=42)
    assert 0 < peak <= 16384.0


def test_categorize_workload() -> None:
    assert categorize_workload("ps_0", req_gpu=0, req_cpu_m=4000, req_mem_mib=32768) == "parameter_server"
    assert categorize_workload("evaluator_1", req_gpu=1, req_cpu_m=2000, req_mem_mib=8192) == "inference_eval"
    assert categorize_workload("data_prep", req_gpu=0, req_cpu_m=2000, req_mem_mib=4096) == "data_prep"
    assert categorize_workload("worker_0", req_gpu=1.0, req_cpu_m=4000, req_mem_mib=16384) == "deep_learning"
    assert categorize_workload("worker_0", req_gpu=0.0, req_cpu_m=8000, req_mem_mib=4096) == "compute_heavy"
    assert categorize_workload("worker_0", req_gpu=0.0, req_cpu_m=2000, req_mem_mib=16384) == "memory_heavy"


def test_extract_raw_fields_and_filtering() -> None:
    # Valid row
    valid_row = {
        "job_name": "job_123",
        "task_name": "worker",
        "start_time": "100.0",
        "end_time": "250.0",
        "plan_cpu": "400.0",
        "plan_mem": "16.0",
        "plan_gpu": "100.0",
        "gpu_util": "60.0",
        "cpu_util": "45.0",
    }
    extracted = extract_raw_fields(valid_row)
    assert extracted is not None
    assert extracted["job_name"] == "job_123"
    assert extracted["task_name"] == "worker"
    assert extracted["start_time"] == 100.0
    assert extracted["end_time"] == 250.0
    assert extracted["plan_cpu"] == 400.0
    assert extracted["plan_mem"] == 16.0
    assert extracted["plan_gpu"] == 100.0

    # Invalid row: non-positive duration (end <= start)
    invalid_row = {
        "job_name": "job_fail",
        "task_name": "worker",
        "start_time": "200.0",
        "end_time": "150.0",
    }
    assert extract_raw_fields(invalid_row) is None


def test_normalize_trace_records() -> None:
    raw_records = [
        {
            "job_name": "job_b",
            "task_name": "worker",
            "start_time": 1050.0,
            "end_time": 1150.0,
            "plan_cpu": 600.0,
            "plan_mem": 16.0,
            "plan_gpu": 100.0,
            "gpu_util": 80.0,
            "cpu_util": 50.0,
        },
        {
            "job_name": "job_a",
            "task_name": "ps",
            "start_time": 1000.0,
            "end_time": 1200.0,
            "plan_cpu": 400.0,
            "plan_mem": 32.0,
            "plan_gpu": 0.0,
            "gpu_util": 0.0,
            "cpu_util": 60.0,
        },
    ]

    normalized = normalize_trace_records(raw_records)
    assert len(normalized) == 2

    # Verify chronological ordering by arrival_time_s
    assert normalized[0]["job_id"] == "job_a"
    assert normalized[0]["arrival_time_s"] == 0.0  # min start_time mapped to 0.0s
    assert normalized[0]["duration_seconds"] == 200.0
    assert normalized[0]["category"] == "parameter_server"
    assert normalized[0]["req_cpu_m"] == 4000
    assert normalized[0]["req_mem_mib"] == 32768.0
    assert normalized[0]["is_gpu"] is False

    assert normalized[1]["job_id"] == "job_b"
    assert normalized[1]["arrival_time_s"] == 50.0  # 1050 - 1000 = 50s
    assert normalized[1]["duration_seconds"] == 100.0
    assert normalized[1]["category"] == "deep_learning"
    assert normalized[1]["req_gpu"] == 1.0
    assert normalized[1]["is_gpu"] is True


def test_end_to_end_io(tmp_path: Path) -> None:
    csv_file = tmp_path / "sample_input.csv"
    jsonl_output = tmp_path / "sample_output.jsonl"

    fieldnames = [
        "job_name", "task_name", "start_time", "end_time",
        "plan_cpu", "plan_mem", "plan_gpu", "gpu_util", "cpu_util"
    ]
    rows = [
        ["job_001", "worker", "5000.0", "5600.0", "400.0", "16.0", "100.0", "75.0", "40.0"],
        ["job_002", "evaluator", "5100.0", "5250.0", "200.0", "8.0", "50.0", "30.0", "25.0"],
    ]

    with csv_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(fieldnames)
        writer.writerows(rows)

    extracted_records = []
    for r in read_raw_pai_trace(csv_file):
        ext = extract_raw_fields(r)
        if ext:
            extracted_records.append(ext)

    assert len(extracted_records) == 2
    normalized = normalize_trace_records(extracted_records)
    export_simulation_jsonl(normalized, jsonl_output)

    assert jsonl_output.is_file()
    lines = jsonl_output.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    first_item = json.loads(lines[0])
    assert first_item["job_id"] == "job_001"
    assert first_item["arrival_time_s"] == 0.0
    assert first_item["duration_seconds"] == 600.0
    assert first_item["actual_peak_cpu_m"] == 1600.0  # 4000m * 40% = 1600m
