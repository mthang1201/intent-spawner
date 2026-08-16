#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-z2jh-context-demo}"
RELEASE="${RELEASE:-context-demo}"
Z2JH_CHART_VERSION="${Z2JH_CHART_VERSION:-4.0.0}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi
BACKEND_VALUES="${BACKEND_VALUES:-$ROOT_DIR/helm/recommender-rule-based-values.yaml}"
BACKEND_AUTH_VALUES="${BACKEND_AUTH_VALUES:-}"
MODE_VALUES="${MODE_VALUES:-}"

if [[ ! -f "$BACKEND_VALUES" ]]; then
  printf 'Backend values file not found: %s\n' "$BACKEND_VALUES" >&2
  exit 1
fi
if [[ -n "$BACKEND_AUTH_VALUES" && ! -f "$BACKEND_AUTH_VALUES" ]]; then
  printf 'Backend auth values file not found: %s\n' "$BACKEND_AUTH_VALUES" >&2
  exit 1
fi
if [[ -n "$MODE_VALUES" && ! -f "$MODE_VALUES" ]]; then
  printf 'Mode values file not found: %s\n' "$MODE_VALUES" >&2
  exit 1
fi

AUDIT_TMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "$AUDIT_TMP_DIR"' EXIT
CONFIGMAP_MANIFEST="$AUDIT_TMP_DIR/recommender-configmap.json"
ROLLOUT_VALUES="$AUDIT_TMP_DIR/recommender-rollout-values.json"

run() {
  printf '\n+'
  printf ' %q' "$@"
  printf '\n'
  "$@"
}

echo "Installing proposed context-aware Z2JH demo into namespace: ${NAMESPACE}"

printf '\n+ kubectl create namespace %q --dry-run=client -o yaml | kubectl apply -f -\n' "$NAMESPACE"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

printf '\n+ kubectl create configmap demo-workload --from-file=%q --namespace %q --dry-run=client -o yaml | kubectl apply -f -\n' "$ROOT_DIR/workload" "$NAMESPACE"
kubectl create configmap demo-workload --from-file="$ROOT_DIR/workload" --namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

run "$PYTHON_BIN" "$ROOT_DIR/scripts/recommender_package.py" verify
printf '\n+ python recommender_package.py manifest > %q\n' "$CONFIGMAP_MANIFEST"
"$PYTHON_BIN" "$ROOT_DIR/scripts/recommender_package.py" manifest \
  --namespace "$NAMESPACE" >"$CONFIGMAP_MANIFEST"
printf '\n+ python recommender_package.py rollout-values > %q\n' "$ROLLOUT_VALUES"
"$PYTHON_BIN" "$ROOT_DIR/scripts/recommender_package.py" rollout-values \
  >"$ROLLOUT_VALUES"

run "$PYTHON_BIN" "$ROOT_DIR/scripts/validate_secret_refs.py" \
  --namespace "$NAMESPACE" --values "$BACKEND_VALUES"
if [[ -n "$BACKEND_AUTH_VALUES" ]]; then
  run "$PYTHON_BIN" "$ROOT_DIR/scripts/validate_secret_refs.py" \
    --namespace "$NAMESPACE" --values "$BACKEND_AUTH_VALUES"
fi

printf '\n+ kubectl apply -f %q\n' "$CONFIGMAP_MANIFEST"
kubectl apply -f "$CONFIGMAP_MANIFEST"

run helm repo add jupyterhub https://hub.jupyter.org/helm-chart/ --force-update
run helm repo update

version_args=()
if [[ "$Z2JH_CHART_VERSION" != "latest" ]]; then
  version_args=(--version "$Z2JH_CHART_VERSION")
fi

values_args=(
  --values "$ROOT_DIR/helm/proposed-values.yaml"
  --values "$BACKEND_VALUES"
  --values "$ROOT_DIR/helm/reprovision-values.yaml"
)
if [[ -n "$BACKEND_AUTH_VALUES" ]]; then
  values_args+=(--values "$BACKEND_AUTH_VALUES")
fi
if [[ -n "$MODE_VALUES" ]]; then
  values_args+=(--values "$MODE_VALUES")
fi
values_args+=(--values "$ROLLOUT_VALUES")

run helm upgrade --install "$RELEASE" jupyterhub/jupyterhub \
  "${version_args[@]}" \
  --namespace "$NAMESPACE" \
  "${values_args[@]}" \
  --wait \
  --timeout 10m

cat <<EOF

Proposed demo installed with backend values: ${BACKEND_VALUES}
Next:
  bash scripts/port-forward.sh
  Open http://127.0.0.1:8000
  This local-only demo uses DummyAuthenticator; enter any username and any non-empty password.
  Enter: I will train a scikit-learn model on a 1.5GB CSV dataset
  Then click Preview recommendation and Confirm recommendation; preview alone creates no pod.
  After the server starts, open http://127.0.0.1:8000/hub/reprovision to preview and confirm a replacement pod.
  Save files first: the PVC is retained, but kernels, terminals, and in-memory state are not.
EOF
