"""Immutable-image policy checks for protocol-v3 build and Helm inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

import yaml

from cluster_evaluation.result_schema_v3 import IMAGE_RE


ROOT = Path(__file__).resolve().parents[1]
HELM_VALUES = ROOT / "helm" / "experiment-v3-values.yaml"
DOCKERFILES = (
    ROOT / "cluster_evaluation" / "Dockerfile.v3",
    ROOT / "cluster_evaluation" / "Dockerfile.jupyter-v3",
)
MUTABLE_OR_PLACEHOLDER = re.compile(
    r"(?:placeholder|example\.invalid|<[^>]+>|:latest(?:@|$))", re.IGNORECASE
)


def repository(image: str) -> str:
    return image.split("@sha256:", 1)[0]


def validate_image_reference(
    image: str, *, expected_repository: str | None = None
) -> None:
    if MUTABLE_OR_PLACEHOLDER.search(image):
        raise ValueError(f"placeholder or mutable image reference: {image}")
    if not IMAGE_RE.fullmatch(image):
        raise ValueError(
            "v3 image must use registry/repository@sha256:<64 lowercase hex>: "
            f"{image}"
        )
    if expected_repository is not None and repository(image) != expected_repository:
        raise ValueError(
            f"unexpected image repository {repository(image)!r}; "
            f"expected {expected_repository!r}"
        )


def validate_dockerfiles(paths: tuple[Path, ...] = DOCKERFILES) -> list[str]:
    bases: list[str] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("FROM "):
                continue
            image = line.split()[1]
            validate_image_reference(image)
            if ":" in image.split("@", 1)[0].rsplit("/", 1)[-1]:
                raise ValueError(
                    f"{path}: v3 base image must not include a mutable tag before its digest"
                )
            bases.append(image)
    if len(bases) != len(paths):
        raise ValueError("each v3 Dockerfile must contain exactly one pinned FROM")
    return bases


def helm_singleuser_image(path: Path = HELM_VALUES) -> str:
    payload: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    image = payload.get("singleuser", {}).get("image", {})
    name = image.get("name")
    tag = image.get("tag")
    if tag not in (None, ""):
        raise ValueError(
            f"{path}: singleuser.image.tag must be empty; put the digest in image.name"
        )
    if not isinstance(name, str):
        raise ValueError(f"{path}: missing singleuser.image.name")
    validate_image_reference(name)
    return name


def validate_rendered_manifest(path: Path, expected_image: str) -> None:
    rendered = path.read_text(encoding="utf-8")
    if expected_image not in rendered:
        raise ValueError("rendered Helm manifest does not contain the pinned Jupyter image")
    expected_repo = repository(expected_image)
    for match in re.finditer(r"(?m)^\s*image:\s*[\"']?([^\"'\s]+)", rendered):
        image = match.group(1)
        if image.startswith(expected_repo) and image != expected_image:
            raise ValueError(
                f"rendered Helm manifest contains unexpected v3 image reference {image}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate protocol-v3 image policy.")
    parser.add_argument("--direct-image")
    parser.add_argument("--expected-direct-repository")
    parser.add_argument("--expected-jupyter-repository")
    parser.add_argument("--helm-values", type=Path, default=HELM_VALUES)
    parser.add_argument("--rendered-helm", type=Path)
    args = parser.parse_args(argv)
    bases = validate_dockerfiles()
    jupyter = helm_singleuser_image(args.helm_values)
    if args.expected_jupyter_repository:
        validate_image_reference(
            jupyter, expected_repository=args.expected_jupyter_repository
        )
    if args.direct_image:
        validate_image_reference(
            args.direct_image,
            expected_repository=args.expected_direct_repository,
        )
    if args.rendered_helm:
        validate_rendered_manifest(args.rendered_helm, jupyter)
    print(
        json.dumps(
            {
                "status": "pass",
                "dockerfile_bases": bases,
                "jupyter_image": jupyter,
                "direct_image": args.direct_image,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
