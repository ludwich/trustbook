"""OpenAI-compatible speech-to-text endpoint (``/audio/transcriptions``)."""

from __future__ import annotations

import httpx

from app.capabilities.adapters.base import AdapterError, TranscriptionResult, parse_json, send
from app.capabilities.adapters.openai_compatible import OpenAICompatibleAdapter
from app.capabilities.http import EXT_EXCERPT, OutboundHttp, describe_size


class OpenAITranscriptionAdapter:
    """``POST {base}/audio/transcriptions`` (multipart: file + model)."""

    def __init__(
        self,
        http: OutboundHttp,
        *,
        capability: str,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self._http = http
        self._capability = capability
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key or None
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def transcribe(self, audio: bytes, filename: str = "audio.wav") -> TranscriptionResult:
        request = httpx.Request(
            "POST",
            f"{self._base_url}/audio/transcriptions",
            headers=self._headers(),
            files={"file": (filename, audio, "audio/wav")},
            data={"model": self._model, "response_format": "json"},
            extensions={EXT_EXCERPT: f"audio file, {describe_size(len(audio))}"},
        )
        with self._http.client(self._capability, timeout=self._timeout) as client:
            response = send(client, request, secret=self._api_key)
        data = parse_json(response, what="speech-to-text endpoint")
        if not isinstance(data, dict) or not isinstance(data.get("text"), str):
            raise AdapterError("bad_response", "The endpoint did not return a transcript.")
        duration = data.get("duration")
        return TranscriptionResult(
            text=data["text"].strip(),
            duration_s=float(duration) if isinstance(duration, int | float) else None,
        )

    def list_models(self) -> list[str]:
        lister = OpenAICompatibleAdapter(
            self._http,
            capability=self._capability,
            base_url=self._base_url,
            model=self._model,
            api_key=self._api_key,
        )
        return lister.list_models()
