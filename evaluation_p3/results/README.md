# P3 evaluation results

Each run is written to a new, non-overwriting directory containing a manifest,
raw paired predictions, aggregate metrics, exact per-query changes, and error
transitions. Observed evidence must never be edited in place.

The observed local-Ollama run is
[`20260821T-observed-p2-p3-ollama-llama3-v1`](20260821T-observed-p2-p3-ollama-llama3-v1/manifest.json).
Its raw evidence is unchanged; a versioned correction directory fixes only the
query-correctness definition for infeasible transition cases. The interpreted
result is documented in
[`P3_INCREMENTAL_EVALUATION_V1.md`](../../../docs/evaluation/P3_INCREMENTAL_EVALUATION_V1.md).
