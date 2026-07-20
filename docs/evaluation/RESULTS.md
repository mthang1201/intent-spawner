# Evaluation Results

## Scope

These results are derived from immutable local synthetic benchmark records. They are not live JupyterHub pod experiments, and they do not support cluster-wide efficiency claims.

- Raw input: `experiments/raw/20260719T140431Z-matrix-aed48949/results.jsonl`
- Experiment directory: `experiments/raw/20260719T140431Z-matrix-aed48949`
- Records analyzed: 180
- Recorded git commit: `fea62042374075e0abc724d2dfcd7752cb3bf865`
- Recorded git branch: `codex/evaluation-analysis-run`
- Environment ID: `local-benchmark-orbstack-no-metrics`
- Planned runs: 180
- Python: `3.14.5`
- Kubernetes context: `orbstack`
- Helm: `v4.2.0+g0646808`

## Environment Capability

- Container runtime: docker-orbstack
- Kubernetes context: orbstack
- Helm available: True
- CPU count: 8
- Memory bytes: 17179869184
- Metrics API available: False

**Blocker:** Kubernetes resource metrics are unavailable in this environment. `kubectl top nodes` failed, so Kubernetes CPU samples, memory peaks, Pending-time, OOMKilled, and restart/respawn comparisons cannot be claimed from live cluster evidence.

Exact preflight command that must succeed in a suitable cluster-backed environment:

```bash
kubectl top nodes && kubectl top pods -A --containers
```

## Directly Observed Findings

- Local synthetic records completed: 180/180; failures: 0/180.
- Median time to success was 0.004771 seconds with IQR 0.029014 across non-missing local timings.
- Median memory request-to-peak ratio was 34.857143 with IQR 24.980952 using Python `resource.getrusage` peak RSS.
- Missing CPU usage measurements: 180/180. Missing Kubernetes Pending-time measurements: 180/180.

Run counts and exclusions:

| method | planned_count | recorded_count | successful_count | failed_count | timeout_count | excluded_count | missing_cpu_usage_count | missing_pending_time_count |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| static_manual | 60 | 60 | 60 | 0 | 0 | 0 | 60 | 60 |
| intent_only | 60 | 60 | 60 | 0 | 0 | 0 | 60 | 60 |
| context_aware | 60 | 60 | 60 | 0 | 0 | 0 | 60 | 60 |
| all | 180 | 180 | 180 | 0 | 0 | 0 | 180 | 180 |

Ablation summary:

| method | run_count | success_rate | acceptable_profile_rate | under_profile_rate | over_profile_rate | policy_warning_rate | median_memory_request_to_peak_ratio |
| --- | --- | --- | --- | --- | --- | --- | --- |
| static_manual | 60 | 1 | 1 | 0 | 0 | 0 | 34.8571 |
| intent_only | 60 | 1 | 0.666667 | 0.333333 | 0 | 0 | 23.5286 |
| context_aware | 60 | 1 | 1 | 0 | 0 | 0.083333 | 34.8571 |

## Interpretation

Within this controlled local benchmark, all methods completed the synthetic workloads. Differences in requested resources are driven by the deterministic profile-selection policies and the fixed manifest signals, not by adaptive tuning after observing results.

The memory waste-ratio table is useful for comparing profile conservatism in this local process model. It should not be interpreted as Kubernetes pod utilization because the resource source is Python peak RSS, not metrics-server or Prometheus.

## Failed Or Inconclusive Cases

- Live cluster resource-metric evidence is inconclusive because the Metrics API is unavailable.
- OOMKilled, restart/respawn, and Pending-time comparisons are reported with missing-data counts rather than inferred values.
- No run was excluded from the local comparative summary; missing cluster-only measurements remain visible in the tables.

## Unsupported Claims

- These results do not show that the approach is generally effective for all JupyterHub deployments.
- These results do not show improved real cluster density or scheduler behavior.
- These results do not validate history-aware provisioning or GPU execution.

## Limitations

- The benchmark uses generated synthetic data and local Python processes.
- Dataset size values are declared hints, not measured data sizes.
- Peak memory is process-level RSS; CPU usage is unavailable here.
- The full live-cluster experiment remains blocked until a working resource-metric source is present and Kubernetes pod evidence is collected.

## Generated Outputs

- `results/environment-capability.json`
- `results/summary.csv`
- `results/run_counts_and_exclusions.csv`
- `results/oom_failure_rates.csv`
- `results/restart_respawn_comparison.csv`
- `results/time_to_success_comparison.csv`
- `results/pending_time_comparison.csv`
- `results/requested_vs_peak_scatter.csv`
- `results/waste_ratio_comparison.csv`
- `results/recommendation_confusion.csv`
- `results/ablation.csv`
- `results/per_workload_results.csv`
- `results/robustness_boundary_summary.csv`
- `results/figures/failure_rate.svg`
- `results/figures/time_to_success_median.svg`
- `results/figures/memory_waste_ratio_median.svg`
- `results/figures/requested_vs_peak_memory.svg`
