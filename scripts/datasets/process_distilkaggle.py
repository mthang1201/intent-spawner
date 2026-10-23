#!/usr/bin/env python3
"""DistilKaggle Large-Scale Notebook Processor & Stratified Curated Sampler.

Dataset reference: DistilKaggle (Zenodo DOI: 10.5281/zenodo.10317389, ~542k notebooks).
Paper: "DistilKaggle: A Distilled Dataset of Kaggle Jupyter Notebooks; Plus 30+ Code and Markdown Metrics" (MSR '24).

Features:
1. Universal Multi-Format Ingestion:
   - Compressed archives (.tar.gz, .tgz, .tar, .zip) without mandatory full disk extraction.
   - Relational metadata SQLite (.sqlite, .db).
   - High-throughput tabular files (Parquet / chunked CSV streaming).
   - Raw Jupyter notebook trees (.ipynb).
2. Deep Cell-Level Extraction:
   - Natural Language Intent extraction from opening Markdown cells (problem statement, overview, objective).
   - Accurate Python AST inspection for all imports, dynamic calls, hardware directives (GPU/CUDA), and dataset file refs.
   - Resource metadata parsing: `is_gpu_enabled`, `total_memory_usage`, input file sizes.
3. Stratified Quality Curation:
   - Filters and curates top 1,000 representative notebooks covering:
     * Tabular ML (scikit-learn, XGBoost, LightGBM, CatBoost)
     * Deep Learning - PyTorch (torch, torchvision, lightning, timm)
     * Deep Learning - TensorFlow/Keras (tensorflow, keras)
     * Exploratory Data Analysis & Visualization (pandas, seaborn, matplotlib, plotly)
     * Specialized ML/Data (NLP, Computer Vision, Polars, Scipy)
4. Standardized Output:
   - Serializes curated notebooks into `benchmarks_v5/raw_sources/distilkaggle_curated_1000.jsonl`.
"""

from __future__ import annotations

import argparse
import ast
import csv
import gzip
import io
import json
import logging
import os
import re
import sqlite3
import sys
import tarfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, Set, Tuple, Union

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("process_distilkaggle")

# Default paths
DEFAULT_OUTPUT_PATH = Path("benchmarks_v5/raw_sources/distilkaggle_curated_1000.jsonl")
DEFAULT_TARGET_COUNT = 1000
DEFAULT_SEED = 20261022

# Canonical Library Mapping & Categorization
CANONICAL_LIBRARIES: Dict[str, str] = {
    "sklearn": "scikit-learn",
    "scikit_learn": "scikit-learn",
    "scikit-learn": "scikit-learn",
    "torch": "pytorch",
    "pytorch": "pytorch",
    "torchvision": "torchvision",
    "torchaudio": "torchaudio",
    "pytorch_lightning": "pytorch-lightning",
    "lightning": "pytorch-lightning",
    "tf": "tensorflow",
    "tensorflow": "tensorflow",
    "keras": "keras",
    "pandas": "pandas",
    "numpy": "numpy",
    "np": "numpy",
    "scipy": "scipy",
    "matplotlib": "matplotlib",
    "plt": "matplotlib",
    "seaborn": "seaborn",
    "sns": "seaborn",
    "plotly": "plotly",
    "altair": "altair",
    "bokeh": "bokeh",
    "folium": "folium",
    "xgboost": "xgboost",
    "xgb": "xgboost",
    "lightgbm": "lightgbm",
    "lgb": "lightgbm",
    "catboost": "catboost",
    "statsmodels": "statsmodels",
    "polars": "polars",
    "dask": "dask",
    "transformers": "transformers",
    "datasets": "huggingface-datasets",
    "nltk": "nltk",
    "spacy": "spacy",
    "cv2": "opencv",
    "opencv": "opencv",
    "pil": "pillow",
    "pillow": "pillow",
    "albumentations": "albumentations",
    "timm": "timm",
    "fastai": "fastai",
    "optuna": "optuna",
    "wandb": "wandb",
    "gym": "gym",
    "gymnasium": "gymnasium",
}

# Domain Quota Allocation for Balanced Top-1000 Representation
DEFAULT_STRATA_QUOTAS: Dict[str, int] = {
    "tabular_ml": 250,
    "deep_learning_pytorch": 250,
    "deep_learning_tensorflow": 200,
    "eda_visualization": 200,
    "specialized_data": 100,
}

