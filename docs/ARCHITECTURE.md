# Architecture

This document explains the system architecture, component contracts, security boundaries, and evaluation framework of the **Intent- and Context-Aware Profile Recommendation for Zero to JupyterHub** research thesis prototype.

---

## 1. Primary Thesis Systems & Research Taxonomy

The thesis defines four primary systems:

| System ID | Name / Description | Pipeline Summary | Research Role |
| :--- | :--- | :--- | :--- |
| **B0** | **Default JupyterHub** | Manual administrator-configured `profileList` selection; no automated recommendation or intent parsing. | True operational baseline (RQ1 comparator). |
| **P1** | **Rule-Based Recommender** | Deterministic lexical keyword and heuristic scoring over intent text, dataset size, and code context. | Deterministic comparator and universal fallback (RQ1/RQ2 comparator). |
| **P2** | **Structured Intent + Hybrid Retrieval + Deterministic Constraints** | Natural language request → `StructuredIntent` → BM25 sparse + dense embeddings retrieval → Reciprocal Rank Fusion (RRF) → deterministic hard-constraint filtering → deterministic ranking → corpus resolution. | **Main research contribution** (RQ1/RQ2 primary system). |
| **P3** | **P2 + Retrieval-Grounded LLM Reranker** | Frozen P2 candidate generation and deterministic constraint evaluation → schema-validated LLM reranking of P2-feasible candidate IDs only → corpus resolution. | Optional / gated extension (evaluated under RQ3 if retained by headroom gate). |

### Motivating & Reference Evidence (Direct-LLM Experiments)
Direct end-to-end prompt-to-recommendation LLM backends (`external_llm` via Google Gemini 3.5 Flash and `self_hosted_llm` via local Ollama Llama 3) were evaluated under Protocol v4. They serve as **motivating and reference evidence** demonstrating the latency, reliability, and hallucination failure modes of unconstrained generation. Head-to-head external-vs-local LLM comparison is **not** a primary research question for the thesis.

---

## 2. End-to-End System Data Flow

The complete verified execution path for P2 and optional P3 recommendations follows this deterministic pipeline:

```mermaid
flowchart TD
    User([User / Data Scientist]) -->|1. Workload Intent, Dataset Size, Code Snippet| Form[Pre-Spawn Intent Form UI]
    Form -->|2. POST /hub/recommendation-preview| HubAPI[Authenticated Hub Preview API]

    subgraph P2_Pipeline [P2 Main Pipeline: recommender/p2_backend.py]
        HubAPI --> Extractor[StructuredIntent Extractor: local / LLM]
        Extractor -->|StructuredIntent Contract| Retrieval[Hybrid Retrieval: BM25 + Dense Embeddings]
        Retrieval -->|Fused Hits via RRF| Evaluator[Deterministic Constraint Evaluator]
        Evaluator -->|Feasible Candidate IDs Only| P2Ranker[Deterministic Preference Ranker]
    end

    subgraph P3_Extension [P3 Optional Reranker: recommender/p3_backend.py]
        P2Ranker -.->|P2 Feasible Ranking| P3Reranker[Grounded LLM Reranker]
        P3Reranker -.->|Validated Feasible IDs Only| P3Selector[P3 Selected Candidate]
    end

    P2Ranker -->|Selected Candidate ID| CorpusResolve[Candidate Corpus Resolution]
    P3Selector -.->|Selected Candidate ID| CorpusResolve
    
    subgraph Trust_Boundary [Trust & Policy Boundary]
        CorpusResolve -->|Admin-Owned EnvironmentCandidate| CandidateDoc[CandidateDocument]
        CandidateDoc -->|SpawnRecommendation| PolicyVal[PolicyValidator]
        PolicyVal -->|Validated Recommendation| PreviewStore[One-Time User-Bound Preview Store]
    end
    
    PreviewStore -->|3. JSON Preview Response + Token| PreviewUI[Recommendation Preview UI]
    
    subgraph Confirmation_Flow [User Decision & Confirmation]
        PreviewUI -->|4a. Confirm / Accept| FormConfirm[Spawn Form Submission]
        PreviewUI -->|4b. Edit Inputs| InvalidateToken[Invalidate Preview Token]
        InvalidateToken --> Form
        PreviewUI -->|4c. Manual Override| OverrideSelector[Admin Allowlist Selector]
        OverrideSelector --> FormConfirm
    end
    
    subgraph Pre_Spawn_Execution [Pre-Spawn Hook: Zero Recomputation]
        FormConfirm -->|5. POST /hub/spawn with Token| PreSpawnHook[KubeSpawner pre_spawn_hook]
        PreSpawnHook -->|Consume Single-Use Token| ValidateBinding[Validate User Binding & Generation]
        ValidateBinding -->|Apply Profile & Digest-Pinned Image| SpawnerConfig[Spawner Hardware & Image Setup]
    end
    
    SpawnerConfig -->|6. K8s Pod Lifecycle| K8sPod[Single-User Notebook Pod]
    
    subgraph Audit_Telemetry [Audit & Operational Telemetry]
        ValidateBinding -.->|Bounded Low-Cardinality Event| AuditLog[(recommendation_audit Event)]
        AuditLog -.-> PodAnnotations[intent-spawner.local/* Pod Annotations]
    end
```

