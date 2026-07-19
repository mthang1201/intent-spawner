# Artifact Manifest

## Source And Configuration

- `recommender/`: rule-based profile recommender and tests.
- `helm/baseline-values.yaml`: baseline static-profile JupyterHub values.
- `helm/proposed-values.yaml`: proposed intent/context-aware JupyterHub values.
- `k8s/`: demo pods and resource quota manifests.
- `workload/`: original demo workload scripts.
- `notebooks/`: demonstration notebooks, not benchmark data sources.

## Setup And Verification

- `requirements.txt`: pinned runtime Python dependency set.
- `requirements-dev.txt`: pinned development/test dependency set.
- `scripts/setup.sh`: creates `.venv` and installs pinned dependencies.
- `scripts/check.sh`: repository verification command.
- `scripts/environment-report.sh`: writes a sanitized local capability report.
- `scripts/check-cluster.sh`: read-only cluster prerequisite check.
- `scripts/uninstall.sh`: Kubernetes cleanup command.

## Benchmark And Experiment Harness

- `benchmarks/workloads.yaml`: deterministic workload manifest, synthetic-data
  declarations, profile expectations, and license statements.
- `benchmarks/workload_runner.py`: local synthetic workload executor.
- `experiments/runner.py`: smoke, dry-run, full-matrix, resume, and aggregate
  orchestration.
- `experiments/recorder.py`: raw result construction and workload execution.
- `experiments/result_schema.py` and `experiments/result_schema.schema.json`:
  versioned raw record schema.
- `experiments/analyze_results.py`: table, figure, and report reproduction.
- `experiments/capture_environment.py`: local capability report capture.
- `tests/`: unit tests and sanitized parser fixtures.

## Preserved Raw Evidence

- `experiments/raw/20260719T140417Z-smoke-171688c0`: smoke run evidence.
- `experiments/raw/20260719T140423Z-matrix-783b4141`: dry-run matrix planning
  evidence.
- `experiments/raw/20260719T140431Z-matrix-aed48949`: full local synthetic
  matrix used to generate the committed analysis outputs.

Raw records include the source `git_commit` used when they were generated. New
raw runs are ignored by default unless explicitly reviewed and force-added.

## Derived Outputs

- `results/*.csv`: regenerated analysis tables.
- `results/figures/*.svg`: regenerated figures.
- `results/environment-capability.json`: captured environment capability report.
- `docs/evaluation/RESULTS.md`: narrative analysis report generated from raw
  records plus the environment capability report.

## Reproduction Commands

```bash
bash scripts/setup.sh
bash scripts/check.sh
.venv/bin/python -m experiments.runner --smoke --environment-id local-smoke --timeout 60
.venv/bin/python -m experiments.runner --full-matrix --repeats 5 --seed 20260719 --dry-run --environment-id local-dry-run
.venv/bin/python -m experiments.analyze_results \
  --experiment-dir experiments/raw/20260719T140431Z-matrix-aed48949 \
  --results-dir /tmp/intent-spawner-results \
  --results-md /tmp/intent-spawner-results/RESULTS.md \
  --environment-report results/environment-capability.json \
  --overwrite
```

## Generated Files Not Normally Committed

- `.venv/`, `.pytest_cache/`, and `__pycache__/`.
- New directories under `experiments/raw/`.
- New CSVs under `experiments/summaries/`.
- Temporary analysis outputs under `/tmp`.
