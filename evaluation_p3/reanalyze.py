"""Create a non-overwriting analysis correction from preserved P3 raw evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from evaluation_p2.dataset import load_evaluation_dataset

from .metrics import aggregate_metrics


CORRECTION_SCHEMA_VERSION = "p2-p3-analysis-correction-v1.0.0"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def create_correction(
    run_directory: Path,
    *,
    correction_id: str,
    reason: str,
) -> Path:
    if not _ID_PATTERN.fullmatch(correction_id):
        raise ValueError("correction_id must be a bounded filesystem-safe identifier")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("correction reason must be non-blank")
    source = run_directory.resolve()
    source_manifest_path = source / "manifest.json"
    source_raw_path = source / "raw/predictions.jsonl"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    predictions = [
        json.loads(line)
        for line in source_raw_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    dataset = load_evaluation_dataset()
    if source_manifest.get("dataset_sha256") != dataset["dataset_sha256"]:
        raise RuntimeError("source evidence used a different dataset")

    metrics, paired, transitions = aggregate_metrics(dataset, predictions)
    target = source / "corrections" / correction_id
    target.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": CORRECTION_SCHEMA_VERSION,
        "correction_id": correction_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reason": reason.strip(),
        "source_run_id": source_manifest["run_id"],
        "source_manifest_sha256": _sha256(source_manifest_path),
        "source_raw_predictions_sha256": _sha256(source_raw_path),
        "source_raw_predictions_unchanged": True,
        "dataset_sha256": dataset["dataset_sha256"],
        "metrics_path": "metrics.json",
        "paired_changes_path": "paired_changes.json",
        "error_transitions_path": "error_transitions.json",
    }
    common = {
        "run_id": source_manifest["run_id"],
        "correction_id": correction_id,
        "dataset_sha256": dataset["dataset_sha256"],
    }
    _write_json(target / "metrics.json", {**metrics, **common})
    _write_json(target / "paired_changes.json", {**paired, **common})
    _write_json(target / "error_transitions.json", {**transitions, **common})
    _write_json(target / "correction-manifest.json", manifest)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--correction-id", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    target = create_correction(
        args.run_directory,
        correction_id=args.correction_id,
        reason=args.reason,
    )
    print(json.dumps({"correction_directory": str(target)}, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = ["CORRECTION_SCHEMA_VERSION", "create_correction"]