---

## 3. Detailed Component Architecture

### 3.1 Structured Intent Extraction (`recommender/structured_intent.py`, `recommender/local_structured_intent.py`)
* Converts untrusted natural language user text into a strictly schema-validated `StructuredIntent` dataclass.
* Captures: `task_types`, `required_features`, `preferred_features`, `forbidden_features`, `required_frameworks`, `preferred_frameworks`, `required_libraries`, `preferred_libraries`, and `resource_constraints` (`gpu_requirement`, `minimum_cpu_cores`, `minimum_memory_gb`, `dataset_size_gb`).
* Explicit user-supplied dataset size from form inputs overrides any inferred value.
* Any extractor failure or malformed JSON deterministically degrades to `DeterministicStructuredIntentExtractor` preserving explicit numeric facts with zero hallucinated requirements.
* Prompt injection in user text is treated as data, not instruction; candidate IDs, image references, and Kubernetes resource values are prohibited in extracted output.

### 3.2 Hybrid Lexical + Semantic Retrieval (`recommender/sparse_retrieval.py`, `recommender/dense_retrieval.py`, `recommender/hybrid_retrieval.py`)
* **Sparse Channel**: BM25 ranking over tokenized candidate documents derived exclusively from administrator-curated images and profiles.
* **Dense Channel**: Dense vector similarity using versioned feature-hash or embedding providers over candidate representation text.
* **Reciprocal Rank Fusion (RRF)**: Combines ranked lists using $RRF(d) = \sum_{m \in \{sparse, dense\}} \frac{w_m}{k_{rrf} + r_m(d)}$ with deterministic tie-breaking.
* If either channel fails, the retriever gracefully uses available hits or falls back to rule-based recommendations.

### 3.3 Deterministic Constraint Evaluation & Ranking (`recommender/constraint_evaluator.py`)
* Evaluates all retrieved candidate IDs against administrator policy and extracted constraints:
  1. **Hard Constraints**: GPU requirement (e.g. required vs. unavailable in catalog), CPU lower bound, memory lower bound, dataset size sufficiency, and forbidden feature conflicts.
  2. **Soft Preferences**: Weighted matching of preferred libraries, frameworks, and suitability tags.
* Candidates violating any hard constraint are marked `feasible = False` and strictly excluded from ranking.
* Feasible candidates are deterministically ranked by composite score: $Score = w_{retrieval} \cdot Score_{RRF} + w_{soft} \cdot Score_{soft}$.
* If zero candidates are feasible, `no_feasible_candidate` triggers a fallback requiring mandatory manual override.

