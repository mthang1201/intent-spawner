"""Freeze and analyze non-overwriting final-evaluation result directories."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import uuid
from typing import Any, Mapping

from evaluation_p2.dataset import load_evaluation_dataset
from recommender.candidate_corpus import build_candidate_corpus
from recommender.constraint_evaluator import (
    CONSTRAINT_EVALUATOR_VERSION,
    CONSTRAINT_POLICY_VERSION,
    DETERMINISTIC_RANKER_VERSION,
)
from recommender.local_embeddings import (
    LOCAL_EMBEDDING_DIMENSIONS,
    LOCAL_EMBEDDING_MODEL_ID,
    LOCAL_EMBEDDING_MODEL_REVISION,
    LOCAL_EMBEDDING_TOKENIZER_VERSION,
)
from recommender.local_structured_intent import (
    LOCAL_EXTRACTOR_MODEL_ID,
    LOCAL_EXTRACTOR_NAME,
    LOCAL_EXTRACTOR_PROMPT_SHA256,
    LOCAL_EXTRACTOR_PROMPT_VERSION,
    LOCAL_EXTRACTOR_VERSION,
)
from recommender.p2_backend import (
    P2_BACKEND_VERSION,
    P2_PIPELINE_VERSION,
    P2Config,
    P2Recommender,
)
from recommender.p3_backend import P3_BACKEND_VERSION, P3_PIPELINE_VERSION
from recommender.p3_reranker import (
    P3_RERANKING_PROMPT_SHA256,
    P3_RERANKING_PROMPT_VERSION,
)
from recommender.rule_based import (
    BACKEND_VERSION as P1_BACKEND_VERSION,
    DEFAULT_CATALOG_PATH,
    load_image_catalog,
)

from .analysis import analyze_rq1, analyze_rq2, analyze_rq3
from .schemas import read_json, read_jsonl
from .systems import (
    PRIMARY_SYSTEM_IDS,
    active_primary_system_ids,
    system_registry,
    validate_p3_gate_status,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent / "results"
DEFAULT_P3_GATE_EVIDENCE = ROOT / "docs/evaluation/P3_INCREMENTAL_EVALUATION_V1.md"
FREEZE_SCHEMA_VERSION = "final-evaluation-freeze-v1.0.0"
ANALYSIS_RUN_SCHEMA_VERSION = "final-evaluation-analysis-run-v1.0.0"
EVIDENCE_STATUS_SCHEMA_VERSION = "final-evaluation-evidence-status-v1.0.0"
P3_GATE_DECISION_VERSION = "final-p3-retention-gate-v1.0.0"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

FROZEN_SOURCE_FILES = (
    "recommender/image-catalog.yaml",
    "recommender/rule_based.py",
    "recommender/models.py",
    "recommender/candidate_corpus.py",
    "recommender/local_structured_intent.py",
    "recommender/local_embeddings.py",
    "recommender/sparse_retrieval.py",
    "recommender/dense_retrieval.py",
    "recommender/hybrid_retrieval.py",
    "recommender/constraint_evaluator.py",
    "recommender/p2_backend.py",
    "recommender/p3_backend.py",
    "recommender/p3_reranker.py",
    "benchmarks/intent-gold-v4.yaml",
    "benchmarks/p2-infeasible-supplement-v1.yaml",
    "evaluation_p2/dataset.py",
    "evaluation_final/systems.py",
    "evaluation_final/schemas.py",
    "evaluation_final/statistics.py",
    "evaluation_final/analysis.py",
    "evaluation_final/runner.py",
    "docs/evaluation/FINAL_EVALUATION_PROTOCOL_V1.md",
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_run_id(kind: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-final-{kind}-{uuid.uuid4().hex[:8]}"


def _validate_run_id(run_id: str) -> str:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be a bounded filesystem-safe identifier")
    return run_id


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _git_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _write_text(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(value)


def _relative_or_absolute(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def _source_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in FROZEN_SOURCE_FILES:
        path = ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(f"required final-evaluation input is missing: {relative}")
        hashes[relative] = _sha256(path)
    return hashes


def _historical_reference(path: Path, classification: str) -> dict[str, Any]:
    return {
        "path": _relative_or_absolute(path),
        "sha256": _sha256(path) if path.is_file() else None,
        "classification": classification,
        "exists": path.is_file(),
    }


def build_freeze_manifest(
    *,
    run_id: str,
    p3_gate_status: str,
    p3_gate_evidence: Path,
) -> dict[str, Any]:
    """Resolve and checksum every final-test input before observations are collected."""

    validate_p3_gate_status(p3_gate_status)
    evidence = p3_gate_evidence.resolve()
    if not evidence.is_file():
        raise FileNotFoundError("P3 gate evidence must exist before freezing")
    dataset = load_evaluation_dataset()
    catalog = load_image_catalog()
    corpus = build_candidate_corpus(image_catalog=catalog)
    p2_config = P2Config()
    p2 = P2Recommender(config=p2_config, catalog=catalog, corpus=corpus)
    dense = p2.retriever.dense_retriever.metadata
    sparse = p2.retriever.sparse_retriever.metadata
    hybrid = p2.retriever.metadata
    active = active_primary_system_ids(p3_gate_status)
    gate = {
        "decision_version": P3_GATE_DECISION_VERSION,
        "status": p3_gate_status,
        "evidence_path": _relative_or_absolute(evidence),
        "evidence_sha256": _sha256(evidence),
        "P3_active_in_final_evaluation": p3_gate_status == "retained",
    }
    return {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "run_id": run_id,
        "created_at_utc": _timestamp(),
        "state": "frozen_before_final_observation_collection",
        "system_registry": system_registry(p3_gate_status),
        "allowed_primary_system_ids": list(PRIMARY_SYSTEM_IDS),
        "active_primary_system_ids": list(active),
        "p3_gate": gate,
        "research_questions": {
            "RQ1": {
                "scope": "user-facing selection effectiveness against B0",
                "B0_ranking_metrics_prohibited": True,
                "status": "awaiting_real_user_study_observations",
            },
            "RQ2": {
                "scope": "paired recommendation quality P2 versus P1",
                "status": "awaiting_final_test_observations",
                "P2_ablations": "optional_secondary_analysis_only",
            },
            "RQ3": {
                "scope": "paired incremental P3 versus P2",
                "status": (
                    "awaiting_final_test_observations"
                    if p3_gate_status == "retained"
                    else "not_applicable_after_gate"
                ),
            },
        },
        "frozen_inputs": {
            "candidate_catalog": {
                "version": catalog["catalog_version"],
                "path": _relative_or_absolute(Path(DEFAULT_CATALOG_PATH)),
                "file_sha256": _sha256(Path(DEFAULT_CATALOG_PATH)),
                "candidate_corpus_version": corpus.corpus_version,
                "candidate_corpus_checksum": corpus.corpus_checksum,
                "candidate_count": len(corpus.candidates),
            },
            "dataset": {
                "dataset_id": dataset["dataset_id"],
                "dataset_schema_version": dataset["schema_version"],
                "dataset_sha256": dataset["dataset_sha256"],
                "base_dataset_id": dataset["base_dataset_id"],
                "base_dataset_sha256": dataset["base_dataset_sha256"],
                "supplement_dataset_id": dataset["supplement_dataset_id"],
                "supplement_file_sha256": dataset["supplement_file_sha256"],
                "sample_count": len(dataset["items"]),
                "tuning_after_freeze_prohibited": True,
            },
            "prompts": {
                "P2_extractor": {
                    "version": LOCAL_EXTRACTOR_PROMPT_VERSION,
                    "sha256": LOCAL_EXTRACTOR_PROMPT_SHA256,
                },
                "P3_reranker": {
                    "applicable": p3_gate_status == "retained",
                    "version": P3_RERANKING_PROMPT_VERSION,
                    "sha256": P3_RERANKING_PROMPT_SHA256,
                },
            },
            "extractor": {
                "name": LOCAL_EXTRACTOR_NAME,
                "version": LOCAL_EXTRACTOR_VERSION,
                "model_id": LOCAL_EXTRACTOR_MODEL_ID,
                "mode": p2_config.extractor_mode,
                "configuration_version": p2_config.config_version,
            },
            "embedding": {
                "model_id": LOCAL_EMBEDDING_MODEL_ID,
                "model_revision": LOCAL_EMBEDDING_MODEL_REVISION,
                "dimensions": LOCAL_EMBEDDING_DIMENSIONS,
                "tokenizer_version": LOCAL_EMBEDDING_TOKENIZER_VERSION,
            },
            "retrieval_and_indexes": {
                "configuration": asdict(p2_config),
                "sparse": asdict(sparse),
                "dense": asdict(dense),
                "hybrid": asdict(hybrid),
            },
            "constraint_rules": {
                "evaluator_version": CONSTRAINT_EVALUATOR_VERSION,
                "policy_version": CONSTRAINT_POLICY_VERSION,
                "ranker_version": DETERMINISTIC_RANKER_VERSION,
            },
            "system_revisions": {
                "B0": "jupyterhub-manual-selection-protocol-v1.0.0",
                "P1": P1_BACKEND_VERSION,
                "P2": {
                    "backend_version": P2_BACKEND_VERSION,
                    "pipeline_version": P2_PIPELINE_VERSION,
                },
                "P3": {
                    "applicable": p3_gate_status == "retained",
                    "backend_version": P3_BACKEND_VERSION,
                    "pipeline_version": P3_PIPELINE_VERSION,
                },
            },
            "source_files_sha256": _source_hashes(),
        },
        "source_control": {
            "git_commit": _git_commit(),
            "git_worktree_dirty": _git_dirty(),
            "python_version": platform.python_version(),
        },
        "historical_reference_evidence": {
            "P1_P2_observed_development_run": _historical_reference(
                ROOT / "evaluation_p2/results/20260821T-observed-p1-p2-v1-4/manifest.json",
                "historical_reference_not_automatically_a_final_test",
            ),
            "P3_gate_run": _historical_reference(
                ROOT
                / "evaluation_p3/results/20260821T-observed-p2-p3-ollama-llama3-v1/manifest.json",
                "gate_evidence_not_a_separate_primary_provider_system",
            ),
            "direct_external_LLM_report": _historical_reference(
                ROOT / "docs/evaluation/EXTERNAL_LLM_GEMINI_3_5_VERIFICATION.md",
                "historical_reference_only",
            ),
            "direct_local_LLM_report": _historical_reference(
                ROOT / "docs/evaluation/LOCAL_LLM_REPAIR_REPORT.md",
                "historical_reference_only",
            ),
        },
        "result_layout": {
            "raw": "raw/",
            "derived": "derived/",
            "interpretation": "interpretation/",
        },
        "integrity_rules": {
            "exclusive_create_no_overwrite": True,
            "raw_observations_immutable": True,
            "missing_results_must_remain_missing": True,
            "observations_must_precede_metrics": True,
            "interpretation_must_not_be_stored_as_raw_or_derived_data": True,
            "kubernetes_cluster_mutation_authorized": False,
        },
    }


def create_frozen_run(
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    run_id: str | None = None,
    p3_gate_status: str,
    p3_gate_evidence: Path = DEFAULT_P3_GATE_EVIDENCE,
) -> Path:
    selected = _validate_run_id(run_id or _default_run_id("freeze"))
    target = output_root / selected
    if target.exists():
        raise FileExistsError(target)
    manifest = build_freeze_manifest(
        run_id=selected,
        p3_gate_status=p3_gate_status,
        p3_gate_evidence=p3_gate_evidence,
    )
    target.mkdir(parents=True, exist_ok=False)
    for name in ("raw", "derived", "interpretation"):
        (target / name).mkdir()
    _write_json(target / "freeze-manifest.json", manifest)
    _write_text(
        target / "raw/README.md",
        "# Raw observations\n\nNo final observations have been collected in this freeze package.\n",
    )
    _write_text(
        target / "derived/README.md",
        "# Derived metrics\n\nNo metrics are generated until validated raw observations exist.\n",
    )
    _write_json(
        target / "interpretation/status.json",
        {
            "schema_version": EVIDENCE_STATUS_SCHEMA_VERSION,
            "state": "frozen_awaiting_observations",
            "claims_permitted": False,
            "real_user_study_executed": False,
            "final_RQ2_test_executed": False,
            "P3_gate_status": p3_gate_status,
            "P3_final_test_executed": False,
            "limitations": [
                "No real user-study observations were supplied.",
                "No final-test recommendation observations were supplied.",
                "The existing synthetic dataset has prior development use; held-out split provenance must be established before confirmatory claims.",
            ],
        },
    )
    return target


def verify_frozen_run(freeze_directory: Path) -> dict[str, Any]:
    manifest = read_json(freeze_directory / "freeze-manifest.json")
    if manifest.get("schema_version") != FREEZE_SCHEMA_VERSION:
        raise ValueError("unsupported final-evaluation freeze manifest")
    mismatches: list[str] = []
    for relative, expected in manifest["frozen_inputs"]["source_files_sha256"].items():
        path = ROOT / relative
        actual = _sha256(path) if path.is_file() else "missing"
        if actual != expected:
            mismatches.append(relative)
    dataset = load_evaluation_dataset()
    if dataset["dataset_sha256"] != manifest["frozen_inputs"]["dataset"]["dataset_sha256"]:
        mismatches.append("composed_dataset_identity")
    if mismatches:
        raise RuntimeError("frozen final-evaluation inputs changed: " + ", ".join(mismatches))
    return manifest


def _copy_exclusive(source: Path, target: Path) -> None:
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer)


def _status_without_metrics(rq: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": EVIDENCE_STATUS_SCHEMA_VERSION,
        "research_question": rq,
        "status": "not_executed",
        "metrics_generated": False,
        "reason": reason,
    }


def create_analysis_run(
    *,
    freeze_directory: Path,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    run_id: str | None = None,
    rq1_tasks_path: Path | None = None,
    rq1_events_path: Path | None = None,
    rq2_predictions_path: Path | None = None,
    p2_ablation_predictions_path: Path | None = None,
    rq3_predictions_path: Path | None = None,
    bootstrap_replicates: int = 2000,
    bootstrap_seed: int = 20260822,
) -> Path:
    """Create a new analysis package; never modify the freeze or source evidence."""

    freeze = verify_frozen_run(freeze_directory.resolve())
    p3_gate_status = freeze["p3_gate"]["status"]
    if (rq1_tasks_path is None) != (rq1_events_path is None):
        raise ValueError("RQ1 tasks and events must be supplied together")
    if p3_gate_status != "retained" and rq3_predictions_path is not None:
        raise ValueError("RQ3 observations are prohibited because P3 was not retained")
    selected = _validate_run_id(run_id or _default_run_id("analysis"))
    target = output_root / selected
    target.mkdir(parents=True, exist_ok=False)
    for name in ("raw", "derived", "interpretation"):
        (target / name).mkdir()

    raw_sources: dict[str, Any] = {}

    def preserve(label: str, source: Path, destination_name: str) -> Path:
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = target / "raw" / destination_name
        _copy_exclusive(source, destination)
        raw_sources[label] = {
            "source_path": _relative_or_absolute(source),
            "preserved_path": f"raw/{destination_name}",
            "sha256": _sha256(source),
        }
        return destination

    if rq1_tasks_path is not None and rq1_events_path is not None:
        tasks = preserve("RQ1_tasks", rq1_tasks_path, "rq1_tasks.json")
        events = preserve("RQ1_events", rq1_events_path, "rq1_events.jsonl")
        rq1 = analyze_rq1(
            read_json(tasks),
            read_jsonl(events),
            p3_gate_status=p3_gate_status,
            bootstrap_replicates=bootstrap_replicates,
            bootstrap_seed=bootstrap_seed,
        )
    else:
        rq1 = _status_without_metrics(
            "RQ1", "No real user-study task set and event observations were supplied."
        )

    dataset = load_evaluation_dataset()
    if rq2_predictions_path is not None:
        predictions = preserve(
            "RQ2_predictions", rq2_predictions_path, "rq2_predictions.jsonl"
        )
        ablations = None
        if p2_ablation_predictions_path is not None:
            ablation_path = preserve(
                "P2_ablation_predictions",
                p2_ablation_predictions_path,
                "p2_ablation_predictions.jsonl",
            )
            ablations = read_jsonl(ablation_path)
        rq2 = analyze_rq2(
            dataset,
            read_jsonl(predictions),
            ablation_predictions=ablations,
            bootstrap_replicates=bootstrap_replicates,
            bootstrap_seed=bootstrap_seed,
        )
    else:
        if p2_ablation_predictions_path is not None:
            raise ValueError("P2 ablations require the primary P1/P2 observations")
        rq2 = _status_without_metrics(
            "RQ2", "No final paired P1/P2 prediction observations were supplied."
        )

    rq3_records = None
    if rq3_predictions_path is not None:
        rq3_path = preserve(
            "RQ3_predictions", rq3_predictions_path, "rq3_predictions.jsonl"
        )
        rq3_records = read_jsonl(rq3_path)
    rq3 = analyze_rq3(
        dataset,
        rq3_records,
        p3_gate_status=p3_gate_status,
        gate_evidence=freeze["p3_gate"],
        bootstrap_replicates=bootstrap_replicates,
        bootstrap_seed=bootstrap_seed,
    )

    _write_json(target / "derived/RQ1.json", rq1)
    _write_json(target / "derived/RQ2.json", rq2)
    _write_json(target / "derived/RQ3.json", rq3)
    interpretation_status = {
        "schema_version": EVIDENCE_STATUS_SCHEMA_VERSION,
        "state": "derived_metrics_created",
        "automatic_interpretive_claims_generated": False,
        "human_interpretation_required": True,
        "RQ1_metrics_generated": bool(rq1.get("systems")),
        "RQ2_metrics_generated": bool(rq2.get("systems")),
        "RQ3_metrics_generated": bool(rq3.get("metrics_generated")),
        "warning": "Interpret only observed fields; null or absent evidence must not be imputed.",
    }
    _write_json(target / "interpretation/status.json", interpretation_status)
    manifest = {
        "schema_version": ANALYSIS_RUN_SCHEMA_VERSION,
        "run_id": selected,
        "created_at_utc": _timestamp(),
        "freeze_run_id": freeze["run_id"],
        "freeze_manifest_path": _relative_or_absolute(
            freeze_directory.resolve() / "freeze-manifest.json"
        ),
        "freeze_manifest_sha256": _sha256(
            freeze_directory.resolve() / "freeze-manifest.json"
        ),
        "frozen_inputs_verified_unchanged": True,
        "allowed_primary_system_ids": list(PRIMARY_SYSTEM_IDS),
        "active_primary_system_ids": list(active_primary_system_ids(p3_gate_status)),
        "p3_gate_status": p3_gate_status,
        "raw_sources": raw_sources,
        "derived_outputs": {
            "RQ1": "derived/RQ1.json",
            "RQ2": "derived/RQ2.json",
            "RQ3": "derived/RQ3.json",
        },
        "interpretation_status": "interpretation/status.json",
        "bootstrap": {
            "replicates": bootstrap_replicates,
            "seed": bootstrap_seed,
        },
    }
    _write_json(target / "analysis-manifest.json", manifest)
    return target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze", help="freeze all final-test inputs")
    freeze.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    freeze.add_argument("--run-id")
    freeze.add_argument(
        "--p3-gate-status", choices=("retained", "not_retained"), required=True
    )
    freeze.add_argument(
        "--p3-gate-evidence", type=Path, default=DEFAULT_P3_GATE_EVIDENCE
    )

    analyze = subparsers.add_parser("analyze", help="analyze supplied raw observations")
    analyze.add_argument("--freeze-directory", type=Path, required=True)
    analyze.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    analyze.add_argument("--run-id")
    analyze.add_argument("--rq1-tasks", type=Path)
    analyze.add_argument("--rq1-events", type=Path)
    analyze.add_argument("--rq2-predictions", type=Path)
    analyze.add_argument("--p2-ablation-predictions", type=Path)
    analyze.add_argument("--rq3-predictions", type=Path)
    analyze.add_argument("--bootstrap-replicates", type=int, default=2000)
    analyze.add_argument("--bootstrap-seed", type=int, default=20260822)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "freeze":
        target = create_frozen_run(
            output_root=args.output_root,
            run_id=args.run_id,
            p3_gate_status=args.p3_gate_status,
            p3_gate_evidence=args.p3_gate_evidence,
        )
    else:
        target = create_analysis_run(
            freeze_directory=args.freeze_directory,
            output_root=args.output_root,
            run_id=args.run_id,
            rq1_tasks_path=args.rq1_tasks,
            rq1_events_path=args.rq1_events,
            rq2_predictions_path=args.rq2_predictions,
            p2_ablation_predictions_path=args.p2_ablation_predictions,
            rq3_predictions_path=args.rq3_predictions,
            bootstrap_replicates=args.bootstrap_replicates,
            bootstrap_seed=args.bootstrap_seed,
        )
    print(json.dumps({"result_directory": str(target)}, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = [
    "ANALYSIS_RUN_SCHEMA_VERSION",
    "DEFAULT_OUTPUT_ROOT",
    "DEFAULT_P3_GATE_EVIDENCE",
    "FREEZE_SCHEMA_VERSION",
    "FROZEN_SOURCE_FILES",
    "build_freeze_manifest",
    "create_analysis_run",
    "create_frozen_run",
    "verify_frozen_run",
]
