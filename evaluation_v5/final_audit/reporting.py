"""Deterministic tables, figures and bounded narrative from audited inputs."""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path

from .common import Inputs, read_json, write_bytes, write_json, file_sha256


def table(rows: list[list], headers: list[str]) -> str:
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"] +
                     ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]) + "\n"


def functional_rows(analysis: dict) -> list[dict]:
    rows = []
    for run in analysis["observed_functional"]:
        for system, metric in sorted(run["metrics"]["systems"].items()):
            rows.append({"run": run["run"], "system": system, "stage": run["stage"],
                         "recommendations": metric["total_recommendations"], "gold_image_matches": metric["gold_acceptable_count"],
                         "eligible_functional_cases": metric["functional_validation_eligible_count"],
                         "functional_passes": metric["functional_passed_count"],
                         "undefined_required_probe_cases": metric["required_probe_not_defined_count"],
                         "probe_passes": run["metrics"]["probe_summary"]["probes_passed"],
                         "configured_probes": run["metrics"]["probe_summary"]["total_probes_configured"],
                         "source": run["metric_source"]})
    return rows


def _compact(value) -> str:
    if value is None or value == {} or value == []:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _claim_result(claim: dict) -> str:
    metrics = claim.get("normalized_metrics") or {}
    return claim["claim_status"] + (("; " + _compact(metrics)) if metrics else "")


def defense_rows(analysis: dict, format_source=None) -> list[list]:
    claims = {row["id"]: row for row in analysis.get("claims", [])}

    def outcome(claim_id: str) -> str:
        return _claim_result(claims[claim_id]) if claim_id in claims else "UNSUPPORTED"

    def claim_ref(claim_id: str) -> str:
        if claim_id not in claims:
            return "N/A"
        ref = claims[claim_id]["source"]
        formatter = format_source or (
            lambda row: row["path"] + " " + row.get("locator", "") + " SHA-256 " + row["sha256"]
        )
        return formatter(ref)

    return [
        ["satisfaction", "E3", "SEQ ease; SUS usability", outcome("H4"), claim_ref("H4")],
        ["time saving", "E3", "Paired decision time and selection effectiveness", outcome("H3"), claim_ref("H3")],
        ["correct image", "E5 functional", "Gold image match; required functional probes", outcome("H7F"), claim_ref("H7F")],
        ["image storage reuse", "E5 storage", "LogicalImageBytes; UniqueLayerBytes; marginal reuse", outcome("H7"), claim_ref("H7")],
        ["additional/fine-grained profiles", "E4", "Dynamic CPU/memory oracle error; allocation coverage", outcome("H6"), claim_ref("H6")],
        ["resource saving", "E4", "CPU/memory request cost per successful workload; reliability", outcome("H5"), claim_ref("H5")],
        ["flexible natural-language interaction", "E2", "Family-level robustness across equivalent variants", outcome("H2"), claim_ref("H2")],
    ]


