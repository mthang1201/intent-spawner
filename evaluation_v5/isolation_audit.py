"""Audit repository and package artifacts for sealed Protocol-v5 bundles.

The audit never extracts archives and never reports dataset contents.  It is a
defence-in-depth packaging check; the confirmatory loader remains responsible
for enforcing the external-custody and pre-freeze boundary at runtime.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import textwrap
from typing import Any
import zipfile

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SPLIT_SCHEMA_PREFIX = "protocol-v5-split-bundle-"

_DOCUMENT_SUFFIXES = frozenset({".json", ".jsonl", ".yaml", ".yml"})
_ARCHIVE_SUFFIXES = (
    ".whl",
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
)
_SKIPPED_DIRECTORY_NAMES = frozenset(
    {".git", ".pytest_cache", ".venv", "__pycache__"}
)
_MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_ARCHIVE_READ_BYTES = 512 * 1024 * 1024
_MAX_NESTED_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_DEPTH = 3
_MAX_SIGNATURE_SCAN_BYTES = 64 * 1024
_MAX_EMBEDDED_DOCUMENT_CANDIDATES = 256
_MAX_REPOSITORY_STREAM_BYTES = 512 * 1024 * 1024
_STREAM_CHUNK_BYTES = 1024 * 1024
_SIGNATURE_OVERLAP_BYTES = 256
_SCHEMA_SIGNATURE = re.compile(
    rb"protocol-v5-split-bundle-[A-Za-z0-9._-]+",
    re.IGNORECASE,
)
_CONFIRMATORY_TOKEN_SIGNATURE = re.compile(
    rb"confirmatory",
    re.IGNORECASE,
)
_YAML_SCHEMA_LINE = re.compile(
    r"(?m)^[ \t]*(?P<key_quote>['\"]?)schema_version(?P=key_quote)"
    r"[ \t]*:[ \t]*(?P<value_quote>['\"]?)"
    r"protocol-v5-split-bundle-[A-Za-z0-9._-]+(?P=value_quote)"
    r"[ \t]*(?:#.*)?$",
    re.IGNORECASE,
)
_YAML_FLOW_SCHEMA_FIELD = re.compile(
    r"(?P<key_quote>['\"]?)schema_version(?P=key_quote)"
    r"\s*:\s*(?P<value_quote>['\"]?)"
    r"protocol-v5-split-bundle-[A-Za-z0-9._-]+(?P=value_quote)",
    re.IGNORECASE,
)
_YAML_ROOT_KEY_LINE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(?P<quote>['\"]?)"
    r"(?P<key>schema_version|split_manifest|cases)(?P=quote)[ \t]*:",
)
_JSON_SCHEMA_FIELD = re.compile(
    r'"schema_version"\s*:\s*"protocol-v5-split-bundle-[A-Za-z0-9._-]+"',
    re.IGNORECASE,
)
_FENCED_BLOCK = re.compile(
    r"(?ms)^[ \t]*(?P<fence>`{3,}|~{3,})[^\r\n]*\r?\n"
    r"(?P<body>.*?)\r?\n^[ \t]*(?P=fence)[ \t]*$",
)
_ARCHIVE_MAGIC_PREFIXES = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\x1f\x8b",
    b"BZh",
    b"\xfd7zXZ\x00",
)


class IsolationAuditError(RuntimeError):
    """The audit could not safely inspect a requested input."""


@dataclass(frozen=True, slots=True, order=True)
class AuditFinding:
    """A content-free description of an isolation violation."""

    location: str
    category: str


@dataclass(frozen=True, slots=True)
class AuditReport:
    """Aggregate counts and findings from one isolation audit."""

    repository_documents_scanned: int
    archives_scanned: int
    archive_documents_scanned: int
    findings: tuple[AuditFinding, ...]

    @property
    def clean(self) -> bool:
        return not self.findings


@dataclass(slots=True)
class _ArchiveBudget:
    """Bound recursive archive work across one top-level artifact."""

    members_seen: int = 0
    bytes_read: int = 0

    def account_members(self, count: int, *, label: str) -> None:
        self.members_seen += count
        if self.members_seen > _MAX_ARCHIVE_MEMBERS:
            raise IsolationAuditError(f"archive member limit exceeded in {label}")

    def account_bytes(self, count: int, *, label: str) -> None:
        self.bytes_read += count
        if self.bytes_read > _MAX_ARCHIVE_READ_BYTES:
            raise IsolationAuditError(f"archive read limit exceeded in {label}")


def _is_document_name(name: str) -> bool:
    return Path(name).suffix.lower() in _DOCUMENT_SUFFIXES


def _is_archive_name(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES)


def _looks_like_confirmatory_bundle(raw: bytes) -> bool:
    """Recognize a suspicious bundle without exposing or logging its fields."""

    return bool(
        _SCHEMA_SIGNATURE.search(raw)
        and _CONFIRMATORY_TOKEN_SIGNATURE.search(raw)
    )


def _is_confirmatory_bundle(document: object) -> bool:
    if not isinstance(document, Mapping):
        return False
    schema_version = document.get("schema_version")
    if not (
        isinstance(schema_version, str)
        and schema_version.startswith(SPLIT_SCHEMA_PREFIX)
    ):
        return False

    split_manifest = document.get("split_manifest")
    if isinstance(split_manifest, Mapping):
        return split_manifest.get("role") == "confirmatory"

    # Retain detection if a future compatible bundle lifts role to the root.
    return document.get("role") == "confirmatory"


def _parse_documents(
    raw: bytes,
    name: str,
    *,
    strict_parse: bool,
) -> Iterator[object]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        if _looks_like_confirmatory_bundle(raw):
            raise IsolationAuditError(
                "a Protocol-v5 confirmatory signature was not valid UTF-8"
            ) from exc
        return

    try:
        suffix = Path(name).suffix.lower()
        if suffix == ".jsonl":
            for line in text.splitlines():
                if line.strip():
                    yield json.loads(line)
        elif suffix == ".json":
            yield json.loads(text)
        else:
            yield from yaml.safe_load_all(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        if strict_parse and _looks_like_confirmatory_bundle(raw):
            raise IsolationAuditError(
                "a Protocol-v5 confirmatory signature could not be parsed"
            ) from exc


def _contains_embedded_confirmatory_bundle(raw: bytes) -> bool:
    """Find an intact bundle embedded in prose, a fence, or a larger document.

    Exact structural schema declarations select candidates; a prose mention of
    the schema name alone is never sufficient.  This lets the repository
    document the format without teaching the audit to ignore a bundle pasted
    into Markdown, source text, or a package-data file.
    """

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # The ordinary parser owns the existing fail-closed invalid-UTF-8 path.
        return False

    seen_candidates: set[str] = set()
    probe_count = 0
    confirmatory_candidate_parse_failed = False

    def account_probe() -> None:
        nonlocal probe_count
        probe_count += 1
        if probe_count > _MAX_EMBEDDED_DOCUMENT_CANDIDATES:
            raise IsolationAuditError(
                "embedded Protocol-v5 document candidate limit exceeded"
            )

    def inspect_candidate(candidate: str) -> bool:
        nonlocal confirmatory_candidate_parse_failed
        encoded = candidate.encode("utf-8")
        if not _SCHEMA_SIGNATURE.search(encoded):
            return False
        digest = hashlib.sha256(encoded).hexdigest()
        if digest in seen_candidates:
            return False
        seen_candidates.add(digest)
        account_probe()
        try:
            documents = tuple(yaml.safe_load_all(candidate))
        except yaml.YAMLError:
            if _looks_like_confirmatory_bundle(encoded):
                confirmatory_candidate_parse_failed = True
            return False
        return any(_is_confirmatory_bundle(document) for document in documents)

    def balanced_flow_mapping(start: int) -> str | None:
        """Return one bounded YAML/JSON flow mapping that begins at ``start``."""

        stack: list[str] = []
        quote: str | None = None
        escaped = False
        cursor = start
        while cursor < len(text):
            character = text[cursor]
            if quote == '"':
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                cursor += 1
                continue
            if quote == "'":
                if character == "'":
                    if cursor + 1 < len(text) and text[cursor + 1] == "'":
                        cursor += 2
                        continue
                    quote = None
                cursor += 1
                continue
            if character in {'"', "'"}:
                quote = character
            elif character == "#":
                newline = text.find("\n", cursor + 1)
                if newline < 0:
                    return None
                cursor = newline + 1
                continue
            elif character in "{[":
                stack.append(character)
            elif character in "}]":
                expected = "{" if character == "}" else "["
                if not stack or stack[-1] != expected:
                    return None
                stack.pop()
                if not stack:
                    return text[start : cursor + 1]
            cursor += 1
        return None

    # Markdown/reStructuredText-style code fences provide an unambiguous
    # boundary, including when the surrounding prose is not valid YAML.
    for match in _FENCED_BLOCK.finditer(text):
        if inspect_candidate(textwrap.dedent(match.group("body"))):
            return True

    # Recover a YAML bundle pasted after a prose prefix.  Normally the schema
    # declaration is the first root key.  If a serializer sorted root keys, walk
    # back across the other two exact bundle root keys at the same indentation.
    root_matches = list(_YAML_ROOT_KEY_LINE.finditer(text))
    if len(root_matches) > _MAX_EMBEDDED_DOCUMENT_CANDIDATES * 4:
        raise IsolationAuditError(
            "embedded Protocol-v5 root-key candidate limit exceeded"
        )
    for schema_match in _YAML_SCHEMA_LINE.finditer(text):
        schema_start = schema_match.start()
        if inspect_candidate(textwrap.dedent(text[schema_start:])):
            return True

        schema_line = next(
            (item for item in root_matches if item.start() == schema_start),
            None,
        )
        if schema_line is None:
            continue
        indent = schema_line.group("indent")
        selected_keys: set[str] = set()
        group_start = schema_start
        for item in reversed(root_matches):
            if item.start() > schema_start or item.group("indent") != indent:
                continue
            key = item.group("key")
            if key in selected_keys:
                break
            selected_keys.add(key)
            group_start = item.start()
            if len(selected_keys) == 3:
                break
        if group_start != schema_start and inspect_candidate(
            textwrap.dedent(text[group_start:])
        ):
            return True

    # YAML also permits a complete bundle as a flow mapping.  Search only from
    # an exact schema field, inspect at most the globally bounded number of
    # preceding braces, and parse the balanced mapping rather than surrounding
    # prose.  This closes prefix/suffix wrapping without treating a schema-name
    # mention in source or documentation as a dataset.
    for signature in _YAML_FLOW_SCHEMA_FIELD.finditer(text):
        cursor = signature.start()
        brace_attempts = 0
        while True:
            brace = text.rfind("{", 0, cursor)
            if brace < 0:
                break
            brace_attempts += 1
            if brace_attempts > _MAX_EMBEDDED_DOCUMENT_CANDIDATES:
                raise IsolationAuditError(
                    "embedded Protocol-v5 flow-mapping candidate limit exceeded"
                )
            candidate = balanced_flow_mapping(brace)
            if (
                candidate is not None
                and brace <= signature.start() < brace + len(candidate)
                and inspect_candidate(candidate)
            ):
                return True
            cursor = brace

    # JSON can be raw-decoded from a root object even with arbitrary text before
    # or after it.  Probe enclosing braces backwards from the exact schema field;
    # the hard candidate cap bounds deliberately brace-heavy inputs.
    decoder = json.JSONDecoder()
    probed_braces: set[int] = set()
    for signature in _JSON_SCHEMA_FIELD.finditer(text):
        cursor = signature.start()
        while True:
            brace = text.rfind("{", 0, cursor)
            if brace < 0 or brace in probed_braces:
                break
            probed_braces.add(brace)
            account_probe()
            try:
                document, _end = decoder.raw_decode(text, brace)
            except json.JSONDecodeError:
                cursor = brace
                continue
            if _is_confirmatory_bundle(document):
                return True
            cursor = brace
    if confirmatory_candidate_parse_failed:
        raise IsolationAuditError(
            "an embedded Protocol-v5 confirmatory bundle could not be parsed"
        )
    return False


def _read_bounded(handle: Any, *, label: str) -> bytes:
    try:
        raw = handle.read(_MAX_DOCUMENT_BYTES + 1)
    except (OSError, RuntimeError) as exc:
        raise IsolationAuditError(f"could not inspect {label}") from exc
    if len(raw) > _MAX_DOCUMENT_BYTES:
        raise IsolationAuditError(f"document size limit exceeded in {label}")
    return raw


def _read_archive_member(
    handle: Any,
    *,
    declared_size: int,
    limit: int,
    label: str,
    budget: _ArchiveBudget,
) -> bytes:
    if declared_size > limit:
        raise IsolationAuditError(f"archive member size limit exceeded in {label}")
    try:
        raw = handle.read(limit + 1)
    except (OSError, RuntimeError) as exc:
        raise IsolationAuditError(f"could not inspect {label}") from exc
    budget.account_bytes(len(raw), label=label)
    if len(raw) > limit:
        raise IsolationAuditError(f"archive member size limit exceeded in {label}")
    return raw


def _read_archive_prefix(
    handle: Any,
    *,
    label: str,
    budget: _ArchiveBudget,
) -> bytes:
    try:
        raw = handle.read(_MAX_SIGNATURE_SCAN_BYTES)
    except (OSError, RuntimeError) as exc:
        raise IsolationAuditError(f"could not inspect {label}") from exc
    budget.account_bytes(len(raw), label=label)
    return raw


def _stream_contains_schema_signature(
    handle: Any,
    *,
    initial: bytes,
    label: str,
    max_total_bytes: int | None,
    budget: _ArchiveBudget | None = None,
) -> bool:
    """Scan a stream with bounded memory and cross-chunk signature matching."""

    total = len(initial)
    if _SCHEMA_SIGNATURE.search(initial):
        return True
    tail = initial[-_SIGNATURE_OVERLAP_BYTES:]
    while True:
        try:
            chunk = handle.read(_STREAM_CHUNK_BYTES)
        except (OSError, RuntimeError) as exc:
            raise IsolationAuditError(f"could not inspect {label}") from exc
        if not chunk:
            return False
        if budget is not None:
            budget.account_bytes(len(chunk), label=label)
        total += len(chunk)
        combined = tail + chunk
        if _SCHEMA_SIGNATURE.search(combined):
            return True
        if max_total_bytes is not None and total > max_total_bytes:
            raise IsolationAuditError(f"stream scan limit exceeded in {label}")
        tail = combined[-_SIGNATURE_OVERLAP_BYTES:]


def _looks_like_archive_prefix(raw: bytes) -> bool:
    return any(raw.startswith(prefix) for prefix in _ARCHIVE_MAGIC_PREFIXES) or (
        len(raw) >= 262 and raw[257:262] == b"ustar"
    )


def _archive_kind(raw: bytes) -> str | None:
    buffer = io.BytesIO(raw)
    if zipfile.is_zipfile(buffer):
        return "zip"
    buffer.seek(0)
    try:
        with tarfile.open(fileobj=buffer, mode="r:*"):
            return "tar"
    except (OSError, tarfile.TarError):
        return None


def _path_is_archive(path: Path, prefix: bytes) -> bool:
    if not _looks_like_archive_prefix(prefix):
        return False
    try:
        return zipfile.is_zipfile(path) or tarfile.is_tarfile(path)
    except OSError as exc:
        raise IsolationAuditError(
            "could not inspect a suspected repository archive"
        ) from exc


def _read_member_payload(
    handle: Any,
    *,
    declared_size: int,
    member_name: str,
    label: str,
    budget: _ArchiveBudget,
) -> tuple[bytes, bool]:
    """Read one bounded member; the boolean says the payload is complete."""

    if _is_archive_name(member_name) and declared_size > _MAX_NESTED_ARCHIVE_BYTES:
        raise IsolationAuditError(f"archive member size limit exceeded in {label}")
    if _is_document_name(member_name) and declared_size > _MAX_DOCUMENT_BYTES:
        raise IsolationAuditError(f"archive member size limit exceeded in {label}")
    if declared_size <= _MAX_NESTED_ARCHIVE_BYTES:
        return (
            _read_archive_member(
                handle,
                declared_size=declared_size,
                limit=_MAX_NESTED_ARCHIVE_BYTES,
                label=label,
                budget=budget,
            ),
            True,
        )

    prefix = _read_archive_prefix(handle, label=label, budget=budget)
    if _looks_like_archive_prefix(prefix):
        raise IsolationAuditError(f"archive member size limit exceeded in {label}")
    if _stream_contains_schema_signature(
        handle,
        initial=prefix,
        label=label,
        max_total_bytes=None,
        budget=budget,
    ):
        raise IsolationAuditError(
            f"suspected Protocol-v5 bundle exceeds the audit limit in {label}"
        )
    return prefix, False


def _inspect_member_payload(
    raw: bytes,
    *,
    complete: bool,
    declared_size: int,
    member_name: str,
    location: str,
    depth: int,
    budget: _ArchiveBudget,
) -> tuple[list[AuditFinding], int]:
    if not complete:
        return [], 0

    kind = _archive_kind(raw)
    if kind is not None:
        if depth >= _MAX_ARCHIVE_DEPTH:
            raise IsolationAuditError(
                f"archive nesting limit exceeded in {location}"
            )
        return _inspect_archive_bytes(
            raw,
            display_name=location,
            depth=depth + 1,
            budget=budget,
            kind=kind,
        )
    if _is_archive_name(member_name) or _looks_like_archive_prefix(raw):
        raise IsolationAuditError(f"unsupported or invalid archive in {location}")

    document_name = _is_document_name(member_name)
    has_signature = bool(_SCHEMA_SIGNATURE.search(raw))
    if not document_name and not has_signature:
        return [], 0
    if max(declared_size, len(raw)) > _MAX_DOCUMENT_BYTES:
        raise IsolationAuditError(
            f"suspected Protocol-v5 bundle exceeds the audit limit in {location}"
        )
    return (
        _inspect_document(
            raw,
            name=member_name,
            location=location,
            strict_parse=document_name,
        ),
        1,
    )


def _inspect_document(
    raw: bytes,
    *,
    name: str,
    location: str,
    strict_parse: bool = True,
) -> list[AuditFinding]:
    if not raw:
        return []
    if (
        _looks_like_confirmatory_bundle(raw)
        and _contains_embedded_confirmatory_bundle(raw)
    ):
        return [AuditFinding(location=location, category="confirmatory-split-bundle")]
    documents = tuple(_parse_documents(raw, name, strict_parse=strict_parse))
    if any(_is_confirmatory_bundle(document) for document in documents):
        return [AuditFinding(location=location, category="confirmatory-split-bundle")]
    return []


def _repository_location(path: Path, repository_root: Path) -> str:
    try:
        return path.relative_to(repository_root).as_posix()
    except ValueError:
        return path.name


def _walk_repository_files(repository_root: Path) -> Iterator[tuple[Path, bool]]:
    def raise_walk_error(error: OSError) -> None:
        raise IsolationAuditError("could not traverse the repository") from error

    for directory, directory_names, file_names in os.walk(
        repository_root,
        followlinks=False,
        onerror=raise_walk_error,
    ):
        symlinked_directories = sorted(
            name
            for name in directory_names
            if name not in _SKIPPED_DIRECTORY_NAMES
            and (Path(directory) / name).is_symlink()
        )
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in _SKIPPED_DIRECTORY_NAMES
            and name not in symlinked_directories
        )
        for name in symlinked_directories:
            yield Path(directory) / name, True
        for name in sorted(file_names):
            yield Path(directory) / name, False


def _external_symlink_finding(
    path: Path,
    *,
    repository_root: Path,
) -> AuditFinding | None:
    if not path.is_symlink():
        return None
    try:
        path.resolve(strict=True).relative_to(repository_root)
    except (OSError, RuntimeError, ValueError):
        return AuditFinding(
            location=_repository_location(path, repository_root),
            category="external-data-artifact-symlink",
        )
    return None


def _inspect_zip(
    source: Any,
    *,
    display_name: str,
    depth: int,
    budget: _ArchiveBudget,
) -> tuple[list[AuditFinding], int]:
    findings: list[AuditFinding] = []
    scanned = 0
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            budget.account_members(len(members), label=display_name)
            for index, member in enumerate(members):
                if member.is_dir():
                    continue
                location = f"{display_name}!member-{index}"
                with archive.open(member, "r") as handle:
                    raw, complete = _read_member_payload(
                        handle,
                        declared_size=member.file_size,
                        member_name=member.filename,
                        label=location,
                        budget=budget,
                    )
                member_findings, member_scanned = _inspect_member_payload(
                    raw,
                    complete=complete,
                    declared_size=member.file_size,
                    member_name=member.filename,
                    location=location,
                    depth=depth,
                    budget=budget,
                )
                findings.extend(member_findings)
                scanned += member_scanned
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        if isinstance(exc, IsolationAuditError):
            raise
        raise IsolationAuditError(f"could not inspect archive {display_name}") from exc
    return findings, scanned


def _inspect_tar(
    source: Any,
    *,
    display_name: str,
    depth: int,
    budget: _ArchiveBudget,
) -> tuple[list[AuditFinding], int]:
    findings: list[AuditFinding] = []
    scanned = 0
    try:
        open_kwargs = (
            {"name": source}
            if isinstance(source, (str, os.PathLike))
            else {"fileobj": source}
        )
        with tarfile.open(mode="r:*", **open_kwargs) as archive:
            members = archive.getmembers()
            budget.account_members(len(members), label=display_name)
            for index, member in enumerate(members):
                if not member.isfile():
                    continue
                location = f"{display_name}!member-{index}"
                handle = archive.extractfile(member)
                if handle is None:
                    raise IsolationAuditError(f"could not inspect {location}")
                with handle:
                    raw, complete = _read_member_payload(
                        handle,
                        declared_size=member.size,
                        member_name=member.name,
                        label=location,
                        budget=budget,
                    )
                member_findings, member_scanned = _inspect_member_payload(
                    raw,
                    complete=complete,
                    declared_size=member.size,
                    member_name=member.name,
                    location=location,
                    depth=depth,
                    budget=budget,
                )
                findings.extend(member_findings)
                scanned += member_scanned
    except (OSError, tarfile.TarError) as exc:
        if isinstance(exc, IsolationAuditError):
            raise
        raise IsolationAuditError(f"could not inspect archive {display_name}") from exc
    return findings, scanned


def _inspect_archive(
    path: Path,
    *,
    display_name: str,
) -> tuple[list[AuditFinding], int]:
    budget = _ArchiveBudget()
    try:
        if zipfile.is_zipfile(path):
            return _inspect_zip(
                path,
                display_name=display_name,
                depth=0,
                budget=budget,
            )
        if tarfile.is_tarfile(path):
            return _inspect_tar(
                path,
                display_name=display_name,
                depth=0,
                budget=budget,
            )
    except OSError as exc:
        raise IsolationAuditError(
            f"could not inspect archive {display_name}"
        ) from exc
    raise IsolationAuditError(f"unsupported or invalid archive {display_name}")


def _inspect_archive_bytes(
    raw: bytes,
    *,
    display_name: str,
    depth: int,
    budget: _ArchiveBudget,
    kind: str | None = None,
) -> tuple[list[AuditFinding], int]:
    selected_kind = _archive_kind(raw) if kind is None else kind
    buffer = io.BytesIO(raw)
    if selected_kind == "zip":
        buffer.seek(0)
        return _inspect_zip(
            buffer,
            display_name=display_name,
            depth=depth,
            budget=budget,
        )
    if selected_kind == "tar":
        buffer.seek(0)
        return _inspect_tar(
            buffer,
            display_name=display_name,
            depth=depth,
            budget=budget,
        )
    raise IsolationAuditError(f"unsupported or invalid archive {display_name}")


def audit_repository(
    repository_root: Path = REPOSITORY_ROOT,
    *,
    archives: Sequence[Path] = (),
) -> AuditReport:
    """Scan repository data files and supplied/discovered package archives."""

    try:
        repository_root = repository_root.resolve(strict=True)
    except OSError as exc:
        raise IsolationAuditError("could not resolve the repository root") from exc
    if not repository_root.is_dir():
        raise IsolationAuditError("repository root is not a directory")

    findings: list[AuditFinding] = []
    discovered_archives: list[tuple[Path, str]] = []
    repository_documents_scanned = 0

    for path, directory_symlink in _walk_repository_files(repository_root):
        symlink_finding = _external_symlink_finding(
            path,
            repository_root=repository_root,
        )
        if symlink_finding is not None:
            findings.append(symlink_finding)
            continue
        if directory_symlink:
            # The resolved in-repository target is traversed at its real path.
            continue

        location = _repository_location(path, repository_root)
        if _is_archive_name(path.name):
            discovered_archives.append((path, location))
            continue
        if _is_document_name(path.name):
            repository_documents_scanned += 1
            try:
                with path.open("rb") as handle:
                    raw = _read_bounded(handle, label=location)
            except OSError as exc:
                raise IsolationAuditError(f"could not inspect {location}") from exc
            findings.extend(
                _inspect_document(raw, name=path.name, location=location)
            )
            continue

        # Content-sniff archives and intact bundles renamed to an otherwise
        # unrecognized suffix. Oversized opaque files are streamed with bounded
        # memory so padding cannot move a schema signature beyond the audit.
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                prefix = handle.read(_MAX_SIGNATURE_SCAN_BYTES)
                content_is_archive = _path_is_archive(path, prefix)
                if content_is_archive:
                    raw = b""
                    has_schema_signature = False
                elif size <= _MAX_DOCUMENT_BYTES:
                    handle.seek(0)
                    raw = _read_bounded(handle, label=location)
                    has_schema_signature = bool(_SCHEMA_SIGNATURE.search(raw))
                else:
                    if size > _MAX_REPOSITORY_STREAM_BYTES:
                        raise IsolationAuditError(
                            f"stream scan limit exceeded in {location}"
                        )
                    raw = prefix
                    has_schema_signature = _stream_contains_schema_signature(
                        handle,
                        initial=prefix,
                        label=location,
                        max_total_bytes=_MAX_REPOSITORY_STREAM_BYTES,
                    )
        except (OSError, RuntimeError) as exc:
            if isinstance(exc, IsolationAuditError):
                raise
            raise IsolationAuditError(f"could not inspect {location}") from exc
        if content_is_archive:
            discovered_archives.append((path, location))
            continue
        if not has_schema_signature:
            continue
        if size > _MAX_DOCUMENT_BYTES:
            raise IsolationAuditError(
                f"suspected Protocol-v5 bundle exceeds the audit limit in {location}"
            )
        repository_documents_scanned += 1
        findings.extend(
            _inspect_document(
                raw,
                name=path.name,
                location=location,
                strict_parse=False,
            )
        )

    archive_inputs: list[tuple[Path, str]] = discovered_archives
    for index, supplied in enumerate(archives):
        try:
            supplied_path = Path(supplied).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise IsolationAuditError("could not resolve a supplied archive") from exc
        if not supplied_path.is_file():
            raise IsolationAuditError("supplied archive is not a file")
        # Do not disclose external custody paths (including their basenames).
        archive_inputs.append((supplied_path, f"supplied-archive-{index}"))

    archives_scanned = 0
    archive_documents_scanned = 0
    seen_archives: set[Path] = set()
    for archive_path, display_name in archive_inputs:
        resolved = archive_path.resolve(strict=True)
        if resolved in seen_archives:
            continue
        seen_archives.add(resolved)
        archives_scanned += 1
        archive_findings, documents_scanned = _inspect_archive(
            resolved,
            display_name=display_name,
        )
        findings.extend(archive_findings)
        archive_documents_scanned += documents_scanned

    return AuditReport(
        repository_documents_scanned=repository_documents_scanned,
        archives_scanned=archives_scanned,
        archive_documents_scanned=archive_documents_scanned,
        findings=tuple(sorted(set(findings))),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail if a Protocol-v5 confirmatory split bundle is present in "
            "the repository or a package archive."
        )
    )
    parser.add_argument(
        "--archive",
        action="append",
        default=[],
        type=Path,
        help="wheel, ZIP, or TAR-family archive to inspect (repeatable)",
    )
    parser.add_argument(
        "--repo-root",
        default=REPOSITORY_ROOT,
        type=Path,
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        report = audit_repository(args.repo_root, archives=args.archive)
    except (FileNotFoundError, IsolationAuditError) as exc:
        # Exceptions are deliberately content-free; do not add source excerpts.
        print(f"ERROR: Protocol-v5 isolation audit could not complete: {exc}")
        return 2

    if report.findings:
        print(
            "FAIL: Protocol-v5 isolation audit found prohibited data artifacts "
            f"({len(report.findings)} finding(s))."
        )
        for finding in report.findings:
            print(f"- {finding.category}: {finding.location}")
        return 1

    print(
        "PASS: Protocol-v5 isolation audit found no confirmatory split bundles "
        f"({report.repository_documents_scanned} repository document(s), "
        f"{report.archives_scanned} archive(s), "
        f"{report.archive_documents_scanned} archive document(s) inspected)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
