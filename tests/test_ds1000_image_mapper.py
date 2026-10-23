"""Unit tests for DS-1000 Image Catalog and Structured Intent Mapper."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from evaluation_v5.dataset_pipeline.ds1000_image_mapper import (
    LIBRARY_TO_IMAGE,
    extract_auxiliary_libraries_from_ast,
    map_problem,
    process_ds1000_directory,
    validate_catalog,
)
from recommender.models import StructuredIntent, TaskType
from recommender.rule_based import DEFAULT_CATALOG_PATH


@pytest.fixture
def valid_catalog():
    return validate_catalog(DEFAULT_CATALOG_PATH)


def test_validate_catalog_success(valid_catalog):
    assert "images" in valid_catalog
    for target_image in ("scipy-data-science", "pytorch-deep-learning", "tensorflow-deep-learning"):
        assert target_image in valid_catalog["images"]
        assert "reference" in valid_catalog["images"][target_image]


def test_validate_catalog_missing_image_raises():
    bad_catalog = {
        "catalog_version": "2026-08-06.1",
        "default_image": "minimal-python",
        "images": {
            "minimal-python": {
                "display_name": "Minimal Python",
                "reference": "quay.io/jupyter/minimal-notebook@sha256:" + "a" * 64,
                "description": "test",
                "capabilities": ["python"],
                "match_terms": [],
                "priority": 0,
            }
        },
    }
    with pytest.raises(ValueError, match="missing required images"):
        from unittest.mock import patch
        with patch("evaluation_v5.dataset_pipeline.ds1000_image_mapper.load_image_catalog", return_value=bad_catalog):
            validate_catalog()


@pytest.mark.parametrize(
    "library,expected_image",
    [
        ("Pandas", "scipy-data-science"),
        ("pandas", "scipy-data-science"),
        ("Numpy", "scipy-data-science"),
        ("numpy", "scipy-data-science"),
        ("Matplotlib", "scipy-data-science"),
        ("matplotlib", "scipy-data-science"),
        ("Scikit-learn", "scipy-data-science"),
        ("scikit-learn", "scipy-data-science"),
        ("sklearn", "scipy-data-science"),
        ("Scipy", "scipy-data-science"),
        ("scipy", "scipy-data-science"),
        ("Pytorch", "pytorch-deep-learning"),
        ("pytorch", "pytorch-deep-learning"),
        ("Tensorflow", "tensorflow-deep-learning"),
        ("tensorflow", "tensorflow-deep-learning"),
    ],
)
def test_library_to_image_mapping(library, expected_image, valid_catalog):
    problem = {
        "problem_id": f"{library}_1",
        "library": library,
        "prompt": f"How to use {library}?",
        "reference_code": "x = 42",
    }
    result = map_problem(problem, valid_catalog)
    assert result["image"] == expected_image
    assert result["image_id"] == expected_image
    assert result["image_reference"] == valid_catalog["images"][expected_image]["reference"]
    assert result["problem_id"] == f"{library}_1"
    assert result["library"] == library


def test_ast_auxiliary_libraries_detection():
    code = textwrap_code = """
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np

def plot_heatmap(matrix):
    sns.heatmap(matrix)
    plt.title("Heatmap")
    plt.show()
    return matrix
"""
    detected = extract_auxiliary_libraries_from_ast(code)
    assert "matplotlib" in detected
    assert "seaborn" in detected
    assert "numpy" in detected


def test_ast_auxiliary_libraries_syntax_resilience():
    # Code with indentation and implicit alias calls
    indented_code = "    plt.plot(x, y)\n    sns.despine()\n"
    detected = extract_auxiliary_libraries_from_ast(indented_code)
    assert "matplotlib" in detected
    assert "seaborn" in detected


def test_structured_intent_contract_conformance(valid_catalog):
    problem = {
        "problem_id": "Pandas_99",
        "library": "Pandas",
        "prompt": "Filter rows and plot distribution with seaborn",
        "reference_code": "import seaborn as sns\nsns.histplot(df['col'])",
    }
    result = map_problem(problem, valid_catalog)

    # Check top-level fields
    assert result["image"] == "scipy-data-science"
    assert "pandas" in result["required_features"]
    assert "seaborn" in result["preferred_features"]
    assert "visualization" in result["preferred_features"]
    assert "data_analysis" in result["task_types"]
    assert "visualization" in result["task_types"]

    # Reconstruct and validate StructuredIntent from nested dict
    intent_dict = result["structured_intent"]
    reconstructed = StructuredIntent.from_dict(intent_dict)
    assert isinstance(reconstructed, StructuredIntent)
    assert "pandas" in reconstructed.required_features
    assert "seaborn" in reconstructed.preferred_features
    # Verify no overlap
    assert set(reconstructed.required_features).isdisjoint(set(reconstructed.preferred_features))


def test_empty_library_or_id_raises(valid_catalog):
    with pytest.raises(ValueError, match="empty 'library'"):
        map_problem({"problem_id": "P_1", "library": "", "prompt": "...", "reference_code": ""}, valid_catalog)

    with pytest.raises(ValueError, match="missing 'problem_id'"):
        map_problem({"problem_id": "", "library": "Pandas", "prompt": "...", "reference_code": ""}, valid_catalog)

    with pytest.raises(ValueError, match="unmapped library"):
        map_problem({"problem_id": "P_2", "library": "UnknownLib", "prompt": "...", "reference_code": ""}, valid_catalog)


def test_process_ds1000_directory_mock(tmp_path, valid_catalog):
    in_dir = tmp_path / "ds1000_mock"
    in_dir.mkdir()

    problems = [
        {"problem_id": "Pandas_1", "library": "Pandas", "prompt": "Pandas task", "reference_code": "df.head()"},
        {"problem_id": "Numpy_1", "library": "Numpy", "prompt": "Numpy task", "reference_code": "np.zeros(5)"},
        {"problem_id": "Matplotlib_1", "library": "Matplotlib", "prompt": "Plot task", "reference_code": "plt.plot()"},
        {"problem_id": "Pytorch_1", "library": "Pytorch", "prompt": "Torch task", "reference_code": "torch.randn(3)"},
        {"problem_id": "Tensorflow_1", "library": "Tensorflow", "prompt": "TF task", "reference_code": "tf.constant(1)"},
    ]

    for p in problems:
        with open(in_dir / f"{p['problem_id']}.json", "w", encoding="utf-8") as f:
            json.dump(p, f)

    out_file = tmp_path / "output.jsonl"
    results = process_ds1000_directory(in_dir, out_file)

    assert len(results) == 5
    assert out_file.is_file()

    with open(out_file, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f]

    assert len(lines) == 5
    images = {l["image"] for l in lines}
    assert images == {"scipy-data-science", "pytorch-deep-learning", "tensorflow-deep-learning"}
    for l in lines:
        assert l["image_reference"].startswith("quay.io/jupyter/")
        assert l["problem_id"]
        assert l["library"]
        assert l["required_features"]
        assert l["task_types"]
