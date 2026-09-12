"""Freeze Protocol-v5 development configuration before sealed data is supplied."""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping as MappingABC
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from evaluation_v4.dataset import file_sha256
from recommender.candidate_corpus import build_candidate_corpus
from recommender.constraint_evaluator import (
    CONSTRAINT_EVALUATOR_VERSION,
    CONSTRAINT_POLICY_VERSION,
    DETERMINISTIC_RANKER_VERSION,
    RETRIEVAL_RANK_WEIGHT,
    SOFT_PREFERENCE_WEIGHT,
)
from recommender.deployment import (
    PACKAGE_VERSION,
    compute_package_checksum,
)
from recommender.dynamic_resources import (
    DEFAULT_RESOURCE_POLICY_PATH,
    load_resource_policy,
    resource_policy_hash,
)
from recommender.external_llm import (
    ExternalLLMConfig,
    MODEL_ENV_VAR as EXTERNAL_LLM_MODEL_ENV_VAR,
    PRICING_CONFIG_PATH_ENV_VAR,
)
from recommender.local_structured_intent import (
    LOCAL_EXTRACTOR_MODEL_ID,
    LOCAL_EXTRACTOR_NAME,
    LOCAL_EXTRACTOR_PROMPT_SHA256,
    LOCAL_EXTRACTOR_PROMPT_VERSION,
    LOCAL_EXTRACTOR_VERSION,
)
from recommender.p2_backend import (
    P2_BACKEND_VERSION,
    P2_PIPELINE_VERSION,
    P2Config,
    P2Recommender,
)
from recommender.p3_backend import P3_BACKEND_VERSION, P3_PIPELINE_VERSION, P3Config
from recommender.p3_reranker import (
    P3_RERANKING_PROMPT_SHA256,
    P3_RERANKING_PROMPT_VERSION,
    PRIMARY_RERANKER_VERSION,
)
from recommender.rule_based import (
    BACKEND_VERSION as P1_BACKEND_VERSION,
    DEFAULT_CATALOG_PATH,
    load_image_catalog,
)
from recommender.structured_intent import (
    EXTRACTION_PROMPT_SHA256,
    EXTRACTION_PROMPT_VERSION,
    PRIMARY_EXTRACTOR_NAME,
    PRIMARY_EXTRACTOR_VERSION,
    create_primary_structured_intent_extractor,
)
from recommender.models import (
    RESOURCE_CONSTRAINTS_SCHEMA_VERSION,
    STRUCTURED_INTENT_SCHEMA_VERSION,
)

from .provenance import write_json_exclusive
from .schemas import PROTOCOL_VERSION
from .split_dataset import DEFAULT_DEVELOPMENT_DATASET, load_development_split


ROOT = Path(__file__).resolve().parents[1]
LEGACY_FREEZE_SCHEMA_VERSION = "protocol-v5-freeze-v1.0.0"
FREEZE_SCHEMA_VERSION = "protocol-v5-freeze-v2.0.0"
PRODUCTION_FREEZE_SCHEMA_VERSION = FREEZE_SCHEMA_VERSION
FREEZE_STATUS = "FROZEN"
DRY_RUN_STATUS = "DRY_RUN"
DEFAULT_FREEZE_ROOT = ROOT / "results_v5" / "protocol-v5.0.0" / "freezes"
DEFAULT_DESIGN_SNAPSHOT = DEFAULT_FREEZE_ROOT / "frozen-configuration.json"
LEGACY_PRODUCTION_FREEZE_SCHEMA_PATH = (
    ROOT / "benchmarks_v5" / "protocol-v5-production-freeze-v1.schema.json"
)
PRODUCTION_FREEZE_SCHEMA_PATH = (
    ROOT / "benchmarks_v5" / "protocol-v5-production-freeze-v2.schema.json"
)
FREEZE_CUSTODY_ROOT = ROOT
DEFAULT_P3_GATE_EVIDENCE = (
    ROOT / "benchmarks_v5" / "protocol-v5-p3-final-inclusion-decision.json"
)
P3_EXCLUSION_SCHEMA_PATH = (
    ROOT / "benchmarks_v5" / "protocol-v5-p3-final-inclusion-decision-v1.schema.json"
)
CONFIRMATORY_DATASET_ENV_VAR = "PROTOCOL_V5_CONFIRMATORY_DATASET"
FREEZE_ARTIFACT_BASENAME = "freeze-manifest.json"
FINAL_HANDOFF_PATH = ROOT / "docs" / "evaluation" / "PROTOCOL_V5_FINAL_EXECUTION_HANDOFF.md"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_GIT_REVISION = re.compile(r"^[0-9a-f]{40}$")


class FreezeValidationError(RuntimeError):
    """A freeze cannot be created or no longer matches current configuration."""


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise FreezeValidationError(
            "freeze data must contain finite JSON values"
        ) from exc


class _ImmutableJsonMapping(MappingABC[str, Any]):
    """Expose immutable typed state while returning copies of nested JSON."""

    __slots__ = ("_json",)

    def __init__(self, document: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_json", _canonical_json(document))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def to_dict(self) -> dict[str, Any]:
        value = json.loads(self._json)
        assert isinstance(value, dict)
        return value

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.to_dict()!r})"


class DesignSnapshot(_ImmutableJsonMapping):
    """Validated configuration design; never a confirmatory capability."""


class ProductionFreezeManifest(_ImmutableJsonMapping):
    """Schema-valid production-freeze envelope, not yet source-verified."""

    @property
    def freeze_id(self) -> str:
        return str(self["freeze_id"])


_VERIFIED_FREEZE_CONSTRUCTION_KEY = object()


class VerifiedProductionFreeze(MappingABC[str, Any]):
    """A production freeze whose artifact and recorded sources were verified."""

    __slots__ = (
        "_manifest",
        "_artifact_path",
        "_artifact_sha256",
        "_freeze_artifact_commit_sha",
    )

    def __init__(
        self,
        *,
        manifest: ProductionFreezeManifest,
        artifact_path: Path,
        artifact_sha256: str,
        freeze_artifact_commit_sha: str | None = None,
        _construction_key: object,
    ) -> None:
        if _construction_key is not _VERIFIED_FREEZE_CONSTRUCTION_KEY:
            raise TypeError(
                "VerifiedProductionFreeze is produced only by verify_production_freeze()"
            )
        object.__setattr__(self, "_manifest", manifest)
        object.__setattr__(self, "_artifact_path", artifact_path)
        object.__setattr__(self, "_artifact_sha256", artifact_sha256)
        object.__setattr__(
            self, "_freeze_artifact_commit_sha", freeze_artifact_commit_sha
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("VerifiedProductionFreeze is immutable")

    @property
    def manifest(self) -> ProductionFreezeManifest:
        return self._manifest

    @property
    def artifact_path(self) -> Path:
        return self._artifact_path

    @property
    def artifact_sha256(self) -> str:
        return self._artifact_sha256

    @property
    def freeze_id(self) -> str:
        return self._manifest.freeze_id

    @property
    def frozen_execution_sha(self) -> str:
        source = self._manifest["source_control"]
        return str(source.get("frozen_execution_sha") or source.get("git_revision"))

    @property
    def freeze_artifact_commit_sha(self) -> str | None:
        return self._freeze_artifact_commit_sha

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "freeze_id": self.freeze_id,
            "freeze_manifest_sha256": self.artifact_sha256,
            "frozen_execution_sha": self.frozen_execution_sha,
            "freeze_artifact_commit_sha": self.freeze_artifact_commit_sha,
            "frozen_at_utc": self._manifest["created_at_utc"],
            "frozen_by": "authoritative_protocol_v5_freeze",
            "source": "confirmatory_freeze_manifest",
        }

    @property
    def configuration_snapshot(self) -> DesignSnapshot:
        """Return the schema-validated nested configuration as typed state."""

        return DesignSnapshot(self._manifest["configuration_snapshot"])

    def configuration_value(self, pointer: str) -> Any:
        """Read one canonical configuration value through a JSON pointer.

        Confirmatory consumers use this accessor instead of accepting a second,
        caller-supplied flat snapshot or reimplementing the freeze schema.
        """

        if not isinstance(pointer, str) or not pointer.startswith("/"):
            raise KeyError(pointer)
        current: Any = self.configuration_snapshot.to_dict()
        for encoded in pointer[1:].split("/"):
            key = encoded.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, Mapping) or key not in current:
                raise KeyError(pointer)
            current = current[key]
        return current

    def __getitem__(self, key: str) -> Any:
        return self._manifest[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._manifest)

    def __len__(self) -> int:
        return len(self._manifest)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise FreezeValidationError(
            f"{label} must be a bounded filesystem-safe identifier"
        )
    return value


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FreezeValidationError(f"{label} must be an object")
    return dict(value)


