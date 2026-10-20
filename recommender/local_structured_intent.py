"""Deterministic, lightweight NLP Information Extraction pipeline for local StructuredIntent extraction.

This module replaces naïve regular expressions with an auditable, lightweight NLP
information extraction architecture:
  1. Multilingual text normalization (Unicode NFKC, whitespace/punctuation handling)
     and clause-level tokenizer supporting English and Vietnamese.
  2. Domain Gazetteer / Data Science Ontology for Deep Learning, Data Engineering,
     Machine Learning, and Visualization.
  3. Negation scope and modality analysis (REQUIRED, PREFERRED, FORBIDDEN).
  4. Slot-filling for quantitative resource constraints (CPU, Memory, Dataset size)
     and confidence-weighted task classification (TRAINING, DATA_ANALYSIS, INFERENCE, EDA).
  5. Strict schema preservation for P2 StructuredIntent contracts with zero network
     dependency, sub-millisecond execution (< 5ms latency), and minimal memory overhead.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
import re
import sys
import unicodedata
from typing import Any, Mapping, Sequence

from .models import (
    ExtractionMode,
    ExtractionProvenance,
    GPURequirement,
    RecommendationRequest,
    ResourceConstraints,
    StructuredIntent,
    TaskType,
)


LOCAL_EXTRACTOR_NAME = "p2-local-nlp-extractor"
LOCAL_EXTRACTOR_VERSION = "local-nlp-extractor-v2.0.0"
LOCAL_EXTRACTOR_MODEL_ID = (
    f"lightweight-nlp-pipeline-v2.0.0-python-{sys.version_info.major}.{sys.version_info.minor}"
)
LOCAL_EXTRACTOR_PROMPT_VERSION = "local-nlp-contract-v2.0.0"
IMPORT_ALIAS_VERSION = "python-import-aliases-v2.0.0"
PYTHON_STDLIB_POLICY_VERSION = "python-stdlib-exclusion-v1.0.0"
PYTHON_STDLIB_MODULES = frozenset(sys.stdlib_module_names)
ONTOLOGY_VERSION = "ds-ontology-v2.0.0"

IMPORT_ALIASES: dict[str, str] = {
    "sklearn": "scikit-learn",
    "torch": "pytorch",
    "tf": "tensorflow",
    "pyspark": "spark",
    "arrow": "pyarrow",
    "xgb": "xgboost",
    "lgb": "lightgbm",
    "lgbm": "lightgbm",
    "plt": "matplotlib",
    "sns": "seaborn",
    "pd": "pandas",
    "pl": "polars",
}


# =============================================================================
# 1. DOMAIN GAZETTEER & DATA SCIENCE ONTOLOGY
# =============================================================================

class Modality(str, Enum):
    """Semantic modality determined by contextual scope analysis."""
    REQUIRED = "required"
    PREFERRED = "preferred"
    FORBIDDEN = "forbidden"
    MENTION = "mention"


@dataclass(frozen=True, slots=True)
class OntologyEntity:
    """Canonical domain entity representation in the scientific stack."""
    canonical_name: str
    category: str  # deep_learning, tabular_data_engineering, machine_learning, visualization, general
    entity_kind: str  # framework, library, feature
    surface_forms: tuple[str, ...]
    task_associations: tuple[TaskType, ...] = ()


DOMAIN_ONTOLOGY: dict[str, OntologyEntity] = {
    # --- Deep Learning ---
    "pytorch": OntologyEntity(
        canonical_name="pytorch",
        category="deep_learning",
        entity_kind="framework",
        surface_forms=("pytorch", "torch", "torchvision", "torchaudio"),
        task_associations=(TaskType.DEEP_LEARNING, TaskType.MODEL_TRAINING),
    ),
    "tensorflow": OntologyEntity(
        canonical_name="tensorflow",
        category="deep_learning",
        entity_kind="framework",
        surface_forms=("tensorflow", "tf"),
        task_associations=(TaskType.DEEP_LEARNING, TaskType.MODEL_TRAINING),
    ),
    "keras": OntologyEntity(
        canonical_name="keras",
        category="deep_learning",
        entity_kind="framework",
        surface_forms=("keras", "tf.keras"),
        task_associations=(TaskType.DEEP_LEARNING, TaskType.MODEL_TRAINING),
    ),
    "jax": OntologyEntity(
        canonical_name="jax",
        category="deep_learning",
        entity_kind="framework",
        surface_forms=("jax", "flax"),
        task_associations=(TaskType.DEEP_LEARNING, TaskType.MODEL_TRAINING),
    ),
    "huggingface": OntologyEntity(
        canonical_name="huggingface",
        category="deep_learning",
        entity_kind="library",
        surface_forms=("huggingface", "transformers", "datasets", "huggingface_hub", "hf"),
        task_associations=(TaskType.DEEP_LEARNING, TaskType.MODEL_TRAINING, TaskType.MODEL_INFERENCE),
    ),
    "cuda": OntologyEntity(
        canonical_name="cuda",
        category="deep_learning",
        entity_kind="feature",
        surface_forms=("cuda", "cudnn", "cuda-userspace", "cuda 12", "cuda 11"),
        task_associations=(TaskType.DEEP_LEARNING,),
    ),
    "tensorrt": OntologyEntity(
        canonical_name="tensorrt",
        category="deep_learning",
        entity_kind="framework",
        surface_forms=("tensorrt", "trt"),
        task_associations=(TaskType.DEEP_LEARNING, TaskType.MODEL_INFERENCE),
    ),

    # --- Data Engineering / Tabular ---
    "pandas": OntologyEntity(
        canonical_name="pandas",
        category="tabular_data_engineering",
        entity_kind="library",
        surface_forms=("pandas",),
        task_associations=(TaskType.DATA_ANALYSIS, TaskType.DATA_PROCESSING),
    ),
    "polars": OntologyEntity(
        canonical_name="polars",
        category="tabular_data_engineering",
        entity_kind="library",
        surface_forms=("polars",),
        task_associations=(TaskType.DATA_ANALYSIS, TaskType.DATA_PROCESSING),
    ),
    "duckdb": OntologyEntity(
        canonical_name="duckdb",
        category="tabular_data_engineering",
        entity_kind="library",
        surface_forms=("duckdb",),
        task_associations=(TaskType.DATA_ANALYSIS, TaskType.DATA_PROCESSING),
    ),
    "dask": OntologyEntity(
        canonical_name="dask",
        category="tabular_data_engineering",
        entity_kind="library",
        surface_forms=("dask",),
        task_associations=(TaskType.DATA_ANALYSIS, TaskType.DATA_PROCESSING),
    ),
    "spark": OntologyEntity(
        canonical_name="spark",
        category="tabular_data_engineering",
        entity_kind="framework",
        surface_forms=("spark", "pyspark", "apache spark"),
        task_associations=(TaskType.DATA_ANALYSIS, TaskType.DATA_PROCESSING),
    ),
    "pyarrow": OntologyEntity(
        canonical_name="pyarrow",
        category="tabular_data_engineering",
        entity_kind="library",
        surface_forms=("pyarrow", "arrow"),
        task_associations=(TaskType.DATA_ANALYSIS, TaskType.DATA_PROCESSING),
    ),

    # --- Machine Learning ---
    "scikit-learn": OntologyEntity(
        canonical_name="scikit-learn",
        category="machine_learning",
        entity_kind="framework",
        surface_forms=("scikit-learn", "sklearn"),
        task_associations=(TaskType.MACHINE_LEARNING, TaskType.MODEL_TRAINING),
    ),
    "xgboost": OntologyEntity(
        canonical_name="xgboost",
        category="machine_learning",
        entity_kind="framework",
        surface_forms=("xgboost", "xgb"),
        task_associations=(TaskType.MACHINE_LEARNING, TaskType.MODEL_TRAINING),
    ),
    "lightgbm": OntologyEntity(
        canonical_name="lightgbm",
        category="machine_learning",
        entity_kind="framework",
        surface_forms=("lightgbm", "lgb", "lgbm"),
        task_associations=(TaskType.MACHINE_LEARNING, TaskType.MODEL_TRAINING),
    ),
    "catboost": OntologyEntity(
        canonical_name="catboost",
        category="machine_learning",
        entity_kind="framework",
        surface_forms=("catboost",),
        task_associations=(TaskType.MACHINE_LEARNING, TaskType.MODEL_TRAINING),
    ),

    # --- Visualization / Light EDA ---
    "matplotlib": OntologyEntity(
        canonical_name="matplotlib",
        category="visualization",
        entity_kind="library",
        surface_forms=("matplotlib",),
        task_associations=(TaskType.VISUALIZATION, TaskType.DATA_ANALYSIS),
    ),
    "seaborn": OntologyEntity(
        canonical_name="seaborn",
        category="visualization",
        entity_kind="library",
        surface_forms=("seaborn",),
        task_associations=(TaskType.VISUALIZATION, TaskType.DATA_ANALYSIS),
    ),
    "plotly": OntologyEntity(
        canonical_name="plotly",
        category="visualization",
        entity_kind="library",
        surface_forms=("plotly",),
        task_associations=(TaskType.VISUALIZATION, TaskType.DATA_ANALYSIS),
    ),

    # --- Standard Scientific Utilities & Environment Features ---
    "numpy": OntologyEntity(
        canonical_name="numpy",
        category="general",
        entity_kind="library",
        surface_forms=("numpy",),
        task_associations=(TaskType.DATA_ANALYSIS, TaskType.SCIENTIFIC_COMPUTING),
    ),
    "scipy": OntologyEntity(
        canonical_name="scipy",
        category="general",
        entity_kind="library",
        surface_forms=("scipy",),
        task_associations=(TaskType.DATA_ANALYSIS, TaskType.SCIENTIFIC_COMPUTING),
    ),
    "jupyterlab": OntologyEntity(
        canonical_name="jupyterlab",
        category="environment",
        entity_kind="feature",
        surface_forms=("jupyterlab", "jupyter-lab", "jupyter notebook", "notebook"),
        task_associations=(TaskType.SOFTWARE_DEVELOPMENT,),
    ),
}


# Pre-build compiled lookup patterns for fast O(N) gazetteer matching
def _build_gazetteer_patterns() -> list[tuple[re.Pattern[str], OntologyEntity]]:
    patterns: list[tuple[re.Pattern[str], OntologyEntity]] = []
    for entity in DOMAIN_ONTOLOGY.values():
        escaped_forms: list[str] = []
        for form in entity.surface_forms:
            if form == "tf":
                # Avoid collision with tf-idf
                escaped_forms.append(r"\btf(?!\s*[-–/]?\s*idf)\b")
            elif form == "arrow":
                escaped_forms.append(r"\barrow\b")
            elif form == "spark":
                escaped_forms.append(r"\b(?:apache\s+)?spark\b")
            else:
                escaped_forms.append(r"\b" + re.escape(form) + r"\b")
        regex = re.compile("|".join(escaped_forms), re.IGNORECASE)
        patterns.append((regex, entity))
    return patterns


_GAZETTEER_MATCHERS = _build_gazetteer_patterns()


# =============================================================================
# 2. PATTERNS: PARSING, NEGATION SCOPE, RESOURCES, AND GPU
# =============================================================================

_IMPORT_PATTERN = re.compile(r"(?m)^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_.-]*)")

# Clause splitting preserves semantic scope between propositions
_CLAUSE_DELIMITER_PATTERN = re.compile(
    r"(?:[.;!?\n]+|\b(?:nhưng|tuy\s+nhiên|song|but|however|whereas|yet|except\s+for|except|instead\s+of|thay\s+vì)\b|,)",
    re.IGNORECASE,
)

# Negation scope cues in English and Vietnamese
_NEGATION_CUES = re.compile(
    r"\b(?:"
    r"strictly\s+avoid|avoid|avoiding|"
    r"strictly\s+no(?:\s+need\s+for)?|no\s+need(?:\s+(?:for|to\s+install))?|"
    r"no\s+demand\s+for|do\s+not\s+(?:need|install|use)|"
    r"don'?t\s+(?:need|install|use)|does\s+not\s+(?:need|install|use)|"
    r"doesn'?t\s+(?:need|install|use)|without|never|"
    r"forbid(?:den)?|prohibit(?:ed)?|disallow(?:ed)?|"
    r"không\s+(?:cần(?:\s+(?:thiết|cài|dùng|sử\s+dụng))?|dùng|cài|sử\s+dụng|được\s+dùng)|"
    r"ko\s+(?:cần|dùng|cài|sử\s+dụng)|k\s+(?:cần|dùng|cài|sử\s+dụng)|"
    r"tuyệt\s+đối\s+(?:không|cấm)|nghiêm\s+cấm|cấm|"
    r"đừng\s+(?:dùng|cài|sử\s+dụng)|tránh|loại\s+bỏ|loại\s+trừ|"
    r"bỏ\s+qua|miễn\s+là\s+không"
    r")\b",
    re.IGNORECASE,
)

_PREFERENCE_CUES = re.compile(
    r"\b(?:"
    r"prefer(?:red)?|preference|would\s+like|nice\s+to\s+have|"
    r"optional|if\s+possible|favor(?:ed)?|ideally|recommend(?:ed)?|"
    r"ưu\s+tiên|thích|nếu\s+có\s+thể|khuyến\s+khích|tốt\s+nhất\s+là|thì\s+tốt|nếu\s+được|tùy\s+chọn"
    r")\b",
    re.IGNORECASE,
)

_REQUIREMENT_CUES = re.compile(
    r"\b(?:"
    r"require[sd]?|must\s+(?:have|use)?|need[sd]?|mandatory|essential|necessary|"
    r"cần(?:\s+thiết)?|bắt\s+buộc|yêu\s+cầu|phải\s+(?:có|dùng)|nhất\s+thiết|đòi\s+hỏi"
    r")\b",
    re.IGNORECASE,
)

# Quantitative resource slot patterns (multilingual EN + VI)
_CPU_PATTERNS = [
    re.compile(
        r"(?:at\s+least|minimum|min\.?|requires?|needs?|cần|tối\s+thiểu|ít\s+nhất|yêu\s+cầu)\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*(?:cpu(?:\s*cores?)?|cores?|vcpus?|nhân(?:\s*cpu)?|core)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b([0-9]+(?:\.[0-9]+)?)\s*(?:[-–\s]*(?:cores?|vcpus?|nhân)\s*(?:cpu)?|cpu(?:\s*cores?)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcpu\s*(?:[:=]|tối\s+thiểu|ít\s+nhất|>=|min\.?)\s*([0-9]+(?:\.[0-9]+)?)\b",
        re.IGNORECASE,
    ),
]

_MEMORY_PATTERNS = [
    re.compile(
        r"(?:at\s+least|minimum|min\.?|requires?|needs?|cần|tối\s+thiểu|ít\s+nhất|yêu\s+cầu)\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*(?:gb|gib)\s*(?:ram|memory|bộ\s*nhớ)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b([0-9]+(?:\.[0-9]+)?)\s*(?:gb|gib)\s*(?:ram|memory|bộ\s*nhớ)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:ram|memory|bộ\s*nhớ)\s*(?:[:=]|tối\s+thiểu|ít\s+nhất|>=|min\.?)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:gb|gib)?\b",
        re.IGNORECASE,
    ),
]

_MEMORY_MB_PATTERNS = [
    re.compile(
        r"(?:at\s+least|minimum|min\.?|requires?|needs?|cần|tối\s+thiểu|ít\s+nhất|yêu\s+cầu)\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*(?:mb|mib)\s*(?:ram|memory|bộ\s*nhớ)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b([0-9]+(?:\.[0-9]+)?)\s*(?:mb|mib)\s*(?:ram|memory|bộ\s*nhớ)\b",
        re.IGNORECASE,
    ),
]

_DATASET_PATTERNS = [
    re.compile(
        r"(?:dataset(?:\s+size)?|data(?:\s+size)?|tập\s*dữ\s*liệu|dung\s*lượng(?:\s*dữ\s*liệu)?|kích\s*thước\s*dữ\s*liệu|dữ\s*liệu)\s*(?:is|of|around|khoảng|là|[:=])?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:gb|gib)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b([0-9]+(?:\.[0-9]+)?)\s*(?:gb|gib)\s*(?:dataset|data|tập\s*dữ\s*liệu|dữ\s*liệu)\b",
        re.IGNORECASE,
    ),
]

# Explicit GPU modality patterns
_GPU_FORBIDDEN = re.compile(
    r"\b(?:no|without|forbid(?:den)?)\s+(?:a\s+)?gpu\b"
    r"|\b(?:strictly\s+)?(?:do\s+not|don't|does\s+not|doesn't)\s+(?:use|need|require)\s+(?:a\s+)?gpu\b"
    r"|\b(?:tuyệt\s+đối\s+)?(?:không|chẳng|k|cấm|nghiêm\s+cấm)\s+(?:cần(?:\s+thiết)?|dùng|cấp|sử\s+dụng|được\s+dùng)?\s*(?:card\s+đồ\s+họa\s+|a\s+)?gpu\b"
    r"|\b(?:cpu\s*[-–]\s*only|only\s+cpu|thuần\s+cpu|chỉ\s+cpu|chỉ\s+dùng\s+cpu)\b",
    re.IGNORECASE,
)
_GPU_REQUIRED = re.compile(
    r"\b(?:require[sd]?|must\s+(?:have|use)|need[sd]?|cần|bắt\s+buộc|yêu\s+cầu)\b[^.\n]{0,40}\bgpu\b"
    r"|\bgpu\b[^.\n]{0,25}\b(?:is\s+required|is\s+mandatory|bắt\s+buộc)\b",
    re.IGNORECASE,
)
_GPU_PREFERRED = re.compile(
    r"\b(?:prefer(?:red)?|would\s+like|ưu\s+tiên)\b[^.\n]{0,40}\bgpu\b"
    r"|\bgpu\b[^.\n]{0,25}\b(?:if\s+possible|nếu\s+có\s+thể|thì\s+tốt)\b",
    re.IGNORECASE,
)
_GPU_MENTION = re.compile(r"\bgpu\b", re.IGNORECASE)


# =============================================================================
# 3. TASK CLASSIFICATION RULES WITH CONFIDENCE WEIGHTS
# =============================================================================

_TASK_CLASSIFICATION_RULES: dict[TaskType, list[tuple[re.Pattern[str], float]]] = {
    TaskType.MODEL_TRAINING: [
        (re.compile(r"\b(?:fine-?tuning|finetune|fine-?tune)\b", re.IGNORECASE), 1.0),
        (re.compile(r"\b(?:training|train|trained)\b", re.IGNORECASE), 0.9),
        (re.compile(r"\b(?:fit|fitting)\b", re.IGNORECASE), 0.8),
        (re.compile(r"\b(?:backprop|backpropagation|epochs?|optimizer)\b", re.IGNORECASE), 0.85),
        (re.compile(r"\b(?:huấn\s+luyện|đào\s+tạo)\b", re.IGNORECASE), 0.95),
        (re.compile(r"\b(?:tinh\s+chỉnh|học\s+mô\s+hình)\b", re.IGNORECASE), 0.9),
    ],
    TaskType.DATA_ANALYSIS: [
        (re.compile(r"\b(?:data\s+analysis|analytics|analyzing|analyze|analysing|analyse)\b", re.IGNORECASE), 0.95),
        (re.compile(r"\b(?:dataframes?|tabular|csv|parquet|aggregate|groupby)\b", re.IGNORECASE), 0.85),
        (re.compile(r"\b(?:phân\s+tích(?:\s+dữ\s+liệu)?|thống\s+kê)\b", re.IGNORECASE), 0.95),
        (re.compile(r"\b(?:xử\s+lý\s+dữ\s+liệu|bảng\s+dữ\s+liệu)\b", re.IGNORECASE), 0.85),
    ],
    TaskType.MODEL_INFERENCE: [
        (re.compile(r"\b(?:inference|inferring|infer)\b", re.IGNORECASE), 0.95),
        (re.compile(r"\b(?:prediction|predicting|predict|predicts)\b", re.IGNORECASE), 0.9),
        (re.compile(r"\b(?:serving|model\s+serving|deploy|deployment)\b", re.IGNORECASE), 0.85),
        (re.compile(r"\b(?:scoring|evaluate\s+model|model\s+evaluation)\b", re.IGNORECASE), 0.8),
        (re.compile(r"\b(?:suy\s+luận|dự\s+đoán|dự\s+báo)\b", re.IGNORECASE), 0.95),
        (re.compile(r"\b(?:chạy\s+mô\s+hình|triển\s+khai\s+mô\s+hình|đánh\s+giá\s+mô\s+hình)\b", re.IGNORECASE), 0.85),
    ],
    TaskType.VISUALIZATION: [
        (re.compile(r"\b(?:eda|exploratory\s+data\s+analysis|exploratory)\b", re.IGNORECASE), 0.95),
        (re.compile(r"\b(?:visualization|visualize|visualizing|visualisation)\b", re.IGNORECASE), 0.9),
        (re.compile(r"\b(?:plotting|plots?|charting|charts?|graphing|graphs?)\b", re.IGNORECASE), 0.85),
        (re.compile(r"\b(?:dashboard|histograms?|scatter\s*plot)\b", re.IGNORECASE), 0.8),
        (re.compile(r"\b(?:trực\s+quan\s+hóa|vẽ\s+biểu\s+đồ|vẽ\s+đồ\s+thị|đồ\s+thị)\b", re.IGNORECASE), 0.95),
        (re.compile(r"\b(?:khám\s+phá\s+dữ\s+liệu)\b", re.IGNORECASE), 0.9),
    ],
    TaskType.DEEP_LEARNING: [
        (re.compile(r"\b(?:deep\s+learning|neural\s+networks?|deep\s+neural)\b", re.IGNORECASE), 0.95),
        (re.compile(r"\b(?:cnn|rnn|lstm|transformer|transformers|llm|bert|gpt|resnet)\b", re.IGNORECASE), 0.9),
        (re.compile(r"\b(?:học\s+sâu|mạng\s+nơ-?ron|mạng\s+neuron)\b", re.IGNORECASE), 0.95),
    ],
    TaskType.MACHINE_LEARNING: [
        (re.compile(r"\b(?:machine\s+learning|ml\s+model|traditional\s+ml)\b", re.IGNORECASE), 0.95),
        (re.compile(r"\b(?:classification|regression|clustering|random\s+forest|gradient\s+boosting)\b", re.IGNORECASE), 0.85),
        (re.compile(r"\b(?:máy\s+học|học\s+máy|phân\s+loại|hồi\s+quy|gom\s+cụm)\b", re.IGNORECASE), 0.95),
    ],
}


# =============================================================================
# 4. NORMALIZATION & TOKENIZATION UTILITIES
# =============================================================================

def _normalize_text(value: str) -> str:
    """Multilingual Unicode NFKC normalization and whitespace standardization."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    # Normalize unicode quotation marks and hyphens
    normalized = normalized.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    normalized = normalized.replace("–", "-").replace("—", "-")
    return " ".join(normalized.split())


