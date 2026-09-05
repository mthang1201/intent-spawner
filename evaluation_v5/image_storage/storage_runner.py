"""Storage scalability runners for Protocol-v5 E5 catalog layer accounting."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping, Sequence

from .contracts import (
    SecurityVerificationError,
    parse_image_digest,
    validate_approved_image_reference,
)
from .storage_contracts import (
    ImageLayerMetadata,
    LayerInspection,
    PrefixStorageMeasurement,
    SIZE_DOMAIN_COMPRESSED_OCI_BLOB,
    StorageExecutionStatus,
    get_ordered_catalog_images,
)

logger = logging.getLogger(__name__)


class BaseStorageRunner:
    """Abstract base class for inspecting catalog image layers and measuring prefix scaling."""

    def __init__(
        self,
        catalog: Mapping[str, Any],
        *,
        target_arch: str = "amd64",
        target_os: str = "linux",
    ) -> None:
        self.catalog = catalog
        self.target_arch = target_arch
        self.target_os = target_os

    def inspect_image_layers(
        self,
        image_id: str,
        image_reference: str,
    ) -> ImageLayerMetadata:
        raise NotImplementedError

    def measure_all(
        self,
        images: Sequence[tuple[str, str, str]] | None = None,
    ) -> tuple[list[ImageLayerMetadata], list[PrefixStorageMeasurement], str]:
        """Inspect catalog images in order and compute cumulative prefix storage."""
        ordered = (
            list(images)
            if images is not None
            else get_ordered_catalog_images(self.catalog)
        )
        inspections: list[ImageLayerMetadata] = []

        for image_id, ref, _ in ordered:
            metadata = self.inspect_image_layers(image_id, ref)
            inspections.append(metadata)

        prefixes: list[PrefixStorageMeasurement] = []
        cumulative_unique_layers: dict[str, int] = {}
        cumulative_naive_bytes = 0

        for idx, meta in enumerate(inspections, start=1):
            cumulative_naive_bytes += meta.total_bytes
            for layer in meta.layers:
                cumulative_unique_layers[layer.digest] = layer.size

            unique_bytes = sum(cumulative_unique_layers.values())
            savings = cumulative_naive_bytes - unique_bytes
            ratio = (savings / cumulative_naive_bytes) if cumulative_naive_bytes > 0 else 0.0
            digests_up_to_prefix = tuple(m.image_digest for m in inspections[:idx])

            prefixes.append(
                PrefixStorageMeasurement(
                    prefix_size=idx,
                    image_digests=digests_up_to_prefix,
                    naive_logical_bytes=cumulative_naive_bytes,
                    unique_layer_bytes=unique_bytes,
                    savings_bytes=savings,
                    savings_ratio=ratio,
                )
            )

        return inspections, prefixes, StorageExecutionStatus.OBSERVED.value


class DryRunStorageRunner(BaseStorageRunner):
    """Dry-run runner that emits explicit NOT_EXECUTED status without inventing layer sizes."""

    def inspect_image_layers(
        self,
        image_id: str,
        image_reference: str,
    ) -> ImageLayerMetadata:
        digest = validate_approved_image_reference(image_reference, self.catalog)
        pinned = "@sha256:" in image_reference
        return ImageLayerMetadata(
            image_id=image_id,
            image_reference=image_reference,
            image_digest=digest,
            platform={"architecture": self.target_arch, "os": self.target_os},
            layers=(),
            total_bytes=0,
            is_digest_pinned=pinned,
            resolved_digest=digest,
            manifest_digest=digest,
            size_domain=SIZE_DOMAIN_COMPRESSED_OCI_BLOB,
        )

    def measure_all(
        self,
        images: Sequence[tuple[str, str, str]] | None = None,
    ) -> tuple[list[ImageLayerMetadata], list[PrefixStorageMeasurement], str]:
        ordered = (
            list(images)
            if images is not None
            else get_ordered_catalog_images(self.catalog)
        )
        inspections = [self.inspect_image_layers(img_id, ref) for img_id, ref, _ in ordered]

        prefixes: list[PrefixStorageMeasurement] = []
        for idx in range(1, len(ordered) + 1):
            digests = tuple(item[2] for item in ordered[:idx])
            prefixes.append(
                PrefixStorageMeasurement(
                    prefix_size=idx,
                    image_digests=digests,
                    naive_logical_bytes=0,
                    unique_layer_bytes=0,
                    savings_bytes=0,
                    savings_ratio=0.0,
                )
            )

        return inspections, prefixes, StorageExecutionStatus.NOT_EXECUTED.value


class SyntheticStorageRunner(BaseStorageRunner):
    """Configurable synthetic runner for deterministic unit testing of prefix deduplication."""

    def __init__(
        self,
        catalog: Mapping[str, Any],
        *,
        target_arch: str = "amd64",
        target_os: str = "linux",
        injected_image_layers: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    ) -> None:
        super().__init__(catalog, target_arch=target_arch, target_os=target_os)
        self.injected_image_layers = injected_image_layers or {}

    def inspect_image_layers(
        self,
        image_id: str,
        image_reference: str,
    ) -> ImageLayerMetadata:
        digest = validate_approved_image_reference(image_reference, self.catalog)
        if image_id in self.injected_image_layers:
            raw_layers = self.injected_image_layers[image_id]
            layers = tuple(LayerInspection.from_dict(l) for l in raw_layers)
        else:
            # Default hierarchical synthetic layers
            base_layers = [
                LayerInspection(digest="sha256:0000000000000000000000000000000000000000000000000000000000000001", size=50_000_000, media_type="application/vnd.oci.image.layer.v1.tar+gzip"),
                LayerInspection(digest="sha256:0000000000000000000000000000000000000000000000000000000000000002", size=100_000_000, media_type="application/vnd.oci.image.layer.v1.tar+gzip"),
            ]
            if image_id == "minimal-python":
                layers = tuple(base_layers)
            elif image_id == "scipy-data-science":
                scipy_layer = LayerInspection(digest="sha256:0000000000000000000000000000000000000000000000000000000000000003", size=200_000_000, media_type="application/vnd.oci.image.layer.v1.tar+gzip")
                layers = tuple(base_layers + [scipy_layer])
            elif image_id == "pytorch-deep-learning":
                torch_layer = LayerInspection(digest="sha256:0000000000000000000000000000000000000000000000000000000000000004", size=500_000_000, media_type="application/vnd.oci.image.layer.v1.tar+gzip")
                layers = tuple(base_layers + [torch_layer])
            else:
                tf_layer = LayerInspection(digest="sha256:0000000000000000000000000000000000000000000000000000000000000005", size=450_000_000, media_type="application/vnd.oci.image.layer.v1.tar+gzip")
                layers = tuple(base_layers + [tf_layer])

        for l in layers:
            if l.size < 0:
                raise RuntimeError(f"Negative layer size in synthetic layer: {l}")

        total = sum(l.size for l in layers)
        pinned = "@sha256:" in image_reference
        return ImageLayerMetadata(
            image_id=image_id,
            image_reference=image_reference,
            image_digest=digest,
            platform={"architecture": self.target_arch, "os": self.target_os},
            layers=layers,
            total_bytes=total,
            is_digest_pinned=pinned,
            resolved_digest=digest,
            manifest_digest=digest,
            manifest_media_type="application/vnd.oci.image.manifest.v1+json",
            config_digest="sha256:c000000000000000000000000000000000000000000000000000000000000000",
            size_domain=SIZE_DOMAIN_COMPRESSED_OCI_BLOB,
        )


class DockerManifestStorageRunner(BaseStorageRunner):
    """Live measurement runner inspecting exact OCI/Docker layers via container CLI/registry."""

    def __init__(
        self,
        catalog: Mapping[str, Any],
        *,
        target_arch: str = "amd64",
        target_os: str = "linux",
        timeout_seconds: float = 60.0,
    ) -> None:
        super().__init__(catalog, target_arch=target_arch, target_os=target_os)
        self.timeout_seconds = timeout_seconds

    def inspect_image_layers(
        self,
        image_id: str,
        image_reference: str,
    ) -> ImageLayerMetadata:
        digest = validate_approved_image_reference(image_reference, self.catalog)

        cmd = ["docker", "manifest", "inspect", "--verbose", image_reference]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to execute docker manifest inspect for {image_reference}: {exc}"
            ) from exc

        if res.returncode != 0:
            raise RuntimeError(
                f"docker manifest inspect failed for {image_reference} (exit {res.returncode}): {res.stderr.strip()}"
            )

        try:
            raw_data = json.loads(res.stdout.strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Malformed JSON from docker manifest inspect for {image_reference}: {exc}"
            ) from exc

        manifest_entries = raw_data if isinstance(raw_data, list) else [raw_data]
        matched_manifest: dict[str, Any] | None = None
        matched_entry: dict[str, Any] | None = None

        for entry in manifest_entries:
            desc = entry.get("Descriptor", {})
            platform = desc.get("platform", {})
            arch = platform.get("architecture")
            os_name = platform.get("os")
            if arch == self.target_arch and os_name == self.target_os:
                matched_manifest = (
                    entry.get("OCIManifest")
                    or entry.get("SchemaV2Manifest")
                )
                if not matched_manifest and "Raw" in entry:
                    try:
                        decoded = base64.b64decode(entry["Raw"]).decode("utf-8")
                        matched_manifest = json.loads(decoded)
                    except Exception:
                        pass
                if matched_manifest:
                    matched_entry = entry
                    break

        if not matched_manifest:
            raise RuntimeError(
                f"No matching manifest found for platform {self.target_os}/{self.target_arch} in {image_reference}"
            )

        raw_layers = matched_manifest.get("layers", [])
        if not raw_layers:
            raise RuntimeError(f"Manifest for {image_reference} contains zero layers")

        desc = matched_entry.get("Descriptor", {}) if matched_entry else {}
        manifest_digest = str(desc.get("digest", digest))
        manifest_media_type = str(
            desc.get(
                "mediaType",
                matched_manifest.get(
                    "mediaType", "application/vnd.oci.image.manifest.v1+json"
                ),
            )
        )
        config_digest = str(matched_manifest.get("config", {}).get("digest", ""))
        pinned = "@sha256:" in image_reference

        layers: list[LayerInspection] = []
        for l in raw_layers:
            if "size" not in l or l["size"] is None or int(l["size"]) < 0:
                raise RuntimeError(
                    f"Missing or negative layer size in manifest for {image_reference}"
                )
            layer_digest = str(l["digest"])
            layer_size = int(l["size"])
            media_type = str(l.get("mediaType", ""))
            layers.append(
                LayerInspection(digest=layer_digest, size=layer_size, media_type=media_type)
            )

        total_bytes = sum(layer.size for layer in layers)
        return ImageLayerMetadata(
            image_id=image_id,
            image_reference=image_reference,
            image_digest=digest,
            platform={"architecture": self.target_arch, "os": self.target_os},
            layers=tuple(layers),
            total_bytes=total_bytes,
            is_digest_pinned=pinned,
            resolved_digest=digest,
            manifest_digest=manifest_digest,
            manifest_media_type=manifest_media_type,
            config_digest=config_digest,
            size_domain=SIZE_DOMAIN_COMPRESSED_OCI_BLOB,
        )


def create_storage_runner(
    catalog: Mapping[str, Any],
    mode: str = "auto",
    *,
    target_arch: str = "amd64",
    target_os: str = "linux",
    dry_run_if_unavailable: bool = True,
    timeout_seconds: float = 60.0,
    injected_image_layers: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> BaseStorageRunner:
    """Factory creating the appropriate storage runner based on mode and availability."""
    selected_mode = mode.lower()

    if selected_mode == "auto":
        # Check if docker manifest inspect is functional
        try:
            proc = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            if proc.returncode == 0:
                return DockerManifestStorageRunner(
                    catalog,
                    target_arch=target_arch,
                    target_os=target_os,
                    timeout_seconds=timeout_seconds,
                )
        except Exception:
            pass

        if dry_run_if_unavailable:
            return DryRunStorageRunner(
                catalog,
                target_arch=target_arch,
                target_os=target_os,
            )
        raise RuntimeError("No container runtime detected and dry-run fallback disabled.")

    if selected_mode == "docker":
        return DockerManifestStorageRunner(
            catalog,
            target_arch=target_arch,
            target_os=target_os,
            timeout_seconds=timeout_seconds,
        )
    if selected_mode in ("dry-run", "dry_run"):
        return DryRunStorageRunner(
            catalog,
            target_arch=target_arch,
            target_os=target_os,
        )
    if selected_mode == "synthetic":
        return SyntheticStorageRunner(
            catalog,
            target_arch=target_arch,
            target_os=target_os,
            injected_image_layers=injected_image_layers,
        )

    raise ValueError(f"Unsupported storage runner mode: {mode!r}")
