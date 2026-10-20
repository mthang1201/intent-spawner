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

RESULT_DIR="${RESULT_DIR:-results_v5/protocol-v5.0.0/E1/run-e1-local}"

echo "=========================================================="
echo " Running Internship Offline Evaluation (Stream E1 Local Mode)"
echo " Python:     $PYTHON_BIN"
echo " Result Dir: $RESULT_DIR"
echo "=========================================================="

echo "==> [Step 1/2] Executing E1 recommendation evaluation (P1 vs P2 Local)..."
env PYTHONPATH=. "$PYTHON_BIN" -m evaluation_v5.offline.runner \
    --systems P1,P2 \
    --extractor-mode local \
    --repeats 1 \
    --result-dir "$RESULT_DIR"

echo ""
echo "==> [Step 2/2] Validating evidence artifacts and schema integrity..."
env PYTHONPATH=. "$PYTHON_BIN" -m evaluation_v5.offline.validate_evidence \
    --dir "$RESULT_DIR"

echo "=========================================================="
echo " Internship Offline Evaluation & Schema Validation Completed!"
echo " Evidence & metrics: $RESULT_DIR"
echo "=========================================================="
