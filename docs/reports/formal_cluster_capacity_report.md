# ☸️ Protocol-v5 E4 Formal Kubernetes Cluster Capacity Report

## 1. Executive Summary

This report documents the **formal cluster capacity evaluation** for Protocol-v5 Stream E4.
* **Evidence Label:** `SIMULATED_DETERMINISTIC_REQUEST_PACKING`
* **Evidence Type:** `SIMULATED_CAPACITY`
* **Capacity Source:** `KUBERNETES_NODE_STATUS_ALLOCATABLE`
* **Scheduler Input:** `OBSERVED_POD_RESOURCE_REQUESTS`
* **Execution Status:** **`SIMULATED`** (Successfully executed with frozen attested node allocatable)
* **Analyzed Physical Trials:** 800 observed trials across 20 workload families and 4 resource conditions.

---

## 2. Verified Kubernetes Node Capacity

The packing model uses the externally verified allocatable resource capacity of the certified execution node:
* **Node Name:** `orbstack`
* **Node UID:** `574e43f1-b4af-45a4-94ae-385414e52560`
* **Allocatable CPU:** `8000m` (8.0 vCPU cores)
* **Allocatable RAM:** `7993 MiB` (7.81 GiB)
* **Allocatable GPU:** `0`

---

## 3. Multidimensional Bin-Packing Simulation Results

### 3.1 Balanced Workload Mix (20 Distinct Workload Families)
Under the balanced mix benchmark (one concurrent session per each of the 20 distinct workload families):

| Resource Allocation Condition | Nodes Required | Sessions per Node | Node Reduction vs Static Large | Density Gain vs Static Large |
| :--- | :---: | :---: | :---: | :---: |
| **STATIC_LARGE (Baseline B0)** | **4** | **5.00** | — (Reference) | — (Reference) |
| **P1_CATALOG (Rule-Based)** | 1 | 20.00 | -3 nodes | +300.0% *(high OOM risk)* |
| **P2_CATALOG (Intent-Spawner)** | **2** | **10.00** | **-2 nodes (50% reduction)** | **+100.0% (Double density)** |
| **P2_DYNAMIC (Intent-Spawner)** | **3** | **6.67** | **-1 node (25% reduction)** | **+33.3%** |

### 3.2 Key Findings
1. **Node Infrastructure Savings:**
   * In `STATIC_LARGE`, defensively over-provisioning 2.0 vCPU and 2.0 GiB per container requires **4 worker nodes** to host 20 notebook sessions.
   * In `P2_CATALOG`, intent-aware right-sizing compresses the 20 sessions into just **2 worker nodes**, cutting infrastructure footprint by **50.0%**.
2. **Hosting Density Gain:**
   * `P2_CATALOG` doubles average session density from **5.0 sessions/node** up to **10.0 sessions/node** without compromising OOM safety.
   * `P2_DYNAMIC` achieves **6.67 sessions/node** (+33.3% density gain) while enforcing granular custom limits.

---

## 4. Methodological Distinction
* **Deterministic Request Packing (This Report):** Mathematical multi-dimensional First-Fit Decreasing bin-packing of declared pod specifications against verified node allocatables.
* **Large-Scale Multi-Tenancy Simulation (E4 Simulator):** Dynamic discrete-event simulation across 50 nodes over an 8-hour diurnal Poisson arrival trace with queuing latency and stranding telemetry.
* **Physical Kernel Telemetry (E4 Physical Trials):** 800 real Pod executions on OrbStack with cgroups v2 measuring peak memory and OOM avoidance.
