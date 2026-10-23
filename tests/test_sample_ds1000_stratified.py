"""Unit tests for DS-1000 stratified sampling script."""

from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
import pytest

from scripts.datasets.sample_ds1000_stratified import (
    DEFAULT_CATALOG_PATH,
    DEFAULT_INPUT_FILE,
    allocate_quotas,
    derive_stratum_seed,
    load_valid_image_ids,
    main,
    sample_ds1000,
)


@pytest.fixture
def mock_dataset(tmp_path):
    """Generate a synthetic multi-image dataset for unit testing."""
    records = []
    # 50 scipy items (10 per library)
    for lib in ["Pandas", "Numpy", "Matplotlib", "Scikit-learn", "Scipy"]:
        for i in range(10):
            records.append({
                "problem_id": f"{lib}_{i}",
                "library": lib,
                "image": "scipy-data-science",
                "image_id": "scipy-data-science",
                "prompt": f"Solve {lib} {i}",
                "reference_code": "pass",
            })
    # 20 pytorch items
    for i in range(20):
        records.append({
            "problem_id": f"PyTorch_{i}",
            "library": "Pytorch",
            "image": "pytorch-deep-learning",
            "image_id": "pytorch-deep-learning",
            "prompt": f"Solve PyTorch {i}",
            "reference_code": "pass",
        })
    # 15 tensorflow items
    for i in range(15):
        records.append({
            "problem_id": f"Tensorflow_{i}",
            "library": "Tensorflow",
            "image": "tensorflow-deep-learning",
            "image_id": "tensorflow-deep-learning",
            "prompt": f"Solve Tensorflow {i}",
            "reference_code": "pass",
        })

    file_path = tmp_path / "mock_dataset.jsonl"
    with open(file_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return file_path


def test_catalog_reading_not_hardcoded():
    images = load_valid_image_ids(DEFAULT_CATALOG_PATH)
    assert isinstance(images, list)
    assert len(images) >= 3
    assert "scipy-data-science" in images
    assert "pytorch-deep-learning" in images
    assert "tensorflow-deep-learning" in images


def test_seed_reproducibility():
    """Identical seed must yield the exact same sample and pilot sequence."""
    if not DEFAULT_INPUT_FILE.is_file():
        pytest.skip(f"Input file {DEFAULT_INPUT_FILE} not found")

    sample1, pilot1, _ = sample_ds1000(
        input_file=DEFAULT_INPUT_FILE,
        catalog_path=DEFAULT_CATALOG_PATH,
        n=150,
        pilot_n=20,
        seed=20261021,
    )
    sample2, pilot2, _ = sample_ds1000(
        input_file=DEFAULT_INPUT_FILE,
        catalog_path=DEFAULT_CATALOG_PATH,
        n=150,
        pilot_n=20,
        seed=20261021,
    )

    assert [r["problem_id"] for r in sample1] == [r["problem_id"] for r in sample2]
    assert [r["problem_id"] for r in pilot1] == [r["problem_id"] for r in pilot2]


def test_different_seeds_produce_different_samples():
    """Different seeds must alter the selection or order."""
    if not DEFAULT_INPUT_FILE.is_file():
        pytest.skip(f"Input file {DEFAULT_INPUT_FILE} not found")

    sample1, _, _ = sample_ds1000(
        input_file=DEFAULT_INPUT_FILE,
        catalog_path=DEFAULT_CATALOG_PATH,
        n=150,
        pilot_n=20,
        seed=20261021,
    )
    sample2, _, _ = sample_ds1000(
        input_file=DEFAULT_INPUT_FILE,
        catalog_path=DEFAULT_CATALOG_PATH,
        n=150,
        pilot_n=20,
        seed=99999999,
    )

    ids1 = [r["problem_id"] for r in sample1]
    ids2 = [r["problem_id"] for r in sample2]
    assert ids1 != ids2


def test_no_duplicate_problems():
    """No problem should be duplicated in sample or pilot."""
    if not DEFAULT_INPUT_FILE.is_file():
        pytest.skip(f"Input file {DEFAULT_INPUT_FILE} not found")

    sample, pilot, _ = sample_ds1000(
        input_file=DEFAULT_INPUT_FILE,
        catalog_path=DEFAULT_CATALOG_PATH,
        n=150,
        pilot_n=20,
        seed=20261021,
    )

    sample_ids = [r["problem_id"] for r in sample]
    assert len(sample_ids) == len(set(sample_ids)) == 150

    pilot_ids = [r["problem_id"] for r in pilot]
    assert len(pilot_ids) == len(set(pilot_ids)) == 20


def test_50_percent_constraint():
    """No image should exceed 50% of the sample or pilot."""
    if not DEFAULT_INPUT_FILE.is_file():
        pytest.skip(f"Input file {DEFAULT_INPUT_FILE} not found")

    sample, pilot, _ = sample_ds1000(
        input_file=DEFAULT_INPUT_FILE,
        catalog_path=DEFAULT_CATALOG_PATH,
        n=150,
        pilot_n=20,
        seed=20261021,
    )

    # In sample 150: max cap is 75
    sample_img_counts = Counter(r["image"] for r in sample)
    for img, count in sample_img_counts.items():
        assert count <= math.floor(150 * 0.5), f"Image {img} has {count} > 75"

    assert sample_img_counts["scipy-data-science"] == 75

    # In pilot 20: max cap is 10
    pilot_img_counts = Counter(r["image"] for r in pilot)
    for img, count in pilot_img_counts.items():
        assert count <= math.floor(20 * 0.5), f"Image {img} has {count} > 10 in pilot"

    assert pilot_img_counts["scipy-data-science"] == 10
    assert pilot_img_counts["pytorch-deep-learning"] == 5
    assert pilot_img_counts["tensorflow-deep-learning"] == 5


def test_pilot_is_prefix_of_sample():
    """Pilot 20 must be the first 20 records of the permutation."""
    if not DEFAULT_INPUT_FILE.is_file():
        pytest.skip(f"Input file {DEFAULT_INPUT_FILE} not found")

    sample, pilot, _ = sample_ds1000(
        input_file=DEFAULT_INPUT_FILE,
        catalog_path=DEFAULT_CATALOG_PATH,
        n=150,
        pilot_n=20,
        seed=20261021,
    )

    assert [r["problem_id"] for r in sample[:20]] == [r["problem_id"] for r in pilot]


def test_library_balance_within_image():
    """Within scipy-data-science, all 5 libraries must be equally balanced."""
    if not DEFAULT_INPUT_FILE.is_file():
        pytest.skip(f"Input file {DEFAULT_INPUT_FILE} not found")

    sample, pilot, _ = sample_ds1000(
        input_file=DEFAULT_INPUT_FILE,
        catalog_path=DEFAULT_CATALOG_PATH,
        n=150,
        pilot_n=20,
        seed=20261021,
    )

    sample_scipy_libs = Counter(r["library"] for r in sample if r["image"] == "scipy-data-science")
    assert len(sample_scipy_libs) == 5
    for lib, count in sample_scipy_libs.items():
        assert count == 15, f"Library {lib} has {count} items, expected 15"

    pilot_scipy_libs = Counter(r["library"] for r in pilot if r["image"] == "scipy-data-science")
    assert len(pilot_scipy_libs) == 5
    for lib, count in pilot_scipy_libs.items():
        assert count == 2, f"Library {lib} has {count} items in pilot, expected 2"


def test_metadata_fields_enriched():
    """Every record must preserve all input fields and add sample_stratum and sample_seed."""
    if not DEFAULT_INPUT_FILE.is_file():
        pytest.skip(f"Input file {DEFAULT_INPUT_FILE} not found")

    sample, pilot, _ = sample_ds1000(
        input_file=DEFAULT_INPUT_FILE,
        catalog_path=DEFAULT_CATALOG_PATH,
        n=150,
        pilot_n=20,
        seed=20261021,
    )

    for r in sample:
        assert "sample_stratum" in r
        assert r["sample_stratum"] == f"{r['image']}::{r['library']}"
        assert r["sample_seed"] == 20261021
        assert "problem_id" in r
        assert "prompt" in r
        assert "reference_code" in r
        assert "structured_intent" in r


def test_quota_allocation_with_small_pool(caplog):
    """If an image has fewer items than requested quota, cap at available, warn, and do not duplicate."""
    strata_counts = {
        ("scipy-data-science", "Pandas"): 100,
        ("pytorch-deep-learning", "Pytorch"): 4,  # Only 4 items available
        ("tensorflow-deep-learning", "Tensorflow"): 25,
    }
    valid_images = {"scipy-data-science", "pytorch-deep-learning", "tensorflow-deep-learning"}

    quotas = allocate_quotas(
        target_n=30,
        strata_counts=strata_counts,
        valid_images=valid_images,
        max_image_share=0.5,
    )

    # Max cap for 30 is 15
    assert quotas[("scipy-data-science", "Pandas")] <= 15
    # Pytorch cannot exceed available 4
    assert quotas[("pytorch-deep-learning", "Pytorch")] <= 4
    assert ("pytorch-deep-learning", "Pytorch") in quotas


def test_cli_execution_end_to_end(mock_dataset, tmp_path):
    """Test full CLI flow with output files generation."""
    sample_out = tmp_path / "cli_sample.jsonl"
    pilot_out = tmp_path / "cli_pilot.jsonl"
    report_out = tmp_path / "cli_report.md"

    exit_code = main([
        "--input-file", str(mock_dataset),
        "--catalog-path", str(DEFAULT_CATALOG_PATH),
        "--n", "20",
        "--pilot-n", "6",
        "--seed", "20261021",
        "--sample-output", str(sample_out),
        "--pilot-output", str(pilot_out),
        "--report-output", str(report_out),
    ])

    assert exit_code == 0
    assert sample_out.is_file()
    assert pilot_out.is_file()
    assert report_out.is_file()

    with open(sample_out) as f:
        sample_lines = [json.loads(line) for line in f]
    with open(pilot_out) as f:
        pilot_lines = [json.loads(line) for line in f]

    assert len(sample_lines) == 20
    assert len(pilot_lines) == 6
    assert [r["problem_id"] for r in sample_lines[:6]] == [r["problem_id"] for r in pilot_lines]
