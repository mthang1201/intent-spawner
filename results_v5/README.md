# Protocol-v5 results

No Protocol-v5 experiment has been executed in this package.

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
