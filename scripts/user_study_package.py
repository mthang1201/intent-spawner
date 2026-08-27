#!/usr/bin/env python3
"""Build and verify the opt-in Protocol-v5 study deployment artifacts.

The study configuration ConfigMap contains only the deterministic assignment
manifest and browser-safe task projection.  The authoritative task/gold bundle
is deliberately not accepted by any command in this script.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
STUDY_PACKAGE_DIR = ROOT / "evaluation_v5" / "user_study"
sys.path.insert(0, str(ROOT))

from evaluation_v5.user_study.assignment import (  # noqa: E402
    AssignmentManifest,
    validate_assignment_manifest,
)
from evaluation_v5.user_study.hub import (  # noqa: E402
    STUDY_HUB_ADAPTER_VERSION,
    STUDY_HUB_MAX_PACKAGE_BYTES,
    STUDY_HUB_PACKAGE_CHECKSUM_ENV,
    STUDY_HUB_PACKAGE_VERSION_ENV,
    STUDY_HUB_RUNTIME_FILES,
    STUDY_ASSIGNMENT_CHECKSUM_ENV,
    STUDY_CONFIG_IDENTITY_ENV,
    STUDY_ENVIRONMENT_ID_ENV,
    compute_study_adapter_checksum,
    validate_browser_task_set,
)
from evaluation_v5.user_study.schemas import canonical_json_sha256  # noqa: E402


DEFAULT_ADAPTER_NAME = "intent-spawner-user-study-adapter"
DEFAULT_CONFIG_NAME = "intent-spawner-user-study-config"
DEFAULT_PVC_NAME = "intent-spawner-user-study-evidence"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read JSON artifact {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} must contain one JSON object")
    return value


def _adapter_data() -> dict[str, str]:
    data = {
        name: (STUDY_PACKAGE_DIR / name).read_text(encoding="utf-8")
        for name in STUDY_HUB_RUNTIME_FILES
    }
    size = sum(len(content.encode("utf-8")) for content in data.values())
    if size > STUDY_HUB_MAX_PACKAGE_BYTES:
        raise RuntimeError(
            f"study adapter package is {size} bytes; limit is "
            f"{STUDY_HUB_MAX_PACKAGE_BYTES} bytes"
        )
    return data


def _load_study_config(
    assignment_path: Path,
    browser_path: Path,
    *,
    allow_development: bool,
) -> tuple[AssignmentManifest, dict[str, Any]]:
    manifest = validate_assignment_manifest(
        AssignmentManifest.from_dict(_read_json(assignment_path))
    )
    browser = validate_browser_task_set(_read_json(browser_path))
    if browser["source_task_set_id"] != manifest.task_set_id:
        raise RuntimeError("browser task-set ID differs from assignment")
    if browser["source_task_set_sha256"] != manifest.task_set_sha256:
        raise RuntimeError("browser task-set source checksum differs from assignment")
    if canonical_json_sha256(browser) != manifest.browser_task_set_sha256:
        raise RuntimeError("browser task-set checksum differs from assignment")
    if not allow_development and manifest.freeze_id == "development-unfrozen":
        raise RuntimeError(
            "live installation rejects development assignments; provide an "
            "externally frozen reviewed assignment"
        )
    return manifest, browser


def _adapter_manifest(name: str, namespace: str) -> dict[str, Any]:
    checksum = compute_study_adapter_checksum(STUDY_PACKAGE_DIR)
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/name": "intent-spawner-user-study",
                "app.kubernetes.io/part-of": "jupyterhub",
                "intent-spawner.openai.com/study-only": "true",
            },
            "annotations": {
                "intent-spawner.openai.com/package-version": STUDY_HUB_ADAPTER_VERSION,
                "intent-spawner.openai.com/package-checksum": checksum,
            },
        },
        "data": _adapter_data(),
    }


def _config_manifest(
    name: str,
    namespace: str,
    assignment_path: Path,
    browser_path: Path,
    *,
    allow_development: bool,
) -> dict[str, Any]:
    manifest, browser = _load_study_config(
        assignment_path, browser_path, allow_development=allow_development
    )
    assignment_json = json.dumps(
        manifest.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    browser_json = json.dumps(
        browser, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/name": "intent-spawner-user-study",
                "app.kubernetes.io/part-of": "jupyterhub",
                "intent-spawner.openai.com/study-only": "true",
            },
            "annotations": {
                "intent-spawner.openai.com/assignment-id": manifest.assignment_id,
                "intent-spawner.openai.com/assignment-checksum": manifest.checksum,
                "intent-spawner.openai.com/browser-task-checksum": canonical_json_sha256(
                    browser
                ),
            },
        },
        "data": {
            "assignment-manifest.json": assignment_json,
            "browser-task-set.json": browser_json,
        },
    }


def _rollout_values(
    manifest: AssignmentManifest, browser: Mapping[str, Any]
) -> dict[str, Any]:
    checksum = compute_study_adapter_checksum(STUDY_PACKAGE_DIR)
    return {
        "hub": {
            "annotations": {
                "intent-spawner.openai.com/user-study-adapter-checksum": checksum,
                "intent-spawner.openai.com/user-study-adapter-version": STUDY_HUB_ADAPTER_VERSION,
                "intent-spawner.openai.com/user-study-assignment-checksum": manifest.checksum,
                "intent-spawner.openai.com/user-study-browser-checksum": canonical_json_sha256(
                    browser
                ),
            },
            "extraEnv": {
                STUDY_HUB_PACKAGE_CHECKSUM_ENV: checksum,
                STUDY_HUB_PACKAGE_VERSION_ENV: STUDY_HUB_ADAPTER_VERSION,
                STUDY_ASSIGNMENT_CHECKSUM_ENV: manifest.checksum,
                STUDY_CONFIG_IDENTITY_ENV: manifest.config_identity,
                STUDY_ENVIRONMENT_ID_ENV: str(
                    manifest.environment_identity["environment_id"]
                ),
            },
        }
    }


def _pvc_manifest(name: str, namespace: str, storage: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/name": "intent-spawner-user-study",
                "app.kubernetes.io/part-of": "jupyterhub",
                "intent-spawner.openai.com/study-only": "true",
            },
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": storage}},
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "checksum",
            "verify",
            "adapter-manifest",
            "study-config-manifest",
            "rollout-values",
            "pvc-manifest",
        ),
    )
    parser.add_argument("--namespace", default="z2jh-context-demo")
    parser.add_argument("--adapter-name", default=DEFAULT_ADAPTER_NAME)
    parser.add_argument("--config-name", default=DEFAULT_CONFIG_NAME)
    parser.add_argument("--pvc-name", default=DEFAULT_PVC_NAME)
    parser.add_argument("--storage", default="1Gi")
    parser.add_argument("--assignment", type=Path)
    parser.add_argument("--browser-tasks", type=Path)
    parser.add_argument(
        "--allow-development",
        action="store_true",
        help="permit draft assignments for local rendering/tests only",
    )
    args = parser.parse_args()

    data = _adapter_data()
    checksum = compute_study_adapter_checksum(STUDY_PACKAGE_DIR)
    needs_config = args.command in {"study-config-manifest", "rollout-values"}
    if needs_config and (args.assignment is None or args.browser_tasks is None):
        parser.error(f"{args.command} requires --assignment and --browser-tasks")
    if args.command == "verify" and (
        (args.assignment is None) != (args.browser_tasks is None)
    ):
        parser.error("verify requires both --assignment and --browser-tasks, or neither")

    if args.command == "checksum":
        print(checksum)
    elif args.command == "adapter-manifest":
        print(json.dumps(_adapter_manifest(args.adapter_name, args.namespace)))
    elif args.command == "study-config-manifest":
        print(
            json.dumps(
                _config_manifest(
                    args.config_name,
                    args.namespace,
                    args.assignment,
                    args.browser_tasks,
                    allow_development=args.allow_development,
                )
            )
        )
    elif args.command == "rollout-values":
        manifest, browser = _load_study_config(
            args.assignment,
            args.browser_tasks,
            allow_development=args.allow_development,
        )
        print(json.dumps(_rollout_values(manifest, browser)))
    elif args.command == "pvc-manifest":
        print(json.dumps(_pvc_manifest(args.pvc_name, args.namespace, args.storage)))
    else:
        result = {
            "adapter_version": STUDY_HUB_ADAPTER_VERSION,
            "adapter_checksum": checksum,
            "runtime_files": list(STUDY_HUB_RUNTIME_FILES),
            "file_count": len(data),
            "payload_bytes": sum(
                len(content.encode("utf-8")) for content in data.values()
            ),
            "max_payload_bytes": STUDY_HUB_MAX_PACKAGE_BYTES,
            "study_config_validated": args.assignment is not None,
        }
        if args.assignment is not None and args.browser_tasks is not None:
            manifest, browser = _load_study_config(
                args.assignment,
                args.browser_tasks,
                allow_development=args.allow_development,
            )
            result.update(
                {
                    "assignment_id": manifest.assignment_id,
                    "assignment_checksum": manifest.checksum,
                    "browser_task_set_checksum": canonical_json_sha256(browser),
                    "development_override": args.allow_development,
                }
            )
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
