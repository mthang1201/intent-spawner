#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "=========================================================="
echo " Starting Full Internship Verification Suite"
echo "=========================================================="

echo ""
echo ">>> [Phase 1/2] Core Unit Tests (P2 Recommender Suite)"
bash scripts/test-internship.sh

echo ""
echo ">>> [Phase 2/2] Offline Recommendation Evaluation (E1 Local)"
bash scripts/eval-internship.sh

echo ""
echo "=========================================================="
echo " ALL INTERNSHIP VERIFICATION CHECKS PASSED!"
echo "=========================================================="
