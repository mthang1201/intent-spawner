from recommender.recommender import recommend_profile


def test_basic_python_recommends_small():
    rec = recommend_profile("basic Python loops", 0.05, "print('hello')")
    assert rec.profile == "small"
    assert "basic/light workload context" in rec.reasons


def test_pandas_read_csv_08gb_recommends_medium():
    rec = recommend_profile(
        "explore a CSV file",
        0.8,
        "import pandas as pd\ndf = pd.read_csv('data.csv')",
    )
    assert rec.profile == "medium"
    assert any("dataset size >= 0.5GB" in reason for reason in rec.reasons)


def test_sklearn_train_fit_15gb_recommends_large():
    rec = recommend_profile(
        "I will train a scikit-learn model on a 1.5GB CSV dataset",
        1.5,
        "import pandas as pd\nfrom sklearn.ensemble import RandomForestClassifier\nmodel.fit(X, y)",
    )
    assert rec.profile == "large"
    assert any("training/modeling context" in reason for reason in rec.reasons)


def test_torch_cuda_deep_learning_recommends_gpu_or_large():
    rec = recommend_profile(
        "deep learning image classifier",
        0.2,
        "import torch\nmodel.cuda()",
    )
    assert rec.profile == "gpu_or_large"
    assert any("GPU/deep-learning context" in reason for reason in rec.reasons)

