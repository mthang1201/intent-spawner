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
    stage: str = "confirmatory",
    dataset_path: Path | str | None = None,
    freeze_path: Path | str | None = None,
    split_bundle: LoadedSplit | None = None,
    k: int = DEFAULT_RECALL_K,
) -> dict[str, Any]:
    """Evaluate P2 recommendation quality, Recall@K, and latency on an approved catalog subset.

    Evaluates:
    1. image_acceptable_accuracy: Primary Protocol-v5 metric (selection in acceptable images for feasible cases).
    2. image_preferred_accuracy: Strict preferred image Top-1 accuracy for feasible cases.
    3. retrieval_recall_at_k: Macro Recall@K of acceptable candidates in fused retrieval top-K.
    4. recommendation_latency: Mean, median, p95, min, max, std of total elapsed latency.

    Strict Stage Isolation Rules:
    - If stage == 'development': loads the frozen development benchmark v5-development.yaml.
    - If stage == 'confirmatory': requires an external sealed confirmatory split. If unavailable,
      marks recommendation evaluation NOT_EXECUTED fail-closed. Under NO circumstances falls
      back to development data.
    """
    if not scale_images:
        return {
            "status": "NOT_EXECUTED",
            "reason": "no_approved_images_provided",
            "stage": stage,
            "split_role": "none",
            "image_acceptable_accuracy": None,
            "image_preferred_accuracy": None,
            "retrieval_recall_at_k": None,
            "recall_k": k,
            "latency": {},
            "evaluated_cases": 0,
            "feasible_cases": 0,
            "dataset_id": "none",
            "dataset_path": "",
            "dataset_sha256": "0" * 64,
            "p2_config_version": "none",
            "p2_version": "p2-hybrid-v1.0.0",
        }

    dataset_path_str = str(dataset_path) if dataset_path else ""

    # Check explicit split bundle passed in
    if split_bundle is not None:
        bundle_role = (
            split_bundle.bundle.split_manifest.role.value
            if hasattr(split_bundle.bundle.split_manifest.role, "value")
            else str(split_bundle.bundle.split_manifest.role)
        )
        if stage == "confirmatory" and bundle_role != "confirmatory":
            logger.warning(
                "Confirmatory stage received non-confirmatory split bundle with role: %s",
                bundle_role,
            )
            return {
                "status": "NOT_EXECUTED",
                "reason": f"confirmatory_stage_received_non_confirmatory_bundle: role={bundle_role}",
                "stage": stage,
                "split_role": bundle_role,
                "image_acceptable_accuracy": None,
                "image_preferred_accuracy": None,
                "retrieval_recall_at_k": None,
                "recall_k": k,
                "latency": {},
                "evaluated_cases": 0,
                "feasible_cases": 0,
                "dataset_id": split_bundle.bundle.split_manifest.dataset_id,
                "dataset_path": dataset_path_str,
                "dataset_sha256": split_bundle.source_file_sha256,
                "p2_config_version": "none",
                "p2_version": "p2-hybrid-v1.0.0",
            }
        split_role = bundle_role
    else:
        # Load stage-appropriate split bundle
        if stage == "development":
            try:
                split_bundle = load_development_split()
                split_role = "development"
                dataset_path_str = dataset_path_str or "benchmarks_v5/v5-development.yaml"
            except Exception as exc:
                logger.warning("Failed to load development split: %s", exc)
                return {
                    "status": "NOT_EXECUTED",
                    "reason": f"development_split_load_failed: {exc}",
                    "stage": stage,
                    "split_role": "development",
                    "image_acceptable_accuracy": None,
                    "image_preferred_accuracy": None,
                    "retrieval_recall_at_k": None,
                    "recall_k": k,
                    "latency": {},
                    "evaluated_cases": 0,
                    "feasible_cases": 0,
                    "dataset_id": "none",
                    "dataset_path": dataset_path_str,
                    "dataset_sha256": "0" * 64,
                    "p2_config_version": "none",
                    "p2_version": "p2-hybrid-v1.0.0",
                }
        elif stage == "confirmatory":
            # For confirmatory evaluation, MUST load an external confirmatory split
            import os
            from evaluation_v5.isolation import (
                CONFIRMATORY_DATASET_ENV_VAR,
                load_confirmatory_split,
                resolve_confirmatory_sources,
            )

            has_dataset_source = (
                dataset_path is not None or CONFIRMATORY_DATASET_ENV_VAR in os.environ
            )
            if not has_dataset_source:
                # Do NOT fall back silently to development data!
                return {
                    "status": "NOT_EXECUTED",
                    "reason": "confirmatory_dataset_not_provided: sealed confirmatory split required for confirmatory stage",
                    "stage": "confirmatory",
                    "split_role": "none",
                    "image_acceptable_accuracy": None,
                    "image_preferred_accuracy": None,
                    "retrieval_recall_at_k": None,
                    "recall_k": k,
                    "latency": {},
                    "evaluated_cases": 0,
                    "feasible_cases": 0,
                    "dataset_id": "none",
                    "dataset_path": "",
                    "dataset_sha256": "0" * 64,
                    "p2_config_version": "none",
                    "p2_version": "p2-hybrid-v1.0.0",
                }

            try:
                ds_p = Path(dataset_path) if dataset_path else None
                fr_p = Path(freeze_path) if freeze_path else None
                ds_src, fr_src = resolve_confirmatory_sources(
                    dataset_path=ds_p, freeze_path=fr_p
                )
                loaded_conf = load_confirmatory_split(ds_src, fr_src)
                split_bundle = loaded_conf.split
                split_role = "confirmatory"
                dataset_path_str = str(ds_src)
            except Exception as exc:
                logger.warning("Failed to load confirmatory split: %s", exc)
                return {
                    "status": "NOT_EXECUTED",
                    "reason": f"confirmatory_split_load_failed: {exc}",
                    "stage": "confirmatory",
                    "split_role": "confirmatory",
                    "image_acceptable_accuracy": None,
                    "image_preferred_accuracy": None,
                    "retrieval_recall_at_k": None,
                    "recall_k": k,
                    "latency": {},
                    "evaluated_cases": 0,
                    "feasible_cases": 0,
                    "dataset_id": "none",
                    "dataset_path": "",
                    "dataset_sha256": "0" * 64,
                    "p2_config_version": "none",
                    "p2_version": "p2-hybrid-v1.0.0",
                }
        else:
            raise ValueError(f"Unknown split stage: {stage!r}")

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
        "stage": stage,
        "split_role": split_role,
        "image_acceptable_accuracy": acc_acceptable,
        "image_preferred_accuracy": acc_preferred,
        "retrieval_recall_at_k": mean_recall,
        "recall_k": k,
        "latency": latency_stats,
        "evaluated_cases": len(cases),
        "feasible_cases": feasible_count,
        "dataset_id": dataset_id,
        "dataset_path": dataset_path_str,
        "dataset_sha256": dataset_sha256,
        "p2_config_version": p2.config.config_version,
        "p2_version": "p2-hybrid-v1.0.0",
    }
