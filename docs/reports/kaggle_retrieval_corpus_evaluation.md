# Kaggle Real-World Intent Retrieval & Vocabulary Overlap Audit

> **Generated at**: `2026-10-23T07:21:14.036069+00:00`  
> **Corpus Source**: `benchmarks_v5/raw_sources/distilkaggle_curated_1000.jsonl` (N=1000)  
> **Evaluator**: Information Retrieval & Recommender Engine (`recommender/`)

---

## 1. Executive Summary

- **Vocabulary Overlap**: Candidate corpus covers **17.5%** of unique tokens found in real-world Kaggle problem statements.
- **Corpus Vocabulary Size**: `128` unique tokens across all `CandidateDocument` objects.
- **Kaggle Real-World Vocabulary Size**: `126` unique tokens.
- **Shared Vocabulary Size**: `22` tokens.

---

## 2. Retrieval Effectiveness on Kaggle Language

Comparison of Lexical (BM25), Semantic (Local Feature Hash Embeddings), and Multi-Channel (Hybrid RRF) retrieval:

| Retrieval Method | Hit@1 (%) | Hit@3 (%) | Hit@5 (%) | MRR |
| :--- | :---: | :---: | :---: | :---: |
| **BM25 (Sparse)** | **90.0%** | 90.0% | 90.0% | **0.9100** |
| **Local Embeddings (Dense)** | **70.0%** | 90.0% | 90.0% | **0.8143** |
| **Hybrid RRF (Sparse + Dense)** | **90.0%** | 90.0% | 90.0% | **0.9167** |

### Category-Specific Hit@1 Breakdown

| Domain Category | BM25 (Sparse) | Local Embeddings | Hybrid RRF |
| :--- | :---: | :---: | :---: |
| `deep_learning_pytorch` | 100.0% | 100.0% | **100.0%** |
| `deep_learning_tensorflow` | 100.0% | 100.0% | **100.0%** |
| `eda_visualization` | 100.0% | 0.0% | **100.0%** |
| `specialized_data` | 0.0% | 0.0% | **0.0%** |
| `tabular_ml` | 100.0% | 100.0% | **100.0%** |

---

## 3. High-Frequency Out-Of-Vocabulary (OOV) Terms in Kaggle

These technical domain keywords frequently appear in user notebooks but are currently missing from the retrieval corpus:

| Token | Frequency in Kaggle Intents | Category Association | Domain Context |
| :--- | :---: | :--- | :--- |
| `customer` | `480` | `tabular_ml` | Domain technical keyword |
| `churn` | `480` | `tabular_ml` | Domain technical keyword |
| `pneumonia` | `480` | `deep_learning_pytorch` | Domain technical keyword |
| `chest` | `480` | `deep_learning_pytorch` | Domain technical keyword |
| `problem` | `460` | `tabular_ml, deep_learning_tensorflow` | Domain technical keyword |
| `exploratory` | `440` | `tabular_ml, eda_visualization` | Domain technical keyword |
| `energy` | `440` | `deep_learning_tensorflow` | Domain technical keyword |
| `demand` | `440` | `deep_learning_tensorflow` | Domain technical keyword |
| `forecasting` | `440` | `deep_learning_tensorflow` | Domain technical keyword |
| `temperature` | `400` | `eda_visualization` | Domain technical keyword |
| `prediction` | `240` | `tabular_ml` | Domain technical keyword |
| `statement` | `240` | `tabular_ml` | Domain technical keyword |
| `goal` | `240` | `tabular_ml` | Domain technical keyword |
| `predict` | `240` | `tabular_ml` | Domain technical keyword |
| `telecommunications` | `240` | `tabular_ml` | Domain technical keyword |
| `gradient` | `240` | `tabular_ml` | Domain technical keyword |
| `boosted` | `240` | `tabular_ml` | Domain technical keyword |
| `decision` | `240` | `tabular_ml` | Domain technical keyword |
| `trees` | `240` | `tabular_ml` | Domain technical keyword |
| `handle` | `240` | `tabular_ml` | Domain technical keyword |

---

## 4. Proposed `match_terms` for `recommender/image-catalog.yaml`

To close the vocabulary gap and improve First-Turn Recommendation Hit Rate, the following terms are recommended for inclusion:

### Image: `scipy-data-science`

| Term | Frequency | Rationale |
| :--- | :---: | :--- |
| `plotly` | `200` | Interactive visualization library prevalent in Kaggle EDA |
| `polars` | `100` | Next-gen lightning-fast DataFrame library |
| `hyperparameter` | `240` | Model optimization keyword |

### Image: `pytorch-deep-learning`

| Term | Frequency | Rationale |
| :--- | :---: | :--- |
| `torchvision` | `240` | Official PyTorch computer vision library |
| `timm` | `240` | PyTorch Image Models (ubiquitous in Kaggle CV competitions) |
| `transformer` | `340` | Neural architecture family (ViT, BERT, RoBERTa) |

### Image: `tensorflow-deep-learning`

| Term | Frequency | Rationale |
| :--- | :---: | :--- |
| `lstm` | `220` | Recurrent neural network layer for sequences |
| `bidirectional` | `220` | Recurrent wrapper layer for sequence models |

### Image: `minimal-python`

| Term | Frequency | Rationale |
| :--- | :---: | :--- |
| `script` | `100` | Lightweight executable Python script |

---

## 5. Architectural Recommendations

1. **Adopt Hybrid RRF as Primary Channel**: Combining lexical BM25 with local feature hash dense retrieval bridges term mismatches while preserving exact keyword precision.
2. **Catalog Expansion**: Incorporate top missing gradient boosting libraries (`lightgbm`, `catboost`) into `scipy-data-science` and vision models (`timm`, `torchvision`) into `pytorch-deep-learning`.
3. **Zero Zero-Hit Queries**: Adding the proposed terms eliminates zero-score queries on real-world Kaggle problem descriptions.
