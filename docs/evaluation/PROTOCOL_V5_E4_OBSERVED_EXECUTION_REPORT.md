# Protocol-v5 E4 Observed Execution and Readiness Report

Protocol `5.0.0`, experiment `E4`.

## Final Verdict

`OBSERVED_EXECUTION_NOT_AUTHORIZED`

E4 readiness/freeze audit completed; OBSERVED execution was not authorized because live cluster eligibility, image verification, oracle approval, and confirmatory freeze gates failed closed. Zero OBSERVED trials were executed or attempted.

---

## 1. Environment Freeze

| Identity / Component | Freeze Status | Value / Digest | Note |
| --- | --- | --- | --- |
| Recommender Catalog | Frozen | `f45b04efc2ea6f271d49c6806b58bfc0f30503cb68944930609f6e0f71882a71` | `recommender/image-catalog.yaml` (v2026-08-06.1) |
| Candidate Corpus | Frozen | `987d78fb0a0ad9d692ee9cfb3561988b1b537595670407d944abc74dc4437444` | 12 candidates |
| Sparse Index | Frozen | `931fac84b818cb934a37bfbfa76092a89626cd5eaffc869887de8558bc6fa747` | BM25 Okapi |
| Dense Index | Frozen | `c0561bcd1ee6ec5153b710aef3deae88bd259a011ddd454513cbb1c675118387` | Cosine dense retriever |
| Hybrid Index | Frozen | `45ea08f29492d796189920713636b3a9cae2f0fb264e023124eb38c8cfad83a4` | RRF hybrid retriever |
| Dynamic Resource Policy | Frozen | `fd1e2696452d4c4eae589e1c01bafeca50e84d21b52cc7320e9b467baa745cc3` | `recommender/resource-policy.yaml` |
| Workload Manifest | Frozen | `fae8f81014a51eec709b119cb23fa0fdb84cfbbecdf2a6dd403e0984950e30bb` | 16 canonical workload families |
| Efficiency Condition Inputs | Frozen | `dce8d2b65bdfc7e2ce280e05645906b91a5d4bbfa1f089601ff54dbb5ab02e66` | Mechanically bound, label-free |
| Efficiency Freeze Contract | Not Frozen | Development phase (`NOT_FROZEN`) | `benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml` |
| Envelope Freeze Contract | Not Frozen | Development phase (`NOT_FROZEN`) | `benchmarks_v5/resource-envelope-freeze-contract-v1.yaml` |
| Node Allocatable Capacity | Not Frozen | `NOT_FROZEN` | Contains no invented allocatable values |
| Workload Container Image | Unavailable | `NOT_BUILT_OR_VERIFIED` | Declared in `cluster_evaluation/resource-v5-image-state.yaml` |
| Approved Oracle Package | Unavailable | `NOT_APPROVED` (path null, digest null) | Required by comparative freeze contract |
| P3 Reranker Gate | Not Applicable | `not_retained` | Authoritative gate excludes P3 from E4 |

---

## 2. Live Execution Gates

| Gate | Status | Evidence |
| --- | --- | --- |
| Confirmatory Freeze | FAIL (Closed) | `confirmatory_freeze_status: NOT_FROZEN` in both freeze contracts |
| Git / Provenance | PASS | Clean worktree at execution time; `git_dirty: false`, Git SHA `2cf206773e63f7086e3c9716a5077793b189fa6d` |
| Workload Manifest | PASS | 16/16 markers verified against frozen canonical workload implementations |
| Plan Identity | PASS | Plan package sealed (`eec8b92fcc6750b466cde3a15893babe2aacba71ef95ee4adb636ead75354466`) |
| Oracle Approval / Digest | FAIL (Closed) | `manual_approval_status: NOT_APPROVED`, path null, sha256 null |
| Workload Image Digest | FAIL (Closed) | Status `NOT_BUILT_OR_VERIFIED`; no pinned image digest verified |
| Capacity Freeze | FAIL (Closed) | `benchmarks_v5/resource-efficiency-capacity-v1.yaml` is `NOT_FROZEN` |
| Kubernetes Context | FAIL (Closed) | Active context is `orbstack`; expected `intent-spawner-eval-v5` |
| Cluster Identity | FAIL (Closed) | Namespace label `z2jh-context-demo.local/cluster-identity` != `intent-spawner-eval-v5` |
| Namespace Safety Label | FAIL (Closed) | `z2jh-context-demo.local/disposable-experiment-v5: "true"` absent |
| Node Identity | FAIL (Closed) | `z2jh-context-demo.local/node-identity: e4-node-v1` absent |
| Isolation / Safety | FAIL (Closed) | `z2jh-context-demo.local/dedicated-e4: "true"` absent; non-dedicated node |
| API Connectivity | FAIL (Closed) | Verified API access absent for dedicated target namespace |
| Required Telemetry | FAIL (Closed) | cgroup-v2 controller check failed on non-dedicated node; no cgroup probe created |
| Cleanup Capability | Not Applicable | Authorization stopped prior to pod creation; no pod created |
| Resume / Evidence Destination | PASS | Exclusive-created local run directories; sealed with SHA256SUMS |

