#!/usr/bin/env python3
"""Build the immutable v1.3 final-audit inventory from reviewed roots.

The script only adds exact current bytes to a new inventory.  It never edits
an earlier inventory or evidence package.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks_v5/protocol-v5-final-audit-inputs-v3.json"
CANDIDATES = ROOT / "benchmarks_v5/protocol-v5-final-evidence-candidates-v1.json"
SCHEMA = ROOT / "benchmarks_v5/protocol-v5-evidence-disposition-v1.schema.json"
OUTPUT = ROOT / "benchmarks_v5/protocol-v5-final-audit-inputs-v4.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    inventory = json.loads(SOURCE.read_text(encoding="utf-8"))
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    files = dict(inventory["files"])
    for path in (CANDIDATES, SCHEMA):
        files[str(path.relative_to(ROOT))] = sha256(path)
    for relative in candidates["preserved_roots"]:
        root = ROOT / relative
        if not root.is_dir():
            raise SystemExit("reviewed evidence root is missing: " + relative)
        for path in sorted(root.rglob("*")):
            if path.is_file():
                files[str(path.relative_to(ROOT))] = sha256(path)
    inventory.update(
        schema_version="protocol-v5-final-audit-inputs-v1.3.0",
        created_at_utc="2026-09-16T00:00:00Z",
        source_git_revision="75611764c2d5d18960d3f08256fb9134a37b2810",
        scope=(
            "Checksum-bound Protocol-v5 final observed-evidence integration. "
            "Existing inventories and evidence remain unchanged; incompatible "
            "and nonconfirmatory observations are preserved without promotion."
        ),
        candidate_inventory=str(CANDIDATES.relative_to(ROOT)),
        disposition_schema=str(SCHEMA.relative_to(ROOT)),
        authoritative_freeze=candidates["authoritative_freeze"]["manifest_path"],
        files=dict(sorted(files.items())),
    )
    inventory["packages"] = list(inventory["packages"]) + [
        "results_v5/protocol-v5.0.0/E5/e5-image-validation-20260912T124154Z",
        "results_v5/protocol-v5.0.0/E5/e5-storage-scalability-20260912T124502Z",
        "results_v5/protocol-v5.0.0/E5/e5-storage-scalability-20260914T012024Z",
    ]
    OUTPUT.write_text(
        json.dumps(inventory, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
