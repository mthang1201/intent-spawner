# Research Evaluation Implementation Roadmap

## Purpose

This document audits the repository as code, not as thesis intent. A feature is marked implemented only when it is present in runnable repository files or directly validated by a command. Claims from slides, notebooks, or prose are treated as plans unless matching implementation exists.

The repository currently supports a runnable demonstration of a context-aware pre-spawn recommendation idea for JupyterHub/Z2JH. It does not yet contain a complete comparative research evaluation package.

## Current Repository State

Observed structure:

- `README.md`, `DEMO_SCRIPT.md`, and `CLEANUP.md` describe a local OrbStack/Kubernetes demo.
- `helm/baseline-values.yaml` defines static Small, Medium, and Large `singleuser.profileList` options.
- `helm/proposed-values.yaml` injects a JupyterHub `extraConfig` block that adds an intent form, parses form data, runs rule-based recommendation logic, sets KubeSpawner CPU/memory fields before spawn, and stores explanation in environment variables and pod annotations.
- `recommender/recommender.py` contains the standalone rule mirror and JSON CLI output.
- `recommender/test_recommender.py` contains four unit tests for Small, Medium, Large, and GPU-or-Large rule paths.
- `scripts/` contains demo install, uninstall, pod-creation, watch, port-forward, and capacity helper commands.
- `workload/` contains bounded scripts for late OOM, light EDA, and training-like memory allocation.
- `notebooks/` contains three short notebooks that invoke the workload scripts.
- `k8s/` contains simple pod and ResourceQuota manifests.
- No CI configuration, Prometheus-backed metrics collector, cluster-mutating
  experiment runner, or history store was found.

This roadmap predates the reproducibility packaging pass. Treat `README.md`,
`docs/ARTIFACT_MANIFEST.md`, and `docs/evaluation/RESULTS.md` as the current
artifact entry points.

## Implemented / Not Implemented / Uncertain Matrix

