# Protocol-v5 E4 Observed Execution and Readiness Report

Protocol `5.0.0`, experiment `E4`.

## 1. Executive Summary

This report documents the execution of the Protocol-v5 E4 experiment (covering both independent resource calibration and comparative resource efficiency) in accordance with Protocol-v5 experiment engineering rules.

In compliance with **Rule 7 & 8 of AGENTS.md**:
* Zero benchmark labels, Kubernetes measurements, latency, or resource usages were fabricated.
* Because the live dedicated disposable Kubernetes cluster (`intent-spawner-eval-v5`), frozen node allocatable capacity, verified container image digest, and approved calibration oracle package are not currently active in this environment, live execution gates failed closed.
* Sealed, tamper-evident packages were generated with explicit `NOT_EXECUTED` status, capturing all read-only preflight facts, contract hashes, and blocker codes.

## 2. Provenance and Git Isolation

* **Task branch**: `experiment/protocol-v5-e4-observed-run`
* **Isolated worktree path**: `/Users/mthang1201/Documents/datn/intent-spawner-protocol-v5-e4-observed-run`
* **Base commit SHA**: `2cf206773e63f7086e3c9716a5077793b189fa6d`
* **Execution Git SHA (`execution_git_sha`)**: `2cf206773e63f7086e3c9716a5077793b189fa6d`
  (The exact clean committed revision present during plan generation, execution attempts, and discovery).
* **Delivery commit SHA (`delivery_commit_sha`)**: To be committed with this report artifact.
* **Concurrent Agent Protection**:
  * The main working tree (`/Users/mthang1201/Documents/datn/intent-spawner`) on branch `main` remained completely untouched.
  * Other worktrees (`intent-spawner-b0-p2-user-study`, `intent-spawner-protocol-v5-research-analysis-hardening`, `intent-spawner-image-storage-scalability`) operate independently without interference.
  * No broad git commands (`git add .`, `git commit -a`, `git reset --hard`, `git clean`) were used; all staged files are explicit paths.

## 3. Experiment Design and Matrix

The Protocol-v5 E4 comparative resource efficiency design binds:
* **Workload Families**: 16 frozen canonical instances (`N = 16`, independent semantic unit).
* **Conditions**: 4 allocation policies (`STATIC_LARGE`, `P1_CATALOG`, `P2_CATALOG`, `P2_DYNAMIC`).
* **Repetitions**: 10 paired repetitions per family-condition combination.
* **Primary Trials**: $16 \times 4 \times 10 = 640$ primary trials.
* **Execution Order Algorithm**: `seeded-family-shuffle-with-balanced-latin-condition-rotation-v1`.
  * Within each repetition, families are shuffled by seed.
  * Conditions rotate through Latin positions so every condition occupies each within-family temporal position exactly 4 times per repeat block.
* **Pareto Frontier Objectives**:
  * Minimization: `cpu_cost_per_success`, `memory_cost_per_success`, `oom_rate`, `timeout_rate`, `pending_or_admission_rate`, `runtime_error_rate`, `incorrect_rate`.
  * Maximization: `success_rate`, `correct_completion_rate`.
  * Lower cost with degraded reliability is classified as `EFFICIENCY_RELIABILITY_TRADEOFF`, never an improvement.
  * No post-hoc success noninferiority margin is permitted.

## 4. Generated Artifacts

### 4.1. Comparative Allocation Plan Package
* **Path**: `results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-plan-20260905T082000Z`
* **Plan SHA-256**: `eec8b92fcc6750b466cde3a15893babe2aacba71ef95ee4adb636ead75354466`
* **Status**: Sealed with `SHA256SUMS`.
* **Contents**:
  * `plan.json`: Complete specification of 16 allocation decisions, 640 planned trials, random seeds, and input hashes.

