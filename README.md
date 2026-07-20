# Intent Spawner Research Artifact

This repository packages the graduation-thesis prototype:

**Intent- and Context-Aware Profile Recommendation Layer for Zero to JupyterHub / KubeSpawner**

The artifact compares static/manual profile selection with an explainable
pre-spawn recommendation layer. It includes the demo Helm values, deterministic
synthetic benchmark workloads, immutable raw result snapshots, derived tables
and figures, and documentation for reproducing the local analysis.

## Quick Start

```bash
git clone https://github.com/mthang1201/intent-spawner.git
cd intent-spawner
bash scripts/setup.sh
bash scripts/check.sh
bash scripts/environment-report.sh --out /tmp/intent-spawner-environment.json --overwrite
.venv/bin/python -m experiments.runner --smoke --environment-id local-smoke --timeout 60
.venv/bin/python -m experiments.runner --full-matrix --repeats 5 --seed 20260719 --dry-run --environment-id local-dry-run
.venv/bin/python -m experiments.analyze_results \
  --experiment-dir experiments/raw/20260719T140431Z-matrix-aed48949 \
  --results-dir /tmp/intent-spawner-results \
  --results-md /tmp/intent-spawner-results/RESULTS.md \
  --environment-report results/environment-capability.json \
  --overwrite
```

Use `.venv/bin/python` explicitly if your shell does not activate the virtual
environment.

## Prerequisites

- Python 3.11 or newer. The artifact was validated locally with Python 3.14.5.
- `bash`, `git`, and `pip`.
- Optional for Helm/Kubernetes rendering and live demos: `helm`, `kubectl`, and
  a local Kubernetes cluster such as OrbStack, kind, minikube, or k3d.
- Optional for live resource-metric claims: Kubernetes Metrics API or a
  Prometheus-equivalent collection path. Without metrics, local synthetic runs
  still work, but peak Kubernetes CPU/memory claims must not be made.

The Helm demo uses the JupyterHub chart version `4.0.0`, namespace
`z2jh-context-demo`, and release name `context-demo` unless overridden by
environment variables.

## Setup

```bash
bash scripts/setup.sh
```

The setup script creates `.venv`, upgrades `pip`, and installs pinned
dependencies from `requirements-dev.txt`, which includes runtime dependencies
from `requirements.txt`.

## Verification

```bash
bash scripts/check.sh
```

This runs unit tests, syntax checks, Helm template rendering when `helm` is on
`PATH`, and Kubernetes client dry-run validation when `kubectl` is on `PATH`.
It does not mutate the cluster unless `RUN_CLUSTER_CHECKS=1` is set, and even
then it only runs read-only cluster inspection.

Record the local tool and cluster capability report:

```bash
bash scripts/environment-report.sh --out results/environment-capability.json --overwrite
git rev-parse HEAD
```

The raw experiment records also contain the `git_commit` used when the preserved
synthetic results were generated.

## Local Cluster

For the UI and Helm demo path, start or select a disposable local cluster first:

```bash
kubectl config current-context
bash scripts/check-cluster.sh
```

Install the baseline JupyterHub profile chooser:

```bash
bash scripts/install-baseline.sh
bash scripts/port-forward.sh
```

Open `http://127.0.0.1:8000`, enter any username and any non-empty password,
and choose Small, Medium, or Large. `DummyAuthenticator` is intentionally
insecure; keep this demo on an isolated local cluster and use local port
forwarding only.

Install the proposed intent/context-aware form:

```bash
bash scripts/install-proposed.sh
bash scripts/port-forward.sh
```

Example proposed-form input:

```text
Intent: I will train a scikit-learn model on a 1.5GB CSV dataset
Dataset size: 1.5
Code context:
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
df = pd.read_csv("data.csv")
model.fit(X, y)
```

Inspect the spawned pod metadata:

```bash
kubectl describe pod -n z2jh-context-demo -l component=singleuser-server
```

## Smoke Test

The smoke path uses synthetic local workload execution and does not require a
cluster:

```bash
.venv/bin/python -m experiments.runner --smoke --environment-id local-smoke --timeout 60
```

It creates a new ignored directory under `experiments/raw/` and appends one
schema-validated JSONL record.

## Benchmark

Preview the planned matrix without running workloads:

```bash
.venv/bin/python -m experiments.runner --full-matrix --repeats 5 --seed 20260719 --dry-run --environment-id local-dry-run
```

Run the full local synthetic matrix:

```bash
.venv/bin/python -m experiments.runner --full-matrix --repeats 5 --seed 20260719 --timeout 120 --environment-id local-benchmark
```

Resume an interrupted run:

```bash
.venv/bin/python -m experiments.runner --resume --experiment-dir experiments/raw/<experiment-id> --environment-id local-benchmark
```

Aggregate a raw run to a simple CSV:

```bash
.venv/bin/python -m experiments.runner --aggregate --experiment-dir experiments/raw/<experiment-id> --overwrite
```

## Analysis Reproduction

