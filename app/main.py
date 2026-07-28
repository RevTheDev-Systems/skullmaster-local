"""FastAPI app: notebooks, sources, grounded chat (SSE), audio overview, static UI."""
import hashlib
import json
import logging
import re
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, db, ingest, store
from .chunker import chunk_segments
from .config import (
    APP_VERSION,
    ARTIFACTS_DIR,
    AUDIO_DIR,
    MAX_UPLOAD_BYTES,
    MEDIA_MAX_UPLOAD_BYTES,
    PRODUCT_NAME,
    PROJECT_ROOT,
    SQLITE_PATH,
    UPLOADS_DIR,
)
from .providers import get_llm, get_stt, get_tts
from .rag import answer_stream, strip_invalid_citations

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("skullmaster")


CHAT_MODEL_SETTING = "chat_model"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    log.info("%s v%s starting", PRODUCT_NAME, APP_VERSION)
    # A model picked in the UI overrides the .env default for later runs.
    saved = db.get_setting(CHAT_MODEL_SETTING)
    if saved:
        get_llm().set_chat_model(saved)
        log.info("Chat model restored from settings: %s", saved)
    result = get_llm().ensure_models()
    log.info("Model check: %s", result)
    yield


app = FastAPI(title=PRODUCT_NAME, version=APP_VERSION, lifespan=lifespan)

STATIC_DIR = PROJECT_ROOT / "static"

# Long-running Studio jobs in flight, keyed (notebook_id, job kind) — guards
# duplicate clicks and parallel tabs.
_jobs: set[tuple[str, str]] = set()
_jobs_lock = threading.Lock()


class _job:
    """Context manager: claim a (notebook, kind) job slot or raise 409."""

    def __init__(self, notebook_id: str, kind: str):
        self.key = (notebook_id, kind)

    def __enter__(self):
        with _jobs_lock:
            if self.key in _jobs:
                raise HTTPException(
                    409, f"A {self.key[1]} is already being generated for this notebook")
            _jobs.add(self.key)

    def __exit__(self, *exc):
        with _jobs_lock:
            _jobs.discard(self.key)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ---------- Authentication guard ----------

# Reachable without a session: the login screen, its assets, and liveness.
PUBLIC_PATHS = {
    "/login", "/healthz", "/favicon.ico",
    "/api/auth/status", "/api/auth/login", "/api/auth/setup",
}
PUBLIC_PREFIXES = ("/static/",)


@app.middleware("http")
async def require_session(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
        return await call_next(request)
    if auth.validate_session(request.cookies.get(auth.COOKIE_NAME)):
        return await call_next(request)
    # Browser navigations get sent to the login screen; API calls get a 401 so
    # the client can redirect without following an HTML response.
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/login", status_code=303)
    return JSONResponse(status_code=401, content={"detail": "Authentication required"})


class PasswordIn(BaseModel):
    password: str


def _set_session_cookie(response: Response, token: str):
    response.set_cookie(
        auth.COOKIE_NAME, token,
        max_age=auth.SESSION_DAYS * 24 * 3600,
        httponly=True,       # not readable from JavaScript
        samesite="lax",      # not sent on cross-site requests
        path="/",
    )


@app.get("/healthz")
def liveness():
    """Unauthenticated liveness probe used by the launcher script."""
    return {"status": "alive", "product": PRODUCT_NAME, "version": APP_VERSION}


@app.get("/api/auth/status")
def auth_status(request: Request):
    return {
        "authenticated": auth.validate_session(request.cookies.get(auth.COOKIE_NAME)),
        "setup_required": auth.setup_required(),
        "product": PRODUCT_NAME,
        "min_password_length": auth.MIN_PASSWORD_LENGTH,
    }


@app.post("/api/auth/setup")
def auth_setup(body: PasswordIn, response: Response):
    """First-run: the owner chooses their password. Refused once one exists."""
    if not auth.setup_required():
        raise HTTPException(409, "A password has already been set")
    try:
        auth.set_password(body.password)
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    _set_session_cookie(response, auth.create_session())
    log.info("Owner password created")
    return {"ok": True}


@app.post("/api/auth/login")
def auth_login(body: PasswordIn, request: Request, response: Response):
    if auth.setup_required():
        raise HTTPException(409, "No password has been set yet")
    client = request.client.host if request.client else "unknown"
    locked = auth.seconds_until_unlocked(client)
    if locked:
        raise HTTPException(429, f"Too many attempts — try again in {locked}s")
    if not auth.verify_password(body.password):
        auth.record_failure(client)
        log.warning("Failed login attempt from %s", client)
        raise HTTPException(401, "Incorrect password")
    auth.clear_failures(client)
    _set_session_cookie(response, auth.create_session())
    return {"ok": True}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    auth.destroy_session(request.cookies.get(auth.COOKIE_NAME))
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/login")
def login_page(request: Request):
    if auth.validate_session(request.cookies.get(auth.COOKIE_NAME)):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC_DIR / "login.html")