| Capability | Status | Code evidence | Verification command |
| --- | --- | --- | --- |
| Baseline static Small/Medium/Large profiles | Implemented | `helm/baseline-values.yaml` has `singleuser.profileList` entries and resource overrides. | `rg -n "profileList|cpu_guarantee|mem_limit" helm/baseline-values.yaml` |
| Proposed intent/context spawn form | Implemented | `helm/proposed-values.yaml` sets `c.KubeSpawner.options_form`. | `rg -n "options_form|intent|code_context|dataset_size_gb" helm/proposed-values.yaml` |
| Pre-spawn resource override before pod creation | Implemented | `context_pre_spawn_hook` sets `cpu_guarantee`, `cpu_limit`, `mem_guarantee`, and `mem_limit`. | `rg -n "pre_spawn_hook|cpu_guarantee|mem_guarantee|mem_limit" helm/proposed-values.yaml` |
| Explainable recommendation | Implemented for demo | Reasons are returned by the recommender and written to env vars/annotations. | `python3 recommender/recommender.py --intent "train sklearn model" --dataset-gb 1.5 --code-context "import pandas as pd; model.fit(X, y)"` |
| Standalone recommender tests | Implemented | Four pytest cases cover rule categories. | `.venv/bin/python -m pytest recommender/test_recommender.py` |
| GPU-like decision | Partially implemented | Recommender can return `gpu_or_large`; Helm maps it to Large resources because demo has no GPU profile. | `python3 recommender/recommender.py --intent "deep learning" --code-context "import torch; model.cuda()"` |
| Admin policy engine | Not substantially implemented | Allowed profile mapping is hard-coded as `PROFILE_RESOURCES`; no external policy file, admission check, quota-aware policy, or per-admin configuration exists. | `rg -n "policy|quota|allowed|PROFILE_RESOURCES" . -g "!*.ipynb"` |
| History-aware provisioning | Not substantially implemented | Slides/prose mention history, but there is no storage model, event capture, feature store, metrics import, or history-based rule path in code. | `rg -n "history|historical|peak|restart|pending|OOM|usage" . -g "!*.ipynb" -g "!*__pycache__*"` |
| Metrics collection | Not implemented | Docs mention optional `kubectl top`; no Prometheus, metrics-server deployment, scraper, or result writer exists. Current cluster reports Metrics API unavailable. | `rg -n "metrics|prometheus|kubectl top|result" . -g "!*.ipynb"` and `kubectl top nodes` |
| Baseline mode | Manually runnable | `scripts/install-baseline.sh` installs `helm/baseline-values.yaml`. | `bash -n scripts/install-baseline.sh` and `helm template ... --values helm/baseline-values.yaml` |
| Intent-only mode | Implemented for experiment records | `experiments.methods` supports `intent_only`, which uses intent text only and passes no dataset-size or code-context signal to the recommender. JupyterHub itself still uses one proposed spawn form. | `.venv/bin/python -m experiments.recorder --workload-id ml_sklearn_fit_small --method intent_only --repeat-index 0 --environment-id local-smoke --no-append` |
| Context-aware mode | Manually runnable | Proposed Helm config combines intent, dataset size, and code context. | `bash -n scripts/install-proposed.sh` and `helm template ... --values helm/proposed-values.yaml` |
| Experiment automation | Implemented for local synthetic benchmark orchestration | `experiments.runner` supports method/workload/category selection, repeats, seeds, timeouts, smoke/full/dry-run modes, resume, unique run directories, and derived CSV aggregation. It does not create Kubernetes pods. | `.venv/bin/python -m experiments.runner --smoke --dry-run --environment-id local-smoke` |
| Machine-readable experiment outputs | Implemented for normalized records | `experiments.recorder` and `experiments.runner` emit schema-versioned JSONL records and preserve supporting stdout/stderr artifacts for attempted local runs. | `.venv/bin/python -m experiments.runner --smoke --environment-id local-smoke` |
| Local Kubernetes environment | Available in this audit environment | `scripts/check-cluster.sh` reports context `orbstack` and one Ready node. | `bash scripts/check-cluster.sh` |
| Metrics API in local cluster | Not available in this audit environment | `kubectl top nodes` returns `Metrics API not available`. | `kubectl top nodes` |
| CI | Not implemented | No `.github/`, tox, nox, pre-commit, or project metadata was found. | `find . -maxdepth 4 -type f -path "./.github/*" -o -name "tox.ini" -o -name "noxfile.py" -o -name ".pre-commit-config.yaml"` |

## Gap Analysis

The main gap is between a strong demonstration artifact and a research evaluation artifact.

Implemented today:

- A lightweight, explainable, rule-based recommender.
- A Z2JH/KubeSpawner integration point that applies the recommendation before user pod creation.
- Demonstrations for late OOM, over-requesting, and applied recommendation.
- Basic unit tests and Helm renderability.

Missing for defensible evaluation:

- A benchmark workload suite with multiple workload classes, seeds, and fixed resource expectations.
- A comparative experiment harness that can run baseline, intent-only, and context-aware modes with the same workload set.
- A result schema that preserves raw command outputs and normalized metrics.
- Usage metrics or an alternative documented measurement path.
- Repetition, uncertainty reporting, and failure classification.
- Reproducibility metadata: commit hash, tool versions, cluster capacity, chart version, image digests, experiment config, and raw output paths.
- Data-governance documentation for intent text, code snippets, notebooks, user IDs, and history signals.

## Recommended Evaluation Scope

Keep the thesis claim scoped to the prototype:

> A lightweight pre-spawn recommendation layer can translate user intent and optional context into administrator-approved JupyterHub profiles, apply the resulting resource request before KubeSpawner creates the pod, and preserve an auditable explanation.

Avoid claiming general cluster-wide efficiency or universal predictive accuracy until comparative results exist across repeated benchmark runs.

The first complete evaluation should compare:

- Baseline static/manual profile selection.
- Intent-only recommendation: intent plus dataset-size hint, with code context intentionally omitted.
- Context-aware recommendation: intent plus dataset-size hint plus safe code/context signals.

Treat history-aware provisioning as future work unless a real history subsystem is implemented and tested.

