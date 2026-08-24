# Protocol-v5 P2/P3 component scoring

Status: harness implemented; real component analysis `NOT_EXECUTED`

This analyzer explains why a completed P2 or P3 recommendation succeeded or
failed. It consumes preserved Protocol-v5 offline evidence and independently
reviewed gold labels after recommendation execution. It never invokes a
recommender, changes backend configuration, or releases gold labels across the
offline adapter boundary.

## Inputs and isolation

Development analysis accepts either a frozen
`protocol-v5-gold-family-v1.0.0` authoring dataset or a compiled
`protocol-v5-split-bundle-v2.0.0`. Confirmatory analysis accepts only an
external compiled v2 split supplied with the authoritative pre-data freeze.
The existing isolation loader verifies the freeze and contamination boundary
before the analyzer reads the sealed split.

Before scoring, the analyzer runs the Prompt-5 evidence validator and requires
exact case, family, variant, prompt, input, catalog, and corpus identity joins.
The full gold and raw evidence coverage must match. A v1 split with partial or
null extraction labels is not silently treated as full component gold.
The Prompt-5 runner and validator preserve their existing raw record schema by
projecting compiled v2 labels into the legacy metric-input fields; component
scoring itself always reads the complete v2 labels, including three-way
feasibility and all StructuredIntent targets.

Development execution:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v5.analysis.component_scoring \
  --evidence-dir /path/to/prompt-5-run \
  --gold-dataset /path/to/frozen-development-gold.yaml \
  --output-dir /path/to/new-component-analysis
```

Confirmatory execution additionally requires explicit custody inputs:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v5.analysis.component_scoring \
  --role confirmatory \
  --evidence-dir /external/path/to/prompt-5-run \
  --gold-dataset /external/path/to/v5-confirmatory.yaml \
  --freeze results_v5/protocol-v5.0.0/freezes/<freeze-id>/freeze-manifest.json \
  --output-dir /external/path/to/new-component-analysis
```

Because complete real Prompt-3 gold and Prompt-5 evidence are not tracked, the
current repository state can be recorded without manufacturing metrics:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v5.analysis.component_scoring \
  --status-only \
  --output-dir /tmp/protocol-v5-component-not-executed
```

This writes only `analysis-manifest.json` with `status: NOT_EXECUTED`,
`claims_permitted: false`, and `p3_headroom_gate_status: NOT_EXECUTED`. Missing,
incomplete, or identity-inconsistent scoring inputs produce the same fail-closed
package. `NOT_EXECUTED` means no empirical metrics were computed: it is never
represented by zero-valued recall, MRR, extraction, constraint, ranking, or P3
gate results.

## Metric definitions

Extraction uses strict `StructuredIntent` contract validation. Prompt-3 feature
labels represent environment capabilities, so required and preferred feature
predictions include the corresponding StructuredIntent library sets. Required
and preferred frameworks remain separate. Required, preferred, and forbidden
feature precision/recall/F1 are never collapsed into one score. Empty gold and
prediction sets have undefined precision/recall/F1 rather than an invented
perfect score; exact match is reported separately.

GPU semantics use exact categorical equality. CPU and memory minima use exact
numeric equality after numeric normalization, with absent, spurious, omitted,
and value-mismatch outcomes. Ambiguity scoring is binary because human gold
ambiguity notes and generated diagnostic text are not lexically equivalent
labels.

Retrieval metrics use `candidate_top_k`, before hard filtering. Binary
relevance is membership in the acceptable-candidate set. The default K values
are 1, 3, and 5. `RETRIEVAL_MISS` means that no acceptable candidate occurs
anywhere in the complete retrieved Top-K for a feasible request.

Constraint scoring reuses the gold authoring validator's frozen catalog
feasibility rule. This supplies a gold-intent feasibility oracle without
changing backend semantics. It measures selected hard violations, false
rejection of retrieved acceptable candidates, infeasible-request detection,
infeasible candidate survival, and unsupported-requirement handling.

## Error attribution and aggregation

Every failed P2 and P3 recommendation receives exactly one primary category:

- `EXTRACTION_ERROR`
- `RETRIEVAL_MISS`
- `CONSTRAINT_ERROR`
- `RANKING_ERROR`
- `UNSUPPORTED_CATALOG`
- `PROVIDER_FAILURE`
- `OTHER`

The primary category is the earliest evidenced failure. All other observations
remain as secondary tags. `RANKING_ERROR` requires an acceptable candidate to
have been retrieved and remain feasible while final Top-1 is unacceptable.
Successful P2 and P3 recommendations use `primary_category: null`, even when an
internal provider failure recovered through a successful deterministic
fallback. A P3 provider failure is primary when it causes an otherwise
unexplained reranking failure, but remains secondary when an earlier frozen P2
extraction, retrieval, or constraint failure already causally explains the
outcome. Secondary diagnostics cannot duplicate the primary category.

Executions are first averaged within stochastic repeats, then variants, then
families. Repeats are stability observations, variants collapse within workload
families, and eligible families are the independent macro and P3-gating unit.
Execution-level rows remain diagnostic observations and are not treated as
additional accuracy samples. Each `per-family.jsonl` row retains explicit variant summaries,
repeat indices/counts, repeat-level category counts, and family-weighted stage
diagnostics so stability observations remain inspectable without inflating the
independent sample count.

The development-only P3 headroom gate defaults to the predeclared threshold:
at least `max(3, ceil(5% of eligible families))` ranking-error families and a
ranking-error rate of at least 5% among eligible families. Thresholds are
configurable for reporting, but the result is advisory and never changes the
backend, freeze, or P3 enablement. Confirmatory analysis reports
`NOT_APPLICABLE_CONFIRMATORY` for this gate.

## Output package

The output directory is created exclusively and is never overwritten:

```text
analysis-manifest.json
aggregates.json
per-recommendation.jsonl
per-family.jsonl
p3-headroom-gate.json
```

The manifest binds raw and gold checksums, Git revision, protocol and schema
versions, backend/catalog identities, analysis configuration, environment, and
every derived output checksum. Raw Prompt-5 evidence is never modified.
