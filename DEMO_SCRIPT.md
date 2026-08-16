# Thesis Demo Script

This runbook is designed for a live presentation of the prototype and research artifact:

> **Intent- and Context-Aware Profile Recommendation for Zero to JupyterHub and KubeSpawner**

It provides the step-by-step commands to run, the key observations to point out, and the claim boundaries for each presentation scene.

---

## Demo Story Sequence

1. **Scene 1**: Show the baseline hardware-selection problem (static Small, Medium, Large guessing).
2. **Scene 2**: Demonstrate underprovisioning and late OOM container failure.
3. **Scene 3**: Demonstrate request-based scheduling pressure from idle overprovisioned pods.
4. **Scene 4**: Illustrate defensive over-requesting behavior.
5. **Scene 5**: Remove temporary pressure resources.
6. **Scene 6**: Switch to the proposed intent/context form and preview the recommendation.
7. **Scene 7**: Explain and verify the recommendation applied to the Kubernetes pod.
8. **Scene 8**: Run a bounded workload successfully inside the recommended environment.
9. **Scene 9**: Demonstrate storage-preserving notebook re-provisioning.
10. **Scene 10**: Demonstrate pluggable LLM inference backends: External LLM (Gemini) & Self-Hosted LLM (Ollama).
11. **Scene 11**: Demonstrate policy-bounded dynamic profile generation.

---

## Safety Gate

> **Do not run this demo on a shared, staging, or production cluster.**
> Use only an isolated local cluster that you are authorized to mutate (e.g. Minikube, Docker Desktop, k3d, kind).

| Setting | Default Value |
| :--- | :--- |
| **Namespace** | `z2jh-context-demo` |
| **Helm release** | `context-demo` |
| **JupyterHub chart** | `4.0.0` |
| **Browser URL** | `http://127.0.0.1:8000` |
| **Authentication** | Insecure local-only `DummyAuthenticator` |

---

## Before the Presentation

### 1. Verify local setup & cluster
```bash
bash scripts/setup.sh
bash scripts/check.sh
kubectl config current-context
bash scripts/check-cluster.sh
```

