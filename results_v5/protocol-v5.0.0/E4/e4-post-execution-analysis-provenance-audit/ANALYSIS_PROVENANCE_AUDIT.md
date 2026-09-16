# Protocol-v5 E4 post-execution analysis provenance audit

## Disposition

Raw execution and oracle evidence are valid. The frozen analyzer defect was reproduced exactly, the analysis-only repair was frozen before execution, and two repaired runs are byte- and semantic-identical. Nevertheless, this audit fails closed at `OVERALL_E4_STATUS=OBSERVED_INCOMPLETE` and does **not** certify numerical conclusions because the requested packing result `4 / 1 / 2 / 2` contradicts immutable raw requests and the frozen node capacity.

```text
RAW_EXECUTION_STATUS=OBSERVED_COMPLETE
ORACLE_STATUS=OBSERVED_VALID
ANALYSIS_STATUS=REPAIRED_REPRODUCIBLE
OVERALL_E4_STATUS=OBSERVED_INCOMPLETE
EMPIRICAL_EXECUTION_NOT_RERUN=true
RAW_EVIDENCE_REUSED=true
NUMERICAL_CONCLUSIONS_CERTIFIED=false
```

No oracle or comparative trial was rerun. Historical raw, oracle, readiness, and analysis packages were not modified or overwritten.

## Raw and oracle audit

The raw package seal verifies 647 files and 640 real Kubernetes trial records. Independent accounting found exactly 640 unique trial IDs, primary IDs, and family-condition-repetition cells: 16 families × 4 conditions × 10 repetitions. Trial order exactly matches the sealed plan; there are no missing, duplicate, or replacement cells. All 640 records retain resource requests, cgroup v2 telemetry, Kubernetes lifecycle fields, correctness markers, terminal outcome flags, and byte-equivalent sidecars.

All trials bind to OrbStack context, node `orbstack`, UID `574e43f1-b4af-45a4-94ae-385414e52560`, Docker runtime `29.4.0`, and image content digest `sha256:1b7702f073b094eb3f5ced5fadd33d6f3e0346481b15ed8de998fd3a4e8a3bdd`.

The oracle package seal verifies 353 files, 344 attempts, 143 decision records, and all 16 families. Nine families have comparison-eligible safe envelopes; seven are failed or non-certifiable calibrations. The immutable review sidecar proves `REQUIRED → APPROVED`, reviewer `eval-reviewer-orbstack-v5`, fingerprint `d05a7fd0f3b5d6e46131d2b39d78d386936cad8ef194b0407db37c54101d3318`, and safe-envelope hash `e98f3e9ff220869ec99b7b9129cceb47d1285ec67c9044c1d5ea03c2ea882a07`. Its package hash matches post-oracle readiness and comparative execution.

## Frozen reproduction and recovered patch

At `FROZEN_EXECUTION_SHA=3d9a777390ae50cac266ceee0a58c718a34d6725`, the untouched analyzer exits 1 before writing an output directory with:

```text
ValueError: node capacity contract is NOT_FROZEN
```

This is classified `POST_FREEZE_ANALYSIS_IMPLEMENTATION_DEFECT`. The exact recovered patch is in `POST_FREEZE_ANALYZER_PATCH.diff`; it synthesized a frozen capacity at runtime from `environment.json` and included hard-coded node/resource defaults, a current-time fallback, an all-zero provenance hash, and broad exception handling. It directly changed capacity/package bytes but did not directly change cost derivation, oracle errors, bootstrap/Holm, Pareto, or the packing algorithm.

## Corrective repair and reproducibility

The repair adds an explicit committed analysis-freeze interface. It validates the original freeze, raw/oracle/readiness hashes, exact node capacity, implementation hashes, source ancestry, and unchanged scientific contract. The legacy path remains fail-closed when the global capacity contract is `NOT_FROZEN`.

The final repair freeze binds source `bfc76bb3d8075f960237e15fe0de9f50df39b84f`, is committed at `9437cbf874a484e2841699bdb755c3540954150e`, and hashes to `cef0733b0fe5ecc75f4feb1a0de00cd6244d03e45c2df093280a3c85d143eb3f`. It was schema/checksum/source-verified from a clean checkout before either analyzer run.

Two independent output directories validate successfully and have byte-identical trees. Their `SHA256SUMS` file hashes are both `d100aaa8c4bbe127d4c3ec5bb3a4a85118ace50dc95c8e14bcf8ea5188afae42`. Compared with the historical analysis, all nine scientific output files are byte-identical; only the v2 provenance manifest and package checksum differ.

## Independent recomputation and blocker

`independent_recompute.py` uses only the Python standard library and imports no project analysis/statistics/capacity code. Starting from raw trials and oracle envelopes, it exactly reproduces 640 derived metric rows, four condition summaries, 68 bootstrap/Wilcoxon/Holm rows, all Pareto classifications, and deterministic request packing.

The immutable capacity is 8000m CPU and 7993 MiB memory. The result is:

| Condition | CPU request total | Memory request total | Hard lower bound | Packed nodes |
|---|---:|---:|---:|---:|
| STATIC_LARGE | 24000m | 24576 MiB | 4 | 4 |
| P1_CATALOG | 2000m | 4608 MiB | 1 | 1 |
| P2_CATALOG | 20600m | 21760 MiB | 3 | 3 |
| P2_DYNAMIC | 17600m | 20480 MiB | 3 | 3 |

Thus `4 / 1 / 2 / 2` is infeasible without changing the immutable capacity, raw requests, or packing semantics. Those changes are outside the authorized corrective-analysis scope. The observed recomputation `4 / 1 / 3 / 3` matches both the historical and repaired packages, but numerical conclusions remain uncertified because the requested gate does not pass.

## Validation

The full targeted suite reports 156 passed tests; the focused suite reports 42 passed. Raw, oracle, historical analysis, repaired analysis, historical immutability, raw integrity, portable evidence, and the clean-checkout confirmatory freeze verification all pass. Running the old confirmatory-freeze verifier on the repair branch correctly rejects the new post-freeze files; verification at the immutable freeze artifact commit reports `VERIFIED`.

See `test-results.txt`, `frozen-reproduction.json`, `repaired-reproductions.json`, and `independent-recomputation.json` for exact outcomes and hashes.
