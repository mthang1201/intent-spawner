# Thesis Demo Script

This runbook is designed for a live presentation of the prototype and research artifact:

> **Intent- and Context-Aware Profile Recommendation for Zero to JupyterHub and KubeSpawner**

It provides step-by-step commands to run, key observations to highlight, and claim boundaries for each presentation scene.

---

## Demo Story Sequence

1. **Scene 1**: Show the baseline hardware-selection problem (static Small, Medium, Large guessing).
2. **Scene 2**: Demonstrate underprovisioning and late OOM container failure.
3. **Scene 3**: Demonstrate request-based scheduling pressure from idle overprovisioned pods.
4. **Scene 4**: Illustrate defensive over-requesting behavior.
5. **Scene 5**: Remove temporary pressure resources.
6. **Scene 6**: Switch to the proposed intent/context form and preview recommendations (P1/P2).
7. **Scene 7**: Explain and verify the recommendation applied to the Kubernetes pod.
8. **Scene 8**: Run a bounded workload successfully inside the recommended environment.
9. **Scene 9**: Demonstrate storage-preserving notebook re-provisioning.
10. **Scene 10**: Demonstrate policy-bounded dynamic resource sizing.
11. **Scene 11 (Optional)**: Protocol-v5 empirical evidence and evaluation suite.

---

## Safety Gate

> **Do not run this demo on a shared, staging, or production cluster.**
> Use only an isolated local cluster that you are authorized to mutate (e.g. Minikube, Docker Desktop, k3d, kind, OrbStack).

| Setting | Default Value |
| :--- | :--- |
| **Namespace** | `z2jh-context-demo` |
| **Helm release** | `context-demo` |
| **JupyterHub chart** | `4.0.0` |
| **Browser URL** | `http://127.0.0.1:8000` |
| **Authentication** | Insecure local-only `DummyAuthenticator` (any username and non-empty password) |

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
Open <http://127.0.0.1:8000>, log in with any username and password. The spawn page displays Small, Medium, Large.

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
Expected: Gradual memory allocation followed by `OOMKilled` (exit code 137) because allocation exceeds the 384Mi limit.

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
Expected: One or more pods remain in `Pending` state (or blocked by quota fallback) because requested CPU/memory cannot fit on the node.

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
# Option A: Rule-based recommender (P1, fast zero-dependency baseline)
bash scripts/install-proposed.sh

# Option B: Proposed hybrid retrieval backend (P2: structured intent + BM25 + dense retrieval + deterministic constraints)
BACKEND_VALUES=helm/recommender-p2-values.yaml bash scripts/install-proposed.sh
```

### Open UI & Enter Example
Open <http://127.0.0.1:8000>. In **Describe your workload**, enter:
* `I will train a scikit-learn model on a 1.5GB CSV dataset using pandas and RandomForestClassifier`

1. Click **Preview recommendation**: Inspect the preview card showing the recommended profile (`Large`), image (`scipy-data-science`), backend details, and human-readable explanation reasons.
2. **Observe live invalidation**: Type or edit any character in **Describe your workload**. The preview card automatically collapses and confirmation is disabled until re-previewed (preventing stale submissions).
3. **Infeasibility constraint check (demo talking point)**: Enter an excessive workload (e.g., `Need 128GB RAM and 8 NVIDIA GPUs for distributed deep learning`). Notice the warning alert: *"Workload exceeds available resources"* detailing requirements vs. catalog limits and unmet constraints, disabling confirmation and enforcing manual override.
4. Open **Manual Override**: If needed, shows administrator-allowlisted hardware profiles (`small`, `medium`, `large`) and curated images (`minimal-python`, `scipy-data-science`, `pytorch-deep-learning`, `tensorflow-deep-learning`).
5. Click **Confirm recommendation**: Launches the single-user notebook server with the confirmed configuration.

---

## Scene 7: Explain & Verify the Applied Recommendation

### Goal
Verify that the recommended profile, container image, and low-cardinality metadata were correctly enforced on Kubernetes without leaking raw user prompts.

### Run (Terminal 1)
```bash
# Check singleuser pod status
kubectl get pods -n z2jh-context-demo -l component=singleuser-server

# Inspect enforced container image, CPU/RAM requests and limits
kubectl describe pod -n z2jh-context-demo -l component=singleuser-server | \
  grep -E 'Image:|Requests:|Limits:'

# Inspect structured privacy-preserving annotations
kubectl get pod -n z2jh-context-demo -l component=singleuser-server \
  -o jsonpath='{.items[0].metadata.annotations}' | jq 'with_entries(select(.key | startswith("intent-spawner.local")))'

