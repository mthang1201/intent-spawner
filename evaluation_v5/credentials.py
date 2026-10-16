"""Interactive LLM credentials and environment resolver for evaluation protocols."""

from __future__ import annotations

import os
import re
import sys
from typing import Mapping


DEFAULT_LLM_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
DEFAULT_LLM_MODEL = "gemini-3.8-flash"


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
      - mode='llm', model='gemini-3.8-flash' -> 'llm-gemini-3.8-flash'
      - mode='llm', model='gpt-4o-mini' -> 'llm-gpt-4o-mini'
      - mode='llm', model='meta-llama/Llama-3.1-8B-Instruct' -> 'llm-meta-llama-llama-3.1-8b-instruct'
    """
    mode_clean = mode.lower().strip()
    if mode_clean != "llm":
        return "local"
    resolved_model = model or os.environ.get("EXTERNAL_LLM_MODEL", DEFAULT_LLM_MODEL)
    slug = sanitize_model_slug(resolved_model)
    return f"llm-{slug}"


PRESET_PROVIDERS = [
    {
        "name": "Google Gemini (OpenAI-compatible)",
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "default_model": "gemini-3.8-flash",
        "suggested_models": [
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-3.5-flash",
            "gemini-3.1-pro",
            "gemini-3.1-flash-lite",
        ],
    },
    {
        "name": "OpenAI (ChatGPT)",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-4o-mini",
        "suggested_models": ["gpt-4o-mini", "gpt-4o"],
    },
    {
        "name": "Anthropic Claude (OpenAI-compatible proxy/bridge)",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "default_model": "claude-3-5-sonnet-20241022",
        "suggested_models": ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"],
    },
]


def verify_llm_connection(endpoint: str, model: str, api_key: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Send a lightweight test prompt to verify endpoint, model, and API key."""
    import json
    import urllib.error
    import urllib.request

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Ping. Respond with 'ok'"}
        ],
        "max_tokens": 5,
        "temperature": 0.0,
    }

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                return True, "Connection successful"
            return False, f"Unexpected HTTP status {response.status}"
    except urllib.error.HTTPError as exc:
        err_msg = exc.read().decode("utf-8", errors="replace")
        if exc.code == 401:
            return False, f"HTTP 401 Unauthorized (Invalid or missing API key): {err_msg[:200]}"
        if exc.code == 404:
            return False, f"HTTP 404 Not Found (Invalid endpoint or model name '{model}'): {err_msg[:200]}"
        return False, f"HTTP {exc.code} error: {err_msg[:200]}"
    except urllib.error.URLError as exc:
        return False, f"Network error connecting to {endpoint}: {exc.reason}"
    except Exception as exc:
        return False, f"Connection error: {exc}"


