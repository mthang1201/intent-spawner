# Protocol-v5 Data Isolation

Protocol version: `5.0.0`

Split-bundle schema: `protocol-v5-split-bundle-v1.0.0`

Freeze schema: `protocol-v5-freeze-v1.0.0`

Status: isolation and preflight harness implemented; confirmatory evaluation
`NOT_EXECUTED`

Adversarial verification:
[PROTOCOL_V5_ISOLATION_VERIFICATION.md](PROTOCOL_V5_ISOLATION_VERIFICATION.md)

## 1. Purpose and security property

Protocol-v5 separates material used to build or tune the systems from material
used for final confirmation:

- `v5-development` is visible, tracked formative material. It contains 18
  cases in 10 workload families: the 12 Protocol-v4 development cases from
  four families plus six P2 diagnostic families. It contains no Protocol-v4
  test cases.
- `v5-confirmatory` is an externally held, sealed bundle. No real
  confirmatory case or label is stored in this repository.

An ordinary checkout is sufficient for development preflight but insufficient
to open confirmation data. Confirmation requires both an authoritative freeze
created before data supply and an explicit external dataset path. This is a
fail-closed boundary against accidental use by normal development, test,
packaging, cache, and index workflows. It does not authorize or execute a
recommendation experiment.

Protocol-v4 evidence remains historical/formative evidence. Copying its
development cases into the visible v5 development bundle does not make them
new Protocol-v5 observations.

## 2. Threat and custody model

Before an authoritative freeze, backend developers and tuning code may read
the tracked `benchmarks_v5/v5-development.yaml` and other explicitly formative
historical material. No Protocol-v5 confirmatory split is among that material.
A data custodian must retain the confirmatory bundle outside the repository,
its worktrees, build contexts, developer caches, and generated indexes. The
custodian must not supply a path, mount, copy, archive, or environment variable
containing the sealed material until the freeze artifact has been created.

The intended sequence is:

1. Develop and tune P1/P2/P3 using visible development material only.
2. Decide the development-only P3 gate.
3. Commit all relevant code and configuration, obtain a clean worktree, and
   create the authoritative freeze.
4. Only after freeze creation, allow the custodian to supply the external
   confirmatory bundle.
5. Run the confirmatory preflight. Resolve any custody or contamination
   failure without tuning against the sealed labels.
6. Execute a separately specified confirmatory experiment only after the
   preflight succeeds.

The loader verifies the freeze and recomputes its repository/configuration
snapshot before it resolves or opens the sealed dataset. External paths are
rejected if either their lexical location or resolved target is inside the
repository. A symlink does not turn an in-repository file into an acceptable
external dataset.

Reserved names such as `.protocol-v5-private/` and
`benchmarks_v5/sealed/` are leak guardrails, not approved custody locations.
All real confirmatory material must remain outside the repository.

## 3. Split-bundle contract and checksums

A bundle has exactly three top-level keys:

- `schema_version` — `protocol-v5-split-bundle-v1.0.0`;
- `split_manifest`; and
- `cases`.

`split_manifest` has exactly these fields:

- `dataset_id`, `split_id`, and `role` (`development` or `confirmatory`);
- sorted `family_ids`, plus `case_count` and `family_count`;
- `checksum`;
- `creation_metadata` with `created_at_utc` and `created_by`; and
- `freeze_metadata` with `frozen_at_utc` and `frozen_by`.

Each case has exactly `case_id`, `family_id`, `variant_id`, `language`,
`prompt`, `inputs`, `gold`, and `source_provenance`. `inputs` contains the
normalized `dataset_size_gb` and `code_context_hints` fields. `gold` contains
feasibility, preferred/acceptable candidate, capability, profile, GPU, and
expected-extraction labels. `source_provenance` identifies the source dataset,
schema, source case and split, and evidence classification. The tracked
development bundle additionally preserves the source record's original
provenance inside that mapping.

Validation is strict: unknown or missing fields fail in the bundle, manifest,
case, input, and gold mappings; source provenance permits additional source
detail but requires its five identity/classification fields. IDs must be safe;
timestamps must be UTC; authorities must be nonblank; case IDs must be unique;
manifest families must be sorted and equal the families derived from cases;
and the declared counts and checksum must match the content.

### Canonical bundle checksum

The manifest `checksum` is the lowercase SHA-256 of the complete parsed bundle
with only `split_manifest.checksum` removed. The remaining value is serialized
as UTF-8 JSON with Unicode preserved, keys sorted recursively, and compact
separators (`,` and `:`). Thus the checksum covers all cases, prompts, labels,
metadata, counts, and identities while remaining independent of YAML spacing,
comments, and key order.

