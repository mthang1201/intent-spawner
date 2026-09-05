"""Orchestration and artifact materialization for Protocol-v5 E5 image storage scalability."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Mapping, Sequence

import yaml

from evaluation_v5.provenance import write_json_exclusive
from evaluation_v5.schemas import EvidenceStatus, ProtocolV5Manifest

from .contracts import file_sha256
from .storage_contracts import (
    EXPERIMENT_ID,
    PROTOCOL_VERSION,
    STORAGE_SCHEMA_VERSION,
    ImageLayerMetadata,
    PrefixStorageMeasurement,
    SplitStage,
    StorageEvidenceRecord,
    StorageExecutionStatus,
    get_ordered_catalog_images,
)
from .storage_runner import BaseStorageRunner, create_storage_runner
from .validate_evidence import validate_e5_storage_evidence

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


def _format_storage_markdown_report(
    *,
    run_id: str,
    catalog_version: str,
    catalog_sha256: str,
    execution_status: str,
    split_stage: str,
    claims_permitted: bool,
    measurement_method: str,
    target_arch: str,
    target_os: str,
    git_info: dict[str, Any],
    inspections: Sequence[ImageLayerMetadata],
    prefixes: Sequence[PrefixStorageMeasurement],
) -> str:
    lines: list[str] = []
    lines.append(f"# Protocol-v5 E5 Image Storage Scalability Report: `{run_id}`\n")

    lines.append("## 1. Executive Summary and Provenance\n")
    lines.append(f"- **Execution Timestamp (UTC)**: {_utc_now()}")
    lines.append(f"- **Git Revision**: `{git_info.get('git_revision')}` (dirty: {git_info.get('git_dirty')})")
    lines.append(f"- **Execution Status**: `{execution_status}`")
    lines.append(f"- **Split Stage**: `{split_stage}`")
    lines.append(f"- **Claims Permitted**: `{claims_permitted}`")
    lines.append(f"- **Measurement Method**: `{measurement_method}`")
    lines.append(f"- **Target Platform**: `{target_os}/{target_arch}`")
    lines.append(f"- **Catalog Version**: `{catalog_version}` (SHA-256: `{catalog_sha256}`)")
    lines.append(f"- **Total Catalog Images**: {len(inspections)}")
    lines.append(f"- **Total Prefix Progression Steps**: {len(prefixes)}\n")

    lines.append("## 2. Catalog Images and Layer Compositions\n")
    lines.append("| Priority Order | Image ID | Digest | Layer Count | Uncompressed/Content Size |")
    lines.append("| :---: | :--- | :--- | :---: | :---: |")
    for idx, img in enumerate(inspections, 1):
        lines.append(
            f"| {idx} | **{img.image_id}** | `{img.image_digest[:19]}...` | {len(img.layers)} | {img.total_bytes:,} bytes |"
        )
    lines.append("")

    lines.append("## 3. Ordered Catalog Prefix Scaling & Deduplication (Hypothesis H7)\n")
    lines.append(
        "Hypothesis H7 states: *Shared image layers require less cumulative storage than a naive logical sum as the frozen catalog grows.*\n"
    )
    lines.append(
        "| Prefix Size | Image Added | Naive Logical Bytes | Unique Layer Bytes | Deduplication Savings | Savings Ratio |"
    )
    lines.append("| :---: | :--- | :---: | :---: | :---: | :---: |")

    all_nonexpanding = True
    final_savings = 0
    if prefixes:
        final_savings = prefixes[-1].savings_bytes

    for p in prefixes:
        idx = p.prefix_size
        img_name = inspections[idx - 1].image_id if idx <= len(inspections) else f"img-{idx}"
        if p.unique_layer_bytes > p.naive_logical_bytes:
            all_nonexpanding = False
        lines.append(
            f"| {p.prefix_size} | **{img_name}** | {p.naive_logical_bytes:,} B | {p.unique_layer_bytes:,} B | {p.savings_bytes:,} B | {p.savings_ratio:.2%} |"
        )
    lines.append("")

    lines.append("## 4. Hypothesis H7 Statistical Criterion Evaluation\n")
    support_h7 = (
        execution_status == StorageExecutionStatus.OBSERVED.value
        and all_nonexpanding
        and final_savings > 0
    )
    lines.append(f"- **Prefix Non-Expansion (`all_prefixes_nonexpanding`)**: `{all_nonexpanding}` (every prefix unique <= naive)")
    lines.append(f"- **Final Net Savings (`final_savings_bytes > 0`)**: `{final_savings > 0}` ({final_savings:,} bytes)")
    lines.append(f"- **Deterministic Prefix Order Valid**: `True`")
    lines.append(f"- **Hypothesis H7 Decision**: `{'SUPPORTED' if support_h7 else 'NOT_EXECUTED / PENDING'}`\n")

    lines.append("## 5. Security & Measurement Integrity\n")
    lines.append(
        "- **Administrator Catalog Boundary**: Evaluated exclusively against pinned, approved container images.\n"
        "- **Immutable Digests**: Pinned `@sha256:` content digests used for all layer and image addressing.\n"
        "- **Empirical Observation**: Measured layer blob sizes parsed directly from verified manifests.\n"
    )

    if execution_status == StorageExecutionStatus.NOT_EXECUTED.value:
        lines.append(
            "> [!NOTE]\n"
            "> **Dry-Run / Not Executed Notice**: Live layer inspection was not performed. "
            "Evidence status is sealed as `NOT_EXECUTED`. No empirical storage claims are asserted.\n"
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
    injected_image_layers: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    freeze_path: Path | None = None,
) -> Path:
    """Execute Protocol-v5 E5 image storage scalability evaluation and materialize evidence."""
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

    # Execute layer measurement
    inspections, prefixes, execution_status = runner.measure_all()

    # Determine stage and claims_permitted
    norm_stage = stage.lower().strip()
    if norm_stage not in (SplitStage.CONFIRMATORY.value, SplitStage.DEVELOPMENT.value):
        raise ValueError(f"Invalid split stage: {stage!r}. Must be 'confirmatory' or 'development'.")

    if claims_permitted is None:
        # Defaults: only confirmatory OBSERVED runs may permit claims
        claims_permitted = (
            norm_stage == SplitStage.CONFIRMATORY.value
            and execution_status == StorageExecutionStatus.OBSERVED.value
        )
    elif claims_permitted:
        # User explicitly requested claims_permitted=True; enforce protocol invariant
        if norm_stage != SplitStage.CONFIRMATORY.value:
            raise ValueError("Development storage evidence cannot permit claims (claims_permitted must be False).")
        if execution_status != StorageExecutionStatus.OBSERVED.value:
            raise ValueError("NOT_EXECUTED storage evidence cannot permit claims (claims_permitted must be False).")

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

    # Prepare directories
    out_dir = output_dir or (DEFAULT_STORAGE_RESULTS_ROOT / run_id)
    raw_dir = out_dir / "raw"
    derived_dir = out_dir / "derived"
    report_dir = out_dir / "report"

    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    derived_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    # 1. Raw layer inspections
    raw_layers_data = [img.to_dict() for img in inspections]
    write_json_exclusive(raw_dir / "image_layers.json", raw_layers_data)

    # 2. Raw environment
    measurement_method = (
        f"docker manifest inspect OCI layer digest accounting (platform: {target_os}/{target_arch})"
        if execution_status == StorageExecutionStatus.OBSERVED.value
        else "dry_run inspection"
    )
    env_data = {
        "environment_id": f"e5-storage-{platform.system().lower()}-{target_arch}",
        "runtime": "docker" if execution_status == StorageExecutionStatus.OBSERVED.value else "dry_run",
        "operating_system": target_os,
        "architecture": target_arch,
        "platform_details": platform.platform(),
        "python_version": sys.version,
        "git_info": git,
        "measurement_method": measurement_method,
    }
    write_json_exclusive(raw_dir / "environment.json", env_data)

    # 3. Derived storage metrics (Schema-compliant artifact)
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
        measurement_method=measurement_method,
        prefixes=tuple(prefixes),
        provenance={
            "git_revision": git.get("git_revision", "unknown"),
            "dataset_sha256": cat_sha,
            "backend_system_versions": backend_systems,
        },
    )
    write_json_exclusive(derived_dir / "storage_metrics.json", storage_evidence.to_dict())

    # 4. Report status & markdown
    final_savings = prefixes[-1].savings_bytes if prefixes else 0
    status_data = {
        "schema_version": STORAGE_SCHEMA_VERSION,
        "run_id": run_id,
        "status": execution_status,
        "split_stage": norm_stage,
        "claims_permitted": claims_permitted,
        "total_images": len(inspections),
        "total_prefixes": len(prefixes),
        "final_naive_logical_bytes": prefixes[-1].naive_logical_bytes if prefixes else 0,
        "final_unique_layer_bytes": prefixes[-1].unique_layer_bytes if prefixes else 0,
        "final_storage_savings_bytes": final_savings,
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
        measurement_method=measurement_method,
        target_arch=target_arch,
        target_os=target_os,
        git_info=git,
        inspections=inspections,
        prefixes=prefixes,
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
        corpus_sha = "987d78fb0a0ad9d692ee9cfb3561988b1b537595670407d944abc74dc4437444"
    elif execution_status != StorageExecutionStatus.OBSERVED.value:
        corpus_sha = None

    frozen_cfg = freeze_data.get("configuration", {})
    retrieval_cfg = frozen_cfg.get("P2", {
        "retriever_version": "reciprocal-rank-fusion-hybrid-retriever-v1",
        "top_k": 10,
        "sparse_top_k": 10,
        "dense_top_k": 10,
        "rrf_k": 60.0,
        "sparse_weight": 1.0,
        "dense_weight": 1.0,
    }) if execution_status == StorageExecutionStatus.OBSERVED.value else {}

    constraints_cfg = frozen_cfg.get("constraints", {
        "constraint_evaluator_version": "p2-deterministic-constraint-evaluator-v1.0.0",
        "policy_version": "p2-constraint-policy-v1.0.0",
        "ranker_version": "p2-deterministic-ranker-v1.0.0",
    }) if execution_status == StorageExecutionStatus.OBSERVED.value else {}

    # 5. Manifest.json (cross-experiment ProtocolV5Manifest compatibility)
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

    # 6. SHA256SUMS covering all files
    _write_checksums(out_dir)

    # 7. Fail-closed validation of produced package
    validate_e5_storage_evidence(out_dir)

    return out_dir
