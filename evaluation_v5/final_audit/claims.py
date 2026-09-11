"""Portable authentication and summarisation of evaluated claim evidence.

The final audit does not evaluate claims a second time.  It consumes one
explicitly selected, checksum-bound research-analysis package and verifies the
selection and evaluated registry before exposing any result to reporting.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from evaluation_v5.analysis.research_contracts import (
    EVALUATED_CLAIM_SCHEMA_VERSION,
    evaluate_conditions,
    load_claim_registry,
    validate_evaluated_claim,
)

from .common import Inputs, file_sha256, read_json, safe_path


CLAIM_PACKAGE_KEY = "claim_analysis_package"
REQUIRED_OUTPUTS = (
    "derived/evaluated-claim-registry.json",
    "derived/evidence-completeness.json",
    "derived/evidence-inventory.json",
    "derived/evidence-selection.json",
    "derived/provenance-consistency.json",
    "report/threats-to-validity.json",
    "status.json",
)

REQUIREMENT_EXPERIMENTS = {
    "offline_recommendation": "E1",
    "natural_language_robustness": "E2",
    "user_study": "E3",
    "resource_efficiency": "E4",
    "image_functional": "E5_FUNCTIONAL",
    "image_storage": "E5_STORAGE",
    "p2_p3": "E6",
}

SYNTHETIC_ORIGINS = {
    "SYNTHETIC",
    "SYNTHETIC_TEST",
    "TEST",
    "FAKE",
    "FIXTURE",
    "MOCK",
    "DRY_RUN",
}


def _identity_path(inputs: Inputs, identity: Mapping[str, Any]) -> Path:
    reference = identity.get("path")
    digest = identity.get("sha256")
    if not isinstance(reference, str) or not isinstance(digest, str):
        raise ValueError("claim-analysis identity lacks path or sha256")
    return inputs.resolve(reference, digest)


def _registered_ref(inputs: Inputs, relative: str, pointer: str = "") -> dict[str, Any]:
    return inputs.ref(relative, pointer)


def _verify_package_outputs(inputs: Inputs, package: str, manifest: Mapping[str, Any]) -> None:
    outputs = manifest.get("output_checksums")
    if not isinstance(outputs, Mapping) or not outputs:
        raise ValueError("claim-analysis package lacks output checksums")
    for relative, expected in outputs.items():
        name = f"{package}/{relative}"
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ValueError("claim-analysis output identity is malformed")
        if file_sha256(inputs.path(name)) != expected:
            raise ValueError("claim-analysis output checksum mismatch: " + relative)
    for relative in REQUIRED_OUTPUTS:
        if relative not in outputs:
            raise ValueError("claim-analysis package lacks required output: " + relative)


def _verify_claim_lineage(inputs: Inputs, claim: Mapping[str, Any]) -> None:
    if claim.get("schema_version") != EVALUATED_CLAIM_SCHEMA_VERSION:
        raise ValueError("decided claims require the authenticated v1.1 evaluated-claim schema")
    lineage = (claim.get("result") or {}).get("metric_lineage") or {}
    condition_fields = {
        str(row["path"]).removeprefix("metrics.")
        for row in (claim.get("decision_rule") or {}).get("conditions") or []
    }
    if any(not lineage.get(field) for field in condition_fields):
        raise ValueError(f"{claim['claim_id']}: exact decision-field lineage is incomplete")
    for field in sorted(condition_fields):
        for source in lineage[field]:
            reference = source.get("source_artifact")
            digest = source.get("artifact_sha256")
            if not isinstance(reference, str) or not isinstance(digest, str):
                raise ValueError(f"{claim['claim_id']}: malformed metric lineage")
            inputs.resolve(reference, digest)
            locator = source.get("locator") or {}
            if not locator.get("json_pointers") or locator.get("matched_record_count", 0) < 1:
                raise ValueError(f"{claim['claim_id']}: non-exact metric locator")


def _selection_rows(selection: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = selection.get("requirements")
    if not isinstance(rows, list):
        raise ValueError("claim evidence selection lacks requirement rows")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        requirement = row.get("requirement_id") if isinstance(row, Mapping) else None
        if not isinstance(requirement, str) or requirement in result:
            raise ValueError("claim evidence selection has invalid or duplicate requirements")
        result[requirement] = row
    return result


def _candidate_records(
    selection_row: Mapping[str, Any], inventory: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    records = selection_row.get("candidate_records")
    if isinstance(records, list):
        return [row for row in records if isinstance(row, Mapping)]
    requirement = selection_row.get("requirement_id")
    return [
        row
        for row in inventory.get("candidates") or []
        if isinstance(row, Mapping) and row.get("requirement_id") == requirement
    ]


def _authentication_errors(candidate: Mapping[str, Any]) -> list[str]:
    requirement = candidate.get("requirement_id")
    authentication = candidate.get("authentication") or {}
    errors: list[str] = []
    if authentication.get("source_checksums_verified") is not True:
        errors.append("SOURCE_CHECKSUMS_NOT_VERIFIED")
    if requirement in {"offline_recommendation", "natural_language_robustness", "p2_p3"}:
        if authentication.get("split_custody_verified") is not True:
            errors.append("CONFIRMATORY_SPLIT_CUSTODY_NOT_VERIFIED")
    elif requirement == "resource_efficiency":
        if (
            authentication.get("collector_origin") != "REAL_KUBERNETES_COLLECTOR"
            or authentication.get("collector_authentic") is not True
        ):
            errors.append("UNAUTHENTICATED_RESOURCE_COLLECTOR_ORIGIN")
    elif requirement == "image_functional":
        origins = set(authentication.get("collector_origins") or [])
        if (
            authentication.get("validator_status") != "CURRENT_VALID"
            or authentication.get("collector_authentic") is not True
            or not origins
            or not origins.issubset({"LIVE_DOCKER", "LIVE_KUBERNETES"})
        ):
            errors.append("UNAUTHENTICATED_IMAGE_COLLECTOR_ORIGIN")
        if authentication.get("recommendation_provenance_verified") is not True:
            errors.append("IMAGE_RECOMMENDATION_PROVENANCE_INVALID")
    elif requirement == "image_storage":
        if (
            candidate.get("schema_version") != "protocol-v5-image-storage-evidence-v1.1.0"
            or authentication.get("validator_status") != "CURRENT_VALID"
            or authentication.get("collector_origin") != "REAL_REGISTRY"
            or authentication.get("collector_authentic") is not True
        ):
            errors.append("UNAUTHENTICATED_STORAGE_COLLECTOR_ORIGIN")
        if authentication.get("recommendation_provenance_verified") is not True:
            errors.append("STORAGE_RECOMMENDATION_PROVENANCE_INVALID")
    return sorted(set(errors))


def _verify_selected_evidence(
    inputs: Inputs,
    *,
    claims: list[Mapping[str, Any]],
    selection: Mapping[str, Any],
    inventory: Mapping[str, Any],
) -> None:
    rows = _selection_rows(selection)
    decided = [claim for claim in claims if claim["claim_status"] != "NOT_EXECUTED"]
    for claim in claims:
        for status in claim.get("evidence_status") or []:
            requirement = status.get("requirement_id")
            if requirement not in rows:
                raise ValueError(f"{claim['claim_id']}: evidence status is absent from selection")
        if claim["claim_status"] == "NOT_EXECUTED":
            continue
        if claim.get("reason_codes") or claim.get("claimable") is not True:
            raise ValueError(f"{claim['claim_id']}: decided claim has contradictory claimability")
        _verify_claim_lineage(inputs, claim)
        for status in claim.get("evidence_status") or []:
            if (
                status.get("stage") != "confirmatory"
                or status.get("validation_status") != "PASS"
                or status.get("claim_eligibility") != "ELIGIBLE_CONFIRMATORY"
            ):
                raise ValueError(f"{claim['claim_id']}: non-confirmatory evidence produced a decision")
            row = rows[status["requirement_id"]]
            selected_path = row.get("selected_package")
            selected_sha = row.get("selected_manifest_sha256")
            if not isinstance(selected_path, str) or not isinstance(selected_sha, str):
                raise ValueError(f"{claim['claim_id']}: selected evidence identity is missing")
            records = [
                candidate
                for candidate in _candidate_records(row, inventory)
                if candidate.get("manifest_sha256", candidate.get("registered_manifest_sha256")) == selected_sha
                and candidate.get("package_path") == selected_path
            ]
            if len(records) != 1:
                raise ValueError(f"{claim['claim_id']}: selected evidence record is ambiguous")
            record = records[0]
            errors = _authentication_errors(record)
            if errors:
                raise ValueError(f"{claim['claim_id']}: selected evidence authentication failed: {'|'.join(errors)}")
        for evidence in claim.get("evidence") or []:
            manifest_path = evidence.get("manifest_path")
            manifest_sha = evidence.get("manifest_sha256")
            if not isinstance(manifest_path, str) or not isinstance(manifest_sha, str):
                raise ValueError(f"{claim['claim_id']}: evidence manifest identity is malformed")
            inputs.resolve(manifest_path, manifest_sha)
            requirement = evidence.get("requirement_id")
            row = rows.get(requirement) or {}
            records = [
                candidate
                for candidate in _candidate_records(row, inventory)
                if candidate.get("manifest_sha256", candidate.get("registered_manifest_sha256")) == manifest_sha
                and candidate.get("package_path") == evidence.get("package_path")
            ]
            if len(records) != 1:
                raise ValueError(f"{claim['claim_id']}: claim evidence is absent from authenticated selection")
            record = records[0]
            if evidence.get("authentication") != record.get("authentication"):
                raise ValueError(f"{claim['claim_id']}: claim authentication differs from selected evidence")
            if evidence.get("semantic_provenance") != record.get("semantic_provenance"):
                raise ValueError(f"{claim['claim_id']}: semantic provenance differs from selected evidence")
            for artifact in evidence.get("artifacts") or []:
                path = artifact.get("path")
                digest = artifact.get("sha256")
                if not isinstance(path, str) or not isinstance(digest, str):
                    raise ValueError(f"{claim['claim_id']}: evidence artifact identity is malformed")
                inputs.resolve(path, digest)
    if decided and selection.get("global_errors"):
        raise ValueError("claim selection has global errors but exposes decided claims")


def _metric_summary(metrics: Mapping[str, Any]) -> dict[str, Any]:
    counts: dict[str, Any] = {}
    intervals: dict[str, Any] = {}
    estimates: dict[str, Any] = {}
    effect_sizes: dict[str, Any] = {}
    ci_low = metrics.get("ci_low")
    ci_high = metrics.get("ci_high")
    if ci_low is not None or ci_high is not None:
        intervals["primary"] = {"low": ci_low, "high": ci_high}
    for key, value in metrics.items():
        token = key.lower()
        if token in {"ci_low", "ci_high"}:
            continue
        if token.endswith(("_count", "_n")) or token.startswith("n_"):
            counts[key] = value
        elif "ci_" in token or token.endswith("_interval"):
            intervals[key] = value
        elif "effect_size" in token or token in {"cohens_d", "rank_biserial"}:
            effect_sizes[key] = value
        elif token not in {"p_value", "p_value_raw", "p_holm", "test_available"}:
            estimates[key] = value
    return {
        "estimates": estimates,
        "confidence_intervals": intervals,
        "counts": counts,
        "effect_sizes": effect_sizes,
    }


def summarize_evaluated_claims(
    inputs: Inputs, package: str, claims: list[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Project authenticated evaluated claims into the exact final-report shape.

    Authentication and recomputation happen before this function in
    ``load_claim_evidence``.  Keeping the projection explicit makes it possible
    to prove that values and selection-derived reasons are not replaced by a
    snapshot-specific reporting template.
    """

    summaries = []
    for index, claim in enumerate(claims):
        metrics = (claim.get("result") or {}).get("normalized_metrics") or {}
        metric_summary = _metric_summary(metrics)
        summaries.append(
            {
                "id": claim["claim_id"],
                "research_question": claim["research_question"]["id"],
                "hypothesis": claim["hypothesis"],
                "claim_status": claim["claim_status"],
                "claimable": claim["claimable"],
                "estimate": metrics.get("effect"),
                "confidence_interval": (
                    {"low": metrics.get("ci_low"), "high": metrics.get("ci_high")}
                    if metrics.get("ci_low") is not None or metrics.get("ci_high") is not None
                    else None
                ),
                "effect_size": metrics.get("effect_size"),
                "normalized_metrics": metrics,
                **metric_summary,
                "reason_codes": list(claim.get("reason_codes") or []),
                "limitations": list(claim.get("limitations") or []),
                "evidence_status": list(claim.get("evidence_status") or []),
                "source": _registered_ref(
                    inputs,
                    package + "/derived/evaluated-claim-registry.json",
                    "/claims/" + str(index),
                ),
            }
        )
    return summaries


