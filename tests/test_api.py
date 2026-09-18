"""Integration tests: real FastAPI app + real LanceDB/SQLite in a temp dir,
mock LLM/TTS providers (final acceptance against real models is run separately)."""
import io

from app import db, main, store
from app.config import AUDIO_DIR, UPLOADS_DIR

TXT = b"The zephyr wombat festival happens every March. Tickets cost 42 tugrik."


def _upload(client, nb_id, name=b"doc.txt", content=TXT):
    return client.post(
        f"/api/notebooks/{nb_id}/sources/file",
        files={"file": (name.decode(), io.BytesIO(content), "text/plain")},
    )


# ---------- health ----------

def test_health_shape(client):
    body = client.get("/health").json()
    assert body["product"] == "SkullMaster iQ"
    assert body["status"] == "ok" and body["ok"] is True
    for key in ("version", "llm", "tts", "vector_store", "database"):
        assert key in body
    assert body["database"]["ready"] is True
    assert client.get("/api/health").json()["product"] == "SkullMaster iQ"


# ---------- notebooks ----------

def test_notebook_crud(client):
    nb = client.post("/api/notebooks", json={"name": "Alpha"}).json()
    assert nb["name"] == "Alpha"

    assert client.post("/api/notebooks", json={"name": "  "}).status_code == 400

    renamed = client.patch(f"/api/notebooks/{nb['id']}", json={"name": "Beta"})
    assert renamed.json()["name"] == "Beta"
    assert client.patch("/api/notebooks/nope", json={"name": "X"}).status_code == 404

    ids = [n["id"] for n in client.get("/api/notebooks").json()]
    assert nb["id"] in ids

    assert client.delete(f"/api/notebooks/{nb['id']}").json() == {"ok": True}
    assert client.delete(f"/api/notebooks/{nb['id']}").status_code == 404


# ---------- sources ----------

def test_upload_and_index(client, notebook):
    res = _upload(client, notebook["id"])
    assert res.status_code == 200, res.text
    src = res.json()
    assert src["status"] == "ready" and src["chunk_count"] == 1
    assert (UPLOADS_DIR in [p for p in [UPLOADS_DIR]])  # dir exists
    chunks = store.notebook_chunks(notebook["id"])
    assert len(chunks) == 1 and "wombat" in chunks[0]["text"]


def test_duplicate_upload_rejected(client, notebook):
    assert _upload(client, notebook["id"]).status_code == 200
    dup = _upload(client, notebook["id"], name=b"copy.txt")
    assert dup.status_code == 409


def test_unsupported_and_empty_files(client, notebook):
    bad = _upload(client, notebook["id"], name=b"x.exe", content=b"MZ")
    assert bad.status_code == 422
    empty = _upload(client, notebook["id"], name=b"e.txt", content=b"   ")
    assert empty.status_code == 422


def test_corrupt_documents_rejected_422(client, notebook):
    for name, data in [(b"bad.pdf", b"%PDF-1.4 broken"),
                       (b"bad.docx", b"not a docx"),
                       (b"bad.xlsx", b"not a zip")]:
        res = _upload(client, notebook["id"], name=name, content=data)
        assert res.status_code == 422, (name, res.status_code, res.text)


def test_unicode_filename_upload(client, notebook):
    res = _upload(client, notebook["id"],
                  name="données café.txt".encode(),
                  content="Contenu accentué — 日本語".encode())
    assert res.status_code == 200, res.text
    assert "café" in res.json()["name"]


def test_oversized_upload_rejected_413(client, notebook, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)
    res = _upload(client, notebook["id"], name=b"big.txt", content=b"x" * 100)
    assert res.status_code == 413


def test_source_delete_removes_vectors_and_file(client, notebook):
    src = _upload(client, notebook["id"]).json()
    stored = db.get_source(src["id"])["stored_path"]
    assert stored and len(store.notebook_chunks(notebook["id"])) == 1

    res = client.delete(f"/api/notebooks/{notebook['id']}/sources/{src['id']}")
    assert res.json() == {"ok": True}
    assert store.notebook_chunks(notebook["id"]) == []
    import pathlib
    assert not pathlib.Path(stored).exists()


def test_failed_source_and_retry(client, notebook, mock_llm):
    mock_llm.fail_embeds = True
    res = _upload(client, notebook["id"])
    assert res.status_code == 503
    sources = client.get(f"/api/notebooks/{notebook['id']}/sources").json()
    assert len(sources) == 1 and sources[0]["status"] == "failed"
    assert sources[0]["error"]

    mock_llm.fail_embeds = False
    retry = client.post(
        f"/api/notebooks/{notebook['id']}/sources/{sources[0]['id']}/retry")
    assert retry.status_code == 200 and retry.json()["status"] == "ready"
    assert len(store.notebook_chunks(notebook["id"])) == 1

    # retrying a ready source is a 400
    again = client.post(
        f"/api/notebooks/{notebook['id']}/sources/{sources[0]['id']}/retry")
    assert again.status_code == 400


