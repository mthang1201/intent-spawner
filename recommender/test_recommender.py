import json
import subprocess
import sys

import pytest

from recommender.recommender import load_image_catalog, recommend_profile


def test_basic_python_recommends_small():
    rec = recommend_profile("basic Python loops", 0.05, "print('hello')")
    assert rec.profile == "small"
    assert "basic/light workload context" in rec.reasons
    assert rec.image_id == "minimal-python"


def test_pandas_read_csv_08gb_recommends_medium():
    rec = recommend_profile(
        "explore a CSV file",
        0.8,
        "import pandas as pd\ndf = pd.read_csv('data.csv')",
    )
    assert rec.profile == "medium"
    assert rec.image_id == "scipy-data-science"
    assert any("dataset size >= 0.5GB" in reason for reason in rec.reasons)


def test_sklearn_train_fit_15gb_recommends_large():
    rec = recommend_profile(
        "I will train a scikit-learn model on a 1.5GB CSV dataset",
        1.5,
        "import pandas as pd\nfrom sklearn.ensemble import RandomForestClassifier\nmodel.fit(X, y)",
    )
    assert rec.profile == "large"
    assert rec.image_id == "scipy-data-science"
    assert any("training/modeling context" in reason for reason in rec.reasons)


def test_torch_cuda_deep_learning_recommends_gpu_or_large():
    rec = recommend_profile(
        "deep learning image classifier",
        0.2,
        "import torch\nmodel.cuda()",
    )
    assert rec.profile == "gpu_or_large"
    assert rec.image_id == "pytorch-deep-learning"
    assert any("GPU/deep-learning context" in reason for reason in rec.reasons)


@pytest.mark.parametrize(
    ("intent", "dataset_size_gb", "code_context", "expected_profile", "reason_fragment"),
    [
        ("basic Python loops", 0.05, "print('hello')", "small", "basic/light workload context"),
        (
            "explore a medium CSV file",
            0.8,
            "import pandas as pd\ndf = pd.read_csv('data.csv')",
            "medium",
            "dataset size >= 0.5GB",
        ),
        (
            "train a sklearn model",
            1.5,
            "from sklearn.linear_model import LogisticRegression\nmodel.fit(X, y)",
            "large",
            "training/modeling context",
        ),
        (
            "deep learning image classifier",
            0.2,
            "import torch\nmodel.cuda()",
            "gpu_or_large",
            "GPU/deep-learning context",
        ),
    ],
)
def test_recommender_workload_signal_matrix(
    intent,
    dataset_size_gb,
    code_context,
    expected_profile,
    reason_fragment,
):
    rec = recommend_profile(intent, dataset_size_gb, code_context)

    assert rec.profile == expected_profile
    assert any(reason_fragment in reason for reason in rec.reasons)
    assert rec.score >= 0


def test_conflicting_light_and_gpu_signals_preserve_gpu_safety_signal():
    rec = recommend_profile(
        "quick tiny notebook, please keep it light",
        0.01,
        "import torch\n# experimenting with cuda kernels\nmodel.cuda()",
    )

    assert rec.profile == "gpu_or_large"
    assert rec.score == 99
    assert rec.reasons == [
        "GPU/deep-learning context detected: torch, cuda",
        "Demo environment has no real GPU, so this maps to Large resources.",
    ]


@pytest.mark.parametrize("dataset_size_gb", [None, "", "not-a-number", -1, float("inf"), float("nan")])
def test_invalid_or_missing_dataset_size_is_treated_as_unknown(dataset_size_gb):
    rec = recommend_profile("basic Python loops", dataset_size_gb, "")

    assert rec.profile == "small"
    assert rec.reasons == ["basic/light workload context"]


def test_explanation_output_is_deterministic():
    first = recommend_profile(
        "train a scikit-learn model on a 1.5GB CSV dataset",
        1.5,
        "import pandas as pd\nfrom sklearn.ensemble import RandomForestClassifier\nmodel.fit(X, y)",
    )
    second = recommend_profile(
        "train a scikit-learn model on a 1.5GB CSV dataset",
        1.5,
        "import pandas as pd\nfrom sklearn.ensemble import RandomForestClassifier\nmodel.fit(X, y)",
    )

    assert first.to_dict() == second.to_dict()
    assert first.reasons[0] == "dataset size >= 0.5GB"
    assert first.reasons[1].startswith("data-processing context detected:")
    assert first.reasons[2].startswith("training/modeling context detected:")


def test_cli_emits_machine_readable_explanation_json():
    result = subprocess.run(
        [
            sys.executable,
            "recommender/recommender.py",
            "--intent",
            "train a sklearn model",
            "--dataset-gb",
            "1.5",
            "--code-context",
            "import pandas as pd\nmodel.fit(X, y)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["profile"] == "large"
    assert payload["score"] == 4
    assert payload["image_id"] == "scipy-data-science"
    assert payload["image_reference"].startswith("quay.io/jupyter/scipy-notebook@sha256:")
    assert any("training/modeling context" in reason for reason in payload["reasons"])


def test_tensorflow_takes_higher_priority_than_generic_deep_learning_image():
    rec = recommend_profile(
        "train a deep learning classifier",
        0.2,
        "import tensorflow as tf\nfrom tensorflow import keras",
    )

    assert rec.profile == "gpu_or_large"
    assert rec.image_id == "tensorflow-deep-learning"
    assert any("tensorflow" in reason for reason in rec.image_reasons)


def test_catalog_rejects_mutable_or_user_supplied_image_reference(tmp_path):
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        "catalog_version: test\n"
        "default_image: arbitrary\n"
        "images:\n"
        "  arbitrary:\n"
        "    display_name: Arbitrary\n"
        "    reference: user.example/notebook:latest\n"
        "    description: Not immutable.\n"
        "    capabilities: [python]\n"
        "    match_terms: []\n"
        "    priority: 0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="immutable sha256"):
        load_image_catalog(catalog)