### 2. Prepare Terminals
* **Terminal 1**: Installation, upgrade commands, and inspection.
* **Terminal 2**: Port forwarding (`bash scripts/port-forward.sh`).
* **Terminal 3**: Pod watch (`bash scripts/watch-pods.sh`).
* **Browser**: JupyterHub UI (<http://127.0.0.1:8000>).

---

## Scene 1: Baseline Static Profile Selection

### Goal
Show that baseline JupyterHub forces users to guess raw hardware amounts.

### Run (Terminal 1)
```bash
bash scripts/install-baseline.sh
kubectl get pods -n z2jh-context-demo
```

### Open UI (Terminal 2)
```bash
bash scripts/port-forward.sh
```
Open <http://127.0.0.1:8000>, log in with any username. The spawn page displays Small, Medium, Large.

### Say
> "The platform asks 'Which hardware profile do you want?' The user must turn a workload intention into CPU and memory sizing without direct guidance."

---

## Scene 2: Underprovisioning and Late Failure

### Goal
Show a workload that starts normally and exceeds a Small memory limit.

### Run (Terminal 1)
```bash
bash scripts/demo-underprovisioning.sh
kubectl get pods -n z2jh-context-demo -w
```

### Verify
```bash
kubectl describe pod underprovision-small -n z2jh-context-demo | grep -A8 -E 'Last State|Reason|OOMKilled'
kubectl logs underprovision-small -n z2jh-context-demo
```
Expected: Gradual memory allocation followed by `OOMKilled` (exit code 137).

### Say
> "The workload looked healthy initially, but the static hardware choice made before spawn caused a late container failure and lost state."

---

## Scene 3: Overprovisioning & Scheduling Pressure

### Goal
Show that idle pods with high requests block cluster capacity.

### Run (Terminal 1)
```bash
bash scripts/demo-overprovisioning.sh
kubectl get pods -n z2jh-context-demo -l demo=overprovisioning
```
Expected: One or more pods remain in `Pending` state because requested CPU/memory cannot fit on the node.

---

## Scene 4: Defensive Over-Requesting

### Goal
Illustrate the cost of assigning a light task to the Large profile.

### Run (Terminal 1)
```bash
bash scripts/demo-defensive-overrequesting.sh
kubectl logs defensive-large-light -n z2jh-context-demo
kubectl describe pod defensive-large-light -n z2jh-context-demo | grep -A8 Requests
```
Expected: Workload finishes very quickly, but still ties up 1.5 CPU and 1.5GB RAM.

---

## Scene 5: Remove Temporary Pressure

```bash
kubectl delete pod -n z2jh-context-demo -l demo=overprovisioning --ignore-not-found
kubectl delete resourcequota overprovisioning-request-quota -n z2jh-context-demo --ignore-not-found
kubectl delete pod defensive-large-light -n z2jh-context-demo --ignore-not-found
```

---

## Scene 6: Switch to Proposed Context-Aware Method

### Goal
Replace hardware guessing with natural language intent and explainable recommendation previews.

### Run (Terminal 1)
```bash
bash scripts/install-proposed.sh
```

### Open UI & Enter Example
Open <http://127.0.0.1:8000>. Enter:
* **Intent**: `I will train a scikit-learn model on a 1.5GB CSV dataset`
* **Dataset size**: `1.5`
* **Code context**:
  ```python
  import pandas as pd
  from sklearn.ensemble import RandomForestClassifier
  df = pd.read_csv("data.csv")
  model.fit(X, y)
  ```

1. Click **Preview recommendation**: Shows Large resources, `scipy-data-science` image, and plain-text reasons.
2. Click **Edit inputs**: Shows preview invalidation.
3. Open **Manual Override**: Shows only administrator-allowlisted options.
4. Click **Confirm recommendation**: Launches the configured notebook server.

---

## Scene 7: Explain & Verify the Applied Recommendation

```bash
kubectl get pods -n z2jh-context-demo -l component=singleuser-server
kubectl describe pod -n z2jh-context-demo -l component=singleuser-server | \
  grep -A18 -E 'recommendation-action|recommended-profile|applied-profile|recommended-image|applied-image|Environment|RECOMMENDED_PROFILE|APPLIED_NOTEBOOK_IMAGE|Image:|Requests|Limits'
kubectl logs -n z2jh-context-demo -l component=hub --tail=200 | grep 'recommendation_audit='
```
Expected: `RECOMMENDED_PROFILE=large`, `APPLIED_NOTEBOOK_IMAGE=scipy-data-science`, matched capability reasons, and structured audit logs.

---

## Scene 8: Complete Bounded Workload

In JupyterLab terminal:
```bash
python /home/jovyan/demo/workload/train_like_workload.py
```
Expected: `Training-like workload finished without OOM.`

---

## Scene 9: Storage-Preserving Notebook Re-Provisioning

### Goal
Demonstrate changing workload profiles post-spawn while retaining home directory storage.

### Run
1. In JupyterLab terminal:
   ```bash
   printf 'persistent marker\n' > /home/jovyan/reprovision-marker.txt
   ```
2. Navigate to: `http://127.0.0.1:8000/hub/reprovision`
3. Enter a new workload description (e.g. Deep Learning with PyTorch).
4. Click **Preview replacement**: Review current vs proposed resources and the red restart warning.
5. Check acknowledgement and click **Stop old pod and create replacement**.
6. Observe pod recreation:
   ```bash
   kubectl get pods,pvc -n z2jh-context-demo -w
   ```
7. Verify file retention in the new JupyterLab terminal:
   ```bash
   cat /home/jovyan/reprovision-marker.txt
   ```

---

## Scene 10: Pluggable LLM Inference Backends

### Goal
Demonstrate seamless switching between Gemini API (External LLM) and Local Ollama (Self-Hosted LLM).

### 1. External LLM (Google Gemini)
```bash
# Apply Gemini configuration
helm upgrade context-demo jupyterhub/jupyterhub \
  --version 4.0.0 \
  --namespace z2jh-context-demo \
  --values helm/proposed-values.yaml \
  --values helm/dynamic-values.yaml \
  --values helm/reprovision-values.yaml \
  --values helm/gemini-values.yaml \
  --wait
```
Test complex natural language prompt in spawn form. Note the LLM-derived profile and semantic image choice.

The evaluated external service had only 21/240 valid held-out completions and used rule fallback for the other 219 trials. During a live demo, identify the requested and effective backend from the preview/audit output; do not describe a fallback recommendation as Gemini output.

### 2. Self-Hosted LLM (Local Ollama)
```bash
# Apply Ollama configuration
helm upgrade context-demo jupyterhub/jupyterhub \
  --version 4.0.0 \
  --namespace z2jh-context-demo \
  --values helm/proposed-values.yaml \
  --values helm/dynamic-values.yaml \
  --values helm/reprovision-values.yaml \
  --values helm/ollama-values.yaml \
  --wait
```
Demonstrates in-cluster / host-local zero-network inference with automatic rule-based fallback.

---

## Scene 11: Policy-Bounded Dynamic Profile Generation

```bash
bash scripts/install-dynamic.sh
```
Demonstrates continuous, fine-grained CPU/RAM resource sizing bounded by administrator policies and static per-spawn caps. The shipped adapter does not query live quota or node headroom.

---

## Closing Summary

* **Interactive Pre-Spawn UI**: Pre-spawn recommendation preview UI with confirm, edit, override, and audit events.
* **Curated Image Catalog**: Admin-curated image catalog with immutable digests and capability matching.
* **Pluggable Architecture**: Pluggable recommender architecture (Rule-based, Gemini External LLM, Ollama Self-hosted LLM) with strict schema validation and automatic fallback.
* **Re-Provisioning Engine**: Stop-and-recreate intent-aware re-provisioning with storage retention.
* **Dynamic Resource Sizing**: Continuous policy-bounded dynamic profile generation.
* **Evaluation Suite**: Completed Protocol-v4 evidence with a 60-sample bilingual gold set, four 48×5 recommender matrices, and a 4×8×10 observed Stage C cluster matrix. The results show an explicit robustness-versus-resource-request trade-off and preserve raw LLM outcomes separately from fallback-assisted decisions.

---

## End the Demo

```bash
bash scripts/uninstall.sh
```
Confirm clean deletion:
```bash
kubectl get namespace z2jh-context-demo
```
