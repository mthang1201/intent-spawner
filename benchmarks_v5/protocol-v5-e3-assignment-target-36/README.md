# Protocol-v5 E3 Assignment Manifest (Development / Readiness)

## Status: READINESS ONLY / DEVELOPMENT

- **Target Participants**: 36
- **Task Set ID**: `protocol-v5-e3-draft-v1` (`benchmarks_v5/user-study-draft-v1.yaml`)
- **Task Set Stage**: `development`
- **Task Set Status**: `draft`
- **Equivalence Review Status**: `needs_review`
- **Seed**: `20260827`
- **Freeze ID**: `development-unfrozen`

### Notice on Confirmatory Execution

This assignment schedule was generated from the draft development task set for harness validation, invariant testing, and dry-run readiness verification.

Because confirmatory execution strictly fails closed when presented with a development task set (enforced by invariant test Check 9), this manifest **CANNOT** be used for confirmatory data collection.

### Generating the Confirmatory Assignment

Once independent human researcher equivalence and gold review is completed and the task set is frozen (`stage: confirmatory`, `status: frozen`), generate the authoritative confirmatory assignment with:

```bash
python -m evaluation_v5.user_study.runner generate-assignment \
  --task-set benchmarks_v5/user-study-v1.yaml \
  --output benchmarks_v5/protocol-v5-e3-assignment-confirmatory-36 \
  --seed 20260827 \
  --confirmatory \
  --freeze-id <frozen_recommender_freeze_id> \
  --config-identity <config_identity_json> \
  --environment-identity <environment_identity_json>
```
