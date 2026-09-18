"""Parse uploaded files and URLs into (page_number, text) segments.

URL policy (offline-first): a URL is fetched exactly once, when the user
explicitly submits it for ingestion. Only http/https schemes are allowed
(no file:// or other local reads), with a hard timeout and size cap. The
extracted text is stored locally; nothing is re-fetched at runtime.
"""

import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import fitz  # PyMuPDF
import trafilatura
from docx import Document as DocxDocument

from . import ocr as _ocr
from .config import (
    CHUNK_CHARS,
    OCR_DPI,
    OCR_LANG,
    OCR_MIN_CHARS_PER_PAGE,
    URL_FETCH_TIMEOUT,
    URL_MAX_BYTES,
)
from .providers import get_stt

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json"}
HTML_SUFFIXES = {".html", ".htm"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
SHEET_SUFFIXES = {".xlsx", ".xlsm"}

# The formal ingestion contract. Kept executable (asserted by tests) so the
# documented matrix and the parser can't silently drift. OCR is deliberately
# NOT in this matrix — it is a future capability, never applied by default.
INGESTION_MATRIX = (
    {
        "kind": "pdf",
        "suffixes": (".pdf",),
        "metadata": "page number",
        "citation": "page",
        "retry": True,
    },
    {
        "kind": "docx",
        "suffixes": (".docx",),
        "metadata": "heading structure",
        "citation": "passage",
        "retry": True,
    },
    {
        "kind": "sheet",
        "suffixes": (".xlsx", ".xlsm"),
        "metadata": "sheet/row",
        "citation": "passage",
        "retry": True,
    },
    {
        "kind": "text",
        "suffixes": tuple(sorted(TEXT_SUFFIXES)),
        "metadata": "none",
        "citation": "passage",
        "retry": True,
    },
    {
        "kind": "html",
        "suffixes": tuple(sorted(HTML_SUFFIXES)),
        "metadata": "heading structure",
        "citation": "passage",
        "retry": True,
    },
    {"kind": "url", "suffixes": (), "metadata": "origin URL", "citation": "passage", "retry": True},
    {
        "kind": "audio",
        "suffixes": tuple(sorted(AUDIO_SUFFIXES)),
        "metadata": "timestamp",
        "citation": "seek",
        "retry": True,
    },
    {
        "kind": "video",
        "suffixes": tuple(sorted(VIDEO_SUFFIXES)),
        "metadata": "timestamp",
        "citation": "seek",
        "retry": True,
    },
)


class IngestError(Exception):
    """Recoverable ingestion failure — safe to show the user (HTTP 422)."""


class _HTMLText(HTMLParser):
    """Minimal tag stripper used when trafilatura finds no article content."""

    _SKIP = {"script", "style"}
    _BREAK = {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "section",
        "article",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def _html_to_text(raw: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(raw)
    except Exception:
        return ""
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def ocr_configured() -> bool:
    from .config import ocr_configured as _configured

    return _configured()


def ocr_available() -> bool:
    return _ocr.available()


def ocr_image(png: bytes, lang: str = OCR_LANG) -> str:
    return _ocr.ocr_image(png, lang)


def _ocr_pages(doc, pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """OCR only the pages with no text layer; text pages are left untouched."""
    out: list[tuple[int, str]] = []
    for index, text in pages:
        if text:
            out.append((index, text))
            continue
        try:
            pixmap = doc[index - 1].get_pixmap(dpi=OCR_DPI)
            out.append((index, ocr_image(pixmap.tobytes("png"), OCR_LANG).strip()))
        except Exception:
            out.append((index, ""))  # one bad page shouldn't fail the whole document
    return out


def parse_pdf(path: Path) -> list[tuple[int | None, str]]:
    segments: list[tuple[int | None, str]] = []
    try:
        with fitz.open(path) as doc:
            pages = [(i, page.get_text("text").strip()) for i, page in enumerate(doc, start=1)]
            total = sum(len(text) for _, text in pages)
            # OCR only when the text layer is insufficient and an engine exists.
            if (
                pages
                and ocr_configured()
                and ocr_available()
                and total < OCR_MIN_CHARS_PER_PAGE * len(pages)
            ):
                pages = _ocr_pages(doc, pages)
            segments = [(i, text) for i, text in pages if text]
    except IngestError:
        raise
    except Exception as e:
        raise IngestError(f"Could not read PDF: {e}") from e
    if not segments:
        raise IngestError("No extractable text found in PDF (is it scanned images?)")
    return segments


def parse_docx(path: Path) -> list[tuple[int | None, str]]:
    try:
        doc = DocxDocument(str(path))
        parts: list[str] = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            # keep heading structure visible to the chunker as paragraph breaks
            style_name = getattr(para.style, "name", "") or ""
            if style_name.startswith("Heading"):
                parts.append(f"\n{text}")
            else:
                parts.append(text)
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
    except IngestError:
        raise
    except Exception as e:
        raise IngestError(f"Could not read DOCX: {e}") from e
    text = "\n\n".join(parts).strip()
    if not text:
        raise IngestError("No extractable text found in DOCX")
    return [(None, text)]


def parse_html(path: Path) -> list[tuple[int | None, str]]:
    """Local HTML → readable text (trafilatura first, tag-stripper fallback)."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise IngestError(f"Could not read HTML file: {e}") from e
    text = trafilatura.extract(raw, include_comments=False)
    if not text or not text.strip():
        text = _html_to_text(raw)
    if not text or not text.strip():
        raise IngestError("No readable content found in HTML")
    return [(None, text.strip())]


def parse_sheet(path: Path) -> list[tuple[int | None, str]]:
    """XLSX/XLSM → one text block per sheet, rows as ' | '-joined lines."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(str(path), read_only=True, data_only=True)
    except Exception as e:
        raise IngestError(f"Could not read spreadsheet: {e}") from e
    parts: list[str] = []
    for ws in wb.worksheets:
        lines = []
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v).strip() for v in row]
            if any(cells):
                lines.append(" | ".join(cells).rstrip(" |"))
        if lines:
            parts.append(f"Sheet: {ws.title}\n" + "\n".join(lines))
    wb.close()
    if not parts:
        raise IngestError("Spreadsheet contains no data")
    return [(None, "\n\n".join(parts))]


def parse_media(path: Path) -> list[tuple[int | None, str]]:
    """Transcribe a video/audio file. Segments carry the start second of their
    first spoken words in the page slot, so citations can seek playback."""
    try:
        segments = get_stt().transcribe(str(path))
    except IngestError:
        raise
    except Exception as e:
        raise IngestError(f"Transcription failed: {e}") from e
    if not segments:
        raise IngestError("No speech detected in this file")
    # pack short whisper segments into ~chunk-sized blocks, keeping the
    # start time of each block's first segment
    out: list[tuple[int | None, str]] = []
    buf, buf_start = "", 0
    for seg in segments:
        if not buf:
            buf_start = int(seg["start"])
        candidate = f"{buf} {seg['text']}".strip()
        if len(candidate) > CHUNK_CHARS and buf:
            out.append((buf_start, buf))
            buf, buf_start = seg["text"], int(seg["start"])
        else:
            buf = candidate
    if buf:
        out.append((buf_start, buf))
    return out


def parse_text_file(path: Path) -> list[tuple[int | None, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as e:
        raise IngestError(f"Could not read file: {e}") from e
    if not text:
        raise IngestError("File is empty")
    return [(None, text)]


def _fetch_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise IngestError("Only http:// and https:// URLs can be ingested")
    if not parsed.netloc:
        raise IngestError(f"Not a valid URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "SkullMasterLocal/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=URL_FETCH_TIMEOUT) as resp:
            body = resp.read(URL_MAX_BYTES + 1)
    except IngestError:
        raise
    except Exception as e:
        raise IngestError(f"Could not fetch URL: {e}") from e
    if len(body) > URL_MAX_BYTES:
        raise IngestError(f"Page exceeds the {URL_MAX_BYTES // (1024 * 1024)}MB fetch limit")
    return body.decode("utf-8", errors="replace")


def parse_url(url: str) -> tuple[str, list[tuple[int | None, str]]]:
    """Returns (title, segments)."""
    downloaded = _fetch_url(url)
    text = trafilatura.extract(downloaded, include_comments=False)
    if not text or not text.strip():
        raise IngestError(f"No readable article content extracted from: {url}")
    meta = trafilatura.extract_metadata(downloaded)
    title = meta.title if meta and meta.title else url
    return title, [(None, text.strip())]


def parse_file(path: Path) -> tuple[str, int | None, list[tuple[int | None, str]]]:
    """Returns (kind, pages, segments) for an uploaded file.

    For video/audio the segments' page slot holds the start second of each
    transcript block instead of a page number.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        segments = parse_pdf(path)
        return "pdf", segments[-1][0], segments
    if suffix == ".docx":
        return "docx", None, parse_docx(path)
    if suffix in SHEET_SUFFIXES:
        return "sheet", None, parse_sheet(path)
    if suffix in VIDEO_SUFFIXES:
        return "video", None, parse_media(path)
    if suffix in AUDIO_SUFFIXES:
        return "audio", None, parse_media(path)
    if suffix in HTML_SUFFIXES:
        return "html", None, parse_html(path)
    if suffix in TEXT_SUFFIXES or suffix == "":
        return "text", None, parse_text_file(path)
    supported = ".pdf, .docx, " + ", ".join(
        sorted(TEXT_SUFFIXES | HTML_SUFFIXES | SHEET_SUFFIXES | VIDEO_SUFFIXES | AUDIO_SUFFIXES)
    )
    raise IngestError(f"Unsupported file type: {suffix} (supported: {supported})")
