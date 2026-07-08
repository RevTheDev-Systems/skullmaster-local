"""Video/audio sources and Studio artifacts (chart / infographic / spreadsheet)."""
import io
import json
from pathlib import Path

import pytest

from app import db, store, studio
from app.config import ARTIFACTS_DIR
from app.rag import format_timestamp


def _upload(client, nb_id, name, content, mime="application/octet-stream"):
    return client.post(
        f"/api/notebooks/{nb_id}/sources/file",
        files={"file": (name, io.BytesIO(content), mime)},
    )


# ---------- video / audio sources ----------

def test_video_upload_transcribed_and_indexed(client, notebook):
    res = _upload(client, notebook["id"], "talk.mp4", b"fake-video-bytes", "video/mp4")
    assert res.status_code == 200, res.text
    src = res.json()
    assert src["kind"] == "video" and src["status"] == "ready"
    chunks = store.notebook_chunks(notebook["id"])
    assert len(chunks) == 1
    assert "wombat" in chunks[0]["text"] and "42 tugrik" in chunks[0]["text"]
    assert chunks[0]["page"] == 0  # transcript block start second


def test_audio_upload_kind(client, notebook):
    res = _upload(client, notebook["id"], "talk.mp3", b"fake-audio", "audio/mpeg")
    assert res.json()["kind"] == "audio"


def test_media_endpoint_serves_file(client, notebook):
    src = _upload(client, notebook["id"], "talk.mp4", b"fake-video-bytes", "video/mp4").json()
    res = client.get(f"/api/media/{src['id']}")
    assert res.status_code == 200
    assert res.headers["content-type"] == "video/mp4"
    assert res.content == b"fake-video-bytes"
    # range requests supported for playback seeking
    partial = client.get(f"/api/media/{src['id']}", headers={"Range": "bytes=0-3"})
    assert partial.status_code == 206 and partial.content == b"fake"
    assert client.get("/api/media/nonexistent").status_code == 404


def test_video_citation_carries_kind_and_timestamp(client, notebook):
    _upload(client, notebook["id"], "talk.mp4", b"fake-video-bytes", "video/mp4")
    res = client.post(f"/api/notebooks/{notebook['id']}/chat",
                      json={"question": "What do tickets cost?", "history": []})
    sources_line = [l for l in res.text.splitlines() if l.startswith("data: [")][0]
    payload = json.loads(sources_line.removeprefix("data: "))
    assert payload[0]["kind"] == "video"
    assert payload[0]["page"] == 0
    msgs = client.get(f"/api/notebooks/{notebook['id']}/messages").json()
    assert msgs[1]["citations"][0]["kind"] == "video"


def test_format_timestamp():
    assert format_timestamp(0) == "0:00"
    assert format_timestamp(75) == "1:15"
    assert format_timestamp(3671) == "1:01:11"


# ---------- spreadsheet ingestion ----------

def test_xlsx_upload_indexed(client, notebook):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["City", "Population"])
    ws.append(["Ulan Bator", 1645000])
    buf = io.BytesIO()
    wb.save(buf)
    res = _upload(client, notebook["id"], "cities.xlsx", buf.getvalue())
    assert res.status_code == 200, res.text
    assert res.json()["kind"] == "sheet"
    chunks = store.notebook_chunks(notebook["id"])
    assert "Ulan Bator | 1645000" in chunks[0]["text"]
    assert "Sheet: Data" in chunks[0]["text"]


# ---------- studio artifacts ----------

def _seed(client, nb_id):
    _upload(client, nb_id, "facts.txt",
            b"The festival happens in March. Tickets cost 42 tugrik.", "text/plain")


@pytest.mark.parametrize("kind,title", [
    ("chart", "Mock Chart"),
    ("infographic", "Mock Infographic"),
    ("spreadsheet", "Mock Table"),
])
def test_artifact_generation(client, notebook, kind, title):
    _seed(client, notebook["id"])
    res = client.post(f"/api/notebooks/{notebook['id']}/artifacts", json={"kind": kind})
    assert res.status_code == 200, res.text
    art = res.json()
    assert art["title"] == title and art["kind"] == kind
    assert art["spec"]["source_note"]

    listed = client.get(f"/api/notebooks/{notebook['id']}/artifacts").json()
    assert len(listed) == 1

    if kind == "spreadsheet":
        assert art["file_url"]
        f = client.get(art["file_url"])
        assert f.status_code == 200
        assert "spreadsheetml" in f.headers["content-type"]
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(f.content))
        rows = list(wb.active.iter_rows(values_only=True))
        assert rows[0] == ("Name", "Value") and rows[1] == ("a", 1)

    res = client.delete(f"/api/notebooks/{notebook['id']}/artifacts/{art['id']}")
    assert res.json() == {"ok": True}
    assert client.get(f"/api/notebooks/{notebook['id']}/artifacts").json() == []
    if kind == "spreadsheet":
        assert not any(ARTIFACTS_DIR.glob(f"{notebook['id']}_*.xlsx"))


def test_artifact_unknown_kind_400(client, notebook):
    _seed(client, notebook["id"])
    assert client.post(f"/api/notebooks/{notebook['id']}/artifacts",
                       json={"kind": "hologram"}).status_code == 400


def test_artifact_empty_notebook_422(client, notebook):
    assert client.post(f"/api/notebooks/{notebook['id']}/artifacts",
                       json={"kind": "chart"}).status_code == 422


def test_notebook_delete_cleans_artifact_files(client, notebook):
    _seed(client, notebook["id"])
    art = client.post(f"/api/notebooks/{notebook['id']}/artifacts",
                      json={"kind": "spreadsheet"}).json()
    files = list(ARTIFACTS_DIR.glob(f"{notebook['id']}_*.xlsx"))
    assert files
    client.delete(f"/api/notebooks/{notebook['id']}")
    assert not any(Path(f).exists() for f in files)
    assert db.get_artifact(art["id"]) is None


# ---------- spec validation unit tests ----------

def test_validate_chart_spec_rejects_bad_shapes():
    with pytest.raises(ValueError):
        studio._validate_artifact_spec("chart", {"title": "t", "type": "scatter",
                                                 "labels": ["a"], "values": [1]})
    with pytest.raises(ValueError):
        studio._validate_artifact_spec("chart", {"title": "t", "type": "bar",
                                                 "labels": ["a", "b"], "values": [1]})
    with pytest.raises(studio.StudioError):
        studio._validate_artifact_spec("chart", {"error": "no numeric data"})


def test_validate_spreadsheet_pads_rows():
    spec = studio._validate_artifact_spec("spreadsheet", {
        "title": "t", "columns": ["a", "b", "c"], "rows": [["1"], ["1", "2", "3", "4"]],
    })
    assert spec["rows"] == [["1", "", ""], ["1", "2", "3"]]
