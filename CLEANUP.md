# Cleanup

This guide separates Kubernetes cleanup from local generated-file cleanup.
Run commands from the repository root and resolve exact targets before deleting
anything.

Return to the [main README](README.md), [Getting Started](docs/GETTING_STARTED.md),
or [Demo Script](DEMO_SCRIPT.md).

## Kubernetes Demo Cleanup

The default demo namespace is:

```text
z2jh-context-demo
```

It contains the Helm release, JupyterHub components, user servers, workload
ConfigMap, standalone demo pods, and any demo ResourceQuota.

### 1. Stop terminal processes

Press `Ctrl+C` in terminals running:

- `bash scripts/port-forward.sh`;
- `bash scripts/watch-pods.sh`; or
- `kubectl get pods ... -w`.

Stopping these client processes does not delete cluster resources.

### 2. Confirm the active context and target

```bash
kubectl config current-context
kubectl get namespace z2jh-context-demo
```

Continue only when this is the disposable cluster and namespace you intended
to remove.

### 3. Delete the demo namespace

```bash
bash scripts/uninstall.sh
```

The script runs:

```bash
kubectl delete namespace z2jh-context-demo --ignore-not-found
```

If `NAMESPACE` was explicitly overridden during installation, use the same
exact value during cleanup:

```bash
NAMESPACE=<exact-demo-namespace> bash scripts/uninstall.sh
```

Do not guess the namespace name. Inspect it first.

### 4. Verify deletion

```bash
kubectl get namespace z2jh-context-demo
```

Expected result after deletion completes:

```text
Error from server (NotFound)
```

If the namespace remains in `Terminating`, inspect it without applying broad
finalizer-removal commands:

```bash
kubectl describe namespace z2jh-context-demo
kubectl get all,configmap,resourcequota \
  -n z2jh-context-demo
```

Do not remove namespace finalizers unless you understand the remaining
resources and have explicit authority to do so.

## What the Uninstall Script Deletes

Deleting the namespace removes namespaced demo resources, including:

- the `context-demo` Helm release state;
- Hub, proxy, user, and demonstration pods;
- per-user PersistentVolumeClaims created by the re-provisioning overlay
  (and, with the demo's dynamic provisioning/reclaim policy, their bound data);
- Services and other namespaced JupyterHub objects;
- the `demo-workload` ConfigMap;
- demo ResourceQuotas; and
- namespace-scoped events as they expire with the namespace.

## What the Uninstall Script Preserves

It does not delete or modify:

- the Kubernetes cluster;
- the current kubeconfig context;
- namespaces other than the exact configured namespace;
- cluster-wide resources;
- Helm repository configuration on the local machine;
- the local source repository;
- `.venv`;
- local experiment outputs; or
- preserved raw evidence.

Stopping or re-provisioning an individual notebook does not delete its PVC.
Deleting the whole demo namespace does. Copy any marker files or other demo data
you intend to keep before running `scripts/uninstall.sh`.

## Local Python Cleanup

The Python environment and caches are reproducible. From the repository root,
inspect them first:

```bash
pwd
find . -type d \
  \( -name __pycache__ -o -name .pytest_cache \) \
  -prune -print
```

Remove the virtual environment and pytest cache only when you intend to rebuild
them:

```bash
rm -rf .venv .pytest_cache
```

Remove Python bytecode caches under this repository:

```bash
find . -type d -name __pycache__ \
  -prune -exec rm -rf {} +
```

Recreate the environment with:

```bash
bash scripts/setup.sh
```

## Generated Local Experiment Cleanup

New smoke, dry-run, and matrix executions create ignored directories under
`experiments/raw/`. Derived CSV exports may appear under
`experiments/summaries/`.

Inspect ignored outputs:

```bash
git status --short --ignored \
  experiments/raw \
  experiments/summaries
```

Delete only an exact generated experiment directory whose identifier was
printed by your runner invocation:

```bash
rm -rf experiments/raw/<exact-generated-experiment-id>
```

Delete only an exact generated summary:

```bash
rm -f experiments/summaries/<exact-generated-experiment-id>.csv
```

Replace the placeholders with one verified identifier. Do not use a wildcard,
do not delete the entire `experiments/raw/` directory, and do not copy these
commands before resolving the target.

Temporary analysis output written by the onboarding guide can be removed by
targeting that exact directory:

```bash
rm -rf /tmp/intent-spawner-results
rm -f /tmp/intent-spawner-environment.json
```

## Preserved Evidence: Do Not Delete

The following committed local snapshots support the published results:

- `experiments/raw/20260719T140417Z-smoke-171688c0`;
- `experiments/raw/20260719T140423Z-matrix-783b4141`; and
- `experiments/raw/20260719T140431Z-matrix-aed48949`.

Preserved Kubernetes evidence lives under:

```text
results/cluster/raw/
```

Do not edit, move, rewrite, or delete these files as routine cleanup. Their
integrity is validated against:

```text
docs/evaluation/RAW_EVIDENCE_SHA256SUMS.txt
```

Validate them with:

```bash
make validate-cluster-results
make validate-raw-integrity
```

## Derived Result Cleanup

Files under `results/`, `results/cluster/derived/`, and generated reports are
derived, but many are intentionally tracked as part of the research artifact.
Do not delete them merely because they can be regenerated.

If you ran `make regenerate-cluster-results`, inspect changes with:

```bash
git status --short
git diff -- \
  results/cluster/derived \
  benchmarks/observed_resource_envelopes.yaml \
  docs/evaluation/CLUSTER_RESULTS.md
```

Preserve or revert those changes through normal version-control review. Do not
use destructive repository-wide reset commands.

## Separate Minikube Evaluation Profiles

The Helm demo namespace cleanup does not delete Minikube profiles used by an
advanced Kubernetes evaluation. Evaluation profiles have their own exact
cleanup procedures in
[Kubernetes Cluster Experiment Protocol](docs/evaluation/CLUSTER_EXPERIMENT_PROTOCOL.md).

Delete only the named disposable profile created for that experiment. Never
delete an existing default, shared, or unrelated profile.
