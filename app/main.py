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
    config_report,
)
from .providers import get_llm, get_stt, get_tts
from .rag import answer_stream, research_stream, strip_invalid_citations

# Logger names carry the failure category, e.g. app.providers.routing
# (provider), app.studio (malformed model output), app.rag (retrieval);
# "skullmaster" covers HTTP-level auth/ingestion/stream failures.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("skullmaster")


CHAT_MODEL_SETTING = "chat_model"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    log.info("%s v%s starting", PRODUCT_NAME, APP_VERSION)
    # Classify configuration up front — invalid values fall back to defaults and
    # are logged, so a bad .env is visible without preventing startup.
    problems = [r for r in config_report() if r["status"] == "invalid"]
    if problems:
        for p in problems:
            log.error("Invalid configuration — %s: %s", p["name"], p["detail"])
    else:
        log.info("Configuration validated")
    llm = get_llm()
    # A model picked in the UI overrides the .env default for later runs.
    saved = db.get_setting(CHAT_MODEL_SETTING)
    if saved:
        try:
            llm.set_chat_model(saved)
            log.info(
                "Chat model restored from settings: %s (active: %s)",
                saved,
                getattr(llm, "chat_model", saved),
            )
        except Exception:
            # A preferred model whose backend is temporarily offline must never
            # prevent the app from booting; providers fall back and record a warning.
            log.exception("Could not restore saved chat model %r; using defaults", saved)
    try:
        result = llm.ensure_models()
        log.info("Model check: %s", result)
    except Exception:
        log.exception("Model check failed; continuing in a degraded state")
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
                    409, f"A {self.key[1]} is already being generated for this notebook"
                )
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
    "/login",
    "/healthz",
    "/favicon.ico",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/setup",
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
        auth.COOKIE_NAME,
        token,
        max_age=auth.SESSION_DAYS * 24 * 3600,
        httponly=True,  # not readable from JavaScript
        samesite="lax",  # not sent on cross-site requests
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


class ResearchIn(BaseModel):
    question: str
    notebook_ids: list[str] = []  # empty = all notebooks
    history: list[dict] = []
    use_tools: bool = False  # opt-in single local-tool round


class LibraryGraphIn(BaseModel):
    notebook_ids: list[str] = []  # empty = all notebooks


class ToolIn(BaseModel):
    name: str
    args: dict = {}


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
    ok = (
        llm.get("reachable", False)
        and llm.get("chat_model_ready", False)
        and llm.get("embed_model_ready", False)
        and vector.get("ready", False)
        and database["ready"]
    )
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


def _llm_attr(llm, name: str):
    """Read a provider attribute that may be a plain value or a method."""
    value = getattr(llm, name, None)
    return value() if callable(value) else value


def _list_models(llm, refresh: bool = False):
    """Call list_models(refresh=...) when supported, else without it."""
    try:
        return llm.list_models(refresh=refresh)
    except TypeError:
        return llm.list_models()


def _model_registry(llm, refresh: bool = False) -> dict:
    """Providers + models; RoutingProvider exposes a richer registry()."""
    registry = getattr(llm, "registry", None)
    if callable(registry):
        try:
            return registry(refresh=refresh)
        except TypeError:
            return registry()
    return {"models": _list_models(llm, refresh)}


@app.get("/api/models")
def models_list(refresh: bool = False):
    """Installed models with capabilities, provider states, and active/preferred.

    `chat_model` is the active runtime model (may be a fallback when the
    preferred backend is offline); `preferred_model` is the user's choice.
    `?refresh=1` bypasses cached per-model metadata, e.g. after a model pull.
    """
    llm = get_llm()
    try:
        registry = _model_registry(llm, refresh)
    except Exception as e:
        raise HTTPException(503, f"Could not reach the model backend: {e}")
    return {
        "models": registry.get("models", []),
        "providers": registry.get("providers"),
        "chat_model": llm.chat_model,  # active runtime model
        "preferred_model": _llm_attr(llm, "preferred_model") or llm.chat_model,
        "warning": _llm_attr(llm, "runtime_warning"),
    }


