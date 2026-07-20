# Repository Operating Notes

## Repository Structure

- `README.md`, `DEMO_SCRIPT.md`, `CLEANUP.md`: demo narrative, manual runbook, and cleanup notes.
- `helm/`: Zero to JupyterHub values for the baseline static profile demo and proposed context-aware pre-spawn demo.
- `recommender/`: standalone rule-based recommender and unit tests.
- `scripts/`: cluster checks, install/uninstall helpers, demo pod launchers, and capacity-value generation.
- `workload/`: bounded Python workloads used by demo pods and JupyterLab terminals.
- `benchmarks/`: deterministic synthetic workload manifest and runner.
- `k8s/`: standalone Kubernetes manifests for request/quota demonstrations.
- `docs/evaluation/`: research evaluation planning and future experiment documentation.

## Setup Commands

Use an isolated Python environment when installing test dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Cluster-oriented commands assume:

```bash
kubectl config current-context
helm repo add jupyterhub https://hub.jupyter.org/helm-chart/ --force-update
helm repo update
```

The demo namespace is `z2jh-context-demo`.

## Test And Validation Commands

Run the checks that fit the current environment:

```bash
python -m pytest recommender/test_recommender.py
python3 -m compileall -q recommender workload scripts/generate-capacity-values.py
bash -n scripts/*.sh
helm template context-demo jupyterhub/jupyterhub --version 4.0.0 --namespace z2jh-context-demo --values helm/baseline-values.yaml >/tmp/intent-spawner-baseline-render.yaml
helm template context-demo jupyterhub/jupyterhub --version 4.0.0 --namespace z2jh-context-demo --values helm/proposed-values.yaml >/tmp/intent-spawner-proposed-render.yaml
kubectl apply --dry-run=client -f k8s/idle-large-pod.yaml
kubectl apply --dry-run=client -f k8s/idle-small-pod.yaml
kubectl apply --dry-run=client -f k8s/resource-quota.yaml
```

Use `bash scripts/check-cluster.sh` only when read-only cluster inspection is acceptable.

## Formatting And Coding Conventions

- Keep Python simple, typed where practical, and compatible with the current direct `python3` invocation style.
- Prefer explicit rule names and human-readable recommendation reasons over opaque scoring.
- Keep shell scripts `set -euo pipefail` and print the commands they run when the command changes cluster state.
- Keep Helm values readable and scoped to the JupyterHub demo; avoid unrelated chart refactors.
- Do not introduce broad architecture changes without first documenting the evaluation need.

## Experiment Safety Rules

- Treat demo and experiment scripts as cluster-mutating unless proven otherwise.
- Keep all created Kubernetes resources inside `z2jh-context-demo` unless a document explicitly justifies otherwise.
- Do not run install, uninstall, pod-creating, quota-changing, or load-generating scripts on a shared or production cluster.
- Use bounded workloads only. Keep memory targets capped and documented.
- Preserve raw outputs from every experiment run before creating summaries.
- Never fabricate experimental results. If a result is illustrative, label it as simulated or example data.
- Clearly distinguish observed results from simulated examples in reports, slides, and roadmaps.
- Avoid storing raw notebooks, raw code snippets, dataset contents, usernames beyond what is needed for audit, secrets, or sensitive user data.
- Prefer storing derived features, recommendation inputs that are safe to retain, policy versions, pod/resource events, and aggregate metrics.