## Recommended Research Claims

Supported now by code and local checks:

- The recommender is rule-based and explainable.
- The standalone recommender can produce Small, Medium, Large, and GPU-or-Large labels.
- The proposed Helm configuration can apply CPU and memory settings through a KubeSpawner pre-spawn hook.
- Demo workloads can illustrate underprovisioning and overprovisioning mechanisms.
- The local audit environment can access an OrbStack Kubernetes cluster.

Not yet supported:

- The proposed method reduces OOM rate across a representative workload suite.
- The proposed method improves cluster density under realistic multi-user arrivals.
- Context-aware mode outperforms intent-only mode.
- History-aware provisioning improves future spawns.
- Real usage waste ratio is measured end to end.

## RQ-To-Experiment-To-Metric Matrix

| Research question | Experiment | Primary metrics | Required outputs |
| --- | --- | --- | --- |
| RQ1: Can intent and lightweight context be mapped to approved profiles before spawn? | Run fixed workload prompts through recommender and proposed JupyterHub spawn path. | Recommendation label, applied CPU/memory, explanation present, policy compliance. | `recommendation.jsonl`, pod describe output, Helm values checksum. |
| RQ2: Does recommendation reduce late underprovisioning compared with static Small choices? | Run memory-growing workloads under baseline Small and recommended profile. | OOMKilled count, completion count, time-to-success, rerun count. | Pod phase/events JSON, workload logs, summary CSV. |
| RQ3: Does recommendation reduce defensive over-requesting for light workloads? | Compare baseline Large-for-light behavior with intent/context recommended Small. | Requested CPU/RAM, peak CPU/RAM if metrics available, request-to-peak ratio, schedulable pod count. | Resource request JSON, metrics snapshots or documented absence, scheduler events. |
| RQ4: Does code/context add value beyond intent-only? | Run same benchmark set through intent-only and context-aware modes. | Accuracy against expected profile class, over/under recommendation count, explanation quality flags. | Mode-labeled recommendation JSONL and confusion matrix. |
| RQ5: What are the safety/privacy boundaries? | Audit stored fields and run synthetic sensitive-input cases. | Raw notebook stored: yes/no, sensitive token retained: yes/no, explanation redaction status. | Data-governance report and redaction test logs. |

## Proposed Benchmark Structure

Add this structure in a future implementation task:

```text
benchmarks/
  workloads/
    light_python/
      workload.py
      workload.yaml
    pandas_medium/
      workload.py
      workload.yaml
    sklearn_training/
      workload.py
      workload.yaml
    gpu_hint_cpu_fallback/
      workload.py
      workload.yaml
    oom_guard/
      workload.py
      workload.yaml
  intents/
    benchmark_intents.yaml
experiments/
  configs/
    baseline.yaml
    intent_only.yaml
    context_aware.yaml
  raw/
    README.md
  summaries/
    README.md
```

Each workload should define:

- workload ID and version.
- safe memory and CPU bounds.
- expected profile class for evaluation, with rationale.
- command to run inside the pod.
- dataset-size hint, using synthetic/generated data where possible.
- whether code context is available.
- cleanup behavior.

## Proposed Experiment Result Schema

Use newline-delimited JSON for raw normalized events and keep original command output beside it.

```json
{
  "schema_version": "1.0",
  "run_id": "2026-07-19T120000Z-local-orbstack-001",
  "commit_sha": "git-commit-here",
  "mode": "context_aware",
  "workload_id": "sklearn_training",
  "trial_index": 1,
  "cluster": {
    "context": "orbstack",
    "node_count": 1,
    "kubernetes_version": "v1.33.9+orb1"
  },
  "input": {
    "intent": "I will train a scikit-learn model on a 1.5GB CSV dataset",
    "dataset_size_gb": 1.5,
    "code_context_features": ["pandas", "sklearn", ".fit("],
    "raw_code_context_stored": false
  },
  "recommendation": {
    "profile": "large",
    "score": 4,
    "reasons": ["dataset size >= 0.5GB", "training/modeling context detected"]
  },
  "applied_resources": {
    "cpu_request_m": 1500,
    "cpu_limit_m": 2000,
    "memory_request_mi": 1536,
    "memory_limit_mi": 2048
  },
  "outcome": {
    "pod_phase": "Succeeded",
    "oom_killed": false,
    "pending_seconds": 0,
    "time_to_success_seconds": 42.0
  },
  "metrics": {
    "peak_cpu_m": null,
    "peak_memory_mi": null,
    "metrics_source": "not_available"
  },
  "artifacts": {
    "raw_stdout_path": "experiments/raw/run-id/workload/stdout.txt",
    "pod_json_path": "experiments/raw/run-id/workload/pod.json"
  }
}
```

