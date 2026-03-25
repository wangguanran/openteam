"""Provider auto-detection from model name prefix.

Model naming convention:
    openrouter/openai/gpt-4.1       → provider=openrouter
    openai/gpt-4.1                  → provider=openai (direct)
    anthropic/claude-sonnet-4       → provider=anthropic (direct)
    google/gemini-2.5-flash         → provider=google
    mistral/mistral-large           → provider=mistral
"""
from __future__ import annotations

from dataclasses import dataclass

_REASONING_MODEL_TOKENS = ("o1", "o3", "o4")


@dataclass(frozen=True)
class ProviderConfig:
    """Provider-specific LLM parameters."""

    name: str
    litellm: bool = True
    api_mode: str = "chat"  # "chat" | "responses" | "messages"
    supports_reasoning: bool = False
    default_base_url: str = ""


_PROVIDERS: dict[str, ProviderConfig] = {
    "openrouter": ProviderConfig(
        name="openrouter",
        litellm=True,
        api_mode="chat",
        supports_reasoning=False,
        default_base_url="https://openrouter.ai/api/v1",
    ),
    "openai": ProviderConfig(
        name="openai",
        litellm=False,
        api_mode="responses",
        supports_reasoning=True,
    ),
    "anthropic": ProviderConfig(
        name="anthropic",
        litellm=True,
        api_mode="messages",
        supports_reasoning=False,
    ),
    "google": ProviderConfig(
        name="google",
        litellm=True,
        api_mode="chat",
        supports_reasoning=False,
    ),
}

_DEFAULT = ProviderConfig(name="unknown", litellm=True, api_mode="chat")


def detect_provider(model: str) -> ProviderConfig:
    """Detect provider from model name prefix (first segment before '/')."""
    normalized = str(model or "").strip().lower()
    if not normalized or "/" not in normalized:
        return ProviderConfig(name=normalized or "unknown", litellm=True, api_mode="chat")
    prefix = normalized.split("/", 1)[0]
    base = _PROVIDERS.get(prefix, ProviderConfig(name=prefix, litellm=True, api_mode="chat"))
    if base.supports_reasoning and is_reasoning_model(model):
        return base
    if base.supports_reasoning:
        return ProviderConfig(
            name=base.name,
            litellm=base.litellm,
            api_mode=base.api_mode,
            supports_reasoning=False,
            default_base_url=base.default_base_url,
        )
    return base


def is_reasoning_model(model: str) -> bool:
    """Check if model is a reasoning model (o1, o3, o4 series) under a direct provider."""
    normalized = str(model or "").strip().lower()
    parts = normalized.split("/")
    if len(parts) < 2:
        return False
    provider = parts[0]
    if provider != "openai":
        return False
    model_name = parts[-1]
    return any(model_name.startswith(token) for token in _REASONING_MODEL_TOKENS)
