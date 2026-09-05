"""Joint P2 recommendation evaluation across catalog scales for Protocol-v5 E5."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import logging
import math
from pathlib import Path
import statistics
import time
from typing import Any

from evaluation_v5.offline.recommenders import OfflineCaseInput
from evaluation_v5.offline.runner import _case_input
from evaluation_v5.split_dataset import LoadedSplit, load_development_split
from recommender.p2_backend import P2Config, P2Recommender

from .storage_contracts import CatalogImageEntry

logger = logging.getLogger(__name__)

DEFAULT_RECALL_K = 5


def evaluate_catalog_scale_recommendation(
    base_catalog: Mapping[str, Any],
    scale_images: Sequence[CatalogImageEntry],
    *,
    split_bundle: LoadedSplit | None = None,
    split_path: Path | str | None = None,
    k: int = DEFAULT_RECALL_K,
) -> dict[str, Any]:
    """Evaluate P2 recommendation quality, Recall@K, and latency on an approved catalog subset.

    Evaluates:
    1. image_acceptable_accuracy: Primary Protocol-v5 metric (selection in acceptable images for feasible cases).
    2. image_preferred_accuracy: Strict preferred image Top-1 accuracy for feasible cases.
    3. retrieval_recall_at_k: Macro Recall@K of acceptable candidates in fused retrieval top-K.
    4. recommendation_latency: Mean, median, p95, min, max, std of total elapsed latency.
    """
    if not scale_images:
        return {
            "status": "NOT_EXECUTED",
            "reason": "no_approved_images_provided",
            "image_acceptable_accuracy": None,
            "image_preferred_accuracy": None,
            "retrieval_recall_at_k": None,
            "recall_k": k,
            "latency": {},
            "evaluated_cases": 0,
            "feasible_cases": 0,
            "dataset_id": "none",
            "dataset_sha256": "0" * 64,
            "p2_config_version": "none",
        }

    # Load split bundle
    if split_bundle is None:
        try:
            split_bundle = load_development_split()
        except Exception as exc:
            logger.warning("Failed to load development split: %s", exc)
            return {
                "status": "NOT_EXECUTED",
                "reason": f"split_load_failed: {exc}",
                "image_acceptable_accuracy": None,
                "image_preferred_accuracy": None,
                "retrieval_recall_at_k": None,
                "recall_k": k,
                "latency": {},
                "evaluated_cases": 0,
                "feasible_cases": 0,
                "dataset_id": "none",
                "dataset_sha256": "0" * 64,
                "p2_config_version": "none",
            }

    dataset_id = split_bundle.bundle.split_manifest.dataset_id
    dataset_sha256 = split_bundle.source_file_sha256
    cases = split_bundle.bundle.cases

    # Construct scale-specific catalog containing only scale_images
    scale_image_ids = {img.image_id for img in scale_images}
    subset_images = {}
    for img in scale_images:
        if img.image_id in base_catalog.get("images", {}):
            subset_images[img.image_id] = dict(base_catalog["images"][img.image_id])
        else:
            # Build minimal entry for approved scale image
            subset_images[img.image_id] = {
                "reference": img.reference,
                "display_name": img.display_name or img.image_id,
                "description": img.description or f"Catalog image {img.image_id}",
                "capabilities": list(img.capabilities),
                "match_terms": list(img.match_terms),
                "priority": img.priority,
            }

    subset_catalog = {
        "catalog_version": str(base_catalog.get("catalog_version", "2026-08-06.1")),
        "default_image": scale_images[0].image_id,
        "images": subset_images,
    }

    try:
        p2 = P2Recommender(catalog=subset_catalog)
    except Exception as exc:
        logger.warning("Failed to initialize P2 for catalog scale: %s", exc)
        return {
            "status": "NOT_EXECUTED",
            "reason": f"p2_initialization_failed: {exc}",
            "image_acceptable_accuracy": None,
            "image_preferred_accuracy": None,
            "retrieval_recall_at_k": None,
            "recall_k": k,
            "latency": {},
            "evaluated_cases": 0,
            "feasible_cases": 0,
            "dataset_id": dataset_id,
            "dataset_sha256": dataset_sha256,
            "p2_config_version": "none",
        }

    acceptable_hits = 0
    preferred_hits = 0
    feasible_count = 0
    recall_list: list[float] = []
    latencies: list[float] = []

    for case in cases:
        case_input = _case_input(case)
        det = p2.recommend_detailed(case_input.request())
        rec = det.recommendation

        lat = (
            det.metadata.total_elapsed_seconds
            if (det.metadata and det.metadata.total_elapsed_seconds is not None)
            else None
        )
        if lat is not None and math.isfinite(lat) and lat >= 0:
            latencies.append(lat)

        gold = case.gold if isinstance(case.gold, Mapping) else {}
        feasible = bool(gold.get("request_feasible", True))

        if feasible:
            feasible_count += 1
            pred_image = rec.image_id

            pref_cand = gold.get("preferred_candidate_id")
            pref_image = (
                pref_cand.split("-", 1)[1]
                if pref_cand and "-" in pref_cand
                else pref_cand
            )

            acceptable_cands = set(gold.get("acceptable_candidate_ids", []))
            acceptable_images = {
                c.split("-", 1)[1] for c in acceptable_cands if "-" in c
            }

            if pred_image in acceptable_images:
                acceptable_hits += 1
            if pred_image == pref_image:
                preferred_hits += 1

            # Recall@K from fused retrieval
            fused = det.retrieval_result.fused_hits if det.retrieval_result else []
            top_k_cands = {h.candidate_id for h in fused[:k]}
            if acceptable_cands:
                rec_val = len(acceptable_cands & top_k_cands) / len(acceptable_cands)
                recall_list.append(rec_val)

    acc_acceptable = (acceptable_hits / feasible_count) if feasible_count > 0 else 0.0
    acc_preferred = (preferred_hits / feasible_count) if feasible_count > 0 else 0.0
    mean_recall = (sum(recall_list) / len(recall_list)) if recall_list else 0.0

    lat_sorted = sorted(latencies) if latencies else []
    latency_stats = {
        "mean_seconds": statistics.fmean(latencies) if latencies else None,
        "median_seconds": statistics.median(latencies) if latencies else None,
        "p95_seconds": (
            lat_sorted[int(math.ceil(0.95 * len(lat_sorted))) - 1]
            if lat_sorted
            else None
        ),
        "min_seconds": min(latencies) if latencies else None,
        "max_seconds": max(latencies) if latencies else None,
        "std_seconds": statistics.stdev(latencies) if len(latencies) > 1 else 0.0,
    }

    return {
        "status": "OBSERVED",
        "reason": "",
        "image_acceptable_accuracy": acc_acceptable,
        "image_preferred_accuracy": acc_preferred,
        "retrieval_recall_at_k": mean_recall,
        "recall_k": k,
        "latency": latency_stats,
        "evaluated_cases": len(cases),
        "feasible_cases": feasible_count,
        "dataset_id": dataset_id,
        "dataset_sha256": dataset_sha256,
        "p2_config_version": p2.config.config_version,
    }
