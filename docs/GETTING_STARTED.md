# Getting Started

This guide provides a comprehensive walkthrough for onboarding, running the interactive JupyterHub prototype, configuring pluggable LLM backends, and executing evaluation benchmarks.

---

## Quick Navigation

| Goal | Recommended Path | Cluster Required |
| :--- | :--- | :--- |
| **Verify repository & unit tests** | Setup & Verification Suite | No |
| **Run local synthetic benchmark** | Path A: Local Synthetic Benchmark | No |
| **Run interactive JupyterHub demo** | Path B: Interactive JupyterHub Demo | Yes (Local disposable cluster) |
| **Configure External LLM (Gemini API)** | Path B-LLM: Gemini Recommender Setup | Yes |
| **Configure Self-Hosted LLM (Ollama)** | Path B-Ollama: Local Ollama Setup | Yes |
| **Test Intent-Aware Re-Provisioning** | Path B10: Re-provisioning Workflow | Yes |
| **Test Policy-Bounded Dynamic Mode** | Path B11: Dynamic Profile Generation | Yes |
| **Run Evaluation Framework v4** | Path D: Evaluation Protocol v4 Suite | No (Offline evaluation) |
| **Validate preserved Kubernetes evidence** | Path C: Preserved Cluster Evaluation | No |

---

## 1. Prerequisites & Local Environment Setup

### Local Tools Required:
* Python 3.11+
* Git and Bash
* `pip` and Python `venv` support

### Optional Tools for the Interactive Demo:
* `kubectl` and Helm 3
* A disposable local Kubernetes cluster (Minikube, Docker Desktop, k3d, kind, or OrbStack)

### Clone & Install Dependencies:
```bash
git clone https://github.com/mthang1201/intent-spawner.git
cd intent-spawner

# Create virtual environment and install dependencies
bash scripts/setup.sh
```

### Run Repository Integrity Verification:
```bash
bash scripts/check.sh
```
This script validates unit tests, smoke tests, preserved cluster artifacts, capacity runner planning, Python/Shell syntax, and Helm template rendering.

---

## Path A: Local Synthetic Benchmark & Unit Tests

### A1. Inspect the Standalone Recommender
Test intent and code-context parsing directly from the command line:

```bash
.venv/bin/python -m recommender.recommender \
  --intent "Train a scikit-learn random forest model on 1.5GB tabular dataset" \
  --dataset-gb 1.5 \
  --code-context "import pandas as pd; from sklearn.ensemble import RandomForestClassifier; model.fit(X, y)"
```

### A2. Run the Full Test Suite
Test all backends (Rule-based, External LLM, Self-hosted LLM, Dynamic Resources, and Reliability layers):

```bash
.venv/bin/python -m pytest recommender/test_recommender.py \
  recommender/test_external_llm.py \
  recommender/test_self_hosted_llm.py \
  recommender/test_reliability.py \
  recommender/test_dynamic_resources.py \
  tests/test_helm_recommender_deployment.py \
  tests/test_recommender_backends_integration.py
```

### A3. Run Local Matrix Benchmark Dry-Run
```bash
.venv/bin/python -m experiments.runner \
  --full-matrix \
  --repeats 5 \
  --seed 20260719 \
  --dry-run \
  --environment-id local-dry-run
```

---

## Path B: Interactive JupyterHub Demo

> **Safety Notice:** Run only on a disposable local Kubernetes cluster. The scripts create and mutate resources inside namespace `z2jh-context-demo`.

### B1. Install Baseline (Static Hardware Profiles)
```bash
# Check cluster context
kubectl config current-context

# Install static baseline
bash scripts/install-baseline.sh

# Start port forwarding (in a separate terminal)
bash scripts/port-forward.sh
```
Open `http://127.0.0.1:8000` and log in with any credentials. Notice the baseline asks you to choose raw hardware (Small, Medium, Large) without guidance.

### B2. Upgrade to Proposed Method (Context-Aware Pre-Spawn Form)
```bash
bash scripts/install-proposed.sh
```
Open `http://127.0.0.1:8000`. You will see the new **Workload Intent Form**:
1. Enter your task (e.g., `I will train a scikit-learn model on a 1.5GB CSV dataset`).
2. Enter dataset size: `1.5`.
3. Enter code snippet:
   ```python
   import pandas as pd
   from sklearn.ensemble import RandomForestClassifier
   df = pd.read_csv("data.csv")
   model.fit(X, y)
   ```
4. Click **Preview recommendation**: Inspect the recommended profile (`large`), image (`scipy-data-science`), and human-readable explanation reasons.
5. Click **Confirm recommendation**: KubeSpawner applies the configuration and creates the user pod.

---

## Path B-LLM: Configuring External LLM (Google Gemini)