def render_report(inputs: Inputs, audit: dict, analysis: dict, figures: dict, target: Path) -> str:
    def source(ref):
        relative = ref["path"]
        link = os.path.relpath(inputs.root / relative, target.parent)
        locator = ref.get("locator", "")
        return f"[{relative}]({link}){(' `' + locator + '`') if locator else ''}; SHA-256 `{ref['sha256']}`"
    if audit.get("input_integrity_blocked"):
        return "\n".join(["# Protocol-v5 Final Reproducibility and Evidence Report", "",
            "**Audit verdict: FAIL. Input integrity blocked dependent verification.**", "",
            "No empirical result or execution status can be certified from the changed/missing inputs. "
            "No sealed dataset or dependent source content was opened. Restore the exact reviewed bytes "
            "or review a new evidence inventory; do not substitute placeholders.", "",
            table([[c["id"], c["title"], c["verdict"], c["reason"]] for c in audit["checks"]],
                  ["ID", "Requirement", "Verdict", "Reason"]), "",
            "Inspect validation/audit.json for content-free missing/checksum findings. Every preserved source remains unchanged.", ""])
    counts = audit.get("claim_counts") or {}
    claim_sources = audit.get("claim_evidence_sources") or {}
    lines = ["# Protocol-v5 Final Reproducibility and Evidence Report", "",
        f"**Audit verdict: {audit['audit_status']}. Confirmatory evidence: {audit['confirmatory_status']}. Primary proposed method: {audit['primary_system']}.**", "",
        "This report is rendered from the checksum-bound input inventory and the explicitly selected evaluated-claim package. It does not rerun claim evaluation or infer a result from a filename, timestamp, or missing value.", "",
        "Input inventory: " + source(audit["source_inventory"]), "",
        f"The reviewed inventory was captured at `{inputs.lock['created_at_utc']}` from source revision `{inputs.lock['source_git_revision']}`. Historical bytes remain unchanged; current audit runtime provenance is recorded separately in run.json and the sealed stage manifests.", ""]
    if claim_sources.get("package"):
        lines += ["Authenticated claim-analysis package: " + source(claim_sources["package"]),
                  "Evidence selection: " + source(claim_sources["selection"]),
                  "Evaluated registry: " + source(claim_sources["evaluated_claims"]), ""]
    else:
        lines += ["Claim evidence status: **UNSUPPORTED**. " + _compact(claim_sources.get("error")), ""]
    lines += ["## Research questions and hypotheses", ""]
    if audit.get("audit_output_relative_path"):
        base = inputs.root / audit["audit_output_relative_path"]
        lines[8:8] = ["Archived audit outputs: "
                      + f"[all findings]({os.path.relpath(base / 'report/audit.json', target.parent)}), "
                      + f"[run provenance]({os.path.relpath(base / 'run.json', target.parent)}), "
                      + f"[regeneration comparisons]({os.path.relpath(base / 'analysis/regeneration.json', target.parent)}).", ""]
    lines.append(table([[q["id"], q["question"]] for q in audit["research_questions"]], ["Question", "Definition"]))
    lines.append(table([[c["id"], c["research_question"], c["hypothesis"], c["claim_status"],
                         _compact(c.get("normalized_metrics")), "; ".join(c.get("reason_codes") or []) or "—"]
                        for c in audit["claims"]],
                       ["Hypothesis", "RQ", "Predeclared statement", "Decision", "Validated result", "Reason codes"]))
    lines += [f"Authenticated decision counts: SUPPORTED={counts.get('SUPPORTED', 0)}, NOT_SUPPORTED={counts.get('NOT_SUPPORTED', 0)}, NOT_EXECUTED={counts.get('NOT_EXECUTED', 0)}. A NOT_EXECUTED decision is neither a zero effect nor evidence against the hypothesis.", "",
        "## Systems", "",
        table([["B0", "Ordinary/manual JupyterHub selection; no recommendation ranking."],
               ["P1", "Frozen existing rule-based recommender."],
               ["P2", "Structured Intent + hybrid retrieval + deterministic constraints/ranking; main proposed method."],
               ["P3", "P2 plus grounded LLM reranking; authenticated state: " + audit.get("p3_state", "UNSUPPORTED") + "."]], ["System", "Definition"]),
        "B0 has no MRR, nDCG or Hit@K outcome. Artifact names containing 'observed-run' do not determine execution status.", "",
        "## Experiment matrix and sample boundaries", "",
        table([[row["experiment"], row["requirement_id"], row["status"], row["candidate_count"],
                row["eligible_candidate_count"], "; ".join(row["reason_codes"]) or "—"]
               for row in audit.get("experiment_states", [])],
              ["Experiment", "Evidence requirement", "Derived state", "Candidates", "Eligible", "Reason codes"])]
    for counts in analysis["observed_offline_counts"]:
        lines += [f"E1 raw execution: **{counts['records']} records**, **{counts['cases']} cases**, **{counts['families']} workload families**; "
                  + ", ".join(f"{s}: {n} records" for s, n in counts["per_system"].items()) + ". These are development observations, not confirmatory accuracy samples. " + source(counts["source"]), ""]
    for p in audit["packages"]:
        if p["kind"] == "resource_plan" and p["validation"] == "PASS":
            plan = inputs.json(p["path"] + "/plan.json")
            lines += [f"E4 **design only**: {plan['family_count']} workload families × {len(plan['conditions'])} conditions × {plan['repetitions']} repetition blocks = {plan['primary_trial_count']} planned trials. Zero observed hardware trials. " + source(inputs.ref(p["path"] + "/plan.json", "/trials")), ""]
    lines += ["## Methods and statistical boundaries", "",
        "Only a validated claim package can produce SUPPORTED or NOT_SUPPORTED. Its exact metric values, confidence intervals, counts, effect sizes, tests, reason codes, and lineage are retained in the evaluated registry linked above.", "",
        "The workload family is the semantic unit for offline and resource inference. Variants and repeated executions describe within-family robustness or stability, not additional independent accuracy samples. E3 uses its frozen participant/task pairing contract. B0 never produces ranking metrics.", "",
        "Development, historical, synthetic, dry-run, incomplete, and invalid packages may remain visible for traceability, but they cannot be promoted to confirmatory claim evidence.", "",
        "## Exact available development observations", ""]
    rows = functional_rows(analysis)
    lines.append(table([[r["run"], r["system"], f"{r['gold_image_matches']}/{r['recommendations']}",
                        f"{r['functional_passes']}/{r['eligible_functional_cases']}", r["undefined_required_probe_cases"]]
                       for r in rows], ["Run", "System", "Image-label matches", "Functional passes / eligible cases", "Cases with undefined required probe"]))
    if not rows:
        lines += ["Current v1.4 functional observations: **NOT EXECUTED**.", ""]
        current_non_observed = analysis.get("current_functional", [])
        if current_non_observed:
            lines += ["Current non-observed packages were reproducible but remain non-claimable:", "",
                      table([[item["run"], item["execution_status"], item["provenance_boundary"], item["claim_eligible"]]
                             for item in current_non_observed],
                            ["Current run", "Execution status", "Provenance", "Claim eligible"]), ""]
        if analysis.get("legacy_functional"):
            lines += ["Archived packages remain available with these explicit limitations:", "",
                      table([[item["run"], item["recorded_status"], item["validation_profile"], ", ".join(item["limitations"])]
                             for item in analysis["legacy_functional"]],
                            ["Archived run", "Recorded status", "Validation profile", "Limitations"]), ""]
    for run in analysis["observed_functional"]:
        probe = run["metrics"]["probe_summary"]
        lines += [f"`{run['run']}`: {probe['probes_passed']}/{probe['total_probes_configured']} configured probes passed, "
                  f"{probe['probes_failed']} failed, {probe['probes_unavailable']} unavailable. These probes are reused when evaluating recommendations. "
                  + source({**run["metric_source"], "locator": "/probe_summary"}), ""]
        for system, m in sorted(run["metrics"]["systems"].items()):
            lines += [f"{system}: conservative functional success {m['functional_passed_count']}/{m['total_recommendations']} "
                      f"(recorded rate {m['conservative_functional_success_rate']}); catalog-underclaim cases {m['catalog_underclaim_count']}; "
                      f"label-fail/functional-pass cases {m['label_fail_functional_pass_count']}. "
                      + source({**run["metric_source"], "locator": "/systems/" + system}), ""]
    lines += ["These development observations are descriptive only. Their presence cannot change the authenticated confirmatory decisions above.", "",
        "### Evidence-driven claim conclusions", ""]
    for claim in audit["claims"]:
        lines += [f"**{claim['id']} — {claim['claim_status']}**. Validated metrics: `{_compact(claim.get('normalized_metrics'))}`. "
                  f"Confidence intervals: `{_compact(claim.get('confidence_intervals'))}`. Counts: `{_compact(claim.get('counts'))}`. "
                  f"Effect sizes: `{_compact(claim.get('effect_sizes'))}`. Reason codes: `{'; '.join(claim.get('reason_codes') or []) or 'none'}`. "
                  + source(claim["source"]), ""]
    regeneration = inputs.lock.get("e3_readiness_regeneration_package")
    if regeneration:
        lines += ["### Historical E3 newline compatibility", "",
                  "The preserved E3 participant-flow CSV and its historical manifest identity were not rewritten. A versioned package records the current regenerated table under the LF policy plus both preserved identities: "
                  + source(inputs.ref(regeneration + "/manifest.json")) + ".", ""]
    scan = audit.get("synthetic_origin_scan") or {}
    lines += ["### Synthetic-origin scan", "",
        f"Collector-origin scan: **{scan.get('status', 'UNSUPPORTED')}** across {scan.get('candidate_count', 0)} discovered candidates; synthetic candidates={scan.get('synthetic_candidate_count', 0)}, promoted synthetic candidates={scan.get('promoted_synthetic_count', 0)}, unauthenticated exposed candidates={scan.get('unauthenticated_exposed_count', 0)}. The scan uses collector provenance and claim eligibility, not filenames.", "",
        "### Isolation parser diagnostic", ""]
    isolation = audit.get("isolation_diagnostic") or {}
    finding = isolation.get("finding") or {}
    repair = isolation.get("repair") or {}
    if isolation.get("source"):
        lines += [
            f"Prior failure classification: **{finding.get('classification', 'UNCLASSIFIED')}**; repair status: **{repair.get('status', 'UNVERIFIED')}**. "
            f"Artifact `{finding.get('artifact_relative_path', 'unknown')}` at observed SHA-256 `{finding.get('artifact_sha256', 'unknown')}` "
            f"was a `{finding.get('artifact_role', 'unknown')}` / `{finding.get('artifact_type', 'unknown')}`. "
            f"Parser `{finding.get('parser', 'unknown')}` encountered schema signature `{finding.get('schema_signature', 'unknown')}` "
            f"and produced `{finding.get('failure_category', 'unknown')}`. Historical={str(finding.get('historical')).lower()}, "
            f"immutable-preserved-evidence={str(finding.get('immutable_preserved_evidence')).lower()}, "
            f"confirmatory-eligible={str(finding.get('eligible_for_confirmatory_execution')).lower()}, "
            f"thesis-claim-eligible={str(finding.get('eligible_to_support_thesis_claim')).lower()}. "
            + source(isolation["source"]),
            "",
        ]
    else:
        lines += ["Isolation diagnostic: **UNAVAILABLE**.", ""]
    lines += [
        "### P3 state", "",
        "Authenticated P3 state: **" + audit.get("p3_state", "UNSUPPORTED") + "**. Historical P3 material remains formative unless the selected claim package contains a validated retained-gate confirmatory decision. " + source(inputs.ref("docs/evaluation/P3_INCREMENTAL_EVALUATION_V1.md")), "",
        "## Defense-summary table", "", table(defense_rows(analysis, source), ["Professor criterion", "Experiment", "Metric", "Observed result", "Evidence reference"]),
        "## Seventeen audit checks", "",
        table([[c["id"], c["title"], c["verdict"], c["reason"]] for c in audit["checks"]], ["ID", "Requirement", "Verdict", "Evidence boundary"]),
        "Detailed per-file errors, source hashes, privacy results and provenance differences are in the generated validation/audit JSON. An INCOMPLETE or FAIL audit does not authorize empirical claims.", "",
        "## Complete package inventory", "",
        table([[p["path"].split("/")[-1], p["kind"], p["status"], p["stage"], p["validation"],
                "; ".join(e.get("reason", e["code"]) for e in p["errors"]) or "—"] for p in audit["packages"]],
              ["Package", "Kind", "Recorded status", "Stage", "Validation", "Failure / limitation"]),
        "Package names above are entries in the reviewed input inventory; every constituent file has a SHA-256. Filesystem ordering and timestamps never select claim evidence.", "",
        "## Threats to validity and evidence boundaries", "",
        "Claim-specific limitations are copied from the authenticated evaluated registry. No narrative sentence can override a machine-readable status, decision predicate, or reason code.", "",
        table([[claim["id"], limitation.get("code"), limitation.get("severity"), limitation.get("statement")]
               for claim in audit["claims"] for limitation in claim.get("limitations") or []],
              ["Claim", "Limitation", "Severity", "Recorded statement"]), "",
        "Repository/archive isolation scanning cannot replace external custody attestation. Functional image checks, storage measurements, user outcomes, resource outcomes, and recommendation quality remain distinct constructs.", "",
        "Historical Protocol-v4 evidence remains historical/formative. Its portable checksum/headline reproduction passes independently of the v5 verdict; external deep-archive sidecars are not required for the portable workflow. Raw observations, derived metrics and report interpretation remain separate.", "",
        "## Reproducibility", "",
        "Use the pinned `requirements-dev.txt` / `requirements-analysis.txt` environment (CPython 3.12–3.14; source observation provenance specifies its actual Python). Run from the repository root:", "",
        "```bash\npython3 -m venv .venv\n.venv/bin/python -m pip install -r requirements-dev.txt\nmake v5-audit\n```", "",
        "For inspectable separate stages, reuse one ID:", "",
        "```bash\nexport V5_RUN_ID=review-20260907-01\nmake v5-validate\nmake v5-analyze\nmake v5-figures\nmake v5-audit\n```", "",
        "Each command creates only missing stages under `results_v5/protocol-v5.0.0/final-audit/<run-id>/`; completed stages are checksum-verified before reuse. A changed input lock or implementation requires a new ID. `make v5-audit` writes the report before returning nonzero for integrity failures. Valid NOT_EXECUTED evidence does not itself cause a nonzero exit.", "",
        "No reproduction command runs recommenders, LLM providers, container probes, registry pulls or Kubernetes jobs. All raw inputs are the privacy-reviewed allowlisted files. Legacy absolute references resolve only through checksum-bound mappings; they are never edited. Missing external evidence remains unavailable.", "",
        "The run contains validation findings, regenerated derived artifacts, JSON/CSV tables, deterministic SVGs, exact reproduction comparisons, manifests and SHA256SUMS. Stage manifests bind input inventory, code hashes and runtime. Regeneration ignores only `created_at_utc` and `git_revision` when comparing status-manifest semantics; all empirical values and other fields must match. Figures use deterministic SVG metadata and fresh output directories.", "",
        "Read-only historical validation and focused tests:", "",
        "```bash\n.venv/bin/python scripts/validate-portable-evidence.py\n.venv/bin/python -m evaluation_v5.isolation_audit\n.venv/bin/python -m pytest -q tests/test_protocol_v5_final_audit.py\n```", ""]
    return "\n".join(lines)


