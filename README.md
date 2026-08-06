# Intent Spawner Research Artifact

This repository contains the graduation-thesis prototype:

> **Intent- and Context-Aware Profile Recommendation for Zero to JupyterHub
> and KubeSpawner**

The prototype asks a user what they intend to do, optionally accepts a dataset
size and lightweight code context, and previews an explainable Kubernetes
resource profile plus an administrator-allowlisted notebook image. The user
must confirm or manually override the recommendation before JupyterHub starts
the server.

The repository is both a runnable demonstration and a research artifact. It
contains three separate execution paths:

1. an interactive Zero to JupyterHub demonstration;
2. a portable local synthetic benchmark; and
3. a preserved Kubernetes-backed evaluation corpus.

The repository also contains a preregistered, not-yet-executed v3
resource-envelope protocol. V3 adds bounded memory pressure,
calibration/hold-out isolation, and a separate JupyterHub fidelity path without
changing the preserved v2 corpus. See
[Resource-Envelope Protocol v3](docs/evaluation/RESOURCE_ENVELOPE_PROTOCOL_V3.md).
The independent implementation audit is documented in
[Protocol-v3 Implementation Audit](docs/evaluation/RESOURCE_ENVELOPE_V3_IMPLEMENTATION_AUDIT.md).
As of 2026-07-23, this revision freezes the reviewed protocol-v3 source, but
immutable registry images are still missing, so no real v3 cluster experiment
has run.

These paths answer different questions and their results must not be treated as
interchangeable.

## Start Here

If this is your first time in the repository:

1. Read [Getting Started](docs/GETTING_STARTED.md) for setup and step-by-step
   commands.
2. Read [Architecture](docs/ARCHITECTURE.md) to understand the components and
   data flows.
3. Use [Demo Script](DEMO_SCRIPT.md) when presenting the live JupyterHub demo.
4. Use [Cleanup](CLEANUP.md) after running anything on Kubernetes.

The safest first run does not create Kubernetes resources:

```bash
git clone https://github.com/mthang1201/intent-spawner.git
cd intent-spawner
bash scripts/setup.sh
bash scripts/check.sh
.venv/bin/python -m experiments.runner \
  --smoke \
  --environment-id local-smoke \
  --timeout 60
```

