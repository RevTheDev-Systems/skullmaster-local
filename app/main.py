"""FastAPI app: notebooks, sources, grounded chat (SSE), audio overview, static UI."""
import json
import logging
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, ingest, store
from .chunker import chunk_segments
from .config import AUDIO_DIR, PROJECT_ROOT, UPLOADS_DIR
from .providers import get_llm, get_tts
from .rag import answer_stream, strip_invalid_citations

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nlm")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    result = get_llm().ensure_models()
    log.info("Model check: %s", result)
    yield


app = FastAPI(title="NotebookLM Local", lifespan=lifespan)

STATIC_DIR = PROJECT_ROOT / "static"


class NotebookIn(BaseModel):
    name: str


class UrlIn(BaseModel):
    url: str


class ChatIn(BaseModel):
    question: str
    history: list[dict] = []


@app.get("/api/health")
def health():
    llm = get_llm().status()
    tts = get_tts().status()
    vector = store.status()
    ok = llm.get("reachable", False) and llm.get("chat_model_ready", False) \
        and llm.get("embed_model_ready", False)
    return {"ok": ok, "llm": llm, "tts": tts, "vector_store": vector}


# ---------- Notebooks ----------

@app.get("/api/notebooks")
def notebooks_list():
    return db.list_notebooks()


@app.post("/api/notebooks")
def notebooks_create(body: NotebookIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Notebook name is required")
    return db.create_notebook(name)


@app.delete("/api/notebooks/{notebook_id}")
def notebooks_delete(notebook_id: str):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    store.delete_notebook_chunks(notebook_id)
    db.delete_notebook(notebook_id)
    return {"ok": True}


# ---------- Sources ----------

@app.get("/api/notebooks/{notebook_id}/sources")
def sources_list(notebook_id: str):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    return db.list_sources(notebook_id)


@app.post("/api/notebooks/{notebook_id}/sources/file")
def sources_upload(notebook_id: str, file: UploadFile):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    if not file.filename:
        raise HTTPException(400, "Missing filename")

    dest = UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}_{Path(file.filename).name}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        kind, pages, segments = ingest.parse_file(dest)
    except ingest.IngestError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, str(e))

    chunks = chunk_segments(segments)
    src = db.create_source(notebook_id, Path(file.filename).name, kind,
                           file.filename, pages, len(chunks))
    store.add_chunks(notebook_id, src["id"], src["name"], chunks)
    return src


@app.post("/api/notebooks/{notebook_id}/sources/url")
def sources_add_url(notebook_id: str, body: UrlIn):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    try:
        title, segments = ingest.parse_url(body.url.strip())
    except ingest.IngestError as e:
        raise HTTPException(422, str(e))

    chunks = chunk_segments(segments)
    src = db.create_source(notebook_id, title, "url", body.url.strip(), None, len(chunks))
    store.add_chunks(notebook_id, src["id"], src["name"], chunks)
    return src


@app.delete("/api/notebooks/{notebook_id}/sources/{source_id}")
def sources_delete(notebook_id: str, source_id: str):
    store.delete_source_chunks(source_id)
    db.delete_source(source_id)
    return {"ok": True}


# ---------- Chat ----------

@app.post("/api/notebooks/{notebook_id}/chat")
def chat(notebook_id: str, body: ChatIn):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")

    chunks, tokens = answer_stream(notebook_id, body.question, body.history)

    def sse():
        # 1) send retrieved excerpts so the client can resolve [n] citations
        sources_payload = [
            {
                "n": i + 1,
                "source_id": c["source_id"],
                "source_name": c["source_name"],
                "page": c["page"],
                "text": c["text"],
            }
            for i, c in enumerate(chunks)
        ]
        yield f"event: sources\ndata: {json.dumps(sources_payload)}\n\n"

        # 2) stream tokens, accumulating for final citation validation
        full = ""
        for tok in tokens:
            full += tok
            yield f"event: token\ndata: {json.dumps(tok)}\n\n"

        # 3) send the validated final text (invalid citation markers removed)
        cleaned = strip_invalid_citations(full, len(chunks))
        yield f"event: done\ndata: {json.dumps(cleaned)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


# ---------- Studio: Audio Overview ----------

@app.post("/api/notebooks/{notebook_id}/audio-overview")
def audio_overview(notebook_id: str):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    from . import studio
    try:
        meta = studio.generate_audio_overview(notebook_id)
    except studio.StudioError as e:
        raise HTTPException(422, str(e))
    meta["url"] = f"/api/audio/{meta['filename']}"
    return meta


@app.get("/api/audio/{filename}")
def get_audio(filename: str):
    safe = Path(filename).name
    path = AUDIO_DIR / safe
    if not path.exists():
        raise HTTPException(404, "Audio not found")
    return FileResponse(path, media_type="audio/wav", filename=safe)


# ---------- Static UI ----------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
