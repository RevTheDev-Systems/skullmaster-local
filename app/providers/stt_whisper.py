"""Whisper STT via faster-whisper (CTranslate2, CPU-friendly, fully local).

Weights download once from HuggingFace into models/whisper/ on first use —
same download-once policy as the Kokoro TTS weights. Video containers are
decoded directly (PyAV), so no external ffmpeg is required.
"""

import logging
import threading

from ..config import MODELS_DIR

log = logging.getLogger(__name__)

WHISPER_DIR = MODELS_DIR / "whisper"


class WhisperSTT:
    def __init__(self, size: str):
        self.size = size
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                log.info("Loading whisper-%s (downloads on first use)…", self.size)
                self._model = WhisperModel(
                    self.size,
                    device="cpu",
                    compute_type="int8",
                    download_root=str(WHISPER_DIR),
                )
        return self._model

    def transcribe(self, path: str) -> list[dict]:
        model = self._load()
        segments, info = model.transcribe(path, vad_filter=True)
        out = [
            {"start": s.start, "end": s.end, "text": s.text.strip()}
            for s in segments
            if s.text.strip()
        ]
        log.info(
            "Transcribed %s: %d segments, %.0fs audio, lang=%s",
            path,
            len(out),
            info.duration,
            info.language,
        )
        return out

    def _cached(self) -> bool:
        return WHISPER_DIR.exists() and any(WHISPER_DIR.rglob("model.bin"))

    def status(self) -> dict:
        cached = self._cached()
        return {
            "backend": "faster-whisper",
            "model": self.size,
            "ready": True,
            "detail": "weights present"
            if cached
            else "weights will download on first transcription",
        }
