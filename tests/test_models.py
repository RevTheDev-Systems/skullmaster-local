"""Model listing, runtime chat-model switching, and thinking-mode fallback."""
import types

import ollama
import pytest

from app import db, main
from app.config import CHAT_MODEL
from app.providers.ollama_provider import OllamaProvider
from app.providers.routing import MLX, OLLAMA, RoutingProvider, qualify


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


def test_qualified_model_ids_round_trip():
    """Ollama names contain ':' and MLX names contain '/', so the separator has
    to be unambiguous — and bare names must still resolve (older settings)."""
    from app.providers.routing import MLX, OLLAMA, qualify, split

    assert split(qualify(OLLAMA, "qwen3:30b")) == (OLLAMA, "qwen3:30b")
    assert split(qualify(MLX, "mlx-community/Qwen3.6-35B-A3B-8bit")) == (
        MLX, "mlx-community/Qwen3.6-35B-A3B-8bit")
    assert split("qwen3:30b") == (OLLAMA, "qwen3:30b")       # legacy setting
    assert split("weird::name::x") == (OLLAMA, "weird::name::x")  # unknown prefix


def test_mlx_filters_non_chat_models():
    from app.providers.mlx_provider import _looks_like_chat_model

    assert _looks_like_chat_model("mlx-community/Qwen3.6-35B-A3B-8bit")
    assert not _looks_like_chat_model("black-forest-labs/FLUX.2-dev")
    assert not _looks_like_chat_model("mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit")


def test_mlx_stream_discards_reasoning():
    """Thinking models stream chain-of-thought in a `reasoning` delta; only
    `content` may reach the user."""
    from app.providers.mlx_provider import MLXProvider

    sse = [
        'data: {"choices":[{"delta":{"role":"assistant"}}]}',
        'data: {"choices":[{"delta":{"reasoning":"Let me think hard..."}}]}',
        'data: {"choices":[{"delta":{"content":"The answer"}}]}',
        'data: {"choices":[{"delta":{"reasoning":"more hidden thought"}}]}',
        'data: {"choices":[{"delta":{"content":" is 42 [1]."}}]}',
        "data: [DONE]",
    ]

    class _Response:
        status_code = 200

        def iter_lines(self):
            return iter(sse)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, *a, **kw):
            return _Response()

    p = MLXProvider(base_url="http://mlx.test/v1")
    p.set_chat_model("thinker")
    import app.providers.mlx_provider as mod
    original, mod.httpx.Client = mod.httpx.Client, lambda **kw: _Client()
    try:
        assert "".join(p._chat_stream([])) == "The answer is 42 [1]."
    finally:
        mod.httpx.Client = original


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


def test_models_response_exposes_active_preferred_and_warning(client):
    body = client.get("/api/models").json()
    assert body["chat_model"] == body["preferred_model"]
    assert body["warning"] is None


# ---------- provider failover & dynamic discovery (Phase 1) ----------

class _FakeOllama:
    def __init__(self, *, reachable=True, models=None, default="qwen3:30b",
                 fail_list=False):
        self.up = reachable
        self.fail_list = fail_list
        self.embed_model = "nomic-embed-text"
        self.chat_model = default
        self._models = models if models is not None else [
            {"name": "qwen3:30b", "size": 1, "parameter_size": "30B",
             "can_chat": True, "can_embed": False},
            {"name": "nomic-embed-text", "size": 1, "parameter_size": None,
             "can_chat": False, "can_embed": True},
        ]

    def status(self):
        return {"backend": "ollama", "base_url": "fake", "reachable": self.up,
                "state": "healthy" if self.up else "offline",
                "chat_model": self.chat_model, "chat_model_ready": self.up,
                "embed_model": self.embed_model, "embed_model_ready": self.up}

    def list_models(self):
        if not self.up or self.fail_list:
            raise RuntimeError("ollama down")
        return [dict(m) for m in self._models]

    def has_model(self, name):
        return self.up and name in {m["name"] for m in self._models}

    def set_chat_model(self, name):
        self.chat_model = name

    def chat(self, messages, stream=False):
        return "ollama-reply"

    def ensure_models(self):
        return {}

    def ensure_models_for(self, wanted):
        return {m: "ready" for m in wanted}


class _FakeMLX:
    def __init__(self, *, up=True, models=("qwen3.6-35b",), fail_list=False):
        self.up = up
        self.fail_list = fail_list
        self.chat_model = ""
        self._models = list(models)

    def reachable(self):
        return self.up

    def list_models(self):
        if not self.up or self.fail_list:
            raise RuntimeError("mlx down")
        return [{"name": n, "size": None, "parameter_size": None,
                 "can_chat": True, "can_embed": False} for n in self._models]

    def set_chat_model(self, name):
        self.chat_model = name

    def chat(self, messages, stream=False):
        return "mlx-reply"

    def status(self):
        return {"backend": "mlx", "base_url": "fake", "reachable": self.up,
                "detail": ""}


