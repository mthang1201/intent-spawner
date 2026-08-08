# Intent- and Context-Aware Profile Recommendation for Zero to JupyterHub

This repository contains the graduation thesis prototype and research artifact:

> **Intent- and Context-Aware Profile Recommendation for Zero to JupyterHub and KubeSpawner**

Instead of forcing users to guess raw hardware quantities (CPU/RAM limits), the system asks users to describe their workload intent, accepts optional dataset sizes and code context, and presents an explainable recommendation preview combining an optimal resource profile and an administrator-allowlisted notebook image. Users can confirm, edit, or override the recommendation before any Kubernetes pod is created.

---

## Table of Contents

1. [Problem Statement](#problem-statement)
2. [Key Architecture & Implemented Features (Tasks A–F)](#key-architecture--implemented-features-tasks-a-f)
   - [Task A — Recommendation Preview UI](#task-a--recommendation-preview-ui)
   - [Task B — Notebook Image Recommendation](#task-b--notebook-image-recommendation)
   - [Task C — Pluggable Recommender Framework](#task-c--pluggable-recommender-framework)
     - [Rule-Based Recommender](#1-rule-based-recommender)
     - [External LLM API (e.g., Google Gemini)](#2-external-llm-api-eg-google-gemini)
     - [Self-Hosted LLM (e.g., Local Ollama)](#3-self-hosted-llm-eg-local-ollama)
   - [Task D — Intent-Aware Re-Provisioning](#task-d--intent-aware-re-provisioning)
   - [Task E — Policy-Bounded Dynamic Profile Generation](#task-e--policy-bounded-dynamic-profile-generation-stretch-goal)
   - [Task F — Evaluation Framework Redesign (Protocol v4)](#task-f--evaluation-framework-redesign-protocol-v4)
3. [Setup & Deployment Guide](#setup--deployment-guide)
   - [Prerequisites & Local Verification](#1-prerequisites--local-verification)
   - [Deploying the Interactive Demo](#2-deploying-the-interactive-demo)
   - [Configuring External LLM (Gemini API)](#3-configuring-external-llm-gemini-api)
   - [Configuring Self-Hosted LLM (Local Ollama)](#4-configuring-self-hosted-llm-local-ollama)
   - [Enabling Dynamic Resource Selection](#5-enabling-dynamic-resource-selection)
4. [Repository Map](#repository-map)
5. [Verification & Test Commands](#verification--test-commands)
6. [Research Scope, Limitations & Data Safety](#research-scope-limitations--data-safety)
7. [Cleanup](#cleanup)
8. [Documentation Index](#documentation-index)

---

## Problem Statement

Conventional JupyterHub deployments ask users to pick from static profiles such as **Small**, **Medium**, or **Large**. Data scientists and students understand their task (e.g., training a scikit-learn model, exploring a CSV, or building deep networks), but often lack Kubernetes infrastructure intuition.

This mismatch causes three major platform inefficiencies:

* **Underprovisioning**: A user chooses Small for a data-intensive workload; the notebook runs briefly and then crashes with an Out-Of-Memory (OOM) error, losing unsaved progress.
* **Overprovisioning**: An idle or lightweight session reserves high CPU/RAM, reducing cluster schedulable capacity for other users.
* **Defensive Over-Requesting**: Users pick Large by default out of fear of OOM crashes, creating artificial resource contention and scheduling bottlenecks.

---

## Key Architecture & Implemented Features (Tasks A–F)

```mermaid
flowchart TD
    User([User / Data Scientist]) -->|1. Enters Intent, Dataset Size, Code| Form[Pre-Spawn Intent Form]
    Form -->|2. Requests Recommendation| Backend{Pluggable Recommender}
    
    subgraph Recommender Backends
        Backend -->|Deterministic Heuristic| RuleBased[Rule-Based Recommender]
        Backend -->|OpenAI-compatible HTTPS API| ExtLLM[External LLM: Gemini 1.5 Flash]
        Backend -->|Local HTTP Inference| SelfLLM[Self-Hosted LLM: Ollama / vLLM]
    end
    
    RuleBased & ExtLLM & SelfLLM -->|3. Validates Schema & Catalog| Validator[Policy Validator]
    Validator -->|4. Renders Preview| PreviewUI[Recommendation Preview UI]
    
    PreviewUI -->|Confirm / Accept| Hook[KubeSpawner Pre-Spawn Hook]
    PreviewUI -->|Edit Inputs| Form
    PreviewUI -->|Manual Override| Allowlist[Admin Allowlist Selector]
    Allowlist --> Hook
    
    Hook -->|5. Creates Configured Pod| UserPod[Kubernetes Notebook Pod]
    UserPod -.->|6. Workload Change / Re-provision| Reprovision[/hub/reprovision Endpoint]
    Reprovision -->|Retains PVC, Stops Pod| Hook
```

---

### Task A — Recommendation Preview UI

Replaces direct hardware guessing with an interactive confirmation flow:
* **Rich Inputs**: Captures natural language task intent, estimated dataset size (in GB), and lightweight code context (imports, API calls).
* **Transparent Recommendation Preview**: Presents the recommended hardware profile, software container image, and human-readable explanation reasons before pod creation.
* **User Agency**:
  * **Confirm / Accept**: Submits the approved profile and image to KubeSpawner.
  * **Edit Inputs**: Invalidates the current preview and allows re-entering workload parameters.
  * **Manual Override**: Allows selecting any administrator-allowlisted profile or image.
* **Structured Audit Logging**: Emits privacy-minimized structured `recommendation_audit` log events (recording event IDs, actions, policy versions, and override status) for platform evaluation without storing sensitive user code.

---

### Task B — Notebook Image Recommendation

Extends the recommender beyond CPU/memory to select an optimal software environment:
* **Admin-Controlled Image Catalog**: Pinned, immutable SHA-256 digests in [`recommender/image-catalog.yaml`](file:///Users/mthang1201/Documents/datn/intent-spawner/recommender/image-catalog.yaml) (e.g., `minimal-python`, `scipy-data-science`, `pytorch-deep-learning`, `tensorflow-deep-learning`).
* **Semantic & Capability Matching**: Matches workload keywords and imported libraries directly to image capabilities.
* **KubeSpawner Enforcement**: Dynamically sets `spawner.image` and writes metadata annotations (`z2jh-context-demo.local/applied-image`, `z2jh-context-demo.local/catalog-version`).

---

### Task C — Pluggable Recommender Framework

A modular, provider-neutral architecture (`recommender/`) that allows seamless switching among inference backends without modifying JupyterHub integration:

```text
RecommendationRequest
  -> Base Recommender (models.py, base.py, registry.py)
  -> Selected Backend Adapter (rule_based | external_llm | self_hosted_llm)
  -> Strict JSON Schema Validation (RESPONSE_SCHEMA)
  -> Policy & Catalog Validation (PolicyValidator)
  -> SpawnRecommendation Dataclass
```

#### 1. Rule-Based Recommender
* **Implementation**: [`recommender/rule_based.py`](file:///Users/mthang1201/Documents/datn/intent-spawner/recommender/rule_based.py)
* **Characteristics**: Zero-dependency, sub-millisecond, deterministic heuristic scoring. Serves as the baseline recommender and universal safety fallback.

#### 2. External LLM API (e.g., Google Gemini)
* **Implementation**: [`recommender/external_llm.py`](file:///Users/mthang1201/Documents/datn/intent-spawner/recommender/external_llm.py)
* **Characteristics**:
  * Connects to any OpenAI-compatible Chat Completions endpoint (such as Google Gemini's OpenAI compatibility layer at `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`).
  * Kubernetes Secret-managed API key (`EXTERNAL_LLM_API_KEY`) via `secretKeyRef`.
  * Configurable timeouts, total budget deadlines, and exponential backoff retries.
  * Strict JSON output enforcement and automatic fail-closed fallback to rule-based logic upon provider errors.

#### 3. Self-Hosted LLM (e.g., Local Ollama)
* **Implementation**: [`recommender/self_hosted_llm.py`](file:///Users/mthang1201/Documents/datn/intent-spawner/recommender/self_hosted_llm.py)
* **Characteristics**:
  * Connects to private inference engines (Ollama, vLLM, LocalAI) running locally or in-cluster.
  * Optional bearer authentication token (`SELF_HOSTED_LLM_API_KEY`).
  * Explicit `SELF_HOSTED_LLM_ALLOW_INSECURE_HTTP: "true"` flag for trusted in-cluster / host networks.
  * Reuses identical response parsing, catalog validation, and rule-based fallback safety mechanisms.

---

### Task D — Intent-Aware Re-Provisioning

Enables changing workload specifications after a session has already started:
* **Endpoint**: `/hub/reprovision`
* **Workflow**: Users describe their new workload and click **Preview replacement**. The UI highlights differences between current and proposed profiles/images along with an explicit restart warning.
* **Stop-and-Recreate Mechanics**: Gracefully stops the existing pod and launches a replacement pod with the new specification.
* **Storage Retention Boundary**: User files saved in `/home/jovyan` on the `PersistentVolumeClaim` (PVC) remain intact. Kernel memory, running terminal processes, and active in-memory variables are intentionally discarded (no fragile live migration).

---

### Task E — Policy-Bounded Dynamic Profile Generation (Stretch Goal)

An advanced opt-in mode that calculates fine-grained, continuous CPU/RAM/GPU resource allocations instead of discrete profile buckets:
* **Implementation**: [`recommender/dynamic_resources.py`](file:///Users/mthang1201/Documents/datn/intent-spawner/recommender/dynamic_resources.py) and [`helm/dynamic-values.yaml`](file:///Users/mthang1201/Documents/datn/intent-spawner/helm/dynamic-values.yaml).
* **Administrator Policy Guardrails**: Enforces minimum guarantees, maximum limits, step increments, and cluster quota headroom.
* **Fail-Safe Fallback**: Automatically reverts to safe Catalog Mode if proposed allocations violate policy constraints or exceed quota headroom.

---

### Task F — Evaluation Framework Redesign (Protocol v4)

A comprehensive evaluation suite designed for research rigor and thesis defense:
* **Implementation**: [`evaluation_v4/`](file:///Users/mthang1201/Documents/datn/intent-spawner/evaluation_v4/)
* **Bilingual 60-Intent Gold Standard**: 60 diverse workload intents in English and Vietnamese across Exploratory Data Analysis, Data Processing, Classical ML Training, and Deep Learning.
* **Multi-Recommender Benchmark Runner**: Evaluates Rule-Based, External LLM (Gemini), Self-Hosted LLM (Ollama), and Baseline heuristics under identical conditions.
* **Multi-Dimensional Metrics**:
  1. **Recommendation Quality**: Profile accuracy, image match accuracy, explainability score, inference latency, fallback rate.
  2. **System Effectiveness**: Schedulable capacity savings, resource allocation waste, OOM prevention rate, pending queue impact.
  3. **User Decision Impact**: Acceptance rate, manual override frequency, re-provisioning success rate.
* **Statistical Rigor**: Family-clustered bootstrap confidence intervals, Wilcoxon signed-rank tests, and strict claim gates distinguishing synthetic local runs from preserved Kubernetes cluster evidence.

---

## Setup & Deployment Guide

### 1. Prerequisites & Local Verification

Clone the repository and set up the isolated Python virtual environment:

```bash
git clone https://github.com/mthang1201/intent-spawner.git
cd intent-spawner

bash scripts/setup.sh
bash scripts/check.sh
```

Run all unit and integration tests:

```bash
.venv/bin/python -m pytest recommender/test_recommender.py \
  recommender/test_external_llm.py \
  recommender/test_self_hosted_llm.py \
  recommender/test_reliability.py \
  recommender/test_dynamic_resources.py \
  tests/test_helm_recommender_deployment.py \
  tests/test_recommender_backends_integration.py
```

---

### 2. Deploying the Interactive Demo

Deploy the proposed context-aware JupyterHub to a local disposable cluster (e.g., Minikube, Docker Desktop, k3d, kind):

```bash
# 1. Install proposed context-aware demo
bash scripts/install-proposed.sh

# 2. Start port forwarding (in a separate terminal)
bash scripts/port-forward.sh
```

Open `http://127.0.0.1:8000` in your browser. Log in with any username and password (`DummyAuthenticator`).

---

### 3. Configuring External LLM (Gemini API)

To use Google Gemini as the external recommendation engine:

#### Step 1: Create the Kubernetes Secret
Store your Gemini API key in Kubernetes without committing it to values files:

```bash
kubectl create secret generic intent-spawner-external-llm \
  --namespace=z2jh-context-demo \
  --from-literal=api-key="YOUR_GEMINI_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
```

#### Step 2: Deploy with Gemini Configuration
Use the prepared [`helm/gemini-values.yaml`](file:///Users/mthang1201/Documents/datn/intent-spawner/helm/gemini-values.yaml):

```bash
# Deploy dynamic package runtime
bash scripts/install-dynamic.sh

# Apply Gemini backend configuration
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

### 4. Configuring Self-Hosted LLM (Local Ollama)

To run recommendations using a locally hosted LLM without external network dependencies:

#### Step 1: Install and Start Ollama
```bash
# Install Ollama (macOS)
brew install ollama

# Start the Ollama server
ollama serve

# Pull your preferred model (in another terminal)
ollama pull llama3
```

#### Step 2: Deploy with Ollama Configuration
Because JupyterHub runs inside a container, connect to the host machine using `http://host.docker.internal:11434/v1/chat/completions` via [`helm/ollama-values.yaml`](file:///Users/mthang1201/Documents/datn/intent-spawner/helm/ollama-values.yaml):

```bash
# Deploy dynamic package runtime
bash scripts/install-dynamic.sh

# Apply Ollama backend configuration
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

### 5. Enabling Dynamic Resource Selection

To enable policy-bounded dynamic CPU/RAM/GPU allocation instead of discrete profile buckets:

```bash
bash scripts/install-dynamic.sh
```

---

## Repository Map

| Path | Purpose & Responsibility |
| --- | --- |
| `recommender/` | Core Python recommender framework: `base.py`, `registry.py`, `rule_based.py`, `external_llm.py`, `self_hosted_llm.py`, `dynamic_resources.py`, and `image-catalog.yaml`. |
| `helm/` | Helm configurations: `baseline-values.yaml`, `proposed-values.yaml`, `reprovision-values.yaml`, `dynamic-values.yaml`, `gemini-values.yaml`, and `ollama-values.yaml`. |
| `evaluation_v4/` | Protocol v4 evaluation suite: bilingual gold set (60 intents), multi-recommender benchmark runner, statistical analysis, and claim gates. |
| `scripts/` | Shell runbooks and cluster utilities: `install-proposed.sh`, `install-dynamic.sh`, `install-baseline.sh`, `port-forward.sh`, `check.sh`, `setup.sh`, `uninstall.sh`. |
| `workload/` | Bounded synthetic workloads mounted into notebook containers for testing and demonstration. |
| `benchmarks/` | Workload manifest and deterministic local workload runner. |
| `experiments/` | Local synthetic matrix runner, schema validation, and summary generators. |
| `cluster_evaluation/` | Single-node Kubernetes experiment execution, evidence harvesting, and cgroup-v2 validation. |
| `results/` | Preserved cluster experiment evidence, deployment rollout records, and audit snapshots. |
| `docs/` | Deep-dive design documents, data governance policies, threat models, and architectural specifications. |
| `tests/` | Pytest test suite covering all recommender backends, reliability layers, dynamic resources, and Helm templating. |

---

## Verification & Test Commands

Run the fast local checks and benchmarks:

```bash
# Full verification suite
bash scripts/check.sh

# Run Evaluation Protocol v4 gold-set validation and preview
make v4-validate

# Run local benchmark matrix dry-run
.venv/bin/python -m experiments.runner --full-matrix --dry-run --environment-id local-dry-run
```

---

## Research Scope, Limitations & Data Safety

* **Evaluation Boundaries**: Local synthetic benchmarks and single-node Minikube cluster evidence are distinct evidence classes. Local process memory measurements must not be equated to Kubernetes pod cgroup limits.
* **Privacy & Data Governance**: The pre-spawn hook and audit log record only derived features, resource profiles, and action tags (`accept`, `override`). User notebooks, dataset contents, raw code files, and credentials are never stored.
* **GPU Hardware**: While deep learning profiles and GPU image recommendations (`pytorch-deep-learning`, `tensorflow-deep-learning`) are fully modeled, physical GPU device scheduling requires an authorized hardware pool.

---

## Cleanup

To completely remove all demo Kubernetes resources:

```bash
bash scripts/uninstall.sh
```

---

## Documentation Index

* [Getting Started Guide](docs/GETTING_STARTED.md)
* [Architecture Guide](docs/ARCHITECTURE.md)
* [Demo Presentation Runbook](DEMO_SCRIPT.md)
* [External LLM Recommender Specification](docs/EXTERNAL_LLM_RECOMMENDER.md)
* [Self-Hosted LLM Recommender Specification](docs/SELF_HOSTED_LLM_RECOMMENDER.md)
* [Intent-Aware Re-Provisioning Design](docs/INTENT_AWARE_REPROVISIONING.md)
* [Dynamic Profile Generation Specification](docs/DYNAMIC_PROFILE_GENERATION.md)
* [Production Helm Deployment Wiring](docs/HELM_BACKEND_DEPLOYMENT.md)
* [Evaluation Protocol v4 Specification](docs/evaluation/EVALUATION_V4_PROTOCOL.md)
* [Recommendation Preview Design](docs/evaluation/RECOMMENDATION_PREVIEW_DESIGN.md)
* [Threats to Validity](docs/evaluation/THREATS_TO_VALIDITY.md)
* [Data Governance Policy](docs/DATA_GOVERNANCE.md)
* [Cleanup Runbook](CLEANUP.md)
