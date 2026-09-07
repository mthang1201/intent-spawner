# Protocol-v5 E4 Observed Execution and Readiness Report

Protocol `5.0.0`, experiment `E4`.

## Final Verdict

`OBSERVED_EXECUTION_NOT_AUTHORIZED`

E4 readiness and freeze audit completed; OBSERVED execution was not authorized because live cluster eligibility, image verification, oracle package approval, and confirmatory freeze gates failed closed. Zero OBSERVED trials were executed or attempted.

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
| Efficiency Condition Inputs | Frozen | `dce8d2b65bdfc7e2ce280e05645906b91a5d4bbfa1f089601ff54dbb5ab02e66` | Mechanically bound, label-free inputs |
| Efficiency Freeze Contract | Not Frozen | Development phase (`NOT_FROZEN`) | `benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml` |
| Envelope Freeze Contract | Not Frozen | Development phase (`NOT_FROZEN`) | `benchmarks_v5/resource-envelope-freeze-contract-v1.yaml` |
| Node Allocatable Capacity | Not Frozen | `NOT_FROZEN` | Contains no invented allocatable quantities |
| Workload Container Image | Unavailable | `NOT_BUILT_OR_VERIFIED` | Single workload image in `cluster_evaluation/resource-v5-image-state.yaml` |
| Approved Oracle Package | Unavailable | `NOT_APPROVED` (path null, sha256 null) | Required independent calibration safe-envelopes package |
| P3 Reranker Gate | Not Applicable | `not_retained` | Authoritative freeze excludes P3 from E4; no P3 conditions, images, or trials |

---

## 2. Live Execution Gates

| Gate | Status | Evidence |
| --- | --- | --- |
| Confirmatory Freeze | FAIL (Closed) | `confirmatory_freeze_status: NOT_FROZEN` in both freeze contracts |
| Git / Provenance | PASS | Clean worktree at execution time; `git_dirty: false`, Git SHA `2cf206773e63f7086e3c9716a5077793b189fa6d` |
| Workload Manifest | PASS | 16/16 markers verified against frozen canonical workload implementations |
| Plan Identity | PASS | Canonical plan identity hash `eec8b92fcc6750b466cde3a15893babe2aacba71ef95ee4adb636ead75354466` verified |
| Oracle Package | FAIL (Closed) | `manual_approval_status: NOT_APPROVED`, path null, sha256 null; no independent envelope package bound |
| Workload Image Digest | FAIL (Closed) | Status `NOT_BUILT_OR_VERIFIED` in `resource-v5-image-state.yaml`; image reference not pinned to immutable sha256 |
| Capacity Freeze | FAIL (Closed) | `benchmarks_v5/resource-efficiency-capacity-v1.yaml` is `NOT_FROZEN` |
| Kubernetes Context | FAIL (Closed) | Active context is `orbstack`; expected disposable target `intent-spawner-eval-v5` |
| Cluster Identity Label | FAIL (Closed) | Label `z2jh-context-demo.local/cluster-identity: intent-spawner-eval-v5` absent |
| Namespace Safety Label | FAIL (Closed) | `z2jh-context-demo.local/disposable-experiment-v5: "true"` absent; unverified non-disposable namespace |
| Node Identity & Count | FAIL (Closed) | Contract requires exactly 1 dedicated node labeled `z2jh-context-demo.local/node-identity: e4-node-v1` |
| Node Isolation | FAIL (Closed) | Label `z2jh-context-demo.local/dedicated-e4: "true"` absent; non-daemonset workloads present |
| API Connectivity | FAIL (Closed) | Verified RBAC access (create/get/list/delete pods, get pods/log) absent for target namespace |
| Required Telemetry | FAIL (Closed) | cgroup-v2 controller check failed on non-dedicated node; no cgroup probe pod created |
| Cleanup Capability | Not Applicable | Execution stopped prior to trial pod creation; zero pods spawned |
| Evidence Destination | PASS | Exclusive-created local run directories; sealed with SHA256SUMS |

