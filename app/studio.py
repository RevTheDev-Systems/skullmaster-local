"""Audio Overview: sources → two-host script (LLM) → local TTS → single WAV."""
import json
import logging
import re
import struct
import time
import wave

from .config import (
    AUDIO_DIR,
    PODCAST_CONTEXT_CHARS,
    TTS_VOICE_A,
    TTS_VOICE_B,
    load_prompt,
)
from .providers import get_llm, get_tts
from .rag import strip_think
from .store import notebook_chunks

log = logging.getLogger(__name__)

TARGET_LINES = 28
PAUSE_SECONDS = 0.35


class StudioError(Exception):
    pass


# ---------- Script generation ----------

def _gather_context(notebook_id: str) -> str:
    """Concatenate the notebook's chunks (grouped by source) up to a budget."""
    rows = notebook_chunks(notebook_id)
    if not rows:
        raise StudioError("Notebook has no sources to talk about")
    rows.sort(key=lambda r: (r["source_name"], r["seq"]))
    parts, used = [], 0
    current_source = None
    for r in rows:
        if r["source_name"] != current_source:
            current_source = r["source_name"]
            header = f"\n\n===== SOURCE: {current_source} =====\n"
            parts.append(header)
            used += len(header)
        take = r["text"][: max(0, PODCAST_CONTEXT_CHARS - used)]
        if not take:
            break
        parts.append(take)
        used += len(take)
        if used >= PODCAST_CONTEXT_CHARS:
            break
    return "".join(parts).strip()


def _extract_json(text: str) -> dict:
    text = strip_think(text).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(text[start : end + 1])


def generate_script(notebook_id: str) -> dict:
    """Returns {"title": str, "lines": [{"speaker": "A"|"B", "text": str}, ...]}."""
    context = _gather_context(notebook_id)
    system = load_prompt("podcast_script").replace("{target_lines}", str(TARGET_LINES))
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Source material:\n\n{context}\n\nWrite the episode now."},
    ]
    llm = get_llm()
    last_err = None
    for attempt in range(2):
        raw = llm.chat(messages)
        try:
            script = _extract_json(raw)
            lines = [
                {"speaker": l["speaker"].strip().upper()[-1], "text": l["text"].strip()}
                for l in script["lines"]
                if l.get("text", "").strip() and l.get("speaker", "").strip().upper()[-1] in ("A", "B")
            ]
            if len(lines) < 4:
                raise ValueError("script too short")
            return {"title": script.get("title", "Audio Overview"), "lines": lines}
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            last_err = e
            log.warning("Script parse failed (attempt %d): %s", attempt + 1, e)
            messages.append({"role": "assistant", "content": raw[:4000]})
            messages.append({
                "role": "user",
                "content": "That was not valid JSON in the required shape. Return ONLY the JSON object.",
            })
    raise StudioError(f"Could not get a valid script from the model: {last_err}")


# ---------- Audio assembly ----------

def _resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Nearest-sample resampling — fine for speech, avoids a scipy dependency."""
    if src_rate == dst_rate:
        return pcm
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    n_out = int(len(samples) * dst_rate / src_rate)
    out = [samples[min(int(i * src_rate / dst_rate), len(samples) - 1)] for i in range(n_out)]
    return struct.pack(f"<{len(out)}h", *out)


def synthesize_podcast(notebook_id: str, script: dict) -> dict:
    """Voice each line, join with pauses, write one WAV. Returns file metadata."""
    tts = get_tts()
    voices = {"A": TTS_VOICE_A, "B": TTS_VOICE_B}

    rendered: list[tuple[bytes, int]] = []
    for i, line in enumerate(script["lines"]):
        pcm, rate = tts.synthesize(line["text"], voices[line["speaker"]])
        rendered.append((pcm, rate))
        log.info("TTS line %d/%d done", i + 1, len(script["lines"]))

    sample_rate = rendered[0][1]
    pause = b"\x00\x00" * int(sample_rate * PAUSE_SECONDS)
    body = pause.join(_resample_pcm16(pcm, rate, sample_rate) for pcm, rate in rendered)

    filename = f"{notebook_id}_{int(time.time())}.wav"
    path = AUDIO_DIR / filename
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(body)

    duration = len(body) / 2 / sample_rate
    return {
        "filename": filename,
        "title": script["title"],
        "duration_seconds": round(duration, 1),
        "lines": len(script["lines"]),
    }


def generate_audio_overview(notebook_id: str) -> dict:
    script = generate_script(notebook_id)
    meta = synthesize_podcast(notebook_id, script)
    meta["script"] = script["lines"]
    return meta
