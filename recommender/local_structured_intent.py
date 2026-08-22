"""Deterministic local StructuredIntent extraction for the primary P2 path.

This extractor recognizes only explicit, auditable syntax: Python imports,
minimum CPU/memory statements, GPU requirement phrases, and an explicit
dataset-size form value.  It does not select candidates or contain P1 scoring
rules.  The parser contract and import aliases are versioned and hashed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import unicodedata

from .models import (
    ExtractionMode,
    ExtractionProvenance,
    GPURequirement,
    RecommendationRequest,
    ResourceConstraints,
    StructuredIntent,
)


LOCAL_EXTRACTOR_NAME = "p2-local-structured-parser"
LOCAL_EXTRACTOR_VERSION = "local-structured-parser-v1.1.0"
LOCAL_EXTRACTOR_MODEL_ID = (
    f"deterministic-explicit-parser-v1.1.0-python-{sys.version_info.major}.{sys.version_info.minor}"
)
LOCAL_EXTRACTOR_PROMPT_VERSION = "local-parser-contract-v1.1.0"
IMPORT_ALIAS_VERSION = "python-import-aliases-v1.0.0"
PYTHON_STDLIB_POLICY_VERSION = "python-stdlib-exclusion-v1.0.0"
PYTHON_STDLIB_MODULES = frozenset(sys.stdlib_module_names)
IMPORT_ALIASES = {
    "sklearn": "scikit-learn",
}

_IMPORT_PATTERN = re.compile(r"(?m)^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_.-]*)")
_CPU_PATTERN = re.compile(
    r"(?:at\s+least|minimum|min\.?|requires?|needs?|cần|tối\s+thiểu)\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*(?:cpu(?:\s+cores?)?|cores?)\b",
    re.IGNORECASE,
)
_MEMORY_PATTERN = re.compile(
    r"(?:at\s+least|minimum|min\.?|requires?|needs?|cần|tối\s+thiểu)\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*(?:gb|gib)\s*(?:ram|memory|bộ\s+nhớ)\b",
    re.IGNORECASE,
)
_REQUIRED_PACKAGE_PATTERN = re.compile(
    r"\b(?:requires?|must\s+use|needs?|cần)\s+(?:the\s+)?"
    r"([A-Za-z][A-Za-z0-9_.+-]{1,63})\s+(?:library|framework|package)\b",
    re.IGNORECASE,
)

_GPU_FORBIDDEN = re.compile(r"\b(?:no|without|forbid(?:den)?|không)\s+(?:a\s+)?gpu\b", re.IGNORECASE)
_GPU_REQUIRED = re.compile(
    r"\b(?:require[sd]?|must\s+(?:have|use)|need[sd]?|cần|bắt\s+buộc)\b[^.\n]{0,40}\bgpu\b",
    re.IGNORECASE,
)
_GPU_PREFERRED = re.compile(r"\b(?:prefer(?:red)?|would\s+like|ưu\s+tiên)\b[^.\n]{0,40}\bgpu\b", re.IGNORECASE)
_GPU_MENTION = re.compile(r"\bgpu\b", re.IGNORECASE)


def local_parser_contract_sha256() -> str:
    payload = {
        "extractor_version": LOCAL_EXTRACTOR_VERSION,
        "model_id": LOCAL_EXTRACTOR_MODEL_ID,
        "prompt_version": LOCAL_EXTRACTOR_PROMPT_VERSION,
        "import_alias_version": IMPORT_ALIAS_VERSION,
        "import_aliases": IMPORT_ALIASES,
        "python_stdlib_policy_version": PYTHON_STDLIB_POLICY_VERSION,
        "python_stdlib_modules": sorted(PYTHON_STDLIB_MODULES),
        "patterns": {
            "import": _IMPORT_PATTERN.pattern,
            "cpu": _CPU_PATTERN.pattern,
            "memory": _MEMORY_PATTERN.pattern,
            "required_package": _REQUIRED_PACKAGE_PATTERN.pattern,
            "gpu_forbidden": _GPU_FORBIDDEN.pattern,
            "gpu_required": _GPU_REQUIRED.pattern,
            "gpu_preferred": _GPU_PREFERRED.pattern,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


LOCAL_EXTRACTOR_PROMPT_SHA256 = local_parser_contract_sha256()


def _explicit_dataset_size(value: object) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _minimum(pattern: re.Pattern[str], text: str) -> float | None:
    values = [float(match.group(1)) for match in pattern.finditer(text)]
    return max(values) if values else None


def _gpu_requirement(text: str) -> GPURequirement:
    if _GPU_FORBIDDEN.search(text):
        return GPURequirement.FORBIDDEN
    if _GPU_REQUIRED.search(text):
        return GPURequirement.REQUIRED
    if _GPU_PREFERRED.search(text) or _GPU_MENTION.search(text):
        return GPURequirement.PREFERRED
    return GPURequirement.UNSPECIFIED


def _normalized_query(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


class LocalStructuredIntentExtractor:
    """Extract explicit structured facts locally with no provider dependency."""

    network_bound = False
    extractor_name = LOCAL_EXTRACTOR_NAME
    extractor_version = LOCAL_EXTRACTOR_VERSION
    model_id = LOCAL_EXTRACTOR_MODEL_ID
    prompt_version = LOCAL_EXTRACTOR_PROMPT_VERSION
    prompt_sha256 = LOCAL_EXTRACTOR_PROMPT_SHA256

    def extract(self, request: RecommendationRequest) -> StructuredIntent:
        if not isinstance(request, RecommendationRequest):
            raise TypeError("request must be a RecommendationRequest")
        combined = f"{request.intent}\n{request.code_context}"
        imported = {
            match.group(1).split(".", 1)[0].casefold()
            for match in _IMPORT_PATTERN.finditer(request.code_context)
        }
        libraries = {
            IMPORT_ALIASES.get(module, module)
            for module in imported
            if module not in PYTHON_STDLIB_MODULES
        }
        ignored_package_words = {
            "a", "an", "the", "cpu", "gpu", "memory", "ram", "at", "additional"
        }
        for match in _REQUIRED_PACKAGE_PATTERN.finditer(combined):
            package = match.group(1).casefold()
            if package not in ignored_package_words and not package[0].isdigit():
                libraries.add(IMPORT_ALIASES.get(package, package))

        return StructuredIntent(
            required_libraries=tuple(libraries),
            resource_constraints=ResourceConstraints(
                gpu_requirement=_gpu_requirement(combined),
                minimum_cpu_cores=_minimum(_CPU_PATTERN, combined),
                minimum_memory_gb=_minimum(_MEMORY_PATTERN, combined),
                dataset_size_gb=_explicit_dataset_size(request.dataset_size_gb),
            ),
            normalized_query=_normalized_query(request.intent),
            extraction_confidence=1.0,
            extraction_provenance=ExtractionProvenance(
                extractor_name=self.extractor_name,
                extractor_version=self.extractor_version,
                prompt_version=self.prompt_version,
                prompt_sha256=self.prompt_sha256,
                model_id=self.model_id,
                mode=ExtractionMode.PRIMARY,
                degraded_reason=None,
            ),
        )


__all__ = [
    "IMPORT_ALIASES",
    "IMPORT_ALIAS_VERSION",
    "LOCAL_EXTRACTOR_MODEL_ID",
    "LOCAL_EXTRACTOR_NAME",
    "LOCAL_EXTRACTOR_PROMPT_SHA256",
    "LOCAL_EXTRACTOR_PROMPT_VERSION",
    "LOCAL_EXTRACTOR_VERSION",
    "PYTHON_STDLIB_MODULES",
    "PYTHON_STDLIB_POLICY_VERSION",
    "LocalStructuredIntentExtractor",
    "local_parser_contract_sha256",
]
