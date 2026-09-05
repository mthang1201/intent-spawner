# Protocol-v5 E3 B0-versus-P2 user-study report

Evidence status: `NOT_EXECUTED`.

**OBSERVED HUMAN STUDY NOT EXECUTED**

No real study exports were supplied. Every empirical result is NOT_EXECUTED; no zero-valued estimate or denominator was substituted.

## Readiness sub-gates

| Sub-gate | Status |
|---|---|
| Framework / Harness | `PASS` |
| Confirmatory Task Set | `DEVELOPMENT_DRAFT` |
| Confirmatory Freeze | `PENDING_RESEARCHER_APPROVAL` |
| Confirmatory Assignment | `NOT_GENERATED` |
| Configuration Fairness | `PASS` |
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
| assignments_issued | 36 |
| session_records | 0 |
| consent_acknowledged | 0 |
| completed_sessions | 0 |
| excluded_sessions | 0 |
| incomplete_sessions | 0 |
| analyzable_participants | 0 |

## Condition summary

NOT_EXECUTED — no empirical condition estimates are available.

## Effect estimates

NOT_EXECUTED — effect sizes, confidence intervals, and p-values are unavailable.

## Final preference

NOT_EXECUTED — no preference denominator was invented.

## Missing responses and limitations

Missing responses are never imputed. A missing SUS item makes that condition's SUS score unavailable; an unanswered preference is not recoded as no preference. Unconfirmed trials remain in task/confirmation denominators and have a coded DecisionTime-unavailability reason. Detailed endpoint denominators are in `tables/missingness.csv`.

Session exclusions are restricted to the frozen reason registry and are never selected from observed performance or significance. Generalization beyond the recruited population, frozen tasks, catalog, and controlled Hub is unsupported.