# Inspect structured audit log on the Hub
kubectl logs -n z2jh-context-demo -l component=hub --tail=200 | grep 'recommendation_audit='
```

### Expected Output
1. **Annotations**:
   - `intent-spawner.local/profile: "large"`
   - `intent-spawner.local/image: "scipy-data-science"`
   - `intent-spawner.local/backend: "p2"` (or `"rule_based"`)
   - `intent-spawner.local/event-id: "<uuid>"`
   - `intent-spawner.local/policy-version: "dynamic-resource-policy-v2"`
   - `intent-spawner.local/catalog-version: "2026-08-06.1"`
   - *(If P2 backend is used)*: `intent-spawner.local/pipeline-version: "p2-v1"`, `intent-spawner.local/candidate-count`, `intent-spawner.local/feasible-count`, etc.
2. **Container Spec**:
   - `Image`: `quay.io/jupyter/scipy-notebook@sha256:1a91a693c8cb086f3607f2ed38a2743ecd53dd1dac2d3e84e6cd647b33fd2bba`
   - `Requests`: `cpu: 1500m`, `memory: 1536Mi`
   - `Limits`: `cpu: 2`, `memory: 2Gi`
3. **Audit Log**:
   - Structured JSON: `recommendation_audit={"attempts": 1, "backend": "p2", "catalog_version": "2026-08-06.1", "event": "recommendation_decision", "event_id": "...", "image_id": "scipy-data-science", "profile": "large", ...}`
   - **Data Governance Boundary**: Raw prompt text and user queries are completely omitted from logs and annotations.

---

## Scene 8: Complete Bounded Workload

### Goal
Show that the recommended Large profile comfortably executes the workload without OOM failure.

### Run
In JupyterLab Terminal:
```bash
python /home/jovyan/demo/workload/train_like_workload.py
```
*(Or run directly via Python one-liner)*:
```bash
python -c 'import time; b = [bytearray(64 * 1024 * 1024) for _ in range(8)]; print("Allocated 512MiB memory successfully without OOM.")'
```
Expected: `Training-like workload finished without OOM.` (Allocates 512 MiB smoothly, whereas Small failed at >384 MiB in Scene 2).

---

## Scene 9: Storage-Preserving Notebook Re-Provisioning

### Goal
Demonstrate changing workload profiles post-spawn while preserving files in `/home/jovyan` on the persistent volume claim (PVC).

### Run
1. In the running JupyterLab terminal, write a marker file:
   ```bash
   printf 'persistent marker\n' > /home/jovyan/reprovision-marker.txt
   ```
2. In browser, navigate to: `http://127.0.0.1:8000/hub/reprovision`
3. Enter new workload:
   - Workload intent: `I need PyTorch and CUDA for deep learning image classification`
4. Click **Preview replacement**:
   - Inspect proposed replacement (`pytorch-deep-learning` image, updated resource sizing).
   - Review warnings regarding notebook restart, kernel state loss, and persistent storage retention.
5. Check the acknowledgement checkbox: *"I saved my files and accept loss of kernel state."*
6. Click **Stop old pod and create replacement**.
7. In Terminal 3 (or Terminal 1), observe pod recreation:
   ```bash
   kubectl get pods,pvc -n z2jh-context-demo -w
   ```
   Notice the old pod terminates and the replacement pod starts with the new image and resources, binding to the exact same PVC.
8. Open the new JupyterLab session terminal and verify data retention:
   ```bash
   cat /home/jovyan/reprovision-marker.txt
   ```
   Expected: `persistent marker`.

---

## Scene 10: Policy-Bounded Dynamic Resource Sizing

### Goal
Demonstrate continuous, fine-grained CPU/RAM resource sizing bounded by administrator policies and static per-spawn caps.

### Run (Terminal 1)
```bash
bash scripts/install-dynamic.sh
```

### Test in UI
Open <http://127.0.0.1:8000>. In the Workload Intent form, enter specific dataset sizes and requirements to inspect dynamic resource sizing. The system computes policy-bounded CPU and memory allocations while respecting administrator limits in `recommender/resource-policy.yaml`.

---

## Scene 11 (Optional): Protocol-v5 Empirical Evidence & Evaluation

### Goal
Showcase the comprehensive offline and online experimental suite (Protocol-v5) evaluating recommendation accuracy, robustness, and system efficiency.

### Run (Terminal 1)
```bash
# Launch interactive unified experiment runner (E1–E5, local/LLM selection, reporting)
make experiments
# or: python scripts/run_experiments.py

# Or inspect the synthesized thesis claim adjudication matrix
make research-analysis
```

Key evidence pillars:
* **E1 (Recommendation Quality)**: Offline Gold benchmark evaluating Top-1/Top-3 accuracy, profile match, and safety constraints.
* **E2 (Natural Language Robustness)**: Evaluates resilience against phrasing variations, typos, and adversarial prompts.
* **E3 (Human Usability Study)**: Measures cognitive load, task completion time, and user satisfaction (B0 baseline vs P2 context-aware).
* **E4 (Kubernetes Resource Sizing & Pod Outcomes)**: Verifies real pod execution outcomes and scheduling efficiency.
* **E5 (Image Storage & Container Functional Validation)**: Verifies container capabilities and digest immutability.

---

## Closing Summary

* **Interactive Pre-Spawn UI**: Pre-spawn recommendation preview UI with explainable recommendations, live input invalidation, manual override safeguards, and bounded low-cardinality audit telemetry.
* **Hybrid Retrieval & Constraint-Governed Optimization (P2)**: Structured intent extraction paired with hybrid BM25 + dense embedding retrieval and strict deterministic hard constraints (preventing OOM and resource waste).
* **Curated Image Catalog & Policy Gate**: Admin-curated image catalog with immutable SHA-256 digests and `PolicyValidator` enforcement preventing arbitrary profile or image injection.
* **Intent-Aware Re-Provisioning**: Storage-preserving stop-and-recreate workflow retaining persistent volume data (`/home/jovyan`) across profile/image transitions.
* **Policy-Bounded Dynamic Resource Sizing**: Continuous fine-grained CPU/RAM sizing bounded by administrator policies and static per-spawn caps.
* **Evaluation Suite (Protocol-v5)**: Multi-dimensional evaluation harness (E1 recommendation quality, E2 natural-language robustness, E3 human usability study, E4 Kubernetes resource sizing & pod outcomes, and E5 image storage & container functional validation).

---

## End the Demo

```bash
bash scripts/uninstall.sh
```
Confirm clean deletion:
```bash
kubectl get namespace z2jh-context-demo
```
