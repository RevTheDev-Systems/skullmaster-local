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
# Keep password hashing fast in tests; production uses the 600k default.
os.environ["SM_PBKDF2_ITERATIONS"] = "1000"

TEST_PASSWORD = "test-password-1234"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, ingest, main, rag, slides, store, studio, video  # noqa: E402


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
            return iter([reply[: len(reply) // 2], reply[len(reply) // 2 :]])
        # non-stream calls are Studio generations — dispatch on the prompt
        if "data-visualization" in system:
            return json.dumps(
                {
                    "title": "Mock Chart",
                    "type": "bar",
                    "x_label": "X",
                    "y_label": "Y",
                    "labels": ["A", "B", "C"],
                    "values": [1, 2, 3],
                    "source_note": "mock sources",
                }
            )
        if "information designer" in system:
            return json.dumps(
                {
                    "title": "Mock Infographic",
                    "subtitle": "sub",
                    "stats": [{"value": "42", "label": "answer"}],
                    "sections": [{"heading": "H", "points": ["p1", "p2"]}],
                    "source_note": "mock sources",
                }
            )
        if "knowledge cartographer" in system:
            return json.dumps(
                {
                    "title": "Mock Mind Graph",
                    "root": "Meridian Array",
                    "branches": [
                        {"label": "Output", "children": ["1.2 GW", "2340 GWh"]},
                        {"label": "Site", "children": ["Atacama Desert", "14 km²"]},
                        {"label": "Cost", "children": ["$940M"]},
                    ],
                    "links": [
                        {"from": "1.2 GW", "to": "$940M", "label": "drives"},
                        {"from": "ghost node", "to": "1.2 GW", "label": "invalid"},
                    ],
                    "source_note": "mock sources",
                }
            )
        if "data-extraction" in system:
            return json.dumps(
                {
                    "title": "Mock Table",
                    "columns": ["Name", "Value"],
                    "rows": [["a", 1], ["b", 2]],
                    "source_note": "mock sources",
                }
            )
        if "briefing document" in system:
            return json.dumps(
                {
                    "title": "Mock Briefing",
                    "sections": [{"heading": "H", "body": "Body."}],
                    "source_note": "mock sources",
                }
            )
        if "study guide" in system:
            return json.dumps(
                {
                    "title": "Mock Study Guide",
                    "objectives": ["o"],
                    "key_concepts": [{"term": "t", "definition": "d"}],
                    "questions": [{"q": "q", "a": "a"}],
                    "source_note": "mock sources",
                }
            )
        if "FAQ" in system:
            return json.dumps(
                {
                    "title": "Mock FAQ",
                    "items": [{"question": "q", "answer": "a"}],
                    "source_note": "mock sources",
                }
            )
        if "timeline" in system:
            return json.dumps(
                {
                    "title": "Mock Timeline",
                    "events": [{"date": "Q1", "event": "e"}],
                    "source_note": "mock sources",
                }
            )
        if "You summarize" in system:
            return json.dumps(
                {
                    "title": "Mock Summary",
                    "summary": "s",
                    "key_points": ["p"],
                    "source_note": "mock sources",
                }
            )
        if "slide deck" in system:
            return json.dumps(
                {
                    "title": "Mock Deck",
                    "slides": [
                        {
                            "title": f"Slide {i}",
                            "bullets": ["first point", "second point"],
                            "notes": "A short spoken line.",
                        }
                        for i in range(1, 5)
                    ],
                }
            )
        if "compare sources" in system:
            return json.dumps(
                {
                    "title": "Mock Comparison",
                    "topics": [
                        {
                            "topic": "Facts",
                            "positions": [{"source": "facts.txt", "stance": "agree", "claim": "c"}],
                        }
                    ],
                    "source_note": "mock sources",
                }
            )
        assert "podcast" in system.lower() or "host" in system.lower()
        lines = [
            {"speaker": "A" if i % 2 == 0 else "B", "text": f"Line {i} about the sources."}
            for i in range(6)
        ]
        return json.dumps({"title": "Mock Overview", "lines": lines})

    def ensure_models(self):
        return {self.chat_model: "ready", self.embed_model: "ready"}

    def list_models(self):
        return [
            {
                "name": "mock-chat",
                "size": 1,
                "parameter_size": "7B",
                "can_chat": True,
                "can_embed": False,
            },
            {
                "name": "mock-chat-2",
                "size": 2,
                "parameter_size": "13B",
                "can_chat": True,
                "can_embed": False,
            },
            {
                "name": "mock-embed",
                "size": 3,
                "parameter_size": "137M",
                "can_chat": False,
                "can_embed": True,
            },
        ]

    def set_chat_model(self, name):
        self.chat_model = name

    def status(self):
        return {
            "backend": "mock",
            "base_url": "local",
            "reachable": True,
            "chat_model": self.chat_model,
            "chat_model_ready": True,
            "embed_model": self.embed_model,
            "embed_model_ready": True,
        }


class MockTTS:
    def synthesize(self, text, voice):
        rate = 8000
        pcm = struct.pack(f"<{rate // 10}h", *([500] * (rate // 10)))  # 0.1s tone
        return pcm, rate

    def status(self):
        return {"backend": "mock-tts", "ready": True, "detail": "mock"}


class MockSTT:
    segments = [
        {"start": 0.0, "end": 3.0, "text": "The zephyr wombat festival happens every March."},
        {"start": 3.0, "end": 6.5, "text": "Tickets cost 42 tugrik."},
    ]

    def transcribe(self, path):
        return list(self.segments)

    def status(self):
        return {"backend": "mock-stt", "model": "mock", "ready": True, "detail": "mock"}


@pytest.fixture()
def mock_llm(monkeypatch):
    llm = MockLLM()
    tts = MockTTS()
    stt = MockSTT()
    for mod in (main, rag, store, studio, ingest, slides, video):
        if hasattr(mod, "get_llm"):
            monkeypatch.setattr(mod, "get_llm", lambda llm=llm: llm)
        if hasattr(mod, "get_tts"):
            monkeypatch.setattr(mod, "get_tts", lambda tts=tts: tts)
        if hasattr(mod, "get_stt"):
            monkeypatch.setattr(mod, "get_stt", lambda stt=stt: stt)
    return llm


@pytest.fixture()
def anon_client(mock_llm):
    """A client with no session — for testing the auth guard itself."""
    db.init_db()
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def client(mock_llm):
    """Signed-in client: sets the owner password on first use, then logs in."""
    db.init_db()
    with TestClient(main.app) as c:
        if c.get("/api/auth/status").json()["setup_required"]:
            c.post("/api/auth/setup", json={"password": TEST_PASSWORD})
        else:
            c.post("/api/auth/login", json={"password": TEST_PASSWORD})
        yield c


@pytest.fixture()
def notebook(client):
    return client.post("/api/notebooks", json={"name": "Test NB"}).json()