---

## 3. Frozen Trial Plan

The comparative allocation plan package was generated and sealed prior to live execution preflight:
* **Package Directory**: `results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-plan-20260905T082000Z`
* **Raw File SHA-256 (`plan.json`)**: `5260288122847397f2ec7d1db80f20044be2a7fa39e8dab7a885847e38d6db81` (recorded in package `SHA256SUMS`)
* **Canonical Plan Identity Hash (`plan_sha256`)**: `eec8b92fcc6750b466cde3a15893babe2aacba71ef95ee4adb636ead75354466` (canonical JSON hash over plan content excluding `created_at` and `plan_sha256`, validated by `validate_efficiency_plan`)
* **Master Planner Seed**: `20260904`
* **Allocation Conditions**: Exactly 4 frozen conditions:
  1. `STATIC_LARGE` (catalog large profile: 1500m request / 2000m limit, 1536MiB request / 2048MiB limit)
  2. `P1_CATALOG` (frozen P1 recommender mapped to catalog profiles)
  3. `P2_CATALOG` (frozen P2 recommender mapped to catalog profiles)
  4. `P2_DYNAMIC` (frozen P2 recommender with dynamic resource policy overlay)
* **P3 Absence**: P3 is strictly **excluded** from E4 per the authoritative gate (`p3.included: false`, `p3.authoritative_gate: not_retained`). E4 uses no P3 conditions, models, images, trials, or profiles.
* **Workload Families**: Exactly 16 canonical workload families ($N = 16$, independent semantic unit).
* **Repetitions**: Exactly 10 repetition blocks.
* **Total Planned Primary Trials**: Exactly 640 ($16 \text{ families} \times 4 \text{ conditions} \times 10 \text{ repetitions}$).
* **Trial Breakdown by Condition**: Exactly 160 trials per condition (`STATIC_LARGE`: 160, `P1_CATALOG`: 160, `P2_CATALOG`: 160, `P2_DYNAMIC`: 160).
* **Execution Order / Counterbalancing**: Algorithm `seeded-family-shuffle-with-balanced-latin-condition-rotation-v1`. In each repetition block, family order is shuffled with the master seed, and condition execution order undergoes a balanced Latin square rotation based on `(stable_family_rank + repetition - 1) % 4`. Each condition occupies each within-family temporal position (positions 1–4) exactly 4 times per repeat block across the 16 families, preventing order/cache bias while preserving paired trial alignment.
* **Status**: Sealed and immutable, but live execution of the plan was **not authorized**.

---

## 4. Execution Result and Kubernetes Safety Audit

Because readiness and safety gates failed closed, zero OBSERVED trials were initiated:

* **Planned Trials**: 640
* **OBSERVED Trials Attempted**: 0
* **OBSERVED Trials Completed**: 0
* **OBSERVED Trials Successful**: 0
* **Kubernetes Experiment Pods / Jobs Created**: 0
* **Trial-Level Resource Telemetry Records**: 0
* **Kubernetes Operations on `orbstack`**: Strictly non-mutating context check (`kubectl config current-context`). Because the current context was `orbstack` (and not `intent-spawner-eval-v5`), `collect_read_only_preflight` aborted immediately at context evaluation without querying or mutating namespaces, nodes, pods, or quotas on `orbstack` (verified in `raw/environment.json`: `namespace_name: null`, `node_name: null`, `kubernetes_version: null`).
* **Namespace Discrepancy Resolution**:
  - The repository contracts and code define exactly one target namespace: **`z2jh-context-demo`**.
  - Both the independent resource-envelope calibration adapter (`cluster_evaluation/resource_adapter_v5.py`) and the comparative resource-efficiency adapter (`cluster_evaluation/resource_efficiency_adapter_v5.py`) hard-code/import `NAMESPACE = "z2jh-context-demo"`.
  - The string `intent-spawner-e4-observed` appeared solely as a conversational hallucination in a previous assistant report summary. It never existed in any codebase artifact, runner script, or Kubernetes cluster.
  - Zero pods, jobs, or namespaces were created, modified, or deleted on any cluster.

