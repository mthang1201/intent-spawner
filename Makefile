E4_RESOURCE_DRY_RUN_ID := e4-resource-envelope-dry-run-$(shell date -u +%Y%m%dT%H%M%SZ)

.PHONY: check validate-cluster-results validate-raw-integrity capacity-dry-run v3-validate v3-dry-run v3-image-policy v4-validate v4-test v5-test v5-resource-validate v5-resource-test v5-resource-dry-run v5-user-study-test v5-user-study-smoke v5-isolation-check regenerate-cluster-results

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

v4-validate:
	.venv/bin/python -m evaluation_v4.run_recommenders --dry-run
	.venv/bin/python -m evaluation_v4.plan_system --dry-run

v4-test:
	.venv/bin/python -m pytest -q tests/test_evaluation_v4.py

v5-test:
	.venv/bin/python -m pytest -q \
		tests/test_evaluation_v5.py \
		tests/test_evaluation_v5_isolation.py \
		tests/test_evaluation_v5_gold_dataset.py \
		tests/test_evaluation_v5_user_study.py \
		tests/test_evaluation_v5_user_study_analysis.py

v5-resource-validate:
	PYTHONPATH=. .venv/bin/python -m evaluation_v5.resource validate-manifest

v5-resource-test: v5-resource-validate
	PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_resource_envelope_v5.py

v5-resource-dry-run: v5-resource-validate
	PYTHONPATH=. .venv/bin/python -m evaluation_v5.resource dry-run \
		--result-dir results_v5/protocol-v5.0.0/E4/$(E4_RESOURCE_DRY_RUN_ID) \
		--run-id $(E4_RESOURCE_DRY_RUN_ID) \
		--image example.invalid/intent-spawner-resource-v5@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
		--reason "Current context is not the required disposable intent-spawner-eval-v5 cluster."

v5-user-study-test:
	PYTHONPATH=. .venv/bin/python -m pytest -q \
		tests/test_evaluation_v5_user_study.py \
		tests/test_evaluation_v5_user_study_analysis.py \
		tests/test_config_validation.py \
		tests/test_p2_backend_integration.py

v5-user-study-smoke:
	PYTHONPATH=. .venv/bin/python -m evaluation_v5.user_study.smoke

v5-isolation-check:
	.venv/bin/python -m evaluation_v5.isolation_audit

regenerate-cluster-results: validate-cluster-results
	.venv/bin/python -m cluster_evaluation.analyze \
		--ground results/cluster/raw/ground-truth-39b6973-seed20260720 \
		--comparative results/cluster/raw/comparative-39b6973-seed20260720 \
		--capacity results/cluster/raw/capacity-v2-ca2e74b-seed20260721 \
		--historical-capacity results/cluster/raw/capacity-39b6973-seed20260721 \
		--out results/cluster/derived \
		--envelopes benchmarks/observed_resource_envelopes.yaml \
		--report docs/evaluation/CLUSTER_RESULTS.md
