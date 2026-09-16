"""Fail-closed candidate disposition and generated claim/reporting views."""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
from pathlib import Path
from typing import Any, Mapping

from evaluation_v5.analysis.research_contracts import evaluate_conditions, load_claim_registry

from .common import Inputs, REGISTRY, file_sha256, read_json, safe_path


SCHEMA_VERSION = "protocol-v5-evidence-disposition-v1.0.0"
ALLOWED_DISPOSITIONS = (
    "ACCEPTED_CONFIRMATORY",
    "ACCEPTED_OBSERVED_NON_CONFIRMATORY",
    "HISTORICAL_FORMATIVE",
    "INCOMPATIBLE_FREEZE",
    "SUPERSEDED",
    "INCOMPLETE",
    "NOT_EXECUTED",
    "REJECTED_INTEGRITY",
    "UNVERIFIED",
)
GROUPS = ("raw", "derived", "statistics", "figures", "report")


def _canonical_digest(inputs: Inputs, package: str, group: str | None = None) -> str:
    prefix = package.rstrip("/") + "/"
    rows = []
    for name, expected in sorted(inputs.files.items()):
        if not name.startswith(prefix):
            continue
        relative = name[len(prefix):]
        if group is not None and relative.split("/", 1)[0] != group:
            continue
        inputs.path(name)
        rows.append(f"{expected}  {relative}\n")
    if not rows:
        raise ValueError("package group has no reviewed files")
    return hashlib.sha256("".join(rows).encode()).hexdigest()


def _original_seal_errors(inputs: Inputs, package: str) -> list[str]:
    sums_relative = package.rstrip("/") + "/SHA256SUMS"
    if sums_relative not in inputs.files:
        return []
    root = safe_path(inputs.root, package)
    errors = []
    for line in inputs.path(sums_relative).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            errors.append("INVALID_CHECKSUM_LINE")
            continue
        expected, relative = parts[0], parts[1].lstrip("*")
        target = safe_path(root, relative)
        registered = package.rstrip("/") + "/" + relative
        if registered not in inputs.files or not target.is_file() or file_sha256(target) != expected:
            errors.append("ORIGINAL_CHECKSUM_MISMATCH:" + relative)
    return errors


def _freeze_path(inventory: Mapping[str, Any], freeze_id: str) -> str | None:
    authority = inventory["authoritative_freeze"]
    if freeze_id == authority["freeze_id"]:
        return authority["manifest_path"]
    if freeze_id == "v5-e4-orbstack-confirmatory-freeze-v1":
        return "results_v5/protocol-v5.0.0/freezes/v5-e4-orbstack-confirmatory-freeze-v1/freeze-manifest.json"
    if freeze_id == "v5-e4-orbstack-analysis-repair-freeze-v1":
        return "results_v5/protocol-v5.0.0/freezes/v5-e4-orbstack-analysis-repair-freeze-v1/analysis-freeze.json"
    return None