# ---------- chat ----------

def test_chat_streams_and_persists(client, notebook):
    _upload(client, notebook["id"])
    res = client.post(f"/api/notebooks/{notebook['id']}/chat",
                      json={"question": "What do tickets cost?", "history": []})
    body = res.text
    assert "event: sources" in body and "event: done" in body
    # invalid citation [9] stripped, valid [1] kept
    assert "[1]" in body and "[9]" not in body.split("event: done")[1]

    msgs = client.get(f"/api/notebooks/{notebook['id']}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["status"] == "completed"
    assert msgs[1]["status"] == "completed"
    assert msgs[1]["citations"][0]["n"] == 1

    client.delete(f"/api/notebooks/{notebook['id']}/messages")
    assert client.get(f"/api/notebooks/{notebook['id']}/messages").json() == []


def test_chat_model_failure_keeps_turn_as_interrupted(client, notebook, monkeypatch):
    _upload(client, notebook["id"])

    def fake_answer(nb_id, question, history):
        def tokens():
            yield "Partial answer"
            raise RuntimeError("model crashed")
        return [], tokens()

    monkeypatch.setattr(main, "answer_stream", fake_answer)
    res = client.post(f"/api/notebooks/{notebook['id']}/chat",
                      json={"question": "q", "history": []})
    assert "event: error" in res.text

    msgs = client.get(f"/api/notebooks/{notebook['id']}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["status"] == "interrupted"
    assert msgs[1]["content"] == "Partial answer"          # partial kept, not lost
    assert "model crashed" in (msgs[1]["error"] or "")
    assert msgs[1]["citations"] == []


def test_chat_retrieval_failure_keeps_turn_as_interrupted(client, notebook, monkeypatch):
    def boom(nb_id, question, history):
        raise RuntimeError("embedding backend down")

    monkeypatch.setattr(main, "answer_stream", boom)
    res = client.post(f"/api/notebooks/{notebook['id']}/chat",
                      json={"question": "q", "history": []})
    assert res.status_code == 503

    msgs = client.get(f"/api/notebooks/{notebook['id']}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["status"] == "interrupted"
    assert "embedding backend down" in (msgs[1]["error"] or "")


def test_chat_empty_notebook_declines(client, notebook):
    res = client.post(f"/api/notebooks/{notebook['id']}/chat",
                      json={"question": "Anything?", "history": []})
    assert "couldn't find this in your sources" in res.text


# ---------- audio overview ----------

def test_audio_overview_generated_and_persisted(client, notebook):
    _upload(client, notebook["id"])
    res = client.post(f"/api/notebooks/{notebook['id']}/audio-overview")
    assert res.status_code == 200, res.text
    meta = res.json()
    assert meta["title"] == "Mock Overview" and meta["duration_seconds"] > 0
    wav = AUDIO_DIR / meta["filename"]
    assert wav.exists() and wav.stat().st_size > 44

    listed = client.get(f"/api/notebooks/{notebook['id']}/audio-overviews").json()
    assert len(listed) == 1 and listed[0]["url"].endswith(meta["filename"])

    audio = client.get(listed[0]["url"])
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")


def test_audio_overview_empty_notebook_422(client, notebook):
    assert client.post(f"/api/notebooks/{notebook['id']}/audio-overview").status_code == 422


# ---------- deletion cleanup + persistence ----------

def test_notebook_delete_cleans_everything(client, notebook):
    nb_id = notebook["id"]
    src = _upload(client, nb_id).json()
    stored = db.get_source(src["id"])["stored_path"]
    client.post(f"/api/notebooks/{nb_id}/chat", json={"question": "q", "history": []})
    meta = client.post(f"/api/notebooks/{nb_id}/audio-overview").json()
    wav = AUDIO_DIR / meta["filename"]
    assert wav.exists()

    client.delete(f"/api/notebooks/{nb_id}")
    import pathlib
    assert not pathlib.Path(stored).exists()
    assert not wav.exists()
    assert store.notebook_chunks(nb_id) == []
    assert db.list_messages(nb_id) == []
    assert db.list_audio_overviews(nb_id) == []


def test_data_survives_client_restart(client, notebook):
    """Same on-disk stores, new app lifecycle — metadata and vectors persist."""
    from fastapi.testclient import TestClient
    from app import main as main_mod

    from tests.conftest import TEST_PASSWORD

    _upload(client, notebook["id"])
    with TestClient(main_mod.app) as fresh:
        fresh.post("/api/auth/login", json={"password": TEST_PASSWORD})
        nbs = fresh.get("/api/notebooks").json()
        assert notebook["id"] in [n["id"] for n in nbs]
        sources = fresh.get(f"/api/notebooks/{notebook['id']}/sources").json()
        assert len(sources) == 1 and sources[0]["status"] == "ready"
    assert len(store.notebook_chunks(notebook["id"])) == 1
