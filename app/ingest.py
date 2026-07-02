"""Parse uploaded files and URLs into (page_number, text) segments."""
from pathlib import Path

import fitz  # PyMuPDF
import trafilatura

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


def parse_text_file(path: Path) -> list[tuple[int | None, str]]:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        raise IngestError("File is empty")
    return [(None, text)]


def parse_url(url: str) -> tuple[str, list[tuple[int | None, str]]]:
    """Returns (title, segments)."""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        raise IngestError(f"Could not fetch URL: {url}")
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
    if suffix in TEXT_SUFFIXES or suffix == "":
        return "text", None, parse_text_file(path)
    raise IngestError(f"Unsupported file type: {suffix} (supported: .pdf, {', '.join(sorted(TEXT_SUFFIXES))})")
