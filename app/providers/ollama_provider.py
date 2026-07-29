"""Ollama LLM provider: chat + embeddings against OLLAMA_BASE_URL."""
import logging
from collections.abc import Iterator

import ollama

from ..config import CHAT_MODEL, EMBED_MODEL, OLLAMA_BASE_URL

log = logging.getLogger(__name__)


class OllamaProvider:
    def __init__(self):
        self.client = ollama.Client(host=OLLAMA_BASE_URL)
        self.chat_model = CHAT_MODEL
        self.embed_model = EMBED_MODEL
        self._thinking: dict[str, bool] = {}   # model name -> supports thinking

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
        stream = self.client.chat(model=self.chat_model, messages=messages,
                                  stream=True, **kwargs)
        for part in stream:
            content = part.get("message", {}).get("content", "")
            if content:
                yield content

    def _supports_thinking(self, model: str) -> bool:
        if model not in self._thinking:
            self._thinking[model] = "thinking" in self._capabilities(model)
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

    def _capabilities(self, name: str) -> list[str]:
        """Ollama reports capabilities from show(), not list()."""
        try:
            return list(getattr(self.client.show(name), "capabilities", None) or [])
        except Exception:
            return []

    def list_models(self) -> list[dict]:
        models = []
        for m in self.client.list().models:
            caps = self._capabilities(m.model)
            models.append({
                "name": m.model,
                "size": m.size,
                "parameter_size": getattr(m.details, "parameter_size", None) if m.details else None,
                "can_chat": "completion" in caps,
                "can_embed": "embedding" in caps,
            })
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
        return {
            "backend": "ollama",
            "base_url": OLLAMA_BASE_URL,
            "reachable": reachable,
            "chat_model": self.chat_model,
            "chat_model_ready": present(self.chat_model),
            "embed_model": self.embed_model,
            "embed_model_ready": present(self.embed_model),
        }
