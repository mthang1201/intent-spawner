# P1 Architecture Freeze

This note records the recommendation architecture before P2 work. It is a
characterization of the current repository, not a runtime redesign. P1 means
the existing deterministic rule-based recommender. No Structured Intent,
retrieval, embedding, candidate, or reranking component exists in this freeze.

## System mapping

| Thesis system | Current repository mapping | Status in this work package |
| --- | --- | --- |
| B0 | [`helm/baseline-values.yaml`](../helm/baseline-values.yaml): the ordinary JupyterHub `profileList` lets the user manually choose `small`, `medium`, or `large`; no recommender is installed. | Deployment reference only; no B0 experiment is implemented. |
| P1 | [`recommender/rule_based.py`](../recommender/rule_based.py): `RuleBasedRecommender` consumes `RecommendationRequest` and returns `SpawnRecommendation`. | Frozen by exact-output regression tests. |
| P1 legacy interface | [`recommender/recommender.py`](../recommender/recommender.py): standalone `recommend_profile()` and CLI used by older tools. | Retained and parity-locked to deployed P1 output. |
| Future P2 | A new backend behind [`recommender/base.py`](../recommender/base.py) and [`recommender/registry.py`](../recommender/registry.py), producing the existing trusted final output contract. | Integration point only; not implemented. |

Protocol-v4's `static_profile_baseline` is a deterministic evaluation method
frozen to Medium plus `minimal-python`; it does not reproduce a person's manual
B0 choice. The older `experiments.methods.static_manual` is also a synthetic
stand-in that selects from benchmark acceptable labels. Neither is treated here
as an implemented B0 experiment.

## Deployed P1 execution path

1. [`helm/proposed-values.yaml`](../helm/proposed-values.yaml) mounts the packaged
   recommender and calls `install_jupyterhub(c)`. The rule overlay sets
   `RECOMMENDER_BACKEND=rule_based`.
2. [`recommender/deployment.py`](../recommender/deployment.py) validates the
   mounted package checksum/version and the selected backend before the Hub
   serves requests. [`recommender/registry.py`](../recommender/registry.py)
   resolves `rule_based` to `RuleBasedRecommender`.
3. The authenticated preview handler in
   [`recommender/jupyterhub_integration.py`](../recommender/jupyterhub_integration.py)
   accepts only `intent`, `dataset_size_gb`, and `code_context`, enforces size
   and numeric bounds, and constructs the frozen
   [`RecommendationRequest`](../recommender/models.py).
4. `AsyncRecommendationExecutor` invokes P1. Because P1 is local rather than
   network-bound, it executes directly and is wrapped in bounded operational
   metadata.
5. P1 lowercases intent plus code context, matches whole terms (with a special
   substring rule for dotted terms such as `.fit(`), applies the `0.5 GB` and
   `2.0 GB` thresholds, and adds deterministic data/training scores. Any GPU or
   deep-learning hit short-circuits to `gpu_or_large` with score `99`.
6. Image matching considers only the validated administrator catalog, ordered
   by descending priority and then image ID. A matching entry supplies its
   administrator-owned immutable reference; otherwise the catalog default is
   used.
7. [`PolicyValidator`](../recommender/policy.py) validates the final
   `SpawnRecommendation` type, schema version, profile, image ID/reference,
   policy version, and catalog version. Only a validated recommendation can be
   stored or previewed.
8. `RecommendationPreviewRuntime.issue()` maps `gpu_or_large` to the applied
   `large` profile, creates UUID preview/event IDs, and stores the bounded
   server-side decision. It does not store raw intent, code context, prompts, or
   provider output.
9. The form receives the recommendation and an opaque preview ID. Editing any
   input clears the browser fingerprint, hides the preview, disables submit,
   and requires a new preview. The browser check is usability protection; the
   server-side record remains authoritative.
10. `options_from_form()` requires the current preview version and an `accept`
    or allowlisted `override` action. It binds the confirmation to the
    authenticated user, applied profile, image ID, event, and policy/catalog/
    package generation.
11. `pre_spawn()` reloads that server-side record, revalidates user, TTL,
    generation, confirmation, submitted bindings, and current allowlists, then
    consumes the one-time preview. It never invokes a recommender. It applies
    CPU/memory from `PROFILE_RESOURCES` and the digest-pinned catalog image
    before KubeSpawner creates the pod.

Preview state is in memory, expires after 1,800 seconds, is capped at 1,000
entries, is isolated per user and browser tab, and fails closed after a Hub
restart or package/policy/catalog generation change. Failed binding checks do
not consume the preview; a successful pre-spawn does. Pod annotations and audit
logs contain bounded identifiers, versions, selected profile/image, fallback
category, attempts, and latency—not raw recommendation inputs or reasons.

The optional [`helm/reprovision-values.yaml`](../helm/reprovision-values.yaml)
flow calls the same `issue()`, `options_from_form()`, and `pre_spawn()` path for
a stop-and-recreate operation. It adds restart acknowledgement and current
server-event binding, but does not introduce a second recommender or token
authority.

