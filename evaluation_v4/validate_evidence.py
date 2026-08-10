"""Validate protocol-v4 evaluation evidence integrity and immutability."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from .dataset import (
    DEFAULT_DATASET,
    canonical_sha256,
    dataset_index,
    file_sha256,
    load_dataset,
)
from .schemas import read_jsonl, validate_prediction


class EvidenceValidationError(RuntimeError):
    """The evaluation evidence is corrupt, incomplete, or invalid."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceValidationError(message)


def validate_evaluation_v4_evidence(
    evidence_dir: Path,
    dataset_path: Path = DEFAULT_DATASET,
    analysis_dir: Path | None = None,
) -> dict[str, Any]:
    """Validate predictions, run manifest, and immutability for evaluation_v4."""
    manifest_file = evidence_dir / "run-manifest.json"
    predictions_file = evidence_dir / "predictions.jsonl"

    _require(evidence_dir.is_dir(), f"evidence directory does not exist: {evidence_dir}")
    _require(manifest_file.is_file(), f"run-manifest.json missing in {evidence_dir}")
    _require(predictions_file.is_file(), f"predictions.jsonl missing in {evidence_dir}")

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise EvidenceValidationError(f"run-manifest.json is not valid JSON: {exc}") from exc

    _require(manifest.get("protocol_version") == "4.0.0", "manifest protocol_version must be 4.0.0")

    # 1. SHA-256 Checksum validation
    actual_pred_sha256 = file_sha256(predictions_file)
    expected_pred_sha256 = manifest.get("predictions_sha256")
    _require(
        actual_pred_sha256 == expected_pred_sha256,
        f"predictions.jsonl SHA-256 mismatch: actual {actual_pred_sha256} != expected {expected_pred_sha256}",
    )

    # 2. Dataset identity validation
    dataset = load_dataset(dataset_path)
    expected_dataset_hash = canonical_sha256(dataset)
    _require(
        manifest.get("dataset_sha256") == expected_dataset_hash,
        f"dataset SHA-256 in manifest ({manifest.get('dataset_sha256')}) does not match gold dataset ({expected_dataset_hash})",
    )

    # 3. Read and validate every record
    records = read_jsonl(predictions_file, validate_prediction)
    _require(len(records) == manifest.get("records"), f"record count mismatch: file has {len(records)}, manifest states {manifest.get('records')}")

    # 4. Check for duplicate keys
    seen_keys: set[tuple[str, str, int]] = set()
    for record in records:
        key = (str(record["recommender"]), str(record["sample_id"]), int(record["repeat_index"]))
        _require(key not in seen_keys, f"duplicate trial record in predictions.jsonl: {key}")
        seen_keys.add(key)

    # 5. Check expected samples if split is known
    split = manifest.get("split")
    recommenders = manifest.get("recommenders", [])
    repeats = manifest.get("repeats", 1)
    if split in {"development", "test"}:
        expected_items = [it for it in dataset["items"] if it["split"] == split]
        expected_total = len(expected_items) * len(recommenders) * repeats
        _require(
            len(records) == expected_total,
            f"expected {expected_total} trials for split={split}, recommenders={recommenders}, repeats={repeats}; found {len(records)}",
        )
        for r_name in recommenders:
            for item in expected_items:
                for rep in range(repeats):
                    k = (r_name, str(item["sample_id"]), rep)
                    _require(k in seen_keys, f"missing expected trial record: {k}")

    # 6. Validate analysis directory if supplied or if sidecar exists
    analysis_summary = None
    if analysis_dir is not None and analysis_dir.is_dir():
        analysis_manifest_file = analysis_dir / "analysis-manifest.json"
        report_file = analysis_dir / "REPORT.md"
        _require(analysis_manifest_file.is_file(), f"analysis-manifest.json missing in {analysis_dir}")
        _require(report_file.is_file(), f"REPORT.md missing in {analysis_dir}")
        try:
            analysis_manifest = json.loads(analysis_manifest_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise EvidenceValidationError(f"analysis-manifest.json invalid JSON: {exc}") from exc
        
        # Verify that analysis input_sha256 for predictions.jsonl matches actual
        input_shas = analysis_manifest.get("input_sha256", {})
        matched_sha = False
        for path_str, sha in input_shas.items():
            if path_str.endswith("predictions.jsonl"):
                _require(sha == actual_pred_sha256, f"analysis-manifest input SHA ({sha}) does not match current predictions.jsonl ({actual_pred_sha256})")
                matched_sha = True
        _require(matched_sha, "predictions.jsonl SHA not recorded in analysis-manifest.json")
        _require("claim_gates" in analysis_manifest, "claim_gates missing from analysis-manifest.json")
        analysis_summary = {
            "claim_gates": analysis_manifest.get("claim_gates"),
            "report_verified": True,
        }

    return {
        "status": "pass",
        "evidence_dir": str(evidence_dir),
        "run_id": manifest.get("run_id"),
        "records_validated": len(records),
        "predictions_sha256": actual_pred_sha256,
        "dataset_id": dataset["dataset_id"],
        "split": split,
        "recommenders": recommenders,
        "repeats": repeats,
        "analysis": analysis_summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate protocol-v4 evaluation evidence.")
    parser.add_argument("--dir", type=Path, required=True, help="Directory containing run-manifest.json and predictions.jsonl.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="Path to intent gold set.")
    parser.add_argument("--analysis-dir", type=Path, default=None, help="Optional analysis directory to validate.")
    args = parser.parse_args(argv)

    try:
        result = validate_evaluation_v4_evidence(
            args.dir.resolve(),
            dataset_path=args.dataset.resolve(),
            analysis_dir=args.analysis_dir.resolve() if args.analysis_dir else None,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except EvidenceValidationError as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
