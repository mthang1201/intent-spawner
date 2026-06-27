#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-z2jh-context-demo}"
POD_PREFIX="${POD_PREFIX:-idle-large}"
POD_COUNT="${POD_COUNT:-3}"

echo "Demo B: overprovisioning. Idle pods request high CPU/RAM and reduce schedulable concurrency."

printf '\n+ kubectl create namespace %q --dry-run=client -o yaml | kubectl apply -f -\n' "$NAMESPACE"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

NODE_NAME="$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')"
NODE_JSON="$(kubectl get node "$NODE_NAME" -o json)"

read -r ALLOCATABLE_CPU_M ALLOCATABLE_MEM_MI < <(NODE_JSON="$NODE_JSON" python3 - <<'PY'
import json
import os
import re

node = json.loads(os.environ["NODE_JSON"])
alloc = node["status"]["allocatable"]

def parse_cpu(value):
    if value.endswith("m"):
        return int(value[:-1])
    return int(float(value) * 1000)

def parse_mem(value):
    match = re.fullmatch(r"([0-9.]+)([KMGTE]i?)?", value)
    if not match:
        raise ValueError(value)
    amount = float(match.group(1))
    suffix = match.group(2) or ""
    factors = {
        "Ki": 1 / 1024,
        "Mi": 1,
        "Gi": 1024,
        "Ti": 1024 * 1024,
        "K": 1000 / 1024 / 1024,
        "M": 1000 / 1024,
        "G": 1000,
        "T": 1000 * 1000,
        "": 1 / 1024 / 1024,
    }
    return int(amount * factors[suffix])

print(parse_cpu(alloc["cpu"]), parse_mem(alloc["memory"]))
PY
)

CPU_REQUEST_M=$((ALLOCATABLE_CPU_M * 55 / 100))
MEM_REQUEST_MI=$((ALLOCATABLE_MEM_MI * 55 / 100))

if (( CPU_REQUEST_M < 100 )); then
  CPU_REQUEST_M=100
fi
if (( MEM_REQUEST_MI < 256 )); then
  MEM_REQUEST_MI=256
fi

echo "Node: ${NODE_NAME}"
echo "Allocatable: ${ALLOCATABLE_CPU_M}m CPU, ${ALLOCATABLE_MEM_MI}Mi memory"
echo "Each idle Large pod requests: ${CPU_REQUEST_M}m CPU, ${MEM_REQUEST_MI}Mi memory"

for i in $(seq 1 "$POD_COUNT"); do
  pod_name="${POD_PREFIX}-${i}"
  printf '\n+ kubectl delete pod %q -n %q --ignore-not-found --wait=false\n' "$pod_name" "$NAMESPACE"
  kubectl delete pod "$pod_name" -n "$NAMESPACE" --ignore-not-found --wait=false

  printf '\n+ kubectl apply -n %q -f -\n' "$NAMESPACE"
  kubectl apply -n "$NAMESPACE" -f - <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
  labels:
    app.kubernetes.io/name: z2jh-context-demo
    demo: overprovisioning
spec:
  restartPolicy: Never
  containers:
    - name: idle
      image: busybox:1.36
      command: ["sh", "-c", "echo idle but reserving requests; sleep infinity"]
      resources:
        requests:
          cpu: "${CPU_REQUEST_M}m"
          memory: "${MEM_REQUEST_MI}Mi"
        limits:
          cpu: "${CPU_REQUEST_M}m"
          memory: "${MEM_REQUEST_MI}Mi"
YAML
done

sleep 10
printf '\n+ kubectl get pods -n %q -l demo=overprovisioning -o wide\n' "$NAMESPACE"
kubectl get pods -n "$NAMESPACE" -l demo=overprovisioning -o wide

PENDING_COUNT="$(kubectl get pods -n "$NAMESPACE" -l demo=overprovisioning --no-headers 2>/dev/null | awk '$3=="Pending"{count++} END{print count+0}')"

if [[ "$PENDING_COUNT" == "0" ]]; then
  echo "No Pending pod observed. Applying a ResourceQuota fallback to demonstrate request-based blocking."
  printf '\n+ kubectl apply -n %q -f -\n' "$NAMESPACE"
  kubectl apply -n "$NAMESPACE" -f - <<YAML
apiVersion: v1
kind: ResourceQuota
metadata:
  name: overprovisioning-request-quota
spec:
  hard:
    requests.cpu: "100m"
    requests.memory: "128Mi"
    pods: "20"
YAML

  if kubectl apply -n "$NAMESPACE" -f - <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: ${POD_PREFIX}-quota-blocked
  labels:
    app.kubernetes.io/name: z2jh-context-demo
    demo: overprovisioning
spec:
  restartPolicy: Never
  containers:
    - name: idle
      image: busybox:1.36
      command: ["sh", "-c", "sleep infinity"]
      resources:
        requests:
          cpu: "${CPU_REQUEST_M}m"
          memory: "${MEM_REQUEST_MI}Mi"
        limits:
          cpu: "${CPU_REQUEST_M}m"
          memory: "${MEM_REQUEST_MI}Mi"
YAML
  then
    echo "Quota fallback pod was accepted; inspect its scheduling status with kubectl describe."
  else
    echo "Quota fallback rejected the extra pod as expected."
  fi
fi

cat <<EOF

Observe:
  kubectl get pods -n ${NAMESPACE}
  kubectl describe pod ${POD_PREFIX}-1 -n ${NAMESPACE} | grep -A8 Requests
  kubectl describe pod ${POD_PREFIX}-2 -n ${NAMESPACE} | grep -A12 -E 'Events|Insufficient|quota|Requests'
  kubectl top pods -n ${NAMESPACE}  # optional, only if metrics-server exists
EOF
