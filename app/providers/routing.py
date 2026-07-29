"""Routes chat to whichever backend owns the selected model.

Model ids are qualified as `<backend>::<model>` (e.g. `ollama::qwen3:30b`,
`mlx::mlx-community/Qwen3.6-35B-A3B-8bit`) because Ollama names contain ':' and
MLX names contain '/', so neither alone is a safe separator. Bare names are
treated as Ollama for backwards compatibility with settings written before MLX
support existed.

Embeddings always go to Ollama: MLX exposes no embeddings endpoint, and swapping
embedding models would invalidate every stored vector.
"""
import logging

from ..config import CHAT_MODEL, MLX_ENABLED
from .mlx_provider import MLXProvider
from .ollama_provider import OllamaProvider

log = logging.getLogger(__name__)

SEPARATOR = "::"
OLLAMA, MLX = "ollama", "mlx"


def qualify(backend: str, name: str) -> str:
    return f"{backend}{SEPARATOR}{name}"


def split(qualified: str) -> tuple[str, str]:
    """('ollama', 'qwen3:30b') — unqualified names default to Ollama."""
    if SEPARATOR in qualified:
        backend, _, name = qualified.partition(SEPARATOR)
        if backend in (OLLAMA, MLX):
            return backend, name
    return OLLAMA, qualified


class RoutingProvider:
    """Implements LLMProvider by composing the Ollama and MLX backends."""

    def __init__(self):
        self.ollama = OllamaProvider()
        self.mlx = MLXProvider() if MLX_ENABLED else None
        self.chat_model = qualify(OLLAMA, CHAT_MODEL)
        self.embed_model = self.ollama.embed_model

    # ---- routing ----

    def _backend(self, name: str):
        if name == MLX:
            if not self.mlx:
                raise RuntimeError("MLX backend is not enabled")
            return self.mlx
        return self.ollama

    def _active(self):
        backend_name, model = split(self.chat_model)
        backend = self._backend(backend_name)
        backend.set_chat_model(model)   # one model is active at a time
        return backend

    # ---- LLMProvider ----

    def chat(self, messages: list[dict], stream: bool = False):
        return self._active().chat(messages, stream=stream)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.ollama.embed(texts)

    def list_models(self) -> list[dict]:
        models: list[dict] = []
        try:
            for m in self.ollama.list_models():
                models.append({**m, "backend": OLLAMA, "label": m["name"],
                               "name": qualify(OLLAMA, m["name"])})
        except Exception as e:
            log.warning("Could not list Ollama models: %s", e)
        if self.mlx:
            try:
                for m in self.mlx.list_models():
                    models.append({**m, "backend": MLX, "label": m["name"],
                                   "name": qualify(MLX, m["name"])})
            except Exception as e:
                log.warning("Could not list MLX models: %s", e)
        return models

    def set_chat_model(self, name: str):
        backend_name, model = split(name)
        self._backend(backend_name)      # raises if MLX is off
        self.chat_model = qualify(backend_name, model)

    def ensure_models(self) -> dict:
        """Only Ollama can pull; MLX models come from the HuggingFace cache."""
        backend_name, model = split(self.chat_model)
        if backend_name == OLLAMA:
            return self.ollama.ensure_models()
        # MLX chat: still make sure the embedding model is present.
        result = self.ollama.ensure_models_for([self.ollama.embed_model])
        result[model] = "mlx" if self._mlx_has(model) else "error: not in MLX cache"
        return result

    def _mlx_has(self, model: str) -> bool:
        try:
            return any(m["name"] == model for m in self.mlx.list_models())
        except Exception:
            return False

    def status(self) -> dict:
        base = self.ollama.status()
        backend_name, model = split(self.chat_model)
        if backend_name == MLX:
            base["chat_model"] = model
            base["chat_model_ready"] = bool(self.mlx) and self._mlx_has(model)
        base["chat_backend"] = backend_name
        base["backend"] = f"{OLLAMA}+{MLX}" if self.mlx else OLLAMA
        if self.mlx:
            base["mlx"] = self.mlx.status()
        return base
