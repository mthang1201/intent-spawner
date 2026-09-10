"""Authenticated Protocol-v5 P3 development-gate decisions.

The gate is deliberately downstream of complete development-only recommendation,
component, and statistical evidence.  A serialized decision is never authority:
verification reopens every recorded source, reruns the authoritative validators,
recomputes component scoring, and evaluates the frozen predicate again.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping as MappingABC, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from evaluation_v4.dataset import file_sha256

from .analysis.component_scoring import (
    AnalysisResult,
    load_component_gold,
    load_validated_evidence,
    score_component_records,
    validate_analysis_package,
)
from .analysis.statistical_analysis import validate_statistical_package
from .offline.runner import (
    COMPLETION_FILENAME,
    PROVENANCE_FILENAME,
    RAW_DIRECTORY_NAME,
    RECORDS_FILENAME,
    REPORT_DIRECTORY_NAME,
)
from .offline.recommenders import default_adapters
from .offline.validate_evidence import validate_offline_evidence
from .split_dataset import (
    DEFAULT_DEVELOPMENT_SPLIT_ID,
    LoadedSplit,
    SplitRole,
    load_development_split,
)


ROOT = Path(__file__).resolve().parents[1]
P3_DEVELOPMENT_DECISION_SCHEMA_VERSION = (
    "protocol-v5-p3-development-decision-v1.0.0"
)
P3_GATE_COMPUTATION_VERSION = "protocol-v5-p3-gate-computation-v1.0.0"
P3_GATE_PREDICATE_VERSION = "protocol-v5-p3-headroom-predicate-v1.0.0"
P3_GATE_SNAPSHOT_VERSION = "protocol-v5-p3-gate-snapshot-v2.0.0"
P3_GATE_MINIMUM_COUNT = 3
P3_GATE_MINIMUM_FRACTION = 0.05
P3_DEVELOPMENT_DECISION_SCHEMA_PATH = (
    ROOT
    / "benchmarks_v5"
    / "protocol-v5-p3-development-decision-v1.schema.json"
)


class P3GateValidationError(RuntimeError):
    """P3 retention was requested without authentic development evidence."""


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
        raise P3GateValidationError(
            "P3 gate data must contain finite JSON values"
        ) from exc


class P3DevelopmentDecision(MappingABC[str, Any]):
    """Strictly parsed decision document without source authority."""

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


_VERIFIED_GATE_CONSTRUCTION_KEY = object()


class VerifiedP3DevelopmentDecision(MappingABC[str, Any]):
    """Immutable capability bound to a revalidated decision and its sources."""

    __slots__ = ("_decision", "_artifact_path", "_artifact_sha256")

    def __init__(
        self,
        *,
        decision: P3DevelopmentDecision,
        artifact_path: Path,
        artifact_sha256: str,
        _construction_key: object,
    ) -> None:
        if _construction_key is not _VERIFIED_GATE_CONSTRUCTION_KEY:
            raise TypeError(
                "VerifiedP3DevelopmentDecision is produced only by "
                "verify_p3_development_decision()"
            )
        object.__setattr__(self, "_decision", decision)
        object.__setattr__(self, "_artifact_path", artifact_path)
        object.__setattr__(self, "_artifact_sha256", artifact_sha256)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("VerifiedP3DevelopmentDecision is immutable")

    @property
    def artifact_path(self) -> Path:
        return self._artifact_path

    @property
    def artifact_sha256(self) -> str:
        return self._artifact_sha256

    @property
    def decision(self) -> str:
        return str(self._decision["decision"])

    def to_dict(self) -> dict[str, Any]:
        return self._decision.to_dict()

    def freeze_snapshot(self) -> dict[str, Any]:
        source = self._decision["source"]
        return {
            "snapshot_version": P3_GATE_SNAPSHOT_VERSION,
            "status": self.decision,
            "p3_active": self.decision == "retained",
            "verification_status": "VERIFIED",
            "decision_schema_version": self._decision["schema_version"],
            "decision_artifact_path": _portable_path(self.artifact_path),
            "decision_artifact_sha256": self.artifact_sha256,
            "development_split": source["development_split"],
            "raw_evidence": source["raw_evidence"],
            "component_evidence": source["component_evidence"],
            "statistical_evidence": source["statistical_evidence"],
            "predicate_version": self._decision["predicate"]["version"],
            "computation_version": self._decision["computation_version"],
        }

    def __getitem__(self, key: str) -> Any:
        return self._decision[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._decision)

    def __len__(self) -> int:
        return len(self._decision)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT.resolve()))
    except ValueError:
        return str(resolved)


def _resolve_path(value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise P3GateValidationError(f"{label} must be a non-blank path")
    candidate = Path(value)
    return candidate if candidate.is_absolute() else ROOT / candidate


def _strict_json_document(raw: bytes, *, label: str) -> dict[str, Any]:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        selected: dict[str, Any] = {}
        for key, value in pairs:
            if key in selected:
                raise P3GateValidationError(
                    f"{label} contains duplicate JSON keys"
                )
            selected[key] = value
        return selected

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate,
            parse_constant=lambda selected: (_ for _ in ()).throw(
                P3GateValidationError(
                    f"{label} contains non-finite JSON value {selected}"
                )
            ),
        )
    except P3GateValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise P3GateValidationError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise P3GateValidationError(f"{label} must contain a JSON object")
    return dict(value)


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        return _strict_json_document(path.read_bytes(), label=label)
    except OSError as exc:
        raise P3GateValidationError(f"{label} could not be read") from exc


def _read_jsonl(path: Path, *, label: str) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise P3GateValidationError(f"{label} could not be read") from exc
    if not raw or not raw.endswith(b"\n"):
        raise P3GateValidationError(
            f"{label} must be a non-empty newline-terminated JSONL file"
        )
    return [
        _strict_json_document(line, label=f"{label} line {index}")
        for index, line in enumerate(raw.splitlines(), start=1)
    ]


def _schema() -> dict[str, Any]:
    try:
        schema = _strict_json_document(
            P3_DEVELOPMENT_DECISION_SCHEMA_PATH.read_bytes(),
            label="P3 development decision schema",
        )
        Draft202012Validator.check_schema(schema)
    except (OSError, SchemaError) as exc:
        raise P3GateValidationError(
            "the canonical P3 development decision schema is unavailable"
        ) from exc
    return schema


def parse_p3_development_decision(document: object) -> P3DevelopmentDecision:
    if not isinstance(document, Mapping):
        raise P3GateValidationError("P3 development decision must be an object")
    payload = dict(document)
    errors = sorted(
        Draft202012Validator(_schema()).iter_errors(payload),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "root"
        raise P3GateValidationError(
            f"P3 development decision schema violation at {location}: "
            f"{first.message}"
        )
    created = payload["created_at_utc"]
    try:
        parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError as exc:
        raise P3GateValidationError(
            "P3 development decision created_at_utc is invalid"
        ) from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise P3GateValidationError(
            "P3 development decision created_at_utc must use UTC"
        )
    return P3DevelopmentDecision(payload)


def _split_identity(split: LoadedSplit) -> dict[str, Any]:
    return {
        "dataset_id": split.manifest.dataset_id,
        "split_id": split.manifest.split_id,
        "role": split.manifest.role.value,
        "bundle_checksum": split.manifest.checksum,
        "dataset_sha256": split.source_file_sha256,
        "case_count": split.manifest.case_count,
        "family_count": split.manifest.family_count,
    }


def _analysis_identity(path: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, Mapping) or not outputs:
        raise P3GateValidationError(
            "P3 gate requires a complete derived analysis package"
        )
    identities: dict[str, Any] = {}
    for name, raw_identity in sorted(outputs.items()):
        if not isinstance(name, str) or not isinstance(raw_identity, Mapping):
            raise P3GateValidationError(
                "P3 gate analysis output registry is malformed"
            )
        relative = raw_identity.get("path")
        if not isinstance(relative, str) or not relative.strip():
            raise P3GateValidationError(
                "P3 gate analysis output lacks a path"
            )
        artifact = path / relative
        identities[name] = {
            "path": relative,
            "sha256": file_sha256(artifact),
        }
    return {
        "path": _portable_path(path),
        "manifest_sha256": file_sha256(path / "analysis-manifest.json"),
        "outputs": identities,
    }


def _same_split(actual: LoadedSplit, expected: LoadedSplit) -> bool:
    return (
        actual.bundle == expected.bundle
        and actual.source_file_sha256 == expected.source_file_sha256
        and _split_identity(actual) == _split_identity(expected)
    )


def _predicate() -> dict[str, Any]:
    return {
        "version": P3_GATE_PREDICATE_VERSION,
        "unit": "workload_family",
        "minimum_absolute_ranking_errors": P3_GATE_MINIMUM_COUNT,
        "minimum_ranking_error_fraction": P3_GATE_MINIMUM_FRACTION,
        "criterion": (
            "ranking errors >= max(minimum_absolute_ranking_errors, "
            "ceil(minimum_ranking_error_fraction * eligible families)) and "
            "ranking-error fraction of eligible families >= "
            "minimum_ranking_error_fraction"
        ),
    }


def _evaluated_inputs(result: AnalysisResult) -> dict[str, Any]:
    headroom = result.p3_headroom
    if headroom.get("status") != "EVALUATED":
        raise P3GateValidationError(
            "P3 gate requires complete evaluated development headroom"
        )
    eligible = headroom.get("eligible_family_count")
    ranking = headroom.get("ranking_error_family_count")
    rate = headroom.get("ranking_error_fraction_of_eligible_families")
    required = headroom.get("gate_configuration", {}).get(
        "required_ranking_error_count"
    )
    if (
        isinstance(eligible, bool)
        or not isinstance(eligible, int)
        or eligible < 1
        or isinstance(ranking, bool)
        or not isinstance(ranking, int)
        or ranking < 0
        or isinstance(required, bool)
        or not isinstance(required, int)
        or required < 1
        or isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not math.isfinite(float(rate))
        or not 0 <= float(rate) <= 1
    ):
        raise P3GateValidationError(
            "P3 gate headroom contains invalid evaluated inputs"
        )
    recomputed_required = max(
        P3_GATE_MINIMUM_COUNT,
        math.ceil(P3_GATE_MINIMUM_FRACTION * eligible),
    )
    criterion_met = (
        ranking >= recomputed_required
        and float(rate) >= P3_GATE_MINIMUM_FRACTION
    )
    if (
        required != recomputed_required
        or headroom.get("criterion_met") is not criterion_met
        or headroom.get("advisory_decision")
        != ("retained" if criterion_met else "not_retained")
    ):
        raise P3GateValidationError(
            "derived P3 headroom disagrees with the predefined predicate"
        )
    return {
        "eligible_family_count": eligible,
        "ranking_error_family_count": ranking,
        "ranking_error_fraction_of_eligible_families": float(rate),
        "required_ranking_error_count": recomputed_required,
        "criterion_met": criterion_met,
    }


def _rationale(evaluated: Mapping[str, Any]) -> str:
    decision = "RETAINED" if evaluated["criterion_met"] else "NOT_RETAINED"
    return (
        f"P3_{decision}: observed {evaluated['ranking_error_family_count']} "
        f"ranking-error workload families out of "
        f"{evaluated['eligible_family_count']} eligible families "
        f"(fraction={evaluated['ranking_error_fraction_of_eligible_families']!r}); "
        f"the predefined gate required at least "
        f"{evaluated['required_ranking_error_count']} families and fraction "
        f"{P3_GATE_MINIMUM_FRACTION!r}."
    )


def _require_source_join(
    *,
    component_manifest: Mapping[str, Any],
    statistical_manifest: Mapping[str, Any],
    provenance: Mapping[str, Any],
    split: LoadedSplit,
    evidence_dir: Path,
) -> None:
    recommendations = file_sha256(
        evidence_dir / RAW_DIRECTORY_NAME / RECORDS_FILENAME
    )
    provenance_sha256 = file_sha256(
        evidence_dir / RAW_DIRECTORY_NAME / PROVENANCE_FILENAME
    )
    completion_sha256 = file_sha256(
        evidence_dir / REPORT_DIRECTORY_NAME / COMPLETION_FILENAME
    )
    component_source = component_manifest.get("source")
    statistical_source = statistical_manifest.get("source")
    if not isinstance(component_source, Mapping) or not isinstance(
        statistical_source, Mapping
    ):
        raise P3GateValidationError(
            "P3 gate derived evidence lacks source provenance"
        )
    common = (
        component_source.get("offline_run_id") == provenance.get("run_id")
        and component_source.get("offline_provenance_fingerprint")
        == provenance.get("provenance_fingerprint")
        and component_source.get("offline_recommendations_sha256")
        == recommendations
        and component_source.get("split_role") == "development"
        and statistical_source.get("offline_run_id") == provenance.get("run_id")
        and statistical_source.get("offline_provenance_fingerprint")
        == provenance.get("provenance_fingerprint")
        and statistical_source.get("offline_provenance_sha256")
        == provenance_sha256
        and statistical_source.get("offline_recommendations_sha256")
        == recommendations
        and statistical_source.get("offline_completion_sha256")
        == completion_sha256
        and statistical_source.get("split_role") == "development"
        and statistical_source.get("split_id") == split.manifest.split_id
        and statistical_source.get("split_checksum") == split.manifest.checksum
        and statistical_source.get("dataset_source_file_sha256")
        == split.source_file_sha256
    )
    if not common:
        raise P3GateValidationError(
            "P3 gate raw, component, statistical, and development split "
            "provenance do not form one source chain"
        )


def _evaluate_sources(
    *,
    evidence_dir: Path,
    gold_path: Path,
    component_analysis_dir: Path,
    statistical_analysis_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    canonical_split = load_development_split(
        expected_split_id=DEFAULT_DEVELOPMENT_SPLIT_ID
    )
    if canonical_split.manifest.role is not SplitRole.DEVELOPMENT:
        raise P3GateValidationError(
            "P3 gate source is not the canonical development split"
        )
    gold = load_component_gold(
        gold_path,
        role="development",
        split_id=canonical_split.manifest.split_id,
    )
    if gold.split is None or not _same_split(gold.split, canonical_split):
        raise P3GateValidationError(
            "P3 gate gold does not match the canonical development split"
        )

    validation = validate_offline_evidence(evidence_dir, split=canonical_split)
    if validation.get("status") != "PASS" or validation.get("split_role") != "development":
        raise P3GateValidationError(
            "P3 gate raw source is not authenticated development evidence"
        )
    provenance, records = load_validated_evidence(
        evidence_dir,
        gold,
        systems=("P2",),
    )
    systems = tuple(provenance.get("systems", ()))
    if set(systems) != {"P1", "P2"} or provenance.get(
        "p3_explicitly_enabled"
    ) is not False:
        raise P3GateValidationError(
            "P3 gate requires pre-P3 development evidence from P1 and P2"
        )
    recorded_systems = provenance.get("system_frozen_provenance")
    if not isinstance(recorded_systems, Mapping):
        raise P3GateValidationError(
            "P3 gate raw evidence lacks frozen system provenance"
        )
    production_adapters = default_adapters(enable_p3=False)
    for system_id in ("P1", "P2"):
        current = production_adapters[system_id].frozen_provenance()
        if recorded_systems.get(system_id) != current:
            raise P3GateValidationError(
                f"P3 gate raw evidence does not use the frozen {system_id} comparator"
            )

    component_validation = validate_analysis_package(component_analysis_dir)
    statistical_validation = validate_statistical_package(
        statistical_analysis_dir
    )
    if (
        component_validation.get("analysis_status")
        != "DERIVED_EVIDENCE_COMPLETE"
        or statistical_validation.get("analysis_status")
        != "DERIVED_EVIDENCE_COMPLETE"
    ):
        raise P3GateValidationError(
            "P3 gate requires completed component and statistical evidence"
        )
    component_manifest = _read_json(
        component_analysis_dir / "analysis-manifest.json",
        label="component analysis manifest",
    )
    statistical_manifest = _read_json(
        statistical_analysis_dir / "analysis-manifest.json",
        label="statistical analysis manifest",
    )
    _require_source_join(
        component_manifest=component_manifest,
        statistical_manifest=statistical_manifest,
        provenance=provenance,
        split=canonical_split,
        evidence_dir=evidence_dir,
    )

    component_config = component_manifest.get("configuration")
    if not isinstance(component_config, Mapping):
        raise P3GateValidationError(
            "component evidence lacks its frozen configuration"
        )
    if (
        component_config.get("p3_gate_minimum_count")
        != P3_GATE_MINIMUM_COUNT
        or component_config.get("p3_gate_minimum_fraction")
        != P3_GATE_MINIMUM_FRACTION
    ):
        raise P3GateValidationError(
            "component evidence used a non-predefined P3 gate predicate"
        )
    retrieval_ks = component_config.get("retrieval_ks")
    if (
        not isinstance(retrieval_ks, list)
        or not retrieval_ks
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in retrieval_ks
        )
    ):
        raise P3GateValidationError(
            "component evidence retrieval configuration is invalid"
        )
    recomputed = score_component_records(
        gold,
        records,
        retrieval_ks=tuple(retrieval_ks),
        gate_minimum_count=P3_GATE_MINIMUM_COUNT,
        gate_minimum_fraction=P3_GATE_MINIMUM_FRACTION,
    )
    stored_outputs = {
        "aggregates": _read_json(
            component_analysis_dir / "aggregates.json",
            label="component aggregates",
        ),
        "p3_headroom": _read_json(
            component_analysis_dir / "p3-headroom-gate.json",
            label="P3 headroom evidence",
        ),
        "recommendations": _read_jsonl(
            component_analysis_dir / "per-recommendation.jsonl",
            label="component recommendation evidence",
        ),
        "families": _read_jsonl(
            component_analysis_dir / "per-family.jsonl",
            label="component family evidence",
        ),
    }
    expected_outputs = {
        "aggregates": recomputed.aggregates,
        "p3_headroom": recomputed.p3_headroom,
        "recommendations": list(recomputed.recommendations),
        "families": list(recomputed.families),
    }
    if stored_outputs != expected_outputs:
        raise P3GateValidationError(
            "component evidence does not match recomputation from authenticated raw evidence"
        )

    evaluated = _evaluated_inputs(recomputed)
    decision = "retained" if evaluated["criterion_met"] else "not_retained"
    raw_path = evidence_dir.resolve()
    source = {
        "development_split": _split_identity(canonical_split),
        "gold_dataset": {
            "path": _portable_path(gold_path),
            "sha256": file_sha256(gold_path),
        },
        "raw_evidence": {
            "path": _portable_path(raw_path),
            "run_id": provenance["run_id"],
            "provenance_fingerprint": provenance["provenance_fingerprint"],
            "provenance_sha256": file_sha256(
                raw_path / RAW_DIRECTORY_NAME / PROVENANCE_FILENAME
            ),
            "recommendations_sha256": file_sha256(
                raw_path / RAW_DIRECTORY_NAME / RECORDS_FILENAME
            ),
            "completion_sha256": file_sha256(
                raw_path / REPORT_DIRECTORY_NAME / COMPLETION_FILENAME
            ),
            "record_count": len(records),
        },
        "component_evidence": _analysis_identity(
            component_analysis_dir.resolve(), component_manifest
        ),
        "statistical_evidence": _analysis_identity(
            statistical_analysis_dir.resolve(), statistical_manifest
        ),
    }
    return source, evaluated, decision, _rationale(evaluated)


def _evaluate_sources_checked(
    *,
    evidence_dir: Path,
    gold_path: Path,
    component_analysis_dir: Path,
    statistical_analysis_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    try:
        return _evaluate_sources(
            evidence_dir=evidence_dir,
            gold_path=gold_path,
            component_analysis_dir=component_analysis_dir,
            statistical_analysis_dir=statistical_analysis_dir,
        )
    except P3GateValidationError:
        raise
    except (RuntimeError, OSError, ValueError, TypeError, KeyError) as exc:
        raise P3GateValidationError(
            "P3 gate source evidence failed authoritative validation"
        ) from exc


def build_p3_development_decision(
    *,
    evidence_dir: Path,
    gold_path: Path,
    component_analysis_dir: Path,
    statistical_analysis_dir: Path,
    supplied_decision: str | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    """Revalidate development evidence and build the one strict decision schema."""

    source, evaluated, decision, rationale = _evaluate_sources_checked(
        evidence_dir=evidence_dir,
        gold_path=gold_path,
        component_analysis_dir=component_analysis_dir,
        statistical_analysis_dir=statistical_analysis_dir,
    )
    if supplied_decision is not None and supplied_decision != decision:
        raise P3GateValidationError(
            "caller-supplied P3 decision disagrees with the recomputed predicate"
        )
    document = {
        "schema_version": P3_DEVELOPMENT_DECISION_SCHEMA_VERSION,
        "protocol_version": "5.0.0",
        "created_at_utc": created_at_utc or _now_utc(),
        "computation_version": P3_GATE_COMPUTATION_VERSION,
        "predicate": _predicate(),
        "source": source,
        "evaluated_inputs": evaluated,
        "decision": decision,
        "rationale": rationale,
        "integrity": {
            "source_evidence_revalidated": True,
            "predicate_recomputed": True,
            "confirmatory_data_used": False,
            "claim_eligible": False,
        },
    }
    return parse_p3_development_decision(document).to_dict()


def write_p3_development_decision(
    path: Path,
    *,
    evidence_dir: Path,
    gold_path: Path,
    component_analysis_dir: Path,
    statistical_analysis_dir: Path,
    supplied_decision: str | None = None,
) -> Path:
    document = build_p3_development_decision(
        evidence_dir=evidence_dir,
        gold_path=gold_path,
        component_analysis_dir=component_analysis_dir,
        statistical_analysis_dir=statistical_analysis_dir,
        supplied_decision=supplied_decision,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    payload = serialized.encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("failed to write P3 development decision")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def verify_p3_development_decision(
    path: Path,
) -> VerifiedP3DevelopmentDecision:
    """Recompute the decision and every source identity at a trust boundary."""

    try:
        artifact = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise P3GateValidationError(
            "P3 development decision artifact does not exist"
        ) from exc
    if artifact.is_symlink() or not artifact.is_file():
        raise P3GateValidationError(
            "P3 development decision must be a regular non-symlink file"
        )
    try:
        raw = artifact.read_bytes()
    except OSError as exc:
        raise P3GateValidationError(
            "P3 development decision could not be read"
        ) from exc
    parsed = parse_p3_development_decision(
        _strict_json_document(raw, label="P3 development decision")
    )
    recorded = parsed.to_dict()
    source = recorded["source"]
    current_source, evaluated, decision, rationale = _evaluate_sources_checked(
        evidence_dir=_resolve_path(
            source["raw_evidence"]["path"], label="raw evidence path"
        ),
        gold_path=_resolve_path(
            source["gold_dataset"]["path"], label="gold dataset path"
        ),
        component_analysis_dir=_resolve_path(
            source["component_evidence"]["path"],
            label="component evidence path",
        ),
        statistical_analysis_dir=_resolve_path(
            source["statistical_evidence"]["path"],
            label="statistical evidence path",
        ),
    )
    expected = {
        **recorded,
        "predicate": _predicate(),
        "source": current_source,
        "evaluated_inputs": evaluated,
        "decision": decision,
        "rationale": rationale,
    }
    if recorded != expected:
        changed = sorted(
            key
            for key in set(recorded) | set(expected)
            if recorded.get(key) != expected.get(key)
        )
        raise P3GateValidationError(
            "P3 development decision or authenticated source evidence changed: "
            + ", ".join(changed)
        )
    return VerifiedP3DevelopmentDecision(
        decision=parsed,
        artifact_path=artifact,
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        _construction_key=_VERIFIED_GATE_CONSTRUCTION_KEY,
    )


def reverify_p3_development_decision(
    decision: VerifiedP3DevelopmentDecision,
) -> VerifiedP3DevelopmentDecision:
    if type(decision) is not VerifiedP3DevelopmentDecision:
        raise TypeError(
            "a VerifiedP3DevelopmentDecision from "
            "verify_p3_development_decision() is required"
        )
    current = verify_p3_development_decision(decision.artifact_path)
    if (
        current.artifact_sha256 != decision.artifact_sha256
        or current.to_dict() != decision.to_dict()
    ):
        raise P3GateValidationError(
            "P3 development decision capability no longer matches its artifact"
        )
    return current


def require_retained_p3_gate(
    decision: VerifiedP3DevelopmentDecision,
) -> VerifiedP3DevelopmentDecision:
    current = reverify_p3_development_decision(decision)
    if current.decision != "retained":
        raise PermissionError(
            "P3 execution requires a VERIFIED RETAINED development gate"
        )
    return current


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser(
        "create",
        help="Recompute source evidence and exclusively write a decision.",
    )
    create.add_argument("--evidence-dir", type=Path, required=True)
    create.add_argument("--gold-dataset", type=Path, required=True)
    create.add_argument("--component-analysis", type=Path, required=True)
    create.add_argument("--statistical-analysis", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument(
        "--assert-decision",
        choices=("retained", "not_retained"),
        help="Optional consistency assertion; never controls the result.",
    )
    verify = subparsers.add_parser(
        "verify",
        help="Reopen every source and recompute an existing decision.",
    )
    verify.add_argument("--decision-artifact", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            path = write_p3_development_decision(
                args.output,
                evidence_dir=args.evidence_dir,
                gold_path=args.gold_dataset,
                component_analysis_dir=args.component_analysis,
                statistical_analysis_dir=args.statistical_analysis,
                supplied_decision=args.assert_decision,
            )
            verified = verify_p3_development_decision(path)
        else:
            verified = verify_p3_development_decision(
                args.decision_artifact
            )
        print(
            json.dumps(
                {
                    "schema_version": P3_DEVELOPMENT_DECISION_SCHEMA_VERSION,
                    "status": "VERIFIED",
                    "decision": verified.decision,
                    "artifact_sha256": verified.artifact_sha256,
                    "claims_permitted": False,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except (P3GateValidationError, FileExistsError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": P3_DEVELOPMENT_DECISION_SCHEMA_VERSION,
                    "status": "ERROR",
                    "error": str(exc),
                    "claims_permitted": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


__all__ = [
    "P3_DEVELOPMENT_DECISION_SCHEMA_PATH",
    "P3_DEVELOPMENT_DECISION_SCHEMA_VERSION",
    "P3_GATE_COMPUTATION_VERSION",
    "P3_GATE_MINIMUM_COUNT",
    "P3_GATE_MINIMUM_FRACTION",
    "P3_GATE_PREDICATE_VERSION",
    "P3_GATE_SNAPSHOT_VERSION",
    "P3DevelopmentDecision",
    "P3GateValidationError",
    "VerifiedP3DevelopmentDecision",
    "build_p3_development_decision",
    "main",
    "parse_p3_development_decision",
    "require_retained_p3_gate",
    "reverify_p3_development_decision",
    "verify_p3_development_decision",
    "write_p3_development_decision",
]


if __name__ == "__main__":
    raise SystemExit(main())
