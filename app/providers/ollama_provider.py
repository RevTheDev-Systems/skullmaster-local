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

    # ---- chat ----

    def chat(self, messages: list[dict], stream: bool = False) -> str | Iterator[str]:
        if stream:
            return self._chat_stream(messages)
        parts = list(self._chat_stream(messages))
        return "".join(parts)

    def _chat_stream(self, messages: list[dict]) -> Iterator[str]:
        # think=True makes Ollama route reasoning into a separate `thinking`
        # field so `content` stays clean; we never yield thinking tokens.
        try:
            stream = self.client.chat(model=self.chat_model, messages=messages,
                                      stream=True, think=True)
        except (TypeError, ollama.ResponseError):
            # backend or model without thinking support
            stream = self.client.chat(model=self.chat_model, messages=messages, stream=True)
        for part in stream:
            content = part.get("message", {}).get("content", "")
            if content:
                yield content

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

    def ensure_models(self) -> dict:
        result = {}
        try:
            installed = self._installed()
        except Exception as e:
            return {"error": f"Ollama unreachable at {OLLAMA_BASE_URL}: {e}"}
        for model in (self.chat_model, self.embed_model):
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
