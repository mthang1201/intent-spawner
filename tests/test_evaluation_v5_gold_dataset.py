from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import zipfile

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

import evaluation_v5.gold_dataset as gold_dataset_module
from evaluation_v5.gold_dataset import (
    COMPILED_SPLIT_SCHEMA_VERSION,
    GOLD_DATASET_SCHEMA_VERSION,
    GoldDatasetReviewError,
    GoldDatasetValidationError,
    compile_gold_dataset,
    current_catalog_identity,
    import_v4_dataset,
    load_gold_dataset,
    main,
    review_gold_dataset,
    summarize_gold_dataset,
    validate_gold_dataset,
    write_document_exclusive,
)
from evaluation_v5.isolation_audit import IsolationAuditError, audit_repository
from evaluation_v5.split_dataset import (
    SPLIT_BUNDLE_SCHEMA_VERSION,
    SplitBundleValidationError,
    load_development_split,
    split_bundle_checksum,
    validate_split_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
GIT_REVISION = "5499309aea4ca896e905f6f2dc0e35e92fab8e60"


def _variant(
    variant_id: str,
    *,
    variant_class: str = "canonical_en",
    language: str = "en",
    intent: str = "Clean a moderate table with pandas.",
    equivalence_status: str = "canonical_reference",
    code_context: list[str] | None = None,
) -> dict[str, object]:
    return {
        "variant_id": variant_id,
        "variant_class": variant_class,
        "language": language,
        "intent": intent,
        "code_context": code_context or [],
        "equivalence_status": equivalence_status,
    }


def _feasible_family() -> dict[str, object]:
    return {
        "family_id": "table-cleaning",
        "title": "Table cleaning",
        "workload_stratum": "data_processing",
        "difficulty": "medium",
        "executable_workload_id": "data_pandas_read_transform",
        "gold_structured_intent": {
            "task_types": ["data_processing"],
            "required_features": ["pandas"],
            "preferred_features": [],
            "forbidden_features": [],
            "required_frameworks": [],
            "preferred_frameworks": [],
            "gpu_semantics": "forbidden",
            "minimum_cpu_cores": 0.5,
            "minimum_memory_gb": 0.5,
            "dataset_size_gb": 0.8,
            "ambiguities": [],
        },
        "candidate_gold": {
            "preferred_candidate_ids": ["medium-scipy-data-science"],
            "acceptable_candidate_ids": [
                "medium-scipy-data-science",
                "large-scipy-data-science",
            ],
        },
        "profile_gold": {
            "preferred_profile_ids": ["medium"],
            "acceptable_profile_ids": ["medium", "large"],
        },
        "image_gold": {
            "preferred_image_ids": ["scipy-data-science"],
            "acceptable_image_ids": ["scipy-data-science"],
            "required_capabilities": ["pandas"],
        },
        "policy_gold": {
            "required_constraints": ["gpu forbidden"],
            "explicitly_unsupported_requirements": [],
            "expected_feasibility": "feasible",
        },
        "variants": [
            _variant("table-cleaning-canonical"),
            _variant(
                "table-cleaning-paraphrase",
                variant_class="paraphrase_en",
                intent="Prepare a medium-sized data frame and fix its column types.",
                equivalence_status="reviewed_equivalent",
            ),
            _variant(
                "table-cleaning-vi",
                variant_class="vietnamese",
                language="vi",
                intent="Làm sạch bảng dữ liệu cỡ vừa bằng pandas.",
                equivalence_status="reviewed_equivalent",
            ),
        ],
        "label_review": {
            "status": "approved",
            "reviewed_by": "fixture-reviewer",
            "reviewed_at_utc": "2026-08-24T01:00:00Z",
            "notes": ["Candidate and resource labels checked against the catalog."],
        },
        "source_provenance": None,
    }


def _infeasible_family() -> dict[str, object]:
    return {
        "family_id": "gpu-required",
        "title": "GPU required",
        "workload_stratum": "gpu_policy",
        "difficulty": "hard",
        "executable_workload_id": None,
        "gold_structured_intent": {
            "task_types": ["deep_learning", "model_training"],
            "required_features": ["pytorch"],
            "preferred_features": [],
            "forbidden_features": [],
            "required_frameworks": ["pytorch"],
            "preferred_frameworks": [],
            "gpu_semantics": "required",
            "minimum_cpu_cores": None,
            "minimum_memory_gb": None,
            "dataset_size_gb": 0.5,
            "ambiguities": [],
        },
        "candidate_gold": {
            "preferred_candidate_ids": [],
            "acceptable_candidate_ids": [],
        },
        "profile_gold": {
            "preferred_profile_ids": [],
            "acceptable_profile_ids": ["small", "medium", "large"],
        },
        "image_gold": {
            "preferred_image_ids": [],
            "acceptable_image_ids": ["pytorch-deep-learning"],
            "required_capabilities": ["pytorch"],
        },
        "policy_gold": {
            "required_constraints": ["GPU device required"],
            "explicitly_unsupported_requirements": [
                "No administrator profile grants a GPU device."
            ],
            "expected_feasibility": "infeasible",
        },
        "variants": [
            _variant(
                "gpu-required-noisy",
                variant_class="informal_or_noisy",
                intent="need cuda gpu pls, training cannot run cpu-only",
                equivalence_status="reviewed_equivalent",
            )
        ],
        "label_review": {
            "status": "approved",
            "reviewed_by": "fixture-reviewer",
            "reviewed_at_utc": "2026-08-24T01:00:00Z",
            "notes": ["Infeasibility checked against all administrator profiles."],
        },
        "source_provenance": None,
    }


def _document(*, lifecycle: str = "frozen", role: str = "development") -> dict[str, object]:
    return {
        "schema_version": GOLD_DATASET_SCHEMA_VERSION,
        "dataset_metadata": {
            "dataset_id": "mini-v5-gold",
            "protocol_version": "5.0.0",
            "role": role,
            "lifecycle": lifecycle,
            "created_at_utc": "2026-08-24T00:00:00Z",
            "created_by": "fixture-author",
            "git_revision": GIT_REVISION,
            "evidence_classification": "synthetic_test_fixture",
            "freeze_metadata": (
                {
                    "frozen_at_utc": "2026-08-24T02:00:00Z",
                    "frozen_by": "fixture-custodian",
                }
                if lifecycle == "frozen"
                else None
            ),
            "source_datasets": [],
        },
        "catalog_identity": current_catalog_identity(),
        "review_policy": {
            "required_workload_strata": [
                "data_processing",
                "gpu_policy",
                "missing_category",
            ],
            "max_preferred_profile_share": 0.5,
            "max_preferred_image_share": 0.5,
        },
        "families": [_feasible_family(), _infeasible_family()],
    }


def test_valid_family_dataset_summary_and_optional_variant_classes():
    dataset = validate_gold_dataset(_document())
    summary = summarize_gold_dataset(dataset)

    assert len(dataset.families) == 2
    assert summary["family_count"] == 2
    assert summary["case_count"] == 4
    assert summary["language_counts"] == {"en": 3, "vi": 1}
    assert summary["workload_strata"]["data_processing"] == {
        "family_count": 1,
        "case_count": 3,
    }
    assert summary["difficulty_distribution"] == {"hard": 1, "medium": 1}
    assert summary["feasibility_distribution"] == {"feasible": 1, "infeasible": 1}
    assert summary["capability_coverage"] == {"pandas": 1, "pytorch": 1}
    assert summary["perturbation_coverage"]["vietnamese"] == {
        "case_count": 1,
        "family_count": 1,
    }


def test_optional_family_and_variant_fields_may_be_omitted():
    document = _document()
    family = document["families"][1]  # type: ignore[index]
    family.pop("executable_workload_id")
    family.pop("source_provenance")
    family["variants"][0].pop("code_context")
    family["variants"][0]["variant_class"] = "vietnamese"
    family["variants"][0]["language"] = "vi"
    family["variants"][0]["equivalence_status"] = "canonical_reference"

    validated = validate_gold_dataset(document)

    assert validated.families[1].executable_workload_id is None
    assert validated.families[1].source_provenance is None
    assert validated.families[1].variants[0].code_context == ()


@pytest.mark.parametrize(
    "variant_class",
    ["optional_code_context", "optional_ambiguity_variant"],
)
def test_optional_perturbation_variant_classes_are_supported(variant_class: str):
    document = _document()
    variant = document["families"][0]["variants"][0]  # type: ignore[index]
    variant["variant_class"] = variant_class
    variant["code_context"] = ["import pandas as pd"]

    validated = validate_gold_dataset(document)

    assert validated.families[0].variants[0].variant_class == variant_class


def test_strict_yaml_and_json_loading(tmp_path: Path):
    valid_yaml = tmp_path / "valid.yaml"
    valid_yaml.write_text(yaml.safe_dump(_document(), sort_keys=False), encoding="utf-8")
    assert load_gold_dataset(valid_yaml).dataset.dataset_metadata["dataset_id"] == "mini-v5-gold"

    valid_json = tmp_path / "valid.json"
    valid_json.write_text(json.dumps(_document()), encoding="utf-8")
    assert load_gold_dataset(valid_json).dataset.families[0].family_id == "table-cleaning"

    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        "schema_version: protocol-v5-gold-family-v1.0.0\nschema_version: duplicate\n",
        encoding="utf-8",
    )
    with pytest.raises(GoldDatasetValidationError, match="duplicate YAML field"):
        load_gold_dataset(duplicate)

    multi = tmp_path / "multi.yaml"
    multi.write_text("a: 1\n---\nb: 2\n", encoding="utf-8")
    with pytest.raises(GoldDatasetValidationError, match="YAML is invalid"):
        load_gold_dataset(multi)

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"resource": NaN}', encoding="utf-8")
    with pytest.raises(GoldDatasetValidationError, match="non-finite"):
        load_gold_dataset(nonfinite)

    unknown = tmp_path / "dataset.txt"
    unknown.write_text("{}", encoding="utf-8")
    with pytest.raises(GoldDatasetValidationError, match=".yaml"):
        load_gold_dataset(unknown)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("extra_field", "unexpected fields"),
        ("duplicate_variant", "globally unique"),
        ("unknown_candidate", "unknown candidates"),
        ("unknown_profile", "unknown profiles"),
        ("unknown_image", "unknown images"),
        ("preferred_not_acceptable", "subset"),
        ("candidate_component_mismatch", "disagrees"),
        ("candidate_constraint_violation", "hard gold constraints"),
        ("missing_unsupported_marker", "explicitly mark"),
        ("catalog_drift", "administrator-owned"),
        ("unknown_workload", "does not resolve"),
        ("infinite_resource", "finite JSON data"),
    ],
)
def test_semantic_validation_rejects_invalid_datasets(mutation: str, message: str):
    document = _document()
    feasible = document["families"][0]  # type: ignore[index]
    infeasible = document["families"][1]  # type: ignore[index]
    if mutation == "extra_field":
        feasible["unexpected"] = True
    elif mutation == "duplicate_variant":
        infeasible["variants"][0]["variant_id"] = "table-cleaning-canonical"
    elif mutation == "unknown_candidate":
        feasible["candidate_gold"]["acceptable_candidate_ids"].append("missing-candidate")
    elif mutation == "unknown_profile":
        infeasible["profile_gold"]["acceptable_profile_ids"].append("gigantic")
    elif mutation == "unknown_image":
        infeasible["image_gold"]["acceptable_image_ids"].append("missing-image")
    elif mutation == "preferred_not_acceptable":
        feasible["candidate_gold"]["preferred_candidate_ids"] = ["small-minimal-python"]
    elif mutation == "candidate_component_mismatch":
        feasible["profile_gold"]["acceptable_profile_ids"] = ["medium"]
    elif mutation == "candidate_constraint_violation":
        feasible["gold_structured_intent"]["minimum_cpu_cores"] = 1.5
    elif mutation == "missing_unsupported_marker":
        infeasible["policy_gold"]["explicitly_unsupported_requirements"] = []
    elif mutation == "catalog_drift":
        document["catalog_identity"]["candidate_corpus_sha256"] = "0" * 64
    elif mutation == "unknown_workload":
        feasible["executable_workload_id"] = "missing-workload"
    else:
        feasible["gold_structured_intent"]["minimum_cpu_cores"] = float("inf")
    with pytest.raises(GoldDatasetValidationError, match=message):
        validate_gold_dataset(document)


