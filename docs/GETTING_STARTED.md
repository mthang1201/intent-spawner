# Getting Started: Intent-Spawner Prototype

This guide provides step-by-step instructions for running, testing, evaluating, and deploying the **Intent-Spawner Proof-of-Concept Prototype**.

> **Scope Notice:** This project represents the **Graduation Internship (~50% scope milestone)**. The core deliverable centers on local offline hybrid recommendation (P2 Local), unit testing, and offline evaluation (E1, E5). Extended features such as external LLM extractors (P2 LLM), dynamic resource rightsizing, multi-node live scheduling (E4), and human user studies (E3) are preserved as previews for the future **Graduation Thesis roadmap**.
>
> For full demarcation details, see [**Graduation Internship Scope & Demarcation**](INTERNSHIP_SCOPE.md).

---

## Quick Navigation

| Scope Category | Goal | Guide Section | Prerequisites / Cluster |
| :--- | :--- | :--- | :--- |
| **Internship Core** | Setup Python virtual environment & dependencies | [1. Prerequisites & Setup](#1-prerequisites--local-environment-setup) | None (Local machine) |
| **Internship Core** | Run recommender logic & constraint unit tests | [Local Unit Tests](#a2-run-software-tests) | None (Local machine) |
| **Internship Core** | Run offline recommendation quality benchmark (E1 Local) | [Offline Evaluation: E1 Local](#protocol-v5-experiments-internship-focus) | None (Local machine) |
| **Internship Core** | Analyze image deduplication & functional probes (E5) | [Image Evaluation: E5 Probes](#offline-evaluation-e5-container-image-storage--functional-probes) | Local Docker daemon |
| **Internship Core** | Inspect standalone recommender via CLI | [CLI Recommender Check](#a1-inspect-the-standalone-recommender) | None (Local machine) |
| **Internship Core** | Deploy JupyterHub with P1 or P2 Local | [Interactive Demo (B0, P1, P2 Local)](#path-b-interactive-jupyterhub-demo-internship-core) | Local disposable cluster |
| *Thesis Preview* | Evaluate LLM extractor mode (E1 LLM) | [E1 with LLM Extractor](#e1-llm-mode-thesis-preview) | OpenAI / Gemini API key |
| *Thesis Preview* | Natural language robustness analysis (E2) | [Robustness Analysis: E2](#e2-natural-language-robustness-analysis) | None (Local machine) |
| *Thesis Preview* | Human usability evaluation (E3) | [Human Usability: E3](#e3-human-usability-study-thesis-preview) | Human participants |
| *Thesis Preview* | Live cluster resource envelope & efficiency (E4) | [Cluster Scheduling: E4](#e4-live-cluster-resource-scheduling-thesis-preview) | Disposable Kubernetes cluster |
| *Thesis Preview* | Storage-preserving notebook reprovisioning | [Notebook Reprovisioning](#path-b3-storage-preserving-notebook-re-provisioning-thesis-preview) | Disposable demo cluster |
| *Thesis Preview* | Continuous dynamic CPU/RAM sizing | [Dynamic Resource Sizing](#path-b4-policy-bounded-dynamic-resource-sizing-thesis-preview) | Disposable demo cluster |

---

## 1. Prerequisites & Local Environment Setup

### Local Tools Required (Internship Core):
* **Python 3.11+**
* **Git** and **Bash**
* `pip` and Python `venv` support
* Local **Docker daemon** (required for container image inspections in E5)

### Optional Tools for Cluster Demos (Internship Core & Thesis Preview):
* `kubectl` and **Helm 3**
* A disposable local Kubernetes cluster (OrbStack, Docker Desktop, Minikube, or k3d)

### Clone & Install:

```bash
git clone https://github.com/mthang1201/intent-spawner.git
cd intent-spawner

# Create virtual environment and install all dependencies
bash scripts/setup.sh
```

---

## 2. Core Offline Evaluation & Unit Tests (Internship Core)

The core internship verification is entirely executable offline without external cloud API dependencies or expensive cluster configurations.

### Protocol-v5 Experiments: Internship Focus

The primary empirical evaluations for the internship are **E1 (Recommendation Quality)** and **E5 (Image Storage & Functional Probes)**:

#### E1 Local Mode: Offline Recommendation Quality Benchmark
Runs the deterministic local regex extractor against the hybrid retrieval pipeline (P2 Local) and compares it against the rule-based baseline (P1):

```bash
# Direct make target (zero API cost, sub-second execution):
make eval-offline-local
```
Results are saved to `results_v5/protocol-v5.0.0/E1/run-e1-local/`.

#### Offline Evaluation: E5 Container Image Storage & Functional Probes
Evaluates container layer deduplication across the allowlisted catalog and verifies that recommended images satisfy workload software constraints:

```bash
# 1. Analyze container layer sharing and catalog disk footprint:
.venv/bin/python -m evaluation_v5.image_storage \
  --experiment storage \
  --mode docker \
  --stage development

# 2. Run functional container probes against E1 local recommendations:
.venv/bin/python -m evaluation_v5.image_storage \
  --experiment functional \
  --recommendation-run results_v5/protocol-v5.0.0/E1/run-e1-local \
  --mode docker \
  --pull-policy missing
```

#### Run Unified Experiment Runner
To interactively explore experiments:

```bash
make experiments
# or directly: python scripts/run_experiments.py
```

---

## Path A: Local Synthetic Benchmark & Unit Tests

### A1. Inspect the Standalone Recommender

Test structured intent parsing, BM25 + dense retrieval, and hard constraint checking directly from the CLI:

```bash
.venv/bin/python -m recommender.recommender \
  --intent "Train a scikit-learn random forest model on 1.5GB tabular dataset" \
  --dataset-gb 1.5 \
  --code-context "import pandas as pd; from sklearn.ensemble import RandomForestClassifier; model.fit(X, y)"
```

### A2. Run Software Tests

Run the core unit test suite verifying the P2 backend, hybrid retrieval, single-input workflow, and constraint evaluator:

```bash
make test
```

To run the complete pytest suite including evaluation harness tests:

```bash
.venv/bin/python -m pytest
```

---

## Path B: Interactive JupyterHub Demo (Internship Core)

> **Safety Notice:** Run only on a disposable local Kubernetes cluster (e.g. OrbStack, Minikube). The scripts create and mutate resources inside the namespace `z2jh-context-demo`.

### B1. Install Baseline (Static Hardware Profiles - B0)

Demonstrates the default unguided JupyterHub user experience:

```bash
# 1. Verify cluster connection
kubectl config current-context

# 2. Install baseline JupyterHub
bash scripts/install-baseline.sh

# 3. Forward port to localhost (run in separate terminal)
bash scripts/port-forward.sh
```
Open `http://127.0.0.1:8000` and log in with any dummy username. Notice that users must manually guess hardware profiles (Small, Medium, Large) without guidance.

### B2. Install the Context-Aware Spawner (P1 or P2 Local)

Deploy the heuristic recommender (P1):

```bash
bash scripts/install-proposed.sh
```

Or deploy the **Core Proposed Method (P2 Local)** featuring local regex intent extraction and hybrid retrieval:

```bash
BACKEND_VALUES=helm/recommender-p2-values.yaml bash scripts/install-proposed.sh
```

Open `http://127.0.0.1:8000`:
1. **Workload Intent Form**: Enter a workload description (e.g., `Train a PyTorch vision model with CUDA GPU acceleration`).
2. **Preview recommendation**: Inspect the retrieved profile, allowlisted container image, generated justification, and constraint checks.
3. **Manual Override (Optional)**: Adjust or select an alternative profile/image from the administrator catalog if desired.
4. **Confirm recommendation**: KubeSpawner spawns the configured user pod.

---

## Extended / Thesis Roadmap Features (Preview)

The following capabilities represent extended research and evaluation tracks designed for the subsequent Graduation Thesis.

### E1 LLM Mode (Thesis Preview)
Evaluates P2 using external large language models (OpenAI or Gemini) for semantic intent extraction:

```bash
make eval-offline-llm
```

### E2: Natural Language Robustness Analysis
Measures recommender resilience against adversarial perturbations, typos, and phrasing variations:

```bash
make eval-robustness
```

### E3: Human Usability Study (Thesis Preview)
Measures user completion time, cognitive friction, and System Usability Scale (SUS) scores comparing B0 against P2:

```bash
# Automated smoke verification of study protocol:
make v5-user-study-smoke
```

### E4: Live Cluster Resource Scheduling (Thesis Preview)
Measures actual multi-pod Kubernetes resource envelopes, scheduling latencies, and allocation efficiency under 20 synthetic workload families:

```bash
# Dry-run resource envelope simulation:
make v5-resource-dry-run
```

### Path B3: Storage-Preserving Notebook Re-Provisioning (Thesis Preview)

Allows users to reconfigure resource profiles on a running session without losing PVC data:

1. Create a marker file in your running notebook terminal:
   ```bash
   printf 'persistent marker\n' > /home/jovyan/reprovision-marker.txt
   ```
2. Navigate to: `http://127.0.0.1:8000/hub/reprovision`
3. Enter new workload requirements, preview changes, and confirm replacement.
4. Verify that the new pod mounts the existing PVC and retains `/home/jovyan/reprovision-marker.txt`.

### Path B4: Policy-Bounded Dynamic Resource Sizing (Thesis Preview)

Enables fine-grained, continuous CPU/RAM rightsizing instead of static profile tiers:

```bash
BACKEND_VALUES=helm/recommender-p2-values.yaml bash scripts/install-dynamic.sh
```

---

## Cleanup

Follow the [cleanup runbook](CLEANUP.md) to safely delete demo namespaces and local artifacts:

```bash
bash scripts/cleanup.sh
```
