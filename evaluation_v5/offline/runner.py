"""Provenance-safe raw evidence runner for Protocol-v5 offline recommendations.

This module records executions only.  It deliberately creates no aggregate
metrics, statistical tests, or performance claims.  Gold labels are isolated
from adapters and retained in raw evaluation evidence solely so later metric
code can derive the registered end-to-end outcomes.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Any

from evaluation_v4.dataset import file_sha256
from evaluation_v5.provenance import write_json_exclusive
from evaluation_v5.split_dataset import LoadedSplit, SplitCase

from .recommenders import (
    OfflineAdapterResult,
    OfflineCaseInput,
    OfflineSystemAdapter,
    SYSTEM_IDS,
    default_adapters,
)


OFFLINE_RUNNER_SCHEMA_VERSION = "protocol-v5-offline-recommendation-runner-v1.0.0"
OFFLINE_RAW_RECORD_SCHEMA_VERSION = "protocol-v5-offline-recommendation-record-v1.0.0"
OFFLINE_PROVENANCE_SCHEMA_VERSION = "protocol-v5-offline-recommendation-provenance-v1.0.0"
OFFLINE_COMPLETION_SCHEMA_VERSION = "protocol-v5-offline-recommendation-completion-v1.0.0"
REPEAT_POLICY_VERSION = "protocol-v5-repeat-policy-v1.0.0"

RAW_DIRECTORY_NAME = "raw"
REPORT_DIRECTORY_NAME = "report"
PROVENANCE_FILENAME = "offline-run-provenance.json"
RECORDS_FILENAME = "recommendations.jsonl"
COMPLETION_FILENAME = "offline-run-completion.json"
LOCK_FILENAME = ".offline-run.lock"


class OfflineRunnerError(RuntimeError):
    """Base error for a refused or malformed offline evidence run."""


class ProvenanceMismatchError(OfflineRunnerError):
    """Existing evidence belongs to a different immutable execution plan."""


class DuplicateRecordError(OfflineRunnerError):
    """Raw evidence contains more than one row for one matrix cell."""


class EvidenceRecordError(OfflineRunnerError):
    """Raw evidence is malformed or cannot safely be resumed."""


@dataclass(frozen=True, slots=True)
class MatrixEntry:
    """One independent case/system execution; repeats never expand families."""

    case: SplitCase
    system_id: str
    repeat_index: int
    seed: int

    @property
    def key(self) -> tuple[str, str, str, str, int]:
        return (
            self.case.case_id,
            self.case.family_id,
            self.case.variant_id,
            self.system_id,
            self.repeat_index,
        )


@dataclass(frozen=True, slots=True)
class OfflineRunResult:
    """Execution summary, intentionally limited to raw-evidence bookkeeping."""

    result_dir: Path
    records_path: Path
    provenance_path: Path | None
    completion_path: Path | None
    planned_records: int
    completed_records: int
    executed_records: int
    skipped_records: int
    error_records: int
    dry_run: bool
    effective_repeats: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OFFLINE_RUNNER_SCHEMA_VERSION,
            "status": "DRY_RUN" if self.dry_run else "RAW_EVIDENCE_COMPLETE",
            "result_dir": str(self.result_dir),
            "records_path": str(self.records_path),
            "provenance_path": str(self.provenance_path) if self.provenance_path else None,
            "completion_path": str(self.completion_path) if self.completion_path else None,
            "planned_records": self.planned_records,
            "completed_records": self.completed_records,
            "executed_records": self.executed_records,
            "skipped_records": self.skipped_records,
            "error_records": self.error_records,
            "dry_run": self.dry_run,
            "effective_repeats": dict(self.effective_repeats),
            "claims_permitted": False,
            "limitations": [
                "Raw offline recommendation observations only; no statistical interpretation was performed.",
                "Repeated stochastic executions estimate stability/runtime variability and are not independent accuracy samples.",
            ],
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _git_identity() -> dict[str, Any]:
    """Record the code revision without exposing remote URLs or credentials."""

    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise OfflineRunnerError("unable to record required Git revision") from exc
    if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
        raise OfflineRunnerError("Git revision is not a full lowercase SHA-1")
    return {"git_revision": revision, "git_worktree_dirty": dirty}


def _finite_json(value: object, *, label: str = "value") -> Any:
    """Normalize a finite JSON tree; reject opaque and secret-like values."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} must not contain non-finite numbers")
        return 0.0 if value == 0 else value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"{label} must use only string mapping keys")
        return {
            key: _finite_json(item, label=f"{label}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_finite_json(item, label=f"{label}[]") for item in value]
    raise TypeError(f"{label} is not finite JSON-compatible")


_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "access_token",
    "auth_token",
    "bearer_token",
    "provider_token",
    "refresh_token",
    "credential",
    "authorization",
    "private_key",
)


def _reject_secrets(value: object, *, label: str = "value") -> None:
    """Fail closed instead of accidentally persisting supplied credentials."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{label} has a non-string key")
            lowered = key.casefold().replace("-", "_")
            if any(part in lowered for part in _SECRET_KEY_PARTS):
                raise ValueError(f"{label} must not include secret-bearing field {key!r}")
            _reject_secrets(nested, label=f"{label}.{key}")
    elif isinstance(value, (tuple, list)):
        for nested in value:
            _reject_secrets(nested, label=label)


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        _finite_json(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _strict_json_loads(text: str, *, label: str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise EvidenceRecordError(f"{label} has duplicate field {key!r}")
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise EvidenceRecordError(f"{label} has non-finite number {value}")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except EvidenceRecordError:
        raise
    except json.JSONDecodeError as exc:
        raise EvidenceRecordError(f"{label} is malformed JSON") from exc


def provenance_fingerprint(provenance: Mapping[str, Any]) -> str:
    """Recompute the immutable execution-plan identity from stored provenance."""

    dynamic = {"provenance_fingerprint", "created_utc"}
    plan = {key: value for key, value in provenance.items() if key not in dynamic}
    return _sha256(plan)


def _entry_seed(base_seed: int, entry: MatrixEntry) -> int:
    digest = hashlib.sha256(
        _canonical_json_bytes(
            {
                "base_seed": base_seed,
                "family_id": entry.case.family_id,
                "variant_id": entry.case.variant_id,
                "case_id": entry.case.case_id,
                "system_id": entry.system_id,
                "repeat_index": entry.repeat_index,
            }
        )
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def _case_input(case: SplitCase) -> OfflineCaseInput:
    return OfflineCaseInput(
        case_id=case.case_id,
        family_id=case.family_id,
        variant_id=case.variant_id,
        language=case.language,
        prompt=case.prompt,
        dataset_size_gb=case.inputs["dataset_size_gb"],
        code_context_hints=tuple(case.inputs["code_context_hints"]),
    )


def _validate_system_ids(
    system_ids: Sequence[str],
    adapters: Mapping[str, OfflineSystemAdapter],
    *,
    enable_p3: bool,
) -> tuple[str, ...]:
    selected = tuple(system_ids)
    if not selected:
        raise ValueError("at least one system ID is required")
    if len(set(selected)) != len(selected):
        raise ValueError("system IDs must not contain duplicates")
    if "B0" in selected:
        raise ValueError(
            "B0 is a manual human-selection baseline and must not run as an offline recommender"
        )
    invalid = sorted(set(selected) - SYSTEM_IDS)
    if invalid:
        raise ValueError("unsupported system IDs: " + ", ".join(invalid))
    if "P3" in selected and not enable_p3:
        raise PermissionError("P3 requires explicit enable_p3=True")
    missing = [system_id for system_id in selected if system_id not in adapters]
    if missing:
        raise ValueError("missing adapters for: " + ", ".join(missing))
    for system_id in selected:
        adapter = adapters[system_id]
        if getattr(adapter, "system_id", None) != system_id:
            raise ValueError(f"adapter system_id mismatch for {system_id}")
        if not callable(getattr(adapter, "recommend", None)):
            raise TypeError(f"adapter {system_id} does not define recommend")
        if not callable(getattr(adapter, "frozen_provenance", None)):
            raise TypeError(f"adapter {system_id} does not define frozen_provenance")
    return selected


def build_execution_matrix(
    split: LoadedSplit,
    *,
    system_ids: Sequence[str],
    adapters: Mapping[str, OfflineSystemAdapter],
    repeats: int,
    seed: int,
    enable_p3: bool = False,
) -> tuple[MatrixEntry, ...]:
    """Build the case matrix while preventing deterministic pseudo-replication."""

    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
        raise ValueError("repeats must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    selected = _validate_system_ids(system_ids, adapters, enable_p3=enable_p3)
    entries: list[MatrixEntry] = []
    keys: set[tuple[str, str, str, str, int]] = set()
    for system_id in selected:
        adapter = adapters[system_id]
        repeat_count = repeats if bool(getattr(adapter, "stochastic", False)) else 1
        for case in split.bundle.cases:
            for repeat_index in range(repeat_count):
                provisional = MatrixEntry(
                    case=case,
                    system_id=system_id,
                    repeat_index=repeat_index,
                    seed=0,
                )
                entry = MatrixEntry(
                    case=case,
                    system_id=system_id,
                    repeat_index=repeat_index,
                    seed=_entry_seed(seed, provisional),
                )
                if entry.key in keys:
                    raise DuplicateRecordError(f"duplicate planned matrix key {entry.key!r}")
                keys.add(entry.key)
                entries.append(entry)
    return tuple(entries)


def _result_layout(result_dir: Path) -> tuple[Path, Path, Path, Path, Path]:
    root = result_dir.resolve()
    raw = root / RAW_DIRECTORY_NAME
    report = root / REPORT_DIRECTORY_NAME
    return (
        root,
        raw,
        report,
        raw / PROVENANCE_FILENAME,
        raw / RECORDS_FILENAME,
    )


def _adapter_provenance(
    system_ids: Iterable[str], adapters: Mapping[str, OfflineSystemAdapter]
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for system_id in system_ids:
        value = adapters[system_id].frozen_provenance()
        _reject_secrets(value, label=f"adapter_provenance.{system_id}")
        values[system_id] = _finite_json(value, label=f"adapter_provenance.{system_id}")
    return values


def _effective_repeats(
    system_ids: Iterable[str],
    adapters: Mapping[str, OfflineSystemAdapter],
    requested_repeats: int,
) -> dict[str, int]:
    return {
        system_id: requested_repeats if bool(getattr(adapters[system_id], "stochastic", False)) else 1
        for system_id in system_ids
    }


def _candidate_catalog_provenance(
    system_ids: Iterable[str],
    systems: Mapping[str, Any],
) -> dict[str, Any]:
    snapshots: list[dict[str, Any]] = []
    for system_id in system_ids:
        provenance = systems[system_id]
        snapshot = provenance.get("candidate_catalog")
        if not isinstance(snapshot, Mapping):
            raise ValueError(
                f"adapter {system_id} frozen provenance lacks candidate_catalog identity"
            )
        selected = _finite_json(snapshot, label=f"candidate_catalog.{system_id}")
        assert isinstance(selected, dict)
        snapshots.append(selected)
    first = snapshots[0]
    if any(snapshot != first for snapshot in snapshots[1:]):
        raise ProvenanceMismatchError(
            "selected systems do not share one frozen candidate catalog/corpus identity"
        )
    return first


def _freeze_identity(split: LoadedSplit) -> dict[str, Any]:
    return {
        "freeze_id": None,
        "frozen_at_utc": split.manifest.freeze_metadata.frozen_at_utc,
        "frozen_by": split.manifest.freeze_metadata.frozen_by,
        "source": "development_split_manifest",
    }


def _run_id(result_dir: Path) -> str:
    name = result_dir.resolve().name
    if name and len(name) <= 128 and all(
        character.isalnum() or character in "._-" for character in name
    ):
        return name
    digest = hashlib.sha256(str(result_dir.resolve()).encode("utf-8")).hexdigest()[:20]
    return f"offline-{digest}"


def _build_provenance(
    split: LoadedSplit,
    *,
    system_ids: tuple[str, ...],
    adapters: Mapping[str, OfflineSystemAdapter],
    matrix: Sequence[MatrixEntry],
    frozen_configuration: Mapping[str, Any],
    seed: int,
    repeats: int,
    include_benchmark_prompts: bool,
    result_dir: Path,
    p3_explicitly_enabled: bool,
    freeze_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    _reject_secrets(frozen_configuration, label="frozen_configuration")
    frozen = _finite_json(frozen_configuration, label="frozen_configuration")
    assert isinstance(frozen, dict)
    systems = _adapter_provenance(system_ids, adapters)
    candidate_catalog = _candidate_catalog_provenance(system_ids, systems)
    selected_freeze = dict(freeze_identity or _freeze_identity(split))
    _reject_secrets(selected_freeze, label="freeze_identity")
    if split.manifest.role.value == "confirmatory":
        freeze_id = selected_freeze.get("freeze_id")
        if not isinstance(freeze_id, str) or not freeze_id.strip():
            raise ValueError("confirmatory runs require an explicit freeze identity")
    plan = {
        "schema_version": OFFLINE_PROVENANCE_SCHEMA_VERSION,
        "protocol_version": "5.0.0",
        "experiment_id": "E6" if "P3" in system_ids else "E1",
        "run_id": _run_id(result_dir),
        "split": {
            "dataset_id": split.manifest.dataset_id,
            "split_id": split.manifest.split_id,
            "role": split.manifest.role.value,
            "bundle_checksum": split.manifest.checksum,
            "dataset_sha256": split.source_file_sha256,
            "case_count": split.manifest.case_count,
            "family_count": split.manifest.family_count,
        },
        "freeze_identity": _finite_json(selected_freeze, label="freeze_identity"),
        **_git_identity(),
        "systems": list(system_ids),
        "system_frozen_provenance": systems,
        "candidate_catalog": candidate_catalog,
        "frozen_configuration": frozen,
        "seed": seed,
        "requested_repeats": repeats,
        "effective_repeats": _effective_repeats(system_ids, adapters, repeats),
        "repeat_policy": {
            "version": REPEAT_POLICY_VERSION,
            "deterministic_systems": [
                system_id
                for system_id in system_ids
                if not bool(getattr(adapters[system_id], "stochastic", False))
            ],
            "stochastic_systems": [
                system_id
                for system_id in system_ids
                if bool(getattr(adapters[system_id], "stochastic", False))
            ],
            "deterministic_effective_repeats": 1,
            "requested_repeats_apply_only_to_stochastic_systems": True,
        },
        "p3_explicitly_enabled": p3_explicitly_enabled,
        "planned_record_count": len(matrix),
        "benchmark_prompt_policy": {
            "stored_in_raw_evidence": include_benchmark_prompts,
            "operational_logging": "prohibited",
        },
        "environment_identity": {
            "python_version": sys.version.split()[0],
            "platform": sys.platform,
            "platform_release": platform.release(),
        },
    }
    fingerprint = provenance_fingerprint(plan)
    return {
        **plan,
        "provenance_fingerprint": fingerprint,
        "created_utc": _utc_now(),
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = _strict_json_loads(
            path.read_text(encoding="utf-8"), label=path.name
        )
    except OSError as exc:
        raise EvidenceRecordError(f"invalid JSON evidence file {path.name}") from exc
    if not isinstance(value, dict):
        raise EvidenceRecordError(f"{path.name} must contain a JSON object")
    return value


def _recover_partial_tail(records_path: Path) -> None:
    """Discard only an unterminated final append after a crash/power loss."""

    if not records_path.exists():
        return
    data = records_path.read_bytes()
    if not data or data.endswith(b"\n"):
        return
    last_newline = data.rfind(b"\n")
    tail = data[last_newline + 1 :]
    try:
        json.loads(tail.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        with records_path.open("r+b") as handle:
            handle.truncate(last_newline + 1)
            handle.flush()
            os.fsync(handle.fileno())
        return
    # A complete JSON value without a newline was not durably committed by this
    # runner, so retaining it would make resume depend on a partial write path.
    with records_path.open("r+b") as handle:
        handle.truncate(last_newline + 1)
        handle.flush()
        os.fsync(handle.fileno())


def _record_key(record: Mapping[str, Any]) -> tuple[str, str, str, str, int]:
    try:
        return (
            str(record["case_id"]),
            str(record["family_id"]),
            str(record["variant_id"]),
            str(record["system_id"]),
            int(record["repeat_index"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceRecordError("raw record has an invalid matrix key") from exc


def validate_raw_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Focused validation for runner-owned raw records used during resume."""

    if not isinstance(record, Mapping):
        raise EvidenceRecordError("raw record must be a JSON object")
    required = {
        "schema_version",
        "provenance_fingerprint",
        "run_id",
        "record_id",
        "timestamp_utc",
        "case_id",
        "family_id",
        "variant_id",
        "system_id",
        "repeat_index",
        "seed",
        "input_identity",
        "benchmark_prompt",
        "evaluation_gold",
        "adapter_provenance",
        "backend_provenance",
        "status",
        "predicted_candidate_id",
        "predicted_profile_id",
        "predicted_image_id",
        "recommendation_reasons",
        "recommendation_codes",
        "structured_intent",
        "sparse_ranks",
        "dense_ranks",
        "hybrid_ranks_scores",
        "candidate_top_k",
        "constraint_evaluations",
        "feasible_top_k",
        "final_ranking",
        "constraint_summary",
        "latency_components",
        "fallback",
        "errors",
        "metric_inputs",
    }
    missing = sorted(required - set(record))
    extra = sorted(set(record) - required)
    if missing:
        raise EvidenceRecordError("raw record missing fields: " + ", ".join(missing))
    if extra:
        raise EvidenceRecordError("raw record unexpected fields: " + ", ".join(extra))
    if record["schema_version"] != OFFLINE_RAW_RECORD_SCHEMA_VERSION:
        raise EvidenceRecordError("raw record schema_version is unsupported")
    for field in ("provenance_fingerprint", "run_id", "record_id", "case_id", "family_id", "variant_id", "system_id", "timestamp_utc"):
        if not isinstance(record[field], str) or not record[field]:
            raise EvidenceRecordError(f"raw record {field} must be non-blank")
    if record["system_id"] not in SYSTEM_IDS:
        raise EvidenceRecordError("raw record system_id is unsupported")
    for field in ("repeat_index", "seed"):
        if isinstance(record[field], bool) or not isinstance(record[field], int) or record[field] < 0:
            raise EvidenceRecordError(f"raw record {field} must be a non-negative integer")
    if record["status"] not in {"completed", "error"}:
        raise EvidenceRecordError("raw record status is unsupported")
    for field in (
        "input_identity",
        "evaluation_gold",
        "adapter_provenance",
        "latency_components",
        "fallback",
        "metric_inputs",
    ):
        if not isinstance(record[field], Mapping):
            raise EvidenceRecordError(f"raw record {field} must be an object")
    for field in (
        "predicted_candidate_id",
        "predicted_profile_id",
        "predicted_image_id",
        "benchmark_prompt",
    ):
        if record[field] is not None and not isinstance(record[field], str):
            raise EvidenceRecordError(f"raw record {field} must be a string or null")
    for field in ("structured_intent", "constraint_summary", "backend_provenance"):
        if record[field] is not None and not isinstance(record[field], Mapping):
            raise EvidenceRecordError(f"raw record {field} must be an object or null")
    if record["errors"] is not None and not isinstance(record["errors"], Mapping):
        raise EvidenceRecordError("raw record errors must be an object or null")
    for field in (
        "recommendation_reasons",
        "recommendation_codes",
        "sparse_ranks",
        "dense_ranks",
        "hybrid_ranks_scores",
        "candidate_top_k",
        "constraint_evaluations",
        "feasible_top_k",
        "final_ranking",
    ):
        if not isinstance(record[field], list):
            raise EvidenceRecordError(f"raw record {field} must be a list")
    for field in ("recommendation_reasons", "recommendation_codes"):
        if not all(isinstance(item, str) for item in record[field]):
            raise EvidenceRecordError(f"raw record {field} entries must be strings")
    fallback = record["fallback"]
    if set(fallback) != {"used", "category"}:
        raise EvidenceRecordError("raw record fallback has invalid fields")
    if not isinstance(fallback["used"], bool):
        raise EvidenceRecordError("raw record fallback.used must be boolean")
    if fallback["category"] is not None and not isinstance(fallback["category"], str):
        raise EvidenceRecordError("raw record fallback.category must be a string or null")
    if record["status"] == "completed" and record["errors"] is not None:
        raise EvidenceRecordError("completed raw record must not contain errors")
    if record["status"] == "error":
        if not record["errors"]:
            raise EvidenceRecordError("error raw record must contain an error object")
        if any(
            record[field] is not None
            for field in (
                "predicted_candidate_id",
                "predicted_profile_id",
                "predicted_image_id",
            )
        ):
            raise EvidenceRecordError("error raw record must not contain a prediction")
    _reject_secrets(record, label="raw_record")
    try:
        normalized = _finite_json(record, label="raw_record")
    except (TypeError, ValueError) as exc:
        raise EvidenceRecordError(str(exc)) from exc
    assert isinstance(normalized, dict)
    return normalized


def _read_records(
    records_path: Path,
    *,
    expected_fingerprint: str,
    expected_keys: set[tuple[str, str, str, str, int]],
) -> dict[tuple[str, str, str, str, int], dict[str, Any]]:
    if not records_path.exists():
        return {}
    _recover_partial_tail(records_path)
    completed: dict[tuple[str, str, str, str, int], dict[str, Any]] = {}
    with records_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise EvidenceRecordError(f"blank raw record at line {line_number}")
            try:
                record = validate_raw_record(
                    _strict_json_loads(line, label=f"raw record line {line_number}")
                )
            except EvidenceRecordError as exc:
                raise EvidenceRecordError(
                    f"invalid raw record at line {line_number}: {exc}"
                ) from exc
            if record["provenance_fingerprint"] != expected_fingerprint:
                raise ProvenanceMismatchError(
                    "raw record provenance fingerprint does not match this run"
                )
            key = _record_key(record)
            if key not in expected_keys:
                raise EvidenceRecordError(f"unexpected raw record key {key!r}")
            if key in completed:
                raise DuplicateRecordError(f"duplicate raw record key {key!r}")
            completed[key] = record
    return completed


def _append_record(records_path: Path, record: Mapping[str, Any]) -> None:
    serialized = json.dumps(
        validate_raw_record(record),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    descriptor = os.open(records_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        written = 0
        while written < len(serialized):
            count = os.write(descriptor, serialized[written:])
            if count <= 0:
                raise OSError("failed to append raw record")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_error(exc: BaseException) -> dict[str, str]:
    """Persist a category only; exception messages can echo benchmark input."""

    return {"category": type(exc).__name__, "code": "adapter_execution_error"}


def _acceptable_image_ids(case: SplitCase) -> list[str]:
    images: set[str] = set()
    for candidate_id in case.gold["acceptable_candidate_ids"]:
        for profile in ("small", "medium", "large"):
            prefix = profile + "-"
            if candidate_id.startswith(prefix):
                images.add(candidate_id.removeprefix(prefix))
                break
    return sorted(images)


def _metric_inputs(case: SplitCase, result: OfflineAdapterResult) -> dict[str, Any]:
    """Raw ingredients for registered metrics, never an aggregate claim."""

    selected_evaluation = next(
        (
            item
            for item in result.constraint_evaluations
            if item.get("candidate_id") == result.predicted_candidate_id
        ),
        None,
    )
    hard_constraints_satisfied = (
        bool(selected_evaluation.get("feasible"))
        if selected_evaluation is not None
        else None
    )
    fallback = dict(result.fallback or {})
    constraint_summary = dict(result.constraint_summary or {})
    return {
        "request_feasible": case.gold["request_feasible"],
        "preferred_candidate_id": case.gold["preferred_candidate_id"],
        "acceptable_candidate_ids": list(case.gold["acceptable_candidate_ids"]),
        "acceptable_profile_ids": list(case.gold["allowed_profiles"]),
        "acceptable_image_ids": _acceptable_image_ids(case),
        "required_image_capabilities": list(case.gold["required_image_capabilities"]),
        "predicted_candidate_id": result.predicted_candidate_id,
        "predicted_profile_id": result.predicted_profile_id,
        "predicted_image_id": result.predicted_image_id,
        "candidate_top_k_ids": [item.get("candidate_id") for item in result.candidate_top_k],
        "final_ranking_candidate_ids": [item.get("candidate_id") for item in result.final_ranking],
        "hard_constraints_satisfied": hard_constraints_satisfied,
        "constraint_violation_codes": (
            list(selected_evaluation.get("violated_hard_constraints", []))
            if selected_evaluation is not None
            else []
        ),
        "infeasible_request_signal": bool(constraint_summary.get("no_feasible_candidate", False)),
        "unsupported_request_signal": bool(constraint_summary.get("unsupported_constraints", [])),
        "fallback_used": bool(fallback.get("used", False)),
        "fallback_category": fallback.get("category"),
    }


def _input_identity(case: SplitCase, split: LoadedSplit) -> dict[str, Any]:
    identity_source = {
        "case_id": case.case_id,
        "family_id": case.family_id,
        "variant_id": case.variant_id,
        "language": case.language,
        "prompt": case.prompt,
        "inputs": dict(case.inputs),
    }
    return {
        "dataset_id": split.manifest.dataset_id,
        "split_id": split.manifest.split_id,
        "split_bundle_checksum": split.manifest.checksum,
        "dataset_sha256": split.source_file_sha256,
        "case_id": case.case_id,
        "case_sha256": _sha256(identity_source),
        "prompt_sha256": hashlib.sha256(case.prompt.encode("utf-8")).hexdigest(),
    }


def _record_for_entry(
    entry: MatrixEntry,
    *,
    split: LoadedSplit,
    result: OfflineAdapterResult | None,
    adapter_provenance: Mapping[str, Any],
    provenance_fingerprint: str,
    run_id: str,
    include_benchmark_prompts: bool,
    error: BaseException | None = None,
    elapsed_seconds: float | None = None,
) -> dict[str, Any]:
    if result is None:
        result = OfflineAdapterResult(
            predicted_candidate_id=None,
            predicted_profile_id=None,
            predicted_image_id=None,
            latency_components={
                "total_elapsed_seconds": elapsed_seconds,
                "inference_latency_seconds": None,
            },
            fallback={"used": False, "category": None},
            errors=_safe_error(error) if error is not None else {"category": "Unknown", "code": "adapter_execution_error"},
        )
    result_data = result.to_dict()
    _reject_secrets(result_data, label="adapter_result")
    status = "error" if error is not None else "completed"
    core = {
        "schema_version": OFFLINE_RAW_RECORD_SCHEMA_VERSION,
        "provenance_fingerprint": provenance_fingerprint,
        "run_id": run_id,
        "case_id": entry.case.case_id,
        "family_id": entry.case.family_id,
        "variant_id": entry.case.variant_id,
        "system_id": entry.system_id,
        "repeat_index": entry.repeat_index,
        "seed": entry.seed,
        "input_identity": _input_identity(entry.case, split),
        "evaluation_gold": {
            "request_feasible": entry.case.gold["request_feasible"],
            "preferred_candidate_id": entry.case.gold["preferred_candidate_id"],
            "acceptable_candidate_ids": list(entry.case.gold["acceptable_candidate_ids"]),
            "required_image_capabilities": list(entry.case.gold["required_image_capabilities"]),
            "allowed_profiles": list(entry.case.gold["allowed_profiles"]),
            "gpu_allowed": entry.case.gold["gpu_allowed"],
        },
        "adapter_provenance": dict(adapter_provenance),
        "status": status,
        **result_data,
        "metric_inputs": _metric_inputs(entry.case, result),
    }
    core["benchmark_prompt"] = entry.case.prompt if include_benchmark_prompts else None
    core["timestamp_utc"] = _utc_now()
    core["record_id"] = _sha256(
        {
            "provenance_fingerprint": provenance_fingerprint,
            "key": list(entry.key),
            "seed": entry.seed,
            "case_sha256": core["input_identity"]["case_sha256"],
        }
    )
    return validate_raw_record(core)


def _acquire_lock(lock_path: Path) -> int:
    try:
        return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise OfflineRunnerError(
            "another runner owns this result directory (remove only a known-stale lock after inspection)"
        ) from exc


def _release_lock(lock_path: Path, descriptor: int) -> None:
    try:
        os.close(descriptor)
    finally:
        lock_path.unlink(missing_ok=True)


def _completion_payload(
    *,
    provenance: Mapping[str, Any],
    records_path: Path,
    completed: Mapping[tuple[str, str, str, str, int], Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": OFFLINE_COMPLETION_SCHEMA_VERSION,
        "provenance_fingerprint": provenance["provenance_fingerprint"],
        "completed_utc": _utc_now(),
        "records": len(completed),
        "error_records": sum(item["status"] == "error" for item in completed.values()),
        "recommendations_jsonl_sha256": file_sha256(records_path),
        "claims_permitted": False,
        "status": "RAW_EVIDENCE_COMPLETE",
    }


def _validate_existing_completion(
    completion_path: Path,
    *,
    provenance_fingerprint: str,
    records_path: Path,
    completed: Mapping[tuple[str, str, str, str, int], Mapping[str, Any]],
) -> None:
    payload = _read_json(completion_path)
    required = {
        "schema_version",
        "provenance_fingerprint",
        "completed_utc",
        "records",
        "error_records",
        "recommendations_jsonl_sha256",
        "claims_permitted",
        "status",
    }
    if set(payload) != required:
        raise EvidenceRecordError("completion fields do not match the frozen schema")
    if payload.get("schema_version") != OFFLINE_COMPLETION_SCHEMA_VERSION:
        raise EvidenceRecordError("completion schema_version is unsupported")
    if payload.get("provenance_fingerprint") != provenance_fingerprint:
        raise ProvenanceMismatchError("completion provenance fingerprint does not match")
    if payload.get("records") != len(completed):
        raise EvidenceRecordError("completion record count does not match the planned matrix")
    if payload.get("error_records") != sum(
        record["status"] == "error" for record in completed.values()
    ):
        raise EvidenceRecordError("completion error count does not match raw records")
    if payload.get("recommendations_jsonl_sha256") != file_sha256(records_path):
        raise EvidenceRecordError("completion checksum does not match recommendations.jsonl")
    if payload.get("status") != "RAW_EVIDENCE_COMPLETE":
        raise EvidenceRecordError("completion status is unsupported")
    if payload.get("claims_permitted") is not False:
        raise EvidenceRecordError("completion must not permit statistical claims")


def run_offline_recommendations(
    split: LoadedSplit,
    *,
    result_dir: Path,
    system_ids: Sequence[str] = ("P1", "P2"),
    adapters: Mapping[str, OfflineSystemAdapter] | None = None,
    frozen_configuration: Mapping[str, Any] | None = None,
    repeats: int = 1,
    seed: int = 20260824,
    enable_p3: bool = False,
    resume: bool = False,
    dry_run: bool = False,
    include_benchmark_prompts: bool = False,
    freeze_identity: Mapping[str, Any] | None = None,
) -> OfflineRunResult:
    """Run or safely resume a raw Protocol-v5 recommendation evidence matrix.

    ``split`` must already be loaded by the v5 split-isolation layer.  This
    keeps confirmatory loading decisions outside the runner and ensures sealed
    labels are not part of any adapter call.
    """

    if not isinstance(split, LoadedSplit):
        raise TypeError("split must be a LoadedSplit from the Protocol-v5 loader")
    selected_adapters = dict(adapters) if adapters is not None else default_adapters(enable_p3=enable_p3)
    selected_systems = _validate_system_ids(
        system_ids, selected_adapters, enable_p3=enable_p3
    )
    matrix = build_execution_matrix(
        split,
        system_ids=selected_systems,
        adapters=selected_adapters,
        repeats=repeats,
        seed=seed,
        enable_p3=enable_p3,
    )
    frozen = dict(frozen_configuration or {})
    provenance = _build_provenance(
        split,
        system_ids=selected_systems,
        adapters=selected_adapters,
        matrix=matrix,
        frozen_configuration=frozen,
        seed=seed,
        repeats=repeats,
        include_benchmark_prompts=include_benchmark_prompts,
        result_dir=result_dir,
        p3_explicitly_enabled=enable_p3,
        freeze_identity=freeze_identity,
    )
    root, raw_dir, report_dir, provenance_path, records_path = _result_layout(result_dir)
    completion_path = report_dir / COMPLETION_FILENAME
    effective = _effective_repeats(selected_systems, selected_adapters, repeats)
    if dry_run:
        return OfflineRunResult(
            result_dir=root,
            records_path=records_path,
            provenance_path=None,
            completion_path=None,
            planned_records=len(matrix),
            completed_records=0,
            executed_records=0,
            skipped_records=0,
            error_records=0,
            dry_run=True,
            effective_repeats=effective,
        )

    if root.exists():
        if not resume:
            # A caller may reserve a target with mkdtemp or a workflow manager.
            # An empty directory contains no evidence, so initializing it does
            # not overwrite or mix any immutable result package.
            if not root.is_dir() or any(root.iterdir()):
                raise FileExistsError(
                    f"refusing to reuse result directory {root}; use resume=True only for the same frozen run"
                )
            raw_dir.mkdir()
            report_dir.mkdir()
            write_json_exclusive(provenance_path, provenance)
        else:
            if not raw_dir.is_dir() or not report_dir.is_dir() or not provenance_path.is_file():
                raise EvidenceRecordError("resume directory does not contain a complete runner layout")
            existing_provenance = _read_json(provenance_path)
            if existing_provenance.get("provenance_fingerprint") != provenance["provenance_fingerprint"]:
                raise ProvenanceMismatchError(
                    "result directory provenance does not match dataset, frozen configuration, systems, seed, or repeat plan"
                )
    else:
        root.mkdir(parents=True)
        raw_dir.mkdir()
        report_dir.mkdir()
        write_json_exclusive(provenance_path, provenance)

    lock_path = root / LOCK_FILENAME
    lock_descriptor = _acquire_lock(lock_path)
    try:
        expected_keys = {entry.key for entry in matrix}
        completed = _read_records(
            records_path,
            expected_fingerprint=str(provenance["provenance_fingerprint"]),
            expected_keys=expected_keys,
        )
        if completion_path.exists():
            _validate_existing_completion(
                completion_path,
                provenance_fingerprint=str(provenance["provenance_fingerprint"]),
                records_path=records_path,
                completed=completed,
            )
            return OfflineRunResult(
                result_dir=root,
                records_path=records_path,
                provenance_path=provenance_path,
                completion_path=completion_path,
                planned_records=len(matrix),
                completed_records=len(completed),
                executed_records=0,
                skipped_records=len(completed),
                error_records=sum(record["status"] == "error" for record in completed.values()),
                dry_run=False,
                effective_repeats=effective,
            )

        executed = 0
        skipped = 0
        for entry in matrix:
            if entry.key in completed:
                skipped += 1
                continue
            adapter = selected_adapters[entry.system_id]
            started = time.monotonic()
            try:
                observed = adapter.recommend(_case_input(entry.case), seed=entry.seed)
                if not isinstance(observed, OfflineAdapterResult):
                    raise TypeError("adapter recommend must return OfflineAdapterResult")
                latency = dict(observed.latency_components or {})
                if latency.get("total_elapsed_seconds") is None:
                    latency["total_elapsed_seconds"] = max(0.0, time.monotonic() - started)
                    observed = OfflineAdapterResult(
                        **{**observed.to_dict(), "latency_components": latency}
                    )
                record = _record_for_entry(
                    entry,
                    split=split,
                    result=observed,
                    adapter_provenance=provenance["system_frozen_provenance"][entry.system_id],
                    provenance_fingerprint=str(provenance["provenance_fingerprint"]),
                    run_id=str(provenance["run_id"]),
                    include_benchmark_prompts=include_benchmark_prompts,
                )
            except Exception as exc:
                record = _record_for_entry(
                    entry,
                    split=split,
                    result=None,
                    adapter_provenance=provenance["system_frozen_provenance"][entry.system_id],
                    provenance_fingerprint=str(provenance["provenance_fingerprint"]),
                    run_id=str(provenance["run_id"]),
                    include_benchmark_prompts=include_benchmark_prompts,
                    error=exc,
                    elapsed_seconds=max(0.0, time.monotonic() - started),
                )
            _append_record(records_path, record)
            completed[entry.key] = record
            executed += 1

        if set(completed) != expected_keys:
            missing = sorted(expected_keys - set(completed))
            raise EvidenceRecordError(f"raw evidence is incomplete; missing keys: {missing!r}")
        completion = _completion_payload(
            provenance=provenance,
            records_path=records_path,
            completed=completed,
        )
        write_json_exclusive(completion_path, completion)
        return OfflineRunResult(
            result_dir=root,
            records_path=records_path,
            provenance_path=provenance_path,
            completion_path=completion_path,
            planned_records=len(matrix),
            completed_records=len(completed),
            executed_records=executed,
            skipped_records=skipped,
            error_records=sum(record["status"] == "error" for record in completed.values()),
            dry_run=False,
            effective_repeats=effective,
        )
    finally:
        _release_lock(lock_path, lock_descriptor)


def _parse_systems(value: str) -> tuple[str, ...]:
    systems = tuple(part.strip() for part in value.split(",") if part.strip())
    if not systems:
        raise ValueError("--systems requires at least one system ID")
    return systems


def _load_frozen_configuration(path: Path) -> Mapping[str, Any]:
    try:
        value = _strict_json_loads(
            path.read_text(encoding="utf-8"), label="--frozen-configuration"
        )
    except (OSError, EvidenceRecordError) as exc:
        raise ValueError("--frozen-configuration must be a readable JSON object") from exc
    if not isinstance(value, Mapping):
        raise ValueError("--frozen-configuration must contain a JSON object")
    return dict(value)


def _cli_split(args: argparse.Namespace) -> tuple[LoadedSplit, Mapping[str, Any] | None]:
    if args.split == "development":
        if args.dataset is not None or args.freeze is not None:
            raise ValueError("development mode loads only the repository's frozen development split")
        from evaluation_v5.split_dataset import load_development_split

        return (
            load_development_split(expected_split_id=args.split_id or "v5-development"),
            None,
        )

    from evaluation_v5.isolation import load_confirmatory_split, resolve_confirmatory_sources

    dataset, freeze = resolve_confirmatory_sources(
        dataset_path=args.dataset,
        freeze_path=args.freeze,
    )
    loaded = load_confirmatory_split(
        dataset,
        freeze,
        expected_split_id=args.split_id or "v5-confirmatory",
    )
    return (
        loaded.split,
        {
            "freeze_id": loaded.freeze_manifest["freeze_id"],
            "freeze_manifest_sha256": file_sha256(freeze),
            "frozen_at_utc": loaded.freeze_manifest["created_at_utc"],
            "frozen_by": "authoritative_protocol_v5_freeze",
            "source": "confirmatory_freeze_manifest",
        },
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("development", "confirmatory"), required=True)
    parser.add_argument("--split-id", help="Expected frozen split identifier.")
    parser.add_argument("--dataset", type=Path, help="Sealed confirmatory split path.")
    parser.add_argument("--freeze", type=Path, help="Sealed confirmatory freeze artifact.")
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--systems", default="P1,P2", help="Comma-separated P1/P2 IDs; P3 also needs --enable-p3.")
    parser.add_argument("--enable-p3", action="store_true", help="Explicitly permit P3 evaluation.")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260824)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--frozen-configuration",
        type=Path,
        required=True,
        help="Versioned JSON snapshot of the fixed evaluator configuration; secrets are rejected.",
    )
    parser.add_argument(
        "--include-benchmark-prompts",
        action="store_true",
        help="Store benchmark prompt text only in raw evaluation evidence, never operational logs.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        split, freeze_identity = _cli_split(args)
        result = run_offline_recommendations(
            split,
            result_dir=args.result_dir,
            system_ids=_parse_systems(args.systems),
            frozen_configuration=_load_frozen_configuration(args.frozen_configuration),
            repeats=args.repeats,
            seed=args.seed,
            enable_p3=args.enable_p3,
            resume=args.resume,
            dry_run=args.dry_run,
            include_benchmark_prompts=args.include_benchmark_prompts,
            freeze_identity=freeze_identity,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OfflineRunnerError, OSError, PermissionError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": OFFLINE_RUNNER_SCHEMA_VERSION,
                    "status": "ERROR",
                    "claims_permitted": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


__all__ = [
    "COMPLETION_FILENAME",
    "DuplicateRecordError",
    "EvidenceRecordError",
    "MatrixEntry",
    "OFFLINE_RAW_RECORD_SCHEMA_VERSION",
    "OFFLINE_RUNNER_SCHEMA_VERSION",
    "OfflineRunResult",
    "OfflineRunnerError",
    "ProvenanceMismatchError",
    "RECORDS_FILENAME",
    "build_execution_matrix",
    "run_offline_recommendations",
    "provenance_fingerprint",
    "validate_raw_record",
]


if __name__ == "__main__":
    raise SystemExit(main())
