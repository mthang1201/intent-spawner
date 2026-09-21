from __future__ import annotations

from argparse import Namespace
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from evaluation_v5 import isolation as isolation_module
from evaluation_v5 import split_dataset as split_dataset_module
from evaluation_v5.isolation import (
    CONFIRMATORY_DATASET_ENV_VAR,
    SplitContaminationError,
    SplitIsolationError,
    VerifiedConfirmatorySplit,
    check_contamination,
    load_confirmatory_split,
    normalize_prompt,
    require_external_dataset_path,
    resolve_confirmatory_sources,
    verify_confirmatory_split,
)
from evaluation_v5.offline.run import main as offline_main, run_preflight
from evaluation_v5.offline.runner import run_offline_recommendations
from evaluation_v5.split_dataset import (
    DEFAULT_DEVELOPMENT_DATASET,
    LoadedSplit,
    SPLIT_BUNDLE_SCHEMA_VERSION,
    SplitBundle,
    SplitBundleValidationError,
    SplitRole,
    load_development_split,
    split_bundle_checksum,
    validate_split_bundle,
)
from recommender.deployment import RUNTIME_FILES


ROOT = Path(__file__).resolve().parents[1]


def _case(
    *,
    case_id: str = "sealed-case-001",
    family_id: str = "sealed-family-001",
    prompt: str = "A novel sealed request with unrelated requirements.",
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "family_id": family_id,
        "variant_id": "canonical",
        "language": "en",
        "prompt": prompt,
        "inputs": {"dataset_size_gb": 0.1, "code_context_hints": []},
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
            "source_dataset_id": "synthetic-test-only",
            "source_schema_version": "synthetic-test-v1",
            "source_case_id": case_id,
            "source_split": "confirmatory",
            "evidence_classification": "synthetic_test_fixture_not_evidence",
        },
    }


