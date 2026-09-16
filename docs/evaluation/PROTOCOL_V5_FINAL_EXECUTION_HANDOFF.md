# Protocol-v5 Final Execution Handoff

This is the safe public handoff for Protocol-v5 confirmatory execution. It
contains identities and field names only. It does not contain prompts, labels,
participant identities, private custody paths, or confirmatory cases.

## Authoritative identities

- Freeze ID: `v5-final-execution-freeze`
- Freeze artifact: `results_v5/protocol-v5.0.0/freezes/v5-final-execution-freeze/freeze-manifest.json`
- Freeze manifest SHA-256: `6228673d9459ab2f447c342adb17e0f3f60867c55a74ff98dac2291a01811b8b`
- `FROZEN_EXECUTION_SHA`: `0f73c0a2da34916df1ef6134cc898ce01512acff`
- Frozen execution tree: `d1f6c417c834ed77f344c330a0b09a36c5731873`
- `FREEZE_ARTIFACT_COMMIT_SHA`: `38877fa71d04f4caad30f253a7cbbb049ecda4f8`
- P3 decision: `not_retained`; `p3_active: false`;
  `claim_eligible: false`; evidence classification `HISTORICAL_FORMATIVE`.

`FROZEN_EXECUTION_SHA` is the tested code and configuration. The freeze
artifact commit is its immediate child and adds only the manifest. The latter
must never be represented as the code revision that was frozen.

## Integrated source revisions

The integration base was `origin/main` at
`78c4e991562b244760505bbb822185f68c21f3aa`. Only the following reviewed heads
were merged, in this order, with non-fast-forward merge commits:

| Work package | Reviewed source SHA | Integration merge commit |
| --- | --- | --- |
| Offline regeneration | `7d027614d65876405a513617a127779dffcd704d` | `f247aac45457880b070a8fca57bbe2454cba10d5` |
| E3 readiness integrity | `d597f81364e2664d875152ce8100220f378299a1` | `3d1950cd0b3bc0d5ad03f8138d02db08b4b5cba2` |
| E4 live readiness | `8425f5274eaf4b1568edfc091ee10fdb51ed0de3` | `6807bf5b17ac6faf6ecc82d3b46c8d1acdd7c13c` |
| E5 live readiness | `ebef7a26c5a086fdaa2af0b38c01ff2f167592b2` | `e61b4ae40754da382f852a7f0c138ac1542db6ee` |

## Accepted pre-freeze validation

The accepted immutable audit run is
`v5-final-execution-freeze-preflight-r3`. Its overall audit verdict is `FAIL`
and its confirmatory status is `NOT_EXECUTED`. The failure is retained rather
than rewritten: it records the historical E3 canonical-LF checksum mismatch,
the early E5 manifest with missing frozen provenance, and their design-snapshot
comparison differences. Current regeneration completed as
`PASS_WITH_UNAVAILABLE_ANALYSES`; figure regeneration completed as `PASS`.
These historical failures are not current-source execution defects and are not
eligible to support new Protocol-v5 claims.

Validation executed against `FROZEN_EXECUTION_SHA` with the confirmatory,
freeze-artifact, E4-readiness, and custody environment variables unset:

| Command | Result |
| --- | --- |
| `make v5-test` | PASS, 251 tests |
| `make v5-resource-test` | PASS, manifest validation plus 72 tests |
| `make v5-resource-efficiency-test` | PASS, contract validation plus 38 tests |
| `make v5-user-study-test` | PASS, 90 tests |
| `make v5-user-study-smoke` | PASS, `SYNTHETIC_DRY_RUN`, temporary output removed |
| `make v5-isolation-check` | PASS; the final pre-freeze scan inspected 1,963 documents and found no confirmatory bundle |
| `make v5-audit-test` | PASS, 54 tests |
| `make v5-validate V5_RUN_ID=v5-final-execution-freeze-preflight-r3` | Expected exit 2; audit `FAIL`, confirmatory `NOT_EXECUTED` |
| `make v5-analyze V5_RUN_ID=v5-final-execution-freeze-preflight-r3` | Expected exit 2; `PASS_WITH_UNAVAILABLE_ANALYSES` |
| `make v5-figures V5_RUN_ID=v5-final-execution-freeze-preflight-r3` | Expected exit 2 from retained audit; figure status `PASS` |
| `make v5-audit V5_RUN_ID=v5-final-execution-freeze-preflight-r3` | Expected exit 2; audit `FAIL`, confirmatory `NOT_EXECUTED` |
| repair/freeze focused pytest invocation | PASS, 90 tests |
| `.venv/bin/python -m pytest -q` | PASS, 1,347 tests |
| `make check` | PASS, 25 checks; 0 failures; live-cluster and mutating-demo checks intentionally skipped |
| `.venv/bin/python scripts/validate-portable-evidence.py` | PASS; 13-file portable core, 320 Stage-C trials |
| historical immutability and v4 evidence validators | PASS; tracked 960-record historical run verified |
| `make validate-raw-integrity` | PASS, 1,877 files |
| `.venv/bin/python scripts/scan-secrets.py` | PASS, 3,146 text files |
| `git diff --check` | PASS |
| `make v5-e4-preflight` | Expected `NOT_EXECUTED`; external readiness/oracle/image/capacity/cgroup and eligible cluster are absent |

