"""Create a derived four-method view without mutating historical predictions."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .dataset import file_sha256
from .schemas import read_jsonl, validate_prediction


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def combine(baseline_dir: Path, external_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Replace only unavailable historical external cells in a new derived view."""

    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory {output_dir}")

    baseline_manifest = json.loads(
        (baseline_dir / "run-manifest.json").read_text(encoding="utf-8")
    )
    external_manifest = json.loads(
        (external_dir / "run-manifest.json").read_text(encoding="utf-8")
    )
    baseline_rows = read_jsonl(baseline_dir / "predictions.jsonl", validate_prediction)
    external_rows = read_jsonl(external_dir / "predictions.jsonl", validate_prediction)
    historical_external = [
        row for row in baseline_rows if row["recommender"] == "external_llm"
    ]

    if not historical_external:
        raise ValueError("baseline evidence has no external_llm cells to replace")
    if any(
        row.get("error_category") != "missing_credentials"
        or row.get("fallback_used") is not False
        for row in historical_external
    ):
        raise ValueError(
            "historical external cells are not uniformly unavailable missing-credentials records"
        )
    if any(row["recommender"] != "external_llm" for row in external_rows):
        raise ValueError("replacement evidence must contain only external_llm records")

    historical_keys = {
        (str(row["sample_id"]), int(row["repeat_index"]))
        for row in historical_external
    }
    replacement_keys = {
        (str(row["sample_id"]), int(row["repeat_index"]))
        for row in external_rows
    }
    if len(replacement_keys) != len(external_rows) or replacement_keys != historical_keys:
        raise ValueError("replacement external matrix keys do not exactly match historical cells")
    if {
        str(row["dataset_sha256"]) for row in baseline_rows + external_rows
    } != {str(baseline_manifest["dataset_sha256"])}:
        raise ValueError("source evidence dataset identities do not match")

    combined_rows = [
        row for row in baseline_rows if row["recommender"] != "external_llm"
    ] + external_rows
    combined_keys = {
        (str(row["recommender"]), str(row["sample_id"]), int(row["repeat_index"]))
        for row in combined_rows
    }
    if len(combined_keys) != len(combined_rows):
        raise ValueError("derived combined view contains duplicate prediction keys")

    output_dir.mkdir(parents=True)
    predictions_path = output_dir / "predictions.jsonl"
    with predictions_path.open("x", encoding="utf-8") as handle:
        for row in combined_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    predictions_sha256 = file_sha256(predictions_path)
    created_utc = _now_utc()
    manifest = copy.deepcopy(baseline_manifest)
    manifest.update(
        {
            "experiment_id": f"protocol-v4-combined-authoritative-{created_utc}",
            "run_id": f"v4-combined-{created_utc}",
            "created_utc": created_utc,
            "git_commit": external_manifest["git_commit"],
            "git_branch": external_manifest["git_branch"],
            "git_worktree_dirty": False,
            "seed": None,
            "seeds_by_method": {
                method: (
                    external_manifest["seed"]
                    if method == "external_llm"
                    else baseline_manifest["seed"]
                )
                for method in baseline_manifest["recommenders"]
            },
            "expected_record_count": len(combined_rows),
            "observed_record_count": len(combined_rows),
            "records": len(combined_rows),
            "errors": sum(
                row.get("error_category") is not None for row in combined_rows
            ),
            "blocked_backends": {},
            "predictions_path": str(predictions_path),
            "predictions_sha256": predictions_sha256,
            "raw_outputs_append_only": True,
            "derived_combined_view": True,
            "source_evidence": {
                str(baseline_dir): {
                    "role": (
                        "authoritative non-external rows; historical external "
                        "missing-credentials rows excluded"
                    ),
                    "predictions_sha256": baseline_manifest["predictions_sha256"],
                    "included_records": len(combined_rows) - len(external_rows),
                },
                str(external_dir): {
                    "role": "authoritative live external_llm rows",
                    "predictions_sha256": external_manifest["predictions_sha256"],
                    "included_records": len(external_rows),
                    "run_id": external_manifest["run_id"],
                },
            },
        }
    )
    manifest["methods_provenance"]["external_llm"] = external_manifest[
        "methods_provenance"
    ]["external_llm"]
    manifest["checksums"]["predictions.jsonl"] = predictions_sha256
    (output_dir / "run-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a derived four-method view with live external results."
    )
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--external-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = combine(args.baseline_dir, args.external_dir, args.output)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
