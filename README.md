# Intent- and Context-Aware Profile Recommendation for JupyterHub
### Graduation Internship Proof-of-Concept (PoC) Prototype

This repository hosts the **Graduation Internship Proof-of-Concept (PoC) Prototype** for intent- and context-aware computational environment and resource profile selection on JupyterHub (Kubernetes/KubeSpawner).

The prototype addresses the manual configuration barrier in JupyterHub by mapping natural-language workload descriptions and optional code context to appropriate, administrator-allowlisted runtime profiles and container images. The proposed method combines structured intent extraction with policy-constrained hybrid retrieval (BM25 sparse + FastEmbed dense vector retrieval merged via Reciprocal Rank Fusion) and deterministic policy/hardware constraints. Before pod creation, users review an explainable recommendation preview with the option to accept or manually override the selection.

For details on the project scope division, see [**Graduation Internship Scope & Demarcation**](docs/INTERNSHIP_SCOPE.md).

## System Architecture & Methods

The prototype establishes a progression of recommendation mechanisms, categorizing them into the **Graduation Internship Core** and **Extended / Thesis Roadmap Preview**:

### Graduation Internship Core Systems

| System | Key Components | Implementation & Role |
| :--- | :--- | :--- |
| **B0 Baseline** | Manual selection | Default JupyterHub static hardware tiers (Small / Medium / Large). Baseline comparator for configuration friction. |
| **P1 Heuristic** | Keyword rules & multipliers | Deterministic rule-based profile recommender. Fast, zero-dependency baseline comparator for offline benchmarks. |
| **P2 Local** | **Regex Extractor + BM25 & FastEmbed Hybrid Retrieval + Hard Constraints** | **Core Proposed Method (Main Internship Deliverable)**: Self-contained offline hybrid retrieval (sparse BM25 + dense vector search via RRF) with strict deterministic hardware and policy constraints. Zero external API cost, ~1ms latency. |

### Extended / Thesis Roadmap Preview (Experimental Features)

| Feature / System | Description | Roadmap Role |
| :--- | :--- | :--- |
| **P2 LLM** | External LLM structured intent extractor (OpenAI / Gemini) + Hybrid Retrieval. | Evaluates semantic nuance against noisy natural language; preview for extended thesis evaluation. |
| **Dynamic Sizing** | Continuous policy-bounded CPU/RAM rightsizing (`helm/dynamic-values.yaml`). | Fine-grained resource envelope generation previewing future scheduler-level rightsizing. |
| **Reprovisioning** | Storage-preserving notebook reprovisioning with PVC retention (`/hub/reprovision`). | Dynamic workload switching without data loss across notebook lifecycles. |

## Internship Prototype Verification

Dành cho giảng viên và người chấm thẩm định nhanh sản phẩm Thực tập tốt nghiệp (nhánh `adapt_thesis_to_internship`):

```bash
# 1. Chạy unit test cốt lõi của P2 (Backend integration, Structured intent, Constraint evaluator, Hybrid retrieval)
make internship-test
# hoặc: bash scripts/test-internship.sh

# 2. Chạy nhanh đánh giá offline Stream E1 (P1 Heuristic vs. P2 Local) và kiểm tra schema kết quả
make internship-eval
# hoặc: bash scripts/eval-internship.sh

# 3. Tự động biên dịch tài liệu báo cáo ra file docs/latex/main.pdf bằng lualatex và bibtex
make internship-build-doc
# hoặc: bash scripts/build-internship-doc.sh

# 4. (Tùy chọn) Chạy trọn gói cả test và eval trong 1 câu lệnh duy nhất:
make internship-verify
# hoặc: bash scripts/verify-internship.sh
```

### Chi tiết các Make Targets kiểm thử:
- **`make internship-test`**: Thực thi 96 unit test cốt lõi bao phủ trọn vẹn thành phần đề xuất P2: tích hợp backend P2, trích xuất intent có cấu trúc (`StructuredIntentExtractor`), bộ đánh giá ràng buộc cứng/mềm (`ConstraintEvaluator`), và bộ máy truy hồi kết hợp BM25 + Dense semantic vector retrieval (`HybridRetriever`).
- **`make internship-eval`**: Chạy đánh giá offline E1 trên tập development split (83 test cases tiếng Anh & tiếng Việt, 166 lượt chạy), so sánh độ chính xác giữa P1 Heuristic và P2 Local, sau đó tự động xác thực fail-closed schema và tính toàn vẹn của artifacts tại `results_v5/protocol-v5.0.0/E1/run-e1-local`. Target này không chạy các benchmark nặng 800 trials của E4 nên hoàn tất chỉ trong ~10 giây.
- **`make internship-build-doc`**: Tự động thực thi pipeline biên dịch tài liệu LaTeX (`lualatex` $\rightarrow$ `bibtex` $\rightarrow$ `lualatex` $\times 2$), dọn dẹp các tệp phụ trợ tạm thời để tránh xung đột counter, và xuất ra file báo cáo chính thức `docs/latex/main.pdf` hoàn chỉnh mục lục, danh mục bảng biểu và tài liệu tham khảo.