@app.post("/api/models/chat")
def models_set_chat(body: ModelIn):
    """Switch the active chat model and remember the preference across restarts."""
    llm = get_llm()
    try:
        available = {m["name"]: m for m in _list_models(llm)}
    except Exception as e:
        raise HTTPException(503, f"Could not reach the model backend: {e}")
    chosen = available.get(body.name)
    if not chosen:
        raise HTTPException(404, f"Model not installed: {body.name}")
    if not chosen["can_chat"]:
        raise HTTPException(400, f"{body.name} cannot generate chat responses")
    result = llm.set_chat_model(body.name)
    # The preference is remembered even if the provider is momentarily offline
    # and a fallback model had to be activated instead.
    db.set_setting(CHAT_MODEL_SETTING, body.name)
    if isinstance(result, dict):
        active, warning = result.get("active"), result.get("warning")
    else:
        active, warning = llm.chat_model, _llm_attr(llm, "runtime_warning")
    log.info("Chat model preference set to %s (active: %s)", body.name, active)
    return {
        "ok": True,
        "chat_model": active or llm.chat_model,
        "preferred_model": body.name,
        "warning": warning,
    }


@app.get("/api/models/route")
def models_route(capability: str = "chat", min_context: int | None = None):
    """Consumer-aware routing decision (read-only): which model, and why."""
    llm = get_llm()
    plan = getattr(llm, "route_plan", None)
    if not callable(plan):
        return {
            "model": llm.chat_model,
            "capability": capability,
            "reason": "provider does not expose routing",
            "alternatives": [],
        }
    try:
        return plan(capability, min_context=min_context)
    except TypeError:
        return plan(capability)


# ---------- Local tools (Phase 22) ----------


@app.get("/api/tools")
def tools_list():
    """Catalog of the safe, local tools available to research workflows."""
    from . import tools

    return {"tools": tools.list_tools()}


@app.post("/api/tools/run")
def tools_run(body: ToolIn):
    """Run one local tool with strict argument validation (no code execution)."""
    from . import tools

    try:
        result = tools.run_tool(body.name, body.args)
    except tools.ToolError as e:
        raise HTTPException(422, str(e))
    return {"name": body.name, "args": body.args, "result": result}


# ---------- Notebooks ----------


@app.get("/api/notebooks")
def notebooks_list():
    return db.list_notebooks()


@app.get("/api/search")
def library_search(q: str = "", k: int = 8):
    """Search across every notebook, returning matching passages + notebooks."""
    query = q.strip()
    if not query:
        raise HTTPException(400, "Search query is required")
    limit = max(1, min(k, 25))
    try:
        chunks = store.search_library(query, k=limit)
    except Exception as e:
        log.exception("Library search failed")
        raise HTTPException(503, f"Search failed: {e}")

    notebooks = db.list_notebooks()
    names = {n["id"]: n["name"] for n in notebooks}
    kinds = {s["id"]: s["kind"] for n in notebooks for s in db.list_sources(n["id"])}

    results = []
    grouped: dict[str, dict] = {}
    for c in chunks:
        nb_id = c.get("notebook_id") or ""
        name = names.get(nb_id, nb_id)
        results.append(
            {
                "source_id": c["source_id"],
                "source_name": c["source_name"],
                "notebook_id": nb_id,
                "notebook_name": name,
                "kind": kinds.get(c["source_id"], "text"),
                "page": c["page"],
                "text": c["text"],
            }
        )
        entry = grouped.setdefault(nb_id, {"notebook_id": nb_id, "name": name, "matches": 0})
        entry["matches"] += 1
    ranked = sorted(grouped.values(), key=lambda n: n["matches"], reverse=True)
    return {"query": query, "notebooks": ranked, "results": results}


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
    return db.get_notebook(notebook_id) or {}


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


