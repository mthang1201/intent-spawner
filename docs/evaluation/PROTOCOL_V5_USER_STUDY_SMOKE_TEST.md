# Protocol-v5 E3 Study-Hub Smoke Test

## Boundary

This procedure is for a researcher-controlled test account only. It is not
participant recruitment, confirmatory execution, or observed E3 evidence. Use
the development task bundle, generated `P-<12 hex>` account, and
`DRY_RUN` status. Do not enter a real name, email, username, secret, notebook
content, or participant response. Do not write the output under `results_v5`.

The no-cluster integration check is:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation_v5.user_study.smoke
```

It exercises the real study session runtime, condition forms, real P2 preview
runtime, server confirmation, B0/P2 pre-spawn selection application,
`notebook_ready`, append-only staging, and immutable finalization in a temporary
directory. Passing this check does not prove browser, authenticator, ingress,
PVC, or live Kubernetes behavior.

## Controlled deployment smoke

1. Create a secret-free JSON file describing the exact study Helm values and
   configuration identity. Never include credentials or authenticator config.
2. Prepare a development fairness identity using the actual cluster label:

   ```bash
   PYTHONPATH=. .venv/bin/python -m evaluation_v5.user_study \
     prepare-environment \
     --output /tmp/e3-smoke-environment.json \
     --environment-id e3-researcher-smoke \
     --kubernetes-environment-id researcher-smoke-cluster \
     --freeze-id development-unfrozen \
     --config-identity /tmp/e3-smoke-config.json
   ```

3. Generate a one-account development assignment with a recorded seed:

   ```bash
   PYTHONPATH=. .venv/bin/python -m evaluation_v5.user_study \
     generate-assignments benchmarks_v5/user-study-draft-v1.yaml \
     --output-dir /tmp/e3-smoke-prepared \
     --study-id e3-researcher-smoke \
     --participant-count 1 \
     --seed 20260827 \
     --consent-version consent-smoke-v1 \
     --freeze-id development-unfrozen \
     --environment-identity /tmp/e3-smoke-environment.json \
     --config-identity /tmp/e3-smoke-config.json
   ```

4. Inspect the assignment and browser projection. Confirm the projection has no
   `gold`, requirements, acceptable candidates, or preferred candidate fields.
5. Install only into the isolated smoke namespace. The explicit development
   flag is deliberately noisy and must never be used for confirmatory work:

   ```bash
   NAMESPACE=e3-researcher-smoke \
     RELEASE=e3-researcher-smoke \
     bash scripts/install-user-study.sh --execute \
       --allow-development-smoke /tmp/e3-smoke-prepared
   ```

   The release name must also be unique. The JupyterHub chart creates
   cluster-scoped scheduler resources whose names include the release, so
   reusing a normal deployment release name would either fail ownership checks
   or compromise namespace isolation.

6. Configure the smoke authenticator account name to be the issued pseudonym
   from the assignment manifest. Complete all eight interactions. Use synthetic
   task descriptions only; do not enter personal or production information.
7. For at least one B0 task, make and correct a manual profile/image selection.
   Verify there is no recommendation or preview request. For at least one P2
   task, request a preview and either accept it or exercise the explicit
   override. Allow one spawn to reach ready; a separate controlled spawn may be
   allowed to miss readiness to exercise the 180-second path.
8. Export the study PVC staging files to an access-controlled temporary
   directory. Verify `events.jsonl`, `sessions.jsonl`, and completion markers
   exist; `exclusions.jsonl` is expected only if an exclusion was recorded. Do
   not copy ordinary Hub logs into research evidence.
9. Validate the events and finalize outside the repository as `DRY_RUN`:

   ```bash
   PYTHONPATH=. .venv/bin/python -m evaluation_v5.user_study validate-events \
     /tmp/e3-smoke-export/events.jsonl \
     --task-set benchmarks_v5/user-study-draft-v1.yaml \
     --assignments /tmp/e3-smoke-prepared/assignment-manifest.json

   PYTHONPATH=. .venv/bin/python -m evaluation_v5.user_study finalize \
     --run-id e3-researcher-smoke \
     --task-set benchmarks_v5/user-study-draft-v1.yaml \
     --assignments /tmp/e3-smoke-prepared/assignment-manifest.json \
     --events /tmp/e3-smoke-export/events.jsonl \
     --sessions /tmp/e3-smoke-export/sessions.jsonl \
     --execution-status DRY_RUN \
     --output-dir /tmp/e3-smoke-finalized
   ```

10. Check each trial begins with one server-owned `task_shown`, has contiguous
    indexes and one terminal `confirm` or `cancel`, and has at most one matching
    `notebook_ready`. Confirm B0 contains no intent/preview/override events,
    final confirmed IDs match the spawned selection, timing statuses are
    explicit, and the provenance fairness checksum matches the assignment.
11. Delete the isolated namespace and temporary smoke artifacts according to
    the researcher's approved operational process. The study PVC is retained by
    design until that explicit cleanup.

## What still requires a deployment check

The deterministic integration test cannot establish live authenticator
mapping, ingress/XSRF behavior, browser reload behavior across a real Hub pod,
PVC storage-class semantics, Kubernetes readiness reporting, or bounded server
cleanup. Those are verified only by the controlled deployment smoke above.
Institutional ethics, consent, retention, access, withdrawal, and incident
requirements remain the researcher's responsibility.
