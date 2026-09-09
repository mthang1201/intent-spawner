# Protocol-v5 E5 Image Functional Contract

This document describes the repaired E5 functional-image harness. It is an
implementation contract, not a report of a new experiment. No container,
registry, Kubernetes, or confirmatory observation was collected by this repair.

## Functional semantics

A current E5 capability result records container lifecycle and capability
outcome separately. `SUCCESS` means that an approved bounded probe executed and
completed its concrete operation. `UNAVAILABLE` means that execution or a
supported capability implementation was unavailable. `FAILURE` means that the
container started but the operation, runtime API, deadline, identity check, or
cleanup failed.

The CUDA probe no longer treats interpreter startup as CUDA evidence. It must
import a supported CUDA-enabled framework and invoke either
`torch.cuda.device_count()` or `tf.config.list_physical_devices("GPU")`.
Missing supported packages or CPU-only builds are `UNAVAILABLE`; an exception
from a CUDA API after a CUDA-enabled build is observed is `FAILURE`.

Every executable probe is generated from the fixed capability registry, bound
to an exact administrator-catalog image ID/reference/digest, and checked for
finite timeout, CPU, and memory limits before runtime invocation. Docker runs
use a durable unique container name, an in-container alarm, runtime resource
limits, no network, reduced privileges, and exact-name stop/remove cleanup in a
`finally` path. Kubernetes runs poll pod status to `Succeeded` or `Failed`,
inspect the terminated container and runtime image identity, and delete the
exact pod in a `finally` path. Kubernetes per-pod network isolation still
depends on cluster NetworkPolicy; service-account token mounting and privilege
escalation are disabled by the harness.

## Recommendation provenance

Current functional evaluation accepts only the immutable
`VerifiedRecommendationRunProvenance` capability produced by the Protocol-v5
offline runner verifier. E5 reopens and revalidates the source package before
any runtime is constructed, copies the exact recommendation JSONL bytes into
the raw layer, and derives all extractor, index, retrieval, constraint/ranking,
catalog, dataset, split, backend, and optional P3 identities from that source.

Each functional record joins one-to-one to the source recommendation record ID
and records the source recommendation checksum, source system-configuration
identity checksum, selected catalog image digest, and observed platform when
available. The current validator rejects source checksum, record/image,
configuration, selected-digest, selected-platform, lifecycle, or origin
mismatches. Synthetic runners retain `SYNTHETIC_TEST` origin and current
`OBSERVED` evidence requires live runner construction, complete execution
receipts, runtime image/platform identity, and successful deterministic cleanup.

The final-audit regeneration adapter is schema-aware. For current v1.4
packages it revalidates the complete inventory-locked package, then reads
`raw/source-recommendation-run.json` and the exact copied bytes in
`raw/source-recommendations.jsonl`. It derives the source checksum, record
joins, system/configuration hashes, catalog mapping, selected digest, and
selected platform from those sealed artifacts and the persisted probe
manifest/results. It does not use `raw/environment.json` as recommendation
lineage and does not reopen an external catalog or split. The environment
file remains execution-environment metadata only.

## Version and evidence boundary

The current schemas are probe manifest/record v1.2 and functional
evaluation/metrics/run v1.4. Existing v1.0-v1.3 E5 packages are preserved
byte-for-byte and continue to validate through read-only compatibility paths.
The archived v1.3 packages are now classified `LEGACY_VALID`, not current or
claim-eligible, because they do not bind the Prompt 3 recommendation capability,
an exact recommendation-record join, or selected-image platform provenance.

The downstream legacy adapter retains its historical checksum-bound
environment/catalog/split references only for read-only compatibility. A
legacy package is never dispatched through the current adapter, upgraded to
`CURRENT_VALID`, or made claim-eligible.

A newly collected live v1.4 package may become current E5 evidence only when
all approved probes execute through a genuine Docker or Kubernetes runner and
all receipt, identity, and cleanup checks pass. Dry-run and synthetic packages
remain `DRY_RUN` or `INCOMPLETE` and are never claim-eligible. Real
confirmatory execution remains a separate, explicitly authorized activity.