def _document(
    *,
    role: str = "confirmatory",
    split_id: str = "v5-confirmatory",
    cases: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    selected_cases = [_case()] if cases is None else cases
    families = sorted({str(case["family_id"]) for case in selected_cases})
    document: dict[str, object] = {
        "schema_version": SPLIT_BUNDLE_SCHEMA_VERSION,
        "split_manifest": {
            "dataset_id": "synthetic-sealed-test-v1",
            "split_id": split_id,
            "role": role,
            "family_ids": families,
            "case_count": len(selected_cases),
            "family_count": len(families),
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
        "cases": selected_cases,
    }
    document["split_manifest"]["checksum"] = split_bundle_checksum(document)  # type: ignore[index]
    return document


def _bundle(**kwargs: object) -> SplitBundle:
    return validate_split_bundle(_document(**kwargs))


def _write_bundle(path: Path, document: dict[str, object] | None = None) -> Path:
    path.write_text(
        yaml.safe_dump(document or _document(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _args(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "split": "development",
        "split_id": None,
        "dataset": None,
        "similarity_threshold": 0.90,
    }
    values.update(overrides)
    return Namespace(**values)


def test_tracked_development_bundle_is_exactly_the_selected_visible_material():
    loaded = load_development_split()
    assert loaded.manifest.split_id == "v5-development"
    assert loaded.manifest.role is SplitRole.DEVELOPMENT
    assert loaded.manifest.case_count == 18
    assert loaded.manifest.family_count == 10
    assert loaded.manifest.family_ids == tuple(sorted(loaded.manifest.family_ids))
    assert all(
        case.source_provenance["evidence_classification"]
        == "historical_formative_development_only"
        for case in loaded.bundle.cases
    )
    assert not any(
        case.source_provenance["source_split"] == "test"
        for case in loaded.bundle.cases
    )

    v4 = yaml.safe_load(
        (ROOT / "benchmarks/intent-gold-v4.yaml").read_text(encoding="utf-8")
    )
    p2 = yaml.safe_load(
        (ROOT / "benchmarks/p2-infeasible-supplement-v1.yaml").read_text(
            encoding="utf-8"
        )
    )
    expected_sources = {
        (v4["dataset_id"], item["sample_id"], "development")
        for item in v4["items"]
        if item["split"] == "development"
    } | {
        (p2["dataset_id"], item["sample_id"], "development")
        for item in p2["items"]
    }
    actual_sources = {
        (
            case.source_provenance["source_dataset_id"],
            case.source_provenance["source_case_id"],
            case.source_provenance["source_split"],
        )
        for case in loaded.bundle.cases
    }
    assert actual_sources == expected_sources


def test_split_schema_rejects_checksum_counts_family_list_and_duplicate_ids():
    for mutation in ("checksum", "count", "families", "duplicate"):
        document = _document()
        if mutation == "checksum":
            document["split_manifest"]["checksum"] = "f" * 64  # type: ignore[index]
        elif mutation == "count":
            document["split_manifest"]["case_count"] = 2  # type: ignore[index]
            document["split_manifest"]["checksum"] = split_bundle_checksum(document)  # type: ignore[index]
        elif mutation == "families":
            document["split_manifest"]["family_ids"] = ["wrong-family"]  # type: ignore[index]
            document["split_manifest"]["checksum"] = split_bundle_checksum(document)  # type: ignore[index]
        else:
            document = _document(cases=[_case(), _case()])
        with pytest.raises(SplitBundleValidationError):
            validate_split_bundle(document)


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_case_field",
        "extra_case_field",
        "invalid_role",
        "invalid_timestamp",
        "invalid_language",
        "unknown_profile",
        "invalid_expected_extraction",
        "unsafe_provenance_id",
        "invalid_original_provenance",
    ),
)
def test_split_schema_rejects_invalid_or_noncanonical_fields(mutation: str):
    document = _document()
    case = document["cases"][0]  # type: ignore[index]
    manifest = document["split_manifest"]  # type: ignore[index]
    if mutation == "missing_case_field":
        case.pop("variant_id")
    elif mutation == "extra_case_field":
        case["unexpected"] = True
    elif mutation == "invalid_role":
        manifest["role"] = "test"
    elif mutation == "invalid_timestamp":
        manifest["creation_metadata"]["created_at_utc"] = "2026-08-22"  # type: ignore[index]
    elif mutation == "invalid_language":
        case["language"] = "not a language tag"
    elif mutation == "unknown_profile":
        case["gold"]["allowed_profiles"] = ["xlarge"]  # type: ignore[index]
    elif mutation == "invalid_expected_extraction":
        case["gold"]["expected_extraction"] = {  # type: ignore[index]
            "gpu_requirement": "unspecified",
            "minimum_cpu_cores": None,
            "minimum_memory_gb": None,
            "required_libraries": [],
            "unexpected": True,
        }
    elif mutation == "unsafe_provenance_id":
        case["source_provenance"]["source_dataset_id"] = "../private"  # type: ignore[index]
    else:
        case["source_provenance"]["original_provenance"] = "not-an-object"  # type: ignore[index]
    manifest["checksum"] = split_bundle_checksum(document)
    with pytest.raises(SplitBundleValidationError):
        validate_split_bundle(document)


def test_split_schema_rejects_non_json_keys_and_recursive_provenance_safely():
    non_string_key = _document()
    non_string_key["cases"][0][7] = "private-field-name"  # type: ignore[index]
    with pytest.raises(SplitBundleValidationError) as non_string_error:
        validate_split_bundle(non_string_key)
    assert "private-field-name" not in str(non_string_error.value)

    recursive = _document()
    provenance = recursive["cases"][0]["source_provenance"]  # type: ignore[index]
    provenance["recursive"] = provenance
    with pytest.raises(SplitBundleValidationError, match="recursive data"):
        validate_split_bundle(recursive)


def test_canonical_checksum_is_reproducible_and_preserves_semantic_order():
    document = _document()
    expected = split_bundle_checksum(document)

    reordered = {
        key: document[key]
        for key in reversed(tuple(document))
    }
    reordered["split_manifest"] = {
        key: document["split_manifest"][key]  # type: ignore[index]
        for key in reversed(tuple(document["split_manifest"]))  # type: ignore[arg-type]
    }
    assert split_bundle_checksum(reordered) == expected
    assert split_bundle_checksum(yaml.safe_load(yaml.safe_dump(document))) == expected

    first = _case(case_id="case-a", family_id="family-a", prompt="Café")
    second = _case(case_id="case-b", family_id="family-b", prompt="Second case")
    ordered = _document(cases=[first, second])
    reversed_cases = copy.deepcopy(ordered)
    reversed_cases["cases"] = list(reversed(reversed_cases["cases"]))  # type: ignore[arg-type]
    assert split_bundle_checksum(reversed_cases) != split_bundle_checksum(ordered)

    exact_string = copy.deepcopy(document)
    exact_string["cases"][0]["prompt"] += "\n"  # type: ignore[index,operator]
    assert split_bundle_checksum(exact_string) != expected

    decomposed_unicode = _document(
        cases=[_case(prompt="Café")],
    )
    composed_unicode = _document(
        cases=[_case(prompt="Café")],
    )
    assert split_bundle_checksum(decomposed_unicode) != split_bundle_checksum(
        composed_unicode
    )

    integer_number = copy.deepcopy(document)
    integer_number["cases"][0]["inputs"]["dataset_size_gb"] = 1  # type: ignore[index]
    floating_number = copy.deepcopy(integer_number)
    floating_number["cases"][0]["inputs"]["dataset_size_gb"] = 1.0  # type: ignore[index]
    assert split_bundle_checksum(integer_number) != split_bundle_checksum(
        floating_number
    )


@pytest.mark.parametrize("alias", ("development", "v5-development"))
def test_default_development_preflight_is_not_executed(alias: str):
    result = run_preflight(_args(split=alias), environ={})
    assert result["status"] == "NOT_EXECUTED"
    assert result["experiment_executed"] is False
    assert result["claims_permitted"] is False
    assert result["split"]["case_count"] == 18
    assert "freeze_id" not in result


@pytest.mark.parametrize("source", ["cli", "environment"])
def test_development_command_cannot_touch_confirmatory_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
):
    sealed = tmp_path / "must-not-open.yaml"
    sealed.write_text("this is deliberately not YAML: [", encoding="utf-8")
    monkeypatch.setattr(
        "evaluation_v5.offline.run.load_development_split",
        lambda **_kwargs: pytest.fail("development loader must not run"),
    )
    args = _args(dataset=sealed) if source == "cli" else _args()
    environ = (
        {}
        if source == "cli"
        else {CONFIRMATORY_DATASET_ENV_VAR: str(sealed)}
    )
    with pytest.raises(SplitIsolationError):
        run_preflight(args, environ=environ)


def test_planted_confirmatory_file_is_not_discovered_by_development_or_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    synthetic_checkout = tmp_path / "synthetic-checkout"
    benchmark_root = synthetic_checkout / "benchmarks_v5"
    benchmark_root.mkdir(parents=True)
    planted = _write_bundle(benchmark_root / "v5-confirmatory.yaml")
    sentinel = planted.read_text(encoding="utf-8")
    planted_resolved = planted.resolve()
    original_path_open = Path.open
    original_os_open = split_dataset_module.os.open

    def guarded_path_open(path: Path, *args: object, **kwargs: object):
        if path.resolve() == planted_resolved:
            pytest.fail("development/index code opened a planted confirmatory bundle")
        return original_path_open(path, *args, **kwargs)

    def guarded_os_open(path: object, *args: object, **kwargs: object):
        if Path(path).resolve() == planted_resolved:
            pytest.fail("development/index code opened a planted confirmatory bundle")
        return original_os_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(split_dataset_module.os, "open", guarded_os_open)

    monkeypatch.chdir(synthetic_checkout)
    development = run_preflight(_args(split="development"), environ={})

    assert development["split"]["split_id"] == "v5-development"
    assert development["split"]["role"] == "development"
    with original_path_open(planted, "r", encoding="utf-8") as handle:
        assert handle.read() == sentinel
    assert not (synthetic_checkout / "evaluation_v5/cache").exists()
    assert not (synthetic_checkout / "evaluation_v5/indexes").exists()


def test_confirmatory_sources_are_explicit_and_unambiguous(tmp_path: Path):
    with pytest.raises(SplitIsolationError, match="both CLI and environment"):
        run_preflight(
            _args(split="confirmatory", dataset=tmp_path / "one.yaml"),
            environ={CONFIRMATORY_DATASET_ENV_VAR: str(tmp_path / "two.yaml")},
        )
    with pytest.raises(SplitIsolationError, match="requires --dataset"):
        run_preflight(_args(split="confirmatory"), environ={})
    with pytest.raises(SplitIsolationError, match="requires --dataset"):
        run_preflight(_args(split="v5-confirmatory"), environ={})
    with pytest.raises(SplitIsolationError, match="non-blank"):
        run_preflight(
            _args(split="confirmatory"),
            environ={CONFIRMATORY_DATASET_ENV_VAR: ""},
        )

    dataset = tmp_path / "sealed.yaml"
    assert resolve_confirmatory_sources(dataset_path=dataset, environ={}) == dataset
    assert resolve_confirmatory_sources(
        dataset_path=None,
        environ={CONFIRMATORY_DATASET_ENV_VAR: str(dataset)},
    ) == dataset


def test_public_cli_confirmation_fails_closed_without_external_inputs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.delenv(CONFIRMATORY_DATASET_ENV_VAR, raising=False)

    assert offline_main(["--split", "confirmatory"]) == 2
    missing_dataset = json.loads(capsys.readouterr().out)
    assert missing_dataset["status"] == "ERROR"
    assert "requires --dataset" in missing_dataset["error"]


def test_public_cli_cannot_use_a_repository_bundled_dataset(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.delenv(CONFIRMATORY_DATASET_ENV_VAR, raising=False)

    assert offline_main(
        ["--split", "confirmatory", "--dataset", str(DEFAULT_DEVELOPMENT_DATASET)]
    ) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ERROR"
    assert "outside the repository" in result["error"]


def test_in_repository_and_symlink_resolved_paths_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    sealed = _write_bundle(repository / "sealed.yaml")
    monkeypatch.setattr(isolation_module, "ROOT", repository)
    with pytest.raises(SplitIsolationError, match="outside the repository"):
        require_external_dataset_path(sealed)

    link = tmp_path / "external-link.yaml"
    link.symlink_to(sealed)
    with pytest.raises(SplitIsolationError, match="resolve inside"):
        require_external_dataset_path(link)


def test_ancestor_symlink_swap_between_resolution_and_open_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    external_directory = tmp_path / "external-custody"
    external_directory.mkdir()
    sealed = _write_bundle(external_directory / "sealed.yaml")
    _write_bundle(repository / sealed.name)
    moved_directory = tmp_path / "moved-external-custody"

    monkeypatch.setattr(isolation_module, "ROOT", repository)
    original_guard = isolation_module.require_external_dataset_path

    def swap_ancestor(path: Path) -> Path:
        resolved = original_guard(path)
        external_directory.rename(moved_directory)
        external_directory.symlink_to(repository, target_is_directory=True)
        return resolved

    monkeypatch.setattr(
        isolation_module,
        "require_external_dataset_path",
        swap_ancestor,
    )

    with pytest.raises(SplitBundleValidationError) as error:
        load_confirmatory_split(sealed)
    encoded_error = str(error.value)
    assert "external-custody" not in encoded_error
    assert "repository" not in encoded_error


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("role", "role mismatch"),
        ("split_id", "split ID mismatch"),
        ("checksum", "checksum"),
    ],
)
def test_confirmatory_loader_rejects_wrong_role_id_or_checksum(
    tmp_path: Path,
    mutation: str,
    message: str,
):
    document = _document()
    if mutation == "role":
        document = _document(role="development")
    elif mutation == "split_id":
        document = _document(split_id="v5-confirmatory-other")
    else:
        document["split_manifest"]["checksum"] = "e" * 64  # type: ignore[index]
    sealed = _write_bundle(tmp_path / "sealed.yaml", document)
    with pytest.raises(SplitBundleValidationError, match=message):
        load_confirmatory_split(sealed)


def test_confirmatory_loader_accepts_an_explicit_future_safe_split_id(
    tmp_path: Path,
):
    sealed = _write_bundle(
        tmp_path / "sealed.yaml",
        _document(split_id="v5-confirmatory-replication-2"),
    )

    loaded = load_confirmatory_split(
        sealed,
        expected_split_id="v5-confirmatory-replication-2",
    )

    assert loaded.split.manifest.split_id == "v5-confirmatory-replication-2"


def test_constructed_confirmatory_loaded_split_cannot_authorize_runner(
    tmp_path: Path,
):
    constructed = LoadedSplit(
        bundle=_bundle(),
        source_file_sha256="a" * 64,
    )

    with pytest.raises(PermissionError, match="cannot authorize confirmatory"):
        run_offline_recommendations(
            constructed,
            result_dir=tmp_path / "constructed-run",
            system_ids=("P1",),
            frozen_configuration={"snapshot": "design-only"},
            dry_run=True,
        )


def test_dataclasses_replace_development_to_confirmatory_cannot_authorize_runner(
    tmp_path: Path,
):
    development = load_development_split()
    relabelled_manifest = replace(
        development.manifest,
        split_id="v5-confirmatory",
        role=SplitRole.CONFIRMATORY,
    )
    relabelled = replace(
        development,
        bundle=replace(
            development.bundle,
            split_manifest=relabelled_manifest,
        ),
    )

    with pytest.raises(PermissionError, match="cannot authorize confirmatory"):
        run_offline_recommendations(
            relabelled,
            result_dir=tmp_path / "relabelled-run",
            system_ids=("P1",),
            frozen_configuration={"snapshot": "design-only"},
            dry_run=True,
        )


def test_arbitrary_freeze_identity_string_cannot_authorize_runner(tmp_path: Path):
    with pytest.raises(ValueError, match="caller-supplied freeze_identity"):
        run_offline_recommendations(
            load_development_split(),
            result_dir=tmp_path / "fake-freeze-run",
            system_ids=("P1",),
            frozen_configuration={"snapshot": "development"},
            freeze_identity={"freeze_id": "foo"},
            dry_run=True,
        )


def test_confirmatory_bundle_is_opened_once_and_hashes_the_parsed_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    sealed = _write_bundle(tmp_path / "sealed.yaml")
    original_open = split_dataset_module.os.open
    sealed_opens = 0

    def counted_open(path: object, flags: int, *args: object, **kwargs: object):
        nonlocal sealed_opens
        if str(path) == sealed.name and kwargs.get("dir_fd") is not None:
            sealed_opens += 1
        return original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(split_dataset_module.os, "open", counted_open)
    loaded = load_confirmatory_split(sealed)

    assert sealed_opens == 1
    assert loaded.split.source_file_sha256 == hashlib.sha256(
        sealed.read_bytes()
    ).hexdigest()


def test_invalid_similarity_option_is_rejected_before_sealed_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        isolation_module,
        "require_external_dataset_path",
        lambda _path: pytest.fail("sealed path must not be inspected"),
    )
    with pytest.raises(ValueError, match="between 0 and 1"):
        load_confirmatory_split(
            tmp_path / "sealed.yaml",
            similarity_threshold=float("nan"),
        )


