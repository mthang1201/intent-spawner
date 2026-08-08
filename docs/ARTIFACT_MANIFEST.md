# Artifact Manifest

## Source And Configuration

- `recommender/`:
  - `base.py`: unified `Recommender` protocol, `RecommendationRequest`, and `SpawnRecommendation` schemas.
  - `registry.py`: provider-neutral backend factory (`create_recommender`).
  - `rule_based.py`: deterministic heuristic and keyword matching recommender.
  - `external_llm.py`: OpenAI-compatible external chat completions client with Kubernetes Secret support (e.g. Gemini 1.5 Flash).
  - `self_hosted_llm.py`: private inference adapter for local/in-cluster engines (e.g. Ollama, vLLM).
  - `dynamic_resources.py`: policy-bounded continuous CPU/RAM/GPU profile generator.
  - `image-catalog.yaml`: curator-managed immutable notebook image catalog pinned to SHA-256 digests.
  - `models.py`, `policy.py`, `reliability.py`: core dataclasses, policy validators, and network deadline managers.
- `helm/`:
  - `baseline-values.yaml`: static-profile baseline JupyterHub values.
  - `proposed-values.yaml`: context-aware pre-spawn preview form, pre-spawn hook, and catalog-mode values.
  - `reprovision-values.yaml`: stop-and-recreate intent-aware re-provisioning overlay with PVC retention.
  - `dynamic-values.yaml`: continuous dynamic profile generation overlay with quota admission controls.
  - `gemini-values.yaml`: external LLM deployment configuration using Google Gemini 1.5 Flash.
  - `ollama-values.yaml`: self-hosted LLM deployment configuration using local Ollama.
- `k8s/`: demo pods and resource quota manifests.
- `workload/`: demo workload scripts mounted into JupyterLab containers.

---

## Evaluation & Benchmarks (Protocol v4)

- `evaluation_v4/`:
  - `gold_set.py` & `gold_dataset.json`: bilingual 60-intent benchmark dataset (English and Vietnamese) across EDA, Data Processing, ML Training, and Deep Learning.
  - `recommenders.py` & `run_recommenders.py`: multi-backend recommender benchmark runner.
  - `plan_system.py`: paired system effectiveness plan and trial generator.
  - `metrics.py`: recommendation quality, system effectiveness, and user decision metrics.
  - `statistics.py`: family-clustered bootstrap confidence intervals and Wilcoxon signed-rank tests.
  - `evidence_schema.py` & `claim_gates.py`: formal claim gates separating synthetic runs from cluster evidence.

---

## Setup And Verification

- `requirements.txt`: runtime Python dependencies.
- `requirements-dev.txt`: development, testing, and evaluation dependencies.
- `scripts/setup.sh`: creates `.venv` and installs pinned dependencies.
- `scripts/check.sh`: complete repository verification command (15 automated stages).
- `scripts/install-proposed.sh`: installs the proposed context-aware JupyterHub demo.
- `scripts/install-dynamic.sh`: packages the recommender library and deploys dynamic mode.
- `scripts/install-baseline.sh`: installs the baseline static hardware profile demo.
- `scripts/port-forward.sh`: local port forwarder to JupyterHub proxy.
- `scripts/watch-pods.sh`: pod monitoring helper.
- `scripts/uninstall.sh`: complete demo namespace deletion.

---

## Documentation Index

- `README.md`: executive summary, problem statement, Task A–F feature breakdown, and quickstart guides.
- `docs/ARCHITECTURE.md`: comprehensive system architecture and sequence diagrams.
- `docs/GETTING_STARTED.md`: step-by-step onboarding, interactive demo, and evaluation guide.
- `DEMO_SCRIPT.md`: presentation runbook with live scenes covering all prototype features.
- `docs/EXTERNAL_LLM_RECOMMENDER.md`: external LLM integration and Secret wiring specification.
- `docs/SELF_HOSTED_LLM_RECOMMENDER.md`: local Ollama/vLLM self-hosted inference specification.
- `docs/INTENT_AWARE_REPROVISIONING.md`: stop-and-recreate re-provisioning with storage retention.
- `docs/DYNAMIC_PROFILE_GENERATION.md`: continuous resource allocation and quota guardrails.
- `docs/HELM_BACKEND_DEPLOYMENT.md`: production Helm wiring and ConfigMap rollout architecture.
- `docs/evaluation/EVALUATION_V4_PROTOCOL.md`: Protocol v4 evaluation methodology and benchmark metrics.
- `docs/evaluation/RECOMMENDATION_PREVIEW_DESIGN.md`: preview state machine, audit schema, and scalability.
- `docs/evaluation/IMPLEMENTATION_ROADMAP.md`: complete capability matrix for Tasks A–F.
- `docs/DATA_GOVERNANCE.md`: privacy minimization and raw evidence storage rules.
- `CLEANUP.md`: cluster cleanup instructions and local artifact lifecycle.
