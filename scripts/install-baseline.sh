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

echo "Installing baseline static-profile Z2JH demo into namespace: ${NAMESPACE}"

printf '\n+ kubectl create namespace %q --dry-run=client -o yaml | kubectl apply -f -\n' "$NAMESPACE"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

printf '\n+ kubectl create configmap demo-workload --from-file=%q --namespace %q --dry-run=client -o yaml | kubectl apply -f -\n' "$ROOT_DIR/workload" "$NAMESPACE"
kubectl create configmap demo-workload --from-file="$ROOT_DIR/workload" --namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

printf '\n+ kubectl create configmap demo-notebooks --from-file=%q --namespace %q --dry-run=client -o yaml | kubectl apply -f -\n' "$ROOT_DIR/notebooks" "$NAMESPACE"
kubectl create configmap demo-notebooks --from-file="$ROOT_DIR/notebooks" --namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

run helm repo add jupyterhub https://hub.jupyter.org/helm-chart/ --force-update
run helm repo update

version_args=()
if [[ "$Z2JH_CHART_VERSION" != "latest" ]]; then
  version_args=(--version "$Z2JH_CHART_VERSION")
fi

run helm upgrade --install "$RELEASE" jupyterhub/jupyterhub \
  "${version_args[@]}" \
  --namespace "$NAMESPACE" \
  --values "$ROOT_DIR/helm/baseline-values.yaml" \
  --wait \
  --timeout 10m

cat <<EOF

Baseline installed.
Next:
  bash scripts/port-forward.sh
  Open http://127.0.0.1:8000
  Login with any username and password: demo
EOF
