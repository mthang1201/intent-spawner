# Protocol-v5 results

No Protocol-v5 experiment has been executed in this package.

The E3 B0-versus-P2 human-study protocol, deterministic assignment tooling,
content-free event and questionnaire instrumentation, session validation,
participant/task-aware analysis, aggregate tables and figures, provenance,
and report privacy audit are implemented, but no participant session has been
collected. The checked-in task bundle is a development-only draft requiring
independent review; synthetic event and questionnaire streams exist only in
tests and the temporary dry-run smoke harness. E3 therefore remains explicitly
`NOT_EXECUTED`, and no accuracy, time, effort, preference, ease, confidence, or
usability claim is available.

The real study-adapter interaction path is exercised only by a deterministic
researcher/CI smoke test using synthetic actions, closed synthetic responses, a
generated pseudonym, fake clocks, and temporary `DRY_RUN` finalization. This is
framework verification, not participant or Kubernetes evidence, and creates no
tracked E3 result.

The E4 independent resource-envelope harness now contains sixteen bounded
synthetic workload families, deterministic discrete CPU/memory calibration,
cgroup-v2 provenance, raw-evidence validation, interval-censored derivation,
and manual-review gating. The generated local E4 package is a `DRY_RUN` because the
required disposable Kubernetes context was unavailable. It contains no CPU,
memory, runtime, OOM, hardware, or cgroup observations, so E4 remains
`NOT_EXECUTED` for empirical purposes.

The P2/P3 component-scoring and family-level statistical harnesses are
implemented, but complete Prompt-3 gold and Prompt-5 raw evidence are not
tracked. Their empirical status therefore remains explicitly `NOT_EXECUTED`;
see `docs/evaluation/PROTOCOL_V5_COMPONENT_SCORING.md` and
`docs/evaluation/PROTOCOL_V5_STATISTICAL_ANALYSIS.md`. Synthetic tests validate
the harnesses and their failure modes but are not observed thesis evidence.

In particular, no Protocol-v5 accuracy, robustness, retrieval, latency,
confidence interval, effect-size, p-value, or significance result is currently
available. The statistical harness treats workload family as the independent
unit and never promotes variants or repeated model calls into additional
accuracy samples.

P3 records, if later present, do not by themselves authorize P2-versus-P3
inference. Ordinary paired P3 inference requires the authoritative frozen gate
to say `retained`; `not_retained` P3 remains descriptive only. Because the
implemented P3 reranker inherits the internal P2 retrieval result, the
statistical package labels that retrieval provenance as shared and never tests
P2 against P3 on retrieval endpoints.

Future runs use this immutable convention:

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

Raw observations are preserved separately from derived metrics and narrative
reports. Run directories and provenance files are exclusive-created. The only
overwrite escape hatch is an explicit development override for non-observed
development work; it is prohibited for confirmatory and `OBSERVED` packages.
`SHA256SUMS`, exclusive creation, and resume refusal after sealing provide
application-level immutability; an ignored local directory is not durable or
externally immutable storage. Approved observed E4 packages must be copied
byte-for-byte to the thesis evidence archive with its checksum manifest and a
recorded retention/object-lock receipt. Local dry runs are planning evidence
only and are not promoted into that archive as hardware observations.
