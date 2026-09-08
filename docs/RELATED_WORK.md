# Background and Related Work

**Repository-informed thesis draft**  
**System:** Intent-Spawner  
**Protocol context:** Protocol-v5  
**Literature checked:** 8 September 2026

> **Evidence boundary.** This chapter explains the background, positions the system, and records the literature synthesis. It does not report Protocol-v5 confirmatory results. In the current repository, the confirmatory human study (E3), cluster resource-efficiency study (E4), and image-storage measurements (E5 storage) are `NOT_EXECUTED`. Protocol-v4 artifacts are historical/formative evidence only. B0 is a manual-selection baseline and therefore has no ranking output; P3 was a formative development branch and was not retained. The principal proposed method is P2.

## Repository sources and scope boundary

The repository was treated as the authority for system behavior. In particular, this draft follows the current [README](../README.md), [architecture description](ARCHITECTURE.md), [Protocol-v5 architecture](evaluation/PROTOCOL_V5_ARCHITECTURE.md), [final audit](evaluation/final/FINAL_REPORT.md), JupyterHub integration, candidate catalog, and resource policy. Where an older design document differs from executable integration code or the current architecture, the current architecture and code take precedence.

The resulting problem statement is:

> Users can describe the work they intend to do—such as lightweight Python exploration, memory-heavy analysis, or framework-specific machine learning—more naturally than they can select a JupyterHub profile, an allowlisted image, and Kubernetes CPU, memory, and GPU settings. Intent-Spawner investigates whether a bounded decision-support layer can translate that workload intent into feasible, policy-compliant environment recommendations while preserving preview, confirmation, editing, and manual override.

This is not a claim to predict a workload's true peak demand before it runs. It is a claim about aiding a configuration decision within an administrator-curated candidate and policy space.

---

## Deliverable A — Repository-informed concept map

| Concept | Why it matters to Intent-Spawner | Thesis section | Foundational or authoritative sources |
|---|---|---|---|
| Multi-user computational notebooks | Establishes why a notebook service must isolate users while giving each user a suitable environment | 2.1 | [Kluyver2016], [JupyterHub2026] |
| JupyterHub Spawner abstraction | The recommendation ultimately becomes bounded `Spawner`/KubeSpawner configuration | 2.1 | [JupyterHub2026], [KubeSpawner2026] |
| Profiles and curated images | Defines the administrator-controlled candidate space exposed to users | 2.1 | [KubeSpawner2026], [Z2JH2026] |
| Kubernetes requests and limits | Explains the meaning and scheduling/runtime consequences of the recommended CPU and memory fields | 2.2 | [K8sResources2026], [K8sScheduler2026] |
| QoS, quotas, and accelerators | Motivates feasibility and policy checks beyond semantic similarity | 2.2 | [K8sQoS2026], [K8sGPU2026], [K8sQuota2026] |
| Resource right-sizing | Provides the main adjacent systems literature and clarifies the difference between telemetry-driven sizing and pre-execution intent interpretation | 2.3 | [K8sVPA2026], [Rzadca2020], [Cortez2017] |
| Natural-language intent | Frames a high-level desired outcome separately from low-level configuration | 2.4 | [Clemm2022], [Leivadeas2023], [Wu2021] |
| Structured intent extraction | Converts free text and explicit inputs into typed requirements that can be validated and filtered | 2.4 | [Lin2018], [Sacco2025], [Angi2025] |
| Sparse and dense retrieval | Generates candidates from both exact technical terms and semantic paraphrases | 2.5 | [Robertson2009], [Karpukhin2020], [Luan2021] |
| Reciprocal Rank Fusion | Combines heterogeneous ranked lists without requiring calibrated score scales | 2.5 | [Cormack2009] |
| Hard feasibility vs. soft preference | Prevents a semantically relevant but invalid environment from being recommended as feasible | 2.6 | [Felfernig2011] |
| Deterministic ranking and tie-breaking | Supports reproducibility and auditability after candidate generation | 2.6 | [Herlocker2004], [Järvelin2002] |
| Decision support and human control | The user previews, confirms, edits, or overrides; the system is not an autonomous scheduler | 2.6–2.7 | [Jameson2015], [Parasuraman2000] |
| User-centered evaluation | Accuracy alone cannot establish reduced effort, confidence, usability, or preference | 2.7 | [Pu2011], [Knijnenburg2012], [Brooke1996], [Sauro2009] |
| OCI image layers and digest identity | Grounds the distinction between summed logical image sizes and unique stored layer bytes | 2.8 | [OCIImageSpec], [DockerLayers2026], [Harter2016], [Anwar2018] |
| Development/confirmatory separation | Protects the thesis from tuning on sealed cases or presenting planned work as observed evidence | 2.9–2.10 | Repository Protocol-v5 documents; not an external algorithmic claim |

---

## Deliverable B — Literature matrix

The matrix includes 45 high-quality academic or authoritative sources. “Background” means that the source defines a mechanism used by the system; “Related work” means that it helps locate the system among alternative approaches. Some sources serve both roles.

