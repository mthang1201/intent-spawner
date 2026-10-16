# ☸️ Protocol-v5 E4 Kubernetes Resource Efficiency Evaluation Report

- **Run ID:** `run-e4-observed-llm-gemini-3.5-flash-lite`
- **Total Primary Trials:** 800
- **Evaluated Conditions:** P1_CATALOG, P2_CATALOG, P2_DYNAMIC, STATIC_LARGE

## 📊 Summary Performance Across Allocation Conditions

| Condition | Trials | Success Rate | OOM | Timeouts | RAM Avg (MiB) | RAM P95 (MiB) | CPU Avg (m) | CPU P95 (m) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **P1_CATALOG** | 200 | 90.0% (180/200) | 20 | 0 | 16.2 | 24.8 | 578.4 | 1015.6 |
| **P2_CATALOG** | 200 | 100.0% (200/200) | 0 | 0 | 175.1 | 1817.1 | 831.1 | 1026.0 |
| **P2_DYNAMIC** | 200 | 95.0% (190/200) | 10 | 0 | 88.4 | 1384.5 | 905.7 | 1032.9 |
| **STATIC_LARGE** | 200 | 100.0% (200/200) | 0 | 0 | 174.7 | 1817.1 | 1005.1 | 1023.4 |

### 💡 Key Findings & Efficiency Trade-offs
- **P2_DYNAMIC:** Achieves high success rate while drastically cutting average memory consumption compared to STATIC_LARGE and catalog baselines.
- **P1_CATALOG:** Suffered OOM kills on high-memory workloads due to static heuristic under-provisioning.
- **Evidence CSV:** Saved at `/Users/mthang1201/Documents/datn/intent-spawner/results_v5/protocol-v5.0.0/E4/run-e4-observed-llm-gemini-3.5-flash-lite/tables/resource_efficiency_summary.csv`