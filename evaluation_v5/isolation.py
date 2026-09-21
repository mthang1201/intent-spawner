"""Fail-closed access and contamination checks for sealed Protocol-v5 data."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import unicodedata
from typing import Any, Mapping, Sequence

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
DEFAULT_SIMILARITY_THRESHOLD = 0.90
CONFIRMATORY_SPLIT_PROVENANCE_SCHEMA_VERSION = (
    "protocol-v5-confirmatory-split-provenance-v1.0.0"
)


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


_VERIFIED_SPLIT_CONSTRUCTION_KEY = object()


class VerifiedConfirmatorySplit:
    """Reverifiable custody capability for one sealed confirmatory split.

    The loaded dataclasses remain useful data containers, but class identity or
    a relabelled role is not authorization.  This capability binds them to the
    external source path so a downstream execution boundary can open and
    validate them again.
    """

    __slots__ = (
        "_split",
        "_development_split",
        "_contamination",
        "_dataset_path",
        "_expected_split_id",
        "_similarity_threshold",
        "_workload_manifests",
    )

    def __init__(
        self,
        *,
        split: LoadedSplit,
        development_split: LoadedSplit,
        contamination: ContaminationReport,
        dataset_path: Path,
        expected_split_id: str,
        similarity_threshold: float,
        workload_manifests: Sequence[Path],
        _construction_key: object,
    ) -> None:
        if _construction_key is not _VERIFIED_SPLIT_CONSTRUCTION_KEY:
            raise TypeError(
                "VerifiedConfirmatorySplit is produced only by "
                "load_confirmatory_split()"
            )
        object.__setattr__(self, "_split", split)
        object.__setattr__(self, "_development_split", development_split)
        object.__setattr__(self, "_contamination", contamination)
        object.__setattr__(self, "_dataset_path", dataset_path)
        object.__setattr__(self, "_expected_split_id", expected_split_id)
        object.__setattr__(self, "_similarity_threshold", similarity_threshold)
        object.__setattr__(self, "_workload_manifests", tuple(workload_manifests))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("VerifiedConfirmatorySplit is immutable")

    @property
    def split(self) -> LoadedSplit:
        return self._split

    @property
    def development_split(self) -> LoadedSplit:
        return self._development_split

    @property
    def contamination(self) -> ContaminationReport:
        return self._contamination

    @property
    def dataset_path(self) -> Path:
        return self._dataset_path

    @property
    def provenance_identity(self) -> Mapping[str, Any]:
        """Return stable JSON provenance derived from this verified capability."""

        split = self._split
        return {
            "schema_version": CONFIRMATORY_SPLIT_PROVENANCE_SCHEMA_VERSION,
            "protocol_version": "5.0.0",
            "authority": {
                "capability_type": "VerifiedConfirmatorySplit",
                "loader": "evaluation_v5.isolation.load_confirmatory_split",
                "verifier": "evaluation_v5.isolation.verify_confirmatory_split",
            },
            "split": {
                "schema_version": split.bundle.schema_version,
                "dataset_id": split.manifest.dataset_id,
                "split_id": split.manifest.split_id,
                "role": split.manifest.role.value,
                "bundle_checksum": split.manifest.checksum,
                "source_file_sha256": split.source_file_sha256,
                "case_count": split.manifest.case_count,
                "family_count": split.manifest.family_count,
            },
        }


# Read-only source compatibility for callers that used the prior result name.
ConfirmatoryLoadResult = VerifiedConfirmatorySplit


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
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Select exactly one explicit CLI or environment source for the dataset."""

    selected = os.environ if environ is None else environ
    dataset_env_present = CONFIRMATORY_DATASET_ENV_VAR in selected
    dataset_env = selected.get(CONFIRMATORY_DATASET_ENV_VAR)
    if dataset_path is not None and dataset_env_present:
        raise SplitIsolationError(
            "confirmatory dataset was supplied by both CLI and environment"
        )
    if dataset_env_present and (
        not isinstance(dataset_env, str) or not dataset_env.strip()
    ):
        raise SplitIsolationError(
            "PROTOCOL_V5_CONFIRMATORY_DATASET must be a non-blank path"
        )
    dataset_value = dataset_path or (
        Path(dataset_env) if dataset_env_present else None
    )
    if dataset_value is None:
        raise SplitIsolationError(
            "confirmatory mode requires --dataset or PROTOCOL_V5_CONFIRMATORY_DATASET"
        )
    return dataset_value


