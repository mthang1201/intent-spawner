#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-z2jh-context-demo}"

printf '+ kubectl get pods -n %q -w\n' "$NAMESPACE"
kubectl get pods -n "$NAMESPACE" -w

