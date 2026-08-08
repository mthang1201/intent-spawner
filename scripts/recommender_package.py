#!/usr/bin/env python3
"""Build the externally managed recommender ConfigMap and rollout values."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "recommender"


def _deployment_module():
    path = PACKAGE_DIR / "deployment.py"
    spec = importlib.util.spec_from_file_location("recommender_deployment_metadata", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load package metadata from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _package_data(module) -> dict[str, str]:
    data = {
        name: (PACKAGE_DIR / name).read_text(encoding="utf-8")
        for name in module.RUNTIME_FILES
    }
    size = sum(len(content.encode("utf-8")) for content in data.values())
    if size > module.MAX_CONFIGMAP_PAYLOAD_BYTES:
        raise RuntimeError(
            f"runtime package is {size} bytes; limit is "
            f"{module.MAX_CONFIGMAP_PAYLOAD_BYTES} bytes"
        )
    return data


def _manifest(module, *, name: str, namespace: str) -> dict[str, object]:
    checksum = module.compute_package_checksum(PACKAGE_DIR)
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/name": "intent-spawner-recommender",
                "app.kubernetes.io/part-of": "jupyterhub",
                "intent-spawner.openai.com/package-version": module.PACKAGE_VERSION,
            },
            "annotations": {
                "intent-spawner.openai.com/package-checksum": checksum,
            },
        },
        "data": _package_data(module),
    }


def _rollout_values(module) -> dict[str, object]:
    checksum = module.compute_package_checksum(PACKAGE_DIR)
    return {
        "hub": {
            "annotations": {
                "intent-spawner.openai.com/recommender-checksum": checksum,
                "intent-spawner.openai.com/recommender-version": module.PACKAGE_VERSION,
            },
            "extraEnv": {
                module.PACKAGE_CHECKSUM_ENV_VAR: checksum,
                module.PACKAGE_VERSION_ENV_VAR: module.PACKAGE_VERSION,
            },
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("checksum", "manifest", "rollout-values", "verify")
    )
    parser.add_argument("--name", default="intent-spawner-recommender")
    parser.add_argument("--namespace", default="z2jh-context-demo")
    args = parser.parse_args()
    module = _deployment_module()

    # Read and size-check the exact allowlist for every command.
    data = _package_data(module)
    checksum = module.compute_package_checksum(PACKAGE_DIR)
    if args.command == "checksum":
        print(checksum)
    elif args.command == "manifest":
        print(json.dumps(_manifest(module, name=args.name, namespace=args.namespace)))
    elif args.command == "rollout-values":
        print(json.dumps(_rollout_values(module)))
    else:
        print(
            json.dumps(
                {
                    "package_version": module.PACKAGE_VERSION,
                    "package_checksum": checksum,
                    "file_count": len(data),
                    "payload_bytes": sum(
                        len(content.encode("utf-8")) for content in data.values()
                    ),
                    "max_payload_bytes": module.MAX_CONFIGMAP_PAYLOAD_BYTES,
                    "runtime_files": list(module.RUNTIME_FILES),
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
