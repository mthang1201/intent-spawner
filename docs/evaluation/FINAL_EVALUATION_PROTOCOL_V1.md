# Final Evaluation Protocol v1

Protocol version: `final-evaluation-protocol-v1.0.0`

Status: harness ready; final observations not yet collected.

## 1. Closed primary-system registry

The final evaluation permits exactly these primary system IDs:

| ID | Definition | Final role |
| --- | --- | --- |
| `B0` | Default JupyterHub manual administrator-provided profile/image selection; no recommendation | RQ1 baseline |
| `P1` | Frozen existing rule-based recommender | RQ1/RQ2 comparator |
| `P2` | Structured Intent, sparse+dense retrieval, Reciprocal Rank Fusion, deterministic hard constraints and ranking | Main contribution; RQ1/RQ2 |
| `P3` | P2 plus grounded, schema-validated reranking of P2-feasible candidate IDs | RQ1/RQ3 only if retained by its gate |

No aliases, providers, deployment locations, or retrieval variants are primary
system IDs. `dense_only` and `sparse_only` are optional secondary ablation labels
nested under `P2`. Direct external/local LLM experiments remain historical or
reference evidence.

The recorded incremental P3 evaluation did not retain P3. Therefore the current
active confirmatory set is `B0`, `P1`, and `P2`; RQ3 is marked
`not_applicable_after_gate`. This does not delete P3 or its negative evidence.
Changing that status requires a new, versioned gate decision and a new freeze;
it must not be changed inside an analysis run.

## 2. Freeze before final observation collection

Run the freeze command before any final test or user-study session. It records:

- candidate catalog version, file checksum, corpus version/checksum, and candidate count;
- composed dataset identity and checksums of each source;
- P2 extractor name/version/model/config and prompt-contract checksum;
- embedding model ID, revision, dimensions, and tokenizer version;
- sparse, dense, and hybrid index versions/checksums and the RRF configuration;
- deterministic constraint evaluator, policy, and ranker versions;
- B0/P1/P2 revisions and the P3 revision when applicable;
- P3 gate status and gate-evidence checksum;
- source-file checksums, Git commit/dirty state, Python version, and protocol version.

The analysis command re-verifies those checksums and aborts on drift. Any change
to a frozen input requires a new freeze and invalidates it as the same final
configuration. Final cases must not be used for tuning.

Example for the currently recorded P3 decision:

```bash
python3 -m evaluation_final.runner freeze \
  --p3-gate-status not_retained \
  --run-id 20260822T-final-evaluation-freeze-v1
```

Result directories are exclusive-created. Reusing an existing run ID fails.

## 3. Evidence layers

Every run uses three separate directories:

```text
raw/             preserved observations, never edited by analysis
derived/         reproducible metrics and statistical outputs
interpretation/  claim status, limitations, and later human interpretation
```

An analysis run is a new directory that references the immutable freeze. It
does not modify the freeze or input evidence. Missing observations produce an
explicit `not_executed`/`metrics_generated: false` status, never zero-filled or
imputed results.

## 4. RQ1 — system/user-facing effectiveness against B0

### 4.1 Study design

Use a real, ethics-appropriate, pseudonymous user study. Recommended design:

1. Freeze a versioned set of tasks and acceptable candidate IDs before sessions.
2. Counterbalance active systems and task families across participants (for
   example, a Latin-square order), avoiding reuse of the same exact task for a
   participant where learning would reveal the answer.
3. Give all systems the same task information and the same administrator-owned
   profile/image catalog.
4. For `B0`, expose only ordinary JupyterHub manual selection. Do not simulate a
   recommendation or ranking.
5. Capture pseudonymous event records. Do not capture raw user intent, source
   code, prompts, or PII in this schema.
6. Predeclare exclusions (for example, instrumentation failure) before analysis
   and preserve excluded raw sessions with a versioned exclusion record.

The task-set schema is `final-rq1-task-set-v1.0.0`. Each task defines only a
versioned task ID, workload family, acceptable candidate IDs, and optional
preferred candidate ID. The event schema is `final-rq1-user-event-v1.0.0` and
records study/session/task/system IDs, a contiguous event index, monotonic
elapsed time, event type, and optional candidate ID.

Supported events are:

- `study_started`;
- `candidate_selected`;
- `recommendation_previewed`, `recommendation_accepted`, or
  `recommendation_rejected` (prohibited for `B0`);
- `manual_correction`;
- `task_completed` or `task_abandoned` as the one terminal event.

### 4.2 Measures

RQ1 reports, by active system:

