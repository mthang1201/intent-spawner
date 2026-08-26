"""Protocol-v5 offline reporting layer for E1/E2 experiments.

This module generates reproducible, thesis-ready tables, standalone SVG vector
figures, structured JSON/CSV data files, P3 development decision reports,
limitations blocks, and comprehensive synthesis reports from validated offline
evidence and gold datasets.

It never fabricates performance values, never modifies backend semantics, and
never exposes sealed confirmatory datasets to development gate decisions.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import math
import os
from pathlib import Path
import platform
import statistics as std_statistics
import subprocess
from typing import Any

from evaluation_v4.dataset import file_sha256
from evaluation_v5.analysis.component_scoring import (
    AnalysisResult,
    ComponentAnalysisError,
    GoldCase,
    GoldSource,
    PRIMARY_CATEGORIES,
    load_component_gold,
    load_validated_evidence,
    p3_headroom_report,
    score_component_records,
)
from evaluation_v5.analysis.statistical_analysis import (
    DEFAULT_RETRIEVAL_KS,
    HOLM_REGISTRY_VERSION,
    LATENCY_POPULATION,
    P3_NOT_RETAINED,
    P3_RETAINED,
    StatisticalAnalysisError,
    StatisticalAnalysisResult,
    _prevalidate_completed_evidence_envelope,
    _require_v2_gold,
    _validate_retrieval_ks,
    analyze_statistical_records,
)
from evaluation_v5.analysis.statistics import (
    DEFAULT_ALPHA,
    DEFAULT_BOOTSTRAP_REPLICATES,
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_CONFIDENCE_LEVEL,
    MIN_PAIRED_DECISION_FAMILY_N,
    SEED_DERIVATION_ALGORITHM,
    STATISTICS_SCHEMA_VERSION,
    derive_bootstrap_seed,
    family_bootstrap_ci,
    family_n_warnings,
    holm_adjust,
    mean,
    paired_effect_sizes,
    paired_family_bootstrap_ci,
    paired_test,
    quantile,
    statistical_decision,
)
from evaluation_v5.gold_dataset import (
    GoldDatasetValidationError,
    candidate_satisfies_gold,
)
from evaluation_v5.offline.runner import (
    COMPLETION_FILENAME,
    LOCK_FILENAME,
    OFFLINE_PROVENANCE_SCHEMA_VERSION,
    PROVENANCE_FILENAME,
    RAW_DIRECTORY_NAME,
    RECORDS_FILENAME,
    REPORT_DIRECTORY_NAME,
    _sha256,
)
from evaluation_v5.offline.validate_evidence import (
    OfflineEvidenceValidationError,
    _read_json as _read_offline_json,
    _read_records as _read_offline_records,
    _validate_completion as _validate_offline_completion,
    validate_offline_evidence,
)
from evaluation_v5.split_dataset import (
    SPLIT_BUNDLE_SCHEMA_VERSION_V2,
    SplitBundleValidationError,
)
from recommender.candidate_corpus import CandidateCorpus, load_candidate_corpus
from recommender.models import ContractValidationError

REPORTING_SCHEMA_VERSION = "protocol-v5-offline-reporting-v1.0.0"
PROTOCOL_VERSION = "5.0.0"

REPORT_MANIFEST_FILENAME = "report-manifest.json"
SYNTHESIS_REPORT_FILENAME = "E1_E2_OFFLINE_REPORT.md"
RECOMMENDATION_QUALITY_MD = "recommendation_quality.md"
ROBUSTNESS_MD = "robustness.md"
P3_DECISION_MD = "p3_development_decision.md"
LIMITATIONS_MD = "limitations.md"

TABLE_FILES = {
    "recommendation_quality": "tables/recommendation_quality.csv",
    "recommendation_quality_json": "tables/recommendation_quality.json",
    "robustness": "tables/robustness.csv",
    "robustness_json": "tables/robustness.json",
    "retrieval_ablation": "tables/retrieval_ablation.csv",
    "retrieval_ablation_json": "tables/retrieval_ablation.json",
    "error_taxonomy": "tables/error_taxonomy.csv",
    "error_taxonomy_json": "tables/error_taxonomy.json",
    "paired_family_outcomes": "tables/paired_family_outcomes.csv",
    "paired_family_outcomes_json": "tables/paired_family_outcomes.json",
    "confidence_intervals": "tables/confidence_intervals.csv",
    "confidence_intervals_json": "tables/confidence_intervals.json",
    "p3_development_decision_json": "tables/p3_development_decision.json",
}

FIGURE_FILES = {
    "retrieval_recall_at_k": "figures/retrieval_recall_at_k.svg",
    "error_taxonomy": "figures/error_taxonomy.svg",
    "paired_family_outcomes": "figures/paired_family_outcomes.svg",
    "confidence_intervals": "figures/confidence_intervals.svg",
}

# Color palette for accessible, publication-grade thesis SVGs
PALETTE = {
    "blue": "#2563eb",
    "blue_light": "#93c5fd",
    "amber": "#d97706",
    "amber_light": "#fde68a",
    "emerald": "#059669",
    "emerald_light": "#a7f3d0",
    "purple": "#7c3aed",
    "purple_light": "#ddd6fe",
    "rose": "#e11d48",
    "rose_light": "#fecdd3",
    "gray": "#64748b",
    "gray_light": "#e2e8f0",
    "text": "#0f172a",
    "subtext": "#475569",
    "grid": "#e2e8f0",
    "axis": "#94a3b8",
    "white": "#ffffff",
    "background": "#f8fafc",
}


class ReportingError(RuntimeError):
    """Reporting inputs are missing, inconsistent, or invalid."""


def _check_output_dir_safety(output_dir: Path) -> None:
    """Ensure output_dir never targets or overwrites historical Protocol-v4 results."""
    resolved = output_dir.resolve()
    root = Path(__file__).resolve().parents[2]
    v4_results_root = (root / "results").resolve()
    v4_eval_root = (root / "evaluation_v4").resolve()
    results_v5_root = (root / "results_v5").resolve()

    if resolved == v4_results_root or (
        v4_results_root in resolved.parents and not (resolved == results_v5_root or results_v5_root in resolved.parents)
    ):
        raise ReportingError(
            f"prohibited output directory '{output_dir}': cannot write into historical Protocol-v4 results"
        )
    if resolved == v4_eval_root or v4_eval_root in resolved.parents:
        raise ReportingError(
            f"prohibited output directory '{output_dir}': cannot write into evaluation_v4 directory"
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _git_revision() -> str:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "0" * 40
    return revision if len(revision) == 40 else "0" * 40


def _round(value: Any, digits: int = 4) -> Any:
    return round(value, digits) if isinstance(value, float) and math.isfinite(value) else value


def _fmt_val(value: float | None, digits: int = 3, pct: bool = False) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    if pct:
        return f"{value * 100:.1f}%"
    return f"{value:.{digits}f}"


def _fmt_ci(ci: tuple[float | None, float | None] | None, digits: int = 3, pct: bool = False) -> str:
    if not ci or ci[0] is None or ci[1] is None:
        return "N/A"
    low = _fmt_val(ci[0], digits=digits, pct=pct)
    high = _fmt_val(ci[1], digits=digits, pct=pct)
    return f"[{low}, {high}]"


def _fmt_pval(pval: float | None) -> str:
    if pval is None:
        return "N/A"
    if pval < 0.001:
        return "< 0.001"
    return f"{pval:.4f}"


def _write_file_exclusive(path: Path, content: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, 0o644)
        with open(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
    else:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, 0o644)
        with open(fd, "wb") as handle:
            handle.write(content)
    return path


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> Path:
    return _write_file_exclusive(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


# ---------------------------------------------------------------------------
# SVG Drawing Helpers
# ---------------------------------------------------------------------------


def _svg_header(width: int, height: int, title: str, subtitle: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'  <rect width="100%" height="100%" fill="{PALETTE["white"]}"/>',
        "  <style>",
        "    text { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }",
        f"    .title {{ font-size: 18px; font-weight: 700; fill: {PALETTE['text']}; }}",
        f"    .subtitle {{ font-size: 12px; fill: {PALETTE['subtext']}; }}",
        f"    .axis {{ stroke: {PALETTE['axis']}; stroke-width: 1; }}",
        f"    .grid {{ stroke: {PALETTE['grid']}; stroke-width: 1; stroke-dasharray: 2 2; }}",
        f"    .tick {{ font-size: 11px; fill: {PALETTE['subtext']}; }}",
        f"    .label {{ font-size: 12px; font-weight: 600; fill: {PALETTE['text']}; }}",
        f"    .val {{ font-size: 11px; font-weight: 600; fill: {PALETTE['text']}; }}",
        f"    .val-light {{ font-size: 10px; fill: {PALETTE['subtext']}; }}",
        f"    .legend {{ font-size: 11px; fill: {PALETTE['text']}; }}",
        f"    .note {{ font-size: 10px; font-style: italic; fill: {PALETTE['subtext']}; }}",
        "  </style>",
        f'  <text x="50" y="32" class="title">{escape(title)}</text>',
        f'  <text x="50" y="52" class="subtitle">{escape(subtitle)}</text>',
    ]


# ---------------------------------------------------------------------------
# Output 1: Recommendation Quality Table (P1 vs P2)
# ---------------------------------------------------------------------------


def compute_recommendation_quality_data(
    statistical_result: StatisticalAnalysisResult,
    component_result: AnalysisResult,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compile comprehensive E1 recommendation quality data."""
    sys_est_map: dict[tuple[str, str], Mapping[str, Any]] = {
        (row["system_id"], row["endpoint"]): row
        for row in statistical_result.system_estimates
    }
    paired_map: dict[tuple[str, str], Mapping[str, Any]] = {
        (row.get("comparison_id", f"{row.get('second_system')}_minus_{row.get('first_system')}"), row["endpoint"]): row
        for row in statistical_result.paired_comparisons
    }

    endpoints = [
        ("joint_accept_at_1", "JointAccept@1", True),
        ("profile_acceptable_accuracy", "Profile Acceptability", True),
        ("image_acceptable_accuracy", "Image Acceptability", True),
        ("hard_constraint_violation_rate", "Constraint Violations", True),
        ("infeasible_detection_accuracy", "Unsupported Detection", True),
        ("latency_seconds", "Latency (seconds)", False),
    ]

    systems = sorted({row["system_id"] for row in statistical_result.system_estimates})
    has_p1 = "P1" in systems
    has_p2 = "P2" in systems
    has_p3 = "P3" in systems

    table_rows = []
    for key, display_name, is_rate in endpoints:
        row_dict: dict[str, Any] = {"metric_key": key, "metric_name": display_name}
        for sys_id in ("P1", "P2", "P3"):
            if sys_id in systems:
                sys_metric = sys_est_map.get((sys_id, key), {})
                est = sys_metric.get("estimate")
                ci_low = sys_metric.get("ci_low")
                ci_high = sys_metric.get("ci_high")
                row_dict[f"{sys_id}_estimate"] = est
                row_dict[f"{sys_id}_ci_low"] = ci_low
                row_dict[f"{sys_id}_ci_high"] = ci_high
                row_dict[f"{sys_id}_formatted"] = (
                    f"{_fmt_val(est, pct=is_rate and key != 'latency_seconds')} {_fmt_ci((ci_low, ci_high), pct=is_rate and key != 'latency_seconds')}"
                    if est is not None
                    else "N/A"
                )
            else:
                row_dict[f"{sys_id}_estimate"] = None
                row_dict[f"{sys_id}_ci_low"] = None
                row_dict[f"{sys_id}_ci_high"] = None
                row_dict[f"{sys_id}_formatted"] = "N/A"

        # Paired difference P2 - P1
        p2_p1_comp = paired_map.get(("P2_minus_P1", key), {})
        diff_est = p2_p1_comp.get("effects", {}).get("mean_difference")
        diff_low = p2_p1_comp.get("ci_low")
        diff_high = p2_p1_comp.get("ci_high")
        p_val = p2_p1_comp.get("p_value_raw")

        row_dict["diff_P2_minus_P1_estimate"] = diff_est
        row_dict["diff_P2_minus_P1_ci_low"] = diff_low
        row_dict["diff_P2_minus_P1_ci_high"] = diff_high
        row_dict["diff_P2_minus_P1_formatted"] = (
            f"{_fmt_val(diff_est, pct=is_rate and key != 'latency_seconds', signed=True)} {_fmt_ci((diff_low, diff_high), pct=is_rate and key != 'latency_seconds')}"
            if diff_est is not None
            else "N/A"
        )
        row_dict["p_value_P2_minus_P1"] = p_val
        row_dict["p_value_formatted"] = _fmt_pval(p_val)
        table_rows.append(row_dict)

    # Calculate fallback and unsupported rates from component/record logs
    unsupported_cases = 0
    infeasible_cases = 0
    p2_unsupported_detections = 0
    p1_unsupported_detections = 0

    for rec in records:
        pol = rec.get("evaluation_gold", {})
        is_feas = pol.get("request_feasible", True)
        if not is_feas:
            infeasible_cases += 1
            if rec.get("system_id") == "P2" and rec.get("metric_inputs", {}).get("infeasible_request_signal"):
                p2_unsupported_detections += 1
            elif rec.get("system_id") == "P1" and rec.get("predicted_candidate_id") is None:
                p1_unsupported_detections += 1

    return {
        "schema_version": REPORTING_SCHEMA_VERSION,
        "experiment": "E1",
        "title": "Primary Recommendation Quality (P1 vs P2)",
        "has_p1": has_p1,
        "has_p2": has_p2,
        "has_p3": has_p3,
        "rows": table_rows,
        "summary": {
            "unsupported_cases": unsupported_cases,
            "infeasible_cases": infeasible_cases,
        },
    }