Regenerate the committed result tables and SVG figures from the preserved raw
snapshot:

```bash
.venv/bin/python -m experiments.analyze_results \
  --experiment-dir experiments/raw/20260719T140431Z-matrix-aed48949 \
  --results-dir results \
  --results-md docs/evaluation/RESULTS.md \
  --environment-report results/environment-capability.json \
  --overwrite
```

For validation without modifying tracked files, write to `/tmp` as shown in the
quick start.

## Demo Workloads

```bash
bash scripts/demo-underprovisioning.sh
kubectl describe pod underprovision-small -n z2jh-context-demo

bash scripts/demo-overprovisioning.sh
kubectl describe pod idle-large-1 -n z2jh-context-demo

bash scripts/demo-defensive-overrequesting.sh
kubectl describe pod defensive-large-light -n z2jh-context-demo
```

If metrics-server is absent, use `kubectl describe` requests/limits and events
instead of `kubectl top`.

## Cleanup

Remove demo Kubernetes resources:

```bash
bash scripts/uninstall.sh
```

Remove generated local artifact outputs:

```bash
rm -rf .venv .pytest_cache
find . -type d -name __pycache__ -prune -exec rm -rf {} +
find experiments/raw -mindepth 1 -maxdepth 1 \
  ! -name README.md \
  ! -name 20260719T140417Z-smoke-171688c0 \
  ! -name 20260719T140423Z-matrix-783b4141 \
  ! -name 20260719T140431Z-matrix-aed48949 \
  -exec rm -rf {} +
```

## Expected Directory Structure

```text
benchmarks/                  Synthetic workload runner and manifest
docs/                        Artifact, governance, and evaluation documents
docs/evaluation/             Protocol, results, schema, threats, design notes
experiments/                 Runner, recorder, schema, analysis code
experiments/raw/             Preserved sanitized raw snapshots plus ignored new runs
experiments/summaries/       Ignored generated aggregate CSVs
helm/                        Baseline and proposed JupyterHub Helm values
k8s/                         Demo Kubernetes manifests
recommender/                 Rule-based recommendation prototype and tests
results/                     Derived tables and SVG figures
scripts/                     Setup, verification, cluster, demo, and cleanup scripts
tests/                       Unit tests and sanitized Kubernetes evidence fixtures
workload/                    Original demo workload scripts
```

## Raw Versus Derived Data Policy

Raw records live under `experiments/raw/` as JSONL, environment JSON, matrix
JSONL, and stdout/stderr artifacts. They are append-only evidence and should not
be edited except for documented sanitization before publication.

Derived outputs live under `results/`, `experiments/summaries/`, and
`docs/evaluation/RESULTS.md`. They can be regenerated from raw records with
`experiments.analyze_results` and should not replace the raw evidence.

Raw notebook contents, datasets, secrets, usernames, and broad Kubernetes
metadata are not collected or stored. See `docs/DATA_GOVERNANCE.md`.

## Artifact Manifest

See `docs/ARTIFACT_MANIFEST.md` for the source files, preserved raw snapshots,
derived outputs, validation commands, and known generated files.

## Known Limitations

- The preserved benchmark run is local and synthetic, not a live multi-user
  Kubernetes workload.
- Kubernetes Metrics API was unavailable in the captured environment, so the
  committed analysis does not claim live cluster peak CPU/memory measurements.
- GPU behavior is represented as a recommendation and policy signal only; no
  GPU workload is executed.
- The prototype is rule-based and does not implement history-aware evaluation.
- Thresholds are fixed for the artifact, but conclusions remain sensitive to
  workload mix and threshold choices.

See `docs/evaluation/THREATS_TO_VALIDITY.md` for the full validity discussion.

## Troubleshooting

- `ModuleNotFoundError: yaml`: run `bash scripts/setup.sh` and use
  `.venv/bin/python` or activate `.venv`.
- `helm template` fails: check network access to `https://hub.jupyter.org` and
  verify `helm version`.
- `kubectl` dry-run fails: check `kubectl config current-context`; the client
  can validate manifests without a live cluster when `--validate=false` is used.
- `kubectl top` fails with `Metrics API not available`: install metrics-server
  before making live resource-usage claims, or report the metric as unavailable.
- JupyterHub install fails: run `bash scripts/uninstall.sh`, confirm the
  namespace is disposable, then retry the baseline or proposed install.
- Analysis refuses to overwrite files: pass `--overwrite` or choose a new
  `--results-dir`.

## Additional Documents

- `DEMO_SCRIPT.md`: narrative demo flow.
- `CLEANUP.md`: original demo cleanup notes.
- `docs/evaluation/EXPERIMENT_PROTOCOL.md`: experimental procedure.
- `docs/evaluation/RESULT_SCHEMA.md`: raw record schema.
- `docs/evaluation/RESULTS.md`: current derived analysis report.
- `docs/evaluation/FINAL_AUDIT.md`: historical audit of the stale Chat 8 branch;
  see its status notice before interpreting its findings.