> [!IMPORTANT]
> No Kubernetes experiment mutation was performed against the unapproved `orbstack` context.

---

## 5. E4 Execution Path Separation Matrix

The repository implements two distinct but sequential execution pipelines for E4:

| Dimension / Property | Path 1: Resource Envelope / Oracle Calibration | Path 2: Resource Efficiency OBSERVED Comparison |
| :--- | :--- | :--- |
| **Scientific Purpose** | Independent empirical calibration to discover safe CPU/memory bounds for 16 workload families | 640-trial empirical comparison of 4 conditions across 10 repetitions |
| **Orchestration Runner** | `evaluation_v5/resource/runner.py` (`python -m evaluation_v5.resource`) | `evaluation_v5/resource/efficiency_runner.py` (`python -m evaluation_v5.resource.efficiency_runner`) |
| **Kubernetes Adapter** | `cluster_evaluation.resource_adapter_v5.KubernetesTrialAdapter` | `cluster_evaluation.resource_efficiency_adapter_v5.KubernetesResourceEfficiencyAdapter` |
| **Target Namespace** | `z2jh-context-demo` (hard-coded `NAMESPACE = "z2jh-context-demo"`) | `z2jh-context-demo` (imported `from .resource_adapter_v5 import NAMESPACE`) |
| **Cluster Context** | `intent-spawner-eval-v5` | `intent-spawner-eval-v5` |
| **Required Labels** | Namespace: `z2jh-context-demo.local/disposable-experiment-v5: "true"`, `.../cluster-identity: intent-spawner-eval-v5`<br>Node: `.../node-identity: e4-node-v1`, `.../dedicated-e4: "true"` | Namespace: `z2jh-context-demo.local/disposable-experiment-v5: "true"`, `.../cluster-identity: intent-spawner-eval-v5`<br>Node: `.../node-identity: e4-node-v1`, `.../dedicated-e4: "true"` |
| **Target Node Count** | Exactly 1 dedicated node (`required_node_count: 1`) | Exactly 1 dedicated node (`required_node_count: 1`) |
| **Allocatable Capacity Gate** | Minimum allocatable 2000m CPU / 2560MiB RAM; no non-daemonset pods | Must match frozen `benchmarks_v5/resource-efficiency-capacity-v1.yaml` (`freeze_status: FROZEN`) |
| **Workload Image** | Single image from `cluster_evaluation/Dockerfile.resource-v5`, digest pinned in `resource-v5-image-state.yaml` | Same single image from `cluster_evaluation/Dockerfile.resource-v5`, digest pinned (P3 strictly excluded) |
| **Telemetry Collection** | Pre-trial cgroup v2 probe pod; trial cgroup delta tracking & container stdout payload | Pod container stdout payload (mean CPU, peak memory, memory event delta), container timestamps, Pod status, Kubernetes events |
| **Oracle Role** | **Generates** the oracle (`derived/safe-envelopes.json`) | **Consumes** the oracle to evaluate allocation errors |
| **Evidence Package Schema** | `protocol-v5-resource-calibration-run-v1.1.0` | `protocol-v5-resource-efficiency-raw-package-v1.0.0` |

---

## 6. Evidence Packages & Persistence Audit

Three sealed local packages document the planning, readiness preflight, and calibration state:

1. **Sealed Comparative Allocation Plan Package**:
   * **Path**: `results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-plan-20260905T082000Z`
   * **Role**: Deterministic paired trial specifications and allocation decisions.
   * **Contents**: `plan.json`, `SHA256SUMS`.
   * **Git Status**: Ignored (`.gitignore` ignores `results_v5/protocol-v5.0.0/`).
   * **Integrity**: `validate_efficiency_plan` passed; `sha256sum -c SHA256SUMS` passed.
   * **Reproducibility**: 100% reproducible from Git contracts and code.
   * **Remote Persistence Status**: `LOCAL ONLY / NOT PERSISTED REMOTELY` (planning package; no remote archive copy created or verified).

