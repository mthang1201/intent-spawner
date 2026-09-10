"""Joint P2 recommendation evaluation across catalog scales for Protocol-v5 E5."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import hashlib
import json
import logging
import math
from pathlib import Path
import statistics
from typing import Any

from evaluation_v5.offline.runner import _case_input
from evaluation_v5.analysis.statistics import (
    DEFAULT_BOOTSTRAP_SEED,
    derive_bootstrap_seed,
    family_bootstrap_ci,
    inference_eligibility,
)
from evaluation_v5.isolation import (
    VerifiedConfirmatorySplit,
    verify_confirmatory_split,
)
from evaluation_v5.split_dataset import (
    SPLIT_BUNDLE_SCHEMA_VERSION,
    SPLIT_BUNDLE_SCHEMA_VERSION_V2,
    LoadedSplit,
    load_development_split,
)
from recommender.p2_backend import P2Recommender

from .storage_contracts import CatalogImageEntry

logger = logging.getLogger(__name__)

DEFAULT_RECALL_K = 5
SCALE_RECOMMENDATION_RECORD_SCHEMA_VERSION = (
    "protocol-v5-catalog-scale-recommendation-record-v1.0.0"
)
SCALE_FAMILY_ESTIMATE_SCHEMA_VERSION = (
    "protocol-v5-catalog-scale-family-estimate-v1.0.0"
)


class CatalogScaleGoldError(ValueError):
    """Raised when a scale evaluator receives incomplete or obsolete gold fields."""


def _mapping_field(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CatalogScaleGoldError(f"{label} must be an object")
    return dict(value)


def _id_list(value: object, *, label: str, allow_empty: bool) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes, Mapping))
        or not isinstance(value, Sequence)
        or (not allow_empty and not value)
    ):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise CatalogScaleGoldError(f"{label} must be {qualifier}")
    selected = tuple(str(item) for item in value)
    if any(not item for item in selected) or len(selected) != len(set(selected)):
        raise CatalogScaleGoldError(f"{label} must contain unique non-blank IDs")
    return selected


def _candidate_image_id(candidate_id: str, p2: P2Recommender) -> str:
    document = p2.corpus.get(candidate_id)
    if document is not None:
        return document.image_id
    for profile_id in ("small", "medium", "large"):
        prefix = f"{profile_id}-"
        if candidate_id.startswith(prefix):
            return candidate_id.removeprefix(prefix)
    raise CatalogScaleGoldError(
        f"gold candidate {candidate_id!r} cannot be mapped to an image"
    )


def _canonical_gold(
    case: Any,
    *,
    schema_version: str,
    p2: P2Recommender,
) -> dict[str, Any]:
    """Return a validated v1 compatibility or canonical-v2 scoring view."""

    gold = _mapping_field(case.gold, label=f"case {case.case_id}.gold")
    if schema_version == SPLIT_BUNDLE_SCHEMA_VERSION_V2:
        required = {
            "gold_structured_intent",
            "candidate_gold",
            "profile_gold",
            "image_gold",
            "policy_gold",
        }
        missing = sorted(required - set(gold))
        if missing:
            raise CatalogScaleGoldError(
                f"case {case.case_id} canonical-v2 gold missing fields: {', '.join(missing)}"
            )
        candidate_gold = _mapping_field(
            gold["candidate_gold"], label=f"case {case.case_id}.candidate_gold"
        )
        _mapping_field(
            gold["gold_structured_intent"],
            label=f"case {case.case_id}.gold_structured_intent",
        )
        profile_gold = _mapping_field(
            gold["profile_gold"], label=f"case {case.case_id}.profile_gold"
        )
        image_gold = _mapping_field(
            gold["image_gold"], label=f"case {case.case_id}.image_gold"
        )
        policy_gold = _mapping_field(
            gold["policy_gold"], label=f"case {case.case_id}.policy_gold"
        )
        try:
            expected_feasibility = str(policy_gold["expected_feasibility"])
            acceptable_candidates = _id_list(
                candidate_gold["acceptable_candidate_ids"],
                label=f"case {case.case_id}.candidate_gold.acceptable_candidate_ids",
                allow_empty=True,
            )
            preferred_candidates = _id_list(
                candidate_gold["preferred_candidate_ids"],
                label=f"case {case.case_id}.candidate_gold.preferred_candidate_ids",
                allow_empty=True,
            )
            acceptable_profiles = _id_list(
                profile_gold["acceptable_profile_ids"],
                label=f"case {case.case_id}.profile_gold.acceptable_profile_ids",
                allow_empty=True,
            )
            preferred_profiles = _id_list(
                profile_gold["preferred_profile_ids"],
                label=f"case {case.case_id}.profile_gold.preferred_profile_ids",
                allow_empty=True,
            )
            acceptable_images = _id_list(
                image_gold["acceptable_image_ids"],
                label=f"case {case.case_id}.image_gold.acceptable_image_ids",
                allow_empty=True,
            )
            preferred_images = _id_list(
                image_gold["preferred_image_ids"],
                label=f"case {case.case_id}.image_gold.preferred_image_ids",
                allow_empty=True,
            )
            _id_list(
                image_gold["required_capabilities"],
                label=f"case {case.case_id}.image_gold.required_capabilities",
                allow_empty=True,
            )
        except KeyError as exc:
            raise CatalogScaleGoldError(
                f"case {case.case_id} canonical-v2 gold missing field {exc.args[0]!r}"
            ) from exc
        if expected_feasibility not in {"feasible", "infeasible", "ambiguous"}:
            raise CatalogScaleGoldError(
                f"case {case.case_id}.policy_gold.expected_feasibility is invalid"
            )
        if not set(preferred_candidates).issubset(acceptable_candidates):
            raise CatalogScaleGoldError(
                f"case {case.case_id} preferred candidates are not acceptable"
            )
        if not set(preferred_profiles).issubset(acceptable_profiles):
            raise CatalogScaleGoldError(
                f"case {case.case_id} preferred profiles are not acceptable"
            )
        if not set(preferred_images).issubset(acceptable_images):
            raise CatalogScaleGoldError(
                f"case {case.case_id} preferred images are not acceptable"
            )
        if expected_feasibility == "feasible" and (
            not acceptable_candidates
            or not preferred_candidates
            or not acceptable_profiles
            or not preferred_profiles
            or not acceptable_images
            or not preferred_images
        ):
            raise CatalogScaleGoldError(
                f"case {case.case_id} feasible canonical-v2 gold requires non-empty candidate and image labels"
            )
        return {
            "gold_schema": "canonical_v2",
            "expected_feasibility": expected_feasibility,
            "feasible": expected_feasibility == "feasible",
            "acceptable_candidate_ids": acceptable_candidates,
            "preferred_candidate_ids": preferred_candidates,
            "acceptable_profile_ids": acceptable_profiles,
            "preferred_profile_ids": preferred_profiles,
            "acceptable_image_ids": acceptable_images,
            "preferred_image_ids": preferred_images,
        }

    if schema_version != SPLIT_BUNDLE_SCHEMA_VERSION:
        raise CatalogScaleGoldError(
            f"case {case.case_id} uses unsupported split schema {schema_version!r}"
        )

    required_v1 = {
        "request_feasible",
        "preferred_candidate_id",
        "acceptable_candidate_ids",
    }
    missing_v1 = sorted(required_v1 - set(gold))
    if missing_v1:
        raise CatalogScaleGoldError(
            f"case {case.case_id} legacy-v1 gold missing fields: {', '.join(missing_v1)}"
        )
    feasible = gold["request_feasible"]
    if not isinstance(feasible, bool):
        raise CatalogScaleGoldError(
            f"case {case.case_id}.gold.request_feasible must be boolean"
        )
    acceptable_candidates = _id_list(
        gold["acceptable_candidate_ids"],
        label=f"case {case.case_id}.gold.acceptable_candidate_ids",
        allow_empty=not feasible,
    )
    preferred_candidate = gold["preferred_candidate_id"]
    if preferred_candidate is not None:
        preferred_candidate = str(preferred_candidate)
    if feasible and (
        not preferred_candidate or preferred_candidate not in acceptable_candidates
    ):
        raise CatalogScaleGoldError(
            f"case {case.case_id} feasible legacy gold requires a preferred acceptable candidate"
        )
    preferred_candidates = (
        (preferred_candidate,) if preferred_candidate is not None else ()
    )
    acceptable_images = tuple(
        sorted({_candidate_image_id(item, p2) for item in acceptable_candidates})
    )
    preferred_images = tuple(
        sorted({_candidate_image_id(item, p2) for item in preferred_candidates})
    )
    acceptable_profiles = tuple(
        sorted({item.split("-", 1)[0] for item in acceptable_candidates})
    )
    preferred_profiles = tuple(
        sorted({item.split("-", 1)[0] for item in preferred_candidates})
    )
    return {
        "gold_schema": "legacy_v1_compatibility",
        "expected_feasibility": "feasible" if feasible else "infeasible",
        "feasible": feasible,
        "acceptable_candidate_ids": acceptable_candidates,
        "preferred_candidate_ids": preferred_candidates,
        "acceptable_profile_ids": acceptable_profiles,
        "preferred_profile_ids": preferred_profiles,
        "acceptable_image_ids": acceptable_images,
        "preferred_image_ids": preferred_images,
    }


def _family_aggregation(
    records: Sequence[Mapping[str, Any]],
    *,
    dataset_sha256: str,
    catalog_size: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Aggregate variants within families, then families with v5 bootstrap primitives."""

    metric_fields = (
        "image_acceptable",
        "image_preferred",
        "retrieval_recall_at_k",
        "latency_seconds",
    )
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record["family_id"])].append(record)

    families: list[dict[str, Any]] = []
    for family_id, family_records in sorted(grouped.items()):
        values: dict[str, float | None] = {}
        denominators: dict[str, int] = {}
        for field in metric_fields:
            selected = [
                float(item[field])
                for item in family_records
                if item.get(field) is not None
            ]
            values[field] = statistics.fmean(selected) if selected else None
            denominators[field] = len(selected)
        families.append(
            {
                "schema_version": SCALE_FAMILY_ESTIMATE_SCHEMA_VERSION,
                "family_id": family_id,
                "catalog_size": catalog_size,
                "case_ids": sorted(str(item["case_id"]) for item in family_records),
                "variant_count": len(family_records),
                "values": values,
                "endpoint_variant_denominators": denominators,
                "aggregation_unit": "workload_family",
            }
        )

    metrics: dict[str, Any] = {}
    for field in metric_fields:
        rows = [
            {"family_id": item["family_id"], "value": item["values"][field]}
            for item in families
            if item["values"][field] is not None
        ]
        seed = derive_bootstrap_seed(
            DEFAULT_BOOTSTRAP_SEED,
            "e5_catalog_scale",
            dataset_sha256,
            catalog_size,
            field,
        )
        low, high = family_bootstrap_ci(rows, "value", seed=seed)
        family_values = [float(item["value"]) for item in rows]
        metrics[field] = {
            "estimate": statistics.fmean(family_values) if family_values else None,
            "ci_low": low,
            "ci_high": high,
            "ci_method": "workload_family_percentile_bootstrap" if low is not None else None,
            "bootstrap_seed": seed,
            "family_count": len(rows),
            "effective_family_n": len(rows),
            **inference_eligibility(len(rows)),
            "aggregation_unit": "workload_family",
        }
    return families, {
        "aggregation_unit": "workload_family",
        "within_family_aggregation": "equal_weight_variant_macro_mean",
        "cross_family_aggregation": "equal_weight_macro_mean",
        "metrics": metrics,
    }


