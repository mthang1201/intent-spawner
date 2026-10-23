#!/usr/bin/env python3
"""DS-1000 to Image Catalog and Structured Intent Mapper.

Maps extracted DS-1000 benchmark problems to verified image entries
in recommender/image-catalog.yaml, performs AST analysis to detect
auxiliary libraries (such as Matplotlib and Seaborn), and constructs
valid StructuredIntent contracts.
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
from pathlib import Path
import re
import sys
import textwrap
from typing import Any, Dict, List, Optional, Set, Tuple

from recommender.models import (
    ExtractionMode,
    ExtractionProvenance,
    StructuredIntent,
    TaskType,
)
from recommender.rule_based import DEFAULT_CATALOG_PATH, load_image_catalog

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ds1000_image_mapper")

# Canonical library mapping to Catalog image ID
# - 'Pandas', 'Numpy', 'Matplotlib', 'Scikit-learn', 'Scipy' -> 'scipy-data-science'
# - 'Pytorch' -> 'pytorch-deep-learning'
# - 'Tensorflow' -> 'tensorflow-deep-learning'
LIBRARY_TO_IMAGE: Dict[str, str] = {
    "pandas": "scipy-data-science",
    "numpy": "scipy-data-science",
    "matplotlib": "scipy-data-science",
    "scikit-learn": "scipy-data-science",
    "scipy": "scipy-data-science",
    "pytorch": "pytorch-deep-learning",
    "tensorflow": "tensorflow-deep-learning",
}

# Base required features and task types by primary library
PRIMARY_LIBRARY_SPECS: Dict[str, Dict[str, Any]] = {
    "pandas": {
        "required_features": ("pandas",),
        "task_types": (TaskType.DATA_ANALYSIS, TaskType.DATA_PROCESSING),
    },
    "numpy": {
        "required_features": ("numpy",),
        "task_types": (TaskType.DATA_PROCESSING, TaskType.SCIENTIFIC_COMPUTING),
    },
    "matplotlib": {
        "required_features": ("matplotlib", "visualization"),
        "task_types": (TaskType.VISUALIZATION,),
    },
    "scikit-learn": {
        "required_features": ("scikit-learn",),
        "task_types": (TaskType.MACHINE_LEARNING,),
    },
    "scipy": {
        "required_features": ("scipy",),
        "task_types": (TaskType.SCIENTIFIC_COMPUTING,),
    },
    "pytorch": {
        "required_features": ("pytorch",),
        "task_types": (TaskType.DEEP_LEARNING, TaskType.MACHINE_LEARNING),
    },
    "tensorflow": {
        "required_features": ("tensorflow",),
        "task_types": (TaskType.DEEP_LEARNING, TaskType.MACHINE_LEARNING),
    },
}

MODULE_NAME_MAP: Dict[str, str] = {
    "matplotlib": "matplotlib",
    "pylab": "matplotlib",
    "seaborn": "seaborn",
    "pandas": "pandas",
    "numpy": "numpy",
    "scipy": "scipy",
    "sklearn": "scikit-learn",
    "torch": "pytorch",
    "torchvision": "pytorch",
    "tensorflow": "tensorflow",
    "keras": "tensorflow",
    "xgboost": "xgboost",
    "lightgbm": "lightgbm",
    "statsmodels": "statsmodels",
}

ALIAS_MAP: Dict[str, str] = {
    "plt": "matplotlib",
    "sns": "seaborn",
    "pd": "pandas",
    "np": "numpy",
    "torch": "pytorch",
    "tf": "tensorflow",
    "scipy": "scipy",
    "sklearn": "scikit-learn",
}


def normalize_lib_key(lib_name: str) -> str:
    """Normalize library string to lowercased canonical key."""
    cleaned = lib_name.strip().lower()
    if cleaned in ("sklearn", "scikit_learn"):
        return "scikit-learn"
    if cleaned in ("torch",):
        return "pytorch"
    if cleaned in ("tf",):
        return "tensorflow"
    return cleaned


def validate_catalog(catalog_path: str | Path | None = None) -> Dict[str, Any]:
    """Load and validate the image catalog using recommender.rule_based.load_image_catalog."""
    path = Path(catalog_path) if catalog_path else DEFAULT_CATALOG_PATH
    if not path.is_file():
        raise FileNotFoundError(f"Catalog file not found at: {path}")

    catalog = load_image_catalog(path)
    required_images = set(LIBRARY_TO_IMAGE.values())
    available_images = set(catalog.get("images", {}).keys())

    missing = required_images - available_images
    if missing:
        raise ValueError(
            f"Image catalog at {path} is missing required images: {sorted(missing)}. "
            f"Available: {sorted(available_images)}"
        )
    logger.info("Successfully validated image catalog (%s) with required images: %s", path, sorted(required_images))
    return catalog


def extract_auxiliary_libraries_from_ast(code: str) -> List[str]:
    """Analyze AST of Python code to detect imported or referenced libraries."""
    if not code or not code.strip():
        return []

    # Attempt parsing directly, dedented, or wrapped in a dummy function
    tree: Optional[ast.AST] = None
    candidates = [
        code,
        textwrap.dedent(code),
        f"def _ast_wrapper():\n{textwrap.indent(code, '    ')}",
    ]
    for candidate in candidates:
        try:
            tree = ast.parse(candidate)
            break
        except Exception:
            continue

    detected: Set[str] = set()

    if tree is not None:
        for node in ast.walk(tree):
            # 1. Direct imports: import matplotlib.pyplot as plt
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0].lower()
                    if root in MODULE_NAME_MAP:
                        detected.add(MODULE_NAME_MAP[root])
            # 2. From imports: from sklearn.ensemble import ...
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0].lower()
                if root in MODULE_NAME_MAP:
                    detected.add(MODULE_NAME_MAP[root])
            # 3. Idiomatic alias or module references: plt.plot(), sns.heatmap()
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                attr_base = node.value.id
                if attr_base in ALIAS_MAP:
                    detected.add(ALIAS_MAP[attr_base])
                elif attr_base.lower() in MODULE_NAME_MAP:
                    detected.add(MODULE_NAME_MAP[attr_base.lower()])

    # Fallback / auxiliary regex search if AST missed something or syntax was unusual
    lower_code = code.lower()
    for alias, canonical in ALIAS_MAP.items():
        if re.search(r"\b" + re.escape(alias) + r"\.", lower_code):
            detected.add(canonical)
    for mod, canonical in MODULE_NAME_MAP.items():
        if re.search(r"\bimport\s+" + re.escape(mod) + r"\b", lower_code) or re.search(
            r"\bfrom\s+" + re.escape(mod) + r"\b", lower_code
        ):
            detected.add(canonical)

    return sorted(detected)


def build_structured_intent(
    primary_library_key: str,
    auxiliary_libraries: List[str],
    prompt: str,
) -> Tuple[List[str], List[str], List[str], StructuredIntent]:
    """Construct a valid StructuredIntent instance conforming to P2 contract.

    Returns:
        (task_types_strings, required_features, preferred_features, StructuredIntent)
    """
    spec = PRIMARY_LIBRARY_SPECS.get(
        primary_library_key,
        {
            "required_features": (primary_library_key,),
            "task_types": (TaskType.DATA_ANALYSIS,),
        },
    )

    required_features_set = set(spec["required_features"])
    task_types_set = set(spec["task_types"])
    preferred_features_set: Set[str] = set()

    for aux_lib in auxiliary_libraries:
        if aux_lib in required_features_set:
            continue
        preferred_features_set.add(aux_lib)

        if aux_lib in ("matplotlib", "seaborn"):
            preferred_features_set.add("visualization")
            task_types_set.add(TaskType.VISUALIZATION)
        elif aux_lib in ("scikit-learn", "xgboost", "lightgbm"):
            task_types_set.add(TaskType.MACHINE_LEARNING)
        elif aux_lib in ("pytorch", "tensorflow"):
            task_types_set.add(TaskType.DEEP_LEARNING)
        elif aux_lib in ("scipy",):
            task_types_set.add(TaskType.SCIENTIFIC_COMPUTING)

    # Preferred features must not overlap with required features
    final_preferred = preferred_features_set - required_features_set

    sorted_required = sorted(required_features_set)
    sorted_preferred = sorted(final_preferred)
    sorted_task_types = tuple(sorted(task_types_set, key=lambda t: t.value))

    # Clean query text for normalized_query field
    query = prompt.replace("\n", " ").strip()
    if len(query) > 500:
        query = query[:500]

    provenance = ExtractionProvenance(
        extractor_name="ds1000-image-mapper",
        extractor_version="v1.0",
        mode=ExtractionMode.PRIMARY,
        degraded_reason=None,
    )

    structured_intent = StructuredIntent(
        task_types=sorted_task_types,
        required_features=tuple(sorted_required),
        preferred_features=tuple(sorted_preferred),
        normalized_query=query,
        extraction_confidence=1.0,
        extraction_provenance=provenance,
    )

    task_type_strings = [t.value for t in sorted_task_types]
    return task_type_strings, sorted_required, sorted_preferred, structured_intent


def map_problem(problem_data: Dict[str, Any], catalog: Dict[str, Any]) -> Dict[str, Any]:
    """Map a single DS-1000 problem JSON object to a verified image catalog entry and StructuredIntent."""
    problem_id = str(problem_data.get("problem_id", "")).strip()
    raw_lib = str(problem_data.get("library", "")).strip()
    prompt = str(problem_data.get("prompt", "")).strip()
    reference_code = str(problem_data.get("reference_code", ""))

    test_code = str(problem_data.get("test_code", ""))

    if not problem_id:
        raise ValueError("Problem is missing 'problem_id'")
    if not raw_lib:
        raise ValueError(f"Problem {problem_id} has an empty 'library' label")

    lib_key = normalize_lib_key(raw_lib)
    if lib_key not in LIBRARY_TO_IMAGE:
        raise ValueError(
            f"Problem {problem_id} has unmapped library '{raw_lib}'. "
            f"Expected one of: {sorted(LIBRARY_TO_IMAGE.keys())}"
        )

    image_id = LIBRARY_TO_IMAGE[lib_key]

    # Analyze full code context (reference_code + prompt + test_code) with AST to find auxiliary libraries
    full_code_context = f"{reference_code}\n{prompt}\n{test_code}"
    detected_aux_libs = extract_auxiliary_libraries_from_ast(full_code_context)

    # Cross-framework upgrade: if problem code requires tensorflow or pytorch, upgrade image
    # from scipy-data-science so container has the required framework installed
    if ("tensorflow" in detected_aux_libs or "keras" in detected_aux_libs) and image_id == "scipy-data-science":
        image_id = "tensorflow-deep-learning"
    elif "pytorch" in detected_aux_libs and image_id == "scipy-data-science":
        image_id = "pytorch-deep-learning"

    images_catalog = catalog.get("images", {})
    if image_id not in images_catalog:
        raise ValueError(f"Target image '{image_id}' not found in image catalog")

    image_info = images_catalog[image_id]
    image_reference = image_info.get("reference", "")
    if not image_reference:
        raise ValueError(f"Image entry '{image_id}' is missing container reference in catalog")

    # Build StructuredIntent
    task_types_str, req_feats, pref_feats, structured_intent = build_structured_intent(
        primary_library_key=lib_key,
        auxiliary_libraries=detected_aux_libs,
        prompt=prompt,
    )

    return {
        "problem_id": problem_id,
        "library": raw_lib,
        "image": image_id,
        "image_id": image_id,
        "image_display_name": image_info.get("display_name", ""),
        "image_reference": image_reference,
        "prompt": prompt,
        "reference_code": reference_code,
        "task_types": task_types_str,
        "required_features": req_feats,
        "preferred_features": pref_feats,
        "auxiliary_libraries": detected_aux_libs,
        "structured_intent": structured_intent.to_dict(),
    }


def natural_sort_key(s: str) -> List[Any]:
    """Key for natural sorting (e.g. Pandas_2 before Pandas_10)."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)]