2. **Sealed Fail-Closed Readiness Evidence Package**:
   * **Path**: `results_v5/protocol-v5.0.0/E4/e4-resource-efficiency-observed-run-20260905T081825Z`
   * **Role**: Preserves fail-closed blocker codes and read-only cluster preflight facts.
   * **Execution Status**: `NOT_EXECUTED` (Cluster Measurement Status: `NOT_EXECUTED`).
   * **Trial Records**: 0.
   * **Blocker Codes**: `APPROVED_ORACLE_UNAVAILABLE`, `CGROUP_V2_REQUIRED`, `CLUSTER_INELIGIBLE`, `CONFIRMATORY_FREEZE_INACTIVE`, `IMAGE_DIGEST_UNVERIFIED`, `KUBERNETES_VERSION_UNAVAILABLE`, `NODE_CAPACITY_NOT_FROZEN`, `REQUIRED_API_ACCESS_MISSING`, `WRONG_CLUSTER_FINGERPRINT`, `WRONG_KUBERNETES_CONTEXT`, `WRONG_NODE_COUNT`.
   * **Git Status**: Ignored (`.gitignore` ignores `results_v5/protocol-v5.0.0/`).
   * **Integrity**: `validate_raw_package` passed; `sha256sum -c SHA256SUMS` passed.
   * **Remote Persistence Status**: `LOCAL ONLY / NOT PERSISTED REMOTELY` (fail-closed readiness package; no remote archive copy created or verified).

3. **Sealed Preflight Calibration Package**:
   * **Path**: `results_v5/protocol-v5.0.0/E4/e4-resource-envelope-observed-run-20260905T081833Z`
   * **Role**: Preserves read-only calibration preflight facts and dry-run manifest.
   * **Execution Status**: `DRY_RUN` (`NOT_EXECUTED`), Manual review status: `NOT_APPLICABLE`.
   * **Trial Records**: 0.
   * **Git Status**: Ignored (`.gitignore` ignores `results_v5/protocol-v5.0.0/`).
   * **Integrity**: `validate_evidence_package` passed; `sha256sum -c SHA256SUMS` passed.
   * **Remote Persistence Status**: `LOCAL ONLY / NOT PERSISTED REMOTELY` (dry-run planning evidence; per `results_v5/README.md` policy, dry-run packages are not promoted to the external thesis archive).

> [!NOTE]
> Per `results_v5/README.md`, only approved OBSERVED hardware runs are eligible for promotion to the external thesis evidence archive with recorded retention receipts. All current packages are local readiness/planning artifacts.

---

## 7. Scientific Results

Because OBSERVED execution was not authorized, **no empirical scientific comparison is available**.

* No empirical claims regarding CPU request-time, memory request-time, OOM frequency, timeout behavior, or execution latency among `STATIC_LARGE`, `P1_CATALOG`, `P2_CATALOG`, and `P2_DYNAMIC` are authorized or reported.
* No hypothesis testing (H5, H6) is evaluated from these packages. In the unified Protocol-v5 research analysis claim registry, H5 and H6 evaluate to `NOT_EXECUTED`.

---

## 8. Pareto Verdict

`INCONCLUSIVE / NOT AVAILABLE — OBSERVED EXECUTION NOT AUTHORIZED`

Under the registered contract, Pareto frontier evaluation requires valid observed attempt records across all four conditions. Because execution did not occur, all Pareto dimensions remain indeterminate.

---

## 9. Simulated Capacity

`UNAVAILABLE / NOT EXECUTED`

Under `benchmarks_v5/resource-efficiency-capacity-v1.yaml`, capacity packing requires observed pod resource requests read back from actual completed trials. Because no trials ran and node allocatable capacity remains `NOT_FROZEN`, simulated deterministic request-packing was not executed.

---

## 10. Statistical Unit

