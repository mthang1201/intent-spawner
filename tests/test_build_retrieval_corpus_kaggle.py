#!/usr/bin/env python3
"""Unit tests for scripts/datasets/build_retrieval_corpus_kaggle.py."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recommender.candidate_corpus import build_candidate_corpus
from recommender.rule_based import load_image_catalog
from scripts.datasets.build_retrieval_corpus_kaggle import (
    KaggleEvaluationItem,
    analyze_vocabulary_overlap,
    derive_ground_truth,
    evaluate_retrievers,
    load_kaggle_items,
    propose_match_terms,
)


class TestBuildRetrievalCorpusKaggle(unittest.TestCase):

    def test_derive_ground_truth(self):
        # PyTorch item
        img1, prof1 = derive_ground_truth("deep_learning_pytorch", ["torch", "torchvision"], True, 4000.0)
        self.assertEqual(img1, "pytorch-deep-learning")
        self.assertEqual(prof1, "large")

        # TensorFlow item
        img2, prof2 = derive_ground_truth("deep_learning_tensorflow", ["tensorflow", "keras"], True, 3000.0)
        self.assertEqual(img2, "tensorflow-deep-learning")
        self.assertEqual(prof2, "large")

        # Tabular ML item
        img3, prof3 = derive_ground_truth("tabular_ml", ["scikit-learn", "xgboost", "pandas"], False, 1000.0)
        self.assertEqual(img3, "scipy-data-science")
        self.assertEqual(prof3, "medium")

        # Minimal item
        img4, prof4 = derive_ground_truth("other", ["sys", "os"], False, 200.0)
        self.assertEqual(img4, "minimal-python")
        self.assertEqual(prof4, "small")

    def test_load_kaggle_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_jsonl = Path(tmpdir) / "test.jsonl"
            records = [
                {
                    "notebook_id": "test_1",
                    "title": "Predict Customer Churn",
                    "intent": "We analyze churn using random forests and xgboost.",
                    "category": "tabular_ml",
                    "libraries": ["pandas", "xgboost", "scikit-learn"],
                    "metadata": {"is_gpu_enabled": False, "total_memory_usage_mb": 1200.0},
                },
                {
                    "notebook_id": "test_2",
                    "title": "Train Vision Transformer",
                    "intent": "Medical imaging with PyTorch and timm vision transformer.",
                    "category": "deep_learning_pytorch",
                    "libraries": ["torch", "torchvision", "timm"],
                    "metadata": {"is_gpu_enabled": True, "total_memory_usage_mb": 6000.0},
                },
            ]
            with open(sample_jsonl, "w", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r) + "\n")

            items = load_kaggle_items(sample_jsonl)
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0].target_image, "scipy-data-science")
            self.assertEqual(items[1].target_image, "pytorch-deep-learning")
            self.assertTrue(items[1].is_gpu_enabled)

    def test_vocabulary_overlap_and_retrievers(self):
        catalog = load_image_catalog()
        corpus = build_candidate_corpus(image_catalog=catalog)

        items = [
            KaggleEvaluationItem(
                notebook_id="test_0",
                title="Customer Churn Prediction",
                intent="Predict customer churn using gradient boosted trees and scikit-learn.",
                category="tabular_ml",
                libraries=("pandas", "scikit-learn", "xgboost"),
                is_gpu_enabled=False,
                total_memory_usage_mb=1024.0,
                input_files=("data.csv",),
                target_image="scipy-data-science",
                target_profile="medium",
            ),
            KaggleEvaluationItem(
                notebook_id="test_1",
                title="PyTorch Deep Vision",
                intent="Train a deep neural network using PyTorch cuda and torchvision.",
                category="deep_learning_pytorch",
                libraries=("torch", "torchvision", "pytorch"),
                is_gpu_enabled=True,
                total_memory_usage_mb=5000.0,
                input_files=(),
                target_image="pytorch-deep-learning",
                target_profile="large",
            ),
        ]

        # Test Vocab Overlap
        vocab_stats = analyze_vocabulary_overlap(corpus, items)
        self.assertGreater(vocab_stats.corpus_vocab_size, 0)
        self.assertGreater(vocab_stats.kaggle_vocab_size, 0)
        self.assertGreater(vocab_stats.kaggle_coverage_ratio, 0.0)

        # Test Retrievers
        results = evaluate_retrievers(corpus, items, top_k=5)
        self.assertIn("BM25 (Sparse)", results)
        self.assertIn("Local Embeddings (Dense)", results)
        self.assertIn("Hybrid RRF (Sparse + Dense)", results)
        self.assertGreaterEqual(results["Hybrid RRF (Sparse + Dense)"].hit_1, 50.0)

        # Test Proposals
        proposals = propose_match_terms(items, catalog_path=Path("recommender/image-catalog.yaml"), vocab_stats=vocab_stats)
        self.assertIn("pytorch-deep-learning", proposals.proposals_by_image)


if __name__ == "__main__":
    unittest.main()
