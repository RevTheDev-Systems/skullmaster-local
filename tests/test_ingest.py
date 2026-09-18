"""Ingestion matrix: positive and negative fixtures for every supported type.

Fixtures are generated on the fly (no binaries committed) so each source type
has both a valid sample and a deliberately broken sample, and the documented
matrix is asserted against the parser.
"""

import urllib.error

import fitz
import pytest
from docx import Document as DocxDocument
from openpyxl import Workbook

from app import ingest
from app.chunker import chunk_segments

# ---------- fixture builders ----------


def _txt(path, text="Hello text content."):
    path.write_text(text, encoding="utf-8")
    return path


def _pdf(path, pages=("Hello from page one.", "Page two has more content.")):
    doc = fitz.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return path


def _blank_pdf(path):
    doc = fitz.open()
    doc.new_page()  # page with no text layer (i.e. a scan)
    doc.save(str(path))
    doc.close()
    return path


def _docx(path, text="Hello DOCX world."):
    doc = DocxDocument()
    doc.add_heading("Title", level=1)
    doc.add_paragraph(text)
    doc.save(str(path))
    return path


def _xlsx(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Name", "Value"])
    ws.append(["alpha", 1])
    ws.append(["beta", 2])
    wb.create_sheet("Empty")
    wb.save(str(path))
    return path


class _FakeSTT:
    def __init__(self, segments=None, error=None):
        self.segments = segments or []
        self.error = error

    def transcribe(self, path):
        if self.error:
            raise self.error
        return self.segments


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, n):
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---------- matrix ----------


def test_ingestion_matrix_covers_required_kinds():
    kinds = {row["kind"] for row in ingest.INGESTION_MATRIX}
    assert {"pdf", "docx", "sheet", "text", "html", "url", "audio", "video"} <= kinds
    for row in ingest.INGESTION_MATRIX:
        assert row["citation"] in ("page", "passage", "seek")
        assert row["retry"] is True
    assert "ocr" not in kinds  # OCR is a future capability, not default


@pytest.mark.parametrize(
    "kind,ext,builder,expected_pages",
    [
        ("pdf", ".pdf", _pdf, 2),
        ("docx", ".docx", _docx, None),
        ("sheet", ".xlsx", _xlsx, None),
        ("text", ".txt", _txt, None),
        ("text", ".md", _txt, None),
        (
            "html",
            ".html",
            lambda p: _txt(p, "<html><body><h1>H</h1><p>Body words here.</p></body></html>"),
            None,
        ),
    ],
)
def test_supported_source_positives(tmp_path, kind, ext, builder, expected_pages):
    path = builder(tmp_path / f"sample{ext}")
    parsed_kind, pages, segments = ingest.parse_file(path)
    assert parsed_kind == kind
    assert pages == expected_pages
    assert segments
    assert any(text.strip() for _, text in segments)


# ---------- negatives: broken/empty/unsupported ----------


def test_zero_byte_and_corrupt_files_raise_ingest_error(tmp_path):
    cases = {
        "empty.txt": b"",
        "empty.pdf": b"",
        "corrupt.pdf": b"%PDF-1.4 this is not really a pdf",
        "corrupt.docx": b"PK\x03\x04 not actually a docx",
        "corrupt.xlsx": b"this is not a zip archive",
    }
    for name, data in cases.items():
        path = tmp_path / name
        path.write_bytes(data)
        with pytest.raises(ingest.IngestError):
            ingest.parse_file(path)


def test_unsupported_extension_raises(tmp_path):
    with pytest.raises(ingest.IngestError, match="Unsupported file type"):
        ingest.parse_file(_txt(tmp_path / "malware.exe", "MZ"))


def test_image_only_pdf_requires_ocr(tmp_path):
    with pytest.raises(ingest.IngestError, match="scanned"):
        ingest.parse_pdf(_blank_pdf(tmp_path / "scan.pdf"))


# ---------- OCR (optional engine, mocked) ----------


