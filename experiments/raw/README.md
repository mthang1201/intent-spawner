# Raw Experiment Results

Raw experiment records are newline-delimited JSON files written by
`python3 -m experiments.runner` or the lower-level
`python3 -m experiments.recorder`.

This directory is intentionally ignored except for this README. Do not commit
live raw outputs, logs, pod JSON, events, notebook contents, datasets, user
names, secrets, or unsanitized Kubernetes metadata.