@pytest.mark.parametrize(
    ("kind", "expected_category"),
    [
        ("case", "case_id_overlap"),
        ("family", "family_id_overlap"),
        ("exact", "exact_prompt_duplicate"),
        ("normalized", "normalized_prompt_duplicate"),
    ],
)
def test_contamination_blockers(kind: str, expected_category: str):
    development = load_development_split().bundle
    baseline = development.cases[0]
    case_id = baseline.case_id if kind == "case" else "sealed-unique-case"
    family_id = baseline.family_id if kind == "family" else "sealed-unique-family"
    if kind == "exact":
        prompt = baseline.prompt
    elif kind == "normalized":
        prompt = "  I ONLY need a notebook for a few basic Python calculations!!! "
    else:
        prompt = "A sealed workload whose wording has no development analogue."
    confirmatory = _bundle(
        cases=[_case(case_id=case_id, family_id=family_id, prompt=prompt)]
    )
    with pytest.raises(SplitContaminationError) as error:
        check_contamination(development, confirmatory)
    assert error.value.report.has_blocking_contamination
    assert expected_category in error.value.report.blocking_categories


def test_high_textual_similarity_is_safe_review_not_rejection():
    development = load_development_split().bundle
    baseline = development.cases[0]
    prompt = baseline.prompt + " Please."
    confirmatory = _bundle(cases=[_case(prompt=prompt)])
    report = check_contamination(development, confirmatory)
    encoded = json.dumps(report.to_safe_dict(), sort_keys=True)
    assert not report.has_blocking_contamination
    assert report.similarity_review_pairs
    assert baseline.prompt not in encoded
    assert prompt not in encoded

    threshold_only = check_contamination(
        development,
        _bundle(
            cases=[
                _case(
                    case_id="threshold-only-case",
                    family_id="threshold-only-family",
                    prompt="Text with no prohibited identity or exact duplicate.",
                )
            ]
        ),
        similarity_threshold=0.0,
    )
    assert threshold_only.similarity_review_pairs
    assert not threshold_only.has_blocking_contamination
    assert threshold_only.to_safe_dict()["blocking_checks_passed"] is True