def _index_chunks(notebook_id: str, source_id: str, source_name: str, chunks: list[dict]):
    """Embed + store chunks; raises HTTPException(503) on provider failure."""
    try:
        store.add_chunks(notebook_id, source_id, source_name, chunks)
    except Exception as e:
        log.exception("Indexing failed for source %s", source_id)
        db.update_source(source_id, status="failed", error=f"Embedding/indexing failed: {e}")
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
                raise HTTPException(413, f"File exceeds the {max_bytes // (1024 * 1024)}MB limit")
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

    src = db.create_source(
        notebook_id,
        safe_name,
        kind,
        safe_name,
        pages,
        len(chunks),
        content_hash=content_hash,
        stored_path=str(dest),
    )
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

    src = db.create_source(
        notebook_id, title, "url", url, None, len(chunks), content_hash=content_hash
    )
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
    db.update_source(source_id, chunk_count=len(chunks), content_hash=_content_hash(chunks))
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
        out.append(
            {
                "role": m["role"],
                "content": m["content"],
                "citations": json.loads(m["citations"]) if m["citations"] else [],
                "status": m.get("status", "completed"),
                "error": m.get("error"),
            }
        )
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

    # Persist the turn *before* generating: an interrupted answer (client
    # disconnect, model failure) stays in history as `interrupted` rather than
    # vanishing, and a partial answer is never mistaken for a complete one.
    db.add_message(notebook_id, "user", body.question)
    assistant = db.add_message(notebook_id, "assistant", "", status="pending")

    try:
        chunks, tokens = answer_stream(notebook_id, body.question, body.history)
    except Exception as e:
        log.exception("Retrieval failed for notebook %s", notebook_id)
        db.update_message(assistant["id"], status="interrupted", error=f"Retrieval failed: {e}")
        raise HTTPException(503, "Could not retrieve sources for this question")

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

    def sse():
        full = ""
        finalized = False

        def finalize(
            status: str, content: str, citations: str | None = None, error: str | None = None
        ):
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                db.update_message(
                    assistant["id"],
                    content=content,
                    status=status,
                    citations=citations,
                    error=error,
                )
            except Exception:
                log.exception("Could not finalize message %s", assistant["id"])

        try:
            try:
                db.update_message(assistant["id"], status="streaming")
                # 1) send retrieved excerpts so the client can resolve [n] citations
                yield f"event: sources\ndata: {json.dumps(sources_payload)}\n\n"

                # 2) stream tokens, accumulating for final citation validation
                for tok in tokens:
                    full += tok
                    yield f"event: token\ndata: {json.dumps(tok)}\n\n"
            except GeneratorExit:
                finalize("interrupted", full)  # client went away mid-answer
                raise
            except Exception as e:
                log.exception("Chat stream failed")
                yield f"event: error\ndata: {json.dumps(f'Model error: {e}')}\n\n"
                finalize("interrupted", full, error=f"Model error: {e}")
                return

            # 3) validate citations, persist the completed turn, then signal done
            cleaned = strip_invalid_citations(full, len(chunks))
            finalize("completed", cleaned, json.dumps(sources_payload))
            yield f"event: done\ndata: {json.dumps(cleaned)}\n\n"
        finally:
            # Covers a close before/around the first yield.
            finalize("interrupted", full)

    return StreamingResponse(sse(), media_type="text/event-stream")


@app.post("/api/research")
def research(body: ResearchIn):
    """Answer across the whole library (or selected notebooks), with citations
    attributed to their notebook. Research answers are transient — they are not
    persisted to any single notebook."""
    notebook_ids = body.notebook_ids or [n["id"] for n in db.list_notebooks()]
    if not notebook_ids:
        raise HTTPException(400, "No notebooks to search")
    for nb_id in notebook_ids:
        if not db.get_notebook(nb_id):
            raise HTTPException(404, f"Notebook not found: {nb_id}")

    try:
        chunks, tokens = research_stream(
            notebook_ids, body.question, body.history, use_tools=body.use_tools
        )
    except Exception:
        log.exception("Research retrieval failed")
        raise HTTPException(503, "Could not retrieve sources for this question")

    sources_payload = [
        {
            "n": i + 1,
            "source_id": c["source_id"],
            "source_name": c["source_name"],
            "notebook_id": c.get("notebook_id"),
            "notebook_name": c.get("notebook_name", ""),
            "kind": c.get("kind", "text"),
            "page": c["page"],
            "text": c["text"],
        }
        for i, c in enumerate(chunks)
    ]

    def sse():
        full = ""
        try:
            yield f"event: sources\ndata: {json.dumps(sources_payload)}\n\n"
            for tok in tokens:
                full += tok
                yield f"event: token\ndata: {json.dumps(tok)}\n\n"
        except GeneratorExit:
            raise
        except Exception as e:
            log.exception("Research stream failed")
            yield f"event: error\ndata: {json.dumps(f'Model error: {e}')}\n\n"
            return
        cleaned = strip_invalid_citations(full, len(chunks))
        yield f"event: done\ndata: {json.dumps(cleaned)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


