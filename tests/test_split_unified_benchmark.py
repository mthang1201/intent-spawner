import json
from pathlib import Path
import pytest

from evaluation_v5.dataset_pipeline.split_unified_benchmark import (
    normalize_prompt_text,
    extract_family_library,
    build_disjoint_prompt_clusters,
    partition_clusters,
    split_unified_benchmark,
)
from evaluation_v5.gold_dataset import load_gold_dataset


def test_normalize_prompt_text():
    assert normalize_prompt_text("  How to convert NumPy array?! ") == "how to convert numpy array"
    assert normalize_prompt_text("Làm thế nào để reshape tensor?") == "làm thế nào để reshape tensor"


def test_zero_leakage_and_manifest_properties(tmp_path):
    gold_path = Path("benchmarks_v5/gold_families_unified_v5.yaml")
    ds1000_oracle = Path("benchmarks_v5/oracle/profile_labels_ds1000.jsonl")
    arcade_oracle = Path("benchmarks_v5/oracle/profile_labels_arcade.jsonl")

    if not gold_path.exists():
        pytest.skip("Unified gold families dataset does not exist")

    out_dir = tmp_path / "benchmark_test"
    manifest = split_unified_benchmark(
        gold_families_path=gold_path,
        ds1000_oracle_path=ds1000_oracle,
        arcade_oracle_path=arcade_oracle,
        output_dir=out_dir,
        seed=42,
    )

    assert manifest["total_families"] == 1051
    assert manifest["total_cases"] == 3153
    assert set(manifest["splits"].keys()) == {"train", "dev", "test"}

    # Check files exist
    assert (out_dir / "train.jsonl").exists()
    assert (out_dir / "dev.jsonl").exists()
    assert (out_dir / "test.jsonl").exists()
    assert (out_dir / "SCHEMA.md").exists()
    assert (out_dir / "manifest.json").exists()

    # Verify zero leakage
    def read_prompts(split_name):
        prompts = set()
        with open(out_dir / f"{split_name}.jsonl") as f:
            for line in f:
                rec = json.loads(line)
                prompts.add(normalize_prompt_text(rec["prompt"]))
        return prompts

    train_p = read_prompts("train")
    dev_p = read_prompts("dev")
    test_p = read_prompts("test")

    assert len(train_p.intersection(dev_p)) == 0
    assert len(train_p.intersection(test_p)) == 0
    assert len(dev_p.intersection(test_p)) == 0
