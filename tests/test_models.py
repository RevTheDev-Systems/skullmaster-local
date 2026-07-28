"""Model listing, runtime chat-model switching, and thinking-mode fallback."""
import ollama
import pytest

from app import db, main
from app.providers.ollama_provider import OllamaProvider


def _provider(model: str, thinking_claimed: bool):
    """An OllamaProvider with a stubbed client (no live Ollama needed)."""
    p = OllamaProvider.__new__(OllamaProvider)
    p.chat_model = model
    p.embed_model = "embed"
    p._thinking = {model: thinking_claimed}
    return p


class _FakeClient:
    """Mimics Ollama: `think` is only rejected once the lazy stream is consumed."""

    def __init__(self, supports_thinking: bool, tokens=("hel", "lo"), fail_after=0):
        self.supports_thinking = supports_thinking
        self.tokens = tokens
        self.fail_after = fail_after
        self.think_flags = []

    def chat(self, model, messages, stream, **kwargs):
        think = kwargs.get("think", False)
        self.think_flags.append(think)

        def gen():
            if think and not self.supports_thinking:
                for t in self.tokens[: self.fail_after]:
                    yield {"message": {"content": t}}
                raise ollama.ResponseError(
                    f'"{model}" does not support thinking', 400)
            for t in self.tokens:
                yield {"message": {"content": t}}

        return gen()


def test_chat_falls_back_when_model_rejects_thinking():
    """Regression: the lazy stream made the old try/except unreachable, so any
    model without a thinking mode (phi4, llama3.1) failed outright."""
    p = _provider("phi4:latest", thinking_claimed=True)
    p.client = _FakeClient(supports_thinking=False)

    assert "".join(p._chat_stream([])) == "hello"
    assert p.client.think_flags == [True, False]   # retried without thinking
    assert p._thinking["phi4:latest"] is False     # and remembered


def test_thinking_model_is_not_retried():
    p = _provider("qwen3:30b", thinking_claimed=True)
    p.client = _FakeClient(supports_thinking=True)

    assert "".join(p._chat_stream([])) == "hello"
    assert p.client.think_flags == [True]          # single request


def test_no_retry_after_partial_output():
    """Retrying mid-stream would duplicate tokens, so the error must surface."""
    p = _provider("weird:latest", thinking_claimed=True)
    p.client = _FakeClient(supports_thinking=False, fail_after=1)

    with pytest.raises(ollama.ResponseError):
        list(p._chat_stream([]))
    assert p.client.think_flags == [True]


def test_models_listed_with_capabilities(client):
    body = client.get("/api/models").json()
    names = [m["name"] for m in body["models"]]
    assert names == sorted(names)                     # stable ordering for the picker
    assert body["chat_model"] == "mock-chat"
    chat_only = [m["name"] for m in body["models"] if m["can_chat"]]
    assert chat_only == ["mock-chat", "mock-chat-2"]  # embedding model excluded
    assert all({"name", "can_chat", "can_embed"} <= set(m) for m in body["models"])


def test_models_requires_auth(anon_client):
    assert anon_client.get("/api/models").status_code == 401
    assert anon_client.post("/api/models/chat", json={"name": "mock-chat"}).status_code == 401


def test_switch_chat_model_persists(client, mock_llm):
    res = client.post("/api/models/chat", json={"name": "mock-chat-2"})
    assert res.status_code == 200 and res.json()["chat_model"] == "mock-chat-2"

    assert mock_llm.chat_model == "mock-chat-2"                    # provider updated
    assert client.get("/api/models").json()["chat_model"] == "mock-chat-2"
    assert db.get_setting(main.CHAT_MODEL_SETTING) == "mock-chat-2"  # survives restart
    assert client.get("/health").json()["llm"]["chat_model"] == "mock-chat-2"

    client.post("/api/models/chat", json={"name": "mock-chat"})


def test_switch_rejects_unknown_and_non_chat_models(client, mock_llm):
    missing = client.post("/api/models/chat", json={"name": "not-installed"})
    assert missing.status_code == 404 and "not installed" in missing.json()["detail"]

    embed = client.post("/api/models/chat", json={"name": "mock-embed"})
    assert embed.status_code == 400 and "cannot generate chat" in embed.json()["detail"]

    assert mock_llm.chat_model == "mock-chat"  # unchanged by either rejection


def test_saved_model_restored_on_startup(client, mock_llm):
    """A model chosen in the UI is reapplied when the app restarts."""
    from fastapi.testclient import TestClient

    client.post("/api/models/chat", json={"name": "mock-chat-2"})
    mock_llm.set_chat_model("mock-chat")  # simulate a fresh process default

    with TestClient(main.app):            # lifespan runs and restores the setting
        assert mock_llm.chat_model == "mock-chat-2"

    client.post("/api/models/chat", json={"name": "mock-chat"})
