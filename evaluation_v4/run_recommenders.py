"""Run the protocol-v4 multi-recommender prediction matrix."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4

from .dataset import DEFAULT_DATASET, canonical_sha256, dataset_summary, file_sha256, load_dataset
from .recommenders import (
    DEFAULT_RECOMMENDERS,
    RECOMMENDERS,
    create_backend,
    error_decision,
    evaluate_item,
)
from .schemas import PREDICTION_SCHEMA, read_jsonl, validate_prediction


ROOT = Path(__file__).resolve().parents[1]


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _git_state() -> tuple[str, str, bool]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return commit, branch, dirty


def _parse_methods(value: str) -> list[str]:
    methods = [part.strip() for part in value.split(",") if part.strip()]
    if not methods:
        raise ValueError("at least one recommender is required")
    unknown = sorted(set(methods) - set(RECOMMENDERS))
    if unknown:
        raise ValueError("unknown recommenders: " + ", ".join(unknown))
    if len(methods) != len(set(methods)):
        raise ValueError("recommenders must not contain duplicates")
    return methods


def build_matrix(
    dataset: dict[str, Any],
    methods: list[str],
    *,
    split: str,
    repeats: int,
    seed: int,
    randomize_order: bool = False,
) -> list[tuple[str, dict[str, Any], int, int]]:
    if split not in {"development", "test", "all"}:
        raise ValueError("split must be development, test, or all")
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    items = [item for item in dataset["items"] if split == "all" or item["split"] == split]

    if not randomize_order:
        matrix: list[tuple[str, dict[str, Any], int, int]] = []
        for method_index, method in enumerate(methods):
            for item_index, item in enumerate(items):
                for repeat_index in range(repeats):
                    random_seed = seed + method_index * 1_000_000 + item_index * 1_000 + repeat_index
                    matrix.append((method, item, repeat_index, random_seed))
        return matrix

    # Counterbalance / randomize trials by repeat block
    full_matrix: list[tuple[str, dict[str, Any], int, int]] = []
    rng = random.Random(seed)
    for repeat_index in range(repeats):
        block: list[tuple[str, dict[str, Any], int, int]] = []
        for method_index, method in enumerate(methods):
            for item_index, item in enumerate(items):
                random_seed = seed + method_index * 1_000_000 + item_index * 1_000 + repeat_index
                block.append((method, item, repeat_index, random_seed))
        rng.shuffle(block)
        full_matrix.extend(block)
    return full_matrix


def _apply_cli_env_overrides(args: argparse.Namespace) -> None:
    """Safely apply CLI model, endpoint, and pricing overrides to environment variables in-memory."""

    if getattr(args, "external_endpoint", None):
        os.environ["EXTERNAL_LLM_ENDPOINT"] = args.external_endpoint
    if getattr(args, "external_model", None):
        os.environ["EXTERNAL_LLM_MODEL"] = args.external_model
    if getattr(args, "external_api_key", None):
        os.environ["EXTERNAL_LLM_API_KEY"] = args.external_api_key
    if getattr(args, "external_temperature", None) is not None:
        os.environ["EXTERNAL_LLM_TEMPERATURE"] = str(args.external_temperature)
    if getattr(args, "external_timeout", None) is not None:
        os.environ["EXTERNAL_LLM_TIMEOUT"] = str(args.external_timeout)

    if getattr(args, "pricing_config", None):
        os.environ["EXTERNAL_LLM_PRICING_CONFIG_PATH"] = str(args.pricing_config)
    if getattr(args, "prompt_price_per_m", None) is not None:
        os.environ["EXTERNAL_LLM_PROMPT_PRICE_PER_M"] = str(args.prompt_price_per_m)
    if getattr(args, "completion_price_per_m", None) is not None:
        os.environ["EXTERNAL_LLM_COMPLETION_PRICE_PER_M"] = str(args.completion_price_per_m)
    if getattr(args, "pricing_id", None):
        os.environ["EXTERNAL_LLM_PRICING_ID"] = str(args.pricing_id)
    if getattr(args, "pricing_date", None):
        os.environ["EXTERNAL_LLM_PRICING_DATE"] = str(args.pricing_date)
    if getattr(args, "pricing_source", None):
        os.environ["EXTERNAL_LLM_PRICING_SOURCE"] = str(args.pricing_source)

    if getattr(args, "ollama_endpoint", None):
        os.environ["SELF_HOSTED_LLM_ENDPOINT"] = args.ollama_endpoint
        os.environ["OLLAMA_ENDPOINT"] = args.ollama_endpoint
    if getattr(args, "ollama_model", None):
        os.environ["SELF_HOSTED_LLM_MODEL"] = args.ollama_model
        os.environ["OLLAMA_MODEL"] = args.ollama_model
    if getattr(args, "ollama_temperature", None) is not None:
        os.environ["SELF_HOSTED_LLM_TEMPERATURE"] = str(args.ollama_temperature)
        os.environ["OLLAMA_TEMPERATURE"] = str(args.ollama_temperature)
    if getattr(args, "ollama_timeout", None) is not None:
        os.environ["SELF_HOSTED_LLM_TIMEOUT"] = str(args.ollama_timeout)
        os.environ["OLLAMA_TIMEOUT"] = str(args.ollama_timeout)


def _configured_model_for(method: str) -> str | None:
    if method == "external_llm":
        return os.environ.get("EXTERNAL_LLM_MODEL") or "gemini-2.0-flash"
    if method in {"self_hosted_llm", "self_hosted_local_ollama_llm"}:
        return os.environ.get("SELF_HOSTED_LLM_MODEL") or os.environ.get("OLLAMA_MODEL") or "llama3:latest"
    return None


def run(args: argparse.Namespace) -> dict[str, Any]:
    _apply_cli_env_overrides(args)
    dataset = load_dataset(args.dataset)
    methods = _parse_methods(args.recommenders)
    randomize_order = getattr(args, "randomize_order", False)
    matrix = build_matrix(
        dataset,
        methods,
        split=args.split,
        repeats=args.repeats,
        seed=args.seed,
        randomize_order=randomize_order,
    )
    if args.dry_run:
        return {
            "dry_run": True,
            "dataset": dataset_summary(dataset),
            "recommenders": methods,
            "matrix_records": len(matrix),
            "repeats": args.repeats,
            "split": args.split,
            "seed": args.seed,
            "randomize_order": randomize_order,
        }

    predictions_path = args.output / "predictions.jsonl"
    existing_keys: set[tuple[str, str, int]] = set()

    if args.output.exists():
        if not getattr(args, "resume", False):
            raise FileExistsError(
                f"refusing to overwrite output directory {args.output} (use --resume to continue an existing run)"
            )
        if predictions_path.exists():
            existing_records = read_jsonl(predictions_path, validate_prediction)
            for rec in existing_records:
                existing_keys.add((str(rec["recommender"]), str(rec["sample_id"]), int(rec["repeat_index"])))
    else:
        args.output.mkdir(parents=True)

    commit, branch, dirty = _git_state()
    run_id = f"v4-recommenders-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    dataset_hash = canonical_sha256(dataset)

    backends: dict[str, Any] = {}
    blocked_backends: dict[str, str] = {}
    for method in methods:
        try:
            backends[method] = create_backend(method)
        except Exception as exc:
            exc_str = str(exc)
            if (
                "missing_credentials" in exc_str
                or "EXTERNAL_LLM_API_KEY" in exc_str
                or "EXTERNAL_LLM_ENDPOINT" in exc_str
                or "EXTERNAL_LLM_MODEL" in exc_str
            ):
                blocked_backends[method] = "missing_credentials"
            else:
                blocked_backends[method] = type(exc).__name__

    error_count = 0
    executed_count = len(existing_keys)

    # Open predictions in append mode (or exclusive create if brand new)
    file_mode = "a" if predictions_path.exists() else "x"
    with predictions_path.open(file_mode, encoding="utf-8") as handle:
        for method, item, repeat_index, random_seed in matrix:
            key = (method, str(item["sample_id"]), repeat_index)
            if key in existing_keys:
                continue

            started = time.monotonic()
            configured_model = _configured_model_for(method)
            if method in blocked_backends:
                error_count += 1
                reason = blocked_backends[method]
                decision = error_decision(
                    method,
                    RuntimeError(reason),
                    time.monotonic() - started,
                    model_id=configured_model,
                    error_category=reason,
                )
            else:
                try:
                    decision = evaluate_item(
                        method,
                        item,
                        backend=backends[method],
                        catalog_images=dataset["image_catalog"]["images"],
                    )
                except Exception as exc:
                    error_count += 1
                    decision = error_decision(
                        method,
                        exc,
                        time.monotonic() - started,
                        model_id=configured_model,
                    )

            record = {
                "schema_version": PREDICTION_SCHEMA,
                "run_id": run_id,
                "timestamp_utc": _now_utc(),
                "dataset_id": dataset["dataset_id"],
                "dataset_sha256": dataset_hash,
                "git_commit": commit,
                "sample_id": item["sample_id"],
                "workload_family": item["workload_family"],
                "split": item["split"],
                "recommender": method,
                "repeat_index": repeat_index,
                "random_seed": random_seed,
                **asdict(decision),
            }
            validate_prediction(record)
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            existing_keys.add(key)
            executed_count += 1

    methods_metadata: dict[str, Any] = {}
    for m in methods:
        if m == "static_profile_baseline":
            methods_metadata[m] = {
                "backend": "static_profile_baseline",
                "version": "evaluation-static-baseline-v1",
                "endpoint_class": "static_fixed_baseline",
                "model_id": None,
                "prompt_version": None,
                "prompt_template_sha256": None,
                "prompt_status": "not_applicable",
            }
        elif m == "rule_based_mapping":
            methods_metadata[m] = {
                "backend": "rule_based",
                "version": "rule-based-v1",
                "endpoint_class": "in_memory_rule_engine",
                "model_id": None,
                "prompt_version": None,
                "prompt_template_sha256": None,
                "prompt_status": "not_applicable",
            }
        elif m == "external_llm":
            ext_cfg = getattr(backends.get(m), "config", None)
            methods_metadata[m] = {
                "backend": "external_llm",
                "version": "external-llm-v1",
                "endpoint_class": "https_openai_compatible",
                "requested_model_id": _configured_model_for(m),
                "actual_model_id": getattr(ext_cfg, "model", None) or _configured_model_for(m),
                "temperature": getattr(ext_cfg, "temperature", 0.0),
                "timeout": getattr(ext_cfg, "timeout", 10.0),
                "pricing_id": getattr(getattr(ext_cfg, "pricing", None), "pricing_id", None),
                "prompt_version": "prompt-v4.0.0",
                "prompt_template_sha256": canonical_sha256(
                    "You recommend one JupyterHub resource profile and one administrator-allowlisted notebook image. "
                    "Return exactly one JSON object matching the provided schema. Do not include Markdown, code fences, or extra fields. "
                    "Never invent an image ID. Keep reasons concise and grounded only in the input."
                ),
            }
        elif m in {"self_hosted_local_ollama_llm", "self_hosted_llm"}:
            ollama_cfg = getattr(backends.get(m), "config", None)
            methods_metadata[m] = {
                "backend": "self_hosted_llm",
                "version": "self-hosted-ollama-v1",
                "endpoint_class": "local_ollama_http",
                "requested_model_id": _configured_model_for(m),
                "actual_model_id": getattr(ollama_cfg, "model", None) or _configured_model_for(m),
                "temperature": getattr(ollama_cfg, "temperature", 0.0),
                "timeout": getattr(ollama_cfg, "timeout", 10.0),
                "prompt_version": "prompt-v4.0.0",
                "prompt_template_sha256": canonical_sha256(
                    "You recommend one JupyterHub resource profile and one administrator-allowlisted notebook image. "
                    "Return exactly one JSON object matching the provided schema. Do not include Markdown, code fences, or extra fields. "
                    "Never invent an image ID. Keep reasons concise and grounded only in the input."
                ),
            }
        else:
            methods_metadata[m] = {
                "backend": m,
                "endpoint_class": "custom",
                "model_id": _configured_model_for(m),
                "prompt_version": None,
                "prompt_template_sha256": None,
                "prompt_status": "not_applicable",
            }

    manifest = {
        "protocol_version": "4.0.0",
        "experiment_id": "protocol-v4-four-method-eval",
        "run_id": run_id,
        "created_utc": _now_utc(),
        "dataset": dataset_summary(dataset),
        "dataset_path": str(args.dataset),
        "dataset_sha256": dataset_hash,
        "policy_version": "resource-image-policy-v1",
        "policy_sha256": canonical_sha256({"policy_version": "resource-image-policy-v1", "profiles": ["small", "medium", "large"]}),
        "catalog_version": dataset.get("image_catalog", {}).get("catalog_version", "unknown"),
        "catalog_sha256": canonical_sha256(dataset.get("image_catalog", {})),
        "git_commit": commit,
        "git_branch": branch,
        "git_worktree_dirty": dirty,
        "environment_id": getattr(args, "environment_id", "local-offline-evaluation"),
        "runtime_environment": {
            "python_version": sys.version.split()[0],
            "platform": sys.platform,
        },
        "split": args.split,
        "recommenders": methods,
        "methods_provenance": methods_metadata,
        "repeats": args.repeats,
        "seed": args.seed,
        "randomize_order": randomize_order,
        "records": executed_count,
        "errors": error_count,
        "blocked_backends": blocked_backends,
        "raw_outputs_append_only": True,
        "predictions_path": str(predictions_path),
        "predictions_sha256": file_sha256(predictions_path) if predictions_path.exists() else None,
    }
    manifest_path = args.output / "run-manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run protocol-v4 four-method recommender evaluation matrix."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--recommenders",
        default=",".join(DEFAULT_RECOMMENDERS),
        help="Comma-separated recommender names.",
    )
    parser.add_argument("--split", choices=("development", "test", "all"), default="test")
    parser.add_argument("--repeats", type=int, default=1, help="Number of repetitions per sample.")
    parser.add_argument("--seed", type=int, default=20260808, help="Deterministic random seed.")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "v4-predictions")
    parser.add_argument("--dry-run", action="store_true", help="Print matrix summary without executing.")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an incomplete run in an existing output directory.",
    )
    parser.add_argument(
        "--randomize-order",
        action="store_true",
        help="Randomize trial execution order by repeat block to reduce drift bias.",
    )

    # Config overrides for external and Ollama backends
    parser.add_argument("--external-endpoint", type=str, help="External LLM endpoint URL.")
    parser.add_argument("--external-model", type=str, help="External LLM model identifier.")
    parser.add_argument("--external-api-key", type=str, help="External LLM API key.")
    parser.add_argument("--external-temperature", type=float, help="External LLM temperature.")
    parser.add_argument("--external-timeout", type=float, help="External LLM request timeout (s).")

    # Pricing overrides
    parser.add_argument("--pricing-config", type=Path, help="Path to versioned JSON pricing configuration.")
    parser.add_argument("--prompt-price-per-m", type=float, help="Prompt price per million tokens (USD).")
    parser.add_argument("--completion-price-per-m", type=float, help="Completion price per million tokens (USD).")
    parser.add_argument("--pricing-id", type=str, help="Identifier for the token pricing version.")
    parser.add_argument("--pricing-date", type=str, help="Snapshot or effective date for pricing.")
    parser.add_argument("--pricing-source", type=str, help="Human-readable provenance or source URL for pricing.")

    parser.add_argument("--ollama-endpoint", type=str, help="Ollama local endpoint URL.")
    parser.add_argument("--ollama-model", type=str, help="Ollama model name or tag.")
    parser.add_argument("--ollama-temperature", type=float, help="Ollama temperature.")
    parser.add_argument("--ollama-timeout", type=float, help="Ollama request timeout (s).")

    args = parser.parse_args(argv)
    result = run(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