def _candidate_integrity(inputs: Inputs, inventory: Mapping[str, Any], row: Mapping[str, Any]) -> dict:
    errors: list[str] = []
    package = row.get("package_path")
    package_checksum = None
    grouped = {name: None for name in GROUPS}
    if package is not None:
        try:
            package_checksum = _canonical_digest(inputs, package)
            if package_checksum != row.get("expected_package_checksum"):
                errors.append("PACKAGE_CHECKSUM_MISMATCH")
            for group in GROUPS:
                expected = (row.get("expected_artifact_checksums") or {}).get(group)
                if expected is not None:
                    grouped[group] = _canonical_digest(inputs, package, group)
                    if grouped[group] != expected:
                        errors.append("GROUP_CHECKSUM_MISMATCH:" + group)
            errors.extend(_original_seal_errors(inputs, package))
        except (OSError, ValueError) as exc:
            errors.append(type(exc).__name__ + ":PACKAGE_UNAVAILABLE_OR_UNREGISTERED")
    freeze = row.get("freeze_identity") or {}
    freeze_path = _freeze_path(inventory, str(freeze.get("freeze_id")))
    if not freeze_path or freeze_path not in inputs.files:
        errors.append("FREEZE_IDENTITY_UNREGISTERED")
    elif inputs.files[freeze_path] != freeze.get("manifest_sha256"):
        errors.append("FREEZE_CHECKSUM_MISMATCH")
    if package and row["candidate_id"].startswith("E5_"):
        manifest = inputs.json(package + "/manifest.json")
        if manifest.get("git_revision") != row.get("recorded_execution_sha"):
            errors.append("RECORDED_EXECUTION_SHA_MISMATCH")
        if manifest.get("execution_status") != row.get("execution_status"):
            errors.append("EXECUTION_STATUS_MISMATCH")
        if manifest.get("split_identity", {}).get("stage") != row.get("stage"):
            errors.append("SPLIT_STAGE_MISMATCH")
        dataset = row.get("dataset_identity") or {}
        recorded_dataset = manifest.get("dataset_identity") or {}
        if (recorded_dataset.get("dataset_id") != dataset.get("dataset_id")
                or recorded_dataset.get("dataset_sha256") != dataset.get("checksum")):
            errors.append("DATASET_IDENTITY_MISMATCH")
        environment = row.get("environment_identity") or {}
        if manifest.get("environment_identity", {}).get("environment_id") != environment.get("environment_id"):
            errors.append("ENVIRONMENT_IDENTITY_MISMATCH")
        configuration = row.get("configuration_identity") or {}
        catalog = manifest.get("candidate_catalog") or {}
        if (catalog.get("catalog_version") != configuration.get("catalog_version")
                or catalog.get("catalog_sha256") != configuration.get("catalog_sha256")):
            errors.append("CATALOG_IDENTITY_MISMATCH")
        authority = inventory["authoritative_freeze"]
        authority_doc = inputs.json(authority["manifest_path"])
        frozen_sha = authority_doc.get("source_control", {}).get("frozen_execution_sha")
        if frozen_sha != authority.get("frozen_execution_sha"):
            errors.append("AUTHORITATIVE_EXECUTION_SHA_MISMATCH")
        if (row.get("expected_disposition") == "ACCEPTED_CONFIRMATORY"
                and row.get("recorded_execution_sha") != frozen_sha):
            errors.append("INCOMPATIBLE_GLOBAL_EXECUTION_SHA")
        if row.get("stage") == "confirmatory":
            frozen_catalog = authority_doc.get("configuration_snapshot", {}).get("candidate_catalog", {})
            if catalog.get("catalog_sha256") != frozen_catalog.get("file_sha256"):
                errors.append("FROZEN_CATALOG_MISMATCH")
            ranker = (manifest.get("constraint_ranking_configuration") or {}).get(
                "ranker_version",
                (manifest.get("constraint_ranking_configuration") or {}).get("ranking", {}).get("ranker_version"),
            )
            if ranker != configuration.get("ranker_version"):
                errors.append("RANKER_IDENTITY_MISMATCH")
    return {
        "status": "FAIL" if errors else "PASS",
        "errors": sorted(set(errors)),
        "reviewed_file_count": sum(
            name.startswith(str(package).rstrip("/") + "/") for name in inputs.files
        ) if package else 0,
        "original_seal_verified": bool(package) and not errors,
        "freeze_identity_verified": not any("FREEZE" in error for error in errors),
        "package_checksum_verified": package is None or package_checksum == row.get("expected_package_checksum"),
        "artifact_checksums_verified": not any(error.startswith("GROUP_CHECKSUM") for error in errors),
        "package_checksum": package_checksum,
        "artifact_checksums": grouped,
    }


def build_dispositions(inputs: Inputs) -> dict[str, Any]:
    relative = inputs.lock.get("candidate_inventory")
    if not isinstance(relative, str):
        raise ValueError("final-audit inventory lacks candidate_inventory")
    inventory = inputs.json(relative)
    if inventory.get("schema_version") != "protocol-v5-final-evidence-candidates-v1.0.0":
        raise ValueError("unsupported candidate inventory")
    authority = inventory.get("authoritative_freeze") or {}
    if authority.get("manifest_path") != inputs.lock.get("authoritative_freeze"):
        raise ValueError("candidate and input inventories disagree on authoritative freeze")
    records = []
    by_id = {}
    for candidate in inventory.get("candidates") or []:
        integrity = _candidate_integrity(inputs, inventory, candidate)
        proposed = candidate.get("expected_disposition")
        if proposed not in ALLOWED_DISPOSITIONS:
            raise ValueError("candidate has unsupported disposition")
        disposition = "REJECTED_INTEGRITY" if integrity["status"] == "FAIL" else proposed
        record = {
            **candidate,
            "package_checksum": integrity["package_checksum"],
            "artifact_checksums": integrity["artifact_checksums"],
            "eligibility": disposition,
            "integrity": integrity,
        }
        records.append(record)
        by_id[record["candidate_id"]] = record
    rerun = by_id["E5_STORAGE_RERUN"]
    older = by_id["E5_STORAGE_OLD"]
    if rerun["eligibility"] == "ACCEPTED_CONFIRMATORY":
        older["eligibility"] = "SUPERSEDED"
        older["reason"] = (
            "Checksum/provenance-valid compatible rerun selected; this older package records "
            "the freeze-artifact commit and is never global confirmatory evidence."
        )
    else:
        older["eligibility"] = "INCOMPATIBLE_FREEZE"
        older["reason"] = (
            "Compatible rerun did not validate; this package records the freeze-artifact "
            "commit rather than the frozen execution SHA and remains globally ineligible."
        )
    result = {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": "5.0.0",
        "candidate_inventory": inputs.ref(relative),
        "authoritative_freeze": authority,
        "allowed_dispositions": list(ALLOWED_DISPOSITIONS),
        "records": records,
        "counts": dict(sorted(Counter(row["eligibility"] for row in records).items())),
        "integrity_status": "FAIL" if any(row["integrity"]["status"] == "FAIL" for row in records) else "PASS",
        "global_confirmatory_candidates": [
            row["candidate_id"] for row in records if row["eligibility"] == "ACCEPTED_CONFIRMATORY"
        ],
    }
    schema_path = inputs.lock.get("disposition_schema")
    if not isinstance(schema_path, str):
        raise ValueError("final-audit inventory lacks disposition schema")
    from jsonschema import validate
    validate(result, inputs.json(schema_path))
    return result


