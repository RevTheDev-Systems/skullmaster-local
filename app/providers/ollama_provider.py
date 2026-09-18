"""Ollama LLM provider: chat + embeddings against OLLAMA_BASE_URL."""

import logging
import time
from collections.abc import Iterator

import ollama

from ..config import CHAT_MODEL, EMBED_MODEL, OLLAMA_BASE_URL

log = logging.getLogger(__name__)

# Per-model capability/context metadata comes from show(), which is one HTTP
# call per model (the "N+1"). Cache it so the model picker is a single list()
# call when warm, with an explicit refresh for when a model was just changed.
METADATA_TTL = 300.0


def _context_length(model_info: dict) -> int | None:
    for key, value in (model_info or {}).items():
        if key.endswith("context_length") and isinstance(value, int):
            return value
    return None


def capability_flags(caps: list[str]) -> dict:
    """Map Ollama's capability strings to the router's boolean flags."""
    return {
        "can_chat": "completion" in caps,
        "can_embed": "embedding" in caps,
        "can_reason": "thinking" in caps,
        "can_vision": "vision" in caps,
        "can_tools": "tools" in caps,
    }


class OllamaProvider:
    def __init__(self):
        self.client = ollama.Client(host=OLLAMA_BASE_URL)
        self.chat_model = CHAT_MODEL
        self.embed_model = EMBED_MODEL
        self._thinking: dict[str, bool] = {}  # model name -> supports thinking
        self._meta_cache: dict[str, tuple[float, dict]] = {}
        self.list_calls = 0  # list() HTTP calls (for latency measurement)
        self.metadata_calls = 0  # show() HTTP calls (the N+1)

    # ---- chat ----

    def chat(self, messages: list[dict], stream: bool = False) -> str | Iterator[str]:
        if stream:
            return self._chat_stream(messages)
        parts = list(self._chat_stream(messages))
        return "".join(parts)

    def _raw_stream(self, messages: list[dict], think: bool) -> Iterator[str]:
        # think=True makes Ollama route reasoning into a separate `thinking`
        # field so `content` stays clean; we never yield thinking tokens.
        kwargs = {"think": True} if think else {}
        stream = self.client.chat(model=self.chat_model, messages=messages, stream=True, **kwargs)
        for part in stream:
            content = part.get("message", {}).get("content", "")
            if content:
                yield content

    def _supports_thinking(self, model: str) -> bool:
        if model not in self._thinking:
            self._thinking[model] = "thinking" in self._metadata(model)["capabilities"]
        return self._thinking[model]

    def _chat_stream(self, messages: list[dict]) -> Iterator[str]:
        think = self._supports_thinking(self.chat_model)
        emitted = False
        try:
            for content in self._raw_stream(messages, think):
                emitted = True
                yield content
            return
        except (TypeError, ollama.ResponseError):
            # Ollama rejects `think` only once the lazy stream is consumed, so
            # this fallback cannot live around the client.chat() call itself.
            # Never retry after partial output — it would duplicate tokens.
            if emitted or not think:
                raise
            log.info("Model %s rejected thinking mode; retrying without it", self.chat_model)
            self._thinking[self.chat_model] = False
        yield from self._raw_stream(messages, False)

    # ---- embeddings ----

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), 32):
            resp = self.client.embed(model=self.embed_model, input=texts[i : i + 32])
            vectors.extend(resp.embeddings)
        return vectors

    # ---- lifecycle ----

    def _installed(self) -> set[str]:
        return {m.model for m in self.client.list().models}

    def has_model(self, name: str) -> bool:
        """Whether `name` (or its `:latest` alias) is installed locally."""
        try:
            return bool({name, f"{name}:latest"} & self._installed())
        except Exception:
            return False

    def _metadata(self, name: str, refresh: bool = False) -> dict:
        """Cached show() metadata: capabilities + context length.

        `refresh=True` bypasses the TTL, which the picker's refresh button uses
        so a freshly pulled/changed model is re-read. Warm listings make no
        show() calls at all.
        """
        now = time.time()
        cached = self._meta_cache.get(name)
        if cached and not refresh and now - cached[0] < METADATA_TTL:
            return cached[1]
        self.metadata_calls += 1
        caps: list[str] = []
        context_length = None
        try:
            show = self.client.show(name)
            caps = list(getattr(show, "capabilities", None) or [])
            # ollama-python names this attribute `modelinfo`; accept both.
            info = getattr(show, "model_info", None) or getattr(show, "modelinfo", None) or {}
            context_length = _context_length(info)
        except Exception:
            pass
        meta = {"capabilities": caps, "context_length": context_length}
        self._meta_cache[name] = (now, meta)
        return meta

    def _capabilities(self, name: str, refresh: bool = False) -> list[str]:
        return self._metadata(name, refresh)["capabilities"]

    def list_models(self, refresh: bool = False) -> list[dict]:
        self.list_calls += 1
        models = []
        for m in self.client.list().models:
            meta = self._metadata(m.model, refresh=refresh)
            caps = meta["capabilities"]
            details = getattr(m, "details", None)
            models.append(
                {
                    "name": m.model,
                    "size": m.size,
                    "parameter_size": getattr(details, "parameter_size", None) if details else None,
                    "quantization": getattr(details, "quantization_level", None)
                    if details
                    else None,
                    "capabilities": caps,
                    "context_length": meta["context_length"],
                    **capability_flags(caps),
                }
            )
        return sorted(models, key=lambda m: m["name"])

    def set_chat_model(self, name: str):
        self.chat_model = name

    def ensure_models(self) -> dict:
        return self.ensure_models_for([self.chat_model, self.embed_model])

    def ensure_models_for(self, wanted: list[str]) -> dict:
        result = {}
        try:
            installed = self._installed()
        except Exception as e:
            return {"error": f"Ollama unreachable at {OLLAMA_BASE_URL}: {e}"}
        for model in wanted:
            names = {model, f"{model}:latest"}
            if names & installed:
                result[model] = "ready"
                continue
            log.info("Model %s missing — pulling (this may take a while)…", model)
            try:
                self.client.pull(model)
                result[model] = "pulled"
            except Exception as e:
                result[model] = f"error: {e}"
        return result

    def status(self) -> dict:
        try:
            installed = self._installed()
            reachable = True
        except Exception:
            installed, reachable = set(), False

        def present(m):
            return bool({m, f"{m}:latest"} & installed)

        chat_ready = present(self.chat_model)
        embed_ready = present(self.embed_model)
        if not reachable:
            state = "offline"
        elif chat_ready and embed_ready:
            state = "healthy"
        else:
            state = "degraded"
        return {
            "backend": "ollama",
            "base_url": OLLAMA_BASE_URL,
            "reachable": reachable,
            "state": state,
            "chat_model": self.chat_model,
            "chat_model_ready": chat_ready,
            "embed_model": self.embed_model,
            "embed_model_ready": embed_ready,
            "list_calls": self.list_calls,
            "metadata_calls": self.metadata_calls,
        }