def test_review_report_is_redaction_safe_and_classifies_findings():
    document = _document()
    first = document["families"][0]  # type: ignore[index]
    second = document["families"][1]  # type: ignore[index]
    secret_prompt = first["variants"][0]["intent"]
    second["variants"][0]["intent"] = secret_prompt.upper() + "!!!"
    first["difficulty"] = "unassessed"
    first["label_review"] = {
        "status": "pending",
        "reviewed_by": None,
        "reviewed_at_utc": None,
        "notes": [],
    }
    first["variants"][1]["equivalence_status"] = "pending_review"
    dataset = validate_gold_dataset(document)
    report = review_gold_dataset(dataset)
    encoded = json.dumps(report.to_dict(), ensure_ascii=False)
    codes = {item.code for item in report.findings}

    assert "unassessed_difficulty" in codes
    assert "unresolved_gold_review" in codes
    assert "pending_semantic_equivalence" in codes
    assert "singleton_stratum" in codes
    assert "missing_workload_category" in codes
    assert "normalized_duplicate_variant_across_families" in codes
    assert "unbalanced_preferred_profile" in codes
    assert "unbalanced_preferred_image" in codes
    assert secret_prompt not in encoded


def test_review_highlights_unresolved_and_documented_gold_ambiguity():
    document = _document()
    family = document["families"][1]  # type: ignore[index]
    family["policy_gold"]["expected_feasibility"] = "ambiguous"
    family["gold_structured_intent"]["ambiguities"] = [
        "Whether a GPU is a hard requirement needs adjudication."
    ]
    family["label_review"] = {
        "status": "pending",
        "reviewed_by": None,
        "reviewed_at_utc": None,
        "notes": [],
    }
    pending = review_gold_dataset(validate_gold_dataset(document))
    assert "unresolved_gold_ambiguity" in {item.code for item in pending.findings}

    family["label_review"] = {
        "status": "approved",
        "reviewed_by": "fixture-reviewer",
        "reviewed_at_utc": "2026-08-24T01:00:00Z",
        "notes": ["Ambiguity is intentional and retained for robustness review."],
    }
    approved = review_gold_dataset(validate_gold_dataset(document))
    assert "documented_gold_ambiguity" in {item.code for item in approved.findings}


