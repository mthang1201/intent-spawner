# Protocol-v4 Stage C Implementation and Validation Report

## Evidence status

Stage C was executed against the disposable local JupyterHub namespace
`z2jh-context-demo` on the single-node OrbStack context. Preflight verified the
namespace safety label, one healthy node, deployed Hub, no active synthetic
user pods, warm required images, and cleanup isolation. The Metrics API was not
available, so successful workloads used in-container cgroup-v2 window metrics;
missing measurements remain null.

The authoritative validation run is
`results/v4-stage-c-validation-v4.2-20260813T013600Z`. It contains 32/32
observed `system-trial-v4.1.0` records: four methods, eight frozen workload
families, and one randomized/counterbalanced runtime repeat. Its
`system-trials.jsonl` SHA-256 is
`087db516208d5d9774d90752a2821585d9bdd078d2febe6442e0179a97fd354a`.
All pods and all between-trial cleanup operations completed; no unrelated
cluster resource was removed.

## Captured fields

The v4.1 evidence contract records spawn/workload success, Pending,
unschedulable, OOM, timeout, image-start failure, spawn/workload durations,
CPU and memory requests/limits, cgroup CPU mean and memory mean/peak, selected
profile/image, recommender, fallback, family/sample/repeat, timestamps, and
pseudonymous SHA-256 pod/node identities. Per-trial sidecars retain sanitized
preview, spawn result, Kubernetes pod/event evidence, stdout/stderr, cleanup,
and decision provenance.

## Observed validation outcomes

| Method | Trials | Spawned | Workload success | OOM | Timeout | Pending/image failure | Fallback | Mean memory request |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `static_small` | 8 | 8 | 2 | 5 | 1 | 0 | 0 | 256 MiB |
| `static_large` | 8 | 8 | 8 | 0 | 0 | 0 | 0 | 1,536 MiB |
| `rule_based_context` | 8 | 8 | 5 | 3 | 0 | 0 | 0 | 800 MiB |
| `self_hosted_local_ollama_llm` | 8 | 8 | 5 | 3 | 0 | 0 | 0 | 768 MiB |

Only completed workloads provide cgroup window metrics: 2, 8, 5, and 5 rows
respectively. Consequently, utilization summaries are conditional on workload
survival and must not be interpreted as full-matrix efficiency estimates.
Static-large achieved the highest validation success but requested the most
memory; the two adaptive conditions reduced mean requests while each missed
three demands. The single repeat supports integration and failure-mode
validation, not stable runtime or efficiency inference.

## Diagnostic runs retained but excluded

- `v4-stage-c-validation-20260813T010400Z`: 11 incomplete records exposed a Hub
  overlay bug that dropped normalized `dataset_size_gb`; no workload was run.
- `v4-stage-c-smoke-20260813T011300Z`: one 404 smoke record after an incomplete
  Helm overlay invocation.
- `v4-stage-c-smoke2-20260813T011500Z`: one real static-small CPU timeout after
  restoring the dynamic/reprovision overlays.
- `v4-stage-c-validation-v4.1-20260813T012000Z`: 32 complete records, but all
  eight nominal Ollama decisions were `transport_error` rule fallbacks because
  the local endpoint was not frozen. This run is diagnostic, not a four-method
  model comparison.

The fix normalizes `dataset_size_gb` consistently in the Hub form path, posts
the exact confirmed recommendation fields, classifies the workload runner's
inner `TimeoutError`, exposes explicit Stage C Ollama CLI settings, and aborts
preflight if an Ollama representative falls back.

## Claim gate and remaining run

RQ4 is **PARTIALLY CLAIMABLE**. The validation establishes real spawn and
workload behavior across all planned cells, but the preregistered confirmatory
design is four methods × eight families × ten runtime repeats (320 trials).
That full plan exists at `results/v4-stage-c-plan-20260812T095453Z` and was not
executed automatically. Runtime and resource comparisons from one repeat must
remain descriptive.

## Reproduction

With the disposable namespace prepared and the Hub port-forwarded to 18000:

```bash
.venv/bin/python -m evaluation_v4.run_system \
  --plan results/v4-stage-c-validation-plan-20260812T102652Z/system-plan.jsonl \
  --experiment-id protocol-v4-stage-c-validation-<timestamp> \
  --context orbstack --hub-url http://127.0.0.1:18000 \
  --ollama-endpoint http://127.0.0.1:11434/api/chat \
  --ollama-model llama3:latest \
  --ollama-prompt-version prompt-v4.1.0 \
  --ollama-temperature 0 --ollama-timeout 60 \
  --output results/v4-stage-c-validation-<timestamp> --execute
```
