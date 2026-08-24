# Protocol-v5 Offline Recommendation Runner

Status: harness implemented; no confirmatory recommendation evidence executed

The offline runner generates raw E1 evidence for frozen P1 and P2. P1 is the
existing rule-based recommender. P2 is StructuredIntent extraction, hybrid
retrieval, deterministic constraints, and deterministic ranking. P3 is
available only with the explicit `--enable-p3` gate and belongs to optional
E6. B0 is excluded because it is a manual human-selection baseline and does
not produce an offline ranking.

## Execution

The development split can be checked without calling a recommender or creating
an evidence directory:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v5.offline.runner \
  --split development \
  --systems P1,P2 \
  --repeats 5 \
  --seed 20260824 \
  --frozen-configuration <frozen-config.json> \
  --result-dir /tmp/protocol-v5-development-smoke \
  --dry-run
```

Remove `--dry-run` to execute. The frozen configuration must be a versioned
JSON object and must not contain credentials or secret-bearing fields.
Benchmark prompts are omitted from raw rows by default. They may be included
inside evaluation evidence with `--include-benchmark-prompts` only when the
dataset policy permits this; the runner never writes them to operational logs.

Confirmatory execution requires both an external sealed dataset and its
authoritative freeze artifact. The isolation loader verifies both before the
runner constructs an execution plan. Development smoke evidence under `/tmp`
is not confirmatory evidence and must not be moved into a final evidence
namespace.

## Repeats and P3

`--repeats` is a requested stability count, not an accuracy-sample multiplier.
Deterministic P1 and deterministic/local P2 execute exactly once per case.
Only an adapter declaring a stochastic or provider-dependent component receives
the requested repetitions. The provenance records `requested_repeats`, each
system's `effective_repeats`, and the repeat-policy partition. Repeated outputs
remain executions of the same workload family.

Selecting P3 without `--enable-p3` fails before evidence is created. Explicit
enablement only opens the runner gate; it does not establish that the P3
development gate has been passed or authorize confirmatory evaluation.

## Evidence layout and resume

Each new result directory is exclusive and contains:

```text
<result-dir>/
  raw/
    offline-run-provenance.json
    recommendations.jsonl
  report/
    offline-run-completion.json
```

The provenance fingerprint binds the exact dataset/split/freeze, full Git
revision and dirty state, environment, systems and versions, StructuredIntent
extractor/prompt identity, catalog/corpus, embedding and indexes, fixed
configuration, seed, prompt-storage policy, and requested/effective repeat
plan. Raw rows bind that fingerprint to an unambiguous case ID, family ID,
variant ID, dataset checksum, and input checksum.

Rows are append-only and fsynced. `--resume` reuses only a package with the
same fingerprint, skips durable logical rows, and refuses duplicates or
foreign rows. An unterminated final append is discarded as crash residue;
malformed durable lines fail closed. A completion marker is exclusive-created
only after every planned execution has either a completed row or a complete
error row. Raw completion explicitly forbids statistical claims.

## Validation

Validate a completed development package with:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v5.offline.validate_evidence \
  --dir /tmp/protocol-v5-development-smoke
```

The validator checks provenance and checksums, the exact execution matrix,
JSONL framing and schema, unique row-to-dataset joins, trusted candidate
references, system-specific traces, fallback/error records, latency fields,
completion counts, and the raw inputs required by the registered end-to-end
metrics. It emits only a PASS/FAIL integrity report; it does not calculate
aggregate metrics, confidence intervals, significance, or thesis claims.

Confirmatory validation must again supply `--dataset` and `--freeze`; the
validator invokes the same isolation loader. A confirmatory package is never
implicitly validated against the visible development split.
