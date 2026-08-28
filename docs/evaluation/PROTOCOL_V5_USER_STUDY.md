# Protocol-v5 E3: B0-versus-P2 User Study

## Status and research question

**Evidence status: `NOT_EXECUTED`.** This document specifies a framework for a
future consented study. The repository contains no Protocol-v5 participant
responses and this protocol must not be cited as observed usability evidence.

The research question is:

> Does P2 help real users select an appropriate JupyterHub environment more
> accurately, faster, and with less interaction effort than default/manual
> JupyterHub?

The two directional co-primary hypotheses are:

- **H1 — SelectionSuccess:** P2 increases the probability that the confirmed
  profile and image are acceptable and satisfy all frozen hard constraints
  (`selection_success`, asserted equivalent to `selection_acceptable`). Exact
  selection of the frozen preferred candidate
  (`selection_correct`) is reported separately.
- **H2 — decision time:** among confirmed complete matched tasks, P2 reduces
  elapsed time from `task_shown` to `confirm`. Every assigned measured task
  remains in confirmation and missingness accounting, and the frozen
  600-second timeout-bound sensitivity retains unconfirmed trials.
Interaction burden, completion, cancellation, corrections, P2 overrides, and
optional task-shown-to-notebook-ready time are secondary or diagnostic
outcomes. Any future formal analysis must account for the two co-primary
hypotheses and the repeated observations within a participant. No significance
conclusion is produced merely because the harness exists.

## Conditions and fairness boundary

The study uses one isolated, frozen JupyterHub deployment and one Kubernetes
environment for both conditions. The deployment exposes the same ordered
profiles, notebook images, immutable image references, descriptions,
administrator policy, storage, quota, and cluster configuration.

- **B0:** the participant manually chooses a profile and image from blank
  selectors. No recommender is called and no recommendation or ranking is
  displayed.
- **P2:** the participant describes the scenario in their own natural language,
  requests a preview, then confirms, edits and previews again, or explicitly
  overrides using the same manual selectors exposed by B0.

Only the intelligent-recommendation interaction differs. The checked-in
`helm/baseline-values.yaml` and `helm/proposed-values.yaml` are not valid as two
separate study arms because their image and storage configuration differs. The
study-only overlay therefore selects B0 or P2 inside a single Hub from the
frozen assignment manifest. P2 extraction, retrieval, constraints, ranking,
policy validation, preview contracts, and normal audit schema remain frozen.

Equality is enforced by the secret-free
`protocol-v5-user-study-environment-fairness-v1.0.0` manifest. It binds SHA-256
identities for the profile catalog, image catalog, policy/candidate corpus,
displayed descriptions, shared configuration, deployment Git revision, and
named Kubernetes study environment. Separately named B0 and P2 arm hashes must
equal one recomputed shared-environment hash. Assignment preparation, the live
Hub adapter, and finalization fail closed on drift. Confirmatory execution
rejects an absent or development-unfrozen fairness identity. Secret-bearing
Helm and authenticator values are never hashed.

B0 does not generate a recommendation or ranking. Acceptance, override,
Top-1, Hit@K, MRR, nDCG, fallback, or recommendation-quality metrics must never
be reported for B0.

## Participants, inclusion, and stopping

The planning target is **36 valid completed crossovers**. Recruitment stops
when 36 participants have valid measured observations in both conditions.
Only predeclared whole-session exclusions may be replaced. There is no interim
efficacy stopping and no sample-size change based on observed condition
differences. If recruitment ends before the target, the run is reported as
`INCOMPLETE` with the actual attempted, excluded, and analyzable counts.
A valid completed crossover means every assigned measured trial reached its
recorded terminal state (`confirm` or `cancel`) under valid instrumentation; it
does not mean that every trial was confirmed or correct. Cancellations and
timeouts therefore never justify recruiting a replacement.

The intended population is English-speaking users who:

- have basic experience using Jupyter notebooks;
- can interpret the administrator-provided profile and image descriptions;
- meet the researcher's institutionally approved eligibility requirements; and
- were not involved in P2 implementation, task authoring, or gold review.

Kubernetes knowledge is not required. Recruitment method and an a-priori
precision or power justification must be completed and frozen by the
researcher before confirmatory collection; 36 is a planning target, not a
universal guarantee of power.

The participant may stop at any time. A whole session is excluded only for:

