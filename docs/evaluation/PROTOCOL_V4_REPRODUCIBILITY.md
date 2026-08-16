# Protocol-v4 Revised Reproducibility Commands

These commands create new timestamped output directories. Never point an
output argument at `results/v4-live-20260810`.

## Environment and non-mutating validation

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest
python3 -m compileall -q recommender workload scripts evaluation_v4
bash -n scripts/*.sh
helm template context-demo jupyterhub/jupyterhub --version 4.0.0 \
  --namespace z2jh-context-demo --values helm/baseline-values.yaml >/tmp/intent-spawner-baseline-render.yaml
helm template context-demo jupyterhub/jupyterhub --version 4.0.0 \
  --namespace z2jh-context-demo --values helm/proposed-values.yaml >/tmp/intent-spawner-proposed-render.yaml
kubectl apply --dry-run=client -f k8s/idle-large-pod.yaml
kubectl apply --dry-run=client -f k8s/idle-small-pod.yaml
kubectl apply --dry-run=client -f k8s/resource-quota.yaml
git diff --check
```

## Local Ollama development smoke

```bash
.venv/bin/python -m evaluation_v4.run_recommenders \
  --recommenders self_hosted_local_ollama_llm \
  --split development --repeats 1 --seed 20260812 --randomize-order \
  --prompt-version prompt-v4.1.0 \
  --ollama-endpoint http://127.0.0.1:11434/api/chat \
  --ollama-model llama3:latest --ollama-temperature 0 --ollama-timeout 60 \
  --experiment-id protocol-v4-ollama-development-<timestamp> \
  --output results/v4-ollama-development-<timestamp>
```

## Revised held-out matrix and analysis

Run this only after freezing the development-tested interface. For any new
execution, external cells are explicit blockers unless secure provider
configuration is present. The authoritative 2026-08-13 matrices described
below are already complete and must not be overwritten.

```bash
.venv/bin/python -m evaluation_v4.run_recommenders \
  --recommenders static_profile_baseline,rule_based_mapping,external_llm,self_hosted_local_ollama_llm \
  --split test --repeats 5 --seed 20260812 --randomize-order \
  --prompt-version prompt-v4.1.0 \
  --ollama-endpoint http://127.0.0.1:11434/api/chat \
  --ollama-model llama3:latest --ollama-temperature 0 --ollama-timeout 60 \
  --experiment-id protocol-v4-revised-test-<timestamp> \
  --output results/v4-revised-test-<timestamp>

.venv/bin/python -m evaluation_v4.analyze \
  --predictions results/v4-revised-test-<timestamp>/predictions.jsonl \
  --bootstrap-replicates 2000 --seed 20260812 \
  --out results/v4-revised-test-<timestamp>-analysis

.venv/bin/python -m evaluation_v4.validate_evidence \
  --dir results/v4-revised-test-<timestamp> \
  --analysis-dir results/v4-revised-test-<timestamp>-analysis
```

## Stage C validation and combined analysis

Only use the disposable labelled namespace. Start the Hub port-forward in a
separate terminal, then run the validation. The authoritative full 320-trial
run has completed at `results/v4-stage-c-confirmatory-20260813T021600Z`; any
new cluster execution still requires a separate deliberate operator decision.

```bash
kubectl --context orbstack -n z2jh-context-demo port-forward service/proxy-public 18000:80

.venv/bin/python -m evaluation_v4.run_system \
  --plan results/v4-stage-c-validation-plan-20260812T102652Z/system-plan.jsonl \
  --experiment-id protocol-v4-stage-c-validation-<timestamp> \
  --context orbstack --hub-url http://127.0.0.1:18000 \
  --ollama-endpoint http://127.0.0.1:11434/api/chat \
  --ollama-model llama3:latest --ollama-prompt-version prompt-v4.1.0 \
  --ollama-temperature 0 --ollama-timeout 60 \
  --output results/v4-stage-c-validation-<timestamp> --execute

# If and only if the same run was interrupted, repeat the exact command with:
#   --execute --resume
# The executor validates the original experiment ID, plan checksum, frozen
# environment, exact completed plan prefix, sidecars, and cleanup status before
# it skips any trial. Interrupted attempt directories are retained and retries
# use a new attempt directory.

.venv/bin/python -m evaluation_v4.analyze \
  --predictions results/v4-revised-test-<timestamp>/predictions.jsonl \
  --system-trials results/v4-stage-c-validation-<timestamp>/system-trials.jsonl \
  --bootstrap-replicates 2000 --seed 20260812 \
  --out results/v4-final-combined-analysis-<timestamp>
```

Every live run must retain its manifest, completion record, checksums, and raw
sidecars before any narrative summary is written.

The authoritative Stage C plan contains four methods, eight executable
families, and ten repeats (320 trials). Its human-readable result and evidence
boundary are in `STAGE_C_CONFIRMATORY_REPORT.md`; do not infer current status
from the earlier one-repeat validation command above.

## External Gemini 3.5 Flash confirmatory matrix (2026-08-13)

The retired `gemini-2.0-flash` model was replaced by the explicitly selected
stable `gemini-3.5-flash` before any held-out external trial. Verify the
development gate in `EXTERNAL_LLM_GEMINI_3_5_VERIFICATION.md` before using the
held-out split. Do not configure pricing unless a versioned model-specific
snapshot with effective date and source is available.

```bash
EXTERNAL_LLM_API_KEY="$(kubectl -n z2jh-context-demo get secret \
  intent-spawner-external-llm -o jsonpath='{.data.api-key}' | base64 --decode)" \
RECOMMENDER_BACKEND=external_llm \
EXTERNAL_LLM_ENDPOINT='https://generativelanguage.googleapis.com/v1beta/openai/chat/completions' \
EXTERNAL_LLM_MODEL='gemini-3.5-flash' \
EXTERNAL_LLM_TIMEOUT='10' EXTERNAL_LLM_TOTAL_TIMEOUT='30' \
EXTERNAL_LLM_MAX_RETRIES='2' EXTERNAL_LLM_RETRY_BACKOFF_SECONDS='0.25' \
EXTERNAL_LLM_TEMPERATURE='0' EXTERNAL_LLM_MAX_CONCURRENT_RECOMMENDATIONS='4' \
EXTERNAL_LLM_ALLOW_INSECURE_HTTP='false' \
.venv/bin/python -m evaluation_v4.run_recommenders \
  --recommenders external_llm \
  --split test --repeats 5 --seed 20260808 --randomize-order \
  --prompt-version prompt-v4.1.0 \
  --experiment-id protocol-v4-external-confirmatory-<UTC> \
  --output results/v4-external-confirmatory-<UTC>
```

Use `--resume` only for the same interrupted directory and exact command. The
authoritative execution produced 240/240 records at
`results/v4-external-confirmatory-20260813T045543Z`.

Create a new derived four-method view without modifying the historical
missing-credentials source, then run the family-aware combined analysis:

```bash
.venv/bin/python -m evaluation_v4.combine_external_results \
  --baseline-dir results/v4-revised-test-20260812T095453Z \
  --external-dir results/v4-external-confirmatory-20260813T045543Z \
  --output results/v4-combined-evidence-<UTC>

.venv/bin/python -m evaluation_v4.validate_evidence \
  --dir results/v4-combined-evidence-<UTC>

.venv/bin/python -m evaluation_v4.analyze \
  --predictions results/v4-combined-evidence-<UTC>/predictions.jsonl \
  --system-trials results/v4-stage-c-confirmatory-20260813T021600Z/system-trials.jsonl \
  --bootstrap-replicates 2000 --seed 20260808 \
  --out results/v4-final-combined-external-analysis-<UTC>

.venv/bin/python -m evaluation_v4.validate_evidence \
  --dir results/v4-combined-evidence-<UTC> \
  --analysis-dir results/v4-final-combined-external-analysis-<UTC>
```

The authoritative combined analysis is
`results/v4-final-combined-external-analysis-v2-20260813T050836Z`.
