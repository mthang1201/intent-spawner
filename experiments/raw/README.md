# Raw Experiment Results

Raw experiment records are newline-delimited JSON files written by
`.venv/bin/python -m experiments.runner` or the lower-level
`.venv/bin/python -m experiments.recorder`.

New local experiment outputs are ignored by default. Previously committed
local synthetic snapshots were removed while resetting the evaluation harness
for a clean re-run; this directory currently contains no run output.

Do not commit live raw outputs, pod JSON, events, notebook contents, datasets,
user names, secrets, broad environment dumps, or unsanitized Kubernetes
metadata.
