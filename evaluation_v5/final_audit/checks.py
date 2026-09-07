"""Evidence inspection only. No collector or recommender is executed here."""
from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from evaluation_v5.analysis.research_contracts import load_claim_registry
from evaluation_v5.freeze import validate_freeze_manifest
from evaluation_v5.isolation_audit import audit_repository

from . import SCHEMA_VERSION
from .common import Inputs, REGISTRY, RESULTS, file_sha256, read_json, read_rows, safe_path

CHECKS = {
    1: "Authoritative final experiment freeze",
    2: "Confirmatory dataset checksum and split manifest",
    3: "Development/confirmatory isolation",
    4: "Frozen P1/P2/P3 implementation identities",
    5: "Catalog, corpus, index, prompt and configuration provenance",
    6: "Raw evidence preservation and package integrity",
    7: "Raw-to-derived regeneration",
    8: "Derived-to-figures/tables regeneration",
    9: "Historical Protocol-v4 preservation",
    10: "Human-study direct-identifier exclusion",
    11: "Kubernetes environment identity",
    12: "Image-storage immutable digests and platforms",
    13: "Observed execution versus synthetic fixtures",
    14: "Independent statistical units",
    15: "No B0 ranking metrics",
    16: "P3 development gate and primary-system boundary",
    17: "Missing experiments and placeholder values",
}


def walk(value: Any, pointer: str = ""):
    yield pointer, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, pointer + "/" + str(key).replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, pointer + "/" + str(index))


def b0_ranking_findings(value: Any, inherited_b0: bool = False) -> list[str]:
    """Inspect actual metric rows and system-keyed maps, including nested tables."""
    findings = []
    if isinstance(value, dict):
        b0 = inherited_b0 or any(value.get(k) == "B0" for k in
                                 ("system", "system_id", "condition", "recommender"))
        b0 = b0 or "B0" in (value.get("systems") or [])
        for key, child in value.items():
            token = str(key).lower().replace("_", "")
            metric_name = str(child).lower().replace("_", "") if key in ("metric", "endpoint", "name") else ""
            if b0 and any(t in token or t in metric_name for t in ("mrr", "ndcg", "hit@", "hitat")):
                findings.append("B0_RANKING_METRIC")
            findings.extend(b0_ranking_findings(child, b0 or key == "B0"))
    elif isinstance(value, list):
        for child in value:
            findings.extend(b0_ranking_findings(child, inherited_b0))
    return sorted(set(findings))


def inference_findings(value: Any, *, experiment: str, family_count: int | None = None,
                       inherited_unit: str | None = None) -> list[str]:
    findings = []
    if isinstance(value, dict):
        unit = next((value[k] for k in ("independent_unit", "resampling_unit", "analysis_unit",
                    "inferential_unit", "statistical_unit") if isinstance(value.get(k), str)), inherited_unit)
        actual_test = any((str(k).startswith("p_value") or k in ("p_holm", "p_raw"))
                          and v is not None for k, v in value.items())
        if actual_test:
            allowed = ("participant", "participant_task", "participant_and_task") if experiment == "E3" else ("family", "workload_family")
            if unit not in allowed:
                findings.append("INFERENCE_UNIT_MISSING_OR_INVALID")
            n = value.get("effective_family_n", value.get("n_families"))
            if family_count is not None and n is not None and n > family_count:
                findings.append("INFLATED_FAMILY_SAMPLE_COUNT")
        for child in value.values():
            findings.extend(inference_findings(child, experiment=experiment,
                                               family_count=family_count, inherited_unit=unit))
    elif isinstance(value, list):
        for child in value:
            findings.extend(inference_findings(child, experiment=experiment,
                                               family_count=family_count, inherited_unit=inherited_unit))
    return sorted(set(findings))


