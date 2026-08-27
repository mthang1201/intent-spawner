"""Frozen B0/P2 study-environment identity and fairness verification."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from recommender.candidate_corpus import (
    CandidateCorpus,
    DEFAULT_PROFILE_DEFINITIONS,
)
from recommender.jupyterhub_integration import PROFILE_RESOURCES

from .schemas import UserStudyValidationError, canonical_json_sha256


FAIRNESS_MANIFEST_SCHEMA_VERSION = (
    "protocol-v5-user-study-environment-fairness-v1.0.0"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_GIT_REVISION = re.compile(r"^(?:[0-9a-f]{40}|unknown)$")
_FIELDS = frozenset(
    {
        "schema_version",
        "freeze_id",
        "profile_catalog_sha256",
        "image_catalog_sha256",
        "policy_sha256",
        "description_config_sha256",
        "configuration_sha256",
        "deployment_revision",
        "kubernetes_environment_id",
        "b0_environment_sha256",
        "p2_environment_sha256",
        "shared_environment_sha256",
    }
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise UserStudyValidationError(message)


def _safe_id(value: object, label: str) -> str:
    _require(
        isinstance(value, str) and bool(_SAFE_ID.fullmatch(value)),
        f"{label} must be a safe identifier",
    )
    return value


def _descriptions(catalog: Mapping[str, Any]) -> dict[str, Any]:
    images = catalog.get("images")
    _require(isinstance(images, Mapping), "image catalog must expose images")
    return {
        "profiles": {
            profile_id: {
                "display_name": definition["display_name"],
                "description": definition["description"],
            }
            for profile_id, definition in DEFAULT_PROFILE_DEFINITIONS.items()
        },
        "images": {
            str(image_id): {
                "display_name": image["display_name"],
                "description": image["description"],
            }
            for image_id, image in images.items()
        },
    }


def _configuration(
    catalog: Mapping[str, Any], *, config_identity: str
) -> dict[str, Any]:
    images = catalog.get("images")
    _require(isinstance(images, Mapping), "image catalog must expose images")
    return {
        "config_identity": _safe_id(config_identity, "config_identity"),
        "profiles": [
            {
                "profile_id": profile_id,
                "resources": dict(resources),
                "display_name": DEFAULT_PROFILE_DEFINITIONS[profile_id]["display_name"],
                "description": DEFAULT_PROFILE_DEFINITIONS[profile_id]["description"],
            }
            for profile_id, resources in PROFILE_RESOURCES.items()
        ],
        "images": [
            {
                "image_id": str(image_id),
                "display_name": image["display_name"],
                "description": image["description"],
                "reference": image["reference"],
            }
            for image_id, image in images.items()
        ],
        "catalog_version": catalog.get("catalog_version"),
    }


def build_fairness_manifest(
    *,
    catalog: Mapping[str, Any],
    corpus: CandidateCorpus,
    freeze_id: str,
    config_identity: str,
    deployment_revision: str,
    kubernetes_environment_id: str,
) -> dict[str, Any]:
    """Build the secret-free identity shared by both study conditions."""

    freeze_id = _safe_id(freeze_id, "fairness.freeze_id")
    config_identity = _safe_id(config_identity, "config_identity")
    kubernetes_environment_id = _safe_id(
        kubernetes_environment_id, "kubernetes_environment_id"
    )
    _require(
        isinstance(deployment_revision, str)
        and bool(_GIT_REVISION.fullmatch(deployment_revision)),
        "deployment_revision must be a full lowercase Git SHA-1 or unknown",
    )
    policy_sha256 = canonical_json_sha256(
        {
            "policy_version": corpus.policy_version,
            "candidate_corpus_sha256": corpus.corpus_checksum,
        }
    )
    description_sha256 = canonical_json_sha256(_descriptions(catalog))
    configuration_sha256 = canonical_json_sha256(
        _configuration(catalog, config_identity=config_identity)
    )
    shared_components = {
        "profile_catalog_sha256": corpus.source_profile_catalog_checksum,
        "image_catalog_sha256": corpus.source_image_catalog_checksum,
        "policy_sha256": policy_sha256,
        "description_config_sha256": description_sha256,
        "configuration_sha256": configuration_sha256,
        "deployment_revision": deployment_revision,
        "kubernetes_environment_id": kubernetes_environment_id,
    }
    shared_hash = canonical_json_sha256(shared_components)
    result = {
        "schema_version": FAIRNESS_MANIFEST_SCHEMA_VERSION,
        "freeze_id": freeze_id,
        **shared_components,
        "b0_environment_sha256": shared_hash,
        "p2_environment_sha256": shared_hash,
        "shared_environment_sha256": shared_hash,
    }
    return validate_fairness_manifest(result, confirmatory=False)


def validate_fairness_manifest(
    value: object, *, confirmatory: bool
) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "fairness_manifest must be an object")
    payload = dict(value)
    _require(set(payload) == _FIELDS, "fairness_manifest fields are incomplete or unsupported")
    _require(
        payload["schema_version"] == FAIRNESS_MANIFEST_SCHEMA_VERSION,
        "unsupported fairness manifest schema",
    )
    freeze_id = _safe_id(payload["freeze_id"], "fairness.freeze_id")
    _safe_id(payload["kubernetes_environment_id"], "kubernetes_environment_id")
    _require(
        isinstance(payload["deployment_revision"], str)
        and bool(_GIT_REVISION.fullmatch(payload["deployment_revision"])),
        "fairness deployment_revision is invalid",
    )
    for field in (
        "profile_catalog_sha256",
        "image_catalog_sha256",
        "policy_sha256",
        "description_config_sha256",
        "configuration_sha256",
        "b0_environment_sha256",
        "p2_environment_sha256",
        "shared_environment_sha256",
    ):
        _require(
            isinstance(payload[field], str) and bool(_SHA256.fullmatch(payload[field])),
            f"fairness {field} must be SHA-256",
        )
    _require(
        payload["b0_environment_sha256"]
        == payload["p2_environment_sha256"]
        == payload["shared_environment_sha256"],
        "B0 and P2 environment identities differ",
    )
    components = {
        field: payload[field]
        for field in (
            "profile_catalog_sha256",
            "image_catalog_sha256",
            "policy_sha256",
            "description_config_sha256",
            "configuration_sha256",
            "deployment_revision",
            "kubernetes_environment_id",
        )
    }
    _require(
        canonical_json_sha256(components) == payload["shared_environment_sha256"],
        "fairness shared environment checksum does not recompute",
    )
    if confirmatory:
        _require(
            freeze_id not in {"development-unfrozen", "not-recorded"},
            "confirmatory execution requires a frozen fairness identity",
        )
        _require(
            payload["deployment_revision"] != "unknown",
            "confirmatory fairness requires a deployment Git revision",
        )
        _require(
            payload["kubernetes_environment_id"] != "not-recorded",
            "confirmatory fairness requires a Kubernetes environment identity",
        )
    return payload


def validate_study_environment_identity(
    value: object, *, confirmatory: bool
) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "environment_identity must be an object")
    payload = dict(value)
    _safe_id(payload.get("environment_id"), "environment_identity.environment_id")
    fairness = payload.get("fairness_manifest")
    if fairness is None:
        _require(
            not confirmatory,
            "confirmatory execution requires environment_identity.fairness_manifest",
        )
        _require(
            payload.get("mode", "development_unfrozen") == "development_unfrozen",
            "an unfrozen environment identity must be explicitly development-only",
        )
        return payload
    payload["fairness_manifest"] = validate_fairness_manifest(
        fairness, confirmatory=confirmatory
    )
    return payload


def verify_fairness_manifest(
    value: object,
    *,
    catalog: Mapping[str, Any],
    corpus: CandidateCorpus,
    freeze_id: str,
    config_identity: str,
    deployment_revision: str,
    kubernetes_environment_id: str,
    confirmatory: bool,
) -> dict[str, Any]:
    observed = validate_fairness_manifest(value, confirmatory=confirmatory)
    expected = build_fairness_manifest(
        catalog=catalog,
        corpus=corpus,
        freeze_id=freeze_id,
        config_identity=config_identity,
        deployment_revision=deployment_revision,
        kubernetes_environment_id=kubernetes_environment_id,
    )
    _require(observed == expected, "frozen study-environment fairness identity drift")
    return observed


__all__ = [
    "FAIRNESS_MANIFEST_SCHEMA_VERSION",
    "build_fairness_manifest",
    "validate_fairness_manifest",
    "validate_study_environment_identity",
    "verify_fairness_manifest",
]
