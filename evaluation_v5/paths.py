"""Immutable, versioned result-directory conventions for Protocol-v5."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .schemas import EvidenceStatus, ProtocolV5Manifest, SplitStage
from .validation import validate_manifest


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_ROOT = ROOT / "results_v5"
PROTOCOL_DIRECTORY = "protocol-v5.0.0"


@dataclass(frozen=True, slots=True)
class ResultPaths:
    root: Path
    manifest: Path
    raw: Path
    derived: Path
    report: Path


def result_paths(
    manifest: ProtocolV5Manifest,
    *,
    results_root: Path = DEFAULT_RESULTS_ROOT,
) -> ResultPaths:
    """Resolve the canonical path without creating anything."""

    validate_manifest(manifest)
    root = (
        results_root.resolve()
        / PROTOCOL_DIRECTORY
        / manifest.experiment_id.value
        / manifest.run_id
    )
    return ResultPaths(
        root=root,
        manifest=root / "manifest.json",
        raw=root / "raw",
        derived=root / "derived",
        report=root / "report",
    )


def require_development_override(manifest: ProtocolV5Manifest) -> None:
    """Reject override use outside non-observed development work."""

    validate_manifest(manifest)
    if manifest.split_identity.stage is not SplitStage.DEVELOPMENT:
        raise PermissionError(
            "development_override is prohibited for confirmatory splits"
        )
    if manifest.execution_status is EvidenceStatus.OBSERVED:
        raise PermissionError(
            "development_override is prohibited for OBSERVED evidence"
        )


def create_result_directory(
    manifest: ProtocolV5Manifest,
    *,
    results_root: Path = DEFAULT_RESULTS_ROOT,
    development_override: bool = False,
) -> ResultPaths:
    """Create raw/derived/report using exclusive creation by default."""

    paths = result_paths(manifest, results_root=results_root)
    if development_override:
        require_development_override(manifest)
    if paths.root.exists() and not development_override:
        raise FileExistsError(paths.root)
    paths.root.mkdir(parents=True, exist_ok=development_override)
    for directory in (paths.raw, paths.derived, paths.report):
        directory.mkdir(exist_ok=development_override)
    return paths


__all__ = [
    "DEFAULT_RESULTS_ROOT",
    "PROTOCOL_DIRECTORY",
    "ResultPaths",
    "create_result_directory",
    "require_development_override",
    "result_paths",
]
