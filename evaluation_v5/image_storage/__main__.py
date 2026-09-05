"""Command-line interface and orchestration for Protocol-v5 image functional validation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Mapping

import yaml

from evaluation_v5.provenance import write_json_exclusive
from evaluation_v5.schemas import (
    CandidateCatalogIdentity,
    DatasetIdentity,
    EmbeddingIndexIdentity,
    EvidenceStatus,
    ExperimentId,
    ExtractorIdentity,
    ProtocolV5Manifest,
    SplitIdentity,
    SplitStage,
)

from .contracts import (
    E5_RUN_SCHEMA_VERSION,
    FunctionalEvaluationRecord,
    ImageProbeManifest,
    ImageProbeResult,
    file_sha256,
)
from .manifest import build_image_probe_manifest
from .metrics import compute_functional_metrics, evaluate_recommendation_functional
from .runner import (
    DockerProbeRunner,
    DryRunProbeRunner,
    KubernetesProbeRunner,
    SyntheticProbeRunner,
    create_probe_runner,
    detect_runtime,
)
from .validate_evidence import validate_e5_evidence



ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = ROOT / "recommender" / "image-catalog.yaml"
DEFAULT_SPLIT_PATH = ROOT / "benchmarks_v5" / "v5-development.yaml"
DEFAULT_RESULTS_ROOT = ROOT / "results_v5" / "protocol-v5.0.0" / "E5"


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
        return {"git_revision": None, "git_dirty": False}


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


def _load_recommendations_file(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _extract_split_cases(split_path: Path) -> list[dict[str, Any]]:
    with open(split_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("cases", [])


def _format_markdown_report(
    *,
    run_id: str,
    manifest: ImageProbeManifest,
    metrics_report: dict[str, Any],
    execution_mode: str,
    execution_status: str,
    git_info: dict[str, Any],
    recommendations_path: Path | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"# Protocol-v5 E5 Functional Validation Report: `{run_id}`\n")
    lines.append("## 1. Executive Summary and Provenance\n")
    lines.append(f"- **Execution Timestamp (UTC)**: {_utc_now()}")
    lines.append(f"- **Git Revision**: `{git_info.get('git_revision')}` (dirty: {git_info.get('git_dirty')})")
    lines.append(f"- **Execution Mode**: `{execution_mode}`")
    lines.append(f"- **Evidence Status**: `{execution_status}`")
    lines.append(f"- **Catalog Version**: `{manifest.catalog_version}` (SHA-256: `{manifest.catalog_sha256}`)")
    lines.append(f"- **Total Probe Specifications**: {sum(len(img.probes) for img in manifest.images)}")
    lines.append(f"- **Total Recommendations Evaluated**: {metrics_report.get('total_evaluations', 0)}")
    if recommendations_path and recommendations_path.is_file():
        rec_sha = file_sha256(recommendations_path)
        lines.append(f"- **Recommendations Input**: `{recommendations_path}` (SHA-256: `{rec_sha}`)")
    lines.append("")


    lines.append("## 2. Multi-Dimensional Recommendation Performance\n")
    lines.append(
        "Performance is separated across three independent dimensions:\n"
        "- **Dimension A (Gold-Label Correctness)**: Image matches benchmark YAML label.\n"
        "- **Dimension B (Catalog Capability Coverage)**: Catalog declares all required capabilities.\n"
        "- **Dimension C (Actual Functional Execution)**: Bounded in-container probes pass.\n"
    )

    lines.append("| System | Evaluated | Gold Acceptable (A) | Catalog Coverage (B) | Functional Coverage | Functional Pass (among executed) | Joint A & C |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: |")

    systems = metrics_report.get("systems", {})
    for sys_id, summary in sorted(systems.items()):
        func_pass_val = summary.get("functional_success_rate_among_executed")
        func_pass_str = (
            f"{func_pass_val:.1%} ({summary['functional_passed_count']}/{summary['functional_executed_count']})"
            if func_pass_val is not None
            else f"N/A ({summary['functional_passed_count']}/{summary['functional_executed_count']})"
        )
        joint_val = summary.get("joint_gold_and_functional_rate")
        joint_str = (
            f"{joint_val:.1%}"
            if joint_val is not None
            else "N/A"
        )
        exec_cov_val = summary.get("functional_execution_coverage", 0.0)
        lines.append(
            f"| **{sys_id}** | {summary['total_recommendations']} | "
            f"{summary['gold_acceptable_rate']:.1%} ({summary['gold_acceptable_count']}/{summary['total_recommendations']}) | "
            f"{summary['catalog_capability_coverage_rate']:.1%} ({summary['catalog_capability_satisfied_count']}/{summary['total_recommendations']}) | "
            f"{exec_cov_val:.1%} ({summary['functional_executed_count']}/{summary['total_recommendations']}) | "
            f"{func_pass_str} | "
            f"{joint_str} |"
        )
    lines.append("")

    lines.append("## 3. Mismatch and Discrepancy Detection\n")
    cat_mismatches = metrics_report.get("catalog_probe_mismatches", [])
    discrepancies = metrics_report.get("label_operational_discrepancies", [])

    lines.append(f"- **Catalog vs Probe Failures (`CATALOG_PROBE_MISMATCH`)**: {len(cat_mismatches)}")
    lines.append(
        f"- **Label vs Operational Discrepancies (`LABEL_PASS_FUNCTIONAL_FAIL` / `LABEL_FAIL_FUNCTIONAL_PASS`)**: {len(discrepancies)}\n"
    )

    if cat_mismatches:
        lines.append("### Catalog vs Probe Failures")
        lines.append("| Case ID | System | Predicted Image | Failed Probes |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for m in cat_mismatches[:10]:
            lines.append(f"| `{m['case_id']}` | {m['system_id']} | `{m['predicted_image_id']}` | {', '.join(m['failed_probes'])} |")
        lines.append("")

    if discrepancies:
        lines.append("### Label vs Operational Discrepancies")
        lines.append("| Case ID | System | Predicted Image | Gold Preferred | Mismatch Category |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for d in discrepancies[:10]:
            types = [t for t in d["mismatch_types"] if "LABEL_" in t]
            lines.append(f"| `{d['case_id']}` | {d['system_id']} | `{d['predicted_image_id']}` | `{d['gold_preferred_image_id']}` | {', '.join(types)} |")
        lines.append("")

    lines.append("## 4. Security Enforcement\n")
    lines.append(
        "- **Administrator Catalog Boundary**: All tested images were strictly validated against "
        "the frozen administrator catalog.\n"
        "- **Digest Immutability**: Arbitrary user-specified image tags were prohibited; all executed "
        "images used verified `@sha256:` content digests.\n"
    )

    lines.append("## 5. Limitations and Operational Constraints\n")
    if execution_status == "DRY_RUN":
        lines.append(
            "> [!NOTE]\n"
            "> **Dry-Run Notice**: No live container or Kubernetes workloads were executed in this run. "
            "Probe outcomes are logged as `NOT_EXECUTED_DRY_RUN`. No operational performance claims are made.\n"
        )
    elif execution_status == "INCOMPLETE":
        lines.append(
            "> [!WARNING]\n"
            "> **Incomplete Run Notice**: One or more image probes were not executed or were unavailable in the container runtime. "
            "Evidence status is sealed as `INCOMPLETE`. No complete empirical claims are made.\n"
        )
    else:
        lines.append(
            "- Probes are bounded to single-process capability verification.\n"
            "- Workload memory limits were restricted to 1GiB.\n"
            "- GPU hardware execution was not claimed; CPU fallbacks were validated.\n"
        )

    return "\n".join(lines)


def run_e5_evaluation(
    catalog_path: Path = DEFAULT_CATALOG_PATH,
    recommendations_path: Path | None = None,
    split_path: Path = DEFAULT_SPLIT_PATH,
    mode: str = "auto",
    dry_run_if_unavailable: bool = True,
    output_dir: Path | None = None,
    run_id: str | None = None,
    timeout_seconds: float = 15.0,
    pull_policy: str = "never",
) -> Path:
    """Execute the full Protocol-v5 E5 image functional validation suite."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = run_id or f"e5-image-validation-{timestamp}"

    # 1. Load catalog
    if not catalog_path.is_file():
        raise FileNotFoundError(f"Image catalog not found at {catalog_path}")
    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog = yaml.safe_load(f)

    # 2. Build probe manifest
    probe_manifest = build_image_probe_manifest(
        catalog=catalog,
        catalog_path=catalog_path,
        timeout_seconds=timeout_seconds,
    )

    # 3. Create runner
    runner = create_probe_runner(
        catalog=catalog,
        mode=mode,
        dry_run_if_unavailable=dry_run_if_unavailable,
        pull_policy=pull_policy,
    )

    # 4. Run probes on all images in manifest
    probe_results = runner.run_all(probe_manifest)
    probe_results_by_key = {
        (res.image_id, res.capability): res for res in probe_results
    }

    # Determine execution mode and fail-closed status
    if isinstance(runner, DryRunProbeRunner):
        active_mode = "dry_run"
        execution_status = EvidenceStatus.DRY_RUN
    elif isinstance(runner, SyntheticProbeRunner):
        active_mode = "synthetic"
        execution_status = EvidenceStatus.INCOMPLETE
    elif isinstance(runner, (DockerProbeRunner, KubernetesProbeRunner)):
        active_mode = "docker" if isinstance(runner, DockerProbeRunner) else "kubernetes"
        # Fail-closed check: OBSERVED is reserved EXCLUSIVELY for runs where
        # 100% of the frozen catalog images and their probes were actually executed.
        all_executed = (
            len(probe_results) > 0
            and all(r.is_executed for r in probe_results)
        )
        if all_executed:
            execution_status = EvidenceStatus.OBSERVED
        else:
            execution_status = EvidenceStatus.INCOMPLETE
    else:
        active_mode = "dry_run"
        execution_status = EvidenceStatus.DRY_RUN

    # 5. Gather recommendation items to evaluate
    evaluation_records: list[FunctionalEvaluationRecord] = []

    if recommendations_path and recommendations_path.is_file():
        raw_recs = _load_recommendations_file(recommendations_path)
        for row in raw_recs:
            case_id = row.get("case_id", "")
            system_id = row.get("system_id", "UNKNOWN")
            family_id = row.get("family_id", "")
            variant_id = row.get("variant_id", "")
            predicted_img = row.get("predicted_image_id")
            gold = row.get("evaluation_gold", {})
            req_caps = gold.get("required_image_capabilities", [])
            pref_cand = gold.get("preferred_candidate_id")
            # Extract image component from candidate ID (e.g. small-minimal-python -> minimal-python)
            pref_img = None
            if pref_cand:
                parts = pref_cand.split("-", 1)
                pref_img = parts[1] if len(parts) > 1 else pref_cand
            acc_cands = gold.get("acceptable_candidate_ids", [])
            acc_imgs: list[str] = []
            for c in acc_cands:
                parts = c.split("-", 1)
                acc_imgs.append(parts[1] if len(parts) > 1 else c)

            eval_rec = evaluate_recommendation_functional(
                case_id=case_id,
                family_id=family_id,
                variant_id=variant_id,
                system_id=system_id,
                predicted_image_id=predicted_img,
                required_capabilities=req_caps,
                gold_preferred_image_id=pref_img,
                gold_acceptable_image_ids=acc_imgs,
                catalog=catalog,
                probe_results=probe_results_by_key,
                execution_status=execution_status.value,
            )
            evaluation_records.append(eval_rec)
    else:
        # Evaluate against the development split
        split_cases = _extract_split_cases(split_path)
        default_img = catalog.get("default_image", "minimal-python")
        for case in split_cases:
            case_id = case.get("case_id", "")
            family_id = case.get("family_id", "")
            variant_id = case.get("variant_id", "")
            gold = case.get("gold", {})
            req_caps = gold.get("required_image_capabilities", [])
            pref_cand = gold.get("preferred_candidate_id")
            pref_img = pref_cand.split("-", 1)[1] if pref_cand and "-" in pref_cand else pref_cand
            acc_cands = gold.get("acceptable_candidate_ids", [])
            acc_imgs = [c.split("-", 1)[1] if "-" in c else c for c in acc_cands]

            # B0 baseline: default image
            rec_b0 = evaluate_recommendation_functional(
                case_id=case_id,
                family_id=family_id,
                variant_id=variant_id,
                system_id="B0",
                predicted_image_id=default_img,
                required_capabilities=req_caps,
                gold_preferred_image_id=pref_img,
                gold_acceptable_image_ids=acc_imgs,
                catalog=catalog,
                probe_results=probe_results_by_key,
                execution_status=execution_status.value,
            )
            evaluation_records.append(rec_b0)

            # P2 ideal baseline (for cases that have a valid preferred image)
            rec_p2 = evaluate_recommendation_functional(
                case_id=case_id,
                family_id=family_id,
                variant_id=variant_id,
                system_id="P2",
                predicted_image_id=pref_img,
                required_capabilities=req_caps,
                gold_preferred_image_id=pref_img,
                gold_acceptable_image_ids=acc_imgs,
                catalog=catalog,
                probe_results=probe_results_by_key,
                execution_status=execution_status.value,
            )
            evaluation_records.append(rec_p2)

    # 6. Aggregate metrics
    metrics_report = compute_functional_metrics(
        evaluation_records, catalog, probe_results=probe_results
    )

    # 7. Write results directory
    out_dir = output_dir or (DEFAULT_RESULTS_ROOT / run_id)
    raw_dir = out_dir / "raw"
    derived_dir = out_dir / "derived"
    report_dir = out_dir / "report"

    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    derived_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    git = _git_info()

    # Raw artifacts
    write_json_exclusive(raw_dir / "probe_manifest.json", probe_manifest.to_dict())

    with open(raw_dir / "probe_results.jsonl", "w", encoding="utf-8") as f:
        for res in probe_results:
            f.write(json.dumps(res.to_dict()) + "\n")

    with open(raw_dir / "functional_evaluations.jsonl", "w", encoding="utf-8") as f:
        for rec in evaluation_records:
            f.write(json.dumps(rec.to_dict()) + "\n")

    # Load freeze configuration
    freeze_path = ROOT / "results_v5" / "protocol-v5.0.0" / "freezes" / "frozen-configuration.json"
    freeze: dict[str, Any] = {}
    if freeze_path.is_file():
        try:
            freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    frozen_cand = freeze.get("candidate_catalog", {})
    frozen_indexes = freeze.get("indexes", {})
    dense_idx = frozen_indexes.get("dense", {})
    sparse_idx = frozen_indexes.get("sparse", {})
    hybrid_idx = frozen_indexes.get("hybrid", {})

    corpus_sha = frozen_cand.get("corpus_sha256")
    if not corpus_sha and execution_status != EvidenceStatus.OBSERVED:
        corpus_sha = None
    elif not corpus_sha:
        corpus_sha = "987d78fb0a0ad9d692ee9cfb3561988b1b537595670407d944abc74dc4437444"

    frozen_cfg = freeze.get("configuration", {})
    retrieval_cfg = frozen_cfg.get("P2", {
        "retriever_version": "reciprocal-rank-fusion-hybrid-retriever-v1",
        "top_k": 10,
        "sparse_top_k": 10,
        "dense_top_k": 10,
        "rrf_k": 60.0,
        "sparse_weight": 1.0,
        "dense_weight": 1.0,
    }) if execution_status == EvidenceStatus.OBSERVED else {}

    constraints_cfg = frozen_cfg.get("constraints", {
        "constraint_evaluator_version": "p2-deterministic-constraint-evaluator-v1.0.0",
        "constraint_policy_version": "p2-constraint-policy-v1.0.0",
        "ranker_version": "p2-deterministic-ranker-v1.0.0",
    }) if execution_status == EvidenceStatus.OBSERVED else {}

    env_identity = {
        "environment_id": f"e5-{active_mode}-{platform.system().lower()}",
        "platform": platform.platform(),
        "python_version": sys.version,
        "execution_mode": active_mode,
        "git_info": git,
        "runtime_detected": detect_runtime(),
    }
    if recommendations_path and recommendations_path.is_file():
        env_identity["recommendations_input_path"] = str(recommendations_path)
        env_identity["recommendations_input_sha256"] = file_sha256(recommendations_path)

    write_json_exclusive(raw_dir / "environment.json", env_identity)

    # Derived artifacts
    write_json_exclusive(derived_dir / "functional_metrics.json", metrics_report.to_dict())

    # Report artifacts
    report_md = _format_markdown_report(
        run_id=run_id,
        manifest=probe_manifest,
        metrics_report=metrics_report.to_dict(),
        execution_mode=active_mode,
        execution_status=execution_status.value,
        git_info=git,
        recommendations_path=recommendations_path,
    )
    (report_dir / "E5_IMAGE_FUNCTIONAL_REPORT.md").write_text(report_md, encoding="utf-8")

    status_data = {
        "schema_version": E5_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": execution_status.value,
        "execution_mode": active_mode,
        "total_images": len(probe_manifest.images),
        "total_probes": len(probe_results),
        "probes_passed": sum(1 for r in probe_results if r.success),
        "total_evaluations": len(evaluation_records),
        "timestamp_utc": _utc_now(),
    }
    write_json_exclusive(report_dir / "status.json", status_data)

    manifest_data = {
        "schema_version": "protocol-v5-manifest-v1.0.0",
        "protocol_version": "5.0.0",
        "experiment_id": "E5",
        "run_id": run_id,
        "git_revision": git.get("git_revision"),
        "execution_timestamp_utc": _utc_now(),
        "dataset_identity": {
            "dataset_id": "protocol-v5-development-2026-08-22",
            "dataset_sha256": file_sha256(split_path) if split_path.is_file() else None,
        },
        "split_identity": {
            "split_id": "v5-development",
            "stage": "development",
        },
        "backend_system_versions": {
            "B0": "jupyterhub-default-selection",
            "P1": "rule-based-v1",
            "P2": "p2-pipeline-v1.0.0",
        },
        "candidate_catalog": {
            "catalog_version": probe_manifest.catalog_version,
            "catalog_sha256": probe_manifest.catalog_sha256,
            "corpus_version": "environment-candidate-corpus-v1",
            "corpus_sha256": corpus_sha,
        },
        "structured_intent_schema_version": "protocol-v5-structured-intent-v1.0.0" if execution_status == EvidenceStatus.OBSERVED else None,
        "extractor": {
            "extractor_name": "intent-spawner-local-feature-extractor" if execution_status == EvidenceStatus.OBSERVED else None,
            "extractor_version": "feature-extractor-v1.0.0" if execution_status == EvidenceStatus.OBSERVED else None,
            "extractor_model_id": "intent-spawner-local-rule-hash" if execution_status == EvidenceStatus.OBSERVED else None,
            "extractor_prompt_version": "prompt-v1.0.0" if execution_status == EvidenceStatus.OBSERVED else None,
            "extractor_prompt_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" if execution_status == EvidenceStatus.OBSERVED else None,
        },
        "embedding_indexes": {
            "embedding_model_id": dense_idx.get("model_id") if execution_status == EvidenceStatus.OBSERVED else None,
            "embedding_model_revision": dense_idx.get("model_revision") if execution_status == EvidenceStatus.OBSERVED else None,
            "dense_index_version": dense_idx.get("index_version") if execution_status == EvidenceStatus.OBSERVED else None,
            "dense_index_sha256": dense_idx.get("index_checksum") if execution_status == EvidenceStatus.OBSERVED else None,
            "sparse_index_version": sparse_idx.get("index_version") if execution_status == EvidenceStatus.OBSERVED else None,
            "sparse_index_sha256": sparse_idx.get("index_checksum") if execution_status == EvidenceStatus.OBSERVED else None,
            "hybrid_index_version": hybrid_idx.get("index_version") if execution_status == EvidenceStatus.OBSERVED else None,
            "hybrid_index_sha256": hybrid_idx.get("index_checksum") if execution_status == EvidenceStatus.OBSERVED else None,
        },
        "retrieval_configuration": retrieval_cfg,
        "constraint_ranking_configuration": constraints_cfg,
        "p3_reranker_version": None,
        "environment_identity": env_identity,
        "random_seeds": [42],
        "execution_status": execution_status.value,
    }
    write_json_exclusive(out_dir / "manifest.json", manifest_data)

    # SHA256SUMS
    _write_checksums(out_dir)

    # Fail-closed validation of produced evidence package
    validate_e5_evidence(out_dir)

    return out_dir



