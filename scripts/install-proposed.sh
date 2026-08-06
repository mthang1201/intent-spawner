#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-z2jh-context-demo}"
RELEASE="${RELEASE:-context-demo}"
Z2JH_CHART_VERSION="${Z2JH_CHART_VERSION:-4.0.0}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
  --wait \
  --timeout 10m

cat <<EOF

Proposed method installed.
Next:
  bash scripts/port-forward.sh
  Open http://127.0.0.1:8000
  This local-only demo uses DummyAuthenticator; enter any username and any non-empty password.
  Enter: I will train a scikit-learn model on a 1.5GB CSV dataset
  Then click Preview recommendation and Confirm recommendation; preview alone creates no pod.
EOF