- missing/withdrawn consent under the applicable approved procedure;
- a duplicate or invalid pseudonymous assignment;
- task-set, assignment, protocol, or frozen-configuration checksum drift; or
- instrumentation corruption that prevents valid coverage of either condition.

Wrong selections, slow decisions, intent edits, overrides, cancellations,
preview/backend failures, spawn failures after confirmation, and missing
notebook-ready events or questionnaire answers are outcomes or missingness,
not exclusions. An active
task interrupted by a Hub restart is preserved and marked instrumentation
incomplete rather than joined across incompatible monotonic clocks. Every
exclusion retains a versioned, non-free-text reason alongside the raw attempt.
No performance-based outlier trimming is permitted.

## Tasks, matched pairs, and crossover

The task contract is `protocol-v5-user-study-task-set-v1.0.0`. A task is a
short scenario and goal; it must not prescribe the sentence to enter into P2.
Fields such as `intent`, `exact_input`, `suggested_wording`, and
`prescribed_prompt` are rejected. Gold requirements belong to the matched pair,
not to either surface variant, so A1 and A2 necessarily share difficulty and
acceptable environment requirements.

The checked-in English draft contains one warm-up pair and three measured
pairs:

- lightweight standard-Python work, with `small-minimal-python` preferred;
- moderate pandas/visualization work, with
  `medium-scipy-data-science` preferred; and
- CPU-only PyTorch training that requires the large envelope, with
  `large-pytorch-deep-learning` preferred.

The draft is development-only and requires independent equivalence and gold
review. It is not a confirmatory task set. Confirmatory preparation rejects a
draft or unapproved pair and accepts only an externally supplied, frozen,
checksum-bound bundle. The live Hub receives a browser-safe projection with
all gold fields removed; the recommender and instrumentation runtime never
receive acceptable or preferred candidate labels.

A confirmatory assignment additionally binds the reviewed task SHA-256,
Protocol 5.0.0, assignment-generator version, event-schema version,
final-selection scoring version, fairness-manifest checksum, Git/deployment
revision, consent version, configuration identity, and catalog/corpus/policy
versions. Missing or unsupported contracts fail closed. The checked-in draft
can produce only explicitly development/`DRY_RUN` or `NOT_EXECUTED` artifacts.

Each participant completes two periods:

1. one unscored warm-up and three measured tasks in the first condition;
2. a standardized transition/break; and
3. the counterpart warm-up and three matched variants in the other condition.

This is eight task interactions in total: two unscored warm-ups and six
measured tasks. A participant never receives the same scenario twice.

The assignment generator uses 12 counterbalance cells: two condition orders,
two A/B-to-condition allocations, and three cyclic measured-pair orders. With
36 participants, each cell is used three times. Condition first, variant
allocation, and ordinal pair position are therefore exactly balanced. The
generator uses canonical SHA-256-keyed ordering rather than interpreter-
dependent random shuffling and records its algorithm version, seed, task-set
checksum, generated pseudonyms, complete sequence, and balance audit.

Recruitment counts need not be divisible by 12. Cells are consumed in complete
12-cell blocks. Within each block, a seed-keyed precomputed ordering
lexicographically minimizes attainable prefix imbalance for condition first,
A/B allocation, cyclic order row, and condition-by-variant allocation. Counts
such as 1, 5, 11, 13, or 25 use a deterministic prefix of the next block;
issued assignments are never rewritten. Dropout, exclusion, or any observed
outcome cannot influence a later cell.

Measured tasks have a ten-minute decision limit. Reaching it records `cancel`
with reason `decision_timeout`; it does not invent a confirmation or decision
time.
Warm-ups are excluded from all measured outcomes and may use standardized
feedback solely to teach the controls.

## Instrumentation and event semantics

The raw contract is `protocol-v5-user-study-event-v1.0.0`. Each record has a
server-assigned contiguous index, authoritative server UTC receipt timestamp,
per-task server-monotonic
elapsed time, study/assignment/session/trial/task/pair/condition identifiers,
consent version, event UUID, and only allowlisted profile/image/status fields.
It supports:

- `task_shown`;
- `intent_focus` and coalesced `intent_edit`;
- `preview_requested` and `preview_received`;
- `profile_changed` and `image_changed`;
- `override`;
- `confirm`;
- `cancel`; and
- `notebook_ready` when observed.

