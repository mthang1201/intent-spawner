# Runbook: Four-Method Recommender Evaluation

This runbook provides end-to-end operational procedures for running the four-method evaluation framework, analyzing results, and validating deployment in Kubernetes.

---

## 1. Prerequisites and Setup

Activate the isolated Python virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Verify the environment:

```bash
.venv/bin/python -m pytest tests/test_four_method_evaluation.py
```

---

## 2. Running the Evaluation Matrix

### 2.1. Offline Deterministic Evaluation (Static & Rule-Based)

To run the deterministic baselines across the test split:

```bash
.venv/bin/python -m evaluation_v4.run_recommenders \
  --recommenders "static_profile_baseline,rule_based_mapping" \
  --split test \
  --repeats 5 \
  --seed 20260808 \
  --randomize-order \
  --output results/offline-predictions
```

### 2.2. External LLM Evaluation (Gemini-Compatible API)

Set your Gemini API key in the environment or provide it via CLI:

```bash
export EXTERNAL_LLM_API_KEY="your-gemini-api-key"
export EXTERNAL_LLM_ENDPOINT="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
export EXTERNAL_LLM_MODEL="gemini-3.5-flash"

# Leave pricing unset unless a versioned, model-specific pricing snapshot is
# available. When it is, prefer EXTERNAL_LLM_PRICING_CONFIG_PATH so the price,
# effective date, applicable model, and source are reproducible.

.venv/bin/python -m evaluation_v4.run_recommenders \
  --recommenders "external_llm" \
  --split test \
  --repeats 5 \
  --seed 20260808 \
  --randomize-order \
  --output results/external-llm-predictions
```

### 2.3. Self-Hosted Local LLM Evaluation (Ollama)

Start Ollama and pull the evaluated model:

```bash
ollama run llama3:latest
```

Execute the Ollama evaluation:

```bash
export OLLAMA_ENDPOINT="http://localhost:11434/v1/chat/completions"
export OLLAMA_MODEL="llama3:latest"

.venv/bin/python -m evaluation_v4.run_recommenders \
  --recommenders "self_hosted_local_ollama_llm" \
  --split test \
  --repeats 5 \
  --seed 20260808 \
  --randomize-order \
  --output results/ollama-predictions
```

### 2.4. Full 4-Method Combined Matrix with Safe Resume

To execute all four approaches concurrently or sequentially with automated checkpointing and retry resumption:

```bash
.venv/bin/python -m evaluation_v4.run_recommenders \
  --recommenders "static_profile_baseline,rule_based_mapping,external_llm,self_hosted_local_ollama_llm" \
  --split test \
  --repeats 5 \
  --seed 20260808 \
  --randomize-order \
  --resume \
  --output results/four-method-predictions
```

---

## 3. Generating Analysis and Thesis Reports

Execute the statistical analyzer:

```bash
.venv/bin/python -m evaluation_v4.analyze \
  --predictions results/four-method-predictions/predictions.jsonl \
  --bootstrap-replicates 2000 \
  --seed 20260808 \
  --out results/four-method-analysis
```

### Generated Artifacts Checklist

| Artifact | Format | Description |
| :--- | :--- | :--- |
| `REPORT.md` | Markdown | Comprehensive report structuring evaluation metrics and evidence status across RQ1–RQ5 (with explicit claim gates for pending live experiments). |
| `recommendation-summary.csv` | CSV | Overall accuracy, under/over-provisioning, and CI bounds. |
| `recommendation-breakdowns.csv` | CSV | Stratified performance across languages, sizes, and strata. |
| `pairwise-mcnemar-holm.csv` | CSV | Exact McNemar paired test matrix with Holm adjustment. |
| `pairwise-wilcoxon-holm.csv` | CSV | Paired Wilcoxon signed-rank latency tests with Holm adjustment. |
| `pairwise-raw-llm-mcnemar-holm.csv` | CSV | Fallback-isolated raw LLM exact paired tests with a separate Holm family. |
| `pairwise-raw-llm-wilcoxon-holm.csv` | CSV | Fallback-isolated raw LLM sample-mean paired tests. |
| `raw-llm-effect-sizes.csv` | CSV | Raw external-vs-local risk differences, family-clustered intervals, and effect sizes. |
| `profile-confusion-matrices.json` | JSON | 3x3 confusion matrices (small, medium, large) per method. |
| `latency-cost-summary.csv` | CSV | Wall-clock latency, token consumption, and cost estimates. |
| `analysis-manifest.json` | JSON | Audit manifest with input SHA-256 sums and claim-gate status. |

---

## 4. Kubernetes Live Cluster Verification

To deploy the context-aware spawner into a test Kubernetes cluster (`z2jh-context-demo` namespace):

```bash
# 1. Inspect cluster safety and namespace isolation
bash scripts/check-cluster.sh

# 2. Deploy rule-based context spawner
helm upgrade --install context-demo jupyterhub/jupyterhub \
  --version 4.0.0 \
  --namespace z2jh-context-demo \
  --create-namespace \
  --values helm/proposed-values.yaml

# 3. Optional: Overlay external Gemini backend
kubectl create secret generic intent-spawner-external-llm \
  --namespace z2jh-context-demo \
  --from-literal=api-key="${EXTERNAL_LLM_API_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install context-demo jupyterhub/jupyterhub \
  --version 4.0.0 \
  --namespace z2jh-context-demo \
  --values helm/proposed-values.yaml \
  --values helm/recommender-external-llm-gemini.example.yaml

# 4. Clean up after validation
helm uninstall context-demo --namespace z2jh-context-demo
```
