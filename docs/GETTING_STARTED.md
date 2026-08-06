# Getting Started

This guide takes a newcomer from a fresh clone to each supported repository
workflow. It assumes basic command-line and Python familiarity but does not
assume prior JupyterHub, Helm, or Kubernetes experience.

Use the workflow that matches your goal:

| Goal | Recommended path | Cluster required |
| --- | --- | --- |
| Check Python, source, and preserved evidence | Setup and verification | No |
| Explore recommendation and benchmark behavior | Path A: local synthetic benchmark | No |
| Demonstrate the JupyterHub user experience | Path B: interactive JupyterHub demo | Yes |
| Change a running notebook's workload | Path B10: intent-aware re-provisioning | Yes |
| Audit or regenerate committed research outputs | Path C: preserved Kubernetes evaluation | No for validation |
| Produce new Kubernetes evidence | Advanced protocol only | Yes, disposable Minikube |

If kubectl is installed, the full `scripts/check.sh` command performs API
discovery for its manifest dry-run. An offline configured API server affects
that one check, not the local benchmark path.

Return to the [main README](../README.md), read the
[architecture guide](ARCHITECTURE.md), or use the
[presentation runbook](../DEMO_SCRIPT.md) as needed.

The stop/start design and persistence boundary for changing an already-running
notebook are documented in
[Intent-aware Re-provisioning](INTENT_AWARE_REPROVISIONING.md).

## 1. Clone and Inspect the Repository

```bash
git clone https://github.com/mthang1201/intent-spawner.git
cd intent-spawner
git status --short
```

Expected result:

- the current directory is the repository root;
- `git status --short` prints nothing for a clean clone; and
- files such as `README.md`, `Makefile`, `requirements-dev.txt`, and
  `scripts/setup.sh` exist.

If the working tree is already modified, do not delete or overwrite changes you
do not recognize.

## 2. Check Local Prerequisites

Required for all non-cluster workflows:

```bash
python3 --version
git --version
bash --version
```

Python must be version 3.11 or newer.

Optional tools for the interactive demo:

```bash
kubectl version --client
helm version
kubectl config current-context
```

If `kubectl config current-context` returns an organization, shared, staging,
or production cluster, do not run any install or demo script. Select or create
a disposable local cluster first.

## 3. Create the Python Environment

Run:

```bash
bash scripts/setup.sh
```

What it does:

1. creates `.venv` if it does not exist;
2. upgrades `pip` inside that environment; and
3. installs pinned packages from `requirements-dev.txt`, including runtime
   requirements.

Expected result:

```text
Setup complete.
```

Use the environment explicitly:

```bash
.venv/bin/python --version
```

Alternatively, activate it for the current shell:

```bash
source .venv/bin/activate
```

If setup reports that `venv` is unavailable, install the Python venv package
provided by your operating system and rerun the script.

## 4. Verify the Repository

```bash
bash scripts/check.sh
```

The command validates:

- unit and smoke tests;
- preserved cluster artifacts and raw checksums;
- capacity-runner planning;
- Python and shell syntax;
- Helm templates when Helm is installed; and
- Kubernetes manifests through client-side dry-run when kubectl is installed.

The final line should report zero failed checks. Optional tools may produce
`SKIP` entries.

This command does not install JupyterHub, create demo pods, apply quotas, or run
the Kubernetes experiment. Read-only live cluster inspection is also skipped
unless you explicitly set `RUN_CLUSTER_CHECKS=1`.

### Common verification problems

- **`ModuleNotFoundError: yaml`:** run `bash scripts/setup.sh` and use
  `.venv/bin/python`.
- **Helm rendering fails:** confirm `helm version` works and that the machine
  can reach the JupyterHub chart repository.
- **kubectl client dry-run fails:** confirm `kubectl version --client` works and
  the configured API server is reachable. Although the repository passes
  `--dry-run=client` and `--validate=false`, kubectl still performs API
  discovery for these manifests.
- **Preserved artifact or checksum validation fails:** run
  `git status --short`. Do not repair raw evidence manually. Restore the exact
  tracked files from version control or investigate the unexpected
  modification.

