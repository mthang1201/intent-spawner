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


def defense_rows(analysis: dict, format_source=None) -> list[list]:
    current = functional_rows(analysis)
    images = "; ".join(f"{row['run']}: {row['system']} {row['gold_image_matches']}/{row['recommendations']} image-label matches"
                       for row in current) or "NOT EXECUTED"
    def refs(key):
        formatter = format_source or (lambda r: r["path"] + " " + r.get("locator", "") + " SHA-256 " + r["sha256"])
        return "; ".join(formatter(r) for r in analysis["defense_sources"][key])
    return [
        ["satisfaction", "E3", "SEQ ease; SUS usability", "NOT EXECUTED", refs("human")],
        ["time saving", "E3", "Paired decision time", "NOT EXECUTED", refs("human")],
        ["correct image", "E5 functional", "Gold image match; required functional probes", images + "; development only; provenance limitations apply", refs("functional")],
        ["image storage reuse", "E5 storage", "LogicalImageBytes; UniqueLayerBytes; marginal reuse", "NOT EXECUTED", refs("storage")],
        ["additional/fine-grained profiles", "E4", "Dynamic CPU/memory oracle error; allocation coverage", "NOT EXECUTED", refs("resources")],
        ["resource saving", "E4", "CPU/memory request cost per successful workload; reliability", "NOT EXECUTED", refs("resources")],
        ["flexible natural-language interaction", "E2", "Family-level robustness across equivalent variants", "NOT EXECUTED", refs("offline")],
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
    lines = ["# Protocol-v5 Final Reproducibility and Evidence Report", "",
        f"**Audit verdict: {audit['audit_status']}. Confirmatory experiments: NOT EXECUTED. Primary proposed method: P2.**", "",
        "This report closes the reviewed repository evidence snapshot. Audit completion does not certify that every requirement passed. Design, genuine development execution, historical evidence and confirmatory evidence are separated below.", "",
        "Input inventory: " + source(audit["source_inventory"]), "",
        f"The inventory was captured at `{inputs.lock['created_at_utc']}` from Git revision `{inputs.lock['source_git_revision']}`. "
        "It preserves the bytes found, including damaged or incomplete packages; it is not a retroactive experiment freeze. Each source manifest records its own collection revision, timestamp, dataset and environment. Audit implementation/runtime provenance is recorded in the generated run and stage manifests.", "",
        "## Research questions and hypotheses", ""]
    if audit.get("audit_output_relative_path"):
        base = inputs.root / audit["audit_output_relative_path"]
        lines[8:8] = ["Archived audit outputs: "
                      + f"[all findings]({os.path.relpath(base / 'report/audit.json', target.parent)}), "
                      + f"[run provenance]({os.path.relpath(base / 'run.json', target.parent)}), "
                      + f"[regeneration comparisons]({os.path.relpath(base / 'analysis/regeneration.json', target.parent)}).", ""]
    lines.append(table([[q["id"], q["question"]] for q in audit["research_questions"]], ["Question", "Definition"]))
    lines.append(table([[c["id"], c["research_question"], c["hypothesis"], c["claim_status"]] for c in audit["claims"]],
                       ["Hypothesis", "RQ", "Predeclared statement", "Confirmatory decision"]))
    lines += ["Hypotheses and decision predicates are reused from the checksum-bound claim registry. None has sufficient authenticated confirmatory evidence for SUPPORTED or NOT_SUPPORTED. Missing results do not contradict a hypothesis.", "",
        "## Systems", "",
        table([["B0", "Ordinary/manual JupyterHub selection; no recommendation ranking."],
               ["P1", "Frozen existing rule-based recommender."],
               ["P2", "Structured Intent + hybrid retrieval + deterministic constraints/ranking; main proposed method."],
               ["P3", "P2 plus grounded LLM reranking; not retained by the development decision."]], ["System", "Definition"]),
        "B0 has no MRR, nDCG or Hit@K outcome. Artifact names containing 'observed-run' do not determine execution status.", "",
        "## Experiment matrix and sample boundaries", "",
        table([["E1", "P1 vs P2", "Development raw outputs available; component/statistical analysis NOT EXECUTED"],
               ["E2", "P1 vs P2 natural-language variants", "Formal robustness analysis NOT EXECUTED"],
               ["E3", "B0 vs P2 human crossover", "NOT EXECUTED; zero participant sessions"],
               ["E4", "Static Large, P1 Catalog, P2 Catalog, P2 Dynamic", "NOT EXECUTED; planning/readiness packages only"],
               ["E5 functional", "Image labels, catalog capabilities, container probes", "Development observations; unresolved manifest provenance"],
               ["E5 storage", "Shared-layer reuse and catalog expansion", "NOT EXECUTED"],
               ["E6", "Optional P2 vs P3 confirmation", "NOT EXECUTED; P3 not retained"]], ["Experiment", "Comparison", "Evidence status"])]
    for counts in analysis["observed_offline_counts"]:
        lines += [f"E1 raw execution: **{counts['records']} records**, **{counts['cases']} cases**, **{counts['families']} workload families**; "
                  + ", ".join(f"{s}: {n} records" for s, n in counts["per_system"].items()) + ". These are development observations, not confirmatory accuracy samples. " + source(counts["source"]), ""]
    for p in audit["packages"]:
        if p["kind"] == "resource_plan" and p["validation"] == "PASS":
            plan = inputs.json(p["path"] + "/plan.json")
            lines += [f"E4 **design only**: {plan['family_count']} workload families × {len(plan['conditions'])} conditions × {plan['repetitions']} repetition blocks = {plan['primary_trial_count']} planned trials. Zero observed hardware trials. " + source(inputs.ref(p["path"] + "/plan.json", "/trials")), ""]
    lines += ["E3 target enrollment is 36 participants in the readiness design; observed enrollment and measured outcomes are zero. Assignments and synthetic smoke actions are not participant observations.", "",
        "## Methods and statistical boundaries", "",
        "E1 preserves paired outputs from frozen P1/P2 on the visible development split. The raw validator recomputes matrix coverage, checksums and case bindings. The current complete component/statistical pipeline requires frozen family gold or compiled split v2; the visible v1 bundle is insufficient. The audit does not fill its missing labels.", "",
        "E5 regeneration rejoins original recommendations, visible gold, catalog metadata and preserved immutable-image probe outcomes. It independently recomputes functional evaluation rows and aggregate metrics. Gold-label agreement, catalog capability declarations and in-container functional success are different constructs. A passing import probe is not proof of GPU hardware, workload success, correct resource allocation, image storage savings or overall recommendation quality.", "",
        "The workload family is the semantic unit for offline/resource inference. Variants and repeated calls describe within-family variation. Human study analysis follows its participant/task pairing and crossover contract. Image probes share digests across recommendations and are not independent human or semantic samples. The two current E5 runs are reported separately and never pooled.", "",
        "## Exact available development observations", ""]
    rows = functional_rows(analysis)
    lines.append(table([[r["run"], r["system"], f"{r['gold_image_matches']}/{r['recommendations']}",
                        f"{r['functional_passes']}/{r['eligible_functional_cases']}", r["undefined_required_probe_cases"]]
                       for r in rows], ["Run", "System", "Image-label matches", "Functional passes / eligible cases", "Cases with undefined required probe"]))
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
    lines += ["The image-label match counts do not show an advantage for P2 in these runs. This is a bounded descriptive observation, not a family-level hypothesis test. The extractor provenance mismatch blocks claims that the complete pipeline matched a final freeze.", "",
        "### Confidence intervals and effect sizes", "",
        "Protocol-v5 inferential confidence intervals, p-values and standardized effect sizes: **N/A — NOT EXECUTED**. Complete offline gold is unavailable; human, resource and storage experiments were not executed. No interval is inferred from repetitions or recycled probes.", "",
        "### Failure analysis and P3 decision", "",
        "The E3 participant-flow CSV no longer matches its recorded checksum. In-memory LF→CRLF reconstruction matches the historical digest, consistent with the repository CSV newline policy. Original bytes and checksums are preserved; regeneration creates a separate corrected artifact. Older E4 contracts fail current validators and remain historical development packages. An older E5 OBSERVED package lacks required retrieval provenance. Its v1.0 probe records predate execution_status and must be interpreted with the legacy error-category adapter; missing fields do not mean missing executions. These are audit limitations and failures, not inferred performance effects.", "",
        "The preserved P3 development/formative decision excludes P3 from the main contribution: its historical evaluation reported no wrong-to-correct transitions, one regression, and substantial reranking overhead. This audit does not relabel that earlier evaluation as Protocol-v5 confirmation. " + source(inputs.ref("docs/evaluation/P3_INCREMENTAL_EVALUATION_V1.md")), "",
        "## Human, resource and image-storage outcomes", "",
        "Human study: **NOT EXECUTED**. Satisfaction, usability, decision-time saving and participant selection outcomes are unavailable. Resource study: **NOT EXECUTED**. CPU/memory savings, capacity, OOM and runtime effects are unavailable. Image storage study: **NOT EXECUTED**. No measured logical bytes, unique layer bytes, node storage use or expansion savings exist. The functional image manifests record digests, but host metadata alone does not establish container platform identity.", "",
        "## Defense-summary table", "", table(defense_rows(analysis, source), ["Professor criterion", "Experiment", "Metric", "Observed result", "Evidence reference"]),
        "All non-E5 rows point to the exact source packages in the inventory below; the correct-image rows point to the checksum-and-locator references above. Planned profile flexibility is design evidence only.", "",
        "## Seventeen audit checks", "",
        table([[c["id"], c["title"], c["verdict"], c["reason"]] for c in audit["checks"]], ["ID", "Requirement", "Verdict", "Evidence boundary"]),
        "Detailed per-file errors, source hashes, privacy results and provenance differences are in the generated validation/audit JSON. An INCOMPLETE or FAIL audit does not authorize empirical claims.", "",
        "## Complete package inventory", "",
        table([[p["path"].split("/")[-1], p["kind"], p["status"], p["stage"], p["validation"],
                "; ".join(e.get("reason", e["code"]) for e in p["errors"]) or "—"] for p in audit["packages"]],
              ["Package", "Kind", "Recorded status", "Stage", "Validation", "Failure / limitation"]),
        "Package names above are unambiguous entries in the reviewed input inventory; every constituent file has a SHA-256. No timestamp ordering selected the reported runs: both currently valid v1.3 functional packages are shown, and every legacy/invalid package remains listed.", "",
        "## Threats to validity and evidence boundaries", "",
        "Construct validity: image gold agreement, catalog capability descriptions, functional probes, user satisfaction and workload success measure different things. Undefined probes and label/operational discrepancies are retained.", "",
        "Internal validity: no final freeze/custody record establishes confirmatory isolation or frozen execution revisions. Several E5 extractor fields disagree with the recommendation source. Integrity failures cannot be repaired by accepting a new inventory checksum.", "",
        "External validity: ten visible development families, a small administrator catalog, developer-machine container probes and repeated use of the same image digests do not establish general performance. There is no participant population or measured eligible Kubernetes environment to generalize from.", "",
        "Statistical validity: cases/variants/repeats are not independent families; probes are reused. No new p-values, intervals, effect sizes or causal improvements are claimed. Failure to execute a hypothesis test is neither support nor contradiction.", "",
        "Custody/privacy: repository/archive scanning detects visible contamination patterns, not undisclosed external access or semantic overlap. Private confirmatory data was not opened. Human-study files are empty of participant observations. Future real human/cluster/storage collection requires a separately authorized, preregistered execution package.", "",
        "Historical Protocol-v4 evidence remains historical/formative. Its portable checksum/headline reproduction passes independently of the v5 verdict; external deep-archive sidecars are not required for the portable workflow. Raw observations, derived metrics and report interpretation remain separate.", "",
        "## Reproducibility", "",
        "Use the pinned `requirements-dev.txt` / `requirements-analysis.txt` environment (CPython 3.12–3.14; source observation provenance specifies its actual Python). Run from the repository root:", "",
        "```bash\npython3 -m venv .venv\n.venv/bin/python -m pip install -r requirements-dev.txt\nmake v5-audit\n```", "",
        "For inspectable separate stages, reuse one ID:", "",
        "```bash\nexport V5_RUN_ID=review-20260907-01\nmake v5-validate\nmake v5-analyze\nmake v5-figures\nmake v5-audit\n```", "",
        "Each command creates only missing stages under `results_v5/protocol-v5.0.0/final-audit/<run-id>/`; completed stages are checksum-verified before reuse. A changed input lock or implementation requires a new ID. `make v5-audit` runs every stage and writes the report before returning nonzero for integrity failures. On this snapshot nonzero is expected; do not suppress it as a success. Valid NOT_EXECUTED evidence does not itself cause a nonzero exit.", "",
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
                expected = inputs.files.get(relative)
                actual = file_sha256(f)
                comparisons.append({"artifact": relative, "comparison": "exact_bytes", "status": "PASS" if actual == expected else "FAIL",
                                    "original_sha256": expected, "regenerated_sha256": actual,
                                    "reason": "New derivative retained separately; original never changed."})
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
            figure.text(.5, .01, "Extractor provenance mismatch unresolved; no confirmatory claim or confidence interval.", ha="center", fontsize=9)
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
