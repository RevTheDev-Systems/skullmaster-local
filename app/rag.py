"""Grounded chat: hybrid retrieval → strict source-only prompt → streamed answer with [n] citations."""

import re
from collections.abc import Iterator

from . import db
from .config import CONTEXT_HISTORY_TURNS, TOP_K, load_prompt
from .providers import get_llm
from .store import hybrid_search, hybrid_search_many


def format_timestamp(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _location(c: dict) -> str:
    """Human label for where an excerpt sits inside its source."""
    if c.get("kind") in ("video", "audio") and c.get("page") is not None:
        return f" (at {format_timestamp(c['page'])})"
    if c.get("page"):
        return f" (page {c['page']})"
    return ""


def _format_excerpts(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, start=1):
        notebook = c.get("notebook_name")
        where = f' (notebook: "{notebook}")' if notebook else ""
        parts.append(f'[{i}] — from "{c["source_name"]}"{where}{_location(c)}:\n{c["text"]}')
    return "\n\n---\n\n".join(parts)


def strip_think(text: str) -> str:
    """Remove inline reasoning that some models leak into content."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.strip()


def strip_invalid_citations(text: str, n_chunks: int) -> str:
    """Remove citation markers that don't map to a provided excerpt."""

    def repl(m: re.Match) -> str:
        num = int(m.group(1))
        return m.group(0) if 1 <= num <= n_chunks else ""

    return re.sub(r"\[(\d+)\]", repl, strip_think(text))


def answer_stream(
    notebook_id: str,
    question: str,
    history: list[dict] | None = None,
    llm=None,
) -> tuple[list[dict], Iterator[str]]:
    """Returns (retrieved_chunks, token_iterator).

    `llm` is injectable so the evaluation harness can exercise the identical
    retrieval + prompt path with a deterministic model.
    """
    llm = llm or get_llm()
    chunks = hybrid_search(notebook_id, question, k=TOP_K, llm=llm)
    kinds = {s["id"]: s["kind"] for s in db.list_sources(notebook_id)}
    for c in chunks:
        c["kind"] = kinds.get(c["source_id"], "text")

    messages = [{"role": "system", "content": load_prompt("grounded_answer")}]
    for turn in (history or [])[-CONTEXT_HISTORY_TURNS * 2 :]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})

    if chunks:
        user_msg = f"Source excerpts:\n\n{_format_excerpts(chunks)}\n\nQuestion: {question}"
    else:
        user_msg = (
            "No relevant source excerpts were found for this question.\n\n"
            f"Question: {question}\n\n"
            "Tell the user you couldn't find this in their sources."
        )
    messages.append({"role": "user", "content": user_msg})

    return chunks, llm.chat(messages, stream=True)


def research_stream(
    notebook_ids: list[str],
    question: str,
    history: list[dict] | None = None,
    llm=None,
    top_k: int = TOP_K,
) -> tuple[list[dict], Iterator[str]]:
    """Answer a question across several notebooks (research mode).

    Retrieval is merged across notebooks with a diversity pass so the context
    isn't dominated by one notebook. Excerpts are labelled with their notebook,
    and returned chunks carry `kind`, `notebook_id`, and `notebook_name` for
    citation rendering.
    """
    llm = llm or get_llm()
    chunks = hybrid_search_many(notebook_ids, question, k=top_k, llm=llm)

    meta: dict[str, dict] = {}
    for nb_id in notebook_ids:
        notebook = db.get_notebook(nb_id)
        name = notebook["name"] if notebook else nb_id
        for source in db.list_sources(nb_id):
            meta[source["id"]] = {
                "kind": source["kind"],
                "notebook_id": nb_id,
                "notebook_name": name,
            }
    for c in chunks:
        info = meta.get(c["source_id"], {})
        c["kind"] = info.get("kind", "text")
        c["notebook_name"] = info.get("notebook_name", "")

    messages = [{"role": "system", "content": load_prompt("grounded_answer")}]
    for turn in (history or [])[-CONTEXT_HISTORY_TURNS * 2 :]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})

    if chunks:
        user_msg = (
            f"Source excerpts from across the user's notebooks:\n\n"
            f"{_format_excerpts(chunks)}\n\nQuestion: {question}"
        )
    else:
        user_msg = (
            "No relevant source excerpts were found across the notebooks.\n\n"
            f"Question: {question}\n\n"
            "Tell the user you couldn't find this in their sources."
        )
    messages.append({"role": "user", "content": user_msg})
    return chunks, llm.chat(messages, stream=True)