### 3.4 Optional P3 Retrieval-Grounded LLM Reranking (`recommender/p3_reranker.py`, `recommender/p3_backend.py`)
* Consumes the complete list of deterministically feasible candidates from P2.
* Reranker prompt provides candidate facts (hardware specs, installed packages) and user context.
* Strict schema validation rejects: unknown candidate IDs, omitted candidate IDs, duplicate candidate IDs, or out-of-bounds scores.
* The model cannot alter resource values, choose arbitrary images, or revive infeasible candidates.
* Any network error, timeout, or schema mismatch immediately degrades to the exact P2 deterministic ranking.

### 3.5 Candidate Corpus Resolution & Trust Boundary (`recommender/candidate_corpus.py`, `recommender/policy.py`)
* Every candidate ID maps to an immutable `CandidateDocument` created from administrator configuration.
* Converts to a trusted `EnvironmentCandidate` and `SpawnRecommendation`.
* `PolicyValidator` acts as the final gate: verifies profile allowlists, pinned image SHA-256 digests, policy versions, and catalog versions before any preview can be issued.

### 3.6 Interactive Preview & Pre-Spawn Binding (`recommender/jupyterhub_integration.py`)
* Previews are server-side, single-use, generation-bound, and tied to the authenticated user with a 30-minute TTL.
* Modifying input parameters invalidates existing preview tokens.
* `pre_spawn_hook` validates the token and consumes it. **Zero recommendation or LLM recomputation occurs during pod spawn.**
* Telemetry and pod annotations log only bounded low-cardinality metadata (`intent-spawner.local/*`), never raw user text, prompts, or code.

---

## 4. Implementation Matrix