| Key | Work/system (year) | Research problem and method | Domain | Relevance, similarity, and difference | Role |
|---|---|---|---|---|---|
| Kluyver2016 | Jupyter Notebooks (2016) | Reproducible computational narratives combining code, results, and prose | Computational science | Grounds the notebook interaction model; does not address multi-user resource selection | Background |
| JupyterHub2026 | JupyterHub concepts (living documentation) | Multi-user server coordination through Hub, proxy, authenticator, and spawner | Notebook infrastructure | Direct platform basis; leaves environment choice to deployment/spawner configuration | Background |
| KubeSpawner2026 | KubeSpawner documentation (living) | Starts per-user notebook servers as Kubernetes resources; supports profile choices and hooks | Notebook/Kubernetes | Direct integration point; offers mechanisms, not intent-based recommendation | Background |
| Z2JH2026 | Zero to JupyterHub configuration reference (living) | Deployment and `profileList` configuration for JupyterHub on Kubernetes | Notebook/Kubernetes | Shows the conventional curated-choice mechanism that B0 exposes manually | Background |
| K8sResources2026 | Kubernetes resource management (living) | Defines resource requests, limits, CPU throttling, and memory enforcement | Cluster management | Gives operational semantics to P2's resource fields; no user-intent translation | Background |
| K8sScheduler2026 | kube-scheduler (living) | Filters and scores nodes for pending Pods | Cluster scheduling | Clarifies that Intent-Spawner selects an environment before Kubernetes performs node placement | Background |
| K8sQoS2026 | Pod QoS classes (living) | Classifies Pods from request/limit configurations for eviction behavior | Cluster management | Shows why profile configuration affects runtime risk; P2 does not implement QoS control | Background |
| K8sGPU2026 | Schedule GPUs (living) | Advertises and requests accelerators through device plugins and extended resources | Cluster management | Grounds GPU feasibility; actual device availability remains a cluster concern | Background |
| K8sQuota2026 | ResourceQuota (living) | Bounds aggregate namespace resource consumption and object counts | Cluster policy | Motivates policy validation; shipped P2 does not query live remaining quota | Background |
| K8sVPA2026 | Vertical Pod Autoscaling (living) | Recommends/updates requests from resource-use history and current observations | Rightsizing | Close objective, different evidence source and lifecycle: runtime telemetry rather than pre-run NL intent | Related work |
| Rzadca2020 | Autopilot (2020) | Production workload autoscaling using historical and current workload signals | Datacenter management | Demonstrates large-scale automated rightsizing; not a user-facing notebook environment recommender | Related work |
| Cortez2017 | Resource Central (2017) | Predicts workload resource behavior from deployment and telemetry data | Cloud scheduling | Uses learned workload knowledge to aid management; assumes operational histories rather than a new user's textual request | Related work |
| Alipourfard2017 | CherryPick (2017) | Bayesian optimization searches cloud configurations using trial runs | Cloud analytics | Recommends configurations, but requires executions and optimizes performance/cost rather than curated notebook compatibility | Related work |
| Venkataraman2016 | Ernest (2016) | Learns performance models from sampled executions to choose cluster resources | Data analytics | Workload-aware resource selection, but measurement-driven and job-level | Related work |
| Delimitrou2013 | Paragon (2013) | Uses interference and workload classification to place heterogeneous applications | Datacenter scheduling | Matches workloads to resources; operates at scheduling/runtime, not user decision time | Related work |
| Delimitrou2014 | Quasar (2014) | Jointly manages resource allocation and assignment under QoS constraints | Cluster management | Constraint- and performance-aware allocation, but not JupyterHub profile/image choice from language | Related work |
| Mao2016 | DeepRM (2016) | Learns cluster scheduling policies with deep reinforcement learning | Cluster scheduling | Illustrates ML scheduling; unlike P2, policy is learned and controls job placement | Related work |
| Clemm2022 | RFC 9315 (2022) | Defines intent as desired outcomes and describes translation, fulfillment, and assurance | Intent-based networking | Supplies a careful intent vocabulary; Intent-Spawner applies it narrowly without claiming full closed-loop intent assurance | Both |
| Leivadeas2023 | Survey on intent-based networking (2023) | Surveys intent expression, translation, resolution, activation, and assurance | Network management | Closest conceptual lifecycle; network automation is broader and often more autonomous | Related work |
| Lin2018 | NL2Bash (2018) | Translates natural-language instructions into shell commands | NL interfaces | Demonstrates NL-to-system-action translation; direct command generation has a larger action space than allowlisted selection | Related work |
| Wu2021 | Intent-driven cloud resource design (2021) | Translates service/performance intent into cloud resource descriptions using workload, environment, and regression models | Cloud resource design | Closest resource-intent work; focuses performance/SLA sizing for cloud services, not notebook image/profile recommendation | Related work |
| Sacco2025 | Intent-Based Kubernetes Configuration via LLMs (2025) | Evaluates LLM generation of Kubernetes manifests and identifies validity/autonomy challenges | Kubernetes/IaC | Same NL/Kubernetes boundary; generates manifests, whereas P2 selects and validates trusted candidates | Related work |
| Angi2025 | KGen (2025) | Uses few-shot/fine-tuned LLMs to produce Kubernetes manifests from intent | Kubernetes/IaC | Shows domain adaptation and validity challenges; P2 avoids open-ended configuration generation | Related work |
| Robertson2009 | BM25 and probabilistic relevance (2009) | Synthesizes probabilistic ranking and BM25 term weighting | Information retrieval | Foundation for P2's sparse channel | Background |
| Salton1975 | Vector-space model (1975) | Represents documents and queries as weighted vectors for similarity retrieval | Information retrieval | Foundational representation concept; modern dense vectors differ by being learned/semantic | Background |
| Reimers2019 | Sentence-BERT (2019) | Produces semantically meaningful sentence embeddings for efficient similarity search | NLP/IR | Supports the general dense-retrieval rationale; P2's exact embedding implementation is versioned repository infrastructure | Background |
| Karpukhin2020 | Dense Passage Retrieval (2020) | Learns dual encoders for dense retrieval | Information retrieval | Establishes semantic candidate generation; studied on open-domain QA, not environment recommendation | Background |
| Luan2021 | Sparse, dense, and attentional representations (2021) | Compares and combines lexical and neural retrieval signals | Information retrieval | Supports complementary sparse/dense evidence; P2 uses a simpler deterministic fusion | Both |
| Cormack2009 | Reciprocal Rank Fusion (2009) | Combines result rankings using reciprocal ranks | Information retrieval | Direct foundation for P2 fusion; the thesis does not claim RRF as novel | Background |
| Sun2023 | RankGPT (2023) | Studies LLMs as passage rerankers | Information retrieval | Motivates the bounded P3 development hypothesis; P3 was not retained and cannot define the final contribution | Related work |
| Järvelin2002 | Cumulated-gain evaluation (2002) | Introduces graded relevance metrics including normalized discounted cumulative gain | IR evaluation | Supports P1/P2 ranking evaluation; does not apply to non-ranking B0 | Background |
| Herlocker2004 | Evaluating collaborative filtering (2004) | Reviews accuracy measures, protocols, and user-task alignment | Recommender evaluation | Supports metric/task fit and cautious interpretation; domain differs | Both |
| Burke2002 | Hybrid recommender systems (2002) | Taxonomy and analysis of combining recommender strategies | Recommender systems | Offers broader hybrid framing; P2's “hybrid” specifically fuses retrieval channels, not collaborative/content recommenders | Background |
| Felfernig2011 | Developing constraint-based recommenders (2011) | Uses explicit requirements and constraints to find feasible products/configurations | Recommender systems | Strong basis for hard filtering and explanations; P2 adds NL retrieval and Kubernetes integration | Both |
| Jameson2015 | Human decision making and recommender systems (2015) | Frames recommenders as aids for choices and analyzes accuracy–effort tradeoffs | Decision support | Directly supports preview/confirm/override positioning | Both |
| Parasuraman2000 | Levels of human interaction with automation (2000) | Models automation across information acquisition, analysis, decision, and action | Human factors | Helps distinguish recommendation from autonomous actuation | Background |
| Pu2011 | ResQue (2011) | User-centric framework measuring recommender quality, interaction, usefulness, and behavioral intentions | Recommender HCI | Supports multi-dimensional E3 outcomes beyond ranking accuracy | Both |
| Pu2012 | User-perspective recommender evaluation survey (2012) | Reviews constructs and measures for recommender user studies | Recommender HCI | Guides construct selection; must not be used to relabel custom items as validated scales | Background |
| Knijnenburg2012 | Recommender user-experience framework (2012) | Links objective system aspects, perceptions, experience, and behavior | Recommender HCI | Supports separating correctness, effort, confidence, satisfaction, and behavior | Both |
| Brooke1996 | System Usability Scale (1996) | Introduces the ten-item SUS questionnaire | Usability | Matches the planned post-condition SUS; results remain unavailable until E3 runs | Background |
| Sauro2009 | Post-task one-question questionnaires (2009) | Compares single-item post-task usability instruments, including SEQ | Usability | Supports a 1–7 post-task ease item; the protocol correctly calls its item “SEQ-style” | Background |
| OCIImageSpec | OCI Image Specification (living) | Defines image manifests, descriptors, configs, and filesystem layers | Containers | Normative basis for digest-addressed layers and manifest accounting | Background |
| DockerLayers2026 | Docker image-layer documentation (living) | Explains immutable layers and layer reuse | Containers | Accessible explanation of sharing; does not itself prove deployment-specific savings | Background |
| Harter2016 | Slacker (2016) | Studies container startup I/O and lazy distribution | Container systems | Demonstrates that image distribution/startup behavior depends on storage design and access patterns | Related work |
| Anwar2018 | Production Docker registry analysis (2018) | Characterizes registry requests and derives caching/prefetch implications | Container registries | Supports empirical treatment of image distribution/storage rather than simple image-count assumptions | Related work |

---

## Deliverable C — Research-gap analysis

### C.1 What prior work already solves

Prior work supplies nearly every individual technical building block. JupyterHub and KubeSpawner already provide multi-user notebook spawning and administrator-defined profiles. Kubernetes already defines requests, limits, extended resources, quotas, scheduling, and enforcement. BM25, vector retrieval, dense encoders, reciprocal-rank fusion, graded ranking metrics, and constraint-based recommendation are established. The resource-management literature already contains telemetry-driven rightsizing, sampled performance modeling, workload classification, learned scheduling, and production autoscaling. Intent-based management already separates desired outcomes from implementation mechanisms, and recent work translates language into Kubernetes manifests. Human-centered recommender research already argues for evaluating effort, confidence, usability, and choice outcomes—not accuracy alone. OCI and container research already establish digest-addressed layers and the importance of measuring registry/storage behavior.

### C.2 Which Intent-Spawner algorithms are established

P2 does not invent BM25, dense similarity, RRF, hard constraints, soft-preference scoring, top-*k* ranking, or deterministic tie-breaking. Nor does it invent Kubernetes profiles, resource requests/limits, image digests, preview interfaces, or SUS. These components must be presented as selected design mechanisms. The thesis may evaluate whether their composition works in this domain, but it should not claim algorithmic novelty for them.

### C.3 Closest work

Wu et al. [Wu2021] is the closest conceptual cloud-resource study because it explicitly translates service-level intent into cloud resource descriptions. VPA [K8sVPA2026], Autopilot [Rzadca2020], and Resource Central [Cortez2017] are the closest rightsizing relatives, although they depend on observed operation. KGen [Angi2025] and Sacco et al. [Sacco2025] are the closest recent language-to-Kubernetes works, but they study manifest generation. Constraint-based recommenders [Felfernig2011] are the closest recommendation paradigm, while JupyterHub and KubeSpawner [JupyterHub2026], [KubeSpawner2026] provide the exact integration substrate. No one source alone covers the same end-to-end decision point.

### C.4 How Intent-Spawner differs

Intent-Spawner operates before notebook launch, when a user has a current task description but may have no useful workload history. It recommends a joint, administrator-curated configuration—profile, image, and associated resource settings—rather than predicting only a scalar CPU or memory quantity. P2 separates semantic candidate generation from feasibility and policy checks, then applies deterministic final ranking. Its action space is bounded by trusted candidate metadata and an image/profile allowlist. Finally, it presents a server-issued preview that the user can confirm, revise, or override before KubeSpawner applies the configuration. Kubernetes still performs node scheduling and admission.

### C.5 Defensible research gap