def _requirement_state(row: Mapping[str, Any], candidates: list[Mapping[str, Any]]) -> dict[str, Any]:
    selected = row.get("selected_package")
    if selected:
        matches = [candidate for candidate in candidates if candidate.get("package_path") == selected]
        status = str(matches[0].get("execution_status") or "OBSERVED") if len(matches) == 1 else "UNSUPPORTED"
    else:
        confirmatory = [candidate for candidate in candidates if candidate.get("stage") == "confirmatory"]
        observed_development = [
            candidate
            for candidate in candidates
            if candidate.get("stage") != "confirmatory"
            and candidate.get("execution_status") in {"OBSERVED", "DERIVED_EVIDENCE_COMPLETE"}
        ]
        if row.get("conflict_status") == "CONFLICTING" or any(
            candidate.get("validation_status") == "FAIL" for candidate in confirmatory
        ):
            status = "UNSUPPORTED"
        elif row.get("eligible_candidate_count", 0) > 0:
            status = "UNRESOLVED"
        elif observed_development:
            status = "DEVELOPMENT_ONLY"
        else:
            status = "NOT_EXECUTED"
    return {
        "requirement_id": row["requirement_id"],
        "experiment": REQUIREMENT_EXPERIMENTS.get(row["requirement_id"], "UNKNOWN"),
        "status": status,
        "selected_package": selected,
        "selected_manifest_sha256": row.get("selected_manifest_sha256"),
        "candidate_count": row.get("candidate_count", len(candidates)),
        "eligible_candidate_count": row.get("eligible_candidate_count", 0),
        "reason_codes": list(row.get("reason_codes") or []),
    }


