#!/usr/bin/env python3
"""Build the immutable post-merge audit inventory without rewriting v4."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks_v5/protocol-v5-final-audit-inputs-v4.json"
MAIN_INVENTORY = ROOT / "benchmarks_v5/protocol-v5-final-audit-inputs-v3.json"
OUTPUT = ROOT / "benchmarks_v5/protocol-v5-final-audit-inputs-v5.json"
RECONCILED_PATH = "recommender/jupyterhub_integration.py"
MERGE_REVISION = "06bdaf87ee7184ba83de6d46b89bead284080347"
PREMERGE_MAIN = "0b9cdbc46cb1dfee626876b21726ede0d7f466af"
REVIEWED_INTEGRATION = "42fe3809a36879f82e1c9891b7a15cc044eb6b0e"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUTPUT.exists():
        raise SystemExit("post-merge inventory already exists")
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if revision != MERGE_REVISION:
        raise SystemExit("inventory must be generated at the reviewed merge commit")

    inventory = json.loads(SOURCE.read_text(encoding="utf-8"))
    main_inventory = json.loads(MAIN_INVENTORY.read_text(encoding="utf-8"))
    previous = inventory["files"][RECONCILED_PATH]
    expected = main_inventory["files"][RECONCILED_PATH]
    actual = sha256(ROOT / RECONCILED_PATH)
    if actual != expected:
        raise SystemExit("post-freeze main product checksum is not the reviewed main identity")

    inventory["files"][RECONCILED_PATH] = actual
    inventory.update(
        created_at_utc="2026-09-16T09:11:11Z",
        source_git_revision=MERGE_REVISION,
        scope=(
            "Checksum-bound Protocol-v5 post-merge audit inventory. Evidence, "
            "freeze identities, selection, and claim inputs are inherited byte-for-byte "
            "from v4; only the pre-existing main product UI identity is reconciled."
        ),
        postmerge_reconciliation={
            "merge_revision": MERGE_REVISION,
            "premerge_main_revision": PREMERGE_MAIN,
            "reviewed_integration_revision": REVIEWED_INTEGRATION,
            "base_inventory": str(SOURCE.relative_to(ROOT)),
            "base_inventory_sha256": sha256(SOURCE),
            "reconciled_current_product_paths": {
                RECONCILED_PATH: {
                    "integration_sha256": previous,
                    "premerge_main_sha256": expected,
                    "postmerge_sha256": actual,
                    "evidence_or_freeze_input": False,
                }
            },
        },
    )
    OUTPUT.write_text(
        json.dumps(inventory, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