def _strict_json_document(raw: bytes, *, label: str) -> dict[str, Any]:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise FreezeValidationError(f"{label} contains duplicate JSON keys")
            value[key] = item
        return value

    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate,
            parse_constant=lambda value: (_ for _ in ()).throw(
                FreezeValidationError(
                    f"{label} contains non-finite JSON value {value}"
                )
            ),
        )
    except FreezeValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FreezeValidationError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(document, Mapping):
        raise FreezeValidationError(f"{label} must contain a JSON object")
    return dict(document)


def _production_freeze_schema(
    schema_version: str = FREEZE_SCHEMA_VERSION,
) -> dict[str, Any]:
    schema_path = (
        LEGACY_PRODUCTION_FREEZE_SCHEMA_PATH
        if schema_version == LEGACY_FREEZE_SCHEMA_VERSION
        else PRODUCTION_FREEZE_SCHEMA_PATH
    )
    if schema_version not in {LEGACY_FREEZE_SCHEMA_VERSION, FREEZE_SCHEMA_VERSION}:
        raise FreezeValidationError(
            f"unsupported production freeze schema version {schema_version!r}"
        )
    try:
        document = _strict_json_document(
            schema_path.read_bytes(),
            label="production freeze schema",
        )
        Draft202012Validator.check_schema(document)
    except (OSError, SchemaError, ValueError) as exc:
        raise FreezeValidationError(
            "the canonical production freeze schema is unavailable"
        ) from exc
    return document