| System / Component | Source Files | Unit & Integration Tests | Configuration / Version | Supported Research Question | Known Limitations | Experimental Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **B0: Default JupyterHub** | `helm/baseline-values.yaml`, `scripts/install-baseline.sh` | `tests/test_helm_recommender_deployment.py` | `baseline-values.yaml` (JupyterHub 4.0.0) | RQ1 (Human user baseline) | No recommendation; relies entirely on user hardware intuition. | Verified operational baseline configuration. |
| **P1: Rule-Based Recommender** | `recommender/rule_based.py`, `recommender/recommender.py` | `tests/test_p1_regression.py` (35 tests), `recommender/test_recommender.py` | `rule-based-v1`, `RECOMMENDER_BACKEND=rule_based` | RQ1, RQ2 (Deterministic baseline comparator) | Coarse keyword matching; cannot infer complex or unlisted dependencies. | Frozen comparator & universal fallback; 100% regression locked. |
| **P2: Structured Intent Extraction** | `recommender/structured_intent.py`, `recommender/local_structured_intent.py` | `tests/test_structured_intent_extractor.py` (16 tests) | `structured-intent-v1`, `P2_STRUCTURED_EXTRACTOR=local\|llm` | RQ1, RQ2 (Intent understanding) | Local extractor uses rule-based parsing; LLM extractor depends on OpenAI API availability. | Implemented & verified with automatic failover to explicit values. |
| **P2: Hybrid Sparse+Dense Retrieval** | `recommender/sparse_retrieval.py`, `recommender/dense_retrieval.py`, `recommender/hybrid_retrieval.py`, `recommender/local_embeddings.py` | `tests/test_sparse_retrieval.py`, `tests/test_dense_retrieval.py`, `tests/test_hybrid_retrieval.py` (41 tests) | `sparse-bm25-v1`, `dense-index-v1`, `hybrid-retriever-v1`, $k_{rrf}=60$ | RQ2 (Retrieval precision & recall) | Small candidate catalog size limits vocabulary diversity in feature hashing. | Implemented, deterministic, and verified. |
| **P2: Constraint Evaluation & Ranking** | `recommender/constraint_evaluator.py`, `recommender/candidate_corpus.py` | `tests/test_constraint_evaluator.py`, `tests/test_candidate_corpus.py` (34 tests) | `constraint-evaluation-v2`, `environment-candidate-v1` | RQ1, RQ2 (Safe allocation) | Static discrete profile matching; requires manual override when catalog lacks capability. | Fully implemented and strictly validated. |
| **P2: Integrated Backend** | `recommender/p2_backend.py` | `tests/test_p2_contracts.py` (41 tests), `tests/test_p2_backend_integration.py` (6 tests) | `p2-hybrid-v1.0.0`, `p2-pipeline-v1.0.0` | RQ1, RQ2 (Core contribution) | Degrades to P1 on infrastructure failure or empty retrieval. | Fully implemented, tested, and integrated into Hub runtime. |
| **P3: Grounded LLM Reranker** | `recommender/p3_reranker.py`, `recommender/p3_backend.py` | `tests/test_p3_reranker.py` (23 tests), `tests/test_p3_backend_integration.py` (7 tests) | `p3-reranker-v1.0.0`, `P3_RERANKER_MODE=llm\|deterministic` | RQ1, RQ3 (Feasible candidate reranking) | Dependent on external LLM response rate; evaluated headroom gate showed no retention gain. | Fully implemented and tested; marked `not_retained` by empirical gate. |
| **Policy & Trust Validation** | `recommender/policy.py`, `recommender/models.py` | `tests/test_p2_contracts.py`, `tests/test_adversarial.py` | `resource-image-policy-v1`, `spawn-recommendation-v1` | Safety & Governance | Rejects any unlisted image/profile; strictly enforce administrator catalog. | Immutable trust boundary protecting KubeSpawner. |
| **JupyterHub Integration & Preview** | `recommender/jupyterhub_integration.py`, `recommender/reliability.py` | `tests/test_adversarial.py` (10 tests), `tests/test_recommender_backends_integration.py`, `tests/test_recommender_reliability_live.py` | `recommendation-preview-v2`, TTL=1800s, max_entries=1000 | System usability & audit | In-memory token store; multi-replica Hub requires shared cache in production. | Fully functional with zero pre-spawn recomputation. |
| **Dynamic Resource Sizing** | `recommender/dynamic_resources.py` | `recommender/test_dynamic_resources.py` (39 tests), `tests/test_dynamic_profile_overlay.py` (7 tests) | `dynamic-resource-policy-v1`, `helm/dynamic-values.yaml` | System Efficiency | Static per-spawn caps; does not query live cluster node headroom. | Opt-in policy-bounded extension. |
| **Notebook Re-Provisioning** | `recommender/jupyterhub_integration.py` | `tests/test_reprovisioning.py` (7 tests) | `/hub/reprovision` handler | Session lifecycle | Pod stop-and-recreate with PVC reattachment; does not preserve in-memory kernel RAM. | Implemented and verified on Kubernetes. |
| **Direct External LLM Adapter** | `recommender/external_llm.py` | `recommender/test_external_llm.py` (28 tests), `recommender/test_reliability.py` (19 tests) | Google Gemini 3.5 Flash via OpenAI endpoint | Reference / Motivating Evidence (Protocol v4) | 8.75% raw valid response coverage (219 fallbacks) in 240-trial matrix. | Historical baseline reference; isolated from Gemini accuracy claims. |
| **Direct Self-Hosted LLM Adapter** | `recommender/self_hosted_llm.py` | `recommender/test_self_hosted_llm.py` (9 tests) | Ollama Llama 3 local inference | Reference / Motivating Evidence (Protocol v4) | 9.20s median latency; no profile accuracy gain over rules. | Historical baseline reference. |

---

## 5. Security and Correctness Review

