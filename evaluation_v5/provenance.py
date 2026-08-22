"""Atomic, overwrite-safe writers for Protocol-v5 provenance JSON."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .paths import ResultPaths, require_development_override
from .schemas import ProtocolV5Manifest
from .validation import validate_manifest


def _serialize_json(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    overwrite: bool,
) -> Path:
    """Publish complete JSON atomically; never expose a partial destination."""

    serialized = _serialize_json(payload)
    if not path.parent.is_dir():
        raise FileNotFoundError(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o644)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            # A same-filesystem hard link publishes without the overwrite race
            # inherent in check-then-rename on POSIX filesystems.
            os.link(temporary, path)
            temporary.unlink()
        _fsync_directory(path.parent)
        return path
    finally:
        if temporary.exists():
            temporary.unlink()


def write_json_exclusive(
    path: Path,
    payload: Mapping[str, Any],
) -> Path:
    """Atomically create one JSON file without ever replacing a destination."""

    return _atomic_write_json(path, payload, overwrite=False)


def _safe_target(paths: ResultPaths, relative_path: str | Path) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("provenance path must be a safe path relative to the run")
    target = paths.root.joinpath(relative)
    if target.suffix != ".json":
        raise ValueError("provenance files must use the .json suffix")
    try:
        target.resolve().relative_to(paths.root.resolve())
    except ValueError as exc:
        raise ValueError("provenance path escapes the run directory") from exc
    return target


def write_provenance_json(
    paths: ResultPaths,
    relative_path: str | Path,
    payload: Mapping[str, Any],
    *,
    manifest: ProtocolV5Manifest,
    development_override: bool = False,
) -> Path:
    """Write one provenance object under a run with guarded overwrite rules."""

    validate_manifest(manifest)
    if development_override:
        require_development_override(manifest)
    target = _safe_target(paths, relative_path)
    return _atomic_write_json(
        target,
        payload,
        overwrite=development_override,
    )


def write_manifest_atomic(
    paths: ResultPaths,
    manifest: ProtocolV5Manifest,
    *,
    development_override: bool = False,
) -> Path:
    """Atomically publish the canonical root manifest."""

    return write_provenance_json(
        paths,
        "manifest.json",
        manifest.to_dict(),
        manifest=manifest,
        development_override=development_override,
    )


__all__ = [
    "write_json_exclusive",
    "write_manifest_atomic",
    "write_provenance_json",
]