---

## 3. Frozen Trial Plan

The comparative allocation plan package was generated and sealed prior to live execution preflight:
* **Package Path**: `results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-plan-20260905T082000Z`
* **Plan SHA-256**: `eec8b92fcc6750b466cde3a15893babe2aacba71ef95ee4adb636ead75354466`
* **Master Planner Seed**: `20260904`
* **Allocation Conditions**: exactly 4 (`STATIC_LARGE`, `P1_CATALOG`, `P2_CATALOG`, `P2_DYNAMIC`)
* **Workload Families**: exactly 16 (`N = 16`, independent semantic unit)
* **Repetitions**: exactly 10 blocks (each block contains 64 paired trials)
* **Total Planned Trials**: exactly 640 ($16 \times 4 \times 10$)
* **Execution Order / Counterbalancing**: Validated computationally. Seed-shuffled family order within each repetition; balanced Latin rotation rotates condition position across all 4 slots so each condition occupies every slot exactly 4 times per repeat block.
* **Status**: Sealed and immutable, but live execution of the plan was **not authorized**.

---

## 4. Execution Result

Because readiness gates failed closed, zero OBSERVED trials were initiated:

* **Planned Trials**: 640
* **OBSERVED Trials Attempted**: 0
* **OBSERVED Trials Completed**: 0
* **OBSERVED Trials Successful**: 0
* **Kubernetes Experiment Pods / Jobs Created**: 0
* **Trial-Level Resource Telemetry Records**: 0
* **Kubernetes Mutations on `orbstack`**: None. All interaction was strictly read-only preflight inspection (`kubectl get namespace`, `kubectl get nodes`, `kubectl get pods -A`, `kubectl get resourcequota`, `kubectl version`, `kubectl auth can-i`).

> [!IMPORTANT]
> No Kubernetes experiment mutation was performed against the unapproved `orbstack` context.

---

## 5. Evidence Packages

Three sealed packages document the planning and fail-closed readiness audit:

1. **Sealed Comparative Allocation Plan Package**:
   * **Path**: `results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-plan-20260905T082000Z`
   * **Role**: Deterministic paired trial specifications and allocation decisions.
   * **Contents**: `plan.json`, `SHA256SUMS`.
   * **Integrity**: Validated (`validate_efficiency_plan: PASS`, SHA256SUMS verified).

2. **Sealed Fail-Closed Readiness Evidence Package**:
   * **Path**: `results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-observed-run-20260905T081825Z`
   * **Role**: Preserves fail-closed blocker codes and read-only cluster preflight facts.
   * **Execution Status**: `NOT_EXECUTED` (Cluster Measurement Status: `NOT_EXECUTED`).
   * **Trial Records**: 0.
   * **Blocker Codes**: `APPROVED_ORACLE_UNAVAILABLE`, `CGROUP_V2_REQUIRED`, `CLUSTER_INELIGIBLE`, `CONFIRMATORY_FREEZE_INACTIVE`, `IMAGE_DIGEST_UNVERIFIED`, `KUBERNETES_VERSION_UNAVAILABLE`, `NODE_CAPACITY_NOT_FROZEN`, `REQUIRED_API_ACCESS_MISSING`, `WRONG_CLUSTER_FINGERPRINT`, `WRONG_KUBERNETES_CONTEXT`, `WRONG_NODE_COUNT`.
   * **Integrity**: Sealed with `SHA256SUMS`; validated with `validate-package`.

3. **Sealed Preflight Calibration Package**:
   * **Path**: `results_v5/protocol-v5.0.0/E4/e4-resource-envelope-observed-run-20260905T081833Z`
   * **Role**: Preserves read-only calibration preflight facts and dry-run manifest.
   * **Execution Status**: `DRY_RUN` (`NOT_EXECUTED`).
   * **Trial Records**: 0 (Manual review status: `NOT_APPLICABLE`).
   * **Integrity**: Sealed with `SHA256SUMS`; validated with `validate-evidence`.

> [!NOTE]
> These packages constitute sealed fail-closed readiness evidence. They do NOT contain authorized OBSERVED raw trial observations.

---

## 6. Scientific Results

Because OBSERVED execution was not authorized, **no empirical scientific comparison is available**.