A defensible gap is the lack of evaluated, JupyterHub-specific decision support that combines current natural-language workload intent and explicit structured requirements with hybrid candidate retrieval, deterministic feasibility/policy enforcement, and a confirm-before-spawn workflow for choosing both software environment and resource profile. The contribution is therefore a domain-specific systems integration and evaluation contribution. A careful thesis claim is that the work *investigates this composition* under a controlled Protocol-v5 comparison—not that the literature contains no related intent, recommendation, or rightsizing systems.

### C.6 What must not be claimed as novel

The thesis must not claim invention of BM25, embeddings, hybrid retrieval, RRF, constraint satisfaction, resource requests/limits, image-layer deduplication, or standard usability instruments. It must not call P2 a runtime autoscaler, scheduler, demand oracle, personalized recommender, or unconstrained natural-language-to-Kubernetes generator. It must not present P3 as the proposed system, generalize Protocol-v4 evidence as Protocol-v5 confirmation, report B0 ranking metrics, or state that storage savings, human benefits, or resource-efficiency improvements have been demonstrated before the corresponding final experiment is executed.

---

## Deliverable D — Detailed chapter outline

| Subsection | Purpose | Key argument | Principal sources | Approx. words |
|---|---|---|---|---:|
| 2.1 Notebook infrastructure and the configuration decision | Introduce only the platform mechanisms necessary to locate the user decision | KubeSpawner makes profiles configurable, but the conventional interface does not interpret workload semantics | Kluyver; JupyterHub; KubeSpawner; Z2JH | 550 |
| 2.2 Kubernetes resource semantics and feasibility | Explain requests, limits, scheduling, QoS, GPU, and policy | A requested environment has both semantic suitability and operational feasibility | Kubernetes resource, scheduler, QoS, GPU, quota docs | 650 |
| 2.3 Rightsizing and workload-aware management | Review the strongest adjacent systems | Most resource recommenders use telemetry, profiling, trials, or runtime feedback; pre-run intent is a distinct evidence regime | VPA; Autopilot; Resource Central; CherryPick; Ernest; Paragon; Quasar; DeepRM | 750 |
| 2.4 Natural-language and intent-based configuration | Establish intent translation and contrast selection with generation | Intent translation is established, but open-ended execution/configuration requires validation and assurance | RFC 9315; IBN survey; NL2Bash; Wu; Sacco; KGen | 700 |
| 2.5 Retrieval and ranking for environment candidates | Explain P2's sparse, dense, fusion, and deterministic stages | Lexical and semantic channels are complementary; fusion generates evidence, not feasibility | BM25; vector-space model; SBERT; DPR; Luan; RRF; nDCG | 650 |
| 2.6 Constraint-aware recommendation as decision support | Join feasibility, policy, ranking, and human control | Hard constraints delimit valid choices; recommendation supports rather than replaces the user and cluster control plane | Felfernig; Burke; Jameson; Parasuraman | 600 |
| 2.7 Human-centered evaluation | Justify E3 constructs without reporting outcomes | Offline ranking quality and user decision quality are different dependent variables | Herlocker; ResQue; Pu survey; Knijnenburg; SUS; SEQ | 600 |
| 2.8 Container images, layers, and storage accounting | Ground E5's metrics | Logical image bytes and unique digest bytes answer different questions; savings are empirical | OCI; Docker; Slacker; Anwar | 500 |
| 2.9 Comparative analysis and research gap | Synthesize closest approaches | Novelty lies in a bounded JupyterHub-specific composition and its evaluation, not new component algorithms | All preceding related work | 650 |
| 2.10 Chapter synthesis | State the precise thesis position and evidence limits | P2 is a pre-spawn decision aid; current chapter makes no result claim | Repository Protocol-v5 docs | 200 |

---

## 2. Background and Related Work — thesis-ready chapter draft

### 2.1 JupyterHub and Kubernetes-based notebook infrastructure

Computational notebooks combine executable code, results, and explanatory text in a form that supports interactive analysis and reproducible computational narratives [Kluyver2016]. JupyterHub extends the single-user notebook model into a multi-user service. Its Hub coordinates authentication, routing, and the lifecycle of individual user servers, while a configurable Spawner determines how each server is created [JupyterHub2026]. Consequently, the notebook interface visible after startup is only one layer of the system: before it appears, the platform must select an execution environment and allocate resources for it.

KubeSpawner implements the Spawner abstraction using Kubernetes. It creates a per-user Pod and exposes configuration points for the image, CPU and memory guarantees or limits, storage, environment variables, and lifecycle hooks [KubeSpawner2026]. The Zero to JupyterHub Helm chart makes this mechanism deployable as a Kubernetes application and supports a `profileList` through which administrators expose named environment choices [Z2JH2026]. Profiles are useful because they replace an unrestricted configuration surface with curated combinations: for example, a lightweight Python image with small CPU and memory settings, or a machine-learning image with a larger resource envelope.

This curation does not by itself solve the user's selection problem. A profile label is an infrastructure abstraction, whereas users tend to reason in task terms: data size, libraries, framework, interactivity, training, or accelerator need. Adding choices may increase coverage but also increases the information a user must inspect. The default/manual baseline B0 in this repository represents that conventional decision: the same administrator-approved choices are available, but no intelligent ranking is produced. B0 is therefore important for user-centered comparison, but ranking metrics such as MRR, nDCG, or Hit@*k* are undefined for it.

Intent-Spawner inserts a decision-support step before KubeSpawner configuration. In P2, a request and any explicit structured fields are transformed into a `StructuredIntent`; candidate environments are retrieved; hard constraints remove infeasible candidates; deterministic scoring orders the remaining choices; and a policy validator checks the selected profile and image digest. A preview is then bound to the user and generation. The user can confirm it, revise the request, or choose a manual allowlisted override. Only after confirmation does the pre-spawn integration apply the selected configuration. This division is important: Intent-Spawner recommends a bounded environment, KubeSpawner constructs the Pod, and Kubernetes decides whether and where the Pod can run.

The repository's current candidate space illustrates the bounded design. It contains administrator-owned environment metadata and digest-pinned images for minimal Python, scientific Python, PyTorch, and TensorFlow workloads. The resource profiles are small, medium, and large CPU/memory envelopes. The current policy exposes no GPU capacity. Thus, language that expresses a GPU requirement does not magically create a GPU-capable deployment; it becomes a feasibility condition that can produce no recommendation and require user or administrator action.

### 2.2 Kubernetes resource semantics and feasibility

A Kubernetes Pod is the scheduling unit that contains one or more containers sharing selected namespaces and storage. For Intent-Spawner, the relevant distinction is between a container image—what software filesystem and metadata are launched—and resource configuration—what compute resources the Pod requests or is allowed to consume. The kube-scheduler assigns pending Pods to feasible nodes and scores feasible placements; this node-level scheduling remains outside the recommender [K8sScheduler2026].

Kubernetes treats requests and limits differently. The scheduler uses resource requests when judging node capacity. A CPU limit is enforced through throttling, whereas a container that exceeds an enforced memory limit may be terminated under memory pressure [K8sResources2026]. Requests therefore affect placement and reserved capacity, while limits bound runtime consumption. A profile that is too small can lead to throttling, allocation failure, or termination; one that is unnecessarily large can reduce schedulable capacity and increase waiting or infrastructure cost. These are possible mechanisms, not proof that any particular Intent-Spawner choice improves utilization.

Request/limit combinations also contribute to Kubernetes Pod Quality of Service classes—Guaranteed, Burstable, and BestEffort—which influence eviction ordering under node pressure [K8sQoS2026]. This thesis need not become a general QoS study, but the concept shows that apparently simple form fields can have consequences beyond the individual notebook. GPU resources introduce another feasibility dimension. Kubernetes exposes GPUs through vendor device plugins and schedules them as extended resources, normally as integer limits [K8sGPU2026]. A textual preference for GPU is therefore not merely a similarity signal: it must correspond to an available, policy-permitted resource and a compatible software environment.

Administrative policy further restricts otherwise valid configurations. `ResourceQuota` limits aggregate namespace use, and related admission controls can reject workloads that violate cluster policy [K8sQuota2026]. In the shipped Intent-Spawner configuration, the local validator checks a versioned policy and catalog, profile allowlists, and image SHA digests. Kubernetes admission is still authoritative. Moreover, the shipped adapter does not inspect live node capacity, remaining namespace quota, or per-user historical consumption. A P2 candidate is therefore *catalog- and policy-feasible according to the inputs available to the recommender*, not guaranteed to be schedulable at a future instant.

This distinction yields two different questions. Relevance asks whether an environment fits the meaning of the request—for example, whether a PyTorch environment matches a deep-learning task. Feasibility asks whether mandatory features, minimum CPU or memory, forbidden features, image/profile policy, and accelerator requirements are satisfied. A semantically strong candidate can fail the second question. Conversely, a feasible environment may be weakly relevant. Treating relevance and feasibility as separate stages is consequently more defensible than asking one opaque similarity score to represent both.

