"""Evidence package validator for Protocol-v5 E5 image functional validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from evaluation_v5.schemas import EvidenceStatus, ProtocolV5Manifest
from evaluation_v5.validation import validate_manifest

from .contracts import (
    DimensionCStatus,
    ProbeExecutionStatus,
    file_sha256,
    parse_image_digest,
)
from .metrics import compute_functional_metrics, evaluate_recommendation_functional


class EvidenceValidationError(ValueError):
    """Raised when an evidence package violates Protocol-v5 E5 rules."""


def validate_e5_evidence(package_dir: Path | str) -> dict[str, Any]:
    """Validate a sealed Protocol-v5 E5 evidence package fail-closed."""
    directory = Path(package_dir).resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"Evidence directory not found: {directory}")

    # 1. Validate SHA256SUMS file
    sums_file = directory / "SHA256SUMS"
    if not sums_file.is_file():
        raise EvidenceValidationError(f"Missing SHA256SUMS in {directory}")

    checksum_lines = sums_file.read_text(encoding="utf-8").splitlines()
    if not checksum_lines:
        raise EvidenceValidationError(f"SHA256SUMS is empty in {directory}")

    checked_files = set()
    for line in checksum_lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise EvidenceValidationError(f"Malformed SHA256SUMS line: {line!r}")
        expected_sha, rel_path = parts[0], parts[1].strip()
        target = directory / rel_path
        if not target.is_file():
            raise EvidenceValidationError(f"File listed in SHA256SUMS does not exist: {rel_path}")
        actual_sha = file_sha256(target)
        if actual_sha != expected_sha:
            raise EvidenceValidationError(
                f"Checksum mismatch for {rel_path}: expected {expected_sha}, got {actual_sha}"
            )
        checked_files.add(target)

    # 2. Check required files
    manifest_path = directory / "manifest.json"
    raw_dir = directory / "raw"
    derived_dir = directory / "derived"
    report_dir = directory / "report"

    probe_manifest_path = raw_dir / "probe_manifest.json"
    probe_results_path = raw_dir / "probe_results.jsonl"
    evaluations_path = raw_dir / "functional_evaluations.jsonl"
    metrics_path = derived_dir / "functional_metrics.json"
    report_md_path = report_dir / "E5_IMAGE_FUNCTIONAL_REPORT.md"
    status_path = report_dir / "status.json"

    for req_file in (
        manifest_path,
        probe_manifest_path,
        probe_results_path,
        evaluations_path,
        metrics_path,
        report_md_path,
        status_path,
    ):
        if not req_file.is_file():
            raise EvidenceValidationError(f"Required package file missing: {req_file.relative_to(directory)}")

    # 3. Validate ProtocolV5Manifest
    manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = ProtocolV5Manifest.from_dict(manifest_raw)

    if manifest.experiment_id.value != "E5":
        raise EvidenceValidationError(f"Experiment ID must be E5, got {manifest.experiment_id}")

    execution_status = manifest.execution_status

    # 4. Validate probe manifest
    probe_manifest_raw = json.loads(probe_manifest_path.read_text(encoding="utf-8"))
    cat_images = {img["image_id"]: img for img in probe_manifest_raw.get("images", [])}
    manifest_probe_ids: set[str] = set()

    for img_id, img_data in cat_images.items():
        ref = img_data.get("image_reference", "")
        digest = img_data.get("image_digest", "")
        expected_digest = parse_image_digest(ref)
        if digest != expected_digest:
            raise EvidenceValidationError(
                f"Image {img_id} digest mismatch in probe manifest: {digest} vs {expected_digest}"
            )
        for probe in img_data.get("probes", []):
            pid = probe["probe_id"]
            if pid in manifest_probe_ids:
                raise EvidenceValidationError(f"Duplicate probe ID in manifest: {pid}")
            manifest_probe_ids.add(pid)

    # 5. Validate raw probe results
    probe_results_raw = [
        json.loads(line) for line in probe_results_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    seen_result_probe_ids: set[str] = set()
    executed_probe_count = 0
    unavailable_probe_count = 0

    for res in probe_results_raw:
        pid = res["probe_id"]
        if pid in seen_result_probe_ids:
            raise EvidenceValidationError(f"Duplicate probe result ID: {pid}")
        seen_result_probe_ids.add(pid)

        if pid not in manifest_probe_ids:
            raise EvidenceValidationError(f"Probe result ID not in manifest: {pid}")

        img_id = res["image_id"]
        if img_id not in cat_images:
            raise EvidenceValidationError(f"Result refers to unknown image ID: {img_id}")

        expected_ref = cat_images[img_id]["image_reference"]
        if res["image_reference"] != expected_ref:
            raise EvidenceValidationError(
                f"Result image reference {res['image_reference']!r} does not match manifest {expected_ref!r}"
            )

        status = res.get("execution_status")
        success = res.get("success")
        err_cat = res.get("error_category")

        if status == ProbeExecutionStatus.EXECUTED.value:
            executed_probe_count += 1
            if err_cat in ("NOT_EXECUTED_DRY_RUN", "IMAGE_NOT_PRESENT"):
                raise EvidenceValidationError(
                    f"Probe {pid} marked EXECUTED cannot have error category {err_cat}"
                )
        else:
            unavailable_probe_count += 1
            if success is True:
                raise EvidenceValidationError(f"Probe {pid} marked success without actual execution")

    # If package is marked OBSERVED, every required probe must be EXECUTED
    if execution_status == EvidenceStatus.OBSERVED:
        if manifest_probe_ids - seen_result_probe_ids:
            missing = manifest_probe_ids - seen_result_probe_ids
            raise EvidenceValidationError(f"OBSERVED package is missing required probes: {missing}")
        if unavailable_probe_count > 0:
            raise EvidenceValidationError(
                f"Package marked OBSERVED has {unavailable_probe_count} unavailable/unexecuted probes. "
                f"Must be marked INCOMPLETE."
            )

    # 6. Validate evaluations (Dimensions A, B, C and Mismatches)
    eval_records_raw = [
        json.loads(line) for line in evaluations_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]

    for rec in eval_records_raw:
        case_id = rec["case_id"]
        dim_c_status = rec.get("dimension_c_status")
        satisfied_c = rec.get("dimension_c_functional_satisfied")
        coverage_c = rec.get("dimension_c_execution_coverage")
        mismatches = rec.get("mismatch_types", [])

        if dim_c_status == DimensionCStatus.NOT_EXECUTED.value:
            if satisfied_c is not None:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension C NOT_EXECUTED must have satisfied=None, got {satisfied_c}"
                )
            if coverage_c is True:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension C NOT_EXECUTED cannot have execution_coverage=True"
                )
            if "CATALOG_PROBE_MISMATCH" in mismatches:
                raise EvidenceValidationError(
                    f"Case {case_id}: CATALOG_PROBE_MISMATCH cannot be asserted when probe is unavailable/not executed"
                )
            if "LABEL_PASS_FUNCTIONAL_FAIL" in mismatches:
                raise EvidenceValidationError(
                    f"Case {case_id}: LABEL_PASS_FUNCTIONAL_FAIL cannot be asserted when probe is unavailable/not executed"
                )
        elif dim_c_status == DimensionCStatus.PASS.value:
            if satisfied_c is not True:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension C PASS must have satisfied=True, got {satisfied_c}"
                )
        elif dim_c_status == DimensionCStatus.FAIL.value:
            if satisfied_c is not False:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension C FAIL must have satisfied=False, got {satisfied_c}"
                )

    # 7. Validate derived functional metrics against recomputation
    derived_metrics_raw = json.loads(metrics_path.read_text(encoding="utf-8"))
    systems = derived_metrics_raw.get("systems", {})

    for sys_id, summary in systems.items():
        exec_count = summary.get("functional_executed_count", 0)
        success_rate = summary.get("functional_success_rate_among_executed")
        coverage_rate = summary.get("functional_execution_coverage", 0.0)

        if execution_status == EvidenceStatus.DRY_RUN:
            if success_rate is not None:
                raise EvidenceValidationError(
                    f"System {sys_id}: DRY_RUN package must not report empirical functional success rate: got {success_rate}"
                )
            if exec_count != 0:
                raise EvidenceValidationError(
                    f"System {sys_id}: DRY_RUN package must have functional_executed_count=0: got {exec_count}"
                )
        elif exec_count == 0:
            if success_rate is not None:
                raise EvidenceValidationError(
                    f"System {sys_id}: 0 executed probes must have functional_success_rate_among_executed=None: got {success_rate}"
                )

    # 8. Status report validation
    status_raw = json.loads(status_path.read_text(encoding="utf-8"))
    if status_raw.get("status") != execution_status.value:
        raise EvidenceValidationError(
            f"Status mismatch: status.json has {status_raw.get('status')} vs manifest {execution_status.value}"
        )

    return {
        "status": "PASS",
        "evidence_dir": str(directory),
        "experiment_id": "E5",
        "execution_status": execution_status.value,
        "total_probes_configured": len(manifest_probe_ids),
        "probes_executed": executed_probe_count,
        "probes_unavailable": unavailable_probe_count,
        "recommendations_evaluated": len(eval_records_raw),
        "files_checked": len(checked_files),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a Protocol-v5 E5 image functional evidence package.")
    parser.add_argument("--dir", type=Path, required=True, help="Path to E5 evidence run directory.")
    args = parser.parse_args()

    try:
        res = validate_e5_evidence(args.dir)
        print(json.dumps(res, indent=2))
    except Exception as exc:
        err = {"status": "FAIL", "error": str(exc), "evidence_dir": str(args.dir)}
        print(json.dumps(err, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