def load_confirmatory_split(
    dataset_path: Path,
    *,
    expected_split_id: str = DEFAULT_CONFIRMATORY_SPLIT_ID,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    workload_manifests: Sequence[Path] = (),
) -> VerifiedConfirmatorySplit:
    """Open, verify, and contamination-check a sealed confirmatory split."""

    threshold = _validated_similarity_threshold(similarity_threshold)

    development = load_development_split()
    external = require_external_dataset_path(dataset_path)
    confirmatory = _read_split_bundle(
        external,
        expected_role=SplitRole.CONFIRMATORY,
        expected_split_id=expected_split_id,
        no_follow=True,
        workload_manifests=workload_manifests,
    )
    report = check_contamination(
        development.bundle,
        confirmatory.bundle,
        forbid_family_overlap=True,
        similarity_threshold=threshold,
    )
    return VerifiedConfirmatorySplit(
        split=confirmatory,
        development_split=development,
        contamination=report,
        dataset_path=external,
        expected_split_id=expected_split_id,
        similarity_threshold=threshold,
        workload_manifests=workload_manifests,
        _construction_key=_VERIFIED_SPLIT_CONSTRUCTION_KEY,
    )


def verify_confirmatory_split(
    capability: VerifiedConfirmatorySplit,
) -> VerifiedConfirmatorySplit:
    """Revalidate split role/checksums/custody at a use boundary."""

    if type(capability) is not VerifiedConfirmatorySplit:
        raise TypeError(
            "confirmatory execution requires a VerifiedConfirmatorySplit from "
            "load_confirmatory_split()"
        )
    current = load_confirmatory_split(
        capability._dataset_path,
        expected_split_id=capability._expected_split_id,
        similarity_threshold=capability._similarity_threshold,
        workload_manifests=capability._workload_manifests,
    )
    expected_split = capability._split
    actual_split = current._split
    expected_identity = (
        expected_split.manifest.dataset_id,
        expected_split.manifest.split_id,
        expected_split.manifest.role,
        expected_split.manifest.checksum,
        expected_split.source_file_sha256,
    )
    actual_identity = (
        actual_split.manifest.dataset_id,
        actual_split.manifest.split_id,
        actual_split.manifest.role,
        actual_split.manifest.checksum,
        actual_split.source_file_sha256,
    )
    if actual_identity != expected_identity:
        raise SplitIsolationError(
            "confirmatory split capability no longer matches its verified source"
        )
    expected_development = capability._development_split
    actual_development = current._development_split
    if (
        actual_development.manifest.checksum,
        actual_development.source_file_sha256,
    ) != (
        expected_development.manifest.checksum,
        expected_development.source_file_sha256,
    ):
        raise SplitIsolationError(
            "development split changed after confirmatory preparation"
        )
    return current


__all__ = [
    "CONFIRMATORY_SPLIT_PROVENANCE_SCHEMA_VERSION",
    "CONFIRMATORY_DATASET_ENV_VAR",
    "DEFAULT_SIMILARITY_THRESHOLD",
    "ConfirmatoryLoadResult",
    "ContaminationReport",
    "SplitContaminationError",
    "SplitIsolationError",
    "VerifiedConfirmatorySplit",
    "check_contamination",
    "load_confirmatory_split",
    "normalize_prompt",
    "prompt_fingerprint",
    "require_external_dataset_path",
    "resolve_confirmatory_sources",
    "verify_confirmatory_split",
]
