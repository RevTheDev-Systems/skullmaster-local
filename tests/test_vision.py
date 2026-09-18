"""Vision-document understanding: image sources and PDF-embedded images."""

import io

import fitz
import pytest

from app import ingest, vision


def _png_bytes(width=420, height=320) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.insert_text((30, 60), "Revenue chart", fontsize=20)
    png = page.get_pixmap(dpi=72).tobytes("png")
    doc.close()
    return png


def _pdf_with_embedded_image(path):
    doc = fitz.open()
    page = doc.new_page(width=600, height=440)
    page.insert_image(fitz.Rect(20, 20, 440, 340), stream=_png_bytes())
    doc.save(str(path))
    doc.close()
    return path


# ---------- vision module ----------


def test_vision_available_with_capable_provider(mock_llm):
    assert vision.available() is True
    assert vision.analyze_image(b"\x89PNG fake").startswith("Mock image")


def test_vision_reports_missing_model(monkeypatch):
    class _NoVision:
        def route_plan(self, capability="chat", **kw):
            return {"model": None}

    with pytest.raises(vision.VisionError):
        vision.analyze_image(b"x", llm=_NoVision())


# ---------- image ingestion ----------


def test_parse_image_uses_vision(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "analyze_image", lambda data: "Bar chart of revenue.")
    segments = ingest.parse_image(_write(tmp_path / "chart.png"))
    assert segments == [(None, "Bar chart of revenue.")]


def test_parse_file_routes_images(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "analyze_image", lambda data: "Diagram text.")
    kind, pages, segments = ingest.parse_file(_write(tmp_path / "d.png"))
    assert kind == "image" and pages is None and segments[0][1] == "Diagram text."


def test_image_ingestion_fails_closed_when_vision_unavailable(tmp_path, monkeypatch):
    def boom(data):
        raise RuntimeError("no vision model")

    monkeypatch.setattr(ingest, "analyze_image", boom)
    with pytest.raises(ingest.IngestError, match="Image analysis failed"):
        ingest.parse_file(_write(tmp_path / "d.png"))


# ---------- PDF embedded images ----------


def test_pdf_embedded_images_are_described(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "vision_available", lambda: True)
    monkeypatch.setattr(ingest, "analyze_image", lambda data: "A revenue bar chart.")
    segments = ingest.parse_pdf(_pdf_with_embedded_image(tmp_path / "img.pdf"))
    assert any("Image on page 1" in text for _, text in segments)


def test_pdf_images_skipped_without_vision(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "vision_available", lambda: False)
    called = []
    monkeypatch.setattr(ingest, "analyze_image", lambda data: called.append(1) or "x")
    with pytest.raises(ingest.IngestError, match="scanned"):
        ingest.parse_pdf(_pdf_with_embedded_image(tmp_path / "img.pdf"))
    assert called == []


# ---------- API ----------


def test_image_upload_is_indexed_and_served(client, notebook):
    import json

    res = client.post(
        f"/api/notebooks/{notebook['id']}/sources/file",
        files={"file": ("chart.png", io.BytesIO(_png_bytes()), "image/png")},
    )
    assert res.status_code == 200, res.text
    source = res.json()
    assert source["kind"] == "image"

    sources = client.get(f"/api/notebooks/{notebook['id']}/sources").json()
    assert sources[0]["kind"] == "image"

    media = client.get(f"/api/media/{source['id']}")
    assert media.status_code == 200
    assert media.headers["content-type"].startswith("image/")

    chat = client.post(
        f"/api/notebooks/{notebook['id']}/chat",
        json={"question": "What does the chart show?", "history": []},
    )
    assert "event: sources" in chat.text and "event: done" in chat.text
    line = next(line for line in chat.text.splitlines() if line.startswith("data: ["))
    payload = json.loads(line.removeprefix("data: "))
    assert payload[0]["kind"] == "image"


def _write(path):
    path.write_bytes(_png_bytes())
    return path