def figures(inputs: Inputs, analysis: dict, analysis_root: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    rows = functional_rows(analysis)
    write_json(output / "tables/functional-results.json", rows)
    if rows:
        buffer = io.StringIO(newline="")
        fields = [k for k in rows[0] if k != "source"]
        writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        write_bytes(output / "tables/functional-results.csv", buffer.getvalue().encode())
    defense = defense_rows(analysis)
    write_json(output / "tables/defense-summary.json", defense)
    write_bytes(output / "tables/defense-summary.md", table(defense, ["Professor criterion", "Experiment", "Metric", "Observed result", "Evidence reference"]).encode())
    comparisons = []
    for entry in analysis["packages"]:
        if entry.get("generated_analysis"):
            from evaluation_v5.user_study.analysis import write_analysis_artifacts
            data = read_json(analysis_root / entry["generated_analysis"])
            target = output / "E3" / Path(entry["path"]).name
            write_analysis_artifacts(target, data)
            for f in sorted((target / "report").rglob("*")):
                if not f.is_file() or f.suffix not in (".svg", ".csv"):
                    continue
                relative = entry["path"] + "/" + str(f.relative_to(target))
                baseline = relative
                reason = "New derivative retained separately; original never changed."
                comparison = "exact_bytes"
                preserved_original_sha256 = None
                if f.name == "participant-flow.csv":
                    raw = f.read_bytes()
                    normalized = raw.replace(b"\r\n", b"\n")
                    if raw != normalized:
                        f.write_bytes(normalized)
                    compatibility = inputs.lock.get("e3_readiness_regeneration_package")
                    if not isinstance(compatibility, str):
                        raise ValueError("versioned E3 newline compatibility package is not selected")
                    baseline = compatibility + "/" + str(f.relative_to(target))
                    preserved_original_sha256 = inputs.files.get(relative)
                    reason = (
                        "Current regeneration is normalized to LF and compared with the versioned compatibility artifact; "
                        "the historical artifact and identity remain unchanged."
                    )
                expected = inputs.files.get(baseline)
                actual = file_sha256(f)
                comparisons.append({"artifact": relative, "baseline_artifact": baseline,
                                    "comparison": comparison, "status": "PASS" if actual == expected else "FAIL",
                                    "original_sha256": expected, "regenerated_sha256": actual,
                                    "preserved_original_sha256": preserved_original_sha256,
                                    "reason": reason})
    if rows:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        with matplotlib.rc_context({"svg.hashsalt": "protocol-v5-final-audit-v1", "font.family": "DejaVu Sans"}):
            figure, axis = plt.subplots(figsize=(10, 5))
            labels = [r["run"].removeprefix("e5-image-validation-") + "\n" + r["system"] for r in rows]
            heights = [r["gold_image_matches"] / r["recommendations"] for r in rows]
            bars = axis.bar(labels, heights, color=["#667085" if r["system"] == "P1" else "#175cd3" for r in rows])
            for bar, r in zip(bars, rows):
                axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + .02,
                          f"{r['gold_image_matches']}/{r['recommendations']}", ha="center")
            axis.set_ylim(0, 1)
            axis.set_ylabel("Image-label agreement (descriptive proportion)")
            axis.set_title("Development observations — separate runs, no pooled inference")
            figure.text(.5, .01, "Development-only evidence; no confirmatory claim or confidence interval.", ha="center", fontsize=9)
            figure.tight_layout(rect=(0, .07, 1, 1))
            figure.savefig(output / "functional-development.svg", metadata={"Date": None})
            figure.savefig(output / "functional-development.png", dpi=160,
                           metadata={"Software": "Protocol-v5 final audit"})
            plt.close(figure)
    result = {"schema_version": "protocol-v5-final-figures-v1.0.0", "comparisons": comparisons,
              "status": "FAIL" if any(c["status"] == "FAIL" for c in comparisons) else "PASS",
              "generated_tables": ["functional-results.json", "defense-summary.json"],
              "source": "Authenticated regenerated derived artifacts; no raw observation or collector access by renderers."}
    write_json(output / "figure-regeneration.json", result)
    return result
