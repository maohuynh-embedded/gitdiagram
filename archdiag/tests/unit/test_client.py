import json
from collections.abc import Callable

import httpx
import pytest

from archdiag.config import Settings
from archdiag.llm.client import (
    LLMError,
    LLMTimeout,
    LLMUnavailable,
    OllamaClient,
    OpenAICompatClient,
    make_client,
)

SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}}
MESSAGES = [{"role": "user", "content": "hi"}]


def transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_ollama_chat_sends_schema_and_context_options() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": '{"a":1}'}})

    client = OllamaClient(Settings(num_ctx=8192, seed=3), transport=transport(handler))
    assert client.chat(MESSAGES, SCHEMA) == '{"a":1}'
    body = seen["body"]
    assert isinstance(body, dict)
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert body["format"] == SCHEMA
    assert body["stream"] is False
    assert body["options"] == {"num_ctx": 8192, "temperature": 0.2, "seed": 3}
    assert body["messages"] == MESSAGES


def test_connection_error_means_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = OllamaClient(Settings(), transport=transport(handler))
    with pytest.raises(LLMUnavailable, match="ollama serve"):
        client.chat(MESSAGES, SCHEMA)


def test_missing_model_suggests_pull() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'qwen3-coder:30b' not found"})

    client = OllamaClient(Settings(), transport=transport(handler))
    with pytest.raises(LLMUnavailable, match="ollama pull qwen3-coder:30b"):
        client.chat(MESSAGES, SCHEMA)


def test_timeout_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = OllamaClient(Settings(), transport=transport(handler))
    with pytest.raises(LLMTimeout):
        client.chat(MESSAGES, SCHEMA)


def test_server_error_is_an_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = OllamaClient(Settings(), transport=transport(handler))
    with pytest.raises(LLMError, match="HTTP 500"):
        client.chat(MESSAGES, SCHEMA)


def test_ollama_list_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen3-coder:30b", "size": 19}]})

    client = OllamaClient(Settings(), transport=transport(handler))
    assert client.list_models() == {"qwen3-coder:30b": 19}


def test_openai_client_uses_json_schema_response_format() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    settings = Settings(api="openai", endpoint="http://localhost:1234/v1", model="m")
    client = OpenAICompatClient(settings, transport=transport(handler))
    assert client.chat(MESSAGES, SCHEMA) == "{}"
    body = seen["body"]
    assert isinstance(body, dict)
    assert seen["url"] == "http://localhost:1234/v1/chat/completions"
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "arch_model", "schema": SCHEMA},
    }
    assert body["model"] == "m"


def test_openai_list_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    settings = Settings(api="openai", endpoint="http://localhost:1234/v1")
    client = OpenAICompatClient(settings, transport=transport(handler))
    assert client.list_models() == {"local-model": 0}


def test_make_client_picks_the_api() -> None:
    assert isinstance(make_client(Settings()), OllamaClient)
    assert isinstance(make_client(Settings(api="openai")), OpenAICompatClient)
