"""Run a non-overwriting, versioned offline P1-versus-P2 evaluation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid
from typing import Any, Mapping

from recommender.candidate_corpus import CandidateCorpus, build_candidate_corpus
from recommender.deployment import PACKAGE_VERSION, compute_package_checksum
from recommender.models import RecommendationRequest, StructuredIntent
from recommender.p2_backend import P2Recommender
from recommender.policy import PolicyValidator
from recommender.rule_based import PROFILES, RuleBasedRecommender, load_image_catalog

from .dataset import load_evaluation_dataset
from .metrics import aggregate_metrics, categorize_p2_errors, p3_decision_report


RUN_SCHEMA_VERSION = "p1-p2-offline-run-v1.0.0"
PREDICTION_SCHEMA_VERSION = "p1-p2-raw-prediction-v1.0.0"
DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent / "results"
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-p1-p2-{uuid.uuid4().hex[:8]}"


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        value = result.stdout.strip()
        return value if value else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _git_worktree_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        return bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def _evaluation_code_sha256() -> str:
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for path in sorted(root.glob("*.py")):
        content = path.read_bytes()
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


def _request(item: Mapping[str, Any]) -> RecommendationRequest:
    inputs = item["inputs"]
    return RecommendationRequest(
        intent=str(inputs["intent"]),
        dataset_size_gb=inputs["dataset_size_gb"],
        code_context="\n".join(inputs["code_context_hints"]),
    )


def _structured_constraints(intent: StructuredIntent | None) -> dict[str, Any] | None:
    if intent is None:
        return None
    constraints = intent.resource_constraints
    return {
        "gpu_requirement": constraints.gpu_requirement.value,
        "minimum_cpu_cores": constraints.minimum_cpu_cores,
        "minimum_memory_gb": constraints.minimum_memory_gb,
        "dataset_size_gb": constraints.dataset_size_gb,
        "required_features": list(intent.required_features),
        "required_frameworks": list(intent.required_frameworks),
        "required_libraries": list(intent.required_libraries),
        "extraction_mode": intent.extraction_provenance.mode.value,
        "degraded_reason": intent.extraction_provenance.degraded_reason,
    }


def _constraint_violated(
    item: Mapping[str, Any],
    candidate_id: str | None,
    corpus: CandidateCorpus,
) -> bool:
    if candidate_id is None:
        return True
    candidate = corpus.get(candidate_id)
    if candidate is None:
        return True
    gold = item["gold"]
    if candidate.profile_id not in gold["allowed_profiles"]:
        return True
    if not set(gold["required_image_capabilities"]).issubset(candidate.capabilities):
        return True
    if not gold["gpu_allowed"] and candidate.resource_metadata.gpu_count > 0:
        return True
    return False


def _base_prediction(
    *,
    run_id: str,
    dataset: Mapping[str, Any],
    item: Mapping[str, Any],
    system: str,
) -> dict[str, Any]:
    return {
        "schema_version": PREDICTION_SCHEMA_VERSION,
        "run_id": run_id,
        "timestamp_utc": _timestamp(),
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": dataset["dataset_sha256"],
        "system": system,
        "sample_id": item["sample_id"],
        "workload_family": item["workload_family"],
        "source_dataset": item["source_dataset"],
    }


def _evaluate_p1(
    *,
    run_id: str,
    dataset: Mapping[str, Any],
    item: Mapping[str, Any],
    backend: RuleBasedRecommender,
    policy: PolicyValidator,
    corpus: CandidateCorpus,
) -> dict[str, Any]:
    started = time.perf_counter()
    recommendation = backend.recommend(_request(item))
    policy_compliant = True
    try:
        policy.validate(recommendation)
    except Exception:
        policy_compliant = False
    profile_id = "large" if recommendation.profile == "gpu_or_large" else recommendation.profile
    candidate_id = f"{profile_id}-{recommendation.image_id}"
    latency = max(0.0, time.perf_counter() - started)
    return {
        **_base_prediction(run_id=run_id, dataset=dataset, item=item, system="p1"),
        "requested_backend": "rule_based",
        "effective_backend": recommendation.backend_name,
        "backend_version": recommendation.backend_version,
        "final_candidate_id": candidate_id,
        "ranked_candidate_ids": [candidate_id],
        "retrieved_candidate_ids": [candidate_id],
        "feasible_candidate_ids": [candidate_id],
        "detected_infeasible": False,
        "constraint_violated": _constraint_violated(item, candidate_id, corpus),
        "policy_compliant": policy_compliant,
        "fallback_used": False,
        "fallback_category": None,
        "latency_seconds": latency,
        "infrastructure_failure": False,
        "extracted_constraints": None,
        "p2_provenance": None,
    }


def _evaluate_p2(
    *,
    run_id: str,
    dataset: Mapping[str, Any],
    item: Mapping[str, Any],
    backend: P2Recommender,
    policy: PolicyValidator,
    corpus: CandidateCorpus,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        detailed = backend.recommend_detailed(_request(item))
        recommendation = detailed.recommendation
        policy_compliant = True
        try:
            policy.validate(recommendation)
        except Exception:
            policy_compliant = False
        retrieval = detailed.retrieval_result
        ranking = detailed.ranking_result
        retrieved = (
            [hit.candidate_id for hit in retrieval.fused_hits] if retrieval is not None else []
        )
        feasible = (
            [candidate.candidate_id for candidate in ranking.ranked_candidates]
            if ranking is not None
            else []
        )
        ranked = list(feasible)
        detected_infeasible = bool(ranking is not None and ranking.no_feasible_candidate)
        structured = detailed.trace.structured_intent if detailed.trace is not None else None
        provenance = detailed.metadata.p2_provenance
        fallback_category = detailed.fallback_category
        infrastructure_failure = fallback_category in {
            "infrastructure_provider_failure",
            "pipeline_validation_failure",
        }
        return {
            **_base_prediction(run_id=run_id, dataset=dataset, item=item, system="p2"),
            "requested_backend": "p2",
            "effective_backend": recommendation.backend_name,
            "backend_version": recommendation.backend_version,
            "final_candidate_id": detailed.final_candidate_id,
            "ranked_candidate_ids": ranked,
            "retrieved_candidate_ids": retrieved,
            "feasible_candidate_ids": feasible,
            "detected_infeasible": detected_infeasible,
            "constraint_violated": _constraint_violated(
                item, detailed.final_candidate_id, corpus
            ),
            "policy_compliant": policy_compliant,
            "fallback_used": detailed.metadata.fallback_used,
            "fallback_category": (
                fallback_category if fallback_category != "none" else None
            ),
            "latency_seconds": max(0.0, time.perf_counter() - started),
            "infrastructure_failure": infrastructure_failure,
            "extracted_constraints": _structured_constraints(structured),
            "p2_provenance": dict(provenance) if provenance is not None else None,
        }
    except Exception:
        return {
            **_base_prediction(run_id=run_id, dataset=dataset, item=item, system="p2"),
            "requested_backend": "p2",
            "effective_backend": "unavailable",
            "backend_version": backend.backend_version,
            "final_candidate_id": None,
            "ranked_candidate_ids": [],
            "retrieved_candidate_ids": [],
            "feasible_candidate_ids": [],
            "detected_infeasible": False,
            "constraint_violated": True,
            "policy_compliant": False,
            "fallback_used": True,
            "fallback_category": "infrastructure_provider_failure",
            "latency_seconds": max(0.0, time.perf_counter() - started),
            "infrastructure_failure": True,
            "extracted_constraints": None,
            "p2_provenance": None,
        }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _write_jsonl(path: Path, records: list[Mapping[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )


def run_evaluation(
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    run_id: str | None = None,
) -> Path:
    selected_run_id = run_id or _default_run_id()
    if not _RUN_ID_PATTERN.fullmatch(selected_run_id):
        raise ValueError("run_id must be a bounded filesystem-safe identifier")
    target = output_root / selected_run_id
    target.mkdir(parents=True, exist_ok=False)
    raw_dir = target / "raw"
    aggregate_dir = target / "aggregates"
    analysis_dir = target / "analysis"
    raw_dir.mkdir()
    aggregate_dir.mkdir()
    analysis_dir.mkdir()

    dataset = load_evaluation_dataset()
    catalog = load_image_catalog()
    corpus = build_candidate_corpus(image_catalog=catalog)
    policy = PolicyValidator.from_catalog(profiles=PROFILES, catalog=catalog)
    p1 = RuleBasedRecommender(catalog=catalog)
    p2 = P2Recommender(catalog=catalog, corpus=corpus)
    predictions: list[dict[str, Any]] = []
    started = time.perf_counter()
    for item in dataset["items"]:
        predictions.append(
            _evaluate_p1(
                run_id=selected_run_id,
                dataset=dataset,
                item=item,
                backend=p1,
                policy=policy,
                corpus=corpus,
            )
        )
        predictions.append(
            _evaluate_p2(
                run_id=selected_run_id,
                dataset=dataset,
                item=item,
                backend=p2,
                policy=policy,
                corpus=corpus,
            )
        )

    metrics = aggregate_metrics(dataset, predictions)
    errors = categorize_p2_errors(dataset, predictions)
    decision = p3_decision_report(dataset, predictions)
    completed = _timestamp()
    manifest = {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": selected_run_id,
        "completed_at_utc": completed,
        "git_commit": _git_commit(),
        "git_worktree_dirty": _git_worktree_dirty(),
        "runtime_package_version": PACKAGE_VERSION,
        "runtime_package_checksum": compute_package_checksum(
            Path(__file__).resolve().parents[1] / "recommender"
        ),
        "evaluation_code_sha256": _evaluation_code_sha256(),
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": dataset["dataset_sha256"],
        "base_dataset_id": dataset["base_dataset_id"],
        "base_dataset_sha256": dataset["base_dataset_sha256"],
        "supplement_dataset_id": dataset["supplement_dataset_id"],
        "supplement_file_sha256": dataset["supplement_file_sha256"],
        "sample_count": len(dataset["items"]),
        "prediction_count": len(predictions),
        "primary_systems": ["p1", "p2"],
        "elapsed_seconds": max(0.0, time.perf_counter() - started),
        "raw_predictions_path": "raw/predictions.jsonl",
        "aggregates_path": "aggregates/metrics.json",
        "p2_errors_path": "analysis/p2_errors.json",
        "p3_decision_path": "analysis/p3_decision.json",
        "dense_only_and_sparse_only": "not_run",
        "p3_implemented": False,
    }
    metrics = {**metrics, "run_id": selected_run_id, "dataset_sha256": dataset["dataset_sha256"]}
    errors = {**errors, "run_id": selected_run_id, "dataset_sha256": dataset["dataset_sha256"]}
    decision = {**decision, "run_id": selected_run_id, "dataset_sha256": dataset["dataset_sha256"]}
    _write_jsonl(raw_dir / "predictions.jsonl", predictions)
    _write_json(aggregate_dir / "metrics.json", metrics)
    _write_json(analysis_dir / "p2_errors.json", errors)
    _write_json(analysis_dir / "p3_decision.json", decision)
    _write_json(target / "manifest.json", manifest)
    return target


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    target = run_evaluation(output_root=args.output_root, run_id=args.run_id)
    print(json.dumps({"result_directory": str(target)}, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_OUTPUT_ROOT",
    "PREDICTION_SCHEMA_VERSION",
    "RUN_SCHEMA_VERSION",
    "run_evaluation",
]