def test_scanned_pdf_is_ocrd_when_engine_available(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(ingest, "ocr_configured", lambda: True)
    monkeypatch.setattr(ingest, "ocr_available", lambda: True)
    monkeypatch.setattr(
        ingest, "ocr_image", lambda png, lang: called.append(lang) or "Recovered scan text."
    )
    segments = ingest.parse_pdf(_blank_pdf(tmp_path / "scan.pdf"))
    assert called
    assert "Recovered scan text." in segments[0][1]


def test_text_pdf_is_never_ocrd(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(ingest, "ocr_available", lambda: True)
    monkeypatch.setattr(ingest, "ocr_image", lambda png, lang: called.append(1) or "should not run")
    ingest.parse_pdf(_pdf(tmp_path / "text.pdf"))
    assert called == []


def test_ocr_runs_only_on_empty_pages(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(ingest, "ocr_configured", lambda: True)
    monkeypatch.setattr(ingest, "ocr_available", lambda: True)
    monkeypatch.setattr(
        ingest, "ocr_image", lambda png, lang: called.append(1) or "page two recovered"
    )

    path = tmp_path / "mixed.pdf"
    doc = fitz.open()
    native_text = "Page one has real text. " * 10
    doc.new_page().insert_text((72, 72), native_text)
    doc.new_page()  # blank page (no text layer)
    doc.save(str(path))
    doc.close()

    segments = ingest.parse_pdf(path)
    assert len(called) == 1
    text = "\n".join(t for _, t in segments)
    assert "Page one has real text." in text and "page two recovered" in text


def test_scanned_pdf_rejected_when_ocr_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "ocr_configured", lambda: False)
    with pytest.raises(ingest.IngestError, match="scanned"):
        ingest.parse_pdf(_blank_pdf(tmp_path / "scan.pdf"))


def test_empty_spreadsheet_raises(tmp_path):
    wb = Workbook()
    path = tmp_path / "empty.xlsx"
    wb.save(str(path))
    with pytest.raises(ingest.IngestError, match="no data"):
        ingest.parse_file(path)


# ---------- HTML parsing ----------


def test_html_is_parsed_not_raw(tmp_path):
    path = _txt(
        tmp_path / "p.html",
        "<html><body><script>evil()</script><h1>Head</h1><p>Body words.</p></body></html>",
    )
    kind, _, segments = ingest.parse_file(path)
    text = segments[0][1]
    assert kind == "html"
    assert "Body words." in text
    assert "<" not in text and "evil" not in text


# ---------- Unicode ----------


def test_unicode_content_is_preserved(tmp_path):
    text = "Données — 日本語 — Ω — satellite 🛰️"
    kind, _, segments = ingest.parse_file(_txt(tmp_path / "uni.txt", text))
    assert kind == "text"
    assert "日本語" in segments[0][1] and "🛰️" in segments[0][1]

    _, _, docx_segments = ingest.parse_file(_docx(tmp_path / "uni.docx", "Résumé — données"))
    assert "Résumé" in docx_segments[0][1]


def test_unusual_spreadsheet_values(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Wéird"
    ws.append(["A", None, "C"])
    ws.append([1, 2, 3])
    ws.merge_cells("A3:B3")
    ws["A3"] = "merged"
    ws.append([])
    path = tmp_path / "odd.xlsx"
    wb.save(str(path))

    _, _, segments = ingest.parse_file(path)
    text = segments[0][1]
    assert "Wéird" in text and "merged" in text and "C" in text


# ---------- large inputs ----------


def test_large_document_chunks_without_error(tmp_path):
    big = "\n\n".join(f"Paragraph {i} " + "lorem ipsum dolor " * 20 for i in range(600))
    _, _, segments = ingest.parse_file(_txt(tmp_path / "big.txt", big))
    chunks = chunk_segments(segments)
    assert len(chunks) > 20
    assert all(c["text"].strip() for c in chunks)


# ---------- media (STT mocked) ----------


def test_media_timestamps_and_packing(tmp_path, monkeypatch):
    long_text = "sensor array telemetry " * 250  # > CHUNK_CHARS
    monkeypatch.setattr(
        ingest,
        "get_stt",
        lambda: _FakeSTT(
            [
                {"start": 0.0, "end": 5.0, "text": long_text},
                {"start": 42, "end": 50.0, "text": long_text},
            ]
        ),
    )
    kind, pages, segments = ingest.parse_file(_txt(tmp_path / "clip.mp4", "x"))
    assert kind == "video" and pages is None
    assert [sec for sec, _ in segments] == [0, 42]  # start seconds preserved


def test_media_no_speech_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "get_stt", lambda: _FakeSTT([]))
    with pytest.raises(ingest.IngestError, match="No speech"):
        ingest.parse_file(_txt(tmp_path / "silent.wav", "x"))


def test_media_transcription_failure_is_wrapped(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "get_stt", lambda: _FakeSTT(error=RuntimeError("whisper boom")))
    with pytest.raises(ingest.IngestError, match="Transcription failed"):
        ingest.parse_file(_txt(tmp_path / "bad.mp3", "x"))


# ---------- URL ----------


def test_url_rejects_non_http_schemes():
    for url in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)"):
        with pytest.raises(ingest.IngestError):
            ingest.parse_url(url)


def test_url_timeout_and_redirect_loop_are_wrapped(monkeypatch):
    def timeout(*a, **k):
        raise TimeoutError("timed out")

    monkeypatch.setattr(ingest.urllib.request, "urlopen", timeout)
    with pytest.raises(ingest.IngestError, match="Could not fetch"):
        ingest.parse_url("https://example.com/a")

    def redirect_loop(*a, **k):
        raise urllib.error.HTTPError("https://x", 302, "too many redirects", None, None)

    monkeypatch.setattr(ingest.urllib.request, "urlopen", redirect_loop)
    with pytest.raises(ingest.IngestError, match="Could not fetch"):
        ingest.parse_url("https://example.com/loop")


def test_url_oversize_body_rejected(monkeypatch):
    big = b"x" * (ingest.URL_MAX_BYTES + 10)
    monkeypatch.setattr(ingest.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(big))
    with pytest.raises(ingest.IngestError, match="exceed"):
        ingest.parse_url("https://example.com/huge")


def test_url_without_readable_content_raises(monkeypatch):
    monkeypatch.setattr(
        ingest.urllib.request,
        "urlopen",
        lambda *a, **k: _FakeResponse(b"<html><body></body></html>"),
    )
    with pytest.raises(ingest.IngestError, match="No readable"):
        ingest.parse_url("https://example.com/empty")


def test_url_positive(monkeypatch):
    html = (
        "<html><head><title>My Article</title></head><body>"
        + "".join(f"<p>Paragraph {i} about the Atlas sensor network.</p>" for i in range(20))
        + "</body></html>"
    ).encode()
    monkeypatch.setattr(ingest.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(html))
    title, segments = ingest.parse_url("https://example.com/article")
    assert title == "My Article"
    assert "Atlas sensor network" in segments[0][1]
