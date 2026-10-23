# Streamlined Experiment Runbook (Protocol-v5)

This document provides the streamlined experimental execution guide for **Intent- and Context-Aware Profile Recommendation for JupyterHub (Intent-Spawner)**.

All legacy administrative gatekeeping (mandatory reviewer sign-offs, immutable SHA-256 seal invariants, synthetic split checks, and over-engineered hypothesis testing) has been simplified to directly serve the engineering and evaluation objectives of the Graduation Thesis.

---

## ⚡ Quick Command Cheat Sheet

| Task | Execution Command | Expected Output |
| :--- | :--- | :--- |
| **Interactive Experiment Menu (E1-E5)** | `make experiments` or `python scripts/run_experiments.py` | Interactive CLI menu for E1..E5, mode selection (`local`/`llm`), automatic API key prompt |
| **Core Logic Unit Tests** | `make test` | 80/80 unit tests pass (0.6s) |
| **E1 Benchmark v1 Evaluation** | `make eval-offline-local` | Evaluates P1 and P2 on `data/benchmark/v1/test.jsonl`, saves to `results_v5/protocol-v5.0.0/E1/run-e1-local` |
| **E1 LLM Mode (Semantic extractor)** | `make eval-offline-llm` | Prompts for API key; saved to `results_v5/.../E1/run-e1-llm-<model-slug>` |
| **Large-Scale Cluster Simulation (E4)** | `make simulate-large-cluster` | 50-node discrete-event cluster simulation from Alibaba PAI trace; saves figure & metrics |
| **Cluster Trace FIFO Simulation (E4)** | `PYTHONPATH=. .venv/bin/python -m evaluation_v5.analysis.cluster_trace_simulator` | 30-seed bootstrap evaluation; exports CSV & JSON to `results_v5/` |
| **Run with Custom Dataset** | `make eval-dataset DATASET=path/to/dataset.yaml` | Evaluation report on the specified benchmark dataset |
| **Natural Language Robustness Overview (E2)** | `make eval-robustness` | Analysis report on 68 variants and cross-lingual robustness |
| **User Study Smoke Test (E3)** | `make v5-user-study-smoke` | Verifies harness integrity and counterbalanced Latin square allocation flow |
| **Kubernetes Cluster Preflight Check (E4)** | `make v5-e4-preflight` | Confirms Kubernetes cluster readiness (OrbStack/Kind/K3s/Minikube) |
| **E5 Image Storage Measurement (Storage)** | `PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage --experiment storage --mode docker --stage development` | OCI layer deduplication and storage footprint report |
| **E5 Image Functional Verification (Functional)** | `PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage --experiment functional --recommendation-run results_v5/protocol-v5.0.0/E1/run-e1-local --mode docker --pull-policy missing` | Container startup and core library probe verification results |

---

## 🔬 Experiment Details & Mode Selection (Local vs LLM)

The system supports two intent extraction modes for P2:
* **`local` (Default):** Deterministic parser based on cross-lingual rules and domain ontology. Operates completely offline, zero token cost, ultra-low latency (~1ms). Output saved under `run-*-local`.
* **`llm`:** Calls a large language model (OpenAI / Ollama / vLLM / LiteLLM) to extract semantic intent. If the environment variable lacks an API key, the script will **automatically prompt the user to paste the API key directly in the terminal**.
* **Isolated Storage Partitions by Model (`run-*-llm-<model-slug>`):** Results from different LLM models (e.g., `gpt-4o-mini`, `gpt-4o`, `llama3.1`, `qwen2.5-coder`) are stored in distinct directories (such as `run-e1-llm-gpt-4o-mini`, `run-e1-llm-llama3.1`) to prevent accidental overwrites and provenance hash collisions.
* **Real-time Progress Bar & Heartbeat:** All experiment runners display a real-time progress bar with Elapsed time, estimated completion (ETA), and periodic heartbeats every 30-60 seconds to ensure the terminal remains responsive during network requests.

### 1. Experiment E1 — Recommendation Quality (P1 vs P2)
Evaluates the capability of translating user intent into appropriate hardware sizing (Small, Medium, Large) and container environment images.

```bash
# Option 1: Use the master interactive CLI menu
python scripts/run_experiments.py --experiment e1

# Option 2: Run directly in Local Mode
make eval-offline-local
# (Results saved to: results_v5/protocol-v5.0.0/E1/run-e1-local)

# Option 3: Run directly in LLM Mode (prompts for API key and model config if unset)
make eval-offline-llm
# (Results saved to: results_v5/protocol-v5.0.0/E1/run-e1-llm-<model-slug>)
```

