from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

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
from evaluation_v5.isolation_audit import audit_repository
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
    assert "normalized_identical_variant" in codes
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
    assert main(["review", str(output), "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["blocking_count"] > 0

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
