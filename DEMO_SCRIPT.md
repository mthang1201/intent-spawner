# Thesis Demo Script

## 1. Static Profile Selection Problem

Goal: show that baseline Z2JH asks users to choose hardware even when they usually know their task better than CPU/RAM sizing.

Commands:

```bash
bash scripts/check-cluster.sh
bash scripts/install-baseline.sh
bash scripts/port-forward.sh
```

Expected output:

- Current context is `orbstack`.
- Namespace `z2jh-context-demo` exists.
- JupyterHub is reachable at `http://127.0.0.1:8000`.
- Spawn page shows Small, Medium, and Large profiles.

Explanation:

The platform asks "Which hardware profile do you want?" This pushes resource-sizing responsibility onto the user.

## 2. Underprovisioning: Small Fails Late

Goal: demonstrate a user choosing Small, running a workload that looks fine at first, then losing state when the pod/kernel is OOMKilled.

Commands:

```bash
bash scripts/demo-underprovisioning.sh
kubectl get pods -n z2jh-context-demo -w
```

Observation commands:

```bash
kubectl describe pod underprovision-small -n z2jh-context-demo | grep -A8 -E 'Last State|Reason|OOMKilled'
kubectl logs underprovision-small -n z2jh-context-demo --previous 2>/dev/null || kubectl logs underprovision-small -n z2jh-context-demo
```

Expected output:

- Pod starts and prints gradual `allocated_mib=...` messages.
- Pod eventually becomes `Failed` or shows container `OOMKilled`.

Pain point:

The user selected too little memory, but the error appears only after some execution time. The user loses progress and must restart and rerun.

## 3. Overprovisioning: Idle Large Users Block Capacity

Goal: show that Kubernetes scheduling accounts for resource requests, not actual idle usage.

Commands:

```bash
bash scripts/demo-overprovisioning.sh
kubectl get pods -n z2jh-context-demo
```

Observation commands:

```bash
kubectl describe pod idle-large-1 -n z2jh-context-demo | grep -A8 Requests
kubectl describe pod idle-large-2 -n z2jh-context-demo | grep -A12 -E 'Events|Insufficient|quota|Requests'
kubectl top pods -n z2jh-context-demo
```

Expected output:

- One or more idle Large pods may be `Pending` due to insufficient requested CPU/RAM.
- If OrbStack capacity allows all pods, the script applies a ResourceQuota fallback and shows a quota/request block.
- `kubectl top` may show low actual usage if metrics-server is installed.

Pain point:

Large idle sessions reserve schedulable capacity even when they do almost no work, reducing concurrency for other users.

## 4. Defensive Over-Requesting

Goal: connect the previous two demos. After being OOMKilled on Small, users often choose Large "just in case."

Commands:

```bash
bash scripts/demo-defensive-overrequesting.sh
kubectl get pods -n z2jh-context-demo
```

Observation commands:

```bash
kubectl describe pod defensive-large-light -n z2jh-context-demo | grep -A8 Requests
kubectl logs defensive-large-light -n z2jh-context-demo
```

Expected output:

- The workload prints a tiny EDA result.
- The pod still requests Large-profile CPU/RAM.

Pain point:

Once trust is broken by underprovisioning, users compensate by over-requesting resources for light work.

## 5. Switch to Proposed Method

Goal: replace hardware selection with intent and code context.

Commands:

```bash
bash scripts/install-proposed.sh
bash scripts/port-forward.sh
```

Spawn form input:

- Intent: `I will train a scikit-learn model on a 1.5GB CSV dataset`
- Dataset size: `1.5`
- Code context:

```python
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
df = pd.read_csv("data.csv")
model.fit(X, y)
```

Expected output:

- The spawn page asks what the user is trying to do.
- The user does not choose Small/Medium/Large manually.

Explanation:

The system now asks the question the user can answer naturally, then translates it into KubeSpawner resources.

## 6. Recommendation and Explainability

Goal: prove the recommendation is applied to the single-user pod.

Observation commands:

```bash
kubectl get pods -n z2jh-context-demo
kubectl describe pod -n z2jh-context-demo -l component=singleuser-server | grep -A8 -E 'z2jh-context-demo.local/recommended-profile|Environment|RECOMMENDED_PROFILE'
```

Inside JupyterLab, open:

```text
/home/jovyan/demo/notebooks/03_proposed_intent_training.ipynb
```

Expected output:

- `RECOMMENDED_PROFILE=large`
- Reasons include dataset size and training/modeling context.

Explanation:

The recommendation is explainable and visible in pod environment variables and annotations.

## 7. Workload Completes Without OOM

Goal: show the same class of training-like workload completes when the profile is recommended from context.

Commands inside the proposed notebook:

```python
!python /home/jovyan/demo/workload/train_like_workload.py
```

Expected output:

- The script prints incremental allocation up to the bounded target.
- It finishes with `Training-like workload finished without OOM.`

Closing sentence:

The prototype does not require an LLM or real GPU. It demonstrates the thesis idea: profile selection can be derived from intent and context, reducing both late failures and defensive over-requesting.