The canonicalization is intentionally exact and reproducible:

- mapping order and YAML serialization details do not affect the digest;
- sequence order is preserved, so reordering cases or another list changes the
  digest;
- strings are hashed exactly after YAML parsing—there is no Unicode, newline,
  or internal-whitespace normalization of prompts or metadata;
- JSON numeric representation remains significant (`1` and `1.0` produce
  different canonical bytes); and
- the canonical JSON has no trailing newline. Its bytes are exactly the UTF-8
  encoding produced with `ensure_ascii=False`, `sort_keys=True`, and compact
  separators.

These checksum rules are separate from prompt normalization used only by the
contamination checker. Another implementation can reproduce the digest without
copying the YAML formatting.

The authoritative freeze records two distinct development digests:

- the canonical bundle checksum above, which identifies semantic parsed
  content; and
- the file SHA-256, which identifies the exact tracked YAML bytes.

Both are required and recomputed. The tracked development bundle currently has
canonical checksum
`18894b73ec98d895348498bf6b1c4dd4d2dc6004437202bd8b93c17d09b0dc0b`.
The machine-readable schema under `benchmarks_v5/` is the source of truth for
the complete structural contract.

This split-bundle schema is independent of, and does not change, the existing
`ProtocolV5Manifest` evidence schema.

## 4. Loader boundary and command behavior

Commands below use `python`; in this repository, `.venv/bin/python` is the
equivalent interpreter when the virtual environment is not activated.

Library callers use the role-specific public interfaces
`load_development_split()` and `load_confirmatory_split(dataset_path,
freeze_path, ...)`. The arbitrary file parser is private. The development
loader is fixed to the tracked bundle; the confirmatory loader always applies
the freeze, external-path, schema, and contamination gates.

### Development preflight

The normal repository command is:

```bash
python -m evaluation_v5.offline.run --split development
```

`--split v5-development` is an equivalent alias. It loads only the fixed
tracked development bundle. Development mode rejects `--dataset`, `--freeze`,
and either confirmatory environment variable before opening any external file.
It cannot be redirected to a private bundle. Supplying another expected
`--split-id` also fails against the fixed tracked identity.

### Confirmatory preflight

Confirmation requires explicit external material and an authoritative freeze:

```bash
python -m evaluation_v5.offline.run \
  --split confirmatory \
  --dataset /private/custody/v5-confirmatory.yaml \
  --freeze results_v5/protocol-v5.0.0/freezes/<freeze-id>/freeze-manifest.json
```

`--split v5-confirmatory` is an equivalent alias. The default expected split
ID is `v5-confirmatory`; `--split-id ID` supports a future externally supplied
split identity without weakening the same gate. A role or split-ID mismatch
fails. The confirmatory dataset path must be absolute; the freeze artifact path
may be explicit relative to the repository.

Instead of CLI paths, an operator may use:

```bash
export PROTOCOL_V5_CONFIRMATORY_DATASET=/private/custody/v5-confirmatory.yaml
export PROTOCOL_V5_FREEZE_ARTIFACT="$PWD/results_v5/protocol-v5.0.0/freezes/<freeze-id>/freeze-manifest.json"
python -m evaluation_v5.offline.run --split confirmatory
unset PROTOCOL_V5_CONFIRMATORY_DATASET PROTOCOL_V5_FREEZE_ARTIFACT
```

CLI and environment custody channels are mutually exclusive: if either input
is supplied on the CLI, neither environment variable may be present. There is
no hidden precedence. Missing dataset or freeze material also fails. Do not set
these variables in shell profiles, test configuration, CI defaults, or
developer `.env` files.

`--similarity-threshold` controls only which nonblocking lexical-review pairs
are reported and defaults to `0.90`. It is not a recommender or evaluation
threshold.

On success, the command emits safe JSON using
`protocol-v5-offline-preflight-v1.0.0`. It reports `status: NOT_EXECUTED`,
`claims_permitted: false`, split identities, counts and checksums, the freeze
ID for confirmation, and contamination counts/review pairs. It emits no raw
prompt, gold label, or external source path. It does not submit any case to P1,
P2, or P3, write predictions, create a result package, or make an empirical
claim. Freeze verification reconstructs catalog-derived P2 index metadata only
to check the frozen identity.

## 5. Freeze-before-supply gate

Create a rehearsal snapshot without writing an artifact:

```bash
python -m evaluation_v5.freeze \
  --freeze-id v5-freeze-rehearsal \
  --p3-gate-status not_retained \
  --dry-run
```

