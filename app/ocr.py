"""Optional OCR engine for scanned PDFs.

OCR is a capability, not a default: it runs only for pages that have no text
layer, and only when an engine is actually installed. The engine is Tesseract
via `pytesseract` (plus Pillow); if either is missing, `available()` is False and
ingestion keeps its existing behaviour (a scanned PDF is rejected with a clear
message).

Install to enable:
    brew install tesseract            # macOS (the binary)
    uv pip install pytesseract pillow # the Python bindings
"""

from __future__ import annotations

import io

from .config import OCR_LANG


class OcrError(Exception):
    """Raised when OCR is requested but unavailable or fails."""


def _pytesseract():
    """Return the pytesseract module if the engine + Pillow are usable."""
    try:
        import PIL.Image  # noqa: F401  (pytesseract renders through Pillow)
        import pytesseract
    except Exception:
        return None
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return None
    return pytesseract


def available() -> bool:
    return _pytesseract() is not None


def engine_name() -> str | None:
    return "tesseract" if available() else None


def ocr_image(png: bytes, lang: str = OCR_LANG) -> str:
    """OCR a PNG image and return the recognized text."""
    pytesseract = _pytesseract()
    if pytesseract is None:
        raise OcrError("No OCR engine available")
    try:
        from PIL import Image

        with Image.open(io.BytesIO(png)) as image:
            return pytesseract.image_to_string(image, lang=lang)
    except Exception as e:
        raise OcrError(f"OCR failed: {e}") from e
