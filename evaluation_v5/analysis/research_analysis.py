"""Unified, fail-closed Protocol-v5 thesis claim analysis.

The module consumes existing evidence packages without modifying them.  It
keeps discovery/validation, normalized metrics, claim adjudication, and
interpretation as separate machine-readable layers.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import io
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
from typing import Any, Mapping, Sequence

from evaluation_v5.analysis.statistics import (
    derive_bootstrap_seed,
    holm_adjust,
    paired_effect_sizes,
    paired_family_bootstrap_ci,
    paired_test,
)

from .research_contracts import (
    EVALUATED_CLAIM_SCHEMA_VERSION,
    REGISTRY_PATH,
    ResearchContractError,
    canonical_json_sha256,
    evaluate_conditions,
    file_sha256,
    json_pointer_get,
    load_claim_registry,
    load_p3_threshold,
    load_selection,
    validate_evaluated_claim,
    validate_storage_evidence,
)


ANALYSIS_PACKAGE_SCHEMA_VERSION = "protocol-v5-research-analysis-package-v1.0.0"
EVIDENCE_INVENTORY_SCHEMA_VERSION = "protocol-v5-research-evidence-inventory-v1.0.0"
PROVENANCE_SCHEMA_VERSION = "protocol-v5-research-provenance-check-v1.0.0"
THREATS_SCHEMA_VERSION = "protocol-v5-threats-to-validity-v1.0.0"
PROTOCOL_VERSION = "5.0.0"
EXIT_SUCCESS = 0
EXIT_FAILED = 2
EXIT_INCOMPLETE = 3
VALIDATION_PASS = "PASS"
VALIDATION_FAIL = "FAIL"
ELIGIBLE_EXECUTION_STATUSES = {"OBSERVED", "DERIVED_EVIDENCE_COMPLETE"}
SEMANTIC_DIGEST_KEYS = {
    "catalog.file_sha256": "catalog_file_bytes",
    "corpus.sha256": "candidate_corpus_canonical",
    "indexes.dense.sha256": "dense_index_canonical",
    "indexes.sparse.sha256": "sparse_index_canonical",
    "indexes.hybrid.sha256": "hybrid_index_canonical",
    "extractor.prompt_sha256": "extractor_prompt_bytes",
    "p3.prompt_sha256": "p3_prompt_bytes",
}


class ResearchAnalysisError(ValueError):
    """Raised for invalid evidence or analysis-package state."""


@dataclass
class EvidenceCandidate:
    requirement_id: str
    evidence_class: str
    experiment_id: str
    package_path: Path
    manifest_path: Path
    manifest_sha256: str
    schema_version: str | None
    stage: str
    execution_status: str
    validation_status: str
    claims_permitted: bool
    claim_eligibility: str
    metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    tests: dict[str, Any] = field(default_factory=dict)
    semantic_provenance: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    validation_error: str | None = None

    @property
    def eligible(self) -> bool:
        return self.claim_eligibility == "ELIGIBLE_CONFIRMATORY"

    def to_dict(self, *, root: Path | None = None) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "evidence_class": self.evidence_class,
            "experiment_id": self.experiment_id,
            "package_path": _display_path(self.package_path, root),
            "manifest_path": _display_path(self.manifest_path, root),
            "manifest_sha256": self.manifest_sha256,
            "schema_version": self.schema_version,
            "stage": self.stage,
            "execution_status": self.execution_status,
            "validation_status": self.validation_status,
            "claims_permitted": self.claims_permitted,
            "claim_eligibility": self.claim_eligibility,
            "metrics": self.metrics,
            "tests": self.tests,
            "semantic_provenance": self.semantic_provenance,
            "provenance": self.provenance,
            "metadata": self.metadata,
            "artifacts": self.artifacts,
            "reason_codes": sorted(set(self.reason_codes)),
            "validation_error": self.validation_error,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _git_identity() -> dict[str, Any]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, check=False
    )
    return {
        "git_revision": revision.stdout.strip() if revision.returncode == 0 else "unknown",
        "git_dirty": dirty.returncode != 0 or bool(dirty.stdout.strip()),
    }


def _display_path(path: Path, root: Path | None) -> str:
    resolved = path.resolve()
    if root is not None:
        try:
            return str(resolved.relative_to(root.resolve()))
        except ValueError:
            pass
    return str(resolved)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResearchAnalysisError(f"{path}: invalid JSON") from exc
    if not isinstance(value, dict):
        raise ResearchAnalysisError(f"{path}: expected an object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ResearchAnalysisError(f"{path}: unreadable JSONL") from exc
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        if not line:
            raise ResearchAnalysisError(f"{path}:{number}: blank JSONL line")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ResearchAnalysisError(f"{path}:{number}: invalid JSON") from exc
        if not isinstance(value, dict):
            raise ResearchAnalysisError(f"{path}:{number}: expected an object")
        rows.append(value)
    return rows


def _artifact(path: Path, package: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "package_relative_path": str(path.resolve().relative_to(package.resolve())),
        "sha256": file_sha256(path),
    }


def _nested(value: Mapping[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def _effective_p(row: Mapping[str, Any]) -> float | None:
    adjusted = _finite(row.get("p_value_holm"))
    return adjusted if adjusted is not None else _finite(row.get("p_value_raw"))


def _comparison_row(
    rows: Sequence[Mapping[str, Any]], comparison: str, endpoint: str
) -> Mapping[str, Any] | None:
    selected = [
        row
        for row in rows
        if row.get("comparison_id") == comparison and row.get("endpoint") == endpoint
    ]
    return selected[0] if len(selected) == 1 else None


def _eligible_state(
    *, stage: str, execution_status: str, claims_permitted: bool, validation_status: str
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if validation_status != VALIDATION_PASS:
        reasons.append("EVIDENCE_VALIDATION_FAILED")
    if stage != "confirmatory":
        reasons.append("NON_CONFIRMATORY_EVIDENCE")
    if execution_status not in ELIGIBLE_EXECUTION_STATUSES:
        reasons.append("EVIDENCE_NOT_OBSERVED_COMPLETE")
    if not claims_permitted:
        reasons.append("CLAIMS_NOT_PERMITTED")
    return (
        "ELIGIBLE_CONFIRMATORY" if not reasons else "INELIGIBLE",
        reasons,
    )


def _offline_semantics(manifest: Mapping[str, Any], provenance: Mapping[str, Any]) -> dict[str, Any]:
    frozen = manifest.get("offline_frozen_configuration") or provenance.get("frozen_configuration") or {}
    systems = manifest.get("backend_system_versions") or provenance.get("system_frozen_provenance") or {}
    p1 = systems.get("P1") or {}
    p2 = systems.get("P2") or {}
    catalog = manifest.get("candidate_catalog") or provenance.get("candidate_catalog") or {}
    return {
        "p1.backend_version": p1.get("backend_version") or _nested(frozen, "systems", "P1", "backend_version"),
        "p2.backend_version": p2.get("backend_version") or _nested(frozen, "systems", "P2", "backend_version"),
        "p2.pipeline_version": p2.get("pipeline_version") or _nested(frozen, "systems", "P2", "pipeline_version"),
        "catalog.version": catalog.get("catalog_version") or catalog.get("version"),
        "corpus.sha256": catalog.get("corpus_sha256"),
        "corpus.version": catalog.get("corpus_version"),
        "p2.config_version": _nested(p2, "config", "config_version") or _nested(frozen, "configuration", "P2", "config_version"),
        "constraints.evaluator_version": _nested(p2, "constraint_ranking_configuration", "constraint_evaluator_version") or _nested(frozen, "configuration", "constraints", "evaluator_version"),
        "constraints.policy_version": _nested(p2, "constraint_ranking_configuration", "constraint_policy_version") or _nested(frozen, "configuration", "constraints", "policy_version"),
        "constraints.ranker_version": _nested(p2, "constraint_ranking_configuration", "ranker_version") or _nested(frozen, "configuration", "constraints", "ranker_version"),
        "indexes.dense.sha256": p2.get("dense_index_sha256") or _nested(frozen, "indexes", "dense", "index_checksum"),
        "indexes.sparse.sha256": p2.get("sparse_index_sha256") or _nested(frozen, "indexes", "sparse", "index_checksum"),
        "indexes.hybrid.sha256": p2.get("hybrid_index_sha256") or _nested(frozen, "indexes", "hybrid", "index_checksum"),
        "extractor.prompt_sha256": p2.get("extractor_prompt_sha256") or _nested(frozen, "prompts", "P2_extractor", "prompt_sha256"),
        "p3.pipeline_version": _nested(frozen, "systems", "P3", "pipeline_version"),
        "p3.reranker_version": _nested(frozen, "systems", "P3", "reranker_version"),
        "p3.prompt_sha256": _nested(frozen, "prompts", "P3_reranker", "prompt_sha256"),
    }


def _h1_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    row = _comparison_row(rows, "P2_minus_P1", "joint_accept_at_1") or {}
    effect = row.get("effect_sizes") or row.get("effects") or {}
    p_value = _effective_p(row)
    return {
        "effect": _finite(effect.get("mean_difference")),
        "ci_low": _finite(row.get("effect_ci_low") if "effect_ci_low" in row else row.get("ci_low")),
        "ci_high": _finite(row.get("effect_ci_high") if "effect_ci_high" in row else row.get("ci_high")),
        "p_value": p_value,
        "test_available": bool(
            row.get("hypothesis_status") == "TESTED"
            and row.get("statistical_decision") not in {"WITHHELD_SMALL_N", "NOT_COMPUTABLE"}
            and p_value is not None
        ),
        "test_method": row.get("test_method"),
        "effective_family_n": row.get("effective_family_n"),
    }


def _h2_metrics(family_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_family: dict[str, dict[str, float]] = defaultdict(dict)
    for row in family_rows:
        system = str(row.get("system_id"))
        if system not in {"P1", "P2"}:
            continue
        denominators = row.get("endpoint_variant_denominators") or {}
        sums = row.get("endpoint_variant_sums") or {}
        joint_n = denominators.get("joint_accept_at_1")
        robust_n = denominators.get("robustness_rate")
        joint_sum = _finite(sums.get("joint_accept_at_1"))
        robust_sum = _finite(sums.get("robustness_rate"))
        if (
            isinstance(joint_n, int)
            and isinstance(robust_n, int)
            and joint_n == robust_n + 1
            and robust_n > 0
            and joint_sum is not None
            and robust_sum is not None
        ):
            canonical = joint_sum - robust_sum
            equivalent = robust_sum / robust_n
            by_family[str(row.get("family_id"))][system] = canonical - equivalent
    paired = [
        {"family_id": family, "P1": values["P1"], "P2": values["P2"]}
        for family, values in sorted(by_family.items())
        if set(values) == {"P1", "P2"}
    ]
    first = [row["P1"] for row in paired]
    second = [row["P2"] for row in paired]
    effect = paired_effect_sizes(first, second)
    test = paired_test(first, second, binary_outcome=False)
    ci_low, ci_high = paired_family_bootstrap_ci(
        paired,
        "P1",
        "P2",
        replicates=2000,
        seed=derive_bootstrap_seed(20260824, "research-analysis", "H2"),
    )
    return {
        "effect": _finite(effect.get("mean_difference")),
        "ci_low": _finite(ci_low),
        "ci_high": _finite(ci_high),
        "p_value": _finite(test.get("p_value_raw")),
        "test_available": bool(
            test.get("inference_status") == "ELIGIBLE"
            and test.get("p_value_raw") is not None
        ),
        "test_method": test.get("test_method"),
        "effective_family_n": len(paired),
        "derivation": "canonical JointAccept@1 minus mean reviewed-equivalent JointAccept@1; P2 loss minus P1 loss",
    }


def _p3_metrics(
    rows: Sequence[Mapping[str, Any]], freeze: Mapping[str, Any], threshold: Mapping[str, Any] | None
) -> dict[str, Any]:
    quality = _comparison_row(rows, "P3_minus_P2", "joint_accept_at_1") or {}
    effect = quality.get("effect_sizes") or quality.get("effects") or {}
    gate = freeze.get("p3_gate") or {}
    limits: list[dict[str, Any]] = []
    all_within = threshold is not None
    if threshold is not None:
        for limit in threshold["overhead_limits"]:
            row = _comparison_row(rows, "P3_minus_P2", str(limit["metric"])) or {}
            ci_high = _finite(row.get("effect_ci_high") if "effect_ci_high" in row else row.get("ci_high"))
            within = ci_high is not None and ci_high <= float(limit["maximum_p3_minus_p2"])
            all_within = bool(all_within and within)
            limits.append({**dict(limit), "observed_ci_high": ci_high, "within_limit": within})
    p_value = _effective_p(quality)
    return {
        "gate_retained": gate.get("status") == "retained" and gate.get("p3_active") is True,
        "threshold_frozen": threshold is not None,
        "quality_effect": _finite(effect.get("mean_difference")),
        "quality_ci_low": _finite(quality.get("effect_ci_low") if "effect_ci_low" in quality else quality.get("ci_low")),
        "quality_ci_high": _finite(quality.get("effect_ci_high") if "effect_ci_high" in quality else quality.get("ci_high")),
        "quality_p_value": p_value,
        "all_overhead_ci_within_limits": all_within,
        "overhead_limits": limits,
        "tests_available": bool(
            quality.get("hypothesis_status") == "TESTED"
            and p_value is not None
            and threshold is not None
            and limits
            and all(row["observed_ci_high"] is not None for row in limits)
        ),
        "effective_family_n": quality.get("effective_family_n"),
    }


def _adapt_offline_package(
    package: Path,
    *,
    freeze: Mapping[str, Any],
    threshold: Mapping[str, Any] | None,
) -> list[EvidenceCandidate]:
    provenance_path = package / "raw" / "offline-run-provenance.json"
    completion_path = package / "report" / "offline-run-completion.json"
    statistics_root = package / "derived" / "statistical_analysis"
    statistics_manifest_path = statistics_root / "analysis-manifest.json"
    provenance = _read_json(provenance_path)
    completion = _read_json(completion_path)
    statistics_manifest = _read_json(statistics_manifest_path)

    from evaluation_v5.analysis.statistical_analysis import validate_statistical_package
    from evaluation_v5.offline.validate_evidence import validate_offline_evidence

    validation_errors: list[str] = []
    try:
        stat_validation = validate_statistical_package(statistics_root)
    except Exception as exc:  # every adapter fails closed at the package boundary
        stat_validation = {"analysis_status": "INVALID"}
        validation_errors.append(f"statistical package: {exc}")
    stage = str(
        _nested(statistics_manifest, "source", "split_role")
        or _nested(provenance, "split", "role")
        or "unknown"
    ).lower()
    if stage == "development":
        try:
            validate_offline_evidence(package)
        except Exception as exc:
            validation_errors.append(f"offline package: {exc}")
    if completion.get("recommendations_jsonl_sha256") != file_sha256(
        package / "raw" / "recommendations.jsonl"
    ):
        validation_errors.append("offline completion/recommendations checksum mismatch")
    validation_status = VALIDATION_FAIL if validation_errors else VALIDATION_PASS
    execution_status = str(statistics_manifest.get("status") or completion.get("status") or "UNKNOWN")
    # Raw completion deliberately never permits claims.  Claim permission is
    # introduced only by the separately validated confirmatory statistics
    # package, after the sealed labels have entered the analysis boundary.
    claims_permitted = bool(statistics_manifest.get("claims_permitted"))
    eligibility, reasons = _eligible_state(
        stage=stage,
        execution_status=execution_status,
        claims_permitted=claims_permitted,
        validation_status=validation_status,
    )
    family_path = statistics_root / "family-estimates.jsonl"
    paired_path = statistics_root / "paired-comparisons.jsonl"
    family_rows = _read_jsonl(family_path) if family_path.is_file() else []
    paired_rows = _read_jsonl(paired_path) if paired_path.is_file() else []
    common = {
        "evidence_class": "E1",
        "experiment_id": "E1",
        "package_path": package,
        "manifest_path": statistics_manifest_path,
        "manifest_sha256": file_sha256(statistics_manifest_path),
        "schema_version": statistics_manifest.get("schema_version"),
        "stage": stage,
        "execution_status": execution_status,
        "validation_status": validation_status,
        "claims_permitted": claims_permitted,
        "claim_eligibility": eligibility,
        "tests": {
            "statistical_package": stat_validation,
            "testing_policy": statistics_manifest.get("testing_policy") or {},
        },
        "semantic_provenance": _offline_semantics(statistics_manifest, provenance),
        "provenance": {
            "git_revision": statistics_manifest.get("git_revision") or provenance.get("git_revision"),
            "git_dirty": provenance.get("git_dirty"),
            "source_environment": statistics_manifest.get("source_environment_identity") or provenance.get("environment_identity"),
            "analysis_environment": statistics_manifest.get("analysis_environment_identity"),
            "dataset": statistics_manifest.get("source") or provenance.get("split"),
            "freeze_identity": _nested(statistics_manifest, "source", "freeze_identity") or provenance.get("freeze_identity"),
        },
        "metadata": {
            "family_n": _nested(provenance, "split", "family_count"),
            "variant_n": _nested(provenance, "split", "case_count"),
            "repeat_policy": statistics_manifest.get("aggregation_policy") or {},
            "split_role": stage,
        },
        "artifacts": [
            _artifact(statistics_manifest_path, package),
            _artifact(provenance_path, package),
            _artifact(completion_path, package),
            *([_artifact(family_path, package)] if family_path.is_file() else []),
            *([_artifact(paired_path, package)] if paired_path.is_file() else []),
        ],
        "reason_codes": reasons,
        "validation_error": "; ".join(validation_errors) or None,
    }
    offline = EvidenceCandidate(
        requirement_id="offline_recommendation",
        metrics={"H1": _h1_metrics(paired_rows)},
        **common,
    )
    robustness = EvidenceCandidate(
        requirement_id="natural_language_robustness",
        evidence_class="E2",
        metrics={"H2": _h2_metrics(family_rows)},
        **{key: value for key, value in common.items() if key != "evidence_class"},
    )
    candidates = [offline, robustness]
    systems = set(statistics_manifest.get("systems") or provenance.get("systems") or [])
    if "P3" in systems:
        p3_eligibility = eligibility
        p3_reasons = list(reasons)
        if _nested(freeze, "p3_gate", "status") != "retained" or _nested(freeze, "p3_gate", "p3_active") is not True:
            p3_eligibility = "INELIGIBLE"
            p3_reasons.append("P3_NOT_RETAINED")
        candidates.append(
            EvidenceCandidate(
                requirement_id="p2_p3",
                evidence_class="P2_P3",
                metrics={"H8": _p3_metrics(paired_rows, freeze, threshold)},
                claim_eligibility=p3_eligibility,
                reason_codes=p3_reasons,
                **{
                    key: value
                    for key, value in common.items()
                    if key not in {"evidence_class", "claim_eligibility", "reason_codes", "metrics"}
                },
            )
        )
    return candidates


def _validate_output_checksums(package: Path, manifest: Mapping[str, Any]) -> None:
    outputs = manifest.get("output_checksums")
    if not isinstance(outputs, Mapping) or not outputs:
        raise ResearchAnalysisError("package manifest lacks output_checksums")
    for relative, expected in outputs.items():
        path = package / str(relative)
        try:
            path.resolve().relative_to(package.resolve())
        except ValueError as exc:
            raise ResearchAnalysisError("package checksum path escapes its root") from exc
        if not path.is_file() or file_sha256(path) != expected:
            raise ResearchAnalysisError(f"output checksum mismatch: {relative}")


def _adapt_user_study_package(package: Path) -> EvidenceCandidate:
    manifest_path = package / "manifest.json"
    manifest = _read_json(manifest_path)
    validation_errors: list[str] = []
    try:
        if manifest.get("schema_version") != "protocol-v5-user-study-provenance-v1.0.0":
            raise ResearchAnalysisError("unsupported E3 provenance schema")
        _validate_output_checksums(package, manifest)
        privacy = _read_json(package / "report" / "privacy-audit.json")
        if privacy.get("status") != "PASS" or privacy.get("direct_identifier_findings") != 0:
            raise ResearchAnalysisError("E3 aggregate privacy audit did not pass")
        analysis = _read_json(package / "derived" / "analysis.json")
        if analysis.get("schema_version") != "protocol-v5-user-study-analysis-v1.2.0":
            raise ResearchAnalysisError("unsupported E3 analysis schema")
        if analysis.get("execution_status") != manifest.get("execution_status"):
            raise ResearchAnalysisError("E3 manifest and analysis execution statuses disagree")
    except Exception as exc:
        validation_errors.append(str(exc))
        privacy = {}
        analysis = {}
    status_path = package / "report" / "status.json"
    status = _read_json(status_path) if status_path.is_file() else {}
    stage = str(status.get("task_set_stage") or "unknown").lower()
    execution_status = str(manifest.get("execution_status") or status.get("execution_status") or "UNKNOWN")
    claims_permitted = bool(analysis.get("claims_permitted"))
    validation_status = VALIDATION_FAIL if validation_errors else VALIDATION_PASS
    eligibility, reasons = _eligible_state(
        stage=stage,
        execution_status=execution_status,
        claims_permitted=claims_permitted,
        validation_status=validation_status,
    )
    effects = analysis.get("effects") or {}
    selection = effects.get("selection_success") or {}
    decision = effects.get("decision_time_seconds") or {}
    raw_time = decision.get("raw_paired_effect") or {}
    seq = effects.get("seq_ease") or {}
    seq_effect = seq.get("paired_effect") or {}
    sus = effects.get("sus") or {}
    selection_ci = selection.get("risk_difference_ci_95") or [None, None]
    time_ci = raw_time.get("confidence_interval_95") or [None, None]
    seq_ci = seq_effect.get("confidence_interval_95") or [None, None]
    sus_ci = sus.get("confidence_interval_95") or [None, None]
    h3 = {
        "selection_effect": _finite(selection.get("risk_difference")),
        "selection_ci_low": _finite(selection_ci[0]) if len(selection_ci) == 2 else None,
        "selection_ci_high": _finite(selection_ci[1]) if len(selection_ci) == 2 else None,
        "selection_p_holm": _finite(selection.get("p_value_holm")),
        "time_effect": _finite(raw_time.get("mean_difference")),
        "time_ci_low": _finite(time_ci[0]) if len(time_ci) == 2 else None,
        "time_ci_high": _finite(time_ci[1]) if len(time_ci) == 2 else None,
        "time_p_holm": _finite(decision.get("p_value_holm")),
        "tests_available": bool(
            selection.get("status") in {"MODELED", "FALLBACK"}
            and decision.get("status") in {"MODELED", "FALLBACK"}
            and selection.get("p_value_holm") is not None
            and decision.get("p_value_holm") is not None
        ),
        "selection_method": selection.get("method"),
        "time_method": decision.get("method"),
    }
    h4 = {
        "seq_effect": _finite(seq_effect.get("mean_difference")),
        "seq_ci_low": _finite(seq_ci[0]) if len(seq_ci) == 2 else None,
        "seq_ci_high": _finite(seq_ci[1]) if len(seq_ci) == 2 else None,
        "sus_effect": _finite(sus.get("mean_difference")),
        "sus_ci_low": _finite(sus_ci[0]) if len(sus_ci) == 2 else None,
        "sus_ci_high": _finite(sus_ci[1]) if len(sus_ci) == 2 else None,
        "estimates_available": all(
            _finite(value) is not None
            for value in (
                seq_effect.get("mean_difference"),
                seq_ci[0] if len(seq_ci) == 2 else None,
                seq_ci[1] if len(seq_ci) == 2 else None,
                sus.get("mean_difference"),
                sus_ci[0] if len(sus_ci) == 2 else None,
                sus_ci[1] if len(sus_ci) == 2 else None,
            )
        ),
        "custom_items": effects.get("custom_items") or {},
    }
    catalog = _nested(manifest, "study_identity", "authoritative_catalog") or {}
    return EvidenceCandidate(
        requirement_id="user_study",
        evidence_class="E3",
        experiment_id="E3",
        package_path=package,
        manifest_path=manifest_path,
        manifest_sha256=file_sha256(manifest_path),
        schema_version=manifest.get("schema_version"),
        stage=stage,
        execution_status=execution_status,
        validation_status=validation_status,
        claims_permitted=claims_permitted,
        claim_eligibility=eligibility,
        metrics={"H3": h3, "H4": h4},
        tests={"primary_inference_registry": analysis.get("primary_inference_registry") or {}},
        semantic_provenance={
            "catalog.version": catalog.get("catalog_version"),
            "corpus.sha256": catalog.get("corpus_sha256"),
            "corpus.version": catalog.get("corpus_version"),
        },
        provenance={
            "git_revision": manifest.get("git_revision"),
            "runtime": manifest.get("runtime"),
            "study_identity": manifest.get("study_identity"),
            "contracts": manifest.get("contracts"),
        },
        metadata={
            "participant_target": status.get("participant_target"),
            "completed_participants": status.get("valid_completed_crossover_count"),
            "design_diagnostics": analysis.get("design_diagnostics") or {},
            "missingness": analysis.get("missingness") or [],
            "privacy_audit": privacy,
        },
        artifacts=[
            _artifact(manifest_path, package),
            *([_artifact(package / "derived" / "analysis.json", package)] if (package / "derived" / "analysis.json").is_file() else []),
            *([_artifact(package / "report" / "privacy-audit.json", package)] if (package / "report" / "privacy-audit.json").is_file() else []),
            *([_artifact(status_path, package)] if status_path.is_file() else []),
        ],
        reason_codes=reasons,
        validation_error="; ".join(validation_errors) or None,
    )


def _resource_row(
    rows: Sequence[Mapping[str, Any]], endpoint: str, candidate: str, reference: str
) -> Mapping[str, Any]:
    matched = [
        row
        for row in rows
        if row.get("endpoint") == endpoint
        and row.get("candidate_condition") == candidate
        and row.get("reference_condition") == reference
    ]
    return matched[0] if len(matched) == 1 else {}


def _resource_effect(row: Mapping[str, Any]) -> dict[str, Any]:
    effect = row.get("effect") or {}
    interval = row.get("ci_95_candidate_minus_reference") or [None, None]
    test = row.get("test") or {}
    return {
        "effect": _finite(effect.get("mean_difference")),
        "ci_low": _finite(interval[0]) if len(interval) == 2 else None,
        "ci_high": _finite(interval[1]) if len(interval) == 2 else None,
        "p_holm": _finite(test.get("p_value_holm_within_endpoint")),
        "p_raw": _finite(test.get("p_value_raw")),
        "test_method": test.get("test_method"),
        "effective_family_n": row.get("effective_family_n"),
    }


def _h5_metrics(
    statistics_rows: Sequence[Mapping[str, Any]], pareto_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    cpu = _resource_effect(
        _resource_row(statistics_rows, "cpu_cost_per_success", "P2_CATALOG", "STATIC_LARGE")
    )
    memory = _resource_effect(
        _resource_row(statistics_rows, "memory_cost_per_success", "P2_CATALOG", "STATIC_LARGE")
    )
    pareto = [
        row
        for row in pareto_rows
        if row.get("condition") == "P2_CATALOG" and row.get("reference") == "STATIC_LARGE"
    ]
    return {
        "pareto_classification": pareto[0].get("classification") if len(pareto) == 1 else None,
        "cpu_effect": cpu["effect"],
        "cpu_ci_low": cpu["ci_low"],
        "cpu_ci_high": cpu["ci_high"],
        "cpu_p_holm": cpu["p_holm"],
        "memory_effect": memory["effect"],
        "memory_ci_low": memory["ci_low"],
        "memory_ci_high": memory["ci_high"],
        "memory_p_holm": memory["p_holm"],
        "tests_available": all(
            value is not None
            for value in (
                cpu["effect"], cpu["ci_low"], cpu["ci_high"], cpu["p_holm"],
                memory["effect"], memory["ci_low"], memory["ci_high"], memory["p_holm"],
            )
        ),
        "cpu": cpu,
        "memory": memory,
        "success_noninferiority_margin": None,
    }


def _h6_axis(
    trials: Sequence[Mapping[str, Any]], field: str, *, seed_offset: str
) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in trials:
        if row.get("condition") not in {"P2_CATALOG", "P2_DYNAMIC"}:
            continue
        value = _finite(row.get(field))
        if value is not None and row.get("valid_attempt", True):
            grouped[(str(row.get("family_id")), str(row.get("condition")))].append(value)
    families = sorted({key[0] for key in grouped})
    paired = [
        {
            "family_id": family,
            "catalog": sum(grouped[(family, "P2_CATALOG")]) / len(grouped[(family, "P2_CATALOG")]),
            "dynamic": sum(grouped[(family, "P2_DYNAMIC")]) / len(grouped[(family, "P2_DYNAMIC")]),
        }
        for family in families
        if grouped.get((family, "P2_CATALOG")) and grouped.get((family, "P2_DYNAMIC"))
    ]
    first = [row["catalog"] for row in paired]
    second = [row["dynamic"] for row in paired]
    effect = paired_effect_sizes(first, second)
    test = paired_test(first, second, binary_outcome=False)
    ci = paired_family_bootstrap_ci(
        paired,
        "catalog",
        "dynamic",
        replicates=2000,
        seed=derive_bootstrap_seed(20260904, "research-analysis", "H6", seed_offset),
    )
    return {
        "effect": _finite(effect.get("mean_difference")),
        "ci_low": _finite(ci[0]),
        "ci_high": _finite(ci[1]),
        "p_raw": _finite(test.get("p_value_raw")),
        "test_method": test.get("test_method"),
        "effective_family_n": len(paired),
        "inference_status": test.get("inference_status"),
    }


def _h6_metrics(trials: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cpu = _h6_axis(trials, "cpu_request_allocation_error_absolute", seed_offset="cpu")
    memory = _h6_axis(trials, "memory_request_allocation_error_absolute", seed_offset="memory")
    if cpu["p_raw"] is not None and memory["p_raw"] is not None:
        cpu_holm, memory_holm = holm_adjust([cpu["p_raw"], memory["p_raw"]])
    else:
        cpu_holm = memory_holm = None
    cpu["p_holm"] = _finite(cpu_holm)
    memory["p_holm"] = _finite(memory_holm)
    available = all(
        value is not None
        for value in (
            cpu["effect"], cpu["ci_low"], cpu["ci_high"], cpu["p_holm"],
            memory["effect"], memory["ci_low"], memory["ci_high"], memory["p_holm"],
        )
    ) and cpu["inference_status"] == "ELIGIBLE" and memory["inference_status"] == "ELIGIBLE"
    return {
        "cpu_effect": cpu["effect"],
        "cpu_ci_low": cpu["ci_low"],
        "cpu_ci_high": cpu["ci_high"],
        "cpu_p_holm": cpu["p_holm"],
        "memory_effect": memory["effect"],
        "memory_ci_low": memory["ci_low"],
        "memory_ci_high": memory["ci_high"],
        "memory_p_holm": memory["p_holm"],
        "tests_available": available,
        "cpu": cpu,
        "memory": memory,
        "multiplicity": "Holm across CPU-request and memory-request absolute-error tests",
    }


def _find_resource_raw_package(analysis_root: Path, raw_sha256: str | None) -> Path | None:
    if not raw_sha256:
        return None
    for sums in sorted(analysis_root.parent.glob("*/SHA256SUMS")):
        if sums.parent == analysis_root:
            continue
        if file_sha256(sums) == raw_sha256:
            return sums.parent
    return None


def _resource_semantics(plan: Mapping[str, Any]) -> dict[str, Any]:
    decisions = plan.get("decisions") or []
    if not decisions:
        return {}
    first = decisions[0]
    provenance = {
        "system_frozen_provenance": {
            "P1": first.get("p1_frozen_provenance") or {},
            "P2": first.get("p2_frozen_provenance") or {},
        }
    }
    values = _offline_semantics({}, provenance)
    return {key: value for key, value in values.items() if value is not None}


def _adapt_resource_analysis(package: Path) -> EvidenceCandidate:
    manifest_path = package / "manifest.json"
    manifest = _read_json(manifest_path)
    validation_errors: list[str] = []
    raw_root = _find_resource_raw_package(package, manifest.get("raw_package_sha256"))
    raw_manifest: dict[str, Any] = {}
    plan: dict[str, Any] = {}
    environment: dict[str, Any] = {}
    if raw_root is not None:
        try:
            raw_manifest = _read_json(raw_root / "manifest.json")
            plan = _read_json(raw_root / "plan.json")
            environment = _read_json(raw_root / "raw" / "environment.json")
        except Exception as exc:
            validation_errors.append(str(exc))
    try:
        from evaluation_v5.resource.efficiency_contracts import FREEZE_PATH, load_efficiency_freeze
        from evaluation_v5.resource.efficiency_evidence import (
            validate_analysis_package,
            validate_raw_package,
        )

        validate_analysis_package(package)
        if raw_root is None:
            raise ResearchAnalysisError("E4 analysis cannot be bound to a raw package")
        validate_raw_package(raw_root)
        freeze_contract = load_efficiency_freeze()
        freeze_matches = plan.get("freeze_contract_sha256") == file_sha256(FREEZE_PATH)
        stage = (
            "confirmatory"
            if freeze_matches
            and freeze_contract.get("current_phase") == "confirmatory"
            and freeze_contract.get("confirmatory_freeze_status") == "FROZEN"
            else "unknown"
        )
    except Exception as exc:
        validation_errors.append(str(exc))
        try:
            from evaluation_v5.resource.efficiency_contracts import FREEZE_PATH, load_efficiency_freeze

            freeze_contract = load_efficiency_freeze()
            stage = (
                "confirmatory"
                if plan.get("freeze_contract_sha256") == file_sha256(FREEZE_PATH)
                and freeze_contract.get("current_phase") == "confirmatory"
                and freeze_contract.get("confirmatory_freeze_status") == "FROZEN"
                else "unknown"
            )
        except Exception:
            stage = "unknown"
    validation_status = VALIDATION_FAIL if validation_errors else VALIDATION_PASS
    execution_status = (
        "DERIVED_EVIDENCE_COMPLETE"
        if raw_manifest.get("execution_status") == "OBSERVED" and validation_status == VALIDATION_PASS
        else str(raw_manifest.get("execution_status") or "INVALID")
    )
    claims_permitted = bool(stage == "confirmatory" and execution_status == "DERIVED_EVIDENCE_COMPLETE")
    eligibility, reasons = _eligible_state(
        stage=stage,
        execution_status=execution_status,
        claims_permitted=claims_permitted,
        validation_status=validation_status,
    )
    statistics_path = package / "statistics" / "results.json"
    pareto_path = package / "report" / "pareto.json"
    trials_path = package / "derived" / "trials.jsonl"
    statistics = _read_json(statistics_path) if statistics_path.is_file() else {}
    pareto = _read_json(pareto_path) if pareto_path.is_file() else {}
    trials = _read_jsonl(trials_path) if trials_path.is_file() else []
    return EvidenceCandidate(
        requirement_id="resource_efficiency",
        evidence_class="E4",
        experiment_id="E4_RESOURCE_EFFICIENCY",
        package_path=package,
        manifest_path=manifest_path,
        manifest_sha256=file_sha256(manifest_path),
        schema_version=manifest.get("schema_version"),
        stage=stage,
        execution_status=execution_status,
        validation_status=validation_status,
        claims_permitted=claims_permitted,
        claim_eligibility=eligibility,
        metrics={
            "H5": _h5_metrics(statistics.get("rows") or [], pareto.get("rows") or []),
            "H6": _h6_metrics(trials),
        },
        tests={
            "family_is_primary_unit": statistics.get("family_is_primary_unit"),
            "repetitions_are_independent_families": statistics.get("repetitions_are_independent_families"),
            "design_counts": statistics.get("design_counts") or {},
        },
        semantic_provenance=_resource_semantics(plan),
        provenance={
            "analysis_manifest": manifest,
            "raw_manifest": raw_manifest,
            "git_revision": plan.get("git_revision"),
            "environment": environment,
            "raw_package_path": str(raw_root.resolve()) if raw_root else None,
        },
        metadata={
            "family_n": _nested(statistics, "design_counts", "number_of_families"),
            "repetitions": _nested(statistics, "design_counts", "repetitions_per_family_condition"),
            "cluster_identity": environment.get("cluster_identity") or environment.get("kubernetes"),
            "environment": environment,
            "capacity_evidence_type": manifest.get("capacity_evidence_type"),
            "success_noninferiority_margin": None,
        },
        artifacts=[
            _artifact(manifest_path, package),
            *([_artifact(statistics_path, package)] if statistics_path.is_file() else []),
            *([_artifact(pareto_path, package)] if pareto_path.is_file() else []),
            *([_artifact(trials_path, package)] if trials_path.is_file() else []),
            *([{"path": str((raw_root / "manifest.json").resolve()), "package_relative_path": None, "sha256": file_sha256(raw_root / "manifest.json")}] if raw_root and (raw_root / "manifest.json").is_file() else []),
        ],
        reason_codes=reasons,
        validation_error="; ".join(validation_errors) or None,
    )


def _adapt_resource_raw(package: Path) -> EvidenceCandidate:
    manifest_path = package / "manifest.json"
    manifest = _read_json(manifest_path)
    validation_errors: list[str] = []
    try:
        from evaluation_v5.resource.efficiency_evidence import validate_raw_package

        validate_raw_package(package)
    except Exception as exc:
        validation_errors.append(str(exc))
    plan_path = package / "plan.json"
    plan = _read_json(plan_path) if plan_path.is_file() else {}
    validation_status = VALIDATION_FAIL if validation_errors else VALIDATION_PASS
    stage = "development"
    eligibility, reasons = _eligible_state(
        stage=stage,
        execution_status=str(manifest.get("execution_status") or "UNKNOWN"),
        claims_permitted=False,
        validation_status=validation_status,
    )
    reasons.append("DERIVED_ANALYSIS_PACKAGE_REQUIRED")
    return EvidenceCandidate(
        requirement_id="resource_efficiency",
        evidence_class="E4",
        experiment_id="E4_RESOURCE_EFFICIENCY",
        package_path=package,
        manifest_path=manifest_path,
        manifest_sha256=file_sha256(manifest_path),
        schema_version=manifest.get("schema_version"),
        stage=stage,
        execution_status=str(manifest.get("execution_status") or "UNKNOWN"),
        validation_status=validation_status,
        claims_permitted=False,
        claim_eligibility=eligibility,
        semantic_provenance=_resource_semantics(plan),
        provenance={"manifest": manifest, "git_revision": plan.get("git_revision")},
        metadata={"blocker_codes": manifest.get("blocker_codes") or []},
        artifacts=[
            _artifact(manifest_path, package),
            *([_artifact(plan_path, package)] if plan_path.is_file() else []),
        ],
        reason_codes=reasons,
        validation_error="; ".join(validation_errors) or None,
    )


def _adapt_image_functional(package: Path) -> EvidenceCandidate:
    manifest_path = package / "manifest.json"
    manifest = _read_json(manifest_path)
    validation_errors: list[str] = []
    validation: dict[str, Any] = {}
    try:
        from evaluation_v5.image_storage.validate_evidence import validate_e5_evidence

        validation = validate_e5_evidence(package)
    except Exception as exc:
        validation_errors.append(str(exc))
    metrics_path = package / "derived" / "functional_metrics.json"
    probe_manifest_path = package / "raw" / "probe_manifest.json"
    status_path = package / "report" / "status.json"
    metrics = _read_json(metrics_path) if metrics_path.is_file() else {}
    probe_manifest = _read_json(probe_manifest_path) if probe_manifest_path.is_file() else {}
    status = _read_json(status_path) if status_path.is_file() else {}
    stage = str(_nested(manifest, "split_identity", "stage") or "unknown").lower()
    execution_status = str(manifest.get("execution_status") or status.get("status") or "UNKNOWN")
    current_schema = validation.get("validator_status") == "CURRENT_VALID"
    claims_permitted = bool(stage == "confirmatory" and execution_status == "OBSERVED" and current_schema)
    validation_status = VALIDATION_FAIL if validation_errors else VALIDATION_PASS
    eligibility, reasons = _eligible_state(
        stage=stage,
        execution_status=execution_status,
        claims_permitted=claims_permitted,
        validation_status=validation_status,
    )
    if validation and not current_schema:
        eligibility = "INELIGIBLE"
        reasons.append("LEGACY_E5_SCHEMA")
    p2 = _nested(metrics, "systems", "P2") or {}
    images = probe_manifest.get("images") or []
    all_digests_immutable = bool(images) and all(
        isinstance(row.get("image_digest"), str)
        and len(row["image_digest"]) == 71
        and row["image_digest"].startswith("sha256:")
        and row.get("image_reference", "").endswith("@" + row["image_digest"])
        for row in images
    )
    catalog = manifest.get("candidate_catalog") or {}
    indexes = manifest.get("embedding_indexes") or {}
    constraints = manifest.get("constraint_ranking_configuration") or {}
    systems = manifest.get("backend_system_versions") or {}
    h7f = {
        "conservative_success": _finite(p2.get("conservative_functional_success_rate")),
        "operational_adequacy": _finite(p2.get("operational_adequacy_rate")),
        "required_probe_not_defined_count": p2.get("required_probe_not_defined_count"),
        "execution_unavailable_count": p2.get("execution_unavailable_count"),
        "failed_probe_count": _nested(metrics, "probe_summary", "probes_failed"),
        "all_digests_immutable": all_digests_immutable,
        "functional_execution_coverage": _finite(p2.get("functional_execution_coverage")),
        "catalog_underclaim_count": p2.get("catalog_underclaim_count"),
    }
    return EvidenceCandidate(
        requirement_id="image_functional",
        evidence_class="E5",
        experiment_id="E5_IMAGE_FUNCTIONAL",
        package_path=package,
        manifest_path=manifest_path,
        manifest_sha256=file_sha256(manifest_path),
        schema_version=manifest.get("schema_version"),
        stage=stage,
        execution_status=execution_status,
        validation_status=validation_status,
        claims_permitted=claims_permitted,
        claim_eligibility=eligibility,
        metrics={"H7F": h7f},
        tests={"criterion": "EXACT_ALL_REQUIRED_PROBES", "validator": validation},
        semantic_provenance={
            "p1.backend_version": systems.get("P1"),
            "p2.pipeline_version": systems.get("P2"),
            "catalog.version": catalog.get("catalog_version"),
            "catalog.file_sha256": catalog.get("catalog_sha256"),
            "corpus.sha256": catalog.get("corpus_sha256"),
            "indexes.dense.sha256": indexes.get("dense_index_sha256"),
            "indexes.sparse.sha256": indexes.get("sparse_index_sha256"),
            "indexes.hybrid.sha256": indexes.get("hybrid_index_sha256"),
            "constraints.evaluator_version": constraints.get("evaluator_version"),
            "constraints.ranker_version": constraints.get("ranker_version"),
        },
        provenance={
            "git_revision": manifest.get("git_revision"),
            "environment": manifest.get("environment_identity"),
            "dataset": manifest.get("dataset_identity"),
            "random_seeds": manifest.get("random_seeds"),
        },
        metadata={
            "environment": manifest.get("environment_identity") or {},
            "image_count": status.get("total_images"),
            "probe_count": status.get("total_probes"),
            "catalog_underclaim_count": p2.get("catalog_underclaim_count"),
            "required_probe_not_defined_count": p2.get("required_probe_not_defined_count"),
        },
        artifacts=[
            _artifact(manifest_path, package),
            *([_artifact(metrics_path, package)] if metrics_path.is_file() else []),
            *([_artifact(probe_manifest_path, package)] if probe_manifest_path.is_file() else []),
            *([_artifact(status_path, package)] if status_path.is_file() else []),
        ],
        reason_codes=reasons,
        validation_error="; ".join(validation_errors) or None,
    )


def _verify_sha256s(package: Path) -> None:
    sums = package / "SHA256SUMS"
    if not sums.is_file():
        raise ResearchAnalysisError("sealed evidence package lacks SHA256SUMS")
    for number, line in enumerate(sums.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise ResearchAnalysisError(f"SHA256SUMS:{number}: malformed line")
        expected, relative = parts
        path = package / relative.strip()
        try:
            path.resolve().relative_to(package.resolve())
        except ValueError as exc:
            raise ResearchAnalysisError("SHA256SUMS path escapes package") from exc
        if not path.is_file() or file_sha256(path) != expected:
            raise ResearchAnalysisError(f"SHA256SUMS mismatch: {relative.strip()}")


def _adapt_image_storage(package: Path, evidence_path: Path) -> EvidenceCandidate:
    validation_errors: list[str] = []
    evidence: dict[str, Any] = {}
    try:
        evidence = _read_json(evidence_path)
        validate_storage_evidence(evidence)
        _verify_sha256s(package)
    except Exception as exc:
        validation_errors.append(str(exc))
    stage = str(evidence.get("split_stage") or "unknown").lower()
    execution_status = str(evidence.get("execution_status") or "UNKNOWN")
    claims_permitted = bool(evidence.get("claims_permitted"))
    validation_status = VALIDATION_FAIL if validation_errors else VALIDATION_PASS
    eligibility, reasons = _eligible_state(
        stage=stage,
        execution_status=execution_status,
        claims_permitted=claims_permitted,
        validation_status=validation_status,
    )
    prefixes = evidence.get("prefixes") or []
    all_nonexpanding = bool(prefixes) and all(
        isinstance(row.get("unique_layer_bytes"), int)
        and isinstance(row.get("naive_logical_bytes"), int)
        and row["unique_layer_bytes"] <= row["naive_logical_bytes"]
        for row in prefixes
    )
    final_savings = (
        prefixes[-1]["naive_logical_bytes"] - prefixes[-1]["unique_layer_bytes"]
        if prefixes
        and isinstance(prefixes[-1].get("naive_logical_bytes"), int)
        and isinstance(prefixes[-1].get("unique_layer_bytes"), int)
        else None
    )
    marginal_growth: list[dict[str, Any]] = []
    for index, row in enumerate(prefixes):
        prior = prefixes[index - 1] if index else None
        marginal_growth.append(
            {
                "prefix_size": row.get("prefix_size"),
                "naive_bytes_added": row.get("naive_logical_bytes") if prior is None else row.get("naive_logical_bytes") - prior.get("naive_logical_bytes"),
                "unique_layer_bytes_added": row.get("unique_layer_bytes") if prior is None else row.get("unique_layer_bytes") - prior.get("unique_layer_bytes"),
            }
        )
    catalog = evidence.get("catalog") or {}
    systems = _nested(evidence, "provenance", "backend_system_versions") or {}
    h7 = {
        "all_prefixes_nonexpanding": all_nonexpanding,
        "final_savings_bytes": final_savings,
        "final_savings_ratio": (
            final_savings / prefixes[-1]["naive_logical_bytes"]
            if final_savings is not None and prefixes[-1]["naive_logical_bytes"] > 0
            else None
        ),
        "prefix_order_valid": not validation_errors and bool(prefixes),
        "prefixes": prefixes,
        "marginal_growth": marginal_growth,
    }
    return EvidenceCandidate(
        requirement_id="image_storage",
        evidence_class="E5",
        experiment_id="E5_IMAGE_STORAGE",
        package_path=package,
        manifest_path=evidence_path,
        manifest_sha256=file_sha256(evidence_path),
        schema_version=evidence.get("schema_version"),
        stage=stage,
        execution_status=execution_status,
        validation_status=validation_status,
        claims_permitted=claims_permitted,
        claim_eligibility=eligibility,
        metrics={"H7": h7},
        tests={"criterion": "EXACT_ORDERED_PREFIX_STORAGE"},
        semantic_provenance={
            "catalog.version": catalog.get("version"),
            "catalog.file_sha256": catalog.get("file_sha256"),
            "p2.pipeline_version": systems.get("P2"),
        },
        provenance=evidence.get("provenance") or {},
        metadata={
            "environment": evidence.get("platform") or {},
            "measurement_method": evidence.get("measurement_method"),
            "catalog_prefix_count": len(prefixes),
        },
        artifacts=[_artifact(evidence_path, package)],
        reason_codes=reasons,
        validation_error="; ".join(validation_errors) or None,
    )


def _invalid_candidate(
    package: Path, requirement_id: str, evidence_class: str, error: Exception
) -> EvidenceCandidate:
    manifest_path = package / "manifest.json"
    if not manifest_path.is_file():
        alternatives = sorted(package.glob("**/analysis-manifest.json"))
        manifest_path = alternatives[0] if alternatives else package
    sha = file_sha256(manifest_path) if manifest_path.is_file() else "0" * 64
    return EvidenceCandidate(
        requirement_id=requirement_id,
        evidence_class=evidence_class,
        experiment_id=evidence_class,
        package_path=package,
        manifest_path=manifest_path,
        manifest_sha256=sha,
        schema_version=None,
        stage="unknown",
        execution_status="INVALID",
        validation_status=VALIDATION_FAIL,
        claims_permitted=False,
        claim_eligibility="INELIGIBLE",
        reason_codes=["EVIDENCE_VALIDATION_FAILED"],
        validation_error=str(error),
    )


def discover_evidence(
    results_root: Path,
    *,
    registry: Mapping[str, Any] | None = None,
    freeze: Mapping[str, Any] | None = None,
    p3_threshold: Mapping[str, Any] | None = None,
) -> list[EvidenceCandidate]:
    """Discover known package schemas and retain ineligible evidence in inventory."""

    registry = dict(registry or load_claim_registry())
    freeze = dict(freeze or {})
    candidates: list[EvidenceCandidate] = []
    e1 = results_root / "E1"
    if e1.is_dir():
        for package in sorted(path for path in e1.iterdir() if path.is_dir()):
            if not (package / "raw" / "offline-run-provenance.json").is_file():
                continue
            try:
                candidates.extend(
                    _adapt_offline_package(package, freeze=freeze, threshold=p3_threshold)
                )
            except Exception as exc:
                candidates.extend(
                    _invalid_candidate(package, requirement, evidence_class, exc)
                    for requirement, evidence_class in (
                        ("offline_recommendation", "E1"),
                        ("natural_language_robustness", "E2"),
                    )
                )
    e3 = results_root / "E3"
    if e3.is_dir():
        for package in sorted(path for path in e3.iterdir() if path.is_dir()):
            manifest = package / "manifest.json"
            if not manifest.is_file():
                continue
            try:
                if _read_json(manifest).get("schema_version") == "protocol-v5-user-study-provenance-v1.0.0":
                    candidates.append(_adapt_user_study_package(package))
            except Exception as exc:
                candidates.append(_invalid_candidate(package, "user_study", "E3", exc))
    e4 = results_root / "E4"
    if e4.is_dir():
        for package in sorted(path for path in e4.iterdir() if path.is_dir()):
            manifest_path = package / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                schema = _read_json(manifest_path).get("schema_version")
                if schema == "protocol-v5-resource-efficiency-analysis-package-v1.0.0":
                    candidates.append(_adapt_resource_analysis(package))
                elif schema == "protocol-v5-resource-efficiency-raw-package-v1.0.0":
                    candidates.append(_adapt_resource_raw(package))
            except Exception as exc:
                candidates.append(_invalid_candidate(package, "resource_efficiency", "E4", exc))
    e5 = results_root / "E5"
    if e5.is_dir():
        for package in sorted(path for path in e5.iterdir() if path.is_dir()):
            functional = package / "derived" / "functional_metrics.json"
            storage = package / "derived" / "storage_metrics.json"
            if functional.is_file():
                try:
                    candidates.append(_adapt_image_functional(package))
                except Exception as exc:
                    candidates.append(_invalid_candidate(package, "image_functional", "E5", exc))
            if storage.is_file():
                try:
                    candidates.append(_adapt_image_storage(package, storage))
                except Exception as exc:
                    candidates.append(_invalid_candidate(package, "image_storage", "E5", exc))
    return sorted(candidates, key=lambda row: (row.requirement_id, str(row.package_path)))


def select_evidence(
    candidates: Sequence[EvidenceCandidate],
    registry: Mapping[str, Any],
    *,
    selection: Mapping[str, Any] | None = None,
    repository_root: Path | None = None,
) -> tuple[dict[str, EvidenceCandidate], dict[str, Any], set[str]]:
    """Choose only eligible confirmatory evidence, failing closed on stale locks."""

    repository_root = (repository_root or Path.cwd()).resolve()
    requirements = {row["id"]: row for row in registry["evidence_requirements"]}
    requested = dict((selection or {}).get("selections") or {})
    unknown = sorted(set(requested) - set(requirements))
    selected: dict[str, EvidenceCandidate] = {}
    rows: list[dict[str, Any]] = []
    fatal_requirements: set[str] = set()
    global_errors = [f"UNKNOWN_SELECTION_REQUIREMENT:{item}" for item in unknown]
    by_requirement: dict[str, list[EvidenceCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_requirement[candidate.requirement_id].append(candidate)

    for requirement_id, requirement in requirements.items():
        all_candidates = by_requirement.get(requirement_id, [])
        accepted_schemas = set(requirement["accepted_schema_versions"])
        eligible = [
            candidate
            for candidate in all_candidates
            if candidate.eligible
            and candidate.validation_status == VALIDATION_PASS
            and candidate.schema_version in accepted_schemas
        ]
        locked = requested.get(requirement_id)
        mode = "NONE"
        reason_codes: list[str] = []
        chosen: EvidenceCandidate | None = None
        if locked is not None:
            locked_path = Path(str(locked["package_path"]))
            if not locked_path.is_absolute():
                locked_path = repository_root / locked_path
            matches = [
                candidate
                for candidate in all_candidates
                if candidate.package_path.resolve() == locked_path.resolve()
            ]
            mode = "LOCKFILE"
            if len(matches) != 1:
                reason_codes.append("SELECTION_TARGET_NOT_DISCOVERED")
                fatal_requirements.add(requirement_id)
            else:
                target = matches[0]
                if target.manifest_sha256 != locked["manifest_sha256"]:
                    reason_codes.append("SELECTION_CHECKSUM_MISMATCH")
                    fatal_requirements.add(requirement_id)
                elif target not in eligible:
                    reason_codes.append("SELECTION_TARGET_INELIGIBLE")
                    fatal_requirements.add(requirement_id)
                else:
                    chosen = target
        elif len(eligible) == 1:
            chosen = eligible[0]
            mode = "AUTO_SINGLE_ELIGIBLE"
        elif len(eligible) > 1:
            mode = "AMBIGUOUS"
            reason_codes.append("MULTIPLE_ELIGIBLE_PACKAGES_REQUIRE_LOCK")
        else:
            reason_codes.append("NO_ELIGIBLE_CONFIRMATORY_EVIDENCE")
            inherited = sorted({code for candidate in all_candidates for code in candidate.reason_codes})
            reason_codes.extend(inherited)
            if requirement_id == "p2_p3":
                reason_codes.append("P3_NOT_RETAINED_OR_NOT_PRESENT")

        invalid_confirmatory = [
            candidate
            for candidate in all_candidates
            if candidate.stage == "confirmatory" and candidate.validation_status == VALIDATION_FAIL
        ]
        if invalid_confirmatory:
            fatal_requirements.add(requirement_id)
            reason_codes.append("CONFIRMATORY_EVIDENCE_INVALID")
            chosen = None
        if chosen is not None:
            selected[requirement_id] = chosen
        rows.append(
            {
                "requirement_id": requirement_id,
                "evidence_class": requirement["evidence_class"],
                "accepted_schema_versions": requirement["accepted_schema_versions"],
                "candidate_count": len(all_candidates),
                "eligible_candidate_count": len(eligible),
                "selection_mode": mode,
                "selected_package": str(chosen.package_path.resolve()) if chosen else None,
                "selected_manifest_sha256": chosen.manifest_sha256 if chosen else None,
                "reason_codes": sorted(set(reason_codes)),
                "candidate_packages": [str(item.package_path.resolve()) for item in all_candidates],
            }
        )
    return selected, {
        "schema_version": "protocol-v5-evidence-selection-result-v1.0.0",
        "requirements": rows,
        "global_errors": global_errors,
        "selection_lock_present": selection is not None,
    }, fatal_requirements


def check_provenance(
    selected: Mapping[str, EvidenceCandidate],
    registry: Mapping[str, Any],
    freeze: Mapping[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    """Check semantic identities against the freeze and disclose environments."""

    requirements = {row["id"]: row for row in registry["evidence_requirements"]}
    comparisons: list[dict[str, Any]] = []
    blocked: set[str] = set()
    for requirement_id, candidate in selected.items():
        for field in requirements[requirement_id]["semantic_provenance"]:
            key = field["key"]
            observed = candidate.semantic_provenance.get(key)
            try:
                expected = json_pointer_get(freeze, field["freeze_pointer"])
            except KeyError:
                expected = None
            if observed is None or expected is None:
                status = "MISSING"
                blocked.add(requirement_id)
            elif observed != expected:
                status = "MISMATCH"
                blocked.add(requirement_id)
            else:
                status = "MATCH"
            comparisons.append(
                {
                    "scope": "FREEZE",
                    "requirement_id": requirement_id,
                    "semantic_key": key,
                    "digest_namespace": SEMANTIC_DIGEST_KEYS.get(key),
                    "freeze_pointer": field["freeze_pointer"],
                    "expected": expected,
                    "observed": observed,
                    "status": status,
                    "source_manifest": str(candidate.manifest_path.resolve()),
                }
            )

    values_by_key: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    for requirement_id, candidate in selected.items():
        for key, value in candidate.semantic_provenance.items():
            if value is not None:
                values_by_key[key].append((requirement_id, value))
    for key, values in sorted(values_by_key.items()):
        if len(values) < 2:
            continue
        distinct = {canonical_json_sha256(value) for _, value in values}
        status = "MATCH" if len(distinct) == 1 else "MISMATCH"
        if status == "MISMATCH":
            blocked.update(requirement_id for requirement_id, _ in values)
        comparisons.append(
            {
                "scope": "CROSS_EXPERIMENT",
                "requirement_id": [requirement_id for requirement_id, _ in values],
                "semantic_key": key,
                "digest_namespace": SEMANTIC_DIGEST_KEYS.get(key),
                "freeze_pointer": None,
                "expected": values[0][1],
                "observed": {requirement_id: value for requirement_id, value in values},
                "status": status,
                "source_manifest": [str(selected[item].manifest_path.resolve()) for item, _ in values],
            }
        )

    disclosures: list[dict[str, Any]] = []
    git_revisions = {
        requirement_id: candidate.provenance.get("git_revision")
        for requirement_id, candidate in selected.items()
        if candidate.provenance.get("git_revision") is not None
    }
    if git_revisions:
        disclosures.append(
            {
                "type": "GIT_REVISION",
                "status": "MATCH" if len(set(git_revisions.values())) == 1 else "DISCLOSED_DIFFERENCE",
                "values": git_revisions,
                "blocking": False,
            }
        )
    environments = {
        requirement_id: candidate.provenance.get("environment")
        or candidate.provenance.get("source_environment")
        or candidate.provenance.get("runtime")
        for requirement_id, candidate in selected.items()
    }
    environments = {key: value for key, value in environments.items() if value is not None}
    if environments:
        identities = {canonical_json_sha256(value) for value in environments.values()}
        disclosures.append(
            {
                "type": "ENVIRONMENT_IDENTITY",
                "status": "MATCH" if len(identities) == 1 else "DISCLOSED_DIFFERENCE",
                "values": environments,
                "blocking": False,
            }
        )
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "semantic_comparisons": comparisons,
        "disclosures": disclosures,
        "blocked_requirements": sorted(blocked),
        "semantic_status": "PASS" if not blocked else "FAIL",
    }, blocked


def _claims_for_requirement(registry: Mapping[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for claim in registry["claims"]:
        for requirement in claim["required_evidence"]:
            result[requirement].append(claim["id"])
    return {key: sorted(value) for key, value in result.items()}


def generate_threats(
    *,
    registry: Mapping[str, Any],
    candidates: Sequence[EvidenceCandidate],
    selected: Mapping[str, EvidenceCandidate],
    selection_report: Mapping[str, Any],
    provenance_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Generate threats only when a registry or evidence metadata trigger fires."""

    claims_by_requirement = _claims_for_requirement(registry)
    threats: list[dict[str, Any]] = []
    candidate_positions = {id(candidate): index for index, candidate in enumerate(candidates)}

    def add(
        *,
        code: str,
        category: str,
        severity: str,
        requirements: Sequence[str],
        observed: Any,
        source_artifact: str,
        source_pointer: str,
        statement: str,
    ) -> None:
        claim_ids = sorted(
            {
                claim
                for requirement in requirements
                for claim in claims_by_requirement.get(requirement, [])
            }
        )
        threats.append(
            {
                "threat_id": f"TV-{len(threats) + 1:03d}",
                "code": code,
                "category": category,
                "severity": severity,
                "affected_claims": claim_ids,
                "affected_evidence_requirements": sorted(set(requirements)),
                "observed_value": observed,
                "source_artifact": source_artifact,
                "source_pointer": source_pointer,
                "statement": statement,
            }
        )

    selection_rows = selection_report.get("requirements") or []
    for index, row in enumerate(selection_rows):
        requirement = str(row["requirement_id"])
        if row.get("selected_package") is None:
            add(
                code="REQUIRED_EVIDENCE_UNAVAILABLE",
                category="statistical_conclusion",
                severity="blocking",
                requirements=[requirement],
                observed={
                    "candidate_count": row.get("candidate_count"),
                    "eligible_candidate_count": row.get("eligible_candidate_count"),
                    "reason_codes": row.get("reason_codes"),
                },
                source_artifact="derived/evidence-selection.json",
                source_pointer=f"/requirements/{index}",
                statement="The required confirmatory evidence was unavailable or unselected, so the linked claim cannot be decided.",
            )

    by_requirement: dict[str, list[EvidenceCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_requirement[candidate.requirement_id].append(candidate)
    for requirement, rows in sorted(by_requirement.items()):
        development = [row for row in rows if row.stage != "confirmatory"]
        if development:
            add(
                code="NON_CONFIRMATORY_EVIDENCE_PRESENT",
                category="benchmark_contamination",
                severity="boundary",
                requirements=[requirement],
                observed={
                    "count": len(development),
                    "stages": sorted({row.stage for row in development}),
                    "manifests": [str(row.manifest_path.resolve()) for row in development],
                },
                source_artifact="derived/evidence-inventory.json",
                source_pointer="/candidates",
                statement="Development, historical, or unknown-stage packages are inventoried but excluded from confirmatory claim decisions.",
            )

    for requirement, candidate in selected.items():
        inventory_pointer = f"/candidates/{candidate_positions[id(candidate)]}"
        provenance = candidate.provenance
        dirty = provenance.get("git_dirty")
        if dirty is None and isinstance(provenance.get("environment"), Mapping):
            dirty = _nested(provenance, "environment", "git_info", "git_dirty")
        if dirty is True:
            add(
                code="DIRTY_GIT_EVIDENCE_ENVIRONMENT",
                category="internal",
                severity="disclosure",
                requirements=[requirement],
                observed=True,
                source_artifact="derived/evidence-inventory.json",
                source_pointer=inventory_pointer + "/provenance/git_dirty",
                statement="The observed package records a dirty working tree; semantic identities passed separately, but unrecorded local changes remain a limitation.",
            )
        family_n = candidate.metadata.get("family_n")
        if isinstance(family_n, int) and family_n < 10:
            add(
                code="SMALL_INDEPENDENT_FAMILY_COUNT",
                category="statistical_conclusion",
                severity="blocking" if family_n < 2 else "high",
                requirements=[requirement],
                observed=family_n,
                source_artifact="derived/evidence-inventory.json",
                source_pointer=inventory_pointer + "/metadata/family_n",
                statement="The number of independent workload families limits or prevents family-level inference.",
            )

        if requirement == "offline_recommendation":
            add(
                code="JOINT_ACCEPT_COMPOSITE_CONSTRUCT",
                category="construct",
                severity="boundary",
                requirements=[requirement],
                observed="joint_accept_at_1",
                source_artifact=str(REGISTRY_PATH.resolve()),
                source_pointer="/claims/0/metrics",
                statement="JointAccept@1 operationalizes acceptability jointly across the frozen profile, image, and constraint criteria rather than every possible deployment objective.",
            )
        if requirement == "natural_language_robustness":
            add(
                code="REVIEWED_EQUIVALENCE_CONSTRUCT",
                category="construct",
                severity="boundary",
                requirements=[requirement],
                observed=candidate.metadata.get("variant_n"),
                source_artifact="derived/evidence-inventory.json",
                source_pointer=inventory_pointer + "/metadata/variant_n",
                statement="Surface-form robustness generalizes only to variants recorded as reviewed-equivalent in the frozen benchmark.",
            )
        if requirement == "user_study":
            diagnostics = candidate.metadata.get("design_diagnostics") or {}
            if diagnostics.get("condition_order_counts") or diagnostics.get("period_counts"):
                add(
                    code="CROSSOVER_LEARNING_ORDER_EFFECTS",
                    category="human_study_learning_order",
                    severity="boundary",
                    requirements=[requirement],
                    observed={
                        "condition_order_counts": diagnostics.get("condition_order_counts"),
                        "period_counts": diagnostics.get("period_counts"),
                        "counterbalance_cell_counts": diagnostics.get("counterbalance_cell_counts"),
                    },
                    source_artifact="derived/evidence-inventory.json",
                    source_pointer=inventory_pointer + "/metadata/design_diagnostics",
                    statement="The crossover design can retain learning or carryover effects; frozen period/order terms and balance diagnostics bound but do not eliminate them.",
                )
            missing = [
                row
                for row in candidate.metadata.get("missingness") or []
                if isinstance(row.get("missing_count"), int) and row["missing_count"] > 0
            ]
            if missing:
                add(
                    code="HUMAN_OUTCOME_MISSINGNESS",
                    category="statistical_conclusion",
                    severity="high",
                    requirements=[requirement],
                    observed=missing,
                    source_artifact="derived/evidence-inventory.json",
                    source_pointer=inventory_pointer + "/metadata/missingness",
                    statement="Recorded missing outcomes reduce the applicable E3 estimand population and may introduce differential missingness.",
                )
            methods = {
                "selection": candidate.metrics.get("H3", {}).get("selection_method"),
                "decision_time": candidate.metrics.get("H3", {}).get("time_method"),
            }
            if any(value and "paired" in str(value) for value in methods.values()):
                add(
                    code="PREDECLARED_MODEL_FALLBACK_USED",
                    category="statistical_conclusion",
                    severity="disclosure",
                    requirements=[requirement],
                    observed=methods,
                    source_artifact="derived/evidence-inventory.json",
                    source_pointer=inventory_pointer + "/metrics/H3",
                    statement="At least one E3 endpoint used its predeclared paired fallback rather than the requested clustered model.",
                )
            add(
                code="USER_STUDY_POPULATION_BOUNDARY",
                category="external",
                severity="boundary",
                requirements=[requirement],
                observed={
                    "participant_target": candidate.metadata.get("participant_target"),
                    "completed_participants": candidate.metadata.get("completed_participants"),
                },
                source_artifact="derived/evidence-inventory.json",
                source_pointer=inventory_pointer + "/metadata/participant_target",
                statement="E3 findings generalize only to the recruited participant population and frozen task set.",
            )
        if requirement == "resource_efficiency":
            add(
                code="NO_SUCCESS_NONINFERIORITY_MARGIN",
                category="construct",
                severity="boundary",
                requirements=[requirement],
                observed=candidate.metadata.get("success_noninferiority_margin"),
                source_artifact="derived/evidence-inventory.json",
                source_pointer=inventory_pointer + "/metadata/success_noninferiority_margin",
                statement="The frozen E4 contract has no formal success noninferiority margin; preservation is limited to the declared Pareto rule.",
            )
            add(
                code="SINGLE_CLUSTER_GENERALIZATION",
                category="single_cluster_generalization",
                severity="high",
                requirements=[requirement],
                observed={
                    "cluster_identity": candidate.metadata.get("cluster_identity"),
                    "scope": "single-pod sequential Kubernetes evidence",
                },
                source_artifact="derived/evidence-inventory.json",
                source_pointer=inventory_pointer + "/metadata/cluster_identity",
                statement="E4 measurements are bounded to the recorded cluster and sequential single-pod execution; simulated packing is not observed concurrency.",
            )
        if requirement in {"image_functional", "image_storage"}:
            add(
                code="IMAGE_PLATFORM_DEPENDENCE",
                category="image_platform_dependence",
                severity="high",
                requirements=[requirement],
                observed=candidate.metadata.get("environment"),
                source_artifact="derived/evidence-inventory.json",
                source_pointer=inventory_pointer + "/metadata/environment",
                statement="Image correctness or storage results apply to the recorded runtime, operating system, architecture, and immutable image digests.",
            )
        if requirement == "p2_p3":
            add(
                code="P3_SERVICE_AND_COST_DEPENDENCE",
                category="external",
                severity="high",
                requirements=[requirement],
                observed=candidate.semantic_provenance.get("p3.reranker_version"),
                source_artifact="derived/evidence-inventory.json",
                source_pointer=inventory_pointer + "/semantic_provenance/p3.reranker_version",
                statement="P3 quality and overhead apply only to the retained reranker, provider, pricing, and runtime configuration.",
            )

    for index, disclosure in enumerate(provenance_report.get("disclosures") or []):
        if disclosure.get("status") == "DISCLOSED_DIFFERENCE":
            add(
                code=f"{disclosure['type']}_DIFFERENCE",
                category="internal",
                severity="disclosure",
                requirements=list(selected),
                observed=disclosure.get("values"),
                source_artifact="derived/provenance-consistency.json",
                source_pointer=f"/disclosures/{index}",
                statement="The selected experiments record different non-semantic execution identities; the difference is disclosed and does not override semantic freeze checks.",
            )

    return {
        "schema_version": THREATS_SCHEMA_VERSION,
        "generation_policy": "metadata_triggered_only",
        "categories": sorted({row["category"] for row in threats}),
        "threats": threats,
    }


def evaluate_claims(
    *,
    registry: Mapping[str, Any],
    selected: Mapping[str, EvidenceCandidate],
    selection_report: Mapping[str, Any],
    provenance_blocked: set[str] | None = None,
    fatal_requirements: set[str] | None = None,
    threats: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    provenance_blocked = set(provenance_blocked or set())
    fatal_requirements = set(fatal_requirements or set())
    rq = {row["id"]: row for row in registry["research_questions"]}
    selection_rows = {
        row["requirement_id"]: row for row in selection_report.get("requirements") or []
    }
    threat_rows = (threats or {}).get("threats") or []
    evaluated: list[dict[str, Any]] = []
    for claim in registry["claims"]:
        claim_id = claim["id"]
        requirements = claim["required_evidence"]
        reasons: list[str] = []
        evidence_rows: list[dict[str, Any]] = []
        evidence_status: list[dict[str, Any]] = []
        metrics: dict[str, Any] = {}
        observed_tests: list[dict[str, Any]] = []
        for requirement in requirements:
            candidate = selected.get(requirement)
            selection_row = selection_rows.get(requirement) or {}
            if candidate is None:
                reasons.extend(selection_row.get("reason_codes") or ["REQUIRED_EVIDENCE_MISSING"])
                evidence_status.append(
                    {
                        "requirement_id": requirement,
                        "stage": None,
                        "execution_status": None,
                        "validation_status": None,
                        "claim_eligibility": "UNAVAILABLE",
                        "selection_mode": selection_row.get("selection_mode"),
                    }
                )
                continue
            evidence_rows.append(
                {
                    "requirement_id": requirement,
                    "package_path": str(candidate.package_path.resolve()),
                    "manifest_path": str(candidate.manifest_path.resolve()),
                    "manifest_sha256": candidate.manifest_sha256,
                    "artifacts": candidate.artifacts,
                }
            )
            evidence_status.append(
                {
                    "requirement_id": requirement,
                    "stage": candidate.stage,
                    "execution_status": candidate.execution_status,
                    "validation_status": candidate.validation_status,
                    "claim_eligibility": candidate.claim_eligibility,
                    "selection_mode": selection_row.get("selection_mode"),
                }
            )
            if claim_id not in candidate.metrics:
                reasons.append("CLAIM_METRICS_UNAVAILABLE")
            else:
                metrics.update(candidate.metrics[claim_id])
            observed_tests.append({"requirement_id": requirement, **candidate.tests})
            if requirement in provenance_blocked:
                reasons.append("SEMANTIC_PROVENANCE_FAILED")
            if requirement in fatal_requirements:
                reasons.append("INVALID_SELECTED_EVIDENCE")

        condition_result, condition_rows = evaluate_conditions(
            claim["support_all_of"], {"metrics": metrics}
        )
        if condition_result is None:
            reasons.append("REQUIRED_METRIC_OR_TEST_UNAVAILABLE")
        if reasons:
            status = "NOT_EXECUTED"
        elif condition_result is True:
            status = "SUPPORTED"
        else:
            status = "NOT_SUPPORTED"
        linked_threats = [row for row in threat_rows if claim_id in row.get("affected_claims", [])]
        value = {
            "schema_version": EVALUATED_CLAIM_SCHEMA_VERSION,
            "claim_id": claim_id,
            "research_question": dict(rq[claim["research_question"]]),
            "hypothesis": claim["hypothesis"],
            "experiments": list(claim["experiments"]),
            "metrics": [dict(row) for row in claim["metrics"]],
            "statistical_tests": [
                {**dict(row), "observed_evidence_tests": observed_tests}
                for row in claim["statistical_tests"]
            ],
            "evidence": evidence_rows,
            "evidence_status": evidence_status,
            "result": {
                "normalized_metrics": metrics,
                "decision_checks": condition_rows,
                "all_support_conditions_passed": condition_result,
            },
            "claim_status": status,
            "claimable": status in {"SUPPORTED", "NOT_SUPPORTED"},
            "reason_codes": sorted(set(reasons)),
            "limitations": linked_threats,
        }
        validate_evaluated_claim(value)
        evaluated.append(value)
    return evaluated


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"


def _write_exclusive(path: Path, content: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = content.encode("utf-8") if isinstance(content, str) else content
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def _compact(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def _claim_table_rows(claims: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for claim in claims:
        evidence = claim.get("evidence") or []
        artifacts = [
            artifact
            for item in evidence
            for artifact in item.get("artifacts") or []
        ]
        evidence_status = claim.get("evidence_status") or []
        normalized = _nested(claim, "result", "normalized_metrics") or {}
        table_result = {
            key: value
            for key, value in normalized.items()
            if not isinstance(value, (dict, list))
        }
        rows.append(
            {
                "claim": str(claim["claim_id"]),
                "research_question": str(_nested(claim, "research_question", "id") or ""),
                "hypothesis": str(claim["hypothesis"]),
                "experiments": "; ".join(claim.get("experiments") or []),
                "metrics": "; ".join(row["label"] for row in claim.get("metrics") or []),
                "statistical_test": "; ".join(row["method"] for row in claim.get("statistical_tests") or []),
                "evidence_status": "; ".join(
                    f"{row['requirement_id']}:{row.get('stage') or 'missing'}/{row.get('execution_status') or 'missing'}/{row.get('validation_status') or 'missing'}"
                    for row in evidence_status
                ),
                "evidence_files": "; ".join(str(row.get("path")) for row in artifacts) or "N/A",
                "evidence_sha256": "; ".join(str(row.get("sha256")) for row in artifacts) or "N/A",
                "result": _compact(table_result),
                "status": str(claim["claim_status"]),
                "claimable": str(bool(claim["claimable"])).lower(),
                "reason_codes": "; ".join(claim.get("reason_codes") or []) or "N/A",
                "limitations": "; ".join(row["code"] for row in claim.get("limitations") or []) or "N/A",
            }
        )
    return rows


def _csv_text(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(fields), lineterminator="\n")
    writer.writeheader()
    writer.writerows({field: row.get(field, "") for field in fields} for row in rows)
    return buffer.getvalue()


def _markdown_table(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> str:
    def escape(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(fields) + " |",
        "| " + " | ".join("---" for _ in fields) + " |",
    ]
    lines.extend(
        "| " + " | ".join(escape(row.get(field, "")) for field in fields) + " |"
        for row in rows
    )
    return "\n".join(lines) + "\n"


def _latex_escape(value: Any) -> str:
    text = str(value)
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in text)


def _latex_table(rows: Sequence[Mapping[str, Any]]) -> str:
    fields = ("claim", "research_question", "result", "status", "reason_codes")
    lines = [
        r"\begin{tabular}{lllll}",
        r"\hline",
        " & ".join(_latex_escape(field) for field in fields) + r" \\",
        r"\hline",
    ]
    for row in rows:
        lines.append(" & ".join(_latex_escape(row[field]) for field in fields) + r" \\")
    lines.extend(
        [
            r"\hline",
            r"\end{tabular}",
            "% Repetitions and surface-form variants are not independent accuracy samples.",
            "% B0 ranking metrics are not applicable. NOT_EXECUTED values are never encoded as zero.",
        ]
    )
    return "\n".join(lines) + "\n"


def _threats_markdown(threats: Mapping[str, Any]) -> str:
    lines = ["# Protocol-v5 Threats to Validity", "", "Generated only from recorded registry or experiment metadata.", ""]
    for row in threats.get("threats") or []:
        lines.extend(
            [
                f"## {row['threat_id']} — {row['code']}",
                "",
                f"- Category: `{row['category']}`",
                f"- Severity: `{row['severity']}`",
                f"- Affected claims: `{', '.join(row['affected_claims']) or 'none'}`",
                f"- Source: `{row['source_artifact']}{row['source_pointer']}`",
                f"- Observed metadata: `{_compact(row['observed_value'])}`",
                "",
                row["statement"],
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _result_markdown(rows: Sequence[Mapping[str, Any]], package_status: str) -> str:
    fields = ("claim", "research_question", "result", "status", "reason_codes")
    return (
        "# Protocol-v5 Thesis Results\n\n"
        f"Analysis package status: `{package_status}`.\n\n"
        "`NOT_EXECUTED` is not a zero result. Workload family or participant is the independent unit as registered; repeated executions only characterize stability. B0 has no ranking metrics.\n\n"
        + _markdown_table(rows, fields)
    )


def _package_status(
    claims: Sequence[Mapping[str, Any]], *, fatal: bool
) -> str:
    if fatal:
        return "FAILED"
    required_not_executed = any(
        claim["claim_status"] == "NOT_EXECUTED" and claim["claim_id"] != "H8"
        for claim in claims
    )
    return "INCOMPLETE" if required_not_executed else "COMPLETE"


def _publish_package(
    *,
    output_root: Path,
    run_id: str,
    registry_path: Path,
    freeze_path: Path,
    selection_path: Path | None,
    inventory: Mapping[str, Any],
    selection_report: Mapping[str, Any],
    provenance_report: Mapping[str, Any],
    threats: Mapping[str, Any],
    claims: list[dict[str, Any]],
    package_status: str,
) -> Path:
    if not run_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in run_id):
        raise ResearchAnalysisError("run_id must contain only letters, digits, dot, underscore, or hyphen")
    output_root.mkdir(parents=True, exist_ok=True)
    final = output_root / run_id
    if final.exists():
        raise FileExistsError(f"analysis package already exists: {final}")
    staging = Path(tempfile.mkdtemp(prefix=f".{run_id}.staging-", dir=output_root))
    try:
        if package_status == "FAILED":
            for claim in claims:
                claim["claimable"] = False
        completeness = {
            "schema_version": "protocol-v5-claim-completeness-v1.0.0",
            "package_status": package_status,
            "required_claims": [row["claim_id"] for row in claims if row["claim_id"] != "H8"],
            "optional_claims": ["H8"],
            "counts": {
                status: sum(row["claim_status"] == status for row in claims)
                for status in ("SUPPORTED", "NOT_SUPPORTED", "NOT_EXECUTED")
            },
            "not_executed_required_claims": [
                row["claim_id"]
                for row in claims
                if row["claim_id"] != "H8" and row["claim_status"] == "NOT_EXECUTED"
            ],
            "all_required_claims_decided": all(
                row["claim_id"] == "H8" or row["claim_status"] != "NOT_EXECUTED"
                for row in claims
            ),
        }
        claim_payload = {
            "schema_version": "protocol-v5-evaluated-claim-registry-v1.0.0",
            "protocol_version": PROTOCOL_VERSION,
            "claims": claims,
        }
        rows = _claim_table_rows(claims)
        table_fields = tuple(rows[0]) if rows else ()
        payloads: dict[str, str] = {
            "derived/evidence-inventory.json": _json_text(inventory),
            "derived/evidence-selection.json": _json_text(selection_report),
            "derived/evidence-completeness.json": _json_text(completeness),
            "derived/provenance-consistency.json": _json_text(provenance_report),
            "derived/evaluated-claim-registry.json": _json_text(claim_payload),
            "report/threats-to-validity.json": _json_text(threats),
            "report/THREATS_TO_VALIDITY.md": _threats_markdown(threats),
            "tables/claim-matrix.json": _json_text({"rows": rows}),
            "tables/claim-matrix.csv": _csv_text(rows, table_fields),
            "tables/claim-matrix.md": _markdown_table(rows, table_fields),
            "tables/thesis-results.csv": _csv_text(rows, ("claim", "research_question", "result", "status", "reason_codes")),
            "tables/thesis-results.md": _result_markdown(rows, package_status),
            "tables/thesis-results.tex": _latex_table(rows),
            "status.json": _json_text(
                {
                    "schema_version": "protocol-v5-research-analysis-status-v1.0.0",
                    "run_id": run_id,
                    "status": package_status,
                    "thesis_claims_permitted": package_status != "FAILED",
                    "all_required_claims_decided": completeness["all_required_claims_decided"],
                    "claim_counts": completeness["counts"],
                }
            ),
        }
        written = [_write_exclusive(staging / relative, content) for relative, content in payloads.items()]
        output_checksums = {
            str(path.relative_to(staging)): file_sha256(path) for path in sorted(written)
        }
        input_artifacts: dict[str, str] = {}
        for candidate in inventory.get("candidates") or []:
            for artifact in candidate.get("artifacts") or []:
                if artifact.get("path") and artifact.get("sha256"):
                    input_artifacts[str(artifact["path"])] = str(artifact["sha256"])
        manifest = {
            "schema_version": ANALYSIS_PACKAGE_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "run_id": run_id,
            "created_at_utc": _utc_now(),
            "status": package_status,
            "thesis_claims_permitted": package_status != "FAILED",
            "registry": {"path": str(registry_path.resolve()), "sha256": file_sha256(registry_path)},
            "freeze": {"path": str(freeze_path.resolve()), "sha256": file_sha256(freeze_path)},
            "selection": (
                {"path": str(selection_path.resolve()), "sha256": file_sha256(selection_path)}
                if selection_path is not None
                else None
            ),
            "analysis_environment": {
                **_git_identity(),
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "implementation_files": {
                    "evaluation_v5/analysis/research_analysis.py": file_sha256(Path(__file__)),
                    "evaluation_v5/analysis/research_contracts.py": file_sha256(Path(__file__).with_name("research_contracts.py")),
                },
            },
            "claim_counts": completeness["counts"],
            "input_artifact_sha256": dict(sorted(input_artifacts.items())),
            "output_checksums": output_checksums,
            "raw_evidence_modified": False,
            "backend_semantics_modified": False,
        }
        manifest_path = _write_exclusive(staging / "manifest.json", _json_text(manifest))
        sums = {
            **output_checksums,
            "manifest.json": file_sha256(manifest_path),
        }
        _write_exclusive(
            staging / "SHA256SUMS",
            "".join(f"{sha}  {relative}\n" for relative, sha in sorted(sums.items())),
        )
        os.rename(staging, final)
        return final
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def run_research_analysis(
    *,
    results_root: Path,
    output_root: Path,
    run_id: str,
    registry_path: Path = REGISTRY_PATH,
    freeze_path: Path,
    selection_path: Path | None = None,
    p3_threshold_path: Path | None = None,
) -> tuple[Path, str, int]:
    """Run discovery through publication and return path, status, and exit code."""

    registry = load_claim_registry(registry_path)
    bootstrap_errors: list[str] = []
    try:
        freeze = _read_json(freeze_path)
        if freeze.get("p3_gate") is None or freeze.get("systems") is None:
            raise ResearchAnalysisError("freeze lacks system or P3-gate identities")
    except Exception as exc:
        freeze = {}
        bootstrap_errors.append(f"FREEZE_INVALID:{exc}")
    threshold: Mapping[str, Any] | None = None
    if p3_threshold_path is not None:
        try:
            threshold = load_p3_threshold(p3_threshold_path)
        except Exception as exc:
            bootstrap_errors.append(f"P3_THRESHOLD_INVALID:{exc}")
    selection: Mapping[str, Any] | None = None
    if selection_path is not None:
        try:
            selection = load_selection(selection_path, registry_path=registry_path)
        except Exception as exc:
            bootstrap_errors.append(f"SELECTION_LOCK_INVALID:{exc}")
    candidates = discover_evidence(
        results_root,
        registry=registry,
        freeze=freeze,
        p3_threshold=threshold,
    )
    selected, selection_report, fatal_requirements = select_evidence(
        candidates,
        registry,
        selection=selection,
        repository_root=registry_path.resolve().parents[1],
    )
    if bootstrap_errors:
        selection_report["global_errors"].extend(bootstrap_errors)
        fatal_requirements.update(row["id"] for row in registry["evidence_requirements"])
    provenance_report, provenance_blocked = check_provenance(selected, registry, freeze)
    threats = generate_threats(
        registry=registry,
        candidates=candidates,
        selected=selected,
        selection_report=selection_report,
        provenance_report=provenance_report,
    )
    claims = evaluate_claims(
        registry=registry,
        selected=selected,
        selection_report=selection_report,
        provenance_blocked=provenance_blocked,
        fatal_requirements=fatal_requirements,
        threats=threats,
    )
    fatal = bool(
        bootstrap_errors
        or selection_report.get("global_errors")
        or fatal_requirements
        or provenance_blocked
    )
    status = _package_status(claims, fatal=fatal)
    inventory = {
        "schema_version": EVIDENCE_INVENTORY_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "results_root": str(results_root.resolve()),
        "candidate_count": len(candidates),
        "candidates": [candidate.to_dict() for candidate in candidates],
        "observations_are_copied": False,
    }
    package = _publish_package(
        output_root=output_root,
        run_id=run_id,
        registry_path=registry_path,
        freeze_path=freeze_path,
        selection_path=selection_path,
        inventory=inventory,
        selection_report=selection_report,
        provenance_report=provenance_report,
        threats=threats,
        claims=claims,
        package_status=status,
    )
    validate_research_analysis_package(package)
    exit_code = EXIT_FAILED if status == "FAILED" else EXIT_INCOMPLETE if status == "INCOMPLETE" else EXIT_SUCCESS
    return package, status, exit_code


def validate_research_analysis_package(package: Path) -> dict[str, Any]:
    package = package.resolve()
    _verify_sha256s(package)
    manifest = _read_json(package / "manifest.json")
    if manifest.get("schema_version") != ANALYSIS_PACKAGE_SCHEMA_VERSION:
        raise ResearchAnalysisError("unsupported research-analysis package schema")
    if manifest.get("protocol_version") != PROTOCOL_VERSION:
        raise ResearchAnalysisError("research-analysis protocol version mismatch")
    outputs = manifest.get("output_checksums")
    if not isinstance(outputs, Mapping) or not outputs:
        raise ResearchAnalysisError("research-analysis manifest lacks output checksums")
    for relative, expected in outputs.items():
        path = package / str(relative)
        try:
            path.resolve().relative_to(package)
        except ValueError as exc:
            raise ResearchAnalysisError("research-analysis output path escapes package") from exc
        if not path.is_file() or file_sha256(path) != expected:
            raise ResearchAnalysisError(f"research-analysis output checksum mismatch: {relative}")
    actual_files = {
        str(path.relative_to(package))
        for path in package.rglob("*")
        if path.is_file()
    }
    expected_files = set(outputs) | {"manifest.json", "SHA256SUMS"}
    if actual_files != expected_files:
        raise ResearchAnalysisError("research-analysis package has missing or unregistered files")
    inputs = manifest.get("input_artifact_sha256")
    if not isinstance(inputs, Mapping):
        raise ResearchAnalysisError("research-analysis manifest lacks input artifact identities")
    for source, expected in inputs.items():
        path = Path(str(source))
        if not path.is_file() or file_sha256(path) != expected:
            raise ResearchAnalysisError(
                f"research-analysis input artifact no longer validates: {source}"
            )
    registry_identity = manifest.get("registry") or {}
    registry_path = Path(str(registry_identity.get("path", "")))
    if not registry_path.is_file() or file_sha256(registry_path) != registry_identity.get("sha256"):
        raise ResearchAnalysisError("claim registry identity no longer validates")
    registry = load_claim_registry(registry_path)
    definitions = {row["id"]: row for row in registry["claims"]}
    claim_payload = _read_json(package / "derived" / "evaluated-claim-registry.json")
    claims = claim_payload.get("claims")
    if not isinstance(claims, list) or set(row.get("claim_id") for row in claims) != set(definitions):
        raise ResearchAnalysisError("evaluated claim registry is incomplete")
    for claim in claims:
        validate_evaluated_claim(claim)
        definition = definitions[claim["claim_id"]]
        decision, checks = evaluate_conditions(
            definition["support_all_of"],
            {"metrics": _nested(claim, "result", "normalized_metrics") or {}},
        )
        if checks != _nested(claim, "result", "decision_checks"):
            raise ResearchAnalysisError(f"{claim['claim_id']}: decision checks do not recompute")
        expected_status = (
            "NOT_EXECUTED"
            if claim.get("reason_codes") or decision is None
            else "SUPPORTED"
            if decision
            else "NOT_SUPPORTED"
        )
        if claim["claim_status"] != expected_status:
            raise ResearchAnalysisError(f"{claim['claim_id']}: claim status does not recompute")
        for evidence in claim.get("evidence") or []:
            for artifact in evidence.get("artifacts") or []:
                path = Path(str(artifact.get("path", "")))
                if not path.is_file() or file_sha256(path) != artifact.get("sha256"):
                    raise ResearchAnalysisError(
                        f"{claim['claim_id']}: source artifact identity no longer validates"
                    )
        if claim["claim_status"] != "NOT_EXECUTED":
            if any(
                row.get("stage") != "confirmatory"
                or row.get("validation_status") != VALIDATION_PASS
                or row.get("claim_eligibility") != "ELIGIBLE_CONFIRMATORY"
                for row in claim.get("evidence_status") or []
            ):
                raise ResearchAnalysisError(
                    f"{claim['claim_id']}: non-confirmatory evidence produced a decision"
                )
    status = _read_json(package / "status.json")
    if status.get("status") != manifest.get("status"):
        raise ResearchAnalysisError("package manifest/status disagree")
    if manifest.get("status") == "FAILED" and (
        manifest.get("thesis_claims_permitted") is not False
        or any(claim.get("claimable") for claim in claims)
    ):
        raise ResearchAnalysisError("FAILED package exposes claimable thesis results")
    completeness = _read_json(package / "derived" / "evidence-completeness.json")
    counts = {
        name: sum(claim["claim_status"] == name for claim in claims)
        for name in ("SUPPORTED", "NOT_SUPPORTED", "NOT_EXECUTED")
    }
    if counts != manifest.get("claim_counts") or counts != completeness.get("counts"):
        raise ResearchAnalysisError("claim counts disagree across package artifacts")
    return {
        "schema_version": ANALYSIS_PACKAGE_SCHEMA_VERSION,
        "status": "PASS",
        "package_status": manifest["status"],
        "claims_validated": len(claims),
        "files_validated": len(actual_files),
    }


def _default_run_id() -> str:
    return "research-analysis-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("discover", help="Discover and validate Protocol-v5 evidence candidates.")
    analyze = commands.add_parser("analyze", help="Generate an immutable unified research-analysis package.")
    validate = commands.add_parser("validate", help="Validate a generated research-analysis package.")
    for command in (discover, analyze):
        command.add_argument("--results-root", type=Path, default=Path("results_v5/protocol-v5.0.0"))
        command.add_argument("--registry", type=Path, default=REGISTRY_PATH)
        command.add_argument(
            "--freeze",
            type=Path,
            default=Path("results_v5/protocol-v5.0.0/freezes/frozen-configuration.json"),
        )
        command.add_argument("--p3-threshold", type=Path)
    discover.add_argument("--selection", type=Path)
    analyze.add_argument("--selection", type=Path)
    analyze.add_argument("--output-root", type=Path, default=Path("results_v5/protocol-v5.0.0/analysis"))
    analyze.add_argument("--run-id", default=None)
    validate.add_argument("package", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            result = validate_research_analysis_package(args.package)
            print(_json_text(result), end="")
            return EXIT_SUCCESS
        registry = load_claim_registry(args.registry)
        freeze = _read_json(args.freeze)
        threshold = load_p3_threshold(args.p3_threshold) if args.p3_threshold else None
        if args.command == "discover":
            candidates = discover_evidence(
                args.results_root,
                registry=registry,
                freeze=freeze,
                p3_threshold=threshold,
            )
            selection = load_selection(args.selection, registry_path=args.registry) if args.selection else None
            selected, report, fatal = select_evidence(
                candidates,
                registry,
                selection=selection,
                repository_root=args.registry.resolve().parents[1],
            )
            print(
                _json_text(
                    {
                        "schema_version": EVIDENCE_INVENTORY_SCHEMA_VERSION,
                        "candidates": [row.to_dict() for row in candidates],
                        "selection": report,
                        "selected_requirements": sorted(selected),
                        "fatal_requirements": sorted(fatal),
                    }
                ),
                end="",
            )
            return EXIT_FAILED if fatal or report["global_errors"] else EXIT_SUCCESS
        package, status, exit_code = run_research_analysis(
            results_root=args.results_root,
            output_root=args.output_root,
            run_id=args.run_id or _default_run_id(),
            registry_path=args.registry,
            freeze_path=args.freeze,
            selection_path=args.selection,
            p3_threshold_path=args.p3_threshold,
        )
        print(_json_text({"package": str(package.resolve()), "status": status, "exit_code": exit_code}), end="")
        return exit_code
    except Exception as exc:
        print(_json_text({"status": "FAIL", "error": str(exc)}), end="")
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ANALYSIS_PACKAGE_SCHEMA_VERSION",
    "EXIT_FAILED",
    "EXIT_INCOMPLETE",
    "EXIT_SUCCESS",
    "EvidenceCandidate",
    "ResearchAnalysisError",
    "check_provenance",
    "discover_evidence",
    "evaluate_claims",
    "generate_threats",
    "main",
    "run_research_analysis",
    "select_evidence",
    "validate_research_analysis_package",
]