def test_contamination_intersections_are_symmetric_after_supply(tmp_path: Path):
    sealed = _write_bundle(tmp_path / "sealed.yaml")
    supplied = load_confirmatory_split(sealed)
    confirmatory_case = supplied.split.bundle.cases[0]
    development = supplied.development_split.bundle
    modified_document = development.to_dict()
    modified_document["cases"][0]["case_id"] = confirmatory_case.case_id
    modified_document["cases"][0]["family_id"] = confirmatory_case.family_id
    modified_document["cases"][0]["prompt"] = confirmatory_case.prompt
    modified_document["split_manifest"]["family_ids"] = sorted(
        {case["family_id"] for case in modified_document["cases"]}
    )
    modified_document["split_manifest"]["family_count"] = len(
        modified_document["split_manifest"]["family_ids"]
    )
    modified_document["split_manifest"]["checksum"] = split_bundle_checksum(
        modified_document
    )
    modified_development = validate_split_bundle(
        modified_document,
        expected_role=SplitRole.DEVELOPMENT,
    )

    for first, second in (
        (modified_development, supplied.split.bundle),
        (supplied.split.bundle, modified_development),
    ):
        with pytest.raises(SplitContaminationError) as error:
            check_contamination(first, second)
        assert set(error.value.report.blocking_categories) >= {
            "case_id_overlap",
            "family_id_overlap",
            "exact_prompt_duplicate",
        }


