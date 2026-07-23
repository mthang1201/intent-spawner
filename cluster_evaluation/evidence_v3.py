"""Fail-closed integrity and completeness validation for protocol-v3 evidence."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

from benchmarks.resource_envelope_runner import deterministic_seed
from cluster_evaluation.policies import PROFILE_RESOURCES
from cluster_evaluation.result_schema_v3 import validate_record
from cluster_evaluation.runner_v3 import ROOT


EXPECTED_ORIGINAL_TRIALS = {
    "calibration": 24,
    "ground-truth": 120,
    "comparative": 120,
    "jupyterhub": 45,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return payload


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: malformed JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number}: expected a JSON object")
        rows.append(row)
    return rows


def verify_sha256sums(directory: Path) -> dict[str, Any]:
    manifest = directory / "SHA256SUMS"
    if not manifest.is_file():
        raise ValueError(f"{directory}: missing SHA256SUMS")
    expected: dict[str, str] = {}
    for line_number, line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line:
            continue
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as exc:
            raise ValueError(f"{manifest}:{line_number}: malformed checksum line") from exc
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not relative
            or relative.startswith("/")
            or ".." in Path(relative).parts
        ):
            raise ValueError(f"{manifest}:{line_number}: invalid checksum entry")
        if relative in expected:
            raise ValueError(f"{manifest}: duplicate checksum path {relative}")
        expected[relative] = digest
    actual_paths = {
        str(path.relative_to(directory))
        for path in directory.rglob("*")
        if path.is_file() and path != manifest
    }
    missing = sorted(set(expected) - actual_paths)
    unexpected = sorted(actual_paths - set(expected))
    mismatches = sorted(
        relative
        for relative, digest in expected.items()
        if relative in actual_paths and sha256(directory / relative) != digest
    )
    if missing or unexpected or mismatches:
        raise ValueError(
            f"{directory}: integrity failure missing={missing} "
            f"unexpected={unexpected} mismatches={mismatches}"
        )
    return {
        "manifest": str(manifest),
        "verified_files": len(expected),
        "manifest_sha256": sha256(manifest),
    }


def _profile_fields(record: dict[str, Any]) -> dict[str, int]:
    expected = PROFILE_RESOURCES[record["applied_profile"]]
    return {
        "cpu_request_m": int(expected["cpu_request_m"]),
        "cpu_limit_m": int(expected["cpu_limit_m"]),
        "memory_request_mi": int(expected["memory_request_mi"]),
        "memory_limit_mi": int(expected["memory_limit_mi"]),
    }


def _validate_matrix(kind: str, matrix: list[dict[str, Any]]) -> None:
    expected = EXPECTED_ORIGINAL_TRIALS[kind]
    if len(matrix) != expected:
        raise ValueError(f"{kind}: expected {expected} matrix rows, found {len(matrix)}")
    run_ids = [row.get("run_id") for row in matrix]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError(f"{kind}: duplicate run IDs in matrix")
    indices = [row.get("plan_index") for row in matrix]
    if indices != list(range(expected)):
        raise ValueError(f"{kind}: plan indices are incomplete or out of order")


def validate_experiment(directory: Path, kind: str) -> dict[str, Any]:
    if kind not in EXPECTED_ORIGINAL_TRIALS:
        raise ValueError(f"unsupported v3 experiment kind {kind!r}")
    directory = directory.resolve()
    environment = _json(directory / "environment.json")
    matrix = _jsonl(directory / "matrix.jsonl")
    records = _jsonl(directory / "results.jsonl")
    _validate_matrix(kind, matrix)
    integrity = verify_sha256sums(directory)

    matrix_by_id = {row["run_id"]: row for row in matrix}
    replacement_path = directory / "replacement-matrix.jsonl"
    replacements = _jsonl(replacement_path) if replacement_path.is_file() else []
    replacement_by_id = {row["run_id"]: row for row in replacements}
    if len(replacement_by_id) != len(replacements):
        raise ValueError(f"{kind}: duplicate replacement run IDs")
    all_expected_ids = set(matrix_by_id) | set(replacement_by_id)

    record_ids = [record.get("run_id") for record in records]
    duplicate_record_ids = sorted(
        run_id for run_id, count in Counter(record_ids).items() if count > 1
    )
    if duplicate_record_ids:
        raise ValueError(f"{kind}: duplicate result run IDs {duplicate_record_ids}")
    unexpected = sorted(set(record_ids) - all_expected_ids)
    missing = sorted(all_expected_ids - set(record_ids))
    if unexpected or missing:
        raise ValueError(
            f"{kind}: result/matrix mismatch missing={missing} unexpected={unexpected}"
        )

    commits: set[str] = set()
    images: set[str] = set()
    invalid_originals: dict[str, dict[str, Any]] = {}
    failures: Counter[str] = Counter()
    for record in records:
        validate_record(record)
        if record["experiment_kind"] != kind:
            raise ValueError(f"{record['run_id']}: wrong experiment_kind")
        expected_row = matrix_by_id.get(record["run_id"]) or replacement_by_id.get(
            record["run_id"]
        )
        if expected_row is None:
            raise AssertionError("unreachable result/matrix mismatch")
        for field in (
            "workload_id",
            "repeat_index",
            "random_seed",
            "method",
            "applied_profile",
            "evaluation_set",
        ):
            if record[field] != expected_row[field]:
                raise ValueError(f"{record['run_id']}: record/matrix mismatch for {field}")
        expected_seed = deterministic_seed(
            record["workload_id"], record["repeat_index"]
        )
        if record["random_seed"] != expected_seed:
            raise ValueError(f"{record['run_id']}: seed differs from preregistration")
        for field, expected_value in _profile_fields(record).items():
            if record[field] != expected_value:
                raise ValueError(f"{record['run_id']}: inconsistent unit/resource field {field}")
        sidecar = directory / "runs" / record["run_id"] / "record.json"
        if not sidecar.is_file() or _json(sidecar) != record:
            raise ValueError(f"{record['run_id']}: sidecar differs from results stream")
        for relative, digest in record["supporting_evidence_sha256"].items():
            evidence_path = ROOT / relative
            if not evidence_path.is_file() or sha256(evidence_path) != digest:
                raise ValueError(f"{record['run_id']}: supporting evidence checksum mismatch")
        if set(record["supporting_log_paths"]) != set(
            record["supporting_evidence_sha256"]
        ):
            raise ValueError(f"{record['run_id']}: supporting evidence path mismatch")
        commits.add(record["git_commit"])
        images.add(record["container_image"])
        failures[record["failure_category"]] += 1
        if record["run_id"] in matrix_by_id and record["infrastructure_invalid"]:
            invalid_originals[record["run_id"]] = record

    if commits != {environment.get("git_commit")}:
        raise ValueError(f"{kind}: inconsistent Git commits {sorted(commits)}")
    if images != {environment.get("container_image")}:
        raise ValueError(f"{kind}: inconsistent image digests {sorted(images)}")
    expected_replacement_ids = {
        record["replacement_run_id"] for record in invalid_originals.values()
    }
    if expected_replacement_ids != set(replacement_by_id):
        raise ValueError(f"{kind}: replacement ledger does not match invalid originals")
    for original_id, original in invalid_originals.items():
        replacement = next(
            row
            for row in replacements
            if row["run_id"] == original["replacement_run_id"]
        )
        if replacement["random_seed"] != original["random_seed"]:
            raise ValueError(f"{original_id}: replacement changed the paired seed")
        replacement_record = next(
            record for record in records if record["run_id"] == replacement["run_id"]
        )
        if replacement_record["infrastructure_invalid"]:
            raise ValueError(f"{replacement['run_id']}: replacement also invalid")

    return {
        "kind": kind,
        "directory": str(directory),
        "expected_original_trials": EXPECTED_ORIGINAL_TRIALS[kind],
        "actual_original_trials": len(matrix),
        "replacement_trials": len(replacements),
        "result_records": len(records),
        "failures_by_category": dict(sorted(failures.items())),
        "exclusions": len(invalid_originals),
        "duplicate_run_ids": 0,
        "missing_records": 0,
        "git_commit": next(iter(commits)),
        "container_image": next(iter(images)),
        "integrity": integrity,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate protocol-v3 evidence.")
    parser.add_argument(
        "--experiment",
        action="append",
        default=[],
        metavar="KIND=PATH",
        help="experiment kind and directory; repeat for each retained phase",
    )
    args = parser.parse_args(argv)
    if not args.experiment:
        parser.error("at least one --experiment KIND=PATH is required")
    reports = []
    all_ids: list[str] = []
    for value in args.experiment:
        if "=" not in value:
            parser.error("--experiment must use KIND=PATH")
        kind, raw_path = value.split("=", 1)
        path = Path(raw_path)
        reports.append(validate_experiment(path, kind))
        all_ids.extend(record["run_id"] for record in _jsonl(path / "results.jsonl"))
    duplicates = sorted(
        run_id for run_id, count in Counter(all_ids).items() if count > 1
    )
    if duplicates:
        raise ValueError(f"duplicate run IDs across experiment directories: {duplicates}")
    print(json.dumps({"status": "pass", "experiments": reports}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