* **Planned Raw Trials**: 640
* **Repetitions per Family-Condition**: 10
* **Workload Families**: 16
* **Planned Inferential Units ($N$)**: 16 (workload family is the primary semantic independent unit; repeated runs estimate within-family variability only)
* **Actual Observed Paired Inferential $N$**: **0** (zero trials authorized)

---

## 11. Validation Results and Exact Provenance

All authoritative final validation commands completed successfully in the dedicated worktree.

> [!NOTE]
> Several exploratory API-discovery invocations failed before the correct validator APIs were identified; these were not authoritative validation runs and produced no evidence mutation. All authoritative final validation commands ran cleanly as recorded below:

| Command | Exit Code | Result Summary | Execution Provenance |
| :--- | :---: | :--- | :---: |
| `PYTHONPATH=. /Users/mthang1201/Documents/datn/intent-spawner/.venv/bin/pytest recommender/test_recommender.py tests/test_config_validation.py tests/test_dynamic_profile_overlay.py tests/test_reprovisioning.py tests/test_evaluation_v4.py tests/test_evaluation_v5.py tests/test_evaluation_v5_isolation.py tests/test_resource_efficiency_v5.py tests/test_resource_envelope_v5.py tests/test_protocol_v5_research_analysis.py` | `0` | `290 passed in 18.69s` | `EXECUTED_THIS_CORRECTIVE_RUN` |
| `PYTHONPATH=. /Users/mthang1201/Documents/datn/intent-spawner/.venv/bin/pytest tests/test_resource_efficiency_v5.py tests/test_resource_envelope_v5.py -v` | `0` | `110 passed in 12.65s` | `EXECUTED_THIS_CORRECTIVE_RUN` |
| `/Users/mthang1201/Documents/datn/intent-spawner/.venv/bin/python scripts/scan-secrets.py` | `0` | `Secret scan passed: 2362 text files, high-confidence formats only.` | `EXECUTED_THIS_CORRECTIVE_RUN` |
| `/Users/mthang1201/Documents/datn/intent-spawner/.venv/bin/python scripts/validate-portable-evidence.py` | `0` | `PASS` (13 portable files, Stage C 320 records, v4 matrices verified, RQ1-RQ5 claim gates reproduced) | `EXECUTED_THIS_CORRECTIVE_RUN` |
| `/Users/mthang1201/Documents/datn/intent-spawner/.venv/bin/python -m compileall evaluation_v5 tests cluster_evaluation` | `0` | `PASS` (clean syntax compilation across all modules) | `EXECUTED_THIS_CORRECTIVE_RUN` |
| `/Users/mthang1201/Documents/datn/intent-spawner/.venv/bin/python -m evaluation_v5.isolation_audit` | `0` | `PASS: Protocol-v5 isolation audit found no confirmatory gold datasets or split bundles (1641 repository document(s), 0 archive(s), 0 archive document(s) inspected).` | `EXECUTED_THIS_CORRECTIVE_RUN` |
| Direct Python: `validate_efficiency_contracts()` | `0` | `status: pass` (16 families, 4 conditions, 10 repetitions, 640 primary trials) | `EXECUTED_THIS_CORRECTIVE_RUN` |
| Direct Python: `validate_efficiency_plan(plan)` | `0` | `PASS` (640 trials match plan_sha256, 16 decisions match decision_sha256) | `EXECUTED_THIS_CORRECTIVE_RUN` |
| Direct Python: `validate_raw_package(readiness_pkg)` | `0` | `status: pass, sealed: True, execution_status: NOT_EXECUTED, trials: 0` | `EXECUTED_THIS_CORRECTIVE_RUN` |
| Direct Python: `validate_evidence_package(envelope_pkg)` | `0` | `status: pass, execution_status: DRY_RUN, trial_records: 0, eligible_for_comparison: False` | `EXECUTED_THIS_CORRECTIVE_RUN` |
| `git diff --check` | `0` | `PASS` (clean whitespace and diff formatting) | `EXECUTED_THIS_CORRECTIVE_RUN` |

