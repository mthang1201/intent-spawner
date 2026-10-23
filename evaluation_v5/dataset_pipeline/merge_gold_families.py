#!/usr/bin/env python3
"""Merge DS-1000 and ARCADE gold families into unified Protocol-v5 gold authoring dataset."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation_v5.gold_dataset import current_catalog_identity, load_gold_dataset


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge DS-1000 and ARCADE gold families")
    parser.add_argument(
        "--ds1000-file",
        type=Path,
        default=PROJECT_ROOT / "benchmarks_v5" / "gold_families_ds1000.yaml",
        help="Path to DS-1000 gold families YAML",
    )
    parser.add_argument(
        "--arcade-file",
        type=Path,
        default=PROJECT_ROOT / "benchmarks_v5" / "gold_families_arcade_supplement.yaml",
        help="Path to ARCADE gold families YAML",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "benchmarks_v5" / "gold_families_unified_v5.yaml",
        help="Path to unified output YAML",
    )

    args = parser.parse_args()

    if not args.ds1000_file.is_file():
        raise FileNotFoundError(f"DS-1000 file not found: {args.ds1000_file}")
    if not args.arcade_file.is_file():
        raise FileNotFoundError(f"ARCADE file not found: {args.arcade_file}")

    with open(args.ds1000_file, "r", encoding="utf-8") as f:
        ds1000_doc = yaml.safe_load(f)
    with open(args.arcade_file, "r", encoding="utf-8") as f:
        arcade_doc = yaml.safe_load(f)

    ds1000_families = ds1000_doc.get("families", [])
    arcade_families = arcade_doc.get("families", [])

    print(f"Loaded {len(ds1000_families)} DS-1000 families and {len(arcade_families)} ARCADE families.")

    combined_families = list(ds1000_families) + list(arcade_families)

    # Combine source datasets
    source_datasets = []
    seen_sources = set()
    for doc in (ds1000_doc, arcade_doc):
        for s in doc.get("dataset_metadata", {}).get("source_datasets", []):
            sid = s.get("dataset_id")
            if sid not in seen_sources:
                seen_sources.add(sid)
                source_datasets.append(s)

    git_rev = ds1000_doc.get("dataset_metadata", {}).get("git_revision", "0000000000000000000000000000000000000000")

    unified_doc = {
        "schema_version": "protocol-v5-gold-family-v1.0.0",
        "dataset_metadata": {
            "dataset_id": "gold-unified-v5",
            "protocol_version": "5.0.0",
            "role": "development",
            "lifecycle": "reviewed",
            "created_at_utc": "2026-10-22T09:30:00Z",
            "created_by": "merge_gold_families.py",
            "git_revision": git_rev,
            "evidence_classification": "human_reviewed_development",
            "freeze_metadata": None,
            "source_datasets": source_datasets,
        },
        "catalog_identity": current_catalog_identity(),
        "review_policy": {
            "required_workload_strata": [
                "boundary",
                "data_processing",
                "deep_learning",
                "light",
                "machine_learning",
                "numerical_computing",
                "policy",
            ],
            "max_preferred_profile_share": 0.9,
            "max_preferred_image_share": 0.9,
        },
        "families": combined_families,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        yaml.dump(unified_doc, f, sort_keys=False, allow_unicode=True)

    print(f"Exported {len(combined_families)} unified families to {args.output}")

    # Validate unified dataset
    print("Validating unified dataset with evaluation_v5.gold_dataset...")
    loaded = load_gold_dataset(args.output)
    print(f"VALIDATION PASSED: dataset_id={loaded.dataset.dataset_metadata['dataset_id']}, total_families={len(loaded.dataset.families)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
