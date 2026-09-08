"""Authoritative immutability checks for Protocol-v5 evidence replacement.

The ordinary manifest validation contract describes an incoming object.  An
override is a two-sided trust decision: the existing run and addressed
evidence must also be loaded and validated before any byte is replaced.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .schemas import EvidenceStatus, ProtocolV5Manifest, SplitStage
from .validation import validate_manifest


class EvidenceImmutabilityError(PermissionError):
    """An override attempted to replace claim-sensitive evidence."""


@dataclass(frozen=True, slots=True)
class ExistingEvidenceIdentity:
    """Validated identity of the existing run and optional addressed JSON."""

    manifest: ProtocolV5Manifest | None
    target_sha256: str | None
    target_markers: tuple[str, ...]


def _strict_json_mapping(raw: bytes, *, label: str) -> dict[str, Any]:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        selected: dict[str, Any] = {}
        for key, value in pairs:
            if key in selected:
                raise ValueError(f"{label} contains duplicate JSON keys")
            selected[key] = value
        return selected

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"{label} contains non-finite JSON value {value}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must contain a JSON object")
    return dict(value)


def _claim_sensitive_markers(value: object) -> tuple[str, ...]:
    """Return explicit immutable/claim-sensitive markers in JSON-like data."""

    markers: set[str] = set()
    active: set[int] = set()

    def visit(selected: object, path: str) -> None:
        if isinstance(selected, Mapping):
            identity = id(selected)
            if identity in active:
                raise ValueError("evidence identity cannot contain recursive data")
            active.add(identity)
            try:
                for raw_key, item in selected.items():
                    if not isinstance(raw_key, str) or not raw_key:
                        raise ValueError("evidence identity keys must be non-blank strings")
                    key = raw_key.casefold()
                    item_path = f"{path}.{raw_key}" if path else raw_key
                    normalized = item.strip().casefold() if isinstance(item, str) else item
                    if key in {
                        "execution_status",
                        "evidence_status",
                        "cluster_measurement_status",
                        "measurement_status",
                    } and normalized == "observed":
                        markers.add(f"{item_path}=OBSERVED")
                    if key == "status" and normalized in {
                        "observed",
                        "frozen",
                        "sealed",
                        "production",
                    }:
                        markers.add(f"{item_path}={item}")
                    if key in {
                        "role",
                        "split_role",
                        "evidence_role",
                        "stage",
                        "current_phase",
                    } and isinstance(normalized, str) and (
                        normalized == "production"
                        or normalized.startswith("confirmatory")
                    ):
                        markers.add(f"{item_path}={item}")
                    if key in {
                        "sealed",
                        "frozen",
                        "claim_eligible",
                        "claims_eligible",
                        "eligible_for_claims",
                        "production_evidence",
                    } and item is True:
                        markers.add(f"{item_path}=true")
                    if key == "claims_permitted" and item is True:
                        markers.add(f"{item_path}=true")
                    if key in {
                        "freeze_status",
                        "confirmatory_freeze_status",
                        "seal_status",
                    } and normalized in {"frozen", "sealed", "verified"}:
                        markers.add(f"{item_path}={item}")
                    if key in {"evidence_classification", "evidence_class"} and isinstance(
                        normalized, str
                    ) and any(
                        token in normalized
                        for token in ("confirmatory", "production", "claim_eligible")
                    ):
                        markers.add(f"{item_path}={item}")
                    visit(item, item_path)
            finally:
                active.remove(identity)
            return
        if isinstance(selected, (list, tuple)):
            identity = id(selected)
            if identity in active:
                raise ValueError("evidence identity cannot contain recursive data")
            active.add(identity)
            try:
                for index, item in enumerate(selected):
                    visit(item, f"{path}[{index}]")
            finally:
                active.remove(identity)

    visit(value, "")
    return tuple(sorted(markers))


def _manifest_markers(manifest: ProtocolV5Manifest) -> tuple[str, ...]:
    validate_manifest(manifest)
    markers = set(_claim_sensitive_markers(manifest.to_dict()))
    if manifest.execution_status is EvidenceStatus.OBSERVED:
        markers.add("manifest.execution_status=OBSERVED")
    if manifest.split_identity.stage is SplitStage.CONFIRMATORY:
        markers.add("manifest.split_identity.stage=confirmatory")
    return tuple(sorted(markers))


def _require_mutable(markers: tuple[str, ...], *, label: str) -> None:
    if markers:
        raise EvidenceImmutabilityError(
            f"development override prohibited because {label} is immutable or "
            "claim-sensitive: " + ", ".join(markers)
        )


def require_incoming_development_override(
    manifest: ProtocolV5Manifest,
    *,
    payload: Mapping[str, Any] | None = None,
) -> None:
    """Require both the incoming manifest and payload to be replaceable."""

    _require_mutable(_manifest_markers(manifest), label="incoming manifest")
    if payload is not None:
        _require_mutable(
            _claim_sensitive_markers(payload), label="incoming evidence"
        )


def inspect_existing_override_target(
    *,
    root: Path,
    target: Path | None = None,
) -> ExistingEvidenceIdentity:
    """Load the existing canonical manifest and addressed evidence identity."""

    manifest_path = root / "manifest.json"
    if root.exists() and not root.is_dir():
        raise EvidenceImmutabilityError(
            "existing evidence root is not a directory"
        )
    root_has_evidence = root.exists() and any(
        candidate.is_file() or candidate.is_symlink()
        for candidate in root.rglob("*")
    )
    manifest: ProtocolV5Manifest | None = None
    if manifest_path.is_file():
        if manifest_path.is_symlink():
            raise EvidenceImmutabilityError(
                "existing manifest cannot be a symbolic link"
            )
        raw_manifest = manifest_path.read_bytes()
        payload = _strict_json_mapping(raw_manifest, label="existing manifest")
        manifest = ProtocolV5Manifest.from_dict(payload)
        _require_mutable(_manifest_markers(manifest), label="existing manifest")
    elif root_has_evidence:
        raise EvidenceImmutabilityError(
            "existing evidence cannot be overridden without a validated manifest"
        )

    target_sha256: str | None = None
    target_markers: tuple[str, ...] = ()
    if target is not None and target.is_file():
        if target.is_symlink():
            raise EvidenceImmutabilityError(
                "existing target evidence cannot be a symbolic link"
            )
        raw_target = target.read_bytes()
        target_sha256 = hashlib.sha256(raw_target).hexdigest()
        payload = _strict_json_mapping(raw_target, label="existing target evidence")
        target_markers = _claim_sensitive_markers(payload)
        _require_mutable(target_markers, label="existing target evidence")
    return ExistingEvidenceIdentity(
        manifest=manifest,
        target_sha256=target_sha256,
        target_markers=target_markers,
    )


def authorize_development_override(
    manifest: ProtocolV5Manifest,
    *,
    root: Path,
    target: Path | None = None,
    payload: Mapping[str, Any] | None = None,
) -> ExistingEvidenceIdentity:
    """Fail closed unless incoming and existing states are development-mutable."""

    require_incoming_development_override(manifest, payload=payload)
    return inspect_existing_override_target(root=root, target=target)


__all__ = [
    "EvidenceImmutabilityError",
    "ExistingEvidenceIdentity",
    "authorize_development_override",
    "inspect_existing_override_target",
    "require_incoming_development_override",
]
