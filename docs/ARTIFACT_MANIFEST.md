# Artifact Manifest

## Source And Configuration

- `recommender/`: rule-based profile recommender and tests.
- `helm/baseline-values.yaml`: baseline static-profile JupyterHub values.
- `helm/proposed-values.yaml`: proposed intent/context-aware JupyterHub values.
- `k8s/`: demo pods and resource quota manifests.
- `workload/`: original demo workload scripts.
- `cluster_evaluation/`: Kubernetes workload image, pod runner, method policies,
  raw-integrity validator, and raw-to-derived analysis.

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
- `cluster_evaluation/validate_artifacts.py`: reconciles every preserved cluster
  plan, result, sidecar, applied-resource observation, and supporting path.
- `cluster_evaluation/result_compat.py`: preserves schema-1 CPU values while
  exposing their actual average or sampled-maximum semantics.
- `cluster_evaluation/timing.py`: versioned interval-censored timing rule.
- `cluster_evaluation/capacity_runner.py`: preregistered capacity-v2 runner with
  dry-run and exact-label cleanup paths.
- `cluster_evaluation/raw_integrity.py`: verifies every tracked raw file against
  `docs/evaluation/RAW_EVIDENCE_SHA256SUMS.txt`.

## Preserved Raw Evidence

- `experiments/raw/20260719T140417Z-smoke-171688c0`: smoke run evidence.
- `experiments/raw/20260719T140423Z-matrix-783b4141`: dry-run matrix planning
  evidence.
- `experiments/raw/20260719T140431Z-matrix-aed48949`: full local synthetic
  matrix used to generate the committed analysis outputs.
- `results/cluster/raw/ground-truth-39b6973-seed20260720`: 108 retained pod
  outcomes and supporting evidence.
- `results/cluster/raw/comparative-39b6973-seed20260720`: 180 retained pod
  outcomes and supporting evidence.
- `results/cluster/raw/capacity-39b6973-seed20260721`: nine batch outcomes and
  108 per-pod capacity observations. The exact evaluated batch generator is not
  present; this corpus is supplementary and not principal claim support.
- `results/cluster/raw/capacity-v2-ca2e74b-seed20260721`: principal controlled
  capacity evidence generated from committed protocol 2.0.0 at
  `ca2e74b2043a5ea85a68119097d6c325fe84c294`; 9 counterbalanced batches, 108
  per-pod outcomes, sanitized Minikube/image provenance, and successful
  exact-label cleanup for every batch.
- `docs/evaluation/RAW_EVIDENCE_SHA256SUMS.before-0ffbd9a.txt`: file-by-file
  baseline recorded before blocker resolution.
- `docs/evaluation/RAW_EVIDENCE_SHA256SUMS.txt`: current file-by-file raw
  integrity manifest.

Raw records include the source `git_commit` used when they were generated. The
current manifest covers 1,877 tracked raw files; the pre-audit baseline covers
1,541 files and remains independently verified. New raw runs are ignored by
default unless explicitly reviewed and force-added.

## Derived Outputs

- `results/*.csv`: regenerated analysis tables.
- `results/figures/*.svg`: regenerated figures.
- `results/environment-capability.json`: captured environment capability report.
- `docs/evaluation/RESULTS.md`: narrative analysis report generated from raw
  records plus the environment capability report.
- `results/cluster/derived/`: Kubernetes-backed tables and SVG figures.
- `benchmarks/observed_resource_envelopes.yaml`: raw-run-linked operational
  envelopes using timing rule 2.0.0 without an offset or arbitrary guard.
- `docs/evaluation/CLUSTER_RESULTS.md`: generated, scoped Kubernetes report.

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
make validate-cluster-results
make validate-raw-integrity
make capacity-dry-run
make regenerate-cluster-results
```

## Generated Files Not Normally Committed

- `.venv/`, `.pytest_cache/`, and `__pycache__/`.
- New directories under `experiments/raw/`.
- New CSVs under `experiments/summaries/`.
- Temporary analysis outputs under `/tmp`.