* **Evaluation Metrics:**
  * **Top-1 Profile Match:** Accuracy of recommending the correct compute tier.
  * **Image Match:** Accuracy of recommending the target notebook image (Data Science, PyTorch, SciPy...).
  * **Joint Match:** Accuracy of predicting both Profile and Image correctly simultaneously.
  * **Median Latency:** Algorithm response latency (ms).
  * **Language Breakdown (English vs Vietnamese):** Accuracy split across English and Vietnamese prompts.
* **Output:** Markdown summary table printed to stdout and saved at `results_v5/protocol-v5.0.0/E1/run-e1-local/SUMMARY.md` (or `run-e1-llm-<model>/SUMMARY.md`) along with `summary_metrics.json`.

---

### 2. Experiment E2 — Natural Language Robustness (NL Robustness)
Measures the stability of the Intent Extractor across paraphrases, colloquial phrasing, and cross-lingual Vietnamese/English queries.

* **Method 1 (Built into E1):** The E1 runner automatically computes and exports the `Language Robustness Analysis (English vs Vietnamese)` table whenever multilingual queries are present in the dataset.
* **Method 2 (Inspect the robustness dataset directly):**
  ```bash
  make eval-robustness
  ```

---

### 3. Experiment E3 — Human Usability Study (B0 vs P2 Protocol)
Evaluates practical usability and decision latency when data scientists select environments in the JupyterHub UI:
* **B0 (Manual Selection):** Users manually browse dropdown menus to pick profile sizing and container images.
* **P2 (Intent-Spawner):** Users enter free-form text describing their task; the system infers and recommends optimal configurations.

* **Evaluation Metrics:**
  * **Selection Correctness:** Proportion of trials selecting an optimal environment satisfying task requirements.
  * **Decision Time:** Time elapsed from task prompt presentation to launch confirmation (seconds).
  * **Interaction Burden:** Number of modifications, click events, and overall completion rate.
* **Testing Protocol & Execution (Integrated in `make experiments`):**
  Run `make experiments`, select `3. [E3] Human Usability Study`. The menu supports 4 execution modes:
  1. **`smoke` (Simulation Smoke Test):** Validates test harness integrity and counterbalanced Latin square allocation.
  2. **`generate-plan` (Generate Participant Allocation Plan):** Generates participant allocation manifests with customizable sample size $N$ (6, 12, or 36).
  3. **`trial-session` (Live Participant Session):** Terminal-guided trial execution across 8 tasks (2 warm-up, 6 measured), exposing participants to both B0 and P2 conditions, recording millisecond-accurate decision latency, and administering SEQ, 10-item SUS, and final preference questionnaires.
  4. **`finalize` (Freeze & Statistical Analysis):** Fits Generalized Estimating Equations (GEE) models, computes SUS scores, generates figures, and exports reports to `results_v5/protocol-v5.0.0/E3/run-e3-observed/`.

* **Manual CLI Commands:**
  ```bash
  # Generate allocation plan for 12 participants:
  python -m evaluation_v5.user_study.runner generate-assignments benchmarks_v5/user-study-draft-v1.yaml --output-dir benchmarks_v5/protocol-v5-e3-assignment-target-12 --study-id e3-user-study --participant-count 12 --consent-version consent-v1 --seed 20260827

  # Finalize results after collecting staging events:
  PROTOCOL_V5_ALLOW_DIRTY=1 python -m evaluation_v5.user_study.runner finalize --run-id run-e3-observed --task-set benchmarks_v5/user-study-draft-v1.yaml --assignments benchmarks_v5/protocol-v5-e3-assignment-target-12/assignment-manifest.json --events results_v5/protocol-v5.0.0/E3/staging/events.jsonl --sessions results_v5/protocol-v5.0.0/E3/staging/sessions.jsonl --exclusions results_v5/protocol-v5.0.0/E3/staging/exclusions.jsonl --questionnaires results_v5/protocol-v5.0.0/E3/staging/questionnaires.jsonl --execution-status OBSERVED
  ```

---

### 4. Experiment E4 — Empirical Kubernetes Resource Evaluation
Measures physical KubeSpawner resource utilization on a local Kubernetes cluster (OrbStack, Kind, Docker Desktop, Minikube, K3s):

1. **Preflight Cluster Connectivity Check:**
   ```bash
   make v5-e4-preflight
   ```
2. **Simulation Validation (Dry-run):**
   ```bash
   make v5-resource-dry-run
   # or check allocation efficiency:
   make v5-resource-efficiency-dry-run
   ```