## Quickstart

### 1. Local Environment Setup & Software Tests

From a local checkout:

```bash
# 1. Setup Python virtual environment & dependencies
bash scripts/setup.sh

# 2. Run core unit tests for recommender logic & constraints
make test

# 3. Test standalone recommender directly from CLI
.venv/bin/python -m recommender.recommender \
  --intent "Train a scikit-learn random forest model on 1.5GB tabular dataset" \
  --dataset-gb 1.5
```

### 2. Core Offline Evaluation (E1 Quality & E5 Image Storage)

Run offline evaluations locally without external API dependencies or cluster setup:

```bash
# E1: Offline Recommendation Quality Benchmark (P1 vs. P2 Local)
make eval-offline-local

# E5: Image Storage & Layer Deduplication Analysis
.venv/bin/python -m evaluation_v5.image_storage --experiment storage --mode docker --stage development

# E5: Container Image Functional Probes (using E1 local recommendations)
.venv/bin/python -m evaluation_v5.image_storage --experiment functional \
  --recommendation-run results_v5/protocol-v5.0.0/E1/run-e1-local \
  --mode docker --pull-policy missing

# E2: Natural Language Robustness Analysis (Optional local summary)
make eval-robustness

# Interactive multi-experiment runner (E1–E5)
make experiments
# or directly: python scripts/run_experiments.py
```

### 3. Local Interactive Demo Deployment (Kubernetes)

Deploy and switch between core methods on a local disposable Kubernetes cluster (namespace `z2jh-context-demo`):

```bash
# Baseline (B0: Manual Small / Medium / Large selection)
bash scripts/install-baseline.sh

# Heuristic Recommender (P1: Fast rule-based baseline)
bash scripts/install-proposed.sh

# Core Proposed Method (P2 Local: Local regex extractor + hybrid retrieval)
BACKEND_VALUES=helm/recommender-p2-values.yaml bash scripts/install-proposed.sh

# Port-forward to access the JupyterHub UI at http://127.0.0.1:8000
bash scripts/port-forward.sh
```

#### Extended / Thesis Roadmap Preview Deployment (Optional):

```bash
# P2 LLM (Hybrid method with external LLM structured intent extractor)
BACKEND_VALUES=helm/recommender-p2-llm-values.yaml bash scripts/install-proposed.sh

# Dynamic Sizing (Continuous CPU/RAM sizing with P2 backend)
BACKEND_VALUES=helm/recommender-p2-values.yaml bash scripts/install-dynamic.sh
```

| Deployment Target | Command | Scope Category |
| :--- | :--- | :--- |
| **B0 Baseline** | `bash scripts/install-baseline.sh` | **Internship Core** |
| **P1 Heuristic** | `bash scripts/install-proposed.sh` | **Internship Core** |
| **P2 Local** | `BACKEND_VALUES=helm/recommender-p2-values.yaml bash scripts/install-proposed.sh` | **Internship Core (Proposed)** |
| **P2 LLM** | `BACKEND_VALUES=helm/recommender-p2-llm-values.yaml bash scripts/install-proposed.sh` | Extended Preview (Thesis) |
| **P1 Dynamic** | `bash scripts/install-dynamic.sh` | Extended Preview (Thesis) |
| **P2 Dynamic** | `BACKEND_VALUES=helm/recommender-p2-values.yaml bash scripts/install-dynamic.sh` | Extended Preview (Thesis) |

## Documentation

- [**Internship Scope & Demarcation**](docs/INTERNSHIP_SCOPE.md): Formal definition of the Graduation Internship PoC scope (~50%) vs. future Graduation Thesis.
- [**Getting Started Guide**](docs/GETTING_STARTED.md): Step-by-step local setup, offline evaluation, and demo deployment.
- [**System Architecture**](docs/ARCHITECTURE.md): End-to-end design, P2 hybrid retrieval pipeline, constraint evaluation, and preview state machine.
- [**Experiment Reproduction Guide**](docs/PROTOCOL_V5_EXPERIMENT_GUIDE.md): Protocol-v5 experiment specifications and runbooks.
- [**Evaluation Results & Evidence**](results_v5/README.md): Tracked experimental evidence for E1 (Quality), E2 (Robustness), E3 (User Study), E4 (Resources), E5 (Images), and Cross-Experiment Synthesis.
- [**Demo Walkthrough Script**](docs/DEMO_SCRIPT.md): Step-by-step presentation script for live interactive demonstrations.
- [**Artifact & Documentation Index**](docs/ARTIFACT_MANIFEST.md): Comprehensive inventory of all technical documents and research artifacts.
