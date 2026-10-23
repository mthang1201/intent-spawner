#!/usr/bin/env python3
"""Unit tests for scripts/datasets/process_distilkaggle.py."""

import json
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.datasets.process_distilkaggle import (
    AstLibraryVisitor,
    CuratedNotebookRecord,
    RawNotebookData,
    categorize_notebook,
    clean_markdown_text,
    compute_curation_score,
    extract_natural_language_intent,
    generate_synthetic_distilkaggle_dataset,
    inspect_code_libraries_ast,
    resolve_metadata,
    sanitize_code_for_ast,
    save_curated_records_jsonl,
    stratified_curate_top_k,
    stream_from_archive_distilkaggle_csvs,
    stream_from_ipynb_archive,
    stream_from_sqlite,
)


class TestProcessDistilKaggle(unittest.TestCase):

    def test_clean_markdown_text(self):
        raw_md = (
            "# Heading\n\n"
            "<div class='alert'>Notice</div>\n"
            "[![Badge](https://img.shields.io/badge)](https://badge.com)\n"
            "Here is a [Dataset Link](https://kaggle.com/dataset).\n"
            "---\n"
            "Clean content paragraph."
        )
        cleaned = clean_markdown_text(raw_md)
        self.assertNotIn("<div>", cleaned)
        self.assertNotIn("[![Badge]", cleaned)
        self.assertNotIn("---", cleaned)
        self.assertIn("Clean content paragraph.", cleaned)
        self.assertIn("Dataset Link", cleaned)

    def test_extract_natural_language_intent_with_problem_statement(self):
        cells = [
            "# California Housing Price Prediction\n\nWelcome to this notebook.",
            "## Problem Statement\nWe aim to predict median house values in Californian districts using census features and regression algorithms.",
            "```python\n# code placeholder\n```",
        ]
        title, intent = extract_natural_language_intent(cells)
        self.assertEqual(title, "California Housing Price Prediction")
        self.assertIn("We aim to predict median house values", intent)

    def test_extract_natural_language_intent_fallback(self):
        cells = [
            "# EDA on Titanic",
            "This exploratory analysis dives deep into passenger demographics, survival correlations, and ticket fare distributions across embarkation ports.",
        ]
        title, intent = extract_natural_language_intent(cells)
        self.assertEqual(title, "EDA on Titanic")
        self.assertIn("passenger demographics", intent)

    def test_sanitize_code_for_ast(self):
        code_with_magic = (
            "%matplotlib inline\n"
            "!pip install -q xgboost\n"
            "import xgboost as xgb\n"
            "%%time\n"
            "model = xgb.XGBClassifier()\n"
            "?model.fit\n"
        )
        sanitized = sanitize_code_for_ast(code_with_magic)
        self.assertIn("# [ipython_magic_sanitized]: %matplotlib inline", sanitized)
        self.assertIn("# [ipython_magic_sanitized]: !pip install -q xgboost", sanitized)
        self.assertIn("# [ipython_cell_magic_sanitized]: %%time", sanitized)
        self.assertIn("import xgboost as xgb", sanitized)

    def test_inspect_code_libraries_ast_imports(self):
        cells = [
            (
                "%load_ext autoreload\n"
                "import pandas as pd\n"
                "import numpy as np\n"
                "from sklearn.ensemble import RandomForestClassifier\n"
                "from torch import nn\n"
                "import torch.nn.functional as F\n"
                "import torchvision\n"
            ),
            (
                "df = pd.read_csv('train_features.csv')\n"
                "device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')\n"
                "model.to('cuda')\n"
            ),
        ]
        libs, input_files, features = inspect_code_libraries_ast(cells)

        self.assertIn("pandas", libs)
        self.assertIn("numpy", libs)
        self.assertIn("scikit-learn", libs)
        self.assertIn("pytorch", libs)
        self.assertIn("torchvision", libs)
        self.assertIn("train_features.csv", input_files)
        self.assertTrue(features["has_gpu_code"])
        self.assertEqual(features["syntax_error_cells_count"], 0)

    def test_inspect_code_libraries_dynamic_import(self):
        cells = [
            "mod = __import__('lightgbm')\n"
            "data = pd.read_parquet('dataset.parquet')"
        ]
        libs, input_files, features = inspect_code_libraries_ast(cells)
        self.assertIn("lightgbm", libs)
        self.assertIn("dataset.parquet", input_files)

    def test_categorize_notebook(self):
        self.assertEqual(categorize_notebook(["pytorch", "torchvision"]), "deep_learning_pytorch")
        self.assertEqual(categorize_notebook(["tensorflow", "keras"]), "deep_learning_tensorflow")
        self.assertEqual(categorize_notebook(["scikit-learn", "xgboost", "pandas"]), "tabular_ml")
        self.assertEqual(categorize_notebook(["matplotlib", "seaborn", "pandas"]), "eda_visualization")
        self.assertEqual(categorize_notebook(["transformers", "torch"]), "deep_learning_pytorch")
        self.assertEqual(categorize_notebook(["transformers", "nltk"]), "specialized_data")

    def test_resolve_metadata(self):
        raw_meta = {
            "IsGpuWorker": 1,
            "total_memory_usage": 2048.5,
            "TotalDatasetSizeBytes": 104857600,
            "PT": "Grandmaster",
        }
        code_features = {"has_gpu_code": False}
        meta = resolve_metadata(raw_meta, code_features, ["data.csv"])
        self.assertTrue(meta["is_gpu_enabled"])
        self.assertEqual(meta["total_memory_usage_mb"], 2048.5)
        self.assertEqual(meta["input_file_size_bytes"], 104857600)
        self.assertEqual(meta["performance_tier"], "Grandmaster")
        self.assertIn("data.csv", meta["input_files"])

    def test_compute_curation_score(self):
        score = compute_curation_score(
            title="Heart Disease Prediction with Random Forest",
            intent="Objective: Predict presence of heart disease using clinical patient records and gradient boosting trees.",
            libraries=["pandas", "numpy", "scikit-learn", "xgboost", "matplotlib"],
            category="tabular_ml",
            code_cells_count=6,
            total_code_lines=45,
            metadata={"is_gpu_enabled": False, "total_memory_usage_mb": 512.0, "input_files": ["heart.csv"]},
        )
        self.assertGreater(score, 60.0)

    def test_stream_from_sqlite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_distilkaggle.sqlite"
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            c.execute("CREATE TABLE notebook_metrics ([Kernel ID] TEXT, PT TEXT, MW INTEGER, MC INTEGER, Lines INTEGER, is_gpu_enabled INTEGER, total_memory_usage REAL, input_file_size INTEGER)")
            c.execute("CREATE TABLE markdown ([Kernel ID] TEXT, [Cell Index] INTEGER, Markdown TEXT)")
            c.execute("CREATE TABLE code ([Kernel ID] TEXT, [Cell Index] INTEGER, Code TEXT)")

            c.execute("INSERT INTO notebook_metrics VALUES ('k1', 'Expert', 100, 2, 30, 1, 1024.0, 5000)")
            c.execute("INSERT INTO markdown VALUES ('k1', 0, '# Deep Learning Vision\n## Problem Statement\nClassify images')")
            c.execute("INSERT INTO code VALUES ('k1', 0, 'import torch\nimport torchvision\ndevice = \"cuda\"')")
            conn.commit()
            conn.close()

            records = list(stream_from_sqlite(db_path))
            self.assertEqual(len(records), 1)
            rec = records[0]
            self.assertEqual(rec.kernel_id, "k1")
            self.assertIn("Classify images", rec.markdown_cells[0])
            self.assertIn("import torch", rec.code_cells[0])

    def test_stratified_curation_pipeline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / "synth_data.tar.gz"
            generate_synthetic_distilkaggle_dataset(archive_path, num_notebooks=25, format_type="tar.gz")

            stream = stream_from_ipynb_archive(archive_path)
            curated = stratified_curate_top_k(
                stream,
                target_count=20,
                quotas={
                    "tabular_ml": 4,
                    "deep_learning_pytorch": 4,
                    "deep_learning_tensorflow": 4,
                    "eda_visualization": 4,
                    "specialized_data": 4,
                },
                min_score=10.0,
            )
            self.assertEqual(len(curated), 20)

            # Test saving to JSONL
            out_jsonl = Path(tmpdir) / "output.jsonl"
            save_curated_records_jsonl(curated, out_jsonl)
            self.assertTrue(out_jsonl.is_file())

            # Validate each line is valid json and contains required fields
            with open(out_jsonl, "r", encoding="utf-8") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 20)
            sample = json.loads(lines[0])
            self.assertIn("notebook_id", sample)
            self.assertIn("intent", sample)
            self.assertIn("category", sample)
            self.assertIn("libraries", sample)
            self.assertIn("metadata", sample)
            self.assertIn("is_gpu_enabled", sample["metadata"])

    def test_stream_from_distilkaggle_csvs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_dir = Path(tmpdir) / "distilkaggle_csv"
            generate_synthetic_distilkaggle_dataset(csv_dir, num_notebooks=10, format_type="csv")

            records = list(stream_from_archive_distilkaggle_csvs(csv_dir) if False else [])
            from scripts.datasets.process_distilkaggle import stream_from_distilkaggle_csvs
            records = list(stream_from_distilkaggle_csvs(
                metrics_file=csv_dir / "notebook_metrics.csv",
                markdown_file=csv_dir / "markdown.csv",
                code_file=csv_dir / "code.csv",
            ))
            self.assertEqual(len(records), 10)
            rec0 = records[0]
            self.assertTrue(len(rec0.markdown_cells) > 0)
            self.assertTrue(len(rec0.code_cells) > 0)
            self.assertIn("Kernel ID", rec0.metadata)

    def test_stream_from_parquet(self):
        import pandas as pd
        from scripts.datasets.process_distilkaggle import stream_from_parquet
        with tempfile.TemporaryDirectory() as tmpdir:
            pq_file = Path(tmpdir) / "test_data.parquet"
            df = pd.DataFrame([
                {
                    "Kernel ID": "k_pq_1",
                    "Markdown": "# Parquet ML Notebook\n## Problem Statement\nPredict latency",
                    "Code": "import pandas as pd\nimport lightgbm as lgb\ndf = pd.read_parquet('traffic.parquet')",
                    "is_gpu_enabled": 0,
                    "total_memory_usage": 512.0,
                }
            ])
            df.to_parquet(pq_file)

            records = list(stream_from_parquet(pq_file))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].kernel_id, "k_pq_1")
            self.assertIn("Predict latency", records[0].markdown_cells[0])
            self.assertIn("lightgbm", records[0].code_cells[0])


if __name__ == "__main__":
    unittest.main()
