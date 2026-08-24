"""Development-only perturbation and paraphrase draft generation helper.

SAFETY WARNING:
- Generated text is for DEVELOPMENT ONLY.
- Generated text is always marked as GENERATED_DRAFT.
- Generated text NEVER automatically becomes gold.
- It requires explicit human review and approval before evaluation.
- Running this generator on confirmatory data or generating confirmatory cases
  is strictly prohibited.
"""

from __future__ import annotations

import random
import re
from typing import Any, Callable, Sequence

from .models import RobustnessFamily, RobustnessVariant
from .taxonomy import (
    EquivalenceStatus,
    HumanReviewStatus,
    PerturbationClass,
    VariantMetadata,
    VariantSource,
)


class ParaphraseGeneratorError(ValueError):
    """An error occurred during paraphrase draft generation."""


# Keyword removal mappings for paraphrase_without_obvious_keywords
_KEYWORD_REPLACEMENTS: dict[str, str] = {
    r"\bpandas\b": "a tabular data manipulation library",
    r"\bdataframe\b": "structured tabular data",
    r"\bpytorch\b": "a deep neural network framework",
    r"\btensorflow\b": "a machine learning computational graph framework",
    r"\bscikit-learn\b": "classical machine learning estimators",
    r"\bsklearn\b": "classical machine learning algorithms",
    r"\bcuda\b": "hardware acceleration",
    r"\bgpu\b": "accelerated compute processor",
    r"\bnumpy\b": "numerical array processing",
    r"\bscipy\b": "scientific computing routines",
    r"\bjupyter\b": "interactive notebook environment",
    r"\br\b": "statistical computing environment",
}

# Conversational filler contexts for irrelevant_extra_context
_EXTRA_CONTEXT_PREFIXES: Sequence[str] = (
    "Good morning assistant! I just finished my team standup meeting and was hoping you could help me. ",
    "Hello there, hope you're having a nice day. By the way, the weather is quite cloudy today. ",
    "Hi, I am currently preparing slides for my upcoming presentation next week. Specifically, ",
    "Hey! My advisor suggested I look into this topic before Friday. Basically, ",
    "Quick question for my morning workflow before I go grab some coffee: ",
)

_EXTRA_CONTEXT_SUFFIXES: Sequence[str] = (
    " Thanks in advance! Let me know if that makes sense.",
    " Any help would be appreciated. Cheers!",
    " Please let me know. Have a wonderful rest of your day!",
    " Thanks! I will check back in an hour.",
)

# Common typo patterns
_KEYBOARD_NEIGHBORS: dict[str, str] = {
    "a": "sqwz",
    "b": "vghn",
    "c": "xdfv",
    "d": "ersfxc",
    "e": "wsdr",
    "f": "rtgvcd",
    "g": "tyhbvf",
    "h": "yujnbg",
    "i": "ujko",
    "j": "uikmnh",
    "k": "ijlm",
    "l": "okp",
    "m": "njk",
    "n": "bhjm",
    "o": "iklp",
    "p": "ol",
    "q": "wa",
    "r": "edft",
    "s": "wazxde",
    "t": "rfgy",
    "u": "yhji",
    "v": "cfgb",
    "w": "qase",
    "x": "zsdc",
    "y": "tghu",
    "z": "asx",
}


def inject_typo_noise(text: str, *, rate: float = 0.08, seed: int = 42) -> str:
    """Inject realistic typos (substitutions, deletions, swaps) into text."""
    rng = random.Random(seed)
    chars = list(text)
    n = len(chars)
    if n < 3:
        return text

    target_mutations = max(1, int(n * rate))
    indices = rng.sample(range(n), min(target_mutations, n))

    for idx in indices:
        char = chars[idx].lower()
        if char in _KEYBOARD_NEIGHBORS:
            # Substitute with adjacent keyboard key
            chars[idx] = rng.choice(_KEYBOARD_NEIGHBORS[char])
        elif char.isalpha() and rng.random() < 0.3:
            # Drop character
            chars[idx] = ""

    return "".join(chars)


