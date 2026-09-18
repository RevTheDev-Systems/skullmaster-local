"""Grounded chat: hybrid retrieval → strict source-only prompt → streamed answer with [n] citations."""

import json
import logging
import re
from collections.abc import Iterator

from . import db
from .config import CONTEXT_HISTORY_TURNS, TOP_K, load_prompt
from .providers import get_llm
from .store import hybrid_search, hybrid_search_many, notebook_chunks

log = logging.getLogger(__name__)

# Opt-in tool protocol: at most one local tool per answer, chosen by strict JSON.
# {tools} is replaced (not .format) because the instruction contains literal braces.
_TOOL_INSTRUCTION = (
    "\n\nLOCAL TOOLS: You may use at most ONE local tool before answering. If the "
    "question needs exact arithmetic, date math, or unit conversion, reply with "
    'ONLY a JSON object of the form {"tool": "<name>", "args": { ... }} using one '
    "of the tools below and nothing else. Otherwise answer the question normally "
    "with citations. Tool results are computed locally and trusted; do not cite "
    "them to a source.\n\nAvailable tools:\n{tools}"
)


def _parse_tool_call(text: str) -> dict | None:
    """Parse a strict tool-call object, or None if this is a normal answer."""
    candidate = strip_think(text).strip()
    candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.MULTILINE).strip()
    if not candidate.startswith("{"):
        return None
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and isinstance(data.get("tool"), str):
        return data
    return None


def _tool_context(notebook_ids: list[str]) -> dict:
    """Bounded source text for source-scoped tools (count/find across sources)."""
    rows: list[dict] = []
    for nb_id in notebook_ids:
        notebook = db.get_notebook(nb_id)
        name = notebook["name"] if notebook else nb_id
        for chunk in notebook_chunks(nb_id):
            rows.append({**chunk, "notebook_name": name})
            if len(rows) >= 500:
                return {"sources": rows}
    return {"sources": rows}


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
    use_tools: bool = False,
) -> tuple[list[dict], Iterator[str]]:
    """Answer a question across several notebooks (research mode).

    Retrieval is merged across notebooks with a diversity pass so the context
    isn't dominated by one notebook. Excerpts are labelled with their notebook,
    and returned chunks carry `kind`, `notebook_id`, and `notebook_name` for
    citation rendering.

    When `use_tools` is set, one bounded tool round is allowed first: the model
    may request a single local tool via strict JSON, the tool is run (logged),
    and its trusted result is fed back before the final grounded answer.
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

    if not use_tools:
        return chunks, llm.chat(messages, stream=True)

    # Bounded tool round: exactly one planning call, at most one tool execution.
    from . import tools as tool_mod

    messages[0]["content"] += _TOOL_INSTRUCTION.replace(
        "{tools}", json.dumps(tool_mod.list_tools())
    )
    plan_raw = llm.chat(messages)
    if not isinstance(plan_raw, str):
        plan_raw = "".join(plan_raw)
    plan = _parse_tool_call(plan_raw)
    if plan is None:
        return chunks, iter([plan_raw])  # answered directly; nothing to run

    try:
        context = None
        spec = tool_mod.TOOLS.get(str(plan["tool"]))
        if spec is not None and spec.get("contextual"):
            context = _tool_context(notebook_ids)
        result = tool_mod.run_tool(plan["tool"], plan.get("args") or {}, context)
        log.info("Research tool %s(%s) -> %s", plan["tool"], plan.get("args"), result)
    except tool_mod.ToolError as e:
        result = {"error": str(e)}
        log.warning("Research tool %s failed: %s", plan.get("tool"), e)

    messages.append({"role": "assistant", "content": plan_raw})
    messages.append(
        {
            "role": "user",
            "content": (
                f"Tool result (computed locally, trusted; do not cite it): "
                f"{json.dumps(result)}\n\nNow answer the question using the "
                "sources and this result."
            ),
        }
    )
    return chunks, llm.chat(messages, stream=True)