To replace the rule-based backend with Google `gemini-3.5-flash` via its OpenAI-compatible endpoint:

### 1. Create the Kubernetes Secret
```bash
read -rsp 'Enter Gemini API key: ' GEMINI_KEY; echo
kubectl create secret generic intent-spawner-external-llm \
  --namespace=z2jh-context-demo \
  --from-literal=api-key="$GEMINI_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
unset GEMINI_KEY
```

### 2. Deploy with Gemini Configuration
```bash
# 1. Package recommender runtime
bash scripts/install-dynamic.sh

# 2. Upgrade Helm with Gemini values
helm upgrade context-demo jupyterhub/jupyterhub \
  --version 4.0.0 \
  --namespace z2jh-context-demo \
  --values helm/proposed-values.yaml \
  --values helm/dynamic-values.yaml \
  --values helm/reprovision-values.yaml \
  --values helm/gemini-values.yaml \
  --wait
```

---

## Path B-Ollama: Configuring Self-Hosted LLM (Local Ollama)

To run private inference with zero external network dependencies:

### 1. Start Local Ollama
```bash
# Install Ollama (macOS)
brew install ollama

# Start the Ollama daemon
ollama serve

# In another terminal, pull the model
ollama pull llama3
```

### 2. Deploy with Ollama Configuration
```bash
# Upgrade Helm with Ollama values (connecting to host.docker.internal)
helm upgrade context-demo jupyterhub/jupyterhub \
  --version 4.0.0 \
  --namespace z2jh-context-demo \
  --values helm/proposed-values.yaml \
  --values helm/dynamic-values.yaml \
  --values helm/reprovision-values.yaml \
  --values helm/ollama-values.yaml \
  --wait
```

---

## Path B10: Storage-Preserving Notebook Re-Provisioning

Test changing workloads on an already-running notebook session:

1. Inside your running JupyterLab terminal, save a persistent file:
   ```bash
   printf 'persistent marker\n' > /home/jovyan/reprovision-marker.txt
   ```
2. In your browser, navigate to: `http://127.0.0.1:8000/hub/reprovision`
3. Enter your new workload (e.g., deep learning with PyTorch).
4. Click **Preview replacement**: Notice the comparison between current and proposed profiles and the red restart warning.
5. Check the acknowledgement checkbox and click **Stop old pod and create replacement**.
6. Observe in your terminal:
   ```bash
   kubectl get pods,pvc -n z2jh-context-demo -w
   ```
   The old pod terminates, a new pod with the new profile/image is created, and `/home/jovyan/reprovision-marker.txt` is retained on the PVC.

---

## Path B11: Policy-Bounded Dynamic Resource Sizing

To enable fine-grained continuous CPU/RAM sizing instead of fixed profile tiers:

```bash
bash scripts/install-dynamic.sh
```
This activates [`helm/dynamic-values.yaml`](file:///Users/mthang1201/Documents/datn/intent-spawner/helm/dynamic-values.yaml) and applies administrator policy boundaries with quota admission checks.

---

## Path D: Evaluation Protocol v4 Suite

To validate the bilingual 60-sample benchmark (12 development + 48 held-out) and preview the Protocol-v4 plans without making live model or cluster calls:

```bash
# 1. Validate Gold-Set Schema & Stratification
make v4-validate

# 2. Run Recommender Benchmark Across All Backends
.venv/bin/python -m evaluation_v4.run_recommenders --dry-run

# 3. Execute System Effectiveness Pairing Plan
.venv/bin/python -m evaluation_v4.plan_system --dry-run
```

The authoritative observed matrices are already complete. Do not overwrite or present a new dry run as those results. Their interpretation and exact evidence identities are documented in:

* [`evaluation/PROTOCOL_V4_REVISED_EVALUATION_REPORT.md`](evaluation/PROTOCOL_V4_REVISED_EVALUATION_REPORT.md)
* [`evaluation/PROTOCOL_V4_EXTERNAL_LLM_LIVE_REPORT.md`](evaluation/PROTOCOL_V4_EXTERNAL_LLM_LIVE_REPORT.md)
* [`evaluation/STAGE_C_CONFIRMATORY_REPORT.md`](evaluation/STAGE_C_CONFIRMATORY_REPORT.md)

Live external or Stage C reproduction requires an explicit operator decision, frozen configuration, credentials, and a disposable cluster. Follow [`evaluation/PROTOCOL_V4_REPRODUCIBILITY.md`](evaluation/PROTOCOL_V4_REPRODUCIBILITY.md); never point a new run at an authoritative result directory.

---

## Cleanup

When you are finished with the demo, remove all created Kubernetes resources:

```bash
bash scripts/uninstall.sh
```

Confirm deletion:
```bash
kubectl get namespace z2jh-context-demo
```