def generate_informal_colloquial(text: str, *, seed: int = 42) -> str:
    """Transform text into informal/colloquial styling."""
    rng = random.Random(seed)
    transformed = text.lower()
    replacements = [
        (r"\bplease\b", "pls"),
        (r"\bwant to\b", "wanna"),
        (r"\bneed to\b", "gotta"),
        (r"\bgive me\b", "gimme"),
        (r"\bwould like to\b", "wanna"),
        (r"\bfor example\b", "e.g."),
        (r"\bmemory\b", "ram"),
        (r"\bprocessor\b", "cpu"),
        (r"\bdataset\b", "data"),
    ]
    for pattern, repl in replacements:
        transformed = re.sub(pattern, repl, transformed, flags=re.IGNORECASE)

    # Strip final punctuation
    transformed = transformed.rstrip(".!?;")
    prefix = rng.choice(["yo ", "hey ", "can u ", "need help with ", ""])
    return prefix + transformed


def generate_paraphrase_no_keywords(text: str) -> str:
    """Generate paraphrase by replacing obvious library/tool keywords with descriptions."""
    result = text
    for pattern, repl in _KEYWORD_REPLACEMENTS.items():
        result = re.sub(pattern, repl, result, flags=re.IGNORECASE)
    return result


def generate_irrelevant_context(text: str, *, seed: int = 42) -> str:
    """Wrap text with extraneous conversational context."""
    rng = random.Random(seed)
    prefix = rng.choice(_EXTRA_CONTEXT_PREFIXES)
    suffix = rng.choice(_EXTRA_CONTEXT_SUFFIXES)
    return f"{prefix}{text}{suffix}"


def generate_code_context_variant(
    intent: str,
    capabilities: Sequence[str],
    *,
    seed: int = 42,
) -> tuple[str, tuple[str, ...]]:
    """Split explicit intent into concise text + code context hints."""
    code_lines: list[str] = []
    text = intent

    for cap in capabilities:
        cap_clean = cap.lower()
        if cap_clean in ("pandas", "data_science"):
            code_lines.append("import pandas as pd")
            code_lines.append("df = pd.read_csv('data.csv')")
            text = re.sub(r"\bpandas\b", "the loaded dataframe", text, flags=re.IGNORECASE)
        elif cap_clean in ("pytorch", "deep_learning"):
            code_lines.append("import torch")
            code_lines.append("import torch.nn as nn")
            code_lines.append("device = 'cuda' if torch.cuda.is_available() else 'cpu'")
            text = re.sub(r"\bpytorch\b", "the model architecture", text, flags=re.IGNORECASE)
        elif cap_clean in ("scipy", "scientific"):
            code_lines.append("import scipy.stats as stats")
            code_lines.append("import numpy as np")
        elif cap_clean in ("r", "r_lang"):
            code_lines.append("library(tidyverse)")
            code_lines.append("df <- read_csv('data.csv')")

    if not code_lines:
        code_lines = [
            "# Setup analysis environment",
            "import os, sys",
            "print('Starting execution')",
        ]

    return text, tuple(code_lines)


def generate_ambiguity_variant(intent: str, *, seed: int = 42) -> str:
    """Create intentional ambiguity or contradictory signal."""
    rng = random.Random(seed)
    contradictions = (
        " (or maybe something super lightweight like basic python if that fits, not sure)",
        " but also make sure it uses maximum GPU acceleration and at the same time zero GPU quota",
        " either a quick 5-minute script or a distributed 100-node training cluster",
        " please assign minimal small container but with 128GB memory",
    )
    return intent.rstrip(". ") + rng.choice(contradictions) + "."


