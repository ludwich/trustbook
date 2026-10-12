"""Build adapters from resolved configuration."""

from __future__ import annotations

from app.capabilities.adapters.anthropic import AnthropicAdapter
from app.capabilities.adapters.azure_openai import AzureOpenAIAdapter
from app.capabilities.adapters.base import (
    AdapterError,
    ChatAdapter,
    SearchAdapter,
    TranscriptionAdapter,
)
from app.capabilities.adapters.mock import MockChatAdapter, MockSearchAdapter
from app.capabilities.adapters.openai_compatible import OpenAICompatibleAdapter
from app.capabilities.adapters.search import BraveAdapter, SearxngAdapter, TavilyAdapter
from app.capabilities.adapters.stt_openai import OpenAITranscriptionAdapter
from app.capabilities.http import OutboundHttp
from app.capabilities.registry import EndpointConfig
from app.config import mock_llm_enabled


def _require(value: str | None, what: str) -> str:
    if not value:
        raise AdapterError("not_configured", f"The {what} is missing.")
    return value


def build_chat_adapter(
    endpoint: EndpointConfig, http: OutboundHttp, *, timeout: float = 60.0
) -> ChatAdapter:
    """Chat adapter for ``endpoint`` (OpenAI-compatible, Azure, Anthropic or mock)."""
    if endpoint.adapter == "mock":
        return MockChatAdapter()
    base_url = _require(endpoint.base_url, "address")
    model = _require(endpoint.model, "model name")
    if endpoint.adapter == "azure_openai":
        return AzureOpenAIAdapter(
            http,
            capability=endpoint.capability,
            base_url=base_url,
            deployment=model,
            api_version=_require(endpoint.api_version, "API version"),
            api_key=endpoint.api_key,
            timeout=timeout,
        )
    if endpoint.adapter == "anthropic":
        return AnthropicAdapter(
            http,
            capability=endpoint.capability,
            base_url=base_url,
            model=model,
            api_key=endpoint.api_key,
            timeout=timeout,
        )
    return OpenAICompatibleAdapter(
        http,
        capability=endpoint.capability,
        base_url=base_url,
        model=model,
        api_key=endpoint.api_key,
        timeout=timeout,
    )


def build_stt_adapter(
    endpoint: EndpointConfig, http: OutboundHttp, *, timeout: float = 300.0
) -> TranscriptionAdapter:
    """OpenAI-compatible transcription adapter for ``endpoint``."""
    return OpenAITranscriptionAdapter(
        http,
        capability=endpoint.capability,
        base_url=_require(endpoint.base_url, "address"),
        model=_require(endpoint.model, "model name"),
        api_key=endpoint.api_key,
        timeout=timeout,
    )


def build_search_adapter(
    provider: str, api_key: str | None, http: OutboundHttp, *, searxng_url: str
) -> SearchAdapter:
    """Search adapter for ``provider`` (searxng, brave, tavily; mock only in test mode)."""
    if provider == "mock":
        if not mock_llm_enabled():
            raise AdapterError("not_configured", "Choose a search service in Settings.")
        return MockSearchAdapter()
    if provider == "brave":
        return BraveAdapter(http, api_key=_require(api_key, "Brave Search API key"))
    if provider == "tavily":
        return TavilyAdapter(http, api_key=_require(api_key, "Tavily API key"))
    return SearxngAdapter(http, base_url=searxng_url)
