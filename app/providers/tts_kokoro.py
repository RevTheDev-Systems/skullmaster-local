"""Kokoro-82M TTS via kokoro-onnx. Weights auto-download once into models/; runtime is offline."""

import logging
import threading
import urllib.request

import numpy as np

from ..config import MODELS_DIR

log = logging.getLogger(__name__)

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
MODEL_FILE = MODELS_DIR / "kokoro-v1.0.onnx"
VOICES_FILE = MODELS_DIR / "voices-v1.0.bin"


class KokoroTTS:
    def __init__(self):
        self._kokoro = None
        self._lock = threading.Lock()

    def _download(self, url: str, dest):
        tmp = dest.with_suffix(dest.suffix + ".part")
        log.info("Downloading %s → %s", url, dest.name)
        urllib.request.urlretrieve(url, tmp)
        tmp.rename(dest)

    def ensure_weights(self):
        if not MODEL_FILE.exists():
            self._download(f"{RELEASE}/kokoro-v1.0.onnx", MODEL_FILE)
        if not VOICES_FILE.exists():
            self._download(f"{RELEASE}/voices-v1.0.bin", VOICES_FILE)

    def _load(self):
        with self._lock:
            if self._kokoro is None:
                self.ensure_weights()
                from kokoro_onnx import Kokoro

                self._kokoro = Kokoro(str(MODEL_FILE), str(VOICES_FILE))
        return self._kokoro

    def synthesize(self, text: str, voice: str) -> tuple[bytes, int]:
        kokoro = self._load()
        samples, sample_rate = kokoro.create(text, voice=voice, speed=1.0, lang="en-us")
        pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        return pcm16, sample_rate

    def status(self) -> dict:
        weights = MODEL_FILE.exists() and VOICES_FILE.exists()
        return {
            "backend": "kokoro",
            "ready": weights,
            "detail": "weights present"
            if weights
            else "weights will download on first use (~340MB)",
        }
