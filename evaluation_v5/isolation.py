"""Fail-closed access and contamination checks for sealed Protocol-v5 data."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import os
from pathlib import Path
import unicodedata
from typing import Any, Mapping

from .split_dataset import (
    DEFAULT_CONFIRMATORY_SPLIT_ID,
    LoadedSplit,
    SplitBundle,
    SplitCase,
    SplitRole,
    _read_split_bundle,
    load_development_split,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIRMATORY_DATASET_ENV_VAR = "PROTOCOL_V5_CONFIRMATORY_DATASET"
FREEZE_ARTIFACT_ENV_VAR = "PROTOCOL_V5_FREEZE_ARTIFACT"
DEFAULT_SIMILARITY_THRESHOLD = 0.90


class SplitIsolationError(RuntimeError):
    """A split-access request violates the sealed-data boundary."""


class SplitContaminationError(SplitIsolationError):
    """Development and confirmatory datasets overlap on a prohibited key."""

    def __init__(self, report: "ContaminationReport") -> None:
        self.report = report
        categories = ", ".join(report.blocking_categories)
        super().__init__(
            "Protocol-v5 split contamination detected"
            + (f" ({categories})" if categories else "")
        )


@dataclass(frozen=True, slots=True)
class ContaminationReport:
    overlapping_case_ids: tuple[str, ...]
    overlapping_family_ids: tuple[str, ...]
    exact_prompt_pairs: tuple[Mapping[str, Any], ...]
    normalized_prompt_pairs: tuple[Mapping[str, Any], ...]
    similarity_review_pairs: tuple[Mapping[str, Any], ...]
    similarity_threshold: float

    @property
    def blocking_categories(self) -> tuple[str, ...]:
        categories: list[str] = []
        if self.overlapping_case_ids:
            categories.append("case_id_overlap")
        if self.overlapping_family_ids:
            categories.append("family_id_overlap")
        if self.exact_prompt_pairs:
            categories.append("exact_prompt_duplicate")
        if self.normalized_prompt_pairs:
            categories.append("normalized_prompt_duplicate")
        return tuple(categories)

    @property
    def has_blocking_contamination(self) -> bool:
        return bool(self.blocking_categories)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "blocking_checks_passed": not self.has_blocking_contamination,
            "blocking_categories": list(self.blocking_categories),
            "overlapping_case_ids": list(self.overlapping_case_ids),
            "overlapping_family_ids": list(self.overlapping_family_ids),
            "exact_prompt_pairs": [dict(item) for item in self.exact_prompt_pairs],
            "normalized_prompt_pairs": [
                dict(item) for item in self.normalized_prompt_pairs
            ],
            "similarity_threshold": self.similarity_threshold,
            "similarity_review_pair_count": len(self.similarity_review_pairs),
            "similarity_review_pairs": [
                dict(item) for item in self.similarity_review_pairs
            ],
        }


@dataclass(frozen=True, slots=True)
class ConfirmatoryLoadResult:
    split: LoadedSplit
    development_split: LoadedSplit
    freeze_manifest: Mapping[str, Any]
    contamination: ContaminationReport


def normalize_prompt(value: str) -> str:
    """Normalize Unicode, case, punctuation, and whitespace."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    characters = (
        " " if unicodedata.category(character).startswith("P") else character
        for character in normalized
    )
    return " ".join("".join(characters).split())


def prompt_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pair(
    development: SplitCase,
    confirmatory: SplitCase,
    *,
    score: float | None = None,
) -> dict[str, Any]:
    pair: dict[str, Any] = {
        "development_case_id": development.case_id,
        "development_family_id": development.family_id,
        "development_prompt_sha256": prompt_fingerprint(development.prompt),
        "confirmatory_case_id": confirmatory.case_id,
        "confirmatory_family_id": confirmatory.family_id,
        "confirmatory_prompt_sha256": prompt_fingerprint(confirmatory.prompt),
    }
    if score is not None:
        pair["similarity_score"] = round(score, 6)
    return pair


