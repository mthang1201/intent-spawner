from __future__ import annotations

from argparse import Namespace
import copy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile

import pytest
import yaml

from evaluation_v5 import freeze as freeze_module
from evaluation_v5 import isolation as isolation_module
from evaluation_v5 import isolation_audit as isolation_audit_module
from evaluation_v5 import split_dataset as split_dataset_module
from evaluation_v5.freeze import (
    DRY_RUN_STATUS,
    FREEZE_ARTIFACT_BASENAME,
    FREEZE_SCHEMA_VERSION,
    FreezeValidationError,
    build_configuration_snapshot,
    build_freeze_manifest,
    create_freeze_artifact,
    verify_freeze_artifact,
)
from evaluation_v5.isolation import (
    CONFIRMATORY_DATASET_ENV_VAR,
    FREEZE_ARTIFACT_ENV_VAR,
    SplitContaminationError,
    SplitIsolationError,
    check_contamination,
    load_confirmatory_split,
    normalize_prompt,
    require_external_dataset_path,
    resolve_confirmatory_sources,
)
from evaluation_v5.isolation_audit import IsolationAuditError, audit_repository
from evaluation_v5.offline.run import main as offline_main, run_preflight
from evaluation_v5.split_dataset import (
    DEFAULT_DEVELOPMENT_DATASET,
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
FIXED_REVISION = "a" * 40


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


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


def _production_freeze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    freeze_id: str = "fixture-freeze",
    p3_gate_status: str = "not_retained",
) -> Path:
    monkeypatch.delenv(CONFIRMATORY_DATASET_ENV_VAR, raising=False)
    monkeypatch.setattr(freeze_module, "_git_state", lambda: (FIXED_REVISION, True))
    freeze_root = tmp_path / "freezes"
    monkeypatch.setattr(freeze_module, "DEFAULT_FREEZE_ROOT", freeze_root)
    monkeypatch.setattr(freeze_module, "FREEZE_CUSTODY_ROOT", tmp_path)
    return create_freeze_artifact(
        freeze_id=freeze_id,
        p3_gate_status=p3_gate_status,
        output_root=freeze_root,
    )