## Path A: Local Synthetic Benchmark

This is the default safe path. It runs generated workloads as local Python
processes and does not interact with Kubernetes.

### A1. Inspect one recommendation

```bash
.venv/bin/python -m recommender.recommender \
  --intent "Train a scikit-learn model" \
  --dataset-gb 1.5 \
  --code-context "import pandas as pd; model.fit(X, y)"
```

Expected result:

- JSON is printed to stdout;
- the profile is `large`; and
- the image is `scipy-data-science`; and
- the resource and image reasons identify dataset, data-processing, training,
  and catalog capability signals.

This command exercises the standalone recommender. The proposed Helm values
contain a mirrored implementation for the live pre-spawn demo.

### A2. Run a smoke experiment

```bash
.venv/bin/python -m experiments.runner \
  --smoke \
  --environment-id local-smoke \
  --timeout 60
```

Purpose:

- execute one bounded synthetic workload;
- create a schema-validated raw record; and
- verify the recording path without Kubernetes.

Expected result:

- the command prints the new experiment directory;
- the directory appears under `experiments/raw/`; and
- it contains a matrix/environment description, `results.jsonl`, and
  supporting workload output.

New experiment directories are ignored by default. Read
[Local Experiment Data Guide](../experiments/README.md) before committing any
generated record.

### A3. Preview the full matrix

```bash
.venv/bin/python -m experiments.runner \
  --full-matrix \
  --repeats 5 \
  --seed 20260719 \
  --dry-run \
  --environment-id local-dry-run
```

Purpose:

- resolve workloads, methods, repeats, and deterministic seeds;
- write the planned matrix; and
- avoid executing workload commands.

Expected result:

- a new raw experiment directory is created;
- `matrix.jsonl` contains the planned combinations; and
- no benchmark workload result is appended.

### A4. Run the full local matrix

```bash
.venv/bin/python -m experiments.runner \
  --full-matrix \
  --repeats 5 \
  --seed 20260719 \
  --timeout 120 \
  --environment-id local-benchmark
```

The methods are:

- `static_manual`: deterministic static/manual comparator;
- `intent_only`: intent text without dataset or code context; and
- `context_aware`: intent plus dataset-size and code-context hints.

This can take several minutes. Every attempted workload writes supporting
stdout/stderr before its normalized JSONL record is appended.

Do not call these results Kubernetes measurements. Runtime and memory fields
come from the local process path.

### A5. Run a smaller selection

One method across selected workloads:

```bash
.venv/bin/python -m experiments.runner \
  --method intent_only \
  --repeats 5 \
  --seed 20260719 \
  --environment-id local-benchmark
```

One workload across all methods:

```bash
.venv/bin/python -m experiments.runner \
  --workload-id ml_sklearn_fit_medium \
  --repeats 5 \
  --seed 20260719 \
  --environment-id local-benchmark
```

### A6. Resume an interrupted run

First identify the experiment directory printed by the original command. Then:

```bash
.venv/bin/python -m experiments.runner \
  --resume \
  --experiment-dir experiments/raw/<experiment-id> \
  --environment-id local-benchmark
```

Resume skips combinations already present in `results.jsonl`. It does not
rewrite completed records. Use the same environment identity and do not edit
the matrix between attempts.

### A7. Export a simple CSV summary

```bash
.venv/bin/python -m experiments.runner \
  --aggregate \
  --experiment-dir experiments/raw/<experiment-id> \
  --csv-out experiments/summaries/<experiment-id>.csv
```

The CSV is derived output. The exporter refuses to overwrite an existing file
unless `--overwrite` is supplied.

### A8. Reproduce the committed local analysis safely

Write regenerated outputs to `/tmp` so tracked results are not changed:

```bash
.venv/bin/python -m experiments.analyze_results \
  --experiment-dir experiments/raw/20260719T140431Z-matrix-aed48949 \
  --results-dir /tmp/intent-spawner-results \
  --results-md /tmp/intent-spawner-results/RESULTS.md \
  --environment-report results/environment-capability.json \
  --overwrite
```