`intent_edit` is emitted once per editing episode and carries no text, length,
key value, DOM object, or transcript. Preview records contain safe status
categories and allowlisted candidate IDs only. B0 rejects intent, preview, and
override events. P2 selector changes are valid only after entering the explicit
override path. `cancel` ends an unconfirmed task; `notebook_ready` is valid only
after `confirm` and is deduplicated.

Browser timestamps are neither trusted nor stored in research events. Browser
UUIDs provide retry idempotency only; identity, index, UTC receipt time, and
monotonic elapsed time are assigned by the Hub. A reload uses the idempotent
server `task_shown` path. Duplicate UUIDs with identical content are
idempotent; reuse for different content fails. Missing `task_shown`,
noncontiguous indexes, nonmonotonic or negative time, repeated confirm, stale
task/condition identity, ready-before-confirm, mismatched ready selection, and
events after terminal finalization are rejected rather than repaired. A Hub
restart during an active task never joins incompatible monotonic clocks.

The Hub progress/readiness path records `notebook_ready` when available,
redirects through the study advance handler, stops the notebook server in both
conditions, and moves to the next assigned task. A 180-second readiness
deadline leaves end-to-end time missing, performs bounded cleanup, and advances
without fabricating readiness.

Live staging uses a study-only persistent volume. Event appends are locked,
opened with `O_APPEND`, flushed, and fsynced. Event UUIDs are idempotent and a
completion marker is exclusive-created. Consent acknowledgement supplies the
session start time; successful sealing after eight terminal task interactions
and every form derived from that participant's frozen assignment schedule
automatically appends the versioned
`sessions.jsonl` record. A restart during a nonterminal trial writes
an immutable incomplete marker plus a versioned
`instrumentation_corruption` exclusion to `exclusions.jsonl`. These appends are
also locked, content-free, flushed, and fsynced, so no spreadsheet timing or
manual timestamp transcription is needed. Finalization validates and copies
the raw streams into a new immutable Protocol-v5 E3 results directory before
deriving metrics; derived or report files never rewrite raw observations.

Questionnaires use the exact, content-free
`protocol-v5-user-study-questionnaire-v1.1.0` contract, published as
`benchmarks_v5/protocol-v5-user-study-questionnaire-v1.schema.json` and backed
by assignment-aware validation. Submission time is supplied by the study
server, not the browser. Each participant submits one SEQ after every scheduled
measured task, one post-condition form after every scheduled period, and one
final-preference form after the final period. This derives to six SEQ forms,
two post-condition forms, and one final preference in the current frozen
design; it is not a separately hard-coded completeness count. Every response
is optional, including an explicit all-null
skip, but each scheduled form must have a durable submission before the
session is complete. Records contain only fixed numeric responses or the
closed final-preference enumeration; comments and other free text are rejected.

The task item is a 1–7 SEQ-style ease rating (1 = very difficult, 7 = very
easy). Each post-condition form presents the ten standard 1–5 SUS items,
followed under a separate **CUSTOM Likert items (not SUS dimensions)** heading
by 1–7 agreement items for confidence in selected environments, ability to
express workload needs naturally, and selection convenience. Final preference
is exactly `B0`, `P2`, or `NO_PREFERENCE`; a skipped answer remains missing.
SUS uses the standard odd-item `response - 1`, even-item `5 - response`, then
multiplies the sum by 2.5. If any SUS item is missing, that condition's SUS
score is unavailable; no questionnaire response is imputed.

## Outcomes and analysis rules

For a measured task:

- `selection_success` is true only if `profile_acceptable`,
  `image_acceptable`, and `hard_constraints_satisfied` are all true for the
  participant's final confirmed selection. The frozen acceptable candidate set
  authoritatively enforces this conjunction. `selection_acceptable` is retained
  as an asserted-equivalent compatibility field and is true only if the
  participant's **final confirmed**
  `profile-image` candidate belongs to the pair's frozen acceptable set. It is
  the primary binary accuracy outcome. `selection_correct` is true only for the
  frozen preferred candidate, so an acceptable non-preferred alternative is
  acceptable but not exact. `profile_acceptable`, `image_acceptable`, and
  `hard_constraints_satisfied` are diagnostic. The acceptable candidate set is
  validated against the frozen corpus and authoritatively represents candidates
  satisfying the pair's hard requirements and policy. Cancellation or timeout
  is false for both selection labels and remains separately non-complete.
  The pre-observation `correct_selection` field is retained only as a
  compatibility alias for `selection_acceptable`.
