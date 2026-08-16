#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export MODE_VALUES="${MODE_VALUES:-$ROOT_DIR/helm/dynamic-values.yaml}"

printf 'Selecting policy-bounded Dynamic Mode overlay: %s\n' "$MODE_VALUES"
exec "$ROOT_DIR/scripts/install-proposed.sh"