Expected result:

- CSV tables and SVG figures are written under
  `/tmp/intent-spawner-results`;
- a regenerated Markdown report is written beside them; and
- the preserved raw snapshot is unchanged.

Compare the regenerated output with `results/` and
`docs/evaluation/RESULTS.md` only after accounting for paths or environment
metadata recorded in the report.

## Path B: Interactive JupyterHub Demo

> **Cluster mutation warning:** every install and demo script in this section
> creates, updates, or deletes Kubernetes resources. Continue only on a
> disposable local cluster. The default namespace is
> `z2jh-context-demo`.

The live demo uses:

- Helm release `context-demo`;
- JupyterHub chart version `4.0.0`;
- a `ClusterIP` proxy accessed through local port forwarding;
- `DummyAuthenticator`; and
- no persistent user storage in the baseline, then a bounded per-user `1Gi`
  PVC in the proposed re-provisioning overlay.

### B1. Confirm the active cluster

```bash
kubectl config current-context
bash scripts/check-cluster.sh
```

The second command is read-only. It prints the active context, nodes, and
namespaces.

Stop if the context is not an isolated cluster you are authorized to mutate.

### B2. Use separate terminals

The demo is easiest to follow with:

- **Terminal 1:** install and observation commands;
- **Terminal 2:** `bash scripts/port-forward.sh`; and
- **Browser:** <http://127.0.0.1:8000>.

An optional third terminal can run:

```bash
bash scripts/watch-pods.sh
```

Press `Ctrl+C` to stop either long-running observation command.

### B3. Install the baseline

```bash
bash scripts/install-baseline.sh
```

The script:

1. creates or reuses `z2jh-context-demo`;
2. creates a ConfigMap from `workload/`;
3. updates the JupyterHub Helm repository; and
4. installs or upgrades release `context-demo` with
   `helm/baseline-values.yaml`.

Expected result:

```text
Baseline installed.
```

If the install times out, inspect:

```bash
kubectl get pods -n z2jh-context-demo
kubectl get events -n z2jh-context-demo --sort-by=.lastTimestamp
```

Common causes are image-pull failures, insufficient local-cluster memory, and
network access to the chart or image registry.

### B4. Open JupyterHub

In Terminal 2:

```bash
bash scripts/port-forward.sh
```

Open <http://127.0.0.1:8000>. Enter any username and any non-empty password.
The spawn page should offer Small, Medium, and Large profiles.

This authentication configuration is deliberately insecure. Keep the service
on local port forwarding and never expose it to an untrusted network.

### B5. Demonstrate underprovisioning

```bash
bash scripts/demo-underprovisioning.sh
kubectl get pods -n z2jh-context-demo -w
```

The bounded workload allocates memory in 32 MiB blocks toward 640 MiB while its
container limit is 384 MiB.

Verify the outcome:

```bash
kubectl describe pod underprovision-small \
  -n z2jh-context-demo |
  grep -A8 -E 'Last State|Reason|OOMKilled'

kubectl logs underprovision-small \
  -n z2jh-context-demo \
  --previous 2>/dev/null ||
  kubectl logs underprovision-small -n z2jh-context-demo
```

Expected result:

- logs show incremental `allocated_mib=...` messages; and
- container status reports `OOMKilled` or exit code 137.

This demonstrates a late-failure mechanism. It does not measure real notebook
state loss or user rerun behavior.

### B6. Demonstrate scheduling pressure

```bash
bash scripts/demo-overprovisioning.sh
kubectl get pods -n z2jh-context-demo -l demo=overprovisioning
```

The script calculates 55% of the first node's allocatable CPU and memory for
each idle pod, then attempts to create three pods.

Expected result:

- one or more pods remain Pending because their requests cannot all fit; or
- if the cluster admits all pods, the script applies a ResourceQuota fallback
  and attempts an additional blocked pod.

Inspect requests and events:

```bash
kubectl describe pod idle-large-1 \
  -n z2jh-context-demo |
  grep -A8 Requests

kubectl describe pod idle-large-2 \
  -n z2jh-context-demo |
  grep -A12 -E 'Events|Insufficient|quota|Requests'
```