def format_recommendation_quality_md(data: dict[str, Any]) -> str:
    has_p3 = data.get("has_p3", False)
    lines = [
        "## 1. Primary Recommendation Quality (E1: P1 vs P2)",
        "",
    ]
    if has_p3:
        lines.extend([
            "| Metric | P1 (Rule-Based) | P2 (Proposed) | P3 (Grounded LLM) | Difference (P2 − P1) | p-value |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ])
    else:
        lines.extend([
            "| Metric | P1 (Rule-Based) | P2 (Proposed) | Difference (P2 − P1) | p-value |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ])

    for r in data["rows"]:
        m_name = r["metric_name"]
        p1_f = r["P1_formatted"]
        p2_f = r["P2_formatted"]
        p3_f = r["P3_formatted"]
        diff_f = r["diff_P2_minus_P1_formatted"]
        p_f = r["p_value_formatted"]

        if has_p3:
            lines.append(f"| **{m_name}** | {p1_f} | {p2_f} | {p3_f} | {diff_f} | {p_f} |")
        else:
            lines.append(f"| **{m_name}** | {p1_f} | {p2_f} | {diff_f} | {p_f} |")

    lines.append("")
    lines.append("> *Notes: Point estimates report workload family macro-averages with 95% percentile bootstrap confidence intervals in brackets. Differences are computed as P2 minus P1. Latency is measured end-to-end per execution.*")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output 2: Robustness Table (E2)
# ---------------------------------------------------------------------------


def compute_robustness_table_data(
    statistical_result: StatisticalAnalysisResult,
    records: Sequence[Mapping[str, Any]],
    gold: GoldSource,
) -> dict[str, Any]:
    """Compute Systems x (Strata, Overall SRR, Worst-Case Robustness)."""
    # 1. Stratified estimates from statistical analysis (variant_stratum)
    strat_map: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in statistical_result.stratified_estimates:
        if row.get("dimension") == "variant_stratum":
            strat_map[(row["system_id"], row["value"])] = row

    # 2. Overall SRR from system estimates (robustness_rate)
    system_srr: dict[str, Mapping[str, Any]] = {}
    for row in statistical_result.system_estimates:
        if row.get("endpoint") == "robustness_rate":
            system_srr[row["system_id"]] = row

    # 3. Worst-case family robustness (fraction of families where 100% of equivalent variants passed)
    gold_cases_map = {c.case_id: c for c in gold.cases}
    family_variants_pass: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))

    for rec in records:
        sys_id = str(rec.get("system_id"))
        case_id = str(rec.get("case_id"))
        case = gold_cases_map.get(case_id)
        if not case:
            continue
        v_class = str(rec.get("variant_class") or getattr(case, "variant_id", ""))
        pol = case.policy_gold
        if pol.get("expected_feasibility") != "feasible":
            continue

        acc_cand = set(case.candidate_gold.get("acceptable_candidate_ids", []))
        m_in = rec.get("metric_inputs") or {}
        passed = bool(
            m_in.get("predicted_candidate_id") in acc_cand
            and m_in.get("hard_constraints_satisfied", True)
        )
        family_id = case.family_id
        is_canonical = "canonical" in v_class
        if not is_canonical:
            family_variants_pass[sys_id][family_id].append(passed)

    worst_case_family_rate: dict[str, float | None] = {}
    for sys_id, fams in family_variants_pass.items():
        if not fams:
            worst_case_family_rate[sys_id] = None
            continue
        passed_all = sum(1 for f_id, v_list in fams.items() if v_list and all(v_list))
        worst_case_family_rate[sys_id] = passed_all / len(fams)

    strata_order = [
        ("canonical", "Canonical"),
        ("paraphrase", "Paraphrase"),
        ("vietnamese", "Vietnamese"),
        ("noisy", "Noisy"),
        ("code_centric", "Code-Centric"),
    ]

    systems = sorted({row["system_id"] for row in statistical_result.system_estimates})
    rows = []
    for sys_id in systems:
        row_dict: dict[str, Any] = {"system_id": sys_id}
        for s_key, s_label in strata_order:
            cell = strat_map.get((sys_id, s_key), {})
            est = cell.get("estimate")
            ci_low = cell.get("ci_low")
            ci_high = cell.get("ci_high")
            row_dict[s_key] = {
                "estimate": est,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "formatted": f"{_fmt_val(est, pct=True)} {_fmt_ci((ci_low, ci_high), pct=True)}" if est is not None else "N/A",
            }

        srr_row = system_srr.get(sys_id, {})
        srr_est = srr_row.get("estimate")
        srr_low = srr_row.get("ci_low")
        srr_high = srr_row.get("ci_high")
        row_dict["overall_srr"] = {
            "estimate": srr_est,
            "ci_low": srr_low,
            "ci_high": srr_high,
            "formatted": f"{_fmt_val(srr_est, pct=True)} {_fmt_ci((srr_low, srr_high), pct=True)}" if srr_est is not None else "N/A",
        }

        wc_rate = worst_case_family_rate.get(sys_id)
        row_dict["worst_case_family_robustness"] = {
            "estimate": wc_rate,
            "formatted": _fmt_val(wc_rate, pct=True),
        }
        rows.append(row_dict)

    return {
        "schema_version": REPORTING_SCHEMA_VERSION,
        "experiment": "E2",
        "title": "Natural-Language Robustness (E2)",
        "strata": strata_order,
        "rows": rows,
    }


def format_robustness_md(data: dict[str, Any]) -> str:
    lines = [
        "## 2. Robustness Table (E2)",
        "",
        "| System | Canonical | Paraphrase | Vietnamese | Noisy | Code-Centric | Overall SRR (Macro) | Worst-Case Family Robustness |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for r in data["rows"]:
        sys_id = r["system_id"]
        c_fmt = r["canonical"]["formatted"]
        p_fmt = r["paraphrase"]["formatted"]
        v_fmt = r["vietnamese"]["formatted"]
        n_fmt = r["noisy"]["formatted"]
        cc_fmt = r["code_centric"]["formatted"]
        srr_fmt = r["overall_srr"]["formatted"]
        wc_fmt = r["worst_case_family_robustness"]["formatted"]
        lines.append(
            f"| **{sys_id}** | {c_fmt} | {p_fmt} | {v_fmt} | {n_fmt} | {cc_fmt} | **{srr_fmt}** | {wc_fmt} |"
        )
    lines.append("")
    lines.append("> *Notes: Overall SRR (Semantic Robustness Rate) is the equal-weight family macro mean across non-canonical reviewed-equivalent variants. Worst-case family robustness measures the fraction of families where all tested equivalent variants succeeded.*")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output 3: Retrieval Ablation Figure & Table
# ---------------------------------------------------------------------------


def compute_retrieval_ablation_data(
    records: Sequence[Mapping[str, Any]],
    gold: GoldSource,
    ks: Sequence[int] = DEFAULT_RETRIEVAL_KS,
) -> dict[str, Any]:
    """Compute Recall@K for Sparse, Dense, and Hybrid retrieval channels."""
    gold_map = {c.case_id: c for c in gold.cases}
    ks = tuple(sorted(set(ks)))

    channel_family_recalls: dict[str, dict[int, dict[str, list[float]]]] = {
        "Sparse": {k: defaultdict(list) for k in ks},
        "Dense": {k: defaultdict(list) for k in ks},
        "Hybrid": {k: defaultdict(list) for k in ks},
    }

    for rec in records:
        if rec.get("system_id") != "P2":
            continue
        case_id = str(rec.get("case_id"))
        case = gold_map.get(case_id)
        if not case:
            continue
        if case.policy_gold.get("expected_feasibility") != "feasible":
            continue

        acceptable = set(case.candidate_gold.get("acceptable_candidate_ids", []))
        if not acceptable:
            continue

        sparse_hits = [h.get("candidate_id") for h in rec.get("sparse_ranks", [])]
        dense_hits = [h.get("candidate_id") for h in rec.get("dense_ranks", [])]
        hybrid_hits = [h.get("candidate_id") for h in (rec.get("candidate_top_k") or rec.get("hybrid_ranks_scores", []))]

        family_id = case.family_id
        for k in ks:
            sp_top = set(sparse_hits[:k])
            dn_top = set(dense_hits[:k])
            hy_top = set(hybrid_hits[:k])

            channel_family_recalls["Sparse"][k][family_id].append(
                len(acceptable & sp_top) / len(acceptable)
            )
            channel_family_recalls["Dense"][k][family_id].append(
                len(acceptable & dn_top) / len(acceptable)
            )
            channel_family_recalls["Hybrid"][k][family_id].append(
                len(acceptable & hy_top) / len(acceptable)
            )

    results = []
    for channel in ("Sparse", "Dense", "Hybrid"):
        for k in ks:
            fam_dict = channel_family_recalls[channel][k]
            if fam_dict:
                fam_means = [std_statistics.fmean(vals) for vals in fam_dict.values() if vals]
                macro_recall = std_statistics.fmean(fam_means) if fam_means else None
            else:
                macro_recall = None

            results.append(
                {
                    "channel": channel,
                    "k": k,
                    "recall_at_k": macro_recall,
                    "is_ablation": channel in {"Sparse", "Dense"},
                    "is_proposed": channel == "Hybrid",
                }
            )

    return {
        "schema_version": REPORTING_SCHEMA_VERSION,
        "title": "P2 Retrieval Ablation: Recall@K",
        "description": "Recall@K comparison for Sparse, Dense, and Hybrid retrieval channels. These are P2 ablations, not primary system IDs.",
        "ks": list(ks),
        "results": results,
    }


def render_retrieval_recall_svg(data: dict[str, Any], width: int = 760, height: int = 420) -> str:
    svg = _svg_header(
        width,
        height,
        "P2 Retrieval Channel Ablation: Recall@K",
        "Pre-constraint retrieval recall across Sparse, Dense, and Hybrid (RRF) channels. These are P2 ablations, not primary system IDs.",
    )

    ks = data.get("ks", [1, 3, 5])
    channels = [("Sparse", PALETTE["blue"]), ("Dense", PALETTE["amber"]), ("Hybrid", PALETTE["emerald"])]

    left, top, bottom, right = 80, 80, 330, 700
    svg.append(f'  <line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" class="axis"/>')
    svg.append(f'  <line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" class="axis"/>')

    for i in range(6):
        y_val = i * 0.2
        y_pos = bottom - (bottom - top) * (y_val / 1.0)
        svg.append(f'  <line x1="{left}" y1="{y_pos:.1f}" x2="{right}" y2="{y_pos:.1f}" class="grid"/>')
        svg.append(f'  <text x="{left - 10}" y="{y_pos + 4:.1f}" text-anchor="end" class="tick">{y_val:.1f}</text>')

    svg.append(f'  <text x="25" y="{(top + bottom) / 2}" transform="rotate(-90 25 {(top + bottom) / 2})" text-anchor="middle" class="label">Macro Recall@K</text>')

    group_width = (right - left) / len(ks)
    bar_width = min(36, group_width / (len(channels) + 1.2))

    data_map = {(r["channel"], r["k"]): r["recall_at_k"] for r in data.get("results", [])}

    for g_idx, k in enumerate(ks):
        g_center = left + group_width * (g_idx + 0.5)
        for c_idx, (channel, color) in enumerate(channels):
            val = data_map.get((channel, k)) or 0.0
            h = (val / 1.0) * (bottom - top)
            x = g_center + (c_idx - (len(channels) - 1) / 2) * (bar_width + 6) - bar_width / 2
            y = bottom - h
            svg.append(f'  <rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{h:.1f}" fill="{color}" rx="3"/>')
            if val > 0.01:
                svg.append(f'  <text x="{x + bar_width / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" class="val">{val:.3f}</text>')

        svg.append(f'  <text x="{g_center:.1f}" y="{bottom + 22}" text-anchor="middle" class="label">K = {k}</text>')

    leg_x = left + 140
    leg_y = height - 30
    for idx, (channel, color) in enumerate(channels):
        x = leg_x + idx * 160
        tag = " (P2 Default)" if channel == "Hybrid" else " (Ablation)"
        svg.append(f'  <rect x="{x}" y="{leg_y - 10}" width="14" height="14" fill="{color}" rx="2"/>')
        svg.append(f'  <text x="{x + 20}" y="{leg_y + 2}" class="legend">{channel}{tag}</text>')

    svg.append("</svg>")
    return "\n".join(svg) + "\n"


# ---------------------------------------------------------------------------
# Output 4: Error Taxonomy Figure & Table
# ---------------------------------------------------------------------------


def compute_error_taxonomy_data(
    component_result: AnalysisResult,
) -> dict[str, Any]:
    """Compute counts and fractions of primary earliest-failure categories."""
    p2_aggregates = component_result.aggregates.get("P2", {})
    categories_dict = p2_aggregates.get("primary_categories", {})

    total_failures = p2_aggregates.get("failed_recommendations", 0)
    total_recs = p2_aggregates.get("total_recommendations", 0)

    order = [
        ("EXTRACTION_ERROR", "Extraction", PALETTE["purple"]),
        ("RETRIEVAL_MISS", "Retrieval", PALETTE["blue"]),
        ("CONSTRAINT_ERROR", "Constraint", PALETTE["amber"]),
        ("RANKING_ERROR", "Ranking", PALETTE["rose"]),
        ("UNSUPPORTED_CATALOG", "Unsupported", PALETTE["gray"]),
        ("PROVIDER_FAILURE", "Provider", PALETTE["gray_light"]),
        ("OTHER", "Other", PALETTE["subtext"]),
    ]

    rows = []
    for cat_key, label, color in order:
        count = categories_dict.get(cat_key, 0)
        frac_failures = count / total_failures if total_failures > 0 else 0.0
        frac_total = count / total_recs if total_recs > 0 else 0.0
        rows.append(
            {
                "category": cat_key,
                "label": label,
                "count": count,
                "fraction_of_failures": frac_failures,
                "fraction_of_total": frac_total,
                "color": color,
            }
        )

    return {
        "schema_version": REPORTING_SCHEMA_VERSION,
        "system_id": "P2",
        "title": "P2 Earliest-Failure Taxonomy",
        "total_recommendations": total_recs,
        "failed_recommendations": total_failures,
        "categories": rows,
    }


def render_error_taxonomy_svg(data: dict[str, Any], width: int = 760, height: int = 420) -> str:
    svg = _svg_header(
        width,
        height,
        "P2 Error Taxonomy: Earliest Causal Failure Breakdown",
        f"Attribution of {data.get('failed_recommendations', 0)} failed recommendations out of {data.get('total_recommendations', 0)} total evaluated attempts.",
    )

    categories = data.get("categories", [])
    left, top, bottom, right = 110, 80, 330, 700

    svg.append(f'  <line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" class="axis"/>')
    svg.append(f'  <line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" class="axis"/>')

    max_count = max([c["count"] for c in categories] + [5])
    y_max = math.ceil(max_count * 1.15)

    for i in range(5):
        y_val = (y_max / 4) * i
        y_pos = bottom - (bottom - top) * (y_val / y_max)
        svg.append(f'  <line x1="{left}" y1="{y_pos:.1f}" x2="{right}" y2="{y_pos:.1f}" class="grid"/>')
        svg.append(f'  <text x="{left - 10}" y="{y_pos + 4:.1f}" text-anchor="end" class="tick">{int(y_val)}</text>')

    svg.append(f'  <text x="35" y="{(top + bottom) / 2}" transform="rotate(-90 35 {(top + bottom) / 2})" text-anchor="middle" class="label">Failure Count</text>')

    bar_step = (right - left) / len(categories)
    bar_width = min(48, bar_step * 0.65)

    for idx, cat in enumerate(categories):
        center = left + bar_step * (idx + 0.5)
        count = cat["count"]
        pct = cat["fraction_of_failures"] * 100
        h = (count / y_max) * (bottom - top) if y_max > 0 else 0
        x = center - bar_width / 2
        y = bottom - h
        color = cat.get("color", PALETTE["blue"])

        svg.append(f'  <rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{h:.1f}" fill="{color}" rx="3"/>')
        if count > 0:
            svg.append(f'  <text x="{center:.1f}" y="{y - 16:.1f}" text-anchor="middle" class="val">{count}</text>')
            svg.append(f'  <text x="{center:.1f}" y="{y - 4:.1f}" text-anchor="middle" class="val-light">({pct:.1f}%)</text>')
        else:
            svg.append(f'  <text x="{center:.1f}" y="{bottom - 6}" text-anchor="middle" class="val-light">0</text>')

        svg.append(f'  <text x="{center:.1f}" y="{bottom + 20}" text-anchor="middle" class="label">{cat["label"]}</text>')

    svg.append(f'  <text x="{right}" y="{height - 15}" text-anchor="end" class="note">Earliest failed pipeline stage per failed request</text>')
    svg.append("</svg>")
    return "\n".join(svg) + "\n"


# ---------------------------------------------------------------------------
# Output 5: Paired Family Outcome Visualization
# ---------------------------------------------------------------------------


def compute_paired_family_outcomes_data(
    statistical_result: StatisticalAnalysisResult,
    gold: GoldSource,
) -> dict[str, Any]:
    """Compute per-family paired JointAccept@1 outcomes and delta (P2 - P1)."""
    fam_est_map: dict[tuple[str, str], float] = {}
    fam_stratum_map: dict[str, str] = {}

    for c in gold.cases:
        fam_stratum_map[c.family_id] = getattr(c, "family_metadata", {}).get("workload_stratum", "unspecified")

    for row in statistical_result.family_estimates:
        s_id = row["system_id"]
        f_id = row["family_id"]
        acc = row.get("values", {}).get("joint_accept_at_1")
        if acc is not None:
            fam_est_map[(s_id, f_id)] = acc

    all_families = sorted(set(f_id for (s_id, f_id) in fam_est_map.keys()))
    family_rows = []
    p2_wins = 0
    ties = 0
    p1_wins = 0
    unpaired_count = 0

    for f_id in all_families:
        p1_val = fam_est_map.get(("P1", f_id))
        p2_val = fam_est_map.get(("P2", f_id))
        p3_val = fam_est_map.get(("P3", f_id))

        if p1_val is not None and p2_val is not None:
            delta = p2_val - p1_val
            if delta > 1e-6:
                winner = "P2"
                p2_wins += 1
            elif delta < -1e-6:
                winner = "P1"
                p1_wins += 1
            else:
                winner = "Tie"
                ties += 1
            pairing_status = "PAIRED"
        else:
            delta = None
            winner = "Incomplete"
            pairing_status = "INELIGIBLE_UNPAIRED"
            unpaired_count += 1

        family_rows.append(
            {
                "family_id": f_id,
                "stratum": fam_stratum_map.get(f_id, "standard"),
                "P1_score": p1_val,
                "P2_score": p2_val,
                "P3_score": p3_val,
                "delta_P2_minus_P1": delta,
                "winner": winner,
                "pairing_status": pairing_status,
            }
        )

    return {
        "schema_version": REPORTING_SCHEMA_VERSION,
        "title": "Paired Family Outcomes (P1 vs P2)",
        "total_families": len(all_families),
        "eligible_paired_families": len(all_families) - unpaired_count,
        "ineligible_unpaired_families": unpaired_count,
        "p2_wins": p2_wins,
        "ties": ties,
        "p1_wins": p1_wins,
        "families": family_rows,
    }


def render_paired_family_outcomes_svg(data: dict[str, Any], width: int = 760, height: int = 460) -> str:
    svg = _svg_header(
        width,
        height,
        "Paired Family JointAccept@1 Outcomes (P1 vs P2)",
        f"Family-by-family delta: {data.get('p2_wins', 0)} P2 Wins, {data.get('ties', 0)} Ties, {data.get('p1_wins', 0)} P1 Wins across {data.get('total_families', 0)} families.",
    )

    families = data.get("families", [])
    if not families:
        svg.append(f'  <text x="380" y="230" text-anchor="middle" class="label">No paired family data available</text>')
        svg.append("</svg>")
        return "\n".join(svg)

    left, top, bottom, right = 140, 85, 410, 710
    svg.append(f'  <line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" class="axis"/>')

    for i in range(5):
        d_val = -1.0 + i * 0.5
        x_pos = left + (right - left) * ((d_val + 1.0) / 2.0)
        svg.append(f'  <line x1="{x_pos:.1f}" y1="{top}" x2="{x_pos:.1f}" y2="{bottom}" class="grid"/>')
        svg.append(f'  <text x="{x_pos:.1f}" y="{bottom + 18}" text-anchor="middle" class="tick">{d_val:+.1f}</text>')

    zero_x = left + (right - left) * 0.5
    svg.append(f'  <line x1="{zero_x:.1f}" y1="{top}" x2="{zero_x:.1f}" y2="{bottom}" stroke="{PALETTE["axis"]}" stroke-width="2"/>')
    svg.append(f'  <text x="{(left + right) / 2}" y="{bottom + 38}" text-anchor="middle" class="label">Paired Difference (P2 - P1 JointAccept@1)</text>')

    row_height = (bottom - top) / max(len(families), 1)
    for idx, f in enumerate(families):
        y = top + row_height * (idx + 0.5)
        d = f.get("delta_P2_minus_P1") or 0.0
        x_target = left + (right - left) * ((d + 1.0) / 2.0)

        f_id = f["family_id"]
        display_f = f_id if len(f_id) <= 16 else f_id[:14] + ".."
        svg.append(f'  <text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" class="tick">{escape(display_f)}</text>')

        bar_color = PALETTE["emerald"] if d > 1e-6 else (PALETTE["rose"] if d < -1e-6 else PALETTE["gray"])
        x_min = min(zero_x, x_target)
        w = abs(x_target - zero_x)

        svg.append(f'  <rect x="{x_min:.1f}" y="{y - 4:.1f}" width="{w:.1f}" height="8" fill="{bar_color}" rx="2"/>')
        svg.append(f'  <circle cx="{x_target:.1f}" cy="{y:.1f}" r="4" fill="{bar_color}"/>')

    svg.append("</svg>")
    return "\n".join(svg) + "\n"


# ---------------------------------------------------------------------------
# Output 6: Confidence Interval Visualization (Forest Plot)
# ---------------------------------------------------------------------------


def compute_confidence_intervals_data(
    statistical_result: StatisticalAnalysisResult,
) -> dict[str, Any]:
    """Compute point estimates and 95% bootstrap CIs for forest plot."""
    endpoints = [
        ("joint_accept_at_1", "JointAccept@1"),
        ("profile_acceptable_accuracy", "Profile Acceptable"),
        ("image_acceptable_accuracy", "Image Acceptable"),
        ("hard_constraint_violation_rate", "Constraint Violations"),
        ("robustness_rate", "Robustness (SRR)"),
    ]

    sys_est_map = {
        (row["system_id"], row["endpoint"]): row
        for row in statistical_result.system_estimates
    }
    paired_map = {
        (row.get("comparison_id", f"{row.get('second_system')}_minus_{row.get('first_system')}"), row["endpoint"]): row
        for row in statistical_result.paired_comparisons
    }

    estimates = []
    for key, name in endpoints:
        p1_m = sys_est_map.get(("P1", key), {})
        p2_m = sys_est_map.get(("P2", key), {})
        p_comp = paired_map.get(("P2_minus_P1", key), {})

        estimates.append(
            {
                "metric_key": key,
                "metric_name": name,
                "P1": {
                    "estimate": p1_m.get("estimate"),
                    "ci_low": p1_m.get("ci_low"),
                    "ci_high": p1_m.get("ci_high"),
                },
                "P2": {
                    "estimate": p2_m.get("estimate"),
                    "ci_low": p2_m.get("ci_low"),
                    "ci_high": p2_m.get("ci_high"),
                },
                "diff_P2_minus_P1": {
                    "estimate": p_comp.get("effects", {}).get("mean_difference"),
                    "ci_low": p_comp.get("ci_low"),
                    "ci_high": p_comp.get("ci_high"),
                    "decision": p_comp.get("hypothesis_status", "UNTESTED"),
                },
            }
        )

    return {
        "schema_version": REPORTING_SCHEMA_VERSION,
        "title": "Protocol-v5 Confidence Intervals and Effect Sizes",
        "estimates": estimates,
    }


def render_confidence_intervals_svg(data: dict[str, Any], width: int = 760, height: int = 420) -> str:
    svg = _svg_header(
        width,
        height,
        "Confidence-Interval Forest Plot (P1 vs P2)",
        "Equal-weight family macro means with 95% bootstrap confidence intervals and paired differences.",
    )

    estimates = data.get("estimates", [])
    top, bottom = 85, 330
    row_height = (bottom - top) / max(len(estimates), 1)

    l_left, l_right = 140, 410
    r_left, r_right = 480, 710

    svg.append(f'  <text x="{(l_left + l_right) / 2}" y="{top - 12}" text-anchor="middle" class="label">Absolute Estimates (0.0 to 1.0)</text>')
    svg.append(f'  <text x="{(r_left + r_right) / 2}" y="{top - 12}" text-anchor="middle" class="label">Paired Difference (P2 - P1)</text>')

    for i in range(5):
        val = i * 0.25
        x = l_left + (l_right - l_left) * (val / 1.0)
        svg.append(f'  <line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}" class="grid"/>')
        svg.append(f'  <text x="{x:.1f}" y="{bottom + 16}" text-anchor="middle" class="tick">{val:.2f}</text>')
    svg.append(f'  <line x1="{l_left}" y1="{bottom}" x2="{l_right}" y2="{bottom}" class="axis"/>')

    for i in range(5):
        val = -0.5 + i * 0.25
        x = r_left + (r_right - r_left) * ((val + 0.5) / 1.0)
        svg.append(f'  <line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}" class="grid"/>')
        svg.append(f'  <text x="{x:.1f}" y="{bottom + 16}" text-anchor="middle" class="tick">{val:+.2f}</text>')
    svg.append(f'  <line x1="{r_left}" y1="{bottom}" x2="{r_right}" y2="{bottom}" class="axis"/>')

    r_zero = r_left + (r_right - r_left) * 0.5
    svg.append(f'  <line x1="{r_zero:.1f}" y1="{top}" x2="{r_zero:.1f}" y2="{bottom}" stroke="{PALETTE["axis"]}" stroke-width="1.5" stroke-dasharray="3 3"/>')

    for idx, e in enumerate(estimates):
        y = top + row_height * (idx + 0.5)
        name = e["metric_name"]
        svg.append(f'  <text x="{l_left - 10}" y="{y + 4:.1f}" text-anchor="end" class="label">{name}</text>')

        p1 = e.get("P1", {})
        p2 = e.get("P2", {})

        for sys_data, color, y_offset in [(p1, PALETTE["amber"], -4), (p2, PALETTE["blue"], 4)]:
            est = sys_data.get("estimate")
            ci_l = sys_data.get("ci_low")
            ci_h = sys_data.get("ci_high")
            if est is not None and ci_l is not None and ci_h is not None:
                cx = l_left + (l_right - l_left) * max(0.0, min(1.0, est))
                x1 = l_left + (l_right - l_left) * max(0.0, min(1.0, ci_l))
                x2 = l_left + (l_right - l_left) * max(0.0, min(1.0, ci_h))
                svg.append(f'  <line x1="{x1:.1f}" y1="{y + y_offset:.1f}" x2="{x2:.1f}" y2="{y + y_offset:.1f}" stroke="{color}" stroke-width="2"/>')
                svg.append(f'  <circle cx="{cx:.1f}" cy="{y + y_offset:.1f}" r="3.5" fill="{color}"/>')

        diff = e.get("diff_P2_minus_P1", {})
        d_est = diff.get("estimate")
        d_l = diff.get("ci_low")
        d_h = diff.get("ci_high")
        if d_est is not None and d_l is not None and d_h is not None:
            cx = r_left + (r_right - r_left) * max(0.0, min(1.0, (d_est + 0.5) / 1.0))
            x1 = r_left + (r_right - r_left) * max(0.0, min(1.0, (d_l + 0.5) / 1.0))
            x2 = r_left + (r_right - r_left) * max(0.0, min(1.0, (d_h + 0.5) / 1.0))
            diff_color = PALETTE["emerald"] if (d_l > 0 or d_h < 0) else PALETTE["purple"]
            svg.append(f'  <line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" stroke="{diff_color}" stroke-width="2"/>')
            svg.append(f'  <circle cx="{cx:.1f}" cy="{y:.1f}" r="4" fill="{diff_color}"/>')

    leg_y = height - 25
    svg.append(f'  <rect x="180" y="{leg_y - 8}" width="12" height="12" fill="{PALETTE["amber"]}" rx="2"/>')
    svg.append(f'  <text x="198" y="{leg_y + 2}" class="legend">P1 (Rule-Based)</text>')
    svg.append(f'  <rect x="310" y="{leg_y - 8}" width="12" height="12" fill="{PALETTE["blue"]}" rx="2"/>')
    svg.append(f'  <text x="328" y="{leg_y + 2}" class="legend">P2 (Structured + Hybrid)</text>')
    svg.append(f'  <rect x="520" y="{leg_y - 8}" width="12" height="12" fill="{PALETTE["emerald"]}" rx="2"/>')
    svg.append(f'  <text x="538" y="{leg_y + 2}" class="legend">Paired Difference (P2 - P1)</text>')

    svg.append("</svg>")
    return "\n".join(svg) + "\n"


# ---------------------------------------------------------------------------
# Output 7: P3 Development Decision Report
# ---------------------------------------------------------------------------


def compute_p3_development_decision(
    component_result: AnalysisResult,
    gold: GoldSource,
    *,
    p3_development_decision: Path | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate or load the predefined P3 development gate decision.

    The decision is ALWAYS determined by frozen development evidence. Confirmatory
    evidence must NEVER be inspected or used to decide P3 enablement.
    """
    if p3_development_decision is not None:
        if isinstance(p3_development_decision, (str, Path)):
            p = Path(p3_development_decision)
            if not p.is_file():
                raise ReportingError(f"frozen P3 development decision file not found: {p}")
            try:
                dec_data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ReportingError(f"frozen P3 development decision file is unreadable: {p}") from exc
        elif isinstance(p3_development_decision, Mapping):
            dec_data = dict(p3_development_decision)
        else:
            raise ReportingError("p3_development_decision must be a Path or Mapping")

        gate_status = dec_data.get("gate_status")
        if gate_status not in {"RETAINED", "NOT_RETAINED", "retained", "not_retained", "not_applicable"}:
            raise ReportingError(f"invalid gate_status in frozen P3 development decision: {gate_status}")
        norm_gate_status = "RETAINED" if str(gate_status).upper() == "RETAINED" else "NOT_RETAINED"
        return {
            "schema_version": REPORTING_SCHEMA_VERSION,
            "gate_status": norm_gate_status,
            "decision": norm_gate_status,
            "source_split_role": "development",
            "source_type": "frozen_development_decision_artifact",
            "confirmatory_inspection": "PROHIBITED",
            "claims_permitted": False,
            "headroom_gate_definition": dec_data.get("headroom_gate_definition", {
                "rule": "P3 is retained only if development evidence exhibits sufficient ranking headroom.",
                "min_ranking_error_families": 3,
                "min_ranking_error_fraction": 0.05,
            }),
            "observed_development_evidence": dec_data.get("observed_development_evidence", {}),
            "rationale": dec_data.get("rationale", f"Frozen development gate decision loaded from artifact with status '{norm_gate_status}'."),
        }

    if gold.role == "confirmatory":
        if gold.p3_gate_identity is not None:
            gate_status_raw = gold.p3_gate_identity.get("status")
            norm_status = "RETAINED" if str(gate_status_raw).lower() == "retained" else "NOT_RETAINED"
            return {
                "schema_version": REPORTING_SCHEMA_VERSION,
                "gate_status": norm_status,
                "decision": norm_status,
                "source_split_role": "development",
                "source_type": "confirmatory_freeze_manifest_snapshot",
                "p3_gate_identity": dict(gold.p3_gate_identity),
                "confirmatory_inspection": "PROHIBITED",
                "claims_permitted": False,
                "headroom_gate_definition": {
                    "rule": "P3 is retained only if development evidence exhibits sufficient ranking headroom.",
                    "min_ranking_error_families": 3,
                    "min_ranking_error_fraction": 0.05,
                },
                "observed_development_evidence": {},
                "rationale": f"Frozen development gate loaded from authoritative freeze manifest snapshot (status: '{norm_status}'). Confirmatory observations did not influence this gate.",
            }
        return {
            "schema_version": REPORTING_SCHEMA_VERSION,
            "gate_status": "NOT_AVAILABLE",
            "decision": "UNAVAILABLE_NO_FROZEN_DEVELOPMENT_GATE",
            "source_split_role": "development",
            "source_type": "none_provided",
            "confirmatory_inspection": "PROHIBITED",
            "claims_permitted": False,
            "headroom_gate_definition": {
                "rule": "P3 is retained only if development evidence exhibits sufficient ranking headroom.",
                "min_ranking_error_families": 3,
                "min_ranking_error_fraction": 0.05,
            },
            "observed_development_evidence": {},
            "rationale": "Confirmatory evidence was not accompanied by a frozen development gate artifact. Confirmatory evidence must NEVER be inspected to decide P3 enablement.",
        }

    # Development split
    headroom = component_result.p3_headroom
    p2_aggregates = component_result.aggregates.get("P2", {})
    ranking_errors = p2_aggregates.get("primary_categories", {}).get("RANKING_ERROR", 0)

    gate_status = headroom.get("gate_status", "NOT_RETAINED")
    decision = "RETAINED" if str(gate_status).upper() == "RETAINED" else "NOT_RETAINED"

    return {
        "schema_version": REPORTING_SCHEMA_VERSION,
        "gate_status": decision,
        "decision": decision,
        "source_split_role": "development",
        "source_type": "evaluated_development_split",
        "confirmatory_inspection": "PROHIBITED",
        "claims_permitted": False,
        "headroom_gate_definition": {
            "rule": "P3 is retained only if development evidence exhibits sufficient ranking headroom.",
            "min_ranking_error_families": headroom.get("threshold_ranking_families", 3),
            "min_ranking_error_fraction": headroom.get("threshold_ranking_rate", 0.05),
        },
        "observed_development_evidence": {
            "eligible_families": headroom.get("eligible_families", 0),
            "ranking_error_families": headroom.get("ranking_error_families", 0),
            "ranking_error_rate": headroom.get("ranking_error_rate", 0.0),
            "total_ranking_error_recommendations": ranking_errors,
        },
        "rationale": (
            f"Predefined development gate evaluated {headroom.get('eligible_families', 0)} eligible families. "
            f"Observed {headroom.get('ranking_error_families', 0)} ranking-error families "
            f"({_fmt_val(headroom.get('ranking_error_rate', 0.0), pct=True)} of eligible families). "
            f"Gate decision is '{decision}'."
        ),
    }


def format_p3_decision_md(data: dict[str, Any]) -> str:
    lines = [
        "## 3. P3 Development Gate Decision Report",
        "",
    ]
    gate_status = data.get("gate_status")
    decision = data.get("decision")
    rationale = data.get("rationale", "")
    source_type = data.get("source_type", "unknown")

    if gate_status == "NOT_AVAILABLE":
        lines.extend([
            "> [!WARNING]",
            "> **P3 Development Gate: `NOT_AVAILABLE`**",
            f"> {rationale}",
            "",
        ])
        return "\n".join(lines)

    alert_type = "TIP" if decision == "RETAINED" else "WARNING"
    lines.extend([
        f"> [!{alert_type}]",
        f"> **P3 Development Gate Decision: `{decision}`** (Source: `{source_type}`)",
        f"> {rationale}",
        "",
        "> [!IMPORTANT]",
        "> **CONFIRMATORY ISOLATION ACTIVE**: Confirmatory observations are strictly isolated and did not influence the P3 development gate decision.",
        "",
        "### Predefined Reranking Headroom Criteria",
        "",
        f"- **Minimum Ranking-Error Families Required**: `{data.get('headroom_gate_definition', {}).get('min_ranking_error_families', 3)}`",
        f"- **Minimum Ranking-Error Fraction Required**: `{_fmt_val(data.get('headroom_gate_definition', {}).get('min_ranking_error_fraction', 0.05), pct=True)}`",
        "",
    ])
    obs = data.get("observed_development_evidence", {})
    if obs:
        lines.extend([
            "### Observed Development Evidence",
            "",
            f"- **Eligible Workload Families**: `{obs.get('eligible_families', 0)}`",
            f"- **Observed Ranking-Error Families**: `{obs.get('ranking_error_families', 0)}`",
            f"- **Observed Ranking-Error Rate**: `{_fmt_val(obs.get('ranking_error_rate', 0.0), pct=True)}`",
            f"- **Total Ranking-Error Attempts**: `{obs.get('total_ranking_error_recommendations', 0)}`",
            "",
        ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output 8: Automatic Limitations Block & Narrative Report
# ---------------------------------------------------------------------------


def compute_limitations_block(
    statistical_result: StatisticalAnalysisResult,
    component_result: AnalysisResult,
    gold: GoldSource,
    provenance: Mapping[str, Any],
    evidence_status: str,
    *,
    missing_evidence: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Assemble strict automated limitations block."""
    total_families = len({c.family_id for c in gold.cases})
    total_variants = len(gold.cases)

    fallbacks: Counter[str] = Counter()
    p2_agg = component_result.aggregates.get("P2", {})
    fb_count = p2_agg.get("fallback_recommendations", 0)

    claims_permitted = bool(gold.role == "confirmatory" and evidence_status in {"OBSERVED", "RAW_EVIDENCE_COMPLETE", "DERIVED_EVIDENCE_COMPLETE"})

    supported_statements = []
    paired_map = {
        (row.get("comparison_id", f"{row.get('second_system')}_minus_{row.get('first_system')}"), row["endpoint"]): row
        for row in statistical_result.paired_comparisons
    }
    ja_comp = paired_map.get(("P2_minus_P1", "joint_accept_at_1"), {})
    ja_status = ja_comp.get("hypothesis_status")
    ja_dec = ja_comp.get("statistical_decision")
    ja_diff = ja_comp.get("effects", {}).get("mean_difference")
    ja_raw_p = ja_comp.get("p_value_raw")
    ja_holm_p = ja_comp.get("p_value_holm")
    effective_p = ja_holm_p if ja_holm_p is not None else ja_raw_p
    ci_low = ja_comp.get("ci_low")
    ci_high = ja_comp.get("ci_high")

    # Strict inferential superiority claim check:
    # 1. claims_permitted must be True (confirmatory split + observed evidence)
    # 2. hypothesis_status must be "TESTED"
    # 3. statistical_decision must be "REJECT_NULL"
    # 4. effect must be positive (mean_difference > 0)
    # 5. 95% bootstrap CI must strictly exclude zero (ci_low > 0)
    if (
        claims_permitted
        and ja_status == "TESTED"
        and ja_dec == "REJECT_NULL"
        and ja_diff is not None
        and ja_diff > 0
        and ci_low is not None
        and ci_low > 0
        and effective_p is not None
        and effective_p < DEFAULT_ALPHA
    ):
        supported_statements.append(
            f"P2 significantly outperforms P1 on JointAccept@1 (mean difference Δ = {_fmt_val(ja_diff, pct=True)}, 95% CI {_fmt_ci((ci_low, ci_high), pct=True)}, p = {_fmt_pval(effective_p)})."
        )
    elif ja_diff is not None:
        supported_statements.append(
            f"Observed JointAccept@1 mean difference Δ = {_fmt_val(ja_diff, pct=True)} (95% CI {_fmt_ci((ci_low, ci_high), pct=True)}, p = {_fmt_pval(effective_p)}, decision: {ja_dec or 'UNTESTED'})."
        )

    missing_items: list[str] = []
    if missing_evidence is not None:
        missing_items = list(missing_evidence)
    elif evidence_status not in {"OBSERVED", "RAW_EVIDENCE_COMPLETE", "DERIVED_EVIDENCE_COMPLETE"}:
        missing_items = ["Observed confirmatory cluster runs are pending."]

    return {
        "schema_version": REPORTING_SCHEMA_VERSION,
        "claims_permitted": claims_permitted,
        "evidence_status": evidence_status,
        "family_N": total_families,
        "variant_count": total_variants,
        "split_id": gold.split.manifest.split_id if gold.split is not None else "synthetic",
        "split_role": gold.role,
        "dataset_checksum": gold.canonical_sha256,
        "unexecuted_conditions": [
            "E3 (B0 vs P2 Real Human Usability Study): NOT_EXECUTED in offline harness",
            "E4 (Resource Efficiency & Live Cluster Execution): NOT_EXECUTED in offline harness",
            "E5 (Image Correctness & Multi-Node Image Storage): NOT_EXECUTED in offline harness",
        ],
        "missing_evidence": missing_items,
        "fallback_details": {
            "total_fallback_invocations": fb_count,
            "deterministic_degradation_active": True,
        },
        "supported_statistical_statements": supported_statements,
        "claim_boundary_rules": [
            "Workload FAMILY is the semantic independent unit; variants and repeats do not inflate N.",
            "Never claim general superiority unless Holm-adjusted p < 0.05 and confidence intervals strictly exclude zero.",
            "B0 does not generate rankings; MRR/nDCG/Hit@K are prohibited for B0.",
        ],
    }


def format_limitations_md(data: dict[str, Any]) -> str:
    lines = [
        "## 4. Automatic Limitations & Provenance Block",
        "",
        f"- **Independent Workload Families ($N$)**: `{data['family_N']}`",
        f"- **Total Prompt Variants Evaluated**: `{data['variant_count']}`",
        f"- **Dataset Split**: `{data['split_id']}` (Role: `{data['split_role']}`)",
        f"- **Dataset Canonical SHA-256**: `{data['dataset_checksum'][:16]}...`",
        f"- **Claims Permitted**: `{'YES (Confirmatory Evidence)' if data['claims_permitted'] else 'NO (Formative/Development Evidence Only)'}`",
        f"- **Fallback Invocations**: `{data['fallback_details']['total_fallback_invocations']}`",
        "",
        "### Missing Evidence & Completeness Status",
        "",
    ]
    if data["missing_evidence"]:
        for item in data["missing_evidence"]:
            lines.append(f"- {item}")
    else:
        lines.append("- *No missing evidence: all planned offline evaluation cells for E1/E2 were executed and validated.*")
    lines.append("")
    lines.append("### Unexecuted Experimental Conditions",
    )
    lines.append("")
    for unexec in data["unexecuted_conditions"]:
        lines.append(f"- {unexec}")
    lines.append("")
    lines.append("### Machine-Readable Statistical Statements")
    lines.append("")
    if data["supported_statistical_statements"]:
        for stmt in data["supported_statistical_statements"]:
            lines.append(f"- {stmt}")
    else:
        lines.append("- *No confirmatory hypothesis tests meet significance thresholds.*")
    lines.append("")
    lines.append("### Claim Boundary Constraints")
    lines.append("")
    for rule in data["claim_boundary_rules"]:
        lines.append(f"- {rule}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Comprehensive Synthesis Report Generator
# ---------------------------------------------------------------------------


def generate_synthesis_report(
    quality_data: dict[str, Any],
    robustness_data: dict[str, Any],
    retrieval_data: dict[str, Any],
    error_data: dict[str, Any],
    paired_data: dict[str, Any],
    ci_data: dict[str, Any],
    p3_decision: dict[str, Any],
    limitations: dict[str, Any],
    manifest_info: dict[str, Any],
) -> str:
    lines = [
        "# Protocol-v5 Offline Research Report: E1 (Quality) & E2 (Robustness)",
        "",
        f"**Protocol Version**: `5.0.0` | **Generated UTC**: `{manifest_info.get('created_at_utc', _utc_now())}` | **Git Revision**: `{manifest_info.get('git_revision', 'unknown')[:10]}`",
        "",
        "---",
        "",
        format_recommendation_quality_md(quality_data),
        "---",
        "",
        format_robustness_md(robustness_data),
        "---",
        "",
        "## Figures and Visualizations",
        "",
        "### Figure 1: P2 Retrieval Channel Recall@K Ablation",
        "![P2 Retrieval Recall@K](figures/retrieval_recall_at_k.svg)",
        "",
        "### Figure 2: P2 Earliest-Failure Taxonomy",
        "![P2 Error Taxonomy](figures/error_taxonomy.svg)",
        "",
        "### Figure 3: Paired Family JointAccept@1 Outcomes",
        "![Paired Family Outcomes](figures/paired_family_outcomes.svg)",
        "",
        "### Figure 4: Confidence Interval Forest Plot",
        "![Confidence Intervals](figures/confidence_intervals.svg)",
        "",
        "---",
        "",
        format_p3_decision_md(p3_decision),
        "---",
        "",
        format_limitations_md(limitations),
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core Analysis & Report Generation Entry Point
# ---------------------------------------------------------------------------


def generate_offline_report(
    evidence_dir: Path,
    gold_path: Path,
    output_dir: Path,
    *,
    role: str = "development",
    freeze_path: Path | None = None,
    split_id: str | None = None,
    p3_development_decision: Path | Mapping[str, Any] | None = None,
    missing_evidence: Sequence[str] | None = None,
    retrieval_ks: Sequence[int] = DEFAULT_RETRIEVAL_KS,
    bootstrap_replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
    created_at_utc: str | None = None,
) -> Path:
    """Read evidence, derive component & statistical metrics, and write complete reporting layer."""
    _check_output_dir_safety(output_dir)
    _prevalidate_completed_evidence_envelope(evidence_dir)
    gold = load_component_gold(
        gold_path,
        role=role,
        freeze_path=freeze_path,
        split_id=split_id,
    )
    _require_v2_gold(gold)
    provenance, records = load_validated_evidence(
        evidence_dir,
        gold,
        systems=("P1", "P2", "P3"),
        require_systems=False,
    )

    # Validate provenance and split binding
    prov_split = provenance.get("split", {})
    valid_dataset_shas = {gold.source_file_sha256, gold.canonical_sha256}
    valid_bundle_shas = {gold.canonical_sha256, gold.source_file_sha256}
    if gold.split is not None:
        valid_dataset_shas.add(gold.split.source_file_sha256)
        valid_dataset_shas.add(gold.split.manifest.checksum)
        valid_bundle_shas.add(gold.split.manifest.checksum)
        valid_bundle_shas.add(gold.split.source_file_sha256)

    if prov_split.get("dataset_sha256") and prov_split["dataset_sha256"] not in valid_dataset_shas:
        raise ReportingError(
            f"evidence dataset checksum mismatch: evidence has {prov_split['dataset_sha256']} but gold has source={gold.source_file_sha256}, canonical={gold.canonical_sha256}"
        )
    if prov_split.get("bundle_checksum") and prov_split["bundle_checksum"] not in valid_bundle_shas:
        raise ReportingError(
            f"evidence bundle checksum mismatch: evidence has {prov_split['bundle_checksum']} but gold has {gold.canonical_sha256}"
        )
    if prov_split.get("role") and prov_split["role"] != gold.role:
        raise ReportingError(
            f"evidence split role mismatch: evidence has {prov_split['role']} but gold has {gold.role}"
        )

    # Validate completion JSON
    raw_completion_path = evidence_dir / REPORT_DIRECTORY_NAME / COMPLETION_FILENAME
    raw_status = "OBSERVED"
    if raw_completion_path.is_file():
        try:
            raw_comp = json.loads(raw_completion_path.read_text(encoding="utf-8"))
            raw_status = raw_comp.get("status", "OBSERVED")
            if raw_comp.get("provenance_fingerprint") != provenance.get("provenance_fingerprint"):
                raise ReportingError("completion provenance fingerprint does not match offline run provenance")
            records_file = evidence_dir / RAW_DIRECTORY_NAME / RECORDS_FILENAME
            if records_file.is_file():
                if raw_comp.get("recommendations_jsonl_sha256") != file_sha256(records_file):
                    raise ReportingError("completion records sha256 does not match recommendations.jsonl")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReportingError("unreadable completion metadata in evidence directory") from exc

    systems = {str(rec.get("system_id")) for rec in records}
    if not {"P1", "P2"}.issubset(systems):
        raise ReportingError("validated evidence does not contain both P1 and P2")

    ks = _validate_retrieval_ks(retrieval_ks)

    # 1. Run statistical analysis
    stat_result = analyze_statistical_records(
        gold,
        records,
        retrieval_ks=ks,
        bootstrap_replicates=bootstrap_replicates,
        bootstrap_seed=bootstrap_seed,
    )

    # 2. Run component scoring (scoped to P2 / P3 structured pipelines)
    p2_p3_records = tuple(r for r in records if r.get("system_id") in {"P2", "P3"})
    comp_result = score_component_records(
        gold,
        p2_p3_records,
        retrieval_ks=ks,
    )

    # 3. Compute derived reporting datasets
    quality_data = compute_recommendation_quality_data(stat_result, comp_result, records)
    robustness_data = compute_robustness_table_data(stat_result, records, gold)
    retrieval_data = compute_retrieval_ablation_data(records, gold, ks)
    error_data = compute_error_taxonomy_data(comp_result)
    paired_data = compute_paired_family_outcomes_data(stat_result, gold)
    ci_data = compute_confidence_intervals_data(stat_result)
    p3_decision = compute_p3_development_decision(
        comp_result,
        gold,
        p3_development_decision=p3_development_decision,
    )

    limitations = compute_limitations_block(
        stat_result,
        comp_result,
        gold,
        provenance,
        raw_status,
        missing_evidence=missing_evidence,
    )

    canonical_time = created_at_utc or provenance.get("completed_utc") or provenance.get("created_utc") or _utc_now()
    manifest_info = {
        "schema_version": REPORTING_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "created_at_utc": canonical_time,
        "git_revision": _git_revision(),
        "status": "REPORT_COMPLETE",
        "claims_permitted": limitations["claims_permitted"],
        "source": {
            "offline_run_id": provenance.get("run_id"),
            "dataset_id": gold.dataset_id,
            "split_role": gold.role,
            "split_id": gold.split.manifest.split_id if gold.split is not None else None,
            "dataset_checksum": gold.canonical_sha256,
        },
        "systems": sorted(systems),
        "retrieval_ks": list(ks),
        "bootstrap": {
            "replicates": bootstrap_replicates,
            "base_seed": bootstrap_seed,
        },
    }

    # 4. Write all outputs exclusively
    output_dir.mkdir(parents=True, exist_ok=False)

    # Write Tables (CSV + JSON)
    _write_csv(output_dir / TABLE_FILES["recommendation_quality"], _flatten_rows(quality_data["rows"]))
    _write_json_exclusive(output_dir / TABLE_FILES["recommendation_quality_json"], quality_data)

    _write_csv(output_dir / TABLE_FILES["robustness"], _flatten_rows(robustness_data["rows"]))
    _write_json_exclusive(output_dir / TABLE_FILES["robustness_json"], robustness_data)

    _write_csv(output_dir / TABLE_FILES["retrieval_ablation"], _flatten_rows(retrieval_data["results"]))
    _write_json_exclusive(output_dir / TABLE_FILES["retrieval_ablation_json"], retrieval_data)

    _write_csv(output_dir / TABLE_FILES["error_taxonomy"], _flatten_rows(error_data["categories"]))
    _write_json_exclusive(output_dir / TABLE_FILES["error_taxonomy_json"], error_data)

    _write_csv(output_dir / TABLE_FILES["paired_family_outcomes"], _flatten_rows(paired_data["families"]))
    _write_json_exclusive(output_dir / TABLE_FILES["paired_family_outcomes_json"], paired_data)

    _write_csv(output_dir / TABLE_FILES["confidence_intervals"], _flatten_rows(ci_data["estimates"]))
    _write_json_exclusive(output_dir / TABLE_FILES["confidence_intervals_json"], ci_data)

    _write_json_exclusive(output_dir / TABLE_FILES["p3_development_decision_json"], p3_decision)

    # Write Figures (SVGs)
    _write_file_exclusive(output_dir / FIGURE_FILES["retrieval_recall_at_k"], render_retrieval_recall_svg(retrieval_data))
    _write_file_exclusive(output_dir / FIGURE_FILES["error_taxonomy"], render_error_taxonomy_svg(error_data))
    _write_file_exclusive(output_dir / FIGURE_FILES["paired_family_outcomes"], render_paired_family_outcomes_svg(paired_data))
    _write_file_exclusive(output_dir / FIGURE_FILES["confidence_intervals"], render_confidence_intervals_svg(ci_data))

    # Write Sub-Reports (Markdown)
    _write_file_exclusive(output_dir / RECOMMENDATION_QUALITY_MD, format_recommendation_quality_md(quality_data))
    _write_file_exclusive(output_dir / ROBUSTNESS_MD, format_robustness_md(robustness_data))
    _write_file_exclusive(output_dir / P3_DECISION_MD, format_p3_decision_md(p3_decision))
    _write_file_exclusive(output_dir / LIMITATIONS_MD, format_limitations_md(limitations))

    # Write Synthesis Report
    synthesis_md = generate_synthesis_report(
        quality_data,
        robustness_data,
        retrieval_data,
        error_data,
        paired_data,
        ci_data,
        p3_decision,
        limitations,
        manifest_info,
    )
    _write_file_exclusive(output_dir / SYNTHESIS_REPORT_FILENAME, synthesis_md)

    # Checksums
    output_checksums = {}
    for rel_path in list(TABLE_FILES.values()) + list(FIGURE_FILES.values()) + [
        RECOMMENDATION_QUALITY_MD,
        ROBUSTNESS_MD,
        P3_DECISION_MD,
        LIMITATIONS_MD,
        SYNTHESIS_REPORT_FILENAME,
    ]:
        p = output_dir / rel_path
        if p.is_file():
            output_checksums[rel_path] = file_sha256(p)

    manifest_info["outputs"] = output_checksums
    _write_json_exclusive(output_dir / REPORT_MANIFEST_FILENAME, manifest_info)

    return output_dir


def write_not_executed_report(
    output_dir: Path,
    *,
    reason: str,
    reason_code: str = "INPUTS_NOT_SUPPLIED",
    created_at_utc: str | None = None,
) -> Path:
    """Write an explicit, compliant NOT_EXECUTED report package."""
    _check_output_dir_safety(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    canonical_time = created_at_utc or _utc_now()
    manifest = {
        "schema_version": REPORTING_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "status": "NOT_EXECUTED",
        "claims_permitted": False,
        "created_at_utc": canonical_time,
        "git_revision": _git_revision(),
        "reason_code": reason_code,
        "reason": reason,
        "outputs": {},
    }
    _write_json_exclusive(output_dir / REPORT_MANIFEST_FILENAME, manifest)

    not_executed_md = f"""# Protocol-v5 Offline Research Report: E1/E2

**Status**: `NOT_EXECUTED` | **Reason Code**: `{reason_code}`

> [!WARNING]
> This analysis report was not executed: {reason}
> No empirical metrics, confidence intervals, or superiority claims are generated.

## Limitations

- No Protocol-v5 experiment was executed.
- Claims permitted: `False`.
"""
    _write_file_exclusive(output_dir / SYNTHESIS_REPORT_FILENAME, not_executed_md)
    return output_dir


def _flatten_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    flattened = []
    for r in rows:
        flat: dict[str, Any] = {}
        for k, v in r.items():
            if isinstance(v, Mapping):
                for sub_k, sub_v in v.items():
                    flat[f"{k}_{sub_k}"] = sub_v
            else:
                flat[k] = v
        flattened.append(flat)
    return flattened


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        _write_file_exclusive(path, "")
        return
    fieldnames = sorted({f for r in rows for f in r})
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o644)
    with open(fd, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow({f: _csv_cell(r.get(f)) for f in fieldnames})


def _csv_cell(val: Any) -> Any:
    if val is None:
        return ""
    if isinstance(val, (dict, list, tuple)):
        return json.dumps(val, sort_keys=True, separators=(",", ":"))
    return val


def _parse_ks(value: str) -> tuple[int, ...]:
    try:
        selected = tuple(sorted({int(item.strip()) for item in value.split(",")}))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("retrieval K must be comma-separated integers") from exc
    if not selected or any(item < 1 for item in selected):
        raise argparse.ArgumentTypeError("retrieval K values must be positive")
    return selected


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--gold-dataset", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--role", choices=("development", "confirmatory"), default="development")
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--split-id")
    parser.add_argument("--p3-development-decision", type=Path)
    parser.add_argument("--created-at-utc", type=str)
    parser.add_argument("--retrieval-k", type=_parse_ks, default=DEFAULT_RETRIEVAL_KS)
    parser.add_argument("--bootstrap-replicates", type=int, default=DEFAULT_BOOTSTRAP_REPLICATES)
    parser.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument(
        "--not-executed-reason",
        default="Complete validated Protocol-v5 offline evidence and gold were not supplied.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.status_only:
            if args.evidence_dir is not None or args.gold_dataset is not None:
                raise ReportingError("--status-only cannot be combined with evidence or gold inputs")
            output = write_not_executed_report(
                args.output_dir,
                reason=args.not_executed_reason,
                created_at_utc=args.created_at_utc,
            )
        elif args.evidence_dir is None or args.gold_dataset is None:
            output = write_not_executed_report(
                args.output_dir,
                reason="Complete validated Protocol-v5 offline evidence and gold dataset were not both supplied.",
                reason_code="INPUTS_NOT_SUPPLIED",
                created_at_utc=args.created_at_utc,
            )
        else:
            output = generate_offline_report(
                args.evidence_dir,
                args.gold_dataset,
                args.output_dir,
                role=args.role,
                freeze_path=args.freeze,
                split_id=args.split_id,
                p3_development_decision=args.p3_development_decision,
                retrieval_ks=args.retrieval_k,
                bootstrap_replicates=args.bootstrap_replicates,
                bootstrap_seed=args.bootstrap_seed,
                created_at_utc=args.created_at_utc,
            )
        status_info = json.loads((output / REPORT_MANIFEST_FILENAME).read_text(encoding="utf-8"))
        print(json.dumps({"status": status_info["status"], "output_dir": str(output)}, sort_keys=True))
        return 0
    except (
        ComponentAnalysisError,
        ContractValidationError,
        GoldDatasetValidationError,
        OfflineEvidenceValidationError,
        ReportingError,
        SplitBundleValidationError,
        StatisticalAnalysisError,
        OSError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {
                    "schema_version": REPORTING_SCHEMA_VERSION,
                    "status": "ERROR",
                    "claims_permitted": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "REPORTING_SCHEMA_VERSION",
    "ReportingError",
    "compute_confidence_intervals_data",
    "compute_error_taxonomy_data",
    "compute_limitations_block",
    "compute_p3_development_decision",
    "compute_paired_family_outcomes_data",
    "compute_recommendation_quality_data",
    "compute_retrieval_ablation_data",
    "compute_robustness_table_data",
    "format_limitations_md",
    "format_p3_decision_md",
    "format_recommendation_quality_md",
    "format_robustness_md",
    "generate_offline_report",
    "generate_synthesis_report",
    "main",
    "render_confidence_intervals_svg",
    "render_error_taxonomy_svg",
    "render_paired_family_outcomes_svg",
    "render_retrieval_recall_svg",
    "write_not_executed_report",
]
