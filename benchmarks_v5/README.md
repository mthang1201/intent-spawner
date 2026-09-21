# Protocol-v5 benchmarks

The unified thesis claim registry is
`protocol-v5-claim-registry-v1.2.yaml`, validated by its adjacent JSON Schema.
The v1.0 and v1.1 registries and evaluated-claim schemas remain unchanged so
prior immutable analysis packages keep their original checksum-bound
contracts. v1.2 is a versioned amendment (see its `amendments:` log) that adds
descriptive/exploratory P1_CATALOG comparator metrics to H5; it does not alter
any confirmatory verdict already decided under v1.1.
Evidence-selection locks, future E5 storage observations, and optional frozen
P3 overhead thresholds have separate versioned schemas. See
`docs/evaluation/PROTOCOL_V5_RESEARCH_ANALYSIS.md` for the read-only discovery,
adjudication, provenance, and report-generation workflow.

This directory contains only the visible Protocol-v5 development split,
machine-readable schemas, and authoring documentation. It must never contain
final confirmatory cases or labels.

Family-oriented authoring is documented in `GOLD_AUTHORING.md`. The repository
tracks its source schema and the v2 compiled-bundle schema, but deliberately
tracks no family-authored gold dataset or generated confirmatory material.

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

The existing tracked development bundle remains on v1. A manually frozen,
human-reviewed family dataset may compile to
`protocol-v5-split-bundle-v2.0.0`, which retains the same manifest/checksum
rules while preserving complete structured-intent, candidate, profile, image,
policy, and family metadata in each flat evaluator case.

## Confirmatory data boundary

There is deliberately no `v5-confirmatory` file or test fixture here. A
confirmatory split must use a supported split-bundle schema but remain under
external custody; it is supplied only through `evaluation_v5.isolation`'s
confirmatory loader, which enforces the external-custody boundary and runs
contamination checks against the development split. There is no separate
freeze-manifest gate — the loader's isolation checks are the whole boundary.
Development code must not copy it into this directory, repository caches,
candidate indexes, results, wheels, or container images.

## E4 resource-efficiency contracts

`resource-efficiency-inputs-v1.yaml` mechanically binds recommendation inputs
to the 16 existing resource workload instances without labels, oracle data, or
code hints. It registers the four allocation conditions, ten paired
repetitions, catalog table, dynamic policy, counterbalanced execution-order
algorithm, explicit Pareto objectives, and contrasts. No success
noninferiority margin is registered; one must not be introduced after
observing results. `resource-efficiency-capacity-v1.yaml` contains no invented
allocatable capacity and remains `NOT_VERIFIED` until it is checked against
the sole eligible node. It permits only Kubernetes node-status `allocatable`
values, never raw physical capacity, and labels every result
`SIMULATED_CAPACITY` / `SIMULATED_DETERMINISTIC_REQUEST_PACKING`.
