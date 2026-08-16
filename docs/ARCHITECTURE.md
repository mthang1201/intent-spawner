# Architecture

This document explains the system architecture and data flows of the **Intent- and Context-Aware Profile Recommendation for Zero to JupyterHub** research prototype.

It covers the complete feature set:
* **Interactive Recommendation & Preview UI**
* **Curated Notebook Container Image Matching**
* **Pluggable Recommender Engine** (Rule-Based, External LLM API, Self-Hosted LLM)
* **Storage-Preserving Notebook Re-Provisioning**
* **Policy-Bounded Dynamic Resource Sizing**
* **Evaluation Protocol v4 & Benchmarking Suite**

---

## 1. System Overview

```mermaid
flowchart TD
    User([User / Data Scientist]) -->|1. Enters Workload Description| Form[Pre-Spawn Intent Form UI]
    Form -->|2. Submits Workload Context| HubServer[JupyterHub Config & Handlers]
    
    subgraph Recommender Layer [Pluggable Recommender Framework]
        HubServer --> RecommenderRegistry{Recommender Backend Registry}
        RecommenderRegistry -->|Zero-dep Deterministic| RuleBased[Rule-Based Recommender]
        RecommenderRegistry -->|HTTPS OpenAI API + Secret| ExtLLM[External LLM: Gemini 3.5 Flash]
        RecommenderRegistry -->|Local HTTP Inference| SelfLLM[Self-Hosted LLM: Ollama / vLLM]
        
        RuleBased & ExtLLM & SelfLLM --> SchemaCheck[Strict JSON Schema Validation]
        SchemaCheck --> CatalogCheck[Image Catalog & Policy Matcher]
    end
    
    CatalogCheck -->|3. Produces Recommendation| PreviewUI[Recommendation Preview UI]
    
    subgraph User Decision & Confirmation
        PreviewUI -->|Confirm / Accept| Hook[KubeSpawner Pre-Spawn Hook]
        PreviewUI -->|Edit Inputs| Form
        PreviewUI -->|Manual Override| AdminAllowlist[Admin Profile & Image Allowlist]
        AdminAllowlist --> Hook
    end
    
    subgraph Profile Allocation Modes
        Hook -->|Catalog Mode| FixedProfile[Discrete Sizing: Small / Medium / Large]
        Hook -->|Dynamic Mode| DynProfile[Policy-Bounded Continuous CPU/RAM/GPU]
    end
    
    FixedProfile & DynProfile -->|4. Pod Creation| K8sPod[Single-User Notebook Pod]
    K8sPod -.->|5. Workload Change / Re-provision| ReprovisionHandler["/hub/reprovision Stop-and-Recreate"]
    ReprovisionHandler -->|Retains PVC, Stops Pod| Hook
    
    subgraph Audit & Evaluation
        PreviewUI -.->|Structured Logs| AuditLog[(recommendation_audit Events)]
        AuditLog -.-> ProtocolV4[Evaluation Protocol v4 Analysis]
    end
```

---

## 2. Resource Profiles & Image Catalog

### Hardware Resource Profiles
The platform provides both discrete allowlisted profiles and continuous dynamic sizing:

| Profile | CPU Request | CPU Limit | Memory Request | Memory Limit | Target Workload |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `small` | 100m | 500m | 256M | 384M | Basic Python, light scripts, text exploration |
| `medium` | 500m | 1 CPU | 768M | 1G | Data analysis with pandas/NumPy (< 1GB datasets) |
| `large` | 1500m | 2 CPU | 1536M | 2G | ML modeling (scikit-learn, XGBoost) and larger datasets |
| `gpu_or_large` | 1500m | 2 CPU | 1536M | 2G | Deep learning; mapped safely to Large when GPU pool is unavailable |

### Curated Notebook Container Image Catalog
Images are pinned to immutable SHA-256 digests in [`recommender/image-catalog.yaml`](../recommender/image-catalog.yaml). Users cannot supply arbitrary registry references:

