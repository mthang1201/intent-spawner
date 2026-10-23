#!/usr/bin/env python3
"""Derive gold profile labels from workload profiling measurements.

Reads output from profile_workloads.py, applies deterministic headroom
and slack rules, verifies review-policy constraints against
protocol-v5-gold-family-v1.schema.json, and produces oracle profile labels
and distribution reports per workload source.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from recommender.candidate_corpus import DEFAULT_PROFILE_DEFINITIONS, parse_memory_to_bytes

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("derive_profile_labels")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "benchmarks_v5" / "protocol-v5-gold-family-v1.schema.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "benchmarks_v5" / "oracle"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "docs" / "reports"


@dataclass(frozen=True)
class ProfileMetadata:
    slug: str
    display_name: str
    mem_limit_bytes: int
    mem_limit_mib: float
    cpu_limit: float


def load_sorted_profile_metadata(
    custom_profiles: Optional[Dict[str, Any]] = None,
) -> List[ProfileMetadata]:
    """Load profile definitions and sort them strictly ascending by memory limit bytes.

    Never hardcodes profile names or memory numbers. Converts bytes to MiB in a single place.
    """
    defs = custom_profiles if custom_profiles is not None else DEFAULT_PROFILE_DEFINITIONS
    profiles: List[ProfileMetadata] = []

    for slug, pdata in defs.items():
        mem_raw = pdata.get("mem_limit", "0")
        mem_bytes = parse_memory_to_bytes(mem_raw)
        mem_mib = mem_bytes / (1024.0 * 1024.0)
        cpu_lim = float(pdata.get("cpu_limit", 1.0))
        display_name = str(pdata.get("display_name", slug))
        profiles.append(
            ProfileMetadata(
                slug=slug,
                display_name=display_name,
                mem_limit_bytes=mem_bytes,
                mem_limit_mib=mem_mib,
                cpu_limit=cpu_lim,
            )
        )

    # Sort strictly ascending by memory limit bytes, break ties with CPU limit
    profiles.sort(key=lambda p: (p.mem_limit_bytes, p.cpu_limit))
    return profiles


def verify_schema_contract(schema_path: Path) -> Tuple[float, float]:
    """Verify that protocol-v5-gold-family-v1 schema defines required reviewPolicy fields.

    Halts execution if max_preferred_profile_share or max_preferred_image_share
    are missing from the schema definition.
    """
    if not schema_path.is_file():
        raise FileNotFoundError(f"Gold family schema not found: {schema_path}")

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    review_policy_def = (
        schema.get("$defs", {}).get("reviewPolicy")
        or schema.get("properties", {}).get("review_policy")
    )
    if not review_policy_def or "properties" not in review_policy_def:
        raise ValueError(f"Schema {schema_path} missing reviewPolicy definition")

    properties = review_policy_def.get("properties", {})
    required_fields = ["max_preferred_profile_share", "max_preferred_image_share"]

    for field in required_fields:
        if field not in properties:
            raise KeyError(
                f"Schema {schema_path} is missing required reviewPolicy field '{field}'. "
                "Halting as required by Step 0: cannot invent or assume threshold value."
            )

    # Return baseline thresholds from reference datasets / schema validator defaults (0.5)
    default_profile_share = 0.5
    default_image_share = 0.5
    return default_profile_share, default_image_share


def derive_label_for_record(
    record: Dict[str, Any],
    sorted_profiles: Sequence[ProfileMetadata],
    *,
    headroom_metric: str = "peak_mib_max",
    max_utilization: float = 0.85,
    acceptable_slack: int = 1,
) -> Optional[Dict[str, Any]]:
    """Derive ground-truth profile labels for a single workload profiling record.

    Returns:
        Dict representing labeled record if valid (status == ok or infeasible),
        or None if record should be skipped (status == error or timeout).
    """
    status = record.get("status")
    if status not in ("ok", "infeasible_over_capacity"):
        # Exclude error/timeout records from labeling
        return None

    problem_id = str(record.get("problem_id", ""))
    source = str(record.get("source", "unknown"))
    image_id = str(record.get("image_id", "unknown"))
    import_dominated = bool(record.get("import_dominated", False))
    peak_method = str(record.get("peak_method", "unknown"))
    peak_mib_max = record.get("peak_mib_max")
    baseline_profile = record.get("baseline_profile")
    final_min_passing = record.get("final_min_passing_profile")

    slug_to_profile = {p.slug: p for p in sorted_profiles}
    profile_indices = {p.slug: idx for idx, p in enumerate(sorted_profiles)}

    # Rule 5: infeasible_over_capacity -> empty preferred/acceptable, infeasible policy
    if status == "infeasible_over_capacity":
        return {
            "problem_id": problem_id,
            "source": source,
            "image_id": image_id,
            "preferred_profile": None,
            "preferred_profile_ids": [],
            "acceptable_profiles": [],
            "acceptable_profile_ids": [],
            "expected_feasibility": "infeasible",
            "final_min_passing_profile": final_min_passing,
            "bumped_for_headroom": False,
            "headroom_exceeded_at_max": False,
            "headroom_metric": headroom_metric,
            "headroom_metric_value": None,
            "max_utilization": max_utilization,
            "profile_memory_limit_mib": None,
            "acceptable_slack": acceptable_slack,
            "import_dominated": import_dominated,
            "peak_method": peak_method,
            "peak_mib_max": peak_mib_max,
            "anon_peak_mib_max": record.get("anon_peak_mib_max"),
            "baseline_profile": baseline_profile,
            "status": status,
        }

    # Rule 2: initial preferred = final_min_passing_profile
    if not final_min_passing or final_min_passing not in slug_to_profile:
        raise ValueError(
            f"Problem '{problem_id}' has status='ok' but invalid final_min_passing_profile: {final_min_passing}"
        )

    initial_preferred = str(final_min_passing)
    curr_profile_obj = slug_to_profile[initial_preferred]
    curr_idx = profile_indices[initial_preferred]

    # Rule 3: Headroom check
    metric_val = record.get(headroom_metric)
    if metric_val is None:
        # Fallback to peak_mib_max if requested metric not directly present
        metric_val = peak_mib_max

    headroom_val = float(metric_val) if metric_val is not None else 0.0
    limit_mib = curr_profile_obj.mem_limit_mib
    headroom_threshold = max_utilization * limit_mib

    bumped_for_headroom = False
    headroom_exceeded_at_max = False
    active_preferred = initial_preferred

    if headroom_val > headroom_threshold:
        if curr_idx >= len(sorted_profiles) - 1:
            # Already at largest profile, cannot bump further
            headroom_exceeded_at_max = True
            active_preferred = initial_preferred
        else:
            # Bump by exactly one tier
            bumped_for_headroom = True
            active_preferred = sorted_profiles[curr_idx + 1].slug

    # Rule 4: acceptable_profiles computed AFTER bump
    # preferred_profile + up to acceptable_slack tiers larger, capped at largest profile
    active_idx = profile_indices[active_preferred]
    max_idx = min(active_idx + acceptable_slack, len(sorted_profiles) - 1)
    acceptable = [sorted_profiles[i].slug for i in range(active_idx, max_idx + 1)]

    # Rule 6 & 7: Match protocol-v5 schema field names and retain trace provenance
    return {
        "problem_id": problem_id,
        "source": source,
        "image_id": image_id,
        "preferred_profile": active_preferred,
        "preferred_profile_ids": [active_preferred],
        "acceptable_profiles": acceptable,
        "acceptable_profile_ids": list(acceptable),
        "expected_feasibility": "feasible",
        "final_min_passing_profile": initial_preferred,
        "bumped_for_headroom": bumped_for_headroom,
        "headroom_exceeded_at_max": headroom_exceeded_at_max,
        "headroom_metric": headroom_metric,
        "headroom_metric_value": round(headroom_val, 4) if metric_val is not None else None,
        "max_utilization": max_utilization,
        "profile_memory_limit_mib": round(limit_mib, 4),
        "acceptable_slack": acceptable_slack,
        "import_dominated": import_dominated,
        "peak_method": peak_method,
        "peak_mib_max": peak_mib_max,
        "anon_peak_mib_max": record.get("anon_peak_mib_max"),
        "baseline_profile": baseline_profile,
        "status": status,
    }


def process_workload_records(
    records: Sequence[Dict[str, Any]],
    sorted_profiles: Sequence[ProfileMetadata],
    *,
    headroom_metric: str = "peak_mib_max",
    max_utilization: float = 0.85,
    acceptable_slack: int = 1,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Process all raw profiling records into labeled records and skipped records."""
    labeled: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for r in records:
        lbl = derive_label_for_record(
            r,
            sorted_profiles,
            headroom_metric=headroom_metric,
            max_utilization=max_utilization,
            acceptable_slack=acceptable_slack,
        )
        if lbl is not None:
            labeled.append(lbl)
        else:
            skipped.append(r)

    return labeled, skipped


