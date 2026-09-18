"""macOS built-in `say` TTS — zero-install fallback backend."""

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

# Friendly mapping so kokoro-style voice names in .env still work if the
# user flips TTS_MODEL to `say` without changing voices.
VOICE_ALIASES = {
    "af_heart": "Samantha",
    "af_bella": "Samantha",
    "am_michael": "Daniel",
    "am_adam": "Daniel",
}

SAMPLE_RATE = 22050


class SayTTS:
    def synthesize(self, text: str, voice: str) -> tuple[bytes, int]:
        voice = VOICE_ALIASES.get(voice, voice)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "line.wav"
            subprocess.run(
                ["say", "-v", voice, "-o", str(out), f"--data-format=LEI16@{SAMPLE_RATE}", text],
                check=True,
                capture_output=True,
            )
            with wave.open(str(out), "rb") as w:
                return w.readframes(w.getnframes()), w.getframerate()

    def status(self) -> dict:
        ready = shutil.which("say") is not None
        return {
            "backend": "say",
            "ready": ready,
            "detail": "macOS say available" if ready else "`say` not found (macOS only)",
        }