| Risk / Threat Area | Defense & Verification Mechanism | Implemented Safeguard Location | Test Coverage |
| :--- | :--- | :--- | :--- |
| **Prompt Injection** | User input treated strictly as data. Extraction and reranker prompts explicitly forbid instruction execution. Output fields are schema-validated against fixed types/enums; raw commands cannot bypass constraints. | `structured_intent.py:55-64`, `p3_reranker.py:58-74` | `tests/test_adversarial.py` |
| **Invented Candidate IDs** | Output candidate IDs are checked against valid administrator corpus IDs. P3 reranker rejects unknown, duplicate, or missing IDs and validates candidate count. | `p3_reranker.py:384-388`, `p2_backend.py:315-321` | `tests/test_p3_reranker.py`, `tests/test_adversarial.py` |
| **Arbitrary Image / Profile References** | AI models cannot output image URLs or raw resources. Output must resolve to a `CandidateDocument` from `image-catalog.yaml`. `PolicyValidator` verifies profile against allowlist and image against pinned SHA-256 digest. | `policy.py:55-73`, `candidate_corpus.py` | `tests/test_adversarial.py`, `tests/test_p2_contracts.py` |
| **Stale Embedding Index** | Dense and hybrid index versions and SHA-256 checksums are tracked in metadata. Preview generation includes index versions/checksums; changes invalidate unconsumed tokens. | `dense_retrieval.py`, `jupyterhub_integration.py:163-170` | `tests/test_dense_retrieval.py`, `tests/test_hybrid_retrieval.py` |
| **Stale Candidate Catalog** | Catalog version checked at startup and during `PolicyValidator.validate()`. Mismatched catalog version raises an immediate validation error. | `policy.py:70-71`, `p2_backend.py:263-264` | `tests/test_candidate_corpus.py`, `tests/test_config_validation.py` |
| **Malformed StructuredIntent** | Strict JSON decoding and schema validation (`_strict_json_object`). Parsing errors trigger safe degradation to `DeterministicStructuredIntentExtractor` with explicit values only. | `structured_intent.py:247-277, 478-484` | `tests/test_structured_intent_extractor.py` |
| **Malformed Reranker Output** | Reranker validates JSON structure, required fields, score bounds $[0.0, 1.0]$, and ID completeness. Any failure immediately degrades to exact P2 ranking. | `p3_reranker.py:348-435`, `p3_backend.py:530-545` | `tests/test_p3_reranker.py` |
| **Provider Timeout / Failure** | Explicit deadlines, retry backoff, and non-blocking timeout handling (`network_work_deadline`). Failures degrade smoothly to deterministic fallback without blocking the user. | `structured_intent.py:435-484`, `p3_reranker.py:517-560`, `reliability.py` | `recommender/test_reliability.py`, `tests/test_p2_backend_integration.py` |
| **Embedding Failure** | Dense retrieval errors catch exceptions and allow sparse-only fallback or complete P1 fallback with `infrastructure_provider_failure` category. | `p2_backend.py:519-525, 640-650` | `tests/test_dense_retrieval.py`, `tests/test_p2_backend_integration.py` |
| **Sparse / Dense Channel Failure** | RRF handles partial hits; empty fused results trigger graceful fallback with `retrieval_empty` category. | `p2_backend.py:562-570`, `hybrid_retrieval.py` | `tests/test_hybrid_retrieval.py` |
| **No Feasible Candidate** | Deterministic evaluator checks hard constraints. If all candidates are infeasible, `no_feasible_candidate` or `unsupported_catalog` flag requires explicit manual user override. | `constraint_evaluator.py`, `p2_backend.py:581-596`, `jupyterhub_integration.py:260-264` | `tests/test_constraint_evaluator.py`, `tests/test_p2_backend_integration.py` |
| **Mandatory GPU without Catalog GPU** | Evaluator marks all candidates violating `gpu_not_available` as infeasible. Sets `no_feasible_candidate=True` and blocks automated spawn without manual override. | `constraint_evaluator.py:180-195` | `tests/test_constraint_evaluator.py` |
| **Privacy Regression** | Free-form intent text, code context, prompts, retrieved documents, and raw model completions are transient and **never** stored in preview records, logs, or pod metadata. | `jupyterhub_integration.py:195-206, 430-446` | `tests/test_adversarial.py`, `tests/test_historical_evidence_immutability.py` |
| **Raw User Text in Logs** | `recommendation_audit` logs only low-cardinality metadata (event UUID, backend name, profile, image ID, latency, attempt count, fallback category). | `jupyterhub_integration.py:430-447` | `tests/test_adversarial.py` |
| **Policy Bypass** | Every recommendation passes through `PolicyValidator` before preview and is verified again during `pre_spawn_hook`. Forged form submissions are rejected. | `jupyterhub_integration.py:188, 301-339` | `tests/test_adversarial.py` |
| **Preview Replay** | Preview tokens are single-use (`consume=False` on check, popped from preview dictionary upon spawn). Replay attempts fail with `already used`. | `jupyterhub_integration.py:342` | `tests/test_adversarial.py:27-35` |
| **Preview Invalidation on Edit** | Client UI clears preview and resets state on form edit. Server validates exact token binding; mismatched options fail pre-spawn validation. | `jupyterhub_integration.py:335-336` | `tests/test_adversarial.py:99-110` |
| **Manual Override Integrity** | Overrides must select from `PROFILE_RESOURCES` and `image-catalog.yaml`. Unlisted profiles or image IDs are strictly rejected. | `jupyterhub_integration.py:268-274, 337-338` | `tests/test_adversarial.py:45-54` |
| **No Pre-Spawn Recomputation** | `pre_spawn_hook` retrieves the existing confirmed preview decision from memory. It performs zero model inference, zero network calls, and zero recomputation. | `jupyterhub_integration.py:301-344` | `tests/test_recommender_backends_integration.py` |

