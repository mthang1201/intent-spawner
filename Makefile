.PHONY: check validate-cluster-results regenerate-cluster-results

check:
	bash scripts/check.sh

validate-cluster-results:
	.venv/bin/python -m cluster_evaluation.validate_artifacts

regenerate-cluster-results: validate-cluster-results
	.venv/bin/python -m cluster_evaluation.analyze \
		--ground results/cluster/raw/ground-truth-39b6973-seed20260720 \
		--comparative results/cluster/raw/comparative-39b6973-seed20260720 \
		--capacity results/cluster/raw/capacity-39b6973-seed20260721 \
		--out results/cluster/derived \
		--envelopes benchmarks/observed_resource_envelopes.yaml \
		--report docs/evaluation/CLUSTER_RESULTS.md
