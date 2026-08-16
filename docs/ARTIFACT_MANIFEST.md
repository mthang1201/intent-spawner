# Artifact Manifest

## Source And Configuration

- `recommender/`:
  - `base.py`: unified `Recommender` protocol, `RecommendationRequest`, and `SpawnRecommendation` schemas.
  - `registry.py`: provider-neutral backend factory (`create_recommender`).
  - `rule_based.py`: deterministic heuristic and keyword matching recommender.
  - `external_llm.py`: OpenAI-compatible external chat completions client with Kubernetes Secret support; the Protocol-v4 live configuration used `gemini-3.5-flash`.
  - `self_hosted_llm.py`: private inference adapter for local/in-cluster engines (e.g. Ollama, vLLM).
  - `dynamic_resources.py`: policy-bounded continuous CPU/RAM/GPU profile generator.
  - `image-catalog.yaml`: curator-managed immutable notebook image catalog pinned to SHA-256 digests.
  - `models.py`, `policy.py`, `reliability.py`: core dataclasses, policy validators, and network deadline managers.
- `helm/`:
  - `baseline-values.yaml`: static-profile baseline JupyterHub values.
  - `proposed-values.yaml`: context-aware pre-spawn preview form, pre-spawn hook, and catalog-mode values.
  - `reprovision-values.yaml`: stop-and-recreate intent-aware re-provisioning overlay with PVC retention.
  - `dynamic-values.yaml`: continuous dynamic profile generation overlay with quota admission controls.
  - `gemini-values.yaml`: external LLM deployment configuration using Google `gemini-3.5-flash`.
  - `ollama-values.yaml`: self-hosted LLM deployment configuration using local Ollama.
- `k8s/`: demo pods and resource quota manifests.
- `workload/`: demo workload scripts mounted into JupyterLab containers.

---

## Evaluation & Benchmarks (Protocol v4)

- `evaluation_v4/`:
  - `dataset.py` and `benchmarks/intent-gold-v4.yaml`: validation and the frozen bilingual 60-sample dataset (12 development + 48 held-out across 24 families).
  - `recommenders.py` and `run_recommenders.py`: four-method benchmark composition, randomized/repeatable execution, telemetry, and safe resume.
  - `plan_system.py`, `run_system.py`, and `pod_runner.py`: paired Stage C planning, bounded Kubernetes execution, sidecar collection, cleanup, and strict resume validation.
  - `schemas.py` and `validate_evidence.py`: JSONL schemas, record validation, checksums, completion gates, and supporting-evidence checks.
  - `statistics.py`, `analyze.py`, and `render_figures.py`: family-aware intervals, paired tests with Holm correction, tables, claim gates, and figures.
  - `combine_external_results.py`: creates a derived four-method view by replacing only historical missing-credential external cells without rewriting the frozen source.
  - `run_reprovision.py` and `run_pending_diagnostic.py`: bounded re-provision and scheduling diagnostics.

- Authoritative Protocol-v4 evidence:
  - `results/v4-external-confirmatory-20260813T045543Z`: 240-trial `gemini-3.5-flash` matrix.
  - `results/v4-combined-evidence-20260813T050500Z`: derived 960-row four-method view.
  - `results/v4-stage-c-confirmatory-20260813T021600Z`: 320 observed Stage C trials.
  - `results/v4-final-combined-external-analysis-v2-20260813T050836Z`: corrected combined analysis.
  - `docs/evaluation/PROTOCOL_V4_EXTERNAL_SHA256SUMS.txt`: tracked checksum manifest for the external and combined handoff.

---

## Setup And Verification

- `requirements.txt`: runtime Python dependencies.
- `requirements-dev.txt`: development, testing, and evaluation dependencies.
- `scripts/setup.sh`: creates `.venv` and installs pinned dependencies.
- `scripts/check.sh`: multi-stage repository verification command covering focused tests, evidence integrity, dry runs, syntax, Helm rendering, and Kubernetes client validation when their tools are available.
- `scripts/install-proposed.sh`: installs the proposed context-aware JupyterHub demo.
- `scripts/install-dynamic.sh`: packages the recommender library and deploys dynamic mode.
- `scripts/install-baseline.sh`: installs the baseline static hardware profile demo.
- `scripts/port-forward.sh`: local port forwarder to JupyterHub proxy.
- `scripts/watch-pods.sh`: pod monitoring helper.
- `scripts/uninstall.sh`: complete demo namespace deletion.

---

## Documentation Index

- `README.md`: executive summary, problem statement, core system architecture & feature breakdown, and quickstart guides.
- `docs/ARCHITECTURE.md`: comprehensive system architecture and sequence diagrams.
- `docs/GETTING_STARTED.md`: step-by-step onboarding, interactive demo, and evaluation guide.
- `DEMO_SCRIPT.md`: presentation runbook with live scenes covering all prototype features.
- `docs/EXTERNAL_LLM_RECOMMENDER.md`: external LLM integration and Secret wiring specification.
- `docs/SELF_HOSTED_LLM_RECOMMENDER.md`: local Ollama/vLLM self-hosted inference specification.
- `docs/INTENT_AWARE_REPROVISIONING.md`: stop-and-recreate re-provisioning with storage retention.
- `docs/DYNAMIC_PROFILE_GENERATION.md`: continuous resource allocation and quota guardrails.
- `docs/HELM_BACKEND_DEPLOYMENT.md`: production Helm wiring and ConfigMap rollout architecture.
- `docs/evaluation/EVALUATION_V4_PROTOCOL.md`: Protocol v4 evaluation methodology and benchmark metrics.
- `docs/evaluation/PROTOCOL_V4_REVISED_EVALUATION_REPORT.md`: authoritative combined Stage A/B/C result and RQ1-RQ5 claim matrix.
- `docs/evaluation/PROTOCOL_V4_EXTERNAL_LLM_LIVE_REPORT.md`: observed external-service reliability, raw/applied accuracy, latency, retries, fallback, and limitations.
- `docs/evaluation/STAGE_C_CONFIRMATORY_REPORT.md`: observed 4×8×10 cluster outcomes and family-aware inference.
- `docs/evaluation/PROTOCOL_V4_REPRODUCIBILITY.md`: non-mutating validation and safe reproduction commands.
- `docs/evaluation/RECOMMENDATION_PREVIEW_DESIGN.md`: preview state machine, audit schema, and scalability.
- `docs/evaluation/IMPLEMENTATION_ROADMAP.md`: system capability, evidence-status, and remaining production-work matrix.

- `docs/DATA_GOVERNANCE.md`: privacy minimization and raw evidence storage rules.
- `CLEANUP.md`: cluster cleanup instructions and local artifact lifecycle.
