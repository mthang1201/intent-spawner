# Getting Started

Start with the offline Protocol-v5 evidence audit. Demo deployment and synthetic
benchmarks are separate workflows and do not supply missing research results.
Run commands from the repository root.

## Quick Navigation

| Goal | Guide section | Cluster required |
| --- | --- | --- |
| Reproduce the current evidence audit | [Protocol-v5 audit](#protocol-v5-evidence-audit) | No |
| Inspect historical evidence | [Protocol-v4 reproduction](#path-d-historical-protocol-v4-reproduction) | No |
| Run software checks or synthetic examples | [Local checks](#path-a-local-synthetic-benchmark--unit-tests) | No |
| Deploy P1/P2 or configure reference LLM adapters | [Interactive demo](#path-b-interactive-jupyterhub-demo) | Disposable demo cluster |
| Try reprovisioning or dynamic resources | [Reprovisioning](#path-b10-storage-preserving-notebook-re-provisioning), [dynamic sizing](#path-b11-policy-bounded-dynamic-resource-sizing) | Disposable demo cluster |

See the [artifact index](ARTIFACT_MANIFEST.md) for detailed contracts and
[the final report](evaluation/PROTOCOL_V5_FINAL_REPORT.md) for measured outcomes.

## 1. Prerequisites & Local Environment Setup

### Local Tools Required:

* Python 3.11+
* Git and Bash
* `pip` and Python `venv` support

### Optional Tools for the Interactive Demo:

* `kubectl` and Helm 3
* A disposable local Kubernetes cluster (Minikube, Docker Desktop, k3d, kind, or OrbStack)

### Clone & Install Dependencies:

```bash
git clone https://github.com/mthang1201/intent-spawner.git
cd intent-spawner

# Create virtual environment and install dependencies
bash scripts/setup.sh
```

## Protocol-v5 evidence audit

Run the complete workflow after dependency setup:

```bash
make v5-audit
```

When `V5_RUN_ID` is unset, a new run ID is generated automatically. Outputs are written under
`results_v5/protocol-v5.0.0/final-audit/<run-id>/`: validation findings,
regenerated analysis, figures/tables, and `report/PROTOCOL_V5_FINAL_REPORT.md`.
The existing [reviewed final report](evaluation/PROTOCOL_V5_FINAL_REPORT.md)
remains the published snapshot; a new run does not overwrite it.

For stages invoked separately, generate and export one ID once:

```bash
export V5_RUN_ID="docs-audit-$(.venv/bin/python -c 'import uuid; print(uuid.uuid4().hex)')"
make v5-validate
make v5-analyze
make v5-figures
make v5-audit
```

Each stage completes its findings before returning its audit status. With the
current evidence, each command returns **2** for preserved integrity/provenance
failures; continue to the next command to inspect the available regeneration.
For a single invocation that attempts all stages despite those failures, use:

```bash
make -k v5-validate v5-analyze v5-figures v5-audit
```

Keep the exported `V5_RUN_ID` for that invocation. Do not connect stages with
`&&`, because the known audit failure would stop the sequence. A valid
`NOT_EXECUTED` package alone does not cause failure. The current overall verdict
remains **FAIL**, with confirmatory, human, resource, and storage outcomes
**NOT EXECUTED**; completing these commands does not fill those evidence gaps.

Completed stages are sealed and may be reused only with matching inputs and
implementation. Changed inputs or code require a new run ID. Inputs are selected
by the [checksum-bound inventory](../benchmarks_v5/protocol-v5-final-audit-inputs-v1.json),
never by filename recency. Original raw observations and damaged packages remain
unchanged. No stage calls a recommender, LLM provider, participant, registry, or
Kubernetes cluster, and unavailable private evidence is handled explicitly.

The [audit verification record](evaluation/PROTOCOL_V5_FINAL_AUDIT_VERIFICATION.md)
contains the reviewed commands, results, and reproduction limitations.

## Path A: Local Synthetic Benchmark & Unit Tests

### A1. Inspect the Standalone Recommender

Test intent and code-context parsing directly from the command line:

```bash
.venv/bin/python -m recommender.recommender \
  --intent "Train a scikit-learn random forest model on 1.5GB tabular dataset" \
  --dataset-gb 1.5 \
  --code-context "import pandas as pd; from sklearn.ensemble import RandomForestClassifier; model.fit(X, y)"
```

### A2. Run Software Tests

Run the full Python test suite, including P2/P3 and evaluation harness checks:

```bash
.venv/bin/python -m pytest
```

For the focused final-audit tests, use `make v5-audit-test`. The broader
`bash scripts/check.sh` runs tests, synthetic smoke checks, evidence validators,
and optional Helm/Kubernetes checks; Helm may fetch charts and Kubernetes client
checks may need API access. Use the Protocol-v5 audit above for offline evidence
reproduction without those services.

### A3. Run Local Matrix Benchmark Dry-Run

```bash
.venv/bin/python -m experiments.runner \
  --full-matrix \
  --repeats 5 \
  --seed 20260719 \
  --dry-run \
  --environment-id local-dry-run
```

---

## Path B: Interactive JupyterHub Demo

> **Safety Notice:** Run only on a disposable local Kubernetes cluster. The scripts create and mutate resources inside namespace `z2jh-context-demo`.

### B1. Install Baseline (Static Hardware Profiles)

```bash
# Check cluster context
kubectl config current-context

# Install static baseline
bash scripts/install-baseline.sh

# Start port forwarding (in a separate terminal)
bash scripts/port-forward.sh
```
Open `http://127.0.0.1:8000` and log in with any credentials. Notice the baseline asks you to choose raw hardware (Small, Medium, Large) without guidance.

### B2. Install the Context-Aware Form with P1 or P2

The default installer selects the P1 rule-based backend:

```bash
bash scripts/install-proposed.sh
```

To select the main proposed P2 backend explicitly:

```bash
BACKEND_VALUES=helm/recommender-p2-values.yaml bash scripts/install-proposed.sh
```

The installer packages the runtime, verifies configuration, and applies its
rollout checksum. See [Helm backend deployment](HELM_BACKEND_DEPLOYMENT.md).

Open `http://127.0.0.1:8000`. You will see the new **Workload Intent Form**:
1. Enter your task (e.g., `I will train a scikit-learn model on a 1.5GB CSV dataset`).
2. Enter dataset size: `1.5`.
3. Enter code snippet:
   ```python
   import pandas as pd
   from sklearn.ensemble import RandomForestClassifier
   df = pd.read_csv("data.csv")
   model.fit(X, y)
   ```
4. Click **Preview recommendation**: Inspect the returned profile, image, explanation, and any fallback/manual-selection notice for the selected backend.
5. Click **Confirm recommendation**: KubeSpawner applies the configuration and creates the user pod.

---

## Path B-LLM: Configuring External LLM (Google Gemini)

The checked-in Gemini overlay records the Protocol-v4 reference configuration.
Using it makes live provider calls; it is separate from offline audit reproduction.
See [external LLM configuration](EXTERNAL_LLM_RECOMMENDER.md).

### 1. Create the Kubernetes Secret

```bash
read -rsp 'Enter Gemini API key: ' GEMINI_KEY; echo
kubectl create secret generic intent-spawner-external-llm \
  --namespace=z2jh-context-demo \
  --from-literal=api-key="$GEMINI_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
unset GEMINI_KEY
```

### 2. Deploy with Gemini Configuration

```bash
BACKEND_VALUES=helm/gemini-values.yaml bash scripts/install-proposed.sh
```

---

## Path B-Ollama: Configuring Self-Hosted LLM (Local Ollama)

To run inference on a local Ollama service (installation and model download require network access):

### 1. Start Local Ollama

```bash
# Install Ollama (macOS)
brew install ollama

# Start the Ollama daemon
ollama serve

# In another terminal, pull the model
ollama pull llama3
```

### 2. Deploy with Ollama Configuration

```bash
BACKEND_VALUES=helm/ollama-values.yaml bash scripts/install-proposed.sh
```

---

## Path B10: Storage-Preserving Notebook Re-Provisioning

Test changing workloads on an already-running notebook session:

1. Inside your running JupyterLab terminal, save a persistent file:
   ```bash
   printf 'persistent marker\n' > /home/jovyan/reprovision-marker.txt
   ```
2. In your browser, navigate to: `http://127.0.0.1:8000/hub/reprovision`
3. Enter your new workload (e.g., deep learning with PyTorch).
4. Click **Preview replacement**: Notice the comparison between current and proposed profiles and the red restart warning.
5. Check the acknowledgement checkbox and click **Stop old pod and create replacement**.
6. Observe in your terminal:
   ```bash
   kubectl get pods,pvc -n z2jh-context-demo -w
   ```
   The old pod terminates, a new pod with the new profile/image is created, and `/home/jovyan/reprovision-marker.txt` is retained on the PVC.

---

## Path B11: Policy-Bounded Dynamic Resource Sizing

To enable fine-grained continuous CPU/RAM sizing instead of fixed profile tiers:

```bash
bash scripts/install-dynamic.sh
```
This activates [`helm/dynamic-values.yaml`](../helm/dynamic-values.yaml) and applies administrator min/max/step, GPU allowlist, and static per-spawn cap checks. It does not query live quota headroom; Kubernetes admission remains authoritative.

---

## Path D: Historical Protocol-v4 Reproduction

Protocol-v4 is historical/formative evidence. Validate its portable core and
reproduce its headline analysis without private services:

```bash
.venv/bin/python scripts/validate-portable-evidence.py
```

To validate the frozen bilingual benchmark and preview its original matrices:

```bash
make v4-validate
```

This target runs the recommendation and system planners with `--dry-run`; it
collects no new model responses or cluster measurements.

The authoritative observed matrices are already complete. Do not overwrite or present a new dry run as those results. Their interpretation and exact evidence identities are documented in:

* [`evaluation/PROTOCOL_V4_REVISED_EVALUATION_REPORT.md`](evaluation/PROTOCOL_V4_REVISED_EVALUATION_REPORT.md)
* [`evaluation/PROTOCOL_V4_EXTERNAL_LLM_LIVE_REPORT.md`](evaluation/PROTOCOL_V4_EXTERNAL_LLM_LIVE_REPORT.md)
* [`evaluation/STAGE_C_CONFIRMATORY_REPORT.md`](evaluation/STAGE_C_CONFIRMATORY_REPORT.md)

Live external or Stage C reproduction requires an explicit operator decision, frozen configuration, credentials, and a disposable cluster. Follow [`evaluation/PROTOCOL_V4_REPRODUCIBILITY.md`](evaluation/PROTOCOL_V4_REPRODUCIBILITY.md); never point a new run at an authoritative result directory.

---

## Cleanup

Follow the [cleanup runbook](../CLEANUP.md) for the exact demo namespace and local
artifact lifecycle. Namespace deletion can delete notebook PVC data. Preserve
all checksum-bound evidence and sealed audit outputs, including failed audits.
