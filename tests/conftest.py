"""Test fixtures: temp data dir + mock providers (no Ollama/TTS needed).

NLM_DATA_DIR is set BEFORE any app import so config.py, the SQLite DB, and
the LanceDB connection all land in a throwaway directory.
"""
import hashlib
import json
import os
import struct
import tempfile

_TMP = tempfile.mkdtemp(prefix="skullmaster-test-")
os.environ["NLM_DATA_DIR"] = _TMP

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, main, rag, store, studio  # noqa: E402


class MockLLM:
    """Deterministic offline stand-in for the Ollama provider."""

    def __init__(self):
        self.fail_embeds = False
        self.chat_model = "mock-chat"
        self.embed_model = "mock-embed"

    def _vec(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 for b in h[:8]]

    def embed(self, texts):
        if self.fail_embeds:
            raise RuntimeError("mock embedding backend down")
        return [self._vec(t) for t in texts]

    def chat(self, messages, stream=False):
        system = messages[0]["content"] if messages else ""
        if stream:
            user = messages[-1]["content"]
            if "No relevant source excerpts" in user:
                reply = "I couldn't find this in your sources."
            else:
                reply = "The answer is in the sources [1]. Bogus claim [9]."
            return iter([reply[: len(reply) // 2], reply[len(reply) // 2:]])
        # non-stream call == podcast script generation
        assert "podcast" in system.lower() or "host" in system.lower()
        lines = [{"speaker": "A" if i % 2 == 0 else "B",
                  "text": f"Line {i} about the sources."} for i in range(6)]
        return json.dumps({"title": "Mock Overview", "lines": lines})

    def ensure_models(self):
        return {self.chat_model: "ready", self.embed_model: "ready"}

    def status(self):
        return {
            "backend": "mock", "base_url": "local", "reachable": True,
            "chat_model": self.chat_model, "chat_model_ready": True,
            "embed_model": self.embed_model, "embed_model_ready": True,
        }


class MockTTS:
    def synthesize(self, text, voice):
        rate = 8000
        pcm = struct.pack(f"<{rate // 10}h", *([500] * (rate // 10)))  # 0.1s tone
        return pcm, rate

    def status(self):
        return {"backend": "mock-tts", "ready": True, "detail": "mock"}


@pytest.fixture()
def mock_llm(monkeypatch):
    llm = MockLLM()
    tts = MockTTS()
    for mod in (main, rag, store, studio):
        if hasattr(mod, "get_llm"):
            monkeypatch.setattr(mod, "get_llm", lambda llm=llm: llm)
        if hasattr(mod, "get_tts"):
            monkeypatch.setattr(mod, "get_tts", lambda tts=tts: tts)
    return llm


@pytest.fixture()
def client(mock_llm):
    db.init_db()
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def notebook(client):
    return client.post("/api/notebooks", json={"name": "Test NB"}).json()
