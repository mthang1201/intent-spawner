"""Unit tests for DS-1000 dataset extraction script."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.datasets.extract_ds1000 import (
    CANONICAL_LIBRARIES,
    build_arg_parser,
    extract_and_save_ds1000,
    fetch_dataset,
    generate_problem_id,
    load_ds1000_from_file,
    main,
    normalize_library_name,
    parse_raw_record,
)

REQUIRED_SCHEMA_KEYS = {"problem_id", "library", "prompt", "reference_code", "test_code"}


@pytest.fixture
def mock_pandas_record():
    return {
        "prompt": "Problem:\nI have a DataFrame df. How to select rows by integer index list?",
        "reference_code": "def g(df, List):\n    return df.iloc[List]\nresult = g(df.copy(), List)",
        "code_context": (
            "import pandas as pd\n"
            "def generate_test_case(test_case_id):\n"
            "    return test_input, expected\n"
            "def test_execution(solution):\n"
            "    assert True\n"
        ),
        "metadata": {
            "problem_id": 12,
            "library_problem_id": 12,
            "library": "Pandas",
            "test_case_cnt": 2,
            "perturbation_type": "Surface",
            "perturbation_origin_id": 11,
        },
    }


@pytest.fixture
def mock_pytorch_record():
    return {
        "prompt": "How to perform matrix multiplication in PyTorch?",
        "reference_code": "result = torch.matmul(a, b)",
        "code_context": (
            "import torch\n"
            "def test_execution(solution):\n"
            "    assert True\n"
        ),
        "metadata": {
            "problem_id": 937,
            "library_problem_id": 5,
            "library": "Pytorch",
            "test_case_cnt": 1,
            "perturbation_type": "Surface",
            "perturbation_origin_id": 4,
        },
    }


@pytest.fixture
def mock_sklearn_record():
    return {
        "prompt": "Fit a Random Forest on Iris data.",
        "reference_code": "clf = RandomForestClassifier().fit(X, y)",
        "code_context": (
            "from sklearn.ensemble import RandomForestClassifier\n"
            "def test_execution(solution):\n"
            "    assert True\n"
        ),
        "metadata": {
            "problem_id": 817,
            "library_problem_id": 0,
            "library": "Sklearn",
            "test_case_cnt": 1,
        },
    }


def test_parse_pandas_record(mock_pandas_record):
    parsed = parse_raw_record(mock_pandas_record)
    assert set(parsed.keys()) == REQUIRED_SCHEMA_KEYS
    assert parsed["problem_id"] == "Pandas_12"
    assert parsed["library"] == "Pandas"
    assert "I have a DataFrame df" in parsed["prompt"]
    assert "df.iloc[List]" in parsed["reference_code"]
    assert "def test_execution" in parsed["test_code"]


def test_parse_pytorch_record(mock_pytorch_record):
    parsed = parse_raw_record(mock_pytorch_record)
    assert set(parsed.keys()) == REQUIRED_SCHEMA_KEYS
    assert parsed["problem_id"] == "PyTorch_5"
    assert parsed["library"] == "Pytorch"
    assert "matrix multiplication" in parsed["prompt"]
    assert "torch.matmul" in parsed["reference_code"]
    assert "def test_execution" in parsed["test_code"]


def test_parse_sklearn_normalization(mock_sklearn_record):
    parsed = parse_raw_record(mock_sklearn_record)
    assert set(parsed.keys()) == REQUIRED_SCHEMA_KEYS
    assert parsed["library"] == "Scikit-learn"
    assert parsed["problem_id"] == "Scikit-learn_0"


def test_all_seven_target_libraries_normalization():
    expected_mappings = {
        "pandas": "Pandas",
        "numpy": "Numpy",
        "matplotlib": "Matplotlib",
        "sklearn": "Scikit-learn",
        "scikit-learn": "Scikit-learn",
        "scipy": "Scipy",
        "pytorch": "Pytorch",
        "torch": "Pytorch",
        "tensorflow": "Tensorflow",
        "tf": "Tensorflow",
    }
    for raw, expected in expected_mappings.items():
        assert normalize_library_name(raw) == expected


def test_generate_problem_id():
    assert generate_problem_id("Pandas", 12) == "Pandas_12"
    assert generate_problem_id("Pytorch", 5) == "PyTorch_5"
    assert generate_problem_id("Scikit-learn", 3) == "Scikit-learn_3"
    assert generate_problem_id("Numpy", 0) == "Numpy_0"
    assert generate_problem_id("Matplotlib", 7) == "Matplotlib_7"
    assert generate_problem_id("Scipy", 9) == "Scipy_9"
    assert generate_problem_id("Tensorflow", 2) == "Tensorflow_2"


def test_parse_record_with_string_metadata():
    raw = {
        "prompt": "Test prompt",
        "reference_code": "result = 42",
        "code_context": "assert result == 42",
        "metadata": json.dumps({"library": "Numpy", "library_problem_id": 4, "problem_id": 4}),
    }
    parsed = parse_raw_record(raw)
    assert parsed["problem_id"] == "Numpy_4"
    assert parsed["library"] == "Numpy"
    assert parsed["reference_code"] == "result = 42"
    assert parsed["test_code"] == "assert result == 42"


def test_parse_record_fallback_defaults():
    raw = {
        "intent": "Compute dot product",
        "solution": "np.dot(a, b)",
        "test_case": "assert True",
    }
    parsed = parse_raw_record(raw, fallback_index=99)
    assert parsed["prompt"] == "Compute dot product"
    assert parsed["reference_code"] == "np.dot(a, b)"
    assert parsed["test_code"] == "assert True"
    assert parsed["library"] == "Unknown"
    assert parsed["problem_id"] == "Unknown_99"


def test_extract_and_save_subset_with_sample_size(tmp_path, mock_pandas_record, mock_pytorch_record, mock_sklearn_record):
    records = [mock_pandas_record, mock_pytorch_record, mock_sklearn_record]

    # Test sample size 2
    out_dir = tmp_path / "ds1000_subset"
    saved = extract_and_save_ds1000(records, output_dir=out_dir, sample_size=2)
    assert len(saved) == 2

    # Check that individual JSON files exist on disk
    file_1 = out_dir / "Pandas_12.json"
    file_2 = out_dir / "PyTorch_5.json"
    file_3 = out_dir / "Scikit-learn_0.json"
    assert file_1.is_file()
    assert file_2.is_file()
    assert not file_3.is_file()

    # Verify JSON content
    with open(file_1, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert set(data.keys()) == REQUIRED_SCHEMA_KEYS
        assert data["problem_id"] == "Pandas_12"
        assert data["library"] == "Pandas"

    # Verify manifest.json
    manifest_path = out_dir / "manifest.json"
    assert manifest_path.is_file()
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
        assert manifest["total_extracted"] == 2
        assert manifest["sample_size"] == 2


def test_extract_and_save_full_dataset(tmp_path, mock_pandas_record, mock_pytorch_record, mock_sklearn_record):
    records = [mock_pandas_record, mock_pytorch_record, mock_sklearn_record]
    out_dir = tmp_path / "ds1000_full"
    saved = extract_and_save_ds1000(records, output_dir=out_dir, sample_size=None)
    assert len(saved) == 3

    assert (out_dir / "Pandas_12.json").is_file()
    assert (out_dir / "PyTorch_5.json").is_file()
    assert (out_dir / "Scikit-learn_0.json").is_file()


def test_load_from_local_jsonl_gz(tmp_path, mock_pandas_record, mock_pytorch_record):
    file_path = tmp_path / "mock_ds1000.jsonl.gz"
    content = "\n".join([json.dumps(mock_pandas_record), json.dumps(mock_pytorch_record)])
    with gzip.open(file_path, "wt", encoding="utf-8") as f:
        f.write(content)

    loaded = load_ds1000_from_file(file_path)
    assert len(loaded) == 2
    assert loaded[0]["metadata"]["library"] == "Pandas"
    assert loaded[1]["metadata"]["library"] == "Pytorch"


def test_cli_argument_parsing():
    parser = build_arg_parser()
    args = parser.parse_args(["--sample-size", "15", "--output-dir", "custom/path", "--dry-run"])
    assert args.sample_size == 15
    assert args.output_dir == "custom/path"
    assert args.dry_run is True


def test_cli_main_success(tmp_path, mock_pandas_record, mock_pytorch_record):
    with patch("scripts.datasets.extract_ds1000.fetch_dataset") as mock_fetch:
        mock_fetch.return_value = ([mock_pandas_record, mock_pytorch_record], "mock_source")
        out_dir = str(tmp_path / "cli_out")
        exit_code = main(["--output-dir", out_dir, "--sample-size", "1"])
        assert exit_code == 0
        assert (Path(out_dir) / "Pandas_12.json").is_file()
