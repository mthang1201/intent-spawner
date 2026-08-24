# Protocol-v5 Experiment Architecture

Protocol version: `5.0.0`

Manifest schema: `protocol-v5-manifest-v1.0.0`

Status: architecture implemented; Protocol-v5 experiments not executed

Data-isolation contract:
[PROTOCOL_V5_DATA_ISOLATION.md](PROTOCOL_V5_DATA_ISOLATION.md)

Offline recommendation runner and raw-evidence validator:
[PROTOCOL_V5_OFFLINE_RUNNER.md](PROTOCOL_V5_OFFLINE_RUNNER.md)

## 1. Purpose and evidence boundary

Protocol-v5 provides one evidence model and filesystem contract for six
experiment families. P2 is the main proposed method. The primary systems are:

- **B0** — default/manual JupyterHub selection without a ranking;
- **P1** — the frozen existing rule-based recommender;
- **P2** — Structured Intent, hybrid retrieval, and deterministic
  constraints/ranking; and
- **P3** — P2 plus grounded LLM reranking, only when retained by its separate
  development gate.

The repository contains the visible `v5-development` bundle for formative
work, but no confirmatory cases or labels. It contains no Protocol-v5
participant records, recommendation outputs, cluster measurements, image-size
measurements, or confirmatory results. The validation-only offline entry point
does not evaluate any case or emit sealed contents. Protocol-v4
evidence remains historical/formative evidence and is not rewritten or
relabeled as Protocol-v5 evidence.

Development and final confirmation are separate. A manifest records a
`split_identity` with an explicit `development` or `confirmatory` stage.
Workload family is the semantic independent unit. Paraphrases, translations,
and repeated stochastic executions do not increase the independent accuracy
sample count. Confirmatory cases and labels must remain inaccessible to code
that changes prompts, retrieval parameters, candidate metadata, thresholds,
ranking, constraints, or P3 configuration. The separate isolation contract
defines the split-bundle schema, freeze-before-supply gate, external loader,
contamination checks, and packaging/cache/index guardrails.

## 2. Experiment registry

### E1 — P1 versus P2 recommendation quality

Run frozen P1 and P2 against the same versioned cases and retain paired raw
outputs. Suitable outcomes include Top-1 correctness, acceptable-candidate
Hit@K, MRR, nDCG, constraint violations, coverage/fallback, and latency. Family
is the inferential unit. B0 is not part of E1.

### E2 — natural-language robustness

Measure stability and correctness across natural-language variants belonging
to the same workload family, including paraphrase, verbosity, noise, and
language variants that were designed before confirmation. Report within-family
variation separately from family-level performance. A text variant is not an
independent workload.

### E3 — B0 versus P2 human usability and selection

Use an ethics-appropriate, counterbalanced, pseudonymous real-user study. B0
shows ordinary administrator-provided manual choices and emits no
recommendation or ranking. Therefore MRR, nDCG, Hit@K, recommendation
acceptance, and similar ranking metrics are prohibited for B0. Compare
selection correctness, time, interaction burden, corrections, completion, and
abandonment. Do not store raw participant identifiers or fabricate sessions.

### E4 — resource efficiency and dynamic resources

Use a controlled disposable environment with frozen systems and workload
families. Preserve resource requests/limits, measured CPU and memory windows,
scheduling outcomes, OOM/Pending outcomes, runtime, cleanup, Kubernetes and
container identities, and measurement-source provenance as raw evidence.
Repeated runs estimate stability and runtime variability; they do not create
new independent workload families. No resource value may be inferred when a
real measurement is unavailable.

### E5 — image correctness and image-storage scalability

Keep image correctness separate from storage scaling. Correctness may cover
catalog capability satisfaction, immutable image identity, pull/start outcome,
and bounded workload compatibility. Storage scaling may cover measured
compressed/content-store sizes, layer sharing, retained image count, and node
storage consumption under a declared runtime. Never substitute catalog text or
registry estimates for measurements, and never invent image sizes.

### Optional E6 — P2 versus P3 incremental reranking value

E6 is permitted only after a development-only gate retains P3. P3 must consume
the frozen P2 feasible set, and failures must preserve the P2 decision. Run P2
and P3 on paired cases; record reranker/model/prompt identity, validity,
fallback, latency, and measured token/cost data when available. Final cases
must not influence the P3 gate or configuration. If P3 is not retained, E6 is
`NOT_EXECUTED`, not zero-valued.

## 3. Manifest and provenance contract

