#!/usr/bin/env python3
"""Unit tests for convert_arcade_to_gold_variants.py."""

import json
from pathlib import Path
import tempfile
import unittest

from evaluation_v5.dataset_pipeline.convert_arcade_to_gold_variants import (
    extract_ast_imports,
    extract_invariant_features,
    generate_perturbed_en,
    is_vague_intent,
    resolve_image_from_ast,
    to_safe_id,
    verify_invariance,
)


class TestConvertArcadeToGoldVariants(unittest.TestCase):

    def setUp(self):
        self.mock_catalog = {
            "catalog_version": "2026-08-06.1",
            "default_image": "minimal-python",
            "images": {
                "minimal-python": {"capabilities": ["python"]},
                "scipy-data-science": {
                    "capabilities": ["pandas", "numpy", "scipy", "scikit-learn", "visualization"]
                },
                "pytorch-deep-learning": {"capabilities": ["pytorch", "data-science"]},
                "tensorflow-deep-learning": {"capabilities": ["tensorflow", "keras", "data-science"]},
            },
        }

    def test_invariance_negation_numbers_units(self):
        """Kiểm tra bất biến: cùng thư viện, số/đơn vị và phủ định."""
        canonical = "Read 'data.csv' containing 500 rows with pandas without GPU."
        # Biến thể chuẩn: giữ nguyên thư viện, số/đơn vị, phủ định
        valid_variant = "Could you please read 'data.csv' with 500 rows in pandas without GPU?"

        feat_canon = extract_invariant_features(canonical)
        feat_valid = extract_invariant_features(valid_variant)
        errors = verify_invariance(feat_canon, feat_valid, "valid_variant")
        self.assertEqual(len(errors), 0, f"Expected 0 errors, got: {errors}")

        # 1. Vi phạm phủ định (bỏ 'without GPU' thành có GPU)
        bad_negation = "Read 'data.csv' containing 500 rows with pandas using GPU."
        feat_bad_neg = extract_invariant_features(bad_negation)
        errors_neg = verify_invariance(feat_canon, feat_bad_neg, "bad_negation")
        self.assertTrue(any("phủ định" in e for e in errors_neg))

        # 2. Vi phạm số/đơn vị (500 rows -> 1000 rows)
        bad_numbers = "Read 'data.csv' containing 1000 rows with pandas without GPU."
        feat_bad_num = extract_invariant_features(bad_numbers)
        errors_num = verify_invariance(feat_canon, feat_bad_num, "bad_numbers")
        self.assertTrue(any("số/đơn vị" in e for e in errors_num))

        # 3. Vi phạm thư viện (bỏ pandas hoặc đổi sang numpy)
        bad_lib = "Read 'data.csv' containing 500 rows with numpy without GPU."
        feat_bad_lib = extract_invariant_features(bad_lib)
        errors_lib = verify_invariance(feat_canon, feat_bad_lib, "bad_lib")
        self.assertTrue(any("thư viện" in e for e in errors_lib))

    def test_deterministic_perturbation_seed_reproducibility(self):
        """Kiểm tra tính tái lập tuyệt đối theo seed của hàm perturbed_en."""
        intent = "Connect to SQLite database 'store.db' and load 100 rows into a DataFrame."
        seed = 12345

        text1, axes1 = generate_perturbed_en(intent, seed=seed)
        text2, axes2 = generate_perturbed_en(intent, seed=seed)

        self.assertEqual(text1, text2, "Kết quả sinh văn bản không trùng khớp với cùng một seed")
        self.assertEqual(axes1, axes2, "Trục biến thể không trùng khớp với cùng một seed")

        # Khác seed phải tạo ra chuỗi hoặc trục khác
        text3, _ = generate_perturbed_en(intent, seed=99999)
        self.assertNotEqual(text1, text3, "Hai seed khác nhau lại sinh kết quả giống hệt nhau")

    def test_vague_intent_filtering(self):
        """Kiểm tra loại bỏ các intent quá mơ hồ không suy được nhu cầu."""
        vague_examples = [
            "làm tiếp phần trước",
            "tiếp tục",
            "chạy tiếp",
            "continue from previous",
            "do next step",
            "run next cell",
            "pass",
            "do the same",
        ]
        for v in vague_examples:
            self.assertTrue(is_vague_intent(v), f"Intent '{v}' phải bị đánh dấu là mơ hồ")

        concrete_examples = [
            "Filter dataframe where age > 30 and save to output.csv",
            "Train a LogisticRegression model using scikit-learn",
            "Connect to SQLite database and query top 10 users",
        ]
        for c in concrete_examples:
            self.assertFalse(is_vague_intent(c), f"Intent cụ thể '{c}' không được bị đánh dấu mơ hồ")

    def test_ast_image_and_catalog_mapping(self):
        """Kiểm tra ánh xạ AST import qua catalog không hardcode."""
        # PyTorch code
        torch_code = "import torch\nimport torch.nn as nn\nx = torch.randn(10, 10)"
        imports = extract_ast_imports(torch_code)
        img, caps, fws = resolve_image_from_ast(imports, self.mock_catalog)
        self.assertEqual(img, "pytorch-deep-learning")
        self.assertIn("pytorch", fws)

        # TensorFlow code
        tf_code = "import tensorflow as tf\nfrom tensorflow import keras\nmodel = keras.Sequential()"
        imports = extract_ast_imports(tf_code)
        img, caps, fws = resolve_image_from_ast(imports, self.mock_catalog)
        self.assertEqual(img, "tensorflow-deep-learning")
        self.assertIn("tensorflow", fws)

        # Pandas / Data science code
        pandas_code = "import pandas as pd\nimport numpy as np\ndf = pd.read_csv('test.csv')"
        imports = extract_ast_imports(pandas_code)
        img, caps, fws = resolve_image_from_ast(imports, self.mock_catalog)
        self.assertEqual(img, "scipy-data-science")
        self.assertIn("pandas", caps)

        # Standard library minimal python
        stdlib_code = "import sys\nimport json\nimport math\nprint(math.sqrt(16))"
        imports = extract_ast_imports(stdlib_code)
        img, caps, fws = resolve_image_from_ast(imports, self.mock_catalog)
        self.assertEqual(img, "minimal-python")
        self.assertEqual(caps, ["python"])

    def test_safe_id_sanitization(self):
        """Kiểm tra chuẩn hóa ID chuỗi an toàn."""
        self.assertEqual(to_safe_id("Arcade_DP_123"), "arcade_dp_123")
        self.assertEqual(to_safe_id("invalid!@#id"), "invalid-id")
        self.assertEqual(to_safe_id("-leading-dash"), "leading-dash")

    def test_exclude_pending_human_variants(self):
        """Kiểm tra tính năng loại bỏ các biến thể pending_human khi compile hoặc xuất gold có cờ."""
        mock_variants = [
            {
                "variant_id": "arcade-fam-1-canonical-en",
                "variant_class": "canonical_en",
                "language": "en",
                "equivalence_status": "canonical_reference",
            },
            {
                "variant_id": "arcade-fam-1-perturbed-en",
                "variant_class": "informal_or_noisy",
                "language": "en",
                "equivalence_status": "reviewed_equivalent",
            },
            {
                "variant_id": "arcade-fam-1-vi",
                "variant_class": "vietnamese",
                "language": "vi",
                "equivalence_status": "pending_review",
            },
        ]
        # Khi compile, các biến thể pending_review / pending_human không được phép vào gold
        approved_variants = [v for v in mock_variants if v["equivalence_status"] != "pending_review"]
        self.assertEqual(len(approved_variants), 2)
        self.assertNotIn("arcade-fam-1-vi", [v["variant_id"] for v in approved_variants])


if __name__ == "__main__":
    unittest.main()

