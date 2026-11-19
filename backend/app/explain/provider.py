"""Gemini adapter over REST (generateContent with a JSON response schema)."""

from __future__ import annotations

from typing import Protocol

import httpx

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class ProviderError(Exception):
    """Timeout, transport failure, non-200 status or an unusable response."""


class Provider(Protocol):
    model: str

    def generate(self, system: str, user: str, schema: dict, timeout: float) -> str: ...


class GeminiProvider:
    def __init__(self, api_key: str, model: str, client: httpx.Client | None = None) -> None:
        self.api_key, self.model = api_key, model
        self._client = client or httpx.Client()

    def _generation_config(self, schema: dict) -> dict:
        config: dict = {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
            "maxOutputTokens": 2048,
        }
        if self.model.startswith("gemini-3"):
            # Gemini 3: keep the default temperature (1.0); lower values can loop.
            config["thinkingConfig"] = {"thinkingLevel": "low"}
        else:
            config["temperature"] = 0.2
        return config

    def generate(self, system: str, user: str, schema: dict, timeout: float) -> str:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": self._generation_config(schema),
        }
        try:
            r = self._client.post(
                GEMINI_URL.format(model=self.model),
                json=body,
                headers={"x-goog-api-key": self.api_key},
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"transport: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise ProviderError(f"status {r.status_code}")
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("unexpected response shape") from exc
        if not text.strip():
            raise ProviderError("empty response")
        return text
