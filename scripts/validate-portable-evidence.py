#!/usr/bin/env python3
"""Validate and reproduce headline Protocol-v4 results from the portable core."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation_v4.analyze import analyze
from evaluation_v4.schemas import read_jsonl, validate_system_trial
from evaluation_v4.validate_evidence import validate_evaluation_v4_evidence


CHECKSUM_MANIFEST = ROOT / "docs/evaluation/PROTOCOL_V4_PORTABLE_SHA256SUMS.txt"
RECOMMENDATION_RUNS = (
    ("v4-external-confirmatory-20260813T045543Z", 240),
    ("v4-revised-test-20260812T095453Z", 960),
    ("v4-combined-evidence-20260813T050500Z", 960),
)
PLAN_DIR = ROOT / "results/v4-stage-c-confirmatory-plan-20260813T021239Z"
STAGE_C_DIR = ROOT / "results/v4-stage-c-confirmatory-20260813T021600Z"
EXPECTED_CLAIM_GATES = {
    "RQ1": "CLAIMABLE",
    "RQ2": "CLAIMABLE",
    "RQ3": "PARTIALLY CLAIMABLE",
    "RQ4": "CLAIMABLE",
    "RQ5": "CLAIMABLE",
}


class PortableEvidenceError(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PortableEvidenceError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checksum_entries(path: Path) -> list[tuple[str, str]]:
    entries = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        parts = line.split("  ", 1)
        _require(len(parts) == 2, f"invalid checksum line {line_number} in {path}")
        digest, relative = parts
        _require(len(digest) == 64, f"invalid SHA-256 at line {line_number} in {path}")
        entries.append((digest, relative))
    return entries


def _validate_checksums() -> dict[str, object]:
    entries = _checksum_entries(CHECKSUM_MANIFEST)
    _require(len(entries) == 13, "portable checksum manifest must contain 13 files")
    total_bytes = 0
    for expected, relative in entries:
        path = ROOT / relative
        _require(path.is_file(), f"portable evidence file is missing: {relative}")
        actual = _sha256(path)
        _require(actual == expected, f"portable evidence checksum mismatch: {relative}")
        total_bytes += path.stat().st_size
    return {"files": len(entries), "bytes": total_bytes}


def _validate_recommendations() -> dict[str, int]:
    counts = {}
    for directory, expected in RECOMMENDATION_RUNS:
        result = validate_evaluation_v4_evidence(ROOT / "results" / directory)
        actual = int(result["records_validated"])
        _require(actual == expected, f"{directory} has {actual} records; expected {expected}")
        counts[directory] = actual
    return counts


def _validate_stage_c() -> dict[str, object]:
    trials = read_jsonl(STAGE_C_DIR / "system-trials.jsonl", validate_system_trial)
    _require(len(trials) == 320, "Stage C must contain 320 system trials")
    trial_ids = [str(item["trial_id"]) for item in trials]
    _require(len(set(trial_ids)) == 320, "Stage C trial IDs must be unique")
    _require(all(item["evidence_class"] == "observed" for item in trials), "Stage C portable records must remain observed evidence")

    plan = [
        json.loads(line)
        for line in (PLAN_DIR / "system-plan.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    _require(len(plan) == 320, "Stage C plan must contain 320 records")
    _require([item["plan_index"] for item in plan] == list(range(320)), "Stage C plan indexes are not contiguous")
    _require([item["trial_id"] for item in plan] == trial_ids, "Stage C trial order does not match the pre-registered plan")

    plan_manifest = json.loads((PLAN_DIR / "plan-manifest.json").read_text(encoding="utf-8"))
    run_manifest = json.loads((STAGE_C_DIR / "run-manifest.json").read_text(encoding="utf-8"))
    completion = json.loads((STAGE_C_DIR / "completion-manifest.json").read_text(encoding="utf-8"))
    _require(plan_manifest["records"] == 320, "Stage C plan manifest count is invalid")
    _require(run_manifest["plan_sha256"] == _sha256(PLAN_DIR / "system-plan.jsonl"), "Stage C run manifest plan hash mismatch")
    _require(completion["expected_record_count"] == completion["observed_record_count"] == 320, "Stage C completion count mismatch")
    _require(completion["checksums"]["system-trials.jsonl"] == _sha256(STAGE_C_DIR / "system-trials.jsonl"), "Stage C completion checksum mismatch")

    deep_entries = _checksum_entries(STAGE_C_DIR / "SHA256SUMS")
    deep_map = {relative: digest for digest, relative in deep_entries}
    for name in ("completion-manifest.json", "environment.json", "run-manifest.json", "system-trials.jsonl"):
        _require(deep_map.get(name) == _sha256(STAGE_C_DIR / name), f"deep-archive checksum mismatch for {name}")
    return {
        "records": len(trials),
        "unique_trial_ids": len(set(trial_ids)),
        "plan_records": len(plan),
        "deep_archive_checksum_entries": len(deep_entries),
        "deep_archive_sidecars_present": sum((STAGE_C_DIR / relative).is_file() for _, relative in deep_entries),
    }


def _rows_by_key(path: Path, key: str) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row[key]: row for row in csv.DictReader(handle)}


def _reproduce_headlines(bootstrap_replicates: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="intent-spawner-portable-") as temporary:
        output = Path(temporary) / "analysis"
        manifest = analyze(
            SimpleNamespace(
                dataset=ROOT / "benchmarks/intent-gold-v4.yaml",
                predictions=ROOT / "results/v4-combined-evidence-20260813T050500Z/predictions.jsonl",
                system_trials=STAGE_C_DIR / "system-trials.jsonl",
                user_events=None,
                reprovision_trials=None,
                out=output,
                bootstrap_replicates=bootstrap_replicates,
                seed=20260808,
            )
        )
        statuses = {
            name: gate["status"] for name, gate in manifest["claim_gates"].items()
        }
        _require(statuses == EXPECTED_CLAIM_GATES, "reproduced Protocol-v4 claim gates changed")
        recommendations = _rows_by_key(output / "recommendation-summary.csv", "recommender")
        systems = _rows_by_key(output / "system-effectiveness.csv", "recommender")
        expected_points = {
            ("recommendation", "external_llm", "raw_valid_response_rate"): 0.0875,
            ("recommendation", "rule_based_mapping", "joint_acceptable_rate"): 0.6875,
            ("recommendation", "self_hosted_local_ollama_llm", "joint_acceptable_rate"): 0.4375,
            ("system", "static_large", "workload_success_rate"): 1.0,
            ("system", "static_small", "workload_success_rate"): 0.3625,
        }
        for (kind, method, metric), expected in expected_points.items():
            rows = recommendations if kind == "recommendation" else systems
            actual = float(rows[method][metric])
            _require(actual == expected, f"reproduced headline changed: {method}.{metric}")
        return {
            "claim_gates": statuses,
            "headline_points": {
                f"{method}.{metric}": expected
                for (_, method, metric), expected in expected_points.items()
            },
            "temporary_output_removed": True,
        }


def validate_portable_bundle(*, bootstrap_replicates: int = 200) -> dict[str, object]:
    return {
        "status": "pass",
        "portable_bundle": _validate_checksums(),
        "recommendation_matrices": _validate_recommendations(),
        "stage_c": _validate_stage_c(),
        "reproduced_analysis": _reproduce_headlines(bootstrap_replicates),
        "boundary": "Stage C per-trial sidecars are external deep-archive evidence; their checksum manifest is portable.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap-replicates", type=int, default=200)
    args = parser.parse_args()
    try:
        result = validate_portable_bundle(bootstrap_replicates=args.bootstrap_replicates)
    except (OSError, ValueError, KeyError, PortableEvidenceError) as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
