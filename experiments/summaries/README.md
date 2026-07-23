# Derived Experiment Summaries

This directory is the default location for simple CSV exports derived from
local raw JSONL records.

Read the [main README](../../README.md),
[Getting Started](../../docs/GETTING_STARTED.md), and
[Local Experiment Data Guide](../README.md) for the complete workflow.

## Raw Versus Derived

| Property | Raw experiment record | Derived summary |
| --- | --- | --- |
| Typical location | `experiments/raw/<experiment-id>/results.jsonl` | `experiments/summaries/<experiment-id>.csv` |
| Role | Append-only evidence | Convenient tabular view |
| May be regenerated | No | Yes |
| May replace the other form | No | No |
| Default Git behavior for new output | Ignored | Ignored |

A CSV summary is not the source of truth. Keep its source experiment identifier
and retain the raw directory for audit and reproduction.

## Create a Summary with the Runner

```bash
.venv/bin/python -m experiments.runner \
  --aggregate \
  --experiment-dir experiments/raw/<experiment-id> \
  --csv-out experiments/summaries/<experiment-id>.csv
```

The command:

1. reads `results.jsonl`;
2. validates and migrates records in memory as needed; and
3. writes a flattened CSV.

It does not execute workloads and does not modify the source JSONL.

## Create a Summary with the Exporter

The lower-level exporter is:

```bash
.venv/bin/python -m experiments.export_results_csv \
  --raw-jsonl experiments/raw/<experiment-id>/results.jsonl \
  --csv-out experiments/summaries/<experiment-id>.csv
```

Use this path when the raw JSONL location is already known and matrix
orchestration is not needed.

## Overwrite Behavior

Export commands refuse to overwrite an existing CSV unless `--overwrite` is
provided:

```bash
.venv/bin/python -m experiments.runner \
  --aggregate \
  --experiment-dir experiments/raw/<experiment-id> \
  --csv-out experiments/summaries/<experiment-id>.csv \
  --overwrite
```

Before overwriting:

- verify that the source experiment is the same;
- preserve any summary used in an external analysis;
- record changes to the schema or exporter; and
- never use overwrite options against raw JSONL.

## Full Analysis Outputs

The simple CSV export is different from the complete analysis pipeline.
`experiments.analyze_results` produces multiple tables, SVG figures, and a
Markdown report:

```bash
.venv/bin/python -m experiments.analyze_results \
  --experiment-dir experiments/raw/<experiment-id> \
  --results-dir /tmp/intent-spawner-results \
  --results-md /tmp/intent-spawner-results/RESULTS.md \
  --overwrite
```

Committed full-analysis outputs live under `results/` and
`docs/evaluation/RESULTS.md`.

The Kubernetes evaluation has a separate analysis path and writes under
`results/cluster/derived/`. Do not combine its measurements with local
summaries.

## Reproducibility Checklist

When sharing a derived summary, record:

- source experiment ID;
- source Git commit from `environment.json` or raw records;
- workload manifest version;
- schema version;
- export or analysis command;
- output filename; and
- whether any record was excluded and why.

Do not silently remove failed, timed-out, or policy-constrained rows.

## Cleanup

List generated summaries:

```bash
git status --short --ignored experiments/summaries
```

Delete only a verified generated CSV:

```bash
rm -f experiments/summaries/<exact-generated-experiment-id>.csv
```

Do not remove this README and do not delete the associated raw experiment as
part of summary cleanup. See [Cleanup](../../CLEANUP.md).