## Deterministic policy and ownership

| Concern | Authoritative modules or data | P2 reuse boundary |
| --- | --- | --- |
| Input/output contracts | [`recommender/models.py`](../recommender/models.py), [`recommender/base.py`](../recommender/base.py) | P2 should accept `RecommendationRequest` through the backend protocol and emit the existing `SpawnRecommendation` after its own validated pipeline. |
| P1 scoring and vocabulary | [`recommender/rule_based.py`](../recommender/rule_based.py) | Comparator behavior only. P2 may consume separately exposed deterministic knowledge later, but must not refactor or change this execution path silently. |
| Images | [`recommender/image-catalog.yaml`](../recommender/image-catalog.yaml) and its strict loader | Reuse IDs, descriptions, capabilities, versions, and immutable references. Any derived P2 catalog/index needs separate explicit version and provenance. Final references must resolve from this administrator data. |
| Profiles/resources | `PROFILES` in P1 and `PROFILE_RESOURCES` in [`recommender/jupyterhub_integration.py`](../recommender/jupyterhub_integration.py) | Reuse allowlisted identifiers and final mappings; do not allow a model to create quantities. |
| Final trust boundary | [`recommender/policy.py`](../recommender/policy.py) | Remains mandatory after P2. It validates one final recommendation; it is not a candidate-filtering or ranking engine. |
| Optional dynamic policy | [`recommender/resource-policy.yaml`](../recommender/resource-policy.yaml), [`recommender/dynamic_resources.py`](../recommender/dynamic_resources.py), and [`helm/dynamic-values.yaml`](../helm/dynamic-values.yaml) | Reusable bounded quantity, GPU, quota-cap, step, alias, fallback, version, and semantic-hash knowledge. Catalog mode remains the default. |
| Network adapters | [`recommender/external_llm.py`](../recommender/external_llm.py), [`recommender/self_hosted_llm.py`](../recommender/self_hosted_llm.py), and [`recommender/reliability.py`](../recommender/reliability.py) | Existing non-P2 backends. They schema-validate model output locally, resolve images from the same catalog, and deterministically fall back to P1. They are not P2 or P3. |
| Deployment provenance | [`recommender/deployment.py`](../recommender/deployment.py) and Helm backend overlays | Extend explicitly for any future P2 backend/version while retaining fail-closed startup validation. |

`PolicyValidator` cannot substitute for the future P2 hard-constraint stage: it
does not evaluate a retrieved candidate set, preferences, feasibility, or rank
ordering. P2 must perform deterministic constraint filtering and deterministic
ranking before it creates the trusted final candidate and converts it to
`SpawnRecommendation`. The existing validator, preview, confirmation, and
pre-spawn path then remain unchanged.

## Evaluation tooling and evidence boundary

- [`evaluation_v4/recommenders.py`](../evaluation_v4/recommenders.py) maps
  `rule_based_mapping`, `rule_based_context`, and `rule_based_intent_only` to
  `RuleBasedRecommender`. The context method matches deployed P1 inputs; the
  intent-only method is an ablation.
- [`evaluation_v4/run_recommenders.py`](../evaluation_v4/run_recommenders.py),
  [`evaluation_v4/schemas.py`](../evaluation_v4/schemas.py), and
  [`evaluation_v4/analyze.py`](../evaluation_v4/analyze.py) run, validate, and
  analyze the versioned offline matrix.
- [`benchmarks/intent-gold-v4.yaml`](../benchmarks/intent-gold-v4.yaml) is the
  frozen synthetic bilingual gold dataset with explicit schema, catalog hash,
  label-policy version, and provenance.
- [`evaluation_v4/run_system.py`](../evaluation_v4/run_system.py) and
  `plan_system.py` cover planned/observed JupyterHub and Kubernetes trials.
- [`experiments/methods.py`](../experiments/methods.py) and
  `experiments/runner.py` are the older local synthetic framework and call the
  legacy `recommend_profile()` implementation. The new parity regression guard
  prevents that historical path from drifting away from P1 unnoticed.

Existing raw and derived evidence is observed history, not a golden-output file
to regenerate when P1 tests are added. This freeze changes no evidence artifact
and manufactures no result.

## Frozen uncertainties and limitations

- The repository has two copies of the rule logic. Tests now detect divergence,
  but this work deliberately does not deduplicate them because that could change
  the P1 comparator or historical tooling.
- P1 term matching is lexical and English-oriented; unsupported paraphrases and
  languages may fall through. This is characterized behavior, not repaired here.
- `gpu_or_large` is a recommendation label, not proof of GPU availability. The
  default catalog path applies it as Large and the shipped dynamic policy allows
  no GPU resources.
- Preview storage is process-local. Multi-replica shared state is not provided.
- Manual overrides are allowlisted but are user decisions, not P1 outputs.