### 2.3 Resource recommendation and right-sizing

Resource right-sizing seeks a resource configuration that meets performance or reliability objectives without persistent over-allocation. The field includes reactive autoscaling, predictive recommendation, offline configuration search, and workload-aware scheduling. These approaches are essential context, but they rely on different evidence and intervene at different points in the workload lifecycle.

Kubernetes Vertical Pod Autoscaler (VPA) estimates appropriate resource requests from observed use. Its recommender, updater, and admission components can calculate recommendations and, depending on configuration, apply them to Pods [K8sVPA2026]. Google's Autopilot similarly demonstrates large-scale workload autoscaling based on operational signals and production control mechanisms [Rzadca2020]. Resource Central learns from deployment and runtime telemetry to predict workload characteristics for cluster-management decisions [Cortez2017]. These systems can exploit evidence that is unavailable when a first-time user has only expressed an intended notebook task.

Other systems deliberately obtain evidence through exploration or profiling. CherryPick uses Bayesian optimization across test deployments to search for cost-effective cloud configurations [Alipourfard2017]. Ernest learns performance models for analytics jobs from sampled runs [Venkataraman2016]. Both can make detailed performance-aware choices, but the measurements are part of their method. A pre-spawn notebook recommender cannot honestly promise the same precision from language alone. Natural-language intent can communicate required libraries, rough data scale, or an explicit accelerator need; it cannot establish an unseen program's memory peak or execution-time curve.

Workload-aware schedulers address a further adjacent problem. Paragon classifies heterogeneous applications and considers interference when assigning them to servers [Delimitrou2013]. Quasar jointly reasons about resource allocation, assignment, and application QoS [Delimitrou2014]. DeepRM explores learned cluster scheduling policies using deep reinforcement learning [Mao2016]. Their control variable is usually workload placement or allocation within the cluster, and their objective is system performance or utilization. Intent-Spawner acts earlier and at a higher semantic level: it helps a person choose among curated notebook environments, after which the ordinary Kubernetes control plane schedules the resulting Pod.

Wu et al. present a closer intent-driven resource design framework. Their system translates service-level performance intent into cloud resource descriptions and uses workload, resource configuration, and environmental conditions to decide vCPU and memory amounts [Wu2021]. This directly supports the proposition that users often articulate *what* a service should achieve while providers need concrete *how* configurations. The difference lies in scope and method. Their target is performance-oriented cloud-service resource design, supported by workload models and environmental data; Intent-Spawner targets a joint JupyterHub profile and software-image decision, uses typed task requirements and retrieval over a curated catalog, and keeps the user at the confirmation boundary.

The comparison defines a narrow role for P2. It is neither a substitute for VPA nor a performance predictor. It is a cold-start decision aid for the moment before useful execution telemetry exists. If the system were extended with historical traces, live quota, or node state, that would create a different experimental condition and additional privacy, stability, and contamination concerns. The current thesis should therefore evaluate P2 for selection quality and decision support within its stated candidate space, and evaluate infrastructure consequences separately rather than assuming that semantic correctness implies physical efficiency.

### 2.4 Natural-language and intent-based system interfaces

Intent-based management distinguishes a desired outcome from the low-level operations used to realize it. RFC 9315 formalizes this distinction for networking and describes functions including intent translation, validation, fulfillment, and assurance [Clemm2022]. Surveys of intent-based networking likewise organize the lifecycle around expression, translation, resolution, activation, and closed-loop assurance [Leivadeas2023]. These ideas are useful to Intent-Spawner, but the analogy must be bounded. P2 performs request interpretation, candidate selection, and pre-spawn validation; it does not implement a general closed-loop assurance system, continuously monitor whether the user's scientific goal was achieved, or autonomously reconfigure the cluster.

Natural-language interfaces have also been used to generate executable system artifacts. NL2Bash maps English descriptions to shell commands, illustrating both the utility and difficulty of translating user language into precise operations [Lin2018]. More recent work targets infrastructure configuration. Sacco et al. evaluate LLM-based generation of Kubernetes manifests and emphasize validity, benchmarking, and autonomy challenges [Sacco2025]. KGen investigates few-shot and fine-tuned models for generating manifests from described intent; its results also show that model and prompting choices can affect the rate of valid outputs [Angi2025]. These systems explore a broad generative action space in which a model can emit new field combinations.

Intent-Spawner instead applies *selection under constraints*. The candidate environments and image digests are created by administrators. The language interface helps find and order those candidates but does not authorize arbitrary images or Kubernetes manifests. This architecture sacrifices open-ended expressiveness for bounded behavior and clearer policy enforcement. It also allows a meaningful “no feasible candidate” outcome. That is safer than coercing every request into a superficially plausible configuration, although safety in this context means conformance to the encoded catalog and policy—not proof of program correctness or security.

P2's intermediate `StructuredIntent` is important for this boundary. It represents task types; required, preferred, and forbidden features; required or preferred frameworks and libraries; and selected resource constraints. Explicit inputs, such as a supplied dataset size, take precedence over inference. If extraction fails, the pipeline degrades to deterministic preservation of explicit fields. The structured representation makes later filtering inspectable: a hard GPU requirement can be checked as a Boolean/resource condition rather than left implicit in an embedding distance.

The term “context-aware” should also be used precisely. In recommender literature, context can include time, location, social situation, or other conditions surrounding a choice. In Intent-Spawner, context primarily means the current workload statement and explicit task metadata, interpreted against versioned administrator knowledge. It does not currently imply long-term personalization, behavioral profiling, or a learned model of an individual. This narrower definition is sufficient for the thesis and avoids implying data collection that the system does not perform.

### 2.5 Information retrieval for environment recommendation

Once intent has been structured, P2 must identify relevant environments from the candidate catalog. This is naturally modeled as information retrieval: a query describes a workload, each environment has an administrator-authored document and structured metadata, and the system produces a ranked candidate list. Retrieval is a candidate-generation stage; it reduces and orders the search space before feasibility and final decision logic.

Sparse lexical retrieval rewards overlap between query and document terms. BM25 belongs to the probabilistic relevance tradition and combines term frequency, inverse document frequency, and document-length normalization [Robertson2009]. It is well suited to discriminative tokens such as `tensorflow`, `pytorch`, or exact library names. Its weakness is vocabulary mismatch: “neural network training” may be relevant to an environment description that uses different wording. The older vector-space model established the general idea of representing queries and documents as weighted vectors and ranking them by similarity [Salton1975], while modern dense retrieval learns continuous representations intended to capture semantic relationships.

Sentence-BERT demonstrates efficient semantic comparison with sentence-level embeddings [Reimers2019], and Dense Passage Retrieval shows the effectiveness of learned dual encoders for retrieving semantically relevant text [Karpukhin2020]. Those works do not validate Intent-Spawner's particular catalog or embedding implementation, but they justify the dense-channel principle. Sparse and dense evidence are often complementary: lexical models preserve exact tokens, while dense models can bridge paraphrases. Comparative work by Luan et al. examines tradeoffs among sparse, dense, and attentional representations and supports hybrid treatment when neither channel dominates across cases [Luan2021].

The scores produced by heterogeneous retrievers are not necessarily calibrated. P2 therefore uses Reciprocal Rank Fusion (RRF), which combines rankings through the reciprocal of each item's rank rather than directly averaging unlike score scales. Cormack, Clarke, and Büttcher introduced RRF as a simple rank-fusion method that performed robustly across retrieval runs [Cormack2009]. In the repository, a candidate's fused value is the weighted sum of reciprocal terms, `w_m / (k + rank_m)`, for the available retrieval methods. If one channel fails, the pipeline can fall back to the remaining channel. RRF is an established algorithm; its use here is an engineering choice.

After fusion, P2 does not equate fused relevance with the final decision. Hard constraints remove infeasible candidates, and soft preferences contribute to deterministic ranking. The present scoring rule combines reciprocal fused rank and a soft-preference term with fixed weights, rounds the score, and resolves ties by candidate identifier. These details make repeated evaluation auditable and prevent incidental collection ordering from changing outputs. They do not make the scoring rule universally optimal; its validity is limited to the frozen Protocol-v5 system and candidate corpus.

Ranking evaluation should respect the task and output type. Normalized discounted cumulative gain captures graded relevance near the top of a ranked list [Järvelin2002], while recommender evaluation literature emphasizes that metric choice must follow the user task and experimental protocol [Herlocker2004]. Such measures are meaningful for P1 and P2 because they return rankings. B0 presents an unranked manual decision and must instead be evaluated through choice outcomes, time, burden, and user-reported measures.

