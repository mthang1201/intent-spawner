"""Evidence package validator for Protocol-v5 E5 image functional validation."""

from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any, Mapping

import yaml

from evaluation_v5.analysis.statistics import (
    DEFAULT_BOOTSTRAP_SEED,
    derive_bootstrap_seed,
    family_bootstrap_ci,
    inference_eligibility,
)
from evaluation_v5.schemas import EvidenceStatus, ProtocolV5Manifest
from evaluation_v5.validation import validate_manifest
from evaluation_v5.offline.source_run import SourceRunProvenanceError

from evaluation_v5.image_storage.contracts import (
    CapabilityProbeStatus,
    DimensionCStatus,
    FUNCTIONAL_EVALUATION_SCHEMA_VERSION,
    FUNCTIONAL_METRICS_SCHEMA_VERSION,
    IMAGE_PROBE_MANIFEST_SCHEMA_VERSION,
    IMAGE_PROBE_RECORD_SCHEMA_VERSION,
    ProbeExecutionOrigin,
    ProbeExecutionStatus,
    file_sha256,
    parse_image_digest,
)
from evaluation_v5.image_storage.functional_provenance import (
    SOURCE_RECOMMENDATIONS_FILENAME,
    SOURCE_RECOMMENDATION_PROVENANCE_FILENAME,
    canonical_identity_sha256,
    source_manifest_identities,
)
from evaluation_v5.image_storage.metrics import compute_functional_metrics, evaluate_recommendation_functional
from evaluation_v5.image_storage.storage_contracts import (
    ImageLayerMetadata,
    LEGACY_STORAGE_SCHEMA_VERSION,
    STORAGE_SCHEMA_VERSION,
    StorageCollectorOrigin,
    compute_marginal_storage,
    compute_pairwise_layer_reuse,
    is_real_storage_collector_origin,
)


class EvidenceValidationError(ValueError):
    """Raised when an evidence package violates Protocol-v5 E5 rules."""


