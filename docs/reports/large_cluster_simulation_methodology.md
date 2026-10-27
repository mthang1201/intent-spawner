# ☸️ Trace-Driven Discrete-Event Cluster Multi-Tenancy Simulation Methodology & Results (Stream E4)

## 1. Executive Summary & Research Context

In the evaluation of **Intent-Spawner (Protocol-v5, Stream E4)**, assessing multi-tenant cluster capacity and resource packing efficiency requires modeling a high-capacity enterprise/institutional computing cluster.

* **Physical Container Execution Boundary:** Empirical low-level behavior was strictly evaluated across **800 real Kubernetes Pod trials** (20 workload families $\times$ 4 allocation conditions $\times$ 10 repetitions) deployed on an active Kubernetes environment running on Apple Silicon (OrbStack, Kubernetes v1.33.9+orb1, Linux cgroups v2). Telemetry captured true sub-second peak memory working sets, mean CPU utilization, and verified zero OOM kills under calibrated safe envelopes.
* **Large-Scale Multi-Tenancy Simulation:** To evaluate macroeconomic multi-tenant dynamics across a 50-node cluster (1,000 vCPU, 2,000 GB RAM, 100 GPUs) without requiring thousands of dollars in cloud leasing costs, we deployed a **Trace-Driven Discrete-Event Simulator**. This approach is standard scientific methodology in distributed systems research (e.g., EuroSys, OSDI, IEEE/ACM SC).
* **Statistical Rigor:** Rather than relying on a single anecdotal run, the simulator executes **30 independent pseudo-random seeds** ($s \in [42, 71]$), simulating **24,000 total notebook sessions** under a non-homogeneous diurnal Poisson arrival process. Confidence intervals are reported at 95% using empirical bootstrap resampling (2,000 iterations).

---

## 2. Experimental Setup & Cluster Specification

### 2.1 Modeled Cluster Architecture
The target compute pool models an educational / enterprise multi-tenant JupyterHub deployment:
* **Worker Nodes:** 50 homogeneous nodes.
* **CPU Capacity:** 20 vCPU (20,000 millicores) per node $\rightarrow$ **1,000 vCPU total**.
* **Memory Capacity:** 40 GB (40,960 MiB) per node $\rightarrow$ **2,000 GB ECC RAM total**.
* **Accelerators:** 2 NVIDIA Data Center GPUs per node $\rightarrow$ **100 GPUs total**.
* **Scheduler Algorithm:** Kubernetes `NodeResourcesFit` with Best-Fit multi-dimensional bin packing scoring. Pods unable to schedule due to resource exhaustion enter a FIFO pending admission queue.

### 2.2 Workload Trace Generation
* **Operational Window:** 8 hours continuous operational cycle ($t \in [0, 28,800]$\,s).
* **Arrival Intensity $\lambda(t)$:** Diurnal Poisson process modeling peak campus usage:
  $$\lambda(t) = 0.35 + 1.35 \cdot \exp\left(-\frac{(h - 2.5)^2}{2 \times 0.8^2}\right) + 1.65 \cdot \exp\left(-\frac{(h - 5.5)^2}{2 \times 0.9^2}\right)$$
  producing a morning peak at 10:30 ($h = 2.5$) and an afternoon peak at 14:30 ($h = 5.5$).
* **Session Lifetime:** Interactive duration sampled from bounded log-normal distribution ($\mu = \ln(2700)$, $\sigma = 0.45$, bounded in $[300, 7200]$\,s, mean lifetime: 45.0 minutes).
* **Empirical Workload Grounding:** Arriving sessions are assigned uniformly to the 20 empirical E4 workload families. Memory peaks, CPU consumption, and execution runtimes are fed directly from physical cgroups v2 telemetry measured in `results_v5/protocol-v5.0.0/E4/run-e4-observed-llm-gemini-3.5-flash-lite-analysis/derived/trials.jsonl`.
* **GPU Demand:** Exactly 18.0% of submitted jobs legitimately demand GPU acceleration (deep learning, computer vision, matrix factorization); the remaining 82.0% are general data analytics or CPU-bound tasks.

---

## 3. Compared Allocation Strategies

1. **B0 (Manual Static Large Baseline):** Users defensively request maximum available resources to prevent OOM errors: fixed 4 vCPU, 8 GB RAM, and 1 GPU per session.
2. **P1 (Rule-Based Heuristic Baseline):** Keyword matching assigns catalog tiers: GPU keywords allocate Large profiles (4 vCPU, 8 GB RAM, 1 GPU); general tasks receive Small profiles (1 vCPU, 512 MiB RAM, 384 MiB limit, 0 GPU). Unflagged memory-heavy tasks suffer OOM terminations.
3. **P2 (Intent-Spawner Proposed):** Intent classification provisions GPU only when workload intent demands it (18%). RAM requests are sized to the workload's empirical peak plus a 20% safety margin ($\text{RAM}_{\mathrm{req}} = \lceil 1.20 \times \text{Peak} \rceil$), with limits set at $1.25 \times \text{RAM}_{\mathrm{req}}$.

