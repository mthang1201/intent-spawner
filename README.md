# Intent- and Context-Aware Profile Recommendation for JupyterHub (Intent-Spawner)

An end-to-end research prototype and evaluation framework for **intent- and context-aware resource profile recommendation in JupyterHub**. 

Users express their analytical goals in natural language (English or Vietnamese) or provide notebook context; the system infers compute requirements, previews an administrator-allowlisted container image and hardware profile, and safely provisions the pod via KubeSpawner with zero-touch or human-in-the-loop confirmation. It also supports notebook reprovisioning with persistent PVC retention and opt-in dynamic resource sizing.

---

## 🏛️ Evaluated Systems

| System | Mode | Description | Research Role |
| :--- | :--- | :--- | :--- |
| **B0** | Manual Static | Standard JupyterHub dropdown selection (Small / Medium / Large static sizing). | Baseline comparator for human study (E3) and cluster simulation (E4). |
| **P1** | Rule-Based | Deterministic keyword/heuristic profile and image matcher. | Zero-dependency baseline comparator for E1/E2. |
| **P2 Local** | NLP Hybrid | **Proposed Method (Offline)**: Domain ontology + bilingual intent parser + BM25 & dense retrieval + deterministic constraint filtering. Zero API cost, ~1ms latency. | Primary proposed system evaluated across E1–E5. |
| **P2 LLM** | LLM Hybrid | **Proposed Method (Semantic)**: Few-shot LLM semantic extractor (Gemini / OpenAI / Ollama) + hybrid retrieval + constraint filtering. | Evaluates semantic extraction under complex / ambiguous phrasing. |

---

## 🚀 Quickstart: Prerequisites & Installation

### 1. Requirements

- **Operating System:** Linux or macOS (Apple Silicon & Intel supported)
- **Python:** 3.10 or higher (3.11+ recommended)
- **Container / Cluster (Optional):** Docker or local Kubernetes (OrbStack, Kind, Docker Desktop, Minikube, K3s) if deploying the Helm chart or executing physical cluster probes. All algorithmic evaluations (E1, E2, large cluster simulation) run completely offline without Kubernetes.

### 2. Setup Local Environment

```bash
# Clone repository
git clone https://github.com/mthang1201/intent-spawner.git
cd intent-spawner

# Run automated virtual environment setup and dependency installation
bash scripts/setup.sh

# Activate virtual environment
source .venv/bin/activate

# Verify core unit tests
make test
```

---

## 🛠️ Installing & Testing B0, P1, and P2

You can test and deploy the systems in two ways:

### Method A: Local Python & CLI Testing (No Kubernetes Required)

Test the recommender adapters and extractors directly in Python:

```bash
# Test P1 and P2 recommenders locally via the evaluation suite
PYTHONPATH=. .venv/bin/python scripts/evaluate_baseline_benchmark.py --help

# Test standalone recommender service logic
PYTHONPATH=. .venv/bin/pytest tests/test_p2_backend_integration.py tests/test_single_input_workflow.py
```

### Method B: Deploying on Local Kubernetes (Helm & JupyterHub)

To deploy or switch methods on a running local Kubernetes cluster (namespace `z2jh-context-demo`):

```bash
# 1. Baseline (B0: Small / Medium / Large static manual selection)
bash scripts/install-baseline.sh

# 2. Heuristic Recommender (P1: Fast rule-based matcher)
bash scripts/install-proposed.sh

# 3. Proposed Method (P2 Local: Bilingual ontology + hybrid retrieval)
BACKEND_VALUES=helm/recommender-p2-values.yaml bash scripts/install-proposed.sh

# 4. Proposed Method (P2 LLM: External LLM semantic extractor)
BACKEND_VALUES=helm/recommender-p2-llm-values.yaml bash scripts/install-proposed.sh

# Access JupyterHub UI at http://127.0.0.1:8000
bash scripts/port-forward.sh
```

---

## 📊 Benchmark Datasets & Generation Pipeline

### 1. Upstream Data Sources

The evaluation benchmark is constructed from four diverse real-world datasets:

