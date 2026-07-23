.PHONY: check validate-cluster-results validate-raw-integrity capacity-dry-run v3-validate v3-dry-run v3-image-policy regenerate-cluster-results

check:
	bash scripts/check.sh

validate-cluster-results:
	.venv/bin/python -m cluster_evaluation.validate_artifacts

validate-raw-integrity:
	.venv/bin/python -m cluster_evaluation.raw_integrity

capacity-dry-run:
	.venv/bin/python -m cluster_evaluation.capacity_runner \
		--experiment-id capacity-v2-dry-run \
		--image intent-spawner-cluster-eval:capacity-v2 \
		--dry-run

v3-validate:
	.venv/bin/python -m benchmarks.resource_envelope_runner --validate-only

v3-dry-run: v3-validate
	.venv/bin/python -m cluster_evaluation.runner_v3 \
		--kind calibration --experiment-id v3-calibration-dry-run \
		--image example.invalid/intent-spawner-v3@sha256:abc --dry-run
	.venv/bin/python -m cluster_evaluation.runner_v3 \
		--kind ground-truth --experiment-id v3-ground-truth-dry-run \
		--image example.invalid/intent-spawner-v3@sha256:abc --dry-run
	.venv/bin/python -m cluster_evaluation.runner_v3 \
		--kind comparative --experiment-id v3-comparative-dry-run \
		--image example.invalid/intent-spawner-v3@sha256:abc --dry-run
	.venv/bin/python -m cluster_evaluation.jupyterhub_v3 \
		--experiment-id v3-jupyterhub-dry-run --dry-run

v3-image-policy:
	.venv/bin/python -m cluster_evaluation.image_policy_v3

regenerate-cluster-results: validate-cluster-results
	.venv/bin/python -m cluster_evaluation.analyze \
		--ground results/cluster/raw/ground-truth-39b6973-seed20260720 \
		--comparative results/cluster/raw/comparative-39b6973-seed20260720 \
		--capacity results/cluster/raw/capacity-v2-ca2e74b-seed20260721 \
		--historical-capacity results/cluster/raw/capacity-39b6973-seed20260721 \
		--out results/cluster/derived \
		--envelopes benchmarks/observed_resource_envelopes.yaml \
		--report docs/evaluation/CLUSTER_RESULTS.md
