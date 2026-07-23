# Thesis Demo Script

This runbook is designed for a live presentation of the prototype. It provides
the commands to run, the observation to point out, and the claim boundary for
each scene.

For installation background, see [Getting Started](docs/GETTING_STARTED.md).
For component details, see [Architecture](docs/ARCHITECTURE.md). After the
presentation, follow [Cleanup](CLEANUP.md).

## Demo Story

The presentation follows this sequence:

1. show the baseline hardware-selection problem;
2. demonstrate underprovisioning;
3. demonstrate request-based scheduling pressure;
4. illustrate defensive over-requesting;
5. remove the temporary pressure resources;
6. switch to the proposed intent/context form;
7. verify that the recommendation is enforced; and
8. run a bounded workload successfully.

The demo establishes prototype mechanics. It does not reproduce the preserved
Kubernetes evaluation or prove production-wide improvements.

## Safety Gate

> **Do not run this demo on a shared, staging, or production cluster.**

The scripts create namespaces, ConfigMaps, pods, a Helm release, and possibly a
ResourceQuota. Use only an isolated local cluster that you are authorized to
delete.

Defaults:

| Setting | Value |
| --- | --- |
| Namespace | `z2jh-context-demo` |
| Helm release | `context-demo` |
| JupyterHub chart | `4.0.0` |
| Browser URL | `http://127.0.0.1:8000` |
| Authentication | Insecure local-only `DummyAuthenticator` |

The demo stores no persistent JupyterHub user volume.

## Before the Presentation

### 1. Verify local setup

```bash
bash scripts/setup.sh
bash scripts/check.sh
```

Expected result: the check summary reports zero failures. Optional Helm or
kubectl checks may be skipped only if those tools are not needed on the
machine. They are required for the live demo.

### 2. Verify the active cluster

```bash
kubectl config current-context
bash scripts/check-cluster.sh
```

Say:

> “The repository prints the current context before any demo mutation. This
> presentation runs only inside the isolated demo namespace on a disposable
> local cluster.”

Stop if the context is not the intended local cluster.

### 3. Prepare terminals

Use:

- **Terminal 1:** installs, demo commands, and inspection;
- **Terminal 2:** port forwarding;
- **Terminal 3:** optional pod watch; and
- **Browser:** JupyterHub.

In Terminal 3:

```bash
bash scripts/watch-pods.sh
```

Keep [Cleanup](CLEANUP.md) available in case the presentation is interrupted.

## Scene 1: Baseline Static Profile Selection

### Goal

Show that the baseline asks the user to choose hardware even though the user
usually understands the task better than Kubernetes resource quantities.

### Run

In Terminal 1:

```bash
bash scripts/install-baseline.sh
kubectl get pods -n z2jh-context-demo
```

Expected result:

- the Helm command completes with `Baseline installed.`;
- Hub and proxy pods become Running; and
- the `demo-workload` ConfigMap exists.

If the install times out:

```bash
kubectl get pods -n z2jh-context-demo
kubectl get events -n z2jh-context-demo --sort-by=.lastTimestamp
```

Look for image-pull failures, insufficient local-cluster resources, or network
errors.

### Open the UI

In Terminal 2:

```bash
bash scripts/port-forward.sh
```

Open <http://127.0.0.1:8000>. Enter any username and any non-empty password.

> `DummyAuthenticator` is intentionally insecure. Keep the endpoint on local
> port forwarding and never expose it publicly.

The spawn page should display:

- Small: low CPU/RAM;
- Medium: moderate CPU/RAM; and
- Large: high CPU/RAM.

### Say

> “The platform asks ‘Which hardware profile do you want?’ The user must turn a
> workload-level intention into CPU and memory sizing without direct evidence.”

### Claim boundary

This screen demonstrates the baseline interaction. It does not show how real
users choose profiles or how often they choose incorrectly.

## Scene 2: Underprovisioning and Late Failure

### Goal

Show a bounded workload that starts normally and then exceeds a Small memory
limit.

### Run

```bash
bash scripts/demo-underprovisioning.sh
kubectl get pods -n z2jh-context-demo -w
```

Stop the watch with `Ctrl+C` after `underprovision-small` fails.

### Verify

```bash
kubectl describe pod underprovision-small \
  -n z2jh-context-demo |
  grep -A8 -E 'Last State|Reason|OOMKilled'

kubectl logs underprovision-small \
  -n z2jh-context-demo \
  --previous 2>/dev/null ||
  kubectl logs underprovision-small -n z2jh-context-demo
```

Expected observation:

- the log shows `allocated_mib=32`, `allocated_mib=64`, and further gradual
  allocations;
- the container has a 384 MiB memory limit; and
- the final status reports `OOMKilled` or exit code 137.

### Say

> “The workload looked healthy while it allocated memory, but the resource
> decision made before spawn caused a late container failure.”

### Claim boundary

This demonstrates an OOM mechanism with a controlled workload. It does not
measure lost notebook state, user frustration, or time spent rerunning work.

## Scene 3: Overprovisioning and Scheduling Pressure

### Goal

Show that Kubernetes schedules against requests even when a container is idle.

### Run

```bash
bash scripts/demo-overprovisioning.sh
kubectl get pods -n z2jh-context-demo -l demo=overprovisioning
```

The script requests 55% of the first node's allocatable CPU and memory for each
of three idle pods.

### Verify

```bash
kubectl describe pod idle-large-1 \
  -n z2jh-context-demo |
  grep -A8 Requests

kubectl describe pod idle-large-2 \
  -n z2jh-context-demo |
  grep -A12 -E 'Events|Insufficient|quota|Requests'
```

Expected observation:

