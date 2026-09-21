#!/usr/bin/env python3
"""Build the v1.6 final-audit inventory: register the v3 freeze identity.

Purely additive over v7. No existing entry is modified and the package list is
byte-identical, so this changes no scientific disposition.

Three files are added:

  * the `v5-final-execution-freeze-v3` manifest and its supersession note;
  * `frozen-configuration-v3.json`, the design snapshot extracted from that
    freeze, recorded so a future run can bind to v3 the way
    20260921T-observed-p1-p2-development-v2 binds to v2.

v3 supersedes v2 without changing P2's identity. It exists because restoring
legacy validity for pre-3f896eb E4 packages changed the E4 harness that v2's
`experiment_contracts.E4` section hashes, and because v2's manifest had been
committed alongside other files and so failed
`verify_production_freeze`. v3's manifest was introduced by commit d3a409d,
which contains nothing else, and verifies at that commit.

`authoritative_freeze` is deliberately still `v5-final-execution-freeze`, for
the same reason documented in the v7 builder: every package in `packages` was
collected under it, and `build_dispositions` requires the input inventory and
the candidate inventory to name the same freeze. Promoting a newer identity
would make E5_STORAGE_RERUN INCOMPATIBLE_GLOBAL_EXECUTION_SHA ->
REJECTED_INTEGRITY and flip claim H7 off SUPPORTED, which is a scientific
re-disposition rather than an inventory refresh. `superseding_freeze` now
names v3 and retains v2 in its chain.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks_v5/protocol-v5-final-audit-inputs-v7.json"
OUTPUT = ROOT / "benchmarks_v5/protocol-v5-final-audit-inputs-v8.json"

FREEZE_V2 = "results_v5/protocol-v5.0.0/freezes/v5-final-execution-freeze-v2"
FREEZE_V3 = "results_v5/protocol-v5.0.0/freezes/v5-final-execution-freeze-v3"
SNAPSHOT_V3 = "results_v5/protocol-v5.0.0/freezes/frozen-configuration-v3.json"

ADDED = (
    FREEZE_V3 + "/freeze-manifest.json",
    FREEZE_V3 + "/SUPERSESSION.md",
    SNAPSHOT_V3,
)

SCOPE = (
    "Checksum-bound Protocol-v5 final-audit inventory. Purely additive over "
    "v7: no existing entry is modified, the package list is byte-identical, "
    "and `authoritative_freeze` still names v5-final-execution-freeze because "
    "every registered package was collected under it and the candidate "
    "inventory is bound to it. The addition is the "
    "v5-final-execution-freeze-v3 identity - manifest, supersession note and "
    "design snapshot - which supersedes v2 without changing P2's "
    "configuration identity. v3 exists because restoring legacy validity for "
    "pre-3f896eb E4 packages changed the E4 harness that v2's E4 experiment "
    "contract hashes, and because v3's manifest was committed alone and so "
    "verifies under verify_production_freeze at its artifact commit. "
    "`superseding_freeze` names v3 and records the v1 -> v2 -> v3 chain."
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUTPUT.exists():
        raise SystemExit("inventory already exists")
    inventory = json.loads(SOURCE.read_text(encoding="utf-8"))
    files = dict(inventory["files"])

    for relative in ADDED:
        if relative in files:
            raise SystemExit("added entry already exists in v7: " + relative)
        path = ROOT / relative
        if not path.is_file():
            raise SystemExit("expected new input is missing: " + relative)
        files[relative] = sha256(path)

    inventory.update(
        schema_version="protocol-v5-final-audit-inputs-v1.6.0",
        created_at_utc="2026-09-21T00:00:00Z",
        superseding_freeze={
            "freeze_id": "v5-final-execution-freeze-v3",
            "manifest_path": FREEZE_V3 + "/freeze-manifest.json",
            "supersession_note": FREEZE_V3 + "/SUPERSESSION.md",
            "design_snapshot": SNAPSHOT_V3,
            "freeze_artifact_commit_sha": "d3a409da17bd747eae066547ee6db2bb592d6055",
            "verifiable_at_artifact_commit": True,
            "chain": [
                "results_v5/protocol-v5.0.0/freezes/v5-final-execution-freeze/freeze-manifest.json",
                FREEZE_V2 + "/freeze-manifest.json",
                FREEZE_V3 + "/freeze-manifest.json",
            ],
            "governs": "current and future P2 execution",
            "does_not_govern": (
                "any package in this inventory's `packages` list; every one of "
                "those was collected under the freeze named by "
                "`authoritative_freeze` and describes that retired identity"
            ),
            "promotion_blocked_because": (
                "`authoritative_freeze` must equal the candidate inventory's "
                "authoritative_freeze.manifest_path (see "
                "evaluation_v5/final_audit/disposition.py::build_dispositions). "
                "Promoting a newer identity here without rebuilding "
                "benchmarks_v5/protocol-v5-final-evidence-candidates-v1.json "
                "would raise AUTHORITATIVE_EXECUTION_SHA_MISMATCH on the E5 "
                "candidates, and rebuilding that inventory against it would "
                "make E5_STORAGE_RERUN INCOMPATIBLE_GLOBAL_EXECUTION_SHA -> "
                "REJECTED_INTEGRITY and flip claim H7 off SUPPORTED. That is a "
                "scientific re-disposition of E4/E5 evidence, not an inventory "
                "refresh, and is deliberately left undone here."
            ),
        },
        scope=SCOPE,
        files=dict(sorted(files.items())),
    )
    OUTPUT.write_text(
        json.dumps(inventory, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
