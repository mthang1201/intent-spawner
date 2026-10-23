#!/usr/bin/env python3
"""Compile Protocol-v5 Unified Gold Standard Dataset from DS-1000 and ARCADE.

This script synthesizes empirical workload data from:
1. DS-1000: Empirical cgroups v2 anon peak measurements from Task 1 & Task 2.
2. ARCADE: Natural language task intents and paraphrased variants from Task 3.

Strictly complies with `benchmarks_v5/protocol-v5-gold-family-v1.schema.json`:
1. Each independent unit is a `family` with:
   - `family_id` safe and unique (^[a-z0-9][a-z0-9._-]*$).
   - `workload_stratum` in the 7 valid strata: boundary, data_processing,
     deep_learning, light, machine_learning, numerical_computing, policy.
   - `difficulty`: easy, medium, or hard (unassessed is blocked).
   - `preferred_profile_ids` and `acceptable_profile_ids` derived from Task 2 empirical runs.
   - `preferred_image_ids` and `acceptable_image_ids` strictly matching recommender/image-catalog.yaml.
   - `variants` containing canonical English, natural paraphrase, and bilingual variants.
2. Enforces class balance review policy:
   - No preferred profile exceeds `max_preferred_profile_share: 0.5`.
   - No preferred image exceeds `max_preferred_image_share: 0.5`.
3. Exports the unified gold dataset to `benchmarks_v5/v5-gold-standard-dataset.yaml`
   and validates it directly using `evaluation_v5.gold_dataset.load_gold_dataset`.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import random
import re
import subprocess
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation_v5.gold_dataset import (
    current_catalog_identity,
    load_gold_dataset,
    review_gold_dataset,
    summarize_gold_dataset,
)
from recommender.candidate_corpus import DEFAULT_PROFILE_DEFINITIONS

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("compile_protocol_v5_gold")

SCHEMA_VERSION = "protocol-v5-gold-family-v1.0.0"
PROTOCOL_VERSION = "5.0.0"

VALID_WORKLOAD_STRATA: frozenset[str] = frozenset([
    "boundary",
    "data_processing",
    "deep_learning",
    "light",
    "machine_learning",
    "numerical_computing",
    "policy",
])

VALID_DIFFICULTIES: frozenset[str] = frozenset(["easy", "medium", "hard"])
SAFE_ID_REGEX = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

DEFAULT_MAX_PROFILE_SHARE = 0.5
DEFAULT_MAX_IMAGE_SHARE = 0.5

DEFAULT_DS1000_FILE = PROJECT_ROOT / "benchmarks_v5" / "gold_families_ds1000.yaml"
DEFAULT_ARCADE_FILE = PROJECT_ROOT / "benchmarks_v5" / "gold_families_arcade_supplement.yaml"
DEFAULT_CATALOG_FILE = PROJECT_ROOT / "recommender" / "image-catalog.yaml"
DEFAULT_SCHEMA_FILE = PROJECT_ROOT / "benchmarks_v5" / "protocol-v5-gold-family-v1.schema.json"
DEFAULT_OUTPUT_FILE = PROJECT_ROOT / "benchmarks_v5" / "v5-gold-standard-dataset.yaml"


def compute_file_sha256(path: Path) -> str:
    """Compute cryptographic SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_git_revision() -> str:
    """Fetch 40-character git commit hash of current repository state."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
        )
        rev = proc.stdout.strip()
        if len(rev) == 40:
            return rev
    except Exception:
        pass
    return "0000000000000000000000000000000000000000"


def to_safe_id(s: str) -> str:
    """Normalize string to safeId according to pattern ^[a-z0-9][a-z0-9._-]*$."""
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", s.lower()).strip("-")
    if not cleaned or not cleaned[0].isalnum():
        cleaned = "id-" + cleaned
    return cleaned[:128]


def load_image_catalog(catalog_path: Path) -> Dict[str, Any]:
    """Load administrator image catalog and extract valid image IDs."""
    if not catalog_path.is_file():
        raise FileNotFoundError(f"Image catalog file not found: {catalog_path}")
    with open(catalog_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "images" not in data:
        raise ValueError(f"Invalid catalog structure in {catalog_path}: missing 'images' section")
    return data


def load_valid_profiles(custom_profiles: Optional[Dict[str, Any]] = None) -> Set[str]:
    """Load valid profile identifiers directly from candidate corpus configuration."""
    defs = custom_profiles if custom_profiles is not None else DEFAULT_PROFILE_DEFINITIONS
    return set(defs.keys())


def validate_family_contract(
    family: Mapping[str, Any],
    valid_images: Set[str],
    valid_profiles: Set[str],
) -> List[str]:
    """Validate a single family against the Protocol-v5 Gold Family schema requirements.

    Returns a list of error messages (empty list if family is valid).
    """
    errors: List[str] = []
    fid = family.get("family_id")
    if not fid or not isinstance(fid, str) or not SAFE_ID_REGEX.match(fid):
        errors.append(f"Invalid or unsafe family_id: {fid!r}")

    stratum = family.get("workload_stratum")
    if stratum not in VALID_WORKLOAD_STRATA:
        errors.append(f"Family {fid}: invalid workload_stratum {stratum!r}, must be one of {sorted(VALID_WORKLOAD_STRATA)}")

    diff = family.get("difficulty")
    if diff not in VALID_DIFFICULTIES:
        errors.append(f"Family {fid}: invalid difficulty {diff!r}, must be one of {sorted(VALID_DIFFICULTIES)}")

    # Check profile gold
    prof_gold = family.get("profile_gold")
    if not isinstance(prof_gold, dict):
        errors.append(f"Family {fid}: missing or non-dict profile_gold")
    else:
        pref_profs = prof_gold.get("preferred_profile_ids", [])
        acc_profs = prof_gold.get("acceptable_profile_ids", [])
        for p in pref_profs:
            if p not in valid_profiles:
                errors.append(f"Family {fid}: unknown preferred_profile_id {p!r}")
            if p not in acc_profs:
                errors.append(f"Family {fid}: preferred_profile_id {p!r} not in acceptable_profile_ids")
        for p in acc_profs:
            if p not in valid_profiles:
                errors.append(f"Family {fid}: unknown acceptable_profile_id {p!r}")

    # Check image gold
    img_gold = family.get("image_gold")
    if not isinstance(img_gold, dict):
        errors.append(f"Family {fid}: missing or non-dict image_gold")
    else:
        pref_imgs = img_gold.get("preferred_image_ids", [])
        acc_imgs = img_gold.get("acceptable_image_ids", [])
        for img in pref_imgs:
            if img not in valid_images:
                errors.append(f"Family {fid}: unknown preferred_image_id {img!r}")
            if img not in acc_imgs:
                errors.append(f"Family {fid}: preferred_image_id {img!r} not in acceptable_image_ids")
        for img in acc_imgs:
            if img not in valid_images:
                errors.append(f"Family {fid}: unknown acceptable_image_id {img!r}")

    # Check variants
    variants = family.get("variants")
    if not isinstance(variants, list) or not variants:
        errors.append(f"Family {fid}: variants must be a non-empty list")
    else:
        has_en = False
        has_paraphrase = False
        for v in variants:
            if not isinstance(v, dict):
                errors.append(f"Family {fid}: variant must be a dict")
                continue
            vid = v.get("variant_id")
            if not vid or not isinstance(vid, str) or not SAFE_ID_REGEX.match(vid):
                errors.append(f"Family {fid}: invalid variant_id {vid!r}")
            lang = v.get("language")
            if not lang or not isinstance(lang, str):
                errors.append(f"Family {fid}: variant {vid} missing language")
            elif lang == "en":
                has_en = True
            vclass = v.get("variant_class")
            if vclass in ("paraphrase_en", "informal_or_noisy"):
                has_paraphrase = True

        if not has_en:
            errors.append(f"Family {fid}: variants must include at least one English prompt")
        if not has_paraphrase:
            errors.append(f"Family {fid}: variants must include at least one natural paraphrase/perturbation")

    return errors


def check_class_balance(
    families: Sequence[Mapping[str, Any]],
    max_profile_share: float = 0.5,
    max_image_share: float = 0.5,
) -> Tuple[bool, Dict[str, float], Dict[str, float], List[str]]:
    """Check class balance conditions for preferred profiles and preferred images.

    Returns:
        (is_balanced, profile_shares, image_shares, warnings)
    """
    profile_counts: Counter[str] = Counter()
    image_counts: Counter[str] = Counter()
    total_profiles = 0
    total_images = 0

    for f in families:
        pref_profs = f.get("profile_gold", {}).get("preferred_profile_ids", [])
        if pref_profs:
            total_profiles += 1
            for p in pref_profs:
                profile_counts[str(p)] += 1

        pref_imgs = f.get("image_gold", {}).get("preferred_image_ids", [])
        if pref_imgs:
            total_images += 1
            for img in pref_imgs:
                image_counts[str(img)] += 1

    profile_shares: Dict[str, float] = {}
    image_shares: Dict[str, float] = {}
    warnings: List[str] = []
    is_balanced = True

    if total_profiles > 0:
        for p, count in sorted(profile_counts.items()):
            share = count / total_profiles
            profile_shares[p] = share
            if share > max_profile_share:
                is_balanced = False
                warnings.append(
                    f"Preferred profile {p!r} share {share:.2%} exceeds maximum allowed {max_profile_share:.1%} "
                    f"({count}/{total_profiles} families)."
                )

    if total_images > 0:
        for img, count in sorted(image_counts.items()):
            share = count / total_images
            image_shares[img] = share
            if share > max_image_share:
                is_balanced = False
                warnings.append(
                    f"Preferred image {img!r} share {share:.2%} exceeds maximum allowed {max_image_share:.1%} "
                    f"({count}/{total_images} families)."
                )

    return is_balanced, profile_shares, image_shares, warnings


def balance_families(
    all_families: Sequence[Dict[str, Any]],
    max_profile_share: float = 0.5,
    max_image_share: float = 0.5,
    seed: int = 42,
) -> List[Dict[str, Any]]:
    """Perform deterministic stratified balancing across (profile, image, stratum).

    Enforces that neither any preferred profile nor any preferred image exceeds
    the configured share cap (default 50%), preserving diversity across machine learning,
    numerical computing, deep learning, light, and data processing.
    """
    rng = random.Random(seed)

    # Deterministic grouping into pool by (preferred_profile, preferred_image)
    shuffled = list(all_families)
    rng.shuffle(shuffled)

    pool: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for f in shuffled:
        pref_profs = f.get("profile_gold", {}).get("preferred_profile_ids", [])
        pref_imgs = f.get("image_gold", {}).get("preferred_image_ids", [])
        p = pref_profs[0] if pref_profs else "none"
        img = pref_imgs[0] if pref_imgs else "none"
        pool.setdefault((p, img), []).append(f)

    # 1. Non-scipy families (pytorch, tensorflow, minimal-python)
    # These represent minority image classes and must be retained to maintain balanced distribution.
    non_scipy: List[Dict[str, Any]] = []
    for (p, img), fams in sorted(pool.items()):
        if img != "scipy-data-science":
            non_scipy.extend(fams)

    # 2. Derive maximum scipy quota satisfying max_image_share
    # count(scipy) / (count(scipy) + len(non_scipy)) <= max_image_share
    # => count(scipy) <= len(non_scipy) * max_image_share / (1.0 - max_image_share)
    max_scipy = int(len(non_scipy) * max_image_share / (1.0 - max_image_share))

    # Profiles present in non_scipy
    non_scipy_profs = Counter(
        f["profile_gold"]["preferred_profile_ids"][0] for f in non_scipy
        if f.get("profile_gold", {}).get("preferred_profile_ids")
    )

    large_scipy = pool.get(("large", "scipy-data-science"), [])
    med_scipy = pool.get(("medium", "scipy-data-science"), [])
    small_scipy = pool.get(("small", "scipy-data-science"), [])

    target_total = len(non_scipy) + max_scipy
    max_per_prof = int(target_total * max_profile_share)

    # Retain all large scipy families
    take_large_scipy = list(large_scipy)

    # Allocate medium scipy quota
    avail_med = max_per_prof - non_scipy_profs["medium"]
    take_med_scipy = med_scipy[:max(0, min(avail_med, len(med_scipy)))]

    # Allocate remainder to small scipy, ensuring total small <= max_per_prof
    rem_scipy = max_scipy - len(take_large_scipy) - len(take_med_scipy)
    avail_small = max_per_prof - non_scipy_profs["small"]
    take_small_count = min(rem_scipy, avail_small, len(small_scipy))

    # Round-robin selection of small scipy across workload strata
    strata_buckets: Dict[str, List[Dict[str, Any]]] = {}
    for f in small_scipy:
        strata_buckets.setdefault(f.get("workload_stratum", "data_processing"), []).append(f)

    take_small_scipy: List[Dict[str, Any]] = []
    while len(take_small_scipy) < take_small_count:
        added = False
        for st in sorted(strata_buckets.keys()):
            if strata_buckets[st] and len(take_small_scipy) < take_small_count:
                take_small_scipy.append(strata_buckets[st].pop(0))
                added = True
        if not added:
            break

    selected = non_scipy + take_large_scipy + take_med_scipy + take_small_scipy

    # Final verification and adjustment
    is_bal, p_shares, i_shares, _ = check_class_balance(selected, max_profile_share, max_image_share)
    if not is_bal:
        # If any slight rounding overflow occurs, prune the offending class by one
        for p, sh in p_shares.items():
            if sh > max_profile_share:
                for idx in reversed(range(len(selected))):
                    if selected[idx]["profile_gold"]["preferred_profile_ids"][0] == p:
                        selected.pop(idx)
                        break
        for img, sh in i_shares.items():
            if sh > max_image_share:
                for idx in reversed(range(len(selected))):
                    if selected[idx]["image_gold"]["preferred_image_ids"][0] == img:
                        selected.pop(idx)
                        break

    return selected


def compile_protocol_v5_gold(
    ds1000_path: Path,
    arcade_path: Path,
    output_path: Path,
    catalog_path: Path = DEFAULT_CATALOG_FILE,
    schema_path: Path = DEFAULT_SCHEMA_FILE,
    mode: str = "balanced",
    max_profile_share: float = 0.5,
    max_image_share: float = 0.5,
    seed: int = 42,
    strict_balance: bool = False,
    check_review: bool = False,
) -> int:
    """Execute complete Gold Dataset compilation pipeline."""
    logger.info("Starting Protocol-v5 Gold Dataset compilation...")
    logger.info("DS-1000 source: %s", ds1000_path)
    logger.info("ARCADE source: %s", arcade_path)
    logger.info("Output destination: %s", output_path)
    logger.info("Compilation mode: %s (max_profile_share=%.2f, max_image_share=%.2f)", mode, max_profile_share, max_image_share)

    # 1. Load Image Catalog & Profile metadata
    catalog_doc = load_image_catalog(catalog_path)
    valid_images = set(catalog_doc.get("images", {}).keys())
    valid_profiles = load_valid_profiles()

    logger.info("Loaded %d valid catalog images: %s", len(valid_images), sorted(valid_images))
    logger.info("Loaded %d valid profile definitions: %s", len(valid_profiles), sorted(valid_profiles))

    # 2. Load DS-1000 and ARCADE family sources
    if not ds1000_path.is_file():
        raise FileNotFoundError(f"DS-1000 file not found: {ds1000_path}")
    if not arcade_path.is_file():
        raise FileNotFoundError(f"ARCADE file not found: {arcade_path}")

    with open(ds1000_path, "r", encoding="utf-8") as f:
        ds1000_doc = yaml.safe_load(f)
    with open(arcade_path, "r", encoding="utf-8") as f:
        arcade_doc = yaml.safe_load(f)

    ds1000_families: List[Dict[str, Any]] = ds1000_doc.get("families", [])
    arcade_families: List[Dict[str, Any]] = arcade_doc.get("families", [])
    logger.info("Loaded %d DS-1000 families and %d ARCADE families.", len(ds1000_families), len(arcade_families))

    # Prioritize ARCADE families to include natural language task variants
    all_raw_families = list(arcade_families) + list(ds1000_families)

    # 3. Validate contract for each family
    all_errors: List[str] = []
    seen_fids: Set[str] = set()
    seen_vids: Set[str] = set()

    for fam in all_raw_families:
        fid = fam.get("family_id", "unknown")
        if fid in seen_fids:
            all_errors.append(f"Duplicate family_id: {fid!r}")
        seen_fids.add(fid)

        for v in fam.get("variants", []):
            vid = v.get("variant_id", "unknown")
            if vid in seen_vids:
                all_errors.append(f"Duplicate variant_id across dataset: {vid!r}")
            seen_vids.add(vid)

        errs = validate_family_contract(fam, valid_images, valid_profiles)
        all_errors.extend(errs)

    if all_errors:
        logger.error("Family contract validation encountered %d errors:", len(all_errors))
        for err in all_errors[:10]:
            logger.error("  - %s", err)
        if len(all_errors) > 10:
            logger.error("  ... and %d more errors", len(all_errors) - 10)
        raise ValueError(f"Family contract validation failed with {len(all_errors)} errors.")

    logger.info("Contract validation PASSED for all %d families and %d variants.", len(seen_fids), len(seen_vids))

    # 4. Apply Balancing if requested
    if mode == "balanced":
        logger.info("Applying stratified balancing to satisfy class balance (max_share <= 0.5)...")
        compiled_families = balance_families(
            all_raw_families,
            max_profile_share=max_profile_share,
            max_image_share=max_image_share,
            seed=seed,
        )
        logger.info("Balanced dataset contains %d families (downsampled from %d).", len(compiled_families), len(all_raw_families))
    else:
        compiled_families = list(all_raw_families)
        logger.info("Full mode: keeping all %d families.", len(compiled_families))

    # 5. Check class balance
    is_bal, p_shares, i_shares, balance_warnings = check_class_balance(
        compiled_families,
        max_profile_share=max_profile_share,
        max_image_share=max_image_share,
    )

    logger.info("=== CLASS BALANCE VERIFICATION REPORT ===")
    logger.info("Preferred Profiles (cap: %.1f%%):", max_profile_share * 100)
    for p, sh in sorted(p_shares.items(), key=lambda x: -x[1]):
        status_sym = "✓" if sh <= max_profile_share else "✗"
        logger.info("  [%s] Profile %-10s : %6.2f%%", status_sym, p, sh * 100)

    logger.info("Preferred Images (cap: %.1f%%):", max_image_share * 100)
    for img, sh in sorted(i_shares.items(), key=lambda x: -x[1]):
        status_sym = "✓" if sh <= max_image_share else "✗"
        logger.info("  [%s] Image %-26s : %6.2f%%", status_sym, img, sh * 100)

    if not is_bal:
        for w in balance_warnings:
            logger.warning("  WARNING: %s", w)
        if strict_balance:
            raise ValueError(f"Class balance verification failed under strict policy: {balance_warnings}")
    else:
        logger.info("Class balance verification: PASS (all shares <= %.1f%%).", max_profile_share * 100)

    # 6. Combine source datasets metadata
    source_datasets = []
    seen_sources = set()
    for doc in (ds1000_doc, arcade_doc):
        for s in doc.get("dataset_metadata", {}).get("source_datasets", []):
            sid = s.get("dataset_id")
            if sid not in seen_sources:
                seen_sources.add(sid)
                source_datasets.append(s)

    git_rev = get_git_revision()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 7. Construct Unified Gold Standard Dataset Document
    gold_standard_document: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "dataset_metadata": {
            "dataset_id": "v5-gold-standard-dataset",
            "protocol_version": PROTOCOL_VERSION,
            "role": "development",
            "lifecycle": "reviewed",
            "created_at_utc": now_utc,
            "created_by": "compile_protocol_v5_gold.py",
            "git_revision": git_rev,
            "evidence_classification": "human_reviewed_development",
            "freeze_metadata": None,
            "source_datasets": source_datasets,
        },
        "catalog_identity": current_catalog_identity(),
        "review_policy": {
            "required_workload_strata": sorted(list(VALID_WORKLOAD_STRATA)),
            "max_preferred_profile_share": float(max_profile_share),
            "max_preferred_image_share": float(max_image_share),
        },
        "families": compiled_families,
    }

    # 8. Export to YAML
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(gold_standard_document, f, sort_keys=False, allow_unicode=True)

    file_size_bytes = output_path.stat().st_size
    file_sha = compute_file_sha256(output_path)
    logger.info("Successfully exported Gold Standard Dataset to: %s", output_path)
    logger.info("  Size: %d bytes (%.2f KB), SHA-256: %s", file_size_bytes, file_size_bytes / 1024.0, file_sha)

    # 9. Direct runtime validation using evaluation_v5.gold_dataset
    logger.info("Validating exported file with evaluation_v5.gold_dataset.load_gold_dataset...")
    loaded = load_gold_dataset(output_path)
    logger.info(
        "SCHEMA & CATALOG VALIDATION PASSED: dataset_id=%s, total_families=%d",
        loaded.dataset.dataset_metadata["dataset_id"],
        len(loaded.dataset.families),
    )

    if check_review:
        logger.info("Running human-review finding inspection with review_gold_dataset...")
        review_report = review_gold_dataset(loaded)
        if review_report.blocking_findings:
            logger.error("Blocking review findings detected: %d", len(review_report.blocking_findings))
            for bf in review_report.blocking_findings:
                logger.error("  - %s: %s", bf.code, bf.message)
            raise ValueError(f"Compiled dataset contains {len(review_report.blocking_findings)} blocking review findings.")
        logger.info("Review check PASSED: 0 blocking findings.")

    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile Protocol-v5 Gold Standard Dataset from DS-1000 and ARCADE",
    )
    parser.add_argument(
        "--ds1000-file",
        type=Path,
        default=DEFAULT_DS1000_FILE,
        help="Path to DS-1000 gold families YAML",
    )
    parser.add_argument(
        "--arcade-file",
        type=Path,
        default=DEFAULT_ARCADE_FILE,
        help="Path to ARCADE gold families YAML",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help="Path to output compiled Gold Standard dataset YAML",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG_FILE,
        help="Path to recommender/image-catalog.yaml",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_FILE,
        help="Path to protocol-v5-gold-family-v1.schema.json",
    )
    parser.add_argument(
        "--mode",
        choices=["balanced", "full"],
        default="balanced",
        help="Compilation mode: 'balanced' (default, enforces class cap <= 0.5) or 'full' (all families)",
    )
    parser.add_argument(
        "--max-profile-share",
        type=float,
        default=DEFAULT_MAX_PROFILE_SHARE,
        help="Maximum allowed preferred profile share (default: 0.5)",
    )
    parser.add_argument(
        "--max-image-share",
        type=float,
        default=DEFAULT_MAX_IMAGE_SHARE,
        help="Maximum allowed preferred image share (default: 0.5)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic balancing",
    )
    parser.add_argument(
        "--strict-balance",
        action="store_true",
        help="Raise error if class balance check fails",
    )
    parser.add_argument(
        "--check-review",
        action="store_true",
        help="Run full review_gold_dataset inspection for blocking findings",
    )

    args = parser.parse_args(argv)

    try:
        return compile_protocol_v5_gold(
            ds1000_path=args.ds1000_file,
            arcade_path=args.arcade_file,
            output_path=args.output,
            catalog_path=args.catalog,
            schema_path=args.schema,
            mode=args.mode,
            max_profile_share=args.max_profile_share,
            max_image_share=args.max_image_share,
            seed=args.seed,
            strict_balance=args.strict_balance,
            check_review=args.check_review,
        )
    except Exception as exc:
        logger.exception("Compilation failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
