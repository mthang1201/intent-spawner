"""Comprehensive test suite for Protocol-v5 E5 image storage scalability and catalog evaluation.

Covers all 23+ required experimental contracts, invariants, and edge cases.
"""

from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
import yaml

from evaluation_v5 import freeze as freeze_module
from evaluation_v5.analysis.research_contracts import (
    ResearchContractError,
    validate_storage_evidence,
)
from evaluation_v5.freeze import create_freeze_artifact
from evaluation_v5.isolation import (
    CONFIRMATORY_SPLIT_PROVENANCE_SCHEMA_VERSION,
    VerifiedConfirmatorySplit,
    load_confirmatory_split,
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
    DockerLocalStorageRunner,
    DockerManifestStorageRunner,
    DryRunStorageRunner,
    SyntheticStorageRunner,
    create_storage_runner,
)
from evaluation_v5.image_storage.validate_evidence import (
    EvidenceValidationError,
    _registry_manifest_from_raw,
    validate_e5_storage_evidence,
)
from evaluation_v5.split_dataset import (
    SPLIT_BUNDLE_SCHEMA_VERSION,
    LoadedSplit,
    SPLIT_BUNDLE_SCHEMA_VERSION_V2,
    SplitCase,
    SplitRole,
    load_development_split,
    split_bundle_checksum,
    validate_split_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "recommender" / "image-catalog.yaml"
FIXED_REVISION = "a" * 40


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


def _confirmatory_case() -> dict[str, object]:
    return {
        "case_id": "storage-sealed-case-001",
        "family_id": "storage-sealed-family-001",
        "variant_id": "canonical",
        "language": "en",
        "prompt": "Prepare a novel isolated Python workspace for a small text file.",
        "inputs": {"dataset_size_gb": 0.01, "code_context_hints": []},
        "gold": {
            "request_feasible": True,
            "preferred_candidate_id": "small-minimal-python",
            "acceptable_candidate_ids": ["small-minimal-python"],
            "required_image_capabilities": ["python"],
            "allowed_profiles": ["small", "medium", "large"],
            "gpu_allowed": False,
            "expected_extraction": None,
        },
        "source_provenance": {
            "source_dataset_id": "synthetic-storage-confirmatory-fixture",
            "source_schema_version": "synthetic-test-v1",
            "source_case_id": "storage-sealed-case-001",
            "source_split": "confirmatory",
            "evidence_classification": "synthetic_test_fixture_not_evidence",
        },
    }


def _confirmatory_document(
    *, dataset_id: str = "synthetic-storage-confirmatory-v1"
) -> dict[str, object]:
    case = _confirmatory_case()
    document: dict[str, object] = {
        "schema_version": SPLIT_BUNDLE_SCHEMA_VERSION,
        "split_manifest": {
            "dataset_id": dataset_id,
            "split_id": "v5-confirmatory",
            "role": "confirmatory",
            "family_ids": [case["family_id"]],
            "case_count": 1,
            "family_count": 1,
            "checksum": "0" * 64,
            "creation_metadata": {
                "created_at_utc": "2026-08-22T01:00:00Z",
                "created_by": "synthetic-test",
            },
            "freeze_metadata": {
                "frozen_at_utc": "2026-08-22T01:01:00Z",
                "frozen_by": "synthetic-test",
            },
        },
        "cases": [case],
    }
    document["split_manifest"]["checksum"] = split_bundle_checksum(document)  # type: ignore[index]
    return document


def _verified_gate_snapshot() -> dict[str, object]:
    development = load_development_split()

    def artifact(name: str) -> dict[str, str]:
        return {"path": name, "sha256": "d" * 64}

    def analysis(name: str) -> dict[str, object]:
        return {
            "path": name,
            "manifest_sha256": "c" * 64,
            "outputs": {"fixture": artifact("fixture.json")},
        }

    return {
        "snapshot_version": "protocol-v5-p3-gate-snapshot-v2.0.0",
        "status": "not_retained",
        "p3_active": False,
        "verification_status": "VERIFIED",
        "decision_schema_version": "protocol-v5-p3-development-decision-v1.0.0",
        "decision_artifact_path": "benchmarks_v5/synthetic-p3-decision.json",
        "decision_artifact_sha256": "b" * 64,
        "development_split": {
            "dataset_id": development.manifest.dataset_id,
            "split_id": development.manifest.split_id,
            "role": "development",
            "bundle_checksum": development.manifest.checksum,
            "dataset_sha256": development.source_file_sha256,
            "case_count": development.manifest.case_count,
            "family_count": development.manifest.family_count,
        },
        "raw_evidence": {
            "path": "results_v5/synthetic-raw",
            "run_id": "synthetic-test-run",
            "provenance_fingerprint": "a" * 64,
            "provenance_sha256": "b" * 64,
            "recommendations_sha256": "c" * 64,
            "completion_sha256": "d" * 64,
            "record_count": 1,
        },
        "component_evidence": analysis("results_v5/synthetic-component"),
        "statistical_evidence": analysis("results_v5/synthetic-statistical"),
        "predicate_version": "protocol-v5-p3-headroom-predicate-v1.0.0",
        "computation_version": "protocol-v5-p3-gate-computation-v1.0.0",
    }


def _authoritative_confirmatory_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[VerifiedConfirmatorySplit, Path, Path]:
    monkeypatch.delenv("PROTOCOL_V5_CONFIRMATORY_DATASET", raising=False)
    monkeypatch.setattr(freeze_module, "_git_state", lambda: (FIXED_REVISION, True))
    freeze_root = tmp_path / "freezes"
    monkeypatch.setattr(freeze_module, "DEFAULT_FREEZE_ROOT", freeze_root)
    monkeypatch.setattr(freeze_module, "FREEZE_CUSTODY_ROOT", tmp_path)
    snapshot = _verified_gate_snapshot()
    monkeypatch.setattr(
        freeze_module,
        "_verified_p3_gate_snapshot",
        lambda **_kwargs: copy.deepcopy(snapshot),
    )
    freeze_path = create_freeze_artifact(
        freeze_id="storage-confirmatory-fixture",
        p3_gate_status="not_retained",
        output_root=freeze_root,
    )
    dataset_path = tmp_path / "sealed-storage-confirmatory.yaml"
    dataset_path.write_text(
        yaml.safe_dump(
            _confirmatory_document(), sort_keys=False, allow_unicode=True
        ),
        encoding="utf-8",
    )
    return (
        load_confirmatory_split(dataset_path, freeze_path),
        dataset_path,
        freeze_path,
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


def test_constructed_loaded_split_cannot_authorize_confirmatory_catalog_scale(catalog):
    development = load_development_split()
    relabeled_manifest = replace(
        development.manifest,
        role=SplitRole.CONFIRMATORY,
    )
    constructed = LoadedSplit(
        bundle=replace(development.bundle, split_manifest=relabeled_manifest),
        source_file_sha256="f" * 64,
    )

    result = evaluate_catalog_scale_recommendation(
        base_catalog=catalog,
        scale_images=get_experimental_catalog_config(catalog).get_scale_images(4),
        stage="confirmatory",
        split_bundle=constructed,
    )

    assert result["status"] == "NOT_EXECUTED"
    assert result["image_acceptable_accuracy"] is None
    assert result["evaluated_cases"] == 0


def test_generic_confirmatory_loaded_split_cannot_authorize_catalog_scale(catalog):
    constructed = LoadedSplit(
        bundle=validate_split_bundle(_confirmatory_document()),
        source_file_sha256="a" * 64,
    )

    result = evaluate_catalog_scale_recommendation(
        base_catalog=catalog,
        scale_images=get_experimental_catalog_config(catalog).get_scale_images(4),
        stage="confirmatory",
        split_bundle=constructed,
    )

    assert result["status"] == "NOT_EXECUTED"
    assert result["confirmatory_provenance"] is None


def test_fake_confirmatory_dataset_id_and_sha_cannot_authorize_catalog_scale(catalog):
    bundle = validate_split_bundle(_confirmatory_document())
    fake_manifest = replace(bundle.split_manifest, dataset_id="fake-confirmatory")
    constructed = LoadedSplit(
        bundle=replace(bundle, split_manifest=fake_manifest),
        source_file_sha256="e" * 64,
    )

    result = evaluate_catalog_scale_recommendation(
        base_catalog=catalog,
        scale_images=get_experimental_catalog_config(catalog).get_scale_images(4),
        stage="confirmatory",
        split_bundle=constructed,
    )

    assert result["status"] == "NOT_EXECUTED"
    assert result["dataset_id"] == "none"


def test_copied_confirmatory_metadata_without_capability_is_rejected(
    catalog, tmp_path, monkeypatch
):
    capability, _, _ = _authoritative_confirmatory_split(tmp_path, monkeypatch)
    copied = LoadedSplit(
        bundle=capability.split.bundle,
        source_file_sha256=capability.split.source_file_sha256,
    )

    result = evaluate_catalog_scale_recommendation(
        base_catalog=catalog,
        scale_images=get_experimental_catalog_config(catalog).get_scale_images(4),
        stage="confirmatory",
        split_bundle=copied,
    )

    assert result["status"] == "NOT_EXECUTED"
    assert "capability_required" in result["reason"]


def test_mismatched_confirmatory_source_sha_fails_reverification(
    catalog, tmp_path, monkeypatch
):
    capability, _, _ = _authoritative_confirmatory_split(tmp_path, monkeypatch)
    object.__setattr__(
        capability,
        "_split",
        LoadedSplit(
            bundle=capability.split.bundle,
            source_file_sha256="d" * 64,
        ),
    )

    result = evaluate_catalog_scale_recommendation(
        base_catalog=catalog,
        scale_images=get_experimental_catalog_config(catalog).get_scale_images(4),
        stage="confirmatory",
        split_bundle=capability,
    )

    assert result["status"] == "NOT_EXECUTED"
    assert "reverification_failed" in result["reason"]


def test_stale_freeze_manifest_fails_catalog_scale_reverification(
    catalog, tmp_path, monkeypatch
):
    capability, _, freeze_path = _authoritative_confirmatory_split(
        tmp_path, monkeypatch
    )
    freeze_document = json.loads(freeze_path.read_text(encoding="utf-8"))
    freeze_document["freeze_id"] = "incorrect-freeze-id"
    freeze_path.write_text(json.dumps(freeze_document), encoding="utf-8")

    result = evaluate_catalog_scale_recommendation(
        base_catalog=catalog,
        scale_images=get_experimental_catalog_config(catalog).get_scale_images(4),
        stage="confirmatory",
        split_bundle=capability,
    )

    assert result["status"] == "NOT_EXECUTED"
    assert "reverification_failed" in result["reason"]


def test_authoritative_confirmatory_loader_path_succeeds(
    catalog, tmp_path, monkeypatch
):
    capability, dataset_path, freeze_path = _authoritative_confirmatory_split(
        tmp_path, monkeypatch
    )

    result = evaluate_catalog_scale_recommendation(
        base_catalog=catalog,
        scale_images=get_experimental_catalog_config(catalog).get_scale_images(4),
        stage="confirmatory",
        dataset_path=dataset_path,
        freeze_path=freeze_path,
    )

    assert result["status"] == "OBSERVED"
    assert result["split_role"] == "confirmatory"
    assert result["dataset_sha256"] == capability.split.source_file_sha256
    provenance = result["confirmatory_provenance"]
    assert provenance["schema_version"] == (
        CONFIRMATORY_SPLIT_PROVENANCE_SCHEMA_VERSION
    )
    assert provenance == capability.provenance_identity
    assert provenance["freeze_identity"]["freeze_manifest_sha256"]
    assert all(
        record["source_identity"]["confirmatory_provenance_sha256"]
        for record in result["case_records"]
    )


def test_validator_requires_live_capability_for_observed_confirmatory_recommendation(
    catalog, tmp_path, monkeypatch
):
    capability, dataset_path, freeze_path = _authoritative_confirmatory_split(
        tmp_path, monkeypatch
    )
    output = run_storage_evaluation(
        catalog_path=CATALOG_PATH,
        mode="synthetic",
        output_dir=tmp_path / "storage-output",
        stage="confirmatory",
        scales=(4,),
        dataset_path=dataset_path,
        freeze_path=freeze_path,
    )

    validated = validate_e5_storage_evidence(
        output, confirmatory_split=capability
    )
    assert validated["status"] == "PASS"
    with pytest.raises(
        EvidenceValidationError,
        match="CONFIRMATORY_RECOMMENDATION_REQUIRES_VERIFIED_SPLIT",
    ):
        validate_e5_storage_evidence(output)

    raw_path = output / "raw" / "catalog_scale_recommendations.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    forged = copy.deepcopy(
        raw["scale_evaluations"][0]["confirmatory_provenance"]
    )
    forged["freeze_identity"]["freeze_manifest_sha256"] = "f" * 64
    raw["scale_evaluations"][0]["confirmatory_provenance"] = forged
    raw_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    derived_path = output / "derived" / "catalog_scalability.json"
    derived = json.loads(derived_path.read_text(encoding="utf-8"))
    derived[0]["provenance"]["confirmatory_recommendation"] = forged
    derived_path.write_text(json.dumps(derived, indent=2), encoding="utf-8")
    _refresh_checksums(output)

    with pytest.raises(
        EvidenceValidationError,
        match="does not match the authoritative split/freeze capability",
    ):
        validate_e5_storage_evidence(output, confirmatory_split=capability)


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


def test_docker_manifest_storage_runner_single_arch_manifest(catalog):
    """Test DockerManifestStorageRunner handles single-arch manifests where Descriptor lacks platform."""
    approved_ref = catalog["images"]["minimal-python"]["reference"]
    test_digest = parse_image_digest(approved_ref)
    config_digest = "sha256:" + "c" * 64
    layer1_digest = "sha256:" + "a" * 64
    layer2_digest = "sha256:" + "b" * 64

    # Single manifest inspect response (no platform in Descriptor)
    mock_single_manifest = {
        "Ref": approved_ref,
        "Descriptor": {
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "digest": test_digest,
            "size": 1500,
        },
        "SchemaV2Manifest": {
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "config": {
                "mediaType": "application/vnd.docker.container.image.v1+json",
                "size": 3000,
                "digest": config_digest,
            },
            "layers": [
                {
                    "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                    "size": 100000,
                    "digest": layer1_digest,
                },
                {
                    "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                    "size": 200000,
                    "digest": layer2_digest,
                },
            ],
        },
    }

    runner = DockerManifestStorageRunner(catalog, target_arch="amd64", target_os="linux")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(mock_single_manifest),
            stderr="",
        )
        meta = runner.inspect_image_layers("minimal-python", approved_ref)

    assert meta.image_id == "minimal-python"
    assert meta.image_digest == test_digest
    assert meta.manifest_digest == test_digest
    assert meta.config_digest == config_digest
    assert meta.size_domain == SIZE_DOMAIN_COMPRESSED_OCI_BLOB
    assert meta.uncompressed_layer_bytes is None
    assert meta.total_bytes == 300000
    assert len(meta.layers) == 2
    assert meta.ordered_layer_digests == (layer1_digest, layer2_digest)
    assert meta.raw_observation_path == f"registry_manifests/{test_digest.removeprefix('sha256:')}.json"


