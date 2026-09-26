"""Chat clients for local LLM runtimes: native Ollama and OpenAI-compatible servers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

import httpx

from archdiag.config import Settings

Message = dict[str, str]

_CONNECT_TIMEOUT_S = 5.0
_LIST_TIMEOUT_S = 10.0


class LLMError(Exception):
    """The LLM call failed (CLI exit code 1)."""


class LLMUnavailable(LLMError):
    """The runtime or the model is not there (CLI exit code 2)."""


class LLMTimeout(LLMError):
    """The runtime did not answer in time."""


class ChatClient(Protocol):
    def chat(self, messages: Sequence[Message], schema: dict[str, Any]) -> str: ...

    def list_models(self) -> dict[str, int]: ...


class _HttpClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self._http = httpx.Client(
            base_url=settings.endpoint.rstrip("/"),
            timeout=httpx.Timeout(settings.timeout_s, connect=_CONNECT_TIMEOUT_S),
            transport=transport,
        )

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        endpoint = self.settings.endpoint
        try:
            response = self._http.request(
                method, path, json=body, timeout=timeout if timeout else httpx.USE_CLIENT_DEFAULT
            )
        except httpx.ConnectError as exc:
            raise LLMUnavailable(
                f"Cannot reach the LLM runtime at {endpoint}. Is Ollama running? "
                "Start it with `ollama serve` or `brew services start ollama`."
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"The LLM runtime at {endpoint} did not answer in time.") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Request to {endpoint}{path} failed: {exc}") from exc
        if response.status_code == 404 and "not found" in response.text.lower():
            raise LLMUnavailable(
                f"Model '{self.settings.model}' is not available at {endpoint}. "
                f"Run: ollama pull {self.settings.model}"
            )
        if response.status_code >= 400:
            raise LLMError(
                f"{path} returned HTTP {response.status_code}: {response.text[:300]}"
            )
        try:
            data: dict[str, Any] = response.json()
        except ValueError as exc:
            raise LLMError(f"{path} returned a non-JSON response") from exc
        return data


class OllamaClient(_HttpClient):
    """Native Ollama API: the only one that accepts num_ctx, so prompts are not cut silently."""

    def chat(self, messages: Sequence[Message], schema: dict[str, Any]) -> str:
        data = self._request("POST", "/api/chat", {
            "model": self.settings.model,
            "messages": list(messages),
            "stream": False,
            "format": schema,
            "options": {
                "num_ctx": self.settings.num_ctx,
                "temperature": self.settings.temperature,
                "seed": self.settings.seed,
            },
        })
        try:
            return str(data["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise LLMError("Ollama returned an unexpected chat payload") from exc

    def list_models(self) -> dict[str, int]:
        data = self._request("GET", "/api/tags", timeout=_LIST_TIMEOUT_S)
        return {str(m["name"]): int(m.get("size", 0)) for m in data.get("models", [])}


class OpenAICompatClient(_HttpClient):
    """LM Studio, llama.cpp server and other /v1 servers."""

    def chat(self, messages: Sequence[Message], schema: dict[str, Any]) -> str:
        data = self._request("POST", "/chat/completions", {
            "model": self.settings.model,
            "messages": list(messages),
            "temperature": self.settings.temperature,
            "seed": self.settings.seed,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "arch_model", "schema": schema},
            },
        })
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("The server returned an unexpected chat payload") from exc

    def list_models(self) -> dict[str, int]:
        data = self._request("GET", "/models", timeout=_LIST_TIMEOUT_S)
        return {str(m["id"]): 0 for m in data.get("data", [])}


def make_client(settings: Settings) -> ChatClient:
    if settings.api == "openai":
        return OpenAICompatClient(settings)
    return OllamaClient(settings)