def execution_findings(status: str, rows: list[dict], *, kind: str) -> list[str]:
    findings = []
    if kind == "image_functional":
        from evaluation_v5.image_storage.contracts import ImageProbeResult
        # v1.0 predates execution_status. Reuse its documented error-category
        # interpretation rather than declaring its real probe outputs missing.
        rows = [({**row, "execution_status": ImageProbeResult.from_dict(row).execution_status}
                 if row.get("schema_version") == "protocol-v5-image-probe-record-v1.0.0"
                 and "execution_status" not in row else row) for row in rows]
    measured = [r for r in rows if r.get("execution_status") in ("EXECUTED", "OBSERVED")]
    if kind != "image_functional":
        measured = rows
    if status == "OBSERVED":
        if not measured:
            findings.append("OBSERVED_WITHOUT_OBSERVATIONS")
        if any(r.get("synthetic") is True or r.get("synthetic_only") is True
               or r.get("execution_mode") in ("synthetic", "fixture", "mock", "dry_run")
               or r.get("evidence_class") in ("synthetic", "fixture") for r in measured):
            findings.append("SYNTHETIC_OBSERVED")
        if kind == "image_functional" and any(
                r.get("execution_mode") not in ("docker", "kubernetes") for r in measured):
            findings.append("EXECUTION_ORIGIN_UNVERIFIED")
    if status in ("NOT_EXECUTED", "DRY_RUN") and measured:
        findings.append("UNEXECUTED_PACKAGE_CONTAINS_OBSERVATIONS")
    return findings


def cluster_findings(status: str, environment: dict) -> list[str]:
    if status != "OBSERVED":
        return []
    # Empty, mock, or host-only identities are not Kubernetes provenance.
    text = json.dumps(environment).lower()
    required = ("kubernetes", "node", "context")
    if not environment or any(k not in text for k in required):
        return ["KUBERNETES_ENVIRONMENT_IDENTITY_INCOMPLETE"]
    if any(t in text for t in ("no-cluster-measurement", '"synthetic"', '"unknown"')):
        return ["KUBERNETES_ENVIRONMENT_IDENTITY_PLACEHOLDER"]
    return []


def storage_identity_findings(images: list[dict]) -> list[str]:
    findings = []
    for image in images:
        reference = image.get("immutable_reference", image.get("image_reference", ""))
        digest = image.get("manifest_digest", image.get("image_digest", ""))
        platform = image.get("platform")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(digest)) or not reference.endswith("@" + str(digest)):
            findings.append("MUTABLE_OR_MISSING_IMAGE_DIGEST")
        if not (isinstance(platform, dict) and platform.get("os") and platform.get("architecture")) and not (
                isinstance(platform, str) and re.fullmatch(r"[a-z0-9]+/[a-z0-9_]+(?:/[a-z0-9]+)?", platform)):
            findings.append("CONTAINER_PLATFORM_UNRECORDED")
    return sorted(set(findings))


def placeholder_findings(value: Any) -> list[str]:
    """Only result estimates are assessed; zero observed counts remain legitimate."""
    findings = []
    for pointer, item in walk(value):
        if not isinstance(item, dict):
            continue
        status = item.get("execution_status", item.get("status"))
        if status in ("NOT_EXECUTED", "DRY_RUN"):
            for key in ("estimate", "p_value", "confidence_interval", "effect_size", "accuracy",
                        "storage_savings_bytes", "cpu_usage", "memory_usage"):
                if item.get(key) is not None:
                    findings.append(pointer + "/" + key)
        if item.get("claims_permitted") is True and status in ("NOT_EXECUTED", "DRY_RUN", "INCOMPLETE"):
            findings.append(pointer + "/claims_permitted")
    return findings


def p3_findings(value: Any, retained: bool) -> list[str]:
    if retained:
        return []
    return [p for p, row in walk(value) if isinstance(row, dict) and
            (row.get("primary_system") == "P3" or
             (row.get("system_id") == "P3" and row.get("primary") is True) or
             (row.get("claim_id") == "H8" and row.get("claim_status") in ("SUPPORTED", "NOT_SUPPORTED")))]


