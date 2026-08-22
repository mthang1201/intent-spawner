# Protocol-v5 benchmarks

This directory contains only the visible Protocol-v5 development split and its
machine-readable schema. It must never contain final confirmatory cases or
labels.

## Tracked development data

`v5-development.yaml` contains 18 historical/formative development cases in 10
workload families:

- the 12 `development` cases from `benchmarks/intent-gold-v4.yaml`; and
- all six cases from `benchmarks/p2-infeasible-supplement-v1.yaml`.

No Protocol-v4 `test` case is copied into this bundle. Every case retains its
source dataset ID, schema version, case ID, source split, original provenance,
and the classification `historical_formative_development_only`.

The bundle uses `protocol-v5-split-bundle-v1.0.0`; its structural contract is
`protocol-v5-split-bundle-v1.schema.json`. Runtime validation additionally
enforces cross-record invariants that JSON Schema cannot fully express: unique
case IDs, sorted and exact family IDs, exact case/family counts, gold
consistency, and the canonical checksum.

## Checksum scope

The `split_manifest.checksum` is the lowercase SHA-256 digest of the complete
parsed bundle after removing only `split_manifest.checksum`. The remaining
object is encoded as UTF-8 JSON with Unicode preserved, object keys sorted, and
separators `,` and `:` without extra whitespace. YAML formatting, comments, and
mapping order therefore do not affect the canonical digest.

## Confirmatory data boundary

There is deliberately no `v5-confirmatory` file or test fixture here. A
confirmatory split must use the same schema but remain under external custody;
it is supplied only through the explicit, freeze-gated confirmatory loader.
Development code must not copy it into this directory, repository caches,
candidate indexes, results, wheels, or container images.