def test_docker_manifest_storage_runner_multi_arch_manifest(catalog):
    """Test DockerManifestStorageRunner selects matching platform in multi-arch manifest list."""
    approved_ref = catalog["images"]["minimal-python"]["reference"]
    amd64_digest = parse_image_digest(approved_ref)
    arm64_digest = "sha256:" + "3" * 64
    config_amd64 = "sha256:" + "4" * 64
    config_arm64 = "sha256:" + "5" * 64

    mock_multi_manifest = [
        {
            "Descriptor": {
                "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                "digest": arm64_digest,
                "size": 1500,
                "platform": {"architecture": "arm64", "os": "linux"},
            },
            "SchemaV2Manifest": {
                "schemaVersion": 2,
                "config": {"digest": config_arm64},
                "layers": [{"size": 100, "digest": "sha256:" + "a" * 64}],
            },
        },
        {
            "Descriptor": {
                "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                "digest": amd64_digest,
                "size": 1600,
                "platform": {"architecture": "amd64", "os": "linux"},
            },
            "SchemaV2Manifest": {
                "schemaVersion": 2,
                "config": {"digest": config_amd64},
                "layers": [{"size": 200, "digest": "sha256:" + "b" * 64}],
            },
        },
    ]

    runner = DockerManifestStorageRunner(catalog, target_arch="amd64", target_os="linux")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(mock_multi_manifest),
            stderr="",
        )
        meta = runner.inspect_image_layers("minimal-python", approved_ref)

    assert meta.image_digest == amd64_digest
    assert meta.config_digest == config_amd64
    assert meta.total_bytes == 200


