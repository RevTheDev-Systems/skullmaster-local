"""SQLite metadata store for notebooks and sources (chunks live in LanceDB)."""
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import SQLITE_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS notebooks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,          -- pdf | docx | text | url
    origin TEXT,                 -- original filename or URL
    pages INTEGER,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    role TEXT NOT NULL,          -- user | assistant
    content TEXT NOT NULL,
    citations TEXT,              -- JSON list of retrieved excerpts (assistant only)
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audio_overviews (
    id TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    title TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    line_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Columns added after the original release; applied idempotently at startup.
SOURCE_MIGRATIONS = {
    "status": "ALTER TABLE sources ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'",
    "error": "ALTER TABLE sources ADD COLUMN error TEXT",
    "content_hash": "ALTER TABLE sources ADD COLUMN content_hash TEXT",
    "stored_path": "ALTER TABLE sources ADD COLUMN stored_path TEXT",
}


@contextmanager
def conn():
    c = sqlite3.connect(SQLITE_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.executescript(SCHEMA)
        existing = {r["name"] for r in c.execute("PRAGMA table_info(sources)")}
        for col, ddl in SOURCE_MIGRATIONS.items():
            if col not in existing:
                c.execute(ddl)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_notebook(name: str) -> dict:
    nb = {"id": uuid.uuid4().hex[:12], "name": name, "created_at": _now()}
    with conn() as c:
        c.execute("INSERT INTO notebooks VALUES (:id, :name, :created_at)", nb)
    return nb


def list_notebooks() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT n.*, COUNT(s.id) AS source_count FROM notebooks n "
            "LEFT JOIN sources s ON s.notebook_id = n.id "
            "GROUP BY n.id ORDER BY n.created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def get_notebook(notebook_id: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM notebooks WHERE id = ?", (notebook_id,)).fetchone()
    return dict(row) if row else None


def rename_notebook(notebook_id: str, name: str):
    with conn() as c:
        c.execute("UPDATE notebooks SET name = ? WHERE id = ?", (name, notebook_id))


def delete_notebook(notebook_id: str):
    with conn() as c:
        c.execute("DELETE FROM notebooks WHERE id = ?", (notebook_id,))


def create_source(notebook_id: str, name: str, kind: str, origin: str | None,
                  pages: int | None, chunk_count: int, *,
                  status: str = "ready", error: str | None = None,
                  content_hash: str | None = None,
                  stored_path: str | None = None) -> dict:
    src = {
        "id": uuid.uuid4().hex[:12],
        "notebook_id": notebook_id,
        "name": name,
        "kind": kind,
        "origin": origin,
        "pages": pages,
        "chunk_count": chunk_count,
        "created_at": _now(),
        "status": status,
        "error": error,
        "content_hash": content_hash,
        "stored_path": stored_path,
    }
    with conn() as c:
        c.execute(
            "INSERT INTO sources VALUES (:id, :notebook_id, :name, :kind, :origin,"
            " :pages, :chunk_count, :created_at, :status, :error, :content_hash,"
            " :stored_path)",
            src,
        )
    return src


def get_source(source_id: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    return dict(row) if row else None


def find_source_by_hash(notebook_id: str, content_hash: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM sources WHERE notebook_id = ? AND content_hash = ?"
            " AND status = 'ready'",
            (notebook_id, content_hash),
        ).fetchone()
    return dict(row) if row else None


def update_source(source_id: str, **fields):
    assign = ", ".join(f"{k} = :{k}" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE sources SET {assign} WHERE id = :id",
                  {**fields, "id": source_id})


def list_sources(notebook_id: str) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM sources WHERE notebook_id = ? ORDER BY created_at",
            (notebook_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_source(source_id: str):
    with conn() as c:
        c.execute("DELETE FROM sources WHERE id = ?", (source_id,))


# ---------- Chat messages ----------

def add_message(notebook_id: str, role: str, content: str,
                citations: str | None = None) -> dict:
    msg = {
        "id": uuid.uuid4().hex[:12],
        "notebook_id": notebook_id,
        "role": role,
        "content": content,
        "citations": citations,
        "created_at": _now(),
    }
    with conn() as c:
        c.execute(
            "INSERT INTO messages VALUES (:id, :notebook_id, :role, :content,"
            " :citations, :created_at)",
            msg,
        )
    return msg


def list_messages(notebook_id: str) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM messages WHERE notebook_id = ? ORDER BY created_at, rowid",
            (notebook_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def clear_messages(notebook_id: str):
    with conn() as c:
        c.execute("DELETE FROM messages WHERE notebook_id = ?", (notebook_id,))


# ---------- Audio overviews ----------

def create_audio_overview(notebook_id: str, filename: str, title: str,
                          duration_seconds: float, line_count: int) -> dict:
    row = {
        "id": uuid.uuid4().hex[:12],
        "notebook_id": notebook_id,
        "filename": filename,
        "title": title,
        "duration_seconds": duration_seconds,
        "line_count": line_count,
        "created_at": _now(),
    }
    with conn() as c:
        c.execute(
            "INSERT INTO audio_overviews VALUES (:id, :notebook_id, :filename,"
            " :title, :duration_seconds, :line_count, :created_at)",
            row,
        )
    return row


def list_audio_overviews(notebook_id: str) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM audio_overviews WHERE notebook_id = ?"
            " ORDER BY created_at DESC",
            (notebook_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def counts() -> dict:
    with conn() as c:
        nb = c.execute("SELECT COUNT(*) AS n FROM notebooks").fetchone()["n"]
        src = c.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
    return {"notebooks": nb, "sources": src}