### 4.2. Resource Efficiency Execution Package
* **Path**: `results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-observed-run-20260905T081825Z`
* **Schema Version**: `protocol-v5-resource-efficiency-raw-package-v1.0.0`
* **Execution Status**: `NOT_EXECUTED`
* **Cluster Measurement Status**: `NOT_EXECUTED`
* **Plan Binding SHA-256**: `eec8b92fcc6750b466cde3a15893babe2aacba71ef95ee4adb636ead75354466`
* **Blocker Codes**:
  1. `APPROVED_ORACLE_UNAVAILABLE`: Independent calibration oracle package not approved.
  2. `CGROUP_V2_REQUIRED`: Cluster preflight did not detect cgroup-v2 controllers on dedicated node.
  3. `CLUSTER_INELIGIBLE`: Cluster fails eligibility criteria.
  4. `CONFIRMATORY_FREEZE_INACTIVE`: Freeze contract is `NOT_FROZEN` in development phase.
  5. `IMAGE_DIGEST_UNVERIFIED`: Image state is `NOT_BUILT_OR_VERIFIED`.
  6. `KUBERNETES_VERSION_UNAVAILABLE`: Read-only preflight did not detect target cluster version.
  7. `NODE_CAPACITY_NOT_FROZEN`: Allocatable capacity contract remains `NOT_FROZEN`.
  8. `REQUIRED_API_ACCESS_MISSING`: Verified API access absent for dedicated target namespace.
  9. `WRONG_CLUSTER_FINGERPRINT`: Cluster label does not match `intent-spawner-eval-v5`.
  10. `WRONG_KUBERNETES_CONTEXT`: Current context is `orbstack` (expected `intent-spawner-eval-v5`).
  11. `WRONG_NODE_COUNT`: Target dedicated single-node count not satisfied.
* **Status**: Sealed with `SHA256SUMS`. Validated with `validate-package`.

### 4.3. Resource Envelope Calibration Package
* **Path**: `results_v5/protocol-v5.0.0/E4/e4-resource-envelope-observed-run-20260905T081833Z`
* **Schema Version**: `protocol-v5-resource-calibration-run-v1.1.0`
* **Execution Status**: `DRY_RUN` (`NOT_EXECUTED`)
* **Manual Review Status**: `NOT_APPLICABLE`
* **Status**: Sealed with `SHA256SUMS`. Validated with `validate-evidence`.

## 5. Verification Commands and Test Results

All commands executed within the dedicated worktree:

1. **Manifest Validation**:
   ```bash
   PYTHONPATH=. .venv/bin/python -m evaluation_v5.resource validate-manifest
   ```
   *Result*: `PASS` (16 families, 16 verified markers, static independence scan pass).

2. **Efficiency Contract Validation**:
   ```bash
   PYTHONPATH=. .venv/bin/python -m evaluation_v5.resource.efficiency_runner validate
   ```
   *Result*: `PASS` (640 primary trials, 16 families, 4 conditions, 10 repetitions).

3. **E4 Unit and Integration Tests**:
   ```bash
   PYTHONPATH=. .venv/bin/python -m pytest \
     tests/test_resource_efficiency_v5.py \
     tests/test_resource_envelope_v5.py \
     tests/test_protocol_v5_research_analysis.py
   ```
   *Result*: `129 passed in 14.07s`.

4. **Package Validation**:
   ```bash
   PYTHONPATH=. .venv/bin/python -m evaluation_v5.resource.efficiency_runner validate-package \
     results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-observed-run-20260905T081825Z
   PYTHONPATH=. .venv/bin/python -m evaluation_v5.resource validate-evidence \
     --result-dir results_v5/protocol-v5.0.0/E4/e4-resource-envelope-observed-run-20260905T081833Z
   ```
   *Result*: `PASS` on both packages.

5. **Research Analysis Discovery Audit**:
   ```bash
   PYTHONPATH=. .venv/bin/python -m evaluation_v5.analysis.research_analysis discover \
     --results-root results_v5/protocol-v5.0.0 \
     --freeze results_v5/protocol-v5.0.0/freezes/frozen-configuration.json
   ```
   *Result*: `PASS` (Discovered E4 candidate packages; adjudicated with explicit `NOT_EXECUTED` / `CLAIMS_NOT_PERMITTED` without errors).

## 6. Prerequisites for Live Execution

Live physical/cluster execution of E4 requires:
1. A dedicated Kubernetes cluster with context `intent-spawner-eval-v5`.
2. Exactly one dedicated node labeled `z2jh-context-demo.local/node-identity: e4-node-v1` and `z2jh-context-demo.local/dedicated-e4: "true"`, running cgroup v2 with `cpu`, `memory`, and `pids` controllers.
3. Disposable namespace `z2jh-context-demo` labeled `z2jh-context-demo.local/disposable-experiment-v5: "true"`.
4. An immutable container image built from `cluster_evaluation/Dockerfile.resource-v5` and pre-pulled onto the eligible node, with its SHA-256 digest verified and recorded in `cluster_evaluation/resource-v5-image-state.yaml`.
5. Confirmatory freeze activation: setting `confirmatory_freeze_status: FROZEN` in `benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml` and `benchmarks_v5/resource-envelope-freeze-contract-v1.yaml`.
6. Frozen node allocatable capacity recorded in `benchmarks_v5/resource-efficiency-capacity-v1.yaml`.
7. Sealed, approved independent resource envelope calibration package.