def evaluate_catalog_scale_recommendation(
    base_catalog: Mapping[str, Any],
    scale_images: Sequence[CatalogImageEntry],
    *,
    stage: str = "confirmatory",
    dataset_path: Path | str | None = None,
    freeze_path: Path | str | None = None,
    split_bundle: LoadedSplit | VerifiedConfirmatorySplit | None = None,
    k: int = DEFAULT_RECALL_K,
) -> dict[str, Any]:
    """Evaluate P2 recommendation quality, Recall@K, and latency on an approved catalog subset.

    Evaluates:
    1. image_acceptable_accuracy: Primary Protocol-v5 metric (selection in acceptable images for feasible cases).
    2. image_preferred_accuracy: Preferred-set image Top-1 accuracy for feasible cases.
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
    confirmatory_provenance: dict[str, Any] | None = None

    # Check explicit split bundle passed in
    if split_bundle is not None:
        if stage == "confirmatory":
            if type(split_bundle) is not VerifiedConfirmatorySplit:
                logger.warning(
                    "Confirmatory catalog-scale evaluation rejected a generic split bundle"
                )
                return {
                    "status": "NOT_EXECUTED",
                    "reason": (
                        "confirmatory_split_capability_required: use "
                        "load_confirmatory_split()"
                    ),
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
                    "confirmatory_provenance": None,
                }
            try:
                verified = verify_confirmatory_split(split_bundle)
            except Exception as exc:
                logger.warning("Failed to reverify confirmatory split: %s", exc)
                return {
                    "status": "NOT_EXECUTED",
                    "reason": f"confirmatory_split_reverification_failed: {exc}",
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
                    "confirmatory_provenance": None,
                }
            split_bundle = verified.split
            split_role = "confirmatory"
            dataset_path_str = str(verified.dataset_path)
            confirmatory_provenance = dict(verified.provenance_identity)
        elif isinstance(split_bundle, VerifiedConfirmatorySplit):
            return {
                "status": "NOT_EXECUTED",
                "reason": "development_stage_received_confirmatory_capability",
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
                "confirmatory_provenance": None,
            }
        else:
            bundle_role = (
                split_bundle.bundle.split_manifest.role.value
                if hasattr(split_bundle.bundle.split_manifest.role, "value")
                else str(split_bundle.bundle.split_manifest.role)
            )
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
                verified = verify_confirmatory_split(loaded_conf)
                split_bundle = verified.split
                split_role = "confirmatory"
                dataset_path_str = str(verified.dataset_path)
                confirmatory_provenance = dict(verified.provenance_identity)
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
    split_schema_version = str(split_bundle.bundle.schema_version)
    confirmatory_provenance_sha256 = (
        hashlib.sha256(
            json.dumps(
                confirmatory_provenance,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if confirmatory_provenance is not None
        else None
    )

    # Construct scale-specific catalog containing only scale_images
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

    feasible_count = 0
    latencies: list[float] = []
    case_records: list[dict[str, Any]] = []

    for case in cases:
        case_input = _case_input(case)
        det = p2.recommend_detailed(case_input.request())
        lat = (
            det.metadata.total_elapsed_seconds
            if (det.metadata and det.metadata.total_elapsed_seconds is not None)
            else None
        )
        if (
            isinstance(lat, bool)
            or not isinstance(lat, (int, float))
            or not math.isfinite(float(lat))
            or lat < 0
        ):
            raise RuntimeError(
                f"case {case.case_id} lacks a finite non-negative recommendation latency"
            )
        lat = float(lat)
        latencies.append(lat)

        gold = _canonical_gold(
            case,
            schema_version=split_schema_version,
            p2=p2,
        )
        feasible = bool(gold["feasible"])
        pred_candidate = det.final_candidate_id
        selected_document = p2.corpus.get(pred_candidate)
        if selected_document is None:
            raise CatalogScaleGoldError(
                f"case {case.case_id} selected candidate is absent from the scale corpus"
            )
        pred_profile = selected_document.profile_id
        pred_image = selected_document.image_id
        acceptable_cands = set(gold["acceptable_candidate_ids"])
        preferred_cands = set(gold["preferred_candidate_ids"])
        acceptable_profiles = set(gold["acceptable_profile_ids"])
        preferred_profiles = set(gold["preferred_profile_ids"])
        acceptable_images = set(gold["acceptable_image_ids"])
        preferred_images = set(gold["preferred_image_ids"])

        fused = det.retrieval_result.fused_hits if det.retrieval_result else ()
        top_k_hits = [hit.to_dict() for hit in fused[:k]]
        top_k_cands = {str(hit["candidate_id"]) for hit in top_k_hits}
        candidate_acceptable = pred_candidate in acceptable_cands if feasible else None
        candidate_preferred = pred_candidate in preferred_cands if feasible else None
        profile_acceptable = pred_profile in acceptable_profiles if feasible else None
        profile_preferred = pred_profile in preferred_profiles if feasible else None
        image_acceptable = pred_image in acceptable_images if feasible else None
        image_preferred = pred_image in preferred_images if feasible else None
        retrieval_recall = (
            len(acceptable_cands & top_k_cands) / len(acceptable_cands)
            if feasible
            else None
        )

        if feasible:
            feasible_count += 1

        source_provenance = dict(case.source_provenance or {})
        source_sha256 = hashlib.sha256(
            json.dumps(
                source_provenance,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        case_records.append(
            {
                "schema_version": SCALE_RECOMMENDATION_RECORD_SCHEMA_VERSION,
                "case_id": str(case.case_id),
                "family_id": str(case.family_id),
                "variant_id": str(case.variant_id),
                "catalog_size": len(scale_images),
                "gold_schema": gold["gold_schema"],
                "expected_feasibility": gold["expected_feasibility"],
                "predicted_candidate_id": pred_candidate,
                "predicted_profile_id": pred_profile,
                "predicted_image_id": pred_image,
                "candidate_acceptable": candidate_acceptable,
                "candidate_preferred": candidate_preferred,
                "profile_acceptable": profile_acceptable,
                "profile_preferred": profile_preferred,
                "image_acceptable": image_acceptable,
                "image_preferred": image_preferred,
                "retrieval_recall_at_k": retrieval_recall,
                "recall_k": k,
                "retrieval_top_k": top_k_hits,
                "latency_seconds": lat,
                "source_identity": {
                    "dataset_id": dataset_id,
                    "dataset_sha256": dataset_sha256,
                    "source_case_id": source_provenance.get("source_case_id"),
                    "source_dataset_id": source_provenance.get("source_dataset_id"),
                    "source_provenance_sha256": source_sha256,
                    "candidate_corpus_version": p2.corpus.corpus_version,
                    "candidate_corpus_sha256": p2.corpus.corpus_checksum,
                    "image_catalog_version": p2.corpus.source_image_catalog_version,
                    "p2_config_version": p2.config.config_version,
                    **(
                        {
                            "confirmatory_provenance_sha256": confirmatory_provenance_sha256
                        }
                        if confirmatory_provenance_sha256 is not None
                        else {}
                    ),
                },
            }
        )

    family_estimates, family_summary = _family_aggregation(
        case_records,
        dataset_sha256=dataset_sha256,
        catalog_size=len(scale_images),
    )
    summary_metrics = family_summary["metrics"]
    acc_acceptable = summary_metrics["image_acceptable"]["estimate"]
    acc_preferred = summary_metrics["image_preferred"]["estimate"]
    mean_recall = summary_metrics["retrieval_recall_at_k"]["estimate"]

    lat_sorted = sorted(latencies) if latencies else []
    latency_stats = {
        # Keep the headline mean aligned with Protocol-v5's independent unit.
        # Distributional diagnostics below retain execution-level variability.
        "mean_seconds": summary_metrics["latency_seconds"]["estimate"],
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
        "split_schema_version": split_schema_version,
        "aggregation_unit": "workload_family",
        "case_records": case_records,
        "family_estimates": family_estimates,
        "family_summary": family_summary,
        "confirmatory_provenance": confirmatory_provenance,
    }
