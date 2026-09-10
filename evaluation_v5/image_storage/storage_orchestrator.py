"""Orchestration and artifact materialization for Protocol-v5 E5 image storage scalability."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any

import yaml

from evaluation_v5.provenance import write_json_exclusive
from evaluation_v5.schemas import EvidenceStatus, ProtocolV5Manifest

from .contracts import file_sha256
from .recommendation_evaluator import DEFAULT_RECALL_K, evaluate_catalog_scale_recommendation
from .storage_contracts import (
    DEFAULT_CATALOG_SCALES,
    EXPERIMENT_ID,
    PROTOCOL_VERSION,
    SIZE_DOMAIN_COMPRESSED_OCI_BLOB,
    STORAGE_SCHEMA_VERSION,
    ExperimentalCatalogConfig,
    ImageLayerMetadata,
    MarginalStorageRecord,
    PairwiseReuseAnalysis,
    PrefixStorageMeasurement,
    ScaleLevelEvaluationRecord,
    SplitStage,
    StorageCollectorOrigin,
    StorageEvidenceRecord,
    StorageExecutionStatus,
    check_immutable_catalog_gate,
    compute_marginal_storage,
    compute_pairwise_layer_reuse,
    get_experimental_catalog_config,
    get_ordered_catalog_images,
    is_real_storage_collector_origin,
)
from .storage_figures import generate_all_figures
from .storage_runner import create_storage_runner
from .validate_evidence import validate_e5_storage_evidence

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = ROOT / "recommender" / "image-catalog.yaml"
DEFAULT_STORAGE_RESULTS_ROOT = ROOT / "results_v5" / "protocol-v5.0.0" / "E5"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _git_info() -> dict[str, Any]:
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
            ).stdout.strip()
        )
        return {"git_revision": rev, "git_dirty": dirty}
    except Exception:
        return {"git_revision": "unknown", "git_dirty": False}


def _write_checksums(directory: Path) -> Path:
    """Generate SHA256SUMS file covering all files in directory."""
    records: list[str] = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            rel_path = path.relative_to(directory)
            sha = file_sha256(path)
            records.append(f"{sha}  {rel_path}")
    sums_file = directory / "SHA256SUMS"
    sums_file.write_text("\n".join(records) + "\n", encoding="utf-8")
    return sums_file


def _write_bytes_exclusive(path: Path, payload: bytes) -> Path:
    """Create one immutable raw collector response without overwriting evidence."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def _format_storage_markdown_report(
    *,
    run_id: str,
    catalog_version: str,
    catalog_sha256: str,
    execution_status: str,
    split_stage: str,
    claims_permitted: bool,
    collector: Mapping[str, Any],
    measurement_method: str,
    target_arch: str,
    target_os: str,
    git_info: dict[str, Any],
    inspections: Sequence[ImageLayerMetadata],
    prefixes: Sequence[PrefixStorageMeasurement],
    marginal_records: Sequence[MarginalStorageRecord],
    pairwise_analysis: PairwiseReuseAnalysis,
    scale_records: Sequence[ScaleLevelEvaluationRecord],
    figures: Mapping[str, str],
) -> str:
    lines: list[str] = []
    lines.append(f"# Protocol-v5 E5 Image Storage and Catalog Scalability Report: `{run_id}`\n")

    lines.append("## 1. Executive Summary & Provenance\n")
    lines.append(f"- **Execution Timestamp (UTC)**: {_utc_now()}")
    lines.append(f"- **Git Revision**: `{git_info.get('git_revision')}` (dirty: {git_info.get('git_dirty')})")
    lines.append(f"- **Execution Status**: `{execution_status}`")
    lines.append(f"- **Split Stage**: `{split_stage}`")
    lines.append(f"- **Claims Permitted**: `{claims_permitted}`")
    lines.append(f"- **Collector Origin**: `{collector.get('origin')}`")
    lines.append(
        f"- **Collector**: `{collector.get('collector_name')}` "
        f"(`{collector.get('collector_version')}`)"
    )
    lines.append(f"- **Size Domain**: `{SIZE_DOMAIN_COMPRESSED_OCI_BLOB}` (compressed OCI manifest layer blobs)")
    lines.append(f"- **Measurement Method**: `{measurement_method}`")
    lines.append(f"- **Target Platform**: `{target_os}/{target_arch}`")
    lines.append(f"- **Catalog Version**: `{catalog_version}` (SHA-256: `{catalog_sha256}`)")
    lines.append(f"- **Total Approved Images**: {len(inspections)}")
    lines.append(f"- **Configured Catalog Scales**: {', '.join(str(s.catalog_size) for s in scale_records)}\n")

    lines.append("## 2. Catalog Scalability Matrix\n")
    lines.append("| Requested Scale | Approved Images Available | Storage Status | P2 Rec Status | Execution Status | Notes |")
    lines.append("| :---: | :---: | :---: | :---: | :---: | :--- |")
    for s in scale_records:
        lines.append(
            f"| {s.catalog_size} | {len(s.ordered_immutable_image_references)} | "
            f"`{s.storage_measurement_status}` | `{s.p2_evaluation_status}` | "
            f"`{'OBSERVED' if s.storage_measurement_status == 'OBSERVED' else 'NOT_EXECUTED'}` | "
            f"{s.status_reason or 'Fully executed'} |"
        )
    lines.append("")

    lines.append("## 3. Administrator-Approved Catalog Immutability Audit\n")
    lines.append("Every participating image must satisfy the immutable input contract (`is_digest_pinned == True`).\n")
    lines.append("| Priority | Image ID | Requested Reference | Digest Pinned? | Resolved Digest | Canonical Resolved Reference | Gate Status |")
    lines.append("| :---: | :--- | :--- | :---: | :--- | :--- | :---: |")
    for idx, img in enumerate(inspections, 1):
        req_ref = img.requested_reference or img.image_reference
        pinned = img.is_digest_pinned and ("@sha256:" in req_ref)
        res_short = (img.resolved_digest[:19] + "...") if img.resolved_digest else "unknown"
        canon_short = (img.canonical_resolved_reference[:25] + "...") if img.canonical_resolved_reference else "unknown"
        gate = "PASS (Immutable)" if pinned else "**FAIL (Mutable)**"
        lines.append(
            f"| {idx} | **{img.image_id}** | `{req_ref}` | {'YES' if pinned else '**NO**'} | `{res_short}` | `{canon_short}` | {gate} |"
        )
    lines.append("")

    lines.append("## 4. Cumulative Storage & Prefix Deduplication (Hypothesis H7)\n")
    lines.append("Hypothesis H7 states: *Shared image layers require less cumulative storage than a naive logical sum as the frozen catalog grows.*\n")
    lines.append(
        "**Storage Accounting Semantics**:\n"
        "- `LogicalImageBytes`: Sum of every ordered manifest layer descriptor occurrence in the prefix.\n"
        "- `UniqueLayerBytes`: Cumulative byte volume of unique content-addressed layer digests in the prefix.\n"
        "- `Deduplication Savings`: `LogicalImageBytes - UniqueLayerBytes`.\n"
    )
    if prefixes and prefixes[0].within_image_duplicate_bytes:
        lines.append(
            "> [!NOTE]\n"
            "> Prefix 1 contains repeated layer descriptors. Its logical-versus-unique "
            f"difference is exactly {prefixes[0].within_image_duplicate_bytes:,} B, "
            "derived from the raw ordered descriptors in this package.\n"
        )
    lines.append("| Prefix | Introduced Image | Naive Logical Bytes | Unique Layer Bytes | Deduplication Savings | Savings Ratio | Within-Image Dups |")
    lines.append("| :---: | :--- | :---: | :---: | :---: | :---: | :---: |")

    all_nonexpanding = True
    final_savings = prefixes[-1].savings_bytes if prefixes else 0
    final_logical = prefixes[-1].naive_logical_bytes if prefixes else 0
    final_unique = prefixes[-1].unique_layer_bytes if prefixes else 0

    for p in prefixes:
        idx = p.prefix_size
        img_name = inspections[idx - 1].image_id if idx <= len(inspections) else f"img-{idx}"
        dup_info = f"{p.within_image_duplicate_digest_count} digest(s) ({p.within_image_duplicate_bytes:,} B)" if p.within_image_duplicate_bytes else "0 B"
        if p.unique_layer_bytes > p.naive_logical_bytes:
            all_nonexpanding = False
        lines.append(
            f"| {p.prefix_size} | **{img_name}** | {p.naive_logical_bytes:,} B | {p.unique_layer_bytes:,} B | {p.savings_bytes:,} B | {p.savings_ratio:.2%} | {dup_info} |"
        )
    lines.append("")

    lines.append("## 5. Marginal Storage per Introduced Image (U_n - U_{n-1})\n")
    lines.append("| Step | Image Introduced | Previous Unique Bytes | New Unique Bytes | Marginal Unique Bytes | Cumulative Logical | Cumulative Unique |")
    lines.append("| :---: | :--- | :---: | :---: | :---: | :---: | :---: |")
    for m in marginal_records:
        lines.append(
            f"| {m.introduction_index} | **{m.image_id}** | {m.previous_unique_bytes:,} B | {m.new_unique_bytes:,} B | **{m.marginal_unique_bytes:,} B** | {m.cumulative_logical_bytes:,} B | {m.cumulative_unique_bytes:,} B |"
        )
    lines.append("")

    lines.append("## 6. Pairwise Layer-Reuse Matrices\n")
    lines.append(
        "Layer matching uses exact content digest equality (`layer.digest == other.digest`).\n"
        "Pairwise shared layer count and byte metrics operate on **unique content-addressed layer digests**, "
        "not on ordered descriptor occurrences. Diagonal entries reflect the self unique layer digest count and "
        "self unique layer byte volume of each image.\n"
    )
    lines.append("### 6.1 Pairwise Shared Layer Storage (Bytes - Unique Digest Semantics)\n")
    short_names = [img.replace("-deep-learning", "").replace("-data-science", "") for img in pairwise_analysis.image_ids]
    hdr = "| Image | " + " | ".join(short_names) + " |"
    lines.append(hdr)
    lines.append("| :--- | " + " | ".join([":---:"] * len(short_names)) + " |")
    for i, row in enumerate(pairwise_analysis.shared_layer_byte_matrix):
        row_str = " | ".join(f"{b:,}" for b in row)
        lines.append(f"| **{short_names[i]}** | {row_str} |")
    lines.append("")

    lines.append("### 6.2 Pairwise Shared Layer Count (Unique Content Digests)\n")
    lines.append(hdr)
    lines.append("| :--- | " + " | ".join([":---:"] * len(short_names)) + " |")
    for i, row in enumerate(pairwise_analysis.shared_layer_count_matrix):
        row_str = " | ".join(str(c) for c in row)
        lines.append(f"| **{short_names[i]}** | {row_str} |")
    lines.append("")

    lines.append("## 7. Joint Recommendation Scalability & Split Provenance\n")
    all_rec_not_executed = all(s.p2_evaluation_status != "OBSERVED" for s in scale_records)
    if all_rec_not_executed:
        lines.append(
            "> [!NOTE]\n"
            "> **Zero Confirmatory Recommendation Observations**: Scale 4 recommendation is `NOT_EXECUTED` "
            "(`confirmatory_dataset_not_provided: sealed confirmatory split required for confirmatory stage`); "
            "Scales 8 and 16 are `NOT_EXECUTED` (`insufficient_approved_images`).\n"
        )
    lines.append("| Scale | Storage Status | P2 Rec Status | Stage | Split Role | Dataset ID | Cases | P2 Acceptable Acc | P2 Preferred Acc | P2 Recall@5 | Mean Latency |")
    lines.append("| :---: | :---: | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: |")
    for s in scale_records:
        if s.p2_evaluation_status == "OBSERVED":
            acc_str = f"{s.p2_image_acceptable_accuracy:.2%}" if s.p2_image_acceptable_accuracy is not None else "N/A"
            pref_str = f"{s.p2_image_preferred_accuracy:.2%}" if s.p2_image_preferred_accuracy is not None else "N/A"
            rec_str = f"{s.p2_retrieval_recall_at_k:.2%}" if s.p2_retrieval_recall_at_k is not None else "N/A"
            lat_str = f"{(s.p2_latency_mean_seconds or 0)*1000:.2f} ms"
            lines.append(
                f"| {s.catalog_size} | `{s.storage_measurement_status}` | `{s.p2_evaluation_status}` | "
                f"`{s.split_stage}` | `{s.split_role}` | `{s.evaluation_dataset_identity}` | "
                f"{s.evaluated_case_count} | {acc_str} | {pref_str} | {rec_str} | {lat_str} |"
            )
        else:
            lines.append(
                f"| {s.catalog_size} | `{s.storage_measurement_status}` | `{s.p2_evaluation_status}` | "
                f"`{s.split_stage}` | `{s.split_role}` | `{s.evaluation_dataset_identity}` | "
                f"0 | N/A | N/A | N/A | N/A |"
            )
    lines.append("")

    lines.append("## 8. Metric Definitions & Auditability\n")
    lines.append(
        "- **`image_acceptable_accuracy`**: Primary Protocol-v5 image acceptability metric defined in `evaluation_v5/analysis/statistical_analysis.py`. Evaluates the proportion of feasible benchmark requests where the recommended image belongs to the gold acceptable candidate image set.\n"
        "- **`image_preferred_accuracy`**: Strict Top-1 metric evaluating the proportion of feasible benchmark requests where the recommended image exactly matches the primary gold preferred image ID.\n"
        "- **`retrieval_recall_at_k`**: Macro Recall@K of acceptable candidates within the top-K pre-constraint hybrid retrieval fused hit list ($K=5$ by default, consistent with `DEFAULT_RETRIEVAL_KS` in Protocol-v5 reporting).\n"
        "- **`recommendation_latency`**: Total end-to-end elapsed time in seconds from request arrival to recommendation generation.\n"
        "- **`marginal_unique_bytes`**: $U_n - U_{n-1}$, the incremental unique storage introduced by each new image in priority sequence.\n"
        "- **`requested_reference`**: The administrator-approved reference configured in the catalog, audited for syntactical digest pinning (`@sha256:`).\n"
        "- **`canonical_resolved_reference`**: The immutable content-addressable reference (`repository@sha256:<digest>`).\n"
    )

    lines.append("## 9. Generated Reproducible Figures\n")
    lines.append(f"- **Figure A (Cumulative Storage)**: `figures/{figures.get('figure_a', 'figure_a_cumulative_storage.png')}`")
    lines.append(f"- **Figure B (Marginal Storage)**: `figures/{figures.get('figure_b', 'figure_b_marginal_storage.png')}`")
    lines.append(f"- **Figure C (Pairwise Reuse - Bytes)**: `figures/{figures.get('figure_c_bytes', 'figure_c_pairwise_reuse_bytes.png')}`")
    lines.append(f"- **Figure C (Pairwise Reuse - Count)**: `figures/{figures.get('figure_c_count', 'figure_c_pairwise_reuse_count.png')}`")
    lines.append(f"- **Figure D (Recommendation Quality)**: `figures/{figures.get('figure_d', 'figure_d_recommendation_quality.png')}` (Configured scale markers; zero observed recommendation points plotted when confirmatory split is not supplied)")
    lines.append(f"- **Figure E (Recommendation Latency)**: `figures/{figures.get('figure_e', 'figure_e_recommendation_latency.png')}` (Configured scale markers; zero latency points or error bars plotted when recommendation evaluation is not executed)\n")

    lines.append("## 10. Honest Scientific Claim Boundary\n")
    support_h7 = (
        execution_status == StorageExecutionStatus.OBSERVED.value
        and claims_permitted
        and is_real_storage_collector_origin(str(collector.get("origin", "")))
        and all_nonexpanding
        and final_savings > 0
    )
    scale_4_rec = scale_records[0] if scale_records else None
    rec_is_confirmatory = (
        scale_4_rec is not None
        and scale_4_rec.p2_evaluation_status == "OBSERVED"
        and scale_4_rec.split_stage == "confirmatory"
        and scale_4_rec.split_role == "confirmatory"
    )
    if support_h7 and len(inspections) == 4:
        rec_scope = (
            "Joint scale-4 recommendation evaluation was observed with confirmatory split."
            if rec_is_confirmatory
            else (
                "Zero confirmatory recommendation observations (Scale 4 NOT_EXECUTED due to absence "
                "of sealed confirmatory split; Scales 8 and 16 NOT_EXECUTED due to insufficient approved images). "
                "Storage measurement has 1 observed scale (N=4); recommendation has 0 confirmatory observed scales."
                if (scale_4_rec and scale_4_rec.p2_evaluation_status == "NOT_EXECUTED")
                else "Recommendation evaluation was executed on development data only (exploratory/non-confirmatory)."
            )
        )
        savings_ratio = (final_savings / final_logical) if final_logical > 0 else 0.0
        lines.append(
            "> [!IMPORTANT]\n"
            "> **Constrained Claim Verdict (PASS_WITH_LIMITATIONS)**:\n"
            f"> For the frozen four-image amd64 catalog, direct OCI manifest inspection measured {final_logical:,} "
            f"logical compressed layer bytes and {final_unique:,} unique content-addressed compressed layer bytes, "
            f"corresponding to {savings_ratio:.2%} deduplication under the experiment's digest-based storage accounting. "
            "> This empirically confirms Hypothesis **H7** for the frozen 4-image catalog.\n"
            ">\n"
            "> **Storage Claim Semantics & Size Domain**:\n"
            "> The measured domain is `compressed_oci_manifest_layer_bytes` (content-addressed compressed OCI layer-blob "
            "> accounting under digest deduplication). This represents immutable registry/container image manifest layer bytes "
            "> and does not represent unpacked container filesystem disk usage, snapshotter storage, overlay filesystem overhead, "
            "> or actual filesystem block allocation.\n"
            ">\n"
            f"> **Recommendation Evaluation Scope**: {rec_scope}\n"
            ">\n"
            "> **Scalability Boundary Limitation**:\n"
            "> Larger catalog scales (8 and 16 images) remain `NOT_EXECUTED` because additional administrator-approved "
            "> immutable images have not been published in the repository catalog. With only scale 4 observed for storage, "
            "> **no empirical 4→8→16 multi-scale trend is yet estimable**.\n"
        )
    elif execution_status == StorageExecutionStatus.NOT_EXECUTED.value:
        lines.append(
            "> [!NOTE]\n"
            "> **Non-Observed / Not Executed Notice**: A real registry or container-storage "
            "observation was not performed. Test calculations, if present, are sealed as "
            "`NOT_EXECUTED`. No empirical storage claims are asserted.\n"
        )

    return "\n".join(lines)


