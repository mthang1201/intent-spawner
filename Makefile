E4_RESOURCE_DRY_RUN_ID := e4-resource-envelope-dry-run-$(shell date -u +%Y%m%dT%H%M%SZ)
E4_RESOURCE_EFFICIENCY_DRY_RUN_ID := e4-resource-efficiency-dry-run-$(shell date -u +%Y%m%dT%H%M%SZ)
V5_PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; elif [ -x ../intent-spawner/.venv/bin/python ]; then echo ../intent-spawner/.venv/bin/python; else echo python3; fi)

.PHONY: test eval-offline eval-dataset eval-robustness check validate-cluster-results validate-raw-integrity v5-test v5-resource-validate v5-resource-test v5-resource-dry-run v5-resource-efficiency-validate v5-resource-efficiency-test v5-resource-efficiency-dry-run v5-user-study-test v5-user-study-smoke v5-isolation-check
.PHONY: v5-e4-preflight v5-resource-preflight v5-resource-efficiency-preflight

test:
	PYTHONPATH=. $(V5_PYTHON) -m pytest -q \
		tests/test_p2_backend_integration.py \
		tests/test_single_input_workflow.py \
		tests/test_constraint_evaluator.py \
		tests/test_dense_retrieval.py \
		tests/test_hybrid_retrieval.py

eval-offline:
	PYTHONPATH=. $(V5_PYTHON) -m evaluation_v5.offline.runner \
		--systems P1,P2 \
		--repeats 1 \
		--result-dir results_v5/protocol-v5.0.0/E1/run-e1-offline

eval-dataset:
	@if [ -z "$(DATASET)" ]; then \
		echo "Usage: make eval-dataset DATASET=path/to/dataset.yaml"; \
		exit 1; \
	fi
	PYTHONPATH=. $(V5_PYTHON) -m evaluation_v5.offline.runner \
		--dataset $(DATASET) \
		--systems P1,P2 \
		--repeats 1 \
		--result-dir results_v5/protocol-v5.0.0/E1/run-custom-dataset

eval-robustness:
	PYTHONPATH=. $(V5_PYTHON) -m evaluation_v5.robustness summary benchmarks_v5/v5-development.yaml

check:
	bash scripts/check.sh

validate-cluster-results:
	.venv/bin/python -m cluster_evaluation.validate_artifacts

validate-raw-integrity:
	.venv/bin/python -m cluster_evaluation.raw_integrity

v5-test:
	.venv/bin/python -m pytest -q \
		tests/test_evaluation_v5.py \
		tests/test_evaluation_v5_isolation.py \
		tests/test_evaluation_v5_gold_dataset.py \
		tests/test_evaluation_v5_user_study.py \
		tests/test_evaluation_v5_user_study_analysis.py

v5-resource-preflight:
	PROTOCOL_V5_ALLOW_DIRTY=1 PYTHONPATH=. $(V5_PYTHON) -m evaluation_v5.resource preflight --target envelope

v5-resource-validate:
	PYTHONPATH=. $(V5_PYTHON) -m evaluation_v5.resource validate-manifest

v5-resource-test: v5-resource-validate
	PYTHONPATH=. $(V5_PYTHON) -m pytest -q tests/test_resource_envelope_v5.py

v5-resource-dry-run: v5-resource-validate
	PYTHONPATH=. $(V5_PYTHON) -m evaluation_v5.resource dry-run \
		--result-dir results_v5/protocol-v5.0.0/E4/$(E4_RESOURCE_DRY_RUN_ID) \
		--run-id $(E4_RESOURCE_DRY_RUN_ID) \
		--image example.invalid/intent-spawner-resource-v5@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
		--reason "Simulated dry-run execution without active cluster measurement."

v5-resource-efficiency-preflight:
	PYTHONPATH=. $(V5_PYTHON) -m evaluation_v5.resource.efficiency_runner preflight

v5-resource-efficiency-validate:
	PYTHONPATH=. $(V5_PYTHON) -m evaluation_v5.resource.efficiency_runner validate

v5-resource-efficiency-test: v5-resource-efficiency-validate
	PYTHONPATH=. $(V5_PYTHON) -m pytest -q tests/test_resource_efficiency_v5.py

v5-resource-efficiency-dry-run: v5-resource-efficiency-validate
	PYTHONPATH=. $(V5_PYTHON) -m evaluation_v5.resource.efficiency_runner dry-run \
		--result-dir results_v5/protocol-v5.0.0/E4/$(E4_RESOURCE_EFFICIENCY_DRY_RUN_ID) \
		--run-id $(E4_RESOURCE_EFFICIENCY_DRY_RUN_ID) \
		--image example.invalid/intent-spawner-resource-v5@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
		--reason "Confirmatory dataset, verified image, and node capacity are unavailable."

v5-e4-preflight:
	PROTOCOL_V5_ALLOW_DIRTY=1 PYTHONPATH=. $(V5_PYTHON) -m evaluation_v5.resource preflight --target all

v5-user-study-test:
	PYTHONPATH=. .venv/bin/python -m pytest -q \
		tests/test_evaluation_v5_user_study.py \
		tests/test_evaluation_v5_user_study_analysis.py \
		tests/test_config_validation.py \
		tests/test_p2_backend_integration.py

v5-user-study-smoke:
	PYTHONPATH=. .venv/bin/python -m evaluation_v5.user_study.smoke

v5-isolation-check:
	PYTHONPATH=. $(V5_PYTHON) -m pytest -q tests/test_evaluation_v5_isolation.py

