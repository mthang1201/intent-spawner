"""Capture revision-bound command evidence without altering command exit status."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import subprocess
import xml.etree.ElementTree as ET
from typing import Any, Sequence

from .common import ROOT, file_sha256, read_json, write_bytes, write_json


SCHEMA_VERSION = "protocol-v5-command-evidence-v1.0.0"
PACKAGE_SCHEMA_VERSION = "protocol-v5-command-evidence-package-v1.0.0"
ALLOWED_CLASSIFICATIONS = {
    "PASS",
    "IMPLEMENTATION_DEFECT",
    "UNAVAILABLE_REAL_EXPERIMENT_EVIDENCE",
    "UNAVAILABLE_FREEZE_CUSTODY",
    "IMMUTABLE_HISTORICAL_COMPATIBILITY_BOUNDARY",
    "SCIENTIFICALLY_CORRECT_FAIL_CLOSED_STATE",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def junit_details(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    cases: dict[str, str] = {}
    for case in root.iter("testcase"):
        classname = str(case.get("classname") or "").replace(".", "/") + ".py"
        nodeid = classname + "::" + str(case.get("name") or "")
        if case.find("failure") is not None:
            status = "FAILED"
        elif case.find("error") is not None:
            status = "ERROR"
        elif case.find("skipped") is not None:
            status = "SKIPPED"
        else:
            status = "PASSED"
        cases[nodeid] = status
    counts = Counter(cases.values())
    return {
        "total": len(cases),
        "passed": counts.get("PASSED", 0),
        "failed": counts.get("FAILED", 0),
        "errors": counts.get("ERROR", 0),
        "skipped": counts.get("SKIPPED", 0),
        "cases": cases,
    }


def verify_command_evidence(directory: Path) -> dict[str, Any]:
    directory = directory.resolve()
    manifest = read_json(directory / "manifest.json")
    if manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise ValueError("unsupported command-evidence package schema")
    outputs = manifest.get("output_checksums") or {}
    for relative, digest in outputs.items():
        path = directory / relative
        if not path.is_file() or file_sha256(path) != digest:
            raise ValueError("command-evidence output checksum mismatch: " + str(relative))
    expected_files = set(outputs) | {"manifest.json", "SHA256SUMS"}
    actual_files = {
        str(path.relative_to(directory))
        for path in directory.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        raise ValueError("command-evidence package contains unregistered files")
    sums = {**outputs, "manifest.json": file_sha256(directory / "manifest.json")}
    expected_sums = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(sums.items())
    ).encode()
    if (directory / "SHA256SUMS").read_bytes() != expected_sums:
        raise ValueError("command-evidence checksum manifest mismatch")
    record = read_json(directory / "record.json")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported command-evidence record schema")
    for key in ("evidence_id", "kind", "git_revision"):
        if manifest.get(key) != record.get(key):
            raise ValueError(f"command-evidence manifest/record {key} mismatch")
    if set(record.get("classifications") or []) - ALLOWED_CLASSIFICATIONS:
        raise ValueError("command evidence has an invalid classification")
    return record


def capture(
    *,
    root: Path,
    output: Path,
    evidence_id: str,
    kind: str,
    classifications: list[str],
    command: list[str],
    junit: Path | None,
) -> int:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,80}", evidence_id):
        raise ValueError("invalid evidence id")
    if not command:
        raise ValueError("a command is required")
    if not classifications or set(classifications) - ALLOWED_CLASSIFICATIONS:
        raise ValueError("invalid or missing command classification")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if junit is not None:
        junit = junit.resolve()
        try:
            junit.relative_to(output)
        except ValueError as exc:
            raise ValueError("JUnit output must be inside the command-evidence directory") from exc

    revision = _git(root, "rev-parse", "HEAD")
    dirty_before = bool(_git(root, "status", "--porcelain"))
    started = _utc_now()
    process = subprocess.run(command, cwd=root, capture_output=True)
    finished = _utc_now()
    write_bytes(output / "stdout.txt", process.stdout)
    write_bytes(output / "stderr.txt", process.stderr)
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": evidence_id,
        "kind": kind,
        "git_revision": revision,
        "git_dirty_before": dirty_before,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "command": shlex.join(command),
        "argv": command,
        "exit_code": process.returncode,
        "classifications": sorted(set(classifications)),
        "stdout": {"path": "stdout.txt", "sha256": file_sha256(output / "stdout.txt")},
        "stderr": {"path": "stderr.txt", "sha256": file_sha256(output / "stderr.txt")},
    }
    if junit is not None:
        if not junit.is_file():
            record["junit"] = {"path": str(junit.relative_to(output)), "missing": True}
        else:
            record["junit"] = {
                "path": str(junit.relative_to(output)),
                "sha256": file_sha256(junit),
                "counts": {key: value for key, value in junit_details(junit).items() if key != "cases"},
            }
    write_json(output / "record.json", record)
    files = {
        str(path.relative_to(output)): file_sha256(path)
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "evidence_id": evidence_id,
        "kind": kind,
        "git_revision": revision,
        "output_checksums": files,
    }
    write_json(output / "manifest.json", manifest)
    sums = {**files, "manifest.json": file_sha256(output / "manifest.json")}
    write_bytes(
        output / "SHA256SUMS",
        "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())).encode(),
    )
    print(json.dumps(record, indent=2, sort_keys=True))
    return process.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--kind", choices=("test", "validator", "workflow"), required=True)
    parser.add_argument("--classification", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--junit", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    return capture(
        root=ROOT,
        output=args.output,
        evidence_id=args.evidence_id,
        kind=args.kind,
        classifications=args.classification,
        command=command,
        junit=args.junit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
