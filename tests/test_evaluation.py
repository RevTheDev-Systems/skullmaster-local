"""RAG evaluation harness: metric math, corpus coverage, deterministic runs."""

import hashlib
import re
from pathlib import Path

import pytest

from app import db, evaluation, rag, store

CORPUS_PATH = Path(__file__).resolve().parent.parent / "evals" / "corpus.json"


@pytest.fixture()
def eval_store(tmp_path, monkeypatch):
    """A private LanceDB for each eval run.

    The suite shares one temp data dir, and other tests create the chunks table
    with a different vector dimension; an isolated table keeps benchmark runs
    independent of test order.
    """
    import lancedb

    monkeypatch.setattr(store, "_db", lancedb.connect(str(tmp_path / "lancedb")))
    return store


REQUIRED_CATEGORIES = {
    "exact_fact",
    "paraphrase",
    "multi_document",
    "conflicting_sources",
    "table_data",
    "pdf_page_citation",
    "media_timestamp_citation",
    "irrelevant",
    "absent",
    "duplicate_material",
}


class LexicalLLM:
    """Deterministic hashed bag-of-words embeddings + a scripted grounded answer.

    Not a substitute for a real model's semantics — it exists so the harness's
    metric plumbing can be exercised reproducibly without Ollama.
    """

    def __init__(self, *, cite: bool = True, refuse: bool = False):
        self.cite = cite
        self.refuse = refuse

    def embed(self, texts):
        return [_hashed_bow(t) for t in texts]

    def chat(self, messages, stream=False):
        user = messages[-1]["content"]
        if self.refuse or "No relevant source excerpts" in user:
            return "I couldn't find this in your sources."
        return (
            "The answer is supported by the sources [1]."
            if self.cite
            else "The answer is supported by the sources."
        )

    def status(self):
        return {"backend": "lexical-test"}


def _hashed_bow(text: str, dim: int = 64) -> list[float]:
    vec = [0.0] * dim
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        h = int.from_bytes(hashlib.sha256(tok.encode()).digest()[:4], "big")
        vec[h % dim] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


# ---------- metric primitives ----------


def test_metric_primitives():
    chunks = [{"_key": "a"}, {"_key": "b"}, {"_key": "c"}]
    order = ["a", "b", "c"]

    assert evaluation.recall_at_k(order, ["a", "b"], "all", 1) == 0.5
    assert evaluation.recall_at_k(order, ["a", "b"], "all", 3) == 1.0
    assert evaluation.recall_at_k(order, ["z"], "any", 3) == 0.0
    assert evaluation.recall_at_k(order, ["c"], "any", 3) == 1.0
    assert evaluation.recall_at_k(order, [], "all", 3) is None

    assert evaluation.reciprocal_rank(chunks, {"b"}) == 0.5
    assert evaluation.reciprocal_rank(chunks, {"z"}) == 0.0

    answer = "A [1] and B [2] and out of range [9]."
    # valid citations: [1]->a (relevant), [2]->b (not), [9] out of range
    assert evaluation.citation_correctness(answer, chunks, {"a"}) == pytest.approx(1 / 3)
    assert evaluation.citation_completeness(answer, chunks, ["a", "c"]) == 0.5

    assert evaluation.has_unsupported_claim("The cost is 800", ["800"]) is True
    assert evaluation.has_unsupported_claim("Nothing here", ["800"]) is False
    assert evaluation.is_refusal("I couldn't find this in your sources.") is True
    assert evaluation.is_refusal("The answer is 42.") is False

    assert evaluation.page_hit([{"page": 0}, {"page": 5}], 0) is True
    assert evaluation.page_hit([{"page": 1}, {"page": 2}], 9) is False
    assert evaluation.page_hit([{"page": 1}], None) is None


# ---------- corpus ----------


def test_corpus_covers_all_required_categories():
    corpus = evaluation.load_corpus(CORPUS_PATH)
    categories = {q["category"] for q in corpus["questions"]}
    assert REQUIRED_CATEGORIES <= categories
    keys = {d["key"] for d in corpus["documents"]}
    for q in corpus["questions"]:
        assert set(q.get("relevant", [])) <= keys, q["id"]
        assert set(q.get("citation_required", [])) <= keys, q["id"]


# ---------- deterministic end-to-end runs ----------


def test_retrieval_harness_is_deterministic(eval_store):
    corpus = evaluation.load_corpus(CORPUS_PATH)
    report = evaluation.evaluate(
        corpus,
        llm=LexicalLLM(),
        k_values=(1, 3, 8),
        generate=False,
        store_mod=eval_store,
        db_mod=db,
    )

    assert report["generated"] is False
    assert report["retrieval"]["questions"] > 0
    assert report["retrieval"]["mrr"] > 0.5
    assert report["retrieval"]["recall@3"] is not None

    by_id = {r["id"]: r for r in report["per_question"]}
    for qid in ("exact-1", "table-1", "media-1"):
        assert by_id[qid]["recall"]["@3"] == 1.0, qid
    assert by_id["media-1"]["page_hit"] is True


def test_generation_metrics_with_scripted_llm(eval_store):
    corpus = evaluation.load_corpus(CORPUS_PATH)
    report = evaluation.evaluate(
        corpus,
        llm=LexicalLLM(cite=True),
        k_values=(1, 3, 8),
        generate=True,
        store_mod=eval_store,
        db_mod=db,
        rag_mod=rag,
    )
    gen = report["generation"]
    # The lexical stub (hashing bag-of-words) is a plumbing check, not a
    # semantic one, so a few of its top chunks are distractors; the point is
    # that citations are scored, not that the stub retrieves perfectly.
    assert gen["citation_correctness"] >= 0.7
    assert gen["unsupported_claim_rate"] == 0.0
    assert gen["fact_coverage"] is not None


def test_refusal_accuracy_with_refusal_llm(eval_store):
    corpus = evaluation.load_corpus(CORPUS_PATH)
    report = evaluation.evaluate(
        corpus,
        llm=LexicalLLM(refuse=True),
        k_values=(1,),
        generate=True,
        store_mod=eval_store,
        db_mod=db,
        rag_mod=rag,
    )
    assert report["generation"]["refusal_accuracy"] == 1.0
