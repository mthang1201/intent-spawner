# Protocol-v3 implementation audit and readiness record

## Status

Audit date: 2026-07-23.

Conclusion: **implementation corrections completed; real experiment blocked
before cluster inspection**.

No Kubernetes context/API inspection, cluster installation, pod creation,
workload execution, JupyterHub trial, or memory-pressure allocation was
performed in this audit. The two custom containers were built locally and
passed manifest-only validation, but they were not pushed because no intended
registry was configured. The source tree was not clean or fully represented by
the recorded Git commit. Therefore immutable deployment references could not be
created defensibly.

The blocked readiness evidence is under
`results/cluster/preflight-v3/20260723T065447Z-readiness-blocked/`. Local build
provenance is under
`results/cluster/build-v3/20260723T065130Z-local-audit/`.

## Audit method

The audit independently compared:

- `benchmarks/workloads-v3.yaml`;
- the workload, direct-pod, and JupyterHub runners;
- the result schema and analysis;
- the inline Helm selector;
- both v3 Dockerfiles;
- protocol v3.0.0;
- the v3 tests and the full repository checks;
- all preserved v2 evidence checksums.

The scientific design was not changed. Workloads, target bands, profile
resources, seeds, rotations, repeats, trial counts, calibration rules,
replacement rules, stop rules, estimands, bootstrap sample count, McNemar
family, Holm correction, and hold-out/robustness separation remain frozen.

## Material findings and resolutions

| Finding | Why it mattered | Resolution |
| --- | --- | --- |
| Critical v3 source files were untracked while execution recorded only `HEAD`. | A commit could not identify the code that produced evidence. | Execution now requires every critical input to be tracked and the tracked tree to be clean. Current readiness therefore blocks. |
| The clean-tree check ignored untracked v3 execution inputs. | Uncommitted scientific code could be omitted from provenance. | The frozen-commit gate explicitly checks critical paths are tracked in addition to tracked-tree cleanliness. |
| Ground-truth, comparative, and JupyterHub runners did not require a passing calibration artifact. | Hold-out work could run even when the preregistered calibration gate failed. | Later phases now require checksum-validated prerequisite directories; the passing calibration gate is evaluated before execution. Comparative also requires complete ground truth, and JupyterHub requires all direct phases. |
| Trial records omitted the Git commit, image digest, configuration identity, and explicit failure category. | Per-trial provenance and failure accounting were incomplete. | Schema v3 records now require commit/image provenance, safe input hash, supporting-evidence hashes, configuration identity where applicable, and a fail-closed category. |
| Schema validation accepted many malformed types and impossible timestamps. | Malformed evidence could enter analysis silently. | Manifest and record validation now enforce strata, fields, resource bounds, types, immutable references, unit/profile consistency, and timestamp ordering. |
| Duplicate/corpus completeness checks were absent. | Missing, extra, duplicated, or overwritten trials could be analyzed. | `cluster_evaluation/evidence_v3.py` validates matrices, originals/replacements, sidecars, IDs, seeds, pairings, commits, images, supporting hashes, and complete `SHA256SUMS` coverage. |
| Analysis did not verify source evidence checksums and silently filtered infrastructure-invalid rows. | Derived results were not fully traceable and exclusions were only summarized as a count. | Analysis now validates each evidence corpus first and emits explicit failure/exclusion accounting plus analysis-input hashes and an output integrity manifest. |
| The reported confirmatory cluster count was eight although only six core workloads form the primary stratum. | The power-boundary metadata contradicted the preregistered primary stratum. | Reporting was corrected to six clusters and 30 repeated core observations. Robustness cases remain separate. |
| Required confusion tables and figures were not generated. | The documented output set was incomplete. | Analysis now emits confusion matrices, McNemar/Holm tables, failure accounting, a descriptive SVG, and end-to-end tables when evidence exists. |
| JupyterHub `kubectl exec` had no client-side timeout. | A failed in-container deadline could leave a workload running. | The harness now applies workload deadline plus the registered controller allowance, then stops and cleans up through the Hub. |
| JupyterHub output directory was created before preflight. | A failed preflight left an ambiguous partial run directory. | JupyterHub preflight now passes before the append-only experiment directory is created. |
| V3 Docker builds sent a broad repository context and used a `latest@digest` base spelling. | Unrelated artifacts entered the context, and the reference violated the requested no-tag digest form. | Dockerfile-specific allowlists were added; bases use `registry/repository@sha256:<digest>` with no tag. |
| Local Docker image IDs were liable to be mistaken for registry manifest digests. | A configuration digest is not a deployable repository manifest reference. | Build provenance records both fields distinctly and leaves the immutable reference null. No placeholder was replaced with a false digest. |
| The Helm single-user image remains `intent-spawner-jupyter-eval:v3`. | Rendered v3 deployment evidence is mutable. | `image_policy_v3.py` now fails on the placeholder, tags, missing/short digests, and unexpected repositories. Pinning remains blocked pending a registry push. |