def _routing(ollama, mlx):
    """A RoutingProvider with stubbed backends (no live Ollama/MLX needed)."""
    p = RoutingProvider.__new__(RoutingProvider)
    p.ollama = ollama
    p.mlx = mlx
    p._preferred = qualify(OLLAMA, "qwen3:30b")
    p.chat_model = p._preferred
    p.embed_model = ollama.embed_model
    p.runtime_warning = None
    return p


def test_persisted_mlx_activates_when_available():
    o, m = _FakeOllama(), _FakeMLX(up=True)
    p = _routing(o, m)
    sel = p.set_chat_model(qualify(MLX, "qwen3.6-35b"))
    assert sel["activated"] is True and sel["warning"] is None
    assert p.chat_model == qualify(MLX, "qwen3.6-35b")
    assert p.preferred_model() == qualify(MLX, "qwen3.6-35b")
    assert m.chat_model == "qwen3.6-35b"


def test_persisted_mlx_falls_back_when_unavailable():
    """Regression: a saved MLX model must not prevent startup when MLX is down."""
    o, m = _FakeOllama(), _FakeMLX(up=False)
    p = _routing(o, m)
    sel = p.set_chat_model(qualify(MLX, "qwen3.6-35b"))
    assert sel["activated"] is False
    assert p.chat_model == qualify(OLLAMA, "qwen3:30b")        # active fallback
    assert p.preferred_model() == qualify(MLX, "qwen3.6-35b")  # preference kept
    assert "unavailable" in sel["warning"]


def test_ollama_fallback_uses_an_installed_chat_model():
    o = _FakeOllama(models=[
        {"name": "phi4", "size": 1, "parameter_size": "14B",
         "can_chat": True, "can_embed": False},
    ])
    p = _routing(o, _FakeMLX(up=False))
    p.set_chat_model(qualify(MLX, "qwen3.6-35b"))
    assert p.chat_model == qualify(OLLAMA, "phi4")


def test_mlx_becomes_reachable_after_startup():
    o, m = _FakeOllama(), _FakeMLX(up=False, models=("later-model",))
    p = _routing(o, m)
    assert not any(x["backend"] == MLX for x in p.list_models())
    m.up = True                                   # server started after boot
    assert any(x["name"] == qualify(MLX, "later-model") for x in p.list_models())
    p.set_chat_model(qualify(MLX, "later-model"))
    assert p.chat_model == qualify(MLX, "later-model")


def test_mlx_disappearing_while_active_falls_back():
    o, m = _FakeOllama(), _FakeMLX(up=True)
    p = _routing(o, m)
    p.set_chat_model(qualify(MLX, "qwen3.6-35b"))
    m.up = False                                  # endpoint went away mid-session
    assert p.chat([{"role": "user", "content": "hi"}]) == "ollama-reply"
    assert p.chat_model == qualify(OLLAMA, "qwen3:30b")
    assert p.preferred_model() == qualify(MLX, "qwen3.6-35b")


def test_malformed_saved_identifier_does_not_raise():
    p = _routing(_FakeOllama(), _FakeMLX(up=True))
    sel = p.set_chat_model("weird::name::x")     # unknown prefix -> Ollama, absent
    assert sel["activated"] is False
    assert p.chat_model == qualify(OLLAMA, "qwen3:30b")
    assert sel["warning"]
    p2 = _routing(_FakeOllama(), _FakeMLX(up=True))
    assert p2.set_chat_model("")["activated"] is False


def test_default_selection_is_env_chat_model():
    p = RoutingProvider()                          # no saved preference
    assert p.chat_model == qualify(OLLAMA, CHAT_MODEL)
    assert p.preferred_model() == p.chat_model
    assert p.runtime_warning is None
    assert p.mlx is not None                        # auto mode: provider available


def test_model_list_failure_is_isolated_per_provider():
    p = _routing(_FakeOllama(fail_list=True), _FakeMLX(up=True))
    assert [m["backend"] for m in p.list_models()] == [MLX]

    p2 = _routing(_FakeOllama(), _FakeMLX(up=True, fail_list=True))
    assert all(m["backend"] == OLLAMA for m in p2.list_models())