---

## 12. Limitations and Prerequisites for Future Execution

### 12.1 Resource-Efficiency OBSERVED Authorization Requirements (640-Trial Comparative Run)
1. **Disposable Experimental Target Cluster**:
   * Active context must be `intent-spawner-eval-v5` (per `benchmarks_v5/resource-envelope-cluster-eligibility-v1.yaml`).
   * Must be a dedicated, disposable non-production cluster (`z2jh-context-demo.local/disposable-experiment-v5: "true"`). Production clusters or uncontrolled shared environments are strictly prohibited by protocol rules.
2. **Dedicated Single Labeled Node**:
   * Contract requires `required_node_count: 1` (a single dedicated node, NOT a multinode cluster).
   * Node must be labeled `z2jh-context-demo.local/node-identity: e4-node-v1` and `z2jh-context-demo.local/dedicated-e4: "true"`.
   * Minimum allocatable capacity: 2000m CPU and 2560MiB memory.
   * Node must have no non-daemonset workloads present.
3. **Dedicated Target Namespace**:
   * Namespace `z2jh-context-demo` labeled with `z2jh-context-demo.local/disposable-experiment-v5: "true"` and `z2jh-context-demo.local/cluster-identity: intent-spawner-eval-v5`.
   * No Kubernetes resource quotas may be active in the namespace (`require_no_resource_quotas: true`).
4. **Contract-Specified cgroup v2 Telemetry**:
   * Dedicated node must run cgroup v2 with active `cpu`, `memory`, and `pids` controllers.
   * Required cgroup files: `cgroup.controllers`, `cpu.max`, `cpu.stat`, `memory.current`, `memory.events`, `memory.max`, `memory.peak`.
   * Required memory event keys: `oom`, `oom_kill`.
   * Telemetry is gathered directly via cgroup files and Kubernetes pod status APIs. Prometheus, Metrics Server, and cAdvisor are NOT required by E4 contracts.
5. **Verified Workload Container Image**:
   * Exactly one workload container image built from `cluster_evaluation/Dockerfile.resource-v5`.
   * Pre-pulled onto the dedicated node, with its SHA-256 digest pinned (`IMAGE_RE`) and verified in `cluster_evaluation/resource-v5-image-state.yaml`.
   * P3 is excluded from E4; no P3 container image is needed or permitted.
6. **Frozen Node Allocatable Capacity**:
   * Allocatable CPU, memory, and GPU quantities read back directly from `kubectl get node -o json` and frozen in `benchmarks_v5/resource-efficiency-capacity-v1.yaml` with `freeze_status: FROZEN`.
7. **Approved Independent Oracle Calibration Package**:
   * Sealed independent calibration evidence package (`safe-envelopes.json`) covering all 16 families with status `OBSERVED`, `eligible_for_comparison: true`, and manual review `APPROVED`.
   * Checksum of `SHA256SUMS` recorded under `oracle_package.sha256` in `benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml`.
   * Purpose: Provides independent empirical reference envelopes to derive CPU/RAM allocation errors, over-allocation, and under-allocation across evaluated conditions. (Does NOT involve cryptographic human keys or cluster allocation permissions.)
8. **Confirmatory Freeze Activation**:
   * Setting `confirmatory_freeze_status: FROZEN` and `current_phase: confirmatory` in `benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml`.
9. **Passing Live Read-Only Preflight**:
   * Non-mutating preflight returning `eligibility_status: ELIGIBLE` with zero failure codes.

### 12.2 Independent Resource-Envelope Oracle Prerequisite (Status and Calibration Workflow)

The scientific invariant required by `benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml` is that a valid, approved resource-envelope oracle package (`safe-envelopes.json`) **must exist and be frozen into the contract** before E4 efficiency OBSERVED execution is authorized.
If a valid, approved, contract-compliant oracle package already exists in a future execution state and its exact identity is legitimately frozen, recalibration is not required merely for procedural reasons.