def _args(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "split": "development",
        "split_id": None,
        "dataset": None,
        "freeze": None,
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

    first = _case(case_id="case-a", family_id="family-a", prompt="Caf\u00e9")
    second = _case(case_id="case-b", family_id="family-b", prompt="Second case")
    ordered = _document(cases=[first, second])
    reversed_cases = copy.deepcopy(ordered)
    reversed_cases["cases"] = list(reversed(reversed_cases["cases"]))  # type: ignore[arg-type]
    assert split_bundle_checksum(reversed_cases) != split_bundle_checksum(ordered)

    exact_string = copy.deepcopy(document)
    exact_string["cases"][0]["prompt"] += "\n"  # type: ignore[index,operator]
    assert split_bundle_checksum(exact_string) != expected

    decomposed_unicode = _document(
        cases=[_case(prompt="Cafe\u0301")],
    )
    composed_unicode = _document(
        cases=[_case(prompt="Caf\u00e9")],
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
    assert result["freeze_id"] is None


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

    before = build_configuration_snapshot(
        p3_gate_status="not_retained",
        p3_gate_evidence=freeze_module.DEFAULT_P3_GATE_EVIDENCE,
    )["indexes"]
    monkeypatch.chdir(synthetic_checkout)
    development = run_preflight(_args(split="development"), environ={})
    after = build_configuration_snapshot(
        p3_gate_status="not_retained",
        p3_gate_evidence=freeze_module.DEFAULT_P3_GATE_EVIDENCE,
    )["indexes"]

    assert development["split"]["split_id"] == "v5-development"
    assert development["split"]["role"] == "development"
    assert before == after
    with original_path_open(planted, "r", encoding="utf-8") as handle:
        assert handle.read() == sentinel
    assert not (synthetic_checkout / "evaluation_v5/cache").exists()
    assert not (synthetic_checkout / "evaluation_v5/indexes").exists()


def test_confirmation_without_freeze_fails_before_dataset_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    sealed = tmp_path / "must-not-open.yaml"
    sealed.write_text("sealed sentinel", encoding="utf-8")
    freeze_root = tmp_path / "freezes"
    monkeypatch.setattr(freeze_module, "DEFAULT_FREEZE_ROOT", freeze_root)
    monkeypatch.setattr(freeze_module, "FREEZE_CUSTODY_ROOT", tmp_path)
    missing_freeze = freeze_root / "missing-freeze" / FREEZE_ARTIFACT_BASENAME
    monkeypatch.setattr(
        isolation_module,
        "require_external_dataset_path",
        lambda *_args, **_kwargs: pytest.fail(
            "sealed dataset path was inspected before freeze verification"
        ),
    )
    with pytest.raises(FreezeValidationError, match="prior production freeze"):
        load_confirmatory_split(sealed, missing_freeze)


def test_confirmatory_sources_are_explicit_and_unambiguous(tmp_path: Path):
    with pytest.raises(SplitIsolationError, match="both CLI and environment"):
        run_preflight(
            _args(
                split="confirmatory",
                dataset=tmp_path / "one.yaml",
                freeze=tmp_path / "freeze.json",
            ),
            environ={CONFIRMATORY_DATASET_ENV_VAR: str(tmp_path / "two.yaml")},
        )
    with pytest.raises(SplitIsolationError, match="both CLI and environment"):
        run_preflight(
            _args(split="confirmatory", dataset=tmp_path / "one.yaml"),
            environ={FREEZE_ARTIFACT_ENV_VAR: str(tmp_path / "freeze.json")},
        )
    with pytest.raises(SplitIsolationError, match="both CLI and environment"):
        run_preflight(
            _args(split="confirmatory", freeze=tmp_path / "freeze.json"),
            environ={CONFIRMATORY_DATASET_ENV_VAR: str(tmp_path / "one.yaml")},
        )
    with pytest.raises(SplitIsolationError, match="requires --dataset"):
        run_preflight(_args(split="confirmatory"), environ={})
    with pytest.raises(SplitIsolationError, match="requires --dataset"):
        run_preflight(_args(split="v5-confirmatory"), environ={})
    with pytest.raises(SplitIsolationError, match="both CLI and environment"):
        run_preflight(
            _args(
                split="confirmatory",
                dataset=tmp_path / "one.yaml",
                freeze=tmp_path / "freeze.json",
            ),
            environ={CONFIRMATORY_DATASET_ENV_VAR: ""},
        )
    with pytest.raises(SplitIsolationError, match="non-blank"):
        run_preflight(
            _args(split="confirmatory", freeze=tmp_path / "freeze.json"),
            environ={CONFIRMATORY_DATASET_ENV_VAR: ""},
        )
    same_input = tmp_path / "same-input"
    with pytest.raises(SplitIsolationError, match="distinct inputs"):
        run_preflight(
            _args(
                split="confirmatory",
                dataset=same_input,
                freeze=same_input,
            ),
            environ={},
        )

    dataset = tmp_path / "sealed.yaml"
    freeze = tmp_path / FREEZE_ARTIFACT_BASENAME
    assert resolve_confirmatory_sources(
        dataset_path=dataset,
        freeze_path=freeze,
        environ={},
    ) == (dataset, freeze)
    assert resolve_confirmatory_sources(
        dataset_path=None,
        freeze_path=None,
        environ={
            CONFIRMATORY_DATASET_ENV_VAR: str(dataset),
            FREEZE_ARTIFACT_ENV_VAR: str(freeze),
        },
    ) == (dataset, freeze)


def test_public_cli_confirmation_fails_closed_without_external_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.delenv(CONFIRMATORY_DATASET_ENV_VAR, raising=False)
    monkeypatch.delenv(FREEZE_ARTIFACT_ENV_VAR, raising=False)

    assert offline_main(["--split", "confirmatory"]) == 2
    missing_dataset = json.loads(capsys.readouterr().out)
    assert missing_dataset["status"] == "ERROR"
    assert "requires --dataset" in missing_dataset["error"]

    sealed = tmp_path / "must-not-open.yaml"
    sealed.write_text("sealed sentinel must not be opened", encoding="utf-8")
    original_open = split_dataset_module.os.open

    def guarded_open(path: object, *args: object, **kwargs: object):
        if Path(path) == sealed:
            pytest.fail("sealed input was opened without a freeze source")
        return original_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(split_dataset_module.os, "open", guarded_open)
    assert offline_main(
        ["--split", "confirmatory", "--dataset", str(sealed)]
    ) == 2
    missing_freeze = json.loads(capsys.readouterr().out)
    assert missing_freeze["status"] == "ERROR"
    assert "requires --freeze" in missing_freeze["error"]


def test_public_cli_cannot_use_a_repository_bundled_dataset(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.delenv(CONFIRMATORY_DATASET_ENV_VAR, raising=False)
    monkeypatch.delenv(FREEZE_ARTIFACT_ENV_VAR, raising=False)
    monkeypatch.setattr(
        freeze_module,
        "verify_freeze_artifact",
        lambda _path: {"freeze_id": "synthetic-verification-only"},
    )

    assert offline_main(
        [
            "--split",
            "confirmatory",
            "--dataset",
            str(DEFAULT_DEVELOPMENT_DATASET),
            "--freeze",
            str(ROOT / "results_v5/protocol-v5.0.0/freezes/fake/freeze-manifest.json"),
        ]
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
    monkeypatch.setattr(
        freeze_module,
        "verify_freeze_artifact",
        lambda _path: {"freeze_id": "fixture"},
    )
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
        load_confirmatory_split(sealed, tmp_path / "freeze.json")
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
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(
        freeze_module,
        "verify_freeze_artifact",
        lambda _path: {"freeze_id": "fixture"},
    )
    with pytest.raises(SplitBundleValidationError, match=message):
        load_confirmatory_split(sealed, tmp_path / "freeze.json")


def test_confirmatory_loader_accepts_an_explicit_future_safe_split_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    sealed = _write_bundle(
        tmp_path / "sealed.yaml",
        _document(split_id="v5-confirmatory-replication-2"),
    )
    monkeypatch.setattr(
        freeze_module,
        "verify_freeze_artifact",
        lambda _path: {"freeze_id": "fixture"},
    )

    loaded = load_confirmatory_split(
        sealed,
        tmp_path / "freeze.json",
        expected_split_id="v5-confirmatory-replication-2",
    )

    assert loaded.split.manifest.split_id == "v5-confirmatory-replication-2"


def test_confirmatory_bundle_is_opened_once_and_hashes_the_parsed_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    sealed = _write_bundle(tmp_path / "sealed.yaml")
    monkeypatch.setattr(
        freeze_module,
        "verify_freeze_artifact",
        lambda _path: {"freeze_id": "fixture"},
    )
    original_open = split_dataset_module.os.open
    sealed_opens = 0

    def counted_open(path: object, flags: int, *args: object, **kwargs: object):
        nonlocal sealed_opens
        if str(path) == sealed.name and kwargs.get("dir_fd") is not None:
            sealed_opens += 1
        return original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(split_dataset_module.os, "open", counted_open)
    loaded = load_confirmatory_split(sealed, tmp_path / "freeze.json")

    assert sealed_opens == 1
    assert loaded.split.source_file_sha256 == hashlib.sha256(
        sealed.read_bytes()
    ).hexdigest()


def test_invalid_similarity_option_is_rejected_before_freeze_or_sealed_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        freeze_module,
        "verify_freeze_artifact",
        lambda _path: pytest.fail("freeze must not run for an invalid local option"),
    )
    monkeypatch.setattr(
        isolation_module,
        "require_external_dataset_path",
        lambda _path: pytest.fail("sealed path must not be inspected"),
    )
    with pytest.raises(ValueError, match="between 0 and 1"):
        load_confirmatory_split(
            tmp_path / "sealed.yaml",
            tmp_path / "freeze.json",
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


def test_contamination_intersections_are_symmetric_after_supply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    sealed = _write_bundle(tmp_path / "sealed.yaml")
    monkeypatch.setattr(
        freeze_module,
        "verify_freeze_artifact",
        lambda _path: {"freeze_id": "synthetic-verification-only"},
    )
    supplied = load_confirmatory_split(sealed, tmp_path / "freeze.json")
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


def test_production_freeze_is_complete_immutable_and_contains_no_sealed_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("EXTERNAL_LLM_MODEL", "frozen-p3-model")
    monkeypatch.setenv("EXTERNAL_LLM_API_KEY", "credential-must-not-be-recorded")
    artifact = _production_freeze(tmp_path, monkeypatch)
    manifest = json.loads(artifact.read_text(encoding="utf-8"))
    encoded = json.dumps(manifest, sort_keys=True).lower()
    snapshot = manifest["configuration_snapshot"]
    assert manifest["schema_version"] == FREEZE_SCHEMA_VERSION
    assert manifest["status"] == "FROZEN"
    assert manifest["source_control"] == {
        "git_revision": FIXED_REVISION,
        "git_worktree_clean": True,
    }
    assert snapshot["development_dataset"]["case_count"] == 18
    assert set(snapshot["systems"]) == {"P1", "P2", "P3"}
    assert snapshot["systems"]["P1"]["backend_version"]
    assert snapshot["systems"]["P2"]["backend_version"]
    assert snapshot["systems"]["P2"]["pipeline_version"]
    assert snapshot["systems"]["P3"]["backend_version"]
    assert snapshot["systems"]["P3"]["pipeline_version"]
    assert snapshot["systems"]["P3"]["reranker_version"]
    assert set(snapshot["indexes"]) == {"sparse", "dense", "hybrid", "source"}
    assert snapshot["indexes"]["source"] == "administrator_catalog_only"
    for index_name in ("sparse", "dense", "hybrid"):
        identity = snapshot["indexes"][index_name]
        assert identity["schema_version"]
        assert identity["index_version"]
        assert _is_sha256(identity["index_checksum"])
        assert _is_sha256(identity["corpus_checksum"])
    assert snapshot["candidate_catalog"]["version"]
    assert _is_sha256(snapshot["candidate_catalog"]["file_sha256"])
    assert _is_sha256(snapshot["candidate_catalog"]["corpus_sha256"])
    assert _is_sha256(snapshot["runtime_package"]["sha256"])
    assert _is_sha256(snapshot["prompts"]["P2_extractor"]["prompt_sha256"])
    assert _is_sha256(snapshot["prompts"]["P3_reranker"]["prompt_sha256"])
    assert snapshot["systems"]["P3"]["reranker_model_id"] == "frozen-p3-model"
    assert snapshot["configuration"]["P2"]["top_k"] == 10
    assert snapshot["configuration"]["P3"]["total_timeout"] == 30.0
    assert snapshot["configuration"]["constraints"]["retrieval_rank_weight"] == 0.75
    assert snapshot["configuration"]["constraints"]["soft_preference_weight"] == 0.25
    assert _is_sha256(snapshot["development_dataset"]["canonical_sha256"])
    assert _is_sha256(snapshot["development_dataset"]["file_sha256"])
    assert "confirmatory" not in encoded
    assert "credential-must-not-be-recorded" not in encoded
    assert verify_freeze_artifact(artifact)["freeze_id"] == "fixture-freeze"
    with pytest.raises(FileExistsError):
        create_freeze_artifact(
            freeze_id="fixture-freeze",
            p3_gate_status="not_retained",
            output_root=tmp_path / "freezes",
        )


@pytest.mark.parametrize(
    ("variable", "frozen_value", "drifted_value", "snapshot_path"),
    [
        ("P2_TOP_K", "7", "8", ("configuration", "P2", "top_k")),
        (
            "P3_TOTAL_TIMEOUT",
            "41",
            "42",
            ("configuration", "P3", "total_timeout"),
        ),
        (
            "EXTERNAL_LLM_MODEL",
            "frozen-model",
            "drifted-model",
            ("systems", "P3", "reranker_model_id"),
        ),
    ],
)
def test_production_freeze_rejects_effective_environment_configuration_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    frozen_value: str,
    drifted_value: str,
    snapshot_path: tuple[str, ...],
):
    monkeypatch.setenv(variable, frozen_value)
    artifact = _production_freeze(tmp_path, monkeypatch, freeze_id="env-freeze")
    manifest = json.loads(artifact.read_text(encoding="utf-8"))
    recorded: object = manifest["configuration_snapshot"]
    for key in snapshot_path:
        recorded = recorded[key]  # type: ignore[index]
    expected: object = int(frozen_value) if frozen_value.isdigit() else frozen_value
    assert recorded == expected

    monkeypatch.setenv(variable, drifted_value)
    with pytest.raises(FreezeValidationError, match="frozen Protocol-v5 inputs changed"):
        verify_freeze_artifact(artifact)


@pytest.mark.parametrize(
    ("variable", "drifted_value"),
    (
        ("EXTERNAL_LLM_ENDPOINT", "https://drifted-provider.example/v1"),
        ("EXTERNAL_LLM_TIMEOUT", "12"),
        ("EXTERNAL_LLM_MAX_RETRIES", "3"),
        ("EXTERNAL_LLM_PROMPT_PRICE_PER_M", "0.3"),
        ("EXTERNAL_LLM_PRICING_SOURCE", "changed-source-provenance"),
    ),
)
def test_freeze_captures_nonsecret_effective_provider_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    drifted_value: str,
):
    endpoint = "https://frozen-provider.example/v1"
    credential = "provider-credential-must-not-be-recorded"
    monkeypatch.setenv("P2_STRUCTURED_EXTRACTOR", "llm")
    monkeypatch.setenv("EXTERNAL_LLM_ENDPOINT", endpoint)
    monkeypatch.setenv("EXTERNAL_LLM_MODEL", "frozen-provider-model")
    monkeypatch.setenv("EXTERNAL_LLM_API_KEY", credential)
    monkeypatch.setenv("EXTERNAL_LLM_TIMEOUT", "11")
    monkeypatch.setenv("EXTERNAL_LLM_MAX_RETRIES", "2")
    monkeypatch.setenv("EXTERNAL_LLM_PROMPT_PRICE_PER_M", "0.1")
    monkeypatch.setenv("EXTERNAL_LLM_COMPLETION_PRICE_PER_M", "0.2")
    monkeypatch.setenv(
        "EXTERNAL_LLM_PRICING_SOURCE",
        "/private/custody/pricing-source-must-not-be-recorded",
    )

    artifact = _production_freeze(
        tmp_path,
        monkeypatch,
        freeze_id="provider-freeze",
        p3_gate_status="retained",
    )
    manifest = json.loads(artifact.read_text(encoding="utf-8"))
    provider = manifest["configuration_snapshot"]["configuration"]["provider"]
    encoded = json.dumps(manifest, sort_keys=True)
    assert provider["P2_extractor"] == provider["P3_reranker"]
    assert provider["P2_extractor"]["timeout"] == 11.0
    assert provider["P2_extractor"]["credentials_configured"] is True
    assert provider["P2_extractor"]["pricing"]["prompt_price_per_m"] == 0.1
    assert "source_provenance" not in provider["P2_extractor"]["pricing"]
    assert "source_provenance_sha256" in provider["P2_extractor"]["pricing"]
    assert endpoint not in encoded
    assert credential not in encoded
    assert "/private/custody" not in encoded

    monkeypatch.setenv(variable, drifted_value)
    with pytest.raises(FreezeValidationError, match="frozen Protocol-v5 inputs changed"):
        verify_freeze_artifact(artifact)


def test_freeze_never_opens_external_file_backed_provider_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    external_pricing = tmp_path / "sealed-material-must-not-open.json"
    external_pricing.write_text("sealed sentinel", encoding="utf-8")
    monkeypatch.setenv("P2_STRUCTURED_EXTRACTOR", "llm")
    monkeypatch.setenv("EXTERNAL_LLM_ENDPOINT", "https://provider.example/v1")
    monkeypatch.setenv("EXTERNAL_LLM_MODEL", "provider-model")
    monkeypatch.setenv("EXTERNAL_LLM_API_KEY", "synthetic-credential")
    monkeypatch.setenv("EXTERNAL_LLM_PRICING_CONFIG_PATH", str(external_pricing))
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):
        if path == external_pricing:
            pytest.fail("external provider configuration was opened before validation")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(FreezeValidationError, match="inside the repository"):
        build_configuration_snapshot(
            p3_gate_status="not_retained",
            p3_gate_evidence=freeze_module.DEFAULT_P3_GATE_EVIDENCE,
        )


def test_relative_pricing_path_is_bound_to_validated_repository_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = tmp_path / "repository"
    working_directory = tmp_path / "outside-working-directory"
    repository.mkdir()
    working_directory.mkdir()

    def pricing(pricing_id: str) -> dict[str, object]:
        return {
            "pricing_id": pricing_id,
            "snapshot_date": "2026-08-22",
            "provider": "synthetic-test",
            "applicable_model": "provider-model",
            "prompt_price_per_m": 0.1,
            "completion_price_per_m": 0.2,
            "source_provenance": "synthetic-test",
        }

    repository_pricing = repository / "pricing.json"
    repository_pricing.write_text(
        json.dumps(pricing("repository-pricing")), encoding="utf-8"
    )
    outside_pricing = working_directory / "pricing.json"
    outside_pricing.write_text(
        json.dumps(pricing("outside-pricing-must-not-open")), encoding="utf-8"
    )
    monkeypatch.setattr(freeze_module, "ROOT", repository)
    monkeypatch.setattr(freeze_module.subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.chdir(working_directory)
    monkeypatch.setenv("EXTERNAL_LLM_PRICING_CONFIG_PATH", "pricing.json")
    monkeypatch.setenv("EXTERNAL_LLM_ENDPOINT", "https://provider.example/v1")
    monkeypatch.setenv("EXTERNAL_LLM_MODEL", "provider-model")
    monkeypatch.setenv("EXTERNAL_LLM_API_KEY", "synthetic-credential")
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):
        if path.resolve() == outside_pricing.resolve():
            pytest.fail("provider opened CWD-relative pricing instead of validated pricing")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    selected, identity = freeze_module._validated_external_provider_environment()
    provider = freeze_module.ExternalLLMConfig.from_environ(selected)
    assert provider.pricing is not None
    assert provider.pricing.pricing_id == "repository-pricing"
    assert selected["EXTERNAL_LLM_PRICING_CONFIG_PATH"] == str(
        repository_pricing.resolve()
    )
    assert identity is not None
    assert identity["path"] == "pricing.json"


def test_provider_drift_fails_before_confirmatory_bundle_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("P2_STRUCTURED_EXTRACTOR", "llm")
    monkeypatch.setenv("EXTERNAL_LLM_ENDPOINT", "https://provider.example/v1")
    monkeypatch.setenv("EXTERNAL_LLM_MODEL", "provider-model")
    monkeypatch.setenv("EXTERNAL_LLM_API_KEY", "synthetic-credential")
    monkeypatch.setenv("EXTERNAL_LLM_TIMEOUT", "11")
    artifact = _production_freeze(
        tmp_path,
        monkeypatch,
        freeze_id="provider-order-freeze",
    )
    sealed = tmp_path / "must-not-open.yaml"
    sealed.write_text("sealed sentinel", encoding="utf-8")
    monkeypatch.setenv("EXTERNAL_LLM_TIMEOUT", "12")
    monkeypatch.setattr(
        isolation_module,
        "_read_split_bundle",
        lambda *_args, **_kwargs: pytest.fail(
            "sealed bundle was opened before provider drift verification"
        ),
    )

    with pytest.raises(FreezeValidationError, match="frozen Protocol-v5 inputs changed"):
        load_confirmatory_split(sealed, artifact)


def test_untracked_private_provider_file_is_rejected_before_content_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = tmp_path / "repository"
    private_directory = repository / ".protocol-v5-private"
    private_directory.mkdir(parents=True)
    private_pricing = private_directory / "pricing.json"
    private_pricing.write_text("sealed sentinel must not be read", encoding="utf-8")
    monkeypatch.setattr(freeze_module, "ROOT", repository)
    monkeypatch.setenv("EXTERNAL_LLM_PRICING_CONFIG_PATH", str(private_pricing))
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):
        if path == private_pricing:
            pytest.fail("untracked private provider configuration was opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(FreezeValidationError, match="tracked repository file"):
        freeze_module._validated_external_provider_environment()


def test_freeze_verification_never_hashes_external_gate_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    artifact = _production_freeze(tmp_path, monkeypatch, freeze_id="gate-path-freeze")
    manifest = json.loads(artifact.read_text(encoding="utf-8"))
    external_gate = tmp_path / "external-gate-with-sealed-sentinel.md"
    external_gate.write_text("sealed sentinel must not be read", encoding="utf-8")
    manifest["configuration_snapshot"]["p3_gate"]["evidence_path"] = str(
        external_gate
    )
    tampered_directory = artifact.parent.parent / "tampered-freeze"
    tampered_directory.mkdir()
    manifest["freeze_id"] = tampered_directory.name
    tampered = tampered_directory / FREEZE_ARTIFACT_BASENAME
    tampered.write_text(json.dumps(manifest), encoding="utf-8")
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):
        if path.resolve() == external_gate.resolve():
            pytest.fail("external gate evidence was opened during freeze verification")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(FreezeValidationError, match="inside the repository"):
        verify_freeze_artifact(tampered)


