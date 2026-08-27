#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--execute" ]]; then
  printf '%s\n' \
    'Refusing an implicit cluster mutation.' \
    'Usage: bash scripts/install-user-study.sh --execute [--allow-development-smoke] /path/to/prepared-study' \
    'The directory must contain assignment-manifest.json and browser-task-set.json.' >&2
  exit 2
fi
shift

PACKAGE_DEVELOPMENT_ARGS=()
STUDY_EXECUTION_CLASS="confirmatory-preparation"
if [[ "${1:-}" == "--allow-development-smoke" ]]; then
  PACKAGE_DEVELOPMENT_ARGS=(--allow-development)
  STUDY_EXECUTION_CLASS="researcher-development-smoke-only"
  shift
fi

if [[ $# -ne 1 ]]; then
  printf 'Expected exactly one prepared-study directory.\n' >&2
  exit 2
fi

NAMESPACE="${NAMESPACE:-z2jh-context-demo}"
RELEASE="${RELEASE:-context-demo}"
Z2JH_CHART_VERSION="${Z2JH_CHART_VERSION:-4.0.0}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STUDY_PREP_DIR="$(cd "$1" && pwd)"
ASSIGNMENT_PATH="$STUDY_PREP_DIR/assignment-manifest.json"
BROWSER_TASKS_PATH="$STUDY_PREP_DIR/browser-task-set.json"

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

for required in "$ASSIGNMENT_PATH" "$BROWSER_TASKS_PATH"; do
  if [[ ! -f "$required" ]]; then
    printf 'Required prepared-study artifact not found: %s\n' "$required" >&2
    exit 1
  fi
done

INSTALL_TMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "$INSTALL_TMP_DIR"' EXIT
RECOMMENDER_MANIFEST="$INSTALL_TMP_DIR/recommender-configmap.json"
RECOMMENDER_ROLLOUT="$INSTALL_TMP_DIR/recommender-rollout-values.json"
STUDY_ADAPTER_MANIFEST="$INSTALL_TMP_DIR/user-study-adapter.json"
STUDY_CONFIG_MANIFEST="$INSTALL_TMP_DIR/user-study-config.json"
STUDY_PVC_MANIFEST="$INSTALL_TMP_DIR/user-study-pvc.json"
STUDY_ROLLOUT="$INSTALL_TMP_DIR/user-study-rollout-values.json"

run() {
  printf '\n+'
  printf ' %q' "$@"
  printf '\n'
  "$@"
}

printf 'Validating frozen Protocol-v5 study package for namespace: %s\n' "$NAMESPACE"
run "$PYTHON_BIN" "$ROOT_DIR/scripts/recommender_package.py" verify
run "$PYTHON_BIN" "$ROOT_DIR/scripts/user_study_package.py" verify \
  --assignment "$ASSIGNMENT_PATH" \
  --browser-tasks "$BROWSER_TASKS_PATH" \
  "${PACKAGE_DEVELOPMENT_ARGS[@]}" \
  --namespace "$NAMESPACE"

"$PYTHON_BIN" "$ROOT_DIR/scripts/recommender_package.py" manifest \
  --namespace "$NAMESPACE" >"$RECOMMENDER_MANIFEST"
"$PYTHON_BIN" "$ROOT_DIR/scripts/recommender_package.py" rollout-values \
  >"$RECOMMENDER_ROLLOUT"
"$PYTHON_BIN" "$ROOT_DIR/scripts/user_study_package.py" adapter-manifest \
  --namespace "$NAMESPACE" >"$STUDY_ADAPTER_MANIFEST"
"$PYTHON_BIN" "$ROOT_DIR/scripts/user_study_package.py" study-config-manifest \
  --namespace "$NAMESPACE" \
  --assignment "$ASSIGNMENT_PATH" \
  --browser-tasks "$BROWSER_TASKS_PATH" \
  "${PACKAGE_DEVELOPMENT_ARGS[@]}" >"$STUDY_CONFIG_MANIFEST"
"$PYTHON_BIN" "$ROOT_DIR/scripts/user_study_package.py" pvc-manifest \
  --namespace "$NAMESPACE" >"$STUDY_PVC_MANIFEST"
"$PYTHON_BIN" "$ROOT_DIR/scripts/user_study_package.py" rollout-values \
  --assignment "$ASSIGNMENT_PATH" \
  --browser-tasks "$BROWSER_TASKS_PATH" \
  "${PACKAGE_DEVELOPMENT_ARGS[@]}" >"$STUDY_ROLLOUT"

run kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
# These generated ConfigMaps are intentionally large enough that client-side
# apply can exceed Kubernetes' 256 KiB annotation limit by copying the entire
# object into kubectl.kubernetes.io/last-applied-configuration. Server-side
# apply stays idempotent without duplicating the package payload in metadata.
run kubectl apply --server-side --field-manager=intent-spawner-user-study \
  -f "$RECOMMENDER_MANIFEST"
run kubectl apply --server-side --field-manager=intent-spawner-user-study \
  -f "$STUDY_ADAPTER_MANIFEST"
run kubectl apply --server-side --field-manager=intent-spawner-user-study \
  -f "$STUDY_CONFIG_MANIFEST"
run kubectl apply --server-side --field-manager=intent-spawner-user-study \
  -f "$STUDY_PVC_MANIFEST"

run helm repo add jupyterhub https://hub.jupyter.org/helm-chart/ --force-update
run helm repo update

version_args=()
if [[ "$Z2JH_CHART_VERSION" != "latest" ]]; then
  version_args=(--version "$Z2JH_CHART_VERSION")
fi

run helm upgrade --install "$RELEASE" jupyterhub/jupyterhub \
  "${version_args[@]}" \
  --namespace "$NAMESPACE" \
  --values "$ROOT_DIR/helm/proposed-values.yaml" \
  --values "$ROOT_DIR/helm/recommender-p2-values.yaml" \
  --values "$ROOT_DIR/helm/user-study-values.yaml" \
  --values "$RECOMMENDER_ROLLOUT" \
  --values "$STUDY_ROLLOUT" \
  --wait \
  --timeout 10m

printf '%s\n' \
  '' \
  'Protocol-v5 study-only Hub installed.' \
  "Execution class: $STUDY_EXECUTION_CLASS" \
  'Issue only pseudonyms from the frozen assignment manifest as login IDs.' \
  'Do not enter participant names or emails into Hub usernames or research logs.' \
  'The consent-version gate is bookkeeping; institutional and ethics requirements remain the researcher responsibility.' \
  'The evidence PVC is intentionally retained and must be finalized through the user-study CLI.'
