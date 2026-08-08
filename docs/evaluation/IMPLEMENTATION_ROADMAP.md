# Research Evaluation Implementation Roadmap

## Purpose

This roadmap describes the integrated repository state. It outlines the complete implementation of core features, distinguishing runnable prototypes and the evaluation suite from future production multi-tenant deployments or history-aware scheduling.

---

## Integrated State

The repository contains:
* **Interactive Recommendation Preview UI**: Pre-spawn recommendation preview UI with Confirm, Edit, and Manual Override states, plus structured audit logging (`recommendation_audit`).
* **Notebook Container Image Matching**: Curator-managed notebook image catalog (`recommender/image-catalog.yaml`) with immutable digest pinning and capability matching.
* **Pluggable Recommender Engine**: Pluggable recommender framework supporting Rule-Based, External LLM (Google Gemini), and Self-Hosted LLM (Local Ollama / vLLM) with strict schema and policy validation.
* **Storage-Preserving Re-Provisioning**: Intent-aware re-provisioning (`/hub/reprovision`) allowing post-spawn workload updates with PVC storage retention.
* **Policy-Bounded Dynamic Resource Sizing**: Policy-bounded dynamic profile generation (`recommender/dynamic_resources.py` and `helm/dynamic-values.yaml`) for continuous CPU/RAM/GPU sizing.
* **Evaluation Protocol v4 & Benchmarking**: Comprehensive Evaluation Protocol v4 (`evaluation_v4/`) with a bilingual 60-intent gold standard dataset, multi-recommender benchmark runner, and statistical claim gates.

---

## Capability Matrix

| Capability | Component | Status | Implementation Evidence |
| :--- | :--- | :--- | :--- |
| **Recommendation Preview UI** | Interactive UI | Complete | `helm/proposed-values.yaml` (HTML options form & pre-spawn hook) |
| **User Decision & Audit Logging** | Audit Logging | Complete | `recommendation_audit` events, `PolicyValidator` |
| **Notebook Image Recommendation** | Image Matcher | Complete | `recommender/image-catalog.yaml`, capability matcher |
| **Pluggable Recommender Protocol** | Engine Core | Complete | `recommender/base.py`, `recommender/registry.py` |
| **Rule-Based Recommender** | Rule Backend | Complete | `recommender/rule_based.py`, `recommender/test_recommender.py` |
| **External LLM Backend (Gemini API)** | Gemini Backend | Complete | `recommender/external_llm.py`, `recommender/test_external_llm.py`, `helm/gemini-values.yaml` |
| **Self-Hosted LLM Backend (Ollama)** | Ollama Backend | Complete | `recommender/self_hosted_llm.py`, `recommender/test_self_hosted_llm.py`, `helm/ollama-values.yaml` |
| **Intent-Aware Re-Provisioning** | Re-provisioner | Complete | `/hub/reprovision`, `helm/reprovision-values.yaml`, PVC retention |
| **Dynamic Profile Generation** | Dynamic Sizing | Complete | `recommender/dynamic_resources.py`, `helm/dynamic-values.yaml` |
| **Evaluation Framework Protocol v4** | Protocol v4 | Complete | `evaluation_v4/`, bilingual 60-intent gold set, multi-recommender runner |
| **Statistical Claim Gates** | Claim Verification | Complete | `evaluation_v4/statistics.py`, bootstrap CI, Wilcoxon tests |

| **Preserved Cluster Evidence** | Baseline & v2 | Complete | `results/cluster/raw/`, `docs/evaluation/CLUSTER_RESULTS.md` |

---

## Method & Claim Boundaries

1. **Rule-Based vs. LLM Trade-offs**:
   * Rule-based recommendations offer sub-millisecond latency, zero external API costs, and deterministic predictability.
   * LLM backends provide rich semantic intent comprehension and unstructured context parsing, protected by strict JSON schema validation and automatic rule-based failover.

2. **Re-Provisioning Scope**:
   * The re-provisioning flow performs stop-and-recreate with PVC volume retention.
   * Kernel variables, active processes, and in-memory execution state are discarded by design (no live migration).

3. **Dynamic vs. Catalog Mode**:
   * Dynamic Mode provides continuous resource sizing but is strictly bound by administrator min/max policies and cluster quota headroom.
   * Fallback to Catalog Mode occurs automatically upon admission failure.

4. **Evaluation Scope**:
   * Evaluation Protocol v4 rigorously measures recommendation quality, cluster capacity impact, and user acceptance.
   * Local synthetic benchmarks and single-node Minikube results are kept cleanly separated.