def test_gate_evidence_symlink_cannot_escape_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    external_gate = tmp_path / "external-gate.md"
    external_gate.write_text("external sentinel", encoding="utf-8")
    linked_gate = repository / "linked-gate.md"
    linked_gate.symlink_to(external_gate)
    monkeypatch.setattr(freeze_module, "ROOT", repository)

    with pytest.raises(FreezeValidationError, match="resolve outside"):
        build_configuration_snapshot(
            p3_gate_status="not_retained",
            p3_gate_evidence=linked_gate,
        )


def test_freeze_artifact_read_errors_are_generic_and_basename_is_canonical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    wrong_name = tmp_path / "looks-like-a-freeze.json"
    with pytest.raises(FreezeValidationError, match=FREEZE_ARTIFACT_BASENAME):
        verify_freeze_artifact(wrong_name)

    freeze_root = tmp_path / "freezes"
    monkeypatch.setattr(freeze_module, "DEFAULT_FREEZE_ROOT", freeze_root)
    monkeypatch.setattr(freeze_module, "FREEZE_CUSTODY_ROOT", tmp_path)
    canonical = freeze_root / "read-error-freeze" / FREEZE_ARTIFACT_BASENAME
    canonical.parent.mkdir(parents=True)
    canonical.write_text("{}", encoding="utf-8")

    def denied_read(*_args: object, **_kwargs: object):
        raise PermissionError("/private/custody/path-must-not-appear")

    monkeypatch.setattr(Path, "read_text", denied_read)
    with pytest.raises(FreezeValidationError) as error:
        verify_freeze_artifact(canonical)
    assert str(error.value) == "freeze artifact could not be read as valid JSON"
    assert "/private/custody" not in str(error.value)