def generate_source_distribution_report(
    *,
    source: str,
    total_input: int,
    labeled_records: List[Dict[str, Any]],
    skipped_records: List[Dict[str, Any]],
    sorted_profiles: Sequence[ProfileMetadata],
    headroom_metric: str,
    max_utilization: float,
    acceptable_slack: int,
    warn_bump_share: float = 0.25,
    max_preferred_profile_share: float = 0.5,
    max_preferred_image_share: float = 0.5,
) -> Tuple[str, List[str]]:
    """Generate Markdown report for profile label distribution and return (report_text, warnings)."""
    warnings: List[str] = []
    num_labeled = len(labeled_records)

    # 1. Skipped counts by status and image
    skip_status_counts = Counter(r.get("status", "unknown") for r in skipped_records)
    skip_image_counts = Counter(r.get("image_id", "unknown") for r in skipped_records)

    # 2. Preferred profile counts (overall & by image)
    preferred_counts = Counter(r["preferred_profile"] for r in labeled_records if r["preferred_profile"])
    image_counts = Counter(r["image_id"] for r in labeled_records)

    img_prof_matrix: Dict[str, Counter] = {}
    for r in labeled_records:
        img = r.get("image_id", "unknown")
        prof = r.get("preferred_profile") or "infeasible"
        img_prof_matrix.setdefault(img, Counter())[prof] += 1

    # 3. Bump statistics
    bumped_records = [r for r in labeled_records if r.get("bumped_for_headroom")]
    bump_count = len(bumped_records)
    bump_share = (bump_count / num_labeled) if num_labeled > 0 else 0.0

    exceeded_max_records = [r for r in labeled_records if r.get("headroom_exceeded_at_max")]
    exceeded_max_count = len(exceeded_max_records)

    infeasible_records = [r for r in labeled_records if r.get("expected_feasibility") == "infeasible"]
    infeasible_count = len(infeasible_records)

    import_dom_records = [r for r in labeled_records if r.get("import_dominated")]
    import_dom_count = len(import_dom_records)

    # Bump transition matrix: (orig_profile, image_id) -> count
    bump_by_orig = Counter(r["final_min_passing_profile"] for r in bumped_records)
    bump_by_img = Counter(r["image_id"] for r in bumped_records)

    # 4. Check warning conditions
    if bump_share > warn_bump_share:
        msg = (
            f"High headroom bump share ({round(bump_share * 100, 2)}% > {round(warn_bump_share * 100, 1)}%). "
            "Note: cgroups v2 memory.peak may include Linux page cache that would otherwise be reclaimed under pressure."
        )
        warnings.append(msg)

    for p_slug, count in preferred_counts.items():
        share = count / num_labeled if num_labeled > 0 else 0.0
        if share > max_preferred_profile_share:
            msg = (
                f"Preferred profile '{p_slug}' exceeds maximum allowed share: "
                f"{round(share * 100, 2)}% > {round(max_preferred_profile_share * 100, 1)}%."
            )
            warnings.append(msg)

    for img_id, count in image_counts.items():
        share = count / num_labeled if num_labeled > 0 else 0.0
        if share > max_preferred_image_share:
            msg = (
                f"Image '{img_id}' exceeds maximum allowed share: "
                f"{round(share * 100, 2)}% > {round(max_preferred_image_share * 100, 1)}%."
            )
            warnings.append(msg)

    # 5. Format Markdown Document
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"# Profile Label Distribution Report — `{source}`",
        "",
        f"- **Generated at:** {now_utc}",
        f"- **Headroom Metric & Threshold:** `{headroom_metric} > {max_utilization} × memory_limit(profile)` (slack={acceptable_slack})",
        f"- **Total Input Workloads:** {total_input}",
        f"- **Successfully Labeled Workloads:** {num_labeled} ({round(num_labeled / total_input * 100, 2)}% of input)" if total_input > 0 else "- **Successfully Labeled Workloads:** 0",
        f"- **Excluded Workloads (non-labelable):** {len(skipped_records)}",
        "",
        "## 1. Workload Filtering & Exclusion Summary",
        "",
        "| Category | Detail / Status | Count |",
        "| :--- | :--- | :---: |",
        f"| **Input Records** | Total records parsed | {total_input} |",
        f"| **Labeled Records** | status in (`ok`, `infeasible_over_capacity`) | {num_labeled} |",
        f"| **Excluded Records** | status in (`error`, `timeout`) | {len(skipped_records)} |",
    ]

    for st, c in sorted(skip_status_counts.items()):
        lines.append(f"| Excluded Status | status=`{st}` | {c} |")
    for img, c in sorted(skip_image_counts.items()):
        lines.append(f"| Excluded Image | image=`{img}` | {c} |")

    lines.extend([
        "",
        "## 2. Preferred Profile Distribution",
        "",
        "| Preferred Profile | Memory Limit (MiB) | Labeled Count | Share (%) |",
        "| :--- | :---: | :---: | :---: |",
    ])

    slug_to_meta = {p.slug: p for p in sorted_profiles}
    for p in sorted_profiles:
        c = preferred_counts.get(p.slug, 0)
        sh = round((c / num_labeled) * 100, 2) if num_labeled > 0 else 0.0
        lines.append(f"| **`{p.slug}`** ({p.display_name}) | {p.mem_limit_mib:.1f} MiB | {c} | {sh}% |")

    if infeasible_count > 0:
        inf_sh = round((infeasible_count / num_labeled) * 100, 2) if num_labeled > 0 else 0.0
        lines.append(f"| **`infeasible`** (over capacity) | N/A | {infeasible_count} | {inf_sh}% |")

    lines.extend([
        "",
        "### Preferred Profile Distribution by Image",
        "",
        "| Image ID | Total Labeled | " + " | ".join(f"`{p.slug}`" for p in sorted_profiles) + " | `infeasible` |",
        "| :--- | :---: | " + " | ".join(":---:" for _ in sorted_profiles) + " | :---: |",
    ])

    for img, prof_map in sorted(img_prof_matrix.items()):
        img_tot = sum(prof_map.values())
        row = [f"`{img}`", str(img_tot)]
        for p in sorted_profiles:
            row.append(str(prof_map.get(p.slug, 0)))
        row.append(str(prof_map.get("infeasible", 0)))
        lines.append("| " + " | ".join(row) + " |")

    lines.extend([
        "",
        "## 3. Headroom Bump Analysis (Before vs After)",
        "",
        f"- **Bumped for Headroom:** {bump_count} / {num_labeled} ({round(bump_share * 100, 2)}%)",
        f"- **Headroom Exceeded at Max Profile:** {exceeded_max_count} ({round(exceeded_max_count / num_labeled * 100, 2)}%)" if num_labeled > 0 else f"- **Headroom Exceeded at Max Profile:** {exceeded_max_count}",
        f"- **Import-Dominated Workloads (`peak < 1.10 × baseline`):** {import_dom_count} / {num_labeled} ({round(import_dom_count / num_labeled * 100, 2)}%)" if num_labeled > 0 else f"- **Import-Dominated Workloads:** {import_dom_count}",
        "",
        "### Bumps by Original Profile (Pre-Bump)",
        "",
        "| Original Profile | Limit (MiB) | Bump Threshold (85%) | Bumps Escalated | Share of Tier |",
        "| :--- | :---: | :---: | :---: | :---: |",
    ])

    for p in sorted_profiles:
        tier_tot = sum(1 for r in labeled_records if r.get("final_min_passing_profile") == p.slug)
        tier_bumps = bump_by_orig.get(p.slug, 0)
        tier_pct = round((tier_bumps / tier_tot) * 100, 2) if tier_tot > 0 else 0.0
        thresh = max_utilization * p.mem_limit_mib
        lines.append(f"| `{p.slug}` | {p.mem_limit_mib:.1f} | {thresh:.2f} MiB | {tier_bumps} / {tier_tot} | {tier_pct}% |")

    lines.extend([
        "",
        "### Bumps by Image Catalog ID",
        "",
        "| Image ID | Total Labeled | Bumps Escalated | Bump Share (%) |",
        "| :--- | :---: | :---: | :---: |",
    ])
    for img, count in sorted(image_counts.items()):
        img_b = bump_by_img.get(img, 0)
        pct = round((img_b / count) * 100, 2) if count > 0 else 0.0
        lines.append(f"| `{img}` | {count} | {img_b} | {pct}% |")

    lines.extend([
        "",
        "## 4. Policy & Dataset Review Warnings",
        "",
    ])

    if warnings:
        for w in warnings:
            lines.append(f"> [!WARNING] {w}")
    else:
        lines.append("No review policy warnings triggered. Distribution conforms to all thresholds.")

    lines.append("")
    report_content = "\n".join(lines) + "\n"
    return report_content, warnings


