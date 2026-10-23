#!/usr/bin/env python3
"""DS-1000 dataset extractor.

Downloads and extracts the DS-1000 benchmark dataset from Hugging Face
or the official GitHub repository (HKUNLP/DS-1000), saving individual
problem JSON files matching the project benchmark schema.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import logging
import os
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("extract_ds1000")

# Primary and fallback URLs for official DS-1000 raw archive
GITHUB_RAW_URLS = [
    "https://raw.githubusercontent.com/HKUNLP/DS-1000/main/data/ds1000.jsonl.gz",
    "https://raw.githubusercontent.com/xlang-ai/DS-1000/main/data/ds1000.jsonl.gz",
]

HF_DATASET_IDENTIFIERS = [
    "xlangai/DS-1000",
    "ds1000",
]

# Canonical library names as required by the specification:
# (Pandas, Numpy, Matplotlib, Scikit-learn, Scipy, Pytorch, Tensorflow)
CANONICAL_LIBRARIES: Dict[str, str] = {
    "pandas": "Pandas",
    "numpy": "Numpy",
    "matplotlib": "Matplotlib",
    "sklearn": "Scikit-learn",
    "scikit-learn": "Scikit-learn",
    "scikit_learn": "Scikit-learn",
    "scipy": "Scipy",
    "pytorch": "Pytorch",
    "torch": "Pytorch",
    "tensorflow": "Tensorflow",
    "tf": "Tensorflow",
}

# Problem ID prefix mappings (e.g. Pandas_12, PyTorch_5)
PROBLEM_ID_PREFIXES: Dict[str, str] = {
    "Pandas": "Pandas",
    "Numpy": "Numpy",
    "Matplotlib": "Matplotlib",
    "Scikit-learn": "Scikit-learn",
    "Scipy": "Scipy",
    "Pytorch": "PyTorch",
    "PyTorch": "PyTorch",
    "Tensorflow": "Tensorflow",
}


def normalize_library_name(raw_name: Optional[str]) -> str:
    """Normalize raw library name to the 7 canonical target libraries."""
    if not raw_name:
        return "Unknown"
    cleaned = raw_name.strip().lower()
    return CANONICAL_LIBRARIES.get(cleaned, raw_name.strip().title())


def generate_problem_id(library: str, library_problem_id: Optional[int], overall_id: Optional[int] = None) -> str:
    """Generate standardized problem_id (e.g., Pandas_12, PyTorch_5)."""
    prefix = PROBLEM_ID_PREFIXES.get(library, library)
    if library_problem_id is not None:
        return f"{prefix}_{library_problem_id}"
    if overall_id is not None:
        return f"{prefix}_{overall_id}"
    return prefix


def parse_raw_record(raw: Dict[str, Any], fallback_index: int = 0) -> Dict[str, Any]:
    """Parse a single raw record from DS-1000 into the target benchmark schema.

    Schema:
      - problem_id: Mã bài toán (ví dụ: Pandas_12, PyTorch_5)
      - library: Thư viện đích (Pandas, Numpy, Matplotlib, Scikit-learn, Scipy, Pytorch, Tensorflow)
      - prompt: Văn bản mô tả bằng ngôn ngữ tự nhiên (NL intent)
      - reference_code: Đoạn code Python giải mẫu
      - test_code: Mã kiểm thử / test input kèm theo bài toán
    """
    metadata = raw.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    elif not isinstance(metadata, dict):
        metadata = {}

    raw_lib = metadata.get("library") or raw.get("library") or raw.get("lib")
    library = normalize_library_name(raw_lib)

    lib_problem_id = metadata.get("library_problem_id")
    if lib_problem_id is None:
        lib_problem_id = raw.get("library_problem_id")

    overall_id = metadata.get("problem_id")
    if overall_id is None:
        overall_id = raw.get("problem_id", fallback_index)

    # Check if problem_id was already pre-formatted
    existing_id = raw.get("problem_id")
    if isinstance(existing_id, str) and "_" in existing_id:
        problem_id = existing_id
    else:
        problem_id = generate_problem_id(library, lib_problem_id, overall_id)

    prompt = raw.get("prompt") or raw.get("nl_intent") or raw.get("intent") or ""
    reference_code = raw.get("reference_code") or raw.get("solution") or raw.get("code") or ""
    test_code = raw.get("code_context") or raw.get("test_code") or raw.get("test_case") or ""

    return {
        "problem_id": str(problem_id),
        "library": library,
        "prompt": str(prompt),
        "reference_code": str(reference_code),
        "test_code": str(test_code),
    }


def load_ds1000_from_huggingface(dataset_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load DS-1000 dataset using Hugging Face datasets library."""
    try:
        from datasets import load_dataset  # type: ignore
    except ImportError as err:
        raise RuntimeError(
            "The 'datasets' package is not installed. Please install it with 'pip install datasets' "
            "or use the GitHub source (--source github)."
        ) from err

    targets = [dataset_name] if dataset_name else HF_DATASET_IDENTIFIERS
    last_err: Optional[Exception] = None

    for target in targets:
        if not target:
            continue
        try:
            logger.info("Attempting to load dataset from Hugging Face: %s", target)
            ds = load_dataset(target)
            # Pick 'test' split if available, otherwise first split
            split_name = "test" if "test" in ds else list(ds.keys())[0]
            records = list(ds[split_name])
            logger.info("Successfully loaded %d records from Hugging Face (%s [%s])", len(records), target, split_name)
            return records
        except Exception as e:
            logger.warning("Failed to load from Hugging Face target '%s': %s", target, e)
            last_err = e

    raise RuntimeError(f"Could not load DS-1000 from Hugging Face: {last_err}")