def check_contamination(
    development: SplitBundle,
    confirmatory: SplitBundle,
    *,
    forbid_family_overlap: bool = True,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    raise_on_blocking: bool = True,
) -> ContaminationReport:
    """Reject identity/text leakage and surface lexical similarity for review."""

    threshold = _validated_similarity_threshold(similarity_threshold)

    development_ids = {case.case_id for case in development.cases}
    confirmatory_ids = {case.case_id for case in confirmatory.cases}
    development_families = {case.family_id for case in development.cases}
    confirmatory_families = {case.family_id for case in confirmatory.cases}

    exact_pairs: list[Mapping[str, Any]] = []
    normalized_pairs: list[Mapping[str, Any]] = []
    review_pairs: list[Mapping[str, Any]] = []
    for development_case in development.cases:
        development_normalized = normalize_prompt(development_case.prompt)
        for confirmatory_case in confirmatory.cases:
            if development_case.prompt == confirmatory_case.prompt:
                exact_pairs.append(_pair(development_case, confirmatory_case))
                continue
            confirmatory_normalized = normalize_prompt(confirmatory_case.prompt)
            if development_normalized == confirmatory_normalized:
                normalized_pairs.append(_pair(development_case, confirmatory_case))
                continue
            score = SequenceMatcher(
                None,
                development_normalized,
                confirmatory_normalized,
                autojunk=False,
            ).ratio()
            if score >= threshold:
                review_pairs.append(
                    _pair(development_case, confirmatory_case, score=score)
                )

    report = ContaminationReport(
        overlapping_case_ids=tuple(sorted(development_ids & confirmatory_ids)),
        overlapping_family_ids=(
            tuple(sorted(development_families & confirmatory_families))
            if forbid_family_overlap
            else ()
        ),
        exact_prompt_pairs=tuple(exact_pairs),
        normalized_prompt_pairs=tuple(normalized_pairs),
        similarity_review_pairs=tuple(
            sorted(
                review_pairs,
                key=lambda item: (
                    -float(item["similarity_score"]),
                    str(item["development_case_id"]),
                    str(item["confirmatory_case_id"]),
                ),
            )
        ),
        similarity_threshold=threshold,
    )
    if raise_on_blocking and report.has_blocking_contamination:
        raise SplitContaminationError(report)
    return report