def package_integrity(inputs: Inputs, relative: str) -> list[dict]:
    """Check existing manifests without accepting the new inventory as original sealing."""
    root = safe_path(inputs.root, relative)
    errors = []
    registrations: list[tuple[str, str]] = []
    sums = root / "SHA256SUMS"
    if sums.is_file():
        for line in sums.read_text().splitlines():
            if line.strip():
                parts = line.split(maxsplit=1)
                if len(parts) != 2:
                    errors.append({"code": "INVALID_CHECKSUM_LINE", "path": relative + "/SHA256SUMS"})
                else:
                    registrations.append((parts[1].lstrip("*"), parts[0]))
    for name in ("manifest.json", "report/analysis-manifest.json"):
        if (root / name).is_file():
            manifest = read_json(root / name)
            for key in ("output_checksums", "output_sha256", "generated_file_sha256"):
                registrations.extend(manifest.get(key, {}).items())
    for name, digest in sorted(set(registrations)):
        path = safe_path(root, name)
        if not path.is_file() or file_sha256(path) != digest:
            entry = {"code": "ORIGINAL_CHECKSUM_MISMATCH", "path": relative + "/" + name,
                     "recorded_sha256": digest,
                     "actual_sha256": file_sha256(path) if path.is_file() else None}
            if path.is_file() and path.suffix == ".csv":
                content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                entry["crlf_reconstruction_matches_recorded_hash"] = hashlib.sha256(content).hexdigest() == digest
            errors.append(entry)
    return errors


def _safe_error(exc: Exception, root: Path) -> str:
    text = str(exc).replace(str(root) + "/", "")
    # Do not emit arbitrary raw values or private path text from validators.
    if "/Users/" in text or "/home/" in text or len(text) > 400:
        return type(exc).__name__ + ": source validator rejected evidence (see source contract)"
    return text


def validate_package(inputs: Inputs, relative: str) -> dict:
    root = safe_path(inputs.root, relative)
    record = {"path": relative, "experiment": relative.split("/")[-2], "status": "UNKNOWN",
              "stage": "unknown", "validation": "PASS", "errors": [], "raw_records": None,
              "kind": "unknown", "source": None}
    try:
        for name in inputs.files:
            if name.startswith(relative + "/"):
                inputs.path(name)
        record["errors"] = package_integrity(inputs, relative)
        manifest_path = root / "manifest.json"
        if (root / "raw/offline-run-provenance.json").is_file():
            manifest_path = root / "raw/offline-run-provenance.json"
        if not manifest_path.is_file():
            manifest_path = root / "plan.json"
        manifest = read_json(manifest_path)
        record["source"] = inputs.ref(str(manifest_path.relative_to(inputs.root)))
        schema = record["schema_version"] = manifest.get("schema_version", "unknown")
        record["status"] = manifest.get("execution_status", manifest.get("status", "UNKNOWN"))
        record["stage"] = manifest.get("split_identity", {}).get("stage", "development")
        result = {}
        if schema.startswith("protocol-v5-offline-recommendation-provenance"):
            from evaluation_v5.offline.validate_evidence import validate_offline_evidence
            record.update(kind="offline", stage=manifest["split"]["role"])
            if record["stage"] != "development":
                raise ValueError("sealed confirmatory validation requires an external safe custody attestation")
            result = validate_offline_evidence(root)
            record.update(status="OBSERVED", raw_records=result["records_validated"],
                          case_count=manifest["split"]["case_count"], family_count=manifest["split"]["family_count"])
        elif schema.startswith("protocol-v5-user-study-provenance"):
            from evaluation_v5.analysis.research_analysis import _adapt_user_study_package
            candidate = _adapt_user_study_package(root)
            record.update(kind="user_study", raw_records=len(read_rows(root / "raw/events.jsonl")))
            if candidate.validation_status != "PASS":
                raise ValueError(candidate.validation_error)
        elif schema.startswith("protocol-v5-resource-calibration-run"):
            from evaluation_v5.resource.evidence import validate_evidence_package
            record.update(kind="resource_envelope", raw_records=len(read_rows(root / "raw/trials.jsonl")) if (root / "raw/trials.jsonl").is_file() else 0)
            result = validate_evidence_package(root)
        elif schema.startswith("protocol-v5-resource-efficiency-raw-package"):
            from evaluation_v5.resource.efficiency_evidence import validate_raw_package
            record.update(kind="resource_efficiency", raw_records=len(read_rows(root / "raw/trials.jsonl")) if (root / "raw/trials.jsonl").is_file() else 0)
            result = validate_raw_package(root)
        elif schema.startswith("protocol-v5-research-analysis-package"):
            # Preserve old bytes, validate all original checksum-bound references via explicit relocation.
            record.update(kind="research_analysis", stage="analysis")
            for reference, digest in manifest["input_artifact_sha256"].items():
                inputs.resolve(reference, digest)
            for key in ("registry", "freeze", "selection", "p3_threshold"):
                identity = manifest.get(key)
                if identity:
                    inputs.resolve(identity["path"], identity["sha256"])
            from evaluation_v5.analysis.research_contracts import validate_evaluated_claim, evaluate_conditions
            for claim in read_json(root / "derived/evaluated-claim-registry.json")["claims"]:
                validate_evaluated_claim(claim)
                if claim["claim_status"] != "NOT_EXECUTED":
                    # Future decided packages need the existing full semantic/locator validator.
                    from evaluation_v5.analysis.research_analysis import validate_research_analysis_package
                    validate_research_analysis_package(root)
        elif (root / "raw/probe_results.jsonl").is_file():
            from evaluation_v5.image_storage.validate_evidence import validate_e5_evidence
            record.update(kind="image_functional", raw_records=len(read_rows(root / "raw/probe_results.jsonl")))
            result = validate_e5_evidence(root)
        elif (root / "raw/image_layers.json").is_file():
            from evaluation_v5.image_storage.validate_evidence import validate_e5_storage_evidence
            record["kind"] = "image_storage"
            result = validate_e5_storage_evidence(root)
        elif manifest_path.name == "plan.json":
            from evaluation_v5.resource.efficiency_plan import validate_efficiency_plan
            validate_efficiency_plan(manifest)
            record.update(kind="resource_plan", status="PLANNED", raw_records=0)
        else:
            raise ValueError("unsupported package schema; cannot certify this evidence")
        record["validator_result"] = {k: v for k, v in result.items() if k != "evidence_dir"}
    except Exception as exc:
        record["errors"].append({"code": "SOURCE_VALIDATOR_FAILED", "reason": _safe_error(exc, inputs.root)})
    if record["errors"]:
        record["validation"] = "FAIL"
    return record