def process_ds1000_directory(
    input_dir: str | Path,
    output_file: str | Path,
    catalog_path: str | Path | None = None,
) -> List[Dict[str, Any]]:
    """Process all JSON files in input_dir and output ds1000_intent_and_images.jsonl."""
    in_path = Path(input_dir)
    out_path = Path(output_file)

    if not in_path.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {in_path}")

    catalog = validate_catalog(catalog_path)

    # Gather all problem JSON files (ignoring manifest.json)
    problem_files = [
        f for f in in_path.glob("*.json") if f.name != "manifest.json" and not f.name.startswith(".")
    ]
    problem_files.sort(key=lambda p: natural_sort_key(p.name))

    if not problem_files:
        raise FileNotFoundError(f"No problem JSON files found in {in_path}")

    logger.info("Found %d problem JSON files to map in %s", len(problem_files), in_path)

    mapped_records: List[Dict[str, Any]] = []
    image_counter: Dict[str, int] = {}
    aux_counter: Dict[str, int] = {}

    for file_path in problem_files:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        mapped = map_problem(data, catalog)

        # Verification: check 100% valid image and no empty labels
        if not mapped["image"]:
            raise ValueError(f"Problem {mapped['problem_id']} mapped to empty image")
        if not mapped["library"]:
            raise ValueError(f"Problem {mapped['problem_id']} has empty library")
        if not mapped["problem_id"]:
            raise ValueError("Found entry with empty problem_id")

        mapped_records.append(mapped)
        img = mapped["image"]
        image_counter[img] = image_counter.get(img, 0) + 1

        for aux in mapped["auxiliary_libraries"]:
            aux_counter[aux] = aux_counter.get(aux, 0) + 1

    # Verify 100% coverage
    assert len(mapped_records) == len(
        problem_files
    ), f"Count mismatch: expected {len(problem_files)}, got {len(mapped_records)}"

    # Ensure parent output directory exists
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as out_f:
        for record in mapped_records:
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info(
        "Successfully mapped %d/%d problems (100%%) to %s",
        len(mapped_records),
        len(problem_files),
        out_path,
    )
    logger.info("Catalog Image distribution: %s", image_counter)
    logger.info("Top detected auxiliary libraries: %s", sorted(aux_counter.items(), key=lambda x: -x[1])[:10])

    return mapped_records


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser for image mapper."""
    parser = argparse.ArgumentParser(
        description="Map DS-1000 benchmark problems to JupyterHub Image Catalog and Structured Intent."
    )
    parser.add_argument(
        "--input-dir",
        type=str,
        default="benchmarks_v5/raw_sources/ds1000",
        help="Directory containing DS-1000 problem JSON files (default: benchmarks_v5/raw_sources/ds1000)",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default="benchmarks_v5/raw_sources/ds1000_intent_and_images.jsonl",
        help="Target output JSONL file (default: benchmarks_v5/raw_sources/ds1000_intent_and_images.jsonl)",
    )
    parser.add_argument(
        "--catalog-path",
        type=str,
        default=str(DEFAULT_CATALOG_PATH),
        help="Path to recommender/image-catalog.yaml",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI execution entry point."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        process_ds1000_directory(
            input_dir=args.input_dir,
            output_file=args.output_file,
            catalog_path=args.catalog_path,
        )
        return 0
    except Exception as e:
        logger.error("Mapping failed: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
