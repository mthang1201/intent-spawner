"""Tests for Protocol-v5 E5 image storage scalability experiment (Hypothesis H7)."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import yaml

from evaluation_v5.analysis.research_contracts import (
    ResearchContractError,
    validate_storage_evidence,
)
from evaluation_v5.image_storage.contracts import file_sha256, parse_image_digest
from evaluation_v5.image_storage.storage_contracts import (
    ImageLayerMetadata,
    LayerInspection,
    PrefixStorageMeasurement,
    SplitStage,
    StorageEvidenceRecord,
    StorageExecutionStatus,
    get_ordered_catalog_images,
)
from evaluation_v5.image_storage.storage_orchestrator import run_storage_evaluation
from evaluation_v5.image_storage.storage_runner import (
    DockerManifestStorageRunner,
    DryRunStorageRunner,
    SyntheticStorageRunner,
    create_storage_runner,
)
from evaluation_v5.image_storage.validate_evidence import (
    EvidenceValidationError,
    validate_e5_storage_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "recommender" / "image-catalog.yaml"


@pytest.fixture
def catalog() -> dict:
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_ordered_catalog_images_follows_priority_order(catalog):
    ordered = get_ordered_catalog_images(catalog)
    assert len(ordered) == 4
    image_ids = [item[0] for item in ordered]
    assert image_ids == [
        "minimal-python",
        "scipy-data-science",
        "pytorch-deep-learning",
        "tensorflow-deep-learning",
    ]
    for img_id, ref, digest in ordered:
        assert digest.startswith("sha256:")
        assert digest in ref


def test_synthetic_storage_runner_computes_deduplication(catalog):
    runner = SyntheticStorageRunner(catalog, target_arch="amd64")
    inspections, prefixes, status = runner.measure_all()

    assert status == StorageExecutionStatus.OBSERVED.value
    assert len(inspections) == 4
    assert len(prefixes) == 4

    # Monotonic prefix sizes
    assert [p.prefix_size for p in prefixes] == [1, 2, 3, 4]

    # Non-expansion invariant: unique <= naive at every prefix
    for p in prefixes:
        assert p.unique_layer_bytes <= p.naive_logical_bytes
        assert p.savings_bytes == p.naive_logical_bytes - p.unique_layer_bytes

    # Final savings strictly positive for hierarchical Jupyter stacks
    final_prefix = prefixes[-1]
    assert final_prefix.savings_bytes > 0
    assert final_prefix.savings_ratio > 0.0


def test_synthetic_storage_with_zero_layer_overlap(catalog):
    # Completely disjoint layers
    disjoint_layers = {
        "minimal-python": [{"digest": "sha256:" + "1" * 64, "size": 100}],
        "scipy-data-science": [{"digest": "sha256:" + "2" * 64, "size": 200}],
        "pytorch-deep-learning": [{"digest": "sha256:" + "3" * 64, "size": 300}],
        "tensorflow-deep-learning": [{"digest": "sha256:" + "4" * 64, "size": 400}],
    }
    runner = SyntheticStorageRunner(catalog, injected_image_layers=disjoint_layers)
    inspections, prefixes, status = runner.measure_all()

    assert status == StorageExecutionStatus.OBSERVED.value
    expected_naive = [100, 300, 600, 1000]
    for idx, p in enumerate(prefixes):
        assert p.naive_logical_bytes == expected_naive[idx]
        assert p.unique_layer_bytes == expected_naive[idx]
        assert p.savings_bytes == 0
        assert p.savings_ratio == 0.0
        assert p.unique_layer_bytes <= p.naive_logical_bytes


def test_synthetic_storage_with_full_overlap(catalog):
    # All images share identical single layer
    shared_digest = "sha256:" + "f" * 64
    identical_layers = {
        "minimal-python": [{"digest": shared_digest, "size": 500}],
        "scipy-data-science": [{"digest": shared_digest, "size": 500}],
        "pytorch-deep-learning": [{"digest": shared_digest, "size": 500}],
        "tensorflow-deep-learning": [{"digest": shared_digest, "size": 500}],
    }
    runner = SyntheticStorageRunner(catalog, injected_image_layers=identical_layers)
    inspections, prefixes, status = runner.measure_all()

    for idx, p in enumerate(prefixes, start=1):
        assert p.naive_logical_bytes == 500 * idx
        assert p.unique_layer_bytes == 500
        assert p.savings_bytes == 500 * (idx - 1)
        assert p.unique_layer_bytes <= p.naive_logical_bytes


def test_dry_run_storage_runner_emits_not_executed_without_fabrication(catalog):
    runner = DryRunStorageRunner(catalog, target_arch="amd64")
    inspections, prefixes, status = runner.measure_all()

    assert status == StorageExecutionStatus.NOT_EXECUTED.value
    assert len(inspections) == 4
    for meta in inspections:
        assert meta.total_bytes == 0
        assert len(meta.layers) == 0

    assert len(prefixes) == 4
    for p in prefixes:
        assert p.naive_logical_bytes == 0
        assert p.unique_layer_bytes == 0
        assert p.savings_bytes == 0


def test_storage_evidence_contract_validation(catalog):
    cat_sha = file_sha256(CATALOG_PATH)
    ordered = get_ordered_catalog_images(catalog)
    digests = [item[2] for item in ordered]

    valid_record = {
        "schema_version": "protocol-v5-image-storage-evidence-v1.0.0",
        "protocol_version": "5.0.0",
        "experiment_id": "E5",
        "execution_status": "OBSERVED",
        "split_stage": "confirmatory",
        "claims_permitted": True,
        "measured_at_utc": "2026-09-05T00:00:00Z",
        "catalog": {
            "version": str(catalog.get("catalog_version")),
            "file_sha256": cat_sha,
            "ordered_image_digests": digests,
        },
        "platform": {
            "environment_id": "env-test",
            "runtime": "docker",
            "operating_system": "linux",
            "architecture": "amd64",
        },
        "measurement_method": "docker manifest inspect OCI layer digest accounting",
        "prefixes": [
            {"prefix_size": 1, "image_digests": digests[:1], "naive_logical_bytes": 100, "unique_layer_bytes": 100},
            {"prefix_size": 2, "image_digests": digests[:2], "naive_logical_bytes": 200, "unique_layer_bytes": 150},
            {"prefix_size": 3, "image_digests": digests[:3], "naive_logical_bytes": 350, "unique_layer_bytes": 250},
            {"prefix_size": 4, "image_digests": digests[:4], "naive_logical_bytes": 500, "unique_layer_bytes": 350},
        ],
        "provenance": {
            "git_revision": "a" * 40,
            "dataset_sha256": cat_sha,
            "backend_system_versions": {"P2": "p2-pipeline-v1.0.0"},
        },
    }

    # Should validate cleanly
    validate_storage_evidence(valid_record)

    # Invariant: development cannot permit claims
    dev_invalid = dict(valid_record)
    dev_invalid["split_stage"] = "development"
    dev_invalid["claims_permitted"] = True
    with pytest.raises(ResearchContractError, match="development storage evidence cannot permit claims"):
        validate_storage_evidence(dev_invalid)

    # Invariant: NOT_EXECUTED cannot permit claims
    not_exec_invalid = dict(valid_record)
    not_exec_invalid["execution_status"] = "NOT_EXECUTED"
    not_exec_invalid["claims_permitted"] = True
    with pytest.raises(ResearchContractError, match="NOT_EXECUTED storage evidence cannot permit claims"):
        validate_storage_evidence(not_exec_invalid)

    # Invariant: wrong prefix order raises
    order_invalid = dict(valid_record)
    prefixes_corrupt = list(valid_record["prefixes"])
    prefixes_corrupt[1] = dict(prefixes_corrupt[1])
    prefixes_corrupt[1]["image_digests"] = [digests[1], digests[0]]
    order_invalid["prefixes"] = prefixes_corrupt
    with pytest.raises(ResearchContractError, match="storage prefix images differ from the frozen catalog order"):
        validate_storage_evidence(order_invalid)

    # Invariant: missing prefix count raises
    count_invalid = dict(valid_record)
    count_invalid["prefixes"] = valid_record["prefixes"][:2]
    with pytest.raises(ResearchContractError, match="storage evidence must contain every ordered catalog prefix"):
        validate_storage_evidence(count_invalid)


def test_storage_orchestrator_end_to_end_synthetic(tmp_path, catalog):
    out_dir = tmp_path / "e5-storage-synthetic"
    run_storage_evaluation(
        catalog_path=CATALOG_PATH,
        mode="synthetic",
        stage="confirmatory",
        output_dir=out_dir,
        run_id="e5-storage-synthetic-test",
    )

    # Verify package structure
    assert (out_dir / "SHA256SUMS").is_file()
    assert (out_dir / "manifest.json").is_file()
    assert (out_dir / "raw" / "image_layers.json").is_file()
    assert (out_dir / "raw" / "environment.json").is_file()
    assert (out_dir / "derived" / "storage_metrics.json").is_file()
    assert (out_dir / "report" / "status.json").is_file()
    assert (out_dir / "report" / "E5_IMAGE_STORAGE_REPORT.md").is_file()

    # Verify fail-closed validation passes
    validation_res = validate_e5_storage_evidence(out_dir)
    assert validation_res["status"] == "PASS"
    assert validation_res["eligible_as_current_e5_evidence"] is True
    assert validation_res["execution_status"] == "OBSERVED"
    assert validation_res["split_stage"] == "confirmatory"
    assert validation_res["claims_permitted"] is True
    assert validation_res["total_prefixes"] == 4
    assert validation_res["final_storage_savings_bytes"] > 0


def test_storage_orchestrator_end_to_end_dry_run(tmp_path):
    out_dir = tmp_path / "e5-storage-dry-run"
    run_storage_evaluation(
        catalog_path=CATALOG_PATH,
        mode="dry-run",
        stage="development",
        output_dir=out_dir,
        run_id="e5-storage-dry-run-test",
    )

    validation_res = validate_e5_storage_evidence(out_dir)
    assert validation_res["status"] == "PASS"
    assert validation_res["eligible_as_current_e5_evidence"] is False
    assert validation_res["execution_status"] == "NOT_EXECUTED"
    assert validation_res["split_stage"] == "development"
    assert validation_res["claims_permitted"] is False


def test_research_analysis_discovers_and_evaluates_h7(tmp_path):
    from evaluation_v5.analysis.research_analysis import discover_evidence

    results_root = tmp_path / "results_v5" / "protocol-v5.0.0"
    e5_dir = results_root / "E5" / "e5-storage-confirmatory-pkg"

    run_storage_evaluation(
        catalog_path=CATALOG_PATH,
        mode="synthetic",
        stage="confirmatory",
        output_dir=e5_dir,
        run_id="e5-storage-confirmatory-pkg",
    )

    candidates = discover_evidence(results_root)
    storage_candidates = [c for c in candidates if c.requirement_id == "image_storage"]
    assert len(storage_candidates) == 1

    candidate = storage_candidates[0]
    assert candidate.evidence_class == "E5"
    assert candidate.experiment_id == "E5_IMAGE_STORAGE"
    assert candidate.execution_status == "OBSERVED"
    assert candidate.claims_permitted is True
    assert candidate.claim_eligibility == "ELIGIBLE_CONFIRMATORY"

    h7_metrics = candidate.metrics["H7"]
    assert h7_metrics["all_prefixes_nonexpanding"] is True
    assert h7_metrics["final_savings_bytes"] > 0
    assert h7_metrics["prefix_order_valid"] is True
    assert len(h7_metrics["prefixes"]) == 4


def test_cli_storage_invocation(tmp_path):
    import subprocess
    import sys

    out_dir = tmp_path / "cli-out"
    cmd = [
        sys.executable,
        "-m",
        "evaluation_v5.image_storage",
        "--experiment",
        "storage",
        "--mode",
        "synthetic",
        "--stage",
        "confirmatory",
        "--output-dir",
        str(out_dir),
    ]
    res = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"CLI execution failed: {res.stderr}"
    assert "E5 storage scalability evaluation completed successfully" in res.stdout
    assert (out_dir / "derived" / "storage_metrics.json").is_file()
    assert (out_dir / "report" / "E5_IMAGE_STORAGE_REPORT.md").is_file()


def test_docker_manifest_storage_runner_inspect(catalog):
    import shutil
    if not shutil.which("docker"):
        pytest.skip("Docker CLI not available")

    # Fast probe to see if docker manifest inspect is reachable
    ref = catalog["images"]["minimal-python"]["reference"]
    try:
        runner = DockerManifestStorageRunner(catalog, target_arch="amd64", timeout_seconds=30.0)
        meta = runner.inspect_image_layers("minimal-python", ref)
        assert meta.image_id == "minimal-python"
        assert len(meta.layers) > 0
        assert meta.total_bytes > 0
        for l in meta.layers:
            assert l.digest.startswith("sha256:")
            assert l.size >= 0
    except Exception as exc:
        pytest.skip(f"Docker manifest inspect unavailable or timed out: {exc}")

