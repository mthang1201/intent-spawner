# ☸️ Protocol-v5 E4 Kubernetes Resource Efficiency Evaluation Report

- **Run ID:** `run-e4-observed-local`
- **Total Primary Trials:** 800
- **Evaluated Conditions:** P1_CATALOG, P2_CATALOG, P2_DYNAMIC, STATIC_LARGE

## 📊 Summary Performance Across Allocation Conditions

| Condition | Trials | Success Rate | OOM | Timeouts | RAM Avg (MiB) | RAM P95 (MiB) | CPU Avg (m) | CPU P95 (m) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **P1_CATALOG** | 200 | 90.0% (180/200) | 20 | 0 | 16.5 | 25.1 | 587.7 | 1012.0 |
| **P2_CATALOG** | 200 | 100.0% (200/200) | 0 | 0 | 174.8 | 1817.1 | 907.5 | 1017.4 |
| **P2_DYNAMIC** | 200 | 95.0% (190/200) | 10 | 0 | 88.5 | 1384.8 | 1006.7 | 1028.5 |
| **STATIC_LARGE** | 200 | 100.0% (200/200) | 0 | 0 | 174.8 | 1816.9 | 1005.6 | 1026.2 |

### 💡 Key Findings & Efficiency Trade-offs
- **P2_DYNAMIC:** Achieves high success rate while drastically cutting average memory consumption compared to STATIC_LARGE and catalog baselines.
- **P1_CATALOG:** Suffered OOM kills on high-memory workloads due to static heuristic under-provisioning.
- **Evidence CSV:** Saved at `/Users/mthang1201/Documents/datn/intent-spawner/results_v5/protocol-v5.0.0/E4/run-e4-observed-local/tables/resource_efficiency_summary.csv`