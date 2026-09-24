"""Parse uploaded files and URLs into (page_number, text) segments.

URL policy (offline-first): a URL is fetched exactly once, when the user
explicitly submits it for ingestion. Only http/https schemes are allowed
(no file:// or other local reads), with a hard timeout and size cap. The
extracted text is stored locally; nothing is re-fetched at runtime.
"""

import json
import logging
import re
import shutil
import tempfile
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

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

log = logging.getLogger(__name__)

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json"}
HTML_SUFFIXES = {".html", ".htm"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
SHEET_SUFFIXES = {".xlsx", ".xlsm"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}

# The formal ingestion contract. Kept executable (asserted by tests) so the
# documented matrix and the parser can't silently drift. OCR and vision are
# opt-in enhancements layered on PDF/image parsing, not separate kinds.
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
    {
        "kind": "url",
        "suffixes": (),
        "metadata": "origin URL",
        "citation": "passage",
        "retry": True,
    },
    {
        "kind": "youtube",
        "suffixes": (),
        "metadata": "origin URL + timestamp",
        "citation": "seek",
        "retry": True,
    },
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
    {
        "kind": "image",
        "suffixes": tuple(sorted(IMAGE_SUFFIXES)),
        "metadata": "none (vision description)",
        "citation": "passage",
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


def vision_available() -> bool:
    from . import vision as _vision

    return _vision.available()


def analyze_image(data: bytes) -> str:
    from . import vision as _vision

    return _vision.analyze_image(data)


def parse_image(path: Path) -> list[tuple[int | None, str]]:
    """Describe/transcribe a standalone image with the vision model."""
    try:
        data = path.read_bytes()
    except OSError as e:
        raise IngestError(f"Could not read image: {e}") from e
    if not data:
        raise IngestError("File is empty")
    try:
        text = analyze_image(data)
    except Exception as e:
        raise IngestError(f"Image analysis failed: {e}") from e
    if not text.strip():
        raise IngestError("No readable content found in image")
    return [(None, text.strip())]


def _pdf_image_segments(doc, limit: int = 5, min_px: int = 256) -> list[tuple[int, str]]:
    """Caption/diagram text for embedded images (bounded, vision only)."""
    out: list[tuple[int, str]] = []
    if not vision_available():
        return out
    for page_number, page in enumerate(doc, start=1):
        for image in page.get_images(full=True):
            xref, width, height = image[0], image[2], image[3]
            if width < min_px or height < min_px:
                continue
            try:
                data = doc.extract_image(xref)["image"]
                text = analyze_image(data).strip()
            except Exception:
                continue  # one unreadable image shouldn't fail the document
            if text:
                out.append((page_number, f"Image on page {page_number}: {text}"))
            if len(out) >= limit:
                return out
    return out


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
            # OCR empty pages regardless of document-level text density.
            # The density threshold also catches documents whose text layer is
            # broadly insufficient, while _ocr_pages preserves text pages.
            needs_ocr = any(not text for _, text in pages) or (
                pages and total < OCR_MIN_CHARS_PER_PAGE * len(pages)
            )
            if pages and ocr_configured() and ocr_available() and needs_ocr:
                pages = _ocr_pages(doc, pages)
            segments = [(i, text) for i, text in pages if text]
            segments += _pdf_image_segments(doc)  # diagrams/charts, if vision is on
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


def _pack_transcript(segments: list[dict]) -> list[tuple[int | None, str]]:
    """Pack short STT/caption segments into ~chunk-sized blocks, keeping the
    start second of each block's first segment in the page slot so citations can
    seek playback. Shared by uploaded media and the YouTube fallback."""
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
    return _pack_transcript(segments)


def parse_text_file(path: Path) -> list[tuple[int | None, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as e:
        raise IngestError(f"Could not read file: {e}") from e
    if not text:
        raise IngestError("File is empty")
    return [(None, text)]


# ---- YouTube ----
# A YouTube watch page is a JavaScript app shell: scraping it yields only the
# site chrome ("About · Press · Copyright…"), never the video. So a YouTube URL
# takes the caption-track route instead, and the general URL path refuses pages
# that produce only boilerplate rather than silently ingesting junk.
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
}
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Below this many characters, "readable article content" is almost always
# navigation/footer chrome, not the page's substance.
MIN_ARTICLE_CHARS = 200
YOUTUBE_BLOCK_CHARS = 900
YOUTUBE_BLOCK_SECONDS = 45


def youtube_video_id(url: str) -> str | None:
    """The 11-character video id for any common YouTube URL form, else None."""
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = (parsed.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        return None
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif parsed.path.rstrip("/") in ("/watch", ""):
        candidate = parse_qs(parsed.query).get("v", [""])[0]
    else:
        parts = [p for p in parsed.path.split("/") if p]
        candidate = (
            parts[1] if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v") else ""
        )
    return candidate if _YOUTUBE_ID.match(candidate) else None


def is_youtube_url(url: str) -> bool:
    return youtube_video_id(url) is not None


def _youtube_snippet(snippet) -> tuple[float, str]:
    """(start_seconds, text) from either the dict (v0.x) or object (v1.x) shape."""
    if isinstance(snippet, dict):
        return float(snippet.get("start", 0) or 0), str(snippet.get("text", "") or "")
    return float(getattr(snippet, "start", 0) or 0), str(getattr(snippet, "text", "") or "")


def _youtube_title(url: str, video_id: str) -> str:
    """Title/author via YouTube's oEmbed endpoint (no API key, no scraping)."""
    try:
        oembed = f"https://www.youtube.com/oembed?format=json&url={quote(url, safe='')}"
        data = json.loads(_fetch_url(oembed))
        title = str(data.get("title") or "").strip()
        author = str(data.get("author_name") or "").strip()
        if title:
            return f"{title} — {author}" if author else title
    except Exception:
        pass
    return f"YouTube video {video_id}"


def youtube_fallback_configured() -> bool:
    """Whether the captions-free audio-transcription fallback may run."""
    from .config import youtube_fallback_configured as _configured

    return _configured()


def _youtube_caption_segments(video_id: str) -> list[tuple[int | None, str]]:
    """The video's caption track as timestamped blocks (raises when absent)."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except Exception as e:  # pragma: no cover - dependency is declared
        raise IngestError("YouTube transcripts need the 'youtube-transcript-api' package") from e
    try:
        snippets = list(YouTubeTranscriptApi().fetch(video_id))
    except Exception as e:
        raise IngestError(
            f"Could not fetch captions for this YouTube video ({e}). It may have "
            "captions disabled, be private, or be region-blocked."
        ) from e
    if not snippets:
        raise IngestError("YouTube returned an empty transcript for this video")

    segments: list[tuple[int | None, str]] = []
    buffer: list[str] = []
    block_start = 0
    for snippet in snippets:
        start, text = _youtube_snippet(snippet)
        text = " ".join(text.split())
        if not text:
            continue
        # A gap in speech starts a NEW block: the snippet after the gap begins
        # it (and keeps its own timestamp) instead of joining the prior block.
        if buffer and (int(start) - block_start) >= YOUTUBE_BLOCK_SECONDS:
            segments.append((block_start, " ".join(buffer)))
            buffer = []
        if not buffer:
            block_start = int(start)
        buffer.append(text)
        if len(" ".join(buffer)) >= YOUTUBE_BLOCK_CHARS:
            segments.append((block_start, " ".join(buffer)))
            buffer = []
    if buffer:
        segments.append((block_start, " ".join(buffer)))
    if not segments:
        raise IngestError("YouTube transcript contained no usable text")
    return segments


def _youtube_download_audio(url: str, video_id: str, max_minutes: int) -> Path:
    """Download just the audio track with yt-dlp into a fresh temp dir.

    Duration is checked before downloading and playlists are never expanded, so a
    link to a 3-hour stream or a playlist cannot silently pull gigabytes.
    """
    try:
        from yt_dlp import YoutubeDL
    except Exception as e:
        raise IngestError("Transcribing a video without captions needs the 'yt-dlp' package") from e
    workdir = Path(tempfile.mkdtemp(prefix="skullmaster-youtube-"))
    options = {
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,  # a playlist URL must not pull the playlist
        "socket_timeout": 30,
        "retries": 2,
    }
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False) or {}
            duration = int(info.get("duration") or 0)
            if duration and duration > max_minutes * 60:
                raise IngestError(
                    f"This video is {duration // 60} minutes long; the transcription "
                    f"fallback is capped at {max_minutes} minutes "
                    "(raise YOUTUBE_MAX_MINUTES to allow longer videos)."
                )
            ydl.download([url])
    except IngestError:
        raise
    except Exception as e:
        raise IngestError(f"Could not download the video's audio: {e}") from e
    files = [
        p for p in workdir.iterdir() if p.is_file() and not p.name.endswith((".part", ".ytdl"))
    ]
    if not files:
        raise IngestError("yt-dlp produced no audio file")
    return max(files, key=lambda p: p.stat().st_size)


def _youtube_transcribe_audio(url: str, video_id: str) -> list[tuple[int | None, str]]:
    """Captions-free fallback: download the audio, transcribe it locally.

    Uses the same local Whisper pipeline as uploaded media, so the video's
    spoken content becomes a timestamped, citable source. The temporary download
    is deleted immediately afterwards — only the transcript is kept.
    """
    from .config import YOUTUBE_MAX_MINUTES

    log.info("No captions for %s — downloading audio to transcribe locally", video_id)
    workdir: Path | None = None
    try:
        audio = _youtube_download_audio(url, video_id, YOUTUBE_MAX_MINUTES)
        workdir = audio.parent
        segments = get_stt().transcribe(str(audio))
    except IngestError:
        raise
    except Exception as e:
        raise IngestError(f"Could not transcribe the downloaded audio: {e}") from e
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)
    if not segments:
        raise IngestError("No speech detected in this video's audio")
    return _pack_transcript(segments)


def parse_youtube(url: str) -> tuple[str, list[tuple[int | None, str]]]:
    """Pull a YouTube video's spoken content as a timestamped transcript.

    The caption track is used first (fast, no download; manual and auto-generated
    both count). When a video has no captions, the audio is downloaded with
    yt-dlp and transcribed locally by the same Whisper pipeline used for uploaded
    media — unless the fallback is disabled (YOUTUBE_TRANSCRIBE_FALLBACK=false).
    """
    video_id = youtube_video_id(url)
    if not video_id:
        raise IngestError(f"Not a YouTube video URL: {url}")
    title = _youtube_title(url, video_id)
    try:
        return title, _youtube_caption_segments(video_id)
    except IngestError as caption_error:
        if not youtube_fallback_configured():
            raise IngestError(
                f"{caption_error} (audio-transcription fallback disabled: set "
                "YOUTUBE_TRANSCRIBE_FALLBACK=auto to enable it)"
            ) from caption_error
        log.info("YouTube captions unavailable (%s); using audio transcription", caption_error)
        return title, _youtube_transcribe_audio(url, video_id)


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
    """Returns (title, segments).

    YouTube URLs return a timestamped caption transcript; everything else is
    fetched once and reduced to readable article text.
    """
    if is_youtube_url(url):
        return parse_youtube(url)
    downloaded = _fetch_url(url)
    text = (trafilatura.extract(downloaded, include_comments=False) or "").strip()
    # A JavaScript-rendered page (video sites, app shells, sign-in walls) yields
    # only chrome. Ingesting that as a "source" would let the model cite
    # boilerplate, so fail honestly instead.
    if len(text) < MIN_ARTICLE_CHARS:
        raise IngestError(
            "No readable article content extracted from this URL — it may be a "
            "JavaScript-rendered page, a video, or require sign-in."
        )
    meta = trafilatura.extract_metadata(downloaded)
    title = meta.title if meta and meta.title else url
    return title, [(None, text)]


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
    if suffix in IMAGE_SUFFIXES:
        return "image", None, parse_image(path)
    if suffix in TEXT_SUFFIXES or suffix == "":
        return "text", None, parse_text_file(path)
    supported = ".pdf, .docx, " + ", ".join(
        sorted(
            TEXT_SUFFIXES
            | HTML_SUFFIXES
            | SHEET_SUFFIXES
            | VIDEO_SUFFIXES
            | AUDIO_SUFFIXES
            | IMAGE_SUFFIXES
        )
    )
    raise IngestError(f"Unsupported file type: {suffix} (supported: {supported})")
