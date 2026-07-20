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