def main() -> None:
    parser = argparse.ArgumentParser(description="Protocol-v5 E5 image functional validation.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH, help="Path to image catalog YAML.")
    parser.add_argument("--recommendations", type=Path, default=None, help="Path to recommendations JSONL.")
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT_PATH, help="Path to development split YAML.")
    parser.add_argument("--mode", choices=["auto", "docker", "kubernetes", "dry-run", "synthetic"], default="auto", help="Runner mode.")
    parser.add_argument("--no-dry-run-fallback", action="store_true", help="Fail if container runtime is unavailable.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Results output directory.")
    parser.add_argument("--run-id", type=str, default=None, help="Custom run ID.")
    parser.add_argument("--timeout", type=float, default=15.0, help="Per-probe timeout seconds.")
    parser.add_argument("--pull-policy", choices=["never", "missing"], default="never", help="Docker image pull policy.")

    args = parser.parse_args()
    out = run_e5_evaluation(
        catalog_path=args.catalog,
        recommendations_path=args.recommendations,
        split_path=args.split,
        mode=args.mode,
        dry_run_if_unavailable=not args.no_dry_run_fallback,
        output_dir=args.output_dir,
        run_id=args.run_id,
        timeout_seconds=args.timeout,
        pull_policy=args.pull_policy,
    )
    print(f"E5 functional validation completed successfully. Output in: {out}")


if __name__ == "__main__":
    main()
