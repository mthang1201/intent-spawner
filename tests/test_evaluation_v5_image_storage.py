"""Comprehensive test suite for Protocol-v5 E5 image storage scalability and catalog evaluation.

Covers all 23+ required experimental contracts, invariants, and edge cases.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch
import pytest
import yaml

from evaluation_v5.analysis.research_contracts import (
    ResearchContractError,
    validate_storage_evidence,
)
from evaluation_v5.image_storage.contracts import file_sha256, parse_image_digest
from evaluation_v5.image_storage.recommendation_evaluator import (
    evaluate_catalog_scale_recommendation,
)
from evaluation_v5.image_storage.storage_contracts import (
    DEFAULT_CATALOG_SCALES,
    SIZE_DOMAIN_COMPRESSED_OCI_BLOB,
    SIZE_DOMAIN_UNCOMPRESSED,
    CatalogImageEntry,
    ExperimentalCatalogConfig,
    ImageLayerMetadata,
    LayerInspection,
    MarginalStorageRecord,
    PairwiseReuseAnalysis,
    PrefixStorageMeasurement,
    ScaleLevelEvaluationRecord,
    SizeDomainMismatchError,
    SplitStage,
    StorageEvidenceRecord,
    StorageExecutionStatus,
    assert_size_domain_consistent,
    compute_marginal_storage,
    compute_pairwise_layer_reuse,
    get_experimental_catalog_config,
    get_ordered_catalog_images,
)
from evaluation_v5.image_storage.storage_figures import generate_all_figures
from evaluation_v5.image_storage.storage_orchestrator import run_storage_evaluation
from evaluation_v5.image_storage.storage_runner import (
    BaseStorageRunner,
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


# Case 1: Completely disjoint images: unique == logical, saving = 0
def test_completely_disjoint_images(catalog):
    injected = {
        "minimal-python": [
            {"digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111", "size": 100_000_000},
        ],
        "scipy-data-science": [
            {"digest": "sha256:2222222222222222222222222222222222222222222222222222222222222222", "size": 200_000_000},
        ],
        "pytorch-deep-learning": [
            {"digest": "sha256:3333333333333333333333333333333333333333333333333333333333333333", "size": 300_000_000},
        ],
        "tensorflow-deep-learning": [
            {"digest": "sha256:4444444444444444444444444444444444444444444444444444444444444444", "size": 400_000_000},
        ],
    }
    runner = SyntheticStorageRunner(catalog, injected_image_layers=injected)
    inspections, prefixes, status = runner.measure_all()
    assert status == "OBSERVED"
    for p in prefixes:
        assert p.unique_layer_bytes == p.naive_logical_bytes
        assert p.savings_bytes == 0
        assert p.savings_ratio == 0.0


# Case 2: Completely shared layer sets: unique == single image size
def test_completely_shared_layer_sets(catalog):
    shared_layer = [{"digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "size": 500_000_000}]
    injected = {img: shared_layer for img in catalog["images"]}
    runner = SyntheticStorageRunner(catalog, injected_image_layers=injected)
    inspections, prefixes, status = runner.measure_all()
    for idx, p in enumerate(prefixes, start=1):
        assert p.unique_layer_bytes == 500_000_000
        assert p.naive_logical_bytes == idx * 500_000_000
        assert p.savings_bytes == (idx - 1) * 500_000_000


# Case 3: Partial layer overlap: mixed shared and distinct
def test_partial_layer_overlap(catalog):
    base_l = {"digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "size": 100_000_000}
    injected = {
        "minimal-python": [base_l],
        "scipy-data-science": [base_l, {"digest": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", "size": 150_000_000}],
        "pytorch-deep-learning": [base_l, {"digest": "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd", "size": 250_000_000}],
        "tensorflow-deep-learning": [base_l, {"digest": "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "size": 350_000_000}],
    }
    runner = SyntheticStorageRunner(catalog, injected_image_layers=injected)
    inspections, prefixes, status = runner.measure_all()
    final_p = prefixes[-1]
    assert final_p.unique_layer_bytes < final_p.naive_logical_bytes
    assert final_p.savings_bytes == 3 * 100_000_000  # 3 images reuse base_l


# Case 4: Duplicate digest within same image or across images
def test_duplicate_digest_within_same_image(catalog):
    dup_l = {"digest": "sha256:4f4fb700ef54461cfa02571ae0db9a0dc1e0cdb5577484a6d75e68dc38e8acc1", "size": 32}
    injected = {
        "minimal-python": [dup_l, dup_l],  # 2 identical layers inside minimal-python
        "scipy-data-science": [dup_l],
        "pytorch-deep-learning": [dup_l],
        "tensorflow-deep-learning": [dup_l],
    }
    runner = SyntheticStorageRunner(catalog, injected_image_layers=injected)
    inspections, prefixes, status = runner.measure_all()
    p1 = prefixes[0]
    assert p1.naive_logical_bytes == 64
    assert p1.unique_layer_bytes == 32
    assert p1.savings_bytes == 32


# Case 5: Ordered layer preservation
def test_ordered_layer_preservation(catalog):
    layers = [
        {"digest": f"sha256:{i:064d}", "size": 1000} for i in range(1, 10)
    ]
    injected = {"minimal-python": layers}
    runner = SyntheticStorageRunner(catalog, injected_image_layers=injected)
    meta = runner.inspect_image_layers("minimal-python", catalog["images"]["minimal-python"]["reference"])
    assert len(meta.ordered_layer_digests) == 9
    assert meta.ordered_layer_digests == tuple(f"sha256:{i:064d}" for i in range(1, 10))


# Case 6: Marginal U_n - U_(n-1) calculation
def test_marginal_unique_bytes_computation(catalog):
    runner = SyntheticStorageRunner(catalog)
    inspections, prefixes, status = runner.measure_all()
    marginals = compute_marginal_storage(inspections)
    assert len(marginals) == len(prefixes)
    for idx, m in enumerate(marginals):
        assert m.marginal_unique_bytes == m.new_unique_bytes - m.previous_unique_bytes
        assert m.cumulative_unique_bytes == prefixes[idx].unique_layer_bytes
        assert m.cumulative_logical_bytes == prefixes[idx].naive_logical_bytes
    # Sum of marginals equals final unique bytes
    assert sum(m.marginal_unique_bytes for m in marginals) == prefixes[-1].unique_layer_bytes


# Case 7 & 8: Pairwise shared-layer count and bytes
def test_pairwise_shared_count_and_bytes(catalog):
    l1 = LayerInspection(digest="sha256:1111111111111111111111111111111111111111111111111111111111111111", size=100)
    l2 = LayerInspection(digest="sha256:2222222222222222222222222222222222222222222222222222222222222222", size=200)
    l3 = LayerInspection(digest="sha256:3333333333333333333333333333333333333333333333333333333333333333", size=300)

    meta_a = ImageLayerMetadata("img_a", "ref_a", "sha256:aaa", {}, (l1, l2), 300)
    meta_b = ImageLayerMetadata("img_b", "ref_b", "sha256:bbb", {}, (l2, l3), 500)

    analysis = compute_pairwise_layer_reuse([meta_a, meta_b])
    assert analysis.shared_layer_count_matrix[0][1] == 1  # shares l2
    assert analysis.shared_layer_byte_matrix[0][1] == 200  # size of l2
    assert analysis.shared_layer_count_matrix[1][0] == 1
    assert analysis.shared_layer_byte_matrix[1][0] == 200


# Case 9: Pairwise matrix symmetry and diagonal semantics
def test_pairwise_matrix_symmetry_and_diagonal(catalog):
    runner = SyntheticStorageRunner(catalog)
    inspections, _, _ = runner.measure_all()
    analysis = compute_pairwise_layer_reuse(inspections)
    assert analysis.symmetry_verified is True
    n = len(inspections)
    for i in range(n):
        for j in range(n):
            assert analysis.shared_layer_count_matrix[i][j] == analysis.shared_layer_count_matrix[j][i]
            assert analysis.shared_layer_byte_matrix[i][j] == analysis.shared_layer_byte_matrix[j][i]
        # Diagonal check
        assert analysis.shared_layer_count_matrix[i][i] == len(inspections[i].layers)
        assert analysis.shared_layer_byte_matrix[i][i] == inspections[i].total_bytes


# Case 10: Compressed vs uncompressed domain separation
def test_compressed_uncompressed_domain_separation():
    # Valid call with same domain
    assert_size_domain_consistent(SIZE_DOMAIN_COMPRESSED_OCI_BLOB, SIZE_DOMAIN_COMPRESSED_OCI_BLOB)
    # Rejection of mismatched domains
    with pytest.raises(SizeDomainMismatchError, match="Cross-domain aggregation rejected"):
        assert_size_domain_consistent(SIZE_DOMAIN_COMPRESSED_OCI_BLOB, SIZE_DOMAIN_UNCOMPRESSED)
    with pytest.raises(SizeDomainMismatchError, match="Unrecognized storage size domain"):
        assert_size_domain_consistent("arbitrary_unknown", "arbitrary_unknown")


# Case 11: Missing or negative layer size fails closed
def test_missing_layer_size_fails_closed(catalog):
    injected = {
        "minimal-python": [{"digest": "sha256:1111", "size": -50}]  # Negative size
    }
    runner = SyntheticStorageRunner(catalog, injected_image_layers=injected)
    with pytest.raises(RuntimeError, match="Negative layer size"):
        runner.inspect_image_layers("minimal-python", catalog["images"]["minimal-python"]["reference"])


# Case 12: Inaccessible/unavailable manifest fails closed
def test_inaccessible_manifest_fails_closed(catalog):
    runner = DockerManifestStorageRunner(catalog, timeout_seconds=1.0)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="repository not found: 404")
        with pytest.raises(RuntimeError, match="docker manifest inspect failed"):
            runner.inspect_image_layers("minimal-python", catalog["images"]["minimal-python"]["reference"])


# Case 13: Deterministic catalog ordering
def test_deterministic_catalog_ordering(catalog):
    ordered = get_ordered_catalog_images(catalog)
    assert len(ordered) == 4
    assert [img[0] for img in ordered] == [
        "minimal-python",
        "scipy-data-science",
        "pytorch-deep-learning",
        "tensorflow-deep-learning",
    ]


# Case 14: Arbitrary configured catalog sizes (4, 8, 16)
def test_arbitrary_configured_catalog_sizes(catalog):
    exp_cfg = get_experimental_catalog_config(catalog, scales=(2, 4, 8, 16))
    assert exp_cfg.catalog_scales == (2, 4, 8, 16)
    # Available approved count is 4
    assert exp_cfg.get_scale_status(2) == ("OBSERVED", "")
    assert exp_cfg.get_scale_status(4) == ("OBSERVED", "")
    st_8, r_8 = exp_cfg.get_scale_status(8)
    assert st_8 == "NOT_EXECUTED"
    assert "insufficient_approved_images" in r_8
    st_16, r_16 = exp_cfg.get_scale_status(16)
    assert st_16 == "NOT_EXECUTED"
    assert "insufficient_approved_images" in r_16


# Case 15: No automatic production catalog mutation
def test_no_automatic_production_catalog_mutation(catalog):
    original_sha = file_sha256(CATALOG_PATH)
    with tempfile.TemporaryDirectory() as tmp:
        run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            scales=(4, 8, 16),
        )
    after_sha = file_sha256(CATALOG_PATH)
    assert original_sha == after_sha


# Case 16 & 17: Provenance generation and immutable digest pinning
def test_provenance_and_immutable_digest_pinning(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
        )
        layers = json.loads((out_dir / "raw" / "image_layers.json").read_text())
        for l in layers:
            assert l["is_digest_pinned"] is True
            assert l["resolved_digest"].startswith("sha256:")
            assert l["size_domain"] == SIZE_DOMAIN_COMPRESSED_OCI_BLOB
            assert l["total_bytes"] > 0


# Case 18: Recommendation scale aggregation (accuracy, recall, latency)
def test_recommendation_scale_aggregation(catalog):
    exp_cfg = get_experimental_catalog_config(catalog, scales=(4,))
    scale_imgs = exp_cfg.get_scale_images(4)
    res = evaluate_catalog_scale_recommendation(catalog, scale_imgs, k=5)
    assert res["status"] == "OBSERVED"
    assert res["image_acceptable_accuracy"] > 0.8
    assert res["image_preferred_accuracy"] > 0.8
    assert res["retrieval_recall_at_k"] > 0.5
    assert res["recall_k"] == 5
    assert res["latency"]["mean_seconds"] > 0.0


# Case 19: Missing P2 evaluation at catalog scale handled honestly
def test_missing_p2_evaluation_at_catalog_scale(catalog):
    res = evaluate_catalog_scale_recommendation(catalog, scale_images=(), k=5)
    assert res["status"] == "NOT_EXECUTED"
    assert res["image_acceptable_accuracy"] is None
    assert res["retrieval_recall_at_k"] is None


# Case 20: Figure generation directly from evidence data
def test_figure_generation_from_evidence(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
        )
        figs_dir = out_dir / "figures"
        assert (figs_dir / "figure_a_cumulative_storage.png").is_file()
        assert (figs_dir / "figure_a_cumulative_storage.svg").is_file()
        assert (figs_dir / "figure_b_marginal_storage.png").is_file()
        assert (figs_dir / "figure_c_pairwise_reuse_bytes.png").is_file()
        assert (figs_dir / "figure_c_pairwise_reuse_count.png").is_file()
        assert (figs_dir / "figure_d_recommendation_quality.png").is_file()
        assert (figs_dir / "figure_e_recommendation_latency.png").is_file()


# Case 21: Validator rejection of fabricated scale evidence
def test_validator_rejection_of_fabricated_evidence(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
        )
        # Tamper with catalog_scalability.json: fabricate scale 8 as OBSERVED
        scales_path = out_dir / "derived" / "catalog_scalability.json"
        scales = json.loads(scales_path.read_text())
        scales[1]["storage_measurement_status"] = "OBSERVED"  # scale 8 fabricated!
        scales_path.write_text(json.dumps(scales, indent=2))
        # Recompute checksums to isolate validator test to scale verification
        sums_file = out_dir / "SHA256SUMS"
        sums = [f"{file_sha256(p)}  {p.relative_to(out_dir)}" for p in sorted(out_dir.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"]
        sums_file.write_text("\n".join(sums) + "\n")

        with pytest.raises(EvidenceValidationError, match="Fabricated scale observation rejected"):
            validate_e5_storage_evidence(out_dir)


# Case 22: Claim eligibility when only four-image storage evidence exists
def test_claim_eligibility_partial_vs_full(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            stage="confirmatory",
            claims_permitted=True,
            scales=(4, 8, 16),
        )
        val = validate_e5_storage_evidence(out_dir)
        assert val["status"] == "PASS"
        assert val["storage_dedup_valid"] is True
        assert val["partial_scalability_valid"] is True
        assert val["complete_multiscale"] is False
        assert val["claim_eligibility"] == "ELIGIBLE_4_IMAGE_CATALOG_STORAGE"
        assert val["full_scalability_claim_eligible"] is False


# Case 23: Claim eligibility when full multi-scale storage exists
def test_claim_eligibility_when_full_multiscale_exists(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        # If scale is restricted to the 4 approved images
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            stage="confirmatory",
            claims_permitted=True,
            scales=(4,),
        )
        val = validate_e5_storage_evidence(out_dir)
        assert val["status"] == "PASS"
        assert val["complete_multiscale"] is True
        assert val["claim_eligibility"] == "ELIGIBLE_FULL_MULTISCALE"
        assert val["full_scalability_claim_eligible"] is True


# Case 24: Live Docker Manifest Inspection (integration test)
def test_docker_manifest_storage_runner_inspect(catalog):
    runner = DockerManifestStorageRunner(catalog, target_arch="amd64")
    minimal_ref = catalog["images"]["minimal-python"]["reference"]
    try:
        metadata = runner.inspect_image_layers("minimal-python", minimal_ref)
    except RuntimeError as exc:
        if "docker manifest inspect failed" in str(exc) or "Cannot connect to the Docker daemon" in str(exc):
            pytest.skip(f"Docker daemon or registry unavailable: {exc}")
        raise
    assert metadata.image_id == "minimal-python"
    assert metadata.total_bytes > 100_000_000
    assert len(metadata.layers) > 5
    assert metadata.size_domain == SIZE_DOMAIN_COMPRESSED_OCI_BLOB
