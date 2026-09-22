# Defense-ready demo Helm wiring for recommender backends

This is reproducible research/demo deployment wiring, not a production
multi-tenant deployment claim.

## Architecture

The repository layers values onto the upstream Zero to JupyterHub 4.0.0 chart;
it does not maintain a chart fork. The Hub image is pinned to the official
`quay.io/jupyterhub/k8s-hub:4.0.0` multi-architecture digest. It contains
JupyterHub 5.2.1 and PyYAML 6.0.2. All other recommender imports use the Python
standard library, so Hub startup never runs `pip install`.

The deployment has three inputs:

1. `helm/proposed-values.yaml` provides the shared Hub integration, mandatory
   ConfigMap mount, default rule backend, async preview endpoint, and one-time
   confirmation flow.
2. Exactly one backend values file provides backend-specific environment and
   Kubernetes `secretKeyRef` entries.
3. `scripts/recommender_package.py` builds the externally managed ConfigMap and
   generates rollout values containing the exact package SHA-256 and version.

Startup is fail-closed:

```text
required ConfigMap and Secret refs resolved by kubelet
  -> mounted package allowlist/version/checksum validation
  -> backend/endpoint/model/credential/security validation
  -> timeout/deadline/retry/backoff/temperature validation
  -> catalog and policy validation
  -> JupyterHub HTTP server and readiness endpoint
```

A missing ConfigMap or required Secret/key prevents container creation. Empty
credentials, invalid package content, unknown backends, unsafe endpoints, and
invalid numeric configuration terminate JupyterHub during config loading. No
credential value is included in startup errors or logs.

## Backend values

### Rule based

`helm/recommender-rule-based-values.yaml` sets only
`RECOMMENDER_BACKEND=rule_based`; it has no endpoint, network credential, or
Secret dependency.

```bash
BACKEND_VALUES=helm/recommender-rule-based-values.yaml \
  bash scripts/install-proposed.sh
```

### P2 hybrid

`helm/recommender-p2-values.yaml` selects the frozen local StructuredIntent,
hybrid retrieval, and deterministic constraint/ranking backend. Its default
configuration requires no endpoint or credential:

```bash
BACKEND_VALUES=helm/recommender-p2-values.yaml \
  bash scripts/install-proposed.sh
```

## ConfigMap package and deterministic rollout

`recommender/deployment.py` is the single runtime allowlist. The generated
ConfigMap contains 25 files: runtime Python modules, the Hub integration, image
catalog, dynamic-resource module/policy, and token-pricing support. It excludes
tests, `__pycache__`, documentation, and experiment results. Generation fails
above a conservative 700 KiB
payload threshold, leaving headroom below Kubernetes' 1 MiB object limit.

Inspect its allowlist, size, version, and checksum without touching a cluster:

```bash
.venv/bin/python scripts/recommender_package.py verify
```

The checksum hashes every allowlisted filename, byte length, and content in a
stable order. `scripts/install-proposed.sh` performs the supported update flow:

1. render and apply the externally managed ConfigMap;
2. generate Helm values containing the same checksum/version;
3. place that checksum in the Hub pod-template annotation and environment;
4. run `helm upgrade --wait`.

Identical package bytes produce the same recommender pod-template fields and no
recommender-driven rollout. Any allowlisted content change produces a new
Deployment ReplicaSet, so the old process cannot keep serving already imported
Python. At startup, the replacement Hub recalculates the mounted bytes and
refuses to start if they do not match the pod-template checksum.

Do not update `intent-spawner-recommender` with ad-hoc `kubectl edit` or a
standalone ConfigMap apply: an external ConfigMap cannot change a Helm-owned pod
template by itself. Always run package generation and Helm upgrade together via
`install-proposed.sh`, or reproduce both steps in GitOps. A GitOps system should
render the generated checksum into the Helm release input whenever it
regenerates the ConfigMap.

Package checksum/version, backend version, policy version, and catalog version
are included in privacy-minimized recommendation audit metadata and spawned-pod
annotations. Secrets are never logged.

## Credential rotation

Update the existing Secret using the same `kubectl create ... --dry-run | apply`
pattern, then restart and wait for Hub because Secret-backed environment values
are read only when a container is created:

```bash
kubectl rollout restart deployment/hub --namespace z2jh-context-demo
kubectl rollout status deployment/hub --namespace z2jh-context-demo --timeout=10m
```

Keep the old provider credential valid until the replacement is Ready, verify
a preview, and then revoke it. The rollout annotation deliberately does not
hash Secret data, so credentials never enter Helm values or manifests.

## Safe backend switching

Create the destination Secret first, then run one Helm upgrade with the new
backend file. Do not use Helm `--reuse-values`, which can retain environment
entries from the previous backend. The install script checks every mandatory
`secretKeyRef` before changing the ConfigMap or release and waits for the new
Hub to become Ready. Switching to rule based removes all network backend
environment and Secret references because each release is rendered from the
base and selected overlays from scratch.

## Rollback

Select the last known-good recommender source and matching Helm values, then
rerun `scripts/install-proposed.sh` with the previous backend overlay. This
restores the old ConfigMap bytes/checksum and creates a replacement Hub. If only
configuration changed, `helm rollback context-demo <revision> --namespace
z2jh-context-demo --wait` is valid only when the external ConfigMap still
matches that revision. Otherwise regenerate and apply the package from that
source revision first; startup validation intentionally rejects mixed versions.

## Validation

```bash
.venv/bin/python -m pytest -q tests/test_helm_recommender_deployment.py
bash scripts/check.sh
```

Tests use local HTTP mocks and dummy credentials only. They render every backend
overlay, check Secret references, exercise startup failures, prove checksum
stability/change behavior, and invoke all three backends through the shared
policy contract. Run cluster-mutating rollout validation only on the local demo
cluster in `z2jh-context-demo`, never on a shared or production cluster.

Observed local rollout evidence from 2026-08-07 is preserved in
`results/deployment-rollout-2026-08-07/`. It is explicitly local evidence, not a
production result.
