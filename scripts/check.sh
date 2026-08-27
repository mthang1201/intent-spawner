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

run_check "unit and smoke tests" env PYTHONPATH=. "$PYTHON_BIN" -m pytest \
  recommender/test_recommender.py \
  tests/test_config_validation.py \
  tests/test_dynamic_profile_overlay.py \
  tests/test_reprovisioning.py \
  tests/test_evaluation_v4.py \
  tests/test_evaluation_v5.py \
  tests/test_evaluation_v5_isolation.py \
  tests/test_evaluation_v5_user_study.py
run_check "study-only package validation" "$PYTHON_BIN" scripts/user_study_package.py verify
run_check "Protocol-v5 E3 real-adapter synthetic smoke" env PYTHONPATH=. "$PYTHON_BIN" -m evaluation_v5.user_study.smoke
run_check "preserved cluster artifact integrity" "$PYTHON_BIN" -m cluster_evaluation.validate_artifacts
run_check "raw evidence SHA-256 integrity" "$PYTHON_BIN" -m cluster_evaluation.raw_integrity
run_check "capacity runner dry run" "$PYTHON_BIN" -m cluster_evaluation.capacity_runner \
  --experiment-id capacity-v2-dry-run \
  --image intent-spawner-cluster-eval:capacity-v2 \
  --dry-run
run_check "v3 workload manifest validation" "$PYTHON_BIN" -m benchmarks.resource_envelope_runner \
  --validate-only
run_check "v3 direct-pod dry run" "$PYTHON_BIN" -m cluster_evaluation.runner_v3 \
  --kind comparative \
  --experiment-id v3-comparative-dry-run \
  --image example.invalid/intent-spawner-v3@sha256:abc \
  --dry-run
run_check "v3 JupyterHub dry run" "$PYTHON_BIN" -m cluster_evaluation.jupyterhub_v3 \
  --experiment-id v3-jupyterhub-dry-run \
  --dry-run
run_check "v4 gold set and recommender matrix validation" "$PYTHON_BIN" -m evaluation_v4.run_recommenders \
  --dry-run
run_check "v4 paired system plan validation" "$PYTHON_BIN" -m evaluation_v4.plan_system \
  --dry-run
run_check "Protocol-v5 split isolation audit" "$PYTHON_BIN" -m evaluation_v5.isolation_audit
run_check "portable Protocol-v4 evidence" "$PYTHON_BIN" scripts/validate-portable-evidence.py
run_check "live acceptance record JSON" "$PYTHON_BIN" -m json.tool docs/evaluation/LIVE_ACCEPTANCE_2026-08-16.json
run_check "high-confidence secret scan" "$PYTHON_BIN" scripts/scan-secrets.py
run_check "Python syntax validation" "$PYTHON_BIN" -m compileall -q benchmarks cluster_evaluation evaluation_v4 evaluation_v5 experiments recommender workload scripts tests
run_check "shell syntax validation" bash -n scripts/*.sh

if command -v helm >/dev/null 2>&1; then
  run_check "baseline Helm render" bash -c \
    'helm template "$1" jupyterhub --repo https://hub.jupyter.org/helm-chart/ --version "$2" --namespace "$3" --values "$4" >/tmp/intent-spawner-baseline-render.yaml' \
    _ "$RELEASE" "$Z2JH_CHART_VERSION" "$NAMESPACE" "$ROOT_DIR/helm/baseline-values.yaml"
  run_check "proposed Helm render" bash -c \
    'helm template "$1" jupyterhub --repo https://hub.jupyter.org/helm-chart/ --version "$2" --namespace "$3" --values "$4" --values "$5" --values "$6" >/tmp/intent-spawner-proposed-render.yaml' \
    _ "$RELEASE" "$Z2JH_CHART_VERSION" "$NAMESPACE" "$ROOT_DIR/helm/proposed-values.yaml" "$ROOT_DIR/helm/recommender-rule-based-values.yaml" "$ROOT_DIR/helm/reprovision-values.yaml"
  run_check "external backend Helm render" bash -c \
    'helm template "$1" jupyterhub --repo https://hub.jupyter.org/helm-chart/ --version "$2" --namespace "$3" --values "$4" --values "$5" --values "$6" >/tmp/intent-spawner-external-render.yaml' \
    _ "$RELEASE" "$Z2JH_CHART_VERSION" "$NAMESPACE" "$ROOT_DIR/helm/proposed-values.yaml" "$ROOT_DIR/helm/recommender-external-llm-values.example.yaml" "$ROOT_DIR/helm/reprovision-values.yaml"
  run_check "self-hosted backend Helm render" bash -c \
    'helm template "$1" jupyterhub --repo https://hub.jupyter.org/helm-chart/ --version "$2" --namespace "$3" --values "$4" --values "$5" --values "$6" >/tmp/intent-spawner-self-hosted-render.yaml' \
    _ "$RELEASE" "$Z2JH_CHART_VERSION" "$NAMESPACE" "$ROOT_DIR/helm/proposed-values.yaml" "$ROOT_DIR/helm/recommender-self-hosted-llm-values.example.yaml" "$ROOT_DIR/helm/reprovision-values.yaml"
  run_check "Dynamic Mode Helm render" bash -c \
    'helm template "$1" jupyterhub --repo https://hub.jupyter.org/helm-chart/ --version "$2" --namespace "$3" --values "$4" --values "$5" --values "$6" --values "$7" >/tmp/intent-spawner-dynamic-render.yaml' \
    _ "$RELEASE" "$Z2JH_CHART_VERSION" "$NAMESPACE" "$ROOT_DIR/helm/proposed-values.yaml" "$ROOT_DIR/helm/recommender-rule-based-values.yaml" "$ROOT_DIR/helm/reprovision-values.yaml" "$ROOT_DIR/helm/dynamic-values.yaml"
  run_check "v3 experiment Helm render" bash -c \
    'helm template "$1" jupyterhub --repo https://hub.jupyter.org/helm-chart/ --version "$2" --namespace "$3" --values "$4" >/tmp/intent-spawner-v3-render.yaml' \
    _ "$RELEASE" "$Z2JH_CHART_VERSION" "$NAMESPACE" "$ROOT_DIR/helm/experiment-v3-values.yaml"
  run_check "Protocol-v5 E3 study Helm render" bash -c \
    'helm template "$1" jupyterhub --repo https://hub.jupyter.org/helm-chart/ --version "$2" --namespace "$3" --values "$4" --values "$5" --values "$6" >/tmp/intent-spawner-user-study-render.yaml' \
    _ "$RELEASE" "$Z2JH_CHART_VERSION" "$NAMESPACE" "$ROOT_DIR/helm/proposed-values.yaml" "$ROOT_DIR/helm/recommender-p2-values.yaml" "$ROOT_DIR/helm/user-study-values.yaml"
else
  skip_check "Helm render validation" "helm is not installed or is not on PATH."
fi

if command -v kubectl >/dev/null 2>&1; then
  run_check "Kubernetes manifest client dry-run" kubectl apply --dry-run=client --validate=false \
    -f "$ROOT_DIR/k8s/idle-large-pod.yaml" \
    -f "$ROOT_DIR/k8s/idle-small-pod.yaml" \
    -f "$ROOT_DIR/k8s/resource-quota.yaml" \
    -f "$ROOT_DIR/k8s/mock-llm.yaml"
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
