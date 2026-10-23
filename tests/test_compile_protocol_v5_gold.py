"""Unit tests for evaluation_v5/dataset_pipeline/compile_protocol_v5_gold.py."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import tempfile
import unittest
import yaml

from evaluation_v5.dataset_pipeline.compile_protocol_v5_gold import (
    DEFAULT_ARCADE_FILE,
    DEFAULT_CATALOG_FILE,
    DEFAULT_DS1000_FILE,
    VALID_WORKLOAD_STRATA,
    VALID_DIFFICULTIES,
    balance_families,
    check_class_balance,
    compile_protocol_v5_gold,
    load_image_catalog,
    load_valid_profiles,
    to_safe_id,
    validate_family_contract,
)
from evaluation_v5.gold_dataset import load_gold_dataset


class TestCompileProtocolV5Gold(unittest.TestCase):

    def setUp(self):
        self.valid_images = {"scipy-data-science", "pytorch-deep-learning", "tensorflow-deep-learning", "minimal-python"}
        self.valid_profiles = {"small", "medium", "large"}

        self.sample_valid_family = {
            "family_id": "test-family-1",
            "title": "Test Family 1",
            "workload_stratum": "data_processing",
            "difficulty": "easy",
            "executable_workload_id": None,
            "gold_structured_intent": {
                "task_types": ["data_processing"],
                "required_features": ["pandas"],
                "preferred_features": [],
                "forbidden_features": [],
                "required_frameworks": [],
                "preferred_frameworks": [],
                "gpu_semantics": "not_needed",
                "minimum_cpu_cores": None,
                "minimum_memory_gb": None,
                "dataset_size_gb": 0.01,
                "ambiguities": [],
            },
            "candidate_gold": {
                "preferred_candidate_ids": ["small-scipy-data-science"],
                "acceptable_candidate_ids": ["small-scipy-data-science", "medium-scipy-data-science"],
            },
            "profile_gold": {
                "preferred_profile_ids": ["small"],
                "acceptable_profile_ids": ["small", "medium"],
            },
            "image_gold": {
                "preferred_image_ids": ["scipy-data-science"],
                "acceptable_image_ids": ["scipy-data-science"],
                "required_capabilities": ["pandas"],
            },
            "policy_gold": {
                "required_constraints": [],
                "explicitly_unsupported_requirements": [],
                "expected_feasibility": "feasible",
            },
            "variants": [
                {
                    "variant_id": "test-family-1-canonical",
                    "variant_class": "canonical_en",
                    "language": "en",
                    "intent": "Read CSV with pandas",
                    "code_context": [],
                    "equivalence_status": "canonical_reference",
                },
                {
                    "variant_id": "test-family-1-paraphrase",
                    "variant_class": "paraphrase_en",
                    "language": "en",
                    "intent": "Please load a CSV into a pandas dataframe",
                    "code_context": [],
                    "equivalence_status": "reviewed_equivalent",
                },
                {
                    "variant_id": "test-family-1-vi",
                    "variant_class": "vietnamese",
                    "language": "vi",
                    "intent": "Đọc tệp CSV bằng pandas",
                    "code_context": [],
                    "equivalence_status": "reviewed_equivalent",
                },
            ],
            "label_review": {
                "status": "approved",
                "reviewed_by": "test-pipeline",
                "reviewed_at_utc": "2026-10-23T00:00:00Z",
                "notes": ["Test notes"],
            },
        }

    def test_safe_id_normalization(self):
        self.assertEqual(to_safe_id("DS1000_Pandas_0"), "ds1000_pandas_0")
        self.assertEqual(to_safe_id("arcade-family-001"), "arcade-family-001")
        self.assertTrue(to_safe_id("123-abc")[0].isalnum())

    def test_validate_family_contract_success(self):
        errors = validate_family_contract(self.sample_valid_family, self.valid_images, self.valid_profiles)
        self.assertEqual(errors, [])

    def test_validate_family_contract_invalid_stratum(self):
        bad_family = dict(self.sample_valid_family)
        bad_family["workload_stratum"] = "invalid_stratum_xyz"
        errors = validate_family_contract(bad_family, self.valid_images, self.valid_profiles)
        self.assertTrue(any("invalid workload_stratum" in e for e in errors))

    def test_validate_family_contract_unassessed_difficulty(self):
        bad_family = dict(self.sample_valid_family)
        bad_family["difficulty"] = "unassessed"
        errors = validate_family_contract(bad_family, self.valid_images, self.valid_profiles)
        self.assertTrue(any("invalid difficulty" in e for e in errors))

    def test_validate_family_contract_unknown_profile(self):
        bad_family = dict(self.sample_valid_family)
        bad_family["profile_gold"] = {
            "preferred_profile_ids": ["unknown_profile_xl"],
            "acceptable_profile_ids": ["unknown_profile_xl"],
        }
        errors = validate_family_contract(bad_family, self.valid_images, self.valid_profiles)
        self.assertTrue(any("unknown preferred_profile_id" in e for e in errors))

    def test_validate_family_contract_unknown_image(self):
        bad_family = dict(self.sample_valid_family)
        bad_family["image_gold"] = {
            "preferred_image_ids": ["unknown-docker-image"],
            "acceptable_image_ids": ["unknown-docker-image"],
            "required_capabilities": [],
        }
        errors = validate_family_contract(bad_family, self.valid_images, self.valid_profiles)
        self.assertTrue(any("unknown preferred_image_id" in e for e in errors))

    def test_check_class_balance_balanced(self):
        # 2 families: 1 small, 1 medium -> each 50%
        # 1 scipy, 1 pytorch -> each 50%
        f1 = dict(self.sample_valid_family)
        f2 = dict(self.sample_valid_family)
        f2["family_id"] = "test-family-2"
        f2["profile_gold"] = {"preferred_profile_ids": ["medium"], "acceptable_profile_ids": ["medium"]}
        f2["image_gold"] = {"preferred_image_ids": ["pytorch-deep-learning"], "acceptable_image_ids": ["pytorch-deep-learning"], "required_capabilities": ["pytorch"]}

        is_bal, p_shares, i_shares, warnings = check_class_balance([f1, f2], max_profile_share=0.5, max_image_share=0.5)
        self.assertTrue(is_bal)
        self.assertEqual(len(warnings), 0)
        self.assertEqual(p_shares["small"], 0.5)
        self.assertEqual(p_shares["medium"], 0.5)
        self.assertEqual(i_shares["scipy-data-science"], 0.5)
        self.assertEqual(i_shares["pytorch-deep-learning"], 0.5)

    def test_check_class_balance_imbalanced(self):
        # 3 families with small -> 100% small > 50%
        fams = [dict(self.sample_valid_family) for _ in range(3)]
        is_bal, p_shares, i_shares, warnings = check_class_balance(fams, max_profile_share=0.5, max_image_share=0.5)
        self.assertFalse(is_bal)
        self.assertTrue(any("Preferred profile 'small' share 100.00% exceeds maximum" in w for w in warnings))

    def test_balance_families_algorithm(self):
        # Create an imbalanced synthetic population: 90 small scipy, 10 medium pytorch
        fams = []
        for i in range(90):
            f = dict(self.sample_valid_family)
            f["family_id"] = f"small-scipy-{i}"
            f["profile_gold"] = {"preferred_profile_ids": ["small"], "acceptable_profile_ids": ["small"]}
            f["image_gold"] = {"preferred_image_ids": ["scipy-data-science"], "acceptable_image_ids": ["scipy-data-science"], "required_capabilities": ["pandas"]}
            fams.append(f)

        for i in range(10):
            f = dict(self.sample_valid_family)
            f["family_id"] = f"med-pytorch-{i}"
            f["profile_gold"] = {"preferred_profile_ids": ["medium"], "acceptable_profile_ids": ["medium"]}
            f["image_gold"] = {"preferred_image_ids": ["pytorch-deep-learning"], "acceptable_image_ids": ["pytorch-deep-learning"], "required_capabilities": ["pytorch"]}
            fams.append(f)

        balanced = balance_families(fams, max_profile_share=0.5, max_image_share=0.5, seed=123)
        is_bal, p_shares, i_shares, warnings = check_class_balance(balanced, max_profile_share=0.5, max_image_share=0.5)
        self.assertTrue(is_bal, f"Balancing failed with warnings: {warnings}")
        for p, sh in p_shares.items():
            self.assertLessEqual(sh, 0.5)
        for img, sh in i_shares.items():
            self.assertLessEqual(sh, 0.5)

    def test_end_to_end_compilation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "v5-gold-standard-dataset.yaml"

            ret = compile_protocol_v5_gold(
                ds1000_path=DEFAULT_DS1000_FILE,
                arcade_path=DEFAULT_ARCADE_FILE,
                output_path=out_file,
                catalog_path=DEFAULT_CATALOG_FILE,
                mode="balanced",
                max_profile_share=0.5,
                max_image_share=0.5,
                seed=42,
                strict_balance=True,
            )
            self.assertEqual(ret, 0)
            self.assertTrue(out_file.is_file())

            # Validate loaded dataset
            loaded = load_gold_dataset(out_file)
            dataset = loaded.dataset
            self.assertEqual(dataset.schema_version, "protocol-v5-gold-family-v1.0.0")
            self.assertEqual(dataset.dataset_metadata["dataset_id"], "v5-gold-standard-dataset")
            self.assertEqual(dataset.review_policy["max_preferred_profile_share"], 0.5)
            self.assertEqual(dataset.review_policy["max_preferred_image_share"], 0.5)
            self.assertGreater(len(dataset.families), 200)

            # Check that all families adhere to the required 7 strata and difficulties
            for fam in dataset.families:
                self.assertIn(fam.workload_stratum, VALID_WORKLOAD_STRATA)
                self.assertIn(fam.difficulty, VALID_DIFFICULTIES)
                self.assertTrue(len(fam.variants) >= 1)

            # Check class balance
            p_counts = Counter(f.profile_gold["preferred_profile_ids"][0] for f in dataset.families if f.profile_gold["preferred_profile_ids"])
            i_counts = Counter(f.image_gold["preferred_image_ids"][0] for f in dataset.families if f.image_gold["preferred_image_ids"])
            n = len(dataset.families)

            for p, c in p_counts.items():
                self.assertLessEqual(c / n, 0.5000001, f"Profile {p} exceeds 0.5 share: {c/n}")

            for img, c in i_counts.items():
                self.assertLessEqual(c / n, 0.5000001, f"Image {img} exceeds 0.5 share: {c/n}")


if __name__ == "__main__":
    unittest.main()