def _split_clauses(text: str) -> list[str]:
    """Segment text into semantic clauses to isolate negation and preference scopes."""
    normalized = _normalize_text(text)
    raw_clauses = _CLAUSE_DELIMITER_PATTERN.split(normalized)
    return [c.strip() for c in raw_clauses if c.strip()]


def _explicit_dataset_size(value: object) -> float | None:
    """Extract and validate explicit dataset size from structured request values."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _extract_number(patterns: Sequence[re.Pattern[str]], text: str) -> float | None:
    """Extract maximum numerical constraint across matching patterns."""
    values: list[float] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            try:
                val = float(match.group(1))
                if math.isfinite(val) and val >= 0:
                    values.append(val)
            except (ValueError, IndexError):
                continue
    return max(values) if values else None


def _extract_memory_gb(text: str) -> float | None:
    """Extract memory lower bound in GB, converting MB if specified."""
    gb_val = _extract_number(_MEMORY_PATTERNS, text)
    mb_val = _extract_number(_MEMORY_MB_PATTERNS, text)
    if mb_val is not None:
        mb_in_gb = round(mb_val / 1024.0, 3)
        if gb_val is None or mb_in_gb > gb_val:
            gb_val = mb_in_gb
    return gb_val


def _extract_dataset_size_gb(text: str) -> float | None:
    """Extract dataset size from natural language text."""
    return _extract_number(_DATASET_PATTERNS, text)


# =============================================================================
# 5. NEGATION SCOPE & MODALITY RESOLUTION
# =============================================================================

def _determine_clause_modality(clause: str, entity_span: tuple[int, int]) -> Modality:
    """Determine whether an entity occurrence in a clause is REQUIRED, PREFERRED, or FORBIDDEN.
    
    Uses proximity and directional governance of negation / modality cues.
    """
    start_pos, _ = entity_span
    prefix = clause[:start_pos]

    # Check negation cue preceding entity or governing the clause
    negation_match = _NEGATION_CUES.search(clause)
    if negation_match:
        # If negation appears before the entity or encompasses the clause
        if negation_match.start() < start_pos or len(clause.split()) <= 6:
            return Modality.FORBIDDEN

    # Check preference cue
    pref_match = _PREFERENCE_CUES.search(prefix)
    if pref_match:
        return Modality.PREFERRED

    # Check requirement cue
    req_match = _REQUIREMENT_CUES.search(prefix)
    if req_match:
        return Modality.REQUIRED

    return Modality.MENTION


def _analyze_gpu_modality(text: str, clauses: list[str]) -> GPURequirement:
    """Analyze context across clauses to determine exact GPURequirement modality."""
    # Hard negation across clauses
    if _GPU_FORBIDDEN.search(text):
        return GPURequirement.FORBIDDEN

    has_required = False
    has_preferred = False

    for clause in clauses:
        if _GPU_FORBIDDEN.search(clause):
            return GPURequirement.FORBIDDEN
        if _GPU_REQUIRED.search(clause):
            has_required = True
        elif _GPU_PREFERRED.search(clause):
            has_preferred = True

    if has_required:
        return GPURequirement.REQUIRED
    if has_preferred or _GPU_MENTION.search(text):
        return GPURequirement.PREFERRED

    return GPURequirement.UNSPECIFIED


# =============================================================================
# 6. CONTRACT DIGEST HASHING
# =============================================================================

def local_parser_contract_sha256() -> str:
    """Return cryptographic SHA-256 digest of the parser contract and ontology."""
    payload: dict[str, Any] = {
        "extractor_name": LOCAL_EXTRACTOR_NAME,
        "extractor_version": LOCAL_EXTRACTOR_VERSION,
        "model_id": LOCAL_EXTRACTOR_MODEL_ID,
        "prompt_version": LOCAL_EXTRACTOR_PROMPT_VERSION,
        "import_alias_version": IMPORT_ALIAS_VERSION,
        "import_aliases": IMPORT_ALIASES,
        "python_stdlib_policy_version": PYTHON_STDLIB_POLICY_VERSION,
        "python_stdlib_modules": sorted(PYTHON_STDLIB_MODULES),
        "ontology_version": ONTOLOGY_VERSION,
        "ontology": {
            name: {
                "category": ent.category,
                "kind": ent.entity_kind,
                "surface_forms": sorted(ent.surface_forms),
            }
            for name, ent in sorted(DOMAIN_ONTOLOGY.items())
        },
        "patterns": {
            "import": _IMPORT_PATTERN.pattern,
            "clause_delimiter": _CLAUSE_DELIMITER_PATTERN.pattern,
            "negation": _NEGATION_CUES.pattern,
            "preference": _PREFERENCE_CUES.pattern,
            "requirement": _REQUIREMENT_CUES.pattern,
            "cpu": [p.pattern for p in _CPU_PATTERNS],
            "memory": [p.pattern for p in _MEMORY_PATTERNS],
            "dataset": [p.pattern for p in _DATASET_PATTERNS],
            "gpu_forbidden": _GPU_FORBIDDEN.pattern,
            "gpu_required": _GPU_REQUIRED.pattern,
            "gpu_preferred": _GPU_PREFERRED.pattern,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


LOCAL_EXTRACTOR_PROMPT_SHA256 = local_parser_contract_sha256()


# =============================================================================
# 7. LOCAL STRUCTURED INTENT EXTRACTOR CLASS
# =============================================================================

class LocalStructuredIntentExtractor:
    """Lightweight NLP information extraction pipeline with zero network dependency."""

    network_bound = False
    extractor_name = LOCAL_EXTRACTOR_NAME
    extractor_version = LOCAL_EXTRACTOR_VERSION
    model_id = LOCAL_EXTRACTOR_MODEL_ID
    prompt_version = LOCAL_EXTRACTOR_PROMPT_VERSION
    prompt_sha256 = LOCAL_EXTRACTOR_PROMPT_SHA256

    def extract(self, request: RecommendationRequest) -> StructuredIntent:
        if not isinstance(request, RecommendationRequest):
            raise TypeError("request must be a RecommendationRequest")

        combined_text = f"{request.intent}\n{request.code_context}"
        normalized_intent = _normalize_text(request.intent)
        clauses = _split_clauses(request.intent)

        # -----------------------------------------------------------------
        # 1. Python Code Imports (Explicit, Auditable Ground Truth)
        # -----------------------------------------------------------------
        imported_modules: set[str] = set()
        for match in _IMPORT_PATTERN.finditer(request.code_context):
            raw_module = match.group(1).split(".", 1)[0].casefold()
            if raw_module not in PYTHON_STDLIB_MODULES:
                canonical = IMPORT_ALIASES.get(raw_module, raw_module)
                imported_modules.add(canonical)

        # -----------------------------------------------------------------
        # 2. Ontology Matching & Clause-Level Negation / Modality Analysis
        # -----------------------------------------------------------------
        required_libraries: set[str] = set(imported_modules)
        required_frameworks: set[str] = set()
        required_features: set[str] = set()

        preferred_libraries: set[str] = set()
        preferred_frameworks: set[str] = set()
        preferred_features: set[str] = set()

        forbidden_features: set[str] = set()

        # Classify imported modules into frameworks / libraries
        for mod in imported_modules:
            if mod in DOMAIN_ONTOLOGY:
                ent = DOMAIN_ONTOLOGY[mod]
                if ent.entity_kind == "framework":
                    required_frameworks.add(ent.canonical_name)

        # Clause-level entity extraction and modality scope analysis
        for clause in clauses:
            for matcher_regex, entity in _GAZETTEER_MATCHERS:
                match = matcher_regex.search(clause)
                if not match:
                    continue

                modality = _determine_clause_modality(clause, (match.start(), match.end()))

                if modality is Modality.FORBIDDEN:
                    forbidden_features.add(entity.canonical_name)
                    # For forbidden entities, ensure they are strictly excluded from required/preferred
                    required_libraries.discard(entity.canonical_name)
                    required_frameworks.discard(entity.canonical_name)
                    required_features.discard(entity.canonical_name)
                    preferred_libraries.discard(entity.canonical_name)
                    preferred_frameworks.discard(entity.canonical_name)
                    preferred_features.discard(entity.canonical_name)

                elif modality is Modality.PREFERRED:
                    if entity.canonical_name not in forbidden_features:
                        if entity.entity_kind == "framework":
                            preferred_frameworks.add(entity.canonical_name)
                            preferred_libraries.add(entity.canonical_name)
                        elif entity.entity_kind == "library":
                            preferred_libraries.add(entity.canonical_name)
                        elif entity.entity_kind == "feature":
                            preferred_features.add(entity.canonical_name)

                elif modality in (Modality.REQUIRED, Modality.MENTION):
                    if entity.canonical_name not in forbidden_features:
                        if entity.entity_kind == "framework":
                            required_frameworks.add(entity.canonical_name)
                            required_libraries.add(entity.canonical_name)
                        elif entity.entity_kind == "library":
                            required_libraries.add(entity.canonical_name)
                        elif entity.entity_kind == "feature":
                            required_features.add(entity.canonical_name)

        # Ensure CUDA feature presence if CUDA is mentioned or required
        if "cuda" in required_features or "cuda" in required_libraries:
            required_features.add("cuda")

        # -----------------------------------------------------------------
        # 3. GPU Modality & Contextual Exclusivity Analysis
        # -----------------------------------------------------------------
        gpu_req = _analyze_gpu_modality(combined_text, clauses)
        if gpu_req is GPURequirement.FORBIDDEN:
            forbidden_features.add("gpu")
        elif gpu_req is GPURequirement.REQUIRED:
            required_features.discard("gpu")
            forbidden_features.discard("gpu")

        # -----------------------------------------------------------------
        # 4. Quantitative Resource Extraction (Slot Filling)
        # -----------------------------------------------------------------
        min_cpu = _extract_number(_CPU_PATTERNS, combined_text)
        min_mem = _extract_memory_gb(combined_text)

        explicit_dataset = _explicit_dataset_size(request.dataset_size_gb)
        if explicit_dataset is not None:
            dataset_size = explicit_dataset
        else:
            dataset_size = _extract_dataset_size_gb(combined_text)

        # -----------------------------------------------------------------
        # 5. Task Classification with Confidence Weighting
        # -----------------------------------------------------------------
        detected_tasks: set[TaskType] = set()
        matched_scores: list[float] = []

        for task_type, rules in _TASK_CLASSIFICATION_RULES.items():
            task_score = 0.0
            for pattern, weight in rules:
                if pattern.search(normalized_intent):
                    task_score = max(task_score, weight)
            if task_score >= 0.7:
                detected_tasks.add(task_type)
                matched_scores.append(task_score)

        # Associate tasks from matched domain ontology entities
        for ent_name in required_libraries | required_frameworks:
            if ent_name in DOMAIN_ONTOLOGY:
                for assoc_task in DOMAIN_ONTOLOGY[ent_name].task_associations:
                    detected_tasks.add(assoc_task)

        # EDA trigger semantics: map to both DATA_ANALYSIS and VISUALIZATION
        if re.search(r"\b(?:eda|exploratory)\b", normalized_intent, re.IGNORECASE):
            detected_tasks.add(TaskType.DATA_ANALYSIS)
            detected_tasks.add(TaskType.VISUALIZATION)

        # -----------------------------------------------------------------
        # 6. Overall Extraction Confidence Scoring
        # -----------------------------------------------------------------
        has_entities = bool(required_libraries or forbidden_features or preferred_libraries)
        has_resources = (
            gpu_req is not GPURequirement.UNSPECIFIED
            or min_cpu is not None
            or min_mem is not None
            or dataset_size is not None
        )
        has_tasks = bool(detected_tasks)

        if has_entities or has_resources or has_tasks:
            confidence = 1.0
        elif normalized_intent:
            confidence = 0.8
        else:
            confidence = 0.0

        # Sort task types canonically by enum value
        sorted_tasks = tuple(sorted(detected_tasks, key=lambda t: t.value))

        return StructuredIntent(
            task_types=sorted_tasks,
            required_features=tuple(sorted(required_features - forbidden_features)),
            preferred_features=tuple(
                sorted(preferred_features - required_features - forbidden_features)
            ),
            forbidden_features=tuple(sorted(forbidden_features)),
            required_frameworks=tuple(sorted(required_frameworks - forbidden_features)),
            preferred_frameworks=tuple(
                sorted(preferred_frameworks - required_frameworks - forbidden_features)
            ),
            required_libraries=tuple(sorted(required_libraries - forbidden_features)),
            preferred_libraries=tuple(
                sorted(preferred_libraries - required_libraries - forbidden_features)
            ),
            resource_constraints=ResourceConstraints(
                gpu_requirement=gpu_req,
                minimum_cpu_cores=min_cpu,
                minimum_memory_gb=min_mem,
                dataset_size_gb=dataset_size,
            ),
            normalized_query=normalized_intent,
            extraction_confidence=confidence,
            extraction_provenance=ExtractionProvenance(
                extractor_name=self.extractor_name,
                extractor_version=self.extractor_version,
                prompt_version=self.prompt_version,
                prompt_sha256=self.prompt_sha256,
                model_id=self.model_id,
                mode=ExtractionMode.PRIMARY,
                degraded_reason=None,
            ),
        )


__all__ = [
    "DOMAIN_ONTOLOGY",
    "IMPORT_ALIASES",
    "IMPORT_ALIAS_VERSION",
    "LOCAL_EXTRACTOR_MODEL_ID",
    "LOCAL_EXTRACTOR_NAME",
    "LOCAL_EXTRACTOR_PROMPT_SHA256",
    "LOCAL_EXTRACTOR_PROMPT_VERSION",
    "LOCAL_EXTRACTOR_VERSION",
    "ONTOLOGY_VERSION",
    "OntologyEntity",
    "PYTHON_STDLIB_MODULES",
    "PYTHON_STDLIB_POLICY_VERSION",
    "LocalStructuredIntentExtractor",
    "local_parser_contract_sha256",
]
