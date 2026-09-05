# Protocol-v5 benchmarks

The unified thesis claim registry is
`protocol-v5-claim-registry-v1.1.yaml`, validated by its adjacent JSON Schema.
The v1.0 registry and evaluated-claim schema remain unchanged so prior immutable
analysis packages keep their original checksum-bound contracts.
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
external custody; it is supplied only through the explicit, freeze-gated
confirmatory loader.
Development code must not copy it into this directory, repository caches,
candidate indexes, results, wheels, or container images.

## E4 resource-efficiency contracts

`resource-efficiency-inputs-v1.yaml` mechanically binds recommendation inputs
to the 16 existing frozen resource workload instances without labels, oracle
data, or code hints. `resource-efficiency-freeze-contract-v1.yaml` registers the
four allocation conditions, ten paired repetitions, catalog table, dynamic
policy, counterbalanced execution-order algorithm, explicit Pareto objectives,
contrasts, and required oracle/image bindings. No success noninferiority margin
is registered; one must not be introduced after observing results. The checked-in contract
is deliberately `NOT_FROZEN`. `resource-efficiency-capacity-v1.yaml` likewise
contains no invented allocatable capacity and remains `NOT_FROZEN` until it is
verified against the sole eligible node. It permits only Kubernetes node-status
`allocatable` values, never raw physical capacity, and labels every result
`SIMULATED_CAPACITY` / `SIMULATED_DETERMINISTIC_REQUEST_PACKING`.
