# Graduation Internship Scope & Project Demarcation

This document establishes the formal demarcation of scope between the **Graduation Internship Proof-of-Concept Prototype** (~50% milestone) and the planned future **Graduation Thesis** (Đồ án tốt nghiệp, 100% full research scope).

---

## 1. Project Background & Internship Objectives

Selecting computational environments in JupyterHub on Kubernetes (clusters running Zero to JupyterHub / KubeSpawner) remains a manual, error-prone task for data scientists and students. Users are forced to guess hardware profiles (CPU, RAM, GPU) and container images without clear mapping from their workload requirements.

The **Graduation Internship (Thực tập tốt nghiệp)** focuses on developing a functional, self-contained **Proof-of-Concept (PoC) Prototype** called **Intent-Spawner**. The project proves the feasibility of intent- and context-aware environment recommendation using deterministic extraction and hybrid retrieval while maintaining strict administrator policy boundaries.

### Core Internship Objective:
Deliver a reliable, fully testable, and locally deployable system that:
1. Extracts structured workload specifications from natural language text and code context without mandatory external cloud API dependencies.
2. Performs policy-constrained hybrid retrieval (BM25 sparse + FastEmbed dense vector retrieval merged via Reciprocal Rank Fusion).
3. Applies deterministic hard technical constraints (hardware quotas, GPU requirements, library availability) against an administrator-allowlisted catalog.
4. Integrates seamlessly with JupyterHub via an explainable UI preview that lets users inspect, confirm, or manually override selections before pod spawning.
5. Provides reproducible offline evaluation (E1 Quality, E5 Image storage & functional probes) and automated test suites.

---

## 2. Scope Demarcation: Internship vs. Graduation Thesis

The table below delineates the deliverables completed for the **Graduation Internship** versus the extended research roadmap planned for the **Graduation Thesis**:

| Dimension | Graduation Internship Scope (~50% Milestone) | Future Graduation Thesis Scope (100% Full Scope) |
| :--- | :--- | :--- |
| **Primary Artifact** | Working Proof-of-Concept (PoC) Prototype & offline evaluation harness. | Comprehensive research dissertation, formal multi-experiment defense, and statistical validation. |
| **Intent Extraction** | **P2 Local (Core):** Deterministic schema-validated regex extractor. Zero external API cost, ultra-low latency (<1 ms), fully offline. | **P2 LLM vs. Local:** Comparative benchmarking with external LLM extractors (GPT-4o, Gemini), prompt engineering, and semantic ambiguity handling. |
| **Retrieval Engine** | **Hybrid Sparse + Dense:** BM25 lexical retrieval + FastEmbed (`bge-small-en-v1.5`) dense retrieval combined via Reciprocal Rank Fusion (RRF). | Advanced rank fusion parameter optimization, ablation studies on sparse vs. dense components, exploration of reranking boundaries. |
| **Safety & Policy** | **Deterministic Hard Constraints:** Pre-spawn filtering based on CPU, RAM, GPU availability, dataset volume, and forbidden libraries. | Dynamic policy admission, organizational multi-tenant quota negotiation, and security auditing of user-supplied code context. |
| **JupyterHub Integration** | Custom Spawner profile form with workload intent input, asynchronous recommendation preview, explanation display, and manual override. | Continuous policy-bounded dynamic resource sizing (`helm/dynamic-values.yaml`) and storage-preserving live session reprovisioning (`/hub/reprovision`). |
| **Deployment Target** | Single-node / local Kubernetes cluster (OrbStack, Minikube, Docker Desktop, k3d) via Helm chart. | Production-grade multi-node Kubernetes cluster with heterogeneous node pools (GPU/CPU nodes) and autoscaling. |
| **Evaluation Suite** | **Focused Offline & Local Evaluation:**<br>• **E1 (Offline Quality):** Benchmark of P1 vs. P2 Local on development dataset.<br>• **E5 (Image Storage & Probes):** Catalog layer deduplication analysis and functional container probes.<br>• **Core Unit Tests:** Recommender and constraint test suites (`make test`). | **Full Protocol-v5 Empirical Suite:**<br>• **E1:** Full offline quality with local & LLM extractors.<br>• **E2:** Natural language perturbation robustness.<br>• **E3:** Human usability study (SUS, completion time, N≥12).<br>• **E4:** Multi-pod live cluster resource envelope & efficiency.<br>• **Cross-experiment Claim Synthesis.** |

---

## 3. Milestone Summary: Internship Deliverables

The Graduation Internship phase successfully delivers:

1. **Recommender Core Engine (`recommender/`):**
   - Local regex-based intent extractor (`recommender/intent_extractor.py`).
   - BM25 sparse retriever (`recommender/hybrid_retrieval.py`).
   - FastEmbed dense vector retriever with pre-indexed catalog embeddings.
   - Reciprocal Rank Fusion (RRF) rank aggregation.
   - Deterministic constraint evaluator enforcing hardware limits and image compatibility (`recommender/constraint_evaluator.py`).

2. **JupyterHub Helm Integration (`helm/` & `scripts/`):**
   - Configured KubeSpawner custom options form for workload descriptions.
   - Microservice backend deployment (`recommender-p2-values.yaml`).
   - Automated local cluster install and tear-down scripts (`scripts/install-proposed.sh`, `scripts/install-baseline.sh`).

3. **Offline Evaluation Framework (`evaluation_v5/` & `tests/`):**
   - Recommendation quality benchmark runner (`make eval-offline-local`).
   - Image layer sharing and functional probe evaluation (`evaluation_v5.image_storage`).
   - Unit test suite verifying hybrid retrieval and constraint enforcement (`make test`).

---

## 4. Transitioning to the Graduation Thesis

The components developed during the internship provide the empirical and architectural foundation for the Graduation Thesis. The subsequent thesis work will expand into:
- Conducting the formal human user study (Experiment E3) comparing manual baseline (B0) against intent-driven selection (P2).
- Validating physical cluster efficiency and node packing on a multi-node Kubernetes testbed (Experiment E4).
- Analyzing natural language robustness against adversarial perturbations and typo noise (Experiment E2).
- Finalizing the full academic dissertation and peer review publications.
