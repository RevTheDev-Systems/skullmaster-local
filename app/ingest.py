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

from .config import URL_FETCH_TIMEOUT, URL_MAX_BYTES

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".html", ".htm"}


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
    """Returns (kind, pages, segments) for an uploaded file."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        segments = parse_pdf(path)
        return "pdf", segments[-1][0], segments
    if suffix == ".docx":
        return "docx", None, parse_docx(path)
    if suffix in TEXT_SUFFIXES or suffix == "":
        return "text", None, parse_text_file(path)
    raise IngestError(
        f"Unsupported file type: {suffix} (supported: .pdf, .docx, {', '.join(sorted(TEXT_SUFFIXES))})"
    )