def test_normalized_duplicate_check_preserves_semantic_symbols():
    assert normalize_prompt("Use C++ for this workload") != normalize_prompt(
        "Use C for this workload"
    )
    assert normalize_prompt("GPU ✅ is required") != normalize_prompt(
        "GPU ❌ is required"
    )


def test_verified_confirmatory_preparation_and_runner_reverification_succeeds(
    tmp_path: Path,
):
    sealed = _write_bundle(tmp_path / "sealed-confirmatory.yaml")

    capability = load_confirmatory_split(sealed)
    assert isinstance(capability, VerifiedConfirmatorySplit)
    reverified = verify_confirmatory_split(capability)
    assert (
        reverified.provenance_identity["split"]["split_id"] == "v5-confirmatory"
    )
    with pytest.raises(TypeError):
        replace(capability, split=load_development_split())

    result = run_offline_recommendations(
        capability,
        result_dir=tmp_path / "verified-confirmatory-dry-run",
        system_ids=("P1", "P2"),
        frozen_configuration={"snapshot": "design-only"},
        dry_run=True,
    )
    assert result.dry_run is True
    assert result.planned_records == capability.split.manifest.case_count * 2


def test_confirmatory_split_capability_is_revalidated_after_dataset_tampering(
    tmp_path: Path,
):
    dataset_path = tmp_path / "sealed-confirmatory.yaml"
    sealed = _write_bundle(dataset_path)
    capability = load_confirmatory_split(sealed)

    tampered = _document(cases=[_case(prompt="tampered after capture")])
    _write_bundle(dataset_path, tampered)

    with pytest.raises(SplitIsolationError, match="no longer matches"):
        verify_confirmatory_split(capability)