## Design checks

- Workload IDs are unique: four calibration, six core hold-out, and two
  robustness workloads.
- Calibration profiles are manifest-separated; hold-out workloads cannot
  define calibration profiles.
- Ground truth forces profiles and never invokes a method.
- Comparative decisions never read operational ground-truth output.
- Intent-only passes zero dataset size and empty code context.
- Static default applies Medium uniformly in v3 because no workload policy
  override is present.
- Context-aware receives only the documented synthetic intent, size hint, and
  code-context hints.
- No history store, history feature, history method, or history claim exists.
- Memory padding uses retained `bytearray` blocks and writes every 4 KiB page.
- Cgroup-v2 `memory.current` is required for targeting; missing cgroup files fail
  the workload.
- The direct pod has an active deadline, non-root execution, no service-account
  token, a read-only root filesystem, a bounded `/tmp`, and dropped
  capabilities.
- Deterministic checksums exclude timing and resource observations and are
  paired by workload/repeat seed.
- McNemar keys are workload/repeat pairs; the Holm family contains success and
  OOM contrasts against both preregistered baselines.
- The bootstrap resamples the six core workload clusters 10,000 times.
- Wilson intervals remain explicitly labeled descriptive trial-level intervals
  because repeats are clustered; inferential emphasis remains on clustered
  estimates.

## Claim-to-evidence boundary

| Claim | Status | Supporting artifact | Limitation | Safe to retain |
| --- | --- | --- | --- | --- |
| Protocol-v3 implementation exists | Implemented and unit tested | v3 sources; `tests/test_resource_envelope_v3.py` | Not a cluster result | Yes, with status label |
| Matrices contain 24/120/120/45 planned trials | Dry-run verified | `make v3-dry-run` | No trials executed | Yes |
| V3 images can be built locally | Observed locally | local build provenance | Dirty tree; not pushed; no manifest digest | Yes, narrowly |
| V3 images are immutable deployment evidence | Not established | immutable-image policy failure | Registry not configured | No |
| Safety preflight passed | Not evaluated | blocked readiness report | Image/source gates failed first | No |
| Synthetic memory bands were achieved | Not evaluated | no calibration evidence | No pressure workload ran | No |
| Any method reduces OOM or waste | Not evaluated | no hold-out evidence | No v3 result exists | No |
| Context-aware is superior | Not evaluated | no comparative evidence | Prior v2 evidence does not establish it | No |
| JupyterHub applies profiles end to end | Not evaluated in v3 | no end-to-end evidence | Dry-run/selector code is insufficient | No |
| V2 evidence remains intact | Verified | `cluster_evaluation.raw_integrity` | Applies only to preserved v2 corpus | Yes |
| Production, history-aware, predictive, or autoscaling effectiveness | Out of scope/not evaluated | protocol limitations | Synthetic single-node design | No |

## Required remediation before any real trial

1. Review and commit only the intended repository changes, leaving unrelated
   `.codex-work` and deliverables out of the experiment commit.
