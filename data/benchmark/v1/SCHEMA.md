# Protocol-v5 Empirical Benchmark Schema (v1.0.0)

## Overview

The Protocol-v5 Benchmark dataset evaluates context-aware JupyterHub spawner recommenders.
Every ground-truth profile label is strictly grounded in **empirical resource profiling**
measured under real Linux cgroup v2 execution with anonymous memory isolation (`anon_peak_mib_max`),
rather than subjective annotation or unverified heuristic guesswork.

## File Structure

- `train.jsonl`: Formative training split (70% stratified clusters).
- `dev.jsonl`: Validation / hyperparameter tuning split (15% stratified clusters).
- `test.jsonl`: Immutable confirmatory evaluation split (15% stratified clusters).
- `manifest.json`: Cryptographic SHA-256 checksums and split distributions.

## Field Definitions

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique benchmark instance identifier (`{variant_id}`). |
| `family_id` | string | Identifier of the parent workload family. |
| `variant_id` | string | Identifier of the variant instance. |
| `variant_class` | string | Linguistic perturbation class (`canonical_en`, `informal_or_noisy`, `vietnamese`). |
| `language` | string | BCP-47 language tag (`en`, `vi`). |
| `source` | string | Benchmark origin (`ds1000` or `arcade`). |
| `source_dataset_id` | string | Formal catalog dataset identifier. |
| `source_case_id` | string | Upstream task identifier. |
| `library` | string | Primary target library (`pandas`, `numpy`, `matplotlib`, `scipy`, `scikit-learn`, `pytorch`, `tensorflow`, `arcade`). |
| `workload_stratum` | string | Workload stratum category. |
| `difficulty` | string | Task complexity (`easy`, `medium`, `hard`). |
| `nl_intent` / `prompt` | string | Natural language workload prompt submitted by user. |
| `code_context` | list[str] | Upstream code cell hints or imported symbols. |
| `dataset_size_gb` | float | Input data volume in Gigabytes. |
| `gt_profile` | string | Empirical ground-truth profile (`small`, `medium`, `large`). |
| `acceptable_profiles` | list[str] | Set of safe profiles accommodating execution without OOM. |
| `gt_image` | string | Preferred Jupyter notebook container image. |
| `acceptable_images` | list[str] | Images satisfying library and capability dependencies. |
| `gt_candidate` | string | Primary candidate ID (`{profile}-{image}`). |
| `acceptable_candidates` | list[str] | Safe (profile, image) deployment candidates. |
| `gold_structured_intent` | object | Expected semantic extraction intent payload. |
| `measurement_provenance` | object | Empirical cgroup v2 measurement telemetry: `peak_mib_max`, `anon_peak_mib_max`, `final_min_passing_profile`, `bumped_for_headroom`, `headroom_metric`. |

## Zero-Leakage Guarantee

All variants derived from the same parent workload family or sharing identical normalized prompts
are assigned to the exact same split cluster via disjoint-set connected component clustering.
Cross-split prompt duplicate count: **0 (strictly verified)**.