def _is_sha256_digest(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def _is_sha256_hex(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value
    )


def _strict_json(raw: bytes, *, label: str) -> Any:
    """Reject duplicate fields and non-finite values at current trust boundaries."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise EvidenceValidationError(
                    f"{label} contains duplicate field {key!r}"
                )
            value[key] = item
        return value

    def constant(value: str) -> Any:
        raise EvidenceValidationError(
            f"{label} contains non-finite number {value}"
        )

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=constant,
        )
    except EvidenceValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceValidationError(f"{label} is not valid JSON") from exc


def _registry_manifest_from_raw(
    raw: bytes,
    *,
    image: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Select the platform manifest from one exact docker-manifest response."""

    parsed = _strict_json(raw, label=f"raw registry response for {image.get('image_id')}")
    entries = parsed if isinstance(parsed, list) else [parsed]
    expected_platform = image.get("platform") or {}
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        descriptor = item.get("Descriptor") or {}
        platform_data = descriptor.get("platform") or {}
        if (
            platform_data.get("architecture") != expected_platform.get("architecture")
            or platform_data.get("os") != expected_platform.get("os")
        ):
            continue
        manifest = item.get("OCIManifest") or item.get("SchemaV2Manifest")
        if manifest is None and item.get("Raw") is not None:
            try:
                manifest = _strict_json(
                    base64.b64decode(item["Raw"]),
                    label=f"embedded registry manifest for {image.get('image_id')}",
                )
            except Exception as exc:
                raise EvidenceValidationError(
                    f"Raw registry response for {image.get('image_id')} has invalid embedded manifest"
                ) from exc
        if isinstance(manifest, Mapping):
            return descriptor, manifest
    raise EvidenceValidationError(
        f"Raw registry response for {image.get('image_id')} lacks the recorded platform manifest"
    )


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
    source_provenance_path = raw_dir / SOURCE_RECOMMENDATION_PROVENANCE_FILENAME
    source_recommendations_path = raw_dir / SOURCE_RECOMMENDATIONS_FILENAME

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
    current_v14 = (
        probe_manifest_raw.get("schema_version")
        == IMAGE_PROBE_MANIFEST_SCHEMA_VERSION
    )
    if current_v14:
        for source_file in (source_provenance_path, source_recommendations_path):
            if not source_file.is_file():
                raise EvidenceValidationError(
                    f"Current E5 package is missing source provenance: {source_file.relative_to(directory)}"
                )
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
    seen_execution_identities: set[str] = set()
    executed_probe_count = 0
    unavailable_probe_count = 0
    functional_unavailable_probe_count = 0

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
        if current_v14 and res.get("image_digest") != cat_images[img_id].get("image_digest"):
            raise EvidenceValidationError(
                f"Probe {pid} selected digest does not match the approved probe manifest"
            )

        status = res.get("execution_status")
        success = res.get("success")
        err_cat = res.get("error_category")

        if current_v14:
            if res.get("schema_version") != IMAGE_PROBE_RECORD_SCHEMA_VERSION:
                raise EvidenceValidationError(
                    f"Probe {pid} does not use the current execution-bound record schema"
                )
            try:
                functional_status = CapabilityProbeStatus(res.get("functional_status"))
                origin = ProbeExecutionOrigin(res.get("execution_origin"))
            except (TypeError, ValueError) as exc:
                raise EvidenceValidationError(
                    f"Probe {pid} has invalid functional status or execution origin"
                ) from exc
            if not isinstance(success, bool) or success != (
                functional_status is CapabilityProbeStatus.SUCCESS
            ):
                raise EvidenceValidationError(
                    f"Probe {pid} success flag disagrees with functional_status"
                )
            if origin is ProbeExecutionOrigin.SYNTHETIC_TEST and execution_status == EvidenceStatus.OBSERVED:
                raise EvidenceValidationError(
                    f"Synthetic probe {pid} cannot appear in OBSERVED evidence"
                )
            expected_origins = {
                "docker": ProbeExecutionOrigin.LIVE_DOCKER,
                "kubernetes": ProbeExecutionOrigin.LIVE_KUBERNETES,
                "synthetic": ProbeExecutionOrigin.SYNTHETIC_TEST,
                "dry_run": ProbeExecutionOrigin.DRY_RUN,
            }
            if (
                origin is not ProbeExecutionOrigin.SYNTHETIC_TEST
                and expected_origins.get(res.get("execution_mode")) is not origin
            ):
                raise EvidenceValidationError(
                    f"Probe {pid} execution origin disagrees with execution mode"
                )
            if (
                res.get("capability") == "cuda-userspace"
                and status == ProbeExecutionStatus.EXECUTED.value
            ):
                metadata = res.get("import_version_metadata", {})
                if metadata.get("cuda_probe_status") != functional_status.value:
                    raise EvidenceValidationError(
                        f"CUDA probe {pid} metadata does not match its functional status"
                    )
                if functional_status is CapabilityProbeStatus.SUCCESS and (
                    not metadata.get("cuda_api")
                    or metadata.get("cuda_api") == "none"
                    or not metadata.get("cuda_library")
                    or metadata.get("cuda_library") == "none"
                ):
                    raise EvidenceValidationError(
                        f"CUDA probe {pid} claims success without a supported library/API observation"
                    )
            resolved_digest = res.get("resolved_image_digest")
            if (
                resolved_digest is not None
                and resolved_digest != res.get("image_digest")
            ):
                raise EvidenceValidationError(
                    f"Probe {pid} runtime digest disagrees with its approved image digest"
                )
            execution_identity = res.get("execution_identity")
            if execution_identity is not None:
                if (
                    not isinstance(execution_identity, str)
                    or not execution_identity
                    or execution_identity in seen_execution_identities
                ):
                    raise EvidenceValidationError(
                        f"Probe {pid} has a missing or duplicate execution identity"
                    )
                seen_execution_identities.add(execution_identity)
            if functional_status is CapabilityProbeStatus.UNAVAILABLE:
                functional_unavailable_probe_count += 1

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
        if current_v14:
            for res in probe_results_raw:
                if res.get("execution_origin") not in {
                    ProbeExecutionOrigin.LIVE_DOCKER.value,
                    ProbeExecutionOrigin.LIVE_KUBERNETES.value,
                }:
                    raise EvidenceValidationError(
                        "OBSERVED E5 requires live runtime probe origins"
                    )
                for field in (
                    "execution_identity",
                    "resolved_image_digest",
                    "resolved_image_platform",
                    "runtime_image_id",
                ):
                    if not res.get(field):
                        raise EvidenceValidationError(
                            f"OBSERVED probe {res['probe_id']} lacks {field}"
                        )
                if res.get("cleanup_succeeded") is not True:
                    raise EvidenceValidationError(
                        f"OBSERVED probe {res['probe_id']} lacks deterministic cleanup"
                    )
                if res["resolved_image_digest"] not in res["runtime_image_id"]:
                    raise EvidenceValidationError(
                        f"OBSERVED probe {res['probe_id']} runtime image ID does not bind its digest"
                    )

    # 6. Validate evaluations (Dimensions A, B, C and Mismatches)
    eval_records_raw = [
        json.loads(line) for line in evaluations_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    probe_results_map = {(res["image_id"], res["capability"]): res for res in probe_results_raw}
    cat_images_caps = {img_id: set(img_data.get("documented_capabilities", [])) for img_id, img_data in cat_images.items()}

    source_provenance: Mapping[str, Any] | None = None
    source_records_by_id: dict[str, Mapping[str, Any]] = {}
    if current_v14:
        source_provenance_raw = _strict_json(
            source_provenance_path.read_bytes(),
            label="source recommendation provenance",
        )
        if not isinstance(source_provenance_raw, Mapping):
            raise EvidenceValidationError("source recommendation provenance must be an object")
        source_provenance = source_provenance_raw
        if source_provenance.get("schema_version") != "protocol-v5-source-run-provenance-v1.1.0":
            raise EvidenceValidationError("current E5 requires Prompt 3 source provenance v1.1.0")
        source_sha = file_sha256(source_recommendations_path)
        if source_sha != source_provenance.get("recommendation_run_sha256"):
            raise EvidenceValidationError(
                "source recommendation snapshot checksum does not match source provenance"
            )
        source_bytes = source_recommendations_path.read_bytes()
        if not source_bytes or not source_bytes.endswith(b"\n"):
            raise EvidenceValidationError(
                "source recommendation snapshot must be non-empty and newline terminated"
            )
        source_rows = [
            _strict_json(line, label=f"source recommendation row {index}")
            for index, line in enumerate(source_bytes.splitlines(), start=1)
        ]
        source_artifacts = source_provenance.get("source_artifacts", {})
        if (
            not isinstance(source_artifacts, Mapping)
            or source_artifacts.get("recommendations_sha256") != source_sha
            or source_provenance.get("claims_permitted") is not False
        ):
            raise EvidenceValidationError(
                "source recommendation provenance does not bind its recommendation artifact"
            )
        source_candidates = {
            item.get("candidate_id"): item
            for item in source_provenance.get("catalog_identity", {}).get("candidates", [])
            if isinstance(item, Mapping)
        }
        source_image_ids = {
            item.get("image_id") for item in source_candidates.values()
        }
        if source_image_ids != set(cat_images) or source_provenance.get(
            "catalog_identity", {}
        ).get("catalog_version") != probe_manifest_raw.get("catalog_version"):
            raise EvidenceValidationError(
                "source recommendation catalog identity does not match the E5 probe manifest"
            )
        for source_row in source_rows:
            if not isinstance(source_row, Mapping):
                raise EvidenceValidationError(
                    "source recommendation rows must be objects"
                )
            record_id = source_row.get("record_id")
            if not isinstance(record_id, str) or not record_id or record_id in source_records_by_id:
                raise EvidenceValidationError("source recommendation record IDs must be unique and non-blank")
            source_records_by_id[record_id] = source_row
            if (
                source_row.get("run_id") != source_provenance.get("run_id")
                or source_row.get("provenance_fingerprint")
                != source_provenance.get("provenance_fingerprint")
                or source_row.get("system_id")
                not in source_provenance.get("systems", [])
            ):
                raise EvidenceValidationError(
                    "source recommendation record identity mismatches source provenance"
                )
            candidate_id = source_row.get("predicted_candidate_id")
            image_id = source_row.get("predicted_image_id")
            candidate = source_candidates.get(candidate_id)
            if candidate_id is None:
                if image_id is not None:
                    raise EvidenceValidationError(
                        "source recommendation image exists without a selected candidate"
                    )
            elif (
                not isinstance(candidate, Mapping)
                or candidate.get("image_id") != image_id
            ):
                raise EvidenceValidationError(
                    "source recommendation record/image mismatches its sealed catalog candidate"
                )
        if sorted(source_records_by_id) != sorted(source_provenance.get("record_ids", [])):
            raise EvidenceValidationError(
                "source recommendation record IDs do not match source provenance"
            )
        try:
            expected_manifest = source_manifest_identities(source_provenance)
        except (KeyError, TypeError, SourceRunProvenanceError) as exc:
            raise EvidenceValidationError(
                "source recommendation provenance is incomplete or malformed"
            ) from exc
        for field, expected in expected_manifest.items():
            if manifest_raw.get(field) != expected:
                raise EvidenceValidationError(
                    f"E5 manifest {field} does not derive from source recommendation provenance"
                )

        evaluation_ids = [
            rec.get("source_recommendation_record_id") for rec in eval_records_raw
        ]
        if (
            any(not isinstance(item, str) or not item for item in evaluation_ids)
            or len(set(evaluation_ids)) != len(evaluation_ids)
            or sorted(evaluation_ids) != sorted(source_records_by_id)
        ):
            raise EvidenceValidationError(
                "functional evaluations do not join one-to-one to source recommendation records"
            )
    elif any(
        rec.get("schema_version") == FUNCTIONAL_EVALUATION_SCHEMA_VERSION
        for rec in eval_records_raw
    ):
        raise EvidenceValidationError(
            "current functional records require the current source-bound probe manifest"
        )

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
        is_v14 = rec.get("schema_version") == FUNCTIONAL_EVALUATION_SCHEMA_VERSION

        if current_v14:
            if not is_v14 or source_provenance is None:
                raise EvidenceValidationError(
                    f"Case {case_id}: current probe evidence requires current functional records"
                )
            record_id = rec.get("source_recommendation_record_id")
            source_row = source_records_by_id[record_id]
            system_id = rec.get("system_id")
            system_identity = source_provenance["system_identities"].get(system_id)
            if not isinstance(system_identity, Mapping):
                raise EvidenceValidationError(
                    f"Case {case_id}: source system identity is missing"
                )
            exact_pairs = {
                "case_id": source_row.get("case_id"),
                "family_id": source_row.get("family_id"),
                "variant_id": source_row.get("variant_id"),
                "system_id": source_row.get("system_id"),
                "source_predicted_image_value": source_row.get("predicted_image_id"),
                "source_predicted_candidate_id": source_row.get("predicted_candidate_id"),
                "source_run_sha256": source_provenance["recommendation_run_sha256"],
                "source_configuration_identity_sha256": canonical_identity_sha256(system_identity),
            }
            for field, expected in exact_pairs.items():
                if rec.get(field) != expected:
                    raise EvidenceValidationError(
                        f"Case {case_id}: functional {field} mismatches source recommendation record"
                    )
            if pimg != source_row.get("predicted_image_id"):
                raise EvidenceValidationError(
                    f"Case {case_id}: functional image mismatches source recommendation image"
                )
            gold = source_row.get("evaluation_gold", {})
            required = tuple(
                sorted(
                    {
                        str(item).strip().lower()
                        for item in gold.get("required_image_capabilities", [])
                        if str(item).strip()
                    }
                )
            )
            if tuple(rec.get("required_capabilities", [])) != required:
                raise EvidenceValidationError(
                    f"Case {case_id}: required capabilities mismatch source recommendation gold"
                )
            preferred_candidate = gold.get("preferred_candidate_id")
            preferred = source_candidates.get(preferred_candidate)
            expected_preferred_image = (
                preferred.get("image_id") if isinstance(preferred, Mapping) else None
            )
            acceptable_images = sorted(
                {
                    source_candidates[candidate_id]["image_id"]
                    for candidate_id in gold.get("acceptable_candidate_ids", [])
                    if candidate_id in source_candidates
                }
            )
            if rec.get("gold_preferred_image_id") != expected_preferred_image or rec.get(
                "gold_acceptable_image_ids"
            ) != acceptable_images:
                raise EvidenceValidationError(
                    f"Case {case_id}: functional gold image mapping mismatches source recommendation record"
                )
            if pimg:
                selected = cat_images.get(pimg)
                if not isinstance(selected, Mapping):
                    raise EvidenceValidationError(
                        f"Case {case_id}: selected image is absent from the probe manifest"
                    )
                if rec.get("selected_image_digest") != selected.get("image_digest"):
                    raise EvidenceValidationError(
                        f"Case {case_id}: selected image digest mismatches the probe manifest"
                    )
                platforms = {
                    result.get("resolved_image_platform")
                    for result in probe_results_raw
                    if result.get("image_id") == pimg and result.get("resolved_image_platform")
                }
                expected_platform = next(iter(platforms), None) if len(platforms) <= 1 else None
                if len(platforms) > 1 or rec.get("selected_image_platform") != expected_platform:
                    raise EvidenceValidationError(
                        f"Case {case_id}: selected image platform mismatches probe observations"
                    )
                if execution_status == EvidenceStatus.OBSERVED and not expected_platform:
                    raise EvidenceValidationError(
                        f"Case {case_id}: OBSERVED recommendation lacks selected image platform"
                    )
            elif rec.get("selected_image_digest") is not None or rec.get("selected_image_platform") is not None:
                raise EvidenceValidationError(
                    f"Case {case_id}: no-image recommendation cannot carry image identity"
                )

        # Invariant: If predicted_image_id is None
        if not pimg:
            if (is_v12 or is_v13 or is_v14) and "NO_IMAGE_RECOMMENDATION" not in mismatches:
                raise EvidenceValidationError(
                    f"Case {case_id}: missing image recommendation must emit NO_IMAGE_RECOMMENDATION"
                )
            if (is_v12 or is_v13 or is_v14) and "EXECUTION_UNAVAILABLE" in mismatches:
                raise EvidenceValidationError(
                    f"Case {case_id}: missing image recommendation must NOT emit EXECUTION_UNAVAILABLE"
                )
            if dim_c_status not in (DimensionCStatus.NOT_EXECUTED.value, DimensionCStatus.NOT_APPLICABLE.value):
                raise EvidenceValidationError(
                    f"Case {case_id}: missing image recommendation must have status NOT_APPLICABLE or NOT_EXECUTED, got {dim_c_status}"
                )

        # Invariant: Dimension B strictly recomputable from catalog capabilities
        if (is_v13 or is_v14) and pimg:
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
        if "LABEL_FAIL_FUNCTIONAL_PASS" in mismatches and (is_v12 or is_v13 or is_v14):
            if not dim_b_sat:
                raise EvidenceValidationError(
                    f"Case {case_id}: LABEL_FAIL_FUNCTIONAL_PASS cannot be asserted when Dimension B is unsatisfied"
                )
            if dim_c_status != DimensionCStatus.PASS.value:
                raise EvidenceValidationError(
                    f"Case {case_id}: LABEL_FAIL_FUNCTIONAL_PASS requires Dimension C to be PASS, got {dim_c_status}"
                )

        # Invariants for v1.3 Dimension C decoupling and discrepancy taxonomy
        if (is_v13 or is_v14) and pimg:
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
                elif (
                    res.get("execution_status") != ProbeExecutionStatus.EXECUTED.value
                    or res.get("functional_status") == CapabilityProbeStatus.UNAVAILABLE.value
                ):
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
    is_metrics_v14 = derived_metrics_raw.get("schema_version") == FUNCTIONAL_METRICS_SCHEMA_VERSION
    is_metrics_v13 = derived_metrics_raw.get("schema_version") == "protocol-v5-image-functional-metrics-v1.3.0"
    is_metrics_v12 = derived_metrics_raw.get("schema_version") == "protocol-v5-image-functional-metrics-v1.2.0"
    if is_metrics_v14 is not current_v14:
        raise EvidenceValidationError(
            "current functional metrics and source-bound probe manifest must use the same schema generation"
        )

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

        elif is_metrics_v13 or is_metrics_v14:
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

    limitations: list[str] = []
    if "v1.4.0" in metrics_schema or "v1.4.0" in eval_schema:
        validation_profile = "CURRENT_V1_4_SEALED_RECOMMENDATION_PROVENANCE"
        validator_status = "CURRENT_VALID"
        eligible_as_current_e5_evidence = (execution_status == EvidenceStatus.OBSERVED)
    elif "v1.3.0" in metrics_schema or "v1.3.0" in eval_schema:
        validation_profile = "LEGACY_SCHEMA_V1_3"
        validator_status = "LEGACY_VALID"
        eligible_as_current_e5_evidence = False
        limitations.extend(
            [
                "UNSEALED_RECOMMENDATION_PROVENANCE",
                "MISSING_RECOMMENDATION_RECORD_JOIN",
                "MISSING_SELECTED_IMAGE_PLATFORM_BINDING",
            ]
        )
    elif "v1.2.0" in metrics_schema or "v1.2.0" in eval_schema:
        validation_profile = "LEGACY_SCHEMA_V1_2"
        validator_status = "LEGACY_VALID"
        eligible_as_current_e5_evidence = False
        limitations.append("UNSEALED_RECOMMENDATION_PROVENANCE")
    elif "v1.1.0" in metrics_schema or "v1.1.0" in eval_schema:
        validation_profile = "LEGACY_SCHEMA_V1_1"
        validator_status = "LEGACY_VALID"
        eligible_as_current_e5_evidence = False
        limitations.append("UNSEALED_RECOMMENDATION_PROVENANCE")
    elif "v1.0.0" in metrics_schema or "v1.0.0" in eval_schema:
        validation_profile = "LEGACY_SCHEMA_V1_0"
        validator_status = "LEGACY_VALID"
        eligible_as_current_e5_evidence = False
        limitations.append("UNSEALED_RECOMMENDATION_PROVENANCE")
    else:
        validation_profile = "UNKNOWN"
        validator_status = "LEGACY_VALID"
        eligible_as_current_e5_evidence = False
        limitations.append("UNSEALED_RECOMMENDATION_PROVENANCE")

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
        "probes_functionally_unavailable": functional_unavailable_probe_count,
        "recommendations_evaluated": len(eval_records_raw),
        "files_checked": len(checked_files),
        "limitations": limitations,
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
    figures_dir = directory / "figures"
    report_dir = directory / "report"

    layers_path = raw_dir / "image_layers.json"
    env_path = raw_dir / "environment.json"
    recommendations_path = raw_dir / "catalog_scale_recommendations.json"
    storage_metrics_path = derived_dir / "storage_metrics.json"
    marginal_path = derived_dir / "marginal_storage.json"
    pairwise_path = derived_dir / "pairwise_layer_reuse.json"
    scalability_path = derived_dir / "catalog_scalability.json"
    report_md_path = report_dir / "E5_IMAGE_STORAGE_REPORT.md"
    status_path = report_dir / "status.json"

    for req_file in (
        manifest_path,
        layers_path,
        env_path,
        storage_metrics_path,
        marginal_path,
        pairwise_path,
        scalability_path,
        report_md_path,
        status_path,
    ):
        if not req_file.is_file():
            raise EvidenceValidationError(f"Required storage package file missing: {req_file.relative_to(directory)}")

    # Check figures presence
    for fig_stem in (
        "figure_a_cumulative_storage",
        "figure_b_marginal_storage",
        "figure_c_pairwise_reuse_bytes",
        "figure_d_recommendation_quality",
        "figure_e_recommendation_latency",
    ):
        png_exists = (figures_dir / f"{fig_stem}.png").is_file()
        svg_exists = (figures_dir / f"{fig_stem}.svg").is_file()
        if not (png_exists or svg_exists):
            raise EvidenceValidationError(f"Required figure {fig_stem} missing in {figures_dir}")

    # 3. Validate storage_metrics.json schema and contract
    from evaluation_v5.analysis.research_contracts import validate_storage_evidence

    try:
        storage_data = _strict_json(
            storage_metrics_path.read_bytes(), label="storage_metrics.json"
        )
        if not isinstance(storage_data, Mapping):
            raise EvidenceValidationError("storage_metrics.json must be an object")
        validate_storage_evidence(storage_data)
    except Exception as exc:
        raise EvidenceValidationError(f"Invalid storage metrics in {directory}: {exc}") from exc

    execution_status = storage_data["execution_status"]
    split_stage = storage_data["split_stage"]
    claims_permitted = storage_data["claims_permitted"]
    prefixes = storage_data.get("prefixes", [])
    storage_schema_version = storage_data["schema_version"]
    current_storage_schema = storage_schema_version == STORAGE_SCHEMA_VERSION
    legacy_storage_schema = storage_schema_version == LEGACY_STORAGE_SCHEMA_VERSION
    if not (current_storage_schema or legacy_storage_schema):
        raise EvidenceValidationError("Unsupported storage evidence schema version")
    if current_storage_schema and not recommendations_path.is_file():
        raise EvidenceValidationError(
            "Current storage package is missing raw/catalog_scale_recommendations.json"
        )

    # Check non-expansion invariant
    for p in prefixes:
        if p["unique_layer_bytes"] > p["naive_logical_bytes"]:
            raise EvidenceValidationError(
                f"Prefix {p['prefix_size']} violates non-expansion: "
                f"unique={p['unique_layer_bytes']} > naive={p['naive_logical_bytes']}"
            )

    # 4. Validate marginal_storage.json
    try:
        marginal_data = _strict_json(
            marginal_path.read_bytes(), label="marginal_storage.json"
        )
    except Exception as exc:
        raise EvidenceValidationError(f"Malformed marginal_storage.json: {exc}") from exc

    if not isinstance(marginal_data, list) or len(marginal_data) != len(prefixes):
        raise EvidenceValidationError("marginal_storage.json row count must match prefix count")

    for idx, m in enumerate(marginal_data, start=1):
        if m["introduction_index"] != idx:
            raise EvidenceValidationError(f"Marginal index mismatch at row {idx}")
        if m["marginal_unique_bytes"] != m["new_unique_bytes"] - m["previous_unique_bytes"]:
            raise EvidenceValidationError(f"Marginal unique bytes calculation mismatch at index {idx}")
        if m["new_unique_bytes"] > m["cumulative_logical_bytes"]:
            raise EvidenceValidationError(f"Marginal record violates non-expansion at index {idx}")
        if m["size_domain"] not in ("compressed_oci_manifest_layer_bytes", "uncompressed_filesystem_layer_bytes"):
            raise EvidenceValidationError(f"Invalid size domain in marginal record: {m['size_domain']}")

    # 5. Validate pairwise_layer_reuse.json
    try:
        pairwise_data = _strict_json(
            pairwise_path.read_bytes(), label="pairwise_layer_reuse.json"
        )
    except Exception as exc:
        raise EvidenceValidationError(f"Malformed pairwise_layer_reuse.json: {exc}") from exc

    if not isinstance(pairwise_data, Mapping):
        raise EvidenceValidationError("pairwise_layer_reuse.json must be an object")
    c_mat = pairwise_data.get("shared_layer_count_matrix", [])
    b_mat = pairwise_data.get("shared_layer_byte_matrix", [])
    n_imgs = len(pairwise_data.get("image_ids", []))

    if len(c_mat) != n_imgs or any(len(row) != n_imgs for row in c_mat):
        raise EvidenceValidationError("Count matrix dimensions mismatch image count")
    if len(b_mat) != n_imgs or any(len(row) != n_imgs for row in b_mat):
        raise EvidenceValidationError("Byte matrix dimensions mismatch image count")

    # Check symmetry and diagonal
    for i in range(n_imgs):
        for j in range(n_imgs):
            if c_mat[i][j] != c_mat[j][i]:
                raise EvidenceValidationError(f"Count matrix asymmetry at ({i},{j})")
            if b_mat[i][j] != b_mat[j][i]:
                raise EvidenceValidationError(f"Byte matrix asymmetry at ({i},{j})")

    # 6. Validate catalog_scalability.json (rejection of fabricated scales)
    try:
        scale_data = _strict_json(
            scalability_path.read_bytes(), label="catalog_scalability.json"
        )
    except Exception as exc:
        raise EvidenceValidationError(f"Malformed catalog_scalability.json: {exc}") from exc

    if not isinstance(scale_data, list) or not scale_data:
        raise EvidenceValidationError("catalog_scalability.json must be a non-empty list of scale records")
    configured_scale_sizes = [int(row["catalog_size"]) for row in scale_data]
    if configured_scale_sizes != sorted(set(configured_scale_sizes)):
        raise EvidenceValidationError(
            "Catalog scale records must have unique increasing sizes"
        )

    for s in scale_data:
        scale_sz = int(s["catalog_size"])
        approved_refs = s.get("ordered_immutable_image_references", [])
        st_status = s.get("storage_measurement_status")

        # Reject fabricated data if approved images are fewer than scale
        if len(approved_refs) < scale_sz and st_status == "OBSERVED":
            raise EvidenceValidationError(
                f"Fabricated scale observation rejected: scale {scale_sz} marked OBSERVED "
                f"with only {len(approved_refs)} approved image(s)"
            )
        if s.get("size_domain") not in ("compressed_oci_manifest_layer_bytes", "uncompressed_filesystem_layer_bytes"):
            raise EvidenceValidationError(f"Invalid size domain in scale record: {s.get('size_domain')}")

    # 7. Validate raw image layers, collector provenance, and immutable catalog gate
    layers_data = _strict_json(layers_path.read_bytes(), label="image_layers.json")
    env_data = _strict_json(env_path.read_bytes(), label="environment.json")
    if not isinstance(layers_data, list) or not layers_data:
        raise EvidenceValidationError("image_layers.json must be a non-empty list")
    if not isinstance(env_data, Mapping):
        raise EvidenceValidationError("environment.json must be an object")

    collector_authentic = False
    collector_origin = "LEGACY_UNRECORDED"
    collector: Mapping[str, Any] = {}
    if current_storage_schema:
        collector = storage_data.get("collector") or {}
        if not isinstance(collector, Mapping):
            raise EvidenceValidationError("Current storage evidence collector must be an object")
        try:
            origin_enum = StorageCollectorOrigin(collector.get("origin"))
        except (TypeError, ValueError) as exc:
            raise EvidenceValidationError("Current storage evidence has invalid collector origin") from exc
        collector_origin = origin_enum.value
        expected_collector_names = {
            StorageCollectorOrigin.REAL_REGISTRY: "docker-manifest-inspect",
            StorageCollectorOrigin.CONTAINER_STORAGE_OBSERVATION: "container-storage-observation",
            StorageCollectorOrigin.SYNTHETIC_TEST: "synthetic-storage-test-runner",
            StorageCollectorOrigin.DRY_RUN: "dry-run-storage-runner",
        }
        if (
            collector.get("collector_name") != expected_collector_names[origin_enum]
            or collector.get("collector_version") != "storage-collector-v1.0.0"
        ):
            raise EvidenceValidationError(
                "Storage collector identity is not a supported fixed implementation"
            )
        if storage_data.get("provenance", {}).get("storage_collector_origin") != collector_origin:
            raise EvidenceValidationError("Storage provenance lost its collector origin")
        if env_data.get("storage_collector") != collector:
            raise EvidenceValidationError("Environment provenance disagrees with storage collector")
        expected_runtime = {
            StorageCollectorOrigin.REAL_REGISTRY: "docker",
            StorageCollectorOrigin.CONTAINER_STORAGE_OBSERVATION: "container_storage",
            StorageCollectorOrigin.SYNTHETIC_TEST: "synthetic_test",
            StorageCollectorOrigin.DRY_RUN: "dry_run",
        }[origin_enum]
        if storage_data.get("platform", {}).get("runtime") != expected_runtime or env_data.get("runtime") != expected_runtime:
            raise EvidenceValidationError(
                "Caller runtime label disagrees with authenticated storage collector origin"
            )
        real_origin = is_real_storage_collector_origin(origin_enum)
        if real_origin != (execution_status == "OBSERVED"):
            raise EvidenceValidationError(
                "Storage execution status is not derivable from collector origin"
            )
        if not real_origin and claims_permitted:
            raise EvidenceValidationError(
                "Synthetic or dry-run storage evidence cannot permit claims"
            )

    immutable_catalog_valid = True
    reconstructed_images: list[ImageLayerMetadata] = []
    for img in layers_data:
        if not isinstance(img, Mapping):
            raise EvidenceValidationError("image_layers.json rows must be objects")
        if not img.get("image_digest"):
            raise EvidenceValidationError(f"Image {img.get('image_id')} missing image_digest")
        if "is_digest_pinned" not in img:
            raise EvidenceValidationError(f"Image {img.get('image_id')} missing is_digest_pinned")
        if not img.get("ordered_layer_digests") and img.get("layers"):
            raise EvidenceValidationError(f"Image {img.get('image_id')} missing ordered_layer_digests")

        raw_layers = img.get("layers") or []
        if not isinstance(raw_layers, list) or any(
            not isinstance(item, Mapping) for item in raw_layers
        ):
            raise EvidenceValidationError(
                f"Image {img.get('image_id')} layers must be a list of objects"
            )
        if img.get("ordered_layer_digests") != [item.get("digest") for item in raw_layers]:
            raise EvidenceValidationError(
                f"Image {img.get('image_id')} ordered layer digests disagree with raw descriptors"
            )
        if img.get("layer_sizes") != [item.get("size") for item in raw_layers]:
            raise EvidenceValidationError(
                f"Image {img.get('image_id')} layer sizes disagree with raw descriptors"
            )
        if any(
            not _is_sha256_digest(item.get("digest"))
            or isinstance(item.get("size"), bool)
            or not isinstance(item.get("size"), int)
            or item["size"] < 0
            for item in raw_layers
        ):
            raise EvidenceValidationError(
                f"Image {img.get('image_id')} contains an invalid layer digest or byte value"
            )
        if img.get("total_bytes") != sum(int(item["size"]) for item in raw_layers):
            raise EvidenceValidationError(
                f"Image {img.get('image_id')} total bytes disagree with layer descriptors"
            )
        try:
            reconstructed_images.append(ImageLayerMetadata.from_dict(img))
        except Exception as exc:
            raise EvidenceValidationError(
                f"Image {img.get('image_id')} cannot be reconstructed from raw descriptors"
            ) from exc

        if current_storage_schema:
            for field in ("collector_origin", "collector_name", "collector_version"):
                expected = {
                    "collector_origin": collector_origin,
                    "collector_name": collector.get("collector_name"),
                    "collector_version": collector.get("collector_version"),
                }[field]
                if img.get(field) != expected:
                    raise EvidenceValidationError(
                        f"Image {img.get('image_id')} lost collector provenance field {field}"
                    )

            if is_real_storage_collector_origin(collector_origin):
                for field in (
                    "resolved_digest",
                    "manifest_digest",
                    "config_digest",
                    "raw_observation_path",
                    "raw_observation_sha256",
                ):
                    if not img.get(field):
                        raise EvidenceValidationError(
                            f"OBSERVED image {img.get('image_id')} lacks {field}"
                        )
                if not raw_layers or not img.get("platform"):
                    raise EvidenceValidationError(
                        f"OBSERVED image {img.get('image_id')} lacks platform or layers"
                    )
                for field in ("resolved_digest", "manifest_digest", "config_digest"):
                    if not _is_sha256_digest(img.get(field)):
                        raise EvidenceValidationError(
                            f"OBSERVED image {img.get('image_id')} has invalid {field}"
                        )
                relative = Path(str(img["raw_observation_path"]))
                if relative.is_absolute() or ".." in relative.parts:
                    raise EvidenceValidationError("Raw storage observation path escapes package")
                raw_path = raw_dir / relative
                if not raw_path.is_file() or raw_path not in checked_files:
                    raise EvidenceValidationError(
                        f"OBSERVED image {img.get('image_id')} lacks sealed raw registry evidence"
                    )
                raw_bytes = raw_path.read_bytes()
                if hashlib.sha256(raw_bytes).hexdigest() != img["raw_observation_sha256"]:
                    raise EvidenceValidationError(
                        f"OBSERVED image {img.get('image_id')} raw observation checksum mismatch"
                    )
                if collector_origin == StorageCollectorOrigin.REAL_REGISTRY.value:
                    descriptor, raw_manifest = _registry_manifest_from_raw(
                        raw_bytes, image=img
                    )
                    raw_config = raw_manifest.get("config") or {}
                    raw_layers_selected = raw_manifest.get("layers") or []
                    if (
                        descriptor.get("digest", img["resolved_digest"])
                        != img["manifest_digest"]
                        or raw_config.get("digest") != img["config_digest"]
                        or [item.get("digest") for item in raw_layers_selected]
                        != img["ordered_layer_digests"]
                        or [item.get("size") for item in raw_layers_selected]
                        != img["layer_sizes"]
                    ):
                        raise EvidenceValidationError(
                            f"OBSERVED image {img.get('image_id')} metadata is not derived from its raw registry response"
                        )
            elif img.get("raw_observation_path") or img.get("raw_observation_sha256"):
                raise EvidenceValidationError(
                    f"Non-observed image {img.get('image_id')} cannot claim raw registry evidence"
                )

        # Check immutable requested reference gate
        req_ref = str(img.get("requested_reference") or img.get("image_reference", ""))
        pinned = bool(img.get("is_digest_pinned", "@sha256:" in req_ref))
        if pinned and parse_image_digest(req_ref) != img.get("image_digest"):
            raise EvidenceValidationError(
                f"Image {img.get('image_id')} requested reference does not bind its recorded digest"
            )
        if not pinned or "@sha256:" not in req_ref:
            if split_stage == "confirmatory":
                raise EvidenceValidationError(
                    f"MUTABLE_INPUT_REFERENCE_IN_CONFIRMATORY_CATALOG: Image {img.get('image_id')} "
                    f"requested reference {req_ref!r} is not digest-pinned"
                )
            immutable_catalog_valid = False

    if current_storage_schema:
        ordered_image_digests = [item.image_digest for item in reconstructed_images]
        if ordered_image_digests != storage_data.get("catalog", {}).get(
            "ordered_image_digests"
        ):
            raise EvidenceValidationError(
                "Raw image order/digests disagree with storage catalog identity"
            )
        layer_sizes_by_digest: dict[str, int] = {}
        cumulative_logical = 0
        expected_prefixes: list[dict[str, Any]] = []
        for index, image in enumerate(reconstructed_images, start=1):
            cumulative_logical += image.total_bytes
            for layer in image.layers:
                prior_size = layer_sizes_by_digest.setdefault(layer.digest, layer.size)
                if prior_size != layer.size:
                    raise EvidenceValidationError(
                        f"Layer {layer.digest} has inconsistent compressed bytes across images"
                    )
            expected_prefixes.append(
                {
                    "prefix_size": index,
                    "image_digests": ordered_image_digests[:index],
                    "naive_logical_bytes": cumulative_logical,
                    "unique_layer_bytes": sum(layer_sizes_by_digest.values()),
                }
            )
        if prefixes != expected_prefixes:
            raise EvidenceValidationError(
                "Derived storage prefixes are not recomputable from raw layer descriptors"
            )

        expected_marginal = [
            row.to_dict() for row in compute_marginal_storage(reconstructed_images)
        ]
        if marginal_data != expected_marginal:
            raise EvidenceValidationError(
                "Derived marginal storage is not recomputable from raw layer descriptors"
            )
        expected_pairwise = compute_pairwise_layer_reuse(
            reconstructed_images
        ).to_dict()
        if pairwise_data != expected_pairwise:
            raise EvidenceValidationError(
                "Derived pairwise reuse is not recomputable from raw layer descriptors"
            )

        for scale in scale_data:
            size = int(scale["catalog_size"])
            available = len(scale.get("ordered_requested_references") or []) >= size
            selected_images = reconstructed_images[:size]
            identity_fields = {
                "ordered_immutable_image_references": [
                    image.requested_reference for image in selected_images
                ],
                "ordered_requested_references": [
                    image.requested_reference for image in selected_images
                ],
                "canonical_resolved_references": [
                    image.canonical_resolved_reference for image in selected_images
                ],
                "all_references_digest_pinned": all(
                    image.is_digest_pinned for image in selected_images
                ),
            }
            if any(scale.get(field) != value for field, value in identity_fields.items()):
                raise EvidenceValidationError(
                    f"Scale {size} catalog identity is not the ordered raw image prefix"
                )
            if available and size <= len(expected_prefixes):
                prefix = expected_prefixes[size - 1]
                marginal = expected_marginal[size - 1]
                saving_bytes = prefix["naive_logical_bytes"] - prefix["unique_layer_bytes"]
                expected_values = {
                    "storage_measurement_status": execution_status,
                    "logical_image_bytes": prefix["naive_logical_bytes"],
                    "unique_layer_bytes": prefix["unique_layer_bytes"],
                    "dedup_saving_bytes": saving_bytes,
                    "marginal_unique_bytes": marginal["marginal_unique_bytes"],
                }
                expected_ratio = (
                    saving_bytes / prefix["naive_logical_bytes"]
                    if prefix["naive_logical_bytes"]
                    else 0.0
                )
            else:
                expected_values = {
                    "storage_measurement_status": "NOT_EXECUTED",
                    "logical_image_bytes": None,
                    "unique_layer_bytes": None,
                    "dedup_saving_bytes": None,
                    "marginal_unique_bytes": None,
                }
                expected_ratio = None
            if any(scale.get(field) != value for field, value in expected_values.items()):
                raise EvidenceValidationError(
                    f"Scale {size} storage metrics are not derived from raw layer descriptors"
                )
            observed_ratio = scale.get("dedup_saving_ratio")
            if (
                (expected_ratio is None and observed_ratio is not None)
                or (
                    expected_ratio is not None
                    and (
                        observed_ratio is None
                        or not math.isclose(
                            float(observed_ratio),
                            expected_ratio,
                            rel_tol=0,
                            abs_tol=1e-6,
                        )
                    )
                )
            ):
                raise EvidenceValidationError(
                    f"Scale {size} storage ratio is not derived from raw layer descriptors"
                )

    if current_storage_schema and is_real_storage_collector_origin(collector_origin):
        if int(collector.get("raw_observation_count", -1)) != len(layers_data):
            raise EvidenceValidationError(
                "Collector raw observation count does not match inspected images"
            )
        collector_authentic = True

    if current_storage_schema:
        derived_claims_permitted = bool(
            execution_status == "OBSERVED"
            and split_stage == "confirmatory"
            and collector_authentic
            and immutable_catalog_valid
        )
        if claims_permitted is not derived_claims_permitted:
            raise EvidenceValidationError(
                "claims_permitted is not derived from validated storage evidence eligibility"
            )

    # Check single-image prefix invariant and within-image duplicate descriptor accounting
    if prefixes and layers_data:
        p1 = prefixes[0]
        img1 = layers_data[0]
        img1_layers = img1.get("layers", [])
        counts1 = Counter(l["digest"] for l in img1_layers)
        sizes1 = {l["digest"]: int(l["size"]) for l in img1_layers}
        expected_dup_bytes = sum((c - 1) * sizes1[d] for d, c in counts1.items() if c > 1)
        all_unique = all(c == 1 for c in counts1.values())

        p1_logical = p1["naive_logical_bytes"]
        p1_unique = p1["unique_layer_bytes"]
        p1_diff = p1_logical - p1_unique
        p1_savings = p1.get("savings_bytes", p1_diff)

        if all_unique:
            if p1_diff != 0:
                raise EvidenceValidationError(
                    f"SINGLE_IMAGE_DEDUPLICATION_MISMATCH: Single image prefix has all unique layer digests "
                    f"but LogicalImageBytes ({p1_logical}) != UniqueLayerBytes ({p1_unique})"
                )
            if p1_savings != 0:
                raise EvidenceValidationError(
                    f"SINGLE_IMAGE_DEDUPLICATION_MISMATCH: Single image prefix has all unique layer digests "
                    f"but savings_bytes={p1_savings} != 0"
                )
        else:
            if p1_diff != expected_dup_bytes:
                raise EvidenceValidationError(
                    f"UNEXPLAINED_SINGLE_IMAGE_DEDUPLICATION_RESIDUAL: Prefix 1 difference "
                    f"({p1_diff} B) does not match expected duplicate descriptor bytes ({expected_dup_bytes} B). "
                    f"Unexplained residual: {p1_diff - expected_dup_bytes} B."
                )
            if p1_savings != expected_dup_bytes:
                raise EvidenceValidationError(
                    f"Prefix 1 savings ({p1_savings} B) does not match duplicate descriptor bytes ({expected_dup_bytes} B)"
                )

    # 8. Validate recommendation split integrity, fail-closed metrics, and provenance
    DEV_DATASET_IDENTITIES = {"protocol-v5-development-2026-08-22", "v5-development"}
    DEV_DATASET_SHAS = {
        "e3fff5167ef2194fb365fec7510d1efc2b17a18b13182063c2b63c26f021d3cd",
        "18894b73ec98d895348498bf6b1c4dd4d2dc6004437202bd8b93c17d09b0dc0b",
    }
    recommendation_split_valid = True
    for s in scale_data:
        p2_status = s.get("p2_evaluation_status")
        rec_stage = s.get("split_stage", split_stage)
        rec_role = s.get("split_role", "none")
        ds_id = s.get("evaluation_dataset_identity", "")
        ds_sha = s.get("dataset_sha256", "")

        # Fail-closed check: NOT_EXECUTED records MUST NOT carry observed metrics
        if p2_status != "OBSERVED":
            forbidden_metrics = [
                ("p2_image_acceptable_accuracy", s.get("p2_image_acceptable_accuracy")),
                ("p2_image_preferred_accuracy", s.get("p2_image_preferred_accuracy")),
                ("p2_retrieval_recall_at_k", s.get("p2_retrieval_recall_at_k")),
                ("p2_latency_mean_seconds", s.get("p2_latency_mean_seconds")),
                ("p2_latency_p95_seconds", s.get("p2_latency_p95_seconds")),
                ("p2_latency_min_seconds", s.get("p2_latency_min_seconds")),
                ("p2_latency_max_seconds", s.get("p2_latency_max_seconds")),
                ("p2_latency_median_seconds", s.get("p2_latency_median_seconds")),
                ("p2_latency_std_seconds", s.get("p2_latency_std_seconds")),
            ]
            for m_name, m_val in forbidden_metrics:
                if m_val is not None:
                    raise EvidenceValidationError(
                        f"NOT_EXECUTED_RECORD_CONTAINS_OBSERVED_METRICS: Scale {s.get('catalog_size')} "
                        f"has p2_evaluation_status={p2_status!r} but contains observed {m_name}={m_val}"
                    )
            if s.get("evaluated_case_count", 0) != 0:
                raise EvidenceValidationError(
                    f"NOT_EXECUTED_RECORD_CONTAINS_OBSERVED_METRICS: Scale {s.get('catalog_size')} "
                    f"has p2_evaluation_status={p2_status!r} but evaluated_case_count={s.get('evaluated_case_count')}"
                )

        if p2_status == "OBSERVED":
            if split_stage == "confirmatory" or rec_stage == "confirmatory":
                if rec_role == "development" or ds_id in DEV_DATASET_IDENTITIES or ds_sha in DEV_DATASET_SHAS:
                    recommendation_split_valid = False
                    raise EvidenceValidationError(
                        f"CONFIRMATORY_RECOMMENDATION_USES_DEVELOPMENT_SPLIT: Scale {s.get('catalog_size')} "
                        f"claims confirmatory recommendation but uses development dataset {ds_id} (SHA: {ds_sha})"
                    )

    if current_storage_schema:
        if recommendations_path not in checked_files:
            raise EvidenceValidationError(
                "Current raw catalog-scale recommendations are not sealed by SHA256SUMS"
            )
        recommendation_payload = _strict_json(
            recommendations_path.read_bytes(),
            label="catalog_scale_recommendations.json",
        )
        if (
            not isinstance(recommendation_payload, Mapping)
            or recommendation_payload.get("schema_version")
            != "protocol-v5-catalog-scale-recommendations-v1.0.0"
            or recommendation_payload.get("collector_origin") != collector_origin
            or not isinstance(recommendation_payload.get("scale_evaluations"), list)
        ):
            raise EvidenceValidationError(
                "catalog_scale_recommendations.json has an invalid collector-bound envelope"
            )
        recommendation_runs = recommendation_payload["scale_evaluations"]
        by_size: dict[int, Mapping[str, Any]] = {}
        for run in recommendation_runs:
            if not isinstance(run, Mapping):
                raise EvidenceValidationError(
                    "catalog-scale recommendation runs must be objects"
                )
            try:
                size = int(run["catalog_size"])
            except (KeyError, TypeError, ValueError) as exc:
                raise EvidenceValidationError(
                    "catalog-scale recommendation run has invalid catalog_size"
                ) from exc
            if size in by_size:
                raise EvidenceValidationError(
                    f"Duplicate raw catalog-scale recommendation run for scale {size}"
                )
            by_size[size] = run
        if set(by_size) != {int(row["catalog_size"]) for row in scale_data}:
            raise EvidenceValidationError(
                "Raw and derived catalog-scale recommendation runs disagree"
            )

        metric_pairs = {
            "image_acceptable": "p2_image_acceptable_accuracy",
            "image_preferred": "p2_image_preferred_accuracy",
            "retrieval_recall_at_k": "p2_retrieval_recall_at_k",
            "latency_seconds": "p2_latency_mean_seconds",
        }
        for scale in scale_data:
            size = int(scale["catalog_size"])
            run = by_size[size]
            exact_fields = {
                "catalog_id": scale.get("catalog_id"),
                "status": scale.get("p2_evaluation_status"),
                "split_stage": scale.get("split_stage"),
                "split_role": scale.get("split_role"),
                "dataset_id": scale.get("evaluation_dataset_identity"),
                "dataset_sha256": scale.get("dataset_sha256"),
            }
            for field, expected in exact_fields.items():
                if run.get(field) != expected:
                    raise EvidenceValidationError(
                        f"Scale {size} raw recommendation field {field} disagrees with derived metrics"
                    )
            if run.get("aggregation_unit") != "workload_family":
                raise EvidenceValidationError(
                    f"Scale {size} recommendation aggregation unit is not workload_family"
                )
            provenance = scale.get("provenance") or {}
            records = run.get("case_records") or []
            families = run.get("family_estimates") or []
            if (
                not isinstance(provenance, Mapping)
                or provenance.get("storage_collector_origin") != collector_origin
                or provenance.get("recommendation_aggregation_unit") != "workload_family"
                or provenance.get("raw_recommendation_record_count") != len(records)
            ):
                raise EvidenceValidationError(
                    f"Scale {size} recommendation provenance is incomplete or inconsistent"
                )
            if run.get("status") != "OBSERVED":
                if records or families or run.get("family_summary"):
                    raise EvidenceValidationError(
                        f"NOT_EXECUTED scale {size} contains raw recommendation observations"
                    )
                continue
            if not isinstance(records, list) or not records:
                raise EvidenceValidationError(
                    f"OBSERVED scale {size} lacks raw per-case recommendation records"
                )
            if not isinstance(families, list) or not families:
                raise EvidenceValidationError(
                    f"OBSERVED scale {size} lacks family-level recommendation estimates"
                )
            if len(records) != scale.get("evaluated_case_count"):
                raise EvidenceValidationError(
                    f"Scale {size} evaluated case count disagrees with raw records"
                )

            records_by_family: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            seen_cases: set[str] = set()
            feasible_cases = 0
            source_corpus_identities: set[tuple[str, str, str, str]] = set()
            for record in records:
                if not isinstance(record, Mapping):
                    raise EvidenceValidationError(
                        f"Scale {size} recommendation record must be an object"
                    )
                case_id = record.get("case_id")
                family_id = record.get("family_id")
                variant_id = record.get("variant_id")
                if (
                    not isinstance(case_id, str)
                    or not case_id
                    or case_id in seen_cases
                    or not isinstance(family_id, str)
                    or not family_id
                    or not isinstance(variant_id, str)
                    or not variant_id
                    or record.get("catalog_size") != size
                ):
                    raise EvidenceValidationError(
                        f"Scale {size} has missing or duplicate case/family/variant identity"
                    )
                seen_cases.add(case_id)
                records_by_family[family_id].append(record)
                source_identity = record.get("source_identity") or {}
                predicted_ids = (
                    record.get("predicted_candidate_id"),
                    record.get("predicted_profile_id"),
                    record.get("predicted_image_id"),
                )
                if (
                    record.get("schema_version")
                    != "protocol-v5-catalog-scale-recommendation-record-v1.0.0"
                    or not isinstance(source_identity, Mapping)
                    or source_identity.get("dataset_id") != run.get("dataset_id")
                    or source_identity.get("dataset_sha256") != run.get("dataset_sha256")
                    or not source_identity.get("candidate_corpus_version")
                    or not source_identity.get("candidate_corpus_sha256")
                    or not source_identity.get("image_catalog_version")
                    or not source_identity.get("p2_config_version")
                    or source_identity.get("p2_config_version")
                    != scale.get("p2_config_version")
                    or any(
                        not isinstance(value, str) or not value
                        for value in predicted_ids
                    )
                    or not _is_sha256_hex(source_identity.get("dataset_sha256"))
                    or not _is_sha256_hex(
                        source_identity.get("source_provenance_sha256")
                    )
                    or not _is_sha256_hex(
                        source_identity.get("candidate_corpus_sha256")
                    )
                ):
                    raise EvidenceValidationError(
                        f"Scale {size} case {case_id} has incomplete source identity"
                    )
                source_corpus_identities.add(
                    (
                        str(source_identity["candidate_corpus_version"]),
                        str(source_identity["candidate_corpus_sha256"]),
                        str(source_identity["image_catalog_version"]),
                        str(source_identity["p2_config_version"]),
                    )
                )
                if (
                    run.get("split_schema_version")
                    == "protocol-v5-split-bundle-v2.0.0"
                    and record.get("gold_schema") != "canonical_v2"
                ):
                    raise EvidenceValidationError(
                        f"Scale {size} case {case_id} did not use canonical-v2 gold"
                    )
                top_k = record.get("retrieval_top_k")
                recall_k = record.get("recall_k")
                if (
                    not isinstance(top_k, list)
                    or isinstance(recall_k, bool)
                    or not isinstance(recall_k, int)
                    or recall_k < 1
                    or len(top_k) > recall_k
                ):
                    raise EvidenceValidationError(
                        f"Scale {size} case {case_id} has invalid top-K retrieval evidence"
                    )
                for rank, hit in enumerate(top_k, start=1):
                    if (
                        not isinstance(hit, Mapping)
                        or not hit.get("candidate_id")
                        or hit.get("rank") not in (None, rank)
                    ):
                        raise EvidenceValidationError(
                            f"Scale {size} case {case_id} has invalid ranked retrieval hit"
                        )
                expected = record.get("expected_feasibility")
                if expected == "feasible":
                    feasible_cases += 1
                    for field in (
                        "candidate_acceptable",
                        "candidate_preferred",
                        "profile_acceptable",
                        "profile_preferred",
                        "image_acceptable",
                        "image_preferred",
                    ):
                        if not isinstance(record.get(field), bool):
                            raise EvidenceValidationError(
                                f"Scale {size} case {case_id} lacks boolean {field}"
                            )
                    recall = record.get("retrieval_recall_at_k")
                    if (
                        isinstance(recall, bool)
                        or not isinstance(recall, (int, float))
                        or not math.isfinite(float(recall))
                        or not 0 <= float(recall) <= 1
                    ):
                        raise EvidenceValidationError(
                            f"Scale {size} case {case_id} has invalid retrieval recall"
                        )
                elif expected in {"infeasible", "ambiguous"}:
                    if any(
                        record.get(field) is not None
                        for field in (
                            "candidate_acceptable",
                            "candidate_preferred",
                            "profile_acceptable",
                            "profile_preferred",
                            "image_acceptable",
                            "image_preferred",
                            "retrieval_recall_at_k",
                        )
                    ):
                        raise EvidenceValidationError(
                            f"Scale {size} non-feasible case {case_id} contains scored outcomes"
                        )
                else:
                    raise EvidenceValidationError(
                        f"Scale {size} case {case_id} has invalid feasibility label"
                    )
                latency = record.get("latency_seconds")
                if (
                    isinstance(latency, bool)
                    or not isinstance(latency, (int, float))
                    or not math.isfinite(float(latency))
                    or latency < 0
                ):
                    raise EvidenceValidationError(
                        f"Scale {size} case {case_id} has invalid latency"
                    )
            if feasible_cases != scale.get("feasible_case_count"):
                raise EvidenceValidationError(
                    f"Scale {size} feasible case count disagrees with raw records"
                )
            if len(source_corpus_identities) != 1:
                raise EvidenceValidationError(
                    f"Scale {size} recommendation cases disagree on frozen source identity"
                )

            family_by_id: dict[str, Mapping[str, Any]] = {}
            for family in families:
                if not isinstance(family, Mapping):
                    raise EvidenceValidationError(
                        f"Scale {size} family estimate must be an object"
                    )
                family_id = family.get("family_id")
                if not isinstance(family_id, str) or not family_id or family_id in family_by_id:
                    raise EvidenceValidationError(
                        f"Scale {size} family IDs must be unique and non-blank"
                    )
                family_by_id[family_id] = family
                source_records = records_by_family.get(family_id, [])
                if (
                    family.get("schema_version")
                    != "protocol-v5-catalog-scale-family-estimate-v1.0.0"
                    or family.get("catalog_size") != size
                    or family.get("aggregation_unit") != "workload_family"
                    or family.get("variant_count") != len(source_records)
                    or sorted(family.get("case_ids") or [])
                    != sorted(str(item["case_id"]) for item in source_records)
                ):
                    raise EvidenceValidationError(
                        f"Scale {size} family {family_id} is not derived from its cases"
                    )
                values = family.get("values") or {}
                denominators = family.get("endpoint_variant_denominators") or {}
                for metric in metric_pairs:
                    selected = [
                        float(item[metric])
                        for item in source_records
                        if item.get(metric) is not None
                    ]
                    expected_value = statistics.fmean(selected) if selected else None
                    if denominators.get(metric) != len(selected) or values.get(metric) != expected_value:
                        raise EvidenceValidationError(
                            f"Scale {size} family {family_id} {metric} is not an equal-weight variant mean"
                        )
            if set(family_by_id) != set(records_by_family):
                raise EvidenceValidationError(
                    f"Scale {size} family estimates do not cover every observed family"
                )

            family_summary = run.get("family_summary") or {}
            summary_metrics = family_summary.get("metrics") or {}
            if (
                family_summary.get("aggregation_unit") != "workload_family"
                or family_summary.get("within_family_aggregation")
                != "equal_weight_variant_macro_mean"
                or family_summary.get("cross_family_aggregation")
                != "equal_weight_macro_mean"
            ):
                raise EvidenceValidationError(
                    f"Scale {size} family summary has invalid aggregation semantics"
                )
            for metric, scale_field in metric_pairs.items():
                estimates = [
                    float(family["values"][metric])
                    for family in families
                    if family.get("values", {}).get(metric) is not None
                ]
                expected_macro = statistics.fmean(estimates) if estimates else None
                summary = summary_metrics.get(metric) or {}
                seed = derive_bootstrap_seed(
                    DEFAULT_BOOTSTRAP_SEED,
                    "e5_catalog_scale",
                    run.get("dataset_sha256"),
                    size,
                    metric,
                )
                ci_low, ci_high = family_bootstrap_ci(
                    [
                        {
                            "family_id": str(family["family_id"]),
                            "value": family["values"][metric],
                        }
                        for family in families
                        if family.get("values", {}).get(metric) is not None
                    ],
                    "value",
                    seed=seed,
                )
                expected_inference = inference_eligibility(len(estimates))
                if (
                    summary.get("aggregation_unit") != "workload_family"
                    or summary.get("family_count") != len(estimates)
                    or summary.get("effective_family_n") != len(estimates)
                    or summary.get("estimate") != expected_macro
                    or summary.get("ci_low") != ci_low
                    or summary.get("ci_high") != ci_high
                    or summary.get("bootstrap_seed") != seed
                    or any(
                        summary.get(field) != value
                        for field, value in expected_inference.items()
                    )
                    or (
                        expected_macro is None
                        and scale.get(scale_field) is not None
                    )
                    or (
                        expected_macro is not None
                        and (
                            scale.get(scale_field) is None
                            or not math.isclose(
                                float(scale[scale_field]),
                                expected_macro,
                                rel_tol=0,
                                abs_tol=1e-6,
                            )
                        )
                    )
                ):
                    raise EvidenceValidationError(
                        f"Scale {size} {metric} is not the validated family macro estimate"
                    )

            latencies = sorted(
                float(record["latency_seconds"])
                for record in records
                if record.get("latency_seconds") is not None
            )
            latency_diagnostics = {
                "p2_latency_median_seconds": (
                    statistics.median(latencies) if latencies else None
                ),
                "p2_latency_p95_seconds": (
                    latencies[int(math.ceil(0.95 * len(latencies))) - 1]
                    if latencies
                    else None
                ),
                "p2_latency_min_seconds": min(latencies) if latencies else None,
                "p2_latency_max_seconds": max(latencies) if latencies else None,
                "p2_latency_std_seconds": (
                    statistics.stdev(latencies) if len(latencies) > 1 else 0.0
                ),
            }
            for field, expected_value in latency_diagnostics.items():
                observed_value = scale.get(field)
                if (
                    (expected_value is None and observed_value is not None)
                    or (
                        expected_value is not None
                        and (
                            observed_value is None
                            or not math.isclose(
                                float(observed_value),
                                expected_value,
                                rel_tol=0,
                                abs_tol=1e-6,
                            )
                        )
                    )
                ):
                    raise EvidenceValidationError(
                        f"Scale {size} {field} is not derived from raw runtime observations"
                    )

    # 9. Validate manifest.json
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
    if current_storage_schema:
        if manifest_raw.get("environment_identity") != env_data:
            raise EvidenceValidationError(
                "Manifest environment identity disagrees with raw storage environment"
            )
        if (
            manifest_raw.get("dataset_identity", {}).get("dataset_sha256")
            != storage_data.get("catalog", {}).get("file_sha256")
            or manifest_raw.get("candidate_catalog", {}).get("catalog_sha256")
            != storage_data.get("catalog", {}).get("file_sha256")
        ):
            raise EvidenceValidationError(
                "Manifest catalog identity disagrees with storage evidence"
            )

    # 10. Validate status.json
    status_raw = _strict_json(status_path.read_bytes(), label="status.json")
    if not isinstance(status_raw, Mapping):
        raise EvidenceValidationError("status.json must be an object")
    if status_raw.get("status") != execution_status:
        raise EvidenceValidationError(
            f"Status mismatch: status.json has {status_raw.get('status')} vs metrics {execution_status}"
        )
    if current_storage_schema:
        if (
            status_raw.get("schema_version") != STORAGE_SCHEMA_VERSION
            or status_raw.get("split_stage") != split_stage
            or status_raw.get("claims_permitted") is not claims_permitted
            or status_raw.get("storage_collector") != collector
        ):
            raise EvidenceValidationError(
                "Status report disagrees with collector-bound storage evidence"
            )
        status_metrics = (
            status_raw.get("final_naive_logical_bytes"),
            status_raw.get("final_unique_layer_bytes"),
            status_raw.get("final_storage_savings_bytes"),
        )
        if execution_status != "OBSERVED" and any(
            value is not None for value in status_metrics
        ):
            raise EvidenceValidationError(
                "Non-observed status report contains empirical storage metrics"
            )
        if execution_status == "OBSERVED" and prefixes:
            expected_status_metrics = (
                prefixes[-1]["naive_logical_bytes"],
                prefixes[-1]["unique_layer_bytes"],
                prefixes[-1]["naive_logical_bytes"]
                - prefixes[-1]["unique_layer_bytes"],
            )
            if status_metrics != expected_status_metrics:
                raise EvidenceValidationError(
                    "Observed status report storage metrics disagree with validated prefixes"
                )
        report_text = report_md_path.read_text(encoding="utf-8").lower()
        if (not claims_permitted or execution_status != "OBSERVED") and (
            "empirically confirms" in report_text
        ):
            raise EvidenceValidationError(
                "Ineligible storage report contains an empirical H7 confirmation"
            )

    final_savings = (
        prefixes[-1]["naive_logical_bytes"] - prefixes[-1]["unique_layer_bytes"]
        if prefixes
        else 0
    )

    complete_multiscale = all(
        s.get("storage_measurement_status") == "OBSERVED" for s in scale_data
    )
    eligible_4_image = (
        current_storage_schema
        and collector_authentic
        and execution_status == "OBSERVED"
        and split_stage == "confirmatory"
        and claims_permitted
        and immutable_catalog_valid
    )
    eligible_full = (
        eligible_4_image and complete_multiscale
    )

    scale_4_confirmatory_rec_observed = (
        len(scale_data) > 0
        and scale_data[0].get("catalog_size") == 4
        and scale_data[0].get("p2_evaluation_status") == "OBSERVED"
        and scale_data[0].get("split_stage") == "confirmatory"
        and scale_data[0].get("split_role") == "confirmatory"
    )

    if legacy_storage_schema:
        claim_eligibility = "LEGACY_INELIGIBLE_UNBOUND_COLLECTOR_ORIGIN"
    elif not collector_authentic:
        claim_eligibility = "INELIGIBLE_UNAUTHENTICATED_COLLECTOR_ORIGIN"
    elif not immutable_catalog_valid:
        claim_eligibility = "INELIGIBLE_MUTABLE_CATALOG_INPUTS"
    elif not recommendation_split_valid:
        claim_eligibility = "INELIGIBLE_SPLIT_CONTAMINATION"
    elif eligible_full and scale_4_confirmatory_rec_observed:
        claim_eligibility = "ELIGIBLE_FULL_MULTISCALE_WITH_CONFIRMATORY_RECOMMENDATION"
    elif eligible_full:
        claim_eligibility = "ELIGIBLE_FULL_MULTISCALE"
    elif eligible_4_image and scale_4_confirmatory_rec_observed:
        claim_eligibility = "ELIGIBLE_4_IMAGE_CATALOG_STORAGE_AND_CONFIRMATORY_RECOMMENDATION"
    elif eligible_4_image:
        claim_eligibility = "ELIGIBLE_4_IMAGE_CATALOG_STORAGE"
    else:
        claim_eligibility = "NOT_ELIGIBLE"

    return {
        "status": "PASS",
        "validator_status": "CURRENT_VALID" if current_storage_schema else "LEGACY_VALID",
        "storage_structurally_valid": True,
        "storage_dedup_valid": True,
        "storage_metric_valid": True,
        "size_domain_valid": True,
        "immutable_catalog_valid": immutable_catalog_valid,
        "recommendation_split_valid": recommendation_split_valid,
        "partial_scalability_valid": True,
        "complete_multiscale": complete_multiscale,
        "claim_eligibility": claim_eligibility,
        "eligible_as_current_e5_evidence": eligible_4_image,
        "full_scalability_claim_eligible": eligible_full,
        "validation_profile": (
            "STORAGE_SCALABILITY_V1_1_AUTHENTICATED"
            if current_storage_schema
            else "LEGACY_STORAGE_SCALABILITY_V1_0"
        ),
        "evidence_dir": str(directory),
        "experiment_id": "E5",
        "requirement_id": "image_storage",
        "execution_status": execution_status,
        "split_stage": split_stage,
        "claims_permitted": claims_permitted,
        "collector_origin": collector_origin,
        "collector_authentic": collector_authentic,
        "total_prefixes": len(prefixes),
        "configured_scales": [s["catalog_size"] for s in scale_data],
        "final_storage_savings_bytes": (
            final_savings if execution_status == "OBSERVED" else None
        ),
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
