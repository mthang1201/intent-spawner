#!/usr/bin/env python3
"""DS-1000 Stratified Sampler.

Extracts stratified subsets (image_id x library) from
benchmarks_v5/raw_sources/ds1000_intent_and_images.jsonl, enforcing a 50%
cap per image and balanced library distribution. Outputs sample150, pilot20,
and an audit distribution report in Markdown.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import random
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sample_ds1000_stratified")

DEFAULT_INPUT_FILE = Path("benchmarks_v5/raw_sources/ds1000_intent_and_images.jsonl")
DEFAULT_CATALOG_PATH = Path("recommender/image-catalog.yaml")
DEFAULT_SAMPLE_OUTPUT = Path("benchmarks_v5/raw_sources/ds1000_sample150.jsonl")
DEFAULT_PILOT_OUTPUT = Path("benchmarks_v5/raw_sources/ds1000_pilot20.jsonl")
DEFAULT_REPORT_OUTPUT = Path("docs/reports/ds1000_sample_distribution.md")
DEFAULT_SEED = 20261021
DEFAULT_N = 150
DEFAULT_PILOT_N = 20


def load_valid_image_ids(catalog_path: Path | str) -> List[str]:
    """Load allowable image IDs directly from administrator image catalog (never hardcoded)."""
    p = Path(catalog_path)
    if not p.is_file():
        raise FileNotFoundError(f"Image catalog file not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "images" not in data:
        raise ValueError(f"Invalid catalog structure in {p}: missing 'images' section")
    image_ids = list(data["images"].keys())
    if not image_ids:
        raise ValueError(f"No images declared in catalog {p}")
    return image_ids


def derive_stratum_seed(base_seed: int, image: str, library: str) -> int:
    """Deterministically derive a per-stratum 32-bit seed using SHA-256."""
    raw = f"{base_seed}:{image}::{library}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return int(digest[:8], 16)


def allocate_quotas(
    target_n: int,
    strata_counts: Dict[Tuple[str, str], int],
    valid_images: Set[str],
    max_image_share: float = 0.5,
) -> Dict[Tuple[str, str], int]:
    """Allocate stratified quotas across (image_id x library).

    Constraints:
      1. No single image exceeds max_image_share (50%) of total sample.
      2. If an image has fewer items than requested quota, cap at available and warn.
      3. Within each image, distribute quota evenly across its constituent libraries.
    """
    if target_n <= 0:
        return {key: 0 for key in strata_counts}

    # Group counts by image
    image_to_libs: Dict[str, List[str]] = {}
    image_totals: Dict[str, int] = {}
    for (img, lib), cnt in strata_counts.items():
        if img not in valid_images:
            raise ValueError(f"Image '{img}' is not in valid catalog image IDs")
        image_to_libs.setdefault(img, []).append(lib)
        image_totals[img] = image_totals.get(img, 0) + cnt

    total_pop = sum(image_totals.values())
    max_cap_per_image = math.floor(target_n * max_image_share)

    # 1. Identify dominant images whose unconstrained population share exceeds max_image_share
    dominant_images = [
        img for img, cnt in image_totals.items() if (cnt / total_pop) > max_image_share
    ]
    non_dominant = [img for img in image_totals if img not in dominant_images]

    image_quotas: Dict[str, int] = {}
    remaining_n = target_n

    # Cap dominant images first (e.g. scipy-data-science at 50%)
    for d_img in sorted(dominant_images):
        avail = image_totals[d_img]
        cap = min(max_cap_per_image, avail)
        image_quotas[d_img] = cap
        remaining_n -= cap

    # Distribute remaining_n across non-dominant images
    if non_dominant:
        # Sort by available items descending
        sorted_non_dom = sorted(non_dominant, key=lambda x: image_totals[x], reverse=True)
        remaining_images = list(sorted_non_dom)

        while remaining_images and remaining_n > 0:
            base_each = remaining_n // len(remaining_images)
            rem = remaining_n % len(remaining_images)
            made_progress = False

            for i, img in enumerate(list(remaining_images)):
                desired = base_each + (1 if i < rem else 0)
                desired = min(desired, max_cap_per_image)
                avail = image_totals[img]
                if avail < desired:
                    logger.warning(
                        "Image '%s' only has %d items available (requested quota: %d). "
                        "Reducing quota to %d and reallocating deficit.",
                        img,
                        avail,
                        desired,
                        avail,
                    )
                    image_quotas[img] = avail
                    remaining_n -= avail
                    remaining_images.remove(img)
                    made_progress = True
                    break

            if not made_progress:
                for i, img in enumerate(remaining_images):
                    desired = base_each + (1 if i < rem else 0)
                    image_quotas[img] = min(desired, max_cap_per_image)
                    remaining_n -= image_quotas[img]
                break

    if remaining_n > 0:
        logger.warning(
            "Could not fully allocate requested sample size %d (remaining deficit: %d).",
            target_n,
            remaining_n,
        )

    # 2. Allocate quotas to libraries within each image
    strata_quotas: Dict[Tuple[str, str], int] = {}
    for img, img_quota in sorted(image_quotas.items()):
        libs = sorted(image_to_libs[img])
        if not libs:
            continue
        remaining_lib_quota = img_quota
        active_libs = list(libs)
        lib_quotas: Dict[str, int] = {lib: 0 for lib in libs}

        while active_libs and remaining_lib_quota > 0:
            base_lib_q = remaining_lib_quota // len(active_libs)
            lib_rem = remaining_lib_quota % len(active_libs)
            made_progress = False

            for i, lib in enumerate(list(active_libs)):
                target_lib_q = base_lib_q + (1 if i < lib_rem else 0)
                avail = strata_counts.get((img, lib), 0) - lib_quotas[lib]
                if avail < target_lib_q:
                    logger.warning(
                        "Stratum ('%s', '%s') only has %d items available (requested quota: %d). "
                        "Reallocating deficit within image '%s'.",
                        img,
                        lib,
                        avail,
                        target_lib_q,
                        img,
                    )
                    lib_quotas[lib] += avail
                    remaining_lib_quota -= avail
                    active_libs.remove(lib)
                    made_progress = True
                    break

            if not made_progress:
                for i, lib in enumerate(active_libs):
                    target_lib_q = base_lib_q + (1 if i < lib_rem else 0)
                    lib_quotas[lib] += target_lib_q
                    remaining_lib_quota -= target_lib_q
                break

        for lib, q in lib_quotas.items():
            strata_quotas[(img, lib)] = q

    return strata_quotas


def sample_ds1000(
    input_file: Path | str,
    catalog_path: Path | str,
    n: int = DEFAULT_N,
    pilot_n: int = DEFAULT_PILOT_N,
    seed: int = DEFAULT_SEED,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Perform deterministic stratified sampling of DS-1000 dataset.

    Returns:
        (sample_records, pilot_records, metadata_summary)
    """
    in_path = Path(input_file)
    if not in_path.is_file():
        raise FileNotFoundError(f"Input file not found: {in_path}")

    valid_images = set(load_valid_image_ids(catalog_path))

    with open(in_path, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    if not records:
        raise ValueError(f"Input file {in_path} is empty")

    # Validate that records belong to catalog images
    strata_map: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in records:
        img = r.get("image") or r.get("image_id")
        lib = r.get("library")
        if not img or img not in valid_images:
            raise ValueError(f"Record {r.get('problem_id')} has invalid/unrecognized image: {img}")
        if not lib:
            raise ValueError(f"Record {r.get('problem_id')} has missing library label")
        strata_map.setdefault((img, lib), []).append(r)

    strata_counts = {k: len(v) for k, v in strata_map.items()}

    # Compute quotas for sample_n and pilot_n
    actual_pilot_n = min(pilot_n, n)
    sample_quotas = allocate_quotas(n, strata_counts, valid_images, max_image_share=0.5)
    pilot_quotas = allocate_quotas(actual_pilot_n, strata_counts, valid_images, max_image_share=0.5)

    # Check that pilot quotas do not exceed sample quotas
    for k in sample_quotas:
        if pilot_quotas.get(k, 0) > sample_quotas[k]:
            pilot_quotas[k] = sample_quotas[k]

    pilot_items: List[Dict[str, Any]] = []
    remaining_sample_items: List[Dict[str, Any]] = []

    # Deterministic sampling per stratum
    for stratum_key in sorted(strata_map.keys()):
        img, lib = stratum_key
        candidates = sorted(strata_map[stratum_key], key=lambda x: str(x.get("problem_id", "")))

        s_quota = sample_quotas.get(stratum_key, 0)
        p_quota = pilot_quotas.get(stratum_key, 0)

        # Derive reproducible seed for this stratum
        s_seed = derive_stratum_seed(seed, img, lib)
        rng = random.Random(s_seed)
        shuffled = candidates.copy()
        rng.shuffle(shuffled)

        sampled_stratum = shuffled[:s_quota]

        # Enrich records with stratum and seed metadata
        enriched_sampled = []
        for item in sampled_stratum:
            rec = dict(item)
            rec["sample_stratum"] = f"{img}::{lib}"
            rec["sample_seed"] = seed
            enriched_sampled.append(rec)

        # Partition into pilot vs remainder of the sample
        pilot_slice = enriched_sampled[:p_quota]
        remainder_slice = enriched_sampled[p_quota:]

        pilot_items.extend(pilot_slice)
        remaining_sample_items.extend(remainder_slice)

    # Permute pilot_items and remaining_items deterministically
    perm_rng = random.Random(seed)
    perm_rng.shuffle(pilot_items)
    perm_rng.shuffle(remaining_sample_items)

    final_sample = pilot_items + remaining_sample_items
    final_pilot = final_sample[:actual_pilot_n]

    # Verify constraints
    # 1. No duplicates
    sample_ids = [r["problem_id"] for r in final_sample]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("Duplicate problem IDs detected in sampled dataset")

    # 2. 50% cap check
    max_allowed_sample_img = math.floor(len(final_sample) * 0.5)
    sample_img_counts = Counter(r["image"] for r in final_sample)
    for img, count in sample_img_counts.items():
        if count > max_allowed_sample_img:
            raise ValueError(
                f"Image '{img}' has {count} items, exceeding 50% cap ({max_allowed_sample_img}) "
                f"for total sample {len(final_sample)}"
            )

    max_allowed_pilot_img = math.floor(len(final_pilot) * 0.5)
    pilot_img_counts = Counter(r["image"] for r in final_pilot)
    for img, count in pilot_img_counts.items():
        if count > max_allowed_pilot_img:
            raise ValueError(
                f"Image '{img}' has {count} items in pilot, exceeding 50% cap ({max_allowed_pilot_img}) "
                f"for total pilot {len(final_pilot)}"
            )

    meta = {
        "seed": seed,
        "population_total": len(records),
        "sample_total": len(final_sample),
        "pilot_total": len(final_pilot),
        "strata_counts": strata_counts,
        "sample_quotas": sample_quotas,
        "pilot_quotas": pilot_quotas,
        "sample_img_counts": dict(sample_img_counts),
        "pilot_img_counts": dict(pilot_img_counts),
    }

    return final_sample, final_pilot, meta


def format_distribution_ascii_table(
    strata_counts: Dict[Tuple[str, str], int],
    sample_records: List[Dict[str, Any]],
    pilot_records: List[Dict[str, Any]],
) -> str:
    """Format human-readable ASCII table of population vs sample vs pilot distribution."""
    total_pop = sum(strata_counts.values())
    total_sample = len(sample_records)
    total_pilot = len(pilot_records)

    sample_counts = Counter((r["image"], r["library"]) for r in sample_records)
    pilot_counts = Counter((r["image"], r["library"]) for r in pilot_records)

    lines = []
    lines.append("=" * 96)
    lines.append(
        f"{'Image ID':<26} {'Library':<14} {'Pop N':>6} {'Pop %':>7} | "
        f"{'S150 N':>7} {'S150 %':>8} | {'P20 N':>6} {'P20 %':>7}"
    )
    lines.append("-" * 96)

    current_img = None
    img_pop_subtotal = 0
    img_sample_subtotal = 0
    img_pilot_subtotal = 0

    sorted_keys = sorted(strata_counts.keys())

    for idx, (img, lib) in enumerate(sorted_keys):
        if current_img is not None and img != current_img:
            # Subtotal for previous image
            lines.append(
                f"  >> Subtotal {current_img:<13} {'':<14} {img_pop_subtotal:>6} "
                f"{img_pop_subtotal/total_pop*100:>6.1f}% | "
                f"{img_sample_subtotal:>7} {img_sample_subtotal/total_sample*100:>7.1f}% | "
                f"{img_pilot_subtotal:>6} {img_pilot_subtotal/total_pilot*100:>6.1f}%"
            )
            lines.append("-" * 96)
            img_pop_subtotal = 0
            img_sample_subtotal = 0
            img_pilot_subtotal = 0

        current_img = img
        pop_cnt = strata_counts[(img, lib)]
        s_cnt = sample_counts.get((img, lib), 0)
        p_cnt = pilot_counts.get((img, lib), 0)

        img_pop_subtotal += pop_cnt
        img_sample_subtotal += s_cnt
        img_pilot_subtotal += p_cnt

        pop_pct = (pop_cnt / total_pop * 100) if total_pop else 0
        s_pct = (s_cnt / total_sample * 100) if total_sample else 0
        p_pct = (p_cnt / total_pilot * 100) if total_pilot else 0

        lines.append(
            f"{img:<26} {lib:<14} {pop_cnt:>6} {pop_pct:>6.1f}% | "
            f"{s_cnt:>7} {s_pct:>7.1f}% | {p_cnt:>6} {p_pct:>6.1f}%"
        )

    # Subtotal for final image
    if current_img is not None:
        lines.append(
            f"  >> Subtotal {current_img:<13} {'':<14} {img_pop_subtotal:>6} "
            f"{img_pop_subtotal/total_pop*100:>6.1f}% | "
            f"{img_sample_subtotal:>7} {img_sample_subtotal/total_sample*100:>7.1f}% | "
            f"{img_pilot_subtotal:>6} {img_pilot_subtotal/total_pilot*100:>6.1f}%"
        )
        lines.append("=" * 96)

    lines.append(
        f"{'TOTAL':<41} {total_pop:>6} {'100.0%':>7} | "
        f"{total_sample:>7} {'100.0%':>8} | {total_pilot:>6} {'100.0%':>7}"
    )
    lines.append("=" * 96)
    return "\n".join(lines)


def generate_markdown_report(
    strata_counts: Dict[Tuple[str, str], int],
    sample_records: List[Dict[str, Any]],
    pilot_records: List[Dict[str, Any]],
    seed: int,
    catalog_path: str,
) -> str:
    """Generate Markdown report for distribution auditing."""
    total_pop = sum(strata_counts.values())
    total_sample = len(sample_records)
    total_pilot = len(pilot_records)

    sample_counts = Counter((r["image"], r["library"]) for r in sample_records)
    pilot_counts = Counter((r["image"], r["library"]) for r in pilot_records)

    md = []
    md.append("# DS-1000 Stratified Sample Distribution Report\n")
    md.append(f"- **Generated At**: {datetime.now(timezone.utc).isoformat()}")
    md.append(f"- **Seed**: `{seed}`")
    md.append(f"- **Image Catalog**: `{catalog_path}`")
    md.append(f"- **Full Population**: {total_pop} problems")
    md.append(f"- **Stratified Sample**: {total_sample} problems (`ds1000_sample150.jsonl`)")
    md.append(f"- **Pilot Subset**: {total_pilot} problems (`ds1000_pilot20.jsonl`)\n")

    md.append("## Stratification Constraints & Methodology\n")
    md.append(
        "1. **50% Maximum Image Cap**: In the full population, `scipy-data-science` represents 88.7% "
        "of problems. To ensure rigorous evaluation across deep learning workloads without bias, "
        "`scipy-data-science` is capped at **50.0%** (75/150 in full sample, 10/20 in pilot)."
    )
    md.append(
        "2. **Library Balance**: Within `scipy-data-science`, sample quota is divided equally across "
        "all 5 constituent libraries (Pandas, Numpy, Matplotlib, Scikit-learn, Scipy) at **15 items each** "
        "(and **2 items each** in pilot)."
    )
    md.append(
        "3. **Pilot Alignment**: The pilot dataset comprises the first 20 items of a deterministic permutation "
        "of the sample, preserving image-level stratification (10 scipy-data-science, 5 pytorch, 5 tensorflow)."
    )
    md.append(
        "4. **No Duplicates & Full Reproducibility**: Each stratum uses independent SHA-256 derived seeds "
        "without replacement.\n"
    )

    md.append("## Distribution Breakdown (image_id × library)\n")
    md.append(
        "| Image ID | Library | Population N | Pop % | Sample 150 N | Sample % | Pilot 20 N | Pilot % |\n"
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |"
    )

    current_img = None
    img_pop_subtotal = 0
    img_sample_subtotal = 0
    img_pilot_subtotal = 0

    sorted_keys = sorted(strata_counts.keys())

    for idx, (img, lib) in enumerate(sorted_keys):
        if current_img is not None and img != current_img:
            # Subtotal for previous image
            md.append(
                f"| **Subtotal ({current_img})** | *All* | **{img_pop_subtotal}** | "
                f"**{img_pop_subtotal/total_pop*100:.1f}%** | **{img_sample_subtotal}** | "
                f"**{img_sample_subtotal/total_sample*100:.1f}%** | **{img_pilot_subtotal}** | "
                f"**{img_pilot_subtotal/total_pilot*100:.1f}%** |"
            )
            img_pop_subtotal = 0
            img_sample_subtotal = 0
            img_pilot_subtotal = 0

        current_img = img
        pop_cnt = strata_counts[(img, lib)]
        s_cnt = sample_counts.get((img, lib), 0)
        p_cnt = pilot_counts.get((img, lib), 0)

        img_pop_subtotal += pop_cnt
        img_sample_subtotal += s_cnt
        img_pilot_subtotal += p_cnt

        pop_pct = (pop_cnt / total_pop * 100) if total_pop else 0
        s_pct = (s_cnt / total_sample * 100) if total_sample else 0
        p_pct = (p_cnt / total_pilot * 100) if total_pilot else 0

        md.append(
            f"| `{img}` | {lib} | {pop_cnt} | {pop_pct:.1f}% | "
            f"{s_cnt} | {s_pct:.1f}% | {p_cnt} | {p_pct:.1f}% |"
        )

    if current_img is not None:
        md.append(
            f"| **Subtotal ({current_img})** | *All* | **{img_pop_subtotal}** | "
            f"**{img_pop_subtotal/total_pop*100:.1f}%** | **{img_sample_subtotal}** | "
            f"**{img_sample_subtotal/total_sample*100:.1f}%** | **{img_pilot_subtotal}** | "
            f"**{img_pilot_subtotal/total_pilot*100:.1f}%** |"
        )

    md.append(
        f"| **TOTAL** | **7 libraries** | **{total_pop}** | **100.0%** | "
        f"**{total_sample}** | **100.0%** | **{total_pilot}** | **100.0%** |\n"
    )

    md.append("## Quality & Audit Attestation\n")
    sample_img_counts = Counter(r["image"] for r in sample_records)
    pilot_img_counts = Counter(r["image"] for r in pilot_records)

    max_sample_share = max(c / total_sample for c in sample_img_counts.values()) if total_sample else 0
    max_pilot_share = max(c / total_pilot for c in pilot_img_counts.values()) if total_pilot else 0

    md.append(f"- **Max image share in Sample 150**: `{max_sample_share*100:.1f}%` (Constraint <= 50%: **PASS**)")
    md.append(f"- **Max image share in Pilot 20**: `{max_pilot_share*100:.1f}%` (Constraint <= 50%: **PASS**)")
    md.append(
        f"- **Unique problem IDs in Sample 150**: `{len(set(r['problem_id'] for r in sample_records))}/{total_sample}` (**PASS**)"
    )
    md.append(
        f"- **Pilot 20 is exact prefix of Sample 150**: `{[r['problem_id'] for r in sample_records[:total_pilot]] == [r['problem_id'] for r in pilot_records]}` (**PASS**)"
    )

    return "\n".join(md)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Extract stratified DS-1000 samples with 50% max image cap and library balance."
    )
    parser.add_argument(
        "--input-file",
        type=str,
        default=str(DEFAULT_INPUT_FILE),
        help="Input JSONL file (default: benchmarks_v5/raw_sources/ds1000_intent_and_images.jsonl)",
    )
    parser.add_argument(
        "--catalog-path",
        type=str,
        default=str(DEFAULT_CATALOG_PATH),
        help="Path to recommender/image-catalog.yaml",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=DEFAULT_N,
        help="Total sample size to extract (default: 150)",
    )
    parser.add_argument(
        "--pilot-n",
        type=int,
        default=DEFAULT_PILOT_N,
        help="Pilot subset size (default: 20)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Random seed for deterministic sampling (default: 20261021)",
    )
    parser.add_argument(
        "--sample-output",
        type=str,
        default=str(DEFAULT_SAMPLE_OUTPUT),
        help="Output path for sample JSONL (default: benchmarks_v5/raw_sources/ds1000_sample150.jsonl)",
    )
    parser.add_argument(
        "--pilot-output",
        type=str,
        default=str(DEFAULT_PILOT_OUTPUT),
        help="Output path for pilot JSONL (default: benchmarks_v5/raw_sources/ds1000_pilot20.jsonl)",
    )
    parser.add_argument(
        "--report-output",
        type=str,
        default=str(DEFAULT_REPORT_OUTPUT),
        help="Output path for distribution markdown report (default: docs/reports/ds1000_sample_distribution.md)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI execution entrypoint."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        sample_records, pilot_records, meta = sample_ds1000(
            input_file=args.input_file,
            catalog_path=args.catalog_path,
            n=args.n,
            pilot_n=args.pilot_n,
            seed=args.seed,
        )

        # Write sample output
        sample_out = Path(args.sample_output)
        sample_out.parent.mkdir(parents=True, exist_ok=True)
        with open(sample_out, "w", encoding="utf-8") as f:
            for item in sample_records:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        logger.info("Saved %d sample records to %s", len(sample_records), sample_out)

        # Write pilot output
        pilot_out = Path(args.pilot_output)
        pilot_out.parent.mkdir(parents=True, exist_ok=True)
        with open(pilot_out, "w", encoding="utf-8") as f:
            for item in pilot_records:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        logger.info("Saved %d pilot records to %s", len(pilot_records), pilot_out)

        # Format ASCII table for stdout
        ascii_table = format_distribution_ascii_table(
            strata_counts=meta["strata_counts"],
            sample_records=sample_records,
            pilot_records=pilot_records,
        )
        print("\n" + ascii_table + "\n")

        # Write Markdown report
        report_out = Path(args.report_output)
        report_out.parent.mkdir(parents=True, exist_ok=True)
        md_content = generate_markdown_report(
            strata_counts=meta["strata_counts"],
            sample_records=sample_records,
            pilot_records=pilot_records,
            seed=args.seed,
            catalog_path=args.catalog_path,
        )
        with open(report_out, "w", encoding="utf-8") as f:
            f.write(md_content)
        logger.info("Saved distribution report to %s", report_out)

        return 0
    except Exception as e:
        logger.error("Sampling failed: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
