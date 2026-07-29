"""MLX chat backend — Apple-silicon models served over an OpenAI-compatible API.

Works with `mlx_lm.server` (and any other OpenAI-compatible local endpoint, e.g.
LM Studio). This is the extension point the provider layer was designed for: no
business logic changes, only a new backend.

Reasoning ("thinking") models stream their chain-of-thought in a separate
`reasoning` delta rather than inside `content`, so — exactly as with Ollama's
thinking mode — we forward only `content` and the answer stays clean.

Chat only: mlx_lm.server exposes no /v1/embeddings, so embeddings stay on the
Ollama backend (see routing.py).
"""
import json
import logging
from collections.abc import Iterator

import httpx

from ..config import MLX_BASE_URL, MLX_REQUEST_TIMEOUT

log = logging.getLogger(__name__)

# /v1/models lists everything in the local HuggingFace cache, including image
# and speech models that cannot serve chat. Filter the obvious ones out so the
# picker only offers things that can actually answer.
NON_CHAT_HINTS = (
    "tts", "flux", "whisper", "embed", "stable-diffusion", "sdxl",
    "clip", "vae", "musicgen", "parler", "bark", "encodec",
)


def _looks_like_chat_model(name: str) -> bool:
    lowered = name.lower()
    return not any(hint in lowered for hint in NON_CHAT_HINTS)


class MLXProvider:
    """Chat-only backend. Not a full LLMProvider — composed by RoutingProvider."""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or MLX_BASE_URL).rstrip("/")
        self.chat_model = ""

    # ---- chat ----

    def chat(self, messages: list[dict], stream: bool = False) -> str | Iterator[str]:
        if stream:
            return self._chat_stream(messages)
        return "".join(self._chat_stream(messages))

    def _chat_stream(self, messages: list[dict]) -> Iterator[str]:
        payload = {
            "model": self.chat_model,
            "messages": messages,
            "stream": True,
            # Reasoning models spend a lot of budget thinking before answering.
            "max_tokens": 8192,
        }
        # No read timeout: loading a large model on the first request can take
        # minutes, and reasoning models pause between tokens.
        timeout = httpx.Timeout(MLX_REQUEST_TIMEOUT, read=None)
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", f"{self.base_url}/chat/completions",
                               json=payload) as response:
                if response.status_code >= 400:
                    response.read()
                    raise RuntimeError(
                        f"MLX backend error ({response.status_code}): {response.text[:300]}")
                for line in response.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    # `reasoning` holds the chain-of-thought — never forwarded.
                    content = delta.get("content")
                    if content:
                        yield content

    # ---- discovery ----

    def list_models(self) -> list[dict]:
        with httpx.Client(timeout=MLX_REQUEST_TIMEOUT) as client:
            response = client.get(f"{self.base_url}/models")
            response.raise_for_status()
            data = response.json().get("data", [])
        return sorted(
            (
                {
                    "name": m["id"],
                    "size": None,
                    "parameter_size": None,
                    "can_chat": True,
                    "can_embed": False,
                }
                for m in data
                if m.get("id") and _looks_like_chat_model(m["id"])
            ),
            key=lambda m: m["name"],
        )

    def set_chat_model(self, name: str):
        self.chat_model = name

    def reachable(self) -> bool:
        try:
            with httpx.Client(timeout=2.0) as client:
                return client.get(f"{self.base_url}/models").status_code == 200
        except Exception:
            return False

    def status(self) -> dict:
        up = self.reachable()
        return {
            "backend": "mlx",
            "base_url": self.base_url,
            "reachable": up,
            "detail": "connected" if up else "no MLX server on this endpoint",
        }