def test_lifespan_survives_model_restore_failure(client, monkeypatch):
    """A saved model whose backend is missing must not abort app startup."""
    from fastapi.testclient import TestClient

    class _Boom:
        chat_model = "boom"
        runtime_warning = None

        def set_chat_model(self, name):
            raise RuntimeError("MLX backend is not enabled")

        def ensure_models(self):
            return {}

        def status(self):
            return {"reachable": False}

    previous = db.get_setting(main.CHAT_MODEL_SETTING)
    db.set_setting(main.CHAT_MODEL_SETTING, "mlx::unavailable")
    monkeypatch.setattr(main, "get_llm", lambda: _Boom())
    try:
        with TestClient(main.app):   # lifespan must swallow the failure and boot
            pass
    finally:
        db.set_setting(main.CHAT_MODEL_SETTING, previous or "mock-chat")


# ---------- capability metadata, caching & router (Phase 6) ----------

class _FakeDetails:
    parameter_size = "8B"
    quantization_level = "Q4_K_M"
    family = "llama"


class _FakeModel:
    def __init__(self, name):
        self.model = name
        self.size = 1
        self.details = _FakeDetails()


class _FakeShow:
    def __init__(self, caps, context_length=None):
        self.capabilities = caps
        self.model_info = {"llama.context_length": context_length} if context_length else {}


class _FakeOllamaClient:
    def __init__(self, models, caps, context=None, reachable=True):
        self._models = [_FakeModel(m) for m in models]
        self._caps = caps
        self._ctx = context or {}
        self.reachable = reachable
        self.list_calls = 0
        self.show_calls = 0

    def list(self):
        self.list_calls += 1
        if not self.reachable:
            raise RuntimeError("ollama down")
        return types.SimpleNamespace(models=self._models)

    def show(self, name):
        self.show_calls += 1
        return _FakeShow(self._caps.get(name, []), self._ctx.get(name))


def _ollama(caps, models=None, **kw):
    p = OllamaProvider()
    p.client = _FakeOllamaClient(models if models is not None else list(caps), caps, **kw)
    return p


def test_ollama_capability_metadata_is_cached_and_refreshable():
    p = _ollama({
        "chat": ["completion", "tools", "thinking"],
        "embed": ["embedding"],
    }, context={"chat": 131072})

    cold = p.list_models()
    assert p.client.show_calls == 2              # cold: one show() per model
    warm = p.list_models()
    assert p.client.show_calls == 2              # warm: cache, no show()
    assert warm == cold
    p.list_models(refresh=True)
    assert p.client.show_calls == 4              # refresh bypasses the cache

    chat = next(m for m in cold if m["name"] == "chat")
    assert chat["can_chat"] and chat["can_reason"] and chat["can_tools"]
    assert not chat["can_embed"] and not chat["can_vision"]
    assert chat["context_length"] == 131072
    assert chat["capabilities"] == ["completion", "tools", "thinking"]

    embed = next(m for m in cold if m["name"] == "embed")
    assert embed["can_embed"] and not embed["can_chat"]


def test_ollama_provider_state_healthy_degraded_offline():
    healthy = _ollama({"qwen3:30b": ["completion"], "nomic-embed-text": ["embedding"]},
                      models=["qwen3:30b", "nomic-embed-text"])
    healthy.chat_model, healthy.embed_model = "qwen3:30b", "nomic-embed-text"
    assert healthy.status()["state"] == "healthy"

    degraded = _ollama({"qwen3:30b": ["completion"]}, models=["qwen3:30b"])
    degraded.chat_model, degraded.embed_model = "qwen3:30b", "nomic-embed-text"
    assert degraded.status()["state"] == "degraded"

    offline = _ollama({}, models=[], reachable=False)
    assert offline.status()["state"] == "offline"


def test_capability_router_selects_by_capability():
    p = _routing(_FakeOllama(), _FakeMLX(up=True))
    p.chat_model = "ollama::chat"
    p.list_models = lambda refresh=False: [
        {"name": "ollama::chat", "can_chat": True, "can_reason": False},
        {"name": "ollama::reasoner", "can_chat": True, "can_reason": True},
        {"name": "mlx::mlx-community/Thinker", "can_chat": True, "can_reason": True},
    ]
    assert p.route("chat") == "ollama::chat"          # active model qualifies
    assert p.route("reasoning") == "ollama::reasoner"  # first capable fallback
    assert p.route("vision") is None


def test_provider_states_reflect_backend_health():
    p = _routing(_FakeOllama(), _FakeMLX(up=False))
    states = p.provider_states()
    assert states["ollama"]["configured"] is True
    assert states["ollama"]["state"] == "healthy"
    assert states["mlx"]["state"] == "offline"


def test_models_api_exposes_providers_and_accepts_refresh(client):
    assert "providers" in client.get("/api/models").json()
    refreshed = client.get("/api/models?refresh=1")
    assert refreshed.status_code == 200
    assert refreshed.json()["models"]
