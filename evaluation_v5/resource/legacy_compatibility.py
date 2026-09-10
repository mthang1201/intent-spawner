"""Explicit registry and read-only adapters for preserved legacy E4 packages.

Historical and readiness artifacts must never be rewritten. Where a legacy package
cannot be regenerated under current semantics, it is explicitly registered as
bounded legacy design/readiness evidence with independently verifiable checksum
and non-claimable status.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

BOUNDED_LEGACY_E4_PACKAGES: dict[str, dict[str, Any]] = {
    "e4-resource-envelope-dry-run-20260828": {
        "package_type": "resource_envelope",
        "schema_version": "protocol-v5-resource-calibration-run-v1.0.0",
        "role": "bounded_legacy_readiness_design",
        "execution_status": "DRY_RUN",
        "cluster_measurement_status": "NOT_EXECUTED",
        "claim_eligible": False,
        "claims_permitted": False,
        "sha256sums_digest": "ddaa9157b347ab0e3c5a256af5f4371b407d24f2030adb6de5cc7cb9cff10881",
        "reason": "Preserved v1.0.0 dry-run readiness package.",
    },
    "e4-resource-envelope-dry-run-20260828T065331Z": {
        "package_type": "resource_envelope",
        "schema_version": "protocol-v5-resource-calibration-run-v1.0.0",
        "role": "bounded_legacy_readiness_design",
        "execution_status": "DRY_RUN",
        "cluster_measurement_status": "NOT_EXECUTED",
        "claim_eligible": False,
        "claims_permitted": False,
        "sha256sums_digest": "2fce2fd8cf005209a6a7195511a1e8085af931a9e0fd48171b1c04607f104908",
        "reason": "Preserved v1.0.0 dry-run readiness package.",
    },
    "e4-resource-envelope-readiness-dry-run-20260828T074359Z": {
        "package_type": "resource_envelope",
        "schema_version": "protocol-v5-resource-calibration-run-v1.0.0",
        "role": "bounded_legacy_readiness_design",
        "execution_status": "DRY_RUN",
        "cluster_measurement_status": "NOT_EXECUTED",
        "claim_eligible": False,
        "claims_permitted": False,
        "sha256sums_digest": "9bcb4a22ad1e55d2f09733aad4673eaacb828dd64f4e1cdc1069bcaea41598a6",
        "reason": "Preserved v1.0.0 readiness dry-run package with v1.0 cluster eligibility policy.",
    },
    "e4-resource-envelope-dry-run-20260904T075658Z": {
        "package_type": "resource_envelope",
        "schema_version": "protocol-v5-resource-calibration-run-v1.1.0",
        "role": "bounded_legacy_dry_run",
        "execution_status": "DRY_RUN",
        "cluster_measurement_status": "NOT_EXECUTED",
        "claim_eligible": False,
        "claims_permitted": False,
        "sha256sums_digest": "e567095d6e5d4585f9c75e252847c8e9ab87b0f343527393f80694e746bc1fe1",
        "reason": "Preserved early v1.1 dry-run package omitting trial_observation_schema_version.",
    },
    "e4-resource-envelope-dry-run-20260904T081412Z": {
        "package_type": "resource_envelope",
        "schema_version": "protocol-v5-resource-calibration-run-v1.1.0",
        "role": "bounded_legacy_dry_run",
        "execution_status": "DRY_RUN",
        "cluster_measurement_status": "NOT_EXECUTED",
        "claim_eligible": False,
        "claims_permitted": False,
        "sha256sums_digest": "02323af41e6f0511b9760ff7a4a55f9474f2f088630c41207a6b1daad2a41678",
        "reason": "Preserved early v1.1 dry-run package omitting trial_observation_schema_version.",
    },
    "e4-resource-efficiency-dry-run-20260904T093503Z": {
        "package_type": "resource_efficiency",
        "schema_version": "protocol-v5-resource-efficiency-raw-package-v1.0.0",
        "role": "bounded_legacy_readiness_design",
        "execution_status": "NOT_EXECUTED",
        "cluster_measurement_status": "NOT_EXECUTED",
        "claim_eligible": False,
        "claims_permitted": False,
        "sha256sums_digest": "87f0858380aae6565942cc1beb4d949ba4c51eb17521f0eb736eb394ecda3ea0",
        "reason": "Preserved efficiency dry-run plan predating explicit independent_semantic_n key.",
    },
    "e4-resource-efficiency-dry-run-20260904T094050Z": {
        "package_type": "resource_efficiency",
        "schema_version": "protocol-v5-resource-efficiency-raw-package-v1.0.0",
        "role": "bounded_legacy_readiness_design",
        "execution_status": "NOT_EXECUTED",
        "cluster_measurement_status": "NOT_EXECUTED",
        "claim_eligible": False,
        "claims_permitted": False,
        "sha256sums_digest": "156bf92cda44a1ece3daa425f17ed6d7c9a0f17071a745f87ad37bb7a2ae88a5",
        "reason": "Preserved efficiency dry-run plan predating explicit independent_semantic_n key.",
    },
    "e4-resource-efficiency-dry-run-20260904T094316Z": {
        "package_type": "resource_efficiency",
        "schema_version": "protocol-v5-resource-efficiency-raw-package-v1.0.0",
        "role": "bounded_legacy_readiness_design",
        "execution_status": "NOT_EXECUTED",
        "cluster_measurement_status": "NOT_EXECUTED",
        "claim_eligible": False,
        "claims_permitted": False,
        "sha256sums_digest": "ad6e0c1da00d99445854f18cba08a0930ef0f1f2e5d9d3620f49c994ff8d8a2a",
        "reason": "Preserved efficiency dry-run plan predating explicit independent_semantic_n key.",
    },
}


def get_bounded_legacy_metadata(name_or_path: str | Path) -> dict[str, Any] | None:
    """Look up legacy registration by directory name or path."""
    name = Path(name_or_path).name
    return BOUNDED_LEGACY_E4_PACKAGES.get(name)


def is_bounded_legacy_package(name_or_path: str | Path) -> bool:
    """Return True if this directory is a known bounded legacy E4 package."""
    return get_bounded_legacy_metadata(name_or_path) is not None


def verify_bounded_legacy_integrity(root: Path) -> dict[str, Any]:
    """Verify SHA256SUMS against recorded bounded registry digest."""
    root = root.resolve()
    meta = get_bounded_legacy_metadata(root)
    if meta is None:
        raise ValueError(f"{root.name} is not a registered bounded legacy package")

    sums_path = root / "SHA256SUMS"
    if not sums_path.is_file():
        raise ValueError(f"bounded legacy package {root.name} lacks SHA256SUMS")

    actual_digest = hashlib.sha256(sums_path.read_bytes()).hexdigest()
    if actual_digest != meta["sha256sums_digest"]:
        raise ValueError(
            f"bounded legacy package {root.name} checksum manifest mismatch: "
            f"expected {meta['sha256sums_digest']}, got {actual_digest}"
        )

    return {
        "status": "pass",
        "bounded_legacy_package": root.name,
        "role": meta["role"],
        "sha256sums_digest": actual_digest,
        "claim_eligible": False,
    }


__all__ = [
    "BOUNDED_LEGACY_E4_PACKAGES",
    "get_bounded_legacy_metadata",
    "is_bounded_legacy_package",
    "verify_bounded_legacy_integrity",
]