def ensure_llm_credentials(
    *,
    interactive: bool | None = None,
    default_endpoint: str = DEFAULT_LLM_ENDPOINT,
    default_model: str = DEFAULT_LLM_MODEL,
) -> dict[str, str]:
    """Ensure required external LLM configuration variables are set in os.environ.

    If variables are missing and execution is interactive (TTY), prompts the user
    with presets and recommendations for popular LLM providers, followed by a connection probe.
    """
    is_interactive = sys.stdin.isatty() if interactive is None else interactive

    endpoint = os.environ.get("EXTERNAL_LLM_ENDPOINT", "").strip()
    model = os.environ.get("EXTERNAL_LLM_MODEL", "").strip()
    api_key = os.environ.get("EXTERNAL_LLM_API_KEY", "").strip()

    matched_preset = None

    if not endpoint:
        if is_interactive:
            print("\n🌐 [LLM Config] EXTERNAL_LLM_ENDPOINT is not set.")
            print("   Available popular endpoints:")
            for idx, p in enumerate(PRESET_PROVIDERS, 1):
                print(f"     [{idx}] {p['name']}: {p['endpoint']}")
            print(f"     [{len(PRESET_PROVIDERS) + 1}] Custom endpoint")

            choice = input(f"\n   Choose provider [1-{len(PRESET_PROVIDERS) + 1}] [Default: 1]: ").strip()
            custom_idx = str(len(PRESET_PROVIDERS) + 1)
            if choice.isdigit() and 1 <= int(choice) <= len(PRESET_PROVIDERS):
                matched_preset = PRESET_PROVIDERS[int(choice) - 1]
                endpoint = matched_preset["endpoint"]
                if not model:
                    default_model = matched_preset["default_model"]
            elif choice == custom_idx or choice.lower() == "c":
                user_ep = input(f"   Enter custom LLM endpoint: ").strip()
                endpoint = user_ep if user_ep else default_endpoint
            else:
                matched_preset = PRESET_PROVIDERS[0]
                endpoint = matched_preset["endpoint"]
                if not model:
                    default_model = matched_preset["default_model"]
        else:
            endpoint = default_endpoint
        os.environ["EXTERNAL_LLM_ENDPOINT"] = endpoint
    else:
        for p in PRESET_PROVIDERS:
            if p["endpoint"].lower() == endpoint.lower():
                matched_preset = p
                break

    if endpoint.startswith("http://"):
        os.environ["EXTERNAL_LLM_ALLOW_INSECURE_HTTP"] = "true"

    if not model:
        if is_interactive:
            print("\n🤖 [LLM Config] EXTERNAL_LLM_MODEL is not set.")
            if matched_preset and matched_preset.get("suggested_models"):
                models_list = ", ".join(matched_preset["suggested_models"])
                print(f"   Suggested models for {matched_preset['name']}: {models_list}")
            user_model = input(f"   Enter model name [Default: {default_model}]: ").strip()
            model = user_model if user_model else default_model
        else:
            model = default_model
        os.environ["EXTERNAL_LLM_MODEL"] = model

    while not api_key:
        if is_interactive:
            print("\n🔑 [LLM Config] EXTERNAL_LLM_API_KEY is not set.")
            user_key = input("   Please enter / paste your API Key: ").strip()
            if not user_key:
                print("   ⚠️  API key cannot be empty when using LLM mode.")
                continue
            api_key = user_key
        else:
            raise ValueError(
                "EXTERNAL_LLM_API_KEY is required in os.environ when running in LLM extractor mode non-interactively."
            )

    os.environ["EXTERNAL_LLM_API_KEY"] = api_key

    # Preflight connectivity check
    if is_interactive:
        print(f"\n🔍 [Health Check] Verifying connection to {endpoint} with model '{model}'...")
        success, message = verify_llm_connection(endpoint, model, api_key)
        if success:
            print("   ✅ Connection verified successfully!\n")
        else:
            print(f"   ❌ Connection failed: {message}")
            retry = input("   Continue anyway? (y/[N]): ").strip().lower()
            if retry != "y":
                print("   Aborted. Please verify your API Key and endpoint.")
                sys.exit(1)

    # Mark P2 extractor mode as llm
    os.environ["P2_STRUCTURED_EXTRACTOR"] = "llm"

    # Rate-limiting guards for Free Tier quotas (RPM limit protection)
    # 1. Delay between consecutive test requests: 4.2s (guarantees strictly <= 13-14 RPM even if network latency is near zero)
    if "EXTERNAL_LLM_PACING_SLEEP_SECONDS" not in os.environ:
        os.environ["EXTERNAL_LLM_PACING_SLEEP_SECONDS"] = "4.2"
    # 2. Retries and exponential backoff on transient 429 errors
    if "EXTERNAL_LLM_MAX_RETRIES" not in os.environ:
        os.environ["EXTERNAL_LLM_MAX_RETRIES"] = "5"
    if "EXTERNAL_LLM_RETRY_BACKOFF_SECONDS" not in os.environ:
        os.environ["EXTERNAL_LLM_RETRY_BACKOFF_SECONDS"] = "5.0"

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