def _validate_against_schema(
    document: Mapping[str, Any],
    *,
    design_snapshot: bool,
) -> None:
    schema_version = (
        LEGACY_FREEZE_SCHEMA_VERSION
        if design_snapshot
        else str(document.get("schema_version", ""))
    )
    schema = _production_freeze_schema(schema_version)
    selected_schema: Mapping[str, Any]
    if design_snapshot:
        selected_schema = {
            "$schema": schema["$schema"],
            "$ref": "#/$defs/configurationSnapshot",
            "$defs": schema["$defs"],
        }
    else:
        selected_schema = schema
    errors = sorted(
        Draft202012Validator(selected_schema).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "root"
        kind = "design snapshot" if design_snapshot else "production freeze"
        raise FreezeValidationError(
            f"{kind} schema violation at {location}: {first.message}"
        )


def _validate_snapshot_semantics(
    snapshot: Mapping[str, Any],
    *,
    require_verified_p3_gate: bool = False,
) -> None:
    gate = _mapping(snapshot.get("p3_gate"), "configuration_snapshot.p3_gate")
    verified_strict_gate = (
        gate.get("snapshot_version") == "protocol-v5-p3-gate-snapshot-v2.0.0"
        and gate.get("verification_status") == "VERIFIED"
    )
    verified_exclusion = (
        gate.get("snapshot_version")
        == "protocol-v5-p3-final-inclusion-snapshot-v1.0.0"
        and gate.get("verification_status") == "VERIFIED_EXCLUSION"
        and gate.get("status") == "not_retained"
        and gate.get("p3_active") is False
        and gate.get("claim_eligible") is False
        and gate.get("evidence_classification") == "HISTORICAL_FORMATIVE"
        and gate.get("confirmatory_data_used") is False
    )
    if require_verified_p3_gate and not (verified_strict_gate or verified_exclusion):
        raise FreezeValidationError(
            "production freeze requires a verified strict P3 gate or a verified conservative exclusion"
        )
    systems = _mapping(snapshot.get("systems"), "configuration_snapshot.systems")
    p3 = _mapping(systems.get("P3"), "configuration_snapshot.systems.P3")
    gate_active = gate.get("status") == "retained"
    if gate.get("p3_active") is not gate_active or p3.get("active") is not gate_active:
        raise FreezeValidationError(
            "P3 gate status and active identities must agree"
        )
    configuration = _mapping(
        snapshot.get("configuration"), "configuration_snapshot.configuration"
    )
    p2_configuration = _mapping(
        configuration.get("P2"), "configuration_snapshot.configuration.P2"
    )
    p3_configuration = _mapping(
        configuration.get("P3"), "configuration_snapshot.configuration.P3"
    )
    prompts = _mapping(snapshot.get("prompts"), "configuration_snapshot.prompts")
    p2_prompt = _mapping(
        prompts.get("P2_extractor"),
        "configuration_snapshot.prompts.P2_extractor",
    )
    if p2_configuration.get("extractor_mode") == "llm" and not p2_prompt.get(
        "model_id"
    ):
        raise FreezeValidationError(
            "LLM P2 extraction requires a bound model identity"
        )
    if gate_active and p3_configuration.get("reranker_mode") == "llm" and not p3.get(
        "reranker_model_id"
    ):
        raise FreezeValidationError(
            "retained LLM P3 requires a bound reranker model identity"
        )
    catalog = _mapping(
        snapshot.get("candidate_catalog"),
        "configuration_snapshot.candidate_catalog",
    )
    indexes = _mapping(snapshot.get("indexes"), "configuration_snapshot.indexes")
    for name in ("sparse", "dense", "hybrid"):
        identity = _mapping(
            indexes.get(name), f"configuration_snapshot.indexes.{name}"
        )
        if identity.get("corpus_checksum") != catalog.get("corpus_sha256"):
            raise FreezeValidationError(
                f"{name} index corpus identity does not match candidate catalog"
            )


def parse_design_snapshot(document: object) -> DesignSnapshot:
    """Validate a configuration snapshot without granting production status."""

    payload = _mapping(document, "design snapshot")
    _validate_against_schema(payload, design_snapshot=True)
    _validate_snapshot_semantics(payload, require_verified_p3_gate=False)
    return DesignSnapshot(payload)


def load_design_snapshot(path: Path = DEFAULT_DESIGN_SNAPSHOT) -> DesignSnapshot:
    """Load the legacy/current design snapshot through its explicit API."""

    try:
        payload = _strict_json_document(path.read_bytes(), label="design snapshot")
    except OSError as exc:
        raise FreezeValidationError("design snapshot could not be read") from exc
    return parse_design_snapshot(payload)


def parse_production_freeze(
    document: object,
    *,
    require_production: bool = True,
) -> ProductionFreezeManifest:
    """Parse the one canonical production-freeze schema into typed state."""

    payload = _mapping(document, "production freeze")
    _validate_against_schema(payload, design_snapshot=False)
    created = payload["created_at_utc"]
    try:
        parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FreezeValidationError("created_at_utc is invalid") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise FreezeValidationError("created_at_utc must use UTC")
    if require_production and payload["status"] != FREEZE_STATUS:
        raise FreezeValidationError(
            "confirmatory access requires a verified production FROZEN artifact"
        )
    if require_production and not payload["source_control"]["git_worktree_clean"]:
        raise FreezeValidationError("production freeze must record a clean worktree")
    _validate_snapshot_semantics(
        payload["configuration_snapshot"],
        require_verified_p3_gate=require_production,
    )
    return ProductionFreezeManifest(payload)


def _git_state() -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FreezeValidationError("Git state is unavailable") from exc
    if not _GIT_REVISION.fullmatch(revision):
        raise FreezeValidationError("Git revision must be a full lowercase commit")
    return revision, not dirty


def _git_tree(revision: str = "HEAD") -> str:
    try:
        tree = subprocess.run(
            ["git", "rev-parse", f"{revision}^{{tree}}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        # Unit fixtures relocate freeze custody outside the repository and use a
        # synthetic revision. Production custody never takes this branch.
        if FREEZE_CUSTODY_ROOT.resolve() != ROOT.resolve() and _GIT_REVISION.fullmatch(
            revision
        ):
            return revision
        raise FreezeValidationError("Git tree identity is unavailable") from exc
    if not _GIT_REVISION.fullmatch(tree):
        raise FreezeValidationError("Git tree identity must be a full lowercase SHA")
    return tree


def _relative_or_absolute(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT.resolve()))
    except ValueError:
        return str(resolved)


def _resolve_recorded_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise FreezeValidationError(f"{label} must be a non-blank path")
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _absolute_repository_path(path: Path) -> Path:
    candidate = path if path.is_absolute() else ROOT / path
    return Path(os.path.abspath(os.fspath(candidate)))


def _require_authoritative_freeze_root(output_root: Path) -> Path:
    """Require the single repository-owned namespace for production freezes."""

    selected = _absolute_repository_path(output_root)
    expected = _absolute_repository_path(DEFAULT_FREEZE_ROOT)
    custody = _absolute_repository_path(FREEZE_CUSTODY_ROOT)
    if selected != expected or not _is_within(expected, custody):
        raise FreezeValidationError(
            "production freezes require the authoritative repository freeze root"
        )
    try:
        custody_resolved = custody.resolve(strict=True)
        root_resolved = expected.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise FreezeValidationError(
            "authoritative freeze custody could not be inspected safely"
        ) from exc
    if not _is_within(root_resolved, custody_resolved):
        raise FreezeValidationError(
            "authoritative freeze root cannot resolve outside repository custody"
        )
    current = custody
    for part in expected.relative_to(custody).parts:
        current /= part
        try:
            if current.is_symlink():
                raise FreezeValidationError(
                    "authoritative freeze root cannot contain symlink components"
                )
        except OSError as exc:
            raise FreezeValidationError(
                "authoritative freeze custody could not be inspected safely"
            ) from exc
    return expected


def _require_authoritative_freeze_artifact(path: Path) -> tuple[Path, str]:
    """Resolve a canonical production freeze without opening its contents."""

    if path.name != FREEZE_ARTIFACT_BASENAME:
        raise FreezeValidationError(
            f"freeze artifact must be named {FREEZE_ARTIFACT_BASENAME}"
        )
    root = _require_authoritative_freeze_root(DEFAULT_FREEZE_ROOT)
    candidate = _absolute_repository_path(path)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise FreezeValidationError(
            "freeze artifact must be inside the authoritative freeze root"
        ) from exc
    if len(relative.parts) != 2 or relative.parts[1] != FREEZE_ARTIFACT_BASENAME:
        raise FreezeValidationError(
            "freeze artifact path must contain exactly one freeze ID directory"
        )
    freeze_id = _safe_id(relative.parts[0], "freeze artifact directory")
    for component in (candidate.parent, candidate):
        try:
            if component.is_symlink():
                raise FreezeValidationError(
                    "freeze artifact path cannot contain symlinks"
                )
        except OSError as exc:
            raise FreezeValidationError(
                "freeze artifact path could not be inspected safely"
            ) from exc
    try:
        resolved = candidate.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FreezeValidationError(
            "a prior production freeze artifact is required"
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise FreezeValidationError(
            "freeze artifact path could not be inspected safely"
        ) from exc
    expected_resolved = root_resolved / freeze_id / FREEZE_ARTIFACT_BASENAME
    if resolved != expected_resolved or not _is_within(resolved, root_resolved):
        raise FreezeValidationError(
            "freeze artifact cannot resolve outside its authoritative directory"
        )
    try:
        regular_file = resolved.is_file()
    except OSError as exc:
        raise FreezeValidationError(
            "freeze artifact path could not be inspected safely"
        ) from exc
    if not regular_file:
        raise FreezeValidationError("freeze artifact must be a regular file")
    return resolved, freeze_id


def _require_repository_file(path: Path, *, label: str) -> Path:
    """Resolve a configuration input without permitting an external-file read."""

    repository = ROOT.resolve()
    candidate = path if path.is_absolute() else ROOT / path
    lexical = Path(os.path.abspath(os.fspath(candidate)))
    if not _is_within(lexical, repository):
        raise FreezeValidationError(f"{label} must be inside the repository")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FreezeValidationError(
            f"{label} must be an existing repository file"
        ) from exc
    if not _is_within(resolved, repository):
        raise FreezeValidationError(
            f"{label} cannot resolve outside the repository"
        )
    try:
        regular_file = resolved.is_file()
    except OSError as exc:
        raise FreezeValidationError(
            f"{label} could not be inspected safely"
        ) from exc
    if not regular_file:
        raise FreezeValidationError(f"{label} must be a regular file")
    try:
        relative = resolved.relative_to(repository)
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(relative)],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise FreezeValidationError(
            f"{label} must be a tracked repository file"
        ) from exc
    return resolved


def _require_repository_gate_evidence(path: Path) -> Path:
    return _require_repository_file(path, label="P3 gate evidence")


def _validate_p3_exclusion_decision(path: Path) -> dict[str, Any]:
    """Validate a conservative, non-claimable exclusion and all source hashes."""

    decision_path = _require_repository_gate_evidence(path)
    schema_path = _require_repository_file(
        P3_EXCLUSION_SCHEMA_PATH, label="P3 exclusion decision schema"
    )
    decision = _strict_json_document(
        decision_path.read_bytes(), label="P3 exclusion decision"
    )
    schema = _strict_json_document(
        schema_path.read_bytes(), label="P3 exclusion decision schema"
    )
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise FreezeValidationError("P3 exclusion schema is invalid") from exc
    errors = sorted(
        Draft202012Validator(schema).iter_errors(decision),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "root"
        raise FreezeValidationError(
            f"P3 exclusion decision schema violation at {location}: {errors[0].message}"
        )
    verified_sources: list[dict[str, str]] = []
    for index, raw_identity in enumerate(decision["source_artifacts"]):
        identity = _mapping(raw_identity, f"source_artifacts[{index}]")
        source = _require_repository_file(
            Path(str(identity["path"])), label=f"P3 exclusion source {index}"
        )
        actual = file_sha256(source)
        if actual != identity["sha256"]:
            raise FreezeValidationError(
                f"P3 exclusion source checksum mismatch for {identity['path']}"
            )
        verified_sources.append(
            {"path": _relative_or_absolute(source), "file_sha256": actual}
        )
    return {
        "snapshot_version": "protocol-v5-p3-final-inclusion-snapshot-v1.0.0",
        "status": "not_retained",
        "p3_active": False,
        "claim_eligible": False,
        "verification_status": "VERIFIED_EXCLUSION",
        "evidence_classification": "HISTORICAL_FORMATIVE",
        "confirmatory_data_used": False,
        "decision_artifact_path": _relative_or_absolute(decision_path),
        "decision_artifact_sha256": file_sha256(decision_path),
        "future_retention_rule": decision["future_retention_rule"],
        "source_artifacts": verified_sources,
    }


def _verified_p3_gate_snapshot(
    *,
    p3_gate_status: str,
    p3_gate_evidence: Path,
) -> dict[str, Any]:
    """Verify and recompute the recorded development gate before freezing it."""

    if p3_gate_status == "not_retained":
        return _validate_p3_exclusion_decision(p3_gate_evidence)

    gate_evidence = _require_repository_gate_evidence(p3_gate_evidence)
    from .p3_gate import P3GateValidationError, verify_p3_development_decision

    try:
        gate = verify_p3_development_decision(gate_evidence)
    except (P3GateValidationError, OSError, ValueError) as exc:
        raise FreezeValidationError(
            "P3 gate evidence failed strict source verification"
        ) from exc
    if gate.decision != p3_gate_status:
        raise FreezeValidationError(
            "caller-supplied P3 gate status disagrees with the verified decision"
        )
    return gate.freeze_snapshot()


def _configured_external_model_id() -> str | None:
    """Return the exact non-secret model identity used by provider-backed stages."""

    value = os.environ.get(EXTERNAL_LLM_MODEL_ENV_VAR)
    return value if value is not None and value.strip() else None


def _validated_external_provider_environment(
) -> tuple[dict[str, str], dict[str, Any] | None]:
    """Bind file-backed provider config to its validated repository path."""

    selected = dict(os.environ)
    value = os.environ.get(PRICING_CONFIG_PATH_ENV_VAR)
    if value is None or not value.strip():
        return selected, None
    path = _require_repository_file(
        Path(value), label="external LLM pricing configuration"
    )
    # ExternalLLMConfig ordinarily resolves this environment value relative to
    # the process CWD. Replace it in the private snapshot environment so the
    # provider can only open the exact path validated above.
    selected[PRICING_CONFIG_PATH_ENV_VAR] = str(path)
    return selected, {
        "path": _relative_or_absolute(path),
        "file_sha256": file_sha256(path),
    }


def _safe_external_provider_configuration(
    config: ExternalLLMConfig,
) -> dict[str, Any]:
    """Serialize effective behavior knobs without endpoint text or credentials."""

    payload = asdict(config)
    endpoint = str(payload.pop("endpoint"))
    credential = str(payload.pop("api_key"))
    pricing = payload.get("pricing")
    if isinstance(pricing, dict):
        source = str(pricing.pop("source_provenance"))
        pricing["source_provenance_sha256"] = hashlib.sha256(
            source.encode("utf-8")
        ).hexdigest()
    payload["endpoint_sha256"] = hashlib.sha256(
        endpoint.encode("utf-8")
    ).hexdigest()
    payload["credentials_configured"] = bool(credential)
    return payload


def _canonical_sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _file_identity(path: Path) -> dict[str, str]:
    verified = _require_repository_file(path, label=f"freeze input {path}")
    return {
        "path": _relative_or_absolute(verified),
        "file_sha256": file_sha256(verified),
    }


def _contract_file_identities(paths: list[Path]) -> list[dict[str, str]]:
    return [_file_identity(path) for path in paths]


def _installed_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _experiment_contracts_snapshot() -> dict[str, Any]:
    """Bind E3/E4/E5 execution, scoring, timing, and evidence contracts."""

    from evaluation_v5.image_storage.contracts import (
        E5_RUN_SCHEMA_VERSION,
        FUNCTIONAL_EVALUATION_SCHEMA_VERSION,
        FUNCTIONAL_METRICS_SCHEMA_VERSION,
        IMAGE_PROBE_MANIFEST_SCHEMA_VERSION,
        IMAGE_PROBE_RECORD_SCHEMA_VERSION,
    )
    from evaluation_v5.image_storage.manifest import (
        MAX_PROBE_CPU_MILLICORES,
        MAX_PROBE_MEMORY_BYTES,
        MAX_PROBE_TIMEOUT_SECONDS,
    )
    from evaluation_v5.image_storage.storage_contracts import (
        DEFAULT_CATALOG_SCALES,
        SIZE_DOMAIN_COMPRESSED_OCI_BLOB,
        SIZE_DOMAIN_UNCOMPRESSED,
        STORAGE_COLLECTOR_SCHEMA_VERSION,
        STORAGE_SCHEMA_VERSION,
    )
    from evaluation_v5.resource.efficiency_analysis import (
        PRIMARY_ENDPOINTS,
        SECONDARY_ENDPOINTS,
    )
    from evaluation_v5.resource.efficiency_contracts import (
        CONDITIONS,
        EXECUTION_ORDER_ALGORITHM,
        FAMILY_COUNT,
        PARETO_OBJECTIVES,
        PRIMARY_TRIAL_COUNT,
        REPETITIONS,
    )
    from evaluation_v5.resource.efficiency_models import (
        DECISION_SCHEMA_VERSION,
        PLAN_SCHEMA_VERSION as EFFICIENCY_PLAN_SCHEMA_VERSION,
        TRIAL_SCHEMA_VERSION as EFFICIENCY_TRIAL_SCHEMA_VERSION,
    )
    from evaluation_v5.resource.manifest import (
        CPU_LATTICE_M,
        MEMORY_LATTICE_MIB,
        SCHEMA_VERSION as RESOURCE_WORKLOAD_SCHEMA_VERSION,
    )
    from evaluation_v5.resource.models import (
        TRIAL_SCHEMA_VERSION as RESOURCE_TRIAL_SCHEMA_VERSION,
    )
    from evaluation_v5.resource.planner import (
        PLAN_SCHEMA_VERSION as RESOURCE_PLAN_SCHEMA_VERSION,
    )
    from evaluation_v5.user_study.analysis import (
        ANALYSIS_SCHEMA_VERSION,
        PINNED_ANALYSIS_DEPENDENCIES,
        PINNED_ANALYSIS_REQUIRES_PYTHON,
        REPORTING_VERSION,
        SUPPORTED_PYTHON,
    )
    from evaluation_v5.user_study.assignment import (
        ASSIGNMENT_GENERATOR_VERSION,
        PARTICIPANT_TARGET,
    )
    from evaluation_v5.user_study.questionnaires import (
        ANALYSIS_PLAN,
        ANALYSIS_PLAN_SHA256,
        ANALYSIS_PLAN_VERSION,
        QUESTIONNAIRE_INSTRUMENT_VERSION,
    )
    from evaluation_v5.user_study.schemas import (
        ASSIGNMENT_SCHEMA_VERSION,
        BROWSER_TASK_SET_SCHEMA_VERSION,
        EVENT_SCHEMA_VERSION,
        STUDY_TIMING_CONTRACT,
        STUDY_TIMING_CONTRACT_SHA256,
        STUDY_TIMING_CONTRACT_VERSION,
        TASK_SET_SCHEMA_VERSION,
    )
    from evaluation_v5.user_study.scoring import FINAL_SELECTION_SCORING_VERSION

    e3_files = [
        ROOT / "evaluation_v5/user_study/analysis.py",
        ROOT / "evaluation_v5/user_study/assignment.py",
        ROOT / "evaluation_v5/user_study/questionnaires.py",
        ROOT / "evaluation_v5/user_study/schemas.py",
        ROOT / "evaluation_v5/user_study/scoring.py",
        ROOT / "benchmarks_v5/user-study-draft-v1.yaml",
        ROOT / "benchmarks_v5/protocol-v5-user-study-task-set-v1.schema.json",
        ROOT / "benchmarks_v5/protocol-v5-user-study-questionnaire-v1.schema.json",
    ]
    e4_files = [
        ROOT / "benchmarks_v5/resource-envelope-workloads-v1.yaml",
        ROOT / "benchmarks_v5/resource-envelope-semantic-independence-v1.yaml",
        ROOT / "benchmarks_v5/resource-envelope-cluster-eligibility-v1.yaml",
        ROOT / "benchmarks_v5/resource-envelope-freeze-contract-v1.yaml",
        ROOT / "benchmarks_v5/resource-efficiency-inputs-v1.yaml",
        ROOT / "benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml",
        ROOT / "benchmarks_v5/resource-efficiency-capacity-v1.yaml",
        ROOT / "benchmarks_v5/resource-allocation-crosswalk-v1.yaml",
        ROOT / "cluster_evaluation/resource-v5-image-state.yaml",
        ROOT / "evaluation_v5/resource/contracts.py",
        ROOT / "evaluation_v5/resource/efficiency_contracts.py",
        ROOT / "evaluation_v5/resource/manifest.py",
        ROOT / "evaluation_v5/resource/preflight.py",
        ROOT / "evaluation_v5/resource/runner.py",
        ROOT / "evaluation_v5/resource/efficiency_runner.py",
        ROOT / "evaluation_v5/resource/efficiency_analysis.py",
    ]
    e5_files = [
        ROOT / "evaluation_v5/image_storage/contracts.py",
        ROOT / "evaluation_v5/image_storage/__main__.py",
        ROOT / "evaluation_v5/image_storage/functional_provenance.py",
        ROOT / "evaluation_v5/image_storage/manifest.py",
        ROOT / "evaluation_v5/image_storage/runner.py",
        ROOT / "evaluation_v5/image_storage/metrics.py",
        ROOT / "evaluation_v5/image_storage/storage_contracts.py",
        ROOT / "evaluation_v5/image_storage/storage_runner.py",
        ROOT / "evaluation_v5/image_storage/storage_orchestrator.py",
        ROOT / "evaluation_v5/image_storage/validate_evidence.py",
        ROOT / "benchmarks_v5/protocol-v5-image-storage-evidence-v1.1.schema.json",
    ]
    return {
        "E3": {
            "analysis": {
                "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
                "reporting_version": REPORTING_VERSION,
                "analysis_plan_version": ANALYSIS_PLAN_VERSION,
                "analysis_plan_sha256": ANALYSIS_PLAN_SHA256,
                "analysis_plan_contract_sha256": _canonical_sha256(ANALYSIS_PLAN),
            },
            "timing": {
                "version": STUDY_TIMING_CONTRACT_VERSION,
                "sha256": STUDY_TIMING_CONTRACT_SHA256,
                "contract": STUDY_TIMING_CONTRACT,
            },
            "scoring": {
                "version": FINAL_SELECTION_SCORING_VERSION,
                "implementation": _file_identity(
                    ROOT / "evaluation_v5/user_study/scoring.py"
                ),
            },
            "workload": {
                "task_set_schema_version": TASK_SET_SCHEMA_VERSION,
                "browser_task_set_schema_version": BROWSER_TASK_SET_SCHEMA_VERSION,
                "assignment_schema_version": ASSIGNMENT_SCHEMA_VERSION,
                "event_schema_version": EVENT_SCHEMA_VERSION,
                "assignment_generator_version": ASSIGNMENT_GENERATOR_VERSION,
                "questionnaire_instrument_version": QUESTIONNAIRE_INSTRUMENT_VERSION,
                "participant_target": PARTICIPANT_TARGET,
            },
            "runtime": {
                "supported_python": SUPPORTED_PYTHON,
                "pinned_dependencies": PINNED_ANALYSIS_DEPENDENCIES,
                "dependency_python_requirements": PINNED_ANALYSIS_REQUIRES_PYTHON,
            },
            "files": _contract_file_identities(e3_files),
        },
        "E4": {
            "workload": {
                "schema_version": RESOURCE_WORKLOAD_SCHEMA_VERSION,
                "family_count": FAMILY_COUNT,
                "memory_lattice_mib": MEMORY_LATTICE_MIB,
                "cpu_lattice_millicores": CPU_LATTICE_M,
                "manifest_contract": _file_identity(
                    ROOT / "benchmarks_v5/resource-envelope-workloads-v1.yaml"
                ),
                "semantic_independence_contract": _file_identity(
                    ROOT
                    / "benchmarks_v5/resource-envelope-semantic-independence-v1.yaml"
                ),
                "efficiency_inputs_contract": _file_identity(
                    ROOT / "benchmarks_v5/resource-efficiency-inputs-v1.yaml"
                ),
            },
            "ordering": {
                "algorithm": EXECUTION_ORDER_ALGORITHM,
                "conditions": list(CONDITIONS),
                "repetitions": REPETITIONS,
                "primary_trial_count": PRIMARY_TRIAL_COUNT,
            },
            "statistics": {
                "primary_endpoints": list(PRIMARY_ENDPOINTS),
                "secondary_endpoints": list(SECONDARY_ENDPOINTS),
                "pareto_objectives": PARETO_OBJECTIVES,
                "independent_unit": "workload_family",
                "repetitions_are_independent_samples": False,
                "analysis_implementation": _file_identity(
                    ROOT / "evaluation_v5/resource/efficiency_analysis.py"
                ),
            },
            "policy": {
                "envelope_freeze_contract": _file_identity(
                    ROOT / "benchmarks_v5/resource-envelope-freeze-contract-v1.yaml"
                ),
                "efficiency_design_contract": _file_identity(
                    ROOT / "benchmarks_v5/resource-efficiency-freeze-contract-v1.yaml"
                ),
                "allocation_crosswalk_contract": _file_identity(
                    ROOT / "benchmarks_v5/resource-allocation-crosswalk-v1.yaml"
                ),
            },
            "eligibility": {
                "cluster_contract": _file_identity(
                    ROOT
                    / "benchmarks_v5/resource-envelope-cluster-eligibility-v1.yaml"
                ),
                "legacy_image_state_contract": _file_identity(
                    ROOT / "cluster_evaluation/resource-v5-image-state.yaml"
                ),
                "capacity_development_contract": _file_identity(
                    ROOT / "benchmarks_v5/resource-efficiency-capacity-v1.yaml"
                ),
            },
            "execution": {
                "resource_plan_schema_version": RESOURCE_PLAN_SCHEMA_VERSION,
                "resource_trial_schema_version": RESOURCE_TRIAL_SCHEMA_VERSION,
                "efficiency_plan_schema_version": EFFICIENCY_PLAN_SCHEMA_VERSION,
                "efficiency_decision_schema_version": DECISION_SCHEMA_VERSION,
                "efficiency_trial_schema_version": EFFICIENCY_TRIAL_SCHEMA_VERSION,
                "readiness_attestation_schema": _file_identity(
                    ROOT / "benchmarks_v5/protocol-v5-e4-readiness-attestation-v1.schema.json"
                ),
                "preflight_implementation": _file_identity(
                    ROOT / "evaluation_v5/resource/preflight.py"
                ),
                "envelope_runner_implementation": _file_identity(
                    ROOT / "evaluation_v5/resource/runner.py"
                ),
                "efficiency_runner_implementation": _file_identity(
                    ROOT / "evaluation_v5/resource/efficiency_runner.py"
                ),
            },
            "files": _contract_file_identities(e4_files),
        },
        "E5": {
            "functional_probe": {
                "manifest_schema_version": IMAGE_PROBE_MANIFEST_SCHEMA_VERSION,
                "record_schema_version": IMAGE_PROBE_RECORD_SCHEMA_VERSION,
                "evaluation_schema_version": FUNCTIONAL_EVALUATION_SCHEMA_VERSION,
                "metrics_schema_version": FUNCTIONAL_METRICS_SCHEMA_VERSION,
                "run_schema_version": E5_RUN_SCHEMA_VERSION,
                "max_timeout_seconds": MAX_PROBE_TIMEOUT_SECONDS,
                "max_cpu_millicores": MAX_PROBE_CPU_MILLICORES,
                "max_memory_bytes": MAX_PROBE_MEMORY_BYTES,
                "contracts_implementation": _file_identity(
                    ROOT / "evaluation_v5/image_storage/contracts.py"
                ),
                "manifest_implementation": _file_identity(
                    ROOT / "evaluation_v5/image_storage/manifest.py"
                ),
                "runner_implementation": _file_identity(
                    ROOT / "evaluation_v5/image_storage/runner.py"
                ),
                "metrics_implementation": _file_identity(
                    ROOT / "evaluation_v5/image_storage/metrics.py"
                ),
            },
            "collector": {
                "schema_version": STORAGE_COLLECTOR_SCHEMA_VERSION,
                "storage_runner_implementation": _file_identity(
                    ROOT / "evaluation_v5/image_storage/storage_runner.py"
                ),
                "orchestrator_implementation": _file_identity(
                    ROOT / "evaluation_v5/image_storage/storage_orchestrator.py"
                ),
                "evidence_validator_implementation": _file_identity(
                    ROOT / "evaluation_v5/image_storage/validate_evidence.py"
                ),
            },
            "platform": {
                "operating_system": "linux",
                "architecture": "amd64",
                "exact_manifest_selection_required": True,
            },
            "storage": {
                "evidence_schema_version": STORAGE_SCHEMA_VERSION,
                "collector_schema_version": STORAGE_COLLECTOR_SCHEMA_VERSION,
                "evidence_schema": _file_identity(
                    ROOT
                    / "benchmarks_v5/protocol-v5-image-storage-evidence-v1.1.schema.json"
                ),
                "allowed_size_domains": [
                    SIZE_DOMAIN_COMPRESSED_OCI_BLOB,
                    SIZE_DOMAIN_UNCOMPRESSED,
                ],
                "catalog_scales": list(DEFAULT_CATALOG_SCALES),
            },
            "files": _contract_file_identities(e5_files),
        },
    }


def build_configuration_snapshot(
    *,
    p3_gate_status: str,
    p3_gate_evidence: Path,
) -> dict[str, Any]:
    """Build deterministic configuration identity without evaluating any cases."""

    if p3_gate_status not in {"retained", "not_retained"}:
        raise FreezeValidationError(
            "p3_gate_status must be retained or not_retained"
        )
    gate_snapshot = _verified_p3_gate_snapshot(
        p3_gate_status=p3_gate_status,
        p3_gate_evidence=p3_gate_evidence,
    )

    development = load_development_split()
    catalog_path = Path(DEFAULT_CATALOG_PATH).resolve()
    catalog = load_image_catalog(str(catalog_path))
    corpus = build_candidate_corpus(image_catalog=catalog)
    resource_policy_path = Path(DEFAULT_RESOURCE_POLICY_PATH).resolve()
    resource_policy = load_resource_policy(resource_policy_path)
    # These are the same environment-aware constructors used by the ordinary
    # backend composition paths.  Recomputing them during verification makes a
    # post-freeze environment override configuration drift rather than an
    # unrecorded change in comparator behavior.
    p2_config = P2Config.from_environ()
    p3_config = P3Config.from_environ()
    p3_provider_active = (
        p3_gate_status == "retained" and p3_config.reranker_mode == "llm"
    )
    provider_active = p2_config.extractor_mode == "llm" or p3_provider_active
    provider_environ, pricing_configuration = (
        _validated_external_provider_environment()
        if provider_active
        else (dict(os.environ), None)
    )
    external_provider = (
        ExternalLLMConfig.from_environ(provider_environ)
        if provider_active
        else None
    )
    p2_extractor = (
        create_primary_structured_intent_extractor(config=external_provider)
        if p2_config.extractor_mode == "llm" and external_provider is not None
        else None
    )
    p2 = P2Recommender(
        config=p2_config,
        catalog=catalog,
        corpus=corpus,
        extractor=p2_extractor,
    )
    dense = p2.retriever.dense_retriever.metadata
    sparse = p2.retriever.sparse_retriever.metadata
    hybrid = p2.retriever.metadata
    if p2_config.extractor_mode == "llm":
        selected_provider = getattr(p2.extractor, "config", None)
        if not isinstance(selected_provider, ExternalLLMConfig):
            raise FreezeValidationError(
                "P2 LLM extractor did not expose its effective provider configuration"
            )
        if selected_provider != external_provider:
            raise FreezeValidationError(
                "P2 LLM extractor provider configuration does not match the freeze"
            )
    safe_provider = (
        _safe_external_provider_configuration(external_provider)
        if external_provider is not None
        else None
    )
    external_model_id = _configured_external_model_id()
    if p2_config.extractor_mode == "llm":
        p2_extractor_identity = {
            "name": PRIMARY_EXTRACTOR_NAME,
            "version": PRIMARY_EXTRACTOR_VERSION,
            "model_id": external_model_id,
            "model_source": EXTERNAL_LLM_MODEL_ENV_VAR,
            "prompt_version": EXTRACTION_PROMPT_VERSION,
            "prompt_sha256": EXTRACTION_PROMPT_SHA256,
        }
    else:
        p2_extractor_identity = {
            "name": LOCAL_EXTRACTOR_NAME,
            "version": LOCAL_EXTRACTOR_VERSION,
            "model_id": LOCAL_EXTRACTOR_MODEL_ID,
            "model_source": "built_in_local_model",
            "prompt_version": LOCAL_EXTRACTOR_PROMPT_VERSION,
            "prompt_sha256": LOCAL_EXTRACTOR_PROMPT_SHA256,
        }

    return {
        "p3_gate": gate_snapshot,
        "systems": {
            "P1": {
                "backend_version": P1_BACKEND_VERSION,
                "implementation": _file_identity(ROOT / "recommender/rule_based.py"),
            },
            "P2": {
                "backend_version": P2_BACKEND_VERSION,
                "pipeline_version": P2_PIPELINE_VERSION,
                "implementation": _file_identity(ROOT / "recommender/p2_backend.py"),
            },
            "P3": {
                "backend_version": P3_BACKEND_VERSION,
                "pipeline_version": P3_PIPELINE_VERSION,
                "implementation": _file_identity(ROOT / "recommender/p3_backend.py"),
                "reranker_version": PRIMARY_RERANKER_VERSION,
                "reranker_model_id": external_model_id,
                "reranker_model_source": EXTERNAL_LLM_MODEL_ENV_VAR,
                "active": p3_gate_status == "retained",
            },
        },
        "structured_intent": {
            "schema_version": STRUCTURED_INTENT_SCHEMA_VERSION,
            "resource_constraints_schema_version": RESOURCE_CONSTRAINTS_SCHEMA_VERSION,
            "model_contract": _file_identity(ROOT / "recommender/models.py"),
            "extractor_implementation": _file_identity(
                ROOT
                / (
                    "recommender/structured_intent.py"
                    if p2_config.extractor_mode == "llm"
                    else "recommender/local_structured_intent.py"
                )
            ),
        },
        "runtime_package": {
            "version": PACKAGE_VERSION,
            "sha256": compute_package_checksum(ROOT / "recommender"),
        },
        "candidate_catalog": {
            "version": catalog["catalog_version"],
            "path": _relative_or_absolute(catalog_path),
            "file_sha256": file_sha256(catalog_path),
            "image_catalog_canonical_sha256": corpus.source_image_catalog_checksum,
            "profile_catalog_canonical_sha256": corpus.source_profile_catalog_checksum,
            "corpus_version": corpus.corpus_version,
            "corpus_sha256": corpus.corpus_checksum,
            "candidate_count": len(corpus.candidates),
        },
        "indexes": {
            "sparse": asdict(sparse),
            "dense": asdict(dense),
            "hybrid": asdict(hybrid),
            "source": "administrator_catalog_only",
        },
        "prompts": {
            "P2_extractor": p2_extractor_identity,
            "P3_reranker": {
                "prompt_version": P3_RERANKING_PROMPT_VERSION,
                "prompt_sha256": P3_RERANKING_PROMPT_SHA256,
            },
        },
        "configuration": {
            "P2": asdict(p2_config),
            "P3": asdict(p3_config),
            "provider": {
                "P2_extractor": (
                    safe_provider if p2_config.extractor_mode == "llm" else None
                ),
                "P3_reranker": safe_provider if p3_provider_active else None,
                "pricing_configuration": pricing_configuration,
            },
            "retrieval": {
                "sparse_top_k": p2_config.sparse_top_k,
                "dense_top_k": p2_config.dense_top_k,
                "top_k": p2_config.top_k,
                "sparse_weight": p2_config.sparse_weight,
                "dense_weight": p2_config.dense_weight,
                "rrf_k": p2_config.rrf_k,
            },
            "constraints": {
                "evaluator_version": CONSTRAINT_EVALUATOR_VERSION,
                "policy_version": CONSTRAINT_POLICY_VERSION,
                "ranker_version": DETERMINISTIC_RANKER_VERSION,
                "retrieval_rank_weight": RETRIEVAL_RANK_WEIGHT,
                "soft_preference_weight": SOFT_PREFERENCE_WEIGHT,
            },
            "ranking": {
                "ranker_version": DETERMINISTIC_RANKER_VERSION,
                "retrieval_rank_weight": RETRIEVAL_RANK_WEIGHT,
                "soft_preference_weight": SOFT_PREFERENCE_WEIGHT,
                "tie_breaker": "candidate_id",
            },
        },
        "dynamic_resource_policy": {
            "path": _relative_or_absolute(resource_policy_path),
            "policy_version": resource_policy.policy_version,
            "file_sha256": file_sha256(resource_policy_path),
            "semantic_sha256": resource_policy_hash(resource_policy),
        },
        "experiment_contracts": _experiment_contracts_snapshot(),
        "development_dataset": {
            "dataset_id": development.manifest.dataset_id,
            "split_id": development.manifest.split_id,
            "role": development.manifest.role.value,
            "schema_version": development.bundle.schema_version,
            "canonical_sha256": development.manifest.checksum,
            "file_sha256": development.source_file_sha256,
            "case_count": development.manifest.case_count,
            "family_count": development.manifest.family_count,
            "path": _relative_or_absolute(DEFAULT_DEVELOPMENT_DATASET),
        },
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        },
        "environment_requirements": {
            "python": ">=3.12,<3.15",
            "dependencies": {
                name: _installed_version(name)
                for name in (
                    "PyYAML",
                    "jsonschema",
                    "jupyterhub",
                    "numpy",
                    "scipy",
                    "pandas",
                    "matplotlib",
                    "statsmodels",
                    "patsy",
                )
            },
            "cluster": {
                "contract": "external_checksum_bound_e4_readiness_attestation",
                "real_kubernetes_required_for_observed_e4": True,
                "disposable_nonproduction_cluster_required": True,
            },
            "storage": {
                "real_registry_collector_required_for_observed_e5": True,
                "digest_pinned_images_required": True,
                "platform": {"os": "linux", "architecture": "amd64"},
            },
        },
    }


def build_freeze_manifest(
    *,
    freeze_id: str,
    p3_gate_status: str,
    p3_gate_evidence: Path = DEFAULT_P3_GATE_EVIDENCE,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a complete in-memory snapshot; never read a sealed dataset."""

    _safe_id(freeze_id, "freeze_id")
    if CONFIRMATORY_DATASET_ENV_VAR in os.environ:
        raise FreezeValidationError(
            "freeze is prohibited while the sealed-dataset environment is present"
        )
    revision, clean = _git_state()
    if not dry_run and not clean:
        raise FreezeValidationError(
            "production freeze requires a clean Git worktree"
        )
    snapshot = build_configuration_snapshot(
        p3_gate_status=p3_gate_status,
        p3_gate_evidence=p3_gate_evidence,
    )
    manifest = {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "freeze_id": freeze_id,
        "created_at_utc": _now_utc(),
        "status": DRY_RUN_STATUS if dry_run else FREEZE_STATUS,
        "source_control": {
            "frozen_execution_sha": revision,
            "frozen_execution_tree": _git_tree(revision),
            "git_worktree_clean": clean,
            "freeze_artifact_commit_recorded_in_manifest": False,
        },
        "configuration_snapshot": snapshot,
        "integrity_rules": {
            "created_before_sealed_data_supply": True,
            "sealed_data_not_read_by_freeze": True,
            "tuning_after_freeze_prohibited": True,
            "exclusive_create_no_overwrite": True,
            "post_freeze_changes_restricted": True,
            "allowed_post_freeze_paths": [
                f"results_v5/protocol-v5.0.0/freezes/{freeze_id}/{FREEZE_ARTIFACT_BASENAME}",
                "docs/evaluation/PROTOCOL_V5_FINAL_EXECUTION_HANDOFF.md",
            ],
        },
    }
    return validate_freeze_manifest(manifest, require_production=not dry_run)


def validate_freeze_manifest(
    document: object,
    *,
    require_production: bool = True,
) -> dict[str, Any]:
    """Compatibility adapter for the canonical typed production parser."""

    return parse_production_freeze(
        document, require_production=require_production
    ).to_dict()


def create_freeze_artifact(
    *,
    freeze_id: str,
    p3_gate_status: str,
    p3_gate_evidence: Path = DEFAULT_P3_GATE_EVIDENCE,
    output_root: Path = DEFAULT_FREEZE_ROOT,
) -> Path:
    """Exclusively create one production freeze directory and JSON artifact."""

    authoritative_root = _require_authoritative_freeze_root(output_root)
    manifest = build_freeze_manifest(
        freeze_id=freeze_id,
        p3_gate_status=p3_gate_status,
        p3_gate_evidence=p3_gate_evidence,
        dry_run=False,
    )
    target = authoritative_root / freeze_id
    target.mkdir(parents=True, exist_ok=False)
    return write_json_exclusive(target / FREEZE_ARTIFACT_BASENAME, manifest)


def verify_production_freeze(path: Path) -> VerifiedProductionFreeze:
    """Verify the authoritative artifact and recompute every recorded source."""

    artifact, path_freeze_id = _require_authoritative_freeze_artifact(path)
    try:
        raw_bytes = artifact.read_bytes()
        raw = _strict_json_document(raw_bytes, label="production freeze artifact")
    except OSError as exc:
        raise FreezeValidationError(
            "freeze artifact could not be read as valid JSON"
        ) from exc
    manifest = parse_production_freeze(raw, require_production=True)
    if manifest.freeze_id != path_freeze_id:
        raise FreezeValidationError(
            "freeze manifest identity does not match its authoritative directory"
        )
    revision, clean = _git_state()
    source = manifest["source_control"]
    artifact_commit: str | None = None
    if manifest["schema_version"] == LEGACY_FREEZE_SCHEMA_VERSION:
        if not clean or revision != source["git_revision"]:
            raise FreezeValidationError(
                "current Git state does not match the legacy production freeze"
            )
    else:
        if not clean:
            raise FreezeValidationError("production freeze verification requires a clean worktree")
        frozen_execution = str(source["frozen_execution_sha"])
        if _git_tree(frozen_execution) != source["frozen_execution_tree"]:
            raise FreezeValidationError("frozen execution tree identity does not match Git")
        relocated_fixture = FREEZE_CUSTODY_ROOT.resolve() != ROOT.resolve()
        if relocated_fixture:
            if revision != frozen_execution:
                raise FreezeValidationError(
                    "relocated freeze fixtures must match the frozen execution revision"
                )
        else:
            try:
                subprocess.run(
                    ["git", "merge-base", "--is-ancestor", frozen_execution, revision],
                    cwd=ROOT,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise FreezeValidationError(
                    "current checkout is not a descendant of the frozen execution"
                ) from exc

        if _is_within(artifact, ROOT.resolve()):
            relative_artifact = str(artifact.relative_to(ROOT.resolve()))
            try:
                additions = subprocess.run(
                    [
                        "git", "log", "--format=%H", "--diff-filter=A", "--",
                        relative_artifact,
                    ],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.splitlines()
            except (OSError, subprocess.SubprocessError) as exc:
                raise FreezeValidationError(
                    "freeze artifact introduction commit could not be discovered"
                ) from exc
            if len(additions) != 1 or not _GIT_REVISION.fullmatch(additions[0]):
                raise FreezeValidationError(
                    "freeze artifact must have exactly one Git introduction commit"
                )
            artifact_commit = additions[0]
            parent = subprocess.run(
                ["git", "rev-parse", f"{artifact_commit}^"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if parent != frozen_execution:
                raise FreezeValidationError(
                    "freeze artifact commit must be the immediate child of the frozen execution"
                )
            artifact_change = subprocess.run(
                [
                    "git", "diff-tree", "--no-commit-id", "--name-only", "-r",
                    parent, artifact_commit,
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            if artifact_change != [relative_artifact]:
                raise FreezeValidationError(
                    "freeze artifact commit contains changes beyond the manifest"
                )
            allowed = set(manifest["integrity_rules"]["allowed_post_freeze_paths"])
            changed = set(
                subprocess.run(
                    ["git", "diff", "--name-only", frozen_execution, revision],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.splitlines()
            )
            if not changed.issubset(allowed) or relative_artifact not in changed:
                prohibited = sorted(changed - allowed)
                raise FreezeValidationError(
                    "post-freeze executable/configuration change detected"
                    + (": " + ", ".join(prohibited) if prohibited else "")
                )
            committed_bytes = subprocess.run(
                ["git", "show", f"{artifact_commit}:{relative_artifact}"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            ).stdout
            if committed_bytes != raw_bytes:
                raise FreezeValidationError(
                    "freeze artifact bytes differ from the introduction commit"
                )
        elif not relocated_fixture and revision != frozen_execution:
            raise FreezeValidationError(
                "relocated freeze fixtures must match the frozen execution revision"
            )
    recorded = manifest["configuration_snapshot"]
    gate = _mapping(recorded["p3_gate"], "configuration_snapshot.p3_gate")
    evidence = _resolve_recorded_path(
        gate.get("decision_artifact_path"),
        "configuration_snapshot.p3_gate.decision_artifact_path",
    )
    current = build_configuration_snapshot(
        p3_gate_status=str(gate.get("status")),
        p3_gate_evidence=evidence,
    )
    if current != recorded:
        changed = sorted(
            key for key in set(current) | set(recorded) if current.get(key) != recorded.get(key)
        )
        raise FreezeValidationError(
            "frozen Protocol-v5 inputs changed: " + ", ".join(changed)
        )
    return VerifiedProductionFreeze(
        manifest=manifest,
        artifact_path=artifact,
        artifact_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        freeze_artifact_commit_sha=artifact_commit,
        _construction_key=_VERIFIED_FREEZE_CONSTRUCTION_KEY,
    )


def reverify_production_freeze(
    freeze: VerifiedProductionFreeze,
) -> VerifiedProductionFreeze:
    """Revalidate a capability at a downstream execution/analysis boundary."""

    if not isinstance(freeze, VerifiedProductionFreeze):
        raise TypeError(
            "a VerifiedProductionFreeze from verify_production_freeze() is required"
        )
    current = verify_production_freeze(freeze.artifact_path)
    if (
        current.freeze_id != freeze.freeze_id
        or current.artifact_sha256 != freeze.artifact_sha256
        or current.freeze_artifact_commit_sha != freeze.freeze_artifact_commit_sha
        or current.manifest.to_dict() != freeze.manifest.to_dict()
    ):
        raise FreezeValidationError(
            "production freeze capability no longer matches its verified artifact"
        )
    return current


def verify_freeze_artifact(path: Path) -> VerifiedProductionFreeze:
    """Compatibility name for :func:`verify_production_freeze`."""

    return verify_production_freeze(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze Protocol-v5 development configuration before confirmation."
    )
    parser.add_argument("--freeze-id", required=True)
    parser.add_argument(
        "--p3-gate-status",
        choices=("retained", "not_retained"),
        required=True,
    )
    parser.add_argument(
        "--p3-gate-evidence", type=Path, default=DEFAULT_P3_GATE_EVIDENCE
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_FREEZE_ROOT,
        help="Must resolve to the authoritative repository freeze root.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print a non-authoritative snapshot without writing an artifact.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    selected_argv = list(sys.argv[1:] if argv is None else argv)
    if selected_argv[:1] == ["verify"]:
        verify_parser = argparse.ArgumentParser(
            description="Verify an authoritative Protocol-v5 final execution freeze."
        )
        verify_parser.add_argument("--freeze", type=Path, required=True)
        verify_args = verify_parser.parse_args(selected_argv[1:])
        try:
            verified = verify_production_freeze(verify_args.freeze)
            print(
                json.dumps(
                    {
                        "schema_version": FREEZE_SCHEMA_VERSION,
                        "status": "VERIFIED",
                        **verified.identity,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        except (FreezeValidationError, OSError, ValueError) as exc:
            print(
                json.dumps(
                    {
                        "schema_version": FREEZE_SCHEMA_VERSION,
                        "status": "ERROR",
                        "error": str(exc),
                    },
                    sort_keys=True,
                )
            )
            return 2
    args = _parser().parse_args(selected_argv)
    try:
        if args.dry_run:
            result: Mapping[str, Any] = build_freeze_manifest(
                freeze_id=args.freeze_id,
                p3_gate_status=args.p3_gate_status,
                p3_gate_evidence=args.p3_gate_evidence,
                dry_run=True,
            )
        else:
            target = create_freeze_artifact(
                freeze_id=args.freeze_id,
                p3_gate_status=args.p3_gate_status,
                p3_gate_evidence=args.p3_gate_evidence,
                output_root=args.output_root,
            )
            result = {
                "schema_version": FREEZE_SCHEMA_VERSION,
                "status": FREEZE_STATUS,
                "freeze_id": args.freeze_id,
                "artifact": str(target),
            }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (FreezeValidationError, FileExistsError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": FREEZE_SCHEMA_VERSION,
                    "status": "ERROR",
                    "error": str(exc),
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_DESIGN_SNAPSHOT",
    "DEFAULT_FREEZE_ROOT",
    "DEFAULT_P3_GATE_EVIDENCE",
    "DRY_RUN_STATUS",
    "FREEZE_ARTIFACT_BASENAME",
    "FREEZE_SCHEMA_VERSION",
    "FREEZE_STATUS",
    "PRODUCTION_FREEZE_SCHEMA_PATH",
    "LEGACY_PRODUCTION_FREEZE_SCHEMA_PATH",
    "LEGACY_FREEZE_SCHEMA_VERSION",
    "PRODUCTION_FREEZE_SCHEMA_VERSION",
    "DesignSnapshot",
    "FreezeValidationError",
    "ProductionFreezeManifest",
    "VerifiedProductionFreeze",
    "build_configuration_snapshot",
    "build_freeze_manifest",
    "create_freeze_artifact",
    "load_design_snapshot",
    "parse_design_snapshot",
    "parse_production_freeze",
    "reverify_production_freeze",
    "validate_freeze_manifest",
    "verify_freeze_artifact",
    "verify_production_freeze",
]