- correct final environment selection;
- time to first appropriate selection;
- interaction and selection action counts;
- manual correction count;
- task completion and completion time.

Paired comparisons use common participant/task observations against `B0`.
Binary outcomes use exact McNemar summaries; mean paired differences use
participant-cluster bootstrap 95% confidence intervals. Report distributions,
not only means.

`B0` does not emit a recommendation or ranked list. Top-1, Hit@k, MRR, nDCG,
fallback, and other recommendation-ranking metrics are therefore prohibited in
RQ1 output for B0. If a real user study is not supplied, RQ1 remains
`not_executed`; no synthetic participant records may be generated as evidence.

## 5. RQ2 — P2 recommendation quality versus P1

Run P1 and P2 on the same frozen final cases and preserve one raw prediction per
system/case. The harness requires complete paired coverage and rejects unknown
system labels or dataset checksum mismatches.

Report:

- preferred-candidate Top-1 accuracy;
- acceptable-candidate Top-1 and Hit@1/3/5;
- MRR and nDCG@5 using binary relevance over the acceptable set;
- hard-constraint violation rate on feasible requests;
- unsupported-request confusion counts, precision, recall, F1, and accuracy;
- end-to-end latency distribution;
- fallback rate and fallback-category counts;
- final `PolicyValidator` compliance;
- P2 failure-category counts and sample IDs.

Paired P2-minus-P1 comparisons include mean difference, workload-family cluster
bootstrap 95% confidence interval, and exact McNemar summaries for binary
outcomes. Latency is marked lower-is-better. All other reported paired quality
or safety outcomes are marked higher-is-better (constraint safety is used for
the paired direction).

Optional `dense_only` and `sparse_only` prediction records must retain
`system: P2` and add only `ablation_id`. They are rendered under `P2_ablations`
and never enter the primary-system table.

The current 66-case observed P1/P2 run is preserved as historical/development
evidence. Its presence does not by itself establish that the same cases are an
unseen confirmatory test set. Split provenance must be reviewed before final
claims.

## 6. RQ3 — conditional P3 value beyond P2

RQ3 runs only when the immutable freeze records `p3_gate.status: retained`.
Supplying P3 final observations after `not_retained` is an error.

When applicable, use one paired P2/P3 observation per frozen case and report:

- P2/P3 Top-1, acceptable-candidate accuracy, MRR, nDCG@5, constraint safety,
  and latency;
- wrong/correct transition counts and exact per-query changes;
- cluster-bootstrap paired differences and exact McNemar summaries;
- reranker invalid-output, provider-failure/fallback, and out-of-feasible-set rates;
- token usage and cost only where measured with explicit pricing provenance.

P3 may reorder only supplied P2-feasible IDs. It cannot revive infeasible IDs,
invent candidates, change resources/images/Kubernetes objects, or override
`PolicyValidator`. Any reranker failure must degrade exactly to P2.

Under the current `not_retained` gate, RQ3 contains status and gate provenance
but no generated final metrics. The existing local Ollama paired run remains
negative gate/reference evidence, not a new provider-specific primary system.

## 7. Analysis command

After collecting genuine observations, create a new analysis directory:

```bash
python3 -m evaluation_final.runner analyze \
  --freeze-directory evaluation_final/results/<freeze-run> \
  --run-id <new-analysis-run> \
  --rq1-tasks <frozen-task-set.json> \
  --rq1-events <observed-user-events.jsonl> \
  --rq2-predictions <observed-p1-p2.jsonl>
```

Omit RQ1 inputs when no user study exists; the output will explicitly state
that RQ1 was not executed. Add `--p2-ablation-predictions` only for explanatory
P2 ablations. Add `--rq3-predictions` only under a retained P3 gate.

Default bootstrap configuration is 2,000 replicates with seed `20260822`.
Both are recorded in the analysis manifest.

## 8. Interpretation rules and limitations

- Separate observed descriptive results from inferential uncertainty and from
  narrative interpretation.
- Treat confidence intervals as uncertainty summaries, not automatic proof.
- Report paired sample counts and missingness for every measure.
- Do not generalize unsupported-request rates from a small diagnostic supplement.
- Report latency distributions and the execution environment; do not infer
  external cost, energy, or hardware amortization when unmeasured.
- Do not treat provider comparisons as a thesis research question.
- Do not modify P1, P2, prompts, rules, indexes, catalog, or the final dataset
  after inspecting final outcomes.
- Do not manufacture user sessions, task outcomes, costs, or missing model runs.
- Do not overwrite historical evidence or mutate a Kubernetes cluster as part
  of this protocol.
