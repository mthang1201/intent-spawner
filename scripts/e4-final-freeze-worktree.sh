#!/usr/bin/env bash
# Create (or reuse) a clean git worktree pinned exactly at the descendant
# commit that introduced results_v5/protocol-v5.0.0/freezes/v5-final-execution-freeze,
# so evaluation_v5.freeze.verify_production_freeze() sees an identical
# frozen_execution_tree with zero post-freeze diff, per
# docs/evaluation/PROTOCOL_V5_FINAL_EXECUTION_HANDOFF.md ("Run only from a
# clean checkout at the handoff descendant, with the exact freeze above.").
#
# This script makes no changes to the frozen tree, any experiment contract,
# or P1/P2/P3 code. It only checks out a second working copy of the same
# repository at a fixed historical commit.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FREEZE_ARTIFACT_COMMIT="38877fa71d04f4caad30f253a7cbbb049ecda4f8"
FREEZE_ID="v5-final-execution-freeze"
WORKTREE_DIR="${1:-$ROOT_DIR/../intent-spawner-${FREEZE_ID}}"

cd "$ROOT_DIR"

if ! git cat-file -e "${FREEZE_ARTIFACT_COMMIT}" 2>/dev/null; then
  echo "error: ${FREEZE_ARTIFACT_COMMIT} is not present in this repository's object store" >&2
  exit 1
fi

if [[ -d "$WORKTREE_DIR" ]]; then
  echo "worktree already exists at $WORKTREE_DIR"
else
  git worktree add --detach "$WORKTREE_DIR" "$FREEZE_ARTIFACT_COMMIT"
fi

echo
echo "Pinned worktree ready at: $WORKTREE_DIR"
echo "HEAD: $(git -C "$WORKTREE_DIR" rev-parse HEAD)"
echo
echo "Next, from inside that worktree (reusing this repo's .venv):"
echo "  cd \"$WORKTREE_DIR\""
echo "  PYTHONPATH=. $ROOT_DIR/.venv/bin/python -m evaluation_v5.freeze verify \\"
echo "    --freeze results_v5/protocol-v5.0.0/freezes/${FREEZE_ID}/freeze-manifest.json"
echo "  PYTHONPATH=. $ROOT_DIR/.venv/bin/python -m evaluation_v5.resource.efficiency_runner preflight \\"
echo "    --freeze results_v5/protocol-v5.0.0/freezes/${FREEZE_ID}/freeze-manifest.json \\"
echo "    --readiness-attestation <external-e4-readiness.json>"
echo
echo "Only a READY preflight result permits 'execute'. See"
echo "docs/evaluation/PROTOCOL_V5_FINAL_EXECUTION_HANDOFF.md for the full allowed-command list."