LLM reranking is a plausible extension because language models can compare a query with a small retrieved set. RankGPT, for example, studies LLMs as reranking agents [Sun2023]. The repository's P3 branch imposed a grounded action space: a reranker could only return feasible candidate identifiers and could not rewrite resources or images, with exact P2 fallback on failure. Nevertheless, the Protocol-v5 design records that P3 was not retained after its formative development gate. It belongs in the thesis as a bounded rejected branch, not as the final proposed system and not as evidence that LLM reranking improves this task.

### 2.6 Constraint-aware recommendation and decision support

Classic recommender systems often infer preference from user–item interactions. Intent-Spawner has a different setting: users may be new, the candidate set is small and administrator-controlled, and requirements such as “must have GPU” are not tastes that can be traded against semantic similarity. Constraint-based recommenders are a closer model because they elicit requirements, apply domain constraints, and support decisions over configurable products [Felfernig2011].

P2 distinguishes hard and soft conditions. Hard conditions include required accelerators, minimum resource bounds, explicit incompatibilities, and forbidden features; violating one removes the candidate. Soft preferences influence ordering among feasible candidates. This ordering matters. Filtering *after* unconstrained ranking but *before* presenting a recommendation ensures that a highly similar but invalid environment cannot win. If filtering leaves no candidates, the system exposes the absence of a feasible choice and preserves manual override rather than silently weakening a mandatory requirement.

The word “hybrid” also requires care. Burke's taxonomy concerns systems that combine different recommender techniques, such as collaborative and content-based methods [Burke2002]. P2 is hybrid in a narrower information-retrieval sense: it fuses sparse and dense candidate rankings. It does not learn from community ratings, and it does not infer enduring user preferences. The two meanings are related at the level of combining evidence, but they should not be treated as identical.

Human decision-making research frames recommenders as tools that help people make choices and highlights tradeoffs between decision accuracy and effort [Jameson2015]. This framing fits Intent-Spawner better than “autonomous resource manager.” The system acquires and analyzes request information, proposes a choice, and supplies a preview; the user retains authority to confirm, revise, or override. Parasuraman, Sheridan, and Wickens distinguish levels of automation across information acquisition, analysis, decision selection, and action implementation [Parasuraman2000]. On that spectrum, Intent-Spawner automates parts of analysis and recommendation while deliberately retaining a human checkpoint before launch.

The checkpoint is also a systems control. The server-side preview is bound to the current user and recommendation generation, has a limited lifetime, and is consumed once. Editing invalidates the prior confirmation. At spawn time the integration applies the already validated selection instead of recomputing an unreviewed recommendation. This reduces the gap between what was shown and what is launched. It does not eliminate all time-of-check/time-of-use risk, because Kubernetes admission and cluster state may still change.

### 2.7 Human-centered evaluation of recommendation

Offline retrieval metrics answer whether known relevant environments appear high in a ranking. They do not answer whether a person chooses successfully, reaches a decision faster, experiences less interaction burden, trusts the result appropriately, or prefers the interface. Herlocker et al. argue that recommender evaluation must align metrics and protocols with the supported user task [Herlocker2004]. ResQue similarly organizes evaluation around perceived recommendation quality, interaction quality, usefulness, satisfaction, and behavioral intentions [Pu2011].

Pu, Chen, and Hu's survey further shows that user-perspective evaluation requires explicit constructs and carefully chosen instruments rather than an undifferentiated “satisfaction” question [Pu2012]. Knijnenburg et al. model how objective system properties influence perceptions, experience, and behavior, and show why accuracy is only one part of recommender-system experience [Knijnenburg2012]. For this thesis, selection outcome, decision time, interaction count, corrections, overrides, perceived task ease, usability, confidence, and final preference are related but distinct measurements.

The Protocol-v5 E3 design accordingly compares B0 and P2 in a controlled crossover study using the same deployment and the same available environments. B0 exposes blank manual selectors; P2 adds intent entry, preview, confirmation, editing, and override. Planned behavioral outcomes include acceptable or preferred selection, time, interaction burden, corrections, overrides, completion, and cancellation. A within-participant design can reduce variance from differences in prior JupyterHub experience, but analysis must still respect workload family and condition order. Repeated stochastic executions, where used elsewhere, measure stability or runtime variability; they are not independent users or additional accuracy cases.

For questionnaires, the ten-item System Usability Scale is a compact post-condition usability instrument [Brooke1996]. A single 1–7 ease question after a task is consistent with the family of post-task measures compared by Sauro and Dumas [Sauro2009]. The repository appropriately calls its item “SEQ-style”; custom confidence, natural-expression, or convenience items must not be reported as SUS or as independently validated multi-item scales. Instrument names do not validate an unexecuted study.

At the time of this draft, E3 has no participant records and is `NOT_EXECUTED`. The chapter can justify the constructs and protocol but cannot claim that P2 reduces time, increases correct choice, improves SUS, or is preferred. If the study later runs, raw participant identifiers must not be logged or published, raw observations must remain separate from derived statistics, and all evidence must retain protocol, code, dataset, and environment provenance.

### 2.8 Container images, layers, and storage accounting

An OCI image manifest references a configuration object and an ordered sequence of filesystem layer descriptors. Descriptors include a media type, digest, and size, making content identity explicit [OCIImageSpec]. Docker's user-facing documentation explains that image builds add immutable layers and that layers can be reused across images [DockerLayers2026]. Two notebook images derived from the same base may therefore refer to some identical layer digests.

This structure creates two legitimate but different size measures. **Logical image bytes** sum the manifest-reported layer sizes for every selected image, counting a shared layer once per image reference. This approximates the aggregate size of treating each image independently. **Unique-layer bytes** group descriptors by the relevant platform and digest and count each unique blob once. The difference is a potential deduplication amount under a compatible content-addressed registry or node cache. It is not automatically equivalent to free disk space, network traffic avoided, or faster startup.

Physical behavior depends on implementation and workload. Slacker shows that container startup can be dominated by image transfer even though only a subset of data is read, and explores lazy fetching on shared storage [Harter2016]. Anwar et al. characterize production registry access patterns and show that cache and prefetch choices should be informed by measured traces [Anwar2018]. These works caution against reasoning from image count alone.

Accordingly, E5 should report manifest-derived logical and unique-layer measures as separate artifacts and record image digest, platform, media type, compression representation, registry/runtime version, timestamp, and catalog revision. It should not assume that layers with different compressed representations are physically identical or that every runtime stores one copy globally. In the current Protocol-v5 evidence, functional image checks are development observations and confirmatory storage measurements are unavailable. The thesis may explain why sublinear growth is possible, but whether the curated catalog actually achieves it remains an empirical question.

### 2.9 Related systems and comparative analysis

The most relevant comparison is not between isolated algorithms but between where a system receives evidence, what it recommends, how broadly it may act, and who retains the final decision.

| Work/system | Target and input | Output/action | NL intent | Constraints or validation | Human confirmation | Runtime telemetry required | JupyterHub-specific |
|---|---|---|:---:|---|:---:|:---:|:---:|
| Manual JupyterHub / B0 | User inspects curated profile/image controls | Unranked manual selection | No | Allowlisted UI and platform admission | User selects directly | No | Yes |
| KubeSpawner profiles | Administrator-defined profile options | Pod configuration template | No | Deployment configuration | User chooses profile | No | Yes |
| Kubernetes VPA [K8sVPA2026] | Observed Pod resource use | Recommended/applied resource requests | No | Kubernetes policies | Mode-dependent, not a per-choice preview | Yes | No |
| Autopilot [Rzadca2020] | Production workload signals | Automated workload scaling | No | Production control policies | No per-launch end-user choice | Yes | No |
| Resource Central [Cortez2017] | Deployment and telemetry history | Resource/workload predictions | No | Cluster-management logic | No end-user preview | Yes | No |
| CherryPick [Alipourfard2017] | Objective plus sampled cloud runs | Cost-effective cloud configuration | No | Search space bounds | Operator consumes result | Requires trial runs | No |
| Ernest [Venkataraman2016] | Sample executions and job scale | Cluster configuration/performance estimate | No | Model/search assumptions | Operator consumes result | Requires profiling runs | No |
| Intent-driven cloud resource design [Wu2021] | Service/performance intent, workload, environment | vCPU/memory resource description | Intent, not necessarily free-form NL | Requirement and performance model | Provider-side design workflow | Model/training evidence | No |
| NL2Bash [Lin2018] | Natural-language command description | Generated shell command | Yes | Decoding/evaluation, not curated candidates | External to core method | No | No |
| KGen [Angi2025] | Natural-language intent and examples | Generated Kubernetes manifest | Yes | Syntax/validity evaluation | Not the defining control | No at inference | No |
| Sacco et al. [Sacco2025] | Human intent | LLM-generated Kubernetes configuration | Yes | Validity and autonomy are open challenges | Varies by approach | No at inference | No |
| Constraint-based recommenders [Felfernig2011] | Explicit requirements and domain knowledge | Feasible product/configuration choices | Usually structured dialogue | Central constraint model | Typically yes | No | No |
| Intent-Spawner P1 | Request features and frozen rules | Ranked curated environment list | Limited feature matching | Frozen rule/policy logic | Preview/override workflow | No | Yes |
| **Intent-Spawner P2** | NL workload statement plus explicit typed inputs | Ranked feasible profile–image recommendation | **Yes** | **Hard constraints, deterministic scoring, allowlists, digest validation** | **Yes: preview, edit, confirm, override** | **No** | **Yes** |
| Intent-Spawner P3 (not retained) | P2 feasible set plus grounded LLM reranker | Reordered feasible identifiers only | Yes | Schema, feasible-ID restriction, exact P2 fallback | Same workflow | No | Yes |

