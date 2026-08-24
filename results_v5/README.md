# Protocol-v5 results

No Protocol-v5 experiment has been executed in this package.

The P2/P3 component-scoring harness is implemented, but complete Prompt-3 gold
and Prompt-5 raw evidence are not tracked. Its empirical status therefore
remains explicitly `NOT_EXECUTED`; see
`docs/evaluation/PROTOCOL_V5_COMPONENT_SCORING.md`. Synthetic tests validate
the harness and error taxonomy but are not observed thesis evidence.

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
