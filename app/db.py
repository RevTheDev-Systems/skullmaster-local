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
    kind TEXT NOT NULL,          -- pdf | text | url
    origin TEXT,                 -- original filename or URL
    pages INTEGER,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


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


def delete_notebook(notebook_id: str):
    with conn() as c:
        c.execute("DELETE FROM notebooks WHERE id = ?", (notebook_id,))


def create_source(notebook_id: str, name: str, kind: str, origin: str | None,
                  pages: int | None, chunk_count: int) -> dict:
    src = {
        "id": uuid.uuid4().hex[:12],
        "notebook_id": notebook_id,
        "name": name,
        "kind": kind,
        "origin": origin,
        "pages": pages,
        "chunk_count": chunk_count,
        "created_at": _now(),
    }
    with conn() as c:
        c.execute(
            "INSERT INTO sources VALUES (:id, :notebook_id, :name, :kind, :origin,"
            " :pages, :chunk_count, :created_at)",
            src,
        )
    return src


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