def test_compile_preserves_full_gold_and_v1_remains_supported():
    dataset = validate_gold_dataset(_document())
    bundle = compile_gold_dataset(dataset)
    payload = bundle.to_dict()

    assert bundle.schema_version == COMPILED_SPLIT_SCHEMA_VERSION
    assert bundle.split_manifest.family_count == 2
    assert bundle.split_manifest.case_count == 4
    assert payload["cases"][0]["gold"]["gold_structured_intent"]["task_types"] == [
        "data_processing"
    ]
    assert payload["cases"][0]["variant_metadata"] == {
        "variant_class": "canonical_en",
        "equivalence_status": "canonical_reference",
    }
    assert validate_split_bundle(payload).to_dict() == payload

    historical = load_development_split()
    assert historical.bundle.schema_version == SPLIT_BUNDLE_SCHEMA_VERSION
    assert historical.manifest.case_count == 18


def test_v2_validation_rejects_cross_case_family_drift():
    payload = compile_gold_dataset(validate_gold_dataset(_document())).to_dict()
    payload["cases"][1]["family_metadata"]["title"] = "Drifted family title"
    payload["split_manifest"]["checksum"] = split_bundle_checksum(payload)

    with pytest.raises(
        SplitBundleValidationError,
        match="disagrees with another case in family",
    ):
        validate_split_bundle(payload)


def test_v2_validation_rejects_source_role_drift():
    payload = compile_gold_dataset(validate_gold_dataset(_document())).to_dict()
    for case in payload["cases"]:
        case["source_provenance"]["source_split"] = "confirmatory"
    payload["split_manifest"]["checksum"] = split_bundle_checksum(payload)

    with pytest.raises(
        SplitBundleValidationError,
        match="does not match split_manifest.role",
    ):
        validate_split_bundle(payload)


def test_compile_blocks_unresolved_or_unfrozen_labels():
    draft = validate_gold_dataset(_document(lifecycle="draft"))
    with pytest.raises(GoldDatasetReviewError, match="manually frozen"):
        compile_gold_dataset(draft)

    document = _document()
    variants = document["families"][0]["variants"]  # type: ignore[index]
    variants[1]["equivalence_status"] = "pending_review"
    pending = validate_gold_dataset(document)
    with pytest.raises(GoldDatasetReviewError, match="pending_semantic_equivalence"):
        compile_gold_dataset(pending)


def test_confirmatory_compile_requires_external_absolute_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    dataset = validate_gold_dataset(_document(role="confirmatory"))
    with pytest.raises(GoldDatasetValidationError, match="external paths"):
        compile_gold_dataset(
            dataset,
            source_path=ROOT / "private-confirmatory.yaml",
            output_path=tmp_path / "compiled.yaml",
        )
    bundle = compile_gold_dataset(
        dataset,
        source_path=tmp_path / "private-confirmatory.yaml",
        output_path=tmp_path / "compiled.yaml",
    )
    assert bundle.split_manifest.role.value == "confirmatory"

    source = tmp_path / "relative-source.yaml"
    write_document_exclusive(source, _document(role="confirmatory"))
    monkeypatch.chdir(tmp_path)
    loaded = load_gold_dataset(Path("relative-source.yaml"))
    with pytest.raises(GoldDatasetValidationError, match="absolute external paths"):
        compile_gold_dataset(loaded, output_path=tmp_path / "relative-output.yaml")


def test_extra_workload_manifests_are_supported_through_compilation(tmp_path: Path):
    manifest = tmp_path / "external-workloads.yaml"
    manifest.write_text(
        yaml.safe_dump({"workloads": [{"workload_id": "custodian-workload"}]}),
        encoding="utf-8",
    )
    document = _document()
    document["families"][0]["executable_workload_id"] = "custodian-workload"  # type: ignore[index]
    dataset = validate_gold_dataset(document, workload_manifests=[manifest])
    bundle = compile_gold_dataset(dataset, workload_manifests=[manifest])
    assert bundle.cases[0].family_metadata["executable_workload_id"] == (
        "custodian-workload"
    )


def test_v4_import_is_development_only_pending_reference_material():
    imported = import_v4_dataset(ROOT / "benchmarks" / "intent-gold-v4.yaml")
    report = review_gold_dataset(imported)

    assert imported.dataset_metadata["role"] == "development"
    assert imported.dataset_metadata["lifecycle"] == "draft"
    assert imported.dataset_metadata["evidence_classification"] == (
        "historical_formative_development_only"
    )
    assert len(imported.families) == 4
    assert sum(len(family.variants) for family in imported.families) == 12
    assert all(family.source_provenance is not None for family in imported.families)
    assert any(
        variant.equivalence_status == "pending_review"
        for family in imported.families
        for variant in family.variants
    )
    assert report.blocking_findings

    test_import = import_v4_dataset(
        ROOT / "benchmarks" / "intent-gold-v4.yaml",
        source_split="test",
        sample_ids=["small-csv-canonical-en"],
    )
    assert test_import.dataset_metadata["role"] == "development"
    assert test_import.families[0].source_provenance["source_split"] == "test"
    assert test_import.dataset_metadata["dataset_id"] != imported.dataset_metadata[
        "dataset_id"
    ]


