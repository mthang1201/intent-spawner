"""Regenerate only persisted evidence. Never call experiment collectors."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from .common import Inputs, file_sha256, read_json, read_rows, safe_path, write_json, write_bytes

# These fields describe the new execution, not an empirical estimate. Nothing
# else (including seed, endpoints, missingness, units, or estimates) is removed.
VOLATILE_MANIFEST_FIELDS = ("created_at_utc", "git_revision")


def compare_json(expected: Any, actual: Any, *, ignored_fields: tuple[str, ...] = ()) -> dict:
    if ignored_fields:
        expected = {k: v for k, v in expected.items() if k not in ignored_fields}
        actual = {k: v for k, v in actual.items() if k not in ignored_fields}
    return {"status": "PASS" if expected == actual else "FAIL", "comparison": "exact_parsed_json",
            "ignored_top_level_fields": list(ignored_fields)}


def _regenerate_current_functional(
    inputs: Inputs,
    package: dict,
) -> tuple[dict, list[dict], list[dict]]:
    """Rebuild current E5 rows from its sealed Prompt 3 source snapshot."""
    relative = package["path"]
    package_root = safe_path(inputs.root, relative)
    actual_files = {
        str(path.relative_to(inputs.root))
        for path in package_root.rglob("*")
        if path.is_file()
    }
    registered_files = {
        name for name in inputs.files if name.startswith(relative + "/")
    }
    if actual_files != registered_files:
        raise ValueError(
            "current E5 package files do not exactly match the reviewed input inventory"
        )
    for name in sorted(actual_files):
        inputs.path(name)

    from evaluation_v5.image_storage.contracts import (
        IMAGE_PROBE_MANIFEST_SCHEMA_VERSION,
        ImageProbeResult,
    )
    from evaluation_v5.image_storage.functional_provenance import (
        SOURCE_RECOMMENDATIONS_FILENAME,
        SOURCE_RECOMMENDATION_PROVENANCE_FILENAME,
        canonical_identity_sha256,
        selected_image_identity,
        source_manifest_identities,
        verify_copied_recommendation_source,
    )
    from evaluation_v5.image_storage.metrics import (
        compute_functional_metrics,
        evaluate_recommendation_functional,
    )
    from evaluation_v5.image_storage.validate_evidence import validate_e5_evidence

    validation = validate_e5_evidence(package_root)
    if (
        validation.get("validator_status") != "CURRENT_VALID"
        or validation.get("validation_profile")
        != "CURRENT_V1_4_SEALED_RECOMMENDATION_PROVENANCE"
    ):
        raise ValueError("current E5 regeneration requires a CURRENT_VALID v1.4 package")

    manifest = inputs.json(relative + "/manifest.json")
    if package.get("status", manifest.get("execution_status")) != manifest.get(
        "execution_status"
    ):
        raise ValueError("final-audit package status disagrees with the E5 manifest")
    # Confirmatory custody is intentionally outside this repository audit.
    if manifest["split_identity"]["stage"] != "development":
        raise ValueError("external confirmatory reanalysis is not part of this audit")

    source_provenance_relative = (
        relative + "/raw/" + SOURCE_RECOMMENDATION_PROVENANCE_FILENAME
    )
    source_records_relative = relative + "/raw/" + SOURCE_RECOMMENDATIONS_FILENAME
    source_provenance_path = inputs.path(source_provenance_relative)
    source_records_path = inputs.path(source_records_relative)
    source = verify_copied_recommendation_source(
        provenance_bytes=source_provenance_path.read_bytes(),
        records_bytes=source_records_path.read_bytes(),
    )
    expected_manifest_identities = source_manifest_identities(source)
    for field, expected in expected_manifest_identities.items():
        if manifest.get(field) != expected:
            raise ValueError(
                f"current E5 manifest {field} does not derive from sealed source provenance"
            )

    probe_manifest_relative = relative + "/raw/probe_manifest.json"
    probe_manifest = inputs.json(probe_manifest_relative)
    if probe_manifest.get("schema_version") != IMAGE_PROBE_MANIFEST_SCHEMA_VERSION:
        raise ValueError("current E5 regeneration requires the current probe manifest")
    images = probe_manifest.get("images")
    if not isinstance(images, list):
        raise ValueError("current E5 probe manifest images are malformed")
    catalog_images: dict[str, dict[str, Any]] = {}
    for image in images:
        if not isinstance(image, dict):
            raise ValueError("current E5 probe manifest image is malformed")
        image_id = image.get("image_id")
        if not isinstance(image_id, str) or not image_id or image_id in catalog_images:
            raise ValueError("current E5 probe manifest image identity is invalid")
        catalog_images[image_id] = {
            "reference": image.get("image_reference"),
            "capabilities": list(image.get("documented_capabilities", [])),
        }
    source_candidates = {
        item["candidate_id"]: item
        for item in source.provenance["catalog_identity"]["candidates"]
    }
    if (
        {item["image_id"] for item in source_candidates.values()}
        != set(catalog_images)
        or source.provenance["catalog_identity"]["catalog_version"]
        != probe_manifest.get("catalog_version")
    ):
        raise ValueError(
            "current E5 probe manifest does not match the sealed source catalog identity"
        )
    catalog = {
        "catalog_version": probe_manifest["catalog_version"],
        "images": catalog_images,
    }

    probes_path = inputs.path(relative + "/raw/probe_results.jsonl")
    probes = [ImageProbeResult.from_dict(row) for row in read_rows(probes_path)]
    by_probe = {(r.image_id, r.capability): r for r in probes}
    evaluations = []
    for row in source.records:
        image = row.get("predicted_image_id")
        candidate_id = row.get("predicted_candidate_id")
        gold = row.get("evaluation_gold", {})
        preferred_candidate = source_candidates.get(gold.get("preferred_candidate_id"))
        preferred_image = (
            preferred_candidate.get("image_id")
            if isinstance(preferred_candidate, dict)
            else None
        )
        acceptable_images = [
            source_candidates[item]["image_id"]
            for item in gold.get("acceptable_candidate_ids", [])
            if item in source_candidates
        ]
        selected_digest, selected_platform = selected_image_identity(
            image_id=image,
            catalog=catalog,
            probe_results=probes,
        )
        evaluations.append(evaluate_recommendation_functional(
            case_id=row["case_id"], family_id=row.get("family_id", ""), variant_id=row.get("variant_id", ""),
            system_id=row["system_id"], source_predicted_image_value=image,
            source_predicted_candidate_id=candidate_id,
            source_run_sha256=source.recommendation_run_sha256,
            source_recommendation_record_id=str(row["record_id"]),
            source_configuration_identity_sha256=canonical_identity_sha256(
                source.system_identity(row["system_id"])
            ),
            selected_image_digest=selected_digest,
            selected_image_platform=selected_platform,
            predicted_image_id=image,
            required_capabilities=list(gold.get("required_image_capabilities", [])),
            gold_preferred_image_id=preferred_image,
            gold_acceptable_image_ids=acceptable_images,
            catalog=catalog, probe_results=by_probe, execution_status=manifest["execution_status"]))
    metrics = compute_functional_metrics(evaluations, catalog, probe_results=probes).to_dict()
    comparisons = [
        {"artifact": relative + "/raw/functional_evaluations.jsonl",
         **compare_json(read_rows(inputs.path(relative + "/raw/functional_evaluations.jsonl")), [e.to_dict() for e in evaluations])},
        {"artifact": relative + "/derived/functional_metrics.json",
         **compare_json(inputs.json(relative + "/derived/functional_metrics.json"), metrics)},
    ]
    lineage = [
        inputs.ref(relative + "/manifest.json"),
        inputs.ref(source_provenance_relative),
        inputs.ref(source_records_relative),
        inputs.ref(probe_manifest_relative),
        inputs.ref(relative + "/raw/probe_results.jsonl"),
    ]
    return metrics, comparisons, lineage


def _regenerate_legacy_functional(
    inputs: Inputs,
    package: dict,
) -> tuple[dict, list[dict], list[dict]]:
    """Read-only adapter for the reviewed legacy development packages."""
    relative = package["path"]
    manifest = inputs.json(relative + "/manifest.json")
    environment = inputs.json(relative + "/raw/environment.json")
    predictions = inputs.resolve(environment["recommendations_input_path"], environment["recommendations_input_sha256"])
    catalog_ref = inputs.json(relative + "/raw/probe_manifest.json")
    catalog_path = inputs.resolve(catalog_ref["catalog_path"], catalog_ref["catalog_sha256"])
    catalog = yaml.safe_load(catalog_path.read_text())
    if manifest["split_identity"]["stage"] != "development":
        raise ValueError("external confirmatory reanalysis is not part of this audit")
    split_path = inputs.resolve("benchmarks_v5/v5-development.yaml", manifest["dataset_identity"]["dataset_sha256"])
    cases = {c["case_id"]: c for c in yaml.safe_load(split_path.read_text())["cases"]}
    from evaluation_v5.image_storage.contracts import ImageProbeResult
    from evaluation_v5.image_storage.metrics import evaluate_recommendation_functional, compute_functional_metrics
    probes_path = inputs.path(relative + "/raw/probe_results.jsonl")
    probes = [ImageProbeResult.from_dict(row) for row in read_rows(probes_path)]
    by_probe = {(r.image_id, r.capability): r for r in probes}
    evaluations = []
    def image_component(candidate):
        return candidate.split("-", 1)[-1] if candidate else None
    records = read_rows(predictions)
    for row in records:
        source_value = row.get("predicted_image_id")
        image = source_value
        if source_value is None and row.get("predicted_candidate_id") is not None:
            source_value = row["predicted_candidate_id"]
            image = image_component(source_value)
        if source_value == "":
            source_value = image = None
        gold = row.get("evaluation_gold", {})
        split_gold = cases[row["case_id"]].get("gold", {})
        capabilities = (gold.get("required_image_capabilities") or split_gold.get("required_image_capabilities")
                        or (split_gold.get("expected_extraction") or {}).get("required_libraries")
                        or (row.get("structured_intent") or {}).get("required_libraries") or [])
        evaluations.append(evaluate_recommendation_functional(
            case_id=row["case_id"], family_id=row.get("family_id", ""), variant_id=row.get("variant_id", ""),
            system_id=row["system_id"], source_predicted_image_value=source_value, predicted_image_id=image,
            required_capabilities=capabilities,
            gold_preferred_image_id=image_component(gold.get("preferred_candidate_id")),
            gold_acceptable_image_ids=[image_component(c) for c in gold.get("acceptable_candidate_ids", [])],
            catalog=catalog, probe_results=by_probe, execution_status=manifest["execution_status"]))
    metrics = compute_functional_metrics(evaluations, catalog, probe_results=probes).to_dict()
    comparisons = [
        {"artifact": relative + "/raw/functional_evaluations.jsonl",
         **compare_json(read_rows(inputs.path(relative + "/raw/functional_evaluations.jsonl")), [e.to_dict() for e in evaluations])},
        {"artifact": relative + "/derived/functional_metrics.json",
         **compare_json(inputs.json(relative + "/derived/functional_metrics.json"), metrics)},
    ]
    lineage = [inputs.ref(str(p.relative_to(inputs.root))) for p in (predictions, catalog_path, split_path, probes_path)]
    return metrics, comparisons, lineage


def regenerate_functional(inputs: Inputs, package: dict) -> tuple[dict, list[dict], list[dict]]:
    """Regenerate E5 through the schema-appropriate read-only adapter."""
    from evaluation_v5.image_storage.contracts import IMAGE_PROBE_MANIFEST_SCHEMA_VERSION

    probe_schema = inputs.json(package["path"] + "/raw/probe_manifest.json").get(
        "schema_version"
    )
    validator_status = package.get("validator_result", {}).get("validator_status")
    if validator_status == "CURRENT_VALID" or probe_schema == IMAGE_PROBE_MANIFEST_SCHEMA_VERSION:
        return _regenerate_current_functional(inputs, package)
    return _regenerate_legacy_functional(inputs, package)


def regenerate_user_study(inputs: Inputs, package: dict) -> tuple[dict, list[dict]]:
    relative = package["path"]
    manifest = inputs.json(relative + "/manifest.json")
    # Validate raw hashes independently of the known derived CSV defect.
    for name, expected in manifest["output_checksums"].items():
        if name.startswith("raw/") and inputs.files.get(relative + "/" + name) != expected:
            raise ValueError("human-study raw evidence checksum mismatch")
    raw = {name: read_rows(inputs.path(relative + "/raw/" + name + ".jsonl"))
           for name in ("events", "sessions", "questionnaires", "exclusions")}
    if manifest["execution_status"] != "NOT_EXECUTED" or any(raw.values()):
        raise ValueError("this current-evidence adapter requires the recorded empty study; observed studies require their finalizer")
    from evaluation_v5.user_study.assignment import load_assignment_manifest
    from evaluation_v5.user_study.analysis import analyze_user_study
    assignment = load_assignment_manifest(str(inputs.path(relative + "/raw/assignment-manifest.json")))
    status = inputs.json(relative + "/report/status.json")
    analysis = analyze_user_study(execution_status="NOT_EXECUTED", task_rows=[], questionnaire_rows=[],
                                sessions=raw["sessions"], exclusions=raw["exclusions"], assignment_manifest=assignment,
                                sub_gates=status["sub_gates"])
    comparison = {"artifact": relative + "/derived/analysis.json",
                  **compare_json(inputs.json(relative + "/derived/analysis.json"), analysis)}
    return analysis, [comparison]


def analyze(inputs: Inputs, audit: dict, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    result = {"schema_version": "protocol-v5-final-regeneration-v1.0.0", "packages": [],
              "current_functional": [], "observed_functional": [], "legacy_functional": [],
              "comparisons": [], "observed_offline_counts": [],
              "volatile_manifest_fields": list(VOLATILE_MANIFEST_FIELDS), "claims": audit["claims"],
              "defense_sources": {"human": [], "resources": [], "offline": [], "functional": [],
                                  "storage": [audit["source_inventory"]]}}
    for package in audit["packages"]:
        relative = package["path"]
        entry = {"path": relative, "status": "NOT_EXECUTED", "reason": "No eligible raw observations for this analysis.",
                 "source_validation": package["validation"]}
        destination = output / package["experiment"] / Path(relative).name
        try:
            if package["kind"] == "image_functional" and package["validation"] == "PASS" and package.get("validator_result", {}).get("validator_status") == "CURRENT_VALID":
                metrics, comparisons, sources = regenerate_functional(inputs, package)
                write_json(destination / "functional_metrics.json", metrics)
                entry.update(
                    status="REGENERATED",
                    reason=(
                        "Sealed Prompt 3 recommendation snapshot, source-bound gold, and "
                        "persisted probe records regenerated exactly; no recommender or probe ran."
                    ),
                    comparisons=comparisons,
                )
                result["comparisons"].extend(comparisons)
                validator = package["validator_result"]
                claim_eligible = bool(
                    validator.get("eligible_as_current_e5_evidence")
                    and package["status"] == "OBSERVED"
                    and package["stage"] == "confirmatory"
                    and audit.get("confirmatory_status") == "OBSERVED"
                )
                current = {"run": Path(relative).name, "source_package": relative,
                    "execution_status": package["status"], "stage": package["stage"], "metrics": metrics,
                    "sources": sources, "metric_source": inputs.ref(relative + "/derived/functional_metrics.json"),
                    "claim_eligible": claim_eligible,
                    "confirmatory_eligible": claim_eligible,
                    "provenance_boundary": "SEALED_SOURCE_PROVENANCE_VERIFIED"}
                result["current_functional"].append(current)
                if package["status"] == "OBSERVED":
                    result["observed_functional"].append(current)
                    result["defense_sources"]["functional"].append(inputs.ref(relative + "/derived/functional_metrics.json", "/systems"))
            elif package["kind"] == "offline" and package["validation"] == "PASS":
                rows = read_rows(inputs.path(relative + "/raw/recommendations.jsonl"))
                counts = {"run": Path(relative).name, "records": len(rows), "cases": len({r["case_id"] for r in rows}),
                          "families": len({r["family_id"] for r in rows}), "per_system": dict(sorted(Counter(r["system_id"] for r in rows).items())),
                          "stage": package["stage"], "source": inputs.ref(relative + "/raw/recommendations.jsonl")}
                write_json(destination / "raw_counts.json", counts)
                result["observed_offline_counts"].append(counts)
                result["defense_sources"]["offline"].append(inputs.ref(relative + "/derived/statistical_analysis/analysis-manifest.json", "/status"))
                from evaluation_v5.analysis.component_scoring import ComponentAnalysisError, load_component_gold, write_not_executed as component_status
                from evaluation_v5.analysis.statistical_analysis import write_not_executed as statistical_status
                try:
                    load_component_gold(inputs.path("benchmarks_v5/v5-development.yaml"), role="development")
                except ComponentAnalysisError:
                    pass
                else:
                    raise ValueError("gold contract changed: current snapshot requires a new reviewed regeneration adapter")
                comparisons = []
                for folder, writer in (("component_scoring", component_status), ("statistical_analysis", statistical_status)):
                    source = inputs.json(relative + "/derived/" + folder + "/analysis-manifest.json")
                    if source["status"] != "NOT_EXECUTED":
                        raise ValueError("unsupported observed offline analysis in this snapshot")
                    writer(destination / folder, reason=source["reason"], reason_code=source["reason_code"])
                    comparisons.append({"artifact": relative + "/derived/" + folder + "/analysis-manifest.json",
                        **compare_json(source, read_json(destination / folder / "analysis-manifest.json"), ignored_fields=VOLATILE_MANIFEST_FIELDS)})
                result["comparisons"].extend(comparisons)
                entry.update(reason="Raw execution counts reproduced; incomplete v1 gold prevents component/statistical inference.", comparisons=comparisons)
            elif package["kind"] == "user_study":
                result["defense_sources"]["human"].append(inputs.ref(relative + "/report/status.json", "/execution_status"))
                analysis, comparisons = regenerate_user_study(inputs, package)
                write_json(destination / "analysis.json", analysis)
                result["comparisons"].extend(comparisons)
                entry.update(reason="Empty raw study and assignment regenerated; original CSV checksum failure remains unchanged.", comparisons=comparisons,
                             generated_analysis=str((destination / "analysis.json").relative_to(output)))
            elif package["kind"] == "image_functional":
                entry.update(reason="Legacy or invalid functional package retained; not silently reinterpreted under current metric semantics.", status="UNVERIFIED")
                validator = package.get("validator_result", {})
                if package["validation"] == "PASS" and validator.get("validator_status") == "LEGACY_VALID":
                    result["legacy_functional"].append(
                        {
                            "run": Path(relative).name,
                            "source_package": relative,
                            "recorded_status": package["status"],
                            "validation_profile": validator.get("validation_profile"),
                            "limitations": list(validator.get("limitations", [])),
                            "claim_eligible": False,
                        }
                    )
            elif package["kind"] == "research_analysis":
                entry.update(reason="Historical aggregate analysis preserved and reference checksums validated; current claim inventory is regenerated separately.", status="PRESERVED")
            elif package["kind"] == "resource_plan":
                result["defense_sources"]["resources"].append(inputs.ref(relative + "/plan.json", "/primary_trial_count"))
        except Exception as exc:
            # Do not erase collected evidence or invent a fallback estimate.
            entry.update(status="FAIL", reason=str(exc).replace(str(inputs.root) + "/", ""))
        result["packages"].append(entry)
    result["status"] = "FAIL" if any(c["status"] == "FAIL" for c in result["comparisons"]) or any(
        p["status"] == "FAIL" for p in result["packages"]) else "PASS_WITH_UNAVAILABLE_ANALYSES"
    write_json(output / "regeneration.json", result)
    return result