- P2 preview values never enter the scorer. Only canonical server-accepted
  `confirm` IDs are scored by the condition-blind
  `protocol-v5-user-study-final-selection-scoring-v1.0.0` contract.
- `decision_time_seconds` is `confirm - task_shown`; it is null without a
  confirmation, with status `available`, `unavailable_cancelled`, or
  `unavailable_no_confirmation` and a coded unavailability reason. The
  secondary timeout-bound sensitivity uses the predeclared 600-second task
  limit for an unconfirmed measured trial; it does not alter or impute the
  primary DecisionTime field.
- `notebook_ready_time_seconds` is `notebook_ready - confirm`; it is null when
  either event is unavailable and carries an explicit status.
- Separately, `end_to_end_seconds` is `notebook_ready - task_shown`; it is null
  when the ready event is unavailable, with an explicit no-confirm or no-ready
  status.
- `control_action_count` counts participant preview requests, profile/image
  changes, explicit override, final confirm, and cancel. Focus-only events,
  preview responses, readiness/rendering, and retry duplicates do not count.
- `edit_count` counts coalesced `intent_edit` events.
- `total_action_count` is the sum of control actions and edits.
- `override_count` and final override status are P2-only diagnostics.
- A correction is one participant-caused profile or image change whose old
  component was already non-null before confirmation. Initial selection from a
  blank B0 selector, P2 recommendation rendering, override annotation, and
  confirm do not count again. This rule is identical across conditions and
  does not penalize keystrokes or automatic P2 DOM/network events.

Matched crossover rows join on `(participant_id, pair_id)`, never exact task
ID. Warm-ups are excluded. Report condition distributions, denominators,
missingness, cancellation/readiness rates, participant-level paired
differences, order/cell coverage, and any exclusions. Participant is the
independent sampling unit; repeated task pairs must remain clustered within
participant. Decision-time comparisons use confirmed matched tasks and must be
interpreted alongside completion and missingness.

### Frozen pre-analysis plan

The two directional hypotheses are co-primary. If confirmatory evidence is
eventually available, family-wise two-sided inference uses Holm's step-down
adjustment across SelectionSuccess and DecisionTime at family alpha 0.05.
Unadjusted estimates and confidence intervals may also be shown but do not
replace that rule. Tests are not selected after inspecting future results.

The primary binary analysis estimates the within-participant P2-minus-B0
difference in `selection_success`. Use a participant-clustered binomial GEE
with exchangeable working correlation and fixed effects for condition,
matched pair, within-pair variant slot, period, and condition order. Report the
marginal risk difference with a participant-cluster refit bootstrap 95%
confidence interval and the odds ratio as a secondary model effect. A
participant-level paired risk-difference analysis is the convergence or
identifiability fallback, and its use and reason are reported independently of
significance.

Decision time and interaction count use predeclared participant-clustered
models suited to positive skew/nonnegative counts: a participant-random-intercept
log-time mixed model for positive confirmed matched decision times and a
participant-clustered quasi-Poisson GEE with exchangeable working correlation
and Pearson dispersion estimated from the fitted residuals for actions and
corrections,
with the same condition, pair, variant, period, and order terms. Report paired
means/medians and participant-level P2-minus-B0 estimates with 95% confidence
intervals alongside model effects. Cancellations remain in accuracy and
completion denominators. An incomplete task pair contributes to descriptive
missingness and any outcome for which its predeclared estimator is valid, but
not to a complete-pair timing/action contrast. A nonpositive eligible time
triggers the raw-scale paired robust fallback for the entire endpoint; zero is
retained and no offset is added. No single-value imputation or
performance-based trimming is allowed. Whole-session exclusions retain their
frozen reason. Period/order and condition-by-period results are reported as
learning/carryover diagnostics and never used to redefine the primary
estimand.

The binary model reports `exp(beta_condition)` as its odds ratio. Its adjusted
P2-minus-B0 risk difference is a marginal standardized contrast: the fitted
probability is predicted twice over every frozen eligible design row, once with
condition set to P2 and once to B0, and the mean probability difference is
taken. A logit coefficient is never reported as a probability difference.
Counterbalance cell is retained as an aggregate coverage diagnostic rather
than an additional model term because the cell is constructed from the frozen
condition-order, variant-allocation, period/position, and pair-order design.

