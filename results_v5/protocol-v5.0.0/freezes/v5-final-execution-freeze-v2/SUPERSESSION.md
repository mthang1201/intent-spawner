# `v5-final-execution-freeze-v2` — supersession note

Status: adjacent, non-authoritative note. The authoritative artifact in this
directory is `freeze-manifest.json`, which is immutable and was written
exclusively by `evaluation_v5.freeze.create_freeze_artifact`. This note records
*why* the new identity exists; it grants no authority of its own.

## What this freeze supersedes

`v5-final-execution-freeze-v2` supersedes
`results_v5/protocol-v5.0.0/freezes/v5-final-execution-freeze/`.

The predecessor is **not** withdrawn, corrected or deleted. It remains
immutable evidence (AGENTS.md rule 11) and it describes the pre-fix P2
algorithm truthfully. It is now a *retired* identity: it describes an
implementation that is no longer the current P2.

## Why

Commit `842109c` ("Core algorithm fix") corrected a defect in P2's
deterministic constraint/ranking stage. The change was intentional and
necessary, and it changed P2's observable ranking behaviour. It bumped:

| identity | `v5-final-execution-freeze` | `v5-final-execution-freeze-v2` |
| --- | --- | --- |
| `constraints.ranker_version` / `ranking.ranker_version` | `p2-deterministic-ranker-v1.0.0` | `p2-deterministic-ranker-v2.0.0` |
| `constraints.evaluator_version` | `p2-deterministic-constraint-evaluator-v1.0.0` | `p2-deterministic-constraint-evaluator-v1.1.0` |
| `P2.config_version` | `p2-config-v1.0.0` | `p2-config-v1.1.0` |
| `ranking.tie_breaker` | `candidate_id` | `resource_cost,retrieval_rank,candidate_id` |
| `ranking.retrieval_rank_weight` | `0.75` | `0.6` |
| `ranking.resource_fit_weight` | *(absent)* | `0.15` |
| `ranking.resource_cost_policy_version` | *(absent)* | `p2-resource-cost-policy-v1.0.0` |
| `ranking.retrieval_rank_decay` / `retrieval_rank_horizon` | *(absent)* | `0.1` / `11` |
| `systems.P2.implementation.file_sha256` | `3ca68ea7…e2ff` | `9c7e7913…2042` |
| `runtime_package.sha256` | `b846fda1…9bf5` | `44a8cf90…923b` |

After `842109c` no freeze described the live tree, so the fail-closed
integrity system correctly reported drift: Check 1 ("Authoritative final
experiment freeze") read `UNVERIFIED` and
`evaluation_p3/runner.py::verify_frozen_inputs()` raised. This freeze
re-establishes a frozen identity over the corrected code rather than
loosening either check.

## What did NOT change

* **P1 is untouched.** `systems.P1.implementation.file_sha256` is
  `063d7024…c581` in both freezes, i.e. `recommender/rule_based.py` is
  byte-identical. P1 remains a frozen comparator (AGENTS.md rule 15).
* **P3 remains excluded.** `p3_gate.status` is `not_retained` and
  `verification_status` is `VERIFIED_EXCLUSION` in both freezes. This freeze
  does not reopen the P3 development gate.
* **The development split is unchanged.** `benchmarks_v5/v5-development.yaml`
  is the same 18 cases / 10 families, `canonical_sha256`
  `18894b73ec98d895348498bf6b1c4dd4d2dc6004437202bd8b93c17d09b0dc0b`.
* **The candidate catalog, corpus, and retrieval indexes are unchanged.**
* **No confirmatory data was read.** `integrity_rules.sealed_data_not_read_by_freeze`
  and `created_before_sealed_data_supply` are both true. The freeze was cut
  with no `PROTOCOL_V5_CONFIRMATORY_DATASET` in the environment.

## Consequence for existing evidence — READ THIS BEFORE CITING ANY RESULT

All Protocol-v5 evidence collected under `v5-final-execution-freeze`
describes the **retired** pre-fix P2 implementation. It must not be cited as
describing current P2 behaviour. Specifically:

* `results_v5/protocol-v5.0.0/E1/20260825T-observed-p1-p2-development-v1`
  — the observed P1-vs-P2 offline development run. Its P2 rows were produced
  by `p2-deterministic-ranker-v1.0.0`.
* Every `results_v5/protocol-v5.0.0/E4/…` package (resource envelope and
  resource efficiency, dry-run, plan and observed).
* Every `results_v5/protocol-v5.0.0/E5/…` package (image validation and
  storage scalability).
* The derived analyses over those packages, including
  `results_v5/protocol-v5.0.0/analysis/research-analysis-20260905T-final`
  and `…-implementation`, and any figure or table regenerated from them.

Those directories stay exactly as they are. They are still valid evidence
*about the identity they were collected under*; they are simply no longer
evidence about current P2. Re-collection under this freeze is required before
any of them can speak to the fixed ranker.

Fresh evidence collected under this freeze is recorded separately and is
labelled with its own run directory and freeze binding.

## E4 input drift recorded here for completeness

This freeze's `experiment_contracts.E4` section also differs from the
predecessor's. That difference is **not** caused by `842109c`. Commits
`3f896eb` ("Add failure-inducing workloads to E4") and `435f543` ("Add P1 as
an explicit E4 comparator") changed ten reviewed `benchmarks_v5/` E4 inputs
without re-recording them in the final-audit inventory:

```
benchmarks_v5/README.md
benchmarks_v5/protocol-v5-resource-efficiency-inputs-v1.schema.json
benchmarks_v5/protocol-v5-resource-semantic-independence-v1.schema.json
benchmarks_v5/protocol-v5-resource-workloads-v1.schema.json
benchmarks_v5/resource-allocation-crosswalk-v1.yaml
benchmarks_v5/resource-efficiency-capacity-v1.yaml
benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml
benchmarks_v5/resource-efficiency-inputs-v1.yaml
benchmarks_v5/resource-envelope-semantic-independence-v1.yaml
benchmarks_v5/resource-envelope-workloads-v1.yaml
```

The existing E4 packages were collected against the *older* bytes of those
files. This freeze records the current bytes. The E4 packages therefore carry
the same retirement caveat as above, for a second and independent reason.