def _storage_metrics(inputs: Inputs, record: Mapping[str, Any]) -> dict[str, Any]:
    relative = str(record["package_path"]) + "/derived/storage_metrics.json"
    source = inputs.json(relative)
    prefixes = source.get("prefixes") or []
    if not prefixes:
        raise ValueError("accepted storage evidence has no ordered prefixes")
    first, final = prefixes[0], prefixes[-1]
    metrics = {
        "catalog_prefix_count": len(prefixes),
        "all_prefixes_nonexpanding": all(
            row["unique_layer_bytes"] <= row["naive_logical_bytes"] for row in prefixes
        ),
        "final_savings_bytes": final["naive_logical_bytes"] - final["unique_layer_bytes"],
        "expansion_naive_bytes": final["naive_logical_bytes"] - first["naive_logical_bytes"],
        "expansion_growth_difference": (
            final["unique_layer_bytes"] - first["unique_layer_bytes"]
        ) - (final["naive_logical_bytes"] - first["naive_logical_bytes"]),
        "strictly_slower_catalog_expansion": (
            final["unique_layer_bytes"] - first["unique_layer_bytes"]
        ) < (final["naive_logical_bytes"] - first["naive_logical_bytes"]),
        "prefix_order_valid": [row["prefix_size"] for row in prefixes] == list(range(1, len(prefixes) + 1)),
    }
    return {"metrics": metrics, "source": inputs.ref(relative, "/prefixes")}


