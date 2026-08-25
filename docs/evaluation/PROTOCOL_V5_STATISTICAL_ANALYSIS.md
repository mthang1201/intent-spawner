# Protocol-v5 family-level statistical analysis

Status: statistical harness implemented; real Protocol-v5 analysis
`NOT_EXECUTED`

This document defines the downstream statistical analysis for validated
Protocol-v5 offline evidence. It does not run a recommender, alter P1/P2/P3,
or provide tuning code access to sealed confirmatory labels. Protocol-v4
evidence remains historical evidence and is never relabelled as a Protocol-v5
observation.

The primary comparison is P1 versus P2, expressed as `P2 - P1`. P2 versus P3,
expressed as `P3 - P2`, requires both validated P3 records and an authoritative
frozen P3 gate with status `retained`. P3 record presence never grants
inferential permission. Valid P3 records with a `not_retained` or unavailable
gate remain descriptive, while their comparison rows are explicitly
`NOT_RETAINED` with no test, p-value, effect interval, or Holm membership. B0
does not produce a ranking and is excluded from ranking and retrieval
comparisons.

## Inputs and evidence gate

The analyzer consumes a complete, validated Protocol-v5 offline-evidence
directory and either frozen family gold or a compiled
`protocol-v5-split-bundle-v2.0.0` dataset. Confirmatory analysis additionally
requires the authoritative freeze/split identity. A v1 split lacks the trusted
variant classes and workload strata required by this analysis and therefore
fails closed as `NOT_EXECUTED`; the analyzer does not infer those labels from
prompt text.

Evidence identity, coverage, dataset checksums, backend versions, catalog and
index versions, protocol version, and environment provenance are validated
before metrics are computed. Missing, incomplete, mismatched, or
metadata-inadequate inputs produce only a machine-readable `NOT_EXECUTED`
manifest. They never produce zero-valued substitute metrics.

Package-fatal defects and row-level absence are intentionally distinct.
Invalid raw evidence, dataset/freeze mismatch, missing trusted v2 metadata, or
checksum failure makes the whole package `NOT_EXECUTED`. A valid package with
an absent stratum, canonical-only robustness exposure, or a structurally
inapplicable retrieval comparison still succeeds and emits typed
`NOT_AVAILABLE`, `NOT_APPLICABLE`, or `NOT_RETAINED` rows.

For confirmatory custody, the raw completion marker, raw-record structure, and
raw checksum are verified before the external gold file is opened. The full
split-bound evidence validator then runs after the isolation-verified split is
loaded. Neither stage invokes or exposes labels to recommender or tuning code.

Development execution:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v5.analysis.statistical_analysis \
  --evidence-dir /path/to/prompt-5-run \
  --gold-dataset /path/to/frozen-development-gold.yaml \
  --output-dir /path/to/new-statistical-analysis
```

Confirmatory execution also declares the evidence role and authoritative
custody identifiers:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v5.analysis.statistical_analysis \
  --role confirmatory \
  --evidence-dir /external/path/to/prompt-5-run \
  --gold-dataset /external/path/to/v5-confirmatory.yaml \
  --freeze /external/path/to/freeze-manifest.json \
  --split-id <authoritative-split-id> \
  --output-dir /external/path/to/new-statistical-analysis
```

The default configuration uses 2,000 bootstrap replicates, a 95% confidence
level, base seed `20260824`, and retrieval cutoffs 1, 3, and 5. These may be
declared explicitly with the CLI configuration options. To record the absence
of executable real evidence without manufacturing results:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v5.analysis.statistical_analysis \
  --status-only \
  --output-dir /tmp/protocol-v5-statistics-not-executed
