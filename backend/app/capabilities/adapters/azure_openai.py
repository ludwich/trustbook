"""Azure OpenAI chat adapter (deployment-based URLs, ``api-key`` header)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.capabilities.adapters.openai_compatible import OpenAICompatibleAdapter
from app.capabilities.http import OutboundHttp


class AzureOpenAIAdapter(OpenAICompatibleAdapter):
    """``POST {base}/openai/deployments/{deployment}/chat/completions?api-version=…``."""

    def __init__(
        self,
        http: OutboundHttp,
        *,
        capability: str,
        base_url: str,
        deployment: str,
        api_version: str,
        api_key: str | None,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(
            http,
            capability=capability,
            base_url=base_url,
            model=deployment,
            api_key=api_key,
            timeout=timeout,
        )
        self._api_version = api_version

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["api-key"] = self._api_key
        return headers

    def _chat_url(self) -> str:
        deployment = quote(self._model, safe="")
        version = quote(self._api_version, safe="")
        return (
            f"{self._base_url}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={version}"
        )

    def _base_body(self) -> dict[str, Any]:
        return {}

    def list_models(self) -> list[str]:
        """Azure deployments cannot be listed with an API key; the user types the name."""
        return []