1. **DS-1000** ([arXiv:2211.11501](https://arxiv.org/abs/2211.11501)): 1,000 data science coding tasks spanning 7 libraries (`pandas`, `numpy`, `scipy`, `matplotlib`, `scikit-learn`, `pytorch`, `tensorflow`).
2. **ARCADE** ([arXiv:2212.09248](https://arxiv.org/abs/2212.09248)): Real-world multi-turn Jupyter notebook natural language intents, supplemented with conversational Vietnamese translations.
3. **DistilKaggle** ([Zenodo:10317389](https://zenodo.org/records/10317389)): ~542k Kaggle notebooks used to evaluate real-world vocabulary overlap and domain retrieval.
4. **Alibaba PAI GPU Cluster Trace** ([alibaba/clusterdata](https://github.com/alibaba/clusterdata)): Multi-tenant GPU cluster traces used for discrete-event multi-tenancy simulation.

### 2. Empirical Ground-Truth Labeling via Cgroups v2

Ground-truth resource profiles are **never annotated subjectively**. Instead, reference solutions are profiled inside isolated containers under Linux **cgroups v2** (`scripts/datasets/profile_workloads.py`), capturing peak anonymous memory (`anon_peak_mib`). With a $1.35\times$ safety headroom factor, workloads are assigned to the minimal safe tier:

- **Small** (1 core, 384 MiB RAM): Peak anon memory $< 260\text{ MiB}$.
- **Medium** (2 cores, 1,024 MiB RAM): Peak anon memory $260\text{ MiB} \le M < 730\text{ MiB}$.
- **Large** (4 cores, 2,048 MiB RAM): Peak anon memory $730\text{ MiB} \le M < 1,490\text{ MiB}$.

Class balance is strictly enforced: no single image or profile tier exceeds $50\%$ of the benchmark, preventing majority-class shortcut learning.

### 3. Pre-Compiled Benchmark v1 (`data/benchmark/v1/`)

The repository includes the standardized, leak-free Benchmark v1 split:

- `data/benchmark/v1/train.jsonl` (70% stratified training split)
- `data/benchmark/v1/dev.jsonl` (15% validation split)
- `data/benchmark/v1/test.jsonl` (15% confirmatory evaluation split)
- `data/benchmark/v1/manifest.json` (Cryptographic SHA-256 digests and distribution stats)
- `data/benchmark/v1/SCHEMA.md` (Detailed schema specification)

Zero data leakage between train and test splits is guaranteed by connected-component family clustering.

### 4. How to Regenerate or Extend Benchmark Data

To re-run the dataset preparation and compilation pipeline from raw sources:

```bash
# 1. Fetch & parse ARCADE intents and create Vietnamese variants
python scripts/datasets/fetch_arcade_benchmark.py
python evaluation_v5/dataset_pipeline/convert_arcade_to_gold_variants.py

# 2. Extract DS-1000 and map problems to allowlisted container images
python scripts/datasets/extract_ds1000.py
python scripts/datasets/map_ds1000_images.py

# 3. Stratified sampling with 50% image cap
python scripts/datasets/sample_ds1000_stratified.py

# 4. Measure cgroup v2 peak memory & assign empirical ground-truth labels
python scripts/datasets/profile_workloads.py

# 5. Compile unified gold dataset & generate leak-free train/dev/test splits
python evaluation_v5/dataset_pipeline/compile_protocol_v5_gold.py
python evaluation_v5/dataset_pipeline/split_unified_benchmark.py
```

---

## 🧪 Running Experiments (E1, E2, E4, E5)

You can launch experiments via the unified interactive CLI or standalone commands.

```bash
# Interactive Master Experiment Menu (E1–E5, local/LLM selection, reports):
make experiments
# or directly:
python scripts/run_experiments.py
```

### Experiment E1: Recommendation Quality (P1 vs P2)

Evaluates accuracy, acceptability, and safety (under-provisioning OOM risk vs over-provisioning resource waste) on `data/benchmark/v1/test.jsonl`:

```bash
# Run P1 and P2 Local (Deterministic, zero API cost, ~1ms per sample):
make eval-offline-local
# or directly:
PYTHONPATH=. .venv/bin/python scripts/evaluate_baseline_benchmark.py \
  --test-file data/benchmark/v1/test.jsonl \
  --output-dir results_v5/baseline_benchmark_v1 \
  --extractor-mode local

# Run P2 LLM Mode (prompts for API key and model if not set in environment):
make eval-offline-llm
```

### Experiment E2: Natural Language Robustness

Evaluates recommender stability under colloquial phrasing, typos, syntactic paraphrasing, and cross-lingual English vs Vietnamese prompts across 68 variants:

```bash
make eval-robustness
# or:
python scripts/run_experiments.py --experiment e2
```

### Experiment E4: Resource Sizing & Multi-Tenancy Cluster Simulation

#### Option 1: Large-Scale Discrete-Event Cluster Simulator (No Kubernetes required)

Simulates 50 Kubernetes worker nodes (1,000 vCPU, 2,000 GB RAM, 100 GPUs) under empirical Alibaba PAI workload arrivals, comparing B0 (manual static large), P1 (rule-based), and P2 (Intent-Spawner):

```bash
# Run full multi-tenancy discrete-event simulation
make simulate-large-cluster
# or:
PYTHONPATH=. .venv/bin/python scripts/simulate_large_cluster.py

# Run FIFO queue trace simulator with 30-seed bootstrap confidence intervals
PYTHONPATH=. .venv/bin/python -m evaluation_v5.analysis.cluster_trace_simulator \
  --output-json results_v5/cluster_simulation_results.json \
  --output-csv results_v5/cluster_simulation_summary.csv
```

#### Option 2: Physical Kubernetes Pod Measurement (Requires local K8s cluster)

```bash
# Check cluster connectivity and readiness
make v5-e4-preflight

# Execute physical container workload trials
PYTHONPATH=. .venv/bin/python -m evaluation_v5.resource execute \
  --result-dir results_v5/protocol-v5.0.0/E4/run-e4-observed \
  --run-id run-e4-observed \
  --image intent-spawner-resource-v5:latest \
  --readiness-attestation benchmarks_v5/protocol-v5-e4-readiness-attestation-orbstack.json
```

### Experiment E5: Container Image Storage & Functional Validation

Measures image layer deduplication, pull latencies, and validates container startup functionality with library probes (`python`, `torch`, `cuda`, `tensorflow`):

```bash
# Dry-run mode (runs on any laptop without Docker daemon):
PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage --experiment storage --mode dry-run --stage development
PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage --experiment functional --recommendation-run results_v5/protocol-v5.0.0/E1/run-e1-local --mode dry-run

# Physical Docker / OrbStack mode (spawns containers and executes probes):
PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage --experiment storage --mode docker --stage development
PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage --experiment functional --recommendation-run results_v5/protocol-v5.0.0/E1/run-e1-local --mode docker --pull-policy missing
```

---

## 📈 Viewing & Interpreting Experiment Results

After running experiments, results, metrics, and visualization artifacts are automatically compiled into `results_v5/`:

### Artifact Map

| Experiment | Key Output Path | Description |
| :--- | :--- | :--- |
| **E1 Baseline Benchmark** | [`results_v5/baseline_benchmark_v1/SUMMARY.md`](results_v5/baseline_benchmark_v1/SUMMARY.md) | Formatted Markdown evaluation report of P1 vs P2 on Benchmark v1. |
| **E1 Metrics JSON** | [`results_v5/baseline_benchmark_v1/metrics.json`](results_v5/baseline_benchmark_v1/metrics.json) | Full machine-readable metrics (Acc@1, Acc@3, Acceptability, Under/Over-provisioning). |
| **E1 Confusion Plots** | `results_v5/baseline_benchmark_v1/confusion_p1.png`<br>`results_v5/baseline_benchmark_v1/confusion_p2.png` | Visual confusion matrices for Small, Medium, and Large profile prediction. |
| **E1 Predictions** | [`results_v5/baseline_benchmark_v1/predictions.jsonl`](results_v5/baseline_benchmark_v1/predictions.jsonl) | Per-sample model predictions and ground-truth comparisons. |
| **E2 Robustness** | [`results_v5/protocol-v5.0.0/E2/run-e2-local/ROBUSTNESS_REPORT.md`](results_v5/protocol-v5.0.0/E2/run-e2-local/ROBUSTNESS_REPORT.md) | Linguistic variant and multilingual (EN vs VI) robustness report. |
| **E4 Cluster Simulation** | [`results_v5/cluster_simulation_summary.csv`](results_v5/cluster_simulation_summary.csv)<br>[`results_v5/cluster_simulation_results.json`](results_v5/cluster_simulation_results.json) | Multi-seed throughput, queuing delay, OOM cascades, and resource utilization. |
| **E4 Simulation Figure** | `docs/latex/figures/figure_large_cluster_simulation.pdf` | Publication-ready multi-panel figure comparing B0, P1, and P2 cluster dynamics. |
| **E4 Resource Report** | [`benchmarks_v5/oracle/report/E4_RESOURCE_REPORT.md`](benchmarks_v5/oracle/report/E4_RESOURCE_REPORT.md) | Physical cgroup v2 envelope analysis and telemetry summaries. |
| **E5 Storage & Probes** | `results_v5/protocol-v5.0.0/E5/run-e5-storage-orbstack/`<br>`results_v5/protocol-v5.0.0/E5/run-e5-functional-orbstack/` | Layer deduplication profiles and container probe execution outcomes. |

### Automated Report Synthesis

To compile the cross-experiment thesis claim verification matrix and export LaTeX tables:

```bash
# Generate comprehensive thesis evidence report:
make report

# Adjudicate formal research claims (H1–H8) across all experiments:
make research-analysis
```

---

## 📚 Complete Documentation Index

- [**Getting Started Runbook**](docs/GETTING_STARTED.md): Step-by-step installation, test suites, and troubleshooting.
- [**Protocol-v5 Experiment Guide**](docs/PROTOCOL_V5_EXPERIMENT_GUIDE.md): In-depth execution guide and parameters for E1–E5.
- [**Dataset Standardization Report**](docs/reports/DATASET_STANDARDIZATION_REPORT.md): Provenance, cgroups v2 methodology, and verification audit.
- [**Benchmark v1 Schema**](data/benchmark/v1/SCHEMA.md): Formal data fields and definitions for the evaluation split.
- [**System Architecture**](docs/ARCHITECTURE.md): Recommender design, preview state machine, and hybrid retrieval index.
- [**Cluster Simulation Assumptions**](docs/reports/cluster_simulation_assumptions.md): Methodology for PAI trace scaling and discrete-event queuing.
- [**Interactive Demo Script**](docs/DEMO_SCRIPT.md): Walkthrough script for live demonstrations.
