"""Vision-document understanding: read diagrams/charts/images with a local model.

Uses the capability router to find a vision-capable model (e.g. `qwen2.5vl:7b`)
and asks it to transcribe and describe an image. The result is plain text that
flows through the existing chunk/retrieval/citation pipeline, so image content is
searchable and citable like any other source.
"""

from __future__ import annotations

import base64
import logging

from .providers import get_llm

log = logging.getLogger(__name__)

VISION_PROMPT = (
    "You are reading one image from a user's document — a diagram, chart, "
    "screenshot, table, or photo. First transcribe ALL visible text, numbers, "
    "labels, axis values, and units verbatim. Then, in one or two sentences, "
    "describe the image's structure and what it shows. Never invent anything "
    "that is not visible; if there is no readable content, say so."
)


class VisionError(Exception):
    """Vision is unavailable or the analysis failed."""


def _vision_model(llm) -> str | None:
    plan = getattr(llm, "route_plan", None)
    if callable(plan):
        try:
            return plan("vision").get("model")
        except Exception:
            return None
    return None


def available(llm=None) -> bool:
    return _vision_model(llm or get_llm()) is not None


def analyze_image(image_bytes: bytes, *, llm=None, prompt: str = VISION_PROMPT) -> str:
    """Transcribe + describe an image; raises VisionError if unavailable."""
    llm = llm or get_llm()
    model = _vision_model(llm)
    if model is None:
        raise VisionError("No vision-capable model is installed (e.g. `ollama pull qwen2.5vl:7b`)")
    messages = [
        {
            "role": "user",
            "content": prompt,
            "images": [base64.b64encode(image_bytes).decode("ascii")],
        }
    ]
    try:
        reply = llm.chat_with(model, messages) if hasattr(llm, "chat_with") else llm.chat(messages)
    except Exception as e:  # noqa: BLE001 — surface any provider failure uniformly
        raise VisionError(f"Vision analysis failed: {e}") from e
    text = reply if isinstance(reply, str) else "".join(reply)
    text = text.strip()
    if not text:
        raise VisionError("The vision model returned no readable content")
    return text