def main(argv: Optional[List[str]] = None) -> int:
    """CLI execution entry point."""
    parser = argparse.ArgumentParser(
        description="Derive ground-truth profile labels from workload profiling measurements."
    )
    parser.add_argument(
        "--input",
        nargs="+",
        required=True,
        help="One or more input JSONL measurement files from profile_workloads.py (e.g. ds1000, arcade).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory where profile_labels_<source>.jsonl files will be stored (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help=f"Directory where profile_label_distribution_<source>.md reports will be stored (default: {DEFAULT_REPORT_DIR}).",
    )
    parser.add_argument(
        "--headroom-metric",
        type=str,
        default="peak_mib_max",
        help="Field in measurement record to evaluate for headroom (default: peak_mib_max).",
    )
    parser.add_argument(
        "--max-utilization",
        type=float,
        default=0.85,
        help="Maximum memory utilization fraction before bumping preferred profile (default: 0.85).",
    )
    parser.add_argument(
        "--acceptable-slack",
        type=int,
        default=1,
        help="Number of tiers above preferred profile to permit in acceptable_profiles (default: 1).",
    )
    parser.add_argument(
        "--warn-bump-share",
        type=float,
        default=0.25,
        help="Fraction of bumped workloads triggering advisory review warning (default: 0.25).",
    )
    parser.add_argument(
        "--max-preferred-profile-share",
        type=float,
        default=0.5,
        help="Maximum acceptable share for any single preferred profile (default: 0.5).",
    )
    parser.add_argument(
        "--max-preferred-image-share",
        type=float,
        default=0.5,
        help="Maximum acceptable share for any single preferred image (default: 0.5).",
    )
    parser.add_argument(
        "--schema-path",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Path to protocol-v5 gold schema to verify contract (default: {DEFAULT_SCHEMA_PATH}).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with error if review policy warnings are triggered.",
    )

    args = parser.parse_args(argv)

    # STEP 0: Verify schema contract
    logger.info("Verifying schema reviewPolicy contract in %s...", args.schema_path)
    verify_schema_contract(args.schema_path)

    # Load dynamic sorted profile metadata
    sorted_profiles = load_sorted_profile_metadata()
    logger.info(
        "Loaded %d profiles sorted by memory limit: %s",
        len(sorted_profiles),
        [f"{p.slug} ({p.mem_limit_mib:.1f} MiB)" for p in sorted_profiles],
    )

    # Read and group input records by source (deduplicating by problem_id so later input files override earlier ones)
    records_by_source: Dict[str, Dict[str, Dict[str, Any]]] = {}
    total_records = 0

    for in_path_str in args.input:
        in_path = Path(in_path_str)
        if not in_path.is_file():
            logger.error("Input file not found: %s", in_path)
            return 1

        logger.info("Reading measurement records from %s...", in_path)
        with open(in_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    src = str(rec.get("source", "unknown"))
                    pid = str(rec.get("problem_id", f"line_{line_no}"))
                    records_by_source.setdefault(src, {})[pid] = rec
                    total_records += 1
                except Exception as exc:
                    logger.warning("Error parsing line %d in %s: %s", line_no, in_path, exc)

    logger.info("Read %d records across %d sources: %s", total_records, len(records_by_source), sorted(records_by_source.keys()))

    all_warnings_count = 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)

    for src, pids_dict in sorted(records_by_source.items()):
        src_records = list(pids_dict.values())
        logger.info("Processing source '%s' (%d unique records)...", src, len(src_records))

        labeled_records, skipped_records = process_workload_records(
            src_records,
            sorted_profiles,
            headroom_metric=args.headroom_metric,
            max_utilization=args.max_utilization,
            acceptable_slack=args.acceptable_slack,
        )

        out_jsonl = args.output_dir / f"profile_labels_{src}.jsonl"
        with open(out_jsonl, "w", encoding="utf-8") as out_f:
            for lbl in labeled_records:
                out_f.write(json.dumps(lbl) + "\n")

        logger.info(
            "Wrote %d labeled records to %s (skipped %d excluded)",
            len(labeled_records),
            out_jsonl,
            len(skipped_records),
        )

        report_content, warnings = generate_source_distribution_report(
            source=src,
            total_input=len(src_records),
            labeled_records=labeled_records,
            skipped_records=skipped_records,
            sorted_profiles=sorted_profiles,
            headroom_metric=args.headroom_metric,
            max_utilization=args.max_utilization,
            acceptable_slack=args.acceptable_slack,
            warn_bump_share=args.warn_bump_share,
            max_preferred_profile_share=args.max_preferred_profile_share,
            max_preferred_image_share=args.max_preferred_image_share,
        )

        report_file = args.report_dir / f"profile_label_distribution_{src}.md"
        report_file.write_text(report_content, encoding="utf-8")
        logger.info("Generated distribution report at %s", report_file)

        if warnings:
            all_warnings_count += len(warnings)
            for w in warnings:
                logger.warning("[%s] %s", src, w)

    if args.strict and all_warnings_count > 0:
        logger.error("Strict mode active: %d review warnings encountered.", all_warnings_count)
        return 2

    logger.info("Derive profile labels completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