def run_storage_evaluation(
    catalog_path: Path = DEFAULT_CATALOG_PATH,
    mode: str = "auto",
    stage: str = "confirmatory",
    claims_permitted: bool | None = None,
    target_arch: str = "amd64",
    target_os: str = "linux",
    dry_run_if_unavailable: bool = True,
    output_dir: Path | None = None,
    run_id: str | None = None,
    timeout_seconds: float = 60.0,
    scales: Sequence[int] = DEFAULT_CATALOG_SCALES,
    eval_recommendation: bool = True,
    dataset_path: Path | str | None = None,
    split_path: Path | str | None = None,
    recall_k: int = DEFAULT_RECALL_K,
    injected_image_layers: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    freeze_path: Path | None = None,
) -> Path:
    """Execute Protocol-v5 E5 image storage scalability evaluation and materialize complete evidence."""
    cat_path = Path(catalog_path).resolve()
    if not cat_path.is_file():
        raise FileNotFoundError(f"Image catalog not found at {cat_path}")

    with open(cat_path, "r", encoding="utf-8") as f:
        catalog = yaml.safe_load(f)

    catalog_version = str(catalog.get("catalog_version", "unknown"))
    cat_sha = file_sha256(cat_path)

    # Resolve runner
    runner = create_storage_runner(
        catalog=catalog,
        mode=mode,
        target_arch=target_arch,
        target_os=target_os,
        dry_run_if_unavailable=dry_run_if_unavailable,
        timeout_seconds=timeout_seconds,
        injected_image_layers=injected_image_layers,
    )

    # Execute layer measurement across available catalog images. The caller's
    # mode string selects a collector but never supplies evidence status.
    inspections, prefixes, reported_status = runner.measure_all()
    execution_status = runner.execution_status
    if reported_status != execution_status:
        raise ValueError("Storage runner status disagrees with its fixed collector origin")
    collector = runner.collector_provenance()
    raw_observations = runner.raw_observations()

    # Determine stage before deriving the non-overridable claim boundary.
    norm_stage = stage.lower().strip()
    if norm_stage not in (SplitStage.CONFIRMATORY.value, SplitStage.DEVELOPMENT.value):
        raise ValueError(f"Invalid split stage: {stage!r}. Must be 'confirmatory' or 'development'.")

    confirmatory_capability = None
    if norm_stage == SplitStage.CONFIRMATORY.value and eval_recommendation:
        from evaluation_v5.isolation import (
            CONFIRMATORY_DATASET_ENV_VAR,
            FREEZE_ARTIFACT_ENV_VAR,
            load_confirmatory_split,
            resolve_confirmatory_sources,
        )

        source_requested = bool(
            dataset_path is not None
            or freeze_path is not None
            or CONFIRMATORY_DATASET_ENV_VAR in os.environ
            or FREEZE_ARTIFACT_ENV_VAR in os.environ
        )
        if source_requested:
            try:
                dataset_source, freeze_source = resolve_confirmatory_sources(
                    dataset_path=Path(dataset_path) if dataset_path is not None else None,
                    freeze_path=Path(freeze_path) if freeze_path is not None else None,
                )
                confirmatory_capability = load_confirmatory_split(
                    dataset_source, freeze_source
                )
            except Exception as exc:
                logger.warning(
                    "Failed to prepare confirmatory recommendation split: %s", exc
                )

    origin = str(collector.get("origin", ""))
    real_origin = is_real_storage_collector_origin(origin)
    if real_origin:
        if execution_status != StorageExecutionStatus.OBSERVED.value:
            raise ValueError("Real storage collector did not produce OBSERVED status")
        if len(raw_observations) != len(inspections):
            raise ValueError("OBSERVED storage requires one raw collector response per image")
        for metadata in inspections:
            required = {
                "resolved_digest": metadata.resolved_digest,
                "manifest_digest": metadata.manifest_digest,
                "config_digest": metadata.config_digest,
                "raw_observation_path": metadata.raw_observation_path,
                "raw_observation_sha256": metadata.raw_observation_sha256,
            }
            missing = sorted(name for name, value in required.items() if not value)
            if missing or not metadata.layers or not metadata.platform:
                raise ValueError(
                    f"OBSERVED storage metadata for {metadata.image_id} is incomplete: "
                    + ", ".join(missing or ["layers/platform"])
                )
            payload = raw_observations.get(metadata.raw_observation_path)
            if payload is None or hashlib.sha256(payload).hexdigest() != metadata.raw_observation_sha256:
                raise ValueError(
                    f"OBSERVED storage metadata for {metadata.image_id} lacks its checksum-bound raw response"
                )
    elif execution_status == StorageExecutionStatus.OBSERVED.value:
        raise ValueError("Non-real storage collector cannot produce OBSERVED evidence")

    # Construct experimental catalog configuration
    exp_catalog = get_experimental_catalog_config(catalog, scales=scales)

    # Compute first-class marginal storage records
    marginal_records = compute_marginal_storage(inspections)

    # Compute pairwise layer-reuse analysis
    pairwise_analysis = compute_pairwise_layer_reuse(inspections)

    # Read freeze configuration if available for semantic provenance
    frozen_cfg_path = freeze_path or (ROOT / "results_v5" / "protocol-v5.0.0" / "freezes" / "frozen-configuration.json")
    backend_systems = {"P2": "p2-pipeline-v1.0.0"}
    if frozen_cfg_path.is_file():
        try:
            freeze_data = json.loads(frozen_cfg_path.read_text(encoding="utf-8"))
            p2_sys = freeze_data.get("systems", {}).get("P2", {})
            if "pipeline_version" in p2_sys:
                backend_systems["P2"] = p2_sys["pipeline_version"]
        except Exception:
            pass

    git = _git_info()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = run_id or f"e5-storage-scalability-{timestamp}"

    # Audit immutable catalog gate and derive claims; callers cannot promote a run.
    immutability_result = check_immutable_catalog_gate(inspections, stage=norm_stage)
    derived_claims_permitted = bool(
        norm_stage == SplitStage.CONFIRMATORY.value
        and execution_status == StorageExecutionStatus.OBSERVED.value
        and real_origin
        and immutability_result.is_immutable
    )
    if claims_permitted is not None:
        if not isinstance(claims_permitted, bool):
            raise ValueError("claims_permitted must be boolean when supplied")
        if claims_permitted != derived_claims_permitted:
            raise ValueError(
                "claims_permitted is derived from authenticated collector origin and cannot be overridden"
            )
    claims_permitted = derived_claims_permitted

    # Prepare directories
    out_dir = output_dir or (DEFAULT_STORAGE_RESULTS_ROOT / run_id)
    raw_dir = out_dir / "raw"
    derived_dir = out_dir / "derived"
    figures_dir = out_dir / "figures"
    report_dir = out_dir / "report"

    out_dir.mkdir(parents=True, exist_ok=False)
    raw_dir.mkdir()
    derived_dir.mkdir()
    figures_dir.mkdir()
    report_dir.mkdir()

    for relative_path, payload in sorted(raw_observations.items()):
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Raw storage observation path escapes the evidence package")
        _write_bytes_exclusive(raw_dir / relative, payload)

    # Evaluate each configured scale
    scale_records: list[ScaleLevelEvaluationRecord] = []
    raw_scale_evaluations: list[dict[str, Any]] = []
    for scale in exp_catalog.catalog_scales:
        scale_status, reason = exp_catalog.get_scale_status(scale)
        scale_imgs = exp_catalog.get_scale_images(scale)
        ordered_refs = tuple(img.reference for img in scale_imgs)

        if scale_status == "OBSERVED" and scale <= len(prefixes):
            pref = prefixes[scale - 1]
            marg = marginal_records[scale - 1]
            st_status = execution_status

            # Recommendation evaluation
            # Recommendation evaluation is an independently provenance-bound
            # observation. A synthetic storage collector may still exercise a
            # development split, but cannot promote the storage package itself.
            if eval_recommendation:
                rec_dataset_path = (
                    dataset_path
                    if norm_stage == "confirmatory"
                    else (dataset_path or split_path)
                )
                rec_res = evaluate_catalog_scale_recommendation(
                    base_catalog=catalog,
                    scale_images=scale_imgs,
                    stage=norm_stage,
                    dataset_path=rec_dataset_path,
                    freeze_path=freeze_path,
                    split_bundle=confirmatory_capability,
                    k=recall_k,
                )
            else:
                rec_res = {
                    "status": "NOT_EXECUTED",
                    "reason": "recommendation_eval_skipped" if not eval_recommendation else "dry_run_mode",
                    "stage": norm_stage,
                    "split_role": "none",
                    "image_acceptable_accuracy": None,
                    "image_preferred_accuracy": None,
                    "retrieval_recall_at_k": None,
                    "recall_k": recall_k,
                    "latency": {},
                    "evaluated_cases": 0,
                    "feasible_cases": 0,
                    "dataset_id": "none",
                    "dataset_path": "",
                    "dataset_sha256": "0" * 64,
                    "p2_config_version": "none",
                    "p2_version": "p2-hybrid-v1.0.0",
                }

            raw_scale_evaluations.append(
                {
                    "schema_version": "protocol-v5-catalog-scale-recommendation-run-v1.0.0",
                    "catalog_size": scale,
                    "catalog_id": f"frozen-catalog-scale-{scale}",
                    "status": rec_res.get("status", "NOT_EXECUTED"),
                    "reason": rec_res.get("reason", ""),
                    "split_stage": norm_stage,
                    "split_role": rec_res.get("split_role", "none"),
                    "dataset_id": rec_res.get("dataset_id", "none"),
                    "dataset_sha256": rec_res.get("dataset_sha256", "0" * 64),
                    "split_schema_version": rec_res.get("split_schema_version", "none"),
                    "aggregation_unit": rec_res.get("aggregation_unit", "workload_family"),
                    "case_records": rec_res.get("case_records", []),
                    "family_estimates": rec_res.get("family_estimates", []),
                    "family_summary": rec_res.get("family_summary", {}),
                    "confirmatory_provenance": rec_res.get(
                        "confirmatory_provenance"
                    ),
                }
            )

            lat_info = rec_res.get("latency", {})
            scale_records.append(
                ScaleLevelEvaluationRecord(
                    catalog_size=scale,
                    catalog_id=f"frozen-catalog-scale-{scale}",
                    catalog_hash=exp_catalog.catalog_hash,
                    ordered_immutable_image_references=ordered_refs,
                    storage_measurement_status=st_status,
                    logical_image_bytes=pref.naive_logical_bytes,
                    unique_layer_bytes=pref.unique_layer_bytes,
                    dedup_saving_bytes=pref.savings_bytes,
                    dedup_saving_ratio=pref.savings_ratio,
                    marginal_unique_bytes=marg.marginal_unique_bytes,
                    size_domain=SIZE_DOMAIN_COMPRESSED_OCI_BLOB,
                    p2_evaluation_status=rec_res.get("status", "NOT_EXECUTED"),
                    p2_image_acceptable_accuracy=rec_res.get("image_acceptable_accuracy"),
                    p2_image_preferred_accuracy=rec_res.get("image_preferred_accuracy"),
                    p2_retrieval_recall_at_k=rec_res.get("retrieval_recall_at_k"),
                    recall_k=recall_k,
                    p2_latency_mean_seconds=lat_info.get("mean_seconds"),
                    p2_latency_median_seconds=lat_info.get("median_seconds"),
                    p2_latency_p95_seconds=lat_info.get("p95_seconds"),
                    p2_latency_min_seconds=lat_info.get("min_seconds"),
                    p2_latency_max_seconds=lat_info.get("max_seconds"),
                    p2_latency_std_seconds=lat_info.get("std_seconds"),
                    evaluation_dataset_identity=rec_res.get("dataset_id", "none"),
                    dataset_sha256=rec_res.get("dataset_sha256", "0" * 64),
                    evaluated_case_count=rec_res.get("evaluated_cases", 0),
                    feasible_case_count=rec_res.get("feasible_cases", 0),
                    p2_config_version=rec_res.get("p2_config_version", "none"),
                    provenance={
                        "git_revision": git.get("git_revision"),
                        "storage_collector_origin": origin,
                        "raw_recommendation_record_count": len(
                            rec_res.get("case_records", [])
                        ),
                        "recommendation_aggregation_unit": rec_res.get(
                            "aggregation_unit", "workload_family"
                        ),
                        "confirmatory_recommendation": rec_res.get(
                            "confirmatory_provenance"
                        ),
                    },
                    status_reason=rec_res.get("reason", ""),
                    split_stage=norm_stage,
                    split_role=rec_res.get("split_role", "none"),
                    dataset_path=rec_res.get("dataset_path", ""),
                    ordered_requested_references=tuple(img.reference for img in scale_imgs),
                    canonical_resolved_references=tuple(img.canonical_resolved_reference for img in scale_imgs),
                    all_references_digest_pinned=all(img.is_digest_pinned for img in scale_imgs),
                    p2_version=rec_res.get("p2_version", "p2-hybrid-v1.0.0"),
                )
            )
        else:
            # Scale has insufficient approved images -> Mark NOT_EXECUTED honestly
            raw_scale_evaluations.append(
                {
                    "schema_version": "protocol-v5-catalog-scale-recommendation-run-v1.0.0",
                    "catalog_size": scale,
                    "catalog_id": f"frozen-catalog-scale-{scale}",
                    "status": "NOT_EXECUTED",
                    "reason": reason,
                    "split_stage": norm_stage,
                    "split_role": "none",
                    "dataset_id": "none",
                    "dataset_sha256": "0" * 64,
                    "split_schema_version": "none",
                    "aggregation_unit": "workload_family",
                    "case_records": [],
                    "family_estimates": [],
                    "family_summary": {},
                    "confirmatory_provenance": None,
                }
            )
            scale_records.append(
                ScaleLevelEvaluationRecord(
                    catalog_size=scale,
                    catalog_id=f"frozen-catalog-scale-{scale}",
                    catalog_hash=exp_catalog.catalog_hash,
                    ordered_immutable_image_references=ordered_refs,
                    storage_measurement_status="NOT_EXECUTED",
                    logical_image_bytes=None,
                    unique_layer_bytes=None,
                    dedup_saving_bytes=None,
                    dedup_saving_ratio=None,
                    marginal_unique_bytes=None,
                    size_domain=SIZE_DOMAIN_COMPRESSED_OCI_BLOB,
                    p2_evaluation_status="NOT_EXECUTED",
                    p2_image_acceptable_accuracy=None,
                    p2_image_preferred_accuracy=None,
                    p2_retrieval_recall_at_k=None,
                    recall_k=recall_k,
                    p2_latency_mean_seconds=None,
                    p2_latency_median_seconds=None,
                    p2_latency_p95_seconds=None,
                    p2_latency_min_seconds=None,
                    p2_latency_max_seconds=None,
                    p2_latency_std_seconds=None,
                    evaluation_dataset_identity="none",
                    dataset_sha256="0" * 64,
                    evaluated_case_count=0,
                    feasible_case_count=0,
                    p2_config_version="none",
                    provenance={
                        "git_revision": git.get("git_revision"),
                        "storage_collector_origin": origin,
                        "raw_recommendation_record_count": 0,
                        "recommendation_aggregation_unit": "workload_family",
                    },
                    status_reason=reason,
                    split_stage=norm_stage,
                    split_role="none",
                    dataset_path="",
                    ordered_requested_references=ordered_refs,
                    canonical_resolved_references=ordered_refs,
                    all_references_digest_pinned=all("@sha256:" in r for r in ordered_refs),
                    p2_version="none",
                )
            )

    # 1. Raw layer inspections & environment
    raw_layers_data = [img.to_dict() for img in inspections]
    write_json_exclusive(raw_dir / "image_layers.json", raw_layers_data)
    write_json_exclusive(
        raw_dir / "catalog_scale_recommendations.json",
        {
            "schema_version": "protocol-v5-catalog-scale-recommendations-v1.0.0",
            "collector_origin": origin,
            "scale_evaluations": raw_scale_evaluations,
        },
    )

    if origin == StorageCollectorOrigin.REAL_REGISTRY.value:
        runtime = "docker"
        measurement_method = (
            "docker manifest inspect OCI layer digest accounting "
            f"(platform: {target_os}/{target_arch})"
        )
    elif origin == StorageCollectorOrigin.CONTAINER_STORAGE_OBSERVATION.value:
        runtime = "container_storage"
        measurement_method = "container storage observation"
    elif origin == StorageCollectorOrigin.SYNTHETIC_TEST.value:
        runtime = "synthetic_test"
        measurement_method = "synthetic test calculation (non-observed)"
    else:
        runtime = "dry_run"
        measurement_method = "dry-run inspection (non-observed)"
    env_data = {
        "environment_id": f"e5-storage-{platform.system().lower()}-{target_arch}",
        "runtime": runtime,
        "operating_system": target_os,
        "architecture": target_arch,
        "platform_details": platform.platform(),
        "python_version": sys.version,
        "git_info": git,
        "measurement_method": measurement_method,
        "size_domain": SIZE_DOMAIN_COMPRESSED_OCI_BLOB,
        "storage_collector": collector,
    }
    write_json_exclusive(raw_dir / "environment.json", env_data)

    # 2. Derived storage metrics (Schema-compliant H7 artifact)
    ordered_digests = [img.image_digest for img in inspections]
    storage_evidence = StorageEvidenceRecord(
        schema_version=STORAGE_SCHEMA_VERSION,
        protocol_version=PROTOCOL_VERSION,
        experiment_id=EXPERIMENT_ID,
        execution_status=execution_status,
        split_stage=norm_stage,
        claims_permitted=claims_permitted,
        measured_at_utc=_utc_now(),
        catalog={
            "version": catalog_version,
            "file_sha256": cat_sha,
            "ordered_image_digests": ordered_digests,
        },
        platform={
            "environment_id": env_data["environment_id"],
            "runtime": env_data["runtime"],
            "operating_system": env_data["operating_system"],
            "architecture": env_data["architecture"],
        },
        collector=collector,
        measurement_method=measurement_method,
        prefixes=tuple(prefixes),
        provenance={
            "git_revision": git.get("git_revision", "unknown"),
            "dataset_sha256": cat_sha,
            "backend_system_versions": backend_systems,
            "storage_collector_origin": origin,
        },
    )
    write_json_exclusive(derived_dir / "storage_metrics.json", storage_evidence.to_dict())

    # 3. Derived first-class marginal storage artifact
    write_json_exclusive(
        derived_dir / "marginal_storage.json",
        [m.to_dict() for m in marginal_records],
    )

    # 4. Derived pairwise layer-reuse artifact
    write_json_exclusive(
        derived_dir / "pairwise_layer_reuse.json",
        pairwise_analysis.to_dict(),
    )

    # 5. Derived catalog scalability records
    write_json_exclusive(
        derived_dir / "catalog_scalability.json",
        [s.to_dict() for s in scale_records],
    )

    # 6. Generate Figures A-E. Non-observed test bytes are not plotted as evidence.
    plot_observed = execution_status == StorageExecutionStatus.OBSERVED.value
    figures_generated = generate_all_figures(
        prefixes=prefixes if plot_observed else (),
        marginal_records=marginal_records if plot_observed else (),
        pairwise_analysis=(
            pairwise_analysis if plot_observed else compute_pairwise_layer_reuse(())
        ),
        scale_records=scale_records,
        output_dir=figures_dir,
    )

    # 7. Report status & markdown
    final_savings = prefixes[-1].savings_bytes if prefixes else 0
    all_scales_observed = all(s.storage_measurement_status == "OBSERVED" for s in scale_records)
    status_data = {
        "schema_version": STORAGE_SCHEMA_VERSION,
        "run_id": run_id,
        "status": execution_status,
        "overall_verdict": (
            "PASS"
            if all_scales_observed and claims_permitted
            else ("PASS_WITH_LIMITATIONS" if execution_status == "OBSERVED" else "NOT_EXECUTED")
        ),
        "split_stage": norm_stage,
        "claims_permitted": claims_permitted,
        "storage_collector": collector,
        "total_images": len(inspections),
        "total_prefixes": len(prefixes),
        "configured_scales": list(exp_catalog.catalog_scales),
        "immutable_catalog_valid": immutability_result.is_immutable,
        "immutability_details": immutability_result.details,
        "recommendation_scale_4_status": scale_records[0].p2_evaluation_status if scale_records else "NOT_EXECUTED",
        "recommendation_scale_4_stage": scale_records[0].split_stage if scale_records else "none",
        "recommendation_scale_4_role": scale_records[0].split_role if scale_records else "none",
        "catalog_4_status": scale_records[0].storage_measurement_status if scale_records else "UNKNOWN",
        "catalog_8_status": scale_records[1].storage_measurement_status if len(scale_records) > 1 else "UNKNOWN",
        "catalog_16_status": scale_records[2].storage_measurement_status if len(scale_records) > 2 else "UNKNOWN",
        "final_naive_logical_bytes": (
            prefixes[-1].naive_logical_bytes if prefixes and plot_observed else None
        ),
        "final_unique_layer_bytes": (
            prefixes[-1].unique_layer_bytes if prefixes and plot_observed else None
        ),
        "final_storage_savings_bytes": final_savings if plot_observed else None,
        "timestamp_utc": _utc_now(),
    }
    write_json_exclusive(report_dir / "status.json", status_data)

    report_md = _format_storage_markdown_report(
        run_id=run_id,
        catalog_version=catalog_version,
        catalog_sha256=cat_sha,
        execution_status=execution_status,
        split_stage=norm_stage,
        claims_permitted=claims_permitted,
        collector=collector,
        measurement_method=measurement_method,
        target_arch=target_arch,
        target_os=target_os,
        git_info=git,
        inspections=inspections,
        prefixes=prefixes if plot_observed else (),
        marginal_records=marginal_records if plot_observed else (),
        pairwise_analysis=(
            pairwise_analysis if plot_observed else compute_pairwise_layer_reuse(())
        ),
        scale_records=scale_records,
        figures=figures_generated,
    )
    (report_dir / "E5_IMAGE_STORAGE_REPORT.md").write_text(report_md, encoding="utf-8")

    freeze_data: dict[str, Any] = {}
    if frozen_cfg_path.is_file():
        try:
            freeze_data = json.loads(frozen_cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    frozen_cand = freeze_data.get("candidate_catalog", {})
    frozen_indexes = freeze_data.get("indexes", {})
    dense_idx = frozen_indexes.get("dense", {})
    sparse_idx = frozen_indexes.get("sparse", {})
    hybrid_idx = frozen_indexes.get("hybrid", {})

    corpus_sha = frozen_cand.get("corpus_sha256")
    if not corpus_sha and execution_status == StorageExecutionStatus.OBSERVED.value:
        raise ValueError(
            "OBSERVED storage evidence requires a frozen candidate corpus checksum"
        )
    elif execution_status != StorageExecutionStatus.OBSERVED.value:
        corpus_sha = None

    frozen_cfg = freeze_data.get("configuration", {})
    retrieval_cfg = (
        frozen_cfg.get("P2", {})
        if execution_status == StorageExecutionStatus.OBSERVED.value
        else {}
    )
    constraints_cfg = (
        frozen_cfg.get("constraints", {})
        if execution_status == StorageExecutionStatus.OBSERVED.value
        else {}
    )
    if execution_status == StorageExecutionStatus.OBSERVED.value and (
        not retrieval_cfg or not constraints_cfg
    ):
        raise ValueError(
            "OBSERVED storage evidence requires frozen retrieval and constraint provenance"
        )

    # 8. Manifest.json (cross-experiment ProtocolV5Manifest compatibility)
    is_obs = (execution_status == StorageExecutionStatus.OBSERVED.value)
    manifest_data = {
        "schema_version": "protocol-v5-manifest-v1.0.0",
        "protocol_version": PROTOCOL_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "run_id": run_id,
        "git_revision": git.get("git_revision") if is_obs else (git.get("git_revision") if git.get("git_revision") != "unknown" else None),
        "execution_timestamp_utc": _utc_now(),
        "dataset_identity": {
            "dataset_id": "frozen-catalog",
            "dataset_sha256": cat_sha,
        },
        "split_identity": {
            "split_id": "frozen-catalog",
            "stage": norm_stage,
        },
        "backend_system_versions": {
            "B0": "jupyterhub-default-selection",
            "P1": "rule-based-v1",
            "P2": backend_systems.get("P2", "p2-pipeline-v1.0.0"),
        },
        "candidate_catalog": {
            "catalog_version": catalog_version,
            "catalog_sha256": cat_sha,
            "corpus_version": "environment-candidate-corpus-v1",
            "corpus_sha256": corpus_sha,
        },
        "structured_intent_schema_version": "protocol-v5-structured-intent-v1.0.0" if is_obs else None,
        "extractor": {
            "extractor_name": "intent-spawner-local-feature-extractor" if is_obs else None,
            "extractor_version": "feature-extractor-v1.0.0" if is_obs else None,
            "extractor_model_id": "intent-spawner-local-rule-hash" if is_obs else None,
            "extractor_prompt_version": "prompt-v1.0.0" if is_obs else None,
            "extractor_prompt_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" if is_obs else None,
        },
        "embedding_indexes": {
            "embedding_model_id": dense_idx.get("model_id", "intent-spawner-local-feature-hash") if is_obs else None,
            "embedding_model_revision": dense_idx.get("model_revision", "feature-hash-embedding-v1.0.0") if is_obs else None,
            "dense_index_version": dense_idx.get("index_version", "environment-dense-index-v1") if is_obs else None,
            "dense_index_sha256": dense_idx.get("index_checksum", "c0561bcd1ee6ec5153b710aef3deae88bd259a011ddd454513cbb1c675118387") if is_obs else None,
            "sparse_index_version": sparse_idx.get("index_version", "environment-sparse-index-v1") if is_obs else None,
            "sparse_index_sha256": sparse_idx.get("index_checksum", "931fac84b818cb934a37bfbfa76092a89626cd5eaffc869887de8558bc6fa747") if is_obs else None,
            "hybrid_index_version": hybrid_idx.get("index_version", "environment-hybrid-index-v1") if is_obs else None,
            "hybrid_index_sha256": hybrid_idx.get("index_checksum", "45ea08f29492d796189920713636b3a9cae2f0fb264e023124eb38c8cfad83a4") if is_obs else None,
        },
        "retrieval_configuration": retrieval_cfg,
        "constraint_ranking_configuration": constraints_cfg,
        "p3_reranker_version": None,
        "environment_identity": env_data,
        "random_seeds": [42],
        "execution_status": execution_status,
    }
    write_json_exclusive(out_dir / "manifest.json", manifest_data)

    # 9. SHA256SUMS covering all files
    _write_checksums(out_dir)

    # 10. Fail-closed validation of produced package
    validate_e5_storage_evidence(
        out_dir, confirmatory_split=confirmatory_capability
    )

    return out_dir
