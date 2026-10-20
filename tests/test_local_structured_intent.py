"""Unit tests for the Lightweight Local NLP StructuredIntent Extractor.

Verifies:
1. Multilingual Normalization & Clause Tokenizer (English & Vietnamese).
2. Domain Gazetteer / Ontology Matching (Deep Learning, Data Engineering, ML, Visualization).
3. Negation Scope & Modality Analysis (REQUIRED, PREFERRED, FORBIDDEN).
4. Resource Extraction (CPU, Memory, Dataset size) and Task Classification (TRAINING, DATA_ANALYSIS, INFERENCE, EDA).
5. Contract Integrity, Provenance (extractor_name = 'p2-local-nlp-extractor'), SHA-256 hash, latency, and determinism.
"""

from __future__ import annotations

import re
import time
import pytest

from recommender.local_structured_intent import (
    DOMAIN_ONTOLOGY,
    IMPORT_ALIASES,
    LOCAL_EXTRACTOR_NAME,
    LOCAL_EXTRACTOR_PROMPT_SHA256,
    LOCAL_EXTRACTOR_VERSION,
    LocalStructuredIntentExtractor,
    _normalize_text,
    _split_clauses,
    local_parser_contract_sha256,
)
from recommender.models import (
    ExtractionMode,
    GPURequirement,
    RecommendationRequest,
    StructuredIntent,
    TaskType,
)


@pytest.fixture
def extractor() -> LocalStructuredIntentExtractor:
    return LocalStructuredIntentExtractor()


# =============================================================================
# 1. MULTILINGUAL NORMALIZATION & TOKENIZER TESTS
# =============================================================================

class TestMultilingualNormalizationAndTokenizer:
    def test_unicode_nfkc_normalization(self):
        # Decomposed Vietnamese "e" + combining acute -> precomposed "é"
        decomposed = "huâ\u0301n luyê\u0323n mô hi\u0300nh"
        normalized = _normalize_text(decomposed)
        assert normalized == "huấn luyện mô hình"

    def test_whitespace_and_quote_standardization(self):
        raw = "   Train  a   model   with   “PyTorch”   and   ‘CUDA’...   "
        normalized = _normalize_text(raw)
        assert normalized == 'Train a model with "PyTorch" and \'CUDA\'...'

    def test_clause_splitting_english_and_vietnamese(self):
        text_en = "I want to use PyTorch, but strictly avoid TensorFlow. Without GPU."
        clauses_en = _split_clauses(text_en)
        assert len(clauses_en) >= 3
        assert "I want to use PyTorch" in clauses_en[0]

        text_vi = "Tôi cần dùng Pandas để phân tích, nhưng không cần GPU; tối thiểu 16 GB RAM."
        clauses_vi = _split_clauses(text_vi)
        assert len(clauses_vi) >= 3
        assert any("không cần GPU" in c for c in clauses_vi)


# =============================================================================
# 2. DOMAIN GAZETTEER / ONTOLOGY MATCHING TESTS
# =============================================================================

class TestDomainGazetteerOntology:
    def test_ontology_contains_all_required_entities(self):
        # Deep Learning
        dl_entities = {"pytorch", "tensorflow", "keras", "jax", "huggingface", "cuda", "tensorrt"}
        # Data Engineering / Tabular
        de_entities = {"pandas", "polars", "duckdb", "dask", "spark", "pyarrow"}
        # Machine Learning
        ml_entities = {"scikit-learn", "xgboost", "lightgbm", "catboost"}
        # Visualization / Light EDA
        vis_entities = {"matplotlib", "seaborn", "plotly"}

        all_required = dl_entities | de_entities | ml_entities | vis_entities
        assert all_required.issubset(set(DOMAIN_ONTOLOGY))

    @pytest.mark.parametrize(
        "phrase,expected_framework",
        [
            ("Run training using PyTorch", "pytorch"),
            ("Distributed pipeline with TensorFlow", "tensorflow"),
            ("Build neural network in Keras", "keras"),
            ("Accelerate computation via JAX", "jax"),
            ("NLP transformer fine-tuning with HuggingFace", "huggingface"),
            ("Deep learning on CUDA 12", "cuda"),
            ("Optimized inference with TensorRT", "tensorrt"),
        ],
    )
    def test_deep_learning_entities_recognized(self, extractor, phrase, expected_framework):
        intent = extractor.extract(RecommendationRequest(intent=phrase))
        all_facts = set(intent.required_frameworks) | set(intent.required_libraries) | set(intent.required_features)
        assert expected_framework in all_facts

    @pytest.mark.parametrize(
        "phrase,expected_library",
        [
            ("Analyze data with Pandas", "pandas"),
            ("Fast dataframe manipulation using Polars", "polars"),
            ("Query parquet files with DuckDB", "duckdb"),
            ("Distributed analytics with Dask", "dask"),
            ("ETL pipeline on Apache Spark", "spark"),
            ("Columnar storage with PyArrow", "pyarrow"),
        ],
    )
    def test_data_engineering_tabular_entities_recognized(self, extractor, phrase, expected_library):
        intent = extractor.extract(RecommendationRequest(intent=phrase))
        assert expected_library in intent.required_libraries

    @pytest.mark.parametrize(
        "phrase,expected_library",
        [
            ("Train random forest with Scikit-learn", "scikit-learn"),
            ("Gradient boosting classification via XGBoost", "xgboost"),
            ("Fast gradient boosted trees with LightGBM", "lightgbm"),
            ("Categorical feature modeling using CatBoost", "catboost"),
        ],
    )
    def test_machine_learning_entities_recognized(self, extractor, phrase, expected_library):
        intent = extractor.extract(RecommendationRequest(intent=phrase))
        assert expected_library in intent.required_libraries
        assert expected_library in intent.required_frameworks

    @pytest.mark.parametrize(
        "phrase,expected_library",
        [
            ("Plot charts using Matplotlib", "matplotlib"),
            ("Statistical visualizations with Seaborn", "seaborn"),
            ("Interactive dashboards with Plotly", "plotly"),
        ],
    )
    def test_visualization_entities_recognized(self, extractor, phrase, expected_library):
        intent = extractor.extract(RecommendationRequest(intent=phrase))
        assert expected_library in intent.required_libraries


