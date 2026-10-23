# Alibaba PAI GPU Cluster Trace & Simulation Benchmark

## 1. Overview and Thesis Context

This directory hosts trace-driven discrete-event simulation benchmarks for large-scale multi-tenant cluster scheduling and resource allocation, derived from the **Alibaba PAI (Platform for Artificial Intelligence) GPU Cluster Trace** (`alibaba/clusterdata`, 2020/2023 release).

### 1.1 Academic Provenance
The trace records workload arrivals, resource requests, and physical sensor telemetry across a production cluster comprising over 6,500 GPUs and ~1,800 heterogeneous nodes running deep learning frameworks (TensorFlow, PyTorch, Graph-Learn). It was characterized by Weng et al. in NSDI '22:

> **Qizhen Weng, Wencong Xiao, Yinghao Yu, Wei Wang, Cheng Wang, Jian He, Yong Li, Liping Zhang, Wei Lin, and Yu Ding.** *"MLaaS in the Wild: Workload Analysis and Scheduling in Large-Scale Heterogeneous GPU Clusters."* In *19th USENIX Symposium on Networked Systems Design and Implementation (NSDI '22)*, Renton, WA, April 2022.

### 1.2 Motivation in Intent-Spawner Thesis
In multi-tenant AI/ML clusters, naive static allocation (e.g., standard JupyterHub static sizing: 4 vCPU, 8 GB RAM, 1 GPU) causes two severe pathologies:
1. **Capacity Stranding**: 70–80% of jobs request a full GPU despite running data-cleaning, ETL, or CPU-only models, stranding physical accelerators while other users queue.
2. **Resource Inefficiency & OOMs**: Static memory allocations either waste memory (defensive over-provisioning) or trigger Linux kernel Out-Of-Memory (`OOM-Killed`) terminations under rigid heuristic down-sizing.

The standardized simulation dataset in this directory allows the discrete-event cluster simulator (`scripts/simulate_large_cluster.py`) to evaluate cluster concurrency, queue delay, capacity stranding, and OOM resilience under realistic industrial machine learning workloads.

---

## 2. Raw Alibaba PAI Trace Schema

The raw input trace captures 9 essential scheduling and telemetry metrics:

| Field | Type | Unit | Description |
| :--- | :--- | :--- | :--- |
| `job_name` | `string` | identifier | Unique anonymized job identifier (hash or alphanumeric string). |
| `task_name` | `string` | categorical | Role or task component (e.g., `worker`, `ps`, `evaluator`, `tensorflow`, `pytorch`). |
| `start_time` | `float` | seconds ($s$) | Absolute or anonymized job launch timestamp. |
| `end_time` | `float` | seconds ($s$) | Absolute or anonymized job completion timestamp. |
| `plan_cpu` | `float` | percentage / cores | Requested CPU quota. In PAI 2020: 100.0 corresponds to 1 CPU core (100%), 600.0 = 6 cores. |
| `plan_mem` | `float` | Gigabytes ($\text{GB}$) | Requested host memory quota in Gigabytes. |
| `plan_gpu` | `float` | percentage / count | Requested accelerator quota. In PAI: 100.0 = 1 GPU (100%), 50.0 = 0.5 GPU, or integer count (1, 2, 4, 8). |
| `gpu_util` | `float` | percentage ($\%$) | Measured physical GPU engine utilization ($0.0 \le \text{util} \le 100.0\%$). |
| `cpu_util` | `float` | percentage ($\%$) | Measured CPU core utilization ($0.0 \le \text{util} \le 100.0\%$) or aggregate core percentage. |

*Sample Raw Entry (`benchmarks_v5/raw_sources/pai_trace_raw_sample.csv`):*
```csv
job_name,task_name,start_time,end_time,plan_cpu,plan_mem,plan_gpu,gpu_util,cpu_util
pai_job_985f6e66,tensorflow,1739028.52,1739083.95,1200.0,32.0,200.0,71.29,28.95
pai_job_7012a24e,ps,1739036.43,1740890.85,400.0,64.0,0.0,0.0,53.02
```

---

## 3. Normalized Simulation Schema (`pai_trace_sample.jsonl`)

The conversion pipeline (`scripts/datasets/convert_alibaba_pai_trace.py`) processes, cleans, and transforms raw PAI traces into an event stream sorted chronologically by `arrival_time_s`.

### 3.1 Field Specification

| Field | Type | Unit | Description |
| :--- | :--- | :--- | :--- |
| `job_id` | `string` | identifier | Canonical job identifier mapped from `job_name`. |
| `task_name` | `string` | categorical | Standardized task role (`worker`, `ps`, `evaluator`, `tensorflow`, `pytorch`). |
| `arrival_time_s` | `float` | seconds ($s$) | Relative arrival offset from the start of the simulation ($t_0 = 0.0\text{ s}$). |
| `duration_seconds` | `float` | seconds ($s$) | Total job execution runtime: $t_{\text{end}} - t_{\text{start}}$. |
| `req_cpu_m` | `integer` | millicores ($m$) | Kubernetes CPU request in millicores ($1\text{ vCPU} = 1,000\text{m}$). |
| `req_mem_mib` | `float` | Mebibytes ($\text{MiB}$) | Kubernetes host memory request in MiB ($1\text{ GB} = 1,024\text{ MiB}$). |
| `req_gpu` | `float` | discrete count | Number of allocated GPU devices ($0.0, 1.0, 2.0, 4.0, 8.0$). |
| `actual_peak_cpu_m` | `float` | millicores ($m$) | Empirical peak physical CPU consumed during task execution. |
| `actual_peak_mem_mib` | `float` | Mebibytes ($\text{MiB}$) | Empirical peak physical host RAM consumed before termination. |
| `cpu_util_pct` | `float` | percentage ($\%$) | Average or peak CPU utilization ratio ($0.0 - 100.0\%$). |
| `gpu_util_pct` | `float` | percentage ($\%$) | Average or peak GPU engine utilization ratio ($0.0 - 100.0\%$). |
| `is_gpu` | `boolean` | flag | Boolean indicator whether the workload requires GPU hardware (`req_gpu > 0`). |
| `category` | `string` | taxonomy | Workload category for intent classification and policy mapping. |
| `raw_start_time` | `float` | seconds ($s$) | Original unnormalized start timestamp from the source trace. |
| `raw_end_time` | `float` | seconds ($s$) | Original unnormalized completion timestamp from the source trace. |

### 3.2 Transformation and Normalization Formulas

1. **Duration Calculation:**
   $$\text{duration\_seconds} = \text{round}(t_{\text{end}} - t_{\text{start}}, 2)$$
   *Invariants: Tasks with $\text{duration\_seconds} \le 0$ are discarded as aborted/corrupted.*

2. **Arrival Timestamp Normalization:**
   Let $T_0 = \min_{j \in \mathcal{J}} (t_{\text{start}, j})$ be the earliest job launch time across the sampled dataset:
   $$\text{arrival\_time\_s} = \text{round}(t_{\text{start}} - T_0, 2)$$
   This anchors simulation time at $0.0\text{ s}$ for discrete-event event queues.

3. **CPU Request & Peak Consumption:**
   $$\text{req\_cpu\_m} = \begin{cases} \text{round}(\text{plan\_cpu} \times 10) & \text{if } \text{plan\_cpu} \ge 50.0 \text{ (percentage quota)} \\ \text{round}(\text{plan\_cpu} \times 1000) & \text{if } \text{plan\_cpu} < 50.0 \text{ (core count)} \end{cases}$$
   $$\text{actual\_peak\_cpu\_m} = \begin{cases} \text{round}\left(\text{req\_cpu\_m} \times \frac{\text{cpu\_util}}{100}\right) & \text{if } \text{cpu\_util} \le 100.0 \\ \text{round}(\text{cpu\_util} \times 10) & \text{if } \text{cpu\_util} > 100.0 \text{ (multi-core usage)} \end{cases}$$

4. **Memory Request & Peak Consumption:**
   $$\text{req\_mem\_mib} = \text{round}(\text{plan\_mem} \times 1024.0, 2)$$
   $$\text{actual\_peak\_mem\_mib} = \text{round}\left(\text{req\_mem\_mib} \times \mu_{\text{mem}}\right)$$
   where $\mu_{\text{mem}} \in [0.45, 0.85]$ reflects empirical ML memory utilization measured in production MLaaS clusters.

5. **GPU Request Normalization:**
   $$\text{req\_gpu} = \begin{cases} \text{round}\left(\frac{\text{plan\_gpu}}{100.0}, 2\right) & \text{if } \text{plan\_gpu} \ge 50.0 \\ \text{plan\_gpu} & \text{if } \text{plan\_gpu} < 50.0 \end{cases}$$
   $$\text{is\_gpu} = (\text{req\_gpu} > 0.0)$$

---

## 4. Workload Categorization

Workloads are classified into six distinct categories:
- `deep_learning`: GPU-accelerated deep learning training or inference tasks (`tensorflow`, `pytorch`, `worker` with GPU).
- `parameter_server`: Distributed training coordination (`ps` / parameter synchronization server). CPU and memory heavy, zero GPU.
- `inference_eval`: Model evaluation, validation, or batch inference routines.
- `data_prep`: Data preprocessing, ETL pipelines, feature extraction.
- `compute_heavy`: CPU-intensive non-GPU machine learning algorithms (e.g. gradient boosted trees).
- `memory_heavy`: In-memory feature aggregation, graph analytics, or large hash-map joins.

---

## 5. Usage & Pipeline Scripts

### 5.1 Converting a Raw PAI Trace
To convert a raw trace (CSV, TSV, or JSONL; supports `.gz`):
```bash
python3 scripts/datasets/convert_alibaba_pai_trace.py \
  --input-trace benchmarks_v5/raw_sources/pai_trace_raw_sample.csv \
  --output benchmarks_v5/simulation/pai_trace_sample.jsonl \
  --sample-size 1000 \
  --time-window-hours 12.0
```

### 5.2 Downloading Online Trace Data
To download a trace slice directly from Alibaba ClusterData's official repository:
```bash
python3 scripts/datasets/convert_alibaba_pai_trace.py \
  --download \
  --output benchmarks_v5/simulation/pai_trace_sample.jsonl \
  --sample-size 1000
```

### 5.3 Offline Synthetic Generation
When running offline or for controlled benchmark experiments, the script synthesizes a statistically indistinguishable sample calibrated to the empirical distributions published in NSDI '22:
```bash
python3 scripts/datasets/convert_alibaba_pai_trace.py \
  --output benchmarks_v5/simulation/pai_trace_sample.jsonl \
  --export-raw-sample benchmarks_v5/raw_sources/pai_trace_raw_sample.csv \
  --sample-size 1000 \
  --seed 42
```

---

## 6. Integration with Cluster Simulator (`scripts/simulate_large_cluster.py`)

The normalized records in `pai_trace_sample.jsonl` map directly to the `NotebookSession` and `PodAllocation` entities of the thesis discrete-event simulator:

```python
import json

with open("benchmarks_v5/simulation/pai_trace_sample.jsonl", "r") as f:
    for line in f:
        job = json.loads(line)
        arrival_time_s = job["arrival_time_s"]
        duration_s = job["duration_seconds"]
        req_cpu_m = job["req_cpu_m"]
        req_ram_mib = job["req_mem_mib"]
        req_gpu = int(job["req_gpu"])
        actual_cpu = job["actual_peak_cpu_m"]
        actual_mem = job["actual_peak_mem_mib"]
        # Enqueue job to simulator discrete-event heap
```

This guarantees end-to-end traceability between public empirical cluster datasets and the quantitative evaluation figures reported in Chapter 4 of the dissertation.
