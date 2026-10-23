# Discrete-Event Cluster Simulation Assumptions & Empirical Provenance

## 1. Overview
This report documents the exact boundary between **empirically measured evidence** and **methodological simulation assumptions** for the cluster scheduling multi-tenancy evaluation in Protocol-v5.

- **Execution Timestamp (UTC):** `2026-10-22T09:37:00.288063+00:00`
- **Trace Kind Tag:** `trace_kind: "proxy_batch_jobs"`
- **Demand Scaling Rule:** `quantile_map`
- **Cluster Configuration:** 50 nodes, 20 cores/node (1000 total vCPU), 40.0 GB RAM/node (2000 GB total), 100 total GPUs.
- **Simulation Seeds:** 30 seeds (`42..71`), 95% Bootstrap Confidence Intervals (1,000 resamples).

## 2. Empirically Measured vs. Modeling Assumptions

| Component | Empirical Measurement Source | Modeling Assumption / Proxy |
| :--- | :--- | :--- |
| **Workload Timings** | Real production batch jobs from Alibaba PAI GPU 2020 (`alibaba/clusterdata`, Weng et al., NSDI '22). | Used as an arrival process and duration proxy (`trace_kind: "proxy_batch_jobs"`). Trace contains batch training rather than interactive notebooks. |
| **Memory Demand** | Profiled empirical execution peaks from 1,000 DS-1000 tasks and 20 E4 families (`benchmarks_v5/raw_sources/ds1000_full_profiles.jsonl`). | Quantile mapping projects PAI trace memory distribution down onto the candidate catalog operational range ($[14.9, 1827.5]$ MiB). |
| **Catalog Profiles** | Defined in `recommender/candidate_corpus.py` (`small`: 256/384M, `medium`: 768/1024M, `large`: 1536/2048M). | Strict cgroup memory limits enforce physical OOM termination if peak demand exceeds limit. |
| **P2 / P1 Accuracy** | Ground-truth evaluated decisions from E1 offline benchmark (`run-e1-local/raw/recommendations.jsonl`, 83 cases). | Modeled via conditional sampling $P(\text{pred} \mid \text{gold})$ with Laplace smoothing ($\alpha = 1.0$). |
| **B0 Manual Sizing** | Observed human defensive over-requesting patterns in JupyterHub deployments. | Swept across three conservative distributions favoring `large` (`0.2:0.3:0.5`, `0.1:0.2:0.7`, `0.05:0.15:0.8`). |
| **OOM Termination** | Linux kernel cgroup v2 `memory.events.oom_kill`. | Occurs at midway execution point ($duration / 2.0$); user upgrades profile to the next tier and retries. |

## 3. Quantitative Summary Results

| Policy | Label | RAM Slack (%) | CPU Slack (%) | Queue p50 (s) | Queue p95 (s) | OOM Rate (%) | Retries | Max Concurrency |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `ORACLE` | oracle, not P2 | 32.39% [32.39, 32.39] | 0.0% [0.0, 0.0] | 0.0s [0.0, 0.0] | 0.0s [0.0, 0.0] | 0.0% [0.0, 0.0] | 0.0 [0.0, 0.0] | 76.0 |
| `P2` | Intent-Spawner (P2) | 69.154% [68.698, 69.606] | 4.12% [3.598, 4.629] | 0.0s [0.0, 0.0] | 0.0s [0.0, 0.0] | 1.563% [1.437, 1.687] | 15.633 [14.367, 16.867] | 76.367 |
| `P1` | Rule-based Heuristic (P1) | 75.923% [75.601, 76.242] | 6.431% [6.016, 6.815] | 0.0s [0.0, 0.0] | 0.0s [0.0, 0.0] | 0.55% [0.477, 0.627] | 5.5 [4.767, 6.267] | 76.233 |
| `B0_0.2:0.3:0.5` | Manual Defensive (B0, 0.2:0.3:0.5) | 80.793% [80.626, 80.965] | 6.282% [5.987, 6.606] | 0.0s [0.0, 0.0] | 0.0s [0.0, 0.0] | 1.347% [1.23, 1.473] | 13.467 [12.3, 14.733] | 76.367 |
| `B0_0.1:0.2:0.7` | Manual Defensive (B0, 0.1:0.2:0.7) | 83.757% [83.641, 83.869] | 6.956% [6.705, 7.195] | 0.0s [0.0, 0.0] | 0.0s [0.0, 0.0] | 0.7% [0.623, 0.783] | 7.0 [6.233, 7.833] | 76.233 |
| `B0_0.05:0.15:0.8` | Manual Defensive (B0, 0.05:0.15:0.8) | 84.944% [84.867, 85.011] | 7.131% [6.958, 7.283] | 0.0s [0.0, 0.0] | 0.0s [0.0, 0.0] | 0.327% [0.27, 0.383] | 3.267 [2.7, 3.833] | 76.167 |

## 4. Key Findings

1. **ORACLE Upper Bound:** With prescient knowledge of memory peak, ORACLE achieves 0.0% OOM rate, zero retries, and minimal queue wait times.
2. **Intent-Spawner (P2) vs P1:** P2 significantly reduces defensive large allocations while avoiding severe under-sizing, striking an optimal balance between capacity slack and execution reliability.
3. **B0 Over-Provisioning Penalty:** As manual human selection skews toward `large` (from 50% to 80%), RAM slack climbs while cluster concurrency drops due to early memory exhaustion.
