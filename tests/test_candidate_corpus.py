"""Focused unit tests for the administrator-owned EnvironmentCandidate corpus."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from unittest.mock import patch

import pytest

from recommender.candidate_corpus import (
    CANDIDATE_CORPUS_SCHEMA_VERSION,
    CANDIDATE_DOCUMENT_SCHEMA_VERSION,
    DEFAULT_CORPUS_VERSION,
    DEFAULT_PROFILE_DEFINITIONS,
    CandidateCorpus,
    CandidateDocument,
    CandidateResourceMetadata,
    build_candidate_corpus,
    canonical_json_checksum,
    generate_candidate_retrieval_text,
    load_candidate_corpus,
    main,
    parse_memory_to_bytes,
    validate_candidate_corpus,
)
from recommender.models import (
    ENVIRONMENT_CANDIDATE_SCHEMA_VERSION,
    POLICY_VERSION,
    ContractValidationError,
    EnvironmentCandidate,
    GPURequirement,
    SpawnRecommendation,
    TaskType,
)
from recommender.policy import PolicyValidator
from recommender.rule_based import load_image_catalog


def test_parse_memory_to_bytes():
    assert parse_memory_to_bytes(1024) == 1024
    assert parse_memory_to_bytes("256M") == 256 * 1024 * 1024
    assert parse_memory_to_bytes("384M") == 384 * 1024 * 1024
    assert parse_memory_to_bytes("768M") == 768 * 1024 * 1024
    assert parse_memory_to_bytes("1G") == 1024 * 1024 * 1024
    assert parse_memory_to_bytes("1.5G") == int(1.5 * 1024 * 1024 * 1024)
    assert parse_memory_to_bytes("1536M") == 1536 * 1024 * 1024
    assert parse_memory_to_bytes("2G") == 2 * 1024 * 1024 * 1024
    assert parse_memory_to_bytes("500MB") == 500 * 1000 * 1000

    with pytest.raises(ContractValidationError, match="boolean"):
        parse_memory_to_bytes(True)
    with pytest.raises(ContractValidationError, match="blank"):
        parse_memory_to_bytes("  ")
    with pytest.raises(ContractValidationError, match="invalid memory specification"):
        parse_memory_to_bytes("invalid-spec")
    with pytest.raises(ContractValidationError, match="unknown memory unit"):
        parse_memory_to_bytes("100XYZ")


def test_candidate_resource_metadata_validation():
    meta = CandidateResourceMetadata(
        cpu_guarantee_cores=0.5,
        cpu_limit_cores=1.0,
        memory_guarantee_gb=0.75,
        memory_limit_gb=1.0,
        memory_guarantee_bytes=768 * 1024 * 1024,
        memory_limit_bytes=1024 * 1024 * 1024,
        gpu_count=0,
        gpu_resource=None,
    )
    assert meta.cpu_limit_cores == 1.0
    assert meta.memory_limit_gb == 1.0
    assert meta.gpu_count == 0

    # CPU guarantee exceeding limit
    with pytest.raises(ContractValidationError, match="cpu_guarantee_cores must not exceed"):
        CandidateResourceMetadata(
            cpu_guarantee_cores=2.0,
            cpu_limit_cores=1.0,
            memory_guarantee_gb=0.5,
            memory_limit_gb=1.0,
            memory_guarantee_bytes=512,
            memory_limit_bytes=1024,
        )

    # Memory guarantee exceeding limit
    with pytest.raises(ContractValidationError, match="memory_guarantee_bytes must not exceed"):
        CandidateResourceMetadata(
            cpu_guarantee_cores=0.5,
            cpu_limit_cores=1.0,
            memory_guarantee_gb=0.5,
            memory_limit_gb=1.0,
            memory_guarantee_bytes=2048,
            memory_limit_bytes=1024,
        )


def test_build_candidate_corpus_from_real_catalogs():
    corpus = load_candidate_corpus()
    image_catalog = load_image_catalog()

    # 3 profiles x 4 images = 12 valid combinations
    assert len(corpus) == 12
    assert corpus.corpus_version == DEFAULT_CORPUS_VERSION
    assert corpus.source_image_catalog_version == image_catalog["catalog_version"]
    assert len(corpus.source_image_catalog_checksum) == 64
    assert len(corpus.source_profile_catalog_checksum) == 64
    assert len(corpus.corpus_checksum) == 64
    assert corpus.schema_version == CANDIDATE_CORPUS_SCHEMA_VERSION
    assert corpus.policy_version == POLICY_VERSION

    expected_candidate_ids = (
        "large-minimal-python",
        "large-pytorch-deep-learning",
        "large-scipy-data-science",
        "large-tensorflow-deep-learning",
        "medium-minimal-python",
        "medium-pytorch-deep-learning",
        "medium-scipy-data-science",
        "medium-tensorflow-deep-learning",
        "small-minimal-python",
        "small-pytorch-deep-learning",
        "small-scipy-data-science",
        "small-tensorflow-deep-learning",
    )
    assert corpus.candidate_ids == expected_candidate_ids


def test_candidate_document_metadata_completeness():
    corpus = load_candidate_corpus()
    candidate = corpus.get("large-pytorch-deep-learning")
    assert candidate is not None

    assert candidate.candidate_id == "large-pytorch-deep-learning"
    assert candidate.profile_id == "large"
    assert candidate.image_id == "pytorch-deep-learning"
    assert "pytorch-notebook@" in candidate.image_reference
    assert "Large" in candidate.display_name
    assert "PyTorch Deep Learning" in candidate.display_name
    assert candidate.resource_metadata.cpu_limit_cores == 2.0
    assert candidate.resource_metadata.memory_limit_gb == 2.0
    assert candidate.resource_metadata.gpu_count == 0
    assert candidate.gpu_capability is GPURequirement.NOT_NEEDED

    # Workload/Task tags
    assert TaskType.DEEP_LEARNING in candidate.task_types
    assert TaskType.MODEL_TRAINING in candidate.task_types

    # Frameworks and libraries
    assert "pytorch" in candidate.frameworks
    assert "torch" in candidate.frameworks
    assert "torchvision" in candidate.libraries

    # Capabilities and match terms
    assert "cuda-userspace" in candidate.capabilities
    assert "bert" in candidate.match_terms
    assert "resnet" in candidate.match_terms

    # Suitability & Preference tags
    assert "deep_learning" in candidate.suitability_tags
    assert "heavy_workload" in candidate.suitability_tags
    assert "pytorch" in candidate.preference_tags


def test_deterministic_retrieval_text_generation():
    corpus1 = load_candidate_corpus()
    corpus2 = load_candidate_corpus()

    for c1, c2 in zip(corpus1, corpus2):
        assert c1.retrieval_text == c2.retrieval_text
        assert f"Candidate ID: {c1.candidate_id}" in c1.retrieval_text
        assert f"Profile: " in c1.retrieval_text
        assert f"Image: " in c1.retrieval_text
        assert f"Description: " in c1.retrieval_text
        assert f"Workloads / Tasks: " in c1.retrieval_text
        assert f"Frameworks: " in c1.retrieval_text
        assert f"Libraries: " in c1.retrieval_text
        assert f"Capabilities: " in c1.retrieval_text
        assert f"Keywords / Match Terms: " in c1.retrieval_text
        assert f"Suitability Tags: " in c1.retrieval_text
        assert f"Preference Tags: " in c1.retrieval_text


def test_checksum_provenance_recording():
    corpus = load_candidate_corpus()
    raw_images = load_image_catalog()
    expected_image_checksum = canonical_json_checksum(raw_images)
    expected_profile_checksum = canonical_json_checksum(DEFAULT_PROFILE_DEFINITIONS)

    assert corpus.source_image_catalog_checksum == expected_image_checksum
    assert corpus.source_profile_catalog_checksum == expected_profile_checksum

    # Corpus checksum changes if candidate content changes
    candidates_dict = [c.to_dict() for c in corpus.candidates]
    assert corpus.corpus_checksum == canonical_json_checksum(candidates_dict)


def test_validation_rejects_duplicate_candidate_ids():
    corpus = load_candidate_corpus()
    duplicate_candidates = corpus.candidates + (corpus.candidates[0],)

    with pytest.raises(ContractValidationError, match="unique candidate IDs"):
        CandidateCorpus(
            candidates=duplicate_candidates,
            corpus_version="v1",
            source_image_catalog_version="v1",
            source_image_catalog_checksum="a" * 64,
            source_profile_catalog_checksum="b" * 64,
            corpus_checksum="c" * 64,
        )


def test_validation_rejects_nonexistent_image_reference():
    corpus = load_candidate_corpus()
    tampered_doc = CandidateDocument(
        candidate_id="small-fake-image",
        profile_id="small",
        image_id="fake-image",
        image_reference="quay.io/jupyter/fake@sha256:" + "0" * 64,
        display_name="Small / Fake",
        description="Fake image",
        task_types=(TaskType.OTHER,),
        capabilities=("python",),
        frameworks=(),
        libraries=("python",),
        resource_metadata=corpus.candidates[0].resource_metadata,
        gpu_capability=GPURequirement.NOT_NEEDED,
        suitability_tags=("light",),
        preference_tags=(),
        match_terms=(),
        retrieval_text="text",
        catalog_version=corpus.source_image_catalog_version,
    )
    tampered_corpus = CandidateCorpus(
        candidates=(tampered_doc,),
        corpus_version="v1",
        source_image_catalog_version=corpus.source_image_catalog_version,
        source_image_catalog_checksum="a" * 64,
        source_profile_catalog_checksum="b" * 64,
        corpus_checksum="c" * 64,
    )
    with pytest.raises(ContractValidationError, match="nonexistent image 'fake-image'"):
        validate_candidate_corpus(tampered_corpus, image_catalog=load_image_catalog())


def test_validation_rejects_nonexistent_profile_reference():
    corpus = load_candidate_corpus()
    tampered_doc = CandidateDocument(
        candidate_id="gigantic-minimal-python",
        profile_id="gigantic",
        image_id="minimal-python",
        image_reference=corpus.candidates[0].image_reference,
        display_name="Gigantic / Minimal",
        description="Gigantic profile",
        task_types=(TaskType.OTHER,),
        capabilities=("python",),
        frameworks=(),
        libraries=("python",),
        resource_metadata=corpus.candidates[0].resource_metadata,
        gpu_capability=GPURequirement.NOT_NEEDED,
        suitability_tags=("light",),
        preference_tags=(),
        match_terms=(),
        retrieval_text="text",
        catalog_version=corpus.source_image_catalog_version,
    )
    tampered_corpus = CandidateCorpus(
        candidates=(tampered_doc,),
        corpus_version="v1",
        source_image_catalog_version=corpus.source_image_catalog_version,
        source_image_catalog_checksum="a" * 64,
        source_profile_catalog_checksum="b" * 64,
        corpus_checksum="c" * 64,
    )
    with pytest.raises(ContractValidationError, match="nonexistent profile 'gigantic'"):
        validate_candidate_corpus(tampered_corpus, profile_catalog=DEFAULT_PROFILE_DEFINITIONS)


def test_validation_rejects_malformed_resource_metadata():
    with pytest.raises(ContractValidationError):
        CandidateResourceMetadata(
            cpu_guarantee_cores=-1.0,
            cpu_limit_cores=1.0,
            memory_guarantee_gb=0.5,
            memory_limit_gb=1.0,
            memory_guarantee_bytes=512,
            memory_limit_bytes=1024,
        )


def test_diagnostic_and_query_mechanisms():
    corpus = load_candidate_corpus()

    # Query helpers
    assert len(corpus.find_by_profile("small")) == 4
    assert len(corpus.find_by_profile("medium")) == 4
    assert len(corpus.find_by_profile("large")) == 4
    assert len(corpus.find_by_image("pytorch-deep-learning")) == 3
    assert corpus.get("nonexistent-id") is None

    # Enumeration summary
    summary = corpus.enumerate_candidates()
    assert len(summary) == 12
    assert summary[0]["candidate_id"] == "large-minimal-python"
    assert "cpu_limit_cores" in summary[0]
    assert "memory_limit_gb" in summary[0]
    assert "retrieval_text_length" in summary[0]

    # Full diagnostic dict and JSON
    diag = corpus.to_diagnostic_dict()
    assert diag["candidate_count"] == 12
    assert len(diag["candidates"]) == 12
    assert json.loads(corpus.to_json()) == diag


def test_conversion_to_environment_candidate_and_trust_boundary():
    corpus = load_candidate_corpus()
    candidate = corpus.get("large-pytorch-deep-learning")
    assert candidate is not None

    # Resolve to trusted EnvironmentCandidate contract
    env_cand = candidate.to_environment_candidate()
    assert isinstance(env_cand, EnvironmentCandidate)
    assert env_cand.candidate_id == "large-pytorch-deep-learning"
    assert env_cand.profile_id == "large"
    assert env_cand.image_id == "pytorch-deep-learning"
    assert env_cand.catalog_version == corpus.source_image_catalog_version
    assert env_cand.policy_version == POLICY_VERSION

    # Resolve to SpawnRecommendation and validate via PolicyValidator
    catalog = load_image_catalog()
    validator = PolicyValidator.from_catalog(
        profiles=["small", "medium", "large"],
        catalog=catalog,
    )

    rec = candidate.to_spawn_recommendation()
    assert isinstance(rec, SpawnRecommendation)
    assert rec.profile == "large"
    assert rec.image_id == "pytorch-deep-learning"

    validated_rec = validator.validate(rec)
    assert validated_rec.profile == "large"
    assert validated_rec.image_id == "pytorch-deep-learning"


def test_policy_validator_rejects_tampered_candidate_recommendations():
    corpus = load_candidate_corpus()
    candidate = corpus.get("large-pytorch-deep-learning")
    assert candidate is not None

    catalog = load_image_catalog()
    validator = PolicyValidator.from_catalog(
        profiles=["small", "medium", "large"],
        catalog=catalog,
    )

    # 1. Tampered profile not in validator allowlist
    tampered_rec_bad_profile = SpawnRecommendation(
        profile="unapproved-gpu-node",
        reasons=["Tampered profile"],
        score=100,
        image_id=candidate.image_id,
        image_reference=candidate.image_reference,
        image_reasons=["Allowlisted image"],
        catalog_version=candidate.catalog_version,
    )
    with pytest.raises(ValueError, match="not recognized by deployment policy"):
        validator.validate(tampered_rec_bad_profile)

    # 2. Tampered image not in allowlist
    tampered_rec_bad_image = SpawnRecommendation(
        profile=candidate.profile_id,
        reasons=["Valid profile"],
        score=100,
        image_id="unapproved-attacker-image",
        image_reference="attacker.invalid/notebook:latest",
        image_reasons=["Attacker image"],
        catalog_version=candidate.catalog_version,
    )
    with pytest.raises(ValueError, match="not allowlisted"):
        validator.validate(tampered_rec_bad_image)


def test_immutability_and_round_trip_serialization():
    corpus = load_candidate_corpus()
    candidate = corpus.get("small-minimal-python")
    assert candidate is not None

    with pytest.raises(FrozenInstanceError):
        candidate.candidate_id = "mutated"  # type: ignore[misc]

    cand_dict = candidate.to_dict()
    reconstructed = CandidateDocument.from_dict(cand_dict)
    assert reconstructed == candidate
    assert reconstructed.to_dict() == cand_dict


def test_cli_diagnostic_execution(capsys):
    with patch("sys.argv", ["candidate_corpus", "--summary"]):
        main()
    captured = capsys.readouterr()
    assert "Candidate Corpus Version: environment-candidate-corpus-v1" in captured.out
    assert "Total Candidates: 12" in captured.out
    assert "large-pytorch-deep-learning" in captured.out

    with patch("sys.argv", ["candidate_corpus", "--validate"]):
        main()
    captured = capsys.readouterr()
    assert "Validation successful: 12 valid candidates." in captured.out

    with patch("sys.argv", ["candidate_corpus", "--dump"]):
        main()
    captured = capsys.readouterr()
    assert "Candidate ID: small-minimal-python" in captured.out

    with patch("sys.argv", ["candidate_corpus", "--json"]):
        main()
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["candidate_count"] == 12