def _validated_similarity_threshold(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError("similarity_threshold must be between 0 and 1")
    return float(value)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _is_physically_within(path: Path, parent: Path) -> bool:
    """Recognize repository ancestors through filesystem identity as well as text."""

    for candidate in (path, *path.parents):
        try:
            if candidate.samefile(parent):
                return True
        except OSError as exc:
            raise SplitIsolationError(
                "confirmatory dataset ancestry could not be inspected safely"
            ) from exc
    return False


def require_external_dataset_path(path: Path) -> Path:
    """Reject relative, in-repository, missing, or directory dataset paths."""

    if not path.is_absolute():
        raise SplitIsolationError(
            "confirmatory dataset must be supplied as an absolute external path"
        )
    repository = ROOT.resolve()
    lexical = Path(os.path.abspath(os.fspath(path)))
    if _is_within(lexical, repository):
        raise SplitIsolationError(
            "confirmatory dataset path must remain outside the repository"
        )
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SplitIsolationError(
            "confirmatory dataset could not be resolved safely"
        ) from exc
    if _is_within(resolved, repository) or _is_physically_within(
        resolved, repository
    ):
        raise SplitIsolationError(
            "confirmatory dataset cannot resolve inside the repository"
        )
    try:
        regular_file = resolved.is_file()
    except OSError as exc:
        raise SplitIsolationError(
            "confirmatory dataset could not be inspected safely"
        ) from exc
    if not regular_file:
        raise SplitIsolationError("confirmatory dataset must be a regular file")
    return resolved


def resolve_confirmatory_sources(
    *,
    dataset_path: Path | None,
    freeze_path: Path | None,
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, Path]:
    """Select exactly one explicit CLI or environment source for each input."""

    selected = os.environ if environ is None else environ
    dataset_env_present = CONFIRMATORY_DATASET_ENV_VAR in selected
    freeze_env_present = FREEZE_ARTIFACT_ENV_VAR in selected
    dataset_env = selected.get(CONFIRMATORY_DATASET_ENV_VAR)
    freeze_env = selected.get(FREEZE_ARTIFACT_ENV_VAR)
    cli_present = dataset_path is not None or freeze_path is not None
    environment_present = dataset_env_present or freeze_env_present
    if dataset_path is not None and dataset_env_present:
        raise SplitIsolationError(
            "confirmatory dataset was supplied by both CLI and environment"
        )
    if freeze_path is not None and freeze_env_present:
        raise SplitIsolationError(
            "freeze artifact was supplied by both CLI and environment"
        )
    if dataset_env_present and (
        not isinstance(dataset_env, str) or not dataset_env.strip()
    ):
        raise SplitIsolationError(
            "PROTOCOL_V5_CONFIRMATORY_DATASET must be a non-blank path"
        )
    if freeze_env_present and (
        not isinstance(freeze_env, str) or not freeze_env.strip()
    ):
        raise SplitIsolationError(
            "PROTOCOL_V5_FREEZE_ARTIFACT must be a non-blank path"
        )
    if cli_present and environment_present:
        raise SplitIsolationError(
            "confirmatory inputs were supplied by both CLI and environment"
        )
    dataset_value = dataset_path or (
        Path(dataset_env) if dataset_env_present else None
    )
    freeze_value = freeze_path or (
        Path(freeze_env) if freeze_env_present else None
    )
    if dataset_value is None:
        raise SplitIsolationError(
            "confirmatory mode requires --dataset or PROTOCOL_V5_CONFIRMATORY_DATASET"
        )
    if freeze_value is None:
        raise SplitIsolationError(
            "confirmatory mode requires --freeze or PROTOCOL_V5_FREEZE_ARTIFACT"
        )
    if Path(os.path.abspath(os.fspath(dataset_value))) == Path(
        os.path.abspath(os.fspath(freeze_value))
    ):
        raise SplitIsolationError(
            "confirmatory dataset and freeze artifact must be distinct inputs"
        )
    return dataset_value, freeze_value


def load_confirmatory_split(
    dataset_path: Path,
    freeze_path: Path,
    *,
    expected_split_id: str = DEFAULT_CONFIRMATORY_SPLIT_ID,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> ConfirmatoryLoadResult:
    """Verify the pre-data freeze before opening and comparing sealed material."""

    threshold = _validated_similarity_threshold(similarity_threshold)

    # Local option validation above does not resolve, stat, or open dataset_path.
    # The freeze is deliberately verified before any filesystem interaction with
    # the sealed dataset path.
    from .freeze import verify_freeze_artifact

    freeze_manifest = verify_freeze_artifact(freeze_path)
    development = load_development_split()
    external = require_external_dataset_path(dataset_path)
    confirmatory = _read_split_bundle(
        external,
        expected_role=SplitRole.CONFIRMATORY,
        expected_split_id=expected_split_id,
        no_follow=True,
    )
    report = check_contamination(
        development.bundle,
        confirmatory.bundle,
        forbid_family_overlap=True,
        similarity_threshold=threshold,
    )
    return ConfirmatoryLoadResult(
        split=confirmatory,
        development_split=development,
        freeze_manifest=freeze_manifest,
        contamination=report,
    )


__all__ = [
    "CONFIRMATORY_DATASET_ENV_VAR",
    "DEFAULT_SIMILARITY_THRESHOLD",
    "FREEZE_ARTIFACT_ENV_VAR",
    "ConfirmatoryLoadResult",
    "ContaminationReport",
    "SplitContaminationError",
    "SplitIsolationError",
    "check_contamination",
    "load_confirmatory_split",
    "normalize_prompt",
    "prompt_fingerprint",
    "require_external_dataset_path",
    "resolve_confirmatory_sources",
]