* **`minimal-python`**: Lightweight JupyterLab and Python base environment.
* **`scipy-data-science`**: NumPy, pandas, SciPy, scikit-learn, matplotlib, seaborn.
* **`pytorch-deep-learning`**: PyTorch, torchvision, torchaudio, and CUDA userspace libraries.
* **`tensorflow-deep-learning`**: TensorFlow, Keras, and CUDA userspace libraries.

---

## 3. Pluggable Recommender Architecture

All backends implement the `Recommender` protocol in [`recommender/base.py`](../recommender/base.py), accepting `RecommendationRequest` and returning a validated `SpawnRecommendation`. The engine and the Hub wiring are separate layers: [`recommender/jupyterhub_integration.py`](../recommender/jupyterhub_integration.py) validates the mounted package, creates the backend selected by `RECOMMENDER_BACKEND`, and exposes the async preview endpoint.

```text
RecommendationRequest
  ├── intent: str
  ├── dataset_size_gb: float
  └── code_context: str
       ↓
Recommender Protocol (recommender/base.py)
  ├── RuleBasedRecommender (recommender/rule_based.py)
  ├── ExternalLLMRecommender (recommender/external_llm.py)
  └── SelfHostedLLMRecommender (recommender/self_hosted_llm.py)
       ↓
PolicyValidator & Schema Validation (RESPONSE_SCHEMA)
       ↓
SpawnRecommendation
  ├── profile: str
  ├── image_id: str
  ├── image_reference: str
  ├── reasons: list[str]
  ├── score: float | None
  ├── backend_name: str
  ├── policy_version: str
  └── catalog_version: str
```

### Backend Specifications:
1. **Rule-Based Backend** (`rule_based.py`):
   * Fast, deterministic keyword and heuristic extraction.
   * Dataset thresholds: `>= 2.0GB` (+3 score -> `large`), `>= 0.5GB` (+1 score -> `medium`).
   * Training syntax (`.fit(`, `sklearn`) and data syntax (`pandas`, `read_csv`) triggers.
   * Serves as automatic fallback whenever network-based backends fail or timeout.

2. **External LLM Backend** (`external_llm.py`):
   * Uses OpenAI-compatible HTTP chat completions (the evaluated configuration used Google `gemini-3.5-flash` at `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`).
   * Authenticates with Kubernetes Secret `intent-spawner-external-llm` (`EXTERNAL_LLM_API_KEY`).
   * Built-in timeout controls, exponential backoff retries, JSON object response format, and fail-closed fallback to `RuleBasedRecommender`.

3. **Self-Hosted LLM Backend** (`self_hosted_llm.py`):
   * Designed for in-cluster or host-local inference (e.g. Ollama with `llama3`, vLLM, LocalAI).
   * Optional bearer token (`SELF_HOSTED_LLM_API_KEY`).
   * Explicit `SELF_HOSTED_LLM_ALLOW_INSECURE_HTTP: "true"` for private internal network boundaries.
   * Reuses identical strict response validation and rule-based safety fallback.

---

## 4. Interactive Pre-Spawn & Preview Workflow

```mermaid
sequenceDiagram
    actor User as User / Data Scientist
    participant UI as Pre-Spawn Options Form
    participant Hub as JupyterHub Server
    participant Backend as Pluggable Recommender
    participant Spawner as KubeSpawner
    participant K8s as Kubernetes API

    User->>UI: Input intent, dataset size, code snippet
    UI->>Hub: POST /hub/recommendation-preview
    Hub->>Backend: bounded async recommend(request)
    Backend-->>Hub: SpawnRecommendation
    Hub->>Hub: Store one-time user/generation-bound record
    Hub-->>UI: Recommendation + safe metadata + token
    
    alt User clicks Confirm
        User->>UI: Confirm recommendation
        UI->>Hub: Submit spawn decision + one-time token
        Hub->>Hub: Validate binding; never recompute LLM
        Hub->>Hub: Log privacy-minimized audit event
        Hub->>Spawner: Apply CPU, RAM, image, env & annotations
        Spawner->>K8s: Create notebook pod
        K8s-->>User: Pod Running -> JupyterLab UI
    else User clicks Edit Inputs
        User->>UI: Modify workload parameters
        UI->>UI: Invalidate preview, prompt for recalculation
    else User clicks Manual Override
        User->>UI: Select allowlisted profile/image
        UI->>Hub: Submit override decision (action=override)
        Hub->>Spawner: Apply overridden profile & image
        Spawner->>K8s: Create notebook pod
    end
```