def synthetic_origin_scan(
    selection: Mapping[str, Any], inventory: Mapping[str, Any]
) -> dict[str, Any]:
    """Inspect discovered collector provenance and eligibility, regardless of path."""

    findings: list[dict[str, Any]] = []
    candidates = [row for row in inventory.get("candidates") or [] if isinstance(row, Mapping)]
    selection_by_requirement = _selection_rows(selection)
    for index, candidate in enumerate(candidates):
        auth = candidate.get("authentication") or {}
        provenance = candidate.get("provenance") or candidate.get("source_provenance") or {}
        metadata = candidate.get("metadata") or {}
        raw_origins: list[Any] = []
        for source in (auth, provenance, metadata):
            raw_origins.extend(
                source.get(key)
                for key in ("collector_origin", "storage_collector_origin")
                if source.get(key) is not None
            )
            origins = source.get("collector_origins")
            if isinstance(origins, list):
                raw_origins.extend(origins)
        origins = sorted({str(origin).upper() for origin in raw_origins})
        reasons = {str(code).upper() for code in candidate.get("reason_codes") or []}
        synthetic = bool(set(origins) & SYNTHETIC_ORIGINS) or any(
            "SYNTHETIC" in reason or "MOCK" in reason or "FIXTURE" in reason
            for reason in reasons
        )
        row = selection_by_requirement.get(str(candidate.get("requirement_id"))) or {}
        selected = row.get("selected_package") == candidate.get("package_path")
        exposed = bool(
            selected
            or candidate.get("claim_eligibility") == "ELIGIBLE_CONFIRMATORY"
            or candidate.get("claims_permitted") is True
        )
        authentication_errors = _authentication_errors(candidate) if exposed else []
        promoted = bool(synthetic and exposed)
        unauthenticated = bool(exposed and authentication_errors)
        if synthetic or promoted or unauthenticated:
            findings.append(
                {
                    "candidate_index": index,
                    "requirement_id": candidate.get("requirement_id"),
                    "collector_origins": origins,
                    "synthetic": synthetic,
                    "selected": selected,
                    "claim_eligibility": candidate.get("claim_eligibility"),
                    "claims_permitted": candidate.get("claims_permitted"),
                    "authentication_errors": authentication_errors,
                    "disposition": (
                        "FAIL_PROMOTED" if promoted else
                        "FAIL_UNAUTHENTICATED" if unauthenticated else
                        "NON_CLAIMABLE"
                    ),
                }
            )
    return {
        "schema_version": "protocol-v5-final-synthetic-origin-scan-v1.0.0",
        "candidate_count": len(candidates),
        "synthetic_candidate_count": sum(row["synthetic"] for row in findings),
        "promoted_synthetic_count": sum(row["disposition"] == "FAIL_PROMOTED" for row in findings),
        "unauthenticated_exposed_count": sum(row["disposition"] == "FAIL_UNAUTHENTICATED" for row in findings),
        "status": "FAIL" if any(row["disposition"].startswith("FAIL_") for row in findings) else "PASS",
        "findings": findings,
    }


