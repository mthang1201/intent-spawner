#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-z2jh-context-demo}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POD_NAME="${POD_NAME:-defensive-large-light}"

echo "Demo C: defensive over-requesting. A light workload reserves Large resources."

printf '\n+ kubectl create namespace %q --dry-run=client -o yaml | kubectl apply -f -\n' "$NAMESPACE"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

printf '\n+ kubectl create configmap demo-workload --from-file=%q --namespace %q --dry-run=client -o yaml | kubectl apply -f -\n' "$ROOT_DIR/workload" "$NAMESPACE"
kubectl create configmap demo-workload --from-file="$ROOT_DIR/workload" --namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

printf '\n+ kubectl delete pod %q -n %q --ignore-not-found --wait=false\n' "$POD_NAME" "$NAMESPACE"
kubectl delete pod "$POD_NAME" -n "$NAMESPACE" --ignore-not-found --wait=false

printf '\n+ kubectl apply -n %q -f -\n' "$NAMESPACE"
kubectl apply -n "$NAMESPACE" -f - <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: ${POD_NAME}
  labels:
    app.kubernetes.io/name: z2jh-context-demo
    demo: defensive-overrequesting
spec:
  restartPolicy: Never
  volumes:
    - name: demo-workload
      configMap:
        name: demo-workload
        defaultMode: 493
  containers:
    - name: workload
      image: quay.io/jupyter/scipy-notebook:latest
      command: ["sh", "-c", "python /demo/workload/light_eda.py; echo sleeping for observation; sleep 600"]
      volumeMounts:
        - name: demo-workload
          mountPath: /demo/workload
          readOnly: true
      resources:
        requests:
          cpu: "1500m"
          memory: "1536Mi"
        limits:
          cpu: "2"
          memory: "2Gi"
YAML

cat <<EOF

Observe:
  kubectl get pods -n ${NAMESPACE}
  kubectl describe pod ${POD_NAME} -n ${NAMESPACE} | grep -A8 Requests
  kubectl top pod ${POD_NAME} -n ${NAMESPACE}  # optional, only if metrics-server exists
  kubectl logs ${POD_NAME} -n ${NAMESPACE}
EOF

