"""Unit tests for convert_ds1000_to_gold_variants.py."""

import json
from pathlib import Path
import unittest

from evaluation_v5.dataset_pipeline.convert_ds1000_to_gold_variants import (
    clean_ds1000_prompt,
    extract_invariant_features,
    generate_perturbed_en,
    infer_stratum_from_library,
    to_safe_id,
    translate_to_vietnamese,
    verify_invariance,
)


class TestConvertDS1000ToGoldVariants(unittest.TestCase):

    def test_clean_ds1000_prompt(self):
        raw = """import numpy as np
x = np.array([1, 2, 3])
# how to sum array
# SOLUTION START
print(x.sum())"""
        cleaned = clean_ds1000_prompt(raw)
        self.assertNotIn("# SOLUTION START", cleaned)
        self.assertIn("import numpy as np", cleaned)
        self.assertIn("# how to sum array", cleaned)

    def test_infer_stratum_from_library(self):
        self.assertEqual(infer_stratum_from_library("PyTorch", ["deep_learning"]), "deep_learning")
        self.assertEqual(infer_stratum_from_library("Tensorflow", ["deep_learning"]), "deep_learning")
        self.assertEqual(infer_stratum_from_library("Scikit-learn", ["machine_learning"]), "machine_learning")
        self.assertEqual(infer_stratum_from_library("Pandas", ["data_analysis"]), "data_processing")
        self.assertEqual(infer_stratum_from_library("Numpy", ["scientific_computing"]), "numerical_computing")
        self.assertEqual(infer_stratum_from_library("Matplotlib", ["visualization"]), "data_processing")

    def test_invariance_verification(self):
        canonical = "Create a pandas dataframe with 10 rows without GPU."
        valid_variant = "Could you please create a pandas df with 10 rows without GPU?"

        feat_c = extract_invariant_features(canonical)
        feat_v = extract_invariant_features(valid_variant)
        errors = verify_invariance(feat_c, feat_v, "valid_test")
        self.assertEqual(len(errors), 0, f"Expected 0 errors, got: {errors}")

        # Violation of library
        bad_lib = "Create a numpy dataframe with 10 rows without GPU."
        feat_bad = extract_invariant_features(bad_lib)
        errors_bad = verify_invariance(feat_c, feat_bad, "bad_lib")
        self.assertTrue(any("pandas" in e for e in errors_bad))

    def test_to_safe_id(self):
        self.assertEqual(to_safe_id("Matplotlib_10"), "matplotlib_10")
        self.assertEqual(to_safe_id("PyTorch_0"), "pytorch_0")
        self.assertTrue(to_safe_id("123_abc")[0].isalnum())
