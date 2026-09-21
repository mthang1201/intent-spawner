#!/usr/bin/env python3
"""Build the v1.5 final-audit inventory: re-review drifted inputs, register the v2 freeze.

Unlike v6 (purely additive over v5), this inventory **re-records thirteen
existing entries** whose bytes drifted after v5/v6 were built. Re-recording a
reviewed input is a deliberate act, so each one is enumerated below with the
commit that changed it and the reason the new bytes are accepted. Nothing is
inferred and no entry is removed.

Group 1 - the ranking fix (commit 842109c, "Core algorithm fix"):

    recommender/constraint_evaluator.py
    recommender/p2_backend.py
    recommender/__init__.py

  These three are `protected_files`. The commit corrected a deterministic
  constraint/ranking defect in P2 and bumped DETERMINISTIC_RANKER_VERSION to
  p2-deterministic-ranker-v2.0.0, CONSTRAINT_EVALUATOR_VERSION to v1.1.0 and
  P2Config.config_version to p2-config-v1.1.0. The new bytes are frozen under
  `v5-final-execution-freeze-v2`, which this inventory records under
  `superseding_freeze`. P1 (recommender/rule_based.py) is untouched and its
  entry is unchanged.

Group 2 - E4 input drift (commits 3f896eb and 435f543):

    benchmarks_v5/README.md
    benchmarks_v5/protocol-v5-resource-efficiency-inputs-v1.schema.json
    benchmarks_v5/protocol-v5-resource-semantic-independence-v1.schema.json
    benchmarks_v5/protocol-v5-resource-workloads-v1.schema.json
    benchmarks_v5/resource-allocation-crosswalk-v1.yaml
    benchmarks_v5/resource-efficiency-capacity-v1.yaml
    benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml
    benchmarks_v5/resource-efficiency-inputs-v1.yaml
    benchmarks_v5/resource-envelope-semantic-independence-v1.yaml
    benchmarks_v5/resource-envelope-workloads-v1.yaml

  "Add failure-inducing workloads to E4" and "Add P1 as an explicit E4
  comparator" changed these ten reviewed E4 inputs without re-recording them,
  which left every `Inputs.verify()` call failing and blocked the whole audit.
  This drift is unrelated to the ranking fix. The existing E4 evidence
  packages were collected against the OLDER bytes; re-recording here does not
  retroactively bind them to the new bytes, and the supersession note in
  results_v5/protocol-v5.0.0/freezes/v5-final-execution-freeze-v2/ records
  that those packages describe a retired input identity.

Additions (six files, none of which replace anything):

  * the `v5-final-execution-freeze-v2` manifest and its supersession note;
  * `frozen-configuration-v2.json`, the design snapshot extracted from that
    freeze and used as the E1 runner's --frozen-configuration;
  * the three raw/report files of the fresh E1 development run
    20260921T-observed-p1-p2-development-v2.

`packages` is deliberately left byte-identical to v6. The new E1 run is
registered as reviewed *files* so that it is checksum-bound and does not read
as UNREGISTERED_EVIDENCE, but it is NOT promoted into the validated package
set: promoting development-split evidence into the final-audit package set is
a scientific decision outside the scope of this repair.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks_v5/protocol-v5-final-audit-inputs-v6.json"
OUTPUT = ROOT / "benchmarks_v5/protocol-v5-final-audit-inputs-v7.json"

FREEZE_V2 = "results_v5/protocol-v5.0.0/freezes/v5-final-execution-freeze-v2"
E1_V2 = "results_v5/protocol-v5.0.0/E1/20260921T-observed-p1-p2-development-v2"

# Existing entries whose bytes are re-recorded from the current tree.
REREVIEWED = (
    "recommender/__init__.py",
    "recommender/constraint_evaluator.py",
    "recommender/p2_backend.py",
    "benchmarks_v5/README.md",
    "benchmarks_v5/protocol-v5-resource-efficiency-inputs-v1.schema.json",
    "benchmarks_v5/protocol-v5-resource-semantic-independence-v1.schema.json",
    "benchmarks_v5/protocol-v5-resource-workloads-v1.schema.json",
    "benchmarks_v5/resource-allocation-crosswalk-v1.yaml",
    "benchmarks_v5/resource-efficiency-capacity-v1.yaml",
    "benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml",
    "benchmarks_v5/resource-efficiency-inputs-v1.yaml",
    "benchmarks_v5/resource-envelope-semantic-independence-v1.yaml",
    "benchmarks_v5/resource-envelope-workloads-v1.yaml",
)

# New entries. None of these exist in v6.
ADDED = (
    FREEZE_V2 + "/freeze-manifest.json",
    FREEZE_V2 + "/SUPERSESSION.md",
    "results_v5/protocol-v5.0.0/freezes/frozen-configuration-v2.json",
    E1_V2 + "/raw/offline-run-provenance.json",
    E1_V2 + "/raw/recommendations.jsonl",
    E1_V2 + "/report/offline-run-completion.json",
)

SCOPE = (
    "Checksum-bound Protocol-v5 final-audit inventory. Evidence selection, "
    "claim inputs, the package list and `authoritative_freeze` are inherited "
    "byte-for-byte from v6. `authoritative_freeze` deliberately still names "
    "v5-final-execution-freeze, because every package in this inventory was "
    "collected under that freeze and must keep being dispositioned against "
    "it; the new v5-final-execution-freeze-v2 identity is recorded separately "
    "under `superseding_freeze`, which explains why promotion is a separate "
    "scientific decision. Thirteen existing entries are re-recorded from the "
    "current tree: three recommender files changed by commit 842109c (the P2 "
    "ranking fix, now frozen as v5-final-execution-freeze-v2), and ten "
    "benchmarks_v5 E4 inputs changed by 3f896eb and 435f543 without being "
    "re-recorded at the time. Six files are added: the v2 freeze manifest and "
    "its supersession note, the v2 design snapshot, and the raw/report files "
    "of the fresh E1 development run 20260921T-observed-p1-p2-development-v2. "
    "That E1 run is registered as a reviewed file set only; it is not promoted "
    "into the validated package set and it is development-split, "
    "non-confirmatory evidence."
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUTPUT.exists():
        raise SystemExit("inventory already exists")
    inventory = json.loads(SOURCE.read_text(encoding="utf-8"))
    files = dict(inventory["files"])

    for relative in REREVIEWED:
        if relative not in files:
            raise SystemExit("expected re-reviewed entry is absent from v6: " + relative)
        path = ROOT / relative
        if not path.is_file():
            raise SystemExit("re-reviewed input is missing: " + relative)
        files[relative] = sha256(path)

    for relative in ADDED:
        if relative in files:
            raise SystemExit("added entry already exists in v6: " + relative)
        path = ROOT / relative
        if not path.is_file():
            raise SystemExit("expected new input is missing: " + relative)
        files[relative] = sha256(path)

    inventory.update(
        schema_version="protocol-v5-final-audit-inputs-v1.5.0",
        created_at_utc="2026-09-21T00:00:00Z",
        superseding_freeze={
            "freeze_id": "v5-final-execution-freeze-v2",
            "manifest_path": FREEZE_V2 + "/freeze-manifest.json",
            "supersession_note": FREEZE_V2 + "/SUPERSESSION.md",
            "design_snapshot": "results_v5/protocol-v5.0.0/freezes/frozen-configuration-v2.json",
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
                "Promoting v2 here without rebuilding "
                "benchmarks_v5/protocol-v5-final-evidence-candidates-v1.json "
                "would raise AUTHORITATIVE_EXECUTION_SHA_MISMATCH on the E5 "
                "candidates, and rebuilding that inventory against v2 would "
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
