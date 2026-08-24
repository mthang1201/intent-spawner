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

After humans approve every family and manually set the dataset lifecycle and
freeze metadata, compile the flat evaluator projection:

```bash
.venv/bin/python -m evaluation_v5.gold_dataset compile \
  /path/to/gold.yaml --output /path/to/split.yaml
```

Compilation emits `protocol-v5-split-bundle-v2.0.0` and never invents labels,
text, timestamps, reviewers, or freeze metadata. It refuses to overwrite an
existing file. Confirmatory source and output paths must both be absolute and
outside this repository.

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

## Confirmatory custody

No confirmatory family source or compiled bundle belongs in this repository,
its caches, indexes, images, archives, or ordinary results. A custodian may run
the generic authoring tools against external files. Final confirmatory loading
still requires the independent pre-data freeze and isolation gate documented
in `docs/evaluation/PROTOCOL_V5_DATA_ISOLATION.md`.
