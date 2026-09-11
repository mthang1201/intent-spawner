"""Checksum-bound input access and exclusive output publication.

No path inference, external custody loading, or implicit evidence enrollment.
Legacy path relocation changes how a reference is resolved, never its bytes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from evaluation_v4.dataset import file_sha256

ROOT = Path(__file__).resolve().parents[2]
LOCK = "benchmarks_v5/protocol-v5-final-audit-inputs-v3.json"
RESULTS = "results_v5/protocol-v5.0.0"
REGISTRY = "benchmarks_v5/protocol-v5-claim-registry-v1.1.yaml"


def read_json(path: Path) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("nonfinite JSON number")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=invalid)


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    write_bytes(path, (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                                  allow_nan=False) + "\n").encode())


def write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(value)


def safe_path(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ValueError("expected a repository-relative path")
    path = root / candidate
    if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise ValueError("symlinked evidence is not accepted")
    path.resolve().relative_to(root.resolve())
    return path


class Inputs:
    def __init__(self, root: Path = ROOT, lock_path: str = LOCK):
        self.root = root.resolve()
        self.lock_path = lock_path
        self.lock = read_json(safe_path(self.root, lock_path))
        if self.lock.get("schema_version") not in {
            "protocol-v5-final-audit-inputs-v1.0.0",
            "protocol-v5-final-audit-inputs-v1.1.0",
            "protocol-v5-final-audit-inputs-v1.2.0",
        }:
            raise ValueError("unsupported final-audit input inventory")
        self.files = self.lock["files"]
        self.relocations = self.lock.get("legacy_reference_map", {})
        for name, digest in self.files.items():
            safe_path(self.root, name)
            if not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("invalid inventory digest")

    def path(self, relative: str) -> Path:
        path = safe_path(self.root, relative)
        expected = self.files.get(relative)
        if expected is None:
            raise ValueError("input is not in the reviewed evidence inventory")
        if not path.is_file() or file_sha256(path) != expected:
            raise ValueError("input missing or checksum changed: " + relative)
        return path

    def resolve(self, reference: str, expected: str) -> Path:
        if Path(reference).is_absolute():
            candidate = Path(reference).resolve()
            try:
                # A newly produced package may bind inputs in this exact checkout.
                # Relocated historical paths still require an explicit hash-keyed
                # mapping and never fall back to basename or path inference.
                relative = str(candidate.relative_to(self.root))
            except ValueError:
                # The key itself is hashed to avoid publishing developer home paths.
                key = hashlib.sha256(reference.encode()).hexdigest()
                relative = self.relocations.get(key)
                if relative is None:
                    raise ValueError("legacy reference has no explicit relocation")
        else:
            relative = reference
        path = self.path(relative)
        if self.files[relative] != expected:
            raise ValueError("referenced checksum differs from reviewed input bytes")
        return path

    def ref(self, relative: str, pointer: str = "") -> dict:
        self.path(relative)
        return {"path": relative, "sha256": self.files[relative], "locator": pointer}

    def json(self, relative: str) -> Any:
        return read_json(self.path(relative))

    def verify(self) -> list[dict]:
        failures = []
        for name in sorted(self.files):
            try:
                self.path(name)
            except (OSError, ValueError):
                failures.append({"path": name, "reason": "MISSING_OR_CHANGED_INPUT"})
        return failures


def seal(directory: Path, metadata: dict) -> None:
    outputs = {str(p.relative_to(directory)): file_sha256(p)
               for p in sorted(directory.rglob("*")) if p.is_file()}
    write_json(directory / "manifest.json", {**metadata, "output_sha256": outputs})
    outputs["manifest.json"] = file_sha256(directory / "manifest.json")
    write_bytes(directory / "SHA256SUMS", "".join(
        f"{digest}  {name}\n" for name, digest in sorted(outputs.items())).encode())


def verify_seal(directory: Path) -> dict:
    manifest = read_json(directory / "manifest.json")
    expected = dict(manifest["output_sha256"])
    for name, digest in expected.items():
        if file_sha256(safe_path(directory, name)) != digest:
            raise ValueError("generated output checksum mismatch: " + name)
    actual = {str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()}
    if actual != set(expected) | {"manifest.json", "SHA256SUMS"}:
        raise ValueError("unregistered or missing generated output")
    expected["manifest.json"] = file_sha256(directory / "manifest.json")
    if (directory / "SHA256SUMS").read_bytes() != "".join(
            f"{digest}  {name}\n" for name, digest in sorted(expected.items())).encode():
        raise ValueError("generated checksum manifest mismatch")
    return manifest