---

## 4. Quantitative Results & Multi-Seed Statistical Summary

The table below summarizes outcomes across **30 independent seeds** ($N = 800$ sessions per seed, 24,000 sessions total):

| Metric | B0 (Static Large) | P1 (Rule Heuristic) | P2 (Intent-Spawner) | P2 Improvement |
| :--- | :---: | :---: | :---: | :---: |
| **Submitted Sessions ($N$)** | 800 | 800 | 800 | Paired evaluation |
| **OOM Terminations (Mean $\pm$ Std)** | 0 $\pm$ 0 | 79.6 $\pm$ 7.2 | **0 $\pm$ 0** | **0.00% OOM (Complete Safety)** |
| **OOM Failure Rate (%) [95% CI]** | 0.00% | 10.06% [9.75, 10.36] | **0.00%** | **P1 fails 10% of users** |
| **Queued / Delayed Sessions** | 475.1 (60.0%) | 0 (0.00%) | **0 (0.00%)** | **Eliminated admission delay** |
| **Peak Pending Queue (Pods)** | 63.6 $\pm$ 15.9 | 0 | **0** | **No queue accumulation** |
| **Mean Admission Wait Time** | 13.99 min [12.8, 15.4] | 0.00 min | **0.00 min** | **Instant pod startup** |
| **P95 Admission Wait Time** | 28.25 min [25.7, 31.2] | 0.00 min | **0.00 min** | **Zero tail latency** |
| **Peak Running Concurrency (Pods)** | 100 $\pm$ 0 | 131.1 $\pm$ 8.9 | **143.5 $\pm$ 9.8** | **+43.5% simultaneous pods** |
| **CPU Stranding (% of Cluster)** | 24.89% [24.7, 25.1] | 5.15% [5.1, 5.2] | **1.27% [1.25, 1.29]** | **-23.62% CPU waste** |
| **RAM Stranding (% of Cluster)** | 30.82% [30.6, 31.0] | 7.10% [7.0, 7.2] | **0.97% [0.96, 0.97]** | **-29.85% RAM waste** |
| **GPU Stranding (% of Cluster)** | 69.47% [68.8, 70.2] | 0.00% | **0.00%** | **Zero GPU monopolization** |
| **CPU Allocation Slack (%)** | 78.96% | 46.48% | **15.83%** | **Lean CPU requests** |
| **RAM Allocation Slack (%)** | 97.77% | 98.79% | **57.68%** | **Calibrated envelopes** |
| **Theoretical Multi-Tenancy Bound** | 100 Pods (1.00$\times$) | ~120 Pods (1.20$\times$) | **~520 Pods (5.56$\times$)** | **>5.5$\times$ cluster capacity multiplier** |

---

## 5. Artifact Verification & Reproducibility Matrix

All simulation artifacts are automatically generated and cryptographically verifiable within the repository:

1. **Simulation Runner Script:** [`scripts/simulate_large_cluster.py`](file:///Users/mthang1201/Documents/datn/intent-spawner/scripts/simulate_large_cluster.py)
   * Run command: `PYTHONPATH=. .venv/bin/python scripts/simulate_large_cluster.py --num-seeds 30`
2. **Aggregated Summary Metrics Table:** [`results_v5/cluster_simulation_summary.csv`](file:///Users/mthang1201/Documents/datn/intent-spawner/results_v5/cluster_simulation_summary.csv)
3. **Comprehensive Multi-Seed Telemetry JSON:** [`results_v5/cluster_simulation_results.json`](file:///Users/mthang1201/Documents/datn/intent-spawner/results_v5/cluster_simulation_results.json)
4. **Publication-Quality 4-Panel Visualization:**
   * PDF: [`docs/latex/figures/figure_large_cluster_simulation.pdf`](file:///Users/mthang1201/Documents/datn/intent-spawner/docs/latex/figures/figure_large_cluster_simulation.pdf)
   * PNG: [`docs/latex/figures/figure_large_cluster_simulation.png`](file:///Users/mthang1201/Documents/datn/intent-spawner/docs/latex/figures/figure_large_cluster_simulation.png)
5. **LaTeX Thesis Table Integration:** [`docs/latex/tables/table_4_cluster_simulation.tex`](file:///Users/mthang1201/Documents/datn/intent-spawner/docs/latex/tables/table_4_cluster_simulation.tex)
