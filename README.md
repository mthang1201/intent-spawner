# Z2JH Context-Aware Profile Recommendation Demo

This repository is a runnable thesis demo for:

**Intent- and Context-Aware Profile Recommendation Layer for Zero to JupyterHub / KubeSpawner**

The baseline asks: **Which hardware profile do you want?**

The proposed layer asks: **What are you trying to do?**

The demo shows why static profile selection is brittle: users can choose too little memory and hit a late OOM, or choose Large defensively and reduce cluster concurrency even when their workload is light. The proposed method uses an explainable rule-based analyzer to recommend Small, Medium, Large, or GPU-or-Large before KubeSpawner creates the pod.

## Prerequisites

- OrbStack Kubernetes enabled.
- `kubectl` configured for the current cluster.
- `helm`.
- `python3`.
- Optional: metrics-server for `kubectl top`.

Verified local context while creating this demo:

- Kubernetes context: `orbstack`
- Node: single OrbStack node, Ready
- Namespace used by all resources: `z2jh-context-demo`

## Quickstart

Check cluster access:

```bash
bash scripts/check-cluster.sh
```

Install the baseline static-profile JupyterHub:

```bash
bash scripts/install-baseline.sh
bash scripts/port-forward.sh
```

Open `http://127.0.0.1:8000`, log in with any username and password `demo`, then choose Small, Medium, or Large.

Run the underprovisioning demo:

```bash
bash scripts/demo-underprovisioning.sh
kubectl get pods -n z2jh-context-demo -w
kubectl describe pod underprovision-small -n z2jh-context-demo | grep -A8 -E 'Last State|Reason|OOMKilled'
```

Run the overprovisioning demo:

```bash
bash scripts/demo-overprovisioning.sh
kubectl get pods -n z2jh-context-demo
kubectl describe pod idle-large-1 -n z2jh-context-demo | grep -A8 Requests
kubectl top pods -n z2jh-context-demo
```

If metrics-server is not installed, use `kubectl describe` to show requested resources and scheduler events.

Run the defensive over-requesting demo:

```bash
bash scripts/demo-defensive-overrequesting.sh
kubectl describe pod defensive-large-light -n z2jh-context-demo | grep -A8 Requests
kubectl logs defensive-large-light -n z2jh-context-demo
```

Switch to the proposed method:

```bash
bash scripts/install-proposed.sh
bash scripts/port-forward.sh
```

In the spawn form, enter:

- Intent: `I will train a scikit-learn model on a 1.5GB CSV dataset`
- Dataset size: `1.5`
- Code context:

```python
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
df = pd.read_csv("data.csv")
model.fit(X, y)
```

The pod should receive:

- `RECOMMENDED_PROFILE=large`
- `RECOMMENDATION_REASONS=...dataset size >= 0.5GB...training/modeling context...`

Inspect it:

```bash
kubectl get pods -n z2jh-context-demo
kubectl describe pod -n z2jh-context-demo -l component=singleuser-server
kubectl exec -n z2jh-context-demo deploy/hub -- grep -R "Context-aware recommendation" /srv/jupyterhub 2>/dev/null || true
```

## Expected Observations

| Scenario | Static Z2JH | Proposed Layer | Observable Metric |
| --- | --- | --- | --- |
| Underprovisioning | Small chosen manually, OOMKilled | intent/code recommends Large | OOMKilled count, rerun avoided |
| Overprovisioning | Large idle users block capacity | light intent recommends Small | requested CPU/RAM reduced |
| Defensive overrequesting | user always picks Large | recommendation explains fit | request/usage ratio |

## Repository Layout

```text
README.md
DEMO_SCRIPT.md
CLEANUP.md
helm/
  baseline-values.yaml
  proposed-values.yaml
  generated-values.yaml
scripts/
  check-cluster.sh
  install-baseline.sh
  install-proposed.sh
  uninstall.sh
  demo-underprovisioning.sh
  demo-overprovisioning.sh
  demo-defensive-overrequesting.sh
  watch-pods.sh
  generate-capacity-values.py
  port-forward.sh
notebooks/
  01_oom_late_failure.ipynb
  02_light_eda_large_waste.ipynb
  03_proposed_intent_training.ipynb
workload/
  oom_late_failure.py
  light_eda.py
  train_like_workload.py
recommender/
  recommender.py
  test_recommender.py
k8s/
  idle-large-pod.yaml
  idle-small-pod.yaml
  resource-quota.yaml
```

## Recommender Rules

The prototype is intentionally rule-based, not LLM-based.

- Basic/light Python -> Small.
- `pandas`, `read_csv`, dataframe, CSV/parquet, or dataset size >= 0.5GB -> Medium unless stronger signals exist.
- Training/modeling signals such as `train`, `fit`, `sklearn`, `xgboost`, or dataset size >= 2GB -> Large.
- `torch`, `tensorflow`, `cuda`, `deep learning`, `resnet`, or `bert` -> GPU-or-Large. In this local demo it maps to Large resources because no real GPU is required.

Run local tests:

```bash
python3 -m pip install -r requirements-dev.txt
make check
```

`make check` runs unit tests, Python syntax checks, shell syntax checks,
cluster-free Helm/KubeSpawner/YAML smoke tests, Helm rendering when `helm` is
available, and Kubernetes manifest client dry-runs when `kubectl` is available.
It intentionally skips cluster-mutating demo execution.

Invalid, missing, or negative dataset-size hints are treated as unknown (`0GB`)
so malformed spawn-form input cannot crash recommendation.

## Safety Notes

- Demo workloads allocate bounded memory.
- The OOM demo is intended to fail inside a low-memory pod, not on the host.
- Idle pods use `sleep infinity` only in namespace `z2jh-context-demo`.
- Cleanup deletes only `z2jh-context-demo`.

## Cleanup

```bash
bash scripts/uninstall.sh
```
