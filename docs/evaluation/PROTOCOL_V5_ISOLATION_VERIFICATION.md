# Protocol-v5 Isolation Adversarial Verification

Date: 2026-08-22

Baseline implementation commit: `3ce9f04d940c78fff011231eb2609180421ffd2d`

Status: infrastructure verification complete; confirmatory experiment
`NOT_EXECUTED`

## Scope

This follow-up verifies the split-isolation boundary with miniature synthetic
fixtures only. It creates no real confirmatory dataset, production freeze,
recommendation result, participant record, or cluster observation. It does not
change Protocol-v4 data/evidence or recommender behavior.

## Executable verification map

The focused tests in `tests/test_evaluation_v5_isolation.py` demonstrate:

1. The public confirmatory CLI exits with status 2 when the external dataset is
   absent, when the freeze source is absent, and when a repository-owned bundle
   is supplied as the dataset. Both complete CLI input and complete environment
   input are supported; mixing the two custody channels is rejected.
2. A supplied external dataset is not resolved, inspected, or opened before an
   authoritative production freeze passes validation.
3. A planted confirmatory bundle is never discovered by development preflight
   or catalog-only index construction. The isolation audit rejects synthetic
   material planted under benchmark-sealed, pytest-fixture, cache, index, and
   result paths; synthetic wheels; Docker-context TARs; and opaque OCI-style
   layer archives. Every Dockerfile uses a default-deny Dockerfile-specific
   context and explicit `COPY` sources, while runtime packaging remains an
   allowlist.
4. Overlapping case IDs, forbidden family IDs, exact prompts, and normalized
   prompts each produce their specific blocking category.
5. Lexical similarity produces redacted review pairs but never becomes a
   blocking category by itself, even when the review threshold is `0.0`. No
   embedding threshold decides admissibility.
6. A synthetic production-freeze fixture requires a clean 40-character Git
   revision; P1/P2/P3 versions; runtime-package checksum; catalog and corpus
   checksums; sparse, dense, and hybrid index schema/version/content checksums;
   P2/P3 prompt hashes; deterministic constraint/ranking configuration; full
   P2/P3 configuration; and canonical plus file checksums for development data.
   Index identity is cryptographic metadata, not a pathname.
7. Changing the development bundle after freeze invalidates the freeze.
8. Changing effective P2/P3/provider configuration, prompt identity, or
   catalog candidate text invalidates the freeze. Catalog drift changes and is
   detected in every sparse/dense/hybrid index checksum.
9. Cross-split identity and prompt intersections are rejected in either
   comparison direction after a synthetic confirmatory bundle has been
   supplied.

Canonical-checksum tests additionally prove that mapping/YAML order is
irrelevant while list order, exact Unicode/string bytes after parsing, newline
content, and integer-versus-float representation remain significant. The full
canonicalization specification is in `PROTOCOL_V5_DATA_ISOLATION.md`.

## Public CLI failure evidence

Command:

```bash
.venv/bin/python -m evaluation_v5.offline.run --split confirmatory
```

Observed result: exit 2, `status: ERROR`, with an explicit requirement for
`--dataset` or `PROTOCOL_V5_CONFIRMATORY_DATASET`.

Command:

```bash
.venv/bin/python -m evaluation_v5.offline.run \
  --split confirmatory \
  --dataset /tmp/synthetic-v5-confirmatory.yaml \
  --freeze results_v5/protocol-v5.0.0/freezes/missing/freeze-manifest.json
```

Observed result: exit 2, `status: ERROR`, reporting that a prior production
freeze is required. The synthetic dataset path is not opened.

## Commands and results

```bash
.venv/bin/python -m pytest -q tests/test_evaluation_v5_isolation.py
# 87 passed

.venv/bin/python -m pytest -q \
  tests/test_evaluation_v5.py tests/test_evaluation_v5_isolation.py
# 97 passed

.venv/bin/python -m evaluation_v5.isolation_audit
# PASS: 5,184 repository documents, 5 archives, 0 findings

bash scripts/check.sh
# unit/smoke: 161 passed
# repository gates: 22 passed, 0 failed, 2 intentionally skipped
```

The 87 count is the isolation-only suite. The 97 count adds the ten existing
Protocol-v5 architecture/manifest tests. The 161 count is broader: it also
includes recommender, configuration, reprovisioning, and Protocol-v4 tests.

The two skipped `scripts/check.sh` steps are optional live-cluster inspection
and cluster-mutating demonstrations. They were deliberately not enabled and
their absence is not treated as Protocol-v5 evidence.

## Interpretation and limitation

These checks support the acceptance property for ordinary repository and test
workflows: development code has no default route to sealed labels, and the
public confirmatory route fails closed until both freeze and external custody
requirements are met.

They do not prove that an external custodian never disclosed a file, prevent a
host user with direct filesystem access from deliberately bypassing the public
interfaces, or establish that 18 visible cases are a sufficient tuning corpus.
The isolation boundary prevents sealed-set leakage; it does not prevent
overfitting to visible development material.
