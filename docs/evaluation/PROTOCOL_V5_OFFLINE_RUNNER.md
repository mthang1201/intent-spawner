# Protocol-v5 Offline Recommendation Runner

Status: harness implemented; no confirmatory recommendation evidence executed

The offline runner generates raw E1 evidence for frozen P1 and P2. P1 is the
existing rule-based recommender. P2 is StructuredIntent extraction, hybrid
retrieval, deterministic constraints, and deterministic ranking. P3 belongs
to optional E6 and is available only when `--enable-p3` is paired with an
authenticated, recomputed `retained` development decision. B0 is excluded
because it is a manual human-selection baseline and does not produce an
offline ranking.

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
runner constructs an execution plan. It returns a source-bound
`VerifiedConfirmatorySplit`; the runner reopens and revalidates the split and
production freeze at the execution boundary. An ordinary/relabelled
`LoadedSplit`, a caller-supplied `freeze_identity`, or the tracked
`frozen-configuration.json` design snapshot cannot authorize confirmation.
Confirmatory provenance and configuration are derived from the reverified
artifacts, and any caller-provided configuration must match that production
freeze. Confirmatory adapter injection is prohibited: the runner constructs
the production adapters and compares their backend, pipeline, prompt/model,
index, retrieval, constraint/ranking, catalog, and corpus identities with the
freeze before building the matrix. Development smoke evidence under `/tmp` is
not confirmatory evidence and must not be moved into a final evidence
namespace.

## Repeats and P3

`--repeats` is a requested stability count, not an accuracy-sample multiplier.
Deterministic P1 and deterministic/local P2 execute exactly once per case.
Only an adapter declaring a stochastic or provider-dependent component receives
the requested repetitions. The provenance records `requested_repeats`, each
system's `effective_repeats`, and the repeat-policy partition. Repeated outputs
remain executions of the same workload family.

Selecting P3 without `--enable-p3` fails before evidence is created. For a
development run, `--p3-gate <decision.json>` is also required. Verification
reopens the canonical development split, raw recommendation package,
component package, statistical package, and gold; checks their recorded
digests and joins; recomputes component scoring and the fixed family-level
headroom predicate; and rejects a supplied decision that differs from that
result. For confirmation, the decision path and digest come only from the
verified production freeze. A `not_retained` gate always refuses P3 execution.

The decision schema is
`benchmarks_v5/protocol-v5-p3-development-decision-v1.schema.json`. The
currently tracked v1 development split does not supply the full v2 component
gold needed to produce a real decision package, so no real gate is created by
this repair; that evidence remains `NOT_EXECUTED` until the registered inputs
are available.

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
validator invokes the same isolation loader and re-verifies its capability at
the validation boundary. Freeze provenance is derived from that artifact, not
accepted as a caller-authored mapping. A confirmatory package is never
implicitly validated against the visible development split.

Downstream E5 code can call
`evaluation_v5.offline.verify_recommendation_run_provenance()` and retain the
returned immutable `VerifiedRecommendationRunProvenance`. It exports the
exact recommendation JSONL digest and record IDs together with source-bound
structured-intent schema, extractor/prompt, index, retrieval,
constraint/ranking, catalog, split, freeze, and Git identities. The v1.1 source
capability adds the structured-intent schema identity required by E5 without
changing recommendation execution. `reverify_recommendation_run_provenance()` reopens the
complete package and fails if any upstream artifact has changed. There is no
API that converts caller-retyped fields into this capability.