def test_cli_import_validate_summary_review_and_overwrite_guard(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    output = tmp_path / "imported.yaml"
    source = ROOT / "benchmarks" / "intent-gold-v4.yaml"
    assert main(["import-v4", str(source), "--output", str(output)]) == 0
    imported_status = json.loads(capsys.readouterr().out)
    assert imported_status["status"] == "DEVELOPMENT_DRAFT"
    assert output.is_file()

    assert main(["validate", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "VALID"
    assert main(["summary", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["family_count"] == 4
    assert main(["summary", str(output), "--format", "markdown"]) == 0
    markdown_summary = capsys.readouterr().out
    assert "## Workload strata" in markdown_summary
    assert "## Profile coverage" in markdown_summary
    assert "## Image coverage" in markdown_summary
    assert "## Perturbation coverage" in markdown_summary
    assert main(["summary", str(output), "--format", "markdown"]) == 0
    assert capsys.readouterr().out == markdown_summary
    assert main(["review", str(output), "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["blocking_count"] > 0
    assert main(["review", str(output), "--format", "markdown"]) == 1
    markdown_review = capsys.readouterr().out
    assert "## Blocking" in markdown_review
    assert main(["review", str(output), "--format", "markdown"]) == 1
    assert capsys.readouterr().out == markdown_review

    assert main(["import-v4", str(source), "--output", str(output)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "ERROR"


def test_cli_compiles_manually_frozen_synthetic_fixture(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    source = tmp_path / "reviewed-family.yaml"
    output = tmp_path / "compiled-split.json"
    write_document_exclusive(source, _document())

    assert main(["compile", str(source), "--output", str(output)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "COMPILED"
    assert status["schema_version"] == COMPILED_SPLIT_SCHEMA_VERSION
    assert validate_split_bundle(json.loads(output.read_text(encoding="utf-8")))


def test_exclusive_writer_supports_json_and_yaml(tmp_path: Path):
    for suffix in (".json", ".yaml"):
        target = tmp_path / f"payload{suffix}"
        write_document_exclusive(target, {"finite": 1.0, "unicode": "Việt Nam"})
        assert target.is_file()
        with pytest.raises(FileExistsError):
            write_document_exclusive(target, {"finite": 2.0})


def test_machine_readable_schema_files_are_valid_json():
    schemas = {}
    for name in (
        "protocol-v5-split-bundle-v1.schema.json",
        "protocol-v5-gold-family-v1.schema.json",
        "protocol-v5-split-bundle-v2.schema.json",
    ):
        payload = json.loads((ROOT / "benchmarks_v5" / name).read_text(encoding="utf-8"))
        assert payload["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(payload)
        schemas[payload["$id"]] = payload

    registry = Registry().with_resources(
        [
            (identifier, Resource.from_contents(schema))
            for identifier, schema in schemas.items()
        ]
    )
    Draft202012Validator(
        schemas["protocol-v5-gold-family-v1.schema.json"], registry=registry
    ).validate(_document())
    compiled = compile_gold_dataset(validate_gold_dataset(_document())).to_dict()
    Draft202012Validator(
        schemas["protocol-v5-split-bundle-v2.schema.json"], registry=registry
    ).validate(compiled)


def test_isolation_audit_detects_confirmatory_authoring_and_v2_without_prompts(
    tmp_path: Path,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    authoring = _document(role="confirmatory")
    secret_prompt = authoring["families"][0]["variants"][0]["intent"]  # type: ignore[index]
    (repository / "custodian-dataset.json").write_text(
        json.dumps(authoring, ensure_ascii=False), encoding="utf-8"
    )

    report = audit_repository(repository)
    encoded = json.dumps([finding.__dict__ if hasattr(finding, "__dict__") else {
        "location": finding.location,
        "category": finding.category,
    } for finding in report.findings])
    assert not report.clean
    assert "confirmatory-split-bundle" in encoded
    assert secret_prompt not in encoded

    for item in repository.iterdir():
        item.unlink()
    dataset = validate_gold_dataset(authoring)
    bundle = compile_gold_dataset(
        dataset,
        source_path=tmp_path / "sealed-source.yaml",
        output_path=tmp_path / "sealed-output.yaml",
    )
    (repository / "compiled.json").write_text(
        json.dumps(bundle.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    assert not audit_repository(repository).clean


def test_isolation_audit_detects_confirmatory_authoring_in_archive(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    archive = repository / "package.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("data/gold.yaml", yaml.safe_dump(_document(role="confirmatory")))
    report = audit_repository(repository)
    assert any(
        finding.category == "confirmatory-split-bundle"
        for finding in report.findings
    )


def test_isolation_audit_does_not_flag_new_schema_artifacts(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    for name in (
        "protocol-v5-gold-family-v1.schema.json",
        "protocol-v5-split-bundle-v2.schema.json",
    ):
        (repository / name).write_bytes((ROOT / "benchmarks_v5" / name).read_bytes())
    assert audit_repository(repository).clean


def _set_pending_review(family: dict[str, object]) -> None:
    family["label_review"] = {
        "status": "pending",
        "reviewed_by": None,
        "reviewed_at_utc": None,
        "notes": [],
    }


def test_compile_gating_matrix_blocks_only_unresolved_label_findings():
    draft = validate_gold_dataset(_document(lifecycle="draft"))
    with pytest.raises(GoldDatasetReviewError, match="manually frozen"):
        compile_gold_dataset(draft)

    reviewed = validate_gold_dataset(_document(lifecycle="reviewed"))
    with pytest.raises(GoldDatasetReviewError, match="manually frozen"):
        compile_gold_dataset(reviewed)

    pending_review_document = _document()
    _set_pending_review(pending_review_document["families"][0])  # type: ignore[index]
    with pytest.raises(GoldDatasetReviewError, match="unresolved_gold_review"):
        compile_gold_dataset(validate_gold_dataset(pending_review_document))

    unassessed_document = _document()
    unassessed_document["families"][0]["difficulty"] = "unassessed"  # type: ignore[index]
    with pytest.raises(GoldDatasetReviewError, match="unassessed_difficulty"):
        compile_gold_dataset(validate_gold_dataset(unassessed_document))

    pending_equivalence_document = _document()
    variants = pending_equivalence_document["families"][0]["variants"]  # type: ignore[index]
    variants[1]["equivalence_status"] = "pending_review"
    with pytest.raises(
        GoldDatasetReviewError,
        match="pending_semantic_equivalence",
    ):
        compile_gold_dataset(validate_gold_dataset(pending_equivalence_document))

    unresolved_ambiguity_document = _document()
    ambiguous = unresolved_ambiguity_document["families"][1]  # type: ignore[index]
    ambiguous["policy_gold"]["expected_feasibility"] = "ambiguous"
    ambiguous["gold_structured_intent"]["ambiguities"] = [
        "The GPU requirement still needs adjudication."
    ]
    _set_pending_review(ambiguous)
    with pytest.raises(GoldDatasetReviewError, match="unresolved_gold_ambiguity"):
        compile_gold_dataset(validate_gold_dataset(unresolved_ambiguity_document))

    advisory_only = validate_gold_dataset(_document())
    report = review_gold_dataset(advisory_only)
    assert not report.blocking_findings
    assert {
        "singleton_stratum",
        "missing_workload_category",
        "unbalanced_preferred_profile",
        "unbalanced_preferred_image",
    }.issubset({finding.code for finding in report.advisory_findings})
    assert compile_gold_dataset(advisory_only).split_manifest.case_count == 4

    duplicate_advisory_document = _document()
    family = duplicate_advisory_document["families"][0]  # type: ignore[index]
    family["variants"][1]["intent"] = family["variants"][0]["intent"]
    duplicate_advisory = validate_gold_dataset(duplicate_advisory_document)
    assert "duplicate_variant_within_family" in {
        finding.code for finding in review_gold_dataset(duplicate_advisory).findings
    }
    assert compile_gold_dataset(duplicate_advisory).split_manifest.case_count == 4


def test_compile_revalidates_mutated_gold_and_live_catalog_identity():
    unresolved = validate_gold_dataset(_document())
    unresolved.families[0].candidate_gold["acceptable_candidate_ids"].append(
        "missing-candidate"
    )
    with pytest.raises(GoldDatasetValidationError, match="unknown candidates"):
        compile_gold_dataset(unresolved)

    drifted = validate_gold_dataset(_document())
    drifted.catalog_identity["candidate_corpus_sha256"] = "0" * 64
    with pytest.raises(
        GoldDatasetValidationError,
        match="administrator-owned configuration",
    ):
        compile_gold_dataset(drifted)


def test_controlled_ambiguity_requires_consistent_reviewed_gold_and_compiles():
    document = _document()
    family = document["families"][1]  # type: ignore[index]
    family["policy_gold"]["expected_feasibility"] = "ambiguous"
    family["gold_structured_intent"]["ambiguities"] = [
        "The request deliberately leaves GPU availability uncertain."
    ]
    family["variants"][0]["equivalence_status"] = "controlled_ambiguity"

    dataset = validate_gold_dataset(document)
    report = review_gold_dataset(dataset)

    assert not report.blocking_findings
    assert "documented_gold_ambiguity" in {
        finding.code for finding in report.advisory_findings
    }
    compiled = compile_gold_dataset(dataset)
    controlled_case = next(
        case for case in compiled.cases if case.family_id == "gpu-required"
    )
    assert controlled_case.variant_metadata["equivalence_status"] == (
        "controlled_ambiguity"
    )


def test_controlled_ambiguity_rejects_feasible_or_unreviewed_gold():
    feasible = _document()
    feasible["families"][0]["variants"][0][  # type: ignore[index]
        "equivalence_status"
    ] = "controlled_ambiguity"
    with pytest.raises(
        GoldDatasetValidationError,
        match="expected_feasibility ambiguous",
    ):
        validate_gold_dataset(feasible)

    unreviewed = _document()
    family = unreviewed["families"][1]  # type: ignore[index]
    family["policy_gold"]["expected_feasibility"] = "ambiguous"
    family["gold_structured_intent"]["ambiguities"] = ["Needs review."]
    family["variants"][0]["equivalence_status"] = "controlled_ambiguity"
    _set_pending_review(family)
    with pytest.raises(
        GoldDatasetValidationError,
        match="explicit family review approval",
    ):
        validate_gold_dataset(unreviewed)


@pytest.mark.parametrize(
    "field",
    [
        "candidate_corpus_version",
        "candidate_corpus_sha256",
        "image_catalog_version",
        "image_catalog_sha256",
        "profile_catalog_sha256",
    ],
)
def test_every_catalog_identity_component_is_drift_checked(field: str):
    document = _document()
    document["catalog_identity"][field] = (
        "drifted-version" if field.endswith("version") else "0" * 64
    )
    with pytest.raises(
        GoldDatasetValidationError,
        match=rf"catalog_identity\.{field}.*administrator-owned",
    ):
        validate_gold_dataset(document)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("reviewed_by", None, "non-blank"),
        ("reviewed_at_utc", "2026-08-24 01:00:00", "UTC timestamp"),
        ("notes", [], "non-empty list"),
    ],
)
def test_approved_review_requires_complete_provenance(
    field: str,
    replacement: object,
    message: str,
):
    document = _document()
    document["families"][0]["label_review"][field] = replacement  # type: ignore[index]
    with pytest.raises(GoldDatasetValidationError, match=message):
        validate_gold_dataset(document)


def test_lifecycle_freeze_metadata_rules_are_enforced():
    frozen_without_provenance = _document()
    frozen_without_provenance["dataset_metadata"]["freeze_metadata"] = None
    with pytest.raises(GoldDatasetValidationError, match="frozen dataset requires"):
        validate_gold_dataset(frozen_without_provenance)

    draft_with_freeze = _document(lifecycle="draft")
    draft_with_freeze["dataset_metadata"]["freeze_metadata"] = {
        "frozen_at_utc": "2026-08-24T02:00:00Z",
        "frozen_by": "invalid-custodian",
    }
    with pytest.raises(GoldDatasetValidationError, match="only valid for a frozen"):
        validate_gold_dataset(draft_with_freeze)

    freeze_before_creation = _document()
    freeze_before_creation["dataset_metadata"]["freeze_metadata"][  # type: ignore[index]
        "frozen_at_utc"
    ] = "2026-08-23T23:59:59Z"
    with pytest.raises(GoldDatasetValidationError, match="cannot precede creation"):
        validate_gold_dataset(freeze_before_creation)


def test_compilation_is_deterministic_traceable_and_representation_independent(
    tmp_path: Path,
):
    document = _document()
    dataset = validate_gold_dataset(document)
    first = compile_gold_dataset(dataset)
    second = compile_gold_dataset(dataset)

    assert first.to_dict() == second.to_dict()
    assert first.split_manifest.checksum == second.split_manifest.checksum
    assert {case.case_id for case in first.cases} == {
        variant["variant_id"]
        for family in document["families"]
        for variant in family["variants"]
    }
    for case in first.cases:
        source_family = next(
            family
            for family in document["families"]
            if family["family_id"] == case.family_id
        )
        assert case.case_id == case.variant_id
        assert case.gold == {
            "gold_structured_intent": source_family["gold_structured_intent"],
            "candidate_gold": source_family["candidate_gold"],
            "profile_gold": source_family["profile_gold"],
            "image_gold": source_family["image_gold"],
            "policy_gold": source_family["policy_gold"],
        }
        assert case.source_provenance["source_dataset_id"] == "mini-v5-gold"

    reordered = deepcopy(document)
    reordered["families"].reverse()
    reordered_bundle = compile_gold_dataset(validate_gold_dataset(reordered))
    assert {case.case_id for case in reordered_bundle.cases} == {
        case.case_id for case in first.cases
    }

    yaml_source = tmp_path / "semantic.yaml"
    json_source = tmp_path / "semantic.json"
    write_document_exclusive(yaml_source, document)
    write_document_exclusive(json_source, document)
    yaml_loaded = load_gold_dataset(yaml_source)
    json_loaded = load_gold_dataset(json_source)
    assert yaml_loaded.source_file_sha256 != json_loaded.source_file_sha256
    assert yaml_loaded.source_canonical_sha256 == json_loaded.source_canonical_sha256
    assert compile_gold_dataset(yaml_loaded).to_dict() == compile_gold_dataset(
        json_loaded
    ).to_dict()
    assert yaml_source.read_bytes().endswith(b"\n")
    assert json_source.read_bytes().endswith(b"\n")


def _second_feasible_family() -> dict[str, object]:
    family = _infeasible_family()
    family["family_id"] = "pytorch-cpu"
    family["title"] = "PyTorch on CPU"
    family["workload_stratum"] = "deep_learning"
    family["difficulty"] = "medium"
    family["gold_structured_intent"]["gpu_semantics"] = "not_needed"
    family["candidate_gold"] = {
        "preferred_candidate_ids": ["large-pytorch-deep-learning"],
        "acceptable_candidate_ids": ["large-pytorch-deep-learning"],
    }
    family["profile_gold"] = {
        "preferred_profile_ids": ["large"],
        "acceptable_profile_ids": ["large"],
    }
    family["image_gold"] = {
        "preferred_image_ids": ["pytorch-deep-learning"],
        "acceptable_image_ids": ["pytorch-deep-learning"],
        "required_capabilities": ["pytorch"],
    }
    family["policy_gold"] = {
        "required_constraints": ["CPU execution accepted"],
        "explicitly_unsupported_requirements": [],
        "expected_feasibility": "feasible",
    }
    family["variants"] = [
        _variant(
            "pytorch-cpu-canonical",
            intent="Run a small PyTorch example on CPU.",
        )
    ]
    return family


def test_review_balance_uses_family_not_variant_denominators():
    document = _document()
    first = document["families"][0]  # type: ignore[index]
    first["variants"] = [
        _variant(
            f"table-cleaning-{index:02d}",
            intent=f"Clean table variant {index} with pandas.",
            equivalence_status=(
                "canonical_reference" if index == 0 else "reviewed_equivalent"
            ),
        )
        for index in range(20)
    ]
    document["families"] = [first, _second_feasible_family()]
    document["review_policy"] = {
        "required_workload_strata": ["data_processing", "deep_learning"],
        "max_preferred_profile_share": 0.6,
        "max_preferred_image_share": 0.6,
    }

    dataset = validate_gold_dataset(document)
    summary = summarize_gold_dataset(dataset)
    codes = {finding.code for finding in review_gold_dataset(dataset).findings}

    assert "unbalanced_preferred_profile" not in codes
    assert "unbalanced_preferred_image" not in codes
    assert summary["profile_coverage"]["primary_denominator"] == (
        "workload_families"
    )
    assert summary["profile_coverage"]["preferred"] == {"large": 1, "medium": 1}
    assert summary["profile_coverage"]["case_counts"]["preferred"] == {
        "large": 1,
        "medium": 20,
    }
    assert summary["capability_coverage"] == {"pandas": 1, "pytorch": 1}
    assert summary["capability_case_coverage"] == {"pandas": 20, "pytorch": 1}
    assert summary["workload_strata"]["data_processing"] == {
        "family_count": 1,
        "case_count": 20,
    }


def test_duplicate_finding_codes_distinguish_scope_and_normalization():
    document = _document()
    first = document["families"][0]  # type: ignore[index]
    second = document["families"][1]  # type: ignore[index]
    first["variants"] = [
        _variant("dup-a", intent="Within exact"),
        _variant(
            "dup-b",
            variant_class="other",
            intent="Within exact",
            equivalence_status="reviewed_equivalent",
        ),
        _variant(
            "dup-c",
            variant_class="other",
            intent="Normalized, within!",
            equivalence_status="reviewed_equivalent",
        ),
        _variant(
            "dup-d",
            variant_class="other",
            intent=" normalized within ",
            equivalence_status="reviewed_equivalent",
        ),
        _variant(
            "dup-e",
            variant_class="other",
            intent="Cross exact",
            equivalence_status="reviewed_equivalent",
        ),
        _variant(
            "dup-f",
            variant_class="other",
            intent="Cross normalized!",
            equivalence_status="reviewed_equivalent",
        ),
    ]
    second["variants"] = [
        _variant(
            "dup-g",
            variant_class="other",
            intent="Cross exact",
            equivalence_status="reviewed_equivalent",
        ),
        _variant(
            "dup-h",
            variant_class="other",
            intent=" cross normalized ",
            equivalence_status="reviewed_equivalent",
        ),
    ]

    report = review_gold_dataset(validate_gold_dataset(document))
    codes = {finding.code for finding in report.findings}

    assert {
        "duplicate_variant_within_family",
        "normalized_duplicate_variant_within_family",
        "duplicate_variant_across_families",
        "normalized_duplicate_variant_across_families",
    }.issubset(codes)


def test_v4_importer_rejects_unresolved_historical_mapping_atomically(
    tmp_path: Path,
):
    source_document = yaml.safe_load(
        (ROOT / "benchmarks" / "intent-gold-v4.yaml").read_text(encoding="utf-8")
    )
    selected = next(
        item
        for item in source_document["items"]
        if item["sample_id"] == "small-csv-canonical-en"
    )
    selected["gold"]["preferred_image_id"] = "retired-historical-image"
    selected["gold"]["acceptable_image_ids"] = ["retired-historical-image"]
    source = tmp_path / "historical-with-retired-label.yaml"
    source.write_text(yaml.safe_dump(source_document), encoding="utf-8")
    output = tmp_path / "must-not-exist.yaml"

    with pytest.raises(GoldDatasetValidationError, match="unknown candidate"):
        import_v4_dataset(
            source,
            source_split="test",
            sample_ids=["small-csv-canonical-en"],
        )
    assert not output.exists()
    assert main(
        [
            "import-v4",
            str(source),
            "--source-split",
            "test",
            "--sample-id",
            "small-csv-canonical-en",
            "--output",
            str(output),
        ]
    ) == 2
    assert not output.exists()


def test_v4_importer_populates_only_explicit_or_mechanical_gold():
    source = ROOT / "benchmarks" / "intent-gold-v4.yaml"
    source_document = yaml.safe_load(source.read_text(encoding="utf-8"))
    source_case = next(
        item
        for item in source_document["items"]
        if item["sample_id"] == "pandas-transform-canonical-en"
    )
    imported = import_v4_dataset(
        source,
        sample_ids=["pandas-transform-canonical-en"],
    )
    family = imported.families[0]

    assert family.variants[0].intent == source_case["inputs"]["intent"]
    assert family.gold_structured_intent["task_types"] == []
    assert family.gold_structured_intent["minimum_cpu_cores"] is None
    assert family.gold_structured_intent["minimum_memory_gb"] is None
    assert family.difficulty == "unassessed"
    assert family.label_review["status"] == "pending"
    assert family.source_provenance["source_split"] == "development"
    assert imported.dataset_metadata["role"] == "development"
    assert imported.dataset_metadata["evidence_classification"] == (
        "historical_formative_development_only"
    )


def test_confirmatory_realpath_guard_covers_symlinks_and_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    dataset = validate_gold_dataset(_document(role="confirmatory"))
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    inside = repository / "inside.yaml"
    outside = external / "outside.yaml"
    inside.write_text("placeholder", encoding="utf-8")
    outside.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(gold_dataset_module, "ROOT", repository)

    with pytest.raises(GoldDatasetValidationError, match="absolute external paths"):
        compile_gold_dataset(
            dataset,
            source_path=Path("relative.yaml"),
            output_path=external / "relative-rejected.yaml",
        )
    with pytest.raises(GoldDatasetValidationError, match="absolute external paths"):
        compile_gold_dataset(
            dataset,
            source_path=inside,
            output_path=external / "inside-rejected.yaml",
        )

    assert compile_gold_dataset(
        dataset,
        source_path=outside,
        output_path=external / "allowed.yaml",
    ).split_manifest.role.value == "confirmatory"

    outward_link = repository / "outward.yaml"
    outward_link.symlink_to(outside)
    with pytest.raises(GoldDatasetValidationError, match="absolute external paths"):
        compile_gold_dataset(
            dataset,
            source_path=outward_link,
            output_path=external / "outward-rejected.yaml",
        )

    inward_link = external / "inward.yaml"
    inward_link.symlink_to(inside)
    with pytest.raises(GoldDatasetValidationError, match="absolute external paths"):
        compile_gold_dataset(
            dataset,
            source_path=inward_link,
            output_path=external / "inward-rejected.yaml",
        )

    outward_output = repository / "outward-output.yaml"
    outward_output.symlink_to(outside)
    with pytest.raises(GoldDatasetValidationError, match="absolute external paths"):
        compile_gold_dataset(
            dataset,
            source_path=outside,
            output_path=outward_output,
        )

    inward_output = external / "inward-output.yaml"
    inward_output.symlink_to(inside)
    with pytest.raises(GoldDatasetValidationError, match="absolute external paths"):
        compile_gold_dataset(
            dataset,
            source_path=outside,
            output_path=inward_output,
        )

    inward_directory = external / "repository-directory"
    inward_directory.symlink_to(repository, target_is_directory=True)
    with pytest.raises(GoldDatasetValidationError, match="absolute external paths"):
        compile_gold_dataset(
            dataset,
            source_path=outside,
            output_path=inward_directory / "new-output.yaml",
        )

    chain_target = external / "chain-target.yaml"
    chain_target.symlink_to(inside)
    chain = external / "chain.yaml"
    chain.symlink_to(chain_target)
    with pytest.raises(GoldDatasetValidationError, match="absolute external paths"):
        compile_gold_dataset(
            dataset,
            source_path=chain,
            output_path=external / "chain-rejected.yaml",
        )

    traversal = external / ".." / "repository" / "inside.yaml"
    with pytest.raises(GoldDatasetValidationError, match="absolute external paths"):
        compile_gold_dataset(
            dataset,
            source_path=traversal,
            output_path=external / "traversal-rejected.yaml",
        )


def test_exclusive_writer_resists_late_destination_creation_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "race.json"
    real_link = os.link

    def create_competitor_then_link(source: object, destination: object) -> None:
        Path(destination).write_text("competitor\n", encoding="utf-8")
        real_link(source, destination)

    monkeypatch.setattr(os, "link", create_competitor_then_link)
    with pytest.raises(FileExistsError):
        write_document_exclusive(target, {"safe": True})

    assert target.read_text(encoding="utf-8") == "competitor\n"
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []

    bad_output = tmp_path / "invalid.extension"
    with pytest.raises(GoldDatasetValidationError, match="output must use"):
        write_document_exclusive(bad_output, {"safe": True})
    assert not bad_output.exists()
    assert list(tmp_path.glob(f".{bad_output.name}.*.tmp")) == []


def test_strict_loader_rejects_additional_adversarial_encodings(tmp_path: Path):
    duplicate_json = tmp_path / "duplicate.json"
    duplicate_json.write_text('{"schema_version": "a", "schema_version": "b"}')
    with pytest.raises(GoldDatasetValidationError, match="duplicate JSON field"):
        load_gold_dataset(duplicate_json)

    malformed_utf8 = tmp_path / "malformed.yaml"
    malformed_utf8.write_bytes(b"schema_version: \xff\xfe")
    with pytest.raises(GoldDatasetValidationError, match="UTF-8"):
        load_gold_dataset(malformed_utf8)

    recursive = tmp_path / "recursive.yaml"
    recursive.write_text("root: &root\n  child: *root\n", encoding="utf-8")
    with pytest.raises(GoldDatasetValidationError, match="YAML is invalid|recursive"):
        load_gold_dataset(recursive)

    unsafe = _document()
    unsafe["families"][0]["family_id"] = "../unsafe"  # type: ignore[index]
    with pytest.raises(GoldDatasetValidationError, match="lowercase bounded"):
        validate_gold_dataset(unsafe)


def test_redaction_safe_paths_never_emit_prompt_sentinel(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    sentinel = "TOP_SECRET_PROMPT_SENTINEL_93A7"
    document = _document(lifecycle="draft")
    family = document["families"][0]  # type: ignore[index]
    family["variants"][0]["intent"] = sentinel
    family["difficulty"] = "unassessed"
    _set_pending_review(family)
    family["variants"][1]["equivalence_status"] = "pending_review"
    dataset = validate_gold_dataset(document)

    review_payload = json.dumps(review_gold_dataset(dataset).to_dict())
    assert sentinel not in review_payload
    with pytest.raises(GoldDatasetReviewError) as compile_error:
        compile_gold_dataset(dataset)
    assert sentinel not in str(compile_error.value)

    invalid = deepcopy(document)
    invalid["families"][0]["candidate_gold"][  # type: ignore[index]
        "acceptable_candidate_ids"
    ].append("missing-candidate")
    with pytest.raises(GoldDatasetValidationError) as validation_error:
        validate_gold_dataset(invalid)
    assert sentinel not in str(validation_error.value)

    invalid_source = tmp_path / "sentinel-invalid.yaml"
    write_document_exclusive(invalid_source, invalid)
    assert main(["validate", str(invalid_source)]) == 2
    cli_failure = capsys.readouterr()
    assert sentinel not in cli_failure.out
    assert "Traceback" not in cli_failure.out + cli_failure.err

    source = tmp_path / "sentinel-draft.yaml"
    write_document_exclusive(source, document)
    assert main(["review", str(source), "--format", "json"]) == 1
    assert sentinel not in capsys.readouterr().out
    assert main(["review", str(source), "--format", "markdown"]) == 1
    assert sentinel not in capsys.readouterr().out
    assert main(["compile", str(source), "--output", str(tmp_path / "no.yaml")]) == 2
    assert sentinel not in capsys.readouterr().out

    confirmatory = deepcopy(document)
    confirmatory["dataset_metadata"]["role"] = "confirmatory"
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "sealed.json").write_text(
        json.dumps(confirmatory), encoding="utf-8"
    )
    direct_report = audit_repository(repository)
    assert sentinel not in repr(direct_report.findings)

    (repository / "sealed.json").unlink()
    with zipfile.ZipFile(repository / "sealed.zip", "w") as archive:
        archive.writestr("gold.json", json.dumps(confirmatory))
    archive_report = audit_repository(repository)
    assert sentinel not in repr(archive_report.findings)

    (repository / "sealed.zip").unlink()
    malformed = repository / "malformed.yaml"
    malformed.write_bytes(
        b"schema_version: protocol-v5-gold-family-v1.0.0\n"
        b"dataset_metadata:\n  role: confirmatory\n"
        + sentinel.encode("utf-8")
        + b"\xff"
    )
    with pytest.raises(IsolationAuditError) as isolation_error:
        audit_repository(repository)
    assert sentinel not in str(isolation_error.value)


def _controlled_ambiguity_family() -> dict[str, object]:
    family = _infeasible_family()
    family["family_id"] = "controlled-gpu-ambiguity"
    family["title"] = "Controlled GPU ambiguity"
    family["workload_stratum"] = "ambiguity"
    family["policy_gold"]["expected_feasibility"] = "ambiguous"
    family["gold_structured_intent"]["ambiguities"] = [
        "GPU availability is deliberately unspecified for this robustness case."
    ]
    family["variants"] = [
        _variant(
            "controlled-gpu-ambiguity-case",
            variant_class="optional_ambiguity_variant",
            intent="Train this model; the available accelerator is intentionally unclear.",
            equivalence_status="controlled_ambiguity",
        )
    ]
    return family


def test_complete_temporary_development_authoring_lifecycle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    draft_document = _document(lifecycle="draft")
    draft_document["dataset_metadata"]["dataset_id"] = "synthetic-lifecycle-draft"
    draft_document["review_policy"]["required_workload_strata"].append(
        "missing-advisory-stratum"
    )
    first = draft_document["families"][0]  # type: ignore[index]
    first["variants"].append(
        _variant(
            "table-cleaning-code-context",
            variant_class="optional_code_context",
            intent="Use the shown pandas import to clean the table.",
            code_context=["import pandas as pd"],
            equivalence_status="pending_review",
        )
    )
    first["difficulty"] = "unassessed"
    _set_pending_review(first)
    draft_document["families"].append(_controlled_ambiguity_family())

    draft_path = tmp_path / "draft.yaml"
    write_document_exclusive(draft_path, draft_document)
    assert main(["validate", str(draft_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "VALID"
    assert main(["summary", str(draft_path), "--format", "json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["language_counts"]["vi"] == 1
    assert summary["perturbation_coverage"]["optional_code_context"] == {
        "case_count": 1,
        "family_count": 1,
    }
    assert main(["review", str(draft_path), "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["blocking_count"] > 0

    reviewed_document = deepcopy(draft_document)
    reviewed_document["dataset_metadata"]["lifecycle"] = "reviewed"
    reviewed_first = reviewed_document["families"][0]
    reviewed_first["difficulty"] = "medium"
    reviewed_first["label_review"] = {
        "status": "approved",
        "reviewed_by": "synthetic-reviewer",
        "reviewed_at_utc": "2026-08-24T01:00:00Z",
        "notes": ["Synthetic lifecycle fixture was manually adjudicated."],
    }
    for variant in reviewed_first["variants"]:
        if variant["equivalence_status"] == "pending_review":
            variant["equivalence_status"] = "reviewed_equivalent"
    reviewed_path = tmp_path / "reviewed.json"
    write_document_exclusive(reviewed_path, reviewed_document)
    reviewed = load_gold_dataset(reviewed_path)
    assert not review_gold_dataset(reviewed).blocking_findings
    with pytest.raises(GoldDatasetReviewError, match="manually frozen"):
        compile_gold_dataset(reviewed)
    assert reviewed.dataset.dataset_metadata["lifecycle"] == "reviewed"

    frozen_document = deepcopy(reviewed_document)
    frozen_document["dataset_metadata"]["lifecycle"] = "frozen"
    frozen_document["dataset_metadata"]["freeze_metadata"] = {
        "frozen_at_utc": "2026-08-24T02:00:00Z",
        "frozen_by": "synthetic-custodian",
    }
    frozen_path = tmp_path / "frozen.yaml"
    compiled_path = tmp_path / "compiled.json"
    write_document_exclusive(frozen_path, frozen_document)
    assert main(["compile", str(frozen_path), "--output", str(compiled_path)]) == 0
    compile_status = json.loads(capsys.readouterr().out)
    assert compile_status["status"] == "COMPILED"
    compiled_payload = json.loads(compiled_path.read_text(encoding="utf-8"))
    compiled = validate_split_bundle(compiled_payload)
    assert compiled.split_manifest.checksum == compile_status["checksum"]
    assert compiled.split_manifest.family_count == 3
    assert any(
        case.variant_metadata["equivalence_status"] == "controlled_ambiguity"
        for case in compiled.cases
    )
    assert all(
        case.source_provenance["source_dataset_id"] == "synthetic-lifecycle-draft"
        for case in compiled.cases
    )
    assert frozen_document["dataset_metadata"]["lifecycle"] == "frozen"