def download_ds1000_from_github(url: Optional[str] = None) -> List[Dict[str, Any]]:
    """Download and extract DS-1000 archive (ds1000.jsonl.gz) from GitHub."""
    urls = [url] if url else GITHUB_RAW_URLS
    last_err: Optional[Exception] = None

    for candidate_url in urls:
        if not candidate_url:
            continue
        try:
            logger.info("Downloading DS-1000 dataset archive from: %s", candidate_url)
            req = urllib.request.Request(
                candidate_url,
                headers={"User-Agent": "intent-spawner-dataset-extractor/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                compressed_data = resp.read()

            logger.info("Decompressing %d bytes of gzip archive...", len(compressed_data))
            decompressed_text = gzip.decompress(compressed_data).decode("utf-8")
            lines = [line.strip() for line in decompressed_text.strip().split("\n") if line.strip()]

            records = [json.loads(line) for line in lines]
            logger.info("Successfully parsed %d records from %s", len(records), candidate_url)
            return records
        except Exception as e:
            logger.warning("Failed downloading from '%s': %s", candidate_url, e)
            last_err = e

    raise RuntimeError(f"Could not download DS-1000 from GitHub: {last_err}")


def load_ds1000_from_file(file_path: Path | str) -> List[Dict[str, Any]]:
    """Load records from a local file (.jsonl.gz, .jsonl, or .json)."""
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"Input file does not exist: {p}")

    logger.info("Loading records from local file: %s", p)
    if p.suffix == ".gz" or str(p).endswith(".jsonl.gz"):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        return [json.loads(line) for line in lines]

    if p.suffix == ".jsonl":
        with open(p, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        return [json.loads(line) for line in lines]

    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # Could be wrapped in a dict
            for key in ["records", "data", "problems"]:
                if key in data and isinstance(data[key], list):
                    return data[key]
            return [data]
        raise ValueError(f"Unrecognized JSON structure in {p}")


def fetch_dataset(
    source: str = "auto",
    input_file: Optional[str] = None,
    github_url: Optional[str] = None,
    hf_dataset_name: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """Fetch raw records according to the chosen source strategy.

    Returns:
        (records, resolved_source_description)
    """
    if input_file:
        return load_ds1000_from_file(input_file), f"local_file:{input_file}"

    if source == "local":
        raise ValueError("Must specify --input-file when --source is 'local'")

    if source == "github":
        records = download_ds1000_from_github(github_url)
        return records, "github:HKUNLP/DS-1000"

    if source == "huggingface":
        records = load_ds1000_from_huggingface(hf_dataset_name)
        return records, f"huggingface:{hf_dataset_name or HF_DATASET_IDENTIFIERS[0]}"

    if source == "auto":
        # 1. Try Hugging Face if datasets is available
        try:
            records = load_ds1000_from_huggingface(hf_dataset_name)
            return records, f"huggingface:{hf_dataset_name or HF_DATASET_IDENTIFIERS[0]}"
        except Exception as hf_err:
            logger.info("Hugging Face load unsuccessful (%s). Falling back to official GitHub repo.", hf_err)

        # 2. Fallback to GitHub raw archive
        records = download_ds1000_from_github(github_url)
        return records, "github:HKUNLP/DS-1000"

    raise ValueError(f"Unknown source option: {source}")


def extract_and_save_ds1000(
    records: Iterable[Dict[str, Any]],
    output_dir: Path | str,
    sample_size: Optional[int] = None,
    libraries: Optional[List[str]] = None,
    dry_run: bool = False,
    source_info: str = "unknown",
) -> List[Dict[str, Any]]:
    """Parse, filter, and save each DS-1000 problem as an individual JSON file."""
    output_path = Path(output_dir)
    parsed_items: List[Dict[str, Any]] = []

    filter_libs = set(normalize_library_name(lib) for lib in libraries) if libraries else None

    for i, raw in enumerate(records):
        parsed = parse_raw_record(raw, fallback_index=i)
        if filter_libs and parsed["library"] not in filter_libs:
            continue
        parsed_items.append(parsed)

    total_matched = len(parsed_items)

    if sample_size is not None and sample_size > 0:
        parsed_items = parsed_items[:sample_size]
        logger.info(
            "Applied --sample-size=%d (selected %d out of %d available problems)",
            sample_size,
            len(parsed_items),
            total_matched,
        )
    else:
        logger.info("Processing all %d problems (full dataset)", total_matched)

    lib_counter = Counter(item["library"] for item in parsed_items)
    logger.info("Library breakdown: %s", dict(lib_counter))

    if dry_run:
        logger.info("[Dry Run] Would write %d problem JSON files to %s", len(parsed_items), output_path)
        return parsed_items

    output_path.mkdir(parents=True, exist_ok=True)

    for item in parsed_items:
        file_path = output_path / f"{item['problem_id']}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(item, f, indent=2, ensure_ascii=False)

    # Write a manifest for provenance and auditing
    manifest_data = {
        "dataset": "DS-1000",
        "total_extracted": len(parsed_items),
        "source": source_info,
        "sample_size": sample_size,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "library_distribution": dict(lib_counter),
        "schema": ["problem_id", "library", "prompt", "reference_code", "test_code"],
    }
    manifest_file = output_path / "manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    logger.info(
        "Successfully wrote %d problem JSON files and manifest.json to %s",
        len(parsed_items),
        output_path,
    )
    return parsed_items


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Extract DS-1000 dataset problems into individual JSON files for benchmarks."
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="benchmarks_v5/raw_sources/ds1000",
        help="Target output directory (default: benchmarks_v5/raw_sources/ds1000)",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help="Subset sample size to extract (e.g. 10, 20). If omitted or <= 0, extracts the full dataset.",
    )
    parser.add_argument(
        "--source",
        choices=["auto", "github", "huggingface", "local"],
        default="auto",
        help="Source to retrieve dataset from: auto (default), github, huggingface, or local.",
    )
    parser.add_argument(
        "--input-file",
        type=str,
        default=None,
        help="Path to local file (.jsonl.gz, .jsonl, or .json) when using local source.",
    )
    parser.add_argument(
        "--github-url",
        type=str,
        default=None,
        help="Custom raw URL for DS-1000 jsonl.gz on GitHub.",
    )
    parser.add_argument(
        "--hf-dataset-name",
        type=str,
        default=None,
        help="Custom Hugging Face dataset identifier (default: xlangai/DS-1000).",
    )
    parser.add_argument(
        "--libraries",
        nargs="+",
        default=None,
        help="Filter specific target libraries (e.g. Pandas PyTorch).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and display summary without writing files.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        raw_records, source_info = fetch_dataset(
            source=args.source,
            input_file=args.input_file,
            github_url=args.github_url,
            hf_dataset_name=args.hf_dataset_name,
        )
        extract_and_save_ds1000(
            records=raw_records,
            output_dir=args.output_dir,
            sample_size=args.sample_size,
            libraries=args.libraries,
            dry_run=args.dry_run,
            source_info=source_info,
        )
        return 0
    except Exception as e:
        logger.error("Execution failed: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