3. **Execute Physical Pod Measurements on Kubernetes:**
   With the cluster running (`status: READY`), execute empirical workload benchmarks demonstrating:
   * **Baseline Small:** Fails with Out Of Memory (OOM-Killed) on large dataset workloads.
   * **Intent-Spawner (P2):** Correctly infers intent, provisions Medium/Large profiles, and achieves successful execution without excessive resource slack.
   ```bash
   PYTHONPATH=. .venv/bin/python -m evaluation_v5.resource execute \
     --result-dir results_v5/protocol-v5.0.0/E4/run-e4-observed \
     --run-id run-e4-observed \
     --image intent-spawner-resource-v5:latest \
     --readiness-attestation benchmarks_v5/protocol-v5-e4-readiness-attestation-orbstack.json
   ```

---

### 5. Experiment E5 — Image Storage Footprint & Container Functional Validation
Measures physical node storage utilization and functional compatibility across notebook container images:
* **Storage Optimization:** Evaluates OCI layer deduplication and layer-sharing ratios across notebook images (`scipy-data-science`, `pytorch-deep-learning`, `tensorflow-deep-learning`, `minimal-python`).
* **Functional Compatibility:** Verifies container startup health and library capability fulfillment (Python, PyTorch, CUDA/GPU, TensorFlow).

* **Evaluation Metrics:**
  * **Layer Deduplication Ratio:** Storage savings achieved by sharing shared base layers.
  * **Container Startup Time:** Time to pull and instantiate the container runtime.
  * **Probe Success Rate:** Passing rate of in-container library health checks.
* **Runtime Integration (Docker / OrbStack):**
  The runner automatically detects and binds to the Docker daemon or **OrbStack** Kubernetes cluster on macOS.

* **Detailed Execution Workflow:**

  1. **Storage Deduplication & Layer Sharing:**
     * *Dry-run Mode (No Docker daemon required):*
       ```bash
       PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage \
         --experiment storage \
         --mode dry-run \
         --stage development \
         --output-dir results_v5/protocol-v5.0.0/E5/run-e5-storage-dry-run
       ```
     * *Docker Physical Mode (OrbStack):*
       ```bash
       PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage \
         --experiment storage \
         --mode docker \
         --stage development \
         --arch arm64 \
         --output-dir results_v5/protocol-v5.0.0/E5/run-e5-storage-orbstack
       ```

  2. **In-Container Functional Validation:**
     *(Requires prior E1 recommendation run output as input)*
     * *Dry-run Mode:*
       ```bash
       PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage \
         --experiment functional \
         --recommendation-run results_v5/protocol-v5.0.0/E1/run-e1-local \
         --mode dry-run \
         --output-dir results_v5/protocol-v5.0.0/E5/run-e5-functional-dry-run
       ```
     * *Docker Physical Mode (OrbStack - spawns ephemeral containers and executes validation probes):*
       ```bash
       PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage \
         --experiment functional \
         --recommendation-run results_v5/protocol-v5.0.0/E1/run-e1-local \
         --mode docker \
         --pull-policy missing \
         --output-dir results_v5/protocol-v5.0.0/E5/run-e5-functional-orbstack
       ```
       *(The `--pull-policy missing` flag ensures OrbStack pulls missing images on demand).*

---

## 📌 Scope Summary of Experiments (E1 – E5)

* **E1 (Recommendation Quality) & E2 (NL Robustness):** Core algorithmic validation verifying P2 retrieval accuracy and stability; executed and tabulated automatically via `make eval-offline`.
* **E4 (Kubernetes Resource Telemetry):** Empirical infrastructure validation on physical Kubernetes nodes (OrbStack/Kind), evaluating cgroups resource envelopes and OOM prevention.
* **E3 (Human Usability Study Protocol) & E5 (OCI Storage Footprint):** Extended usability evaluation protocol and OCI storage optimization analysis.

---

## 📁 Results Directory Layout

* `results_v5/protocol-v5.0.0/E1/`: Contains `SUMMARY.md` (thesis results table), `summary_metrics.json` (machine-readable metrics), and `raw/` directory (individual trial provenance logs).
* `results_v5/protocol-v5.0.0/E3/`: Contains user trial interaction event logs (decision latency, selection correctness).
* `results_v5/protocol-v5.0.0/E4/`: Contains resource allocation telemetry and cgroup metrics captured from physical Kubernetes pods.
* `results_v5/protocol-v5.0.0/E5/`: Contains layer size deduplication profiles and container functional probe validation reports.
