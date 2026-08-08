"""Run the protocol-v4 multi-recommender prediction matrix."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import subprocess
import time
from typing import Any
from uuid import uuid4

from .dataset import DEFAULT_DATASET, canonical_sha256, dataset_summary, load_dataset
from .recommenders import (
    DEFAULT_RECOMMENDERS,
    RECOMMENDERS,
    create_backend,
    error_decision,
    evaluate_item,
)
from .schemas import PREDICTION_SCHEMA, validate_prediction


ROOT = Path(__file__).resolve().parents[1]


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _git_state() -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
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
    return commit, dirty


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
) -> list[tuple[str, dict[str, Any], int, int]]:
    if split not in {"development", "test", "all"}:
        raise ValueError("split must be development, test, or all")
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    items = [item for item in dataset["items"] if split == "all" or item["split"] == split]
    matrix: list[tuple[str, dict[str, Any], int, int]] = []
    for method_index, method in enumerate(methods):
        for item_index, item in enumerate(items):
            for repeat_index in range(repeats):
                random_seed = seed + method_index * 1_000_000 + item_index * 1_000 + repeat_index
                matrix.append((method, item, repeat_index, random_seed))
    return matrix


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset = load_dataset(args.dataset)
    methods = _parse_methods(args.recommenders)
    matrix = build_matrix(
        dataset,
        methods,
        split=args.split,
        repeats=args.repeats,
        seed=args.seed,
    )
    if args.dry_run:
        return {
            "dry_run": True,
            "dataset": dataset_summary(dataset),
            "recommenders": methods,
            "matrix_records": len(matrix),
        }

    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite output directory {args.output}")
    commit, dirty = _git_state()
    run_id = f"v4-recommenders-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    dataset_hash = canonical_sha256(dataset)
    backends: dict[str, Any] = {}
    for method in methods:
        try:
            backends[method] = create_backend(method)
        except Exception as exc:
            raise RuntimeError(
                f"could not configure {method}; check its documented environment variables"
            ) from exc
    args.output.mkdir(parents=True)

    predictions_path = args.output / "predictions.jsonl"
    error_count = 0
    with predictions_path.open("x", encoding="utf-8") as handle:
        for method, item, repeat_index, random_seed in matrix:
            started = time.monotonic()
            try:
                decision = evaluate_item(
                    method,
                    item,
                    backend=backends[method],
                    catalog_images=dataset["image_catalog"]["images"],
                )
            except Exception as exc:
                error_count += 1
                decision = error_decision(method, exc, time.monotonic() - started)
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

    manifest = {
        "protocol_version": "4.0.0",
        "run_id": run_id,
        "created_utc": _now_utc(),
        "dataset": dataset_summary(dataset),
        "dataset_path": str(args.dataset),
        "git_commit": commit,
        "git_worktree_dirty": dirty,
        "split": args.split,
        "recommenders": methods,
        "repeats": args.repeats,
        "seed": args.seed,
        "records": len(matrix),
        "errors": error_count,
        "raw_outputs_append_only": True,
    }
    with (args.output / "run-manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run protocol-v4 recommender evaluation.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--recommenders",
        default=",".join(DEFAULT_RECOMMENDERS),
        help="Comma-separated recommender names.",
    )
    parser.add_argument("--split", choices=("development", "test", "all"), default="test")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260808)
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "v4-predictions")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = run(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