class NotebookIn(BaseModel):
    name: str


class UrlIn(BaseModel):
    url: str


class ChatIn(BaseModel):
    question: str
    history: list[dict] = []


# ---------- Health ----------

def _health() -> dict:
    llm = get_llm().status()
    tts = get_tts().status()
    stt = get_stt().status()
    vector = store.status()
    try:
        counts = db.counts()
        database = {"ready": True, "path": str(SQLITE_PATH), **counts}
    except Exception as e:
        database = {"ready": False, "path": str(SQLITE_PATH), "error": str(e)}
    ok = (llm.get("reachable", False) and llm.get("chat_model_ready", False)
          and llm.get("embed_model_ready", False) and vector.get("ready", False)
          and database["ready"])
    return {
        "status": "ok" if ok else "degraded",
        "ok": ok,
        "product": PRODUCT_NAME,
        "version": APP_VERSION,
        "llm": llm,
        "tts": tts,
        "stt": stt,
        "vector_store": vector,
        "database": database,
    }


@app.get("/health")
@app.get("/api/health")
def health():
    return _health()


# ---------- Models ----------

class ModelIn(BaseModel):
    name: str


@app.get("/api/models")
def models_list():
    """Installed models plus which one chat is currently using."""
    llm = get_llm()
    try:
        models = llm.list_models()
    except Exception as e:
        raise HTTPException(503, f"Could not reach the model backend: {e}")
    return {"models": models, "chat_model": llm.chat_model}


@app.post("/api/models/chat")
def models_set_chat(body: ModelIn):
    """Switch the active chat model and remember it across restarts."""
    llm = get_llm()
    try:
        available = {m["name"]: m for m in llm.list_models()}
    except Exception as e:
        raise HTTPException(503, f"Could not reach the model backend: {e}")
    chosen = available.get(body.name)
    if not chosen:
        raise HTTPException(404, f"Model not installed: {body.name}")
    if not chosen["can_chat"]:
        raise HTTPException(400, f"{body.name} cannot generate chat responses")
    llm.set_chat_model(body.name)
    db.set_setting(CHAT_MODEL_SETTING, body.name)
    log.info("Chat model switched to %s", body.name)
    return {"ok": True, "chat_model": body.name}


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


@app.patch("/api/notebooks/{notebook_id}")
def notebooks_rename(notebook_id: str, body: NotebookIn):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Notebook name is required")
    db.rename_notebook(notebook_id, name)
    return {**db.get_notebook(notebook_id)}


@app.delete("/api/notebooks/{notebook_id}")
def notebooks_delete(notebook_id: str):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    for src in db.list_sources(notebook_id):
        _delete_source_file(src)
    for overview in db.list_audio_overviews(notebook_id):
        (AUDIO_DIR / Path(overview["filename"]).name).unlink(missing_ok=True)
    for artifact in db.list_artifacts(notebook_id):
        _delete_artifact_file(artifact)
    store.delete_notebook_chunks(notebook_id)
    db.delete_notebook(notebook_id)  # cascades sources, messages, audio, artifacts
    return {"ok": True}


# ---------- Sources ----------

def _sanitize_filename(name: str) -> str:
    clean = re.sub(r"[^\w.\- ]", "_", Path(name).name).strip(". ")
    return clean or "upload"


def _content_hash(chunks: list[dict]) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(c["text"].encode())
        h.update(b"\x00")
    return h.hexdigest()


def _delete_source_file(src: dict):
    if src.get("stored_path"):
        p = Path(src["stored_path"])
        # only ever delete files inside our uploads directory
        if p.parent == UPLOADS_DIR:
            p.unlink(missing_ok=True)


