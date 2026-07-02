"""Grounded chat: hybrid retrieval → strict source-only prompt → streamed answer with [n] citations."""
import re
from collections.abc import Iterator

from .config import CONTEXT_HISTORY_TURNS, TOP_K, load_prompt
from .providers import get_llm
from .store import hybrid_search


def _format_excerpts(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, start=1):
        loc = f" (page {c['page']})" if c.get("page") else ""
        parts.append(f"[{i}] — from \"{c['source_name']}\"{loc}:\n{c['text']}")
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
) -> tuple[list[dict], Iterator[str]]:
    """Returns (retrieved_chunks, token_iterator)."""
    chunks = hybrid_search(notebook_id, question, k=TOP_K)

    messages = [{"role": "system", "content": load_prompt("grounded_answer")}]
    for turn in (history or [])[-CONTEXT_HISTORY_TURNS * 2:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})

    if chunks:
        user_msg = (
            f"Source excerpts:\n\n{_format_excerpts(chunks)}\n\n"
            f"Question: {question}"
        )
    else:
        user_msg = (
            "No relevant source excerpts were found for this question.\n\n"
            f"Question: {question}\n\n"
            "Tell the user you couldn't find this in their sources."
        )
    messages.append({"role": "user", "content": user_msg})

    return chunks, get_llm().chat(messages, stream=True)
