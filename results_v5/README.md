# Protocol-v5 evidence and reproducibility

The authoritative current evidence boundary is documented in
[the final report](../docs/evaluation/PROTOCOL_V5_FINAL_REPORT.md).

The reviewed portable core preserves 34 existing packages byte-for-byte,
including invalid, legacy, incomplete and unexecuted evidence. Its exact file
allowlist and checksums are in
[the input inventory](../benchmarks_v5/protocol-v5-final-audit-inputs-v1.json).
An inventory digest identifies the bytes audited; it does not repair a broken
original checksum, establish a final freeze, or certify an observation.

Available observations are development-only E1 recommendation outputs and E5
container functional probes. Complete E1/E2 statistical analyses, real E3 human
sessions, E4 hardware trials, E5 storage measurements and v5 confirmation are
**NOT EXECUTED**. P2 remains primary and P3 is not retained. The file
`freezes/frozen-configuration.json` is a design/configuration snapshot, not an
authoritative production freeze.

Run the complete offline workflow from the repository root:

```bash
make v5-audit
```

It writes all findings and the final report before returning nonzero for
integrity errors. The current preserved defects intentionally produce exit 2.
A valid `NOT_EXECUTED` state alone is not a command failure. No command collects
new predictions, participant responses, registry data or Kubernetes metrics.

For separate stages, export one unique `V5_RUN_ID`, then run `make v5-validate`,
`make v5-analyze`, `make v5-figures`, and `make v5-audit`. All stage outputs live
under `protocol-v5.0.0/final-audit/<run-id>/` and are exclusive-created and
sealed. Completed stages may only be reused with identical code and inputs;
changed runs need a new ID. Source observations are never overwritten.

Git exceptions enumerate only reviewed files. New evidence stays ignored until
separately reviewed and enrolled. Git attributes disable byte normalization in
this namespace so checksums survive checkout. Human-study raw files currently
contain no participant observations. The legacy path relocation map identifies
old absolute references by SHA-256 of the reference string and resolves only
explicit, checksum-matching repository inputs; it never reads an external
custodian path.

The observed evidence's own timestamp, protocol, dataset checksum, backend,
catalog/index and environment metadata remain in its original manifests. Audit
runs independently record their source inventory, code hashes, Git revision,
Python and dependency versions. Application-level sealing is not external
object-lock storage or proof against deliberate tampering.
