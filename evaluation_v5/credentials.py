"""Interactive LLM credentials and environment resolver for evaluation protocols."""

from __future__ import annotations

import os
import re
import sys
from typing import Mapping


DEFAULT_LLM_ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_LLM_MODEL = "gpt-4o-mini"


def sanitize_model_slug(model: str) -> str:
    """Convert an arbitrary model identifier into a clean filesystem directory slug."""
    cleaned = model.strip().lower()
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or "default-model"


def get_mode_tag(mode: str, model: str | None = None) -> str:
    """Return directory tag suffix for the given mode and model.

    Examples:
      - mode='local' -> 'local'
      - mode='llm', model='gpt-4o-mini' -> 'llm-gpt-4o-mini'
      - mode='llm', model='meta-llama/Llama-3.1-8B-Instruct' -> 'llm-meta-llama-llama-3.1-8b-instruct'
    """
    mode_clean = mode.lower().strip()
    if mode_clean != "llm":
        return "local"
    resolved_model = model or os.environ.get("EXTERNAL_LLM_MODEL", DEFAULT_LLM_MODEL)
    slug = sanitize_model_slug(resolved_model)
    return f"llm-{slug}"


def ensure_llm_credentials(
    *,
    interactive: bool | None = None,
    default_endpoint: str = DEFAULT_LLM_ENDPOINT,
    default_model: str = DEFAULT_LLM_MODEL,
) -> dict[str, str]:
    """Ensure required external LLM configuration variables are set in os.environ.

    If variables are missing and execution is interactive (TTY), prompts the user
    in real-time to enter them.
    """
    is_interactive = sys.stdin.isatty() if interactive is None else interactive

    endpoint = os.environ.get("EXTERNAL_LLM_ENDPOINT", "").strip()
    model = os.environ.get("EXTERNAL_LLM_MODEL", "").strip()
    api_key = os.environ.get("EXTERNAL_LLM_API_KEY", "").strip()

    if not endpoint:
        if is_interactive:
            print(f"\n🌐 [LLM Config] EXTERNAL_LLM_ENDPOINT is not set.")
            user_ep = input(f"   Enter LLM endpoint [Default: {default_endpoint}]: ").strip()
            endpoint = user_ep if user_ep else default_endpoint
        else:
            endpoint = default_endpoint
        os.environ["EXTERNAL_LLM_ENDPOINT"] = endpoint

    if endpoint.startswith("http://"):
        os.environ["EXTERNAL_LLM_ALLOW_INSECURE_HTTP"] = "true"

    if not model:
        if is_interactive:
            print(f"🤖 [LLM Config] EXTERNAL_LLM_MODEL is not set.")
            user_model = input(f"   Enter model name [Default: {default_model}]: ").strip()
            model = user_model if user_model else default_model
        else:
            model = default_model
        os.environ["EXTERNAL_LLM_MODEL"] = model

    if not api_key:
        if is_interactive:
            print(f"🔑 [LLM Config] EXTERNAL_LLM_API_KEY is not set.")
            user_key = input("   Please enter / paste LLM API Key (press Enter if using local Ollama): ").strip()
            if not user_key:
                if "localhost" in endpoint or "127.0.0.1" in endpoint or "ollama" in endpoint:
                    user_key = "ollama"
                else:
                    user_key = "dummy-key"
            api_key = user_key
        else:
            raise ValueError(
                "EXTERNAL_LLM_API_KEY is required in os.environ when running in LLM extractor mode non-interactively."
            )
        os.environ["EXTERNAL_LLM_API_KEY"] = api_key

    # Mark P2 extractor mode as llm
    os.environ["P2_STRUCTURED_EXTRACTOR"] = "llm"

    return {
        "endpoint": endpoint,
        "model": model,
        "api_key": api_key,
    }


def configure_extractor_mode(
    mode: str,
    *,
    interactive: bool | None = None,
) -> str:
    """Configure P2 extractor mode ('local' or 'llm') and return the validated mode."""
    mode_clean = mode.lower().strip()
    if mode_clean not in {"local", "llm"}:
        raise ValueError(f"Unsupported extractor mode: {mode!r}. Must be 'local' or 'llm'.")

    if mode_clean == "llm":
        ensure_llm_credentials(interactive=interactive)
    else:
        os.environ["P2_STRUCTURED_EXTRACTOR"] = "local"

    return mode_clean