@pytest.mark.parametrize("via_symlink", [False, True])
def test_external_canonical_freeze_is_rejected_before_content_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    via_symlink: bool,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    freeze_root = repository / "results_v5/protocol-v5.0.0/freezes"
    external_directory = tmp_path / "external-custody"
    external_directory.mkdir()
    external = external_directory / FREEZE_ARTIFACT_BASENAME
    external.write_text("sealed sentinel must not be read", encoding="utf-8")
    monkeypatch.setattr(freeze_module, "DEFAULT_FREEZE_ROOT", freeze_root)
    monkeypatch.setattr(freeze_module, "FREEZE_CUSTODY_ROOT", repository)
    if via_symlink:
        supplied = freeze_root / "symlink-freeze" / FREEZE_ARTIFACT_BASENAME
        supplied.parent.mkdir(parents=True)
        supplied.symlink_to(external)
    else:
        supplied = external
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args: object, **kwargs: object):
        if path.resolve() == external.resolve():
            pytest.fail("external canonical freeze contents were read before path rejection")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    with pytest.raises(FreezeValidationError):
        verify_freeze_artifact(supplied)


def test_freeze_creation_rejects_non_authoritative_output_root_before_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    freeze_root = repository / "results_v5/protocol-v5.0.0/freezes"
    monkeypatch.setattr(freeze_module, "DEFAULT_FREEZE_ROOT", freeze_root)
    monkeypatch.setattr(freeze_module, "FREEZE_CUSTODY_ROOT", repository)
    monkeypatch.setattr(
        freeze_module,
        "build_freeze_manifest",
        lambda **_kwargs: pytest.fail(
            "configuration snapshot was built before output-root rejection"
        ),
    )

    with pytest.raises(FreezeValidationError, match="authoritative repository freeze root"):
        create_freeze_artifact(
            freeze_id="external-output-freeze",
            p3_gate_status="not_retained",
            output_root=tmp_path / "external-output",
        )