# Regex to detect data file loading in python code
DATA_FILE_PATTERNS = [
    re.compile(
        r'(?:read_csv|read_table|read_excel|read_parquet|read_feather|read_pickle|read_json|read_sql_table|read_sql_query|read_sql)\s*\(\s*["\']([^"\'\r\n]+\.(?:csv|tsv|txt|xlsx|xls|parquet|feather|pkl|pickle|json|sqlite|db|sqlite3))["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'(?:sqlite3\.connect|connect)\s*\(\s*["\']([^"\'\r\n]+\.(?:sqlite|sqlite3|db))["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'(?:h5py\.File|open|load)\s*\(\s*["\']([^"\'\r\n]+\.(?:h5|hdf5|npy|npz|csv|parquet|db|sqlite))["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'["\']([^"\'\s<>\r\n]+\.(?:csv|tsv|sqlite|sqlite3|db|parquet|xlsx|feather|h5|hdf5))["\']',
        re.IGNORECASE,
    ),
]

# Section headers indicating problem intent in Markdown
INTENT_HEADER_PATTERNS = [
    re.compile(r"^(?:#+\s*)(?:problem statement|problem description|task description|objective|goal|problem overview)", re.IGNORECASE),
    re.compile(r"^(?:#+\s*)(?:introduction|overview|about the dataset|background|context|abstract)", re.IGNORECASE),
    re.compile(r"^(?:#+\s*)(?:project description|eda & modeling|data science workflow)", re.IGNORECASE),
]


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class RawNotebookData:
    """Raw extracted components of a notebook."""
    kernel_id: str
    markdown_cells: List[str] = field(default_factory=list)
    code_cells: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    source_origin: str = "distilkaggle"


@dataclass
class CuratedNotebookRecord:
    """Standardized curated benchmark record."""
    benchmark: str
    notebook_id: str
    title: str
    intent: str
    category: str
    libraries: List[str]
    code_snippet: str
    metadata: Dict[str, Any]
    structured_intent: Dict[str, Any]
    curation_score: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================================
# Markdown Intent Extractor
# ============================================================================

def clean_markdown_text(text: str) -> str:
    """Remove HTML tags, image badges, excessive markdown symbols while preserving semantics."""
    if not text:
        return ""
    # Strip HTML tags like <img ...>, <div ...>, <br>, etc.
    cleaned = re.sub(r"<[^>]+>", " ", text)
    # Strip markdown images [![...](...)](...) or ![alt](...)
    cleaned = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", cleaned)
    # Strip excessive link wrappers keeping display text: [Text](URL) -> Text
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    # Remove markdown horizontal rules (---, ***, ===)
    cleaned = re.sub(r"^[ \t]*[-*_]{3,}[ \t]*$", "", cleaned, flags=re.MULTILINE)
    # Collapse multiple whitespaces / blank lines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_natural_language_intent(
    markdown_cells: List[str],
    max_chars: int = 2500,
    min_chars: int = 35,
) -> Tuple[str, str]:
    """Extract (title, intent) from the opening markdown cells of a notebook.

    Args:
        markdown_cells: Ordered list of markdown cell texts.
        max_chars: Upper bound for natural language intent string.
        min_chars: Minimum text length to qualify as a valid intent.

    Returns:
        Tuple of (title, extracted_intent).
    """
    if not markdown_cells:
        return "Untitled Kaggle Notebook", "Data analysis and computational machine learning pipeline."

    title = "Untitled Kaggle Notebook"
    intent_paragraphs: List[str] = []
    found_titled_section = False

    # First pass: check for explicit titles and headings
    for cell_idx, raw_cell in enumerate(markdown_cells[:5]):
        lines = [line.strip() for line in raw_cell.splitlines() if line.strip()]
        for line in lines:
            if line.startswith("# ") and title == "Untitled Kaggle Notebook":
                clean_title = re.sub(r"^#+\s*", "", line).strip()
                # Exclude trivial titles like '# Imports' or '# Libraries'
                if clean_title.lower() not in {"imports", "libraries", "setup", "table of contents", "toc"}:
                    title = clean_title
                    break

    # Second pass: scan for structured problem statement / introduction sections
    for raw_cell in markdown_cells[:6]:
        cleaned_cell = clean_markdown_text(raw_cell)
        if len(cleaned_cell) < min_chars:
            continue

        lines = cleaned_cell.splitlines()
        capturing = False
        captured_section_lines: List[str] = []

        for line in lines:
            # Check if this line is an intent header
            is_header = any(pat.match(line) for pat in INTENT_HEADER_PATTERNS)
            if is_header:
                capturing = True
                found_titled_section = True
                captured_section_lines.append(re.sub(r"^#+\s*", "", line))
                continue

            if capturing:
                # If hit another top-level heading, stop capturing this section
                if line.startswith("# ") or line.startswith("## "):
                    capturing = False
                else:
                    captured_section_lines.append(line)

        if captured_section_lines:
            section_text = "\n".join(captured_section_lines).strip()
            if len(section_text) >= min_chars:
                intent_paragraphs.append(section_text)

    # Fallback: If no explicit Problem Statement / Intro header was found,
    # accumulate clean opening markdown cells before code execution
    if not intent_paragraphs:
        accumulated: List[str] = []
        accum_chars = 0
        for raw_cell in markdown_cells[:4]:
            cleaned_cell = clean_markdown_text(raw_cell)
            # Skip tiny headers like '# 1. Import libraries'
            if len(cleaned_cell) < min_chars:
                continue
            accumulated.append(cleaned_cell)
            accum_chars += len(cleaned_cell)
            if accum_chars >= 500:
                break
        if accumulated:
            intent_paragraphs = accumulated

    final_intent = "\n\n".join(intent_paragraphs).strip()
    if not final_intent or len(final_intent) < min_chars:
        final_intent = f"Analyze and model the {title} dataset using machine learning and exploratory workflows."

    # Truncate if intent exceeds max_chars safely at sentence boundary
    if len(final_intent) > max_chars:
        truncated = final_intent[:max_chars]
        last_period = max(truncated.rfind(". "), truncated.rfind(".\n"))
        if last_period > max_chars // 2:
            final_intent = truncated[:last_period + 1]
        else:
            final_intent = truncated.rstrip() + "..."

    return title, final_intent


# ============================================================================
# Python AST Code Parser & Library Inspector
# ============================================================================

def sanitize_code_for_ast(code: str) -> str:
    """Sanitize IPython/Jupyter-specific syntax (magics, shell escapes) for valid Python AST parsing."""
    if not code:
        return ""
    sanitized_lines = []
    for line in code.splitlines():
        stripped = line.strip()
        # Handle cell magics (%%time, %%writefile) before line magics (%)
        if stripped.startswith("%%"):
            sanitized_lines.append(f"# [ipython_cell_magic_sanitized]: {line}")
        elif stripped.startswith(("%", "!", "?")) or stripped.endswith("?"):
            sanitized_lines.append(f"# [ipython_magic_sanitized]: {line}")
        else:
            sanitized_lines.append(line)
    return "\n".join(sanitized_lines)


class AstLibraryVisitor(ast.NodeVisitor):
    """AST Visitor that traverses syntax trees to collect imported modules and dynamic calls."""

    def __init__(self) -> None:
        self.raw_libraries: Set[str] = set()
        self.has_gpu_calls: bool = False
        self.data_file_candidates: List[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root_module = alias.name.split(".")[0].strip().lower()
            if root_module:
                self.raw_libraries.add(root_module)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root_module = node.module.split(".")[0].strip().lower()
            if root_module:
                self.raw_libraries.add(root_module)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Detect dynamic imports: __import__('pkg') or importlib.import_module('pkg')
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name in ("__import__", "import_module") and node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                root_module = first_arg.value.split(".")[0].strip().lower()
                if root_module:
                    self.raw_libraries.add(root_module)

        # Detect GPU / CUDA invocations in AST
        if func_name in ("cuda", "is_available", "to") and isinstance(node.func, ast.Attribute):
            self.has_gpu_calls = True

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in ("cuda", "is_available"):
            self.has_gpu_calls = True
        self.generic_visit(node)


def inspect_code_libraries_ast(
    code_cells: List[str],
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Scan code cells using Python AST to identify imports, GPU indicators, and input file references.

    Args:
        code_cells: List of code strings representing individual notebook code cells.

    Returns:
        Tuple of:
          - normalized_libraries (sorted unique list)
          - detected_input_files (sorted unique list)
          - code_features (dictionary with line count, GPU heuristics, AST status)
    """
    visitor = AstLibraryVisitor()
    ast_success_count = 0
    ast_syntax_error_count = 0
    total_code_lines = 0
    input_files_set: Set[str] = set()

    full_code_text_parts: List[str] = []

    for cell in code_cells:
        if not cell or not cell.strip():
            continue

        full_code_text_parts.append(cell)
        lines = cell.splitlines()
        total_code_lines += len(lines)

        # Extract data files via regex patterns across raw cell text
        for pat in DATA_FILE_PATTERNS:
            for match in pat.finditer(cell):
                candidate = match.group(1).strip()
                # Filter out obvious false positives like URLs or code parameters
                if candidate and not candidate.startswith(("http://", "https://")) and len(candidate) < 120:
                    input_files_set.add(os.path.basename(candidate))

        sanitized = sanitize_code_for_ast(cell)
        try:
            tree = ast.parse(sanitized)
            visitor.visit(tree)
            ast_success_count += 1
        except SyntaxError:
            ast_syntax_error_count += 1
            # Fallback regex parser for malformed/partial notebook cells
            for line in lines:
                m_import = re.match(r"^\s*import\s+([a-zA-Z0-9_\.,\s]+)", line)
                if m_import:
                    for item in m_import.group(1).split(","):
                        pkg = item.strip().split()[0].split(".")[0].lower()
                        if pkg:
                            visitor.raw_libraries.add(pkg)
                m_from = re.match(r"^\s*from\s+([a-zA-Z0-9_]+)", line)
                if m_from:
                    visitor.raw_libraries.add(m_from.group(1).lower())

    full_text = "\n".join(full_code_text_parts)
    # Secondary check for GPU calls via text regex if not caught in AST
    gpu_regex_matched = bool(
        re.search(r"(?:torch\.cuda|device\s*=\s*['\"]cuda['\"]|\.to\(['\"]cuda['\"]\)|tf\.test\.is_gpu_available|tf\.config\.list_physical_devices\(['\"]GPU['\"]\))", full_text, re.IGNORECASE)
    )

    # Normalize extracted libraries to canonical forms
    canonical_set: Set[str] = set()
    for raw in visitor.raw_libraries:
        cleaned = raw.strip().lower()
        if not cleaned:
            continue
        canon = CANONICAL_LIBRARIES.get(cleaned, cleaned)
        canonical_set.add(canon)

    code_features = {
        "has_gpu_code": visitor.has_gpu_calls or gpu_regex_matched,
        "total_code_lines": total_code_lines,
        "ast_valid_cells_count": ast_success_count,
        "syntax_error_cells_count": ast_syntax_error_count,
    }

    return sorted(list(canonical_set)), sorted(list(input_files_set)), code_features


# ============================================================================
# Domain Categorization
# ============================================================================

def categorize_notebook(libraries: List[str], full_code_snippet: str = "") -> str:
    """Categorize notebook into one of the 5 evaluation strata based on libraries and workflows.

    Strata:
      - deep_learning_pytorch
      - deep_learning_tensorflow
      - tabular_ml
      - eda_visualization
      - specialized_data
    """
    libs_set = set(libraries)

    # 1. Deep Learning - PyTorch
    pytorch_indicators = {"pytorch", "torch", "torchvision", "torchaudio", "pytorch-lightning", "lightning", "timm", "fastai"}
    if pytorch_indicators.intersection(libs_set):
        return "deep_learning_pytorch"

    # 2. Deep Learning - TensorFlow / Keras
    tf_indicators = {"tensorflow", "tf", "keras"}
    if tf_indicators.intersection(libs_set):
        return "deep_learning_tensorflow"

    # 3. Tabular Machine Learning
    tabular_indicators = {"scikit-learn", "sklearn", "xgboost", "xgb", "lightgbm", "lgb", "catboost", "statsmodels", "optuna"}
    if tabular_indicators.intersection(libs_set):
        return "tabular_ml"

    # 4. Specialized Data / NLP / Computer Vision / Scientific
    specialized_indicators = {
        "transformers", "nltk", "spacy", "huggingface-datasets",
        "opencv", "pillow", "albumentations", "polars", "dask", "gym", "gymnasium"
    }
    if specialized_indicators.intersection(libs_set):
        return "specialized_data"

    # 5. Exploratory Data Analysis & Visualization (Default for pandas, matplotlib, seaborn, etc.)
    return "eda_visualization"


# ============================================================================
# Metadata Resolver & Curation Scorer
# ============================================================================

def resolve_metadata(
    raw_metadata: Dict[str, Any],
    code_features: Dict[str, Any],
    detected_input_files: List[str],
    dataset_base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Combine declared metadata with code inspection heuristics."""
    # 1. GPU resolution
    declared_gpu = raw_metadata.get("is_gpu_enabled")
    if declared_gpu is None:
        # Check Kaggle metadata aliases (IsGpuWorker, AcceleratorTypeId: 2 is GPU)
        if "IsGpuWorker" in raw_metadata:
            declared_gpu = bool(raw_metadata["IsGpuWorker"])
        elif "AcceleratorTypeId" in raw_metadata:
            declared_gpu = int(raw_metadata["AcceleratorTypeId"]) in (2, 3)
        elif "accelerator" in raw_metadata:
            declared_gpu = str(raw_metadata["accelerator"]).lower() in ("gpu", "nvidia", "tpu")
        else:
            declared_gpu = code_features.get("has_gpu_code", False)
    else:
        declared_gpu = bool(declared_gpu)

    # 2. Memory usage resolution (convert to MB)
    total_memory_usage_mb: Optional[float] = None
    for key in ("total_memory_usage", "total_memory_usage_mb", "memory_usage", "memory_mb", "TotalBytes"):
        if key in raw_metadata and raw_metadata[key] is not None:
            try:
                val = float(raw_metadata[key])
                if key == "TotalBytes":
                    total_memory_usage_mb = round(val / (1024 * 1024), 2)
                else:
                    total_memory_usage_mb = round(val, 2)
                break
            except (ValueError, TypeError):
                pass

    # 3. Input file size resolution
    input_file_size_bytes: Optional[int] = None
    for key in ("input_file_size", "input_file_size_bytes", "TotalDatasetSizeBytes", "dataset_size_bytes"):
        if key in raw_metadata and raw_metadata[key] is not None:
            try:
                input_file_size_bytes = int(raw_metadata[key])
                break
            except (ValueError, TypeError):
                pass

    # If file size is not in metadata, inspect local files if accessible
    if input_file_size_bytes is None and dataset_base_dir and dataset_base_dir.is_dir():
        computed_size = 0
        found_local_file = False
        for fname in detected_input_files:
            candidate_path = dataset_base_dir / fname
            if candidate_path.is_file():
                computed_size += candidate_path.stat().st_size
                found_local_file = True
        if found_local_file:
            input_file_size_bytes = computed_size

    return {
        "is_gpu_enabled": declared_gpu,
        "total_memory_usage_mb": total_memory_usage_mb,
        "input_file_size_bytes": input_file_size_bytes,
        "input_files": detected_input_files,
        "performance_tier": raw_metadata.get("PT", raw_metadata.get("performance_tier", "Unknown")),
        "code_cells_count": raw_metadata.get("code_cells_count", 0),
        "markdown_cells_count": raw_metadata.get("markdown_cells_count", 0),
    }


def compute_curation_score(
    title: str,
    intent: str,
    libraries: List[str],
    category: str,
    code_cells_count: int,
    total_code_lines: int,
    metadata: Dict[str, Any],
) -> float:
    """Calculate quality score (0 - 100) to select top representative notebooks.

    Evaluation Dimensions:
      1. Natural Language Intent Quality (0 - 40 pts)
      2. Code & Library Diversity (0 - 35 pts)
      3. Code Substance & Structure (0 - 15 pts)
      4. Hardware & Resource Metadata Completeness (0 - 10 pts)
    """
    score = 0.0

    # 1. Intent Quality (0 - 40)
    intent_len = len(intent)
    if 100 <= intent_len <= 1800:
        score += 20.0
    elif intent_len > 1800:
        score += 15.0
    elif intent_len >= 50:
        score += 10.0

    # Bonus for clear problem statements and objectives
    intent_lower = intent.lower()
    for keyword in ("problem", "objective", "dataset", "predict", "classify", "explore", "model", "analysis", "pipeline"):
        if keyword in intent_lower:
            score += 2.0
    score = min(score, 35.0)

    # Title quality
    if title != "Untitled Kaggle Notebook" and len(title) >= 8:
        score += 5.0

    # 2. Library Diversity (0 - 35)
    num_libs = len(libraries)
    if num_libs >= 5:
        score += 25.0
    elif num_libs >= 3:
        score += 18.0
    elif num_libs >= 1:
        score += 10.0

    # Bonus for primary machine learning / deep learning frameworks
    primary_ml_libs = {"scikit-learn", "pytorch", "tensorflow", "xgboost", "lightgbm", "catboost", "transformers"}
    if primary_ml_libs.intersection(set(libraries)):
        score += 10.0

    # 3. Code Substance (0 - 15)
    if code_cells_count >= 5 and total_code_lines >= 30:
        score += 15.0
    elif code_cells_count >= 2 and total_code_lines >= 15:
        score += 10.0
    elif total_code_lines >= 5:
        score += 5.0

    # 4. Resource & Metadata Completeness (0 - 10)
    if metadata.get("is_gpu_enabled") is not None:
        score += 4.0
    if metadata.get("total_memory_usage_mb") is not None:
        score += 3.0
    if metadata.get("input_file_size_bytes") is not None or metadata.get("input_files"):
        score += 3.0

    return round(min(score, 100.0), 2)


# ============================================================================
# Readers & Ingestion Adapters
# ============================================================================

def parse_ipynb_dict(ipynb: Dict[str, Any], kernel_id: str) -> RawNotebookData:
    """Extract raw cells and metadata from a standard Jupyter Notebook JSON structure."""
    markdown_cells: List[str] = []
    code_cells: List[str] = []
    cells = ipynb.get("cells", [])

    for cell in cells:
        cell_type = cell.get("cell_type", "")
        source_val = cell.get("source", "")
        if isinstance(source_val, list):
            cell_text = "".join(source_val)
        else:
            cell_text = str(source_val)

        if cell_type == "markdown":
            markdown_cells.append(cell_text)
        elif cell_type == "code":
            code_cells.append(cell_text)

    nb_metadata = ipynb.get("metadata", {})
    # Check GPU flag in notebook kernel metadata
    accelerator = nb_metadata.get("accelerator", "")
    is_gpu = str(accelerator).lower() in ("gpu", "nvidia")

    meta: Dict[str, Any] = {
        "is_gpu_enabled": is_gpu,
        "code_cells_count": len(code_cells),
        "markdown_cells_count": len(markdown_cells),
        "orig_metadata": nb_metadata,
    }
    # Direct extraction of hardware and dataset resource fields if present
    for k in (
        "total_memory_usage", "total_memory_usage_mb", "memory_mb", "TotalBytes",
        "input_file_size", "input_file_size_bytes", "TotalDatasetSizeBytes",
        "PT", "performance_tier", "is_gpu_enabled", "IsGpuWorker", "AcceleratorTypeId"
    ):
        if k in nb_metadata:
            meta[k] = nb_metadata[k]

    return RawNotebookData(
        kernel_id=kernel_id,
        markdown_cells=markdown_cells,
        code_cells=code_cells,
        metadata=meta,
        source_origin="ipynb",
    )


def stream_from_archive_distilkaggle_csvs(
    archive_path: Path,
    limit: Optional[int] = None,
) -> Generator[RawNotebookData, None, None]:
    """Stream DistilKaggle CSV files directly from a compressed .tar.gz or .zip archive without extracting to disk."""
    p = Path(archive_path)
    logger.info("Opening DistilKaggle archive for in-memory streaming: %s", p)

    notebooks_dict: Dict[str, RawNotebookData] = {}

    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p, "r") as zf:
            namelist = zf.namelist()
            metrics_entry = next((n for n in namelist if "notebook_metrics" in n and n.endswith(".csv")), None)
            md_entry = next((n for n in namelist if "markdown" in n and n.endswith(".csv")), None)
            code_entry = next((n for n in namelist if "code" in n and n.endswith(".csv")), None)

            if not metrics_entry:
                logger.error("Could not find notebook_metrics.csv inside %s", p)
                return

            # 1. Read metrics
            with zf.open(metrics_entry) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
                for row in reader:
                    k_id = row.get("Kernel ID", row.get("kernel_id", row.get("KernelId", ""))).strip()
                    if not k_id:
                        continue
                    notebooks_dict[k_id] = RawNotebookData(
                        kernel_id=k_id,
                        markdown_cells=[],
                        code_cells=[],
                        metadata=dict(row),
                        source_origin="zip_distilkaggle_csv",
                    )
                    if limit and len(notebooks_dict) >= limit:
                        break

            target_ids = set(notebooks_dict.keys())
            # 2. Read markdown
            if md_entry:
                with zf.open(md_entry) as f:
                    reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
                    for row in reader:
                        k_id = row.get("Kernel ID", row.get("kernel_id", "")).strip()
                        if k_id in target_ids:
                            md_text = row.get("Markdown", row.get("markdown", ""))
                            if md_text:
                                notebooks_dict[k_id].markdown_cells.append(md_text)

            # 3. Read code
            if code_entry:
                with zf.open(code_entry) as f:
                    reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
                    for row in reader:
                        k_id = row.get("Kernel ID", row.get("kernel_id", "")).strip()
                        if k_id in target_ids:
                            code_text = row.get("Code", row.get("code", ""))
                            if code_text:
                                notebooks_dict[k_id].code_cells.append(code_text)

    elif p.name.endswith((".tar.gz", ".tgz", ".tar")):
        mode = "r:gz" if p.name.endswith((".tar.gz", ".tgz")) else "r:"
        with tarfile.open(p, mode) as tf:
            names = tf.getnames()
            metrics_entry = next((n for n in names if "notebook_metrics" in n and n.endswith(".csv")), None)
            md_entry = next((n for n in names if "markdown" in n and n.endswith(".csv")), None)
            code_entry = next((n for n in names if "code" in n and n.endswith(".csv")), None)

            if not metrics_entry:
                logger.error("Could not find notebook_metrics.csv inside %s", p)
                return

            f_m = tf.extractfile(metrics_entry)
            if f_m:
                reader = csv.DictReader(io.TextIOWrapper(f_m, encoding="utf-8", errors="replace"))
                for row in reader:
                    k_id = row.get("Kernel ID", row.get("kernel_id", row.get("KernelId", ""))).strip()
                    if not k_id:
                        continue
                    notebooks_dict[k_id] = RawNotebookData(
                        kernel_id=k_id,
                        markdown_cells=[],
                        code_cells=[],
                        metadata=dict(row),
                        source_origin="tar_distilkaggle_csv",
                    )
                    if limit and len(notebooks_dict) >= limit:
                        break

            target_ids = set(notebooks_dict.keys())
            if md_entry:
                f_md = tf.extractfile(md_entry)
                if f_md:
                    reader = csv.DictReader(io.TextIOWrapper(f_md, encoding="utf-8", errors="replace"))
                    for row in reader:
                        k_id = row.get("Kernel ID", row.get("kernel_id", "")).strip()
                        if k_id in target_ids:
                            md_text = row.get("Markdown", row.get("markdown", ""))
                            if md_text:
                                notebooks_dict[k_id].markdown_cells.append(md_text)

            if code_entry:
                f_cd = tf.extractfile(code_entry)
                if f_cd:
                    reader = csv.DictReader(io.TextIOWrapper(f_cd, encoding="utf-8", errors="replace"))
                    for row in reader:
                        k_id = row.get("Kernel ID", row.get("kernel_id", "")).strip()
                        if k_id in target_ids:
                            code_text = row.get("Code", row.get("code", ""))
                            if code_text:
                                notebooks_dict[k_id].code_cells.append(code_text)

    for record in notebooks_dict.values():
        record.metadata["code_cells_count"] = len(record.code_cells)
        record.metadata["markdown_cells_count"] = len(record.markdown_cells)
        yield record



def stream_from_ipynb_archive(
    archive_path: Path,
    limit: Optional[int] = None,
) -> Generator[RawNotebookData, None, None]:
    """Stream .ipynb notebooks directly from compressed .zip or .tar.gz archives without writing to disk."""
    p = Path(archive_path)
    count = 0

    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p, "r") as zf:
            for member in zf.infolist():
                if member.filename.endswith(".ipynb") and not member.filename.startswith("__MACOSX"):
                    try:
                        with zf.open(member) as f:
                            content = json.load(io.TextIOWrapper(f, encoding="utf-8"))
                            kernel_id = Path(member.filename).stem
                            yield parse_ipynb_dict(content, kernel_id)
                            count += 1
                            if limit and count >= limit:
                                return
                    except Exception as e:
                        logger.warning("Error parsing %s from zip: %s", member.filename, e)

    elif p.name.endswith((".tar.gz", ".tgz", ".tar")):
        mode = "r:gz" if p.name.endswith((".tar.gz", ".tgz")) else "r:"
        with tarfile.open(p, mode) as tf:
            for member in tf:
                if member.isfile() and member.name.endswith(".ipynb"):
                    try:
                        extracted_f = tf.extractfile(member)
                        if extracted_f:
                            content = json.load(io.TextIOWrapper(extracted_f, encoding="utf-8"))
                            kernel_id = Path(member.name).stem
                            yield parse_ipynb_dict(content, kernel_id)
                            count += 1
                            if limit and count >= limit:
                                return
                    except Exception as e:
                        logger.warning("Error parsing %s from tar: %s", member.name, e)


def stream_from_sqlite(
    sqlite_path: Path,
    limit: Optional[int] = None,
) -> Generator[RawNotebookData, None, None]:
    """Stream notebook records from SQLite database containing DistilKaggle or Meta Kaggle tables."""
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Discover tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0].lower(): row[0] for row in cursor.fetchall()}

    # Check for DistilKaggle schema (tables: notebook_metrics, markdown, code)
    if "notebook_metrics" in tables and "code" in tables:
        logger.info("Found DistilKaggle SQLite schema (notebook_metrics, code, markdown)")
        # Query metrics first
        query = f"SELECT * FROM {tables['notebook_metrics']}"
        if limit:
            query += f" LIMIT {limit}"
        cursor.execute(query)
        metrics_rows = cursor.fetchall()

        code_table = tables["code"]
        md_table = tables.get("markdown")

        for m_row in metrics_rows:
            row_dict = dict(m_row)
            kernel_id_key = next((k for k in ("Kernel ID", "kernel_id", "KernelId", "id") if k in row_dict), None)
            if not kernel_id_key:
                continue
            k_id = str(row_dict[kernel_id_key])

            # Fetch code cells
            c_cursor = conn.cursor()
            c_cursor.execute(f"SELECT * FROM {code_table} WHERE [{kernel_id_key}]=? ORDER BY [Cell Index] ASC", (k_id,))
            code_cells = [str(r["Code"] if "Code" in r.keys() else r["code"]) for r in c_cursor.fetchall()]

            # Fetch markdown cells if available
            markdown_cells = []
            if md_table:
                md_cursor = conn.cursor()
                md_cursor.execute(f"SELECT * FROM {md_table} WHERE [{kernel_id_key}]=? ORDER BY [Cell Index] ASC", (k_id,))
                markdown_cells = [str(r["Markdown"] if "Markdown" in r.keys() else r["markdown"]) for r in md_cursor.fetchall()]

            row_dict["code_cells_count"] = len(code_cells)
            row_dict["markdown_cells_count"] = len(markdown_cells)

            yield RawNotebookData(
                kernel_id=k_id,
                markdown_cells=markdown_cells,
                code_cells=code_cells,
                metadata=row_dict,
                source_origin="sqlite_distilkaggle",
            )

    # Check for Meta Kaggle schema (KernelVersions / Kernels)
    elif "kernelversions" in tables or "kernels" in tables:
        target_tbl = tables.get("kernelversions", tables.get("kernels"))
        logger.info("Found Meta Kaggle SQLite table: %s", target_tbl)
        query = f"SELECT * FROM {target_tbl}"
        if limit:
            query += f" LIMIT {limit}"
        cursor.execute(query)
        for row in cursor.fetchall():
            row_dict = dict(row)
            k_id = str(row_dict.get("Id", row_dict.get("ScriptId", "unknown")))
            code_content = row_dict.get("Code", row_dict.get("ScriptContent", ""))
            code_cells = [code_content] if code_content else []
            yield RawNotebookData(
                kernel_id=k_id,
                markdown_cells=[],
                code_cells=code_cells,
                metadata=row_dict,
                source_origin="sqlite_metakaggle",
            )
    else:
        logger.warning("Unrecognized SQLite tables: %s", list(tables.keys()))
    conn.close()


def stream_from_distilkaggle_csvs(
    metrics_file: Path,
    markdown_file: Optional[Path] = None,
    code_file: Optional[Path] = None,
    limit: Optional[int] = None,
    candidate_kernel_ids: Optional[Set[str]] = None,
) -> Generator[RawNotebookData, None, None]:
    """High-performance streaming join for DistilKaggle multi-CSV files (Zenodo structure).

    To handle Big Data (~542k notebooks, 12M code rows) without memory exhaustion:
    1. Reads `notebook_metrics.csv` first.
    2. Collects markdown and code cells for candidate IDs via streaming chunks.
    """
    logger.info("Streaming DistilKaggle metrics from %s", metrics_file)
    notebooks_dict: Dict[str, RawNotebookData] = {}

    def open_maybe_compressed(p: Path):
        if str(p).endswith(".gz"):
            return gzip.open(p, "rt", encoding="utf-8", errors="replace")
        return open(p, "r", encoding="utf-8", errors="replace")

    with open_maybe_compressed(metrics_file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            k_id = row.get("Kernel ID", row.get("kernel_id", row.get("KernelId", ""))).strip()
            if not k_id:
                continue
            if candidate_kernel_ids and k_id not in candidate_kernel_ids:
                continue

            notebooks_dict[k_id] = RawNotebookData(
                kernel_id=k_id,
                markdown_cells=[],
                code_cells=[],
                metadata=dict(row),
                source_origin="csv_distilkaggle",
            )
            if limit and len(notebooks_dict) >= limit:
                break

    logger.info("Loaded %d candidate notebook records from metrics", len(notebooks_dict))
    target_ids = set(notebooks_dict.keys())

    # Stream markdown cells
    if markdown_file and markdown_file.is_file():
        logger.info("Streaming markdown cells from %s", markdown_file)
        with open_maybe_compressed(markdown_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                k_id = row.get("Kernel ID", row.get("kernel_id", "")).strip()
                if k_id in target_ids:
                    md_text = row.get("Markdown", row.get("markdown", ""))
                    if md_text:
                        notebooks_dict[k_id].markdown_cells.append(md_text)

    # Stream code cells
    if code_file and code_file.is_file():
        logger.info("Streaming code cells from %s", code_file)
        with open_maybe_compressed(code_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                k_id = row.get("Kernel ID", row.get("kernel_id", "")).strip()
                if k_id in target_ids:
                    code_text = row.get("Code", row.get("code", ""))
                    if code_text:
                        notebooks_dict[k_id].code_cells.append(code_text)

    for record in notebooks_dict.values():
        record.metadata["code_cells_count"] = len(record.code_cells)
        record.metadata["markdown_cells_count"] = len(record.markdown_cells)
        yield record


def stream_from_parquet(
    parquet_path: Path,
    limit: Optional[int] = None,
) -> Generator[RawNotebookData, None, None]:
    """Stream notebook records from a Parquet file or directory containing Parquet partitions."""
    try:
        import pandas as pd
    except ImportError:
        logger.error("Pandas is required to read Parquet files")
        return

    p = Path(parquet_path)
    logger.info("Reading Parquet data from %s", p)
    if p.is_dir():
        # Directory of parquet files
        parquet_files = sorted(list(p.glob("*.parquet")))
        count = 0
        for pf in parquet_files:
            df = pd.read_parquet(pf)
            for _, row in df.iterrows():
                row_dict = row.to_dict()
                k_id = str(row_dict.get("Kernel ID", row_dict.get("kernel_id", f"pq_{count}")))
                code = row_dict.get("Code", row_dict.get("code", ""))
                md = row_dict.get("Markdown", row_dict.get("markdown", ""))
                yield RawNotebookData(
                    kernel_id=k_id,
                    markdown_cells=[str(md)] if md else [],
                    code_cells=[str(code)] if code else [],
                    metadata=row_dict,
                    source_origin="parquet",
                )
                count += 1
                if limit and count >= limit:
                    return
    else:
        df = pd.read_parquet(p)
        count = 0
        for _, row in df.iterrows():
            row_dict = row.to_dict()
            k_id = str(row_dict.get("Kernel ID", row_dict.get("kernel_id", f"pq_{count}")))
            code = row_dict.get("Code", row_dict.get("code", ""))
            md = row_dict.get("Markdown", row_dict.get("markdown", ""))
            yield RawNotebookData(
                kernel_id=k_id,
                markdown_cells=[str(md)] if md else [],
                code_cells=[str(code)] if code else [],
                metadata=row_dict,
                source_origin="parquet",
            )
            count += 1
            if limit and count >= limit:
                return


def stream_from_ipynb_directory(
    dir_path: Path,
    limit: Optional[int] = None,
) -> Generator[RawNotebookData, None, None]:
    """Stream .ipynb notebook files from a directory."""
    p = Path(dir_path)
    count = 0
    for file_path in p.rglob("*.ipynb"):
        if file_path.is_file() and not file_path.name.startswith("."):
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = json.load(f)
                    yield parse_ipynb_dict(content, file_path.stem)
                    count += 1
                    if limit and count >= limit:
                        return
            except Exception as e:
                logger.warning("Error reading %s: %s", file_path, e)


# ============================================================================
# Universal Source Detection Dispatcher
# ============================================================================

def auto_detect_and_stream(
    input_path: Optional[Path] = None,
    metrics_file: Optional[Path] = None,
    markdown_file: Optional[Path] = None,
    code_file: Optional[Path] = None,
    limit: Optional[int] = None,
) -> Generator[RawNotebookData, None, None]:
    """Automatically discover format and stream notebook records."""
    if metrics_file and Path(metrics_file).is_file():
        yield from stream_from_distilkaggle_csvs(
            metrics_file=Path(metrics_file),
            markdown_file=Path(markdown_file) if markdown_file else None,
            code_file=Path(code_file) if code_file else None,
            limit=limit,
        )
        return

    if not input_path:
        raise ValueError("Must provide either --input or --metrics-file.")

    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")

    # Case 1: Compressed Archive (.zip, .tar.gz, .tgz, .tar)
    if path.is_file() and path.name.lower().endswith((".zip", ".tar.gz", ".tgz", ".tar")):
        # Check if archive contains DistilKaggle CSVs
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path, "r") as zf:
                names = zf.namelist()
                has_metrics = any("notebook_metrics" in n for n in names)
            if has_metrics:
                yield from stream_from_archive_distilkaggle_csvs(path, limit=limit)
                return
            else:
                yield from stream_from_ipynb_archive(path, limit=limit)
                return
        elif path.name.lower().endswith((".tar.gz", ".tgz", ".tar")):
            mode = "r:gz" if path.name.lower().endswith((".tar.gz", ".tgz")) else "r:"
            with tarfile.open(path, mode) as tf:
                names = tf.getnames()
                has_ipynb = any(n.endswith(".ipynb") for n in names)
                has_metrics = any("notebook_metrics" in n for n in names)

            if has_metrics:
                yield from stream_from_archive_distilkaggle_csvs(path, limit=limit)
                return
            elif has_ipynb:
                yield from stream_from_ipynb_archive(path, limit=limit)
                return

    # Case 2: SQLite DB (.sqlite, .db)
    if path.is_file() and path.suffix.lower() in (".sqlite", ".db", ".sqlite3"):
        yield from stream_from_sqlite(path, limit=limit)
        return

    # Case 3: Parquet (.parquet)
    if path.name.endswith(".parquet") or (path.is_dir() and any(path.glob("*.parquet"))):
        yield from stream_from_parquet(path, limit=limit)
        return

    # Case 4: Directory with DistilKaggle CSVs
    if path.is_dir():
        metrics_candidates = list(path.glob("*notebook_metrics*.csv"))
        if metrics_candidates:
            metrics_p = metrics_candidates[0]
            md_candidates = list(path.glob("*markdown*.csv"))
            code_candidates = list(path.glob("*code*.csv"))
            md_p = md_candidates[0] if md_candidates else None
            code_p = code_candidates[0] if code_candidates else None
            yield from stream_from_distilkaggle_csvs(
                metrics_file=metrics_p,
                markdown_file=md_p,
                code_file=code_p,
                limit=limit,
            )
            return

        # Case 5: Directory of .ipynb files
        ipynb_candidates = list(path.rglob("*.ipynb"))
        if ipynb_candidates:
            yield from stream_from_ipynb_directory(path, limit=limit)
            return

    # Case 6: Standalone CSV file
    if path.is_file() and path.suffix.lower() == ".csv":
        yield from stream_from_distilkaggle_csvs(metrics_file=path, limit=limit)
        return

    raise ValueError(f"Could not automatically determine processing pipeline for path: {path}")


# ============================================================================
# Stratified Curated Sampler (Top 1,000)
# ============================================================================

def process_and_score_notebook(raw: RawNotebookData) -> CuratedNotebookRecord:
    """Run extraction, AST analysis, categorization, metadata resolution and quality scoring."""
    # 1. Natural Language Intent extraction
    title, intent = extract_natural_language_intent(raw.markdown_cells)

    # 2. Python AST inspection
    libraries, detected_input_files, code_features = inspect_code_libraries_ast(raw.code_cells)

    # 3. Code snippet summary (sample from first executable cells)
    code_snippet_parts = []
    accum_lines = 0
    for cell in raw.code_cells[:3]:
        cell_lines = cell.splitlines()
        code_snippet_parts.append(cell)
        accum_lines += len(cell_lines)
        if accum_lines >= 40:
            break
    code_snippet = "\n\n".join(code_snippet_parts).strip()

    # 4. Domain Categorization
    category = categorize_notebook(libraries, code_snippet)

    # 5. Metadata resolution
    metadata = resolve_metadata(
        raw_metadata=raw.metadata,
        code_features=code_features,
        detected_input_files=detected_input_files,
    )

    # 6. Quality Curation Scoring
    score = compute_curation_score(
        title=title,
        intent=intent,
        libraries=libraries,
        category=category,
        code_cells_count=len(raw.code_cells),
        total_code_lines=code_features["total_code_lines"],
        metadata=metadata,
    )
    metadata["curation_score"] = score

    # 7. Build structured intent schema for project interoperability
    gpu_req = "required" if metadata["is_gpu_enabled"] else "not_required"
    structured_intent = {
        "task_types": [category],
        "required_libraries": libraries,
        "resource_constraints": {
            "gpu_requirement": gpu_req,
            "total_memory_usage_mb": metadata["total_memory_usage_mb"],
            "input_file_size_bytes": metadata["input_file_size_bytes"],
        },
        "input_files": detected_input_files,
    }

    return CuratedNotebookRecord(
        benchmark="DistilKaggle",
        notebook_id=f"distilkaggle_{raw.kernel_id}",
        title=title,
        intent=intent,
        category=category,
        libraries=libraries,
        code_snippet=code_snippet,
        metadata=metadata,
        structured_intent=structured_intent,
        curation_score=score,
    )


def stratified_curate_top_k(
    records_iter: Iterable[RawNotebookData],
    target_count: int = DEFAULT_TARGET_COUNT,
    quotas: Optional[Dict[str, int]] = None,
    min_score: float = 30.0,
) -> List[CuratedNotebookRecord]:
    """Perform stratified selection of top K representative notebooks across balanced domains.

    Allocates quotas across Tabular ML, PyTorch, TensorFlow, EDA, and Specialized Data.
    Surplus capacity from underrepresented strata is reallocated to high-scoring candidates.
    """
    if quotas is None:
        quotas = dict(DEFAULT_STRATA_QUOTAS)

    strata_pools: Dict[str, List[CuratedNotebookRecord]] = defaultdict(list)
    total_processed = 0

    logger.info("Scoring and grouping notebooks into domain strata...")
    for raw in records_iter:
        curated = process_and_score_notebook(raw)
        total_processed += 1
        if curated.curation_score >= min_score:
            strata_pools[curated.category].append(curated)
        if total_processed % 500 == 0:
            logger.info("Processed %d notebooks...", total_processed)

    logger.info(
        "Finished processing %d notebooks. Strata pool sizes: %s",
        total_processed,
        {k: len(v) for k, v in strata_pools.items()},
    )

    # Sort each stratum by curation score descending
    for cat in strata_pools:
        strata_pools[cat].sort(key=lambda r: r.curation_score, reverse=True)

    selected: List[CuratedNotebookRecord] = []
    remaining_pools: Dict[str, List[CuratedNotebookRecord]] = {}

    # Stage 1: Fulfill primary strata quotas
    for cat, quota in quotas.items():
        candidates = strata_pools.get(cat, [])
        take_n = min(len(candidates), quota)
        selected.extend(candidates[:take_n])
        remaining_pools[cat] = candidates[take_n:]

    # Stage 2: Dynamic reallocation for unfilled quota slots
    needed = target_count - len(selected)
    if needed > 0:
        logger.info("Reallocating %d remaining slots across top remaining candidates...", needed)
        leftovers: List[CuratedNotebookRecord] = []
        for cand_list in remaining_pools.values():
            leftovers.extend(cand_list)
        leftovers.sort(key=lambda r: r.curation_score, reverse=True)
        selected.extend(leftovers[:needed])

    logger.info("Total curated notebooks selected: %d / %d requested", len(selected), target_count)
    return selected


# ============================================================================
# Output Serialization & Audit Summary
# ============================================================================

def save_curated_records_jsonl(records: List[CuratedNotebookRecord], output_path: Path) -> None:
    """Save curated records to JSONL and display statistical breakdown."""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    with open(p, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")

    logger.info("Saved %d curated records to %s", len(records), p)

    # Print Distribution Summary
    category_counter = Counter(r.category for r in records)
    gpu_counter = Counter(r.metadata.get("is_gpu_enabled", False) for r in records)
    all_libs: List[str] = []
    for r in records:
        all_libs.extend(r.libraries)
    top_libs = Counter(all_libs).most_common(12)

    logger.info("=========================================================")
    logger.info("           DISTILKAGGLE CURATION AUDIT SUMMARY          ")
    logger.info("=========================================================")
    logger.info("Total Notebooks Curated: %d", len(records))
    logger.info("Category Distribution:")
    for cat, count in category_counter.items():
        logger.info("  - %-26s: %4d (%.1f%%)", cat, count, (count / len(records)) * 100)
    logger.info("Hardware Acceleration:")
    logger.info("  - GPU Enabled: %4d (%.1f%%)", gpu_counter[True], (gpu_counter[True] / len(records)) * 100)
    logger.info("  - CPU Only:    %4d (%.1f%%)", gpu_counter[False], (gpu_counter[False] / len(records)) * 100)
    logger.info("Top Imported Libraries:")
    for lib, count in top_libs:
        logger.info("  - %-18s: %4d", lib, count)
    logger.info("=========================================================")


# ============================================================================
# Synthetic Dataset Generator for Testing & Demos
# ============================================================================

def generate_synthetic_distilkaggle_dataset(
    output_dir_or_file: Path,
    num_notebooks: int = 1200,
    format_type: str = "tar.gz",
) -> Path:
    """Generate a realistic synthetic DistilKaggle dataset for end-to-end testing and verification.

    Covers all 5 core strata:
      - EDA / Visualization
      - Tabular ML (Random Forest, XGBoost)
      - PyTorch Deep Learning
      - TensorFlow / Keras Deep Learning
      - Specialized NLP / CV
    """
    logger.info("Generating %d synthetic DistilKaggle notebooks (%s)...", num_notebooks, format_type)
    p = Path(output_dir_or_file)
    p.parent.mkdir(parents=True, exist_ok=True)

    templates = [
        # 1. Tabular ML
        {
            "category": "tabular_ml",
            "title": "Customer Churn Prediction with XGBoost and Optuna",
            "markdown": [
                "# Customer Churn Prediction\n\n## Problem Statement\nThe goal of this notebook is to predict customer churn in telecommunications using gradient boosted decision trees. We perform exploratory data analysis, handle missing values, and optimize hyperparameters.",
                "### Feature Engineering\nWe create interaction terms and normalize financial tenure features.",
            ],
            "code": [
                "import pandas as pd\nimport numpy as np\nimport matplotlib.pyplot as plt\nfrom sklearn.model_selection import train_test_split\nimport xgboost as xgb\n\ndf = pd.read_csv('telecom_churn.csv')\nprint(df.shape)",
                "X = df.drop(columns=['churn'])\ny = df['churn']\nX_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)\nclf = xgb.XGBClassifier(n_estimators=100)\nclf.fit(X_train, y_train)",
            ],
            "is_gpu": False,
            "memory_mb": 1450.0,
            "input_file_size": 25482000,
        },
        # 2. PyTorch Deep Learning
        {
            "category": "deep_learning_pytorch",
            "title": "Medical Imaging Classification with PyTorch and Vision Transformers",
            "markdown": [
                "# Pneumonia Detection from Chest X-Rays\n\n## Objective\nTrain a deep convolutional neural network and vision transformer using PyTorch to detect pneumonia from radiographic chest scans.",
                "### Model Architecture & Training\nWe utilize transfer learning with pretrained weights and AdamW optimizer with cosine learning rate scheduling.",
            ],
            "code": [
                "import torch\nimport torch.nn as nn\nimport torchvision.transforms as T\nfrom torch.utils.data import DataLoader\nimport timm\n\ndevice = torch.device('cuda' if torch.cuda.is_available() else 'cpu')\nprint(f'Training on {device}')",
                "model = timm.create_model('resnet50', pretrained=True, num_classes=2).to(device)\ncriterion = nn.CrossEntropyLoss()\noptimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)",
            ],
            "is_gpu": True,
            "memory_mb": 7820.0,
            "input_file_size": 184500000,
        },
        # 3. TensorFlow / Keras Deep Learning
        {
            "category": "deep_learning_tensorflow",
            "title": "Time Series Forecasting with Bidirectional LSTM in TensorFlow",
            "markdown": [
                "# Energy Consumption Demand Forecasting\n\n## Problem Overview\nForecast hourly grid electricity demand using bidirectional LSTMs and residual connections in TensorFlow/Keras. Accurate load forecasting is crucial for renewable energy integration.",
                "### Data Normalization\nWe apply MinMax scaling to temporal lag features.",
            ],
            "code": [
                "import tensorflow as tf\nfrom tensorflow import keras\nfrom tensorflow.keras import layers\nimport pandas as pd\nimport numpy as np\n\ndf = pd.read_parquet('energy_hourly.parquet')",
                "model = keras.Sequential([\n    layers.Bidirectional(layers.LSTM(64, return_sequences=True)),\n    layers.Dropout(0.2),\n    layers.Dense(1)\n])\nmodel.compile(optimizer='adam', loss='mse')",
            ],
            "is_gpu": True,
            "memory_mb": 4200.0,
            "input_file_size": 95000000,
        },
        # 4. EDA & Visualization
        {
            "category": "eda_visualization",
            "title": "Comprehensive EDA: Global Climate Trends and Carbon Emissions",
            "markdown": [
                "# Global Climate Impact and Temperature Trends (1950 - 2023)\n\n## Background\nThis exploratory analysis investigates historical greenhouse gas concentrations, ocean temperature anomalies, and deforestation metrics across 180 countries.",
                "### Visualizing Regional Outliers\nWe generate interactive Choropleth maps and multivariate correlation matrices.",
            ],
            "code": [
                "import pandas as pd\nimport matplotlib.pyplot as plt\nimport seaborn as sns\nimport plotly.express as px\n\ndf = pd.read_csv('climate_emissions.csv')\nplt.figure(figsize=(10, 6))\nsns.heatmap(df.corr(), annot=True, cmap='coolwarm')",
                "fig = px.scatter(df, x='gdp_per_capita', y='co2_per_capita', color='continent')\nfig.show()",
            ],
            "is_gpu": False,
            "memory_mb": 850.0,
            "input_file_size": 12400000,
        },
        # 5. Specialized Data / NLP
        {
            "category": "specialized_data",
            "title": "Customer Review Sentiment Analysis with HuggingFace Transformers and Polars",
            "markdown": [
                "# E-commerce Review Sentiment Mining\n\n## Task Description\nAnalyze 500,000 product reviews using HuggingFace Transformers for zero-shot sentiment extraction. We leverage Polars for blazing-fast columnar preprocessing.",
                "### Tokenization & Inference\nBatched tokenization using DistilBERT tokenizer.",
            ],
            "code": [
                "import polars as pl\nfrom transformers import AutoTokenizer, AutoModelForSequenceClassification\nimport nltk\n\ndf = pl.read_csv('reviews_data.csv')\ntokenizer = AutoTokenizer.from_pretrained('distilbert-base-uncased')",
                "tokens = tokenizer(df['text'].to_list()[:100], padding=True, truncation=True, return_tensors='pt')",
            ],
            "is_gpu": True,
            "memory_mb": 3100.0,
            "input_file_size": 48000000,
        },
    ]

    if format_type == "sqlite":
        conn = sqlite3.connect(str(p))
        c = conn.cursor()
        c.execute("""
            CREATE TABLE notebook_metrics (
                [Kernel ID] TEXT PRIMARY KEY,
                PT TEXT,
                MW INTEGER,
                MC INTEGER,
                Lines INTEGER,
                is_gpu_enabled INTEGER,
                total_memory_usage REAL,
                input_file_size INTEGER
            )
        """)
        c.execute("""
            CREATE TABLE markdown (
                [Kernel ID] TEXT,
                [Cell Index] INTEGER,
                Markdown TEXT
            )
        """)
        c.execute("""
            CREATE TABLE code (
                [Kernel ID] TEXT,
                [Cell Index] INTEGER,
                Code TEXT
            )
        """)

        for i in range(num_notebooks):
            tmpl = templates[i % len(templates)]
            k_id = f"syn_{i+1:05d}"
            c.execute(
                "INSERT INTO notebook_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (k_id, "Expert" if i % 2 == 0 else "Master", 250, len(tmpl["markdown"]), 60, int(tmpl["is_gpu"]), tmpl["memory_mb"], tmpl["input_file_size"])
            )
            for idx, md in enumerate(tmpl["markdown"]):
                c.execute("INSERT INTO markdown VALUES (?, ?, ?)", (k_id, idx, md))
            for idx, cd in enumerate(tmpl["code"]):
                c.execute("INSERT INTO code VALUES (?, ?, ?)", (k_id, idx, cd))

        conn.commit()
        conn.close()
        logger.info("Generated synthetic SQLite database at: %s", p)
        return p

    elif format_type in ("tar.gz", "tar"):
        with tarfile.open(p, "w:gz") as tf:
            for i in range(num_notebooks):
                tmpl = templates[i % len(templates)]
                k_id = f"syn_{i+1:05d}"
                nb_dict = {
                    "cells": [],
                    "metadata": {
                        "accelerator": "GPU" if tmpl["is_gpu"] else "None",
                        "total_memory_usage_mb": tmpl["memory_mb"],
                        "input_file_size_bytes": tmpl["input_file_size"],
                    },
                    "nbformat": 4,
                    "nbformat_minor": 5,
                }
                for md in tmpl["markdown"]:
                    nb_dict["cells"].append({"cell_type": "markdown", "source": md})
                for cd in tmpl["code"]:
                    nb_dict["cells"].append({"cell_type": "code", "source": cd})

                content_bytes = json.dumps(nb_dict, indent=2).encode("utf-8")
                tar_info = tarfile.TarInfo(name=f"notebooks/{k_id}.ipynb")
                tar_info.size = len(content_bytes)
                tar_info.mtime = int(datetime.now(timezone.utc).timestamp())
                tf.addfile(tar_info, io.BytesIO(content_bytes))

        logger.info("Generated synthetic tar.gz archive at: %s", p)
        return p

    elif format_type == "csv":
        p.mkdir(parents=True, exist_ok=True)
        metrics_p = p / "notebook_metrics.csv"
        md_p = p / "markdown.csv"
        code_p = p / "code.csv"

        with open(metrics_p, "w", encoding="utf-8", newline="") as f_m, \
             open(md_p, "w", encoding="utf-8", newline="") as f_md, \
             open(code_p, "w", encoding="utf-8", newline="") as f_cd:

            w_m = csv.writer(f_m)
            w_md = csv.writer(f_md)
            w_cd = csv.writer(f_cd)

            w_m.writerow(["Kernel ID", "PT", "MW", "MC", "Lines", "is_gpu_enabled", "total_memory_usage", "input_file_size"])
            w_md.writerow(["Kernel ID", "Cell Index", "Markdown"])
            w_cd.writerow(["Kernel ID", "Cell Index", "Code"])

            for i in range(num_notebooks):
                tmpl = templates[i % len(templates)]
                k_id = f"syn_{i+1:05d}"
                w_m.writerow([k_id, "Expert", 220, len(tmpl["markdown"]), 55, int(tmpl["is_gpu"]), tmpl["memory_mb"], tmpl["input_file_size"]])
                for idx, md in enumerate(tmpl["markdown"]):
                    w_md.writerow([k_id, idx, md])
                for idx, cd in enumerate(tmpl["code"]):
                    w_cd.writerow([k_id, idx, cd])

        logger.info("Generated synthetic CSV directory at: %s", p)
        return p

    raise ValueError(f"Unsupported synthetic format: {format_type}")


# ============================================================================
# Main CLI Orchestration
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Process DistilKaggle notebooks, extract AST imports & NL intent, and curate top 1,000 diverse workloads."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Path to input archive (.tar.gz, .zip), SQLite file (.sqlite, .db), Parquet, CSV, or directory.",
    )
    parser.add_argument(
        "--metrics-file",
        type=Path,
        default=None,
        help="Path to standalone notebook_metrics.csv (if specifying individual DistilKaggle files).",
    )
    parser.add_argument(
        "--markdown-file",
        type=Path,
        default=None,
        help="Path to standalone markdown.csv.",
    )
    parser.add_argument(
        "--code-file",
        type=Path,
        default=None,
        help="Path to standalone code.csv.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Destination JSONL path (default: {DEFAULT_OUTPUT_PATH}).",
    )
    parser.add_argument(
        "--target-count",
        type=int,
        default=DEFAULT_TARGET_COUNT,
        help=f"Number of curated notebooks to sample (default: {DEFAULT_TARGET_COUNT}).",
    )
    parser.add_argument(
        "--limit-scan",
        type=int,
        default=None,
        help="Optional ceiling on number of candidate notebooks scanned.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run end-to-end demo using auto-generated synthetic DistilKaggle dataset.",
    )
    parser.add_argument(
        "--generate-synthetic",
        type=Path,
        default=None,
        help="Path to generate a synthetic DistilKaggle benchmark dataset file (.tar.gz or .sqlite).",
    )
    parser.add_argument(
        "--synthetic-format",
        choices=["tar.gz", "sqlite", "csv"],
        default="tar.gz",
        help="Format for generated synthetic dataset.",
    )
    parser.add_argument(
        "--synthetic-count",
        type=int,
        default=1200,
        help="Number of notebooks in synthetic dataset.",
    )

    args = parser.parse_args()

    # Case A: Standalone Synthetic Dataset Generation
    if args.generate_synthetic:
        generate_synthetic_distilkaggle_dataset(
            output_dir_or_file=args.generate_synthetic,
            num_notebooks=args.synthetic_count,
            format_type=args.synthetic_format,
        )
        return 0

    # Case B: Demo Mode
    if args.demo:
        demo_archive = Path("tmp/distilkaggle_synthetic_demo.tar.gz")
        demo_archive.parent.mkdir(parents=True, exist_ok=True)
        generate_synthetic_distilkaggle_dataset(
            output_dir_or_file=demo_archive,
            num_notebooks=args.synthetic_count,
            format_type="tar.gz",
        )
        args.input = demo_archive

    if not args.input and not args.metrics_file:
        logger.error("No input specified! Provide --input, --metrics-file, or use --demo.")
        parser.print_help()
        return 1

    try:
        # Stream raw notebook records
        records_stream = auto_detect_and_stream(
            input_path=args.input,
            metrics_file=args.metrics_file,
            markdown_file=args.markdown_file,
            code_file=args.code_file,
            limit=args.limit_scan,
        )

        # Stratified curation of top 1,000 representative notebooks
        curated_records = stratified_curate_top_k(
            records_iter=records_stream,
            target_count=args.target_count,
        )

        if not curated_records:
            logger.warning("No notebooks met curation criteria.")
            return 1

        # Save to target JSONL
        save_curated_records_jsonl(curated_records, args.output)
        return 0

    except Exception as e:
        logger.exception("Processing pipeline failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
