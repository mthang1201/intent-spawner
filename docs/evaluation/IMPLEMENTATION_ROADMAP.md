# Research Evaluation Implementation Roadmap

## Purpose

This document distinguishes implemented repository behavior from future
evaluation work. Plans and hypotheses are not evidence. The authoritative final
review is `docs/evaluation/FINAL_AUDIT.md`.

## Current State

Implemented:

- Static Small, Medium, and Large JupyterHub profiles with the same approved
  resource mapping used by the proposed method.
- A proposed spawn form that accepts intent, a dataset-size hint, and optional
  code context.
- A rule-based recommender mirrored in standalone Python and Helm inline Python.
- A pre-spawn hook that applies CPU and memory before pod creation and emits a
  derived explanation without retaining raw intent or code context.
- Twelve synthetic deterministic workload definitions and a portable workload
  runner.
- Unit, integration-smoke, syntax, Helm-render, and Kubernetes-manifest checks.
- Bounded manual demos for OOM, request-based scheduling pressure, and a
  recommendation-applied workload.

Not implemented:

- A fair automated static baseline condition.
- A separately runnable intent-only condition.
- A method matrix, repetitions, randomization, or exclusion protocol in code.
- A result schema and immutable raw result recorder.
- Comparative results, statistical summaries, or figure generation.
- Reliable peak-usage collection.
- A history store or history-aware recommendation path.
- CI and a project software license.

## Capability Matrix

| Capability | Status | Evidence |
| --- | --- | --- |
| Static profiles | Implemented for demo | `helm/baseline-values.yaml` |
| Context-aware form and rules | Implemented for demo | `helm/proposed-values.yaml` |
| Applied pre-spawn resources | Implemented for demo | `context_pre_spawn_hook` and `tests/test_config_validation.py` |
| Standalone rule mirror | Implemented | `recommender/recommender.py` |
| Deterministic workload suite | Implemented | `benchmarks/workloads.yaml`, `benchmarks/workload_runner.py` |
| Static comparative baseline | Not implemented | No method runner or fixed static assignment protocol |
| Intent-only isolation | Not implemented | No named mode that omits code context |
| Context-aware isolation | Partially implemented | Signals are applied, but no comparative trials exist |
| Admin policy enforcement | Not implemented | `PROFILE_RESOURCES` is a hard-coded mapping; GPU fallback is not a policy engine |
| Experiment integrity controls | Not implemented | No run IDs, recorder, expected trial count, exclusion log, or commit capture |
| Results and figures | Not implemented | No merged `results/` or raw experiment directory |
| History-aware behavior | Not implemented | No history collection, storage, feature, or test |

## Supported Claim Boundary

The repository can support this prototype claim:

> A lightweight rule-based layer can translate intent, a dataset-size hint, and
> optional code context into one of four recommendation labels, map that label
> to approved Small/Medium/Large resources, and apply the resources in a
> KubeSpawner pre-spawn hook with a derived explanation.

It cannot currently support claims that the method reduces OOM rate, resource
waste, pending time, restarts, or time to success; outperforms static or
intent-only selection; generalizes to real users; enforces arbitrary admin
policy; or uses history effectively.

## Minimum Evaluation Still Required

1. Define a method runner with explicit `static`, `intent_only`, and
   `context_aware` modes. Intent-only must pass an empty code-context field by
   construction and record that fact.
2. Freeze a fair static assignment policy before observing outcomes. Use the
   same workloads, images, resource bands, timeouts, and execution order rules
   for all methods.
3. Add a versioned result schema with run ID, commit SHA, environment metadata,
   method, workload, trial index, recommendation, applied resources, outcome,
   units, failure classification, and artifact hashes.
4. Reject duplicate run IDs and incomplete records; preserve failed runs and
   record every exclusion with a reason.
5. Run repeated trials with a declared expected count. Report actual, failed,
   and excluded counts before outcome metrics.
6. Capture Kubernetes pod status, termination reason, scheduling events,
   requests/limits, and timestamps. Add a validated metrics source before
   making peak-usage or request-to-usage claims.
7. Regenerate tables and figures from committed sanitized raw evidence using
   one documented command.
8. Add CI, an environment report, and an author-selected software license.

## Suggested Result Fields

This is a design requirement, not an observed result:

```text
schema_version
run_id
commit_sha
method
workload_id
trial_index
seed
recommended_profile
applied_cpu_request_m
applied_memory_request_mi
pod_phase
oom_killed
pending_seconds
time_to_success_seconds
metrics_source
failure_class
excluded
exclusion_reason
artifact_sha256
```

Use explicit units in field names. Do not store raw intent, pasted code,
notebook content, usernames, pod UIDs, node names, or cluster context names in
committed evidence.

## History-Aware Provisioning

Treat history awareness as future work. A defensible implementation would need
event capture, durable storage, privacy-preserving workload identity, retention
rules, policy guardrails, and cold-start versus warm-history trials. None of
those components is present in the evaluated repository.

## Current Verification Commands

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
make check

python -m benchmarks.workload_runner \
  --workload-id light_basic_python --scale tiny --seed 1101

bash scripts/check-cluster.sh
bash scripts/install-baseline.sh
bash scripts/install-proposed.sh
bash scripts/uninstall.sh
```

The install and demo scripts mutate `z2jh-context-demo`; use them only on an
isolated local cluster. There is no result- or figure-regeneration command until
the missing experiment package is implemented.
