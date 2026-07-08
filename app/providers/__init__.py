"""Provider factory. App code imports get_llm() / get_tts() / get_stt() — never a concrete backend."""
from functools import lru_cache

from ..config import LLM_PROVIDER, STT_MODEL, TTS_MODEL
from .base import LLMProvider, STTProvider, TTSProvider


@lru_cache(maxsize=1)
def get_llm() -> LLMProvider:
    if LLM_PROVIDER == "ollama":
        from .ollama_provider import OllamaProvider
        return OllamaProvider()
    raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER!r} (supported: ollama)")


@lru_cache(maxsize=1)
def get_stt() -> STTProvider:
    if STT_MODEL.startswith("whisper-"):
        from .stt_whisper import WhisperSTT
        return WhisperSTT(STT_MODEL.removeprefix("whisper-"))
    raise ValueError(f"Unknown STT_MODEL: {STT_MODEL!r} (supported: whisper-<size>)")


@lru_cache(maxsize=1)
def get_tts() -> TTSProvider:
    if TTS_MODEL == "say":
        from .tts_say import SayTTS
        return SayTTS()
    if TTS_MODEL == "kokoro":
        from .tts_kokoro import KokoroTTS
        return KokoroTTS()
    raise ValueError(f"Unknown TTS_MODEL: {TTS_MODEL!r} (supported: kokoro, say)")
