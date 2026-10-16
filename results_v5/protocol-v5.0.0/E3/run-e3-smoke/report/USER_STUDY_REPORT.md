# Protocol-v5 E3 B0-versus-P2 user-study report

Evidence status: `DRY_RUN`.

This package contains clearly labeled SYNTHETIC DRY_RUN inputs and supports no empirical claim.

## Readiness sub-gates

| Sub-gate | Status |
|---|---|
| Framework / Harness | `PASS` |
| Confirmatory Task Set | `DEVELOPMENT_DRAFT` |
| Confirmatory Freeze | `PENDING_RESEARCHER_APPROVAL` |
| Confirmatory Assignment | `NOT_GENERATED` |
| Configuration Fairness | `NOT_VERIFIED` |
| Local Deterministic Smoke | `PASS` |
| Live Deployment Preflight | `NOT_VERIFIED` |
| Privacy Audit | `PASS` |
| Genuine Participants | `0` |
| Observed Evidence | `NOT_EXECUTED` |

## Frozen analysis contract

SelectionSuccess and DecisionTime are the two co-primary outcomes. Their two-sided p-values alone form a fixed-size two-hypothesis Holm family at alpha 0.05, including when one endpoint is unavailable. Interaction effort, corrections, notebook readiness, questionnaires, preference, and the frozen timeout-bound non-confirmation analysis are secondary. The three CUSTOM Likert items are reported separately and are not SUS dimensions.

Analysis plan: `protocol-v5-user-study-analysis-plan-v1.2.0` (`1874b426666111362ef79f4e23315dc8f51a97b10827ae2e596cc70523b1a3ce`).

Participant is the clustered/random sampling structure; the three frozen matched task pairs are fixed repeated factors only for task-level outcomes. Counterbalance cell is a coverage diagnostic because it is redundant with the frozen condition-order, variant, period, and position design.

Primary DecisionTime is conditional on matched task pairs with valid positive confirmation times in both B0 and P2. Every assigned measured trial remains in participant-flow and missingness denominators. Non-confirmation is outcome unavailability, not participant or task exclusion; differential non-confirmation is reported explicitly and evaluated only in the separately labeled predeclared timeout-bound sensitivity.

## Participant flow

| Stage | Count |
|---|---:|
| assignments_issued | 1 |
| session_records | 1 |
| consent_acknowledged | 1 |
| completed_sessions | 1 |
| excluded_sessions | 0 |
| incomplete_sessions | 0 |
| analyzable_participants | 1 |

## Condition summary

| Condition | Participants | Tasks | SelectionSuccess | Decision time, mean s | Actions, mean | Corrections, mean | SEQ | SUS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B0 | 1 | 3 | 0.333 | 3.000 | 3.000 | 0.000 | 5.000 | 50.000 |
| P2 | 1 | 3 | 0.000 | 4.000 | 3.000 | 0.000 | 5.000 | 50.000 |

## Effect estimates

- SelectionSuccess: P2-minus-B0 risk difference -0.333, 95% CI [unavailable, unavailable]; odds ratio unavailable, 95% CI [unavailable, unavailable]; Holm p unavailable. Method status: `FALLBACK`.
- DecisionTime: geometric-mean ratio unavailable, 95% CI [unavailable, unavailable]; raw paired P2-minus-B0 difference 1.000 seconds, 95% CI [unavailable, unavailable]; Holm p unavailable. Method status: `FALLBACK`.
- DecisionTime non-confirmation sensitivity: every measured trial is retained; an unconfirmed trial uses only the predeclared 600-second task-timeout bound from `protocol-v5-user-study-timing-contract-v1.0.0` (`3a1cbe7ea27d0c52eb909770d8c72f6b7a6c566e049692cae647569dd47e055d`). Paired P2-minus-B0 difference 1.000 seconds, 95% CI [unavailable, unavailable]. This sensitivity is not in the primary Holm family and no timeout penalty or pseudo-time enters the primary DecisionTime model.
- Total actions: incidence-rate ratio unavailable, 95% CI [unavailable, unavailable]; paired P2-minus-B0 difference 0.000, paired 95% CI [unavailable, unavailable]. Method status: `FALLBACK`.
- Corrections: incidence-rate ratio unavailable, 95% CI [unavailable, unavailable]; paired P2-minus-B0 difference 0.000, paired 95% CI [unavailable, unavailable]. Method status: `FALLBACK`.
- Notebook-ready time (`notebook_ready - confirmed_environment`): geometric-mean ratio unavailable, 95% CI [unavailable, unavailable]; raw paired difference 0.000 seconds, 95% CI [unavailable, unavailable]. Method status: `FALLBACK`.
- End-to-end launch time (`notebook_ready - task_shown`, separately labeled): geometric-mean ratio unavailable, 95% CI [unavailable, unavailable]; raw paired difference 1.000 seconds, 95% CI [unavailable, unavailable]. Method status: `FALLBACK`.
- P2 final override rate among confirmed P2 trials: 0.000, participant-bootstrap 95% CI [unavailable, unavailable]. B0 has no recommendation or override metric.
- SEQ ease: paired P2-minus-B0 difference 0.000, standardized paired effect unavailable, 95% CI [unavailable, unavailable]. Method status: `FALLBACK`.
- SUS: paired P2-minus-B0 difference 0.000, standardized paired effect unavailable, 95% CI [unavailable, unavailable].
- CUSTOM `custom_confidence_selected_environment`: paired P2-minus-B0 difference 0.000, standardized paired effect unavailable, 95% CI [unavailable, unavailable].
- CUSTOM `custom_natural_workload_expression`: paired P2-minus-B0 difference 0.000, standardized paired effect unavailable, 95% CI [unavailable, unavailable].
- CUSTOM `custom_selection_convenience`: paired P2-minus-B0 difference 0.000, standardized paired effect unavailable, 95% CI [unavailable, unavailable].

## Final preference

| Preference | Count | Answered denominator | Percent | Simultaneous 95% CI | Missing |
|---|---:|---:|---:|---:|---:|
| B0 | 0 | 1 | 0.000 | [0.000, 85.144] | 0 |
| P2 | 0 | 1 | 0.000 | [0.000, 85.144] | 0 |
| NO_PREFERENCE | 1 | 1 | 100.000 | [14.856, 100.000] | 0 |

## Missing responses and limitations

Missing responses are never imputed. A missing SUS item makes that condition's SUS score unavailable; an unanswered preference is not recoded as no preference. Unconfirmed trials remain in task/confirmation denominators and have a coded DecisionTime-unavailability reason. Detailed endpoint denominators are in `tables/missingness.csv`.

Session exclusions are restricted to the frozen reason registry and are never selected from observed performance or significance. Generalization beyond the recruited population, frozen tasks, catalog, and controlled Hub is unsupported.
