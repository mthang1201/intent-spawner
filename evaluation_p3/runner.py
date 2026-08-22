"""Run a non-overwriting paired evaluation of frozen P2 versus P3."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import time
import uuid
from typing import Any, Mapping

from evaluation_p2.dataset import load_evaluation_dataset
from recommender.candidate_corpus import CandidateCorpus, build_candidate_corpus
from recommender.deployment import PACKAGE_VERSION, compute_package_checksum
from recommender.external_llm import ExternalLLMConfig
from recommender.models import RecommendationRequest
from recommender.p2_backend import P2Config, P2DetailedResult, P2Recommender
from recommender.p3_backend import P3Config, P3DetailedResult, P3Recommender
from recommender.p3_reranker import (
    P3_RERANKING_PROMPT_SHA256,
    P3_RERANKING_PROMPT_VERSION,
    P3Reranker,
)
from recommender.policy import PolicyValidator
from recommender.rule_based import PROFILES, load_image_catalog

from .metrics import aggregate_metrics


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent / "results"
DEFAULT_REFERENCE_RUN = (
    ROOT
    / "evaluation_p2/results/20260821T-observed-p1-p2-v1-4"
)
RUN_SCHEMA_VERSION = "p2-p3-paired-offline-run-v1.0.0"
PREDICTION_SCHEMA_VERSION = "p2-p3-raw-prediction-v1.0.0"
FREEZE_MANIFEST_VERSION = "p3-frozen-inputs-v1.0.0"
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Recorded before P3 implementation changes. The evaluation aborts if any
# frozen comparator, dataset, catalog, prompt/parser, retrieval, or ranking
# implementation differs.
FROZEN_INPUT_SHA256 = {
    "recommender/rule_based.py": "063d70244a59d261101e286f6c0fcff5f92f8c7d906072d305b5a2c328e3c581",
    "recommender/p2_backend.py": "3ca68ea72f7010c671684021ca3172dbc58c76408f6d31fb0900e88beb78e2ff",
    "recommender/candidate_corpus.py": "aac2c918ddf4345f82ebcb526f29c9b8e60f934fdb1ab3b014e3f4844e28d4af",
    "recommender/constraint_evaluator.py": "bb28299f8cd07ae7d85667c7166dbf4d04722bd4156b42f609b0ca916d8146d9",
    "recommender/dense_retrieval.py": "5c5a63120cea5baed7f8e72f94281724cb18073f7b36410dcdc650a6f78a1d8b",
    "recommender/hybrid_retrieval.py": "59a550e8f42214c13b284d4a59c99e28aa9d8db0f6456af60ad3adedd6b544b3",
    "recommender/local_embeddings.py": "70bc2de0dd1423268e10618308e63264aa6298ca16e00dc63c3545500136adf6",
    "recommender/local_structured_intent.py": "b9c249d1c6db9b664ef11275b7ca5dc355d6b75ab0f0ebac4d3a59631882d5ba",
    "recommender/sparse_retrieval.py": "56bb94fc32e23e5be0e3b097dbe634eb89b2681c114a764220a18c0aa58ef5d5",
    "recommender/structured_intent.py": "8b4a50a61e887aced0044c7299b69a383ebf3d82ee14b134330747cbdf00c1c6",
    "recommender/image-catalog.yaml": "f45b04efc2ea6f271d49c6806b58bfc0f30503cb68944930609f6e0f71882a71",
    "benchmarks/intent-gold-v4.yaml": "a0f23920e90c6f4b338b51ec4517a4ba49216940bf041729c8c7b0db452afc4d",
    "benchmarks/p2-infeasible-supplement-v1.yaml": "c030c6f7b3bf6e08ab06ff1c449b4e7b0707283f75c7c2c1acfd5a2ce60e32be",
    "evaluation_p2/dataset.py": "dade743236ceff488bf7e8c1d012eed31f72d896fd5bd36c751a7caa3d0032a6",
    "evaluation_p2/metrics.py": "c7b7bf73d93e19994477c6c97aa0d6dbbd254ca632f22d43bb1975c29d482dc0",
    "evaluation_p2/runner.py": "63174409c6cd0206d1aa3408f659613576870d4914ac1b9ee1e41a27cc49204c",
}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-p2-p3-{uuid.uuid4().hex[:8]}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_frozen_inputs() -> dict[str, str]:
    observed: dict[str, str] = {}
    mismatches: list[str] = []
    for relative, expected in FROZEN_INPUT_SHA256.items():
        path = ROOT / relative
        actual = _sha256(path) if path.is_file() else "missing"
        observed[relative] = actual
        if actual != expected:
            mismatches.append(relative)
    if mismatches:
        raise RuntimeError(
            "frozen P1/P2/catalog/dataset inputs changed: " + ", ".join(mismatches)
        )
    return observed


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


def _git_worktree_dirty() -> bool | None:
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


def _evaluation_code_sha256() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
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
    return bool(
        candidate.profile_id not in gold["allowed_profiles"]
        or not set(gold["required_image_capabilities"]).issubset(
            candidate.capabilities
        )
        or (
            not gold["gpu_allowed"]
            and candidate.resource_metadata.gpu_count > 0
        )
    )


def _policy_compliant(
    policy: PolicyValidator, recommendation: object
) -> bool:
    try:
        policy.validate(recommendation)
        return True
    except Exception:
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


def _p2_prediction(
    *,
    run_id: str,
    dataset: Mapping[str, Any],
    item: Mapping[str, Any],
    detailed: P2DetailedResult,
    policy: PolicyValidator,
    corpus: CandidateCorpus,
) -> dict[str, Any]:
    ranking = detailed.ranking_result
    retrieval = detailed.retrieval_result
    ranked = (
        [candidate.candidate_id for candidate in ranking.ranked_candidates]
        if ranking is not None
        else []
    )
    return {
        **_base_prediction(
            run_id=run_id, dataset=dataset, item=item, system="p2"
        ),
        "requested_backend": "p2",
        "effective_backend": detailed.recommendation.backend_name,
        "backend_version": detailed.recommendation.backend_version,
        "final_candidate_id": detailed.final_candidate_id,
        "ranked_candidate_ids": ranked,
        "retrieved_candidate_ids": (
            [hit.candidate_id for hit in retrieval.fused_hits]
            if retrieval is not None
            else []
        ),
        "feasible_candidate_ids": ranked,
        "detected_infeasible": bool(
            ranking is not None and ranking.no_feasible_candidate
        ),
        "constraint_violated": _constraint_violated(
            item, detailed.final_candidate_id, corpus
        ),
        "policy_compliant": _policy_compliant(policy, detailed.recommendation),
        "fallback_used": detailed.metadata.fallback_used,
        "fallback_category": (
            detailed.fallback_category
            if detailed.fallback_category != "none"
            else None
        ),
        "latency_seconds": detailed.metadata.total_elapsed_seconds,
        "p2_provenance": (
            dict(detailed.metadata.p2_provenance)
            if detailed.metadata.p2_provenance is not None
            else None
        ),
    }


def _p3_prediction(
    *,
    run_id: str,
    dataset: Mapping[str, Any],
    item: Mapping[str, Any],
    detailed: P3DetailedResult,
    policy: PolicyValidator,
    corpus: CandidateCorpus,
) -> dict[str, Any]:
    p2_ranking = detailed.p2_result.ranking_result
    p2_feasible = (
        [candidate.candidate_id for candidate in p2_ranking.ranked_candidates]
        if p2_ranking is not None
        else []
    )
    ranked = (
        [candidate.candidate_id for candidate in detailed.trace.ranked_candidates]
        if detailed.trace is not None
        else []
    )
    provenance = detailed.metadata.p3_provenance or {}
    reranking = detailed.reranking_result
    invoked = bool(provenance.get("reranker_invoked"))
    return {
        **_base_prediction(
            run_id=run_id, dataset=dataset, item=item, system="p3"
        ),
        "requested_backend": "p3",
        "effective_backend": detailed.recommendation.backend_name,
        "backend_version": detailed.recommendation.backend_version,
        "final_candidate_id": detailed.final_candidate_id,
        "ranked_candidate_ids": ranked,
        "p2_feasible_candidate_ids": p2_feasible,
        "detected_infeasible": bool(
            p2_ranking is not None and p2_ranking.no_feasible_candidate
        ),
        "constraint_violated": _constraint_violated(
            item, detailed.final_candidate_id, corpus
        ),
        "policy_compliant": _policy_compliant(policy, detailed.recommendation),
        "fallback_used": detailed.metadata.fallback_used,
        "fallback_category": (
            detailed.fallback_category
            if detailed.fallback_category != "none"
            else None
        ),
        "latency_seconds": detailed.metadata.total_elapsed_seconds,
        "reranker_invoked": invoked,
        "reranker_degraded": bool(provenance.get("reranker_degraded")),
        "reranker_degraded_reason": provenance.get("reranker_degraded_reason"),
        "invalid_reranker_output": bool(
            provenance.get("invalid_reranker_output")
        ),
        "provider_failure": bool(provenance.get("provider_failure")),
        "selected_outside_p2_feasible": bool(
            invoked
            and detailed.final_candidate_id not in set(p2_feasible)
        ),
        "attempt_count": detailed.metadata.attempt_count,
        "prompt_tokens": detailed.metadata.prompt_tokens,
        "completion_tokens": detailed.metadata.completion_tokens,
        "total_tokens": detailed.metadata.total_tokens,
        "inference_latency_seconds": detailed.metadata.inference_latency_seconds,
        "estimated_cost_usd": detailed.metadata.estimated_cost_usd,
        "pricing_id": detailed.metadata.pricing_id,
        "pricing_provenance": detailed.metadata.pricing_provenance,
        "validation_error": detailed.metadata.validation_error,
        "reranker_raw_response": (
            reranking.raw_response if reranking is not None else None
        ),
        "p3_provenance": dict(provenance),
    }


def _load_reference_p2(reference_run: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest_path = reference_run / "manifest.json"
    raw_path = reference_run / "raw/predictions.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in raw_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    p2 = {item["sample_id"]: item for item in records if item["system"] == "p2"}
    return manifest, p2


def _verify_p2_reference(
    dataset: Mapping[str, Any],
    predictions: list[Mapping[str, Any]],
    reference_run: Path,
) -> dict[str, Any]:
    manifest, reference = _load_reference_p2(reference_run)
    if manifest.get("dataset_sha256") != dataset["dataset_sha256"]:
        raise RuntimeError("reference P2 run used a different frozen dataset")
    current = {
        item["sample_id"]: item for item in predictions if item["system"] == "p2"
    }
    fields = (
        "final_candidate_id",
        "ranked_candidate_ids",
        "retrieved_candidate_ids",
        "feasible_candidate_ids",
        "detected_infeasible",
        "constraint_violated",
        "policy_compliant",
        "fallback_category",
    )
    mismatches: list[dict[str, Any]] = []
    for item in dataset["items"]:
        sample_id = item["sample_id"]
        if sample_id not in reference or sample_id not in current:
            mismatches.append({"sample_id": sample_id, "fields": ["missing"]})
            continue
        changed = [
            field
            for field in fields
            if current[sample_id].get(field) != reference[sample_id].get(field)
        ]
        if changed:
            mismatches.append({"sample_id": sample_id, "fields": changed})
    if mismatches:
        raise RuntimeError(
            "current P2 output does not match the frozen reference run: "
            + json.dumps(mismatches, sort_keys=True)
        )
    return {
        "reference_run_id": manifest["run_id"],
        "reference_manifest_sha256": _sha256(reference_run / "manifest.json"),
        "reference_predictions_sha256": _sha256(
            reference_run / "raw/predictions.jsonl"
        ),
        "matched": True,
        "compared_fields": list(fields),
        "matched_sample_count": len(dataset["items"]),
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
    llm_config: ExternalLLMConfig,
    provider_provenance: str,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    reference_run: Path = DEFAULT_REFERENCE_RUN,
    run_id: str | None = None,
) -> Path:
    if not isinstance(provider_provenance, str) or not provider_provenance.strip():
        raise ValueError("provider_provenance must be non-blank")
    selected_run_id = run_id or _default_run_id()
    if not _RUN_ID_PATTERN.fullmatch(selected_run_id):
        raise ValueError("run_id must be a bounded filesystem-safe identifier")
    frozen_hashes = verify_frozen_inputs()

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
    frozen_p2_config = P2Config()
    p2 = P2Recommender(
        config=frozen_p2_config,
        catalog=catalog,
        corpus=corpus,
    )
    reranker = P3Reranker(config=llm_config)
    p3 = P3Recommender(
        config=P3Config(
            reranker_mode="llm",
            total_timeout=llm_config.total_timeout,
            max_concurrent_recommendations=llm_config.max_concurrent_recommendations,
        ),
        p2_backend=p2,
        reranker=reranker,
    )

    predictions: list[dict[str, Any]] = []
    started = time.perf_counter()
    for number, item in enumerate(dataset["items"], start=1):
        detailed = p3.recommend_detailed(_request(item))
        predictions.append(
            _p2_prediction(
                run_id=selected_run_id,
                dataset=dataset,
                item=item,
                detailed=detailed.p2_result,
                policy=policy,
                corpus=corpus,
            )
        )
        predictions.append(
            _p3_prediction(
                run_id=selected_run_id,
                dataset=dataset,
                item=item,
                detailed=detailed,
                policy=policy,
                corpus=corpus,
            )
        )
        if number == 1 or number % 5 == 0 or number == len(dataset["items"]):
            print(
                json.dumps(
                    {
                        "completed_queries": number,
                        "total_queries": len(dataset["items"]),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    p2_reference = _verify_p2_reference(
        dataset, predictions, reference_run.resolve()
    )
    metrics, paired, transitions = aggregate_metrics(dataset, predictions)
    completed = _timestamp()
    pricing = llm_config.pricing.to_dict() if llm_config.pricing is not None else None
    manifest = {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": selected_run_id,
        "completed_at_utc": completed,
        "git_commit": _git_commit(),
        "git_worktree_dirty": _git_worktree_dirty(),
        "python_version": platform.python_version(),
        "runtime_package_version": PACKAGE_VERSION,
        "runtime_package_checksum": compute_package_checksum(ROOT / "recommender"),
        "evaluation_code_sha256": _evaluation_code_sha256(),
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": dataset["dataset_sha256"],
        "base_dataset_sha256": dataset["base_dataset_sha256"],
        "supplement_file_sha256": dataset["supplement_file_sha256"],
        "sample_count": len(dataset["items"]),
        "prediction_count": len(predictions),
        "primary_systems": ["p2", "p3"],
        "elapsed_seconds": max(0.0, time.perf_counter() - started),
        "frozen_inputs": {
            "schema_version": FREEZE_MANIFEST_VERSION,
            "files": frozen_hashes,
            "p2_config": asdict(frozen_p2_config),
            "p2_reference": p2_reference,
            "candidate_corpus_version": corpus.corpus_version,
            "candidate_corpus_checksum": corpus.corpus_checksum,
            "catalog_version": corpus.source_image_catalog_version,
        },
        "p3_model_configuration": {
            "configuration_version": "p3-evaluation-model-config-v1.0.0",
            "provider_provenance": provider_provenance.strip(),
            "endpoint": llm_config.endpoint,
            "model_id": llm_config.model,
            "temperature": 0.0,
            "attempt_timeout_seconds": llm_config.timeout,
            "total_timeout_seconds": llm_config.total_timeout,
            "max_retries": llm_config.max_retries,
            "retry_backoff_seconds": llm_config.retry_backoff_seconds,
            "reranker_prompt_version": P3_RERANKING_PROMPT_VERSION,
            "reranker_prompt_sha256": P3_RERANKING_PROMPT_SHA256,
            "pricing": pricing,
            "api_key_recorded": False,
        },
        "raw_predictions_path": "raw/predictions.jsonl",
        "aggregates_path": "aggregates/metrics.json",
        "paired_changes_path": "analysis/paired_changes.json",
        "error_transitions_path": "analysis/error_transitions.json",
        "b0_user_experiments": "not_performed",
    }
    metrics = {
        **metrics,
        "run_id": selected_run_id,
        "dataset_sha256": dataset["dataset_sha256"],
    }
    paired = {
        **paired,
        "run_id": selected_run_id,
        "dataset_sha256": dataset["dataset_sha256"],
    }
    transitions = {
        **transitions,
        "run_id": selected_run_id,
        "dataset_sha256": dataset["dataset_sha256"],
    }
    _write_jsonl(raw_dir / "predictions.jsonl", predictions)
    _write_json(aggregate_dir / "metrics.json", metrics)
    _write_json(analysis_dir / "paired_changes.json", paired)
    _write_json(analysis_dir / "error_transitions.json", transitions)
    _write_json(target / "manifest.json", manifest)
    return target


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--reference-run", type=Path, default=DEFAULT_REFERENCE_RUN)
    parser.add_argument("--run-id")
    parser.add_argument("--provider-provenance", required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    target = run_evaluation(
        llm_config=ExternalLLMConfig.from_environ(),
        provider_provenance=args.provider_provenance,
        output_root=args.output_root,
        reference_run=args.reference_run,
        run_id=args.run_id,
    )
    print(json.dumps({"result_directory": str(target)}, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_OUTPUT_ROOT",
    "DEFAULT_REFERENCE_RUN",
    "FROZEN_INPUT_SHA256",
    "PREDICTION_SCHEMA_VERSION",
    "RUN_SCHEMA_VERSION",
    "run_evaluation",
    "verify_frozen_inputs",
]