# =============================================================================
# 3. NEGATION SCOPE & MODALITY ANALYSIS TESTS
# =============================================================================

class TestNegationScopeAndModality:
    def test_strictly_avoid_tensorflow(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(intent="Train a classifier with PyTorch, strictly avoid TensorFlow.")
        )
        assert "tensorflow" in intent.forbidden_features
        assert "tensorflow" not in intent.required_frameworks
        assert "tensorflow" not in intent.required_libraries
        assert "pytorch" in intent.required_frameworks

    def test_without_gpu_sets_forbidden(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(intent="Data processing script without GPU, requires 8 cores.")
        )
        assert intent.resource_constraints.gpu_requirement is GPURequirement.FORBIDDEN
        assert "gpu" in intent.forbidden_features

    def test_vietnamese_cpu_only_and_no_gpu(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(intent="Chỉ dùng CPU, không cần GPU, phân tích dữ liệu bảng với Pandas.")
        )
        assert intent.resource_constraints.gpu_requirement is GPURequirement.FORBIDDEN
        assert "gpu" in intent.forbidden_features
        assert "pandas" in intent.required_libraries

    def test_preference_modality(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(
                intent="Analyze tabular dataset with Pandas. Prefer Seaborn and Plotly, avoid Matplotlib."
            )
        )
        assert "pandas" in intent.required_libraries
        assert "seaborn" in intent.preferred_libraries
        assert "plotly" in intent.preferred_libraries
        assert "matplotlib" in intent.forbidden_features
        assert "matplotlib" not in intent.preferred_libraries

    def test_negation_scope_isolation_across_clauses(self, extractor):
        # Negation in clause 2 must not spill back into clause 1
        intent = extractor.extract(
            RecommendationRequest(
                intent="Tôi muốn dùng PyTorch để huấn luyện mô hình, nhưng tuyệt đối không cài TensorFlow."
            )
        )
        assert "pytorch" in intent.required_frameworks
        assert "pytorch" in intent.required_libraries
        assert "tensorflow" in intent.forbidden_features
        assert "pytorch" not in intent.forbidden_features

    def test_gpu_required_modality(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(intent="Deep learning training requires a GPU and CUDA acceleration.")
        )
        assert intent.resource_constraints.gpu_requirement is GPURequirement.REQUIRED
        assert "cuda" in intent.required_features

    def test_gpu_preferred_modality(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(intent="Data exploration with pandas, would like a GPU if possible.")
        )
        assert intent.resource_constraints.gpu_requirement is GPURequirement.PREFERRED


# =============================================================================
# 4. RESOURCE EXTRACTION & TASK CLASSIFICATION TESTS
# =============================================================================