def test_registry_manifest_from_raw_single_arch():
    """Test _registry_manifest_from_raw with single-arch manifest lacking Descriptor.platform."""
    test_digest = "sha256:" + "7" * 64
    raw_payload = {
        "Descriptor": {
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "digest": test_digest,
        },
        "OCIManifest": {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": "sha256:" + "8" * 64},
            "layers": [{"digest": "sha256:" + "9" * 64, "size": 500}],
        },
    }
    raw_bytes = json.dumps(raw_payload).encode("utf-8")
    img_meta = {
        "image_id": "minimal-python",
        "platform": {"architecture": "amd64", "os": "linux"},
    }
    desc, manifest = _registry_manifest_from_raw(raw_bytes, image=img_meta)
    assert desc.get("digest") == test_digest
    assert manifest.get("config", {}).get("digest") == "sha256:" + "8" * 64


def test_docker_local_storage_runner_uncompressed_domain_and_unavailable_layers(catalog):
    """Test DockerLocalStorageRunner cleanly marks layer byte quantities unavailable without guessing."""
    approved_ref = catalog["images"]["minimal-python"]["reference"]
    test_digest = parse_image_digest(approved_ref)
    diff_id_1 = "sha256:" + "d" * 64
    diff_id_2 = "sha256:" + "e" * 64

    mock_inspect = [
        {
            "Id": "sha256:" + "c" * 64,
            "RepoDigests": [approved_ref],
            "Architecture": "amd64",
            "Os": "linux",
            "Size": 1500000000,
            "RootFS": {
                "Type": "layers",
                "Layers": [diff_id_1, diff_id_2],
            },
        }
    ]

    runner = DockerLocalStorageRunner(catalog, target_arch="amd64", target_os="linux")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(mock_inspect),
            stderr="",
        )
        meta = runner.inspect_image_layers("minimal-python", approved_ref)

    assert meta.collector_origin == StorageCollectorOrigin.CONTAINER_STORAGE_OBSERVATION.value
    assert meta.size_domain == SIZE_DOMAIN_UNCOMPRESSED
    assert meta.uncompressed_layer_bytes == 1500000000
    # Individual layer sizes unavailable from docker inspect; layers is empty, total_bytes is 0
    # (prohibiting guessing from Dockerfile inheritance)
    assert meta.layers == ()
    assert meta.total_bytes == 0
    assert meta.ordered_layer_digests == (diff_id_1, diff_id_2)
    assert meta.raw_observation_path == f"container_inspect/{test_digest.removeprefix('sha256:')}.json"
    assert runner.execution_status == StorageExecutionStatus.NOT_EXECUTED.value


