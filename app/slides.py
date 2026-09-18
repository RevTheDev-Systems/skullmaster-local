"""Grounded slide decks and slide rendering.

A separate pipeline from `studio.py`: it reuses the shared grounded-generation
helpers (context gathering, strict JSON parsing, the untrusted-source rule) but
owns its own prompt, validation, and rendering. Slides render to PNG with
PyMuPDF, so no imaging dependency is needed.
"""

from __future__ import annotations

import logging
from pathlib import Path

import fitz  # PyMuPDF

from .config import ARTIFACT_CONTEXT_CHARS, load_prompt
from .providers import get_llm
from .studio import (
    UNTRUSTED_DATA_RULE,
    ModelOutputError,
    StudioError,
    _chat_text,
    _clean_str,
    _extract_json,
    _gather_context,
)

log = logging.getLogger(__name__)

MIN_SLIDES = 3
MAX_SLIDES = 15
SLIDE_W, SLIDE_H = 1280, 720


def validate_deck(deck: dict) -> dict:
    """Shape-check a slide deck so rendering never sees malformed slides."""
    title = deck.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ModelOutputError("deck needs a title")
    slides = deck.get("slides")
    if not isinstance(slides, list) or not MIN_SLIDES <= len(slides) <= MAX_SLIDES:
        raise ModelOutputError(f"slides must be a list of {MIN_SLIDES}-{MAX_SLIDES} entries")
    clean = []
    for slide in slides:
        if not isinstance(slide, dict):
            raise ModelOutputError("each slide must be an object")
        bullets = slide.get("bullets")
        if not isinstance(bullets, list) or not 1 <= len(bullets) <= 6:
            raise ModelOutputError("each slide needs 1-6 bullets")
        notes = slide.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise ModelOutputError("slide.notes must be a string")
        clean.append(
            {
                "title": _clean_str(slide.get("title"), "slide.title"),
                "bullets": [_clean_str(b, "slide.bullet") for b in bullets],
                "notes": (notes or "").strip(),
            }
        )
    deck["title"] = title.strip()
    deck["slides"] = clean
    return deck


def generate_deck(notebook_id: str) -> dict:
    """Grounded slide deck via the LLM, validated; one retry on bad shape."""
    context = _gather_context(notebook_id, ARTIFACT_CONTEXT_CHARS)
    messages = [
        {"role": "system", "content": load_prompt("slides_spec") + UNTRUSTED_DATA_RULE},
        {"role": "user", "content": f"Source material:\n\n{context}\n\nProduce the JSON now."},
    ]
    llm = get_llm()
    last_err = None
    for attempt in range(2):
        raw = _chat_text(llm, messages)
        try:
            return validate_deck(_extract_json(raw))
        except StudioError:
            raise
        except ModelOutputError as e:
            last_err = e
            log.warning("Slides parse failed (attempt %d): %s", attempt + 1, e)
            messages.append({"role": "assistant", "content": raw[:4000]})
            messages.append(
                {
                    "role": "user",
                    "content": f"That was invalid ({e}). Return ONLY the JSON object.",
                }
            )
    raise StudioError(f"Could not get a valid deck from the model: {last_err}")


def narration_text(slide: dict) -> str:
    """What a video overview speaks for a slide (notes, else the bullets)."""
    return slide.get("notes") or ". ".join(slide["bullets"])


def _fit_textbox(page, rect, text, *, size, font, colour):
    """Insert text into a box, shrinking the font until it fits."""
    current = size
    while current >= 10:
        if (
            page.insert_textbox(rect, text, fontsize=current, fontname=font, color=colour, align=0)
            >= 0
        ):
            return
        current -= 2
    page.insert_textbox(rect, text, fontsize=10, fontname=font, color=colour, align=0)


def render_slide_png(
    deck: dict, index: int, path, *, width: int = SLIDE_W, height: int = SLIDE_H
) -> Path:
    """Render one slide to a PNG (via PyMuPDF) and return the path."""
    slide = deck["slides"][index]
    doc = fitz.open()
    try:
        page = doc.new_page(width=width, height=height)
        page.draw_rect(fitz.Rect(0, 0, width, height), color=None, fill=(0.97, 0.975, 1.0))
        page.draw_rect(fitz.Rect(0, 0, 10, height), color=None, fill=(0.31, 0.27, 0.9))
        _fit_textbox(
            page,
            fitz.Rect(56, 56, width - 56, 176),
            slide["title"],
            size=40,
            font="hebo",
            colour=(0.12, 0.14, 0.19),
        )
        y = 196
        for bullet in slide["bullets"]:
            _fit_textbox(
                page,
                fitz.Rect(72, y, width - 56, y + 72),
                "•  " + bullet,
                size=24,
                font="helv",
                colour=(0.20, 0.23, 0.30),
            )
            y += 80
        page.insert_textbox(
            fitz.Rect(56, height - 52, width - 56, height - 20),
            f"{deck['title']}   ·   {index + 1}/{len(deck['slides'])}",
            fontsize=14,
            fontname="helv",
            color=(0.55, 0.58, 0.65),
        )
        pixmap = page.get_pixmap(dpi=96)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(path))
    finally:
        doc.close()
    return path


def deck_markdown(deck: dict) -> str:
    lines = [f"# {deck['title']}", ""]
    for i, slide in enumerate(deck["slides"], start=1):
        lines += [f"## {i}. {slide['title']}", ""]
        lines += [f"- {bullet}" for bullet in slide["bullets"]]
        if slide.get("notes"):
            lines += ["", f"> {slide['notes']}"]
        lines.append("")
    return "\n".join(lines)
