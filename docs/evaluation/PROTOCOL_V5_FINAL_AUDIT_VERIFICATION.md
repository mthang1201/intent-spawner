# Protocol-v5 final audit implementation verification

These are software/reproducibility checks, not new experiment observations.
The authoritative report is [PROTOCOL_V5_FINAL_REPORT.md](PROTOCOL_V5_FINAL_REPORT.md).
The reviewed audit output is
[final-audit-20260907-v2](../../results_v5/protocol-v5.0.0/final-audit/final-audit-20260907-v2/report/audit.json).

## Commands executed and results

```bash
.venv/bin/python -m pytest -q tests/test_evaluation_v5*.py tests/test_protocol_v5_research_analysis.py tests/test_protocol_v5_final_audit.py tests/test_resource_envelope_v5.py tests/test_resource_efficiency_v5.py tests/test_evaluation_v4.py
```

656 passed. This broad run preceded the final legacy-probe regression fix.
After that fix, the affected tests were rerun:

```bash
.venv/bin/python -m pytest -q tests/test_protocol_v5_final_audit.py tests/test_evaluation_v5_image_functional.py tests/test_protocol_v5_research_analysis.py
```

122 passed, including 31 final-audit test cases. The legacy v1.0 E5 regression
verifies that missing `execution_status` fields do not erase recorded Docker
executions: the existing adapter recognizes 13 executed probes and four launch
failures. The older package still fails required manifest provenance.

```bash
make v5-audit V5_RUN_ID=final-audit-20260907-v2
make -k v5-validate v5-analyze v5-figures v5-audit V5_RUN_ID=final-audit-20260907-v2
```

Both commands returned 2 after producing/reusing all requested stages. This is
expected: source integrity/provenance and historical CSV reproduction findings
remain unresolved. Analysis itself is `PASS_WITH_UNAVAILABLE_ANALYSES`; valid
`NOT_EXECUTED` packages do not cause a failure. The final 17-check breakdown is
7 PASS, 3 FAIL, 5 UNVERIFIED and 2 NOT_APPLICABLE. No result was changed to make
these commands green.

The workflow also executed these existing read-only checks:

```bash
.venv/bin/python scripts/validate-portable-evidence.py
.venv/bin/python -m evaluation_v5.isolation_audit
```

The portable v4 validator passed its 13-file checksum core and reproduced its
headline results. Repository/archive isolation scanning found no confirmatory
gold bundles. External custody and semantic independence remain unverified.

After allowlisting the final archived outputs:

```bash
.venv/bin/python -m pytest -q tests/test_protocol_v5_final_audit.py::test_clean_relocated_checkout_runs_full_audit_with_only_portable_core
.venv/bin/python scripts/scan-secrets.py
git diff --check
```

The clean-checkout test passed. It created and committed a separate temporary
repository, forced a checkout of its committed blobs, reproduced the full audit
using only the portable core, and verified that tracked files stayed clean.
It also changed a temporary input deliberately and confirmed that a new run
emitted a diagnostic report without interpreting the unauthenticated input.
The high-confidence secret scan passed on 2,724 candidate text files before
this verification note was added. Diff whitespace validation passed.

## Outputs and reproduction evidence

The final versioned run contains 44 files:

- `run.json`: audit timestamp, Git revision/dirty state, implementation hashes,
  runtime/dependency versions and input-inventory checksum.
- `validation/`: all 34 source package dispositions, 17 checks, claim-registry
  decisions, detailed integrity/provenance findings, and sealing metadata.
- `analysis/`: regenerated E1 counts and unavailable-analysis manifests, E3
  empty-study analysis, both current E5 functional metric packages, seven exact
  parsed-JSON comparisons and checksum-bound metric lineage.
- `figures/`: defense and functional-result tables, the E3 reconstructed
  aggregate artifacts, deterministic SVG/PNG development plot, and nine
  byte-comparison results. Eight match; the participant-flow CSV mismatch is
  preserved separately from the regenerated artifact.
- `report/`: generated final report, final audit JSON and checksums.

The final report has 21 local evidence links; all targets were checked to
exist. The development PNG was visually inspected for readable labels,
clipping and alignment. It presents separate P1/P2 runs with observed 13/18
image-label matches and no invented confidence intervals.

## Preservation and remaining evidence boundaries

All 420 checksum-bound source/dependency files match the audit-start input
inventory. Recommender files and original evidence bytes were not changed.
The original E3 CSV and its recorded checksum remain mismatched. No historical
v4 artifact was overwritten, and no final freeze was manufactured.

The final audit output is allowlisted separately from the observed input core.
Intermediate implementation runs remain ignored and cannot be selected as
experiment evidence. The earlier local audit v1 used an incorrect missing-field
interpretation for legacy probe status; it is superseded by v2, not rewritten
or used in the final report.

Real confirmation still requires an authoritative pre-supply freeze and safe
custodian attestation. Human usability, measured resource efficiency and image
storage reuse require their actual human/cluster/storage execution. This work
package performed none of those experiments. Current v1 development gold also
cannot support the complete v2 component/statistical analysis. Legacy analysis
reproduction gaps and source metadata failures remain disclosed in the report.