## Required custody attestation

Before any confirmatory material is supplied, an external custodian must issue
a checksum-bound attestation with at least these fields:

- `schema_version`, `protocol_version`, and a safe `attestation_id`;
- pseudonymous `custodian_id` and `reviewer_id` values only;
- `attested_at_utc` and `supplied_at_utc` timestamps;
- `dataset_id`, `split_id`, and `role: confirmatory`;
- `case_count` and semantic `family_count`;
- canonical dataset SHA-256 and byte-level file SHA-256;
- freeze ID, freeze-manifest SHA-256, `FROZEN_EXECUTION_SHA`, and
  `FREEZE_ARTIFACT_COMMIT_SHA` exactly as listed above;
- an explicit assertion that the freeze was created and committed before data
  supply;
- assertions that custody is read-only, the execution mount is read-only, and
  implementation/tuning agents did not receive labels or case content;
- contamination-review status, pseudonymous reviewer, UTC review time, and a
  fail-closed disposition.

The attestation must not include prompts, labels, participant identities,
private filesystem paths, or any P5/P6 private custody content. A missing,
mismatched, ambiguous, duplicate-key, or non-finite attestation must block
execution.

E4 additionally requires an external document conforming exactly to
`benchmarks_v5/protocol-v5-e4-readiness-attestation-v1.schema.json`. It binds
the approved independent oracle, digest-pinned execution image, disposable
cluster, exact Kubernetes-version hash, node identity/name/UID and allocatable
capacity, and cgroup-v2 capability to both freeze SHAs.

## Allowed commands

Run only from a clean checkout at the handoff descendant, with the exact freeze
above. Resolve dataset, custody, readiness, oracle, and output placeholders
outside the repository without recording private paths in public artifacts.

1. Verify authority and isolation:

   ```bash
   .venv/bin/python -m evaluation_v5.freeze verify \
     --freeze results_v5/protocol-v5.0.0/freezes/v5-final-execution-freeze/freeze-manifest.json
   make v5-isolation-check
   ```

2. E1/E2 offline execution may use
   `python -m evaluation_v5.offline.runner` only with the verified external
   confirmatory split, the authoritative `--freeze`, a temporary exact export
   of that manifest's `configuration_snapshot` for
   `--frozen-configuration`, a new immutable result directory, and systems
   `P1,P2`. P3 must remain disabled.

3. E3 may use the existing
   `python -m evaluation_v5.user_study` validation, assignment, and
   `finalize` entry points only after the external custody attestation has
   passed and only with a new immutable output directory. Human collection is
   external; do not place direct participant identifiers in repository data.

4. E4 must first run:

   ```bash
   PYTHONPATH=. .venv/bin/python -m evaluation_v5.resource preflight \
     --target all \
     --freeze results_v5/protocol-v5.0.0/freezes/v5-final-execution-freeze/freeze-manifest.json \
     --readiness-attestation <external-e4-readiness.json>
   ```

   Only a `READY` result permits the existing
   `python -m evaluation_v5.resource execute` and
   `python -m evaluation_v5.resource.efficiency_runner execute` entry points,
   both with the same freeze and readiness attestation, the attested image, and
   new immutable result directories.

5. E5 may use `python -m evaluation_v5.image_storage` only with the exact
   freeze, verified external dataset custody, an authenticated E1/E2
   recommendation package where required, digest-pinned images, the contracted
   `linux/amd64` platform, and a real collector for any `OBSERVED` status.

Analysis, validation, and figure commands may consume only authenticated,
sealed result packages produced by these entry points. Dry-run output remains
non-observed.

## Prohibited changes and interpretations

- Do not modify, amend, rebase, merge, cherry-pick, or otherwise change the
  frozen execution tree. Do not merge this branch into `main` as part of this
  handoff.
- Do not change or tune code, dependencies, P1/P2/P3 identities, P3 state,
  prompts, schemas, catalogs, corpus, indexes, retrieval parameters,
  thresholds, ranking or constraint weights, candidate metadata, dynamic
  resource policy, experiment contracts, workloads, scoring, timing, storage
  domains, or platform requirements.
- Do not open P5 or P6 private custody files. Do not expose sealed labels or
  cases to implementation, tuning, prompt, ranking, or catalog code.
- Do not overwrite or relabel historical evidence. Use new immutable/versioned
  result directories and preserve raw observations separately from derived
  metrics and interpretation.
- Do not convert dry runs, synthetic fixtures, readiness checks, failed runs,
  or stochastic repetitions into observed evidence or independent semantic
  samples. Workload family remains the E4 semantic unit; participant remains
  the E3 unit.
- Do not report B0 ranking metrics or activate P3. Do not make causal,
  general-performance, cluster, human, or storage claims without the required
  real authenticated evidence.

## Outstanding real execution

No Protocol-v5 confirmatory split or custody attestation has been supplied; no
human study has been executed; no eligible E4 cluster/oracle/image/capacity or
cgroup attestation exists; and no real E5 registry/storage observation exists.
Those absences are correctly represented as `NOT_EXECUTED`, `UNVERIFIED`, or
`NOT_APPLICABLE`, never as zero-valued evidence.