`--dry-run` prints a `DRY_RUN` snapshot, writes no artifact, and cannot unlock
the confirmatory loader.

After all development decisions are committed and the worktree is clean,
create the production freeze:

```bash
unset PROTOCOL_V5_CONFIRMATORY_DATASET PROTOCOL_V5_FREEZE_ARTIFACT
python -m evaluation_v5.freeze \
  --freeze-id v5-final-<UTC-or-preregistered-id> \
  --p3-gate-status not_retained
```

For a retained P3, use `--p3-gate-status retained`. The default gate-evidence
document is `docs/evaluation/P3_INCREMENTAL_EVALUATION_V1.md`; an explicit
pre-existing repository record may be selected with `--p3-gate-evidence PATH`.
Gate evidence must be a tracked regular file lexically and physically inside
the repository; an untracked/private file, external path, or repository symlink
that resolves outside is rejected before its contents are read.
When P2 uses its LLM extractor or retained P3 uses LLM reranking, the effective
provider environment must also be valid at freeze time; credentials are
required by the backend validator but are never serialized.
Production writes exclusively to the repository-owned authoritative namespace:

```text
results_v5/protocol-v5.0.0/freezes/<freeze-id>/freeze-manifest.json
```

`--output-root`, when supplied for command-wrapper compatibility, must resolve
to that exact root and cannot relocate an authoritative artifact. The writer
will not replace an existing freeze. Verification requires the lexical and
resolved path
`<authoritative-root>/<safe-freeze-id>/freeze-manifest.json`, rejects symlinked
or external paths before reading them, binds the manifest ID to its directory,
and reports unreadable or malformed artifacts without echoing their path.
Production freeze creation
refuses a dirty Git worktree and refuses to run while
`PROTOCOL_V5_CONFIRMATORY_DATASET` is set. A clean production artifact is the
only accepted authority and records `status: FROZEN`; a dry-run snapshot,
dirty or stale Git state, or changed frozen configuration fails confirmation
before the sealed file is opened.

The freeze records the Git revision and clean-state assertion; P1/P2/P3 and P3
gate identities; recommender package checksum; administrator catalog version
and checksum; candidate corpus plus dense, sparse, and hybrid index identities;
extractor/reranker prompt identities; deterministic retrieval, constraint,
ranking, and P2/P3 configurations; both development dataset checksums; and the
Python/platform environment identity. The index snapshot is derived from
administrator catalog content before sealed material is available. The
artifact contains no confirmatory path, dataset identity, checksum, prompt,
case, or label.

P2 and P3 configuration values are resolved through the same environment-aware
configuration constructors as the ordinary backends. For active provider-backed
stages, the snapshot also records every effective non-secret provider behavior
setting; the endpoint is represented only by SHA-256, and only credential
presence—not credential content—is recorded. A file-backed pricing configuration
must be a tracked repository-owned regular file and is identified by relative
path and checksum. Its free-form source-provenance value is represented only by SHA-256
so it cannot disclose an operator-local path. Changing a captured
P2/P3/provider setting or model identity after
freezing causes verification to fail before the sealed bundle is opened.
Deterministic ranking provenance includes both its version and its
retrieval/soft-preference weights.

## 6. Cross-split contamination checks

Before returning a sealed bundle, confirmation compares it with the tracked
development bundle and:

- rejects any repeated case ID;
- rejects any repeated family ID, because Protocol-v5 requires disjoint
  development and confirmatory workload families;
- rejects exact prompt duplicates; and
- rejects normalized prompt duplicates.

Prompt normalization applies Unicode NFKC normalization, case folding,
punctuation-to-space conversion, whitespace collapsing, and trimming. Symbols
remain significant so inputs such as `C++` versus `C` are not automatically
collapsed. This catches formatting and case changes without treating
paraphrases as distinct independent families merely because their surface form
changed.

For remaining cross-split pairs, `difflib.SequenceMatcher` scores at or above
the configured threshold are surfaced for human review. Similarity alone does
not reject a bundle. The report includes only case IDs, SHA-256 prompt
fingerprints, scores, and counts. Reviewers must adjudicate workload-family or
semantic leakage outside the output; they must not copy sealed text or labels
into issues, caches, tuning notes, or the repository. No embedding threshold
is used as an automatic semantic-contamination decision.

A hard overlap or duplicate is a custody/protocol failure, not a tuning
opportunity. Do not change prompts, retrieval parameters, candidate metadata,
thresholds, ranking weights, constraints, or P3 configuration in response to
the sealed case. Quarantine the supplied material and follow the preregistered
governance decision (for example, replace the sealed split under custodian
control or invalidate and re-freeze a new experiment) without inspecting its
labels for development.

