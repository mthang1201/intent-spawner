from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from evaluation_v4.dataset import file_sha256
from evaluation_v5 import (
    CandidateCatalogIdentity,
    ChecksumMismatchError,
    DatasetIdentity,
    EmbeddingIndexIdentity,
    EvidenceStatus,
    ExperimentId,
    ExtractorIdentity,
    ProtocolV5Manifest,
    SplitIdentity,
    SplitStage,
    adapt_operational_provenance,
    create_result_directory,
    load_manifest,
    validate_manifest,
    verify_manifest_checksums,
    write_manifest,
    write_provenance_json,
)


def _manifest(
    *,
    status: EvidenceStatus = EvidenceStatus.DRY_RUN,
    stage: SplitStage = SplitStage.DEVELOPMENT,
    dataset_sha256: str = "d" * 64,
    catalog_sha256: str = "c" * 64,
) -> ProtocolV5Manifest:
    return ProtocolV5Manifest(
        experiment_id=ExperimentId.E1,
        run_id="fixture-run-001",
        git_revision="a" * 40,
        execution_timestamp_utc="2026-08-22T01:02:03Z",
        dataset_identity=DatasetIdentity(
            dataset_id="fixture-dataset-v1",
            dataset_sha256=dataset_sha256,
        ),
        split_identity=SplitIdentity(split_id="development-v1", stage=stage),
        backend_system_versions={
            "P1": {"backend_version": "rule-based-v1"},
            "P2": {
                "backend_version": "p2-hybrid-v1.0.0",
                "pipeline_version": "p2-pipeline-v1.0.0",
            },
        },
        candidate_catalog=CandidateCatalogIdentity(
            catalog_version="catalog-v1",
            catalog_sha256=catalog_sha256,
            corpus_version="corpus-v1",
            corpus_sha256="b" * 64,
        ),
        structured_intent_schema_version="structured-intent-v1",
        extractor=ExtractorIdentity(
            extractor_name="fixture-extractor",
            extractor_version="fixture-extractor-v1",
            extractor_model_id="fixture-model-v1",
            extractor_prompt_version="fixture-prompt-v1",
            extractor_prompt_sha256="e" * 64,
        ),
        embedding_indexes=EmbeddingIndexIdentity(
            embedding_model_id="fixture-embedding",
            embedding_model_revision="fixture-embedding-v1",
            dense_index_version="dense-v1",
            dense_index_sha256="1" * 64,
            sparse_index_version="sparse-v1",
            sparse_index_sha256="2" * 64,
            hybrid_index_version="hybrid-v1",
            hybrid_index_sha256="3" * 64,
        ),
        retrieval_configuration={
            "top_k": 10,
            "rrf_k": 60.0,
            "sparse_weight": 1.0,
            "dense_weight": 1.0,
        },
        constraint_ranking_configuration={
            "constraint_evaluator_version": "constraint-v1",
            "constraint_policy_version": "policy-v1",
            "ranker_version": "ranker-v1",
        },
        p3_reranker_version=None,
        environment_identity={
            "environment_id": "fixture-local",
            "python_version": "3.fixture",
        },
        random_seeds=(),
        execution_status=status,
    )


def test_valid_manifest_round_trip_and_verified_write(tmp_path: Path):
    dataset = tmp_path / "dataset.yaml"
    catalog = tmp_path / "catalog.yaml"
    dataset.write_text("schema_version: fixture\n", encoding="utf-8")
    catalog.write_text("catalog_version: fixture\n", encoding="utf-8")
    manifest = _manifest(
        dataset_sha256=file_sha256(dataset),
        catalog_sha256=file_sha256(catalog),
    )

    assert ProtocolV5Manifest.from_dict(manifest.to_dict()) == manifest
    paths = create_result_directory(manifest, results_root=tmp_path / "results")
    write_manifest(
        paths,
        manifest,
        dataset_path=dataset,
        catalog_path=catalog,
    )

    loaded = load_manifest(
        paths.manifest,
        dataset_path=dataset,
        catalog_path=catalog,
    )
    assert loaded == manifest
    assert paths.root.parts[-3:] == (
        "protocol-v5.0.0",
        "E1",
        "fixture-run-001",
    )
    assert {path.name for path in paths.root.iterdir()} == {
        "manifest.json",
        "raw",
        "derived",
        "report",
    }


def test_observed_manifest_rejects_missing_required_provenance():
    manifest = _manifest(status=EvidenceStatus.OBSERVED)
    incomplete = replace(
        manifest,
        extractor=replace(manifest.extractor, extractor_model_id=None),
    )

    with pytest.raises(ValueError, match="OBSERVED|extractor_model_id"):
        validate_manifest(incomplete)


def test_invalid_execution_status_is_rejected():
    payload = _manifest().to_dict()
    payload["execution_status"] = "SYNTHETIC"

    with pytest.raises(ValueError, match="execution_status"):
        ProtocolV5Manifest.from_dict(payload)