Every canonical cross-experiment evidence package uses `ProtocolV5Manifest`.
The append-only offline recommendation collector additionally uses its
specialized `offline-run-provenance.json` execution-plan schema and completion
marker, as documented in the offline runner contract. Its fields bind the raw
rows to the same Protocol-v5 identities while also recording requested versus
effective repeats and crash-safe resume state. Its required keys are stable
for that collector. The cross-experiment manifest's required keys are stable
for all statuses, while incomplete/non-executed packages may use `null` for
provenance that genuinely does not yet exist. An `OBSERVED` manifest rejects
null, blank, `unknown`, `unavailable`, `TBD`, and similar placeholders in
required provenance.

The manifest records:

- schema/protocol version, experiment ID, run ID, full Git revision, and UTC
  execution timestamp;
- dataset ID/SHA-256 and split ID/stage;
- participating B0/P1/P2/P3 system versions;
- catalog and P2 candidate-corpus versions/SHA-256 values;
- StructuredIntent schema and extractor name/version/model/prompt identity;
- embedding model revision and dense, sparse, and hybrid index
  versions/SHA-256 values;
- retrieval and constraint/ranking configurations;
- P3 reranker version when P3 participates;
- environment identity and explicit random seed list; and
- one evidence status.

An empty seed list explicitly means the run has no stochastic/randomized
component. It is not a missing field. P3 in `backend_system_versions` requires
a non-blank `p3_reranker_version`; the field must otherwise be `null`.

The v5 adapter accepts already-produced `P2OperationalProvenance` and optional
`P3OperationalProvenance` mappings. It does not import or construct a backend.
Because P2 operational provenance does not contain the catalog file digest,
the caller must supply that SHA-256 separately and should verify it against the
catalog file before writing the manifest. The adapter also rejects a P3
snapshot whose frozen P2 backend, pipeline, corpus, or hybrid index identity
does not match the supplied P2 snapshot.

### Evidence statuses

| Status | Meaning | Claim use |
| --- | --- | --- |
| `PLANNED` | Preregistered or scheduled work only | No observations |
| `DRY_RUN` | Harness/schema/path validation only | No empirical claims |
| `OBSERVED` | Genuine execution with complete provenance | Eligible after validation |
| `INCOMPLETE` | Genuine collection began but is incomplete | Preserve raw data; no complete-run claim |
| `FAILED` | Execution failed with the failure retained | Failure evidence only |
| `NOT_EXECUTED` | Required real execution did not occur | Explicitly missing, never imputed |

## 4. Immutable evidence layout

```text
results_v5/
  protocol-v5.0.0/
    E1|E2|E3|E4|E5|E6/
      <run-id>/
        manifest.json
        raw/
        derived/
        report/
```

`raw/` contains preserved observations. `derived/` contains reproducible
metrics/statistics computed from referenced raw inputs. `report/` contains
status, interpretation, figures, and limitations. Analysis must never edit
raw observations, and reports must not masquerade as raw or derived evidence.

Run directories and JSON provenance files are exclusive-created. JSON is
serialized before publication, written to a same-directory temporary file,
flushed and fsynced, and atomically published. A failed write leaves the prior
file byte-identical and removes the temporary file.

An explicit `development_override=True` may reuse a development run directory
or replace an addressed JSON file only when its status is not `OBSERVED`.
Supplying the flag for a confirmatory split or `OBSERVED` package fails before
filesystem mutation. The override never deletes an entire directory; unique
development run IDs remain preferred.

## 5. Reuse and validation

Protocol-v5 reuses the streaming `file_sha256` helper from
`evaluation_v4.dataset`, the v4 exact-field/fail-closed validation style, and
the existing exclusive-create convention. Its raw/derived/report separation
also follows the repository's later final-evaluation evidence layering. No
Protocol-v4 module, historical result, cluster record, or recommendation
semantic is modified.

Focused unit tests use temporary `DRY_RUN` directories. The only `OBSERVED`
objects constructed by tests are in-memory negative validation fixtures and
are never persisted as evidence. Compatibility checks validate historical raw
checksums, cluster artifacts, portable v4 evidence, and a tracked v4
recommendation package without executing a recommender.

## 6. Work still requiring real execution

The architecture alone supports no empirical thesis claim. Later work must
independently design and freeze benchmark families, collect real user-study
observations for E3, run controlled cluster trials for E4, measure real image
correctness/storage behavior for E5, and execute E6 only if P3 passes its
development gate. Each activity must create a new immutable run and preserve
raw observations, derived outputs, provenance, limitations, and any explicit
`NOT_EXECUTED` status.