def generate_draft_variant(
    family: RobustnessFamily,
    perturbation_class: PerturbationClass,
    *,
    variant_id: str | None = None,
    language: str = "en",
    seed: int = 42,
    custom_transform: Callable[[str], str] | None = None,
) -> RobustnessVariant:
    """Generate a single draft perturbation variant for a family with safety enforcement.

    All generated variants are stamped GENERATED_DRAFT and PENDING_REVIEW.
    """
    # Check confirmatory / frozen guards
    if family.source_provenance is not None:
        source_split = family.source_provenance.get("source_split") or family.source_provenance.get("role")
        if str(source_split).lower() == "confirmatory":
            raise PermissionError(
                f"Paraphrase generation is strictly prohibited on confirmatory family {family.family_id!r}."
            )

    canonical = family.canonical_variant
    base_text = canonical.intent
    code_context: tuple[str, ...] = canonical.code_context
    differences: str | None = None
    expected_equiv = EquivalenceStatus.PENDING_REVIEW

    if perturbation_class == PerturbationClass.CANONICAL:
        intent = base_text
        equiv_status = EquivalenceStatus.CANONICAL_REFERENCE
    elif perturbation_class == PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS:
        intent = generate_paraphrase_no_keywords(base_text)
        differences = "Keyword removal: domain tools replaced with functional descriptions"
    elif perturbation_class == PerturbationClass.INFORMAL_COLLOQUIAL:
        intent = generate_informal_colloquial(base_text, seed=seed)
        differences = "Informal colloquial styling and slang"
    elif perturbation_class == PerturbationClass.TYPO_NOISE:
        intent = inject_typo_noise(base_text, seed=seed)
        differences = "Synthetic keyboard typo mutations"
    elif perturbation_class == PerturbationClass.IRRELEVANT_EXTRA_CONTEXT:
        intent = generate_irrelevant_context(base_text, seed=seed)
        differences = "Extraneous conversational prefix and suffix"
    elif perturbation_class == PerturbationClass.REQUIREMENT_EXPRESSED_IN_CODE_CONTEXT:
        req_caps = tuple(family.image_gold.get("required_capabilities", ()))
        intent, code_context = generate_code_context_variant(
            base_text, req_caps, seed=seed
        )
        differences = "Requirements shifted from natural prompt to code context hints"
    elif perturbation_class == PerturbationClass.AMBIGUOUS_OR_CONFLICTING_SIGNAL:
        intent = generate_ambiguity_variant(base_text, seed=seed)
        differences = "Intentional ambiguity or conflicting resource signal"
        expected_equiv = EquivalenceStatus.CONTROLLED_AMBIGUITY
    elif perturbation_class == PerturbationClass.VIETNAMESE:
        # Template draft in Vietnamese
        intent = f"[Bản nháp tiếng Việt] {base_text}"
        language = "vi"
        differences = "Vietnamese language translation draft"
    else:
        if custom_transform is not None:
            intent = custom_transform(base_text)
        else:
            intent = f"[Draft {perturbation_class.value}] {base_text}"
        differences = f"Custom perturbation: {perturbation_class.value}"

    if variant_id is None:
        safe_suffix = perturbation_class.value.replace("_", "-")
        variant_id = f"{family.family_id}-draft-{safe_suffix}-{seed}"

    notes_list = (
        "AI/Rule-generated draft; requires human approval before evaluation.",
        "generator_id: protocol-v5-robustness-draft-generator-v1.0.0",
        "template_version: v1.0.0",
        f"seed: {seed}",
        f"source_canonical_id: {canonical.variant_id}",
    )

    meta = VariantMetadata(
        variant_type=perturbation_class,
        language=language,
        source=VariantSource.GENERATED_DRAFT,
        human_review_status=HumanReviewStatus.PENDING,
        equivalence_status=expected_equiv,
        expected_semantic_differences=differences,
        notes=notes_list,
    )

    return RobustnessVariant(
        variant_id=variant_id,
        family_id=family.family_id,
        intent=intent,
        code_context=code_context,
        metadata=meta,
        dataset_size_gb=canonical.dataset_size_gb,
    )


def generate_family_drafts(
    family: RobustnessFamily,
    *,
    classes: Sequence[PerturbationClass] | None = None,
    seed: int = 42,
) -> tuple[RobustnessVariant, ...]:
    """Generate draft variants across selected perturbation classes for a family."""
    target_classes = (
        classes
        if classes is not None
        else (
            PerturbationClass.PARAPHRASE_WITHOUT_OBVIOUS_KEYWORDS,
            PerturbationClass.VIETNAMESE,
            PerturbationClass.INFORMAL_COLLOQUIAL,
            PerturbationClass.TYPO_NOISE,
            PerturbationClass.IRRELEVANT_EXTRA_CONTEXT,
            PerturbationClass.REQUIREMENT_EXPRESSED_IN_CODE_CONTEXT,
            PerturbationClass.AMBIGUOUS_OR_CONFLICTING_SIGNAL,
        )
    )
    drafts: list[RobustnessVariant] = []
    for idx, pclass in enumerate(target_classes):
        variant = generate_draft_variant(
            family,
            pclass,
            seed=seed + idx * 10,
        )
        drafts.append(variant)
    return tuple(drafts)


__all__ = [
    "ParaphraseGeneratorError",
    "generate_ambiguity_variant",
    "generate_code_context_variant",
    "generate_draft_variant",
    "generate_family_drafts",
    "generate_informal_colloquial",
    "generate_irrelevant_context",
    "generate_paraphrase_no_keywords",
    "inject_typo_noise",
]