---

## 5. Storage-Preserving Notebook Re-Provisioning

When user workloads change mid-session, users navigate to `/hub/reprovision`:
1. **Workload Update**: Users enter their new intent, dataset size, or code snippet.
2. **Replacement Preview**: The UI displays side-by-side comparisons of the current configuration vs. the proposed recommendation.
3. **Explicit Warning**: The UI clearly states that kernel state, memory variables, and background processes will be discarded.
4. **Stop-and-Recreate**:
   * The old notebook pod is gracefully terminated.
   * A new pod is spawned with the updated resource profile and image.
   * The user's home directory volume (`PersistentVolumeClaim`) is re-attached, preserving all files and saved datasets.

---

## 6. Policy-Bounded Dynamic Resource Sizing

When `RESOURCE_SELECTION_MODE: dynamic` is enabled via [`helm/dynamic-values.yaml`](../helm/dynamic-values.yaml):
* Instead of jumping directly between fixed tiers, continuous CPU, RAM, and GPU values are calculated within administrator-defined policies (`min_cpu`, `max_cpu`, `step_cpu`, `min_memory_mb`, `max_memory_mb`).
* **Static Per-Spawn Caps**: The shipped adapter validates min/max/step, GPU allowlists, and conservative policy caps. It does not query live `ResourceQuota`, current per-user use, or node headroom.
* **Fail-Safe Fallback**: If dynamic sizing violates those policy constraints, the system reverts to Catalog Mode. Kubernetes admission remains the final capacity authority.

---

## 7. Evaluation Protocol v4 & Benchmarking Suite

The evaluation layer ([`evaluation_v4/`](../evaluation_v4/)) provides a complete scientific benchmark suite:
* **Bilingual 60-Sample Gold Standard**: 12 development and 48 held-out English/Vietnamese samples grouped into 24 workload families.
* **Multi-Recommender Benchmarking**: Evaluates Rule-Based, External LLM (Gemini), Self-Hosted LLM (Ollama), and Baseline heuristics.
* **Separated Evidence Streams**:
  1. *Recommendation Quality*: Applied and raw-model profile/image accuracy, under/over-provisioning, latency, retries, and fallback.
  2. *System Effectiveness*: Observed Stage C workload success, OOM/timeout behavior, allocation, and cgroup-v2 utilization.
  3. *User Decision Impact*: Audit and re-provisioning schemas are implemented, but no real-user acceptance outcome is claimed.
* **Statistical Claim Gates**: Accuracy inference aggregates repeats to 48 held-out samples and resamples 20 workload-family clusters. Stage C inference aggregates ten repeats within eight executable families. Exact paired tests and Holm correction prevent repeated runs from being treated as independent samples.

The authoritative observed evaluation consists of four 48×5 recommender matrices and a 4×8×10 Stage C matrix. The combined claim matrix is in [`evaluation/PROTOCOL_V4_REVISED_EVALUATION_REPORT.md`](evaluation/PROTOCOL_V4_REVISED_EVALUATION_REPORT.md). The external pipeline's 21/240 raw-response coverage and 219 fallbacks are documented separately so fallback output is never credited to Gemini.

The fresh-clone portable core is checksum-locked by [`PROTOCOL_V4_PORTABLE_SHA256SUMS.txt`](evaluation/PROTOCOL_V4_PORTABLE_SHA256SUMS.txt) and validated by `scripts/validate-portable-evidence.py`. Stage C per-trial sidecars remain an external deep archive; the repository carries their full checksum manifest, not the ~95 MiB payload.
