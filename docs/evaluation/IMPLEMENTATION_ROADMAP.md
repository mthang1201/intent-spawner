# Research Evaluation Implementation Roadmap

Last updated: 2026-08-16, after the Protocol-v4 external matrix, combined analysis, Stage C confirmatory run, and recommendation-preview JavaScript regression fix.

## Purpose

This roadmap describes the integrated repository state. It outlines the complete implementation of core features, distinguishing runnable prototypes and the evaluation suite from future production multi-tenant deployments or history-aware scheduling.

---

## Integrated State

The repository contains:
* **Interactive Recommendation Preview UI**: Authenticated async server-side preview with Confirm and Manual Override states, one-time user/generation-bound tokens, and privacy-minimized audit logging (`recommendation_audit`).
* **Notebook Container Image Matching**: Curator-managed notebook image catalog (`recommender/image-catalog.yaml`) with immutable digest pinning and capability matching.
* **Pluggable Recommender Engine**: Pluggable recommender framework supporting Rule-Based, External LLM (Google Gemini), and Self-Hosted LLM (Local Ollama / vLLM) with strict schema and policy validation.
* **Storage-Preserving Re-Provisioning**: Intent-aware re-provisioning (`/hub/reprovision`) allowing post-spawn workload updates with PVC storage retention.
* **Policy-Bounded Dynamic Resource Sizing**: Policy-bounded dynamic profile generation (`recommender/dynamic_resources.py` and `helm/dynamic-values.yaml`) for continuous CPU/RAM/GPU sizing.
* **Evaluation Protocol v4 & Benchmarking**: Completed four-method held-out evaluation and 320-trial Stage C run using a bilingual 60-sample gold dataset, append-only evidence, strict validation, family-aware statistics, and explicit claim gates.

---

## Capability Matrix

| Capability | Component | Status | Implementation Evidence |
| :--- | :--- | :--- | :--- |
| **Recommendation Preview UI** | Interactive UI | Complete in demo wiring | `recommender/jupyterhub_integration.py`, `helm/proposed-values.yaml` |
| **User Decision & Audit Logging** | Audit Logging | Complete | `recommendation_audit` events, `PolicyValidator` |
| **Notebook Image Recommendation** | Image Matcher | Complete | `recommender/image-catalog.yaml`, capability matcher |
| **Pluggable Recommender Protocol** | Engine Core | Complete | `recommender/base.py`, `recommender/registry.py` |
| **Backend-to-Hub Wiring** | Deployment Runtime | Complete in demo wiring | Mounted v2 package, startup checksum/version validation, `RECOMMENDER_BACKEND`, bounded async executor |
| **Rule-Based Recommender** | Rule Backend | Complete | `recommender/rule_based.py`, `recommender/test_recommender.py` |
| **External LLM Backend (Gemini API)** | Gemini Backend | Complete | `recommender/external_llm.py`, `recommender/test_external_llm.py`, `helm/gemini-values.yaml` |
| **Self-Hosted LLM Backend (Ollama)** | Ollama Backend | Complete | `recommender/self_hosted_llm.py`, `recommender/test_self_hosted_llm.py`, `helm/ollama-values.yaml` |
| **Intent-Aware Re-Provisioning** | Re-provisioner | Complete | `/hub/reprovision`, `helm/reprovision-values.yaml`, PVC retention |
| **Dynamic Profile Generation** | Dynamic Sizing | Complete | `recommender/dynamic_resources.py`, `helm/dynamic-values.yaml` |
| **Evaluation Framework Protocol v4** | Protocol v4 | Complete | `evaluation_v4/`, bilingual 60-sample gold set, multi-recommender runner |
| **Statistical Claim Gates** | Claim Verification | Complete | `evaluation_v4/statistics.py`, clustered bootstrap intervals, exact McNemar/Wilcoxon tests, Holm correction |
| **Preserved Cluster Evidence** | Baseline & v2 | Complete | `results/cluster/raw/`, `docs/evaluation/CLUSTER_RESULTS.md` |
| **Protocol-v4 External Matrix** | Observed Stage B | Complete with limitations | 240 `gemini-3.5-flash` trials; 21 raw completions and 219 rule fallbacks; `PROTOCOL_V4_EXTERNAL_LLM_LIVE_REPORT.md` |
| **Protocol-v4 Stage C** | Observed cluster effects | Complete | 4 methods × 8 families × 10 repeats; `STAGE_C_CONFIRMATORY_REPORT.md` |
| **Combined RQ1-RQ5 Analysis** | Claim Matrix | Complete | `PROTOCOL_V4_REVISED_EVALUATION_REPORT.md` |
| **Portable Protocol-v4 Core** | Fresh-clone evidence | Complete | 3 recommendation matrices, Stage C summary/plan/manifests, checksum validator and reproduced headline analysis |
| **Real-User Acceptance Study** | User Outcomes | Not executed | Audit schema exists, but no observed acceptance or task-success corpus is claimed |
| **Production Multi-Node Validation** | Generalization | Future work | Current Stage C evidence is limited to one disposable node and eight executable families |

---

## Method & Claim Boundaries

1. **Rule-Based vs. LLM Trade-offs**:
   * Rule-based recommendations offer sub-millisecond latency, zero external API costs, and deterministic predictability.
   * LLM backends accept richer semantic context and are protected by strict JSON validation and rule fallback, but the observed study did not establish a general LLM profile-quality improvement. External applied results were mostly fallback-driven; local Ollama added roughly nine seconds median latency.

2. **Re-Provisioning Scope**:
   * The re-provisioning flow performs stop-and-recreate with PVC volume retention.
   * Kernel variables, active processes, and in-memory execution state are discarded by design (no live migration).

3. **Dynamic vs. Catalog Mode**:
   * Dynamic Mode provides continuous resource sizing bounded by administrator min/max/step rules, GPU allowlists, and static per-spawn caps.
   * The shipped adapter does not query live quota or node headroom. Policy rejection falls back to Catalog Mode; Kubernetes admission is still authoritative.

4. **Evaluation Scope**:
   * Protocol v4 measures recommendation quality, LLM reliability/latency, and applied single-node cluster effects. Real-user acceptance, monetary cost, energy, and provider-retention outcomes remain unmeasured.
   * Offline recommendation evidence and observed single-node cluster evidence are kept separate. Repeats measure stability/runtime variation and are not counted as independent accuracy samples.
   * The authoritative claim matrix marks RQ1, RQ2, and RQ4 claimable; RQ3 partially claimable; and RQ5 claimable with limitations.

5. **Evidence Packaging**:
   * The ~3.9 MiB portable core is committed and hash-validated from a fresh clone.
   * The ~95 MiB Stage C per-trial sidecars remain an external deep archive; their complete checksum manifest is included in the portable core.
   * `NEXT_AGENT_CONTEXT.md` and similar handoff notes are historical documentation, not substitutes for evidence.