The JSON above is a schema example, not an observed result.

## Sequential Implementation Milestones

1. Freeze current demo evidence.
   - Record commit SHA, tool versions, chart version, cluster capacity, and current demo commands.
   - Add a `docs/evaluation/RUNBOOK.md` that separates manual demo steps from benchmark steps.

2. Add benchmark metadata only.
   - Create workload descriptors with safe bounds, expected class, synthetic-data assumptions, and cleanup rules.
   - Do not add raw datasets or raw notebooks.

3. Add recommender batch evaluation.
   - Implement a small script that runs benchmark intents through `recommender.recommend_profile`.
   - Emit JSONL and summary CSV.
   - Compare intent-only and context-aware inputs without touching Kubernetes.

4. Add Kubernetes experiment harness.
   - Run one workload per trial in `z2jh-context-demo`.
   - Capture pod JSON, events, logs, start/end timestamps, and exit status.
   - Keep each raw output file immutable once written.

5. Add metrics path.
   - If metrics-server is installed, collect `kubectl top` snapshots and document sampling limitations.
   - For stronger claims, add Prometheus or another time-series collector and pin its install/config.
   - If metrics are unavailable, report request-based metrics only and label usage metrics as unavailable.

6. Add comparative analysis.
   - Produce tables for baseline, intent-only, and context-aware modes.
   - Report repeated trials and uncertainty.
   - Include ablation results before making claims about context value.

7. Add reproducibility package.
   - Add exact commands, expected environment, cleanup commands, schema docs, and known failure modes.
   - Add CI for unit tests, syntax checks, and non-cluster validation.

8. Revisit history-aware provisioning as future work.
   - Only implement after event capture, privacy rules, persistent storage, and evaluation design are stable.

## History-Aware Provisioning

History-aware provisioning is not substantially implemented in this repository. It appears in the slide narrative as an architectural component, but code does not currently collect prior OOM events, peak usage, restarts, pending time, user/workload fingerprints, or history-derived recommendations.

Recommended treatment for the thesis:

- Present history awareness as future work.
- Keep current evaluated system to baseline, intent-only, and context-aware modes.
- Do not imply measured effectiveness for history-aware adjustment.

Implementation cost and dependencies:

- Event capture: collect pod status, termination reasons, restart counts, scheduling events, and timestamps.
- Metrics capture: install and validate metrics-server for basic snapshots or Prometheus for reliable time series.
- Storage: choose SQLite/PostgreSQL/Kubernetes CRDs/object storage for durable history records.
- Privacy design: store derived features and aggregate events, not raw notebooks or sensitive code/data.
- Workload identity: define a safe fingerprinting strategy so "similar workload" is meaningful without storing raw content.
- Policy integration: define admin-approved escalation/de-escalation rules and guardrails.
- Evaluation: add cold-start and warm-history trials with repeated runs and ablations.

This is large enough to be a separate implementation milestone, not a documentation-only fix.

## Risks And Blockers

- Metrics API is unavailable in the audited local cluster, so current usage metrics cannot be collected with `kubectl top`.
- Demo scripts mutate Kubernetes resources and should not be run on shared or production clusters.
- `quay.io/jupyter/scipy-notebook:latest` is not pinned by digest, which weakens reproducibility.
- The recommender logic is duplicated in Python and Helm inline Python; drift is possible.
- The proposed mode is configured manually through Helm, not as a packaged extension.
- Intent-only mode does not yet exist as a named runnable mode.
- Experiment outputs are not yet normalized or preserved by scripts.
- History-aware provisioning needs storage, event capture, privacy controls, and new evaluation design.

