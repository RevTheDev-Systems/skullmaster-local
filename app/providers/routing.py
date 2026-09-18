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

from ..config import CHAT_MODEL, mlx_configured
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


def _label(qualified: str) -> str:
    """`mlx::org/Model` -> `org/Model` for human-facing messages."""
    return split(qualified)[1]


class RoutingProvider:
    """Implements LLMProvider by composing the Ollama and MLX backends.

    Two model identities are tracked deliberately:

    * ``preferred_model`` — what the user last chose (persisted by the app).
    * ``chat_model``      — the *active* runtime model, which may be a fallback
      when the preferred model's backend is currently unreachable.

    Availability is probed live, never frozen at import time, so an endpoint
    that starts or stops later is handled without a restart and a temporarily
    offline provider can never prevent the application from booting.
    """

    def __init__(self):
        self.ollama = OllamaProvider()
        # Constructed whenever the mode allows it; reachability is probed live.
        # auto/true -> an MLXProvider instance exists even if nothing is listening.
        self.mlx = MLXProvider() if mlx_configured() else None
        self._preferred = qualify(OLLAMA, CHAT_MODEL)
        self.chat_model = self._preferred
        self.embed_model = self.ollama.embed_model
        self.runtime_warning: str | None = None

    # ---- backend selection ----

    def _backend(self, backend_name: str):
        if backend_name == MLX:
            if not self.mlx:
                raise RuntimeError("MLX backend is disabled (MLX_ENABLED=false)")
            return self.mlx
        if backend_name == OLLAMA:
            return self.ollama
        raise RuntimeError(f"unknown model backend: {backend_name!r}")

    def _reachable(self, backend_name: str) -> bool:
        if backend_name == OLLAMA:
            try:
                return bool(self.ollama.status().get("reachable"))
            except Exception:
                return False
        if backend_name == MLX:
            try:
                return bool(self.mlx) and self.mlx.reachable()
            except Exception:
                return False
        return False

    def _installed(self, backend_name: str, model: str) -> bool:
        if backend_name == OLLAMA:
            return self.ollama.has_model(model)
        if backend_name == MLX:
            return self._mlx_has(model)
        return False

    def _safe_fallback(self) -> str:
        """A reachable Ollama chat model to use while the preferred one is down.

        Never touches ``_preferred`` — preference is preserved, only the active
        runtime model changes. Falls back to the configured default even when
        Ollama itself is down, so the resulting failure is deterministic and
        attributable rather than a crash.
        """
        if self._reachable(OLLAMA):
            if self.ollama.has_model(CHAT_MODEL):
                self.ollama.set_chat_model(CHAT_MODEL)
                return qualify(OLLAMA, CHAT_MODEL)
            try:
                for m in self.ollama.list_models():
                    if m.get("can_chat"):
                        self.ollama.set_chat_model(m["name"])
                        return qualify(OLLAMA, m["name"])
            except Exception:
                log.warning("Could not enumerate Ollama models for fallback", exc_info=True)
        self.ollama.set_chat_model(CHAT_MODEL)
        return qualify(OLLAMA, CHAT_MODEL)

    def _ensure_active(self):
        """If the active MLX endpoint vanished, move to a working model."""
        backend_name, model = split(self.chat_model)
        if backend_name == MLX and not self._reachable(MLX):
            fallback = self._safe_fallback()
            self.chat_model = fallback
            self.runtime_warning = (
                f"MLX model {model!r} went offline; using {_label(fallback)}.")
            log.warning("%s", self.runtime_warning)

    def _active(self):
        backend_name, model = split(self.chat_model)
        backend = self._backend(backend_name)
        backend.set_chat_model(model)   # one model is active at a time
        return backend

    # ---- LLMProvider ----

    def chat(self, messages: list[dict], stream: bool = False):
        self._ensure_active()
        return self._active().chat(messages, stream=stream)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.ollama.embed(texts)

    def list_models(self, refresh: bool = False) -> list[dict]:
        """All models from every configured provider, with capability flags.

        A failure in one provider is isolated: the other still lists its models.
        `refresh=True` re-reads per-model metadata (bypassing Ollama's cache).
        """
        models: list[dict] = []
        try:
            for m in self.ollama.list_models(refresh=refresh):
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

    # ---- capability router / registry ----

    def provider_states(self) -> dict:
        """Per-provider state: configured / reachable / healthy|degraded|offline."""
        states: dict[str, dict] = {}
        try:
            o = self.ollama.status()
            states[OLLAMA] = {"configured": True, "reachable": o.get("reachable", False),
                              "state": o.get("state", "offline")}
        except Exception:
            states[OLLAMA] = {"configured": True, "reachable": False, "state": "offline"}
        if self.mlx:
            try:
                m = self.mlx.status()
                states[MLX] = {"configured": True, "reachable": m.get("reachable", False),
                               "state": m.get("state", "offline")}
            except Exception:
                states[MLX] = {"configured": True, "reachable": False, "state": "offline"}
        else:
            states[MLX] = {"configured": False, "reachable": False, "state": "offline"}
        return states

    def route(self, capability: str = "chat") -> str | None:
        """The active model if it supports `capability`, else the first that does.

        Capabilities: chat | reasoning | embedding | vision | tools. Returns a
        qualified model id or None. Embeddings still execute on Ollama.
        """
        flag = {"chat": "can_chat", "reasoning": "can_reason",
                "embedding": "can_embed", "vision": "can_vision",
                "tools": "can_tools"}.get(capability, f"can_{capability}")
        models = self.list_models()
        active = next((m for m in models if m.get("name") == self.chat_model), None)
        if active and active.get(flag):
            return self.chat_model
        return next((m.get("name") for m in models if m.get(flag)), None)

    def registry(self, refresh: bool = False) -> dict:
        """Provider + model registry for the picker and diagnostics."""
        return {
            "providers": self.provider_states(),
            "active": self.chat_model,
            "preferred": self._preferred,
            "models": self.list_models(refresh=refresh),
        }

    def preferred_model(self) -> str:
        return self._preferred

    def set_chat_model(self, name: str) -> dict:
        """Remember `name` as the preferred model and activate it if usable.

        Never raises because a provider is offline — the saved preference is
        preserved and a reachable fallback becomes active instead. Returns
        {activated, active, preferred, warning}.
        """
        backend_name, model = split(name)
        self._preferred = qualify(backend_name, model)
        if self._reachable(backend_name) and self._installed(backend_name, model):
            self._backend(backend_name).set_chat_model(model)
            self.chat_model = self._preferred
            self.runtime_warning = None
            return self._selection(activated=True)
        fallback = self._safe_fallback()
        self.chat_model = fallback
        self.runtime_warning = (
            f"{_label(self._preferred)} is unavailable; using {_label(fallback)}.")
        log.warning("Model fallback: %s", self.runtime_warning)
        return self._selection(activated=False)

    def _selection(self, activated: bool) -> dict:
        return {
            "activated": activated,
            "active": self.chat_model,
            "preferred": self._preferred,
            "warning": self.runtime_warning,
        }

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
        base["preferred_model"] = self._preferred
        if self.runtime_warning:
            base["warning"] = self.runtime_warning
        states = {OLLAMA: {"configured": True, "reachable": base.get("reachable", False),
                           "state": base.get("state", "offline")}}
        if self.mlx:
            mlx_status = self.mlx.status()
            base["mlx"] = mlx_status
            states[MLX] = {"configured": True, "reachable": mlx_status.get("reachable", False),
                           "state": mlx_status.get("state", "offline")}
        else:
            states[MLX] = {"configured": False, "reachable": False, "state": "offline"}
        base["provider_states"] = states
        return base
