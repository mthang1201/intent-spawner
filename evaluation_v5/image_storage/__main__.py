"""Command-line interface and orchestration for Protocol-v5 image functional validation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Mapping

import yaml

from evaluation_v5.provenance import write_json_exclusive
from evaluation_v5.schemas import EvidenceStatus
from evaluation_v5.offline.source_run import (
    VerifiedRecommendationRunProvenance,
    verify_recommendation_run_provenance,
)

from .contracts import (
    E5_RUN_SCHEMA_VERSION,
    FunctionalEvaluationRecord,
    ImageProbeManifest,
    SecurityVerificationError,
    file_sha256,
)
from .manifest import build_image_probe_manifest
from .metrics import compute_functional_metrics, evaluate_recommendation_functional
from .functional_provenance import (
    SOURCE_RECOMMENDATIONS_FILENAME,
    SOURCE_RECOMMENDATION_PROVENANCE_FILENAME,
    bind_recommendation_source,
    canonical_identity_sha256,
    selected_image_identity,
    source_manifest_identities,
)
from .runner import (
    DockerProbeRunner,
    DryRunProbeRunner,
    KubernetesProbeRunner,
    SyntheticProbeRunner,
    create_probe_runner,
    detect_runtime,
)
from .storage_orchestrator import run_storage_evaluation
from .validate_evidence import validate_e5_evidence, validate_e5_storage_evidence



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


def _write_bytes_exclusive(path: Path, payload: bytes) -> None:
    """Publish immutable raw bytes without permitting silent replacement."""
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _format_markdown_report(
    *,
    run_id: str,
    manifest: ImageProbeManifest,
    metrics_report: dict[str, Any],
    execution_mode: str,
    execution_status: str,
    git_info: dict[str, Any],
    source_recommendation_run: Mapping[str, Any] | None = None,
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
    if source_recommendation_run:
        lines.append(
            "- **Source Recommendation Run**: "
            f"`{source_recommendation_run['run_id']}` "
            f"(recommendations SHA-256: `{source_recommendation_run['recommendation_run_sha256']}`)"
        )
    lines.append("")


    lines.append("## 2. Multi-Dimensional Recommendation Performance\n")
    lines.append(
        "Performance is separated across three independent dimensions:\n"
        "- **Dimension A (Gold-Label Correctness)**: Recommendation matches benchmark gold label.\n"
        "- **Dimension B (Catalog Capability Coverage)**: Administrator image catalog declares all required workload capabilities.\n"
        "- **Dimension C (Actual Functional Execution)**: In-container functional capability probes pass when executed.\n"
    )

    lines.append("### Dimension A: Gold-Label Benchmark Correctness\n")
    lines.append("| System | Total Cases | Preferred Match | Preferred Rate | Acceptable Match | Acceptable Rate |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: |")

    systems = metrics_report.get("systems", {})
    for sys_id, summary in sorted(systems.items()):
        n = summary["total_recommendations"]
        pref_cnt = summary.get("gold_preferred_count", 0)
        pref_rate = summary.get("gold_preferred_rate", 0.0)
        acc_cnt = summary.get("gold_acceptable_count", 0)
        acc_rate = summary.get("gold_acceptable_rate", 0.0)
        lines.append(
            f"| **{sys_id}** | {n} | {pref_cnt}/{n} | {pref_rate:.1%} | {acc_cnt}/{n} | {acc_rate:.1%} |"
        )
    lines.append("")

    lines.append("### Dimensions B & C: Catalog Coverage, Execution, and Operational Adequacy\n")
    lines.append("| System | Total | With Image | Catalog Covered (B) | Functional Eligible | Functional Executed | Functional Pass (C) | Operational Adequacy | Joint (A & C) |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")

    for sys_id, summary in sorted(systems.items()):
        func_pass_val = summary.get("functional_success_rate_among_executed")
        func_pass_str = (
            f"{func_pass_val:.1%} ({summary['functional_passed_count']}/{summary['functional_executed_count']})"
            if func_pass_val is not None
            else f"N/A ({summary['functional_passed_count']}/{summary['functional_executed_count']})"
        )
        op_adeq_val = summary.get("operational_adequacy_rate", 0.0)
        exec_cov_val = summary.get("functional_execution_coverage", 0.0)
        joint_val = summary.get("joint_gold_and_functional_rate")
        joint_str = f"{joint_val:.1%}" if joint_val is not None else "N/A"
        lines.append(
            f"| **{sys_id}** | {summary['total_recommendations']} | "
            f"{summary.get('recommendations_with_image_count', summary['total_recommendations'])} | "
            f"{summary['catalog_capability_coverage_rate']:.1%} ({summary['catalog_capability_satisfied_count']}/{summary['total_recommendations']}) | "
            f"{summary.get('functional_validation_eligible_count', summary['catalog_capability_satisfied_count'])} | "
            f"{exec_cov_val:.1%} ({summary['functional_executed_count']}/{summary.get('functional_validation_eligible_count', summary['catalog_capability_satisfied_count'])}) | "
            f"{func_pass_str} | "
            f"{op_adeq_val:.1%} ({summary.get('operationally_adequate_count', summary['functional_passed_count'])}/{summary['total_recommendations']}) | "
            f"{joint_str} |"
        )
    lines.append("")

    lines.append("## 3. Mismatch and Discrepancy Detection\n")
    cat_mismatches = metrics_report.get("catalog_probe_mismatches", [])
    cat_underclaims = metrics_report.get("catalog_underclaims", [])
    discrepancies = metrics_report.get("label_operational_discrepancies", [])

    lines.append(f"- **Catalog vs Probe Failures (`CATALOG_PROBE_MISMATCH`)**: {len(cat_mismatches)}")
    lines.append(f"- **Catalog Underclaim Functional Pass (`CATALOG_UNDERCLAIM_FUNCTIONAL_PASS`)**: {len(cat_underclaims)}")
    lines.append(
        f"- **Label vs Operational Discrepancies (`LABEL_PASS_FUNCTIONAL_FAIL` / `LABEL_FAIL_FUNCTIONAL_PASS`)**: {len(discrepancies)}\n"
    )

    if cat_underclaims:
        lines.append("### Catalog Underclaim (Metadata Absent, Empirical Probe Passed)")
        lines.append("| Case ID | System | Predicted Image | Underclaimed Capabilities |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for u in cat_underclaims[:10]:
            lines.append(f"| `{u['case_id']}` | {u['system_id']} | `{u['predicted_image_id']}` | {', '.join(u.get('missing_catalog_capabilities', []))} |")
        lines.append("")

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
    recommendation_run: VerifiedRecommendationRunProvenance | None = None,
    mode: str = "auto",
    dry_run_if_unavailable: bool = True,
    output_dir: Path | None = None,
    run_id: str | None = None,
    timeout_seconds: float = 15.0,
    pull_policy: str = "never",
) -> Path:
    """Execute the full Protocol-v5 E5 image functional validation suite."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if recommendation_run is None:
        raise TypeError(
            "E5 functional evaluation requires the verified originating "
            "recommendation-run provenance capability"
        )

    # 1. Load catalog
    if not catalog_path.is_file():
        raise FileNotFoundError(f"Image catalog not found at {catalog_path}")
    catalog_file_sha256 = file_sha256(catalog_path)
    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog = yaml.safe_load(f)

    # Reverify Prompt 3's capability, exact record checksum, record IDs, and
    # catalog identity before any container or pod can be created.
    source = bind_recommendation_source(recommendation_run, catalog=catalog)
    source_identities = source_manifest_identities(source)

    # 2. Build probe manifest
    probe_manifest = build_image_probe_manifest(
        catalog=catalog,
        catalog_path=catalog_path,
        timeout_seconds=timeout_seconds,
    )
    if (
        probe_manifest.catalog_sha256 != catalog_file_sha256
        or file_sha256(catalog_path) != catalog_file_sha256
    ):
        raise SecurityVerificationError(
            "image catalog changed while E5 established its execution boundary"
        )

    # 3. Create runner
    runner = create_probe_runner(
        catalog=catalog,
        mode=mode,
        dry_run_if_unavailable=dry_run_if_unavailable,
        pull_policy=pull_policy,
    )

    if not run_id:
        if type(runner) is DryRunProbeRunner:
            run_id = f"e5-image-validation-dry-run-{timestamp}"
        elif type(runner) is SyntheticProbeRunner:
            run_id = f"e5-image-validation-synthetic-{timestamp}"
        else:
            run_id = f"e5-image-validation-{timestamp}"
    out_dir = output_dir or (DEFAULT_RESULTS_ROOT / run_id)
    if out_dir.exists():
        raise FileExistsError(f"E5 result directory already exists: {out_dir}")

    # 4. Run probes on all images in manifest
    probe_results = runner.run_all(probe_manifest)
    probe_results_by_key = {
        (res.image_id, res.capability): res for res in probe_results
    }

    # Determine execution mode and fail-closed status
    if type(runner) is DryRunProbeRunner:
        active_mode = "dry_run"
        execution_status = EvidenceStatus.DRY_RUN
    elif type(runner) is SyntheticProbeRunner:
        active_mode = "synthetic"
        execution_status = EvidenceStatus.INCOMPLETE
    elif type(runner) in (DockerProbeRunner, KubernetesProbeRunner):
        active_mode = "docker" if type(runner) is DockerProbeRunner else "kubernetes"
        # Fail-closed check: OBSERVED is reserved EXCLUSIVELY for runs where
        # 100% of the frozen catalog images and their probes were actually executed.
        all_executed = (
            len(probe_results) > 0
            and all(r.is_executed for r in probe_results)
        )
        expected_origin = "LIVE_DOCKER" if type(runner) is DockerProbeRunner else "LIVE_KUBERNETES"
        all_runtime_bound = all(
            r.execution_origin == expected_origin
            and r.cleanup_succeeded is True
            and bool(r.execution_identity)
            and bool(r.resolved_image_digest)
            and bool(r.resolved_image_platform)
            and bool(r.runtime_image_id)
            for r in probe_results
        )
        runner_is_live = getattr(runner, "execution_origin", None) == expected_origin
        if all_executed and all_runtime_bound and runner_is_live:
            execution_status = EvidenceStatus.OBSERVED
        else:
            execution_status = EvidenceStatus.INCOMPLETE
    else:
        active_mode = "dry_run"
        execution_status = EvidenceStatus.DRY_RUN

    # 5. Gather recommendation items to evaluate
    evaluation_records: list[FunctionalEvaluationRecord] = []
    candidates = {
        item["candidate_id"]: item
        for item in source.provenance["catalog_identity"]["candidates"]
    }
    for row in source.records:
        case_id = row.get("case_id", "")
        system_id = row.get("system_id", "UNKNOWN")
        family_id = row.get("family_id", "")
        variant_id = row.get("variant_id", "")
        source_img = row.get("predicted_image_id")
        source_candidate = row.get("predicted_candidate_id")
        predicted_img = source_img

        gold = row.get("evaluation_gold", {})
        req_caps = list(gold.get("required_image_capabilities", []))
        pref_cand = gold.get("preferred_candidate_id")
        pref = candidates.get(pref_cand) if pref_cand else None
        pref_img = pref.get("image_id") if isinstance(pref, Mapping) else None
        acc_cands = gold.get("acceptable_candidate_ids", [])
        acc_imgs = [candidates[c]["image_id"] for c in acc_cands if c in candidates]
        selected_digest, selected_platform = selected_image_identity(
            image_id=predicted_img,
            catalog=catalog,
            probe_results=probe_results,
        )

        eval_rec = evaluate_recommendation_functional(
            case_id=case_id,
            family_id=family_id,
            variant_id=variant_id,
            system_id=system_id,
            source_predicted_image_value=source_img,
            source_predicted_candidate_id=source_candidate,
            source_run_sha256=source.recommendation_run_sha256,
            source_recommendation_record_id=str(row["record_id"]),
            source_configuration_identity_sha256=canonical_identity_sha256(
                source.system_identity(system_id)
            ),
            selected_image_digest=selected_digest,
            selected_image_platform=selected_platform,
            predicted_image_id=predicted_img,
            required_capabilities=req_caps,
            gold_preferred_image_id=pref_img,
            gold_acceptable_image_ids=acc_imgs,
            catalog=catalog,
            probe_results=probe_results_by_key,
            execution_status=execution_status.value,
        )
        evaluation_records.append(eval_rec)

    # 6. Aggregate metrics
    metrics_report = compute_functional_metrics(
        evaluation_records, catalog, probe_results=probe_results
    )

    # 7. Write results directory
    raw_dir = out_dir / "raw"
    derived_dir = out_dir / "derived"
    report_dir = out_dir / "report"

    out_dir.mkdir(parents=True, exist_ok=False)
    raw_dir.mkdir()
    derived_dir.mkdir()
    report_dir.mkdir()

    git = _git_info()

    # Raw artifacts
    write_json_exclusive(raw_dir / "probe_manifest.json", probe_manifest.to_dict())
    write_json_exclusive(
        raw_dir / SOURCE_RECOMMENDATION_PROVENANCE_FILENAME,
        dict(source.provenance),
    )
    _write_bytes_exclusive(
        raw_dir / SOURCE_RECOMMENDATIONS_FILENAME,
        source.records_bytes,
    )

    with open(raw_dir / "probe_results.jsonl", "x", encoding="utf-8") as f:
        for res in probe_results:
            f.write(json.dumps(res.to_dict()) + "\n")

    with open(raw_dir / "functional_evaluations.jsonl", "x", encoding="utf-8") as f:
        for rec in evaluation_records:
            f.write(json.dumps(rec.to_dict()) + "\n")

    env_identity = {
        "environment_id": f"e5-{active_mode}-{platform.system().lower()}",
        "platform": platform.platform(),
        "python_version": sys.version,
        "execution_mode": active_mode,
        "git_info": git,
        "runtime_detected": detect_runtime(),
        "source_recommendation_run_id": source.provenance["run_id"],
        "source_recommendation_run_sha256": source.recommendation_run_sha256,
    }

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
        source_recommendation_run=source.provenance,
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
        **source_identities,
        "environment_identity": env_identity,
        "random_seeds": [],
        "execution_status": execution_status.value,
    }
    write_json_exclusive(out_dir / "manifest.json", manifest_data)

    # SHA256SUMS
    _write_checksums(out_dir)

    # Fail-closed validation of produced evidence package
    validate_e5_evidence(out_dir)

    return out_dir