Several observations follow. First, VPA, Autopilot, Resource Central, CherryPick, and Ernest are stronger references for quantitative right-sizing than Intent-Spawner, because they use observations or controlled runs. Their limitation for this problem is cold-start decision support, not general technical inferiority. Second, language-to-command or language-to-manifest systems are more expressive than P2, but that expressiveness enlarges the validity and safety problem. Third, constraint-based recommendation supplies the most direct conceptual model for keeping feasibility explicit. Finally, KubeSpawner provides the target platform but not the translation from workload meaning to a ranked environment.

### 2.10 Research gap and positioning of Intent-Spawner

The literature does not justify a categorical claim that intent-driven resource selection is unprecedented. Intent-based management, cloud resource design, configuration generation, constraint-based recommendation, and telemetry-driven rightsizing all address parts of the semantic gap. Nor is the thesis contribution a new retrieval algorithm. P2 uses established sparse and dense retrieval, RRF, constraints, and deterministic ranking.

The narrower research opportunity is at their intersection: pre-spawn, JupyterHub-specific decision support that translates a current workload description into a joint software-and-resource recommendation drawn from an administrator-curated catalog; separates relevance from feasibility; applies deterministic policy-aware ranking; and preserves a user confirmation boundary before KubeSpawner configuration. Protocol-v5 then asks whether this composition improves ranking quality relative to frozen P1, helps users relative to manual B0, behaves robustly across workload families, and has measurable infrastructure consequences. Those questions are empirical and must remain unanswered where evidence is `NOT_EXECUTED`.

The strongest defensible thesis position is therefore:

> Intent-Spawner P2 is a bounded, context-aware recommendation layer for JupyterHub on Kubernetes. Its contribution is the design and controlled evaluation of an end-to-end composition—structured workload intent, hybrid retrieval, explicit feasibility constraints, deterministic ranking, policy validation, and preview/confirm/override—not the invention of its component algorithms and not autonomous resource scheduling.

---

## Deliverable F — Concise comparison summary

| Dimension | B0 | P1 | P2 (proposed) | P3 |
|---|---|---|---|---|
| User input | Manual selectors | Request interpreted by frozen rules | NL request plus explicit structured inputs | Same as P2 |
| Ranking | None | Rule-based ranking | Sparse+dense retrieval, RRF, hard filtering, deterministic final ranking | Grounded LLM reorders P2-feasible IDs |
| Candidate authority | Admin-curated | Admin-curated | Admin-curated, versioned catalog | Same feasible set as P2 |
| Resource/image mutation | Manual allowlisted choice | No arbitrary mutation | No arbitrary mutation | Reranker cannot mutate resources/images |
| User control | Direct manual choice | Preview/override | Preview/edit/confirm/override | Same as P2 |
| Protocol-v5 role | Human-study baseline | Frozen ranking comparator | Main proposed method | Not retained after development gate |
| Appropriate metrics | Choice success, time, burden, usability | MRR/nDCG/Hit@*k*, stability/runtime | Same ranking metrics plus robustness and user outcomes | No final confirmatory claims |

---

## Deliverable G — References

Citation metadata below was checked against publisher, proceedings, standards, or official project pages. Documentation without a stable publication year is marked “living documentation” and includes the access date.