The deterministic fallback order is: check eligible participants and complete
pairs; check outcome variation and positive-time requirements; fit the
predeclared model; reject convergence, singularity, numerical-warning,
non-identifiable, or non-finite inference; then use the endpoint's predeclared
participant-paired fallback. P-values, significance, effect direction,
magnitude, and interval width never choose a method. Each machine-readable
result records the requested and actual method, eligibility, convergence, and
technical fallback reason.

Participant bootstraps draw participants with replacement using NumPy PCG64,
seed 20260827 plus a frozen endpoint offset, and keep every row for a drawn
participant together. Percentile equal-tailed 95% intervals use 2,000 requested
replicates. Failed model refits are discarded and counted; a model-refit
interval is unavailable unless at least 95% succeed. These settings are frozen
before participant evidence and are not changed according to significance.

SEQ uses a task-pair-aware participant-random-intercept scale model with a
paired participant bootstrap fallback. SUS and each CUSTOM item use paired
participant effects with bootstrap confidence intervals and no task-pair term,
because they are collected once per condition. Standardized paired effects are
`d_z = mean(participant P2-minus-B0 difference) / SD(participant difference)`.
Preference reports
counts and answered-response percentages with Bonferroni-adjusted Wilson
simultaneous 95% intervals; missing is reported separately. CUSTOM items are
never combined with or described as SUS dimensions.

## Privacy, consent, and limitations

Participants sign in using issued study-local identifiers matching
`P-<12 hexadecimal characters>`. Research logs do not copy authenticator
usernames, real account identifiers, emails, names, source code, notebook data,
raw intent, Kubernetes object dumps, credentials, or an identity mapping. Any
separate recruitment-to-pseudonym mapping remains outside this artifact under
the researcher's approved access, retention, withdrawal, and deletion process.
Finalization emits aggregate-only report files and runs a fail-closed privacy
audit over them. The audit rejects participant/session identifiers, issued
pseudonyms, email/IP-shaped values, participant free-text fields, and absolute
local paths. Raw pseudonymous event, session, exclusion, and questionnaire
streams stay under `raw/` and are never included in aggregate figures or the
thesis-ready report.

The consent gate records a boolean acknowledgement and a nonblank consent
document version. It is not an ethics approval, legal consent form, or IRB
claim. Obtaining institutional approval where applicable, authoring the actual
consent materials, participant compensation, accessibility, incident handling,
and lawful retention remain the researcher's responsibility.

The bounded study can support statements only about the recruited population,
frozen tasks, catalog, and controlled Hub environment. Learning, carryover,
task-pair equivalence, self-selection, artificial scenarios, English-only
materials, and the operational definition of effort as recorded actions limit
generalization. The framework does not measure subjective workload and cannot
establish production-wide causal, reliability, or resource-efficiency claims.

The strongest no-cluster integration path uses the deterministic real-adapter
smoke module. The controlled deployment procedure and remaining live checks
are documented in
[PROTOCOL_V5_USER_STUDY_SMOKE_TEST.md](PROTOCOL_V5_USER_STUDY_SMOKE_TEST.md).

## Reproducible operator flow

1. Review and externally freeze the confirmatory task/gold bundle, P2
   configuration, catalog, policy, Hub values, and environment identity.
2. Generate and verify the B0/P2 fairness manifest, then validate tasks and
   generate the assignment/public projection with an explicit seed and consent
   version using `python -m evaluation_v5.user_study`.
3. Install the opt-in study overlay on the isolated Hub and issue only generated
   pseudonyms to consented participants.
4. Export the append-only staging directory. Validate `events.jsonl` and
   `questionnaires.jsonl`, then finalize `events.jsonl`, `sessions.jsonl`,
   optional `exclusions.jsonl`, and required real-run `questionnaires.jsonl`
   together into a new E3 run ID. Use supported CPython 3.11 through 3.14 and
   install the exact NumPy, SciPy, Pandas, Patsy, Statsmodels, and Matplotlib
   versions with `pip install -r requirements-analysis.txt`; finalization
   refuses analysis dependency drift, records runtime/package versions, and
   creates all tables, figures, model/effect JSON, provenance, limitations, and
   the privacy-audited Markdown report without manual editing.
5. Preserve raw, derived, report, checksums, exclusions, and limitations. If no
   real sessions were supplied, emit `NOT_EXECUTED`, never zero-filled metrics.

All repository tests use simulated streams only. Running the harness or its dry
run is not participant recruitment and produces no empirical result.
