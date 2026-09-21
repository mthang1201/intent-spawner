# `v5-final-execution-freeze-v3` — supersession note

Status: adjacent, non-authoritative note. The authoritative artifact in this
directory is `freeze-manifest.json`, written exclusively by
`evaluation_v5.freeze.create_freeze_artifact`. This note records why the
identity exists; it grants no authority of its own.

## Chain

```
v5-final-execution-freeze      retired  (pre-fix P2, p2-deterministic-ranker-v1.0.0)
  -> v5-final-execution-freeze-v2   retired  (fixed P2, stale E4 harness)
       -> v5-final-execution-freeze-v3   current
```

All three are immutable (AGENTS.md rule 11). None is withdrawn or corrected.

## What v3 supersedes, and why

v3 supersedes `v5-final-execution-freeze-v2`. The **P2 configuration identity
is unchanged between v2 and v3** — both record the ranking fix from commit
`842109c` (`p2-deterministic-ranker-v2.0.0`,
`p2-deterministic-constraint-evaluator-v1.1.0`, `p2-config-v1.1.0`, P2
implementation `9c7e7913…`). See v2's note for that rationale.

v3 exists for two reasons, neither of which touches recommendation semantics:

1. **The E4 harness changed.** v2's `experiment_contracts.E4` section hashes
   the E4 efficiency harness implementation. Restoring legacy validity for
   pre-`3f896eb` E4 packages changed
   `evaluation_v5/resource/efficiency_plan.py`,
   `efficiency_contracts.py`, `efficiency_evidence.py` and
   `efficiency_runner.py`, so v2 no longer described the live tree:

   | contract entry | v2 | v3 |
   | --- | --- | --- |
   | `execution/efficiency_runner_implementation/file_sha256` | `b9e469c3…` | `aafe73d4…` |
   | `files[10]/file_sha256` | `ea80ca18…` | `47ee46c3…` |
   | `files[14]/file_sha256` | `b9e469c3…` | `aafe73d4…` |

   The change replaced a single global `FAMILY_COUNT` comparison with a closed
   registry of recognized design generations, so a 16-family package validates
   as evidence about the 16-family design while only the current 20-family
   design is eligible as current E4 evidence. No E4 measurement, workload,
   allocation rule or trial datum was altered.

2. **Git custody.** v2's manifest was committed together with the inventory,
   the E1 evidence and tests, so
   `evaluation_v5.freeze.verify_production_freeze` rejected it with
   "freeze artifact commit contains changes beyond the manifest". v3's
   manifest was introduced by commit `d3a409d`, which contains nothing else
   and whose parent is exactly the recorded `frozen_execution_sha`
   `a68115f9823e4dc1b7878c6bcc440d1ae59965af`. This matches how
   `v5-final-execution-freeze` was recorded in `38877fa`, and makes v3
   independently verifiable:

   ```
   git checkout d3a409d
   python -m evaluation_v5.freeze verify \
     --freeze results_v5/protocol-v5.0.0/freezes/v5-final-execution-freeze-v3/freeze-manifest.json
   ```

   returns `"status": "VERIFIED"`.

   Note the expected lifecycle: registering this freeze in the final-audit
   inventory is itself a post-freeze repository change, so
   `verify_production_freeze` reports "post-freeze executable/configuration
   change detected" at later revisions. That is by design
   (`integrity_rules.post_freeze_changes_restricted`); the artifact commit
   above remains permanently verifiable. `v5-final-execution-freeze` behaves
   identically.

## What did NOT change

* **P1 is untouched.** `systems.P1.implementation.file_sha256` is
  `063d7024…c581` in all three freezes. P1 remains a frozen comparator
  (AGENTS.md rule 15).
* **P2's identity is identical to v2.** v3 is not a new algorithm.
* **P3 remains excluded**: `p3_gate.status` `not_retained`,
  `verification_status` `VERIFIED_EXCLUSION`.
* **The development split, candidate catalog, corpus and retrieval indexes
  are unchanged** from both predecessors.
* **No confirmatory data was read**:
  `integrity_rules.sealed_data_not_read_by_freeze` and
  `created_before_sealed_data_supply` are both true.

## Consequence for existing evidence

The retirement caveat in v2's note still applies in full, and still names
`v5-final-execution-freeze` as the identity that governs it. In particular
`results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1`, every
`E4/…` and `E5/…` package, and the derived analyses over them describe the
retired pre-fix P2 and must not be cited as describing current P2 behaviour.

Evidence collected under the fixed P2 so far:

* `results_v5/protocol-v5.0.0/E1/20260921T-observed-p1-p2-development-v2` —
  offline P1-vs-P2 development run, bound to v2's design snapshot. Its P2
  configuration identity is the same one v3 records, so it remains valid
  evidence about the current P2; it is development-split and non-confirmatory.
* `evaluation_p2/results/20260921T-observed-p1-p2-v2-ranker-v2` — the
  Protocol-v4-lane paired reference for the P3 harness. Historical/formative
  evidence only (AGENTS.md rule 1); never Protocol-v5 evidence.

## E4 evidence status under v3

Every existing E4 efficiency package declares the superseded 16-family design
and is reported `LEGACY_VALID` with
`eligible_as_current_e4_evidence: false`. No E4 package is current-design
evidence under v3. Observed E4 re-collection at 20 families requires a real
disposable Kubernetes cluster
(`environment_requirements.cluster.real_kubernetes_required_for_observed_e4`)
and has not been performed.
