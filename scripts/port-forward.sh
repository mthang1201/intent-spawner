#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-z2jh-context-demo}"
LOCAL_PORT="${LOCAL_PORT:-8000}"

printf 'Forwarding local port %s to JupyterHub proxy-public in namespace %s\n' "$LOCAL_PORT" "$NAMESPACE"
printf '+ kubectl port-forward -n %q svc/proxy-public %q:80\n' "$NAMESPACE" "$LOCAL_PORT"
kubectl port-forward -n "$NAMESPACE" svc/proxy-public "$LOCAL_PORT:80"