def main() -> None:
    parser = argparse.ArgumentParser(description="Protocol-v5 E5 image functional and storage scalability evaluation.")
    parser.add_argument(
        "--experiment",
        choices=["functional", "storage", "both"],
        default="functional",
        help="Experiment to run: 'functional' (default), 'storage', or 'both'.",
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH, help="Path to image catalog YAML.")
    parser.add_argument(
        "--recommendation-run",
        type=Path,
        default=None,
        help="Originating validated offline recommendation evidence directory (required for functional E5).",
    )
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT_PATH, help="Path to development split YAML.")
    parser.add_argument(
        "--mode",
        choices=["auto", "docker", "docker-local", "local", "kubernetes", "dry-run"],
        default="auto",
        help="Runner mode.",
    )
    parser.add_argument("--no-dry-run-fallback", action="store_true", help="Fail if container runtime is unavailable.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Results output directory.")
    parser.add_argument("--run-id", type=str, default=None, help="Custom run ID.")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-probe or inspection timeout seconds.")
    parser.add_argument("--pull-policy", choices=["never", "missing"], default="never", help="Docker image pull policy.")
    parser.add_argument(
        "--stage",
        choices=["development", "confirmatory"],
        default="confirmatory",
        help="Split stage for storage evaluation (default: confirmatory).",
    )
    parser.add_argument(
        "--arch",
        choices=["amd64", "arm64"],
        default="amd64",
        help="Target CPU architecture for image storage manifest inspection (default: amd64).",
    )
    parser.add_argument(
        "--scales",
        type=int,
        nargs="+",
        default=[4, 8, 16],
        help="Catalog scales to evaluate (default: 4 8 16).",
    )
    parser.add_argument(
        "--no-recommendation-eval",
        action="store_true",
        help="Skip joint recommendation evaluation for catalog scales.",
    )
    parser.add_argument("--dataset", type=Path, default=None, help="Path to sealed confirmatory dataset YAML.")
    parser.add_argument("--freeze", type=Path, default=None, help="Path to frozen configuration/freeze artifact.")

    args = parser.parse_args()

    if args.experiment in ("functional", "both"):
        if args.recommendation_run is None:
            parser.error("--recommendation-run is required for functional E5")
        confirmatory_split = None
        if (args.dataset is None) != (args.freeze is None):
            parser.error("functional confirmatory verification requires both --dataset and --freeze")
        if args.dataset is not None and args.freeze is not None:
            from evaluation_v5.isolation import load_confirmatory_split

            confirmatory_split = load_confirmatory_split(args.dataset, args.freeze)
        recommendation_capability = verify_recommendation_run_provenance(
            args.recommendation_run,
            confirmatory_split=confirmatory_split,
        )
        out_func = run_e5_evaluation(
            catalog_path=args.catalog,
            recommendation_run=recommendation_capability,
            mode=args.mode,
            dry_run_if_unavailable=not args.no_dry_run_fallback,
            output_dir=args.output_dir if args.experiment == "functional" else None,
            run_id=args.run_id if args.experiment == "functional" else None,
            timeout_seconds=args.timeout,
            pull_policy=args.pull_policy,
        )
        print(f"E5 functional validation completed successfully. Output in: {out_func}")

    if args.experiment in ("storage", "both"):
        out_storage = run_storage_evaluation(
            catalog_path=args.catalog,
            mode=args.mode,
            stage=args.stage,
            target_arch=args.arch,
            dry_run_if_unavailable=not args.no_dry_run_fallback,
            output_dir=args.output_dir if args.experiment == "storage" else None,
            run_id=args.run_id if args.experiment == "storage" else None,
            timeout_seconds=args.timeout,
            scales=args.scales,
            eval_recommendation=not args.no_recommendation_eval,
            dataset_path=args.dataset,
            split_path=args.split if args.stage == "development" else None,
            freeze_path=args.freeze,
        )
        print(f"E5 storage scalability evaluation completed successfully. Output in: {out_storage}")


if __name__ == "__main__":
    main()
