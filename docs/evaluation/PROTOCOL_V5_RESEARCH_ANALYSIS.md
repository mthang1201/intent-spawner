# Protocol-v5 Unified Research Analysis

## Purpose and claim boundary

`evaluation_v5.analysis.research_analysis` is the Protocol-v5 thesis claim
registry, evidence-completeness checker, provenance gate, decision engine, and
report generator. It reads existing evidence packages and never runs a
recommender, changes backend behavior, mutates Kubernetes, or rewrites source
evidence.

The authoritative registry is
`benchmarks_v5/protocol-v5-claim-registry-v1.1.yaml`. It connects RQ1–RQ6 to H1–H8
and H7F, their evidence requirements, metrics, tests, directions, exact
decision predicates, and limitation boundaries. Observed outcomes are not
stored in the registry. They are materialized only in a versioned analysis
package.

Registry v1.1 adds frozen source-endpoint and independent-unit contracts, exact
metric lineage, explicit H5 reliability and H6 oracle-independence conjunctions,
and an H7 post-baseline catalog-growth criterion. The prior v1.0 registry and
evaluated-claim schema are retained byte-for-byte for validation of existing
immutable packages.

Only complete, validated, observed **confirmatory** evidence is eligible to
produce `SUPPORTED` or `NOT_SUPPORTED`. Development, Protocol-v4, dry-run,
synthetic, incomplete, missing, statistically withheld, and unretained-P3
evidence produces `NOT_EXECUTED`. A failed predicate is `NOT_SUPPORTED` only
when every required confirmatory input and test was available.

## Commands

Discover and validate candidates without writing an analysis package:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v5.analysis.research_analysis discover \
  --results-root results_v5/protocol-v5.0.0 \
  --freeze results_v5/protocol-v5.0.0/freezes/frozen-configuration.json
```

Generate an immutable package:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v5.analysis.research_analysis analyze \
  --results-root results_v5/protocol-v5.0.0 \
  --freeze results_v5/protocol-v5.0.0/freezes/frozen-configuration.json \
  --output-root results_v5/protocol-v5.0.0/analysis \
  --run-id research-analysis-YYYYMMDDTHHMMSSZ
```

If multiple eligible packages satisfy one evidence requirement, pass a YAML or
JSON selection lock with `--selection`. Every entry binds the repository-relative
package path to its exact manifest SHA-256, and the lock binds to the registry
SHA-256. A stale or invalid lock creates a `FAILED` audit package rather than
silently choosing evidence.

Without a lock, multiple decision-equivalent packages remain unresolved and
produce `NOT_EXECUTED`; conflicting decision signatures are an evidence failure
and exit 2. A valid lock may choose one exact checksum. The selection report
retains every candidate's registered and current checksum, content and decision
signatures, integrity errors, and selected/rejected/duplicate disposition. No
filesystem ordering, timestamp, filename, or mtime participates in selection.

Optional H8 also requires `--p3-threshold` pointing to a contract validated by
`protocol-v5-p3-overhead-threshold-v1.schema.json`. The file must have been
frozen before confirmatory evidence. Without a retained P3 gate and this
contract, H8 is `NOT_EXECUTED`.

Validate a generated package and all still-addressable input checksums:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v5.analysis.research_analysis validate \
  results_v5/protocol-v5.0.0/analysis/<run-id>
```

Exit codes are:

- `0`: valid package and every required claim is decided;
- `2`: invalid, contradictory, checksum-stale, or semantically inconsistent
  evidence; a failed audit package is written when the registry can be loaded;
- `3`: valid analysis, but at least one required claim is `NOT_EXECUTED`.

H8 is optional while P3 is not retained and does not by itself cause exit 3.

## Evidence selection and provenance

Discovery is schema-first. Zero eligible packages records missing evidence;
one is selected automatically; more than one requires a checksum-bound
selection. Every discovered package, including ineligible development and
dry-run packages, remains visible in `derived/evidence-inventory.json`.

Semantic identities are compared against the declared freeze and across
selected experiments. The comparison includes the applicable P1/P2/P3
pipeline identities, candidate corpus, indexes, prompt identities,
configuration, deterministic constraints, and ranker. A missing or changed
semantic identity blocks affected claims. Digest types are named explicitly,
so a catalog-file digest is never compared with a canonicalized catalog-object
digest.

E1 and E2 additionally require the same declared offline benchmark dataset and
split identities. Cross-experiment fields are compared only inside their
declared comparison group and namespace; unrelated E3, E4, and E5 dataset
digests are never equated.

Git revisions, dirty-tree state, runtime, platform, and cluster identities are
recorded and differences are disclosed as limitations. They cannot override a
semantic mismatch. Dataset identities are validated inside their own
experiment contracts; unrelated E1, E3, E4, and E5 datasets are not required
to have the same checksum.

The E3 adapter reads only the finalizer's aggregate analysis, status, manifest,
and privacy audit. It does not copy participant IDs, event logs, questionnaire
rows, free text, or other participant-level evidence into the unified package.

## Output package

Every run is created under a new directory and contains:

- `manifest.json`, `SHA256SUMS`, and `status.json`;
- evidence inventory, selection, completeness, provenance consistency, and the
  evaluated claim registry under `derived/`;
- metadata-triggered threats to validity under `report/`;
- claim matrices and thesis result tables in JSON, CSV, Markdown, and LaTeX
  under `tables/`.

Every decided claim contains exact source artifact paths and SHA-256s, evidence
and schema versions, machine-readable JSON/JSONL/YAML locators, selector
cardinality, normalized estimates and uncertainty, observed tests, and the
frozen all-of decision rule. The independent validator resolves every decision
field locator and rejects whole-file-only lineage. Thesis tables retain a
compact checksum-and-locator form of the same trace. A later change to a
referenced artifact makes validation fail. Missing values are rendered as
`N/A`, never as zero. B0 ranking metrics are prohibited by registry validation
because B0 does not create a ranking.

The threats generator emits a record only when a registry or experiment field
triggers it. Records include the category, affected claims, observed metadata,
source artifact, and JSON pointer. Categories cover construct, internal,
external, statistical conclusion, benchmark contamination, human-study
learning/order effects, single-cluster generalization, and image-platform
dependence.

## Current evidence status

The available Protocol-v5 tree contains development E1/E2 evidence, no
executed E3 study, dry-run E4 packages, development E5 functional evidence, no
E5 storage measurements, and an unretained P3 gate. Therefore a current-tree
analysis is expected to exit 3 with every claim `NOT_EXECUTED`. This is an
evidence result, not a software failure, and must not be rewritten as a zero
effect or a negative confirmatory finding.