* No empirical claims regarding CPU request-time, memory request-time, OOM frequency, timeout behavior, or execution latency among `STATIC_LARGE`, `P1_CATALOG`, `P2_CATALOG`, and `P2_DYNAMIC` are authorized or reported.
* No hypothesis testing (H5, H6) is evaluated from these packages. In the unified Protocol-v5 research analysis claim registry, H5 and H6 evaluate to `NOT_EXECUTED`.

---

## 7. Pareto Verdict

`INCONCLUSIVE / NOT AVAILABLE — OBSERVED EXECUTION NOT AUTHORIZED`

Under the registered contract, Pareto frontier evaluation requires valid observed attempt records across all four conditions. Because execution did not occur, all Pareto dimensions remain indeterminate.

---

## 8. Simulated Capacity

`UNAVAILABLE / NOT EXECUTED`

Under `benchmarks_v5/resource-efficiency-capacity-v1.yaml`, capacity packing requires observed pod resource requests read back from actual completed trials. Because no trials ran and node allocatable capacity remains `NOT_FROZEN`, simulated deterministic request-packing was not executed.

---

## 9. Statistical Unit

* **Planned Raw Trials**: 640
* **Repetitions per Family-Condition**: 10
* **Workload Families**: 16
* **Planned Inferential Units ($N$)**: 16 (workload family is the semantic independent unit; repeated runs estimate within-family variability only)
* **Actual Observed Paired Inferential $N$**: **0** (zero trials authorized)

---

## 10. Validation Results

All verification commands executed cleanly in the dedicated worktree:

| Command | Exit Code | Result Summary |
| --- | :---: | --- |
| `pytest` (comprehensive suite) | `0` | `290 passed in 19.60s` (0 failed, 0 skipped, 0 xfailed) |
| `evaluation_v5.resource validate-manifest` | `0` | `PASS` (16 families, 16 verified markers, static independence pass) |
| `evaluation_v5.resource.efficiency_runner validate` | `0` | `PASS` (640 primary trials, 16 families, 4 conditions, 10 repetitions) |
| `evaluation_v5.resource.efficiency_runner validate-package` | `0` | `PASS` (`sealed: true, execution_status: NOT_EXECUTED, trials: 0`) |
| `evaluation_v5.resource validate-evidence` | `0` | `PASS` (`sealed: true, execution_status: DRY_RUN, trial_records: 0`) |
| `evaluation_v5.isolation_audit` | `0` | `PASS` (1641 docs inspected, no confirmatory split leakage) |
| `scripts/scan-secrets.py` | `0` | `PASS` (2362 text files scanned, zero secrets found) |
| `scripts/validate-portable-evidence.py` | `0` | `PASS` (portable bundle verified, analysis reproduced) |
| `compileall` across all modules | `0` | `PASS` (clean syntax compilation) |
| `git diff --check` | `0` | `PASS` (clean whitespace and diff formatting) |

---

## 11. Limitations and Prerequisites for Future Execution

OBSERVED E4 execution cannot occur until the following prerequisite conditions are legitimately satisfied in a clean committed Git revision:

1. **Dedicated Disposable Cluster**: Availability of a disposable cluster matching context `intent-spawner-eval-v5`.
2. **Dedicated Labeled Node**: A single node labeled `z2jh-context-demo.local/node-identity: e4-node-v1` and `z2jh-context-demo.local/dedicated-e4: "true"`.
3. **Dedicated Namespace**: Namespace `z2jh-context-demo` labeled `z2jh-context-demo.local/disposable-experiment-v5: "true"`.
4. **cgroup v2 Telemetry**: Dedicated node exposing cgroup v2 with active `cpu`, `memory`, and `pids` controllers and `memory.events` counters (`oom`, `oom_kill`).
5. **Verified Container Image**: Workload image built from `cluster_evaluation/Dockerfile.resource-v5`, pushed, pre-pulled onto the dedicated node, with its SHA-256 digest verified and recorded in `cluster_evaluation/resource-v5-image-state.yaml`.
6. **Frozen Node Capacity**: Allocatable CPU, memory, and GPU quantities read back from `kubectl get node -o json` and frozen in `benchmarks_v5/resource-efficiency-capacity-v1.yaml` with `freeze_status: FROZEN`.
7. **Approved Oracle Package**: Sealed and manually approved independent envelope calibration package recorded in `benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml`.
8. **Confirmatory Freeze Activation**: Setting `confirmatory_freeze_status: FROZEN` in `benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml` and `benchmarks_v5/resource-envelope-freeze-contract-v1.yaml`.
9. **Passing Live Preflight**: Non-mutating preflight returning `eligibility_status: ELIGIBLE` with zero failure codes.
