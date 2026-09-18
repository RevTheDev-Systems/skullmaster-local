"""Chunk index: LanceDB for vectors + in-memory BM25, fused with reciprocal rank fusion.

LanceDB was chosen over Chroma: embedded (zero server processes), columnar with
fast ANN + SQL-style metadata filtering, and trivially portable (one directory).
"""

import hashlib
import re

import lancedb
from rank_bm25 import BM25Okapi

from .config import LANCEDB_DIR, TOP_K, VECTOR_CANDIDATES
from .providers import get_llm

TABLE = "chunks"
_db = lancedb.connect(str(LANCEDB_DIR))


def _table():
    if TABLE in _db.table_names():
        return _db.open_table(TABLE)
    return None


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _chunk_id(source_id: str, seq: int, text: str) -> str:
    """Stable, deterministic chunk ID: same source content → same ID."""
    digest = hashlib.sha256(f"{source_id}:{seq}:{text}".encode()).hexdigest()[:24]
    return f"ch_{digest}"


def add_chunks(notebook_id: str, source_id: str, source_name: str, chunks: list[dict], llm=None):
    """Embed + store chunks. `llm` is injectable for benchmarks/tests."""
    vectors = (llm or get_llm()).embed([c["text"] for c in chunks])
    rows = [
        {
            "id": _chunk_id(source_id, c["seq"], c["text"]),
            "notebook_id": notebook_id,
            "source_id": source_id,
            "source_name": source_name,
            "page": c["page"] if c["page"] is not None else -1,
            "seq": c["seq"],
            "text": c["text"],
            "vector": vec,
        }
        for c, vec in zip(chunks, vectors)
    ]
    tbl = _table()
    if tbl is None:
        _db.create_table(TABLE, rows)
    else:
        tbl.add(rows)


def delete_source_chunks(source_id: str):
    tbl = _table()
    if tbl is not None:
        tbl.delete(f"source_id = '{source_id}'")


def delete_notebook_chunks(notebook_id: str):
    tbl = _table()
    if tbl is not None:
        tbl.delete(f"notebook_id = '{notebook_id}'")


def _notebook_rows(notebook_id: str) -> list[dict]:
    tbl = _table()
    if tbl is None:
        return []
    return tbl.search().where(f"notebook_id = '{notebook_id}'").limit(100_000).to_list()


def notebook_chunks(notebook_id: str) -> list[dict]:
    """All chunks for a notebook (used by Studio artifact generation)."""
    return [
        {
            "source_id": r["source_id"],
            "source_name": r["source_name"],
            "page": r["page"] if r["page"] >= 0 else None,
            "seq": r["seq"],
            "text": r["text"],
        }
        for r in _notebook_rows(notebook_id)
    ]


def status() -> dict:
    tbl = _table()
    return {
        "backend": "lancedb",
        "path": str(LANCEDB_DIR),
        "ready": True,
        "chunks": tbl.count_rows() if tbl is not None else 0,
    }


def hybrid_search(notebook_id: str, query: str, k: int = TOP_K, llm=None) -> list[dict]:
    """Vector + BM25 retrieval merged with reciprocal rank fusion.

    `llm` is injectable so the evaluation harness can run deterministically.
    """
    tbl = _table()
    if tbl is None:
        return []

    qvec = (llm or get_llm()).embed([query])[0]
    vector_hits = (
        tbl.search(qvec)
        .where(f"notebook_id = '{notebook_id}'", prefilter=True)
        .limit(VECTOR_CANDIDATES)
        .to_list()
    )

    all_rows = _notebook_rows(notebook_id)
    if not all_rows:
        return []
    bm25 = BM25Okapi([_tokenize(r["text"]) for r in all_rows])
    scores = bm25.get_scores(_tokenize(query))
    bm25_ranked = sorted(zip(all_rows, scores), key=lambda p: p[1], reverse=True)
    bm25_hits = [r for r, s in bm25_ranked[:VECTOR_CANDIDATES] if s > 0]

    # Reciprocal rank fusion
    fused: dict[str, dict] = {}
    RRF_K = 60
    for rank, row in enumerate(vector_hits):
        entry = fused.setdefault(row["id"], {"row": row, "score": 0.0})
        entry["score"] += 1.0 / (RRF_K + rank + 1)
    for rank, row in enumerate(bm25_hits):
        entry = fused.setdefault(row["id"], {"row": row, "score": 0.0})
        entry["score"] += 1.0 / (RRF_K + rank + 1)

    ranked = sorted(fused.values(), key=lambda e: e["score"], reverse=True)[:k]
    results = []
    for e in ranked:
        row = e["row"]
        results.append(
            {
                "id": row["id"],
                "source_id": row["source_id"],
                "source_name": row["source_name"],
                "page": row["page"] if row["page"] >= 0 else None,
                "text": row["text"],
            }
        )
    return results