- one or more pods are Pending because all requested resources do not fit; or
- if the cluster admits every pod, the script applies
  `overprovisioning-request-quota` and attempts an additional pod that should
  be rejected or blocked.

If Metrics Server is available:

```bash
kubectl top pods -n z2jh-context-demo
```

If the command reports `Metrics API not available`, do not infer usage. Point
to pod requests, limits, scheduling status, and events instead.

### Say

> “The containers are deliberately idle, but their requests reduce the
> capacity Kubernetes can offer to another session.”

### Claim boundary

This demonstrates request-based scheduling pressure on the local demo cluster.
It does not measure production utilization or multi-user arrival behavior.

## Scene 4: Defensive Over-Requesting

### Goal

Illustrate the resource cost of assigning a light task to the Large profile.

### Run

```bash
bash scripts/demo-defensive-overrequesting.sh
kubectl logs defensive-large-light -n z2jh-context-demo
kubectl describe pod defensive-large-light \
  -n z2jh-context-demo |
  grep -A8 Requests
```

Expected observation:

- the workload reports `Light EDA workload complete.`;
- it processes only 10,000 generated values; and
- the pod still requests 1500m CPU and 1536Mi memory.

If Metrics Server is available:

```bash
kubectl top pod defensive-large-light -n z2jh-context-demo
```

### Say

> “A lightweight task can reserve the Large profile when the sizing decision is
> made defensively.”

### Claim boundary

The script shows the cost of this hypothetical assignment. It does not
establish that real users make this choice or identify their motivation.

## Scene 5: Remove Temporary Pressure Before Spawning

The scheduling-pressure scene may leave large Pending pods and a restrictive
ResourceQuota. Remove those temporary resources before demonstrating the
proposed user-server spawn:

```bash
kubectl delete pod -n z2jh-context-demo \
  -l demo=overprovisioning \
  --ignore-not-found

kubectl delete resourcequota overprovisioning-request-quota \
  -n z2jh-context-demo \
  --ignore-not-found

kubectl delete pod defensive-large-light \
  -n z2jh-context-demo \
  --ignore-not-found
```

Verify:

```bash
kubectl get resourcequota,pods -n z2jh-context-demo
```

The Hub and proxy should remain. No overprovisioning quota should be listed.

## Scene 6: Switch to the Proposed Method

### Goal

Replace direct hardware selection with inputs the user can describe naturally.

### Run

```bash
bash scripts/install-proposed.sh
```

This upgrades the same Helm release with `helm/proposed-values.yaml`.

If port forwarding stopped during the upgrade, restart Terminal 2:

```bash
bash scripts/port-forward.sh
```

If an old baseline user server is still running, open `/hub/home`, stop that
server, and start a new server so the proposed form and pre-spawn hook are used.

### Enter this example

```text
Intent:
I will train a scikit-learn model on a 1.5GB CSV dataset

Dataset size:
1.5

Code context:
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
df = pd.read_csv("data.csv")
model.fit(X, y)
```

Expected observation:

- the form asks what the user plans to do;
- it does not ask the user to choose Small, Medium, or Large; and
- the user server starts successfully.

### Say

> “The user supplies intent and lightweight context. The platform translates
> those inputs into an approved resource profile before the pod is created.”

## Scene 7: Explain and Verify the Recommendation

### Goal

Prove that the recommendation was applied to the Kubernetes pod, not merely
displayed in the UI.

### Run

```bash
kubectl get pods -n z2jh-context-demo \
  -l component=singleuser-server

kubectl describe pod -n z2jh-context-demo \
  -l component=singleuser-server |
  grep -A8 -E \
  'z2jh-context-demo.local/recommended-profile|Environment|RECOMMENDED_PROFILE|Requests|Limits'
```

Expected observation:

- `RECOMMENDED_PROFILE=large`;
- reasons mention the dataset-size, data-processing, and training signals;
- recommendation annotations are present; and
- resources match the Large profile.

### Say

> “The rule is explainable. The selected profile, reasons, and enforced
> requests are visible in pod metadata and configuration.”

The reason strings are demo output, not a statistical explanation of model
behavior. The prototype uses deterministic rules, not an LLM.

## Scene 8: Complete the Bounded Workload

In the JupyterLab terminal:

```bash
python /home/jovyan/demo/workload/train_like_workload.py
```

Expected output includes:

```text
RECOMMENDED_PROFILE=large
allocated_mib=512
Training-like workload finished without OOM.
```

### Say

> “The proposed path applied a profile before spawn and the bounded
> training-like workload completed within that profile.”

### Claim boundary

Do not say that this single run proves a reduction in OOM rate. The preserved
comparative cluster matrix observed no OOM, so it cannot estimate an OOM
reduction. The live run only demonstrates end-to-end mechanics.

## Closing Summary

Recommended closing statement:

> “This artifact demonstrates that natural-language intent, dataset-size hints,
> and lightweight code context can be converted into an explainable pre-spawn
> KubeSpawner profile. It also provides separate local and Kubernetes evaluation
> pipelines with preserved raw evidence. The results apply to controlled
> synthetic workloads and a single-node Minikube environment, not directly to a
> production multi-user JupyterHub.”

Accurate defense points:

- the prototype is rule-based and auditable;
- the Helm path applies resources before pod creation;
- raw and derived evidence are kept separate;
- local and Kubernetes measurements are not mixed;
- no real GPU behavior is evaluated;
- no history-aware recommender is implemented; and
- production performance remains future work.

## End the Demo

Stop port forwarding and pod watching with `Ctrl+C`, then:

```bash
bash scripts/uninstall.sh
```

Confirm deletion:

```bash
kubectl get namespace z2jh-context-demo
```

See [Cleanup](CLEANUP.md) for the exact deletion scope and local artifact
cleanup.
