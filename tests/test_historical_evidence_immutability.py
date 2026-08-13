"""Regression guard for the immutable Protocol-v4 historical evidence."""

from pathlib import Path

import pytest

from evaluation_v4.dataset import file_sha256


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = ROOT / "results" / "v4-live-20260810"


def test_v4_live_20260810_evidence_is_byte_for_byte_immutable():
    if not HISTORICAL.is_dir():
        pytest.skip("historical live evidence is intentionally not distributed with source-only clones")
    assert file_sha256(HISTORICAL / "predictions.jsonl") == (
        "3a5476ce8f2ebf9dfdafaa31c8473abb6454f7998560c03b78c459721c300587"
    )
    assert file_sha256(HISTORICAL / "run-manifest.json") == (
        "14fc24443bd5fdf248040afbad36b645c52188e78a322579eabe93b98010e227"
    )