2. Configure the intended direct-pod and Jupyter image repositories.
3. Rebuild both images from that clean commit and push them once.
4. Record the registry manifest digests and configuration digests separately.
5. Pin the Jupyter image in `singleuser.image.name` with an empty `tag`.
6. Render chart 4.0.0 and pass `image_policy_v3.py` against the render.
7. Run a new append-only read-only preflight report.
8. Continue only if every safety check passes.

## Reproduction sequence

The following sequence is intentionally fail-closed. Replace the angle-bracket
values before running a command; never use the local configuration IDs from the
blocked build as manifest digests.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest recommender tests
bash scripts/check.sh
python -m cluster_evaluation.raw_integrity
make v3-dry-run

git status --short
git rev-parse HEAD
docker buildx build --platform <cluster-platform> --push --provenance=false \
  --build-arg VCS_REF="$(git rev-parse HEAD)" \
  --tag <direct-registry/repository>:build-$(git rev-parse --short=12 HEAD) \
  --metadata-file <new-direct-metadata.json> \
  --file cluster_evaluation/Dockerfile.v3 .
docker buildx build --platform <cluster-platform> --push --provenance=false \
  --build-arg VCS_REF="$(git rev-parse HEAD)" \
  --tag <jupyter-registry/repository>:build-$(git rev-parse --short=12 HEAD) \
  --metadata-file <new-jupyter-metadata.json> \
  --file cluster_evaluation/Dockerfile.jupyter-v3 .

# Pin the returned manifest digest in helm/experiment-v3-values.yaml, then:
helm template context-demo jupyterhub \
  --repo https://hub.jupyter.org/helm-chart/ --version 4.0.0 \
  --namespace z2jh-context-demo \
  --values helm/experiment-v3-values.yaml \
  > <new-rendered-v3.yaml>
python -m cluster_evaluation.image_policy_v3 \
  --direct-image <direct-registry/repository@sha256:digest> \
  --expected-direct-repository <direct-registry/repository> \
  --expected-jupyter-repository <jupyter-registry/repository> \
  --rendered-helm <new-rendered-v3.yaml>

python -m cluster_evaluation.runner_v3 \
  --kind calibration --experiment-id <new-calibration-id> \
  --image <direct-registry/repository@sha256:digest> \
  --preflight-only --preflight-report <new-preflight-report.json>
python -m cluster_evaluation.runner_v3 \
  --kind calibration --experiment-id <new-calibration-id> \
  --image <direct-registry/repository@sha256:digest> --execute
python -m cluster_evaluation.analyze_v3 \
  --calibration <calibration-directory> --calibration-only \
  --out <new-calibration-analysis-directory>

python -m cluster_evaluation.runner_v3 \
  --kind ground-truth --experiment-id <new-ground-id> \
  --image <direct-registry/repository@sha256:digest> \
  --calibration-evidence <calibration-directory> --execute
python -m cluster_evaluation.runner_v3 \
  --kind comparative --experiment-id <new-comparative-id> \
  --image <direct-registry/repository@sha256:digest> \
  --calibration-evidence <calibration-directory> \
  --ground-truth-evidence <ground-truth-directory> --execute

python -m cluster_evaluation.jupyterhub_v3 \
  --experiment-id <new-jupyterhub-id> \
  --image <jupyter-registry/repository@sha256:digest> \
  --calibration-evidence <calibration-directory> \
  --ground-truth-evidence <ground-truth-directory> \
  --comparative-evidence <comparative-directory> \
  --out <new-jupyterhub-directory> --execute

python -m cluster_evaluation.evidence_v3 \
  --experiment calibration=<calibration-directory> \
  --experiment ground-truth=<ground-truth-directory> \
  --experiment comparative=<comparative-directory> \
  --experiment jupyterhub=<jupyterhub-directory>
python -m cluster_evaluation.analyze_v3 \
  --calibration <calibration-directory> \
  --ground-truth <ground-truth-directory> \
  --comparative <comparative-directory> \
  --end-to-end <jupyterhub-directory> \
  --out <new-analysis-directory>
python -m cluster_evaluation.raw_integrity
```