def _index_chunks(notebook_id: str, source_id: str, source_name: str,
                  chunks: list[dict]):
    """Embed + store chunks; raises HTTPException(503) on provider failure."""
    try:
        store.add_chunks(notebook_id, source_id, source_name, chunks)
    except Exception as e:
        log.exception("Indexing failed for source %s", source_id)
        db.update_source(source_id, status="failed",
                         error=f"Embedding/indexing failed: {e}")
        raise HTTPException(503, f"Embedding failed (is the embedding model available?): {e}")


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

    safe_name = _sanitize_filename(file.filename)
    suffix = Path(safe_name).suffix.lower()
    is_media = suffix in ingest.VIDEO_SUFFIXES | ingest.AUDIO_SUFFIXES
    max_bytes = MEDIA_MAX_UPLOAD_BYTES if is_media else MAX_UPLOAD_BYTES
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}_{safe_name}"
    size = 0
    with dest.open("wb") as f:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    413, f"File exceeds the {max_bytes // (1024 * 1024)}MB limit")
            f.write(chunk)

    try:
        kind, pages, segments = ingest.parse_file(dest)
    except ingest.IngestError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, str(e))

    chunks = chunk_segments(segments)
    content_hash = _content_hash(chunks)
    dup = db.find_source_by_hash(notebook_id, content_hash)
    if dup:
        dest.unlink(missing_ok=True)
        raise HTTPException(409, f'Already in this notebook as "{dup["name"]}"')

    src = db.create_source(notebook_id, safe_name, kind, safe_name, pages,
                           len(chunks), content_hash=content_hash,
                           stored_path=str(dest))
    _index_chunks(notebook_id, src["id"], src["name"], chunks)
    return db.get_source(src["id"])


@app.post("/api/notebooks/{notebook_id}/sources/url")
def sources_add_url(notebook_id: str, body: UrlIn):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    url = body.url.strip()
    try:
        title, segments = ingest.parse_url(url)
    except ingest.IngestError as e:
        raise HTTPException(422, str(e))

    chunks = chunk_segments(segments)
    content_hash = _content_hash(chunks)
    dup = db.find_source_by_hash(notebook_id, content_hash)
    if dup:
        raise HTTPException(409, f'Already in this notebook as "{dup["name"]}"')

    src = db.create_source(notebook_id, title, "url", url, None, len(chunks),
                           content_hash=content_hash)
    _index_chunks(notebook_id, src["id"], src["name"], chunks)
    return db.get_source(src["id"])


@app.post("/api/notebooks/{notebook_id}/sources/{source_id}/retry")
def sources_retry(notebook_id: str, source_id: str):
    """Re-index a source that failed at the embedding/indexing step."""
    src = db.get_source(source_id)
    if not src or src["notebook_id"] != notebook_id:
        raise HTTPException(404, "Source not found")
    if src["status"] != "failed":
        raise HTTPException(400, "Source is not in a failed state")

    try:
        if src["kind"] == "url":
            _, segments = ingest.parse_url(src["origin"])
        else:
            if not src.get("stored_path") or not Path(src["stored_path"]).exists():
                raise ingest.IngestError("Original file is no longer available")
            _, _, segments = ingest.parse_file(Path(src["stored_path"]))
    except ingest.IngestError as e:
        db.update_source(source_id, error=str(e))
        raise HTTPException(422, str(e))

    chunks = chunk_segments(segments)
    store.delete_source_chunks(source_id)  # clear any partial index
    db.update_source(source_id, chunk_count=len(chunks),
                     content_hash=_content_hash(chunks))
    _index_chunks(notebook_id, source_id, src["name"], chunks)
    db.update_source(source_id, status="ready", error=None)
    return db.get_source(source_id)


@app.delete("/api/notebooks/{notebook_id}/sources/{source_id}")
def sources_delete(notebook_id: str, source_id: str):
    src = db.get_source(source_id)
    if not src or src["notebook_id"] != notebook_id:
        raise HTTPException(404, "Source not found")
    store.delete_source_chunks(source_id)
    _delete_source_file(src)
    db.delete_source(source_id)
    return {"ok": True}


# ---------- Chat ----------

@app.get("/api/notebooks/{notebook_id}/messages")
def messages_list(notebook_id: str):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    out = []
    for m in db.list_messages(notebook_id):
        out.append({
            "role": m["role"],
            "content": m["content"],
            "citations": json.loads(m["citations"]) if m["citations"] else [],
        })
    return out


@app.delete("/api/notebooks/{notebook_id}/messages")
def messages_clear(notebook_id: str):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    db.clear_messages(notebook_id)
    return {"ok": True}


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
                "kind": c.get("kind", "text"),
                "page": c["page"],
                "text": c["text"],
            }
            for i, c in enumerate(chunks)
        ]
        yield f"event: sources\ndata: {json.dumps(sources_payload)}\n\n"

        # 2) stream tokens, accumulating for final citation validation
        full = ""
        try:
            for tok in tokens:
                full += tok
                yield f"event: token\ndata: {json.dumps(tok)}\n\n"
        except Exception as e:
            log.exception("Chat stream failed")
            yield f"event: error\ndata: {json.dumps(f'Model error: {e}')}\n\n"
            return

        # 3) send the validated final text (invalid citation markers removed)
        cleaned = strip_invalid_citations(full, len(chunks))
        yield f"event: done\ndata: {json.dumps(cleaned)}\n\n"

        # 4) persist the exchange so chat survives refresh/restart
        db.add_message(notebook_id, "user", body.question)
        db.add_message(notebook_id, "assistant", cleaned,
                       citations=json.dumps(sources_payload))

    return StreamingResponse(sse(), media_type="text/event-stream")