The smoke command writes a new ignored experiment directory under
`experiments/raw/`. It does not create Kubernetes resources. If kubectl is
installed, `scripts/check.sh` also performs API discovery for its manifest
dry-run; use [Getting Started](docs/GETTING_STARTED.md#common-verification-problems)
if the configured API server is offline.

## The Problem

A conventional JupyterHub deployment often presents profiles such as Small,
Medium, and Large. Users usually understand their task—exploring a CSV,
training a model, or running basic Python—but may not know how much CPU and
memory that task needs.

That mismatch can produce:

- **underprovisioning**, where a workload fails after the user has already
  started working;
- **overprovisioning**, where idle sessions reserve more schedulable capacity
  than they need; and
- **defensive over-requesting**, where users choose Large to avoid an uncertain
  failure.

The proposed method moves the sizing decision into a pre-spawn recommendation
layer. Its inputs are:

- natural-language intent;
- an estimated dataset size in GB; and
- optional imports or code-context hints.

The output is a profile, an immutable notebook image selected from the admin
catalog, and human-readable reasons. The user can Confirm, Edit, or Manual
Override before KubeSpawner applies the confirmed decision. Accept/override
actions are recorded as privacy-minimized structured audit events.

See [Resource-and-Image Recommendation Preview Design](docs/evaluation/RECOMMENDATION_PREVIEW_DESIGN.md)
for the mapping, state machine, audit schema, scalability assessment, and
production suitability limits.

## Three Evidence Paths

| Path | Purpose | Requires Kubernetes | Output and claim boundary |
| --- | --- | --- | --- |
| Helm demo | Show the baseline and proposed user experience | Yes, disposable local cluster | Demonstrates mechanics and observable Kubernetes requests; it is not a production study |
| Local synthetic benchmark | Compare `static_manual`, `intent_only`, and `context_aware` deterministically | No | Produces local synthetic records and process measurements; it does not prove cluster behavior |
| Kubernetes evaluation | Evaluate `static_default`, `intent_only`, and `context_aware` with pod evidence | Yes for new runs; no for validating preserved results | Preserved single-node Minikube evidence; it does not establish production or multi-user performance |

The committed local and Kubernetes results are different evidence classes.
Never use a local process measurement as a Kubernetes scheduling or utilization
measurement.

## Quick Paths

### Run without Kubernetes

Set up the Python environment and validate the repository:

```bash
bash scripts/setup.sh
bash scripts/check.sh
```

The setup and local benchmark do not need Kubernetes. When kubectl is present,
the repository check expects its configured API server to be reachable for
manifest discovery; all non-kubectl checks and the benchmark remain local.

Preview the complete local experiment matrix without executing workloads:

```bash
.venv/bin/python -m experiments.runner \
  --full-matrix \
  --repeats 5 \
  --seed 20260719 \
  --dry-run \
  --environment-id local-dry-run
```

Validate and preview every v3 matrix without allocating pressure memory or
accessing Kubernetes:

```bash
make v3-dry-run
```

See [Local Synthetic Benchmark](docs/GETTING_STARTED.md#path-a-local-synthetic-benchmark)
for smoke, full-matrix, resume, aggregation, and analysis commands.

### Run the interactive JupyterHub demo

> **Safety:** use only a disposable local Kubernetes cluster. The install and
> demo scripts create or update resources.

```bash
kubectl config current-context
bash scripts/check-cluster.sh
bash scripts/install-baseline.sh
bash scripts/port-forward.sh
```

Open <http://127.0.0.1:8000>. The demo uses `DummyAuthenticator`: enter any
username and any non-empty password. It is intentionally insecure and must not
be exposed publicly.

Continue with the
[Interactive JupyterHub Demo](docs/GETTING_STARTED.md#path-b-interactive-jupyterhub-demo)
or follow the presentation-oriented [Demo Script](DEMO_SCRIPT.md).

### Validate preserved Kubernetes evidence

These commands inspect committed evidence and do not create pods:

```bash
make validate-cluster-results
make validate-raw-integrity
```

See
[Preserved Kubernetes Evaluation](docs/GETTING_STARTED.md#path-c-preserved-kubernetes-evaluation)
before regenerating derived results or starting a new experiment.

## Prerequisites

### Required for local setup

- Python 3.11 or newer;
- Bash;
- Git; and
- `pip` and Python `venv` support.

The artifact was most recently validated locally with Python 3.14.5. Setup
installs pinned dependencies from `requirements-dev.txt`.

### Additional tools for the Helm demo

- `kubectl`;
- Helm;
- a disposable local Kubernetes cluster, such as Minikube, kind, k3d, or
  OrbStack; and
- network access to pull the JupyterHub chart and container images.

Metrics Server is optional for the demo. If it is unavailable, resource
requests, limits, pod status, and events remain observable, but live usage
claims must be reported as unavailable.

### Additional tools for a new Kubernetes evaluation

- Minikube;
- Docker;
- the exact environment and protocol controls described in
  [Kubernetes Cluster Experiment Protocol](docs/evaluation/CLUSTER_EXPERIMENT_PROTOCOL.md).

A new evaluation is an advanced, cluster-mutating workflow. Validating the
preserved evidence does not require recreating its cluster.

## Repository Defaults

| Setting | Default |
| --- | --- |
| Kubernetes namespace | `z2jh-context-demo` |
| Helm release | `context-demo` |
| JupyterHub chart version | `4.0.0` |
| Local JupyterHub URL | `http://127.0.0.1:8000` |
| Python environment | `.venv` |
| Baseline Helm values | `helm/baseline-values.yaml` |
| Proposed Helm values | `helm/proposed-values.yaml` |

The shell scripts allow selected overrides through environment variables such
as `NAMESPACE`, `RELEASE`, `Z2JH_CHART_VERSION`, `LOCAL_PORT`, and `PYTHON`.
Keep the documented defaults when reproducing the artifact unless the changed
environment is explicitly recorded.

## Repository Map

| Path | Responsibility |
| --- | --- |
| `recommender/` | Standalone explainable rule-based recommender and unit tests |
| `helm/` | Baseline static-profile and proposed context-aware JupyterHub values |
| `scripts/` | Setup, validation, installation, observation, demo, and cleanup entry points |
| `workload/` | Small bounded workloads mounted into demo pods and JupyterLab |
| `benchmarks/` | Synthetic workload manifest and portable workload runner |
| `experiments/` | Local matrix orchestration, recording, schema, export, and analysis |
| `cluster_evaluation/` | Kubernetes pod runners, policies, evidence collection, validation, and analysis |
| `k8s/` | Standalone manifests used for request and quota demonstrations |
| `results/` | Derived local tables/figures and preserved cluster evidence |
| `docs/evaluation/` | Protocols, result interpretation, audit records, and validity limits |
| `tests/` | Unit tests and sanitized Kubernetes evidence fixtures |

See [Architecture](docs/ARCHITECTURE.md) for component-level behavior and data
flow.

## Verification

Run:

```bash
bash scripts/check.sh
```

The script performs:

- unit and smoke tests;
- preserved cluster artifact validation;
- raw SHA-256 integrity validation;
- a capacity-runner dry run;
- Python and shell syntax checks;
- Helm template rendering when Helm is available; and
- Kubernetes client dry-run validation when kubectl is available.

It skips read-only live cluster inspection unless `RUN_CLUSTER_CHECKS=1` is
set. It never runs cluster-mutating demo or experiment scripts.

To capture a sanitized environment capability report without overwriting the
committed report:

```bash
bash scripts/environment-report.sh \
  --out /tmp/intent-spawner-environment.json \
  --overwrite
```

## Research Scope and Limitations

- The recommender is rule-based; it does not use an LLM.
- No real GPU workload or GPU scheduling policy is evaluated.
- The local benchmark uses generated data and standard-library workload
  approximations.
- The preserved Kubernetes corpus comes from controlled, single-node Minikube
  experiments, not a live multi-user JupyterHub deployment.
- The Helm demo and Kubernetes evaluation are separate execution paths.
- The cluster corpus observed no OOM in the comparative matrix, so it does not
  estimate an OOM-reduction rate.
- Cgroup-v2 memory peaks are valid pod-boundary observations. Historical CPU
  values have average or hybrid sampled-maximum semantics and are not
  continuous peaks.
- History-aware provisioning remains future work.

Read [Threats to Validity](docs/evaluation/THREATS_TO_VALIDITY.md) and
[Final Audit](docs/evaluation/FINAL_AUDIT.md) before presenting quantitative
claims.

## Data Safety

Raw records are append-only evidence. Do not store or commit:

- notebook contents or raw code;
- datasets;
- secrets or credentials;
- usernames or longitudinal user identifiers; or
- broad unsanitized Kubernetes metadata.

Store only permitted derived context features, allowlisted pod metadata,
resource evidence, and aggregate results. See
[Data Governance](docs/DATA_GOVERNANCE.md),
[Local Experiment Data Guide](experiments/README.md), and
[Derived Experiment Summaries](experiments/summaries/README.md).

## Cleanup

After a Kubernetes demo:

```bash
bash scripts/uninstall.sh
```

This deletes the configured demo namespace and does not delete the cluster or
other namespaces. Read [Cleanup](CLEANUP.md) before removing local experiment
outputs or working with preserved evidence.

## Documentation Index

- [Getting Started](docs/GETTING_STARTED.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Demo Script](DEMO_SCRIPT.md)
- [Cleanup](CLEANUP.md)
- [Local Experiment Data Guide](experiments/README.md)
- [Artifact Manifest](docs/ARTIFACT_MANIFEST.md)
- [Data Governance](docs/DATA_GOVERNANCE.md)
- [Experiment Protocol](docs/evaluation/EXPERIMENT_PROTOCOL.md)
- [Recommendation Preview Design](docs/evaluation/RECOMMENDATION_PREVIEW_DESIGN.md)
- [Kubernetes Cluster Experiment Protocol](docs/evaluation/CLUSTER_EXPERIMENT_PROTOCOL.md)
- [Result Schema](docs/evaluation/RESULT_SCHEMA.md)
- [Local Results](docs/evaluation/RESULTS.md)
- [Kubernetes Results](docs/evaluation/CLUSTER_RESULTS.md)
- [Threats to Validity](docs/evaluation/THREATS_TO_VALIDITY.md)
- [Final Audit](docs/evaluation/FINAL_AUDIT.md)