def inspect(inputs: Inputs, *, isolation: bool = True, historical: bool = True) -> dict:
    checks = {i: {"id": i, "title": title, "verdict": "UNVERIFIED", "reason": "Not assessed",
                  "sources": [], "details": []} for i, title in CHECKS.items()}
    def set_check(i, verdict, reason, details=None, sources=None):
        checks[i].update(verdict=verdict, reason=reason, details=details or [], sources=sources or [])

    input_failures = inputs.verify()
    if input_failures:
        # Do not parse unauthenticated data, including a replaced file that could
        # now contain sealed cases. Still publish a complete diagnostic audit.
        set_check(6, "FAIL", "Reviewed input integrity failed. Dependent content was not opened or analyzed.", input_failures)
        return {"schema_version": SCHEMA_VERSION, "protocol_version": "5.0.0", "checks": list(checks.values()),
                "packages": [], "claims": [], "evaluated_claims": [], "research_questions": [],
                "primary_system": "P2", "confirmatory_status": "UNVERIFIED", "audit_status": "FAIL",
                "input_integrity_blocked": True,
                "source_inventory": {"path": inputs.lock_path, "sha256": file_sha256(inputs.root / inputs.lock_path)}}
    packages = [validate_package(inputs, p) for p in inputs.lock["packages"]]
    unregistered = []
    for section in ("E1", "E2", "E3", "E4", "E5", "E6", "analysis", "freezes"):
        base = inputs.root / RESULTS / section
        if base.exists():
            unregistered.extend(str(p.relative_to(inputs.root)) for p in base.rglob("*")
                                if p.is_file() and str(p.relative_to(inputs.root)) not in inputs.files)
    failures = input_failures + [{"path": p["path"], "errors": p["errors"]}
                                 for p in packages if p["validation"] != "PASS"]
    failures.extend({"path": p, "reason": "UNREGISTERED_EVIDENCE"} for p in sorted(unregistered))
    set_check(6, "FAIL" if failures else "PASS", "Original seals and reviewed input bytes checked; failed packages remain preserved.", failures)

    freezes = [p for p in inputs.files if p.startswith(RESULTS + "/freezes/") and p.endswith("/freeze-manifest.json")]
    authority = None
    if not freezes:
        set_check(1, "UNVERIFIED", "No authoritative final freeze exists. frozen-configuration.json is a design snapshot, not a FROZEN envelope.")
    elif len(freezes) != 1:
        set_check(1, "FAIL", "Multiple final freezes require an explicit reviewed authority selection.")
    else:
        try:
            authority = validate_freeze_manifest(inputs.json(freezes[0]))
            if Path(freezes[0]).parent.name != authority["freeze_id"]:
                raise ValueError("freeze directory identity mismatch")
            set_check(1, "PASS", "Production freeze envelope validates; collection chronology still requires custody records.", sources=[inputs.ref(freezes[0])])
        except Exception as exc:
            set_check(1, "FAIL", _safe_error(exc, inputs.root))
    set_check(2, "UNVERIFIED", "Confirmatory split and safe custodian checksum attestation are unavailable; no sealed file was opened.")
    if isolation:
        try:
            report = audit_repository(inputs.root)
            set_check(3, "FAIL" if not report.clean else "UNVERIFIED",
                      "Repository/archive isolation scan completed. External custody and semantic independence cannot be proven without custodian evidence.",
                      [{"repository_scan": "PASS" if report.clean else "FAIL",
                        "documents": report.repository_documents_scanned, "archives": report.archives_scanned,
                        "findings": [{"location": f.location, "category": f.category} for f in report.findings]}])
        except Exception as exc:
            set_check(3, "FAIL", _safe_error(exc, inputs.root))

    snapshot_rel = RESULTS + "/freezes/frozen-configuration.json"
    snapshot = inputs.json(snapshot_rel) if snapshot_rel in inputs.files else {}
    semantic_differences = []
    for package in packages:
        if package["kind"] == "offline" and package.get("source"):
            recorded = inputs.json(package["source"]["path"]).get("frozen_configuration", {})
            for key in ("systems", "runtime_package", "candidate_catalog", "indexes", "prompts", "configuration"):
                if recorded.get(key) != snapshot.get(key):
                    semantic_differences.append({"path": package["source"]["path"], "field": "/frozen_configuration/" + key})
        if package["kind"] == "image_functional" and package.get("source"):
            manifest = inputs.json(package["source"]["path"])
            if package["status"] != "OBSERVED":
                continue
            comparisons = {
                "/candidate_catalog/catalog_sha256": (manifest.get("candidate_catalog", {}).get("catalog_sha256"), snapshot.get("candidate_catalog", {}).get("file_sha256")),
                "/candidate_catalog/corpus_sha256": (manifest.get("candidate_catalog", {}).get("corpus_sha256"), snapshot.get("candidate_catalog", {}).get("corpus_sha256")),
                "/extractor/extractor_prompt_sha256": (manifest.get("extractor", {}).get("extractor_prompt_sha256"), snapshot.get("prompts", {}).get("P2_extractor", {}).get("prompt_sha256")),
                "/retrieval_configuration": (manifest.get("retrieval_configuration"), snapshot.get("configuration", {}).get("P2")),
                "/constraint_ranking_configuration": (manifest.get("constraint_ranking_configuration"), snapshot.get("configuration", {}).get("constraints")),
            }
            for index in ("dense", "sparse", "hybrid"):
                comparisons["/embedding_indexes/" + index + "_index_sha256"] = (
                    manifest.get("embedding_indexes", {}).get(index + "_index_sha256"),
                    snapshot.get("indexes", {}).get(index, {}).get("index_checksum"))
            for pointer, (actual, expected) in comparisons.items():
                if actual != expected or actual is None:
                    semantic_differences.append({"path": package["source"]["path"], "field": pointer,
                                                 "recorded": actual, "snapshot": expected})
    protected_errors = [f for f in input_failures if f["path"] in inputs.lock["protected_files"]]
    set_check(4, "FAIL" if protected_errors else "UNVERIFIED",
              "Recommender bytes checked against audit-start inventory. No final authority exists to certify confirmatory revisions; audit revision is separate from collection revision.", protected_errors)
    set_check(5, "FAIL" if semantic_differences else "UNVERIFIED",
              "Recorded metadata compared with the design snapshot without rebuilding indexes or invoking recommenders. Snapshot agreement alone cannot certify confirmation.", semantic_differences)
    set_check(7, "UNVERIFIED", "Run analyze to verify raw-to-derived reproduction.")
    set_check(8, "UNVERIFIED", "Run figures to verify derived-to-report reproduction.")

    if historical:
        process = subprocess.run([sys.executable, "scripts/validate-portable-evidence.py"], cwd=inputs.root,
                                 capture_output=True, text=True, check=False)
        try:
            result = json.loads(process.stdout)
        except ValueError:
            result = {"status": "FAIL", "reason": "Historical validator did not return JSON"}
        # Deep sidecar availability is a custody detail, not a portable-core requirement.
        set_check(9, "PASS" if process.returncode == 0 and not protected_errors else "FAIL",
                  "Protocol-v4 portable checksums and reproduced headline values; external deep sidecars remain a separate boundary.",
                  [result], [inputs.ref("docs/evaluation/PROTOCOL_V4_PORTABLE_SHA256SUMS.txt")])

    privacy = []
    execution_errors = []
    cluster_errors = []
    storage_errors = []
    stats_errors = []
    b0_errors = []
    p3_errors = []
    placeholder_errors = []
    retained = authority is not None and authority["configuration_snapshot"]["p3_gate"]["status"] == "retained"
    for package in packages:
        path = safe_path(inputs.root, package["path"])
        if package["kind"] == "user_study":
            from evaluation_v5.user_study.analysis import audit_report_privacy
            for portion, allow in (("raw", True), ("derived", True), ("report", False)):
                # The sealed audit document contains identifier-category names, not
                # participant content. Its bytes are checked by package_integrity.
                files = [p for p in (path / portion).rglob("*") if p.is_file() and p.name != "privacy-audit.json" and p.suffix in (".json", ".jsonl", ".csv", ".md", ".svg")]
                try:
                    privacy.append({"path": package["path"] + "/" + portion,
                                    **audit_report_privacy(files, allow_pseudonyms=allow)})
                except Exception:
                    privacy.append({"path": package["path"] + "/" + portion, "status": "FAIL",
                                    "reason": "Privacy validator rejected content; matched values suppressed."})
        names = {"offline": "recommendations.jsonl", "image_functional": "probe_results.jsonl",
                 "resource_envelope": "trials.jsonl", "resource_efficiency": "trials.jsonl", "user_study": "events.jsonl"}
        raw = path / "raw" / names.get(package["kind"], "__no_raw__")
        if raw.is_file():
            rows = read_rows(raw)
            for code in execution_findings(package["status"], rows, kind=package["kind"]):
                execution_errors.append({"path": package["path"], "code": code})
        if package["kind"] in ("resource_envelope", "resource_efficiency"):
            env_path = path / "raw/environment.json"
            env = read_json(env_path) if env_path.is_file() else {}
            cluster_errors.extend({"path": package["path"], "code": c}
                                  for c in cluster_findings(package["status"], env))
        if package["kind"] == "image_storage":
            layers = read_json(path / "raw/image_layers.json")
            images = layers.get("images", []) if isinstance(layers, dict) else layers
            storage_errors.extend({"path": package["path"], "code": c} for c in storage_identity_findings(images))
        for f in path.rglob("*"):
            if not f.is_file() or f.suffix not in (".json", ".jsonl", ".csv"):
                continue
            # Metric/unit checks inspect result-bearing artifacts, not preregistered thresholds.
            if "derived" not in f.parts and "tables" not in f.parts and f.name not in ("manifest.json", "status.json"):
                continue
            if f.suffix == ".jsonl":
                data = read_rows(f)
            elif f.suffix == ".csv":
                with f.open(newline="") as handle:
                    data = list(csv.DictReader(handle))
            else:
                data = read_json(f)
            ref = str(f.relative_to(inputs.root))
            b0_errors.extend({"path": ref, "code": c} for c in b0_ranking_findings(data))
            stats_errors.extend({"path": ref, "code": c} for c in inference_findings(
                data, experiment=package["experiment"], family_count=package.get("family_count")))
            p3_errors.extend({"path": ref, "locator": c} for c in p3_findings(data, retained))
            placeholder_errors.extend({"path": ref, "locator": c} for c in placeholder_findings(data))
    set_check(10, "FAIL" if any(p["status"] == "FAIL" for p in privacy) else "PASS",
              "Direct-identifier checks applied to available human-study files. No participant sessions were observed; public aggregate reports exclude pseudonyms.", privacy)
    set_check(11, "FAIL" if cluster_errors else "NOT_APPLICABLE",
              "No observed Kubernetes trials exist; readiness identities are not hardware measurements.", cluster_errors)
    set_check(12, "FAIL" if storage_errors else "NOT_APPLICABLE",
              "No storage measurements exist. Functional-probe host metadata does not establish an image platform or storage reuse.", storage_errors)
    set_check(13, "FAIL" if execution_errors else "PASS",
              "Available records checked for missing observations and synthetic/mock origins; v1.0 probes use the existing legacy error-category adapter. This is artifact consistency, not independent attestation of collection.", execution_errors)
    set_check(14, "FAIL" if stats_errors else "PASS",
              "No available v5 inferential p-value was found using repetitions as semantic samples. Family/participant contracts are also checked by the existing claim-registry validator.", stats_errors)
    set_check(15, "FAIL" if b0_errors else "PASS", "Result-bearing JSON, JSONL and CSV artifacts checked for B0 ranking metrics.", b0_errors)
    gate = snapshot.get("p3_gate", {})
    gate_source = gate.get("evidence_path")
    try:
        if gate_source:
            inputs.resolve(gate_source, gate["evidence_sha256"])
    except Exception:
        p3_errors.append({"code": "GATE_EVIDENCE_CHECKSUM_MISMATCH"})
    set_check(16, "FAIL" if p3_errors else "PASS",
              "P2 remains primary; recorded P3 development decision is not_retained. No v5 confirmatory P3 conclusion is authorized.", p3_errors,
              [inputs.ref(gate_source)] if gate_source else [])
    set_check(17, "FAIL" if placeholder_errors else "PASS",
              "Unavailable experiments remain NOT_EXECUTED with null estimates; planned counts and fixture image identifiers are design only.", placeholder_errors)
    registry = load_claim_registry(inputs.path(REGISTRY))
    # This work package closes the current evidence snapshot; it never admits newly supplied confirmation.
    from evaluation_v5.analysis.research_analysis import evaluate_claims
    evaluated = evaluate_claims(registry=registry, selected={}, selection_report={"requirements": []})
    decisions = {c["claim_id"]: c for c in evaluated}
    claims = [{"id": c["id"], "research_question": c["research_question"], "hypothesis": c["hypothesis"],
               "claim_status": decisions[c["id"]]["claim_status"], "estimate": None, "confidence_interval": None,
               "effect_size": None, "reason": "No complete authenticated confirmatory evidence in the reviewed snapshot.",
               "source": inputs.ref(REGISTRY, "/claims/" + str(i))} for i, c in enumerate(registry["claims"])]
    return {"schema_version": SCHEMA_VERSION, "protocol_version": "5.0.0", "checks": list(checks.values()),
            "packages": packages, "claims": claims, "evaluated_claims": evaluated, "research_questions": registry["research_questions"],
            "primary_system": "P2", "confirmatory_status": "NOT_EXECUTED", "source_inventory": inputs.ref(inputs.lock_path) if inputs.lock_path in inputs.files else
            {"path": inputs.lock_path, "sha256": file_sha256(inputs.root / inputs.lock_path)},
            "audit_status": "FAIL" if any(c["verdict"] == "FAIL" for c in checks.values()) else "INCOMPLETE"}
