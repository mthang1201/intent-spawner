"""Interval-aware comparison of frozen allocations to sealed independent E4 evidence.

This module consumes files only.  It never instantiates or calls a recommendation
system, and it is not imported by the calibration execution path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from .contracts import COMPARISON_SCHEMA_PATH, CROSSWALK_PATH, load_crosswalk
from .evidence import file_sha256, validate_evidence_package


ROOT = Path(__file__).resolve().parents[2]
ALLOCATION_SCHEMA = ROOT / "benchmarks_v5" / "protocol-v5-resource-allocation-evidence-v1.schema.json"
COMPARISON_VERSION = "protocol-v5-resource-allocation-comparison-v1.0.0"


def classify_axis(allocation: int, interval: Mapping[str, Any], selected: int | None) -> str:
    if selected is None or not interval.get("ordinary_interval_supported"):
        return "NO_REFERENCE_AVAILABLE"
    rejected = interval.get("largest_tested_rejected")
    accepted = interval.get("smallest_tested_accepted")
    if not isinstance(accepted, int):
        return "NO_REFERENCE_AVAILABLE"
    if isinstance(rejected, int) and allocation <= rejected:
        return "EMPIRICALLY_INSUFFICIENT"
    if allocation >= accepted:
        return "EMPIRICALLY_SUPPORTED"
    return "INDETERMINATE_UNTESTED_INTERVAL"


def _joint(cpu_status: str, memory_status: str) -> str:
    if "NO_REFERENCE_AVAILABLE" in {cpu_status, memory_status}:
        return "NO_REFERENCE_AVAILABLE"
    if "EMPIRICALLY_INSUFFICIENT" in {cpu_status, memory_status}:
        return "UNDER_ALLOCATION_ON_AT_LEAST_ONE_AXIS"
    if cpu_status == memory_status == "EMPIRICALLY_SUPPORTED":
        return "EMPIRICALLY_SUPPORTED_BOTH_AXES"
    return "INDETERMINATE_ON_AT_LEAST_ONE_AXIS"


def compare_allocations(
    *,
    e4_package: Path,
    allocation_evidence: Path,
    crosswalk_path: Path = CROSSWALK_PATH,
) -> dict[str, Any]:
    package = validate_evidence_package(e4_package)
    if package["execution_status"] != "OBSERVED" or not package["sealed"]:
        raise ValueError("comparison requires sealed observed E4 evidence")
    package_approved = package["eligible_for_comparison"]
    derived_path = e4_package / "derived" / "safe-envelopes.json"
    derived = json.loads(derived_path.read_text(encoding="utf-8"))
    envelopes = {item["family_id"]: item for item in derived["envelopes"]}

    allocation = json.loads(allocation_evidence.read_text(encoding="utf-8"))
    schema = json.loads(ALLOCATION_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(allocation)
    crosswalk = load_crosswalk(crosswalk_path)
    links = {item["allocation_case_id"]: item for item in crosswalk["entries"]}

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case in allocation["cases"]:
        case_id = case["allocation_case_id"]
        if case_id in seen:
            raise ValueError(f"duplicate allocation case {case_id}")
        seen.add(case_id)
        link = links.get(case_id)
        if link is None:
            raise ValueError(f"missing frozen crosswalk for {case_id}")
        if (
            case["workload_instance_id"] != link["workload_instance_id"]
            or case["workload_fingerprint"] != link["workload_fingerprint"]
        ):
            raise ValueError(f"allocation/crosswalk workload mismatch for {case_id}")
        envelope = envelopes.get(link["family_id"])
        eligible = bool(
            package_approved
            and envelope
            and envelope.get("status") == "CALIBRATED_PENDING_REVIEW"
            and envelope.get("manual_review_status") == "PENDING"
            and envelope.get("workload_instance_id") == link["workload_instance_id"]
            and envelope.get("workload_fingerprint") == link["workload_fingerprint"]
        )
        if not eligible:
            cpu_status = memory_status = "NO_REFERENCE_AVAILABLE"
            selected_cpu = selected_memory = None
        else:
            selected_cpu = envelope["cpu_selected_m"]
            selected_memory = envelope["memory_selected_mib"]
            cpu_status = classify_axis(case["cpu_m"], envelope["cpu_minimum_interval"], selected_cpu)
            memory_status = classify_axis(case["memory_mib"], envelope["memory_minimum_interval"], selected_memory)
        results.append({
            "allocation_case_id": case_id,
            "family_id": link["family_id"],
            "workload_instance_id": link["workload_instance_id"],
            "workload_fingerprint": link["workload_fingerprint"],
            "cpu_classification": cpu_status,
            "memory_classification": memory_status,
            "joint_classification": _joint(cpu_status, memory_status),
            "cpu_allocation_m": case["cpu_m"],
            "memory_allocation_mib": case["memory_mib"],
            "cpu_safe_reference_m": selected_cpu,
            "memory_safe_reference_mib": selected_memory,
            "cpu_ratio_to_safe_reference": None if selected_cpu is None else case["cpu_m"] / selected_cpu,
            "memory_ratio_to_safe_reference": None if selected_memory is None else case["memory_mib"] / selected_memory,
            "cpu_absolute_excess_m": None if selected_cpu is None else case["cpu_m"] - selected_cpu,
            "memory_absolute_excess_mib": None if selected_memory is None else case["memory_mib"] - selected_memory,
            "cpu_percentage_excess": None if selected_cpu is None else (case["cpu_m"] - selected_cpu) / selected_cpu * 100,
            "memory_percentage_excess": None if selected_memory is None else (case["memory_mib"] - selected_memory) / selected_memory * 100,
        })
    report = {
        "schema_version": COMPARISON_VERSION,
        "protocol_version": "5.0.0",
        "classification_semantics": {
            "insufficient": "allocation <= largest tested rejected point",
            "supported": "allocation >= smallest tested accepted and jointly verified reference",
            "indeterminate": "allocation lies strictly between tested reject and accept bounds",
            "axes": "CPU and memory remain separate; no scalar resource score is computed",
        },
        "e4_package_sha256s": file_sha256(e4_package / "SHA256SUMS"),
        "allocation_evidence_sha256": file_sha256(allocation_evidence),
        "crosswalk_sha256": file_sha256(crosswalk_path),
        "results": results,
    }
    output_schema = json.loads(COMPARISON_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(output_schema).validate(report)
    return report


__all__ = ["COMPARISON_VERSION", "classify_axis", "compare_allocations"]