## Commands Used To Verify Major Statements

Repository inventory:

```bash
rg --files -g '!*node_modules*' -g '!*.git*'
find .. -name AGENTS.md -print
find . -maxdepth 4 -type f \( -path './.github/*' -o -name 'pyproject.toml' -o -name 'setup.cfg' -o -name 'tox.ini' -o -name 'noxfile.py' -o -name 'Makefile' -o -name '.pre-commit-config.yaml' -o -name 'requirements*.txt' -o -name 'environment.yml' -o -name 'Pipfile' -o -name 'poetry.lock' -o -name 'uv.lock' -o -name 'Dockerfile' -o -name 'docker-compose.yml' \) -print
```

Feature search:

```bash
rg -n "history|historical|metric|metrics|prometheus|benchmark|experiment|baseline|intent-only|context-aware|context_aware|result|json|csv|parquet|mlflow|wandb|recommend|profile|KubeSpawner|pre_spawn|options_form|options_from_form" . -g '!*__pycache__*' -g '!*.ipynb'
```

Notebook inspection:

```bash
python3 - <<'PY'
import json
from pathlib import Path
for p in sorted(Path('notebooks').glob('*.ipynb')):
    nb = json.loads(p.read_text())
    print(p, len(nb.get('cells', [])))
    for cell in nb.get('cells', []):
        print(cell.get('cell_type'), ''.join(cell.get('source', [])).strip().splitlines()[:1])
PY
```

Unit and syntax checks:

```bash
tmpenv=$(mktemp -d /tmp/intent-spawner-venv.XXXXXX)
python3 -m venv "$tmpenv"
"$tmpenv/bin/python" -m pip install -q -r requirements-dev.txt
"$tmpenv/bin/python" -m pytest recommender/test_recommender.py
rm -rf "$tmpenv"

python3 -m compileall -q recommender workload scripts/generate-capacity-values.py
bash -n scripts/*.sh
```

Helm and Kubernetes manifest validation:

```bash
helm template context-demo jupyterhub/jupyterhub --version 4.0.0 --namespace z2jh-context-demo --values helm/baseline-values.yaml >/tmp/intent-spawner-baseline-render.yaml
helm template context-demo jupyterhub/jupyterhub --version 4.0.0 --namespace z2jh-context-demo --values helm/proposed-values.yaml >/tmp/intent-spawner-proposed-render.yaml

kubectl apply --dry-run=client -f k8s/idle-large-pod.yaml
kubectl apply --dry-run=client -f k8s/idle-small-pod.yaml
kubectl apply --dry-run=client -f k8s/resource-quota.yaml
```

Cluster and metrics availability:

```bash
bash scripts/check-cluster.sh
kubectl auth can-i create pods -n z2jh-context-demo
kubectl auth can-i create namespaces
kubectl get storageclass
kubectl top nodes
kubectl get apiservice v1beta1.metrics.k8s.io -o wide
```

Recommender behavior:

```bash
python3 recommender/recommender.py --intent 'I will train a scikit-learn model on a 1.5GB CSV dataset' --dataset-gb 1.5 --code-context 'import pandas as pd
from sklearn.ensemble import RandomForestClassifier
model.fit(X, y)'
```

Embedded Helm Python parse check without requiring PyYAML:

```bash
python3 - <<'PY'
import ast
from pathlib import Path
text = Path('helm/proposed-values.yaml').read_text()
lines = text.splitlines()
start = next(i + 1 for i, line in enumerate(lines) if line.strip() == '00-context-aware-recommender: |')
block = []
for line in lines[start:]:
    if line and not line.startswith('      '):
        break
    block.append(line[6:] if line.startswith('      ') else '')
ast.parse('\n'.join(block))
print('helm/proposed-values.yaml: embedded extraConfig Python parses')
PY
```
