# Benchmark Datasets Directory (`data/`)

This directory houses the standardized benchmark datasets used to evaluate the Intent-Spawner framework.

## Structure

```text
data/
└── benchmark/
    └── v1/
        ├── SCHEMA.md        # Detailed JSON schema specification for benchmark records
        ├── manifest.json    # Cryptographic SHA-256 checksums and split distributions
        ├── train.jsonl      # Formative training split (70% stratified clusters)
        ├── dev.jsonl        # Validation split (15% stratified clusters)
        └── test.jsonl       # Confirmatory evaluation split (15% stratified clusters)
```

## Dataset Origins

- **DS-1000** ([arXiv:2211.11501](https://arxiv.org/abs/2211.11501)): Code generation problems across Pandas, NumPy, Scipy, Matplotlib, Scikit-learn, PyTorch, and TensorFlow.
- **ARCADE** ([arXiv:2212.09248](https://arxiv.org/abs/2212.09248)): Multi-turn Jupyter notebook natural language intents, extended with conversational Vietnamese variants.
- **DistilKaggle** ([Zenodo:10317389](https://zenodo.org/records/10317389)): Real-world Kaggle notebook retrieval corpus.
- **Alibaba PAI GPU Cluster Trace** ([alibaba/clusterdata](https://github.com/alibaba/clusterdata)): Trace arrivals for discrete-event cluster simulation.

## Labeling Methodology

Ground-truth profiles (`small`, `medium`, `large`) are derived from **empirical cgroups v2 resource measurements** under isolated container execution (`scripts/datasets/profile_workloads.py`).Workloads are assigned to the minimal safe profile that accommodates execution without triggering Out-Of-Memory (OOM) termination.

## Zero Data Leakage

All splits are generated using connected-component disjoint-set clustering (`evaluation_v5/dataset_pipeline/split_unified_benchmark.py`). All variants originating from the same problem family or identical prompts are strictly partitioned into the same split. Cross-split duplicate count: **0**.
