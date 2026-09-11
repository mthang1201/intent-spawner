"""Attest that a tested-code to publication revision delta is publication-only.

The attestation deliberately keeps the two revisions distinct.  It classifies
every changed path and fails closed on executable, validation, schema,
workflow, test, configuration, deletion, symlink, or executable-mode changes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Sequence


SCHEMA_VERSION = "protocol-v5-publication-delta-attestation-v1.0.0"
RESULT_PREFIX = "results_v5/protocol-v5.0.0/final-audit/"
DOCUMENTATION_PATHS = {"docs/ARTIFACT_MANIFEST.md"}
DISALLOWED_PREFIXES = (
    "evaluation_v5/", "evaluation_v4/", "cluster_evaluation/", "recommender/",
    "tests/", "scripts/", "benchmarks/", "benchmarks_v5/", "config/", ".github/",
)
DISALLOWED_BASENAMES = {
    "Makefile", "pyproject.toml", "pytest.ini", "requirements.txt",
    "requirements-dev.txt", "requirements-analysis.txt", ".gitattributes",
}


def _git(root: Path, *args: str, text: bool = True) -> str | bytes:
    process = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=text, check=True
    )
    return process.stdout


def _revision(root: Path, value: str) -> str:
    revision = str(_git(root, "rev-parse", "--verify", value + "^{commit}")).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("revision did not resolve to a commit")
    return revision


def _changed_paths(root: Path, tested: str, publication: str) -> list[tuple[str, str]]:
    payload = _git(
        root, "diff", "--name-status", "--no-renames", "-z", tested, publication,
        text=False,
    )
    assert isinstance(payload, bytes)
    fields = payload.split(b"\0")
    if fields and not fields[-1]:
        fields.pop()
    if len(fields) % 2:
        raise ValueError("unexpected git name-status payload")
    return [
        (fields[index].decode("ascii"), fields[index + 1].decode("utf-8"))
        for index in range(0, len(fields), 2)
    ]


def _tree_entry(root: Path, revision: str, path: str) -> tuple[str | None, str | None]:
    raw = str(_git(root, "ls-tree", revision, "--", path)).strip()
    if not raw:
        return None, None
    metadata, listed = raw.split("\t", 1)
    if listed != path:
        raise ValueError("git tree returned an unexpected path")
    mode, object_type, object_id = metadata.split()
    if object_type != "blob":
        raise ValueError("publication path is not a blob")
    return mode, object_id


def _blob_sha256(root: Path, revision: str, path: str) -> str | None:
    process = subprocess.run(
        ["git", "show", f"{revision}:{path}"], cwd=root, capture_output=True, check=False
    )
    if process.returncode != 0:
        return None
    return hashlib.sha256(process.stdout).hexdigest()


def classify_path(path: str) -> str | None:
    if path in DOCUMENTATION_PATHS:
        return "DOCUMENTATION_INDEX"
    if path.startswith(RESULT_PREFIX):
        remainder = path.removeprefix(RESULT_PREFIX)
        artifact, separator, child = remainder.partition("/")
        if (
            separator
            and child
            and artifact.startswith(("final-audit-", "completion-audit-", "publication-attestation-"))
        ):
            return "GENERATED_AUDIT_REPORT_OR_EVIDENCE"
    return None


def build_attestation(root: Path, tested: str, publication: str) -> dict[str, Any]:
    root = root.resolve()
    tested_revision = _revision(root, tested)
    publication_revision = _revision(root, publication)
    if tested_revision == publication_revision:
        raise ValueError("tested and publication revisions must remain distinct")
    rows = []
    disallowed = []
    for status, path in _changed_paths(root, tested_revision, publication_revision):
        path_class = classify_path(path)
        mode, object_id = _tree_entry(root, publication_revision, path)
        reasons = []
        if status not in {"A", "M"}:
            reasons.append("DELETION_OR_UNSUPPORTED_STATUS")
        if path_class is None:
            reasons.append("DISALLOWED_PATH_CLASS")
        if path.startswith(DISALLOWED_PREFIXES) or Path(path).name in DISALLOWED_BASENAMES:
            reasons.append("EXECUTABLE_VALIDATOR_SCHEMA_WORKFLOW_TEST_OR_CONFIG_PATH")
        if mode not in {"100644"}:
            reasons.append("NON_REGULAR_OR_EXECUTABLE_MODE")
        row = {
            "status": status,
            "path": path,
            "path_class": path_class or "DISALLOWED",
            "git_mode": mode,
            "git_blob_oid": object_id,
            "sha256": _blob_sha256(root, publication_revision, path),
            "disallowed_reasons": sorted(set(reasons)),
        }
        rows.append(row)
        if reasons:
            disallowed.append({"path": path, "reasons": sorted(set(reasons))})
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "tested_code_revision": tested_revision,
        "publication_revision": publication_revision,
        "relationship": "DISTINCT_TESTED_CODE_AND_PUBLICATION_REVISIONS",
        "changed_path_count": len(rows),
        "changed_paths": rows,
        "disallowed_paths": disallowed,
        "executable_or_validation_semantics_changed": bool(disallowed),
        "verdict": "PASS" if rows and not disallowed else "FAIL",
    }


def verify_attestation(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported publication-delta attestation schema")
    current = build_attestation(
        root,
        str(payload.get("tested_code_revision")),
        str(payload.get("publication_revision")),
    )
    for key in (
        "tested_code_revision", "publication_revision", "relationship",
        "changed_path_count", "changed_paths", "disallowed_paths",
        "executable_or_validation_semantics_changed", "verdict",
    ):
        if payload.get(key) != current.get(key):
            raise ValueError("publication-delta attestation does not recompute: " + key)
    if current["verdict"] != "PASS":
        raise ValueError("publication delta includes a disallowed path class")
    return current


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("attest")
    create.add_argument("--tested", required=True)
    create.add_argument("--publication", required=True)
    create.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--attestation", type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    if args.command == "attest":
        payload = build_attestation(root, args.tested, args.publication)
        _write_json(args.output, payload)
    else:
        payload = json.loads(args.attestation.read_text(encoding="utf-8"))
        payload = verify_attestation(root, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
