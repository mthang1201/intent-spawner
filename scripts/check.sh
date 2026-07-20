#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${PYTHON:-}" && -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON:-python3}"
fi
NAMESPACE="${NAMESPACE:-z2jh-context-demo}"
RELEASE="${RELEASE:-context-demo}"
Z2JH_CHART_VERSION="${Z2JH_CHART_VERSION:-4.0.0}"

passed=0
failed=0
skipped=0

run_check() {
  local name="$1"
  shift

  printf '\n==> %s\n' "$name"
  printf '+'
  printf ' %q' "$@"
  printf '\n'

  if "$@"; then
    printf 'PASS: %s\n' "$name"
    passed=$((passed + 1))
  else
    printf 'FAIL: %s\n' "$name"
    failed=$((failed + 1))
  fi
}

skip_check() {
  local name="$1"
  local reason="$2"

  printf '\n==> %s\n' "$name"
  printf 'SKIP: %s\n' "$reason"
  skipped=$((skipped + 1))
}

cd "$ROOT_DIR" || exit 1

run_check "unit and smoke tests" "$PYTHON_BIN" -m pytest recommender tests
run_check "preserved cluster artifact integrity" "$PYTHON_BIN" -m cluster_evaluation.validate_artifacts
run_check "raw evidence SHA-256 integrity" "$PYTHON_BIN" -m cluster_evaluation.raw_integrity
run_check "capacity runner dry run" "$PYTHON_BIN" -m cluster_evaluation.capacity_runner \
  --experiment-id capacity-v2-dry-run \
  --image intent-spawner-cluster-eval:capacity-v2 \
  --dry-run
run_check "Python syntax validation" "$PYTHON_BIN" -m compileall -q benchmarks cluster_evaluation experiments recommender workload scripts/generate-capacity-values.py tests
run_check "shell syntax validation" bash -n scripts/check-cluster.sh scripts/check.sh scripts/demo-defensive-overrequesting.sh scripts/demo-overprovisioning.sh scripts/demo-underprovisioning.sh scripts/environment-report.sh scripts/install-baseline.sh scripts/install-proposed.sh scripts/port-forward.sh scripts/setup.sh scripts/uninstall.sh scripts/watch-pods.sh

if command -v helm >/dev/null 2>&1; then
  run_check "baseline Helm render" bash -c \
    'helm template "$1" jupyterhub --repo https://hub.jupyter.org/helm-chart/ --version "$2" --namespace "$3" --values "$4" >/tmp/intent-spawner-baseline-render.yaml' \
    _ "$RELEASE" "$Z2JH_CHART_VERSION" "$NAMESPACE" "$ROOT_DIR/helm/baseline-values.yaml"
  run_check "proposed Helm render" bash -c \
    'helm template "$1" jupyterhub --repo https://hub.jupyter.org/helm-chart/ --version "$2" --namespace "$3" --values "$4" >/tmp/intent-spawner-proposed-render.yaml' \
    _ "$RELEASE" "$Z2JH_CHART_VERSION" "$NAMESPACE" "$ROOT_DIR/helm/proposed-values.yaml"
else
  skip_check "Helm render validation" "helm is not installed or is not on PATH."
fi

if command -v kubectl >/dev/null 2>&1; then
  run_check "Kubernetes manifest client dry-run" kubectl apply --dry-run=client --validate=false \
    -f "$ROOT_DIR/k8s/idle-large-pod.yaml" \
    -f "$ROOT_DIR/k8s/idle-small-pod.yaml" \
    -f "$ROOT_DIR/k8s/resource-quota.yaml"
else
  skip_check "Kubernetes manifest client dry-run" "kubectl is not installed or is not on PATH."
fi

if [[ "${RUN_CLUSTER_CHECKS:-0}" == "1" ]]; then
  run_check "read-only cluster inspection" bash scripts/check-cluster.sh
else
  skip_check "read-only cluster inspection" "cluster access is optional; set RUN_CLUSTER_CHECKS=1 to run scripts/check-cluster.sh."
fi

skip_check "cluster-mutating demo execution" "install, uninstall, quota-changing, and pod-creating demo scripts are intentionally not executed by this smoke path."

printf '\nSummary: %s passed, %s failed, %s skipped\n' "$passed" "$failed" "$skipped"

if ((failed > 0)); then
  exit 1
fi
