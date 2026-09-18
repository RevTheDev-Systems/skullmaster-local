"""Deterministic RAG evaluation harness.

Runs the *real* retrieval and generation path over a fixed corpus and reports
retrieval quality (Recall@K, MRR, page grounding) and grounded-generation
quality (citation correctness/completeness, unsupported-claim rate, refusal
accuracy). Corpus and metrics are versioned so changes are measured, not
guessed.

The module imports only the standard library at import time; application
modules are imported lazily so the CLI can point the run at an isolated data
directory before `app.config` (and therefore LanceDB/SQLite) is initialised.

Usage:
    python -m app.evaluation                    # temp data dir, configured models
    python -m app.evaluation --no-generate      # retrieval metrics only (fast)
    python -m app.evaluation --json evals/baseline.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REFUSAL_PATTERNS = (
    "couldn't find this in your sources",
    "could not find this in your sources",
    "not in your sources",
    "don't have that information",
    "do not have that information",
    "no relevant source",
)


# ---------- corpus ----------


def load_corpus(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def doc_segments(doc: dict) -> list[tuple[int | None, str]]:
    """(page_or_second, text) segments for a corpus document."""
    if "pages" in doc:
        return [(p.get("page"), p["text"]) for p in doc["pages"]]
    if "segments" in doc:
        return [(s.get("page"), s["text"]) for s in doc["segments"]]
    return [(None, doc["text"])]


def _page_count(doc: dict) -> int | None:
    pages = [p.get("page") for p in doc.get("pages", []) if p.get("page") is not None]
    return max(pages) if pages else None


# ---------- metric primitives (pure, unit-tested) ----------


def _doc_order(chunks: list[dict]) -> list[str]:
    """Unique source keys in ranked order (document-level retrieval order)."""
    order: list[str] = []
    for c in chunks:
        key = c.get("_key")
        if key is not None and key not in order:
            order.append(key)
    return order


def recall_at_k(
    retrieved_keys: list[str], relevant: list[str], match: str = "all", k: int | None = None
) -> float | None:
    """Document-level Recall@K. 'all' = fraction of relevant docs retrieved;
    'any' = 1.0 if at least one relevant doc is retrieved. None if no labels."""
    if not relevant:
        return None
    found = set(retrieved_keys[:k] if k else retrieved_keys)
    rel = set(relevant)
    if match == "any":
        return 1.0 if found & rel else 0.0
    return len(found & rel) / len(rel)


def reciprocal_rank(chunks: list[dict], relevant) -> float:
    """1/rank of the first retrieved chunk whose source is relevant."""
    rel = set(relevant)
    for rank, c in enumerate(chunks, start=1):
        if c.get("_key") in rel:
            return 1.0 / rank
    return 0.0


def _citation_numbers(answer: str) -> list[int]:
    return [int(m) for m in re.findall(r"\[(\d+)\]", answer or "")]


def citation_correctness(answer: str, chunks: list[dict], relevant) -> float | None:
    """Fraction of cited excerpt numbers that point at a relevant source."""
    numbers = _citation_numbers(answer)
    if not numbers:
        return None
    rel = set(relevant)
    good = sum(1 for n in numbers if 1 <= n <= len(chunks) and chunks[n - 1].get("_key") in rel)
    return good / len(numbers)


def citation_completeness(
    answer: str, chunks: list[dict], required_keys: list[str]
) -> float | None:
    """Fraction of required source keys that appear among the cited chunks."""
    if not required_keys:
        return None
    cited = {chunks[n - 1].get("_key") for n in _citation_numbers(answer) if 1 <= n <= len(chunks)}
    required = set(required_keys)
    return len(cited & required) / len(required)


# A "must_not" phrase inside a negated clause is not an assertion, e.g. a
# correct refusal that says "no information about a chief scientist is provided"
# mentions the phrase without claiming it.
NEGATION_CUES = (
    "no ",
    "not ",
    "never ",
    "without ",
    "n't ",
    "nothing about ",
    "no information",
    "no mention",
)


def has_unsupported_claim(answer: str, must_not: list[str]) -> bool:
    """True only if a `must_not` phrase appears as an assertion, not a negation."""
    low = (answer or "").lower()
    for phrase in must_not or []:
        needle = phrase.lower()
        start = 0
        while True:
            idx = low.find(needle, start)
            if idx == -1:
                break
            window = low[max(0, idx - 40) : idx]
            if not any(cue in window for cue in NEGATION_CUES):
                return True
            start = idx + len(needle)
    return False


def is_refusal(answer: str) -> bool:
    low = (answer or "").lower()
    return any(p in low for p in REFUSAL_PATTERNS)


def page_hit(chunks: list[dict], expected_page, within: int = 3) -> bool | None:
    """Whether the expected page/second appears among the top-`within` chunks."""
    if expected_page is None:
        return None
    return any(c.get("page") == expected_page for c in chunks[:within])


def _mean(values) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


# ---------- harness ----------


def evaluate(
    corpus: dict,
    *,
    llm,
    k_values=(1, 3, 8),
    generate: bool = True,
    store_mod=None,
    db_mod=None,
    rag_mod=None,
    notebook_name: str | None = None,
) -> dict:
    """Ingest the corpus into an isolated notebook and score every question."""
    from .chunker import chunk_segments

    if store_mod is None:
        from . import store as store_mod
    if db_mod is None:
        from . import db as db_mod
    if rag_mod is None:
        from . import rag as rag_mod

    db_mod.init_db()  # idempotent; makes the harness usable standalone
    notebook = db_mod.create_notebook(notebook_name or f"eval::{corpus.get('name', 'corpus')}")
    notebook_id = notebook["id"]
    key_by_source: dict[str, str] = {}
    max_k = max(k_values)

    try:
        for doc in corpus["documents"]:
            chunks = chunk_segments(doc_segments(doc))
            source = db_mod.create_source(
                notebook_id,
                doc["name"],
                doc.get("kind", "text"),
                doc.get("name"),
                _page_count(doc),
                len(chunks),
                status="ready",
            )
            key_by_source[source["id"]] = doc["key"]
            store_mod.add_chunks(notebook_id, source["id"], doc["name"], chunks, llm=llm)

        per_question = []
        for q in corpus["questions"]:
            relevant = q.get("relevant", [])
            match = q.get("match", "all")
            retrieved = store_mod.hybrid_search(notebook_id, q["question"], k=max_k, llm=llm)
            for c in retrieved:
                c["_key"] = key_by_source.get(c["source_id"])
            order = _doc_order(retrieved)

            row = {
                "id": q["id"],
                "category": q["category"],
                "retrieved": order,
                "recall": {f"@{k}": recall_at_k(order, relevant, match, k) for k in k_values},
                "rr": reciprocal_rank(retrieved, relevant) if relevant else None,
                "page_hit": page_hit(retrieved, q.get("expected_page")),
            }

            if generate:
                chunks, tokens = rag_mod.answer_stream(notebook_id, q["question"], [], llm=llm)
                for c in chunks:
                    c["_key"] = key_by_source.get(c["source_id"])
                answer = "".join(tokens)
                row["answer"] = answer
                row["citation_correctness"] = (
                    citation_correctness(answer, chunks, relevant) if relevant else None
                )
                row["citation_completeness"] = citation_completeness(
                    answer, chunks, q.get("citation_required", [])
                )
                row["unsupported_claim"] = has_unsupported_claim(answer, q.get("must_not", []))
                row["refusal"] = is_refusal(answer) if q.get("expected_refusal") else None
                row["facts_covered"] = (
                    all(f.lower() in answer.lower() for f in q["expected_facts"])
                    if q.get("expected_facts")
                    else None
                )
            per_question.append(row)

        return _aggregate(corpus, k_values, per_question, generate, llm)
    finally:
        try:
            store_mod.delete_notebook_chunks(notebook_id)
        finally:
            db_mod.delete_notebook(notebook_id)


def _aggregate(corpus: dict, k_values, per_question: list[dict], generate: bool, llm) -> dict:
    labelled = [r for r in per_question if any(v is not None for v in r["recall"].values())]
    retrieval: dict[str, float | int | None] = {"questions": len(labelled)}
    for k in k_values:
        retrieval[f"recall@{k}"] = _mean([r["recall"][f"@{k}"] for r in labelled])
    retrieval["mrr"] = _mean([r["rr"] for r in labelled])
    retrieval["page_accuracy"] = _mean([r["page_hit"] for r in per_question])

    report = {
        "corpus": corpus.get("name"),
        "k_values": list(k_values),
        "generated": generate,
        "retrieval": retrieval,
    }
    if generate:
        report["generation"] = {
            "questions": len(per_question),
            "citation_correctness": _mean([r["citation_correctness"] for r in per_question]),
            "citation_completeness": _mean([r["citation_completeness"] for r in per_question]),
            "unsupported_claim_rate": _mean(
                [1.0 if r["unsupported_claim"] else 0.0 for r in per_question]
            ),
            "refusal_accuracy": _mean([r["refusal"] for r in per_question]),
            "fact_coverage": _mean([r["facts_covered"] for r in per_question]),
        }
    report["backend"] = _backend_name(llm)
    report["per_question"] = per_question
    return report


def _backend_name(llm) -> str:
    try:
        status = llm.status()
        return str(status.get("backend", "unknown"))
    except Exception:
        return "unknown"


# ---------- CLI ----------


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _print_report(report: dict) -> None:
    print(f"\nCorpus: {report['corpus']}  (backend: {report['backend']})")
    r = report["retrieval"]
    print(f"\nRetrieval ({r['questions']} labelled questions)")
    for key, value in r.items():
        if key != "questions":
            print(f"  {key:>16}: {_fmt(value)}")
    if report["generated"]:
        g = report["generation"]
        print(f"\nGrounded generation ({g['questions']} questions)")
        for key, value in g.items():
            if key != "questions":
                print(f"  {key:>24}: {_fmt(value)}")
        print("\nPer question:")
        for row in report["per_question"]:
            flags = []
            if row.get("unsupported_claim"):
                flags.append("UNSUPPORTED")
            if row.get("refusal") is False:
                flags.append("NO-REFUSAL")
            print(
                f"  {row['id']:<14} {row['category']:<24} "
                f"recall@3={_fmt(row['recall'].get('@3'))} "
                f"rr={_fmt(row.get('rr'))} {' '.join(flags)}"
            )


def main(argv=None) -> int:
    import argparse
    import os
    import tempfile
    import time

    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(prog="python -m app.evaluation", description=__doc__)
    parser.add_argument("--corpus", default=str(root / "evals" / "corpus.json"))
    parser.add_argument("--k", default="1,3,8", help="comma-separated K values")
    parser.add_argument(
        "--no-generate", action="store_true", help="skip the LLM and report retrieval metrics only"
    )
    parser.add_argument(
        "--data-dir", default=None, help="data dir for the throwaway eval index (default: temp)"
    )
    parser.add_argument(
        "--json", dest="json_path", default=None, help="write the full report to this JSON path"
    )
    args = parser.parse_args(argv)

    # Point the app at an isolated data dir BEFORE importing app modules, so the
    # benchmark never touches the real LanceDB/SQLite.
    os.environ["NLM_DATA_DIR"] = args.data_dir or tempfile.mkdtemp(prefix="skullmaster-eval-")
    k_values = tuple(int(x) for x in args.k.split(",") if x.strip())

    from . import db, rag, store
    from .providers import get_llm

    corpus = load_corpus(args.corpus)
    db.init_db()
    llm = get_llm()

    started = time.time()
    report = evaluate(
        corpus,
        llm=llm,
        k_values=k_values,
        generate=not args.no_generate,
        store_mod=store,
        db_mod=db,
        rag_mod=rag,
    )
    report["elapsed_seconds"] = round(time.time() - started, 1)
    report["corpus_path"] = str(args.corpus)

    _print_report(report)
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
