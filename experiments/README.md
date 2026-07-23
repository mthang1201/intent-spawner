# Local Experiment Data Guide

This guide explains the local synthetic experiment directory, raw-record
lifecycle, resume behavior, derived summaries, and evidence-protection rules.

Start with the [main README](../README.md) and
[Getting Started](../docs/GETTING_STARTED.md) for setup and runnable examples.
See [Architecture](../docs/ARCHITECTURE.md) for the complete data flow.

## Why This Guide Is Outside `raw/`

`experiments/raw/README.md` is included in both the current raw SHA-256 manifest
and the historical pre-audit manifest. It remains byte-for-byte unchanged so
the audit baseline stays valid.

This file is the maintained newcomer guide. The actual preserved experiment
records are also unchanged.

## What Creates an Experiment Directory

The recommended entry point is:

```bash
.venv/bin/python -m experiments.runner
```

The lower-level single-record entry point is:

```bash
.venv/bin/python -m experiments.recorder
```

The runner creates a unique directory for smoke, dry-run, selected, and
full-matrix experiments. New directories are ignored by Git unless they are
explicitly reviewed and force-added.

## Directory Layout

A completed local experiment has this shape:

```text
experiments/raw/<experiment-id>/
├── environment.json
├── matrix.jsonl
├── results.jsonl
└── <run-id>/
    └── <workload-id>/
        ├── workload_stdout.jsonl
        └── workload_stderr.txt
```

| File | Meaning |
| --- | --- |
| `environment.json` | Sanitized experiment identity, Git commit, runner settings, and environment metadata |
| `matrix.jsonl` | One planned workload/method/repeat combination per line |
| `results.jsonl` | One normalized, schema-validated attempted result per line |
| `workload_stdout.jsonl` | Workload-emitted metadata captured before normalization |
| `workload_stderr.txt` | Workload stderr, including empty output |

A dry-run contains `environment.json` and `matrix.jsonl` but no executed
workload results. A smoke run normally contains one matrix item and one result.

The lower-level recorder can also retain sanitized Kubernetes supporting
artifacts. The main preserved Kubernetes evaluation corpus is separate and
lives under `results/cluster/raw/`.

## Append-Only Raw Evidence

- Never edit or reorder existing `results.jsonl` lines.
- Never replace a failed attempt with a successful attempt.
- Preserve timeouts, missing measurements, errors, and cleanup failures.
- Store corrections as new runs with new identifiers.
- Keep the original matrix and environment with their results.
- Write summaries and figures outside the raw directory.

Resume mode respects these rules:

```bash
.venv/bin/python -m experiments.runner \
  --resume \
  --experiment-dir experiments/raw/<experiment-id> \
  --environment-id <same-environment-id>
```

It skips only combinations already represented in `results.jsonl`. Do not
change the manifest, matrix, method definitions, or environment identity
mid-run.

## Preserved Local Snapshots

| Snapshot | Purpose |
| --- | --- |
| `20260719T140417Z-smoke-171688c0` | One local smoke result |
| `20260719T140423Z-matrix-783b4141` | Full-matrix dry-run planning evidence |
| `20260719T140431Z-matrix-aed48949` | 180-record local synthetic matrix used for the committed analysis |

These directories are committed evidence, not disposable output.

## Inspect a Run

Count planned and completed records:

```bash
wc -l \
  experiments/raw/<experiment-id>/matrix.jsonl \
  experiments/raw/<experiment-id>/results.jsonl
```

Format the environment metadata for inspection:

```bash
.venv/bin/python -m json.tool \
  experiments/raw/<experiment-id>/environment.json
```

Inspection must not rewrite line endings or JSONL formatting.

## Create a Derived CSV

```bash
.venv/bin/python -m experiments.runner \
  --aggregate \
  --experiment-dir experiments/raw/<experiment-id> \
  --csv-out experiments/summaries/<experiment-id>.csv
```

The CSV is a derived view and does not replace `results.jsonl`. See
[Derived Experiment Summaries](summaries/README.md) for exporter and overwrite
behavior.

## Produce Full Analysis Outputs

For a new run:

```bash
.venv/bin/python -m experiments.analyze_results \
  --experiment-dir experiments/raw/<experiment-id> \
  --results-dir /tmp/intent-spawner-results \
  --results-md /tmp/intent-spawner-results/RESULTS.md \
  --overwrite
```

To reproduce the committed local analysis:

```bash
.venv/bin/python -m experiments.analyze_results \
  --experiment-dir experiments/raw/20260719T140431Z-matrix-aed48949 \
  --results-dir /tmp/intent-spawner-results \
  --results-md /tmp/intent-spawner-results/RESULTS.md \
  --environment-report results/environment-capability.json \
  --overwrite
```

Analysis reads raw records and writes derived tables, figures, and Markdown. It
must never rewrite raw JSONL.

## Prohibited Data

Do not store or commit:

- raw notebook contents or pasted user code;
- raw datasets or rows;
- secrets, tokens, kubeconfigs, or credentials;
- usernames or longitudinal user identifiers;
- broad environment dumps;
- full unsanitized Kubernetes objects; or
- node names, UIDs, and metadata outside the documented allowlist.

Use declared benchmark intent, dataset-size hints, derived context signals,
allowlisted recommendation metadata, and sanitized resource evidence only.
Read [Data Governance](../docs/DATA_GOVERNANCE.md) for the complete policy.

## Cleanup

List new ignored experiment outputs:

```bash
git status --short --ignored \
  experiments/raw \
  experiments/summaries
```

Remove only exact generated paths that you created. Do not use wildcards and do
not delete the entire raw directory. Follow [Cleanup](../CLEANUP.md) for
target-resolution examples and the complete protected-evidence list.