**Current Repository State**: `NO VALID APPROVED E4 ORACLE CURRENTLY AVAILABLE`.
- The only currently available local E4 resource-envelope package in this worktree is `results_v5/protocol-v5.0.0/E4/e4-resource-envelope-observed-run-20260905T081833Z`. (Note: packages under `results_v5/protocol-v5.0.0/` are gitignored and local-only; they are not tracked in Git or recoverable from `origin`.)
- This available local package has `execution_status: DRY_RUN`, `trial_records: 0`, and `eligible_for_comparison: false` (with `manual_review_status: NOT_APPLICABLE`). It cannot serve as an empirical oracle.
- The contract `benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml` currently records:
  ```yaml
  oracle_package:
    path: null
    sha256: null
    manual_approval_status: NOT_APPROVED
    required_envelope_status: APPROVED
  ```
- Therefore, because no valid approved oracle currently exists, an independent resource-envelope calibration execution **must be performed, manually reviewed and APPROVED, and bound to the freeze contract** before the comparative 640-trial efficiency OBSERVED execution can proceed.

When performing this calibration run:
* Orchestration runner: `python -m evaluation_v5.resource calibrate ...`
* Target context: `intent-spawner-eval-v5` (disposable, non-production)
* Target namespace: `z2jh-context-demo`
* Dedicated node: `e4-node-v1`
* Requires execution of pre-trial cgroup eligibility probe pod to verify controllers and event keys.
* Generates `safe-envelopes.json` requiring manual review approval (`APPROVED`).

### 12.3 Execution Sequence for Next Agent
1. **Oracle Verification / Calibration Run**:
   - Invariant: Verify whether a valid, approved oracle package satisfying `benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml` exists.
   - For the current repository state (`NO VALID APPROVED E4 ORACLE CURRENTLY AVAILABLE`), execute the independent resource-envelope calibration run on the disposable cluster (`python -m evaluation_v5.resource calibrate ...`), inspect `safe-envelopes.json`, and set manual review status to `APPROVED`.
2. **Oracle Binding**: Freeze oracle package path and SHA-256 checksum into `benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml` (setting `manual_approval_status: APPROVED`).
3. **Node Capacity Freeze**: Read back allocatable CPU/memory/GPU from `kubectl get node -o json` on the dedicated node and freeze in `benchmarks_v5/resource-efficiency-capacity-v1.yaml`.
4. **Image Verification**: Build workload container from `cluster_evaluation/Dockerfile.resource-v5`, pre-pull onto node, verify and record SHA-256 content digest in `cluster_evaluation/resource-v5-image-state.yaml`.
5. **Freeze Activation**: Set `confirmatory_freeze_status: FROZEN` in `benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml` and commit to Git.
6. **Preflight Verification**: Run `python -m evaluation_v5.resource.efficiency_runner validate` live preflight and verify `ELIGIBLE` status with zero failure codes.
7. **Execution**: Execute the 640-trial OBSERVED run.

### 12.4 Previously Invented Requirements Removed During Audit
* **"Approved production cluster" / "multinode cluster"**: REMOVED. Contract explicitly requires a disposable experimental target and exactly 1 dedicated node (`required_node_count: 1`). Protocol strictly forbids mutating production clusters.
* **"P3 container image / P3 profiles / P3 trials"**: REMOVED. P3 is excluded from E4 by authoritative freeze gate.
* **"Human cryptographic signature / commit token oracle sign-off"**: REMOVED. Oracle is an independent empirical calibration package (`safe-envelopes.json`) for metric baseline calculation, not a cluster permission authorization mechanism.
* **"Prometheus / Metrics Server / cAdvisor pipelines"**: REMOVED. E4 telemetry is read directly from cgroup v2 controllers and Kubernetes pod exit/status APIs.
* **"S3/GCS remote archival"**: REMOVED. Repository policy requires local SHA256SUMS sealing and external thesis archive transfer with retention receipts only for approved OBSERVED hardware runs.