def test_confirmatory_runner_now_permits_adapter_injection(tmp_path: Path):
    from evaluation_v5.offline.recommenders import default_adapters

    sealed = _write_bundle(tmp_path / "sealed-confirmatory.yaml")
    capability = load_confirmatory_split(sealed)

    result = run_offline_recommendations(
        capability,
        result_dir=tmp_path / "injected-adapter",
        system_ids=("P1",),
        adapters=default_adapters(),
        frozen_configuration={"snapshot": "design-only"},
        dry_run=True,
    )
    assert result.dry_run is True


def test_docker_and_runtime_packages_are_allowlisted_away_from_v5_data():
    dockerfile_path = ROOT / "cluster_evaluation/Dockerfile"
    dockerfile = dockerfile_path.read_text(encoding="utf-8")
    dockerignore = Path(f"{dockerfile_path}.dockerignore").read_text(encoding="utf-8")
    assert "COPY benchmarks /app/benchmarks" not in dockerfile
    assert "COPY benchmarks/__init__.py benchmarks/workload_runner.py" in dockerfile
    assert dockerignore.splitlines()[0] == "*"
    prohibited = ("evaluation_v5", "benchmarks_v5", "results_v5", "tests", "cache")
    dockerfiles = sorted(
        path
        for path in (ROOT / "cluster_evaluation").glob("Dockerfile*")
        if not path.name.endswith(".dockerignore")
    )
    assert {path.name for path in dockerfiles} == {
        "Dockerfile",
        "Dockerfile.jupyter-v3",
        "Dockerfile.resource-v5",
        "Dockerfile.v3",
    }
    for selected_dockerfile in dockerfiles:
        instructions = selected_dockerfile.read_text(encoding="utf-8").splitlines()
        context_rules = Path(f"{selected_dockerfile}.dockerignore").read_text(
            encoding="utf-8"
        ).splitlines()
        assert context_rules[0] == "*"
        copy_or_add = [
            line.strip()
            for line in instructions
            if line.strip().startswith(("COPY ", "ADD "))
        ]
        assert copy_or_add
        if selected_dockerfile.name == "Dockerfile.resource-v5":
            allowed_v5_inputs = {
                "evaluation_v5/resource/__init__.py",
                "evaluation_v5/resource/models.py",
                "evaluation_v5/resource/manifest.py",
                "evaluation_v5/resource/workloads.py",
                "evaluation_v5/resource/planner.py",
                "evaluation_v5/resource/derive.py",
                "evaluation_v5/resource/pod_runner.py",
                "benchmarks_v5/resource-envelope-workloads-v1.yaml",
            }
            exceptions = {
                line[1:]
                for line in context_rules
                if line.startswith("!")
                and (line[1:].startswith("evaluation_v5/") or line[1:].startswith("benchmarks_v5/"))
            }
            assert exceptions == allowed_v5_inputs
            encoded = "\n".join(instructions)
            assert "recommender/" not in encoded
            assert "v5-development" not in encoded
            assert "results_v5" not in encoded
            assert "tests" not in encoded
            assert "evaluation_v5/cache" not in encoded
            continue
        assert not any(
            any(token in rule for token in prohibited)
            for rule in context_rules
            if rule.startswith("!")
        )
        assert not any(
            line in {"COPY . /app", "COPY . .", "ADD . /app", "ADD . ."}
            or any(token in line for token in prohibited)
            for line in copy_or_add
        )

    root_dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for protected in (
        "benchmarks_v5/",
        "results_v5/",
        "tests/",
        ".protocol-v5-private/",
        "evaluation_v5/cache/",
        "evaluation_v5/indexes/",
    ):
        assert protected in root_dockerignore
    assert not any(any(token in name for token in prohibited) for name in RUNTIME_FILES)
    assert not any(
        (ROOT / name).exists()
        for name in ("pyproject.toml", "setup.py", "setup.cfg", "MANIFEST.in")
    )
    assert DEFAULT_DEVELOPMENT_DATASET.parent.name == "benchmarks_v5"
