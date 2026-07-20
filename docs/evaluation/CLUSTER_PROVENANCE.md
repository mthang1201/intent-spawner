# Cluster Evaluation Provenance

Evaluation date: 2026-07-20 (Asia/Ho_Chi_Minh)

Evaluated source commit: `39b69731a9aeaa85247c01e946e26656beae6e64`

Container image ID: `sha256:7891d2193ce73874c2fd48c8109d971a12252efa6b0e6e2ca709a9dc7e583ded`

The pre-existing `orbstack` cluster was inspected read-only and was not
mutated. It reported Kubernetes v1.33.9+orb1 and no Metrics API. All workload
mutations used the separately named `intent-spawner-eval` Minikube profile and
the `z2jh-context-demo` namespace.

## Mutation log

1. Created `intent-spawner-eval` with Minikube v1.36.0, Kubernetes v1.33.1,
   Docker driver, containerd 1.7.27, 6 Docker CPUs, and 6144MiB Docker memory.
2. Deleted that empty profile before any experiment because kubelet advertised
   host capacity rather than the Docker constraint.
3. Recreated it with `kubelet.system-reserved=cpu=2,memory=2Gi`, producing 6
   CPUs and 6088560Ki allocatable memory.
4. Enabled the pinned Metrics Server v0.7.2 digest documented in
   `CLUSTER_EXPERIMENT_PROTOCOL.md`.
5. Created `z2jh-context-demo`.
6. Created and deleted one bounded `metrics-probe`; `kubectl top` observed its
   250m-limited container at 251m and returned cluster-wide container metrics.
7. Loaded locally built evaluation images. Every analyzed pod used the image ID
   above.
8. Created and deleted 108 sanitized ground-truth pods, 180 comparative pods,
   and 108 capacity pods. Cleanup succeeded for all analyzed pods.

No ResourceQuota was installed. Admission configuration was unchanged across
capacity methods. Pod security used a non-root UID, RuntimeDefault seccomp,
dropped capabilities, no service-account token, no privilege escalation, and a
read-only root filesystem with a temporary `emptyDir` at `/tmp`.

## Raw and derived locations

- Ground truth: `results/cluster/raw/ground-truth-39b6973-seed20260720/`
- Comparative: `results/cluster/raw/comparative-39b6973-seed20260720/`
- Capacity: `results/cluster/raw/capacity-39b6973-seed20260721/`
- Derived tables and figures: `results/cluster/derived/`
- Independently observed envelopes: `benchmarks/observed_resource_envelopes.yaml`
- Scoped report: `docs/evaluation/CLUSTER_RESULTS.md`

The first 108-run ground-truth pilot is retained locally but excluded because
its environment metadata contained boot/machine identifiers. The runner was
corrected before the analyzed matrices; raw pilot files were neither edited nor
deleted. The pilot location is
`experiments/raw/cluster-pilot-ground-truth-9f6e326-unsanitized-env/`.

## Teardown

After validation, the disposable profile is deleted with:

```bash
minikube delete -p intent-spawner-eval
kubectl config use-context orbstack
```

## Capacity-v2 blocker-resolution rerun

On 2026-07-20, the capacity experiment was rerun independently from the
historical corpus. Protocol 2.0.0 was committed at
`f759c45a3246916d2a9f9048ffaab17bbbea6982`; its 20 GiB/20,480 MiB preflight
unit correction was committed at
`ca2e74b2043a5ea85a68119097d6c325fe84c294` before any recorded pod ran.

The rerun used only the disposable `intent-spawner-capacity-v2` profile and
`z2jh-context-demo` namespace. Its sanitized environment record retains the
Minikube driver, 6 CPU/6144 MiB/20 GiB profile inputs, Kubernetes v1.33.1,
containerd 1.7.27, kubelet system reservation, 6 CPU/6088560Ki allocatable node
resources, profile table, method order, Git commit, and exact local image ID
`sha256:bee0fc6942d2c9001053b1923d6ea23a2c34fb8735853ffc0ee806e5e5aede83`.
It excludes host paths, IP addresses, SSH material, node names, and machine
identifiers.

A single bounded non-root smoke pod completed with exit code 0 and zero
restarts, then was deleted. The full 9-batch/108-pod run completed with zero
pod failures and zero cleanup failures. Raw evidence is retained at
`results/cluster/raw/capacity-v2-ca2e74b-seed20260721/`; historical capacity
evidence remains separately labeled supplementary. After validation found no
remaining pods, `minikube delete -p intent-spawner-capacity-v2` removed the new
profile, `orbstack` was restored as the current context, and the pre-existing
`minikube` profile remained present.