1. **[Kluyver2016]** Thomas Kluyver, Benjamin Ragan-Kelley, Fernando Pérez, Brian Granger, Matthias Bussonnier, Jonathan Frederic, Kyle Kelley, Jessica Hamrick, Jason Grout, Sylvain Corlay, Paul Ivanov, Damián Avila, Safia Abdalla, Carol Willing, and the Jupyter Development Team. “Jupyter Notebooks—a Publishing Format for Reproducible Computational Workflows.” *Positioning and Power in Academic Publishing: Players, Agents and Agendas*, pp. 87–90, 2016. DOI: [10.3233/978-1-61499-649-1-87](https://doi.org/10.3233/978-1-61499-649-1-87). Supports the notebook and reproducible-workflow background.

2. **[JupyterHub2026]** Project Jupyter. “JupyterHub Concepts.” *JupyterHub Documentation*, living documentation, accessed 8 September 2026. [Official documentation](https://jupyterhub.readthedocs.io/en/stable/explanation/concepts.html). Supports Hub, proxy, authenticator, spawner, and per-user-server responsibilities.

3. **[KubeSpawner2026]** Project Jupyter. “KubeSpawner API and Configuration.” *KubeSpawner Documentation*, living documentation, accessed 8 September 2026. [Official documentation](https://jupyterhub-kubespawner.readthedocs.io/en/latest/spawner.html). Supports Kubernetes Pod spawning, profiles, images, resources, and hooks.

4. **[Z2JH2026]** Project Jupyter. “Configuration Reference.” *Zero to JupyterHub with Kubernetes*, living documentation, accessed 8 September 2026. [Official documentation](https://z2jh.jupyter.org/en/stable/resources/reference.html). Supports `singleuser.profileList` and Helm-configured user environments.

5. **[K8sResources2026]** Kubernetes Authors. “Resource Management for Pods and Containers.” *Kubernetes Documentation*, living documentation, accessed 8 September 2026. [Official documentation](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/). Supports requests, limits, scheduler accounting, CPU throttling, and memory enforcement.

6. **[K8sScheduler2026]** Kubernetes Authors. “Kubernetes Scheduler.” *Kubernetes Documentation*, living documentation, accessed 8 September 2026. [Official documentation](https://kubernetes.io/docs/concepts/scheduling-eviction/kube-scheduler/). Supports the separation between environment recommendation and node placement.

7. **[K8sQoS2026]** Kubernetes Authors. “Pod Quality of Service Classes.” *Kubernetes Documentation*, living documentation, accessed 8 September 2026. [Official documentation](https://kubernetes.io/docs/concepts/workloads/pods/pod-qos/). Supports Guaranteed, Burstable, and BestEffort behavior.

8. **[K8sGPU2026]** Kubernetes Authors. “Schedule GPUs.” *Kubernetes Documentation*, living documentation, accessed 8 September 2026. [Official documentation](https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/). Supports device plugins and accelerator extended resources.

9. **[K8sQuota2026]** Kubernetes Authors. “Resource Quotas.” *Kubernetes Documentation*, living documentation, accessed 8 September 2026. [Official documentation](https://kubernetes.io/docs/concepts/policy/resource-quotas/). Supports namespace-level policy and aggregate resource bounds.

10. **[K8sVPA2026]** Kubernetes Authors. “Vertical Pod Autoscaling.” *Kubernetes Documentation*, living documentation, accessed 8 September 2026. [Official documentation](https://kubernetes.io/docs/concepts/workloads/autoscaling/vertical-pod-autoscale/). Supports telemetry/history-based request recommendation and update behavior.

11. **[Rzadca2020]** Krzysztof Rzadca, Pawel Findeisen, Jacek Świderski, Przemysław Zych, Przemysław Broniek, Jarek Kusmierek, Paweł Nowak, Beata Strack, Piotr Witusowski, Steven Hand, and John Wilkes. “Autopilot: Workload Autoscaling at Google Scale.” *Proceedings of the Fifteenth European Conference on Computer Systems (EuroSys ’20)*, 2020. DOI: [10.1145/3342195.3387524](https://doi.org/10.1145/3342195.3387524). [Google Research page](https://research.google/pubs/autopilot-workload-autoscaling-at-google-scale/). Supports production telemetry-driven autoscaling.

12. **[Cortez2017]** Eli Cortez, Anand Bonde, Alexandre Muzio, Mark Russinovich, Marcus Fontoura, and Ricardo Bianchini. “Resource Central: Understanding and Predicting Workloads for Improved Resource Management in Large Cloud Platforms.” *Proceedings of the 26th ACM Symposium on Operating Systems Principles (SOSP ’17)*, pp. 153–167, 2017. DOI: [10.1145/3132747.3132772](https://doi.org/10.1145/3132747.3132772). Supports prediction from production workload and deployment evidence.

13. **[Alipourfard2017]** Omid Alipourfard, Hongqiang Harry Liu, Jianshu Chen, Shivaram Venkataraman, Minlan Yu, and Ming Zhang. “CherryPick: Adaptively Unearthing the Best Cloud Configurations for Big Data Analytics.” *14th USENIX Symposium on Networked Systems Design and Implementation (NSDI ’17)*, pp. 469–482, 2017. [USENIX proceedings page](https://www.usenix.org/conference/nsdi17/technical-sessions/presentation/alipourfard). Supports trial-driven Bayesian configuration search.

14. **[Venkataraman2016]** Shivaram Venkataraman, Zongheng Yang, Michael Franklin, Benjamin Recht, and Ion Stoica. “Ernest: Efficient Performance Prediction for Large-Scale Advanced Analytics.” *13th USENIX Symposium on Networked Systems Design and Implementation (NSDI ’16)*, pp. 363–378, 2016. [USENIX proceedings page](https://www.usenix.org/conference/nsdi16/technical-sessions/presentation/venkataraman). Supports sampled-run performance modeling.

15. **[Delimitrou2013]** Christina Delimitrou and Christos Kozyrakis. “Paragon: QoS-Aware Scheduling for Heterogeneous Datacenters.” *Proceedings of the 18th International Conference on Architectural Support for Programming Languages and Operating Systems (ASPLOS ’13)*, pp. 77–88, 2013. DOI: [10.1145/2451116.2451125](https://doi.org/10.1145/2451116.2451125). Supports workload classification and interference-aware placement.

16. **[Delimitrou2014]** Christina Delimitrou and Christos Kozyrakis. “Quasar: Resource-Efficient and QoS-Aware Cluster Management.” *Proceedings of the 19th International Conference on Architectural Support for Programming Languages and Operating Systems (ASPLOS ’14)*, pp. 127–144, 2014. DOI: [10.1145/2541940.2541941](https://doi.org/10.1145/2541940.2541941). Supports QoS-aware joint resource assignment and allocation.

17. **[Mao2016]** Hongzi Mao, Mohammad Alizadeh, Ishai Menache, and Srikanth Kandula. “Resource Management with Deep Reinforcement Learning.” *Proceedings of the 15th ACM Workshop on Hot Topics in Networks (HotNets ’16)*, pp. 50–56, 2016. DOI: [10.1145/3005745.3005750](https://doi.org/10.1145/3005745.3005750). Supports learned cluster scheduling as a distinct approach.

18. **[Clemm2022]** Alexander Clemm, Laurent Ciavaglia, Lisandro Zambenedetti Granville, and Jeff Tantsura. “Intent-Based Networking—Concepts and Definitions.” *RFC 9315*, IETF, 2022. [RFC 9315](https://datatracker.ietf.org/doc/html/rfc9315). Supports intent terminology and lifecycle distinctions.

19. **[Leivadeas2023]** Aris Leivadeas and Matthias Falkner. “A Survey on Intent-Based Networking.” *IEEE Communications Surveys & Tutorials*, 25(1), pp. 625–655, 2023. DOI: [10.1109/COMST.2022.3215919](https://doi.org/10.1109/COMST.2022.3215919). Supports the intent expression/translation/resolution/activation/assurance lifecycle.

20. **[Lin2018]** Xi Victoria Lin, Chenglong Wang, Luke Zettlemoyer, and Michael D. Ernst. “NL2Bash: A Corpus and Semantic Parser for Natural Language Interface to the Linux Operating System.” *Proceedings of LREC 2018*, pp. 3502–3509, 2018. [ACL Anthology](https://aclanthology.org/L18-1491/). Supports natural-language-to-system-action translation.

21. **[Wu2021]** Chao Wu, Shingo Horiuchi, Kenji Murase, Hiroaki Kikushima, and Kenichi Tayama. “Intent-Driven Cloud Resource Design Framework to Meet Cloud Performance Requirements and Its Application to a Cloud-Sensor System.” *Journal of Cloud Computing*, 10, article 30, 2021. DOI: [10.1186/s13677-021-00242-w](https://doi.org/10.1186/s13677-021-00242-w). Supports intent-to-resource translation using workload and environment models.

22. **[Sacco2025]** Alessio Sacco, Cristian Zilli, and Guido Marchetto. “Intent-Based Kubernetes Configuration via LLMs: Current Trends and Open Challenges.” *2025 IEEE 50th Conference on Local Computer Networks (LCN)*, pp. 1–7, 2025. DOI: [10.1109/LCN65610.2025.11146359](https://doi.org/10.1109/LCN65610.2025.11146359). Supports the comparison with open-ended LLM manifest generation and its validity challenges.

23. **[Angi2025]** Antonino Angi, Liubov Nedoshivina, Alessio Sacco, Stefano Braghin, and Mark Purcell. “A Perspective on LLM Data Generation with Few-Shot Examples: From Intent to Kubernetes Manifest.” *Proceedings of the 63rd Annual Meeting of the Association for Computational Linguistics, Industry Track*, pp. 345–354, 2025. DOI: [10.18653/v1/2025.acl-industry.27](https://doi.org/10.18653/v1/2025.acl-industry.27). Supports few-shot/fine-tuned NL-to-Kubernetes generation.

24. **[Robertson2009]** Stephen Robertson and Hugo Zaragoza. “The Probabilistic Relevance Framework: BM25 and Beyond.” *Foundations and Trends in Information Retrieval*, 3(4), pp. 333–389, 2009. DOI: [10.1561/1500000019](https://doi.org/10.1561/1500000019). Supports BM25 and probabilistic lexical ranking.

25. **[Salton1975]** Gerard Salton, Anita Wong, and Chung-Shu Yang. “A Vector Space Model for Automatic Indexing.” *Communications of the ACM*, 18(11), pp. 613–620, 1975. DOI: [10.1145/361219.361220](https://doi.org/10.1145/361219.361220). Supports vector representation and similarity ranking.

26. **[Reimers2019]** Nils Reimers and Iryna Gurevych. “Sentence-BERT: Sentence Embeddings Using Siamese BERT-Networks.” *Proceedings of EMNLP-IJCNLP 2019*, pp. 3982–3992, 2019. DOI: [10.18653/v1/D19-1410](https://doi.org/10.18653/v1/D19-1410). Supports efficient semantic sentence embeddings.

27. **[Karpukhin2020]** Vladimir Karpukhin, Barlas Oğuz, Sewon Min, Patrick Lewis, Ledell Wu, Sergey Edunov, Danqi Chen, and Wen-tau Yih. “Dense Passage Retrieval for Open-Domain Question Answering.” *Proceedings of EMNLP 2020*, pp. 6769–6781, 2020. DOI: [10.18653/v1/2020.emnlp-main.550](https://doi.org/10.18653/v1/2020.emnlp-main.550). Supports learned dense retrieval.

28. **[Luan2021]** Yi Luan, Jacob Eisenstein, Kristina Toutanova, and Michael Collins. “Sparse, Dense, and Attentional Representations for Text Retrieval.” *Transactions of the Association for Computational Linguistics*, 9, pp. 329–345, 2021. DOI: [10.1162/tacl_a_00369](https://doi.org/10.1162/tacl_a_00369). Supports comparison and complementarity of sparse and dense representations.

29. **[Cormack2009]** Gordon V. Cormack, Charles L. A. Clarke, and Stefan Büttcher. “Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods.” *Proceedings of the 32nd International ACM SIGIR Conference*, pp. 758–759, 2009. DOI: [10.1145/1571941.1572114](https://doi.org/10.1145/1571941.1572114). Supports RRF.

30. **[Sun2023]** Weiwei Sun, Lingyong Yan, Xinyu Ma, Shuaiqiang Wang, Pengjie Ren, Zhumin Chen, Dawei Yin, and Zhaochun Ren. “Is ChatGPT Good at Search? Investigating Large Language Models as Re-Ranking Agents.” *Proceedings of EMNLP 2023*, pp. 14918–14937, 2023. DOI: [10.18653/v1/2023.emnlp-main.923](https://doi.org/10.18653/v1/2023.emnlp-main.923). Supports the bounded LLM-reranking motivation for the rejected P3 branch.

31. **[Järvelin2002]** Kalervo Järvelin and Jaana Kekäläinen. “Cumulated Gain-Based Evaluation of IR Techniques.” *ACM Transactions on Information Systems*, 20(4), pp. 422–446, 2002. DOI: [10.1145/582415.582418](https://doi.org/10.1145/582415.582418). Supports graded ranking evaluation and nDCG.

32. **[Herlocker2004]** Jonathan L. Herlocker, Joseph A. Konstan, Loren G. Terveen, and John T. Riedl. “Evaluating Collaborative Filtering Recommender Systems.” *ACM Transactions on Information Systems*, 22(1), pp. 5–53, 2004. DOI: [10.1145/963770.963772](https://doi.org/10.1145/963770.963772). Supports protocol- and task-sensitive evaluation.

33. **[Burke2002]** Robin Burke. “Hybrid Recommender Systems: Survey and Experiments.” *User Modeling and User-Adapted Interaction*, 12(4), pp. 331–370, 2002. DOI: [10.1023/A:1021240730564](https://doi.org/10.1023/A:1021240730564). Supports hybrid-recommender terminology and the need to distinguish it from hybrid retrieval.

34. **[Felfernig2011]** Alexander Felfernig, Gerhard Friedrich, Dietmar Jannach, and Markus Zanker. “Developing Constraint-Based Recommenders.” In Francesco Ricci, Lior Rokach, Bracha Shapira, and Paul B. Kantor (eds.), *Recommender Systems Handbook*, pp. 187–215, Springer, 2011. DOI: [10.1007/978-0-387-85820-3_6](https://doi.org/10.1007/978-0-387-85820-3_6). Supports explicit feasibility and constraint-based recommendation.

35. **[Jameson2015]** Anthony Jameson, Martijn C. Willemsen, Alexander Felfernig, Marco de Gemmis, Pasquale Lops, Giovanni Semeraro, and Li Chen. “Human Decision Making and Recommender Systems.” In Francesco Ricci, Lior Rokach, and Bracha Shapira (eds.), *Recommender Systems Handbook*, 2nd ed., pp. 611–648, Springer, 2015. DOI: [10.1007/978-1-4899-7637-6_18](https://doi.org/10.1007/978-1-4899-7637-6_18). Supports the decision-aid and accuracy–effort framing.

36. **[Parasuraman2000]** Raja Parasuraman, Thomas B. Sheridan, and Christopher D. Wickens. “A Model for Types and Levels of Human Interaction with Automation.” *IEEE Transactions on Systems, Man, and Cybernetics—Part A*, 30(3), pp. 286–297, 2000. DOI: [10.1109/3468.844354](https://doi.org/10.1109/3468.844354). Supports the distinction between recommendation and autonomous action.

37. **[Pu2011]** Pearl Pu, Li Chen, and Rong Hu. “A User-Centric Evaluation Framework for Recommender Systems.” *Proceedings of the Fifth ACM Conference on Recommender Systems (RecSys ’11)*, pp. 157–164, 2011. DOI: [10.1145/2043932.2043962](https://doi.org/10.1145/2043932.2043962). Supports multidimensional recommender user evaluation.

38. **[Pu2012]** Pearl Pu, Li Chen, and Rong Hu. “Evaluating Recommender Systems from the User’s Perspective: Survey of the State of the Art.” *User Modeling and User-Adapted Interaction*, 22(4–5), pp. 317–355, 2012. DOI: [10.1007/s11257-011-9115-7](https://doi.org/10.1007/s11257-011-9115-7). Supports user-centered constructs and measurement selection.

39. **[Knijnenburg2012]** Bart P. Knijnenburg, Martijn C. Willemsen, Zeno Gantner, Hakan Soncu, and Chris Newell. “Explaining the User Experience of Recommender Systems.” *User Modeling and User-Adapted Interaction*, 22(4–5), pp. 441–504, 2012. DOI: [10.1007/s11257-011-9118-4](https://doi.org/10.1007/s11257-011-9118-4). Supports separation of objective system properties, perceptions, experience, and behavior.

40. **[Brooke1996]** John Brooke. “SUS: A ‘Quick and Dirty’ Usability Scale.” In Patrick W. Jordan, Bruce Thomas, Bernard A. Weerdmeester, and Ian L. McClelland (eds.), *Usability Evaluation in Industry*, pp. 189–194, Taylor & Francis, 1996. Supports the standard ten-item SUS instrument.

41. **[Sauro2009]** Jeff Sauro and Joseph S. Dumas. “Comparison of Three One-Question, Post-Task Usability Questionnaires.” *Proceedings of CHI 2009*, pp. 1599–1608, 2009. DOI: [10.1145/1518701.1518946](https://doi.org/10.1145/1518701.1518946). Supports post-task single-item ease measurement.

42. **[OCIImageSpec]** Open Container Initiative. “OCI Image Format Specification.” Living specification, accessed 8 September 2026. [Manifest specification](https://specs.opencontainers.org/image-spec/manifest/) and [image-layout specification](https://specs.opencontainers.org/image-spec/image-layout/). Supports manifests, descriptors, digests, and ordered filesystem layers.

43. **[DockerLayers2026]** Docker, Inc. “Understanding the Image Layers.” *Docker Documentation*, living documentation, accessed 8 September 2026. [Official documentation](https://docs.docker.com/get-started/docker-concepts/building-images/understanding-image-layers/). Supports immutable layers and reuse across images.

44. **[Harter2016]** Tyler Harter, Brandon Salmon, Rose Liu, Andrea C. Arpaci-Dusseau, and Remzi H. Arpaci-Dusseau. “Slacker: Fast Distribution with Lazy Docker Containers.” *14th USENIX Conference on File and Storage Technologies (FAST ’16)*, pp. 181–195, 2016. [USENIX proceedings page](https://www.usenix.org/conference/fast16/technical-sessions/presentation/harter). Supports the empirical dependence of startup and distribution on accessed data and storage design.

45. **[Anwar2018]** Ali Anwar, Mohamed Mohamed, Vasily Tarasov, Michael Littley, Lukas Rupprecht, Yue Cheng, Nannan Zhao, Dimitrios Skourtis, Amit S. Warke, Heiko Ludwig, Dean Hildebrand, and Ali R. Butt. “Improving Docker Registry Design Based on Production Workload Analysis.” *16th USENIX Conference on File and Storage Technologies (FAST ’18)*, pp. 265–278, 2018. [USENIX proceedings page](https://www.usenix.org/conference/fast18/presentation/anwar). Supports trace-based registry analysis and empirical caching/prefetch design.

---

## Quality audit and readiness

### Coverage audit

- Repository behavior is separated from general literature claims.
- JupyterHub/KubeSpawner, Kubernetes resource semantics, rightsizing, NL/intent interfaces, sparse/dense/hybrid retrieval, RRF, constraint-aware recommendation, decision support, human evaluation, and image layers are covered.
- Forty-five academic or authoritative sources are included; foundational sources are preferred for algorithms and official documentation for platform behavior.
- B0, P1, P2, and P3 are distinguished; P2 is the main method and P3 is explicitly not retained.
- Planned, development, historical, and confirmatory evidence are not conflated.
- Storage sharing is stated as a testable property, not a guaranteed saving.

### Citation audit

- Every bibliography key appears in the chapter draft.
- Every bracketed scholarly citation in the chapter has a bibliography entry.
- DOI links are used where a DOI is available; official proceedings/specification/documentation links are used otherwise.
- Custom questionnaire items are not misrepresented as validated scales.

### Remaining gaps before thesis submission

1. Convert the references to the university's mandated style or reference-manager format; the content currently uses readable citation keys rather than a specific CSL style.
2. Recheck living documentation URLs and access dates immediately before submission.
3. Add results only after immutable Protocol-v5 artifacts exist. In particular, do not turn E3, E4, or E5-storage hypotheses into past-tense findings.
4. If the deployed catalog, policy, preview lifetime, or P2 weights change, update both the architecture chapter and the repository-specific sentences here; do not silently describe a different version.
5. Have the supervisor confirm whether “context-aware” should appear in the chapter title, because the implemented context is task-level rather than personalized or history-based.

### Strongest defensible positioning sentence

> Intent-Spawner contributes and evaluates a bounded JupyterHub/Kubernetes decision-support workflow that composes established intent representation, hybrid retrieval, feasibility constraints, deterministic ranking, policy validation, and human confirmation to recommend a curated notebook software-and-resource environment before execution.

### Claims to avoid

- “Intent-Spawner invents hybrid retrieval/RRF/constraint-based recommendation.”
- “Intent-Spawner predicts exact workload demand” or “guarantees optimal resources.”
- “A feasible recommendation is guaranteed to schedule.”
- “P2 is a Kubernetes scheduler, autoscaler, or autonomous operator.”
- “P2 is personalized from user history.”
- “P3/LLM reranking improves results.”
- “B0 has lower MRR/nDCG than P2.”
- “Users are faster, more accurate, or more satisfied,” until E3 is executed.
- “P2 improves cluster utilization,” until E4 is executed.
- “The image catalog saves a stated amount of storage,” until E5 storage is executed on the declared registry/runtime/platform.
- Any causal or general-performance claim beyond the sampled workload families, participants, cluster, catalog, and protocol version.

### Final readiness verdict

**Readiness: 92/100 — thesis-ready as a repository-informed Background and Related Work draft.** The conceptual coverage, comparison, evidence boundaries, and source base are strong enough for supervisor review and thesis integration. The remaining work is primarily institutional citation formatting and later insertion of independently produced Protocol-v5 results in the evaluation chapter, not retroactive revision of the background to fit outcomes.
