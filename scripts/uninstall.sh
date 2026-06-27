#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-z2jh-context-demo}"

printf 'This script deletes only namespace: %s\n' "$NAMESPACE"
printf '+ kubectl delete namespace %q --ignore-not-found\n' "$NAMESPACE"
kubectl delete namespace "$NAMESPACE" --ignore-not-found

echo "Cleanup requested. No cluster/context-wide resources were deleted."