If Metrics Server is available:

```bash
kubectl top pods -n z2jh-context-demo
```

If it is unavailable, report usage as unavailable. Requests, limits, pod
status, and scheduling events are still valid observations.

### B7. Demonstrate defensive over-requesting

```bash
bash scripts/demo-defensive-overrequesting.sh
kubectl logs defensive-large-light -n z2jh-context-demo
kubectl describe pod defensive-large-light \
  -n z2jh-context-demo |
  grep -A8 Requests
```

Expected result:

- the light workload completes quickly;
- the pod remains available briefly for observation; and
- it requests the Large profile's CPU and memory.

This is an illustrative mechanism, not evidence of how often real users choose
Large or why they do so.

### B8. Switch to the proposed method

The proposed install upgrades the same Helm release:

```bash
bash scripts/install-proposed.sh
```

If the port-forward disconnected during the upgrade, restart it:

```bash
bash scripts/port-forward.sh
```

Open JupyterHub and enter:

```text
Intent: I will train a scikit-learn model on a 1.5GB CSV dataset
Dataset size: 1.5
Code context:
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
df = pd.read_csv("data.csv")
model.fit(X, y)
```

Expected result:

- the form asks about the task rather than asking the user to choose hardware;
- **Preview recommendation** shows `large`, `scipy-data-science`, and their
  explanations without creating a pod;
- **Edit inputs** invalidates the preview and requires another preview;
- **Manual Override** permits only the three resource profiles and catalog
  image IDs; and
- **Confirm recommendation** applies Large resources and the digest-pinned
  SciPy image before the user pod is created.

### B9. Verify the applied recommendation

```bash
kubectl get pods -n z2jh-context-demo \
  -l component=singleuser-server

kubectl describe pod -n z2jh-context-demo \
  -l component=singleuser-server |
  grep -A18 -E \
  'recommendation-action|recommended-profile|applied-profile|recommended-image|applied-image|Environment|RECOMMENDED_PROFILE|APPLIED_NOTEBOOK_IMAGE|Image:|Requests|Limits'

kubectl logs -n z2jh-context-demo \
  -l component=hub --tail=200 |
  grep 'recommendation_audit='
```

The pod should expose:

- `RECOMMENDED_PROFILE=large`;
- `APPLIED_NOTEBOOK_IMAGE=scipy-data-science`;
- `RECOMMENDATION_ACTION=accept`;
- human-readable `RECOMMENDATION_REASONS`;
- recommended/applied profile and image annotations;
- one structured Hub audit event; and
- Large CPU and memory requests/limits.

Inside a JupyterLab terminal, run:

```bash
python /home/jovyan/demo/workload/train_like_workload.py
```

Expected result:

```text
Training-like workload finished without OOM.
```

The script is bounded and intended only for the demonstration.

For a presentation-ready narrative, use [Demo Script](../DEMO_SCRIPT.md).

### B10. Change the running workload

First save a marker file inside a JupyterLab terminal:

```bash
printf 'persistent marker\n' > /home/jovyan/reprovision-marker.txt
```

Open <http://127.0.0.1:8000/hub/reprovision>. Describe a different workload,
select **Preview replacement**, and review the current/proposed profile and
image. The page explicitly warns that it cannot live-migrate the server and
cannot retain kernel variables, active computations, or terminal processes.

After saving all files, check the acknowledgement and select
**Stop old pod and create replacement**. In another terminal, observe the
ordered pod transition and stable claim:

```bash
kubectl get pods,pvc -n z2jh-context-demo -w
```

Expected result:

- the old single-user pod terminates before the replacement starts;
- the replacement uses the confirmed resource profile and image;
- the per-user PVC name remains unchanged; and
- `/home/jovyan/reprovision-marker.txt` exists in the replacement server.

Kernel variables and running processes from the old pod must be treated as
lost. If the replacement fails to schedule or pull its image, the server stays
stopped, but the PVC remains for an explicit retry. There is no automatic
rollback in this prototype. See
[Intent-aware Re-provisioning](INTENT_AWARE_REPROVISIONING.md) for the state
machine, concurrency rule, audit fields, and failure behavior.

