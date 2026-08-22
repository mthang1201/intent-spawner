"""Final thesis evaluation harness for the canonical B0/P1/P2/P3 systems."""

from .systems import (
    P2_ABLATION_IDS,
    PRIMARY_SYSTEM_IDS,
    active_primary_system_ids,
    system_registry,
)

__all__ = [
    "P2_ABLATION_IDS",
    "PRIMARY_SYSTEM_IDS",
    "active_primary_system_ids",
    "system_registry",
]
