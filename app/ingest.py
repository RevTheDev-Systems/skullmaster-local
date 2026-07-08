"""Parse uploaded files and URLs into (page_number, text) segments.

URL policy (offline-first): a URL is fetched exactly once, when the user
explicitly submits it for ingestion. Only http/https schemes are allowed
(no file:// or other local reads), with a hard timeout and size cap. The
extracted text is stored locally; nothing is re-fetched at runtime.
"""
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import fitz  # PyMuPDF
import trafilatura
from docx import Document as DocxDocument

from .config import CHUNK_CHARS, URL_FETCH_TIMEOUT, URL_MAX_BYTES
from .providers import get_stt

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".html", ".htm"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
SHEET_SUFFIXES = {".xlsx", ".xlsm"}


class IngestError(Exception):
    pass


def parse_pdf(path: Path) -> list[tuple[int, str]]:
    segments = []
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if text:
                segments.append((i, text))
    if not segments:
        raise IngestError("No extractable text found in PDF (is it scanned images?)")
    return segments


def parse_docx(path: Path) -> list[tuple[int | None, str]]:
    doc = DocxDocument(str(path))
    parts: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        # keep heading structure visible to the chunker as paragraph breaks
        if para.style.name.startswith("Heading"):
            parts.append(f"\n{text}")
        else:
            parts.append(text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    text = "\n\n".join(parts).strip()
    if not text:
        raise IngestError("No extractable text found in DOCX")
    return [(None, text)]


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


def parse_media(path: Path) -> list[tuple[int, str]]:
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
    out: list[tuple[int, str]] = []
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
    text = path.read_text(encoding="utf-8", errors="replace").strip()
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
    title = (meta.title if meta and meta.title else url)
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
    if suffix in TEXT_SUFFIXES or suffix == "":
        return "text", None, parse_text_file(path)
    supported = ".pdf, .docx, " + ", ".join(sorted(
        TEXT_SUFFIXES | SHEET_SUFFIXES | VIDEO_SUFFIXES | AUDIO_SUFFIXES))
    raise IngestError(f"Unsupported file type: {suffix} (supported: {supported})")
