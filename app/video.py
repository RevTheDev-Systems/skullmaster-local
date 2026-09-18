"""Narrated Video Overviews: a grounded slide deck + spoken narration, muxed to MP4.

A separate pipeline from `studio.py`: it composes the `slides` module (deck +
PNG rendering) with the TTS provider, then muxes with ffmpeg. It requires
ffmpeg on PATH; without it a clear error is raised rather than a silent failure.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path

from .config import ARTIFACTS_DIR, TTS_VOICE_A
from .providers import get_tts
from .slides import generate_deck, narration_text, render_slide_png
from .studio import StudioError, _resample_pcm16

log = logging.getLogger(__name__)

SLIDE_PAUSE_SECONDS = 0.4
MIN_SLIDE_SECONDS = 1.0


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _write_wav(path: Path, pcm: bytes, rate: int) -> float:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return len(pcm) / 2 / rate


def _build_audio_track(
    audios: list[tuple[Path, int]], durations: list[float], sample_rate: int
) -> bytes:
    """Concatenate per-slide narration, each padded to its on-screen duration."""
    chunks = []
    for (wav, rate), duration in zip(audios, durations):
        with wave.open(str(wav), "rb") as reader:
            pcm = reader.readframes(reader.getnframes())
        pcm = _resample_pcm16(pcm, rate, sample_rate)
        target_samples = int(sample_rate * duration)
        pad = max(0, target_samples - len(pcm) // 2)
        chunks.append(pcm + b"\x00\x00" * pad)
    return b"".join(chunks)


def build_video_overview(notebook_id: str) -> dict:
    """Generate and mux a narrated slide video; returns artifact metadata."""
    if not ffmpeg_available():
        raise StudioError("Video Overview requires ffmpeg (install it with `brew install ffmpeg`)")

    deck = generate_deck(notebook_id)
    tts = get_tts()

    with tempfile.TemporaryDirectory(prefix="skullmaster-video-") as tmp:
        work = Path(tmp)
        images: list[Path] = []
        audios: list[tuple[Path, int]] = []
        durations: list[float] = []

        for i, slide in enumerate(deck["slides"]):
            images.append(render_slide_png(deck, i, work / f"slide_{i:03d}.png"))
            pcm, rate = tts.synthesize(narration_text(slide), TTS_VOICE_A)
            wav = work / f"narration_{i:03d}.wav"
            spoken = _write_wav(wav, pcm, rate)
            audios.append((wav, rate))
            durations.append(max(MIN_SLIDE_SECONDS, spoken + SLIDE_PAUSE_SECONDS))
            log.info("Video slide %d/%d narrated", i + 1, len(deck["slides"]))

        # Image sequence: one still per slide, shown for its narration duration.
        concat = work / "images.txt"
        lines = []
        for image, duration in zip(images, durations):
            lines.append(f"file '{image.name}'")
            lines.append(f"duration {duration:.3f}")
        lines.append(f"file '{images[-1].name}'")  # concat demuxer repeats the last
        concat.write_text("\n".join(lines) + "\n")

        sample_rate = audios[0][1]
        narration = work / "narration.wav"
        _write_wav(narration, _build_audio_track(audios, durations, sample_rate), sample_rate)

        out = ARTIFACTS_DIR / f"{notebook_id}_video_{int(time.time())}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-i",
                str(narration),
                "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "30",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-shortest",
                str(out),
            ],
            check=True,
            capture_output=True,
        )

    return {
        "title": deck["title"],
        "filename": out.name,
        "duration_seconds": round(sum(durations), 1),
        "slides": deck["slides"],
    }