```

## Independent unit and aggregation

The semantic independent unit is the workload family. Execution repeats and
prompt variants within the same family are dependent observations and are not
additional accuracy samples. The fixed aggregation order is:

```text
execution repeats -> variant mean -> family mean -> equal-family macro mean
```

This gives each eligible family equal weight even when families contain
different numbers of variants or executions. Execution and variant counts are
retained as diagnostic provenance. Repeated stochastic calls estimate
stability and runtime variability only; the effective accuracy sample size is
always the number of eligible families.

All bootstrap resampling occurs after this collapse and resamples whole family
rows. Every bootstrap stream has a deterministic seed derived from the base
seed and analysis identity with SHA-256. The manifest records the base seed,
derivation algorithm, and each effective seed so input ordering cannot silently
change the analysis. Specifically, the seed is the unsigned big-endian integer
from the first eight SHA-256 digest bytes of the canonical JSON payload holding
the algorithm identifier, base seed, and namespace components.

## Estimands

For feasible requests, the analyzer reports:

- **JointAccept@1**: the completed selected candidate belongs to the frozen
  acceptable-candidate set, has both an acceptable profile and acceptable
  image, and has no hard-constraint violation under the frozen gold/catalog
  oracle.
- **Acceptable profile accuracy** and **acceptable image accuracy**: a missing
  or errored selection is a failure.
- **Hard-constraint violation rate**: every selected P1/P2/P3 candidate is
  evaluated by the same frozen oracle. An unknown selected candidate ID is a
  violation. No selection is retained as a distinct coverage failure rather
  than silently converted into a selected-candidate violation. Oracle safety
  takes precedence over acceptable-set membership: even an internally
  inconsistent gold row that lists a candidate as acceptable cannot make
  `JointAccept@1` pass when the oracle finds a hard-constraint violation.
- **Retrieval quality**: pre-constraint Hit, Recall, and nDCG at K = 1, 3, and
  5, plus MRR, using acceptable candidates as relevant items. P1 retrieval is
  `NOT_APPLICABLE`, not zero. The implemented P3 backend calls the frozen P2
  pipeline and exposes `P3DetailedResult.retrieval_result` directly from its
  internal `p2_result`; only the feasible P2 ranking is reranked. P3 retrieval
  estimates are therefore labelled `INHERITED_FROM_P2_INTERNAL_PIPELINE`, not
  an independently generated retrieval stage. P2-versus-P3 retrieval tests,
  p-values, effect intervals, and Holm activation are prohibited; their
  planned cells are explicit untested `NOT_APPLICABLE` rows.
- **Robustness rate**: within each family, JointAccept@1 is averaged over
  non-canonical variants labelled `reviewed_equivalent`; the reported primary
  robustness estimate is the equal-family macro mean. Variant-micro exposure
  is descriptive and separately labelled. A family with only canonical (or
  otherwise ineligible) variants has no robustness value; it is not assigned
  zero. Robustness comparisons use only the intersection of families with
  eligible non-canonical `reviewed_equivalent` exposure in both systems.

Infeasible-request detection and controlled-ambiguity detection are reported
as separate diagnostic estimands. They are never mixed into feasible-request
accuracy.

Descriptive family-clustered confidence intervals are also reported for the
trusted variant strata `canonical`, `paraphrase`, `Vietnamese`, `noisy`, and
`code-centric`, and for each trusted `family_metadata.workload_stratum`.
Absent cells are explicit `NOT_AVAILABLE` rows. Stratified cells receive no
hypothesis tests.

## Confidence intervals, paired tests, and effects

System estimates and paired effects use 95% family-clustered percentile
bootstrap confidence intervals. Comparisons use only common eligible families
and always define the signed effect as the second system minus the first.

An exact, two-sided McNemar test is used only when each system contributes
exactly one genuinely binary observation per family after collapse: one
variant and one execution, with value zero or one. Multiple variants or
repeated calls make the family outcome an aggregated rate even when its
realized mean happens to equal zero or one; such outcomes use paired,
two-sided Wilcoxon signed-rank without thresholding. Fractional family rates,
retrieval measures where a distinct comparison is structurally valid,
robustness, and latency also use Wilcoxon.

The latency population is every validated recommendation attempt with a
recorded total duration. This includes completed attempts and recorded
fallback, error, and timeout attempts. A full offline package with a missing
required duration is invalid; an in-memory row without a duration is excluded
as unavailable, never imputed. Durations follow the same repeat mean, variant
mean, and family mean hierarchy before pairing. Execution-level status counts
and runtime distributions remain separately labelled descriptive telemetry,
so repeated calls characterize variability without increasing effective
family N.

Every comparison reports an effect and confidence interval, never only a
p-value. Outputs include the risk or mean difference, its family-bootstrap
confidence interval, median paired difference, and matched-pairs rank-biserial
effect. Paired Cohen's `d_z` is included only when its variance is defined.
The direction and whether lower or higher values are favorable are explicit in
the immutable metric registry and each row: metric name, effect definition,
unit, `higher_is_better`, null value, and Holm domain. Effects are always
second system minus first. Consequently, a positive hard-constraint-violation
or latency effect favors the first system and is never labelled an
improvement.

## Multiplicity and inference policy

The predeclared primary endpoint, JointAccept@1, is the sole unadjusted primary
hypothesis. Secondary p-values receive Holm correction within the following
named domains:

| Domain | Endpoints |
| --- | --- |
| Quality/safety | acceptable profile accuracy, acceptable image accuracy, hard-constraint violation |
| Robustness | robustness rate |
| Retrieval | all declared Hit, Recall, nDCG cutoffs and MRR endpoints |
| Latency | paired latency endpoint |

P1-versus-P2 and eligible P2-versus-P3 hypothesis families are corrected
separately and are never pooled. Registry membership is predeclared by
comparison, endpoint, and domain and cannot change based on observed p-value
magnitude. Planned but unavailable/inapplicable/ineligible hypotheses are
recorded as untested with a reason; Holm uses only structurally active,
computable tests. The two-sided significance level is 0.05. Stratified
estimates are descriptive and excluded from Holm families.

Small effective family counts are handled explicitly:

- fewer than 20 eligible families emits `SMALL_EFFECTIVE_FAMILY_N`;
- fewer than 10 paired families may retain computable estimates, confidence
  intervals, effect sizes, and raw/adjusted p-values, but the decision is
  `WITHHELD_SMALL_N`, never a significance claim;
- fewer than two families omits confidence intervals and tests and emits an
  explicit insufficiency warning.

These gates prevent a nominal p-value from being interpreted as sufficient
evidence when the effective independent sample is inadequate.

## Machine-readable output and provenance

The output directory is exclusive-created and never silently overwritten:

```text
analysis-manifest.json
family-estimates.jsonl
system-estimates.jsonl
paired-comparisons.jsonl
stratified-estimates.jsonl
```

The Protocol-v5 statistical v1 schemas retain applicability, direction,
confidence-interval method, effective family N, family/variant/execution
counts, warning codes, and derived bootstrap seeds. Comparison rows bind every
p-value to its effect and confidence interval.

`analysis-manifest.json` records the timestamp, analysis Git revision,
protocol/statistics schema versions, dataset and raw-evidence checksums,
development or confirmatory role, split/freeze identity, relevant backend,
catalog and index versions, environment identity, bootstrap configuration,
all effective seeds, metric-direction registry, frozen/active Holm registry,
P3 gate/inference identity, and checksums of every derived output. The package
validator checks those bindings and rejects a modified or incomplete package.

## Current evidence status

No complete real Protocol-v5 evidence package suitable for this statistical
analysis is tracked in the repository. Therefore the empirical status remains
`NOT_EXECUTED`, and no Protocol-v5 accuracy, robustness, retrieval, latency,
effect, confidence-interval, p-value, or significance claim is made here.
Synthetic fixtures validate statistical behavior and package failure modes;
they are not thesis observations and must not appear in empirical results.