def test_create_storage_runner_local_modes(catalog):
    """Test create_storage_runner routes 'local' and 'docker-local' to DockerLocalStorageRunner."""
    runner1 = create_storage_runner(catalog, mode="local")
    assert isinstance(runner1, DockerLocalStorageRunner)

    runner2 = create_storage_runner(catalog, mode="docker-local")
    assert isinstance(runner2, DockerLocalStorageRunner)


def test_storage_assert_size_domain_consistent_rejection(catalog):
    """Test that mixing compressed and uncompressed size domains raises SizeDomainMismatchError."""
    compressed_meta = ImageLayerMetadata(
        image_id="img-1",
        image_reference="img-1@sha256:" + "1" * 64,
        image_digest="sha256:" + "1" * 64,
        platform={"architecture": "amd64", "os": "linux"},
        layers=(LayerInspection(digest="sha256:" + "a" * 64, size=100),),
        total_bytes=100,
        size_domain=SIZE_DOMAIN_COMPRESSED_OCI_BLOB,
    )
    uncompressed_meta = ImageLayerMetadata(
        image_id="img-2",
        image_reference="img-2@sha256:" + "2" * 64,
        image_digest="sha256:" + "2" * 64,
        platform={"architecture": "amd64", "os": "linux"},
        layers=(LayerInspection(digest="sha256:" + "b" * 64, size=200),),
        total_bytes=200,
        size_domain=SIZE_DOMAIN_UNCOMPRESSED,
    )

    with pytest.raises(SizeDomainMismatchError, match="Cross-domain aggregation rejected"):
        assert_size_domain_consistent(compressed_meta.size_domain, uncompressed_meta.size_domain)

    with pytest.raises(SizeDomainMismatchError):
        compute_marginal_storage([compressed_meta, uncompressed_meta])

    with pytest.raises(SizeDomainMismatchError):
        compute_pairwise_layer_reuse([compressed_meta, uncompressed_meta])


def test_storage_orchestrator_run_id_differentiation(tmp_path, catalog):
    """Test that default run_id generation separates dry-run, synthetic, and observed packages."""
    with patch("evaluation_v5.image_storage.storage_orchestrator.DEFAULT_STORAGE_RESULTS_ROOT", tmp_path):
        out_dry = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="dry-run",
            stage="development",
            output_dir=None,
            run_id=None,
            scales=[4],
            eval_recommendation=False,
        )
        assert "e5-storage-scalability-dry-run-" in out_dry.name

        out_synth = run_storage_evaluation(
            catalog_path=CATALOG_PATH,
            mode="synthetic",
            stage="development",
            output_dir=None,
            run_id=None,
            scales=[4],
            eval_recommendation=False,
        )
        assert "e5-storage-scalability-synthetic-" in out_synth.name
