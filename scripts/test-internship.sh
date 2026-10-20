#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Resolve Python binary
if [[ -n "${PYTHON:-}" ]]; then
    PYTHON_BIN="$PYTHON"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif [[ -x "$(git rev-parse --git-common-dir 2>/dev/null)/../.venv/bin/python" ]]; then
    PYTHON_BIN="$(git rev-parse --git-common-dir 2>/dev/null)/../.venv/bin/python"
elif [[ -x "$ROOT_DIR/../intent-spawner/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/../intent-spawner/.venv/bin/python"
else
    PYTHON_BIN="python3"
fi

echo "=========================================================="
echo " Running Internship Core Unit Tests (P2 Recommender Suite)"
echo " Python: $PYTHON_BIN"
echo "=========================================================="

env PYTHONPATH=. "$PYTHON_BIN" -m pytest -v \
    tests/test_p2_backend_integration.py \
    tests/test_structured_intent_extractor.py \
    tests/test_single_input_workflow.py \
    tests/test_constraint_evaluator.py \
    tests/test_dense_retrieval.py \
    tests/test_hybrid_retrieval.py

echo "=========================================================="
echo " All Internship Core Unit Tests Passed Successfully!"
echo "=========================================================="