# ---------- Studio: Audio Overview ----------

@app.get("/api/notebooks/{notebook_id}/audio-overviews")
def audio_overviews_list(notebook_id: str):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    rows = db.list_audio_overviews(notebook_id)
    for r in rows:
        r["url"] = f"/api/audio/{r['filename']}"
    return rows


@app.post("/api/notebooks/{notebook_id}/audio-overview")
def audio_overview(notebook_id: str):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    from . import studio
    with _job(notebook_id, "audio overview"):
        try:
            meta = studio.generate_audio_overview(notebook_id)
        except studio.StudioError as e:
            raise HTTPException(422, str(e))
    db.create_audio_overview(notebook_id, meta["filename"], meta["title"],
                             meta["duration_seconds"], meta["lines"])
    meta["url"] = f"/api/audio/{meta['filename']}"
    return meta


@app.get("/api/audio/{filename}")
def get_audio(filename: str):
    safe = Path(filename).name
    path = AUDIO_DIR / safe
    if not path.exists():
        raise HTTPException(404, "Audio not found")
    return FileResponse(path, media_type="audio/wav", filename=safe)


# ---------- Studio: chart / infographic / spreadsheet artifacts ----------

class ArtifactIn(BaseModel):
    kind: str


def _delete_artifact_file(artifact: dict):
    if artifact.get("file_path"):
        p = Path(artifact["file_path"])
        if p.parent == ARTIFACTS_DIR:
            p.unlink(missing_ok=True)


def _artifact_out(row: dict) -> dict:
    out = {**row, "spec": json.loads(row["spec"])}
    if row.get("file_path"):
        out["file_url"] = f"/api/artifacts/{row['id']}/file"
    out.pop("file_path", None)
    return out


@app.get("/api/notebooks/{notebook_id}/artifacts")
def artifacts_list(notebook_id: str):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    return [_artifact_out(r) for r in db.list_artifacts(notebook_id)]


@app.post("/api/notebooks/{notebook_id}/artifacts")
def artifacts_create(notebook_id: str, body: ArtifactIn):
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    from . import studio
    if body.kind not in studio.ARTIFACT_PROMPTS:
        raise HTTPException(400, f"Unknown artifact kind: {body.kind}")
    with _job(notebook_id, body.kind):
        try:
            spec = studio.generate_artifact_spec(notebook_id, body.kind)
        except studio.StudioError as e:
            raise HTTPException(422, str(e))
    file_path = None
    if body.kind == "spreadsheet":
        file_path = ARTIFACTS_DIR / f"{notebook_id}_{uuid.uuid4().hex[:8]}.xlsx"
        studio.write_xlsx(spec, file_path)
    row = db.create_artifact(notebook_id, body.kind, spec["title"],
                             json.dumps(spec),
                             str(file_path) if file_path else None)
    return _artifact_out(row)


@app.delete("/api/notebooks/{notebook_id}/artifacts/{artifact_id}")
def artifacts_delete(notebook_id: str, artifact_id: str):
    artifact = db.get_artifact(artifact_id)
    if not artifact or artifact["notebook_id"] != notebook_id:
        raise HTTPException(404, "Artifact not found")
    _delete_artifact_file(artifact)
    db.delete_artifact(artifact_id)
    return {"ok": True}


@app.get("/api/artifacts/{artifact_id}/file")
def artifact_file(artifact_id: str):
    artifact = db.get_artifact(artifact_id)
    if not artifact or not artifact.get("file_path"):
        raise HTTPException(404, "Artifact file not found")
    path = Path(artifact["file_path"])
    if path.parent != ARTIFACTS_DIR or not path.exists():
        raise HTTPException(404, "Artifact file not found")
    safe_title = re.sub(r"[^\w\- ]", "_", artifact["title"])[:60] or "spreadsheet"
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{safe_title}.xlsx",
    )


# ---------- Media playback for video/audio sources ----------

MEDIA_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".webm": "video/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".flac": "audio/flac", ".ogg": "audio/ogg", ".aac": "audio/aac",
}


@app.get("/api/media/{source_id}")
def get_media(source_id: str):
    src = db.get_source(source_id)
    if not src or not src.get("stored_path"):
        raise HTTPException(404, "Media not found")
    path = Path(src["stored_path"])
    if path.parent != UPLOADS_DIR or not path.exists():
        raise HTTPException(404, "Media not found")
    media_type = MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=src["name"])


# ---------- Static UI ----------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
