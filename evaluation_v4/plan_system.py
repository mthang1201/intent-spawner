"""Generate a blocked, paired, randomized protocol-v4 system trial plan."""

from __future__ import annotations

from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import random
from typing import Any

from .dataset import DEFAULT_DATASET, canonical_sha256, load_dataset
from .recommenders import RECOMMENDERS


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_METHODS = (
    "static_small",
    "static_large",
    "rule_based_context",
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _methods(value: str) -> list[str]:
    methods = [part.strip() for part in value.split(",") if part.strip()]
    if not methods:
        raise ValueError("at least one system method is required")
    unknown = sorted(set(methods) - set(RECOMMENDERS))
    if unknown:
        raise ValueError("unknown system methods: " + ", ".join(unknown))
    if len(methods) != len(set(methods)):
        raise ValueError("system methods must not contain duplicates")
    return methods


def _paired_seed(master_seed: int, family: str, repeat_index: int) -> int:
    digest = hashlib.sha256(
        f"{master_seed}:{family}:{repeat_index}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:4], "big")


def build_system_plan(
    dataset: dict[str, Any],
    methods: list[str],
    *,
    repeats: int,
    seed: int,
) -> list[dict[str, Any]]:
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    items_by_family: dict[str, list[dict[str, Any]]] = {}
    for item in dataset["items"]:
        items_by_family.setdefault(item["workload_family"], []).append(item)
    plan: list[dict[str, Any]] = []
    generator = random.Random(seed)
    plan_index = 0
    for repeat_index in range(repeats):
        block: list[dict[str, Any]] = []
        for mapping in dataset["system_workload_mapping"]:
            family = mapping["workload_family"]
            representative = sorted(
                items_by_family[family],
                key=lambda item: (item["variant"] != "canonical", item["sample_id"]),
            )[0]
            paired_seed = _paired_seed(seed, family, repeat_index)
            for method in methods:
                block.append(
                    {
                        "plan_schema_version": "system-plan-v4.0.0",
                        "trial_id": f"v4-{family}-{method}-r{repeat_index:02d}",
                        "repeat_block": repeat_index,
                        "workload_family": family,
                        "representative_sample_id": representative["sample_id"],
                        "system_manifest_path": mapping["manifest_path"],
                        "system_workload_id": mapping["workload_id"],
                        "recommender": method,
                        "paired_workload_seed": paired_seed,
                        "cache_condition": "warm_required",
                    }
                )
        generator.shuffle(block)
        for record in block:
            record["plan_index"] = plan_index
            plan_index += 1
            plan.append(record)
    return plan


def write_plan(args: argparse.Namespace) -> dict[str, Any]:
    dataset = load_dataset(args.dataset)
    methods = _methods(args.methods)
    plan = build_system_plan(
        dataset,
        methods,
        repeats=args.repeats,
        seed=args.seed,
    )
    if args.dry_run:
        return {
            "dry_run": True,
            "records": len(plan),
            "families": len(dataset["system_workload_mapping"]),
            "methods": methods,
            "repeats": args.repeats,
            "seed": args.seed,
        }
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite plan directory {args.output}")
    args.output.mkdir(parents=True)
    with (args.output / "system-plan.jsonl").open("x", encoding="utf-8") as handle:
        for record in plan:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    manifest = {
        "protocol_version": "4.0.0",
        "created_utc": _now_utc(),
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": canonical_sha256(dataset),
        "methods": methods,
        "repeats": args.repeats,
        "seed": args.seed,
        "system_families": len(dataset["system_workload_mapping"]),
        "records": len(plan),
        "randomization": "within-repeat-block deterministic shuffle",
        "pairing": "same family/repeat uses the same paired_workload_seed",
        "cache_control": "all confirmatory trials require a verified warm image cache",
    }
    with (args.output / "plan-manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate protocol-v4 system trial plan.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--methods", default=",".join(DEFAULT_METHODS))
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260808)
    parser.add_argument("--output", type=Path, default=ROOT / "experiments" / "raw" / "v4-system-plan")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(write_plan(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
