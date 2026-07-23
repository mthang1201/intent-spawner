# Protocol-v3 local image build audit

Both v3 Dockerfiles were built for `linux/arm64` and passed manifest-only
container smoke validation. The source working tree was dirty and no intended
registry was configured, so the builds were deliberately not pushed.

The local Docker image IDs in `build-provenance.json` are configuration
digests, not registry manifest digests. They are not valid substitutes for a
deployable `registry/repository@sha256:<manifest-digest>` reference. Therefore
the immutable-image gate remains blocked and the Helm placeholder is unchanged.

No Kubernetes command, Helm install/upgrade, pod creation, workload execution,
or memory-pressure allocation was performed during this build stage.
