"""Protocol-v5 split-isolation preflight; no recommender is executed."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping

from evaluation_v5.freeze import FreezeValidationError
from evaluation_v5.isolation import (
    CONFIRMATORY_DATASET_ENV_VAR,
    DEFAULT_SIMILARITY_THRESHOLD,
    FREEZE_ARTIFACT_ENV_VAR,
    SplitContaminationError,
    SplitIsolationError,
    load_confirmatory_split,
    resolve_confirmatory_sources,
)
from evaluation_v5.split_dataset import (
    DEFAULT_CONFIRMATORY_SPLIT_ID,
    DEFAULT_DEVELOPMENT_SPLIT_ID,
    LoadedSplit,
    SplitBundleValidationError,
    load_development_split,
)


PREFLIGHT_SCHEMA_VERSION = "protocol-v5-offline-preflight-v1.0.0"
DEVELOPMENT_ALIASES = frozenset({"development", "v5-development"})
CONFIRMATORY_ALIASES = frozenset({"confirmatory", "v5-confirmatory"})


def _split_summary(split: LoadedSplit) -> dict[str, Any]:
    manifest = split.manifest
    return {
        "dataset_id": manifest.dataset_id,
        "split_id": manifest.split_id,
        "role": manifest.role.value,
        "case_count": manifest.case_count,
        "family_count": manifest.family_count,
        "canonical_sha256": manifest.checksum,
        "file_sha256": split.source_file_sha256,
    }


def _base_result(split: LoadedSplit) -> dict[str, Any]:
    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "status": "NOT_EXECUTED",
        "claims_permitted": False,
        "experiment_executed": False,
        "split": _split_summary(split),
        "limitations": [
            "This command validates data access and isolation only.",
            "No recommender, participant study, cluster workload, or analysis was executed.",
        ],
    }


def _mode(value: str) -> str:
    if value in DEVELOPMENT_ALIASES:
        return "development"
    if value in CONFIRMATORY_ALIASES:
        return "confirmatory"
    allowed = sorted(DEVELOPMENT_ALIASES | CONFIRMATORY_ALIASES)
    raise SplitIsolationError(
        "--split must be one of: " + ", ".join(allowed)
    )


def run_preflight(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    selected = os.environ if environ is None else environ
    mode = _mode(args.split)
    if mode == "development":
        if args.dataset is not None or args.freeze is not None:
            raise SplitIsolationError(
                "development mode prohibits --dataset and --freeze"
            )
        if (
            CONFIRMATORY_DATASET_ENV_VAR in selected
            or FREEZE_ARTIFACT_ENV_VAR in selected
        ):
            raise SplitIsolationError(
                "development mode is prohibited while sealed-data inputs are configured"
            )
        expected = args.split_id or DEFAULT_DEVELOPMENT_SPLIT_ID
        development = load_development_split(expected_split_id=expected)
        result = _base_result(development)
        result["freeze_id"] = None
        result["contamination"] = {
            "status": "not_applicable_to_development_only_preflight"
        }
        return result

    dataset, freeze = resolve_confirmatory_sources(
        dataset_path=args.dataset,
        freeze_path=args.freeze,
        environ=selected,
    )
    expected = args.split_id or DEFAULT_CONFIRMATORY_SPLIT_ID
    loaded = load_confirmatory_split(
        dataset,
        freeze,
        expected_split_id=expected,
        similarity_threshold=args.similarity_threshold,
    )
    result = _base_result(loaded.split)
    result["freeze_id"] = loaded.freeze_manifest["freeze_id"]
    result["contamination"] = loaded.contamination.to_safe_dict()
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", required=True)
    parser.add_argument(
        "--split-id",
        help="Expected manifest split ID; defaults to the selected canonical v5 ID.",
    )
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--freeze", type=Path)
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=DEFAULT_SIMILARITY_THRESHOLD,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_preflight(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (
        FreezeValidationError,
        SplitBundleValidationError,
        SplitContaminationError,
        SplitIsolationError,
        OSError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {
                    "schema_version": PREFLIGHT_SCHEMA_VERSION,
                    "status": "ERROR",
                    "claims_permitted": False,
                    "error": str(exc),
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["PREFLIGHT_SCHEMA_VERSION", "run_preflight"]
