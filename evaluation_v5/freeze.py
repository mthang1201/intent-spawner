"""Freeze Protocol-v5 development configuration before sealed data is supplied."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
from typing import Any, Mapping

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

from .provenance import write_json_exclusive
from .schemas import PROTOCOL_VERSION
from .split_dataset import DEFAULT_DEVELOPMENT_DATASET, load_development_split


ROOT = Path(__file__).resolve().parents[1]
FREEZE_SCHEMA_VERSION = "protocol-v5-freeze-v1.0.0"
FREEZE_STATUS = "FROZEN"
DRY_RUN_STATUS = "DRY_RUN"
P3_GATE_SNAPSHOT_VERSION = "protocol-v5-p3-gate-snapshot-v1.0.0"
DEFAULT_FREEZE_ROOT = ROOT / "results_v5" / "protocol-v5.0.0" / "freezes"
FREEZE_CUSTODY_ROOT = ROOT
DEFAULT_P3_GATE_EVIDENCE = ROOT / "docs/evaluation/P3_INCREMENTAL_EVALUATION_V1.md"
CONFIRMATORY_DATASET_ENV_VAR = "PROTOCOL_V5_CONFIRMATORY_DATASET"
FREEZE_ARTIFACT_BASENAME = "freeze-manifest.json"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_GIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "protocol_version",
        "freeze_id",
        "created_at_utc",
        "status",
        "source_control",
        "configuration_snapshot",
        "integrity_rules",
    }
)


class FreezeValidationError(RuntimeError):
    """A freeze cannot be created or no longer matches current configuration."""


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


def _exact_mapping(
    value: object,
    fields: frozenset[str],
    label: str,
) -> dict[str, Any]:
    payload = _mapping(value, label)
    missing = sorted(fields - set(payload))
    extra = sorted(set(payload) - fields)
    if missing or extra:
        raise FreezeValidationError(
            f"{label} fields differ from the schema; "
            f"missing={missing}, extra_count={len(extra)}"
        )
    return payload


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
    gate_evidence = _require_repository_gate_evidence(p3_gate_evidence)

    development = load_development_split()
    catalog_path = Path(DEFAULT_CATALOG_PATH).resolve()
    catalog = load_image_catalog(str(catalog_path))
    corpus = build_candidate_corpus(image_catalog=catalog)
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
        "p3_gate": {
            "snapshot_version": P3_GATE_SNAPSHOT_VERSION,
            "status": p3_gate_status,
            "evidence_path": _relative_or_absolute(gate_evidence),
            "evidence_sha256": file_sha256(gate_evidence),
            "p3_active": p3_gate_status == "retained",
        },
        "systems": {
            "P1": {"backend_version": P1_BACKEND_VERSION},
            "P2": {
                "backend_version": P2_BACKEND_VERSION,
                "pipeline_version": P2_PIPELINE_VERSION,
            },
            "P3": {
                "backend_version": P3_BACKEND_VERSION,
                "pipeline_version": P3_PIPELINE_VERSION,
                "reranker_version": PRIMARY_RERANKER_VERSION,
                "reranker_model_id": external_model_id,
                "reranker_model_source": EXTERNAL_LLM_MODEL_ENV_VAR,
                "active": p3_gate_status == "retained",
            },
        },
        "runtime_package": {
            "version": PACKAGE_VERSION,
            "sha256": compute_package_checksum(ROOT / "recommender"),
        },
        "candidate_catalog": {
            "version": catalog["catalog_version"],
            "path": _relative_or_absolute(catalog_path),
            "file_sha256": file_sha256(catalog_path),
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
            "constraints": {
                "evaluator_version": CONSTRAINT_EVALUATOR_VERSION,
                "policy_version": CONSTRAINT_POLICY_VERSION,
                "ranker_version": DETERMINISTIC_RANKER_VERSION,
                "retrieval_rank_weight": RETRIEVAL_RANK_WEIGHT,
                "soft_preference_weight": SOFT_PREFERENCE_WEIGHT,
            },
        },
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
            "git_revision": revision,
            "git_worktree_clean": clean,
        },
        "configuration_snapshot": snapshot,
        "integrity_rules": {
            "created_before_sealed_data_supply": True,
            "sealed_data_not_read_by_freeze": True,
            "tuning_after_freeze_prohibited": True,
            "exclusive_create_no_overwrite": True,
        },
    }
    return validate_freeze_manifest(manifest, require_production=not dry_run)


def validate_freeze_manifest(
    document: object,
    *,
    require_production: bool = True,
) -> dict[str, Any]:
    """Validate the freeze envelope without recomputing current inputs."""

    root = _exact_mapping(document, _ROOT_FIELDS, "freeze manifest")
    if root["schema_version"] != FREEZE_SCHEMA_VERSION:
        raise FreezeValidationError("freeze schema_version is unsupported")
    if root["protocol_version"] != PROTOCOL_VERSION:
        raise FreezeValidationError("freeze protocol_version is unsupported")
    _safe_id(root["freeze_id"], "freeze_id")
    created = root["created_at_utc"]
    if not isinstance(created, str) or not created.endswith("Z"):
        raise FreezeValidationError("created_at_utc must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FreezeValidationError("created_at_utc is invalid") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise FreezeValidationError("created_at_utc must use UTC")
    if root["status"] not in {FREEZE_STATUS, DRY_RUN_STATUS}:
        raise FreezeValidationError("freeze status is unsupported")
    if require_production and root["status"] != FREEZE_STATUS:
        raise FreezeValidationError(
            "confirmatory access requires a production FROZEN artifact"
        )
    source = _exact_mapping(
        root["source_control"],
        frozenset({"git_revision", "git_worktree_clean"}),
        "source_control",
    )
    if not isinstance(source["git_revision"], str) or not _GIT_REVISION.fullmatch(
        source["git_revision"]
    ):
        raise FreezeValidationError("source_control.git_revision is invalid")
    if not isinstance(source["git_worktree_clean"], bool):
        raise FreezeValidationError(
            "source_control.git_worktree_clean must be boolean"
        )
    if require_production and not source["git_worktree_clean"]:
        raise FreezeValidationError("production freeze must record a clean worktree")
    snapshot = _mapping(root["configuration_snapshot"], "configuration_snapshot")
    required_snapshot = {
        "p3_gate",
        "systems",
        "runtime_package",
        "candidate_catalog",
        "indexes",
        "prompts",
        "configuration",
        "development_dataset",
        "environment",
    }
    if set(snapshot) != required_snapshot:
        raise FreezeValidationError(
            "configuration_snapshot fields differ from the schema"
        )
    rules = _exact_mapping(
        root["integrity_rules"],
        frozenset(
            {
                "created_before_sealed_data_supply",
                "sealed_data_not_read_by_freeze",
                "tuning_after_freeze_prohibited",
                "exclusive_create_no_overwrite",
            }
        ),
        "integrity_rules",
    )
    if not all(value is True for value in rules.values()):
        raise FreezeValidationError("freeze integrity rules must all be true")
    try:
        json.dumps(root, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise FreezeValidationError("freeze must contain finite JSON data") from exc
    return root


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


def verify_freeze_artifact(path: Path) -> dict[str, Any]:
    """Require an authoritative freeze and fail when any current input drifted."""

    artifact, path_freeze_id = _require_authoritative_freeze_artifact(path)
    try:
        raw = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FreezeValidationError(
            "freeze artifact could not be read as valid JSON"
        ) from exc
    manifest = validate_freeze_manifest(raw, require_production=True)
    if manifest["freeze_id"] != path_freeze_id:
        raise FreezeValidationError(
            "freeze manifest identity does not match its authoritative directory"
        )
    revision, clean = _git_state()
    source = manifest["source_control"]
    if not clean or revision != source["git_revision"]:
        raise FreezeValidationError(
            "current Git state does not match the production freeze"
        )
    recorded = manifest["configuration_snapshot"]
    gate = _mapping(recorded["p3_gate"], "configuration_snapshot.p3_gate")
    evidence = _resolve_recorded_path(
        gate.get("evidence_path"), "configuration_snapshot.p3_gate.evidence_path"
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
    return manifest


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
    args = _parser().parse_args(argv)
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
    "DEFAULT_FREEZE_ROOT",
    "DEFAULT_P3_GATE_EVIDENCE",
    "DRY_RUN_STATUS",
    "FREEZE_ARTIFACT_BASENAME",
    "FREEZE_SCHEMA_VERSION",
    "FREEZE_STATUS",
    "FreezeValidationError",
    "build_configuration_snapshot",
    "build_freeze_manifest",
    "create_freeze_artifact",
    "validate_freeze_manifest",
    "verify_freeze_artifact",
]