### B11. Clean up

Stop port forwarding with `Ctrl+C`, then:

```bash
bash scripts/uninstall.sh
kubectl get namespace z2jh-context-demo
```

The final command should report that the namespace is not found after deletion
finishes. See [Cleanup](../CLEANUP.md) for exact scope and local-file cleanup.

## Path C: Preserved Kubernetes Evaluation

The committed Kubernetes corpus is already present under
`results/cluster/raw/`. You can validate and analyze it without creating a
cluster.

### C1. Validate structural and cross-file integrity

```bash
make validate-cluster-results
```

This reconciles plans, normalized records, pod evidence, supporting paths,
profile application, and expected corpus structure.

### C2. Validate raw file checksums

```bash
make validate-raw-integrity
```

This compares tracked raw files with
`docs/evaluation/RAW_EVIDENCE_SHA256SUMS.txt`.

Any failure must be investigated. Do not edit raw records to make validation
pass.

### C3. Inspect the reported interpretation

Read:

- [Kubernetes Results](evaluation/CLUSTER_RESULTS.md);
- [Cluster Provenance](evaluation/CLUSTER_PROVENANCE.md);
- [Audit Blocker Resolution](evaluation/AUDIT_BLOCKER_RESOLUTION.md);
- [Threats to Validity](evaluation/THREATS_TO_VALIDITY.md); and
- [Final Audit](evaluation/FINAL_AUDIT.md).

The principal capacity-v2 corpus and historical capacity corpus have different
claim boundaries. The historical generator was not committed at the evaluated
revision, so those historical capacity observations are supplementary.

### C4. Regenerate tracked derived outputs

> This step rewrites tracked derived tables, figures, envelopes, and the
> Kubernetes results report. Run it only in a clean worktree when you intend to
> compare regenerated files.

```bash
git status --short
make regenerate-cluster-results
git diff -- \
  results/cluster/derived \
  benchmarks/observed_resource_envelopes.yaml \
  docs/evaluation/CLUSTER_RESULTS.md
```

An empty diff means the committed derived outputs reproduce exactly in the
current environment.

### C5. Preview capacity-runner planning

```bash
make capacity-dry-run
```

This prints the planned capacity-v2 batches and does not create pods.

## Advanced: Producing New Kubernetes Evidence

Do not treat this as a continuation of the Helm demo. The evaluation uses
dedicated pod runners, strict environment checks, immutable evidence
directories, and controlled Minikube profiles.

Before any new run:

1. read the complete
   [Kubernetes Cluster Experiment Protocol](evaluation/CLUSTER_EXPERIMENT_PROTOCOL.md);
2. use a clean Git commit;
3. create only the named disposable Minikube profile from the protocol;
4. verify the required current context and namespace safety controls;
5. build and load the exact evaluation image;
6. preregister identifiers, repeats, seed, timeout, and capacity controls; and
7. preserve all raw outputs before producing summaries.

The ground-truth/comparative runner and capacity-v2 runner deliberately refuse
unexpected contexts or unsafe environment shapes. Do not weaken those checks.

## Environment Report

Capture local tool and cluster capability metadata:

```bash
bash scripts/environment-report.sh \
  --out /tmp/intent-spawner-environment.json \
  --overwrite
```

This report is sanitized. It records capabilities and versions needed to
interpret missing or available measurement paths without collecting notebook
contents, datasets, secrets, or usernames.

## Next Reading

- [Architecture](ARCHITECTURE.md)
- [Demo Script](../DEMO_SCRIPT.md)
- [Cleanup](../CLEANUP.md)
- [Local Experiment Data Guide](../experiments/README.md)
- [Artifact Manifest](ARTIFACT_MANIFEST.md)
- [Data Governance](DATA_GOVERNANCE.md)
- [Experiment Protocol](evaluation/EXPERIMENT_PROTOCOL.md)
- [Result Schema](evaluation/RESULT_SCHEMA.md)