def load_claim_evidence(inputs: Inputs) -> dict[str, Any]:
    package = inputs.lock.get(CLAIM_PACKAGE_KEY)
    if not isinstance(package, str) or package not in inputs.lock.get("packages", []):
        raise ValueError("final-audit inventory lacks one explicit claim_analysis_package")
    root = safe_path(inputs.root, package)
    manifest = inputs.json(package + "/manifest.json")
    _verify_package_outputs(inputs, package, manifest)
    for identity_name in ("registry", "freeze", "selection", "p3_threshold"):
        identity = manifest.get(identity_name)
        if identity is not None:
            _identity_path(inputs, identity)

    registry_identity = manifest.get("registry") or {}
    registry_path = _identity_path(inputs, registry_identity)
    registry = load_claim_registry(registry_path)
    claim_payload = inputs.json(package + "/derived/evaluated-claim-registry.json")
    claims = claim_payload.get("claims")
    if not isinstance(claims, list):
        raise ValueError("evaluated claim registry lacks claims")
    definitions = {row["id"]: row for row in registry["claims"]}
    if {claim.get("claim_id") for claim in claims if isinstance(claim, Mapping)} != set(definitions):
        raise ValueError("evaluated claim registry is incomplete")

    selection = inputs.json(package + "/derived/evidence-selection.json")
    inventory = inputs.json(package + "/derived/evidence-inventory.json")
    completeness = inputs.json(package + "/derived/evidence-completeness.json")
    provenance = inputs.json(package + "/derived/provenance-consistency.json")
    threats = inputs.json(package + "/report/threats-to-validity.json")
    selection_rows = _selection_rows(selection)
    expected_requirements = {row["id"] for row in registry["evidence_requirements"]}
    if set(selection_rows) != expected_requirements:
        raise ValueError("claim evidence selection does not cover the registry requirements")

    for claim in claims:
        validate_evaluated_claim(claim)
        definition = definitions[claim["claim_id"]]
        if claim.get("hypothesis") != definition["hypothesis"]:
            raise ValueError(f"{claim['claim_id']}: hypothesis differs from registry")
        decision, checks = evaluate_conditions(
            definition["support_all_of"],
            {"metrics": (claim.get("result") or {}).get("normalized_metrics") or {}},
        )
        if checks != (claim.get("result") or {}).get("decision_checks"):
            raise ValueError(f"{claim['claim_id']}: decision checks do not recompute")
        expected_status = (
            "NOT_EXECUTED"
            if claim.get("reason_codes") or decision is None
            else "SUPPORTED"
            if decision
            else "NOT_SUPPORTED"
        )
        if claim["claim_status"] != expected_status:
            raise ValueError(f"{claim['claim_id']}: claim status does not recompute")

    _verify_selected_evidence(
        inputs, claims=claims, selection=selection, inventory=inventory
    )
    counts = Counter(claim["claim_status"] for claim in claims)
    expected_counts = {name: counts.get(name, 0) for name in ("SUPPORTED", "NOT_SUPPORTED", "NOT_EXECUTED")}
    if expected_counts != manifest.get("claim_counts") or expected_counts != completeness.get("counts"):
        raise ValueError("claim counts disagree across authenticated package artifacts")
    package_status = manifest.get("status")
    if package_status == "FAILED" and any(claim.get("claimable") for claim in claims):
        raise ValueError("failed claim-analysis package exposes claimable conclusions")

    candidates = [row for row in inventory.get("candidates") or [] if isinstance(row, Mapping)]
    requirement_states = []
    for requirement, row in selection_rows.items():
        requirement_states.append(
            _requirement_state(
                row,
                [candidate for candidate in candidates if candidate.get("requirement_id") == requirement],
            )
        )
    required_claims = set(completeness.get("required_claims") or definitions)
    required_rows = [claim for claim in claims if claim["claim_id"] in required_claims]
    decided_required = sum(claim["claim_status"] != "NOT_EXECUTED" for claim in required_rows)
    if package_status == "FAILED":
        confirmatory_status = "UNSUPPORTED"
    elif decided_required == 0:
        confirmatory_status = "NOT_EXECUTED"
    elif decided_required == len(required_rows):
        confirmatory_status = "EXECUTED_COMPLETE"
    else:
        confirmatory_status = "EXECUTED_INCOMPLETE"
    summaries = summarize_evaluated_claims(inputs, package, claims)
    h8 = next(claim for claim in claims if claim["claim_id"] == "H8")
    p3_selection = selection_rows["p2_p3"]
    if h8["claim_status"] != "NOT_EXECUTED":
        p3_state = "RETAINED_CONFIRMATORY_DECIDED"
    elif "P3_NOT_RETAINED_OR_NOT_PRESENT" in (p3_selection.get("reason_codes") or []):
        p3_state = "NOT_RETAINED_OR_NOT_PRESENT"
    elif p3_selection.get("selected_package"):
        p3_state = "RETAINED_CONFIRMATORY_INCOMPLETE"
    else:
        p3_state = "UNSUPPORTED_OR_UNAVAILABLE"

    scan = synthetic_origin_scan(selection, inventory)
    return {
        "package": package,
        "package_schema_version": manifest.get("schema_version"),
        "package_status": package_status,
        "source": _registered_ref(inputs, package + "/manifest.json"),
        "selection_source": _registered_ref(inputs, package + "/derived/evidence-selection.json"),
        "evaluated_claims_source": _registered_ref(inputs, package + "/derived/evaluated-claim-registry.json"),
        "registry_source": inputs.ref(str(registry_path.relative_to(inputs.root))),
        "claims": summaries,
        "evaluated_claims": claims,
        "research_questions": registry["research_questions"],
        "claim_counts": expected_counts,
        "confirmatory_status": confirmatory_status,
        "requirement_states": sorted(requirement_states, key=lambda row: row["experiment"]),
        "p3_state": p3_state,
        "selection": selection,
        "inventory": inventory,
        "provenance": provenance,
        "threats": threats,
        "synthetic_origin_scan": scan,
    }
