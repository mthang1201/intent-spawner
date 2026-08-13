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

Run this only after freezing the development-tested interface. External cells
will be explicit blockers unless secure provider configuration is present.

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
separate terminal, then run the validation. The full 320-trial plan requires a
separate deliberate operator decision.

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