def apply_confirmatory_dispositions(
    inputs: Inputs, audit: Mapping[str, Any], disposition: Mapping[str, Any]
) -> dict[str, Any]:
    """Create a generated view; never mutate the authenticated source package."""
    updated = copy.deepcopy(dict(audit))
    records = {row["candidate_id"]: row for row in disposition["records"]}
    claims = {row["id"]: row for row in updated.get("claims") or []}
    storage = records["E5_STORAGE_RERUN"]
    if storage["eligibility"] == "ACCEPTED_CONFIRMATORY":
        registry = load_claim_registry(inputs.path(REGISTRY))
        definition = next(row for row in registry["claims"] if row["id"] == "H7")
        extracted = _storage_metrics(inputs, storage)
        decision, checks = evaluate_conditions(
            definition["support_all_of"], {"metrics": extracted["metrics"]}
        )
        if decision is None:
            raise ValueError("accepted H7 storage evidence did not satisfy a decidable frozen predicate")
        claim = claims["H7"]
        claim.update(
            claim_status="SUPPORTED" if decision else "NOT_SUPPORTED",
            claimable=True,
            estimate=extracted["metrics"]["expansion_growth_difference"],
            confidence_interval=None,
            effect_size=None,
            normalized_metrics=extracted["metrics"],
            estimates={
                key: value for key, value in extracted["metrics"].items()
                if key != "catalog_prefix_count"
            },
            confidence_intervals={},
            counts={"catalog_prefix_count": extracted["metrics"]["catalog_prefix_count"]},
            effect_sizes={},
            reason_codes=[],
            limitations=[{
                "code": "CATALOG_AND_PLATFORM_SCOPE",
                "severity": "LIMITATION",
                "statement": "Exact result is limited to the four-image frozen catalog prefix and linux/amd64 registry manifests; configured 8/16-image scales were not executed."
            }],
            evidence_status=[{
                "requirement_id": "image_storage",
                "stage": "confirmatory",
                "validation_status": "PASS",
                "claim_eligibility": "ELIGIBLE_CONFIRMATORY",
                "execution_status": "OBSERVED",
            }],
            source=extracted["source"],
            decision_checks=checks,
        )
    updated["claims"] = [claims[row["id"]] for row in updated.get("claims") or []]
    counts = Counter(row["claim_status"] for row in updated["claims"])
    updated["claim_counts"] = {
        name: counts.get(name, 0) for name in ("SUPPORTED", "NOT_SUPPORTED", "NOT_EXECUTED")
    }
    decided = updated["claim_counts"]["SUPPORTED"] + updated["claim_counts"]["NOT_SUPPORTED"]
    updated["confirmatory_status"] = "EXECUTED_INCOMPLETE" if decided else updated.get("confirmatory_status")
    updated["evidence_dispositions"] = disposition
    if storage["eligibility"] == "ACCEPTED_CONFIRMATORY":
        manifest_ref = inputs.ref(str(storage["package_path"]) + "/manifest.json")
        for state in updated.get("experiment_states") or []:
            if state.get("requirement_id") != "image_storage":
                continue
            state.update(
                status="OBSERVED",
                selected_package=storage["package_path"],
                selected_manifest_sha256=manifest_ref["sha256"],
                candidate_count=sum(
                    row.get("experiment") == "E5_STORAGE"
                    for row in disposition["records"]
                ),
                eligible_candidate_count=1,
                reason_codes=[],
            )
    criteria = criterion_rows(updated, disposition)
    updated["criteria"] = criteria
    updated["evaluated_claim_view"] = {
        "schema_version": "protocol-v5-generated-evaluated-claim-view-v1.0.0",
        "source_registry": inputs.ref(REGISTRY),
        "source_authenticated_claim_package": updated.get("claim_evidence_sources", {}).get("package"),
        "claims": updated["claims"],
        "criteria": criteria,
        "experiment_states": updated.get("experiment_states") or [],
        "note": "Generated reporting view only; frozen registry, hypotheses, exclusions, predicates, and methods are unchanged.",
    }
    return updated


def criterion_rows(audit: Mapping[str, Any], disposition: Mapping[str, Any]) -> list[dict[str, Any]]:
    claims = {row["id"]: row for row in audit.get("claims") or []}
    candidates = {row["candidate_id"]: row for row in disposition["records"]}
    definitions = (
        ("satisfaction", "E3", "H4", "SEQ ease; SUS usability", "E3_FINAL_ANALYSIS"),
        ("time saving", "E3", "H3", "Paired decision time and selection effectiveness", "E3_FINAL_ANALYSIS"),
        ("correct image", "E5", "H7F", "Conservative functional success and operational adequacy", "E5_FUNCTIONAL_DEVELOPMENT"),
        ("image storage reuse", "E5", "H7", "Ordered-prefix unique-layer versus logical-byte growth", "E5_STORAGE_RERUN"),
        ("additional/fine-grained profiles", "E4", "H6", "Dynamic CPU/memory oracle error and allocation coverage", "E4_ORBSTACK_EFFICIENCY"),
        ("resource saving", "E4", "H5", "CPU/memory request cost per successful workload and reliability", "E4_ORBSTACK_EFFICIENCY"),
        ("flexible natural-language interaction", "E2", "H2", "Family-level robustness across equivalent variants", "E2_CONFIRMATORY"),
    )
    rows = []
    for criterion, experiment, claim_id, metric, candidate_id in definitions:
        claim = claims.get(claim_id) or {}
        candidate = candidates[candidate_id]
        descriptive = "N/A"
        if candidate_id == "E5_FUNCTIONAL_DEVELOPMENT":
            descriptive = "CONTRADICTS_FROZEN_CRITERION_NONCONFIRMATORY"
        rows.append({
            "criterion": criterion,
            "experiment": experiment,
            "hypothesis": claim_id,
            "metric_definition": metric,
            "independent_sample_count": candidate["counts"].get("sample_count"),
            "semantic_family_count": candidate["counts"].get("semantic_family_count"),
            "repetitions": candidate["counts"].get("repetitions"),
            "estimate_ci_effect": claim.get("normalized_metrics") or "N/A",
            "global_decision": claim.get("claim_status", "NOT_EXECUTED"),
            "descriptive_relationship": descriptive,
            "evidence_classification": candidate["eligibility"],
            "execution_status": candidate["execution_status"],
            "artifact": candidate["package_path"],
            "package_checksum": candidate["package_checksum"],
            "source_commit": candidate["source_commit"],
            "frozen_execution_sha": candidate["recorded_execution_sha"],
            "limitations": candidate["reason"],
        })
    return rows
