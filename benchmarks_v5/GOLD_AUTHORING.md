# Protocol-v5 gold-dataset authoring

Protocol-v5 gold data is authored as workload families. A family is the
independent semantic unit; its language, paraphrase, noise, ambiguity, and
code-context variants are repeated cases within that unit.

The source contract is
`protocol-v5-gold-family-v1.schema.json`. Runtime validation is stricter than
JSON Schema alone: it checks duplicate keys, finite resources, administrator
catalog identities, candidate/profile/image consistency, feasibility, global
variant IDs, executable workload references, and cross-record review state.
`executable_workload_id`, `source_provenance`, and variant `code_context` may
be omitted when they do not apply. No language or perturbation class is
mandatory; every family only needs at least one human-authored variant.
Use a stable non-identifying reviewer code in `reviewed_by`; do not record raw
participant or reviewer personal information in dataset artifacts.

## Authoring workflow

Validate a YAML or JSON source file:

```bash
.venv/bin/python -m evaluation_v5.gold_dataset validate /path/to/gold.yaml
```

Inspect deterministic coverage and the redaction-safe human-review report:

```bash
.venv/bin/python -m evaluation_v5.gold_dataset summary /path/to/gold.yaml
.venv/bin/python -m evaluation_v5.gold_dataset review /path/to/gold.yaml
```

The review command returns exit status `1` when label work remains. Pending
semantic equivalence, unassessed difficulty, and unapproved family labels are
blocking. Singleton strata, declared category gaps, duplicate text, and
profile/image imbalance are advisory. Advisory findings remain in the report
but do not prevent compilation.

### Lifecycle and review state

The normative dataset lifecycle is `draft -> reviewed -> frozen`; transitions
are manual edits by an authorized researcher or custodian. The tooling does
not promote lifecycle state, invent a timestamp, or identify a reviewer.

- `draft` may contain unassessed difficulty, pending equivalence, and pending
  family review. It can be validated, summarized, and reviewed, but not
  compiled.
- `reviewed` records that authoring review has occurred but is deliberately
  not frozen and cannot compile.
- `frozen` requires non-null `freeze_metadata.frozen_at_utc` and
  `freeze_metadata.frozen_by`, with freezing no earlier than creation.
  Compilation also independently revalidates live catalog identity and
  requires zero unresolved-label findings.

An approved `label_review` requires a nonblank reviewer code, a valid UTC
timestamp ending in `Z`, and at least one nonblank review note. To freeze a
reviewed dataset, a custodian manually changes `lifecycle` to `frozen` and
adds both freeze fields. No separate freeze command exists, and `compile`
never changes the source document.

### Variant equivalence states

- `canonical_reference` identifies at most one reference variant in a family;
- `reviewed_equivalent` is a human-reviewed semantic equivalent;
- `pending_review` is unresolved and always blocks compilation; and
- `controlled_ambiguity` is deliberate, reviewed ambiguity. It is valid only
  with `expected_feasibility: ambiguous`, at least one explicit structured
  ambiguity note, and an approved family review. It remains visible as an
  advisory finding but does not block compilation.

An ambiguous feasibility label is therefore not itself an error. Ambiguity
with pending family review remains blocking.

### Coverage denominators

Workload family is the independence unit. Profile, image, and capability
primary coverage counts are family-level; review-policy imbalance thresholds
divide by the number of families that define at least one preferred label of
that type. Adding 20 variants to one family cannot increase that family's
weight in an imbalance threshold. Summary `case_counts` report the projected
case-level profile/image exposure separately. Capability case exposure is
reported as `capability_case_coverage`; language counts are case-level with
`language_family_counts` alongside them. Workload strata and perturbations
report both family and case counts.

Duplicate findings distinguish exact and normalized matches within a family
from matches across different families. All are advisory coverage/review
signals and use stable machine-readable finding codes.

After humans approve every family and manually set the dataset lifecycle and
freeze metadata, compile the flat evaluator projection:

```bash
.venv/bin/python -m evaluation_v5.gold_dataset compile \
  /path/to/gold.yaml --output /path/to/split.yaml
```

Compilation emits `protocol-v5-split-bundle-v2.0.0` and never invents labels,
text, timestamps, reviewers, or freeze metadata. It refuses to overwrite an
existing file. Confirmatory source and output paths must both be absolute and
outside this repository both lexically and after filesystem resolution.
Repository-local symlinks are rejected even if they point outward; external
paths or symlink chains that resolve into the repository are also rejected.

Canonical identity is SHA-256 over normalized finite JSON data with sorted
object keys, UTF-8 text, compact separators, and JSON distinctions between
numbers and null. YAML comments and mapping-key order do not affect it. Lists
(including family and variant order) are ordered, and `1` is distinct from
`1.0`. Omitted optional fields normalize to their explicit null or empty-list
form. Equivalent YAML and JSON documents normalize to the same canonical
identity, while their source-file SHA-256 values normally differ. Compilation
adds no clock or random state; case IDs equal the immutable `variant_id` and
retain `dataset_id` and `family_id` provenance.

## Protocol-v4 migration

Importing v4 is an aid for development authoring only:

```bash
.venv/bin/python -m evaluation_v5.gold_dataset import-v4 \
  benchmarks/intent-gold-v4.yaml \
  --source-split development \
  --output /tmp/v5-development-draft.yaml
```

The importer defaults to the v4 development split. `--source-split test` or
`all` must be explicit, but the result is still classified
`historical_formative_development_only`. Imported difficulty is `unassessed`,
family review is pending, and every non-canonical variant remains
`pending_review`. The command does not generate paraphrases or declare them
semantically equivalent.

Candidate IDs are formed only by the documented Cartesian combination of
explicit v4 acceptable/preferred profile and image labels. If an historical
component no longer resolves in the administrator corpus, import fails
atomically with the unresolved candidate ID. It does not skip the case or
invent a replacement; the original v4 source remains the preserved historical
record and no draft output is written.

## Confirmatory custody

No confirmatory family source or compiled bundle belongs in this repository,
its caches, indexes, images, archives, or ordinary results. A custodian may run
the generic authoring tools against external files. Final confirmatory loading
still requires the independent pre-data freeze and isolation gate documented
in `docs/evaluation/PROTOCOL_V5_DATA_ISOLATION.md`.
