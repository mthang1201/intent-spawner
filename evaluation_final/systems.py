"""Canonical system registry for the final thesis evaluation.

The registry is deliberately closed. Retrieval variants are properties of P2,
not additional systems, and historical provider experiments do not enter it.
"""

from __future__ import annotations

from typing import Any, Iterable


SYSTEM_REGISTRY_SCHEMA_VERSION = "final-primary-system-registry-v1.0.0"
PRIMARY_SYSTEM_IDS = ("B0", "P1", "P2", "P3")
P2_ABLATION_IDS = ("sparse_only", "dense_only")
P3_GATE_STATUSES = ("retained", "not_retained")

_DEFINITIONS: dict[str, dict[str, Any]] = {
    "B0": {
        "label": "Default JupyterHub manual selection",
        "description": "Manual administrator-provided profile/image selection with no recommendation.",
        "emits_recommendation": False,
        "emits_ranking": False,
        "research_questions": ["RQ1"],
    },
    "P1": {
        "label": "Existing rule-based recommendation",
        "description": "Frozen existing deterministic rule-based recommender.",
        "emits_recommendation": True,
        "emits_ranking": False,
        "research_questions": ["RQ1", "RQ2"],
    },
    "P2": {
        "label": "Structured Intent and Hybrid Retrieval",
        "description": (
            "Structured Intent, sparse+dense retrieval, Reciprocal Rank Fusion, "
            "and deterministic constraint filtering/ranking."
        ),
        "emits_recommendation": True,
        "emits_ranking": True,
        "research_questions": ["RQ1", "RQ2", "RQ3"],
        "secondary_ablations": list(P2_ABLATION_IDS),
    },
    "P3": {
        "label": "Grounded LLM reranking over P2",
        "description": "P2 followed only by schema-validated reranking of P2-feasible IDs.",
        "emits_recommendation": True,
        "emits_ranking": True,
        "research_questions": ["RQ1", "RQ3"],
        "conditional_on_gate": True,
    },
}


def validate_p3_gate_status(status: str) -> str:
    if status not in P3_GATE_STATUSES:
        raise ValueError(f"p3_gate_status must be one of {P3_GATE_STATUSES}")
    return status


def active_primary_system_ids(p3_gate_status: str) -> tuple[str, ...]:
    """Return the confirmatory systems, adding P3 only after a retained gate."""

    validate_p3_gate_status(p3_gate_status)
    return PRIMARY_SYSTEM_IDS if p3_gate_status == "retained" else PRIMARY_SYSTEM_IDS[:3]


def validate_primary_system_id(system_id: object) -> str:
    if not isinstance(system_id, str) or system_id not in PRIMARY_SYSTEM_IDS:
        raise ValueError(
            f"primary system ID must be exactly one of {PRIMARY_SYSTEM_IDS}; got {system_id!r}"
        )
    return system_id


def validate_active_systems(
    system_ids: Iterable[str], *, p3_gate_status: str
) -> tuple[str, ...]:
    observed = tuple(system_ids)
    if len(observed) != len(set(observed)):
        raise ValueError("active primary system IDs must be unique")
    expected = active_primary_system_ids(p3_gate_status)
    if observed != expected:
        raise ValueError(f"active primary systems must be exactly {expected}")
    return observed


def validate_p2_ablation_id(ablation_id: object) -> str:
    if not isinstance(ablation_id, str) or ablation_id not in P2_ABLATION_IDS:
        raise ValueError(f"P2 ablation ID must be one of {P2_ABLATION_IDS}")
    return ablation_id


def system_registry(p3_gate_status: str) -> dict[str, Any]:
    active = set(active_primary_system_ids(p3_gate_status))
    systems = {
        system_id: {
            **definition,
            "active_in_final_evaluation": system_id in active,
            "gate_status": p3_gate_status if system_id == "P3" else "not_applicable",
        }
        for system_id, definition in _DEFINITIONS.items()
    }
    if tuple(systems) != PRIMARY_SYSTEM_IDS:
        raise RuntimeError("canonical system registry order changed")
    return {
        "schema_version": SYSTEM_REGISTRY_SCHEMA_VERSION,
        "allowed_primary_system_ids": list(PRIMARY_SYSTEM_IDS),
        "active_primary_system_ids": list(active_primary_system_ids(p3_gate_status)),
        "systems": systems,
        "classification_rules": {
            "p2_retrieval_variants": "secondary_ablations_nested_under_P2",
            "direct_external_or_local_llm_experiments": "historical_reference_only",
            "additional_primary_system_ids_permitted": False,
        },
    }


__all__ = [
    "P2_ABLATION_IDS",
    "P3_GATE_STATUSES",
    "PRIMARY_SYSTEM_IDS",
    "SYSTEM_REGISTRY_SCHEMA_VERSION",
    "active_primary_system_ids",
    "system_registry",
    "validate_active_systems",
    "validate_p2_ablation_id",
    "validate_p3_gate_status",
    "validate_primary_system_id",
]