def test_production_freeze_rejects_dirty_worktree_and_configured_sealed_data(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv(CONFIRMATORY_DATASET_ENV_VAR, raising=False)
    monkeypatch.setattr(freeze_module, "_git_state", lambda: (FIXED_REVISION, False))
    with pytest.raises(FreezeValidationError, match="clean Git"):
        build_freeze_manifest(
            freeze_id="dirty-freeze",
            p3_gate_status="not_retained",
        )

    monkeypatch.setenv(CONFIRMATORY_DATASET_ENV_VAR, "/external/sealed.yaml")
    with pytest.raises(FreezeValidationError, match="prohibited"):
        build_freeze_manifest(
            freeze_id="configured-freeze",
            p3_gate_status="not_retained",
            dry_run=True,
        )


def test_dry_run_and_stale_or_wrong_development_checksum_freezes_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv(CONFIRMATORY_DATASET_ENV_VAR, raising=False)
    monkeypatch.setattr(freeze_module, "_git_state", lambda: (FIXED_REVISION, True))
    freeze_root = tmp_path / "freezes"
    monkeypatch.setattr(freeze_module, "DEFAULT_FREEZE_ROOT", freeze_root)
    monkeypatch.setattr(freeze_module, "FREEZE_CUSTODY_ROOT", tmp_path)
    dry = build_freeze_manifest(
        freeze_id="dry-freeze",
        p3_gate_status="not_retained",
        dry_run=True,
    )
    assert dry["status"] == DRY_RUN_STATUS
    dry_directory = freeze_root / "dry-freeze"
    dry_directory.mkdir(parents=True)
    dry_path = dry_directory / FREEZE_ARTIFACT_BASENAME
    dry_path.write_text(json.dumps(dry), encoding="utf-8")
    with pytest.raises(FreezeValidationError, match="production FROZEN"):
        verify_freeze_artifact(dry_path)

    artifact = _production_freeze(tmp_path, monkeypatch, freeze_id="stale-freeze")
    stale = json.loads(artifact.read_text(encoding="utf-8"))
    stale["configuration_snapshot"]["development_dataset"][
        "canonical_sha256"
    ] = "0" * 64
    stale_directory = freeze_root / "stale-copy"
    stale_directory.mkdir()
    stale["freeze_id"] = stale_directory.name
    stale_path = stale_directory / FREEZE_ARTIFACT_BASENAME
    stale_path.write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(FreezeValidationError, match="development_dataset"):
        verify_freeze_artifact(stale_path)


def test_modifying_development_dataset_after_freeze_invalidates_freeze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    development_path = tmp_path / "v5-development.yaml"
    development_path.write_bytes(DEFAULT_DEVELOPMENT_DATASET.read_bytes())
    monkeypatch.setattr(
        split_dataset_module,
        "DEFAULT_DEVELOPMENT_DATASET",
        development_path,
    )
    monkeypatch.setattr(
        freeze_module,
        "DEFAULT_DEVELOPMENT_DATASET",
        development_path,
    )
    artifact = _production_freeze(
        tmp_path,
        monkeypatch,
        freeze_id="development-drift-freeze",
    )

    changed = yaml.safe_load(development_path.read_text(encoding="utf-8"))
    changed["cases"][0]["prompt"] += " synthetic post-freeze drift"
    changed["split_manifest"]["checksum"] = split_bundle_checksum(changed)
    development_path.write_text(
        yaml.safe_dump(changed, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    with pytest.raises(FreezeValidationError, match="development_dataset"):
        verify_freeze_artifact(artifact)


def test_catalog_content_and_every_index_identity_are_freeze_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    catalog_path = tmp_path / "image-catalog.yaml"
    catalog_path.write_bytes((ROOT / "recommender/image-catalog.yaml").read_bytes())
    monkeypatch.setattr(freeze_module, "DEFAULT_CATALOG_PATH", str(catalog_path))
    artifact = _production_freeze(
        tmp_path,
        monkeypatch,
        freeze_id="catalog-index-drift-freeze",
    )
    frozen = json.loads(artifact.read_text(encoding="utf-8"))[
        "configuration_snapshot"
    ]
    frozen_checksums = {
        name: frozen["indexes"][name]["index_checksum"]
        for name in ("sparse", "dense", "hybrid")
    }

    changed = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    changed["images"]["minimal-python"]["description"] += " Synthetic drift."
    catalog_path.write_text(
        yaml.safe_dump(changed, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    current = build_configuration_snapshot(
        p3_gate_status="not_retained",
        p3_gate_evidence=freeze_module.DEFAULT_P3_GATE_EVIDENCE,
    )
    assert all(
        current["indexes"][name]["index_checksum"] != frozen_checksums[name]
        for name in ("sparse", "dense", "hybrid")
    )
    with pytest.raises(FreezeValidationError) as error:
        verify_freeze_artifact(artifact)
    assert "candidate_catalog" in str(error.value)
    assert "indexes" in str(error.value)


def test_prompt_identity_drift_invalidates_freeze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    artifact = _production_freeze(
        tmp_path,
        monkeypatch,
        freeze_id="prompt-drift-freeze",
    )
    monkeypatch.setattr(
        freeze_module,
        "LOCAL_EXTRACTOR_PROMPT_SHA256",
        "f" * 64,
    )
    with pytest.raises(FreezeValidationError, match="prompts"):
        verify_freeze_artifact(artifact)


def test_loading_synthetic_confirmation_does_not_change_candidate_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    sentinel = "sealed-confirmatory-prompt-must-never-be-persisted-7d91"
    sealed = _write_bundle(
        tmp_path / "sealed.yaml",
        _document(cases=[_case(prompt=sentinel)]),
    )
    monkeypatch.setattr(
        freeze_module,
        "verify_freeze_artifact",
        lambda _path: {"freeze_id": "fixture"},
    )
    before = build_configuration_snapshot(
        p3_gate_status="not_retained",
        p3_gate_evidence=freeze_module.DEFAULT_P3_GATE_EVIDENCE,
    )["indexes"]
    loaded = load_confirmatory_split(sealed, tmp_path / "freeze.json")
    after = build_configuration_snapshot(
        p3_gate_status="not_retained",
        p3_gate_evidence=freeze_module.DEFAULT_P3_GATE_EVIDENCE,
    )["indexes"]
    assert loaded.split.manifest.role is SplitRole.CONFIRMATORY
    assert before == after
    for protected in (ROOT / "evaluation_v5/cache", ROOT / "evaluation_v5/indexes"):
        assert not protected.exists()
    result_root = ROOT / "results_v5/protocol-v5.0.0"
    if result_root.exists():
        for artifact in result_root.rglob("*"):
            if artifact.is_file():
                assert sentinel.encode("utf-8") not in artifact.read_bytes()


def test_isolation_audit_detects_synthetic_wheel_without_disclosing_content(
    tmp_path: Path,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    wheel = tmp_path / "private-name-must-not-appear.whl"
    prompt = "sentinel prompt must not appear"
    document = _document(cases=[_case(prompt=prompt)])
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "package/sealed.yaml",
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        )
    report = audit_repository(repository, archives=[wheel])
    encoded = json.dumps(
        [
            {"location": finding.location, "category": finding.category}
            for finding in report.findings
        ],
        sort_keys=True,
    )
    assert not report.clean
    assert "confirmatory-split-bundle" in encoded
    assert wheel.name not in encoded
    assert prompt not in encoded


@pytest.mark.parametrize(
    "relative_path",
    (
        "benchmarks_v5/sealed/v5-confirmatory.yaml",
        "tests/fixtures/protocol-v5-private.yaml",
        "evaluation_v5/cache/cached-split.yaml",
        "evaluation_v5/indexes/dataset-derived-index.yaml",
        "results_v5/protocol-v5.0.0/raw/sealed-labels.yaml",
    ),
)
def test_isolation_audit_detects_planted_material_in_protected_locations(
    tmp_path: Path,
    relative_path: str,
):
    repository = tmp_path / "repository"
    planted = repository / relative_path
    planted.parent.mkdir(parents=True)
    _write_bundle(planted)

    report = audit_repository(repository)

    assert not report.clean
    assert any(
        finding.category == "confirmatory-split-bundle"
        for finding in report.findings
    )


def test_isolation_audit_detects_docker_context_and_opaque_oci_layer(
    tmp_path: Path,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    bundle = yaml.safe_dump(
        _document(
            cases=[
                _case(
                    prompt="synthetic Docker isolation sentinel must stay redacted"
                )
            ]
        ),
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")

    context = tmp_path / "docker-context.tar"
    with tarfile.open(context, mode="w") as archive:
        info = tarfile.TarInfo(".protocol-v5-private/sealed.yaml")
        info.size = len(bundle)
        archive.addfile(info, io.BytesIO(bundle))

    layer_payload = io.BytesIO()
    with tarfile.open(fileobj=layer_payload, mode="w") as layer:
        info = tarfile.TarInfo("app/sealed.yaml")
        info.size = len(bundle)
        layer.addfile(info, io.BytesIO(bundle))
    oci_image = tmp_path / "synthetic-image.tar"
    with tarfile.open(oci_image, mode="w") as outer:
        opaque_layer = layer_payload.getvalue()
        info = tarfile.TarInfo("blobs/sha256/opaque-layer-digest")
        info.size = len(opaque_layer)
        outer.addfile(info, io.BytesIO(opaque_layer))

    report = audit_repository(repository, archives=[context, oci_image])
    encoded = json.dumps(
        [
            {"location": finding.location, "category": finding.category}
            for finding in report.findings
        ],
        sort_keys=True,
    )
    assert len(report.findings) == 2
    assert all(
        finding.category == "confirmatory-split-bundle"
        for finding in report.findings
    )
    assert "synthetic Docker isolation sentinel" not in encoded
    assert context.name not in encoded
    assert oci_image.name not in encoded


def test_isolation_audit_parses_confirmatory_role_across_json_lines(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    payload = json.dumps(_document(), indent=2)
    payload = payload.replace(
        '"role": "confirmatory"',
        '"role":\n      "confirmatory"',
    )
    (repository / "sealed.json").write_text(payload, encoding="utf-8")

    report = audit_repository(repository)

    assert not report.clean
    assert report.findings[0].category == "confirmatory-split-bundle"


def test_isolation_audit_detects_bundle_renamed_as_text(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    prompt = "renamed synthetic prompt must remain redacted"
    document = _document(cases=[_case(prompt=prompt)])
    (repository / "ordinary-looking.payload").write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    report = audit_repository(repository)
    encoded = json.dumps(
        [
            {"location": finding.location, "category": finding.category}
            for finding in report.findings
        ],
        sort_keys=True,
    )

    assert not report.clean
    assert "confirmatory-split-bundle" in encoded
    assert prompt not in encoded


@pytest.mark.parametrize(
    "wrapper",
    [
        "markdown_fence",
        "plain_prefix",
        "quoted_yaml_prefix",
        "flow_yaml_prefix",
        "json_wrapper",
    ],
)
def test_isolation_audit_detects_bundle_wrapped_in_text(
    tmp_path: Path,
    wrapper: str,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    prompt = "wrapped synthetic prompt must remain redacted"
    document = _document(cases=[_case(prompt=prompt)])
    if wrapper == "markdown_fence":
        name = "review-notes.md"
        payload = (
            "# Custody review\n\n```yaml\n"
            + yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
            + "```\n"
        )
    elif wrapper == "plain_prefix":
        name = "review-notes.txt"
        payload = "Custody review notes precede the intact bundle.\n" + yaml.safe_dump(
            document,
            sort_keys=False,
            allow_unicode=True,
        )
    elif wrapper == "quoted_yaml_prefix":
        name = "review-notes.payload"
        serialized = yaml.safe_dump(
            document,
            sort_keys=False,
            allow_unicode=True,
        )
        for key in ("schema_version", "split_manifest", "cases"):
            serialized = serialized.replace(f"{key}:", f"'{key}':", 1)
        payload = "Custody review notes precede quoted YAML keys.\n" + serialized
    elif wrapper == "flow_yaml_prefix":
        name = "review-notes.payload"
        serialized = yaml.safe_dump(
            document,
            default_flow_style=True,
            sort_keys=False,
            allow_unicode=True,
            width=1_000_000,
        ).replace("schema_version: ", "schema_version:\n ", 1)
        payload = (
            "Custody review notes precede a flow-style YAML bundle.\n"
            + serialized
            + "End custody review notes.\n"
        )
    else:
        name = "review-notes.log"
        payload = (
            "Custody review notes precede the intact JSON bundle.\n"
            + json.dumps(document, ensure_ascii=False)
            + "\nEnd custody review notes.\n"
        )
    (repository / name).write_text(payload, encoding="utf-8")

    report = audit_repository(repository)
    encoded = json.dumps(
        [
            {"location": finding.location, "category": finding.category}
            for finding in report.findings
        ],
        sort_keys=True,
    )

    assert not report.clean
    assert "confirmatory-split-bundle" in encoded
    assert prompt not in encoded


def test_isolation_audit_does_not_flag_schema_code_or_prose_mentions(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "schema.json").write_bytes(
        (ROOT / "benchmarks_v5/protocol-v5-split-bundle-v1.schema.json").read_bytes()
    )
    (repository / "notes.md").write_text(
        "Protocol documentation may mention protocol-v5-split-bundle-v1.0.0 "
        "and the confirmatory role without containing a dataset bundle.\n",
        encoding="utf-8",
    )
    (repository / "constants.py").write_text(
        'SCHEMA = "protocol-v5-split-bundle-v1.0.0"\n'
        'ROLE = "confirmatory"\n',
        encoding="utf-8",
    )

    assert audit_repository(repository).clean


def test_isolation_audit_streams_past_large_unknown_file_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(isolation_audit_module, "_MAX_DOCUMENT_BYTES", 1024)
    monkeypatch.setattr(isolation_audit_module, "_MAX_SIGNATURE_SCAN_BYTES", 64)
    prompt = "large padded prompt must remain redacted"
    payload = (
        b"x" * 2048
        + yaml.safe_dump(
            _document(cases=[_case(prompt=prompt)]),
            sort_keys=False,
            allow_unicode=True,
        ).encode("utf-8")
    )
    (repository / "opaque-large.payload").write_bytes(payload)

    with pytest.raises(IsolationAuditError, match="exceeds the audit limit") as error:
        audit_repository(repository)

    assert prompt not in str(error.value)


def test_isolation_audit_streams_oversized_archive_member_past_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(isolation_audit_module, "_MAX_DOCUMENT_BYTES", 512)
    monkeypatch.setattr(isolation_audit_module, "_MAX_NESTED_ARCHIVE_BYTES", 1024)
    monkeypatch.setattr(isolation_audit_module, "_MAX_SIGNATURE_SCAN_BYTES", 64)
    prompt = "oversized archive prompt must remain redacted"
    payload = (
        b"x" * 2048
        + yaml.safe_dump(
            _document(cases=[_case(prompt=prompt)]),
            sort_keys=False,
            allow_unicode=True,
        ).encode("utf-8")
    )
    archive_path = tmp_path / "private-name-must-not-appear.whl"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("opaque-member-without-suffix", payload)

    with pytest.raises(IsolationAuditError, match="exceeds the audit limit") as error:
        audit_repository(repository, archives=[archive_path])

    message = str(error.value)
    assert archive_path.name not in message
    assert "opaque-member" not in message
    assert prompt not in message


def test_isolation_audit_rejects_external_directory_symlink_without_path_leak(
    tmp_path: Path,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    external = tmp_path / "private-custody-name-must-not-appear"
    external.mkdir()
    prompt = "external symlink prompt must remain redacted"
    _write_bundle(external / "sealed.yaml", _document(cases=[_case(prompt=prompt)]))
    (repository / "external-data-link").symlink_to(external, target_is_directory=True)

    report = audit_repository(repository)
    encoded = json.dumps(
        [
            {"location": finding.location, "category": finding.category}
            for finding in report.findings
        ],
        sort_keys=True,
    )

    assert not report.clean
    assert "external-data-artifact-symlink" in encoded
    assert external.name not in encoded
    assert prompt not in encoded


def test_isolation_audit_recurses_into_nested_archive_without_name_leak(
    tmp_path: Path,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    wheel = tmp_path / "outer-private-name-must-not-appear.whl"
    prompt = "nested archive prompt must remain redacted"
    document = _document(cases=[_case(prompt=prompt)])
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as archive:
        archive.writestr(
            "inner-private-name-must-not-appear.payload",
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        )
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("outer-private-name-must-not-appear.zip", nested.getvalue())

    report = audit_repository(repository, archives=[wheel])
    encoded = json.dumps(
        [
            {"location": finding.location, "category": finding.category}
            for finding in report.findings
        ],
        sort_keys=True,
    )

    assert not report.clean
    assert report.archive_documents_scanned == 1
    assert "confirmatory-split-bundle" in encoded
    assert wheel.name not in encoded
    assert "inner-private-name" not in encoded
    assert "outer-private-name" not in encoded
    assert prompt not in encoded


@pytest.mark.parametrize("nested_kind", ["zip", "tar"])
def test_isolation_audit_content_sniffs_opaque_nested_archives(
    tmp_path: Path,
    nested_kind: str,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    wheel = tmp_path / "outer-private-name-must-not-appear.whl"
    prompt = "opaque nested archive prompt must remain redacted"
    bundle = yaml.safe_dump(
        _document(cases=[_case(prompt=prompt)]),
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")
    nested = io.BytesIO()
    if nested_kind == "zip":
        with zipfile.ZipFile(
            nested,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr("sealed.yaml", bundle)
    else:
        with tarfile.open(fileobj=nested, mode="w:gz") as archive:
            info = tarfile.TarInfo("sealed.yaml")
            info.size = len(bundle)
            archive.addfile(info, io.BytesIO(bundle))
    with zipfile.ZipFile(
        wheel,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr("blobs/sha256/opaque-layer-without-suffix", nested.getvalue())

    report = audit_repository(repository, archives=[wheel])
    encoded = json.dumps(
        [
            {"location": finding.location, "category": finding.category}
            for finding in report.findings
        ],
        sort_keys=True,
    )

    assert not report.clean
    assert "confirmatory-split-bundle" in encoded
    assert wheel.name not in encoded
    assert "opaque-layer" not in encoded
    assert prompt not in encoded


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_isolation_audit_content_sniffs_opaque_top_level_archives(
    tmp_path: Path,
    archive_kind: str,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    prompt = "opaque top-level archive prompt must remain redacted"
    bundle = yaml.safe_dump(
        _document(cases=[_case(prompt=prompt)]),
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")
    opaque = repository / "opaque-package.bin"
    if archive_kind == "zip":
        with zipfile.ZipFile(
            opaque,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr("sealed.yaml", bundle)
    else:
        with tarfile.open(opaque, mode="w:gz") as archive:
            info = tarfile.TarInfo("sealed.yaml")
            info.size = len(bundle)
            archive.addfile(info, io.BytesIO(bundle))

    report = audit_repository(repository)
    encoded = json.dumps(
        [
            {"location": finding.location, "category": finding.category}
            for finding in report.findings
        ],
        sort_keys=True,
    )

    assert not report.clean
    assert report.archives_scanned == 1
    assert "confirmatory-split-bundle" in encoded
    assert prompt not in encoded


def test_isolation_audit_fails_closed_at_archive_nesting_limit(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("innermost.json", "{}")
    for _ in range(4):
        wrapper = io.BytesIO()
        with zipfile.ZipFile(wrapper, "w") as archive:
            archive.writestr("private-nested-name-must-not-appear.zip", payload.getvalue())
        payload = wrapper
    wheel = tmp_path / "private-wheel-name-must-not-appear.whl"
    wheel.write_bytes(payload.getvalue())

    with pytest.raises(IsolationAuditError, match="nesting limit") as exc_info:
        audit_repository(repository, archives=[wheel])

    message = str(exc_info.value)
    assert wheel.name not in message
    assert "private-nested-name" not in message


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
        "Dockerfile.v3",
    }
    for selected_dockerfile in dockerfiles:
        instructions = selected_dockerfile.read_text(encoding="utf-8").splitlines()
        context_rules = Path(f"{selected_dockerfile}.dockerignore").read_text(
            encoding="utf-8"
        ).splitlines()
        assert context_rules[0] == "*"
        assert not any(
            any(token in rule for token in prohibited)
            for rule in context_rules
            if rule.startswith("!")
        )
        copy_or_add = [
            line.strip()
            for line in instructions
            if line.strip().startswith(("COPY ", "ADD "))
        ]
        assert copy_or_add
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
