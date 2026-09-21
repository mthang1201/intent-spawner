#!/usr/bin/env python3
"""Build the immutable v1.4 final-audit inventory from v5 without rewriting it.

This is a purely additive extension: it registers the new v1.2 claim-registry
amendment (yaml + schema) that adds an exploratory P1_CATALOG comparator to
H5, alongside the untouched v1.1 registry entries which remain in the
inventory for historical reproducibility. No existing entry is modified.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks_v5/protocol-v5-final-audit-inputs-v5.json"
OUTPUT = ROOT / "benchmarks_v5/protocol-v5-final-audit-inputs-v6.json"
NEW_FILES = (
    ROOT / "benchmarks_v5/protocol-v5-claim-registry-v1.2.yaml",
    ROOT / "benchmarks_v5/protocol-v5-claim-registry-v1.2.schema.json",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUTPUT.exists():
        raise SystemExit("inventory already exists")
    inventory = json.loads(SOURCE.read_text(encoding="utf-8"))
    files = dict(inventory["files"])
    for path in NEW_FILES:
        if not path.is_file():
            raise SystemExit("expected new registry input is missing: " + str(path))
        files[str(path.relative_to(ROOT))] = sha256(path)
    inventory.update(
        schema_version="protocol-v5-final-audit-inputs-v1.4.0",
        created_at_utc="2026-09-21T00:00:00Z",
        scope=(
            "Checksum-bound Protocol-v5 final-audit inventory. Evidence, freeze "
            "identities, selection, claim inputs, and the post-merge reconciliation "
            "are inherited byte-for-byte from v5; the only addition is the v1.2 "
            "claim-registry amendment (yaml + schema) that adds an exploratory "
            "P1_CATALOG comparator to H5. The v1.1 registry and schema remain in "
            "this inventory unchanged as the historical frozen predeclaration."
        ),
        files=dict(sorted(files.items())),
    )
    OUTPUT.write_text(
        json.dumps(inventory, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
