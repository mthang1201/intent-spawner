"""Comprehensive test suite for Protocol-v5 E5 image storage scalability and catalog evaluation.

Covers all 23+ required experimental contracts, invariants, and edge cases.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
import yaml

from evaluation_v5.analysis.research_contracts import (
    ResearchContractError,
    validate_storage_evidence,
)
from evaluation_v5.image_storage.contracts import file_sha256, parse_image_digest
from evaluation_v5.image_storage.recommendation_evaluator import (
    CatalogScaleGoldError,
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
    StorageCollectorOrigin,
    StorageEvidenceRecord,
    StorageExecutionStatus,
    assert_size_domain_consistent,
    check_immutable_catalog_gate,
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
from evaluation_v5.split_dataset import (
    SPLIT_BUNDLE_SCHEMA_VERSION_V2,
    SplitCase,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "recommender" / "image-catalog.yaml"


@pytest.fixture
def catalog() -> dict:
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _refresh_checksums(package: Path) -> None:
    records = [
        f"{file_sha256(path)}  {path.relative_to(package)}"
        for path in sorted(package.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    (package / "SHA256SUMS").write_text(
        "\n".join(records) + "\n", encoding="utf-8"
    )


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
    assert status == "NOT_EXECUTED"
    assert inspections[0].collector_origin == StorageCollectorOrigin.SYNTHETIC_TEST.value
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
            assert l["collector_origin"] == "SYNTHETIC_TEST"
            restored = ImageLayerMetadata.from_dict(l)
            assert restored.collector_origin == "SYNTHETIC_TEST"
        evidence = json.loads(
            (out_dir / "derived" / "storage_metrics.json").read_text()
        )
        restored_evidence = StorageEvidenceRecord.from_dict(evidence)
        assert restored_evidence.collector["origin"] == "SYNTHETIC_TEST"


# Case 18: Recommendation scale aggregation (accuracy, recall, latency)
def test_recommendation_scale_aggregation(catalog):
    exp_cfg = get_experimental_catalog_config(catalog, scales=(4,))
    scale_imgs = exp_cfg.get_scale_images(4)
    res = evaluate_catalog_scale_recommendation(catalog, scale_imgs, stage="development", k=5)
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


# Case 22: Synthetic storage is never eligible, even with four-image calculations
def test_claim_eligibility_partial_vs_full(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            stage="confirmatory",
            scales=(4, 8, 16),
        )
        val = validate_e5_storage_evidence(out_dir)
        assert val["status"] == "PASS"
        assert val["storage_dedup_valid"] is True
        assert val["partial_scalability_valid"] is True
        assert val["complete_multiscale"] is False
        assert val["claim_eligibility"] == "INELIGIBLE_UNAUTHENTICATED_COLLECTOR_ORIGIN"
        assert val["claims_permitted"] is False
        assert val["collector_authentic"] is False
        assert val["full_scalability_claim_eligible"] is False


# Case 23: Configuring only the available synthetic scale does not create evidence
def test_claim_eligibility_when_full_multiscale_exists(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        # If scale is restricted to the 4 approved images
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            stage="confirmatory",
            scales=(4,),
        )
        val = validate_e5_storage_evidence(out_dir)
        assert val["status"] == "PASS"
        assert val["complete_multiscale"] is False
        assert val["claim_eligibility"] == "INELIGIBLE_UNAUTHENTICATED_COLLECTOR_ORIGIN"
        assert val["full_scalability_claim_eligible"] is False


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
    assert metadata.collector_origin == "REAL_REGISTRY"
    assert metadata.raw_observation_path.startswith("registry_manifests/")
    assert len(metadata.raw_observation_sha256) == 64
    assert runner.raw_observations()[metadata.raw_observation_path]


# Case 25: --stage development selects development split
def test_stage_development_selects_development_split(catalog):
    config = get_experimental_catalog_config(catalog)
    res = evaluate_catalog_scale_recommendation(
        base_catalog=catalog,
        scale_images=config.get_scale_images(4),
        stage="development",
    )
    assert res["status"] == "OBSERVED"
    assert res["stage"] == "development"
    assert res["split_role"] == "development"
    assert res["dataset_id"] == "protocol-v5-development-2026-08-22"
    assert res["image_acceptable_accuracy"] is not None


# Case 26: --stage confirmatory requires external confirmatory split
def test_stage_confirmatory_requires_confirmatory_split(catalog):
    config = get_experimental_catalog_config(catalog)
    res = evaluate_catalog_scale_recommendation(
        base_catalog=catalog,
        scale_images=config.get_scale_images(4),
        stage="confirmatory",
    )
    assert res["status"] == "NOT_EXECUTED"
    assert "confirmatory_dataset_not_provided" in res["reason"]
    assert res["stage"] == "confirmatory"
    assert res["split_role"] == "none"
    assert res["dataset_id"] == "none"
    assert res["image_acceptable_accuracy"] is None


# Case 27: Confirmatory mode cannot silently fall back to development data
def test_confirmatory_cannot_silently_fallback(catalog):
    config = get_experimental_catalog_config(catalog)
    res = evaluate_catalog_scale_recommendation(
        base_catalog=catalog,
        scale_images=config.get_scale_images(4),
        stage="confirmatory",
        dataset_path=None,
    )
    assert res["dataset_id"] != "protocol-v5-development-2026-08-22"
    assert res["status"] == "NOT_EXECUTED"


# Case 28: Validator rejects confirmatory recommendation backed by development split
def test_validator_rejects_confirmatory_rec_backed_by_dev_split(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            stage="confirmatory",
            scales=(4,),
        )
        scalability_path = out_dir / "derived" / "catalog_scalability.json"
        data = json.loads(scalability_path.read_text(encoding="utf-8"))
        data[0]["p2_evaluation_status"] = "OBSERVED"
        data[0]["split_stage"] = "confirmatory"
        data[0]["evaluation_dataset_identity"] = "protocol-v5-development-2026-08-22"
        data[0]["dataset_sha256"] = "e3fff5167ef2194fb365fec7510d1efc2b17a18b13182063c2b63c26f021d3cd"
        scalability_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        sums_file = out_dir / "SHA256SUMS"
        records = []
        for p in sorted(out_dir.rglob("*")):
            if p.is_file() and p.name != "SHA256SUMS":
                records.append(f"{file_sha256(p)}  {p.relative_to(out_dir)}")
        sums_file.write_text("\n".join(records) + "\n", encoding="utf-8")

        with pytest.raises(EvidenceValidationError, match="CONFIRMATORY_RECOMMENDATION_USES_DEVELOPMENT_SPLIT"):
            validate_e5_storage_evidence(out_dir)


# Case 29: Dataset identity and hash are persisted
def test_dataset_identity_and_hash_persisted(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            stage="development",
            claims_permitted=False,
            scales=(4,),
        )
        data = json.loads((out_dir / "derived" / "catalog_scalability.json").read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["evaluation_dataset_identity"] == "protocol-v5-development-2026-08-22"
        assert len(data[0]["dataset_sha256"]) == 64
        assert data[0]["split_stage"] == "development"
        assert data[0]["split_role"] == "development"


# Case 30: Digest-pinned requested reference passes immutable gate
def test_digest_pinned_requested_reference_passes_immutable_gate():
    meta = [
        ImageLayerMetadata(
            image_id="img1",
            image_reference="quay.io/repo@sha256:1111111111111111111111111111111111111111111111111111111111111111",
            image_digest="sha256:1111111111111111111111111111111111111111111111111111111111111111",
            platform={"os": "linux", "architecture": "amd64"},
            layers=(LayerInspection(digest="sha256:aaa", size=100),),
            total_bytes=100,
            is_digest_pinned=True,
            requested_reference="quay.io/repo@sha256:1111111111111111111111111111111111111111111111111111111111111111",
        )
    ]
    gate_res = check_immutable_catalog_gate(meta, stage="confirmatory")
    assert gate_res.is_immutable is True
    assert len(gate_res.violating_images) == 0


# Case 31: Mutable tag requested reference fails confirmatory immutable-input gate
def test_mutable_tag_requested_reference_fails_confirmatory_gate():
    meta = [
        ImageLayerMetadata(
            image_id="img-mutable",
            image_reference="quay.io/repo:latest",
            image_digest="sha256:2222222222222222222222222222222222222222222222222222222222222222",
            platform={"os": "linux", "architecture": "amd64"},
            layers=(LayerInspection(digest="sha256:bbb", size=100),),
            total_bytes=100,
            is_digest_pinned=False,
            requested_reference="quay.io/repo:latest",
        )
    ]
    gate_res = check_immutable_catalog_gate(meta, stage="confirmatory")
    assert gate_res.is_immutable is False
    assert len(gate_res.violating_images) == 1
    assert gate_res.violating_images[0]["image_id"] == "img-mutable"
    assert "FAILED" in gate_res.details


# Case 32: Resolved digest alone does NOT make mutable reference immutable
def test_resolved_digest_alone_does_not_make_reference_immutable():
    meta = [
        ImageLayerMetadata(
            image_id="img-resolved-only",
            image_reference="docker.io/library/ubuntu:latest",
            image_digest="sha256:3333333333333333333333333333333333333333333333333333333333333333",
            platform={"os": "linux", "architecture": "amd64"},
            layers=(LayerInspection(digest="sha256:ccc", size=200),),
            total_bytes=200,
            is_digest_pinned=False,
            requested_reference="docker.io/library/ubuntu:latest",
            resolved_digest="sha256:3333333333333333333333333333333333333333333333333333333333333333",
        )
    ]
    gate_res = check_immutable_catalog_gate(meta, stage="confirmatory")
    assert gate_res.is_immutable is False
    assert len(gate_res.violating_images) == 1


# Case 33: Mixed immutable/mutable catalog fails confirmatory gate
def test_mixed_immutable_mutable_catalog_fails_confirmatory_gate():
    meta = [
        ImageLayerMetadata(
            image_id="img-pinned",
            image_reference="quay.io/repo@sha256:aaaa",
            image_digest="sha256:aaaa",
            platform={"os": "linux", "architecture": "amd64"},
            layers=(),
            total_bytes=0,
            is_digest_pinned=True,
            requested_reference="quay.io/repo@sha256:aaaa",
        ),
        ImageLayerMetadata(
            image_id="img-unpinned",
            image_reference="quay.io/repo:v1.0",
            image_digest="sha256:bbbb",
            platform={"os": "linux", "architecture": "amd64"},
            layers=(),
            total_bytes=0,
            is_digest_pinned=False,
            requested_reference="quay.io/repo:v1.0",
        ),
    ]
    gate_res = check_immutable_catalog_gate(meta, stage="confirmatory")
    assert gate_res.is_immutable is False
    assert len(gate_res.violating_images) == 1
    assert gate_res.violating_images[0]["image_id"] == "img-unpinned"


# Case 34: Development runs may inspect mutable inputs with warning
def test_development_runs_may_inspect_mutable_inputs_with_warning():
    meta = [
        ImageLayerMetadata(
            image_id="img-dev",
            image_reference="quay.io/repo:dev",
            image_digest="sha256:cccc",
            platform={"os": "linux", "architecture": "amd64"},
            layers=(),
            total_bytes=0,
            is_digest_pinned=False,
            requested_reference="quay.io/repo:dev",
        )
    ]
    gate_res = check_immutable_catalog_gate(meta, stage="development")
    assert gate_res.is_immutable is False
    assert "Development catalog" in gate_res.details


# Case 35: Split validity cannot promote a synthetic storage collector
def test_claim_eligibility_changes_based_on_split_validity(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            stage="confirmatory",
            scales=(4, 8, 16),
        )
        val = validate_e5_storage_evidence(out_dir)
        assert val["claim_eligibility"] == "INELIGIBLE_UNAUTHENTICATED_COLLECTOR_ORIGIN"
        assert val["recommendation_split_valid"] is True


# Case 36: Report claim eligibility changes based on immutable-input validity
def test_claim_eligibility_changes_based_on_immutable_input_validity(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            stage="confirmatory",
            scales=(4, 8, 16),
        )
        layers_path = out_dir / "raw" / "image_layers.json"
        data = json.loads(layers_path.read_text(encoding="utf-8"))
        data[0]["requested_reference"] = "quay.io/jupyter/minimal-notebook:latest"
        data[0]["is_digest_pinned"] = False
        layers_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        sums_file = out_dir / "SHA256SUMS"
        records = []
        for p in sorted(out_dir.rglob("*")):
            if p.is_file() and p.name != "SHA256SUMS":
                records.append(f"{file_sha256(p)}  {p.relative_to(out_dir)}")
        sums_file.write_text("\n".join(records) + "\n", encoding="utf-8")

        with pytest.raises(EvidenceValidationError, match="MUTABLE_INPUT_REFERENCE_IN_CONFIRMATORY_CATALOG"):
            validate_e5_storage_evidence(out_dir)


# Case 37: Single image with all unique layer digests (logical == unique, savings == 0)
def test_single_image_with_all_unique_layer_digests():
    digest_a = "sha256:" + "1" * 64
    ref_a = f"quay.io/test@{digest_a}"
    meta = ImageLayerMetadata(
        image_id="img-unique",
        image_reference=ref_a,
        image_digest=digest_a,
        platform={"os": "linux", "architecture": "amd64"},
        layers=(
            LayerInspection(digest="sha256:l1", size=100),
            LayerInspection(digest="sha256:l2", size=200),
            LayerInspection(digest="sha256:l3", size=300),
        ),
        total_bytes=600,
        is_digest_pinned=True,
    )
    assert meta.within_image_duplicate_digest_count == 0
    assert meta.within_image_duplicate_bytes == 0
    assert meta.unique_layer_count == 3
    assert meta.unique_layer_bytes == 600

    catalog_dict = {"images": {"img-unique": {"reference": ref_a}}}
    runner = SyntheticStorageRunner(
        catalog=catalog_dict,
        injected_image_layers={"img-unique": [{"digest": "sha256:l1", "size": 100}, {"digest": "sha256:l2", "size": 200}, {"digest": "sha256:l3", "size": 300}]},
    )
    inspections, prefixes, status = runner.measure_all([("img-unique", ref_a, digest_a)])
    p1 = prefixes[0]
    assert p1.naive_logical_bytes == 600
    assert p1.unique_layer_bytes == 600
    assert p1.savings_bytes == 0
    assert p1.within_image_duplicate_bytes == 0


# Case 38: Single image containing a repeated digest (difference exactly explained)
def test_single_image_with_repeated_layer_digest():
    digest_b = "sha256:" + "2" * 64
    ref_b = f"quay.io/test@{digest_b}"
    meta = ImageLayerMetadata(
        image_id="img-dup",
        image_reference=ref_b,
        image_digest=digest_b,
        platform={"os": "linux", "architecture": "amd64"},
        layers=(
            LayerInspection(digest="sha256:empty", size=32),
            LayerInspection(digest="sha256:data1", size=100),
            LayerInspection(digest="sha256:empty", size=32),
            LayerInspection(digest="sha256:data2", size=200),
            LayerInspection(digest="sha256:empty", size=32),
        ),
        total_bytes=396,
        is_digest_pinned=True,
    )
    # 3 occurrences of 32 B => (3 - 1) * 32 = 64 duplicate bytes
    assert meta.within_image_duplicate_digest_count == 1
    assert meta.within_image_duplicate_bytes == 64
    assert meta.unique_layer_count == 3
    assert meta.unique_layer_bytes == 332
    assert meta.total_bytes - meta.unique_layer_bytes == 64

    catalog_dict = {"images": {"img-dup": {"reference": ref_b}}}
    runner = SyntheticStorageRunner(
        catalog=catalog_dict,
        injected_image_layers={"img-dup": [
            {"digest": "sha256:empty", "size": 32},
            {"digest": "sha256:data1", "size": 100},
            {"digest": "sha256:empty", "size": 32},
            {"digest": "sha256:data2", "size": 200},
            {"digest": "sha256:empty", "size": 32},
        ]},
    )
    inspections, prefixes, status = runner.measure_all([("img-dup", ref_b, digest_b)])
    p1 = prefixes[0]
    assert p1.naive_logical_bytes == 396
    assert p1.unique_layer_bytes == 332
    assert p1.savings_bytes == 64
    assert p1.within_image_duplicate_bytes == 64


# Case 39: Pairwise matrix with duplicate descriptors follows unique digest semantics
def test_pairwise_matrix_with_duplicate_descriptors():
    meta_a = ImageLayerMetadata(
        image_id="img-a",
        image_reference="quay.io/test/a@sha256:aaaa",
        image_digest="sha256:aaaa",
        platform={"os": "linux", "architecture": "amd64"},
        layers=(
            LayerInspection(digest="sha256:dup_shared", size=50),
            LayerInspection(digest="sha256:dup_shared", size=50),
            LayerInspection(digest="sha256:uniq_a", size=100),
        ),
        total_bytes=200,
        is_digest_pinned=True,
    )
    meta_b = ImageLayerMetadata(
        image_id="img-b",
        image_reference="quay.io/test/b@sha256:bbbb",
        image_digest="sha256:bbbb",
        platform={"os": "linux", "architecture": "amd64"},
        layers=(
            LayerInspection(digest="sha256:dup_shared", size=50),
            LayerInspection(digest="sha256:dup_shared", size=50),
            LayerInspection(digest="sha256:dup_shared", size=50),
            LayerInspection(digest="sha256:uniq_b", size=300),
        ),
        total_bytes=450,
        is_digest_pinned=True,
    )

    analysis = compute_pairwise_layer_reuse([meta_a, meta_b])
    # Unique shared layer count: exactly 1 (sha256:dup_shared)
    assert analysis.shared_layer_count_matrix[0][1] == 1
    assert analysis.shared_layer_count_matrix[1][0] == 1
    # Unique shared layer bytes: exactly 50 (not 100 or 150)
    assert analysis.shared_layer_byte_matrix[0][1] == 50
    assert analysis.shared_layer_byte_matrix[1][0] == 50
    # Diagonal uses unique layer counts and unique layer bytes
    assert analysis.shared_layer_count_matrix[0][0] == 2  # dup_shared and uniq_a
    assert analysis.shared_layer_byte_matrix[0][0] == 150  # 50 + 100
    assert analysis.shared_layer_count_matrix[1][1] == 2  # dup_shared and uniq_b
    assert analysis.shared_layer_byte_matrix[1][1] == 350  # 50 + 300
    assert analysis.symmetry_verified is True


# Case 40: NOT_EXECUTED P2 record cannot contain accuracy metrics
def test_not_executed_p2_record_cannot_contain_accuracy_metrics(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            stage="confirmatory",
            scales=(4, 8, 16),
        )
        scal_path = out_dir / "derived" / "catalog_scalability.json"
        scales = json.loads(scal_path.read_text(encoding="utf-8"))
        # Tamper scale 8 (NOT_EXECUTED) to carry accuracy
        scales[1]["p2_image_acceptable_accuracy"] = 0.95
        scal_path.write_text(json.dumps(scales, indent=2), encoding="utf-8")
        sums_file = out_dir / "SHA256SUMS"
        records = [f"{file_sha256(p)}  {p.relative_to(out_dir)}" for p in sorted(out_dir.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"]
        sums_file.write_text("\n".join(records) + "\n", encoding="utf-8")

        with pytest.raises(EvidenceValidationError, match="NOT_EXECUTED_RECORD_CONTAINS_OBSERVED_METRICS"):
            validate_e5_storage_evidence(out_dir)


# Case 41: NOT_EXECUTED P2 record cannot contain latency metrics
def test_not_executed_p2_record_cannot_contain_latency_metrics(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            stage="confirmatory",
            scales=(4, 8, 16),
        )
        scal_path = out_dir / "derived" / "catalog_scalability.json"
        scales = json.loads(scal_path.read_text(encoding="utf-8"))
        # Tamper scale 8 (NOT_EXECUTED) to carry latency
        scales[1]["p2_latency_mean_seconds"] = 0.005
        scal_path.write_text(json.dumps(scales, indent=2), encoding="utf-8")
        sums_file = out_dir / "SHA256SUMS"
        records = [f"{file_sha256(p)}  {p.relative_to(out_dir)}" for p in sorted(out_dir.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"]
        sums_file.write_text("\n".join(records) + "\n", encoding="utf-8")

        with pytest.raises(EvidenceValidationError, match="NOT_EXECUTED_RECORD_CONTAINS_OBSERVED_METRICS"):
            validate_e5_storage_evidence(out_dir)


# Case 42: Figure D with zero confirmatory recommendation observations
def test_figure_d_with_zero_confirmatory_recommendation_observations():
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp)
        scale_records = [
            ScaleLevelEvaluationRecord.from_dict({
                "catalog_size": 4,
                "catalog_id": "scale-4",
                "catalog_hash": "hash4",
                "ordered_immutable_image_references": (),
                "all_references_digest_pinned": True,
                "p2_evaluation_status": "NOT_EXECUTED",
                "split_stage": "confirmatory",
                "split_role": "none",
                "status_reason": "confirmatory_dataset_not_provided",
            }),
            ScaleLevelEvaluationRecord.from_dict({
                "catalog_size": 8,
                "catalog_id": "scale-8",
                "catalog_hash": "hash8",
                "ordered_immutable_image_references": (),
                "all_references_digest_pinned": True,
                "p2_evaluation_status": "NOT_EXECUTED",
                "split_stage": "confirmatory",
                "split_role": "none",
                "status_reason": "insufficient_approved_images",
            }),
        ]
        figs = generate_all_figures(
            prefixes=(),
            marginal_records=(),
            pairwise_analysis=PairwiseReuseAnalysis(
                image_ids=(),
                image_digests=(),
                shared_layer_count_matrix=(),
                shared_layer_byte_matrix=(),
                jaccard_byte_matrix=(),
                pairwise_records=(),
            ),
            scale_records=scale_records,
            output_dir=out_path,
        )
        assert (out_path / "figure_d_recommendation_quality.png").is_file()


# Case 43: Figure E with zero confirmatory recommendation observations
def test_figure_e_with_zero_confirmatory_recommendation_observations():
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp)
        scale_records = [
            ScaleLevelEvaluationRecord.from_dict({
                "catalog_size": 4,
                "catalog_id": "scale-4",
                "catalog_hash": "hash4",
                "ordered_immutable_image_references": (),
                "all_references_digest_pinned": True,
                "p2_evaluation_status": "NOT_EXECUTED",
                "split_stage": "confirmatory",
                "split_role": "none",
                "status_reason": "confirmatory_dataset_not_provided",
            }),
        ]
        figs = generate_all_figures(
            prefixes=(),
            marginal_records=(),
            pairwise_analysis=PairwiseReuseAnalysis(
                image_ids=(),
                image_digests=(),
                shared_layer_count_matrix=(),
                shared_layer_byte_matrix=(),
                jaccard_byte_matrix=(),
                pairwise_records=(),
            ),
            scale_records=scale_records,
            output_dir=out_path,
        )
        assert (out_path / "figure_e_recommendation_latency.png").is_file()


# Case 44: Development recommendation observation is visually distinct from confirmatory
def test_development_recommendation_observation_distinct_from_confirmatory():
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp)
        scale_records = [
            ScaleLevelEvaluationRecord.from_dict({
                "catalog_size": 4,
                "catalog_id": "scale-4",
                "catalog_hash": "hash4",
                "ordered_immutable_image_references": (),
                "all_references_digest_pinned": True,
                "p2_evaluation_status": "OBSERVED",
                "p2_image_acceptable_accuracy": 0.92,
                "p2_image_preferred_accuracy": 0.92,
                "p2_retrieval_recall_at_k": 0.71,
                "p2_latency_mean_seconds": 0.0025,
                "p2_latency_p95_seconds": 0.004,
                "p2_latency_min_seconds": 0.001,
                "p2_latency_max_seconds": 0.006,
                "split_stage": "development",
                "split_role": "development",
            }),
            ScaleLevelEvaluationRecord.from_dict({
                "catalog_size": 8,
                "catalog_id": "scale-8",
                "catalog_hash": "hash8",
                "ordered_immutable_image_references": (),
                "all_references_digest_pinned": True,
                "p2_evaluation_status": "NOT_EXECUTED",
                "split_stage": "confirmatory",
                "split_role": "none",
            }),
        ]
        figs = generate_all_figures(
            prefixes=(),
            marginal_records=(),
            pairwise_analysis=PairwiseReuseAnalysis(
                image_ids=(),
                image_digests=(),
                shared_layer_count_matrix=(),
                shared_layer_byte_matrix=(),
                jaccard_byte_matrix=(),
                pairwise_records=(),
            ),
            scale_records=scale_records,
            output_dir=out_path,
        )
        assert (out_path / "figure_d_recommendation_quality.png").is_file()
        assert (out_path / "figure_e_recommendation_latency.png").is_file()


# Case 45: Figure generation consumes only supplied records without reading stale run state
def test_figure_generation_consumes_only_supplied_records():
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp)
        scale_records = [
            ScaleLevelEvaluationRecord.from_dict({
                "catalog_size": 4,
                "catalog_id": "scale-4",
                "catalog_hash": "hash4",
                "ordered_immutable_image_references": (),
                "all_references_digest_pinned": True,
                "p2_evaluation_status": "NOT_EXECUTED",
                "split_stage": "confirmatory",
                "split_role": "none",
            }),
        ]
        figs = generate_all_figures(
            prefixes=(),
            marginal_records=(),
            pairwise_analysis=PairwiseReuseAnalysis(
                image_ids=(),
                image_digests=(),
                shared_layer_count_matrix=(),
                shared_layer_byte_matrix=(),
                jaccard_byte_matrix=(),
                pairwise_records=(),
            ),
            scale_records=scale_records,
            output_dir=out_path,
        )
        assert "figure_d" in figs
        assert "figure_e" in figs


# Case 46: Report does not call deterministic storage measurement statistically confirmed
def test_report_does_not_call_deterministic_storage_statistically_confirmed(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            stage="confirmatory",
            scales=(4, 8, 16),
        )
        report_text = (out_dir / "report" / "E5_IMAGE_STORAGE_REPORT.md").read_text(encoding="utf-8")
        assert "statistically confirmed" not in report_text.lower()
        assert "empirically confirms" not in report_text.lower()
        assert "non-observed / not executed" in report_text.lower()
        assert "compressed_oci_manifest_layer_bytes" in report_text


def test_synthetic_confirmatory_cannot_be_promoted_by_caller_flags(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError, match="cannot be overridden"):
            run_storage_evaluation(
                catalog_path=CATALOG_PATH,
                mode="synthetic",
                output_dir=Path(tmp) / "rejected",
                stage="confirmatory",
                claims_permitted=True,
                eval_recommendation=False,
                scales=(4,),
            )

        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "accepted",
            stage="confirmatory",
            eval_recommendation=False,
            scales=(4,),
        )
        evidence = json.loads(
            (out_dir / "derived" / "storage_metrics.json").read_text(
                encoding="utf-8"
            )
        )
        assert evidence["execution_status"] == "NOT_EXECUTED"
        assert evidence["claims_permitted"] is False
        assert evidence["collector"]["origin"] == "SYNTHETIC_TEST"
        validation = validate_e5_storage_evidence(out_dir)
        assert validation["eligible_as_current_e5_evidence"] is False


def test_validator_rejects_runtime_label_spoofing_collector_origin(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            stage="confirmatory",
            eval_recommendation=False,
            scales=(4,),
        )
        metrics_path = out_dir / "derived" / "storage_metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        metrics["platform"]["runtime"] = "docker"
        metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        env_path = out_dir / "raw" / "environment.json"
        environment = json.loads(env_path.read_text(encoding="utf-8"))
        environment["runtime"] = "docker"
        env_path.write_text(json.dumps(environment, indent=2), encoding="utf-8")
        _refresh_checksums(out_dir)

        with pytest.raises(EvidenceValidationError, match="runtime label disagrees"):
            validate_e5_storage_evidence(out_dir)


def test_validator_rejects_forged_real_origin_without_raw_registry_evidence(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            stage="confirmatory",
            eval_recommendation=False,
            scales=(4,),
        )
        metrics_path = out_dir / "derived" / "storage_metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        collector = metrics["collector"]
        collector.update(
            {
                "origin": "REAL_REGISTRY",
                "collector_name": "docker-manifest-inspect",
                "evidence_classification": "REAL_OBSERVATION",
                "raw_observation_count": 4,
            }
        )
        metrics["execution_status"] = "OBSERVED"
        metrics["claims_permitted"] = True
        metrics["platform"]["runtime"] = "docker"
        metrics["provenance"]["storage_collector_origin"] = "REAL_REGISTRY"
        metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

        env_path = out_dir / "raw" / "environment.json"
        environment = json.loads(env_path.read_text(encoding="utf-8"))
        environment["runtime"] = "docker"
        environment["storage_collector"] = collector
        env_path.write_text(json.dumps(environment, indent=2), encoding="utf-8")

        layers_path = out_dir / "raw" / "image_layers.json"
        layers = json.loads(layers_path.read_text(encoding="utf-8"))
        for image in layers:
            image["collector_origin"] = "REAL_REGISTRY"
            image["collector_name"] = "docker-manifest-inspect"
        layers_path.write_text(json.dumps(layers, indent=2), encoding="utf-8")

        manifest_path = out_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["execution_status"] = "OBSERVED"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        status_path = out_dir / "report" / "status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["status"] = "OBSERVED"
        status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
        _refresh_checksums(out_dir)

        with pytest.raises(EvidenceValidationError, match="lacks raw_observation_path"):
            validate_e5_storage_evidence(out_dir)


def test_validator_rejects_fabricated_layer_digest_and_size(catalog):
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            output_dir=Path(tmp) / "out",
            stage="development",
            eval_recommendation=False,
            scales=(4,),
        )
        layers_path = out_dir / "raw" / "image_layers.json"
        layers = json.loads(layers_path.read_text(encoding="utf-8"))
        fabricated_digest = "sha256:" + "f" * 64
        layers[0]["layers"][0]["digest"] = fabricated_digest
        layers[0]["ordered_layer_digests"][0] = fabricated_digest
        layers[0]["layers"][0]["size"] += 123
        layers[0]["layer_sizes"][0] += 123
        layers[0]["total_bytes"] += 123
        layers_path.write_text(json.dumps(layers, indent=2), encoding="utf-8")
        _refresh_checksums(out_dir)

        with pytest.raises(EvidenceValidationError, match="not recomputable from raw"):
            validate_e5_storage_evidence(out_dir)


def _canonical_v2_scipy_case(case_id: str, variant_id: str) -> SplitCase:
    return SplitCase(
        case_id=case_id,
        family_id="canonical-v2-scipy-family",
        variant_id=variant_id,
        language="en",
        prompt="Clean and transform a medium CSV using pandas.",
        inputs={
            "dataset_size_gb": 0.8,
            "code_context_hints": ["import pandas as pd"],
        },
        gold={
            "gold_structured_intent": {},
            "candidate_gold": {
                "acceptable_candidate_ids": ["medium-scipy-data-science"],
                "preferred_candidate_ids": ["medium-scipy-data-science"],
            },
            "profile_gold": {
                "acceptable_profile_ids": ["medium"],
                "preferred_profile_ids": ["medium"],
            },
            "image_gold": {
                "acceptable_image_ids": ["scipy-data-science"],
                "preferred_image_ids": ["scipy-data-science"],
                "required_capabilities": ["pandas"],
            },
            "policy_gold": {
                "expected_feasibility": "feasible",
                "required_constraints": [],
                "explicitly_unsupported_requirements": [],
            },
        },
        source_provenance={
            "source_case_id": case_id,
            "source_dataset_id": "canonical-v2-test-fixture",
        },
    )


def _canonical_v2_split(*cases: SplitCase) -> SimpleNamespace:
    return SimpleNamespace(
        bundle=SimpleNamespace(
            schema_version=SPLIT_BUNDLE_SCHEMA_VERSION_V2,
            cases=cases,
            split_manifest=SimpleNamespace(
                dataset_id="canonical-v2-test-fixture",
                role=SimpleNamespace(value="development"),
            ),
        ),
        source_file_sha256="a" * 64,
    )


def test_catalog_scale_scores_canonical_v2_one_case_scipy_fixture(catalog):
    case = _canonical_v2_scipy_case("canonical-v2-scipy", "canonical")
    result = evaluate_catalog_scale_recommendation(
        base_catalog=catalog,
        scale_images=get_experimental_catalog_config(catalog).get_scale_images(4),
        stage="development",
        split_bundle=_canonical_v2_split(case),
    )

    assert result["status"] == "OBSERVED"
    assert result["image_acceptable_accuracy"] == 1.0
    assert result["image_preferred_accuracy"] == 1.0
    assert result["retrieval_recall_at_k"] == 1.0
    assert len(result["case_records"]) == 1
    assert all(row["gold_schema"] == "canonical_v2" for row in result["case_records"])
    assert len(result["family_estimates"]) == 1
    assert result["family_estimates"][0]["variant_count"] == 1
    summary = result["family_summary"]["metrics"]["image_acceptable"]
    assert summary["aggregation_unit"] == "workload_family"
    assert summary["effective_family_n"] == 1
    assert "INSUFFICIENT_EFFECTIVE_FAMILY_N" in summary["warning_codes"]


def test_catalog_scale_collapses_variants_to_workload_family(catalog):
    cases = (
        _canonical_v2_scipy_case("canonical-v2-scipy", "canonical"),
        _canonical_v2_scipy_case("canonical-v2-scipy-paraphrase", "paraphrase"),
    )
    result = evaluate_catalog_scale_recommendation(
        base_catalog=catalog,
        scale_images=get_experimental_catalog_config(catalog).get_scale_images(4),
        stage="development",
        split_bundle=_canonical_v2_split(*cases),
    )

    assert len(result["case_records"]) == 2
    assert len(result["family_estimates"]) == 1
    assert result["family_estimates"][0]["variant_count"] == 2
    assert result["family_summary"]["metrics"]["image_acceptable"][
        "family_count"
    ] == 1


def test_catalog_scale_rejects_missing_canonical_v2_acceptable_gold(catalog):
    case = _canonical_v2_scipy_case("canonical-v2-invalid", "canonical")
    malformed_gold = dict(case.gold)
    malformed_gold["image_gold"] = {
        "preferred_image_ids": ["scipy-data-science"],
        "required_capabilities": ["pandas"],
    }
    malformed = SplitCase(
        case_id=case.case_id,
        family_id=case.family_id,
        variant_id=case.variant_id,
        language=case.language,
        prompt=case.prompt,
        inputs=case.inputs,
        gold=malformed_gold,
        source_provenance=case.source_provenance,
    )

    with pytest.raises(CatalogScaleGoldError, match="acceptable_image_ids"):
        evaluate_catalog_scale_recommendation(
            base_catalog=catalog,
            scale_images=get_experimental_catalog_config(catalog).get_scale_images(4),
            stage="development",
            split_bundle=_canonical_v2_split(malformed),
        )