def test_result_directories_are_immutable_by_default(tmp_path: Path):
    manifest = _manifest()
    first = create_result_directory(manifest, results_root=tmp_path)

    with pytest.raises(FileExistsError):
        create_result_directory(manifest, results_root=tmp_path)
    assert first.root.is_dir()


def test_checksum_mismatch_is_rejected(tmp_path: Path):
    dataset = tmp_path / "dataset.yaml"
    dataset.write_text("changed: true\n", encoding="utf-8")

    with pytest.raises(ChecksumMismatchError, match="dataset checksum mismatch"):
        verify_manifest_checksums(_manifest(), dataset_path=dataset)


def test_atomic_write_succeeds_and_failure_leaves_original_unchanged(tmp_path: Path):
    manifest = _manifest()
    paths = create_result_directory(manifest, results_root=tmp_path)
    target = write_provenance_json(
        paths,
        "raw/provenance.json",
        {"state": "complete"},
        manifest=manifest,
    )
    original = target.read_bytes()

    with pytest.raises(FileExistsError):
        write_provenance_json(
            paths,
            "raw/provenance.json",
            {"state": "would-overwrite"},
            manifest=manifest,
        )
    assert target.read_bytes() == original

    with pytest.raises(TypeError):
        write_provenance_json(
            paths,
            "raw/provenance.json",
            {"not_json": object()},
            manifest=manifest,
            development_override=True,
        )

    assert target.read_bytes() == original
    assert json.loads(target.read_text(encoding="utf-8")) == {"state": "complete"}
    assert not list(target.parent.glob(".provenance.json.*.tmp"))


def test_development_override_can_reuse_and_replace_dry_run_files(tmp_path: Path):
    manifest = _manifest()
    paths = create_result_directory(manifest, results_root=tmp_path)
    write_provenance_json(
        paths,
        "report/status.json",
        {"revision": 1},
        manifest=manifest,
    )

    reused = create_result_directory(
        manifest,
        results_root=tmp_path,
        development_override=True,
    )
    write_provenance_json(
        reused,
        "report/status.json",
        {"revision": 2},
        manifest=manifest,
        development_override=True,
    )

    assert json.loads((reused.report / "status.json").read_text()) == {"revision": 2}


@pytest.mark.parametrize(
    "manifest",
    [
        _manifest(stage=SplitStage.CONFIRMATORY),
        _manifest(status=EvidenceStatus.OBSERVED),
    ],
)
def test_development_override_rejects_confirmatory_and_observed(
    tmp_path: Path,
    manifest: ProtocolV5Manifest,
):
    with pytest.raises(PermissionError, match="prohibited"):
        create_result_directory(
            manifest,
            results_root=tmp_path,
            development_override=True,
        )


def test_operational_provenance_adapter_uses_supplied_snapshots_only():
    p2 = {
        "backend_version": "p2-backend-v1",
        "pipeline_version": "p2-pipeline-v1",
        "structured_intent_schema_version": "intent-v1",
        "extractor_name": "extractor",
        "extractor_version": "extractor-v1",
        "extractor_model_id": "model-v1",
        "extractor_prompt_version": "prompt-v1",
        "extractor_prompt_sha256": "4" * 64,
        "dense_embedding_model_id": "embedding",
        "dense_embedding_model_revision": "embedding-v1",
        "dense_index_version": "dense-v1",
        "dense_index_checksum": "5" * 64,
        "sparse_index_version": "sparse-v1",
        "sparse_index_checksum": "6" * 64,
        "hybrid_index_version": "hybrid-v1",
        "hybrid_index_checksum": "7" * 64,
        "hybrid_retriever_version": "retriever-v1",
        "hybrid_rrf": {"top_k": 10, "rrf_k": 60.0},
        "corpus_version": "corpus-v1",
        "corpus_checksum": "8" * 64,
        "catalog_version": "catalog-v1",
        "constraint_evaluator_version": "constraint-v1",
        "constraint_policy_version": "policy-v1",
        "ranker_version": "ranker-v1",
    }
    p3 = {
        "backend_version": "p3-backend-v1",
        "pipeline_version": "p3-pipeline-v1",
        "reranker_version": "reranker-v1",
        "frozen_p2_provenance": dict(p2),
    }

    adapted = adapt_operational_provenance(
        p2,
        candidate_catalog_sha256="9" * 64,
        p3_provenance=p3,
    )

    assert set(adapted["backend_system_versions"]) == {"P2", "P3"}
    assert adapted["candidate_catalog"].corpus_sha256 == "8" * 64
    assert adapted["embedding_indexes"].hybrid_index_sha256 == "7" * 64
    assert adapted["p3_reranker_version"] == "reranker-v1"
