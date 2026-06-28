"""LangChain chat-model factory — provider-agnostic.

Every agent that makes LLM calls uses get_llm_client() with its own
model/provider settings instead of sharing a single hardcoded instance.
This lets different agents run different models (e.g. GPT-4o for extraction,
GPT-4o-mini for classification) or even different providers (OpenAI, Anthropic,
Google) without any code change — only environment-variable / .env configuration.

Supported providers and the corresponding LangChain package:
  openai    → langchain-openai    (ChatOpenAI)
  anthropic → langchain-anthropic (ChatAnthropic)
  google    → langchain-google-genai (ChatGoogleGenerativeAI)

API keys are read from common.config (which reads them from .env / environment)
and passed explicitly to each LangChain integration.  Passing None falls back
to the provider's own standard env-var lookup (OPENAI_API_KEY, etc.), which
works in production environments where keys are injected directly into the shell.
"""

from __future__ import annotations

import logging
from typing import Optional

from langchain_core.language_models import BaseChatModel

log = logging.getLogger(__name__)


def get_llm_client(
    model: str = "gpt-4o-mini",
    provider: str = "openai",
    temperature: float = 0.0,
) -> BaseChatModel:
    """Return a LangChain chat model for the given provider and model.

    API keys are sourced from common.config (which reads .env) so callers
    never need to handle credentials — they only configure model and provider.

    Args:
        model: Model identifier (e.g. "gpt-4o", "claude-3-5-sonnet-20241022",
               "gemini-1.5-pro").  Provider-specific identifier.
        provider: One of "openai", "anthropic", "google".
        temperature: Passed through to the model.  Default 0.0 for all
               classification/extraction calls in this pipeline.

    Returns:
        A LangChain BaseChatModel.  Call .with_structured_output(Schema) and
        .with_retry() at the call site — those are schema-specific, not global.

    Raises:
        ValueError: If provider is not one of the supported values.
    """
    # Import here (lazy) to avoid importing common.config at module level,
    # which would create a circular-import risk if common modules import each other.
    from common.config import settings as _common

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=_common.OPENAI_API_KEY or None,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(  # type: ignore[call-arg]
            model=model,
            temperature=temperature,
            api_key=_common.ANTHROPIC_API_KEY or None,
        )

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            google_api_key=_common.GOOGLE_API_KEY or None,
        )

    raise ValueError(
        f"Unknown LLM provider {provider!r}. "
        "Supported values: 'openai', 'anthropic', 'google'."
    )