## 7. Packaging, cache, result, and index controls

Confirmatory material must never enter Python wheels, Docker images, ordinary
test fixtures, development caches, result trees, or generated candidate
indexes.

The repository reserves/ignores these leak-prone namespaces:

- `.protocol-v5-private/`;
- `benchmarks_v5/sealed/` and `benchmarks_v5/v5-confirmatory.*`;
- `evaluation_v5/cache/` and `evaluation_v5/indexes/`; and
- `results_v5/protocol-v5.0.0/`.

Ignore rules reduce accidental commits; they do not make those paths approved
storage. Tests generate miniature synthetic confirmatory bundles only in
pytest-owned temporary directories and do not track a confirmatory fixture.

The root Docker context excludes `benchmarks_v5/`, `results_v5/`, `tests/`,
and private/cache/index namespaces. The cluster evaluation image copies only
`benchmarks/__init__.py`, `benchmarks/workload_runner.py`, and its own required
pod-runner package files. Its Dockerfile-specific context is default-deny and
allowlists only those inputs.

Candidate indexes must be built solely from the administrator catalog/corpus.
Prompts, cases, labels, and split data are not candidate text. A confirmatory
preflight must not change candidate-index identities or write sealed bytes to
cache or result locations.

Run the repository and archive audit with:

```bash
python -m evaluation_v5.isolation_audit
make v5-isolation-check
```

The normal `scripts/check.sh` path includes the same gate. The audit
semantically scans repository YAML, YML, JSON, JSONL, and text documents,
including structurally wrapped block/flow YAML and JSON, and signature-checks
other suffixes so padding or renaming a bundle does not bypass the gate.
External data-directory/file symlinks fail closed. It content-sniffs wheel,
ZIP, and TAR-family archives even when their suffix is opaque, and recursively
inspects bounded nested archive members without extracting or printing their
content. Inspect additional build products with repeatable arguments:

```bash
python -m evaluation_v5.isolation_audit \
  --archive /tmp/dist/package.whl \
  --archive /tmp/image-context.tar
```

Any detected confirmatory bundle fails the audit. The audit itself must not be
given the custodian's real sealed dataset; `--archive` is for distributable
artifacts whose cleanliness is being checked. Supplied archive paths and
basenames are redacted as synthetic indexes, findings contain no document
content, and unreadable, invalid, or oversized artifacts fail closed.

## 8. Operational checklist

Before data supply:

- confirm only `v5-development` is visible;
- run development preflight and the isolation audit;
- finish all prompt, retrieval, candidate, threshold, ranking, constraint, and
  P3 decisions using development material;
- record the P3 gate, commit the repository, and verify a clean worktree;
- create the immutable authoritative freeze and preserve it; and
- have the custodian verify that the freeze predates any data transfer.

After data supply:

- mount or reference the sealed bundle read-only outside the repository;
- run confirmatory preflight with explicit paths or ephemeral environment
  variables;
- retain only the safe preflight report and separately protect any operational
  logs or shell history that could reveal the external path;
- stop on any checksum, freeze, path, role, split, or contamination failure;
  and
- do not interpret `NOT_EXECUTED` as zero effect, failed accuracy, or evidence
  of system performance.

## 9. Limits of the controls

These controls prevent ordinary repository commands from accidentally loading
or packaging sealed labels. They cannot prove that an external custodian never
disclosed a file, defend against a developer deliberately bypassing Python
interfaces with host-level read access, erase secrets already copied into
shell history or backups, or inspect encrypted/proprietary archive formats.
Custody policy, filesystem permissions, audit logs, and human governance remain
necessary.

Lexical similarity is a review aid, not a semantic oracle. A low score cannot
prove that two workload families are independent, and a high score is not by
itself a rejection. Human review must apply the family-level protocol without
using sealed labels for development.

The visible 18-case, 10-family bundle validates Protocol-v5 infrastructure and
supports bounded formative work; it is not evidence that this corpus is
sufficient for unrestricted tuning. Split isolation prevents leakage from the
sealed set, but it cannot prevent overfitting to visible development material.
Any claim about tuning adequacy requires a separate design justification and
must not be inferred from the existence of the isolation controls.

Finally, a freeze records and verifies the captured repository and
configuration identity; it is not execution evidence or a cryptographic
signature from an external authority. Preserve and access-control the JSON
artifact accordingly. Real user responses, Kubernetes measurements, image
sizes, latency, resource use, accuracy, and statistical results remain
unavailable until separately and genuinely collected. Until then, Protocol-v5
confirmation remains explicitly `NOT_EXECUTED`.
