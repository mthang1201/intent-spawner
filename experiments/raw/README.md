# Raw Experiment Results

Raw experiment records are newline-delimited JSON files written by
`.venv/bin/python -m experiments.runner` or the lower-level
`.venv/bin/python -m experiments.recorder`.

New local experiment outputs are ignored by default. This artifact intentionally
keeps only the sanitized synthetic snapshots listed below so the published
tables and figures can be regenerated from raw records in a fresh clone:

- `20260719T140417Z-smoke-171688c0`: one smoke run.
- `20260719T140423Z-matrix-783b4141`: dry-run matrix planning output.
- `20260719T140431Z-matrix-aed48949`: full local synthetic matrix used for the
  committed derived results.

Do not commit live raw outputs, pod JSON, events, notebook contents, datasets,
user names, secrets, broad environment dumps, or unsanitized Kubernetes
metadata.