@app.post("/api/research/graph")
def research_graph(body: LibraryGraphIn):
    """Knowledge graph across notebooks (transient — not stored as an artifact)."""
    from . import studio

    notebook_ids = body.notebook_ids or [n["id"] for n in db.list_notebooks()]
    if not notebook_ids:
        raise HTTPException(400, "No notebooks to graph")
    for nb_id in notebook_ids:
        if not db.get_notebook(nb_id):
            raise HTTPException(404, f"Notebook not found: {nb_id}")

    with _job("*library*", "mindgraph"):
        try:
            spec = studio.generate_library_graph(notebook_ids)
        except studio.StudioError as e:
            raise HTTPException(422, str(e))
        except Exception as e:
            log.exception("Library graph generation failed")
            raise HTTPException(503, f"Could not generate the graph: {e}")
    return {"kind": "mindgraph", "title": spec["title"], "spec": spec}


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
    db.create_audio_overview(
        notebook_id, meta["filename"], meta["title"], meta["duration_seconds"], meta["lines"]
    )
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
    row = db.create_artifact(
        notebook_id,
        body.kind,
        spec["title"],
        json.dumps(spec),
        str(file_path) if file_path else None,
    )
    return _artifact_out(row)


@app.post("/api/notebooks/{notebook_id}/slides")
def slides_create(notebook_id: str):
    """Generate a grounded slide deck for a notebook."""
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    from . import slides

    with _job(notebook_id, "slides"):
        try:
            deck = slides.generate_deck(notebook_id)
        except slides.StudioError as e:
            raise HTTPException(422, str(e))
        except Exception as e:
            log.exception("Slide deck generation failed")
            raise HTTPException(503, f"Could not generate slides: {e}")
    row = db.create_artifact(notebook_id, "slides", deck["title"], json.dumps(deck), None)
    return _artifact_out(row)


@app.post("/api/notebooks/{notebook_id}/video-overview")
def video_overview_create(notebook_id: str):
    """Build a narrated Video Overview (grounded slides + TTS + ffmpeg)."""
    if not db.get_notebook(notebook_id):
        raise HTTPException(404, "Notebook not found")
    from . import studio, video

    with _job(notebook_id, "video"):
        try:
            meta = video.build_video_overview(notebook_id)
        except studio.StudioError as e:
            raise HTTPException(422, str(e))
        except Exception as e:
            log.exception("Video overview failed")
            raise HTTPException(503, f"Could not build the video overview: {e}")
    spec = {
        "slides": meta["slides"],
        "duration_seconds": meta["duration_seconds"],
        "source_note": "",
    }
    row = db.create_artifact(
        notebook_id,
        "video",
        meta["title"],
        json.dumps(spec),
        str(ARTIFACTS_DIR / meta["filename"]),
    )
    return _artifact_out(row)


@app.delete("/api/notebooks/{notebook_id}/artifacts/{artifact_id}")
def artifacts_delete(notebook_id: str, artifact_id: str):
    artifact = db.get_artifact(artifact_id)
    if not artifact or artifact["notebook_id"] != notebook_id:
        raise HTTPException(404, "Artifact not found")
    _delete_artifact_file(artifact)
    db.delete_artifact(artifact_id)
    return {"ok": True}


_ARTIFACT_MEDIA = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".mp4": "video/mp4",
    ".wav": "audio/wav",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


@app.get("/api/artifacts/{artifact_id}/file")
def artifact_file(artifact_id: str):
    artifact = db.get_artifact(artifact_id)
    if not artifact or not artifact.get("file_path"):
        raise HTTPException(404, "Artifact file not found")
    path = Path(artifact["file_path"])
    if path.parent != ARTIFACTS_DIR or not path.exists():
        raise HTTPException(404, "Artifact file not found")
    safe_title = re.sub(r"[^\w\- ]", "_", artifact["title"])[:60] or "artifact"
    suffix = path.suffix.lower()
    return FileResponse(
        path,
        media_type=_ARTIFACT_MEDIA.get(suffix, "application/octet-stream"),
        filename=f"{safe_title}{suffix}",
    )


# ---------- Media playback for video/audio sources ----------

MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".aac": "audio/aac",
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


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Browsers request /favicon.ico directly; serve the app icon if present."""
    icon = STATIC_DIR / "favicon.png"
    if icon.exists():
        return FileResponse(icon, media_type="image/png")
    raise HTTPException(404, "No favicon configured")
