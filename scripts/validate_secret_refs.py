#!/usr/bin/env python3
"""Preflight required secretKeyRef entries without printing secret data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import yaml


def required_secret_refs(values_path: str | Path) -> list[tuple[str, str]]:
    with Path(values_path).open(encoding="utf-8") as handle:
        values = yaml.safe_load(handle) or {}
    extra_env = values.get("hub", {}).get("extraEnv", {})
    refs: list[tuple[str, str]] = []
    for setting in extra_env.values():
        if not isinstance(setting, dict):
            continue
        ref = setting.get("valueFrom", {}).get("secretKeyRef")
        if not isinstance(ref, dict) or ref.get("optional", False):
            continue
        name = ref.get("name")
        key = ref.get("key")
        if not isinstance(name, str) or not name or not isinstance(key, str) or not key:
            raise ValueError("secretKeyRef requires non-empty name and key")
        refs.append((name, key))
    return refs


def load_secret_metadata(namespace: str, name: str) -> dict[str, Any]:
    command = ["kubectl", "get", "secret", name, "--namespace", namespace, "-o", "json"]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"required Kubernetes Secret {name!r} was not found in namespace "
            f"{namespace!r}"
        )
    return json.loads(result.stdout)


def validate_secret_refs(values_path: str | Path, namespace: str) -> None:
    by_name: dict[str, set[str]] = {}
    for name, key in required_secret_refs(values_path):
        by_name.setdefault(name, set()).add(key)
    for name, keys in by_name.items():
        secret = load_secret_metadata(namespace, name)
        available = set((secret.get("data") or {}).keys())
        for key in sorted(keys):
            if key not in available:
                raise RuntimeError(
                    f"required key {key!r} is missing from Kubernetes Secret "
                    f"{name!r} in namespace {namespace!r}"
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--values", required=True)
    parser.add_argument("--namespace", default="z2jh-context-demo")
    args = parser.parse_args()
    try:
        validate_secret_refs(args.values, args.namespace)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"recommender Secret preflight failed: {exc}", file=sys.stderr)
        return 1
    print("Required recommender Secret references are present (values not read or logged).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
