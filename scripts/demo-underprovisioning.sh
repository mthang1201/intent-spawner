#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-z2jh-context-demo}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POD_NAME="${POD_NAME:-underprovision-small}"

echo "Demo A: underprovisioning. This creates a Small-profile pod that should be OOMKilled."

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
    demo: underprovisioning
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
      command: ["python", "/demo/workload/oom_late_failure.py"]
      env:
        - name: OOM_TARGET_MIB
          value: "640"
        - name: OOM_BLOCK_MIB
          value: "32"
        - name: OOM_SLEEP_SECONDS
          value: "1"
      volumeMounts:
        - name: demo-workload
          mountPath: /demo/workload
          readOnly: true
      resources:
        requests:
          cpu: "100m"
          memory: "256Mi"
        limits:
          cpu: "500m"
          memory: "384Mi"
YAML

cat <<EOF

Observe:
  kubectl get pods -n ${NAMESPACE} -w
  kubectl describe pod ${POD_NAME} -n ${NAMESPACE} | grep -A8 -E 'Last State|Reason|OOMKilled'
  kubectl logs ${POD_NAME} -n ${NAMESPACE} --previous 2>/dev/null || kubectl logs ${POD_NAME} -n ${NAMESPACE}
EOF

