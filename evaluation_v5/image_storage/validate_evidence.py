"""Evidence package validator for Protocol-v5 E5 image functional validation."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from evaluation_v5.schemas import EvidenceStatus, ProtocolV5Manifest
from evaluation_v5.validation import validate_manifest

from evaluation_v5.image_storage.contracts import (
    DimensionCStatus,
    ProbeExecutionStatus,
    file_sha256,
    parse_image_digest,
)
from evaluation_v5.image_storage.metrics import compute_functional_metrics, evaluate_recommendation_functional


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
    try:
        manifest = ProtocolV5Manifest.from_dict(manifest_raw)
    except Exception as exc:
        raise EvidenceValidationError(f"Invalid manifest in {directory}: {exc}") from exc

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
    probe_results_map = {(res["image_id"], res["capability"]): res for res in probe_results_raw}
    cat_images_caps = {img_id: set(img_data.get("documented_capabilities", [])) for img_id, img_data in cat_images.items()}

    for rec in eval_records_raw:
        case_id = rec["case_id"]
        pimg = rec.get("predicted_image_id")
        dim_b_sat = rec.get("dimension_b_catalog_satisfied")
        dim_c_status = rec.get("dimension_c_status")
        satisfied_c = rec.get("dimension_c_functional_satisfied")
        coverage_c = rec.get("dimension_c_execution_coverage")
        mismatches = rec.get("mismatch_types", [])
        is_v13 = rec.get("schema_version") == "protocol-v5-image-functional-evaluation-v1.3.0"
        is_v12 = rec.get("schema_version") == "protocol-v5-image-functional-evaluation-v1.2.0"

        # Invariant: If predicted_image_id is None
        if not pimg:
            if (is_v12 or is_v13) and "NO_IMAGE_RECOMMENDATION" not in mismatches:
                raise EvidenceValidationError(
                    f"Case {case_id}: missing image recommendation must emit NO_IMAGE_RECOMMENDATION"
                )
            if (is_v12 or is_v13) and "EXECUTION_UNAVAILABLE" in mismatches:
                raise EvidenceValidationError(
                    f"Case {case_id}: missing image recommendation must NOT emit EXECUTION_UNAVAILABLE"
                )
            if dim_c_status not in (DimensionCStatus.NOT_EXECUTED.value, DimensionCStatus.NOT_APPLICABLE.value):
                raise EvidenceValidationError(
                    f"Case {case_id}: missing image recommendation must have status NOT_APPLICABLE or NOT_EXECUTED, got {dim_c_status}"
                )

        # Invariant: Dimension B strictly recomputable from catalog capabilities
        if is_v13 and pimg:
            declared_caps = cat_images_caps.get(pimg, set())
            req_caps = set(rec.get("required_capabilities", []))
            expected_b_sat = req_caps.issubset(declared_caps)
            if dim_b_sat != expected_b_sat:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension B mismatch: expected {expected_b_sat}, got {dim_b_sat}"
                )

        # Invariant: If dimension_b_catalog_satisfied is False in v1.2
        if not dim_b_sat and is_v12:
            if dim_c_status == DimensionCStatus.PASS.value:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension B is unsatisfied; Dimension C MUST NOT be PASS in v1.2"
                )
            if pimg and "CAPABILITY_UNSATISFIED" not in mismatches:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension B is unsatisfied; mismatch_types must include CAPABILITY_UNSATISFIED"
                )
            if "EXECUTION_UNAVAILABLE" in mismatches:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension B is unsatisfied; mismatch_types must NOT include EXECUTION_UNAVAILABLE"
                )

        # Invariant: EXECUTION_UNAVAILABLE in v1.2 requires predicted_image_id and dim_b_satisfied
        if "EXECUTION_UNAVAILABLE" in mismatches and is_v12:
            if not pimg:
                raise EvidenceValidationError(
                    f"Case {case_id}: EXECUTION_UNAVAILABLE cannot be asserted without an image recommendation"
                )
            if not dim_b_sat:
                raise EvidenceValidationError(
                    f"Case {case_id}: EXECUTION_UNAVAILABLE cannot be asserted when catalog capabilities are unsatisfied"
                )

        # Invariant: LABEL_FAIL_FUNCTIONAL_PASS requires dim_b_satisfied is True and dim_c_status is PASS
        if "LABEL_FAIL_FUNCTIONAL_PASS" in mismatches and (is_v12 or is_v13):
            if not dim_b_sat:
                raise EvidenceValidationError(
                    f"Case {case_id}: LABEL_FAIL_FUNCTIONAL_PASS cannot be asserted when Dimension B is unsatisfied"
                )
            if dim_c_status != DimensionCStatus.PASS.value:
                raise EvidenceValidationError(
                    f"Case {case_id}: LABEL_FAIL_FUNCTIONAL_PASS requires Dimension C to be PASS, got {dim_c_status}"
                )

        # Invariants for v1.3 Dimension C decoupling and discrepancy taxonomy
        if is_v13 and pimg:
            req_caps = rec.get("required_capabilities", [])
            caps_to_check = set(req_caps) if req_caps else {"python"}

            all_probes_executed_and_passed = True
            any_probe_failed = False
            any_probe_unavailable = False
            missing_probe_defs = []

            for cap in sorted(caps_to_check):
                key = (pimg, cap)
                res = probe_results_map.get(key)
                if res is None:
                    all_probes_executed_and_passed = False
                    missing_probe_defs.append(cap)
                elif res.get("execution_status") != ProbeExecutionStatus.EXECUTED.value:
                    all_probes_executed_and_passed = False
                    any_probe_unavailable = True
                elif not res.get("success"):
                    all_probes_executed_and_passed = False
                    any_probe_failed = True

            # Invariant 2 & 6 & 7: C=PASS requires all required capabilities to have executed successful exact probes
            if all_probes_executed_and_passed:
                if dim_c_status != DimensionCStatus.PASS.value:
                    raise EvidenceValidationError(
                        f"Case {case_id}: All required probes for {caps_to_check} passed on {pimg}, "
                        f"so Dimension C MUST be PASS; got {dim_c_status} (B={dim_b_sat} does not suppress C)"
                    )
            elif dim_c_status == DimensionCStatus.PASS.value:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension C is PASS but required capabilities {caps_to_check} "
                    f"did not all have executed successful exact probes on {pimg}."
                )

            # Invariant 3: If exact probes exist and passed, cannot claim NOT_EXECUTED
            if dim_c_status == DimensionCStatus.NOT_EXECUTED.value and all_probes_executed_and_passed:
                raise EvidenceValidationError(
                    f"Case {case_id}: Record cannot claim NOT_EXECUTED when all required probes executed and passed."
                )

            # Invariant 4: Catalog underclaim + successful exact probe emits CATALOG_UNDERCLAIM_FUNCTIONAL_PASS
            if not dim_b_sat and dim_c_status == DimensionCStatus.PASS.value:
                if "CATALOG_UNDERCLAIM_FUNCTIONAL_PASS" not in mismatches:
                    raise EvidenceValidationError(
                        f"Case {case_id}: Catalog underclaim with passing functional probe must emit "
                        f"CATALOG_UNDERCLAIM_FUNCTIONAL_PASS."
                    )
            if "CATALOG_UNDERCLAIM_FUNCTIONAL_PASS" in mismatches:
                if dim_b_sat:
                    raise EvidenceValidationError(
                        f"Case {case_id}: CATALOG_UNDERCLAIM_FUNCTIONAL_PASS cannot be asserted when Dimension B is satisfied."
                    )
                if dim_c_status != DimensionCStatus.PASS.value:
                    raise EvidenceValidationError(
                        f"Case {case_id}: CATALOG_UNDERCLAIM_FUNCTIONAL_PASS requires Dimension C to be PASS, got {dim_c_status}."
                    )

            # Invariant 5: Missing probe definition is distinct from runtime execution unavailable
            if missing_probe_defs:
                if "REQUIRED_PROBE_NOT_DEFINED" not in mismatches:
                    raise EvidenceValidationError(
                        f"Case {case_id}: Missing probe definition for {missing_probe_defs} must emit REQUIRED_PROBE_NOT_DEFINED."
                    )
                if not any_probe_unavailable and "EXECUTION_UNAVAILABLE" in mismatches:
                    raise EvidenceValidationError(
                        f"Case {case_id}: EXECUTION_UNAVAILABLE emitted when probe definition was missing; "
                        f"missing probe definition must not collapse into EXECUTION_UNAVAILABLE."
                    )

        if dim_c_status in (DimensionCStatus.NOT_EXECUTED.value, DimensionCStatus.NOT_APPLICABLE.value):
            if satisfied_c is not None:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension C {dim_c_status} must have satisfied=None, got {satisfied_c}"
                )
            if coverage_c is True:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension C {dim_c_status} cannot have execution_coverage=True"
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
            if coverage_c is not True:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension C PASS must have execution_coverage=True"
                )
            if is_v12 and not dim_b_sat:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension C PASS cannot be asserted when Dimension B is unsatisfied"
                )
        elif dim_c_status == DimensionCStatus.FAIL.value:
            if satisfied_c is not False:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension C FAIL must have satisfied=False, got {satisfied_c}"
                )
            if coverage_c is not True:
                raise EvidenceValidationError(
                    f"Case {case_id}: Dimension C FAIL must have execution_coverage=True"
                )

    # 7. Validate derived functional metrics against recomputation
    derived_metrics_raw = json.loads(metrics_path.read_text(encoding="utf-8"))
    systems = derived_metrics_raw.get("systems", {})
    is_metrics_v13 = derived_metrics_raw.get("schema_version") == "protocol-v5-image-functional-metrics-v1.3.0"
    is_metrics_v12 = derived_metrics_raw.get("schema_version") == "protocol-v5-image-functional-metrics-v1.2.0"

    by_system_recs = defaultdict(list)
    for rec in eval_records_raw:
        by_system_recs[rec["system_id"]].append(rec)

    for sys_id, summary in systems.items():
        sys_records = by_system_recs.get(sys_id, [])
        n = len(sys_records)
        if summary.get("total_recommendations") != n:
            raise EvidenceValidationError(
                f"System {sys_id}: total_recommendations mismatch: {summary.get('total_recommendations')} vs {n}"
            )

        if is_metrics_v12:
            expected_with_img = sum(1 for r in sys_records if r.get("predicted_image_id") is not None)
            expected_no_img = n - expected_with_img
            expected_b_sat = sum(1 for r in sys_records if r.get("dimension_b_catalog_satisfied"))
            expected_b_unsat = n - expected_b_sat
            expected_eligible = sum(
                1 for r in sys_records if r.get("predicted_image_id") is not None and r.get("dimension_b_catalog_satisfied")
            )
            expected_exec = sum(
                1 for r in sys_records
                if r.get("predicted_image_id") is not None and r.get("dimension_b_catalog_satisfied") and r.get("dimension_c_execution_coverage")
            )
            expected_pass = sum(
                1 for r in sys_records
                if r.get("predicted_image_id") is not None and r.get("dimension_b_catalog_satisfied") and r.get("dimension_c_status") == DimensionCStatus.PASS.value
            )
            expected_fail = sum(
                1 for r in sys_records
                if r.get("predicted_image_id") is not None and r.get("dimension_b_catalog_satisfied") and r.get("dimension_c_status") == DimensionCStatus.FAIL.value
            )
            expected_unavail = sum(
                1 for r in sys_records
                if r.get("predicted_image_id") is not None and r.get("dimension_b_catalog_satisfied") and r.get("dimension_c_status") == DimensionCStatus.NOT_EXECUTED.value
            )

            if summary.get("recommendations_with_image_count") != expected_with_img:
                raise EvidenceValidationError(f"System {sys_id}: recommendations_with_image_count mismatch")
            if summary.get("no_image_recommendation_count") != expected_no_img:
                raise EvidenceValidationError(f"System {sys_id}: no_image_recommendation_count mismatch")
            if summary.get("catalog_capability_satisfied_count") != expected_b_sat:
                raise EvidenceValidationError(f"System {sys_id}: catalog_capability_satisfied_count mismatch")
            if summary.get("catalog_unsatisfied_count") != expected_b_unsat:
                raise EvidenceValidationError(f"System {sys_id}: catalog_unsatisfied_count mismatch")
            if summary.get("functional_validation_eligible_count") != expected_eligible:
                raise EvidenceValidationError(f"System {sys_id}: functional_validation_eligible_count mismatch")
            if summary.get("functional_executed_count") != expected_exec:
                raise EvidenceValidationError(f"System {sys_id}: functional_executed_count mismatch")
            if summary.get("functional_passed_count") != expected_pass:
                raise EvidenceValidationError(f"System {sys_id}: functional_passed_count mismatch")
            if summary.get("functional_failed_count") != expected_fail:
                raise EvidenceValidationError(f"System {sys_id}: functional_failed_count mismatch")
            if summary.get("functional_unavailable_count") != expected_unavail:
                raise EvidenceValidationError(f"System {sys_id}: functional_unavailable_count mismatch")
            if summary.get("operationally_adequate_count") != expected_pass:
                raise EvidenceValidationError(f"System {sys_id}: operationally_adequate_count mismatch")

            expected_pref = sum(1 for r in sys_records if r.get("dimension_a_preferred_match"))
            expected_acc = sum(1 for r in sys_records if r.get("dimension_a_gold_match"))
            if summary.get("gold_preferred_count") != expected_pref:
                raise EvidenceValidationError(f"System {sys_id}: gold_preferred_count mismatch")
            if summary.get("gold_acceptable_count") != expected_acc:
                raise EvidenceValidationError(f"System {sys_id}: gold_acceptable_count mismatch")

        elif is_metrics_v13:
            expected_with_img = sum(1 for r in sys_records if r.get("predicted_image_id") is not None)
            expected_no_img = n - expected_with_img
            expected_b_sat = sum(1 for r in sys_records if r.get("dimension_b_catalog_satisfied"))
            expected_b_unsat = n - expected_b_sat
            expected_eligible = sum(
                1 for r in sys_records
                if r.get("predicted_image_id") is not None and r.get("dimension_c_eligible", True)
            )
            expected_exec = sum(
                1 for r in sys_records
                if r.get("predicted_image_id") is not None and r.get("dimension_c_eligible", True) and r.get("dimension_c_execution_coverage")
            )
            expected_pass = sum(
                1 for r in sys_records
                if r.get("predicted_image_id") is not None and r.get("dimension_c_eligible", True) and r.get("dimension_c_status") == DimensionCStatus.PASS.value
            )
            expected_fail = sum(
                1 for r in sys_records
                if r.get("predicted_image_id") is not None and r.get("dimension_c_eligible", True) and r.get("dimension_c_status") == DimensionCStatus.FAIL.value
            )
            expected_unavail = sum(
                1 for r in sys_records
                if r.get("predicted_image_id") is not None and r.get("dimension_c_eligible", True) and r.get("dimension_c_status") == DimensionCStatus.NOT_EXECUTED.value
            )
            expected_op_adequate = sum(
                1 for r in sys_records
                if r.get("predicted_image_id") is not None
                and r.get("dimension_b_catalog_satisfied")
                and r.get("dimension_c_status") == DimensionCStatus.PASS.value
            )
            expected_pref = sum(1 for r in sys_records if r.get("dimension_a_preferred_match"))
            expected_acc = sum(1 for r in sys_records if r.get("dimension_a_gold_match"))

            if summary.get("recommendations_with_image_count") != expected_with_img:
                raise EvidenceValidationError(f"System {sys_id}: recommendations_with_image_count mismatch")
            if summary.get("no_image_recommendation_count") != expected_no_img:
                raise EvidenceValidationError(f"System {sys_id}: no_image_recommendation_count mismatch")
            if summary.get("catalog_capability_satisfied_count") != expected_b_sat:
                raise EvidenceValidationError(f"System {sys_id}: catalog_capability_satisfied_count mismatch")
            if summary.get("catalog_unsatisfied_count") != expected_b_unsat:
                raise EvidenceValidationError(f"System {sys_id}: catalog_unsatisfied_count mismatch")
            if summary.get("functional_validation_eligible_count") != expected_eligible:
                raise EvidenceValidationError(f"System {sys_id}: functional_validation_eligible_count mismatch")
            if summary.get("functional_executed_count") != expected_exec:
                raise EvidenceValidationError(f"System {sys_id}: functional_executed_count mismatch")
            if summary.get("functional_passed_count") != expected_pass:
                raise EvidenceValidationError(f"System {sys_id}: functional_passed_count mismatch")
            if summary.get("functional_failed_count") != expected_fail:
                raise EvidenceValidationError(f"System {sys_id}: functional_failed_count mismatch")
            if summary.get("functional_unavailable_count") != expected_unavail:
                raise EvidenceValidationError(f"System {sys_id}: functional_unavailable_count mismatch")
            if summary.get("operationally_adequate_count") != expected_op_adequate:
                raise EvidenceValidationError(f"System {sys_id}: operationally_adequate_count mismatch")
            if summary.get("gold_preferred_count") != expected_pref:
                raise EvidenceValidationError(f"System {sys_id}: gold_preferred_count mismatch")
            if summary.get("gold_acceptable_count") != expected_acc:
                raise EvidenceValidationError(f"System {sys_id}: gold_acceptable_count mismatch")

            expected_req_probe_not_def = sum(1 for r in sys_records if "REQUIRED_PROBE_NOT_DEFINED" in r.get("mismatch_types", []))
            expected_exec_unavail = sum(1 for r in sys_records if "EXECUTION_UNAVAILABLE" in r.get("mismatch_types", []))
            expected_underclaim = sum(1 for r in sys_records if "CATALOG_UNDERCLAIM_FUNCTIONAL_PASS" in r.get("mismatch_types", []))
            if summary.get("required_probe_not_defined_count") != expected_req_probe_not_def:
                raise EvidenceValidationError(f"System {sys_id}: required_probe_not_defined_count mismatch")
            if summary.get("execution_unavailable_count") != expected_exec_unavail:
                raise EvidenceValidationError(f"System {sys_id}: execution_unavailable_count mismatch")
            if summary.get("catalog_underclaim_count") != expected_underclaim:
                raise EvidenceValidationError(f"System {sys_id}: catalog_underclaim_count mismatch")

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

    # 9. Determine version-aware profile and eligibility
    metrics_schema = derived_metrics_raw.get("schema_version", "")
    eval_schema = eval_records_raw[0].get("schema_version", "") if eval_records_raw else ""

    if "v1.3.0" in metrics_schema or "v1.3.0" in eval_schema:
        validation_profile = "CURRENT_V1_3"
        validator_status = "CURRENT_VALID"
        eligible_as_current_e5_evidence = (execution_status == EvidenceStatus.OBSERVED)
    elif "v1.2.0" in metrics_schema or "v1.2.0" in eval_schema:
        validation_profile = "LEGACY_SCHEMA_V1_2"
        validator_status = "LEGACY_VALID"
        eligible_as_current_e5_evidence = False
    elif "v1.1.0" in metrics_schema or "v1.1.0" in eval_schema:
        validation_profile = "LEGACY_SCHEMA_V1_1"
        validator_status = "LEGACY_VALID"
        eligible_as_current_e5_evidence = False
    elif "v1.0.0" in metrics_schema or "v1.0.0" in eval_schema:
        validation_profile = "LEGACY_SCHEMA_V1_0"
        validator_status = "LEGACY_VALID"
        eligible_as_current_e5_evidence = False
    else:
        validation_profile = "UNKNOWN"
        validator_status = "LEGACY_VALID"
        eligible_as_current_e5_evidence = False

    return {
        "status": "PASS",
        "validator_status": validator_status,
        "eligible_as_current_e5_evidence": eligible_as_current_e5_evidence,
        "validation_profile": validation_profile,
        "evidence_dir": str(directory),
        "experiment_id": "E5",
        "execution_status": execution_status.value,
        "total_probes_configured": len(manifest_probe_ids),
        "probes_executed": executed_probe_count,
        "probes_unavailable": unavailable_probe_count,
        "recommendations_evaluated": len(eval_records_raw),
        "files_checked": len(checked_files),
    }


def validate_e5_storage_evidence(package_dir: Path | str) -> dict[str, Any]:
    """Validate a sealed Protocol-v5 E5 image storage scalability evidence package fail-closed."""
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

    layers_path = raw_dir / "image_layers.json"
    env_path = raw_dir / "environment.json"
    storage_metrics_path = derived_dir / "storage_metrics.json"
    report_md_path = report_dir / "E5_IMAGE_STORAGE_REPORT.md"
    status_path = report_dir / "status.json"

    for req_file in (
        manifest_path,
        layers_path,
        env_path,
        storage_metrics_path,
        report_md_path,
        status_path,
    ):
        if not req_file.is_file():
            raise EvidenceValidationError(f"Required storage package file missing: {req_file.relative_to(directory)}")

    # 3. Validate storage_metrics.json schema and contract
    from evaluation_v5.analysis.research_contracts import validate_storage_evidence

    try:
        storage_data = json.loads(storage_metrics_path.read_text(encoding="utf-8"))
        validate_storage_evidence(storage_data)
    except Exception as exc:
        raise EvidenceValidationError(f"Invalid storage metrics in {directory}: {exc}") from exc

    execution_status = storage_data["execution_status"]
    split_stage = storage_data["split_stage"]
    claims_permitted = storage_data["claims_permitted"]
    prefixes = storage_data.get("prefixes", [])

    # Check non-expansion invariant
    for p in prefixes:
        if p["unique_layer_bytes"] > p["naive_logical_bytes"]:
            raise EvidenceValidationError(
                f"Prefix {p['prefix_size']} violates non-expansion: "
                f"unique={p['unique_layer_bytes']} > naive={p['naive_logical_bytes']}"
            )

    # 4. Validate manifest.json
    try:
        manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = ProtocolV5Manifest.from_dict(manifest_raw)
    except Exception as exc:
        raise EvidenceValidationError(f"Invalid manifest.json in {directory}: {exc}") from exc

    if manifest.experiment_id.value != "E5":
        raise EvidenceValidationError(f"Manifest experiment ID must be E5, got {manifest.experiment_id}")
    if manifest.execution_status.value != execution_status:
        raise EvidenceValidationError(
            f"Execution status mismatch: manifest has {manifest.execution_status.value} vs metrics {execution_status}"
        )

    # 5. Validate status.json
    status_raw = json.loads(status_path.read_text(encoding="utf-8"))
    if status_raw.get("status") != execution_status:
        raise EvidenceValidationError(
            f"Status mismatch: status.json has {status_raw.get('status')} vs metrics {execution_status}"
        )

    final_savings = (
        prefixes[-1]["naive_logical_bytes"] - prefixes[-1]["unique_layer_bytes"]
        if prefixes
        else 0
    )

    eligible = (execution_status == "OBSERVED" and split_stage == "confirmatory" and claims_permitted)

    return {
        "status": "PASS",
        "validator_status": "CURRENT_VALID",
        "eligible_as_current_e5_evidence": eligible,
        "validation_profile": "STORAGE_V1_0",
        "evidence_dir": str(directory),
        "experiment_id": "E5",
        "requirement_id": "image_storage",
        "execution_status": execution_status,
        "split_stage": split_stage,
        "claims_permitted": claims_permitted,
        "total_prefixes": len(prefixes),
        "final_storage_savings_bytes": final_savings,
        "files_checked": len(checked_files),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a Protocol-v5 E5 image functional or storage evidence package.")
    parser.add_argument("--dir", type=Path, required=True, help="Path to E5 evidence run directory.")
    parser.add_argument(
        "--type",
        choices=["auto", "functional", "storage"],
        default="auto",
        help="Evidence package type to validate (default: auto-detect).",
    )
    args = parser.parse_args()

    try:
        pkg_type = args.type
        if pkg_type == "auto":
            if (args.dir / "derived" / "storage_metrics.json").is_file() and not (args.dir / "derived" / "functional_metrics.json").is_file():
                pkg_type = "storage"
            else:
                pkg_type = "functional"

        if pkg_type == "storage":
            res = validate_e5_storage_evidence(args.dir)
        else:
            res = validate_e5_evidence(args.dir)

        print(json.dumps(res, indent=2))
    except Exception as exc:
        err = {
            "status": "FAIL",
            "validator_status": "INVALID",
            "eligible_as_current_e5_evidence": False,
            "validation_profile": "INVALID",
            "error": str(exc),
            "evidence_dir": str(args.dir),
        }
        print(json.dumps(err, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
