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


# ---------- Studio artifacts: chart / infographic / spreadsheet ----------

ARTIFACT_PROMPTS = {
    "chart": "chart_spec",
    "infographic": "infographic_spec",
    "spreadsheet": "spreadsheet_spec",
    "mindgraph": "mindgraph_spec",
}


def _validate_artifact_spec(kind: str, spec: dict) -> dict:
    """Shape-check the model's JSON so the client renderer never sees garbage."""
    if "error" in spec:
        raise StudioError(spec["error"])
    if not isinstance(spec.get("title"), str) or not spec["title"].strip():
        raise ValueError("missing title")

    if kind == "chart":
        if spec.get("type") not in ("bar", "line", "pie"):
            raise ValueError("chart type must be bar, line, or pie")
        labels, values = spec.get("labels"), spec.get("values")
        if (not isinstance(labels, list) or not isinstance(values, list)
                or len(labels) != len(values) or not 2 <= len(labels) <= 12):
            raise ValueError("labels/values must be equal-length lists (2-12)")
        spec["values"] = [float(v) for v in values]
        spec["labels"] = [str(l) for l in labels]
        spec.setdefault("x_label", "")
        spec.setdefault("y_label", "")

    elif kind == "infographic":
        stats, sections = spec.get("stats"), spec.get("sections")
        if not isinstance(stats, list) or not 1 <= len(stats) <= 6:
            raise ValueError("stats must be a list of 1-6 entries")
        for s in stats:
            if not (isinstance(s, dict) and s.get("value") and s.get("label")):
                raise ValueError("each stat needs value and label")
        if not isinstance(sections, list) or not sections:
            raise ValueError("sections must be a non-empty list")
        for sec in sections:
            if not (isinstance(sec, dict) and sec.get("heading")
                    and isinstance(sec.get("points"), list) and sec["points"]):
                raise ValueError("each section needs heading and points")

    elif kind == "mindgraph":
        root, branches = spec.get("root"), spec.get("branches")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("mind graph needs a root topic")
        if not isinstance(branches, list) or not 2 <= len(branches) <= 8:
            raise ValueError("mind graph needs 2-8 branches")
        labels = {root.strip()}
        clean_branches = []
        for b in branches:
            if not (isinstance(b, dict) and isinstance(b.get("label"), str) and b["label"].strip()):
                raise ValueError("each branch needs a label")
            children = [str(c).strip() for c in (b.get("children") or []) if str(c).strip()]
            if not children:
                raise ValueError(f"branch {b['label']!r} has no children")
            clean_branches.append({"label": b["label"].strip(), "children": children[:6]})
            labels.add(b["label"].strip())
            labels.update(children[:6])
        spec["root"] = root.strip()
        spec["branches"] = clean_branches
        # Drop cross-links that don't resolve to real nodes — the renderer can
        # only draw an edge between nodes it actually placed.
        links = []
        for link in spec.get("links") or []:
            if not isinstance(link, dict):
                continue
            src, dst = str(link.get("from", "")).strip(), str(link.get("to", "")).strip()
            if src in labels and dst in labels and src != dst:
                links.append({"from": src, "to": dst,
                              "label": str(link.get("label", "")).strip()[:20]})
        spec["links"] = links[:8]

    elif kind == "spreadsheet":
        cols, rows = spec.get("columns"), spec.get("rows")
        if not isinstance(cols, list) or not cols:
            raise ValueError("columns must be a non-empty list")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
            raise ValueError("rows must be a list of 1-200 entries")
        width = len(cols)
        spec["rows"] = [(list(r) + [""] * width)[:width] for r in rows]

    else:
        raise StudioError(f"Unknown artifact kind: {kind}")

    spec.setdefault("source_note", "")
    return spec


def generate_artifact_spec(notebook_id: str, kind: str) -> dict:
    """Grounded artifact spec via the LLM, validated; one retry on bad shape."""
    if kind not in ARTIFACT_PROMPTS:
        raise StudioError(f"Unknown artifact kind: {kind}")
    context = _gather_context(notebook_id)
    messages = [
        {"role": "system", "content": load_prompt(ARTIFACT_PROMPTS[kind])},
        {"role": "user", "content": f"Source material:\n\n{context}\n\nProduce the JSON now."},
    ]
    llm = get_llm()
    last_err = None
    for attempt in range(2):
        raw = llm.chat(messages)
        try:
            return _validate_artifact_spec(kind, _extract_json(raw))
        except StudioError:
            raise  # model correctly reported unusable sources — don't retry
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            last_err = e
            log.warning("Artifact spec parse failed (attempt %d): %s", attempt + 1, e)
            messages.append({"role": "assistant", "content": raw[:4000]})
            messages.append({
                "role": "user",
                "content": f"That was invalid ({e}). Return ONLY the JSON object in the required shape.",
            })
    raise StudioError(f"Could not get a valid {kind} from the model: {last_err}")


def write_xlsx(spec: dict, path) -> None:
    """Materialize a spreadsheet spec as a real .xlsx file."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = spec["title"][:31] or "Data"
    ws.append(spec["columns"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in spec["rows"]:
        ws.append(row)
    for i, col in enumerate(spec["columns"], start=1):
        width = max([len(str(col))] + [len(str(r[i - 1])) for r in spec["rows"]])
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(width + 2, 50)
    wb.save(str(path))