---

## 6. Observed Evaluation Evidence Summary

The repository maintains frozen, reproducible empirical evidence collected under standardized protocols:

1. **Protocol v4 Recommender Matrix (48 held-out samples × 5 repeats)**:
   - Rule-based P1 achieved the highest observed acceptable profile accuracy (79.17%), with 100% image accuracy on benchmark triggers.
   - External LLM (Gemini 3.5 Flash) experienced 219/240 fallbacks (8.75% raw valid completion rate), demonstrating the necessity of fail-closed fallbacks.
   - Self-hosted LLM (Ollama Llama 3) achieved 100% completion reliability (240/240) with 9.20s median latency, but showed no statistically significant profile accuracy improvement over rule-based logic.
2. **Protocol v4 Stage C Cluster Impact Matrix (4 methods × 8 workload families × 10 repeats = 320 pod runs)**:
   - Overprovisioned `static_large` completed 80/80 workloads successfully.
   - Adaptive sizing (`rule_based` and `ollama`) completed 50/80 workloads, achieving substantial CPU/memory reservation savings while trading off against OOM events on memory-intensive unflagged workloads.
   - Underprovisioned `static_small` completed only 29/80 workloads (51 OOM failures).
3. **P2 Evaluation v1 (48 held-out samples)**:
   - P2 demonstrated robust structured extraction, zero hard-constraint violations on feasible cases, and correct handling of unsupported requirements via explicit manual override.
4. **P3 Incremental Evaluation v1**:
   - P3 reranking was tested over frozen P2. Because P2 already achieved optimal candidate selection on feasible cases, LLM reranking showed zero headroom gain and was marked `not_retained` by the preregistered research gate.

---

## 7. Remaining Work for Thesis Completion

The technical prototype, backend implementations (B0, P1, P2, P3), safety boundaries, and offline benchmarks are fully implemented and verified. The remaining tasks for thesis submission are:

1. **Conduct the Human User Study (RQ1)**:
   - Execute the preregistered Latin-square pseudonymous user study comparing B0 (Default JupyterHub) against P1 and P2 across representative data science tasks (`final-evaluation-protocol-v1.0.0`).
   - Collect interaction counts, time-to-appropriate-selection, and subjective task completion metrics.
2. **Final Confirmatory Evaluation Freeze**:
   - Execute `python3 -m evaluation_final.runner freeze` and record the final frozen dataset and candidate corpus checksums.
3. **Thesis Manuscript Compilation**:
   - Transcribe empirical matrices and confidence intervals into thesis chapter results.
   - Discuss architectural trade-offs: deterministic rule constraints vs. statistical retrieval vs. generative LLM risks.
