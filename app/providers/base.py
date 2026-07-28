"""Provider interfaces. App code depends only on these — never on a concrete backend."""
from collections.abc import Iterator
from typing import Protocol


class LLMProvider(Protocol):
    """Chat + embeddings backend (Ollama today; any OpenAI-compatible endpoint tomorrow)."""

    def chat(self, messages: list[dict], stream: bool = False) -> str | Iterator[str]:
        """Non-stream: returns full text. Stream: returns iterator of text deltas.
        Reasoning/thinking tokens are never included in the output."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    def ensure_models(self) -> dict:
        """Verify configured models exist; pull them if missing.
        Returns {model_name: 'ready' | 'pulled' | 'error: ...'}."""
        ...

    def list_models(self) -> list[dict]:
        """Installed models: [{name, size, parameter_size, can_chat, can_embed}]."""
        ...

    def set_chat_model(self, name: str):
        """Switch the active chat model for this process."""
        ...

    def status(self) -> dict:
        """Lightweight health info: {backend, chat_model, embed_model, reachable}."""
        ...


class TTSProvider(Protocol):
    """Text-to-speech backend for Audio Overviews."""

    def synthesize(self, text: str, voice: str) -> tuple[bytes, int]:
        """Returns (pcm16 mono audio bytes, sample_rate)."""
        ...

    def status(self) -> dict:
        """{backend, ready, detail}."""
        ...


class STTProvider(Protocol):
    """Speech-to-text backend for video/audio source transcription."""

    def transcribe(self, path: str) -> list[dict]:
        """Returns segments: [{"start": float_seconds, "end": float_seconds, "text": str}]."""
        ...

    def status(self) -> dict:
        """{backend, model, ready, detail}."""
        ...