class TestResourceExtractionAndTaskClassification:
    def test_cpu_and_memory_extraction_english(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(
                intent="This workload needs at least 8 CPU cores with minimum 32 GB memory."
            )
        )
        assert intent.resource_constraints.minimum_cpu_cores == 8.0
        assert intent.resource_constraints.minimum_memory_gb == 32.0

    def test_cpu_and_memory_extraction_vietnamese(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(
                intent="Tác vụ cần tối thiểu 16 nhân cpu và ít nhất 64 gb ram."
            )
        )
        assert intent.resource_constraints.minimum_cpu_cores == 16.0
        assert intent.resource_constraints.minimum_memory_gb == 64.0

    def test_memory_mb_conversion(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(intent="Small notebook with 512 MB RAM and 2 cores.")
        )
        assert intent.resource_constraints.minimum_cpu_cores == 2.0
        assert intent.resource_constraints.minimum_memory_gb == 0.5

    def test_dataset_size_extraction_natural_language(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(
                intent="Processing a 25 GB dataset using Polars and DuckDB."
            )
        )
        assert intent.resource_constraints.dataset_size_gb == 25.0
        assert "polars" in intent.required_libraries
        assert "duckdb" in intent.required_libraries

    def test_dataset_size_extraction_vietnamese(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(
                intent="Xử lý tập dữ liệu 50 gb trên 8 nhân cpu."
            )
        )
        assert intent.resource_constraints.dataset_size_gb == 50.0
        assert intent.resource_constraints.minimum_cpu_cores == 8.0

    def test_explicit_form_dataset_size_precedence(self, extractor):
        # When request.dataset_size_gb is supplied explicitly, it takes precedence
        intent = extractor.extract(
            RecommendationRequest(
                intent="Text mentions 25 GB dataset",
                dataset_size_gb=10.0,
            )
        )
        assert intent.resource_constraints.dataset_size_gb == 10.0

    def test_task_classification_training(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(intent="Fine-tuning a deep neural network with PyTorch for 10 epochs.")
        )
        assert TaskType.MODEL_TRAINING in intent.task_types
        assert TaskType.DEEP_LEARNING in intent.task_types

    def test_task_classification_training_vietnamese(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(intent="Huấn luyện mô hình học máy với Scikit-learn.")
        )
        assert TaskType.MODEL_TRAINING in intent.task_types
        assert TaskType.MACHINE_LEARNING in intent.task_types

    def test_task_classification_data_analysis(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(intent="Phân tích dữ liệu bảng với Pandas và thực hiện thống kê mô tả.")
        )
        assert TaskType.DATA_ANALYSIS in intent.task_types

    def test_task_classification_inference(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(intent="Deploy ResNet model for real-time inference and prediction scoring.")
        )
        assert TaskType.MODEL_INFERENCE in intent.task_types

    def test_task_classification_inference_vietnamese(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(intent="Chạy suy luận và dự đoán nhãn với mô hình đã huấn luyện.")
        )
        assert TaskType.MODEL_INFERENCE in intent.task_types

    def test_task_classification_eda(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(intent="Thực hiện EDA và trực quan hóa phân bố dữ liệu với Seaborn.")
        )
        assert TaskType.DATA_ANALYSIS in intent.task_types
        assert TaskType.VISUALIZATION in intent.task_types


# =============================================================================
# 5. CONTRACT INTEGRITY, PROVENANCE, AND PERFORMANCE
# =============================================================================

class TestContractIntegrityAndPerformance:
    def test_provenance_metadata(self, extractor):
        intent = extractor.extract(
            RecommendationRequest(intent="Analyze dataframe with pandas")
        )
        assert isinstance(intent, StructuredIntent)
        prov = intent.extraction_provenance
        assert prov.extractor_name == "p2-local-nlp-extractor"
        assert prov.extractor_version == LOCAL_EXTRACTOR_VERSION
        assert prov.mode is ExtractionMode.PRIMARY
        assert prov.degraded_reason is None
        assert prov.prompt_sha256 == LOCAL_EXTRACTOR_PROMPT_SHA256
        assert re.fullmatch(r"[0-9a-f]{64}", prov.prompt_sha256)

    def test_contract_sha256_reproducibility(self):
        digest1 = local_parser_contract_sha256()
        digest2 = local_parser_contract_sha256()
        assert digest1 == digest2
        assert len(digest1) == 64

    def test_zero_network_bound(self, extractor):
        assert extractor.network_bound is False

    def test_execution_latency_under_5ms(self, extractor):
        request = RecommendationRequest(
            intent="Huấn luyện mô hình PyTorch với 8 nhân CPU, 32 GB RAM, tập dữ liệu 15 GB, không dùng TensorFlow.",
            code_context="import torch\nimport pandas as pd",
        )
        # Warmup
        extractor.extract(request)

        # Measure 100 runs
        start = time.perf_counter()
        iterations = 100
        for _ in range(iterations):
            extractor.extract(request)
        total_time = time.perf_counter() - start

        avg_latency_ms = (total_time / iterations) * 1000.0
        # Strict latency requirement: < 5 ms per extraction
        assert avg_latency_ms < 5.0, f"Average latency too high: {avg_latency_ms:.3f} ms"

    def test_deterministic_output(self, extractor):
        request = RecommendationRequest(
            intent="This job requires a GPU, at least 4 cores and 16 GB RAM. Prefer Seaborn.",
            code_context="import pandas as pd\nfrom sklearn.model_selection import train_test_split",
            dataset_size_gb=5.0,
        )
        first = extractor.extract(request).to_json()
        for _ in range(20):
            assert extractor.extract(request).to_json() == first

    def test_empty_request_graceful_handling(self, extractor):
        intent = extractor.extract(RecommendationRequest(intent=""))
        assert isinstance(intent, StructuredIntent)
        assert intent.required_libraries == ()
        assert intent.forbidden_features == ()
        assert intent.resource_constraints.gpu_requirement is GPURequirement.UNSPECIFIED
        assert intent.resource_constraints.minimum_cpu_cores is None
        assert intent.resource_constraints.minimum_memory_gb is None
        assert intent.resource_constraints.dataset_size_gb is None
